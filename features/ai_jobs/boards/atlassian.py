"""Atlassian's own career site (public listings feed, every open role in one call)."""
import re

from features.ai_jobs.boards._util import get_json, plain, title_matches

NAME, KIND, REGIONS = "Atlassian Careers", "search", ["global"]


def fetch(role):
    d = get_json("https://www.atlassian.com/endpoint/careers/listings")
    jobs = []
    for j in d if isinstance(d, list) else []:
        title = (j.get("title") or "").strip()
        url = ((j.get("portalJobPost") or {}).get("portalUrl") or j.get("applyUrl") or "").strip()
        if not title or not url.startswith("http") or not title_matches(title, role):
            continue
        jobs.append({"id": f"atl:{j.get('id')}", "ats": "", "board": "atlassian", "title": title, "company": "Atlassian",
                     "location": "; ".join(re.sub(r"\s+", " ", str(l)).strip() for l in j.get("locations") or []),
                     "url": url, "posted": "", "via": NAME,
                     "text": plain(" ".join(j.get(k) or "" for k in ("overview", "responsibilities", "qualifications")))})
    return jobs[:100]
