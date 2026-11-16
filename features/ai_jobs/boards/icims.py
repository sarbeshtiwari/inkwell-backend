"""iCIMS career portals (<slug>.icims.com, e.g. careers-acme). Example: careers-peraton"""
import re
from datetime import datetime

from features.ai_jobs.boards._util import get_text, plain

NAME, KIND, REGIONS = "iCIMS", "company", ["global"]


def _card(card, origin):
    href = re.search(r'href="([^"]*/jobs/(\d+)/[^"/]+/job[^"]*)"', card)
    title = re.search(r"<h3\b[^>]*>(.*?)</h3>", card, re.S)
    if not href or not title or not href.group(1).startswith(origin):
        return None
    where = re.search(r'field-label[^"]*"[^>]*>\s*Job Locations?\s*</span>\s*<span\b[^>]*>(.*?)</span>', card, re.S)
    posted = re.search(r'Posted Date</span>\s*<span title="(\d+/\d+/\d{4})', card)
    try:
        day = datetime.strptime(posted.group(1), "%m/%d/%Y").date().isoformat() if posted else ""
    except ValueError:
        day = ""
    return href.group(2), plain(title.group(1)), plain(where.group(1)) if where else "", href.group(1).split("?")[0], day


def fetch(slug):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug or "", re.I):
        return []
    origin = f"https://{slug}.icims.com"
    out, seen = [], set()
    for page in range(5):
        cards = [_card(c, origin) for c in get_text(f"{origin}/jobs/search?ss=1&pr={page}&in_iframe=1", allow_redirects=False).split("iCIMS_JobCardItem")[1:]]
        new = [c for c in cards if c and c[0] not in seen]
        if not new:
            break
        for jid, title, where, url, day in new:
            seen.add(jid)
            out.append({"id": f"icm:{slug}:{jid}", "ats": "", "board": slug, "title": title,
                        "company": re.sub(r"^(careers|jobs|globalcareers|uscareers)-", "", slug).capitalize(),
                        "location": where, "url": url, "text": "", "posted": day, "via": NAME})
    return out
