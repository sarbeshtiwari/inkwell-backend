# ADR-0009: Super Admin sees metadata only

- **Status:** Approved with conditions (product owner, 2026-11-25)
- **Date:** 2026-11-25
- **Implemented:** The permission model, yes (Step 1, commit `4cb8b1d`/`8b59986`, deployed). The admin views, no.

## Decision
The product owner's wording:

> "Implement metadata-only visibility into other users' agent executions by default."

They listed what the Super Admin should see: agent activity and health, execution history and status, duration and
latency, model and token consumption, estimated costs, tool invocation metadata, errors and failure diagnostics.

> "Actual user content, including emails, resumes, contacts and personal information, must remain protected by
> default. If exceptional content access is implemented, it must require explicit authorization, record the reason
> and create an immutable audit event. Update the privacy policy before enabling any functionality that changes the
> existing privacy promise."

## Implemented (verified, `infra/rbac.py`)
- Roles: `user`, `super_admin`.
- `admin.reveal_content` is excluded from the `super_admin` role:
  `ROLE_PERMISSIONS[SUPER_ADMIN] = frozenset(PERMISSIONS) - {"admin.reveal_content"}`.
- `CONTENT_REVEAL_ENABLED = False` (`infra/rbac.py:38`).
- Admin actions are written to the hash-chained audit log (`infra/audit.py`). The log is tamper-evident, not
  immutable: its own docstring says anyone holding the signing key and database write access could rebuild it.
  External anchoring ([../audit-anchoring.md](../audit-anchoring.md)) is designed but not implemented.

## Consequences
- The requirement says "immutable audit event". The current log is tamper-evident. Before content reveal is ever
  enabled, anchoring (or another immutability mechanism) must be implemented. **Recorded as an inconsistency.**
- Admin views must be built from metadata fields only (counts, timings, ids, error classes). Telemetry needs a
  redacted summary field kept separate from encrypted payloads.

## Related
[../engineering/AUTHENTICATION_AND_RBAC.md](../engineering/AUTHENTICATION_AND_RBAC.md),
[../architecture/OBSERVABILITY_ARCHITECTURE.md](../architecture/OBSERVABILITY_ARCHITECTURE.md)
