# Completion session statistics

Bind the current main chat's actual CODEX_THREAD_ID at creation. `sessionId` is a `workflow_create` argument, outside the role/phase plan. It is immutable and optional for older callers; an unbound run reports unavailable statistics. Never infer the chat identity from a long-lived MCP server process or a recently modified log.

After completion, the board calls `workflow_usage({runId})` directly through MCP. The first call reads local session metadata to select the bound root and descendants, aggregates their retained token events, and saves a numeric snapshot. Later reads use that snapshot, including after reopening the board. The main agent can make the same call if the board is closed. Neither path starts a model request. Failed transport reads can be retried; a saved partial or unavailable report remains a fixed snapshot.

The scope is the **whole current main session and its subagents**, including activity before this workflow. It is not a workflow-only delta, an account-wide usage limit, or a billing statement. All runs bound to the same chat may overlap, so do not sum their reports as independent costs. No credit or currency estimates are supplied.

The collector adapts Team Mode's `usage_by_model.py` approach: select the tree using session_meta parent links, deduplicate active/archive copies, group last_token_usage by the observed turn_context model, and skip repeated last/total snapshots only with cumulative evidence. Cached input is included in input, and reasoning output is included in output. Total processed tokens = input + output.

Speed pools per-response samples: output tokens / (time since the previous token event minus the union of observed tool execution spans). Windows reset at turn and model/effort boundaries; only raw intervals over 0.2 and at most 120 seconds with positive generating time/output contribute. The panel shows mean, P10/P90, and sample count by model, plus main/subagent details. This includes queue/prefill time and reasoning output; it is not streaming token rate, TTFT, or inter-token latency. Missing samples are null, not zero.

Snapshots cover records retained and flushed at collection time. The final main-agent response or other in-flight children may not yet have token events on disk. Missing root/child records, malformed events, or unreadable traces produce partial/unavailable coverage; a missing report is never represented as confirmed zero usage. Logs can omit deleted or unretained sessions, even when all discovered files parse successfully.

Default sources are `$CODEX_HOME/sessions` and `$CODEX_HOME/archived_sessions` (otherwise `~/.codex/`). The collector only parses local files and returns numerical summaries, model/role labels, session identifiers, and coverage indicators. Raw conversation content, tool arguments, and log paths are not stored in the workflow database or returned to the board. Tests pass explicit temporary roots; the MCP tool accepts only a runId, not arbitrary file paths or session queries.

Snapshot persistence does not change run status, workflow revision, acceptance evidence, or dispatch permissions. A collection failure leaves completed work completed.
