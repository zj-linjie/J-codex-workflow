# 0.3 接口契约

Python 3.9+ 标准库。SQLite 数据默认在 `~/.local/share/codex-workflow/workflows.sqlite3`，环境变量 `CODEX_WORKFLOW_DATA_DIR` 可覆盖；测试必须临时目录。工程 staging 不作为运行数据。

计划 plan:
```json
{"title":"调研并评审","goal":"输出可验证建议","roles":[{"id":"analyst","name":"调研员","kind":"native","agentType":"WorkflowExpertAdvisor","instructions":"核查依据"},{"id":"owner","name":"主代理","kind":"primary","instructions":"验收"}],"phases":[{"id":"research","name":"并行调研"},{"id":"review","name":"验收交付"}],"tasks":[{"id":"t1","name":"核对能力","roleId":"analyst","phaseId":"research","dependsOn":[],"acceptanceCriteria":"可追溯依据"},{"id":"t2","name":"汇总结论","roleId":"owner","phaseId":"review","dependsOn":["t1"],"acceptanceCriteria":"结论覆盖目标"}]}
```

严格验证字符串长度、非空、唯一 ID、有效引用、DAG、依赖不能指向后续阶段、每阶段有任务。max roles=32, phases=12, tasks=64。角色 kind native/primary。

0.2新增：native角色可带`agentType`（标识符，最多64字符），primary禁止该字段。新技能生成的native角色必须明确绑定；旧计划可省略以兼容读取。绑定类型是角色定义，任务中的agentType是启动事件上报的执行类型，均不作为宿主身份认证。Astra咨询策略位于独立`settings/orchestrator.json`，不属于角色表。

字段上限（字符数）：title 120、goal 4000、role.name 80、role.instructions 8000、phase.name 100、task.name 160、acceptanceCriteria 2000、nativeId 256、message/summary/acceptance 4000、confirmation/reason 2000、eventId/clientId 128。计划 ID 最长64，使用字母数字开头及字母数字点下划线连字符；runId 最长128，另允许冒号。artifacts 最多32项，每项2048；list limit 1–100。

状态统一返回 `{run: {...}}`。run 包含 `id,title,goal,planVersion,revision,status,roles,phases,tasks,events,createdAt,updatedAt,pauseReason`。时间 ISO UTC 字符串。roles/phases 保留计划字段；tasks 计划字段加 `status,nativeId,agentType,message,summary,artifacts,acceptance,attempt`。events 最近 100 条。phase 进度由 UI 从 task 计算。

MCP 工具/本地 Python Store 对应（工作流状态写入需 eventId 唯一字符串；create 外需 expectedRevision 非负整数，派生统计与呈现回执除外）：

- `workflow_create({plan,eventId,sessionId?})` / `Store.create(plan,event_id,session_id=None)` → run，草案。sessionId为当前主会话实际ID（最长128，标识符含冒号），只在创建时绑定；缺省兼容旧调用。
- `workflow_update_draft({runId,plan,expectedRevision,eventId})` / `Store.update_draft(...)`。
- `workflow_confirm({runId,expectedRevision,eventId,confirmation})` / `Store.confirm(...)`，confirmation 为真实用户确认内容摘要，不能空。
- `workflow_event({runId,taskId,status,nativeId?,agentType?,message?,summary?,artifacts?,acceptance?,expectedRevision,eventId})` / `Store.task_event(run_id,task_id,status,expected_revision,event_id,**fields)`。
- `workflow_control({runId,action,reason,taskId?,expectedRevision,eventId})` / `Store.control(...)`，action pause/resume/retry/cancel。reason 为用户决定/暂停原因；retry 需 taskId=failed 任务。
- `workflow_get({runId})` / `Store.get(run_id)`，UI 和 model 可见，只读。
- `workflow_usage({runId})` / `Store.capture_usage(run_id,collector)`，UI和model可见，仅completed允许；首次本地采集并保存派生快照，重复调用读取缓存，不改revision/status/events。采集器在SQLite写事务外运行，并发首次采集先保存者生效。
- `workflow_list({limit?})` / `Store.list_runs(limit=20)` → `{runs:[{id,title,status,updatedAt,...}]}`，仅 model。
- `workflow_board({runId})` 返回 run 并绑定 `ui://codex-workflow/board.html`，仅 model 入口，UI 自行调用 get。
- `workflow_observed({runId,clientId,revision,rendered,visibility})` app-only，可保存至独立 receipts 表，最多每运行200条；不改 revision。DOM 回执不是独立截图。

幂等语义：相同 eventId 且相同操作/参数返回已提交结果（即使 expectedRevision 过期），相同 eventId 不同参数拒绝。create 幂等跨进程。各次写入 revision+1（create=0）；updateDraft planVersion+1（初始1）。状态写入和事件记录单事务。Store 返回值直接 run dict，list 返回概要列表。错误使用 `WorkflowError`，MCP tools/call 返回 isError，无静默成功；协议未知方法 -32601，非法参数 -32602。server 可 import，只有 __main__ 开始读 stdin。

MCP UI 沿用已实测 JSON-RPC postMessage 握手：ui/initialize、ui/notifications/initialized、ui/notifications/tool-result；get 输入 runId 必填，错误不切换其他运行。页面可用的只读/派生工具为 get、observed、usage。model 状态写入工具 ui.visibility=[model]；board 入口采用已实测 metadata。零外链脚本、零 localhost iframe、严格 CSP、文本安全呈现。

事件字段按状态限制：running 只接受 nativeId/agentType/message；绑定角色必须上报匹配agentType，primary禁止原生身份字段；submitted 接受 message/summary/artifacts，summary必填；done 接受 message/artifacts/acceptance，acceptance必填；failed 接受 message/artifacts，message必填。无关字段拒绝。普通暂停保留在途任务；paused允许其提交/失败收尾，但不能开始或完成验收。resume不能绕过failed，需用户明确retry；retry重置指定失败任务并增加attempt，还有其他failed时保持paused。

代码位置：`plugin/mcp/store.py` 与 `server.py`；看板 `plugin/ui/board.html`；技能 `plugin/skills/codex-workflow/`。可选 CLI 由主代理补齐。所有模块不依赖绝对 authoring 路径。

retry同时清空任务nativeId和执行agentType，保留角色agentType绑定；旧JSON缺少agentType时快照返回null。

0.3新增run.sessionId和run.usage（缺省null），使用附加run_sessions/run_usage表兼容既有SQLite库。usage由本地collector生成，包含version/status/capturedAt/lastEventAt/sessionId/scope/source/totals/speed/models/sessions/coverage/notes；status=available|partial|unavailable，scope=session_tree。totals为inputTokens/cachedInputTokens/uncachedInputTokens/outputTokens/reasoningOutputTokens/totalTokens/tokenEvents；无用量记录返回null。speed为samples/meanTokensPerSecond/medianTokensPerSecond/p10TokensPerSecond/p90TokensPerSecond；无样本返回null。models按模型聚合，sessions按会话与模型细分；coverage报告发现与有用量会话数、坏行、重复文件和跳过的重复事件。采集不返回原始内容、工具参数或日志路径，不估算费用。

workflow_usage的readOnlyHint表示不控制工作流；它与呈现回执一样只写本地派生记录。普通get/board不会扫描日志。完成面板先停止工作流轮询，再独立读取一次usage；统计传输失败只显示重试入口，不改变验收结果。全会话累计、速率算法和落盘边界详见技能session-usage引用。
