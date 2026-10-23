from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app import secrets_store
from app.api import settings as settings_api
from app.api import strategy as strategy_api
from app.config import Settings, settings
from app.services import ai_provider

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AI_ENV_VARS = ("AI_PROVIDER", "AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "AI_CODEX_COMMAND")
_MESSAGES = [{"role": "user", "content": "Reply exactly: OK"}]


def _built_in_defaults(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in _AI_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return Settings(_env_file=None)


@pytest.fixture
def ai_state(monkeypatch: pytest.MonkeyPatch, deny_network) -> dict:
    """Run against built-in defaults with an in-memory secrets store and a recording HTTP layer."""
    defaults = _built_in_defaults(monkeypatch)
    for field in (
        "ai_provider",
        "ai_base_url",
        "ai_api_key",
        "ai_model",
        "ai_codex_command",
        "ai_codex_reasoning_effort",
        "ai_user_agent",
        "ai_max_output_tokens",
        "ai_context_window",
    ):
        monkeypatch.setattr(settings, field, getattr(defaults, field))

    stored: dict = {}

    def save(updates: dict) -> dict:
        stored.update({k: v for k, v in updates.items() if v is not None})
        return stored

    def clear(*keys: str) -> dict:
        for key in keys:
            stored.pop(key, None)
        return stored

    monkeypatch.setattr(secrets_store, "load", lambda: stored)
    monkeypatch.setattr(secrets_store, "save", save)
    monkeypatch.setattr(secrets_store, "clear", clear)

    state: dict = {"stored": stored, "requests": [], "response": None}

    async def recording_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        state["requests"].append(request)
        if state["response"] is None:
            raise httpx.ConnectError("outbound AI request blocked in tests", request=request)
        return httpx.Response(200, json=state["response"], request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", recording_send)
    return state


def _chat_completion(text: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }


def test_built_in_defaults_leave_ai_endpoint_and_model_empty(monkeypatch):
    defaults = _built_in_defaults(monkeypatch)

    assert defaults.ai_base_url == ""
    assert defaults.ai_model == ""

    sources = [*(_REPO_ROOT / "backend" / "app").rglob("*.py")]
    sources += [
        p for p in (_REPO_ROOT / "frontend" / "src").rglob("*")
        if p.suffix in {".ts", ".tsx"}
    ]
    sources.append(_REPO_ROOT / ".env.example")
    offenders = [str(p) for p in sources if "zhaji" in p.read_text(encoding="utf-8", errors="replace")]
    assert offenders == []


@pytest.mark.asyncio
async def test_missing_base_url_reports_unconfigured_without_any_request(ai_state):
    ai_state["stored"].update({"ai_api_key": "sk-test", "ai_model": "deepseek-chat"})

    with pytest.raises(RuntimeError) as generate_error:
        await ai_provider.generate_ai_text(_MESSAGES)
    with pytest.raises(RuntimeError) as stream_error:
        async for _ in ai_provider.stream_ai_text(_MESSAGES):
            pass

    assert [str(r.url) for r in ai_state["requests"]] == []
    assert "未設定 AI 服務" in str(generate_error.value)
    assert "未設定 AI 服務" in str(stream_error.value)
    assert ai_provider.ai_configured() is False
    assert strategy_api.ai_status(None)["configured"] is False


@pytest.mark.asyncio
async def test_missing_model_is_rejected_before_any_request(ai_state):
    ai_state["stored"].update({
        "ai_api_key": "sk-test",
        "ai_base_url": "https://api.deepseek.com/v1",
    })

    with pytest.raises(RuntimeError) as error:
        await ai_provider.generate_ai_text(_MESSAGES)

    assert [str(r.url) for r in ai_state["requests"]] == []
    assert "模型" in str(error.value)
    assert ai_provider.ai_configured() is False


@pytest.mark.asyncio
async def test_missing_api_key_is_rejected_before_any_request(ai_state):
    ai_state["stored"].update({
        "ai_base_url": "https://api.deepseek.com/v1",
        "ai_model": "deepseek-chat",
    })

    with pytest.raises(RuntimeError) as generate_error:
        await ai_provider.generate_ai_text(_MESSAGES)
    with pytest.raises(RuntimeError) as stream_error:
        async for _ in ai_provider.stream_ai_text(_MESSAGES):
            pass

    assert [str(r.url) for r in ai_state["requests"]] == []
    assert "API Key" in str(generate_error.value)
    assert "API Key" in str(stream_error.value)
    assert ai_provider.ai_configured() is False
    assert strategy_api.ai_status(None)["configured"] is False


@pytest.mark.asyncio
async def test_configured_endpoint_is_used(ai_state):
    ai_state["stored"].update({
        "ai_api_key": "sk-test",
        "ai_base_url": "https://api.deepseek.com/v1",
        "ai_model": "deepseek-chat",
    })
    ai_state["response"] = _chat_completion("OK")

    assert ai_provider.ai_configured() is True
    assert strategy_api.ai_status(None)["configured"] is True

    text = await ai_provider.generate_ai_text(_MESSAGES)

    assert text == "OK"
    [request] = ai_state["requests"]
    assert str(request.url) == "https://api.deepseek.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_codex_provider_does_not_need_base_url(ai_state, monkeypatch):
    ai_state["stored"].update({"ai_provider": "codex_cli", "ai_codex_model": "gpt-5.6-sol"})
    captured: dict = {}

    def fake_run_process(args, prompt, env, timeout):
        captured["args"] = list(args)
        return 0, b"", b""

    monkeypatch.setattr(ai_provider, "codex_cli_available", lambda: True)
    monkeypatch.setattr(ai_provider, "_codex_base_command", lambda: ["codex"])
    monkeypatch.setattr(ai_provider, "_prepare_codex_home", lambda path: None)
    monkeypatch.setattr(ai_provider, "_codex_process_env", lambda path: {})
    monkeypatch.setattr(ai_provider, "_run_codex_process", fake_run_process)
    monkeypatch.setattr(ai_provider, "_read_output_file", lambda path: "OK")

    assert ai_provider.ai_configured() is True
    assert strategy_api.ai_status(None)["configured"] is True
    assert await ai_provider.generate_ai_text(_MESSAGES) == "OK"

    assert captured["args"][captured["args"].index("--model") + 1] == "gpt-5.6-sol"
    assert ai_state["requests"] == []


def test_clearing_ai_settings_falls_back_to_empty_after_restart(ai_state, monkeypatch):
    ai_state["stored"].update({
        "ai_provider": "openai_compat",
        "ai_api_key": "sk-test",
        "ai_base_url": "https://api.deepseek.com/v1",
        "ai_model": "deepseek-chat",
    })

    settings_api.clear_ai_settings()

    restarted = _built_in_defaults(monkeypatch)
    for field in ("ai_provider", "ai_base_url", "ai_api_key", "ai_model"):
        monkeypatch.setattr(settings, field, getattr(restarted, field))

    assert secrets_store.get_ai_config("ai_base_url", settings.ai_base_url) == ""
    assert ai_provider.current_ai_model() == ""
    assert ai_provider.ai_configured() is False
