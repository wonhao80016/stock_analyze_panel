"""Review follow-ups: latest-row cache invalidation, empty fundamentals lists, per-month official retries,
the busy update-to-latest response and long count windows."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import httpx
import polars as pl
import pytest
from fastapi import HTTPException

from app.api.taiwan import update_taiwan_history_to_latest
from app.services import kline_sync
from app.taiwan import daily_store as daily_store_module
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import TaiwanUpdateBusyError
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
from tests.test_stage5e_official_sources import REVENUE_ROW, _install, _Server
from tests.test_stage5e_store_reads import (
    DAYS,
    FIRST,
    _daily_rows,
    _institutional_rows,
    _write_partitions,
)


def _cache_key(path) -> str:
    return daily_store_module._listing_sessions_key(path)


def test_a_daily_write_drops_the_cached_latest_rows(tmp_path):
    _write_partitions(tmp_path, _daily_rows)
    store = TaiwanDailyStore(data_dir=tmp_path)
    store.read_latest_per_symbol()
    assert _cache_key(tmp_path) in daily_store_module._latest_rows_cache

    store.write_batch(_daily_rows(FIRST, ["1101.TWSE"]))

    assert _cache_key(tmp_path) not in daily_store_module._latest_rows_cache


def test_an_institutional_write_drops_the_cached_latest_rows(tmp_path):
    store = TaiwanInstitutionalStore(data_dir=tmp_path)
    store.write_batch(_institutional_rows(FIRST, ["2330.TWSE"]))
    store.read_latest_per_symbol()
    assert _cache_key(tmp_path) in daily_store_module._latest_rows_cache

    store.write_batch(_institutional_rows(DAYS[1], ["2330.TWSE"]))

    assert _cache_key(tmp_path) not in daily_store_module._latest_rows_cache


def test_a_partition_that_failed_to_read_is_read_again_next_time(tmp_path, monkeypatch):
    _write_partitions(tmp_path, _daily_rows)
    store = TaiwanDailyStore(data_dir=tmp_path)
    latest_part = f"date={DAYS[-1].isoformat()}"
    real_read = pl.read_parquet
    failures = [1]

    def locked_once(source, *args, **kwargs):
        if latest_part in str(source) and failures:
            failures.pop()
            raise OSError("partition is locked by a writer")
        return real_read(source, *args, **kwargs)

    monkeypatch.setattr(pl, "read_parquet", locked_once)

    first = store.read_latest_per_symbol(["2330.TWSE"])
    second = store.read_latest_per_symbol(["2330.TWSE"])

    assert first["date"].to_list() == [DAYS[-2]]
    assert second["date"].to_list() == [DAYS[-1]]


def test_an_empty_fundamentals_list_is_asked_for_again_after_the_failure_window(monkeypatch):
    server = _Server(httpx.Response(200, json=[]), httpx.Response(200, json=[REVENUE_ROW]))
    _install(monkeypatch, server)
    from app.taiwan import fundamentals

    now = [1000.0]
    monkeypatch.setattr(fundamentals, "monotonic", lambda: now[0])
    provider = fundamentals.TaiwanOfficialFundamentals()
    with pytest.raises(LookupError):
        provider.monthly_revenue("2330.TWSE", "TWSE")
    now[0] += fundamentals.FAILURE_CACHE_SECONDS - 1
    with pytest.raises(LookupError):
        provider.monthly_revenue("2330.TWSE", "TWSE")
    assert len(server.requests) == 1

    now[0] += 2
    got = provider.monthly_revenue("2330.TWSE", "TWSE")

    assert got.values["revenue"] == 1_000_000
    assert len(server.requests) == 2


def _month_payload(month: date) -> dict:
    roc = f"{month.year - 1911}/{month.month:02d}/03"
    return {"data": [[roc, "1,000", "100,000", "100.0", "101.0", "99.0", "100.5", "0.5", "10"]]}


def test_only_the_month_that_failed_is_fetched_again(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    provider = TaiwanHybridProvider()
    requested: list[str] = []
    failed_once: list[str] = []

    def fake_json(url: str) -> object:
        requested.append(url)
        month = datetime.strptime(url.split("date=")[1][:8], "%Y%m%d").date()
        if month.month == 7 and not failed_once:
            failed_once.append(url)
            raise httpx.ConnectError("reset")
        return _month_payload(month)

    monkeypatch.setattr(provider.official, "_json", fake_json)

    result = provider.get_daily(["2330.TWSE"], start_time=datetime(2026, 6, 1), end_time=datetime(2026, 8, 28))

    assert sorted(d.month for d in result["date"].to_list()) == [6, 7, 8]
    assert "status" not in result.columns or set(result["status"]) == {"official"}
    assert len(requested) == 4


def test_a_busy_update_to_latest_answers_409_without_a_traceback(caplog):
    svc = MagicMock()
    svc.update_to_latest.side_effect = TaiwanUpdateBusyError("另一個台股資料更新正在執行, 請稍後再試")
    caplog.set_level(logging.ERROR, logger="app.api.taiwan")
    with (
        patch("app.api.taiwan.TaiwanBootstrapService", return_value=svc),
        patch("app.jobs.daily_pipeline.refresh_benchmark_index", return_value=None),
        pytest.raises(HTTPException) as exc,
    ):
        update_taiwan_history_to_latest()

    assert exc.value.status_code == 409
    assert "正在執行" in exc.value.detail
    assert [r for r in caplog.records if r.exc_info] == []


def test_a_long_count_window_covers_2000_sessions():
    end = datetime(2026, 10, 23)

    start, _ = kline_sync._window_for_count(2000, None, end)

    # 2000 Taiwan sessions back from 2026-10-23 reach into 2018; allow for every holiday run.
    assert start <= datetime(2018, 6, 1)
