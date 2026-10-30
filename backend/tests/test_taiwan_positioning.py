from __future__ import annotations

# ruff: noqa: RUF001 -- fixtures mirror official placeholders exactly.
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import httpx
import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan import positioning
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.fundamentals import TaiwanOfficialFundamentals
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.monthly_revenue_store import TaiwanMonthlyRevenueStore
from app.taiwan.positioning import (
    TaiwanPositioningService,
    decimal_to_percent,
    dimension_grade,
    metric_grade,
    metric_score,
    mid_rank_percentile,
    revenue_table,
    segments,
    statement_tables,
    valuation_number,
    valuation_tables,
)
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.quarterly_financials_store import TaiwanQuarterlyFinancialStore
from app.taiwan.universe import get_security_master

LAST_SESSION = date(2026, 10, 29)
RETRIEVED = datetime(2026, 10, 29, 16, 31, tzinfo=TAIPEI)


@pytest.fixture(autouse=True)
def _fresh_snapshot():
    positioning.clear_snapshot_cache()
    yield
    positioning.clear_snapshot_cache()


def _percentiles(values: dict[str, float | None]) -> dict[str, float]:
    frame = pl.DataFrame({"symbol": list(values), "value": list(values.values())}, schema={"symbol": pl.Utf8, "value": pl.Float64})
    ranked = mid_rank_percentile(frame)
    return dict(zip(ranked["symbol"].to_list(), ranked["percentile"].to_list(), strict=True))




def test_ties_share_the_average_rank_and_missing_values_are_not_ranked():
    got = _percentiles({"a": 10.0, "b": 20.0, "c": 20.0, "d": 30.0, "e": None})
    assert got == pytest.approx({"a": 12.5, "b": 50.0, "c": 50.0, "d": 87.5})
    assert _percentiles({"only": 3.0}) == {"only": 50.0}
    assert _percentiles({"x": 0.0, "y": 0.0}) == {"x": 50.0, "y": 50.0}


def test_reverse_metrics_score_the_complement():
    assert metric_score(72.1, "pb") == pytest.approx(27.9)
    assert metric_score(49.3, "pe") == pytest.approx(50.7)
    assert metric_score(51.6, "dividend_yield") == 51.6
    assert metric_grade(50.7, "pe") == ("fair", 2)
    assert metric_grade(27.9, "pb") == ("pricey", 1)
    assert metric_grade(19.9, "pe") == ("expensive", 0)
    assert metric_grade(80.0, "pe") == ("cheap", 3)
    assert metric_grade(51.6, "dividend_yield") == ("good", 2)
    assert metric_grade(20.0, "roe") == ("average", 1)
    assert metric_grade(90.6, "operating_margin") == ("top", 3)


def test_dimension_grade_is_held_between_its_metric_grades():
    assert dimension_grade("growth", 58.1, [2]) == ("good", 2), "neutral by score, lifted to the only metric"
    assert dimension_grade("momentum", 84.45, [3, 2]) == ("strong", 3)
    assert dimension_grade("chips", 85.0, [2, 2]) == ("good", 2), "capped at the best metric"
    assert dimension_grade("valuation", 38.3, [2, 1]) == ("pricey", 1)
    assert dimension_grade("valuation", 10.0, [1, 1]) == ("pricey", 1)
    assert segments(58.1) == 3 and segments(0.0) == 1 and segments(100.0) == 5 and segments(20.0) == 1


def test_return_windows_are_twenty_and_two_hundred_forty_sessions():
    assert positioning.SHORT_MOMENTUM_SESSIONS == 20
    assert positioning.LONG_MOMENTUM_SESSIONS == 240


def test_unit_conversion_happens_once_at_the_boundary():
    assert decimal_to_percent(0.0366) == pytest.approx(3.66)
    assert decimal_to_percent(-0.05) == pytest.approx(-5.0)
    assert decimal_to_percent(None) is None
    tables, _ = valuation_tables(
        {"TWSE": [{"Date": "1151029", "Code": "2330", "PEratio": "20", "PBratio": "5", "DividendYield": "2.36"}],
         "TPEX": [{"Date": "1151029", "SecuritiesCompanyCode": "8069", "PriceEarningRatio": "9",
                   "PriceBookRatio": "1", "YieldRatio": "0.5"},
                  {"Date": "1151029", "SecuritiesCompanyCode": "6488", "PriceEarningRatio": 9,
                   "PriceBookRatio": 1, "YieldRatio": 0}]},
        {"2330.TWSE", "8069.TPEX", "6488.TPEX"},
    )
    yields = dict(zip(tables["dividend_yield"].rows["symbol"], tables["dividend_yield"].rows["value"], strict=True))
    assert yields == {"2330.TWSE": 2.36, "8069.TPEX": 0.5, "6488.TPEX": 0.0}, "the official yield is already percent"


@pytest.mark.parametrize("raw", ["", "-", "--", "N/A", "－", "—", "abc"])
def test_valuation_placeholders_are_missing_not_zero(raw):
    assert valuation_number(raw) is None


def _twse(code, pe="10", pb="1", dy="1", day="1151029"):
    return {"Date": day, "Code": code, "Name": code, "PEratio": pe, "PBratio": pb, "DividendYield": dy}


def _tpex(code, pe="10", pb="1", dy="1", day="1151028"):
    return {"Date": day, "SecuritiesCompanyCode": code, "CompanyName": code,
            "PriceEarningRatio": pe, "PriceBookRatio": pb, "YieldRatio": dy}


def test_loss_pe_is_not_ranked_and_a_malformed_value_does_not_fail_the_list():
    universe = {"1101.TWSE", "1102.TWSE", "2330.TWSE", "2317.TWSE", "2303.TWSE", "2454.TWSE", "8069.TPEX"}
    tables, dates = valuation_tables(
        {
            "TWSE": [
                _twse("1101", pe=""), _twse("1102", pe="0.00"), _twse("2330", pe="-3.2"),
                _twse("2317", pe="－"), _twse("2303", pe="15", pb="abc"), _twse("2454", pe="30"),
                _twse("0050", pe="1"),
            ],
            "TPEX": [_tpex("8069", pe="20")],
        },
        universe,
    )
    pe = {r["symbol"]: r for r in tables["pe"].rows.iter_rows(named=True)}
    for symbol in ("1101.TWSE", "1102.TWSE", "2330.TWSE", "2317.TWSE"):
        assert pe[symbol]["reason"] == "loss_or_not_applicable" and pe[symbol]["value"] is None
    assert "0050.TWSE" not in pe, "an ETF row never enters the universe"
    assert pe["2454.TWSE"]["percentile"] == pytest.approx(5 / 6 * 100)
    assert {r["compared"] for r in pe.values()} == {3}
    pb = {r["symbol"]: r for r in tables["pb"].rows.iter_rows(named=True)}
    assert pb["2303.TWSE"]["reason"] == "no_value" and pb["2454.TWSE"]["compared"] == 6
    assert dates == {"TWSE": "2026-10-29", "TPEX": "2026-10-28"}


def test_one_failed_valuation_list_makes_valuation_unavailable():
    tables, dates = valuation_tables({"TWSE": [_twse("2330")], "TPEX": None}, {"2330.TWSE"})
    assert {name: t.unavailable for name, t in tables.items()} == dict.fromkeys(
        ("pe", "pb", "dividend_yield"), "dataset_unavailable"
    )
    assert dates == {"TWSE": None, "TPEX": None}


def _statement(symbol, *, category="ci", year=2026, quarter=2, revenue=1000, operating=100,
               parent_income=50, parent_equity=500):
    return {
        "symbol": symbol, "exchange": symbol.split(".")[1], "accounting_category": category,
        "fiscal_year": year, "fiscal_quarter": quarter, "period_end": f"{year}-{quarter * 3:02d}-30",
        "revenue": revenue, "gross_profit": None, "operating_income": operating, "net_income": parent_income,
        "parent_net_income": parent_income, "cumulative_eps": 1.0, "assets": 2 * (parent_equity or 0),
        "liabilities": parent_equity, "equity": parent_equity, "parent_equity": parent_equity,
        "source": "twse_openapi", "source_url": "https://example.invalid/t187ap06",
        "last_retrieved_at": RETRIEVED.isoformat(),
    }


def _store_statements(tmp_path, rows):
    store = TaiwanQuarterlyFinancialStore(tmp_path / "quarterly")
    store.upsert(rows)
    return store


def test_roe_is_annualized_year_to_date_and_margin_ranks_general_industry_only(tmp_path):
    store = _store_statements(tmp_path, [
        _statement("2330.TWSE", revenue=1000, operating=400, parent_income=100, parent_equity=1000),
        _statement("2317.TWSE", revenue=1000, operating=100, parent_income=50, parent_equity=1000),
        _statement("2881.TWSE", category="fh", revenue=500, operating=None, parent_income=80, parent_equity=400),
        _statement("1101.TWSE", parent_income=10, parent_equity=-5),
        _statement("1102.TWSE", parent_income=None),
        _statement("8069.TPEX", quarter=1, parent_income=10, parent_equity=100),
    ])
    universe = {"2330.TWSE", "2317.TWSE", "2881.TWSE", "1101.TWSE", "1102.TWSE", "8069.TPEX"}
    tables = statement_tables(store.read_all(), universe)
    roe = {(r["symbol"], r["period"]): r for r in tables["roe"].rows.iter_rows(named=True)}
    assert roe[("2330.TWSE", "2026Q2")]["value"] == pytest.approx(20.0), "100 * 4 / 2 / 1000"
    assert roe[("2881.TWSE", "2026Q2")]["value"] == pytest.approx(40.0)
    assert roe[("1101.TWSE", "2026Q2")]["reason"] == "non_positive_equity"
    assert roe[("1102.TWSE", "2026Q2")]["reason"] == "no_value"
    assert roe[("2330.TWSE", "2026Q2")]["percentile"] == pytest.approx(50.0)
    assert roe[("2330.TWSE", "2026Q2")]["compared"] == 3, "the Q1-only stock is not compared with Q2"
    assert roe[("8069.TPEX", "2026Q1")]["value"] == pytest.approx(40.0), "10 * 4 / 1 / 100"
    assert roe[("8069.TPEX", "2026Q1")]["compared"] == 1
    margin = {r["symbol"]: r for r in tables["operating_margin"].rows.filter(pl.col("period") == "2026Q2").iter_rows(named=True)}
    assert margin["2330.TWSE"]["value"] == pytest.approx(40.0)
    assert margin["2881.TWSE"]["reason"] == "not_general_industry"
    assert margin["2330.TWSE"]["compared"] == 4, "every general-industry Q2 statement, whatever its ROE"


def test_revenue_growth_is_compared_within_the_same_revenue_month(tmp_path):
    store = TaiwanMonthlyRevenueStore(tmp_path / "revenue")
    base = {"revenue": 1, "previous_month_revenue": 1, "previous_year_revenue": 1, "cumulative_revenue": 1,
            "previous_year_cumulative_revenue": 1, "mom_pct": 0.0, "cumulative_yoy_pct": 0.0,
            "report_date": "2026-10-09", "source": "twse_openapi", "source_url": "x",
            "last_retrieved_at": RETRIEVED.isoformat()}
    store.upsert([
        {**base, "symbol": "2330.TWSE", "exchange": "TWSE", "year": 2026, "month": 9, "yoy_pct": 25.0},
        {**base, "symbol": "2317.TWSE", "exchange": "TWSE", "year": 2026, "month": 9, "yoy_pct": -5.0},
        {**base, "symbol": "2881.TWSE", "exchange": "TWSE", "year": 2026, "month": 9, "yoy_pct": None},
        {**base, "symbol": "8069.TPEX", "exchange": "TPEX", "year": 2026, "month": 8, "yoy_pct": 100.0},
    ])
    table = revenue_table(store.read_all(), {"2330.TWSE", "2317.TWSE", "2881.TWSE", "8069.TPEX"})
    rows = {r["symbol"]: r for r in table.rows.iter_rows(named=True)}
    assert rows["2330.TWSE"]["percentile"] == pytest.approx(75.0) and rows["2330.TWSE"]["compared"] == 2
    assert rows["2881.TWSE"]["reason"] == "no_value"
    assert rows["8069.TPEX"]["period"] == "2026-08" and rows["8069.TPEX"]["compared"] == 1




def _sessions(count: int) -> list[date]:
    days: list[date] = []
    day = LAST_SESSION
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return days[::-1]


SESSIONS = _sessions(positioning.LONG_MOMENTUM_SESSIONS + 1)


def _write_daily(store: TaiwanDailyStore) -> None:
    rows = []
    for i, session in enumerate(SESSIONS):
        closes = {"2330.TWSE": 50.0 if i == 0 else 100.0, "2317.TWSE": 100.0 if i == 0 else 90.0, "2881.TWSE": 20.0}
        if i >= len(SESSIONS) - 100:
            closes["8069.TPEX"] = 40.0
        for symbol, close in closes.items():
            rows.append({"symbol": symbol, "date": session, "open": close, "high": close, "low": close,
                         "close": close, "volume": 1000.0, "amount": close * 1000.0, "quote_ts": None})
    store.write_batch(pl.DataFrame(rows))


def _write_flows(store: TaiwanInstitutionalStore) -> None:
    rows = []
    window = SESSIONS[-positioning.CHIP_WINDOW_SESSIONS:]
    for i, session in enumerate(window):
        nets = {"2330.TWSE": 100, "2317.TWSE": -50, "8069.TPEX": 10}
        if i > 0:
            nets["2881.TWSE"] = 500
        for symbol, net in nets.items():
            rows.append({"symbol": symbol, "date": session, "trade_date": session, "foreign_net": net,
                         "investment_trust_net": 0, "dealer_net": 0, "status": "official", "source": "synthetic"})
    store.write_batch(pl.DataFrame(rows))


def _write_enriched(data_dir, day="2026-10-29"):
    frame = pl.DataFrame({
        "symbol": ["2330.TWSE", "2317.TWSE", "2881.TWSE", "8069.TPEX", "0050.TWSE"],
        "momentum_60d": [0.1, 0.3478, None, -0.05, 0.9],
    })
    path = data_dir / "kline_daily_enriched" / f"date={day}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)


def _write_revenue(store: TaiwanMonthlyRevenueStore, month: int = 9) -> None:
    base = {"revenue": 1, "previous_month_revenue": 1, "previous_year_revenue": 1, "cumulative_revenue": 1,
            "previous_year_cumulative_revenue": 1, "mom_pct": 0.0, "cumulative_yoy_pct": 0.0,
            "report_date": "2026-10-09", "source": "twse_openapi", "source_url": "x",
            "last_retrieved_at": RETRIEVED.isoformat()}
    store.upsert([
        {**base, "symbol": "2330.TWSE", "exchange": "TWSE", "year": 2026, "month": month, "yoy_pct": 25.0},
        {**base, "symbol": "2317.TWSE", "exchange": "TWSE", "year": 2026, "month": month, "yoy_pct": -5.0},
    ])


class _Lists:
    """Official valuation endpoints behind an httpx mock; counts every request sent."""

    def __init__(self, tpex_status=200):
        self.requests: list[str] = []
        self.tpex_status = tpex_status

    def handler(self, request):
        self.requests.append(request.url.path)
        if request.url.path.endswith("BWIBBU_ALL"):
            return httpx.Response(200, json=[
                _twse("2330", pe="20", pb="5", dy="2"), _twse("2317", pe="10", pb="1.5", dy="4"),
                _twse("2881", pe="－", pb="1.0", dy=""), _twse("0050", pe="15", pb="1", dy="3"),
            ])
        if request.url.path.endswith("tpex_mainboard_peratio_analysis"):
            if self.tpex_status != 200:
                return httpx.Response(self.tpex_status)
            return httpx.Response(200, json=[_tpex("8069", pe="0.00", pb="3", dy="0")])
        raise AssertionError(f"unexpected request {request.url}")

    def fundamentals(self):
        return TaiwanOfficialFundamentals(httpx.Client(transport=httpx.MockTransport(self.handler)))


@pytest.fixture
def market(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    root = tmp_path / "taiwan"
    stores = SimpleNamespace(
        quarterly=TaiwanQuarterlyFinancialStore(root / "quarterly_financials"),
        revenue=TaiwanMonthlyRevenueStore(root / "monthly_revenue"),
        daily=TaiwanDailyStore(root / "daily"),
        institutional=TaiwanInstitutionalStore(root / "institutional"),
    )
    stores.quarterly.upsert([
        _statement("2330.TWSE", revenue=1000, operating=400, parent_income=100, parent_equity=1000),
        _statement("2317.TWSE", revenue=1000, operating=100, parent_income=50, parent_equity=1000),
        _statement("2881.TWSE", category="fh", revenue=500, operating=None, parent_income=80, parent_equity=400),
    ])
    _write_revenue(stores.revenue)
    _write_daily(stores.daily)
    _write_flows(stores.institutional)
    _write_enriched(tmp_path)
    stores.data_dir = tmp_path
    return stores


def _service(market, lists: _Lists, master=None, **overrides):
    from app.taiwan.corporate_actions import TaiwanCorporateActionStore

    return TaiwanPositioningService(
        fundamentals=lists.fundamentals(),
        security_master=master or get_security_master(),
        quarterly_store=overrides.get("quarterly", market.quarterly),
        revenue_store=market.revenue,
        daily_store=market.daily,
        institutional_store=market.institutional,
        corporate_actions=TaiwanCorporateActionStore(market.data_dir / "taiwan" / "corporate_actions"),
    )


def _metrics(response) -> dict[str, dict]:
    return {m.id: m.model_dump() for d in response.dimensions for m in d.metrics}


def test_one_stock_is_ranked_on_every_metric_with_its_own_dates(market):
    response = _service(market, _Lists()).get("2330.TWSE")

    assert response.applicable and response.usage_scope == "current_reference" and response.available_at is None
    assert response.valuation_dates == {"TWSE": "2026-10-29", "TPEX": "2026-10-28"}
    assert response.valuation_period_mixed is True
    m = _metrics(response)
    assert m["pe"]["status"] == "ranked" and m["pe"]["compared"] == 2, "loss PE and the ETF are not compared"
    assert m["pe"]["percentile"] == pytest.approx(75.0) and m["pe"]["score"] == pytest.approx(25.0)
    assert m["pe"]["grade"] == "pricey" and m["pe"]["as_of"] == "2026-10-29" and m["pe"]["unit"] == "times"
    assert m["pb"]["percentile"] == pytest.approx(87.5) and m["pb"]["grade"] == "expensive"
    assert m["dividend_yield"]["compared"] == 3 and m["dividend_yield"]["percentile"] == pytest.approx(50.0)
    assert m["roe"]["value"] == pytest.approx(20.0) and m["roe"]["period"] == "2026Q2"
    assert m["roe"]["basis"] == "ytd_annualized" and m["roe"]["compared"] == 3
    assert m["operating_margin"]["value"] == pytest.approx(40.0) and m["operating_margin"]["compared"] == 2
    assert m["operating_margin"]["basis"] == "ytd"
    assert m["revenue_yoy"]["period"] == "2026-09" and m["revenue_yoy"]["compared"] == 2
    assert m["return_60d"]["value"] == pytest.approx(10.0) and m["return_60d"]["compared"] == 3
    assert m["return_240d"]["value"] == pytest.approx(100.0) and m["return_240d"]["compared"] == 3
    assert m["return_240d"]["as_of"] == LAST_SESSION.isoformat()
    assert m["foreign_flow_20d"]["value"] == pytest.approx(10.0), "2000 shares over 20000 shares"
    assert m["foreign_flow_20d"]["compared"] == 3, "2881 lacks one session of flows"

    valuation = response.dimensions[0]
    assert valuation.id == "valuation" and valuation.score == pytest.approx((25.0 + 12.5 + 50.0) / 3)
    assert (valuation.grade, valuation.tier, valuation.segments) == ("pricey", 1, 2)


def test_not_ranked_reasons_for_other_stocks(market):
    service = _service(market, _Lists())
    financial = _metrics(service.get("2881.TWSE"))
    assert financial["pe"]["status"] == "not_ranked" and financial["pe"]["reason"] == "loss_or_not_applicable"
    assert financial["dividend_yield"]["reason"] == "no_value" and financial["dividend_yield"]["value"] is None
    assert financial["operating_margin"]["reason"] == "not_general_industry"
    assert financial["roe"]["status"] == "ranked"
    assert financial["revenue_yoy"]["reason"] == "no_value" and financial["revenue_yoy"]["compared"] == 2
    assert financial["return_60d"]["reason"] == "insufficient_history"
    assert financial["foreign_flow_20d"]["reason"] == "incomplete_window"

    otc = _metrics(service.get("8069.TPEX"))
    assert otc["pe"]["reason"] == "loss_or_not_applicable", "0.00 is a placeholder, not a ratio"
    assert otc["dividend_yield"]["status"] == "ranked" and otc["dividend_yield"]["value"] == 0.0
    assert otc["pe"]["as_of"] == "2026-10-28"
    assert otc["return_240d"]["reason"] == "insufficient_history"
    assert otc["return_60d"]["value"] == pytest.approx(-5.0)

    other = service.get("2317.TWSE")
    assert _metrics(other)["return_60d"]["value"] == pytest.approx(34.78)
    assert _metrics(other)["return_240d"]["value"] == pytest.approx(-10.0)


def test_a_loss_pe_is_left_out_of_the_valuation_average(market):
    valuation = _service(market, _Lists()).get("8069.TPEX").dimensions[0]
    pe, pb, dy = valuation.metrics
    assert pe.reason == "loss_or_not_applicable" and pe.score is None
    assert pb.score == pytest.approx(100 - 62.5) and dy.score == pytest.approx(100 / 6)
    assert valuation.score == pytest.approx((37.5 + 100 / 6) / 2), "a loss PE is not averaged in as 0"
    assert (valuation.grade, valuation.tier) == ("pricey", 1)


def test_the_universe_leaves_out_tdrs():
    instruments = {
        "2330.TWSE": SimpleNamespace(symbol="2330.TWSE", code="2330", name="台積電", industry="半導體業",
                                     instrument_type="stock", security_class=None),
        "9103.TWSE": SimpleNamespace(symbol="9103.TWSE", code="9103", name="美德醫療-DR", industry=None,
                                     instrument_type="stock", security_class="tdr"),
    }
    master = SimpleNamespace(get_universe=lambda _kind: sorted(instruments), get_instrument=instruments.get)
    assert TaiwanPositioningService(fundamentals=_NoFetch(), security_master=master).universe() == {"2330.TWSE"}


def test_the_cross_section_is_built_once_per_dataset_dates_and_valuation_fetched_once(market, monkeypatch):
    lists = _Lists()
    service = _service(market, lists)
    builds: list[int] = []
    original = TaiwanPositioningService.build_snapshot

    def counting(self, *args):
        builds.append(1)
        return original(self, *args)

    monkeypatch.setattr(TaiwanPositioningService, "build_snapshot", counting)
    before = sorted(p for p in market.data_dir.rglob("*") if p.is_file())

    service.get("2330.TWSE")
    service.get("2317.TWSE")
    assert len(builds) == 1 and len(lists.requests) == 2, "two lists on the first build"

    positioning.clear_snapshot_cache()
    service.get("2330.TWSE")
    assert len(builds) == 2 and len(lists.requests) == 2, "a second build on the same day sends nothing"

    assert sorted(p for p in market.data_dir.rglob("*") if p.is_file()) == before, "nothing is written to disk"

    _write_enriched(market.data_dir, "2026-10-30")
    service.get("2330.TWSE")
    assert len(builds) == 3 and len(lists.requests) == 2, "a newer enriched partition invalidates the cache"

    _write_revenue(market.revenue, month=10)
    service.get("2330.TWSE")
    assert len(builds) == 4, "a new monthly revenue partition invalidates the cache"

    market.quarterly.upsert([_statement("2330.TWSE", quarter=3)])
    response = service.get("2330.TWSE")
    assert len(builds) == 5, "a new quarterly partition invalidates the cache"
    assert _metrics(response)["roe"]["period"] == "2026Q3"
    service.get("2330.TWSE")
    assert len(builds) == 5 and len(lists.requests) == 2


def test_a_failed_dataset_only_degrades_its_own_metrics(market, monkeypatch):
    failing = _metrics(_service(market, _Lists(tpex_status=404)).get("2330.TWSE"))
    for metric in ("pe", "pb", "dividend_yield"):
        assert failing[metric]["status"] == "unavailable" and failing[metric]["reason"] == "dataset_unavailable"
    assert failing["roe"]["status"] == "ranked" and failing["return_60d"]["status"] == "ranked"

    positioning.clear_snapshot_cache()

    def broken():
        raise OSError("unreadable partition")

    monkeypatch.setattr(market.quarterly, "read_all", broken)
    partial = _metrics(_service(market, _Lists()).get("2330.TWSE"))
    assert partial["roe"]["status"] == partial["operating_margin"]["status"] == "unavailable"
    assert partial["pe"]["status"] == "ranked" and partial["revenue_yoy"]["status"] == "ranked"


def test_empty_archives_and_stores_report_not_archived_or_unavailable(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    service = TaiwanPositioningService(
        fundamentals=_Lists().fundamentals(),
        quarterly_store=TaiwanQuarterlyFinancialStore(tmp_path / "q"),
        revenue_store=TaiwanMonthlyRevenueStore(tmp_path / "r"),
        daily_store=TaiwanDailyStore(tmp_path / "d"),
        institutional_store=TaiwanInstitutionalStore(tmp_path / "i"),
    )
    m = _metrics(service.get("2330.TWSE"))
    assert m["roe"]["reason"] == m["revenue_yoy"]["reason"] == "not_archived"
    assert m["return_60d"]["reason"] == "dataset_unavailable"
    assert m["return_240d"]["reason"] == "insufficient_history"
    assert m["foreign_flow_20d"]["reason"] == "dataset_unavailable"
    assert m["pe"]["status"] == "ranked"


class _NoFetch:
    def market_rows(self, *_args):
        raise AssertionError("a not-applicable listing must not fetch")


def test_etf_index_and_tdr_are_not_applicable_without_reading(market):
    service = TaiwanPositioningService(fundamentals=_NoFetch(), security_master=get_security_master())
    assert service.get("0050.TWSE").not_applicable_reason == "etf"
    index = service.get("t00.TWSE")
    assert index.applicable is False and index.not_applicable_reason == "index" and index.dimensions == []

    master = SimpleNamespace(get_instrument=lambda s: SimpleNamespace(
        symbol=s, code="9103", name="美德醫療-DR", industry=None, instrument_type="stock", security_class="tdr",
    ))
    assert TaiwanPositioningService(fundamentals=_NoFetch(), security_master=master).get(
        "9103.TWSE"
    ).not_applicable_reason == "tdr"



URL = "/api/taiwan/stocks/{}/positioning"


@pytest.fixture
def client():
    return TestClient(app, client=("127.0.0.1", 50000))


def test_api_returns_the_positioning(market, monkeypatch, client):
    monkeypatch.setattr("app.api.taiwan.get_taiwan_positioning_service", lambda: _service(market, _Lists()))
    response = client.get(URL.format("2330.TWSE"))
    assert response.status_code == 200
    payload = response.json()
    assert [d["id"] for d in payload["dimensions"]] == ["valuation", "profitability", "growth", "momentum", "chips"]
    assert payload["universe_size"] == 14 and payload["valuation_period_mixed"] is True
    pe = payload["dimensions"][0]["metrics"][0]
    assert pe["id"] == "pe" and pe["lower_is_better"] is True and pe["compared"] == 2


def test_api_marks_etf_not_applicable(monkeypatch, client):
    monkeypatch.setattr(
        "app.api.taiwan.get_taiwan_positioning_service",
        lambda: TaiwanPositioningService(fundamentals=_NoFetch()),
    )
    payload = client.get(URL.format("0050.TWSE")).json()
    assert payload["applicable"] is False and payload["not_applicable_reason"] == "etf"


def test_api_errors(tmp_path, monkeypatch, client):
    def broken(self):
        raise OSError(f"cannot open {tmp_path / 'part.parquet'}")

    monkeypatch.setattr(TaiwanPositioningService, "snapshot", broken)
    monkeypatch.setattr(
        "app.api.taiwan.get_taiwan_positioning_service",
        lambda: TaiwanPositioningService(fundamentals=_NoFetch()),
    )
    failed = client.get(URL.format("2330.TWSE"))
    assert failed.status_code == 500
    assert failed.json()["detail"] == "定位資料讀取失敗"
    assert str(tmp_path) not in failed.text and "parquet" not in failed.text.lower()
    assert client.get(URL.format("9999.TWSE")).status_code == 404
    assert client.get(URL.format("not-a-symbol")).status_code == 400


INDUSTRY_SESSIONS = SESSIONS[-25:]
LOOKBACK_20D = len(INDUSTRY_SESSIONS) - 1 - positioning.SHORT_MOMENTUM_SESSIONS
SEMICONDUCTOR = "半導體業"
# symbol: (industry, close before the 20-session lookback, close at it, newest close, foreign net, trust net)
INDUSTRY_STOCKS = {
    "2330.TWSE": (SEMICONDUCTOR, 70.0, 100.0, 110.0, 100, 0),
    "2303.TWSE": (SEMICONDUCTOR, 50.0, 100.0, 120.0, 50, 0),
    "2454.TWSE": (SEMICONDUCTOR, 90.0, 100.0, 110.0, 100, 0),
    "6488.TPEX": (SEMICONDUCTOR, 50.0, 50.0, 40.0, 0, 0),
    "5274.TPEX": (SEMICONDUCTOR, 80.0, 100.0, 130.0, -100, 20),
    "1101.TWSE": ("水泥工業", 10.0, 10.0, 11.0, 10, 0),
    "1102.TWSE": ("水泥工業", 10.0, 10.0, 9.0, -10, 0),
    "2603.TWSE": ("航運業", 10.0, 10.0, 12.0, 10, 0),
    "1234.TWSE": (None, 10.0, 10.0, 10.0, 0, 0),
    "4444.TWSE": ("未知產業（77）", 10.0, 10.0, 10.0, 0, 0),
}
# 2303 has no bar on these sessions, so a row-count lookback would reach an older close.
MISSING_2303 = set(INDUSTRY_SESSIONS[LOOKBACK_20D + 1:LOOKBACK_20D + 3])
EX_DIVIDEND_6488 = INDUSTRY_SESSIONS[-10]


def _industry_master():
    instruments = {
        symbol: SimpleNamespace(symbol=symbol, code=symbol[:4], name=f"name-{symbol[:4]}", industry=spec[0],
                                instrument_type="stock", security_class=None)
        for symbol, spec in INDUSTRY_STOCKS.items()
    }
    instruments["9103.TWSE"] = SimpleNamespace(symbol="9103.TWSE", code="9103", name="美德醫療-DR", industry=None,
                                               instrument_type="stock", security_class="tdr")
    return SimpleNamespace(get_universe=lambda _kind: sorted(instruments), get_instrument=instruments.get)


class _IndustryLists(_Lists):
    def handler(self, request):
        self.requests.append(request.url.path)
        if request.url.path.endswith("BWIBBU_ALL"):
            pes = {"2330": "20", "2303": "10", "2454": "30", "1101": "8", "1102": "12", "2603": "5",
                   "1234": "15", "4444": "15"}
            return httpx.Response(200, json=[_twse(code, pe=pe) for code, pe in pes.items()])
        if request.url.path.endswith("tpex_mainboard_peratio_analysis"):
            return httpx.Response(200, json=[_tpex("6488", pe="15"), _tpex("5274", pe="－")])
        raise AssertionError(f"unexpected request {request.url}")


def _industry_close(symbol: str, i: int, session: date) -> float:
    _industry, early, then, now, _foreign, _trust = INDUSTRY_STOCKS[symbol]
    if symbol == "6488.TPEX" and session >= EX_DIVIDEND_6488:
        return 40.0
    return early if i < LOOKBACK_20D else now if i == len(INDUSTRY_SESSIONS) - 1 else then


@pytest.fixture
def industry_market(tmp_path, monkeypatch):
    from app.config import settings
    from app.taiwan.corporate_actions import TaiwanCorporateActionStore, corporate_action_schema
    from app.taiwan.enrichment.corporate_actions import EXDIV

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    root = tmp_path / "taiwan"
    daily = TaiwanDailyStore(root / "daily")
    institutional = TaiwanInstitutionalStore(root / "institutional")
    actions = TaiwanCorporateActionStore(root / "corporate_actions")
    window = INDUSTRY_SESSIONS[-positioning.CHIP_WINDOW_SESSIONS:]
    bars, flows = [], []
    for i, session in enumerate(INDUSTRY_SESSIONS):
        for symbol, (*_prices, foreign, trust) in INDUSTRY_STOCKS.items():
            if not (symbol == "2303.TWSE" and session in MISSING_2303):
                close = _industry_close(symbol, i, session)
                bars.append({"symbol": symbol, "date": session, "open": close, "high": close, "low": close,
                             "close": close, "volume": 1000.0, "amount": close * 1000.0, "quote_ts": None})
            if session in window:
                flows.append({"symbol": symbol, "date": session, "trade_date": session, "foreign_net": foreign,
                              "investment_trust_net": trust, "dealer_net": 0, "status": "official",
                              "source": "synthetic"})
    daily.write_batch(pl.DataFrame(bars))
    institutional.write_batch(pl.DataFrame(flows))
    actions.upsert(pl.DataFrame([{
        "symbol": "6488.TPEX", "date": EX_DIVIDEND_6488, "kind": EXDIV, "pre_close": 50.0, "ref_price": 40.0,
        "limit_up": 44.0, "limit_down": 36.0, "open_ref": 40.0, "ex_factor": None, "official_label": None,
        "provider": "test", "source": "twse:twt49u", "source_url": None, "retrieved_at": None,
        "trade_date": EX_DIVIDEND_6488, "status": "ok",
    }], schema=corporate_action_schema()))
    master = _industry_master()
    lists = _IndustryLists()
    service = TaiwanPositioningService(
        fundamentals=lists.fundamentals(),
        security_master=master,
        quarterly_store=TaiwanQuarterlyFinancialStore(root / "quarterly_financials"),
        revenue_store=TaiwanMonthlyRevenueStore(root / "monthly_revenue"),
        daily_store=daily,
        institutional_store=institutional,
        corporate_actions=actions,
    )
    return SimpleNamespace(service=service, master=master, lists=lists, data_dir=tmp_path)


def _industry_metrics(response) -> dict[str, dict]:
    return {m.id: m.model_dump() for m in response.industry.metrics}


def test_industry_ranks_use_competition_ranks_with_ascending_mid_rank_percentiles(industry_market):
    response = industry_market.service.get("2330.TWSE")
    industry = response.industry
    assert industry.applicable and industry.industry == SEMICONDUCTOR and industry.members == 5
    assert [metric.id for metric in industry.metrics] == ["return_20d", "foreign_flow_20d", "trust_flow_20d", "pe"]
    m = _industry_metrics(response)
    ret = m["return_20d"]
    assert ret["status"] == "ranked" and ret["value"] == pytest.approx(10.0)
    assert (ret["rank"], ret["compared"]) == (3, 5), "tied with 2454 behind 5274 and 2303"
    assert ret["percentile"] == pytest.approx(40.0) and ret["as_of"] == LAST_SESSION.isoformat()
    foreign = m["foreign_flow_20d"]
    assert (foreign["rank"], foreign["compared"]) == (1, 5) and foreign["percentile"] == pytest.approx(80.0)
    assert foreign["value"] == pytest.approx(10.0), "2000 shares over 20000 shares"
    other = _industry_metrics(industry_market.service.get("2454.TWSE"))
    assert other["return_20d"]["rank"] == 3 and other["foreign_flow_20d"]["rank"] == 1
    assert _industry_metrics(industry_market.service.get("2303.TWSE"))["foreign_flow_20d"]["rank"] == 3


def test_industry_pe_ranks_the_cheapest_first_and_leaves_out_loss_pe(industry_market):
    pe = _industry_metrics(industry_market.service.get("2330.TWSE"))["pe"]
    assert pe["lower_is_better"] is True and pe["unit"] == "times"
    assert (pe["rank"], pe["compared"]) == (3, 4), "PE 20 after 10 and 15; the loss-making 5274 is not compared"
    assert pe["percentile"] == pytest.approx(37.5), "100 - 62.5, so a better rank has a higher percentile"
    assert pe["period_mixed"] is True, "TWSE and TPEx valuation dates differ within the industry"
    cheapest = _industry_metrics(industry_market.service.get("2303.TWSE"))["pe"]
    assert cheapest["rank"] == 1 and cheapest["percentile"] == pytest.approx(87.5)
    loss = _industry_metrics(industry_market.service.get("5274.TPEX"))["pe"]
    assert loss["status"] == "not_ranked" and loss["reason"] == "loss_or_not_applicable" and loss["compared"] == 4
    assert _industry_metrics(industry_market.service.get("1101.TWSE"))["pe"]["period_mixed"] is False


def test_trust_strength_zeros_tie_at_a_shared_rank(industry_market):
    trust = {s: _industry_metrics(industry_market.service.get(s))["trust_flow_20d"]
             for s in ("2330.TWSE", "2303.TWSE", "2454.TWSE", "6488.TPEX", "5274.TPEX")}
    assert trust["5274.TPEX"]["value"] == pytest.approx(2.0) and trust["5274.TPEX"]["rank"] == 1
    assert trust["5274.TPEX"]["percentile"] == pytest.approx(90.0)
    for symbol in ("2330.TWSE", "2303.TWSE", "2454.TWSE", "6488.TPEX"):
        assert trust[symbol]["value"] == 0.0 and trust[symbol]["rank"] == 2 and trust[symbol]["compared"] == 5
        assert trust[symbol]["percentile"] == pytest.approx(40.0), "a zero is a ranked value, not missing"


def test_twenty_day_return_counts_trading_sessions_and_adjusts_for_corporate_actions(industry_market):
    ret = {s: _industry_metrics(industry_market.service.get(s))["return_20d"]["value"]
           for s in ("2303.TWSE", "6488.TPEX", "5274.TPEX")}
    assert ret["2303.TWSE"] == pytest.approx(20.0), "the close 20 sessions back, though 2303 skipped two sessions"
    assert ret["6488.TPEX"] == pytest.approx(0.0), "50 before a 50 -> 40 ex-dividend is 40 on today's scale"
    assert ret["5274.TPEX"] == pytest.approx(30.0)


def test_fewer_than_three_rankable_peers_get_no_rank(industry_market):
    pair = industry_market.service.get("1101.TWSE").industry
    assert pair.members == 2
    for metric in pair.metrics:
        assert metric.status == "not_ranked" and metric.reason == "insufficient_peers" and metric.compared == 2
        assert metric.rank is None and metric.value is not None
    alone = industry_market.service.get("2603.TWSE").industry
    assert alone.applicable and alone.members == 1 and alone.peers == []
    assert {(m.reason, m.compared) for m in alone.metrics} == {("insufficient_peers", 1)}


@pytest.mark.parametrize("symbol", ["1234.TWSE", "4444.TWSE"])
def test_unclassified_and_unresolved_industries_are_not_applicable(industry_market, symbol):
    response = industry_market.service.get(symbol)
    assert response.applicable is True and response.dimensions
    assert response.industry.applicable is False and response.industry.not_applicable_reason == "unclassified"
    assert response.industry.metrics == [] and response.industry.peers == []
    ranks = industry_market.service.snapshot().industry_ranks
    assert not any(ranks[m]["symbol"].is_in([symbol]).any() for m in positioning.INDUSTRY_METRICS)


def test_a_tdr_gets_no_industry_section(industry_market):
    response = industry_market.service.get("9103.TWSE")
    assert response.not_applicable_reason == "tdr" and response.industry is None
    assert "9103.TWSE" not in industry_market.service.snapshot().industries


def test_top_three_peers_by_twenty_day_return_leave_out_the_stock_itself(industry_market):
    industry = industry_market.service.get("2330.TWSE").industry
    assert industry.peers_metric == "return_20d"
    assert [(p.symbol, p.name) for p in industry.peers] == [
        ("5274.TPEX", "name-5274"), ("2303.TWSE", "name-2303"), ("2454.TWSE", "name-2454"),
    ]
    assert [p.value for p in industry.peers] == pytest.approx([30.0, 20.0, 10.0])
    top = [p.symbol for p in industry_market.service.get("5274.TPEX").industry.peers]
    assert top == ["2303.TWSE", "2330.TWSE", "2454.TWSE"], "a tie falls back to symbol order"


def test_an_industry_change_rebuilds_the_cross_section_without_new_requests(industry_market, monkeypatch):
    service, lists = industry_market.service, industry_market.lists
    builds: list[int] = []
    original = TaiwanPositioningService.build_snapshot

    def counting(self, *args):
        builds.append(1)
        return original(self, *args)

    monkeypatch.setattr(TaiwanPositioningService, "build_snapshot", counting)
    before = sorted(p for p in industry_market.data_dir.rglob("*") if p.is_file())
    assert service.get("2330.TWSE").industry.members == 5
    service.get("1101.TWSE")
    assert len(builds) == 1 and len(lists.requests) == 2, "two valuation lists per data date"

    industry_market.master.get_instrument("2603.TWSE").industry = SEMICONDUCTOR
    response = service.get("2330.TWSE")
    assert len(builds) == 2 and response.industry.members == 6
    assert _industry_metrics(response)["pe"]["compared"] == 5
    assert len(lists.requests) == 2
    service.get("2330.TWSE")
    assert len(builds) == 2
    assert sorted(p for p in industry_market.data_dir.rglob("*") if p.is_file()) == before, "nothing is written"


def test_api_returns_the_industry_section(market, monkeypatch, client):
    monkeypatch.setattr("app.api.taiwan.get_taiwan_positioning_service", lambda: _service(market, _Lists()))
    industry = client.get(URL.format("2330.TWSE")).json()["industry"]
    assert industry["applicable"] is True and industry["industry"] == SEMICONDUCTOR
    assert [m["id"] for m in industry["metrics"]] == ["return_20d", "foreign_flow_20d", "trust_flow_20d", "pe"]
    assert industry["peers_metric"] == "return_20d"
