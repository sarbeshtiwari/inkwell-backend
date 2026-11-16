"""Teamtailor company boards (<slug>.teamtailor.com/jobs.rss). Example: polestar"""
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from features.ai_jobs.boards._util import get_text, plain

NAME, KIND, REGIONS = "Teamtailor", "company", ["global"]


def _day(value):
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError):
        return ""


def fetch(slug):
    try:
        root = ET.fromstring(get_text(f"https://{slug}.teamtailor.com/jobs.rss", allow_redirects=False).encode())
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        url, title = (it.findtext("link") or "").strip(), (it.findtext("title") or "").strip()
        if not (url and title):
            continue
        places = [e.text for e in it.iter() if e.tag.endswith("}name") and e.text]
        if it.findtext("remoteStatus") == "fully":
            places.append("Remote")
        jid = re.search(r"/jobs/(\d+)", url)
        out.append({"id": f"tt:{slug}:{jid.group(1) if jid else url}", "ats": "", "board": slug, "title": title,
                    "company": it.findtext("company_name") or slug.capitalize(), "location": "; ".join(places), "url": url,
                    "text": plain(it.findtext("description") or ""), "posted": _day(it.findtext("pubDate")), "via": NAME})
    return out
