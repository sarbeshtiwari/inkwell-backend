# Deployment write-path inventory

This inventory feeds `DEPLOYMENT_GATE_ARCHITECTURE.md`. The runbook's external-operation preconditions and its
unknown-outcome checks (step 1.10) come from sections 3, 3a and 3b together:
- §3 lists the effects;
- §3a gives the order of write and provider call per handler;
- §3b covers the tasks still running after ingress closes.

**Scope:** every place the backend writes to MongoDB or causes an external side effect.

| Release | Notes |
|---|---|
| `a83d17e` | The code this inventory was generated from. Runtime code is identical to `5ba9022`: `5ba9022..a83d17e` changes only `docs/` and `tests/`. |
| `8b59986` | The deployed release. Same write sites, minus the Step 2 additions (`locks`, `operations`, `schedules`), and with no account-lock lease, no transactions and no versioning (see the compatibility matrix). |

**Method:**
- **Static analysis** (Python `ast`) of every module except `tests/`, `tools/` and `features/learning/content` (static text).
- **Endpoints:** follows calls up to the helpers they use.
- **Background work:** listed by hand from `app.py` and `bridge.every`, because work started with `threading.Thread` / `operations.spawn` is passed as a function object and the static analysis doesn't follow it.

**Totals:**
- 142 MongoDB write calls in 26 collections.
- 180 HTTP endpoints that write or cause external effects.
- 8 recurring background jobs, plus the per-task worker threads.

## 1. Startup and initialisation (run at import, before any request)

| Where | Writes | Control |
|---|---|---|
| `app.py` → `core/accounts.py:init_storage` | `create_index` on users, otps, kv, send_log, documents, leads, sessions, site_images, site_stats, ratelimits, queue; `locks`/`operations`/`schedules` indexes; `audit.ensure_indexes` (audit_meta head insert, `repair_head` updates); `rbac.ensure_indexes`; **`rbac.bootstrap()` → `reconcile()`: role grants/revocations + audit events + outbox flush**; SQLite migration if the users collection is empty | Idempotent, except `reconcile` (writes roles + audit). |
| `features/notify.py:init`, `features/inbox.py:ensure_indexes`, `features/replies.py:init`, `features/jobs.py:start_workers`, `features/notify.py:vapid` (first push) | Indexes; VAPID keypair `site` record | Idempotent / insert-if-missing. |

**Gate consequence:** any instance that imports `app` writes before serving traffic, `8b59986` included. "Don't route traffic to it" doesn't stop those writes.

**With a fenced user:**
- In `8b59986`, `app.py` calls `init_storage()` at import. Only `M.connect()` (a `ping`) is inside a `try`. The
  following `create_index` calls are not.
- With a fenced user, the first `create_index` is therefore **expected** to raise `OperationFailure` (`Unauthorized`).
  That fails the import before the background loops are started, and gunicorn reports that the worker failed to boot.
- The resumed release in runbook step 1.7.1 is thus expected **not** to boot or pass its health check.
- Not verified:
  - that `createIndexes` on an existing index is refused (rather than a no-op) on this Atlas tier;
  - what Render then does.

  Both are runbook Stage 0.6.
- `a83d17e` also creates indexes at import, so a fenced `a83d17e` is expected to behave the same.

## 2. Background work (`app.py` when `BOUNCE_WATCH=1`, the default)

| Job | Interval | What it writes / sends | Control in `a83d17e` | In `8b59986` |
|---|---|---|---|---|
| `send-queue` (`features/jobs.py:run_queue`) | 20 s | claims queue items, **sends email (SMTP)**, `send_log`, `recipients` status, notifications | atomic claim; schedule slot + loop lease | atomic claim (the same), no loop lease |
| `job-alert-scan` (`auto_scan_all` → `scan_alerts`) | 10 min | IMAP read; `jobs`, `jobs_seen_mail`, settings; notification | slot + per-account lease held by the scan | none (per-process `last`) |
| `reply-watcher` (`replies.tick` → `scan`) | 1 min | IMAP read; `replies` insert, `send_log` mark, contact events, notifications | slot `reply-check` + `replies-scan` lease | per-process lock + `LAST` dict |
| `daily-sync` (`apps.sync.daily_tick` → `sync_all`) | 1 min | `apps_sync` marker; spawns the morning mail sync (replies, applications, inbox, job alerts, bounces) | `once(daily-sync:<date>)` + task lease | `last_auto` marker only |
| `learning-briefing` (`briefing.daily_tick` → `build`) | 5 min | `briefings`; notification | task lease + "exists today" | in-memory set |
| `ai-weekly-digest` (`weekly_digest_all`) | 1 h (Mondays) | notification; `ai_digest` | `once(weekly-digest:<week>)` before notifying | `ai_digest` marker after notifying |
| `bounce-watcher` (`core/bounces.py:bounce_tick`) | 30 s | IMAP read; `send_log` bounce marks, `recipients` status, notification | slot `bounce-check` + lease held by the check | own thread, per-process dicts |
| `wa-expiry` (`expire_wa_sessions`) | 1 min | `wa_sessions` delete (atomic), settings, notification | `find_one_and_delete` on the file id | the same |
| `lock-renewer` (`infra/locks.py`) | ~1 s | lease renewals | lease protocol | absent |
| Task workers (`operations.spawn`) | per task | campaign (**SMTP / WhatsApp sends**), inbox, application and mail sync, nearby checks, AI evaluate/match/**apply (job-site submissions)**, finder, briefing, research, WhatsApp link | task lease; cooperative cancel | plain threads, in-memory flags |

## 3. External side effects

| Effect | Code | Reached from | Reversible? |
|---|---|---|---|
| Campaign email | `core/sending.py:_run_job` → `smtp.send_message` | `POST /api/send` (user starts it) | No |
| Campaign WhatsApp | `core/sending.py:_run_job` → `whatsapp.send_one` | same; **off on hosted** (`WHATSAPP_ENABLED=0`) | No |
| Scheduled/queued email | `features/jobs.py:send_queued` | `send-queue` loop; `POST /api/queue/email`, `/api/queue/<id>/now` | No |
| Reply email | `features/replies.py:send_reply` | `POST /api/replies/<key>/send` | No |
| OTP / login / lead emails | `routes/auth.py`, `core/mail.py` (`SMTP_*`) | auth endpoints, website contact form | No |
| Job application submission | `features/ai_jobs/apply.py:apply_one`, `watch.py:watch_apply` | `POST /api/ai-jobs/apply`, `/finish/<jid>` | No |
| LinkedIn post | `features/linkedin.py:publish` | `POST /api/li/post` | No (can be deleted manually) |
| GitHub writes | `features/github.py` (commit, branch, fork, issue, PR, merge, comment), `portfolio.publish` | `/api/gh/*`, `/api/portfolio/publish` | No |
| Web push / in-app notifications | `features/notify.py:notify` → `push_all` | everywhere | No (cosmetic) |
| Cloudinary uploads/deletes | `core/storage.py:put_file/drop_file` | documents, site images, account deletion | Partly |
| Reads with side effects on others | IMAP login/search (read-only), job-board and website fetches, LLM calls | sync, finder, AI features | No harm if repeated; costs only |

**Human approval today:**
- Every email and WhatsApp campaign, reply, queued email and application submission starts from an explicit user action in the UI. Campaign = start button; application = picks + consent setting; queued email = scheduled by the user.
- The `send-queue` loop only sends items the user queued.
- No agent or automation sends anything on its own. Weekly digests and notifications are in-app.

## 3a. Order of database write and provider call in `8b59986` (fencing alone vs closing ingress)

Audited on `git archive 8b59986`.
- **Before any handler:** a signed-in request writes to the database first only if its session was last touched > 1 h
  ago, so usually it doesn't.
- **Handler order** decides whether revoking the database user stops the external action:

| Handler (8b59986) | First action | Does database fencing alone prevent the provider call? |
|---|---|---|
| `routes/send.py:start_send` (campaign) | `ws.update_settings` (write), then thread | Yes |
| `features/replies.py:send_reply` | `rate_limited` (write), then SMTP | Yes |
| `features/jobs.py:queue_email`, `send_now`; send-queue loop (`email`, `linkedin_post`) | `ws.save` / queue claim (write), then provider | Yes |
| `features/ai_jobs/routes.py:apply`, `finish` | `rate_limited` (write), then thread | Yes |
| `features/linkedin.py:post_now` | `rate_limited` (write), then LinkedIn | Yes |
| `routes/auth.py` OTP/login emails, `routes/leads.py:create_lead`, `features/site.py:visitor_contact` | `rate_limited` (write), then SMTP | Yes |
| `features/github.py`: `create_repo`, `commit`, `create_branch`, `create_pull`, `merge_pull`, `close_pull`, `fork`, `comment`, `create_issue`, `markdown`, `connect` | **GitHub API first** | **No: closing ingress is required** |
| `features/portfolio.py:publish` | **GitHub API first** | **No** |
| `features/site.py:upload_image`; document uploads (`Workspace.save_doc`) | **Cloudinary upload first** | **No** |
| Already-running campaign / apply / sync threads | provider call, then write | **No**: they keep running after ingress closes and can't be observed as finished (§3b); only stopping the process ends them |
| Send-queue item **already claimed** (`status: sending`) before the fence | provider call (the claim write is already done) | **No**: the call needs no further write; only stopping the process (or nothing being claimed) prevents it |

"Yes" in this table means only that the fence stops a **new** call through that handler. It never stops a call that
has already passed its first write.

So the runbook **closes HTTP ingress (rotates the Netlify proxy secret) before fencing**. The backend then refuses
every request before any handler runs, in every release (`core/security.py:only_via_netlify`).

**Closing ingress doesn't stop requests already accepted or threads already running.** See §3b.

## 3b. Background tasks that can cause an irreversible external action (`8b59986`)

**Read-only provider calls** (IMAP search/fetch, job-board and website fetches, LLM calls, GitHub GETs) aren't listed:
repeating or interrupting them changes nothing outside Inkwell. **Irreversible actions:** sending email or WhatsApp,
submitting an application, publishing to LinkedIn, writing to GitHub, uploading or deleting on Cloudinary, web push.

| Task (8b59986) | Started by | Started by a request accepted before ingress closed? | Spawns others? | Calls the provider before its next database write? | How an operator can tell it has finished (8b59986) |
|---|---|---|---|---|---|
| Campaign (`core/sending.py:run_job`, `threading.Thread`) | `POST /api/send` | **Yes**: the handler returns at once; the thread runs on | No | **Yes**: per person it sends (SMTP/WhatsApp), *then* `set_status` and `log`. Between people it waits `min_delay`–`max_delay` (each 0–3600 s, user-set) | Only indirectly: the campaign's status in memory (`/api/job`, unreachable once ingress is closed) or the send log stopping. **No positive proof from outside the process.** |
| Auto-apply (`features/ai_jobs/routes.py:run_apply`, thread) | `POST /api/ai-jobs/apply`, `/finish/<jid>` | **Yes** | No | **Yes**: opens the job site and submits (`apply_one`), *then* saves `ai_applied`. No overall deadline | Only indirectly (`ai_applied` entries stop appearing). **No positive proof.** |
| Send queue (`features/jobs.py:run_queue`, `bridge.every(20)`) | its loop | No (loop) | No | No: claims (`status: sending`, a write) **before** the SMTP / LinkedIn call, then writes the result | `queue` has no item `status: sending` and none `queued` that is due. Proof only for that moment: the loop may claim the next due item at any time. |
| Reply send (`features/replies.py:send_reply`) | `POST /api/replies/<key>/send` | **Yes**, if accepted just before the close (runs inside the request) | No | **No** (rate-limit write first), but after it the SMTP call happens inside the request | The request finishes (bounded only by SMTP timeouts, see below) |
| LinkedIn post-now (`features/linkedin.py:post_now`) | HTTP | Yes, inside the request | No | No (rate-limit write first) | Request ends |
| GitHub writes (`features/github.py`, `portfolio.publish`) | HTTP | Yes, inside the request | No | **Yes** (GitHub first) | Request ends |
| Cloudinary upload (`site.upload_image`, `save_doc`) | HTTP | Yes, inside the request | No | **Yes** (upload first) | Request ends |
| Morning mail sync (`apps.sync.daily_tick` → thread) | `daily-sync` loop | No | Thread per account | Read-only IMAP; notifications only | No external irreversible action |
| Inbox / application / bounce / reply scans, job-alert scan | loops and `/api/*/sync` | Yes for `/sync` | Threads | Read-only IMAP; in-app notifications and web push (irreversible but cosmetic) | Not observable from outside |
| Weekly digest, briefing | loops | No | Thread | In-app notification / push only | Not observable |
| WhatsApp linking | `POST /api/whatsapp/link` | Yes | Thread | WhatsApp Web session (off in production: `WHATSAPP_ENABLED=0`) | n/a in production |

**Bounds on how long an accepted HTTP request can run in `8b59986`:**
- **Gunicorn:** `--timeout 120` is a worker *heartbeat* timeout. With the threaded worker (`--threads 16`), the worker
  process keeps heartbeating while one request thread runs for any length of time, so **it doesn't limit a single
  request's duration**.
- **No request timeout is configured** anywhere in the release, at gunicorn or app level.
- **Provider calls have their own timeouts in places:** `bridge.http` uses 30 s by default, and SMTP/IMAP use
  `smtplib`/`imaplib` connection timeouts where set. These aren't an overall per-request bound.
- **Conclusion:** the maximum request duration **can't be established** from the deployed release.

**Bounds on background threads:**
- they are daemon threads, ended only when the process exits;
- `with_deadline` stops *waiting* after 240 s but **doesn't stop the thread**.

**Conclusion:** in `8b59986`, **no waiting period proves quiescence**, and the release exposes no task registry. The
only positive evidence that no task is still running is that **the process has stopped**.
- **What counts as evidence of that:** only *termination evidence*: a post-exit record that passed runbook Stage 0.5's
  acceptance conditions (provenance, termination positive control, negative controls, agreement with a fake
  provider's log). *Liveness observations* never qualify. **None is known today**, so this is a blocker (Stage
  0.5).
- **What doesn't count:** Render's *Suspended* status, the absence of any liveness observation (`/healthz` failing, a
  quiet log, **zero database connections**), or elapsed time. With `pymongo==4.18.2` and `minPoolSize` 0, monitoring connections are unauthenticated and
  pools are cleared on network errors, so a live campaign thread can sit in its delay with no authenticated
  connection, then send.
- **The database fence doesn't substitute for it.** The campaign, auto-apply and claimed send-queue rows above call
  their provider before their next write, so a live thread is not stopped by a fence.


## 4. HTTP endpoints that write or cause external effects

Generated table:
- "Account-data writes" means `Workspace` methods (`kv` records, send log).
- "Concurrency control" is the strongest control found on the path. "Atomic single-document ops only" means each write is atomic on its own, with no cross-request coordination.
- Static analysis: work handed to background threads is listed in section 2, not here.

| Method | Path | Handler | MongoDB collections written | Account-data writes | External effects | Concurrency control |
|---|---|---|---|---|---|---|
| POST | `/api/ai-jobs/answers` | `features/ai_jobs/answers.py:add_answer` | kv, locks, notifications, sessions | save | - | account lock |
| PUT | `/api/ai-jobs/answers/<aid>` | `features/ai_jobs/answers.py:edit_answer` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/ai-jobs/answers/approve-all` | `features/ai_jobs/answers.py:approve_all` | kv, locks, notifications, sessions | save | - | account lock |
| DELETE | `/api/ai-jobs/answers/<aid>` | `features/ai_jobs/answers.py:delete_answer` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/ai-jobs/evaluate/<jid>` | `features/ai_jobs/evaluate.py:evaluate_one` | kv, locks, notifications, ratelimits, sessions | save | - | account lock |
| POST | `/api/ai-jobs/evaluate` | `features/ai_jobs/evaluate.py:evaluate_many` | operations, ratelimits | - | - | task lease |
| POST | `/api/ai-jobs/cover/<jid>` | `features/ai_jobs/evaluate.py:make_cover` | kv, locks, notifications, ratelimits, sessions | save | - | account lock |
| PUT | `/api/ai-jobs/cover/<jid>` | `features/ai_jobs/evaluate.py:save_cover` | documents, files, kv, locks, notifications, sessions | save, save_doc | file storage | account lock |
| POST | `/api/ai-jobs/resume/<jid>` | `features/ai_jobs/extras.py:make_resume` | documents, files, kv, locks, notifications, ratelimits, sessions | save, save_doc | file storage | account lock |
| PUT | `/api/ai-jobs/resume/<jid>` | `features/ai_jobs/extras.py:use_resume` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/ai-jobs/archive/<jid>` | `features/ai_jobs/extras.py:archive_posting` | documents, files | save_doc | file storage | atomic single-document ops only |
| POST | `/api/ai-jobs/stale` | `features/ai_jobs/extras.py:close_stale` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/ai-jobs/prep/<jid>` | `features/ai_jobs/extras.py:make_prep` | kv, locks, notifications, ratelimits, sessions | save | - | account lock |
| POST | `/api/ai-jobs/feedback/<jid>` | `features/ai_jobs/learning.py:give_feedback` | kv, locks, notifications, send_log, sessions | save | - | account lock |
| GET | `/api/ai-jobs/learning` | `features/ai_jobs/learning.py:get_learning` | kv, send_log | - | - | atomic single-document ops only |
| POST | `/api/ai-jobs/learning/suggestion/<path:sid>` | `features/ai_jobs/learning.py:decide_suggestion` | kv, locks, notifications, send_log, sessions | save | - | account lock |
| POST | `/api/ai-jobs/learning/reset` | `features/ai_jobs/learning.py:reset_learning` | kv, locks, notifications, send_log, sessions | save | - | account lock |
| PUT | `/api/ai-jobs/model` | `features/ai_jobs/models.py:put_model` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/ai-jobs/model/test` | `features/ai_jobs/models.py:test_model` | ratelimits | - | - | atomic single-document ops only |
| POST | `/api/ai-jobs/portals` | `features/ai_jobs/portals.py:add_portal` | ratelimits | update | - | versioned CAS |
| DELETE | `/api/ai-jobs/portals/<ats>/<path:slug>` | `features/ai_jobs/portals.py:remove_portal` | - | update | - | versioned CAS |
| POST | `/api/ai-jobs/apply` | `features/ai_jobs/routes.py:apply` | operations, ratelimits | - | - | task lease |
| GET | `/api/ai-jobs/live` | `features/ai_jobs/routes.py:live_view` | operations | - | - | atomic single-document ops only |
| POST | `/api/ai-jobs/finish/<jid>` | `features/ai_jobs/routes.py:finish` | operations | - | - | task lease |
| GET | `/api/ai-jobs` | `features/ai_jobs/runs.py:status` | kv, operations, send_log | - | - | atomic single-document ops only |
| POST | `/api/ai-jobs/match` | `features/ai_jobs/runs.py:start` | operations, ratelimits | - | - | task lease |
| PUT | `/api/ai-jobs/keys` | `features/ai_jobs/runs.py:save_keys` | kv, locks, notifications, sessions | save | - | account lock |
| PUT | `/api/ai-jobs/details` | `features/ai_jobs/runs.py:save_details` | kv, locks, notifications, sessions | save, update_settings | - | account lock |
| PUT | `/api/ai-jobs/status/<jid>` | `features/ai_jobs/tracker.py:put_status` | kv, locks, notifications, sessions | save | - | account lock |
| PUT | `/api/ai-jobs/rules` | `features/ai_jobs/tracker.py:put_rules` | kv, locks, notifications, sessions | save | - | atomic single-document ops only |
| POST | `/api/ai-jobs/followups/<aid>` | `features/ai_jobs/tracker.py:mark_followup` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/naukri/connect` | `features/apps/naukri.py:naukri_connect` | kv, locks, notifications, operations, sessions | save | mailbox read | atomic single-document ops only |
| POST | `/api/naukri/disconnect` | `features/apps/naukri.py:naukri_disconnect` | kv, locks, notifications, sessions | save | - | atomic single-document ops only |
| GET | `/api/overview` | `features/apps/overview.py:overview` | operations | - | GitHub API, external API | atomic single-document ops only |
| POST | `/api/apps/sync` | `features/apps/routes.py:sync_now` | operations | - | - | task lease |
| PUT | `/api/apps/sync-prefs` | `features/apps/routes.py:save_sync_prefs` | kv, locks, notifications, sessions | save | - | atomic single-document ops only |
| PUT | `/api/apps/<aid>` | `features/apps/routes.py:edit_app` | kv, locks, notifications, sessions | save | - | account lock |
| DELETE | `/api/apps/<aid>` | `features/apps/routes.py:delete_app` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/apps` | `features/apps/routes.py:add_app` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/mail-sync` | `features/apps/routes.py:mail_sync_now` | kv, locks, notifications, operations, send_log, sessions | add_event, save, set_status, update_settings | mailbox read, notification | account lock, task lease |
| POST | `/api/extension/key` | `features/extension.py:new_key` | kv, locks, notifications, sessions | save, update_settings | - | account lock |
| DELETE | `/api/extension/key` | `features/extension.py:revoke_key` | kv, locks, notifications, sessions | save, update_settings | - | account lock |
| POST | `/api/ext/autofill` | `features/extension.py:autofill` | kv, locks, notifications, ratelimits, sessions | save, update_settings | - | account lock |
| GET | `/api/ext/resume` | `features/extension.py:resume` | ratelimits | - | - | atomic single-document ops only |
| GET | `/api/ext/cover` | `features/extension.py:cover` | ratelimits | - | - | atomic single-document ops only |
| POST | `/api/ext/learn` | `features/extension.py:learn` | kv, locks, notifications, ratelimits, sessions | save | - | account lock |
| POST | `/api/finder/scan` | `features/finder.py:start_scan` | operations, ratelimits | - | - | task lease |
| POST | `/api/finder/stop` | `features/finder.py:stop` | operations | - | - | atomic single-document ops only |
| POST | `/api/finder/save` | `features/finder.py:save_contacts` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/gh/connect` | `features/github.py:connect` | kv, locks, notifications, sessions | save | external API | atomic single-document ops only |
| POST | `/api/gh/disconnect` | `features/github.py:disconnect` | kv, locks, notifications, sessions | save | - | atomic single-document ops only |
| GET | `/api/gh/repos` | `features/github.py:repos` | - | - | GitHub API, external API | - |
| POST | `/api/gh/repos` | `features/github.py:create_repo` | - | - | GitHub API, external API | - |
| GET | `/api/gh/repo/<owner>/<repo>` | `features/github.py:repo_info` | - | - | GitHub API, external API | - |
| GET | `/api/gh/tree/<owner>/<repo>` | `features/github.py:tree` | - | - | GitHub API, external API | - |
| GET | `/api/gh/file/<owner>/<repo>` | `features/github.py:read_file` | - | - | GitHub API, external API | - |
| POST | `/api/gh/commit/<owner>/<repo>` | `features/github.py:commit` | - | - | GitHub API, external API | - |
| POST | `/api/gh/branch/<owner>/<repo>` | `features/github.py:create_branch` | - | - | GitHub API, external API | - |
| GET | `/api/gh/commits/<owner>/<repo>` | `features/github.py:commits` | - | - | GitHub API, external API | - |
| GET | `/api/gh/commit/<owner>/<repo>/<sha>` | `features/github.py:commit_detail` | - | - | GitHub API, external API | - |
| GET | `/api/gh/pulls/<owner>/<repo>` | `features/github.py:pulls` | - | - | GitHub API, external API | - |
| GET | `/api/gh/pull/<owner>/<repo>/<int:number>` | `features/github.py:pull_detail` | - | - | GitHub API, external API | - |
| POST | `/api/gh/pulls/<owner>/<repo>` | `features/github.py:create_pull` | - | - | GitHub API, external API | - |
| POST | `/api/gh/pull/<owner>/<repo>/<int:number>/merge` | `features/github.py:merge_pull` | - | - | GitHub API, external API | - |
| POST | `/api/gh/pull/<owner>/<repo>/<int:number>/close` | `features/github.py:close_pull` | - | - | GitHub API, external API | - |
| POST | `/api/gh/markdown` | `features/github.py:markdown` | - | - | external API | - |
| POST | `/api/gh/fork/<owner>/<repo>` | `features/github.py:fork` | - | - | GitHub API, external API | - |
| GET | `/api/gh/issue/<owner>/<repo>/<int:number>` | `features/github.py:issue` | - | - | GitHub API, external API | - |
| POST | `/api/gh/issue/<owner>/<repo>/<int:number>/comments` | `features/github.py:comment` | - | - | GitHub API, external API | - |
| GET | `/api/gh/issues/<owner>/<repo>` | `features/github.py:list_issues` | - | - | GitHub API, external API | - |
| POST | `/api/gh/issues/<owner>/<repo>` | `features/github.py:create_issue` | - | - | GitHub API, external API | - |
| PUT | `/api/inbox/company/<key>` | `features/inbox.py:edit_company` | - | update | - | versioned CAS |
| POST | `/api/inbox/sync` | `features/inbox.py:sync` | kv, locks, mail_index, notifications, operations, sessions | save | mailbox read | task lease |
| GET | `/api/jobs` | `features/jobs.py:list_jobs` | kv, locks, notifications, sessions | save | - | atomic single-document ops only |
| POST | `/api/jobs/scan` | `features/jobs.py:jobs_scan` | kv, locks, notifications, ratelimits, sessions | save, update_settings | mailbox read | account lock |
| PUT | `/api/jobs/prefs` | `features/jobs.py:save_prefs` | kv, locks, notifications, sessions | save | - | atomic single-document ops only |
| POST | `/api/jobs/prefs/reset` | `features/jobs.py:reset_prefs` | kv, locks, notifications, sessions | save | - | atomic single-document ops only |
| PUT | `/api/jobs/<jid>` | `features/jobs.py:set_job_state` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/posts/parse` | `features/jobs.py:posts_parse` | kv, locks, notifications, sessions | save | - | atomic single-document ops only |
| POST | `/api/posts/draft` | `features/jobs.py:posts_redraft` | kv, locks, notifications, sessions | save | - | atomic single-document ops only |
| GET | `/api/posts/template` | `features/jobs.py:get_post_template` | kv, locks, notifications, sessions | save | - | atomic single-document ops only |
| PUT | `/api/posts/template` | `features/jobs.py:set_post_template` | kv, locks, notifications, sessions | save | - | atomic single-document ops only |
| POST | `/api/queue/email` | `features/jobs.py:queue_email` | kv, locks, notifications, queue, sessions | add_event, save | - | account lock |
| PUT | `/api/queue/<qid>` | `features/jobs.py:edit_queue_item` | queue | - | - | atomic single-document ops only |
| POST | `/api/queue/<qid>/cancel` | `features/jobs.py:cancel_queue_item` | queue | - | - | atomic single-document ops only |
| POST | `/api/queue/<qid>/now` | `features/jobs.py:send_now` | queue | - | - | atomic single-document ops only |
| GET | `/api/learning/briefing` | `features/learning/briefing.py:get_briefing` | kv, locks, notifications, operations, sessions | save | notification | account lock, task lease |
| POST | `/api/learning/briefing` | `features/learning/briefing.py:rebuild_briefing` | kv, locks, notifications, operations, ratelimits, sessions | save | notification | account lock, task lease |
| POST | `/api/learning/day/<int:day>/complete` | `features/learning/course.py:complete_day` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/learning/dsa/complete` | `features/learning/course.py:complete_dsa` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/learning/review/<qid>` | `features/learning/course.py:grade_review` | kv, locks, notifications, sessions | save | - | account lock |
| PUT | `/api/li/config` | `features/linkedin.py:save_config` | site | - | - | atomic single-document ops only |
| GET | `/api/li/callback` | `features/linkedin.py:callback` | kv, locks, notifications, sessions | save | external API | atomic single-document ops only |
| POST | `/api/li/disconnect` | `features/linkedin.py:disconnect` | kv, locks, notifications, sessions | save | - | atomic single-document ops only |
| POST | `/api/li/post` | `features/linkedin.py:post_now` | kv, locks, notifications, ratelimits, sessions | save | external API | account lock |
| POST | `/api/li/schedule` | `features/linkedin.py:schedule` | queue | - | - | atomic single-document ops only |
| GET | `/api/nearby` | `features/nearby.py:status` | operations | - | - | atomic single-document ops only |
| POST | `/api/nearby/search` | `features/nearby.py:search` | kv, locks, notifications, operations, ratelimits, sessions | save | - | account lock |
| PUT | `/api/nearby/google-key` | `features/nearby.py:save_google_key` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/nearby/check` | `features/nearby.py:check` | operations, ratelimits | - | - | task lease |
| POST | `/api/nearby/track/<cid>` | `features/nearby.py:track` | - | update | - | versioned CAS |
| GET | `/api/notifications` | `features/notify.py:list_notes` | site | - | - | atomic single-document ops only |
| POST | `/api/notifications/read` | `features/notify.py:mark_read` | notifications | - | - | atomic single-document ops only |
| POST | `/api/notifications/clear` | `features/notify.py:clear` | notifications | - | - | atomic single-document ops only |
| PUT | `/api/notifications/prefs` | `features/notify.py:save_prefs` | kv, locks, notifications, sessions | save | - | atomic single-document ops only |
| POST | `/api/notifications/subscribe` | `features/notify.py:subscribe` | push_subs | - | - | atomic single-document ops only |
| POST | `/api/notifications/unsubscribe` | `features/notify.py:unsubscribe` | push_subs | - | - | atomic single-document ops only |
| POST | `/api/notifications/test` | `features/notify.py:test` | notifications, ratelimits | - | notification | atomic single-document ops only |
| POST | `/api/portfolio/items` | `features/portfolio.py:add_items` | kv, locks, notifications, sessions | save | GitHub API, external API | account lock |
| PUT | `/api/portfolio/items/<path:full>` | `features/portfolio.py:edit_item` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/portfolio/items/<path:full>/detect` | `features/portfolio.py:redetect` | kv, locks, notifications, sessions | save | GitHub API, external API | account lock |
| DELETE | `/api/portfolio/items/<path:full>` | `features/portfolio.py:remove_item` | kv, locks, notifications, sessions | save | - | account lock |
| PUT | `/api/portfolio/order` | `features/portfolio.py:reorder` | kv, locks, notifications, sessions | save | - | account lock |
| PUT | `/api/portfolio/settings` | `features/portfolio.py:settings` | kv, locks, notifications, sessions, site | save | - | account lock |
| POST | `/api/portfolio/publish` | `features/portfolio.py:publish` | kv, locks, notifications, sessions | save | GitHub API, external API | account lock |
| GET | `/p/<slug>` | `features/portfolio.py:public_page` | ratelimits, site_stats | - | - | atomic single-document ops only |
| POST | `/api/replies/check` | `features/replies.py:check_now` | kv, locks, notifications, ratelimits, replies, schedules, send_log, sessions | save | mailbox read, notification | account lock |
| PUT | `/api/replies/<rid_>` | `features/replies.py:mark` | replies | - | - | atomic single-document ops only |
| GET | `/api/replies/<key>/draft` | `features/replies.py:get_draft` | kv, locks, notifications, sessions | save | - | atomic single-document ops only |
| PUT | `/api/replies/details` | `features/replies.py:save_details` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/replies/<key>/send` | `features/replies.py:send_reply` | kv, locks, notifications, ratelimits, replies, schedules, send_log, sessions | log, save | SMTP, email | account lock |
| GET | `/api/research/companies` | `features/research/company.py:list_companies` | operations | - | - | atomic single-document ops only |
| POST | `/api/research/companies` | `features/research/company.py:start` | kv, locks, notifications, operations, ratelimits, sessions | save | - | account lock, task lease |
| DELETE | `/api/research/companies/<cid>` | `features/research/company.py:forget` | kv, locks, notifications, sessions | save | - | account lock |
| GET | `/api/site` | `features/site.py:get_site` | kv, locks, notifications, sessions | save | - | account lock |
| PUT | `/api/site` | `features/site.py:save_site` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/site/render` | `features/site.py:render_preview` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/site/publish` | `features/site.py:publish` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/site/unpublish` | `features/site.py:unpublish` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/site/reset` | `features/site.py:reset` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/site/images` | `features/site.py:upload_image` | site_images | - | - | atomic single-document ops only |
| POST | `/api/site/public/<slug>/contact` | `features/site.py:visitor_contact` | leads, notifications, ratelimits | - | notification | atomic single-document ops only |
| GET | `/api/update` | `features/updater.py:check` | ratelimits | - | - | atomic single-document ops only |
| GET | `/api/update/progress` | `features/updater.py:progress` | ratelimits | - | - | atomic single-document ops only |
| POST | `/api/auth/request-code` | `routes/auth.py:request_code` | otps, ratelimits | - | email | atomic single-document ops only |
| POST | `/api/auth/verify-code` | `routes/auth.py:verify_code` | kv, locks, notifications, otps, ratelimits, sessions, users | create, save | - | versioned CAS |
| POST | `/api/auth/signup` | `routes/auth.py:signup_password` | kv, locks, notifications, ratelimits, sessions, users | create, save | - | versioned CAS |
| POST | `/api/auth/login` | `routes/auth.py:login_password` | notifications, ratelimits, sessions, users | - | - | atomic single-document ops only |
| POST | `/api/account/password` | `routes/auth.py:change_password` | ratelimits, users | - | - | atomic single-document ops only |
| POST | `/api/auth/logout` | `routes/auth.py:logout` | notifications, sessions | - | - | atomic single-document ops only |
| POST | `/api/auth/logout-everywhere` | `routes/auth.py:logout_everywhere` | notifications, sessions | - | - | atomic single-document ops only |
| POST | `/api/account/delete/request` | `routes/auth.py:delete_account_request` | otps, ratelimits | - | email | atomic single-document ops only |
| POST | `/api/account/delete` | `routes/auth.py:delete_account` | notifications, operations, otps, queue, ratelimits, sessions, site, users, wa_sessions | - | file storage | atomic single-document ops only |
| POST | `/api/sentmail/scan` | `routes/contacts.py:sentmail_scan` | notifications, ratelimits, sessions | - | mailbox read | atomic single-document ops only |
| POST | `/api/sentmail/import` | `routes/contacts.py:sentmail_import` | kv, locks, notifications, send_log, sessions | add_event, log, save, set_status, update_settings | mailbox read, notification | account lock |
| POST | `/api/recipients` | `routes/contacts.py:add_recipient` | kv, locks, notifications, sessions | save | - | account lock |
| PUT | `/api/recipients/<rid>` | `routes/contacts.py:update_recipient` | kv, locks, notifications, sessions | add_event, save | - | account lock |
| GET | `/api/contacts/<rid>` | `routes/contacts.py:contact_detail` | kv | - | - | atomic single-document ops only |
| POST | `/api/contacts/<rid>/notes` | `routes/contacts.py:add_note` | kv, locks, notifications, sessions | add_event, save | - | account lock |
| DELETE | `/api/contacts/<rid>/notes/<nid>` | `routes/contacts.py:delete_note` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/recipients/delete` | `routes/contacts.py:delete_recipients` | kv, locks, notifications, sessions | drop_events, save | - | account lock |
| POST | `/api/recipients/reset` | `routes/contacts.py:reset_recipients` | kv, locks, notifications, sessions | save | - | account lock |
| POST | `/api/recipients/import` | `routes/contacts.py:import_recipients` | kv, locks, notifications, sessions | drop_events, save | - | account lock |
| GET | `/api/recipients/export` | `routes/contacts.py:export_recipients` | kv, locks, notifications, sessions | - | - | atomic single-document ops only |
| GET | `/api/recipients/sample` | `routes/contacts.py:sample_sheet` | kv, locks, notifications, sessions | - | - | atomic single-document ops only |
| POST | `/api/leads` | `routes/leads.py:create_lead` | leads, ratelimits | - | - | atomic single-document ops only |
| PUT | `/api/leads/<lid>` | `routes/leads.py:update_lead` | leads | - | - | atomic single-document ops only |
| DELETE | `/api/leads/<lid>` | `routes/leads.py:delete_lead` | leads | - | - | atomic single-document ops only |
| GET | `/api/leads/export` | `routes/leads.py:export_leads` | kv, locks, notifications, sessions | - | - | atomic single-document ops only |
| POST | `/api/operations/<kind>/cancel` | `routes/operations.py:operation_cancel` | operations | - | - | atomic single-document ops only |
| POST | `/api/admin/operations/<uid>/<kind>/cancel` | `routes/operations.py:admin_operation_cancel` | operations | - | - | atomic single-document ops only |
| POST | `/api/send` | `routes/send.py:start_send` | kv, locks, notifications, sessions | save, update_settings | - | account lock, task lease |
| POST | `/api/bounces/check` | `routes/send.py:bounces_check` | kv, locks, notifications, ratelimits, send_log, sessions | add_event, save, set_status, update_settings | mailbox read, notification | account lock |
| POST | `/api/job/stop` | `routes/send.py:stop_job` | operations | - | - | atomic single-document ops only |
| GET | `/login` | `routes/site_pages.py:auth_page` | sessions | - | - | atomic single-document ops only |
| GET | `/signup` | `routes/site_pages.py:auth_page` | sessions | - | - | atomic single-document ops only |
| GET | `/app` | `routes/site_pages.py:app_page` | sessions | - | - | atomic single-document ops only |
| GET | `/app/<path:subpath>` | `routes/site_pages.py:app_page` | sessions | - | - | atomic single-document ops only |
| GET | `/o/<token>.gif` | `routes/site_pages.py:open_pixel` | kv, locks, notifications, send_log, sessions | add_event, mark_opened, save | - | account lock |
| POST | `/api/client-errors` | `routes/site_pages.py:client_error` | ratelimits | - | - | atomic single-document ops only |
| POST | `/api/whatsapp/link` | `routes/whatsapp.py:wa_link` | operations | - | - | task lease |
| POST | `/api/whatsapp/cancel` | `routes/whatsapp.py:wa_cancel` | operations | - | - | atomic single-document ops only |
| POST | `/api/whatsapp/unlink` | `routes/whatsapp.py:wa_unlink` | kv, locks, notifications, sessions, wa_sessions | save, update_settings | file storage | account lock |
| POST | `/api/templates` | `routes/workspace_api.py:save_template` | - | update | - | versioned CAS |
| DELETE | `/api/templates/<tid>` | `routes/workspace_api.py:delete_template` | - | update | - | versioned CAS |
| PUT | `/api/profile` | `routes/workspace_api.py:save_profile` | kv, locks, notifications, operations, send_log, sessions | add_event, save, set_status, update_settings | mailbox read, notification | account lock, task lease |
| POST | `/api/profile/test` | `routes/workspace_api.py:test_profile` | ratelimits | - | SMTP, email | atomic single-document ops only |
| POST | `/api/documents` | `routes/workspace_api.py:upload_documents` | documents, files | save_doc | file storage | atomic single-document ops only |
| DELETE | `/api/documents/<name>` | `routes/workspace_api.py:delete_document` | documents | delete_doc | file storage | atomic single-document ops only |
| POST | `/api/settings` | `routes/workspace_api.py:save_settings` | kv, locks, notifications, sessions | save, update_settings | - | account lock |

180 endpoints that write or cause external effects (static call-graph analysis, depth-limited; see method notes).

## 5. Every direct MongoDB write, by collection

| Collection | Write sites (file:function:line op) | Existing concurrency control |
|---|---|---|
| `audit_log` | `infra/audit.py:ensure_indexes:94` create_index<br>`infra/audit.py:ensure_indexes:95` create_index<br>`infra/audit.py:ensure_indexes:96` create_index<br>`infra/audit.py:ensure_indexes:97` create_index | unique seq + hash chain; fail-closed |
| `audit_meta` | `infra/audit.py:_advance_head:124` update_one<br>`infra/audit.py:ensure_indexes:101` insert_one | forward-only conditional update |
| `db` | `migrations.py:migrate_sqlite:62` delete_many | migration only |
| `documents` | `core/accounts.py:init_storage:66` create_index<br>`core/workspace.py:save_doc:327` find_one_and_update<br>`core/workspace.py:delete_doc:335` find_one_and_delete<br>`migrations.py:migrate_sqlite:47` insert_one | atomic find_one_and_* |
| `files` | `core/storage.py:put_file:84` upload_from_stream<br>`core/storage.py:drop_file:120` delete | GridFS (legacy); Cloudinary in production |
| `getattr(M, name)` | `core/accounts.py:delete_user_data:196` delete_many | account deletion: delete_many per user collection |
| `kv` | `core/accounts.py:init_storage:61` create_index<br>`core/workspace.py:drop_events:280` delete_many<br>`core/workspace.py:_insert:202` update_one<br>`core/workspace.py:save:134` replace_one<br>`core/workspace.py:update:192` update_one<br>`migrations.py:migrate_sqlite:28` insert_many | Workspace.save: account lock + transactional lease check when under ws.lock (guarded_write), plain write otherwise; versioned keys: CAS (version+wid). drop_events: none (delete). |
| `leads` | `core/accounts.py:init_storage:67` create_index<br>`core/accounts.py:init_storage:68` create_index<br>`features/site.py:visitor_contact:961` insert_one<br>`migrations.py:migrate_sqlite:56` insert_many<br>`routes/leads.py:create_lead:75` insert_one<br>`routes/leads.py:update_lead:119` update_one<br>`routes/leads.py:delete_lead:126` delete_one | per-document (admin/owner) |
| `locks` | `infra/locks.py:ensure_indexes:114` create_index<br>`infra/locks.py:_take:215` find_one_and_update<br>`infra/locks.py:_take:220` find_one_and_update<br>`infra/locks.py:body:314` update_one<br>`infra/locks.py:renew:170` update_one<br>`infra/locks.py:release:188` update_one<br>`infra/locks.py:_take:216` find_one_and_update | lease protocol itself |
| `mail_index` | `features/inbox.py:ensure_indexes:178` create_index<br>`features/inbox.py:ensure_indexes:179` create_index<br>`features/inbox.py:ensure_indexes:180` create_index<br>`features/inbox.py:scan:211` delete_many<br>`features/inbox.py:scan:236` insert_many | unique (uid,mid); delete_many on full rescan (task lease) |
| `notifications` | `features/notify.py:init:54` create_index<br>`features/notify.py:init:55` create_index<br>`features/notify.py:mark_read:172` update_many<br>`features/notify.py:clear:179` delete_many<br>`features/notify.py:notify:78` insert_one | insert-only + bulk mark/clear |
| `operations` | `infra/operations.py:ensure_indexes:60` create_index<br>`infra/operations.py:ensure_indexes:61` create_index<br>`infra/operations.py:ensure_indexes:62` create_index<br>`infra/operations.py:_settle:243` update_one<br>`infra/operations.py:_write:93` update_one<br>`infra/operations.py:start:215` update_many<br>`infra/operations.py:start:221` insert_one<br>`infra/operations.py:cancel:293` update_one | lease token + state match |
| `otps` | `core/accounts.py:init_storage:60` create_index<br>`routes/auth.py:otp_throttle:66` replace_one<br>`routes/auth.py:issue_otp:83` replace_one<br>`routes/auth.py:check_otp:98` delete_one<br>`routes/auth.py:check_otp:108` find_one_and_update<br>`routes/auth.py:check_otp:105` delete_one<br>`routes/auth.py:check_otp:114` delete_one | per-document atomic |
| `push_subs` | `features/notify.py:init:56` create_index<br>`features/notify.py:subscribe:219` replace_one<br>`features/notify.py:unsubscribe:230` delete_one<br>`features/notify.py:unsubscribe:232` delete_many<br>`features/notify.py:push_all:134` delete_one<br>`features/notify.py:push_all:139` delete_one | per-document |
| `queue` | `core/accounts.py:init_storage:74` create_index<br>`core/accounts.py:delete_user_data:183` update_many<br>`features/jobs.py:queue_doc:631` insert_one<br>`features/jobs.py:edit_queue_item:735` update_one<br>`features/jobs.py:cancel_queue_item:742` update_one<br>`features/jobs.py:send_now:751` update_one<br>`features/jobs.py:run_queue:836` update_many<br>`features/jobs.py:start_workers:841` create_index<br>`features/jobs.py:start_workers:842` create_index<br>`features/jobs.py:run_queue:810` find_one_and_update<br>`features/jobs.py:run_queue:827` update_one<br>`features/jobs.py:run_queue:825` update_one | atomic claim (find_one_and_update status queued->sending) |
| `ratelimits` | `core/accounts.py:init_storage:73` create_index<br>`core/security.py:rate_limited:83` find_one_and_update<br>`core/security.py:rate_limited:87` find_one_and_update | atomic $inc |
| `replies` | `features/replies.py:init:37` create_index<br>`features/replies.py:init:38` create_index<br>`features/replies.py:init:39` create_index<br>`features/replies.py:mark:557` update_one<br>`features/replies.py:send_reply:827` update_one<br>`features/replies.py:ingest:452` insert_one | unique ingest + per-doc updates |
| `roles` | `infra/rbac.py:ensure_indexes:42` create_index<br>`infra/rbac.py:ensure_indexes:43` create_index<br>`infra/rbac.py:grant:205` update_one<br>`infra/rbac.py:reconcile:87` update_one<br>`infra/rbac.py:revoke:107` update_one<br>`infra/rbac.py:_flush_one:122` update_one | audit-first; revocation outbox |
| `schedules` | `infra/schedule.py:ensure_indexes:31` create_index<br>`infra/schedule.py:ensure_indexes:32` create_index<br>`infra/schedule.py:claim:40` find_one_and_update<br>`infra/schedule.py:soon:52` update_one<br>`infra/schedule.py:postpone:59` update_one<br>`infra/schedule.py:claim:43` insert_one<br>`infra/schedule.py:claim:46` find_one_and_update<br>`infra/schedule.py:once:69` insert_one | atomic slot claim / once-marker |
| `send_log` | `core/accounts.py:init_storage:62` create_index<br>`core/accounts.py:init_storage:63` create_index<br>`core/accounts.py:init_storage:64` create_index<br>`core/accounts.py:init_storage:65` create_index<br>`core/bounces.py:_check_bounces:166` update_one<br>`core/workspace.py:log:243` insert_one<br>`features/replies.py:ingest:450` update_one<br>`migrations.py:migrate_sqlite:40` insert_many<br>`routes/site_pages.py:open_pixel:155` find_one_and_update<br>`routes/site_pages.py:open_pixel:156` update_one | insert-only + atomic counters |
| `sessions` | `core/accounts.py:init_storage:69` create_index<br>`core/accounts.py:init_storage:72` create_index<br>`core/accounts.py:start_session:147` insert_one<br>`core/accounts.py:delete_user_data:199` delete_many<br>`core/accounts.py:end_session:172` delete_one<br>`core/accounts.py:current_uid:161` delete_one<br>`core/accounts.py:current_uid:164` update_one<br>`routes/auth.py:logout_everywhere:258` delete_many | per-document; none needed |
| `site` | `core/accounts.py:delete_user_data:198` delete_many<br>`features/linkedin.py:save_config:76` replace_one<br>`features/notify.py:vapid:111` replace_one<br>`features/portfolio.py:claim:44` insert_one<br>`features/portfolio.py:settings:248` delete_one | atomic claims (slug insert); config replace (admin, audited) |
| `site_images` | `core/accounts.py:init_storage:70` create_index<br>`core/storage.py:move_files_to_cloudinary:155` update_one<br>`features/site.py:upload_image:868` insert_one | insert-only |
| `site_stats` | `core/accounts.py:init_storage:71` create_index<br>`features/site.py:public_html:903` update_one | atomic $inc |
| `users` | `core/accounts.py:init_storage:58` create_index<br>`core/accounts.py:init_storage:59` create_index<br>`core/accounts.py:new_user:91` insert_one<br>`core/accounts.py:set_password:122` update_one<br>`core/accounts.py:delete_user_data:200` delete_one<br>`migrations.py:migrate_sqlite:22` insert_one<br>`routes/auth.py:verify_code:163` update_one<br>`routes/auth.py:signup_password:208` update_one<br>`routes/auth.py:login_password:227` update_one | per-document; signup insert (unique) |
| `wa_sessions` | `core/accounts.py:delete_user_data:197` delete_one<br>`core/whatsapp_store.py:store_wa_blob:59` find_one_and_update<br>`core/whatsapp_store.py:link_whatsapp:176` update_one<br>`core/whatsapp_store.py:expire_wa_sessions:84` find_one_and_delete<br>`routes/whatsapp.py:wa_unlink:78` find_one_and_delete | atomic find_one_and_* on file id |

## 6. What this means for a deployment gate

- **Writers per process:** every instance writes from its own import-time startup, 8 background loops, task worker threads and ~180 endpoints, through **26 collections**. Writes that coordinate across processes do so only through `locks`, `operations`, `schedules` and versioned records, and only in releases after `8b59986`.
- **No single application-level choke point exists today:**
  - writes go through `core.db.M` (a thin wrapper), but in `8b59986` nothing checks anything there;
  - GridFS (`M.files`) and `getattr(M, name)` are dynamic.
  - The only point that every write passes through, in every release, is **the MongoDB server's authorisation of the connection's user**. The architecture document builds on that.
