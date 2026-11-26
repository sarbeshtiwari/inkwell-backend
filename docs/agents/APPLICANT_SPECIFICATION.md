# Applicant specification

**Status:** PROPOSED (agent set approved in direction; this specification is not approved) · **Phase:** 2 (first workflow) ·
**Implemented:** No · **Last reviewed:** 2026-11-26

Shared rules for every agent: [README.md](README.md#rules-shared-by-every-agent).

## 1. Purpose
Submit applications where it is allowed, and only after the user has explicitly approved each one.

## 2. Responsibilities
- Assemble each application package (url, values, resume, cover letter, approved answers).
- Check eligibility (board allowed, not already applied, not blocked, live).
- Create one approval request per application.
- After approval, submit through the existing apply code, and record the outcome.
- Hand back anything only the user can do (`needs_you`: sign-in, CAPTCHA, email code, unanswered questions).

## 3. Must never
- Submit without a matching approval for that exact payload. Standing consent (`auto_apply_consent`) does not count.
- Submit in shadow mode.
- Auto-submit on LinkedIn, Naukri, Indeed or Glassdoor (`siteapply.NEVER`), or on account sites outside Watch mode.
- Bypass a CAPTCHA or bot check, or retry after a site flags automation.
- Exceed the 20-per-day apply limit.
- Retry a submission whose outcome is unknown without asking the user.

## 4. Inputs
Application packages (from the Writer and Orchestrator), the user's apply values, and `ToolContext` (`mode`).

## 5. Outputs
For each job: status (`awaiting_approval`, `submitted`, `needs_you`, `skipped` with reason, `failed`, `outcome_unknown`) and the application record.

## 6. Available tools
Prepare: `apply.apply_url`, `apply_values`, `resume_file`, `cover_file`, `is_blocked_board`. Submit: `apply.apply_one(…, submit=True)` (`features/ai_jobs/apply.py:162`), `siteapply`, `forms.fill_form`.

## 7. Tool permissions
Prepare tools are `read`. **Submit is `external_action`: approval policy `always`.**

## 8. Model requirements
Minimal. The steps are deterministic (Playwright). A model may map form questions to approved answers; drafted answers can't be used without approval.

## 9. Memory requirements
Application history (existing tracker), to avoid duplicates.

## 10. Planning and execution lifecycle
Prepare the package, then the eligibility gate, then an approval request with `interrupt()`, then (when approved) `checkpoint()` and the idempotency-key check, then submit, then record the outcome once.

## 11. Delegation
May delegate to the Writer (for a missing answer, which comes back as a draft for review).

## 12. Human approval
**Always, per application.** The approval shows the exact payload (fields, files, answers). An edit creates a new approval. In shadow mode no approval is created and nothing is submitted.

## 13. Errors and retries
- A bot block marks the board blocked (existing behaviour) and stops.
- `needs_you` returns to the user.
- A crash after the submit click but before the record is written is `outcome_unknown`. It is never retried automatically.

## 14. Execution limits
- 20 applications per day (`APPLY_PER_DAY`).
- One browser at a time today (`APPLY_LOCK`; it is process-local).

## 15. Cost controls
- Runs under the per-agent run budget and the trace ceiling it inherits.
- The gateway refuses calls past the user, global or task limits ([../operations/COST_AND_USAGE_GOVERNANCE.md](../operations/COST_AND_USAGE_GOVERNANCE.md)).
- Amounts are TO BE DEFINED. The main cost is browser time rather than tokens.

## 16. Observability
Spans for every model call, tool call, delegation, retry and error, plus the final output, under the task's `trace_id` ([../architecture/OBSERVABILITY_ARCHITECTURE.md](../architecture/OBSERVABILITY_ARCHITECTURE.md)). Summaries are redacted with `redact.pii`. One `approval` span per request and one `tool_call` span per submission, each with its idempotency key.

## 17. Evaluation criteria
- Zero submissions without approval: a hard gate, verified by tests and audit.
- Share of approved submissions that succeed.
- Correct `needs_you` hand-backs.

## 18. Acceptance tests
- Shadow mode: no approval records and no submissions.
- On mode: no submission without a matching approved record.
- A duplicate idempotency key is refused.
- Rejecting an approval stops that application.
- A worker crash after approval doesn't submit twice.
