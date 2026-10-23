"""External notification adapters for LINE Messaging API and Telegram Bot API.

Delivery failures are logged and returned as ``False`` so notification outages never
interrupt alert persistence or SSE delivery.
"""
from __future__ import annotations

import logging
import re
import threading
from urllib.parse import quote, quote_plus

logger = logging.getLogger(__name__)

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
TELEGRAM_API_ROOT = "https://api.telegram.org"
_MAX_TEXT_LEN = 4096

_REDACTED = "<redacted>"
# Every pattern starts at a literal or a single delimiter so a scan stays linear on hostile input.
_URL_SECRET_PATTERNS = (
    (re.compile(r"(api\.telegram\.org/(?:file/)?bot)[^/\s'\"]+"), rf"\g<1>{_REDACTED}"),
    (re.compile(r"\bbot\d+:[\w-]+"), f"bot{_REDACTED}"),
    (re.compile(r"://[^/\s@'\"?#]*@"), f"://{_REDACTED}@"),
)
_QUERY_PARAM = re.compile(r"([?&])([\w.-]+)=([^&#\s'\"]+)")
_SECRET_PARAM_WORDS = ("token", "key", "secret", "passw", "signature", "auth")
# Shorter values would mask unrelated log text more often than they protect anything.
_MIN_REGISTERED_SECRET_LEN = 6
_registered_secrets: tuple[str, ...] = ()
_registered_secrets_lock = threading.Lock()


def register_log_secret(value: str | None) -> None:
    """Mask ``value`` and its URL-encoded forms in every log record from now on."""
    global _registered_secrets
    text = str(value or "").strip()
    if len(text) < _MIN_REGISTERED_SECRET_LEN:
        return
    variants = {text, quote(text, safe=""), quote_plus(text, safe="")}
    with _registered_secrets_lock:
        merged = set(_registered_secrets) | variants
        _registered_secrets = tuple(sorted(merged, key=len, reverse=True))


def _mask_secret_param(match: re.Match[str]) -> str:
    name = match.group(2).lower()
    if any(word in name for word in _SECRET_PARAM_WORDS):
        return f"{match.group(1)}{match.group(2)}={_REDACTED}"
    return match.group(0)


def redact_url_secrets(text: str) -> str:
    for secret in _registered_secrets:
        if secret in text:
            text = text.replace(secret, _REDACTED)
    for pattern, replacement in _URL_SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return _QUERY_PARAM.sub(_mask_secret_param, text)


class UrlSecretRedactingFilter(logging.Filter):
    """Mask bot tokens and secret query values in log messages and tracebacks.

    httpx logs every request URL at INFO, and httpx exception strings embed the URL.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = redact_url_secrets(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_url_secrets(record.exc_text)
        return True


def install_url_secret_redaction(target: logging.Logger | None = None) -> None:
    """Attach the filter to every handler of ``target`` (root by default).

    Handler filters also see records propagated from child loggers such as httpx;
    handlers added later are not covered.
    """
    for handler in (target or logging.getLogger()).handlers:
        if not any(isinstance(f, UrlSecretRedactingFilter) for f in handler.filters):
            handler.addFilter(UrlSecretRedactingFilter())


def _message(title: str, body: str) -> str:
    text = f"{title}\n{body}".strip()
    return text if len(text) <= _MAX_TEXT_LEN else text[:_MAX_TEXT_LEN - 1] + "…"


def send_line(channel_access_token: str, target_id: str, title: str, body: str) -> bool:
    token = str(channel_access_token or "").strip()
    target = str(target_id or "").strip()
    text = _message(title, body)
    if not token or not target or not text:
        return False

    try:
        import httpx

        response = httpx.post(
            LINE_PUSH_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"to": target, "messages": [{"type": "text", "text": text}]},
            timeout=5.0,
        )
        if response.status_code == 200:
            return True
        logger.warning("LINE Messaging API push failed: HTTP %s", response.status_code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LINE Messaging API push failed (%s)", type(exc).__name__)
    return False


def send_telegram(bot_token: str, chat_id: str, title: str, body: str) -> bool:
    token = str(bot_token or "").strip()
    chat = str(chat_id or "").strip()
    text = _message(title, body)
    if not token or not chat or not text:
        return False

    try:
        import httpx

        response = httpx.post(
            f"{TELEGRAM_API_ROOT}/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text},
            timeout=5.0,
        )
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if not isinstance(payload, dict) or payload.get("ok", True) is True:
                return True
        logger.warning("Telegram Bot API push failed: HTTP %s", response.status_code)
    except Exception as exc:  # noqa: BLE001
        # Telegram embeds the token in the request URL, so never log the exception text.
        logger.warning("Telegram Bot API push failed (%s)", type(exc).__name__)
    return False
