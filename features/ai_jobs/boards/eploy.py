"""Eploy careers sites, from their /live-jobs.xml sitemap. Slug: the careers host, or <tenant> for <tenant>.eploy.net. Example: careers.allpay.net"""
import re
from urllib.parse import unquote

from features.ai_jobs.boards._util import get_text, unescape

NAME, KIND, REGIONS = "Eploy", "company", ["gb"]
GENERIC = {"careers", "career", "jobs", "www", "recruitment", "vacancies"}
SMALL = {"ai": "AI", "hr": "HR", "it": "IT", "qa": "QA", "uk": "UK", "ux": "UX", "ui": "UI", "ml": "ML", "api": "API"}


def _title(path_slug):
    parts = unquote(path_slug).split("--")
    return " - ".join(" ".join(SMALL.get(w.lower(), w[:1].upper() + w[1:]) for w in re.split(r"[-_]+", p) if w) for p in parts if p.strip("-_"))


def fetch(slug):
    host = (slug or "").strip().lower()
    host = host if "." in host else f"{host}.eploy.net"
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", host):
        return []
    company = next((w for w in host.split(".") if w not in GENERIC), host).capitalize()
    out, seen = [], set()
    for loc in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", get_text(f"https://{host}/live-jobs.xml"))[:500]:
        m = re.match(r"https://[^/]+/vacancies/(\d+)/([^/?#]+)\.html/?$", unescape(loc), re.I)
        if not m or m[1] in seen:
            continue
        seen.add(m[1])
        out.append({"id": f"eploy:{host}:{m[1]}", "ats": "", "board": host, "title": _title(m[2]),
                    "company": company, "location": "",
                    "url": unescape(loc), "text": "", "posted": "", "via": NAME})
    return out
