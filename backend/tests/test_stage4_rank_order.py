"""The backtest buys candidates in the order the strategy list shows them, for either direction."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.backtest.engine import BacktestEngine, MatcherConfig
from app.backtest.matrix import (
    build_market_data_matrix,
    build_market_matrix_from_signals,
    make_signal_matrix,
)
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.strategy.engine import StrategyDataContext, StrategyEngine, ranks_higher_first

pytestmark = pytest.mark.usefixtures("deny_network")

CLOSES = {"000001.SZ": 30.0, "000002.SZ": 10.0, "000003.SZ": 20.0}
HIGH_FIRST = ["000001.SZ", "000003.SZ", "000002.SZ"]
LOW_FIRST = HIGH_FIRST[::-1]

MATRIX = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {{"id": "ranked", "name": "ranked", "asset_types": ["stock"], "scoring": {scoring},
        "descending": {descending}, "limit": 0}}
BASIC_FILTER = {{"enabled": False}}
EXECUTION_BACKEND = "matrix_native"


class _EveryBar:
    def required_fields(self):
        return frozenset({{"close"}})

    def required_warmup_bars(self, params):
        return 1

    def compute_signals(self, market, params):
        entry = np.ones(market.shape, dtype=np.uint8)
        return make_signal_matrix(market.shape, entry=entry, score=market.close)


MATRIX_STRATEGY = _EveryBar()
"""

POLARS = """
import polars as pl

META = {{"id": "ranked", "name": "ranked", "asset_types": ["stock"], "scoring": {scoring},
        "descending": {descending}, "limit": 0}}
BASIC_FILTER = {{"enabled": False}}
EXECUTION_BACKEND = "polars_expr"


def filter(df, params):
    return pl.lit(True)
"""

COMPOSITE = """
META = {{"id": "ranked_combo", "name": "ranked combo", "asset_types": ["stock"],
        "timeframes": ["1d"], "basic_filter": {{"enabled": False}}, "descending": {descending},
        "limit": 0, "children": [{{"strategy_id": "ranked", "weight": 1.0}}]}}
EXECUTION_BACKEND = "composite"
"""


def _sessions(count: int) -> list[date]:
    out, day = [], date(2025, 1, 2)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _panel(days: list[date]) -> pl.DataFrame:
    rows = []
    for symbol, close in CLOSES.items():
        for day in days:
            rows.append(
                {
                    "symbol": symbol,
                    "name": symbol[:6],
                    "date": day,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "raw_close": close,
                    "raw_high": close,
                    "raw_low": close,
                    "volume": 1e6,
                    "amount": close * 1e6,
                }
            )
    return pl.DataFrame(rows).sort(["symbol", "date"])


class _SimulatingBacktestEngine:
    """Serves a fixed panel and runs the production matcher on it."""

    def __init__(self, panel: pl.DataFrame) -> None:
        self.panel = panel
        self.repo = None

    def load_panel_for_backtest(self, symbols, start, end, feature_plan, asset_type="stock"):
        return self.panel

    def load_market_data_matrix_for_backtest(
        self, symbols, start, end, feature_plan, asset_type="stock", **kwargs
    ):
        fields = (
            set(feature_plan.base_columns)
            | set(feature_plan.instrument_columns)
            | set(feature_plan.matrix_columns)
        )
        return build_market_data_matrix(self.panel, field_columns=fields)

    def simulate_market_matrix(
        self, matrix, config, progress_cb=None, cancel_event=None, options=None
    ):
        return BacktestEngine(None).simulate_market_matrix(
            matrix, config, progress_cb, cancel_event, options
        )


@pytest.mark.parametrize(
    ("meta", "scoring", "expected"),
    [
        ({"descending": True}, {"close": 1.0}, True),
        ({"descending": False}, {"close": 1.0}, False),
        ({"descending": False}, {}, False),
        ({"descending": False, "order_by": "score"}, {}, False),
        ({"descending": False, "order_by": "close"}, {}, True),
        ({"descending": False, "order_by": "close"}, {"amount": 1.0}, False),
    ],
)
def test_the_score_ranks_higher_first_unless_descending_is_false(meta, scoring, expected):
    assert ranks_higher_first(meta, scoring) is expected


@pytest.mark.parametrize("descending", [False, True], ids=["low_first", "high_first"])
@pytest.mark.parametrize(
    ("template", "scoring"),
    [(MATRIX, {"close": 1.0}), (MATRIX, {}), (POLARS, {"close": 1.0})],
    ids=["matrix_weights", "matrix_own_score", "polars_weights"],
)
def test_the_backtest_buys_the_first_listed_candidate(tmp_path, template, scoring, descending):
    (tmp_path / "ranked.py").write_text(
        template.format(scoring=repr(scoring), descending=descending), encoding="utf-8"
    )
    days = _sessions(5)

    listed, result = _list_and_backtest(tmp_path, "ranked", days, max_positions=1)

    assert [row["symbol"] for row in listed] == (HIGH_FIRST if descending else LOW_FIRST)
    assert result.error is None, result.error
    first = min(result.trades, key=lambda trade: str(trade["entry_date"]))
    assert str(first["entry_date"]) == days[1].isoformat()
    assert first["symbol"] == listed[0]["symbol"]
    assert first["entry_score"] == pytest.approx(listed[0]["score"])


def _list_and_backtest(strategy_dir, strategy_id: str, days: list[date], **config):
    """The strategy's list on days[1] and a backtest from days[1] on the same panel."""
    engine = StrategyEngine(strategy_dirs=[strategy_dir])
    panel = _panel(days)
    context = StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=days[1],
        current=panel.filter(pl.col("date") == days[1]),
        history=panel.filter(pl.col("date") <= days[1]),
    )
    listed = engine.run(strategy_id, context).rows
    result = StrategyBacktestService(
        engine=_SimulatingBacktestEngine(panel), strategy_engine=engine
    ).run(
        StrategyBacktestConfig(
            strategy_id=strategy_id,
            symbols=None,
            start=days[1],
            end=days[-1],
            matching="close_t",
            **config,
        )
    )
    return listed, result


@pytest.mark.parametrize("descending", [False, True], ids=["low_first", "high_first"])
def test_a_composite_backtest_buys_in_the_composite_list_order(tmp_path, descending):
    (tmp_path / "ranked.py").write_text(
        MATRIX.format(scoring=repr({}), descending=True), encoding="utf-8"
    )
    (tmp_path / "ranked_combo.py").write_text(
        COMPOSITE.format(descending=descending), encoding="utf-8"
    )
    days = _sessions(5)

    listed, result = _list_and_backtest(tmp_path, "ranked_combo", days, max_positions=1)

    assert [row["symbol"] for row in listed] == (HIGH_FIRST if descending else LOW_FIRST)
    assert result.error is None, result.error
    first = min(result.trades, key=lambda trade: str(trade["entry_date"]))
    assert str(first["entry_date"]) == days[1].isoformat()
    assert first["symbol"] == listed[0]["symbol"]


@pytest.mark.parametrize("descending", [False, True], ids=["low_first", "high_first"])
def test_score_weighted_sizing_gives_the_first_listed_buy_the_most_capital(tmp_path, descending):
    (tmp_path / "ranked.py").write_text(
        MATRIX.format(scoring=repr({}), descending=descending), encoding="utf-8"
    )
    days = _sessions(5)

    listed, result = _list_and_backtest(
        tmp_path, "ranked", days, max_positions=3, position_sizing="score_weight"
    )

    assert result.error is None, result.error
    share = {
        trade["symbol"]: trade["position_pct"]
        for trade in result.trades
        if str(trade["entry_date"]) == days[1].isoformat()
    }
    first, second, last = (share[row["symbol"]] for row in listed)
    assert first >= second > last


def _sized_entries(scores: list[float], **config) -> dict[str, float]:
    """position_pct of each symbol bought on the first bar, where every symbol enters with *scores*."""
    symbols = [f"S{index}" for index in range(len(scores))]
    days = _sessions(3)
    panel = pl.DataFrame(
        [
            {
                "symbol": symbol,
                "name": symbol,
                "date": day,
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 1e6,
            }
            for symbol in symbols
            for day in days
        ]
    ).sort(["symbol", "date"])
    market = build_market_data_matrix(panel)
    entry = np.zeros(market.shape, dtype=np.uint8)
    entry[0] = 1
    score = np.zeros(market.shape, dtype=np.float32)
    score[0] = [scores[symbols.index(symbol)] for symbol in market.symbols]
    matrix = build_market_matrix_from_signals(
        market,
        make_signal_matrix(market.shape, entry=entry, score=score),
        entry_delay_bars=1,
        exit_delay_bars=1,
    )
    result = BacktestEngine(None).simulate_market_matrix(
        matrix,
        MatcherConfig(
            matching="open_t+1",
            fees_pct=0,
            slippage_bps=0,
            position_sizing="score_weight",
            **config,
        ),
    )
    return {trade.symbol: trade.position_pct for trade in result.trades}


def test_lower_first_score_weights_follow_the_rank_and_skip_missing_scores():
    shares = _sized_entries(
        [float("nan"), 1.0, 3.0, 5.0], max_positions=4, score_higher_first=False
    )

    # Rank weights 3:2:1 over the scored buys; the two best exceed one slot and are capped at it.
    assert set(shares) == {"S1", "S2", "S3"}
    assert shares["S1"] == pytest.approx(0.25, abs=1e-3)
    assert shares["S2"] == pytest.approx(0.25, abs=1e-3)
    assert shares["S3"] == pytest.approx(1 / 6, abs=1e-3)


def test_lower_first_score_weights_keep_negative_scores():
    shares = _sized_entries([-4.0, -2.0, 6.0], max_positions=6, score_higher_first=False)

    assert shares["S0"] >= shares["S1"] > shares["S2"] > 0


def test_higher_first_score_weights_stay_proportional_to_the_positive_score():
    shares = _sized_entries([1.0, 3.0, -3.0], max_positions=3)

    assert set(shares) == {"S0", "S1"}
    assert shares["S0"] == pytest.approx(0.25, abs=1e-3)
    assert shares["S1"] == pytest.approx(1 / 3, abs=1e-3)
