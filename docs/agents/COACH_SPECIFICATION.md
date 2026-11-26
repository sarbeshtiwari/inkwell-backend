# Coach specification

**Status:** PROPOSED (agent set approved in direction; this specification is not approved) · **Phase:** 3 ·
**Implemented:** No · **Last reviewed:** 2026-11-26

Shared rules for every agent: [README.md](README.md#rules-shared-by-every-agent).

## 1. Purpose
Help the user prepare for interviews and keep learning, using the existing course, review and prep features.

## 2. Responsibilities
- Suggest what to study next.
- Prepare for specific interviews, using the existing `make_prep`.
- Summarise progress.

## 3. Must never
- Invent facts about the user's experience.
- Send anything outside the app.

## 4. Inputs
Course state, upcoming interviews (tracker), job descriptions, and `ToolContext`.

## 5. Outputs
Prep material, study suggestions, and notifications.

## 6. Available tools
`extras.make_prep`, the `learning/` course and `briefing`, `tracker`, `notify`.

## 7. Tool permissions
`read` / `write_internal`.

## 8. Model requirements
- Any active `model_configs` version assigned to this agent, through the gateway.
- Structured JSON output validated against the agent's output schema.
- The provider and model are TO BE DEFINED. The proposal suggests cheaper models for high-volume steps.

## 9. Memory requirements
Learning progress, which exists in `kv`.

## 10. Planning and execution lifecycle
TO BE DEFINED (Phase 3).

## 11. Delegation
None planned.

## 12. Human approval
None.

## 13. Errors and retries
TO BE DEFINED.

## 14. Execution limits
TO BE DEFINED.

## 15. Cost controls
- Runs under the per-agent run budget and the trace ceiling it inherits.
- The gateway refuses calls past the user, global or task limits ([../operations/COST_AND_USAGE_GOVERNANCE.md](../operations/COST_AND_USAGE_GOVERNANCE.md)).
- Amounts are TO BE DEFINED.

## 16. Observability
Spans for every model call, tool call, delegation, retry and error, plus the final output, under the task's `trace_id` ([../architecture/OBSERVABILITY_ARCHITECTURE.md](../architecture/OBSERVABILITY_ARCHITECTURE.md)). Summaries are redacted with `redact.pii`.

## 17. Evaluation criteria
TO BE DEFINED.

## 18. Acceptance tests
TO BE DEFINED.
