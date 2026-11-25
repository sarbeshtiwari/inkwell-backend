"""Phase 0 step 2: recurring jobs coordinated across processes (shared schedule + leases). Separate processes where
the claim is about processes."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import DB_NAME, H, make_user

from core.db import M
from infra import locks, schedule

BACKEND = Path(__file__).resolve().parents[1]
HEAD = f"""
import os, sys, time, json
sys.path.insert(0, {str(BACKEND)!r})
from core.db import M
M.connect()
from infra import locks, schedule
"""


def _spawn(body, **env):
    e = {**os.environ, "MONGODB_DB": DB_NAME, "INKWELL_NO_DOTENV": "1", **{k: str(v) for k, v in env.items()}}
    return subprocess.Popen([sys.executable, "-c", HEAD + body], env=e, cwd=BACKEND, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)


def _uid():
    return make_user()[0]


LOOP = """
import bridge
name, run_for, work = {name!r}, {run_for}, {work}
def job():
    inside = M.step2.find_one_and_update({{"_id": "in-" + name}}, {{"$inc": {{"n": 1}}}}, upsert=True, return_document=True)
    M.step2.insert_one({{"loop": name, "pid": os.getpid(), "at": time.time(), "overlap": inside["n"] > 1}})
    time.sleep(work)
    M.step2.update_one({{"_id": "in-" + name}}, {{"$inc": {{"n": -1}}}})
bridge.every({every}, job, name)
time.sleep(run_for)
"""


def test_a_loop_runs_once_per_interval_across_processes_never_overlapping():
    name = "t-loop-" + os.urandom(3).hex()
    procs = [_spawn(LOOP.format(name=name, run_for=6.5, work=0.3, every=1)) for _ in range(3)]
    for p in procs:
        assert p.wait(timeout=60) == 0, p.stdout.read()
    runs = list(M.step2.find({"loop": name}).sort("at", 1))
    assert 4 <= len(runs) <= 8, len(runs)            # ~once per second in total; uncoordinated would be ~18
    assert not any(r["overlap"] for r in runs)
    assert len({r["pid"] for r in runs}) >= 1


def test_a_crashed_leader_is_replaced_after_its_lease_runs_out():
    """The process running a long job is killed mid-run: nobody overlaps it while its lease lasts, then the job runs
    again in another process."""
    name = "t-crash-" + os.urandom(3).hex()
    leader = _spawn(LOOP.format(name=name, run_for=30, work=20, every=1), LOCK_LEASE_SECONDS=2, LOCK_RENEW_SECONDS=0.5)
    end = time.time() + 15
    while not M.step2.find_one({"loop": name}) and time.time() < end:
        time.sleep(0.1)
    first = M.step2.find_one({"loop": name})
    assert first and first["pid"] == leader.pid
    others = [_spawn(LOOP.format(name=name, run_for=8, work=0.2, every=1), LOCK_LEASE_SECONDS=2) for _ in range(2)]
    time.sleep(1.5)
    assert M.step2.count_documents({"loop": name}) == 1   # still leased by the leader: no overlap
    leader.kill()
    leader.wait()
    M.step2.update_one({"_id": "in-" + name}, {"$inc": {"n": -1}})   # the killed run never decremented
    for p in others:
        p.wait(timeout=60)
    later = list(M.step2.find({"loop": name, "pid": {"$ne": leader.pid}}))
    assert later and not any(r["overlap"] for r in later)


CLAIM = """
name, n = {name!r}, 0
time.sleep(max(0, {start_at} - time.time()))
got = schedule.claim(name, 60)
once = schedule.once(name + "-once", "user1")
print(json.dumps([got, once]), flush=True)
"""


def test_exactly_one_process_claims_each_slot_and_once_is_once():
    name = "t-claim-" + os.urandom(3).hex()
    start = time.time() + 3
    procs = [_spawn(CLAIM.format(name=name, start_at=start)) for _ in range(8)]
    results = [json.loads(p.communicate(timeout=60)[0].strip().splitlines()[-1]) for p in procs]
    assert sum(r[0] for r in results) == 1 and sum(r[1] for r in results) == 1
    assert not schedule.claim(name, 60)                    # next slot isn't open yet
    schedule.soon(name, 0)
    assert schedule.claim(name, 60)                        # ... until someone asks for it sooner


def test_postpone_and_last_scan_time_are_stored():
    uid = _uid()
    schedule.postpone("reply-check", 170, uid)
    assert not schedule.claim("reply-check", 170, uid)
    assert schedule.last_at("reply-check", uid) is not None
    assert M.schedules.find_one({"_id": schedule.key("reply-check", uid)})["uid"] == uid


BOUNCE = """
from core import bounces
calls = []
bounces.check_bounces = lambda ws, days=4: M.step2.update_one({{"_id": "bounce-" + ws.uid}}, {{"$inc": {{"n": 1}}}}, upsert=True)
import bridge
class _B:
    @staticmethod
    def with_deadline(seconds, fn, *a, **k):
        return fn(*a, **k)
bounces.bridge_mod = lambda: _B
time.sleep(max(0, {start_at} - time.time()))
bounces.bounce_tick()
"""


def test_bounce_watcher_checks_each_account_once_when_several_processes_tick():
    uid = _uid()
    M.send_log.insert_one({"uid": uid, "ts": time.time(), "mid": "x", "day": "d"})
    start = time.time() + 3
    procs = [_spawn(BOUNCE.format(start_at=start)) for _ in range(4)]
    for p in procs:
        out = p.communicate(timeout=60)[0]
        assert p.returncode == 0, out
    assert M.step2.find_one({"_id": "bounce-" + uid})["n"] == 1
    from core.bounces import check_bounces_soon
    check_bounces_soon(uid, 0)                             # after a send: due again right away, for every process
    p = _spawn(BOUNCE.format(start_at=time.time()))
    assert p.wait(timeout=60) == 0
    assert M.step2.find_one({"_id": "bounce-" + uid})["n"] == 2


@pytest.mark.parametrize("which", ["bounces", "job_alerts"])
def test_per_account_lock_is_held_for_the_whole_check_even_after_the_caller_stops_waiting(user, monkeypatch, which):
    import bridge
    from core import bounces
    from features import jobs
    ws = user["ws"]
    ws.save("profile", {**ws.profile(), "smtp_password": "x", "email": "me@example.com"})
    release = threading.Event()

    def slow_imap(*a, **k):
        release.wait(20)
        raise OSError("mailbox closed")
    monkeypatch.setattr(bounces, "imap_connect", slow_imap)
    monkeypatch.setattr(jobs.C(), "imap_connect", slow_imap, raising=False)
    fn = (lambda: bounces.check_bounces(ws, days=1)) if which == "bounces" else (lambda: jobs.scan_alerts(ws, days=1))
    with pytest.raises(bridge.Timeout):
        bridge.with_deadline(0.3, fn)                       # the caller gives up; the check carries on in its thread
    with pytest.raises(Exception) as e:
        fn()                                                # so a second check is refused, not run alongside it
    assert getattr(e.value, "status", None) == 409 and "already running" in str(getattr(e.value, "message", e.value))
    release.set()
    lease_name = "bounce-check" if which == "bounces" else "job-alert-scan"
    end = time.time() + 10
    while locks.holder(lease_name, ws.uid) and time.time() < end:
        time.sleep(0.05)
    assert locks.holder(lease_name, ws.uid) is None        # freed when the check itself ended


def test_manual_bounce_check_while_one_runs_is_refused(user):
    ws = user["ws"]
    lease = locks.acquire("bounce-check", ws.uid)
    try:
        r = user["client"].post("/api/bounces/check", json={}, headers=H)
        assert r.status_code in (400, 409)
    finally:
        lease.release()
