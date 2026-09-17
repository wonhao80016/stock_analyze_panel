"""交易所限流回應的處理。

TWSE 對請求過於頻繁的用戶端不回 429, 而是 428 或一個「沒有 Location 的 307」
(body 為「因為安全性考量」封鎖頁)。urllib 把後者報成 redirect loop, 於是回補
畫面出現一整排看不懂的 "HTTP Error 308: ... infinite loop"。
"""
from __future__ import annotations

import json
import urllib.error

import pytest

from app.taiwan import institutional_margin_refresh as refresh
from app.taiwan.enrichment_backfill import _count_throttled


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://www.twse.com.tw/rwd/zh/fund/T86", code=code,
        msg="blocked", hdrs=None, fp=None,
    )


@pytest.mark.parametrize("code", [307, 308, 428, 429])
def test_throttle_codes_are_retried_then_reported_clearly(monkeypatch, code):
    calls = {"n": 0}

    def _always_throttled(req, timeout=None):
        calls["n"] += 1
        raise _http_error(code)

    monkeypatch.setattr(refresh.urllib.request, "urlopen", _always_throttled)
    monkeypatch.setattr(refresh.time, "sleep", lambda _s: None)

    with pytest.raises(refresh.ExchangeThrottledError) as excinfo:
        refresh._fetch_json_with_retry("https://example.invalid", max_attempts=3)

    assert calls["n"] == 3, "throttling must be retried, not given up on immediately"
    assert "交易所暫時拒絕存取" in str(excinfo.value)
    assert str(code) in str(excinfo.value)


@pytest.mark.parametrize("code", [400, 403, 404])
def test_non_throttle_client_errors_still_fail_fast(monkeypatch, code):
    calls = {"n": 0}

    def _client_error(req, timeout=None):
        calls["n"] += 1
        raise _http_error(code)

    monkeypatch.setattr(refresh.urllib.request, "urlopen", _client_error)
    monkeypatch.setattr(refresh.time, "sleep", lambda _s: None)

    with pytest.raises(urllib.error.HTTPError):
        refresh._fetch_json_with_retry("https://example.invalid", max_attempts=3)

    assert calls["n"] == 1, "permanent 4xx must not be retried"


def test_throttle_error_is_not_raised_for_server_errors(monkeypatch):
    """5xx stays a plain retry-then-raise, not a throttling message."""
    monkeypatch.setattr(
        refresh.urllib.request, "urlopen",
        lambda req, timeout=None: (_ for _ in ()).throw(_http_error(503)),
    )
    monkeypatch.setattr(refresh.time, "sleep", lambda _s: None)

    with pytest.raises(urllib.error.HTTPError):
        refresh._fetch_json_with_retry("https://example.invalid", max_attempts=2)


def test_successful_fetch_needs_no_retry(monkeypatch):
    class _Resp:
        def read(self):
            return json.dumps({"stat": "ok"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(refresh.urllib.request, "urlopen", lambda req, timeout=None: _Resp())
    assert refresh._fetch_json_with_retry("https://example.invalid") == {"stat": "ok"}


def test_backfill_separates_throttled_days_from_real_failures():
    stats = {
        "failed_dates": [
            "2026-04-03: HTTP Error 307: Temporary Redirect",
            "2026-06-10: HTTP Error 308: redirect loop",
            "2026-05-01: 交易所暫時拒絕存取 (HTTP 428)",
            "2026-07-01: ValueError: official directory schema changed",
        ],
    }
    assert _count_throttled(stats) == 3


def test_backfill_throttle_count_handles_missing_key():
    assert _count_throttled({}) == 0
    assert _count_throttled({"failed_dates": []}) == 0


def test_inter_request_delay_is_configured():
    """A 120-day backfill fires ~480 requests; without spacing it trips the throttle."""
    assert refresh.INTER_REQUEST_DELAY_SEC > 0
