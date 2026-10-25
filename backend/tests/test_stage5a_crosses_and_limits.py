"""Crosses with a float tolerance, one limit-down recovery rule and the 60-day close labels (D8).

A cross moves from strictly one side to strictly the other; a difference within
max(1e-9, 1e-9 * |close|) of zero is no side, so such a bar neither crosses nor splits one
cross into two. Limit-down recovery touches the limit-down price and closes above the
previous close.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.backtest import matrix
from app.backtest.matrix import build_market_data_matrix
from app.indicators import pipeline
from app.indicators.pipeline import compute_signals
from app.strategy import field_labels
from app.strategy.builtin import ma_golden_cross, macd_golden, volume_price_surge
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService

pytestmark = pytest.mark.usefixtures("deny_network")

SYMBOL = "1101.TWSE"
PRICE = 100.0
NEAR_ZERO = 5e-8  # Below 1e-9 * PRICE.

# (difference series, expected up-crosses, expected down-crosses)
CASES = {
    "through_a_zero_bar": ([-1.0, 0.0, 1.0], [False, False, True], [False, False, False]),
    "touch_and_back": ([1.0, 0.0, 1.0], [False, False, False], [False, False, False]),
    "noise_around_zero": (
        [-1.0, NEAR_ZERO, -NEAR_ZERO, 1.0],
        [False, False, False, True],
        [False, False, False, False],
    ),
    "down_through_noise": ([1.0, -NEAR_ZERO, -1.0], [False, False, False], [False, False, True]),
    "plain_cross": ([-1.0, 1.0, -1.0], [False, True, False], [False, False, True]),
}

PAIRS = [
    ("signal_ma_golden_5_20", "signal_ma_dead_5_20", "ma5", "ma20"),
    ("signal_macd_golden", "signal_macd_dead", "macd_dif", "macd_dea"),
    ("signal_ma20_breakout", "signal_ma20_breakdown", "close", "ma20"),
    ("signal_ma5_breakout", "signal_ma5_breakdown", "close", "ma5"),
]


def _days(count: int) -> list[date]:
    out, day = [], date(2026, 1, 5)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _signal_frame(left: str, right: str, diffs: list[float]) -> pl.DataFrame:
    n = len(diffs)
    base = {"symbol": [SYMBOL] * n, "date": _days(n), "close": [PRICE] * n}
    if left == "close":
        base[right] = [PRICE - d for d in diffs]
    else:
        base[right] = [PRICE] * n
        base[left] = [PRICE + d for d in diffs]
    return pl.DataFrame(base)


@pytest.mark.parametrize("case", list(CASES))
@pytest.mark.parametrize(("up", "down", "left", "right"), PAIRS, ids=[p[0] for p in PAIRS])
def test_published_crosses_need_both_strict_sides(case, up, down, left, right):
    diffs, expected_up, expected_down = CASES[case]
    out = compute_signals(_signal_frame(left, right, diffs), needed={up, down}).sort("date")

    assert out[up].fill_null(False).to_list() == expected_up
    assert out[down].fill_null(False).to_list() == expected_down


@pytest.mark.parametrize("case", list(CASES))
def test_matrix_crosses_follow_the_published_rule(case):
    diffs, expected_up, expected_down = CASES[case]
    right = np.full((len(diffs), 1), PRICE)
    left = (right + np.array(diffs)[:, None]).astype(np.float64)

    assert matrix.cross_above(left, right, right)[:, 0].tolist() == expected_up
    assert matrix.cross_below(left, right, right)[:, 0].tolist() == expected_down


def test_matrix_crosses_skip_missing_bars_like_the_published_rows():
    right = np.full((4, 1), PRICE)
    left = right + np.array([[-1.0], [np.nan], [0.0], [1.0]])

    assert matrix.cross_above(left, right, right)[:, 0].tolist() == [False, False, False, True]


def _market(n: int):
    closes = [PRICE] * n
    return build_market_data_matrix(
        pl.DataFrame(
            {
                "symbol": [SYMBOL] * n,
                "date": _days(n),
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [1_000_000.0] * n,
                "amount": [5e7] * n,
            }
        )
    )


@pytest.mark.parametrize(
    ("module", "left", "right", "params"),
    [
        (ma_golden_cross, "ma5", "ma20", {"use_volume_filter": False, "require_above_ma60": False}),
        (macd_golden, "macd_dif", "macd_dea", {"use_volume_filter": False}),
    ],
    ids=["ma_golden_cross", "macd_golden"],
)
@pytest.mark.parametrize("case", list(CASES))
def test_builtin_cross_strategies_use_the_shared_rule(monkeypatch, module, left, right, params, case):
    diffs, expected_up, expected_down = CASES[case]
    market = _market(len(diffs))
    series = {
        right: np.full(market.shape, PRICE, dtype=np.float32),
        left: (PRICE + np.array(diffs, dtype=np.float64)[:, None]).astype(np.float32),
    }
    original = module.matrix_feature
    monkeypatch.setattr(
        module, "matrix_feature", lambda m, name: series[name] if name in series else original(m, name)
    )

    signals = module.MATRIX_STRATEGY.compute_signals(market, params)

    assert signals.entry[:, 0].astype(bool).tolist() == expected_up
    assert signals.exit[:, 0].astype(bool).tolist() == expected_down


@pytest.mark.parametrize("case", list(CASES))
def test_close_versus_ma20_strategy_uses_the_shared_rule(monkeypatch, case):
    diffs, _, expected_down = CASES[case]
    market = _market(len(diffs))
    ma20 = (PRICE - np.array(diffs, dtype=np.float64)[:, None]).astype(np.float64)
    original = volume_price_surge.matrix_feature
    monkeypatch.setattr(
        volume_price_surge,
        "matrix_feature",
        lambda m, name: ma20 if name == "ma20" else original(m, name),
    )

    signals = volume_price_surge.MATRIX_STRATEGY.compute_signals(market, {"use_volume_filter": False})

    assert signals.exit[:, 0].astype(bool).tolist() == expected_down


# ---- limit-down recovery -------------------------------------------------------------------

def _two_days(open_: float, low: float, close: float) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["2330.TWSE"] * 2,
        "date": [date(2024, 1, 2), date(2024, 1, 3)],
        "open": [10.0, open_],
        "high": [10.1, max(open_, close)],
        "low": [9.9, low],
        "close": [10.0, close],
        "volume": [10000.0, 10000.0],
        "amount": [1.0e7, 1.0e7],
    })


INSTRUMENTS = pl.DataFrame({"symbol": ["2330.TWSE"], "float_shares": [1.0e8]})

# Previous close 10.00, limit-down price 9.00.
RECOVERY_CASES = [
    (10.50, 9.00, 10.20, True),
    (9.10, 9.004, 10.10, True),
    (9.50, 9.00, 9.50, False),
    (9.50, 9.00, 10.00, False),
    (9.50, 9.00, 9.00, False),
    (10.50, 9.10, 10.20, False),
]
RECOVERY_IDS = [
    "touched_closed_above_previous_close",
    "touched_within_tolerance",
    "opened_between_limit_and_previous_close",
    "closed_at_previous_close",
    "closed_at_limit",
    "never_touched",
]


@pytest.mark.parametrize(("open_", "low", "close", "expected"), RECOVERY_CASES, ids=RECOVERY_IDS)
def test_publish_pipeline_recovery_needs_a_close_above_the_previous_close(open_, low, close, expected):
    out = pipeline.compute_enriched(_two_days(open_, low, close), instruments=INSTRUMENTS)

    assert out.sort("date")["signal_limit_down_recovery"][-1] is expected


def test_taiwan_publish_uses_the_same_recovery_rule(monkeypatch):
    calls = []
    shared = pipeline.limit_down_recovery

    def spy(*args, **kwargs):
        calls.append(args)
        return shared(*args, **kwargs)

    monkeypatch.setattr("app.taiwan.enriched_refresh.limit_down_recovery", spy)
    service = TaiwanEnrichedRefreshService.__new__(TaiwanEnrichedRefreshService)
    service._limit_profile = lambda symbols, _sessions: pl.DataFrame(
        {"symbol": symbols, "limit_pct": [0.10] * len(symbols), "is_etf_tick": [False] * len(symbols),
         "ipo_start": [None] * len(symbols), "ipo_end": [None] * len(symbols)},
        schema_overrides={"ipo_start": pl.Date, "ipo_end": pl.Date},
    )
    # Previous close 5180, limit-down price 4665.
    closes = [5200.0, 5200.0, 4700.0, 5180.0, 5200.0]
    lows = [4665.0, 4665.004, 4665.0, 4665.0, 4670.0]
    frame = pl.DataFrame({
        "symbol": [f"S{i}" for i in range(len(closes))],
        "date": [date(2026, 10, 6)] * len(closes),
        "prev_close": [5180.0] * len(closes),
        "close": closes,
        "raw_close": closes,
        "raw_high": [max(c, 5180.0) for c in closes],
        "raw_low": lows,
    })

    out = service._add_limit_signals(frame)

    assert calls
    assert out["signal_limit_down_recovery"].to_list() == [True, True, False, False, False]


# ---- labels --------------------------------------------------------------------------------

def test_high_60d_is_labelled_as_the_highest_close():
    assert "收盘最高" in pipeline.ENRICHED_COLUMNS["high_60d"]
    assert "收盘" in pipeline.ENRICHED_COLUMNS["signal_n_day_high"]
    assert field_labels.FIELD_LABELS["high_60d"] == "60日收盤最高"
    assert field_labels.FIELD_LABELS["signal_n_day_high"] == "60日收盤新高"


def test_indicator_labels_state_the_taiwan_conventions():
    assert pipeline.ENRICHED_COLUMNS["macd_hist"].endswith("(DIF-DEA)")
    assert "对数" in pipeline.ENRICHED_COLUMNS["annual_vol_20d"]


# ---- float32 moving averages ----------------------------------------------------------------

def _close_on_ma20(base: float, step: float) -> tuple[pl.DataFrame, float]:
    # 19 closes alternate around *base*; the next close is their mean, so it equals its MA20.
    window = [round(base + (step if i % 2 else -step), 2) for i in range(19)]
    close = round(sum(window) / 19, 2)
    assert close == pytest.approx(sum(window) / 19, abs=1e-9)
    closes = [round(base * 1.2, 2)] * 5 + [round(base * 0.8, 2)] * 5 + window + [close, round(close * 1.01, 2)]
    n = len(closes)
    frame = pl.DataFrame({
        "symbol": [SYMBOL] * n, "date": _days(n), "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1e6] * n, "amount": [1e8] * n,
    })
    return frame, close


# The float32 MA20 of these windows lands one ulp away from the close (134.01, 260.48).
@pytest.mark.parametrize(("base", "step"), [(134.02, 0.19), (260.49, 0.19)])
def test_a_close_equal_to_the_matrix_ma20_crosses_as_on_the_publish_path(base, step):
    frame, close = _close_on_ma20(base, step)
    published = compute_signals(pipeline.compute_indicators(frame)).sort("date")
    market = build_market_data_matrix(frame)
    ma20 = matrix.matrix_feature(market, "ma20")
    assert published["ma20"][-2] == pytest.approx(close, abs=1e-9)

    assert matrix.cross_side(market.close, ma20, market.close)[-2, 0] == 0
    for name, rule in (("signal_ma20_breakout", matrix.cross_above), ("signal_ma20_breakdown", matrix.cross_below)):
        expected = published[name].fill_null(False).to_list()
        assert rule(market.close, ma20, market.close)[:, 0].tolist() == expected, name
