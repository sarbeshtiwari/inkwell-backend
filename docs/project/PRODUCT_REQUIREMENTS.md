# Product requirements

**Status:** Section 1 is CURRENT IMPLEMENTATION; sections 2–4 are APPROVED requirements (product owner, 2026-11-25),
quoted where possible; section 5 is PROPOSED · **Last reviewed:** 2026-11-26

## 1. The product today
Inkwell helps a person run outreach and a job search from one place, with their own accounts. Feature descriptions
come from `backend/README.md`; the code is described in
[../architecture/BACKEND_ARCHITECTURE.md](../architecture/BACKEND_ARCHITECTURE.md#8-product-areas).

| Area | Users can |
|---|---|
| Outreach | Import contacts, write one templated message, and send personal copies by email (WhatsApp only when self-hosted), with attachments and a daily limit |
| AI job match | Upload a resume; search public career sites and job boards; rank, evaluate (A–H report) and tailor (reorder only); apply on supported public forms with consent (up to 20 a day); Watch mode locally; Chrome extension autofill (never submits) |
| Tracking | Applications from mailbox emails; replies and their intent; inbox by company; follow-ups |
| Learning | 90-day interview course, DSA, spaced review, daily briefing |
| Research | Company facts validated against their pages |
| Presence | A personal website and portfolio, GitHub contributions, LinkedIn posting through the official API |
| Privacy | All content encrypted at rest. Email addresses are found only through keyed hashes. Super Admin has no access to content |

## 2. Agentic platform vision (approved direction)
> "I want this to become a production-grade agentic AI platform, not simply an existing application with LLM calls
> added to it. Prioritize reliability, security, extensibility, observability and actual agent autonomy over rushing
> to add features."

Agents must be able to:
- receive goals and plan;
- select authorized tools and execute;
- delegate to specialised agents;
- keep their execution state and recover from failures;
- request human approval;
- record telemetry;
- work within budgets and permissions.

## 3. Product-owner decisions (2026-11-25)

| # | Decision | ADR |
|---|---|---|
| 1 | Stay on MongoDB Atlas for everything, agent memory included; check Vector Search on the tier first | [0001](../decisions/ADR-0001-mongodb-atlas-not-postgresql.md) |
| 2 | Super Admin: metadata-only visibility by default; content access, if ever built, needs explicit authorization, a recorded reason and an immutable audit event, and the privacy policy is updated first | [0009](../decisions/ADR-0009-metadata-only-super-admin.md) |
| 3 | Platform-managed Anthropic/OpenAI keys with per-user budgets; BYOK designed for later | [0004](../decisions/ADR-0004-platform-managed-model-keys.md) |
| 4 | First workflow: Find and Prepare Applications (Orchestrator → Job Scout → Job Analyst → Researcher when needed → Writer → Applicant with approval); shadow first, then the owner's account, then wider | [0010](../decisions/ADR-0010-shadow-mode-first-workflow.md) |

## 4. Mandatory engineering requirements (2026-11-25)
1. **Agent versioning:** record the agent version, instructions version, tool configuration and model configuration
   of every execution.
2. **Agent evaluations:** measurable quality evaluations, regression tests and historical replay. Execution success
   is not quality.
3. **Cost controls:** token, execution, delegation-depth and expenditure limits. No infinite delegation or runaway
   execution.
4. **Tenant isolation:** every task, trace, memory lookup, tool call and database operation is scoped to the user,
   with explicit cross-user tests.
5. **Global kill switch:** stop new executions globally, per agent or per user, with controlled cancellation of
   running ones.
6. **Queue reliability:** test atomic claims, leases, heartbeats, expired-task recovery, duplicate prevention,
   concurrency, idempotency and crash recovery.
7. **Human approval:** sending email, sending WhatsApp messages and submitting applications always need explicit user
   approval. Agents never bypass it.
8. **Model governance:** no new model or agent configuration becomes production-active without evaluation and
   explicit approval.
9. **Observability:** correlated telemetry for every model call, tool call, delegation, retry, approval, error and
   final output.
10. **Security:** never expose API keys, credentials, unrestricted database access or unrestricted shell to agents.

Also required: preserve all existing functionality and APIs, and keep the existing tests passing.

## 5. Non-functional requirements (PROPOSED; derived, to be confirmed)
- Existing user-facing APIs stay backward compatible while agents are introduced. Agent features sit behind flags.
- The application must work without any model configured. Existing rules-based fallbacks stay.
- No LinkedIn scraping; no bypassing CAPTCHAs or bot checks; no automated WhatsApp on the hosted service (existing
  product rules).
- Production safety: no deploy outside the accepted fenced procedure
  ([../operations/DEPLOYMENT_STRATEGY.md](../operations/DEPLOYMENT_STRATEGY.md)).
