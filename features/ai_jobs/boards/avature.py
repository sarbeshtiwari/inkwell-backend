"""Avature career sites (<tenant>.avature.net; slug "tenant" or "tenant/en_US/main" for a non-/careers portal). Example: bloomberg"""
import re

from features.ai_jobs.boards._util import get_text, plain, unescape

NAME, KIND, REGIONS = "Avature", "company", ["global"]
ARTICLE = re.compile(r'<article class="article article--result.*?</article>', re.S)
LINK = re.compile(r'<a[^>]*href="([^"]*/JobDetail/[^"]*?/(\d+)[^"]*)"[^>]*>(.*?)</a>', re.S)


def fetch(slug):
    tenant, _, path = (slug or "").strip().strip("/").partition("/")
    if not re.fullmatch(r"[a-z0-9-]+", tenant.lower()):
        return []
    base = f"https://{tenant}.avature.net/{path or 'careers'}/SearchJobs"
    out, seen, offset = [], set(), 0
    for _ in range(5):
        blocks = ARTICLE.findall(get_text(f"{base}?jobOffset={offset}"))
        fresh = 0
        for b in blocks:
            m = LINK.search(b)
            if not m or m.group(2) in seen or not plain(m.group(3)):
                continue
            seen.add(m.group(2))
            fresh += 1
            loc = re.search(r'class="list-item-location">(.*?)</span>', b, re.S)
            out.append({"id": f"avt:{tenant}:{m.group(2)}", "ats": "", "board": tenant, "title": plain(m.group(3)),
                        "company": tenant.capitalize(), "location": plain(loc.group(1)) if loc else "",
                        "url": unescape(m.group(1)), "text": "", "posted": "", "via": NAME})
        if not fresh:
            break
        offset += len(blocks)
    return out
