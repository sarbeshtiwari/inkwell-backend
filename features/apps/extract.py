"""Reading company, role and job ID out of an email (including Naukri digests)."""

from html.parser import HTMLParser
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from features.apps.common import C
from features.apps.noise import PORTALS, ROLE_WORDS, SENDER_WORDS, good_company, good_role, norm_company, tidy


HTML_MAX = 200_000  # characters of email HTML we ever look at


class _Text(HTMLParser):
    """Linear-time HTML → text (no regex backtracking, so a hostile email can't stall the server)."""
    BREAKS = {"br", "p", "div", "tr", "td", "li", "a", "table", "span", "h1", "h2", "h3", "h4", "h5", "h6"}
    SKIP = {"script", "style", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "br":
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BREAKS:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html):
    p = _Text()
    try:
        p.feed(str(html or "")[:HTML_MAX])
        p.close()
    except Exception:
        pass
    return " ".join(p.out).replace(" \n ", "\n")


def body_text(msg):
    plain = html = ""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        ctype = part.get_content_type()
        try:
            content = part.get_content()
        except (LookupError, UnicodeError, AssertionError):
            continue
        if ctype == "text/plain" and not plain:
            plain = content
        elif ctype == "text/html" and not html:
            html = content
    # Plain parts are sometimes a one-line stub ("view this email in a browser"); prefer the HTML then.
    text = plain if len(plain.strip()) > 200 or not html else html_to_text(html)
    text = re.sub(r"[​-‏⁠﻿\xa0\t ]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()


def calendar_start(msg):
    """DTSTART of an attached calendar invite, as a naive local datetime."""
    for part in msg.walk():
        if part.get_content_type() in ("text/calendar", "application/ics"):
            try:
                ics = part.get_content()
            except Exception:
                ics = (part.get_payload(decode=True) or b"").decode("utf-8", "ignore")
            m = re.search(r"DTSTART(?:;TZID=([^:;]+))?(?:;VALUE=DATE-TIME)?:(\d{8}T\d{4,6})(Z?)", ics or "")
            if not m:
                continue
            try:
                dt = datetime.strptime(m.group(2)[:13], "%Y%m%dT%H%M")
                if m.group(3) == "Z":
                    dt = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone().replace(tzinfo=None)
                elif m.group(1):
                    dt = dt.replace(tzinfo=ZoneInfo(m.group(1).strip('"'))).astimezone().replace(tzinfo=None)
                return dt
            except Exception:
                return None
    return None


SUBJ_COMPANY = [
    re.compile(r"(?:applying|applied|application|apply|interest|interested|candidature|candidacy|journey|career opportunity)"
               r"\s+(?:to|at|with|in)\s+(?!the\b|our\b|us\b|a\b|an\b)([A-Z][\w&.,'’ -]{1,48}?)(?:\s*[!.|:,–—-]|\s+for\b|\s+-\s|$)"),
    re.compile(r"\bat\s+([A-Z][\w&.'’ -]{1,40}?)\s*[!.]?$"),
    re.compile(r"\bwith\s+([A-Z][\w&.'’ -]{1,40}?)\s*[!.]?$"),
    re.compile(r"^(?:\[)?([A-Z][\w&.'’ -]{1,32}?)\]?\s*(?::|\||::|–|—)\s"),
    re.compile(r"\bYour\s+([A-Z][\w&.'’ -]{1,30}?)\s+(?:Job\s+)?Application\b"),
    re.compile(r"^([A-Z][\w&.'’ -]{1,30}?)\s+(?:Recruiting|Hiring|Careers?)\s+Update\b"),
    re.compile(r"\bfrom\s+([A-Z][\w&.'’() -]{1,40}?)\s+on your\b"),
    re.compile(r"\bat\s+([A-Z][\w&.'’ -]{1,40}?)\s+(?:has|is|was)\b"),
    re.compile(r"^([A-Z][\w&.'’ -]{1,40}?)\s+(?:has\s+)?received your application\b"),
]


BODY_COMPANY = [
    re.compile(r"(?:position|role|opening|opportunit(?:y|ies)|job)\s+(?:of\s+[^\n.]{2,70}?\s+)?(?:at|with)\s+"
               r"([A-Z][\w&.,'’ -]{1,50}?)(?:\.\s|[.,!\n]|\s+and\s|\s+in\s|\s+we\s)"),
    re.compile(r"(?:interest in|applying (?:to|at|with)|considering|application (?:to|with))\s+(?:joining\s+)?(?:the\s+)?"
               r"([A-Z][\w&.,'’ -]{1,50}?)(?:\.\s|[.,!\n]|\s+and\s|\s+as\s|\s+for\s|\s+in\s)"),
    re.compile(r"\n([A-Z][\w&.'’ -]{1,40}?)\s+(?:Talent Acquisition|Recruiting|Recruitment|Careers|HR|Hiring) Team\b"),
]


SUBJ_ROLE = [
    re.compile(r"(?:for|to)\s+(?:the\s+)?(?:position of\s+|role of\s+)?(.{3,80}?)\s+(?:position|role|opening|opportunity|job)?\s*"
               r"(?:at|with|in)\s+[A-Z]", re.I),
    re.compile(r"^(?:Re:\s*)?(.{3,70}?)\s+-\s+Application", re.I),
    re.compile(r"(?:interest|applying|application|update)\s*[-–—:]\s*(.{3,80}?)$", re.I),
    re.compile(r"(?:interview|scheduled)\s*[-–—:]\s*(?:[A-Z][\w .]+\s[-–—]\s)?(.{3,80}?)(?:\s+at\s|$)", re.I),
    re.compile(r"\bfor\s+(?:the\s+)?(?:position of\s+|role of\s+|post of\s+)?(.{3,80}?)\s*(?:[!.]|$)", re.I),
    re.compile(r"\((.{3,60}?)\)\s*@"),
]


BODY_ROLE = [
    re.compile(r"(?:position|role|post) of\s+([^\n.]{3,80}?)(?:\s+(?:at|with|in|and|which|that)\s|[.\n,])", re.I),
    re.compile(r"for the\s+(?:R\d+\s+|JR\d+\s+)?([^\n.]{3,80}?)\s+(?:position|role|opening|job)\b", re.I),
    re.compile(r"(?:application|applying) for\s+(?:the\s+)?(?:R\d+\s+|JR\d+\s+)?([^\n.]{3,80}?)\s+(?:position|role|at|with)\b", re.I),
    re.compile(r"(?:applying|applied) for\s+([^\n.]{3,80}?)\s+position\b", re.I),
    re.compile(r"Ref:\s*\d+\s*-\s*([^\n]{3,60})"),
    re.compile(r"(?:Job Title|Position|Role|Designation)\s*[:\-]\s*([^\n]{3,80})", re.I),
]


JOB_ID = re.compile(r"\b(?:job\s*(?:id|number|no\.?|code|req(?:uisition)?)|req(?:uisition)?\s*(?:id|#|no\.?|number)?|"
                    r"ref(?:erence)?(?:\s*(?:id|no\.?|number))?|posting\s*(?:id|number))\s*[:#-]?\s*"
                    r"([A-Z]{0,5}[-_]?\d{3,}[\w-]{0,12})", re.I)


JOB_ID2 = re.compile(r"\b((?:JR|REQ|R|JOB)[-_]?\d{5,}(?:[-_]\d+)?)\b")


def first_match(patterns, text, check):
    for rx in patterns:
        for m in rx.finditer(text):
            v = check(m.group(1))
            if v:
                return v
    return ""


COMPANYISH = re.compile(r"\b(?:team|hr|careers?|recruit\w*|talent|group|inc|ltd|llp|llc|pvt|corp\w*|technolog\w*|solutions|"
                        r"global|labs?|services|systems|software|security|digital|consulting|capital|bank|financial|"
                        r"networks?|communications|healthcare|health|motors|industries|international|ai|tech|"
                        r"workday|notifications?|people|acquisition|resourcing|university|academy)\b", re.I)


GENERIC_LOCAL = re.compile(r"^(?:no-?reply|do-?not-?reply|donotreply|noreply|contact|support|info|hello|hr|careers?|jobs|"
                           r"talent|recruit\w*|hiring|team|mail|admin|notifications?|wcm)\b", re.I)


def looks_like_person(name, local, domain, portal):
    """'Pragati <pragati.k@…>', 'Chamoli, Lavanya', 'Shreya Panchal <careers@betterworks…>' are people, not companies."""
    name = re.sub(r"\s*\(.*?\)|\s+from\s+.*$|\s*<.*$", "", name or "").strip().strip('"')
    words = [w for w in re.split(r"[\s,]+", name) if w]
    if not (1 <= len(words) <= 3) or not all(re.fullmatch(r"[A-Za-z][A-Za-z.'’-]*", w) for w in words):
        return False
    if COMPANYISH.search(name) or ROLE_WORDS.search(name):
        return False
    tokens = [w.lower().strip(".") for w in words if len(w.strip(".")) >= 3]
    bare_local, bare_domain = re.sub(r"[^a-z]", "", local), re.sub(r"[^a-z]", "", domain)
    if tokens and bare_local == "".join(tokens):
        return not portal  # 'gevernova@myworkday.com' is a Workday tenant, 'pragati@x.com' a person
    if any(t in bare_domain for t in tokens):
        return False  # 'Visa <careers@visa.com>'
    if portal:
        return not re.match(r"no-?reply|do-?not-?reply|donotreply", local) and (
            bool(GENERIC_LOCAL.match(local)) or any(t in bare_local for t in tokens))
    return True


def company_from_sender(name, addr):
    local, _, domain = addr.lower().partition("@")
    portal = next((label for d, label in PORTALS.items() if domain == d or domain.endswith("." + d)), "")
    tenant = ""
    if portal and domain.endswith(("myworkday.com", "icims.com")):
        t = re.sub(r"[+_.-].*", "", local)
        if t and not re.match(r"no-?reply|donotreply|workday", t):
            tenant = good_company(re.sub(r"\d+$", "", t))
    if re.search(r"\bfrom\s+([A-Za-z][\w&. -]{1,30})$", name or ""):  # "Zara from micro1"
        return good_company(re.search(r"\bfrom\s+(.+)$", name).group(1)), portal, tenant
    if looks_like_person(name, local, domain, portal):
        return "", portal, tenant
    raw = re.sub(r"\s*<.*", "", name or "")
    raw = re.sub(r"[_]+", " ", raw)
    raw = re.sub(r"(?<=[A-Za-z])(?=(?:Recruit\w*|Talent|Careers?|Workday|Notifications?|Hiring|Touch|Jobs)\b)", " ", raw)
    raw = re.sub(r"\b(?:TA|in Touch|ITM|attraction and acquisition|auto ?notification|system)\b", " ", raw, flags=re.I)
    words = []
    for w in SENDER_WORDS.sub(" ", raw).split():
        if not words or w.lower() != words[-1].lower():
            words.append(w)
    display = good_company(" ".join(words))
    if display and display.lower() not in {p.lower() for p in PORTALS.values()}:
        return display, portal, tenant
    return "", portal, tenant


def company_from_domain(addr):
    domain = addr.lower().partition("@")[2]
    if not domain or domain in C().PERSONAL_DOMAINS or any(domain == d or domain.endswith("." + d) for d in PORTALS):
        return ""
    return good_company(C().company_from_domain(re.sub(r"^(?:(?:mail|email|hr|careers?|jobs|talent|people|notifications?|"
                                                         r"recruit\w*|qazmail|messages?|e|m)\.)+", "", domain)))


def extract(subject, sender_name, sender_addr, body, me=()):
    subj = tidy(re.sub(r"^(?:(?:re|fw|fwd|reg)\s*:\s*)+", "", subject, flags=re.I), 200)
    from_company, portal, tenant = company_from_sender(sender_name, sender_addr)
    company = (from_company or first_match(SUBJ_COMPANY, subj, good_company)
               or first_match(BODY_COMPANY, body[:2500], good_company) or tenant or company_from_domain(sender_addr))
    check = lambda r: good_role(r, me)  # noqa: E731
    role = first_match(SUBJ_ROLE, subj, check) or first_match(BODY_ROLE, body[:3000], check)
    if role and company and norm_company(role) == norm_company(company):
        role = ""
    jid = ""
    for text in (subj, body[:4000]):
        m = JOB_ID.search(text) or JOB_ID2.search(text)
        if m:
            jid = m.group(1).strip("-_")
            break
    return {"company": company, "role": role, "job_id": jid, "portal": portal}


def snippet(body, phrase):
    if not phrase:
        return tidy(body[:220], 220)
    i = body.lower().find(phrase.lower()[:40])
    if i < 0:
        return tidy(body[:220], 220)
    start = max(body.rfind("\n", 0, i), body.rfind(". ", 0, i) + 1, i - 160)
    return tidy(body[start:i + 220], 240)


# ---- Naukri emails list several jobs in one message

def naukri_events(subject, body, when):
    events = []
    if re.search(r"you applied for \d+ jobs?", subject, re.I):
        block = re.search(r"Applied on [^\n]+\n(.*?)(?:\nTrack applications|\nSimilar jobs)", body, re.S)
        lines = [ln.strip() for ln in (block.group(1).split("\n") if block else []) if ln.strip()]
        piped = [ln for ln in lines if ln.count("|") >= 1]
        pairs = ([(ln.split("|")[1], ln.split("|")[0]) for ln in piped] if piped
                 else [(lines[i], lines[i + 1]) for i in range(0, len(lines) - 1, 2)])
        for role, comp in pairs:
            role, comp = good_role(role), good_company(re.sub(r"\s*\(.*$", "", comp))
            if role and comp:
                events.append({"company": comp, "role": role, "status": "applied", "phrase": "Applied on Naukri"})
    elif re.search(r"status of your job application", subject, re.I):
        seen = set()
        for m in re.finditer(r"\n([^\n|]{2,60})\|\s*([^\n|]{3,90})(?:\|[^\n]*)?\n", body):
            comp, role = good_company(m.group(1)), good_role(m.group(2))
            if comp and role and (comp, role) not in seen:
                seen.add((comp, role))
                events.append({"company": comp, "role": role, "status": "in_review",
                               "phrase": "Recruiter activity on your Naukri application"})
    return events
