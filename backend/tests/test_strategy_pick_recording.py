"""Post-close recompute -> RunReport -> strategy_pick_history.record()."""
from __future__ import annotations

import json
import sys
import threading
import time
import types
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.api import screener as screener_api
from app.jobs import daily_pipeline
from app.services import screener
from app.services import strategy_pick_history as sph
from app.strategy.engine import StrategyEngine, StrategyResult

T = date(2026, 9, 30)
EARLIER = date(2026, 9, 29)
SYMBOLS = ("2330.TWSE", "6488.TPEX")
BUILTIN_DIR = Path(screener_api.__file__).resolve().parents[1] / "strategy" / "builtin"


def _bars(days, symbols=SYMBOLS) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [s for _ in days for s in symbols],
        "date": [d for d in days for _ in symbols],
        "close": [10.0] * (len(days) * len(symbols)),
    })


class _Repo:
    """Serves the inputs from the repo caches unless cached=False, which forces the disk paths."""

    def __init__(self, data_dir: Path, *, cached: bool = True, current=None, history=None):
        self.store = types.SimpleNamespace(data_dir=data_dir)
        self.cached = cached
        self.current = _bars([T]) if current is None else current
        self.history = _bars([EARLIER, T]) if history is None else history

    def taiwan_official_latest_date(self):
        return T

    def get_enriched_latest_asset(self, asset_type):
        return (self.current, T) if self.cached else (pl.DataFrame(), None)

    def get_enriched_history(self, target_date, lookback_days):
        return self.history if self.cached else None

    def get_instruments_asset(self, asset_type):
        return pl.DataFrame()


def _bars_engine(bars: int):
    return types.SimpleNamespace(required_history_bars=lambda ids, **kwargs: bars)


def _partition(data_dir: Path, day: date = T) -> Path:
    path = data_dir / "kline_daily_enriched" / f"date={day.isoformat()}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(autouse=True)
def _clean_ttl_cache():
    screener.ScreenerService.clear_history_cache()
    yield
    screener.ScreenerService.clear_history_cache()


@pytest.fixture
def no_indicators(monkeypatch):
    monkeypatch.setattr("app.indicators.pipeline.compute_indicators", lambda df: df)
    monkeypatch.setattr("app.indicators.pipeline.compute_signals", lambda df: df)


def _scan_returns(monkeypatch, frame: pl.DataFrame) -> None:
    def read(data_dir, dirname, start, end, **kwargs):
        return frame.filter(pl.col("date").is_between(start or date.min, end))

    monkeypatch.setattr(screener, "read_enriched_window", read)


def _scan_raises(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(screener, "read_enriched_window", boom)


def _status(svc, *, bars: int = 1, **kwargs) -> dict:
    return svc.build_strategy_context(_bars_engine(bars), T, ["s1"], **kwargs).input_status


def test_cached_inputs_are_ok_with_both_exchanges_at_t(tmp_path):
    status = _status(screener.ScreenerService(_Repo(tmp_path)), bars=121)
    assert status[sph.CURRENT] == sph.InputStatus(
        state="ok", rows=2, exchanges_at_t=frozenset({"TWSE", "TPEX"}), dates=(T,)
    )
    assert status[sph.HISTORY] == sph.InputStatus(
        state="ok", rows=4, exchanges_at_t=frozenset({"TWSE", "TPEX"}), dates=(EARLIER, T)
    )


def test_exchanges_count_only_rows_dated_t(tmp_path):
    history = pl.concat([_bars([EARLIER]), _bars([T], symbols=("2330.TWSE",))])
    status = _status(screener.ScreenerService(_Repo(tmp_path, history=history)), bars=121)
    assert status[sph.HISTORY].exchanges_at_t == frozenset({"TWSE"})


def test_history_not_requested_is_none(tmp_path):
    assert _status(screener.ScreenerService(_Repo(tmp_path)), bars=1)[sph.HISTORY] is None


def test_caller_supplied_current_is_provided(tmp_path):
    svc = screener.ScreenerService(_Repo(tmp_path))
    status = _status(svc, current=_bars([T], symbols=("2330.TWSE",)))
    assert status[sph.CURRENT].state == "provided"
    assert status[sph.CURRENT].exchanges_at_t == frozenset({"TWSE"})


def test_missing_current_partition_is_missing(tmp_path):
    status = _status(screener.ScreenerService(_Repo(tmp_path, cached=False)))
    assert status[sph.CURRENT] == sph.InputStatus(state="missing")


def test_unreadable_current_partition_is_error(tmp_path):
    _partition(tmp_path).write_bytes(b"not parquet")
    status = _status(screener.ScreenerService(_Repo(tmp_path, cached=False)))
    assert status[sph.CURRENT].state == "error"


def test_empty_current_partition_is_empty(tmp_path):
    _bars([T]).clear().write_parquet(_partition(tmp_path))
    status = _status(screener.ScreenerService(_Repo(tmp_path, cached=False)))
    assert status[sph.CURRENT].state == "empty"


def test_current_computed_over_warmup_history_is_ok(tmp_path, monkeypatch, no_indicators):
    _bars([T]).write_parquet(_partition(tmp_path))
    _scan_returns(monkeypatch, _bars([EARLIER, T]))
    status = _status(screener.ScreenerService(_Repo(tmp_path, cached=False)))
    assert status[sph.CURRENT].state == "ok"
    assert status[sph.CURRENT].rows == 2


@pytest.mark.parametrize("warmup", ["raises", "empty"])
def test_single_day_warmup_fallback_is_not_ok_and_not_recorded(tmp_path, monkeypatch, no_indicators, warmup):
    _bars([T]).write_parquet(_partition(tmp_path))
    if warmup == "raises":
        _scan_raises(monkeypatch)
    else:
        _scan_returns(monkeypatch, _bars([T]).clear())
    svc = screener.ScreenerService(_Repo(tmp_path, cached=False))
    context = svc.build_strategy_context(_bars_engine(1), T, ["px"])
    assert context.current.height == 2
    current = context.input_status[sph.CURRENT]
    assert current.state == "error"

    report = sph.RunReport(current=current, input_groups={"px": frozenset({sph.CURRENT})})
    result = {"px": {"total": 1, "as_of": str(T), "rows": [{"symbol": "2330.TWSE", "date": T}]}}
    out = sph.record(tmp_path, T, result, report, "16:30", None)
    assert out.rejected == {"px": sph.REJECT_INPUT_UNAVAILABLE}
    assert not (tmp_path / "user_data" / "strategy_picks").exists()


def test_history_read_failure_is_error(tmp_path, monkeypatch):
    _scan_raises(monkeypatch)
    status = _status(screener.ScreenerService(_Repo(tmp_path, cached=False)), bars=121)
    assert status[sph.HISTORY] == sph.InputStatus(state="error")


def test_history_empty_read_is_empty(tmp_path, monkeypatch):
    _scan_returns(monkeypatch, _bars([T]).clear())
    status = _status(screener.ScreenerService(_Repo(tmp_path, cached=False)), bars=121)
    assert status[sph.HISTORY].state == "empty"


def test_computed_and_ttl_cached_history_are_ok(tmp_path, monkeypatch, no_indicators):
    _scan_returns(monkeypatch, _bars([EARLIER, T]))
    svc = screener.ScreenerService(_Repo(tmp_path, cached=False))
    first = _status(svc, bars=121)[sph.HISTORY]
    _scan_raises(monkeypatch)
    second = _status(svc, bars=121)[sph.HISTORY]
    assert first.state == second.state == "ok"
    assert first.dates == second.dates == (EARLIER, T)


def test_loader_that_does_not_report_is_unverified(tmp_path, monkeypatch):
    svc = screener.ScreenerService(_Repo(tmp_path))
    monkeypatch.setattr(svc, "_load_enriched_history", lambda target, lookback: _bars([T]))
    assert _status(svc, bars=121)[sph.HISTORY] == sph.InputStatus()


def test_status_does_not_leak_between_builds_or_out_of_them(tmp_path, monkeypatch):
    svc = screener.ScreenerService(_Repo(tmp_path))
    assert _status(svc, bars=121)[sph.HISTORY].state == "ok"
    assert screener._input_sink.get() is None

    monkeypatch.setattr(svc, "_load_enriched_history", lambda target, lookback: _bars([T]))
    assert _status(svc, bars=121)[sph.HISTORY].state == "unverified"
    assert screener._input_sink.get() is None

    def boom(target, lookback):
        raise RuntimeError("history exploded")

    monkeypatch.setattr(svc, "_load_enriched_history", boom)
    with pytest.raises(RuntimeError):
        _status(svc, bars=121)
    assert screener._input_sink.get() is None

    svc._load_enriched_for_date(T)
    assert screener._input_sink.get() is None


def test_status_failure_leaves_loader_output_untouched(tmp_path, monkeypatch):
    repo = _Repo(tmp_path)

    def boom(*args, **kwargs):
        raise ValueError("symbol column has an unexpected type")

    monkeypatch.setattr(screener, "_input_status", boom)
    context = screener.ScreenerService(repo).build_strategy_context(_bars_engine(121), T, ["s1"])
    assert context.current is repo.current
    assert context.history is repo.history
    assert context.input_status == {sph.CURRENT: sph.InputStatus(), sph.HISTORY: sph.InputStatus()}
    assert context.input_status[sph.CURRENT].state == "unverified"
    assert screener._input_sink.get() is None


def test_concurrent_builds_keep_their_own_status(tmp_path):
    barrier = threading.Barrier(2, timeout=10)

    class _InterleavingRepo(_Repo):
        def get_enriched_latest_asset(self, asset_type):
            barrier.wait()
            return super().get_enriched_latest_asset(asset_type)

    symbols = {"one": ("2330.TWSE",), "three": ("2330.TWSE", "2317.TWSE", "6488.TPEX")}
    seen: dict[str, dict] = {}
    errors: list[BaseException] = []

    def build(name: str) -> None:
        try:
            repo = _InterleavingRepo(tmp_path, current=_bars([T], symbols=symbols[name]))
            seen[name] = _status(screener.ScreenerService(repo), bars=121)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=build, args=(name,)) for name in symbols]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert seen["one"][sph.CURRENT].rows == 1
    assert seen["one"][sph.CURRENT].exchanges_at_t == frozenset({"TWSE"})
    assert seen["three"][sph.CURRENT].rows == 3
    assert seen["three"][sph.CURRENT].exchanges_at_t == frozenset({"TWSE", "TPEX"})


def test_concurrent_clears_do_not_break_the_ttl_history_cache():
    frame = pl.DataFrame({"symbol": ["2330.TWSE"]})
    stop = threading.Event()
    errors: list[BaseException] = []

    def clear() -> None:
        try:
            while not stop.is_set():
                screener.ScreenerService.clear_history_cache()
        except BaseException as exc:
            errors.append(exc)

    def use() -> None:
        try:
            for i in range(5000):
                key = ("stock", T, i % 40)
                now = time.monotonic()
                screener._history_cache_put(key, now, frame)
                assert screener._history_cache_get(key, now + 2 * screener._HISTORY_CACHE_TTL) is None
        except BaseException as exc:
            errors.append(exc)
        finally:
            stop.set()

    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=clear), threading.Thread(target=use)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
    finally:
        sys.setswitchinterval(previous)
        stop.set()
    assert errors == []


@pytest.fixture(scope="module")
def builtin_engine():
    return StrategyEngine(strategy_dirs=[BUILTIN_DIR])


def _stub_run_all(engine, monkeypatch, seen: dict):
    def run_all(context, *, params_map=None, overrides_map=None, strategy_ids=None, skip_failed=False,
                failures=None):
        if context.asset_type == "stock":
            seen["ids"] = list(strategy_ids)
            seen["context"] = context
            seen["overrides_map"] = overrides_map
        return {
            sid: StrategyResult(
                as_of=context.as_of,
                strategy_id=sid,
                rows=[{"symbol": "2330.TWSE", "date": context.as_of, "raw_close": 10.0, "score": 1.0}],
                total=1,
            )
            for sid in strategy_ids
        }

    monkeypatch.setattr(engine, "run_all", run_all)


def test_scheduled_refresh_records_every_executed_builtin_strategy(tmp_path, monkeypatch, builtin_engine):
    seen: dict = {}
    _stub_run_all(builtin_engine, monkeypatch, seen)
    reports: list[sph.RunReport] = []
    real_record = sph.record

    def spy(data_dir, as_of, results, run_report, run_label, input_mtime, now=None):
        reports.append(run_report)
        return real_record(data_dir, as_of, results, run_report, run_label, input_mtime, now)

    monkeypatch.setattr(sph, "record", spy)
    state = types.SimpleNamespace(repo=_Repo(tmp_path), strategy_engine=builtin_engine)
    monkeypatch.setattr(daily_pipeline, "_get_app_state", lambda: state)

    daily_pipeline.refresh_strategy_cache("daily+institutional")

    executed = set(seen["ids"])
    assert executed
    assert all(not builtin_engine.get(sid).meta.get("research_only") for sid in executed)
    (report,) = reports
    assert set(report.input_groups) == executed
    assert all(report.input_groups[sid] for sid in executed)
    assert set(report.fingerprints) == executed
    assert all(report.fingerprints[sid] for sid in executed)
    assert report.current.state == "ok"
    assert report.history is not None and report.history.state == "ok"
    assert T in report.history.dates
    assert seen["context"].input_status[sph.HISTORY] == report.history

    runs = sph.read_picks(tmp_path).runs
    assert set(runs["strategy_id"].to_list()) == executed
    assert runs["universe_complete"].to_list() == [True] * len(executed)
    assert set(runs["run_label"].to_list()) == {"daily+institutional"}
    assert runs["strategy_fingerprint"].to_list() == [report.fingerprints[s] for s in runs["strategy_id"]]
    assert all(report.versions[sid] for sid in executed)
    assert runs["strategy_version"].to_list() == [report.versions[s] for s in runs["strategy_id"]]


def test_fingerprints_use_the_overrides_snapshot_the_run_used(tmp_path, monkeypatch, builtin_engine):
    seen: dict = {}
    _stub_run_all(builtin_engine, monkeypatch, seen)
    snapshots = iter([{"boll_breakout": {"params": {"period": 30}}}, {}])
    monkeypatch.setattr(
        screener_api.strategy_config, "list_overrides", lambda data_dir: next(snapshots)
    )
    report = sph.RunReport()
    screener_api.compute_and_cache_all(_Repo(tmp_path), builtin_engine, as_of=T, run_report=report)

    snapshot = {"boll_breakout": {"params": {"period": 30}}}
    assert seen["overrides_map"]["boll_breakout"] == snapshot["boll_breakout"]
    expected = sph.strategy_fingerprints(builtin_engine, snapshot, ["boll_breakout"])["boll_breakout"]
    assert report.fingerprints["boll_breakout"] == expected
    assert expected != sph.strategy_fingerprints(builtin_engine, {}, ["boll_breakout"])["boll_breakout"]


def test_run_report_does_not_change_the_output_or_the_cache_file(tmp_path, monkeypatch, builtin_engine):
    seen: dict = {}
    _stub_run_all(builtin_engine, monkeypatch, seen)
    outputs = []
    for name, report in (("plain", None), ("reported", sph.RunReport())):
        data_dir = tmp_path / name
        data_dir.mkdir()
        outputs.append(
            screener_api.compute_and_cache_all(_Repo(data_dir), builtin_engine, as_of=T, run_report=report)
        )
    assert outputs[0] == outputs[1]
    cached = [
        json.loads((tmp_path / name / "user_data" / "strategy_cache.json").read_text(encoding="utf-8"))
        for name in ("plain", "reported")
    ]
    for payload in cached:
        payload.pop("updated_at")
    assert cached[0] == cached[1]
