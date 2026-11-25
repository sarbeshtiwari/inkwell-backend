"""Stage 2A: optimistic concurrency (Workspace.update) and the pilot record family, `templates`."""
import ast
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time

import pytest
from conftest import DB_NAME, H, client_for, make_user
from pymongo.errors import ServerSelectionTimeoutError

from core import workspace as W
from core.db import M
from core.workspace import VersionConflict, VersionCorrupt, Workspace

BACKEND = pathlib.Path(__file__).resolve().parents[1]
HEAD = f"""
import os, sys, time, json
sys.path.insert(0, {str(BACKEND)!r})
from core.db import M
M.connect()
from core.workspace import Workspace
"""


def _env(**extra):
    return {**os.environ, "MONGODB_DB": DB_NAME, "INKWELL_NO_DOTENV": "1", **{k: str(v) for k, v in extra.items()}}


def _ws():
    return Workspace(make_user()[0])


def _row(ws, key="templates"):
    return M.kv.find_one({"_id": f"{ws.uid}:{key}"})


@pytest.fixture
def seeded(user):
    """The user as signup leaves them: starter template created through the version protocol (version 1)."""
    W.seed_workspace(user["ws"], "Test User", user["email"])
    return user


def _add(name):
    return lambda items: items + [{"id": name, "name": name}]


# ---- the primitive ------------------------------------------------------------------------------------------------

def test_two_writers_read_the_same_version_one_wins_the_other_recomputes_from_fresh_data():
    ws = _ws()
    ws.update("templates", _add("base"), default=[])
    a_read, b_done = threading.Event(), threading.Event()
    seen_by_a = []

    def a_change(items):
        seen_by_a.append([t["id"] for t in items])
        if len(seen_by_a) == 1:                     # A has read version 1 and pauses before writing
            a_read.set()
            b_done.wait(10)
        return items + [{"id": "A", "name": "A"}]
    ta = threading.Thread(target=lambda: ws.update("templates", a_change, default=[]))
    ta.start()
    a_read.wait(10)
    ws.update("templates", _add("B"), default=[])   # B reads the same version 1 and wins
    b_done.set()
    ta.join(10)
    assert seen_by_a == [["base"], ["base", "B"]]   # A computed again, from B's result: its stale value was dropped
    row = _row(ws)
    assert [t["id"] for t in ws.load("templates", [])] == ["base", "B", "A"] and row["version"] == 3


STALE = """
uid = sys.argv[1]
ws = Workspace(uid)
calls = []
def change(items):
    calls.append([t["id"] for t in items])
    if len(calls) == 1:
        print("A-read", flush=True)
        sys.stdin.readline()                    # paused (SIGSTOP) for as long as the test likes
    return items + [{"id": "A", "name": "A"}]
ws.update("templates", change, default=[])
print("calls", json.dumps(calls), flush=True)
"""


def test_a_stale_writer_in_another_process_resuming_after_a_long_pause_loses_nothing():
    ws = _ws()
    ws.update("templates", _add("base"), default=[])
    p = subprocess.Popen([sys.executable, "-c", HEAD + STALE, ws.uid], env=_env(), cwd=BACKEND, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert p.stdout.readline().strip() == "A-read"
        os.kill(p.pid, signal.SIGSTOP)
        for name in ("B1", "B2"):
            ws.update("templates", _add(name), default=[])
        time.sleep(3)                                # "arbitrary" delay: much longer than the writes above
        os.kill(p.pid, signal.SIGCONT)
        p.stdin.write("go\n")
        p.stdin.flush()
        out = p.communicate(timeout=60)[0]
    finally:
        if p.poll() is None:
            os.kill(p.pid, signal.SIGCONT)
            p.kill()
    assert 'calls [["base"], ["base", "B1", "B2"]]' in out, out
    assert [t["id"] for t in ws.load("templates", [])] == ["base", "B1", "B2", "A"]


WRITER = """
uid, tag = sys.argv[1], sys.argv[2]
ws = Workspace(uid)
from core.workspace import VersionConflict
done = conflicts = 0
for i in range(25):
    try:
        ws.update("templates", lambda items, i=i: items + [{"id": f"{tag}-{i}", "name": f"{tag}-{i}"}], default=[])
        done += 1
    except VersionConflict:
        conflicts += 1
print("result", done, conflicts, flush=True)
"""


def test_many_concurrent_writers_in_separate_processes_lose_no_updates(monkeypatch):
    ws = _ws()
    monkeypatch.setattr(W, "UPDATE_ATTEMPTS", 5)
    procs = [subprocess.Popen([sys.executable, "-c", HEAD + WRITER, ws.uid, f"p{n}"],
                              env=_env(), cwd=BACKEND, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
             for n in range(4)]
    outs = [p.communicate(timeout=180)[0] for p in procs]
    results = [tuple(int(x) for x in o.split("result")[-1].split()[:2]) for o in outs]
    done, conflicts = sum(r[0] for r in results), sum(r[1] for r in results)
    ids = [t["id"] for t in ws.load("templates", [])]
    assert done + conflicts == 100 and done > 0
    assert len(ids) == len(set(ids)) == done                       # every write that returned is there, none twice
    assert _row(ws)["version"] == done                             # one version per successful write


def test_threads_hammering_one_record_lose_nothing_that_succeeded():
    ws = _ws()
    done, conflicts = [], []

    def worker(n):
        for i in range(10):
            try:
                ws.update("templates", _add(f"t{n}-{i}"), default=[])
                done.append(f"t{n}-{i}")
            except VersionConflict:
                conflicts.append(1)
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    ids = [t["id"] for t in ws.load("templates", [])]
    assert sorted(ids) == sorted(done) and len(done) + len(conflicts) == 60


def test_missing_version_is_zero_and_the_first_versioned_write_sets_one():
    ws = _ws()
    M.kv.insert_one({"_id": f"{ws.uid}:templates", "uid": ws.uid, "key": "templates", "data": W.seal([{"id": "old"}])})
    assert ws.load_versioned("templates") == ([{"id": "old"}], 0)
    ws.update("templates", _add("new"), default=[])
    assert _row(ws)["version"] == 1 and [t["id"] for t in ws.load("templates", [])] == ["old", "new"]


def test_a_record_rewritten_by_the_previous_release_drops_its_version_and_stays_safe():
    """Rollback compatibility: 8b59986 replaces the whole document (no version). The next versioned write treats it
    as version 0 and goes on from there."""
    ws = _ws()
    ws.update("templates", _add("a"), default=[])
    ws.update("templates", _add("b"), default=[])
    M.kv.replace_one({"_id": f"{ws.uid}:templates"}, {"uid": ws.uid, "key": "templates", "data": W.seal([{"id": "legacy"}])})
    ws.update("templates", _add("c"), default=[])
    assert [t["id"] for t in ws.load("templates", [])] == ["legacy", "c"] and _row(ws)["version"] == 1


@pytest.mark.parametrize("bad", [None, "3", 1.0, True, -1, {"v": 1}])
def test_null_or_malformed_versions_are_refused_not_treated_as_zero(bad):
    ws = _ws()
    M.kv.insert_one({"_id": f"{ws.uid}:templates", "uid": ws.uid, "key": "templates", "data": W.seal([{"id": "x"}]),
                     "version": bad})
    calls = []
    with pytest.raises(VersionCorrupt):
        ws.update("templates", lambda items: calls.append(1) or items + [{"id": "y"}], default=[])
    assert calls == [] and _row(ws)["version"] == bad and ws.load("templates", []) == [{"id": "x"}]


def test_concurrent_creation_one_inserts_the_other_recomputes():
    ws = _ws()
    both_read = threading.Barrier(2)
    seen = {"A": [], "B": []}

    def change(tag):
        def fn(items):
            seen[tag].append(len(items))
            if len(seen[tag]) == 1:
                both_read.wait(10)                  # both read "no record yet"
            return items + [{"id": tag}]
        return fn
    ts = [threading.Thread(target=lambda t=t: ws.update("templates", change(t), default=[])) for t in ("A", "B")]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert sorted(t["id"] for t in ws.load("templates", [])) == ["A", "B"] and _row(ws)["version"] == 2
    assert sorted(len(v) for v in seen.values()) == [1, 2]   # the loser got a duplicate key and computed again


def test_create_only_writes_when_missing():
    ws = _ws()
    assert ws.create("templates", [{"id": "first"}]) is True
    assert ws.create("templates", [{"id": "second"}]) is False     # duplicate key: left as it was
    assert ws.load("templates", []) == [{"id": "first"}] and _row(ws)["version"] == 1


def test_exhausted_retries_give_409_and_write_nothing(seeded, monkeypatch):
    ws, c = seeded["ws"], seeded["client"]
    before = _row(ws)
    calls = []
    real = W.Workspace.update

    def always_raced(self, key, fn, default=None, expected_version=None):
        def counting(items):
            calls.append(1)
            M.kv.update_one({"_id": f"{self.uid}:{key}"}, {"$inc": {"version": 1}})   # someone else always lands first
            return fn(items)
        return real(self, key, counting, default, expected_version)
    monkeypatch.setattr(W.Workspace, "update", always_raced)
    r = c.post("/api/templates", json={"name": "Raced", "body": "Hello {name}, this loses every race."}, headers=H)
    assert r.status_code == 409 and r.headers.get("Retry-After") == "2" and "changed somewhere else" in r.get_json()["error"]
    assert len(calls) == W.UPDATE_ATTEMPTS
    assert not any(t["name"] == "Raced" for t in ws.load("templates", []))
    assert W.unseal(_row(ws)["data"]) == W.unseal(before["data"])


def test_database_outage_answers_503_and_writes_nothing(user, monkeypatch):
    ws, c = user["ws"], user["client"]
    before = ws.load("templates", [])
    real_db = M.db

    class Down:
        def __getitem__(self, name):
            if name == "kv":
                raise ServerSelectionTimeoutError("no servers")
            return real_db[name]
    monkeypatch.setattr(M, "db", Down())
    r = c.post("/api/templates", json={"name": "Outage", "body": "Hello {name}, the database is down."}, headers=H)
    monkeypatch.setattr(M, "db", real_db)
    assert r.status_code == 503 and "unavailable" in r.get_json()["error"]
    assert ws.load("templates", []) == before


def test_the_callback_cannot_cause_side_effects(monkeypatch):
    """Inside update callbacks (which may run more than once) network, mail, notifications, other writes and the
    account lock are refused, so nothing external can be repeated by a retry."""
    import requests

    import bridge
    from core import mail
    from features import notify
    ws = _ws()
    sent = []
    monkeypatch.setattr(requests, "request", lambda *a, **k: sent.append(a))
    attempts = [
        lambda: bridge.http("GET", "https://example.com/"),
        lambda: mail.smtp_connect({"smtp_host": "smtp.example.com", "smtp_password": "x"}),
        lambda: mail.imap_connect({"smtp_password": "x", "email": "a@example.com"}),
        lambda: notify.notify(ws.uid, "hi"),
        lambda: ws.save("settings", {}),
        lambda: ws.log("email", "a@example.com", "", "sent"),
        lambda: ws.update("settings", lambda s: s, default={}),
        lambda: ws.lock.__enter__(),
    ]
    for side_effect in attempts:
        with pytest.raises(RuntimeError, match="inside Workspace.update"):
            ws.update("templates", lambda items, f=side_effect: (f(), items)[1], default=[])
    assert sent == [] and _row(ws) is None and M.notifications.count_documents({"uid": ws.uid}) == 0
    assert W.locks.holder("workspace", ws.uid) is None


def test_callback_errors_propagate_and_nothing_is_written(seeded):
    c, ws = seeded["client"], seeded["ws"]
    before = _row(ws)["version"] if _row(ws) else None
    r = c.post("/api/templates", json={"name": ws.load("templates", [])[0]["name"], "body": "Hello {name}, duplicate name."},
               headers=H)
    assert r.status_code == 400 and r.get_json()["field"] == "name"
    assert (_row(ws) or {}).get("version") == before


def test_account_deleted_meanwhile_is_not_recreated():
    ws = _ws()
    W.DELETED[ws.uid] = time.time()
    try:
        ws.update("templates", _add("ghost"), default=[])
        assert _row(ws) is None
    finally:
        W.DELETED.pop(ws.uid, None)


def test_expected_version_refuses_a_stale_form():
    ws = _ws()
    ws.update("templates", _add("a"), default=[])
    _, v = ws.load_versioned("templates")
    ws.update("templates", _add("b"), default=[])
    with pytest.raises(VersionConflict):
        ws.replace_versioned("templates", [{"id": "from-an-old-tab"}], expected_version=v)
    assert [t["id"] for t in ws.load("templates", [])] == ["a", "b"]


# ---- the pilot: templates ------------------------------------------------------------------------------------------

def test_templates_can_only_be_written_through_the_version_protocol():
    ws = _ws()
    with pytest.raises(RuntimeError, match="versioned"):
        ws.save("templates", [])


def _is_template_key(node):
    return isinstance(node, ast.Constant) and node.value in W.VERSIONED_KEYS


def test_code_scan_no_direct_writes_of_versioned_records():
    """Every write of a versioned record must use update / create / replace_versioned. Flags ws.save("templates", ...),
    direct M.kv writes naming the key, and kv writes in modules other than core/workspace.py."""
    bad = []
    for p in BACKEND.rglob("*.py"):
        rel = p.relative_to(BACKEND).as_posix()
        if rel.startswith((".venv/", "tests/", "tools/")) or "learning/content" in rel:
            continue
        tree = ast.parse(p.read_text())
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
                continue
            name, target = n.func.attr, ast.unparse(n.func.value)
            if name == "save" and n.args and _is_template_key(n.args[0]):
                bad.append(f"{rel}:{n.lineno} save({ast.unparse(n.args[0])})")
            if target.endswith("kv") and name in ("insert_one", "insert_many", "update_one", "update_many", "replace_one",
                                                  "delete_one", "delete_many", "find_one_and_update", "find_one_and_replace",
                                                  "find_one_and_delete", "bulk_write"):
                text = ast.unparse(n)
                allowed = rel == "core/workspace.py" or rel == "migrations.py"
                if any(k in text for k in W.VERSIONED_KEYS) or not allowed:
                    bad.append(f"{rel}:{n.lineno} {text[:80]}")
    assert bad == [], bad


def test_template_endpoints_behave_as_before(seeded):
    c, ws = seeded["client"], seeded["ws"]
    starter = ws.load("templates", [])
    assert len(starter) == 1 and _row(ws)["version"] == 1            # signup creates it (version 1)
    W.seed_workspace(ws, "Again", "again@example.com")
    assert ws.load("templates", []) == starter and _row(ws)["version"] == 1   # a second seed never overwrites
    r = c.post("/api/templates", json={"name": "Second", "subject": "Hi", "body": "Hello {name}, second template."}, headers=H)
    assert r.status_code == 200 and r.get_json()["name"] == "Second"
    tid = r.get_json()["id"]
    r = c.post("/api/templates", json={"id": tid, "name": "Second (edited)", "body": "Hello {name}, edited."}, headers=H)
    assert r.status_code == 200 and r.get_json()["id"] == tid and r.get_json()["name"] == "Second (edited)"
    assert c.post("/api/templates", json={"name": "second (EDITED)", "body": "Hello {name}, dup."}, headers=H).status_code == 400
    assert c.delete(f"/api/templates/{tid}", headers=H).status_code == 200
    r = c.delete(f"/api/templates/{starter[0]['id']}", headers=H)
    assert r.status_code == 400 and "at least one" in r.get_json()["error"]
    assert c.get("/api/state", headers=H).get_json()["templates"] == ws.load("templates", [])


def test_the_fifty_template_limit_holds_under_concurrency(user):
    ws = user["ws"]
    ws.replace_versioned("templates", [{"id": f"x{i}", "name": f"X{i}", "subject": "", "body": "b"} for i in range(48)])
    clients = [client_for(ws.uid) for _ in range(6)]
    codes = []

    def add(i):
        codes.append(clients[i].post("/api/templates", json={"name": f"New{i}", "body": "Hello {name}, racing."},
                                     headers=H).status_code)  # order doesn't matter here: only counts are checked
    ts = [threading.Thread(target=add, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert len(ws.load("templates", [])) == 50                        # never more than the limit
    assert codes.count(200) == 2 and all(code in (200, 400, 409) for code in codes)


def test_parallel_template_saves_lose_nothing_and_never_take_the_account_lock(user, monkeypatch):
    ws = user["ws"]
    taken = []
    real = W.locks.UserLock.__enter__
    monkeypatch.setattr(W.locks.UserLock, "__enter__", lambda self: (taken.append(1), real(self))[1])
    clients = [client_for(ws.uid) for _ in range(8)]
    codes = {}

    def add(i):
        codes[i] = clients[i].post("/api/templates", json={"name": f"P{i}", "body": f"Hello {{name}}, number {i}."},
                                   headers=H).status_code
    ts = [threading.Thread(target=add, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    names = {t["name"] for t in ws.load("templates", [])}
    assert set(codes.values()) <= {200, 409}
    assert {f"P{i}" for i, code in codes.items() if code == 200} == names - {"Introduction"}   # exactly the successes
    assert taken == []                                                 # no account lock for templates any more
