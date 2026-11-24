"""A monitor rule scoped below the whole market alerts on its scope but never stands in for the
post-close strategy results the screener page and the stock page read."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import polars as pl

from app.market_time import cn_today
from app.services import strategy_realtime
from app.strategy.monitor import MonitorRuleEngine

SYMBOLS = ["2330.TWSE", "2317.TWSE", "2454.TWSE"]


class _PicksRowsAtTen:
    def get(self, strategy_id: str):
        return SimpleNamespace(
            filter_history_fn=None,
            execution_backend="polars_expr",
            meta={"id": strategy_id, "name": strategy_id},
        )

    def run(self, strategy_id: str, context, **kwargs):
        picked = context.current.filter(pl.col("close") >= 10.0)
        rows = [{"symbol": symbol, "score": 1.0} for symbol in picked["symbol"].to_list()]
        return SimpleNamespace(
            total=len(rows),
            rows=rows,
            scores={},
            entry_signal_hits=[],
            exit_signal_hits=[],
            as_of=cn_today(),
        )


def _rule(rule_id: str, scope: str, symbols: list[str] | None = None) -> dict:
    return {
        "id": rule_id,
        "name": rule_id,
        "type": "strategy",
        "asset_type": "stock",
        "strategy_id": "picks",
        "scope": scope,
        "symbols": symbols or [],
        "cooldown_seconds": 0,
    }


def _monitor(rules: list[dict]) -> MonitorRuleEngine:
    monitor = MonitorRuleEngine()
    monitor.set_strategy_engine(_PicksRowsAtTen())
    monitor.set_rules(rules)
    return monitor


def _quotes(close: float = 10.0) -> pl.DataFrame:
    return pl.DataFrame({"symbol": SYMBOLS, "close": [close] * len(SYMBOLS)})


def _page_results(monitor: MonitorRuleEngine) -> dict:
    """The strategy results /api/screener/cached and the stock page's current selections read."""
    previous = str(cn_today() - timedelta(days=1))
    cached = {
        "as_of": previous,
        "results": {
            "picks": {"as_of": previous, "total": 3, "rows": [{"symbol": s} for s in SYMBOLS]},
        },
        "updated_at": 1,
    }
    return strategy_realtime.overlay_realtime(SimpleNamespace(monitor_engine=monitor), cached)["results"]


def test_scoped_rule_alerts_on_its_scope_without_replacing_the_whole_market_result():
    monitor = _monitor([_rule("scoped", "symbols", ["2330.TWSE"])])

    monitor.evaluate(_quotes(9.0))
    events = monitor.evaluate(_quotes())

    assert [(event["rule_id"], event["type"], event["symbol"]) for event in events] == [
        ("scoped", "pool_entry", "2330.TWSE")
    ]
    assert "picks" not in monitor.latest_strategy_results()
    assert monitor.consume_strategy_result_updates() is False
    result = _page_results(monitor)["picks"]
    assert result["total"] == 3
    assert result["as_of"] == str(cn_today() - timedelta(days=1))


def test_whole_market_rule_result_is_shown_when_a_scoped_rule_of_the_strategy_runs_after_it():
    monitor = _monitor([
        _rule("whole_market", "all"),
        _rule("scoped", "symbols", ["2330.TWSE"]),
    ])

    monitor.evaluate(_quotes())

    result = _page_results(monitor)["picks"]
    assert result["as_of"] == str(cn_today())
    assert [row["symbol"] for row in result["rows"]] == SYMBOLS
    assert monitor.consume_strategy_result_updates() is True
