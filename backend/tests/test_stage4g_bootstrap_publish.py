"""The history bundle import publishes the enriched table and refreshes the panel caches."""
from __future__ import annotations

import hashlib
import io
import threading
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import polars as pl
import pytest

from app.jobs import daily_pipeline
from app.taiwan import bootstrap
from app.taiwan.bootstrap import TaiwanBootstrapService

BUNDLED_DAY = date(2024, 1, 2)


def _bundle() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        master = io.BytesIO()
        pl.DataFrame(
            {"symbol": ["2330.TWSE"], "name": ["台積電"], "exchange": ["TWSE"], "asset_type": ["stock"]}
        ).write_parquet(master)
        archive.writestr("data/taiwan/security_master.parquet", master.getvalue())
        part = io.BytesIO()
        pl.DataFrame({
            "symbol": ["2330.TWSE"], "date": [BUNDLED_DAY], "open": [590.0], "high": [595.0],
            "low": [588.0], "close": [593.0], "volume": [1000.0], "amount": [593000.0],
            "quote_ts": [1704153600000],
        }).write_parquet(part)
        archive.writestr(f"data/taiwan/daily/date={BUNDLED_DAY.isoformat()}/part.parquet", part.getvalue())
    return buffer.getvalue()


@pytest.fixture
def bundle_job(tmp_path: Path, monkeypatch):
    payload = _bundle()

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

    order: list[tuple[str, object]] = []
    sync = MagicMock()
    sync.return_value.refresh_dates.return_value = {"dates_fetched": 1}
    monkeypatch.setattr(httpx, "Client", _Client)
    monkeypatch.setattr(bootstrap, "taiwan_data_root", lambda: tmp_path / "taiwan")
    monkeypatch.setattr(bootstrap, "import_security_master", lambda _src, _dest: True)
    monkeypatch.setattr(bootstrap, "TaiwanDailyRefreshService", sync)
    monkeypatch.setattr(bootstrap, "resolve_target_latest_trading_date", lambda: date(2024, 1, 3))
    monkeypatch.setattr(
        bootstrap,
        "publish_enriched",
        lambda only_if_behind=False, since=None: order.append(("publish", only_if_behind)) or True,
    )
    monkeypatch.setattr(daily_pipeline, "refresh_enriched_cache", lambda label: order.append(("enriched", label)))
    monkeypatch.setattr(daily_pipeline, "refresh_strategy_cache", lambda label: order.append(("strategy", label)))
    svc = TaiwanBootstrapService(
        download_url="https://mock/bundle.zip", expected_sha256=hashlib.sha256(payload).hexdigest()
    )
    return svc, order


def _run(svc: TaiwanBootstrapService) -> object:
    job_id = svc.start_bootstrap()
    for thread in threading.enumerate():
        if thread.name == f"taiwan-bootstrap-{job_id[:8]}":
            thread.join(timeout=30)
    return svc.get_job(job_id)


def test_an_imported_bundle_is_published_then_the_caches_reload(bundle_job):
    svc, order = bundle_job

    job = _run(svc)

    assert job.status == "success", job.error
    assert [step for step, _ in order] == ["publish", "enriched", "strategy"]
    assert order[0] == ("publish", False)


def test_a_bundle_adding_nothing_publishes_only_a_lagging_table(bundle_job, monkeypatch):
    svc, order = bundle_job
    _run(svc)
    order.clear()
    bootstrap.TaiwanDailyRefreshService.return_value.refresh_dates.return_value = {"dates_fetched": 0}
    monkeypatch.setattr(
        bootstrap,
        "publish_enriched",
        lambda only_if_behind=False, since=None: order.append(("publish", only_if_behind)) and False,
    )

    job = _run(svc)

    assert job.status == "success", job.error
    assert order == [("publish", True)]
