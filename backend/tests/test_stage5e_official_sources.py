"""Official ETF and fundamentals sources share one HTTP client, cache a day's lists and retry transient failures;
the hybrid daily provider's per-month retry is in test_stage5e_review_followups."""
from __future__ import annotations

import time
from datetime import date

import httpx
import pytest

from app.taiwan import etf_data, fundamentals
from app.taiwan.etf_data import TaiwanOfficialETFData
from app.taiwan.fundamentals import TaiwanOfficialFundamentals
from app.taiwan.providers import hybrid_provider
from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider

ETF_ROW = {
    "出表日期": "1150830", "基金代號": "0050", "基金簡稱": "元大台灣50",
    "基金類型": "國內成分證券指數股票型基金", "標的指數/追蹤指數名稱": "臺灣50指數",
    "成立日期": "0920625", "上市日期": "0920630", "發行單位數/轉換數": "22360500000",
}
REVENUE_ROW = {
    "出表日期": "1150831", "資料年月": "11507", "公司代號": "2330",
    "營業收入-當月營收": "1,000", "營業收入-上月營收": "0",
    "營業收入-去年當月營收": "N/A", "營業收入-上月比較增減(%)": "10",
    "營業收入-去年同月增減(%)": "--", "累計營業收入-當月累計營收": "2,000",
}

_REAL_CLIENT = httpx.Client


class _Server:
    """MockTransport answering each GET from a queue of responses or exceptions; the last one repeats."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.requests: list[str] = []
        self.clients = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def client_factory(self, *args, **kwargs) -> httpx.Client:
        self.clients += 1
        return _REAL_CLIENT(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)


def _install(monkeypatch, server: _Server) -> None:
    monkeypatch.setattr(httpx, "Client", server.client_factory)
    monkeypatch.setattr(etf_data, "_shared_client", None, raising=False)
    monkeypatch.setattr(fundamentals, "_shared_client", None, raising=False)
    monkeypatch.setattr(hybrid_provider, "_official_fundamentals_provider", None, raising=False)


def test_etf_data_instances_share_one_client(monkeypatch):
    server = _Server(httpx.Response(200, json=[ETF_ROW]))
    _install(monkeypatch, server)
    # The provider's own daily adapters build clients of their own; only ETF requests are counted.
    hybrid = TaiwanHybridProvider()
    server.clients = 0

    TaiwanOfficialETFData().profile("0050.TWSE", "TWSE")
    TaiwanOfficialETFData().snapshot("0050.TWSE", "TWSE")
    hybrid.get_etf_data("0050.TWSE", "profile", exchange="TWSE")

    assert server.clients == 1
    assert len(server.requests) == 3


def test_fundamentals_reuse_the_same_days_list(monkeypatch):
    server = _Server(httpx.Response(200, json=[REVENUE_ROW]))
    _install(monkeypatch, server)
    provider = TaiwanOfficialFundamentals()

    first = provider.monthly_revenue("2330.TWSE", "TWSE")
    second = provider.monthly_revenue("2330.TWSE", "TWSE")

    assert first.values == second.values
    assert len(server.requests) == 1


def test_fundamentals_cache_is_keyed_by_the_taipei_date(monkeypatch):
    server = _Server(httpx.Response(200, json=[REVENUE_ROW]))
    _install(monkeypatch, server)
    provider = TaiwanOfficialFundamentals()
    today = [date(2026, 10, 23)]
    monkeypatch.setattr(fundamentals, "taipei_today", lambda: today[0], raising=False)

    provider.monthly_revenue("2330.TWSE", "TWSE")
    today[0] = date(2026, 10, 24)
    provider.monthly_revenue("2330.TWSE", "TWSE")

    assert len(server.requests) == 2


def test_fundamentals_retry_a_transient_failure(monkeypatch, no_sleep):
    server = _Server(
        httpx.ConnectError("reset"), httpx.Response(503), httpx.Response(200, json=[REVENUE_ROW])
    )
    _install(monkeypatch, server)

    got = TaiwanOfficialFundamentals().monthly_revenue("2330.TWSE", "TWSE")

    assert got.values["revenue"] == 1_000_000
    assert len(server.requests) == 3


def test_fundamentals_do_not_retry_a_client_error(monkeypatch, no_sleep):
    server = _Server(httpx.Response(404))
    _install(monkeypatch, server)

    with pytest.raises(httpx.HTTPStatusError):
        TaiwanOfficialFundamentals().monthly_revenue("2330.TWSE", "TWSE")
    assert len(server.requests) == 1


def test_hybrid_fundamentals_reuse_one_provider_across_calls(monkeypatch):
    server = _Server(httpx.Response(200, json=[REVENUE_ROW]))
    _install(monkeypatch, server)

    providers = [TaiwanHybridProvider(), TaiwanHybridProvider()]
    server.clients = 0

    for provider in providers:
        provider.get_fundamentals("2330.TWSE", "monthly_revenue", exchange="TWSE")

    assert len(server.requests) == 1
    assert server.clients == 1
