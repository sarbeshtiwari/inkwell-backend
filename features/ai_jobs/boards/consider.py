"""Consider job boards (VC portfolio boards); slug is the board host. Example: jobs.lsvp.com"""
import json
import re

import requests

from features.ai_jobs.boards._util import TIMEOUT, iso_day
from features.ai_jobs.common import UA

NAME, KIND, REGIONS = "Consider", "company", ["global"]


def fetch(slug):
    host = (slug or "").strip().lower().removeprefix("https://").split("/")[0]
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", host):
        return []
    origin, s = f"https://{host}", requests.Session()
    s.headers.update(UA)
    try:
        page = s.get(f"{origin}/jobs", timeout=TIMEOUT, allow_redirects=False).text
        m = re.search(r"window\.serverInitialData\s*=\s*(\{.*?\})\s*;?\s*</script>", page, re.S)
        data = json.loads(m.group(1)) if m else {}
        board = data.get("board") or {}
        if not board.get("id"):
            return []
        r = s.post(f"{origin}/api-boards/search-jobs", timeout=TIMEOUT, headers={
            "Accept": "application/json", "Referer": f"{origin}/jobs", "x-csrf-token": data.get("csrfToken") or ""},
            json={"meta": {"size": 500}, "board": {"id": board["id"], "isParent": board.get("isParent", True)},
                  "query": {"promoteFeatured": True}})
        jobs = r.json().get("jobs") or [] if r.ok else []
    except (requests.RequestException, ValueError):
        return []
    out = []
    for j in jobs:
        url = j.get("url") or j.get("applyUrl") or ""
        if not (url and j.get("title")):
            continue
        locs = j.get("locations") or [l.get("label") or l.get("value") for l in j.get("normalizedLocations") or [] if l]
        out.append({"id": f"cons:{host}:{j.get('jobId') or url}", "ats": "", "board": host, "title": j["title"],
                    "company": j.get("companyName") or "", "location": ", ".join(l.strip() for l in locs if l) or ("Remote" if j.get("remote") else ""),
                    "url": requests.compat.urljoin(origin, url), "text": "", "posted": iso_day(j.get("timeStamp")), "via": NAME})
    return out
