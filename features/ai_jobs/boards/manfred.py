"""Manfred (getmanfred.com): tech jobs in Spain with stated salary, public API."""
from features.ai_jobs.boards._util import get_json, title_matches

NAME, KIND, REGIONS = "Manfred", "search", ["es"]


def fetch(role):
    out = []
    for lang in ("EN", "ES"):
        for j in get_json("https://www.getmanfred.com/api/v2/public/offers", params={"lang": lang}) or []:
            if not (j.get("position") and j.get("slug")) or not title_matches(j["position"], role) or j.get("status") != "ACTIVE":
                continue
            if any(o["id"] == f"mf:{j['id']}" for o in out):
                continue
            locs = ", ".join(l.get("name", "") if isinstance(l, dict) else str(l) for l in j.get("locations") or [])
            remote = "Remote" if (j.get("remotePercentage") or 0) >= 100 else ""
            pay = (f"{j.get('currency', '')}{j['salaryFrom']}–{j['salaryTo']}" if j.get("salaryFrom") else f"up to {j.get('currency', '')}{j['salaryTo']}") \
                if j.get("salaryTo") else ""
            out.append({"id": f"mf:{j['id']}", "ats": "", "board": "manfred", "title": j["position"],
                        "company": (j.get("company") or {}).get("name", ""), "location": ", ".join(filter(None, [locs, remote])) or "Spain",
                        "url": f"https://www.getmanfred.com/ofertas-empleo/{j['id']}/{j['slug']}", "text": " · ".join(j.get("highlights") or []),
                        "posted": (j.get("updatedAt") or "")[:10], "salary": pay, "via": NAME})
    return out[:100]
