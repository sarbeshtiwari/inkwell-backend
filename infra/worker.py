"""Agent worker: runs queued tasks (infra/tasks.py) outside the web process.

    python -m infra.worker              # from backend/; AGENT_WORKER_THREADS (default 2) tasks at a time

It connects to the same database as the web app (MONGODB_URI / MONGODB_DB, .env locally), loads the modules that
register tools, agents and task handlers, and runs that many worker loops. It doesn't serve HTTP and doesn't start the
web app's recurring loops.

Stopping (SIGTERM or Ctrl-C): no new task is claimed; running tasks get AGENT_WORKER_GRACE_SECONDS (default 20) to
reach their end, then the process exits. A task still running at exit isn't recorded as anything: its lease runs out
and another worker resumes it from its last checkpoint. The worker can't stop a step mid-way; anything that step had
started (an approved external action) is covered by the approval record, which never repeats it automatically.

In-process alternative for local development: start(n) runs the same loops as daemon threads of the current process
(the web app does this when AGENT_WORKER_THREADS is set in its own environment)."""

import logging
import os
import signal
import socket
import sys
import threading

log = logging.getLogger("inkwell.worker")

HANDLER_MODULES = ("features.ai_jobs", "infra.supervisor")  # importing these registers their tools, agents and task handlers


def _int(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def load_handlers():
    import importlib
    for m in HANDLER_MODULES:
        importlib.import_module(m)


class Pool:
    def __init__(self, n, name=None):
        self.stop = threading.Event()
        self.name = name or f"{socket.gethostname()}:{os.getpid()}"
        self.threads = [threading.Thread(target=self._loop, args=(i,), daemon=True, name=f"agent-worker-{i}")
                        for i in range(n)]

    def _loop(self, i):
        from infra import tasks
        try:
            tasks.work(self.stop, worker=f"{self.name}:{i}")
        except BaseException as e:  # a worker loop must never die silently
            from infra.redact import error_text
            log.error("agent worker %s stopped: %s", i, error_text(e, 200))

    def start(self):
        for t in self.threads:
            t.start()
        return self

    def shutdown(self, grace=None):
        """Stop claiming; wait up to `grace` seconds for running tasks. Returns how many loops were still busy."""
        grace = _int("AGENT_WORKER_GRACE_SECONDS", 20) if grace is None else grace
        self.stop.set()
        for t in self.threads:
            t.join(timeout=max(0.0, grace / max(1, len(self.threads))))
        return sum(t.is_alive() for t in self.threads)


def start(n=None):
    """Run n worker loops in this process (daemon threads). Returns the Pool, or None when n is 0."""
    n = _int("AGENT_WORKER_THREADS", 2) if n is None else n
    if n <= 0:
        return None
    load_handlers()
    return Pool(n).start()


def main():
    done = threading.Event()

    def on_signal(signum, frame):
        print("[Inkwell] agent worker stopping: no new tasks will start", flush=True)
        done.set()
    signal.signal(signal.SIGTERM, on_signal)  # installed first, so a stop during startup is clean too
    signal.signal(signal.SIGINT, on_signal)
    from core.accounts import init_storage
    init_storage()
    if done.is_set():
        return 0
    pool = start(_int("AGENT_WORKER_THREADS", 2) or 1)
    print(f"[Inkwell] agent worker running {len(pool.threads)} task loop(s)", flush=True)
    done.wait()
    busy = pool.shutdown()
    if busy:
        print(f"[Inkwell] {busy} task(s) still running at exit; they resume elsewhere from their checkpoints", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
