# Glossary

**Status:** Reference · **Last reviewed:** 2026-11-26

Deployment terms (fence, fence evidence, `P`, `M`, `T`, termination evidence, liveness observation, generation) are
defined once in the runbook's *Definitions*
([../deployment/RENDER_FREE_DEPLOYMENT_RUNBOOK.md](../deployment/RENDER_FREE_DEPLOYMENT_RUNBOOK.md#definitions-used-identically-in-all-five-documents)).
They are only summarised here; the runbook is authoritative.

| Term | Meaning |
|---|---|
| **Agent** | A goal-driven component that plans, uses granted tools, delegates and records telemetry. None exist yet |
| **Agent version** | An immutable agent configuration: instructions, I/O schema, tool grants, model config version, limits (proposed `agent_versions`) |
| **Approval** | A user's explicit, per-action decision on a proposed external action (proposed `approvals`) |
| **Audit log** | `audit_log`: an HMAC hash chain of administrative actions; tamper-evident, not immutable (`infra/audit.py`) |
| **BYOK** | Bring your own key: the user supplies a model provider key (how cloud models work today) |
| **Checkpoint (operations)** | `op.checkpoint()`: the safe point where a running operation checks cancellation and its lease (`infra/operations.py`) |
| **Checkpoint (LangGraph)** | Persisted graph state that lets a run resume (proposed) |
| **Content reveal** | Exceptional Super Admin access to user content. Disabled (`CONTENT_REVEAL_ENABLED = False`) |
| **Delegation** | An agent creating a child task for another agent, with the same `uid` and `trace_id` (proposed) |
| **External action** | A tool effect outside Inkwell: email, WhatsApp, application submission, posts, GitHub writes. Always needs approval for agents |
| **Fence (deployment)** | Removing a deployment generation's database user's write privileges, so a stale process can't write. See the runbook |
| **Fence number (`fence`)** | The ever-growing number on a lease in `locks`, used to order holders (`infra/locks.py`) |
| **Generation** | One deployment's database user and secret (`inkwell_g1`, `inkwell_g2`, …). See the runbook |
| **Guarded write** | A transaction that re-checks the caller's leases before writing (`locks.guarded_write`) |
| **Kill switch** | A stop flag for new agent executions: global, per agent or per user (approved; not built) |
| **Lease** | Time-limited ownership of a lock, judged by the database server clock and renewed in the background |
| **Liveness observation** | Any sign about whether a process is running (connections, status, elapsed time). **Never** termination evidence |
| **Model config** | A versioned assignment of a model and parameters to a purpose or agent (proposed `model_configs`) |
| **Model gateway** | The single code path to model providers, enforcing budgets and recording usage (approved; not built) |
| **Operation** | A user-started long-running job recorded in `operations` (`infra/operations.py`) |
| **Outcome unknown** | An external action that may or may not have happened because the process stopped before recording it |
| **Schedule slot** | The shared "next run" record for a recurring job in `schedules` (`infra/schedule.py`) |
| **Seal / unseal** | Encrypt or decrypt a value with the data key (`core/crypto.py`) |
| **Shadow mode** | Running an agent workflow beside the existing feature with no external or user-visible effects, for comparison |
| **Span / trace** | Telemetry records: one trace per goal, one span per event (proposed) |
| **Super Admin** | The `super_admin` role: platform permissions, metadata only |
| **Termination evidence** | A post-exit record accepted by Stage 0.5 proving the old process stopped. None is known today |
| **Tool** | A registered, schema'd, permissioned wrapper around existing code, run with a `ToolContext` (proposed) |
| **ToolContext** | `uid`, trace, run, task, agent and version, grants and mode, built by the runtime; tools can't change the `uid` (proposed) |
| **Versioned record** | A `kv` record written by compare-and-set, with `version` and `wid` (`Workspace.update`) |
| **Workspace** | `core/workspace.py:Workspace(uid)`: user-scoped access to `kv`, documents and the send log |
| **`8b59986` / `a83d17e`** | The production release, and the tested local candidate release |
