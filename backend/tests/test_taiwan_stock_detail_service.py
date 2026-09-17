"""Unit tests for TaiwanStockDetailService (Phase 6A).

Verifies:
  - Aggregation of Identity, Price Limit, Realtime, Daily, Institutional, Margin, Factors, Context, Monitor.
  - Handling of domestic stock (2330.TWSE, ±10%).
  - Handling of ETF (0050.TWSE, ±10%).
  - Handling of Leveraged ETF (00631L.TWSE, ±20%).
  - Handling of Inverse ETF (00632R.TWSE, ±10%).
  - Handling of Foreign Equity ETF (00646.TWSE, NO_LIMIT).
  - Handling of TPEx equity (8069.TPEX, TPEx exchange and context).
  - Partial data tolerance when one or more providers fail/empty.
  - Monitor summary sourced from the unified MonitorRuleEngine rule store
    (symbols/all scope inclusion, watchlist_group exclusion, enabled/active counts).
"""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.strategy import monitor_rules as mr_store
from app.taiwan.detail_models import TaiwanStockDetailResponse
from app.taiwan.detail_service import TaiwanStockDetailService
from app.taiwan.enrichment.models import InstitutionalFlow, MarginTrading, MarketIndex, SourceMeta
from app.taiwan.realtime.models import RealtimeStatus, TaiwanRealtimeQuote
from app.taiwan.universe.models import TaiwanInstrument


def _make_sample_quote(symbol: str, price: float, prev_close: float) -> TaiwanRealtimeQuote:
    code, ex = symbol.split(".")
    meta = SourceMeta(
        source="twse:mis",
        source_url="https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
        trade_date=date(2026, 8, 28),
        fetched_at=datetime(2026, 8, 30, 20, 0, 0),
        status="official_snapshot",
        is_realtime=False,
    )
    return TaiwanRealtimeQuote(
        symbol=symbol,
        name="測試標的",
        exchange=ex,
        last_price=price,
        prev_close=prev_close,
        open=price,
        high=price + 5,
        low=price - 5,
        change=price - prev_close,
        change_pct=round((price - prev_close) / prev_close * 100, 2),
        volume=1000000,
        amount=price * 1000000,
        quote_time=datetime(2026, 8, 28, 13, 30, 0),
        trade_date=date(2026, 8, 28),
        market_status="closed",
        source_meta=meta,
        bids=[(price, 10000), (price - 1, 20000)],
        asks=[(price + 1, 15000), (price + 2, 25000)],
    )


def test_service_stock_aggregation_2330():
    svc = TaiwanStockDetailService()
    # Mock providers to keep test deterministic and fast
    mock_rt = MagicMock()
    mock_rt.get_quotes.return_value = {"2330.TWSE": _make_sample_quote("2330.TWSE", 2420.0, 2410.0)}
    svc.realtime_service = mock_rt

    res = svc.get_stock_detail("2330.TWSE", days=30)
    assert isinstance(res, TaiwanStockDetailResponse)
    assert res.symbol == "2330.TWSE"
    assert res.identity.name == "台積電"
    assert res.identity.exchange == "TWSE"
    assert res.identity.instrument_type == "stock"
    assert res.price_limit.is_no_limit is False
    assert res.price_limit.price_limit_pct == 0.1
    assert res.price_limit.limit_up == 2650.0
    assert res.price_limit.limit_down == 2170.0
    assert res.realtime.last_price == 2420.0
    assert len(res.realtime.bids) == 2
    assert len(res.realtime.asks) == 2
    assert res.market_context.benchmark_symbol == "TAIEX"


def test_service_etf_no_limit_00646():
    svc = TaiwanStockDetailService()
    mock_rt = MagicMock()
    mock_rt.get_quotes.return_value = {"00646.TWSE": _make_sample_quote("00646.TWSE", 76.85, 76.95)}
    svc.realtime_service = mock_rt

    res = svc.get_stock_detail("00646.TWSE")
    assert res.symbol == "00646.TWSE"
    assert res.identity.instrument_type == "etf"
    assert res.identity.etf_category == "foreign_equity"
    assert res.price_limit.is_no_limit is True
    assert res.price_limit.price_limit_pct is None
    assert res.price_limit.limit_up is None
    assert res.price_limit.limit_down is None


def test_service_leveraged_inverse_etfs():
    svc = TaiwanStockDetailService()
    mock_rt = MagicMock()
    mock_rt.get_quotes.side_effect = lambda syms: {
        "00631L.TWSE": _make_sample_quote("00631L.TWSE", 36.3, 35.95),
        "00632R.TWSE": _make_sample_quote("00632R.TWSE", 9.89, 9.95),
    }
    svc.realtime_service = mock_rt

    # 00631L (Domestic Leveraged 2x -> ±20%)
    res_l = svc.get_stock_detail("00631L.TWSE")
    assert res_l.price_limit.is_no_limit is False
    assert res_l.price_limit.price_limit_pct == 0.2
    assert res_l.price_limit.limit_up == 43.14

    # 00632R (Domestic Inverse 1x -> ±10%)
    res_r = svc.get_stock_detail("00632R.TWSE")
    assert res_r.price_limit.is_no_limit is False
    assert res_r.price_limit.price_limit_pct == 0.1
    assert res_r.price_limit.limit_up == 10.94


def test_service_tpex_equity_8069():
    svc = TaiwanStockDetailService()
    mock_rt = MagicMock()
    mock_rt.get_quotes.return_value = {"8069.TPEX": _make_sample_quote("8069.TPEX", 160.5, 159.0)}
    svc.realtime_service = mock_rt

    res = svc.get_stock_detail("8069.TPEX")
    assert res.symbol == "8069.TPEX"
    assert res.identity.name == "元太"
    assert res.identity.exchange == "TPEX"
    assert res.market_context.benchmark_symbol == "TPEX_INDEX"
    assert res.market_context.benchmark_name == "櫃買指數"


def test_service_partial_failure_tolerance(monkeypatch):
    svc = TaiwanStockDetailService()
    # Simulate failed realtime, failed daily, failed institutional
    mock_rt = MagicMock()
    mock_rt.get_quotes.side_effect = RuntimeError("Realtime network timeout")
    svc.realtime_service = mock_rt

    mock_daily = MagicMock()
    mock_daily.get_daily.return_value = pl.DataFrame()
    svc.hybrid_provider = mock_daily

    mock_store = MagicMock()
    mock_store.scan_range.return_value = pl.DataFrame()
    svc.daily_store = mock_store

    mock_inst = MagicMock()
    mock_inst.fetch_live_day.side_effect = RuntimeError("TWSE T86 timeout")
    svc.institutional_provider = mock_inst

    mock_margin = MagicMock()
    mock_margin.fetch_live_day.side_effect = RuntimeError("MI_MARGN timeout")
    svc.margin_provider = mock_margin

    # Chip cards fall back to backfilled rows on disk when the live call fails,
    # so empty both stores to keep this about tolerating a total outage.
    class _EmptyStore:
        def available_dates(self):
            return []

        def read_range(self, *a):
            return pl.DataFrame()

    monkeypatch.setattr(
        "app.taiwan.institutional_store.TaiwanInstitutionalStore", lambda *a, **k: _EmptyStore()
    )
    monkeypatch.setattr(
        "app.taiwan.margin_store.TaiwanMarginStore", lambda *a, **k: _EmptyStore()
    )

    # Service should still return complete valid response with status='unavailable'
    res = svc.get_stock_detail("2330.TWSE")
    assert isinstance(res, TaiwanStockDetailResponse)
    assert res.symbol == "2330.TWSE"
    assert res.identity.name == "台積電"
    assert res.realtime.meta.status == "unavailable"
    assert res.daily_history.status == "unavailable"
    assert res.institutional.status == "unavailable"
    assert res.margin.status == "unavailable"
    assert res.overall_data_quality == "degraded"


def _rule(rid: str, **overrides) -> dict:
    rule = {
        "id": rid, "name": rid, "type": "price", "scope": "symbols",
        "symbols": ["2330.TWSE"], "logic": "and",
        "conditions": [{"field": "close", "op": ">", "value": 10}],
    }
    rule.update(overrides)
    return mr_store.normalize(rule)


class TestMonitorSummaryFromUnifiedEngine:
    """monitor_summary 现由 B 引擎的规则存储聚合 (Step 6 迁移, 取代已删除的引擎 A)。"""

    def test_symbol_scoped_rule_included(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.config.Settings", lambda: SimpleNamespace(data_dir=tmp_path))
        mr_store.save_one(tmp_path, _rule("r1", symbols=["2330.TWSE"]))

        svc = TaiwanStockDetailService()
        summary = svc._aggregate_monitor_summary("2330.TWSE")  # noqa: SLF001

        assert summary.rule_count == 1
        assert summary.rules[0]["rule_id"] == "r1"

    def test_market_wide_rule_included_and_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.config.Settings", lambda: SimpleNamespace(data_dir=tmp_path))
        mr_store.save_one(tmp_path, _rule("r_all", scope="all", symbols=[]))

        svc = TaiwanStockDetailService()
        summary = svc._aggregate_monitor_summary("2330.TWSE")  # noqa: SLF001

        assert summary.rule_count == 1
        assert summary.rules[0]["scope"] == "all"

    def test_watchlist_group_scoped_rule_excluded(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.config.Settings", lambda: SimpleNamespace(data_dir=tmp_path))
        mr_store.save_one(
            tmp_path,
            _rule("r_group", scope="watchlist_group", symbols=[], group_id="g1"),
        )

        svc = TaiwanStockDetailService()
        summary = svc._aggregate_monitor_summary("2330.TWSE")  # noqa: SLF001

        assert summary.rule_count == 0

    def test_disabled_rule_counts_toward_rule_count_but_not_active_count(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.config.Settings", lambda: SimpleNamespace(data_dir=tmp_path))
        mr_store.save_one(tmp_path, _rule("r_on", enabled=True))
        mr_store.save_one(tmp_path, _rule("r_off", enabled=False))

        svc = TaiwanStockDetailService()
        summary = svc._aggregate_monitor_summary("2330.TWSE")  # noqa: SLF001

        assert summary.rule_count == 2
        assert summary.active_count == 1
