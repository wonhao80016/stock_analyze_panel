from app.strategy.monitor import MonitorRuleEngine
from app.strategy import monitor_rules


def test_history_loader_selection_by_asset_type():
    eng = MonitorRuleEngine()

    def stock_loader(d, l):
        return "STOCK"

    def etf_loader(d, l):
        return "ETF"

    eng.set_history_loader(stock_loader)
    eng.set_history_loader_etf(etf_loader)

    assert eng._history_loader_for({"asset_type": "etf"}) is etf_loader
    assert eng._history_loader_for({"asset_type": "stock"}) is stock_loader
    # 未标注 asset_type 的旧规则默认走股票加载器
    assert eng._history_loader_for({}) is stock_loader


def test_etf_loader_defaults_none():
    eng = MonitorRuleEngine()
    assert eng._history_loader_for({"asset_type": "etf"}) is None


def test_rule_model_defaults_stock():
    from app.api.monitor_rules import RuleModel

    r = RuleModel(id="x", name="n", type="price")
    assert r.asset_type == "stock"


def test_normalize_preserves_and_defaults_asset_type():
    assert monitor_rules.normalize({"id": "a", "type": "price"})["asset_type"] == "stock"
    assert monitor_rules.normalize({"id": "a", "type": "signal", "asset_type": "etf"})["asset_type"] == "etf"


def _signal_rule(rid, asset_type, sym):
    return {
        "id": rid, "name": rid, "type": "signal", "asset_type": asset_type,
        "scope": "symbols", "symbols": [sym], "logic": "and",
        "conditions": [{"field": "rsi_14", "op": "<", "value": 100}],
        "cooldown_seconds": 0, "enabled": True,
    }


def _etf_df():
    import polars as pl
    return pl.DataFrame({
        "symbol": ["510300"],
        "close": [4.0],
        "change_pct": [0.01],
        "rsi_14": [40.0],
    })


def test_evaluate_asset_type_filters_rules():
    """evaluate(asset_type=etf) 只评估 ETF 规则; 股票规则被过滤。"""
    eng = MonitorRuleEngine()
    eng.set_rules([_signal_rule("r_etf", "etf", "510300"),
                   _signal_rule("r_stock", "stock", "510300")])
    df = _etf_df()

    etf_events = eng.evaluate(df, asset_type="etf")
    assert any(e["rule_id"] == "r_etf" for e in etf_events)
    assert all(e["rule_id"] != "r_stock" for e in etf_events)

    stock_events = eng.evaluate(df, asset_type="stock", reset_strategy_results=False)
    assert all(e["rule_id"] != "r_etf" for e in stock_events)


def test_has_asset_rules():
    eng = MonitorRuleEngine()
    eng.set_rules([_signal_rule("r_etf", "etf", "510300")])
    assert eng.has_asset_rules("etf") is True
    assert eng.has_asset_rules("stock") is False


def test_evaluate_default_asset_type_is_stock():
    """不传 asset_type 时默认只评估股票规则 (向后兼容旧调用)。"""
    eng = MonitorRuleEngine()
    eng.set_rules([_signal_rule("r_etf", "etf", "510300")])
    # 默认 asset_type=stock → ETF 规则不评估
    assert eng.evaluate(_etf_df()) == []


def test_etf_round_reads_the_taiwan_etf_rows_of_the_stock_snapshot():
    """Taiwan ETFs are polled as stocks, so ETF rules see the ETF rows of the stock snapshot."""
    from datetime import date
    from unittest.mock import MagicMock, patch

    import polars as pl

    from app.services.quote_service import QuoteService

    svc = QuoteService.__new__(QuoteService)
    svc._repo = MagicMock()
    engine = MagicMock()
    engine.rule_count = 1
    engine.has_asset_rules.side_effect = lambda asset_type: asset_type == "etf"
    engine.has_rule_type.return_value = False
    engine.evaluate.return_value = []
    engine.consume_strategy_result_updates.return_value = False
    svc._app_state = MagicMock()
    svc._app_state.monitor_engine = engine
    svc._app_state.repo = svc._repo
    today = date(2026, 8, 28)
    snapshot = pl.DataFrame({
        "symbol": ["2330.TWSE", "0050.TWSE"], "close": [500.0, 150.0], "rsi_14": [50.0, 40.0],
    })

    with (
        patch.object(QuoteService, "_is_continuous_trading", return_value=True),
        patch.object(QuoteService, "get_enriched_today", return_value=(snapshot, today)),
        patch.object(QuoteService, "_inject_intraday_signals", side_effect=lambda df, e, at: df),
        patch("app.services.quote_service.cn_today", return_value=today),
    ):
        svc._evaluate_monitors(pl.DataFrame(), None)

    etf_calls = [c for c in engine.evaluate.call_args_list if c.kwargs.get("asset_type") == "etf"]
    assert len(etf_calls) == 1
    assert etf_calls[0].args[0]["symbol"].to_list() == ["0050.TWSE"]
    svc._repo.get_enriched_latest_asset.assert_not_called()
