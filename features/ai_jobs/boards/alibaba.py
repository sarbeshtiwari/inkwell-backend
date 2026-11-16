"""Alibaba's own career site (talent.alibaba.com experienced-hire search)."""
import uuid

from features.ai_jobs.boards._util import get_json, iso_day, plain, words

NAME, KIND, REGIONS = "Alibaba Careers", "search", ["cn"]
HOST = "https://talent.alibaba.com"


def _search(key, token):
    body = {"channel": "group_official_site", "language": "zh", "batchId": "", "categories": "", "deptCodes": [],
            "key": key, "pageIndex": 1, "pageSize": 100, "regions": "", "subCategories": ""}
    d = get_json(f"{HOST}/position/search", method="POST", json=body,
                 headers={"Cookie": f"XSRF-TOKEN={token}", "X-XSRF-TOKEN": token}) or {}
    return [p for p in ((d.get("content") or {}).get("datas") or []) if p.get("name") and p.get("id") is not None]


def fetch(role):
    token = str(uuid.uuid4())  # the site's own double-submit CSRF pair: any matching cookie/header value
    rows = _search(role, token)
    for w in ([] if rows else sorted(words(role), key=len, reverse=True)[:2]):  # it matches the exact phrase, mostly in Chinese titles
        rows += _search(w, token)
    jobs = {}
    for p in rows:
        jobs.setdefault(p["id"], {"id": f"ali:{p['id']}", "ats": "", "board": "alibaba", "title": p["name"], "company": "Alibaba",
                                  "location": "/".join(filter(None, p.get("workLocations") or [])),
                                  "url": f"{HOST}/off-campus/position-detail?positionId={p['id']}",
                                  "text": plain(f"{p.get('description') or ''} {p.get('requirement') or ''}"),
                                  "posted": iso_day(int(p.get("publishTime") or p.get("modifyTime") or 0) // 1000 or ""), "via": NAME})
    return list(jobs.values())[:100]
