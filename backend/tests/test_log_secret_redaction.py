"""Secrets carried in outbound request URLs must never reach the log files."""
from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import httpx
import pytest

from app.config import settings
from app.services import webhook_adapter

TELEGRAM_TOKEN = "123456789:AAH-secretTokenValue_0123456789abcdef"
QUERY_TOKEN = "query-secret-0123456789"


@pytest.fixture
def telegram_returns_500(monkeypatch, deny_network):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, json={"ok": False})

    def fake_post(url, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return client.post(url, **kwargs)

    monkeypatch.setattr(httpx, "post", fake_post)
    return requests


def _backend_log_handler() -> RotatingFileHandler:
    import app.main  # noqa: F401  (attaches the backend.log handler on import)

    handlers = [
        h for h in logging.getLogger().handlers
        if isinstance(h, RotatingFileHandler) and Path(h.baseFilename).name == "backend.log"
    ]
    assert len(handlers) == 1
    assert Path(handlers[0].baseFilename).parent == settings.data_dir
    return handlers[0]


def _read_new_lines(path: Path, offset: int) -> str:
    with path.open("r", encoding="utf-8") as fh:
        fh.seek(offset)
        return fh.read()


def _log_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def test_backend_log_masks_telegram_token(caplog, telegram_returns_500):
    caplog.set_level(logging.INFO)
    handler = _backend_log_handler()
    log_path = Path(handler.baseFilename)
    offset = _log_size(log_path)

    assert webhook_adapter.send_telegram(TELEGRAM_TOKEN, "42", "title", "body") is False
    handler.flush()

    assert len(telegram_returns_500) == 1
    assert TELEGRAM_TOKEN in str(telegram_returns_500[0].url)
    written = _read_new_lines(log_path, offset)
    assert "Telegram Bot API push failed: HTTP 500" in written
    assert "HTTP Request: POST https://api.telegram.org/bot" in written
    assert TELEGRAM_TOKEN not in written
    assert TELEGRAM_TOKEN.split(":", 1)[1] not in written


def test_desktop_log_masks_telegram_token(caplog, telegram_returns_500):
    from app import desktop

    caplog.set_level(logging.INFO)
    root = logging.getLogger()
    before = set(root.handlers)
    desktop._setup_logging()
    added = [h for h in root.handlers if h not in before]
    try:
        assert len(added) == 1
        log_path = Path(added[0].baseFilename)
        assert log_path.parent == settings.data_dir
        added[0].flush()
        offset = _log_size(log_path)

        assert webhook_adapter.send_telegram(TELEGRAM_TOKEN, "42", "title", "body") is False
        added[0].flush()

        written = _read_new_lines(log_path, offset)
        assert "Telegram Bot API push failed: HTTP 500" in written
        assert "HTTP Request: POST https://api.telegram.org/bot" in written
        assert TELEGRAM_TOKEN not in written
    finally:
        for h in added:
            root.removeHandler(h)
            h.close()


def _status_error(url: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(401, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError("raise_for_status did not raise")


@pytest.mark.parametrize(
    ("url", "secret", "kept"),
    [
        (f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", TELEGRAM_TOKEN, "sendMessage"),
        (f"https://example.test/api?symbols=2330&token={QUERY_TOKEN}", QUERY_TOKEN, "symbols=2330"),
        (f"https://example.test/api?apiKey={QUERY_TOKEN}&date=2026-10-22", QUERY_TOKEN, "date=2026-10-22"),
    ],
    ids=["telegram-path", "token-query", "apikey-query"],
)
def test_exception_text_and_traceback_mask_url_secrets(caplog, url, secret, kept):
    caplog.set_level(logging.INFO)
    handler = _backend_log_handler()
    log_path = Path(handler.baseFilename)
    offset = _log_size(log_path)
    test_logger = logging.getLogger("app.tests.log_secret_redaction")

    exc = _status_error(url)
    assert secret in str(exc)
    test_logger.warning("custom source fetch failed: %s", exc)
    try:
        raise exc
    except httpx.HTTPStatusError:
        test_logger.exception("custom source fetch crashed")
    handler.flush()

    written = _read_new_lines(log_path, offset)
    assert "custom source fetch failed: Client error '401 Unauthorized'" in written
    assert "Traceback (most recent call last)" in written
    assert kept in written
    assert secret not in written


def test_custom_source_query_token_with_arbitrary_param_name_is_masked(caplog, monkeypatch, deny_network):
    from app.data_providers.custom.config import AuthConfig, CustomSourceConfig, DatasetConfig
    from app.data_providers.custom.provider import GenericHTTPProvider

    secret = "S3CR3T"
    monkeypatch.setenv("B035_CUSTOM_SID", secret)
    caplog.set_level(logging.INFO)
    handler = _backend_log_handler()
    log_path = Path(handler.baseFilename)
    offset = _log_size(log_path)

    provider = GenericHTTPProvider(CustomSourceConfig(
        name="sid_source",
        display_name="Sid Source",
        auth=AuthConfig(type="query", token_env="B035_CUSTOM_SID", param="sid"),
        datasets={"realtime": DatasetConfig(url="https://example.test/realtime", field_map={})},
    ))
    requests: list[httpx.Request] = []

    def reject(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(401, request=request)

    provider._client.close()
    provider._client = httpx.Client(transport=httpx.MockTransport(reject))
    try:
        with pytest.raises(httpx.HTTPStatusError) as raised:
            provider.get_realtime()
    finally:
        provider.close()
    logging.getLogger("app.services.quote_service").warning("自定义实时行情拉取失败: %s", raised.value)
    handler.flush()

    assert requests[0].url.params["sid"] == secret
    written = _read_new_lines(log_path, offset)
    assert "HTTP Request: GET https://example.test/realtime?sid=" in written
    assert "自定义实时行情拉取失败: Client error '401 Unauthorized'" in written
    assert secret not in written


def test_url_userinfo_password_is_masked(caplog, deny_network):
    password = "p4ss-w0rd-B035"
    url = f"https://alice:{password}@example.test/ext/data?date=2026-10-22"
    caplog.set_level(logging.INFO)
    handler = _backend_log_handler()
    log_path = Path(handler.baseFilename)
    offset = _log_size(log_path)

    def reject(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    with httpx.Client(transport=httpx.MockTransport(reject)) as client:
        response = client.get(url)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logging.getLogger("app.services.ext_pull").warning("ext pull failed: %s", exc)
    handler.flush()

    written = _read_new_lines(log_path, offset)
    assert "HTTP Request: GET https://" in written
    assert "example.test/ext/data?date=2026-10-22" in written
    assert "ext pull failed: Client error '403 Forbidden'" in written
    assert password not in written


@pytest.mark.parametrize(
    "payload",
    [
        "?" + "key" * 17_000,
        "&" + "token" * 10_000,
        ("?" + "authkey" * 50) * 140,
        "://" + "a" * 50_000,
        "bot" + "1" * 50_000,
        "api.telegram.org/bot" + "a" * 50_000,
    ],
    ids=["query-key-run", "query-token-run", "many-query-starts", "userinfo-run", "bot-digits", "telegram-path"],
)
def test_redaction_is_linear_on_hostile_input(payload):
    started = time.perf_counter()
    webhook_adapter.redact_url_secrets(payload)
    assert time.perf_counter() - started < 0.05
