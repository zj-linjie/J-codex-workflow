# J-codex-workflow 首版最终独立复核

日期：2026-10-14。结论：在本轮审阅与验证范围内，未发现需要阻止首版代码/本地安装包交付的实质缺陷。正式 Codex 宿主 A9 原生端到端验收仍未完成；此结论不将开发浏览器样例、DOM 回执或历史探针视为新插件的 A9 证据。

## 范围与方法

独立阅读 `PRD.md`、`docs/CONTRACT.md`、`AGENTS.md`、`README.md`、全部 `plugin/` 源码和技能参考、两个示例、打包/开发预览脚本及全部测试。包含已经稳定的 `plugin/ui/board.html`。历史 `docs/REVIEW.md` 仅用于定位待复核项目，结论依据当前源码与本轮执行结果。

复跑 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v`：17/17 通过。测试使用临时数据库；未操作默认用户数据库，未安装插件或启动原生工作流子代理。

另在临时目录独立执行了多失败场景：三个并行任务中两个失败，第三个在暂停期间返回；只重试其中一个失败任务仍保持暂停且不能派发，第二个明确重试后才恢复，第三个提交结果完整保留。携带无关字段的验收事件被拒绝且数据库快照不变。

## 已修项目复核

- **快照一致性已修。** `plugin/mcp/store.py:610` 的 `get` 对完整 `_snapshot` 开启显式读事务；写操作继续在 `BEGIN IMMEDIATE` 事务内提交状态、事件与幂等结果。交错读写回归 `tests/test_store.py:198` 通过：确认在读取过程中提交，读者仍返回同一个旧版本的运行与事件。
- **MCP 工具可见性已修。** `plugin/mcp/server.py:60` 同时保留 board 的 `resourceUri` 和 `visibility=["model"]`。最终工具输出中，create/update_draft/confirm/event/control/list/board 为 model-only 且 `openai/widgetAccessible=false`；get 为 model+app 且 true；observed 为 app-only 且 true。源码、最终安装包和工具矩阵回归一致。
- **暂停策略符合已确认意图。** `plugin/mcp/store.py:480` 拒绝暂停期间的新启动，`:503` 允许已运行任务提交，`:512` 拒绝暂停验收，`:574` 阻止 resume 绕过 failed。技能明确保留在途返回、由用户决定重试/恢复、取消时先调用真实原生中断；状态写入本身不冒充代理控制。

## 其他核验

- 草案门禁、计划修改版本、阶段/依赖约束、真实 nativeId 要求、submitted 与 done 区分、终态拒绝事件、幂等重放、CAS 竞争、多运行隔离和重启读取，与契约一致。确认来源、原生身份和验收质量依赖主代理的边界在 PRD/README/技能中明确披露。
- 看板静态审读确认：runId 一次绑定后不切换；get 返回身份不匹配时停止；轮询单请求进行、失败保留旧状态并重试，陈旧状态有提示；completed/cancelled 停止轮询；更新保留展开任务与键盘焦点；用户文本通过 textContent 呈现。脚本与样式 CSP 哈希重新计算一致，无外链脚本/网络连接。
- 本轮未重复主代理已执行的浏览器渲染检查。主代理所报草案/执行/待验收/暂停/完成、展开保留、断连恢复、390px 和终态停止，均应归类为明确标注的 development fixture UI 检查，不能替代 Codex 宿主 A9。
- 临时构建并启动打包后的 stdio 服务的测试通过；技能、契约、示例、UI 与 MCP 文件均在包内。技能参考的 examples 已随包提供，历史提到的死分支与未使用映射已删除。没有发现需要额外重构才能解决的清晰度问题。

## 最终包核对

首次核对时 staging/dist 仍是旧临时包，两个历史修复尚未进入该包。主代理随后重新构建，本轮再次独立比对通过，当前没有该项遗留。

- `<BUILD_WORKSPACE>/plugin/` 与 `<PROJECT_ROOT>/plugin/` 的全部非缓存文件逐字节一致。
- 两处工程的安装包分别与其当前源码、CONTRACT 逐字节一致；两个 zip 内的全部包文件分别与生成目录一致。
- 最终项目 `.mcp.json` 指向 `<PROJECT_ROOT>/dist/marketplace/plugins/codex-workflow/mcp/server.py`，解释器文件存在；staging 包使用各自本地路径。两处 zip 哈希不同由生成的本机启动路径不同造成，符合 README 的本地构建说明。
- 正式 zip：`<PROJECT_ROOT>/dist/codex-workflow-0.1.0.zip`，SHA-256 `ffac5f5684a11635e99e0659760de2ebe27a7fd9e28f880e8c36402580048b76`。
- staging zip SHA-256：`38fb2b820e9272987bfe44162b5fdb66cf8e7e9478c5a05bc26293d8f6298120`。

仅新增此报告，未修改被审源码或默认用户数据。读取最终包工具元数据的 Python import 可能生成了 `__pycache__`；已通知主代理在交付前清理该临时解释器缓存。未启动任何子代理。
