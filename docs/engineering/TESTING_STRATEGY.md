# Testing strategy

**Status:**
- Backend suite: TESTED IMPLEMENTATION.
- Agent test plan: APPROVED requirements (ADR-0006, ADR-0013) with a PROPOSED structure.

**Last reviewed:** 2026-11-26

## Latest verified result
- **419 passed in 160.55 s**, run on 2026-11-26 at local `main` `a83d17e`.
- It ran against a local mongod replica set `rs0` on `127.0.0.1`.
- No production system was involved: `tests/conftest.py` removes `MONGODB_URI` and sets `INKWELL_NO_DOTENV=1`.

## How the suite is isolated (`backend/tests/conftest.py`)
- **Database:** a random database `reachout_test_<8 hex>`, dropped at session end. It asserts the name is not
  `reachout`.
- **Environment:**
  - removed: `MONGODB_URI`, `CLOUDINARY_URL`, `SMTP_HOST`, `APP_ENV`, `COOKIE_SECURE`, `NETLIFY_PROXY_SECRET`,
    `SITE_URL`, `LANDING_URL`, `SENTRY_DSN`, `WHATSAPP_ENABLED`;
  - set: a test-only `SECRET_KEY`, `BOUNCE_WATCH=0` and `NO_WORKERS=1` (no background loops), a temporary
    `DATA_DIR`, and `HOST=127.0.0.1`.
- **Replica set check:** the suite exits if transactions aren't supported, i.e. if the database isn't a replica set.
- **Helpers:** `make_user()`, `client_for(uid)` (a test client with a real session), the `H` header
  (`X-Requested-With: fetch`), and `patch_shared()` (patch a name in every module that imported it).
- **Skips:**
  - page tests skip when `web/privacy.html` is absent (`needs_pages`);
  - the rollback tests skip without git;
  - one multi-process test skips without a local `mongod` binary (`tests/test_step2_locks_progress.py:767`).

## Test files

| File | Tests | Covers |
|---|---|---|
| `test_security.py` | 26 | Request guards, rate limits, headers, auth |
| `test_password_auth.py` | 6 | Password sign-up, login and change |
| `test_features.py` | 22 | Core features |
| `test_ai_jobs.py` | 21 | AI job match pipeline |
| `test_ai_models.py` | 3 | Model configuration |
| `test_career_ops.py` | 15 | Evaluation, tailoring, tracker |
| `test_finder.py` | 9 | Email finder |
| `test_github_contrib.py` | 1 | GitHub contribution flow |
| `test_learning_sites.py` | 8 | Learning, sites |
| `test_morrow_features.py` | 19 | Briefing and other features |
| `test_nearby.py` | 5 | Nearby companies |
| `test_updater.py` | 2 | Updater |
| `test_rbac_audit_redact.py` | 19 | **Step 1**: roles, permissions, audit chain, redaction |
| `test_step1_fixes.py` | 24 | **Step 1 fixes**: safe audit writes, admin reconciliation, redacted errors |
| `test_step2_locks_progress.py` | 36 | **Step 2**: leases, fencing, guarded writes, operations, cancellation, multi-process |
| `test_step2_scheduler.py` | 7 | **Step 2**: schedule slots, once markers |
| `test_stage1_lock_waits.py` | 13 | **Stage 1**: bounded waits, 409 + Retry-After, metrics |
| `test_stage2a_versioned.py` | 20 | **Stage 2A**: compare-and-set primitive |
| `test_stage2b_versioned.py` | 18 | **Stage 2B**: `inbox_companies`, `ai_portals` |
| `test_stage2b_aba.py` | 19 | **ABA fix**: server-computed versions, write tokens, legacy records |
| `test_stage2b_rollback.py` | 10 | Rollback and forward against the real code of `8b59986` and `5c21025`, taken with `git archive` |

The function counts above (`def test_`) add up to 303. Parametrised tests expand to the 419 collected.

## Gaps
- **Frontend and web:** no tests. CI only checks that the build succeeds.
- **Real providers:** no end-to-end tests against SMTP, IMAP, job boards, GitHub, LinkedIn or model providers. These
  are faked or monkeypatched.
- **Concurrency in production:** never exercised. The fence tests (Atlas, credential revocation) belong to deployment
  Stage 0.2 and are **not approved**.
- **Isolation:** no dedicated cross-user isolation suite.
- **Load:** no load tests.

## How to run
See [DEVELOPMENT_SETUP.md](DEVELOPMENT_SETUP.md#running-the-tests).

## Agent testing plan (PROPOSED structure; requirements approved)

| Suite (proposed name) | Must prove | Source of the requirement |
|---|---|---|
| `test_queue.py` | Atomic claim (many workers, each task claimed once); lease expiry and recovery; heartbeat; retries with backoff; dead-letter; idempotency key prevents duplicates; crash mid-task → reclaimed and finished once; kill switch blocks claim; cancellation; depth, cycle and child limits; deadlines | Requirement 6; checklist §9 |
| `test_gateway.py` | `chat()` keeps its signature for every caller (fake provider); usage record fields including the returned model id; cost from the price table; budget refusal per scope; error classification; no secrets in usage or spans | ADR-0004, ADR-0008 |
| `test_isolation.py` | Cross-user isolation for tasks, traces, usage, approvals, memory, tools | ADR-0006 |
| `test_approvals.py` | `external_action` tools always pause; no execution without a matching approval; edits create a new approval; idempotent resume | ADR-0005 |
| `test_versions.py` | Each run records agent, instructions, tools and model config versions; no promotion without an evaluation result and an approval | ADR-0007 |
| `test_runtime.py` | Graph checkpoint and resume after a worker crash; interrupt and resume; delegation guard | ADR-0002 |
| Evaluation suites | Output quality per agent on fixed datasets, kept separate from execution success; historical task replay | Requirement 2; [../agents/EVALUATOR_SPECIFICATION.md](../agents/EVALUATOR_SPECIFICATION.md) |

**Rule:** agent tests use a **scripted fake model provider** through the gateway. No real provider key is used in
tests.
