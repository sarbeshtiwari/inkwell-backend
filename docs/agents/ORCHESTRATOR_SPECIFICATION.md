# Orchestrator specification

**Status:** PROPOSED (agent set approved in direction; this specification is not approved) · **Phase:** 2 (first workflow) ·
**Implemented:** No · **Last reviewed:** 2026-11-26

Shared rules for every agent: [README.md](README.md#rules-shared-by-every-agent).

## 1. Purpose
Turn a user's goal (e.g. "find and prepare applications for 10 backend roles this week") into a plan, delegate sub-tasks to specialised agents, track them to completion, and report to the user.

## 2. Responsibilities
- Interpret the goal and the user's constraints (roles, location, count, whether submission is wanted).
- Read the user's profile, rules and learned preferences.
- Produce a plan of sub-goals.
- Create child tasks through `delegate`, wait for them, and combine their typed outputs.
- Decide when the goal is met, partly met or cannot be met.
- Produce a final report: what was found, prepared, submitted and skipped, and why.

## 3. Must never
- Call external-action tools. It has none.
- Bypass approvals, or present a draft as submitted.
- Delegate to itself or to an ancestor (cycle), or exceed the depth and child limits.
- Change the task's `uid` or `trace_id`.
- Invent results that a child did not return.

## 4. Inputs
Task `goal` (text), `constraints` (schema TO BE DEFINED), `mode` (`shadow` / `on`), budget, deadline, and `ToolContext`.

## 5. Outputs
A plan (sub-goals); a final report (structured: counts, items with status and reason, links to child outputs); task status.

## 6. Available tools
Runtime tools (`task.create`, `task.status`, `delegate`), `memory.read` (Phase 4), profile, rules and learned-taste reads (`learning.taste`), and `notify`.

## 7. Tool permissions
`read` and runtime tools only. It may delegate to: Job Scout, Job Analyst, Researcher, Writer, Applicant (Phase 3: Communicator, Coach).

## 8. Model requirements
- Any active `model_configs` version assigned to this agent, through the gateway.
- Structured JSON output validated against the agent's output schema.
- The provider and model are TO BE DEFINED. The proposal suggests cheaper models for high-volume steps. The plan must be valid JSON against a plan schema.

## 9. Memory requirements
Working memory (plan, child results) lives in LangGraph state. Long-term memory (Phase 4) is optional, e.g. goals the user repeats.

## 10. Planning and execution lifecycle
1. Plan, then checkpoint.
2. Delegate to the Job Scout, then wait (`waiting_children`).
3. Delegate to the Job Analyst with the Scout's jobs.
4. Delegate to the Writer for the top N.
5. Delegate to the Applicant.
6. Wait for approvals (on mode).
7. Report.

The Orchestrator may re-plan once when a child returns too few results (for example, by widening the criteria). The re-plan limit is TO BE DEFINED.

## 11. Delegation
- It is the root of the trace and delegates to every specialised agent.
- Children inherit `uid`, `trace_id`, `mode`, deadline and the remaining budget.

## 12. Human approval
None for itself. It surfaces the Applicant's pending approvals in its report and waits.

## 13. Errors and retries
- A failed child: the Orchestrator retries it once if the error is retryable, otherwise reports partial results.
- A budget or deadline breach stops the whole tree with a clear status.

## 14. Execution limits
- Depth is the root, at 0.
- Max children per task, max re-plans and max steps are proposed values, TO BE DEFINED. The proposal sets max depth at 3.

## 15. Cost controls
- Runs under the per-agent run budget and the trace ceiling it inherits.
- The gateway refuses calls past the user, global or task limits ([../operations/COST_AND_USAGE_GOVERNANCE.md](../operations/COST_AND_USAGE_GOVERNANCE.md)).
- Amounts are TO BE DEFINED. The Orchestrator sets how the trace ceiling is split between its children (allocation rule TO BE DEFINED).

## 16. Observability
Spans for every model call, tool call, delegation, retry and error, plus the final output, under the task's `trace_id` ([../architecture/OBSERVABILITY_ARCHITECTURE.md](../architecture/OBSERVABILITY_ARCHITECTURE.md)). Summaries are redacted with `redact.pii`. One `delegation` span per child.

## 17. Evaluation criteria
- Plan quality: steps relevant to the goal, no redundant delegations.
- Goal completion rate.
- Report accuracy: every claim is backed by a child output.
- Datasets: TO BE DEFINED.

## 18. Acceptance tests
- A goal produces child tasks with the same `uid` and `trace_id`.
- A cycle is refused, and so is a delegation past the depth limit.
- Cancelling the root cancels every child.
- In shadow mode the final report shows zero external effects.
- A run record contains every version field.
