"""The daily sync: when it runs, and the one mail sync that feeds applications, replies, inbox and jobs."""

import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import bridge

from features.apps.common import C
from features.apps.mailbox import scan


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
        threading.Thread(target=morning_sync, args=(ws.uid,), daemon=True, name=f"morning-sync-{ws.uid[:6]}").start()


MAIL_SYNC = {}  # uid -> {"stage", "step", "steps", "started", "full"} while a mail sync runs


def sync_all(uid, full=False, reason="manual"):
    """One mail sync for everything that reads the mailbox: replies, applications, the inbox by company,
    job alerts and bounces. Used by the daily schedule and by the single "Sync now" in mail settings."""
    core = C()
    ws = core.Workspace(uid)
    if uid in MAIL_SYNC:
        return
    steps = [("Checking replies", "replies"), ("Updating applications", "apps"), ("Sorting your inbox by company", "inbox"),
             ("Reading job alerts", "jobs"), ("Checking bounced emails", "bounces")]
    MAIL_SYNC[uid] = {"stage": steps[0][0], "step": 0, "steps": len(steps), "started": time.time(), "full": full}
    parts, errors = [], []
    try:
        for i, (label, key) in enumerate(steps):
            MAIL_SYNC[uid].update(stage=label, step=i)
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
    finally:
        MAIL_SYNC.pop(uid, None)
    notify = getattr(bridge, "notify", None)
    if notify and reason != "silent":
        title = {"daily": "Morning mail sync done"}.get(reason, "Mail sync done")
        body = ("; ".join(parts) or "Everything was already up to date") + "." + (f" Some parts failed: {'; '.join(errors)}" if errors else "")
        notify(uid, title, body[:1].upper() + body[1:], "#profile", "sync")


def morning_sync(uid):
    sync_all(uid, reason="daily")


def start_workers():
    bridge.every(60, daily_tick, "daily-sync")
