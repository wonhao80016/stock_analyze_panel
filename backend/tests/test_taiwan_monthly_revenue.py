from __future__ import annotations

import asyncio
import os
from datetime import datetime
from types import SimpleNamespace

import httpx
import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.jobs import daily_pipeline
from app.jobs.daily_pipeline import refresh_monthly_revenue, refresh_quarterly_financials
from app.main import app
from app.taiwan import monthly_revenue, quarterly_financials
from app.taiwan.fundamentals import TaiwanOfficialFundamentals
from app.taiwan.monthly_revenue import (
    TaiwanMonthlyRevenueService,
    parse_revenue_rows,
    refresh_latest_month,
)
from app.taiwan.monthly_revenue_store import TaiwanMonthlyRevenueStore
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.universe import service as universe_service

RETRIEVED = datetime(2026, 10, 29, 16, 31, tzinfo=TAIPEI)
LATER = datetime(2026, 10, 30, 16, 31, tzinfo=TAIPEI)


def _row(symbol="2330.TWSE", year=2026, month=9, retrieved=RETRIEVED, **values):
    base = {
        "symbol": symbol, "exchange": symbol.split(".")[1], "year": year, "month": month,
        "revenue": 1_000, "previous_month_revenue": 900, "previous_year_revenue": 800,
        "cumulative_revenue": 9_000, "previous_year_cumulative_revenue": 7_500,
        "mom_pct": 11.11, "yoy_pct": 25.0, "cumulative_yoy_pct": 20.0,
        "report_date": "2026-10-29", "source": "twse_openapi",
        "source_url": "https://example.invalid/t187ap05_L",
        "last_retrieved_at": retrieved.isoformat(),
    }
    return {**base, **values}


# --- store ---------------------------------------------------------------------------------


def test_upsert_adds_keeps_and_revises_rows(tmp_path, caplog):
    store = TaiwanMonthlyRevenueStore(tmp_path)

    added = store.upsert([_row()])
    assert (added.added, added.unchanged, added.revised) == (1, 0, [])
    first = store.rows_for("2330.TWSE")[0]
    assert first["first_retrieved_at"] == first["last_retrieved_at"] == RETRIEVED.isoformat()
    assert first["revised_at"] is None and first["revised_fields"] is None

    same = store.upsert([_row(retrieved=LATER, report_date="2026-10-30")])
    assert (same.added, same.unchanged, same.revised) == (0, 1, [])
    kept = store.rows_for("2330.TWSE")[0]
    assert kept["first_retrieved_at"] == RETRIEVED.isoformat()
    assert kept["last_retrieved_at"] == LATER.isoformat()
    assert kept["revised_at"] is None and kept["revenue"] == 1_000
    assert kept["report_date"] == "2026-10-29", "a new report date alone is not a revision"

    revised_at = datetime(2026, 11, 2, 16, 31, tzinfo=TAIPEI)
    revised = store.upsert([_row(retrieved=revised_at, revenue=1_100, yoy_pct=37.5, report_date="2026-11-02")])
    assert revised.revised == ["2330.TWSE"]
    row = store.rows_for("2330.TWSE")[0]
    assert row["revenue"] == 1_100 and row["yoy_pct"] == 37.5
    assert row["report_date"] == "2026-11-02"
    assert row["first_retrieved_at"] == RETRIEVED.isoformat()
    assert row["revised_at"] == revised_at.isoformat()
    assert row["revised_fields"] == "revenue,yoy_pct"
    assert "2330.TWSE 2026-09 revised (revenue: 1000 -> 1100, yoy_pct: 25.0 -> 37.5)" in caplog.text


def test_rows_are_partitioned_by_month_and_read_newest_first(tmp_path):
    store = TaiwanMonthlyRevenueStore(tmp_path)
    store.upsert([_row(month=9), _row(year=2025, month=12)])
    store.upsert([_row(month=1), _row("8069.TPEX", month=1)])

    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.parquet")) == [
        "year=2025/month=12/part.parquet",
        "year=2026/month=01/part.parquet",
        "year=2026/month=09/part.parquet",
    ]
    assert [(r["year"], r["month"]) for r in store.rows_for("2330.TWSE")] == [(2026, 9), (2026, 1), (2025, 12)]


def test_an_invalid_month_is_rejected_before_any_write(tmp_path):
    store = TaiwanMonthlyRevenueStore(tmp_path)
    with pytest.raises(ValueError, match="invalid revenue month"):
        store.upsert([_row(), _row("2317.TWSE", month=13)])
    assert list(tmp_path.rglob("*.parquet")) == []


def test_a_failed_replace_raises_and_keeps_the_previous_partition(tmp_path, monkeypatch):
    store = TaiwanMonthlyRevenueStore(tmp_path)
    store.upsert([_row()])
    path = store.partition_path(2026, 9)
    before = path.read_bytes()

    def locked(src, dst):
        raise PermissionError("target is open in another process")

    monkeypatch.setattr(universe_service, "_REPLACE_RETRY_SECONDS", 0)
    monkeypatch.setattr(universe_service.os, "replace", locked)
    with pytest.raises(PermissionError):
        store.upsert([_row(retrieved=LATER, revenue=9_999)])

    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir()] == ["part.parquet"]
    monkeypatch.setattr(universe_service.os, "replace", os.replace)
    assert store.rows_for("2330.TWSE")[0]["revenue"] == 1_000


def test_an_unreadable_partition_is_not_overwritten(tmp_path):
    store = TaiwanMonthlyRevenueStore(tmp_path)
    path = store.partition_path(2026, 9)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not parquet")

    with pytest.raises(Exception):  # noqa: B017 -- any read error must stop the write
        store.upsert([_row()])
    assert path.read_bytes() == b"not parquet"


# --- parsing -------------------------------------------------------------------------------


def _official(**fields):
    return {
        "出表日期": "1151029", "資料年月": "11509", "公司代號": "2330", "公司名稱": "台積電",
        "產業別": "半導體業", "營業收入-當月營收": "1,000", "營業收入-上月營收": "900",
        "營業收入-去年當月營收": "800", "營業收入-上月比較增減(%)": "11.11",
        "營業收入-去年同月增減(%)": "25.00", "累計營業收入-當月累計營收": "9,000",
        "累計營業收入-去年累計營收": "7,500", "累計營業收入-前期比較增減(%)": "20.00",
        "備註": "-", **fields,
    }


def _parse(rows, exchange="TWSE"):
    return parse_revenue_rows(exchange, rows, source_url="https://example.invalid/list", retrieved_at=RETRIEVED)


def test_official_rows_convert_thousands_and_keep_percent_units():
    (row,) = _parse([_official()])
    assert row["symbol"] == "2330.TWSE" and (row["year"], row["month"]) == (2026, 9)
    assert row["revenue"] == 1_000_000 and row["previous_month_revenue"] == 900_000
    assert row["previous_year_revenue"] == 800_000 and row["cumulative_revenue"] == 9_000_000
    assert row["previous_year_cumulative_revenue"] == 7_500_000
    assert (row["mom_pct"], row["yoy_pct"], row["cumulative_yoy_pct"]) == (11.11, 25.0, 20.0)
    assert row["report_date"] == "2026-10-29" and row["source"] == "twse_openapi"
    assert row["source_url"] == "https://example.invalid/list"
    assert row["last_retrieved_at"] == RETRIEVED.isoformat()


def test_blank_or_absent_fields_are_stored_as_null(tmp_path):
    official = _official(**{"營業收入-去年同月增減(%)": "--", "營業收入-上月比較增減(%)": ""})
    del official["累計營業收入-去年累計營收"]
    del official["累計營業收入-前期比較增減(%)"]
    del official["出表日期"]
    (row,) = _parse([official], exchange="TPEX")
    assert row["symbol"] == "2330.TPEX" and row["source"] == "tpex_openapi"
    assert row["yoy_pct"] is None and row["mom_pct"] is None
    assert row["previous_year_cumulative_revenue"] is None and row["cumulative_yoy_pct"] is None
    assert row["report_date"] is None
    assert row["revenue"] == 1_000_000

    store = TaiwanMonthlyRevenueStore(tmp_path)
    store.upsert([row])
    stored = store.rows_for("2330.TPEX")[0]
    assert stored["yoy_pct"] is None and stored["previous_year_cumulative_revenue"] is None
    assert stored["report_date"] is None


def test_rows_without_a_code_or_month_and_malformed_numbers_are_skipped():
    rows = _parse([
        _official(公司代號=""),
        _official(資料年月="11513"),
        _official(資料年月=""),
        _official(公司代號="2317", **{"營業收入-當月營收": "n/a"}),
        _official(公司代號="2454"),
    ])
    assert [row["symbol"] for row in rows] == ["2454.TWSE"]


# --- refresh -------------------------------------------------------------------------------


class _Lists:
    def __init__(self, lists, failing=()):
        self.lists, self.failing, self.calls = lists, set(failing), []

    def market_rows(self, exchange, path):
        self.calls.append((exchange, path))
        if (exchange, path) in self.failing:
            raise ValueError("upstream returned non-JSON response (text/html)")
        return f"https://example.invalid/{path}", self.lists.get((exchange, path), []), RETRIEVED


def test_refresh_skips_a_failed_list_and_symbols_listed_twice(tmp_path):
    lists = {
        ("TWSE", "opendata/t187ap05_L"): [
            _official(), _official(公司代號="1101"), _official(公司代號="1101", **{"營業收入-當月營收": "5"}),
        ],
    }
    fake = _Lists(lists, failing={("TPEX", "mopsfin_t187ap05_O")})
    store = TaiwanMonthlyRevenueStore(tmp_path)

    report = refresh_latest_month(fake, store)

    assert report.added == 1 and report.ambiguous == ["1101.TWSE"]
    assert report.failures == ["TPEX: ValueError: upstream returned non-JSON response (text/html)"]
    assert store.rows_for("2330.TWSE")[0]["revenue"] == 1_000_000
    assert store.rows_for("1101.TWSE") == []
    assert fake.calls == [("TWSE", "opendata/t187ap05_L"), ("TPEX", "mopsfin_t187ap05_O")]


def test_refresh_reuses_the_official_per_day_list_cache(tmp_path):
    requests: list[str] = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path.endswith("t187ap05_L"):
            return httpx.Response(200, json=[_official()])
        return httpx.Response(200, json=[_official(公司代號="8069")])

    fundamentals = TaiwanOfficialFundamentals(httpx.Client(transport=httpx.MockTransport(handler)))
    store = TaiwanMonthlyRevenueStore(tmp_path)

    first = refresh_latest_month(fundamentals, store)
    assert first.added == 2
    assert len(requests) == 2 and len(set(requests)) == 2
    second = refresh_latest_month(fundamentals, store)
    assert len(requests) == 2, "the same Taipei date sends no second request"
    assert (second.added, second.unchanged) == (0, 2)
    fundamentals.monthly_revenue("2330.TWSE", "TWSE")
    fundamentals.monthly_revenue("8069.TPEX", "TPEX")
    assert len(requests) == 2, "the stock page reads the same cached lists"


def test_a_failed_refresh_is_only_logged(monkeypatch, caplog):
    def broken():
        raise OSError("disk full")

    monkeypatch.setattr(monthly_revenue, "refresh_latest_month", broken)
    refresh_monthly_revenue("daily+institutional")
    assert "monthly revenue archive (daily+institutional) failed" in caplog.text


class _UpdateService:
    def __init__(self, order, error=None):
        self.order, self.error = order, error

    def __call__(self):
        return self

    def run_update(self, **_kwargs):
        self.order.append("update")
        if self.error is not None:
            raise self.error
        status = SimpleNamespace(status="success", note=None)
        return SimpleNamespace(overall_status="success", daily=status, institutional=status, margin=status)


def _run_scheduled_update(expect_error=None):
    async def _build():
        return daily_pipeline.start_scheduler()

    loop = asyncio.new_event_loop()
    try:
        scheduler = loop.run_until_complete(_build())
        try:
            job = scheduler.get_job("taiwan_daily_update")
            if expect_error is None:
                job.func(**job.kwargs)
            else:
                with pytest.raises(type(expect_error), match=str(expect_error)):
                    job.func(**job.kwargs)
        finally:
            scheduler.shutdown(wait=False)
    finally:
        loop.close()


@pytest.mark.parametrize("update_error", [None, RuntimeError("boom")])
def test_the_scheduled_run_archives_revenue_even_when_both_archives_fail(monkeypatch, update_error):
    from app.taiwan import daily_update

    order: list[str] = []

    def failing(name):
        def archive():
            order.append(name)
            raise OSError("disk full")
        return archive

    monkeypatch.setattr(daily_pipeline, "refresh_quarterly_financials", refresh_quarterly_financials)
    monkeypatch.setattr(daily_pipeline, "refresh_monthly_revenue", refresh_monthly_revenue)
    monkeypatch.setattr(quarterly_financials, "refresh_latest_quarter", failing("quarterly"))
    monkeypatch.setattr(monthly_revenue, "refresh_latest_month", failing("monthly"))
    monkeypatch.setattr(daily_pipeline, "refresh_benchmark_index", lambda _l: order.append("benchmark"))
    monkeypatch.setattr(daily_update, "TaiwanDailyUpdateService", _UpdateService(order, update_error))
    monkeypatch.setattr(daily_pipeline, "refresh_enriched_cache", lambda _l: order.append("enriched"))
    monkeypatch.setattr(daily_pipeline, "refresh_strategy_cache", lambda _l: order.append("strategy"))

    _run_scheduled_update()

    expected = ["benchmark", "update", "quarterly", "monthly"] if update_error else [
        "benchmark", "update", "enriched", "strategy", "quarterly", "monthly",
    ]
    assert order == expected


def test_the_revenue_archive_still_runs_when_the_after_close_work_raises(monkeypatch):
    from app.taiwan import daily_update

    order: list[str] = []

    def broken_enriched(_label):
        order.append("enriched")
        raise RuntimeError("repo exploded")

    monkeypatch.setattr(daily_pipeline, "refresh_quarterly_financials", lambda _l: order.append("quarterly"))
    monkeypatch.setattr(daily_pipeline, "refresh_monthly_revenue", lambda _l: order.append("monthly"))
    monkeypatch.setattr(daily_pipeline, "refresh_benchmark_index", lambda _l: order.append("benchmark"))
    monkeypatch.setattr(daily_update, "TaiwanDailyUpdateService", _UpdateService(order))
    monkeypatch.setattr(daily_pipeline, "refresh_enriched_cache", broken_enriched)

    _run_scheduled_update(expect_error=RuntimeError("repo exploded"))

    assert order == ["benchmark", "update", "enriched", "quarterly", "monthly"]


# --- service and API -----------------------------------------------------------------------


class _Master:
    def __init__(self, instrument_type="stock", security_class=None):
        self.instrument_type, self.security_class = instrument_type, security_class

    def get_instrument(self, symbol):
        if self.instrument_type is None:
            return None
        return SimpleNamespace(
            symbol=symbol, code=symbol.split(".")[0], name="fixture", industry=None,
            instrument_type=self.instrument_type, security_class=self.security_class,
        )


def test_service_reports_display_only_months_newest_first(tmp_path):
    store = TaiwanMonthlyRevenueStore(tmp_path)
    store.upsert([_row(year=2025, month=12, revenue=700), _row(month=1, previous_month_revenue=650,
                                                                previous_year_revenue=600)])
    result = TaiwanMonthlyRevenueService(store, _Master()).get("2330.TWSE")

    assert result.applicable and result.usage_scope == "historical_reference"
    assert result.available_at is None and result.display_only is True
    assert result.accumulating and result.target_months == 12
    assert [m.period for m in result.months] == ["2026-01", "2025-12"]
    assert result.months[1].revenue == 700
    assert result.months[0].source == "twse_openapi"
    assert result.months[0].first_retrieved_at == RETRIEVED.isoformat()
    comparison = result.latest_comparison
    assert comparison is not None and comparison.period == "2026-01"
    assert (comparison.previous_month, comparison.previous_month_revenue) == ("2025-12", 650)
    assert (comparison.previous_year_month, comparison.previous_year_revenue) == ("2025-01", 600)


def test_twelve_stored_months_stop_accumulating(tmp_path):
    store = TaiwanMonthlyRevenueStore(tmp_path)
    store.upsert([_row(year=2025 + (m > 12), month=(m - 1) % 12 + 1) for m in range(11, 23)])
    result = TaiwanMonthlyRevenueService(store, _Master()).get("2330.TWSE")
    assert len(result.months) == 12 and result.accumulating is False


@pytest.mark.parametrize(("master", "reason"), [(_Master("etf"), "etf"), (_Master(security_class="tdr"), "tdr")])
def test_etf_and_tdr_are_not_applicable(tmp_path, master, reason):
    store = TaiwanMonthlyRevenueStore(tmp_path)
    store.upsert([_row("0050.TWSE")])
    result = TaiwanMonthlyRevenueService(store, master).get("0050.TWSE")
    assert not result.applicable and result.not_applicable_reason == reason
    assert result.months == [] and result.latest_comparison is None


@pytest.fixture
def client():
    return TestClient(app, client=("127.0.0.1", 50000))


def _serve(monkeypatch, service):
    monkeypatch.setattr("app.api.taiwan.get_taiwan_monthly_revenue_service", lambda: service)


URL = "/api/taiwan/stocks/{}/monthly-revenue"


def test_api_returns_stored_months(tmp_path, monkeypatch, client):
    store = TaiwanMonthlyRevenueStore(tmp_path)
    store.upsert([_row(month=8, revenue=400), _row()])
    _serve(monkeypatch, TaiwanMonthlyRevenueService(store, _Master()))

    response = client.get(URL.format("2330.TWSE"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["usage_scope"] == "historical_reference" and payload["available_at"] is None
    assert payload["display_only"] is True and payload["accumulating"] is True
    assert [m["period"] for m in payload["months"]] == ["2026-09", "2026-08"]
    assert payload["months"][1]["revenue"] == 400 and payload["months"][0]["yoy_pct"] == 25.0
    assert payload["latest_comparison"] == {
        "period": "2026-09", "previous_month": "2026-08", "previous_month_revenue": 900,
        "previous_year_month": "2025-09", "previous_year_revenue": 800,
    }


def test_api_returns_an_empty_accumulating_answer(tmp_path, monkeypatch, client):
    _serve(monkeypatch, TaiwanMonthlyRevenueService(TaiwanMonthlyRevenueStore(tmp_path), _Master()))
    payload = client.get(URL.format("2330.TWSE")).json()
    assert payload["applicable"] is True and payload["months"] == []
    assert payload["accumulating"] is True and payload["latest_comparison"] is None


@pytest.mark.parametrize(("master", "reason"), [(_Master("etf"), "etf"), (_Master(security_class="tdr"), "tdr")])
def test_api_marks_etf_and_tdr_not_applicable(tmp_path, monkeypatch, client, master, reason):
    _serve(monkeypatch, TaiwanMonthlyRevenueService(TaiwanMonthlyRevenueStore(tmp_path), master))
    payload = client.get(URL.format("0050.TWSE")).json()
    assert payload["applicable"] is False and payload["not_applicable_reason"] == reason


def test_api_errors(tmp_path, monkeypatch, client):
    store = TaiwanMonthlyRevenueStore(tmp_path)
    path = store.partition_path(2026, 9)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not parquet")
    _serve(monkeypatch, TaiwanMonthlyRevenueService(store, _Master()))
    failed = client.get(URL.format("2330.TWSE"))
    assert failed.status_code == 500
    detail = failed.json()["detail"]
    assert detail == "月營收讀取失敗"
    assert "/" not in detail and "\\" not in detail and "part.parquet" not in detail
    assert str(tmp_path) not in failed.text and "parquet" not in failed.text.lower()

    _serve(monkeypatch, TaiwanMonthlyRevenueService(store, _Master(None)))
    assert client.get(URL.format("2330.TWSE")).status_code == 404
    assert client.get(URL.format("not-a-symbol")).status_code == 400


# --- display-only boundary -----------------------------------------------------------------


def test_the_archive_never_writes_the_backtest_financial_metrics_path(tmp_path, monkeypatch):
    from app.taiwan import data_root

    monkeypatch.setattr(data_root, "taiwan_data_root", lambda: tmp_path / "taiwan")
    TaiwanMonthlyRevenueStore().upsert([_row()])
    written = [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.parquet")]
    assert written == ["taiwan/monthly_revenue/year=2026/month=09/part.parquet"]
    assert not pl.read_parquet(tmp_path / written[0]).is_empty()
