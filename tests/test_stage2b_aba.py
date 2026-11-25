"""Stage 2B correction: the ABA race is closed by the write token, and versions are monotonic.

A "new" writer uses Workspace.update. An "old-release" writer is simulated exactly as 8b59986 writes: a whole-document
replace with uid/key/data only (no version, no token)."""
import os
import pathlib
import re
import signal
import subprocess
import sys
import threading
import time

import pytest
from conftest import DB_NAME, make_user
from pymongo.errors import AutoReconnect

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
FAMILIES = {"inbox_companies": ({}, lambda t: (lambda d: {**d, t: {"name": t}}), lambda v: sorted(v)),
            "ai_portals": ([], lambda t: (lambda r: r + [{"ats": "greenhouse", "slug": t, "name": t, "jobs": 1}]),
                           lambda v: sorted(r["slug"] for r in v)),
            "templates": ([], lambda t: (lambda r: r + [{"id": t, "name": t}]), lambda v: sorted(r["id"] for r in v))}


@pytest.fixture(params=["inbox_companies", "ai_portals"])
def fam(request):
    default, add, tags = FAMILIES[request.param]
    return request.param, default, add, tags


def _ws():
    return Workspace(make_user()[0])


def _row(ws, key):
    return M.kv.find_one({"_id": f"{ws.uid}:{key}"})


def _old_release_write(ws, key, value):
    """Exactly what the previous release does: replace the document, dropping version and wid."""
    M.kv.replace_one({"_id": f"{ws.uid}:{key}"}, {"uid": ws.uid, "key": key, "data": W.seal(value)}, upsert=True)


def _env():
    return {**os.environ, "MONGODB_DB": DB_NAME, "INKWELL_NO_DOTENV": "1"}


PAUSED_A = """
key, uid = sys.argv[1], sys.argv[2]
ws = Workspace(uid)
calls = []
def change(v):
    calls.append(json.dumps(v, sort_keys=True))
    if len(calls) == 1:
        print("A-read", flush=True)
        sys.stdin.readline()                     # frozen (SIGSTOP) after reading, before writing
    if key == "inbox_companies":
        return {**v, "A": {"name": "A"}}
    return v + [{"ats": "greenhouse", "slug": "A", "name": "A", "jobs": 1}]
ws.update(key, change, default={} if key == "inbox_companies" else [])
print("attempts", len(calls), flush=True)
"""


def _paused_writer(key, uid):
    p = subprocess.Popen([sys.executable, "-c", HEAD + PAUSED_A, key, uid], env=_env(), cwd=BACKEND, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert p.stdout.readline().strip() == "A-read"
    os.kill(p.pid, signal.SIGSTOP)
    return p


def _resume(p):
    os.kill(p.pid, signal.SIGCONT)
    p.stdin.write("go\n")
    p.stdin.flush()
    out = p.communicate(timeout=60)[0]
    attempts = int(re.search(r"attempts (\d+)", out).group(1))
    return attempts, out


# ---- the demonstrated ABA interleaving, across processes ------------------------------------------------------------

def test_aba_interleaving_across_processes_is_rejected(fam):
    """A reads, pauses; an old release removes version (and token); B writes; A resumes: A must not overwrite B."""
    key, default, add, tags = fam
    ws = _ws()
    ws.update(key, add("base"), default=default)
    a = _paused_writer(key, ws.uid)
    try:
        _old_release_write(ws, key, add("OLD")(ws.load(key, default)))
        ws.update(key, add("B"), default=default)
        attempts, out = _resume(a)
    finally:
        if a.poll() is None:
            os.kill(a.pid, signal.SIGCONT)
            a.kill()
    assert attempts == 2, out                                       # A's first write was refused; it recomputed
    assert tags(ws.load(key, default)) == sorted(["base", "OLD", "B", "A"])


def test_multiple_old_release_writes_between_two_new_writes(fam):
    key, default, add, tags = fam
    ws = _ws()
    ws.update(key, add("base"), default=default)
    a = _paused_writer(key, ws.uid)
    try:
        for t in ("OLD1", "OLD2", "OLD3"):
            _old_release_write(ws, key, add(t)(ws.load(key, default)))
        ws.update(key, add("B"), default=default)
        _old_release_write(ws, key, add("OLD4")(ws.load(key, default)))
        attempts, out = _resume(a)
    finally:
        if a.poll() is None:
            os.kill(a.pid, signal.SIGCONT)
            a.kill()
    assert attempts == 2, out
    assert tags(ws.load(key, default)) == sorted(["base", "OLD1", "OLD2", "OLD3", "B", "OLD4", "A"])


def test_an_old_release_rewrite_of_a_legacy_record_is_detected_too(fam):
    """A record with no version and no token (legacy): A reads it; an old release rewrites it (still legacy). The stored
    bytes differ (new ciphertext), so A recomputes instead of overwriting."""
    key, default, add, tags = fam
    ws = _ws()
    _old_release_write(ws, key, add("legacy")(default))
    a = _paused_writer(key, ws.uid)
    try:
        _old_release_write(ws, key, add("OLD")(ws.load(key, default)))
        attempts, out = _resume(a)
    finally:
        if a.poll() is None:
            os.kill(a.pid, signal.SIGCONT)
            a.kill()
    assert attempts == 2, out
    assert tags(ws.load(key, default)) == sorted(["legacy", "OLD", "A"])


def test_old_then_new_and_new_then_old(fam):
    key, default, add, tags = fam
    ws = _ws()
    _old_release_write(ws, key, add("old1")(default))                # old first: new write builds on it
    ws.update(key, add("new1"), default=default)
    assert tags(ws.load(key, default)) == sorted(["old1", "new1"]) and "wid" in _row(ws, key)
    _old_release_write(ws, key, add("old2")(ws.load(key, default)))  # then old again: token and version dropped
    assert "wid" not in _row(ws, key) and "version" not in _row(ws, key)
    ws.update(key, add("new2"), default=default)                     # and new again: works, from the old write
    assert tags(ws.load(key, default)) == sorted(["old1", "new1", "old2", "new2"])


# ---- bursts in one millisecond, clocks moving back ------------------------------------------------------------------

def test_same_millisecond_burst_then_old_release_then_new_write(fam):
    """Versions can run ahead of the clock in a burst (T, T+1, T+2...). Then an old release drops the fields and a new
    write lands: even if that new version equalled one read during the burst, the token differs."""
    key, default, add, tags = fam
    ws = _ws()
    for i in range(30):                                              # a burst: many writes per millisecond
        ws.update(key, add(f"burst{i}"), default=default)
    stale = dict(_row(ws, key))
    _old_release_write(ws, key, add("OLD")(ws.load(key, default)))
    ws.update(key, add("B"), default=default)
    # The worst case: B's version is exactly the version the stale writer read (forced here).
    M.kv.update_one({"_id": stale["_id"]}, {"$set": {"version": stale["version"]}})
    assert M.kv.update_one(W._cas_filter(stale), W._write_pipeline({"uid": ws.uid, "key": key,
                                                                    "data": W.seal(default)})).matched_count == 0
    assert "B" in tags(ws.load(key, default)) and "OLD" in tags(ws.load(key, default))


def test_clock_moving_back_still_gives_a_strictly_greater_version(fam):
    key, default, add, tags = fam
    ws = _ws()
    ws.update(key, add("a"), default=default)
    future = int(time.time() * 1000) + 10 ** 9                       # stored version far "ahead" (as after a clock jump back)
    M.kv.update_one({"_id": f"{ws.uid}:{key}"}, {"$set": {"version": future}})
    ws.update(key, add("b"), default=default)
    assert _row(ws, key)["version"] == future + 1                    # prev + 1 when the clock is behind
    ws.update(key, add("c"), default=default)
    assert _row(ws, key)["version"] == future + 2


def test_clock_collision_with_a_different_token_never_matches(fam):
    """Simulated skewed clock: after an old-release write, a new write happens to produce exactly the version a stale
    writer read. The stale writer's token still doesn't match."""
    key, default, add, tags = fam
    ws = _ws()
    ws.update(key, add("a"), default=default)
    stale = dict(_row(ws, key))
    _old_release_write(ws, key, add("OLD")(ws.load(key, default)))
    ws.update(key, add("b"), default=default)
    M.kv.update_one({"_id": stale["_id"]}, {"$set": {"version": stale["version"]}})   # same version, new token
    before = dict(_row(ws, key))
    assert M.kv.update_one(W._cas_filter(stale), W._write_pipeline({"uid": ws.uid, "key": key, "data": W.seal(default)})
                           ).matched_count == 0
    assert _row(ws, key) == before


def test_versions_strictly_increase_and_tokens_change_on_every_write(fam):
    key, default, add, tags = fam
    ws = _ws()
    seen = []
    for i in range(20):
        ws.update(key, add(f"w{i}"), default=default)
        row = _row(ws, key)
        assert re.fullmatch(r"[0-9a-f]{32}", row["wid"])
        seen.append((row["version"], row["wid"]))
    versions = [v for v, _ in seen]
    assert all(b > a for a, b in zip(versions, versions[1:], strict=False)) and versions[0] >= 1_700_000_000_000
    assert len({w for _, w in seen}) == 20


TOKENS = """
from core.workspace import VersionConflict
key, uid, tag = sys.argv[1], sys.argv[2], sys.argv[3]
ws = Workspace(uid)
for i in range(15):
    t = f"{tag}-{i}"
    fn = (lambda v, t=t: {**v, t: {"name": t}}) if key == "inbox_companies" else \\
         (lambda v, t=t: v + [{"ats": "greenhouse", "slug": t, "name": t, "jobs": 1}])
    try:
        ws.update(key, fn, default={} if key == "inbox_companies" else [])
        row = M.kv.find_one({"_id": f"{uid}:{key}"})
        print("ok", t, flush=True)
    except VersionConflict:
        print("conflict", t, flush=True)
"""


def test_concurrent_writers_in_separate_processes_with_old_release_writes_mixed_in(fam):
    """4 new-release processes and an old-release writer in this process, all at once. Every new write that reported
    success is present; old-release writes may be overwritten by later new writes' data (they built on it) but the new
    writers never lose each other's successes."""
    key, default, add, tags = fam
    ws = _ws()
    procs = [subprocess.Popen([sys.executable, "-c", HEAD + TOKENS, key, ws.uid, f"p{n}"], env=_env(), cwd=BACKEND,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for n in range(4)]
    outs = [p.communicate(timeout=180)[0] for p in procs]
    ok = {line.split()[1] for o in outs for line in o.splitlines() if line.startswith("ok ")}
    assert ok and set(tags(ws.load(key, default))) >= ok


def test_concurrent_updates_in_the_same_millisecond(fam):
    key, default, add, tags = fam
    ws = _ws()
    barrier, done, lock = threading.Barrier(8), [], threading.Lock()

    def go(i):
        barrier.wait(10)                                             # all start together
        try:
            ws.update(key, add(f"s{i}"), default=default)
            with lock:
                done.append(f"s{i}")
        except VersionConflict:
            pass
    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert done and tags(ws.load(key, default)) == sorted(done)


# ---- legacy and corrupt records -------------------------------------------------------------------------------------

def test_pre_token_versioned_record_needs_its_exact_bytes(fam):
    """Records written by Stage 2A/2B before tokens: version but no wid. Matched on version + no token + exact bytes,
    never on version alone."""
    key, default, add, tags = fam
    ws = _ws()
    M.kv.insert_one({"_id": f"{ws.uid}:{key}", "uid": ws.uid, "key": key, "data": W.seal(add("v3")(default)), "version": 3})
    stale = dict(_row(ws, key))
    M.kv.update_one({"_id": stale["_id"]}, {"$set": {"data": W.seal(add("other")(default))}})   # same version, other bytes
    assert M.kv.update_one(W._cas_filter(stale), W._write_pipeline({"uid": ws.uid, "key": key,
                                                                    "data": W.seal(default)})).matched_count == 0
    ws.update(key, add("new"), default=default)
    assert tags(ws.load(key, default)) == sorted(["other", "new"]) and "wid" in _row(ws, key)


@pytest.mark.parametrize("bad", [{"wid": "not-hex"}, {"wid": None}, {"wid": 12345}, {"wid": "A" * 32},
                                 {"wid": "ab" * 15}, {"wid": "0" * 32, "_drop_version": True}])
def test_missing_or_malformed_tokens_are_refused_and_nothing_is_written(fam, bad):
    key, default, add, tags = fam
    ws = _ws()
    ws.update(key, add("x"), default=default)
    patch = {k: v for k, v in bad.items() if not k.startswith("_")}
    M.kv.update_one({"_id": f"{ws.uid}:{key}"}, {"$set": patch, **({"$unset": {"version": ""}} if bad.get("_drop_version") else {})})
    before = dict(_row(ws, key))
    calls = []
    with pytest.raises(VersionCorrupt):
        ws.update(key, lambda v: calls.append(1) or add("y")(v), default=default)
    assert calls == [] and _row(ws, key) == before


@pytest.mark.parametrize("bad", [9.3e18, -5, True, "17000"])
def test_malformed_versions_are_still_refused(fam, bad):
    key, default, add, tags = fam
    ws = _ws()
    ws.update(key, add("x"), default=default)
    M.kv.update_one({"_id": f"{ws.uid}:{key}"}, {"$set": {"version": bad}})
    before = dict(_row(ws, key))
    with pytest.raises(VersionCorrupt):
        ws.update(key, add("y"), default=default)
    assert _row(ws, key) == before


def test_versions_near_the_bson_int64_limit(fam):
    key, default, add, tags = fam
    ws = _ws()
    ws.update(key, add("x"), default=default)
    M.kv.update_one({"_id": f"{ws.uid}:{key}"}, {"$set": {"version": 2 ** 63 - 3}})
    ws.update(key, add("y"), default=default)
    assert _row(ws, key)["version"] == 2 ** 63 - 2 and isinstance(_row(ws, key)["version"], int)
    ws.update(key, add("z"), default=default)
    assert _row(ws, key)["version"] == 2 ** 63 - 1                    # still an exact 64-bit integer, never a double
    before = dict(_row(ws, key))
    with pytest.raises(VersionCorrupt):                               # no room for a greater version: refused
        ws.update(key, add("w"), default=default)
    assert _row(ws, key) == before


# ---- failures -------------------------------------------------------------------------------------------------------

def test_a_failed_compare_and_set_changes_nothing(fam, monkeypatch):
    key, default, add, tags = fam
    ws = _ws()
    ws.update(key, add("base"), default=default)
    last = {}

    def raced(v):                                                     # someone else always writes first
        other = Workspace(ws.uid)
        W._PURE.depth -= 1
        try:
            other.update(key, add(f"other{len(last)}"), default=default)
        finally:
            W._PURE.depth += 1
        last["row"] = dict(_row(ws, key))
        return add("mine")(v)
    with pytest.raises(VersionConflict):
        ws.update(key, raced, default=default)
    assert _row(ws, key) == last["row"]                              # exactly the competitor's write, nothing of ours
    assert "mine" not in tags(ws.load(key, default))


def test_database_failure_during_the_write_never_falls_back(fam, monkeypatch):
    key, default, add, tags = fam
    ws = _ws()
    ws.update(key, add("base"), default=default)
    before = dict(_row(ws, key))
    real = M.db
    other_writes = []

    class Kv:
        def __init__(self, c):
            self.c = c

        def __getattr__(self, name):
            if name == "update_one":
                def fail(*a, **k):
                    raise AutoReconnect("primary stepped down")
                return fail
            if name in ("insert_one", "replace_one", "update_many", "find_one_and_update", "find_one_and_replace"):
                return lambda *a, **k: other_writes.append(name)
            return getattr(self.c, name)

    class Db:
        def __getitem__(self, name):
            return Kv(real[name]) if name == "kv" else real[name]
    monkeypatch.setattr(M, "db", Db())
    with pytest.raises(AutoReconnect):
        ws.update(key, add("lost"), default=default)
    with pytest.raises(AutoReconnect):
        ws.create(key + "_new", default)
    monkeypatch.setattr(M, "db", real)
    assert other_writes == [] and _row(ws, key) == before


def test_templates_keep_working_with_tokens():
    ws = _ws()
    W.seed_workspace(ws, "T", "t@example.com")
    row = _row(ws, "templates")
    assert "wid" in row and row["version"] >= 1_700_000_000_000
    default, add = FAMILIES["templates"][0], FAMILIES["templates"][1]
    ws.update("templates", add("more"), default=default)
    assert _row(ws, "templates")["wid"] != row["wid"] and _row(ws, "templates")["version"] > row["version"]


# ---- writer scan: computed keys, dynamic calls, direct collection writes -------------------------------------------

def _py_files():
    for p in BACKEND.rglob("*.py"):
        rel = p.relative_to(BACKEND).as_posix()
        if not rel.startswith((".venv/", "tests/", "tools/")) and "learning/content" not in rel:
            yield rel, p.read_text()


def test_writer_scan_covers_computed_keys_dynamic_calls_and_raw_collections():
    """Beyond literal names: every account-data write must be one of the known forms.
    - save() with a computed key is only allowed for the per-contact event records (f"contact:{...}");
    - no getattr()-based calls of save/update/create/replace_versioned with a non-literal name;
    - no other way to reach the kv collection (db["kv"], get_collection("kv"), db.kv) outside core/workspace.py and
      the one-off SQLite migration."""
    import ast
    bad = []
    for rel, src in _py_files():
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "save" and n.args:
                k = n.args[0]
                literal = isinstance(k, ast.Constant)
                contact = isinstance(k, ast.JoinedStr) and isinstance(k.values[0], ast.Constant) and \
                    str(k.values[0].value).startswith("contact:")
                if not literal and not contact and rel != "core/workspace.py":
                    bad.append(f"{rel}:{n.lineno} computed save key: {ast.unparse(k)}")
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr" and len(n.args) >= 2:
                name = n.args[1]
                if not isinstance(name, ast.Constant) and rel != "core/accounts.py":
                    bad.append(f"{rel}:{n.lineno} dynamic attribute: {ast.unparse(n)}")
            if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and n.slice.value == "kv":
                if rel not in ("core/workspace.py", "core/db.py"):
                    bad.append(f"{rel}:{n.lineno} {ast.unparse(n)}")
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get_collection":
                bad.append(f"{rel}:{n.lineno} {ast.unparse(n)}")
            if isinstance(n, ast.Attribute) and n.attr == "kv" and rel not in ("core/workspace.py", "migrations.py",
                                                                            "core/accounts.py"):
                parent = ast.unparse(n)
                if not parent.startswith(("M.kv.find", "core.M.kv.find", "C().M.kv.find")):
                    bad.append(f"{rel}:{n.lineno} {parent}")
    reviewed = {  # each checked by hand: not account data
        "core/tables.py": "openpyxl Workbook.save(buf): writes the spreadsheet export into memory",
        "infra/redact.py": "RedactingStream.__getattr__ forwards attributes to the wrapped stdout/stderr",
    }
    bad = [b for b in bad if b.split(":", 1)[0] not in reviewed]
    assert bad == [], "\n".join(bad)


def test_save_refuses_versioned_keys_even_when_computed():
    ws = _ws()
    for key in W.VERSIONED_KEYS:
        computed = "".join(list(key))                                   # not a literal anywhere
        with pytest.raises(RuntimeError, match="versioned"):
            ws.save(computed, None)
