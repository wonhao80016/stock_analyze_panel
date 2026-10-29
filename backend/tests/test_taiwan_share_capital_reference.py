"""The stock page's share-capital section falls back to the after-close archive when the live
profile cannot be fetched, and a failed official list is held briefly instead of being fetched
again on every page open."""
from __future__ import annotations

# ruff: noqa: RUF001 -- fixtures mirror official field names exactly.
from datetime import date, datetime
from types import SimpleNamespace

import httpx
import polars as pl
import pytest

from app.taiwan import fundamentals
from app.taiwan.current_data import TaiwanCurrentDataService
from app.taiwan.fundamentals import FAILURE_CACHE_SECONDS, TaiwanOfficialFundamentals
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.share_capital_store import STORED_SCHEMA, TaiwanShareCapitalStore

LIVE_ROW = {
    "出表日期": "1151028", "公司代號": "2454",
    "普通股每股面額": "新台幣 10.0000元", "實收資本額": "15995000000",
    "已發行普通股數或TDR原股發行股數": "1599500000",
}
REVENUE_ROW = {
    "出表日期": "1150831", "資料年月": "11507", "公司代號": "2330",
    "營業收入-當月營收": "1,000", "營業收入-上月營收": "0",
    "營業收入-去年當月營收": "N/A", "營業收入-上月比較增減(%)": "10",
    "營業收入-去年同月增減(%)": "--", "累計營業收入-當月累計營收": "2,000",
}
BLOCK_PAGE = "<html><body>因為安全性考量，您所執行的頁面無法呈現</body></html>"


class _Server:
    """Answers each GET from a queue; the last answer repeats."""

    def __init__(self, *answers: httpx.Response) -> None:
        self.answers = list(answers)
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def _html() -> httpx.Response:
    return httpx.Response(200, text=BLOCK_PAGE, headers={"content-type": "text/html; charset=utf-8"})


def _archive(tmp_path, *rows: dict) -> TaiwanShareCapitalStore:
    store = TaiwanShareCapitalStore(tmp_path)
    pl.DataFrame(list(rows), schema=STORED_SCHEMA).write_parquet(store.path)
    return store


ARCHIVED_2454 = {
    "symbol": "2454.TWSE",
    "issued_shares": 1_600_000_000,
    "report_date": "2026-10-28",
    "retrieved_at": "2026-10-28T21:56:03+08:00",
    "private_shares": 0,
}


LIVE_ERROR = (
    "ValueError: official opendata/t187ap03_L: upstream returned non-JSON response "
    "(text/html; charset=utf-8)"
)


class _UnreadableStore:
    def load(self):
        raise AssertionError("the archive must not be read")


def test_a_live_profile_is_used_without_reading_the_archive():
    server = _Server(httpx.Response(200, json=[LIVE_ROW]))
    provider = TaiwanOfficialFundamentals(server.client(), _UnreadableStore())

    got = provider.share_capital("2454.TWSE", "TWSE")

    assert len(server.requests) == 1
    assert got.source == "twse:share_capital_record" and got.period_end == "2026-10-28"
    assert got.values["issued_shares"] == 1_599_500_000
    assert got.values["capital_twd"] == 15_995_000_000
    assert got.values["par_value_twd"] == 10.0
    assert "live_error" not in got.values


def test_a_failed_live_fetch_falls_back_to_the_archived_row(tmp_path):
    server = _Server(_html())
    provider = TaiwanOfficialFundamentals(server.client(), _archive(tmp_path, ARCHIVED_2454))

    got = provider.share_capital("2454.TWSE", "TWSE")

    assert len(server.requests) == 1
    assert got.values == {
        "total_shares": None,
        "issued_shares": 1_600_000_000,
        "float_shares": None,
        "capital_twd": None,
        "par_value_twd": None,
        "par_value_raw": None,
        "par_value_currency": None,
        "live_error": LIVE_ERROR,
    }
    assert got.period_end == "2026-10-28"
    assert got.retrieved_at == datetime(2026, 10, 28, 21, 56, 3, tzinfo=TAIPEI)
    assert got.source == "twse:share_capital_store" and got.provider == "TWSE"
    assert got.source_url == "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
    assert got.status == "data_insufficient"
    assert got.available_at is None and got.published_at is None


@pytest.mark.parametrize(
    "answer",
    [httpx.Response(404), httpx.Response(200, json={"not": "a list"})],
    ids=["http-error", "schema-changed"],
)
def test_other_live_failures_also_fall_back(tmp_path, answer):
    provider = TaiwanOfficialFundamentals(_Server(answer).client(), _archive(tmp_path, ARCHIVED_2454))

    got = provider.share_capital("2454.TWSE", "TWSE")

    assert got.source == "twse:share_capital_store"
    assert got.values["issued_shares"] == 1_600_000_000


def test_a_live_timeout_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = httpx.Client(transport=httpx.MockTransport(timeout))
    got = TaiwanOfficialFundamentals(client, _archive(tmp_path, ARCHIVED_2454)).share_capital(
        "2454.TWSE", "TWSE"
    )

    assert got.source == "twse:share_capital_store"
    assert got.values["live_error"] == "ReadTimeout: timed out"


def test_a_symbol_absent_from_a_fetched_list_does_not_fall_back(tmp_path):
    other = {**LIVE_ROW, "公司代號": "2330"}
    provider = TaiwanOfficialFundamentals(
        _Server(httpx.Response(200, json=[other])).client(), _archive(tmp_path, ARCHIVED_2454)
    )

    with pytest.raises(LookupError, match=r"row not found for 2454\.TWSE"):
        provider.share_capital("2454.TWSE", "TWSE")


def test_a_failed_live_fetch_without_an_archived_row_stays_an_error(tmp_path):
    other = {**ARCHIVED_2454, "symbol": "2330.TWSE"}
    provider = TaiwanOfficialFundamentals(_Server(_html()).client(), _archive(tmp_path, other))

    with pytest.raises(ValueError, match="non-JSON"):
        provider.share_capital("2454.TWSE", "TWSE")


def test_a_failed_live_fetch_without_an_archive_file_stays_an_error(tmp_path):
    store = TaiwanShareCapitalStore(tmp_path / "absent")
    provider = TaiwanOfficialFundamentals(_Server(_html()).client(), store)

    with pytest.raises(ValueError, match="non-JSON"):
        provider.share_capital("2454.TWSE", "TWSE")
    assert not store.path.exists()


def test_a_held_failure_answers_from_the_archive_without_a_refetch(tmp_path, clock):
    server = _Server(_html())
    provider = TaiwanOfficialFundamentals(server.client(), _archive(tmp_path, ARCHIVED_2454))

    first = provider.share_capital("2454.TWSE", "TWSE")
    clock[0] += FAILURE_CACHE_SECONDS - 1
    second = provider.share_capital("2454.TWSE", "TWSE")

    assert len(server.requests) == 1
    assert first.source == second.source == "twse:share_capital_store"
    assert second.values["live_error"] == LIVE_ERROR


def test_a_tpex_archive_row_names_the_tpex_source(tmp_path):
    row = {**ARCHIVED_2454, "symbol": "6488.TPEX", "issued_shares": 478_113_725}
    provider = TaiwanOfficialFundamentals(_Server(_html()).client(), _archive(tmp_path, row))

    got = provider.share_capital("6488.TPEX", "TPEX")

    assert got.provider == "TPEx" and got.source == "tpex:share_capital_store"


class _Master:
    def get_instrument(self, symbol: str):
        return SimpleNamespace(symbol=symbol, name="fixture", instrument_type="stock")


class _ShareCapitalOnlyProvider:
    def __init__(self, official: TaiwanOfficialFundamentals) -> None:
        self.official = official

    def get_fundamentals(self, symbol, dataset, *, exchange, security_type, **kwargs):
        if dataset != "share_capital_record":
            raise LookupError("not under test")
        return self.official.share_capital(symbol, exchange, security_type=security_type)


def test_the_fallback_section_keeps_the_current_reference_contract(tmp_path):
    official = TaiwanOfficialFundamentals(_Server(_html()).client(), _archive(tmp_path, ARCHIVED_2454))
    service = TaiwanCurrentDataService(_ShareCapitalOnlyProvider(official), _Master())

    section = service.get_current_data("2454.TWSE").sections["share_capital_record"]

    assert section.status == "data_insufficient"
    assert section.usage_scope == "current_reference"
    assert section.available_at is None and not section.historically_eligible
    assert section.source == "twse:share_capital_store"
    assert section.data["retrieved_at"] == "2026-10-28T21:56:03+08:00"
    assert section.data["values"]["issued_shares"] == 1_600_000_000
    assert section.data["values"]["live_error"] == LIVE_ERROR


def test_a_non_json_answer_reads_as_an_upstream_error(tmp_path):
    provider = TaiwanOfficialFundamentals(_Server(_html()).client(), TaiwanShareCapitalStore(tmp_path))

    with pytest.raises(ValueError, match=r"upstream returned non-JSON response \(text/html; charset=utf-8\)"):
        provider.share_capital("2454.TWSE", "TWSE")


def test_the_section_reason_names_the_non_json_answer(tmp_path):
    official = TaiwanOfficialFundamentals(_Server(_html()).client(), TaiwanShareCapitalStore(tmp_path))
    service = TaiwanCurrentDataService(_ShareCapitalOnlyProvider(official), _Master())

    section = service.get_current_data("2454.TWSE").sections["share_capital_record"]

    assert section.status == "error"
    assert section.reason == LIVE_ERROR


@pytest.fixture
def clock(monkeypatch):
    now = [1_000.0]
    monkeypatch.setattr(fundamentals, "monotonic", lambda: now[0])
    return now


def test_a_failed_list_is_not_fetched_again_until_the_failure_expires(clock):
    server = _Server(_html(), _html(), httpx.Response(200, json=[REVENUE_ROW]))
    provider = TaiwanOfficialFundamentals(server.client())

    for _ in range(3):
        with pytest.raises(ValueError, match="non-JSON"):
            provider.monthly_revenue("2330.TWSE", "TWSE")
    assert len(server.requests) == 1

    clock[0] += FAILURE_CACHE_SECONDS - 1
    with pytest.raises(ValueError, match="non-JSON"):
        provider.monthly_revenue("2330.TWSE", "TWSE")
    assert len(server.requests) == 1

    clock[0] += 1
    with pytest.raises(ValueError, match="non-JSON"):
        provider.monthly_revenue("2330.TWSE", "TWSE")
    assert len(server.requests) == 2

    clock[0] += FAILURE_CACHE_SECONDS
    got = provider.monthly_revenue("2330.TWSE", "TWSE")
    assert got.values["revenue"] == 1_000_000
    assert len(server.requests) == 3


def test_a_cached_http_failure_keeps_its_error_type(clock):
    server = _Server(httpx.Response(404))
    provider = TaiwanOfficialFundamentals(server.client())

    for _ in range(2):
        with pytest.raises(httpx.HTTPStatusError):
            provider.monthly_revenue("2330.TWSE", "TWSE")
    assert len(server.requests) == 1


def test_a_failure_is_not_carried_into_the_next_taipei_date(clock, monkeypatch):
    server = _Server(_html(), httpx.Response(200, json=[REVENUE_ROW]))
    provider = TaiwanOfficialFundamentals(server.client())
    today = [date(2026, 10, 28)]
    monkeypatch.setattr(fundamentals, "taipei_today", lambda: today[0])

    with pytest.raises(ValueError, match="non-JSON"):
        provider.monthly_revenue("2330.TWSE", "TWSE")
    today[0] = date(2026, 10, 29)
    got = provider.monthly_revenue("2330.TWSE", "TWSE")

    assert got.values["revenue"] == 1_000_000
    assert len(server.requests) == 2


def test_a_failure_on_one_list_does_not_hold_back_another(clock, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("t187ap03_L"):
            return _html()
        return httpx.Response(200, json=[REVENUE_ROW])

    requests: list[str] = []
    provider = TaiwanOfficialFundamentals(
        httpx.Client(transport=httpx.MockTransport(handler)), TaiwanShareCapitalStore(tmp_path)
    )

    with pytest.raises(ValueError, match="non-JSON"):
        provider.share_capital("2330.TWSE", "TWSE")
    got = provider.monthly_revenue("2330.TWSE", "TWSE")

    assert got.values["revenue"] == 1_000_000
    assert len(requests) == 2


def test_a_successful_list_is_still_reused_for_the_whole_date(clock):
    server = _Server(httpx.Response(200, json=[REVENUE_ROW]))
    provider = TaiwanOfficialFundamentals(server.client())

    provider.monthly_revenue("2330.TWSE", "TWSE")
    clock[0] += FAILURE_CACHE_SECONDS * 100
    provider.monthly_revenue("2330.TWSE", "TWSE")

    assert len(server.requests) == 1
