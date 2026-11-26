# Error handling and recovery

**Status:** CURRENT IMPLEMENTATION, plus a PROPOSED DESIGN for agents · **Last reviewed:** 2026-11-26

## HTTP error handlers (`core/security.py`, `routes/site_pages.py`)

| Error | Response |
|---|---|
| `core.validation.Invalid` | JSON `{error, field}` with its status. Adds `Retry-After` when retryable: lock timeouts and `AccountBusy` return 409 with a 2 s retry (`infra/locks.py`); `VersionConflict` returns 409 |
| `core.crypto.Undecryptable` | 500, "Some saved data couldn't be read (the encryption key may have changed). Nothing was changed." |
| `PyMongoError` | 503, "The database is unavailable right now. Nothing was changed." |
| Any other `Exception` | HTTP exceptions pass through (as JSON under `/api/`). Anything else is logged with `logger.exception` (redacted) and returns a generic 500 |
| 413 | Upload too large (`MAX_CONTENT_LENGTH` = 3 × `MAX_FILE_MB`) |
| 404 | JSON for `/api/*`, otherwise `404.html` |

The browser reports its own crashes to `POST /api/client-errors` (rate-limited). Sentry captures server exceptions
when `SENTRY_DSN` is set, with PII off and redaction applied in `before_send`.

## Retries in the current code

| Where | Policy |
|---|---|
| Cloudinary upload | 3 attempts, linear backoff |
| User SMTP during a campaign | One reconnect on `SMTPServerDisconnected` |
| CLI model (JSON requested) | One re-ask for JSON only |
| `Workspace.update` | Up to 5 compare-and-set attempts with jittered backoff, then 409 **[a83d17e]** |
| Audit `seq` insert | Retried within a 10 s budget |
| Lock acquisition | Polls until its wait limit (18 s in requests, 45 s in background) **[a83d17e]** |
| Send queue | Items hitting the daily limit are re-queued for 09:30 the next day. Items stuck in `sending` for more than 600 s are marked failed and **not** re-sent, to avoid double sends |
| Company research | At most 3 attempts, 300 s apart |
| HTTP model providers, job-board fetches, web push | **No retries** |

## Failure isolation
- `bridge.every` catches every exception, so a loop never dies.
- `bridge.with_deadline` stops waiting for one slow account (the thread keeps running).
- The match chain catches errors per source; evaluation batches catch errors per job.
- Model-dependent steps fall back to deterministic output when the call fails or no model is configured.

## Recovery after a crash or restart
- **`8b59986` (production):** in-memory progress, locks and schedule state are lost on restart. Running campaigns and
  operations stop.
- **`a83d17e`:**
  - Leases expire after 30 s.
  - An operation whose lease is gone is recorded as `interrupted` the first time someone looks at it.
  - Recurring jobs resume at the next free slot.
  - External actions call `checkpoint()` before acting and record the outcome afterwards, under the account lock. An
    action that ran but whose record was lost in a crash is **outcome unknown**; there is no delivery state machine yet.

The case-by-case analysis is in [../deployment/DEPLOYMENT_FAILURE_ANALYSIS.md](../deployment/DEPLOYMENT_FAILURE_ANALYSIS.md).

## Design for agents (PROPOSED)
- **Task retries:** bounded attempts with backoff, then dead-letter. Retries resume from the last LangGraph
  checkpoint rather than starting over.
- **Model errors:** the gateway classifies them (`rate_limit`, `timeout`, `auth`, `server`, `invalid_output`),
  retries only the retryable classes, and may fall back to an approved alternative model config.
- **Tool errors:** returned to the agent as typed errors. External actions are never retried without their
  idempotency key, and never re-run after an *outcome unknown* result without asking the user.
- **Crash of a worker:** the task lease expires, another worker claims the task, and the run resumes from its
  checkpoint.
- **Every retry and error** produces a span ([../architecture/OBSERVABILITY_ARCHITECTURE.md](../architecture/OBSERVABILITY_ARCHITECTURE.md)).
