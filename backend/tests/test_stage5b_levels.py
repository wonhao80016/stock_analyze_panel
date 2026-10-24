"""Key price levels: completed-bar pivots, gap fills, Keltner, short histories, ticks and labels."""

from __future__ import annotations

import itertools
import math
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.api import stock_analysis
from app.indicators import levels as levels_module
from app.indicators.levels import LEVEL_TYPES, compute_levels, summarize_levels
from app.taiwan.market_rules import TickSizeModel
from app.taiwan.realtime.calendar import TAIPEI_TZ
from app.taiwan.realtime.models import MarketStatus

TODAY = date(2026, 10, 23)


def _dates(n: int, last: date = TODAY) -> list[date]:
    out, day = [], last
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day -= timedelta(days=1)
    return out[::-1]


def _frame(bars: list[tuple[float, float, float, float]], **extra) -> pl.DataFrame:
    """Bars are (open, high, low, close), oldest first, ending on TODAY."""
    data = {
        "date": _dates(len(bars)),
        "open": [b[0] for b in bars],
        "high": [b[1] for b in bars],
        "low": [b[2] for b in bars],
        "close": [b[3] for b in bars],
        "volume": [1000.0] * len(bars),
    }
    data.update(extra)
    return pl.DataFrame(data)


def _wave(n: int, base: float, amp: float = 0.04) -> list[tuple[float, float, float, float]]:
    bars, prev = [], None
    for t in range(n):
        close = base * (1 + amp * math.sin(t / 5) + 0.02 * math.sin(t / 17))
        open_ = close if prev is None else prev
        spread = base * 0.006 * (1 + 0.5 * math.sin(t / 3))
        bars.append((open_, max(open_, close) + spread, min(open_, close) - spread, close))
        prev = close
    return bars


def _get_levels(monkeypatch, df: pl.DataFrame, asset_type: str, status: MarketStatus,
                now: datetime | None = None) -> dict:
    now = now or datetime(TODAY.year, TODAY.month, TODAY.day, 10, 0, tzinfo=TAIPEI_TZ)
    monkeypatch.setattr(stock_analysis, "taipei_now", lambda: now, raising=False)
    monkeypatch.setattr(stock_analysis, "get_market_status", lambda *a, **k: status, raising=False)
    repo = MagicMock()
    repo.resolve_asset_type.return_value = asset_type
    repo.get_daily_asset.return_value = df
    request = MagicMock()
    request.app.state.repo = repo
    return stock_analysis.get_levels(request, symbol="9999.TWSE", days=120)


def _pivot(response: dict) -> dict[str, float]:
    return {p["label"].split()[-1]: p["value"] for p in response["levels"]["pivot"]}


def _classic_pivot(h: float, low: float, c: float) -> dict[str, float]:
    p = (h + low + c) / 3
    return {"P": p, "R1": 2 * p - low, "S1": 2 * p - h, "R2": p + (h - low), "S2": p - (h - low)}


# B020 -------------------------------------------------------------------------------------------

_PIVOT_BARS = [(10.0, 10.5, 9.5, 10.0)] * 29 + [(10.0, 11.0, 9.0, 10.4), (10.4, 12.0, 10.2, 11.8)]


@pytest.mark.parametrize(
    "status", [MarketStatus.PRE_OPEN, MarketStatus.OPEN, MarketStatus.CLOSING_AUCTION]
)
def test_pivot_skips_the_live_bar_during_the_session(monkeypatch, status):
    response = _get_levels(monkeypatch, _frame(_PIVOT_BARS), "index", status)
    expected = _classic_pivot(11.0, 9.0, 10.4)
    assert {k: _pivot(response)[k] for k in expected} == {k: round(v, 2) for k, v in expected.items()}


@pytest.mark.parametrize("status", [MarketStatus.POST_CLOSE, MarketStatus.CLOSED])
def test_pivot_uses_todays_bar_once_it_is_complete(monkeypatch, status):
    now = datetime(TODAY.year, TODAY.month, TODAY.day, 15, 0, tzinfo=TAIPEI_TZ)
    response = _get_levels(monkeypatch, _frame(_PIVOT_BARS), "index", status, now)
    expected = _classic_pivot(12.0, 10.2, 11.8)
    assert {k: _pivot(response)[k] for k in expected} == {k: round(v, 2) for k, v in expected.items()}


def test_pivot_uses_a_previous_day_last_bar_during_the_session(monkeypatch):
    """The newest bar is yesterday's when no live row was merged: it is complete."""
    now = datetime(2026, 10, 26, 10, 0, tzinfo=TAIPEI_TZ)
    response = _get_levels(monkeypatch, _frame(_PIVOT_BARS), "index", MarketStatus.OPEN, now)
    assert _pivot(response)["P"] == round(_classic_pivot(12.0, 10.2, 11.8)["P"], 2)


# B021 -------------------------------------------------------------------------------------------

def _gap_frame(after: list[tuple[float, float, float, float]]) -> pl.DataFrame:
    """An upward gap between 10.0 (day 3 high) and 11.0 (day 4 low), then *after*."""
    before = [(9.5, 9.8, 9.2, 9.6), (9.6, 9.9, 9.3, 9.7), (9.7, 10.0, 9.4, 9.8)]
    return _frame([*before, (11.2, 11.6, 11.0, 11.4), *after])


def _gaps(df: pl.DataFrame, label: str) -> list[float]:
    return [p["value"] for p in compute_levels(df)["gap"] if p["label"] == label]


def test_a_partial_fill_keeps_the_gap_and_shrinks_it():
    df = _gap_frame([(11.4, 11.6, 10.5, 11.3), (11.3, 11.8, 11.1, 11.6)])
    assert _gaps(df, "向上缺口") == [round((10.0 + 10.5) / 2, 2)]


def test_touching_the_gap_edge_keeps_the_whole_gap():
    df = _gap_frame([(11.4, 11.6, 11.0, 11.3), (11.3, 11.8, 11.1, 11.6)])
    assert _gaps(df, "向上缺口") == [round((10.0 + 11.0) / 2, 2)]


def test_a_full_fill_removes_the_gap():
    df = _gap_frame([(11.4, 11.6, 10.0, 10.3), (10.3, 10.8, 10.1, 10.6)])
    assert _gaps(df, "向上缺口") == []


def test_a_downward_jump_across_the_gap_fills_it():
    df = _gap_frame([(11.4, 11.6, 11.3, 11.5), (9.6, 9.8, 9.4, 9.5)])
    assert _gaps(df, "向上缺口") == []


def test_a_later_gap_further_up_keeps_the_earlier_gap():
    df = _gap_frame([(11.4, 11.7, 11.2, 11.6), (12.5, 12.9, 12.4, 12.8)])
    assert sorted(_gaps(df, "向上缺口")) == [round((10.0 + 11.0) / 2, 2), round((11.7 + 12.4) / 2, 2)]


def test_a_downward_gap_is_filled_symmetrically():
    before = [(11.5, 11.8, 11.2, 11.4), (11.4, 11.7, 11.1, 11.3), (11.3, 11.6, 11.0, 11.2)]
    gap = [(9.8, 10.0, 9.6, 9.7)]
    partial = _frame(before + gap + [(9.7, 10.4, 9.6, 9.9), (9.9, 10.1, 9.5, 9.6)])
    edge = _frame(before + gap + [(9.7, 10.0, 9.6, 9.9), (9.9, 10.0, 9.5, 9.6)])
    full = _frame(before + gap + [(9.7, 11.0, 9.6, 9.9), (9.9, 10.1, 9.5, 9.6)])
    jump = _frame(before + gap + [(9.7, 9.9, 9.6, 9.8), (11.4, 11.6, 11.3, 11.5)])
    assert _gaps(partial, "向下缺口") == [round((10.4 + 11.0) / 2, 2)]
    assert _gaps(edge, "向下缺口") == [round((10.0 + 11.0) / 2, 2)]
    assert _gaps(full, "向下缺口") == []
    assert _gaps(jump, "向下缺口") == []


# B118 -------------------------------------------------------------------------------------------

_SIMPLIFIED = set("压撑枢轴区轨关带长动当价无数据补线档极")


def _full_frame() -> pl.DataFrame:
    bars = _wave(260, 40.0)
    df = _frame(bars)
    return df.with_columns(
        pl.col("close").rolling_mean(20).alias("ma20"),
        pl.col("close").rolling_mean(60).alias("ma60"),
        (pl.col("close").rolling_mean(20) + 2 * pl.col("close").rolling_std(20)).alias("boll_upper"),
        (pl.col("close").rolling_mean(20) - 2 * pl.col("close").rolling_std(20)).alias("boll_lower"),
        ((pl.col("high") - pl.col("low")).rolling_mean(14)).alias("atr_14"),
        pl.lit(1.5).alias("turnover_rate"),
    )


def test_labels_and_summary_are_traditional_chinese():
    out = compute_levels(_full_frame())
    labels = [p["label"] for group in out.values() for p in group]
    assert len(labels) > 20
    text = "".join(labels) + "".join(LEVEL_TYPES.values()) + summarize_levels(out, 40.0)
    text += summarize_levels({}, None)
    assert not (_SIMPLIFIED & set(text)), sorted(_SIMPLIFIED & set(text))
    assert {"樞軸位 P", "壓力位 R1", "支撐位 S1"} <= set(labels)


# B119 / B122 ------------------------------------------------------------------------------------

def _ema(values: list[float], span: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    alpha = 2 / (span + 1)
    state = sum(values[:span]) / span
    out[span - 1] = state
    for i in range(span, len(values)):
        state = alpha * values[i] + (1 - alpha) * state
        out[i] = state
    return out


def _atr(df: pl.DataFrame, n: int) -> list[float | None]:
    h, low, c = df["high"].to_list(), df["low"].to_list(), df["close"].to_list()
    tr = [max(h[i] - low[i], abs(h[i] - c[i - 1]), abs(low[i] - c[i - 1])) for i in range(1, len(h))]
    out: list[float | None] = [None] * len(h)
    state = sum(tr[:n]) / n
    out[n] = state
    for i in range(n, len(tr)):
        state = (state * (n - 1) + tr[i]) / n
        out[i + 1] = state
    return out


def test_short_keltner_is_ema20_plus_minus_two_atr10():
    df = _full_frame()
    ema20 = _ema(df["close"].to_list(), 20)[-1]
    atr10 = _atr(df, 10)[-1]
    band = {p["label"]: p["value"] for p in compute_levels(df)["keltner_s"]}
    assert band == {"短期通道上軌": round(ema20 + 2 * atr10, 2), "短期通道下軌": round(ema20 - 2 * atr10, 2)}


def test_keltner_series_matches_the_levels():
    df = _full_frame()
    series = stock_analysis._build_series(df)
    out = compute_levels(df)
    for key in ("keltner_s", "keltner_m", "keltner_l"):
        values = sorted(p["value"] for p in out[key])
        assert values == sorted([series[key]["lower"][-1], series[key]["upper"][-1]]), key
    ema60 = _ema(df["close"].to_list(), 60)[-1]
    atr10 = _atr(df, 10)[-1]
    assert series["keltner_m"]["upper"][-1] == round(ema60 + 2.5 * atr10, 2)


# B120 -------------------------------------------------------------------------------------------

def _new_listing() -> pl.DataFrame:
    """Twelve sessions: the enriched ATR, moving averages and Bollinger bands are still null."""
    bars = _wave(12, 30.0, amp=0.08)
    null = pl.Series([None] * 12, dtype=pl.Float64)
    return _frame(bars, atr_14=null, ma20=null, ma60=null, boll_upper=null, boll_lower=null)


def test_a_new_listing_still_reports_the_levels_its_history_supports():
    out = compute_levels(_new_listing())
    for key in ("pivot", "fib", "round"):
        assert out[key], key
    for key in ("atr_stop", "keltner_s", "boll"):
        assert out[key] == [], key


def test_one_failing_group_leaves_the_others(monkeypatch):
    def boom(df):
        raise ValueError("boom")

    monkeypatch.setattr(levels_module, "_fibonacci_levels", boom)
    out = compute_levels(_full_frame())
    assert out["fib"] == []
    assert out["pivot"] and out["extreme"] and out["keltner_s"]


# B121 -------------------------------------------------------------------------------------------

def _on_grid(value: float, tick: float) -> bool:
    steps = value / tick
    return abs(steps - round(steps)) < 1e-6


@pytest.mark.parametrize(("asset_type", "base"), [("stock", 120.0), ("stock", 640.0), ("etf", 75.0)])
def test_levels_snap_to_the_taiwan_tick(monkeypatch, asset_type, base):
    df = _frame(_wave(260, base))
    response = _get_levels(monkeypatch, df, asset_type, MarketStatus.POST_CLOSE)
    from app.taiwan.market_rules import TickSizeClass

    cls = TickSizeClass.ETF if asset_type == "etf" else TickSizeClass.ORDINARY_STOCK
    points = [p for group in response["levels"].values() for p in group]
    assert len(points) > 20
    off = [(p["label"], p["value"]) for p in points
           if not _on_grid(p["value"], TickSizeModel.get_tick_size(p["value"], cls))]
    assert off == []


def test_resistance_rounds_up_and_support_down(monkeypatch):
    bars = [(120.0, 121.0, 119.0, 120.0)] * 29 + [(120.0, 123.3, 118.1, 121.7)]
    response = _get_levels(monkeypatch, _frame(bars), "stock", MarketStatus.POST_CLOSE)
    raw = _classic_pivot(123.3, 118.1, 121.7)
    pivot = _pivot(response)
    assert pivot["R1"] == math.ceil(raw["R1"] / 0.5) * 0.5
    assert pivot["S1"] == math.floor(raw["S1"] / 0.5) * 0.5
    assert pivot["P"] == round(raw["P"] / 0.5) * 0.5


def test_the_tick_follows_the_raw_price_scale(monkeypatch):
    """Adjusted 49 is raw 98, whose tick is 0.1: in adjusted terms the grid is 0.05."""
    bars = _wave(60, 49.0, amp=0.005)
    df = _frame(bars).with_columns((pl.col("close") * 2).alias("raw_close"))
    response = _get_levels(monkeypatch, df, "stock", MarketStatus.POST_CLOSE)
    points = [p for group in response["levels"].values() for p in group]
    assert points
    assert [(p["label"], p["value"]) for p in points if not _on_grid(p["value"] * 2, 0.1)] == []


def test_index_levels_keep_two_decimals(monkeypatch):
    df = _frame(_wave(260, 120.0))
    response = _get_levels(monkeypatch, df, "index", MarketStatus.POST_CLOSE)
    values = [p["value"] for group in response["levels"].values() for p in group]
    assert any(not _on_grid(v, 0.5) for v in values)
    assert all(v == round(v, 2) for v in values)


# B123 -------------------------------------------------------------------------------------------

def test_aggregation_does_not_chain_and_reports_the_group_mean():
    assert levels_module._aggregate_levels([100.0, 100.9, 101.8, 102.7], 0.01) == pytest.approx(
        [100.45, 102.25]
    )


# Review follow-ups ------------------------------------------------------------------------------

def _swing_frame(base: float, peaks: dict[int, float], troughs: dict[int, float]) -> pl.DataFrame:
    """Flat bars spanning base..base+1 with the given swing highs and lows; the close is 100."""
    bars = []
    for i in range(30):
        low = peaks[i] - 1.0 if i in peaks else troughs.get(i, base)
        bars.append((low + 0.5, low + 1.0, low, low + 0.5))
    bars[-1] = (100.0, 100.0, 100.0, 100.0)
    return _frame(bars)


def test_swing_highs_straddling_the_close_keep_the_one_above_it():
    df = _swing_frame(97.0, {7: 99.5, 18: 100.4}, {})
    assert [p["value"] for p in compute_levels(df)["extreme"] if p["label"] == "前高"] == [100.4]


def test_swing_lows_straddling_the_close_keep_the_one_below_it():
    df = _swing_frame(102.0, {}, {12: 100.5, 20: 99.6})
    assert [p["value"] for p in compute_levels(df)["extreme"] if p["label"] == "前低"] == [99.6]


def test_complete_data_fills_every_group():
    df = _full_frame()
    shift = [0.0] * (df.height - 20) + [3.0] * 20
    df = df.with_columns(*(pl.col(c) + pl.Series(shift) for c in ("open", "high", "low", "close")))
    out = compute_levels(df)
    assert [key for key in LEVEL_TYPES if not out[key]] == []


@pytest.mark.parametrize(
    ("close", "step"),
    [(9.3, 0.5), (30.0, 1.0), (73.0, 5.0), (300.0, 10.0), (720.0, 50.0), (1530.0, 100.0)],
)
def test_round_numbers_step_by_the_taiwan_tick_tier(close, step):
    df = _frame([(close, close, close, close)] * 5)
    values = sorted(p["value"] for p in compute_levels(df)["round"])
    assert len(values) >= 2
    assert all(_on_grid(v, step) for v in values)
    assert min(b - a for a, b in itertools.pairwise(values)) == pytest.approx(step)


def test_pivot_uses_todays_bar_on_a_non_trading_day(monkeypatch):
    response = _get_levels(monkeypatch, _frame(_PIVOT_BARS), "index", MarketStatus.NON_TRADING_DAY)
    assert _pivot(response)["P"] == round(_classic_pivot(12.0, 10.2, 11.8)["P"], 2)


@pytest.mark.parametrize(
    ("asset", "value", "side", "expected"),
    [
        ("stock", 9.991, "resistance", 10.0),
        ("stock", 10.01, "resistance", 10.05),
        ("stock", 49.951, "resistance", 50.0),
        ("stock", 49.999, "support", 49.95),
        ("stock", 50.01, "resistance", 50.1),
        ("stock", 50.04, "support", 50.0),
        ("stock", 99.951, "resistance", 100.0),
        ("stock", 99.97, "support", 99.9),
        ("stock", 100.01, "resistance", 100.5),
        ("stock", 100.4, "support", 100.0),
        ("etf", 49.991, "resistance", 50.0),
        ("etf", 50.02, "resistance", 50.05),
        ("etf", 50.04, "support", 50.0),
    ],
)
def test_snapping_across_a_tick_boundary(asset, value, side, expected):
    from app.taiwan.market_rules import TickSizeClass

    cls = TickSizeClass.ETF if asset == "etf" else TickSizeClass.ORDINARY_STOCK
    snap = levels_module._tick_snapper(_frame([(50.0, 50.0, 50.0, 50.0)]), cls)
    assert snap(value, side) == pytest.approx(expected)
