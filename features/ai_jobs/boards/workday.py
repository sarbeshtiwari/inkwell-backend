"""Workday career sites; slug "tenant/wdN/site" (from <tenant>.wdN.myworkdayjobs.com/<site>). Example: mastercard/wd1/CorporateCareers"""
import re
from datetime import date, timedelta

from features.ai_jobs.boards._util import get_json

NAME, KIND, REGIONS = "Workday", "company", ["global"]
PAGE = 20  # the most Workday's CXS API returns per call


def _posted(label):
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' -> ISO day ('' for '30+ Days Ago' or unknown)."""
    label = (label or "").lower()
    if "today" in label:
        return date.today().isoformat()
    if "yesterday" in label:
        return (date.today() - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+) days? ago", label)
    return (date.today() - timedelta(days=int(m.group(1)))).isoformat() if m and "+" not in label else ""


def fetch(slug):
    m = re.fullmatch(r"([A-Za-z0-9_-]+)/(wd\d{1,3})/([A-Za-z0-9_-]+)", (slug or "").strip("/"))
    if not m:
        return []
    tenant, wd, site = m.groups()
    origin = f"https://{tenant}.{wd}.myworkdayjobs.com"
    out, total = [], 0
    for page in range(10):  # 20 a page, so up to 200 postings
        d = get_json(f"{origin}/wday/cxs/{tenant}/{site}/jobs", method="POST",
                     json={"appliedFacets": {}, "limit": PAGE, "offset": page * PAGE, "searchText": ""}) or {}
        rows = d.get("jobPostings") or []
        total = total or d.get("total") or 0  # only the first page reports it
        for j in rows:
            path = j.get("externalPath") or ""
            if not j.get("title") or not path:
                continue
            out.append({"id": f"wd:{tenant}:{path.rsplit('/', 1)[-1]}", "ats": "", "board": slug, "title": j["title"],
                        "company": tenant.capitalize(), "location": j.get("locationsText", ""), "url": f"{origin}/{site}{path}",
                        "text": "", "posted": _posted(j.get("postedOn")), "via": NAME})
        if len(rows) < PAGE or (page + 1) * PAGE >= total:
            break
    return out
