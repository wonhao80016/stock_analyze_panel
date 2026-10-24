"""Taiwan indicator conventions (D8) on the publish pipeline and the strategy matrix.

MACD histogram = DIF - DEA; Bollinger bands use the population deviation of 20 closes;
annual_vol_20d is the sample deviation of 20 log returns times sqrt(252); KD keeps K when the
9-bar high equals the 9-bar low; RSI is 50 with no gain and no loss, 100 with gains only.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.backtest.matrix import build_market_data_matrix, matrix_feature
from app.indicators import pipeline
from app.indicators.pipeline import compute_indicators

pytestmark = pytest.mark.usefixtures("deny_network")

SYMBOL = "1101.TWSE"


def _days(count: int) -> list[date]:
    out, day = [], date(2026, 1, 5)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _frame(closes: list[float], highs: list[float] | None = None, lows: list[float] | None = None) -> pl.DataFrame:
    n = len(closes)
    return pl.DataFrame(
        {
            "symbol": [SYMBOL] * n,
            "date": _days(n),
            "open": closes,
            "high": highs if highs is not None else closes,
            "low": lows if lows is not None else closes,
            "close": closes,
            "volume": [1_000_000.0] * n,
            "amount": [5e7] * n,
        }
    )


def _published(frame: pl.DataFrame, name: str) -> list:
    return compute_indicators(frame).sort("date")[name].to_list()


def _matrix(frame: pl.DataFrame, name: str) -> np.ndarray:
    market = build_market_data_matrix(frame)
    return matrix_feature(market, name)[:, market.symbols.index(SYMBOL)].astype(np.float64)


# 33 flat closes and one jump: EMA12 and EMA26 both start at 10 and DEA averages nine DIF values.
MACD_CLOSES = [10.0] * 33 + [20.0]
MACD_DIF = 10.0 * 2 / 13 - 10.0 * 2 / 27
MACD_DEA = MACD_DIF / 9
MACD_HIST = MACD_DIF - MACD_DEA


def test_macd_histogram_is_dif_minus_dea_on_the_publish_path():
    frame = _frame(MACD_CLOSES)
    assert _published(frame, "macd_dif")[-1] == pytest.approx(MACD_DIF, rel=1e-12)
    assert _published(frame, "macd_dea")[-1] == pytest.approx(MACD_DEA, rel=1e-12)
    assert _published(frame, "macd_hist")[-1] == pytest.approx(MACD_HIST, rel=1e-12)


def test_macd_histogram_is_dif_minus_dea_on_the_matrix_path():
    assert _matrix(_frame(MACD_CLOSES), "macd_hist")[-1] == pytest.approx(MACD_HIST, rel=1e-5)


BOLL_CLOSES = [float(c) for c in range(1, 21)]
BOLL_MIDDLE = 10.5
BOLL_DEVIATION = math.sqrt((20**2 - 1) / 12)


@pytest.mark.parametrize(("name", "sign"), [("boll_upper", 1), ("boll_lower", -1)])
def test_bollinger_bands_use_the_population_deviation(name, sign):
    expected = BOLL_MIDDLE + sign * 2 * BOLL_DEVIATION
    frame = _frame(BOLL_CLOSES)
    assert _published(frame, name)[-1] == pytest.approx(expected, rel=1e-12)
    assert _matrix(frame, name)[-1] == pytest.approx(expected, rel=1e-5)


VOL_RETURNS = [0.01 if t % 2 == 0 else -0.02 for t in range(20)]
VOL_CLOSES = [100.0 * math.exp(sum(VOL_RETURNS[:t])) for t in range(21)]
_VOL_MEAN = sum(VOL_RETURNS) / 20
VOL_EXPECTED = math.sqrt(sum((r - _VOL_MEAN) ** 2 for r in VOL_RETURNS) / 19) * math.sqrt(252)


def test_annual_volatility_uses_log_returns_on_the_publish_path():
    values = _published(_frame(VOL_CLOSES), "annual_vol_20d")
    assert values[19] is None
    assert values[20] == pytest.approx(VOL_EXPECTED, rel=1e-9)


def test_annual_volatility_uses_log_returns_on_the_matrix_path():
    values = _matrix(_frame(VOL_CLOSES), "annual_vol_20d")
    assert np.isnan(values[19])
    assert values[20] == pytest.approx(VOL_EXPECTED, rel=1e-4)


FLAT = [10.0] * 12


@pytest.mark.parametrize("name", ["kdj_k", "kdj_d", "kdj_j"])
def test_kd_of_a_flat_first_window_starts_at_50(name):
    frame = _frame(FLAT)
    published = _published(frame, name)
    assert published[:8] == [None] * 8
    assert published[8:] == pytest.approx([50.0] * 4, abs=1e-12)
    assert _matrix(frame, name)[8:] == pytest.approx([50.0] * 4, abs=1e-4)


# Nine moving bars, then nine identical bars: from bar 17 the 9-bar high equals the 9-bar low.
KD_CLOSES = [10.0, 11.0, 12.0, 11.5, 13.0, 12.5, 14.0, 13.0, 12.0] + [12.5] * 11
KD_HIGHS = [c + 0.5 for c in KD_CLOSES[:9]] + [12.5] * 11
KD_LOWS = [c - 0.5 for c in KD_CLOSES[:9]] + [12.5] * 11


def _kd_reference() -> tuple[list[float | None], list[float | None]]:
    k_out: list[float | None] = [None] * 8
    d_out: list[float | None] = [None] * 8
    k = d = 50.0
    for t in range(8, len(KD_CLOSES)):
        low9, high9 = min(KD_LOWS[t - 8 : t + 1]), max(KD_HIGHS[t - 8 : t + 1])
        rsv = k if high9 == low9 else 100 * (KD_CLOSES[t] - low9) / (high9 - low9)
        k = 2 / 3 * k + rsv / 3
        d = 2 / 3 * d + k / 3
        k_out.append(k)
        d_out.append(d)
    return k_out, d_out


def test_kd_keeps_k_when_the_window_is_flat_on_both_paths():
    frame = _frame(KD_CLOSES, KD_HIGHS, KD_LOWS)
    k_ref, d_ref = _kd_reference()
    k_pub, d_pub = _published(frame, "kdj_k"), _published(frame, "kdj_d")
    assert k_pub[:8] == [None] * 8
    assert k_pub[8:] == pytest.approx(k_ref[8:], rel=1e-12)
    assert d_pub[8:] == pytest.approx(d_ref[8:], rel=1e-12)
    assert k_pub[17] == pytest.approx(k_pub[16], rel=1e-12)
    assert k_pub[19] == pytest.approx(k_pub[16], rel=1e-12)
    assert _matrix(frame, "kdj_k")[8:] == pytest.approx(k_ref[8:], rel=1e-5)
    assert _matrix(frame, "kdj_d")[8:] == pytest.approx(d_ref[8:], rel=1e-5)


@pytest.mark.parametrize(
    ("closes", "expected"),
    [
        ([10.0] * 10, 50.0),
        ([10.0 + t for t in range(10)], 100.0),
        ([20.0 - t for t in range(10)], 0.0),
    ],
    ids=["no_change", "gains_only", "losses_only"],
)
def test_rsi_without_losses_or_without_changes(closes, expected):
    frame = _frame(closes)
    published = _published(frame, "rsi_6")
    assert published[:6] == [None] * 6
    assert published[6:] == [expected] * 4
    assert _matrix(frame, "rsi_6")[6:].tolist() == [expected] * 4


def test_the_definition_version_moves_past_the_stage_4_conventions():
    assert pipeline.INDICATOR_DEFINITION_VERSION == 3
