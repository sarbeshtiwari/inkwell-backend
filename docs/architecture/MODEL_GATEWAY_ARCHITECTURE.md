# Model gateway architecture

**Status:**
- Current model access: CURRENT IMPLEMENTATION.
- Gateway: APPROVED DESIGN ([ADR-0004](../decisions/ADR-0004-platform-managed-model-keys.md),
  [ADR-0007](../decisions/ADR-0007-versioned-agent-and-model-configs.md)), **not implemented**. It was Phase 0
  step 4, and it hasn't been started.

**Last reviewed:** 2026-11-26

## Today
Every model call goes through `features/ai_jobs/models.py:chat()`
([BACKEND_ARCHITECTURE.md §7](BACKEND_ARCHITECTURE.md#7-ai-and-model-calls)).

| Property | Current behaviour |
|---|---|
| Keys | The user's own keys (sealed in `kv` `ai_keys`), local Ollama, or local CLIs (not in production). **No platform key** |
| Usage records | **None.** The provider's `usage` field is discarded; only a display `engine` label is kept on some outputs |
| Exact model id | Not recorded |
| Budgets | None |
| Retries and fallbacks | None (one JSON re-ask for the CLI) |
| Timeouts | Fixed at 90 s for HTTP providers, whatever `timeout` is passed; Ollama has none set |
| Structured output | Native JSON only for Ollama; elsewhere the first JSON object is parsed from the text |
| Tool use | Not supported |

## Target (APPROVED direction; interface details PROPOSED)

### Compatibility rule (from `phase0-checklist.md` §6)
`models.chat(cfg, system, human, as_json, max_tokens, temperature, web, timeout)` keeps its exact signature and return
values. Internally it calls the gateway, so every existing caller keeps working.

### Interface (PROPOSED)
`gateway.complete(GatewayRequest)` → `GatewayResponse`.

| Request field | Meaning |
|---|---|
| `uid` | The user the call is for |
| `purpose` | What the call is for |
| `messages` | The conversation turns |
| `tools` | Tools the model may call |
| `response_schema` | Expected structure of the output |
| `params` | Generation parameters |
| `trace_ctx` | `trace_id`, `span_id`, `task_id`, `agent_id`, `agent_version`, `model_config_version` |
| `billing` | `platform` or `byok` |

| Response field | Meaning |
|---|---|
| `output` | Text, or JSON validated against `response_schema` |
| `tool_calls` | Tool calls the model asked for |
| `usage` | `input_tokens`, `output_tokens` |
| `returned_model_id` | Exactly as the provider reports it |
| `latency_ms` | Time taken by the call |
| `provider_request_id` | The provider's id for the request |
| `error_class` | `rate_limit`, `timeout`, `auth`, `server` or `invalid_output` |

### Pipeline for every call
1. **Resolve the model config.** Map an alias to an *active* `model_configs` version, unless the caller pins a
   version. Only `active` configs serve production traffic. Candidates run only in evaluation or shadow mode.
2. **Kill switch check:** global, agent, user.
3. **Budget check** before the call: user per day and month, agent per run, task, and global per day and month. A
   breach is a hard stop with a clear error.
4. **Redact** secrets from the prompt (`infra/redact.secrets`).
5. **Call the provider adapter** with a timeout, and retry only the retryable error classes.
6. **Write a `model_usage` record.** It holds no prompt and no output. Fields: ids, requested alias, returned model id,
   tokens, cost (from `model_prices` at call time, with the price version), latency, status, error class and billing.
7. **Emit a span** with a redacted summary ([OBSERVABILITY_ARCHITECTURE.md](OBSERVABILITY_ARCHITECTURE.md)).
8. **Fallback:** on a circuit-open or retry-exhausted provider, fall back to an *approved* alternative config, if the
   config defines one.

### Provider adapters (PROPOSED)

| Adapter | Use |
|---|---|
| `anthropic`, `openai` | Platform keys (initial) |
| OpenAI-compatible | Gemini, OpenRouter, Groq, Mistral, DeepSeek; BYOK |
| `ollama` | Self-hosted |
| `claude_cli`, `kiro_cli` | **Local development only**. Never on the hosted server, as today |

### Keys
- Platform keys live only in the server environment: `PLATFORM_ANTHROPIC_KEY` and `PLATFORM_OPENAI_KEY`, the names
  proposed in the checklist. They are never stored in MongoDB, sent to the browser, logged, or given to agents.
- BYOK keys stay sealed in `kv`. Calls made with them are marked `billing: byok` and don't count against platform
  budgets.
- **BYOK is not required for the first release.**

### Registry (PROPOSED collections)
- `model_providers`
- `models` (capabilities)
- `model_prices` (`effective_from`)
- `model_configs` (versioned; `candidate → approved → active → retired`)
- `model_usage`

Promotion needs an evaluation result and an audited approval (`admin.manage_models`).

### Health (PROPOSED)
- a per-provider rolling error rate and latency;
- a circuit breaker;
- an alert when a provider degrades.

## To be defined
- the initial provider and model list and their prices;
- the default budgets;
- the retry counts and timeouts per purpose;
- the response-schema validation library (the proposal lists `jsonschema` as a new dependency);
- how a LangGraph chat model wraps the gateway, so usage tracking stays in one place.
