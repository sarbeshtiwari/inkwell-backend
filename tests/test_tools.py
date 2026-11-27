"""Tool registry and ToolContext (task PH1-06) and cross-user isolation for tools (task SEC-05)."""

import pytest
from conftest import make_user

import app as A
from features.ai_jobs import agent_tools, ranking, signals
from infra import controls, rbac, telemetry, tools
from infra.tools import ToolContext


@pytest.fixture(autouse=True)
def _clean():
    A.M.kill_switches.delete_many({})
    yield
    A.M.kill_switches.delete_many({})
    for tid in [t for t in tools.REGISTRY if t.startswith("test.")]:
        tools.REGISTRY.pop(tid)


def ctx(uid, *grants, mode="on", agent_id="job_analyst"):
    return ToolContext(uid=uid, grants=frozenset(grants), mode=mode, agent_id=agent_id)


def save_rules(uid, **rules):
    ws = A.Workspace(uid)
    with ws.lock:
        ws.save("ai_rules", rules)


EMPTY = {"type": "object", "additionalProperties": False, "properties": {}}


def test_registered_agent_tools_are_read_only_and_well_formed():
    ids = {t["id"] for t in tools.describe()}
    assert {"profile.candidate", "rules.read", "preferences.taste", "jobs.search", "jobs.rank", "jobs.liveness"} <= ids
    for t in tools.describe():
        if not t["id"].startswith("test."):
            assert t["input_schema"]["additionalProperties"] is False
            # the only tool that acts outside Inkwell is application submission, and it is approval-gated
            # the only tool that acts outside Inkwell is application submission (approval-gated); the Supervisor's
            # control tools change only Inkwell's own agent state
            expected = "external_action" if t["id"] == "applications.submit" else \
                "write_internal" if t["id"] in ("supervision.pause_agent", "supervision.cancel_task", "supervision.alert") else "read"
            assert t["risk"] == expected
    assert [t["id"] for t in tools.describe({"rules.read"})] == ["rules.read"]


def test_registration_refuses_open_input_schemas_and_bad_ids():
    with pytest.raises(ValueError):
        tools.register(tools.Tool("test.open", "1", "", {"type": "object"}, {}, tools.READ, lambda c: None))
    with pytest.raises(ValueError):
        tools.register(tools.Tool("Bad Id", "1", "", EMPTY, {}, tools.READ, lambda c: None))
    with pytest.raises(ValueError):
        tools.register(tools.Tool("test.risk", "1", "", EMPTY, {}, "admin", lambda c: None))
    with pytest.raises(ValueError):
        tools.register(tools.Tool("test.keys", "1", "", {**EMPTY, "$where": "x"}, {}, tools.READ, lambda c: None))


def test_tools_act_for_the_context_user_only():
    a, _ = make_user()
    b, _ = make_user()
    save_rules(a, blacklist=["acme-for-a"])
    save_rules(b, blacklist=["globex-for-b"])
    assert tools.call(ctx(a, "rules.read"), "rules.read")["blacklist"] == ["acme-for-a"]
    assert tools.call(ctx(b, "rules.read"), "rules.read")["blacklist"] == ["globex-for-b"]
    for smuggled in ({"uid": b}, {"user_id": b}, {"ws": b}):  # a model can't choose whose data a tool reads
        with pytest.raises(tools.InvalidArguments, match="unexpected field"):
            tools.call(ctx(a, "rules.read"), "rules.read", smuggled)


def test_context_needs_a_user_and_a_known_mode():
    with pytest.raises(ValueError):
        ToolContext(uid="", grants=frozenset())
    with pytest.raises(ValueError):
        ToolContext(uid="u1", grants=frozenset(), mode="yolo")
    with pytest.raises(TypeError):
        tools.call({"uid": "u1", "grants": {"rules.read"}}, "rules.read")  # a plain dict is not a context


def test_ungranted_tools_are_refused_and_audited():
    a, _ = make_user()
    with pytest.raises(tools.ToolDenied, match="may not use"):
        tools.call(ctx(a, "jobs.rank"), "rules.read")
    ev = A.M.audit_log.find_one({"action": "access.denied", "target": "tool:rules.read", "actor": a})
    assert ev and ev["details"]["reason"] == "not_granted"
    with pytest.raises(tools.ToolNotFound):
        tools.call(ctx(a, "nope.tool"), "nope.tool")


def test_risk_classes_shadow_mode_and_approval():
    a, _ = make_user()
    ran = []
    tools.register(tools.Tool("test.write", "1", "", EMPTY, {"type": "boolean"}, tools.WRITE_INTERNAL, lambda c: ran.append("w") or True))
    tools.register(tools.Tool("test.send", "1", "", EMPTY, {"type": "boolean"}, tools.EXTERNAL_ACTION, lambda c: ran.append("s") or True))
    tools.register(tools.Tool("test.delete", "1", "", EMPTY, {"type": "boolean"}, tools.DESTRUCTIVE, lambda c: ran.append("d") or True))
    every = ("test.write", "test.send", "test.delete")
    with pytest.raises(tools.ShadowRefused):
        tools.call(ctx(a, *every, mode="shadow"), "test.write")
    with pytest.raises(tools.ShadowRefused):
        tools.call(ctx(a, *every, mode="shadow"), "test.send")
    assert tools.call(ctx(a, *every), "test.write") is True
    with pytest.raises(tools.ApprovalRequired):  # never runs from a plain call, granted or not, mode on or not
        tools.call(ctx(a, *every), "test.send")
    with pytest.raises(tools.ToolDenied):
        tools.call(ctx(a, *every), "test.delete")
    assert ran == ["w"]


def test_kill_switch_stops_tool_calls(monkeypatch):
    a, email = make_user()
    admin, admin_email = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {admin_email})
    rbac.reconcile()
    controls.set_switch(admin, agent_id="job_analyst", reason="stop analyst")
    with pytest.raises(tools.ToolDenied, match="kill switch"):
        tools.call(ctx(a, "rules.read"), "rules.read")
    assert tools.call(ctx(a, "rules.read", agent_id="job_scout"), "rules.read") is not None


def test_arguments_and_results_are_validated():
    a, _ = make_user()
    for bad in ({}, {"role": "dev", "years": "five", "jobs": []}, {"role": "dev", "years": 99, "jobs": []},
                {"role": "dev", "years": 2, "jobs": [{"id": "x"}]}):
        with pytest.raises(tools.InvalidArguments):
            tools.call(ctx(a, "jobs.rank"), "jobs.rank", bad)
    tools.register(tools.Tool("test.liar", "1", "", EMPTY, {"type": "object", "required": ["n"]}, tools.READ, lambda c: {"m": 1}))
    with pytest.raises(tools.InvalidResult):
        tools.call(ctx(a, "test.liar"), "test.liar")


def test_rank_tool_runs_the_real_engine_with_the_context_users_resume(monkeypatch):
    a, _ = make_user()
    seen = []
    monkeypatch.setattr(agent_tools, "_profile",
                        lambda ws, role, years, location="", company="": seen.append(ws.uid) or
                        {"role": role, "years": years, "location": location, "company": company,
                         "resume": "python django postgres backend developer", "skills": ["Python", "Django", "PostgreSQL"]})
    jobs = [{"id": "gh:acme:1", "title": "Backend Developer", "company": "Acme", "text": "Python Django PostgreSQL, 2+ years"},
            {"id": "gh:acme:2", "title": "Sales Manager", "company": "Acme", "text": "quota, CRM"}]
    out = tools.call(ctx(a, "jobs.rank"), "jobs.rank", {"role": "backend developer", "years": 3, "jobs": jobs})
    assert seen == [a] and [r["id"] for r in out][0] == "gh:acme:1" and out[0]["score"] > out[1]["score"]
    assert "text" not in out[0] and out[0]["reasons"]


def test_search_tool_reuses_sources_and_skips_keyless_web_search(monkeypatch):
    a, _ = make_user()
    calls = []

    class FakeTool:
        def __init__(self, jobs):
            self.jobs = jobs

        def invoke(self, args):
            calls.append(args)
            return self.jobs
    same = {"id": "gh:a:1", "title": "Dev", "company": "A", "ats": "greenhouse", "url": "u", "text": ""}
    monkeypatch.setattr(agent_tools, "make_tools", lambda keys, portals, location: {
        "career_sites": FakeTool([same, {**same, "id": "site:a:1", "ats": "site"}]), "remote_boards": FakeTool([])})
    out = tools.call(ctx(a, "jobs.search"), "jobs.search", {"role": "dev", "sources": ["career_sites", "tavily"]})
    assert [j["id"] for j in out["jobs"]] == ["gh:a:1"]  # duplicate across sources kept once
    assert out["errors"] == ["tavily: no search key saved"] and calls == [{"role": "dev", "company": ""}]
    with pytest.raises(tools.InvalidArguments):
        tools.call(ctx(a, "jobs.search"), "jobs.search", {"role": "dev", "sources": ["linkedin"]})


def test_liveness_tool(monkeypatch):
    a, _ = make_user()
    monkeypatch.setattr(signals, "ats_alive", lambda job: False)
    out = tools.call(ctx(a, "jobs.liveness"), "jobs.liveness", {"job": {"id": "gh:a:1", "title": "Dev", "company": "A", "url": "u"}})
    assert out["state"] == "expired"


def test_tool_calls_write_spans_without_arguments_or_results():
    a, _ = make_user()
    save_rules(a, blacklist=["SECRET-COMPANY-NAME"])
    with telemetry.trace(a, "x") as tid:
        tools.call(ctx(a, "rules.read"), "rules.read")
        with pytest.raises(tools.ToolDenied):
            tools.call(ctx(a), "rules.read")
    spans = list(A.M.spans.find({"trace_id": tid}).sort("start", 1))
    assert [(s["tool_id"], s["status"], s["error_class"]) for s in spans] == [
        ("rules.read", "ok", None), ("rules.read", "error", "permission_denied")]
    assert "SECRET-COMPANY-NAME" not in repr(spans) and "rules.read@1 (read) ok" in spans[0]["summary"]


def test_schema_validator_edge_cases():
    v = tools.validate
    v(3, {"type": "integer"})
    with pytest.raises(ValueError):
        v(True, {"type": "integer"})  # booleans are not numbers
    with pytest.raises(ValueError):
        v("abc", {"type": "string", "pattern": "[a-b]+"})  # whole-string match
    v(None, {"anyOf": [{"type": "string"}, {"type": "null"}]})
    with pytest.raises(ValueError):
        v([1, 2, 3], {"type": "array", "maxItems": 2})
    assert ranking.search_sources({}, [], {"role": "x"}) == ([], [])
