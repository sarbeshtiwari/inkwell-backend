# API documentation

**Status:** CURRENT IMPLEMENTATION (`a83d17e`; routes marked **[a83d17e]** are not in production `8b59986`) ·
**Last reviewed:** 2026-11-26

This is an inventory of every route, grouped by module. Request and response bodies aren't specified here. Each
handler validates its own input (`core/validation.py`), and the handler file is the reference.

## Conventions
- **Base:** same origin. The browser calls the Netlify site, which forwards to Render with a signature.
- **Writes:** non-GET `/api/*` requests need the header `X-Requested-With: fetch`.
- **Errors:** JSON `{error, field?}`, with `Retry-After` on retryable 409s
  ([ERROR_HANDLING_AND_RECOVERY.md](ERROR_HANDLING_AND_RECOVERY.md)).
- **Totals:** 247 URL rules on 245 handlers. 69 are on `app` (`routes/`); 178 are on 16 blueprints, none with a
  prefix. GET 99, POST 111, PUT 27, DELETE 10.
- **Auth column key:**
  - **L**: needs a session (`login_required`).
  - **P(x)**: also needs permission x.
  - **Pub**: public.
  - **Ext**: needs the extension bearer key.
  - **RL**: rate-limited inside the handler.

## Proposed agent APIs (PROPOSED DESIGN, not implemented)
From `inkwell-agentic-architecture.md` §7; none exists.
- **User:**
  - `POST /api/agents/tasks`, `GET /api/agents/tasks/<id>`, `GET /api/agents/tasks/<id>/events` (SSE)
  - `GET|POST /api/agents/approvals[/<id>]`
  - `GET|DELETE /api/agents/memory`
- **Admin** (`/api/admin/*`, Super Admin, every call audited):
  - overview, agents and versions, runs and traces (metadata only), models and configs, tools and grants, queue,
    budgets, kill switches, alerts, config.

The only `/api/admin/*` route that exists today is the operation cancel below.

## Auth and account (`routes/auth.py`)
| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/auth/request-code` | Pub, RL | Email a 6-digit login or sign-up code |
| POST | `/api/auth/verify-code` | Pub, RL | Verify a code and start a session |
| POST | `/api/auth/signup` | Pub, RL | Sign up with a password |
| POST | `/api/auth/login` | Pub, RL | Log in with a password |
| POST | `/api/account/password` | L, RL | Set or change the password |
| POST | `/api/auth/logout` | Pub | End this session |
| POST | `/api/auth/logout-everywhere` | L | End all sessions |
| GET | `/api/me` | L | User, `role`, `permissions`, `is_admin`, limits, feature switches |
| POST | `/api/account/delete/request` | L | Email an account-deletion code |
| POST | `/api/account/delete` | L, RL | Delete the account (code or password) |

## Operations **[a83d17e]** (`routes/operations.py`)
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/operations/<kind>` | L | The user's latest run of this kind (`?key=`) |
| POST | `/api/operations/<kind>/cancel` | L | Cancel own running task (idempotent) |
| POST | `/api/admin/operations/<uid>/<kind>/cancel` | L+P(`admin.control_agents`) | Super Admin cancels another user's task (metadata only) |

## Workspace (`routes/workspace_api.py`), contacts (`routes/contacts.py`), sending (`routes/send.py`)
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/state` | L | Full workspace state for the app |
| POST, DELETE | `/api/templates`, `/api/templates/<tid>` | L | Save or delete a template |
| PUT | `/api/profile` | L | Sender profile and SMTP settings |
| POST | `/api/profile/test` | L, RL | Test the mail settings |
| POST, GET, DELETE | `/api/documents`, `/api/documents/<name>` | L | Upload, view or delete files |
| POST | `/api/settings` | L | Save settings |
| POST | `/api/sentmail/scan`, `/api/sentmail/import` | L (scan RL) | Import contacts from the Sent folder |
| POST, PUT | `/api/recipients`, `/api/recipients/<rid>` | L | Add or edit a contact |
| GET | `/api/contacts/<rid>` | L | Contact detail |
| POST, DELETE | `/api/contacts/<rid>/notes`, `/api/contacts/<rid>/notes/<nid>` | L | Contact notes |
| POST | `/api/recipients/delete`, `/api/recipients/reset`, `/api/recipients/import` | L | Bulk delete, reset, import |
| GET | `/api/recipients/export` | L | Export contacts |
| GET | `/api/recipients/sample` | Pub | Sample import sheet |
| POST | `/api/preview` | L | Preview a personalised message |
| POST | `/api/send` | L | **Start a campaign (external action)** |
| POST | `/api/bounces/check` | L, RL | Check bounces now |
| GET, POST | `/api/job`, `/api/job/stop` | L | Campaign status, stop |
| GET | `/api/history` | L | Send history |

## Leads (`routes/leads.py`), WhatsApp (`routes/whatsapp.py`)
| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/leads` | Pub, RL | Contact-form enquiry |
| GET, PUT, DELETE | `/api/leads`, `/api/leads/<lid>` | L; `?scope=site` needs P(`admin.site_leads`) | List, update or delete enquiries |
| GET | `/api/leads/export` | L (same scope rule) | CSV export |
| GET | `/api/whatsapp` | L | Link status |
| POST, GET | `/api/whatsapp/link`, `/api/whatsapp/qr`, `/api/whatsapp/cancel`, `/api/whatsapp/unlink` | L | Linking. Returns 404 when `WHATSAPP_ENABLED=0`, except `GET /api/whatsapp` |

## Site pages and public files (`routes/site_pages.py`)
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET, POST | `/api/prefs` | Pub | Theme and sidebar preference cookies |
| GET | `/`, `/contact`, `/privacy`, `/terms`, `/robots.txt`, `/sitemap.xml`, `/site.webmanifest` | Pub | Public pages when served by Flask |
| GET | `/login`, `/signup`, `/app`, `/app/<path>` | Pub (the page checks the session) | App shell |
| GET | `/dist/<path>`, `/downloads/<path>`, `/assets/<path>` | Pub | Static files |
| GET | `/o/<token>.gif` | Pub (token) | Email open pixel |
| POST | `/api/client-errors` | Pub, RL | Browser crash reports |
| GET | `/healthz` | Pub, **exempt from the Netlify check** | Uptime and database ping |

## Blueprints (`features/`)

| Blueprint (file) | Routes | Auth | Paths |
|---|---|---|---|
| `github` (`features/github.py`) | 23 | L | `/api/gh/status`, `connect`, `disconnect`, `repos`, `repo`, `tree`, `file`, **`commit`**, `branch`, `commits`, `pulls`, `pull`, **`pulls` (create)**, **`merge`**, **`close`**, `markdown`, **`fork`**, `issue`, **`comments`**, `issues` (list / **create**). Bold rows write to GitHub |
| `jobs` (`features/jobs.py`) | 15 | L | `/api/jobs` (list, `scan` RL, `prefs`, `prefs/reset`, `<jid>`), `/api/posts/parse`, `/api/posts/draft`, `/api/posts/template`, `/api/queue/email`, `/api/queue`, `/api/queue/<qid>` (get, edit, `cancel`, **`now`**) |
| `linkedin` (`features/linkedin.py`) | 7 | L; `PUT /api/li/config` needs P(`admin.integrations`) | `/api/li/status`, `config`, `connect` (OAuth), `callback`, `disconnect`, **`post`** (RL), `schedule` |
| `apps` (`features/apps/`) | 14 | L | `/api/apps` (list, get, add, edit, delete, `sync`, `progress`, `sync-prefs`), `/api/mail-sync`, `/api/naukri` (status, connect, disconnect), `/api/overview` |
| `notify` (`features/notify.py`) | 9 | L; `/sw.js` Pub | `/api/notifications` (list, `read`, `clear`, `prefs`, `subscribe`, `unsubscribe`, `test` RL, `stream` SSE), `/sw.js` |
| `inbox` (`features/inbox.py`) | 5 | L | `/api/inbox`, `/api/inbox/company/<key>` (get, edit), `/api/inbox/sync`, `/api/inbox/progress` |
| `replies` (`features/replies.py`) | 6 | L | `/api/replies` (list, `check` RL, `<rid>`), `/api/replies/<key>/draft`, `/api/replies/details`, **`/api/replies/<key>/send`** (RL) |
| `portfolio` (`features/portfolio.py`) | 11 | L; `/p/*` Pub | `/api/portfolio` (get, `items`, `items/<path>` edit, detect or delete, `order`, `settings`, **`publish`**), `/p/<slug>.json` (CORS `*`), `/p/<slug>`, `/p/<slug>/` |
| `site` (`features/site.py`) | 10 | L; preview, images and visitor contact Pub | `/api/site` (get, save, `render`, **`publish`**, `unpublish`, `reset`, `images`), `/site-preview/<token>`, `/p/i/<iid>`, `/api/site/public/<slug>/contact` (Pub, RL, honeypot) |
| `finder` (`features/finder.py`) | 5 | L | `/api/finder/scan` (RL), `/api/finder`, `stop`, `save`, `sample` |
| `ai_jobs` (`features/ai_jobs/`) | 43 | L | Answers (`/api/ai-jobs/answers`, `approve-all`); evaluation (`evaluate`, `evaluate/<jid>`, `report/<jid>`, `cover/<jid>`); extras (`resume/<jid>`, `archive/<jid>`, `context/<jid>`, `skills`, `stale`, `prep/<jid>`); learning (`feedback/<jid>`, `learning`, `learning/suggestion/<sid>`, `learning/reset`); model (`model`, `model/test`, `model/list`); portals (`portals`); **apply** (`/api/ai-jobs/apply`, needs consent, RL 20 per day), `live`, `finish/<jid>` (own computer only), `kit/<jid>`; runs (`/api/ai-jobs`, `match`, `keys`, `details`); tracker (`status/<jid>`, `rules`, `followups`, `insights`) |
| `extension` (`features/extension.py`) | 8 | L for `/api/extension*`; Ext for `/api/ext/*` | `/api/extension/key` (create, revoke), `/api/extension`, `/api/extension/install` (own computer only), `/api/ext/autofill`, `/api/ext/resume`, `/api/ext/cover`, `/api/ext/learn` |
| `updater` (`features/updater.py`) | 4 | L+P(`admin.update_app`); own computer only | `/api/update` (check, run), `/api/update/undo`, `/api/update/progress` |
| `nearby` (`features/nearby.py`) | 5 | L | `/api/nearby`, `search` (RL), `google-key`, `check` (RL), `track/<cid>` |
| `learning` (`features/learning/`) | 10 | L | `/api/learning/briefing` (get, rebuild RL), `/api/learning`, `syllabus`, `day/<n>`, `day/<n>/complete`, `dsa/complete`, `review/<qid>`, `questions`, `assessment/<week>` |
| `company_research` (`features/research/company.py`) | 3 | L | `/api/research/companies` (list, start RL), `/api/research/companies/<cid>` (delete) |

## Routes that cause external effects (candidates for `external_action` tools)
- **Email:**
  - `POST /api/send`, `POST /api/replies/<key>/send`;
  - queue sends (`/api/queue/email`, `/api/queue/<qid>/now`);
  - `POST /api/profile/test`, `POST /api/notifications/test`.
- **Applications:** `POST /api/ai-jobs/apply`.
- **WhatsApp:** campaigns in WhatsApp mode (local only).
- **LinkedIn:** `POST /api/li/post`, `/api/li/schedule`.
- **GitHub:** the writes marked in bold above, plus `POST /api/portfolio/publish`.
- **Public site:** `POST /api/site/publish`.
