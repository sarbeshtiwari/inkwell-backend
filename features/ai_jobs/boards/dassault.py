"""Dassault Systèmes' own career site (the Exalead card search behind 3ds.com/careers)."""
import re
from urllib.parse import urlparse

from features.ai_jobs.boards._util import get_text, plain, unescape

NAME, KIND, REGIONS = "Dassault Systèmes Careers", "search", ["global"]
META = re.compile(r'<Meta name="([^"]+)"[^>]*>\s*<MetaString[^>]*name="value"[^>]*>(.*?)</MetaString>', re.S)


def _place(cats):
    found = dict(re.findall(r"(?:^|\s)(City|Country|Ville|Pays|Ciudad|País)/(.*?)(?=\s(?:Category|Type|Country|City|Products|Year|Pays|Ville|Ciudad|País)/|$)", cats))
    return found.get("City") or found.get("Ville") or found.get("Ciudad") or found.get("Country") or found.get("Pays") or found.get("País") or ""


def fetch(role):
    xml = get_text("https://www.3ds.com/apisearch/card_search_api", params=[
        ("lang", "en"), ("r", "f/card_content_type/career"), ("r", "f/card_content_categories_facet/cards language/en"),
        ("q", role), ("start", 0), ("hf", 100)])
    jobs = {}
    for hit in re.findall(r"<Hit\b.*?</Hit>", xml, re.S):
        meta = {}
        for k, v in META.findall(hit):
            meta.setdefault(k, v)
        title, url = plain(meta.get("content_title", "")), unescape(meta.get("content_cta_1_url", ""))
        host = urlparse(url).hostname or ""
        if not title or not (host == "3ds.com" or host.endswith(".3ds.com")):
            continue
        jid = (meta.get("card_id") or "").strip() or url.rstrip("/").rsplit("/", 1)[-1]
        day = re.match(r"(\d{4})/(\d{2})/(\d{2})", meta.get("content_start_datetime") or meta.get("card_update_timestamp") or "")
        jobs.setdefault(jid, {"id": f"3ds:{jid}", "ats": "", "board": "dassault", "title": title, "company": "Dassault Systèmes",
                              "location": _place(unescape(meta.get("content_categories", ""))), "url": url,
                              "text": plain(meta.get("content_description", "")), "posted": "-".join(day.groups()) if day else "", "via": NAME})
    return list(jobs.values())
