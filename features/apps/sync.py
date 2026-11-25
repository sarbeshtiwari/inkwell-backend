"""The daily sync: when it runs, and the one mail sync that feeds applications, replies, inbox and jobs."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import bridge

from features.apps.common import C
from features.apps.mailbox import scan
from infra import locks, operations


DEFAULT_SYNC = {"auto": True, "time": "06:00", "tz": "Asia/Kolkata"}


def sync_prefs(ws):
    return {**DEFAULT_SYNC, **ws.load("apps_sync_prefs", {})}


def next_run(p, last_auto=""):
    try:
        tz = ZoneInfo(p["tz"])
    except Exception:
        tz = ZoneInfo("UTC")
    now = datetime.now(tz)
    hh, mm = (int(x) for x in p["time"].split(":"))
    run = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now >= run and last_auto == now.date().isoformat():
        run += timedelta(days=1)
    elif now >= run:
        return now  # due (catch-up)
    return run


def daily_tick():
    """Every minute: run each account's morning sync once per local day, catching up if the app was off at 6."""
    core = C()
    for row in core.M.users.find({}, {"_id": 1}):
        ws = core.Workspace(row["_id"])
        p = sync_prefs(ws)
        if not p["auto"] or not ws.profile().get("smtp_password"):
            continue
        state = ws.load("apps_sync", {})
        try:
            tz = ZoneInfo(p["tz"])
        except Exception:
            tz = ZoneInfo("UTC")
        now = datetime.now(tz)
        hh, mm = (int(x) for x in p["time"].split(":"))
        if (now.hour, now.minute) < (hh, mm) or state.get("last_auto") == now.date().isoformat():
            continue
        state["last_auto"] = now.date().isoformat()
        ws.save("apps_sync", state)
        start_sync(ws.uid, reason="daily")


STEPS = [("Checking replies", "replies"), ("Updating applications", "apps"), ("Sorting your inbox by company", "inbox"),
         ("Reading job alerts", "jobs"), ("Checking bounced emails", "bounces")]


def mail_sync_progress(uid):
    """(running, {"stage", "step", "steps", "started", "full"}) from the shared record."""
    v = operations.current(uid, "mail_sync")
    if not v or v["state"] != operations.RUNNING:
        return False, None
    return True, {**{k: v.get(k) for k in ("stage", "step", "steps", "full")}, "started": v["started"]}


def start_sync(uid, full=False, reason="manual"):
    """Start a combined mail sync in the background. False if one is already running for this account (anywhere)."""
    try:
        op = operations.start(uid, "mail_sync", public={"stage": STEPS[0][0], "step": 0, "steps": len(STEPS), "full": full},
                              busy="A mail sync is already running.")
    except locks.LockTimeout:
        return False
    operations.spawn(op, lambda op: sync_all(uid, full, reason, op), name=f"mail-sync-{uid[:6]}")
    return True


def sync_all(uid, full=False, reason="manual", op=None):
    """One mail sync for everything that reads the mailbox: replies, applications, the inbox by company,
    job alerts and bounces. Used by the daily schedule and by the single "Sync now" in mail settings.
    Cancelling stops it between steps; a step that's running finishes first."""
    if op is None:
        try:
            op = operations.start(uid, "mail_sync", public={"stage": STEPS[0][0], "step": 0, "steps": len(STEPS), "full": full},
                                  busy="A mail sync is already running.")
        except locks.LockTimeout:
            return
        with op:
            return sync_all(uid, full, reason, op)
    core = C()
    ws = core.Workspace(uid)
    steps = STEPS
    parts, errors = [], []
    for i, (label, key) in enumerate(steps):
        op.checkpoint()
        op.update(public={"stage": label, "step": i})
        try:
            if key == "replies":
                from features import replies as feature_replies
                n = feature_replies.scan(ws, first_days=60 if full else 21).get("found", 0)
                if n:
                    parts.append(f"{n} new repl{'y' if n == 1 else 'ies'}")
            elif key == "apps":
                r = scan(ws, full=full or not ws.load("apps_sync", {}).get("full_done"), notify=reason != "first")
                if r["changed"] or r["added"]:
                    parts.append(f"{r['changed']} status change{'s' if r['changed'] != 1 else ''}, {r['added']} new application{'s' if r['added'] != 1 else ''}")
            elif key == "inbox":
                from features import inbox as feature_inbox
                n = feature_inbox.scan(ws, full=full)["added"]
                if n:
                    parts.append(f"{n} new email{'s' if n != 1 else ''} sorted")
            elif key == "jobs":
                from features import jobs as feature_jobs
                j = feature_jobs.scan_alerts(ws, days=30 if full else 3)
                if j["added"]:
                    parts.append(f"{j['added']} new job{'s' if j['added'] != 1 else ''} from alerts")
            elif key == "bounces":
                core.check_bounces(ws, days=14 if full else 4)
        except Exception as e:
            msg = str(getattr(e, "message", e)).splitlines()[0][:160]
            if "already running" not in msg:
                errors.append(f"{label.split()[-1]}: {msg}")
    state = ws.load("mail_sync", {})
    state.update(last_ok=datetime.now().isoformat(timespec="seconds"), last_error="; ".join(errors)[:400],
                 summary="; ".join(parts) or "Everything was already up to date", last_reason=reason)
    ws.save("mail_sync", state)
    notify = getattr(bridge, "notify", None)
    if notify and reason != "silent":
        title = {"daily": "Morning mail sync done"}.get(reason, "Mail sync done")
        body = ("; ".join(parts) or "Everything was already up to date") + "." + (f" Some parts failed: {'; '.join(errors)}" if errors else "")
        notify(uid, title, body[:1].upper() + body[1:], "#profile", "sync")


def morning_sync(uid):
    sync_all(uid, reason="daily")


def start_workers():
    bridge.every(60, daily_tick, "daily-sync")
