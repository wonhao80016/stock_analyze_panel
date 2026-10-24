"""Chip refreshes report where they stopped, a bundle import publishes its older days, and run_update notes deferred margin."""
from __future__ import annotations

import hashlib
import io
import threading
import time
import zipfile
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import httpx
import polars as pl
import pytest

from app.config import settings
from app.jobs import daily_pipeline
from app.taiwan import bootstrap, daily_update
from app.taiwan.bootstrap import TaiwanBootstrapService
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import TaiwanDailyUpdateService
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.enrichment_backfill import TaiwanEnrichmentBackfillService
from app.taiwan.institutional_margin_refresh import TaiwanInstitutionalRefreshService
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.realtime.calendar import TAIPEI_TZ
from app.taiwan.share_capital_store import TaiwanShareCapitalStore
from tests.test_stage4g_chip_refresh_gaps import _Calendar, _flow, _Provider
from tests.test_taiwan_market_institutional import (
    SEP22,
    SEP23,
    SEP24,
    SEP25,
    _Market,
    _service,
    _stored,
    _taipei,
)

SYMBOL = "2330.TWSE"
SEP29_NOON = (date(2026, 9, 29), 12)


@pytest.fixture(autouse=True)
def _no_spacing(monkeypatch):
    monkeypatch.setattr("app.taiwan.institutional_margin_refresh.INTER_REQUEST_DELAY_SEC", 0)


# F1: market-wide institutional amounts


def test_market_amounts_stop_at_a_failed_new_day(tmp_path):
    market = _Market(tpex={SEP23: RuntimeError("TPEx down")})
    svc, store = _service(tmp_path, market)
    _stored(store, tmp_path, SEP22, _taipei(*SEP29_NOON))

    stats = svc.refresh_days([SEP23, SEP24, SEP25])

    assert store.available_dates() == [SEP22]
    assert [f["date"] for f in stats["failed_dates"]] == [str(SEP23)]
    assert (stats["stopped_at"], stats["dates_not_attempted"]) == (str(SEP23), 2)
    assert market.twse.calls == [SEP23]


def test_market_amounts_keep_going_past_a_failed_stored_range_day(tmp_path):
    market = _Market(tpex={SEP23: RuntimeError("TPEx down")})
    svc, store = _service(tmp_path, market)
    _stored(store, tmp_path, SEP22, _taipei(*SEP29_NOON))
    _stored(store, tmp_path, SEP24, _taipei(*SEP29_NOON))

    stats = svc.refresh_days([SEP23, SEP25])

    assert SEP25 in store.available_dates()
    assert [f["date"] for f in stats["repair_failed_dates"]] == [str(SEP23)]
    assert stats["stopped_at"] is None



# F3: where a refresh stopped reaches the backfill message


def test_an_institutional_run_reports_where_it_stopped(tmp_path):
    days = [date(2026, 9, d) for d in (14, 15, 16, 17, 18)]
    store = TaiwanInstitutionalStore(data_dir=tmp_path / "institutional")
    service = TaiwanInstitutionalRefreshService(
        store=store, provider=_Provider(_flow, raise_on={days[1]}), calendar=_Calendar()
    )
    service._fetch_json = lambda url: {}

    stats = service.refresh_dates(days[0], days[-1])

    assert store.available_dates() == [days[0]]
    assert (stats["stopped_at"], stats["dates_not_attempted"]) == (str(days[1]), 3)


def test_the_backfill_message_says_it_stopped_and_from_when(tmp_path):
    inst = MagicMock()
    inst.refresh_dates.return_value = {
        "dates_requested": 2, "dates_fetched": 1, "dates_skipped": 0, "total_rows_written": 1,
        "failed_dates": [{"date": "2026-09-15", "error": "down"}], "repair_failed_dates": [],
        "stopped_at": "2026-09-15", "dates_not_attempted": 3,
    }
    margin = MagicMock()
    margin.refresh_dates.return_value = {
        "dates_requested": 5, "dates_fetched": 5, "dates_skipped": 0, "total_rows_written": 5,
        "failed_dates": [], "stopped_at": None, "dates_not_attempted": 0,
    }
    svc = TaiwanEnrichmentBackfillService(inst_service=inst, margin_service=margin, calendar=_Calendar())

    job_id = svc.start_backfill(days=5)
    deadline = time.time() + 5
    while svc.get_job(job_id).status not in ("success", "failed") and time.time() < deadline:
        time.sleep(0.02)
    job = svc.get_job(job_id)

    assert job.status == "success"
    assert "回補完成" not in job.message
    assert "2026-09-15" in job.message
    assert "3 天未嘗試" in job.message


# F2: a bundle adding days older than the published window


def _bars(days: list[date], base: float) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "symbol": SYMBOL, "date": d, "open": base + i, "high": base + i, "low": base + i,
                "close": base + i, "volume": 1_000_000.0, "amount": (base + i) * 1_000_000.0,
                "quote_ts": None,
            }
            for i, d in enumerate(days)
        ],
        schema_overrides={"quote_ts": pl.Int64},
    )


def _weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _bundle(days: list[date]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        master = io.BytesIO()
        pl.DataFrame({"symbol": [SYMBOL], "name": ["x"], "exchange": ["TWSE"], "asset_type": ["stock"]}).write_parquet(master)
        archive.writestr("data/taiwan/security_master.parquet", master.getvalue())
        frame = _bars(days, 900.0)
        for d in days:
            part = io.BytesIO()
            frame.filter(pl.col("date") == d).write_parquet(part)
            archive.writestr(f"data/taiwan/daily/date={d.isoformat()}/part.parquet", part.getvalue())
    return buffer.getvalue()


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    from app.taiwan import universe
    from app.taiwan.data_root import taiwan_data_root

    master = universe.TaiwanSecurityMaster(cache_path=taiwan_data_root() / "security_master.parquet")
    assert master.load_cache()
    monkeypatch.setattr(universe, "_default_master", master)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(TaiwanShareCapitalStore, "refresh", lambda self: 0)
    monkeypatch.setattr(daily_update, "_refresh_corporate_actions", lambda window, service: None)
    monkeypatch.setattr(daily_pipeline, "refresh_enriched_cache", lambda label: None)
    monkeypatch.setattr(daily_pipeline, "refresh_strategy_cache", lambda label: None)
    return tmp_path


def test_a_bundle_of_older_days_publishes_every_imported_day(isolated_data_dir, monkeypatch):
    recent = _weekdays(date(2026, 9, 1), 10)
    older = _weekdays(date(2026, 6, 1), 40)
    TaiwanDailyStore().write_batch(_bars(recent, 1000.0))
    TaiwanEnrichedRefreshService().refresh()
    payload = _bundle(older)

    class _Response:
        status_code = 200

        def __init__(self):
            self.headers = {"content-length": str(len(payload))}

        def iter_bytes(self, chunk_size=1024):
            yield payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def stream(self, method, url):
            return _Response()

    monkeypatch.setattr(httpx, "Client", _Client)
    monkeypatch.setattr(bootstrap, "import_security_master", lambda _src, _dest: True)
    monkeypatch.setattr(bootstrap, "resolve_target_latest_trading_date", lambda: recent[-1])
    svc = TaiwanBootstrapService(download_url="https://mock/b.zip", expected_sha256=hashlib.sha256(payload).hexdigest())

    job_id = svc.start_bootstrap()
    for thread in threading.enumerate():
        if thread.name == f"taiwan-bootstrap-{job_id[:8]}":
            thread.join(timeout=60)

    assert svc.get_job(job_id).status == "success", svc.get_job(job_id).error
    root = isolated_data_dir / "kline_daily_enriched"
    missing = [d for d in older if not (root / f"date={d.isoformat()}" / "part.parquet").exists()]
    assert missing == []


# F4: run_update tells when margin waits for its publication


def test_run_update_notes_margin_deferred_before_its_publication(monkeypatch):
    session = date(2026, 9, 18)
    monkeypatch.setattr(daily_update, "taipei_now", lambda: datetime(2026, 9, 18, 17, 0, tzinfo=TAIPEI_TZ))
    monkeypatch.setattr(daily_update, "publish_enriched", lambda *a, **k: False)
    stores = {}
    for name in ("daily", "inst", "margin"):
        store = MagicMock()
        store.available_dates.return_value = [date(2026, 9, 17)]
        store.repair_dates.return_value = []
        store.provisional_dates.return_value = []
        stores[name] = store
    ok = {"dates_requested": 1, "dates_fetched": 1, "total_rows_written": 1, "failed_dates": []}
    margin = MagicMock()
    margin.refresh_dates.return_value = ok
    market = MagicMock()
    market.refresh_through.return_value = {}
    svc = TaiwanDailyUpdateService(
        daily_store=stores["daily"], inst_store=stores["inst"], margin_store=stores["margin"],
        daily_service=MagicMock(refresh_dates=MagicMock(return_value=ok)),
        inst_service=MagicMock(refresh_dates=MagicMock(return_value=ok)),
        margin_service=margin, calendar=_Calendar(), market_inst_service=market,
        corporate_action_service=MagicMock(), security_master=None,
    )

    result = svc.run_update(target_date=session, refresh_master=False)

    assert margin.refresh_dates.call_count == 0
    assert "21:40" in (result.margin.note or "")
