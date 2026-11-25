# Shared locks, progress and cancellation (Phase 0, Step 2)

Coordination that used to live in each process's memory now lives in MongoDB, so it holds across Gunicorn workers,
separate processes, restarts and a future worker service. Code: `infra/locks.py`, `infra/operations.py`,
`routes/operations.py`.

## What moved

| Before (per process) | Used for | Now |
|---|---|---|
| `workspace.LOCKS` (RLock per user) | `with ws.lock:` around read-modify-write of account data (≈90 call sites) | `locks.user_lock(uid)`: shared lease `u:<uid>:workspace`, re-entrant in a thread, threads of one process queue locally first. Saves made while holding it are transactions that re-check the lease (see below). |
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
| `bridge.every` loops, bounce watcher, WhatsApp-expiry loop, per-process `LAST`/`last`/`BOUNCE_CHECK_SOON` throttles | background jobs | shared schedule (`schedules`): one run per interval across all processes, never overlapping; per-account due times stored in MongoDB (see Recurring jobs) |

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
  expired. The record is created on first use. Each attempt waits at most 500 ms on a record that is in another
  holder's open transaction, and then counts as busy.
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
  - **Protected writes are transactions** (`locks.guarded_write`). This covers account data saved under `ws.lock`,
    and releasing a portfolio address. One MongoDB transaction:
    1. re-checks every lease the thread holds for that account on its lock record: our token, not expired by the
       server clock;
    2. touches that lock record;
    3. writes.
  - **A stale holder can never write:** if its lease expired or was taken over, the transaction aborts with
    `LeaseLost` and nothing is written. This holds however long it was paused, and whether or not the new holder has
    written yet.
  - **No takeover between check and commit:** step 2 writes the lock record, so a competing acquire waits for the
    transaction to finish, or is told "busy".
  - **Retries and limits:** transient conflicts are retried within `LOCK_TXN_SECONDS`. Any database failure raises
    `LockUnavailable` (503) and writes nothing.
  - **Replica set required:** Atlas always is one. Locally, `start.command` starts a one-node set and
    `tools/local_replset.py` initialises it. On a plain server, protected writes are refused, never done unguarded.
    Writes made outside `ws.lock` are plain writes, as before.
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

## Versioned records (optimistic concurrency)

**Which records:** those listed in `core.workspace.VERSIONED_KEYS`; currently `templates`, `inbox_companies` and
`ai_portals`. They are changed without the account lock.

**How a change is written** (`ws.update(key, fn, default)`):
1. Read the record, its `version` and its write token `wid`.
2. Compute `fn(fresh copy)`.
3. Write in one atomic operation (`update_one` with an aggregation pipeline). It matches only if the record is still
   exactly what was read, and sets:
   - the new value;
   - a new random 128-bit token (`secrets.token_hex(16)`);
   - `version = max(previous + 1, server time in ms)`, computed by MongoDB (`$max`, `$add`, `$toLong: "$$NOW"`).

   Every write therefore gets a strictly greater version, even if the server clock moves back.
4. If the record changed, re-read and compute again (up to 5 attempts, with jitter). After that, answer **409** with
   `Retry-After: 2`, having written nothing.

**What "still exactly what was read" means:**

| Record as read | The write must find |
|---|---|
| `version` + `wid` (normal) | the same `version` **and** the same `wid` |
| `version`, no `wid` (written by Stage 2A/2B before tokens existed) | the same `version`, no `wid`, **and the same stored bytes** |
| no `version`, no `wid` (written before versioning, or by an older release) | no `version`, no `wid`, **and the same stored bytes** |
| `wid` without `version`, a malformed `wid`, or a non-integer / negative / out-of-range `version` | nothing: refused (500) before anything is computed |

A stale writer's token never comes back, so a paused writer can't match again. That holds even if an old release removed
the fields, if versions ran ahead in a burst, or if the server clock moved back (the "ABA" problem). Records without a
token are compared on their stored bytes; the encryption makes those different on every write.

**Creation:** an upsert whose filter can't match an existing record. Two writers creating the record at once: one
inserts, the other gets a duplicate key and computes again.

**Limits:**
- **Version range:** BSON int64. A record whose version leaves no room for a greater one is refused, never wrapped.
- **Rules for `fn`:** it must only compute. Network calls, mail, notifications, other writes and taking the account
  lock all raise inside it.
- **Coverage:** `ws.save` refuses versioned keys, and two code-scan tests fail on any other write path. They cover
  literal names, computed `save` keys (only `contact:<id>` is allowed), dynamic `getattr` calls and raw access to the
  account-data collection.

**Converted so far:** `templates` (Stage 2A), then `inbox_companies` and `ai_portals` (Stage 2B).

**Old and new releases at once — still not safe.**
- **What the token fixes:** a *new* writer can no longer overwrite an old release's write with stale data.
- **What it can't fix:** an *old-release* writer doesn't check anything. Its whole-record replace can still overwrite
  a newer versioned change made between its read and its write.
- **Rule:** never let an old and a new release write the same database at the same time. Render's overlapping
  (zero-downtime) deploy must not be used for this change; deploy as below.

## Deploying versioned records (no overlap)

Do this for the first deploy of versioned records, and for any move forward after a rollback.

1. **Suspend** the Render service: Dashboard → service → *Suspend Service*. Netlify then gets errors from the API and
   the app shows "Can't reach the server"; plan a quiet time.
2. **Verify** that no old instance is still running:
   - Render shows the service *Suspended*, with no running instance under *Events*.
   - `curl -s -o /dev/null -w "%{http_code}" https://<service>.onrender.com/healthz` does not return 200.
   - In MongoDB, no lock lease or `loop:*` lease is being renewed: wait at least `LOCK_LEASE_SECONDS` (30 s).
3. **Deploy** the new commit, with the service still suspended (manual deploy of that commit).
4. **Resume** the service and check health:
   - `/healthz` returns 200;
   - signing in works;
   - a template save returns 200;
   - the saved record in MongoDB has an integer `version` ≥ the server time in ms, and a 32-character hex `wid`;
   - the Render logs show no `VersionCorrupt` or `ALERT` lines.
5. **Traffic** returns automatically with the resume; watch the logs for 10 minutes.

**Rollback:**
1. Suspend.
2. Verify as above.
3. Deploy the previous commit.
4. Resume.

The old release ignores `version` and `wid` and drops them on its next write of each record. Nothing needs migrating.
To move forward again later, use the same no-overlap procedure.

## Recurring jobs (`schedules` collection)
`schedules` holds one document per job (`g:<name>`) or per account and job (`u:<uid>:<name>`): `next_at`, `last_at`.

- **`schedule.claim(name, every)`:** atomically takes the current slot, only if `next_at` has passed by the server
  clock, and moves `next_at` on. Exactly one caller gets each slot.
- **`soon` / `postpone`:** move the next slot earlier or later.
- **`once`:** an "exactly once" marker (removed after a TTL), taken *before* an external action.

**`bridge.every(seconds, fn, name)`:**
- Every process wakes every `seconds`.
- A run needs both the `loop:<name>` lease (no overlap with a run still going anywhere) and the interval's slot (once
  per interval in total, not once per process).
- **Leadership:** there is no fixed leader. Whichever process gets the lease and the slot runs it. Its lease is
  renewed while it runs.
- **If the process running it dies:** the lease expires within `LOCK_LEASE_SECONDS`, and the next slot runs in any
  process.

| Job | Interval | Per-account coordination | Notes |
|---|---|---|---|
| send-queue | 20 s | each queued item claimed atomically | an item stuck mid-send is marked failed after 10 min, never resent |
| job-alert-scan | 10 min | slot `job-alert-scan` (55 min) + lease held by `scan_alerts` itself for the whole scan | a timed-out caller can't let a second scan run; only new jobs are announced |
| reply-watcher | 1 min | slot `reply-check` (170 s); a manual check postpones it | lease `replies-scan` held by the scan; replies de-duplicated |
| daily-sync | 1 min | `once("daily-sync:<date>")` + task lease `mail_sync` | the morning sync starts and announces once a day |
| learning-briefing | 5 min | task lease `briefing` + "today's briefing exists" | |
| ai-weekly-digest | 1 h | `once("weekly-digest:<week>")` taken before notifying | at most once a week, even across a crash |
| bounce-watcher | 30 s | slot `bounce-check` (10 min; `check_bounces_soon` after a send) + lease held by `check_bounces` itself | seen bounces de-duplicated |
| wa-expiry | 1 min | each deletion atomic (`find_one_and_delete` on the file id) | the owner is told once |

## Settings
| Variable | Default | Meaning |
|---|---|---|
| `LOCK_LEASE_SECONDS` | 30 | lease length |
| `LOCK_RENEW_SECONDS` | 10 | renewal interval (at most a third of the lease) |
| `LOCK_SAFETY_SECONDS` | 5 | a holder stops when less than this remains (at most a third of the lease) |
| `LOCK_TXN_SECONDS` | 10 | time limit for one protected write, retries included |
| `LOCK_WAIT_INTERACTIVE_SECONDS` | 18 | longest wait for the account lock in a web request; then 409 + `Retry-After: 2`, nothing read or changed |
| `LOCK_WAIT_BACKGROUND_SECONDS` | 45 | longest wait for the account lock in background work |
| `LOCK_SLOW_WAIT_SECONDS` / `LOCK_SLOW_HOLD_SECONDS` | 1 / 5 | waits and holds at least this long are logged on `inkwell.locks` (lock kind, operation, request id; never account data) |
| `OPERATIONS_KEEP_DAYS` | 7 | how long finished runs are kept |

None of these are required: the defaults suit Render.

## Compatibility and rollback
- **Additive:** three new collections (`locks`, `operations`, `schedules`) and their indexes. Account data documents
  are unchanged: the same full replace, just inside a transaction when made under the lock.
- **The database must be a replica set.** Atlas already is. A local MongoDB must be started with `--replSet rs0` and
  initialised once (`tools/local_replset.py`); the data folder is kept.
- **Rolling back:**
  - Redeploy `8b59986`.
  - Runs in flight at that moment stop. Their records stay as `running` until the TTL or a later release settles them.
  - The old code ignores both new collections.
  - Optionally drop `locks`, `operations` and `schedules` afterwards.
  - The old code runs fine against a replica set, so a local MongoDB can stay one.
- **Moving forward again after a rollback:** fences restart from the server clock, so they still increase.

## Known limitations
- **A holder frozen inside its transaction:** it keeps the lock record busy until its transaction ends. Others are
  told "busy" after their wait; they never hang. Its client gives up after `LOCK_TXN_SECONDS`, and MongoDB aborts any
  transaction after 60 s.
- **Cost:** a locked account operation takes 7 database operations, against 3 before:
  - acquire, the account check and the read, as before;
  - inside the transaction: the lease check, the write and the commit;
  - release.

  On a cluster with an operations-per-second cap, this lowers how many such operations fit per second.
  Measurements are in the Step 2 report; run `tools/bench_locks.py` where the app runs.
- **Writes to other records:** only writes routed through `guarded_write` are protected. That means account data
  saved under the lock, and the portfolio address release. Other records written while holding a lock (for example
  send logs and queue items) rely on their own atomic updates, as before.
- **Long single steps:** a slow mailbox or browser step can't be interrupted part-way, by design. Cancellation waits
  for the step to finish.
- **Settling dead runs:** a run whose process died is shown as `interrupted` once its lease expires (up to
  `LOCK_LEASE_SECONDS`). A new run of the same kind can start only after that.
