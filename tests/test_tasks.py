"""Durable task queue (task PH1-04; reliability suite QA-03): atomic claims, leases, heartbeats, recovery after a
worker stops, retries, dead letters, idempotency, cancellation, deadlines, stale writers and user isolation."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import app as A
from conftest import make_user
from infra import tasks


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Each test sees only its own tasks, with fast backoff, and its own handlers."""
    A.M.tasks.delete_many({})
    A.M.task_events.delete_many({})
    monkeypatch.setattr(tasks, "RETRY_BASE_SECONDS", 0.05)
    monkeypatch.setattr(tasks, "RETRY_MAX_SECONDS", 0.2)
    monkeypatch.setattr(tasks, "HANDLERS", {})
    yield
    A.M.tasks.delete_many({})
    A.M.task_events.delete_many({})


def handler(kind, fn):
    tasks.register(kind, fn)
    return kind


def drain(kinds=None, lease=5, limit=100):
    """Run tasks until none is runnable right now."""
    out = []
    for _ in range(limit):
        r = tasks.run_once("w", kinds, lease)
        if r is None:
            break
        out.append(r)
    return out


def wait_until_runnable(tid, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        doc = A.M.tasks.find_one({"_id": tid})
        if doc["status"] != tasks.QUEUED or doc["run_after"] <= tasks._now():
            return
        time.sleep(0.02)


def test_enqueue_seals_input_and_runs_to_done():
    uid, _ = make_user()
    handler("echo", lambda t: {"got": t.input["x"], "uid": t.uid})
    tid = tasks.enqueue(uid, "echo", {"x": "PRIVATE-RESUME-LINE"})
    raw = A.M.tasks.find_one({"_id": tid})
    assert "PRIVATE-RESUME-LINE" not in repr(raw)  # content is sealed in the database
    assert drain() == [(tid, tasks.DONE)]
    v = tasks.get(uid, tid)
    assert v["status"] == "done" and v["result"] == {"got": "PRIVATE-RESUME-LINE", "uid": uid} and v["attempts"] == 1
    assert [e["event"] for e in tasks.events(uid, tid)] == ["enqueued", "claimed", "done"]


def test_unknown_kind_bad_input_and_missing_user_are_refused():
    uid, _ = make_user()
    with pytest.raises(ValueError):
        tasks.enqueue(uid, "nobody-handles-this")
    handler("k", lambda t: None)
    for bad in (dict(idempotency_key="has space"), dict(max_attempts=0), dict(max_attempts=99)):
        with pytest.raises(ValueError):
            tasks.enqueue(uid, "k", **bad)
    with pytest.raises(ValueError):
        tasks.enqueue("", "k")


def test_many_workers_claim_each_task_exactly_once():
    uid, _ = make_user()
    ran, mu = [], threading.Lock()

    def fn(t):
        with mu:
            ran.append(t.id)
    handler("count", fn)
    ids = {tasks.enqueue(uid, "count", {"i": i}) for i in range(200)}

    def worker(n):
        done = 0
        while tasks.run_once(f"w{n}", lease=10) is not None:
            done += 1
        return done
    with ThreadPoolExecutor(20) as pool:
        counts = list(pool.map(worker, range(20)))
    assert sum(counts) == 200 and len(ran) == 200 and set(ran) == ids
    assert A.M.tasks.count_documents({"status": "done"}) == 200
    assert all(d["attempts"] == 1 for d in A.M.tasks.find({}, {"attempts": 1}))


def test_priority_then_age_order():
    uid, _ = make_user()
    handler("p", lambda t: None)
    low = tasks.enqueue(uid, "p", priority=0)
    high = tasks.enqueue(uid, "p", priority=5)
    low2 = tasks.enqueue(uid, "p", priority=0)
    assert [r[0] for r in drain()] == [high, low, low2]


def test_idempotency_key_returns_the_same_task_even_under_a_race():
    uid, _ = make_user()
    other, _ = make_user()
    handler("send", lambda t: None)
    with ThreadPoolExecutor(16) as pool:
        ids = set(pool.map(lambda _: tasks.enqueue(uid, "send", {"to": "x"}, idempotency_key="apply:job42"), range(32)))
    assert len(ids) == 1 and A.M.tasks.count_documents({"uid": uid}) == 1
    drain()
    assert tasks.enqueue(uid, "send", idempotency_key="apply:job42") in ids  # still the same, even once done
    assert tasks.enqueue(other, "send", idempotency_key="apply:job42") not in ids  # keys are per user


def test_crashed_worker_task_is_recovered_and_the_stale_worker_cannot_write():
    uid, _ = make_user()
    handler("job", lambda t: "second worker's result")
    tid = tasks.enqueue(uid, "job")
    stale = tasks.claim("crashed-worker", lease=0.3)  # claimed, then the worker "dies" (no heartbeat)
    time.sleep(0.45)
    assert drain(lease=5) == [(tid, tasks.DONE)]  # recovered by the next claim and finished once
    with pytest.raises(tasks.LeaseLost):
        stale.complete("stale result")
    with pytest.raises(tasks.LeaseLost):
        stale.fail("stale failure")
    v = tasks.get(uid, tid)
    assert v["result"] == "second worker's result" and v["attempts"] == 2
    assert "lease_expired" in [e["event"] for e in tasks.events(uid, tid)]


def test_a_paused_worker_whose_lease_ran_out_writes_nothing_even_before_reclaim():
    uid, _ = make_user()
    handler("job", lambda t: None)
    tid = tasks.enqueue(uid, "job")
    t = tasks.claim("slow", lease=0.2)
    time.sleep(0.3)  # nobody reclaimed it yet, but the lease has run out
    with pytest.raises(tasks.LeaseLost):
        t.complete("too late")
    assert A.M.tasks.find_one({"_id": tid})["status"] == "running"  # left for recovery, not overwritten


def test_heartbeat_keeps_a_long_task_alive_past_its_lease():
    uid, _ = make_user()

    def slow(t):
        time.sleep(1.0)  # well past the 0.3 s lease; the heartbeat renews it
        return "finished"
    handler("slow", slow)
    tid = tasks.enqueue(uid, "slow")
    result = {}
    th = threading.Thread(target=lambda: result.update(r=tasks.run_once("a", lease=0.3)))
    th.start()
    time.sleep(0.5)
    assert tasks.run_once("b", lease=0.3) is None  # never reclaimed while alive
    th.join()
    assert result["r"] == (tid, tasks.DONE) and tasks.get(uid, tid)["attempts"] == 1


def test_retries_with_backoff_then_success():
    uid, _ = make_user()
    calls = []

    def flaky(t):
        calls.append(t.attempt)
        if t.attempt < 3:
            raise ConnectionError("provider down")
        return "ok"
    handler("flaky", flaky)
    tid = tasks.enqueue(uid, "flaky", max_attempts=3)
    assert drain() == [(tid, tasks.QUEUED)]
    doc = A.M.tasks.find_one({"_id": tid})
    assert doc["run_after"] > tasks._now() and doc["error_class"] == "connectionerror"
    assert tasks.run_once() is None  # not before its backoff
    for _ in range(2):
        wait_until_runnable(tid)
        drain()
    assert calls == [1, 2, 3] and tasks.get(uid, tid)["status"] == "done"
    ev = [e for e in tasks.events(uid, tid) if e["event"] == "retry_scheduled"]
    assert [e["backoff_seconds"] for e in ev] == [0.05, 0.1]


def test_dead_letter_after_max_attempts():
    uid, _ = make_user()

    def always(t):
        raise RuntimeError("boom")
    handler("bad", always)
    tid = tasks.enqueue(uid, "bad", max_attempts=2)
    drain()
    wait_until_runnable(tid)
    drain()
    v = tasks.get(uid, tid)
    assert v["status"] == "dead_letter" and v["attempts"] == 2 and "boom" in v["error"]


def test_task_that_keeps_crashing_workers_ends_in_dead_letter():
    uid, _ = make_user()
    handler("crash", lambda t: None)
    tid = tasks.enqueue(uid, "crash", max_attempts=2)
    for _ in range(2):
        assert tasks.claim("dies", lease=0.2) is not None
        time.sleep(0.3)
    tasks.recover()
    v = tasks.get(uid, tid)
    assert v["status"] == "dead_letter" and v["error_class"] == "lease_expired" and tasks.run_once() is None


def test_permanent_and_validation_errors_are_not_retried():
    uid, _ = make_user()

    def refuse(t):
        raise tasks.Permanent("Not allowed for this user.", "permission_denied")
    handler("refuse", refuse)
    tid = tasks.enqueue(uid, "refuse", max_attempts=5)
    assert drain() == [(tid, tasks.FAILED)]
    v = tasks.get(uid, tid)
    assert v["error_class"] == "permission_denied" and v["attempts"] == 1

    def invalid(t):
        raise A.Invalid("bad input")
    handler("invalid", invalid)
    tid2 = tasks.enqueue(uid, "invalid")
    assert drain() == [(tid2, tasks.FAILED)] and tasks.get(uid, tid2)["error_class"] == "invalid"


def test_cancel_queued_task_never_runs():
    uid, _ = make_user()
    ran = []
    handler("c", lambda t: ran.append(1))
    tid = tasks.enqueue(uid, "c")
    assert tasks.cancel(uid, tid)["status"] == "cancelled"
    assert drain() == [] and ran == []
    assert tasks.cancel(uid, tid)["status"] == "cancelled"  # idempotent


def test_cancel_running_task_stops_at_its_next_checkpoint():
    uid, _ = make_user()
    started, steps = threading.Event(), []

    def long(t):
        for i in range(100):
            started.set()
            t.checkpoint()
            steps.append(i)
            time.sleep(0.02)
        return "finished"
    handler("long", long)
    tid = tasks.enqueue(uid, "long")
    th = threading.Thread(target=lambda: tasks.run_once("w", lease=5))
    th.start()
    started.wait(2)
    assert tasks.cancel(uid, tid)["cancel_requested"]
    th.join(5)
    v = tasks.get(uid, tid)
    assert v["status"] == "cancelled" and len(steps) < 100
    assert "cancel_requested" in [e["event"] for e in tasks.events(uid, tid)]


def test_cancelled_task_whose_worker_died_is_not_run_again():
    uid, _ = make_user()
    handler("c", lambda t: "ran")
    tid = tasks.enqueue(uid, "c")
    tasks.claim("dies", lease=0.2)
    tasks.cancel(uid, tid)
    time.sleep(0.3)
    assert drain() == [] and tasks.get(uid, tid)["status"] == "cancelled"


def test_deadline_fails_queued_task_and_stops_a_running_one():
    uid, _ = make_user()
    handler("d", lambda t: "ran")
    late = tasks.enqueue(uid, "d", deadline=0.05)
    time.sleep(0.1)
    assert drain() == []
    assert tasks.get(uid, late)["status"] == "failed" and tasks.get(uid, late)["error_class"] == "deadline"

    def slow(t):
        while True:
            t.checkpoint()
            time.sleep(0.02)
    handler("slow", slow)
    tid = tasks.enqueue(uid, "slow", deadline=0.3)
    assert drain() == [(tid, tasks.FAILED)] and tasks.get(uid, tid)["error_class"] == "deadline"


def test_retry_is_not_scheduled_past_the_deadline(monkeypatch):
    uid, _ = make_user()
    monkeypatch.setattr(tasks, "RETRY_BASE_SECONDS", 1.0)  # the retry would land after the deadline
    monkeypatch.setattr(tasks, "RETRY_MAX_SECONDS", 1.0)

    def bad(t):
        raise RuntimeError("x")
    handler("b", bad)
    tid = tasks.enqueue(uid, "b", max_attempts=5, deadline=0.5)
    assert drain() == [(tid, tasks.FAILED)] and tasks.get(uid, tid)["error_class"] == "deadline"


def test_users_cannot_see_or_cancel_each_others_tasks():
    a, _ = make_user()
    b, _ = make_user()
    handler("x", lambda t: None)
    tid = tasks.enqueue(a, "x", {"secret": "a's data"})
    assert tasks.get(b, tid) is None and tasks.events(b, tid) == []
    assert tasks.cancel(b, tid) is None and tasks.get(a, tid)["status"] == "queued"
    assert tasks.get(a, "not-an-id") is None and tasks.cancel(a, "$where") is None
    with pytest.raises(ValueError):
        tasks.enqueue(b, "x", parent_id=tid)  # a child task always belongs to its parent's user


def test_child_tasks_inherit_root_and_depth():
    uid, _ = make_user()
    handler("x", lambda t: None)
    root = tasks.enqueue(uid, "x")
    child = tasks.enqueue(uid, "x", parent_id=root)
    grandchild = tasks.enqueue(uid, "x", parent_id=child)
    g = tasks.get(uid, grandchild)
    assert (g["parent_id"], g["root_id"], g["depth"]) == (child, root, 2)
    assert tasks.get(uid, child)["root_id"] == root and tasks.get(uid, root)["depth"] == 0


def test_worker_loop_runs_tasks_and_stops():
    uid, _ = make_user()
    done = threading.Event()
    handler("loop", lambda t: done.set())
    stop = threading.Event()
    th = threading.Thread(target=tasks.work, args=(stop,), kwargs={"idle": 0.05})
    th.start()
    tasks.enqueue(uid, "loop")
    assert done.wait(3)
    stop.set()
    th.join(3)
    assert not th.is_alive()


def test_account_deletion_removes_tasks_and_events():
    uid, _ = make_user()
    handler("x", lambda t: None)
    tid = tasks.enqueue(uid, "x")
    running = tasks.claim("w")
    A.delete_user_data(uid)
    assert A.M.tasks.count_documents({"uid": uid}) == 0 and A.M.task_events.count_documents({"uid": uid}) == 0
    with pytest.raises(tasks.LeaseLost):
        running.complete("after deletion")  # a worker still running for a deleted account writes nothing
    assert tid == running.id


def test_error_messages_are_sealed_but_readable_by_the_owner():
    uid, _ = make_user()

    def bad(t):
        raise tasks.Permanent("failed for PRIVATE-COMPANY-NAME")
    handler("bad", bad)
    tid = tasks.enqueue(uid, "bad")
    drain()
    assert "PRIVATE-COMPANY-NAME" not in repr(A.M.tasks.find_one({"_id": tid}))
    assert "PRIVATE-COMPANY-NAME" in tasks.get(uid, tid)["error"]
    assert "error" not in tasks.view_of(A.M.tasks.find_one({"_id": tid}), with_content=False)
