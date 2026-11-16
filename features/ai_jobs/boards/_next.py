"""Boards whose pages carry their job list as data (Next.js __NEXT_DATA__, Nuxt, JSON-LD): found generically."""
import json
import re

from features.ai_jobs.boards._util import get_text


def page_data(html):
    """The JSON blobs a page embeds for its scripts (Next.js, Nuxt, Apollo, plain application/json)."""
    out = []
    for m in re.finditer(r'<script[^>]*(?:id="__NEXT_DATA__"|type="application/json")[^>]*>(.*?)</script>', html or "", re.S):
        try:
            out.append(json.loads(m.group(1)))
        except ValueError:
            continue
    m = re.search(r"window\.__(?:NUXT|APOLLO_STATE|INITIAL_STATE|PRELOADED_STATE)__\s*=\s*(\{.*?\});?\s*</script>", html or "", re.S)
    if m:
        try:
            out.append(json.loads(m.group(1)))
        except ValueError:
            pass
    return out


def jobs_on(url, base, board, via, prefix, **kw):
    """Jobs listed on a board's search page: its embedded data first, else its JobPosting data."""
    from features.ai_jobs import sitejobs
    html = get_text(url, **kw)
    jobs = []
    for data in page_data(html):
        jobs = sitejobs.feed_jobs(data, base, board, "")
        if jobs:
            break
    if not jobs:
        jobs = sitejobs.jobs_in(html, base, board)
    for j in jobs:
        j["id"] = f"{prefix}:" + j["id"].split(":", 2)[-1]
        j["board"], j["via"] = board, via
    return jobs
