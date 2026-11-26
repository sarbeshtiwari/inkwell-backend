# Development setup

**Status:** CURRENT IMPLEMENTATION · **Last reviewed:** 2026-11-26

## Folders
The project root is a plain folder, not a git repository. It contains:

| Folder or file | What it is |
|---|---|
| `backend/` | The `inkwell-backend` repository |
| `frontend/` | The `inkwell-frontend` repository |
| `web/` | The `inkwell-web` repository |
| `data/` | Local data: the encryption key, the local MongoDB files, backups. **Never commit** |
| `.venv/` | Python virtual environment |
| `.env` | Local settings. **Never commit**. See the warning below |
| `start.command` | Local start script (outside git) |
| `inkwell-agentic-architecture.md`, `phase0-checklist.md` | Planning documents (outside git) |

## Prerequisites
- Python with `.venv` (`pip install -r backend/requirements.txt -c backend/requirements.lock.txt`, plus
  `backend/requirements-dev.txt`).
- MongoDB at `~/.local/mongodb/current/bin/mongod`. That is the path `start.command` expects.
- Node, for the frontend: `npm ci` in `frontend/`.
- Playwright Chromium, for the apply and WhatsApp features.
- Optional:
  - Ollama (`brew install ollama && ollama pull qwen3:4b`);
  - `tectonic`, for LaTeX resumes;
  - the Claude Code CLI, used only when the app runs locally.

## Local database: a replica set is required
Protected writes use transactions, so MongoDB must run as a replica set.

`start.command` does both steps:
1. starts `mongod --replSet rs0 --dbpath data/mongodb --bind_ip 127.0.0.1 --port 27017`;
2. runs `backend/tools/local_replset.py`, which initiates `rs0` once and waits until it accepts writes.

It skips starting MongoDB when `MONGODB_URI` is set in the shell or something is already listening on port 27017.

## ⚠️ `.env` and production
- `core/config.py` loads `.env` automatically (real environment variables win).
- **The local `.env` currently holds the production `MONGODB_URI`.** So running the app locally connects to
  **production** with the production database user.
  - The deployment documents count that as a concurrent production instance.
  - It must be stopped before any deploy
    ([../deployment/DEPLOYMENT_COMPATIBILITY_MATRIX.md](../deployment/DEPLOYMENT_COMPATIBILITY_MATRIX.md#local-developer-processes)).
- For development, point `.env` at the local replica set. This change is task SEC-02, and it is not made by this
  documentation.

## Running
- **Backend:** `.venv/bin/python backend/app.py` serves `http://127.0.0.1:5050` and opens a browser unless
  `OPEN_BROWSER=0`.
  - Without SMTP settings, login codes are printed to the terminal.
  - `start.command` wraps this.
- **Frontend dev server:** `cd frontend && npm run dev` serves `http://localhost:5173`, proxying `/api` to `:5050`.
- **Flask-served frontend:** `cd frontend && npm run build` writes `web/dist/`, served at `/app`.

## Running the tests
```bash
# MongoDB must be running as replica set rs0 on 127.0.0.1:27017 (start.command, or mongod --replSet rs0 + tools/local_replset.py)
cd backend
../.venv/bin/python -m pytest -q tests
```
The suite never reads `.env` and never uses `MONGODB_URI`
([TESTING_STRATEGY.md](TESTING_STRATEGY.md#how-the-suite-is-isolated-backendtestsconftestpy)).

## Benchmarks
`backend/tools/bench_locks.py` measures lock and guarded-write latency. Run it only against a database you are
allowed to use. Running it against production isn't approved.

## Git rules for this project
- Commit as `Sarbesh Kumar Tiwari <89567003+sarbeshtiwari@users.noreply.github.com>`, with no co-author line.
- Don't push large changes without asking.
- **Render auto-deploys** (`render.yaml`: `autoDeploy: true`). So pushing the backend's tracked branch can deploy.
  Which branch Render tracks is **UNKNOWN** (Stage 0.1).
- Local `main` holds 7 unpushed commits (`45e2fec`…`a83d17e`) that must not be deployed without the fenced procedure
  ([../operations/DEPLOYMENT_STRATEGY.md](../operations/DEPLOYMENT_STRATEGY.md)).
