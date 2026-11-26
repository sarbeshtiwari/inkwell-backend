# Deployment failure analysis

**Status:** credential fencing is **not approved for production** until runbook Stage 0 passes: the Atlas fence test
on the real cluster (0.2), and the Render checks for process termination (0.5) and fenced restart (0.6). Entries
marked **[Stage 0]** hold only once it has passed.

**Recovery and abort steps** are defined only in `RENDER_FREE_DEPLOYMENT_RUNBOOK.md`. This table points to them.

**Terms:**
- **Fence / fenced user:** as defined in the runbook's *Definitions*: no write privilege on `reachout`, no other
  write-capable or cluster-wide role, at most `read@inkwell_fenced` left.
- **Ingress closed:** the Netlify proxy secret is rotated, so the backend refuses every request before any handler runs.
- **Termination evidence:** runbook step 1.4. A post-exit record accepted by Stage 0.5 (provenance, termination
  positive control, negative controls, agreement with a fake provider).
- **Liveness observation:** evidence the process is running (database connections, `/healthz`, logs; the fake
  provider in tests). Used for controls and consistency checks only. Its absence, zero database connections
  included, is **never** termination evidence. **None is known today**, so Stage 0.5, and the first migration, are
  blocked.
- **`P` / `T`:** fence propagation time / transaction lifetime, measured in Stage 0.
- **Gen:** a deployment generation, with one database user per generation.

**Release scope labels used in every row:**
- **All releases:** behaviour of MongoDB, Atlas, Render or the procedure, whatever code runs.
- **`8b59986`:** the deployed release. It has in-memory locks only; no shared leases, no `operations` task records, no
  transactions, no versioned records, no soft gate, and no `PyMongoError` handler (database errors on `/api/` reach the
  generic handler: HTTP 500).
- **Step 2 releases (`5ba9022` / `a83d17e`, implemented, not deployed):** shared leases (`locks`), `operations` task
  records, schedule slots, transactions under the account lock, versioned CAS, and a `PyMongoError` handler (HTTP 503).
  They are **not** gate-aware.
- **Gate-aware releases (proposed, Stage 3; none exists):** read `deploy_state` and hold (HOLD). The delivery state
  machine is Stage 4, also proposed.
- **[Stage 0]:** depends on Stage 0 proving the Atlas or Render behaviour.
- **[empirical P]:** relies on the measured threshold `P + M` (largest observed propagation plus margin), not on a proven
  bound. Server-enforced authorisation itself is not empirical: once a role change reaches a connection, its writes
  are refused.

| # | Case | Expected behaviour | Business writes allowed? | Fails closed? | Recovery | Operator? |
|---|---|---|---|---|---|---|
| 1 | New release starts while old is still running | **All releases:** Render overlaps old and new on every deploy (F1). **First migration (`8b59986` → `a83d17e`):** ingress closed, old process stopped with termination evidence **[Stage 0.5, blocking]**, old user fenced with fence evidence **[Stage 0, empirical P]**, all before the new one is deployed. The `8b59986` resumed fenced in step 1.7.1 is expected to fail at import (`create_index` refused) before starting any loop **[Stage 0.6]**, so the new release is the only writer. **Later deploys:** the same procedure until Stage 3 + Stage 5 exist. **Gate-aware releases (proposed):** the new one boots held until the old is fenced and the new one activated. | Old: no (fenced) **[Stage 0, empirical P]**. New: after the fence evidence. | Yes for data, if the runbook order is kept **[Stage 0]** | n/a | Yes (runbook) |
| 2 | Old release crashes during the deployment | All work hosted inside the crashed process stops writing and calling providers. This does not establish termination of detached or independently hosted workers. Anything in flight becomes *outcome unknown* (runbook step 1.10). **`8b59986`:** it has no shared leases or task records; its in-memory locks and threads vanish with it, and nothing records what was interrupted. **Step 2 releases:** its leases expire (≤ `LOCK_LEASE_SECONDS`, 30 s) and its `operations` records read `interrupted`. | Old: no (gone). New: per the runbook (after the fence). | Yes for data | Step 1.10 for unknown outcomes | Sometimes |
| 3 | New release crashes during startup | **All releases:** Render keeps running the previous deploy, if it is running (F1). **First migration:** the previous deploy is the fenced `8b59986`, which is itself expected not to boot **[Stage 0.6]**, and ingress is closed, so it's a full outage until resolved. **`8b59986` and `a83d17e`:** `init_storage()` runs at import and only its `connect()` is guarded, so a failing start-up write fails the import and the instance doesn't serve. | None during the outage | Yes | Fix forward, **or** runbook *Abort*: **Case B** if any new instance reached starting or `inkwell_g2` was saved in Render's environment (stop it, fence the new user, wait `T`, then restore the old); **Case A** only with the proof listed there. | Yes |
| 4 | Deployment gate (`deploy_state`) unreadable | **Gate-aware releases only (proposed; none exists):** treated as HOLD: no new work, mutations get 503; in-flight work held within ≤ 5 s. **`8b59986` and Step 2 releases:** don't read `deploy_state`; not applicable. | No (gate-aware); n/a otherwise | Yes (gate-aware) | Gate-aware: restore read access to `inkwell_control`, or create the document | Yes |
| 5 | MongoDB unavailable | **All releases:** writes fail, and `/healthz` returns 503 (its `ping` fails). **`8b59986`:** an `/api/` request that hits the error gets the generic handler's **500**, unless the handler catches it itself; how each background loop handles the error isn't audited here. **Step 2 releases:** the `PyMongoError` handler answers **503**; leases can't renew, so holders stop at their next `ensure()`; task records settle as `interrupted`. **Gate-aware (proposed):** the gate read fails, so the process is held. | No | Yes for data | Automatic when Atlas returns (Step 2: leases re-acquired; gate-aware: gate re-read) | No (only if Atlas is down for long) |
| 6 | Permit/lease holder crashes | **`8b59986`:** locks are in process memory and vanish with it; no shared lease exists. **Step 2 releases:** the lease expires (`LOCK_LEASE_SECONDS`); the next holder takes over with a higher fence; the task record reads `interrupted`. **Gate-aware:** its permit lapses at the next gate read. | Yes for others (Step 2: after expiry) | Yes | Automatic | No |
| 7 | Permit/lease holder becomes unresponsive | **`8b59986`:** no shared lease; another *process* is not coordinated with it at all (a reason it is never overlapped). **Step 2 releases:** it stops renewing; its lease expires; its later writes under the account lock are refused (transactional lease check), and versioned writes are refused by CAS. **All releases, across a deployment:** after the fence evidence, the fence refuses all its database writes **[Stage 0, empirical P]**, but not its provider calls; only stopping the process ends those. | Others: yes (Step 2: after expiry). Stale holder: no (Step 2; fenced). | Yes for data | Automatic | No |
| 8 | Request outlives its permit or lease | **All releases:** no request-duration bound exists (gunicorn's `--timeout 120` is a worker heartbeat, not a request limit, with threaded workers). Across deploys the fence bounds its database writes **[Stage 0, empirical P]**, and stopping the process **[Stage 0.5, blocking]** bounds its life. **`8b59986`:** no lease to outlive. **Step 2 releases:** under `ws.lock` the transactional lease check refuses the write (`LeaseLost`, 503). **Gate-aware (proposed):** an in-flight request keeps its admission until it finishes. | No (stale) | Yes for data | Client retries | No |
| 9 | Background task runs longer than expected | **All releases:** the fence refuses its database writes only after the fence evidence (elapsed ≥ `t_fence + P + M`, fresh probe refused, definition check), **not immediately** **[Stage 0, empirical P]**. It **cannot** stop a provider call made before the task's next database write: a live campaign thread sends then records, an apply thread submits then records, and a claimed queue item is sent without a further write. So the runbook **stops the process** (step 1.4) before fencing. Anything in flight at that moment becomes *outcome unknown*. **`8b59986`:** no registry; nothing records the interruption. **Step 2 releases:** the lease renewer keeps its lease; `operations` reads `interrupted` after the process stops. **Gate-aware (proposed):** HOLD stops it at its next safe point; with the delivery state machine (Stage 4, proposed) the item is recorded `unknown`. | Until the fence evidence (data); provider calls until the process stops | Yes for data; **no for external effects** | Step 1.10: list unknown outcomes, never resend automatically | Sometimes |
| 10 | Deployment cancelled | **All releases:** depends on whether the old user was fenced, and whether the new generation could have started. | Only the generation that is unfenced after the abort | Yes | Runbook *Abort*: *before the fence* (nothing fenced yet), **Case A** (proof that the new one never started) or **Case B** (otherwise). Never restore the old generation's roles while the new one may still write. | Yes |
| 11 | Rollback to `8b59986` | **All releases:** close ingress; stop the current instance with termination evidence **[Stage 0.5, blocking]** (`a83d17e`'s `operations` and `queue` checked first); fence the current gen with full fence evidence **[Stage 0, empirical P]**; wait `T` (Step 2 releases use transactions); resume fenced (a fenced `a83d17e` is expected not to boot, **[Stage 0.6]**); then deploy `8b59986` with a **new** user and secret. **`8b59986`:** can't be held (no gate) and writes at import, so it must never be deployed before the fence. | Only `8b59986`, after deploy | Yes for data **[Stage 0]** | Runbook §R (its abort is the same as Step 1's) | Yes |
| 12 | Forward deployment after rollback | Same as the first migration: close ingress, stop the `8b59986` instance with termination evidence, fence its user, resume fenced, then deploy **[Stage 0]**. | New release only, after deploy | Yes for data **[Stage 0]** | Runbook §F | Yes |
| 13 | Unexpected second instance starts (a Render restart overlapping, a stray laptop, a second service) | **All releases:** with old or fenced credentials, its writes are refused **[Stage 0, empirical P]**; its provider calls before any write are not. With the current gen's credentials it is **not** fenced. **Step 2 releases, same release twice:** the safety rests on Step 2's multi-process design (tested, not run in production). **`8b59986` twice:** unsafe (in-memory locks only). **Different release with current credentials:** unsafe (operator mistake). | Same Step 2 release: yes (designed for it). Otherwise: **not prevented**. | Only if credentials are per release | Rule: one database user per (generation, release); never copy production credentials elsewhere. | Prevention |
| 14 | Local development instance tries to connect to production | **All releases, after the first fence:** its old user is fenced, so every write is refused **[Stage 0, empirical P]**; any provider call it makes before writing is not. **Today (any release):** the Mac's `.env` holds the production URI, so it is a writer like any other (runbook precondition: stopped). **With the proposed local safeguards (Stage 1, not implemented):** it would refuse to start against a non-local URI without `INKWELL_ALLOW_PRODUCTION_DB=1`, with loops off in development. | No (after the fence) | Yes, for credentials fenced or never issued; **not** if a laptop is given the current generation's credentials (operator mistake) | Remove production URIs from `.env`; use a local database. | Yes (one time) |
| 15 | Gate stuck in HOLD | **Gate-aware releases only (proposed; none exists):** they serve reads, refuse mutations (503) and run no loops; the fence state is unaffected. **`8b59986` and Step 2 releases:** not applicable (no gate). | No (gate-aware) | Yes (gate-aware) | Gate-aware: the operator sets `deploy_state` `ACTIVE` for the current gen (after checking that the current user is the only one with write roles). | Yes |
| 16 | *Suspend* doesn't terminate the old process (SIGTERM not reaching gunicorn through the shell-form `CMD`, no platform kill, or anything else) | **All releases:** its threads keep running, and the fence wouldn't stop their provider calls. *Suspended*, `/healthz` failing, quiet logs, elapsed time **or zero database connections** don't reveal it: a live process can have no authenticated connection (F15). Only termination evidence accepted by Stage 0.5 would; **none is known today [Stage 0.5, blocking]**. | Old: yes (not fenced yet) | Yes, **if** the operator doesn't fence without termination evidence | Runbook *Abort before the fence*; don't retry until understood | Yes |
| 17 | Resumed fenced release fails to boot, or Render refuses the next deploy | **All releases:** a fenced user's start-up `create_index` is expected to be refused **[Stage 0.6]**. **`8b59986` and `a83d17e`:** both create indexes at import, so both are expected to fail before starting loops; with a fenced user, no write succeeds either way. **Unknown until Stage 0.6:** Render's handling (restarts, status) and whether step 1.7.2 is accepted out of that state. | No (fenced) | Yes for data | If step 1.7.2 is refused: runbook *Abort* (Case A if its proof holds, otherwise Case B). Stage 0.6 failing blocks Step 1.7. | Yes |

## What is guaranteed, what is assumed, what is not covered

**Enforced by the server (all releases):**
- a connection that has received the role change can't write any collection on `reachout`, whatever its code
  (F5; to be shown on Atlas in Stage 0.2);
- a *fresh* connection after the fence is refused, and this is checked directly in production (fence evidence
  item 2);
- after `T` more, none of its transactions can commit (relevant to Step 2 releases; `8b59986` has none);
- **these are about database writes only.** Provider calls are covered below.

**Assumed, not enforced:**
- **Same-release multi-process safety:** several processes of the *same* release sharing one generation's
  credentials are safe only because of Step 2's design (leases, versioned CAS, task records, schedule slots). That is
  tested in multi-process tests but has never run in production. The fence doesn't separate them (case 13).
- **Atlas propagation (empirical):** that the fence has reached every previously open connection by
  `t_fence + P + M`.
  - `P` is the largest propagation observed in the Stage 0.2 runs. `M` is a margin chosen from it.
  - Three runs don't bound future behaviour: a later role change could propagate more slowly.
  - Production checks fresh connections only.
  - Limited by stopping the old process first (no old connection expected), and by the runbook's *Production
    contradicts Stage 0.2* abort.
- **Process termination:** *Suspend* ends the old process. This needs termination evidence accepted by Stage 0.5.
  **Zero database connections is a liveness observation, and never qualifies:** a live PyMongo 4.18.2 client can have
  no authenticated connection (unauthenticated monitors, lazy pool, pool clears). No Render signal is documented as
  container-termination evidence. Until one is accepted, quiescence is unproven, and the migration doesn't proceed.
- **Fenced restart:** a resumed fenced release writes nothing and can be deployed out of (Stage 0.6).

**Not prevented, operator mistakes:**
- giving the current generation's credentials to a *different* release (case 13), or reusing a revoked user's
  credentials for a new release;
- running a laptop with production credentials;
- skipping quiescence (or fencing without termination evidence) or ingress closure;
- deploying with the new user saved before the old release is gone (runbook step 1.7).

The runbook's rules exist for exactly these.

**Not covered by MongoDB at all, external effects:**
- a request already sent to a provider can't be cancelled or undone;
- several `8b59986` handlers call GitHub or Cloudinary before any database write;
- campaign and apply threads call providers before writing, and a claimed send-queue item is sent without a further
  write: **the fence doesn't stop any of these while the process is alive**.

**Mitigations, in order:**
1. close ingress (no new HTTP-started operation);
2. stop the old instance, with termination evidence (no background thread survives; anything interrupted mid-call has
   an **unknown** outcome);
3. fence;
4. list the unknown outcomes for follow-up (runbook step 1.10). Once implemented, the delivery state machine
   (Stage 4, proposed) would record `sending` → `unknown` explicitly. Nothing is retried automatically.

**Atlas propagation of role changes** is undocumented and may differ on shared tiers. Stage 0 measures `P` with eight
independent connections over three runs. That is an empirical measurement, not a bound. In production, proceeding
needs the full *fence evidence*: elapsed ≥ `t_fence + P + M`, a refused fresh connection, and the definition check. A
fresh-connection refusal alone is never treated as proof that existing connections are refused. If production
contradicts the measurement, the runbook's abort applies.
