# Architecture decision records

**Status:** Draft for review · **Last reviewed:** 2026-11-26

An ADR records one significant decision: why it was made, what else was considered, and what follows from it.
The list is in [ADR_INDEX.md](ADR_INDEX.md).

## ADR statuses

| Status | Meaning | Evidence required |
|---|---|---|
| **Approved** | The product owner explicitly agreed to it | A dated approval from the product owner (quoted or referenced in the ADR) |
| **Approved in direction** | Part of a proposal whose overall direction was approved, but not decided on its own | The 2026-11-25 approval of the architecture assessment's "overall direction" |
| **Accepted (documentation only)** | The written design is accepted; executing it needs separate approval | An acceptance message that scopes approval to documentation |
| **Proposed** | Suggested and not yet approved | None |
| **Superseded** | Replaced by a later ADR | Link to the replacing ADR |

An ADR never becomes **Approved** because it is written down or implemented. Approving one design does not approve
the work to build it: Phase 1 implementation is **not authorized** (see
[../project/PROJECT_STATUS.md](../project/PROJECT_STATUS.md)).

## Sources of the decisions

- **Product owner's approval message, 2026-11-25.** Approves the overall direction of
  `inkwell-agentic-architecture.md` (project root) and gives four decisions (database, Super Admin visibility,
  model costs, first workflow) and ten mandatory engineering requirements. It is quoted in the ADRs.
- `inkwell-agentic-architecture.md` (project root, outside git): the proposal itself.
- `phase0-checklist.md` (project root, outside git): the Phase 0 plan written after that approval.
- [../deployment/](../deployment/): the five deployment documents. The product owner accepted them as documentation
  on 2026-11-26. Executing them is not approved.

## Writing a new ADR

1. Copy the section layout of an existing ADR (Status, Date, Context, Decision, Alternatives, Consequences, Related).
2. Use the next number and add a row to [ADR_INDEX.md](ADR_INDEX.md).
3. Set the status to **Proposed** until the product owner approves it in writing, then record where and when.
