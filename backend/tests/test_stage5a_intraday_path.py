"""The intraday incremental path (compute_enriched_today) follows the D8 conventions.

KD keeps K on a flat 9-bar window, RSI is 50 with no gain and no loss, annual_vol_20d is the
sample deviation of 20 log returns times sqrt(252), high_60d / low_60d are closes, and a cross
needs yesterday strictly on the other side.
"""

from __future__ import annotations

import math
from datetime import date

import polars as pl
import pytest

from app.indicators.pipeline import compute_enriched_today

pytestmark = pytest.mark.usefixtures("deny_network")

SYMBOL = "600001.SH"
PREV_CLOSE = 10.0


def _state(**overrides) -> pl.DataFrame:
    state = {
        "symbol": SYMBOL,
        "ema5": 10.0, "ema10": 10.0, "ema20": 10.0, "ema30": 10.0, "ema60": 10.0,
        "macd_dea": 0.0, "kdj_k": 50.0, "kdj_d": 50.0, "atr_14": 0.2,
        "close": PREV_CLOSE, "high": 10.1, "low": 9.9,
        "_ema12": 10.0, "_ema26": 10.0, "_adj_factor": 1.0,
        "_vol_19d_pct_sum": 0.0, "_vol_19d_pct_sq_sum": 0.0,
        "_ma5_partial_sum": 40.0, "_ma10_partial_sum": 90.0, "_ma20_partial_sum": 190.0,
        "_ma30_partial_sum": 290.0, "_ma60_partial_sum": 590.0,
        "_boll_partial_sum": 190.0, "_boll_partial_sq_sum": 1900.0,
        "_high_59d": 10.0, "_low_59d": 10.0,
        "_close_5d_ago": 10.0, "_close_10d_ago": 10.0, "_close_20d_ago": 10.0,
        "_close_30d_ago": 10.0, "_close_60d_ago": 10.0,
        "_vol_ma5_partial_sum": 4000.0, "_vol_ma10_partial_sum": 9000.0, "_vol_ma5_prev_sum": 5000.0,
        "_kdj_8d_low": 9.9, "_kdj_8d_high": 10.1, "_window_len": 59,
        **{f"_rsi_avg_{side}_{n}": 0.01 for side in ("gain", "loss") for n in (6, 14, 24)},
    }
    state.update(overrides)
    return pl.DataFrame([state])


def _today(open_: float, high: float, low: float, close: float) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [SYMBOL], "date": [date(2026, 7, 30)],
        "open": [open_], "high": [high], "low": [low], "close": [close],
        "volume": [1000.0], "amount": [close * 1000.0],
    })


def _previous(**values) -> pl.DataFrame:
    row = {
        "symbol": SYMBOL, "ma5": 10.0, "ma10": 10.0, "ma20": 10.0, "ma60": 10.0,
        "macd_dif": 0.0, "macd_dea": 0.0, "boll_upper": 10.2, "boll_lower": 9.8, "close": PREV_CLOSE,
    }
    row.update(values)
    return pl.DataFrame([row])


def _run(state: pl.DataFrame, today: pl.DataFrame, previous: pl.DataFrame | None = None) -> dict:
    out = compute_enriched_today(state, _previous() if previous is None else previous, today)
    return out.row(0, named=True)


def test_kd_keeps_k_on_a_flat_window():
    state = _state(kdj_k=60.0, kdj_d=40.0, _kdj_8d_low=10.0, _kdj_8d_high=10.0)
    row = _run(state, _today(10.0, 10.0, 10.0, 10.0))

    assert row["kdj_k"] == pytest.approx(60.0, abs=1e-12)
    assert row["kdj_d"] == pytest.approx(2 / 3 * 40.0 + 60.0 / 3, abs=1e-12)


def test_rsi_is_50_without_gains_or_losses():
    zero = {f"_rsi_avg_{side}_{n}": 0.0 for side in ("gain", "loss") for n in (6, 14, 24)}
    row = _run(_state(**zero), _today(10.0, 10.0, 10.0, 10.0))

    assert [row[f"rsi_{n}"] for n in (6, 14, 24)] == [50.0, 50.0, 50.0]


def test_rsi_is_100_with_gains_only():
    no_loss = {f"_rsi_avg_loss_{n}": 0.0 for n in (6, 14, 24)}
    row = _run(_state(**no_loss), _today(10.0, 10.2, 10.0, 10.2))

    assert [row[f"rsi_{n}"] for n in (6, 14, 24)] == [100.0, 100.0, 100.0]


def test_annual_volatility_is_the_sample_deviation_of_log_returns():
    history = [0.01 if t % 2 == 0 else -0.02 for t in range(19)]
    today_return = math.log(10.1 / PREV_CLOSE)
    returns = [*history, today_return]
    mean = sum(returns) / 20
    expected = math.sqrt(sum((r - mean) ** 2 for r in returns) / 19) * math.sqrt(252)
    state = _state(_vol_19d_pct_sum=sum(history), _vol_19d_pct_sq_sum=sum(r * r for r in history))

    row = _run(state, _today(10.0, 10.3, 9.9, 10.1))

    assert row["annual_vol_20d"] == pytest.approx(expected, rel=1e-12)


def test_60_day_extremes_use_the_close():
    row = _run(_state(_high_59d=10.2, _low_59d=9.8), _today(10.0, 12.0, 8.0, 10.5))

    assert row["high_60d"] == 10.5
    assert row["low_60d"] == 9.8


@pytest.mark.parametrize(
    ("previous_close", "expected"),
    [(9.9, True), (PREV_CLOSE, False), (PREV_CLOSE - 1e-12, False)],
    ids=["from_strictly_below", "from_the_line", "from_float_noise"],
)
def test_a_cross_needs_yesterday_strictly_on_the_other_side(previous_close, expected):
    row = _run(_state(), _today(10.5, 10.6, 10.4, 10.5), _previous(close=previous_close, ma20=PREV_CLOSE))

    assert row["signal_ma20_breakout"] is expected
