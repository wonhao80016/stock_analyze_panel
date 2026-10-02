"""Strategy pick outcomes on synthetic data; sessions are consecutive days, as only stored partitions count.

Filler symbols keep both exchanges present in every partition unless a test removes one.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.services import strategy_pick_performance as spp
from app.services.strategy_pick_performance import (
    ADJUSTMENT_NOT_COVERED,
    COMPLETED,
    CORPORATE_ACTION_SOURCES,
    COVERAGE_MISSING,
    DATA_INCOMPLETE,
    EVENTS_MISSING,
    EVENTS_UNREADABLE,
    HORIZONS,
    LIMIT_CHECKED,
    LIMIT_UNKNOWN,
    NOT_FILLED_LIMIT_UP,
    NOT_FILLED_NO_TRADE,
    PENDING,
    PENDING_ENTRY,
    PRICES_INCOMPLETE,
    TRACKING,
    AdjustmentState,
    PriceCalendar,
    compute_outcomes,
    load_inputs,
    summarize,
)
from app.taiwan.corporate_actions import TaiwanCorporateActionStore, corporate_action_schema
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enrichment.corporate_actions import EXDIV

D = [date(2026, 1, 5) + timedelta(days=i) for i in range(30)]
SYM = "2330.TWSE"
OTC = "6510.TPEX"
FILLERS = ("1101.TWSE", "6488.TPEX")
OK = AdjustmentState("ok", covered_from=date(2020, 1, 1), covered_through=date(2030, 1, 1))
HALT = "halt"
BAR_SCHEMA = {
    "symbol": pl.String,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
}


def _bar(symbol: str, d: date, price) -> dict:
    if price == HALT:
        return {"symbol": symbol, "date": d, "open": None, "high": None, "low": None, "close": None, "volume": 5000.0}
    o, c = price if isinstance(price, tuple) else (price, price)
    return {"symbol": symbol, "date": d, "open": o, "high": max(o, c), "low": min(o, c), "close": c, "volume": 1000.0}


def _path(symbol: str, prices: dict[int, object]) -> list[dict]:
    """Bars of *symbol* keyed by index into D; a missing index is a session without a row."""
    return [_bar(symbol, D[i], p) for i, p in sorted(prices.items())]


def _market(rows: list[dict], n: int, drop_fillers: dict[int, tuple[str, ...]] | None = None) -> pl.DataFrame:
    drop = drop_fillers or {}
    fillers = [
        _bar(s, D[i], 10.0) for i in range(n) for s in FILLERS if s not in drop.get(i, ())
    ]
    return pl.DataFrame(rows + fillers, schema=BAR_SCHEMA)


def _picks(*rows: tuple, **flags: list) -> pl.DataFrame:
    base = pl.DataFrame(
        {
            "signal_date": [r[0] for r in rows],
            "strategy_id": [r[1] for r in rows],
            "symbol": [r[2] for r in rows],
            "rank": [i + 1 for i in range(len(rows))],
        },
        schema={"signal_date": pl.Date, "strategy_id": pl.String, "symbol": pl.String, "rank": pl.Int32},
    )
    defaults = {"recorded_late": False, "manifest_missing": False, "universe_complete": True}
    return base.with_columns([
        pl.Series(name, flags.get(name, [default] * len(rows)), dtype=pl.Boolean)
        for name, default in defaults.items()
    ])


def _profile(symbols, limit_pct: float | None = 0.10) -> pl.DataFrame:
    return pl.DataFrame(
        {"symbol": list(symbols), "limit_pct": [limit_pct] * len(symbols), "is_etf_tick": [False] * len(symbols)},
        schema={"symbol": pl.String, "limit_pct": pl.Float64, "is_etf_tick": pl.Boolean},
    )


def _event(symbol: str, d: date, pre_close: float, ref_price: float, limit_up: float, limit_down: float) -> dict:
    return {
        "symbol": symbol, "date": d, "kind": EXDIV, "pre_close": pre_close, "ref_price": ref_price,
        "limit_up": limit_up, "limit_down": limit_down, "open_ref": ref_price, "ex_factor": None,
        "official_label": None, "provider": "test", "source": "twse:twt49u", "source_url": None,
        "retrieved_at": None, "trade_date": d, "status": "ok",
    }


def _events(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(list(rows), schema=corporate_action_schema())


def _run(
    picks: pl.DataFrame,
    market: pl.DataFrame,
    *,
    stored: list[date] | None = None,
    events: pl.DataFrame | None = None,
    adjustment: AdjustmentState = OK,
    limit_pct: float | None = 0.10,
) -> pl.DataFrame:
    symbols = sorted(set(picks["symbol"].to_list()))
    stored = stored if stored is not None else sorted(set(market["date"].to_list()))
    calendar = PriceCalendar.from_market(stored, market)
    bars = market.filter(pl.col("symbol").is_in(symbols))
    return compute_outcomes(
        picks, bars, events if events is not None else _events(), adjustment, calendar, _profile(symbols, limit_pct)
    )


def _rising(symbol: str, n: int) -> list[dict]:
    """T = D[0] closes at 100; session k opens at 100 + k - 1 and closes at 100 + k."""
    return _path(symbol, {0: 100.0, **{k: (100.0 + k - 1, 100.0 + k) for k in range(1, n)}})


def _one(outcomes: pl.DataFrame, symbol: str = SYM) -> dict:
    return outcomes.filter(pl.col("symbol") == symbol).row(0, named=True)


def test_regular_pick_returns_k_th_close_over_next_open_as_decimals():
    out = _run(_picks((D[0], "s1", SYM)), _market(_rising(SYM, 22), 22))
    row = _one(out)
    assert row["status"] == COMPLETED
    assert row["entry_date"] == D[1]
    assert row["entry_price_raw"] == 100.0
    assert row["limit_check"] == LIMIT_CHECKED
    for k in HORIZONS:
        assert row[f"exit_date_{k}"] == D[k]
        assert row[f"reason_{k}"] is None
        assert row[f"ret_{k}"] == pytest.approx(k / 100)
        assert row[f"stale_{k}"] is False
    assert row["ret_1"] == pytest.approx(0.01)
    assert row["ret_1"] != pytest.approx(1.0)
    assert row["absent_since"] is None


def test_summary_mean_median_and_zero_return_boundary():
    closes = {"1111.TWSE": 101.0, "1112.TWSE": 100.0, "1113.TWSE": 99.0, "1114.TWSE": 103.0}
    odd = {"2101.TWSE": 99.0, "2102.TWSE": 100.0, "2103.TWSE": 102.0}
    rows = []
    for symbol, close in {**closes, **odd}.items():
        rows += _path(symbol, {0: 100.0, 1: (100.0, close)})
    picks = _picks(
        *[(D[0], "even", s) for s in closes],
        *[(D[0], "odd", s) for s in odd],
    )
    out = _run(picks, _market(rows, 2))
    summary = summarize(out, OK, D[1])
    by_id = {s["strategy_id"]: s for s in summary["strategies"]}

    even = by_id["even"]["horizons"]["1"]
    assert even["sample_count"] == 4
    assert even["mean_return"] == pytest.approx(0.0075)
    assert even["median_return"] == pytest.approx(0.005)
    assert even["positive_ratio"] == pytest.approx(0.5)

    odd_h = by_id["odd"]["horizons"]["1"]
    assert odd_h["median_return"] == pytest.approx(0.0)
    assert odd_h["positive_ratio"] == pytest.approx(1 / 3)
    assert by_id["odd"]["horizons"]["5"]["sample_count"] == 0
    assert by_id["odd"]["horizons"]["5"]["mean_return"] is None

    assert summary["method"]["costs_included"] is False
    assert summary["method"]["slippage_included"] is False
    assert summary["method"]["exit_fill_simulated"] is False
    assert summary["method"]["return_unit"] == "decimal"
    assert summary["prices_through"] == D[1].isoformat()


def test_open_at_limit_up_is_not_filled():
    rows = _path(SYM, {0: 100.0, 1: (110.0, 111.0), 2: 112.0})
    rows += _path("2317.TWSE", {0: 100.0, 1: (109.5, 111.0), 2: 112.0})
    # 47.30 * 1.1 = 52.03, floored to the 0.10 tick of the 50-100 band: 52.00.
    rows += _path("2303.TWSE", {0: 47.3, 1: (52.0, 52.0), 2: 52.0})
    picks = _picks((D[0], "s1", SYM), (D[0], "s1", "2317.TWSE"), (D[0], "s1", "2303.TWSE"))
    out = _run(picks, _market(rows, 3))

    hit = _one(out, SYM)
    assert hit["status"] == NOT_FILLED_LIMIT_UP
    assert hit["limit_check"] == LIMIT_CHECKED
    assert all(hit[f"ret_{k}"] is None and hit[f"reason_{k}"] == NOT_FILLED_LIMIT_UP for k in HORIZONS)
    assert _one(out, "2303.TWSE")["status"] == NOT_FILLED_LIMIT_UP
    below = _one(out, "2317.TWSE")
    assert below["status"] == TRACKING
    assert below["ret_1"] == pytest.approx(111.0 / 109.5 - 1)


def test_next_session_with_null_ohlc_but_volume_is_no_trade():
    rows = _path(SYM, {0: 100.0, 1: HALT, 2: 101.0, 3: 102.0})
    out = _run(_picks((D[0], "s1", SYM)), _market(rows, 4))
    row = _one(out)
    assert row["status"] == NOT_FILLED_NO_TRADE
    assert row["entry_date"] == D[1]
    assert row["entry_price_raw"] is None
    assert all(row[f"ret_{k}"] is None and row[f"reason_{k}"] == NOT_FILLED_NO_TRADE for k in HORIZONS)


@pytest.mark.parametrize("bad_open", [0.0, -1.0])
def test_non_positive_open_is_no_trade_and_never_an_infinite_return(bad_open):
    rows = _path(SYM, {0: 100.0, 1: (bad_open, 101.0), 2: 101.0})
    rows += [r for r in _rising("2317.TWSE", 7) if r["date"] != D[5]]
    rows += _path("2317.TWSE", {5: (bad_open, 105.0)})
    out = _run(_picks((D[0], "s1", SYM), (D[0], "s1", "2317.TWSE")), _market(rows, 7))
    entry = _one(out, SYM)
    assert entry["status"] == NOT_FILLED_NO_TRADE
    assert entry["entry_price_raw"] is None
    assert all(entry[f"ret_{k}"] is None for k in HORIZONS)
    exit_row = _one(out, "2317.TWSE")
    assert exit_row["ret_1"] == pytest.approx(0.01)
    assert exit_row["stale_5"] is True and exit_row["ret_5"] == pytest.approx(0.04)
    stats = summarize(out, OK, D[6])["strategies"][0]
    assert stats["horizons"]["5"]["mean_return"] == pytest.approx(0.04)


def test_entry_on_event_session_uses_official_limit_up():
    # Ex-dividend on T+1: the official limit is 99, the T-close rule would give 110.
    rows = _path(SYM, {0: 100.0, 1: (99.0, 99.0), 2: 99.0})
    rows += _path("2317.TWSE", {0: 100.0, 1: (98.5, 99.0), 2: 99.0})
    events = _events(
        _event(SYM, D[1], 100.0, 90.0, 99.0, 81.0),
        _event("2317.TWSE", D[1], 100.0, 90.0, 99.0, 81.0),
    )
    picks = _picks((D[0], "s1", SYM), (D[0], "s1", "2317.TWSE"))
    out = _run(picks, _market(rows, 3), events=events)
    assert _one(out, SYM)["status"] == NOT_FILLED_LIMIT_UP
    filled = _one(out, "2317.TWSE")
    assert filled["status"] == TRACKING
    assert filled["limit_check"] == LIMIT_CHECKED
    assert filled["ret_1"] == pytest.approx(99.0 / 98.5 - 1)


def test_missing_limit_pct_enters_with_unknown_limit_check():
    rows = _path(SYM, {0: 100.0, 1: (110.0, 112.0), 2: 113.0})
    out = _run(_picks((D[0], "s1", SYM)), _market(rows, 3), limit_pct=None)
    row = _one(out)
    assert row["status"] == TRACKING
    assert row["limit_check"] == LIMIT_UNKNOWN
    assert row["ret_1"] == pytest.approx(112.0 / 110.0 - 1)
    assert summarize(out, OK, D[2])["strategies"][0]["limit_check_unknown"] == 1


def test_ex_dividend_inside_holding_period_is_forward_adjusted():
    # Cash dividend 5 on D[3]: factor 100 / 95, so the T+1 open of 100 is 95 on the latest scale.
    rows = _path(SYM, {0: 100.0, 1: 100.0, 2: 100.0, 3: 95.0, 4: 95.0, 5: (95.0, 96.9), 6: 97.0})
    events = _events(_event(SYM, D[3], 100.0, 95.0, 104.5, 85.5))
    out = _run(_picks((D[0], "s1", SYM)), _market(rows, 7), events=events)
    row = _one(out)
    assert row["entry_price_raw"] == 100.0
    assert row["ret_1"] == pytest.approx(0.0)
    assert row["ret_5"] == pytest.approx(0.02)
    assert row["ret_5"] != pytest.approx(96.9 / 100.0 - 1)


def test_no_trade_or_missing_row_at_exit_reuses_previous_close():
    prices = {i: (100.0 + i - 1, 100.0 + i) for i in range(1, 12)}
    prices[0] = 100.0
    prices[5] = HALT
    del prices[10]
    out = _run(_picks((D[0], "s1", SYM)), _market(_path(SYM, prices), 12))
    row = _one(out)
    assert row["stale_5"] is True and row["stale_days_5"] == 1
    assert row["ret_5"] == pytest.approx(0.04)
    assert row["stale_10"] is True and row["stale_days_10"] == 1
    assert row["ret_10"] == pytest.approx(0.09)
    assert row["stale_1"] is False
    assert row["stale_exit"] is True
    assert row["absent_since"] is None
    stats = summarize(out, OK, D[11])["strategies"][0]
    assert "stale_exit_count" not in stats
    assert [stats["horizons"][str(k)]["stale_exit_count"] for k in HORIZONS] == [0, 1, 1, 0]
    assert stats["horizons"]["10"]["sample_count"] == 1
    assert stats["horizons"]["20"]["pending_count"] == 1
    assert stats["history_degraded_count"] == 0
    assert stats["horizons"]["5"]["sample_count"] == 1


def test_unfinished_horizons_stay_pending_and_no_session_means_pending_entry():
    out = _run(_picks((D[0], "s1", SYM), (D[6], "s1", SYM)), _market(_rising(SYM, 7), 7))
    early, late = out.row(0, named=True), out.row(1, named=True)
    assert early["status"] == TRACKING
    assert early["ret_5"] == pytest.approx(0.05)
    assert early["exit_date_10"] is None
    assert early["reason_10"] == PENDING and early["reason_20"] == PENDING
    assert late["status"] == PENDING_ENTRY
    assert late["entry_date"] is None
    assert all(late[f"reason_{k}"] == PENDING for k in HORIZONS)
    stats = summarize(out, OK, D[6])["strategies"][0]
    assert stats["tracking_count"] == 1 and stats["pending_entry_count"] == 1


def test_absent_rows_never_leave_a_pick_pending():
    absent_entry = _path(SYM, {0: 100.0, **{i: 100.0 + i for i in range(2, 12)}})
    absent_exit = _path("2317.TWSE", {0: 100.0, **{i: (100.0, 100.0 + i) for i in range(1, 8) if i != 5}})
    gone = _path("2303.TWSE", {0: 100.0, **{i: (100.0, 100.0 + i) for i in range(1, 8)}})
    picks = _picks((D[0], "s1", SYM), (D[0], "s1", "2317.TWSE"), (D[0], "s1", "2303.TWSE"))
    out = _run(picks, _market(absent_entry + absent_exit + gone, 12))

    entry = _one(out, SYM)
    assert entry["status"] == NOT_FILLED_NO_TRADE
    assert entry["absent_since"] is None

    exit_gap = _one(out, "2317.TWSE")
    assert exit_gap["status"] == TRACKING
    assert exit_gap["stale_5"] is True and exit_gap["stale_days_5"] == 1
    assert exit_gap["ret_5"] == pytest.approx(0.04)
    assert exit_gap["absent_since"] == D[8]

    delisted = _one(out, "2303.TWSE")
    assert delisted["absent_since"] == D[8]
    assert delisted["status"] == TRACKING
    assert delisted["stale_10"] is True and delisted["stale_days_10"] == 3
    assert delisted["ret_10"] == pytest.approx(0.07)
    assert summarize(out, OK, D[11])["strategies"][0]["absent_count"] == 2


def test_symbol_absent_from_entry_through_latest_is_marked_absent_since_entry():
    out = _run(_picks((D[0], "s1", SYM)), _market(_path(SYM, {0: 100.0}), 4))
    row = _one(out)
    assert row["status"] == NOT_FILLED_NO_TRADE
    assert row["absent_since"] == D[1]


def test_price_gaps_use_market_wide_partition_dates():
    rows = _path(SYM, {0: 100.0, 1: (100.0, 101.0), 3: 103.0, 4: 104.0, 5: (105.0, 105.0)})
    market = _market(rows, 6)
    absent_only = _one(_run(_picks((D[0], "s1", SYM)), market))
    assert absent_only["reason_5"] is None
    assert absent_only["ret_5"] == pytest.approx(0.05)

    unread = market.filter(pl.col("date") != D[2])
    gap = _one(_run(_picks((D[0], "s1", SYM)), unread, stored=D[:6]))
    assert gap["ret_1"] == pytest.approx(0.01)
    assert gap["reason_5"] == PRICES_INCOMPLETE and gap["ret_5"] is None


def test_unreadable_partition_marks_prices_incomplete(tmp_path):
    rows = _rising(SYM, 12)
    _write_daily(tmp_path, _market(rows, 12))
    (tmp_path / "taiwan" / "daily" / f"date={D[3].isoformat()}" / "part.parquet").write_bytes(b"not parquet")
    _write_adjustment(tmp_path)
    picks = _picks((D[0], "s1", SYM), (D[2], "s1", SYM), (D[5], "s1", SYM))
    inputs = load_inputs(tmp_path, picks, lambda symbols: _profile(symbols))
    assert D[3] in inputs.calendar.stored_dates
    assert D[3] not in inputs.calendar.exchanges
    out = compute_outcomes(picks, inputs.bars, inputs.events, inputs.adjustment, inputs.calendar, inputs.limit_profile)

    before, on_gap, after = (out.row(i, named=True) for i in range(3))
    assert before["ret_1"] == pytest.approx(0.01)
    assert before["reason_5"] == PRICES_INCOMPLETE and before["ret_5"] is None
    assert on_gap["status"] == PRICES_INCOMPLETE
    assert on_gap["reason_1"] == PRICES_INCOMPLETE
    assert after["reason_1"] is None and after["ret_1"] is not None
    assert summarize(out, inputs.adjustment, D[11])["strategies"][0]["horizons"]["5"]["prices_incomplete"] == 2


def test_single_exchange_exit_partition_is_data_incomplete_not_stale():
    # D[5] holds TWSE rows only: the TPEx pick is unknown there, not absent.
    rows = _rising(OTC, 8)
    rows = [r for r in rows if r["date"] != D[5]]
    rows += _rising(SYM, 8)
    market = _market(rows, 8, drop_fillers={5: ("6488.TPEX",)})
    picks = _picks((D[0], "s1", OTC), (D[0], "s1", SYM))
    out = _run(picks, market)

    otc = _one(out, OTC)
    assert otc["reason_5"] == DATA_INCOMPLETE
    assert otc["ret_5"] is None and otc["stale_5"] is False
    assert otc["stale_exit"] is False
    assert otc["ret_1"] == pytest.approx(0.01)
    assert _one(out, SYM)["reason_5"] == DATA_INCOMPLETE

    stats = summarize(out, OK, D[7])["strategies"][0]
    assert stats["horizons"]["5"]["stale_exit_count"] == 0
    assert stats["horizons"]["5"]["sample_count"] == 0
    assert stats["horizons"]["5"]["data_incomplete"] == 2


def test_single_exchange_entry_partition_is_data_incomplete_not_no_trade():
    rows = [r for r in _rising(OTC, 4) if r["date"] != D[1]]
    out = _run(_picks((D[0], "s1", OTC)), _market(rows, 4, drop_fillers={1: ("6488.TPEX",)}))
    row = _one(out, OTC)
    assert row["status"] == DATA_INCOMPLETE
    assert row["reason_1"] == DATA_INCOMPLETE


def test_stale_span_over_a_single_exchange_partition_is_data_incomplete():
    prices = {0: 100.0, 1: (100.0, 101.0), 2: 102.0, 3: 103.0, 5: HALT, 6: 106.0}
    out = _run(_picks((D[0], "s1", OTC)), _market(_path(OTC, prices), 7, drop_fillers={4: ("6488.TPEX",)}))
    row = _one(out, OTC)
    assert row["reason_5"] == DATA_INCOMPLETE and row["ret_5"] is None


def _write_daily(data_dir: Path, market: pl.DataFrame) -> None:
    TaiwanDailyStore(data_dir / "taiwan" / "daily").write_batch(
        market.with_columns(pl.lit(1.0).alias("amount"), pl.lit(None, dtype=pl.Int64).alias("quote_ts"))
    )


def _write_adjustment(
    data_dir: Path,
    events: pl.DataFrame | None = None,
    *,
    sources: tuple[str, ...] = CORPORATE_ACTION_SOURCES,
    covered: tuple[date, date] = (date(2020, 1, 1), date(2030, 1, 1)),
) -> TaiwanCorporateActionStore:
    store = TaiwanCorporateActionStore(data_dir / "taiwan" / "corporate_actions")
    frame = events if events is not None else _events(_event("9999.TWSE", date(2020, 6, 1), 10.0, 9.0, 9.9, 8.1))
    store.upsert(frame)
    store.record_fetch(sources, covered[0], covered[1], datetime(2026, 1, 1, 18, 0))
    return store


def _drop_events(store: TaiwanCorporateActionStore) -> None:
    store.path.unlink()


def _corrupt_events(store: TaiwanCorporateActionStore) -> None:
    store.path.write_bytes(b"not parquet")


def _drop_fetch_state(store: TaiwanCorporateActionStore) -> None:
    store.fetch_state_path.unlink()


def _drop_one_source(store: TaiwanCorporateActionStore) -> None:
    state = json.loads(store.fetch_state_path.read_text(encoding="utf-8"))
    del state[CORPORATE_ACTION_SOURCES[-1]]
    store.fetch_state_path.write_text(json.dumps(state), encoding="utf-8")


def _malform_one_source(store: TaiwanCorporateActionStore) -> None:
    state = json.loads(store.fetch_state_path.read_text(encoding="utf-8"))
    del state[CORPORATE_ACTION_SOURCES[0]]["covered_through"]
    store.fetch_state_path.write_text(json.dumps(state), encoding="utf-8")


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        (_drop_events, EVENTS_MISSING),
        (_corrupt_events, EVENTS_UNREADABLE),
        (_drop_fetch_state, COVERAGE_MISSING),
        (_drop_one_source, COVERAGE_MISSING),
        (_malform_one_source, COVERAGE_MISSING),
    ],
)
def test_unavailable_adjustment_withholds_every_return(tmp_path, damage, reason):
    rows = _rising(SYM, 7) + _path("2317.TWSE", {0: 100.0, 1: (110.0, 110.0), 2: 110.0})
    _write_daily(tmp_path, _market(rows, 7))
    damage(_write_adjustment(tmp_path))
    picks = _picks((D[0], "s1", SYM), (D[0], "s1", "2317.TWSE"), (D[6], "s1", SYM))
    inputs = load_inputs(tmp_path, picks, lambda symbols: _profile(symbols))
    assert inputs.adjustment.status == "unavailable"
    assert inputs.adjustment.reason == reason
    out = compute_outcomes(picks, inputs.bars, inputs.events, inputs.adjustment, inputs.calendar, inputs.limit_profile)

    for k in HORIZONS:
        assert out[f"ret_{k}"].is_null().all()
    assert out["limit_check"].to_list() == [LIMIT_UNKNOWN] * 3
    first = _one(out, SYM)
    assert first["status"] == TRACKING
    assert first["reason_1"] == reason and first["reason_5"] == reason
    assert _one(out, "2317.TWSE")["status"] == TRACKING
    summary = summarize(out, inputs.adjustment, inputs.calendar.prices_through)
    assert summary["adjustment"] == {"status": "unavailable", "reason": reason, "covered_through": None}
    assert summary["strategies"][0]["signal_count"] == 3


def test_coverage_ending_before_exit_excludes_only_the_later_horizons():
    adjustment = AdjustmentState("ok", covered_from=date(2020, 1, 1), covered_through=D[3])
    out = _run(_picks((D[0], "s1", SYM)), _market(_rising(SYM, 12), 12), adjustment=adjustment)
    row = _one(out)
    assert row["ret_1"] == pytest.approx(0.01)
    assert row["limit_check"] == LIMIT_CHECKED
    assert row["reason_5"] == ADJUSTMENT_NOT_COVERED and row["ret_5"] is None
    assert row["reason_10"] == ADJUSTMENT_NOT_COVERED
    stats = summarize(out, adjustment, D[11])["strategies"][0]["horizons"]
    assert stats["1"]["sample_count"] == 1
    assert stats["5"]["adjustment_not_covered"] == 1


def test_entry_outside_coverage_excludes_every_horizon_and_the_limit_check():
    adjustment = AdjustmentState("ok", covered_from=D[2], covered_through=date(2030, 1, 1))
    out = _run(_picks((D[0], "s1", SYM)), _market(_rising(SYM, 7), 7), adjustment=adjustment)
    row = _one(out)
    assert row["limit_check"] == LIMIT_UNKNOWN
    assert row["reason_1"] == ADJUSTMENT_NOT_COVERED
    assert row["reason_5"] == ADJUSTMENT_NOT_COVERED


def test_coverage_window_is_the_intersection_of_all_sources(tmp_path):
    store = _write_adjustment(tmp_path)
    store.record_fetch(CORPORATE_ACTION_SOURCES[:1], date(2030, 1, 2), date(2031, 1, 1), datetime(2031, 1, 1))
    state = json.loads(store.fetch_state_path.read_text(encoding="utf-8"))
    state[CORPORATE_ACTION_SOURCES[1]]["covered_through"] = D[4].isoformat()
    store.fetch_state_path.write_text(json.dumps(state), encoding="utf-8")
    _, adjustment = spp.read_adjustment(store)
    assert adjustment.status == "ok"
    assert adjustment.covered_from == date(2020, 1, 1)
    assert adjustment.covered_through == D[4]


def test_late_manifest_missing_and_incomplete_market_picks_are_only_counted():
    symbols = ["1201.TWSE", "1202.TWSE", "1203.TWSE", "1204.TWSE", "1205.TWSE", "1206.TWSE"]
    rows = []
    for s in symbols:
        rows += _path(s, {0: 100.0, 1: (100.0, 102.0)})
    picks = _picks(
        *[(D[0], "s1", s) for s in symbols],
        recorded_late=[False, True, False, False, None, None],
        manifest_missing=[False, False, True, False, False, True],
        universe_complete=[True, True, True, False, True, None],
    )
    stats = summarize(_run(picks, _market(rows, 2)), OK, D[1])["strategies"][0]
    assert stats["signal_count"] == 6
    assert stats["excluded"] == {"recorded_late": 2, "universe_incomplete": 1, "manifest_missing": 2}
    assert stats["filled_count"] == 1
    assert stats["horizons"]["1"]["sample_count"] == 1
    assert stats["horizons"]["1"]["mean_return"] == pytest.approx(0.02)


def test_consecutive_signal_days_count_once_each():
    out = _run(_picks((D[0], "s1", SYM), (D[1], "s1", SYM)), _market(_rising(SYM, 4), 4))
    stats = summarize(out, OK, D[3])["strategies"][0]
    assert stats["signal_count"] == 2 and stats["signal_days"] == 2
    assert stats["horizons"]["1"]["sample_count"] == 2
    assert stats["horizons"]["1"]["sample_days"] == 2


def test_empty_picks_give_empty_outcomes_and_summary():
    picks = _picks()
    out = _run(picks, _market([], 2))
    assert out.is_empty()
    summary = summarize(out, OK, None)
    assert summary["strategies"] == [] and summary["first_signal_date"] is None


def test_load_inputs_reads_only_from_data_dir_and_injected_limit_profile(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("global data root or security master used")

    monkeypatch.setattr("app.taiwan.data_root.taiwan_data_root", forbidden)
    monkeypatch.setattr("app.taiwan.universe.get_security_master", forbidden)
    _write_daily(tmp_path, _market(_rising(SYM, 3), 3))
    _write_adjustment(tmp_path)
    asked: list[list[str]] = []

    def profile(symbols: list[str]) -> pl.DataFrame:
        asked.append(symbols)
        return _profile(symbols)

    picks = _picks((D[0], "s1", SYM))
    inputs = load_inputs(tmp_path, picks, profile)
    assert asked == [[SYM]]
    assert inputs.adjustment.status == "ok"
    assert inputs.bars["symbol"].unique().to_list() == [SYM]
    assert inputs.calendar.stored_dates == tuple(D[:3])
    assert inputs.calendar.exchanges[D[1]] == frozenset({"TWSE", "TPEX"})
    out = compute_outcomes(picks, inputs.bars, inputs.events, inputs.adjustment, inputs.calendar, inputs.limit_profile)
    assert _one(out)["ret_1"] == pytest.approx(0.01)


def test_manifest_missing_pick_keeps_null_flags_and_one_exclusion():
    rows = _path(SYM, {0: 100.0, 1: (100.0, 102.0)})
    picks = _picks((D[0], "s1", SYM), manifest_missing=[True], recorded_late=[None], universe_complete=[None])
    out = _run(picks, _market(rows, 2))
    row = _one(out)
    assert row["recorded_late"] is None and row["universe_complete"] is None
    assert row["excluded_reason"] == "manifest_missing"
    stats = summarize(out, OK, D[1])["strategies"][0]
    assert stats["excluded"] == {"manifest_missing": 1, "recorded_late": 0, "universe_incomplete": 0}
    assert stats["horizons"]["1"]["sample_count"] == 0


@pytest.mark.parametrize("dropped", ["recorded_late", "manifest_missing", "universe_complete"])
def test_picks_without_a_manifest_flag_column_are_rejected(dropped):
    picks = _picks((D[0], "s1", SYM)).drop(dropped)
    with pytest.raises(ValueError, match=dropped):
        _run(picks, _market(_rising(SYM, 3), 3))


def test_load_inputs_creates_nothing_under_an_empty_data_dir(tmp_path):
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    picks = _picks((D[0], "s1", SYM))
    inputs = load_inputs(tmp_path, picks, lambda symbols: _profile(symbols))
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before
    assert inputs.calendar.stored_dates == ()
    assert inputs.adjustment.reason == EVENTS_MISSING
    out = compute_outcomes(picks, inputs.bars, inputs.events, inputs.adjustment, inputs.calendar, inputs.limit_profile)
    assert _one(out)["status"] == PENDING_ENTRY


def test_status_and_exclusion_counts_sum_to_signal_count():
    # D[2] is stored but unreadable; D[3] holds TWSE rows only.
    rows = _rising(SYM, 5)
    rows += _path("2317.TWSE", {0: 100.0, 1: (110.0, 110.0), 4: 110.0})
    rows += _path("2303.TWSE", {0: 100.0, 1: HALT, 4: 100.0})
    rows += _path(OTC, {0: 50.0, 1: 50.0, 4: 50.0})
    market = _market(rows, 5, drop_fillers={3: ("6488.TPEX",)}).filter(pl.col("date") != D[2])
    picks = _picks(
        (D[0], "s1", SYM),
        (D[0], "s1", "2317.TWSE"),
        (D[0], "s1", "2303.TWSE"),
        (D[1], "s1", SYM),
        (D[2], "s1", OTC),
        (D[4], "s1", SYM),
        (D[0], "s1", "2330.TWSE"),
        recorded_late=[False, False, False, False, False, False, True],
    )
    out = _run(picks, market, stored=D[:5])
    assert "limit_pct" not in out.columns and "is_etf_tick" not in out.columns
    stats = summarize(out, OK, D[4])["strategies"][0]
    per_status = {
        name: stats[name]
        for name in (
            "not_filled_limit_up",
            "not_filled_no_trade",
            "pending_entry_count",
            "tracking_count",
            "completed_count",
            "entry_prices_incomplete_count",
            "entry_data_incomplete_count",
        )
    }
    assert per_status == {
        "not_filled_limit_up": 1,
        "not_filled_no_trade": 1,
        "pending_entry_count": 1,
        "tracking_count": 1,
        "completed_count": 0,
        "entry_prices_incomplete_count": 1,
        "entry_data_incomplete_count": 1,
    }
    assert sum(per_status.values()) + sum(stats["excluded"].values()) == stats["signal_count"] == 7
