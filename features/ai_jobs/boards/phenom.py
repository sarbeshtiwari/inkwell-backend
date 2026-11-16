"""Phenom People career sites (branded host; slug "careers.acme.com" or "careers.acme.com/us/en"). Example: careers.mastercard.com"""
import re

from features.ai_jobs.boards._util import get_json, iso_day, plain

NAME, KIND, REGIONS = "Phenom", "company", ["global"]


def fetch(slug):
    host, _, prefix = (slug or "").strip().strip("/").partition("/")
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host.lower()):
        return []
    prefix = prefix or "global/en"
    out, seen = [], set()
    for page in range(5):
        body = {"lang": "en_global", "deviceType": "desktop", "country": "global", "pageName": "search-results",
                "ddoKey": "refineSearch", "from": page * 100, "size": 100, "jobs": True, "counts": True,
                "siteType": "external", "keywords": "", "global": True, "selected_fields": {}, "locationData": {}}
        rs = (get_json(f"https://{host}/widgets", method="POST", json=body) or {}).get("refineSearch") or {}
        rows = (rs.get("data") or {}).get("jobs") or []
        fresh = 0
        for j in rows:
            jid, title = str(j.get("jobId") or ""), plain(j.get("title", ""))
            if not jid or not title or jid in seen:
                continue
            seen.add(jid)
            fresh += 1
            where = plain(j.get("location") or j.get("cityStateCountry") or "") or ", ".join(
                filter(None, [j.get("city"), j.get("state"), j.get("country")]))
            out.append({"id": f"phn:{host}:{jid}", "ats": "", "board": host, "title": title,
                        "company": j.get("companyName") or host.split(".")[-2].capitalize(), "location": where,
                        "url": f"https://{host}/{prefix}/job/{jid}/{re.sub(r'[^A-Za-z0-9]+', '-', title).strip('-')}",
                        "text": plain(j.get("descriptionTeaser", "")), "posted": iso_day(j.get("postedDate") or j.get("dateCreated")),
                        "via": NAME})
        if not fresh or (page + 1) * 100 >= (rs.get("totalHits") or 0):
            break
    return out
