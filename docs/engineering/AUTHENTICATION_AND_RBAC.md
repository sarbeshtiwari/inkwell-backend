# Authentication and RBAC

**Status:** CURRENT IMPLEMENTATION. RBAC and audit are a PRODUCTION IMPLEMENTATION (Step 1, `4cb8b1d`/`8b59986`,
deployed) and are tested by `tests/test_rbac_audit_redact.py` (19 tests) and `tests/test_step1_fixes.py` (24).
**Last reviewed:** 2026-11-26

## Sign-in methods (`routes/auth.py`)

| Method | Endpoints | Rules |
|---|---|---|
| Email code (passwordless) | `POST /api/auth/request-code`, `POST /api/auth/verify-code` | 6-digit code stored only as an HMAC (`otp_hash`). Expires in 600 s (`OTP_TTL`), 5 attempts (`OTP_ATTEMPTS`), 60 s resend cooldown, 15 wrong codes per email per day before a 24 h lock (`OTP_FAILS_PER_DAY`). Rate limits per IP and per email. With no SMTP outside production, codes are printed to the terminal (`DEV_OTP`) |
| Password | `POST /api/auth/signup`, `POST /api/auth/login`, `POST /api/account/password` | scrypt (`n=2^14, r=8, p=1`, 16-byte salt). Comparison runs against a dummy hash when none is stored, so timing doesn't reveal whether an account has a password. Rate limits per IP and per account |
| Sign-up switch | `ALLOW_SIGNUP` (default on) | When off, new accounts are refused |
| Terms | `TERMS_VERSION = "2026-10-01"` | Recorded on the user (`terms_accepted`) |

The frontend's `Auth.jsx` currently uses email + password ([../architecture/FRONTEND_ARCHITECTURE.md](../architecture/FRONTEND_ARCHITECTURE.md)).

## Sessions (`core/accounts.py`)
- **Starting a session** (`start_session`, :143): a random 32-byte token goes into the signed Flask session cookie.
  Only `lookup_hash("session:"+token)` is stored in `sessions`.
- **Lifetime:** 30 days (`SESSION_DAYS`), with a TTL index. A session unused for more than 7 days
  (`SESSION_IDLE_DAYS`) is deleted on its next use. `seen` is refreshed at most once an hour.
- **Reading the session** (`current_uid`, :154): cached per request in `g.uid`. It returns `None` if the user record
  no longer exists.
- **Cookie** (`core/http.py`):
  - name `__Host-ro_session` when `COOKIE_SECURE`, otherwise `ro_session`;
  - `HttpOnly`, `SameSite=Lax`, `Secure` when configured;
  - the Flask signing key is derived from `SECRET_KEY` (`_derive("reachout-session-v1")`).
- **Ending sessions:** `POST /api/auth/logout` ends this session; `POST /api/auth/logout-everywhere` deletes all of the
  user's sessions.
- **`login_required`** (`core/security.py:184`) returns 401 when there's no valid session. Otherwise it calls the view
  with `Workspace(uid)` as its first argument. Feature modules use the same decorator through `bridge.login_required`.

## Other credentials
- **Browser extension key** (`features/extension.py`): `Authorization: Bearer <uid>.<secret>`.
  - The secret is stored only as a hash (`ext_key_hash`).
  - It opens only the four `/api/ext/*` routes and is rate-limited to 600 requests per hour.
  - Users create and revoke it in the app.
- **Integration credentials** (user SMTP app passwords, GitHub token, LinkedIn OAuth token, search and model keys,
  Google key): stored sealed inside the user's `kv` records. They're never returned to the browser.

## Roles and permissions (`infra/rbac.py`)

| Role | Permissions |
|---|---|
| `user` | none (ordinary use of one's own workspace needs no permission) |
| `super_admin` | every permission below except `admin.reveal_content` |

| Permission | Meaning (from the code) | Checked by today |
|---|---|---|
| `admin.read_metadata` | See platform metadata: agent task status, timings, models, token use and costs, errors | **No route** (reserved for the Observatory) |
| `admin.site_leads` | Read and manage enquiries sent to Inkwell's own website | `routes/leads.py:86` (only with `?scope=site`) |
| `admin.integrations` | Configure platform-wide integrations (e.g. the LinkedIn app) | `features/linkedin.py:68` |
| `admin.update_app` | Update the app from its git folders (own computer only) | `features/updater.py:167`, `:218` |
| `admin.control_agents` | Start, stop, pause or kill-switch agents | `routes/operations.py:45` (cancel another user's operation) |
| `admin.manage_models` | Register, approve, promote or roll back models and model configurations | **No route** |
| `admin.manage_budgets` | Set usage budgets and limits | **No route** |
| `admin.manage_roles` | Grant or revoke roles | `rbac.grant()`; **no HTTP route** |
| `admin.reveal_content` | See a user's actual content (exceptional, audited) | **Disabled:** `CONTENT_REVEAL_ENABLED = False`; `has()` always returns false for it |

### Enforcement
- `rbac.check(uid, permission)` raises 403 and writes an `access.denied` audit event.
- `@rbac.require_permission(permission, audit_success=False)` goes below `@login_required`. With `audit_success=True`,
  the allowed call is audited **before** it runs, so if the audit can't be written the action doesn't happen.

### Role assignment
- **Bootstrap:** `rbac.reconcile`, run at startup (`init_storage`). It grants `super_admin` to existing accounts whose
  emails are in `ADMIN_EMAILS`, and writes the audit event first; it fails closed.
  - Roles previously granted through `ADMIN_EMAILS` whose email is no longer listed are revoked.
  - Revocations take effect at once. Their audit event goes through a durable outbox (`roles.pending_audit`), written
    by `flush_pending_audit()`.
- **Fallback** (`role_of`): with no stored role, a user is `user`. The exception is a computer that isn't a production
  server and has no `ADMIN_EMAILS` at all; there the oldest account is treated as `super_admin`, which keeps existing
  local setups working.
- **Manual changes:** `rbac.grant(actor, target, role)` needs `admin.manage_roles`. Changing your own role is refused
  and audited.

### What the frontend sees
`GET /api/me` returns `role` and a sorted `permissions` list, plus `is_admin` (kept for older callers;
`core/accounts.py:203` maps it to `role_of(uid) == super_admin`). No frontend page uses `permissions` yet.

## Audit log (`infra/audit.py`)
- One event per administrative action and role change. Fields: `seq`, `at`, `actor`, `action`, `target`, `outcome`,
  `request_id`, `trace_id`, `details` (PII-redacted), `key_id`, `prev_hash`, `hash`.
- **Ordering:** `seq` is taken by inserting under a unique index, so the sequence has no gaps or duplicates across
  processes. The insert is retried for up to 10 s.
- **Integrity:** `hash` is an HMAC over the canonical JSON and the previous hash.
  - Keys come from `AUDIT_KEYS` / `AUDIT_KEY_ID`, or the legacy `k0` derived from `SECRET_KEY`.
  - Each event records its `key_id`. Old events are verified with their own key.
- **Writes** use write concern `majority` with `j=True`. A failed write raises `AuditError`, and security-sensitive
  operations stop.
- **Verification:** `verify()` recomputes the chain. `checkpoint()` returns the head, for external export.
- **Limitation (from the code):** someone holding both the signing key and database write access could rebuild a
  valid-looking chain. The log is tamper-evident, not immutable. External anchoring
  ([../audit-anchoring.md](../audit-anchoring.md)) is designed but **not implemented**.

## Redaction (`infra/redact.py`)
- **`secrets(value)`:**
  - whole values under secret-looking key names (`password`, `token`, `api_key`, `cookie`, `x-nf-sign`, …);
  - PEM private keys;
  - provider keys (`sk-ant-`, `sk-`, GitHub, Google, Slack, Tavily, AWS, Groq prefixes);
  - JWTs, bearer, basic and token strings;
  - `user:password@` in URLs;
  - `key=value` secrets.
- **`pii(value)`:** `secrets()` plus email masking (`a***@acme.com`) and phone replacement.
- **Where it is applied:** to all logging (`RedactingFilter`, stdout/stderr wrapping), to Sentry events (`core/http.py`
  `before_send`), to audit `details`, and to `error_text(exc)` used by background work.

## Related
[SECURITY_AND_GOVERNANCE.md](SECURITY_AND_GOVERNANCE.md),
[../decisions/ADR-0009-metadata-only-super-admin.md](../decisions/ADR-0009-metadata-only-super-admin.md)
