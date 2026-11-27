"""Model gateway (task PH1-01): every model call goes through infra/gateway.py, which records usage.

All tests use a scripted fake provider or a faked HTTP layer; no real provider is ever called."""

import threading
import time

import pytest
import requests

import app as A
from conftest import make_user
from features.ai_jobs import evaluate, models
from infra import gateway
from infra.gateway import FakeProvider, GatewayError, Reply


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.ok = data, status, status < 400

    def json(self):
        return self.data


@pytest.fixture
def fake(monkeypatch):
    """Install a fake provider under the id "fake"; returns a function that scripts its answers."""
    def install(*answers, model_id="fake-model-1"):
        f = FakeProvider(*answers, model_id=model_id)
        monkeypatch.setitem(gateway.ADAPTERS, "fake", f)
        return f
    return install


def cfg_for(uid, provider="fake", **kw):
    return {"provider": provider, "model": "requested-model", "key": "sk-secret-key-value-1234567890",
            "base": "https://provider.example/v1", "uid": uid, "label": "x", **kw}


def records(uid):
    return list(A.M.model_usage.find({"uid": uid}))


def test_chat_keeps_its_signature_and_records_one_usage_row(fake):
    uid, _ = make_user()
    f = fake('{"ok": true}')
    out = models.chat(cfg_for(uid), "system text", "human text", as_json=True, max_tokens=60, temperature=0, timeout=5)
    assert out == {"ok": True}
    req = f.calls[0]
    assert (req.system, req.human, req.max_tokens, req.temperature, req.timeout, req.as_json) == ("system text", "human text", 60, 0, 5, True)
    [r] = records(uid)
    assert r["status"] == "ok" and r["error_class"] is None
    assert r["provider"] == "fake" and r["requested_model"] == "requested-model" and r["model_id"] == "fake-model-1"
    assert r["input_tokens"] > 0 and r["output_tokens"] >= 0 and r["latency_ms"] >= 0
    assert r["billing"] == "byok" and r["day"] == time.strftime("%Y-%m-%d", time.gmtime(r["at"]))
    assert r["cost"] is None and r["price_version"] is None  # no price on record: never guessed


def test_usage_record_never_holds_prompts_answers_keys_or_urls(fake):
    uid, _ = make_user()
    fake("THE-ANSWER-TEXT")
    models.chat(cfg_for(uid), "SECRET-SYSTEM-PROMPT", "PRIVATE-RESUME-TEXT")
    [r] = records(uid)
    flat = repr(r)
    for s in ("SECRET-SYSTEM-PROMPT", "PRIVATE-RESUME-TEXT", "THE-ANSWER-TEXT", "sk-secret-key-value", "provider.example"):
        assert s not in flat


def test_returned_model_id_and_tokens_come_from_the_provider_response(monkeypatch):
    uid, _ = make_user()
    sent = []

    def post(url, **kw):
        sent.append(kw)
        if "anthropic" in url:
            return Resp({"id": "msg_1", "model": "claude-sonnet-5-5-20261101", "content": [{"type": "text", "text": "hi"}],
                         "usage": {"input_tokens": 11, "output_tokens": 3}})
        return Resp({"id": "chatcmpl-9", "model": "gpt-x-2026-10-01", "choices": [{"message": {"content": "yo"}}],
                     "usage": {"prompt_tokens": 20, "completion_tokens": 5}})
    monkeypatch.setattr(requests, "post", post)
    assert models.chat(cfg_for(uid, "anthropic", base="https://api.anthropic.com/v1"), "s", "h") == "hi"
    assert models.chat(cfg_for(uid, "openai", base="https://api.openai.com/v1"), "s", "h") == "yo"
    assert all(kw["timeout"] == gateway.HTTP_TIMEOUT for kw in sent)
    a, o = sorted(records(uid), key=lambda r: r["at"])
    assert (a["model_id"], a["input_tokens"], a["output_tokens"], a["request_id"]) == ("claude-sonnet-5-5-20261101", 11, 3, "msg_1")
    assert (o["model_id"], o["input_tokens"], o["output_tokens"], o["request_id"]) == ("gpt-x-2026-10-01", 20, 5, "chatcmpl-9")


def test_cost_uses_the_price_in_force_at_call_time(fake):
    uid, _ = make_user()
    now = time.time()
    A.M.model_prices.insert_many([
        {"provider": "fake", "model": "fake-model-1", "effective_from": now - 1000, "input_per_mtok": 1.0, "output_per_mtok": 2.0, "currency": "USD", "version": "p1"},
        {"provider": "fake", "model": "fake-model-1", "effective_from": now - 10, "input_per_mtok": 3.0, "output_per_mtok": 6.0, "currency": "USD", "version": "p2"},
        {"provider": "fake", "model": "fake-model-1", "effective_from": now + 10_000, "input_per_mtok": 99.0, "output_per_mtok": 99.0, "currency": "USD", "version": "future"},
    ])
    try:
        fake(Reply("ok", model_id="fake-model-1", input_tokens=1_000_000, output_tokens=500_000))
        models.chat(cfg_for(uid), "s", "h")
        [r] = records(uid)
        assert r["price_version"] == "p2" and r["currency"] == "USD" and r["cost"] == pytest.approx(3.0 + 3.0)
    finally:
        A.M.model_prices.delete_many({"provider": "fake"})


@pytest.mark.parametrize("status,cls,msg", [(401, "auth", "key"), (403, "auth", "key"), (404, "not_found", "model name"),
                                            (429, "rate_limit", "rate limit"), (500, "server", "HTTP 500"), (418, "http_error", "HTTP 418")])
def test_provider_errors_keep_their_messages_and_are_classified(monkeypatch, status, cls, msg):
    uid, _ = make_user()
    monkeypatch.setattr(requests, "post", lambda url, **kw: Resp({}, status))
    with pytest.raises(RuntimeError, match=msg) as e:
        models.chat(cfg_for(uid, "groq", base="https://x"), "s", "h")
    assert isinstance(e.value, GatewayError) and e.value.error_class == cls
    [r] = records(uid)
    assert r["status"] == "error" and r["error_class"] == cls and r["model_id"] is None and r["input_tokens"] is None


def test_timeouts_network_failures_and_bad_json_are_recorded(monkeypatch, fake):
    uid, _ = make_user()

    def timeout(url, **kw):
        raise requests.Timeout()
    monkeypatch.setattr(requests, "post", timeout)
    with pytest.raises(GatewayError) as e:
        models.chat(cfg_for(uid, "openai", base="https://x"), "s", "h")
    assert e.value.error_class == "timeout"

    def down(url, **kw):
        raise requests.ConnectionError("no route")
    monkeypatch.setattr(requests, "post", down)
    with pytest.raises(GatewayError) as e:
        models.chat(cfg_for(uid, "openai", base="https://x"), "s", "h")
    assert e.value.error_class == "network"

    fake("no json here")
    with pytest.raises(GatewayError, match="JSON") as e:
        models.chat(cfg_for(uid), "s", "h", as_json=True)
    assert e.value.error_class == "invalid_output"
    assert sorted(r["error_class"] for r in records(uid)) == ["invalid_output", "network", "timeout"]


def test_cli_and_local_providers_are_billed_local_and_cli_retries_json_once(monkeypatch):
    uid, _ = make_user()
    answers = ["not json", '{"a": 1}']
    calls = []

    def run(pid, prompt, web=False, timeout=90, model=""):
        calls.append((pid, web, timeout))
        return answers.pop(0)
    monkeypatch.setattr(models.cli_llm, "run", run)
    out = models.chat({"provider": "claude_cli", "model": "", "local": True, "uid": uid, "label": "x"}, "s", "h", as_json=True, web=True, timeout=7)
    assert out == {"a": 1} and calls == [("claude_cli", True, 7), ("claude_cli", True, 7)]
    [r] = records(uid)
    assert r["billing"] == "local" and r["model_id"] is None and r["status"] == "ok"

    def quota(*a, **k):
        raise models.cli_llm.QuotaError("Your Claude Code / Kiro plan has reached its usage limit. Try again later.")
    monkeypatch.setattr(models.cli_llm, "run", quota)
    with pytest.raises(RuntimeError, match="usage limit") as e:
        models.chat({"provider": "kiro_cli", "model": "", "local": True, "uid": uid, "label": "x"}, "s", "h")
    assert e.value.error_class == "quota"


def test_config_carries_the_users_id_so_usage_is_scoped(user, monkeypatch, fake):
    c, ws = user["client"], user["ws"]
    c.put("/api/ai-jobs/model", json={"provider": "groq", "model": "llama-x", "key": "gsk_" + "a" * 30}, headers={"X-Requested-With": "fetch"})
    cfg = models.config(ws)
    assert cfg["uid"] == ws.uid
    monkeypatch.setattr(requests, "post", lambda url, **kw: Resp({"model": "llama-x-real", "choices": [{"message": {"content": "ok"}}]}))
    models.chat(cfg, "s", "h")
    other, _ = make_user()
    assert [r["model_id"] for r in records(ws.uid)] == ["llama-x-real"]
    assert records(other) == [] and gateway.usage(other) == []
    assert gateway.totals(ws.uid)["calls"] == 1 and gateway.totals(other)["calls"] == 0


def test_existing_callers_go_through_the_gateway(fake):
    """Existing models.current() callers (cover letters) record usage without changing their code."""
    uid, _ = make_user()
    fake("Dear team, " + "x" * 400)
    profile = {"role": "dev", "years": 3, "resume": "python"}
    with models.use(cfg_for(uid)):
        letter = evaluate.cover_letter(profile, {"title": "Dev", "company": "Acme"}, "posting", "Sam")
    assert letter.startswith("Dear team") and len(records(uid)) == 1


def test_context_fields_are_recorded_and_unknown_fields_refused(fake):
    uid, _ = make_user()
    fake("a", "b")
    with gateway.context(trace_id="t1", task_id="task9", agent_id="job_analyst", agent_version="3", purpose="evaluate"):
        models.chat(cfg_for(uid), "s", "h")
    models.chat(cfg_for(uid), "s", "h")
    inside, outside = sorted(records(uid), key=lambda r: r["at"])
    assert (inside["trace_id"], inside["task_id"], inside["agent_id"], inside["agent_version"], inside["purpose"]) == ("t1", "task9", "job_analyst", "3", "evaluate")
    assert "trace_id" not in outside and outside["purpose"] == ""
    with pytest.raises(ValueError):
        with gateway.context(uid="someone-else"):
            pass


def test_context_is_per_thread(fake):
    uid, _ = make_user()
    fake("a", "b")
    seen = {}

    def other_thread():
        seen["ctx"] = gateway.current_context()
    with gateway.context(trace_id="mine"):
        t = threading.Thread(target=other_thread)
        t.start()
        t.join()
    assert seen["ctx"] == {}


def test_a_usage_store_failure_never_breaks_the_answer(fake, monkeypatch):
    uid, _ = make_user()
    fake("still answers")
    from pymongo.errors import AutoReconnect

    class Broken:
        def insert_one(self, doc):
            raise AutoReconnect("down")

        def find_one(self, *a, **k):
            return None
    real = A.M.__class__.__getattr__

    def getattr_(self, name):
        return Broken() if name in ("model_usage", "model_prices") else real(self, name)
    monkeypatch.setattr(A.M.__class__, "__getattr__", getattr_)
    assert models.chat(cfg_for(uid), "s", "h") == "still answers"


def test_account_deletion_removes_usage_records(user, fake):
    ws = user["ws"]
    fake("x")
    models.chat(cfg_for(ws.uid), "s", "h")
    assert records(ws.uid)
    A.delete_user_data(ws.uid)
    assert records(ws.uid) == []
