# Main-agent Astra consultation policy

This policy belongs to the main agent, outside the workflow role table. Read the packaged `settings/orchestrator.json`, or a user-specified settings file. The default allows consideration after two observed setbacks on the same unresolved problem and caps consultation at one per incident. `enabled=false` disables this path.

Use the packaged helper to validate settings and evaluate the counters:

```bash
python3 plugin/scripts/escalation_policy.py --failures 2 --consultations-used 0
```

From an installed package, use `scripts/escalation_policy.py`. Pass `--settings PATH` for an explicit override. The helper only prints eligibility and proposed spawn arguments; it does not invoke models, spawn agents, or persist counters.

An incident is one unresolved problem across attempts, not an entire run or a single tool call. Track its key, evidence of setbacks, consultation count, and outcome in run notes and handoffs. Reconcile these notes after compaction/restart; unknown counts are not a fresh zero allowance. A user-authorized task retry does not reset the incident quota.

When eligible, the main agent decides whether another bounded diagnosis would help. If so, use the available, model-free WorkflowExpertAdvisor profile with `fork_turns="none"`, the configured `model` (gpt-6-astra), and `reasoning_effort`. Include the goal, evidence, prior attempts, and a clear question. Count a consultation when dispatch succeeds. Each new consulting turn counts again; do not use follow-up turns on the same child to bypass the quota.

This is a one-off child consultation, not a change to the parent model or fixed role definitions. Keep Astra out of the role table. Do not override a fixed WorkflowExplorer/Executor/Reviewer model to request it. If the intended profile/model is unavailable, report that limitation instead of silently substituting a different role or model.

Preserve the existing failure rule: a failed workflow stays paused; diagnosis alone does not retry the failed task, accept pending results, or resume dispatch. Consult only within the approved scope. Return the advice to the user and wait for their recovery decision before further execution.
