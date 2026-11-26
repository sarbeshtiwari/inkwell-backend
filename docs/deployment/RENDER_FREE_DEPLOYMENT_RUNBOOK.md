# Render Free deployment runbook (credential-fenced)

**Status:** proposal. Nothing has been executed. Every step that changes Atlas, Render or Netlify needs explicit
approval at the time it's done.

> **Credential fencing is not approved for production until Stage 0 passes on the real Atlas cluster** (0.2), **and
> the Render checks pass** (0.5 process termination, 0.6 fenced restart). Until then, don't run Step 1, R or F.

**Principle:**
- A release may write only with its own database user.
- Before the next release may write, the previous user is **fenced** (see *Definitions*), and that is established by the
  *fence evidence* below.
- HTTP ingress is closed before the fence, because some handlers call external providers before any database write.
- **Closing ingress does not stop background threads that are already running, and neither does the database fence.**
  A thread that is alive can still call a provider before its next database write. The old instance is stopped
  before the fence (*Quiescence*), because `8b59986` can't report its own background work. Stopping it counts only
  with **termination evidence**: a post-exit record that passed every Stage 0.5 acceptance condition. A liveness
  observation, such as zero database connections, never qualifies. **Today none is known, so Step 1 is blocked** (Stage 0.5).

**This runbook is the only source of recovery instructions.** The other documents defer to it.

**Names:**

| Name | Meaning |
|---|---|
| `inkwell_g1` | The database user production uses today (in Render's `MONGODB_URI`, and also in the Mac's `.env`). |
| `inkwell_gN` | The user for deployment generation N: unique per generation, never reused. |
| `S1` / `S2` | Proxy secrets. `S1` is the current Netlify proxy secret (`NETLIFY_PROXY_SECRET`, both Netlify sites and Render); `S2` is a new random value. |
| `P` | **Largest observed** fence propagation time in the Stage 0.2 runs: from the role change until every previously open test connection was refused. It is an empirical measurement, **not an upper bound** on future role changes. |
| `M` | Safety margin added to `P` in production: **max(60 s, P)**, so the wait is at least 2·P and at least P + 60 s. `P + M` is an **operational threshold chosen from the measurement**, not a proven maximum. |
| `T` | Transaction lifetime, measured in Stage 0. |
| `t_fence` | The exact time (UTC, to the second) the fence action was saved in Atlas, recorded by the operator. |

## Definitions (used identically in all five documents)

**Fenced user.** A database user is fenced when **all** of these hold:
1. it has **no write privilege on `reachout`**;
2. it has **no other write-capable role and no cluster-wide privilege** (no `readWriteAnyDatabase`, `atlasAdmin`,
   `dbAdmin*`, `clusterMonitor`, custom roles…);
3. its only remaining role, if Atlas refuses an empty role list, is **`read@inkwell_fenced`**, a read-only role on an
   intentionally empty scratch database that holds no application data. That leftover is expected, not a failure.

**Fence evidence.** In production the old connections can't be inspected one by one. Proceeding past a fence requires
all three of these, recorded in the deployment log:
1. **Elapsed time:** now ≥ `t_fence + P + M`.
2. **Fresh-connection probe:** a newly opened connection as the fenced user is refused a write to `reachout` (or to
   `inkwell_fence_test` if `reachout` writes can't be attempted safely), and `connectionStatus` with `showPrivileges`
   shows only what the definition above allows.
3. **Indirect evidence from the old connections, where available:** the fenced release's own log lines show
   authorisation errors on its existing connections. This isn't available when the old process was stopped before the
   fence (Step 1, R, F). A *restarted* fenced release opens new connections, so its errors count as fresh-connection
   evidence only.

**What is enforced and what is empirical:**
- **Server-enforced:** once a connection's session reflects the role change, every write on it is refused.
- **Checked directly in production:** fresh connections are refused (item 2).
- **Empirical:** that **previously open** connections have also been refused by `t_fence + P + M`. This rests on the
  Stage 0.2 measurement on this cluster, plus the margin.
- **Residual uncertainty:** a future role change could propagate more slowly than any observed run (different load,
  topology, proxy or Atlas version). Three runs can't rule that out.

Two measures limit what that uncertainty can do:
- the old process is stopped before the fence (step 1.4), so in the planned case no old connection is open;
- any sign that production contradicts Stage 0.2 stops the migration (*Abort*, *Production contradicts Stage 0.2*).

No next-generation write is allowed until the complete fence evidence has been obtained.

## Stage 0: verification on the real systems

No production data is touched. It uses throwaway users and a scratch database on the production cluster, and each item
needs approval when it's run.

### 0.1 Record the environment (read-only)
- **Atlas:** the cluster tier (M0 / Flex / M10+), topology (replica set members, sharding) and MongoDB version, from
  the Atlas UI. Note whether authentication goes through an Atlas proxy (shared and Flex tiers).
- **Render:** the plan (Free or Starter); whether the service is Blueprint-managed; that `NETLIFY_PROXY_SECRET` is set;
  whether `RENDER_GIT_COMMIT` is available.
- **Atlas, Database Access:** every database user in the project, its roles, and its cluster restrictions. Any user
  other than the app's with write access to `reachout` is outside the fence. Each must be accounted for (owner,
  purpose), disabled, or kept under the same discipline.
- **Atlas App Services** (Triggers, Functions, Data API) and **Atlas Charts:** check whether anything writes to
  `reachout`. Anything that does is another writer the fence doesn't cover.

### 0.2 Atlas fence test (mandatory)

**Script:** a local-only script run from a trusted machine. It reads credentials from a prompt or environment
variable, never prints, logs or saves them, and redacts the URI in any error output. It uses database
`inkwell_fence_test` only.

**Each run:**
1. **Users:** in the Atlas UI or Admin API, create `fence_a` and the control user `fence_b`, each with
   `readWrite@inkwell_fence_test`, restricted to this cluster.
2. **Eight independent connections:**
   - start 8 independent `MongoClient`s as `fence_a`, each with `maxPoolSize=1`, `minPoolSize=1`, and each driven by
     its own thread;
   - for each client, record the server-side connection id (`connectionStatus` / `hello` → `connectionId`) and check
     that the 8 ids are **distinct**;
   - each thread writes `{t, thread, connectionId}` once per second and logs success / `Unauthorized` / other with a
     timestamp;
   - **before the fence:** every thread has at least 5 successful writes, and each still reports its original
     connection id (no reconnects: pool `connectionCreated` events = 1 per client).
3. **Control:** a separate client as `fence_b` writes once per second throughout.
4. **Fence:** at `t_fence` (recorded), remove `fence_a`'s write role through the Atlas UI or Admin API (direct
   `revokeRolesFromUser` changes are rolled back by Atlas).
   - If Atlas refuses an empty role list, replace the roles with `read@inkwell_fenced`. That replacement is then the
     fence action everywhere.
5. **Measure:**
   - for each of the 8 connections, the time from `t_fence` to its first `Unauthorized`, on its **original**
     connection id (a reconnect during the test invalidates that thread's result);
   - `P_run` = the maximum of those 8;
   - after `P_run`, open a **new** client as `fence_a`: its write must also be refused;
   - `fence_b` must have kept writing without errors throughout.
6. **Clean up:** delete `fence_a` and `fence_b`.

**Repeat 3 times.** `P` = the largest `P_run` observed. This is an empirical maximum over these runs only, not a
bound on future propagation.

**Transaction lifetime `T`:**
1. As `fence_c` (`readWrite@inkwell_fence_test`), start a transaction, insert, and keep it open.
2. Fence `fence_c`, wait `P + M`, then try to commit; record the outcome (expected: commits, F6).
3. Repeat, but don't commit: poll every 10 s for the server aborting the transaction; that time is `T`.
4. Try `getParameter transactionLifetimeLimitSeconds` and record the result or the refusal.
5. Delete `fence_c` and drop `inkwell_fence_test` and `inkwell_fenced`.

**Pass criteria:**
- in every run, all 8 original connections are refused, and the fresh connection is refused;
- the control user is unaffected;
- all three `P_run` values (and each connection's time) are recorded. No "stability" criterion is used, since none
  could be defined that turns three samples into a bound. The margin `M` = max(60 s, P) absorbs ordinary variation;
- `T` is known.

If any criterion fails, **credential fencing is not approved**. Stop and report.

### 0.3 Least privilege
- **The app's user needs:** `readWrite@reachout` only (find, insert, update, delete, createIndex, transactions) and
  `hello`/`ping`. No `readWriteAnyDatabase`, `atlasAdmin`, `dbAdmin`, custom roles or other databases. With the soft
  gate (Stage 3), add `read@inkwell_control`.
- **Check a new user:** connect as it and run `connectionStatus` with `showPrivileges: true`. The privileges listed are
  only on `reachout` (and read on `inkwell_control` when used).
- **Check a fenced user:** it matches the *Fenced user* definition. Atlas shows no role except, at most,
  `read@inkwell_fenced`, and `connectionStatus` from a fresh connection shows no write action on any database and no
  cluster privilege.

### 0.4 Ingress rotation (if a non-production pair exists)

Rotate the proxy secret on a test site/backend pair and confirm that requests are refused with 404 once the sites
redeploy. Without a test pair, confirm in step 1.3 by probing before fencing.

### 0.5 Render suspension and process termination (mandatory)

**Why:** Step 1.4 relies on *Suspend* ending the old process. Nothing verified so far says that it does:
- Render documents SIGTERM, then SIGKILL after the shutdown delay, **for deploys**. Its documentation doesn't describe
  what *Suspend* does to a running instance (no documented guarantee is assumed here).
- The Dockerfile uses the shell form of `CMD` (`/bin/sh -c "gunicorn …"`). Whether SIGTERM reaches gunicorn depends on
  whether `/bin/sh` on the image execs gunicorn. **Not verified.**
- If gunicorn never gets SIGTERM, the process ends only if the platform then kills the container. **Not verified for
  Suspend.**

**What is not evidence of process termination:**
- **Render's *Suspended* status:** a control-plane state. It says Render accepted the suspension, not that the
  container has exited.
- **`/healthz` not returning 200:** once suspended, Render's edge answers for the service itself (a suspension notice),
  so this changes even while the container is still running. An app that is merely hung also fails `/healthz`.
- **A quiet or ended log stream:** the log pipeline can stop, and a process that is alive but idle (a campaign thread
  inside a 3600 s delay) writes nothing.
- **Any fixed wait** (30 s, 60 s, or the shutdown delay): it proves nothing about the process.

**Why zero database connections is not evidence of termination either.** Earlier drafts used "no connection
authenticated as the old user remains" as termination evidence. That inference is **invalid**, from the driver's own
code, before any test is run. `8b59986` pins `pymongo==4.18.2` and creates one
`MongoClient(uri, serverSelectionTimeoutMS=8000, appname="reachout", tz_aware=True)` (`core/db.py`): no
`minPoolSize` (default 0), no `maxIdleTimeMS`, no heartbeat setting. In PyMongo 4.18.2:
- **Monitoring connections are not authenticated.** `Topology._create_pool_for_monitor` builds the monitor's pool
  options without credentials, so `Connection.authenticate()` does nothing for them. A process's monitors are never
  attributed to its user, so they can't serve even as a liveness observation.
- **Application connections are lazy.** With `minPoolSize` 0, a connection exists only after an operation needed one.
  Nothing re-creates it while the process is idle.
- **Pools are cleared on errors.** A network error, a primary step-down (Atlas maintenance, an election) or a server
  marked *Unknown* clears the pool (`Pool.reset`). Its connections are closed and re-created only when the next
  operation runs.
- **The server or proxy can close idle connections.** Atlas, its proxy (F12), or the network path can close them,
  independently of the process.

**Concrete counterexample:** the process stays alive. A campaign thread is inside its delay of up to 3600 s, and a
pool clear or a proxy reset closes every authenticated connection. The observation reports zero for that user. The
thread then wakes and sends to its next person *before* its next database write (inventory §3b). The operator would
wrongly conclude that the process had ended.

**What each control proves:**
- **A liveness positive control** (connections visible while the process runs) proves only that the observation method can
  *see* connections. It says nothing about whether a live process can have none.
- **The negative control** below shows whether a live process *can* be observed with zero connections. If it can, the
  method is invalid. If the test fails to produce zero, that proves nothing in the other direction: the driver
  behaviour above already allows it.

So **zero connections is at most a necessary condition**: connections still present mean the process is alive. It is
never sufficient.

**Two different observations.** Stage 0.5 and step 1.4 use two concepts, which are never interchangeable:

| | **Liveness observation** | **Termination evidence** |
|---|---|---|
| Answers | "Is the test process running right now?" | "Have the container's processes exited?" |
| Used for | positive and negative controls, and as a *consistency check* in production (a liveness observation that still shows the process means abort) | the only evidence accepted before the production database fence (step 1.4) |
| Examples (candidates) | the fake provider still receiving calls (test only); the process's database connections; `/healthz`; log lines | a post-exit record from the component that ends the container, documented as emitted only after all its processes have exited |
| Can it alone qualify as termination evidence? | **Never.** Its absence ("no calls seen", "no connections", "no health", "no logs") is not proof of exit | Yes, if it meets the acceptance conditions below |

- **One source, or two?** One documented state source could provide both: a per-instance state that reads "running"
  while the container lives and changes to "exited" only after all its processes end. That needs Render to document
  those semantics. **No such source is documented today** (below), so **two separate sources are assumed**: liveness
  observations for the controls, and a distinct termination record.
- **A post-exit event isn't required to report "alive" first.** An event that exists only after exit can't show
  liveness. Its *positive* control is the opposite: it must be **absent** while the process is demonstrably alive
  (liveness observations show it running), and present after exit.

**Render's own signals, and how they may be used (API reference, checked for this review):**
- **Service-event history** (`GET /services/{id}/events`). This is a log of past events, **not current process state**:
  - `service_suspended` / `suspender_added`: a control-plane action was accepted. Not termination evidence;
  - `instance_count_changed` (`fromInstances` / `toInstances`): a count changed. Whether it means "desired" or
    "exited" isn't documented;
  - `server_failed` (reasons such as `nonZeroExit`, `earlyExit`, `oomKilled`): describes failures, not a requested
    stop;
  - `server_restarted`, `zero_downtime_redeploy_*`: replacements.

  **None is documented as "container processes exited".** An event may be a *candidate* for termination evidence
  only if Render documents or confirms that semantics, and the tests below verify it.
- **The instance-count metric:** a sampled time series (resolution at least 30 s), documented only as "instance count".
  It is not documented as running vs desired, nor for this plan. It is used **only as a recorded observation in the
  tests**, never as liveness or termination evidence, unless those semantics are documented and verified.
- **Documentation of Suspend:** none describing what it does to a running container.

So **no documented Render mechanism is known to give termination evidence.** None is assumed to.

**Required property:** before fencing and activating the next generation, there is **reliable evidence that the old
process can no longer initiate external provider calls**. A candidate is accepted as **termination evidence** only if
**all** hold:
1. **Provenance:** it comes from the component that ends the container, or is documented by Render to be emitted only
   after all the container's processes have exited (documentation, or Render's written answer to a support request,
   recorded).
2. **Termination positive control:** after Suspend, it appears in every run, and only after the fake provider's last
   call.
3. **Negative controls:** it does **not** appear while the process is alive:
   - in normal running;
   - with no database connections (step 2 below);
   - hung (step 3 below).

   Liveness during these controls is established by the fake provider's calls, which must keep arriving.
4. **Agreement:** in every run, it never appears while the fake provider is still receiving calls, and the calls never
   resume after it, until Resume.

**Liveness observations** are tested only as liveness observations:
- the fake provider's log is the ground truth;
- each other liveness observation (connections, `/healthz`, logs) is recorded running, during the negative controls
  and after Suspend;
- one that reports "not alive" while the fake provider still receives calls (expected for connections, `/healthz` and
  logs) is valid for nothing but the production consistency check, where a "still alive" reading means abort.

**Test, on a non-production Render service** with the same relevant configuration:
- the same plan as production (0.1);
- the same Dockerfile and shell-form `CMD`, built from `8b59986`;
- the same health check path;
- the default shutdown delay.

**Test set-up:**
- **Database:** a scratch database `inkwell_render_test`, with throwaway user `render_probe`
  (`readWrite@inkwell_render_test`) and `MONGODB_DB=inkwell_render_test`.
- **Credentials:** no SMTP, Cloudinary, GitHub or LinkedIn credentials, and a random `NETLIFY_PROXY_SECRET`.
- **The test service is not an exact copy of production.** It needs a **controlled background thread** that calls a
  **fake external provider** every few seconds: an HTTP endpoint the operator controls, which logs each call with its
  time and the caller's identifier. The thread must make no database write between calls, mimicking a campaign thread.
  It is added only to a test-only build of `8b59986` on a throwaway branch, never merged or deployed to production
  (that build needs its own approval). The fake provider's log is the **ground truth** for "can this process still
  call a provider".

**Steps:**
1. **Observation methods.** Record which of these work on this tier:
   - **liveness observations:**
     - connections: `$currentOp` with `allUsers: true, idleConnections: true`, filtered by user / `appName` (needs
       `inprog`), the Real-Time Performance Panel, or the per-host connections metric;
     - `/healthz` and log lines;
     - the fake provider (ground truth);
   - **termination-evidence candidates:** Render service events, and anything Render documents or confirms;
   - **recorded only:** the instance-count metric.

   **Liveness positive control:** with the service running, the fake provider is receiving calls, and each liveness
   observation shows the process. **Termination negative control (normal running):** no termination-evidence candidate
   appears.
2. **Negative control: alive, with no database connections.** Keep the process running, and make its authenticated
   connections disappear without stopping it, in one of these ways:
   - kill its connections on the server side (`killOp` / `killAllSessionsByPattern` for `render_probe`, where the
     tier allows it);
   - or force a pool clear from a test endpoint in the test build, with no database operation after it;
   - or, in a separate test Atlas project only, briefly remove Render's egress from the IP access list.

   Then record:
   - whether the connection observation reports **zero** for `render_probe`;
   - whether the fake provider **keeps receiving calls** (liveness ground truth);
   - whether any termination-evidence candidate appears (it must not);
   - the instance-count metric (recorded only).

   Expected from the driver behaviour: zero connections while calls continue. That result is recorded as **the
   connection observation being unusable as termination evidence** (it was never eligible: it is a liveness
   observation).
3. **Negative control: alive but hung.** Make the process stop answering HTTP (a handler that blocks all request
   threads) while the background thread keeps calling. Record what `/healthz` and the other liveness observations
   report, and whether any termination-evidence candidate appears (it must not).
4. **Suspend (termination positive control).** Record:
   - the time *Suspended* appears;
   - every Render event, with its time, and the metric;
   - whether each termination-evidence candidate appears, and when, relative to the fake provider's last call;
   - whether gunicorn logged `Handling signal: term` / `Shutting down: Master`;
   - **the time of the fake provider's last call**;
   - whether the calls ever resume while suspended.
5. **Suspend a busy process.** Hold a long request open and keep the background thread calling, then Suspend. This
   covers SIGTERM not reaching gunicorn, where only a platform kill could end it.
6. **Resume.** Record which commit and environment Resume starts, and that the fake provider's calls restart (liveness
   positive control again).
7. **Clean up:** delete `render_probe`, the scratch database and the fake provider's endpoint. Delete the test service,
   or keep it suspended for 0.6.

**Pass criteria (all):**
- in every Suspend run, including the busy one, the fake provider receives no call after the process's end and none
  until Resume;
- **at least one termination-evidence candidate available in production meets all four acceptance conditions
  above.** The fake provider itself isn't available in production, because the real `8b59986` has no such thread; it
  is a liveness ground truth for the test only.
- no liveness observation is counted towards this criterion.

**If no candidate is accepted as termination evidence:**
- **Stage 0.5 fails.** Process termination can't be proven for `8b59986` in production, and **the first migration must
  not proceed with this procedure.**
- Fake-provider results from the test service alone don't change that. They show how Suspend behaved in those runs,
  not that a given production Suspend ended a given process.
- Any alternative needs its own design and approval, for example:
  - a stop mechanism that produces its own evidence;
  - provider-side revocation of the credentials the old process holds;
  - an explicit, documented risk acceptance by the owner.

**Status today:** no Render signal is documented to meet condition 1 (provenance). **Stage 0.5 is expected to fail unless Render
documents or confirms one.** This is a blocking item.

### 0.6 Restart of a fenced release, and deploying out of that state (mandatory)

**Why:** Step 1.7 resumes `8b59986` with its **fenced** user, and only then deploys `a83d17e`.
- **`8b59986` writes during import, before serving:** `app.py` calls `init_storage()` at import, which runs
  `create_index` on `reachout` collections and then `rbac.bootstrap()`.
- **Expected with a fenced user, not verified:**
  - `M.connect()`'s `ping` succeeds;
  - the first `create_index` fails with `Unauthorized`;
  - nothing catches it there, so importing `app` fails;
  - gunicorn reports *Worker failed to boot* and exits;
  - the background loops are never started (they start later in `app.py`).
- **So the resumed old release probably never reaches its health check.** It may restart repeatedly.
- **Unknown:** what Render does with a resumed service that fails, and whether it then accepts the next deploy.

`a83d17e` also creates indexes at import, so the same applies wherever a fenced `a83d17e` is resumed (Step R,
*Abort* Case B).

**Test, on the 0.5 test service** (or a second one with the same configuration), with a fenced throwaway user
`render_fenced`. Fence it with the Stage 0 fence action, and wait for its fence evidence before the test.
1. **The fenced old release:** with the service suspended, set `MONGODB_URI` to `render_fenced` and Resume (commit
   `8b59986`). Record:
   - the start-up log: the expected `Unauthorized` and boot failure, or something else;
   - whether any write to `inkwell_render_test` happened (there must be none);
   - whether any background loop logged activity;
   - `/healthz`;
   - the service and deploy status Render shows;
   - whether Render keeps restarting it.
2. **Deploying out of that state:** while it is failing, do exactly the single change of step 1.7.2: set the URI to an
   unfenced throwaway user `render_new`, and deploy a test commit of `a83d17e` with `MONGODB_DB=inkwell_render_test`.
   Record:
   - whether the Dashboard allows it;
   - whether the deploy reaches *Live*;
   - whether anything of `8b59986` still runs alongside: its accepted 0.5 termination evidence, plus liveness
     observations (the fake provider, if the test build is used).
3. **The fenced new release:** repeat 1 with a fenced user and commit `a83d17e`. This covers Step R and Case B.
4. **Recovering a failing resumed service:** with it failing as in 1, give it an *unfenced* user and use the exact
   action the Case A abort would use (*Manual Deploy* of the same commit, or *Restart*). Record whether it recovers.
5. **Clean up:** delete the throwaway users, the scratch database and the test service.

**Pass criteria:**
- the fenced release makes no writes and starts no loops;
- Render permits the single deploy of step 1.7.2 out of that state, and the new release reaches *Live*;
- the recovery action of step 4 works.

If any of these fails or can't be observed, **Step 1.7 is blocked**. The order of production operations is not
changed on guesswork; a revised sequence needs its own verification and approval.

### Stage 0 result

Stage 0 passes only if 0.2, 0.5 and 0.6 all pass and 0.1 and 0.3 are recorded.

## Preconditions for Steps 1, R and F (all must be true)

**Systems:**
- [ ] Stage 0 passed, with recorded `P`, `M`, `T` and the fence action, the accepted 0.5 termination evidence (with its
      provenance record and control results) and the liveness observations used for consistency checks, and the 0.6 results for the fenced restart and the deploy out of it.
- [ ] A quiet time is chosen, and users are told about a full outage of several minutes.
- [ ] **The Mac:** no app process running (no `start.command`, no Python with `app` imported); `.env` has no production
      URI.
- [ ] No restore or copy tool is running. No App Services trigger writes to `reachout` (Stage 0.1).

**External operations, from the write-path inventory** (§3, §3a and §3b there).
- **Before the window:** these checks only make it *unlikely* that anything is running.
- **What prevents new operations:** closing ingress (step 1.3) for HTTP-started ones, and stopping the old instance
  (step 1.4, with the 0.5 termination evidence) for background ones.
- **What the database fence does *not* prevent:**
  - a provider call made by a live thread before its next database write. Campaign threads send, *then* record, and can
    wait up to 3600 s between people. Apply threads submit, *then* record;
  - a send-queue item already claimed (`status: sending`) before the fence: its provider call needs no further write.

  So the fence is never a substitute for stopping the process.
- **Three states:**
  - *not started*: prevented by the two measures above;
  - *in flight*: nothing can stop it; it finishes or dies with the instance;
  - *outcome unknown*: listed for follow-up in step 1.10, and **never retried automatically**.
- [ ] **Campaign email / WhatsApp:** nothing running (Activity page, or a read-only check: no campaign running).
      WhatsApp is off in production (`WHATSAPP_ENABLED=0`), but still check.
- [ ] **Queued email and scheduled LinkedIn posts** (both go through `queue`: kinds `email` and `linkedin_post`; the
      send-queue loop claims each item with a write, then calls the provider): no `queue` item with `status: queued`
      due before the window ends + 1 h, and none `status: sending` (read-only query). If there is one, reschedule it
      with the owner, or wait.
- [ ] **Reply sending:** none in progress (no user active; ingress is closed in step 1.3).
- [ ] **Job application submissions:** no auto-apply run in progress, and no handover form open.
- [ ] **LinkedIn "post now":** user-initiated (rate-limit write first, then the LinkedIn call); prevented by closing
      ingress. Scheduled posts are covered by the queue check above.
- [ ] **GitHub writes** (commit, branch, PR, merge, issue, comment, fork, portfolio publish): no user active; these call
      GitHub **before** any database write, so **only closing ingress prevents them**.
- [ ] **Cloudinary uploads/deletes** (documents, site images, account deletion): no user active; uploads happen before
      the database write, so **only closing ingress prevents them**.
- [ ] **OTP / login / lead / contact-form emails, web push:** user-initiated; closing ingress prevents new ones.
- [ ] **Other provider calls** (LLM, job boards, IMAP reads): read-only towards the provider; no action needed.

## Step 1: first migration, `8b59986` (user `inkwell_g1`, secret `S1`) → `a83d17e`

1. **Create `inkwell_g2`:**
   - `readWrite@reachout` only, restricted to this cluster, with a generated password kept in a password manager;
   - check it with `connectionStatus` (Stage 0.3);
   - it is not used by anything yet, so there is no downtime.
2. **Prepare:**
   - generate `S2`;
   - `a83d17e` is committed locally and **not pushed** (Render's `autoDeploy` deploys on push).
3. **Close ingress.** ⏱ **The outage starts here.**
   - In Netlify, set `NETLIFY_PROXY_SECRET = S2` on **both** sites, then redeploy each site's current commit (no
     repository change).
   - **Prove it:** through each Netlify site, an `/api/me` request returns **404**.
   - **This stops new HTTP requests only.** Requests already inside the old backend, and every background thread
     (loops, campaign and apply threads started earlier), keep running.
4. **Quiescence: stop the old instance.** `8b59986` can't report its own background work (see *Quiescence* below), so
   the safe, observable way to end all of it is to stop the process.
   - **Before suspending:**
     - **liveness observation:** `inkwell_g1` has connections from Render (0.5 connection observation), so the process
       is running;
     - **termination evidence absent:** the accepted 0.5 termination evidence has not appeared for the current
       instance. If it already has, the record is stale or misread, so stop.
   - Render → *Suspend* the service.
   - **Termination evidence (required before step 5):** the termination evidence accepted in Stage 0.5 appears for this
     instance, after the suspension, exactly as in the passing 0.5 runs. **Nothing else qualifies.**
   - **Consistency checks** (can only stop the migration, never allow it):
     - Render shows the service *Suspended*;
     - no liveness observation still shows the process: no `inkwell_g1` connection from Render remains. A connection
       still present means the process is alive, so abort. Zero proves nothing on its own.
   - **Not termination evidence:** any liveness observation's absence (zero connections, `/healthz` failing (Render's edge answers for a suspended service), a
     quiet or ended log stream), service-event history or the instance-count metric without the semantics verified in
     Stage 0.5, or any elapsed time.
   - Whether SIGTERM reaches gunicorn through the shell-form `CMD` is recorded in 0.5, not assumed. Either way, daemon
     threads die with the process, **mid-step if they were busy**: their external outcome may be unknown (step 1.10).
   - **If the evidence isn't obtained:** quiescence is unproven. **Don't fence.** Use *Abort before the fence*.
5. **Fence `inkwell_g1`.**
   - Apply the Stage 0 fence action in Atlas, and **record `t_fence`**.
   - This also covers every other process holding `inkwell_g1`, such as a laptop.
6. **Fence evidence:**
   - elapsed time ≥ `t_fence + P + M`;
   - a fresh-connection probe as `inkwell_g1` is refused, and `connectionStatus` matches the *Fenced user* definition;
   - (indirect evidence from the old connections isn't available, because the process was stopped in step 4, which is
     expected. `P + M` still matters, for any *unknown* holder of `inkwell_g1` that is still connected.)
   - `8b59986` uses no transactions, so no `T` wait is needed.
7. **Resume the old release fenced, then deploy the new one with the new user.** A suspended service can't be
   deployed (deploy hook → 409), and *Resume* restarts the **last deployed** release (`8b59986`) with whatever
   environment is saved. So `inkwell_g2` must never be in the environment when resuming.
   1. Leave `MONGODB_URI` = the **fenced** `inkwell_g1` string. Set `NETLIFY_PROXY_SECRET = S2` (Render), then
      **Resume**. The resumed `8b59986` can't write (fenced, proven in step 6) and gets no signed traffic.
      - **Its start-up writes fail.** `init_storage()` runs `create_index` at import; with the fenced user it is
        expected to fail with `Unauthorized`, and the import with it, before any background loop starts.
      - **So the resumed instance is expected not to boot or pass its health check**, possibly restarting repeatedly.
        This is expected. It is safe only because the user is fenced.
      - What Render does with it, and whether step 1.7.2 is then accepted, is what Stage 0.6 verified. **Run this step
        only exactly as Stage 0.6 passed it.** If the production behaviour differs from 0.6 (for example Render refuses
        step 1.7.2), stop, and use Abort Case A if its proof holds, otherwise Case B.
   2. In **one** change, set `MONGODB_URI` = the `inkwell_g2` string and deploy `a83d17e`: edit the environment with
      *Save, rebuild and deploy* while choosing commit `a83d17e`, or save the environment without deploying, then
      *Manual Deploy → `a83d17e`*. **Verify** which of these the Dashboard offers before the window; if saving the
      environment alone triggers a deploy of `8b59986`, use the first form only.
   3. **The only process that ever gets `inkwell_g2` is `a83d17e`.** During this deploy, the fenced `8b59986` and the
      new `a83d17e` overlap for ~60–90 s; the old one can't write.
8. **Verify:**
   - Render shows the deploy *Live* with commit `a83d17e`;
   - through Netlify, `/healthz` and `/api/me` work;
   - sign-in works;
   - a template save returns 200, and its record has an integer `version` ≥ the server time in ms and a 32-character
     hex `wid`;
   - the logs show no `Unauthorized`, `VersionCorrupt` or `ALERT` lines from the new instance.

   ⏱ **The outage ends here.**
9. **The fenced `8b59986` instance resumed in step 1.7.1** (if it is still restarting) is replaced by the new deploy.
   - Its log lines should show only authorisation errors (expected at start-up), never successful writes.
   - It opened **new** connections, so this is fresh-connection evidence only, not evidence about the original ones.
   - Consistency check: no `inkwell_g1` connection remains after the new deploy is *Live* (zero is necessary, not proof
     of anything).
10. **Unknown external outcomes:** list anything that may have been interrupted at step 4, and check each with the
    provider:
    - campaign or reply email: the Sent folder (by Message-ID where available);
    - campaign WhatsApp: off in production (`WHATSAPP_ENABLED=0`); if it was on, the WhatsApp chat;
    - queued email and scheduled LinkedIn posts: `queue` entries still `sending`, then the Sent folder or the LinkedIn
      profile;
    - LinkedIn "post now": the LinkedIn profile's recent posts;
    - applications: the `ai_applied` state vs the job site or confirmation email;
    - GitHub writes and portfolio publish: the repo, branch or PR;
    - Cloudinary uploads/deletes: the media library;
    - OTP / login / lead / contact-form emails: no follow-up (the person repeats the action themselves);
    - web push and in-app notifications, syncs, scans, digests, briefings: cosmetic or read-only (inventory §3b), no
      follow-up.

    Tell the affected user. **Never resend automatically.**
11. After 24 h, delete `inkwell_g1`.

### Quiescence: why the old instance is stopped, not watched

`8b59986` has no task registry, no `operations` collection and no delivery records. Its background work is tracked in
process memory only, and threads it started earlier keep running after ingress closes:
- campaign threads with gaps of up to 3600 s between messages;
- apply threads (a browser per job, no deadline);
- syncs (≤ 240 s per account under `with_deadline`, but the thread continues after the deadline);
- the 8 loops.

From outside, the only observable states are "running somewhere" and "process gone". So:
- the pre-window checks (Activity page, `queue` `sending` items, no apply run) **reduce the chance** something is
  interrupted;
- **only stopping the process positively ends all of it**, and only with the termination evidence of step 1.4: a
  post-exit record accepted by Stage 0.5 (provenance, termination positive control, negative controls, agreement with
  the fake provider). A liveness observation (database connections, `/healthz`, logs) never qualifies, because a live
  process can show none of them (Stage 0.5);
- the database fence doesn't replace this. A live thread can still reach a provider before its next write.

Anything interrupted is handled as **unknown** (step 1.10), never retried automatically.

### Abort during Step 1

**Rule:** never give a generation its write role back while another generation might still be able to write.

**Abort before the fence (steps 1.3–1.4, `inkwell_g1` not yet fenced).** Use it, for example, when step 1.4's termination
evidence isn't obtained.
1. Leave `inkwell_g1` unchanged; delete `inkwell_g2` (never used).
2. If the service is suspended, *Resume* it (it restarts `8b59986` with `inkwell_g1`). If the old process never
   stopped, it is still running with `inkwell_g1`; nothing else writes.
3. Netlify: `NETLIFY_PROXY_SECRET = S1`, and redeploy both sites. Render still has `S1`, because it is changed only
   in step 1.7.1.
4. Verify through Netlify: `/api/me` and sign-in work, and the running commit is `8b59986`.
5. Do step 1.10 for anything interrupted by a suspension.
6. Report. Termination evidence that fails in production, after Stage 0.5 passed, means the 0.5 result doesn't hold
   for production. Don't retry Step 1 until that is understood.

**Production contradicts Stage 0.2 (after the fence, before `inkwell_g2` is used):**
- **Triggers:**
  - at `t_fence + P + M` the fresh-connection probe still writes, or `connectionStatus` still shows a write privilege;
  - or any log shows a successful `inkwell_g1` write after `t_fence`.
- **Then:**
  - don't use `inkwell_g2`. Measured `P + M` is an empirical threshold, and this run exceeded it;
  - keep the outage, re-probe, and record the times; never "wait a bit longer and proceed";
  - use **Case A**: `inkwell_g2` is unused, so its proof holds;
  - treat the Stage 0.2 result as not holding for production. Re-run Stage 0.2 before any further fence.

**Case A: the new generation never started.**
- **Proof needed (all of these):**
  - no deploy of `a83d17e` reached *Live* or "starting service" (Render deploy list: none after step 1.6, or *build
    failed / cancelled* before any instance started);
  - **and** `inkwell_g2`'s URI was never saved into the environment of any instance that started;
  - **and**, if in doubt, Atlas shows no connections or authentications by `inkwell_g2`.
- **If any of these can't be shown, use Case B.**
- **Steps:**
  1. Fence `inkwell_g2`, or delete it.
  2. Give `inkwell_g1` back `readWrite@reachout` (and remove `read@inkwell_fenced`, if used).
  3. Probe that `inkwell_g1` can write.
  4. Render: `MONGODB_URI` = the `inkwell_g1` string and `NETLIFY_PROXY_SECRET = S1`.
     - If the service is suspended, *Resume* it (it restarts `8b59986`, the last deployed release).
     - If it was already resumed fenced (step 1.7.1) and is failing, use the recovery action verified in Stage 0.6
       step 4.
  5. Netlify: `NETLIFY_PROXY_SECRET = S1` and redeploy both sites.
  6. Verify through Netlify:
     - `/api/me` and sign-in work;
     - the running commit is `8b59986`;
     - only `inkwell_g1` has a write role on `reachout`.

**Case B: the new generation may have started.**
1. **Keep `inkwell_g1` fenced.**
2. **Close ingress and stop the new instance first**, as in steps 1.3–1.4. From step 1.7.1 Render has `S2`, so a live
   `a83d17e` may be serving.
   - Set a new secret `S3` on both Netlify sites, and prove it with 404.
   - *Suspend*, and obtain step 1.4's termination evidence for the new instance.
   - If that evidence isn't obtained, still fence `inkwell_g2` (Case B step 3): this protects the data. But don't continue past
     Case B step 6. Its live threads may still reach providers, so report and keep the outage.
3. **Fence `inkwell_g2`**, and record `t_fence2`.
4. **Fence evidence for `inkwell_g2`:**
   - elapsed ≥ `t_fence2 + P + M`;
   - a fresh-connection probe is refused and `connectionStatus` matches the definition;
   - the new instance's logs show authorisation errors on its existing connections, if it was still running (if step
     2 stopped it, this item isn't available).
5. **Wait `T`** after `t_fence2 + P + M`: `a83d17e` uses transactions.
6. **Check `inkwell_g2` can't write:** a fresh probe is refused, and in Atlas no user except the operator's has a write
   role on `reachout`.
7. **Give `inkwell_g1` back `readWrite@reachout`.**
8. **Restore the release**, in step 1.7's shape:
   - **If suspended:** resume with the **fenced** `inkwell_g2` URI. A fenced `a83d17e` is expected not to boot
     (Stage 0.6 step 3).
   - **Then, in one change:** `MONGODB_URI` = the `inkwell_g1` string, `NETLIFY_PROXY_SECRET = S1`, and deploy
     `8b59986` (Render *Rollback* to its last successful deploy).
   - Netlify: `NETLIFY_PROXY_SECRET = S1`, and redeploy both sites.
9. **Check that only the old generation can write:**
   - an `inkwell_g1` probe writes OK; an `inkwell_g2` probe is refused;
   - Render shows `8b59986` *Live*;
   - the `a83d17e` instance is terminated;
   - sign-in works through Netlify.

   Then do step 1.10 (unknown external outcomes), and delete `inkwell_g2` after 24 h.

## Step R: rollback `a83d17e` (gen N, user `inkwell_gN`) → `8b59986`

1. Check the preconditions.
2. Create `inkwell_gN+1`: `readWrite@reachout` only; check it.
3. **Close ingress:** new secret, both Netlify sites redeployed, prove with 404.
4. **Quiescence:** `a83d17e` records its tasks in `operations`.
   - Check through the read-only database view that none is `running`, and that no `queue` item is `sending`.
   - Then **Suspend** the service, and obtain step 1.4's termination evidence. Stopping
     is still required, because loops and in-flight HTTP threads aren't all in `operations`. A claimed queue item or a
     live campaign thread could still reach its provider after a fence.
   - If the evidence isn't obtained, use *Abort before the fence*.
5. **Fence `inkwell_gN`:**
   - record `t_fence`;
   - fence evidence (elapsed ≥ `t_fence + P + M`, fresh probe, definition check);
   - then **wait `T`** (`a83d17e` uses transactions).
6. **Deploy, as in step 1.7:**
   - resume with the fenced URI. A fenced `a83d17e` is expected to fail at start-up (its index creation is refused):
     proceed only as verified in Stage 0.6 step 3;
   - then, in one change, `MONGODB_URI` = `inkwell_gN+1`, `NETLIFY_PROXY_SECRET` = the new secret, and deploy
     **`8b59986`** (Render *Rollback*). **Never `1d544b7` or `5c21025`.**
7. Verify as in step 1.8. The `8b59986` save drops `version`/`wid`, which is expected.
8. Do step 1.10.
9. **Abort:** as Step 1's abort, with `inkwell_gN` in the old generation's role and `inkwell_gN+1` in the new one's.

## Step F: forward again after a rollback

Exactly Step 1 (including quiescence by suspension and step 1.7's order), with the `8b59986` generation's user in place of
`inkwell_g1`.

## Later deploys

- **Until Stage 5 (compatibility declaration) is implemented and tested:** **every deploy uses Step 1's fenced
  procedure**, whatever it changes, including same-schema and "docs only" backend deploys.
- **After Stage 3 (soft gate) and Stage 5:** a pair of releases that is proven compatible across *all* dimensions
  (`DEPLOYMENT_COMPATIBILITY_MATRIX.md`) may use the shorter held-then-activate flow (architecture §9b). It still
  creates a new user per generation and fences the old one before activation.
- **A matching `DATA_CONTRACT` alone never permits overlap.**

## Standing rules
- One unique database identity per deployment generation; never reused; never on a developer machine.
- New users get only `readWrite@reachout` (plus `read@inkwell_control` with Stage 3), restricted to this cluster.
- Never deploy `1d544b7` or `5c21025` after `5ba9022` has written.
- Local development uses the local MongoDB replica set only. Production URIs live only in Render's environment and the
  password manager.
