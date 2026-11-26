# ADR-0004: Platform-managed model keys first, BYOK later

- **Status:** Approved (product owner, 2026-11-25)
- **Date:** 2026-11-25
- **Implemented:** No

## Context
- Today every model call goes through `features/ai_jobs/models.py:chat()` (line 117). It uses one of:
  - the user's own encrypted key for a cloud provider;
  - local Ollama;
  - local Claude Code or Kiro CLI, only when Inkwell runs on the user's own computer.
- The hosted server has no platform key.
- No call records tokens, cost or the model id the provider returned.

## Decision
The product owner's wording:

> "Initially, use platform-managed Anthropic/OpenAI API keys with configurable per-user usage budgets. […] Design the
> Model Gateway to support BYOK in the future, but do not make it a requirement for the first release."

Required controls (same message):
- per-user daily and monthly limits;
- per-agent execution budgets;
- per-task token and cost limits;
- global platform expenditure limits;
- usage visibility for users;
- administrative budget controls;
- automatic protection against runaway execution.

## Alternatives considered
- **BYOK only (as today).** Rejected for the first release: hosted agents would only work for users with keys.
- **A self-hosted Ollama GPU server.** Not chosen: it is a running cost and the model quality is unknown.

## Consequences
- Platform keys live only in the server's environment (the checklist names `PLATFORM_ANTHROPIC_KEY` and
  `PLATFORM_OPENAI_KEY`; **not implemented**). They are never stored in MongoDB, sent to the browser, logged, or
  passed to agents.
- The gateway must enforce budgets before every call
  ([ADR-0008](ADR-0008-budgets-and-execution-controls.md)).
- Existing BYOK user keys stay as they are (encrypted in `kv`). The gateway would mark those calls `billing: byok`
  so they don't count against platform budgets. That is proposed, not decided.
- **Open:** which providers and models, the price table, and the default budgets. All are TO BE DEFINED.

## Related
[../architecture/MODEL_GATEWAY_ARCHITECTURE.md](../architecture/MODEL_GATEWAY_ARCHITECTURE.md),
[../operations/COST_AND_USAGE_GOVERNANCE.md](../operations/COST_AND_USAGE_GOVERNANCE.md)
