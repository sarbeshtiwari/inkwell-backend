"""Boards whose pages carry their job list as data (Next.js __NEXT_DATA__, Nuxt, JSON-LD): found generically."""
import json
import re



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
