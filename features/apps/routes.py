"""Applications API: list, edit, add, delete, sync now and sync settings."""

import re
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import jsonify
from bridge import login_required

from features.apps.common import C, bp
from features.apps.mailbox import PROGRESS, app_id, recompute, scan
from features.apps.statuses import STATUSES
from features.apps.sync import MAIL_SYNC, next_run, sync_all, sync_prefs


def public(a):
    out = {k: a.get(k, "") for k in ("id", "company", "role", "job_id", "portal", "status", "prev_status", "applied_at",
                                     "updated_at", "interview_at", "events", "contacted", "hidden", "notes", "url",
                                     "location", "manual_status")}
    return out


def run_in_background(ws, full):
    def go():
        try:
            scan(ws, full=full)
        except Exception as e:
            print(f"[Reachout] apps sync: {e}", flush=True)
    threading.Thread(target=go, daemon=True, name=f"apps-sync-{ws.uid[:6]}").start()


@bp.get("/api/apps")
@login_required
def list_apps(ws):
    apps = ws.load("applications", {})
    for a in apps.values():
        if "status" not in a:
            recompute(a)
    state, p = ws.load("apps_sync", {}), sync_prefs(ws)
    nxt = next_run(p, state.get("last_auto", ""))
    return jsonify(apps=[public(a) for a in apps.values() if not a.get("merged_into")],
                   statuses={k: {"label": v[0], "rank": v[1], "terminal": v[2]} for k, v in STATUSES.items()},
                   sync={**{k: state.get(k) for k in ("last_run", "last_ok", "last_error", "full_done", "emails", "added", "updated")},
                         "running": ws.uid in PROGRESS, "progress": PROGRESS.get(ws.uid), "prefs": p,
                         "next_run": nxt.isoformat(timespec="minutes") if p["auto"] else None},
                   email_ready=bool(ws.profile().get("smtp_password")))


@bp.get("/api/apps/<aid>")
@login_required
def get_app(ws, aid):
    a = ws.load("applications", {}).get(aid)
    if not a:
        raise C().Invalid("That application no longer exists.", status=404)
    recompute(a)
    return jsonify(app=public(a), history=[{k: h.get(k, "") for k in ("status", "at", "subject", "from", "snippet", "mid",
                                                                        "interview_at", "note", "source")} for h in a["history"]])


@bp.post("/api/apps/sync")
@login_required
def sync_now(ws):
    if not ws.profile().get("smtp_password"):
        raise C().Invalid("Set up email on the Profile page first, so Reachout can read your inbox.")
    if ws.uid in PROGRESS:
        return jsonify(ok=True, running=True)
    full = bool(C().body().get("full")) or not ws.load("apps_sync", {}).get("full_done")
    run_in_background(ws, full)
    time.sleep(0.3)
    return jsonify(ok=True, running=True, full=full)


@bp.get("/api/apps/progress")
@login_required
def progress(ws):
    return jsonify(running=ws.uid in PROGRESS, progress=PROGRESS.get(ws.uid),
                   last_error=ws.load("apps_sync", {}).get("last_error", ""))


@bp.put("/api/apps/sync-prefs")
@login_required
def save_sync_prefs(ws):
    core, p = C(), C().body()
    cur = sync_prefs(ws)
    if "auto" in p:
        cur["auto"] = bool(p["auto"])
    if "time" in p:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(p["time"])):
            raise core.Invalid("Pick a time like 06:00.", "time")
        cur["time"] = p["time"]
    if "tz" in p:
        try:
            ZoneInfo(str(p["tz"]))
            cur["tz"] = str(p["tz"])
        except Exception:
            raise core.Invalid("Unknown time zone.", "tz")
    ws.save("apps_sync_prefs", cur)
    return jsonify(prefs=cur, next_run=next_run(cur, ws.load("apps_sync", {}).get("last_auto", "")).isoformat(timespec="minutes"))


@bp.put("/api/apps/<aid>")
@login_required
def edit_app(ws, aid):
    core, p = C(), C().body()
    with ws.lock:
        apps = ws.load("applications", {})
        a = apps.get(aid)
        if not a:
            raise core.Invalid("That application no longer exists.", status=404)
        for k, n in (("company", 80), ("role", 120), ("job_id", 40), ("notes", 2000), ("url", 500), ("location", 80)):
            if k in p:
                v = core.v_text(p.get(k), k, k.replace("_", " ").title(), n, required=k == "company")
                if k == "url" and v and not re.match(r"https?://", v):
                    raise core.Invalid("Links must start with http:// or https://", "url")
                a[k] = v
        if "hidden" in p:
            a["hidden"] = bool(p["hidden"])
        if "status" in p:
            st = p["status"]
            if st not in STATUSES:
                raise core.Invalid("Unknown status.", "status")
            a["history"].append({"status": st, "at": datetime.now().isoformat(timespec="seconds"), "source": "you",
                                 "subject": "Updated by you", "snippet": core.v_text(p.get("note"), "note", "Note", 300) or ""})
            a.pop("manual_status", None)
        recompute(a)
        ws.save("applications", apps)
    return jsonify(app=public(a))


@bp.delete("/api/apps/<aid>")
@login_required
def delete_app(ws, aid):
    with ws.lock:
        apps = ws.load("applications", {})
        apps.pop(aid, None)
        ws.save("applications", apps)
    return jsonify(ok=True)


@bp.post("/api/apps")
@login_required
def add_app(ws):
    core, p = C(), C().body()
    company = core.v_text(p.get("company"), "company", "Company", 80, required=True)
    role = core.v_text(p.get("role"), "role", "Position", 120)
    st = p.get("status") or "applied"
    if st not in STATUSES:
        raise core.Invalid("Unknown status.", "status")
    when = str(p.get("applied_at") or "")
    try:
        at = datetime.fromisoformat(when) if when else datetime.now()
    except ValueError:
        raise core.Invalid("Pick a valid date.", "applied_at")
    if at > datetime.now() + timedelta(days=1):
        raise core.Invalid("The application date can't be in the future.", "applied_at")
    with ws.lock:
        apps = ws.load("applications", {})
        aid = app_id(company, role, str(time.time()))
        hist = [{"status": "applied", "at": at.isoformat(timespec="seconds"), "source": "you", "subject": "Added by you"}]
        if st != "applied":
            hist.append({"status": st, "at": datetime.now().isoformat(timespec="seconds"), "source": "you", "subject": "Updated by you"})
        apps[aid] = {"id": aid, "company": company, "role": role, "job_id": core.v_text(p.get("job_id"), "job_id", "Job ID", 40),
                     "portal": core.v_text(p.get("portal"), "portal", "Applied through", 40) or "Manual", "history": hist,
                     "url": str(p.get("url") or "")[:500] if re.match(r"https?://", str(p.get("url") or "")) else "",
                     "created": datetime.now().isoformat(timespec="seconds")}
        recompute(apps[aid])
        ws.save("applications", apps)
    return jsonify(app=public(apps[aid]))


@bp.get("/api/mail-sync")
@login_required
def mail_sync_status(ws):
    state, p = ws.load("mail_sync", {}), sync_prefs(ws)
    apps_state = ws.load("apps_sync", {})
    last = state.get("last_ok") or apps_state.get("last_ok")
    return jsonify(running=ws.uid in MAIL_SYNC or ws.uid in PROGRESS, progress=MAIL_SYNC.get(ws.uid), last_ok=last,
                   last_error=state.get("last_error", ""), summary=state.get("summary", ""), prefs=p,
                   next_run=next_run(p, apps_state.get("last_auto", "")).isoformat(timespec="minutes")
                   if p["auto"] and ws.profile().get("smtp_password") else None,
                   email_ready=bool(ws.profile().get("smtp_password")), first_done=bool(apps_state.get("full_done")))


@bp.post("/api/mail-sync")
@login_required
def mail_sync_now(ws):
    if not ws.profile().get("smtp_password"):
        raise C().Invalid("Add your Gmail app password below first.")
    if ws.uid in MAIL_SYNC:
        return jsonify(ok=True, running=True)
    full = bool(C().body().get("full"))
    threading.Thread(target=sync_all, args=(ws.uid, full), daemon=True, name=f"mail-sync-{ws.uid[:6]}").start()
    time.sleep(0.3)
    return jsonify(ok=True, running=True)
