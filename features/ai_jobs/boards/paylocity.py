"""Paylocity recruiting boards (slug = the board GUID in recruiting.paylocity.com/Recruiting/Jobs/All/<guid>). Example: a432d829-f701-4cf3-9108-56ef703b2ac5"""
import json
import re

from features.ai_jobs.boards._util import get_text, iso_day

NAME, KIND, REGIONS = "Paylocity", "company", ["us"]
ORIGIN = "https://recruiting.paylocity.com/Recruiting/Jobs"


def fetch(slug):
    guid = (slug or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", guid):
        return []
    m = re.search(r"window\.pageData\s*=\s*(\{.*?\});\s*</script>", get_text(f"{ORIGIN}/All/{guid}"), re.S)
    try:
        page = json.loads(m.group(1)) if m else {}
    except ValueError:
        return []
    company = (page.get("ModuleTitle") or "").strip() or "Paylocity board"
    out = []
    for j in page.get("Jobs") or []:
        if not (j.get("JobTitle") and j.get("JobId")):
            continue
        place = j.get("JobLocation") or {}
        where = "" if j.get("ShouldDisplayLocation") is False else (
            j.get("LocationName") or ", ".join(filter(None, [place.get("City"), place.get("State")])))
        out.append({"id": f"pyl:{guid[:8]}:{j['JobId']}", "ats": "", "board": guid, "title": j["JobTitle"].strip(),
                    "company": company, "location": where, "url": f"{ORIGIN}/Details/{j['JobId']}", "text": "",
                    "posted": iso_day(j.get("PublishedDate")), "via": NAME})
    return out
