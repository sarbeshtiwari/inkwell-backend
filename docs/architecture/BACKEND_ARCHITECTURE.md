# Backend architecture

**Status:** CURRENT IMPLEMENTATION, verified in source at local `main` `a83d17e` · **Last reviewed:** 2026-11-26

All paths are relative to `backend/`. Line numbers are from `a83d17e`. Anything that exists only from `45e2fec` on
(Step 2 and later), and so is **not in production `8b59986`**, is marked **[a83d17e]**.

## 1. Layout

| Folder | Role |
|---|---|
| `app.py` | Wiring only. Imports `core`, `routes` and `features`; registers 16 blueprints; starts background loops |
| `core/` | Building blocks: `config`, `crypto`, `db`, `http` (Flask app object), `security`, `accounts`, `workspace`, `storage`, `mail`, `sending`, `sentmail`, `bounces`, `whatsapp_store`, `tables`, `validation`, `pages` |
| `routes/` | HTTP routes registered directly on `app`: `auth`, `contacts`, `leads`, `operations` **[a83d17e]**, `send`, `site_pages`, `whatsapp`, `workspace_api` |
| `features/` | Product areas as Flask blueprints: `ai_jobs/`, `apps/`, `learning/`, `research/`, `github`, `jobs`, `linkedin`, `notify`, `inbox`, `replies`, `portfolio`, `site`, `finder`, `extension`, `updater`, `nearby` |
| `infra/` | Platform foundation from Phase 0: `rbac`, `audit`, `redact` (Step 1, in production); `locks`, `operations`, `schedule` **[a83d17e]** |
| `bridge.py` | Shared helpers for feature modules: `login_required`, `http()`, `every()`, `with_deadline()` |
| `migrations.py` | One-off SQLite → MongoDB import (runs only when `data/app.db` exists and `users` is empty) |
| `tools/` | `local_replset.py` (set up a local replica set), `bench_locks.py` (lock latency benchmark) **[a83d17e]** |
| `tests/` | pytest suite ([../engineering/TESTING_STRATEGY.md](../engineering/TESTING_STRATEGY.md)) |
| `whatsapp.py` | Playwright automation of WhatsApp Web; also usable as a standalone CLI |
| Local-only scripts | `migrate_to_live.py`, `push_with_reachout_token.py`, `rename_repos.py`, `set_password.py`. Not tracked by git (listed in `.git/info/exclude`); not part of the application |

## 2. Initialization (`app.py`)

1. Importing `core.config` reads `.env` from the project root and `backend/` (`core/config.py:load_dotenv`).
   - Real environment variables always win (`os.environ.setdefault`).
   - `INKWELL_NO_DOTENV=1` turns this off (the tests set it).
2. `core.http` creates `app = Flask("app", …)` and installs log redaction (`_install_redaction`).
   - It sets the session cookie options.
   - It wraps the app in `ProxyFix` when `TRUST_PROXY=1`.
   - It initialises Sentry when `SENTRY_DSN` is set.
3. `init_storage()` (`core/accounts.py:50`):
   - refuses to start a production server with weak settings (`check_config`: https `SITE_URL`, `SECRET_KEY` length);
   - connects to MongoDB (`M.connect()`) and creates the core indexes;
   - calls `ensure_indexes()` for `locks`, `operations` and `schedule` **[a83d17e]**, and for `audit` and `rbac`;
   - reconciles Super Admin roles from `ADMIN_EMAILS` (`rbac.bootstrap`).
4. Importing `routes.*` registers their URLs on `app`.
5. `bridge.setup()`, then `notify.init()`, `inbox.ensure_indexes()` and `replies.init()` run, and the 16 blueprints
   are registered (`app.py:81`).
6. If `BOUNCE_WATCH` is `"1"` (the default), the 8 recurring loops start (§6).
7. Run directly (`python app.py`), Flask's threaded server listens on `127.0.0.1:5050`.
   - `python app.py move-files-to-cloudinary` migrates GridFS files to Cloudinary.
   - Production runs `gunicorn app:app --workers 1 --threads 16 --timeout 120` (shell-form `CMD` in `Dockerfile`).

## 3. Configuration

All settings come from environment variables, in `core/config.py` unless noted. Values are never documented here;
`render.yaml` lists which ones are set in production (`sync: false` means entered in the Render dashboard).

| Group | Variables |
|---|---|
| Runtime | `APP_ENV` (`production` turns on production checks), `HOST`, `PORT`, `DATA_DIR`, `OPEN_BROWSER`, `INKWELL_NO_DOTENV`, `BOUNCE_WATCH`, `NO_WORKERS` (set by tests) |
| Database | `MONGODB_URI` (default `mongodb://127.0.0.1:27017`), `MONGODB_DB` (default `reachout`) |
| Secrets | `SECRET_KEY` (data key; falls back to `data/secret.key`), `AUDIT_KEYS`, `AUDIT_KEY_ID` (`infra/audit.py`), `NETLIFY_PROXY_SECRET` |
| Web | `SITE_URL`, `LANDING_URL`, `COOKIE_SECURE`, `TRUST_PROXY`, `STREAM_SECONDS`, `ALLOW_SIGNUP` |
| Email | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`, `SYSTEM_MAIL_PER_HOUR`, `DAILY_LIMIT`, `ALLOW_PRIVATE_MAIL_HOSTS` (`core/mail.py`) |
| Files | `CLOUDINARY_URL`, `MAX_FILE_MB`, `MAX_FILES_MB` |
| WhatsApp | `WHATSAPP_ENABLED` (production: `0`), `WA_HEADLESS`, `WA_SESSION_HOURS`, `MAX_BROWSERS`, `MIN_WA_DELAY`, `CHROMIUM_NO_SANDBOX` |
| Admin and legal | `ADMIN_EMAILS`, `OPERATOR_NAME`, `JURISDICTION` |
| Integrations | `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` (`features/linkedin.py`), `SENTRY_DSN`, `OLLAMA_URL`, `OLLAMA_MODEL` (`features/ai_jobs/common.py`) |
| Coordination **[a83d17e]** | `LOCK_LEASE_SECONDS` (30), `LOCK_RENEW_SECONDS` (10), `LOCK_SAFETY_SECONDS` (5), `LOCK_TXN_SECONDS` (10), `LOCK_WAIT_INTERACTIVE_SECONDS` (18), `LOCK_WAIT_BACKGROUND_SECONDS` (45), `LOCK_SLOW_WAIT_SECONDS`, `LOCK_SLOW_HOLD_SECONDS` (`infra/locks.py`); `OPERATIONS_KEEP_DAYS` (7, `infra/operations.py`) |
| Misc | `MAX_CONTACTS`, `MAC_ALERTS` (`features/notify.py`), `DISPLAY` (`features/ai_jobs/apply.py`) |

Not implemented (planned in `phase0-checklist.md`):
- `PLATFORM_ANTHROPIC_KEY`, `PLATFORM_OPENAI_KEY`;
- the rollback flags `SCHEDULER`, `LOCKS`, `PROGRESS`, `GATEWAY`.

## 4. HTTP surface

- **Rules and handlers:** 247 URL rules served by 245 handlers.
  - 69 rules are registered on `app` (from `routes/`); 178 are on 16 blueprints, none with a `url_prefix`.
  - By method: GET 99, POST 111, PUT 27, DELETE 10. Write methods: 148.
- **Call graph:** the deployment write-path inventory counts 180 endpoints that "write or cause external effects".
  That count comes from a call-graph analysis, so it includes some GETs with side effects.

The full table is in [../engineering/API_DOCUMENTATION.md](../engineering/API_DOCUMENTATION.md).

## 5. Request-level behaviour (`core/security.py`, `core/http.py`)

| Concern | Implementation |
|---|---|
| Netlify-only ingress | `before_request only_via_netlify`. When `NETLIFY_PROXY_SECRET` is set, every path except `/healthz` needs a valid `X-Nf-Sign` HS256 JWT (`netlify_signed`); otherwise 404 |
| CSRF | `before_request guard`. Non-GET/HEAD `/api/*` requests need `X-Requested-With: fetch` (400 otherwise). Session cookie `SameSite=Lax`, `HttpOnly`, `__Host-` prefix when secure |
| Authentication | `login_required` (`core/security.py:184`); details in [../engineering/AUTHENTICATION_AND_RBAC.md](../engineering/AUTHENTICATION_AND_RBAC.md) |
| Rate limits | `rate_limited(key, limit, window)`: fixed-window counters in `ratelimits`, keys hashed, TTL. Called inside handlers; no global limiter |
| Headers | `after_request security_headers`: nosniff, `X-Frame-Options: DENY`, Referrer-Policy, Permissions-Policy, COOP, HSTS, CSP with per-request nonce, `no-store` on `/api/*`, noindex on `/api/*` and `/app*` |
| Errors | [../engineering/ERROR_HANDLING_AND_RECOVERY.md](../engineering/ERROR_HANDLING_AND_RECOVERY.md) |
| Log redaction | `infra/redact.py`: `RedactingFilter` on the root, app, werkzeug and gunicorn loggers, plus `install_stream_redaction` for stdout and stderr |

## 6. Background work and concurrency

### Recurring loops
There are 8 loops, started only when `BOUNCE_WATCH=1`. Each `bridge.every(seconds, fn, name)` is a daemon thread that
sleeps `seconds` and then runs `fn`.
- **In `a83d17e`:** before running, it takes the lease `loop:<name>` (`locks.try_acquire`) and claims a shared slot
  (`schedule.claim`, 0.9 × interval). Each loop therefore runs at most once per interval across all processes.
- **In `8b59986`:** coordination was in-process only.

| Loop | Interval | Registered at | Does |
|---|---|---|---|
| `bounce-watcher` | 30 s | `app.py:85` | IMAP bounce checks for accounts that sent in the last 3 days (`core/bounces.py:bounce_tick`) |
| `wa-expiry` | 60 s | `app.py:86` | Deletes saved WhatsApp logins older than `WA_SESSION_HOURS` (`core/whatsapp_store.py`) |
| `send-queue` | 20 s | `features/jobs.py:843` | Sends due queued emails and scheduled LinkedIn posts (`run_queue`, atomic claim `queued → sending`) |
| `job-alert-scan` | 600 s | `features/jobs.py:844` | Reads job-alert emails for users with auto-scan on |
| `daily-sync` | 60 s | `features/apps/sync.py:142` | Morning mail sync per account at its configured local time |
| `reply-watcher` | 60 s | `features/replies.py:500` | Detects replies to sent emails |
| `ai-weekly-digest` | 3600 s | `app.py:90` | Monday digest notification |
| `learning-briefing` | 300 s | `app.py:91` | Builds the daily briefing after 07:00 local time |

Per-account work inside loops uses `schedule.claim(name, interval, uid)`, `schedule.once(...)` **[a83d17e]** and
`bridge.with_deadline(seconds, fn)`. The deadline only stops waiting; it doesn't stop the thread.

### User-started operations **[a83d17e]**
- `infra/operations.py` `start(uid, kind, key)` takes the lease `op:<kind>[:<key>]`, so there is one run per user,
  kind and key across processes (409 on a second start). It inserts an `operations` document.
- `spawn(op, fn)` runs the work in a daemon thread.
- The allowed kinds are `send`, `inbox`, `apps_sync`, `mail_sync`, `nearby`, `ai_evaluate`, `ai_apply`, `ai_match`,
  `finder`, `briefing`, `research` and `wa_link` (`infra/operations.py:50`).
- States: `running → done | failed | cancelled | interrupted`.
- Cancellation is cooperative: `cancel_requested`, then `checkpoint()` between units of work. Nothing is killed.
- External actions call `checkpoint()` right before acting.
- `operations.spawn` / `operations.start` are called from 28 places.

There are 14 plain `threading.Thread(` call sites outside `tools/`:
- `bridge.py` (2);
- `core/sending.py` (1: the campaign worker, which holds the operation lease);
- `features/jobs.py` (1: "send now");
- `features/notify.py` (3: push fan-out and macOS alerts);
- `features/updater.py` (3);
- `infra/locks.py` (1: the lease renewer);
- `infra/operations.py` (1: `spawn`);
- `routes/leads.py` (1: notifying admins).

### Locks and versioned writes **[a83d17e]**
- `Workspace.lock` is a MongoDB lease (`locks.user_lock(uid)`), re-entrant within one thread.
  - Waits are bounded: 18 s in requests, 45 s in background work. A timeout returns 409 with `Retry-After`.
  - `Workspace.save` writes through `locks.guarded_write`, a transaction that re-checks the lease.
- `Workspace.update` is optimistic compare-and-set for the keys `templates`, `inbox_companies` and `ai_portals`.

Design: [../locks-progress-cancellation.md](../locks-progress-cancellation.md),
[DATABASE_ARCHITECTURE.md §Concurrency](DATABASE_ARCHITECTURE.md#concurrency-mechanisms).

### Process-local state that remains
Even in `a83d17e`, these stay in process memory:
- `features/ai_jobs/common.py`: `LIVE` (live browser frames), `HANDOVER`, `APPLY_LOCK` (one browser at a time);
- `features/updater.py`: `STATE`;
- `core/workspace.py`: `DELETED`, the "account being deleted" flag that stops background writers;
- `features/site.py`: the preview-token store.

## 7. AI and model calls

### `chat()`
- Signature: `features/ai_jobs/models.py:117`,
  `chat(cfg, system, human, as_json=False, max_tokens=700, temperature=0.2, web=False, timeout=90)`.
- `models.config(ws)` returns the user's configured model, or `None` when none is usable. Every caller checks for
  `None` and falls back to rules-based output.

### Providers (`PROVIDERS`)

| Provider | How it is called | Allowed where |
|---|---|---|
| `claude_cli`, `kiro_cli` | Local CLI subprocess (`cli_llm.py`); Claude runs `--tools ""` unless `web=True` (then `WebSearch`/`WebFetch` only) | Only when not in production |
| `ollama` | LangChain `ChatOllama` | Anywhere (`OLLAMA_URL` is empty in production, so hosted use needs one configured) |
| `anthropic`, `openai`, `gemini`, `openrouter`, `groq`, `mistral`, `deepseek` | Raw `requests.post` with the **user's own key**; the timeout is fixed at 90 s and the `timeout` argument is ignored for these | Anywhere |
| `local` | Any OpenAI-compatible server on localhost | Only when not in production |

### Per-user settings
- `ai_model` holds `{provider, model, base_url}`.
- `ai_keys["llm_<provider>"]` holds the key, inside the encrypted `kv` record. The browser only ever sees booleans
  (`models.public()`).

### Behaviour of a call
- One system turn and one user turn.
- JSON mode is native only for Ollama; elsewhere the first JSON object is parsed out of the text.
- No retries for HTTP providers. The CLI retries once for JSON.
- **Usage fields from the provider are discarded:** no tokens, no cost, no exact model id.
- Only a human-readable `engine` label is stored on some outputs: evaluation reports, match runs, tailored resumes and
  prep.

### Callers
- **`models.py:213`:** connection test.
- **`ranking.py:142`:** `llm_review` of the top 6 matches.
- **`evaluate.py:147`:** `llm` wrapper, used for `llm_blocks`, `cover_letter` and `draft_answers`.
- **`extras.py:68`, `:94`, `:477`:** `resume_structure`, the tailoring proposal (checked afterwards by
  `tailor_rules`), and `make_prep`.
- **`research/company.py:175`, `:183`:** CLI web research, or extraction from crawled pages; every claim is
  re-validated against its page.
- **`learning/briefing.py:109`:** briefing notes.

### LangChain usage
- `RunnableLambda` chains in `ranking.py`.
- `@tool`-decorated source functions in `sources.py`, invoked directly. There is no agent loop.
- `ChatPromptTemplate` in `evaluate.py`.

## 8. Product areas

| Area | Main files | Summary |
|---|---|---|
| Outreach | `core/sending.py`, `core/mail.py`, `routes/send.py`, `routes/contacts.py`, `core/tables.py` | Contacts, templates with `{name}`/`{company}`, campaigns by email (and WhatsApp locally), daily limit `DAILY_LIMIT` (default 100), MX checks, open pixel |
| AI job match | `features/ai_jobs/` (`runs.py`, `sources.py`, `boards/` with 118 modules, `providers.py` = job sources, `sitejobs.py`, `portals.py`, `ranking.py`, `learning.py`, `signals.py`, `evaluate.py`, `extras.py`, `tailor_rules.py`, `latex.py`, `answers.py`, `forms.py`, `apply.py`, `siteapply.py`, `watch.py`, `tracker.py`) | Discovery → rank → learned nudge → optional model review → rules → evaluation (A–H report) → tailoring (reorder only) → apply with consent |
| Applications tracker | `features/apps/` | Mailbox sync (IMAP read-only), status classification by regex, Naukri stats from email |
| Inbox, replies | `features/inbox.py`, `features/replies.py` | Inbox grouped by company; reply detection and intent by regex; reply drafts sent only by the user |
| Jobs from alerts, hiring posts, queue | `features/jobs.py` | Alert-email parsing and scoring (`score_job`), post parsing, send queue |
| Learning | `features/learning/` | 90-day course, DSA, spaced review (FSRS-4.5), daily briefing |
| Research | `features/research/` | Company facts with page-quote validation |
| Website and portfolio | `features/site.py`, `features/portfolio.py`, `features/site_notebook.py` | Personal site builder, public `/p/<slug>`, GitHub-backed project list |
| Integrations | `features/github.py`, `features/linkedin.py` (official OAuth and posting API; no scraping), `features/notify.py` (in-app, SSE, web push), `features/extension.py` (Chrome autofill; never submits), `features/nearby.py` (OpenStreetMap, optional Google Places), `features/finder.py` (role addresses from public sites, respects robots.txt), `features/updater.py` (local only) | |

Pipeline and consent details: [../engineering/APPROVAL_AND_PERMISSION_MODEL.md](../engineering/APPROVAL_AND_PERMISSION_MODEL.md).

## 9. Dependencies (`requirements.txt`, pinned; `requirements.lock.txt` constrains transitive versions)
- `flask==3.1.3`, `gunicorn==26.2.0`, `pymongo==4.18.2`, `dnspython==2.8.0`, `cryptography==50.0.1`
- `requests==2.32.5`, `playwright==1.63.0`, `openpyxl==3.1.5`, `pypdf==6.1.1`
- `pywebpush==2.5.0`, `sentry-sdk[flask]==2.19.2`
- `langchain-core==1.6.6`, `langchain-ollama==1.1.0`
- Dev: `pytest==8.3.4`

## 10. Known limitations
- Background work shares the web process. It stops on deploys and crashes, and long work holds threads out of the 16.
- No model usage accounting, no retries for model calls, and the provider timeout is fixed.
- Two scoring stacks for jobs (`features/jobs.py:score_job` and `ai_jobs/ranking.py`), as noted in the original
  assessment.
- Process-local state remains (§6). A second process wouldn't see live browser frames or the updater's state.
- `Dockerfile` uses a shell-form `CMD`, so gunicorn isn't PID 1. The effect on signal delivery is discussed in
  [../deployment/DEPLOYMENT_GATE_ARCHITECTURE.md](../deployment/DEPLOYMENT_GATE_ARCHITECTURE.md).
