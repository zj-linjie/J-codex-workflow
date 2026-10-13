# J-codex-workflow skill validation

This record covers the plugin skill and the two plan examples. The source contract is `docs/CONTRACT.md`; the user-facing workflow is governed by `plugin/skills/codex-workflow/SKILL.md`.

## Checks

- Ran the bundled `skill-creator/scripts/quick_validate.py` against `plugin/skills/codex-workflow/` after authoring.
- Checked both example files as strict JSON and reviewed their role/phase/task references against the contract: all IDs are unique, every phase has a task, every reference resolves, and dependencies point only to the same or an earlier phase.
- Reviewed the instructions against the requested execution boundary: draft confirmation gates spawning; only the current main agent dispatches native collaboration children; native IDs come from successful spawn results; children return as `submitted`; the main agent reads and accepts artifacts before `done`; failure/rejected review pauses new dispatch while other active children may return as `submitted`; acceptance and further dispatch wait for an explicit recovery decision; manual pause preserves active children by default; cancellation interrupts active children; all writes use fresh event IDs and the latest revision.

## Operational limits

- The skill cannot verify that the host actually loaded the plugin MCP tools or that a native child spawned, returned, or was interrupted. It requires real tool results and surfaces unavailable or uncertain states to the user.
- The skill does not add a native lifecycle subscription. The main agent must reconcile native child state when resuming and must perform explicitly requested interruption through host collaboration controls. A stored `running` state alone does not prove a child remains active.
- Plan examples validate the plan shape and can exercise state transitions when passed to the implementation. They do not prove the MCP server, SQLite store, board rendering, or a real native-agent round trip; those checks belong to the plugin implementation test plan.
