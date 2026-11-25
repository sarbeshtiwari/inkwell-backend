"""Phase 0 step 2: shared locks, progress and cancellation. Multi-process behaviour is tested with real separate
processes (and one test stops and restarts a real MongoDB server); single-process tests cover the rest."""
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from conftest import DB_NAME, H, client_for, make_user
from pymongo.errors import ServerSelectionTimeoutError

from core.db import M
from core.workspace import Workspace
from infra import audit, locks, operations, rbac

BACKEND = Path(__file__).resolve().parents[1]
HEAD = f"""
import os, sys, time, json
sys.path.insert(0, {str(BACKEND)!r})
from core.db import M
M.connect()
from infra import locks, operations
"""


def _env(**extra):
    env = {**os.environ, "MONGODB_DB": DB_NAME, "INKWELL_NO_DOTENV": "1"}
    env.update({k: str(v) for k, v in extra.items()})
    return env


def _spawn(body, **env):
    return subprocess.Popen([sys.executable, "-c", HEAD + body], env=_env(**env), cwd=BACKEND,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _until(cond, timeout=20.0, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = cond()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("condition not met in time")


def _uid():
    return make_user()[0]


# ================================================================ locks

COUNTER = """
uid, n = {uid!r}, {n}
for _ in range(n):
    with locks.acquire("counter", uid, wait=30):
        v = M.step2.find_one({{"_id": "c"}})["v"]      # read ... (deliberately not atomic)
        time.sleep(0.002)
        M.step2.update_one({{"_id": "c"}}, {{"$set": {{"v": v + 1}}}})   # ... then write
print("ok")
"""


def test_two_processes_competing_for_one_lock_never_overlap():
    uid = _uid()
    M.step2.replace_one({"_id": "c"}, {"v": 0}, upsert=True)
    procs = [_spawn(COUNTER.format(uid=uid, n=60)) for _ in range(2)]
    outs = [p.communicate(timeout=180)[0] for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    assert M.step2.find_one({"_id": "c"})["v"] == 120  # every read-modify-write was exclusive


def test_independent_users_get_independent_locks():
    a, b = _uid(), _uid()
    held = _spawn(f"lease = locks.acquire('counter', {a!r})\nprint('held', flush=True)\ntime.sleep(30)")
    try:
        assert held.stdout.readline().strip() == "held"
        assert locks.try_acquire("counter", a) is None          # same user, same resource: taken
        lb = locks.try_acquire("counter", b)                   # another user: free
        assert lb is not None
        lb.release()
        lg = locks.try_acquire("counter")                      # global resource of the same name: separate too
        assert lg is not None
        lg.release()
    finally:
        held.kill()
        held.wait()


def test_lease_expires_and_is_reacquired_with_a_higher_fence():
    uid = _uid()
    first = locks.acquire("res", uid, ttl=0.6)
    locks._RENEWER.discard(first)  # behave like a holder that stopped renewing
    assert locks.try_acquire("res", uid) is None
    time.sleep(0.8)
    second = locks.acquire("res", uid)
    assert second.fence > first.fence and second.token != first.token
    with pytest.raises(locks.LeaseLost):
        first.ensure()
    second.release()


def test_renewal_keeps_a_lease_alive(monkeypatch):
    monkeypatch.setattr(locks, "RENEW_SECONDS", 0.3)
    uid = _uid()
    lease = locks.acquire("res", uid, ttl=1.5)
    time.sleep(4)  # well past the original 1.5 s
    assert lease.held and locks.holder("res", uid)["token"] == lease.token
    assert locks.try_acquire("res", uid) is None
    lease.ensure()
    lease.release()


def test_only_the_owner_can_release_and_release_is_idempotent():
    uid = _uid()
    old = locks.acquire("res", uid, ttl=0.5)
    locks._RENEWER.discard(old)
    time.sleep(0.7)
    new = locks.acquire("res", uid)
    old.release()  # the stale owner "releases": must not free the new owner's lock
    old.release()
    assert locks.holder("res", uid)["token"] == new.token
    new.release()
    new.release()
    assert locks.holder("res", uid) is None


STALE_WRITER = """
from core.workspace import Workspace
ws = Workspace({uid!r})
with ws.lock:
    print("locked", flush=True)
    sys.stdin.readline()            # paused here (SIGSTOP) until the test lets it go on
    try:
        ws.save("step2", {{"by": "stale"}})
        print("WROTE", flush=True)
    except locks.LeaseLost:
        print("refused", flush=True)
"""


def test_stale_owner_cannot_write_after_losing_its_lease():
    """A process holding the account lock is frozen past its lease; another takes the lock and writes; when the frozen
    one wakes up, its write is refused and the newer data stays."""
    uid = _uid()
    p = subprocess.Popen([sys.executable, "-c", HEAD + STALE_WRITER.format(uid=uid)], env=_env(LOCK_LEASE_SECONDS=2,
                         LOCK_RENEW_SECONDS=0.5, LOCK_SAFETY_SECONDS=0.5), cwd=BACKEND, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert p.stdout.readline().strip() == "locked"
        os.kill(p.pid, signal.SIGSTOP)   # frozen: no renewals, no writes
        ws = Workspace(uid)
        ws.lock.wait = 10
        with ws.lock:                    # waits for the frozen holder's lease to run out
            ws.save("step2", {"by": "new"})
        os.kill(p.pid, signal.SIGCONT)
        p.stdin.write("go\n")
        p.stdin.flush()
        out = p.communicate(timeout=30)[0]
    finally:
        if p.poll() is None:
            os.kill(p.pid, signal.SIGCONT)
            p.kill()
    assert "refused" in out and "WROTE" not in out, out
    assert Workspace(uid).load("step2", {}) == {"by": "new"}


EXACT_RACE = """
real = locks.transactions_supported          # runs right after A's local lease check, before its transaction
def paused():
    print("A-checked", locks.current({uid!r})[0].fence, flush=True)
    sys.stdin.readline()                     # A is frozen here, right before the database write
    return real()
locks.transactions_supported = paused
from core.workspace import Workspace
ws = Workspace({uid!r})
try:
    with ws.lock:
        ws.save("protected", {{"by": "A (stale)"}})
    print("A-WROTE", flush=True)
except locks.LeaseLost:
    print("A-REJECTED", flush=True)
"""


def test_exact_stale_writer_race_is_rejected():
    """1. A takes the account lock (fence N). 2. A pauses past its lease. 3. B takes the lock (fence N+1).
    4. B has NOT written the protected record. 5. A resumes and does its old write: it must be rejected."""
    uid = _uid()
    a = subprocess.Popen([sys.executable, "-c", HEAD + EXACT_RACE.format(uid=uid)],
                         env=_env(LOCK_LEASE_SECONDS=2, LOCK_RENEW_SECONDS=0.5, LOCK_SAFETY_SECONDS=0.5), cwd=BACKEND,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        line = a.stdout.readline().split()
        assert line[0] == "A-checked"
        fence_a = int(line[1])
        os.kill(a.pid, signal.SIGSTOP)                       # 2. paused: no renewals
        time.sleep(2.5)                                      #    past the 2 s lease
        b = locks.acquire("workspace", uid, wait=5)          # 3. B: fence N+1
        assert b.fence > fence_a
        assert Workspace(uid).load("protected", None) is None   # 4. B hasn't written it
        os.kill(a.pid, signal.SIGCONT)                       # 5. A resumes
        a.stdin.write("go\n")
        a.stdin.flush()
        out = a.communicate(timeout=30)[0]
        b.release()
    finally:
        if a.poll() is None:
            os.kill(a.pid, signal.SIGCONT)
            a.kill()
    assert "A-REJECTED" in out and "A-WROTE" not in out, out
    assert Workspace(uid).load("protected", None) is None


IN_TXN = """
from pymongo.collection import Collection
real = Collection.replace_one
def paused(self, *a, **k):                   # runs inside A's transaction, after its lease check on the lock record
    if self.name == "kv" and k.get("session") is not None:
        print("A-in-transaction", flush=True)
        sys.stdin.readline()
    return real(self, *a, **k)
Collection.replace_one = paused
from core.workspace import Workspace
ws = Workspace({uid!r})
try:
    with ws.lock:
        ws.save("protected", {{"by": "A"}})
    print("A-WROTE", flush=True)
except locks.LeaseLost:
    print("A-REJECTED", flush=True)
except locks.LockUnavailable:
    print("A-ABORTED", flush=True)
"""


def _in_txn_writer(uid, **env):
    p = subprocess.Popen([sys.executable, "-c", HEAD + IN_TXN.format(uid=uid)], env=_env(**env), cwd=BACKEND,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert p.stdout.readline().strip() == "A-in-transaction"
    return p


def test_a_holder_frozen_inside_its_transaction_blocks_takeover_until_it_ends():
    """A has checked its lease inside the transaction and is frozen before writing. Its lease runs out, but nobody can
    take the lock while A's transaction is open (B is told it's busy); when A finishes, its write lands *before* B
    holds the lock, so B sees it. Order is preserved: there is never a moment where B holds the lock and A writes."""
    uid = _uid()
    a = _in_txn_writer(uid, LOCK_LEASE_SECONDS=2, LOCK_RENEW_SECONDS=0.5, LOCK_SAFETY_SECONDS=0.5)
    try:
        os.kill(a.pid, signal.SIGSTOP)
        time.sleep(2.5)                                        # A's lease has expired by the clock
        with pytest.raises(locks.LockTimeout):
            locks.acquire("workspace", uid, wait=2)            # but the record is in A's open transaction: busy
        os.kill(a.pid, signal.SIGCONT)
        a.stdin.write("go\n")
        a.stdin.flush()
        out = a.communicate(timeout=30)[0]
    finally:
        if a.poll() is None:
            os.kill(a.pid, signal.SIGCONT)
            a.kill()
    assert "A-WROTE" in out, out
    b = locks.acquire("workspace", uid, wait=5)
    assert Workspace(uid).load("protected", None) == {"by": "A"}   # B sees A's committed write before acting
    b.release()


def test_a_transaction_stuck_past_its_time_limit_is_aborted_and_writes_nothing():
    uid = _uid()
    a = _in_txn_writer(uid, LOCK_TXN_SECONDS=1.5)
    time.sleep(2.5)                                            # past A's client-side limit for the protected write
    a.stdin.write("go\n")
    a.stdin.flush()
    out = a.communicate(timeout=60)[0]
    assert "A-ABORTED" in out and "A-WROTE" not in out, out
    assert Workspace(uid).load("protected", None) is None
    lease = locks.acquire("workspace", uid, wait=5)            # the lock is usable again
    lease.release()


CONTENDER = """
from pymongo import ReturnDocument
uid, end, got = {uid!r}, time.time() + 4, []
while time.time() < end:
    lease = locks.try_acquire("contended", uid)
    if lease is None:
        time.sleep(0.005)
        continue
    inside = M.step2.find_one_and_update({{"_id": "inside-" + uid}}, {{"$inc": {{"n": 1}}}}, upsert=True,
                                         return_document=ReturnDocument.AFTER)["n"]
    got.append((lease.fence, inside))
    time.sleep(0.002)
    M.step2.update_one({{"_id": "inside-" + uid}}, {{"$inc": {{"n": -1}}}})
    lease.release()
print(json.dumps(got), flush=True)
"""


def test_many_processes_acquiring_at_once_never_overlap_and_fences_are_unique():
    uid = _uid()
    procs = [_spawn(CONTENDER.format(uid=uid)) for _ in range(6)]
    results = []
    for p in procs:
        out = p.communicate(timeout=120)[0]
        assert p.returncode == 0, out
        results += [tuple(x) for x in __import__("json").loads(out.strip().splitlines()[-1])]
    fences = [f for f, _ in results]
    assert len(results) > 50                                   # plenty of real contention
    assert all(inside == 1 for _, inside in results)           # never two holders at once
    assert len(set(fences)) == len(fences)                     # every acquisition got its own fence


def test_transient_transaction_errors_are_retried_and_write_once():
    from pymongo.errors import OperationFailure
    uid = _uid()
    calls = {"n": 0}

    def fn(session):
        calls["n"] += 1
        M.step2.update_one({"_id": "retry-" + uid}, {"$inc": {"n": 1}}, upsert=True, session=session)
        if calls["n"] == 1:
            raise OperationFailure("WriteConflict", code=112, details={"errorLabels": ["TransientTransactionError"]})
    with locks.user_lock(uid):
        locks.guarded_write(uid, fn)
    assert calls["n"] == 2 and M.step2.find_one({"_id": "retry-" + uid})["n"] == 1   # first attempt rolled back


def test_a_failure_inside_the_transaction_rolls_everything_back():
    uid = _uid()
    ws = Workspace(uid)
    ws.save("keep", {"v": 1})
    before = M.locks.find_one({"_id": locks.lock_id("workspace", uid)})

    def fn(session):
        M.kv.update_one({"_id": f"{uid}:keep"}, {"$set": {"uid": uid, "key": "keep", "data": b"half"}}, session=session)
        raise RuntimeError("crash in the middle of a protected write")
    with locks.user_lock(uid):
        with pytest.raises(RuntimeError):
            locks.guarded_write(uid, fn)
    assert ws.load("keep", None) == {"v": 1}                    # the half-done write never became visible
    after = M.locks.find_one({"_id": locks.lock_id("workspace", uid)})
    assert (after or {}).get("last_write_at") == (before or {}).get("last_write_at")


def test_database_failure_during_a_protected_write_writes_nothing(monkeypatch):
    from pymongo.errors import AutoReconnect
    uid = _uid()
    monkeypatch.setattr(locks, "TXN_SECONDS", 0.5)

    def fn(session):
        raise AutoReconnect("connection lost")                # network errors are transient: retried until the limit
    with locks.user_lock(uid):
        with pytest.raises(locks.LockUnavailable):
            locks.guarded_write(uid, fn)
    assert Workspace(uid).load("nothing", None) is None


def test_protected_writes_are_refused_without_transaction_support(monkeypatch):
    uid = _uid()
    ws = Workspace(uid)
    monkeypatch.setattr(locks, "transactions_supported", lambda: False)
    with ws.lock:
        with pytest.raises(locks.LockUnavailable):
            ws.save("guarded", {"v": 1})
    assert ws.load("guarded", None) is None                     # never written unguarded
    ws.save("plain", {"v": 1})                                  # writes outside the lock are unaffected
    assert ws.load("plain", None) == {"v": 1}


def test_portfolio_address_release_is_a_guarded_write(user, monkeypatch):
    from features import portfolio
    c, ws = user["client"], user["ws"]
    assert c.put("/api/portfolio/settings", json={"slug": "first-addr"}, headers=H).status_code == 200
    seen = []
    real = portfolio.guarded_write
    monkeypatch.setattr(portfolio, "guarded_write", lambda uid, fn: (seen.append((uid, bool(locks.current(uid)))), real(uid, fn))[1])
    assert c.put("/api/portfolio/settings", json={"slug": "second-addr"}, headers=H).status_code == 200
    assert seen == [(ws.uid, True)]                             # released inside the account lock's transaction
    assert not M.site.find_one({"_id": "portfolio-slug:first-addr"})
    assert M.site.find_one({"_id": "portfolio-slug:second-addr"})["uid"] == ws.uid


def test_fencing_rejects_a_stale_write_that_slipped_past_the_lease_check():
    """Even if a stale holder passed its local check just before losing the lease, the database refuses its write."""
    uid = _uid()
    ws = Workspace(uid)
    old = locks.acquire("workspace", uid, ttl=0.5)
    locks._RENEWER.discard(old)
    time.sleep(0.7)
    with ws.lock:
        ws.save("fenced", {"v": "new"})
    old.ensure = lambda margin=None: old       # paused right after its check
    locks.push(old)
    try:
        with pytest.raises(locks.LeaseLost):
            ws.save("fenced", {"v": "stale"})
    finally:
        locks.pop(old)
    assert ws.load("fenced", {}) == {"v": "new"}


def test_process_crash_while_holding_a_lock_is_recovered():
    uid = _uid()
    p = _spawn(f"lease = locks.acquire('res', {uid!r})\nprint(lease.fence, flush=True)\nos.kill(os.getpid(), 9)",
               LOCK_LEASE_SECONDS=1.5)
    fence = int(p.stdout.readline())
    assert p.wait(timeout=30) == -9
    assert locks.try_acquire("res", uid) is None             # still leased right after the crash
    lease = locks.acquire("res", uid, wait=5)                 # free once the lease runs out
    assert lease.fence > fence
    lease.release()


def test_database_failure_means_no_lock_and_no_local_fallback(monkeypatch):
    uid = _uid()

    class Down:
        def __getitem__(self, name):
            raise ServerSelectionTimeoutError("no servers")
    real = M.db
    monkeypatch.setattr(M, "db", Down())
    with pytest.raises(locks.LockUnavailable) as e:
        locks.acquire("res", uid)
    assert e.value.status == 503
    monkeypatch.setattr(M, "db", real)                       # database back
    lease = locks.acquire("res", uid)
    lease.release()


def test_a_holder_that_cannot_renew_stops_before_its_lease_ends(monkeypatch):
    monkeypatch.setattr(locks, "RENEW_SECONDS", 0.2)
    monkeypatch.setattr(locks, "SAFETY_SECONDS", 0.5)
    uid = _uid()
    lease = locks.acquire("res", uid, ttl=1.5)

    def fail():
        raise ServerSelectionTimeoutError("no servers")
    monkeypatch.setattr(lease, "renew", fail)
    lease.ensure()
    _until(lambda: not _ok(lease), timeout=3)
    assert lease.remaining() > 0  # it stopped while the lease was still formally valid


def _ok(lease):
    try:
        lease.ensure()
        return True
    except locks.LeaseLost:
        return False


def test_workspace_lock_is_reentrant_and_shared_across_threads():
    uid = _uid()
    ws = Workspace(uid)
    order = []
    with ws.lock:
        with ws.lock:  # re-entrant in the same thread
            ws.save("re", {"n": 1})
        t = threading.Thread(target=lambda: (ws.lock.acquire(), order.append("other"), ws.lock.release()))
        t.start()
        time.sleep(0.3)
        order.append("first")
    t.join(5)
    assert order == ["first", "other"] and locks.holder("workspace", uid) is None


def test_bad_lock_names_are_refused():
    with pytest.raises(ValueError):
        locks.lock_id("Bad Name!")
    with pytest.raises(ValueError):
        locks.lock_id("ok", uid="../x")


# ================================================================ progress

PROGRESS = """
op = operations.start({uid!r}, "inbox", public={{"stage": "Reading", "done": 0, "total": 10}})
with op:
    for i in range(1, 4):
        op.update(public={{"done": i}}, private={{"current": "msg-" + str(i)}})
    print("ready", flush=True)
    sys.stdin.readline()
print("finished", flush=True)
"""


def test_progress_written_in_one_process_is_read_in_another():
    uid = _uid()
    p = subprocess.Popen([sys.executable, "-c", HEAD + PROGRESS.format(uid=uid)], env=_env(), cwd=BACKEND,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert p.stdout.readline().strip() == "ready"
    v = operations.current(uid, "inbox")
    assert v["state"] == "running" and v["done"] == 3 and v["total"] == 10 and v["current"] == "msg-3"
    with pytest.raises(locks.LockTimeout):   # a second run, from this other process, is refused
        operations.start(uid, "inbox")
    p.stdin.write("go\n")
    p.stdin.flush()
    assert "finished" in p.communicate(timeout=30)[0]
    v = operations.current(uid, "inbox")
    assert v["state"] == "done" and v["done"] == 3 and v["finished"]


def test_progress_survives_a_process_crash_and_restart():
    uid = _uid()
    p = _spawn(f"""
op = operations.start({uid!r}, "apps_sync", public={{"done": 0, "total": 9}})
op.update(public={{"done": 5}})
print("at5", flush=True)
os.kill(os.getpid(), 9)""", LOCK_LEASE_SECONDS=1.5)
    assert p.stdout.readline().strip() == "at5" and p.wait(timeout=30) == -9
    _until(lambda: operations.current(uid, "apps_sync")["state"] == "interrupted", timeout=10)
    v = operations.current(uid, "apps_sync")
    assert v["done"] == 5 and "restarted" in v["error"]  # last known progress kept, clearly marked
    with operations.start(uid, "apps_sync") as op:       # a new run can start ("after restart")
        op.update(public={"done": 1})
    assert operations.current(uid, "apps_sync")["state"] == "done"


def test_users_only_see_their_own_progress(user):
    other = _uid()
    op = operations.start(other, "inbox", public={"stage": "Reading", "done": 7, "total": 9})
    try:
        c = user["client"]
        assert c.get("/api/operations/inbox", headers=H).get_json()["operation"] is None
        assert c.get("/api/inbox/progress", headers=H).get_json()["running"] is False
        assert c.post("/api/operations/inbox/cancel", json={}, headers=H).get_json()["state"] is None
        assert not operations.current(other, "inbox")["cancel_requested"]    # the other user's run is untouched
        mine = client_for(other).get("/api/operations/inbox", headers=H).get_json()["operation"]
        assert mine["done"] == 7 and mine["state"] == "running"
        assert c.get("/api/operations/nope", headers=H).status_code == 404
    finally:
        with op:
            pass


def test_concurrent_progress_updates_are_not_lost():
    uid = _uid()
    with operations.start(uid, "nearby", public={"done": 0, "total": 400}) as op:
        threads = [threading.Thread(target=lambda: [op.update(inc={"done": 1}) for _ in range(50)]) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
    doc = M.operations.find_one({"_id": op.id})
    assert doc["public"]["done"] == 400 and doc["version"] >= 401 and doc["state"] == "done"


def test_stale_progress_updates_are_rejected():
    uid = _uid()
    old = operations.start(uid, "inbox", public={"done": 1})
    locks._RENEWER.discard(old.lease)
    M.locks.update_one({"_id": old.lease.id}, {"$set": {"expires_at": locks.EPOCH}})  # its lease ran out
    new = operations.start(uid, "inbox", public={"done": 0})
    old.lease._valid_until = time.monotonic() + 60   # even if the old worker still believes it holds the lease
    with pytest.raises(locks.LeaseLost):
        old.update(public={"done": 99})
    assert M.operations.find_one({"_id": old.id})["state"] == "interrupted"
    assert operations.current(uid, "inbox")["done"] == 0
    with new:
        pass


def test_finished_and_failed_runs_keep_their_state():
    uid = _uid()
    with operations.start(uid, "ai_evaluate", public={"done": 2}):
        pass
    assert operations.cancel(uid, "ai_evaluate")["state"] == "done"            # cancel after completion: no change
    assert operations.current(uid, "ai_evaluate")["cancel_requested"] is False
    with pytest.raises(RuntimeError):
        with operations.start(uid, "ai_match"):
            raise RuntimeError("boom mongodb+srv://admin:Hunter2pass@cluster0.x.mongodb.net/db")
    v = operations.current(uid, "ai_match")
    assert v["state"] == "failed" and "Hunter2pass" not in v["error"] and "boom" in v["error"]
    assert locks.holder("op:ai_match", uid) is None


def test_progress_keeps_personal_fields_encrypted():
    uid = _uid()
    with operations.start(uid, "finder", public={"done": 1}, private={"results": [{"email": "jobs@acme.example"}]}):
        pass
    raw = M.operations.find_one({"uid": uid, "kind": "finder"})
    assert "jobs@acme.example" not in str(raw) and raw["public"] == {"done": 1}
    assert operations.current(uid, "finder")["results"][0]["email"] == "jobs@acme.example"


def test_finished_runs_expire_and_indexes_exist():
    uid = _uid()
    with operations.start(uid, "briefing"):
        pass
    assert M.operations.find_one({"uid": uid})["expire_at"]
    idx = {i["name"]: i for i in M.operations.list_indexes()}
    assert any(i.get("expireAfterSeconds") == 0 for i in idx.values())


# ================================================================ cancellation

WORKER = """
op = operations.start({uid!r}, "mail_sync", public={{"done": 0}})
with op:
    for i in range(1, 400):
        op.wait(0.05)                     # safe point between units of work
        M.step2.update_one({{"_id": op.id}}, {{"$inc": {{"units": 1}}}}, upsert=True)   # one whole unit
        op.update(public={{"done": i}})
print("exited", operations.current({uid!r}, "mail_sync")["state"], flush=True)
"""


def test_cancel_from_another_process_stops_at_a_safe_checkpoint():
    uid = _uid()
    p = _spawn(WORKER.format(uid=uid))
    _until(lambda: (operations.current(uid, "mail_sync") or {}).get("done", 0) >= 5)
    view = operations.cancel(uid, "mail_sync")                   # this process asks; the other one stops
    assert view["cancel_requested"]
    out = p.communicate(timeout=30)[0]
    assert "exited cancelled" in out, out
    v = operations.current(uid, "mail_sync")
    units = M.step2.find_one({"_id": v["op_id"]})["units"]
    assert v["state"] == "cancelled" and units == v["done"] and units < 399   # no unit half-done or skipped in counting
    assert locks.holder("op:mail_sync", uid) is None             # lock released
    with operations.start(uid, "mail_sync"):                      # and a new run can start right away
        pass


def test_repeated_cancellation_is_harmless():
    uid = _uid()
    op = operations.start(uid, "inbox")
    for _ in range(3):
        assert operations.cancel(uid, "inbox")["cancel_requested"]
    with op:
        with pytest.raises(operations.Cancelled):
            op.checkpoint()
        raise operations.Cancelled()
    assert operations.current(uid, "inbox")["state"] == "cancelled"
    assert operations.cancel(uid, "inbox")["state"] == "cancelled"


def test_cancelling_one_user_leaves_another_users_run_alone():
    a, b = _uid(), _uid()
    pa, pb = _spawn(WORKER.format(uid=a)), _spawn(WORKER.format(uid=b).replace("range(1, 400)", "range(1, 30)"))
    _until(lambda: (operations.current(a, "mail_sync") or {}).get("done", 0) >= 3)
    operations.cancel(a, "mail_sync")
    assert "exited cancelled" in pa.communicate(timeout=30)[0]
    assert "exited done" in pb.communicate(timeout=60)[0]
    assert operations.current(b, "mail_sync")["done"] == 29


def test_cancel_through_the_api_and_admin_cancel_is_audited(user, monkeypatch):
    other = _uid()
    op = operations.start(user["uid"], "nearby")
    their = operations.start(other, "nearby")
    try:
        r = user["client"].post("/api/operations/nearby/cancel", json={}, headers=H).get_json()
        assert r["cancel_requested"] and not operations.current(other, "nearby")["cancel_requested"]
        assert user["client"].post(f"/api/admin/operations/{other}/nearby/cancel", json={"reason": "testing"},
                                   headers=H).status_code == 403
        admin_uid, admin_email = make_user()
        monkeypatch.setattr(rbac, "ADMIN_EMAILS", {admin_email})
        rbac.reconcile()
        ac = client_for(admin_uid)
        assert ac.post(f"/api/admin/operations/{other}/nearby/cancel", json={}, headers=H).status_code == 400  # reason
        r = ac.post(f"/api/admin/operations/{other}/nearby/cancel", json={"reason": "stuck run"}, headers=H).get_json()
        assert r == {"ok": True, "state": "running", "cancel_requested": True}   # metadata only
        ev = M.audit_log.find_one({"action": "operation.cancel", "target": f"user:{other}"})
        assert ev and ev["actor"] == admin_uid and ev["details"]["kind"] == "nearby" and audit.verify()[0]
    finally:
        for o in (op, their):
            with o:
                pass


def test_campaign_stop_from_another_process_records_stopped(user, monkeypatch):
    """The real campaign loop: stop requested through the API while it waits between messages."""
    from core import sending
    ws = user["ws"]
    monkeypatch.setattr(sending, "plan_job", lambda ws, cfg: (["email"], [({"id": f"r{i}", "name": f"P{i}", "email": f"p{i}@example.com"}, ["email"]) for i in range(5)]))
    sent = []

    class FakeSMTP:
        def send_message(self, msg):
            sent.append(msg["To"])

        def quit(self):
            pass
    monkeypatch.setattr(sending, "smtp_connect", lambda profile: FakeSMTP())
    monkeypatch.setattr(sending, "domain_problem", lambda d, cache: "")
    monkeypatch.setattr(sending, "build_email", lambda profile, to, subject, text, files, pixel: {"To": to, "Message-ID": f"<{to}>"})
    ws.save("profile", {**ws.profile(), "email": "me@example.com", "name": "Me"})
    cfg = {"mode": "email", "body": "Hi", "subject": "Hello", "documents": [], "min_delay": 30, "max_delay": 30,
           "limit": 5, "dry_run": False, "public_url": "", "track_opens": False}
    sending.start_job(ws, cfg)
    _until(lambda: sending.job_view(ws.uid)["phase"] == "waiting", timeout=20)
    assert user["client"].post("/api/job/stop", json={}, headers=H).status_code == 200
    _until(lambda: not sending.job_view(ws.uid)["running"], timeout=20)
    v = sending.job_view(ws.uid)
    assert v["phase"] == "stopped" and len(sent) == 1 and v["sent"] == 1          # nothing sent after the stop
    assert operations.current(ws.uid, "send")["state"] == "cancelled"


def test_cancel_while_the_database_is_unavailable(monkeypatch, user):
    op = operations.start(user["uid"], "inbox")

    class Down:
        def __getitem__(self, name):
            raise ServerSelectionTimeoutError("no servers")
    real = M.db
    monkeypatch.setattr(M, "db", Down())
    with pytest.raises(locks.LockUnavailable):
        operations.cancel(user["uid"], "inbox")
    r = user["client"].post("/api/operations/inbox/cancel", json={}, headers=H)
    assert r.status_code == 503 and "unavailable" in r.get_json()["error"]   # clear failure, nothing done locally
    monkeypatch.setattr(M, "db", real)
    assert operations.cancel(user["uid"], "inbox")["cancel_requested"]   # retry after recovery works
    with op:
        with pytest.raises(operations.Cancelled):
            op.checkpoint()
        raise operations.Cancelled()
    assert operations.current(user["uid"], "inbox")["state"] == "cancelled"


# ================================================================ a real MongoDB outage

MONGOD = Path.home() / ".local/mongodb/current/bin/mongod"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _mongod(path, port):
    p = subprocess.Popen([str(MONGOD), "--dbpath", path, "--port", str(port), "--bind_ip", "127.0.0.1"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _until(lambda: _port_open(port))
    return p


def _port_open(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
        return True
    except OSError:
        return False


OUTAGE_WORKER = """
op = operations.start("outage-user", "inbox", public={"done": 0})
try:
    with op:
        for i in range(1, 100000):
            op.update(public={"done": i})
            print("unit", i, flush=True)
            op.wait(0.1)
except Exception as e:
    print("stopped:", type(e).__name__, flush=True)
print("exit", flush=True)
"""


@pytest.mark.skipif(not MONGOD.exists(), reason="needs a local mongod binary")
def test_real_database_outage_stops_work_and_recovers():
    """A worker runs against its own MongoDB server; the server is stopped mid-run. The worker must stop (no local
    fallback), and after the server comes back the run reads as interrupted and its lock can be taken again."""
    from pymongo import MongoClient
    path, port = tempfile.mkdtemp(prefix="inkwell-outage-"), _free_port()
    server = _mongod(path, port)
    uri = f"mongodb://127.0.0.1:{port}"
    env = {"MONGODB_URI": uri, "MONGODB_DB": "outage", "LOCK_LEASE_SECONDS": 4, "LOCK_RENEW_SECONDS": 0.5,
           "LOCK_SAFETY_SECONDS": 1.5}
    worker = subprocess.Popen([sys.executable, "-c", HEAD + OUTAGE_WORKER], env=_env(**env), cwd=BACKEND,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        lines = []
        while len([x for x in lines if x.startswith("unit")]) < 5:
            lines.append(worker.stdout.readline().strip())
        server.send_signal(signal.SIGTERM)          # the database goes away
        server.wait(timeout=30)
        down_at = time.monotonic()
        out = worker.communicate(timeout=60)[0]
        lines += out.splitlines()
        units_after = [x for x in out.splitlines() if x.startswith("unit")]
        assert any(x.startswith("stopped:") for x in lines) and "exit" in lines, lines
        assert len(units_after) <= 1                  # at most the unit already in flight; no work without the DB
        assert time.monotonic() - down_at < 45
        server = _mongod(path, port)                  # the database comes back
        client = MongoClient(uri, tz_aware=True)
        doc = client.outage.operations.find_one({"uid": "outage-user"})
        assert doc["state"] == "running"              # the worker couldn't record anything while it was down
        probe = _spawn("""
v = operations.current("outage-user", "inbox")
print("state", v["state"], flush=True)
lease = locks.acquire("op:inbox", "outage-user", wait=10)
print("reacquired", lease.fence, flush=True)""", **env)
        out = probe.communicate(timeout=60)[0]
        assert "state interrupted" in out and "reacquired" in out, out
        client.close()
    finally:
        for p in (worker, server):
            if p.poll() is None:
                p.kill()
                p.wait()
        shutil.rmtree(path, ignore_errors=True)
