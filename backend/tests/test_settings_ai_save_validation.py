"""POST /api/settings/ai must reject an invalid request before touching runtime settings or secrets."""
from __future__ import annotations

import copy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import secrets_store
from app.api import settings as settings_api
from app.config import Settings, settings
from app.services import ai_provider

_SAVED_SECRETS = {
    "ai_provider": "openai_compat",
    "ai_base_url": "https://api.example.com/v1",
    "ai_api_key": "sk-saved",
    "ai_model": "saved-model",
    "ai_user_agent": "saved-agent",
    "ai_max_output_tokens": 4096,
    "ai_context_window": 32000,
}

_SAVED_SETTINGS = {
    "ai_provider": "openai_compat",
    "ai_base_url": "https://api.example.com/v1",
    "ai_api_key": "sk-saved",
    "ai_model": "saved-model",
    "ai_codex_command": "codex",
    "ai_codex_reasoning_effort": "",
    "ai_user_agent": "saved-agent",
    "ai_max_output_tokens": 4096,
    "ai_context_window": 32000,
}


@pytest.fixture
def stored(monkeypatch) -> dict:
    for field in Settings.model_fields:
        monkeypatch.setattr(settings, field, getattr(settings, field))
    for field, value in _SAVED_SETTINGS.items():
        monkeypatch.setattr(settings, field, value)
    data = copy.deepcopy(_SAVED_SECRETS)

    def save(updates: dict) -> dict:
        data.update(updates)
        return data

    def clear(*keys: str) -> dict:
        for key in keys:
            data.pop(key, None)
        return data

    monkeypatch.setattr(secrets_store, "load", lambda: data)
    monkeypatch.setattr(secrets_store, "save", save)
    monkeypatch.setattr(secrets_store, "clear", clear)
    monkeypatch.setattr(ai_provider, "ai_configured", lambda provider=None: True)
    return data


@pytest.fixture
def client(stored) -> TestClient:
    app = FastAPI()
    app.include_router(settings_api.router)
    return TestClient(app)


def _settings_snapshot() -> dict:
    return {field: copy.deepcopy(getattr(settings, field)) for field in Settings.model_fields}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            {"provider": "codex_cli", "model": "gpt-5.5", "codex_command": r"C:\tools\codex.exe", "user_agent": "new"},
            id="custom-codex-command",
        ),
        pytest.param({"provider": "codex_cli", "model": "-c", "user_agent": "new"}, id="unsafe-codex-model"),
        pytest.param(
            {"provider": "codex_cli", "model": "gpt-5.5", "codex_reasoning_effort": "high", "max_output_tokens": 0},
            id="codex-zero-output-limit",
        ),
        pytest.param(
            {"provider": "openai", "model": "new-model", "api_key": "", "user_agent": "new", "max_output_tokens": 0},
            id="zero-output-limit",
        ),
        pytest.param(
            {"provider": "openai", "model": "new-model", "api_key": "sk-new", "max_output_tokens": -5},
            id="negative-output-limit",
        ),
        pytest.param(
            {"provider": "openai", "model": "new-model", "api_key": "", "user_agent": "new", "context_window": 0},
            id="zero-context-window",
        ),
        pytest.param(
            {"provider": "openai", "model": "new-model", "max_output_tokens": 2048, "context_window": -1},
            id="valid-output-limit-negative-context-window",
        ),
    ],
)
def test_rejected_save_leaves_settings_and_secrets_untouched(client, stored, body):
    settings_before = _settings_snapshot()
    secrets_before = copy.deepcopy(stored)

    resp = client.post("/api/settings/ai", json=body)

    assert resp.status_code == 400
    assert _settings_snapshot() == settings_before
    assert stored == secrets_before


def test_valid_openai_save_updates_settings_and_secrets(client, stored):
    resp = client.post(
        "/api/settings/ai",
        json={
            "provider": "openai",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-new",
            "model": "new-model",
            "reasoning_effort": " medium ",
            "user_agent": "new-agent",
            "max_output_tokens": 2048,
            "context_window": 16000,
        },
    )

    assert resp.status_code == 200
    assert stored == {
        "ai_provider": "openai",
        "ai_base_url": "https://api.example.com/v1",
        "ai_api_key": "sk-new",
        "ai_model": "new-model",
        "ai_reasoning_effort": "medium",
        "ai_user_agent": "new-agent",
        "ai_max_output_tokens": 2048,
        "ai_context_window": 16000,
    }
    assert settings.ai_provider == "openai"
    assert settings.ai_api_key == "sk-new"
    assert settings.ai_model == "new-model"
    assert settings.ai_user_agent == "new-agent"
    assert settings.ai_max_output_tokens == 2048
    assert settings.ai_context_window == 16000
    body = resp.json()
    assert body["ai_provider"] == "openai"
    assert body["ai_max_output_tokens"] == 2048
    assert body["ai_context_window"] == 16000


def test_valid_save_with_empty_key_clears_the_saved_key(client, stored):
    resp = client.post("/api/settings/ai", json={"provider": "openai_compat", "api_key": ""})

    assert resp.status_code == 200
    assert "ai_api_key" not in stored
    assert settings.ai_api_key == ""
    assert stored["ai_max_output_tokens"] == 4096
    assert settings.ai_max_output_tokens == 4096


def test_valid_codex_save_updates_settings_and_secrets(client, stored):
    resp = client.post(
        "/api/settings/ai",
        json={"provider": "codex_cli", "model": "gpt5.5", "codex_command": "CODEX", "codex_reasoning_effort": "HIGH"},
    )

    assert resp.status_code == 200
    assert stored["ai_provider"] == "codex_cli"
    assert stored["ai_codex_model"] == "gpt-5.5"
    assert stored["ai_codex_command"] == "codex"
    assert stored["ai_codex_reasoning_effort"] == "high"
    assert stored["ai_api_key"] == "sk-saved"
    assert stored["ai_model"] == "saved-model"
    assert settings.ai_provider == "codex_cli"
    assert settings.ai_codex_command == "codex"
    assert settings.ai_codex_reasoning_effort == "high"
    assert resp.json()["ai_codex_model"] == "gpt-5.5"
