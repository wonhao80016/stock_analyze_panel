"""Strategy versions of recorded picks and the 0050 benchmark aligned to each pick's own entry and exit."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from app.services import strategy_pick_history as sph
from app.services.strategy_pick_performance import (
    BENCHMARK_SYMBOL,
    COMPLETED,
    NOT_FILLED_LIMIT_UP,
    AdjustmentState,
    PriceCalendar,
    compute_outcomes,
    load_inputs,
    summarize,
)
from app.strategy.engine import StrategyEngine
from app.taiwan.corporate_actions import corporate_action_schema
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enrichment.corporate_actions import EXDIV
from app.taiwan.realtime.calendar import TAIPEI_TZ

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "app" / "strategy" / "builtin"

D = [date(2026, 3, 2) + timedelta(days=i) for i in range(25)]
SYM = "2330.TWSE"
FILLERS = ("1101.TWSE", "6488.TPEX")
OK = AdjustmentState("ok", covered_from=date(2020, 1, 1), covered_through=date(2030, 1, 1))
BAR_SCHEMA = {
    "symbol": pl.String, "date": pl.Date,
    **{c: pl.Float64 for c in ("open", "high", "low", "close", "volume")},
}

SOURCE = '''"""Close above its 20-day mean."""
import polars as pl

META = {"id": "toy", "name": "Toy", "asset_types": ["stock"], "timeframes": ["1d"]}


def filter(df, params):
    return pl.col("close") > pl.col("ma20")


def score(df):
    return pl.col("close") / pl.col("ma20")
'''

REFORMATTED = '''"""Close above its 20-day mean."""
import polars as pl

# A new comment.
META = {"id": "toy",   "name": "Toy", "asset_types": ["stock"],"timeframes": ["1d"]}



def filter(df, params):
    return pl.col("close")>pl.col("ma20")  # trailing comment


def score(df):

    return pl.col( "close" )  /  pl.col("ma20")
'''

REDOCUMENTED = SOURCE.replace("Close above its 20-day mean.", "Close above the 20-day mean.")

RESCORED = SOURCE.replace('pl.col("close") / pl.col("ma20")', 'pl.col("close") / pl.col("ma60")')


def _fake_engine(path: Path, asset_types=("stock",)) -> SimpleNamespace:
    strategy = SimpleNamespace(
        file_path=path,
        meta={"id": "toy", "asset_types": list(asset_types)},
        basic_filter={},
        composite=None,
    )
    return SimpleNamespace(get=lambda sid: strategy, has=lambda sid: sid == "toy")


def _version_of(tmp_path: Path, source: str) -> str | None:
    path = tmp_path / "toy.py"
    path.write_text(source, encoding="utf-8")
    return sph.strategy_versions(_fake_engine(path), {}, ["toy"])["toy"]


def test_comment_blank_line_and_spacing_edits_keep_the_version(tmp_path):
    base = _version_of(tmp_path, SOURCE)
    assert base is not None
    assert _version_of(tmp_path, REFORMATTED) == base


def test_a_scoring_change_is_a_new_version(tmp_path):
    assert _version_of(tmp_path, RESCORED) != _version_of(tmp_path, SOURCE)


def test_a_docstring_edit_is_a_new_version(tmp_path):
    assert _version_of(tmp_path, REDOCUMENTED) != _version_of(tmp_path, SOURCE)


def test_moving_a_statement_break_is_a_new_version(tmp_path):
    joined = tmp_path / "joined.py"
    joined.write_text("STOP_LOSS = 0 -0.06\n", encoding="utf-8")
    split = tmp_path / "split.py"
    split.write_text("STOP_LOSS = 0\n-0.06\n", encoding="utf-8")
    crlf = tmp_path / "crlf.py"
    crlf.write_bytes(b"STOP_LOSS = 0 -0.06\r\n")
    assert sph._code_digest(joined) != sph._code_digest(split)
    assert sph._code_digest(joined) == sph._code_digest(crlf)


def test_the_code_digest_reads_f_strings_as_one_token(tmp_path):
    plain = tmp_path / "plain.py"
    plain.write_text('label = f"{name}: {value:.2f}"\n', encoding="utf-8")
    spaced = tmp_path / "spaced.py"
    spaced.write_text('label  =  f"{name}: {value:.2f}"   # note\n', encoding="utf-8")
    changed = tmp_path / "changed.py"
    changed.write_text('label = f"{name}: {value:.3f}"\n', encoding="utf-8")
    assert sph._code_digest(plain) == sph._code_digest(spaced)
    assert sph._code_digest(plain) != sph._code_digest(changed)


def test_overrides_and_recorded_universe_are_part_of_the_version(tmp_path):
    path = tmp_path / "toy.py"
    path.write_text(SOURCE, encoding="utf-8")
    base = sph.strategy_versions(_fake_engine(path), {}, ["toy"])["toy"]
    assert sph.strategy_versions(_fake_engine(path), {"toy": {"params": {"n": 5}}}, ["toy"])["toy"] != base
    assert sph.strategy_versions(_fake_engine(path, ("stock", "etf")), {}, ["toy"])["toy"] != base


def test_current_versions_flags_strategies_whose_stock_run_no_longer_picks_etfs():
    engine = StrategyEngine(strategy_dirs=[BUILTIN_DIR])
    current = sph.current_versions(engine, {}, ["boll_breakout", "strong_open", "no_such_strategy"])
    assert set(current) == {"boll_breakout", "strong_open"}
    assert current["boll_breakout"].universe_unchanged is False
    assert current["strong_open"].universe_unchanged is True
    assert all(c.version and c.fingerprint for c in current.values())


def _taipei(d: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(d, time(hour, minute), TAIPEI_TZ)


def _runs(recorded_at: datetime | None = None, **columns) -> pl.DataFrame:
    n = len(columns["strategy_id"])
    return pl.DataFrame(
        {
            "signal_date": [D[i] for i in range(n)],
            **columns,
            "last_recorded_at": [recorded_at or _taipei(D[0], 16)] * n,
        },
        schema={
            "signal_date": pl.Date, "strategy_id": pl.Utf8, "strategy_version": pl.Utf8,
            "strategy_fingerprint": pl.Utf8, "last_recorded_at": pl.Datetime("us", "UTC"),
        },
    )


def _statuses(runs: pl.DataFrame, current, recorded_etf: pl.DataFrame | None) -> list[str]:
    return sph.with_version_status(runs, sph.run_version_status(runs, current, recorded_etf))["version_status"].to_list()


NO_ETF_RUNS = pl.DataFrame(schema={"signal_date": pl.Date, "strategy_id": pl.Utf8})


def test_version_status_uses_the_stored_version_before_the_fingerprint():
    current = {
        "a": sph.CurrentVersion("v2", "fp2", True),
        "b": sph.CurrentVersion("v2", "fp2", False),
    }
    runs = _runs(
        strategy_id=["a", "a", "a", "a", "a", "b", "b", "gone"],
        strategy_version=["v2", "v1", None, None, None, None, "v2", None],
        strategy_fingerprint=["old", "fp2", "fp2", "fp1", None, "fp2", "fp1", "fp2"],
    )
    assert _statuses(runs, current, NO_ETF_RUNS) == [
        "current",  # stored version matches; the fingerprint is not consulted
        "legacy",  # stored version differs even though the fingerprint matches
        "current",  # no stored version: same fingerprint, universe unchanged
        "legacy",
        "legacy",  # nothing to compare: unknown counts as legacy
        "legacy",  # same fingerprint but the run still picked ETFs
        "current",
        "legacy",  # strategy no longer loaded
    ]


STOCK_ONLY_AFTER = datetime(2026, 11, 24, 21, 41, tzinfo=TAIPEI_TZ)
ETF = "0050.TWSE"


def test_a_scope_changed_run_without_a_version_is_current_only_after_the_cutover_without_etfs():
    current = {"b": sph.CurrentVersion("v2", "fp2", False)}
    after = _runs(STOCK_ONLY_AFTER, strategy_id=["b", "b"], strategy_version=[None, None],
                  strategy_fingerprint=["fp2", "fp1"])
    etf_on_first = pl.DataFrame({"signal_date": [D[0]], "strategy_id": ["b"]})
    before = _runs(sph.STOCK_ONLY_CUTOVER - timedelta(minutes=1), strategy_id=["b"], strategy_version=[None],
                   strategy_fingerprint=["fp2"])

    assert _statuses(after, current, NO_ETF_RUNS) == ["current", "legacy"]
    assert _statuses(after, current, etf_on_first) == ["legacy", "legacy"]
    assert _statuses(after, current, None) == ["legacy", "legacy"]
    assert _statuses(before, current, NO_ETF_RUNS) == ["legacy"]


def test_a_stock_only_run_recorded_after_the_cutover_is_read_back_as_current(tmp_path):
    report = sph.RunReport(
        current=sph.InputStatus(state="ok", rows=10, exchanges_at_t=frozenset({"TWSE", "TPEX"})),
        input_groups={sid: frozenset({sph.CURRENT}) for sid in ("stocks", "with_etf")},
        fingerprints={"stocks": "fp", "with_etf": "fp"},
    )
    signal_date = date(2026, 11, 24)
    results = {
        "stocks": {"total": 1, "as_of": signal_date.isoformat(), "rows": [{"symbol": SYM, "date": signal_date}]},
        "with_etf": {"total": 1, "as_of": signal_date.isoformat(), "rows": [{"symbol": ETF, "date": signal_date}]},
    }
    sph.record(tmp_path, signal_date, results, report, "margin", 1.0, STOCK_ONLY_AFTER)

    snapshot = sph.read_picks(tmp_path)
    assert snapshot.runs["strategy_version"].to_list() == [None, None]
    current = {sid: sph.CurrentVersion("v-now", "fp", False) for sid in ("stocks", "with_etf")}
    statuses = sph.run_version_status(snapshot.runs, current, sph.etf_runs(snapshot.picks, {ETF}))
    picks = sph.with_version_status(snapshot.picks, statuses)
    assert dict(picks.select("strategy_id", "version_status").iter_rows()) == {
        "stocks": "current", "with_etf": "legacy",
    }


def test_record_stores_the_version_and_old_partitions_read_as_null(tmp_path):
    report = sph.RunReport(
        current=sph.InputStatus(state="ok", rows=10, exchanges_at_t=frozenset({"TWSE", "TPEX"})),
        input_groups={"s1": frozenset({sph.CURRENT}), "s2": frozenset({sph.CURRENT})},
        fingerprints={"s1": "fp1", "s2": "fp2"},
        versions={"s1": "v1"},
    )
    results = {sid: {"total": 1, "as_of": D[0].isoformat(), "rows": [{"symbol": SYM, "date": D[0]}]} for sid in ("s1", "s2")}
    now = datetime.combine(D[0], time(16), TAIPEI_TZ)
    assert sph.record(tmp_path, D[0], results, report, "16:30", 1.0, now).written == ("s1", "s2")

    snapshot = sph.read_picks(tmp_path)
    assert dict(snapshot.runs.select("strategy_id", "strategy_version").iter_rows()) == {"s1": "v1", "s2": None}
    assert dict(snapshot.picks.select("strategy_id", "strategy_version").iter_rows()) == {"s1": "v1", "s2": None}

    runs_file = tmp_path / "user_data" / "strategy_picks" / f"date={D[0].isoformat()}" / "runs.parquet"
    pl.read_parquet(runs_file).drop("strategy_version").write_parquet(runs_file)
    old = sph.read_picks(tmp_path)
    assert old.runs["strategy_version"].to_list() == [None, None]
    assert old.picks["strategy_fingerprint"].to_list() == ["fp1", "fp2"]


def _bar(symbol: str, d: date, o: float, c: float) -> dict:
    return {"symbol": symbol, "date": d, "open": o, "high": max(o, c), "low": min(o, c), "close": c, "volume": 1000.0}


def _market(rows: list[dict], n: int) -> pl.DataFrame:
    fillers = [_bar(s, D[i], 10.0, 10.0) for i in range(n) for s in FILLERS]
    return pl.DataFrame(rows + fillers, schema=BAR_SCHEMA)


def _rising(symbol: str, n: int, skip: tuple[int, ...] = ()) -> list[dict]:
    """T = D[0] closes at 100; session k opens at 100 + k - 1 and closes at 100 + k."""
    rows = [_bar(symbol, D[0], 100.0, 100.0)]
    return rows + [_bar(symbol, D[k], 100.0 + k - 1, 100.0 + k) for k in range(1, n) if k not in skip]


def _benchmark(n: int, skip: tuple[int, ...] = ()) -> list[dict]:
    """0050 opens at 100 + k and closes at 100 + k + 1 on session k; _dividend() holds its D[3] event."""
    return [_bar(BENCHMARK_SYMBOL, D[k], 100.0 + k, 101.0 + k) for k in range(n) if k not in skip]


def _dividend() -> pl.DataFrame:
    # D[2] closes at 103; the reference price after a 2.00 cash dividend is 101.
    event = {
        "symbol": BENCHMARK_SYMBOL, "date": D[3], "kind": EXDIV, "pre_close": 103.0, "ref_price": 101.0,
        "limit_up": 111.1, "limit_down": 90.9, "open_ref": 101.0, "ex_factor": None, "official_label": None,
        "provider": "test", "source": "twse:twt49u", "source_url": None, "retrieved_at": None,
        "trade_date": D[3], "status": "ok",
    }
    return pl.DataFrame([event], schema=corporate_action_schema())


def _picks(*symbols: str, versions: list[str] | None = None) -> pl.DataFrame:
    frame = pl.DataFrame(
        {
            "signal_date": [D[0]] * len(symbols),
            "strategy_id": ["s1"] * len(symbols),
            "symbol": list(symbols),
            "rank": list(range(1, len(symbols) + 1)),
            "recorded_late": [False] * len(symbols),
            "manifest_missing": [False] * len(symbols),
            "universe_complete": [True] * len(symbols),
        },
        schema_overrides={"rank": pl.Int32},
    )
    if versions is not None:
        frame = frame.with_columns(pl.Series("version_status", versions))
    return frame


def _outcomes(picks: pl.DataFrame, rows: list[dict], n: int, *, with_benchmark: bool = True) -> pl.DataFrame:
    market = _market(rows, n)
    calendar = PriceCalendar.from_market(sorted(set(market["date"].to_list())), market)
    symbols = sorted(set(picks["symbol"].to_list()))
    profile = pl.DataFrame(
        {"symbol": symbols, "limit_pct": [0.10] * len(symbols), "is_etf_tick": [False] * len(symbols)}
    )
    return compute_outcomes(
        picks,
        market.filter(pl.col("symbol").is_in(symbols)),
        _dividend(),
        OK,
        calendar,
        profile,
        benchmark=market.filter(pl.col("symbol") == BENCHMARK_SYMBOL) if with_benchmark else None,
    )


def test_benchmark_spans_the_pick_entry_open_to_its_exit_close_including_dividends():
    rows = _rising(SYM, 25) + _benchmark(25)
    out = _outcomes(_picks(SYM), rows, 25).row(0, named=True)
    assert out["status"] == COMPLETED

    # T+1: D[1] open 101 to D[1] close 102, both before the dividend.
    assert out["ret_1"] == pytest.approx(101.0 / 100.0 - 1)
    assert out["bench_ret_1"] == pytest.approx(102.0 / 101.0 - 1)
    assert out["excess_ret_1"] == pytest.approx(0.01 - (102.0 / 101.0 - 1))
    # T+5: D[1] open 101 scaled by 101/103 for the D[3] dividend, to the D[5] close 106.
    assert out["bench_ret_5"] == pytest.approx(106.0 / (101.0 * 101.0 / 103.0) - 1)
    assert out["bench_ret_5"] == pytest.approx(0.0702872, abs=1e-7)
    assert out["excess_ret_5"] == pytest.approx(-0.0202872, abs=1e-7)
    assert out["bench_ret_20"] == pytest.approx(121.0 / (101.0 * 101.0 / 103.0) - 1)


def test_a_stale_pick_exit_uses_the_benchmark_close_of_the_same_session():
    rows = _rising(SYM, 25, skip=(5,)) + _benchmark(25)
    out = _outcomes(_picks(SYM), rows, 25).row(0, named=True)
    assert out["stale_5"] is True
    assert out["ret_5"] == pytest.approx(104.0 / 100.0 - 1)
    assert out["bench_ret_5"] == pytest.approx(105.0 / (101.0 * 101.0 / 103.0) - 1)


def test_no_benchmark_return_when_the_pick_is_not_filled_at_limit_up():
    rows = [_bar(SYM, D[0], 100.0, 100.0), _bar(SYM, D[1], 110.0, 111.0), _bar(SYM, D[2], 112.0, 112.0)]
    out = _outcomes(_picks(SYM), rows + _benchmark(3), 3).row(0, named=True)
    assert out["status"] == NOT_FILLED_LIMIT_UP
    assert out["bench_ret_1"] is None and out["excess_ret_1"] is None


@pytest.mark.parametrize("missing", [1, 5])
def test_missing_benchmark_bar_on_the_entry_or_exit_session_leaves_null(missing):
    rows = _rising(SYM, 25) + _benchmark(25, skip=(missing,))
    out = _outcomes(_picks(SYM), rows, 25).row(0, named=True)
    assert out["ret_5"] == pytest.approx(0.05)
    assert out["bench_ret_5"] is None and out["excess_ret_5"] is None
    assert (out["bench_ret_10"] is None) == (missing == 1)


def test_without_benchmark_bars_every_benchmark_column_is_null():
    out = _outcomes(_picks(SYM), _rising(SYM, 25), 25, with_benchmark=False).row(0, named=True)
    assert out["ret_1"] is not None
    assert all(out[f"bench_ret_{k}"] is None and out[f"excess_ret_{k}"] is None for k in (1, 5, 10, 20))


def test_summary_keeps_current_and_legacy_picks_apart_with_benchmark_statistics():
    other = "2317.TWSE"
    flat = [_bar(other, D[0], 100.0, 100.0)] + [_bar(other, D[k], 100.0, 100.0) for k in range(1, 25)]
    legacy = "1216.TWSE"
    falling = [_bar(legacy, D[0], 100.0, 100.0)] + [_bar(legacy, D[k], 100.0, 90.0) for k in range(1, 25)]
    picks = _picks(SYM, other, legacy, versions=["current", "current", "legacy"])
    out = _outcomes(picks, _rising(SYM, 25) + flat + falling + _benchmark(25), 25)
    payload = summarize(out, OK, D[24])

    assert payload["version_counts"] == {"current": 2, "legacy": 1}
    assert payload["method"]["benchmark"] == BENCHMARK_SYMBOL
    (current,) = payload["strategies"]
    (old,) = payload["legacy_strategies"]
    assert current["signal_count"] == 2 and old["signal_count"] == 1

    bench_1 = 102.0 / 101.0 - 1
    one = current["horizons"]["1"]
    assert one["mean_return"] == pytest.approx((0.01 + 0.0) / 2)
    assert one["benchmark_sample_count"] == 2
    assert one["mean_benchmark_return"] == pytest.approx(bench_1)
    assert one["mean_excess_return"] == pytest.approx((0.01 - bench_1 + 0.0 - bench_1) / 2)
    assert one["beat_benchmark_ratio"] == pytest.approx(0.5)
    assert old["horizons"]["1"]["mean_return"] == pytest.approx(-0.10)
    assert old["horizons"]["1"]["mean_excess_return"] == pytest.approx(-0.10 - bench_1)
    assert old["horizons"]["1"]["beat_benchmark_ratio"] == 0.0


def test_load_inputs_reads_the_benchmark_even_when_nobody_picked_it(tmp_path):
    rows = _rising(SYM, 3) + _benchmark(3)
    frame = _market(rows, 3).with_columns(pl.lit(1.0).alias("amount"), pl.lit(None, dtype=pl.Int64).alias("quote_ts"))
    TaiwanDailyStore(tmp_path / "taiwan" / "daily").write_batch(frame)
    inputs = load_inputs(tmp_path, _picks(SYM), lambda symbols: pl.DataFrame({"symbol": symbols}))
    assert inputs.bars["symbol"].unique().to_list() == [SYM]
    assert inputs.benchmark.sort("date")["close"].to_list() == [101.0, 102.0, 103.0]
