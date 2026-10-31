from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import mean, median
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import strategy_pick_performance as spp
from app.strategy import custom_signals
from app.taiwan import signal_stats
from app.taiwan.corporate_actions import TaiwanCorporateActionStore, corporate_action_schema
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enrichment.corporate_actions import EXDIV
from app.taiwan.index_daily import TaiwanIndexDailyService
from app.taiwan.monthly_revenue_store import TaiwanMonthlyRevenueStore
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.return_index import TAIEX_TOTAL_RETURN, TaiwanReturnIndexStore
from app.taiwan.signal_stats import (
    FOREIGN_BUY_5D,
    NEW_HIGH_252,
    TaiwanSignalStatsService,
    event_kind,
    foreign_streak_flags,
    horizon_stats,
    new_high_flags,
    revenue_condition,
    signal_events,
)

N = 90
D = [date(2026, 1, 5) + timedelta(days=i) for i in range(N)]
SYM = "2330.TWSE"
FILLERS = ("1101.TWSE", "6488.TPEX")
LIMIT_DAY = 40
CORRUPT_DAY = 80
UNSTORED_INSTITUTIONAL_DAY = 12
CROSS_DAYS = (2, 3, 5, 7, 10, LIMIT_DAY)
FILLED_CROSS_DAYS = (2, 3, 5, 7, 10)
HIGH_DAYS = (5, 6, 7, 8, 15, 16, 30, 31, 60)
FOREIGN_BUY_DAYS = (*range(8, 15), *range(20, 27))
RETRIEVED = datetime(2026, 10, 29, 16, 31, tzinfo=TAIPEI)


def _close(i: int) -> float:
    return 100.0 + i


def _open(i: int) -> float:
    # T close 140 puts the statutory 10% limit at 154.
    return 154.0 if i == LIMIT_DAY + 1 else 99.5 + i


def _bench(i: int) -> float:
    return 1000.0 + i


class _Master:
    def __init__(self, instrument_type="stock"):
        self.instrument_type = instrument_type

    def get_instrument(self, symbol):
        if self.instrument_type is None:
            return None
        return SimpleNamespace(
            symbol=symbol, code=symbol.split(".")[0], name="fixture", industry=None,
            instrument_type=self.instrument_type, security_class=None,
        )


def _profile(symbols: list[str]) -> pl.DataFrame:
    return pl.DataFrame(
        {"symbol": symbols, "limit_pct": [0.10] * len(symbols), "is_etf_tick": [False] * len(symbols)},
        schema={"symbol": pl.String, "limit_pct": pl.Float64, "is_etf_tick": pl.Boolean},
    )


def _service(instrument_type="stock") -> TaiwanSignalStatsService:
    return TaiwanSignalStatsService(security_master=_Master(instrument_type), limit_profile=_profile)


def _bar(symbol: str, d: date, o: float, c: float) -> dict:
    return {"symbol": symbol, "date": d, "open": o, "high": max(o, c), "low": min(o, c), "close": c,
            "volume": 1000.0, "amount": 1.0, "quote_ts": None}


def _write_daily(root) -> None:
    rows = []
    for i, d in enumerate(D):
        rows.append(_bar(SYM, d, _open(i), _close(i)))
        rows += [_bar(s, d, 10.0, 10.0) for s in FILLERS]
    frame = pl.DataFrame(rows, schema={
        "symbol": pl.String, "date": pl.Date, **{c: pl.Float64 for c in ("open", "high", "low", "close", "volume")},
        "amount": pl.Float64, "quote_ts": pl.Int64,
    })
    store = TaiwanDailyStore(root / "taiwan" / "daily")
    store.write_batch(frame)
    (root / "taiwan" / "daily" / f"date={D[CORRUPT_DAY]}" / "part.parquet").write_bytes(b"not parquet")


def _write_enriched(root) -> None:
    for i in range(N):
        frame = pl.DataFrame(
            {
                "symbol": [SYM, FILLERS[0]],
                "date": [D[i], D[i]],
                "close": [_close(i), 10.0],
                "ma5": [_close(i) - 1, 10.0],
                "signal_macd_golden": [i in CROSS_DAYS, False],
                "signal_n_day_high": [i in HIGH_DAYS, False],
            },
            schema={"symbol": pl.String, "date": pl.Date, "close": pl.Float64, "ma5": pl.Float64,
                    "signal_macd_golden": pl.Boolean, "signal_n_day_high": pl.Boolean},
        )
        path = root / "kline_daily_enriched" / f"date={D[i]}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(path)


def _write_institutional(root) -> None:
    for i, d in enumerate(D):
        if i == UNSTORED_INSTITUTIONAL_DAY:
            continue
        frame = pl.DataFrame(
            {"symbol": [SYM], "date": [d], "foreign_net": [1000 if i in FOREIGN_BUY_DAYS else -1000]},
            schema={"symbol": pl.String, "date": pl.Date, "foreign_net": pl.Int64},
        )
        path = root / "taiwan" / "institutional" / f"date={d}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(path)


def _write_return_index(root, days: list[int] | None = None) -> None:
    store = TaiwanReturnIndexStore(root / "taiwan" / "return_index")
    for i in range(N) if days is None else days:
        store.write_day(D[i], {TAIEX_TOTAL_RETURN: _bench(i)}, "https://example.invalid/MI_INDEX", RETRIEVED)


def _write_adjustment(root) -> None:
    store = TaiwanCorporateActionStore(root / "taiwan" / "corporate_actions")
    event = {
        "symbol": "9999.TWSE", "date": date(2020, 6, 1), "kind": EXDIV, "pre_close": 10.0, "ref_price": 9.0,
        "limit_up": 9.9, "limit_down": 8.1, "open_ref": 9.0, "ex_factor": None, "official_label": None,
        "provider": "test", "source": "twse:twt49u", "source_url": None, "retrieved_at": None,
        "trade_date": date(2020, 6, 1), "status": "ok",
    }
    store.upsert(pl.DataFrame([event], schema=corporate_action_schema()))
    store.record_fetch(spp.CORPORATE_ACTION_SOURCES, date(2020, 1, 1), date(2030, 1, 1), datetime(2026, 1, 1, 18))


def _write_revenue(root, yoy_pct=25.0) -> None:
    TaiwanMonthlyRevenueStore(root / "taiwan" / "monthly_revenue").upsert([{
        "symbol": SYM, "exchange": "TWSE", "year": 2026, "month": 9, "revenue": 1000,
        "previous_month_revenue": 900, "previous_year_revenue": 800, "cumulative_revenue": 9000,
        "previous_year_cumulative_revenue": 7500, "mom_pct": 11.1, "yoy_pct": yoy_pct,
        "cumulative_yoy_pct": 20.0, "report_date": "2026-10-09", "source": "twse_openapi",
        "source_url": "https://example.invalid/t187ap05_L", "last_retrieved_at": RETRIEVED.isoformat(),
    }])


@pytest.fixture(autouse=True)
def _fresh_cache():
    signal_stats.clear_signal_stats_cache()
    yield
    signal_stats.clear_signal_stats_cache()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


@pytest.fixture
def world(data_dir):
    _write_daily(data_dir)
    _write_enriched(data_dir)
    _write_institutional(data_dir)
    _write_return_index(data_dir)
    _write_adjustment(data_dir)
    _write_revenue(data_dir)
    return data_dir


def _stat(response, signal_id):
    return next(s for s in response.signals if s.id == signal_id)


def _horizon(stat, k):
    return next(h for h in stat.horizons if h.horizon == k)


def _stock_return(t: int, k: int) -> float:
    return _close(t + k) / _open(t + 1) - 1.0


def _bench_return(t: int, k: int) -> float:
    return _bench(t + k) / _bench(t) - 1.0


def test_cross_signals_count_every_true_day():
    flags = [i in (1, 2, 3, 9) for i in range(12)]
    assert signal_events(D[:12], flags, "cross", D) == [D[1], D[2], D[3], D[9]]


def test_state_signals_count_the_first_day_and_skip_events_within_twenty_sessions():
    true_days = {1, 2, 3, 10, 11, 25, 29, 30, 50}
    flags = [True if i in true_days else (None if i == 49 else False) for i in range(60)]
    assert signal_events(D[:60], flags, "state", D) == [D[1], D[25], D[50]]


def test_the_state_gap_counts_market_sessions_not_the_symbol_rows():
    dates = [D[0], D[1], D[19], D[21]]
    flags = [False, True, False, True]
    assert signal_events(dates, flags, "state", D) == [D[1], D[21]]
    assert signal_events([D[0], D[1], D[19], D[20]], flags, "state", D) == [D[1]]


def test_event_kinds_split_crossovers_from_states():
    crosses = ["signal_ma_golden_5_20", "signal_macd_dead", "signal_ma10_breakdown", "signal_ma20_breakout"]
    states = ["signal_n_day_high", "signal_boll_breakout_upper", "signal_volume_surge", "signal_limit_up",
              "signal_broken_limit_up", "vp_double", "csg_mine", NEW_HIGH_252, FOREIGN_BUY_5D]
    assert [event_kind(s) for s in crosses] == ["cross"] * len(crosses)
    assert [event_kind(s) for s in states] == ["state"] * len(states)


def test_252_session_high_needs_a_full_window_and_ties_count():
    closes = [100.0 + i * 0.1 for i in range(251)] + [200.0, 150.0, 200.0, 210.0]
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(len(closes))]
    flags = new_high_flags(pl.DataFrame({"date": days, "close": closes})).get_column("flag").to_list()
    assert flags[:251] == [None] * 251
    assert flags[251:] == [True, False, True, True]


def test_252_session_high_skips_rows_without_a_close():
    closes = [1.0] * 251 + [None, 2.0]
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(len(closes))]
    flags = new_high_flags(pl.DataFrame({"date": days, "close": closes}, schema={"date": pl.Date, "close": pl.Float64}))
    assert flags.height == 252 and flags.get_column("flag").to_list()[-1] is True


def test_foreign_streak_is_true_on_the_fifth_buying_session_and_unknown_without_a_partition():
    sessions = D[:14]
    stored = set(sessions) - {D[3]}
    net = {d: 100 for d in sessions}
    net[D[10]] = -5
    del net[D[11]]
    flags = foreign_streak_flags(sessions, stored, net)
    assert flags[:4] == [None] * 4
    assert flags[4:8] == [None] * 4
    assert flags[8:10] == [True, True]
    assert flags[10:14] == [False] * 4


def test_a_stored_partition_without_the_symbol_breaks_the_foreign_streak():
    sessions = D[:6]
    net = {d: 100 for d in sessions if d != D[2]}
    flags = foreign_streak_flags(sessions, set(sessions), net)
    assert flags[4:] == [False, False]


def test_foreign_streak_counts_a_null_net_as_unknown():
    sessions = D[:5]
    flags = foreign_streak_flags(sessions, set(sessions), {d: (None if d == D[2] else 1) for d in sessions})
    assert flags == [None] * 5


def _outcomes(rets: list[float | None], reasons: list[str | None], k: int = 20) -> pl.DataFrame:
    n = len(rets)
    return pl.DataFrame(
        {
            "signal_date": D[:n],
            f"exit_date_{k}": [D[i + k] if i + k < N else None for i in range(n)],
            f"ret_{k}": rets,
            f"reason_{k}": reasons,
        },
        schema={"signal_date": pl.Date, f"exit_date_{k}": pl.Date, f"ret_{k}": pl.Float64, f"reason_{k}": pl.String},
    )


EMPTY_BENCH = pl.DataFrame(schema={"date": pl.Date, "close": pl.Float64})


def test_the_sample_threshold_counts_completed_events_only():
    four = horizon_stats(_outcomes([0.1, -0.1, 0.2, 0.0, None, None, None],
                                   [None, None, None, None, spp.PENDING, spp.PENDING, spp.NOT_FILLED_LIMIT_UP]),
                         20, EMPTY_BENCH)
    assert (four.event_count, four.completed_count, four.pending_count, four.not_filled_limit_up) == (7, 4, 2, 1)
    assert four.sufficient is False
    assert four.mean_return is None and four.median_return is None and four.up_ratio is None

    five = horizon_stats(_outcomes([0.1, -0.1, 0.2, 0.0, 0.3], [None] * 5), 20, EMPTY_BENCH)
    assert five.sufficient is True
    assert five.mean_return == pytest.approx(0.1)
    assert five.median_return == pytest.approx(0.1)
    assert five.up_ratio == pytest.approx(3 / 5)


def test_data_exclusions_are_counted_apart_from_limit_up_and_pending():
    stats = horizon_stats(
        _outcomes([None] * 4, [spp.PRICES_INCOMPLETE, spp.ADJUSTMENT_NOT_COVERED, spp.NOT_FILLED_NO_TRADE,
                               spp.EVENTS_MISSING]),
        20, EMPTY_BENCH,
    )
    assert (stats.data_excluded_count, stats.not_filled_no_trade, stats.pending_count) == (3, 1, 0)


def test_excess_uses_the_signal_close_to_the_exit_close_and_needs_five_covered_events():
    rets = [0.05, 0.02, 0.10, -0.01, 0.03, 0.04]
    rows = _outcomes(rets, [None] * 6)
    bench = pl.DataFrame({"date": D[:30], "close": [100.0 * (1.01 ** i) for i in range(30)]})
    stats = horizon_stats(rows, 20, bench)
    bench_ret = 1.01 ** 20 - 1
    excess = [r - bench_ret for r in rets]
    assert stats.benchmark_count == 6 and stats.benchmark_sufficient is True
    assert stats.excess_mean == pytest.approx(mean(excess))
    assert stats.beat_ratio == pytest.approx(sum(e > 0 for e in excess) / 6)

    partial = horizon_stats(rows, 20, bench.filter(pl.col("date") != D[0]).filter(pl.col("date") != D[21]))
    assert partial.benchmark_count == 4 and partial.benchmark_sufficient is False
    assert partial.excess_mean is None and partial.beat_ratio is None


def test_no_benchmark_leaves_excess_unset():
    stats = horizon_stats(_outcomes([0.1] * 6, [None] * 6), 20, EMPTY_BENCH)
    assert stats.benchmark_count == 0 and stats.benchmark_sufficient is False
    assert stats.excess_mean is None and stats.beat_ratio is None


@pytest.mark.parametrize(("yoy", "holds"), [(20.0, True), (19.99, False), (35.5, True), (None, None)])
def test_revenue_growth_holds_from_twenty_percent(yoy, holds):
    condition = revenue_condition([{"year": 2026, "month": 9, "yoy_pct": yoy}, {"year": 2026, "month": 8, "yoy_pct": 99.0}])
    assert condition.status == "ok" and condition.holds is holds and condition.period == "2026-09"
    assert condition.history_stats is False and condition.threshold_pct == 20.0


def test_revenue_without_a_stored_month_has_no_data():
    condition = revenue_condition([])
    assert condition.status == "no_data" and condition.holds is None


def test_pick_outcomes_take_the_horizons_as_a_parameter():
    market = pl.DataFrame(
        [_bar(SYM, D[i], _open(i), _close(i)) for i in range(70)]
        + [_bar(s, D[i], 10.0, 10.0) for i in range(70) for s in FILLERS]
    ).drop("amount", "quote_ts")
    calendar = spp.PriceCalendar.from_market(D[:70], market)
    picks = signal_stats._picks(SYM, {"s": [D[2]]})
    adjustment = spp.AdjustmentState("ok", covered_from=date(2020, 1, 1), covered_through=date(2030, 1, 1))
    events = pl.DataFrame(schema=corporate_action_schema())
    bars = market.filter(pl.col("symbol") == SYM)

    default = spp.compute_outcomes(picks, bars, events, adjustment, calendar, _profile([SYM]))
    assert [c for c in default.columns if c.startswith("ret_")] == ["ret_1", "ret_5", "ret_10", "ret_20"]
    assert default.row(0, named=True)["status"] == spp.COMPLETED

    custom = spp.compute_outcomes(picks, bars, events, adjustment, calendar, _profile([SYM]), horizons=(20, 60))
    row = custom.row(0, named=True)
    assert [c for c in custom.columns if c.startswith("ret_")] == ["ret_20", "ret_60"]
    assert row["ret_20"] == pytest.approx(default.row(0, named=True)["ret_20"])
    assert row["ret_60"] == pytest.approx(_stock_return(2, 60))
    assert row["status"] == spp.COMPLETED


def test_cross_events_enter_the_next_open_and_exclude_limit_up_entries(world):
    result = _service().get(SYM)

    assert result.applicable and result.status == "ok" and result.as_of == D[-1].isoformat()
    assert result.history_from == D[0].isoformat() and result.prices_through == D[-1].isoformat()
    macd = _stat(result, "signal_macd_golden")
    assert macd.event_kind == "cross" and macd.evaluated and macd.current is False
    assert (macd.first_event_date, macd.last_event_date) == (D[2].isoformat(), D[LIMIT_DAY].isoformat())
    for k in (20, 60):
        h = _horizon(macd, k)
        expected = [_stock_return(t, k) for t in FILLED_CROSS_DAYS]
        assert (h.event_count, h.completed_count, h.not_filled_limit_up) == (6, 5, 1)
        assert h.sufficient is True
        assert h.mean_return == pytest.approx(mean(expected))
        assert h.median_return == pytest.approx(median(expected))
        assert h.up_ratio == 1.0
        excess = [_stock_return(t, k) - _bench_return(t, k) for t in FILLED_CROSS_DAYS]
        assert h.benchmark_count == 5 and h.benchmark_sufficient is True
        assert h.excess_mean == pytest.approx(mean(excess))
        assert h.beat_ratio == pytest.approx(sum(e > 0 for e in excess) / 5)


def test_state_events_dedupe_and_a_missing_partition_excludes_the_window(world):
    result = _service().get(SYM)

    high = _stat(result, "signal_n_day_high")
    assert high.event_kind == "state"
    assert (high.first_event_date, high.last_event_date) == (D[5].isoformat(), D[60].isoformat())
    h20, h60 = _horizon(high, 20), _horizon(high, 60)
    assert (h20.event_count, h20.completed_count, h20.data_excluded_count) == (3, 2, 1)
    assert h20.sufficient is False and h20.mean_return is None and h20.up_ratio is None
    assert (h60.completed_count, h60.pending_count) == (1, 2)


def test_computed_signals_and_the_revenue_condition(world):
    result = _service().get(SYM)

    foreign = _stat(result, FOREIGN_BUY_5D)
    assert foreign.source == "computed" and foreign.name == "外資連5買" and foreign.event_kind == "state"
    assert (foreign.first_event_date, foreign.last_event_date) == (D[24].isoformat(), D[24].isoformat())
    assert foreign.current is False
    assert _horizon(foreign, 20).completed_count == 1
    assert result.institutional_from == D[0].isoformat()

    high = _stat(result, NEW_HIGH_252)
    assert high.evaluated and high.current is None and high.first_event_date is None
    assert _horizon(high, 60).event_count == 0

    vp = _stat(result, "vp_double")
    assert vp.evaluated is False and vp.horizons == [] and vp.current is None

    assert result.revenue is not None
    assert (result.revenue.status, result.revenue.holds, result.revenue.period) == ("ok", True, "2026-09")
    assert result.revenue.history_stats is False
    assert result.method.horizons == [20, 60] and result.method.min_samples == 5
    assert result.method.benchmark_window == "signal_close_to_exit_close"


def test_no_return_index_means_no_excess_and_never_the_price_index(data_dir, monkeypatch):
    _write_daily(data_dir)
    _write_enriched(data_dir)
    _write_institutional(data_dir)
    _write_adjustment(data_dir)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the t00 price index must not stand in for the return index")

    monkeypatch.setattr(TaiwanIndexDailyService, "read_range", forbidden)
    monkeypatch.setattr(TaiwanIndexDailyService, "ensure_range", forbidden)
    result = _service().get(SYM)

    assert result.benchmark is not None and result.benchmark.covered_from is None
    h = _horizon(_stat(result, "signal_macd_golden"), 20)
    assert h.sufficient is True and h.benchmark_count == 0
    assert h.benchmark_sufficient is False and h.excess_mean is None and h.beat_ratio is None


def test_an_etf_is_computed_without_a_revenue_condition(world):
    result = _service("etf").get(SYM)
    assert result.applicable and result.status == "ok" and result.revenue is None
    assert _horizon(_stat(result, "signal_macd_golden"), 20).completed_count == 5


def test_an_index_is_not_applicable_and_reads_nothing(data_dir, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("an index must not read the enriched table")

    monkeypatch.setattr(signal_stats, "read_latest_enriched", forbidden)
    result = _service().get("t00.TWSE")
    assert result.applicable is False and result.not_applicable_reason == "index" and result.signals == []


def test_no_published_partition_is_unpublished(data_dir):
    result = _service().get(SYM)
    assert result.applicable and result.status == "unpublished" and result.signals == []


def test_the_second_request_of_the_same_data_reads_no_partition(world, monkeypatch):
    first = _service().get(SYM)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("a cached answer must not read partitions")

    with monkeypatch.context() as patch:
        for name in ("scan_parquet", "read_parquet", "read_parquet_schema"):
            patch.setattr(pl, name, forbidden)
        assert _service().get(SYM) == first

    _write_return_index(world, days=[0])
    (world / "taiwan" / "return_index" / f"date={D[N - 1]}" / "part.parquet").unlink()
    refreshed = _service().get(SYM)
    assert refreshed.benchmark.covered_through == D[N - 2].isoformat()


def test_a_new_custom_signal_definition_shows_up_on_the_next_request(world):
    assert all(s.id != "csg_mine" for s in _service().get(SYM).signals)
    custom_signals.save_one(world, {"id": "mine", "name": "我的訊號", "enabled": True,
                                    "conditions": [{"left": "close", "op": ">", "right": 1}]})
    mine = _stat(_service().get(SYM), "csg_mine")
    assert mine.source == "custom" and mine.name == "我的訊號" and mine.evaluated is False


def test_a_new_daily_partition_rebuilds_the_market_calendar(world):
    first = _service().get(SYM)
    assert first.prices_through == D[-1].isoformat()
    extra = D[-1] + timedelta(days=1)
    TaiwanDailyStore(world / "taiwan" / "daily").write_batch(pl.DataFrame(
        [_bar(s, extra, 10.0, 10.0) for s in (SYM, *FILLERS)],
        schema={"symbol": pl.String, "date": pl.Date,
                **{c: pl.Float64 for c in ("open", "high", "low", "close", "volume")},
                "amount": pl.Float64, "quote_ts": pl.Int64},
    ))
    assert _service().get(SYM).prices_through == extra.isoformat()


@pytest.fixture
def client():
    return TestClient(app, client=("127.0.0.1", 50000))


URL = "/api/taiwan/stocks/{}/signal-stats"


def _serve(monkeypatch, service):
    monkeypatch.setattr("app.api.taiwan.get_taiwan_signal_stats_service", lambda: service)


def test_api_returns_the_statistics(world, monkeypatch, client):
    _serve(monkeypatch, _service())
    response = client.get(URL.format(SYM))
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok" and payload["method"]["entry"] == "next_session_open"
    assert payload["method"]["costs_included"] is False
    macd = next(s for s in payload["signals"] if s["id"] == "signal_macd_golden")
    assert [h["horizon"] for h in macd["horizons"]] == [20, 60]
    assert macd["horizons"][0]["not_filled_limit_up"] == 1
    assert payload["revenue"]["history_stats"] is False


def test_api_answers_without_data_and_for_an_index(data_dir, monkeypatch, client):
    _serve(monkeypatch, _service())
    payload = client.get(URL.format(SYM)).json()
    assert payload["applicable"] is True and payload["status"] == "unpublished" and payload["signals"] == []
    index = client.get(URL.format("t00.TWSE")).json()
    assert index["applicable"] is False and index["not_applicable_reason"] == "index"


def test_api_errors(world, monkeypatch, client):
    service = _service()

    def broken(*_args, **_kwargs):
        raise OSError(f"cannot read {world / 'kline_daily_enriched' / 'part.parquet'}")

    monkeypatch.setattr(service, "_symbol_inputs", broken)
    _serve(monkeypatch, service)
    failed = client.get(URL.format(SYM))
    assert failed.status_code == 500
    assert failed.json()["detail"] == "訊號統計讀取失敗"
    assert str(world) not in failed.text and "parquet" not in failed.text.lower()

    _serve(monkeypatch, _service(None))
    assert client.get(URL.format(SYM)).status_code == 404
    assert client.get(URL.format("not-a-symbol")).status_code == 400
