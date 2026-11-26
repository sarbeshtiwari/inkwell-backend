"""Usage budgets (task PH1-02): scoped daily/monthly limits, per-task limits, refusal before the provider is called."""

import pytest
from conftest import H, client_for, make_user

import app as A
from features.ai_jobs import models
from infra import audit, budgets, gateway, rbac, tasks
from infra.gateway import FakeProvider, Reply


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for c in (A.M.budgets, A.M.model_usage, A.M.tasks, A.M.task_events):
        c.delete_many({})
    monkeypatch.setattr(tasks, "HANDLERS", {})
    yield
    for c in (A.M.budgets, A.M.model_usage, A.M.tasks, A.M.task_events):
        c.delete_many({})


@pytest.fixture
def admin(monkeypatch):
    uid, email = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {email})
    rbac.reconcile()
    return {"uid": uid, "client": client_for(uid)}


@pytest.fixture
def provider(monkeypatch):
    def install(tokens_in=100, tokens_out=50, n=50):
        f = FakeProvider(*[Reply("ok", model_id="m-1", input_tokens=tokens_in, output_tokens=tokens_out) for _ in range(n)])
        monkeypatch.setitem(gateway.ADAPTERS, "fake", f)
        return f
    return install


def cfg(uid, local=False):
    return {"provider": "fake", "model": "m", "uid": uid, "label": "x", "local": local}


def test_no_budgets_means_nothing_is_refused(provider):
    uid, _ = make_user()
    provider()
    for _ in range(5):
        assert models.chat(cfg(uid), "s", "h") == "ok"


def test_user_daily_token_budget_refuses_before_the_provider_is_called(admin, provider):
    uid, _ = make_user()
    other, _ = make_user()
    f = provider()
    budgets.set_budget(admin["uid"], uid=uid, limits={"daily_tokens": 300}, applies_to=["byok"], reason="test")
    models.chat(cfg(uid), "s", "h")          # 150 tokens
    models.chat(cfg(uid), "s", "h")          # 300 tokens: at the limit
    with pytest.raises(gateway.GatewayError, match="daily tokens limit for your account") as e:
        models.chat(cfg(uid), "s", "h")
    assert e.value.error_class == "budget" and len(f.calls) == 2
    refused = A.M.model_usage.find_one({"uid": uid, "status": "refused"})
    assert refused and refused["error_class"] == "budget" and refused["input_tokens"] is None
    assert models.chat(cfg(other), "s", "h") == "ok"  # another user's budget is untouched


def test_budgets_only_count_the_billing_kinds_they_apply_to(admin, provider):
    uid, _ = make_user()
    provider()
    budgets.set_budget(admin["uid"], uid=uid, limits={"daily_calls": 1}, reason="platform only")  # default: platform
    for _ in range(3):  # byok calls neither count nor are limited by a platform budget
        models.chat(cfg(uid), "s", "h")
    budgets.set_budget(admin["uid"], uid=uid, limits={"daily_calls": 1}, applies_to=["local"], reason="local")
    models.chat(cfg(uid, local=True), "s", "h")
    with pytest.raises(gateway.GatewayError):
        models.chat(cfg(uid, local=True), "s", "h")
    assert models.chat(cfg(uid), "s", "h") == "ok"


def test_global_and_agent_budgets(admin, provider):
    a, _ = make_user()
    b, _ = make_user()
    provider()
    budgets.set_budget(admin["uid"], limits={"monthly_calls": 2}, applies_to=["byok"], reason="global cap")
    models.chat(cfg(a), "s", "h")
    models.chat(cfg(b), "s", "h")
    with pytest.raises(gateway.GatewayError, match="the whole service"):
        models.chat(cfg(b), "s", "h")
    A.M.budgets.delete_many({})
    budgets.set_budget(admin["uid"], agent_id="writer", limits={"daily_calls": 1}, applies_to=["byok"], reason="writer")
    with gateway.context(agent_id="writer"):
        models.chat(cfg(a), "s", "h")
        with pytest.raises(gateway.GatewayError, match="writer agent"):
            models.chat(cfg(a), "s", "h")
    with gateway.context(agent_id="job_scout"):
        assert models.chat(cfg(a), "s", "h") == "ok"


def test_cost_budget_uses_recorded_costs(admin, provider):
    uid, _ = make_user()
    A.M.model_prices.insert_one({"provider": "fake", "model": "m-1", "effective_from": 0, "input_per_mtok": 10_000,
                                 "output_per_mtok": 10_000, "currency": "USD", "version": "t"})
    try:
        provider()
        budgets.set_budget(admin["uid"], uid=uid, limits={"daily_cost": 2.0}, applies_to=["byok"], reason="cost")
        models.chat(cfg(uid), "s", "h")  # 150 tokens at $0.01 each = $1.50
        models.chat(cfg(uid), "s", "h")  # $3.00 total
        with pytest.raises(gateway.GatewayError, match="daily cost"):
            models.chat(cfg(uid), "s", "h")
    finally:
        A.M.model_prices.delete_many({"provider": "fake"})


def test_task_budget_covers_the_whole_task_tree_and_stops_it_without_retry(provider):
    uid, _ = make_user()
    provider()

    def root(t):
        models.chat(cfg(t.uid), "s", "h")
        tasks.enqueue(t.uid, "child", parent_id=t.id, budget={"max_calls": 99})  # can't widen the tree's budget
        return "planned"

    def child(t):
        models.chat(cfg(t.uid), "s", "h")
        models.chat(cfg(t.uid), "s", "h")  # third call in the tree: over max_calls=2
        return "unreachable"
    tasks.register("root", root)
    tasks.register("child", child)
    rid = tasks.enqueue(uid, "root", budget={"max_calls": 2}, max_attempts=3)
    while tasks.run_once("w", lease=5):
        pass
    c = A.M.tasks.find_one({"parent_id": rid})
    assert c["budget"] == {"max_calls": 2}
    assert c["status"] == "failed" and c["error_class"] == "budget" and c["attempts"] == 1
    assert A.M.model_usage.count_documents({"root_task_id": rid, "status": "ok"}) == 2


def test_budget_changes_need_permission_are_validated_and_audited(admin):
    u, _ = make_user()
    with pytest.raises(A.Invalid) as e:
        budgets.set_budget(u, uid=u, limits={"daily_calls": 1000000}, reason="raise my own")
    assert e.value.status == 403
    for bad in ({"daily_bananas": 1}, {"daily_calls": -1}, {"daily_calls": "lots"}):
        with pytest.raises(A.Invalid):
            budgets.set_budget(admin["uid"], limits=bad, reason="bad")
    with pytest.raises(A.Invalid):
        budgets.set_budget(admin["uid"], limits={"daily_calls": 1}, applies_to=["everything"], reason="bad")
    tasks.register("x", lambda t: None)
    for bad in ({"max_dollars": 1}, {"max_calls": -1}, "lots"):
        with pytest.raises(ValueError):
            tasks.enqueue(u, "x", budget=bad)
    budgets.set_budget(admin["uid"], uid=u, limits={"daily_calls": 5}, reason="audited")
    ev = A.M.audit_log.find_one({"action": "budget.set", "target": f"user:{u}"})
    assert ev["details"]["limits"] == {"daily_calls": 5.0} and audit.verify()[0]


def test_usage_and_budget_apis(admin, provider):
    uid, _ = make_user()
    c = client_for(uid)
    provider()
    models.chat(cfg(uid), "s", "h")
    me = c.get("/api/usage").get_json()
    assert me["today"]["calls"] == 1 and me["today"]["input_tokens"] == 100 and me["month"]["calls"] == 1
    assert c.get("/api/admin/budgets").status_code == 403
    assert c.post("/api/admin/budgets", json={"uid": uid, "limits": {"daily_calls": 99}, "reason": "mine"}, headers=H).status_code == 403
    a = admin["client"]
    assert a.post("/api/admin/budgets", json={"limits": {"daily_tokens": 1000}, "reason": "cap"}, headers=H).status_code == 200
    assert a.get("/api/admin/budgets").get_json()["budgets"][0]["limits"] == {"daily_tokens": 1000.0}
