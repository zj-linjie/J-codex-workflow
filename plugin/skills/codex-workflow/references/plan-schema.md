# Workflow plan schema

Use this reference when creating or revising the `plan` passed to `workflow_create` or `workflow_update_draft`. `docs/CONTRACT.md` is authoritative for the full MCP arguments, limits, and state transitions.

## Shape

```json
{
  "title": "Short run title",
  "goal": "Observable outcome",
  "roles": [
    {"id": "researcher", "name": "Researcher", "kind": "native", "agentType": "WorkflowExpertAdvisor", "instructions": "Task-specific brief"},
    {"id": "lead", "name": "Main agent", "kind": "primary", "instructions": "Synthesize and accept the result"}
  ],
  "phases": [
    {"id": "research", "name": "Research"},
    {"id": "delivery", "name": "Synthesis and delivery"}
  ],
  "tasks": [
    {"id": "collect", "name": "Collect evidence", "roleId": "researcher", "phaseId": "research", "dependsOn": [], "acceptanceCriteria": "Claims have traceable evidence"},
    {"id": "synthesize", "name": "Synthesize findings", "roleId": "lead", "phaseId": "delivery", "dependsOn": ["collect"], "acceptanceCriteria": "The result answers the stated goal and records limitations"}
  ]
}
```

## Validation rules

- `title`, `goal`, names, instructions, and acceptance criteria are non-empty strings within the limits enforced by the contract.
- Keep role, phase, and task IDs unique. Every task references an existing role and phase; every phase contains at least one task.
- Use only `native` and `primary` role kinds. Native roles are dispatched by the main agent through Codex collaboration; primary roles are performed by the main agent.
- For newly drafted native roles, specify `agentType` as the loaded native profile name (up to 64 identifier characters). Primary roles must omit it. The API accepts old plans without it for compatibility; the skill updates old drafts before new dispatch. A bound task's running event must report the matching agentType. Later status events omit it.
- Keep dependencies acyclic. A task may depend only on tasks in its phase or an earlier phase. A later phase becomes runnable only after all earlier-phase tasks are accepted as done.
- Contract limits: at most 32 roles, 12 phases, and 64 tasks. See `docs/CONTRACT.md` for the exact field-level length and validation requirements.
- A simple task can be a single primary role performed by the main agent. At draft time, include an independent review when the user requests one or task complexity or material risk warrants it. Keep the requested review and its observable acceptance criteria in the confirmed plan.
- Acceptance criteria must be observable from the returned artifact or work. Make task instructions bounded to their approved responsibility and name expected artifacts.

The JSON files in `examples/` are complete plan objects suitable for exercising draft creation and state transitions.
