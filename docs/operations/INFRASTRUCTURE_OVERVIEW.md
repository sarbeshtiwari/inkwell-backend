# Infrastructure overview

**Status:** CURRENT IMPLEMENTATION, from configuration files. Facts about the live systems are marked **UNKNOWN**
where they haven't been verified. Stage 0.1 is the read-only inventory that would verify them; it is **not
executed**. · **Last reviewed:** 2026-11-26

## Production topology

| Component | Configuration (in git) | Verified facts | Unknown |
|---|---|---|---|
| Render web service `reachout-api` | `backend/render.yaml`: `runtime: docker`, `plan: starter`, `healthCheckPath: /healthz`, `autoDeploy: true`. Env: `APP_ENV=production`, `COOKIE_SECURE=1`, `TRUST_PROXY=1`, `STREAM_SECONDS=20`, `MONGODB_DB=reachout`, `WHATSAPP_ENABLED=0`; the rest are `sync: false` | Runs `8b59986` (deployed and smoke-tested earlier) | **Actual plan** (the deployment docs say Render Free, the blueprint says `starter`); the branch it tracks; whether it is Blueprint-managed; `RENDER_GIT_COMMIT`; the region |
| Docker image | `backend/Dockerfile`: `mcr.microsoft.com/playwright/python:v1.63.0-noble`; runs as `pwuser`; `DATA_DIR=/data`; `CMD gunicorn app:app --workers 1 --threads 16 --timeout 120` (shell form) | — | — |
| MongoDB Atlas | — | MongoDB 8.0, region `AP_SOUTH_1`, replica set (earlier checks); `list_search_indexes` accepted (`phase0-checklist.md`) | **Tier** (M0 / Flex / M10+); other database users; App Services triggers; backups and snapshots; Vector Search index limits |
| Netlify: app | `frontend/netlify.toml`: build `npm ci && npm run build:netlify`, publish `netlify-dist`; signed rewrites to Render | — | Current site names, deploy settings |
| Netlify: landing | `web/netlify.toml`: signed `/api/*` rewrite | — | same |
| Cloudinary | `CLOUDINARY_URL` (`sync: false`) | — | Plan, retention |
| Email (system) | `SMTP_*`, `MAIL_FROM` (`sync: false`) | — | **Whether outbound SMTP works on the current Render plan.** Render Free blocks outbound ports 25, 465 and 587 (fact F2 in [DEPLOYMENT_GATE_ARCHITECTURE.md](../deployment/DEPLOYMENT_GATE_ARCHITECTURE.md)); if the service is on Free, login-code and user email sending would fail |
| Sentry | `SENTRY_DSN` (optional) | — | Whether it is set |

## Development topology
- **Backend:** runs on the developer Mac (`start.command`) against a local replica set `rs0`, unless `.env` points
  elsewhere. **`.env` currently points at production** (risk SEC-02).
- **Frontend:** Vite dev server on `:5173`, proxying to `:5050`.
- **Tests:** the local replica set, with a random database name ([../engineering/TESTING_STRATEGY.md](../engineering/TESTING_STRATEGY.md)).

## Code versions

| Repository | Local | Origin | Production |
|---|---|---|---|
| `inkwell-backend` | `main` at `a83d17e`, **7 commits ahead** of `origin/main` (`45e2fec`, `04cf85d`, `72a1681`, `1d544b7`, `5c21025`, `5ba9022`, `a83d17e`); untracked `docs/` additions (`docs/deployment/` and this documentation set) | `8b59986` | `8b59986` |
| `inkwell-frontend` | `65fd7bf`, clean | `65fd7bf` | UNKNOWN (assumed the latest Netlify build) |
| `inkwell-web` | `238ca36`, clean | `238ca36` | UNKNOWN (assumed the latest Netlify build) |

**The 7 unpushed commits and all the untracked documentation exist only on the developer Mac** (risk OPS-01).

## Known performance constraints
- **Threads:** one process with 16 threads, shared by requests and background work. Long jobs (Playwright, IMAP)
  occupy threads.
- **Coordination latency:** guarded writes from Render measured about 0.6–0.8 s of database round trips in Stage 1
  (`a83d17e` only, not production).
- **Proxy timeout:** Netlify's proxy timeout (about 26 s, from `infra/locks.py` comments) bounds interactive
  requests. Lock waits are capped at 18 s for that reason.
- **Memory:** 512 MB on `starter` (from the `render.yaml` comment). WhatsApp automation needs the `standard` plan.
