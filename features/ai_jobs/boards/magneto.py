"""Magneto (magneto365.com): jobs in Colombia, from its search page's job cards."""
import re
from urllib.parse import quote, urljoin

from features.ai_jobs.boards._util import get_text, plain

NAME, KIND, REGIONS = "Magneto", "search", ["co"]


def fetch(role):
    base = "https://www.magneto365.com/co/trabajos/buscar/" + quote(role.strip().lower().replace(" ", "-"))
    html = get_text(base)
    out = []
    for chunk in re.split(r"<article\b", html, flags=re.I)[1:]:
        card = re.split(r"</article>", chunk, flags=re.I)[0]
        if "card-jobs" not in card[:card.find(">") + 1]:
            continue
        link = next(((urljoin(base, h), t) for h, t in re.findall(r'<a\b[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>', card, re.S)
                     if re.search(r"^https://(www\.)?magneto365\.com/[a-z]{2}/empleos/[a-z0-9-]+-\d+/?$", urljoin(base, h), re.I)), None)
        if not link or len(plain(link[1])) < 3:
            continue
        named = plain((re.search(r"<h3\b[^>]*>(.*?)</h3>", card, re.S) or [None, ""])[1]).split(" | ")[0].strip()
        paras = [plain(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", card, re.S)]
        out.append({"id": "mg:" + re.search(r"-(\d+)/?$", link[0]).group(1), "ats": "", "board": "magneto", "title": plain(link[1]),
                    "company": "" if re.match(r"(empresa )?confidencial|magneto", named, re.I) else named,
                    "location": paras[-1] if paras and not paras[-1].startswith("$") else "Colombia", "url": link[0].rstrip("/"),
                    "text": "", "posted": "", "salary": next((p for p in paras if p.startswith("$")), ""), "via": NAME})
    return out
