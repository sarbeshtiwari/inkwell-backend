"""Cutshort (cutshort.io): Indian tech jobs from the public category pages (the job list is in the page's data).
Find only: you open the job and apply on Cutshort yourself. Category pages as in Morrow's cutshort_bot (MIT)."""
import re
from datetime import datetime

from features.ai_jobs.boards._next import page_data
from features.ai_jobs.boards._util import get_text, plain

NAME, KIND, REGIONS = "Cutshort", "search", ["in"]


def slug(role):
    return re.sub(r"[^a-z0-9]+", "-", (role or "software developer").lower()).strip("-")


# Cutshort's own category names for common roles (its URLs aren't the plain role name).
CATEGORIES = [(r"\b(ai|artificial intelligence|genai|generative|llm|agent)", "artificial-intelligence-ai"),
              (r"machine learning|\bml\b|mlops", "machine-learning-ml"), (r"deep learning", "deep-learning"),
              (r"\bnlp\b|natural language", "natural-language-processing-nlp"), (r"data scien", "datascience"),
              (r"full.?stack", "fullstack-developer"), (r"back.?end", "backend-developer"), (r"front.?end", "frontend-developer"),
              (r"devops|sre\b", "devops"), (r"android", "android-developer"), (r"\bios\b", "ios-developer")]


def pages(role):
    """Cutshort has a page per skill or role ('python-jobs', 'artificial-intelligence-ai-jobs'): its category for the
    role, then the role as written, then its main word (e.g. 'python')."""
    s = slug(role)
    cats = [c for pat, c in CATEGORIES if re.search(pat, role or "", re.I)]
    words = [w for w in s.split("-") if len(w) > 2 and w not in ("senior", "junior", "lead", "engineer", "developer")]
    return list(dict.fromkeys(cats + [s] + ([words[0]] if words else [])))


def fetch(role):
    out = []
    for s in pages(role):
        out += read(get_text(f"https://cutshort.io/jobs/{s}-jobs"))
        if out:
            break
    return out


def read(html):
    out = []
    for data in page_data(html):
        if not isinstance(data, dict):
            continue
        for q in (((data.get("props") or {}).get("pageProps") or {}).get("dehydratedState") or {}).get("queries") or []:
            jobs = ((((q.get("state") or {}).get("data") or {}).get("data") or {}).get("pageData") or {}).get("jobs") or []
            for j in jobs:
                if not j.get("publicUrl") or not j.get("headline"):
                    continue
                exp, pay = j.get("expRange") or {}, j.get("salaryRange") or {}
                where = ", ".join(j.get("locations") or []) or ("Remote" if "remote" in (j.get("remoteType") or "") else "India")
                if j.get("remoteType") == "remote_only" and "Remote" not in where:
                    where += " · Remote"
                out.append({"id": f"cs:{j.get('_id')}", "ats": "", "board": "cutshort.io", "title": j["headline"],
                            "company": (j.get("companyDetails") or {}).get("name", ""), "location": where, "url": j["publicUrl"],
                            "text": plain(j.get("sanitizedComment") or "")[:6000] + "\nSkills: " + ", ".join(j.get("allSkills") or []),
                            "posted": (datetime.fromtimestamp(int(str(j.get("_id"))[:8], 16)).date().isoformat() if j.get("_id") else ""),
                            "salary": (f"₹{pay['min'] / 1e5:g}–{pay['max'] / 1e5:g} LPA" if pay.get("max") else ""),
                            "experience": f"{exp.get('min')}–{exp.get('max')} yrs" if exp.get("max") else "", "via": NAME})
    return out
