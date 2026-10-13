# J-codex-workflow

[English](README.md) · [简体中文](README.zh-CN.md)

在 Codex 会话中定义角色与阶段，确认方案后调度原生子代理，通过动态内联看板查看执行进度。

**早期预览版 · v0.1.1。** 本地 17 项测试全部通过；正式安装包在 Codex 桌面宿主中的完整工作流验收仍待完成。此前的原生 MCP 探针已验证：真实子代理执行期间，会话内联组件可以自动更新。

## 工作方式

1. 用自然语言描述目标，主代理生成角色、阶段、依赖和验收标准。
2. 审阅并确认当前草案。确认前不会启动工作流子代理。
3. Codex 原生子代理执行任务，内联看板自动更新；可按需展开到侧栏。
4. 子代理返回后进入**待验收**，主代理检查结果后才标记完成。
5. 任务失败或评审不通过时暂停新派发。已运行的子代理可以返回，结果保留待验收，由你决定重试或取消。

插件由执行技能、本地 stdio MCP 服务、SQLite 状态库和自包含 HTML 看板组成，无需单独的模型 API Key 或第三方 Python 包。

## 本地安装

要求 Python 3.9+、Codex CLI，以及支持本地 MCP 插件和原生子代理的 Codex 桌面环境。开发与本地安装已在 macOS 测试；Linux 宿主接入尚未验证。

```bash
git clone https://github.com/zj-linjie/J-codex-workflow.git
cd J-codex-workflow
python3 scripts/package.py
codex plugin marketplace add "$PWD/dist/marketplace"
codex plugin add codex-workflow@codex-workflow-local
```

安装后重启 Codex，或使用已加载新工具的会话。显示名称为 **J-codex-workflow**，兼容插件 ID 仍为 `codex-workflow`。

生成的安装包包含本机启动路径，请在使用的机器上构建；移动项目后重新构建。生成包、本地数据库和凭据均不提交到 Git。

## 使用

在已加载插件的会话中说：

> 用 J-codex-workflow 帮我调研这个方案。先给出角色、阶段和验收要求，等我确认再执行。

在会话中修改草案并确认。执行时可以要求暂停、重试指定失败任务或取消。普通暂停保留在途任务；取消时由主代理实际中断活跃的原生子代理。

通过 `workflow_list` 找到历史运行，再使用 `workflow_board` 和对应运行 ID 重开看板。看板只读，控制操作通过会话完成。

## 数据与边界

- 每张卡片绑定明确的运行 ID，写入使用版本号和幂等键。
- SQLite 保存计划、简述、产物引用和事件，不读取会话数据库或产物正文。
- 数据默认位于 `~/.local/share/codex-workflow/`，可用 `CODEX_WORKFLOW_DATA_DIR` 修改。
- 状态由主代理显式上报；插件不会独立订阅全部原生代理生命周期或认证记录中的代理 ID。
- 服务重启后可读取保存记录；主代理需要核对原生代理状态后再恢复执行。
- 确认工作流不会扩展文件系统、发布、部署或外发消息权限。

## 开发

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/dev_host.py
```

第二条命令启动带明确标识的开发样例，用于验证布局、自动更新和断连行为，不能代替 Codex 宿主验收。

| 路径 | 用途 |
| --- | --- |
| `plugin/skills/` | 规划、确认、原生派发和验收技能 |
| `plugin/mcp/` | 事务状态库和 MCP 工具 |
| `plugin/ui/board.html` | 内联工作流看板 |
| `examples/` | 调研评审、实现复核计划示例 |
| `scripts/package.py` | 构建本地市场和安装包 |

详见 [PRD](PRD.md)、[接口契约](docs/CONTRACT.md)和[验收记录](docs/ACCEPTANCE.md)。

## 许可证

[MIT](LICENSE) © 2026 zj-linjie。
