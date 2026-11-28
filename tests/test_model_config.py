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


def test_the_platform_model_is_the_only_model_and_clearing_it_turns_ai_off(admin, monkeypatch, anthropic):
    uid, _ = make_user()
    ws = A.Workspace(uid)
    assert models.config(ws) is None                       # nothing set yet: no model anywhere
    monkeypatch.setenv("PLATFORM_ANTHROPIC_KEY", KEY)
    model_config.set_model(admin["uid"], "anthropic", "claude-sonnet-5-5", "start with Sonnet")
    assert models.config(ws)["billing"] == "platform"      # outside agents too
    with gateway.context(agent_id="job_analyst"):
        cfg = models.config(ws)
        assert cfg["billing"] == "platform" and cfg["model"] == "claude-sonnet-5-5"
        assert models.chat(cfg, "s", "h") == "ok"
    assert anthropic[0].key == KEY and anthropic[0].billing == "platform"
    u = A.M.model_usage.find_one({"uid": uid})
    assert (u["billing"], u["model_config_version"], u["model_id"], u["agent_id"]) == ("platform", "1", "claude-x-2026-11-01", "job_analyst")
    assert KEY not in repr(u)
    model_config.set_model(admin["uid"], None, None, "turn the model off")
    assert models.config(ws) is None                       # cleared: no model anywhere


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


def _fake_claude(tmp_path, monkeypatch, out='{"fit": 80}'):
    """A stand-in `claude` command on PATH, so tests never use a real Claude plan."""
    import os
    path = tmp_path / "claude"
    path.write_text(f"#!/bin/sh\necho '{out}'\n")
    path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}/usr/bin:/bin")
    monkeypatch.delenv("APP_ENV", raising=False)


def test_claude_code_on_this_computer_can_be_the_platform_model(admin, tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch)
    st = admin["client"].get("/api/admin/model").get_json()
    local = next(p for p in st["providers"] if p["id"] == "claude_cli")
    assert local["key_configured"] and local["models"] == ["sonnet", "opus", "haiku"]
    model_config.set_model(admin["uid"], "claude_cli", "sonnet", "use my own Claude plan")
    uid, _ = make_user()
    cfg = models.config(A.Workspace(uid))
    assert cfg["provider"] == "claude_cli" and cfg["billing"] == "platform" and cfg["key"] == ""
    with gateway.context(agent_id="job_analyst"):
        assert models.chat(cfg, "s", "h", as_json=True) == {"fit": 80}
    u = A.M.model_usage.find_one({"uid": uid})
    assert u["provider"] == "claude_cli" and u["status"] == "ok" and u["agent_id"] == "job_analyst"


def test_claude_code_is_never_offered_in_production_or_when_missing(admin, tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    assert all(p["id"] != "claude_cli" for p in admin["client"].get("/api/admin/model").get_json()["providers"])
    with pytest.raises(Exception, match="Claude Code isn't installed"):
        model_config.set_model(admin["uid"], "claude_cli", "sonnet", "use my own Claude plan")
    monkeypatch.delenv("APP_ENV")
    model_config.set_model(admin["uid"], "claude_cli", "sonnet", "use my own Claude plan")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")                       # claude uninstalled later: no model, no crash
    assert models.config(A.Workspace(admin["uid"])) is None
