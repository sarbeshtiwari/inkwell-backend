# Project status

**Status:** Reflects the read-only project status audit of 2026-11-26, re-checked against the code while writing this
documentation. · **Last reviewed:** 2026-11-26

## One-paragraph summary
- **What exists:** Inkwell is a working job-search and outreach application, plus a security foundation that is in
  production (roles, audit, redaction) and a coordination layer that is tested but not deployed.
- **Agentic capability:** none exists in code.
- **Production** runs `8b59986` (Phase 0 Step 1).
- **Local `main`** (`a83d17e`) adds Steps 2 to 2B. It is 7 commits ahead of `origin/main`, unpushed.
- **The deployment procedure** to ship it is accepted as documentation, but executing it is not approved, and the
  first migration is **BLOCKED** at Stage 0.5.
- **The original Phase 0 plan:** steps 1–3 are built. Steps 4–7 are not started: gateway and budgets, telemetry,
  task queue, kill switches, agent collections.
- **Phase 1 agent infrastructure is not authorized.**

## Milestones

| Milestone | Commits | Implemented | Tests (all pass in the 419) | Accepted | Deployed |
|---|---|---|---|---|---|
| Phase 0 Step 1: roles, permissions, audit, redaction | `4cb8b1d`, `8b59986` | `infra/rbac.py`, `infra/audit.py`, `infra/redact.py`, `/api/me` role and permissions | `test_rbac_audit_redact` (19), `test_step1_fixes` (24) | Yes | **Yes** (smoke-tested only) |
| Step 2: shared locks, progress, cancellation, scheduler | `45e2fec`, `04cf85d` | `infra/locks.py`, `infra/operations.py`, `infra/schedule.py`, `routes/operations.py` | `test_step2_locks_progress` (36), `test_step2_scheduler` (7) | Yes | No |
| Stage 1: bounded lock waits | `72a1681` | 409 + Retry-After, metrics, I/O outside the lock, `tools/bench_locks.py` | `test_stage1_lock_waits` (13) | Yes | No |
| Stage 2A: optimistic concurrency (templates) | `1d544b7` | Versioned compare-and-set | `test_stage2a_versioned` (20) | Yes, then **superseded** | No; must never be deployed after `5ba9022` |
| Stage 2B batch 1 | `5c21025` | `inbox_companies`, `ai_portals` | `test_stage2b_versioned` (18) | Yes, then **superseded** | No; same |
| ABA fix and verification | `5ba9022`, `a83d17e` | Server-computed versions plus 128-bit write token; legacy byte matching | `test_stage2b_aba` (19), `test_stage2b_rollback` (10) | Yes | No |
| Deployment documentation (5 files) | untracked | — | — | Yes, as documentation only | n/a |

## Phase 0 (original plan, `phase0-checklist.md`)

| Step | Planned | State |
|---|---|---|
| 1 | redact, audit, rbac | **Done**, in production |
| 2 | locks, progress, controls (kill switches, cancellation tokens) | Locks, progress and cancellation done (local). **Kill switches not started** |
| 3 | Leader-elected scheduler | Done differently: shared slots per job (local) |
| 4 | Gateway and budgets (`models.chat` → `gateway.complete`, usage, registry, prices) | **Not started** |
| 5 | Telemetry (traces, spans) | **Not started** |
| 6 | Durable task queue | **Not started** |
| 7 | Empty agent and eval collections, schemas, flag defaults | **Not started** |

### Differences from the plan
- The package is `backend/infra/`, not `backend/platform/`.
- The planned rollback flags `SCHEDULER`, `LOCKS`, `PROGRESS` and `GATEWAY` don't exist.
- The plan targeted about 70 new tests on top of 193. The suite now has 419 tests, but none for the queue, gateway or
  isolation.

## Approval boundaries in force (product owner, 2026-11-26)

| Item | State |
|---|---|
| Deployment documentation revision | **Accepted** |
| Stage 0.2 Atlas verification | Not yet approved |
| Stage 0.5 test-only build | Not approved |
| Stage 0.5 Render verification | Not approved |
| Stage 0.6 Render verification | Not approved |
| Production credential fencing | Not approved |
| First migration `8b59986` → `a83d17e` | Not approved, and **BLOCKED** (no accepted termination evidence) |
| Phase 1 agent infrastructure | **Not authorized** |
| This documentation set | Created on request. Not yet reviewed |

## Risks

| ID | Risk | Mitigation (task) |
|---|---|---|
| R1 | The 7 unpushed commits and all untracked documentation exist only on the developer Mac | OPS-01 |
| R2 | The Mac's `.env` holds the production `MONGODB_URI` and user. A local run is a concurrent production instance | SEC-02 |
| R3 | Production can't move past `8b59986` until Stage 0.5 is resolved | DEP-05 decision |
| R4 | Coordination adds about 0.6–0.8 s per guarded write (measured locally against Render latency) | Accept or optimise |
| R5 | The Render plan is unknown. If it's Free, SMTP is blocked (F2) | DEP-01 |
| R6 | `SECRET_KEY` is a single root key, with no rotation and its offline copy unknown | OPS-02 |

## Pending decisions

| ID | Decision | Owner | Blocks |
|---|---|---|---|
| D-01 | Authorize Phase 1 agent infrastructure (local development) | Product owner | All of Track A |
| D-02 | Path out of the Stage 0.5 blocker: an accepted termination-evidence source from Render, a different stop mechanism (with its own design), or staying on `8b59986` | Product owner | DEP-05, DEP-07 |
| D-03 | Ship `a83d17e` alone, or together with Phase 1 code in one fenced migration | Product owner | Release planning |
| D-04 | How to get the local commits and documents off the Mac (bundle, or a non-deploying branch) | Product owner | OPS-01 |
| D-05 | Initial model providers and models, prices, default budgets | Product owner | PH1-01, PH1-02 |
| D-06 | BYOK timing (approved as "later") | Product owner | — |
| D-07 | Move existing auto-apply from standing consent to per-action approval? | Product owner | — |
| D-08 | Retention periods for traces, usage, approvals, memory, checkpoints | Product owner | PH1-03, PH4-01 |
| D-09 | Evaluation datasets and promotion thresholds | Product owner | PH2-07, PH4-02 |
| D-10 | Keep or remove `roles` rows on account deletion | Product owner | — |
| D-11 | When to add the separate worker service | Product owner | Hosting agents |
| D-12 | Storage of shadow-mode flags and shadow records | Engineering proposal, then owner | PH1-14 |
| D-13 | Shared (non-user) knowledge for agents, e.g. company facts | Product owner | PH4-01 |
| D-14 | Render plan (Free vs Starter) and Atlas tier, which have cost implications | Product owner | DEP-01, PH4-01 |

## Inconsistencies found

| # | Inconsistency | Where |
|---|---|---|
| I-1 | `render.yaml` says `plan: starter`; the deployment documents describe Render Free | `render.yaml`, `docs/deployment/` |
| I-2 | The Phase 0 plan put new code in `backend/platform/`; it was built in `backend/infra/` | `phase0-checklist.md` |
| I-3 | Rollback flags were planned; none were implemented | same |
| I-4 | Plan step 3 was a leader-elected scheduler; the built design uses per-job slots | `infra/schedule.py` |
| I-5 | Plan step 2 included kill switches; only cancellation was built | `infra/operations.py` |
| I-6 | Content reveal requires an "immutable audit event"; the audit log is tamper-evident, not immutable (anchoring not implemented) | `infra/audit.py`, `docs/audit-anchoring.md` |
| I-7 | Existing auto-apply uses standing consent; the approved agent rule is per-action approval | `features/ai_jobs/routes.py:74` |
| I-8 | The frontend uses email + password; `web/README` and the root README describe email codes only | `frontend/src/pages/Auth.jsx`, `README.md` |
| I-9 | Four permissions (`read_metadata`, `manage_models`, `manage_budgets`, `manage_roles`) exist with no route using them | `infra/rbac.py` |
| I-10 | The root `README.md` still uses the name "Reachout"; internal identifiers (`reachout` database, cookie `ro_session`, `appname`) keep the old name | `README.md`, `core/db.py`, `core/http.py` |
| I-11 | `search` is listed in `USER_COLLECTIONS` but has no reader or writer | `core/accounts.py:177` |
| I-12 | The architecture proposal says 19 `threading.Thread` call sites; the current code has 14 (outside `tools/`), after Step 2 | `inkwell-agentic-architecture.md` §0 |
| I-13 | The Phase 0 checklist says "193 tests"; the suite now has 419 | `phase0-checklist.md` |
