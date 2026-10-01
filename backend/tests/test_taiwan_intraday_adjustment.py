"""Intraday indicators on and after a corporate-action day match the forward-adjusted history.

The expected row always comes from the same intraday code run on history that is already
on today's scale with no factor to apply. Three moments are covered: the event day before
the evening publish (published history still on the old scale), the event day after that
publish rewrote history (the live state still ends the day before), and the next day.
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from app.indicators.pipeline import compute_enriched_today, compute_indicators
from app.services.quote_service import QuoteService
from app.taiwan.corporate_actions import TaiwanCorporateActionStore, corporate_action_schema
from app.tickflow.repository import DataStore, KlineRepository

SYMBOL = "2379.TWSE"
LATEST = date(2026, 9, 7)
TODAY = date(2026, 9, 8)
NEXT_DAY = date(2026, 9, 9)
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


def _event(symbol: str, d: date, pre: float, ref: float) -> dict:
    return {
        "symbol": symbol, "date": d, "kind": "exdiv", "pre_close": pre, "ref_price": ref, "limit_up": None,
        "limit_down": None, "open_ref": ref, "ex_factor": pre / ref, "official_label": None, "provider": "twse",
        "source": "test", "source_url": "u", "retrieved_at": None, "trade_date": d, "status": "official",
    }


@pytest.fixture
def quote_path(tmp_path, monkeypatch):
    """An event on TODAY in the table; returns a setter for the published record and today's date."""
    from app.config import settings
    from app.services import quote_service

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    pre_close = _published(1.0, 1.0).filter(pl.col("date") == LATEST)["raw_close"][0]
    ref = pre_close / (729.0 / 704.0)
    TaiwanCorporateActionStore().upsert(pl.DataFrame(
        [_event(SYMBOL, TODAY, pre_close, ref), _event("9927.TWSE", NEXT_DAY, 70.10, 65.10)],
        schema=corporate_action_schema(),
    ))

    def at(day: date, published_through: date | None) -> float:
        monkeypatch.setattr(quote_service, "cn_today", lambda: day)
        record = tmp_path / "data" / ".taiwan_enriched_factors.json"
        record.unlink(missing_ok=True)
        if published_through is not None:
            record.write_text(json.dumps({"through": published_through.isoformat()}), encoding="utf-8")
        return ref

    return at


def _through_quote_path(tmp_path: Path, history: pl.DataFrame, today: pl.DataFrame, baseline: date) -> pl.DataFrame:
    repo = _repo(tmp_path, history, baseline)
    joined = QuoteService._with_today_factors(SimpleNamespace(_repo=repo), today)
    return compute_enriched_today(repo.get_live_agg(), history.filter(pl.col("date") == baseline), joined)


def test_the_event_day_before_the_evening_publish(tmp_path, quote_path):
    ref = quote_path(TODAY, LATEST)
    factor = _published(1.0, 1.0).filter(pl.col("date") == LATEST)["raw_close"][0] / ref

    got = _through_quote_path(tmp_path / "live", _published(1.0, 1.0), _today(ref), LATEST)
    expected = _intraday(tmp_path / "expected", _published(factor, 1.0), _today(ref))

    _assert_same(got, expected)
    assert got["close"].to_list() == [692.0]


def test_the_event_day_after_the_evening_publish_applies_the_factor_once(tmp_path, quote_path):
    """The publish rewrote history onto the new scale; the live state still ends the day before."""
    ref = quote_path(TODAY, TODAY)
    factor = _published(1.0, 1.0).filter(pl.col("date") == LATEST)["raw_close"][0] / ref
    republished = _published(factor, 1.0, raw_moves=False)
    assert republished.filter(pl.col("date") == LATEST).select(pl.col("close") / pl.col("raw_close")).item() != 1.0

    got = _through_quote_path(tmp_path / "live", republished, _today(ref), LATEST)
    expected = _intraday(tmp_path / "expected", _published(factor, 1.0), _today(ref))

    _assert_same(got, expected)
    assert got["close"].to_list() == [692.0]


def test_the_day_after_the_event(tmp_path, quote_path):
    ref = quote_path(NEXT_DAY, TODAY)
    factor = _published(1.0, 1.0).filter(pl.col("date") == LATEST)["raw_close"][0] / ref
    history = _published(factor, 1.0, last=TODAY, raw_moves=False)

    got = _through_quote_path(tmp_path / "live", history, _today(692.0, NEXT_DAY), TODAY)
    expected = _intraday(tmp_path / "expected", _published(factor, 1.0, last=TODAY), _today(692.0, NEXT_DAY), TODAY)

    _assert_same(got, expected)


def test_the_quote_path_gives_taiwan_rows_a_factor_and_never_tomorrows(tmp_path, quote_path):
    quote_path(TODAY, LATEST)
    fake = SimpleNamespace(_repo=SimpleNamespace(get_live_agg_date=lambda: LATEST))
    frame = pl.DataFrame({"symbol": [SYMBOL, "9927.TWSE", "600000.SH"], "close": [692.0, 66.0, 10.0]})

    joined = QuoteService._with_today_factors(fake, frame).sort("symbol")

    assert joined.select("symbol", "_today_factor", "_today_volume_factor").rows() == [
        ("2379.TWSE", pytest.approx(729.0 / 704.0), 1.0), ("600000.SH", None, None), ("9927.TWSE", 1.0, 1.0),
    ]


def test_the_quote_path_leaves_other_markets_untouched(tmp_path, monkeypatch):
    """Regression guard: frames without Taiwan symbols never read the corporate-action table."""
    fake = SimpleNamespace(_repo=SimpleNamespace(get_live_agg_date=lambda: pytest.fail("read the Taiwan table")))
    frame = pl.DataFrame({"symbol": ["600000.SH"], "close": [10.0]})

    assert QuoteService._with_today_factors(fake, frame) is frame
