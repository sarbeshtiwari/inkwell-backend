"""Stage 2B batch 1: `inbox_companies` and `ai_portals` on the version protocol (Workspace.update)."""
import os
import pathlib
import signal
import subprocess
import sys
import threading
import time

import pytest
from conftest import DB_NAME, H, client_for, make_user

from core import workspace as W
from core.db import M
from core.workspace import VersionConflict, Workspace
from infra import locks

BACKEND = pathlib.Path(__file__).resolve().parents[1]
HEAD = f"""
import os, sys, time, json
sys.path.insert(0, {str(BACKEND)!r})
from core.db import M
M.connect()
from core.workspace import Workspace, VersionConflict
"""
KEYS = {"inbox_companies": ({}, lambda tag: (lambda d: {**d, tag: {"name": tag}})),
        "ai_portals": ([], lambda tag: (lambda rows: rows + [{"ats": "greenhouse", "slug": tag, "name": tag, "jobs": 1}]))}


def _env(**extra):
    return {**os.environ, "MONGODB_DB": DB_NAME, "INKWELL_NO_DOTENV": "1", **{k: str(v) for k, v in extra.items()}}


def _ws():
    return Workspace(make_user()[0])


def _row(ws, key):
    return M.kv.find_one({"_id": f"{ws.uid}:{key}"})


def _is_ts(v):
    """A version written now: an int at least the server clock in ms (max(previous + 1, server ms))."""
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1_700_000_000_000


def _tags(ws, key):
    v = ws.load(key, KEYS[key][0])
    return sorted(v) if key == "inbox_companies" else [r["slug"] for r in v]


@pytest.fixture(params=sorted(KEYS))
def key(request):
    return request.param


# ---- the primitive, per record ------------------------------------------------------------------------------------

def test_both_are_versioned_and_plain_save_is_refused(key):
    assert key in W.VERSIONED_KEYS
    with pytest.raises(RuntimeError, match="versioned"):
        _ws().save(key, KEYS[key][0])


def test_two_writers_same_version_loser_recomputes_from_fresh_data(key):
    ws, (default, add) = _ws(), KEYS[key]
    ws.update(key, add("base"), default=default)
    a_read, b_done, seen = threading.Event(), threading.Event(), []

    def a_change(v):
        seen.append(_tags_of(key, v))
        if len(seen) == 1:
            a_read.set()
            b_done.wait(10)
        return add("A")(v)
    t = threading.Thread(target=lambda: ws.update(key, a_change, default=default))
    t.start()
    a_read.wait(10)
    ws.update(key, add("B"), default=default)
    b_done.set()
    t.join(10)
    assert seen == [["base"], sorted(["base", "B"]) if key == "inbox_companies" else ["base", "B"]]
    assert sorted(_tags(ws, key)) == ["A", "B", "base"] and _is_ts(_row(ws, key)["version"])


def _tags_of(key, v):
    return sorted(v) if key == "inbox_companies" else [r["slug"] for r in v]


STALE = """
key, uid = sys.argv[1], sys.argv[2]
ws = Workspace(uid)
calls = []
def change(v):
    calls.append(len(v))
    if len(calls) == 1:
        print("A-read", flush=True)
        sys.stdin.readline()
    if key == "inbox_companies":
        return {**v, "A": {"name": "A"}}
    return v + [{"ats": "greenhouse", "slug": "A", "name": "A", "jobs": 1}]
ws.update(key, change, default={} if key == "inbox_companies" else [])
print("calls", json.dumps(calls), flush=True)
"""


def test_stale_writer_in_another_process_resuming_after_a_delay(key):
    ws, (default, add) = _ws(), KEYS[key]
    ws.update(key, add("base"), default=default)
    p = subprocess.Popen([sys.executable, "-c", HEAD + STALE, key, ws.uid], env=_env(), cwd=BACKEND, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert p.stdout.readline().strip() == "A-read"
        os.kill(p.pid, signal.SIGSTOP)
        ws.update(key, add("B1"), default=default)
        ws.update(key, add("B2"), default=default)
        time.sleep(3)
        os.kill(p.pid, signal.SIGCONT)
        p.stdin.write("go\n")
        p.stdin.flush()
        out = p.communicate(timeout=60)[0]
    finally:
        if p.poll() is None:
            os.kill(p.pid, signal.SIGCONT)
            p.kill()
    assert "calls [1, 3]" in out, out                                # recomputed from the fresh value (3 entries)
    assert sorted(_tags(ws, key)) == ["A", "B1", "B2", "base"]


WRITER = """
key, uid, tag = sys.argv[1], sys.argv[2], sys.argv[3]
ws = Workspace(uid)
done = conflicts = 0
for i in range(15):
    t = f"{tag}-{i}"
    fn = (lambda v, t=t: {**v, t: {"name": t}}) if key == "inbox_companies" else \\
         (lambda v, t=t: v + [{"ats": "greenhouse", "slug": t, "name": t, "jobs": 1}])
    try:
        ws.update(key, fn, default={} if key == "inbox_companies" else [])
        done += 1
        print("ok", t, flush=True)
    except VersionConflict:
        conflicts += 1
print("result", done, conflicts, flush=True)
"""


def test_separate_processes_lose_no_reported_success(key):
    ws = _ws()
    procs = [subprocess.Popen([sys.executable, "-c", HEAD + WRITER, key, ws.uid, f"p{n}"], env=_env(), cwd=BACKEND,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for n in range(4)]
    outs = [p.communicate(timeout=180)[0] for p in procs]
    succeeded = {line.split()[1] for o in outs for line in o.splitlines() if line.startswith("ok ")}
    results = [tuple(int(x) for x in o.split("result")[-1].split()[:2]) for o in outs]
    assert sum(r[0] + r[1] for r in results) == 60 and succeeded
    assert set(_tags(ws, key)) == succeeded and len(_tags(ws, key)) == len(succeeded)
    assert _is_ts(_row(ws, key)["version"])


def test_threads_lose_no_reported_success(key):
    ws, (default, add) = _ws(), KEYS[key]
    done, lock = [], threading.Lock()

    def worker(n):
        for i in range(8):
            try:
                ws.update(key, add(f"t{n}-{i}"), default=default)
                with lock:
                    done.append(f"t{n}-{i}")
            except VersionConflict:
                pass
    ts = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert sorted(_tags(ws, key)) == sorted(done)


def test_concurrent_creation(key):
    ws, (default, add) = _ws(), KEYS[key]
    barrier, calls = threading.Barrier(2), {"A": 0, "B": 0}

    def change(tag):
        def fn(v):
            calls[tag] += 1
            if calls[tag] == 1:
                barrier.wait(10)                                   # both saw "no record yet"
            return add(tag)(v)
        return fn
    ts = [threading.Thread(target=lambda t=t: ws.update(key, change(t), default=default)) for t in ("A", "B")]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert sorted(_tags(ws, key)) == ["A", "B"] and sorted(calls.values()) == [1, 2] and _is_ts(_row(ws, key)["version"])


def test_account_deleted_during_an_update_is_not_recreated(key):
    ws, (default, add) = _ws(), KEYS[key]

    def change(v):
        W.DELETED[ws.uid] = time.time()                            # deleted while this change was being computed
        return add("ghost")(v)
    try:
        ws.update(key, change, default=default)
    finally:
        W.DELETED.pop(ws.uid, None)
    assert _row(ws, key) is None


def test_stale_whole_record_replacement_is_refused(key):
    ws, (default, add) = _ws(), KEYS[key]
    ws.update(key, add("a"), default=default)
    _, v = ws.load_versioned(key, default)
    ws.update(key, add("b"), default=default)
    with pytest.raises(VersionConflict):
        ws.replace_versioned(key, add("from-old-form")(default), expected_version=v)
    assert sorted(_tags(ws, key)) == ["a", "b"]


def test_legacy_record_without_version_converts_on_first_write(key):
    ws, (default, add) = _ws(), KEYS[key]
    M.kv.insert_one({"_id": f"{ws.uid}:{key}", "uid": ws.uid, "key": key, "data": W.seal(add("old")(default))})
    ws.update(key, add("new"), default=default)
    assert sorted(_tags(ws, key)) == ["new", "old"] and _is_ts(_row(ws, key)["version"])


def test_callbacks_cannot_reach_the_network(key, monkeypatch):
    import requests

    import bridge
    sent = []
    monkeypatch.setattr(requests, "request", lambda *a, **k: sent.append(a))
    ws, (default, add) = _ws(), KEYS[key]
    with pytest.raises(RuntimeError, match="inside Workspace.update"):
        ws.update(key, lambda v: (bridge.http("GET", "https://example.com/"), add("x")(v))[1], default=default)
    assert sent == [] and _row(ws, key) is None


# ---- endpoints: inbox_companies ------------------------------------------------------------------------------------

CO = "0123456789abcdef"


def test_company_edit_keeps_name_and_hidden_independently(user):
    c, ws = user["client"], user["ws"]
    assert c.put(f"/api/inbox/company/{CO}", json={"name": "Acme"}, headers=H).get_json() == {"ok": True, "name": "Acme"}
    r = c.put(f"/api/inbox/company/{CO}", json={"hidden": True}, headers=H).get_json()
    assert r == {"ok": True, "name": "Acme", "hidden": True}
    assert c.put(f"/api/inbox/company/{CO}", json={"name": ""}, headers=H).status_code == 400
    assert c.put("/api/inbox/company/not-a-key", json={"name": "x"}, headers=H).status_code == 404
    assert ws.load("inbox_companies", {}) == {CO: {"name": "Acme", "hidden": True}} and _is_ts(_row(ws, "inbox_companies")["version"])


def test_parallel_company_edits_from_several_tabs_lose_nothing(user, monkeypatch):
    ws = user["ws"]
    taken = []
    real = locks.UserLock.__enter__
    monkeypatch.setattr(locks.UserLock, "__enter__", lambda self: (taken.append(1), real(self))[1])
    clients, codes = [client_for(ws.uid) for _ in range(6)], {}

    def edit(i):
        codes[i] = clients[i].put(f"/api/inbox/company/{i:016x}", json={"name": f"Co{i}", "hidden": i % 2 == 0},
                                  headers=H).status_code
    ts = [threading.Thread(target=edit, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    saved = ws.load("inbox_companies", {})
    assert set(codes.values()) <= {200, 409}
    assert {k for k, v in saved.items()} == {f"{i:016x}" for i, code in codes.items() if code == 200}
    assert all(saved[f"{i:016x}"] == {"name": f"Co{i}", "hidden": i % 2 == 0} for i, code in codes.items() if code == 200)
    assert taken == []                                             # no account lock any more


def test_name_and_hidden_set_at_the_same_moment_both_survive(user):
    ws = user["ws"]
    a, b = client_for(ws.uid), client_for(ws.uid)
    ts = [threading.Thread(target=lambda: a.put(f"/api/inbox/company/{CO}", json={"name": "Renamed"}, headers=H)),
          threading.Thread(target=lambda: b.put(f"/api/inbox/company/{CO}", json={"hidden": True}, headers=H))]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert ws.load("inbox_companies", {})[CO] == {"name": "Renamed", "hidden": True}


# ---- endpoints: ai_portals (two features write it) ------------------------------------------------------------------

@pytest.fixture
def boards(monkeypatch):
    from features.ai_jobs import portals
    monkeypatch.setattr(portals, "from_link", lambda link: [("greenhouse", link.rstrip("/").rsplit("/", 1)[-1])])
    monkeypatch.setattr(portals, "try_boards", lambda cands: (cands[0][0], cands[0][1], [{}, {}]) if cands else None)
    from features import finder
    monkeypatch.setattr(finder, "host_is_public", lambda host: True)
    return portals


def test_portal_add_remove_and_limit(user, boards, monkeypatch):
    c, ws = user["client"], user["ws"]
    r = c.post("/api/ai-jobs/portals", json={"link": "https://boards.example/acme"}, headers=H).get_json()
    assert r["portals"] == [{"ats": "greenhouse", "slug": "acme", "name": "acme", "jobs": 2}]
    c.post("/api/ai-jobs/portals", json={"link": "https://boards.example/acme"}, headers=H)       # same one again: replaced
    assert [p["slug"] for p in ws.load("ai_portals", [])] == ["acme"]
    assert c.delete("/api/ai-jobs/portals/greenhouse/acme", headers=H).get_json() == {"portals": []}
    monkeypatch.setattr(boards, "MAX_PORTALS", 2)
    for name in ("a", "b"):
        assert c.post("/api/ai-jobs/portals", json={"link": f"https://boards.example/{name}"}, headers=H).status_code == 200
    r = c.post("/api/ai-jobs/portals", json={"link": "https://boards.example/c"}, headers=H)
    assert r.status_code == 400 and "up to 2" in r.get_json()["error"]
    assert [p["slug"] for p in ws.load("ai_portals", [])] == ["a", "b"]


def _nearby(ws, n):
    ws.save("nearby", {"companies": [{"id": f"n{i}", "name": f"Near{i}", "website": f"https://near.example/near{i}"}
                                     for i in range(n)]})
    ws.save("nearby_info", {f"n{i}": {"careers": f"https://near.example/near{i}", "openings": 3} for i in range(n)})


def test_track_keeps_the_latest_sixty_as_before(user, boards):
    c, ws = user["client"], user["ws"]
    ws.replace_versioned("ai_portals", [{"ats": "greenhouse", "slug": f"old{i}", "name": "x", "jobs": 1} for i in range(60)])
    _nearby(ws, 1)
    assert c.post("/api/nearby/track/n0", headers=H).status_code == 200
    slugs = [p["slug"] for p in ws.load("ai_portals", [])]
    assert len(slugs) == 60 and slugs[-1] == "near0" and "old0" not in slugs


def test_both_features_writing_portals_at_once_lose_nothing(user, boards):
    """The portals page and the nearby page write the same list (two features, previously both under ws.lock)."""
    ws = user["ws"]
    _nearby(ws, 4)
    clients, codes = [client_for(ws.uid) for _ in range(8)], {}

    def go(i):
        if i < 4:
            codes[f"near{i}"] = clients[i].post(f"/api/nearby/track/n{i}", headers=H).status_code
        else:
            codes[f"site{i}"] = clients[i].post("/api/ai-jobs/portals", json={"link": f"https://boards.example/site{i}"},
                                                headers=H).status_code
    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    saved = {p["slug"] for p in ws.load("ai_portals", [])}
    assert set(codes.values()) <= {200, 409}
    assert saved == {slug for slug, code in codes.items() if code == 200}


def test_removing_while_another_tab_adds_never_resurrects_or_drops(user, boards):
    c, ws = user["client"], user["ws"]
    for name in ("keep", "gone"):
        c.post("/api/ai-jobs/portals", json={"link": f"https://boards.example/{name}"}, headers=H)
    a, b = client_for(ws.uid), client_for(ws.uid)
    ts = [threading.Thread(target=lambda: a.delete("/api/ai-jobs/portals/greenhouse/gone", headers=H)),
          threading.Thread(target=lambda: b.post("/api/ai-jobs/portals", json={"link": "https://boards.example/new"}, headers=H))]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert sorted(p["slug"] for p in ws.load("ai_portals", [])) == ["keep", "new"]


# ---- mixed versions: an old release writing the same record (fixed by the write token) ------------------------------

def test_old_release_writer_interleaving_cannot_cause_a_lost_update(key):
    ws, (default, add) = _ws(), KEYS[key]
    ws.update(key, add("base"), default=default)                    # version 1
    a_read, go = threading.Event(), threading.Event()
    calls = []

    def a_change(v):
        calls.append(1)
        if len(calls) == 1:
            a_read.set()
            go.wait(10)                                             # new writer A read version 1 and is paused
        return add("A")(v)
    t = threading.Thread(target=lambda: ws.update(key, a_change, default=default))
    t.start()
    a_read.wait(10)
    legacy = add("OLD")(ws.load(key, default))                      # an old-release instance: whole-record replace,
    M.kv.replace_one({"_id": f"{ws.uid}:{key}"}, {"uid": ws.uid, "key": key, "data": W.seal(legacy)})   # no version
    ws.update(key, add("B"), default=default)                       # a new writer: missing -> version 1 again
    go.set()
    t.join(10)
    assert set(_tags(ws, key)) >= {"base", "OLD", "B", "A"}          # A's stale write must not drop OLD or B
