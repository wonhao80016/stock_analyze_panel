"""Builtin strategy conditions match their stated rules (stage 4: D2, D8, D10, RC36)."""

from __future__ import annotations

import dataclasses
import importlib
import math
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.backtest.engine import SimResult
from app.backtest.matrix import build_market_data_matrix, make_signal_matrix
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.services.pick_grades import assign_grades
from app.strategy.engine import StrategyDataContext, StrategyEngine

pytestmark = pytest.mark.usefixtures("deny_network")

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "app" / "strategy" / "builtin"
ENGINE = StrategyEngine(strategy_dirs=[BUILTIN_DIR])


def _module(name: str):
    return importlib.import_module(f"app.strategy.builtin.{name}")


def _sessions(count: int, start: date = date(2025, 1, 6)) -> list[date]:
    out, day = [], start
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _frame(bars_by_symbol: dict[str, list[dict]]) -> pl.DataFrame:
    rows = []
    for symbol, bars in bars_by_symbol.items():
        for day, bar in zip(_sessions(len(bars)), bars, strict=True):
            rows.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "date": day,
                    "amount": bar["close"] * bar["volume"],
                    **bar,
                }
            )
    return pl.DataFrame(rows).sort(["symbol", "date"])


def _market(name: str, frame: pl.DataFrame):
    fields = _module(name).MATRIX_STRATEGY.required_fields()
    return build_market_data_matrix(frame, field_columns=fields)


def _signals(name: str, frame: pl.DataFrame, params: dict | None = None):
    market = _market(name, frame)
    resolved = StrategyEngine.resolve_params(ENGINE.get(name), params)
    return market, _module(name).MATRIX_STRATEGY.compute_signals(market, resolved)


def _bar(close: float, *, open_: float | None = None, high=None, low=None, volume=1000.0):
    open_ = close if open_ is None else open_
    return {
        "open": open_,
        "high": max(open_, close) + 0.1 if high is None else high,
        "low": min(open_, close) - 0.1 if low is None else low,
        "close": close,
        "volume": volume,
    }


# --------------------------------------------------------------------------
# D8a / D8b bottom_reversal
# --------------------------------------------------------------------------

ENTRY_BAR = 49


def _capitulation_bars(pre_stop_volumes=(1000.0,) * 5, stop_volume=3000.0) -> list[dict]:
    """Flat at 100, an eight-bar slide, a long-lower-wick stop bar, then a bar above its high."""
    bars = [_bar(100.0, high=100.5, low=99.5) for _ in range(40)]
    previous = 100.0
    for close in (97.0, 94.0, 91.0, 88.0, 85.0, 82.0, 79.0, 76.0):
        bars.append(_bar(close, open_=previous, high=previous + 0.2, low=close - 0.2))
        previous = close
    for offset, volume in enumerate(pre_stop_volumes):
        bars[43 + offset]["volume"] = volume
    bars.append(_bar(75.5, open_=75.0, high=76.0, low=72.0, volume=stop_volume))
    bars.append(_bar(78.0, open_=76.0, high=78.5, low=75.8, volume=1500.0))
    return bars


def test_bottom_reversal_exit_is_not_true_on_its_own_entry_bar():
    _, signals = _signals("bottom_reversal", _frame({"1101.TWSE": _capitulation_bars()}))

    assert signals.entry[ENTRY_BAR, 0] == 1
    assert signals.exit[ENTRY_BAR, 0] == 0


def test_bottom_reversal_exits_on_a_new_20_day_closing_low():
    bars = _capitulation_bars()
    bars.append(_bar(77.0, open_=78.0, high=78.5, low=76.5))
    bars.append(_bar(75.0, open_=77.0, high=77.2, low=74.8))
    _, signals = _signals("bottom_reversal", _frame({"1101.TWSE": bars}))

    assert signals.exit[ENTRY_BAR + 1, 0] == 0
    assert signals.exit[ENTRY_BAR + 2, 0] == 1
    code = int(signals.exit_signal_code[ENTRY_BAR + 2, 0])
    assert signals.exit_signal_ids[code] == "signal_close_20d_low"


def test_bottom_reversal_exits_on_the_ma20_cross_down_only():
    closes = [100.0 + 0.5 * index for index in range(40)] + [110.0, 111.0]
    _, signals = _signals("bottom_reversal", _frame({"1101.TWSE": [_bar(c) for c in closes]}))

    assert signals.exit[40, 0] == 1
    assert signals.exit_signal_ids[int(signals.exit_signal_code[40, 0])] == "signal_ma20_breakdown"
    # Still below MA20 the next bar, but no new cross and no new closing low.
    assert signals.exit[41, 0] == 0


@pytest.mark.parametrize(
    ("pre_stop_volumes", "stop_volume", "expected"),
    [
        # Twice the previous bar (500) but not twice the 5-bar average (1700).
        ((2000.0, 2000.0, 2000.0, 2000.0, 500.0), 1500.0, 0),
        # Twice the 5-bar average (800) but not twice the previous bar (2000).
        ((500.0, 500.0, 500.0, 500.0, 2000.0), 1700.0, 1),
    ],
    ids=["previous_bar_only", "five_bar_average"],
)
def test_bottom_reversal_climax_volume_is_measured_against_the_5_bar_average(
    pre_stop_volumes, stop_volume, expected
):
    bars = _capitulation_bars(pre_stop_volumes, stop_volume)
    _, signals = _signals("bottom_reversal", _frame({"1101.TWSE": bars}))

    assert signals.entry[ENTRY_BAR, 0] == expected


# --------------------------------------------------------------------------
# D8c n_day_low_reversal
# --------------------------------------------------------------------------


def _oscillating_bars(last: dict) -> list[dict]:
    closes = [105.0 + 5.0 * math.sin(index / 3.0) for index in range(69)]
    return [_bar(close, low=close - 0.5, high=close + 0.5) for close in closes] + [last]


def test_n_day_low_reversal_needs_an_intraday_break_and_a_close_back_above_the_prior_low():
    frame = _frame(
        {
            # Low undercuts the prior 60-bar low (about 99.5) and the close recovers above it.
            "1101.TWSE": _oscillating_bars(
                _bar(100.6, open_=99.8, high=101.0, low=98.0, volume=3000.0)
            ),
            # A new closing low that stays below the prior low.
            "1102.TWSE": _oscillating_bars(
                _bar(96.0, open_=95.0, high=96.5, low=94.0, volume=3000.0)
            ),
        }
    )
    market, signals = _signals("n_day_low_reversal", frame)

    entries = dict(zip(market.symbols, signals.entry[-1].tolist(), strict=True))
    assert entries == {"1101.TWSE": 1, "1102.TWSE": 0}


def test_n_day_low_reversal_takes_the_prior_low_from_lows_not_closes():
    # The prior 60 bars bottom at a low of about 99.5 and a close of about 100.0.
    frame = _frame(
        {
            # Closes back above the prior low but not above the prior lowest close.
            "1103.TWSE": _oscillating_bars(_bar(99.75, open_=99.0, low=98.0, volume=3000.0)),
            # Undercuts the prior lowest close but not the prior low.
            "1104.TWSE": _oscillating_bars(_bar(100.6, open_=99.9, low=99.75, volume=3000.0)),
        }
    )
    market, signals = _signals("n_day_low_reversal", frame)

    entries = dict(zip(market.symbols, signals.entry[-1].tolist(), strict=True))
    assert entries == {"1103.TWSE": 1, "1104.TWSE": 0}


# --------------------------------------------------------------------------
# D8g pullback_to_support
# --------------------------------------------------------------------------


def _pullback_bars(ma20_multiple: float) -> list[dict]:
    """A steady uptrend whose last quiet bar closes at ``ma20_multiple`` times its own MA20."""
    closes = [100.0 * 1.004**index for index in range(79)]
    previous_19 = sum(closes[-19:])
    closes.append(ma20_multiple * previous_19 / (20.0 - ma20_multiple))
    bars = [_bar(close) for close in closes]
    bars[-1]["volume"] = 500.0
    return bars


def test_pullback_to_support_requires_the_close_to_hold_ma20():
    frame = _frame({"1101.TWSE": _pullback_bars(0.99), "1102.TWSE": _pullback_bars(1.01)})
    market, signals = _signals("pullback_to_support", frame)

    entries = dict(zip(market.symbols, signals.entry[-1].tolist(), strict=True))
    assert entries == {"1101.TWSE": 0, "1102.TWSE": 1}


# --------------------------------------------------------------------------
# D10 min_amount
# --------------------------------------------------------------------------

LIQUIDITY_CASES = {
    "swing_trend_confirmation": {
        "swing_radius": 2,
        "require_ma_alignment": False,
        "ma20_slope_bars": 1,
        "edge_triggered": False,
    },
    "pullback_resume": {
        "pullback_window": 5,
        "pullback_vol_ratio_max": 1.5,
        "require_break_prev_high": False,
    },
}


def _random_walk(symbols: tuple[str, ...], sessions: int = 160, seed: int = 20261009):
    rng = np.random.default_rng(seed)
    drift = np.array([0.004, 0.0, -0.006, 0.002])[(np.arange(sessions) // 30) % 4]
    frames = []
    for symbol in symbols:
        close = 50.0 * np.cumprod(1.0 + drift + rng.normal(0.0, 0.018, sessions))
        previous = np.concatenate(([50.0], close[:-1]))
        open_ = previous * (1.0 + rng.normal(0.0, 0.006, sessions))
        high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.008, sessions)))
        low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.008, sessions)))
        volume = rng.lognormal(13.0, 0.35, sessions) * np.where(
            rng.random(sessions) < 0.08, 2.5, 1.0
        )
        frames.append(
            pl.DataFrame(
                {
                    "symbol": [symbol] * sessions,
                    "name": [symbol] * sessions,
                    "date": _sessions(sessions),
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "amount": close * volume,
                }
            )
        )
    return pl.concat(frames)


def _liquid_entry(name: str) -> tuple[pl.DataFrame, int]:
    """One symbol's bars and an entry bar that fires with every liquidity minimum off."""
    unfiltered = {**LIQUIDITY_CASES[name], "min_volume": 0.0, "min_amount": 0.0}
    panel = _random_walk(tuple(f"{1101 + offset}.TWSE" for offset in range(8)))
    for symbol in panel["symbol"].unique(maintain_order=True).to_list():
        bars = panel.filter(pl.col("symbol") == symbol)
        _, signals = _signals(name, bars, unfiltered)
        hits = np.flatnonzero(signals.entry[:, 0])
        if hits.size:
            return bars, int(hits[-1])
    raise AssertionError(f"{name} fires no entry on the random walk")


def _with_liquidity(bars: pl.DataFrame, at: int, volume: float, amount: float) -> pl.DataFrame:
    """Scale the symbol's volume so bar ``at`` trades ``volume`` shares; its amount is ``amount``."""
    scale = volume / float(bars["volume"][at])
    scaled = bars.with_columns(pl.col("volume") * scale)
    amounts = (scaled["close"] * scaled["volume"]).to_list()
    amounts[at] = amount
    return scaled.with_columns(pl.Series("amount", amounts))


@pytest.mark.parametrize("name", sorted(LIQUIDITY_CASES))
def test_liquidity_minimum_is_ntd_100m_traded_by_default(name):
    bars, at = _liquid_entry(name)
    params = StrategyEngine.resolve_params(ENGINE.get(name), LIQUIDITY_CASES[name])

    thin_value = _with_liquidity(bars, at, volume=3_000_000.0, amount=5e7)
    traded_value = _with_liquidity(bars, at, volume=1_000_000.0, amount=2e8)
    strategy = _module(name).MATRIX_STRATEGY

    assert strategy.compute_signals(_market(name, thin_value), params).entry[at, 0] == 0
    assert strategy.compute_signals(_market(name, traded_value), params).entry[at, 0] == 1


@pytest.mark.parametrize("name", sorted(LIQUIDITY_CASES))
def test_saved_min_volume_override_is_still_honoured(name):
    bars, at = _liquid_entry(name)
    traded_value = _market(name, _with_liquidity(bars, at, volume=1_000_000.0, amount=2e8))
    saved = {"params": {"min_volume": 2_000_000.0}}
    params = StrategyEngine.resolve_params(ENGINE.get(name), LIQUIDITY_CASES[name], saved)

    signals = _module(name).MATRIX_STRATEGY.compute_signals(traded_value, params)

    assert signals.entry[at, 0] == 0


# --------------------------------------------------------------------------
# RC36 entry ids; D8a / swing exit ids
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "entry_id"),
    [
        ("bullish_alignment", "signal_bullish_alignment_entry"),
        ("pullback_ma20_bounce", "signal_pullback_ma20_bounce_entry"),
        ("pullback_to_support", "signal_pullback_to_support_entry"),
        ("n_day_low_reversal", "signal_n_day_low_reclaim"),
        ("low_volatility_leader", "signal_low_volatility_leader_entry"),
    ],
)
def test_strategy_entry_condition_is_emitted_under_its_own_id(name, entry_id):
    frame = _frame({"1101.TWSE": _pullback_bars(1.01)})
    _, signals = _signals(name, frame)

    assert tuple(_module(name).ENTRY_SIGNALS) == (entry_id,)
    assert signals.entry_signal_ids == (entry_id,)


@pytest.mark.parametrize(
    ("name", "exit_ids"),
    [
        ("bottom_reversal", ("signal_ma20_breakdown", "signal_close_20d_low")),
        ("swing_trend_confirmation", ("signal_ma20_break", "signal_swing_structure_exit")),
    ],
)
def test_exit_ids_name_each_exit_condition(name, exit_ids):
    _, signals = _signals(name, _frame({"1101.TWSE": _pullback_bars(1.01)}))

    assert tuple(_module(name).EXIT_SIGNALS) == exit_ids
    assert signals.exit_signal_ids == exit_ids


SWING_PARAMS = {**LIQUIDITY_CASES["swing_trend_confirmation"], "min_volume": 0.0, "min_amount": 0.0}


def _swing_low_break(market) -> np.ndarray:
    module = _module("swing_trend_confirmation")
    last_low, prev_low = module._confirmed_swings(
        market.low, SWING_PARAMS["swing_radius"], peak=False
    )
    return last_low < prev_low


def test_swing_structure_exit_stays_off_rows_without_a_bar():
    panel = _random_walk(("1101.TWSE", "1102.TWSE"))
    market, _ = _signals("swing_trend_confirmation", panel, SWING_PARAMS)
    broken = _swing_low_break(market)[:, 0]
    session = next(t for t in range(40, broken.size - 1) if broken[t - 1] and broken[t])
    day = market.timestamp_labels[session][:10]
    gapped = panel.filter(
        ~((pl.col("symbol") == "1101.TWSE") & (pl.col("date") == date.fromisoformat(day)))
    )

    gapped_market, signals = _signals("swing_trend_confirmation", gapped, SWING_PARAMS)

    assert not np.isfinite(gapped_market.close[session, 0])
    assert signals.exit[session, 0] == 0
    assert not signals.exit[~np.isfinite(gapped_market.close)].any()


def test_swing_structure_break_reports_its_own_exit_id():
    panel = _random_walk(("1101.TWSE",))
    market, signals = _signals("swing_trend_confirmation", panel, SWING_PARAMS)
    ma20 = panel["close"].rolling_mean(20).to_numpy()
    structure_only = _swing_low_break(market)[:, 0] & (market.close[:, 0] >= ma20 * 1.001)
    rows = np.flatnonzero(structure_only)
    assert rows.size

    codes = signals.exit_signal_code[rows, 0]
    assert signals.exit[rows, 0].all()
    assert {signals.exit_signal_ids[int(code)] for code in codes} == {"signal_swing_structure_exit"}


# --------------------------------------------------------------------------
# D2 / D8d / D8f scoring directions
# --------------------------------------------------------------------------

SYMBOLS = ("1101.TWSE", "1102.TWSE", "1103.TWSE")
DIRECTION_CASES = [
    ("bottom_reversal", {"rsi_14": "low"}),
    ("n_day_low_reversal", {"momentum_5d": "low"}),
    ("oversold_bounce", {"rsi_14": "low", "momentum_5d": "low"}),
    ("oversold_reversal", {"rsi_14": "low"}),
    ("consolidation_breakout", {"distance_from_low_60d": "low"}),
    ("bullish_alignment", {"vol_ratio_5d": "high"}),
    ("low_volatility_leader", {"vol_ratio_5d": "low"}),
    ("pullback_to_support", {"vol_ratio_5d": "low"}),
]


def _scored_panel(scoring: dict, varied: dict[str, str]) -> pl.DataFrame:
    """Every scoring factor equal across symbols except ``varied``, which rises 1101 < 1102 < 1103."""
    rows = []
    for day in _sessions(10):
        for rank, symbol in enumerate(SYMBOLS):
            row = {
                "symbol": symbol,
                "name": symbol,
                "date": day,
                "open": 50.0,
                "high": 50.5,
                "low": 49.5,
                "close": 50.0,
                "volume": 1_000_000.0,
                "amount": 5e7,
                "signal_limit_up": False,
                "signal_limit_down": False,
            }
            for field in scoring:
                value = 10.0 + rank if field in varied else 1.0
                if field == "distance_from_low_60d":
                    # A virtual factor: the matrix derives it from close and low_60d.
                    row["low_60d"] = row["close"] / (1.0 + value / 100.0)
                else:
                    row[field] = value
            rows.append(row)
    return pl.DataFrame(rows).sort(["symbol", "date"])


@pytest.mark.parametrize(("name", "varied"), DIRECTION_CASES, ids=[c[0] for c in DIRECTION_CASES])
def test_strategy_ranks_its_factors_in_the_declared_direction(monkeypatch, name, varied):
    strategy = ENGINE.get(name)
    scoring = strategy.meta["scoring"]
    assert set(varied) <= set(scoring)
    monkeypatch.setattr(
        strategy.matrix_strategy,
        "compute_signals",
        lambda market, params: make_signal_matrix(
            market.shape, entry=np.ones(market.shape, dtype=np.uint8)
        ),
    )
    panel = _scored_panel(scoring, varied)
    as_of = panel["date"].max()
    context = StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=as_of,
        current=panel.filter(pl.col("date") == as_of),
        history=panel,
    )

    result = ENGINE.run(name, context, overrides={"basic_filter": {"enabled": False}})

    ranked = [str(row["symbol"]) for row in result.rows]
    low_first = set(varied.values()) == {"low"}
    assert ranked == (list(SYMBOLS) if low_first else list(reversed(SYMBOLS)))


@pytest.mark.parametrize(
    "name", ["bullish_alignment", "low_volatility_leader", "pullback_to_support"]
)
def test_turnover_factor_is_replaced_by_the_5_day_volume_ratio(name):
    scoring = ENGINE.get(name).meta["scoring"]

    assert "turnover_rate" not in scoring
    assert scoring["vol_ratio_5d"] == 0.3


def test_consolidation_volume_contraction_default_is_0_9():
    params = StrategyEngine.resolve_params(ENGINE.get("consolidation_breakout"))

    assert params["volume_contraction_max"] == 0.9


# --------------------------------------------------------------------------
# D2 backtests run without turnover history
# --------------------------------------------------------------------------


class _AllEntries:
    def required_fields(self):
        return frozenset({"close"})

    def required_warmup_bars(self, params):
        return 1

    def compute_signals(self, market, params):
        return make_signal_matrix(market.shape, entry=np.ones(market.shape, dtype=np.uint8))


class _StrategyEngineStub:
    def __init__(self, strategy) -> None:
        self.strategy = strategy

    def get(self, strategy_id: str):
        return self.strategy


class _BacktestEngineStub:
    def __init__(self, panel: pl.DataFrame) -> None:
        self.panel = panel
        self.repo = SimpleNamespace(store=SimpleNamespace(data_dir=None))
        self.simulated = False

    def load_panel_for_backtest(self, symbols, start, end, feature_plan, asset_type="stock"):
        return self.panel

    def load_market_data_matrix_for_backtest(
        self, symbols, start, end, feature_plan, asset_type="stock", **kwargs
    ):
        fields = (
            set(feature_plan.base_columns)
            | set(feature_plan.instrument_columns)
            | set(feature_plan.matrix_columns)
            | {"turnover_rate"}
        )
        return build_market_data_matrix(self.panel, field_columns=fields)

    def simulate_market_matrix(
        self, matrix, config, progress_cb=None, cancel_event=None, options=None
    ):
        self.simulated = True
        return SimResult(
            equity_curve=[{"date": "2024-01-01", "value": config.initial_capital}],
            drawdown_curve=[{"date": "2024-01-01", "value": 0.0}],
            trades=[],
            per_symbol_stats=[],
            stats={"total_return": 0.0, "n_trades": 0},
        )


def _turnover_on_the_newest_day_only() -> pl.DataFrame:
    start, days = date(2024, 1, 1), 120
    rows = []
    for rank, symbol in enumerate(SYMBOLS):
        for offset in range(days):
            close = 50.0 + rank + 0.1 * offset + math.sin(offset / 4.0)
            rows.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "date": start + timedelta(days=offset),
                    "open": close,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 1_000_000.0 * (1.0 + 0.3 * math.sin(offset / 3.0 + rank)),
                    "amount": 5e7,
                    "raw_close": close,
                    "raw_high": close + 0.5,
                    "raw_low": close - 0.5,
                    "turnover_rate": 1.5 if offset == days - 1 else None,
                    "signal_limit_up": False,
                    "signal_limit_down": False,
                }
            )
    return pl.DataFrame(rows, infer_schema_length=None).sort(["symbol", "date"])


@pytest.mark.parametrize(
    "name", ["bullish_alignment", "low_volatility_leader", "pullback_to_support"]
)
def test_former_turnover_scorers_backtest_without_turnover_history(name):
    strategy = dataclasses.replace(
        ENGINE.get(name), basic_filter={"enabled": False}, matrix_strategy=_AllEntries()
    )
    engine = _BacktestEngineStub(_turnover_on_the_newest_day_only())
    service = StrategyBacktestService(engine=engine, strategy_engine=_StrategyEngineStub(strategy))

    result = service.run(
        StrategyBacktestConfig(
            strategy_id=name,
            symbols=None,
            start=date(2024, 3, 21),
            end=date(2024, 4, 29),
            matching="close_t",
            mode="position",
        )
    )

    assert result.error is None
    assert engine.simulated


# --------------------------------------------------------------------------
# D8e limit_up_locked grade
# --------------------------------------------------------------------------


def _rows(*specs) -> list[dict]:
    return [
        {"symbol": f"{1000 + index}.TWSE", "score": score, "signal_limit_up": locked}
        for index, (score, locked) in enumerate(specs)
    ]


def _graded(rows, market_state=None):
    graded = assign_grades({"s": {"total": len(rows), "rows": rows}}, market_state, {"s"})
    return graded["s"]["rows"]


def test_limit_up_locked_pick_keeps_its_grade_and_is_flagged():
    rows = _rows((90.0, False), (80.0, True), (70.0, None), (60.0, False))

    graded = _graded(rows)

    assert [(row["grade"], row["grade_reason"]) for row in graded] == [("buy", None)] * 4
    assert [row["limit_up_locked"] for row in graded] == [False, True, False, False]
    assert [row["score"] for row in graded] == [90.0, 80.0, 70.0, 60.0]
    assert [row["symbol"] for row in graded] == [row["symbol"] for row in rows]


def test_limit_up_flag_leaves_every_other_grade_reason_unchanged():
    rows = _rows(
        (None, True), (90.0, True), *[(80.0 - index, False) for index in range(4)], (1.0, True)
    )
    below = {"as_of": "2026-09-30", "status": "ok", "below_ma": True, "close": 90.0, "ma": 100.0}

    graded = _graded(rows, below)

    assert [(row["grade"], row["grade_reason"]) for row in graded] == [
        ("watch", "score_unavailable"),
        *[("watch", "market_below_ma20")] * 4,
        ("watch", "outside_top_n"),
        ("watch", "outside_top_n"),
    ]
    assert [row["limit_up_locked"] for row in graded] == [True, True, *[False] * 4, True]
