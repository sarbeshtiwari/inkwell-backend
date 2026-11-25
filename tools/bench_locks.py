"""Benchmark protected account writes: the deployed baseline (process-local lock, plain save) against the current
shared-lease + transaction path, on equal workloads. Prints JSON with median/p95/p99 (ms), MongoDB operations per
account operation, sequential and concurrent results, and contention.

Run where the app runs (e.g. Render shell) to measure its real network path:

    python tools/bench_locks.py            # N=60 iterations; set N=200 for more

It works in a temporary database named inkwell_bench_<time> on the configured cluster and drops it at the end; it never
touches the app's own database."""
import json
import os
import statistics
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = f"inkwell_bench_{int(time.time())}"
os.environ["MONGODB_DB"] = DB

from pymongo import monitoring  # noqa: E402


class Counter(monitoring.CommandListener):
    IGNORE = {"hello", "isMaster", "ping", "endSessions", "abortTransaction"}

    def __init__(self):
        self.n, self.on = {}, threading.local()

    def started(self, e):
        if getattr(self.on, "x", False) and e.command_name not in self.IGNORE:
            self.n[e.command_name] = self.n.get(e.command_name, 0) + 1

    def succeeded(self, e):
        pass

    def failed(self, e):
        pass


COUNT = Counter()
monitoring.register(COUNT)

from core.db import M  # noqa: E402

M.connect()
assert M.db.name == DB and DB.startswith("inkwell_bench_")

from core import workspace  # noqa: E402
from core.crypto import seal  # noqa: E402
from infra import locks  # noqa: E402

CURRENT_INIT, CURRENT_SAVE = workspace.Workspace.__init__, workspace.Workspace.save


def use(mode):
    """'baseline': the deployed behaviour (per-process RLock, full replace). 'current': this code."""
    workspace.Workspace.__init__, workspace.Workspace.save = CURRENT_INIT, CURRENT_SAVE
    locks._USER_LOCKS.clear()
    if mode == "baseline":
        rlocks = {}

        def init(self, uid):
            self.uid, self.lock = uid, rlocks.setdefault(uid, threading.RLock())

        def save(self, key, value):
            blob = seal(value)
            if workspace.DELETED.get(self.uid) or not M.users.find_one({"_id": self.uid}, {"_id": 1}):
                return
            M.kv.replace_one({"_id": f"{self.uid}:{key}"}, {"uid": self.uid, "key": key, "data": blob}, upsert=True)
        workspace.Workspace.__init__, workspace.Workspace.save = init, save


def stats(xs):
    xs = sorted(xs)
    at = lambda p: xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))]  # noqa: E731
    return {"median": round(statistics.median(xs), 2), "p95": round(at(0.95), 2), "p99": round(at(0.99), 2), "n": len(xs)}


def timed(fn, n):
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    return out


def new_users(k):
    ids = ["b" + uuid.uuid4().hex[:12] for _ in range(k)]
    M.users.insert_many([{"_id": u} for u in ids])
    return ids


def account_op(ws):  # a typical protected change: lock, read, modify, save, release
    with ws.lock:
        v = ws.load("bench", {"n": 0})
        v["n"] += 1
        ws.save("bench", v)


def run(n):
    out = {"ping": stats(timed(lambda: M.client.admin.command("ping"), 30))}
    for mode in ("baseline", "current"):
        use(mode)
        r = out[mode] = {}
        uid = new_users(1)[0]
        ws = workspace.Workspace(uid)
        ws.save("bench", {"n": 0})
        r["read"] = stats(timed(lambda ws=ws: ws.load("bench", {}), n))
        r["save_unlocked"] = stats(timed(lambda ws=ws: ws.save("bench", {"n": 1}), n))
        if mode == "current":
            parts = []
            for _ in range(n):
                t0 = time.perf_counter()
                lease = locks.acquire("bench", uid)
                t1 = time.perf_counter()
                lease.renew()
                t2 = time.perf_counter()
                lease.release()
                parts.append(((t1 - t0) * 1000, (t2 - t1) * 1000, (time.perf_counter() - t2) * 1000))
            r["acquire"], r["renew"], r["release"] = (stats([p[i] for p in parts]) for i in range(3))  # noqa: B023

            def locked_save(ws=ws):
                with ws.lock:
                    ws.save("bench", {"n": 2})
            r["locked_save"] = stats(timed(locked_save, n))
        r["account_op_sequential"] = stats(timed(lambda ws=ws: account_op(ws), n))
        COUNT.n, COUNT.on.x = {}, True
        account_op(ws)
        COUNT.on.x = False
        r["mongo_ops_per_account_op"] = {"total": sum(COUNT.n.values()), **COUNT.n}
        per_thread = max(5, n // 4)
        for label, ids in (("concurrent_8_accounts", new_users(8)), ("contended_8_threads_1_account", [uid] * 8)):
            workspace.Workspace(uid).save("bench", {"n": 0})
            lat, errors = [], []

            def worker(u, lat=lat, errors=errors, per_thread=per_thread):
                w = workspace.Workspace(u)
                for _ in range(per_thread):
                    t = time.perf_counter()
                    try:
                        account_op(w)
                    except Exception as e:  # counted, not hidden
                        errors.append(type(e).__name__)
                    lat.append((time.perf_counter() - t) * 1000)
            t0 = time.perf_counter()
            threads = [threading.Thread(target=worker, args=(u,)) for u in ids]
            [t.start() for t in threads]
            [t.join() for t in threads]
            r[label] = {**stats(lat), "ops_per_s": round(len(lat) / (time.perf_counter() - t0), 1), "errors": len(errors)}
        r["lost_updates_contended"] = 8 * per_thread - workspace.Workspace(uid).load("bench", {})["n"]
    return out


if __name__ == "__main__":
    try:
        print(json.dumps(run(int(os.environ.get("N", "60"))), indent=1))
    finally:
        M.client.drop_database(DB)
