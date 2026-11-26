# ADR-0008: Budgets, execution limits and kill switches

- **Status:** Approved (product owner, 2026-11-25)
- **Date:** 2026-11-25
- **Implemented:** No. Only per-operation cooperative cancellation exists (`infra/operations.py`).

## Decision
The product owner's wording (mandatory requirements 3 and 5):

> "Enforce token, execution, delegation-depth and expenditure limits. Prevent infinite delegation and runaway
> execution."

> "As Super Admin, I must be able to stop new agent executions globally, per agent, or per user. Existing running
> tasks must have a controlled cancellation mechanism."

The budget scopes come from [ADR-0004](ADR-0004-platform-managed-model-keys.md).

## Design (proposed; numbers are the proposal's and not approved)
- **Budgets:** checked by the gateway before each call.
  - Scopes: per user per day and per month; per agent per run; per task (tokens and cost); global per day and per
    month.
  - Exceeding one is a hard stop with a clear message.
- **Execution limits:**
  - maximum delegation depth (the proposal says 3);
  - maximum child tasks per task;
  - a deadline, propagated to children;
  - cycle detection through an ancestry list on each task;
  - a maximum number of steps per run.
- **Kill switches** (a `controls` or `kill_switches` collection; scopes global, `agent:<id>`, `user:<uid>`):
  - checked when a task is claimed and at every checkpoint;
  - setting one needs `admin.control_agents` and is audited.
- **Cancellation of running tasks:** reuse the cooperative model in `infra/operations.py` (`cancel_requested`,
  `checkpoint()`, state `cancelled`). Nothing is killed mid-step.

## Existing pieces to reuse
- `admin.control_agents` permission (`infra/rbac.py`).
- Operations cancellation, including Super Admin cancellation of another user's task (`routes/operations.py:45`).
- `core/sending.py` daily send limit (`DAILY_LIMIT`). It is an email limit, not a model budget.

## Related
[../operations/COST_AND_USAGE_GOVERNANCE.md](../operations/COST_AND_USAGE_GOVERNANCE.md),
[../engineering/SECURITY_AND_GOVERNANCE.md](../engineering/SECURITY_AND_GOVERNANCE.md)
