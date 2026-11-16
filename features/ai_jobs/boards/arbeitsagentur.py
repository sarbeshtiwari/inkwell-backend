"""Bundesagentur für Arbeit Jobsuche (Germany's federal job board), public API with its published client key."""
from urllib.parse import quote

from features.ai_jobs.boards._util import get_json, iso_day

NAME, KIND, REGIONS = "Arbeitsagentur", "search", ["de", "at"]


def fetch(role):
    out = []
    for page in range(1, 4):
        d = get_json("https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs",
                     params={"was": role, "size": 50, "page": page, "angebotsart": 1, "veroeffentlichtseit": 30},
                     headers={"X-API-Key": "jobboerse-jobsuche"}) or {}
        rows = d.get("ergebnisliste") or []
        for j in rows:
            ref, title = j.get("referenznummer"), j.get("stellenangebotsTitel")
            if not ref or not title:
                continue
            addr = ((j.get("stellenlokationen") or [{}])[0].get("adresse") or {})
            out.append({"id": f"ba:{ref}", "ats": "", "board": "arbeitsagentur", "title": title, "company": j.get("firma", ""),
                        "location": ", ".join(filter(None, [addr.get("ort"), (addr.get("land") or "").title()])),
                        "url": "https://www.arbeitsagentur.de/jobsuche/jobdetail/" + quote(ref, safe=""), "text": "",
                        "posted": iso_day((j.get("veroeffentlichungszeitraum") or {}).get("von")), "via": NAME})
        if len(rows) < 50:
            break
    return out[:150]
