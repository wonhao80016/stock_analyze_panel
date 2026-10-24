"""POST /api/settings/ai/test probes a candidate AI configuration without persisting it."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import secrets_store
from app.api import settings as settings_api
from app.config import Settings, settings

_AI_FIELDS = (
    "ai_provider",
    "ai_base_url",
    "ai_api_key",
    "ai_model",
    "ai_codex_command",
    "ai_codex_reasoning_effort",
    "ai_user_agent",
    "ai_max_output_tokens",
    "ai_context_window",
)

_SAVED = {
    "ai_provider": "openai_compat",
    "ai_base_url": "https://saved.example/v1",
    "ai_api_key": "sk-saved-k1",
    "ai_model": "saved-model",
}


def _completion(text: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "candidate-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
    }


@pytest.fixture
def ai_env(monkeypatch: pytest.MonkeyPatch, deny_network) -> dict:
    for name in ("AI_PROVIDER", "AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "AI_CODEX_COMMAND"):
        monkeypatch.delenv(name, raising=False)
    defaults = Settings(_env_file=None)
    for field in _AI_FIELDS:
        monkeypatch.setattr(settings, field, getattr(defaults, field))

    stored: dict = dict(_SAVED)
    writes: list = []

    def save(updates: dict) -> dict:
        writes.append(dict(updates))
        stored.update(updates)
        return stored

    def clear(*keys: str) -> dict:
        writes.append({"clear": keys})
        for key in keys:
            stored.pop(key, None)
        return stored

    monkeypatch.setattr(secrets_store, "load", lambda: stored)
    monkeypatch.setattr(secrets_store, "save", save)
    monkeypatch.setattr(secrets_store, "clear", clear)

    state: dict = {"stored": stored, "writes": writes, "requests": [], "status": 200, "body": _completion("OK")}

    async def recording_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        state["requests"].append(request)
        return httpx.Response(state["status"], json=state["body"], request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", recording_send)

    app = FastAPI()
    app.include_router(settings_api.router)
    state["client"] = TestClient(app)
    state["runtime"] = {field: getattr(settings, field) for field in _AI_FIELDS}
    return state


def _assert_nothing_persisted(state: dict) -> None:
    assert state["writes"] == []
    assert state["stored"] == _SAVED
    assert {field: getattr(settings, field) for field in _AI_FIELDS} == state["runtime"]


def test_candidate_key_is_probed_without_being_saved(ai_env):
    resp = ai_env["client"].post("/api/settings/ai/test", json={
        "provider": "openai_compat",
        "base_url": "https://candidate.example/v1",
        "api_key": "sk-candidate-k2",
        "model": "candidate-model",
        "user_agent": "",
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["model"] == "candidate-model"
    [request] = ai_env["requests"]
    assert str(request.url) == "https://candidate.example/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-candidate-k2"
    assert json.loads(request.content)["model"] == "candidate-model"
    _assert_nothing_persisted(ai_env)


def test_blank_fields_fall_back_to_saved_values_like_save_does(ai_env):
    resp = ai_env["client"].post("/api/settings/ai/test", json={"provider": "openai_compat", "model": "draft-model"})

    assert resp.json()["ok"] is True
    [request] = ai_env["requests"]
    assert str(request.url) == "https://saved.example/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-saved-k1"
    assert json.loads(request.content)["model"] == "draft-model"
    _assert_nothing_persisted(ai_env)


def test_openai_candidate_sends_its_reasoning_effort(ai_env):
    ai_env["client"].post("/api/settings/ai/test", json={
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-openai",
        "model": "gpt-5.5",
        "reasoning_effort": "low",
    })

    [request] = ai_env["requests"]
    assert json.loads(request.content)["reasoning_effort"] == "low"
    _assert_nothing_persisted(ai_env)


def test_rejected_candidate_reports_failure_and_keeps_saved_key(ai_env):
    ai_env["status"] = 401
    ai_env["body"] = {"error": {"message": "invalid api key", "type": "invalid_request_error"}}

    resp = ai_env["client"].post("/api/settings/ai/test", json={"api_key": "sk-bad", "model": "candidate-model"})

    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is False
    assert "401" in body["error"]
    assert "sk-bad" not in body["error"]
    _assert_nothing_persisted(ai_env)


def test_cleared_key_is_reported_before_any_request(ai_env):
    resp = ai_env["client"].post("/api/settings/ai/test", json={"api_key": ""})

    body = resp.json()
    assert body["ok"] is False
    assert "API Key" in body["error"]
    assert ai_env["requests"] == []
    _assert_nothing_persisted(ai_env)


def test_non_positive_output_limit_is_rejected(ai_env):
    resp = ai_env["client"].post("/api/settings/ai/test", json={"max_output_tokens": 0})

    assert resp.status_code == 400
    assert ai_env["requests"] == []
    _assert_nothing_persisted(ai_env)


def test_codex_candidate_is_rejected_without_running_anything(ai_env):
    resp = ai_env["client"].post("/api/settings/ai/test", json={"provider": "codex_cli", "model": "gpt-5.6-sol"})

    assert resp.status_code == 400
    assert ai_env["requests"] == []
    _assert_nothing_persisted(ai_env)


def test_probe_refuses_to_send_the_saved_key_to_a_new_address(ai_env):
    resp = ai_env["client"].post("/api/settings/ai/test", json={
        "base_url": "https://other.example/v1",
        "model": "saved-model",
    })

    assert resp.status_code == 400
    assert "API Key" in resp.json()["detail"]
    assert ai_env["requests"] == []
    _assert_nothing_persisted(ai_env)


def test_probe_treats_an_equivalent_address_as_unchanged(ai_env):
    resp = ai_env["client"].post("/api/settings/ai/test", json={"base_url": "https://saved.example/v1/"})

    assert resp.json()["ok"] is True
    [request] = ai_env["requests"]
    assert request.headers["authorization"] == "Bearer sk-saved-k1"


def test_save_refuses_to_move_the_saved_key_to_a_new_address(ai_env):
    resp = ai_env["client"].post("/api/settings/ai", json={
        "provider": "openai_compat",
        "base_url": "https://other.example/v1",
        "model": "saved-model",
    })

    assert resp.status_code == 400
    assert "API Key" in resp.json()["detail"]
    _assert_nothing_persisted(ai_env)


def test_save_accepts_a_new_address_together_with_its_key(ai_env):
    resp = ai_env["client"].post("/api/settings/ai", json={
        "provider": "openai_compat",
        "base_url": "https://other.example/v1",
        "api_key": "sk-other",
        "model": "other-model",
    })

    assert resp.status_code == 200
    assert ai_env["stored"]["ai_base_url"] == "https://other.example/v1"
    assert ai_env["stored"]["ai_api_key"] == "sk-other"


def test_save_without_address_change_keeps_the_saved_key(ai_env):
    resp = ai_env["client"].post("/api/settings/ai", json={
        "provider": "openai_compat",
        "base_url": "https://saved.example/v1",
        "model": "new-model",
    })

    assert resp.status_code == 200
    assert ai_env["stored"]["ai_api_key"] == "sk-saved-k1"
    assert ai_env["stored"]["ai_model"] == "new-model"
