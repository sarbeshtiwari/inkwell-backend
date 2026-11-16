"""Heckler & Koch careers (heckler-koch.com): the vacancy cards on its job page."""
import re

from features.ai_jobs.boards._util import get_text, plain, title_matches

NAME, KIND, REGIONS = "Heckler & Koch", "search", ["de"]


def fetch(role):
    html = get_text("https://www.heckler-koch.com/de/Karriere/Stellenangebote")
    out, seen = [], set()
    for m in re.finditer(r'href="(https://karriere\.heckler-koch\.com/jobposting/([a-f0-9]+))"[^>]*>(.*?)</a>', html, re.S):
        url, key, card = m.groups()
        title = plain((re.search(r"<h3[^>]*>(.*?)</h3>", card, re.S) or [None, ""])[1])
        if not title or key in seen or not title_matches(title, role):
            continue
        seen.add(key)
        field = plain((re.search(r"<p>(.*?)</p>", card, re.S) or [None, ""])[1])
        out.append({"id": f"hk:{key[:24]}", "ats": "", "board": "hecklerkoch", "title": title, "company": "Heckler & Koch",
                    "location": "Oberndorf am Neckar, Germany", "url": url, "text": field, "posted": "", "via": NAME})
    return out
