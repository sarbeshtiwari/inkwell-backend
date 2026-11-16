"""Amazon's own career site (amazon.jobs search JSON)."""
from datetime import datetime

from features.ai_jobs.boards._util import get_json, plain

NAME, KIND, REGIONS = "Amazon Jobs", "search", ["global"]
ORIGIN = "https://www.amazon.jobs"


def _day(s):
    try:
        return datetime.strptime(" ".join((s or "").split()), "%B %d, %Y").date().isoformat()
    except ValueError:
        return ""


def fetch(role):
    d = get_json(f"{ORIGIN}/en/search.json", params={"base_query": role, "loc_query": "", "sort": "relevant",
                                                       "result_limit": 100, "offset": 0}) or {}
    return [{"id": f"amzn:{j.get('id_icims') or j.get('id')}", "ats": "", "board": "amazon", "title": (j.get("title") or "").strip(),
             "company": "Amazon", "location": j.get("normalized_location") or j.get("location", ""),
             "url": ORIGIN + j["job_path"], "posted": _day(j.get("posted_date")), "via": NAME,
             "text": plain(" ".join(j.get(k) or "" for k in ("description", "basic_qualifications", "preferred_qualifications")))}
            for j in d.get("jobs", []) if j.get("job_path") and j.get("title")]
