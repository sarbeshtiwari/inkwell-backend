"""Choosing the AI model: Ollama by default, or a cloud provider with the user's own key."""

import pytest
from conftest import H

from features.ai_jobs import models


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.ok = data, status, status < 400

    def json(self):
        return self.data


def test_cloud_provider_needs_a_key_and_never_returns_it(user):
    c, ws = user["client"], user["ws"]
    assert models.config(ws) is None  # no local model in tests, nothing set up
    r = c.put("/api/ai-jobs/model", json={"provider": "groq", "model": "llama-3.3-70b-versatile"}, headers=H).get_json()
    assert not r["ready"] and r["keys"]["groq"] is False
    r = c.put("/api/ai-jobs/model", json={"provider": "groq", "model": "llama-3.3-70b-versatile", "key": "gsk_" + "a" * 30}, headers=H)
    body = r.get_data(as_text=True)
    assert r.get_json()["ready"] and "gsk_" not in body
    assert models.config(ws)["base"] == "https://api.groq.com/openai/v1"
    assert c.put("/api/ai-jobs/model", json={"provider": "nope"}, headers=H).status_code == 400
    assert c.put("/api/ai-jobs/model", json={"provider": "openai", "model": "bad model!"}, headers=H).status_code == 400


def test_local_server_must_be_on_this_computer(user):
    c = user["client"]
    assert c.put("/api/ai-jobs/model", json={"provider": "local", "model": "qwen", "base_url": "http://10.0.0.5:1234/v1"}, headers=H).status_code == 400
    assert c.put("/api/ai-jobs/model", json={"provider": "local", "model": "qwen", "base_url": "http://localhost:1234/v1"}, headers=H).get_json()["ready"]


def test_chat_shapes(monkeypatch):
    sent = []

    def post(url, **kw):
        sent.append((url, kw))
        if "anthropic" in url:
            return Resp({"content": [{"type": "text", "text": 'Sure: {"ok": true}'}]})
        return Resp({"choices": [{"message": {"content": "<think>x</think>```json\n{\"ok\": true}\n```"}}]})
    monkeypatch.setattr(models.requests, "post", post)
    a = models.chat({"provider": "anthropic", "model": "claude-sonnet-5-5", "key": "k", "base": "https://api.anthropic.com/v1"}, "s", "h", as_json=True)
    o = models.chat({"provider": "openai", "model": "m", "key": "k", "base": "https://api.openai.com/v1"}, "s", "h", as_json=True)
    assert a == o == {"ok": True}
    assert sent[0][0].endswith("/messages") and sent[0][1]["headers"]["x-api-key"] == "k" and sent[0][1]["json"]["system"] == "s"
    assert sent[1][0].endswith("/chat/completions") and sent[1][1]["headers"]["Authorization"] == "Bearer k"
    monkeypatch.setattr(models.requests, "post", lambda url, **kw: Resp({}, 401))
    with pytest.raises(RuntimeError, match="key"):
        models.chat({"provider": "groq", "model": "m", "key": "k", "base": "https://x"}, "s", "h")
