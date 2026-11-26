# Monitoring and alerting

**Status:** CURRENT IMPLEMENTATION is minimal; agent monitoring is PROPOSED · **Last reviewed:** 2026-11-26

## Current

| What | Mechanism | Status |
|---|---|---|
| Liveness | Render health check on `GET /healthz` (database ping; exempt from the Netlify check) | Configured in `render.yaml` |
| Exceptions | Sentry, when `SENTRY_DSN` is set (scrubbed) | Whether it is set in production is UNKNOWN |
| Logs | Render log stream; all output redacted (`infra/redact.py`) | Retention UNKNOWN |
| Browser errors | `POST /api/client-errors` → server log | Implemented |
| Lock health **[a83d17e]** | `infra/locks.metrics()`; slow-wait and slow-hold logs on `inkwell.locks` | Not exposed by any endpoint or dashboard |
| Admin alerts | `routes/leads.py` notifies admins of new site enquiries | Implemented |
| Alerts on errors, latency, queue age or cost | — | **None** |
| Dashboards | — | **None** |

## Gaps
- No alert routing: nothing pages or notifies the operator on errors or downtime, apart from Render's and Sentry's own
  notifications, if configured (UNKNOWN).
- No uptime monitor outside Render.
- No metrics endpoint.

## Proposed (with the agent platform)
These are the alerts listed in [../architecture/OBSERVABILITY_ARCHITECTURE.md](../architecture/OBSERVABILITY_ARCHITECTURE.md#alerts-proposed-phase-4):
- a provider error-rate spike;
- stuck runs;
- repeated failures per agent;
- queue age over a threshold;
- a budget near its limit;
- a missing worker heartbeat.

They are delivered in-app through `notify()` to Super Admins, and visible in the Observatory. Thresholds are TO BE
DEFINED.
