"""IBM's own career site (the public search API behind careers.ibm.com)."""
import re

from features.ai_jobs.boards._util import get_json, plain

NAME, KIND, REGIONS = "IBM Careers", "search", ["global"]
API = "https://www-api.ibm.com/search/api/v2"


def fetch(role):
    body = {"appId": "careers", "scopes": ["careers2"], "size": 100, "from": 0, "lang": "zz", "localeSelector": {},
            "query": {"bool": {"must": [{"multi_match": {"query": role, "fields": ["title^3", "description"]}}]}},
            "post_filter": {"bool": {"must": []}}, "sort": [{"_score": "desc"}], "sm": {"query": role, "lang": "zz"},
            "_source": ["_id", "title", "url", "description", "field_keyword_17", "field_keyword_19"]}
    d = get_json(API, method="POST", json=body) or {}
    jobs = []
    for h in (d.get("hits") or {}).get("hits") or []:
        s = h.get("_source") or {}
        url, title = (s.get("url") or "").strip(), (s.get("title") or "").strip()
        if not title or not url.startswith("http"):
            continue
        jid = (re.search(r"jobId=(\d+)", url) or [None, h.get("_id", "")[:16]])[1]
        jobs.append({"id": f"ibm:{jid}", "ats": "", "board": "ibm", "title": title, "company": "IBM",
                     "location": " · ".join(filter(None, [s.get("field_keyword_19", ""), s.get("field_keyword_17", "")])),
                     "url": url, "text": plain(s.get("description", "")), "posted": "", "via": NAME})
    return jobs
