"""Personio company boards (<slug>.jobs.personio.de/xml feed). Example: c4a8"""
import xml.etree.ElementTree as ET

from features.ai_jobs.boards._util import get_text, iso_day, plain

NAME, KIND, REGIONS = "Personio", "company", ["de", "at", "ch", "es", "global"]


def fetch(slug):
    host = f"{slug}.jobs.personio.de"
    try:
        root = ET.fromstring(get_text(f"https://{host}/xml", allow_redirects=False).encode())
    except ET.ParseError:
        return []
    out = []
    for p in root.iter("position"):
        jid, title = (p.findtext("id") or "").strip(), (p.findtext("name") or "").strip()
        if not (jid.isdigit() and title):
            continue
        offices = list(dict.fromkeys(o.text.strip() for o in p.iter("office") if o.text and o.text.strip()))
        text = " ".join(plain(v.text or "") for v in p.iter("value"))
        out.append({"id": f"pso:{slug}:{jid}", "ats": "", "board": slug, "title": title,
                    "company": p.findtext("subcompany") or slug.capitalize(), "location": ", ".join(offices),
                    "url": f"https://{host}/job/{jid}", "text": text, "posted": iso_day(p.findtext("createdAt")), "via": NAME})
    return out
