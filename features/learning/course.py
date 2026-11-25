"""The 90-day interview course, daily DSA and spaced review for each user (content and scheduling from Morrow, MIT).

The user's progress is Morrow's learning record (content/learning_memory.empty()), kept encrypted in the workspace
under "learning". A day is "today" until it is marked done, so a missed calendar day never skips a lesson; one day
can be completed per calendar date, as in Morrow."""

import datetime as dt
import re

from flask import jsonify, request

from bridge import login_required
from features.learning.common import C, bp
from features.learning.content import (dsa_basics, dsa_depth, interview_ai_basics, interview_ai_interviews, interview_course,
                                       interview_engineering, interview_fullstack_basics, interview_fullstack_interviews,
                                       interview_practice, interview_review, interview_system_basics,
                                       interview_system_interviews, learning_activity, learning_memory)

TOTAL = interview_course.TOTAL_DAYS
TRACKS = {"ai_ml": ("AI & machine learning", interview_ai_basics, interview_ai_interviews),
          "system_design": ("System design", interview_system_basics, interview_system_interviews),
          "full_stack": ("Full-stack development", interview_fullstack_basics, interview_fullstack_interviews)}

# The syllabus names a DSA topic per day; this picks the matching lesson from Morrow's 22 DSA topics.
DSA_TOPICS = [
    (r"binary search tree", "binary-search-trees"), (r"binary search on|boundar", "search-boundaries"),
    (r"binary search", "binary-search"), (r"two pointers? and window|sliding|monotonic queue", "sliding-window"),
    (r"two pointer", "two-pointers"), (r"monotonic stack|stack|queue", "stacks"), (r"linked list", "linked-lists"),
    (r"heap and interval|interval", "intervals"), (r"scheduling|heap pattern", "heap-patterns"), (r"heap|top k", "heaps"),
    (r"topological|union|cycle|shortest|graph path", "graph-paths"),
    (r"graph|breadth|depth|bfs|dfs|adjacency|reachab", "graph-traversal"),
    (r"pruning|constraint", "constraint-search"), (r"backtrack", "backtracking"), (r"grid", "dp-grids"),
    (r"dynamic programming|knapsack|subsequence|repeated subproblem", "dynamic-programming"), (r"greedy", "greedy"),
    (r"bit", "bits"), (r"trie|string matching", "tries"), (r"tree|recursion|travers", "tree-traversal"),
    (r"hash|map|set|duplicate|frequenc", "hashing"), (r"array|prefix|string|sort", "array-patterns"),
]
DSA_IDS = list(dsa_depth._TOPICS)


def dsa_for(day):
    topic = interview_course.day_spec(day)["dsa"]
    key = next((k for pat, k in DSA_TOPICS if re.search(pat, topic, re.I)), DSA_IDS[(day - 1) % len(DSA_IDS)])
    return {"topic": topic, "id": key, "basics": dsa_basics.lesson(key), "depth": dsa_depth.lesson(key)}


def questions_for(day):
    return [q for q in interview_practice.bank_rows() if q["day"] == day]


def lesson(day):
    """Everything for one course day: the plan, each track's beginner and interview lessons, the weekly notebook and
    lab, the DSA lesson and the day's interview questions."""
    spec = interview_course.day_spec(day)
    tracks = []
    for key, (title, basics, interviews) in TRACKS.items():
        tracks.append({"key": key, "title": title, "topic": spec[key], "basics": basics.lesson(day),
                       "interview": interviews.lesson(day), "notebook": interview_engineering.notebook(day, key),
                       "job_use": (spec.get("job_relevance") or {}).get(key, "")})
    return {"day": day, "spec": spec, "tracks": tracks, "lab": interview_engineering.implementation_lab(day),
            "dsa": dsa_for(day), "questions": questions_for(day), "week": interview_course.week_spec(spec["week"])}


def record(ws):
    data = ws.load("learning", None) or learning_memory.empty()
    for k, v in learning_memory.empty().items():
        data.setdefault(k, v)
    return data


def today():
    return dt.date.today()


def due_items(data, limit=10):
    by_id = {q["id"]: q for q in interview_practice.bank_rows()}
    return [{**by_id[i], "schedule": data["reviews"].get(i)} for i in interview_review.due(data, today(), limit) if i in by_id]


def state(ws):
    data = record(ws)
    done = interview_course.completed_days(data)
    current = interview_course.next_day(data)
    marked_today = (data.get("interview_course") or {}).get("marked", {}).get(today().isoformat())
    act = learning_activity.summary(data, today())
    dsa_today = data["dsa"]["days"].get(today().isoformat()) or {}
    return {"course": interview_course.course_info(), "done": done, "current": current, "finished": current is None,
            "completed_today": marked_today, "due": due_items(data), "due_count": len(interview_review.due(data, today(), 999)),
            "activity": act, "dsa_today": dsa_today}


@bp.get("/api/learning")
@login_required
def get_state(ws):
    s = state(ws)
    day = s["completed_today"] or s["current"] or TOTAL
    return jsonify({**s, "lesson": lesson(day)})


@bp.get("/api/learning/syllabus")
@login_required
def get_syllabus(ws):
    data = record(ws)
    done = set(interview_course.completed_days(data))
    weeks = []
    for w in range(1, 14):
        spec = interview_course.week_spec(w)
        days = [d for d in range(1, TOTAL + 1) if interview_course.day_spec(d)["week"] == w]
        weeks.append({"week": w, "title": spec.get("title"), "project": spec.get("project"),
                      "days": [{"day": d, "title": interview_course.day_spec(d)["title"], "done": d in done} for d in days]})
    return jsonify(weeks=weeks, phases=interview_course.course_info()["phases"])


@bp.get("/api/learning/day/<int:day>")
@login_required
def get_day(ws, day):
    if not 1 <= day <= TOTAL:
        raise C().Invalid("Pick a day from 1 to 90.", status=404)
    return jsonify(lesson=lesson(day), done=day in interview_course.completed_days(record(ws)))


@bp.post("/api/learning/day/<int:day>/complete")
@login_required
def complete_day(ws, day):
    core = C()
    if not 1 <= day <= TOTAL:
        raise core.Invalid("Pick a day from 1 to 90.", status=404)
    done = core.body().get("done", True) is not False
    with ws.lock:
        data = record(ws)
        marked = (data.get("interview_course") or {}).get("marked", {})
        if done and marked.get(today().isoformat()) not in (None, day):
            raise core.Invalid("You've already finished a day today. Come back tomorrow for the next one; reviews are open.")
        interview_course.record_completion(data, day, done)
        ws.save("learning", data)
    return jsonify(state(ws))


@bp.post("/api/learning/dsa/complete")
@login_required
def complete_dsa(ws):
    """Mark today's DSA topic practised (Morrow's 'dsa' activity: 50 XP)."""
    with ws.lock:
        data = record(ws)
        s = state(ws)
        day = s["completed_today"] or s["current"] or TOTAL
        d = dsa_for(day)
        key = today().isoformat()
        entry = data["dsa"]["days"].setdefault(key, {"topic": d["id"], "read": True, "practiced": []})
        if not entry.get("completed_at"):
            entry["completed_at"] = learning_memory.stamp()
            learning_memory.record(data, "dsa", d["id"] + ":" + key, "DSA: " + d["topic"], True)
        ws.save("learning", data)
    return jsonify(state(ws))


@bp.post("/api/learning/review/<qid>")
@login_required
def grade_review(ws, qid):
    core = C()
    rating = str(core.body().get("rating", ""))
    if rating not in interview_review.RATINGS and rating not in interview_review.RUBRIC:
        raise core.Invalid("Grade with again, hard, good or easy.")
    if not any(q["id"] == qid for q in interview_practice.bank_rows()):
        raise core.Invalid("That question isn't in the course.", status=404)
    with ws.lock:
        data = record(ws)
        interview_review.grade(data, qid, rating, today())
        ws.save("learning", data)
    return jsonify(state(ws))


@bp.get("/api/learning/questions")
@login_required
def search_questions(ws):
    core = C()
    q, track = (request.args.get("q") or "").strip()[:160], request.args.get("track") or ""
    try:
        week = int(request.args.get("week") or 0)
        page = max(1, int(request.args.get("page") or 1))
    except ValueError:
        raise core.Invalid("Week and page must be numbers.")
    terms = q.casefold().split()
    rows = [r for r in interview_practice.bank_rows() if (not track or r["track"] == track) and (not week or r["week"] == week)
            and all(t in (r["topic"] + " " + r["question"] + " " + r.get("follow_up", "")).casefold() for t in terms)]
    size = 12
    return jsonify(total=len(rows), page=page, pages=max(1, -(-len(rows) // size)), items=rows[(page - 1) * size: page * size],
                   tracks=[{"key": k, "title": t} for k, t, _ in interview_practice.TRACKS])


@bp.get("/api/learning/assessment/<int:week>")
@login_required
def get_assessment(ws, week):
    if not 1 <= week <= 13:
        raise C().Invalid("Pick a week from 1 to 13.", status=404)
    return jsonify(interview_practice.weekly_assessment(week))
