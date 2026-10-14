# Workflow recovery

Read this when a task fails or a review is rejected, the user pauses/cancels/resumes/retries, or native state is uncertain. Use the same explicit `runId`; use a full `workflow_get` when recovery needs plan, history, or long results.

## Failure and pause

- A failed task or rejected review pauses the scheduler. Stop new dispatches and leave other active children running. Record each actual return as `submitted`, but do not accept it, mark it `done`, or dispatch more work while paused. Ask whether the user wants to retry the failed task, resume after reviewing the paused state, or cancel. Never retry automatically.
- For a user-requested pause, stop dispatch and leave active children running by default. Record their actual returns as `submitted`; wait for an explicit resume decision before accepting results or dispatching. Interrupt a child only when the user explicitly asks to stop it.
- To cancel, interrupt every actually active child, report successful interruptions and unresolved identities, then persist cancellation with `workflow_control`. A board view or status write does not interrupt a child.

## Resume and retry

Before every resume, read the same run and reconcile every stored `nativeId` with native collaboration state. Keep active children attached to their existing task; never replace them. Record actual returns as `submitted`. If a child is confirmed stopped without a result, record the observed failure and ask the user what to do. Resume does not revive an interrupted child or retry a failed task.

Retry only after the user explicitly chooses that failed task and `workflow_control` accepts the retry. After an authorized resume or retry, accept submitted results only after reading their artifacts and checking the confirmed criteria. Any task still failed remains unresolved and cannot be treated as done. For write conflicts, uncertain results, or external changes, follow the compact-state reconciliation rule in the main skill before acting; read full state when details or history are needed.
