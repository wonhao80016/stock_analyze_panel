"""台股行情回退闸门 (A1) + 监控标的并入轮询池 (A3) + change_pct 单位修正 (B)。

背景: TaiwanHybridProvider.get_realtime() 在即时报价为空时会回退到
official.fetch_quote / get_daily (status != "realtime"); 这些数据此前未经
过滤就进入监控评估, 等于用官方回退/昨收价发出"即时"告警。另外该 provider
的 change_pct 是百分数 (mis_provider.py 的 ×100), 而 enriched 管线是小数制,
此前未做单位转换直接进入规则判定 (100 倍误差)。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from app.services.quote_service import QuoteService


# ── A1: 回退来源闸门 ──────────────────────────────────────

def test_records_realtime_status_all_realtime():
    records = [{"symbol": "2330.TWSE", "status": "realtime"}, {"symbol": "8069.TPEX", "status": "realtime"}]
    assert QuoteService._records_realtime_status(records) is True


@pytest.mark.parametrize("status", ["third_party_fallback", "official", "stale"])
def test_records_realtime_status_fallback(status):
    records = [{"symbol": "2330.TWSE", "status": status}]
    assert QuoteService._records_realtime_status(records) is False


def test_records_realtime_status_mixed_is_not_realtime():
    records = [{"symbol": "2330.TWSE", "status": "realtime"}, {"symbol": "8069.TPEX", "status": "official"}]
    assert QuoteService._records_realtime_status(records) is False


def test_records_realtime_status_no_status_key_is_none():
    """非台股 provider (TickFlow SDK) 的 records 不带 status, 门槛不适用。"""
    records = [{"symbol": "600000.SH", "close": 10.0}]
    assert QuoteService._records_realtime_status(records) is None


def _svc_for_evaluate() -> QuoteService:
    svc = QuoteService.__new__(QuoteService)
    svc._repo = MagicMock()
    svc._app_state = MagicMock()
    engine = MagicMock()
    engine.rule_count = 1
    svc._app_state.monitor_engine = engine
    return svc


def test_evaluate_monitors_skips_when_fallback_source():
    """回退来源 (status!=realtime) 的整轮 -> 规则零触发, 引擎完全不被调用。"""
    svc = _svc_for_evaluate()
    svc._quote_source_realtime = False

    with patch.object(QuoteService, "_is_continuous_trading", return_value=True):
        svc._evaluate_monitors(pl.DataFrame(), None)

    svc._app_state.monitor_engine.evaluate.assert_not_called()


def test_evaluate_monitors_runs_when_realtime_source():
    """即时来源 (status==realtime) -> 闸门不拦截, 照常评估 (闸门未过度封锁)。"""
    from datetime import date

    svc = _svc_for_evaluate()
    svc._quote_source_realtime = True

    engine = svc._app_state.monitor_engine
    engine.has_rule_type.return_value = False
    engine.has_asset_rules.return_value = False
    engine.evaluate.return_value = []
    engine.consume_strategy_result_updates.return_value = False

    enriched = pl.DataFrame({"symbol": ["2330.TWSE"], "close": [500.0]})
    with (
        patch.object(QuoteService, "_is_continuous_trading", return_value=True),
        patch.object(QuoteService, "get_enriched_today", return_value=(enriched, date(2026, 9, 17))),
        patch.object(QuoteService, "_inject_intraday_signals", side_effect=lambda df, e, at: df),
        patch("app.services.quote_service.cn_today", return_value=date(2026, 9, 17)),
    ):
        svc._evaluate_monitors(enriched, None)

    engine.evaluate.assert_called_once()


def test_evaluate_monitors_gate_is_noop_when_no_status_signal():
    """非台股 provider (无 status 信号, None) -> 闸门不适用, 行为不变。"""
    from datetime import date

    svc = _svc_for_evaluate()
    svc._quote_source_realtime = None

    engine = svc._app_state.monitor_engine
    engine.has_rule_type.return_value = False
    engine.has_asset_rules.return_value = False
    engine.evaluate.return_value = []
    engine.consume_strategy_result_updates.return_value = False

    enriched = pl.DataFrame({"symbol": ["600000.SH"], "close": [10.0]})
    with (
        patch.object(QuoteService, "_is_continuous_trading", return_value=True),
        patch.object(QuoteService, "get_enriched_today", return_value=(enriched, date(2026, 9, 17))),
        patch.object(QuoteService, "_inject_intraday_signals", side_effect=lambda df, e, at: df),
        patch("app.services.quote_service.cn_today", return_value=date(2026, 9, 17)),
    ):
        svc._evaluate_monitors(enriched, None)

    engine.evaluate.assert_called_once()


# ── A3: 监控标的自动并入轮询池 ────────────────────────────

def _svc_for_fetch() -> QuoteService:
    svc = QuoteService.__new__(QuoteService)
    svc._app_state = MagicMock()
    svc._repo = MagicMock()
    return svc


def test_monitor_rule_symbol_outside_watchlist_is_polled():
    """规则绑定了不在自选清单的 symbol -> 该 symbol 出现在 get_realtime 的请求清单中。"""
    svc = _svc_for_fetch()
    svc._app_state.monitor_engine.rules = {
        "r1": {"enabled": True, "scope": "symbols", "symbols": ["9999.TWSE"]},
    }

    captured: dict = {}

    class _FakeProvider:
        def get_realtime(self, symbols):
            captured["symbols"] = list(symbols)
            return pl.DataFrame()

    with (
        patch("app.services.preferences.get_realtime_data_provider", return_value="taiwan"),
        patch("app.data_providers.registry.get_provider", return_value=_FakeProvider()),
        patch("app.services.watchlist.list_symbols", return_value=[{"symbol": "2330.TWSE"}]),
    ):
        svc._fetch_full_market_quotes()

    assert "2330.TWSE" in captured["symbols"]
    assert "9999.TWSE" in captured["symbols"]


def test_monitor_rule_symbols_deduped_against_watchlist():
    svc = _svc_for_fetch()
    svc._app_state.monitor_engine.rules = {
        "r1": {"enabled": True, "scope": "symbols", "symbols": ["2330.TWSE"]},
    }

    captured: dict = {}

    class _FakeProvider:
        def get_realtime(self, symbols):
            captured["symbols"] = list(symbols)
            return pl.DataFrame()

    with (
        patch("app.services.preferences.get_realtime_data_provider", return_value="taiwan"),
        patch("app.data_providers.registry.get_provider", return_value=_FakeProvider()),
        patch("app.services.watchlist.list_symbols", return_value=[{"symbol": "2330.TWSE"}]),
    ):
        svc._fetch_full_market_quotes()

    assert captured["symbols"].count("2330.TWSE") == 1


def test_symbol_pool_capped_to_protect_mis_endpoint():
    """MIS 端点单次 URL 查询无内建分批, 规则数量爆增时截断而非无上限请求。"""
    svc = _svc_for_fetch()
    many_symbols = [f"{1000 + i}.TWSE" for i in range(QuoteService.TAIWAN_REALTIME_MAX_SYMBOLS + 50)]
    svc._app_state.monitor_engine.rules = {
        "r1": {"enabled": True, "scope": "symbols", "symbols": many_symbols},
    }

    captured: dict = {}

    class _FakeProvider:
        def get_realtime(self, symbols):
            captured["symbols"] = list(symbols)
            return pl.DataFrame()

    with (
        patch("app.services.preferences.get_realtime_data_provider", return_value="taiwan"),
        patch("app.data_providers.registry.get_provider", return_value=_FakeProvider()),
        patch("app.services.watchlist.list_symbols", return_value=[]),
    ):
        svc._fetch_full_market_quotes()

    assert len(captured["symbols"]) <= QuoteService.TAIWAN_REALTIME_MAX_SYMBOLS


def test_disabled_monitor_rule_symbol_not_polled():
    svc = _svc_for_fetch()
    svc._app_state.monitor_engine.rules = {
        "r1": {"enabled": False, "scope": "symbols", "symbols": ["9999.TWSE"]},
    }

    captured: dict = {}

    class _FakeProvider:
        def get_realtime(self, symbols):
            captured["symbols"] = list(symbols)
            return pl.DataFrame()

    with (
        patch("app.services.preferences.get_realtime_data_provider", return_value="taiwan"),
        patch("app.data_providers.registry.get_provider", return_value=_FakeProvider()),
        patch("app.services.watchlist.list_symbols", return_value=[{"symbol": "2330.TWSE"}]),
    ):
        svc._fetch_full_market_quotes()

    assert "9999.TWSE" not in captured["symbols"]


# ── B: change_pct 单位修正 (百分数 -> 小数) ───────────────

def test_taiwan_realtime_branch_normalizes_change_pct_to_decimal():
    """mis_provider 的 change_pct 是百分数 (1.30=1.30%); 进入 quote_extra 前须转小数。"""
    svc = _svc_for_fetch()
    svc._app_state.monitor_engine = None
    svc._repo.get_index_symbol_set.return_value = set()
    svc._repo.get_etf_instruments.return_value = pl.DataFrame()

    captured_records: list[dict] = []

    class _FakeProvider:
        def get_realtime(self, symbols):
            return pl.DataFrame([{
                "symbol": "3545.TWSE", "date": "2026-09-17", "close": 50.50,
                "prev_close": 49.85, "volume": 1000.0, "amount": None,
                "change_pct": 1.3039, "status": "realtime",
            }])

    def _capture(records, **kwargs):
        captured_records.extend(records)

    with (
        patch("app.services.preferences.get_realtime_data_provider", return_value="taiwan"),
        patch("app.data_providers.registry.get_provider", return_value=_FakeProvider()),
        patch("app.services.watchlist.list_symbols", return_value=[{"symbol": "3545.TWSE"}]),
        patch.object(QuoteService, "_record_minute_bars"),
        patch.object(QuoteService, "_process_full_market_records", side_effect=_capture),
    ):
        svc._fetch_full_market_quotes()

    assert captured_records[0]["change_pct"] == pytest.approx(0.013039)


def test_build_quote_extra_change_pct_unaffected_by_turnover_normalization():
    """change_pct 由调用方 (taiwan 分支) 预先转换; _build_quote_extra 本身不重复转换,
    否则会把 TickFlow 分支既有的小数值再次除以 100 (双重错误)。"""
    out = QuoteService._build_quote_extra([{"symbol": "600000.SH", "change_pct": 0.0366}])
    assert out["change_pct"][0] == pytest.approx(0.0366)
