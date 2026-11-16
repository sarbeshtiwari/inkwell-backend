"""Pipeline tracking, after career-ops: one canonical status per evaluated job, the user's own rules (blacklist and
dealbreakers), follow-up cadence for applications, and patterns in what got replies.

Follow-ups are drafts only: Reachout never sends them. The user copies the text, or opens it in their own mail app,
then marks it sent so the next one is scheduled."""

import re
import time
from datetime import datetime, timedelta
from statistics import median

from flask import jsonify, request
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
    learn(ws, jid, status)
    return row


def learn(ws, jid, status):
    """What you did with a job is a lesson too (applied, interview, offer…); see learning.py."""
    from features.ai_jobs import learning
    if status not in learning.WEIGHTS:
        return
    try:
        job = next((r for r in (ws.load("ai_match", {}) or {}).get("results", []) if r["id"] == jid), None) \
            or (ws.load("ai_reports", {}) or {}).get(jid)
        if job and job.get("title"):
            learning.record(ws, {**job, "id": jid}, status, jd=(ws.load("ai_jds", {}) or {}).get(jid, ""))
    except Exception as e:
        print(f"[Reachout] learning skipped: {e!r}", flush=True)


@bp.put("/api/ai-jobs/status/<jid>")
@login_required
def put_status(ws, jid):
    core = C()
    p = core.body()
    row = set_status(ws, jid, str(p.get("status", "")), note=core.v_text(p.get("note"), "note", "Note", 200))
    return jsonify(status=row)


# --- my rules -------------------------------------------------------------------------------------------------

def words(value, n=30, size=80):
    items = value if isinstance(value, list) else re.split(r"[\n,]", str(value or ""))
    return [str(x).strip()[:size] for x in items if str(x).strip()][:n]


@bp.get("/api/ai-jobs/rules")
@login_required
def get_rules_api(ws):
    from features.ai_jobs.evaluate import get_rules
    return jsonify(rules=get_rules(ws))


@bp.put("/api/ai-jobs/rules")
@login_required
def put_rules(ws):
    core = C()
    p = core.body()
    try:
        floor = float(str(p.get("min_salary") or 0).replace(",", ""))
        score = float(p.get("min_score_to_apply") or 0)
    except ValueError:
        raise core.Invalid("Enter numbers for pay and score.", "min_salary")
    rules = {"blacklist": words(p.get("blacklist"), 100), "targets": words(p.get("targets"), 12), "avoid_words": words(p.get("avoid_words"), 30, 40),
             "min_salary": max(0.0, min(floor, 1e10)), "currency": str(p.get("currency") or "").upper()[:3],
             "remote_only": bool(p.get("remote_only")), "no_contract": bool(p.get("no_contract")),
             "need_sponsorship": bool(p.get("need_sponsorship")),
             "max_years": core.v_int(p.get("max_years") or 0, "max_years", "Years", 0, 40),
             "min_score_to_apply": max(0.0, min(score, 5.0)), "followups": bool(p.get("followups", True)),
             "followup_days": core.v_int(p.get("followup_days") or 7, "followup_days", "Follow-up days", 3, 30)}
    ws.save("ai_rules", rules)
    return jsonify(rules=rules)


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


def due_followups(ws, today=None):
    """career-ops' cadence: applied → follow up after N days (default 7), then again N days later, at most twice, then
    the application goes cold. Responded → reply within a day, then every 3 days. Interview → thank-you the next day."""
    from features.ai_jobs.evaluate import get_rules
    rules = get_rules(ws)
    if not rules.get("followups", True):
        return []
    every = rules.get("followup_days") or 7
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

def band(score):
    return "4.5+" if score >= 4.5 else "4.0–4.4" if score >= 4.0 else "3.5–3.9" if score >= 3.5 else "below 3.5"


def rate(rows):
    heard = sum(1 for r in rows if r["heard"])
    return {"total": len(rows), "heard": heard, "rate": round(100 * heard / len(rows)) if rows else 0,
            "interviews": sum(1 for r in rows if r["interview"])}


def insights(ws, days=180):
    """What got replies: by score band, archetype, remote mode and where you applied; plus why jobs were skipped."""
    since = datetime.now() - timedelta(days=days)
    reports = ws.load("ai_reports", {}) or {}
    by_url = {r.get("url"): r for r in reports.values() if r.get("url")}
    rows = []
    for a in (ws.load("applications", {}) or {}).values():
        if a.get("merged_into") or a.get("hidden") or (parse_day(a.get("applied_at")) or since) < since:
            continue
        st = a.get("status") or "applied"
        hist = a.get("history", [])
        first = next((h for h in hist if h.get("status") not in ("applied", "incomplete")), None)
        lag = None
        if first and parse_day(a.get("applied_at")) and parse_day(first.get("at")):
            lag = max(0, (parse_day(first["at"]) - parse_day(a["applied_at"])).days)
        rep = by_url.get(a.get("url"))
        rows.append({"heard": st not in ("applied", "incomplete"), "interview": st in ("interview", "offer") or a.get("contacted"),
                     "status": st, "portal": a.get("portal") or "Other", "lag": lag,
                     "band": band(rep["score5"]) if rep else "",
                     "archetype": rep["summary"]["archetype"] if rep else "", "remote": rep["summary"]["remote"] if rep else ""})

    def group(key):
        out = {}
        for r in rows:
            if r[key]:
                out.setdefault(r[key], []).append(r)
        return sorted(({"name": k, **rate(v)} for k, v in out.items()), key=lambda x: -x["total"])[:8]

    total = rate(rows)
    funnel = {k: sum(1 for r in rows if r["status"] == k) for k in ("applied", "in_review", "shortlisted", "assessment", "interview",
                                                                    "offer", "rejected", "closed", "withdrawn")}
    lags = [r["lag"] for r in rows if r["lag"] is not None]
    pipe = ws.load("ai_pipeline", {}) or {}
    skipped = {}
    for p in pipe.values():
        if p.get("status") == "skip" and p.get("note"):
            skipped[p["note"]] = skipped.get(p["note"], 0) + 1
    tips = []
    bands = {b["name"]: b for b in group("band")}
    hi, lo = bands.get("4.5+") or bands.get("4.0–4.4"), bands.get("below 3.5") or bands.get("3.5–3.9")
    if hi and lo and hi["total"] >= 3 and lo["total"] >= 3 and hi["rate"] > lo["rate"]:
        tips.append(f"Jobs scored {hi['name']} got replies {hi['rate']}% of the time, vs {lo['rate']}% for {lo['name']}: "
                    "apply to fewer, better-fitting jobs.")
    if total["total"] >= 10 and total["rate"] < 10:
        tips.append("Under 10% of applications got a reply. Evaluate before applying, tailor the summary and cover letter, "
                    "and follow up after a week.")
    portals = group("portal")
    if len(portals) >= 2 and portals[0]["total"] >= 3:
        best = max(portals, key=lambda x: (x["rate"], x["total"]))
        if best["rate"] > total["rate"]:
            tips.append(f"{best['name']} works best for you so far ({best['rate']}% replies).")
    if not rows:
        tips.append("No applications yet in this period. Once you apply, this shows what gets replies.")
    return {"days": days, "total": total, "funnel": funnel, "by_band": group("band"), "by_archetype": group("archetype"),
            "by_remote": group("remote"), "by_portal": portals, "median_days_to_reply": median(lags) if lags else None,
            "pipeline": {k: sum(1 for p in pipe.values() if p.get("status") == k) for k in STATES},
            "skipped_because": sorted(({"rule": k, "count": v} for k, v in skipped.items()), key=lambda x: -x["count"])[:6],
            "tips": tips, "evaluated": len(reports)}


@bp.get("/api/ai-jobs/insights")
@login_required
def get_insights(ws):
    days = C().v_int(request.args.get("days", 180), "days", "Period", 7, 730)
    return jsonify(insights=insights(ws, days), at=time.time())
