# J-codex-workflow

[English](README.md) · [简体中文](README.zh-CN.md)

Define roles and phases in a Codex conversation, approve the plan, and follow native subagents on a live inline workflow board.

**Early preview · v0.3.0.** All 47 local tests pass. Full workflow validation of the packaged plugin inside the Codex desktop host is still pending. An earlier native MCP probe confirmed that an inline component can update during a real subagent task.

## How it works

1. Describe your goal. The main agent drafts roles, phases, dependencies, and acceptance criteria.
2. Review and confirm the current draft. Workflow subagents start only after confirmation.
3. Native Codex subagents execute the tasks. The inline board updates automatically; side panel expansion is optional.
4. Returned work enters **awaiting acceptance**. The main agent checks results before marking them complete.
5. Failure or rejected review pauses new dispatch. Already running subagents can return, with results retained for acceptance. You decide whether to retry or cancel.

The plugin combines a reusable skill, a local stdio MCP server, an SQLite state store, and a self-contained HTML board. No separate model API key or third-party Python packages are required.

## Install locally

Requirements: Python 3.9+, the Codex CLI, and a Codex desktop environment supporting local MCP plugins and native subagents. Development and local installation have been tested on macOS; Linux host integration has not been verified.

```bash
git clone https://github.com/zj-linjie/J-codex-workflow.git
cd J-codex-workflow
python3 scripts/package.py
codex plugin marketplace add "$PWD/dist/marketplace"
codex plugin add codex-workflow@codex-workflow-local
```

Restart Codex, or use a conversation that has loaded the new tools. The display name is **J-codex-workflow**; the compatibility plugin ID remains `codex-workflow`.

Generated packages contain local launch paths. Build on the machine where you will use the plugin, and rebuild after moving the project. Generated packages, databases, and credentials are excluded from Git.

## Native workflow profiles

Install the four bundled definitions explicitly before using bound roles:

```bash
python3 plugin/scripts/install_agents.py --dry-run
python3 plugin/scripts/install_agents.py
```

The installer adds WorkflowExplorer, WorkflowExecutor, WorkflowReviewer, and WorkflowExpertAdvisor alongside the original profiles. It preserves their configuration, refuses conflicting files, and leaves identical files unchanged. Use `--destination /path/to/project/.codex/agents` for project scope. Restart/reload Codex and verify the intended types are loaded. This PR has not installed them or run native-host acceptance.

A native role's `agentType` is passed to `spawn_agent` and reported with its running event. Old unbound plans remain readable. See [profile setup](plugin/skills/codex-workflow/references/agent-profiles.md).

The separate [orchestrator setting](plugin/settings/orchestrator.json) lets the main agent consider one Astra consultation after two setbacks on the same problem. It is enabled by default; set `enabled` to false to disable. This does not switch the parent model or automatically retry a paused task. The [policy reference](plugin/skills/codex-workflow/references/escalation.md) explains counters, quotas, and overrides. Role and consultation execution in this version remain unverified in the live host.

## Completion session statistics

After a workflow completes, the board collects one local snapshot of the **current main session and its subagents**, including earlier activity in the same chat. It shows input/cache/output/reasoning tokens, model and session breakdowns, and response-average output speed with sample counts and percentiles. Cached input and reasoning output are already included in their parent totals.

The orchestrator binds the actual current `CODEX_THREAD_ID` as `sessionId` when creating a run. The board calls `workflow_usage` directly over MCP, then reuses the stored snapshot; it does not ask the model to poll for statistics. Missing logs or an unbound legacy run show partial/unavailable coverage. Speed is not a streaming rate, and the current response may not yet be flushed to disk. Reports from runs in the same chat overlap. No billing estimates or raw conversation content are stored. See [statistics scope and algorithm](plugin/skills/codex-workflow/references/session-usage.md).

## Use

In a conversation with the plugin loaded:

> Use J-codex-workflow to research this approach. Draft the roles, phases, and acceptance criteria, and wait for my confirmation before executing.

Revise and confirm the draft in the conversation. You can ask to pause, retry a named failed task, or cancel. Ordinary pauses retain in-flight work; cancellation requires the main agent to interrupt active native subagents.

Use `workflow_list` to find a previous run and `workflow_board` with its run ID to reopen the board. The board is read-only; control actions happen through the conversation.

## Data and boundaries

- Each board binds to an explicit run ID. Writes use revisions and idempotency keys.
- SQLite persists plans, summaries, artifact references, events, and numeric usage snapshots. Completion statistics read local session logs, without reading conversation databases or artifact contents.
- Data defaults to `~/.local/share/codex-workflow/`; override it with `CODEX_WORKFLOW_DATA_DIR`.
- The main agent explicitly reports status. There is no independent subscription to all native agent lifecycle events or authentication of recorded agent IDs.
- Restarting the service restores saved records. The main agent reconciles native agent state before resuming.
- Plan confirmation does not grant extra filesystem, publishing, deployment, or messaging permissions.

## Development

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/dev_host.py
```

The second command starts a clearly labeled development fixture for layout, automatic updates, and disconnect behavior. It is not Codex host validation.

| Path | Purpose |
| --- | --- |
| `plugin/skills/` | Planning, confirmation, native dispatch, and acceptance |
| `plugin/mcp/` | Transactional state store and MCP tools |
| `plugin/ui/board.html` | Inline workflow board |
| `examples/` | Research/review and implementation/review plans |
| `scripts/package.py` | Local marketplace and package build |

See the [PRD](PRD.md), [interface contract](docs/CONTRACT.md), and [acceptance record](docs/ACCEPTANCE.md).

## License

[MIT](LICENSE) © 2026 zj-linjie.
