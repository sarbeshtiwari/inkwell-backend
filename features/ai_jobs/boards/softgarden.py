"""Softgarden company boards (<slug>.softgarden.io job widget). Example: renk-group"""
import re
from urllib.parse import urljoin

from features.ai_jobs.boards._util import get_text, plain, unescape

NAME, KIND, REGIONS = "Softgarden", "company", ["de", "at", "ch"]


def _day(raw):
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})$", raw.strip())
    if not m:
        return ""
    d, mo, y = (int(x) for x in m.groups())
    return f"{y + 2000 if y < 100 else y:04d}-{mo:02d}-{d:02d}"


def fetch(slug):
    if not re.fullmatch(r"[a-z0-9-]+", slug or ""):
        return []
    widget = f"https://{slug}.softgarden.io/de/widgets/jobs"
    out, seen = [], set()
    for block in get_text(widget).split('<div class="matchElement" id="job_id_')[1:500]:
        jid = (re.match(r"(\d+)\"", block) or [None, None])[1]
        link = re.search(r'<a href="([^"]*/job/[^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not jid or not link or jid in seen:
            continue
        seen.add(jid)
        cities = list(dict.fromkeys(plain(c) for c in re.findall(r'class="location-view-item"[^>]*>(.*?)</span>', block, re.S)))
        date = re.search(r'class="matchValue date"[^>]*>(.*?)</div>', block, re.S)
        out.append({"id": f"sg:{slug}:{jid}", "ats": "", "board": slug, "title": plain(link[2]),
                    "company": slug.replace("-", " ").title(), "location": " / ".join(filter(None, cities)),
                    "url": urljoin(widget, unescape(link[1])), "text": "", "posted": _day(plain(date[1])) if date else "",
                    "via": NAME})
    return out
