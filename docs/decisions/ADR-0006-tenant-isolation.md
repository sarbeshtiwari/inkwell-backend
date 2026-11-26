# ADR-0006: Tenant isolation for every agent operation

- **Status:** Approved (product owner, 2026-11-25)
- **Date:** 2026-11-25
- **Implemented:** Partly. Existing data access is scoped by `uid`; agent operations and their isolation tests don't
  exist yet.

## Decision
The product owner's wording (mandatory requirement 4):

> "Every task, trace, memory lookup, tool invocation and database operation must be scoped to the authenticated
> user's identity. Add explicit cross-user isolation tests."

## Current state (verified in code)
- Per-user documents in `kv` are keyed `<uid>:<key>` (`core/workspace.py`, `Workspace.load`).
- Other user collections carry `uid`.
- Account deletion removes them by `uid` (`core/accounts.py:180`, `delete_user_data`).
- Route handlers receive a `Workspace(uid)` from `login_required` (`core/security.py`), so handlers don't take a user
  id from the request.
- Operations are read and cancelled only for the signed-in user (`routes/operations.py`). The Super Admin path is
  separate and gated by `admin.control_agents`.

## Design (proposed)
- Every tool receives a `ToolContext(uid, trace_id, run_id, …)` built by the runtime from the task's owner. Tools
  can't choose another `uid`.
- Every agent collection carries `uid`, and every query includes it.
- Admin metadata views read only fields with no content.
- Explicit cross-user tests: user A cannot read, claim or see B's tasks, traces, spans, usage, approvals or memory.
  The checklist names `test_isolation.py`; it doesn't exist.

## Consequences
Shared (non-user) memory, if it is ever added, needs its own policy. **TO BE DEFINED.**

## Related
[../engineering/TENANT_ISOLATION.md](../engineering/TENANT_ISOLATION.md)
