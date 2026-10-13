# 首版独立审阅

审阅日期：2026-10-14。范围：`PRD.md`、`docs/CONTRACT.md`、`plugin/mcp/store.py`、`server.py`、`plugin/skills/**`、`scripts/package.py`、`README.md`、全部现有 Python 测试。按父任务要求，本轮未评审正在修改的 `board.html`。本报告记录首次稳定实现的审阅结果；修复后的结论需要复核。

## Material findings

### P2 — 只读快照能混入比自身 revision 更新的事件

位置：`plugin/mcp/store.py:311`（`_snapshot` 中的两个 SELECT）、`plugin/mcp/store.py:614`（`get` 未开启读事务）。连接使用 `isolation_level=None`；`get` 先读取 runs 行，随后单独读取 events，两个语句没有共同 SQLite 快照。

已复现：在临时 SQLite 中创建 revision=0 的 draft；通过 SQLite trace callback 精确安排第二个 Store 在读取 events 前提交 confirm。`get` 返回 `revision=0,status=draft`，同时 `events` 包含 revision=1 的 `confirmed`。这与任一真实提交版本均不一致，会让轮询页面或恢复调度依据相互冲突的状态和历史作判断。写事务中的 `_snapshot` 不受该问题影响。

建议：对 `get` 的完整 `_snapshot` 开启一个显式读事务；加入双连接交错读写回归，断言所有返回事件的 revision 不超过 run.revision，且行数据和事件属于同一快照。

### P2 — 看板入口丢失 model-only 声明，兼容权限字段与写工具声明相反

位置：`plugin/mcp/server.py:62`、`:63`、`:65`。普通工具统一声明 `openai/widgetAccessible=true`，包括 create/confirm/event/control；board 分支又替换了整个 `_meta.ui`，丢弃传入的 `visibility=["model"]`。

静态输出已验证：`tools_list()` 中 `workflow_board._meta.ui` 只有 `resourceUri`。标准中省略 visibility 默认允许 model 和 app，因而 board 不符合 CONTRACT 的“仅 model 入口”和“页面只读工具只有 get、observed”。此外，写工具虽然有现代 `visibility=["model"]`，却同时用旧兼容字段声明 widget 可调用；兼容路径可能违反预期的只读边界。本轮未声称已在真实 Codex 宿主复现旧字段绕过。

依据：[MCP Apps 官方规范](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/draft/apps.mdx)规定默认双可见、由宿主拒绝无 app 可见性的页面调用；[官方兼容字段映射](https://github.com/modelcontextprotocol/ext-apps/blob/main/docs/migrate_from_openai_apps.md)将 widgetAccessible=true 对应为包含 app。

建议：board 的 ui 同时保留 `resourceUri` 和传入的 visibility；仅当 visibility 包含 app 时设置 widgetAccessible=true。对全部工具矩阵做回归，而不只检查 confirm/get/observed 三个条目。

## Verified behavior and limits

- 现有 15 项 unittest 全部通过，包含跨进程 create 幂等、CAS 竞争、草案确认、依赖、返回与验收、失败暂停、重试、取消、运行隔离、重启读取、MCP stdio。
- 在独立临时目录调用 `package.build` 成功；用生成 `.mcp.json` 的命令与路径启动打包后的 server，正常返回 9 个工具、exit=0、stderr 为空。README 的安装命令语法与本机 `codex plugin` 帮助一致。未更改用户插件安装配置，故不将此当作实际安装成功或正式原生 A9 验收。
- 草案未确认的执行事件、过期 CAS、跨参数 eventId 重用均被原子拒绝；已提交事件重放返回原提交结果；没有发现跨 run 状态覆盖。
- 暂停仍允许 running 子代理 submitted/failed，done 被拒绝；技能明确停止新派发、保留真实返回，并在取消时先实际中断。submitted→failed 也被允许；服务不认证失败是否来自原生结束或主动验收拒绝，因此“暂停期间不验收”仍需技能约束。按父任务澄清，本项作为约束说明，不列为确定缺陷。
- 原生身份、用户确认来源、取消是否真实中断依赖主代理执行；这与 PRD 明示边界一致，后端记录不能单独证明。

## Optional simplification / documentation

- `store.py:533` 的 failed 分支检查 `summary in cleaned` 不可达：该状态在前面的 allowed_by_status 中禁止 summary。可删除此死分支，行为不变。
- `plan-schema.md:35` 提到 `examples/`，生成插件不包含该目录。可随包带示例，或删除该句；它不阻止核心 MCP 启动。
- 重复的初始 task-state 字典可提取小型工厂，但本轮没有证据表明需要较大的状态机重构。

本轮仅新增此报告，未修改被审源码；未启动任何子代理。
