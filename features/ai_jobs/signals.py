"""Deterministic job checks, after career-ops: is the posting still open (liveness), ATS keyword coverage, the
six-point role-clarity check, posting-legitimacy (ghost job) signals, archetype and role summary, pay found in
the description, and the user's own rules (blacklist and dealbreakers).

No model is used here. Posting text is data: it is only matched against patterns, never followed."""

import re
import unicodedata
from datetime import datetime

import requests

from features.ai_jobs.common import J, UA, get_json


def norm_text(text):
    """Typographic quotes to ASCII, no accents, single spaces (so one pattern spelling matches every board)."""
    text = (text or "").translate(str.maketrans({"‘": "'", "’": "'", "ʼ": "'", "“": '"', "”": '"'}))
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text)


# --- liveness -----------------------------------------------------------------------------------------------

HARD_EXPIRED = re.compile("|".join([
    r"job (is )?no longer available", r"job.{0,40}no longer open", r"no longer accepting applications",
    r"\b(?:job|position|role|posting|opening|vacancy|requisition|listing)\b.{0,60}?(?<!application )(?<!form )has been filled\b(?! out)",
    r"this job has expired", r"job posting has expired", r"this (position|role|job) (is )?no longer",
    r"this (?:job|role|position)(?: listing)? is closed\b(?!-)", r"job (listing )?not found", r"\bsorry,? this job was removed\b",
    r"the page you are looking for doesn.t exist", r"applications? (?:(?:have|are|is) )?closed\b(?!-)",
    r"diese stelle (ist )?(nicht mehr|bereits) besetzt", r"offre (expiree|n'est plus disponible)"]), re.I)
LISTING_PAGE = re.compile(r"\d+\s+jobs?\s+found|search for jobs page is loaded|\bjob openings\b.{0,30}\bfilter\b", re.I)
APPLY_CONTROL = re.compile(r"\bapply\b|submit application|start application|easy apply|\bbewerben\b|\bpostuler\b", re.I)
BOT_WALL = re.compile(r"just a moment|performing security verification|checking your browser|verify you are (a |not a )?human|"
                      r"enable javascript and cookies to continue|attention required", re.I)


def ats_alive(job):
    """For a job read from a public ATS API: is it still listed there? None when the API can't tell."""
    kind, _, rest = job["id"].partition(":")
    board, _, jid = rest.partition(":")
    if kind == "gh":
        r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}", headers=UA, timeout=15)
        return r.ok if r.status_code in (200, 404) else None
    if kind == "lv":
        r = requests.get(f"https://api.lever.co/v0/postings/{board}/{jid}", headers=UA, timeout=15)
        return r.ok if r.status_code in (200, 404) else None
    if kind == "ab":
        d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
        return None if d is None else any(j.get("id") == jid for j in d.get("jobs", []))
    if kind == "wk":
        d = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{board}")
        return None if d is None else any(j.get("shortcode") == jid for j in d.get("jobs", []))
    return None


def liveness(job):
    """{"state": "active" | "expired" | "uncertain", "reason": ...}. A dead posting never gets evaluated or applied to."""
    try:
        alive = ats_alive(job)
    except requests.RequestException:
        alive = None
    if alive is True:
        return {"state": "active", "reason": "Still listed on the company's job board"}
    if alive is False:
        return {"state": "expired", "reason": "No longer listed on the company's job board"}
    try:
        r = requests.get(job["url"], headers={**UA, "Accept": "text/html"}, timeout=15, allow_redirects=True)
    except requests.RequestException:
        return {"state": "uncertain", "reason": "The job page didn't answer"}
    if r.status_code in (404, 410):
        return {"state": "expired", "reason": f"The job page is gone (HTTP {r.status_code})"}
    text = norm_text(re.sub(r"<[^>]+>", " ", r.text[:400_000]))
    if BOT_WALL.search(text[:3000]):
        return {"state": "uncertain", "reason": "The site shows a robot check, so it couldn't be read"}
    if HARD_EXPIRED.search(text):
        return {"state": "expired", "reason": "The page says the job is closed"}
    if re.search(r"[?&]error=true", r.url) or (LISTING_PAGE.search(text) and r.url.rstrip("/") != job["url"].rstrip("/")):
        return {"state": "expired", "reason": "The link now goes to a list of jobs"}
    if not r.ok:
        return {"state": "uncertain", "reason": f"The job page answered HTTP {r.status_code}"}
    if APPLY_CONTROL.search(text) and len(text) > 1500:
        return {"state": "active", "reason": "The page has the job and an Apply button"}
    return {"state": "uncertain", "reason": "The page loads but shows no Apply button (it may load later)"}


# --- ATS keyword coverage -------------------------------------------------------------------------------------

SYNONYMS = [("javascript", "js"), ("typescript", "ts"), ("kubernetes", "k8s"), ("machine learning", "ml"), ("ci/cd", "cicd"),
            ("natural language processing", "nlp"), ("large language model", "llm"), ("amazon web services", "aws"),
            ("google cloud platform", "gcp"), ("postgresql", "postgres"), ("node.js", "nodejs", "node"),
            ("react", "reactjs", "react.js"), ("rest", "restful"), ("generative ai", "genai", "gen ai")]
EXTRA_TERMS = ["REST", "GraphQL", "microservices", "CI/CD", "Agile", "Scrum", "unit testing", "system design", "LLM", "RAG",
               "LangChain", "prompt engineering", "fine-tuning", "vector database", "embeddings", "MLOps", "ETL", "data pipelines",
               "distributed systems", "observability", "A/B testing", "SaaS", "API design", "OAuth", "WebSockets", "Linux",
               "Terraform", "Kafka", "Spark", "Airflow", "Snowflake", "Tableau", "Power BI", "Figma", "accessibility"]


def term_in(term, text):
    low = text.lower()
    for group in SYNONYMS:
        if term.lower() in group:
            return any(re.search(rf"(?<![\w.]){re.escape(g)}(?![\w])", low) for g in group)
    return bool(re.search(rf"(?<![\w.]){re.escape(term.lower())}(?![\w])", low))


def keyword_coverage(jd, resume):
    """The JD's keywords (skills and common ATS terms) and which ones the resume already has, the way an ATS reads it.
    Diagnostic only: keywords get reworded on a resume, never invented."""
    jm = J()
    terms = [s for s in jm.skill_hits(jd, jm.SKILLS) if s not in ("C", "R")] + [t for t in EXTRA_TERMS if term_in(t, jd)]
    terms = list(dict.fromkeys(terms))
    covered = [t for t in terms if t in jm.skill_hits(resume, [t]) or term_in(t, resume)]
    missing = [t for t in terms if t not in covered]
    pct = round(100 * len(covered) / len(terms)) if terms else None
    return {"keywords": terms, "covered": covered, "missing": missing, "percent": pct}


# --- six-point role clarity -----------------------------------------------------------------------------------

CLARITY = [
    ("supervisor", "Who the role reports to",
     "Who would this role report to (title and department)?",
     r"\b(?:reports?|reporting) (?:directly )?to (?:the |our |a |an )?[A-Za-z]",
     r"\b(?:join|within|part of) (?:the|our) [\w&\s-]{2,40}\b(?:department|division|team|group)\b"),
    ("scope", "Scope of work", "What is in scope for this role, and what is owned by someone else?",
     r"\b(?:responsible for|accountable for|you will (?:own|lead|manage|build|design|develop|deliver|drive|oversee)|will own|own(?:ing)? the)\b",
     r"\b(?:responsibilities|what you.ll do|what you will do|the role)\b"),
    ("deliverables", "Deliverables and their format", "What are the first concrete deliverables, and by when?",
     r"\b(?:deliverables?|you will (?:produce|deliver|publish|ship|launch|build))\b.{0,80}\b(?:reports?|dashboards?|documents?|"
     r"plans?|roadmaps?|releases?|pipelines?|features?|services?|apis?|models?|systems?)\b",
     r"\b(?:deliverables?|outputs?|ship|launch)\b"),
    ("success", "How success is measured in the first 3–6 months", "How will success be measured in the first 3 to 6 months?",
     r"\b(?:first|initial) (?:30|60|90|three|six|3|6)\b|\b(?:30|60|90)[- ]day\b|\bsuccess (?:is|will be|looks like) ",
     r"\b(?:kpis?|okrs?|metrics?|targets?|measured (?:by|on))\b"),
    ("coordination", "Teams the role works with", "Which teams would this role work with day to day?",
     r"\b(?:work(?:ing)? (?:closely |cross-functionally )?with|partner(?:ing)? with|collaborat(?:e|ing) with)\b.{0,60}"
     r"\b(?:engineering|product|design|data|sales|marketing|operations|security|customers?|leadership|research)\b",
     r"\bcross-functional\b|\bcollaborat"),
    ("cadence", "Delivery cadence", "What is the delivery cadence, and which deadlines are already fixed?",
     r"\b(?:daily|weekly|bi-?weekly|monthly|quarterly|sprints?|release cycles?)\b.{0,40}\b(?:deliver\w*|release\w*|ship\w*|"
     r"updates?|reviews?|demos?|stand-?ups?|planning)\b", r"\b(?:fast-paced|deadlines?|regular(?:ly)?|iterat\w+)\b"),
]
SOFT_VERBS = [("participate in", r"\bparticipat\w* in\b"), ("support", r"\bsupport(?:s|ed|ing)? (?:the|our|with|in)\b"),
              ("assist", r"\bassist\w* (?:with|in|the)\b"), ("contribute to", r"\bcontribut\w+ to\b"),
              ("exposure to", r"\bexposure to\b"), ("other duties as assigned", r"\bother duties as (?:assigned|required)\b"),
              ("as needed", r"\bas (?:needed|required)\b")]


def sentence_with(text, m):
    start = max(text.rfind(".", 0, m.start()) + 1, m.start() - 160)
    end = text.find(".", m.end())
    return text[start:end + 1 if 0 <= end < m.end() + 160 else m.end() + 80].strip()[:240]


def role_clarity(jd):
    """Each of six facts a candidate needs before an interview: present / partial / missing, with the quoted words."""
    text = norm_text(jd)
    points = []
    for pid, label, question, strong, weak in CLARITY:
        m = re.search(strong, text, re.I)
        status = "present" if m else "missing"
        if not m:
            m = re.search(weak, text, re.I)
            status = "partial" if m else "missing"
        points.append({"id": pid, "label": label, "status": status, "evidence": sentence_with(text, m) if m else "", "question": question})
    soft = [label for label, pat in SOFT_VERBS for _ in re.finditer(pat, text, re.I)]
    missing = sum(p["status"] == "missing" for p in points)
    return {"points": points, "soft_verbs": sorted(set(soft)), "soft_heavy": len(soft) >= 3, "ask_before": missing >= 3,
            "questions": [p["question"] for p in points if p["status"] != "present"] if missing >= 3 else []}


# --- role summary, archetype, pay -----------------------------------------------------------------------------

ARCHETYPES = [  # (name, signal words) — career-ops' defaults, widened for software roles
    ("AI Platform / LLMOps", r"observability|evals?\b|llmops|mlops|model serving|inference|monitoring|reliability"),
    ("Agentic / Automation", r"\bagents?\b|agentic|orchestration|workflow automation|multi-agent|\bhitl\b|tool calling"),
    ("AI / ML Engineer", r"machine learning|deep learning|\bllms?\b|\brag\b|pytorch|tensorflow|fine-tun|embeddings|nlp|computer vision"),
    ("Technical AI PM", r"\bprd\b|roadmap|discovery|stakeholders?|product manager"),
    ("AI Solutions Architect", r"solutions? architect|enterprise|pre-?sales|customer-facing|field engineer|forward deployed"),
    ("Full Stack", r"full[ -]?stack|frontend and backend|end-to-end features"),
    ("Backend / Platform", r"backend|back-end|apis?\b|microservices|distributed systems|databases?|platform engineer"),
    ("Frontend", r"frontend|front-end|react|vue|angular|css|design systems?|ui engineer"),
    ("Data", r"data engineer|data pipelines?|etl|warehouse|analytics|\bsql\b|spark|airflow|dbt"),
    ("DevOps / SRE", r"devops|\bsre\b|site reliability|kubernetes|terraform|infrastructure|on-call"),
    ("Mobile", r"\bios\b|android|react native|flutter|swift|kotlin|mobile"),
]


def archetype(title, jd, targets=()):
    """Best-fitting archetype by signal words (title counts triple), plus whether it is one the user targets."""
    t, text = title.lower(), f"{title} {jd}".lower()
    scores = []
    for name, pat in ARCHETYPES:
        n = len(re.findall(pat, text)) + 2 * len(re.findall(pat, t))
        if n:
            scores.append((n, name))
    scores.sort(reverse=True)
    best = [s[1] for s in scores[:2] if s[0] >= max(2, scores[0][0] * 0.6)] if scores else []
    names = best or ["Unclassified"]
    wanted = [x.lower() for x in targets if x]
    hit = any(any(w in n.lower() or n.lower().split(" /")[0] in w for w in wanted) for n in names) if wanted else None
    return {"archetype": " + ".join(names), "targeted": hit}


def remote_mode(job, jd):
    text = f"{job.get('location', '')} {jd}".lower()
    if re.search(r"\b(fully remote|100% remote|remote[- ]first|work from anywhere|remote \(|^remote)", text) or job.get("location", "").lower().startswith("remote"):
        return "Remote"
    if re.search(r"\bhybrid\b|\d days? (a|per) week in (the )?office|in[- ]office \d", text):
        return "Hybrid"
    if re.search(r"\bon[- ]?site\b|in[- ]office|office-based|relocat", text):
        return "On-site"
    return "Remote" if "remote" in text[:400] else "Not stated"


def seniority(title, jd):
    t = title.lower()
    for label, pat in [("Principal / Staff", r"principal|staff|distinguished"), ("Lead / Manager", r"\blead\b|manager|head of|director"),
                       ("Senior", r"senior|\bsr\.?\b|\biii\b"), ("Junior / Entry", r"junior|\bjr\.?\b|entry|graduate|new grad|intern|fresher|associate")]:
        if re.search(pat, t):
            return label
    from features.ai_jobs.ranking import years_asked
    ask = years_asked(jd)
    if ask:
        return "Senior" if ask[0] >= 5 else "Mid-level" if ask[0] >= 2 else "Junior / Entry"
    return "Mid-level"


MONEY = re.compile(r"(?P<cur>[$€£₹]|usd|eur|gbp|inr|cad|aud)\s?(?P<lo>\d[\d,.]*)\s?(?P<lok>k|lpa|l|lakhs?|m)?\s*(?:-|–|to)\s*"
                   r"(?:[$€£₹]|usd|eur|gbp|inr|cad|aud)?\s?(?P<hi>\d[\d,.]*)\s?(?P<hik>k|lpa|l|lakhs?|m)?", re.I)


def amount(num, unit):
    """'146,200.00.' / '146.200' / '18' -> a number (a sentence's full stop or European thousands dots don't break it)."""
    num = num.strip(".,")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", num):
        num = num.replace(".", "")
    try:
        v = float(num.replace(",", ""))
    except ValueError:
        return 0.0
    unit = (unit or "").lower()
    return v * 1000 if unit == "k" else v * 100_000 if unit in ("l", "lpa", "lakh", "lakhs") else v * 1_000_000 if unit == "m" else v


def pay_range(job, jd):
    """{"text", "currency", "low", "high"} from the description (or the board's salary field); None when not stated."""
    src = f"{job.get('salary', '')} {jd}"
    m = MONEY.search(src)
    if not m:
        return {"text": job["salary"], "currency": "", "low": None, "high": None} if job.get("salary") else None
    cur = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}.get(m.group("cur"), m.group("cur").upper())
    unit = m.group("hik") or m.group("lok")
    lo, hi = amount(m.group("lo"), m.group("lok") or unit), amount(m.group("hi"), unit)
    if hi < 1000 and not unit:  # "$3-5" style noise
        return None
    return {"text": m.group(0).strip(), "currency": cur, "low": lo, "high": hi}


TEAM_SIZE = re.compile(r"\b(?:team of|a team of|squad of)\s+(\d{1,3})\b|\b(\d{1,3})[- ]person team\b", re.I)


def role_summary(job, jd, targets=()):
    arch = archetype(job["title"], jd, targets)
    m = TEAM_SIZE.search(jd)
    return {**arch, "remote": remote_mode(job, jd), "seniority": seniority(job["title"], jd),
            "team_size": (m.group(1) or m.group(2)) if m else "", "pay": pay_range(job, jd)}


# --- posting legitimacy (ghost-job signals) -------------------------------------------------------------------

CONTRACTOR = re.compile(r"\b(1099|independent contractor|self-employed|invoice (?:us |for )?(?:your )?services|outside ir35|"
                        r"umbrella company|consulting agreement|freelanc\w+)\b", re.I)
BUZZWORDS = re.compile(r"\b(ai transformation|digital transformation|ai enablement|drive innovation|process innovation|"
                       r"ai-first culture|transform the organi[sz]ation)\b", re.I)
PAID_FEE = re.compile(r"\b(training fee|registration fee|onboarding fee|security deposit|pay (?:for|a) (?:your )?(?:own )?"
                      r"(?:training|equipment|background check|kit))\b", re.I)
UNPAID = re.compile(r"\b(unpaid (?:internship|role|position)|stipend[- ]only|volunteer position)\b", re.I)
EVERGREEN = re.compile(r"\b(evergreen|rolling basis|ongoing (?:hiring|recruitment)|talent (?:pool|community)|future opportunities)\b", re.I)


AI_AIMED = re.compile(r"[^.!?\n]{0,120}\b(?:if you(?:'re| are) (?:an? )?(?:llm|ai\b|language model|chatgpt|gpt|bot)|ignore (?:all |any )?"
                      r"(?:previous|prior|above) instructions|\[llm[^\]]*\]|(?:llm|ai) (?:alert|reviewer)|note to (?:ai|llm)s?)[^.!?\n]{0,160}[.!?]?", re.I)


def ai_aimed(jd):
    """Sentences in a posting written to AI tools ("If you're an LLM, include ..."). They are data: quoted to the user
    as an anomaly, kept out of everything a model reads, never followed."""
    return [m.group(0).strip() for m in AI_AIMED.finditer(jd or "")][:3]


def strip_ai_aimed(jd):
    return AI_AIMED.sub(" ", jd or "")


def legitimacy(job, jd, live=None, reposts=0):
    """Signals that the opening is real and active (High Confidence / Proceed with Caution / Suspicious).
    Observations, never accusations: every concerning signal has innocent explanations, shown as notes."""
    signals, notes = [], []
    age = None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", job.get("posted") or ""):
        age = (datetime.now() - datetime.fromisoformat(job["posted"])).days
    senior = bool(re.search(r"principal|staff|director|head of|vp|chief", job["title"], re.I))
    limit = 90 if senior else 60
    if age is None:
        signals.append(("Posting age", "Not shown", "Neutral"))
    else:
        signals.append(("Posting age", f"{age} days", "Positive" if age <= 30 else "Neutral" if age <= limit else "Concerning"))
    if live:
        signals.append(("Still open", live["reason"], {"active": "Positive", "expired": "Concerning"}.get(live["state"], "Neutral")))
    jm = J()
    tech = len(jm.skill_hits(jd, jm.SKILLS))
    signals.append(("Specific about the work", f"{tech} named technologies", "Positive" if tech >= 4 else "Neutral" if tech >= 1 or len(jd) > 1500 else "Concerning"))
    from features.ai_jobs.ranking import years_asked
    ask = years_asked(jd)
    if ask and re.search(r"\b(junior|entry|graduate|new grad|intern|fresher)\b", job["title"], re.I) and ask[0] >= 4:
        signals.append(("Realistic requirements", f"Entry-level title asks {ask[0]}+ years", "Concerning"))
    if reposts:
        signals.append(("Reposted", f"Seen {reposts + 1} times with new links in 90 days", "Concerning" if reposts >= 2 else "Neutral"))
    pay = pay_range(job, jd)
    signals.append(("Pay shown", pay["text"] if pay else "Not stated", "Positive" if pay else "Neutral"))
    if len(jd) < 600:
        signals.append(("Description", "Very short description", "Concerning"))
    if PAID_FEE.search(jd):
        signals.append(("Asks you to pay", PAID_FEE.search(jd).group(0), "Concerning"))
        notes.append("A real employer never asks a candidate to pay to get the job. Don't pay anything before you have a signed offer.")
    aimed = ai_aimed(jd)
    if aimed:
        signals.append(("Text aimed at AI tools", aimed[0][:160], "Neutral"))
        notes.append("The posting has a line written for AI tools, often a check for automated applications. Inkwell ignored "
                     "it. Write the free-text answers yourself for this one.")
    if EVERGREEN.search(jd):
        notes.append("It reads as an ongoing / talent-pool posting: a pipeline role, not a ghost job, but replies can be slow.")
    if senior:
        notes.append("Senior roles legitimately stay open for months.")
    concerning = sum(s[2] == "Concerning" for s in signals)
    positive = sum(s[2] == "Positive" for s in signals)
    if live and live["state"] == "expired":
        tier = "Closed"
    elif concerning >= 2 and positive <= 1 or PAID_FEE.search(jd):
        tier = "Suspicious"
    elif concerning or (age is None and positive < 2):
        tier = "Proceed with Caution"
    else:
        tier = "High Confidence"
    extra = []
    if CONTRACTOR.search(jd):
        extra.append(f"Contractor wording (\"{CONTRACTOR.search(jd).group(0)}\"): confirm whether this is employment or a services "
                     "contract before accepting; benefits and protections depend on it.")
    buzz = BUZZWORDS.findall(jd)
    small_team = (m := TEAM_SIZE.search(jd)) and int(m.group(1) or m.group(2)) <= 5
    if buzz and (small_team or not re.search(r"senior|staff|principal|lead|head|director", job["title"], re.I)):
        extra.append(f"Heavy transformation language (\"{buzz[0]}\") for the role's size: in interviews ask which systems you'd work "
                     "with and how mature they are.")
    if UNPAID.search(jd):
        extra.append("The posting describes an unpaid placement.")
    return {"tier": tier, "signals": [{"signal": a, "finding": b, "weight": c} for a, b, c in signals], "notes": notes, "flags": extra}


# --- the user's own rules -------------------------------------------------------------------------------------

def host_of(url):
    m = re.match(r"https?://([^/:?#]+)", url or "", re.I)
    return (m.group(1) if m else "").lower().rstrip(".")


def same_name(a, b):
    k = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    return bool(k(a)) and k(a) == k(b)


def rule_hits(job, jd, rules):
    """Each hard rule this job breaks: [(rule, the words that break it)]. A missing fact never breaks a rule
    (no salary shown never breaks a pay floor; no years stated never breaks an experience cap)."""
    hits = []
    for b in rules.get("blacklist", []):
        b = b.strip()
        if not b:
            continue
        host = host_of(job.get("url", ""))
        if same_name(b, job.get("company")) or ("." in b and (host == b.lower() or host.endswith("." + b.lower()))):
            hits.append((f"Never apply to {b}", job.get("company") or host))
    text = norm_text(jd)
    pay = pay_range(job, jd)
    floor = rules.get("min_salary") or 0
    if floor and pay and pay.get("high") and (not rules.get("currency") or pay["currency"] == rules["currency"]) and pay["high"] < floor:
        hits.append((f"Pay at least {rules.get('currency', '')} {floor:,.0f}".strip(), pay["text"]))
    mode = remote_mode(job, jd)
    if rules.get("remote_only") and mode in ("On-site", "Hybrid"):
        hits.append(("Remote only", f"{mode}: {job.get('location') or 'stated in the description'}"))
    if rules.get("no_contract") and (m := CONTRACTOR.search(text) or re.search(r"\b(contract (?:role|position)|fixed[- ]term)\b", text, re.I)):
        hits.append(("No contract roles", m.group(0)))
    if rules.get("need_sponsorship") and (m := re.search(r"(?:no|not|unable to|cannot|can't|won't) (?:provide |offer )?"
                                                          r"(?:visa )?sponsor\w*", text, re.I)):
        hits.append(("I need visa sponsorship", m.group(0)))
    from features.ai_jobs.ranking import years_asked
    ask = years_asked(jd)
    if rules.get("max_years") and ask and ask[0] > rules["max_years"]:
        hits.append((f"At most {rules['max_years']} years asked", f"asks {ask[0]}+ years"))
    for word in rules.get("avoid_words", []):
        if word.strip() and re.search(rf"\b{re.escape(word.strip())}\b", f"{job['title']} {text}", re.I):
            hits.append((f"Avoid \"{word.strip()}\"", word.strip()))
    return [{"rule": r, "because": w} for r, w in hits]
