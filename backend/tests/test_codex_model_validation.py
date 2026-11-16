"""Codex model names reach the CLI as arguments, possibly through a .cmd shim parsed by cmd.exe."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import secrets_store
from app.api import settings as settings_api
from app.config import Settings, settings
from app.services import ai_provider

UNSAFE_MODELS = [
    "-c",
    "--config=model_provider=evil",
    "gpt&calc",
    "gpt|calc",
    "gpt^x",
    "gpt%PATH%",
    'gpt"x',
    "gpt 5",
    "gpt\n5",
    "gpt;x",
    ".gpt",
    "/gpt",
    "gpt@x",
]

SAFE_MODELS = ["gpt-5.5", "o3", "openai/gpt-oss:20b", "gpt_5.6-sol"]

_AI_FIELDS = (
    "ai_provider",
    "ai_base_url",
    "ai_api_key",
    "ai_model",
    "ai_codex_command",
    "ai_codex_reasoning_effort",
    "ai_user_agent",
)


@pytest.fixture
def stored(monkeypatch) -> dict:
    defaults = Settings(_env_file=None)
    for field in _AI_FIELDS:
        monkeypatch.setattr(settings, field, getattr(defaults, field))
    data: dict = {}

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


@pytest.fixture
def codex_runs(monkeypatch) -> list[list[str]]:
    runs: list[list[str]] = []

    def fake_process(args, prompt, env, timeout):
        runs.append(list(args))
        return 0, b"done", b""

    def refuse_subprocess(*_args, **_kwargs):
        raise AssertionError("subprocess.run must not be reached")

    monkeypatch.setattr(ai_provider, "_codex_base_command", lambda: ["codex"])
    monkeypatch.setattr(ai_provider, "_prepare_codex_home", lambda target: None)
    monkeypatch.setattr(ai_provider, "_run_codex_process", fake_process)
    monkeypatch.setattr(ai_provider.subprocess, "run", refuse_subprocess)
    return runs


@pytest.mark.parametrize("model", UNSAFE_MODELS)
def test_unsafe_codex_model_is_rejected(model):
    with pytest.raises(ValueError):
        ai_provider.validate_codex_model(model)


@pytest.mark.parametrize("model", [*SAFE_MODELS, ""])
def test_safe_codex_model_is_returned_unchanged(model):
    assert ai_provider.validate_codex_model(model) == model


@pytest.mark.parametrize("model", ["-c", " --profile x", "gpt&calc", "gpt 5", 'gpt"x'])
def test_saving_unsafe_codex_model_returns_400_and_saves_nothing(client, stored, model):
    resp = client.post("/api/settings/ai", json={"provider": "codex_cli", "model": model})

    assert resp.status_code == 400
    assert "Codex 模型名稱" in resp.json()["detail"]
    assert stored == {}
    assert settings.ai_provider == Settings(_env_file=None).ai_provider


@pytest.mark.parametrize("model", SAFE_MODELS)
def test_saving_safe_codex_model_is_stored(client, stored, model):
    resp = client.post("/api/settings/ai", json={"provider": "codex_cli", "model": model})

    assert resp.status_code == 200
    assert stored["ai_codex_model"] == model


def test_openai_compatible_model_may_contain_at_sign(client, stored):
    resp = client.post("/api/settings/ai", json={
        "provider": "openai_compat",
        "base_url": "http://127.0.0.1:1234/v1",
        "api_key": "sk-local",
        "model": "qwen/qwen3-8b@q4_k_m",
    })

    assert resp.status_code == 200
    assert stored["ai_model"] == "qwen/qwen3-8b@q4_k_m"
    assert ai_provider.current_ai_model() == "qwen/qwen3-8b@q4_k_m"


@pytest.mark.parametrize("model", ["-c", "gpt&calc", 'gpt" & calc & "'])
async def test_stored_unsafe_codex_model_never_starts_the_cli(stored, codex_runs, model):
    stored.update({"ai_provider": "codex_cli", "ai_codex_model": model})

    with pytest.raises(RuntimeError, match="Codex 模型名稱"):
        await ai_provider._run_codex_cli([{"role": "user", "content": "hi"}], max_tokens=None, timeout=5)

    assert codex_runs == []


@pytest.mark.parametrize("model", SAFE_MODELS)
async def test_stored_safe_codex_model_is_passed_to_the_cli(stored, codex_runs, model):
    stored.update({"ai_provider": "codex_cli", "ai_codex_model": model})

    result = await ai_provider._run_codex_cli([{"role": "user", "content": "hi"}], max_tokens=None, timeout=5)

    assert result == "done"
    [args] = codex_runs
    assert args[args.index("--model") + 1] == model
