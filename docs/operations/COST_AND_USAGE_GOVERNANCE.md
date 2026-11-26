# Cost and usage governance

**Status:** APPROVED requirements ([ADR-0004](../decisions/ADR-0004-platform-managed-model-keys.md),
[ADR-0008](../decisions/ADR-0008-budgets-and-execution-controls.md)); **nothing is implemented**. ·
**Last reviewed:** 2026-11-26

## Current cost-related controls (CURRENT IMPLEMENTATION)
None of these are model budgets.

| Control | Value | Where |
|---|---|---|
| Emails per user per day | `DAILY_LIMIT`, default 100 | `core/config.py`, `core/sending.py` |
| System emails per hour | `SYSTEM_MAIL_PER_HOUR`, default 300 | `core/config.py` |
| AI match runs | 30 per day per user | `features/ai_jobs/runs.py` (rate limit `ai-match`) |
| Evaluations | 150 per day; batch up to 15 | `features/ai_jobs/evaluate.py` |
| Cover letters / resumes / prep | 60 / 40 / 40 per day | `features/ai_jobs/evaluate.py`, `extras.py` |
| Applications | 20 per day (`APPLY_PER_DAY`) | `features/ai_jobs/common.py` |
| Company research | 30 per day | `features/research/company.py` |
| Google Places | 60 searches per day | `features/nearby.py` |
| Model keys | **User's own (BYOK)**, so model cost is borne by the user today | `features/ai_jobs/models.py` |

## Approved requirements (2026-11-25)
- Platform-managed Anthropic/OpenAI keys initially, with configurable per-user budgets.
- Per-user daily and monthly limits.
- Per-agent execution budgets.
- Per-task token and cost limits.
- Global platform expenditure limits.
- Usage visibility for users.
- Administrative budget controls (`admin.manage_budgets`, which already exists as a permission).
- Automatic protection against runaway execution: delegation depth, execution and step limits.
- BYOK designed for later, not required in the first release.

## Proposed mechanism
1. **`model_usage`:** one record per call (tokens, cost from `model_prices` with the price version, returned model id,
   billing).
2. **`budgets`:** per scope, `global`, `user:<uid>`, `agent:<id>`, plus per-task limits on the task itself.
3. **Gateway check before every call:**
   - the current spend comes from a running counter per scope and period;
   - how the counter is kept consistent across processes is TO BE DEFINED; options are an atomic `$inc` on a
     per-period document, or an aggregation;
   - a breach is a hard stop with a clear message to the user.
4. **Runaway protection:** a per-trace ceiling, max depth, children and steps, deadlines, and kill switches.
5. **Visibility:**
   - users see their own usage (UI TO BE DEFINED);
   - Super Admins see totals per user, agent and model, as metadata only.
6. **BYOK calls** are marked `billing: byok` and excluded from platform budgets.

## Cost estimate (from the proposal, unvalidated)
- One agentic job-search run (search, rank, evaluate 10 jobs, draft 3 applications) uses roughly 100k–400k tokens,
  depending on the model.
- The proposal recommends measuring this in shadow mode before enabling.
- Render worker cost: about $7+/month (proposal; UNKNOWN).

## To be defined (pending decisions)
- default budget amounts;
- the price table;
- the alert threshold for "near limit";
- what users see when a limit is hit;
- monthly reset timezone.
