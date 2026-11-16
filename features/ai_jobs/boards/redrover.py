"""Red Rover K12: school district job boards (jobs.redroverk12.com/org/<id>), public API. Board address: the org id. Example: 1651"""
import re

from features.ai_jobs.boards._util import get_json, iso_day

NAME, KIND, REGIONS = "Red Rover K12", "company", ["us"]
QUERY = """query GetJobPostings($search: JobPostingSearchInput!) { jobSeekerSiteUnauthenticated { jobPostingSearch(search: $search) {
  results { id name organizationName location { name } activePublicOnDateUtc closedOnDateUtc allowsRemote } hasMoreData } } }"""


def fetch(slug):
    org = (re.search(r"(\d+)\s*$", str(slug or "")) or [None, ""])[1]
    if not org:
        return []
    d = get_json("https://api.redroverk12.com/graphql", method="POST", headers={"Content-Type": "application/json"},
                 json={"query": QUERY, "variables": {"search": {"orgId": int(org), "searchTerm": "", "locationIds": [], "jobPostingCategoryIds": []}}}) or {}
    rows = ((((d.get("data") or {}).get("jobSeekerSiteUnauthenticated") or {}).get("jobPostingSearch") or {}).get("results")) or []
    return [{"id": f"rr:{j['id']}", "ats": "", "board": org, "title": j["name"], "company": j.get("organizationName", ""),
             "location": ", ".join(filter(None, [(j.get("location") or {}).get("name"), "Remote" if j.get("allowsRemote") else ""])),
             "url": f"https://jobs.redroverk12.com/org/{org}/opening/{j['id']}", "text": "", "posted": iso_day(j.get("activePublicOnDateUtc")),
             "via": NAME} for j in rows if j.get("id") and j.get("name") and j.get("activePublicOnDateUtc")]
