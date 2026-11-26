# Researcher specification

**Status:** PROPOSED (agent set approved in direction; this specification is not approved) · **Phase:** 2 (first workflow, only when needed) ·
**Implemented:** No · **Last reviewed:** 2026-11-26

Shared rules for every agent: [README.md](README.md#rules-shared-by-every-agent).

## 1. Purpose
Provide validated public facts about a company: website, careers page, public hiring contact, size, industry, location, leadership.

## 2. Responsibilities
- Read the company's public pages within the existing limits.
- Extract facts.
- Re-check every claim against the page it came from, with a quote, as the existing `research/company.py` validator does.
- Refuse namesake companies.
- Return found, partial, or nothing found.

## 3. Must never
- Collect personal email addresses. Only role addresses (careers@, hr@ …) are kept, as in `finder.py` and `nearby.py`.
- Use non-public sources or logins.
- Return a claim without a supporting quote.

## 4. Inputs
Company name and/or domain, the question asked (from the Analyst), and `ToolContext`.

## 5. Outputs
Facts with source URL and quote, plus a status (found, partial, nothing found, failed).

## 6. Available tools
`features/research/company.py` (crawl and validate), `research/public_web.py` (bounded, cookie-less reader). The CLI web-search path is local only.

## 7. Tool permissions
`read`.

## 8. Model requirements
- Any active `model_configs` version assigned to this agent, through the gateway.
- Structured JSON output validated against the agent's output schema.
- The provider and model are TO BE DEFINED. The proposal suggests cheaper models for high-volume steps. The existing extraction call uses 2,500 tokens and a 300 s timeout.

## 9. Memory requirements
Research results are cached per user (the existing research store). Shared, cross-user company facts are a PENDING DECISION.

## 10. Planning and execution lifecycle
Fetch pages (at most 16 per company today), then extract, then validate claim by claim, then return.

## 11. Delegation
None.

## 12. Human approval
None.

## 13. Errors and retries
- At most 3 attempts, 5 minutes apart (existing behaviour).
- A failure is reported, not invented around.

## 14. Execution limits
- 16 pages per company.
- 30 companies per day per user (existing limit).

## 15. Cost controls
- Runs under the per-agent run budget and the trace ceiling it inherits.
- The gateway refuses calls past the user, global or task limits ([../operations/COST_AND_USAGE_GOVERNANCE.md](../operations/COST_AND_USAGE_GOVERNANCE.md)).
- Amounts are TO BE DEFINED.

## 16. Observability
Spans for every model call, tool call, delegation, retry and error, plus the final output, under the task's `trace_id` ([../architecture/OBSERVABILITY_ARCHITECTURE.md](../architecture/OBSERVABILITY_ARCHITECTURE.md)). Summaries are redacted with `redact.pii`.

## 17. Evaluation criteria
- Claim precision: share of claims supported by a quote; target 100%, enforced by the validator.
- Coverage of the requested fields.

## 18. Acceptance tests
- Every returned claim has a URL and a quote found on that page.
- A namesake company is refused.
- No personal addresses are returned.
