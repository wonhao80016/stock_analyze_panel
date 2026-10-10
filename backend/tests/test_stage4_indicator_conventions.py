"""Recursive indicators follow one convention on every path (D7).

EMA seeds with the SMA of its first n inputs and is null before; MACD is the EMA12 - EMA26
difference with a nine-value SMA-seeded DEA; RSI and ATR are Wilder averages seeded with the
simple mean of the first n changes or true ranges; KD starts from K = D = 50. The publish
pipeline, the strategy matrix and the macd_golden builtin must all give these values, and no
MACD cross may fire before both DEA values it compares exist.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.backtest.matrix import build_market_data_matrix, matrix_feature
from app.indicators.pipeline import compute_indicators, compute_signals
from app.strategy.builtin import macd_golden
from app.strategy.scoring import recursive_warmup_bars

pytestmark = pytest.mark.usefixtures("deny_network")

LONG = "1101.TWSE"
NEW = "6999.TPEX"
LONG_BARS = 80
NEW_BARS = 45
NEW_FROM = 30
NEW_GAPS = (8, 9, 20)
SEED_WEIGHT_LIMIT = 1e-4


def _days(count: int) -> list[date]:
    out, day = [], date(2026, 1, 5)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


DAYS = _days(LONG_BARS)


def _bars(symbol: str, count: int, phase: float) -> list[dict]:
    out, prev = [], None
    for t in range(count):
        close = 50.0 + 3.0 * math.sin(t / 4.0 + phase) + 0.15 * t
        open_ = close - 0.3 if prev is None else prev
        out.append(
            {
                "symbol": symbol,
                "open": open_,
                "high": max(open_, close) + 0.5 + 0.2 * abs(math.sin(t / 3.0)),
                "low": min(open_, close) - 0.4 - 0.1 * abs(math.cos(t / 5.0)),
                "close": close,
                "volume": 1_000_000.0 + 10_000.0 * t,
            }
        )
        prev = close
    return out


def _panel() -> pl.DataFrame:
    rows = [{**bar, "date": DAYS[t]} for t, bar in enumerate(_bars(LONG, LONG_BARS, 0.0))]
    new_days = [d for i, d in enumerate(DAYS[NEW_FROM:]) if i not in NEW_GAPS]
    rows += [
        {**bar, "date": day}
        for bar, day in zip(_bars(NEW, len(new_days), 1.3), new_days, strict=True)
    ]
    return pl.DataFrame(rows).with_columns(pl.lit(5e7).alias("amount"))


PANEL = _panel()


# ---- reference definitions in plain Python -------------------------------------------------


def _seeded(xs: list[float | None], alpha: float, seed: int) -> list[float | None]:
    """The mean of the first *seed* present inputs, then state = alpha * x + (1 - alpha) * state."""
    out: list[float | None] = [None] * len(xs)
    present = [i for i, x in enumerate(xs) if x is not None]
    if len(present) < seed:
        return out
    state = sum(xs[i] for i in present[:seed]) / seed
    out[present[seed - 1]] = state
    for i in present[seed:]:
        state = alpha * xs[i] + (1 - alpha) * state
        out[i] = state
    return out


def _from_prior(xs: list[float | None], alpha: float, prior: float) -> list[float | None]:
    out: list[float | None] = [None] * len(xs)
    state = prior
    for i, x in enumerate(xs):
        if x is not None:
            state = alpha * x + (1 - alpha) * state
            out[i] = state
    return out


def _ema(xs: list[float | None], n: int) -> list[float | None]:
    return _seeded(xs, 2.0 / (n + 1), n)


def _wilder(xs: list[float | None], n: int) -> list[float | None]:
    return _seeded(xs, 1.0 / n, n)


def _minus(a: list, b: list) -> list:
    return [None if x is None or y is None else x - y for x, y in zip(a, b, strict=True)]


def _reference(bars: list[dict]) -> dict[str, list]:
    closes = [b["close"] for b in bars]
    ref: dict[str, list] = {f"ema{n}": _ema(closes, n) for n in (5, 10, 20, 30, 60)}
    dif = _minus(_ema(closes, 12), _ema(closes, 26))
    dea = _ema(dif, 9)
    ref["macd_dif"], ref["macd_dea"] = dif, dea
    ref["macd_hist"] = [None if d is None else 2 * d for d in _minus(dif, dea)]
    changes = [None] + [closes[t] - closes[t - 1] for t in range(1, len(closes))]
    gains = [None if c is None else max(c, 0.0) for c in changes]
    losses = [None if c is None else max(-c, 0.0) for c in changes]
    for n in (6, 14, 24):
        avg_gain, avg_loss = _wilder(gains, n), _wilder(losses, n)
        ref[f"rsi_{n}"] = [
            None if g is None else 100 - 100 / (1 + g / (lo if lo != 0 else 1e-12))
            for g, lo in zip(avg_gain, avg_loss, strict=True)
        ]
    true_range = [None] + [
        max(
            bars[t]["high"] - bars[t]["low"],
            abs(bars[t]["high"] - closes[t - 1]),
            abs(bars[t]["low"] - closes[t - 1]),
        )
        for t in range(1, len(bars))
    ]
    ref["atr_14"] = _wilder(true_range, 14)
    rsv: list[float | None] = [None] * 8
    for t in range(8, len(bars)):
        low9 = min(b["low"] for b in bars[t - 8 : t + 1])
        high9 = max(b["high"] for b in bars[t - 8 : t + 1])
        rsv.append(100 * (closes[t] - low9) / (high9 - low9))
    k = _from_prior(rsv, 1.0 / 3.0, 50.0)
    d = _from_prior(k, 1.0 / 3.0, 50.0)
    ref["kdj_k"], ref["kdj_d"] = k, d
    ref["kdj_j"] = [None if a is None else 3 * a - 2 * b for a, b in zip(k, d, strict=True)]
    return ref


RECURSIVE = [
    *(f"ema{n}" for n in (5, 10, 20, 30, 60)),
    "macd_dif",
    "macd_dea",
    "macd_hist",
    "rsi_6",
    "rsi_14",
    "rsi_24",
    "atr_14",
    "kdj_k",
    "kdj_d",
    "kdj_j",
]
FIRST_VALUE = {
    **{f"ema{n}": n - 1 for n in (5, 10, 20, 30, 60)},
    "macd_dif": 25,
    "macd_dea": 33,
    "macd_hist": 33,
    **{f"rsi_{n}": n for n in (6, 14, 24)},
    "atr_14": 14,
    "kdj_k": 8,
    "kdj_d": 8,
    "kdj_j": 8,
}


def _symbol_bars(symbol: str) -> list[dict]:
    return PANEL.filter(pl.col("symbol") == symbol).sort("date").to_dicts()


def _published(symbol: str, name: str) -> list:
    out = compute_indicators(PANEL).filter(pl.col("symbol") == symbol).sort("date")
    return out[name].to_list()


@pytest.mark.parametrize("symbol", [LONG, NEW])
@pytest.mark.parametrize("name", RECURSIVE)
def test_published_values_equal_the_reference_definition(symbol, name):
    expected = _reference(_symbol_bars(symbol))[name]
    got = _published(symbol, name)

    assert [g is None for g in got] == [e is None for e in expected]
    assert [g for g in got if g is not None] == pytest.approx(
        [e for e in expected if e is not None], rel=1e-12, abs=1e-9
    )


@pytest.mark.parametrize("name", RECURSIVE)
def test_values_are_null_until_the_definition_has_its_bars(name):
    got = _published(LONG, name)

    first = FIRST_VALUE[name]
    assert got[:first] == [None] * first
    assert got[first] is not None


def _matrix_values(name: str) -> dict[str, np.ndarray]:
    market = build_market_data_matrix(PANEL)
    values = matrix_feature(market, name)
    dates = [date.fromisoformat(label[:10]) for label in market.timestamp_labels]
    out = {}
    for symbol in (LONG, NEW):
        column = values[:, market.symbols.index(symbol)]
        own = set(PANEL.filter(pl.col("symbol") == symbol)["date"].to_list())
        out[symbol] = np.array([column[t] for t, d in enumerate(dates) if d in own])
    return out


@pytest.mark.parametrize("name", RECURSIVE)
def test_the_matrix_computes_the_published_values(name):
    matrix = _matrix_values(name)
    for symbol in (LONG, NEW):
        published = np.array(
            [np.nan if v is None else v for v in _published(symbol, name)], dtype=np.float64
        )
        assert np.array_equal(np.isnan(matrix[symbol]), np.isnan(published)), symbol
        np.testing.assert_allclose(matrix[symbol], published, rtol=1e-5, atol=1e-4, err_msg=symbol)


def _listing(closes: list[float]) -> pl.DataFrame:
    days = _days(len(closes))
    return pl.DataFrame(
        {
            "symbol": [NEW] * len(closes),
            "date": days,
            "open": closes,
            "high": [c + 0.2 for c in closes],
            "low": [c - 0.2 for c in closes],
            "close": closes,
            "volume": [1_000_000.0] * len(closes),
            "amount": [5e7] * len(closes),
        }
    )


# Up on the second session, then a slow wave: a first-close seed crosses at once.
LISTING = _listing([20.0 + (0.5 if t else 0.0) + 1.5 * math.sin(t / 3.0) for t in range(60)])
# The first bar whose DEA and previous DEA both exist.
FIRST_CROSS_BAR = 34


@pytest.mark.parametrize("signal", ["signal_macd_golden", "signal_macd_dead"])
def test_no_macd_cross_is_published_during_the_warm_up(signal):
    flags = compute_signals(compute_indicators(LISTING))[signal].to_list()

    assert not any(flags[:FIRST_CROSS_BAR])
    assert any(flags[FIRST_CROSS_BAR:])


def test_macd_golden_enters_no_listing_during_the_warm_up():
    market = build_market_data_matrix(LISTING)
    signals = macd_golden.MATRIX_STRATEGY.compute_signals(market, {"use_volume_filter": False})

    entry = np.asarray(signals.entry)[:, 0].astype(bool)
    exit_ = np.asarray(signals.exit)[:, 0].astype(bool)
    assert not entry[:FIRST_CROSS_BAR].any()
    assert not exit_[:FIRST_CROSS_BAR].any()
    assert (entry | exit_)[FIRST_CROSS_BAR:].any()


def _seed_bars(alpha: float) -> int:
    return math.ceil(math.log(SEED_WEIGHT_LIMIT) / math.log(1.0 - alpha))


SEEDS = {
    **{f"ema{n}": (n, 2.0 / (n + 1)) for n in (5, 20, 60)},
    **{f"ema{n}_bias": (n, 2.0 / (n + 1)) for n in (10, 30)},
    **{f"rsi_{n}": (n + 1, 1.0 / n) for n in (6, 14, 24)},
    "atr_14": (15, 1.0 / 14),
    "atr_pct": (15, 1.0 / 14),
    "macd_dif": (26, 2.0 / 27),
    "macd_dif_pct": (26, 2.0 / 27),
}


@pytest.mark.parametrize("name", sorted(SEEDS))
def test_a_scoring_warm_up_leaves_the_seed_below_the_weight_limit(name):
    first_value_bar, alpha = SEEDS[name]

    bars_after_seed = recursive_warmup_bars(name) - first_value_bar

    assert (1.0 - alpha) ** bars_after_seed < SEED_WEIGHT_LIMIT


@pytest.mark.parametrize("name", ["macd_dea", "macd_hist", "macd_dea_pct", "macd_hist_pct"])
def test_the_dea_warm_up_follows_the_dif_warm_up(name):
    assert recursive_warmup_bars(name) >= recursive_warmup_bars("macd_dif") + _seed_bars(0.2)
