"""My answers: the user's saved answers, matching questions to them, and their API."""

import re
import time
from flask import jsonify
from bridge import login_required

from features.ai_jobs.common import C, bp


# Answers the user wrote, or answers Reachout learned from forms they filled in themselves. Learned and drafted
# answers wait for the user's approval ("review"); only approved answers are ever typed into a form.

ANS_STOP = set("a an the to of in for and or your you are is do does did have has with this that what which how why "
               "please describe tell us about at on any".split())


def norm_q(text, company=""):
    t = (text or "").lower().replace("✱", " ").replace("*", " ")
    t = re.sub(r"\((optional|required)\)|select\.\.\.", " ", t)
    if company and len(company) > 2:
        t = re.sub(rf"\b{re.escape(company.lower())}\b", " {company} ", t)
    t = re.sub(r"[^a-z0-9{} ]+", " ", t)
    return " ".join(t.split())


def q_score(a, b):
    if a == b:
        return 1.0
    from difflib import SequenceMatcher
    ta = {w for w in a.split() if w not in ANS_STOP and len(w) > 1}
    tb = {w for w in b.split() if w not in ANS_STOP and len(w) > 1}
    jac = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    return max(jac, SequenceMatcher(None, a, b).ratio() * 0.95)


def same_company(a, b):
    a, b = (a or "").lower().strip(), (b or "").lower().strip()
    return bool(a and b and (a in b or b in a))


def answer_for(bank, label, company):
    """The user's approved answer to this question, or None. Company-specific answers only go to that company."""
    q = norm_q(label, company)
    if len(q) < 2:
        return None
    best, best_s = None, 0.0
    for a in bank.values():
        if a.get("status") != "approved" or not a.get("answer"):
            continue
        if a.get("company") and not same_company(a["company"], company):
            continue
        sc = q_score(q, a.get("norm") or norm_q(a["question"], a.get("company", "")))
        if sc > best_s:
            best, best_s = a, sc
    if best and best_s >= 0.8:
        return best["answer"].replace("{company}", company or "your company")
    return None


def save_answer(ws, question, answer, company="", status="approved", source="you", learned_from="", aid=None):
    with ws.lock:
        bank = ws.load("ai_answers", {})
        norm = norm_q(question, company)
        if not aid:  # same question already saved: update it instead of adding a copy
            aid = next((k for k, v in bank.items() if v.get("norm") == norm and (v.get("company") or "").lower() == (company or "").lower()), None)
        if aid and aid in bank and source == "learned" and bank[aid].get("status") == "approved" and bank[aid].get("answer") == answer:
            return bank[aid]
        aid = aid or C().new_id()
        old = bank.get(aid, {})
        if source == "learned" and old.get("status") == "approved" and old.get("answer") != answer:
            status = "review"  # the user answered differently this time: ask which one to keep
        bank[aid] = {"id": aid, "question": question[:300], "answer": answer[:4000], "company": company[:80], "norm": norm,
                     "status": status, "source": source, "learned_from": learned_from[:120], "updated": time.time(),
                     "uses": old.get("uses", 0)}
        ws.save("ai_answers", bank)
        return bank[aid]


def answer_out(a):
    return {k: a.get(k) for k in ("id", "question", "answer", "company", "status", "source", "learned_from", "updated")}


@bp.get("/api/ai-jobs/answers")
@login_required
def list_answers(ws):
    bank = ws.load("ai_answers", {})
    rows = sorted(bank.values(), key=lambda a: (a.get("status") != "review", -a.get("updated", 0)))
    return jsonify(answers=[answer_out(a) for a in rows])


@bp.post("/api/ai-jobs/answers")
@login_required
def add_answer(ws):
    core, p = C(), C().body()
    q = core.v_text(p.get("question"), "question", "Question", 300, required=True)
    a = core.v_text(p.get("answer"), "answer", "Answer", 4000, required=True)
    company = core.v_text(p.get("company"), "company", "Company", 80)
    if len(ws.load("ai_answers", {})) >= 500:
        raise core.Invalid("You can save up to 500 answers. Remove some first.")
    return jsonify(answer=answer_out(save_answer(ws, q, a, company)))


@bp.put("/api/ai-jobs/answers/<aid>")
@login_required
def edit_answer(ws, aid):
    core, p = C(), C().body()
    bank = ws.load("ai_answers", {})
    if aid not in bank:
        raise core.Invalid("That answer no longer exists.", status=404)
    cur = bank[aid]
    q = core.v_text(p.get("question", cur["question"]), "question", "Question", 300, required=True)
    a = core.v_text(p.get("answer", cur["answer"]), "answer", "Answer", 4000, required=True)
    company = core.v_text(p.get("company", cur.get("company", "")), "company", "Company", 80)
    status = "approved" if p.get("approve", cur["status"] == "approved") else cur["status"]
    row = save_answer(ws, q, a, company, status=status, source=cur.get("source", "you"), learned_from=cur.get("learned_from", ""), aid=aid)
    return jsonify(answer=answer_out(row))


@bp.post("/api/ai-jobs/answers/approve-all")
@login_required
def approve_all(ws):
    with ws.lock:
        bank = ws.load("ai_answers", {})
        n = 0
        for a in bank.values():
            if a.get("status") == "review" and a.get("answer"):
                a["status"], n = "approved", n + 1
        ws.save("ai_answers", bank)
    return jsonify(approved=n)


@bp.delete("/api/ai-jobs/answers/<aid>")
@login_required
def delete_answer(ws, aid):
    with ws.lock:
        bank = ws.load("ai_answers", {})
        bank.pop(aid, None)
        ws.save("ai_answers", bank)
    return jsonify(ok=True)
