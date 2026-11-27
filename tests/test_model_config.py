"""Service-wide agent model and per-user default budgets, set by the Super Admin (product decision D-05)."""

import pytest
from conftest import H, client_for, make_user

import app as A
from features.ai_jobs import models
from infra import budgets, gateway, model_config, rbac
from infra.gateway import Reply

KEY = "sk-ant-platform-test-key-0000000000000000"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for c in (A.M.model_configs, A.M.budgets, A.M.model_usage):
        c.delete_many({})
    monkeypatch.delenv("PLATFORM_ANTHROPIC_KEY", raising=False)
    monkeypatch.delenv("PLATFORM_OPENAI_KEY", raising=False)
    yield
    for c in (A.M.model_configs, A.M.budgets, A.M.model_usage):
        c.delete_many({})


@pytest.fixture
def admin(monkeypatch):
    uid, email = make_user()
    monkeypatch.setattr(rbac, "ADMIN_EMAILS", {email})
    rbac.reconcile()
    return {"uid": uid, "client": client_for(uid)}


@pytest.fixture
def anthropic(monkeypatch):
    sent = []

    def adapter(req):
        sent.append(req)
        return Reply("ok", model_id="claude-x-2026-11-01", input_tokens=100, output_tokens=50)
    monkeypatch.setitem(gateway.ADAPTERS, "anthropic", adapter)
    return sent


def test_only_a_super_admin_sets_it_only_with_a_server_key_and_it_is_audited(admin, monkeypatch):
    u, _ = make_user()
    with pytest.raises(A.Invalid) as e:
        model_config.set_model(u, "anthropic", "claude-sonnet-5-5", "I want it")
    assert e.value.status == 403
    with pytest.raises(A.Invalid) as e:
        model_config.set_model(admin["uid"], "anthropic", "claude-sonnet-5-5", "no key on the server yet")
    assert e.value.status == 409
    monkeypatch.setenv("PLATFORM_ANTHROPIC_KEY", KEY)
    for bad in (("gemini", "x"), ("anthropic", "bad name!"), ("anthropic", "")):
        with pytest.raises(A.Invalid):
            model_config.set_model(admin["uid"], *bad, reason="checking validation")
    c = model_config.set_model(admin["uid"], "anthropic", "claude-sonnet-5-5", "start with Sonnet")
    assert (c["provider"], c["model"], c["version"], c["key_configured"]) == ("anthropic", "claude-sonnet-5-5", 1, True)
    ev = A.M.audit_log.find_one({"action": "model_config.set"})
    assert ev["details"]["model"] == "claude-sonnet-5-5"
    st = admin["client"].get("/api/admin/model").get_json()
    assert KEY not in repr(st) and st["providers"][0]["key_configured"] is True
    assert KEY not in repr(A.M.model_configs.find_one({"_id": "global"}))
    assert client_for(u).get("/api/admin/model").status_code == 403


def test_agents_use_the_platform_model_and_the_rest_of_the_app_keeps_the_users_own(admin, monkeypatch, anthropic):
    monkeypatch.setenv("PLATFORM_ANTHROPIC_KEY", KEY)
    model_config.set_model(admin["uid"], "anthropic", "claude-sonnet-5-5", "start with Sonnet")
    uid, _ = make_user()
    ws = A.Workspace(uid)
    assert models.config(ws) is None                       # outside agents: the user's own setting (none here)
    with gateway.context(agent_id="job_analyst"):
        cfg = models.config(ws)
        assert cfg["billing"] == "platform" and cfg["model"] == "claude-sonnet-5-5"
        assert models.chat(cfg, "s", "h") == "ok"
    assert anthropic[0].key == KEY and anthropic[0].billing == "platform"
    u = A.M.model_usage.find_one({"uid": uid})
    assert (u["billing"], u["model_config_version"], u["model_id"], u["agent_id"]) == ("platform", "1", "claude-x-2026-11-01", "job_analyst")
    assert KEY not in repr(u)
    model_config.set_model(admin["uid"], None, None, "back to users' own models")
    with gateway.context(agent_id="job_analyst"):
        assert models.config(ws) is None                   # cleared: agents fall back to the user's own model


def test_per_user_default_budget_applies_to_each_user_separately(admin, monkeypatch, anthropic):
    monkeypatch.setenv("PLATFORM_ANTHROPIC_KEY", KEY)
    model_config.set_model(admin["uid"], "anthropic", "claude-sonnet-5-5", "start with Sonnet")
    budgets.set_budget(admin["uid"], per_user=True, limits={"daily_tokens": 300}, reason="per-user daily cap")
    a, _ = make_user()
    b, _ = make_user()
    with gateway.context(agent_id="writer"):
        for u in (a, b):
            cfg = models.config(A.Workspace(u))
            models.chat(cfg, "s", "h")
            models.chat(cfg, "s", "h")                      # 300 tokens: at this user's limit
        with pytest.raises(gateway.GatewayError, match="your account") as e:
            models.chat(models.config(A.Workspace(a)), "s", "h")
    assert e.value.error_class == "budget" and len(anthropic) == 4
    with pytest.raises(ValueError):
        budgets.set_budget(admin["uid"], per_user=True, uid=a, limits={}, reason="both")
    r = admin["client"].post("/api/admin/budgets", json={"per_user": True, "limits": {"monthly_cost": 5}, "reason": "monthly cap"}, headers=H)
    assert r.status_code == 200 and r.get_json()["budget"]["scope"] == "user_default"
