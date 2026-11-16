"""Self-update for the copy of Reachout running on your own computer (after career-ops' update-system.mjs).

Reachout is three git checkouts side by side: backend/, frontend/ and web/. Check fetches each one's GitHub
branch and lists what's new. Update fast-forwards only (never merges or overwrites): it refuses when a checkout
has local edits or commits of its own, so nothing you changed is lost. Your data is never touched: it lives in
MongoDB and in data/ and .env, which git ignores. Before updating, the current version of each checkout is
recorded in data/update-history.json, so Undo can put it back. After updating it installs new Python packages,
rebuilds the app pages when the frontend changed, and restarts itself.

Off on a hosted server (APP_ENV=production): hosted copies update by deploying from GitHub."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify
from bridge import login_required

import bridge

bp = Blueprint("updater", __name__)

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
REPOS = {"backend": BACKEND, "frontend": ROOT / "frontend", "web": ROOT / "web"}
HISTORY = ROOT / "data" / "update-history.json"
STATE = {"state": "idle", "log": [], "at": 0}
LOCK = threading.Lock()


def C():
    return bridge.C


def git(repo, *args, timeout=60):
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else f"git {args[0]} failed")
    return r.stdout.strip()


def repos():
    return {k: p for k, p in REPOS.items() if (p / ".git").exists()}


def enabled():
    return not C().PRODUCTION and bool(repos())


def repo_status(name, path, fetch=True):
    """Where one checkout stands against its GitHub branch."""
    out = {"name": name, "ok": True}
    try:
        if fetch:
            git(path, "fetch", "--quiet", "origin", timeout=90)
        out["branch"] = git(path, "rev-parse", "--abbrev-ref", "HEAD")
        out["version"] = git(path, "rev-parse", "--short", "HEAD")
        upstream = git(path, "rev-parse", "--abbrev-ref", "@{u}")
        behind, ahead = git(path, "rev-list", "--left-right", "--count", "@{u}...HEAD").split()
        out.update(upstream=upstream, behind=int(behind), ahead=int(ahead))
        out["changed_files"] = [l[3:] for l in git(path, "status", "--porcelain", "--untracked-files=no").splitlines()][:50]
        out["new"] = [{"sha": l[:7], "subject": l[8:]} for l in git(path, "log", "--format=%h %s", "HEAD..@{u}").splitlines()][:30]
    except Exception as e:
        out.update(ok=False, error=str(e)[:200])
    return out


def blockers(statuses):
    msgs = []
    for s in statuses:
        if not s["ok"]:
            msgs.append(f"{s['name']}: {s['error']}")
        elif s["changed_files"]:
            msgs.append(f"{s['name']} has {len(s['changed_files'])} changed file{'s' if len(s['changed_files']) != 1 else ''} "
                        "that aren't committed. Commit or put them aside first, so nothing of yours is overwritten.")
        elif s["ahead"] and s["behind"]:
            msgs.append(f"{s['name']} has {s['ahead']} commit{'s' if s['ahead'] != 1 else ''} of its own that GitHub doesn't have. "
                        "Push or merge them first.")
    return msgs


def say(line):
    STATE["log"] = (STATE["log"] + [line])[-40:]
    STATE["at"] = time.time()
    print(f"[Reachout] update: {line}", flush=True)


def rebuild(changed):
    """Install what the new version needs: Python packages, and the app pages when the frontend changed."""
    if "backend" in changed:
        say("Installing Python packages…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(BACKEND / "requirements.txt")], check=True, timeout=900)
    if "frontend" in changed and (REPOS["frontend"] / "package.json").exists():
        say("Installing app page packages…")
        subprocess.run(["npm", "install", "--no-audit", "--no-fund", "--silent"], cwd=REPOS["frontend"], check=True, timeout=900)
        say("Building the app pages…")
        subprocess.run(["npx", "vite", "build"], cwd=REPOS["frontend"], check=True, timeout=900, capture_output=True)


def restart():
    """Replace this process with a fresh one running the new code (same command line)."""
    say("Restarting Reachout…")
    time.sleep(1.5)
    os.execv(sys.executable, [sys.executable, *sys.argv])


def load_history():
    try:
        return json.loads(HISTORY.read_text())
    except (OSError, ValueError):
        return []


def run_update(target=None):
    """target=None: update to GitHub's latest. target={repo: sha}: undo to that recorded version."""
    changed = []
    try:
        if target is None:
            statuses = [repo_status(n, p) for n, p in repos().items()]
            stop = blockers(statuses)
            if stop:
                STATE.update(state="error", error=" ".join(stop))
                return
            todo = [s for s in statuses if s["behind"]]
            if not todo:
                STATE.update(state="done", error="", message="Already up to date.")
                return
            HISTORY.parent.mkdir(parents=True, exist_ok=True)
            hist = load_history()
            hist.append({"at": time.time(), "versions": {n: git(p, "rev-parse", "HEAD") for n, p in repos().items()}})
            HISTORY.write_text(json.dumps(hist[-20:], indent=1))
            for s in todo:
                say(f"Updating {s['name']} ({s['behind']} change{'s' if s['behind'] != 1 else ''})…")
                git(REPOS[s["name"]], "merge", "--ff-only", "@{u}")
                changed.append(s["name"])
        else:
            for name, sha in target.items():
                path = repos().get(name)
                if not path or git(path, "rev-parse", "HEAD") == sha:
                    continue
                say(f"Putting {name} back to {sha[:7]}…")
                git(path, "reset", "--keep", sha)  # --keep refuses rather than lose uncommitted edits
                changed.append(name)
        if not changed:
            STATE.update(state="done", error="", message="Nothing to change.")
            return
        rebuild(changed)
        STATE.update(state="restarting", error="", message="Updated. Reachout is restarting; this page reloads in a moment.")
        threading.Thread(target=restart, daemon=True).start()
    except Exception as e:
        STATE.update(state="error", error=f"The update stopped: {str(e)[:200]}. Your data is untouched.")
    finally:
        if STATE["state"] == "running":
            STATE["state"] = "done"


def guard(core, ws):
    if not enabled():
        raise core.Invalid("Updates from here work when Reachout runs from its git folders on your own computer.", status=400)
    if not core.is_admin(ws.uid):
        raise core.Invalid("Only the owner of this Reachout can update it.", status=403)


@bp.get("/api/update")
@login_required
def check(ws):
    core = C()
    guard(core, ws)
    if core.rate_limited(("update-check", ws.uid), 60, 3600):
        raise core.Invalid("Checked too often. Try again in a while.", status=429)
    statuses = [repo_status(n, p) for n, p in repos().items()]
    hist = load_history()
    return jsonify(repos=statuses, blockers=blockers(statuses), behind=sum(s.get("behind", 0) for s in statuses),
                   can_undo=bool(hist), last=hist[-1] if hist else None, job={k: STATE.get(k) for k in ("state", "log", "error", "message")})


@bp.post("/api/update")
@login_required
def update(ws):
    core = C()
    guard(core, ws)
    with LOCK:
        if STATE["state"] in ("running", "restarting"):
            raise core.Invalid("An update is already running.", status=409)
        STATE.update(state="running", log=[], error="", message="")
    threading.Thread(target=run_update, daemon=True, name="self-update").start()
    return jsonify(ok=True)


@bp.post("/api/update/undo")
@login_required
def undo(ws):
    core = C()
    guard(core, ws)
    hist = load_history()
    if not hist:
        raise core.Invalid("There's no earlier version to go back to.")
    with LOCK:
        if STATE["state"] in ("running", "restarting"):
            raise core.Invalid("An update is already running.", status=409)
        STATE.update(state="running", log=[], error="", message="")
    last = hist[-1]
    HISTORY.write_text(json.dumps(hist[:-1], indent=1))
    threading.Thread(target=run_update, args=(last["versions"],), daemon=True, name="self-update").start()
    return jsonify(ok=True)


@bp.get("/api/update/progress")
@login_required
def progress(ws):
    return jsonify(job={k: STATE.get(k) for k in ("state", "log", "error", "message")}, enabled=enabled() and C().is_admin(ws.uid))
