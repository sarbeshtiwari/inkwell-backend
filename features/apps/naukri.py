"""Naukri: email-based connection and stats."""

import re
from datetime import datetime
from flask import jsonify
from bridge import login_required

from features.apps.common import C, bp
from features.apps.mailbox import PROGRESS, recompute
from features.apps.routes import public, run_in_background


NAUKRI_LINKS = {
    "profile": "https://www.naukri.com/mnjuser/profile", "applies": "https://www.naukri.com/myapply/historypage",
    "recommended": "https://www.naukri.com/mnjuser/recommendedjobs", "inbox": "https://www.naukri.com/mnjuser/inbox",
    "search": "https://www.naukri.com/software-developer-jobs?jobAge=1",
}


def naukri_stats(ws):
    apps = [a for a in ws.load("applications", {}).values() if a.get("portal") == "Naukri" and not a.get("hidden")]
    for a in apps:
        if "status" not in a:
            recompute(a)
    jobs = [j for j in ws.load("jobs", {}).values() if j.get("source") == "Naukri"]
    return {"applications": len(apps), "recruiter_activity": sum(1 for a in apps if a["status"] != "applied"),
            "job_alerts": len(jobs), "recent": sorted((public(a) for a in apps), key=lambda a: a["updated_at"], reverse=True)[:8]}


@bp.get("/api/naukri")
@login_required
def naukri_status(ws):
    n = ws.load("naukri", {})
    return jsonify(connected=bool(n.get("connected")), profile_url=n.get("profile_url", ""), checked_at=n.get("checked_at"),
                   emails_found=n.get("emails_found", 0), links=NAUKRI_LINKS, stats=naukri_stats(ws),
                   email_ready=bool(ws.profile().get("smtp_password")))


@bp.post("/api/naukri/connect")
@login_required
def naukri_connect(ws):
    core, p = C(), C().body()
    url = str(p.get("profile_url") or "").strip()
    if url and not re.match(r"https://(?:www\.)?naukri\.com/", url):
        raise core.Invalid("Paste a link that starts with https://www.naukri.com/", "profile_url")
    profile = ws.profile()
    imap = core.imap_connect(profile)
    try:
        imap.select("INBOX", readonly=True)
        typ, data = imap.search(None, "FROM", '"naukri.com"')
        found = len(data[0].split()) if typ == "OK" and data and data[0] else 0
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    if not found:
        raise core.Invalid("No Naukri emails in this mailbox yet. Use the email address your Naukri account is registered "
                           "with on the Profile page, and turn on Naukri's job-alert and application emails.")
    ws.save("naukri", {"connected": True, "profile_url": url, "emails_found": found,
                       "checked_at": datetime.now().isoformat(timespec="seconds")})
    if ws.uid not in PROGRESS:
        run_in_background(ws, not ws.load("apps_sync", {}).get("full_done"))
    return jsonify(ok=True, emails_found=found)


@bp.post("/api/naukri/disconnect")
@login_required
def naukri_disconnect(ws):
    ws.save("naukri", {})
    return jsonify(ok=True)
