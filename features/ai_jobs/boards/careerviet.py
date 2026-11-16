"""CareerViet (careerviet.vn): jobs in Vietnam, from the job list its search page carries as data."""
import json
import re
from urllib.parse import quote

from features.ai_jobs.boards._util import get_text, iso_day

NAME, KIND, REGIONS = "CareerViet", "search", ["vn"]


def fetch(role):
    html = get_text("https://careerviet.vn/viec-lam/" + quote(role.strip().lower().replace(" ", "-").replace("/", "")) + "-k-vi.html")
    html = html.replace('\\"', '"').replace("\\/", "/")
    out, seen = [], set()
    for m in re.finditer(r'"job_link":"(https://careerviet\.vn/[^"]+?\.([0-9A-F]{8})\.html)"', html):
        link, jid = m.groups()
        if jid in seen:
            continue
        seen.add(jid)
        start = html.rfind('{"job_id"', 0, m.start())
        blob = html[start:m.end() + 600] if start >= 0 and m.start() - start < 6000 else html[m.start() - 3000:m.end() + 600]

        def val(k):
            v = re.search(rf'"{k}":"((?:[^"\\]|\\.)*)"', blob)
            try:
                return json.loads(f'"{v.group(1)}"') if v else ""
            except ValueError:
                return v.group(1)
        title = val("job_title") or val("job_title_en")
        if not title:
            continue
        out.append({"id": f"cv:{jid}", "ats": "", "board": "careerviet", "title": title, "company": val("emp_name") or val("job_company_name"),
                    "location": val("location_name_arr") or val("city_name") or "Vietnam", "url": link, "text": "",
                    "posted": iso_day(val("job_activedate") or val("date_view")), "salary": val("job_salary_string"),
                    "closes": val("job_last_date")[:10], "via": NAME})
    return out
