"""BeeSite (milch & zucker) job search APIs; slug is the <host>.app.beesite.de prefix. Example: mercedes-benz-beesite-production-gjb"""
import json
import re

from features.ai_jobs.boards._util import get_json, iso_day, unescape

NAME, KIND, REGIONS = "BeeSite", "company", ["de"]

FIELDS = ["PositionID", "PositionTitle", "PositionURI", "PositionLocation.CityName", "PublicationStartDate"]


def fetch(slug):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug or ""):
        return []
    company = slug.split("-beesite")[0].replace("-", " ").title()
    out, seen = [], set()
    for page in range(5):
        data = {"LanguageCode": "EN", "SearchCriteria": [], "SearchParameters": {
            "FirstItem": page * 100 + 1, "CountItem": 100, "MatchedObjectDescriptor": FIELDS,
            "Sort": [{"Criterion": "PublicationStartDate", "Direction": "DESC"}]}}
        d = get_json(f"https://{slug}.app.beesite.de/search", params={"data": json.dumps(data)}) or {}
        items = (d.get("SearchResult") or {}).get("SearchResultItems") or []
        fresh = 0
        for it in items:
            p = it.get("MatchedObjectDescriptor") or {}
            jid, url = str(it.get("MatchedObjectId") or p.get("PositionID") or ""), p.get("PositionURI") or ""
            if not (jid and url.startswith("http") and p.get("PositionTitle")) or jid in seen:
                continue
            seen.add(jid)
            fresh += 1
            cities = dict.fromkeys(filter(None, ((l or {}).get("CityName", "") for l in p.get("PositionLocation") or [])))
            out.append({"id": f"bee:{slug}:{jid}", "ats": "", "board": slug, "title": unescape(p["PositionTitle"]),
                        "company": company, "location": ", ".join(cities), "url": url,
                        "text": "", "posted": iso_day(p.get("PublicationStartDate")), "via": NAME})
        if len(items) < 100 or not fresh:
            break
    return out
