"""ITviec (itviec.com): IT jobs in Vietnam, from its search pages."""
import re
from urllib.parse import quote

from features.ai_jobs.boards._cards import cards, first
from features.ai_jobs.boards._util import get_text, plain

NAME, KIND, REGIONS = "ITviec", "search", ["vn"]


def fetch(role):
    out = []
    path = "https://itviec.com/it-jobs/" + quote(role.strip().lower().replace(" ", "-").replace("/", ""))
    for page in (1, 2, 3):
        html = get_text(path + (f"?page={page}" if page > 1 else ""))
        found = cards(html, r"data-search--job-selection-job-slug-value=['\"]([^'\"]+)['\"]")
        for m, card in found:
            slug = m.group(1)
            title = first(r"data-search--job-selection-target=['\"]jobTitle['\"][^>]*>\s*<a[^>]*>(.*?)</a>", card)
            if not title or any(o["id"] == f"itv:{slug}" for o in out):
                continue
            out.append({"id": f"itv:{slug}", "ats": "", "board": "itviec", "title": title,
                        "company": next((c for c in (plain(x) for x in re.findall(r"href=[\"'](?:https?://itviec\.com)?/companies/[^\"']+[\"'][^>]*>(.*?)</a>",
                                                                                  card, re.S)) if c), ""),
                        "location": first(r"#map-pin\"?></use></svg>\s*<div[^>]*title=[\"']([^\"']*)[\"']", card) or "Vietnam",
                        "url": f"https://itviec.com/it-jobs/{slug}", "text": "", "posted": "", "via": NAME})
        if len(found) < 10:
            break
    return out
