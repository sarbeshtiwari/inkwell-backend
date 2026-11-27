"""Job-board recognition: which public board a careers link (or a company name) belongs to, and reading it.
Used by Companies near me and by careers-site discovery."""

import re
from concurrent.futures import ThreadPoolExecutor


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
