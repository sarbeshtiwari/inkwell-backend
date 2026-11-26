# Tenant isolation

**Status:**
- Current application: CURRENT IMPLEMENTATION.
- Agent requirements: APPROVED DESIGN ([ADR-0006](../decisions/ADR-0006-tenant-isolation.md)), not implemented.

**Last reviewed:** 2026-11-26

## Requirement (approved 2026-11-25)
> "Every task, trace, memory lookup, tool invocation and database operation must be scoped to the authenticated user's
> identity. Add explicit cross-user isolation tests."

## How the current application isolates users

| Mechanism | Where |
|---|---|
| Handlers never take a user id from the request; `login_required` supplies `Workspace(uid)` from the session | `core/security.py:184` |
| Per-user `kv` documents have `_id = "<uid>:<key>"`; every `Workspace` method builds that id from `self.uid` | `core/workspace.py` |
| Every other user collection carries `uid`, and its queries filter on it | [DATABASE_SCHEMA_REFERENCE.md](DATABASE_SCHEMA_REFERENCE.md) |
| Content is sealed with one platform key (not one key per user). Isolation is by query scoping, not cryptography | `core/crypto.py` |
| Operations: users read and cancel only their own (`operations.current(ws.uid, …)`). The Super Admin path is a separate route with a permission check, and returns metadata only | `routes/operations.py` |
| Leads: `scope=site` (all site enquiries) needs `admin.site_leads`; otherwise only the user's own | `routes/leads.py:86` |
| Public, token-addressed resources: `/p/<slug>`, `/p/i/<iid>` (32 hex), `/site-preview/<token>` (64 hex, 300 s, in memory), `/o/<token>.gif` | `features/portfolio.py`, `features/site.py`, `routes/site_pages.py` |
| Account deletion removes data by `uid` | `core/accounts.py:180` |

**Existing tests:** the route tests use separate users through `make_user` / `client_for` (`tests/conftest.py`). There
is **no dedicated cross-user isolation suite**. The checklist's `test_isolation.py` doesn't exist.

## Design for agents (PROPOSED DESIGN, implementing ADR-0006)
1. **The identity comes from the task.** The runtime loads the task and builds
   `ToolContext(uid=task.uid, trace_id, run_id, task_id, agent_id, agent_version, grants)`. Agents and models never
   supply a `uid`.
2. **Tools are the only access path.** Agents get no database handle, shell or HTTP client. Each tool uses
   `Workspace(ctx.uid)` or a query that includes `uid: ctx.uid`.
3. **Every agent collection carries `uid`.** That covers tasks, task events, runs, traces, usage, approvals, memory
   and checkpoints. Repository helpers require `uid` as an argument, so an unscoped query can't be written by
   accident.
4. **Delegation keeps the owner.** A child task inherits `uid` and `trace_id` from its parent and can't change them.
5. **Admin views are separate code paths.** They read only metadata fields through `admin.read_metadata`, with no
   content fields in the projection.
6. **Memory is per user.** Vector search queries always filter on `uid`. Shared, cross-user memory isn't part of the
   design. If it is ever wanted, it's a separate decision.
7. **Checkpoints hold user content.** They must be sealed or access-controlled to the same standard as `kv`.

## Required tests (to be written in Phase 1)
- User A cannot read, list, claim, cancel or approve B's tasks, runs, traces, usage, approvals or memory, through the
  API or through tool calls.
- A tool given a `ToolContext` for A cannot reach B's `kv`, even if the model's arguments name B's ids.
- A child task cannot change `uid`.
- Admin metadata endpoints return no content fields (checked against a list of forbidden keys).
- Vector search with A's context never returns B's memory.
