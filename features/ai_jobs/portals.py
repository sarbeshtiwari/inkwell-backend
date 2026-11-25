"""My companies (career-ops' portals.yml): companies whose job boards every search also reads. Paste a careers
link and the board is recognised from it; or type a name and the boards that need only a name are tried."""

import re
from concurrent.futures import ThreadPoolExecutor

from flask import jsonify
from bridge import login_required

from features.ai_jobs.common import C, bp
from features.ai_jobs import boards as more_boards

MAX_PORTALS = 60

# (ats, pattern on the careers link -> the board address that ats's reader takes)
LINKS = [
    ("greenhouse", r"(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"),
    ("lever", r"jobs(?:\.eu)?\.lever\.co/([\w-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([\w.-]+)"),
    ("workable", r"apply\.workable\.com/([\w-]+)"),
    ("smartrecruiters", r"(?:careers|jobs)\.smartrecruiters\.com/([\w-]+)"),
    ("recruitee", r"([\w-]+)\.recruitee\.com"),
    ("bamboohr", r"([\w-]+)\.bamboohr\.com"),
    ("teamtailor", r"([\w-]+)\.teamtailor\.com"),
    ("breezy", r"([\w-]+)\.breezy\.hr"),
    ("personio", r"([\w-]+)\.jobs\.personio\.(?:de|com)"),
    ("pinpoint", r"([\w-]+)\.pinpointhq\.com"),
    ("homerun", r"([\w-]+)\.homerun\.co"),
    ("rippling", r"ats\.rippling\.com/([\w-]+)"),
    ("gem", r"jobs\.gem\.com/([\w-]+)"),
    ("jazzhr", r"([\w-]+)\.applytojob\.com"),
    ("jobvite", r"jobs\.jobvite\.com/([\w-]+)"),
    ("icims", r"([\w-]+)\.icims\.com"),
    ("workday", r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)"),
    ("oraclecloud", r"([\w.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[\w-]+/sites/(CX_\w+)"),
    ("eightfold", r"([\w-]+\.eightfold\.ai)"),
    ("csod", r"([\w-]+\.csod\.com)/ux/ats/careersite/(\d+)"),
    ("taleo", r"([\w-]+\.taleo\.net)/careersection/([\w-]+)"),
    ("peoplesoft", r"([\w.-]+)/psc/([\w-]+)/EMPLOYEE/HRMS/c/HRS_HRAM_FL"),
    ("deel", r"jobs\.deel\.com/([\w-]+)"),
    ("schoolspring", r"([\w-]+\.schoolspring\.com)"),
    ("redrover", r"jobs\.redroverk12\.com/org/(\d+)"),
    ("applitrack", r"applitrack\.com/([\w-]+)"),
    ("hiringroom", r"([\w-]+)\.hiringroom\.com"),
    ("telegram_channel", r"t\.me/(?:s/)?([A-Za-z0-9_]{4,64})"),
    ("teamtailor", r"careers\.([\w-]+)\.com"),  # many Teamtailor sites use careers.<company>.com; checked live below
]


def from_link(url):
    """[(ats, slug)] the link could be, most specific first."""
    out = []
    for ats, pat in LINKS:
        m = re.search(pat, url or "", re.I)
        if m:
            slug = "/".join(m.groups())
            out.append((ats, slug))
    host = re.match(r"https?://([^/?#]+)", url or "")
    if host:  # boards whose readers take the careers host itself
        for ats in ("successfactors", "phenom", "radancy", "eploy", "consider", "wp_job_manager"):
            out.append((ats, host.group(1).lower()))
    return out


def reader(ats):
    from features.ai_jobs.sources import board_jobs
    return lambda slug: board_jobs(ats, slug)


def try_boards(candidates):
    """The first (ats, slug, jobs) that answers with jobs, or None."""
    def one(c):
        try:
            return c, reader(c[0])(c[1]) or []
        except Exception:
            return c, []
    with ThreadPoolExecutor(max_workers=10) as pool:
        for (ats, slug), jobs in pool.map(one, candidates[:30]):
            if jobs:
                return ats, slug, jobs
    return None


@bp.get("/api/ai-jobs/portals")
@login_required
def list_portals(ws):
    boards = [{"id": k, "name": m.NAME, "kind": m.KIND, "regions": m.REGIONS} for k, m in more_boards.load().items()]
    boards.append({"id": "site", "name": "Company website", "kind": "company", "regions": ["global"]})
    return jsonify(portals=ws.load("ai_portals", []) or [], boards=sorted(boards, key=lambda b: b["name"].lower()))


@bp.post("/api/ai-jobs/portals")
@login_required
def add_portal(ws):
    """Body: {link} (a careers page or job link) or {name} (a company name). Checks the board live before saving it."""
    core = C()
    p = core.body()
    if core.rate_limited(("ai-portal", ws.uid), 60, 3600):
        raise core.Invalid("Too many tries. Try again in a while.", status=429)
    link = core.v_text(p.get("link"), "link", "Careers link", 400)
    name = core.v_text(p.get("name"), "name", "Company", 80)
    if link and not re.match(r"https://", link):
        raise core.Invalid("Paste the full https:// link of the company's careers page.", "link")
    from features.finder import host_is_public
    if link and not host_is_public(re.match(r"https://([^/:?#]+)", link).group(1)):
        raise core.Invalid("That link isn't on the public internet.", "link")
    if link:
        cands = from_link(link)
    elif name:
        from features.ai_jobs.sources import company_boards
        cands = company_boards(name)
    else:
        raise core.Invalid("Paste a careers link or type the company's name.", "link")
    hit = try_boards(cands)
    if not hit and link:  # the company's own careers site: a board it embeds, or the jobs published on the page itself
        from features.ai_jobs import sitejobs
        found = sitejobs.discover(link)
        hit = try_boards(found["boards"]) or (("site", found["url"], found["jobs"]) if found["jobs"] else None)
    if not hit:
        raise core.Invalid("Couldn't find open jobs there. Try the link of the company's careers page that lists the jobs, "
                           "or of one job on it.", "link" if link else "name")
    ats, slug, jobs = hit
    company = name or next((j.get("company") for j in jobs if j.get("company")), slug)
    entry = {"ats": ats, "slug": slug, "name": company[:80], "jobs": len(jobs)}

    def change(rows):  # applied to the latest list (versioned record: no account lock)
        rows = [r for r in (rows or []) if (r["ats"], r["slug"]) != (ats, slug)]
        if len(rows) >= MAX_PORTALS:
            raise core.Invalid(f"You can keep up to {MAX_PORTALS} companies.")
        return rows + [entry]

    rows = ws.update("ai_portals", change, default=[])
    return jsonify(portals=rows, added={"ats": ats, "slug": slug, "name": company, "jobs": len(jobs)})


@bp.delete("/api/ai-jobs/portals/<ats>/<path:slug>")
@login_required
def remove_portal(ws, ats, slug):
    rows = ws.update("ai_portals", lambda rows: [r for r in (rows or []) if (r["ats"], r["slug"]) != (ats, slug)], default=[])
    return jsonify(portals=rows)
