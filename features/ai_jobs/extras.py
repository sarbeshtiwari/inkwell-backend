"""More of career-ops' scripts, as features of AI job match:

  tailored resume per job + fact check (cv tailoring, verify-cv-facts)   skills to learn (upskill)
  similar jobs you already looked at (jd-similarity)                    your history with the company (company-history)
  application deadlines (closing-soon)                                  close long-silent applications (close-stale)
  resume changed since an evaluation (reeval-candidates)                save a posting as PDF (archive-posting)
  interview and offer preparation (interview-prep, offer-prep)          weekly digest (weekly-digest)

Everything written for you comes from your own resume; a model may reword, never add. Drafts are yours to check."""

import html
import re
import time
from collections import Counter
from datetime import datetime, timedelta

from flask import jsonify, request
from bridge import login_required

from features.ai_jobs.common import C, J, bp
from features.ai_jobs import models, signals
from features.ai_jobs import tailor_rules as R
from features.ai_jobs.evaluate import GUARD, find_job, job_text, llm, render_pdf, resume_for, resume_lines
from features.ai_jobs.ranking import cosine, tokens


# --- tailored resume + fact check -----------------------------------------------------------------------------

NUM = re.compile(r"(?<![\w.])\d[\d,.]*\s?(?:%|x|k|m|\+|lpa|cr|users|people|ms)?(?![\w])", re.I)


def fact_check(text, original):
    """Numbers, skills and proper nouns in the tailored text that aren't in the original resume (should be none)."""
    low = original.lower()
    jm = J()
    issues = []
    for n in set(m.group(0).strip() for m in NUM.finditer(text)):
        digits = re.sub(r"[^\d.]", "", n)
        if digits and digits.strip(".") and digits.strip(".") not in re.sub(r"[^\d.\s]", " ", original):
            issues.append(n)
    have = set(jm.skill_hits(original, jm.SKILLS))
    issues += [s for s in jm.skill_hits(text, jm.SKILLS) if s not in have and s not in ("C", "R")]
    for name in set(re.findall(r"\b[A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)+\b", text)):
        if name.lower() not in low and not re.match(r"(Professional|Technical|Work|Key|Selected|Relevant) ", name):
            issues.append(name)
    return sorted(set(issues))


def clean_bullets(bullets, original):
    """Keep only bullets whose facts are all in the original; report the ones dropped."""
    kept, dropped = [], []
    for b in bullets:
        b = str(b).strip(" -•*")
        if not b:
            continue
        (dropped if fact_check(b, original) else kept).append(b)
    return kept, dropped


def tailor(ws, job):
    """A resume for this job: summary and skills aimed at it, the most relevant work first. Returns a dict."""
    original = resume_for(ws)
    jd = job_text(ws, job)
    kw = signals.keyword_coverage(f"{job['title']} {jd}", original)
    lines = resume_lines(original)
    jt = tokens(f"{job['title']} {jd}")
    ranked = sorted(lines, key=lambda l: -cosine(tokens(l), jt, {}))
    out = {"summary": "", "skills": kw["covered"] + [s for s in J().skill_hits(original, J().SKILLS) if s not in kw["covered"] and s not in ("C", "R")],
           "highlights": ranked[:6], "sections": [], "dropped": [], "engine": "Reachout AI"}
    cfg = models.config(ws) if original.strip() else None
    if cfg:
        with models.use(cfg):
            try:
                ordered(ws, cfg, original, job, jd, out)
                out["engine"] = f"Reachout AI + {cfg['label']}"
            except Exception as e:
                print(f"[Reachout] tailored resume model step skipped: {e!r}", flush=True)
    if not out["summary"]:
        years = (ws.load("ai_match", {}).get("want") or {}).get("years")
        out["summary"] = (f"{(ws.load('ai_match', {}).get('want') or {}).get('role') or job['title']} with {years:g} years of experience"
                          if isinstance(years, (int, float)) else job["title"]) + \
                         (f", working with {', '.join(kw['covered'][:5])}." if kw["covered"] else ".")
    out["keywords"] = kw
    out["original"] = original
    return out


def resume_structure(ws, cfg, original):
    """The resume split into summary, skill groups and roles, every piece copied word for word from the resume text.
    Pieces that aren't in the resume are dropped. Cached per resume, since the resume rarely changes."""
    import hashlib
    key = hashlib.sha256(original.encode()).hexdigest()[:16]
    cache = ws.load("ai_resume_struct", {}) or {}
    if cache.get("key") == key:
        return cache["data"]
    r = models.chat(cfg, GUARD + " You split a resume into parts. Copy text EXACTLY as written; never rephrase, fix or add.",
                    "Resume:\n" + original[:9000] + '\n\nReturn JSON {"summary": "<the summary paragraph, verbatim, or empty>", '
                    '"skills": [{"category": "<as written>", "items": ["<each skill verbatim>"]}], "sections": [{"heading": '
                    '"Experience|Projects|Education|...", "items": [{"title": "<verbatim>", "org": "<verbatim>", "dates": "<verbatim>", '
                    '"bullets": ["<each bullet verbatim, in order>"]}]}]}', as_json=True, max_tokens=4000, timeout=300)
    of = R.flat(original)
    data = {"summary": str(r.get("summary") or "") if R.verbatim(r.get("summary") or "", of) else "", "skills": [], "sections": []}
    for g in r.get("skills") or []:
        items = [str(i) for i in (g.get("items") or []) if isinstance(i, str) and R.verbatim(i, of)] if isinstance(g, dict) else []
        if items:
            data["skills"].append([str(g.get("category") or "Skills")[:80], list(dict.fromkeys(items))])
    for sec in r.get("sections") or []:
        items = []
        for it in (sec.get("items") or []) if isinstance(sec, dict) else []:
            bullets = [str(b).strip(" -•*") for b in it.get("bullets") or [] if isinstance(b, str) and R.verbatim(b, of)]
            fields = {k: str(it.get(k) or "")[:120] for k in ("title", "org", "dates")}
            if bullets or fields["title"]:
                items.append({**{k: v if R.verbatim(v, of) else "" for k, v in fields.items()}, "bullets": bullets})
        if items:
            data["sections"].append({"heading": str(sec.get("heading") or "")[:40], "items": items})
    ws.save("ai_resume_struct", {"key": key, "data": data})
    return data


def propose(cfg, job, jd, listing):
    """The AI's proposed order for a resume (and its read of what the job needs). Checked by tailor_rules after."""
    return models.chat(cfg, GUARD + " You tailor a resume by ORDER only: you may reorder skills within a category and bullets within a "
                       "role, and rewrite the summary using only words, tools and numbers already in the resume.",
                       f"Job: {job['title']} at {job['company']}\nPosting:\n{jd[:3000]}\n\nResume parts:\n{listing}\n\nReturn JSON "
                       '{"requirements": [{"need": "<what the job needs>", "match": "direct|transferable|adjacent|gap"}], '
                       '"summary": "<rewritten summary>", "skills": {"<category>": ["<same items, best first>"]}, '
                       '"bullets": [[<bullet indexes of role 0, best first>], [<role 1>], ...], "notes": ["<why this order, short>"]}',
                       as_json=True, max_tokens=2500, timeout=300)


def analysis_of(r):
    return [{"need": str(q.get("need", ""))[:160], "match": q.get("match") if q.get("match") in ("direct", "transferable", "adjacent", "gap")
             else "gap"} for q in (r.get("requirements") or []) if isinstance(q, dict)][:12]


def ordered(ws, cfg, original, job, jd, out):
    """Morrow's tailoring: the AI proposes an order (and may rewrite the summary); tailor_rules accepts only pure
    reorderings and a summary grounded in the resume. Fills out[summary, skills, sections, notes, analysis]."""
    base = resume_structure(ws, cfg, original)
    roles = [(si, ii, it) for si, sec in enumerate(base["sections"]) for ii, it in enumerate(sec["items"])]
    listing = {"summary": base["summary"], "skills": {c: items for c, items in base["skills"]},
               "roles": [{"index": n, "role": f"{it['title']} {it['org']}".strip(), "bullets": list(enumerate(it["bullets"]))}
                         for n, (_, _, it) in enumerate(roles)]}
    r = propose(cfg, job, jd, listing)
    terms = R.terms(original)
    notes = [str(n)[:200] for n in (r.get("notes") or []) if isinstance(n, str)][:6]
    skills, n1 = R.accept_skills(base["skills"], r.get("skills"))
    bullets, n2 = R.accept_bullets([it["bullets"] for _, _, it in roles], [f"{it['title']} {it['org']}".strip() for _, _, it in roles],
                                   r.get("bullets"))
    summary, n3 = R.accept_text("Summary", r.get("summary"), base["summary"], R.summary_bounds(base["summary"]), terms)
    sections = [{"heading": sec["heading"], "items": [dict(it) for it in sec["items"]]} for sec in base["sections"]]
    for (si, ii, _), bl in zip(roles, bullets):
        sections[si]["items"][ii]["bullets"] = bl
    out.update(summary=summary, sections=sections, notes=notes, checks=n1 + n2 + n3, skill_groups=skills,
               skills=[i for _, items in skills for i in items] or out["skills"],
               analysis=analysis_of(r))


def resume_html(ws, t):
    prof, extra = ws.profile(), ws.load("ai_apply_profile", {}) or {}
    e = html.escape
    contact = " · ".join(e(x) for x in [prof.get("email", ""), prof.get("phone", ""), extra.get("location", ""), extra.get("linkedin", ""),
                                         extra.get("github", ""), extra.get("portfolio", "")] if x)
    parts = [f"<h1>{e(prof.get('name', ''))}</h1><p class=c>{contact}</p>", f"<h2>Summary</h2><p>{e(t['summary'])}</p>"]
    if t.get("skill_groups"):
        parts.append("<h2>Skills</h2>" + "".join(f"<p><b>{e(c)}:</b> {e(', '.join(items))}</p>" for c, items in t["skill_groups"]))
    elif t["skills"]:
        parts.append(f"<h2>Skills</h2><p>{e(', '.join(t['skills']))}</p>")
    if t["sections"]:
        for sec in t["sections"]:
            parts.append(f"<h2>{e(sec['heading'])}</h2>")
            for it in sec["items"]:
                parts.append(f"<p class=r><b>{e(it['title'])}</b>{' · ' + e(it['org']) if it['org'] else ''}<span>{e(it['dates'])}</span></p>")
                if it["bullets"]:
                    parts.append("<ul>" + "".join(f"<li>{e(b)}</li>" for b in it["bullets"]) + "</ul>")
    else:  # no model: the most relevant lines first, then the full resume text as it is
        parts.append("<h2>Relevant highlights</h2><ul>" + "".join(f"<li>{e(h)}</li>" for h in t["highlights"]) + "</ul>")
        body = "".join(f"<p>{e(l)}</p>" for l in t["original"].splitlines() if l.strip())
        parts.append(f"<h2>Resume</h2>{body}")
    css = ("body{font:10.5pt/1.45 Helvetica,Arial,sans-serif;color:#111;margin:0}h1{font-size:20pt;margin:0}.c{color:#444;margin:2pt 0 8pt}"
           "h2{font-size:11pt;text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid #999;margin:12pt 0 4pt}p{margin:0 0 4pt}"
           ".r{display:flex;justify-content:space-between;margin-top:6pt}.r span{color:#555}ul{margin:2pt 0 4pt 16pt;padding:0}li{margin:0 0 2pt}")
    return f"<html><head><meta charset='utf-8'><style>{css}</style></head><body>{''.join(parts)}</body></html>"


def tex_resume(ws):
    """The user's own LaTeX resume in Files (the first .tex that isn't a cover letter), or ""."""
    for d in ws.documents():
        name = d["name"] if isinstance(d, dict) else d
        if name.lower().endswith(".tex") and "cover" not in name.lower():
            data = ws.doc_bytes(name)
            if data:
                return data.decode("utf-8", "replace")
    return ""


def latex_resume(ws, job, t):
    """The tailored resume typeset with LaTeX (Morrow's way), or None to use the HTML one. With your own .tex the AI's
    order is applied to it under the reorder-only rules; otherwise Morrow's template is filled from your resume's
    parts. A tailored version that runs longer than the original gets the original summary back, then gives up."""
    from features.ai_jobs import latex
    if not latex.available():
        return None
    prof, extra = ws.profile(), ws.load("ai_apply_profile", {}) or {}
    try:
        base_tex = tex_resume(ws)
        if base_tex:
            cfg = models.config(ws)
            s = latex.parse(base_tex)
            r = {}
            if cfg:
                with models.use(cfg):
                    r = propose(cfg, job, job_text(ws, job), latex.latex_listing(s, base_tex))
            tex, checks, _ = latex.tailor_tex(base_tex, r, R.terms(latex.plain(base_tex)))
            t["checks"] = checks + [c for c in t.get("checks", []) if not c.startswith(("Skills", "Bullets", "Summary"))]
            t["notes"] = [str(n)[:200] for n in (r.get("notes") or []) if isinstance(n, str)][:6] or t.get("notes", [])
            t["analysis"] = analysis_of(r) or t.get("analysis", [])
            longer_tex = latex.render(base_tex, {**latex.parse(tex), "summary": s["summary"]})
        else:
            if not t.get("sections"):
                return None
            tex = latex.from_parts(prof, extra, t)
            base = (ws.load("ai_resume_struct", {}) or {}).get("data") or {}
            base_tex = latex.from_parts(prof, extra, {**base, "skill_groups": base.get("skills"), "skills": []}) if base.get("sections") else tex
            longer_tex = latex.from_parts(prof, extra, {**t, "summary": base.get("summary", t.get("summary", ""))})
        pdf = latex.compile_pdf(tex)
        limit = latex.page_count(latex.compile_pdf(base_tex)) if base_tex != tex else latex.page_count(pdf)
        if latex.page_count(pdf) > limit:
            pdf = latex.compile_pdf(longer_tex)
            t.setdefault("checks", []).append("Summary: the tailored version ran longer than your resume, so the original summary was kept.")
            if latex.page_count(pdf) > limit:
                return None
        return pdf
    except Exception as e:
        print(f"[Reachout] LaTeX resume skipped, using the HTML one: {e}", flush=True)
        t.setdefault("checks", []).append(f"LaTeX: {str(e).splitlines()[0][:200]} The HTML resume was used instead.")
        return None


@bp.post("/api/ai-jobs/resume/<jid>")
@login_required
def make_resume(ws, jid):
    core = C()
    if core.rate_limited(("ai-resume", ws.uid), 40, 86400):
        raise core.Invalid("You've made 40 tailored resumes today. Try again tomorrow.", status=429)
    job = find_job(ws, jid)
    t = tailor(ws, job)
    pdf, fmt = latex_resume(ws, job, t), "LaTeX"
    if not pdf:
        pdf, fmt = render_pdf_html(resume_html(ws, t)), "HTML"
    if not pdf:
        raise core.Invalid("Couldn't make the PDF (the browser isn't available).", status=500)
    name = re.sub(r"[^\w .&-]", "", f"Resume - {job['company']} - {job['title']}")[:90].strip() + ".pdf"
    ws.save_doc(name, pdf)
    with ws.lock:
        rows = ws.load("ai_resumes", {}) or {}
        rows[jid] = {"doc": name, "use": rows.get(jid, {}).get("use", False), "at": time.time(), "dropped": t["dropped"][:20],
                     "engine": t["engine"], "keywords": t["keywords"]["percent"], "notes": t.get("notes", []),
                     "checks": t.get("checks", []), "analysis": t.get("analysis", []), "format": fmt}
        ws.save("ai_resumes", rows)
    return jsonify(resume=rows[jid], summary=t["summary"], skills=t["skills"])


@bp.put("/api/ai-jobs/resume/<jid>")
@login_required
def use_resume(ws, jid):
    with ws.lock:
        rows = ws.load("ai_resumes", {}) or {}
        if jid not in rows:
            raise C().Invalid("Make the tailored resume first.", status=404)
        rows[jid]["use"] = bool(C().body().get("use"))
        ws.save("ai_resumes", rows)
    return jsonify(resume=rows[jid])


def job_resume_doc(ws, job):
    row = ((ws.load("ai_resumes", {}) or {}).get(job["id"]) or {}) if job else {}
    return row.get("doc", "") if row.get("use") else ""


def render_pdf_html(page_html):
    """A full HTML page as a PDF (headless Chromium); None when the browser isn't available."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=["--no-sandbox"] if C().NO_SANDBOX else [])
            try:
                p = b.new_page()
                p.set_content(page_html)
                return p.pdf(format="A4", margin={"top": "14mm", "bottom": "14mm", "left": "15mm", "right": "15mm"})
            finally:
                b.close()
    except Exception as e:
        print(f"[Reachout] PDF failed: {e!r}", flush=True)
        return None


# --- save the posting -----------------------------------------------------------------------------------------

@bp.post("/api/ai-jobs/archive/<jid>")
@login_required
def archive_posting(ws, jid):
    """Keep a copy of the job description in Files: postings disappear, and interviews ask about them."""
    job = find_job(ws, jid)
    text = job_text(ws, job, raw=True)
    if len(text) < 100:
        raise C().Invalid("There's no description saved for this job to keep.")
    head = f"{job['title']} — {job['company']}\n{job.get('location', '')}\n{job['url']}\nSaved {datetime.now().date().isoformat()}\n\n"
    name = re.sub(r"[^\w .&-]", "", f"Posting - {job['company']} - {job['title']}")[:90].strip()
    pdf = render_pdf(head + text, name)
    ws.save_doc(f"{name}.pdf" if pdf else f"{name}.txt", pdf or (head + text).encode())
    return jsonify(doc=f"{name}.pdf" if pdf else f"{name}.txt")


# --- deadlines, similar jobs, company history -----------------------------------------------------------------

MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
DEADLINE = re.compile(rf"(?:apply by|applications? (?:close|closes|closing|due|deadline)|closing date|deadline(?: to apply)?|last date(?: to apply)?)"
                      rf"\s*(?:is|on|:)?\s*(\d{{1,2}}(?:st|nd|rd|th)? (?:{MONTHS})\w*,? \d{{4}}|(?:{MONTHS})\w* \d{{1,2}}(?:st|nd|rd|th)?,? \d{{4}}|"
                      rf"\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}/\d{{4}})", re.I)


def deadline(jd):
    """'YYYY-MM-DD' when the posting states a closing date it can be read from, else ''."""
    m = DEADLINE.search(jd or "")
    if not m:
        return ""
    raw = re.sub(r"(\d)(st|nd|rd|th)", r"\1", m.group(1)).replace(",", "")
    for fmt in ("%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def similar_jobs(ws, job, limit=3):
    """Jobs you evaluated or applied to whose description reads most like this one (reuse that cover letter or story)."""
    jds = ws.load("ai_jds", {}) or {}
    reports = ws.load("ai_reports", {}) or {}
    me = tokens(f"{job['title']} {jds.get(job['id'], '')}")
    out = []
    for rid, r in reports.items():
        if rid == job["id"]:
            continue
        other = tokens(f"{r['title']} {jds.get(rid, '')}")
        sim = cosine(me, other, {})
        if sim >= 0.35:
            out.append({"id": rid, "title": r["title"], "company": r["company"], "score5": r["score5"], "similarity": round(sim * 100)})
    return sorted(out, key=lambda x: -x["similarity"])[:limit]


def company_history(ws, company):
    """Your earlier applications to this company: when, and whether they answered (information, never a score)."""
    key = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    rows = []
    for a in (ws.load("applications", {}) or {}).values():
        if key and re.sub(r"[^a-z0-9]", "", (a.get("company") or "").lower()) == key:
            rows.append({"role": a.get("role", ""), "applied": (a.get("applied_at") or "")[:10], "status": a.get("status", "applied")})
    rows.sort(key=lambda r: r["applied"], reverse=True)
    silent = sum(r["status"] == "applied" for r in rows)
    return {"applications": rows[:6], "count": len(rows), "silent": silent,
            "label": "no-history" if not rows else "silent" if silent == len(rows) else "responded" if not silent else "mixed"}


def resume_changed_since(ws, at):
    """True when the resume used for matching was replaced after `at` (time.time())."""
    from core.db import M
    name = (ws.load("ai_match", {}).get("want") or {}).get("resume")
    if not name:
        return False
    row = M.documents.find_one({"uid": ws.uid, "name_hash": ws._doc_key(name)}, {"created": 1})
    return bool(row and row.get("created", 0) > at)


@bp.get("/api/ai-jobs/context/<jid>")
@login_required
def job_context(ws, jid):
    """The extra panels of a report: deadline, similar jobs, history with the company, tailored resume state."""
    job = find_job(ws, jid)
    rep = (ws.load("ai_reports", {}) or {}).get(jid)
    return jsonify(deadline=deadline(job_text(ws, job)), similar=similar_jobs(ws, job), history=company_history(ws, job["company"]),
                   resume=(ws.load("ai_resumes", {}) or {}).get(jid), stale=bool(rep and resume_changed_since(ws, rep["at"])),
                   prep=(ws.load("ai_prep", {}) or {}).get(jid))


# --- skills to learn ------------------------------------------------------------------------------------------

def skills_to_learn(ws):
    """Keywords the jobs you evaluated ask for and your resume lacks, most asked first — what's worth learning."""
    reports = (ws.load("ai_reports", {}) or {}).values()
    asked, missing, strong = Counter(), Counter(), Counter()
    for r in reports:
        for k in r["keywords"]["keywords"]:
            asked[k] += 1
        for k in r["keywords"]["missing"]:
            missing[k] += 1
            if r["score5"] >= 4:
                strong[k] += 1
    n = len(list((ws.load("ai_reports", {}) or {}).values()))
    return {"evaluated": n, "skills": [{"skill": k, "missing_in": c, "share": round(100 * c / n) if n else 0, "in_good_fits": strong[k]}
                                       for k, c in missing.most_common(15)],
            "have": [k for k, _ in asked.most_common(30) if k not in missing][:12]}


@bp.get("/api/ai-jobs/skills")
@login_required
def get_skills(ws):
    return jsonify(skills_to_learn(ws))


# --- close long-silent applications ---------------------------------------------------------------------------

def stale_apps(ws, days):
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for a in (ws.load("applications", {}) or {}).values():
        if a.get("merged_into") or (a.get("status") or "applied") != "applied":
            continue
        try:
            when = datetime.fromisoformat(str(a.get("updated_at") or a.get("applied_at"))[:19])
        except ValueError:
            continue
        if when < cutoff:
            out.append({"id": a["id"], "company": a.get("company", ""), "role": a.get("role", ""), "applied": str(a.get("applied_at", ""))[:10]})
    return sorted(out, key=lambda x: x["applied"])


@bp.get("/api/ai-jobs/stale")
@login_required
def list_stale(ws):
    days = C().v_int(request.args.get("days", 60), "days", "Days", 21, 3650)
    return jsonify(stale=stale_apps(ws, days), days=days)


@bp.post("/api/ai-jobs/stale")
@login_required
def close_stale(ws):
    """Mark applications with no reply in N days as closed (a note says why). Dry run unless confirm=true."""
    core = C()
    p = core.body()
    days = core.v_int(p.get("days", 60), "days", "Days", 21, 3650)
    rows = stale_apps(ws, days)
    if not p.get("confirm"):
        return jsonify(stale=rows, days=days, closed=0)
    from features import apps
    now = datetime.now().isoformat(timespec="seconds")
    with ws.lock:
        allapps = ws.load("applications", {})
        for r in rows:
            a = allapps.get(r["id"])
            if a:
                a["history"].append({"status": "closed", "at": now, "source": "you", "note": f"No reply in {days} days; closed by you"})
                apps.recompute(a)
        ws.save("applications", allapps)
    return jsonify(stale=[], days=days, closed=len(rows))


# --- interview and offer preparation --------------------------------------------------------------------------

def prep_fallback(kind, job, rep):
    if kind == "offer":
        pay = ((rep or {}).get("comp") or {}).get("pay") or {}
        return {"sections": [
            {"title": "Before you answer", "points": ["Thank them and ask for the offer in writing, with base, bonus, equity, notice period "
                                                       "and start date separated.", "Ask for a few days to decide; that's normal."]},
            {"title": "Your number", "points": [f"The posting showed {pay['text']}." if pay.get("text") else
                                                "Look up the band for this level and city before the call.",
                                                "Ask for the top of the band, with one reason tied to the role (a result you've shown)."]},
            {"title": "What else to ask for", "points": ["Joining bonus if base can't move", "Earlier review date", "Remote days, learning budget"]},
            {"title": "Script", "points": [f"\"Thank you, I'm excited about the {job['title']} role. Based on the scope and my experience with "
                                           "what the team needs, I was hoping for [number]. Is there flexibility on base?\""]}]}
    qs = ((rep or {}).get("interview") or {}).get("questions") or []
    return {"sections": [
        {"title": "Rounds to expect", "points": ["Recruiter screen: motivation, notice period, pay range", "Technical: the skills in the posting",
                                                 "Hiring manager: how you work, ownership, a project deep-dive", "Final / culture"]},
        {"title": "Likely questions", "points": qs or ["Walk me through a project you're proud of.", "Why this company?"]},
        {"title": "Stories to have ready", "points": [s.get("story", "")[:240] for s in ((rep or {}).get("interview") or {}).get("stories", [])]},
        {"title": "Ask them", "points": ["What would success look like in the first 90 days?", "Who would I work with day to day?",
                                         "What's the biggest challenge the team has right now?"]}]}


@bp.post("/api/ai-jobs/prep/<jid>")
@login_required
def make_prep(ws, jid):
    """Interview prep (rounds, questions per interviewer, stories, questions to ask) or offer negotiation prep."""
    core = C()
    if core.rate_limited(("ai-prep", ws.uid), 40, 86400):
        raise core.Invalid("You've prepared 40 times today. Try again tomorrow.", status=429)
    kind = "offer" if core.body().get("kind") == "offer" else "interview"
    job = find_job(ws, jid)
    rep = (ws.load("ai_reports", {}) or {}).get(jid)
    out = prep_fallback(kind, job, rep)
    out["engine"] = "Reachout AI"
    cfg = models.config(ws)
    if cfg:
        with models.use(cfg):
            try:
                ask = ("Prepare the candidate to negotiate this job offer: how to respond, the number to ask for and why, what else to "
                       "negotiate, and a short script. Never advise accepting below market." if kind == "offer" else
                       "Prepare the candidate for interviews for this job: likely rounds, questions for the recruiter, the hiring manager "
                       "and technical interviewers, which resume stories answer them (STAR, from the resume only), a technical checklist "
                       "(mark an item covered only when the resume names it; otherwise write 'To brush up: ...'), "
                       "and good questions to ask.")
                r = llm(GUARD + " " + ask + " Answer only JSON: {{\"sections\": [{{\"title\": \"...\", \"points\": [\"...\"]}}]}}.",
                        "Resume:\n{resume}\n\nJob: {title} at {company}\nPosting (excerpt):\n{jd}",
                        {"resume": resume_for(ws)[:4000], "title": job["title"], "company": job["company"], "jd": job_text(ws, job)[:3000]},
                        tokens_out=1600)
                secs = [{"title": str(s.get("title", ""))[:80], "points": [str(x)[:400] for x in s.get("points", []) if x][:10]}
                        for s in r.get("sections", []) if isinstance(s, dict) and s.get("points")]
                if secs:
                    out = {"sections": secs[:8], "engine": f"Reachout AI + {cfg['label']}"}
            except Exception as e:
                print(f"[Reachout] prep model step skipped: {e!r}", flush=True)
    out.update(kind=kind, at=time.time())
    with ws.lock:
        rows = ws.load("ai_prep", {}) or {}
        rows[jid] = out
        ws.save("ai_prep", rows)
    return jsonify(prep=out)


# --- weekly digest --------------------------------------------------------------------------------------------

def weekly_digest_all():
    """Mondays: one notification per user with the week's strong matches, follow-ups due and reply rate."""
    if datetime.now().weekday() != 0:
        return
    from features.ai_jobs.tracker import due_followups, insights
    from features.notify import notify
    week = datetime.now().strftime("%G-W%V")
    for u in C().M.users.find({}, {"_id": 1}):
        ws = C().Workspace(str(u["_id"]))
        try:
            if (ws.load("ai_digest", {}) or {}).get("week") == week or not ws.load("ai_match", {}).get("results"):
                continue
            strong = sum(1 for r in ws.load("ai_match", {}).get("results", []) if r.get("score", 0) >= 70)
            due = sum(1 for f in due_followups(ws) if f["kind"] != "cold")
            ins = insights(ws, 7)
            notify(ws.uid, "Your job search this week",
                   f"{strong} strong matches · {due} follow-up{'s' if due != 1 else ''} due · {ins['total']['total']} applied, "
                   f"{ins['total']['rate']}% replied", "/app/ai-jobs", "info")
            ws.save("ai_digest", {"week": week, "at": time.time()})
        except Exception as e:
            print(f"[Reachout] weekly digest skipped: {e!r}", flush=True)
