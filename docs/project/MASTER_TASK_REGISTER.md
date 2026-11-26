# Master task register

**Status:** Reconciled on 2026-11-26 from the code, git history, the status audit and the planning documents ·
**Last reviewed:** 2026-11-26

## Statuses
Exactly these values are used:
- COMPLETED AND VERIFIED
- IMPLEMENTED BUT NOT FULLY VERIFIED
- PARTIALLY IMPLEMENTED
- DOCUMENTED BUT NOT IMPLEMENTED
- APPROVED BUT NOT STARTED
- NOT APPROVED
- BLOCKED
- DEFERRED
- NOT STARTED
- UNKNOWN

Rules:
- "Verified" for code means it was tested and, where relevant, deployed.
- Documentation alone never makes a task complete.
- Every Phase 1–4 task is **NOT STARTED, and implementation is not authorized** (D-01 in
  [PROJECT_STATUS.md](PROJECT_STATUS.md#pending-decisions)). Their designs are approved in direction only.

**Priority:**
- **P0:** needed first, or a risk now.
- **P1:** needed for the first workflow.
- **P2:** needed before wider rollout.
- **P3:** later.

## Phase 0: foundations (original plan, `phase0-checklist.md`)

| ID | Task | Status | Evidence | Remaining work | Dependencies | Priority | Acceptance criteria |
|---|---|---|---|---|---|---|---|
| P0-01 | Roles, permissions, audit chain, redaction | COMPLETED AND VERIFIED | `4cb8b1d`, `8b59986`; `infra/rbac.py`, `audit.py`, `redact.py`; 43 tests; deployed and smoke-tested | Production behaviour beyond the smoke test is UNKNOWN | — | — | Met |
| P0-02 | Shared lease-based locks, guarded writes | IMPLEMENTED BUT NOT FULLY VERIFIED | `45e2fec`, `04cf85d`; `infra/locks.py`; `test_step2_locks_progress` | Deploy (DEP-07); multi-process run in production | DEP-07 | P1 | Runs in production with no lost updates; lock metrics within limits |
| P0-03 | Shared progress and cooperative cancellation (`operations`) | IMPLEMENTED BUT NOT FULLY VERIFIED | `infra/operations.py`, `routes/operations.py` | Deploy | DEP-07 | P1 | Same JSON shapes as before; cancel works from any process |
| P0-04 | Scheduler coordination (slots, `once` markers) | IMPLEMENTED BUT NOT FULLY VERIFIED | `infra/schedule.py`, `bridge.every`; `test_step2_scheduler` | Deploy | DEP-07 | P1 | Each loop runs once per interval across processes |
| P0-05 | Bounded lock waits (Stage 1) | IMPLEMENTED BUT NOT FULLY VERIFIED | `72a1681`; `test_stage1_lock_waits` | Deploy | DEP-07 | P1 | 409 + Retry-After before the proxy timeout |
| P0-06 | Versioned compare-and-set with the ABA fix | IMPLEMENTED BUT NOT FULLY VERIFIED | `5ba9022`, `a83d17e`; `test_stage2b_aba`, `test_stage2b_rollback` | Deploy; extend to more keys if needed | DEP-07 | P1 | No lost updates on versioned keys; rollback compatibility as tested |
| P0-07 | Kill switches (global / agent / user) | DEFERRED | Planned in step 2; not built | Carried into PH1-05 | — | — | See PH1-05 |
| P0-08 | Model gateway and budgets (step 4) | DEFERRED | Not built | Carried into PH1-01, PH1-02 | — | — | See PH1-01/02 |
| P0-09 | Telemetry (step 5) | DEFERRED | Not built | Carried into PH1-03 | — | — | See PH1-03 |
| P0-10 | Durable task queue (step 6) | DEFERRED | Not built | Carried into PH1-04 | — | — | See PH1-04 |
| P0-11 | Empty agent and eval collections, schemas (step 7) | DEFERRED | Not built | Carried into PH1-09 | — | — | See PH1-09 |
| P0-12 | Env rollback flags (`SCHEDULER`, `LOCKS`, `PROGRESS`, `GATEWAY`) | DOCUMENTED BUT NOT IMPLEMENTED | `phase0-checklist.md` §10; no flags in the code | Decide whether still wanted (rollback today is by redeploy) | — | P3 | Decision recorded |

## Phase 1: agent infrastructure (design approved in direction; implementation not authorized)

| ID | Task | Status | Evidence | Remaining work | Dependencies | Priority | Acceptance criteria |
|---|---|---|---|---|---|---|---|
| PH1-01 | Model gateway, registry, usage records; `chat()` signature unchanged | COMPLETED AND VERIFIED (locally, 2026-11-26) | Commit `1579f80`: `infra/gateway.py`, `models.chat()` delegates; `tests/test_gateway.py` 18 tests; full suite 437 passed | Platform-key adapter configuration and `model_configs` registry wait for D-05 / PH1-09; not deployed. New collections `model_usage`, `model_prices` are not yet in the deployment write-path inventory | D-05 (platform keys) | P0 | Met locally: existing callers unchanged; each call records the returned model id, tokens, latency, error class; no prompts, keys or URLs in records |
| PH1-02 | Budgets (user day/month, agent, task, global) | IMPLEMENTED BUT NOT FULLY VERIFIED | Commit `1af02ed`: `infra/budgets.py`, gateway pre-call check, `/api/usage`, `/api/admin/budgets`; `tests/test_budgets.py` 8 tests; full suite 490 passed | **Amounts are not set (D-05)**, so nothing is limited until an admin sets limits; platform billing exists only once platform keys are configured (D-05); edge overshoot under concurrency documented; user-facing usage UI not built | D-05 | P0 | Mechanism met with tests: refusals per scope before the provider is called, task-tree limits, audited admin changes. Not complete until amounts are decided |
| PH1-03 | Telemetry: traces and spans, redacted | COMPLETED AND VERIFIED (locally, 2026-11-26) | Commit `0c85908`: `infra/telemetry.py`; gateway `model_call` spans; task `step` spans with retry and failure marking; `tests/test_telemetry.py` 10 tests; full suite 482 passed | Tool-call, delegation and approval spans are emitted by PH1-06/07/08 as they are built; retention period waits for D-08; not deployed | D-08 | P0 | Met for model calls and tasks: one `trace_id` per task tree, correlated spans, redacted summaries, sealed payloads, owner-only and metadata-only readers |
| PH1-04 | Task queue: claim, lease, heartbeat, retry, dead-letter, idempotency | COMPLETED AND VERIFIED (locally, 2026-11-26) | Commit `cb8a7ff`: `infra/tasks.py` (`tasks`, `task_events`); `tests/test_tasks.py` 22 tests, stable over 5 repeated runs; full suite 459 passed | Retention TTL waits for D-08; worker process entry point is PH1-11; kill-switch check at claim is PH1-05; not deployed (new collections not yet in the deployment write-path inventory) | — | P0 | Met locally: atomic claim (200 tasks, 20 workers, each once), lease recovery, heartbeat, backoff retries, dead letter, idempotency race, cancellation, deadlines, stale-writer refusal, per-user isolation |
| PH1-05 | Kill switches and controlled cancellation for agent tasks | COMPLETED AND VERIFIED (locally, 2026-11-26) | Commit `d77afde`: `infra/controls.py`, checks in `tasks.claim`/`checkpoint`, `/api/admin/kill-switches`; `tests/test_controls.py` | Admin UI (PH1-12); cooperative only, cannot interrupt a step inside an external call; not deployed | — | P0 | Met locally: global, agent and user switches block new runs; running tasks stop at the next checkpoint; audited before effect; Super Admin only |
| PH1-06 | Tool registry, grants, `ToolContext(uid)` | COMPLETED AND VERIFIED (locally, 2026-11-26) | Commit `dd03e64`: `infra/tools.py`, `features/ai_jobs/agent_tools.py` (6 read tools over existing code); `tests/test_tools.py` 13 tests; full suite 503 passed | Write and external-action tools (Writer, Applicant) come with PH1-08 approvals; per-tool timeouts are declared and recorded, enforced only through the tools' own HTTP timeouts | — | P0 | Met: tools act only for the context user; ungranted, destructive and shadow-mode writes refused; external actions never run without approval |
| PH1-07 | LangGraph runtime and MongoDB checkpointer (spike first) | COMPLETED AND VERIFIED (locally, 2026-11-26) | Commit `e05e2e9`: `infra/runtime.py` (own sealed, uid-scoped `MongoCheckpointer`; agents as queue tasks); langgraph 1.2.24 added to requirements and lock; `tests/test_runtime.py` 9 tests; full suite 512 passed | Approval interrupts (PH1-08) and run version records (PH1-09); hosted use needs the new dependency deployed (not done) | — | P0 | Met: a graph checkpoints, survives a simulated worker death mid-run and resumes without repeating finished steps; checkpoints are sealed and scoped by uid |
| PH1-08 | Approvals and pause/resume | COMPLETED AND VERIFIED (locally, 2026-11-26) | Commit `4e63803`: `infra/approvals.py`, `waiting_approval` task state, `/api/agents/approvals`; `tests/test_approvals.py` 13 tests; full suite 525 passed | Approval expiry period (pending decision); approvals UI (PH2-08); no real external-action tool registered yet (Applicant, PH2-06) | — | P0 | Met: external actions always pause; run once with the approved (or edited) arguments; reject skips; owner-only, audited; crash mid-action is outcome-unknown and never repeated; shadow mode creates no approvals |
| PH1-09 | Agent and config versioning (`agent_versions`, `model_configs`) | PARTIALLY IMPLEMENTED | Commit `f7a4db6`: `agent_runs` record per run (agent, instructions, grants with tool versions, models actually returned, tokens); `agent_versions` fingerprints pinned, changed definitions refused; `tests/test_versions.py` 5 tests; full suite 530 passed | `model_configs` and the candidate/approved/active promotion lifecycle need the evaluation gate (PH4-02) and model decisions (D-05) | PH4-02, D-05 | P1 | Requirement 1 (record exact versions on every run) met; requirement 8 (no promotion without evaluation and approval) not yet |
| PH1-10 | Delegation guards (depth, children, cycles, deadline, steps) | PARTIALLY IMPLEMENTED | Commit `d77afde`: depth, children, cycle and deadline inheritance enforced in `tasks.enqueue`; tests in `tests/test_controls.py` | Max steps per run belongs to the runtime (PH1-07); default numbers (3 levels, 10 children) are proposal values awaiting product approval | PH1-07 | P0 | Depth, children, cycle, deadline: met with tests. Steps: not yet |
| PH1-11 | Worker entry point (in-process locally first) | NOT STARTED | — | `agents.worker` | PH1-04, PH1-07 | P1 | Runs K concurrent tasks against the local replica set |
| PH1-12 | Minimal Observatory (admin JSON, metadata only) | NOT STARTED | `admin.read_metadata` exists, unused | Endpoints, then UI | PH1-03 | P1 | No content fields in any response (checked by test) |
| PH1-13 | Agent APIs (`/api/agents/tasks`, events, approvals) | NOT STARTED | Proposed in architecture §7 | Implement | PH1-04, PH1-08 | P1 | Owner-only access; SSE events |
| PH1-14 | Shadow-mode flags and shadow records | NOT STARTED | — | Design (D-12), then build | D-12 | P1 | Shadow runs can't produce user-visible or external effects |

## Phase 2: first agentic workflow, "Find and Prepare Applications"

| ID | Task | Status | Evidence | Remaining work | Dependencies | Priority | Acceptance criteria |
|---|---|---|---|---|---|---|---|
| PH2-01 | Orchestrator | NOT STARTED | [spec](../agents/ORCHESTRATOR_SPECIFICATION.md) | Build | PH1-* | P1 | Spec §18 |
| PH2-02 | Job Scout | NOT STARTED | [spec](../agents/JOB_SCOUT_SPECIFICATION.md) | Build | PH1-06 | P1 | Spec §18 |
| PH2-03 | Job Analyst | NOT STARTED | [spec](../agents/JOB_ANALYST_SPECIFICATION.md) | Build | PH1-06 | P1 | Spec §18 |
| PH2-04 | Researcher (conditional) | NOT STARTED | [spec](../agents/RESEARCHER_SPECIFICATION.md) | Build | PH1-06 | P1 | Spec §18 |
| PH2-05 | Writer | NOT STARTED | [spec](../agents/WRITER_SPECIFICATION.md) | Build | PH1-06 | P1 | Spec §18 (100% `tailor_rules` compliance) |
| PH2-06 | Applicant (approval-gated) | NOT STARTED | [spec](../agents/APPLICANT_SPECIFICATION.md) | Build | PH1-08 | P1 | Zero submissions without approval |
| PH2-07 | Shadow comparison with the existing AI job match | NOT STARTED | — | Comparison metrics, evaluation datasets | PH2-01…06, D-09 | P1 | Workflow acceptance criteria in [AGENT_EXECUTION_LIFECYCLE.md](../architecture/AGENT_EXECUTION_LIFECYCLE.md#acceptance-criteria-for-the-workflow-proposed-to-be-approved) |
| PH2-08 | Approvals UI in the frontend | NOT STARTED | No such page | Build | PH1-13 | P1 | The user can approve, edit or reject each action |
| PH2-09 | Enable for the product owner's account only | NOT STARTED | — | Flag on for one user | PH2-07, PH4-02 (gate) | P2 | Product owner's acceptance |

## Phase 3: additional agents

| ID | Task | Status | Evidence | Remaining work | Dependencies | Priority | Acceptance criteria |
|---|---|---|---|---|---|---|---|
| PH3-01 | Communicator (approval-gated sends) | NOT STARTED | [spec](../agents/COMMUNICATOR_SPECIFICATION.md) | Design details, then build | PH2-09 | P2 | No send without approval; `DAILY_LIMIT` holds |
| PH3-02 | Coach | NOT STARTED | [spec](../agents/COACH_SPECIFICATION.md) | Design, then build | PH2-09 | P3 | TO BE DEFINED |

## Phase 4: intelligence and governance

| ID | Task | Status | Evidence | Remaining work | Dependencies | Priority | Acceptance criteria |
|---|---|---|---|---|---|---|---|
| PH4-01 | Vector memory (per user) | BLOCKED | Atlas tier unverified ([MEMORY_ARCHITECTURE.md](../architecture/MEMORY_ARCHITECTURE.md)) | Tier check (OPS-06), then design and build | OPS-06, D-08 | P3 | Lookups always filtered by uid; the user can view and delete |
| PH4-02 | Evaluator and promotion gate | NOT STARTED | [spec](../agents/EVALUATOR_SPECIFICATION.md) | Datasets, replay harness, gate. **The gate is needed before any configuration goes production-active**, so before PH2-09 | D-09 | P1 (gate) / P3 (full) | No promotion without a passing result and approval |
| PH4-03 | Alerts | NOT STARTED | — | Build | PH1-03 | P3 | Alert list in [OBSERVABILITY_ARCHITECTURE.md](../architecture/OBSERVABILITY_ARCHITECTURE.md) |
| PH4-04 | Full Observatory UI | NOT STARTED | — | Build | PH1-12, SEC-04 | P3 | Metadata only |
| PH4-05 | Optional OTLP export | NOT APPROVED | Proposal only | Decide | — | P3 | — |

## Deployment and production readiness

| ID | Task | Status | Evidence | Remaining work | Dependencies | Priority | Acceptance criteria |
|---|---|---|---|---|---|---|---|
| DEP-00 | Deployment documentation (five files) | COMPLETED AND VERIFIED | Accepted by the product owner, 2026-11-26 (as documentation); untracked | Commit when approved (keep uncommitted until then) | — | — | Accepted |
| DEP-01 | Stage 0.1: environment record (read-only) | NOT APPROVED | Runbook §0.1 | Approval, then execution | — | P0 | All 0.1 facts recorded (Render plan, Atlas tier, users, triggers) |
| DEP-02 | Stage 0.2: Atlas fence test | NOT APPROVED | Runbook §0.2 | Approval, then execution | DEP-01 | P1 | As defined in the runbook |
| DEP-03 | Stage 0.3: least privilege | NOT APPROVED | Runbook §0.3 | — | DEP-01 | P1 | As defined |
| DEP-04 | Stage 0.4: ingress rotation | NOT APPROVED | Runbook §0.4 | — | — | P2 | As defined |
| DEP-05 | Stage 0.5: Render suspension and process termination | BLOCKED | No accepted termination-evidence source is known | Product owner decision D-02 | D-02 | P0 (decision) | As defined; liveness observations never qualify |
| DEP-06 | Stage 0.6: fenced restart and deploy out of it | NOT APPROVED | Runbook §0.6 | — | DEP-05 | P1 | As defined |
| DEP-07 | Step 1: first migration `8b59986` → `a83d17e` | BLOCKED | — | — | DEP-01…06 pass | P1 | Runbook Step 1 complete; production on `a83d17e` |
| DEP-08 | Step R rollback rehearsal | NOT STARTED | Documented only | Rehearse on a non-production service | DEP-06 | P2 | Rehearsal passes |
| DEP-09 | Gate Stage 1: exec-form `CMD`, drain hooks, local safeguards | DOCUMENTED BUT NOT IMPLEMENTED | Architecture §8, §11, §13 | Implement in a future release | — | P2 | As defined |
| DEP-10 | Gate Stage 2: fence test harness | DOCUMENTED BUT NOT IMPLEMENTED | Architecture §12 | Implement | — | P2 | As defined |
| DEP-11 | Gate Stage 3: soft gate | DOCUMENTED BUT NOT IMPLEMENTED | Architecture §5 | — | — | P3 | As defined |
| DEP-12 | Gate Stage 4: delivery state machine | DOCUMENTED BUT NOT IMPLEMENTED | Architecture §7 | — | — | P2 (agents send email later) | As defined |
| DEP-13 | Gate Stage 5: compatibility declaration | DOCUMENTED BUT NOT IMPLEMENTED | Compatibility matrix | — | — | P3 | As defined |
| DEP-14 | Hosted agent worker service and its fencing | NOT STARTED | ADR-0003 | Design its deploy procedure | D-11, PH1-11 | P2 | Included in the write-path inventory and runbook |

## Security and governance

| ID | Task | Status | Evidence | Remaining work | Dependencies | Priority | Acceptance criteria |
|---|---|---|---|---|---|---|---|
| SEC-01 | Metadata-only Super Admin; content reveal disabled | COMPLETED AND VERIFIED (permission model) | `CONTENT_REVEAL_ENABLED = False`; deployed | Admin views come later (PH1-12) | — | — | Met for the permission model |
| SEC-02 | Point the developer `.env` at local MongoDB; local safeguards | NOT STARTED | `.env` holds the production URI (verified earlier in this project) | The owner edits `.env` | — | P0 | A local start connects to `127.0.0.1` |
| SEC-03 | External anchoring of the audit chain | DOCUMENTED BUT NOT IMPLEMENTED | `docs/audit-anchoring.md` | Implement before any content reveal | — | P2 | The anchor can be verified independently |
| SEC-04 | Privacy policy update before any admin trace or content view | NOT STARTED | Owner's condition (ADR-0009) | Draft, then approve | — | P2 | Policy updated before the feature ships |
| SEC-05 | Cross-user isolation tests for agent data | PARTIALLY IMPLEMENTED | Isolation tests exist for tasks (`test_tasks.py`), traces (`test_telemetry.py`), usage (`test_gateway.py`, `test_budgets.py`) and tools (`test_tools.py`) | Approvals, runs and memory tests come with PH1-08, PH1-09, PH4-01 | PH1-08 | P0 | Passing for every agent collection built so far |
| SEC-06 | `SECRET_KEY` rotation design | NOT STARTED | No rotation exists | Design | — | P3 | Documented procedure |
| SEC-07 | `roles` rows on account deletion | NOT STARTED | `core/accounts.py:176` | Decision D-10 | D-10 | P3 | Decision recorded |
| SEC-08 | Per-action approval for the existing auto-apply | NOT APPROVED | Standing consent today | Decision D-07 | D-07 | P3 | Decision recorded |

## Testing and quality

| ID | Task | Status | Evidence | Remaining work | Dependencies | Priority | Acceptance criteria |
|---|---|---|---|---|---|---|---|
| QA-01 | Backend suite | COMPLETED AND VERIFIED (locally) | 419 passed, 2026-11-26 | Keep green | — | — | All pass |
| QA-02 | Frontend tests | NOT STARTED | No runner configured | Choose a runner; smoke tests | — | P3 | CI runs them |
| QA-03 | Queue reliability suite | COMPLETED AND VERIFIED (locally, 2026-11-26) | `tests/test_tasks.py`, commit `cb8a7ff` | Extend with kill switches (PH1-05) and delegation limits (PH1-10) | PH1-04 | P0 | Requirement 6 list covered |
| QA-04 | Gateway tests with a scripted fake provider | COMPLETED AND VERIFIED (locally, 2026-11-26) | `tests/test_gateway.py`, `gateway.FakeProvider`, commit `1579f80` | Extend with budgets (PH1-02) | PH1-01 | P0 | Existing callers covered |
| QA-05 | Evaluation datasets | NOT STARTED | — | Decision D-09 | D-09 | P1 | Datasets versioned |
| QA-06 | Load tests for concurrent runs | NOT STARTED | — | — | PH1-11 | P2 | TO BE DEFINED |

## Operational readiness

| ID | Task | Status | Evidence | Remaining work | Dependencies | Priority | Acceptance criteria |
|---|---|---|---|---|---|---|---|
| OPS-01 | Off-machine copy of the 7 commits and the documentation | NOT STARTED | `main` is 7 ahead of `origin/main`; `docs/` untracked | Decision D-04 | D-04 | P0 | A copy exists somewhere other than the Mac, without triggering a deploy |
| OPS-02 | Offline record of `SECRET_KEY` and the audit keys | UNKNOWN | — | Confirm | — | P0 | Confirmed by the owner |
| OPS-03 | Atlas backup policy | UNKNOWN | — | Part of DEP-01 | DEP-01 | P1 | Recorded |
| OPS-04 | Restore rehearsal | NOT STARTED | — | Rehearse into a non-production database | OPS-03 | P2 | A restore works |
| OPS-05 | Monitoring and alerting beyond Sentry | NOT STARTED | [MONITORING_AND_ALERTING.md](../operations/MONITORING_AND_ALERTING.md) | — | — | P2 | TO BE DEFINED |
| OPS-06 | Verify Atlas Vector Search support and limits | NOT STARTED | `list_search_indexes` accepted (checklist) | Needs approval (a test index changes Atlas) | DEP-01 | P3 | Limits recorded |
| OPS-07 | This documentation set | IMPLEMENTED BUT NOT FULLY VERIFIED | Files under `backend/docs/`, untracked | Product owner review | — | P0 | Review accepted |
