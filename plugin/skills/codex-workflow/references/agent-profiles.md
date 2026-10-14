# Native workflow profiles

The four templates in the plugin's `agent-profiles/` copy the existing Team Mode profiles, changing only `name` to add the Workflow prefix. Model settings and developer instructions are preserved.

| agentType | Based on | Model | Effort | Assignment |
| --- | --- | --- | --- | --- |
| WorkflowExplorer | Explorer | gpt-6-luna | medium | Locate files and entry points in a large codebase |
| WorkflowExecutor | Executor | gpt-6-luna | xhigh | Implement a bounded result within clear write ownership |
| WorkflowReviewer | Reviewer | gpt-6.1-sol | high | Independently review stable work without modifying it |
| WorkflowExpertAdvisor | ExpertAdvisor | Chosen per consultation | Chosen per consultation | Investigate complex decisions or unresolved problems |

Business labels such as “接口实现员” are independent of agentType. Multiple business roles can bind the same profile with different task briefs. Fixed profile model/effort settings take precedence over spawn overrides; the ExpertAdvisor template deliberately leaves both unset.

## Installation

From the repository root, run `python3 plugin/scripts/install_agents.py --dry-run` to inspect, then the same command without `--dry-run` to install into `~/.codex/agents/`. For project scope, pass `--destination /path/to/project/.codex/agents`. In an installed package, use its `scripts/install_agents.py`.

The helper checks all four destinations before writing, leaves identical definitions unchanged, and refuses conflicting files or symlinks. It does not overwrite the four original Team Mode roles. Packaging alone does not register roles; restart or reload Codex and check the actual spawn interface before dispatch.

Templates retain the original sandbox/tier fields for compatibility. Current official documentation says these fields do not override parent settings; a read-only instruction is a behavioral constraint, not an independent sandbox. See [custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents#custom-agents).

## Binding and execution evidence

For a new native role, persist `agentType` in the draft and confirm that draft before dispatch. Supply the same value to spawn `agent_type` and the task's running event, together with the returned nativeId. The server rejects a missing or mismatched type for bound roles. It validates reported values, not the host's actual agent identity or effective model.

Old plans remain readable without agentType. Update an old draft before confirming new work. In a confirmed legacy plan, choose an explicit available profile matching the approved responsibility for an undispatched or user-authorized retry task; record the choice in notes and report its execution type without editing the role. If no profile fits, pause for a revised plan. Retain existing identities when reconciling an already running task. A retry clears the execution type and nativeId, while retaining any role binding.
