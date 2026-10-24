"""History bundle import, update-to-latest and the scheduled run: atomic partitions, mutual exclusion, repair-only failures."""
from __future__ import annotations

import threading
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from app.taiwan import bootstrap
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import (
    ChipDataRefreshResult,
    DatasetRefreshStats,
    TaiwanDailyUpdateService,
)
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from tests.test_stage4g_bootstrap_publish import BUNDLED_DAY, _run, bundle_job  # noqa: F401
from tests.test_taiwan_bootstrap import _refresh_service_for

TAIPEI = ZoneInfo("Asia/Taipei")


def test_an_interrupted_partition_copy_leaves_no_truncated_file(bundle_job, tmp_path: Path, monkeypatch):  # noqa: F811
    svc, _order = bundle_job
    real_copy = bootstrap.shutil.copy2

    def interrupted_copy(src, dst, *args, **kwargs):
        Path(dst).write_bytes(Path(src).read_bytes()[:16])
        raise OSError("copy interrupted")

    monkeypatch.setattr(bootstrap.shutil, "copy2", interrupted_copy)
    job = _run(svc)
    assert job.status == "failed"
    part = tmp_path / "taiwan" / "daily" / f"date={BUNDLED_DAY.isoformat()}" / "part.parquet"
    assert not part.exists()

    monkeypatch.setattr(bootstrap.shutil, "copy2", real_copy)
    job = _run(svc)
    assert job.status == "success", job.error
    assert pl.read_parquet(part)["symbol"].to_list() == ["2330.TWSE"]


def _chip_service() -> MagicMock:
    chip = MagicMock()
    chip.calendar = TaiwanTradingCalendar()
    chip.refresh_chip_data.return_value = ChipDataRefreshResult(
        institutional=DatasetRefreshStats(),
        market_institutional=DatasetRefreshStats(),
        margin=DatasetRefreshStats(),
    )
    chip.chip_data_as_of.return_value = {"institutional": None, "market_institutional": None, "margin": None}
    return chip


def test_a_failed_refetch_alone_still_catches_up_chips_and_publishes(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.taiwan.bootstrap.resolve_target_latest_trading_date", lambda: date(2024, 1, 2))
    afternoon = datetime(2024, 1, 2, 16, 40, tzinfo=TAIPEI)
    svc, mock_refresh = _refresh_service_for(tmp_path, written_on=afternoon)
    chip = _chip_service()
    published: list[bool] = []
    svc._chip_service = chip
    svc._publish = lambda only_if_behind, since=None: published.append(only_if_behind) or False
    mock_refresh.refresh_dates.return_value = {
        "dates_fetched": 0, "failed_dates": [], "repair_failed_dates": [{"date": "2024-01-02", "error": "no rows"}],
    }

    res = svc.update_to_latest()

    chip.refresh_chip_data.assert_called_once()
    assert published == [True]
    assert res["dates_fetched"] == 0
    assert "2024-01-02 重抓失敗, 保留原有資料" in res["message"]


def test_a_failed_new_day_still_catches_up_chips_before_reporting(tmp_path: Path, monkeypatch):
    daily_dir = tmp_path / "taiwan" / "daily"
    svc, mock_refresh = _refresh_service_for(tmp_path, written_on=datetime(2024, 1, 3, 9, tzinfo=TAIPEI))
    monkeypatch.setattr("app.taiwan.bootstrap.resolve_target_latest_trading_date", lambda: date(2024, 1, 3))
    chip = _chip_service()
    svc._chip_service = chip
    mock_refresh.refresh_dates.return_value = {
        "dates_fetched": 0, "failed_dates": [{"date": "2024-01-03", "error": "down"}],
    }

    with pytest.raises(RuntimeError, match="2024-01-03"):
        svc.update_to_latest()
    chip.refresh_chip_data.assert_called_once()
    assert TaiwanDailyStore(data_dir=daily_dir).available_dates() == [date(2024, 1, 2)]


class _Blocking:
    """refresh_dates stand-in that parks the caller until released."""

    def __init__(self, result: dict) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.result = result

    def __call__(self, *args, **kwargs):
        self.entered.set()
        assert self.release.wait(timeout=10)
        return self.result


def _scheduled_service(blocking: _Blocking) -> TaiwanDailyUpdateService:
    stores = [MagicMock() for _ in range(3)]
    for store in stores:
        store.available_dates.return_value = [date(2026, 8, 28)]
        store.repair_dates.return_value = []
    inst = MagicMock()
    inst.refresh_dates.side_effect = blocking
    margin = MagicMock()
    margin.refresh_dates.return_value = {"dates_requested": 0, "dates_fetched": 0, "failed_dates": []}
    return TaiwanDailyUpdateService(
        daily_store=stores[0],
        inst_store=stores[1],
        margin_store=stores[2],
        inst_service=inst,
        margin_service=margin,
        market_inst_service=MagicMock(),
        corporate_action_service=MagicMock(),
    )


def _in_thread(target) -> threading.Thread:
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def test_update_to_latest_refuses_while_the_scheduled_run_is_writing(tmp_path: Path, monkeypatch):
    blocking = _Blocking({"dates_requested": 0, "dates_fetched": 0, "failed_dates": []})
    scheduled = _scheduled_service(blocking)
    thread = _in_thread(lambda: scheduled.run_update(target_date=date(2026, 8, 31), refresh_daily=False))
    assert blocking.entered.wait(timeout=10)
    monkeypatch.setattr("app.taiwan.bootstrap.resolve_target_latest_trading_date", lambda: date(2024, 1, 3))
    svc, mock_refresh = _refresh_service_for(tmp_path, written_on=datetime(2024, 1, 3, 9, tzinfo=TAIPEI))
    try:
        with pytest.raises(RuntimeError, match="更新"):
            svc.update_to_latest()
        mock_refresh.refresh_dates.assert_not_called()
    finally:
        blocking.release.set()
        thread.join(timeout=10)


def test_a_second_update_to_latest_refuses_while_the_first_runs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.taiwan.bootstrap.resolve_target_latest_trading_date", lambda: date(2024, 1, 3))
    svc, mock_refresh = _refresh_service_for(tmp_path, written_on=datetime(2024, 1, 3, 9, tzinfo=TAIPEI))
    blocking = _Blocking({"dates_fetched": 1})
    mock_refresh.refresh_dates.side_effect = blocking
    first = _in_thread(svc.update_to_latest)
    assert blocking.entered.wait(timeout=10)
    try:
        with pytest.raises(RuntimeError, match="更新"):
            svc.update_to_latest()
        assert mock_refresh.refresh_dates.call_count == 1
    finally:
        blocking.release.set()
        first.join(timeout=10)


def test_the_lock_is_released_after_an_update(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.taiwan.bootstrap.resolve_target_latest_trading_date", lambda: date(2024, 1, 3))
    svc, _mock_refresh = _refresh_service_for(tmp_path, written_on=datetime(2024, 1, 3, 9, tzinfo=TAIPEI))

    assert svc.update_to_latest()["dates_fetched"] == 1
    assert svc.update_to_latest()["dates_fetched"] == 1
