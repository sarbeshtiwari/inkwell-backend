"""Execution telemetry (task PH1-03): correlated traces and spans, redacted, owner-scoped, never breaking the work."""

import threading

import pytest
from conftest import make_user

import app as A
from features.ai_jobs import models
from infra import gateway, tasks, telemetry
from infra.gateway import FakeProvider


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for c in (A.M.traces, A.M.spans, A.M.tasks, A.M.task_events):
        c.delete_many({})
    monkeypatch.setattr(tasks, "HANDLERS", {})
    yield


def cfg(uid):
    return {"provider": "fake", "model": "m", "uid": uid, "label": "x"}


def spans(tid):
    return list(A.M.spans.find({"trace_id": tid}).sort("start", 1))


def test_nested_spans_are_correlated_and_totals_add_up(monkeypatch):
    uid, _ = make_user()
    monkeypatch.setitem(gateway.ADAPTERS, "fake", FakeProvider("a", "b"))
    with telemetry.trace(uid, "find and prepare") as tid:
        with telemetry.span("delegation", "job_scout", agent_id="orchestrator") as d:
            with telemetry.span("tool_call", "search", tool_id="sources.search") as t:
                t.update(summary="12 jobs found")
            models.chat(cfg(uid), "You judge job fit.", "Resume and posting text for the job.")
        models.chat(cfg(uid), "You judge job fit.", "Resume and posting text for the job.")
    sp = spans(tid)
    by = {s["name"]: s for s in sp}
    assert by["search"]["parent_span_id"] == by["job_scout"]["span_id"] == d.span_id
    assert by["job_scout"]["parent_span_id"] is None
    model_spans = [s for s in sp if s["type"] == "model_call"]
    assert len(model_spans) == 2 and model_spans[0]["parent_span_id"] == d.span_id and model_spans[1]["parent_span_id"] is None
    assert all(s["model_id"] == "fake-model-1" and s["uid"] == uid for s in model_spans)
    usage = list(A.M.model_usage.find({"trace_id": tid}))
    assert sorted(u["span_id"] for u in usage) == sorted(s["span_id"] for s in model_spans)
    t = A.M.traces.find_one({"_id": tid})
    assert t["status"] == "ok" and t["ended"] >= t["started"]
    assert t["totals"]["spans"] == 4 and t["totals"]["model_calls"] == 2
    assert t["totals"]["input_tokens"] == sum(s["input_tokens"] for s in model_spans) > 0


def test_errors_mark_span_and_trace_and_propagate_unchanged():
    uid, _ = make_user()
    with pytest.raises(KeyError):
        with telemetry.trace(uid, "x") as tid:
            with telemetry.span("tool_call", "boom"):
                raise KeyError("missing")
    [s] = spans(tid)
    assert s["status"] == "error" and s["error_class"] == "keyerror"
    t = A.M.traces.find_one({"_id": tid})
    assert t["status"] == "error" and t["totals"]["errors"] == 1


def test_failed_model_call_produces_an_error_span(monkeypatch):
    uid, _ = make_user()
    monkeypatch.setitem(gateway.ADAPTERS, "fake", FakeProvider(gateway.GatewayError("rate limited", "rate_limit")))
    with telemetry.trace(uid, "x") as tid:
        with pytest.raises(gateway.GatewayError):
            models.chat(cfg(uid), "s", "h")
    [s] = spans(tid)
    assert s["type"] == "model_call" and s["status"] == "error" and s["error_class"] == "rate_limit"


def test_summaries_are_redacted_and_payloads_sealed():
    uid, _ = make_user()
    with telemetry.trace(uid, "x") as tid:
        with telemetry.span("tool_call", "mail") as s:
            s.update(summary="wrote to jane.doe@acme.com, +91 98765 43210, key sk-ant-" + "a" * 30,
                     payload={"resume": "PRIVATE-RESUME-TEXT"})
    raw = repr(A.M.spans.find_one({"trace_id": tid}))
    for leak in ("jane.doe@", "98765 43210", "sk-ant-aaaa", "PRIVATE-RESUME-TEXT"):
        assert leak not in raw
    assert "acme.com" in raw  # masked address keeps its domain
    own = telemetry.owner_trace(uid, tid)
    assert own["spans"][0]["payload"] == {"resume": "PRIVATE-RESUME-TEXT"}


def test_traces_are_owner_only_and_admin_view_has_no_content():
    a, _ = make_user()
    b, _ = make_user()
    with telemetry.trace(a, "a's job search for Acme") as tid:
        telemetry.record("approval", "submit to Acme", summary="Apply to Acme as backend dev")
    assert telemetry.owner_trace(b, tid) is None
    meta = telemetry.metadata_trace(tid)
    flat = repr(meta)
    assert "Acme" not in flat and "summary" not in flat and "payload" not in flat
    assert meta["spans"][0]["type"] == "approval" and meta["trace"]["uid"] == a
    with pytest.raises(PermissionError):
        with telemetry.attach(tid, b):
            pass


def test_spans_outside_a_trace_are_no_ops_and_bad_fields_refused():
    with telemetry.span("step", "nothing") as s:
        assert s is None
    uid, _ = make_user()
    with telemetry.trace(uid, "x"):
        with pytest.raises(ValueError):
            with telemetry.span("thinking", "no such type"):
                pass
        with pytest.raises(ValueError):
            with telemetry.span("step", "x", chain_of_thought="..."):
                pass


def test_context_is_per_thread():
    uid, _ = make_user()
    seen = {}
    with telemetry.trace(uid, "x"):
        th = threading.Thread(target=lambda: seen.update(c=telemetry.current()))
        th.start()
        th.join()
        assert telemetry.current() is not None
    assert seen["c"] is None and telemetry.current() is None


def test_a_telemetry_store_failure_never_breaks_the_work(monkeypatch):
    uid, _ = make_user()
    from pymongo.errors import AutoReconnect

    class Broken:
        def __getattr__(self, name):
            def fail(*a, **k):
                raise AutoReconnect("down")
            return fail
    real = A.M.__class__.__getattr__
    monkeypatch.setattr(A.M.__class__, "__getattr__", lambda self, n: Broken() if n in ("spans", "traces") else real(self, n))
    with telemetry.trace(uid, "x"):
        with telemetry.span("step", "still works"):
            value = 42
    assert value == 42


def test_tasks_report_into_one_trace_with_children_and_retries(monkeypatch):
    uid, _ = make_user()
    monkeypatch.setattr(tasks, "RETRY_BASE_SECONDS", 0.01)
    attempts = []

    def parent(t):
        tasks.enqueue(t.uid, "child", parent_id=t.id, agent_id="job_scout")
        return "planned"

    def child(t):
        attempts.append(t.attempt)
        if t.attempt == 1:
            raise ConnectionError("flaky")
        return "ok"
    tasks.register("parent", parent)
    tasks.register("child", child)
    root = tasks.enqueue(uid, "parent", agent_id="orchestrator")
    import time
    for _ in range(20):
        if tasks.run_once("w", lease=5) is None:
            time.sleep(0.02)
        if attempts == [1, 2]:
            break
    trace_id = tasks.get(uid, root)["trace_id"]
    assert trace_id == root
    child_task = A.M.tasks.find_one({"parent_id": root})
    assert child_task["trace_id"] == trace_id
    steps = [s for s in spans(trace_id) if s["type"] == "step"]
    assert [(s["name"], s["status"], s.get("error_class")) for s in steps] == [
        ("parent", "ok", None), ("child", "error", "retry_scheduled"), ("child", "ok", None)]
    assert A.M.traces.find_one({"_id": trace_id})["status"] == "done"


def test_account_deletion_removes_traces_and_spans():
    uid, _ = make_user()
    with telemetry.trace(uid, "x") as tid:
        telemetry.record("step", "y")
    A.delete_user_data(uid)
    assert A.M.traces.count_documents({"uid": uid}) == 0 and A.M.spans.count_documents({"trace_id": tid}) == 0
