---
name: codex-workflow
description: Use when the user explicitly asks to run or manage a J-codex-workflow run. Turn the request into a confirmed plan, coordinate native Codex subagents, and verify their returned work.
---

# J-codex-workflow

Use only when the user explicitly requests this workflow. MCP arguments and state transitions are defined in the plugin's `docs/CONTRACT.md`. Read [plan-schema.md](references/plan-schema.md) when drafting or editing a plan. Read [agent-profiles.md](references/agent-profiles.md) when choosing/installing profiles or when a confirmed legacy role has no `agentType`. For failed/rejected tasks or pause, cancel, resume, retry, or uncertain native state, read [recovery.md](references/recovery.md). When the same unresolved problem reaches the configured setback threshold (default: two), read [escalation.md](references/escalation.md). Read [session-usage.md](references/session-usage.md) when explaining usage coverage.

## Draft and confirm

Turn the goal into a small plan with observable acceptance criteria. See [plan-schema.md](references/plan-schema.md) for choosing primary-only work or an independent review. Preserve requested review and acceptance criteria in the confirmed plan. A confirmed run is immutable: never edit it or add tasks. For a scope change, present a separate draft for explicit confirmation; do not automatically create or start a replacement run.

Use `kind: "primary"` for work the main agent performs, including synthesis and acceptance. Use `kind: "native"` for delegated work. Every newly drafted native role needs an explicit `agentType` available in the current spawn interface; a TOML template alone does not prove it is loaded. Resolve missing roles before confirmation.

Before `workflow_create`, read the current main chat's actual `CODEX_THREAD_ID` from the execution environment and pass it as `sessionId`. If unavailable, omit it and tell the user session statistics will be unavailable. Never guess the ID or use the MCP process's inherited identity. The binding stays fixed when editing a draft.

For normal workflow calls, explicitly request `view: "compact"`; see `docs/CONTRACT.md` for which calls support it and the returned fields. After a successful write, reuse its state and revision instead of issuing an unconditional get. The board UI uses the run ID and fetches its default full view when it needs the plan. Get compact state after a conflict, uncertain write, or external change; reconcile it with actual native state when relevant and preserve newer state. Get full state only when plan/evidence/context is missing or recovery needs history/details. Never blindly repeat a spawn.

Create a draft, then show it with `workflow_board`; explain phases, roles, task order, and acceptance criteria in ordinary language. Keep it in `draft` and do not spawn before the user confirms that exact plan version. If changed, persist with `workflow_update_draft`, show the new version, and ask for confirmation again. On explicit confirmation, call `workflow_confirm` for the current revision and summarize the user's actual confirmation. Silence or a request to inspect the board is not confirmation.

## Dispatch and acceptance

Use the explicit `runId` on every operation. Treat each write other than create as compare-and-swap: pass the latest `expectedRevision` and a fresh unique `eventId`; reuse an event ID only to replay the identical operation. For conflict, uncertainty, or external change, follow the compact-state rule above before acting.

Schedule only `readyTaskIds` whose dependencies and earlier phases are accepted, and respect the host's native collaboration slots. Only the current top-level main agent dispatches native tasks, using its native Codex collaboration tool, `agent_type` from the confirmed role, and `fork_turns="none"`. Do not create another chat/task, invoke an appserver or external agent API, or let a child spawn children. Give each child a bounded brief: goal, acceptance criteria, read/write scope, known findings and sources/files, failed attempts, and remaining unknowns; mark unknowns as unknown. Do not send the full chat.

Write `running` with the real returned `nativeId` and actual requested `agentType` only after spawn succeeds. Without a real identity, leave the task undispatched and report the failure. Primary tasks stay with the main agent; never invent a native ID for them. Status/progress writes never perform control actions or spawn, stop, or resume an agent.

When a native child returns, require a bounded report of findings, supporting evidence/artifact references, and remaining unknowns; mark unknowns as unknown. Write `submitted` with that concise result. The main agent reads the actual artifacts and checks every confirmed acceptance criterion before writing `done` with a concrete acceptance explanation. Independently verify when evidence is missing or stale, files changed, new changes arrived, or material risk warrants it; avoid repeating unchanged whole-repository checks without such a reason. A return, progress update, or green card is not acceptance.

## Recovery and completion

Any failed task or rejected review pauses dispatch. When a task fails or is rejected, the user pauses/cancels/resumes/retries, or native state is uncertain, follow [recovery.md](references/recovery.md) before acting. Preserve native identities and require the specified user recovery choice; never accept pending work or retry automatically.

A resume/retry request authorizes only the requested workflow work, not publishing, deployment, external messages, or other outward actions. Statistics are separate from acceptance. Completion requires every task `done` with an acceptance explanation and run status `completed`; keep paused, cancelled, and unresolved native states explicit. On completion, the board calls `workflow_usage` locally once to save a session-tree usage/speed snapshot. If the board is closed, the main agent may call it once. Statistics cannot change acceptance or turn completed work into failure.

Use only the `workflow_*` tools listed in `docs/CONTRACT.md`. If they are missing, do not imitate successful calls with a shell command, API, or fabricated status. Tell the user the plugin tools are not loaded and Codex may need to restart or reload the plugin; continue only when available.
