"""Homerun company boards (Atom feed at feed.homerun.co/<slug>). Example: trifork"""
import xml.etree.ElementTree as ET

from features.ai_jobs.boards._util import get_text, iso_day, plain

NAME, KIND, REGIONS = "Homerun", "company", ["nl", "de", "global"]
A = "{http://www.w3.org/2005/Atom}"


def fetch(slug):
    try:
        root = ET.fromstring(get_text(f"https://feed.homerun.co/{slug.lower()}", allow_redirects=False).encode())
    except ET.ParseError:
        return []
    company = root.findtext(f"{A}title") or slug.capitalize()
    out = []
    for e in root.iter(f"{A}entry"):
        link = e.find(f"{A}link")
        url, title = (link.get("href", "") if link is not None else "").removesuffix("/apply"), (e.findtext(f"{A}title") or "").strip()
        if not (title and url.startswith("https://")):
            continue
        where = e.findtext(f"{A}location/{A}name") or ""
        salary = e.findtext(f"{A}salary_indication") or ""
        out.append({"id": f"hr:{slug}:{e.findtext(f'{A}id') or url}", "ats": "", "board": slug, "title": title,
                    "company": company, "location": "" if where == "Other" else where, "url": url,
                    "text": plain(e.findtext(f"{A}description") or ""), "posted": iso_day(e.findtext(f"{A}updated")),
                    "salary": "" if salary == "N/A" else salary, "via": NAME})
    return out
