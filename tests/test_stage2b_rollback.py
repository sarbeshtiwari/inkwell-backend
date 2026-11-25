"""Verification pass for 5ba9022: legacy byte-identical rewrites, fresh ciphertext, and rollback/forward compatibility
with the real code of earlier releases (8b59986 = deployed, 5c21025 = pre-token versioned), run from `git archive`
in separate processes against the same test database."""
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile

import pytest
from conftest import DB_NAME, make_user

from core import crypto
from core.db import M
from core.workspace import Workspace

BACKEND = pathlib.Path(__file__).resolve().parents[1]
KEY = "inbox_companies"


def _env():
    return {**os.environ, "MONGODB_DB": DB_NAME, "INKWELL_NO_DOTENV": "1"}


@pytest.fixture(scope="module")
def release():
    """{commit: path} of the backend at that commit (git archive into a temp folder)."""
    if not shutil.which("git"):
        pytest.skip("needs git")
    root = pathlib.Path(tempfile.mkdtemp(prefix="inkwell-releases-"))
    trees = {}
    for commit in ("8b59986", "5c21025"):
        dest = root / commit
        dest.mkdir()
        archive = subprocess.run(["git", "archive", commit], cwd=BACKEND, capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)
        trees[commit] = dest
    yield trees
    shutil.rmtree(root, ignore_errors=True)


def _run(tree, code, *args, stdin=None):
    """Run `code` with that release's backend importable, against the shared test database."""
    head = f"import os, sys, json, time\nsys.path.insert(0, {str(tree)!r})\nfrom core.db import M\nM.connect()\n" \
           "from core.workspace import Workspace\n"
    return subprocess.Popen([sys.executable, "-c", head + code, *args], env=_env(), cwd=tree, stdin=stdin,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _out(p, timeout=60):
    out = p.communicate(timeout=timeout)[0]
    return out


def _row(ws):
    return M.kv.find_one({"_id": f"{ws.uid}:{KEY}"})


def _names(ws):
    return sorted(ws.load(KEY, {}))


def _add(tag):
    return lambda d: {**d, tag: {"name": tag}}


# ---- 1. fresh ciphertext ---------------------------------------------------------------------------------------------

def test_every_seal_produces_new_ciphertext():
    value = {"same": {"name": "value"}}
    blobs = {crypto.seal(value) for _ in range(200)}
    assert len(blobs) == 200                                  # random 128-bit IV every time (Fernet)
    assert all(crypto.unseal(b) == value for b in blobs)


def test_the_deployed_releases_save_always_writes_new_bytes(release):
    """8b59986's real Workspace.save, saving the same value twice: the stored bytes differ each time."""
    ws = Workspace(make_user()[0])
    seen = []
    for _ in range(2):
        p = _run(release["8b59986"], f"Workspace({ws.uid!r}).save({KEY!r}, {{'x': {{'name': 'same'}}}})\nprint('ok')")
        assert "ok" in _out(p)
        seen.append(_row(ws)["data"])
    assert seen[0] != seen[1] and ws.load(KEY, {}) == {"x": {"name": "same"}}


# ---- 2. legacy record rewritten with the exact same bytes ---------------------------------------------------------------

PAUSED = """
uid, key = sys.argv[1], sys.argv[2]
ws = Workspace(uid)
calls = []
def change(d):
    calls.append(sorted(d))
    if len(calls) == 1:
        print("A-read", flush=True)
        sys.stdin.readline()
    return {**d, "A": {"name": "A"}}
ws.update(key, change, default={})
print("attempts", len(calls), json.dumps(calls), flush=True)
"""


def _paused_new_writer(ws):
    p = _run(BACKEND, PAUSED, ws.uid, KEY, stdin=subprocess.PIPE)
    assert p.stdout.readline().strip() == "A-read"
    os.kill(p.pid, signal.SIGSTOP)
    return p


def _resume(p):
    os.kill(p.pid, signal.SIGCONT)
    p.stdin.write("go\n")
    p.stdin.flush()
    out = _out(p)
    return int(re.search(r"attempts (\d+)", out).group(1)), out


def test_legacy_record_rewritten_with_identical_bytes_matches_but_only_onto_the_same_value():
    """A reads a legacy record (no version, no wid). A byte-for-byte copy of that same document is written back (only
    possible with a restore or copy tool: the app always encrypts afresh). A's compare-and-set matches. This is not a
    stale overwrite: identical authenticated ciphertext means the stored value is exactly the one A computed from,
    so A's result is f(current value)."""
    ws = Workspace(make_user()[0])
    M.kv.insert_one({"_id": f"{ws.uid}:{KEY}", "uid": ws.uid, "key": KEY, "data": crypto.seal({"X": {"name": "X"}})})
    snapshot = dict(_row(ws))
    a = _paused_new_writer(ws)
    try:
        M.kv.replace_one({"_id": snapshot["_id"]}, {k: v for k, v in snapshot.items() if k != "_id"})   # same bytes
        attempts, out = _resume(a)
    finally:
        if a.poll() is None:
            os.kill(a.pid, signal.SIGCONT)
            a.kill()
    assert attempts == 1, out
    assert _names(ws) == ["A", "X"]


def test_a_restore_of_older_bytes_is_what_discards_intermediate_writes_not_the_new_writer():
    """A reads legacy X; B (new release) writes; then a restore/copy tool puts the old bytes X back. A then matches X
    and writes X+A. B's change is gone, but it was discarded by the restore, before A wrote: A's write is computed from
    the value actually stored. (Operational rule: don't restore or copy records while the app is running.)"""
    ws = Workspace(make_user()[0])
    M.kv.insert_one({"_id": f"{ws.uid}:{KEY}", "uid": ws.uid, "key": KEY, "data": crypto.seal({"X": {"name": "X"}})})
    snapshot = dict(_row(ws))
    a = _paused_new_writer(ws)
    try:
        ws.update(KEY, _add("B"), default={})
        assert _names(ws) == ["B", "X"]
        M.kv.replace_one({"_id": snapshot["_id"]}, {k: v for k, v in snapshot.items() if k != "_id"})  # the restore
        assert _names(ws) == ["X"]                                    # B already gone, by the restore
        attempts, out = _resume(a)
    finally:
        if a.poll() is None:
            os.kill(a.pid, signal.SIGCONT)
            a.kill()
    assert attempts == 1 and _names(ws) == ["A", "X"], out


def test_identical_plaintext_rewritten_by_the_old_release_is_still_detected(release):
    """The old release writing the very same value still re-encrypts: different bytes, so A recomputes."""
    ws = Workspace(make_user()[0])
    M.kv.insert_one({"_id": f"{ws.uid}:{KEY}", "uid": ws.uid, "key": KEY, "data": crypto.seal({"X": {"name": "X"}})})
    a = _paused_new_writer(ws)
    try:
        old = _run(release["8b59986"], f"Workspace({ws.uid!r}).save({KEY!r}, {{'X': {{'name': 'X'}}}})\nprint('ok')")
        assert "ok" in _out(old)
        attempts, out = _resume(a)
    finally:
        if a.poll() is None:
            os.kill(a.pid, signal.SIGCONT)
            a.kill()
    assert attempts == 2, out
    assert _names(ws) == ["A", "X"]


# ---- 3. rollback and forward with the real earlier releases -------------------------------------------------------

def test_rollback_to_the_deployed_release_and_forward_again(release):
    """5ba9022 writes -> 8b59986 (rollback) writes -> 5ba9022 (forward) writes: every write kept, fields dropped by the
    old release, then restored by the new one."""
    ws = Workspace(make_user()[0])
    ws.update(KEY, _add("new1"), default={})
    assert "wid" in _row(ws)
    old = _run(release["8b59986"], f"ws = Workspace({ws.uid!r})\nd = ws.load({KEY!r}, {{}})\nd['old'] = {{'name': 'old'}}\n"
                                   f"ws.save({KEY!r}, d)\nprint('ok')")
    assert "ok" in _out(old)
    assert "wid" not in _row(ws) and "version" not in _row(ws)
    ws.update(KEY, _add("new2"), default={})
    assert _names(ws) == ["new1", "new2", "old"] and "wid" in _row(ws)


def test_a_paused_new_writer_across_a_rollback_write_recomputes(release):
    """Even if a new-release writer survived across a rollback (it can't with no overlap, but if it did), an old-release
    write in between makes its compare-and-set fail."""
    ws = Workspace(make_user()[0])
    ws.update(KEY, _add("base"), default={})
    a = _paused_new_writer(ws)
    try:
        old = _run(release["8b59986"], f"ws = Workspace({ws.uid!r})\nd = ws.load({KEY!r}, {{}})\nd['old'] = {{'name': 'old'}}\n"
                                       f"ws.save({KEY!r}, d)\nprint('ok')")
        assert "ok" in _out(old)
        ws.update(KEY, _add("B"), default={})
        attempts, out = _resume(a)
    finally:
        if a.poll() is None:
            os.kill(a.pid, signal.SIGCONT)
            a.kill()
    assert attempts == 2 and _names(ws) == ["A", "B", "base", "old"], out


def test_records_written_by_the_pre_token_release_are_handled(release):
    """5c21025 writes (small integer version, no wid) -> 5ba9022 matches on version + no wid + exact bytes."""
    ws = Workspace(make_user()[0])
    p = _run(release["5c21025"], f"ws = Workspace({ws.uid!r})\nws.update({KEY!r}, lambda d: {{**d, 'pre': {{'name': 'pre'}}}}, "
                                 "default={})\nprint('ok')")
    assert "ok" in _out(p)
    row = _row(ws)
    assert row["version"] == 1 and "wid" not in row
    ws.update(KEY, _add("new"), default={})
    assert _names(ws) == ["new", "pre"] and "wid" in _row(ws) and _row(ws)["version"] > 1


def test_rolling_back_to_the_pre_token_release_is_not_supported(release):
    """5c21025 checks `type(version) is int`; MongoDB returns the server-computed versions of 5ba9022 as Int64, so the
    pre-token release refuses those records (500, nothing written). It fails closed, but it means 5c21025 (and
    1d544b7) must never be a rollback target once 5ba9022 has written: roll back to 8b59986 instead."""
    ws = Workspace(make_user()[0])
    ws.update(KEY, _add("new"), default={})
    before = dict(_row(ws))
    p = _run(release["5c21025"], f"ws = Workspace({ws.uid!r})\ntry:\n    ws.update({KEY!r}, lambda d: {{**d, 'x': {{}}}}, default={{}})\n"
                                 "    print('WROTE')\nexcept Exception as e:\n    print('refused', type(e).__name__)")
    out = _out(p)
    assert "refused VersionCorrupt" in out, out
    assert _row(ws) == before


def test_a_pre_token_writer_running_alongside_cannot_be_overwritten_by_a_stale_new_writer(release):
    """If 5c21025 and 5ba9022 ever overlapped: 5c21025 changes version (and data) but leaves the old wid. A paused
    5ba9022 writer that read (version, wid) no longer matches, because the version moved."""
    ws = Workspace(make_user()[0])
    p = _run(release["5c21025"], f"ws = Workspace({ws.uid!r})\nws.update({KEY!r}, lambda d: {{**d, 'pre': {{}}}}, default={{}})\nprint('ok')")
    assert "ok" in _out(p)
    ws.update(KEY, _add("base"), default={})
    M.kv.update_one({"_id": f"{ws.uid}:{KEY}"}, {"$set": {"version": 7}})   # a small int, readable by 5c21025
    a = _paused_new_writer(ws)
    try:
        p = _run(release["5c21025"], f"ws = Workspace({ws.uid!r})\nws.update({KEY!r}, lambda d: {{**d, 'pre2': {{}}}}, "
                                     "default={})\nprint('ok')")
        assert "ok" in _out(p)
        assert _row(ws)["version"] == 8 and "wid" in _row(ws)            # wid left as it was by the pre-token release
        attempts, out = _resume(a)
    finally:
        if a.poll() is None:
            os.kill(a.pid, signal.SIGCONT)
            a.kill()
    assert attempts == 2 and _names(ws) == ["A", "base", "pre", "pre2"], out
