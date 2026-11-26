# ADR-0013: A MongoDB-backed task queue, with no Redis or Celery at first

- **Status:** Approved in direction (part of the approved overall direction, 2026-11-25)
- **Date:** 2026-11-25
- **Implemented:** No. This was Phase 0 step 6, and it hasn't been started.

## Decision (as proposed)
- Use a `tasks` collection.
- Claim tasks atomically with `find_one_and_update` and a lease, renewed by heartbeat.
- Retry with backoff, dead-letter after N attempts, and require a unique `idempotency_key`.
- **Later, if throughput needs it:** Redis (Render Key Value) with RQ.
- **Not recommended:** Celery, because its broker, beat and result backend add complexity that buys little at this
  scale.

## Required tests
The product owner's wording (mandatory requirement 6):

> "Thoroughly test atomic claiming, leases, heartbeats, expired task recovery, duplicate prevention, concurrent
> executions, idempotency and crash recovery."

## Reuse
- The lease, renewal and fencing logic in `infra/locks.py`.
- The state machine and cancellation in `infra/operations.py`.

## Related
[../architecture/WORKER_AND_QUEUE_ARCHITECTURE.md](../architecture/WORKER_AND_QUEUE_ARCHITECTURE.md)
