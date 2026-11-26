# ADR-0012: Shared coordination in MongoDB

- **Status:** Built under the approved Phase 0 plan. Each step was reviewed and accepted:
  - Step 1;
  - Step 2;
  - Stage 1, 2A and 2B, then the ABA fix.
- **Date:** 2026-11-25 to 2026-11-26
- **Implemented:** Yes, in local commits `45e2fec` through `a83d17e`.
  - Tests pass locally (419 passed, 2026-11-26).
  - **Not pushed and not deployed.** Production `8b59986` still uses in-process coordination for these.

## Context
Running a worker, or any second process, needs locks, progress, schedules and record updates that every process
shares.

## Decision (as built)

| Mechanism | File | What it does |
|---|---|---|
| Leases | `infra/locks.py` | One document per lock in `locks`. Atomic acquire judged by the server clock (`$$NOW`); a random `token` and an ever-growing `fence` number; background renewal; `guarded_write` re-checks the lease inside a transaction |
| Operations | `infra/operations.py` | One document per long run in `operations`: states `running → done | failed | cancelled | interrupted`, cooperative cancellation, encrypted `private` progress, TTL retention |
| Schedule slots | `infra/schedule.py` | Shared "last run" slots and `once` markers in MongoDB, so each recurring job runs once per interval across processes |
| Versioned writes | `core/workspace.py` (`Workspace.update`) | Compare-and-set on `kv` records: a server-computed version `max(prev+1, $$NOW ms)` plus a 128-bit `wid` token. Applies to `templates`, `inbox_companies` and `ai_portals` |

Design notes: [../locks-progress-cancellation.md](../locks-progress-cancellation.md).

## Differences from the original Phase 0 plan
- The plan put these in a new `backend/platform/` package. They were built in `backend/infra/`.
- The plan named a "leader-elected scheduler". The built design uses per-job slots instead of a single leader.
- The plan's rollback flags (`SCHEDULER=thread`, `LOCKS=memory`, `PROGRESS=memory`) were **not implemented**.
  Rollback is by redeploying an earlier release.

## Consequences
- Measured cost from Render: about 0.6–0.8 s of round trips per guarded write (earlier stages' benchmarks;
  `tools/bench_locks.py`).
- Two releases from `5ba9022` on must never be followed by `1d544b7` or `5c21025`
  ([../deployment/DEPLOYMENT_COMPATIBILITY_MATRIX.md](../deployment/DEPLOYMENT_COMPATIBILITY_MATRIX.md)).

## Related
[../architecture/BACKEND_ARCHITECTURE.md](../architecture/BACKEND_ARCHITECTURE.md),
[../architecture/WORKER_AND_QUEUE_ARCHITECTURE.md](../architecture/WORKER_AND_QUEUE_ARCHITECTURE.md)
