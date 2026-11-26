# ADR-0001: MongoDB Atlas for all data, not PostgreSQL

- **Status:** Approved (product owner, 2026-11-25)
- **Date:** 2026-11-25
- **Implemented:** Partly (see Consequences)

## Context
- All of Inkwell's state already lives in MongoDB (`core/db.py`, `core/workspace.py`).
- Production runs on MongoDB Atlas 8.0, region `AP_SOUTH_1` (from earlier checks). The tier is **UNKNOWN**.
- The agentic proposal needs collections for tasks, traces, model configurations, usage records and memory.
- Adding PostgreSQL with pgvector would mean running two databases, or migrating everything.

## Decision
The product owner's wording:

> "Stay with MongoDB Atlas. Do not introduce PostgreSQL just for agent memory. Use MongoDB for the existing
> application, agent registry, task queue, execution traces, model configurations, usage records and agent memory.
> Before implementing vector memory, verify that my current Atlas tier supports the required Vector Search
> functionality."

## Alternatives considered
- **PostgreSQL + pgvector for agent data only.** Rejected: a second database to run, back up and secure.
- **A full migration to PostgreSQL.** Rejected: it brings no benefit that justifies rewriting every data path.
- **A dedicated vector database.** Rejected: Atlas Vector Search covers the need, if the tier allows it.

## Consequences
- The agent collections ([../architecture/DATABASE_ARCHITECTURE.md](../architecture/DATABASE_ARCHITECTURE.md) §
  "Proposed agentic collections") are designed for MongoDB.
- Transactions require a replica set. Atlas always is one; locally `tools/local_replset.py` sets one up.
- **Open:** whether the Atlas tier supports Vector Search indexes, and its index limits.
  - `phase0-checklist.md` records that `list_search_indexes` succeeded on the cluster. That shows the command works,
    not the tier's limits.
  - This must be verified (task OPS-06) before memory work (Phase 4).

## Related
[ADR-0012](ADR-0012-mongodb-coordination-layer.md), [ADR-0013](ADR-0013-mongodb-task-queue.md),
[../architecture/MEMORY_ARCHITECTURE.md](../architecture/MEMORY_ARCHITECTURE.md)
