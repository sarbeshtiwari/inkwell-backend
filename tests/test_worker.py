"""Agent worker (task PH1-11): concurrent task loops, graceful stop, a separate process resuming another's work."""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import DB_NAME, make_user

import app as A
from infra import tasks, worker


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    A.M.tasks.delete_many({})
    A.M.task_events.delete_many({})
    monkeypatch.setattr(tasks, "HANDLERS", dict(tasks.HANDLERS))
    yield
    A.M.tasks.delete_many({})


def wait_for(pred, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_pool_runs_tasks_concurrently_and_stops_cleanly():
    uid, _ = make_user()
    running, peak, mu = [0], [0], threading.Lock()

    def job(t):
        with mu:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.2)
        with mu:
            running[0] -= 1
    tasks.register("job", job)
    ids = [tasks.enqueue(uid, "job") for _ in range(6)]
    pool = worker.Pool(3).start()
    try:
        assert wait_for(lambda: A.M.tasks.count_documents({"_id": {"$in": ids}, "status": "done"}) == 6)
    finally:
        busy = pool.shutdown(grace=5)
    assert busy == 0 and peak[0] >= 2


def test_shutdown_stops_claiming_new_tasks():
    uid, _ = make_user()
    tasks.register("job", lambda t: None)
    pool = worker.Pool(2).start()
    assert pool.shutdown(grace=5) == 0
    tid = tasks.enqueue(uid, "job")
    time.sleep(0.3)
    assert A.M.tasks.find_one({"_id": tid})["status"] == "queued"


def test_start_with_zero_threads_does_nothing():
    assert worker.start(0) is None


def test_a_killed_worker_process_task_is_finished_by_another_worker():
    """A real second process claims a task, is SIGKILLed mid-task, and this process finishes it after the lease."""
    uid, _ = make_user()
    tasks.register("slow", lambda t: "finished here")
    tid = tasks.enqueue(uid, "slow")
    backend = Path(__file__).resolve().parents[1]
    code = ("import os, time\n"
            "from core.accounts import init_storage\n"
            "init_storage()\n"
            "from infra import tasks\n"
            "tasks.register('slow', lambda t: time.sleep(60))\n"
            "t = tasks.claim('child-process', lease=1)\n"
            "print('claimed' if t else 'none', flush=True)\n"
            "tasks.execute(t)\n")
    env = {**os.environ, "MONGODB_DB": DB_NAME, "INKWELL_NO_DOTENV": "1", "BOUNCE_WATCH": "0"}
    p = subprocess.Popen([sys.executable, "-c", code], cwd=backend, env=env, stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "claimed"
        assert A.M.tasks.find_one({"_id": tid})["status"] == "running"
        p.send_signal(signal.SIGKILL)
        p.wait(10)
        assert wait_for(lambda: tasks.run_once("parent", lease=5) is not None, timeout=10)
        v = tasks.get(uid, tid)
        assert v["status"] == "done" and v["result"] == "finished here" and v["attempts"] == 2
    finally:
        if p.poll() is None:
            p.kill()
