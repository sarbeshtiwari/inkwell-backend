# ADR-0003: A separate agent worker service

- **Status:** Approved in direction (part of the approved overall direction, 2026-11-25). Timing not decided.
- **Date:** 2026-11-25
- **Implemented:** No

## Context
- The web service is one gunicorn process (`Dockerfile`: `--workers 1 --threads 16 --timeout 120`).
- Background loops and long jobs run as threads inside that process: 8 recurring loops and 14 `threading.Thread`
  call sites. See [../architecture/BACKEND_ARCHITECTURE.md](../architecture/BACKEND_ARCHITECTURE.md).
- Agent runs are long, use a lot of memory (Playwright) and must not block or crash web requests.

## Decision (as proposed)
- Run agents in a Render background worker (`python -m agents.worker` in the proposal), from the same image as the
  web service, claiming tasks from a MongoDB queue ([ADR-0013](ADR-0013-mongodb-task-queue.md)).
- The web service only creates tasks, reads state and streams events.
- Recurring jobs may move into the worker, still coordinated through `infra/schedule.py`.

## Prerequisites already built (Phase 0 Steps 2–2B, local commits, not deployed)
State shared across processes: leases (`infra/locks.py`), operations (`infra/operations.py`), schedule slots
(`infra/schedule.py`) and versioned writes (`Workspace.update`). Before them, two processes would have corrupted data.

## Alternatives considered
- **Run agents as threads in the web process.** Acceptable for local development and an early spike. Not for
  production, where they compete with web requests for memory.
- **Several gunicorn workers.** That gives no isolation, and agents would still share the web process's lifetime.

## Consequences
- A second Render service, about $7+/month according to the proposal; the real price is **UNKNOWN**.
- A worker has its own deploy and its own database user. The credential-fencing procedure
  ([ADR-0011](ADR-0011-credential-fenced-deployment.md)) treats detached workers explicitly: a crash of the web
  process does not stop them.
- **Open:** when the worker is introduced. A reasonable order is to develop in-process locally and add the worker
  before the first hosted agent run. That is not decided.

## Related
[../architecture/WORKER_AND_QUEUE_ARCHITECTURE.md](../architecture/WORKER_AND_QUEUE_ARCHITECTURE.md)
