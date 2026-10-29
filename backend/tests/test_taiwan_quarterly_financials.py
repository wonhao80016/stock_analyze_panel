from __future__ import annotations

# ruff: noqa: RUF001 -- fixtures mirror official field names exactly.
import ast
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.jobs import daily_pipeline
from app.jobs.daily_pipeline import refresh_quarterly_financials
from app.main import app
from app.taiwan import quarterly_financials
from app.taiwan.fundamentals import TaiwanOfficialFundamentals
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.quarterly_financials import (
    TaiwanQuarterlyFinancialsService,
    derive_periods,
    parse_statement_rows,
    refresh_latest_quarter,
)
from app.taiwan.quarterly_financials_store import TaiwanQuarterlyFinancialStore
from app.taiwan.universe import service as universe_service

RETRIEVED = datetime(2026, 10, 29, 16, 31, tzinfo=TAIPEI)
LATER = datetime(2026, 10, 30, 16, 31, tzinfo=TAIPEI)


def _row(symbol="2330.TWSE", year=2026, quarter=2, retrieved=RETRIEVED, **values):
    base = {
        "symbol": symbol, "exchange": symbol.split(".")[1], "accounting_category": "ci",
        "fiscal_year": year, "fiscal_quarter": quarter, "period_end": f"{year}-06-30",
        "revenue": 1_000, "gross_profit": 600, "operating_income": 400, "net_income": 300,
        "parent_net_income": 290, "cumulative_eps": 2.5, "assets": 5_000, "liabilities": 2_000,
        "equity": 3_000, "parent_equity": 2_900, "source": "twse_openapi",
        "source_url": "https://example.invalid/06 | https://example.invalid/07",
        "last_retrieved_at": retrieved.isoformat(),
    }
    return {**base, **values}


# --- store ---------------------------------------------------------------------------------


def test_upsert_adds_keeps_and_revises_rows(tmp_path):
    store = TaiwanQuarterlyFinancialStore(tmp_path)

    added = store.upsert([_row()])
    assert (added.added, added.unchanged, added.revised) == (1, 0, [])
    first = store.rows_for("2330.TWSE")[0]
    assert first["first_retrieved_at"] == first["last_retrieved_at"] == RETRIEVED.isoformat()
    assert first["revised_at"] is None

    same = store.upsert([_row(retrieved=LATER)])
    assert (same.added, same.unchanged, same.revised) == (0, 1, [])
    kept = store.rows_for("2330.TWSE")[0]
    assert kept["first_retrieved_at"] == RETRIEVED.isoformat()
    assert kept["last_retrieved_at"] == LATER.isoformat()
    assert kept["revised_at"] is None and kept["revenue"] == 1_000

    revised_at = datetime(2026, 11, 2, 16, 31, tzinfo=TAIPEI)
    revised = store.upsert([_row(retrieved=revised_at, revenue=1_100, cumulative_eps=2.6)])
    assert revised.revised == ["2330.TWSE"]
    row = store.rows_for("2330.TWSE")[0]
    assert row["revenue"] == 1_100 and row["cumulative_eps"] == 2.6
    assert row["first_retrieved_at"] == RETRIEVED.isoformat()
    assert row["revised_at"] == revised_at.isoformat()
    assert row["revised_fields"] == "revenue,cumulative_eps"
    assert store.partition_path(2026, 2).is_file()


def test_rows_are_partitioned_by_fiscal_quarter_and_read_oldest_first(tmp_path):
    store = TaiwanQuarterlyFinancialStore(tmp_path)
    store.upsert([_row(quarter=2), _row(year=2025, quarter=4, period_end="2025-12-31")])
    store.upsert([_row(quarter=1, period_end="2026-03-31"), _row("8069.TPEX", quarter=1)])

    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.parquet")) == [
        "year=2025/quarter=4/part.parquet",
        "year=2026/quarter=1/part.parquet",
        "year=2026/quarter=2/part.parquet",
    ]
    assert [(r["fiscal_year"], r["fiscal_quarter"]) for r in store.rows_for("2330.TWSE")] == [
        (2025, 4), (2026, 1), (2026, 2),
    ]


def test_a_failed_replace_raises_and_keeps_the_previous_partition(tmp_path, monkeypatch):
    store = TaiwanQuarterlyFinancialStore(tmp_path)
    store.upsert([_row()])
    path = store.partition_path(2026, 2)
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
    store = TaiwanQuarterlyFinancialStore(tmp_path)
    path = store.partition_path(2026, 2)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not parquet")

    with pytest.raises(Exception):  # noqa: B017 -- any read error must stop the write
        store.upsert([_row()])
    assert path.read_bytes() == b"not parquet"


# --- parsing -------------------------------------------------------------------------------


def _twse_ci_income(**fields):
    return {
        "出表日期": "1151029", "年度": "115", "季別": "2", "公司代號": "2330", "公司名稱": "台積電",
        "營業收入": "1,000", "營業毛利（毛損）": "620", "營業毛利（毛損）淨額": "600",
        "營業利益（損失）": "400", "本期淨利（淨損）": "300",
        "淨利（淨損）歸屬於母公司業主": "290", "基本每股盈餘（元）": "2.50", **fields,
    }


def _twse_ci_balance(**fields):
    return {
        "出表日期": "1151029", "年度": "115", "季別": "2", "公司代號": "2330",
        "資產總計": "5,000", "負債總計": "2,000", "權益總計": "3,000",
        "歸屬於母公司業主之權益合計": "2,900", **fields,
    }


def _parse(exchange, category, income, balance):
    return parse_statement_rows(
        exchange, category, income, balance,
        income_url="https://example.invalid/06", balance_url="https://example.invalid/07",
        retrieved_at=RETRIEVED,
    )


def test_general_industry_rows_carry_parent_figures_in_twd():
    (row,) = _parse("TWSE", "ci", [_twse_ci_income()], [_twse_ci_balance()])
    assert row["symbol"] == "2330.TWSE" and row["accounting_category"] == "ci"
    assert (row["fiscal_year"], row["fiscal_quarter"], row["period_end"]) == (2026, 2, "2026-06-30")
    assert row["revenue"] == 1_000_000 and row["gross_profit"] == 600_000
    assert row["parent_net_income"] == 290_000 and row["cumulative_eps"] == 2.5
    assert row["parent_equity"] == 2_900_000 and row["equity"] == 3_000_000
    assert row["source"] == "twse_openapi"
    assert row["last_retrieved_at"] == RETRIEVED.isoformat()


def test_tpex_rows_use_the_english_period_and_code_keys():
    income = {
        "Date": "1151028", "Year": "115", "Season": "1", "SecuritiesCompanyCode": "1240",
        "營業收入": "100", "本期淨利（淨損）": "10", "淨利（淨損）歸屬於母公司業主": "9",
        "基本每股盈餘（元）": "0.5",
    }
    balance = {"Date": "1151028", "年度": "115", "季別": "1", "SecuritiesCompanyCode": "1240",
               "資產總計": "1,000", "歸屬於母公司業主之權益合計": "500"}
    (row,) = _parse("TPEX", "ci", [income], [balance])
    assert row["symbol"] == "1240.TPEX" and row["period_end"] == "2026-03-31"
    assert row["source"] == "tpex_openapi" and row["parent_equity"] == 500_000


def test_financial_rows_have_no_gross_or_operating_figures():
    income = {
        "出表日期": "1151029", "年度": "115", "季別": "2", "公司代號": "2880",
        "淨收益": "800", "本期稅後淨利（淨損）": "200", "淨利（淨損）歸屬於母公司業主": "190",
        "基本每股盈餘（元）": "1.2",
    }
    balance = {"出表日期": "1151029", "年度": "115", "季別": "2", "公司代號": "2880",
               "資產總額": "9,000", "負債總額": "8,000", "權益總額": "1,000",
               "歸屬於母公司業主之權益": "990"}
    (row,) = _parse("TWSE", "fh", [income], [balance])
    assert row["gross_profit"] is None and row["operating_income"] is None
    assert row["revenue"] == 800_000 and row["net_income"] == 200_000
    assert row["parent_equity"] == 990_000 and row["equity"] == 1_000_000


def test_bank_rows_without_a_parent_line_keep_it_null():
    income = {"出表日期": "1151029", "年度": "115", "季別": "2", "公司代號": "2801",
              "本期淨利（淨損）": "50", "淨利（損）歸屬於母公司業主": "49", "基本每股盈餘（元）": "0.4"}
    (row,) = _parse("TWSE", "basi", [income], [])
    assert row["revenue"] is None and row["parent_net_income"] == 49_000
    assert row["assets"] is None and row["parent_equity"] is None


def test_placeholder_rows_and_balance_of_another_period_are_not_used():
    placeholder = {"Date": "1151028", "Year": "", "Season": "", "SecuritiesCompanyCode": ""}
    stale_balance = _twse_ci_balance(季別="1")
    rows = _parse("TWSE", "ci", [placeholder, _twse_ci_income()], [stale_balance])
    assert len(rows) == 1
    assert rows[0]["assets"] is None and rows[0]["parent_equity"] is None


# --- refresh -------------------------------------------------------------------------------


class _Lists:
    def __init__(self, lists, failing=()):
        self.lists, self.failing, self.calls = lists, set(failing), []

    def market_rows(self, exchange, path):
        self.calls.append((exchange, path))
        if (exchange, path) in self.failing:
            raise ValueError("upstream returned non-JSON response (text/html)")
        return f"https://example.invalid/{path}", self.lists.get((exchange, path), []), RETRIEVED


def test_refresh_skips_failed_lists_and_ambiguous_symbols(tmp_path):
    lists = {
        ("TWSE", "opendata/t187ap06_L_ci"): [_twse_ci_income(), _twse_ci_income(公司代號="1101")],
        ("TWSE", "opendata/t187ap07_L_ci"): [_twse_ci_balance(), _twse_ci_balance(公司代號="1101")],
        ("TWSE", "opendata/t187ap06_L_ins"): [_twse_ci_income(公司代號="1101")],
        ("TWSE", "opendata/t187ap07_L_ins"): [],
    }
    fake = _Lists(lists, failing={("TPEX", "mopsfin_t187ap06_O_ci")})
    store = TaiwanQuarterlyFinancialStore(tmp_path)

    report = refresh_latest_quarter(fake, store)

    assert report.added == 1 and report.ambiguous == ["1101.TWSE"]
    assert report.failures == ["TPEX ci: ValueError: upstream returned non-JSON response (text/html)"]
    assert store.rows_for("2330.TWSE")[0]["revenue"] == 1_000_000
    assert store.rows_for("1101.TWSE") == []
    assert len(fake.calls) == 23, "a failed income list skips its balance list"


def test_refresh_reuses_the_official_per_day_list_cache(tmp_path):
    requests: list[str] = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path.endswith("t187ap06_L_ci"):
            return httpx.Response(200, json=[_twse_ci_income()])
        if request.url.path.endswith("t187ap07_L_ci"):
            return httpx.Response(200, json=[_twse_ci_balance()])
        return httpx.Response(200, json=[{"出表日期": "1151029", "年度": "", "季別": "", "公司代號": ""}])

    fundamentals = TaiwanOfficialFundamentals(httpx.Client(transport=httpx.MockTransport(handler)))
    store = TaiwanQuarterlyFinancialStore(tmp_path)

    refresh_latest_quarter(fundamentals, store)
    assert len(requests) == 24 and len(set(requests)) == 24
    second = refresh_latest_quarter(fundamentals, store)
    assert len(requests) == 24, "the same Taipei date sends no second request"
    assert (second.added, second.unchanged) == (0, 1)
    fundamentals.financial_statement("2330.TWSE", "TWSE")
    assert len(requests) == 24, "the stock page reads the same cached lists"


def test_a_failed_refresh_is_only_logged(monkeypatch, caplog):
    def broken():
        raise OSError("disk full")

    monkeypatch.setattr(quarterly_financials, "refresh_latest_quarter", broken)
    refresh_quarterly_financials("daily+institutional")
    assert "quarterly financials archive (daily+institutional) failed" in caplog.text


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


@pytest.mark.parametrize("update_error", [None, RuntimeError("boom")])
def test_the_scheduled_run_archives_last_and_survives_an_archive_failure(monkeypatch, update_error):
    import asyncio

    from app.taiwan import daily_update

    order: list[str] = []

    def failing_archive():
        order.append("archive")
        raise OSError("disk full")

    monkeypatch.setattr(daily_pipeline, "refresh_quarterly_financials", refresh_quarterly_financials)
    monkeypatch.setattr(quarterly_financials, "refresh_latest_quarter", failing_archive)
    monkeypatch.setattr(daily_pipeline, "refresh_benchmark_index", lambda _l: order.append("benchmark"))
    monkeypatch.setattr(daily_update, "TaiwanDailyUpdateService", _UpdateService(order, update_error))
    monkeypatch.setattr(daily_pipeline, "refresh_enriched_cache", lambda _l: order.append("enriched"))
    monkeypatch.setattr(daily_pipeline, "refresh_strategy_cache", lambda _l: order.append("strategy"))

    async def _build():
        return daily_pipeline.start_scheduler()

    loop = asyncio.new_event_loop()
    try:
        scheduler = loop.run_until_complete(_build())
        try:
            job = scheduler.get_job("taiwan_daily_update")
            job.func(**job.kwargs)
        finally:
            scheduler.shutdown(wait=False)
    finally:
        loop.close()

    expected = ["benchmark", "update", "archive"] if update_error else [
        "benchmark", "update", "enriched", "strategy", "archive",
    ]
    assert order == expected


def test_the_archive_still_runs_when_the_after_close_work_raises(monkeypatch):
    import asyncio

    from app.taiwan import daily_update

    order: list[str] = []

    def broken_enriched(_label):
        order.append("enriched")
        raise RuntimeError("repo exploded")

    monkeypatch.setattr(daily_pipeline, "refresh_quarterly_financials", lambda _l: order.append("archive"))
    monkeypatch.setattr(daily_pipeline, "refresh_benchmark_index", lambda _l: order.append("benchmark"))
    monkeypatch.setattr(daily_update, "TaiwanDailyUpdateService", _UpdateService(order))
    monkeypatch.setattr(daily_pipeline, "refresh_enriched_cache", broken_enriched)

    async def _build():
        return daily_pipeline.start_scheduler()

    loop = asyncio.new_event_loop()
    try:
        scheduler = loop.run_until_complete(_build())
        try:
            job = scheduler.get_job("taiwan_daily_update")
            with pytest.raises(RuntimeError, match="repo exploded"):
                job.func(**job.kwargs)
        finally:
            scheduler.shutdown(wait=False)
    finally:
        loop.close()

    assert order == ["benchmark", "update", "enriched", "archive"]


# --- derivation ----------------------------------------------------------------------------


def _stored(year, quarter, **values):
    return _row(year=year, quarter=quarter, period_end=f"{year}-Q{quarter}", **values)


def test_single_quarter_figures_subtract_the_previous_cumulative():
    rows = [
        _stored(2025, 4, parent_equity=2_000),
        _stored(2026, 1, revenue=100, gross_profit=50, operating_income=30, net_income=20,
                parent_net_income=18, cumulative_eps=0.5, parent_equity=2_100),
        _stored(2026, 2, revenue=250, gross_profit=110, operating_income=60, net_income=45,
                parent_net_income=40, cumulative_eps=1.25, parent_equity=2_300),
    ]
    q1, q2 = derive_periods(rows)[1:]

    assert q1.single.revenue == 100 and q1.single.eps == 0.5
    assert q1.single.gross_margin_pct == pytest.approx(50.0)
    assert q2.single.revenue == 150 and q2.single.gross_profit == 60
    assert q2.single.operating_income == 30 and q2.single.net_income == 25
    assert q2.single.eps == pytest.approx(0.75)
    assert q2.single.gross_margin_pct == pytest.approx(40.0)
    assert q2.single.operating_margin_pct == pytest.approx(20.0)
    assert q2.single.net_margin_pct == pytest.approx(25 / 150 * 100)
    assert q2.cumulative.gross_margin_pct == pytest.approx(44.0)
    assert q2.cumulative.roe_pct is None
    assert q1.single.roe_pct == pytest.approx(18 / 2_050 * 100)
    assert q2.single.roe_pct == pytest.approx(22 / 2_200 * 100)


def test_q4_single_quarter_and_a_missing_previous_quarter():
    rows = [
        _stored(2025, 2, revenue=500),
        _stored(2025, 3, revenue=800, cumulative_eps=3.0),
        _stored(2025, 4, revenue=1_200, cumulative_eps=4.5),
    ]
    q2, q3, q4 = derive_periods(rows)

    assert q2.single.revenue is None and q2.single.eps is None
    assert q2.single.gross_margin_pct is None
    assert q2.single_missing_reason == "previous_quarter_missing"
    assert q2.single.roe_pct is None and q2.roe_missing_reason == "opening_equity_missing"
    assert q3.single.revenue == 300 and q3.single_missing_reason is None
    assert q4.single.revenue == 400 and q4.single.eps == pytest.approx(1.5)


def test_roe_needs_both_equity_ends():
    rows = [_stored(2026, 1, parent_equity=None), _stored(2026, 2)]
    q1, q2 = derive_periods(rows)
    assert q1.single.roe_pct is None and q1.roe_missing_reason == "opening_equity_missing"
    assert q2.single.roe_pct is None and q2.roe_missing_reason == "opening_equity_missing"


def test_roe_names_a_missing_closing_equity():
    rows = [_stored(2026, 1), _stored(2026, 2, parent_equity=None)]
    _, q2 = derive_periods(rows)
    assert q2.single.roe_pct is None and q2.roe_missing_reason == "closing_equity_missing"


def test_a_field_null_in_both_quarters_stays_null_in_the_single_quarter():
    rows = [
        _stored(2026, 1, gross_profit=None, operating_income=None),
        _stored(2026, 2, gross_profit=None, operating_income=None),
    ]
    _, q2 = derive_periods(rows)
    assert q2.single.gross_profit is None and q2.single.operating_income is None
    assert q2.single.gross_margin_pct is None and q2.single.operating_margin_pct is None
    assert q2.single.revenue == 0


def test_financial_rows_keep_null_margins_instead_of_zero():
    (period,) = derive_periods([_stored(2026, 1, gross_profit=None, operating_income=None)])
    assert period.cumulative.gross_margin_pct is None
    assert period.cumulative.operating_margin_pct is None
    assert period.cumulative.net_margin_pct == pytest.approx(30.0)


def test_year_ago_quarter_and_annual_eps():
    rows = [
        _stored(2024, 4, cumulative_eps=8.0),
        _stored(2025, 1, revenue=100),
        _stored(2025, 4, cumulative_eps=9.5),
        _stored(2026, 1, revenue=120),
    ]
    periods = derive_periods(rows)
    latest = periods[-1]
    assert latest.year_ago is not None
    assert (latest.year_ago.fiscal_year, latest.year_ago.fiscal_quarter) == (2025, 1)
    assert latest.year_ago.single.revenue == 100
    assert periods[1].year_ago is None


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


def test_service_reports_display_only_history(tmp_path):
    store = TaiwanQuarterlyFinancialStore(tmp_path)
    store.upsert([_row(year=2025, quarter=4, cumulative_eps=9.5), _row()])
    result = TaiwanQuarterlyFinancialsService(store, _Master()).get("2330.TWSE")

    assert result.applicable and result.usage_scope == "historical_reference"
    assert result.available_at is None and result.display_only is True
    assert result.accumulating and result.target_quarters == 8
    assert [(q.fiscal_year, q.fiscal_quarter) for q in result.quarters] == [(2025, 4), (2026, 2)]
    assert [(a.fiscal_year, a.eps) for a in result.annual_eps] == [(2025, 9.5)]
    assert result.quarters[-1].source == "twse_openapi"
    assert result.quarters[-1].first_retrieved_at == RETRIEVED.isoformat()


@pytest.mark.parametrize(("master", "reason"), [(_Master("etf"), "etf"), (_Master(security_class="tdr"), "tdr")])
def test_etf_and_tdr_are_not_applicable(tmp_path, master, reason):
    store = TaiwanQuarterlyFinancialStore(tmp_path)
    store.upsert([_row("0050.TWSE")])
    result = TaiwanQuarterlyFinancialsService(store, master).get("0050.TWSE")
    assert not result.applicable and result.not_applicable_reason == reason
    assert result.quarters == []


def test_unknown_security_is_a_lookup_error(tmp_path):
    with pytest.raises(LookupError):
        TaiwanQuarterlyFinancialsService(TaiwanQuarterlyFinancialStore(tmp_path), _Master(None)).get("2330.TWSE")


@pytest.fixture
def client():
    return TestClient(app, client=("127.0.0.1", 50000))


def _serve(monkeypatch, service):
    monkeypatch.setattr("app.api.taiwan.get_taiwan_quarterly_financials_service", lambda: service)


def test_api_returns_stored_quarters(tmp_path, monkeypatch, client):
    store = TaiwanQuarterlyFinancialStore(tmp_path)
    store.upsert([_row(quarter=1, period_end="2026-03-31", revenue=400), _row()])
    _serve(monkeypatch, TaiwanQuarterlyFinancialsService(store, _Master()))

    response = client.get("/api/taiwan/stocks/2330.TWSE/quarterly-financials")

    assert response.status_code == 200
    payload = response.json()
    assert payload["usage_scope"] == "historical_reference" and payload["available_at"] is None
    assert payload["display_only"] is True
    assert payload["quarters"][1]["single"]["revenue"] == 600
    assert payload["quarters"][1]["cumulative"]["revenue"] == 1_000


def test_api_returns_an_empty_accumulating_answer(tmp_path, monkeypatch, client):
    _serve(monkeypatch, TaiwanQuarterlyFinancialsService(TaiwanQuarterlyFinancialStore(tmp_path), _Master()))
    payload = client.get("/api/taiwan/stocks/2330.TWSE/quarterly-financials").json()
    assert payload["applicable"] is True and payload["quarters"] == []
    assert payload["accumulating"] is True and payload["annual_eps"] == []


def test_api_errors(tmp_path, monkeypatch, client):
    store = TaiwanQuarterlyFinancialStore(tmp_path)
    path = store.partition_path(2026, 2)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not parquet")
    _serve(monkeypatch, TaiwanQuarterlyFinancialsService(store, _Master()))
    failed = client.get("/api/taiwan/stocks/2330.TWSE/quarterly-financials")
    assert failed.status_code == 500
    detail = failed.json()["detail"]
    assert detail == "季度財報讀取失敗"
    assert "/" not in detail and "\\" not in detail and "part.parquet" not in detail

    _serve(monkeypatch, TaiwanQuarterlyFinancialsService(store, _Master(None)))
    assert client.get("/api/taiwan/stocks/2330.TWSE/quarterly-financials").status_code == 404
    assert client.get("/api/taiwan/stocks/not-a-symbol/quarterly-financials").status_code == 400


# --- display-only boundary -----------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parents[1] / "app"
DISPLAY_ONLY_MODULES = {"app.taiwan.quarterly_financials", "app.taiwan.quarterly_financials_store"}
SELECTION_ROOTS = (
    "app/backtest", "app/strategy", "app/api/backtest.py", "app/api/screener.py",
    "app/services/screener.py", "app/services/strategy_cache.py", "app/services/strategy_inputs.py",
    "app/services/strategy_pick_backfill.py", "app/services/strategy_pick_history.py",
    "app/services/strategy_pick_performance.py", "app/taiwan/screener.py",
    "app/taiwan/screener_nl.py", "app/taiwan/comparison.py",
)


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(APP_DIR.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _module_path(name: str) -> Path | None:
    base = APP_DIR.parent.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _imports(path: Path) -> set[str]:
    package = _module_name(path).rsplit(".", 1)[0] if path.name != "__init__.py" else _module_name(path)
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                module = f"{anchor}.{node.module}" if node.module else anchor
            else:
                module = node.module or ""
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return {name for name in found if name.startswith("app.") and _module_path(name)}


def test_backtest_and_selection_never_reach_the_display_only_archive():
    pending = []
    for root in SELECTION_ROOTS:
        target = APP_DIR.parent / root
        pending.extend(sorted(target.rglob("*.py")) if target.is_dir() else [target])
    assert len(pending) > 20, "the scan must cover the backtest and selection packages"
    seen: dict[str, str] = {}
    while pending:
        path = pending.pop()
        for name in _imports(path) - set(seen):
            seen[name] = _module_name(path)
            pending.append(_module_path(name))
    reached = DISPLAY_ONLY_MODULES & set(seen)
    assert not reached, {name: seen[name] for name in reached}


def test_the_archive_never_writes_the_backtest_financial_metrics_path(tmp_path, monkeypatch):
    from app.taiwan import data_root

    monkeypatch.setattr(data_root, "taiwan_data_root", lambda: tmp_path / "taiwan")
    store = TaiwanQuarterlyFinancialStore()
    store.upsert([_row()])
    written = [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.parquet")]
    assert written == ["taiwan/quarterly_financials/year=2026/quarter=2/part.parquet"]
    assert not pl.read_parquet(tmp_path / written[0]).is_empty()
