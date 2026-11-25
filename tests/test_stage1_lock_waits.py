"""Stage 1: bounded account-lock waits, external I/O outside the lock, lock measurements."""
import logging
import threading
import time

import pytest
from conftest import H, client_for, make_user

from core.workspace import Workspace
from infra import locks


@pytest.fixture
def short_waits(monkeypatch):
    monkeypatch.setattr(locks, "WAIT_INTERACTIVE", 1.0)
    monkeypatch.setattr(locks, "WAIT_BACKGROUND", 4.0)
    locks._USER_LOCKS.clear()  # fresh UserLock objects (wait chosen per entry)
    yield
    locks._USER_LOCKS.clear()


def _hold_elsewhere(uid):
    """The account lock held by 'another process' (a separate lease, not this process's UserLock)."""
    return locks.acquire("workspace", uid)


# ---- interactive vs background waits ----------------------------------------------------------------------------

def test_interactive_request_times_out_at_the_limit_with_retry_after_and_changes_nothing(user, short_waits):
    c, ws = user["client"], user["ws"]
    before = ws.load("recipients", [])
    lease = _hold_elsewhere(ws.uid)
    try:
        t0 = time.monotonic()
        r = c.post("/api/recipients", json={"name": "Blocked", "email": "blocked@example.com"}, headers=H)  # under ws.lock
        took = time.monotonic() - t0
    finally:
        lease.release()
    assert r.status_code == 409 and r.headers.get("Retry-After") == "2"
    assert r.get_json() == {"error": locks.UserLock.BUSY, "field": None}      # the existing error shape
    assert 1.0 <= took < 2.5                                                   # the limit, plus at most one attempt
    time.sleep(1.5)                                                            # nothing carries on in the background
    assert ws.load("recipients", []) == before


def test_the_default_interactive_limit_is_18_seconds_and_background_45():
    assert locks.WAIT_INTERACTIVE == 18.0 and locks.WAIT_BACKGROUND == 45.0
    assert locks.WAIT_INTERACTIVE + 1 < 26                                    # answered before Netlify's ~26 s cut-off


def test_background_work_keeps_its_longer_wait(short_waits):
    uid = make_user()[0]
    ws = Workspace(uid)
    lease = _hold_elsewhere(uid)
    done = {}

    def background():                                                         # no request context: background limit
        t0 = time.monotonic()
        with ws.lock:
            ws.save("bg", {"ok": True})
        done["waited"] = time.monotonic() - t0
    t = threading.Thread(target=background)
    t.start()
    time.sleep(2.0)                                                           # past the 1 s interactive limit
    lease.release()
    t.join(10)
    assert done["waited"] >= 1.9 and ws.load("bg", None) == {"ok": True}


def test_background_work_also_gives_up_at_its_own_limit(short_waits):
    uid = make_user()[0]
    lease = _hold_elsewhere(uid)
    try:
        t0 = time.monotonic()
        with pytest.raises(locks.AccountBusy):
            with Workspace(uid).lock:
                pass
        assert 4.0 <= time.monotonic() - t0 < 5.5
    finally:
        lease.release()


def test_waiting_behind_another_thread_of_this_process_is_bounded_too(user, short_waits):
    """The in-process queue counts toward the limit (it used to wait without one)."""
    c, ws = user["client"], user["ws"]
    entered, release = threading.Event(), threading.Event()

    def holder():
        with ws.lock:
            entered.set()
            release.wait(10)
    t = threading.Thread(target=holder)
    t.start()
    entered.wait(5)
    try:
        t0 = time.monotonic()
        r = c.post("/api/recipients", json={"name": "Queued", "email": "queued@example.com"}, headers=H)
        assert r.status_code == 409 and 1.0 <= time.monotonic() - t0 < 2.5
    finally:
        release.set()
        t.join(10)
    assert not any(x.get("name") == "Queued" for x in ws.load("recipients", []))


def test_account_lock_timeout_is_answered_before_anything_is_read(user, short_waits, monkeypatch):
    ws = user["ws"]
    reads = []
    real = Workspace.load
    monkeypatch.setattr(Workspace, "load", lambda self, k, d=None: (reads.append(k), real(self, k, d))[1])
    lease = _hold_elsewhere(ws.uid)
    try:
        r = user["client"].post("/api/recipients", json={"name": "NoRead", "email": "noread@example.com"}, headers=H)
    finally:
        lease.release()
    assert r.status_code == 409 and "recipients" not in reads


def test_other_409s_keep_no_retry_after(user):
    from infra import operations
    op = operations.start(user["uid"], "finder")
    try:
        r = user["client"].post("/api/finder/scan", json={"items": [{"company": "A", "website": "https://a.example"}]}, headers=H)
        assert r.status_code == 409 and "Retry-After" not in r.headers
    finally:
        with op:
            pass


# ---- external I/O outside the lock, fresh state afterwards ------------------------------------------------------

def _repo(name):
    return {"name": name, "description": "d", "topics": ["x"], "language": "Python", "html_url": f"https://github.com/me/{name}",
            "private": False, "stargazers_count": 1, "pushed_at": "2026-01-01", "homepage": "", "has_pages": False}


def test_github_calls_happen_outside_the_lock_and_fresh_state_is_used(user, monkeypatch):
    from features import portfolio
    ws, c = user["ws"], user["client"]
    ws.save("portfolio", {"headline": "Before", "items": []})
    held_during_calls = []

    def fake_gh(ws_, method, path):
        held_during_calls.append(locks.holder("workspace", ws.uid) is not None)
        if path.endswith("/repos/me/one"):                     # meanwhile, another change lands
            with Workspace(ws.uid).lock:
                p = Workspace(ws.uid).load("portfolio", {})
                p["headline"] = "Changed meanwhile"
                p["items"] = p.get("items", []) + [{"repo": "me/two", "title": "Two (added meanwhile)"}]
                Workspace(ws.uid).save("portfolio", p)
        if "deployments" in path:
            return []
        return _repo(path.rsplit("/", 1)[-1])
    monkeypatch.setattr(portfolio.ghub, "gh", fake_gh)
    r = c.post("/api/portfolio/items", json={"repos": ["me/one", "me/two", "me/three"]}, headers=H)
    assert r.status_code == 200
    assert held_during_calls and not any(held_during_calls)    # no GitHub call ran under the account lock
    saved = ws.load("portfolio", {})
    assert saved["headline"] == "Changed meanwhile"            # not overwritten by the snapshot taken before
    repos = [i["repo"] for i in saved["items"]]
    assert sorted(repos) == ["me/one", "me/three", "me/two"] and repos.count("me/two") == 1
    assert next(i for i in saved["items"] if i["repo"] == "me/two")["title"] == "Two (added meanwhile)"


def test_dns_lookup_happens_outside_the_lock_and_fresh_profile_is_used(user, monkeypatch):
    from routes import workspace_api
    ws, c = user["ws"], user["client"]
    ws.save("profile", {**ws.profile(), "name": "Old", "signature": "kept", "smtp_password": ""})
    held = []

    def fake_public_host(host, field="smtp_host"):
        held.append(locks.holder("workspace", ws.uid) is not None)
        with Workspace(ws.uid).lock:                            # meanwhile: a password is saved elsewhere
            p = Workspace(ws.uid).profile()
            p.update(smtp_password="set-meanwhile", signature="changed meanwhile")
            Workspace(ws.uid).save("profile", p)
        return host
    monkeypatch.setattr(workspace_api, "public_host", fake_public_host)
    r = c.put("/api/profile", json={"name": "New Name", "email": "me@example.com", "smtp_host": "smtp.example.com",
                                     "smtp_port": 465, "smtp_user": "me@example.com"}, headers=H)
    assert r.status_code == 200 and held == [False]
    p = ws.profile()
    assert p["name"] == "New Name" and p["signature"] == "changed meanwhile" and p["smtp_password"] == "set-meanwhile"


def test_profile_validation_errors_are_unchanged(user):
    c = user["client"]
    r = c.put("/api/profile", json={"name": "Ok Name", "email": "me@example.com", "smtp_host": "not a host", "smtp_port": 465}, headers=H)
    assert r.status_code == 400 and r.get_json()["field"] == "smtp_host"
    r = c.put("/api/profile", json={"name": "Ok Name", "email": "me@example.com", "smtp_host": "smtp.gmail.com", "smtp_port": 2526}, headers=H)
    assert r.status_code == 400 and r.get_json()["field"] == "smtp_port"
    r = c.put("/api/profile", json={"name": "Ok Name", "email": "me@example.com", "smtp_password": "x" * 201}, headers=H)
    assert r.status_code == 400 and r.get_json()["field"] == "smtp_password"


def test_concurrent_account_changes_lose_nothing(user):
    """Contacts added from 6 parallel requests while the lock is contended: every one is kept."""
    ws = user["ws"]
    clients = [client_for(ws.uid) for _ in range(6)]
    codes = []

    def add(i):
        codes.append(clients[i].post("/api/recipients", json={"name": f"T{i}", "email": f"t{i}@example.com"}, headers=H).status_code)
    threads = [threading.Thread(target=add, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    names = {r.get("name") for r in ws.load("recipients", [])}
    assert codes == [200] * 6 and {f"T{i}" for i in range(6)} <= names


# ---- measurements ------------------------------------------------------------------------------------------------

def test_lock_measurements_and_logs_carry_no_account_data(user, short_waits, caplog):
    locks.reset_metrics()
    ws, c = user["ws"], user["client"]
    caplog.set_level(logging.WARNING, logger="inkwell.locks")
    assert c.post("/api/recipients", json={"name": "M1", "email": "m1@example.com"}, headers=H).status_code == 200
    lease = _hold_elsewhere(ws.uid)
    try:
        r = c.post("/api/recipients", json={"name": "M2", "email": "m2@example.com"}, headers={**H, "X-Request-Id": "req-test-123"})
    finally:
        lease.release()
    assert r.status_code == 409
    m = locks.metrics()["workspace"]
    assert m["acquired"] >= 1 and m["timeouts"] == 1
    assert m["wait_ms"]["p99"] >= 1000 and m["hold_ms"]["n"] >= 1
    lines = [rec.getMessage() for rec in caplog.records if rec.name == "inkwell.locks"]
    timeout_line = next(x for x in lines if "timeout" in x)
    assert "request=req-test-123" in timeout_line and "op=" in timeout_line
    joined = " ".join(lines)
    assert ws.uid not in joined and user["email"] not in joined and "M2" not in joined


def test_try_acquire_busy_is_not_reported_as_a_failure(caplog):
    locks.reset_metrics()
    caplog.set_level(logging.WARNING, logger="inkwell.locks")
    uid = make_user()[0]
    lease = locks.acquire("res", uid)
    try:
        assert locks.try_acquire("res", uid) is None
    finally:
        lease.release()
    assert not [r for r in caplog.records if r.name == "inkwell.locks"]
