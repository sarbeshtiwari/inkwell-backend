"""JOIN company boards (join.com/companies/<slug>, read via JOIN's public candidate GraphQL). Example: join"""
import re

from features.ai_jobs.boards._util import get_json, get_text, iso_day, plain

NAME, KIND, REGIONS = "JOIN", "company", ["de", "at", "ch", "global"]
QUERY = """query PublicJobsList($input: PublicJobsQueryInput!) { publicJobs(input: $input) {
  items { id title createdAt descriptionHtml remoteType city { cityName countryName } }
  pageInfo { pageCount } } }"""


def fetch(slug):
    page = get_text(f"https://join.com/companies/{slug}", allow_redirects=False)
    m = re.search(r'"company":\{"id":(\d+)', page)
    if not m:
        return []
    name = re.search(r'"company":\{"id":\d+,[^}]*?"name":"([^"]+)"', page)
    out = []
    for p in range(1, 6):
        d = get_json("https://join.com/candidate-api/graphql", method="POST", json={"query": QUERY, "variables": {
            "input": {"companyId": int(m.group(1)), "paginationPage": {"page": p, "pageSize": 100}, "filters": {}}}}) or {}
        jobs = ((d.get("data") or {}).get("publicJobs") or {})
        for j in jobs.get("items") or []:
            city = j.get("city") or {}
            where = "Remote" if j.get("remoteType") == "REMOTE" else ", ".join(filter(None, [city.get("cityName"), city.get("countryName")]))
            out.append({"id": f"join:{slug}:{j['id']}", "ats": "", "board": slug, "title": (j.get("title") or "").strip(),
                        "company": name.group(1) if name else slug.capitalize(), "location": where,
                        "url": f"https://join.com/companies/{slug}/{j['id']}", "text": plain(j.get("descriptionHtml", "")),
                        "posted": iso_day(j.get("createdAt")), "via": NAME})
        if p >= ((jobs.get("pageInfo") or {}).get("pageCount") or 0):
            break
    return out
