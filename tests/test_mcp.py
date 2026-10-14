import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

BUILD = pathlib.Path(__file__).resolve().parents[1]
MCP_DIR = BUILD / "plugin" / "mcp"
sys.path.insert(0, str(MCP_DIR))
import server
from store import Store
from test_store import sample_plan


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(pathlib.Path(self.temp.name) / "mcp.sqlite3")
        server.use_store(self.store)

    def tearDown(self):
        server._STORE = None
        self.temp.cleanup()

    def tool_call(self, name, arguments):
        return server.respond("tools/call", {"name": name, "arguments": arguments})

    def test_tool_schemas_publish_correct_visibility_and_board_binding(self):
        response = server.respond("tools/list")
        tools = {item["name"]: item for item in response["tools"]}
        self.assertEqual(set(tools), {
            "workflow_create", "workflow_update_draft", "workflow_confirm", "workflow_event",
            "workflow_control", "workflow_get", "workflow_list", "workflow_board", "workflow_observed"})
        self.assertEqual(tools["workflow_create"]["inputSchema"]["required"], ["plan", "eventId"])
        role_properties = tools["workflow_create"]["inputSchema"]["properties"]["plan"]["properties"]["roles"]["items"]["properties"]
        self.assertIn("agentType", role_properties)
        self.assertIn("agentType", tools["workflow_event"]["inputSchema"]["properties"])
        self.assertEqual(tools["workflow_confirm"]["_meta"]["ui"]["visibility"], ["model"])
        self.assertEqual(tools["workflow_observed"]["_meta"]["ui"]["visibility"], ["app"])
        self.assertEqual(tools["workflow_get"]["_meta"]["ui"]["visibility"], ["model", "app"])
        for name in ("workflow_create", "workflow_update_draft", "workflow_confirm", "workflow_event",
                     "workflow_control", "workflow_list"):
            self.assertFalse(tools[name]["_meta"]["openai/widgetAccessible"], name)
        self.assertTrue(tools["workflow_get"]["_meta"]["openai/widgetAccessible"])
        self.assertTrue(tools["workflow_observed"]["_meta"]["openai/widgetAccessible"])
        self.assertEqual(tools["workflow_board"]["_meta"]["ui"]["resourceUri"], server.BOARD_URI)
        self.assertEqual(tools["workflow_board"]["_meta"]["ui"]["visibility"], ["model"])
        self.assertFalse(tools["workflow_board"]["_meta"]["openai/widgetAccessible"])
        self.assertEqual(tools["workflow_board"]["_meta"]["openai/outputTemplate"], server.BOARD_URI)
        self.assertEqual(tools["workflow_board"]["outputSchema"]["required"], ["run"])
        self.assertEqual(server.respond("resources/list")["resources"][0]["uri"], server.BOARD_URI)
        resource = server.respond("resources/read", {"uri": server.BOARD_URI})["contents"][0]
        self.assertIn("<html", resource["text"])
        self.assertEqual(resource["_meta"]["ui"]["csp"], {"connectDomains": [], "resourceDomains": []})

    def test_mcp_workflow_calls_keep_run_identity_and_report_tool_errors(self):
        created = self.tool_call("workflow_create", {"plan": sample_plan(), "eventId": "mcp-create"})
        run = created["structuredContent"]["run"]
        confirmed = self.tool_call("workflow_confirm", {"runId": run["id"], "expectedRevision": 0,
                                                            "eventId": "mcp-confirm", "confirmation": "用户确认"})
        run = confirmed["structuredContent"]["run"]
        got = self.tool_call("workflow_get", {"runId": run["id"]})["structuredContent"]["run"]
        self.assertEqual((got["id"], got["status"], got["revision"]), (run["id"], "running", 1))
        other = self.store.create(sample_plan() | {"title": "second"}, "other-run")
        second = self.tool_call("workflow_get", {"runId": other["id"]})["structuredContent"]["run"]
        self.assertNotEqual(got["id"], second["id"])
        invalid = self.tool_call("workflow_create", {"plan": {"bad": True}, "eventId": "bad-plan"})
        self.assertTrue(invalid["isError"])
        invalid_arguments = self.tool_call("workflow_get", {"runId": run["id"], "fallback": True})
        self.assertTrue(invalid_arguments["isError"])
        unknown = self.tool_call("no_such_tool", {})
        self.assertTrue(unknown["isError"])
        listed = self.tool_call("workflow_list", {})["structuredContent"]["runs"]
        self.assertEqual({item["id"] for item in listed}, {run["id"], other["id"]})

    def test_mcp_create_update_and_running_event_carry_agent_type_binding(self):
        plan = sample_plan()
        plan["roles"][0]["agentType"] = "research-v1"
        created = self.tool_call("workflow_create", {"plan": plan, "eventId": "agent-create"})
        run = created["structuredContent"]["run"]
        self.assertEqual(run["roles"][0]["agentType"], "research-v1")

        plan["roles"][0]["agentType"] = "research-v2"
        updated = self.tool_call("workflow_update_draft", {"runId": run["id"], "plan": plan,
                                                            "expectedRevision": run["revision"],
                                                            "eventId": "agent-update"})
        run = updated["structuredContent"]["run"]
        self.assertEqual(run["roles"][0]["agentType"], "research-v2")
        run = self.tool_call("workflow_confirm", {"runId": run["id"], "expectedRevision": run["revision"],
                                                  "eventId": "agent-confirm", "confirmation": "用户确认"})["structuredContent"]["run"]
        started = self.tool_call("workflow_event", {"runId": run["id"], "taskId": "t1", "status": "running",
                                                      "nativeId": "native-1", "agentType": "research-v2",
                                                      "expectedRevision": run["revision"], "eventId": "agent-start"})
        self.assertNotIn("isError", started)
        self.assertEqual(started["structuredContent"]["run"]["tasks"][0]["agentType"], "research-v2")

    def test_observed_is_app_tool_and_does_not_advance_revision(self):
        run = self.store.create(sample_plan(), "create-receipt")
        result = self.tool_call("workflow_observed", {"runId": run["id"], "clientId": "board-1", "revision": 0,
                                                      "rendered": [{"id": "t1", "status": "pending"}],
                                                      "visibility": "visible"})
        self.assertTrue(result["structuredContent"]["acknowledged"])
        self.assertEqual(self.store.get(run["id"])["revision"], 0)

    def test_jsonrpc_error_classes_and_notifications(self):
        init = server.handle_message(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
        self.assertEqual(init["result"]["serverInfo"]["name"], "codex-workflow")
        unknown = server.handle_message(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "no/such-method"}))
        self.assertEqual(unknown["error"]["code"], -32601)
        invalid = server.handle_message(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "bad:"}}))
        self.assertEqual(invalid["error"]["code"], -32602)
        parse = server.handle_message("{")
        self.assertEqual(parse["error"]["code"], -32700)
        notification = server.handle_message(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}))
        self.assertIsNone(notification)

    def test_stdio_process_uses_json_lines_without_import_side_effects(self):
        env = os.environ.copy()
        env["CODEX_WORKFLOW_DATA_DIR"] = self.temp.name
        messages = [
            {"jsonrpc": "2.0", "id": "a", "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": "b", "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": "c", "method": "bad/method", "params": {}},
        ]
        proc = subprocess.run([sys.executable, str(MCP_DIR / "server.py")], input="\n".join(json.dumps(item) for item in messages) + "\n",
                              text=True, capture_output=True, env=env, timeout=20, check=True)
        self.assertEqual(proc.stderr, "")
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual([item["id"] for item in replies], ["a", "b", "c"])
        self.assertEqual(replies[-1]["error"]["code"], -32601)
        self.assertEqual({item["name"] for item in replies[1]["result"]["tools"]},
                         {"workflow_create", "workflow_update_draft", "workflow_confirm", "workflow_event",
                          "workflow_control", "workflow_get", "workflow_list", "workflow_board", "workflow_observed"})


if __name__ == "__main__":
    unittest.main()
