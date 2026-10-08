"""compute_enriched_today moves the recursive history state onto today's scale by _today_factor.

The expected row always comes from the same intraday code run on history that is already
on today's scale with no factor to apply.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.indicators.pipeline import compute_enriched_today, compute_indicators
from app.tickflow.repository import DataStore, KlineRepository

SYMBOL = "2379.TWSE"
LATEST = date(2026, 9, 7)
TODAY = date(2026, 9, 8)
TODAY_BAR = {"open": 690.0, "high": 700.0, "low": 680.0, "close": 692.0, "volume": 3_000_000.0}


def _raw_history(last: date = LATEST) -> list[dict]:
    rows = []
    first = LATEST - timedelta(days=99)
    for offset in range((last - first).days + 1):
        d = first + timedelta(days=offset)
        if d == TODAY:
            bar = TODAY_BAR
        else:
            close = 700.0 + 12.0 * math.sin(offset / 3.0) + offset * 0.3
            bar = {"open": close - 2.0, "high": close + 5.0, "low": close - 6.0, "close": close,
                   "volume": 1_000_000.0 + 1000.0 * offset}
        rows.append({"symbol": SYMBOL, "date": d, **bar, "amount": bar["close"] * bar["volume"]})
    return rows


def _published(scale: float, volume_scale: float, *, last: date = LATEST, raw_moves: bool = True) -> pl.DataFrame:
    """History before TODAY divided by *scale*, its volume multiplied by *volume_scale*.

    raw_moves=True gives the frame the intraday code must see as already adjusted (its newest
    bar unadjusted, so no factor applies); raw_moves=False is what a publish really stores, raw_*
    left as traded.
    """
    rows = []
    for r in _raw_history(last):
        s, v = (scale, volume_scale) if r["date"] < TODAY else (1.0, 1.0)
        o, h, lo, c = (r[k] / s for k in ("open", "high", "low", "close"))
        raw = (c, h, lo) if raw_moves else (r["close"], r["high"], r["low"])
        rows.append({
            **r, "open": o, "high": h, "low": lo, "close": c,
            "raw_close": raw[0], "raw_high": raw[1], "raw_low": raw[2], "volume": r["volume"] * v,
        })
    return compute_indicators(pl.DataFrame(rows).sort(["symbol", "date"]))


def _repo(tmp_path: Path, history: pl.DataFrame, baseline: date = LATEST) -> KlineRepository:
    repo = KlineRepository(DataStore(tmp_path))
    repo._enriched_history_cache = history
    partition = tmp_path / "kline_daily_enriched" / f"date={baseline.isoformat()}"
    partition.mkdir(parents=True)
    history.filter(pl.col("date") == baseline).select(
        "symbol", "date", "open", "high", "low", "close", "volume", "amount", "raw_close", "raw_high", "raw_low",
        pl.lit(0, dtype=pl.UInt32).alias("consecutive_limit_ups"),
        pl.lit(0, dtype=pl.UInt32).alias("consecutive_limit_downs"),
    ).write_parquet(partition / "part.parquet")
    repo._build_live_agg(baseline)
    return repo


def _today(prev_close: float, day: date = TODAY, **factors: float) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [SYMBOL], "date": [day], **{k: [v] for k, v in TODAY_BAR.items()},
        "amount": [692.0 * 3_000_000.0], "prev_close": [prev_close],
        **{name: [value] for name, value in factors.items()},
    })


def _intraday(tmp_path: Path, history: pl.DataFrame, today: pl.DataFrame, baseline: date = LATEST) -> pl.DataFrame:
    repo = _repo(tmp_path, history, baseline)
    prev = history.filter(pl.col("date") == baseline)
    return compute_enriched_today(repo.get_live_agg(), prev, today)


def _assert_same(got: pl.DataFrame, expected: pl.DataFrame) -> None:
    got_row, expected_row = got.row(0, named=True), expected.row(0, named=True)
    for name, value in expected_row.items():
        if name == "ex_rights":
            continue
        if isinstance(value, float):
            assert got_row[name] == pytest.approx(value, rel=1e-9, abs=1e-9), name
        else:
            assert got_row[name] == value, name


@pytest.mark.parametrize(("factor", "volume_factor"), [(729.0 / 704.0, 1.0), (4.0, 4.0)])
def test_todays_factor_moves_the_history_state_onto_todays_scale(tmp_path, factor, volume_factor):
    old_scale = _published(1.0, 1.0)
    pre_close = old_scale.filter(pl.col("date") == LATEST)["raw_close"][0]
    ref = pre_close / factor

    got = _intraday(
        tmp_path / "live", old_scale,
        _today(ref, _today_factor=factor, _today_volume_factor=volume_factor),
    )
    expected = _intraday(tmp_path / "republished", _published(factor, volume_factor), _today(ref))

    _assert_same(got, expected)
    row = got.row(0, named=True)
    assert (row["close"], row["raw_close"], row["volume"]) == (692.0, 692.0, 3_000_000.0)
    assert row["change_pct"] == pytest.approx(692.0 / ref - 1)
    assert row["ex_rights"] is True
    assert not {"_today_factor", "_today_volume_factor"} & set(got.columns)


def test_the_state_does_not_move_without_an_event_today(tmp_path):
    """Regression guard: a factor of 1 (or no factor column) computes exactly as before."""
    history = _published(1.0, 1.0)

    plain = _intraday(tmp_path / "plain", history, _today(705.0))
    neutral = _intraday(tmp_path / "neutral", history, _today(705.0, _today_factor=1.0, _today_volume_factor=1.0))

    assert neutral.select(plain.columns).equals(plain)
    assert plain["ex_rights"].to_list() == [False]
