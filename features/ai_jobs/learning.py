"""Learning from feedback (career-ops' outcome journal, calibrate and learn loop).

Every "Good fit" / "Not for me" you give, every job you apply to, and what happens next (interview, offer,
rejection, no reply) is a lesson. From them Inkwell keeps a small preference model of what you like (kinds of
role, title words, skills, companies, remote mode, seniority) and nudges future matches by up to ±15 points,
saying why. Repeated reasons ("too senior", "pay too low") become suggested rules that wait for your approval:
nothing is added to your rules on its own. Calibration shows whether higher scores actually led to interviews."""

import math
import re
import time
from collections import Counter
from datetime import datetime

from flask import jsonify
from bridge import login_required

from features.ai_jobs.common import C, J, bp
from features.ai_jobs import signals

REASONS = {"role": "Wrong kind of role", "senior": "Too senior for me", "junior": "Too junior for me", "location": "Wrong location",
           "remote": "Not remote", "pay": "Pay too low", "company": "Not this company", "skills": "Skills don't match",
           "contract": "Contract / freelance", "other": "Something else"}
# lesson weights: what you said, then what you did, then what happened
WEIGHTS = {"good": 2.0, "bad": -2.0, "applied": 1.0, "responded": 1.5, "assessment": 1.5, "interview": 2.5, "offer": 3.0,
           "hired": 3.0, "discarded": -1.0}
MIN_LESSONS = 3      # the model starts nudging after this many
MAX_NUDGE = 15       # points, either way
HALF_LIFE = 120      # days: older lessons count less, tastes change
STOP = set("and the for with of to in at a an or senior junior sr jr ii iii iv i remote hybrid onsite on site india".split())


def features(job, jd=""):
    """What a job is, as named features: 'w:backend', 'sk:Python', 'co:acme', 'ar:Backend', 'rm:Remote', 'se:Senior'."""
    title = job.get("title", "")
    text = jd or job.get("summary", "") or job.get("text", "")
    out = {f"w:{w}" for w in re.findall(r"[a-z][a-z0-9+#.]{2,20}", title.lower()) if w not in STOP}
    out |= {f"sk:{s}" for s in J().skill_hits(f"{title} {text}", J().SKILLS)[:15] if s not in ("C", "R")}
    company = re.sub(r"[^a-z0-9]", "", (job.get("company") or "").lower())
    if company:
        out.add(f"co:{company}")
    for a in signals.archetype(title, text)["archetype"].split(" + "):
        if a != "Unclassified":
            out.add(f"ar:{a}")
    out.add(f"rm:{signals.remote_mode(job, text)}")
    out.add(f"se:{signals.seniority(title, text)}")
    return sorted(out)


def label(f):
    kind, _, v = f.partition(":")
    return {"w": f"“{v}” in the title", "sk": v, "co": f"{v} (company)", "ar": f"{v} roles", "rm": v.lower() if v else "",
            "se": f"{v.lower()} level"}.get(kind, v)


def record(ws, job, signal, reason="", note="", jd=""):
    """Keep one lesson about a job (the newest signal per job and kind wins)."""
    if signal not in WEIGHTS:
        return None
    with ws.lock:
        book = ws.load("ai_feedback", {}) or {}
        row = book.get(job["id"]) or {"id": job["id"], "title": job.get("title", "")[:160], "company": job.get("company", "")[:120],
                                       "features": features(job, jd), "signals": {}}
        row["signals"][signal] = time.time()
        if signal in ("good", "bad"):
            row["signals"].pop("bad" if signal == "good" else "good", None)
            row["reason"], row["note"] = (reason if signal == "bad" else ""), note[:200]
        row["score"] = job.get("score", row.get("score"))
        row["updated"] = time.time()
        book[job["id"]] = row
        if len(book) > 2000:  # keep the newest lessons
            book = dict(sorted(book.items(), key=lambda kv: -kv[1].get("updated", 0))[:2000])
        ws.save("ai_feedback", book)
        return row


def history(ws, book, pipe):
    """Jobs whose status you set before feedback existed (applied, interview, rejected…), as lessons too."""
    known = {r["id"]: r for r in (ws.load("ai_match", {}) or {}).get("results", [])}
    reports = ws.load("ai_reports", {}) or {}
    jds = ws.load("ai_jds", {}) or {}
    out = {}
    done = {jid: {"status": "applied", "at": datetime.fromtimestamp(a.get("at") or time.time()).isoformat()}
            for jid, a in (ws.load("ai_applied", {}) or {}).items() if (a or {}).get("state") == "applied"}
    pipe = {**done, **{k: v for k, v in pipe.items() if v.get("status") in WEIGHTS or k not in done}}
    for jid, p in pipe.items():
        if jid in book or p.get("status") not in WEIGHTS:
            continue
        job = known.get(jid) or reports.get(jid)
        if not job or not job.get("title"):
            continue
        out[jid] = {"id": jid, "title": job["title"], "company": job.get("company", ""), "score": job.get("score"),
                    "features": features(job, jds.get(jid, "")), "signals": {}, "updated": _ts(p.get("at")) or time.time()}
    return out


def lessons(ws):
    """[(features, weight)] from feedback, statuses and outcomes, older ones fading."""
    pipe = ws.load("ai_pipeline", {}) or {}
    book = ws.load("ai_feedback", {}) or {}
    book = {**history(ws, book, pipe), **book}
    applied = {jid for jid, a in (ws.load("ai_applied", {}) or {}).items() if (a or {}).get("state") == "applied"}
    now = time.time()
    out = []
    for jid, row in book.items():
        sig = dict(row.get("signals", {}))
        st = (pipe.get(jid) or {}).get("status")
        st = "applied" if jid in applied and st not in WEIGHTS else st
        if st in WEIGHTS and st not in sig:
            sig[st] = _ts((pipe.get(jid) or {}).get("at")) or row.get("updated", now)
        elif jid in applied and not ({"applied"} | set(WEIGHTS) - {"good", "bad", "discarded"}) & set(sig):
            sig["applied"] = row.get("updated", now)
        if not sig:
            continue
        said = [k for k in ("good", "bad") if k in sig]
        did = max((k for k in sig if k not in ("good", "bad")), key=lambda k: abs(WEIGHTS[k]), default=None)
        w = (WEIGHTS[said[0]] if said else 0) + (WEIGHTS[did] if did else 0)
        age = (now - max(sig.values())) / 86400
        out.append((row["features"], w * 0.5 ** (age / HALF_LIFE)))
    return out


def _ts(value):
    try:
        return datetime.fromisoformat(str(value)[:19]).timestamp()
    except ValueError:
        return None


def model(ws):
    """{feature: weight} learned from the lessons (log-odds of liked vs not, smoothed), or {} with too few."""
    ls = [x for x in lessons(ws) if abs(x[1]) > 0.05]
    if len(ls) < MIN_LESSONS:
        return {"weights": {}, "lessons": len(ls)}
    pos, neg = Counter(), Counter()
    for feats, w in ls:
        for f in feats:
            (pos if w > 0 else neg)[f] += abs(w)
    tp, tn = sum(w for _, w in ls if w > 0) or 1, -sum(w for _, w in ls if w < 0) or 1
    weights = {}
    for f in set(pos) | set(neg):
        p, n = pos[f], neg[f]
        if p + n < 1.5:  # seen too little to mean anything
            continue
        weights[f] = round(math.log(((p + 0.5) / (tp + 1)) / ((n + 0.5) / (tn + 1))), 3)
    return {"weights": weights, "lessons": len(ls)}


def nudge(job, m, jd=""):
    """(points, why) the learned taste adds to or takes from one job."""
    w = m.get("weights") or {}
    if not w:
        return 0, []
    hits = [(f, w[f]) for f in features(job, jd) if f in w]
    if not hits:
        return 0, []
    raw = sum(v for _, v in hits) / math.sqrt(len(hits))
    pts = round(MAX_NUDGE * math.tanh(raw / 3))
    top = sorted(hits, key=lambda x: -abs(x[1]))
    why = [label(f) for f, v in top if (v > 0) == (pts > 0)][:3]
    return pts, why


def nudge_why(job, m, jd, liked):
    hits = [(f, m["weights"][f]) for f in features(job, jd) if f in m["weights"] and (m["weights"][f] > 0) == liked]
    return [label(f) for f, _ in sorted(hits, key=lambda x: -abs(x[1]))[:3]]


def adjust(ranked, m, texts=None):
    """Apply the learned nudge to ranked matches (in place) and re-sort."""
    if not (m or {}).get("weights"):
        return ranked
    raw = [nudge(r, m, (texts or {}).get(r["id"], "")) for r in ranked]
    mid = sorted(p for p, _ in raw)[len(raw) // 2] if len(raw) > 2 else 0  # relative to this list: reorder, don't inflate
    for r, (pts, why) in zip(ranked, raw):
        pts = max(-MAX_NUDGE, min(MAX_NUDGE, pts - mid))
        if abs(pts) < 2:
            continue
        why = why if (pts > 0) == (pts + mid > 0) else nudge_why(r, m, (texts or {}).get(r["id"], ""), pts > 0)
        r["score"] = max(0, min(100, r["score"] + pts))
        r["learned"] = pts
        msg = ("You liked jobs like this: " + ", ".join(why) if pts > 0 else
               "You passed on jobs like this: " + ", ".join(why) if why else "Less like the jobs you've liked")
        (r.setdefault("reasons", []) if pts > 0 else r.setdefault("gaps", [])).append(msg)
    ranked.sort(key=lambda x: -x["score"])
    return ranked


def taste(ws, n=6):
    """A short plain-language summary of what you like and pass on, for the AI's prompts and the page."""
    w = model(ws).get("weights") or {}
    likes = [label(f) for f, v in sorted(w.items(), key=lambda x: -x[1]) if v > 0.4][:n]
    dislikes = [label(f) for f, v in sorted(w.items(), key=lambda x: x[1]) if v < -0.4][:n]
    reasons = Counter(r.get("reason") for r in (ws.load("ai_feedback", {}) or {}).values() if r.get("reason"))
    return {"likes": likes, "dislikes": dislikes, "reasons": [{"reason": REASONS.get(k, k), "count": c} for k, c in reasons.most_common(5)]}


def prompt_hint(ws):
    """One line for the AI review: what this user has liked and passed on so far."""
    t = taste(ws, 5)
    if not (t["likes"] or t["dislikes"]):
        return ""
    return ("From the candidate's own feedback so far: they tend to like " + (", ".join(t["likes"]) or "—") +
            "; they passed on " + (", ".join(t["dislikes"]) or "—") +
            (". Their usual reasons for passing: " + ", ".join(r["reason"].lower() for r in t["reasons"][:3]) if t["reasons"] else "") + ".")


# --- suggested rules (never applied without approval) ------------------------------------------------------------

def suggestions(ws):
    """Rules your feedback keeps pointing at, each waiting for a yes or no."""
    from features.ai_jobs.evaluate import get_rules
    book = list((ws.load("ai_feedback", {}) or {}).values())
    rules = get_rules(ws)
    decided = ws.load("ai_learn_decided", {}) or {}
    bad = [r for r in book if "bad" in r.get("signals", {})]
    out = []

    def add(sid, text, patch):
        if sid not in decided:
            out.append({"id": sid, "text": text, "patch": patch})
    per_company = Counter(r["company"] for r in bad if r.get("reason") == "company" and r.get("company"))
    for company, n in per_company.items():
        if company.lower() not in [b.lower() for b in rules.get("blacklist", [])]:
            add(f"bl:{company.lower()}", f"Never show jobs at {company}", {"blacklist_add": company})
    seniors = [r for r in bad if r.get("reason") == "senior"]
    if len(seniors) >= 3 and not rules.get("max_years"):
        add("max_years", "Hide jobs asking for much more experience than you have (you marked "
            f"{len(seniors)} as too senior)", {"max_years": "auto"})
    if sum(r.get("reason") == "remote" for r in bad) >= 3 and not rules.get("remote_only"):
        add("remote_only", "Only show remote jobs", {"remote_only": True})
    if sum(r.get("reason") == "contract" for r in bad) >= 2 and not rules.get("no_contract"):
        add("no_contract", "Hide contract and freelance jobs", {"no_contract": True})
    words = Counter(f.split(":", 1)[1] for r in bad if r.get("reason") == "role" for f in r["features"] if f.startswith("w:"))
    good_words = {f.split(":", 1)[1] for r in book if "good" in r.get("signals", {}) for f in r["features"] if f.startswith("w:")}
    for w, n in words.most_common(3):
        if n >= 3 and w not in good_words and w not in [x.lower() for x in rules.get("avoid_words", [])]:
            add(f"avoid:{w}", f"Skip jobs with “{w}” in the title (you passed on {n})", {"avoid_add": w})
    return out[:6]


def apply_suggestion(ws, sid, accept):
    """Yes: change the rule; no: never suggest it again. Returns the rules."""
    from features.ai_jobs.evaluate import get_rules
    s = next((x for x in suggestions(ws) if x["id"] == sid), None)
    if not s:
        raise C().Invalid("That suggestion isn't open any more.", status=404)
    with ws.lock:
        decided = ws.load("ai_learn_decided", {}) or {}
        decided[sid] = {"accepted": bool(accept), "at": time.time()}
        ws.save("ai_learn_decided", decided)
        rules = {**(ws.load("ai_rules", {}) or {})}
        if accept:
            p = s["patch"]
            if "blacklist_add" in p:
                rules["blacklist"] = (get_rules(ws).get("blacklist") or []) + [p["blacklist_add"]]
            if "avoid_add" in p:
                rules["avoid_words"] = (get_rules(ws).get("avoid_words") or []) + [p["avoid_add"]]
            if p.get("max_years") == "auto":
                rules["max_years"] = int((ws.load("ai_match", {}).get("want") or {}).get("years") or 0) + 3 or 5
            for k in ("remote_only", "no_contract"):
                if k in p:
                    rules[k] = p[k]
            ws.save("ai_rules", rules)
    return get_rules(ws)


# --- calibration: do higher scores lead to interviews? -----------------------------------------------------------

def calibration(ws, floor=5):
    """Interview rate by match score band, from jobs with a known outcome. Bands with fewer than `floor` jobs show
    counts only: two of three is an anecdote, not a rate. Jobs still waiting for a reply aren't counted."""
    pipe = ws.load("ai_pipeline", {}) or {}
    book = ws.load("ai_feedback", {}) or {}
    results = {r["id"]: r for r in (ws.load("ai_match", {}) or {}).get("results", [])}
    bands = {}
    waiting = 0
    for jid, p in pipe.items():
        hist = {h.get("status") for h in p.get("history", [])} | {p.get("status")}
        if not hist & {"applied", "responded", "assessment", "interview", "offer", "rejected", "hired"}:
            continue
        score = (results.get(jid) or {}).get("score") or (book.get(jid) or {}).get("score")
        if score is None:
            continue
        won = bool(hist & {"interview", "offer", "hired"})
        if not won and p.get("status") not in ("rejected", "discarded"):
            waiting += 1
            continue
        b = "80+" if score >= 80 else "65–79" if score >= 65 else "50–64" if score >= 50 else "below 50"
        x = bands.setdefault(b, {"band": b, "total": 0, "interviews": 0})
        x["total"] += 1
        x["interviews"] += won
    rows = sorted(bands.values(), key=lambda x: ["80+", "65–79", "50–64", "below 50"].index(x["band"]))
    for x in rows:
        x["rate"] = round(100 * x["interviews"] / x["total"]) if x["total"] >= floor else None
    rated = [x for x in rows if x["rate"] is not None]
    verdict = ""
    if len(rated) >= 2:
        verdict = ("Higher scores are leading to more interviews, so the scoring works for you." if rated[0]["rate"] > rated[-1]["rate"]
                   else "Higher scores aren't leading to more interviews yet. Your Good fit / Not for me feedback helps fix that.")
    return {"bands": rows, "waiting": waiting, "verdict": verdict, "floor": floor}


# --- API -----------------------------------------------------------------------------------------------------

def _job(ws, jid):
    job = next((r for r in (ws.load("ai_match", {}) or {}).get("results", []) if r["id"] == jid), None)
    if not job:
        row = (ws.load("ai_feedback", {}) or {}).get(jid)
        if row:
            return {"id": jid, "title": row["title"], "company": row["company"], "score": row.get("score")}
        raise C().Invalid("That job isn't in your matches any more.", status=404)
    return job


@bp.post("/api/ai-jobs/feedback/<jid>")
@login_required
def give_feedback(ws, jid):
    """Body: {verdict: good|bad|clear, reason?, note?}. 'bad' also sets the job to Discarded."""
    core = C()
    p = core.body()
    verdict = str(p.get("verdict", ""))
    job = _job(ws, jid)
    if verdict == "clear":
        with ws.lock:
            book = ws.load("ai_feedback", {}) or {}
            if jid in book:
                for k in ("good", "bad"):
                    book[jid]["signals"].pop(k, None)
                book[jid]["reason"] = ""
                ws.save("ai_feedback", book)
        return jsonify(feedback=None, learning=summary(ws))
    if verdict not in ("good", "bad"):
        raise core.Invalid("Say whether it's a good fit.", "verdict")
    reason = str(p.get("reason") or "")
    if verdict == "bad" and reason not in REASONS:
        reason = "other"
    jd = (ws.load("ai_jds", {}) or {}).get(jid, "")
    row = record(ws, job, verdict, reason, core.v_text(p.get("note"), "note", "Note", 200) or "", jd)
    if verdict == "bad":
        from features.ai_jobs.tracker import set_status
        set_status(ws, jid, "discarded", note=REASONS[reason])
    return jsonify(feedback={"verdict": verdict, "reason": row.get("reason", "")}, learning=summary(ws))


def summary(ws):
    m = model(ws)
    return {"lessons": m["lessons"], "active": bool(m.get("weights")), "needed": MIN_LESSONS, **taste(ws),
            "suggestions": suggestions(ws), "calibration": calibration(ws), "reasons_list": list(REASONS.items())}


@bp.get("/api/ai-jobs/learning")
@login_required
def get_learning(ws):
    book = ws.load("ai_feedback", {}) or {}
    return jsonify(**summary(ws), given={k: ("good" if "good" in v["signals"] else "bad" if "bad" in v["signals"] else "")
                                         for k, v in book.items() if {"good", "bad"} & set(v.get("signals", {}))})


@bp.post("/api/ai-jobs/learning/suggestion/<path:sid>")
@login_required
def decide_suggestion(ws, sid):
    rules = apply_suggestion(ws, sid, bool(C().body().get("accept")))
    return jsonify(rules=rules, learning=summary(ws))


@bp.post("/api/ai-jobs/learning/reset")
@login_required
def reset_learning(ws):
    with ws.lock:
        ws.save("ai_feedback", {})
        ws.save("ai_learn_decided", {})
    return jsonify(learning=summary(ws))
