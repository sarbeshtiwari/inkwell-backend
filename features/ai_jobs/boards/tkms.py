"""thyssenkrupp Marine Systems' own career site (jobs.tkmsgroup.com query API)."""
import re

from features.ai_jobs.boards._util import get_json, iso_day, plain

NAME, KIND, REGIONS = "TKMS Careers", "search", ["de"]
ORIGIN = "https://jobs.tkmsgroup.com"


def _where(d):
    locs = list(dict.fromkeys(str(l.get("cityState") or l.get("city") or "").strip() for l in d.get("locations") or []))
    return " / ".join(filter(None, locs)) or ", ".join(dict.fromkeys(filter(None, [d.get("city"), d.get("country")])))


def fetch(role):
    jobs = {}
    for page in range(3):
        r = get_json(f"{ORIGIN}/api/filter/query", method="POST",
                     json={"searchQuery": role, "filter": {}, "subclient": "tkms", "locale": "en", "page": page}) or {}
        for item in r.get("jobs") or []:
            d = item.get("data") or {}
            jid, title = str(d.get("id") or ""), plain(str(d.get("title") or ""))
            if jid and title:
                slug = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-") or "job"
                jobs.setdefault(jid, {"id": f"tkms:{jid}", "ats": "", "board": "tkms", "title": title,
                                      "company": d.get("company") or "thyssenkrupp Marine Systems", "location": _where(d),
                                      "url": f"{ORIGIN}/en/job/{slug}/{jid}", "text": "",
                                      "posted": iso_day(d.get("postingDate_timestamp") or d.get("postingDate")), "via": NAME})
        if not isinstance(r.get("nextPage"), int):
            break
    return list(jobs.values())
