"""Oracle Recruiting Cloud (Oracle HCM Candidate Experience) company boards, public REST API.
Board address: <host>/<site number>, e.g. jpmc.fa.oraclecloud.com/CX_1002. Example: jpmc.fa.oraclecloud.com/CX_1002"""
import re

from features.ai_jobs.boards._util import get_json, iso_day

NAME, KIND, REGIONS = "Oracle Recruiting Cloud", "company", ["global"]


def fetch(slug):
    m = re.fullmatch(r"([a-z0-9.-]+\.oraclecloud\.com)(?:/(CX_\w+))?", (slug or "").strip("/").lower().replace("cx_", "CX_"))
    if not m:
        return []
    host, site = m.group(1), m.group(2) or "CX_1"
    out = []
    for offset in range(0, 500, 100):
        d = get_json(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
                     params={"onlyData": "true", "expand": "requisitionList.workLocation,requisitionList.secondaryLocations",
                             "finder": f"findReqs;siteNumber={site},limit=100,sortBy=POSTING_DATES_DESC,offset={offset}"}) or {}
        items = (d.get("items") or [{}])[0]
        rows = items.get("requisitionList") or []
        for j in rows:
            if not j.get("Id") or not j.get("Title"):
                continue
            out.append({"id": f"orc:{host.split('.')[0]}:{j['Id']}", "ats": "", "board": f"{host}/{site}", "title": j["Title"],
                        "company": host.split(".")[0].upper() if len(host.split(".")[0]) <= 4 else host.split(".")[0].capitalize(),
                        "location": j.get("PrimaryLocation", ""), "url": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{j['Id']}",
                        "text": j.get("ShortDescriptionStr") or "", "posted": iso_day(j.get("PostedDate")), "via": NAME})
        if len(rows) < 100 or offset + 100 >= (items.get("TotalJobsCount") or 0):
            break
    return out
