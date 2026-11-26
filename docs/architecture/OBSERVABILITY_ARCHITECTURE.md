# Observability architecture

**Status:**
- Current: CURRENT IMPLEMENTATION (logs, Sentry, lock metrics, audit).
- Agent telemetry and the Observatory: APPROVED requirement; design PROPOSED; **not implemented**.

**Last reviewed:** 2026-11-26

## Today

| Signal | Implementation | Notes |
|---|---|---|
| Application logs | Python logging and `print`, all through `infra/redact` filters | Render's log stream. Retention is UNKNOWN |
| Exceptions | Sentry (`SENTRY_DSN`, optional): `send_default_pii=False`, `traces_sample_rate=0.0`, scrubbed `before_send` | Whether a DSN is set in production is UNKNOWN |
| Browser errors | `POST /api/client-errors` (rate-limited) | Logged server-side |
| Lock metrics **[a83d17e]** | `infra/locks.py:metrics()`: wait and hold times, timeouts, database failures per lock kind and operation; slow waits and holds logged on `inkwell.locks` | No account ids in the metrics |
| Operation progress **[a83d17e]** | `operations` documents (`public` = non-personal figures) | Readable by the owner; Super Admin can cancel |
| Audit trail | `audit_log` (hash chain) | Admin actions and role changes only |
| Health | `GET /healthz` (database ping) | Used by Render (`healthCheckPath`) |
| Traces and spans, usage, cost | **None** | — |
| Admin dashboard | **None** | — |

## Requirement (approved 2026-11-25, requirement 9)
> "All model calls, tool calls, delegations, retries, approvals, errors and final outputs must produce correlated
> execution telemetry."

Super Admin visibility, metadata only ([ADR-0009](../decisions/ADR-0009-metadata-only-super-admin.md)):
- agent activity and health;
- execution history and status;
- duration and latency;
- model and token consumption;
- estimated costs;
- tool invocation metadata;
- errors and diagnostics.

## Proposed design

### Data model
- **`traces`:** one per goal or task tree.
  - Fields: `_id` = `trace_id`, `uid`, root task, status, `started`, `ended`, totals (tokens, cost, steps).
- **`spans`:** one per event. Types: `model_call`, `tool_call`, `delegation`, `approval`, `retry`, `error`,
  `final_output`.
  - **Correlation:** `trace_id`, `span_id`, `parent_span_id`, `task_id`, `run_id`, `agent_id`, `agent_version`,
    `model_config_version`, `tool_id`.
  - **Timing and outcome:** `start`, `end`, `ms`, `status`, `error_class`.
  - **Usage:** `tokens_in`, `tokens_out`, `cost`.
  - **`summary`:** redacted with `redact.pii`, so it holds no content.
  - **`payload_ref`:** the full payload, sealed, readable by the owner only.
  - **`expires_at`:** TTL.
- Spans are written by the gateway (model calls), the tool layer, and the runtime (delegation, approval, retry,
  final output). Writes are append-only.

### Rules
- Redaction happens **before** writing. Secrets are never stored. In summaries, emails and phone numbers are masked,
  and content goes only into the sealed payload.
- No hidden chain-of-thought is stored or shown (from the proposal).
- Admin views read only metadata fields. Content reveal stays disabled.
- Every number on the Observatory comes from stored telemetry; nothing is estimated in the UI.

### Agent Observatory (PROPOSED; admin pages that don't exist)
- **Minimal** (Phase 1): Overview, Agents, Traces (metadata), Models (usage, cost, latency, errors).
- **Later** (Phase 4): Logs search by trace, Memory explorer (metadata), Tools and grants matrix, Queue, Health,
  Alerts, Config (flags, budgets, retention).
- **Access:** gated by `admin.read_metadata`, a permission that already exists. Actions such as kill switches,
  promotions and budgets use their own permissions, and are audited.

### Alerts (PROPOSED, Phase 4)
Provider error-rate spike; stuck runs (repeated lease expiry); repeated failures per agent; queue age over a
threshold; budget near its limit; a missing worker heartbeat. Delivered in-app through the existing `notify()`.

### Optional export (PROPOSED, not decided)
- **OpenTelemetry OTLP to Langfuse Cloud:** optional.
- **Self-hosted Langfuse:** not recommended (it needs Postgres and ClickHouse).
- **Prometheus/Grafana:** not now.

## To be defined
- trace and span retention;
- which payloads are stored at all;
- sampling, if any;
- the Observatory's exact metrics and thresholds.
