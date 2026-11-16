"""PageUp career sites (careers.pageuppeople.com). Slug: "<tenant>/<site>/<lang>". Example: 1216/cw/en-us"""
import re

from features.ai_jobs.boards._util import get_text, plain

NAME, KIND, REGIONS = "PageUp", "company", ["au", "nz", "gb", "us"]
ORIGIN = "https://careers.pageuppeople.com"


def fetch(slug):
    if not re.fullmatch(r"\d+/[A-Za-z0-9_-]+/[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})?", (slug or "").strip("/")):
        return []
    slug = slug.strip("/")
    out, seen, company = [], set(), ""
    for page in range(1, 6):
        page_html = get_text(f"{ORIGIN}/{slug}/listing/", params={"page": page, "page-items": 100})
        if not company:
            parts = plain((re.search(r"<title>(.*?)</title>", page_html, re.S) or [None, ""])[1]).split(" - ")
            company = ([p for p in parts if not re.search(r"opportunit|vacanc|listing|search", p, re.I)] or parts)[-1]
        fresh = 0
        for row in page_html.split('class="job-link"')[1:]:
            m = re.match(r'[^>]*href="(/[^"]*/job/(\d+)[^"]*)"[^>]*>(.*?)</a>', row, re.S)
            if not m or m[2] in seen:
                continue
            seen.add(m[2])
            fresh += 1
            where = re.search(r'class="location"[^>]*>(.*?)</span>', row.split("job-link")[0], re.S)
            summary = re.search(r'<td colspan="\d+">(.*?)</td>', row, re.S)
            out.append({"id": f"pu:{slug}:{m[2]}", "ats": "", "board": slug, "title": plain(m[3]),
                        "company": company or "PageUp", "location": plain(where[1]) if where else "",
                        "url": f"{ORIGIN}/{slug}/job/{m[2]}", "text": plain(summary[1]) if summary else "",
                        "posted": "", "via": NAME})
        if not fresh or "more-link" not in page_html or len(out) >= 500:
            break
    return out
