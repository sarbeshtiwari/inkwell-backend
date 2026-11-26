# Deployment compatibility matrix

**Two ways two releases can share the production database:**
- **Concurrent execution:** both run against it at the same time. On Render that happens during every overlapping
  deploy, for ~60–90 s, and the old instance's background loops keep writing.
- **Sequential (fenced):** all before the new release writes:
  - HTTP ingress is closed;
  - the old instance is stopped, with termination evidence (runbook step 1.4): a post-exit record accepted by Stage
    0.5. A liveness observation (such as zero database connections) never qualifies. None is known today, so this is
    blocked;
  - its database user is *fenced*, with the *fence evidence* recorded (runbook *Definitions*). That old connections
    are refused by then is empirical: `P + M` is a measured operational threshold, not a proven bound.

  See `RENDER_FREE_DEPLOYMENT_RUNBOOK.md`. The fence alone doesn't make it sequential for **external side effects**: a
  live old thread can still call a provider before its next write, so stopping the process is part of the definition.

**Credential fencing is not approved for production until Stage 0 passes**: Atlas 0.2 and Render 0.5 and 0.6. "Safe
(fenced)" below means safe *given* that approval.

## Four kinds of compatibility

Sharing a database schema does **not** make two releases safe to run at the same time. Each of these is a separate
question, and overlap is allowed only when **all four** have been shown, with tests, for that exact pair of releases.

| Dimension | Question | How it's shown |
|---|---|---|
| **Data format** | Can each release read and write what the other writes without corrupting it or refusing it? (field names, encodings, version/token fields, record shapes) | Each release run against records written by the other (real code from `git archive`, as in `tests/test_stage2b_rollback.py`) |
| **Concurrent execution** | Do both use the same coordination for the same data: shared leases, versioned CAS, transactions, task records? Can one's writes overwrite the other's? | Both run as separate processes against one database, with interleaved writers, checking for lost updates |
| **Background scheduler** | Do both coordinate recurring and once-only work the same way (schedule slots, `once` markers, leases), so nothing runs twice or overlaps? | Both releases' loops run together; check that each job runs once per interval and notifications aren't duplicated |
| **External side effects** | Can both start the same external action (email, WhatsApp, application, LinkedIn, GitHub, Cloudinary) for the same item, and do they record outcomes the same way? | Both run with fake providers; check that each item is sent at most once and outcomes are recorded once |

**`DATA_CONTRACT` / compatibility declaration (Stage 5)** is **proposed, not implemented**. When implemented, it
declares all four dimensions, not data format alone. Admission (Stage 3) refuses a release whose declaration differs
from the active one. Even then, **a matching declaration only permits overlap if the pair has been tested** as above.

**Default until Stage 3 and Stage 5 are implemented and tested: every deploy is credential-fenced.**

## Matrix

| Old release | New release | Data format | Concurrent execution | Scheduler | External effects | Concurrent execution allowed? | Safe? |
|---|---|---|---|---|---|---|---|
| `8b59986` | `a83d17e` | **Compatible** in both directions (tested with real `8b59986` code) | **Incompatible**: the old release locks in memory only; whole-record saves overwrite newer data | **Incompatible**: different markers (`last_auto` vs `once`; digest marker after vs before notifying), so duplicate morning-sync and digest notifications | **Incompatible**: campaigns tracked differently (memory vs `operations`), so the same person can be emailed twice; the queue claim is the same | **No** | Concurrent: **unsafe**. Fenced: **safe** (given Stage 0) |
| `5ba9022` | `a83d17e` | **Identical** runtime code (`git diff 5ba9022 a83d17e` changes only `docs/`, `tests/`) | Designed for it (Step 2) and tested in multi-process tests; **not run in production** | Designed for it; tested | Same code; delivery state machine absent in both, so an in-flight failure leaves an unrecorded outcome in either | **Not until Stage 3 + 5 exist** (default rule). Technically: same code | Fenced: **safe**. Concurrent: expected safe but not demonstrated in production |
| `a83d17e` | future release | Unknown until tested | Unknown until tested | Unknown until tested | Unknown until tested | **No** until all four are shown for that pair (and Stage 3 + 5 exist) | Fenced: **safe** if data format is compatible in both directions. Concurrent: **not established** |
| `a83d17e` | `8b59986` (rollback) | **Compatible** (old ignores and drops `version`/`wid`; new handles legacy records; tested) | **Incompatible** (as row 1) | **Incompatible** (as row 1) | **Incompatible** (as row 1) | **No** | Concurrent: **unsafe**. Fenced: **safe** (given Stage 0) |

## Releases that must never be deployed after `5ba9022` has written

| Release | Why |
|---|---|
| `1d544b7`, `5c21025` | They require `type(version) is int`; MongoDB returns server-computed versions as Int64, so they refuse every record `5ba9022` wrote (HTTP 500 on those features). It fails closed, but it's an outage. Tested: `test_rolling_back_to_the_pre_token_release_is_not_supported`. |
| `45e2fec`, `04cf85d`, `72a1681` | They predate versioned records, and would plain-save `templates`/`inbox_companies`/`ai_portals` (dropping `version`/`wid`). Fenced use would be data-compatible, but there is no reason to deploy them. Never run concurrently with anything. |

## Local developer processes

**Any release, run on a laptop against production, counts as a concurrent instance** of that release.
- today: the Mac's `.env` points at production with the production user;
- it must be stopped before any deploy;
- after the first fence, its credentials are fenced (no write privilege).
