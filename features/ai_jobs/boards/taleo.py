"""Oracle Taleo career sections (<tenant>.taleo.net/careersection/<section>/jobsearch.ftl): the section's own
search request, as its page makes it. Board address: <host>/<section>. Example: cu.taleo.net/2"""
import json
import re
from datetime import datetime

import requests

from features.ai_jobs.common import UA

NAME, KIND, REGIONS = "Taleo", "company", ["global"]


def _text(v):
    return re.sub(r"\s+", " ", str(v if not isinstance(v, (list, dict)) else json.dumps(v, ensure_ascii=False))).strip()


def _day(text):
    try:
        return datetime.strptime(text.strip(), "%b %d, %Y").date().isoformat()
    except ValueError:
        return ""


def fetch(slug):
    m = re.fullmatch(r"([a-z0-9-]+\.taleo\.net)/([\w-]+)", (slug or "").strip("/").lower())
    if not m:
        return []
    host, section = m.groups()
    s = requests.Session()
    try:
        shell = s.get(f"https://{host}/careersection/{section}/jobsearch.ftl", headers=UA, timeout=12).text.replace("&amp;", "&")
        portal = re.search(r"[?&]portal=(\d+)|[\"']portal(?:No|Id)?[\"']?\s*[:=]\s*[\"']?(\d+)", shell)
        if not portal:
            return []
        portal = portal.group(1) or portal.group(2)
        lang = (re.search(r"[?&]lang=([\w-]+)", shell) or [None, "en"])[1]
        out = []
        for page in range(1, 11):
            d = s.post(f"https://{host}/careersection/rest/jobboard/searchjobs", params={"lang": lang, "portal": portal}, timeout=12,
                       headers={**UA, "Content-Type": "application/json", "Accept": "application/json", "X-Requested-With": "XMLHttpRequest",
                                "tz": "GMT+00:00", "tzname": "UTC"},
                       json={"multilineEnabled": False, "sortingSelection": {"sortBySelectionParam": "5", "ascendingSortingOrder": "true"},
                             "fieldData": {"fields": {"KEYWORD": "", "LOCATION": ""}, "valid": True},
                             "filterSelectionParam": {"searchFilterSelections": []},
                             "advancedSearchFiltersSelectionParam": {"searchFilterSelections": []}, "pageNo": page}).json()
            rows = d.get("requisitionList") or []
            for r in rows:
                jid = str(r.get("contestNo") or r.get("jobId") or "").strip()
                cols = [_text(c) for c in r.get("column") or []]
                if not jid or not cols or not cols[0]:
                    continue
                where = re.sub(r'[\[\]"]', "", cols[1]) if len(cols) > 1 else ""
                out.append({"id": f"tal:{host.split('.')[0]}:{jid}", "ats": "", "board": slug, "title": cols[0],
                            "company": host.split(".")[0].upper() if len(host.split(".")[0]) <= 4 else host.split(".")[0].capitalize(),
                            "location": where, "url": f"https://{host}/careersection/{section}/jobdetail.ftl?job={jid}&lang={lang}",
                            "text": "", "posted": _day(cols[2] if len(cols) > 2 else ""), "via": NAME})
            total = ((d.get("pagingData") or {}).get("totalCount")) or 0
            if not rows or len(out) >= total or len(out) >= 300:
                break
        return out
    except (requests.RequestException, ValueError):
        return []
    finally:
        s.close()
