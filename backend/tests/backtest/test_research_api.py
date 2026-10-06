from __future__ import annotations

from datetime import date

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api import backtest as api


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/backtest/run"),
        ("POST", "/api/backtest/factor/run"),
        ("POST", "/api/backtest/factor/batch"),
        ("GET", "/api/backtest/factor/columns"),
    ],
)
def test_removed_legacy_backtest_routes_are_not_registered(method, path):
    app = FastAPI()
    app.include_router(api.router)

    assert path not in {route.path for route in api.router.routes}
    assert TestClient(app).request(method, path).status_code == 404


def test_candidate_api_create_list_and_update(monkeypatch, tmp_path):
    monkeypatch.setattr(api.settings, "data_dir", tmp_path)
    created = api.candidate_create(api.CandidateCreateRequest(
        kind="factor",
        name="RSI 候选",
        source_id="rsi_14",
        config={"factor_name": "rsi_14"},
        metrics={"ic_mean": 0.03},
        data_as_of=date(2026, 8, 11),
    ))

    assert api.candidates_list()["items"][0]["id"] == created["id"]
    updated = api.candidate_update(
        created["id"],
        api.CandidateUpdateRequest(status="validated"),
    )
    assert updated["status"] == "validated"


def test_candidate_api_returns_clear_error_for_corrupt_file(monkeypatch, tmp_path):
    monkeypatch.setattr(api.settings, "data_dir", tmp_path)
    path = tmp_path / "user_data" / "research_candidates.json"
    path.parent.mkdir(parents=True)
    path.write_text("not-json", encoding="utf-8")

    with pytest.raises(HTTPException) as exc_info:
        api.candidates_list()
    assert exc_info.value.status_code == 500
    assert "损坏" in str(exc_info.value.detail)
