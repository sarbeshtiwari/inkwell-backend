# Writer specification

**Status:** PROPOSED (agent set approved in direction; this specification is not approved) · **Phase:** 2 (first workflow) ·
**Implemented:** No · **Last reviewed:** 2026-11-26

Shared rules for every agent: [README.md](README.md#rules-shared-by-every-agent).

## 1. Purpose
Produce the application materials: a tailored resume, a cover letter, form answers and (Phase 3) outreach drafts.

## 2. Responsibilities
- Tailor the resume within the existing `tailor_rules`: reorder skills and bullets; reword the summary using only the resume's own words, tools and numbers.
- Write a cover letter.
- Draft answers to application questions. Approved answers are used as they are; new drafts go to `review`.
- Explain the tailoring ("What the job needs" / "Why this order").

## 3. Must never
- Add, drop or change any fact on the resume. `tailor_rules` rejects it and the original is kept.
- Mark its own drafted answers as approved.
- Send anything.

## 4. Inputs
Shortlisted jobs with their descriptions, the resume (and its `.tex` if present), approved answers, and `ToolContext`.

## 5. Outputs
For each job: a tailored resume (PDF, LaTeX or HTML), a cover letter, form answers (approved ones reused, new ones as drafts), and a tailoring explanation.

## 6. Available tools
`extras.resume_structure` and `ordered`, `tailor_rules.*`, `latex`, `evaluate.cover_letter`, `answers.*`.

## 7. Tool permissions
`write_internal`, as drafts only. In shadow mode, output goes to shadow records.

## 8. Model requirements
- Any active `model_configs` version assigned to this agent, through the gateway.
- Structured JSON output validated against the agent's output schema.
- The provider and model are TO BE DEFINED. The proposal suggests cheaper models for high-volume steps. The proposal suggests a stronger model for the Writer. The existing calls use 2,500–4,000 tokens with a 300 s timeout.

## 9. Memory requirements
Approved answers (existing `ai_answers`). Writing-style memory is Phase 4 and optional.

## 10. Planning and execution lifecycle
Structure the resume, then propose an order, then validate with `tailor_rules` (rejected proposals fall back to the original), then write the cover letter, then draft answers, then return.

## 11. Delegation
May delegate to the Researcher (for company context in the cover letter).

## 12. Human approval
None for drafts. Drafted answers wait in `review` for the user (existing behaviour).

## 13. Errors and retries
- A rejected tailoring proposal keeps the original resume; it is not retried endlessly.
- Model failure falls back to the deterministic cover letter (existing behaviour).

## 14. Execution limits
Max jobs per run and max proposal attempts: TO BE DEFINED.

## 15. Cost controls
- Runs under the per-agent run budget and the trace ceiling it inherits.
- The gateway refuses calls past the user, global or task limits ([../operations/COST_AND_USAGE_GOVERNANCE.md](../operations/COST_AND_USAGE_GOVERNANCE.md)).
- Amounts are TO BE DEFINED.

## 16. Observability
Spans for every model call, tool call, delegation, retry and error, plus the final output, under the task's `trace_id` ([../architecture/OBSERVABILITY_ARCHITECTURE.md](../architecture/OBSERVABILITY_ARCHITECTURE.md)). Summaries are redacted with `redact.pii`.

## 17. Evaluation criteria
- Zero added facts: 100% `tailor_rules` compliance (hard gate).
- Keyword coverage of the job description.
- User edits needed before approval (lower is better).

## 18. Acceptance tests
- Every tailored resume passes `tailor_rules`.
- No drafted answer is marked approved.
- In shadow mode, nothing user-visible changes.
