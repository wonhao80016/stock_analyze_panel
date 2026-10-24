"""The data-source job timeout preferences are gone: nothing reads them, so nothing may save them."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import settings as settings_api
from app.services import pipeline_jobs, preferences


def test_job_store_uses_fixed_stall_thresholds(monkeypatch, tmp_path):
    monkeypatch.setattr(preferences, "load", lambda: {
        "data_source_job_timeout_s": 99_999,
        "data_source_long_job_timeout_s": 88_888,
    })
    store = pipeline_jobs.JobStore(store_dir=tmp_path)

    regular_id, _ = store.create()
    assert store.get(regular_id)["timeout_s"] == pipeline_jobs.DEFAULT_JOB_TIMEOUT_S
    store.fail(regular_id, "done")

    long_id, _ = store.create(long_running=True)
    assert store.get(long_id)["timeout_s"] == pipeline_jobs.LONG_JOB_TIMEOUT_S


def test_timeout_preferences_endpoint_is_removed():
    app = FastAPI()
    app.include_router(settings_api.router)
    client = TestClient(app)

    resp = client.put("/api/settings/preferences/data-source-job-timeouts", json={
        "data_source_job_timeout_s": 600,
        "data_source_long_job_timeout_s": 900,
    })

    assert resp.status_code in (404, 405)
    assert not hasattr(settings_api, "DataSourceJobTimeoutPrefs")


def test_preferences_response_no_longer_offers_job_timeouts(monkeypatch):
    monkeypatch.setattr(preferences, "load", lambda: {})
    prefs = settings_api.get_preferences()

    assert "data_source_job_timeout_s" not in prefs
    assert "data_source_long_job_timeout_s" not in prefs
