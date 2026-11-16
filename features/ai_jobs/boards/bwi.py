"""BWI (bwi.de), the German Armed Forces' IT company: its open positions, read from each posting's JobPosting data."""
import re
from concurrent.futures import ThreadPoolExecutor

from features.ai_jobs.boards._util import get_text, title_matches

NAME, KIND, REGIONS = "BWI", "search", ["de"]


def _title(url):
    slug = re.sub(r"-(?:m-w-d|m-d-w|w-m-d|d-m-w)?-?\d{4,6}$", "", url.rstrip("/").rsplit("/", 1)[-1])
    return slug.replace("-", " ")


def fetch(role):
    from features.ai_jobs import sitejobs
    html = get_text("https://www.bwi.de/karriere/stellenangebote")
    urls = list(dict.fromkeys(re.findall(r'"url":\s*"(https://www\.bwi\.de/karriere/stellenangebote/job/[^"]+)"', html)))
    urls = [u for u in urls if title_matches(_title(u), role)][:30]

    def one(u):
        jobs = sitejobs.jobs_in(get_text(u), u, "bwi.de")
        return {**jobs[0], "id": "bwi:" + u.rstrip("/").rsplit("-", 1)[-1], "url": u, "company": "BWI", "via": NAME} if jobs else None
    with ThreadPoolExecutor(max_workers=6) as pool:
        return [j for j in pool.map(one, urls) if j]
