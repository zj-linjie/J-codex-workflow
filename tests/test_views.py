"""MCP response projections: smaller coordination payloads, unchanged state gates."""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "plugin" / "mcp"))
import server
from store import Store
from test_store import sample_plan


class ResponseViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(pathlib.Path(self.temp.name) / "views.sqlite3")
        server.use_store(self.store)
        self.serial = 0

    def tearDown(self):
        server._STORE = None
        self.temp.cleanup()

    def call(self, name, **args):
        return server.respond("tools/call", {"name": name, "arguments": args})

    def success(self, name, **args):
        result = self.call(name, **args)
        self.assertNotIn("isError", result, result)
        return result["structuredContent"]["run"]

    def write(self, name, run, **args):
        self.serial += 1
        return self.success(name, runId=run["id"], expectedRevision=run["revision"],
                            eventId="write-%d" % self.serial, view="compact", **args)

    def create(self, plan=None):
        return self.success("workflow_create", plan=plan or sample_plan(), eventId="create", view="compact")

    def test_compact_flow_drives_parallel_tasks_and_next_phase_without_get(self):
        run = self.create()
        self.assertEqual(run["readyTaskIds"], [])
        # Compact board results still bind the same run ID; the page reads full itself.
        board = self.success("workflow_board", runId=run["id"], view="compact")
        self.assertEqual(board, run)
        run = self.write("workflow_update_draft", run, plan=sample_plan())
        self.assertEqual(run["planVersion"], 2)
        run = self.write("workflow_confirm", run, confirmation="确认当前草案")
        self.assertEqual(run["readyTaskIds"], ["t1", "t1b"])
        for task_id in ("t1", "t1b"):
            run = self.write("workflow_event", run, taskId=task_id, status="running", nativeId="native-" + task_id)
            task = next(t for t in run["tasks"] if t["id"] == task_id)
            self.assertEqual(task["nativeId"], "native-" + task_id)
            run = self.write("workflow_event", run, taskId=task_id, status="submitted", summary="真实返回")
            self.assertNotIn("t2", run["readyTaskIds"])
            run = self.write("workflow_event", run, taskId=task_id, status="done", acceptance="已读产物并核对标准")
        self.assertEqual(run["readyTaskIds"], ["t2"])
        run = self.write("workflow_event", run, taskId="t2", status="running")
        run = self.write("workflow_event", run, taskId="t2", status="submitted", summary="主代理完成")
        run = self.write("workflow_event", run, taskId="t2", status="done", acceptance="最终核对通过")
        self.assertEqual((run["status"], run["readyTaskIds"]), ("completed", []))
        self.assertNotIn("usage", run)
        with mock.patch.object(server, "collect_usage", return_value={"status": "unavailable"}) as collect:
            usage = self.success("workflow_usage", runId=run["id"], view="compact")
            self.assertEqual(usage["usage"], {"status": "unavailable"})
            collect.assert_called_once_with(None)
        read = self.success("workflow_get", runId=run["id"], view="compact")
        self.assertNotIn("usage", read)
        self.assertEqual(read["revision"], run["revision"])

    def test_ready_tasks_respect_same_phase_dependencies_pause_failure_retry_and_cancel(self):
        plan = sample_plan()
        plan["tasks"][1]["dependsOn"] = ["t1"]
        run = self.write("workflow_confirm", self.create(plan), confirmation="确认")
        self.assertEqual(run["readyTaskIds"], ["t1"])
        run = self.write("workflow_control", run, action="pause", reason="用户暂停")
        self.assertEqual(run["readyTaskIds"], [])
        run = self.write("workflow_control", run, action="resume", reason="用户恢复")
        self.assertEqual(run["readyTaskIds"], ["t1"])
        run = self.write("workflow_event", run, taskId="t1", status="running", nativeId="native-1")
        run = self.write("workflow_event", run, taskId="t1", status="failed", message="真实失败")
        self.assertEqual((run["status"], run["readyTaskIds"]), ("paused", []))
        run = self.write("workflow_control", run, action="retry", taskId="t1", reason="用户重试")
        self.assertEqual(run["readyTaskIds"], ["t1"])
        self.assertEqual((run["tasks"][0]["attempt"], run["tasks"][0]["nativeId"]), (2, None))
        run = self.write("workflow_control", run, action="cancel", reason="用户取消")
        self.assertEqual((run["status"], run["readyTaskIds"]), ("cancelled", []))

    def test_view_changes_only_response_and_invalid_view_never_writes(self):
        plan = sample_plan()
        full = self.success("workflow_create", plan=plan, eventId="create")
        compact = self.success("workflow_create", plan=plan, eventId="create", view="compact")
        self.assertEqual((compact["id"], compact["revision"]), (full["id"], 0))
        self.assertEqual(self.success("workflow_get", runId=full["id"], view="full"), full)
        self.assertEqual(self.success("workflow_board", runId=full["id"]), full)
        invalid = self.call("workflow_confirm", runId=full["id"], expectedRevision=0,
                            eventId="confirm", confirmation="确认", view="invalid")
        self.assertTrue(invalid["isError"])
        self.assertEqual(self.store.get(full["id"]), full)
        args = dict(runId=full["id"], expectedRevision=0, eventId="confirm", confirmation="确认")
        confirmed = self.success("workflow_confirm", **args, view="compact")
        replay = self.success("workflow_confirm", **args, view="full")
        self.assertEqual(replay["revision"], confirmed["revision"])
        self.assertEqual(len(self.store.get(full["id"])["events"]), 2)
        stale = self.call("workflow_control", runId=full["id"], expectedRevision=0,
                          eventId="stale", action="pause", reason="过期", view="compact")
        self.assertTrue(stale["isError"])
        self.assertEqual(self.store.get(full["id"])["revision"], 1)
        bad_create = self.call("workflow_create", plan=plan, eventId="bad", view=None)
        self.assertTrue(bad_create["isError"])
        self.assertEqual(len(self.store.list_runs()), 1)

    def test_compact_omits_long_fields_and_history_even_when_results_are_retained(self):
        plan = sample_plan()
        plan["roles"][0]["instructions"] = "known-evidence " * 400
        run = self.write("workflow_confirm", self.create(plan), confirmation="确认")
        run = self.write("workflow_event", run, taskId="t1", status="running", nativeId="native-1")
        run = self.write("workflow_event", run, taskId="t1", status="submitted", summary="result " * 400,
                         artifacts=["report.md"])
        run = self.write("workflow_event", run, taskId="t1", status="done", acceptance="verified " * 300)
        full = self.success("workflow_get", runId=run["id"])
        self.assertEqual(set(run), {"id", "title", "status", "planVersion", "revision", "pauseReason",
                                    "sessionId", "updatedAt", "view", "tasks", "readyTaskIds"})
        for task in run["tasks"]:
            self.assertEqual(set(task), {"id", "status", "nativeId", "agentType", "attempt"})
        self.assertGreater(len(full["events"]), 0)
        self.assertEqual(full["tasks"][0]["artifacts"], ["report.md"])
        self.assertLess(len(json.dumps(run).encode()), len(json.dumps(full).encode()) / 5)

    def test_compact_replays_legacy_receipt_without_replacing_it_with_current_state(self):
        full = self.success("workflow_create", plan=sample_plan(), eventId="create")
        legacy = json.loads(json.dumps(full))
        legacy.pop("sessionId")
        legacy.pop("usage")
        for task in legacy["tasks"]:
            task.pop("agentType")
        with self.store._connection() as db:
            db.execute("UPDATE idempotency SET result_json=? WHERE event_id=?", (json.dumps(legacy), "create"))
        current = self.write("workflow_confirm", full, confirmation="确认")
        for view in ("full", "compact"):
            replay = self.success("workflow_create", plan=sample_plan(), eventId="create", view=view)
            self.assertEqual((replay["status"], replay["revision"], replay["sessionId"]), ("draft", 0, None))
            if view == "compact":
                self.assertTrue(all(task["agentType"] is None for task in replay["tasks"]))
                self.assertEqual(replay["readyTaskIds"], [])
        self.assertEqual(self.store.get(full["id"])["revision"], current["revision"])


if __name__ == "__main__":
    unittest.main()
