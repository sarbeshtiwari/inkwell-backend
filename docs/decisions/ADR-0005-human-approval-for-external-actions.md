# ADR-0005: Human approval before external actions

- **Status:** Approved (product owner, 2026-11-25)
- **Date:** 2026-11-25
- **Implemented:** No for agents, since no agents exist. Existing features have their own consent rules
  (Consequences).

## Decision
The product owner's wording (mandatory requirement 7):

> "Sending emails, sending WhatsApp messages and submitting applications must always require explicit user approval.
> Agents must never bypass this requirement."

And for the first workflow: "Applicant (human approval required before submission)".

## Design (proposed)
1. Every tool is classified by risk: `read`, `write_internal`, `external_action` or `destructive`.
2. Calling an `external_action` tool creates an approval record and pauses the run (LangGraph `interrupt()`).
3. The user approves, edits or rejects it in the app. Approval is per action and is never inferred.
4. The run resumes from its checkpoint.
5. The external action carries an idempotency key, so a retry never repeats it.

Details are in [../engineering/APPROVAL_AND_PERMISSION_MODEL.md](../engineering/APPROVAL_AND_PERMISSION_MODEL.md).

## Alternatives considered
- **Standing consent (a setting) for agents.** Rejected by the requirement's wording: "always require explicit user
  approval".

## Consequences and an existing inconsistency
The existing auto-apply feature uses **standing consent**, not per-application approval. Once the
`auto_apply_consent` setting is on, it submits forms (`features/ai_jobs/routes.py:74`, `:117`).
- That predates this decision and isn't agent behaviour, so it doesn't break the requirement as written.
- But an agent must **not** reuse that code path to submit without a per-action approval.
- The Applicant agent may call `apply.apply_one(ws, job, submit=...)` (`features/ai_jobs/apply.py:162`) only after
  an approval has been recorded, or with `submit=False` to draft.
- Whether the existing auto-apply should also move to per-action approval is a **PENDING DECISION** (D-07 in
  [../project/PROJECT_STATUS.md](../project/PROJECT_STATUS.md)).

## Related
[ADR-0010](ADR-0010-shadow-mode-first-workflow.md),
[../agents/APPLICANT_SPECIFICATION.md](../agents/APPLICANT_SPECIFICATION.md)
