"""Wellfound (formerly AngelList Talent): startup jobs from the public role pages (the list is in the page's data).
Find only: Wellfound is on the never-auto-apply list, so you apply there yourself. URLs as in Morrow's wellfound_bot (MIT)."""
import re
from datetime import datetime

from features.ai_jobs.boards._next import page_data
from features.ai_jobs.boards._util import get_text

NAME, KIND, REGIONS = "Wellfound", "search", ["global"]


def slug(role):
    return re.sub(r"[^a-z0-9]+", "-", (role or "software engineer").lower()).strip("-")


def fetch(role):
    html = get_text(f"https://wellfound.com/role/r/{slug(role)}")
    out = []
    for data in page_data(html):
        if not isinstance(data, dict):
            continue
        state = ((((data.get("props") or {}).get("pageProps") or {}).get("apolloState")) or {}).get("data") or {}
        owner = {}
        for k, v in state.items():
            if k.startswith("StartupResult:"):
                for ref in v.get("highlightedJobListings") or []:
                    owner[ref.get("__ref")] = v
        for k, j in state.items():
            if not k.startswith("JobListingSearchResult:") or not j.get("id"):
                continue
            co = owner.get(k) or {}
            where = ", ".join(j.get("locationNames") or [])
            if j.get("remote"):
                where = (where + " · " if where else "") + "Remote"
            out.append({"id": f"wf:{j['id']}", "ats": "", "board": "wellfound.com", "title": j.get("title", ""),
                        "company": co.get("name", ""), "location": where,
                        "url": f"https://wellfound.com/jobs/{j['id']}-{j.get('slug', '')}", "text": (j.get("description") or "")[:6000],
                        "posted": datetime.fromtimestamp(int(j["liveStartAt"])).date().isoformat() if j.get("liveStartAt") else "",
                        "salary": j.get("compensation") or "", "via": NAME})
    return out
