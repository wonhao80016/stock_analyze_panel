"""Unit tests for Taiwan Institutional/Margin enrichment coverage status and
the background historical backfill job (app.taiwan.enrichment_backfill).

Covers:
  - Coverage status query: empty vs populated stores.
  - Backfill job lifecycle: creation, progress fields, completion.
  - Single active job protection (duplicate start returns the same job_id).
  - A zero-row day (e.g. margin not yet published for today) is skipped, not failed.
  - Per-date failures are surfaced in the job result's failed_dates, without
    failing the job as a whole.
  - HTTP endpoints: enrichment-status and backfill job creation/query, with all
    network calls mocked.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.taiwan.enrichment_backfill import (
    TaiwanEnrichmentBackfillService,
    _count_throttled,
    get_enrichment_status,
)
from app.taiwan.institutional_margin_refresh import (
    TaiwanInstitutionalRefreshService,
    TaiwanMarginRefreshService,
)
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar, taipei_now


@pytest.fixture(autouse=True)
def _margin_published_today(monkeypatch):
    """The backfill range ends at today after 16:00; margin waits for 21:40, so pin the margin clock past it."""
    late = taipei_now().replace(hour=23, minute=59)
    monkeypatch.setattr("app.taiwan.institutional_margin_refresh.taipei_now", lambda: late)


def _wait_for_job(svc: TaiwanEnrichmentBackfillService, job_id: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    job = svc.get_job(job_id)
    while job and job.status not in ("success", "failed") and time.time() < deadline:
        time.sleep(0.02)
        job = svc.get_job(job_id)
    return job


# =====================================================================
# 1. Coverage Status
# =====================================================================

class TestEnrichmentStatus:
    def test_status_no_data(self, tmp_path: Path):
        inst_store = TaiwanInstitutionalStore(data_dir=tmp_path / "institutional")
        margin_store = TaiwanMarginStore(data_dir=tmp_path / "margin")

        status = get_enrichment_status(inst_store=inst_store, margin_store=margin_store)

        assert status.institutional.has_data is False
        assert status.institutional.day_count == 0
        assert status.institutional.earliest is None
        assert status.institutional.latest is None
        assert status.margin.has_data is False
        assert status.margin.day_count == 0

    def test_status_has_data(self, tmp_path: Path):
        import polars as pl

        inst_store = TaiwanInstitutionalStore(data_dir=tmp_path / "institutional")
        margin_store = TaiwanMarginStore(data_dir=tmp_path / "margin")

        inst_store.write_batch(pl.DataFrame([
            {"symbol": "2330.TWSE", "date": date(2026, 9, 15), "foreign_net": 100},
            {"symbol": "2330.TWSE", "date": date(2026, 9, 16), "foreign_net": 200},
        ]))
        margin_store.write_batch(pl.DataFrame([
            {"symbol": "2330.TWSE", "date": date(2026, 9, 16), "margin_balance": 1000},
        ]))

        status = get_enrichment_status(inst_store=inst_store, margin_store=margin_store)

        assert status.institutional.has_data is True
        assert status.institutional.day_count == 2
        assert status.institutional.earliest == "2026-09-15"
        assert status.institutional.latest == "2026-09-16"
        assert status.margin.has_data is True
        assert status.margin.day_count == 1
        assert status.margin.earliest == "2026-09-16"
        assert status.margin.latest == "2026-09-16"


# =====================================================================
# 2. Backfill Job Service (mocked providers, 0 network calls)
# =====================================================================

class _StubInstitutionalProvider:
    """Mock institutional provider: 1 row/day for both exchanges, unless the date is in `raise_on`."""

    def __init__(self, raise_on: set[date] | None = None):
        self.raise_on = raise_on or set()
        self.twse_calls: list[date] = []

    @property
    def twse(self):
        return self._Adapter(self, "twse")

    @property
    def tpex(self):
        return self._Adapter(self, "tpex")

    class _Adapter:
        def __init__(self, outer, exch):
            self.outer = outer
            self.exch = exch

        def build_url(self, d):
            return f"mock://{self.exch}/{d}"

        def parse_payload(self, payload, d, url):
            if d in self.outer.raise_on:
                raise RuntimeError(f"simulated upstream error for {d}")
            self.outer.twse_calls.append(d)
            from app.taiwan.enrichment.models import InstitutionalFlow
            sym = "2330.TWSE" if self.exch == "twse" else "8069.TPEX"
            return [
                InstitutionalFlow(
                    symbol=sym, trade_date=d,
                    foreign_buy=1000, foreign_sell=500, foreign_net=500,
                    investment_trust_buy=0, investment_trust_sell=0, investment_trust_net=0,
                    dealer_buy=0, dealer_sell=0, dealer_net=0,
                )
            ]


class _StubMarginProvider:
    """Mock margin provider: 1 row/day, but returns 0 rows for dates in `empty_on`
    (simulating margin data not yet published for today)."""

    def __init__(self, empty_on: set[date] | None = None):
        self.empty_on = empty_on or set()

    @property
    def twse(self):
        return self._Adapter(self, "twse")

    @property
    def tpex(self):
        return self._Adapter(self, "tpex")

    class _Adapter:
        def __init__(self, outer, exch):
            self.outer = outer
            self.exch = exch

        def build_url(self, d):
            return f"mock://{self.exch}/{d}"

        def parse_payload(self, payload, d, url):
            if d in self.outer.empty_on:
                return []
            from app.taiwan.enrichment.models import MarginTrading
            sym = "2330.TWSE" if self.exch == "twse" else "8069.TPEX"
            return [
                MarginTrading(
                    symbol=sym, trade_date=d, unit="shares", source_unit="lots", lot_multiplier=1000,
                    margin_previous_balance=0, margin_buy=0, margin_sell=0, margin_cash_redemption=0,
                    margin_balance=1000, margin_change=0,
                    short_previous_balance=0, short_sell=0, short_cover=0, short_stock_redemption=0,
                    short_balance=0, short_change=0, short_margin_ratio=None,
                )
            ]


def _make_service(tmp_path: Path, inst_provider=None, margin_provider=None) -> TaiwanEnrichmentBackfillService:
    calendar = TaiwanTradingCalendar()
    inst_store = TaiwanInstitutionalStore(data_dir=tmp_path / "institutional")
    margin_store = TaiwanMarginStore(data_dir=tmp_path / "margin")
    inst_service = TaiwanInstitutionalRefreshService(
        store=inst_store, provider=inst_provider or _StubInstitutionalProvider(), calendar=calendar,
    )
    margin_service = TaiwanMarginRefreshService(
        store=margin_store, provider=margin_provider or _StubMarginProvider(), calendar=calendar,
    )
    # Both refresh services delegate JSON fetches to _fetch_json -> _fetch_json_with_retry.
    # Stub providers never look at the payload, but bypass it anyway to guarantee 0 network I/O.
    inst_service._fetch_json = lambda url: {}
    margin_service._fetch_json = lambda url: {}
    return TaiwanEnrichmentBackfillService(inst_service=inst_service, margin_service=margin_service, calendar=calendar)


class TestBackfillJobLifecycle:
    def test_backfill_completes_with_progress_fields(self, tmp_path: Path):
        svc = _make_service(tmp_path)
        job_id = svc.start_backfill(days=3)
        job = _wait_for_job(svc, job_id)

        assert job is not None
        assert job.status == "success"
        assert job.progress == 100
        assert job.total_days == 3
        assert job.result is not None
        assert job.result["institutional"]["dates_fetched"] == 3
        assert job.result["margin"]["dates_fetched"] == 3
        assert job.result["institutional"]["failed_dates"] == []
        assert job.result["margin"]["failed_dates"] == []

    def test_single_active_job_returns_same_job_id(self, tmp_path: Path):
        release = threading.Event()

        class _BlockingInstitutionalProvider(_StubInstitutionalProvider):
            @property
            def twse(self):
                outer = self

                class _BlockingAdapter(_StubInstitutionalProvider._Adapter):
                    def parse_payload(self, payload, d, url):
                        release.wait(timeout=5.0)
                        return super().parse_payload(payload, d, url)

                return _BlockingAdapter(outer, "twse")

        svc = _make_service(tmp_path, inst_provider=_BlockingInstitutionalProvider())
        try:
            job_id_1 = svc.start_backfill(days=1)
            time.sleep(0.05)  # let the thread reach and block inside parse_payload
            job_id_2 = svc.start_backfill(days=1)

            assert job_id_1 == job_id_2
            job = svc.get_job(job_id_1)
            assert job is not None
            assert job.status not in ("success", "failed")
        finally:
            release.set()

        job = _wait_for_job(svc, job_id_1)
        assert job is not None
        assert job.status == "success"

    def test_zero_row_day_is_skipped_not_failed(self, tmp_path: Path):
        """A day with 0 margin rows (not yet published) must land in dates_skipped,
        never in failed_dates, and must not fail the overall job."""
        range_probe = _make_service(tmp_path / "probe")
        _, end_date, total_days = range_probe._resolve_backfill_range(2)

        empty_svc = _make_service(tmp_path / "scenario", margin_provider=_StubMarginProvider(empty_on={end_date}))
        job_id = empty_svc.start_backfill(days=2)
        job = _wait_for_job(empty_svc, job_id)

        assert job is not None
        assert job.status == "success"
        assert job.result["margin"]["failed_dates"] == []
        assert job.result["margin"]["dates_skipped"] >= 1
        assert job.result["margin"]["dates_fetched"] == total_days - 1

    def test_failed_dates_propagate_to_result_without_failing_job(self, tmp_path: Path):
        range_probe = _make_service(tmp_path / "probe")
        _, end_date, total_days = range_probe._resolve_backfill_range(2)

        svc = _make_service(tmp_path, inst_provider=_StubInstitutionalProvider(raise_on={end_date}))
        job_id = svc.start_backfill(days=2)
        job = _wait_for_job(svc, job_id)

        assert job is not None
        assert job.status == "success"
        failed = job.result["institutional"]["failed_dates"]
        assert len(failed) == 1
        assert failed[0]["date"] == str(end_date)
        assert job.result["institutional"]["dates_fetched"] == total_days - 1

    def test_failed_repairs_of_stored_days_are_counted_in_the_completion_message(self, tmp_path: Path):
        range_probe = _make_service(tmp_path / "probe")
        _, end_date, _ = range_probe._resolve_backfill_range(2)

        svc = _make_service(tmp_path / "scenario", inst_provider=_StubInstitutionalProvider(raise_on={end_date}))
        store = svc.inst_service._store
        store.write_batch(pl.DataFrame([{"symbol": "2330.TWSE", "date": end_date, "foreign_net": 1}]))
        written = datetime(end_date.year, end_date.month, end_date.day, 16, 30, tzinfo=TAIPEI_TZ).timestamp()
        os.utime(tmp_path / "scenario" / "institutional" / f"date={end_date.isoformat()}" / "part.parquet", (written, written))
        job = _wait_for_job(svc, svc.start_backfill(days=2))

        assert job is not None
        assert job.status == "success"
        assert job.result["institutional"]["failed_dates"] == []
        assert [f["date"] for f in job.result["institutional"]["repair_failed_dates"]] == [str(end_date)]
        assert "失敗 1；" in job.message  # noqa: RUF001

    def test_throttled_repairs_are_reported_as_throttled(self):
        stats = {"failed_dates": [], "repair_failed_dates": [{"date": "2026-09-23", "error": "HTTP Error 307: redirect"}]}
        assert _count_throttled(stats) == 1


# =====================================================================
# 3. HTTP Endpoints
# =====================================================================

class TestEnrichmentEndpoints:
    def test_enrichment_status_endpoint(self, tmp_path: Path, monkeypatch):
        target_root = tmp_path / "data" / "taiwan"
        monkeypatch.setattr("app.taiwan.data_root.taiwan_data_root", lambda: target_root)

        from app.main import app
        client = TestClient(app, client=("127.0.0.1", 50000))
        resp = client.get("/api/taiwan/enrichment-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["institutional"]["has_data"] is False
        assert data["institutional"]["day_count"] == 0
        assert data["margin"]["has_data"] is False
        assert data["margin"]["day_count"] == 0

    def test_backfill_endpoint_creates_and_reports_job(self, tmp_path: Path, monkeypatch):
        target_root = tmp_path / "data" / "taiwan"
        monkeypatch.setattr("app.taiwan.data_root.taiwan_data_root", lambda: target_root)
        # Block all real network I/O: any JSON fetch returns an empty payload,
        # which both adapters parse as 0 rows (skipped, not failed).
        monkeypatch.setattr(
            "app.taiwan.institutional_margin_refresh._fetch_json_with_retry",
            lambda url, **kwargs: {},
        )

        from app.main import app
        client = TestClient(app, client=("127.0.0.1", 50000))

        resp = client.post("/api/taiwan/enrichment/backfill", json={"days": 1})
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]
        assert job_id

        deadline = time.time() + 5.0
        status = None
        while time.time() < deadline:
            job_resp = client.get(f"/api/taiwan/enrichment/backfill/{job_id}")
            assert job_resp.status_code == 200
            status = job_resp.json()["status"]
            if status in ("success", "failed"):
                break
            time.sleep(0.05)

        assert status == "success"

    def test_backfill_endpoint_unknown_job_returns_404(self, monkeypatch, tmp_path: Path):
        target_root = tmp_path / "data" / "taiwan"
        monkeypatch.setattr("app.taiwan.data_root.taiwan_data_root", lambda: target_root)

        from app.main import app
        client = TestClient(app, client=("127.0.0.1", 50000))
        resp = client.get("/api/taiwan/enrichment/backfill/does-not-exist")
        assert resp.status_code == 404
