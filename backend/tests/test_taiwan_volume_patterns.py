from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.parquet import scan_enriched_parquet
from app.taiwan.corporate_actions import TaiwanCorporateActionStore, corporate_action_schema
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.enrichment.corporate_actions import EXDIV, PAR_CHANGE
from app.taiwan.volume_patterns import VOLUME_PATTERN_COLUMNS, add_volume_patterns

DAYS = [date(2026, 1, 1) + timedelta(days=i) for i in range(40)]


def _bars(
    symbol: str,
    volumes: list[float | None],
    closes: list[float] | None = None,
    opens: list[float] | None = None,
    highs: list[float] | None = None,
    changes: list[float | None] | None = None,
) -> list[dict]:
    """One row per calendar day; a None volume means the symbol has no row that day."""
    n = len(volumes)
    closes = closes or [100.0] * n
    opens = opens or closes
    highs = highs or [max(o, c) for o, c in zip(opens, closes, strict=True)]
    changes = changes or [0.05] * n
    return [
        {
            "symbol": symbol, "date": DAYS[i], "open": opens[i], "high": highs[i],
            "low": min(opens[i], closes[i]), "close": closes[i], "volume": volumes[i],
            "change_pct": changes[i],
        }
        for i in range(n)
        if volumes[i] is not None
    ]


def _frame(*rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(
        [r for group in rows for r in group],
        schema={
            "symbol": pl.String, "date": pl.Date, "open": pl.Float64, "high": pl.Float64,
            "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64, "change_pct": pl.Float64,
        },
    )


def _run(frame: pl.DataFrame, calendar: list[date] | None = None) -> pl.DataFrame:
    return add_volume_patterns(frame, DAYS if calendar is None else calendar)


def _last(frame: pl.DataFrame, column: str, symbol: str = "A.TWSE"):
    return _run(frame).filter(pl.col("symbol") == symbol).sort("date")[column].to_list()[-1]


def _series(frame: pl.DataFrame, column: str, symbol: str = "A.TWSE") -> list:
    return _run(frame).filter(pl.col("symbol") == symbol).sort("date")[column].to_list()


def test_double_volume_hits_at_exactly_twice_yesterday():
    assert _last(_frame(_bars("A.TWSE", [100.0, 200.0])), "vp_double") is True


def test_double_volume_misses_just_below_twice():
    assert _last(_frame(_bars("A.TWSE", [100.0, 199.0])), "vp_double") is False


def test_double_volume_is_null_on_the_first_session():
    assert _series(_frame(_bars("A.TWSE", [100.0, 300.0])), "vp_double")[0] is None


def test_double_volume_is_null_after_a_halted_session():
    frame = _frame(_bars("A.TWSE", [100.0, None, 300.0]), _bars("B.TWSE", [100.0, 100.0, 300.0]))
    assert _series(frame, "vp_double") == [None, None]
    assert _series(frame, "vp_double", "B.TWSE") == [None, False, True]


def test_zero_volume_counts_as_untraded():
    assert _series(_frame(_bars("A.TWSE", [100.0, 0.0, 300.0])), "vp_double") == [None, None, None]


def test_null_ohlc_counts_as_untraded():
    rows = _bars("A.TWSE", [100.0, 100.0, 300.0])
    rows[1]["close"] = None
    result = _run(_frame(rows)).sort("date").row(1, named=True)
    assert all(result[c] is None for c in VOLUME_PATTERN_COLUMNS)
    assert _series(_frame(rows), "vp_double")[2] is None


def test_rows_outside_the_calendar_are_undecidable():
    frame = _frame(_bars("A.TWSE", [100.0, 300.0]))
    assert _run(frame, calendar=DAYS[:1])["vp_double"].to_list() == [None, None]


def test_step_up_compares_the_close_with_two_sessions_back():
    frame = _frame(_bars("A.TWSE", [100.0, 110.0, 120.0], closes=[10.0, 11.0, 10.5]))
    assert _last(frame, "vp_step_up") is True
    assert _last(frame, "vp_step_down") is False


def test_step_up_misses_on_equal_volume_or_a_flat_close():
    assert _last(_frame(_bars("A.TWSE", [100.0, 110.0, 110.0], closes=[10.0, 11.0, 12.0])), "vp_step_up") is False
    assert _last(_frame(_bars("A.TWSE", [100.0, 110.0, 120.0], closes=[10.0, 11.0, 10.0])), "vp_step_up") is False


def test_step_down_hits_on_falling_volume_and_a_lower_close():
    frame = _frame(_bars("A.TWSE", [120.0, 110.0, 100.0], closes=[10.0, 9.0, 9.5]))
    assert _last(frame, "vp_step_down") is True
    assert _last(frame, "vp_step_up") is False


def test_step_down_misses_when_the_close_recovers():
    assert _last(_frame(_bars("A.TWSE", [120.0, 110.0, 100.0], closes=[10.0, 9.0, 10.0])), "vp_step_down") is False


def test_step_patterns_need_three_traded_sessions():
    frame = _frame(_bars("A.TWSE", [100.0, 110.0, None, 120.0, 130.0], closes=[10.0, 11.0, 0.0, 12.0, 13.0]))
    assert _series(frame, "vp_step_up") == [None, None, None, None]
    assert _series(frame, "vp_step_down") == [None, None, None, None]


def test_low_volume_hits_below_sixty_percent_of_the_prior_mean():
    assert _last(_frame(_bars("A.TWSE", [100.0] * 20 + [59.0])), "vp_low") is True


def test_low_volume_misses_at_exactly_sixty_percent():
    assert _last(_frame(_bars("A.TWSE", [100.0] * 20 + [60.0])), "vp_low") is False


def test_low_volume_averages_only_traded_sessions_and_needs_fifteen_of_twenty():
    fifteen = [None, 0.0, None, 0.0, None] + [100.0] * 15
    assert _last(_frame(_bars("A.TWSE", [*fifteen, 59.0])), "vp_low") is True
    fourteen = [None, 0.0, None, 0.0, None, 0.0] + [100.0] * 14
    assert _last(_frame(_bars("A.TWSE", [*fourteen, 10.0])), "vp_low") is None


def test_low_volume_ignores_sessions_older_than_twenty():
    volumes = [1000.0] * 5 + [100.0] * 20 + [59.0]
    assert _last(_frame(_bars("A.TWSE", volumes)), "vp_low") is True


def test_low_volume_window_starts_exactly_twenty_sessions_back():
    """t-20 decides each case below; a 19- or 21-session window would flip it."""
    assert _last(_frame(_bars("A.TWSE", [1.0, 300.0] + [100.0] * 19 + [65.0])), "vp_low") is True
    untraded = [None, 0.0, None, 0.0, None]
    fifteenth = [None, 100.0, *untraded] + [100.0] * 14 + [10.0]
    assert _last(_frame(_bars("A.TWSE", fifteenth)), "vp_low") is True
    without_t20 = [100.0, None, *untraded] + [100.0] * 14 + [10.0]
    assert _last(_frame(_bars("A.TWSE", without_t20)), "vp_low") is None


def test_low_volume_is_null_on_a_zero_volume_day_and_for_short_history():
    assert _last(_frame(_bars("A.TWSE", [100.0] * 20 + [0.0])), "vp_low") is None
    assert _last(_frame(_bars("A.TWSE", [100.0] * 14 + [10.0])), "vp_low") is None


def test_flat_volume_hits_inside_fifteen_percent():
    assert _last(_frame(_bars("A.TWSE", [100.0, 90.0, 86.0])), "vp_flat") is True


def test_flat_volume_misses_at_exactly_fifteen_percent():
    assert _last(_frame(_bars("A.TWSE", [100.0, 90.0, 85.0])), "vp_flat") is False


def test_flat_volume_is_null_with_a_zero_volume_session():
    assert _last(_frame(_bars("A.TWSE", [100.0, 0.0, 100.0])), "vp_flat") is None


def _stall(today: float, close: float, open_: float, high: float, change: float | None) -> bool | None:
    rows = _bars(
        "A.TWSE", [100.0] * 5 + [today],
        closes=[100.0] * 5 + [close], opens=[100.0] * 5 + [open_],
        highs=[100.0] * 5 + [high], changes=[0.05] * 5 + [change],
    )
    return _last(_frame(rows), "vp_up_stall")


def test_stall_hits_on_a_red_candle_at_exactly_one_and_a_half_times_volume():
    assert _stall(150.0, close=99.0, open_=100.0, high=100.0, change=0.05) is True


def test_stall_misses_below_one_and_a_half_times_volume():
    assert _stall(149.0, close=99.0, open_=100.0, high=100.0, change=0.05) is False


def test_stall_hits_on_an_upper_shadow_longer_than_twice_the_body():
    assert _stall(200.0, close=101.0, open_=100.0, high=103.5, change=0.05) is True
    assert _stall(200.0, close=101.0, open_=100.0, high=103.0, change=0.05) is False


def test_stall_ignores_the_shadow_of_a_doji():
    assert _stall(200.0, close=100.0, open_=100.0, high=110.0, change=0.05) is False


def test_stall_counts_a_flat_close_but_not_one_percent():
    assert _stall(200.0, close=100.0, open_=100.0, high=100.0, change=0.0) is True
    assert _stall(200.0, close=100.0, open_=100.0, high=100.0, change=0.009) is True
    assert _stall(200.0, close=100.0, open_=100.0, high=100.0, change=0.01) is False
    assert _stall(200.0, close=100.0, open_=100.0, high=100.0, change=-0.001) is False


def test_stall_is_undecidable_without_five_traded_sessions():
    halted = _bars("A.TWSE", [100.0, None, 100.0, 100.0, 100.0, 300.0], closes=[100.0] * 5 + [99.0])
    assert _last(_frame(halted), "vp_up_stall") is None
    zero = _bars("A.TWSE", [100.0, 0.0, 100.0, 100.0, 100.0, 300.0], closes=[100.0] * 5 + [99.0])
    assert _last(_frame(zero), "vp_up_stall") is None
    short = _bars("A.TWSE", [100.0] * 4 + [300.0], closes=[100.0] * 4 + [99.0])
    assert _last(_frame(short), "vp_up_stall") is None


def test_patterns_keep_row_order_and_the_other_columns():
    frame = _frame(_bars("B.TWSE", [100.0, 200.0]), _bars("A.TWSE", [100.0, 50.0]))
    shuffled = frame.reverse()
    result = _run(shuffled)
    assert result.drop(list(VOLUME_PATTERN_COLUMNS)).equals(shuffled)
    assert result["vp_double"].to_list() == [False, None, True, None]
    assert all(result.schema[c] == pl.Boolean for c in VOLUME_PATTERN_COLUMNS)


def test_an_empty_frame_gets_typed_columns():
    result = _run(_frame([]))
    assert result.is_empty()
    assert all(result.schema[c] == pl.Boolean for c in VOLUME_PATTERN_COLUMNS)


@pytest.fixture
def cached_master(monkeypatch) -> None:
    from app.taiwan import universe
    from app.taiwan.data_root import taiwan_data_root

    master = universe.TaiwanSecurityMaster(cache_path=taiwan_data_root() / "security_master.parquet")
    assert master.load_cache(), "tests need the security master cache under DATA_DIR/taiwan"
    monkeypatch.setattr(universe, "_default_master", master)


SESSIONS = [date(2026, 8, 31) + timedelta(days=i) for i in range(12) if (date(2026, 8, 31) + timedelta(days=i)).weekday() < 5]


def _daily(tmp_path: Path, bars: dict[str, list[tuple[float, float] | None]]) -> TaiwanDailyStore:
    rows = []
    for symbol, series in bars.items():
        for d, bar in zip(SESSIONS, series, strict=True):
            if bar is None:
                continue
            close, volume = bar
            rows.append({
                "symbol": symbol, "date": d, "open": close, "high": close, "low": close, "close": close,
                "volume": volume, "amount": close * volume, "quote_ts": None,
            })
    store = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    store.write_batch(pl.DataFrame(rows, schema_overrides={"quote_ts": pl.Int64}))
    return store


def _event(symbol: str, d: date, kind: str, pre: float, ref: float) -> pl.DataFrame:
    return pl.DataFrame([{
        "symbol": symbol, "date": d, "kind": kind, "pre_close": pre, "ref_price": ref,
        "limit_up": None, "limit_down": None, "open_ref": ref, "ex_factor": pre / ref, "official_label": None,
        "provider": "twse", "source": "test", "source_url": "u", "retrieved_at": None, "trade_date": d,
        "status": "official",
    }], schema=corporate_action_schema())


def _publish(tmp_path: Path, daily: TaiwanDailyStore, events: pl.DataFrame | None = None) -> None:
    actions = TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions")
    if events is not None:
        actions.upsert(events)
    TaiwanEnrichedRefreshService(
        daily_store=daily, enriched_root=tmp_path / "kline_daily_enriched",
        share_capital=MagicMock(as_instruments=MagicMock(return_value=pl.DataFrame())), corporate_actions=actions,
    ).refresh()


def _published(tmp_path: Path, d: date, symbol: str) -> dict:
    part = pl.read_parquet(tmp_path / "kline_daily_enriched" / f"date={d.isoformat()}" / "part.parquet")
    return part.filter(pl.col("symbol") == symbol).row(0, named=True)


def test_publish_writes_the_patterns_against_the_market_calendar(tmp_path, cached_master):
    steady = [(100.0, 1000.0)] * len(SESSIONS)
    gapped = [(100.0, 1000.0)] * 3 + [None] + [(100.0, 3000.0)] * (len(SESSIONS) - 4)
    _publish(tmp_path, _daily(tmp_path, {"2330.TWSE": steady, "2317.TWSE": gapped}))

    part = pl.read_parquet(tmp_path / "kline_daily_enriched" / f"date={SESSIONS[4].isoformat()}" / "part.parquet")
    assert all(part.schema[c] == pl.Boolean for c in VOLUME_PATTERN_COLUMNS)
    assert _published(tmp_path, SESSIONS[4], "2317.TWSE")["vp_double"] is None
    assert _published(tmp_path, SESSIONS[4], "2330.TWSE")["vp_double"] is False
    assert _published(tmp_path, SESSIONS[5], "2317.TWSE")["vp_double"] is False


def test_a_split_day_is_compared_on_restated_volume(tmp_path, cached_master):
    bars = [(546.0, 1_000_000.0)] * 6 + [(136.5, 4_000_000.0)] * (len(SESSIONS) - 6)
    daily = _daily(tmp_path, {"2327.TWSE": bars})
    _publish(tmp_path, daily, _event("2327.TWSE", SESSIONS[6], PAR_CHANGE, 546.0, 136.5))

    row = _published(tmp_path, SESSIONS[6], "2327.TWSE")
    assert row["vp_double"] is False
    assert row["vp_flat"] is True


def test_an_ex_dividend_day_is_compared_on_adjusted_closes(tmp_path, cached_master):
    bars = [(100.0, 300.0)] * 5 + [(100.0, 200.0), (80.0, 100.0)] + [(80.0, 100.0)] * (len(SESSIONS) - 7)
    daily = _daily(tmp_path, {"2330.TWSE": bars})
    _publish(tmp_path, daily, _event("2330.TWSE", SESSIONS[6], EXDIV, 100.0, 80.0))

    row = _published(tmp_path, SESSIONS[6], "2330.TWSE")
    assert row["change_pct"] == pytest.approx(0.0)
    assert row["vp_step_down"] is False


def test_partitions_published_before_the_patterns_still_scan(tmp_path, cached_master):
    _publish(tmp_path, _daily(tmp_path, {"2330.TWSE": [(100.0, 1000.0)] * len(SESSIONS)}))
    old = tmp_path / "kline_daily_enriched" / f"date={SESSIONS[0].isoformat()}" / "part.parquet"
    pl.read_parquet(old).drop(list(VOLUME_PATTERN_COLUMNS)).write_parquet(old)

    scanned = scan_enriched_parquet(str(tmp_path / "kline_daily_enriched" / "**" / "*.parquet")).collect()
    assert scanned.height == len(SESSIONS)
    assert not set(VOLUME_PATTERN_COLUMNS) & set(scanned.columns)
