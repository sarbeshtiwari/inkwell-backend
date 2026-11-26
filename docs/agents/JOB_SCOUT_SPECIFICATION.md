# Job Scout specification

**Status:** PROPOSED (agent set approved in direction; this specification is not approved) · **Phase:** 2 (first workflow) ·
**Implemented:** No · **Last reviewed:** 2026-11-26

Shared rules for every agent: [README.md](README.md#rules-shared-by-every-agent).

## 1. Purpose
Find relevant openings across sources, remove duplicates, and keep only live ones.

## 2. Responsibilities
- Choose sources for the criteria (role, location, remote, companies).
- Call the search tools.
- Normalise and remove duplicates (the same job from several sources is shown once, as today).
- Run the liveness checks.
- Return candidates with their source and liveness.

## 3. Must never
- Log in to any site, or bypass a bot check or CAPTCHA. The existing readers stop on bot checks.
- Scrape LinkedIn or Naukri.
- Apply, or contact anyone.
- Use search keys other than the user's own (Tavily/Brave are BYOK today).

## 4. Inputs
Search criteria (from the Orchestrator), the user's location and My companies, and `ToolContext`.

## 5. Outputs
A list of jobs: id, title, company, location, url, source, posting date, `liveness` result.

## 6. Available tools
`sources.make_tools` (career sites, `tavily_search` and `brave_search` with the user's keys, remote boards); the 118 board readers in `boards/`; `sitejobs.discover` and `fetch`; `portals`; `nearby.find_places` (optional); `signals.liveness`.

## 7. Tool permissions
`read` only.

## 8. Model requirements
- Any active `model_configs` version assigned to this agent, through the gateway.
- Structured JSON output validated against the agent's output schema.
- The provider and model are TO BE DEFINED. The proposal suggests cheaper models for high-volume steps. Model use is light: choosing sources and expanding queries. Most of the work is deterministic tools.

## 9. Memory requirements
Working memory only. It may use learned preferences through the Orchestrator's input.

## 10. Planning and execution lifecycle
Pick sources, then search (in parallel, within limits), then normalise and dedupe, then check liveness, then return. One refinement round is allowed if too few results come back.

## 11. Delegation
May delegate to the Job Analyst (from the proposal), but in the first workflow it returns to the Orchestrator.

## 12. Human approval
None.

## 13. Errors and retries
- Per-source failures are recorded and skipped, as the existing ranking chain does.
- HTTP timeouts follow the existing `get_json` (15 s).
- No retries on board fetches today. Retry policy TO BE DEFINED.

## 14. Execution limits
- Max sources and max jobs returned (proposed, TO BE DEFINED).
- Existing per-user rate limits apply (e.g. `ai-match` 30 per day).

## 15. Cost controls
- Runs under the per-agent run budget and the trace ceiling it inherits.
- The gateway refuses calls past the user, global or task limits ([../operations/COST_AND_USAGE_GOVERNANCE.md](../operations/COST_AND_USAGE_GOVERNANCE.md)).
- Amounts are TO BE DEFINED. Search API calls use the user's own keys and are not on platform budgets.

## 16. Observability
Spans for every model call, tool call, delegation, retry and error, plus the final output, under the task's `trace_id` ([../architecture/OBSERVABILITY_ARCHITECTURE.md](../architecture/OBSERVABILITY_ARCHITECTURE.md)). Summaries are redacted with `redact.pii`. One `tool_call` span per source, with counts.

## 17. Evaluation criteria
- Recall against the existing AI job match on the same input (shadow comparison).
- Duplicate rate.
- Share of returned jobs that are live.

## 18. Acceptance tests
- Shadow run on a fixture input returns jobs that are a superset of, or comparable to, the existing pipeline's (threshold TO BE DEFINED).
- A failing source doesn't fail the run.
- Closed postings are excluded.
- No calls to LinkedIn or Naukri.
