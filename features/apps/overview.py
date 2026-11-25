"""Dashboard overview across platforms (applications, GitHub, Naukri)."""

import time
from datetime import date, datetime, timedelta
from flask import jsonify, request
from bridge import login_required

from features.apps.common import C, bp
from features.apps.mailbox import recompute, sync_running
from features.apps.naukri import naukri_stats
from features.apps.routes import public
from features.apps.statuses import ACTIVE, STATUSES
from features.apps.sync import next_run, sync_prefs


GH_CACHE = {}  # uid -> (time, summary)


def github_summary(ws):
    g = ws.load("github", {})
    if not g.get("token"):
        return {"connected": False}
    hit = GH_CACHE.get(ws.uid)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    out = {"connected": True, "login": g.get("login"), "avatar": g.get("avatar")}
    try:
        from features import github as feature_github
        repos = feature_github.gh(ws, "GET", "/user/repos?per_page=100&sort=pushed&affiliation=owner")
        out.update(repos=len(repos), private=sum(1 for r in repos if r.get("private")),
                   stars=sum(r.get("stargazers_count", 0) for r in repos),
                   recent=[{"name": r["name"], "owner": r["owner"]["login"], "pushed_at": r.get("pushed_at"),
                            "language": r.get("language")} for r in repos[:5]])
        langs = {}
        for r in repos:
            if r.get("language"):
                langs[r["language"]] = langs.get(r["language"], 0) + 1
        out["languages"] = sorted(langs.items(), key=lambda kv: -kv[1])[:6]
    except Exception as e:
        out["error"] = str(getattr(e, "message", e))[:200]
    GH_CACHE[ws.uid] = (time.time(), out)
    return out


STATUS_GROUPS = {"active": lambda a: a["status"] in ACTIVE and a["status"] != "incomplete",
                 "heard": lambda a: a["status"] != "applied",
                 "interview": lambda a: a["status"] in ("interview", "shortlisted", "assessment"),
                 "offer": lambda a: a["status"] == "offer",
                 "rejected": lambda a: a["status"] in ("rejected", "closed", "withdrawn")}


def dash_filters(core):
    """Period (days back, or from/to dates), 'applied through' and status group from the query string."""
    args = request.args
    today = datetime.now().date()
    try:
        d_from = date.fromisoformat(args["from"]) if args.get("from") else None
        d_to = date.fromisoformat(args["to"]) if args.get("to") else None
    except ValueError:
        raise core.Invalid("Pick valid dates.", "from")
    days = core.v_int(args.get("days", 365), "days", "Period", 0, 3650)
    if d_from or d_to:
        d_to = min(d_to or today, today)
        d_from = d_from or d_to - timedelta(days=364)
        if d_from > d_to:
            raise core.Invalid("The start date is after the end date.", "from")
    elif days:
        d_to, d_from = today, today - timedelta(days=days - 1)
    else:
        d_to, d_from = today, None
    portal = str(args.get("portal") or "")[:60]
    group = args.get("status") if args.get("status") in STATUS_GROUPS else ""
    return d_from, d_to, portal, group


def buckets(d_from, d_to):
    """Chart buckets for a period: days (≤ 45 days), weeks (≤ 200 days) or months."""
    span = (d_to - d_from).days + 1
    if span <= 45:
        keys = [(d_from + timedelta(days=i)).isoformat() for i in range(span)]
        return "day", keys, lambda iso: iso[:10]
    if span <= 200:
        start = d_from - timedelta(days=d_from.weekday())
        keys, d = [], start
        while d <= d_to:
            keys.append(d.isoformat())
            d += timedelta(days=7)
        return "week", keys, lambda iso: (date.fromisoformat(iso[:10]) - timedelta(days=date.fromisoformat(iso[:10]).weekday())).isoformat()
    keys, y, m = [], d_from.year, d_from.month
    while (y, m) <= (d_to.year, d_to.month):
        keys.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return "month", keys, lambda iso: iso[:7]


def app_stats(apps, lo, hi):
    """Counts for applications made in [lo, hi] (ISO date strings; lo may be '')."""
    sel = [a for a in apps if lo <= (a.get("applied_at") or "")[:10] <= hi]
    heard = sum(1 for a in sel if a["status"] != "applied")
    return {"total": len(sel), "responded": heard, "response_rate": round(100 * heard / len(sel)) if sel else 0,
            "interviews": sum(1 for a in sel if any(h["status"] == "interview" for h in a["history"])),
            "offers": sum(1 for a in sel if any(h["status"] == "offer" for h in a["history"])),
            "rejected": sum(1 for a in sel if a["status"] in ("rejected", "closed"))}


@bp.get("/api/overview")
@login_required
def overview(ws):
    core = C()
    today = datetime.now().date()
    d_from, d_to, portal, group = dash_filters(core)
    all_apps = [a for a in ws.load("applications", {}).values() if not a.get("hidden") and not a.get("merged_into")]
    for a in all_apps:
        if "status" not in a:
            recompute(a)
    portal_counts = {}
    for a in all_apps:
        pk = a.get("portal") or "Company site"
        portal_counts[pk] = portal_counts.get(pk, 0) + 1
    if not d_from:  # "all time": start at the first application / send
        first = min([(a.get("applied_at") or today.isoformat())[:10] for a in all_apps] or [today.isoformat()])
        d_from = min(date.fromisoformat(first), today - timedelta(days=29))
    lo, hi = d_from.isoformat(), d_to.isoformat()
    base = [a for a in all_apps if not portal or (a.get("portal") or "Company site") == portal]
    apps = [a for a in base if lo <= (a.get("applied_at") or "")[:10] <= hi and (not group or STATUS_GROUPS[group](a))]
    # previous period of the same length, for "vs previous" deltas
    span = (d_to - d_from).days + 1
    p_hi = (d_from - timedelta(days=1)).isoformat()
    p_lo = (d_from - timedelta(days=span)).isoformat()
    prev = app_stats([a for a in base if not group or STATUS_GROUPS[group](a)], p_lo, p_hi)
    cur = app_stats(apps, lo, hi)
    by_status = {k: 0 for k in STATUSES}
    for a in apps:
        by_status[a["status"]] = by_status.get(a["status"], 0) + 1
    unit, keys, key_of = buckets(d_from, d_to)
    series = {k: {"applied": 0, "rejected": 0, "progress": 0} for k in keys}
    for a in apps:
        k = key_of(a.get("applied_at") or "0000-00-00")
        if k in series:
            series[k]["applied"] += 1
        for h in a["history"]:
            if not (lo <= h["at"][:10] <= hi):
                continue
            hk = key_of(h["at"])
            if hk in series and h["status"] in ("rejected", "closed"):
                series[hk]["rejected"] += 1
            elif hk in series and h["status"] in ("shortlisted", "interview", "assessment", "offer"):
                series[hk]["progress"] += 1
    funnel = [("Applied", len(apps)),
              ("Heard back", sum(1 for a in apps if a["status"] != "applied" or a.get("events", 1) > 1)),
              ("Assessment / shortlisted", sum(1 for a in apps if any(h["status"] in ("assessment", "shortlisted", "interview", "offer") for h in a["history"]))),
              ("Interview", sum(1 for a in apps if any(h["status"] in ("interview", "offer") for h in a["history"]))),
              ("Offer", sum(1 for a in apps if any(h["status"] == "offer" for h in a["history"])))]
    portals = {}
    for a in apps:
        pk = a.get("portal") or "Company site"
        portals[pk] = portals.get(pk, 0) + 1
    now_iso = datetime.now().isoformat(timespec="minutes")
    upcoming = sorted((public(a) for a in base if a.get("interview_at") and a["interview_at"] >= now_iso[:10]),
                      key=lambda a: a["interview_at"])[:6]
    recent = sorted((public(a) for a in apps), key=lambda a: a["updated_at"] or "", reverse=True)[:8]
    # ---- outreach (campaign emails + WhatsApp) in the period, bucketed like the applications chart
    o_series = {k: {"email": 0, "whatsapp": 0, "failed": 0} for k in keys}
    o_tot = {"emailed": 0, "whatsapp": 0, "failed": 0, "opened": 0, "replied": 0}
    o_prev = 0
    since = datetime.combine(d_from - timedelta(days=span), datetime.min.time()).timestamp()
    until = datetime.combine(d_to + timedelta(days=1), datetime.min.time()).timestamp()
    for row in core.M.send_log.find({"uid": ws.uid, "ts": {"$gte": since, "$lt": until}},
                                    {"day": 1, "data": 1, "ts": 1, "opens": 1, "replied_at": 1}).limit(20000):
        e = core.unseal(row["data"], {}) or {}
        day = row.get("day") or datetime.fromtimestamp(row["ts"]).date().isoformat()
        sent = e.get("status") == "sent"
        if day < lo:
            o_prev += sent
            continue
        b = o_series.get(key_of(day))
        if sent:
            ch = "email" if e.get("channel") == "email" else "whatsapp"
            o_tot["emailed" if ch == "email" else "whatsapp"] += 1
            o_tot["opened"] += bool(row.get("opens"))
            o_tot["replied"] += bool(row.get("replied_at"))
            if b:
                b[ch] += 1
        elif e.get("status") in ("failed", "bounced", "invalid", "not_on_whatsapp"):
            o_tot["failed"] += 1
            if b:
                b["failed"] += 1
    recips = ws.load("recipients", [])
    outreach = {"contacts": len(recips), **o_tot, "bounced": o_tot["failed"], "prev_sent": o_prev,
                "by_day": [{"day": k, **v} for k, v in o_series.items()]}
    # ---- jobs, queue, platforms
    from features import jobs as feature_jobs
    jobs = ws.load("jobs", {})
    jp = feature_jobs.prefs(ws)
    job_stats = {"total": len(jobs), "matches": sum(1 for j in jobs.values() if j.get("state") == "new" and feature_jobs.score_job(j, jp)[3]),
                 "saved": sum(1 for j in jobs.values() if j.get("state") == "saved"),
                 "applied": sum(1 for j in jobs.values() if j.get("state") == "applied")}
    queued = core.M.queue.count_documents({"uid": ws.uid, "status": "queued"})
    li = ws.load("linkedin", {})
    nk = ws.load("naukri", {})
    state = ws.load("apps_sync", {})
    return jsonify(
        filters={"from": lo, "to": hi, "portal": portal, "status": group, "unit": unit, "days": span,
                 "portals": sorted(portal_counts.items(), key=lambda kv: -kv[1])},
        apps={**cur, "active": sum(1 for a in apps if a["status"] in ACTIVE and a["status"] != "incomplete"),
              "prev": prev, "this_week": sum(1 for a in base if (a.get("applied_at") or "") >= (today - timedelta(days=7)).isoformat()),
              "by_status": by_status, "months": [{"month": k, **v} for k, v in series.items()], "funnel": funnel,
              "portals": sorted(portals.items(), key=lambda kv: -kv[1])[:8], "upcoming": upcoming, "recent": recent},
        statuses={k: {"label": v[0], "rank": v[1], "terminal": v[2]} for k, v in STATUSES.items()},
        outreach=outreach, jobs=job_stats, queued=queued,
        platforms={"email": bool(ws.profile().get("smtp_password")), "github": github_summary(ws),
                   "linkedin": {"connected": bool(li.get("token")) and li.get("expires_at", 0) > time.time(),
                                "name": li.get("name"), "posts": len(ws.load("li_posts", []))},
                   "naukri": {"connected": bool(nk.get("connected")), **naukri_stats(ws)}},
        sync={"last_ok": state.get("last_ok"), "last_error": state.get("last_error"), "running": sync_running(ws.uid),
              "next_run": next_run(sync_prefs(ws), state.get("last_auto", "")).isoformat(timespec="minutes") if sync_prefs(ws)["auto"] else None})
