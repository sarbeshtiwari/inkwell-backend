# Deployment gate architecture

**Status:** proposal only. Nothing here is implemented.

> **Credential fencing is NOT approved for production yet.** It has been shown on local MongoDB 8.0 servers only. It
> becomes approvable only after the Atlas verification in `RENDER_FREE_DEPLOYMENT_RUNBOOK.md` → *Stage 0* passes on
> the real cluster. The cluster's hostnames (`ac-…-shard-00-0x`) and its restricted `serverStatus` suggest a
> shared-tier / proxied deployment (M0 or Flex), where authentication may be handled by an Atlas proxy (F12). If Stage 0
> can't prove the behaviour there, the design is **not** usable as described, and a paid dedicated tier (or another
> fence) is needed first.
>
> Stage 0 also includes two **Render** checks that the first migration depends on, both unverified today:
> - that *Suspend* actually terminates the old process, shown by **termination evidence**: a post-exit record that
>   meets runbook 0.5's acceptance conditions (provenance, termination positive control, negative controls, agreement
>   with a fake provider). **Liveness observations** (database connections, `/healthz`, logs) are used only for
>   controls and consistency checks, and never qualify: a live PyMongo client can have no connection. No Render signal
>   is documented as container-termination evidence. **So 0.5 is expected to fail
>   unless Render documents or confirms one: this blocks the first migration today;**
> - what a fenced release does when it is restarted, and whether Render then accepts the next deploy (runbook 0.6).
>
> If either can't be verified, the migration must not proceed.

**Goal:** two releases that aren't compatible must never write to the production database at the same time. This has to
hold on Render Free, where every deploy overlaps the old and new instance and there is no persistent disk.

## 1. Facts that constrain the design

These were verified this session, by test or from the documentation.

| # | Fact | Source |
|---|---|---|
| F1 | Render redeploys "with zero downtime, unless they attach a persistent disk"; the old instance keeps running ~60 s after traffic switches, then gets SIGTERM, then SIGKILL after the shutdown delay (default 30 s). This is documented **for deploys**. What *Suspend* does to a running instance (signal, delay, kill) is **not documented**, and is verified in runbook Stage 0.5. *Suspended* is a control-plane state, not evidence that the container exited. | Render docs, *Deploys*; API reference (suspend/resume) |
| F2 | Free services can't have persistent disks, can't scale, have no shell or one-off jobs, may be restarted at any time, spin down after 15 min idle, and **can't send outbound traffic on ports 25, 465 or 587** (SMTP). | Render docs, *Free* |
| F3 | A suspended service can't be deployed (deploy hook → 409). | Render docs, *Deploy hooks* |
| F4 | `8b59986` imports `app` → `init_storage()` and starts 7 background loops **before serving any request**, and reads no deployment state. | `git show 8b59986:app.py` |
| F5 | **Revoking a MongoDB user's write role applies to its already-open, pooled connections at once**: the next write fails `Unauthorized`. Dropping the user does the same. **Changing the password does NOT** affect open connections. | Tested on MongoDB 8.0 with `--auth` (scratch servers, this session) |
| F6 | **A transaction that already ran its statements can still commit after the role is revoked.** | Tested on an 8.0 replica set with `--auth` |
| F7 | Atlas database users must be changed through the Atlas UI, CLI or Admin API (other changes are rolled back). Changes are audited in the Activity Feed. Atlas supports temporary users that expire. **How quickly a change reaches the cluster is not documented.** | MongoDB Atlas docs |
| F8 | `8b59986` uses no transactions (F10). `5ba9022`/`a83d17e` use transactions for writes under the account lock (client limit `LOCK_TXN_SECONDS` = 10 s; the server aborts any transaction after 60 s). | code |
| F9 | `8b59986` and later run `gunicorn==26.2.0` with `--workers 1 --threads 16 --timeout 120` and no config file: `--timeout` is the worker heartbeat timeout (with threaded workers a long request doesn't trip it), `graceful_timeout` is the default 30 s, and **no per-request duration limit exists**. Render's shutdown delay defaults to 30 s (configurable up to 300 s on paid plans). The Dockerfile runs gunicorn through the shell form of `CMD`: `/bin/sh -c "gunicorn …"`. Whether SIGTERM reaches gunicorn depends on `/bin/sh` exec-ing it (dash/bash behaviour, **unverified on the image**). All background work runs in **daemon threads**, which are killed, not drained, when the worker exits. | Dockerfile, code |
| F10 | `8b59986` has **no MongoDB transactions** (no `with_transaction` / `start_transaction` / `client.start_session`; its `start_session(uid)` is the app's login-session helper). A signed-in request writes to the database before its handler only if the session was last touched > 1 h ago. Handlers that reach an external provider **before any database write**: GitHub writes (`create_repo`, `commit`, `create_branch`, `create_pull`, `merge_pull`, `close_pull`, `fork`, `comment`, `create_issue`, `markdown`), `github.connect`, `portfolio.publish` (GitHub), `site.upload_image` and document uploads (`save_doc`: Cloudinary). Handlers that write first (rate limit, settings, queue claim): campaign start, reply send, queue/send-now, auto-apply, LinkedIn post, OTP and lead emails; the send-queue loop claims before sending. | code audit of `git archive 8b59986` |
| F11 | Every backend path except `/healthz` requires a valid Netlify proxy signature (`only_via_netlify`, HS256 with `NETLIFY_PROXY_SECRET`); production sets it (a direct request to the Render URL returned 404). Both Netlify sites sign with an environment variable of that name (`signed = "NETLIFY_PROXY_SECRET"`). | code + production check |
| F12 | Atlas cluster: 3 members `ac-icuf2xg-shard-00-0{0,1,2}`, region `AP_SOUTH_1`, MongoDB 8.0.x; `serverStatus` exposes only a subset of sections. **Tier unconfirmed** (needs the Atlas UI); the naming and restrictions are typical of shared/proxied tiers, where authentication and user caching are handled by Atlas, not visible to us. | read-only checks this session |
| F13 | The server's transaction lifetime (`transactionLifetimeLimitSeconds`, default 60 s) may not be readable or settable on shared tiers. The wait after a fence must use the value proven in Stage 0, not an assumed 60 s. | MongoDB docs; to verify |
| F14 | `8b59986`'s `app.py` calls `init_storage()` at import, which runs `create_index` on `reachout` collections and `rbac.bootstrap()`; only `M.connect()` is wrapped in error handling. With a fenced user, the first `create_index` is **expected** to fail `Unauthorized`, so the import fails before the background loops start, and gunicorn reports that the worker failed to boot. **Unverified on Render:** whether the service then restarts repeatedly, what status Render shows, and whether it accepts the next deploy (runbook Stage 0.6). `a83d17e` also creates indexes at import. | `git show 8b59986:app.py`, `core/accounts.py` |
| F15 | `8b59986` pins `pymongo==4.18.2` and uses `MongoClient(uri, serverSelectionTimeoutMS=8000, appname="reachout", tz_aware=True)`: `minPoolSize` 0, no `maxIdleTimeMS`. In 4.18.2, monitoring connections carry **no credentials** (`Topology._create_pool_for_monitor`), application connections are created only on demand, and the pool is cleared on network errors and step-downs. So a **live process can have zero authenticated connections**, and connection absence is not evidence of termination. Render's service events (`service_suspended`, `instance_count_changed`, `server_failed`, …) include none documented as "container terminated". | PyMongo 4.18.2 source; Render API reference |

## 2. Options evaluated

| Option | Stops a paused process's late write? | Stops `8b59986`? | Cost per write | Verdict |
|---|---|---|---|---|
| A. Boolean flag checked before writing | **No**: check-then-write race (§4) | No: the old release never reads it | +1 read (or a cached value) | Rejected |
| B. Per-process admission lease ("permit") checked before writing | Only bounded by the lease lifetime; a process paused past its lease can still write | No | ~0 (renewed in the background) | Useful as a **soft** gate only |
| C. Every business write in a transaction that also checks/updates the gate document (like `guarded_write`) | Yes, atomic at the server, *for transactional writes* | No | +3–4 round trips per write (~0.6–0.8 s from Render, measured in Stage 1/2) and conflicts on the gate document under load | Rejected: too costly; also can't cover non-transactional writes, GridFS or external effects |
| D. **Credential fencing:** each deployment generation gets its own database user; the previous generation's user is fenced (runbook *Definitions*) before the next generation is allowed to write | **Yes, by the server, on every connection, for every collection** (F5), regardless of code. Only exception: transactions whose statements already ran (F6), bounded by the transaction lifetime `T` (F13). Proven on local MongoDB 8.0; **unproven on Atlas until Stage 0** | **Yes**: it doesn't need the old release's cooperation | 0 | **Recommended hard fence** |
| E. Network fencing (Atlas IP access list) | Partly: existing connections aren't necessarily dropped (unverified); Render Free has no static outbound IP | Partly | 0 | Rejected (unverifiable, coarse) |
| F. Separate coordinator service / Redis / PostgreSQL | Nothing D doesn't already do; the processes still write to MongoDB directly | No better than D | n/a | Not justified (MongoDB authorisation already gives the fence) |

**Recommendation:** **D (hard, server-enforced)**, optionally with **B (soft, cooperative)** for graceful draining and
less downtime. D is the safety mechanism. B only makes D pleasant.

## 3. Components and trust boundaries

```
 ┌──────────────── Operator (you) ─────────────────┐   Trust: Atlas project access (MFA) = authority to change
 │ Atlas UI / Admin API: create/disable DB users   │          which release may write. Render dashboard: env + deploy.
 │ Render dashboard: env var MONGODB_URI, deploy   │
 └───────────────┬──────────────────────┬──────────┘
                 │ (credentials)        │ (deploy)
       ┌─────────▼────────┐     ┌───────▼────────────┐
       │ Release gen N-1  │     │ Release gen N      │   Trust: an app process holds only ITS generation's
       │ user inkwell_gN-1│     │ user inkwell_gN    │          credentials (env var). It can't grant itself roles.
       └─────────┬────────┘     └───────┬────────────┘
                 │ authenticated connections (TLS)
       ┌─────────▼──────────────────────▼────────────┐
       │ MongoDB Atlas: authorises EVERY command by   │   Enforcement point: the server. No application code
       │ the connection's user roles (F5)             │   (old or new) can bypass it.
       │ db reachout: business data                   │
       │ db inkwell_control: deploy_state (read-only  │
       │   for app users, read-write for the operator)│
       └──────────────────────────────────────────────┘
```

- **Boundary 1 (operator ↔ Atlas):** only people with Atlas project roles can change database users. Their actions
  are audited in the Atlas Activity Feed.
- **Boundary 2 (application ↔ Atlas):** an application process is trusted only as far as its user's roles. A
  compromised, old or forgotten process (including a developer laptop) is contained by removing its user's roles.
- **Boundary 3 (application ↔ external providers):** SMTP, WhatsApp, job sites, GitHub, LinkedIn and Cloudinary
  can't be fenced or rolled back. Some old-release handlers call them *before* any database write (F10), so database
  fencing alone doesn't stop new requests reaching them. They are handled by closing ingress (boundary 4) and by
  policy (§7).
- **Boundary 4 (users ↔ backend, ingress):** the only way into the backend is the Netlify proxy, which signs every
  request (F11). Changing the secret the Netlify sites sign with (an operator action in Netlify, plus a redeploy of the
  same site commit) makes the backend refuse every request before any handler runs. It closes HTTP ingress for any
  backend release, old ones included. It does **not** stop that backend's own background loops or threads, and
  neither does the database fence: a live thread can call a provider before its next database write (§7). Only
  stopping the process, with the termination evidence of runbook step 1.4, ends them.
- **Untrusted:** a developer machine holding production credentials (today the Mac's `.env` holds the production URI
  with the production user). D removes it from the trust set as soon as that user is disabled.

## 4. The critical race (check, pause, HOLD, write)

1. Process A (generation N-1) checks that it may write.
2. A pauses (GC, CPU starvation, SIGSTOP).
3. The operator starts the switch to generation N.
4. Generation N starts writing.
5. A resumes and writes.

**With a soft gate only (A, B):** A's write lands. A pre-write check can't be atomic with a write it doesn't take part
in. A lease (B) only limits the window to the lease lifetime, and a pause can outlast any lease. **Not acceptable on
its own.**

**With credential fencing (D):** generation N is allowed to write only after generation N-1's user is *fenced* (runbook
*Definitions*: no write privilege on `reachout`, no other write-capable or cluster-wide role, at most
`read@inkwell_fenced`), and the *fence evidence* has been recorded
(step 3 is "revoke, then verify, then activate"). A's write in step 5 reaches the server with generation N-1's user
and is **refused `Unauthorized`** (F5), however long A was paused, provided the role change has reached A's
connection. That covers every collection and every code path, including GridFS, `getattr` collections, startup code and
`8b59986`. That it has reached every open connection by `t_fence + P + M` is empirical (Stage 0.2, §10).
**Database writes only:** A's provider calls aren't stopped by this (§7).

**The one gap: transactions already running (F6).** If A had started a transaction and run its statements before the
revoke, it can still commit afterwards.
- **Closing it:** after revoking, wait out the server's transaction lifetime before activating generation N. The
  value used is the one measured in Stage 0, with margin; the 60 s default is not assumed (F13). An open transaction
  can't outlive that.
- **Checking it:** optionally list open transactions for the old user with `$currentOp`, which needs an Atlas role
  with `inprog`.
- **Scope:** `8b59986` uses no transactions (F10), so this applies only when fencing a `5ba9022`-or-later
  generation.

**Permit acquisition, HOLD and in-flight work (soft gate B):**
- A permit is checked when work *starts* (a request, a loop tick, a task).
- HOLD stops new permits being issued.
- In-flight work is drained by the shutdown protocol (§8, gate-aware releases only). The fence cuts off only its
  *database writes*, not its provider calls. `8b59986` has no shutdown protocol, so its instance is stopped instead
  (runbook step 1.4).
- **No business write's safety depends on B.**

**What is atomic in MongoDB, and what isn't:**

| Atomic in MongoDB | Not atomic in MongoDB |
|---|---|
| a single-document write (incl. versioned CAS) | an email, WhatsApp message, job-site submission, GitHub/LinkedIn call |
| a multi-document transaction (writes under `ws.lock`) | a Cloudinary upload/delete |
| server authorisation of each command (the fence) | a sequence of separate writes without a transaction (e.g. send, then `set_status`, then `log`) |
| | anything in another process |

## 5. Deployment-control data (soft gate, optional component B)

Database `inkwell_control` (separate, so built-in roles can make it read-only for app users), collection
`deploy_state`, one document:

```json
{ "_id": "current", "generation": 42, "state": "ACTIVE",            // ACTIVE | HOLD
  "release": "a83d17e", "db_user": "inkwell_g42",
  "changed_at": "...", "changed_by": "operator:<atlas user>", "reason": "deploy a83d17e" }
```

- **Who writes:** only the operator's user (app users get `read` on `inkwell_control` only) (Atlas UI/CLI or a local script with operator credentials). App users have
  `read` on `inkwell_control`. **The app can't change its own gate.**
- **What the app does with it:**
  - at startup and every ≤ 5 s, it reads the document;
  - it compares `generation`, `db_user` (from its own connection string) and `state`;
  - it is **admitted** only if `state == ACTIVE` and `db_user` is its own user.
  - **Not admitted means:** no background loops claim work, no task starts, mutating endpoints answer
    `503 {"error": "Inkwell is being updated…"}` with `Retry-After`, reads still work, and `/readyz` reports `held`.
- **Identity:**
  - release = `RENDER_GIT_COMMIT` (set by Render; **verify**), or the build's git SHA;
  - process = `host:pid`;
  - generation = the number in `deploy_state`, which only increases (the operator sets N+1 at each activation).
- **Fencing stale permits:** the soft gate re-reads ≤ 5 s, so a stale "admitted" lasts ≤ 5 s *plus any pause*. The
  hard fence (D) is what makes that harmless.
- **Audit:**
  - Atlas Activity Feed: user created, roles changed, user deleted. This is authoritative.
  - The new release writes `deploy.observed` (generation, release, state) to the app audit log when its admission
    changes.
  - The operator records each step in the runbook log.
- **If `deploy_state` can't be read:** treated as HOLD (fail closed for new work). Existing in-flight work keeps its
  admission for ≤ 5 s and is then held.
- **Stuck in HOLD:** the operator sets `ACTIVE` (§ runbook). The fence is unaffected.

## 6. Where enforcement lives (no bypass)

- **Hard fence (D): in the database.** Nothing in any release can bypass it. No code changes are needed for it to
  hold against `8b59986`.
- **Soft gate (B), if implemented:**
  1. In `core/db.Mongo.__getattr__` (the only route to collections, `M.<name>`): a wrapper whose write methods
     (`insert*`, `update*`, `replace*`, `delete*`, `find_one_and_*`, `bulk_write`, GridFS `upload*`/`delete`) raise
     `Held` unless the process is admitted or the call is in an explicit exemption scope.
  2. **Explicit exemptions**, all listed in one place and covered by a test:
     - `deploy_state` reads;
     - lease **release and renewal** (so held processes free their locks);
     - audit `deploy.*` events;
     - `/healthz` (read-only);
     - the operator's admin actions, under a narrow `with exempt("reason")` that is audited.
  3. A code-scan test fails on any `MongoClient(` outside `core/db.py`, any `.client[`/`.db[` access outside
     `core/db.py`, and any new exemption not on the list.
- **Startup routines:** `init_storage` (indexes, `rbac.reconcile`, audit head repair) runs only after admission.
  Creating indexes is idempotent but still a write; it is gated too.

## 7. External side effects

**Rule:** revoking MongoDB credentials **cannot cancel, undo or prevent an external request that has already left the
process**, and it doesn't stop a handler that calls a provider before writing (F10).

**What the database fence prevents, and what it can't:**

| The fence **prevents** | The fence **does not prevent** |
|---|---|
| a *new* HTTP-started operation whose handler writes first (rate limit, settings, queue claim), but ingress closure already stops these | a handler that calls the provider **before** any write (GitHub writes, portfolio publish, Cloudinary uploads; inventory §3a) |
| a *new* send-queue claim: the loop claims with a write before calling the provider | the provider call for a queue item **already claimed** (`status: sending`) before the fence: the call needs no further write |
| | a **live campaign thread** sending to its next person: it sends first, then writes, and can wait up to 3600 s between people (inventory §3b) |
| | a **live apply thread** submitting to a job site: it submits first, then records `ai_applied` |
| | an HTTP request accepted before ingress closed that calls a provider before its next write |
| | a request already in flight to a provider |

It also stops the *recording* of any outcome after the fence, so a message a live thread sends afterwards goes
unrecorded. So the fence protects **data**, and **stopping the process** is the protection against old-release background
threads. The fence is never a substitute for it.

**Three states for every external operation:**

| State | Meaning | How it is controlled during a deploy |
|---|---|---|
| **Not started** | No request to the provider has been made. | *HTTP-initiated:* **close ingress** (boundary 4) before fencing, so no new request reaches a handler. *Background-initiated* (campaign and apply threads, the send queue): **stop the old process** (runbook step 1.4, with termination evidence) before fencing. The fence alone isn't enough (table above). Preconditions make it unlikely that a campaign or apply run is active, or that anything is queued for the window. |
| **In flight** | The request has been sent; no response yet. | Nothing can stop it, and no wait proves it has ended: `8b59986` has no request-duration bound (`--timeout 120` is a worker heartbeat, not a request limit, with threaded workers) and no task registry. Stopping the process ends it from Inkwell's side; anything cut off mid-call becomes *outcome unknown*. |
| **Outcome unknown** | Sent, but the process stopped, crashed or was fenced before recording the result, or the provider's answer was lost. | Recorded as `unknown` by releases with the delivery state machine (Stage 4, proposed). For `8b59986`, identified afterwards by comparing provider-side evidence (Sent folder, LinkedIn, job site, GitHub, Cloudinary) with Inkwell's records (runbook step 1.10). **Never retried automatically.** The operator lists these and asks the user. |

**Policy:**
1. **Human approval:** every email, WhatsApp message, reply, queued email, application submission, LinkedIn post and
   GitHub write is started by an explicit user action. The gate never sends anything itself, and HOLD only prevents
   new operations.
2. **No automatic retries** of any external operation, by the gate or recovery code. A user may resend an `unknown`
   item only after explicit confirmation.
3. **Order during a deploy:** close ingress → stop the old instance, with termination evidence (no wait proves that
   `8b59986` threads have finished, and the fence doesn't stop them) → fence the database → list unknown outcomes. The runbook's preconditions cover each provider path from the inventory: campaign email and WhatsApp,
   queued email, replies, applications, LinkedIn, GitHub, Cloudinary, OTP/login/lead emails and web push.
4. **Releases with the delivery state machine** (Stage 4, proposed, not implemented) would record `sending`/`submitting` durably before calling the provider, so
   a fence or crash would leave an explicit `unknown`, never a silent gap.

**Render Free and SMTP (F2):** on the Free plan, outbound SMTP (465/587) is blocked. If the service is Free, email
features already don't work in production (Stage 0 confirms the plan).

## 8. Shutdown protocol (new releases)

**Today:**
- gunicorn gets SIGTERM only if `/bin/sh` passes it on (F9);
- the gunicorn master then asks its worker to finish in-flight requests within `graceful_timeout` (default 30 s);
- **daemon threads (loops, campaigns, syncs) keep running until the process exits, then die mid-step**;
- the operation records stay `running` until their lease expires, and then read `interrupted`.

**Proposed:**
1. **Signal delivery:** exec-form `CMD ["gunicorn", …]` (or `exec gunicorn` in a script), so gunicorn is PID 1 and
   gets SIGTERM directly.
2. **`gunicorn.conf.py`:** `graceful_timeout = 25` (below Render's 30 s shutdown delay). A `worker_int`/`worker_exit`
   hook sets a process-wide `draining` flag.
3. **When `draining` is set:**
   - the process stops being admitted, for its own new work;
   - loops stop claiming;
   - `operations.start` refuses;
   - running tasks get a cancellation request, are stopped at their next safe point, and are recorded `cancelled`;
   - external actions already in flight finish and are recorded;
   - leases are released.
4. **"Drained" is reported** (log line plus a `deploy.drained` audit event with the count of tasks still running) only
   when no admitted request is in flight and no task thread is alive. If the 25 s run out first, it logs
   `deploy.drain_incomplete`, with the task kinds, and exits. **It never claims "drained" while work is still
   running.**
5. **Render timing:**
   - after traffic switches, the old instance keeps running ~60 s, then gets SIGTERM;
   - with the fence, its writes have already been refused for that whole time (the operator fences *before*
     activating the new generation);
   - the shutdown protocol only matters for releases that implement it, and for a tidy finish. Safety comes from the
     fence.

## 9. Sequence diagrams

### 9a. First migration, 8b59986 (gen 1, user `inkwell_g1`) → a83d17e (gen 2) — no app changes required

```
Operator         Netlify          Atlas               Render old (8b59986,g1)   Render new (a83d17e,g2)   Mac
   |  0 checks: Stage 0 passed; preconditions (runbook); Mac app stopped; nothing running or queued |
   |--create inkwell_g2 (readWrite reachout only)-->|                                                 |
   |  ======= maintenance window starts =======                                                      |
   |--rotate proxy secret S1->S2, redeploy sites-->|  (old backend now refuses every request: no handler runs)
   |--Render: Suspend old service; termination evidence = the post-exit record accepted in Stage 0.5 (none known today)
   |   (Suspended, liveness absent [zero DB connections, /healthz, quiet logs], elapsed time are NOT evidence; none -> abort)
   |--fence inkwell_g1 (record t_fence)------->|                                                      |
   |                                           |--any process still holding g1 (e.g. the Mac): Unauthorized---X
   |--fence evidence: now >= t_fence+P+M, fresh probe refused, definition check                     |
   |--Render: secret := S2 (URI still the FENCED g1); Resume -> 8b59986 restarts; init_storage create_index
   |     Unauthorized -> expected boot failure before any loop (F14); Render's handling verified in Stage 0.6 ---X|
   |--ONE change: MONGODB_URI := g2 + deploy commit a83d17e ------------------------------------------->|
   |                                           |                         build, boot, init_storage(g2) OK
   |                                           |<------------------------ writes as g2 (allowed)    |
   |   the failing fenced 8b59986 (no writes, no loops, no signed traffic) is replaced by a83d17e         |
   |--consistency check (liveness observation): no g1 connection remains (can only stop us, not proof) |
   |  ======= maintenance window ends: new instance healthy =======                                   |
   |--verify: /healthz 200, sign-in, template save (version+wid), logs clean                          |
   |--(later) delete user inkwell_g1-------------->|                                                  |
```

**Window:** from the ingress close to the new instance being healthy (suspend + termination evidence + P + M + resume +
build + boot):
several minutes. It's a planned full outage. Abort procedures: runbook *Abort, case A / case B* (the only recovery
instructions; all documents defer to them).

### 9b. Later deploys between gate-aware releases (gen N → N+1), with the soft gate: shorter window

```
Operator      Atlas / deploy_state            old (gen N, admitted)        new (gen N+1)
   |--create inkwell_gN+1 (readWrite)--->|
   |--Render: MONGODB_URI := gN+1; deploy ----------------------------------------->| boots: deploy_state says
   |                                     |                                          | db_user=gN -> NOT admitted
   |                                     |                                          | (no init writes, no loops,
   |                                     |                                          |  503 on mutations)
   |   Render switches traffic -> new (held): mutations answer 503 "updating", reads work
   |--set deploy_state HOLD (gen N)----->|--old sees HOLD <=5 s: stops loops/tasks, drains
   |--remove roles from inkwell_gN------>|--old's remaining writes: Unauthorized (hard)
   |--full fence evidence for gN (t_fence+P+M, fresh probe, definition check); wait T (F6, F13)
   |--set deploy_state ACTIVE, generation N+1, db_user gN+1 -->| new admitted <=5 s: init_storage,
   |                                     |                                          | loops start, mutations OK
   |--verify, then delete inkwell_gN
```

**Window for mutations:** from the traffic switch to activation (~1–2 min). Reads stay up. This path needs Stage 3 and a compatibility declaration (Stage 5) proven for the pair; until then use 9a's fenced procedure. Abort: runbook *Abort*.

### 9c. Rollback a83d17e (gen N) → 8b59986

`8b59986` has no gate and writes at import (F4). So:
1. Close ingress.
2. Check `operations` and `queue`, then stop the `a83d17e` instance (*Suspend*) with termination evidence: the fence
   doesn't stop its live threads.
3. Fence gen N's user, obtain the fence evidence, and wait out the transaction lifetime proven in Stage 0 (`a83d17e`
   uses transactions).
4. Resume with the fenced URI (a fenced `a83d17e` is expected to fail at start-up; verified in Stage 0.6), then in one
   change deploy `8b59986` with a **new** user gen N+1 (readWrite on `reachout` only) and the new proxy secret. Prefer
   a Render rollback to the previous successful deploy of `8b59986`: no rebuild, so a shorter window.

The window is a full outage, as in 9a. **Never** deploy `1d544b7` or `5c21025`. Runbook §R is authoritative.

## 10. What this design guarantees, and what it doesn't

**Server-enforced (verified on local MongoDB 8.0):** once a role change has reached a connection, every write on it is
refused, whatever release or code path issued it (F5). Transactions whose statements already ran are the exception
(F6).

**Empirical, not guaranteed (Atlas):** *when* a role change reaches every previously open connection.
- `P` is the largest propagation time observed in the Stage 0.2 runs. `P + M` is an operational threshold chosen
  from that measurement, **not a proven maximum**.
- A future role change could propagate more slowly.
- With the fence evidence complete (elapsed ≥ `t_fence + P + M`, fresh-connection probe refused, definition check),
  the operator proceeds on the basis that every connection of that user is refused. That basis is empirical for old
  connections and directly checked only for fresh ones.
- The residual risk is limited by:
  - stopping the old process before the fence, so no old connection is expected;
  - the runbook's rule to stop if production contradicts Stage 0.2.

**Transactions:** likewise, the wait of the transaction lifetime `T` measured in Stage 0 is what prevents a
transaction it started from committing afterwards.

All of this is about **database writes only**. It says nothing about provider calls (§7).

**Depends on Stage 0 on Atlas:** all of the above (F12). Until then, none of it is relied on for production.

**Depends on Stage 0.5 and 0.6 (Render):**
- that the old process is really gone before the fence. This is the only protection against its background threads'
  provider calls, and needs termination evidence accepted by 0.5 (none is known today); liveness observations never
  qualify;
- that the fenced restart in step 1.7 is harmless and can be deployed out of.

**Depends on operator discipline (the runbook):**
- creating a new user per generation, with only `readWrite` on `reachout` (no other roles, no custom roles, restricted
  to this cluster);
- stopping the old process with termination evidence *before* fencing;
- revoking *before* activating;
- obtaining the fence evidence;
- the transaction-lifetime wait proven in Stage 0;
- closing ingress before fencing, and the external-operation preconditions;
- no campaign or queued email due at the fence;
- never reusing a revoked user for a new release;
- never putting production credentials on a developer machine.

**Not guaranteed:**
- external actions: nothing here can stop or undo an external request already sent, and the database fence doesn't
  stop a live thread's next provider call (§7). Stopping the process does, from Inkwell's side; what was in flight at
  that moment becomes *outcome unknown*. If the process were **not** stopped, the number of unrecorded provider calls
  isn't bounded by the fence: it depends on how each `8b59986` thread handles the failed write afterwards, which isn't
  established;
- Atlas propagation time (undocumented). Stage 0 measures it; the threshold `P + M` is empirical, and a slower future
  propagation can't be excluded;
- Render's behaviour beyond what Stage 0.5 and 0.6 observed (suspension, fenced restart, deploy out of a failing
  state).

## 11. Local development safeguards

**The problem today:** `core/config.py` auto-loads `.env`, and the Mac's `.env` contains the **production**
`MONGODB_URI`. `start.command` then starts a full app, background loops included, against production.

**Proposed** (code, implementation stage 1):
1. **Refuse production unless explicitly allowed:** `APP_ENV != production` with a `MONGODB_URI` whose host isn't
   `127.0.0.1`/`localhost` makes the app refuse to start, unless `INKWELL_ALLOW_PRODUCTION_DB=1` is set *in the real
   environment*, not in `.env`. The refusal message names the host only, never the credentials.
2. **No background loops in development:** `BOUNCE_WATCH` defaults to `0` when `APP_ENV != production`. Turning loops
   on locally is explicit.
3. **No production URI loaded silently:** `.env` loading skips `MONGODB_URI` when it isn't local, unless the
   permission in (1) is given. A warning prints the host only.
4. **Removed from the trust set:** after the first fence, the Mac's copy of `inkwell_g1` is fenced (no write
   privilege). Even an
   old checkout can't write.
5. **Runbook check:** the Step 1 checklist requires the local app to be stopped and `.env` pointed at the local
   database.

## 12. Testing strategy

Tests use real MongoDB with `--auth` and a keyfile replica set, started per test session on a free port (as in this
investigation), with separate processes. Old-release tests use the real `8b59986` code from `git archive` (as in
`tests/test_stage2b_rollback.py`).

| Test | How |
|---|---|
| Old/new overlap, fenced | `8b59986` process (user A) writing in a loop; revoke A; start `a83d17e` (user B); assert every A write after the revoke is `Unauthorized` and B writes succeed; data checked for lost updates |
| Gate transition while writes are in flight | gate-aware process with requests in flight; HOLD; assert new requests get 503 and in-flight ones finish |
| Permit acquisition racing HOLD | 8 processes acquiring admission in a loop while the operator flips HOLD/ACTIVE; assert no write after a revoke of their user |
| Stale permit attempts a write | process admitted, SIGSTOP; HOLD + revoke; SIGCONT; assert `Unauthorized` |
| Open transaction across the fence | start a transaction, run its statements, revoke, commit: **expected to commit** (documents the gap); then the 60 s-wait variant: assert nothing commits after the server's transaction limit (`transactionLifetimeLimitSeconds` lowered in the test server) |
| Crash while holding a permit | SIGKILL; assert leases expire and the next generation admits |
| Background job shutdown | SIGTERM to gunicorn (exec form); assert loops stop claiming, tasks end `cancelled`, `deploy.drained` only when no thread is alive |
| External side-effect uncertainty | fake SMTP that accepts, then fence the process before `set_status`; assert the delivery record stays `sending` → `unknown`, and no automatic resend in the next generation |
| Rollback to `8b59986` | real `8b59986` started with a new user after a fence; data written by `a83d17e` read correctly (existing tests) |
| Forward deployment | `a83d17e` after a fenced `8b59986` |
| MongoDB unavailable during gate checks | stop the test server; assert held (503, no loops); restart; assert re-admitted |
| Unexpected process during HOLD | start a gate-aware process with the current user while HOLD; assert it stays held; start one with a revoked user; assert `Unauthorized` |
| Gate recovery after restart | restart every process; assert admission follows `deploy_state`, with no writes before admission |
| Local safety | start the app with a non-local URI and no permission: refuses; with permission: starts, loops off |

## 13. Implementation stages (after approval)

The stage numbers are used the same way in every document.

| Stage | Change | Needed for the first migration? |
|---|---|---|
| 0 | **No code.** Verifications on the real Atlas cluster and Render (runbook *Stage 0*): the fence behaviour and propagation time, the transaction lifetime, least-privilege roles, other users and triggers, the Render plan, `RENDER_GIT_COMMIT`, the ingress rotation tried on a non-production pair if possible, **Render suspension ending the process (0.5)**, and **a fenced release's restart and the deploy out of it (0.6)**. | **Yes. Fencing is not approved until it passes.** |
| 1 | Local-development safeguards (§11); exec-form `CMD` + `gunicorn.conf.py` drain hooks (§8). | Recommended |
| 2 | Fence test harness (auth + keyfile replica set) and the fence tests from §12 against the real `8b59986`. | **Yes** |
| 3 | Soft gate: `deploy_state` reader, admission, central write wrapper in `core/db`, exemptions list and scan test, 503 + `/readyz`. | No |
| 4 | Delivery state machine: `sending`/`unknown`, no automatic retry. | No |
| 5 | Compatibility declaration: `DATA_CONTRACT` plus the other compatibility dimensions (compatibility matrix), checked at admission. **Proposed, not implemented.** | No |

**Default until Stage 5 exists and has been tested:** every deploy is a credential-fenced deploy (runbook Step 1 shape),
whatever it changes. A matching `DATA_CONTRACT` alone never justifies an overlapping deploy.
