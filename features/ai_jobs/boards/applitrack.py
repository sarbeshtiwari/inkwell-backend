"""Frontline AppliTrack: K-12 district job boards (www.applitrack.com/<district>/onlineapp). Board address: the district slug.
Example: camas"""
import re
from datetime import datetime

from features.ai_jobs.boards._util import get_text, plain

NAME, KIND, REGIONS = "AppliTrack", "company", ["us"]


def fetch(slug):
    slug = (re.search(r"applitrack\.com/([\w-]+)", slug or "") or [None, slug or ""])[1].strip("/")
    if not re.fullmatch(r"[\w-]+", slug):
        return []
    base = f"https://www.applitrack.com/{slug}/onlineapp"
    body = get_text(f"{base}/jobpostings/Output.asp?all=1")
    if "function applyFor" not in body:
        return []
    html = body.replace("\\'", "'").replace('\\"', '"')
    out, seen = [], set()
    for block in re.split(r"<table class='title'", html, flags=re.I)[1:]:
        jid = (re.search(r"JobID:\s*(\d+)", block) or [None, ""])[1]
        title = plain((re.search(r"<td id='wrapword'[^>]*>(.*?)</td>", block, re.S) or [None, ""])[1])
        if not jid or not title or jid in seen:
            continue
        seen.add(jid)

        def field(name):
            return plain((re.search(rf"{name}:\s*</span>.*?<span class=['\"]normal['\"][^>]*>(.*?)</span>", block, re.S | re.I) or [None, ""])[1])
        try:
            day = datetime.strptime(field("Date Posted"), "%m/%d/%Y").date().isoformat()
        except ValueError:
            day = ""
        out.append({"id": f"at:{slug}:{jid}", "ats": "", "board": slug, "title": title.replace("�", ""), "company": field("District") or slug.capitalize(),
                    "location": field("Location"), "url": f"{base}/default.aspx?AppliTrackJobId={jid}&AppliTrackLayoutMode=detail&AppliTrackViewPosting=1",
                    "text": "", "posted": day, "via": NAME})
    return out
