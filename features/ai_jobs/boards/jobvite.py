"""Jobvite boards (jobs.jobvite.com/<slug>), read from the public XML job feed. Example: tylertech"""
import re
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

from features.ai_jobs.boards._util import get_text, plain
from features.ai_jobs.common import UA

NAME, KIND, REGIONS = "Jobvite", "company", ["global"]


def _day(value):
    try:
        return datetime.strptime(value or "", "%m/%d/%Y").date().isoformat()
    except ValueError:
        return ""


def fetch(slug):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", slug or ""):
        return []
    eid = re.search(r"companyEId\s*[:=]\s*['\"]([A-Za-z0-9_-]{4,40})['\"]", get_text(f"https://jobs.jobvite.com/{slug}?fr=true&nl=1"))
    if not eid:
        return []
    try:  # the feed is slow to build (~15s), so it gets a longer timeout than get_text's
        r = requests.get(f"https://app.jobvite.com/CompanyJobs/Xml.aspx?c={eid.group(1)}", headers=UA, timeout=40, allow_redirects=False)
        root = ET.fromstring(r.content) if r.ok else None
    except (requests.RequestException, ET.ParseError):
        return []
    if root is None:
        return []
    out, by_url = [], {}
    for j in root.iter("job"):
        title = (j.findtext("title") or "").strip()
        url = (j.findtext("detail-url") or j.findtext("apply-url") or "").strip().replace("http://", "https://", 1)
        where = (j.findtext("location") or "").strip()
        if not title or not url:
            continue
        if url in by_url:  # one posting per location in the feed
            if where and where not in by_url[url]["location"]:
                by_url[url]["location"] += " · " + where
            continue
        job = {"id": f"jv:{slug}:{j.findtext('id') or url}", "ats": "", "board": slug, "title": title,
               "company": slug.capitalize(), "location": where, "url": url,
               "text": plain(j.findtext("description") or "")[:4000], "posted": _day(j.findtext("date")), "via": NAME}
        by_url[url] = job
        out.append(job)
        if len(out) >= 500:
            break
    return out
