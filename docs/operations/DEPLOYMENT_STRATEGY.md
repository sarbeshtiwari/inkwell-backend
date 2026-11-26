# Deployment strategy

**Status:** The five deployment documents were **accepted as documentation** on 2026-11-26.
- **None of their stages has been executed.**
- **Production credential fencing is not approved.**
- **The first migration is BLOCKED.**

**Last reviewed:** 2026-11-26

This page is an index and status summary. It does **not** restate, reinterpret or relax the accepted documents. Where
the two differ, the documents in [../deployment/](../deployment/) are authoritative.

## The accepted documents

| Document | What it covers |
|---|---|
| [RENDER_FREE_DEPLOYMENT_RUNBOOK.md](../deployment/RENDER_FREE_DEPLOYMENT_RUNBOOK.md) | Definitions; Stage 0 (0.1–0.6); preconditions; Step 1 (first migration); quiescence; abort cases; Step R (rollback); Step F (forward again); later deploys; standing rules |
| [DEPLOYMENT_GATE_ARCHITECTURE.md](../deployment/DEPLOYMENT_GATE_ARCHITECTURE.md) | Facts F1–F15; options; components and trust boundaries; the critical race; the soft gate; external side effects; the shutdown protocol; sequence diagrams; guarantees; local safeguards; testing; implementation stages 0–5 |
| [DEPLOYMENT_WRITE_PATH_INVENTORY.md](../deployment/DEPLOYMENT_WRITE_PATH_INVENTORY.md) | Every write path per release, including startup writes made by a fenced user |
| [DEPLOYMENT_FAILURE_ANALYSIS.md](../deployment/DEPLOYMENT_FAILURE_ANALYSIS.md) | Failure scenarios with scope labels; guarantees and assumptions |
| [DEPLOYMENT_COMPATIBILITY_MATRIX.md](../deployment/DEPLOYMENT_COMPATIBILITY_MATRIX.md) | Four compatibility dimensions; the matrix between releases; releases that must never be deployed after `5ba9022` has written |

## Releases
- **Production:** `8b59986` (Phase 0 Step 1 fixes).
- **Candidate:** `a83d17e` (Steps 2 to 2B). It is tested locally and has never been deployed.
- **Never deploy** `1d544b7` or `5c21025` once `5ba9022` or later has written. `45e2fec`, `04cf85d` and `72a1681`
  have no reason to be deployed
  ([compatibility matrix](../deployment/DEPLOYMENT_COMPATIBILITY_MATRIX.md#releases-that-must-never-be-deployed-after-5ba9022-has-written)).
- **`8b59986` and `a83d17e` must not run concurrently.** Every deploy is credential-fenced until gate Stages 3 and 5
  exist and are tested.

## Status of the verification stages and steps

| Item | Documented | Approved to execute | Executed | Result |
|---|---|---|---|---|
| Stage 0.1: environment record (read-only) | Yes | Not approved | No | — |
| Stage 0.2: Atlas fence test | Yes | **Not approved** | No | — |
| Stage 0.3: least privilege | Yes | Not approved | No | — |
| Stage 0.4: ingress rotation | Yes | Not approved | No | — |
| Stage 0.5: Render suspension and process termination | Yes | **Not approved** (Render verification) | No | **BLOCKED**: no termination evidence is known |
| Stage 0.6: fenced restart and deploying out of it | Yes | **Not approved** | No | — |
| Stage 0.5 test-only build | Yes | **Not approved** | No | — |
| Production credential fencing | Yes | **Not approved** | No | — |
| Step 1: first migration `8b59986` → `a83d17e` | Yes | **Not approved** | No | **BLOCKED** by Stage 0.5 |
| Step R: rollback | Yes | — | Never rehearsed | — |
| Gate Stages 1–5 (code) | Yes (architecture §13) | Not approved | No | — |

**Stage 0 result rule** (runbook): Stage 0 passes only if 0.2, 0.5 and 0.6 all pass, and 0.1 and 0.3 are recorded.

## The Stage 0.5 blocker (as accepted)
- The migration needs **termination evidence**: a post-exit record that passes every Stage 0.5 acceptance condition.
- A **liveness observation** never qualifies. That covers zero database connections, Render's Suspended status, and
  elapsed time.
- Fake-provider test results don't qualify either.
- **No accepted termination-evidence source is known today.** Until one is, Stage 0.5 is expected to fail and the
  first migration stays blocked.
- Resolving this needs new evidence or a decision by the product owner. This documentation proposes no substitute.

## Implications for agent work
- Building and testing the agent platform locally doesn't depend on any of these stages.
- Deploying it does. A worker service also needs its own fencing treatment and its own write-path entries.
- Shipping one combined release later (Phase 1 code together with `a83d17e`) through a single fenced migration is an
  option recorded in the status audit. It is **not decided** (D-03).
