"""JobStreet and JobsDB (SEEK Asia): Indonesia, Malaysia, Singapore, Philippines, Hong Kong, Thailand. Public search API."""
from features.ai_jobs.boards._util import get_json, iso_day

NAME, KIND, REGIONS = "JobStreet / JobsDB", "search", ["id", "my", "sg", "ph", "hk", "th"]
SITES = {"id": ("id.jobstreet.com", "ID-Main"), "my": ("my.jobstreet.com", "MY-Main"), "sg": ("sg.jobstreet.com", "SG-Main"),
         "ph": ("ph.jobstreet.com", "PH-Main"), "hk": ("hk.jobsdb.com", "HK-Main"), "th": ("th.jobsdb.com", "TH-Main")}


def fetch(role, countries=("sg",)):
    out = []
    for c in list(countries or ["sg"])[:2]:
        host, key = SITES.get(c, SITES["sg"])
        for page in (1, 2):
            d = get_json(f"https://{host}/api/jobsearch/v5/search", params={"siteKey": key, "keywords": role, "pageSize": 50, "page": page}) or {}
            rows = d.get("data") or []
            for j in rows:
                if not j.get("id") or not j.get("title"):
                    continue
                where = ", ".join(x.get("label", "") for x in j.get("locations") or [])
                out.append({"id": f"js:{c}:{j['id']}", "ats": "", "board": "jobstreet", "title": j["title"],
                            "company": j.get("companyName") or (j.get("advertiser") or {}).get("description", ""), "location": where,
                            "url": f"https://{host}/job/{j['id']}", "text": " ".join([j.get("teaser", "")] + (j.get("bulletPoints") or [])),
                            "posted": iso_day(j.get("listingDate")), "salary": j.get("salaryLabel", ""), "via": "JobsDB" if "jobsdb" in host else "JobStreet"})
            if len(rows) < 50:
                break
    return out
