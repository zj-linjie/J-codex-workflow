"""Dependency-free JSON-RPC stdio MCP server for J-codex-workflow."""
from __future__ import annotations

import json
import pathlib
import sys
from typing import Any, Dict, Optional

try:  # Support both script execution and package imports in tests/embedders.
    from .store import Store, WorkflowError
except ImportError:  # pragma: no cover - used when launched as a stdio script
    from store import Store, WorkflowError


ROOT = pathlib.Path(__file__).resolve().parent.parent
BOARD_URI = "ui://codex-workflow/board.html"
BOARD_MIME = "text/html;profile=mcp-app"
_STORE: Optional[Store] = None


def get_store() -> Store:
    global _STORE
    if _STORE is None:
        _STORE = Store()
    return _STORE


def use_store(store: Store) -> None:
    """Inject a store for embedding or tests; importing the module is inert."""
    global _STORE
    _STORE = store


def _object_schema(properties: Dict[str, Any], required: Any) -> Dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


STRING = {"type": "string"}
INTEGER = {"type": "integer", "minimum": 0}
PLAN = {
    "type": "object",
    "properties": {
        "title": STRING, "goal": STRING,
        "roles": {"type": "array", "items": {"type": "object", "properties": {
            "id": STRING, "name": STRING, "kind": {"type": "string", "enum": ["native", "primary"]},
            "instructions": STRING, "agentType": STRING},
            "required": ["id", "name", "kind", "instructions"], "additionalProperties": False}},
        "phases": {"type": "array", "items": {"type": "object", "properties": {
            "id": STRING, "name": STRING}, "required": ["id", "name"], "additionalProperties": False}},
        "tasks": {"type": "array", "items": {"type": "object", "properties": {
            "id": STRING, "name": STRING, "roleId": STRING, "phaseId": STRING,
            "dependsOn": {"type": "array", "items": STRING}, "acceptanceCriteria": STRING},
            "required": ["id", "name", "roleId", "phaseId", "dependsOn", "acceptanceCriteria"], "additionalProperties": False}}
    },
    "required": ["title", "goal", "roles", "phases", "tasks"],
    "additionalProperties": False,
}


def _tool(name: str, title: str, description: str, schema: Dict[str, Any], required: Any,
          visibility: Any, *, board: bool = False, read_only: bool = False) -> Dict[str, Any]:
    widget_accessible = "model" not in visibility or "app" in visibility
    meta: Dict[str, Any] = {"ui": {"visibility": visibility},
                            "openai/widgetAccessible": widget_accessible}
    if board:
        meta = {
            "ui": {"resourceUri": BOARD_URI, "visibility": visibility},
            "openai/ui": {"entrypoints": [{"type": "thread"}]},
            "openai/outputTemplate": BOARD_URI,
            "openai/widgetAccessible": False,
        }
    if name == "workflow_list":
        output_schema = {"type": "object", "properties": {"runs": {"type": "array", "items": {"type": "object"}}},
                         "required": ["runs"], "additionalProperties": False}
    elif name == "workflow_observed":
        output_schema = {"type": "object", "properties": {"acknowledged": {"type": "boolean"}, "receipt": {"type": "object"}},
                         "required": ["acknowledged", "receipt"], "additionalProperties": False}
    else:
        output_schema = {"type": "object", "properties": {"run": {"type": "object"}},
                         "required": ["run"], "additionalProperties": False}
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": _object_schema(schema, required),
        "outputSchema": output_schema,
        "annotations": {"readOnlyHint": read_only, "destructiveHint": False, "openWorldHint": False},
        "_meta": meta,
    }


def tools_list() -> Dict[str, Any]:
    model = ["model"]
    both = ["model", "app"]
    app = ["app"]
    return {"tools": [
        _tool("workflow_create", "创建工作流草案", "保存经过严格校验的工作流草案；不会开始执行。",
              {"plan": PLAN, "eventId": STRING}, ["plan", "eventId"], model),
        _tool("workflow_update_draft", "修改工作流草案", "修改未确认的草案并提升计划版本。",
              {"runId": STRING, "plan": PLAN, "expectedRevision": INTEGER, "eventId": STRING},
              ["runId", "plan", "expectedRevision", "eventId"], model),
        _tool("workflow_confirm", "确认并启动工作流", "记录用户确认摘要并打开执行门禁。",
              {"runId": STRING, "expectedRevision": INTEGER, "eventId": STRING, "confirmation": STRING},
              ["runId", "expectedRevision", "eventId", "confirmation"], model),
        _tool("workflow_event", "上报工作流任务状态", "记录原生任务进展、结果或主代理验收；执行必须满足阶段与依赖门禁。",
              {"runId": STRING, "taskId": STRING, "status": {"type": "string", "enum": ["running", "submitted", "done", "failed"]},
               "nativeId": STRING, "message": STRING, "summary": STRING,
               "agentType": STRING,
               "artifacts": {"type": "array", "items": STRING}, "acceptance": STRING,
               "expectedRevision": INTEGER, "eventId": STRING},
              ["runId", "taskId", "status", "expectedRevision", "eventId"], model),
        _tool("workflow_control", "控制工作流状态", "按用户决定暂停、恢复、重试指定失败任务或取消运行。",
              {"runId": STRING, "action": {"type": "string", "enum": ["pause", "resume", "retry", "cancel"]},
               "reason": STRING, "taskId": STRING, "expectedRevision": INTEGER, "eventId": STRING},
              ["runId", "action", "reason", "expectedRevision", "eventId"], model),
        _tool("workflow_get", "读取工作流运行", "读取指定 runId 的持久状态与最近事件。",
              {"runId": STRING}, ["runId"], both, read_only=True),
        _tool("workflow_list", "列出工作流运行", "列出本机最近的工作流运行摘要。",
              {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}, [], model, read_only=True),
        _tool("workflow_board", "打开工作流看板", "为指定 runId 打开只读内联看板。",
              {"runId": STRING}, ["runId"], model, board=True, read_only=True),
        _tool("workflow_observed", "记录看板呈现回执", "保存看板报告的 DOM 呈现摘要，不改变工作流版本。",
              {"runId": STRING, "clientId": STRING, "revision": INTEGER,
               "rendered": {"type": "array", "items": {"type": "object", "properties": {
                   "id": STRING, "status": STRING}, "required": ["id", "status"], "additionalProperties": False}},
               "visibility": {"type": "string", "enum": ["visible", "hidden", "prerender"]}},
              ["runId", "clientId", "revision", "rendered", "visibility"], app, read_only=True),
    ]}


def _content_result(value: Dict[str, Any], text: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "structuredContent": value}


def _tool_error(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _validate_schema(value: Any, schema: Dict[str, Any], where: str) -> None:
    """Validate the JSON shape published to MCP clients before dispatch."""
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise ValueError(where + " must be an object")
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(where + " is missing " + key)
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise ValueError(where + " has unsupported fields: " + ", ".join(sorted(extras)))
        for key, item in value.items():
            if key in properties:
                _validate_schema(item, properties[key], where + "." + key)
    elif kind == "array":
        if not isinstance(value, list):
            raise ValueError(where + " must be an array")
        for index, item in enumerate(value):
            _validate_schema(item, schema.get("items", {}), "%s[%d]" % (where, index))
    elif kind == "string":
        if not isinstance(value, str):
            raise ValueError(where + " must be a string")
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(where + " must be an integer")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(where + " is below its minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(where + " is above its maximum")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(where + " has an unsupported value")


def _call_tool(params: Any) -> Dict[str, Any]:
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        raise ValueError("tools/call requires a tool name")
    name = params["name"]
    arguments = params.get("arguments", {})
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    definitions = {item["name"]: item for item in tools_list()["tools"]}
    if name not in definitions:
        return _tool_error("Unknown tool: " + name)
    try:
        _validate_schema(arguments, definitions[name]["inputSchema"], "arguments")
    except ValueError as error:
        return _tool_error(str(error))
    try:
        store = get_store()
        if name == "workflow_create":
            run = store.create(arguments.get("plan"), arguments.get("eventId"))
            return _content_result({"run": run}, "工作流草案已保存，等待用户确认。")
        if name == "workflow_update_draft":
            run = store.update_draft(arguments.get("runId"), arguments.get("plan"),
                                     arguments.get("expectedRevision"), arguments.get("eventId"))
            return _content_result({"run": run}, "工作流草案已更新，原确认不再适用。")
        if name == "workflow_confirm":
            run = store.confirm(arguments.get("runId"), arguments.get("expectedRevision"),
                                arguments.get("eventId"), arguments.get("confirmation"))
            return _content_result({"run": run}, "工作流已确认并进入执行状态。")
        if name == "workflow_event":
            fields = {key: arguments[key] for key in ("nativeId", "agentType", "message", "summary", "artifacts", "acceptance") if key in arguments}
            run = store.task_event(arguments.get("runId"), arguments.get("taskId"), arguments.get("status"),
                                   arguments.get("expectedRevision"), arguments.get("eventId"), **fields)
            return _content_result({"run": run}, "工作流任务状态已记录。")
        if name == "workflow_control":
            run = store.control(arguments.get("runId"), arguments.get("action"), arguments.get("reason"),
                                arguments.get("expectedRevision"), arguments.get("eventId"),
                                task_id=arguments.get("taskId"))
            return _content_result({"run": run}, "工作流控制决定已记录。")
        if name == "workflow_get":
            return _content_result({"run": store.get(arguments.get("runId"))}, "工作流状态已读取。")
        if name == "workflow_list":
            return _content_result({"runs": store.list_runs(arguments.get("limit", 20))}, "工作流运行列表已读取。")
        if name == "workflow_board":
            return _content_result({"run": store.get(arguments.get("runId"))}, "正在显示指定工作流的只读看板。")
        if name == "workflow_observed":
            receipt = store.observed(arguments.get("runId"), arguments.get("clientId"), arguments.get("revision"),
                                     arguments.get("rendered"), arguments.get("visibility"))
            return _content_result({"acknowledged": True, "receipt": receipt}, "看板呈现回执已保存。")
        return _tool_error("Unknown tool: " + name)
    except WorkflowError as error:
        return _tool_error(str(error))
    except Exception:
        return _tool_error("The workflow store could not complete the operation.")


def respond(method: str, params: Any = None) -> Dict[str, Any]:
    """Handle a single MCP request; raises ValueError for malformed parameters."""
    params = {} if params is None else params
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}, "resources": {},
                                 "extensions": {"io.modelcontextprotocol/ui": {}}},
                "serverInfo": {"name": "codex-workflow", "version": "0.2.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return tools_list()
    if method == "resources/list":
        return {"resources": [{"uri": BOARD_URI, "name": "J-codex-workflow 看板", "mimeType": BOARD_MIME}]}
    if method == "resources/templates/list":
        return {"resourceTemplates": []}
    if method == "resources/read":
        if params.get("uri") != BOARD_URI:
            raise ValueError("Unknown resource URI")
        board_path = ROOT / "ui" / "board.html"
        if not board_path.is_file():
            raise RuntimeError("workflow board resource is missing")
        return {"contents": [{"uri": BOARD_URI, "mimeType": BOARD_MIME, "text": board_path.read_text(encoding="utf-8"),
                              "_meta": {"ui": {"prefersBorder": True,
                                               "csp": {"connectDomains": [], "resourceDomains": []}},
                                        "openai/ui": {"availableDisplayModes": ["inline", "fullscreen"]}}}]}
    if method == "tools/call":
        return _call_tool(params)
    raise LookupError("Unknown method: " + method)


def _rpc_error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_message(raw: str) -> Optional[Dict[str, Any]]:
    """Parse and dispatch a JSON-RPC line, returning None for notifications."""
    try:
        message = json.loads(raw)
    except (TypeError, ValueError):
        return _rpc_error(None, -32700, "Parse error")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _rpc_error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")
    request_id = message.get("id")
    if "id" not in message:
        # MCP notifications do not receive a response.  The UI lifecycle is
        # owned by the host; accepting notifications here keeps stdio clean.
        return None
    method = message["method"]
    try:
        result = respond(method, message.get("params"))
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except LookupError as error:
        return _rpc_error(request_id, -32601, str(error))
    except ValueError as error:
        return _rpc_error(request_id, -32602, str(error))
    except WorkflowError as error:
        return _rpc_error(request_id, -32602, str(error))
    except Exception:
        return _rpc_error(request_id, -32603, "Internal error")


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        response = handle_message(line)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
