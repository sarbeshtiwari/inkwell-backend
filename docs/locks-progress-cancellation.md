# Shared locks, progress and cancellation (Phase 0, Step 2)

Coordination that used to live in each process's memory now lives in MongoDB, so it holds across Gunicorn workers,
separate processes, restarts and a future worker service. Code: `infra/locks.py`, `infra/operations.py`,
`routes/operations.py`.

## What moved

| Before (per process) | Used for | Now |
|---|---|---|
| `workspace.LOCKS` (RLock per user) | `with ws.lock:` around read-modify-write of account data (≈90 call sites) | `locks.user_lock(uid)`: shared lease `u:<uid>:workspace`, re-entrant in a thread, threads of one process queue locally first. Saves made while holding it are fenced. |
| `sending.JOBS`, `JOBS_LOCK`, `Job.stop` | one campaign per account, its live log and progress, Stop | operation `send`; Stop = cancel request; the loop checks between people and while waiting |
| `inbox.LOCK`, `inbox.PROGRESS` | one inbox sync per account, its progress | operation `inbox` |
| `apps.mailbox.SYNC_LOCK`, `PROGRESS` | application sync | operation `apps_sync` |
| `apps.sync.MAIL_SYNC` | combined mail sync | operation `mail_sync` (cancels between steps) |
| `replies.LOCKS` | one reply scan per account | lease `replies-scan` |
| `nearby.CHECKS` | company checks (4 threads) | operation `nearby` (atomic `$inc` for done) |
| `ai_jobs.EVALUATING`, `RUNS`, `APPLYING` | batch evaluation, AI match, auto-apply | operations `ai_evaluate`, `ai_match`, `ai_apply` |
| `finder.RUNS`, `RUNS_LOCK`, `Run.stop` | website finder | operation `finder` (results so far, encrypted) |
| `briefing.BUILDING` | one briefing build per account | operation `briefing` |
| `research.RUNNING` | one research per company | operation `research`, key = company id |
| `whatsapp_store.WA_LINKS`, `BUSY` | QR linking, one WhatsApp browser per account | operation `wa_link` (QR image encrypted, erased at the end); lease `wa-browser` |
| `bridge.every` loops, bounce watcher | background ticks | each tick holds `g:loop:<name>`; bounce checks hold `bounce-check` per account |

Deliberately still per process (not coordination state):
- **Machine-local limits:** `SLOTS` (browsers on this machine) and `APPLY_LOCK` (one apply browser on this machine).
- **Things that only exist in that process:** `LIVE` (browser screenshots for the live view) and `HANDOVER` (a browser
  window on the user's own computer). Storing screenshots of application forms in the database would keep personal
  data for no reason.
- **Caches and throttles:** `SENT_CACHE`, `GH_CACHE`, `STREAMS`, `LAST`, `BOUNCE_CHECK_SOON`, `DELETED`.
  `Workspace.save` still checks the user exists, so a deletion is seen everywhere.

## Locks (`locks` collection)
- `_id` is `u:<uid>:<resource>` or `g:<resource>`. Other fields: `token`, `fence`, `owner` (host:pid:thread),
  `expires_at`, `acquired_at`, `renewed_at`, `released_at`.
- **Acquire:** one conditional update, judged by the server clock (`$$NOW`). It succeeds only if the lease is free or
  expired. The record is created on first use.
- **Fence:** never decreases. Each acquisition sets it to `max(previous + 1, server time in ms)`, so it can't restart
  low even if records are removed.
- **Renew:** every `LOCK_RENEW_SECONDS`, and only while the lease is still ours and unexpired. Losing it marks the
  lease lost.
- **Release:** owner-only (matches on token) and idempotent. The record stays.
- **Crash:** the lease simply expires and the next acquirer takes over. TTL deletion is never involved.
- **Database down:** acquiring raises `LockUnavailable` (503). There is no local fallback.
- **Stale holders:**
  - `ensure()` stops a holder once less than `min(LOCK_SAFETY_SECONDS, lease/3)` remains. The check uses the local
    monotonic clock, measured from before the request that set the lease.
  - Account writes made under `ws.lock` carry the fence. A document already written by a newer holder rejects the
    write, and the stale holder gets `LeaseLost`.
- **Indexes:** `_id`, plus `(uid, resource)`.

## Operations (`operations` collection)
- **Fields:** `{_id, uid, kind, key, state, public, private (encrypted), version, lock_token, fence, cancel_requested,
  error, started_at, updated_at, finished_at, expire_at}`.
- **One running at a time:** `start()` takes the lease `op:<kind>[:<key>]` for the user, so a second start from any
  process gets 409. Any record still marked running for that slot is from a dead process and is marked `interrupted`.
- **States:** `running` → `done` | `failed` | `cancelled` | `interrupted`. A run that has finished never changes.
- **Updates:**
  - Every update matches the run id, `state: running` and the lease token, and the lease must still be valid.
  - Updates from a superseded or lease-less run are refused (`LeaseLost`).
  - `version` increases on every write, and counters use `$inc`.
- **What's stored:** `public` holds counts, stages and timings only. Names, log lines, results and the QR image go in
  `private`, encrypted.
- **Reading:**
  - Endpoints show only the signed-in user's runs.
  - A running record whose lease is gone is reported, and recorded, as `interrupted` with its last progress.
  - Response shapes are unchanged: `/api/job`, `/api/inbox/progress`, `/api/apps/progress`, `/api/mail-sync`, and the
    `checking`, `evaluating`, `applying`, AI-match, finder and WhatsApp states.
- **Retention:** finished runs get `expire_at` (`OPERATIONS_KEEP_DAYS`, default 7), and a TTL index removes them.
  This is clean-up only. Deleting an account also deletes its operations and locks.
- **Indexes:**
  - `(uid, kind, key, fence desc)`
  - `(state, updated_at)`
  - `expire_at` (TTL)

## Cancellation
- **Requesting:**
  - `POST /api/operations/<kind>/cancel` (own runs).
  - The existing Stop buttons: `/api/job/stop`, `/api/finder/stop`, `/api/whatsapp/cancel`.
  - `POST /api/admin/operations/<uid>/<kind>/cancel` for a Super Admin. It needs `admin.control_agents` and a reason,
    writes an audit event before acting, and returns metadata only.
- **Effect:**
  - Sets `cancel_requested` on the running record. It is idempotent, and a finished run is left as it is.
  - If the database is down, the request fails with 503. Nothing pretends to have stopped.
- **Where work stops:** at safe points only.
  - Between people in a campaign, and while waiting between them.
  - Between mail batches or sync steps, before the sync position is saved.
  - Between companies, jobs and websites.
  - Before each application is submitted.
- **What happens to finished work:** a unit that has started finishes and is recorded, and nothing is killed.
- **Outcome:** the run is recorded as `cancelled` and its lease is released.
- **External actions:**
  - A campaign checks its lease right before each send, and an apply run checks for cancellation right before each
    submission.
  - Records of actions that did happen are written under the account lock, not the run's lease, so they are never
    lost.
  - Nothing is ever retried because of cancellation.
- **Not cancellable part-way:** a single AI-match search and a single company research. They check before starting
  and before saving.

## Settings
| Variable | Default | Meaning |
|---|---|---|
| `LOCK_LEASE_SECONDS` | 30 | lease length |
| `LOCK_RENEW_SECONDS` | 10 | renewal interval (at most a third of the lease) |
| `LOCK_SAFETY_SECONDS` | 5 | a holder stops when less than this remains (at most a third of the lease) |
| `OPERATIONS_KEEP_DAYS` | 7 | how long finished runs are kept |

None of these are required: the defaults suit Render.

## Compatibility and rollback
- **Additive:** two new collections (`locks`, `operations`) and their indexes. Account data documents (`kv`) gain a
  small `fences` field.
- **Account saves:** they are now `$set` upserts instead of full replaces. The previous release reads the same
  documents, and its full replace simply drops `fences`.
- **Rolling back:**
  - Redeploy `8b59986`.
  - Runs in flight at that moment stop. Their records stay as `running` until the TTL or a later release settles them.
  - The old code ignores both new collections.
  - Optionally drop `locks` and `operations` afterwards.
- **Moving forward again after a rollback:** fences restart from the server clock, so they still increase.

## Known limitations
- **Paused writer:** a process paused for longer than the safety margin between its lease check and its write can
  still win if it writes a document the new holder hasn't written yet. Fencing covers documents the new holder has
  written. Closing the gap fully needs multi-document transactions, and Atlas supports them.
- **Lock latency:** each `with ws.lock:` costs about 2 extra database round trips (acquire and release). Measured
  locally: 0.34 ms extra per locked save, about 4 local round trips. On Atlas this is a few ms to tens of ms per
  locked write.
- **Long single steps:** a slow mailbox or browser step can't be interrupted part-way, by design. Cancellation waits
  for the step to finish.
- **Settling dead runs:** a run whose process died is shown as `interrupted` once its lease expires (up to
  `LOCK_LEASE_SECONDS`). A new run of the same kind can start only after that.
