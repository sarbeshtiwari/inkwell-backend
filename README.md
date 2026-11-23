# Reachout API (backend)

**Live app:** [reachout-web.netlify.app](https://reachout-web.netlify.app) · **Frontend repo:** [reachout-frontend](https://github.com/sarbeshtiwari/reachout-frontend)

Reachout helps people run their outreach and job search from one place:

- **Outreach**: import contacts, write one message with `{name}`/`{company}` placeholders, and send everyone a
  personal copy by email (and optionally WhatsApp), with attachments, from their **own** accounts.
- **AI job match** (Reachout AI): reads the user's resume, searches company career sites (Greenhouse, Lever,
  Ashby; no key) and optionally Tavily/Brave web search (each user's own key), ranks every opening with
  Reachout's own matching engine (LangChain pipeline; an optional local open-source model via Ollama adds a
  short verdict) and, with the user's consent, auto-applies on public Greenhouse/Lever/Ashby forms with
  Playwright. No logins, no made-up answers, stops at CAPTCHAs, at most 20 applications a day.
  Local model setup on a Mac: `brew install ollama && brew services start ollama && ollama pull qwen3:4b`.
  - **Watch mode** (on your own computer): auto-apply opens a visible Chrome window, one tab per job, and pauses with a bar at the bottom of the page (Continue / Skip) whenever you're needed: signing in or creating an account, questions only you can answer (outlined in red), a CAPTCHA. **Fill & finish** does the same for one job. A live view on the page shows each step.
  - **My answers** (supervised learning): answers to application questions, typed by you or learned from forms you filled in. Learned and drafted answers wait for your approval; only approved ones are ever filled. Company-specific answers (e.g. "Why Notion?") only go to that company; `{company}` is replaced in general ones. Rewordings of a question match, different questions don't.
  - **Cover letter**: attached when a form has a cover letter upload, or pasted where it asks for the text; never sent otherwise. Choose it in the auto-apply settings (automatic = the file with "cover" in its name, a specific file, or none).
  - **Sites that reject automated submissions** (e.g. Ashby's "flagged as possible spam"): Reachout stops, never retries or hides itself, stops automating that job board, and offers **Apply in my browser**: the form link plus your details and answers, ready to copy (or use the extension).
  - **Evaluation, after [career-ops](https://github.com/santifer/career-ops)** (Evaluate on a job, or Evaluate top 10): a report scored out of 5 from five dimensions (resume match, fit with the roles you target, pay, culture signals, red flags), each marked supported / partial / unknown, plus how sure it is. Blocks: A role summary (kind of role, level, remote, pay), B requirement-by-requirement match quoting your real resume lines, with gaps and how to handle them, C level and strategy, D pay, E tailoring (summary for this job, what to move up, ATS keyword coverage), F interview plan (STAR outlines from your resume, likely questions), G is the posting real and open (age, still listed, specificity, reposts, pay shown, fee scams, contractor wording, six-point role clarity), H drafted answers (to My answers, for review) and a one-page cover letter you edit and can attach to that job. Below 4.0/5 it recommends against applying. The local model writes the prose parts when it runs; every block also has a plain version without it. Lines in a posting aimed at AI tools are shown to you and ignored.
  - **Liveness gate:** a posting is checked before it's evaluated or applied to; closed ones are marked Closed and never applied to.
  - **More sources:** Workable, SmartRecruiters and Recruitee company boards, and the **Remote job boards** source (Remotive, Remote OK, Himalayas, Arbeitnow, Jobicy); the same job from several sources is shown once.
  - **My rules:** roles you target, companies/domains to never apply to, lowest pay, remote only, no contract roles, visa sponsorship, most years asked, words to avoid, and a minimum score for auto-apply. A job that breaks a rule is marked Skip; auto-apply asks before applying anyway. A missing fact never breaks a rule.
  - **Statuses** per job (Evaluated, Applied, Responded, Assessment, Interview, Offer, Rejected, Discarded, Skip, Hired), **Follow-ups** (after 7 days, twice at most, replies within a day, thank-you after an interview; drafts only, you send them) and **Insights** (reply rate by score, where you applied, kind of role and work setup; why jobs were skipped).
  - **AI model** (career-ops runs on whichever AI tool you have; same idea here): local Ollama by default, or your own key for Claude, OpenAI, Gemini, OpenRouter, Groq, Mistral or DeepSeek, or any OpenAI-compatible server on your computer (LM Studio, llama.cpp). Keys are stored encrypted. With a cloud provider, the resume and job text you evaluate go to that provider.
  - **Job boards** (`features/ai_jobs/boards/`, one small module per board, after career-ops' providers): 108 public, no-login readers, each tested live. 50 company job systems (Workday, SuccessFactors, Oracle Recruiting Cloud, Taleo, iCIMS, Eightfold, Cornerstone, PeopleSoft, Jobvite, BambooHR, Deel, Teamtailor, Personio, school-district systems, WordPress job sites, public Telegram job channels…) and 58 search boards (Hacker News, Built In, We Work Remotely, Remotive, the a16z talent network, JustJoin, NoFluffJobs, Bulldogjob, Prace.cz, Arbeitsagentur, Platsbanken, The Hub, Job Bank Canada, JobStreet, Kalibrr, ITviec, CareerViet, Yourator, Magneto, Manfred, Remotli, Joinup, HigherEdJobs, Amazon, Google, Apple, IBM, Atlassian…). Boards for other countries run only when your location is there, and boards that serve several countries search yours. **My companies**: paste a company's careers link (or type its name) and its board is recognised, checked live and read on every search.
- **Any company's own website** (`sitejobs.py`): when a careers page isn't on a known job system, Reachout looks around it like a person would: the job board it embeds or links (a Greenhouse frame, a Workday link, the board its page loads in the background), its "Open roles" pages, the JobPosting data careers pages publish for Google, or its own job feed. Pages drawn by scripts are opened in a headless browser; it stops at bot checks instead of getting round them.
- **Applying on company websites** (`siteapply.py`): auto-apply now also works on a company's own site. From the job page it finds the form behind the Apply button (or in a frame), fills it, and hands back anything only you can do. Sites that want an account first (Workday, iCIMS, Taleo…) are done in Watch mode, where you sign in yourself. It remembers per site which button worked and which sites always need you. LinkedIn, Naukri, Indeed and similar boards are never auto-applied to.
- **Learns from your feedback** (`learning.py`): Good fit / Not for me (with a reason) on each match, the jobs you apply to, and what happens next (interview, offer, rejected) teach a small preference model of what you like: kinds of role, title words, skills, companies, remote, level. New matches move by up to 15 points and say why ("You liked jobs like this: React, frontend roles"); the AI's second opinion is told the same. Reasons you repeat become suggested rules that wait for your yes. **What it learned** shows your tastes, the suggestions, and whether higher scores have actually led to interviews (rates only once a score band has 5+ outcomes). Older lessons count less; you can make it forget.
  - **More from career-ops:** tailored resume per job (PDF in Files, facts checked against your resume; lines that add anything are dropped; choose to send it for that job), interview and offer-negotiation prep, save the posting as PDF, closing dates, your earlier applications to the company, similar jobs you already evaluated, a warning when your resume changed after an evaluation, skills worth learning (Insights), closing applications silent for 60+ days, and a Monday digest notification.
  - **Updates** (on your own computer, owner only): checks the backend, frontend and web checkouts against GitHub, fast-forwards only, refuses when you have local edits or commits, installs packages, rebuilds the pages and restarts. Undo goes back to the previous version (kept in `data/update-history.json`). Your data isn't touched.
- **Getting the web search keys (optional; career sites need none).** Each user adds their own in AI job match → Search keys:
  - **Tavily** (free 1,000 searches/month, no card): sign up at https://app.tavily.com → the dashboard shows a key starting `tvly-` → copy it into Reachout.
  - **Brave Search** ($5 free credit/month, about 1,000 searches; a credit card is required): sign up at
    https://api-dashboard.search.brave.com → Subscriptions → choose the Search plan → API Keys → Add API key → copy it into Reachout.
    Set a spending limit if offered; usage beyond the credit is billed.
- **Reachout Autofill (Chrome extension).** Fills job applications in the user's own browser with their details, resume,
  location, cover letter and approved answers; the user checks the form and presses Submit (it never submits, and is not a bot).
  Source in `frontend/extension/`, downloadable from AI job match → Browser extension (`/downloads/reachout-autofill.zip`).
  It signs in with a personal extension key (create/revoke in the app), which only opens `/api/ext/*`: read application
  details, download the resume, match answers to a form's questions, and save typed answers for review.
  After changing the extension: `cd frontend/extension && zip -qr ../public/downloads/reachout-autofill.zip . -x ".*"`.
- **Email finder**: give company websites (typed in or as a CSV/Excel list) and Reachout reads each site's
  pages for the HR and careers addresses it publishes (hr@, careers@, jobs@…), ready to save as contacts.
- **Replies**: reads the user's mailbox read-only, matches replies to what they sent, works out what the person
  wants (interested, asks for résumé, wants a call…) and suggests an answer they can send from the app.
- **Career**: turns application emails (applied → review → assessment → interview → offer) into one timeline per
  job, and scores job-alert emails against the user's profile.
- **Website**: a one-page website builder with themes, SEO and a contact form; messages land in **Leads**.
- **Integrations**: GitHub (repos, files, PRs), LinkedIn (official sign-in and sharing), Naukri.

This repository is the **Python API** behind it. The app UI and the public landing pages are separate repositories:

| Repository | What | Hosted on |
|---|---|---|
| **reachout-backend** (this one) | Flask API, background jobs, public user websites (`/p/<name>`) | Render (Docker) |
| reachout-frontend | React app: `/app`, `/login`, `/signup` | Netlify |
| reachout-web | Landing, contact, privacy and terms pages | Netlify |

```
Browser ──► Netlify (app site) ──signed proxy──► Render (this API) ──► MongoDB Atlas
        └─► Netlify (landing)  ──signed proxy──┘        │
                                                         ├─► user's mailbox (IMAP read-only / SMTP)
                                                         └─► GitHub, LinkedIn, web push, Sentry
```

The browser only ever talks to Netlify. Netlify forwards `/api/*` (and `/p/*`) here and signs each request
with `NETLIFY_PROXY_SECRET`; this server refuses anything unsigned. That keeps the login cookie first-party and
makes the API unreachable directly.

---

## Architecture at a glance

- **Flask 3 API** (app.py) wires the shared building blocks in core/ (config, MongoDB access, Fernet encryption, mail sending, storage, security, validation) to the HTTP routes in routes/ and the feature blueprints in features/, and starts the background workers.
- **AI job matching** (features/ai_jobs/): 100+ job-board and ATS connectors in boards/; resume-based evaluation and ranking built on LangChain (langchain-core), with an optional local open-source model through Ollama (langchain-ollama); consent-based auto-apply on public application forms with Playwright.
- **Outreach and inbox**: personal email and optional WhatsApp sends from the user's own accounts, reply detection and bounce handling.
- **Data protection**: MongoDB with sensitive fields encrypted at rest (Fernet); uploaded files are stored in Cloudinary as still-encrypted objects.
- **Delivery**: Docker image and render.yaml for Render, CI in GitHub Actions; the React + Vite frontend is deployed separately on Netlify.

## Tech

Python 3.12 · Flask 3 · MongoDB (pymongo) · Fernet encryption (cryptography) · gunicorn · Playwright (Chromium,
for WhatsApp Web and job auto-apply) · openpyxl / pypdf (imports) · pywebpush (browser notifications) · Sentry (optional) · LangChain (langchain-core, langchain-ollama) for AI job matching.

## Project layout

```
app.py              Entry point: wires core/ and routes/ together, registers feature blueprints, starts workers
core/               Building blocks shared by everything
  config.py         Environment settings, limits and fixed texts
  crypto.py         Encryption key, seal/unseal, keyed lookup hashes
  http.py           The Flask app object, session cookie settings, Sentry
  db.py             MongoDB connection (M)
  storage.py        File storage: Cloudinary (encrypted) with GridFS fallback
  accounts.py       Users, login sessions, account deletion, startup checks
  validation.py     Input validation and the Invalid error
  workspace.py      Per-user encrypted workspace (contacts, templates, settings, documents)
  security.py       Rate limits, Netlify signed proxy, CSRF, security headers/CSP, login_required, error handlers
  mail.py           System emails, the user's SMTP/IMAP connections
  tables.py         CSV/Excel import, Excel export, {field} templates
  whatsapp_store.py WhatsApp login storage, 2-hour expiry, browser linking
  bounces.py        Domain checks and bounce detection
  sentmail.py       Importing past sends from the Sent folder
  sending.py        Campaign send jobs
  pages.py          HTML page serving helpers (cookies, source tracking)
routes/             HTTP API and pages, by area: auth, contacts, workspace_api, send, whatsapp, leads, site_pages
migrations.py       One-off SQLite → MongoDB migration
bridge.py           Shared plumbing feature modules use (bridge.C = the core helpers, HTTP helper, timers, deadlines)
whatsapp.py         WhatsApp Web automation (Playwright); also a standalone CLI
features/
  apps/             Applications tracker + the one mail sync (statuses, noise, extract, mailbox, sync, routes, naukri, overview)
  replies.py        Reply detection, intent reading, suggested answers, sending replies in-thread
  inbox.py          Inbox insights: emails grouped by company and category
  jobs.py           Job alerts → scored job matches; scheduled email queue; hiring posts
  github.py         GitHub workspace (repos, files, commits, PRs)
  linkedin.py       LinkedIn sign-in and posting (official API only)
  notify.py         Notifications: live stream (SSE), browser push, Mac alerts
  portfolio.py      Projects for the website (from GitHub) + Git publishing
  site.py           Website builder: themes, sections, renderer, SEO, public pages, contact form
  finder.py         Email finder: HR/careers addresses from company websites → contacts
  ai_jobs/          AI job match (sources, providers, ranking, runs, answers, forms, apply, watch, routes,
                    signals, evaluate, tracker, models, portals, extras, sitejobs, siteapply, learning; boards/ = job board readers)
  updater.py        self-update from GitHub (local copies only)
  extension.py      API for the Reachout Autofill Chrome extension (personal extension key)
tests/              pytest suite (security regressions + core flows)
Dockerfile          The image Render runs
render.yaml         Render blueprint
```

Each feature module exposes a Flask blueprint `bp`, registered at the bottom of `app.py`. Modules reach shared
helpers through `bridge.C` (the `app` module, which looks names up live in `core/`) rather than importing them directly.

## How data is stored

- **MongoDB.** Most per-user data lives in the `kv` collection as one document per user and key
  (`<uid>:recipients`, `<uid>:applications`, `<uid>:website`…), read and written with `Workspace.load()` /
  `Workspace.save()`. Event-like data has its own collections: `send_log`, `mail_index`, `replies`, `leads`,
  `notifications`, `queue`, `sessions`, `otps`, `ratelimits`, `site_*`. Files (documents, the WhatsApp login) go to Cloudinary as private, still-encrypted objects and
  MongoDB keeps only a reference; website images are served from Cloudinary's CDN. Without `CLOUDINARY_URL`
  they stay in GridFS. Move existing GridFS files with `python app.py move-files-to-cloudinary`.
- **Everything is encrypted** with Fernet before it's stored (`seal()` / `unseal()`). Emails are looked up by a
  keyed HMAC (`lookup_hash()`), never stored in plain text. The key comes from `SECRET_KEY`, or `data/secret.key`
  if that's not set. **Losing the key makes all data unreadable; leaking it with a database dump exposes it.**
  Keep it in a password manager, never next to a backup.
- **Concurrency.** `Workspace.save()` replaces the whole document, so read-modify-write must hold `ws.lock`.
  Locks, in-memory caches and background jobs live in one process: run **one worker** (threads are fine).

## Security model

- Sign-in by one-time email code (no passwords). Codes are HMAC-hashed, single-use, attempt-limited, with a
  daily lock after repeated wrong codes. Sessions: random token in an HttpOnly cookie, hash stored server-side,
  7 days idle / 30 days max, "log out everywhere" supported.
- CSRF: every state-changing `/api/` call needs `X-Requested-With: fetch` and a JSON body.
- Rate limits are stored in MongoDB (shared, survive restarts, expire automatically).
- Strict security headers and a CSP on every page; published user sites get their own nonce-based CSP.
- Outbound requests are restricted: custom mail servers must be public hosts on mail ports, push endpoints must
  be real push services, links from emails/data are checked before use.
- Every user's data is scoped by `uid`; account deletion removes everything and stops running jobs.

## Run locally

Needs Python 3.12 and MongoDB on `127.0.0.1:27017` (`brew install mongodb-community`, or Docker:
`docker run -d -p 127.0.0.1:27017:27017 mongo:8`).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -c requirements.lock.txt
.venv/bin/playwright install chromium      # only if you want WhatsApp
.venv/bin/python app.py                     # → http://127.0.0.1:5050
```

Without `SMTP_HOST`, sign-in codes are printed in the terminal instead of emailed. The API runs on its own;
for the UI, run the frontend repo's `npm run dev` (it proxies `/api` to `:5050`).

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q tests
```

Uses a throwaway database with a random name (dropped afterwards); never touches `reachout`. Page tests skip
when the `web/` pages aren't next to this folder. GitHub Actions runs lint, tests (against MongoDB) and the
Docker build on every push.

## Configuration

Copy `.env.example` to `.env` for local use; on Render set these in the dashboard.

| Variable | Default | What it does |
|---|---|---|
| `SECRET_KEY` | `data/secret.key` | Encryption + session key. **Must stay the same forever** for existing data. |
| `MONGODB_URI` / `MONGODB_DB` | local / `reachout` | Database connection and name |
| `CLOUDINARY_URL` | empty | `cloudinary://<key>:<secret>@<cloud>` from Cloudinary → API Keys; file storage (empty = GridFS) |
| `WA_SESSION_HOURS` | `2` | A saved WhatsApp login is deleted this many hours after linking; the user gets a notification |
| `OLLAMA_URL` / `OLLAMA_MODEL` | local `http://127.0.0.1:11434` (empty in production) / `qwen3:4b` | Optional local open-source model that adds a verdict to the top AI job matches |
| `SITE_URL` | request host | Public address of the **app** site (links, sitemap, share cards) |
| `LANDING_URL` | — | Public address of the landing site (split hosting) |
| `NETLIFY_PROXY_SECRET` | — | When set, only Netlify-signed requests are accepted (except `/healthz`) |
| `APP_ENV` | — | `production` turns on secure cookies, HSTS and startup checks |
| `COOKIE_SECURE` | `1` in production | HTTPS-only cookies |
| `TRUST_PROXY` | — | Trust `X-Forwarded-*` from the load balancer (Render: `1`) |
| `ADMIN_EMAILS` | — | Comma-separated; can see enquiries from the main Reachout site |
| `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASSWORD` `MAIL_FROM` | — | Server email for sign-in codes |
| `ALLOW_SIGNUP` | `1` | `0` closes new sign-ups |
| `DAILY_LIMIT` | `100` | Messages per user per day (campaigns + replies + queue) |
| `MAX_CONTACTS` / `MAX_FILES_MB` | `5000` / `25` | Per-user limits |
| `WHATSAPP_ENABLED` | `1` | `0` removes WhatsApp sending everywhere |
| `MIN_WA_DELAY` / `MAX_BROWSERS` | `20` / `3` | WhatsApp pacing and concurrent browsers |
| `SENTRY_DSN` | — | Error reports (no cookies, bodies or form data are sent) |
| `STREAM_SECONDS` / `STREAM_SLOTS` | `50` / `4` | Live notification connection length and cap (Netlify: `20`) |
| `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` | — | LinkedIn app (or set by an admin in the app) |
| `VAPID_EMAIL` | — | Contact address for browser push |
| `OPERATOR_NAME` / `JURISDICTION` | — | Shown on the privacy and terms pages |
| `DATA_DIR` | `./data` | Where `secret.key` lives if `SECRET_KEY` isn't set |
| `PORT` / `HOST` | `5050` / `127.0.0.1` | Local server address |

## Deploy to Render

1. **Make a shared secret** (also used on both Netlify sites):
   `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`
2. Render → **New → Blueprint** → pick this repository. It reads `render.yaml` (Docker, health check on
   `/healthz`, plan `starter`).
3. Fill in the values it asks for:
   - `SECRET_KEY`: **your existing key** if you're moving existing data (a new key can't read it)
   - `SITE_URL` = `https://<app>.netlify.app`, `LANDING_URL` = `https://<landing>.netlify.app`
   - `NETLIFY_PROXY_SECRET` (step 1), `MONGODB_URI`, `ADMIN_EMAILS`, `SMTP_*`, optional `SENTRY_DSN`
4. MongoDB Atlas → **Network Access**: allow Render's outbound IPs (or `0.0.0.0/0` with a strong password),
   and turn on **backups**.
5. Deploy, then check `https://<service>.onrender.com/healthz` returns `ok`. Any other URL opened directly
   returns "Not found": that's the signed-proxy protection working.
6. Put the Render address into both Netlify repos' `netlify.toml` and redeploy them.

Notes:
- Keep **one gunicorn worker** (see `Dockerfile`). Background jobs need an instance that doesn't sleep.
- WhatsApp is off in `render.yaml`. Turning it on needs the `standard` plan (Chrome needs memory) and carries
  the risk that WhatsApp restricts the user's number.
- Point an uptime monitor at `/healthz`; it also checks the database.

## Adding a feature

1. Create `features/<name>.py` with `bp = Blueprint("<name>", __name__)` and routes under `/api/<name>/…`.
2. Protect routes with `@login_required` (from `bridge`), which passes a `Workspace`; read/write user data with
   `ws.load()` / `ws.save()` inside `with ws.lock:`, and validate input with `C().v_text()`, `v_email()`, `v_int()`.
3. Raise `C().Invalid("message", "field")` for user errors; the frontend shows it next to the field.
4. Register the blueprint at the bottom of `app.py`, add any new collection to `USER_COLLECTIONS` (in `core/accounts.py`) so account
   deletion removes it, and add tests in `tests/`.
