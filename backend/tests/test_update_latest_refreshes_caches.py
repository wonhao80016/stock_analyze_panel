"""The settings update button has to move the panels, not just the store.

New bars on disk are invisible to a running process: the repo caches and the
strategy picks file are built once and never reread, which is why a fetch could
succeed while every panel kept showing the previous trading day.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from fastapi import HTTPException

from app.api.taiwan import update_taiwan_history_to_latest
from app.services.strategy_pick_history import CURRENT, InputStatus, RunReport


def _run(update_result: dict):
    svc = MagicMock()
    svc.update_to_latest.return_value = update_result
    with (
        patch("app.api.taiwan.TaiwanBootstrapService", return_value=svc),
        patch("app.jobs.daily_pipeline.refresh_enriched_cache") as enriched,
        patch("app.jobs.daily_pipeline.refresh_strategy_cache") as strategy,
    ):
        return update_taiwan_history_to_latest(), enriched, strategy


def test_new_trading_days_refresh_both_caches():
    result, enriched, strategy = _run(
        {"ok": True, "already_current": False, "message": "成功更新 1 個交易日", "dates_fetched": 1}
    )
    assert enriched.call_count == 1
    assert strategy.call_count == 1
    assert result["caches_refreshed"] is True


def test_a_publish_without_new_days_refreshes_both_caches():
    """Indicators caught up for bars fetched earlier must reach the panels too."""
    result, enriched, strategy = _run({
        "ok": True, "already_current": True, "message": "本地資料已是最新",
        "dates_fetched": 0, "enriched_published": True,
    })
    assert enriched.call_count == 1
    assert strategy.call_count == 1
    assert result["caches_refreshed"] is True


def test_nothing_fetched_refreshes_the_index_but_leaves_the_caches_alone():
    """Recomputing every strategy over an unchanged store is pure cost; the index may still lag."""
    svc = MagicMock()
    svc.update_to_latest.return_value = {
        "ok": True, "already_current": True, "message": "本地資料已是最新", "dates_fetched": 0,
    }
    with (
        patch("app.api.taiwan.TaiwanBootstrapService", return_value=svc),
        patch("app.jobs.daily_pipeline.refresh_enriched_cache") as enriched,
        patch("app.jobs.daily_pipeline.refresh_strategy_cache") as strategy,
        patch(
            "app.jobs.daily_pipeline.refresh_benchmark_index", return_value=date(2026, 9, 30)
        ) as benchmark,
    ):
        result = update_taiwan_history_to_latest()

    benchmark.assert_called_once_with("manual update-latest")
    assert result["benchmark_index_latest_date"] == "2026-09-30"
    assert enriched.call_count == 0
    assert strategy.call_count == 0
    assert "caches_refreshed" not in result


def test_the_caches_are_refreshed_in_order():
    """Strategy picks are computed from the enriched cache, so it reloads first."""
    order: list[str] = []
    svc = MagicMock()
    svc.update_to_latest.return_value = {"ok": True, "dates_fetched": 2}
    with (
        patch("app.api.taiwan.TaiwanBootstrapService", return_value=svc),
        patch(
            "app.jobs.daily_pipeline.refresh_enriched_cache",
            side_effect=lambda _l: order.append("enriched"),
        ),
        patch(
            "app.jobs.daily_pipeline.refresh_strategy_cache",
            side_effect=lambda _l: order.append("strategy"),
        ),
    ):
        update_taiwan_history_to_latest()

    assert order == ["enriched", "strategy"]


def test_a_failed_fetch_still_raises_and_refreshes_nothing():
    order: list[str] = []

    def fail():
        order.append("update")
        raise ValueError("本地尚無資料，請先下載歷史資料包")

    svc = MagicMock()
    svc.update_to_latest.side_effect = fail
    with (
        patch("app.api.taiwan.TaiwanBootstrapService", return_value=svc),
        patch("app.jobs.daily_pipeline.refresh_enriched_cache") as enriched,
        patch("app.jobs.daily_pipeline.refresh_strategy_cache") as strategy,
        patch(
            "app.jobs.daily_pipeline.refresh_benchmark_index",
            side_effect=lambda _label: order.append("benchmark"),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        update_taiwan_history_to_latest()

    assert exc.value.status_code == 400
    assert order == ["benchmark", "update"]
    assert enriched.call_count == 0
    assert strategy.call_count == 0


# The endpoint deliberately does not guard the two refresh calls: it relies on
# them logging and swallowing. Pinned here because the fetch has already written
# to disk by then, so letting a refresh error surface would report a failure for
# work that succeeded.
def test_enriched_refresh_swallows_a_broken_repo():
    from app.jobs import daily_pipeline

    repo = MagicMock()
    repo.rebuild_views.side_effect = RuntimeError("duckdb is gone")
    with patch.object(daily_pipeline, "_get_app_state", return_value=MagicMock(repo=repo)):
        daily_pipeline.refresh_enriched_cache("test")


def test_strategy_refresh_swallows_a_broken_engine():
    from app.jobs import daily_pipeline

    state = MagicMock(repo=MagicMock(), strategy_engine=MagicMock())
    with (
        patch.object(daily_pipeline, "_get_app_state", return_value=state),
        patch(
            "app.api.screener.compute_and_cache_all",
            side_effect=RuntimeError("parquet unreadable"),
        ),
    ):
        daily_pipeline.refresh_strategy_cache("test")


def test_refreshes_are_skipped_rather_than_crashing_without_app_state():
    from app.jobs import daily_pipeline

    with patch.object(daily_pipeline, "_get_app_state", return_value=None):
        daily_pipeline.refresh_enriched_cache("test")
        daily_pipeline.refresh_strategy_cache("test")


SIGNAL_DAY = date(2026, 9, 30)
RESULTS = {"s1": {"total": 1, "as_of": "2026-09-30", "rows": [{"symbol": "2330.TWSE", "date": SIGNAL_DAY}]}}


def _app_state(data_dir: Path):
    repo = MagicMock()
    repo.store.data_dir = data_dir
    return MagicMock(repo=repo, strategy_engine=MagicMock())


def _computed(returned_as_of=None):
    def compute(repo, engine, **kwargs):
        return returned_as_of or kwargs["as_of"], RESULTS

    return compute


def _refresh_strategies(data_dir: Path, *, latest=SIGNAL_DAY, compute=None, record=None, label="test"):
    from app.jobs import daily_pipeline

    with (
        patch.object(daily_pipeline, "_get_app_state", return_value=_app_state(data_dir)),
        patch("app.services.screener.ScreenerService.latest_date", return_value=latest) as latest_date,
        patch("app.api.screener.compute_and_cache_all", side_effect=compute or _computed()) as compute_mock,
        patch("app.services.strategy_pick_history.record", side_effect=record) as record_mock,
    ):
        daily_pipeline.refresh_strategy_cache(label)
    return latest_date, compute_mock, record_mock


def test_strategy_refresh_resolves_the_signal_day_once_and_records_it(tmp_path):
    latest_date, compute, record = _refresh_strategies(tmp_path, label="margin")

    assert latest_date.call_count == 1
    assert compute.call_count == 1
    kwargs = compute.call_args.kwargs
    assert kwargs["as_of"] == SIGNAL_DAY
    assert isinstance(kwargs["run_report"], RunReport)
    record.assert_called_once_with(tmp_path, SIGNAL_DAY, RESULTS, kwargs["run_report"], "margin", None)


def test_strategy_refresh_does_not_record_another_day(tmp_path):
    _, compute, record = _refresh_strategies(tmp_path, compute=_computed(date(2026, 9, 29)))
    assert compute.call_count == 1
    assert record.call_count == 0


def test_strategy_refresh_without_a_signal_day_computes_and_records_nothing(tmp_path):
    _, compute, record = _refresh_strategies(tmp_path, latest=None)
    assert compute.call_count == 0
    assert record.call_count == 0


def test_input_mtime_is_read_before_the_computation(tmp_path):
    part = tmp_path / "kline_daily_enriched" / "date=2026-09-30" / "part.parquet"
    part.parent.mkdir(parents=True)
    part.write_bytes(b"x")
    os.utime(part, (1_000_000, 1_000_000))

    def compute(repo, engine, **kwargs):
        os.utime(part, (2_000_000, 2_000_000))
        return kwargs["as_of"], RESULTS

    _, _, record = _refresh_strategies(tmp_path, compute=compute)
    assert record.call_args.args[5] == 1_000_000


def test_ttl_history_cache_is_cleared_before_the_computation(tmp_path):
    from app.services import screener

    screener._history_cache[("stock", SIGNAL_DAY, 121)] = (0.0, MagicMock())
    seen: list[int] = []

    def compute(repo, engine, **kwargs):
        seen.append(len(screener._history_cache))
        return kwargs["as_of"], RESULTS

    try:
        _refresh_strategies(tmp_path, compute=compute)
    finally:
        screener._history_cache.clear()
    assert seen == [0]


def test_record_failure_leaves_the_strategy_cache_written(tmp_path):
    from app.services import strategy_cache

    def compute(repo, engine, **kwargs):
        strategy_cache.write_cache(tmp_path, str(kwargs["as_of"]), RESULTS)
        return kwargs["as_of"], RESULTS

    cache_file = tmp_path / "user_data" / "strategy_cache.json"
    _, _, record = _refresh_strategies(
        tmp_path,
        compute=compute,
        record=OSError("disk full"),
    )
    assert record.call_count == 1
    written = strategy_cache.read_cache(tmp_path)
    assert written["as_of"] == "2026-09-30"
    assert written["results"]["s1"]["total"] == 1
    assert cache_file.exists()


def test_compute_failure_records_nothing(tmp_path):
    _, compute, record = _refresh_strategies(tmp_path, compute=RuntimeError("parquet unreadable"))
    assert compute.call_count == 1
    assert record.call_count == 0


def _reporting_compute(symbol: str):
    def compute(repo, engine, *, run_report, **kwargs):
        run_report.current = InputStatus(
            state="ok", rows=2, exchanges_at_t=frozenset({"TWSE", "TPEX"}), dates=(SIGNAL_DAY,)
        )
        run_report.input_groups = {"s1": frozenset({CURRENT})}
        run_report.fingerprints = {"s1": "fp"}
        rows = [{"symbol": symbol, "date": SIGNAL_DAY}]
        return kwargs["as_of"], {"s1": {"total": 1, "as_of": "2026-09-30", "rows": rows}}

    return compute


def test_picks_and_the_freeze_check_use_the_repo_data_dir(tmp_path, monkeypatch):
    from app.config import settings
    from app.jobs import daily_pipeline
    from app.services import strategy_pick_history

    decoy = tmp_path / "decoy"
    decoy.mkdir()
    monkeypatch.setattr(settings, "data_dir", decoy)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    def refresh(symbol: str):
        with (
            patch.object(daily_pipeline, "_get_app_state", return_value=_app_state(data_dir)),
            patch("app.services.screener.ScreenerService.latest_date", return_value=SIGNAL_DAY),
            patch("app.api.screener.compute_and_cache_all", side_effect=_reporting_compute(symbol)),
        ):
            daily_pipeline.refresh_strategy_cache("test")

    refresh("2330.TWSE")
    assert strategy_pick_history.read_picks(data_dir).picks["symbol"].to_list() == ["2330.TWSE"]

    next_day = data_dir / "taiwan" / "daily" / "date=2026-10-01" / "part.parquet"
    next_day.parent.mkdir(parents=True)
    pl.DataFrame({"symbol": ["2330.TWSE"], "date": [date(2026, 10, 1)]}).write_parquet(next_day)
    refresh("6488.TPEX")
    assert strategy_pick_history.read_picks(data_dir).picks["symbol"].to_list() == ["2330.TWSE"]
    assert list(decoy.iterdir()) == []


def test_update_button_records_picks_through_the_same_refresh(tmp_path):
    from app.jobs import daily_pipeline

    svc = MagicMock()
    svc.update_to_latest.return_value = {"ok": True, "dates_fetched": 1}
    with (
        patch("app.api.taiwan.TaiwanBootstrapService", return_value=svc),
        patch("app.jobs.daily_pipeline.refresh_enriched_cache"),
        patch.object(daily_pipeline, "_get_app_state", return_value=_app_state(tmp_path)),
        patch("app.services.screener.ScreenerService.latest_date", return_value=SIGNAL_DAY),
        patch("app.api.screener.compute_and_cache_all", side_effect=_computed()) as compute,
        patch("app.services.strategy_pick_history.record") as record,
    ):
        result = update_taiwan_history_to_latest()

    assert result["caches_refreshed"] is True
    assert compute.call_args.kwargs["as_of"] == SIGNAL_DAY
    record.assert_called_once()
    assert record.call_args.args[:2] == (tmp_path, SIGNAL_DAY)
    assert record.call_args.args[4] == "manual update-latest"


def test_benchmark_refresh_is_stubbed_offline_by_default():
    from app.jobs import daily_pipeline

    with patch("app.taiwan.index_daily.get_index_daily_service") as service:
        assert daily_pipeline.refresh_benchmark_index("test") is None

    assert service.call_count == 0


class _IndexService:
    def __init__(self, dates: list[date]) -> None:
        self.dates = dates
        self.calls: list[tuple] = []

    def read_range(self, symbol, start, end, *, refresh_current_month=False):
        self.calls.append((symbol, start, end, refresh_current_month))
        return pl.DataFrame({"date": self.dates}, schema={"date": pl.Date})


@pytest.mark.real_benchmark_refresh
def test_benchmark_refresh_rereads_60_days_of_taiex_bypassing_the_cache_period():
    from app.jobs import daily_pipeline
    from app.taiwan.benchmarks import TAIEX

    service = _IndexService([date(2026, 9, 29), date(2026, 9, 30)])
    with (
        patch("app.taiwan.index_daily.get_index_daily_service", return_value=service),
        patch("app.taiwan.realtime.calendar.taipei_today", return_value=date(2026, 10, 1)),
    ):
        latest = daily_pipeline.refresh_benchmark_index("test")

    assert latest == date(2026, 9, 30)
    assert service.calls == [(TAIEX.symbol, date(2026, 8, 2), date(2026, 10, 1), True)]


@pytest.mark.real_benchmark_refresh
def test_benchmark_refresh_without_bars_returns_none():
    from app.jobs import daily_pipeline

    with patch("app.taiwan.index_daily.get_index_daily_service", return_value=_IndexService([])):
        assert daily_pipeline.refresh_benchmark_index("test") is None


@pytest.mark.real_benchmark_refresh
def test_a_failed_benchmark_refresh_does_not_block_the_update():
    svc = MagicMock()
    svc.update_to_latest.return_value = {"ok": True, "dates_fetched": 1}
    with (
        patch(
            "app.taiwan.index_daily.get_index_daily_service",
            side_effect=RuntimeError("exchange unreachable"),
        ),
        patch("app.api.taiwan.TaiwanBootstrapService", return_value=svc),
        patch("app.jobs.daily_pipeline.refresh_enriched_cache") as enriched,
        patch("app.jobs.daily_pipeline.refresh_strategy_cache") as strategy,
    ):
        result = update_taiwan_history_to_latest()

    assert result["benchmark_index_latest_date"] is None
    assert result["caches_refreshed"] is True
    assert enriched.call_count == 1
    assert strategy.call_count == 1
