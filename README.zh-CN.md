# J-codex-workflow

[English](README.md) · [简体中文](README.zh-CN.md)

**先确认计划，再跟进原生执行，最后验收结果。**

一个本地 Codex 插件：将会话中的目标变成经过确认的工作流，由原生子代理执行，通过动态内联看板查看进度。

<p align="center">
  <img src="assets/readme/workflow-hero.png" width="100%" alt="J-codex-workflow：计划通过用户确认关口后分发给原生子代理，返回结果汇合到主代理验收关口。">
</p>

*封面由 ImageGen 生成，用于表达工作流机制，并非产品截图。*

[快速开始](#快速开始) · [工作方式](#工作方式) · [原生验收证据](docs/NATIVE_ACCEPTANCE.md) · [接口契约](docs/CONTRACT.md)

> **早期预览版 · v0.3.0**
>
> 本地 52 项测试全部通过。v0.1.1 正式插件已在 Codex 桌面宿主完成“用户确认 → 1 个原生子代理执行返回 → 主代理验收 → 动态看板完成”，并取得独立 MCP Apps DOM 与截图证据。[查看验收范围与证据 →](docs/NATIVE_ACCEPTANCE.md)
>
> 新增角色、咨询策略与完成统计尚未做真实宿主验收，Linux 宿主接入也尚未验证。

## 工作方式

1. **起草** — 用自然语言描述目标，主代理定义角色、阶段、依赖和可核对的验收标准。
2. **确认** — 在会话中审阅当前草案，明确确认该版本后才启动工作流子代理。
3. **执行** — Codex 原生子代理完成任务，内联看板自动更新；可按需展开到侧栏。
4. **验收** — 返回结果先进入**待验收**，主代理检查实际产物后才标记完成。
5. **恢复** — 任务失败或评审不通过时暂停新派发。在途任务可以返回，结果保留待验收，由你决定重试或取消。

看板只读，修改计划和控制执行都通过会话完成。子代理返回结果，不代表结果已通过验收。

## 快速开始

**环境要求：** Python 3.9+、Codex CLI，以及支持本地 MCP 插件和原生子代理的 Codex 桌面环境。开发与本地安装已在 macOS 测试。

### 1. 本地构建与安装

```bash
git clone https://github.com/zj-linjie/J-codex-workflow.git
cd J-codex-workflow
python3 scripts/package.py
codex plugin marketplace add "$PWD/dist/marketplace"
codex plugin add codex-workflow@codex-workflow-local
```

安装后重启 Codex，或打开已加载新工具的会话。显示名称为 **J-codex-workflow**，兼容插件 ID 仍为 `codex-workflow`。

安装包包含本机启动路径，请在使用的机器上构建；移动项目后重新构建。生成包、本地数据库和凭据均不提交到 Git。

### 2. 加载原生工作流角色

使用绑定到内置角色的任务前，先预览，再执行安装助手：

```bash
python3 plugin/scripts/install_agents.py --dry-run
python3 plugin/scripts/install_agents.py
```

重启或重新加载 Codex，确认目标类型可用。助手在原角色之外新增四个 `Workflow*` 角色，保留原配置，相同文件不重复写入，冲突文件拒绝覆盖。限定项目范围时添加 `--destination /path/to/project/.codex/agents`。[角色配置说明 →](plugin/skills/codex-workflow/references/agent-profiles.md)

### 3. 发起第一份计划

在已加载插件的会话中说：

> 用 J-codex-workflow 帮我调研这个方案。先给出角色、阶段和验收要求，等我确认再执行。

查看看板，在会话中提出修改，然后确认当前草案。执行时可以要求暂停、重试指定失败任务或取消。普通暂停保留在途任务；取消时由主代理实际中断活跃的原生子代理。

回看历史运行时，让主代理调用 `workflow_list`，再用对应运行 ID 调用 `workflow_board`。

## 角色与完成统计

| 原生角色 | 职责 |
| --- | --- |
| `WorkflowExplorer` | 定位文件与入口 |
| `WorkflowExecutor` | 实现职责明确的任务 |
| `WorkflowReviewer` | 独立评审稳定产物 |
| `WorkflowExpertAdvisor` | 调查复杂决策与疑难问题 |

原生角色通过 `agentType` 绑定已加载的类型，主代理向 `spawn_agent` 和 running 事件传入相同类型。旧的无绑定计划仍可读取。这些新增角色仍待真实宿主验收。

<details>
<summary><strong>反复受挫后的咨询策略</strong></summary>

独立的[主代理设置](plugin/settings/orchestrator.json)默认允许主代理在同一问题受挫两次后考虑一次 Astra 咨询；设 `enabled` 为 `false` 即可关闭。它不改变父模型，也不自动重试暂停任务。该策略的真实宿主执行仍未验证。[计数、限额与覆盖方式 →](plugin/skills/codex-workflow/references/escalation.md)

</details>

<details>
<summary><strong>完成统计的范围与口径</strong></summary>

完成后，看板本地采集一次**当前主会话及其子代理累计**快照，包含同一会话在本工作流之前的活动。展示输入、缓存输入、输出、推理输出 token、模型与会话明细，以及响应平均速率、分位数和样本数。缓存输入已包含在输入中，推理输出已包含在输出中。

主代理创建运行时将实际 `CODEX_THREAD_ID` 作为 `sessionId` 绑定。看板直接通过 MCP 调用 `workflow_usage`，随后复用保存的快照，无需模型轮询。日志缺失或旧运行未绑定会话时显示部分可用或不可用；当前轮尚未落盘的记录可能不计入。速率不代表流式生成速度，同一会话的多个运行统计范围会重叠。不估算账单、不保存会话正文。本功能仍待真实宿主验收。[统计范围与算法 →](plugin/skills/codex-workflow/references/session-usage.md)

</details>

## 本地运行与边界

插件由执行技能、本地 stdio MCP 服务、SQLite 状态库和自包含 HTML 看板组成，**无需单独的模型 API Key 或第三方 Python 包。**

- **明确状态：** 每张看板绑定运行 ID，写入使用版本号和幂等键。
- **本地记录：** SQLite 保存计划、简述、产物引用、事件和数值统计快照。默认位于 `~/.local/share/codex-workflow/`，可用 `CODEX_WORKFLOW_DATA_DIR` 修改。
- **统计范围：** 完成统计只读取本地会话日志，不读取会话数据库或产物正文。
- **进度上报：** 主代理显式记录状态；插件不会独立订阅全部原生代理生命周期或认证记录中的代理 ID。
- **恢复执行：** 服务重启后可读取保存记录，主代理核对实际原生状态后再恢复。
- **权限范围：** 确认计划不会扩展文件系统、发布、部署或外发消息权限。

## 开发

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/dev_host.py
```

第二条命令启动带标识的开发样例，用于验证布局、自动更新和断连行为，不能代替 Codex 宿主验收。状态测试使用临时 SQLite 数据库或 `CODEX_WORKFLOW_DATA_DIR`，避免触及默认用户数据库。

| 路径 | 用途 |
| --- | --- |
| `plugin/skills/` | 规划、确认、派发、验收技能 |
| `plugin/mcp/` | 状态库与 MCP 工具 |
| `plugin/ui/board.html` | 内联工作流看板 |
| `examples/` | 调研评审、实现复核计划示例 |
| `scripts/package.py` | 本地市场与安装包构建 |

[产品规格](PRD.md) · [接口契约](docs/CONTRACT.md) · [本地验收](docs/ACCEPTANCE.md) · [原生宿主验收](docs/NATIVE_ACCEPTANCE.md)

## 许可证

[MIT](LICENSE) © 2026 zj-linjie。
