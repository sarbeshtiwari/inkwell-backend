# ADR index

**Status:** Draft for review · **Last reviewed:** 2026-11-26

| ADR | Title | Status | Implemented? |
|---|---|---|---|
| [0001](ADR-0001-mongodb-atlas-not-postgresql.md) | MongoDB Atlas for all data, not PostgreSQL | **Approved** (2026-11-25) | Partly. Existing app and Phase 0 collections use MongoDB; agent collections don't exist yet |
| [0002](ADR-0002-langgraph-durable-agent-runtime.md) | LangGraph for durable agent execution | **Approved in direction** | No |
| [0003](ADR-0003-separate-agent-worker.md) | A separate agent worker service | **Approved in direction** | No |
| [0004](ADR-0004-platform-managed-model-keys.md) | Platform-managed model keys first, BYOK later | **Approved** (2026-11-25) | No (today only user keys and local CLIs) |
| [0005](ADR-0005-human-approval-for-external-actions.md) | Human approval before external actions | **Approved** (2026-11-25) | Not for agents (none exist). Existing features use their own consent rules |
| [0006](ADR-0006-tenant-isolation.md) | Tenant isolation for every agent operation | **Approved** (2026-11-25) | Existing data is scoped by `uid`. Agent isolation tests don't exist yet |
| [0007](ADR-0007-versioned-agent-and-model-configs.md) | Versioned agent and model configurations, evaluated before promotion | **Approved** (2026-11-25) | No |
| [0008](ADR-0008-budgets-and-execution-controls.md) | Budgets, execution limits and kill switches | **Approved** (2026-11-25) | No (only per-operation cancellation exists) |
| [0009](ADR-0009-metadata-only-super-admin.md) | Super Admin sees metadata only | **Approved** (2026-11-25) | Yes for the permission model (`CONTENT_REVEAL_ENABLED = False`); no admin UI exists |
| [0010](ADR-0010-shadow-mode-first-workflow.md) | "Find and Prepare Applications" first, in shadow mode | **Approved** (2026-11-25) | No |
| [0011](ADR-0011-credential-fenced-deployment.md) | Credential-fenced production deployments | **Accepted (documentation only)** (2026-11-26) | Not executed. **Blocked** at Stage 0.5 |
| [0012](ADR-0012-mongodb-coordination-layer.md) | Shared coordination in MongoDB (leases, operations, schedule slots, versioned writes) | Built under the approved Phase 0 plan; each step accepted after review | Yes, in local commits `45e2fec`…`a83d17e` (tests pass locally). Not deployed |
| [0013](ADR-0013-mongodb-task-queue.md) | A MongoDB-backed task queue (no Redis or Celery initially) | **Approved in direction** | No |
