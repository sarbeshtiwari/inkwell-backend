# Memory architecture

**Status:**
- Database decision: APPROVED ([ADR-0001](../decisions/ADR-0001-mongodb-atlas-not-postgresql.md)).
- Memory design: PROPOSED.
- Implementation: **not started** (Phase 4).
- **Blocked on** verifying the Atlas tier's Vector Search support.

**Last reviewed:** 2026-11-26

## Requirement (approved 2026-11-25)
Use MongoDB for agent memory. "Before implementing vector memory, verify that my current Atlas tier supports the
required Vector Search functionality."

What is known:
- `phase0-checklist.md` records that `list_search_indexes` worked on the cluster. That shows the command is accepted.
- The tier's index limits are **UNKNOWN**.
- The checklist planned to create and delete one test vector index to confirm the limit. That is **not done**, and it
  changes Atlas, so it needs approval.

## Existing memory-like data (CURRENT IMPLEMENTATION)
These are not agent memory, but agents would read them through tools:
- **Learned job preferences:** `features/ai_jobs/learning.py`, stored in `kv`. It records Good fit / Not for me
  feedback, applied jobs and outcomes, and decays older lessons.
- **Answer bank:** `ai_answers` in `kv`, with approval status.
- **Application tracker history:** `applications` and `ai_*` keys in `kv`.
- **Company research results:** `features/research/`.

## Proposed design
| Kind | Storage | Scope | Notes |
|---|---|---|---|
| Working memory (within a run) | LangGraph state and checkpoints | task / trace | Lost when the run ends, except the outputs |
| Long-term user memory | `memory` collection: `uid`, `kind`, `text` (sealed), `embedding`, `source` (trace or tool), `created`, `updated`, `expires_at?` | **per user only** | Vector index filtered on `uid`. The user can list and delete (`GET/DELETE /api/agents/memory`, proposed) |
| Shared knowledge (job boards, company facts that aren't user data) | **TO BE DEFINED** | global | Must not contain any user's content |

## Constraints
- Every lookup filters on `uid` ([../engineering/TENANT_ISOLATION.md](../engineering/TENANT_ISOLATION.md)).
- Embeddings come through the model gateway, so they count against budgets and are recorded.
- A memory entry records which trace produced it, for explanation and deletion.
- Retention and deletion: the user controls it; account deletion removes it. Periods are **TO BE DEFINED**.
- Embedding text derived from user content is sensitive. Whether embedding vectors count as personal data for the
  privacy policy is a **PENDING DECISION**.

## Out of scope for the minimum first workflow
Phases 1–2 don't need vector memory. The first workflow can use the existing learned preferences and answer bank
through tools.
