"""Zoho Recruit career sites (slug "tenant" for <tenant>.zohorecruit.com, or a full host like "tenant.zohorecruit.in"). Example: shijigroup"""
import json
import re

from features.ai_jobs.boards._util import get_text, iso_day, plain, unescape

NAME, KIND, REGIONS = "Zoho Recruit", "company", ["global"]
HOST = re.compile(r"[a-z0-9][a-z0-9-]*\.zohorecruit\.(com|ca|eu|in|com\.au|jp|sa|com\.cn)")


def fetch(slug):
    host = (slug or "").strip().lower()
    host = host if "." in host else f"{host}.zohorecruit.com"
    if not HOST.fullmatch(host):
        return []
    m = re.search(r'<input[^>]*\bid="jobs"[^>]*>', get_text(f"https://{host}/jobs/Careers"))
    val = re.search(r'value="([^"]*)"', m.group(0)) if m else None
    try:
        rows = json.loads(unescape(val.group(1))) if val else []
    except ValueError:
        return []
    board = host.split(".")[0]
    out = []
    for r in rows if isinstance(rows, list) else []:
        title = r.get("Posting_Title") or r.get("Job_Opening_Name")
        if not title or not r.get("id") or r.get("Is_Locked") is True or r.get("Publish") is False:
            continue
        where = ", ".join(filter(None, [r.get("City"), r.get("State"), r.get("Country"), "Remote" if r.get("Remote_Job") else ""]))
        out.append({"id": f"zr:{board}:{r['id']}", "ats": "", "board": board, "title": title, "company": board.capitalize(),
                    "location": where, "url": f"https://{host}/jobs/Careers/{r['id']}", "text": plain(r.get("Job_Description", "")),
                    "posted": iso_day(r.get("Date_Opened")), "via": NAME})
    return out
