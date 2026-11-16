"""Self-update: fast-forward only, refuses over local edits, records the old version, and can undo."""

import subprocess
import time

import pytest

from features import updater as U


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    origin, work, other = tmp_path / "origin.git", tmp_path / "backend", tmp_path / "other"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    for d in (work, other):
        sh(tmp_path, "git", "clone", "-q", str(origin), str(d))
        sh(d, "git", "config", "user.email", "t@example.com"); sh(d, "git", "config", "user.name", "T")
    (other / "a.txt").write_text("1"); sh(other, "git", "add", "."); sh(other, "git", "commit", "-qm", "one"); sh(other, "git", "push", "-q", "origin", "HEAD:main")
    sh(work, "git", "pull", "-q", "origin", "main"); sh(work, "git", "branch", "-u", "origin/main")
    (other / "a.txt").write_text("2"); sh(other, "git", "commit", "-qam", "two"); sh(other, "git", "push", "-q", "origin", "HEAD:main")
    monkeypatch.setattr(U, "REPOS", {"backend": work})
    monkeypatch.setattr(U, "HISTORY", tmp_path / "data" / "update-history.json")
    monkeypatch.setattr(U, "rebuild", lambda changed: None)
    monkeypatch.setattr(U, "restart", lambda: None)
    return work


def wait():
    for _ in range(100):
        if U.STATE["state"] not in ("running",):
            break
        time.sleep(0.02)


def test_update_and_undo(checkout):
    s = U.repo_status("backend", checkout)
    assert s["behind"] == 1 and s["new"][0]["subject"] == "two" and not U.blockers([s])
    U.STATE["state"] = "running"; U.run_update(); wait()
    assert (checkout / "a.txt").read_text() == "2" and U.STATE["state"] == "restarting"
    hist = U.load_history()
    U.STATE["state"] = "running"; U.run_update(hist[-1]["versions"]); wait()
    assert (checkout / "a.txt").read_text() == "1"


def test_refuses_over_local_edits(checkout):
    (checkout / "a.txt").write_text("mine")
    U.STATE["state"] = "running"; U.run_update(); wait()
    assert U.STATE["state"] == "error" and "changed file" in U.STATE["error"]
    assert (checkout / "a.txt").read_text() == "mine"
