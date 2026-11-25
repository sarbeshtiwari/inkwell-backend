"""Jobs from any company's own careers website (career-ops' scan of a careers URL / browser-extract).

1. The page often embeds or links a job board Inkwell already reads (Greenhouse iframe, Lever links, Workday…):
   that board is used, so its jobs come with full descriptions (and Greenhouse/Lever/Ashby can be auto-applied).
2. Otherwise the jobs are read from the page itself: schema.org JobPosting data (what careers pages publish for
   Google's job search), on the careers page and on the job pages it links to.
Public pages only; every hop (redirects included) must stay on the public internet."""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

import requests

from features.ai_jobs.common import UA, plain
from features.ai_jobs.providers import iso_day

HOST_GUESSES = ("successfactors", "phenom", "radancy", "eploy", "consider", "wp_job_manager")
MAX_PAGES = 40          # job pages read per careers site
MAX_BYTES = 3_000_000
NOT_JOB = re.compile(r"\.(pdf|jpe?g|png|svg|gif|css|js|zip|docx?)(\?|$)|/(blog|news|press|privacy|terms|cookie|login|signin|"
                     r"sign-in|register|account|category|tag|about|contact|faq|benefits|culture|life|events?|life-at[\w-]*|[\w-]*diversity[\w-]*|[\w-]*inclusion[\w-]*|learning[\w-]*|values|how-we-hire|our-story|perks|locations?|offices?)(/|$)", re.I)
JOB_TYPES = {"JobPosting"}


def public(url):
    from features.finder import host_is_public
    host = urlsplit(url or "").hostname or ""
    return urlsplit(url).scheme in ("http", "https") and bool(host) and host_is_public(host)


def get_page(url, hops=5):
    """(final url, html) of a public page, following redirects only while they stay public; ("", "") otherwise."""
    for _ in range(hops):
        if not public(url):
            return "", ""
        try:
            r = requests.get(url, headers={**UA, "Accept": "text/html,application/xhtml+xml"}, timeout=12, allow_redirects=False, stream=True)
        except requests.RequestException:
            return "", ""
        if r.is_redirect and r.headers.get("location"):
            url = urljoin(url, r.headers["location"])
            r.close()
            continue
        if not r.ok or "html" not in r.headers.get("content-type", "html"):
            r.close()
            return "", ""
        body = r.raw.read(MAX_BYTES, decode_content=True)
        r.close()
        return url, body.decode(r.encoding or "utf-8", errors="replace")
    return "", ""


def ld_items(html):
    """Every JSON-LD object in the page, @graph and lists flattened."""
    out = []
    for raw in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html or "", re.S | re.I):
        try:
            data = json.loads(raw.strip().rstrip(";"))
        except ValueError:
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack += x
            elif isinstance(x, dict):
                out.append(x)
                stack += [x[k] for k in ("@graph", "itemListElement", "item") if k in x]
    return out


def _type(x):
    t = x.get("@type")
    return set(t) if isinstance(t, list) else {t}


def _where(loc):
    locs = loc if isinstance(loc, list) else [loc]
    out = []
    for l in locs:
        a = (l or {}).get("address", l) if isinstance(l, dict) else {}
        if isinstance(a, str):
            out.append(a)
        elif isinstance(a, dict):
            out.append(", ".join(filter(None, [a.get("addressLocality"), a.get("addressRegion"), a.get("addressCountry") if isinstance(a.get("addressCountry"), str)
                                               else (a.get("addressCountry") or {}).get("name")])))
    return "; ".join(dict.fromkeys(filter(None, out)))[:200]


def _salary(s):
    if not isinstance(s, dict):
        return ""
    v = s.get("value") or {}
    if isinstance(v, dict):
        lo, hi = v.get("minValue"), v.get("maxValue") or v.get("value")
        unit = v.get("unitText", "")
        rng = f"{lo}–{hi}" if lo and hi and lo != hi else str(hi or lo or "")
        return f"{s.get('currency', '')} {rng} {unit.lower()}".strip() if rng else ""
    return f"{s.get('currency', '')} {v}".strip()


def posting(x, url, site):
    """A schema.org JobPosting -> Inkwell's job shape."""
    org = x.get("hiringOrganization") or {}
    company = (org.get("name") if isinstance(org, dict) else str(org)) or site
    remote = "Remote" if str(x.get("jobLocationType", "")).upper() == "TELECOMMUTE" else ""
    link = x.get("url") if isinstance(x.get("url"), str) and x.get("url", "").startswith("http") else url
    ident = x.get("identifier")
    ident = ident.get("value") if isinstance(ident, dict) else ident
    key = re.sub(r"[^\w-]", "", str(ident or "")) or re.sub(r"[^\w-]", "-", urlsplit(link).path.strip("/"))[-60:] or "job"
    return {"id": f"site:{site}:{key}", "ats": "", "board": site, "title": plain(str(x.get("title", "")))[:200],
            "company": plain(company)[:120], "location": _where(x.get("jobLocation")) or remote, "url": link,
            "text": plain(str(x.get("description", "")))[:20000], "posted": iso_day(str(x.get("datePosted", ""))[:10]),
            "salary": _salary(x.get("baseSalary")), "closes": str(x.get("validThrough", ""))[:10], "via": "Company website"}


def jobs_in(html, url, site):
    return [posting(x, url, site) for x in ld_items(html) if _type(x) & JOB_TYPES and x.get("title")]


def anchors_of(html):
    """[(href, text)] of the plain page's links."""
    return [(h, plain(t)) for h, t in re.findall(r"""<a\b[^>]*?href\s*=\s*["']([^"'#]+)["'][^>]*>(.*?)</a>""", html or "", re.I | re.S)]


def site_of(url):
    """'careers.swiggy.in' -> 'swiggy.in' (the registrable part, roughly)."""
    parts = (urlsplit(url).hostname or "").removeprefix("www.").split(".")
    return ".".join(parts[-3:] if len(parts) > 2 and len(parts[-1]) == 2 and len(parts[-2]) <= 3 else parts[-2:])


LISTING_TEXT = re.compile(r"^(see |view |explore |browse |search |all |current |our )*(all |open |current )*(jobs?|openings?|"
                          r"positions?|roles?|vacancies|opportunities)( and teams)?\b|open (roles|positions)|job openings|"
                          r"(see|view|explore|browse|search) (all )?(open )?(jobs|roles|positions|openings)|apply now|join (us|our team)", re.I)
LISTING = re.compile(r"open[-_ ]?(positions|roles|jobs)|all[-_ ]?jobs|explore[-_ ]?jobs|jobs?[-_ ]?(search|openings|listings?|list|board)|"
                     r"openings|vacancies|careerpage|/jobs/?$|/positions/?$|/roles/?$|[\w]+-careers/?$|/careers/[\w-]+/?$|"
                     r"/careers/?$|search[-_]?jobs", re.I)
DETAIL = re.compile(r"(/|[?&](id|jobid|job_id|gh_jid|jid|req|reqid|pid)=)[\w%-]*\d{3,}", re.I)
JOB_PATH = re.compile(r"/(jobs?|positions?|roles?|openings?|careers?|vacanc\w*|job-details?|jobdetails?|listing|requisitions?)/[\w%-]{6,}", re.I)
ROLE_TEXT = re.compile(r"\b(engineer|developer|manager|analyst|designer|lead|intern(ship)?|specialist|executive|associate|scientist|"
                       r"consultant|officer|head|director|architect|administrator|admin|sales|marketing|recruiter|writer|counsel|"
                       r"accountant|technician|operator|coordinator|representative|programmer|sde|devops|sre|qa|tester|partner|"
                       r"agent|advisor|editor|product|support|success|researcher|trainee|apprentice|fellow)\b", re.I)
FILTER = re.compile(r"(^|&)(department|dept|team|teams|location|locations|category|categories|function|q|query|search|page|"
                    r"office|country|city|type|level|keyword|keywords)=", re.I)
GENERIC = re.compile(r"^(apply( now)?|learn more|read more|view( job| details| role)?|details|more|see (all|more)|explore.*|"
                     r"jobs?|careers?|open (roles|positions)|.*openings|back|next|previous|\d+)$", re.I)


def classify(base, pairs, home):
    """(listing pages, job pages {url: title}) among a page's links. home: the company's site; links to another site
    count only when their text says jobs (a hiring site like company.turbohire.co)."""
    listings, details = [], {}
    for href, text in pairs:
        u = urljoin(base, (href or "").strip()).split("#")[0]
        text = re.sub(r"\s+", " ", text or "").strip()
        if not u.startswith("http") or NOT_JOB.search(u) or re.search(r"\$\{|undefined|favicon|mailto:|javascript:", u) \
                or u.rstrip("/") == base.rstrip("/"):
            continue
        same = site_of(u) == home
        if not same and not LISTING_TEXT.search(text):
            continue
        path = urlsplit(u).path + ("?" + urlsplit(u).query if urlsplit(u).query else "")
        if FILTER.search(urlsplit(u).query):  # a filtered job list ("?department=engineering"), not a job
            if u not in [x for _, x in listings]:
                listings.append((2, u))
            continue
        if same and not GENERIC.match(text) and (DETAIL.search(path) and (ROLE_TEXT.search(text) or JOB_PATH.search(path))
                                                 or JOB_PATH.search(path) and ROLE_TEXT.search(text) and len(text) < 120):
            details.setdefault(u, text[:200])
        elif (LISTING.search(urlsplit(u).path) or LISTING_TEXT.search(text)) and u not in [x for _, x in listings]:
            strong = LISTING_TEXT.search(text) or re.search(r"open|jobs?|openings?|positions?|vacanc|search|roles", urlsplit(u).path, re.I)
            listings.append((0 if strong else 1, u))
    return [u for _, u in sorted(listings, key=lambda x: x[0])], details


def links(html, base, anchors=None):
    """Job pages a page links to: {url: link text}."""
    return classify(base, anchors if anchors is not None else anchors_of(html), site_of(base))[1]


API_BOARDS = [("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)"), ("lever", r"api\.lever\.co/v0/postings/([\w-]+)"),
              ("ashby", r"api\.ashbyhq\.com/posting-api/job-board/([\w.-]+)"),
              ("smartrecruiters", r"api\.smartrecruiters\.com/v1/companies/([\w-]+)")]


TITLE_KEYS = ("title", "jobTitle", "job_title", "name", "positionTitle", "position", "postingTitle", "t", "designation", "role")
URL_KEYS = ("url", "applyUrl", "apply_url", "jobUrl", "job_url", "absolute_url", "hostedUrl", "link", "href", "u", "detailsUrl", "canonicalUrl")
LOC_KEYS = ("location", "locationName", "location_name", "city", "l", "locations", "office", "jobLocation")
TEXT_KEYS = ("description", "descriptionPlain", "content", "summary", "jobDescription")


def _job_lists(data, depth=0):
    """Lists of dicts in a JSON reply that look like jobs (each has a title-ish and a url-ish field)."""
    if depth > 4:
        return
    if isinstance(data, list) and len(data) >= 2 and all(isinstance(x, dict) for x in data[:5]):
        sample = data[:5]
        if all(any(isinstance(x.get(k), str) and x[k].strip() for k in TITLE_KEYS) for x in sample) and \
                sum(any(isinstance(x.get(k), str) and x[k].startswith(("http", "/")) for k in URL_KEYS) for x in sample) >= len(sample) - 1:
            yield data
            return
    if isinstance(data, dict):
        for v in list(data.values())[:40]:
            yield from _job_lists(v, depth + 1)
    elif isinstance(data, list):
        for v in data[:3]:
            yield from _job_lists(v, depth + 1)


def feed_jobs(data, base, site, company):
    """Jobs from a careers page's own JSON feed (its script fetched it to draw the list)."""
    out = []
    for rows in _job_lists(data):
        for x in rows[:2000]:
            title = next((x[k] for k in TITLE_KEYS if isinstance(x.get(k), str) and x[k].strip()), "")
            link = urljoin(base, next((x[k] for k in URL_KEYS if isinstance(x.get(k), str) and x[k].startswith(("http", "/"))), ""))
            if not title or not link.startswith("https://"):
                continue
            loc = next((x[k] for k in LOC_KEYS if x.get(k)), "")
            loc = loc if isinstance(loc, str) else ", ".join(str(v.get("name", v) if isinstance(v, dict) else v) for v in loc)[:200] \
                if isinstance(loc, list) else str((loc or {}).get("name", "")) if isinstance(loc, dict) else ""
            text = next((x[k] for k in TEXT_KEYS if isinstance(x.get(k), str)), "")
            key = re.sub(r"[^\w-]", "-", urlsplit(link).path.strip("/") or urlsplit(link).query)[-60:]
            out.append({"id": f"site:{site}:{key}", "ats": "", "board": site, "title": plain(title)[:200], "company": company,
                        "location": plain(loc)[:200], "url": link.split("?trid=")[0], "text": plain(text)[:20000], "posted": "",
                        "via": "Company website"})
        if out:
            break
    return out


def feed_urls(reqs, home):
    """JSON the page fetched from the company's own site (its job list, often), most promising first."""
    own = [u for u in dict.fromkeys(reqs) if site_of(u) == home and not re.search(r"\.(js|css|png|jpe?g|svg|webp|gif|woff2?|mp4|ico)(\?|$)", u)]
    return sorted(own, key=lambda u: not re.search(r"jobs?|careers?|openings?|positions?|vacanc|requisition|posting", u, re.I))[:8]


def from_feeds(reqs, home, base, company):
    """(jobs, boards) from the JSON a rendered page fetched."""
    from features.ai_jobs.portals import from_link
    for u in feed_urls(reqs, home):
        if not public(u):
            continue
        try:
            r = requests.get(u, headers={**UA, "Accept": "application/json"}, timeout=12)
            data = r.json() if r.ok and len(r.content) < 20_000_000 else None
        except (requests.RequestException, ValueError):
            continue
        jobs = feed_jobs(data, base, (urlsplit(base).hostname or "").removeprefix("www."), company)
        if jobs:  # links into a board Inkwell reads: read the board itself (full descriptions, every job)
            boards = []
            for j in jobs[:20]:
                c = (from_link(j["url"]) or [None])[0]
                if c and c[0] not in HOST_GUESSES and c not in boards:
                    boards.append(c)
            return jobs, boards[:2]
    return [], []


def request_boards(urls):
    """Job boards a rendered page called while loading (its job list comes from them)."""
    from features.ai_jobs.portals import from_link
    found = []
    for u in urls:
        cands = [(ats, m.group(1)) for ats, pat in API_BOARDS for m in [re.search(pat, u)] if m] or from_link(u)[:1]
        found += [c for c in cands if c[0] not in HOST_GUESSES and c not in found]
    return found


def render(url):
    """(final url, html, [(href, text)], [request urls]) of a page drawn by its scripts, in a headless browser.
    Stops at bot checks (Cloudflare and the like) rather than getting round them."""
    if not public(url):
        return "", "", [], []
    try:
        from playwright.sync_api import sync_playwright
        from features.ai_jobs.common import C
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox"] if C().NO_SANDBOX else [])
            try:
                page = browser.new_page(user_agent=C().USER_AGENT)
                reqs = []
                page.on("request", lambda r: reqs.append(r.url))
                page.route("**/*", lambda route: route.abort() if not public(route.request.url) or route.request.resource_type in
                           ("image", "media", "font") else route.continue_())
                page.goto(url, timeout=30000, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                html = page.content()
                anchors = page.evaluate("() => [...document.querySelectorAll('a[href]')].map(a => [a.href, a.innerText || a.title || ''])")
                return page.url, html, anchors, reqs
            finally:
                browser.close()
    except Exception as e:
        print(f"[Inkwell] couldn't open {urlsplit(url).hostname}: {e!r}", flush=True)
        return "", "", [], []


def embedded_boards(html):
    """[(ats, slug)] of job boards the page embeds or links (Greenhouse iframe, Lever links, Workday…)."""
    from features.ai_jobs.portals import from_link
    found = []
    for u in re.findall(r"""(?:src|href|data-[\w-]+)\s*=\s*["'](https?://[^"']+)""", html or "", re.I):
        for c in from_link(u)[:1]:  # the specific match only, not the host-based guesses
            if c[0] not in HOST_GUESSES and c not in found:
                found.append(c)
    for m in re.finditer(r"Grnhse\.Settings\.boardToken\s*=\s*['\"]([\w-]+)|greenhouse\.io/embed/job_board/js\?for=([\w-]+)", html or ""):
        c = ("greenhouse", m.group(1) or m.group(2))
        if c not in found:
            found.insert(0, c)
    return found


def _pages(urls, site):
    """JobPostings and embedded boards on these pages (read in parallel)."""
    jobs, boards = [], []

    def one(u):
        f, h = get_page(u)
        return (jobs_in(h, f, site), embedded_boards(h)) if h else ([], [])
    with ThreadPoolExecutor(max_workers=8) as pool:
        for js, bs in pool.map(one, urls):
            jobs += js
            boards += [b for b in bs if b not in boards]
    return jobs, boards


MAX_RENDERS = 4
BUDGET = 60  # seconds spent looking around one site


def discover(url, rendered=True):
    """What a careers site offers: {"boards": [(ats, slug)], "jobs": [...], "url": final url}.

    Crawls like a person would: the careers page, then its job-list pages ("Open roles", "Explore jobs", a hiring
    site the company links to), up to two levels, reading each page plain first and in a headless browser when its
    scripts draw the jobs. It stops as soon as it finds a job board Inkwell reads (Greenhouse, Workday…) or the
    jobs' own JobPosting data; otherwise it reads the job pages it found, or at least their titles and links."""
    home, start, began = site_of(url), url, time.time()
    queue, seen, renders = [(url, 0)], set(), 0
    jobs, boards, details = [], [], {}
    while queue and not boards and not jobs and len(seen) < 12 and time.time() - began < BUDGET:
        page_url, depth = queue.pop(0)
        if page_url in seen:
            continue
        seen.add(page_url)
        final, html = get_page(page_url)
        site = (urlsplit(final or page_url).hostname or "").removeprefix("www.")
        pairs, reqs = anchors_of(html), []
        if html:
            jobs, boards = jobs_in(html, final, site), embedded_boards(html)
            if page_url == start:  # careers.acme.com may move to careers.acme.in
                start, home = final, site_of(final)
        listings, found = classify(final or page_url, pairs, home)
        if not (jobs or boards) and rendered and renders < MAX_RENDERS and (not found or depth == 0):
            renders += 1
            r_url, r_html, r_pairs, reqs = render(final or page_url)
            if r_html:
                jobs = jobs_in(r_html, r_url, site)
                boards = embedded_boards(r_html) + [b for b in request_boards(reqs) if b not in embedded_boards(r_html)]
                if not jobs and not boards:  # the page draws its list from its own JSON feed
                    jobs, boards = from_feeds(reqs, home, r_url, home.split(".")[0].capitalize())
                more_l, more_d = classify(r_url, r_pairs, home)
                listings += [x for x in more_l if x not in listings]
                found.update(more_d)
        if len(found) > len(details):  # the page listing the most jobs is the job list
            details = found
        if depth < 2:
            queue += [(u, depth + 1) for u in listings[:6] if u not in seen]
    if not jobs and not boards and details:
        jobs, boards = _pages(list(details)[:MAX_PAGES], (urlsplit(start).hostname or "").removeprefix("www."))
        if not jobs and not boards:  # no JobPosting data: the job links with their titles
            site = (urlsplit(start).hostname or "").removeprefix("www.")
            jobs = [{"id": f"site:{site}:" + re.sub(r"[^\w-]", "-", urlsplit(u).path.strip("/") + urlsplit(u).query)[-60:],
                     "ats": "", "board": site, "title": t, "company": home.split(".")[0].capitalize(), "location": "", "url": u,
                     "text": "", "posted": "", "via": "Company website"} for u, t in details.items() if 3 < len(t) < 120]
    uniq = {}
    for j in jobs:
        if j["title"] and j["url"]:
            uniq.setdefault(j["id"], j)
    return {"boards": boards, "jobs": list(uniq.values()), "url": start}


def fetch(url):
    """Jobs on a company's careers site (the reader behind My companies entries of kind "site"). A job board the site
    uses wins over what's on the page: it has every job, with full descriptions."""
    url = url if url.startswith("http") else "https://" + url
    d = discover(url)
    from features.ai_jobs.portals import try_boards
    hit = try_boards(d["boards"]) if d["boards"] else None
    if not hit:
        return d["jobs"]
    def key(j):
        return re.sub(r"\W", "", j["title"].lower()) + re.sub(r"\W", "", (j.get("location") or "").split(",")[0].lower())
    have = {key(j) for j in hit[2]}
    return hit[2] + [j for j in d["jobs"] if key(j) not in have]
