"""Applying on a company's own careers site (not just Greenhouse, Lever and Ashby).

From the job page Reachout finds the application form the way a person would: a form already on the page, one
embedded in a frame, or the "Apply" button or link that leads to it (on the company's site or the job board it
uses). Sites that make you create an account first (Workday, iCIMS, Taleo…) are left to you in Watch mode,
where you sign in yourself. It never gets round CAPTCHAs or bot checks.

It also learns per site: which "Apply" button worked, and which sites always need you, so the next job on the
same site goes faster (or isn't attempted in the background at all)."""

import re
import time
from urllib.parse import urljoin, urlsplit

NATIVE = ("greenhouse", "lever", "ashby")
# Applicant systems that need an account before the form: only in Watch mode, where the user signs in themselves.
ACCOUNT_SITES = re.compile(r"myworkdayjobs\.com|myworkdaysite\.com|successfactors\.|\.icims\.com|taleo\.net|oraclecloud\.com|"
                           r"avature\.net|eightfold\.ai|csod\.com|\.adp\.com|peoplesoft|brassring|kenexa|ultipro|paylocity|"
                           r"workforcenow|jobs\.sap\.com|careers\.google\.com|amazon\.jobs|jobs\.apple\.com", re.I)
# Sites Reachout never applies on (logins it won't handle, or job sites with their own rules on automation).
NEVER = re.compile(r"linkedin\.com|naukri\.com|indeed\.|glassdoor\.|monster\.|foundit\.|instahyre\.|wellfound\.com|angel\.co|hirist\.|cutshort\.io|ycombinator\.com", re.I)
APPLY_TEXT = re.compile(r"^\W*(apply|apply now|apply here|apply online|apply for (this|the) (job|position|role|opening)|"
                        r"apply to (this|the) (job|position|role)|easy apply|quick apply|i'?m interested|start (your )?application|"
                        r"submit (your )?(application|cv|resume)|bewerben|jetzt bewerben|postuler|postular|candidatar(-se)?|"
                        r"solliciteer|aplicar)\W*$", re.I)
FORM_READY = "input[type=file], input[type=email]:visible"


def mode(job):
    """How a job can be applied to: 'board' (Greenhouse/Lever/Ashby form), 'site' (the company's own site, tried),
    'account' (needs an account: Watch mode only) or '' (not by Reachout)."""
    url = job.get("apply_url") or job.get("url") or ""
    if job.get("ats") in NATIVE:
        return "board"
    if not url.startswith("https://") or NEVER.search(url):
        return ""
    return "account" if ACCOUNT_SITES.search(url) else "site"


def host(url):
    return (urlsplit(url or "").hostname or "").removeprefix("www.")


def memory(ws, url):
    return (ws.load("ai_site_memory", {}) or {}).get(host(url), {})


def remember(ws, url, outcome, button=""):
    """Keep what happened on this site: applied / needs_account / no_form / needs_you / blocked, and the button that worked."""
    h = host(url)
    if not h:
        return
    with ws.lock:
        mem = ws.load("ai_site_memory", {}) or {}
        row = mem.get(h) or {"outcomes": {}}
        row["outcomes"][outcome] = row["outcomes"].get(outcome, 0) + 1
        row["last"], row["at"] = outcome, time.time()
        if button:
            row["button"] = button[:60]
        mem[h] = row
        if len(mem) > 500:
            mem = dict(sorted(mem.items(), key=lambda kv: -kv[1].get("at", 0))[:500])
        ws.save("ai_site_memory", mem)


def known_account_site(ws, url):
    """This site asked for an account the last two times: don't try it in the background."""
    o = memory(ws, url).get("outcomes", {})
    return o.get("needs_account", 0) >= 2 and not o.get("applied")


def has_form(page):
    try:
        return page.locator(FORM_READY).count() > 0 and page.locator("input:visible, textarea:visible").count() >= 2
    except Exception:
        return False


def needs_account(page):
    try:
        if page.locator("input[type=password]:visible").count():
            return True
        body = page.locator("body").inner_text(timeout=3000)[:4000]
    except Exception:
        return False
    return not page.locator("input[type=file]").count() and bool(re.search(
        r"\b(sign in|log ?in) to (apply|continue)|create (an |your )?account (to|and) (apply|continue)|register to apply", body, re.I))


def _public(url):
    from features.finder import host_is_public
    return url.startswith("https://") and host_is_public(urlsplit(url).hostname or "") and not NEVER.search(url)


def frame_form(page):
    """The URL of an application form inside a frame on the page (an embedded Greenhouse form and the like)."""
    for f in page.frames[1:]:
        try:
            if f.url.startswith("https://") and f.locator("input[type=file]").count():
                return f.url
        except Exception:
            continue
    return ""


def apply_controls(page, remembered=""):
    """'Apply' buttons and links on the page, the one that worked on this site before first."""
    found = []
    for role in ("link", "button"):
        try:
            loc = page.get_by_role(role, name=APPLY_TEXT)
            for i in range(min(loc.count(), 6)):
                el = loc.nth(i)
                if el.is_visible():
                    found.append((el, (el.inner_text(timeout=1000) or "").strip()))
        except Exception:
            continue
    if remembered:
        found.sort(key=lambda x: x[1].lower() != remembered.lower())
    return found


def find_form(page, live, ws=None, steps=3):
    """Walk from a job page to its application form. Returns ("form", button text) when one is open,
    ("account", "") when the site wants an account first, ("none", "") when no way to apply was found."""
    remembered = memory(ws, page.url).get("button", "") if ws else ""
    used = ""
    for _ in range(steps):
        if has_form(page):
            return "form", used
        if needs_account(page) or ACCOUNT_SITES.search(page.url):
            return "account", used
        src = frame_form(page)
        if src and _public(src):
            live(page, "Opening the application form")
            page.goto(src, timeout=45000, wait_until="domcontentloaded")
            _settle(page)
            continue
        controls = apply_controls(page, remembered)
        if not controls:
            return "none", used
        el, text = controls[0]
        href = ""
        try:
            href = el.get_attribute("href") or ""
        except Exception:
            pass
        target = urljoin(page.url, href) if href and not href.startswith(("#", "javascript:")) else ""
        if target and ACCOUNT_SITES.search(target):
            return "account", text
        if target and not _public(target):
            return "none", used
        live(page, f"Pressing “{text or 'Apply'}”")
        used = text
        try:
            if target:
                page.goto(target, timeout=45000, wait_until="domcontentloaded")
            else:
                el.click(timeout=8000)
        except Exception:
            return "none", used
        _settle(page)
    return ("form", used) if has_form(page) else ("account", used) if needs_account(page) else ("none", used)


def _settle(page):
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(1200)
