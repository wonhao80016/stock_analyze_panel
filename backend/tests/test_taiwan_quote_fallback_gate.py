"""台股行情回退闸门 (A1) + 监控标的并入轮询池 (A3) + change_pct 单位修正 (B)。

背景: TaiwanHybridProvider.get_realtime() 在即时报价为空时会回退到
official.fetch_quote / get_daily (status != "realtime"); 这些数据此前未经
过滤就进入监控评估, 等于用官方回退/昨收价发出"即时"告警。另外该 provider
的 change_pct 是百分数 (mis_provider.py 的 ×100), 而 enriched 管线是小数制,
此前未做单位转换直接进入规则判定 (100 倍误差)。
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from app.market_time import cn_today
from app.services.quote_service import QuoteService
from tests.test_realtime_enriched_resume import _live_state, _previous_enriched

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


# ── Poll pool cap: rule symbols go before the watchlist ──


def test_monitor_rule_symbol_survives_truncation_when_watchlist_fills_the_cap():
    """A rule symbol dropped by the cap silently disables its rule."""
    svc = _svc_for_fetch()
    cap = QuoteService.TAIWAN_REALTIME_MAX_SYMBOLS
    watchlist_symbols = [f"{1000 + i}.TWSE" for i in range(cap)]
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
        patch("app.services.watchlist.list_symbols", return_value=[{"symbol": s} for s in watchlist_symbols]),
    ):
        svc._fetch_full_market_quotes()

    assert "9999.TWSE" in captured["symbols"]
    assert len(captured["symbols"]) == cap
    assert len(captured["symbols"]) == len(set(captured["symbols"]))


def test_monitor_rule_symbol_in_watchlist_appears_once_within_cap():
    svc = _svc_for_fetch()
    watchlist_symbols = [f"{2000 + i}.TWSE" for i in range(5)]
    svc._app_state.monitor_engine.rules = {
        "r1": {"enabled": True, "scope": "symbols", "symbols": [watchlist_symbols[0]]},
    }

    captured: dict = {}

    class _FakeProvider:
        def get_realtime(self, symbols):
            captured["symbols"] = list(symbols)
            return pl.DataFrame()

    with (
        patch("app.services.preferences.get_realtime_data_provider", return_value="taiwan"),
        patch("app.data_providers.registry.get_provider", return_value=_FakeProvider()),
        patch("app.services.watchlist.list_symbols", return_value=[{"symbol": s} for s in watchlist_symbols]),
    ):
        svc._fetch_full_market_quotes()

    assert sorted(captured["symbols"]) == sorted(watchlist_symbols)
    assert len(captured["symbols"]) == len(set(captured["symbols"]))


# ── A5: fetch_live_bar, today's bar for a symbol outside the poll pool ──


def test_fetch_live_bar_builds_bar_from_realtime_row():
    svc = _svc_for_fetch()
    today = cn_today()

    class _FakeProvider:
        def get_realtime(self, symbols):
            assert list(symbols) == ["6196.TWSE"]
            return pl.DataFrame([{
                "symbol": "6196.TWSE", "date": str(today), "open": 100.0, "high": 105.0,
                "low": 99.0, "close": 104.0, "volume": 1000.0, "amount": 104000.0,
                "prev_close": 101.0, "change_pct": 2.97, "quote_ts": 1_700_000_000_000,
                "status": "realtime",
            }])

    with (
        patch("app.services.preferences.get_realtime_data_provider", return_value="taiwan"),
        patch("app.data_providers.registry.get_provider", return_value=_FakeProvider()),
    ):
        out = svc.fetch_live_bar("6196.TWSE")

    assert out is not None
    assert out["symbol"] == "6196.TWSE"
    assert out["date"] == today
    assert out["close"] == 104.0
    assert out["open"] == 100.0
    assert out["high"] == 105.0
    assert out["low"] == 99.0
    assert out["change_pct"] == pytest.approx(0.0297)


@pytest.mark.parametrize("status", ["third_party_fallback", "official", "stale"])
def test_fetch_live_bar_none_when_row_is_not_realtime(status):
    svc = _svc_for_fetch()

    class _FakeProvider:
        def get_realtime(self, symbols):
            return pl.DataFrame([{
                "symbol": "6196.TWSE", "date": "2026-09-17", "open": 100.0, "high": 105.0,
                "low": 99.0, "close": 104.0, "volume": 1000.0, "amount": 104000.0,
                "prev_close": 101.0, "change_pct": 2.97, "status": status,
            }])

    with (
        patch("app.services.preferences.get_realtime_data_provider", return_value="taiwan"),
        patch("app.data_providers.registry.get_provider", return_value=_FakeProvider()),
    ):
        assert svc.fetch_live_bar("6196.TWSE") is None


def test_fetch_live_bar_none_when_provider_raises():
    svc = _svc_for_fetch()

    class _ExplodingProvider:
        def get_realtime(self, symbols):
            raise RuntimeError("HTTP Error 428: Precondition Required")

    with (
        patch("app.services.preferences.get_realtime_data_provider", return_value="taiwan"),
        patch("app.data_providers.registry.get_provider", return_value=_ExplodingProvider()),
    ):
        assert svc.fetch_live_bar("6196.TWSE") is None


def test_fetch_live_bar_none_and_no_provider_call_when_source_is_not_taiwan():
    svc = _svc_for_fetch()

    with (
        patch("app.services.preferences.get_realtime_data_provider", return_value="custom_provider"),
        patch("app.data_providers.registry.get_provider") as mock_get_provider,
    ):
        assert svc.fetch_live_bar("6196.TWSE") is None
        mock_get_provider.assert_not_called()


def _repo_with_state(symbol: str) -> MagicMock:
    repo = MagicMock()
    repo.get_live_agg.return_value = _live_state().with_columns(pl.lit(symbol).alias("symbol"))
    repo.get_enriched_latest.return_value = (
        _previous_enriched().with_columns(pl.lit(symbol).alias("symbol")),
        cn_today() - timedelta(days=1),
    )
    repo.get_instruments.return_value = pl.DataFrame()
    return repo


def _fetch_live_bar_with_row(svc: QuoteService, row: dict) -> dict | None:
    class _FakeProvider:
        def get_realtime(self, symbols):
            return pl.DataFrame([row])

    with (
        patch("app.services.preferences.get_realtime_data_provider", return_value="taiwan"),
        patch("app.data_providers.registry.get_provider", return_value=_FakeProvider()),
    ):
        return svc.fetch_live_bar(row["symbol"])


_LIVE_ROW = {
    "symbol": "6196.TWSE", "open": 10.1, "high": 10.2, "low": 10.0, "close": 10.2,
    "volume": 1200.0, "amount": 12240.0, "prev_close": 10.0, "change_pct": 2.0,
    "quote_ts": 1_700_000_000_000, "status": "realtime",
}


def test_fetch_live_bar_carries_indicators_from_the_incremental_pipeline():
    svc = _svc_for_fetch()
    svc._repo = _repo_with_state("6196.TWSE")

    out = _fetch_live_bar_with_row(svc, _LIVE_ROW)

    assert out["close"] == 10.2
    assert out["change_pct"] == pytest.approx(0.02)
    assert out["ma5"] == pytest.approx((40.0 + 10.2) / 5)
    assert out["macd_dif"] is not None


def test_fetch_live_bar_keeps_the_bare_bar_without_recursive_state():
    svc = _svc_for_fetch()
    svc._repo = _repo_with_state("6196.TWSE")
    svc._repo.get_live_agg.return_value = pl.DataFrame()

    out = _fetch_live_bar_with_row(svc, _LIVE_ROW)

    assert out["close"] == 10.2
    assert out.get("ma5") is None


def test_fetch_live_bar_none_when_building_the_bar_fails():
    svc = _svc_for_fetch()

    with patch.object(QuoteService, "_build_daily", side_effect=RuntimeError("malformed quote")):
        assert _fetch_live_bar_with_row(svc, _LIVE_ROW) is None


def test_fetch_live_bar_keeps_the_bare_bar_when_the_pipeline_fails():
    svc = _svc_for_fetch()
    svc._repo = _repo_with_state("6196.TWSE")
    svc._repo.get_live_agg.side_effect = RuntimeError("live agg rebuild failed")

    out = _fetch_live_bar_with_row(svc, _LIVE_ROW)

    assert out["close"] == 10.2
    assert out.get("ma5") is None


# ── Taiwan same-day turnover: filled from issued shares, for stocks only ──


def _patch_instrument_types(monkeypatch, types: dict[str, str]) -> None:
    master = SimpleNamespace(
        get_instrument=lambda s: SimpleNamespace(instrument_type=types[s]) if s in types else None,
    )
    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: master)


def _fake_share_capital_store(rows: list[dict]):
    """rows: [{"symbol": ..., "float_shares": ...}, ...] — mirrors as_instruments()'s schema."""
    schema = {"symbol": pl.Utf8, "float_shares": pl.Float64}

    class _Store:
        def as_instruments(self):
            return pl.DataFrame(rows, schema=schema) if rows else pl.DataFrame(schema=schema)

    return _Store


def test_fill_taiwan_turnover_rate_backfills_and_leaves_no_helper_columns(monkeypatch):
    from app.services.quote_service import _fill_taiwan_turnover_rate
    _patch_instrument_types(monkeypatch, {"6196.TWSE": "stock"})

    monkeypatch.setattr(
        "app.taiwan.share_capital_store.TaiwanShareCapitalStore",
        _fake_share_capital_store([{"symbol": "6196.TWSE", "float_shares": 100_000_000.0}]),
    )
    today = cn_today()
    df = pl.DataFrame(
        {"symbol": ["6196.TWSE"], "date": [today], "volume": [1_000_000.0]},
        schema={"symbol": pl.Utf8, "date": pl.Date, "volume": pl.Float64},
    )

    out = _fill_taiwan_turnover_rate(df)

    assert out["turnover_rate"][0] == pytest.approx(1.0)
    assert "float_shares" not in out.columns
    assert "turnover_rate_tw_fill" not in out.columns


def test_fill_taiwan_turnover_rate_ignores_a_non_today_row(monkeypatch):
    """Guards the same-day-only contract even if this helper is ever handed a stale row."""
    from app.services.quote_service import _fill_taiwan_turnover_rate
    _patch_instrument_types(monkeypatch, {"6196.TWSE": "stock"})

    monkeypatch.setattr(
        "app.taiwan.share_capital_store.TaiwanShareCapitalStore",
        _fake_share_capital_store([{"symbol": "6196.TWSE", "float_shares": 100_000_000.0}]),
    )
    yesterday = cn_today() - timedelta(days=1)
    df = pl.DataFrame(
        {"symbol": ["6196.TWSE"], "date": [yesterday], "volume": [1_000_000.0]},
        schema={"symbol": pl.Utf8, "date": pl.Date, "volume": pl.Float64},
    )

    out = _fill_taiwan_turnover_rate(df)

    assert out["turnover_rate"][0] is None


def test_fill_taiwan_turnover_rate_skips_non_positive_issued_shares(monkeypatch):
    from app.services.quote_service import _fill_taiwan_turnover_rate
    _patch_instrument_types(monkeypatch, {"6196.TWSE": "stock"})

    monkeypatch.setattr(
        "app.taiwan.share_capital_store.TaiwanShareCapitalStore",
        _fake_share_capital_store([{"symbol": "6196.TWSE", "float_shares": 0.0}]),
    )
    today = cn_today()
    df = pl.DataFrame(
        {"symbol": ["6196.TWSE"], "date": [today], "volume": [1_000_000.0]},
        schema={"symbol": pl.Utf8, "date": pl.Date, "volume": pl.Float64},
    )

    out = _fill_taiwan_turnover_rate(df)

    assert out["turnover_rate"][0] is None


def test_fill_taiwan_turnover_rate_leaves_an_etf_null(monkeypatch):
    """ETF units are not company share capital (docs/taiwan-share-capital-phase-6h.md)."""
    from app.services.quote_service import _fill_taiwan_turnover_rate

    _patch_instrument_types(monkeypatch, {"0050.TWSE": "etf"})
    monkeypatch.setattr(
        "app.taiwan.share_capital_store.TaiwanShareCapitalStore",
        _fake_share_capital_store([{"symbol": "0050.TWSE", "float_shares": 100_000_000.0}]),
    )
    df = pl.DataFrame(
        {"symbol": ["0050.TWSE"], "date": [cn_today()], "volume": [1_000_000.0]},
        schema={"symbol": pl.Utf8, "date": pl.Date, "volume": pl.Float64},
    )

    assert _fill_taiwan_turnover_rate(df)["turnover_rate"][0] is None


def test_fill_taiwan_turnover_rate_stays_null_when_the_security_master_fails(monkeypatch):
    from app.services.quote_service import _fill_taiwan_turnover_rate

    monkeypatch.setattr(
        "app.taiwan.universe.get_security_master",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        "app.taiwan.share_capital_store.TaiwanShareCapitalStore",
        _fake_share_capital_store([{"symbol": "6196.TWSE", "float_shares": 100_000_000.0}]),
    )
    df = pl.DataFrame(
        {"symbol": ["6196.TWSE"], "date": [cn_today()], "volume": [1_000_000.0]},
        schema={"symbol": pl.Utf8, "date": pl.Date, "volume": pl.Float64},
    )

    assert _fill_taiwan_turnover_rate(df)["turnover_rate"][0] is None


def test_enrich_today_incremental_backfills_taiwan_turnover_rate(monkeypatch):
    _patch_instrument_types(monkeypatch, {"6196.TWSE": "stock"})
    svc = _svc_for_fetch()
    svc._repo = _repo_with_state("6196.TWSE")
    monkeypatch.setattr(
        "app.taiwan.share_capital_store.TaiwanShareCapitalStore",
        _fake_share_capital_store([{"symbol": "6196.TWSE", "float_shares": 100_000_000.0}]),
    )
    row = {**_LIVE_ROW, "volume": 1_000_000.0}

    out = _fetch_live_bar_with_row(svc, row)

    assert out["turnover_rate"] == pytest.approx(1.0)


def test_enrich_today_incremental_leaves_a_share_turnover_rate_untouched(monkeypatch):
    """The Taiwan-suffix mask must not touch a non-Taiwan symbol's turnover_rate."""
    svc = _svc_for_fetch()
    svc._repo = _repo_with_state("600000.SH")
    monkeypatch.setattr(
        "app.taiwan.share_capital_store.TaiwanShareCapitalStore",
        _fake_share_capital_store([{"symbol": "600000.SH", "float_shares": 999.0}]),
    )
    row = {**_LIVE_ROW, "symbol": "600000.SH", "volume": 1_000_000.0}

    out = _fetch_live_bar_with_row(svc, row)

    assert out.get("turnover_rate") is None


def test_enrich_today_incremental_turnover_stays_null_when_share_capital_empty(monkeypatch):
    _patch_instrument_types(monkeypatch, {"6196.TWSE": "stock"})
    svc = _svc_for_fetch()
    svc._repo = _repo_with_state("6196.TWSE")
    monkeypatch.setattr(
        "app.taiwan.share_capital_store.TaiwanShareCapitalStore",
        _fake_share_capital_store([]),
    )

    out = _fetch_live_bar_with_row(svc, _LIVE_ROW)

    assert out.get("turnover_rate") is None


def test_enrich_today_incremental_turnover_stays_null_when_share_capital_read_fails(monkeypatch):
    _patch_instrument_types(monkeypatch, {"6196.TWSE": "stock"})
    svc = _svc_for_fetch()
    svc._repo = _repo_with_state("6196.TWSE")

    class _BoomStore:
        def as_instruments(self):
            raise RuntimeError("boom")

    monkeypatch.setattr("app.taiwan.share_capital_store.TaiwanShareCapitalStore", _BoomStore)

    out = _fetch_live_bar_with_row(svc, _LIVE_ROW)

    assert out is not None
    assert out.get("turnover_rate") is None
