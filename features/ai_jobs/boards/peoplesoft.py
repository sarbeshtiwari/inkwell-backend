"""PeopleSoft Candidate Gateway career sites (universities and public bodies: Western, UC campuses…). The job search
page lists the first jobs in plain HTML. Board address: <host>/<site>. Example: recruit.uwo.ca/hrprdwebER"""
import re
from datetime import datetime
from urllib.parse import quote

from features.ai_jobs.boards._util import get_text, plain

NAME, KIND, REGIONS = "PeopleSoft", "company", ["global"]
PAGE = "/EMPLOYEE/HRMS/c/HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL"


def _by_id(html, el_id):
    m = re.search(rf"""id=['"]{re.escape(el_id)}['"][^>]*>(.*?)</""", html, re.S)
    return plain(m.group(1)) if m else ""


def fetch(slug):
    m = re.fullmatch(r"([A-Za-z0-9.-]+\.[A-Za-z]{2,})/([\w-]+)", (slug or "").strip("/"))
    if not m:
        return []
    host, site = m.group(1).lower(), m.group(2)  # the site segment is case-sensitive
    base = f"https://{host}/psc/{site}{PAGE}"
    html = get_text(base, params={"Page": "HRS_APP_SCHJOB_FL", "Action": "U", "FOCUS": "Applicant", "SiteId": "1"})
    out = []
    for idx in sorted({int(i) for i in re.findall(r"SCH_JOB_TITLE\$(\d+)", html)}):
        jid, title = _by_id(html, f"HRS_APP_JBSCH_I_HRS_JOB_OPENING_ID${idx}"), _by_id(html, f"SCH_JOB_TITLE${idx}")
        if not jid or not title:
            continue
        posted = ""
        for key in (f"SCH_OPENED${idx}", f"HRS_APP_JBSCH_I_HRS_JO_PST_OPN_DT${idx}"):
            try:
                posted = datetime.strptime(_by_id(html, key), "%m/%d/%Y").date().isoformat()
                break
            except ValueError:
                continue
        dept = next((v for v in (_by_id(html, f"{k}${idx}") for k in ("HRS_APP_JBSCH_I_HRS_DEPT_DESCR", "WHR_JO_POST_WRK_WHR_DEPT_DESCR")) if v), "")
        out.append({"id": f"ps:{host.split('.')[-2]}:{site}:{jid}", "ats": "", "board": slug, "title": title,
                    "company": host.split(".")[-2].upper() if len(host.split(".")[-2]) <= 4 else host.split(".")[-2].capitalize(),
                    "location": _by_id(html, f"LOCATION${idx}") or _by_id(html, f"HRS_APP_JBSCH_I_HRS_LOCATION_DESCR${idx}"), "department": dept, "text": "", "posted": posted, "via": NAME,
                    "url": f"{base}?Page=HRS_APP_JBPST_FL&Action=U&FOCUS=Applicant&SiteId=1&PostingSeq=1&JobOpeningId={quote(jid)}"})
    return out
