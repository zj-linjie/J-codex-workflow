---
name: codex-workflow
description: Use when the user explicitly asks to run or manage a J-codex-workflow run. Turn the request into a role-and-phase plan, wait for confirmation of the current draft, then coordinate native Codex subagents and verify their returned work.
---

# J-codex-workflow

Use this skill when the user names J-codex-workflow or explicitly asks to use a workflow. Read [plan-schema.md](references/plan-schema.md) when creating or changing a plan. The MCP tool names and argument contract are defined in the plugin's `docs/CONTRACT.md`.

## Draft and confirm

Translate the user's goal into roles, ordered phases, tasks, dependencies, expected artifacts, and checkable acceptance criteria. Assign `kind: "native"` to delegated roles and `kind: "primary"` to work the current main agent will do, including synthesis and final acceptance. Keep the plan as small as the request allows while covering its real dependencies.

Create a draft with `workflow_create`, then show it with `workflow_board`. Explain the phases, role responsibilities, task order, and acceptance criteria in ordinary language. The run must remain `draft`; do not spawn any workflow subagent before the user confirms that exact plan version. If the user changes the draft, persist it with `workflow_update_draft`, show the new version, and request confirmation again. A prior confirmation never carries over to an edited plan.

On explicit confirmation, call `workflow_confirm` for the current `revision`, with a short summary of the user's actual confirmation. Do not infer confirmation from silence or from an unrelated request to inspect the board.

## Dispatch and acceptance

Use the explicit `runId` on every operation. Read `workflow_get` after state changes and schedule only tasks whose dependencies are accepted and whose earlier phases are complete. Respect the native collaboration slots available in the current host.

The current top-level main agent is the only dispatcher: use its native Codex collaboration subagent tool for each `native` task. Do not create another chat or task, invoke an appserver, or call an external agent API. A child does not spawn children. Record `running` with the real `nativeId` only after the native spawn call succeeds. If spawn does not return a real identity, leave the task undispatched and report the tool failure.

Run `primary` tasks in the main agent and identify them as primary work; never invent a native ID for them. A status write only records workflow state. It does not spawn, stop, or resume an agent.

When a native child actually returns, record `submitted` with a concise result summary and artifact references, if any. Then read the referenced work yourself and check every acceptance criterion. Record `done` only with a concrete acceptance explanation. A returned child, a progress update, or a green-looking card is not acceptance.

## Pause, failure, and recovery

Any failed task or rejected review pauses the scheduler. Stop new dispatches and leave other active children running so they can return. Record each real return as `submitted` while paused, but do not accept it, mark it `done`, or dispatch further work until the user explicitly chooses a recovery action. Ask whether to retry the failed task, resume after reviewing the paused state, or cancel. Never retry a task or rejected review automatically.

For a user-requested pause, stop new dispatches and leave active children running by default; record their real returns as `submitted` and wait for an explicit resume decision before acceptance or further dispatch. Interrupt a child only when the user explicitly asks to stop it. For cancellation, interrupt every child that is actually active, report which interruptions succeeded and any unresolved identities, then persist cancellation with `workflow_control`. A board or status write never interrupts a child.

Before any resume, read the same `runId` and reconcile every stored `nativeId` against the native collaboration state. Keep a child that is still active attached to its existing task; never spawn a replacement for it. Record a real return as `submitted`. If a child is confirmed stopped without a result, record the observed failure and ask the user how to proceed. A resumed scheduler does not revive an interrupted child or retry a failed task. Retry only after the user explicitly chooses that failed task and `workflow_control` accepts the retry. After a user-authorized resume or retry, accept submitted results only by reading their artifacts and checking the criteria; any task left failed remains unresolved and cannot be treated as done.

Treat each write as a compare-and-swap: pass the latest `expectedRevision` and a fresh unique `eventId` to every write other than create. Reuse an event ID only to replay the identical operation. If a write conflicts or its result is uncertain, read the same run and reconcile before taking another action; never blindly repeat a spawn or overwrite newer state. Do not write progress as though it were a control action.

A request to resume or retry authorizes only the requested workflow work. It does not authorize publishing, deployment, external messages, or other outward actions that the user did not request.

## Tool availability

Use only the `workflow_*` tools listed in `docs/CONTRACT.md`. If those tools are missing from the current session, do not imitate a successful tool call with a shell command, API, or fabricated status. Tell the user the plugin tools are not loaded and that Codex may need to restart or reload the plugin, then continue only after they are available.

The run is complete only when every task is `done` with an acceptance explanation and the run reports `completed`. Keep `paused`, `cancelled`, and unresolved native-agent states explicit in the handoff.
