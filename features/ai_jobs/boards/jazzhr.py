"""JazzHR boards (<slug>.applytojob.com/apply). Example: ratehubinc"""
import re

from features.ai_jobs.boards._util import get_text, plain

NAME, KIND, REGIONS = "JazzHR", "company", ["global"]
CARD = re.compile(r'<li\b[^>]*class="[^"]*list-group-item[^"]*"[^>]*>(.*?)</li>', re.S | re.I)
LINK = re.compile(r'<a\b[^>]*href="([^"]*/apply/([^"/?]+)[^"]*)"[^>]*>(.*?)</a>', re.S | re.I)


def fetch(slug):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug or "", re.I):
        return []
    origin = f"https://{slug}.applytojob.com"
    page = get_text(f"{origin}/apply", headers={"Accept": "text/html"}, allow_redirects=False)
    out, seen = [], set()
    for card in CARD.findall(page):
        m = LINK.search(card)
        if not m or m.group(2) in seen:
            continue
        seen.add(m.group(2))
        where = plain((re.search(r"fa-map-marker[^<]*</i>\s*([^<]+)", card) or [None, ""])[1])
        url = m.group(1).split("?")[0]
        out.append({"id": f"jz:{slug}:{m.group(2)}", "ats": "", "board": slug, "title": plain(m.group(3)),
                    "company": slug.capitalize(), "location": where, "url": url if url.startswith("http") else origin + url,
                    "text": "", "posted": "", "via": NAME})
    return out
