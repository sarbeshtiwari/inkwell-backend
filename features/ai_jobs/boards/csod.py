"""Cornerstone OnDemand career sites (<tenant>.csod.com/ux/ats/careersite/<site>/home?c=<corp>).
The site's home page hands every visitor an anonymous search token; the job list comes from its search API.
Board address: <host>/<site id>. Example: career-ohb.csod.com/4"""
import re

import requests

from features.ai_jobs.common import UA

NAME, KIND, REGIONS = "Cornerstone", "company", ["global"]


def fetch(slug):
    m = re.fullmatch(r"([a-z0-9-]+\.csod\.com)/(\d+)", (slug or "").strip("/").lower())
    if not m:
        return []
    host, site = m.groups()
    corp = host.split(".")[0]
    home = f"https://{host}/ux/ats/careersite/{site}/home?c={corp}"
    s = requests.Session()
    try:
        r = s.get(home, headers=UA, timeout=10)
        token = re.search(r'"token":"(eyJ[^"]+)"', r.text)
        if not token:
            return []
        out = []
        for page in range(1, 11):
            d = s.post(f"https://{host}/services/x/career-site/v1/search", timeout=10,
                       headers={**UA, "Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {token.group(1)}"},
                       json={"careerSiteId": int(site), "careerSitePageId": int(site), "pageNumber": page, "pageSize": 25, "cultureId": 1,
                             "cultureName": "en-US", "searchText": "", "states": [], "countryCodes": [], "cities": [], "placeID": "",
                             "radius": None, "postingsWithinDays": None, "customFieldCheckboxKeys": [], "customFieldDropdowns": [],
                             "customFieldRadios": []}).json().get("data") or {}
            rows = d.get("requisitions") or []
            for j in rows:
                if not j.get("requisitionId") or not j.get("displayJobTitle"):
                    continue
                loc = (j.get("locations") or [{}])[0]
                day = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", j.get("postingEffectiveDate") or "")
                out.append({"id": f"csod:{corp}:{j['requisitionId']}", "ats": "", "board": slug, "title": j["displayJobTitle"],
                            "company": corp.replace("career-", "").replace("careers-", "").upper(),
                            "location": ", ".join(filter(None, [loc.get("city"), loc.get("state"), loc.get("country")])),
                            "url": f"https://{host}/ux/ats/careersite/{site}/home/requisition/{j['requisitionId']}?c={corp}", "text": "",
                            "posted": f"{day.group(3)}-{int(day.group(1)):02d}-{int(day.group(2)):02d}" if day else "", "via": NAME})
            if len(rows) < 25 or len(out) >= (d.get("totalCount") or 0):
                break
        return out
    except (requests.RequestException, ValueError):
        return []
    finally:
        s.close()
