"""Pipeline tracking, after career-ops: one canonical status per evaluated job, the user's own rules (blacklist and
dealbreakers), follow-up cadence for applications, and patterns in what got replies.

Follow-ups are drafts only: Inkwell never sends them. The user copies the text, or opens it in their own mail app,
then marks it sent so the next one is scheduled."""

import re
from datetime import datetime, timedelta

from flask import jsonify
from bridge import login_required

from features.ai_jobs.common import C, bp


STATES = {  # career-ops' canonical states (templates/states.yml): key -> (label, terminal)
    "evaluated": ("Evaluated", False), "applied": ("Applied", False), "responded": ("Responded", False),
    "assessment": ("Assessment", False), "interview": ("Interview", False), "offer": ("Offer", True),
    "rejected": ("Rejected", True), "discarded": ("Discarded", True), "skip": ("Skip", True), "hired": ("Hired", True)}


def set_status(ws, jid, status, only_if_new=False, note=""):
    if status not in STATES:
        raise C().Invalid("Pick a valid status.", "status")
    with ws.lock:
        pipe = ws.load("ai_pipeline", {}) or {}
        cur = pipe.get(jid)
        if only_if_new and cur and cur.get("status") != "evaluated":
            return cur
        if cur and cur.get("status") == status and not note:
            return cur
        now = datetime.now().isoformat(timespec="seconds")
        row = cur or {"history": []}
        if row.get("status") != status:
            row["history"] = (row.get("history", []) + [{"status": status, "at": now, "note": note[:200]}])[-20:]
        row.update(status=status, at=now, note=note[:200] if note else row.get("note", ""))
        pipe[jid] = row
        ws.save("ai_pipeline", pipe)
    return row


@bp.put("/api/ai-jobs/status/<jid>")
@login_required
def put_status(ws, jid):
    core = C()
    p = core.body()
    row = set_status(ws, jid, str(p.get("status", "")), note=core.v_text(p.get("note"), "note", "Note", 200))
    return jsonify(status=row)


# --- my rules -------------------------------------------------------------------------------------------------

# --- follow-up cadence ----------------------------------------------------------------------------------------

RESPONDED = {"in_review", "shortlisted", "assessment"}
WINDOW = {"applied": 45, "responded": 30, "interview": 14}  # older than this (days since the last activity): not worth a nudge


def parse_day(value):
    try:
        return datetime.fromisoformat(str(value)[:19])
    except (TypeError, ValueError):
        return None


def recruiter_email(app):
    for h in reversed(app.get("history", [])):
        m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", h.get("from") or "")
        if m and not re.search(r"no-?reply|donotreply|notifications?@|mailer", m.group(0), re.I):
            return m.group(0)
    return ""


def draft(kind, app, name, count):
    company, role = app.get("company") or "your team", app.get("role") or "the role"
    sign = f"\n\nBest regards,\n{name}" if name else "\n\nBest regards"
    if kind == "thank_you":
        return (f"Thank you for the interview: {role} at {company}",
                f"Hi,\n\nThank you for taking the time to talk with me about the {role} role. I enjoyed learning more about the team, "
                f"and the conversation made me even more interested in the work. If anything else would help, I'm happy to send it.{sign}")
    if kind == "responded":
        return (f"Re: {role} at {company}",
                f"Hi,\n\nThank you for getting back to me about the {role} role. I'm still very interested, and happy to share anything "
                f"else you need or to find a time that works for the next step.{sign}")
    if count == 0:
        return (f"Following up: {role} application",
                f"Hi,\n\nI applied for the {role} role at {company} a little while ago and wanted to follow up. I'm still very interested "
                f"and would love to hear about next steps, or anything else I can share.{sign}")
    return (f"Checking in: {role} application",
            f"Hi,\n\nI wanted to check in once more on my application for the {role} role at {company}. If the position has been "
            f"filled, no problem, and thank you for your time. If it's still open, I'd be glad to talk.{sign}")


FOLLOWUP_DAYS = 7


def due_followups(ws, today=None):
    """career-ops' cadence: applied → follow up after N days (default 7), then again N days later, at most twice, then
    the application goes cold. Responded → reply within a day, then every 3 days. Interview → thank-you the next day."""
    every = FOLLOWUP_DAYS
    today = today or datetime.now()
    sent = ws.load("ai_followups", {}) or {}
    name = ws.profile().get("name", "").strip()
    out = []
    for a in (ws.load("applications", {}) or {}).values():
        if a.get("merged_into") or a.get("hidden"):
            continue
        status = a.get("status") or "applied"
        mine = [d for d in (sent.get(a["id"]) or {}).get("sent", []) if parse_day(d)]
        snooze = parse_day((sent.get(a["id"]) or {}).get("snooze_until"))
        if snooze and snooze > today:
            continue
        last = parse_day(mine[-1]) if mine else None
        applied = parse_day(a.get("applied_at"))
        updated = parse_day(a.get("updated_at")) or applied
        if not applied:
            continue
        kind = due = None
        quiet = (today - max(filter(None, [applied if status == "applied" else updated, last, applied]))).days
        if quiet > WINDOW["responded" if status in RESPONDED else status if status in WINDOW else "applied"]:
            continue
        if status == "applied":
            if len(mine) >= 2:
                if (today - (last or applied)).days >= every:
                    out.append({"id": a["id"], "company": a.get("company"), "role": a.get("role"), "kind": "cold",
                                "days": (today - applied).days, "count": len(mine), "status": status,
                                "note": "No reply after two follow-ups. Move on; mark it closed if you like."})
                continue
            kind, due = "follow_up", (last or applied) + timedelta(days=every)
        elif status in RESPONDED:
            ref = max(filter(None, [updated, last]))
            kind, due = "responded", ref + timedelta(days=1 if not last or (updated and last < updated) else 3)
        elif status == "interview":
            iv = parse_day(a.get("interview_at")) or updated
            if iv and (iv > today or (applied and iv < applied)):  # a future or garbled interview date: thank-you after the last update
                iv = updated if iv < today else None
            if last and iv and last >= iv:
                continue
            kind, due = ("thank_you", iv + timedelta(days=1)) if iv else (None, None)
        if not kind or not due or due > today:
            continue
        subject, body = draft(kind, a, name, len(mine))
        out.append({"id": a["id"], "company": a.get("company"), "role": a.get("role"), "kind": kind, "status": status,
                    "days": (today - applied).days, "count": len(mine), "overdue": (today - due).days,
                    "to": recruiter_email(a), "subject": subject, "body": body})
    out.sort(key=lambda x: (x["kind"] == "cold", -x.get("overdue", 0)))
    return out


@bp.get("/api/ai-jobs/followups")
@login_required
def list_followups(ws):
    return jsonify(followups=due_followups(ws))


@bp.post("/api/ai-jobs/followups/<aid>")
@login_required
def mark_followup(ws, aid):
    """action: sent (records today and schedules the next) or snooze (days)."""
    core = C()
    p = core.body()
    if aid not in (ws.load("applications", {}) or {}):
        raise core.Invalid("That application no longer exists.", status=404)
    with ws.lock:
        rows = ws.load("ai_followups", {}) or {}
        row = rows.get(aid) or {"sent": []}
        if p.get("action") == "snooze":
            days = core.v_int(p.get("days") or 3, "days", "Days", 1, 60)
            row["snooze_until"] = (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")
        else:
            row["sent"] = (row.get("sent", []) + [datetime.now().isoformat(timespec="seconds")])[-10:]
            row.pop("snooze_until", None)
        rows[aid] = row
        ws.save("ai_followups", rows)
    return jsonify(ok=True, followups=due_followups(ws))


# --- patterns -------------------------------------------------------------------------------------------------

def applied_summary(ws, days=7):
    """Applications in the last `days`: how many, how many heard back (any status past applied), interviews, reply rate.
    Used by the weekly digest and the morning briefing."""
    since = datetime.now() - timedelta(days=days)
    rows = [a for a in (ws.load("applications", {}) or {}).values()
            if not a.get("merged_into") and not a.get("hidden") and (parse_day(a.get("applied_at")) or since) >= since]
    heard = sum(1 for a in rows if (a.get("status") or "applied") not in ("applied", "incomplete"))
    return {"total": len(rows), "heard": heard, "rate": round(100 * heard / len(rows)) if rows else 0,
            "interviews": sum(1 for a in rows if a.get("status") in ("interview", "offer") or a.get("contacted"))}


