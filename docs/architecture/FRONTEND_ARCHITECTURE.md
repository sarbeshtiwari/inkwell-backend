# Frontend architecture

**Status:** CURRENT IMPLEMENTATION · `inkwell-frontend` at `65fd7bf`, `inkwell-web` at `238ca36` (both clean and in
sync with origin) · **Last reviewed:** 2026-11-26

## Web app (`inkwell-frontend`, folder `frontend/`)

### Stack
- React 18.3.1 and Vite 5.4.11 (`@vitejs/plugin-react` 4.3.4).
- Libraries: `framer-motion` 11.11.17, `chart.js` 4.4.4 with `react-chartjs-2` 5.2.0, `leaflet` 1.9.4 (Nearby map),
  and `@monaco-editor/react` 4.6.0 (GitHub file editor).
- No router library, no state library, no TypeScript, no linter, **no test runner**.

### Builds (`vite.config.js`)

| Target | Command | Base | Output | Served by |
|---|---|---|---|---|
| Flask (local) | `vite build` | `/dist/` | `../web/dist` | Flask at `/app`, `/login`, `/signup` |
| Netlify | `npm run build:netlify` (`BUILD_TARGET=netlify`, then `scripts/netlify-postbuild.mjs`) | `/` | `netlify-dist/` | Netlify |

- **Dev server:** port 5173. It proxies `/api`, `/p/`, `/site-preview` and `/o/` to `http://127.0.0.1:5050`.
- **The postbuild script writes:**
  - `index.html`, `login.html`, `signup.html`, `site.webmanifest`;
  - `_headers`: security headers, plus a CSP that allows hashed inline scripts, `cdn.jsdelivr.net` and Google Fonts;
  - `_redirects`: `/` → `/app` (302), and `/app/*` → `/index.html` (200).

### Entry and routing
- **`src/main.jsx`:**
  - Paths matching `/^\/(login|signup)\/?$/` render `pages/Auth.jsx`; everything else renders
    `<AppProvider><App/></AppProvider>`.
  - Global error handlers report to `POST /api/client-errors`, at most 5 per page.
- **`src/lib/router.js`:** a custom router.
  - URLs look like `/app/<page>/<param>?query`; `go()` uses `pushState`.
  - A global click handler takes over `<a href="/app/...">` links.
- **`src/App.jsx`:** a lazy route table. An unknown page falls back to `dashboard`.

| Path | Page | Purpose |
|---|---|---|
| `/app`, `/app/dashboard` | `Dashboard.jsx` | KPIs and charts |
| `/app/campaign` | `Campaign.jsx` | Audience, message, preview, send |
| `/app/contacts`, `/app/contact/:id` | `Contacts.jsx`, `Contact.jsx` | Contacts grid and detail |
| `/app/templates` | `Templates.jsx` | Message templates |
| `/app/files` | `Files.jsx` | Attachments |
| `/app/replies` | `Replies.jsx` | Replies and suggested answers |
| `/app/activity` | `Activity.jsx` | Send log |
| `/app/inbox` | `Inbox.jsx` | Inbox by company |
| `/app/applications(/:id)` | `Applications.jsx` | Applications found in mail |
| `/app/jobs` | `Jobs.jsx` | Matches from alert emails |
| `/app/posts` | `Posts.jsx` | Hiring posts and drafted replies |
| `/app/naukri`, `/app/linkedin`, `/app/github`, `/app/whatsapp` | connected-service pages | |
| `/app/portfolio` | `Portfolio.jsx` (lazy `Website.jsx`) | Website and portfolio builder |
| `/app/profile`, `/app/settings` | | Account |
| `/app/leads` | `Leads.jsx` | Enquiries. Admins get a site-wide scope toggle (`d?.is_admin` from `/api/leads`) |
| `/app/finder` | `Finder.jsx` | Email finder |
| `/app/nearby` | `Nearby.jsx` | Companies near me (Leaflet) |
| `/app/ai-jobs` | `AIJobs.jsx` and `pages/ai-jobs/*` drawers | AI job match, apply, answers, model, keys, rules, insights |
| `/app/learning` | `Learning.jsx` | Course, DSA, review, briefing |
| `/login`, `/signup` | `Auth.jsx` | Email + password (selected in `main.jsx`) |

There is **no admin, Super Admin, Observatory or agent page.** The only role-aware UI is the Leads scope toggle.

### Session and API (`src/lib/api.js`, `src/lib/store.jsx`)
- **Session:** an HttpOnly cookie set by the backend. No token is kept in JavaScript.
- **`api(url, opts)`:**
  - uses relative URLs and always sends `X-Requested-With: fetch`;
  - turns errors into `ApiError(message, field, status)`, so a message can be shown next to its form field;
  - sends any 401 outside `/api/auth*` to `/login`.
- **`upload()`:** uses XHR so it can report progress.
- **`AppProvider`:**
  - loads `/api/me`, `/api/state` and `/api/whatsapp` on mount;
  - polls `/api/job` every 1.5 s (4 s after a failure).
- **`lib/bus.js`:** a small store and event bus, with the events `history-changed`, `replies-changed`, `apps-changed`
  and `sync-done`.

### Other
- **Styling:** global CSS (`src/styles/`), themes via `html[data-theme]`.
- **`public/sw.js`:** a service worker for push notifications only.
- **`frontend/extension/`:** the MV3 "Inkwell Autofill" Chrome extension. A zipped copy is in `public/downloads/`.
- **Environment variables (names only):** `VITE_LANDING_URL`, `BUILD_TARGET`, `NODE_VERSION`. `NETLIFY_PROXY_SECRET`
  is set in the Netlify UI and referenced in `netlify.toml`.
- **CI** (`.github/workflows/ci.yml`): `npm ci`, `npm run build:netlify`, then a check that the output files exist.

### Netlify proxy (`frontend/netlify.toml`)
- Rewrites `/api/*`, `/p/*`, `/site-preview/*`, `/o/*`, `/robots.txt` and `/sitemap.xml` to the Render backend
  (`status = 200`, `force`, `signed = "NETLIFY_PROXY_SECRET"`).
- Netlify adds an `X-Nf-Sign` JWT, which the backend verifies (`core/security.py:netlify_signed`).
- Because the browser only talks to the Netlify origin, the session cookie stays first-party.

### Known limitations
- No automated tests.
- `pages/Auth.jsx` uses email + password. Its comment says email codes will return later, while `web/README` still
  describes one-time codes. The backend supports both (`/api/auth/request-code`, `/api/auth/verify-code`,
  `/api/auth/login`).

## Public site (`inkwell-web`, folder `web/`)
- Static HTML, CSS and vanilla JS: `landing.html`, `contact.html`, `privacy.html`, `terms.html`, `404.html`.
- `netlify-build.mjs` writes `_site/`. It fills in `{{SITE_URL}}`, `{{OPERATOR}}` and `{{JURISDICTION}}`, and writes
  the manifest, robots, sitemap, `_redirects` (`/login`, `/signup` and `/app*` → `APP_URL`) and `_headers` (CSP).
- The contact form posts to `/api/leads` through a signed rewrite of `/api/*` (`web/netlify.toml`).
- `web/dist/` holds the frontend's Flask build output, used when the backend serves the app locally.
- **CI:** a build, plus a check that no `{{` placeholders are left. No tests.

### Privacy promise
- The privacy page is the user-facing statement that [ADR-0009](../decisions/ADR-0009-metadata-only-super-admin.md)
  protects. The product owner's condition is that it is updated before anything changes that promise.
- The exact current wording is in `web/privacy.html`. It is not quoted here, so this document doesn't go stale.
