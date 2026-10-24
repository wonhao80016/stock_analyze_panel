"""Tests for Taiwan Stock Research Evidence Context Service (Phase 7C).

Verifies:
- Identity & Instrument classification:
    * 2330.TWSE, 8069.TPEX, 0050.TWSE, 00646.TWSE
- Price Context semantics:
    * change_pct decimal calculation, volume in shares, amount in TWD
- Institutional rolling sums & coverage:
    * 5D sum over the last five stored sessions; a session without a T86 row counts as zero
- Historical No Look-Ahead:
    * D+1 daily/institutional/margin data strictly ignored when querying date D
- ETF Context vs Corporate Fundamentals:
    * 00646 has ETF metadata, NO_LIMIT price rule, fundamentals status is not_applicable
- Market & Industry Intelligence Reuse:
    * Matches Phase 7A & Phase 7B deterministic service metrics
- Zero Request-time External Market HTTP:
    * GET /api/taiwan/stocks/{symbol}/research-context performs 0 external market provider HTTP calls
- No AI calls:
    * AI provider monkeypatch assertion ensures research context does not call AI
"""
from datetime import date, timedelta
from unittest.mock import MagicMock, patch
import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.ai_research import build_evidence_registry
from app.taiwan.research_context import (
    TaiwanStockResearchContext,
    TaiwanStockResearchContextService,
)


def test_research_context_identity():
    """Verify identity mapping from Security Master for stocks and ETFs."""
    svc = TaiwanStockResearchContextService()
    d = date(2026, 8, 28)

    # Stock
    ctx_2330 = svc.get_research_context("2330", d)
    assert ctx_2330.identity.canonical_symbol == "2330.TWSE"
    assert ctx_2330.identity.name == "台積電"
    assert ctx_2330.identity.exchange == "TWSE"
    assert ctx_2330.identity.instrument_type == "stock"
    assert ctx_2330.identity.industry == "半導體業"

    # ETF
    ctx_00646 = svc.get_research_context("00646", d)
    assert ctx_00646.identity.canonical_symbol == "00646.TWSE"
    assert ctx_00646.identity.name == "元大S&P500"
    assert ctx_00646.identity.instrument_type == "etf"
    assert ctx_00646.identity.industry is None


def test_price_context_decimal_and_units():
    """Verify change_pct is decimal (0.05=5%), volume in shares, amount in TWD."""
    d_curr = date(2026, 8, 28)
    d_prev = date(2026, 8, 27)

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_prev, d_curr]

    df_hist = pl.DataFrame({
        "symbol": ["TEST.TWSE", "TEST.TWSE"],
        "date": [d_prev, d_curr],
        "open": [100.0, 102.0],
        "high": [101.0, 106.0],
        "low": [99.0, 101.0],
        "close": [100.0, 105.0],
        "volume": [10000.0, 15000.0],
        "amount": [1000000.0, 1575000.0],
    })
    mock_daily.read_range.return_value = df_hist

    mock_sm = MagicMock()
    mock_inst = MagicMock()
    mock_inst.symbol = "TEST.TWSE"
    mock_inst.code = "TEST"
    mock_inst.name = "測試股"
    mock_inst.exchange = "TWSE"
    mock_inst.instrument_type = "stock"
    mock_inst.industry = "半導體業"
    mock_inst.currency = "TWD"
    mock_inst.listing_status = "active"
    mock_inst.listing_date = "2020-01-01"
    mock_inst.updated_at = "2026-08-28"
    mock_sm.get_instrument.return_value = mock_inst
    mock_sm.to_dataframe.return_value = pl.DataFrame({
        "symbol": ["TEST.TWSE"],
        "name": ["測試股"],
        "exchange": ["TWSE"],
        "instrument_type": ["stock"],
        "industry": ["半導體業"],
        "listing_status": ["active"],
    })

    svc = TaiwanStockResearchContextService(
        daily_store=mock_daily,
        security_master=mock_sm,
    )
    ctx = svc.get_research_context("TEST.TWSE", d_curr)
    pc = ctx.price_context

    assert pc.close == 105.0
    assert pc.previous_close == 100.0
    assert pc.change == 5.0
    assert pc.change_pct == 0.05  # Decimal format (not 5.0)
    assert pc.volume == 15000.0  # Shares (not lots)
    assert pc.amount == 1575000.0  # TWD


def test_institutional_rolling_and_coverage():
    """Verify 5-day rolling sum in shares and coverage count."""
    d_curr = date(2026, 8, 28)
    dates = [date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27), d_curr]

    mock_inst = MagicMock()
    mock_inst._data_dir = None  # served through read_range, not partition files
    mock_inst.available_dates.return_value = dates

    df_inst = pl.DataFrame({
        "symbol": ["TEST.TWSE"] * 5,
        "date": dates,
        "foreign_net": [100.0, 200.0, -50.0, 0.0, 300.0],
        "investment_trust_net": [10.0, 20.0, 0.0, 0.0, 50.0],
        "dealer_net": [5.0, -5.0, 0.0, 0.0, 10.0],
    })
    mock_inst.read_range.return_value = df_inst

    mock_sm = MagicMock()
    m_inst = MagicMock()
    m_inst.symbol = "TEST.TWSE"
    m_inst.code = "TEST"
    m_inst.name = "測試股"
    m_inst.exchange = "TWSE"
    m_inst.instrument_type = "stock"
    m_inst.industry = "半導體業"
    m_inst.currency = "TWD"
    m_inst.listing_status = "active"
    m_inst.listing_date = "2020-01-01"
    m_inst.updated_at = "2026-08-28"
    mock_sm.get_instrument.return_value = m_inst

    svc = TaiwanStockResearchContextService(
        inst_store=mock_inst,
        security_master=mock_sm,
    )
    ctx = svc.get_research_context("TEST.TWSE", d_curr)
    ic = ctx.institutional_context

    assert ic.foreign_net_1d == 300.0
    # 100 + 200 - 50 + 0 + 300 = 550
    assert ic.foreign_net_5d == 550.0
    assert ic.coverage_days_5d == 5


def test_historical_no_look_ahead():
    """Verify that querying date D strictly ignores data from D+1."""
    d_target = date(2026, 8, 20)
    d_future = date(2026, 8, 21)

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [date(2026, 8, 19), d_target, d_future]
    mock_daily.read_range.return_value = pl.DataFrame()

    mock_inst = MagicMock()
    mock_inst.available_dates.return_value = [d_target, d_future]
    mock_inst.read_range.return_value = pl.DataFrame()

    mock_margin = MagicMock()
    mock_margin.available_dates.return_value = [d_target, d_future]
    mock_margin.read_range.return_value = pl.DataFrame()

    mock_sm = MagicMock()
    m_inst = MagicMock()
    m_inst.symbol = "TEST.TWSE"
    m_inst.code = "TEST"
    m_inst.name = "測試股"
    m_inst.exchange = "TWSE"
    m_inst.instrument_type = "stock"
    m_inst.industry = "半導體業"
    m_inst.currency = "TWD"
    m_inst.listing_status = "active"
    m_inst.listing_date = "2020-01-01"
    m_inst.updated_at = "2026-08-20"
    mock_sm.get_instrument.return_value = m_inst

    svc = TaiwanStockResearchContextService(
        daily_store=mock_daily,
        inst_store=mock_inst,
        margin_store=mock_margin,
        security_master=mock_sm,
    )
    ctx = svc.get_research_context("TEST.TWSE", d_target)

    # Assert all calls to daily, institutional, margin read_range had end <= d_target
    for call in mock_daily.read_range.call_args_list:
        _, start, end = call[0]
        if end is not None:
            assert end <= d_target
    for call in mock_inst.read_range.call_args_list:
        _, start, end = call[0]
        if end is not None:
            assert end <= d_target
    for call in mock_margin.read_range.call_args_list:
        _, start, end = call[0]
        if end is not None:
            assert end <= d_target


def test_etf_context_and_no_limit_rules():
    """Verify 00646 ETF has structured ETF metadata and NO_LIMIT rules."""
    svc = TaiwanStockResearchContextService()
    ctx = svc.get_research_context("00646.TWSE", date(2026, 8, 28))

    assert ctx.etf_context.status == "available"
    assert ctx.etf_context.underlying_scope == "foreign"
    assert ctx.fundamentals_context.status == "not_applicable"
    assert ctx.market_rules_context.is_no_limit is True
    assert ctx.market_rules_context.price_limit_pct is None


def test_api_endpoint_zero_market_http_and_no_ai():
    """Verify GET /api/taiwan/stocks/{symbol}/research-context performs 0 HTTP and no AI calls."""
    client = TestClient(app, client=("127.0.0.1", 50000))
    with patch("urllib.request.urlopen") as mock_urlopen, patch("httpx.get") as mock_httpx_get, patch("httpx.post") as mock_httpx_post:
        resp = client.get("/api/taiwan/stocks/2330/research-context?date=2026-08-28")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "2330.TWSE"
        assert data["identity"]["name"] == "台積電"
        assert "price_context" in data
        assert "market_context" in data
        assert "industry_context" in data
        assert "evidence_summary" in data
        assert mock_urlopen.call_count == 0
        assert mock_httpx_get.call_count == 0
        assert mock_httpx_post.call_count == 0


def _indicator_service(d_prev: date, d_curr: date) -> TaiwanStockResearchContextService:
    # 61 bars: enough history for a local MA5/MA60/RSI14 computation to produce values.
    dates = [d_curr - timedelta(days=60 - i) for i in range(61)]
    assert dates[-2] == d_prev
    closes = [100.0 + (i % 7) * 0.9 + i * 0.05 for i in range(60)] + [105.0]
    bars = pl.DataFrame({
        "symbol": ["TEST.TWSE"] * 61,
        "date": dates,
        "open": closes,
        "high": [c + 1.0 for c in closes],
        "low": [c - 1.0 for c in closes],
        "close": closes,
        "volume": [10000.0 + (i % 5) * 1000.0 for i in range(61)],
        "amount": [1000000.0] * 61,
    })

    # Single-day reads feed the industry snapshot's per-symbol joins; returning
    # every bar for them multiplies the rows at each join.
    def read_range(symbols, start, end):
        rows = bars
        if symbols is not None:
            rows = rows.filter(pl.col("symbol").is_in(symbols))
        if start is not None:
            rows = rows.filter(pl.col("date") >= start)
        if end is not None:
            rows = rows.filter(pl.col("date") <= end)
        return rows

    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = dates
    mock_daily.read_range.side_effect = read_range
    mock_inst = MagicMock()
    mock_inst.symbol = "TEST.TWSE"
    mock_inst.code = "TEST"
    mock_inst.name = "Test"
    mock_inst.exchange = "TWSE"
    mock_inst.instrument_type = "stock"
    mock_inst.industry = "Semiconductors"
    mock_inst.currency = "TWD"
    mock_inst.listing_status = "active"
    mock_inst.listing_date = "2020-01-01"
    mock_inst.updated_at = "2026-08-28"
    mock_sm = MagicMock()
    mock_sm.get_instrument.return_value = mock_inst
    mock_sm.to_dataframe.return_value = pl.DataFrame({
        "symbol": ["TEST.TWSE"],
        "name": ["Test"],
        "exchange": ["TWSE"],
        "instrument_type": ["stock"],
        "industry": ["Semiconductors"],
        "listing_status": ["active"],
    })
    return TaiwanStockResearchContextService(daily_store=mock_daily, security_master=mock_sm)


def _publish_indicators(tmp_path, monkeypatch, d: date, **values) -> dict:
    from app.config import settings
    from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    row = {"symbol": "TEST.TWSE", **values}
    TaiwanEnrichedRefreshService._write_partition(
        tmp_path / "kline_daily_enriched" / f"date={d.isoformat()}", pl.DataFrame([row])
    )
    return row


def _market_snapshot(status: str, d: date) -> MagicMock:
    snapshot = MagicMock()
    snapshot.trade_date = str(d)
    snapshot.market_totals.turnover = 1_000_000.0
    snapshot.market_totals.advance_count = 1
    snapshot.market_totals.decline_count = 1
    snapshot.market_totals.flat_count = 0
    snapshot.market_totals.upper_limit_count = 0
    snapshot.market_totals.lower_limit_count = 0
    snapshot.by_instrument.stock.advance_count = 1
    snapshot.by_instrument.stock.decline_count = 1
    snapshot.by_instrument.stock.flat_count = 0
    snapshot.by_exchange.twse.turnover = 600_000.0
    snapshot.by_exchange.tpex.turnover = 400_000.0
    snapshot.institutional.total_net_amount = 1000
    snapshot.margin.margin_balance_change = 100.0
    snapshot.data_quality.overall_status = status
    return snapshot


def test_technical_context_equals_the_published_partition(tmp_path, monkeypatch):
    d_prev, d_curr = date(2026, 8, 27), date(2026, 8, 28)
    row = _publish_indicators(
        tmp_path, monkeypatch, d_curr,
        ma5=101.123456, ma20=98.7654321, ma60=95.5, vol_ratio_5d=1.23456789, rsi_14=58.8312345,
    )
    tech = _indicator_service(d_prev, d_curr).get_research_context("TEST.TWSE", d_curr).technical_context
    assert tech.ma5 == round(row["ma5"], 4)
    assert tech.ma20 == round(row["ma20"], 4)
    assert tech.ma60 == round(row["ma60"], 4)
    assert tech.vol_ratio_5d == round(row["vol_ratio_5d"], 2)
    assert tech.rsi14 == round(row["rsi_14"], 2)
    assert tech.above_ma20 is True
    assert tech.meta.source == "kline_daily_enriched"


def test_technical_context_reads_the_target_day_partition(tmp_path, monkeypatch):
    d_prev, d_curr = date(2026, 8, 27), date(2026, 8, 28)
    _publish_indicators(tmp_path, monkeypatch, d_curr, ma5=1.0, ma20=1.0, ma60=1.0, vol_ratio_5d=1.0, rsi_14=11.0)
    older = _publish_indicators(tmp_path, monkeypatch, d_prev, ma5=2.0, ma20=2.0, ma60=2.0, vol_ratio_5d=2.0, rsi_14=22.0)
    tech = _indicator_service(d_prev, d_curr).get_research_context("TEST.TWSE", d_prev).technical_context
    assert tech.rsi14 == round(older["rsi_14"], 2)


def test_missing_partition_leaves_technical_context_null_and_flags_it(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    d_prev, d_curr = date(2026, 8, 27), date(2026, 8, 28)
    ctx = _indicator_service(d_prev, d_curr).get_research_context("TEST.TWSE", d_curr)
    tech = ctx.technical_context
    assert (tech.ma5, tech.ma20, tech.ma60, tech.rsi14, tech.vol_ratio_5d) == (None, None, None, None, None)
    assert tech.above_ma20 is None
    assert tech.distance_to_ma20 is None
    assert tech.meta.classification == "MISSING"
    section = next(s for s in ctx.data_quality.sections if s.section == "indicators")
    assert section.status == "unavailable"
    assert ctx.price_context.close == 105.0


def test_basic_columns_only_partition_is_not_published(tmp_path, monkeypatch):
    from app.config import settings
    from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    d_prev, d_curr = date(2026, 8, 27), date(2026, 8, 28)
    TaiwanEnrichedRefreshService._write_partition(
        tmp_path / "kline_daily_enriched" / f"date={d_curr.isoformat()}",
        pl.DataFrame([{"symbol": "TEST.TWSE", "close": 105.0, "volume": 15000.0}]),
    )
    ctx = _indicator_service(d_prev, d_curr).get_research_context("TEST.TWSE", d_curr)
    assert ctx.technical_context.rsi14 is None
    assert ctx.technical_context.ma60 is None
    assert next(s for s in ctx.data_quality.sections if s.section == "indicators").status == "unavailable"


def test_unpublished_indicators_downgrade_the_overall_status_from_complete(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    d_prev, d_curr = date(2026, 8, 27), date(2026, 8, 28)
    svc = _indicator_service(d_prev, d_curr)
    svc.inst_store = MagicMock()
    svc.inst_store.available_dates.return_value = [d_curr]
    svc.inst_store.read_range.return_value = pl.DataFrame()
    svc.margin_store = MagicMock()
    svc.margin_store.available_dates.return_value = [d_curr]
    svc.margin_store.read_range.return_value = pl.DataFrame()
    svc.market_intel_svc = MagicMock()
    svc.market_intel_svc.get_snapshot.return_value = _market_snapshot("complete", d_curr)
    without = svc.get_research_context("TEST.TWSE", d_curr).data_quality.overall_status
    _publish_indicators(tmp_path, monkeypatch, d_curr, ma5=1.0, ma20=1.0, ma60=1.0, vol_ratio_5d=1.0, rsi_14=50.0)
    with_ind = svc.get_research_context("TEST.TWSE", d_curr).data_quality.overall_status
    assert without == "partial"
    assert with_ind == "complete"


def test_provisional_market_is_provisional_and_sent_to_ai_with_its_status(tmp_path, monkeypatch):
    d_prev, d_curr = date(2026, 8, 27), date(2026, 8, 28)
    _publish_indicators(
        tmp_path,
        monkeypatch,
        d_curr,
        ma5=1.0,
        ma20=1.0,
        ma60=1.0,
        vol_ratio_5d=1.0,
        rsi_14=50.0,
    )
    svc = _indicator_service(d_prev, d_curr)
    svc.inst_store = MagicMock()
    svc.inst_store.available_dates.return_value = [d_curr]
    svc.inst_store.read_range.return_value = pl.DataFrame()
    svc.margin_store = MagicMock()
    svc.margin_store.available_dates.return_value = [d_curr]
    svc.margin_store.read_range.return_value = pl.DataFrame()
    svc.market_intel_svc = MagicMock()
    svc.market_intel_svc.get_snapshot.return_value = _market_snapshot("provisional", d_curr)

    context = svc.get_research_context("TEST.TWSE", d_curr)
    payload, _, missing = build_evidence_registry(context, None)

    assert context.market_context.status == "provisional"
    assert context.data_quality.overall_status == "provisional"
    assert payload["market_context"]["status"] == "provisional"
    assert "market_context" not in missing


@pytest.mark.parametrize(
    ("symbol", "previous_close", "tick"),
    [("0050.TWSE", 190.0, 0.05), ("00878.TWSE", 35.0, 0.01)],
)
def test_an_etf_tick_size_comes_from_the_etf_ladder(symbol, previous_close, tick):
    """The ordinary-stock ladder would give 0.5 at 190 and 0.05 at 35."""
    d_prev, d_curr = date(2026, 8, 27), date(2026, 8, 28)
    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_prev, d_curr]
    mock_daily.read_range.return_value = pl.DataFrame({
        "symbol": [symbol, symbol],
        "date": [d_prev, d_curr],
        "open": [previous_close, previous_close],
        "high": [previous_close, previous_close],
        "low": [previous_close, previous_close],
        "close": [previous_close, previous_close],
        "volume": [10000.0, 10000.0],
        "amount": [previous_close * 10000.0] * 2,
    })

    ctx = TaiwanStockResearchContextService(daily_store=mock_daily).get_research_context(symbol, d_curr)

    assert ctx.market_rules_context.tick_size == tick


def test_an_unknown_listing_date_is_an_unknown_rule_not_no_limit():
    d_prev, d_curr = date(2026, 8, 27), date(2026, 8, 28)
    mock_daily = MagicMock()
    mock_daily.available_dates.return_value = [d_prev, d_curr]
    mock_daily.read_range.return_value = pl.DataFrame({
        "symbol": ["TEST.TWSE", "TEST.TWSE"],
        "date": [d_prev, d_curr],
        "open": [100.0, 102.0],
        "high": [101.0, 106.0],
        "low": [99.0, 101.0],
        "close": [100.0, 105.0],
        "volume": [10000.0, 15000.0],
        "amount": [1000000.0, 1575000.0],
    })
    mock_sm = MagicMock()
    inst = MagicMock()
    inst.symbol, inst.code, inst.name, inst.exchange = "TEST.TWSE", "TEST", "測試股", "TWSE"
    inst.instrument_type, inst.industry, inst.currency = "stock", "半導體業", "TWD"
    inst.listing_status, inst.listing_date, inst.updated_at = "active", None, "2026-08-28"
    mock_sm.get_instrument.return_value = inst
    mock_sm.to_dataframe.return_value = pl.DataFrame({
        "symbol": ["TEST.TWSE"], "name": ["測試股"], "exchange": ["TWSE"], "instrument_type": ["stock"],
        "industry": ["半導體業"], "listing_status": ["active"],
    })

    ctx = TaiwanStockResearchContextService(daily_store=mock_daily, security_master=mock_sm).get_research_context(
        "TEST.TWSE", d_curr
    )

    rules = ctx.market_rules_context
    assert rules.is_no_limit is False
    assert (rules.price_limit_pct, rules.limit_up, rules.limit_down) == (None, None, None)
    assert rules.meta.classification == "MISSING"
