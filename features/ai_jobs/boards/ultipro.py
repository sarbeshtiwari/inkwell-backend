"""UKG Pro / UltiPro job boards (slug "host/TENANT/boardId", e.g. recruiting.ultipro.com/ABC1000/<guid>). Example: recruiting.ultipro.ca/YMC5001YMGT/7fde8f3e-7920-4d9d-8394-87bd44cc2330"""
import re

from features.ai_jobs.boards._util import get_json, iso_day, plain

NAME, KIND, REGIONS = "UKG Pro", "company", ["us", "ca"]


def _where(locs):
    out = []
    for loc in locs or []:
        a = (loc or {}).get("Address") or {}
        label = ", ".join(filter(None, [a.get("City"), (a.get("State") or {}).get("Code")])) or (loc or {}).get("LocalizedName") or ""
        if label and label not in out:
            out.append(label)
    return " / ".join(out)


def fetch(slug):
    m = re.fullmatch(r"(recruiting\d*\.ultipro\.(?:com|ca))/([A-Za-z0-9]+)/(?:JobBoard/)?([0-9a-fA-F-]{36})", (slug or "").strip().strip("/"))
    if not m:
        return []
    host, tenant, board = m.groups()
    base = f"https://{host}/{tenant}/JobBoard/{board}"
    out, seen = [], set()
    for page in range(5):
        body = {"opportunitySearch": {"Top": 50, "Skip": page * 50, "QueryString": "", "Filters": [],
                                      "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate", "Ascending": False}]},
                "matchCriteria": {"PreferredJobs": [], "Educations": [], "LicenseAndCertifications": [], "Skills": [],
                                  "hasNoLicenses": False, "SkippedSkills": []}}
        rows = (get_json(f"{base}/JobBoardView/LoadSearchResults", method="POST", json=body) or {}).get("opportunities") or []
        for j in rows:
            jid = str(j.get("Id") or "")
            if not jid or not j.get("Title") or jid in seen:
                continue
            seen.add(jid)
            out.append({"id": f"ukg:{tenant}:{jid}", "ats": "", "board": tenant, "title": j["Title"].strip(),
                        "company": tenant, "location": _where(j.get("Locations")),
                        "url": f"{base}/OpportunityDetail?opportunityId={jid}", "text": plain(j.get("BriefDescription", "")),
                        "posted": iso_day(j.get("PostedDate")), "via": NAME})
        if len(rows) < 50:
            break
    return out
