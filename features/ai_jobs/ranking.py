"""Matching engine: role fit, skills, experience, TF-IDF similarity, optional local model review; the LangChain chain."""

import math
import re
from collections import Counter
from datetime import datetime
import requests

from features.ai_jobs.common import J
from features.ai_jobs import siteapply

ANYWHERE = re.compile(r"\b(remote|anywhere|worldwide|global|work from home|wfh)\b", re.I)


def location_fits(wanted, where):
    """Whether a job's location suits the place the user asked for. No place asked, or a job that doesn't say where:
    kept. Otherwise the job must name that place or a city/country in it, or be remote without naming another country
    ("Remote" yes, "Remote, Canada" no when India was asked)."""
    from features.ai_jobs.boards import countries_for
    wanted, where = (wanted or "").strip().lower(), (where or "").strip().lower()
    if not wanted or not where or wanted in where:
        return True
    want, there = countries_for(wanted), countries_for(where)
    if want & there:
        return True
    return bool(ANYWHERE.search(where)) and not there


STOP = set("a an and are as at be by for from has have in is it of on or our that the this to we will with you your "
           "who what work working team teams role job about across able experience years year using use".split())


def tokens(text):
    return [w for w in re.findall(r"[a-z][a-z0-9+#.]{1,30}", (text or "").lower()) if w not in STOP]


def cosine(a, b, idf):
    va = {w: c * idf.get(w, 1.0) for w, c in Counter(a).items()}
    vb = {w: c * idf.get(w, 1.0) for w, c in Counter(b).items()}
    dot = sum(v * vb.get(w, 0) for w, v in va.items())
    na, nb = math.sqrt(sum(v * v for v in va.values())), math.sqrt(sum(v * v for v in vb.values()))
    return dot / (na * nb) if na and nb else 0.0


def years_asked(text):
    """Smallest 'N+ years' / 'N-M years' the description asks for, or None."""
    found = []
    text = text if isinstance(text, str) else ""  # never fail a whole page on one oddly stored description
    for lo, hi in re.findall(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?)", text.lower()):
        lo = int(lo)
        if lo <= 20:
            found.append((lo, int(hi) if hi and int(hi) <= 25 else None))
    return min(found, key=lambda x: x[0]) if found else None


GENERIC_TECH = re.compile(r"\b(software|developer|sde|programmer|full[ -]?stack|front[ -]?end|back[ -]?end|web|mobile|platform|"
                          r"product engineer|application engineer)\b", re.I)


NON_TECH = re.compile(r"\b(finance|financial analyst|account(ing|ant)?|sales|marketing|recruit(er|ing)|legal|counsel|partnerships?|"
                      r"business development|strategic|operations manager|customer success|support specialist|hr|people partner)\b", re.I)


def role_fit(wanted, title, want_fam=None):
    """30 = the same kind of role, 15 = a related engineering role, 0 = something else."""
    jm = J()
    if NON_TECH.search(title) and not NON_TECH.search(wanted):
        return 0
    want_fam = want_fam if want_fam is not None else jm.role_families(wanted)
    have = jm.role_families(title)
    words = [w for w in tokens(wanted) if len(w) > 2 and w not in ("developer", "engineer", "senior", "junior")]
    if (want_fam & have) - {"software engineer"} or (words and all(w in title.lower() for w in words)):
        return 30
    if GENERIC_TECH.search(title) and ("software engineer" in want_fam or want_fam & {"full stack", "frontend", "backend"}):
        return 15
    return 0


def rank(profile, jobs):
    """Score every job 0–100 with reasons and gaps (our own engine, no outside service)."""
    jm = J()
    resume_tok = tokens(profile["resume"])
    docs = [tokens(f"{j['title']} {j['text']}") for j in jobs]
    df = Counter(w for d in docs + [resume_tok] for w in set(d))
    n = len(docs) + 1
    idf = {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items()}
    want_fam = jm.role_families(profile["role"])
    have_skills = set(profile["skills"])
    years = profile["years"]
    loc = profile.get("location", "").lower()
    out = []
    for j, d in zip(jobs, docs):
        title, body = j["title"], j["text"]
        reasons, gaps = [], []
        role = role_fit(profile["role"], title, want_fam)
        if role >= 30:
            reasons.append("Same kind of role")
        elif role:
            reasons.append("Related role")
        job_skills = set(jm.skill_hits(f"{title} {body}", jm.SKILLS)) - {"C", "R"}
        common = sorted(job_skills & have_skills)
        missing = sorted(job_skills - have_skills)
        skills = round(35 * (len(common) / (len(job_skills) + 1)) ** 0.6) if job_skills else 10
        if common:
            reasons.append("Your skills: " + ", ".join(common[:5]))
        gaps += missing[:4]
        ask = years_asked(body)
        if ask is None:
            exp = 12
        elif ask[0] <= years + 0.5 and (ask[1] is None or years <= ask[1] + 1.5):
            exp = 20; reasons.append(f"Asks {ask[0]}{'–' + str(ask[1]) if ask[1] else '+'} yrs: fits")
        elif ask[0] <= years + 1.5:
            exp = 10; gaps.append(f"Asks {ask[0]}+ yrs")
        else:
            exp = 0; gaps.append(f"Needs {ask[0]}+ yrs")
        if ask and years > (ask[1] or ask[0]) + 5:
            exp = min(exp, 8); gaps.append("May be junior for you")
        sim = round(15 * min(cosine(resume_tok, d, idf) / 0.25, 1))
        if re.search(r"\b(principal|staff|director|head of|vp|vice president|chief|distinguished|architect)\b", title, re.I) and years < 8:
            role, gaps = role // 3, gaps + ["Very senior title"]
        elif re.search(r"\b(senior|sr\.?|lead|manager)\b", title, re.I) and years < 4:
            role, gaps = role // 2, gaps + ["Senior title"]
        elif re.search(r"\b(intern|internship|graduate|fresher|new grad)\b", title, re.I) and years >= 2:
            role, gaps = role // 3, gaps + ["Entry-level role"]
        if loc and not location_fits(loc, j.get("location", "")):
            sim, role = 0, role // 2; gaps.append(f"Based in {j['location']}")
        bonus = 5 if profile.get("company") and profile["company"].lower()[:5] in j["company"].lower() else 0
        age = (datetime.now() - datetime.fromisoformat(j["posted"])).days if re.fullmatch(r"\d{4}-\d{2}-\d{2}", j.get("posted") or "") else None
        if age is not None and age > 120:
            bonus -= 8; gaps.append("Posted over 4 months ago")
        score = max(min(role + skills + exp + sim + bonus, 100), 0)
        out.append({**{k: v for k, v in j.items() if k != "text"}, "score": score, "reasons": reasons, "gaps": gaps,
                    "can_apply": bool(siteapply.mode(j)), "apply_mode": siteapply.mode(j), "summary": j["text"][:400]})
    out.sort(key=lambda x: -x["score"])
    return out


def search_sources(tools, sources, profile):
    """Run the chosen search sources and drop duplicates (the same job on several sources is kept once, preferring the
    one that can be applied to). Returns (jobs, errors); a failing source becomes an error line, never a failure."""
    found, errors = [], []
    for src in sources:
        try:
            if src == "career_sites":
                found += tools["career_sites"].invoke({"role": profile["role"], "company": profile.get("company", "")})
            elif src == "remote_boards":
                found += tools["remote_boards"].invoke({"role": profile["role"]})
        except Exception as e:
            errors.append(f"{'Job boards' if src == 'remote_boards' else 'Career sites'}: {str(e)[:120]}")
    uniq, same = {}, {}
    for j in sorted(found, key=lambda j: j["ats"] not in ("greenhouse", "lever", "ashby")):  # the same job on several sources: keep the appliable one
        key = re.sub(r"[^a-z0-9]", "", f"{j.get('company', '')}|{j['title']}".lower())
        if j["id"] in uniq or key in same:
            continue
        uniq[j["id"]] = j
        same[key] = j["id"]
    jobs = list(uniq.values())
    return jobs, errors
