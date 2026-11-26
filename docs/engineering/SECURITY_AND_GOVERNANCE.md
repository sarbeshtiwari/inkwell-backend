# Security and governance

**Status:** Mixed. Each row is classified. · **Last reviewed:** 2026-11-26

Classifications used in this document:
- **PROD:** production implementation (`8b59986`).
- **TESTED:** tested implementation (`a83d17e`, local only).
- **APPROVED:** approved design, not implemented.
- **PROPOSED:** proposed design.
- **PENDING:** pending decision.

## Summary

| Area | Status | Current state | Required / planned | Document |
|---|---|---|---|---|
| User and Super Admin roles, permissions | PROD | `infra/rbac.py`: 2 roles, 9 permissions | Admin routes for models, budgets, kill switches (APPROVED) | [AUTHENTICATION_AND_RBAC.md](AUTHENTICATION_AND_RBAC.md) |
| Tenant isolation | PROD (app) / APPROVED (agents) | `uid` scoping everywhere | `ToolContext`, isolation tests | [TENANT_ISOLATION.md](TENANT_ISOLATION.md) |
| Audit chain | PROD | HMAC chain, fail-closed | External anchoring (PROPOSED, [../audit-anchoring.md](../audit-anchoring.md)) | [AUTHENTICATION_AND_RBAC.md](AUTHENTICATION_AND_RBAC.md#audit-log-infraauditpy) |
| Redaction | PROD | Logs, Sentry, audit details, error text | Telemetry summaries (APPROVED with observability) | same |
| Data encryption at rest | PROD | Fernet `seal` on all content; files encrypted before Cloudinary or GridFS | — | [../architecture/DATABASE_ARCHITECTURE.md](../architecture/DATABASE_ARCHITECTURE.md) |
| Secret management | PROD | Environment variables (Render dashboard, `sync: false`); `.env` locally; `data/secret.key` fallback | Platform model keys in env only (APPROVED) | below |
| Model key management | PROD (BYOK) / APPROVED (platform) | User keys sealed in `kv`, never returned | Platform-managed keys first ([ADR-0004](../decisions/ADR-0004-platform-managed-model-keys.md)) | [../architecture/MODEL_GATEWAY_ARCHITECTURE.md](../architecture/MODEL_GATEWAY_ARCHITECTURE.md) |
| Tool grants | APPROVED (not built) | — | Per agent version | [APPROVAL_AND_PERMISSION_MODEL.md](APPROVAL_AND_PERMISSION_MODEL.md) |
| External action approval | APPROVED (not built for agents) | Feature-specific consent (auto-apply is standing consent) | Per action, always | same |
| Content reveal policy | PROD (disabled) | `CONTENT_REVEAL_ENABLED = False` | Only after a privacy policy update and an immutable audit; explicit authorization and reason | [../decisions/ADR-0009-metadata-only-super-admin.md](../decisions/ADR-0009-metadata-only-super-admin.md) |
| Budgets | APPROVED (not built) | Only the email `DAILY_LIMIT` and feature rate limits | Per user day/month, per agent, per task, global | [../operations/COST_AND_USAGE_GOVERNANCE.md](../operations/COST_AND_USAGE_GOVERNANCE.md) |
| Rate limits | PROD | `ratelimits` fixed windows per endpoint | Per-provider limits in the gateway (PROPOSED) | [API_DOCUMENTATION.md](API_DOCUMENTATION.md) |
| Execution depth and limits | APPROVED (not built) | — | Max depth (proposed 3), max children, deadlines, cycle detection, max steps | [../architecture/WORKER_AND_QUEUE_ARCHITECTURE.md](../architecture/WORKER_AND_QUEUE_ARCHITECTURE.md) |
| Cancellation | TESTED | Cooperative cancellation in `operations` (own; Super Admin via `admin.control_agents`) | Same model for agent tasks | same |
| Kill switches (global / per agent / per user) | APPROVED (not built) | — | Checked at claim and at checkpoints | [../decisions/ADR-0008-budgets-and-execution-controls.md](../decisions/ADR-0008-budgets-and-execution-controls.md) |
| Data retention | PROD (partial) | TTLs on sessions, OTPs, rate limits, notifications (90 d), operations (7 d), once markers; `send_log` and `kv` until account deletion; audit never deleted | Trace, usage and memory retention: PENDING | [../architecture/DATABASE_ARCHITECTURE.md](../architecture/DATABASE_ARCHITECTURE.md) |
| Privacy requirements | PROD (policy page) | `web/privacy.html`; metadata-only promise | Update before any reveal or admin content view (APPROVED condition) | [../architecture/FRONTEND_ARCHITECTURE.md](../architecture/FRONTEND_ARCHITECTURE.md#privacy-promise) |
| No unrestricted access for agents | APPROVED | — | No keys, credentials, raw database or shell for agents | below |

## Agent security requirements (approved 2026-11-25, requirement 10)
> "Never expose API keys, credentials, unrestricted database access or unrestricted shell execution to agents."

**Design consequences (PROPOSED):**
- Agents interact only through registered tools.
- Tools receive a `ToolContext` and resolve credentials internally (for example, the user's sealed SMTP password)
  without returning them.
- Model prompts and telemetry go through `redact.secrets()`.
- The existing local Claude CLI integration (`cli_llm.py`) runs `--tools ""` by default. With `web=True` it allows only
  `WebSearch` and `WebFetch`, and it is refused in production.
- Playwright automation (apply, WhatsApp) is a tool with fixed behaviour, not a general browser for the model.

## Ingress and request security (PROD)
- Requests pass the Netlify signature check, the `X-Requested-With` CSRF header and secure cookies.
- CSP with nonces, HSTS, and frame denial are set on responses.

Details: [../architecture/BACKEND_ARCHITECTURE.md §5](../architecture/BACKEND_ARCHITECTURE.md#5-request-level-behaviour-coresecuritypy-corehttppy).

## Startup guard (PROD)
- `core/accounts.py:check_config` refuses a production start without an `https://` `SITE_URL`, or with a `SECRET_KEY`
  shorter than 32 characters.
- It warns when `ADMIN_EMAILS` is empty or `COOKIE_SECURE=0`.

## Secret handling rules
- Never commit `.env`, `data/secret.key`, `*.key` or `*.pem`. They are listed in `.gitignore`.
- **`SECRET_KEY` is the root key.**
  - It derives the data key, the lookup-hash key, the session signing key and the legacy audit key `k0`.
  - Losing it makes data unreadable; leaking it exposes all sealed data.
  - Rotation is **not implemented**. Audit keys can rotate (`AUDIT_KEYS`).
- **Known risk:** the developer Mac's `.env` holds the production `MONGODB_URI` with the production database user.
  - So a local run writes to production. The tests are protected: `tests/conftest.py` removes `MONGODB_URI` and sets
    `INKWELL_NO_DOTENV=1`.
  - The deployment documents treat a local run against production as a concurrent instance.
  - The remedy (pointing `.env` at local MongoDB) is task SEC-02 in the
    [master task register](../project/MASTER_TASK_REGISTER.md).

## Known inconsistencies
1. "Immutable audit event" is required for content reveal, but the log is tamper-evident only.
2. Auto-apply uses standing consent. Agents must not reuse it (D-07).
3. `roles` rows survive account deletion (D-10).
4. `admin.read_metadata`, `admin.manage_models`, `admin.manage_budgets` and `admin.manage_roles` exist as permissions,
   but no route uses them.
