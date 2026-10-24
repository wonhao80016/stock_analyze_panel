from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.backtest.mining_runtime as mining_runtime
from app.api.mining import router
from app.backtest.mining import (
    FactorMetric,
    MiningBudget,
    MiningRequest,
    MiningService,
    generate_nested_folds,
    validation_config_for_profile,
)
from app.services import preferences
from app.services.mining_jobs import MiningRunStore
from app.services.mining_reevaluation import PublishedStrategyReevaluator
from app.services.mining_schedule import MINING_ALGORITHM_VERSION
from app.strategy import monitor_rules
from tests.test_stage3b_mining_reevaluation import (
    _FAILING,
    _OUTDATED_VERSION,
    _PASSING,
    _ApiManager,
    _ApiRepo,
    _complete_run,
    _factor_definition,
    _origin_row,
    _published_setup,
    _write_run,
)

_SINGLE_FACTOR = {
    "kind": "factor_rank",
    "factor_names": ["rsi_14"],
    "scoring": {"rsi_14": 1.0},
    "directions": {"rsi_14": "low"},
}


def _publish_second(store, service, *, strategy_ids: list[str]) -> str:
    """Publish a second mined strategy from ``z-run`` and mark its run outdated."""
    request = {
        "factor_names": ["rsi_14"],
        "strategy_ids": strategy_ids,
        "asset_type": "stock",
        "start": "2025-01-01",
        "end": None,
        "budget_profile": "balanced",
    }
    signature = _write_run(
        store, "z-run", _SINGLE_FACTOR, _PASSING,
        algorithm_version=MINING_ALGORITHM_VERSION, request=request,
    )
    published = service.publish("z-run", signature)
    store.write_summary("z-run", {
        "data_as_of": "2026-01-09",
        "algorithm_version": _OUTDATED_VERSION,
        "methodology_version": "factor_v2",
    })
    return published["strategy_id"]


class _Reruns:
    """Fake job starter keyed by the origin request's factor list."""

    def __init__(self, store: MiningRunStore, engine, outcomes: dict[tuple, tuple]) -> None:
        self.store = store
        self.engine = engine
        self.outcomes = outcomes
        self.started: list[dict[str, Any]] = []

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        missing = [sid for sid in request.get("strategy_ids") or [] if not self.engine.has(sid)]
        if missing:
            raise ValueError(f"unknown strategy: {missing}")
        self.started.append(dict(request))
        definition, metrics, status = self.outcomes[tuple(request["factor_names"])]
        run_id = f"rerun-{len(self.started)}"
        self.store.create(request, {"generation": run_id}, run_id=run_id)
        if status != "queued":
            self.store.transition_status(run_id, "running")
            if status == "failed":
                self.store.transition_status(run_id, "failed", error="worker crashed")
            else:
                _complete_run(
                    self.store, run_id, definition, metrics,
                    algorithm_version=MINING_ALGORITHM_VERSION,
                    status=status, data_as_of="2026-01-09",
                )
        return self.store.get(run_id)


def _ledger(tmp_path: Path) -> dict[str, Any]:
    path = tmp_path / "research" / "mining" / "reevaluation" / "ledger.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _source(tmp_path: Path, strategy_id: str) -> Path:
    return tmp_path / "strategies" / "custom" / f"{strategy_id}.py"


def _api_client(tmp_path, store, engine, service) -> tuple[TestClient, _ApiManager]:
    enriched = [date(2025, 1, 1) + timedelta(days=index) for index in range(219)]
    for value in enriched:
        partition = tmp_path / "kline_daily_enriched" / f"date={value.isoformat()}"
        partition.mkdir(parents=True, exist_ok=True)
        (partition / "part.parquet").touch()
    app = FastAPI()
    app.include_router(router)
    app.state.repo = _ApiRepo(tmp_path)
    app.state.mining_manager = _ApiManager(store)
    app.state.strategy_engine = engine
    app.state.mining_candidate_service = service
    return TestClient(app), app.state.mining_manager


def test_scheduled_request_is_rerun_instead_of_delisted(tmp_path) -> None:
    scheduled_request = {
        "factor_names": ["turnover_rate", "rsi_14"],
        "strategy_ids": [],
        "symbols": None,
        "asset_type": "stock",
        "start": None,
        "end": "2025-08-07",
        "budget_profile": "exploratory",
        "require_regime": True,
    }
    store, engine, service, _sig, strategy_id, _ = _published_setup(
        tmp_path, request=scheduled_request, data_as_of="2025-08-07"
    )
    client, manager = _api_client(tmp_path, store, engine, service)

    response = client.post("/api/backtest/mining/published/reevaluate")

    assert response.status_code == 200, response.text
    assert [item["status"] for item in response.json()["items"]] == ["pending"]
    [(rerun_request, source)] = manager.started
    assert source == "reevaluation"
    assert rerun_request == scheduled_request
    assert _source(tmp_path, strategy_id).is_file()


@pytest.mark.parametrize("reruns", ["refused", "failed"])
def test_rerun_problems_are_held_for_review_without_delisting(tmp_path, reruns) -> None:
    store, engine, service, _sig, strategy_id, _ = _published_setup(tmp_path)
    if reruns == "failed":
        starter = _Reruns(store, engine, {
            ("turnover_rate", "rsi_14"): (_factor_definition(), _FAILING, "failed"),
        })
    else:
        calls: list[dict] = []

        def starter(request):
            calls.append(request)
            raise ValueError("balanced mining requires more enriched trading bars")

    first = PublishedStrategyReevaluator(service, starter).run()
    second = PublishedStrategyReevaluator(service, starter).run()

    assert [item["status"] for item in first["items"]] == ["needs_review"]
    assert first["items"][0]["reasons"]
    assert second["items"] == first["items"]
    assert _source(tmp_path, strategy_id).is_file()
    assert engine.has(strategy_id)
    assert _origin_row(store)["published_strategy_id"] == strategy_id
    started = starter.started if reruns == "failed" else calls
    assert len(started) == 1
    [entry] = _ledger(tmp_path)["entries"].values()
    assert entry["status"] == "needs_review"


def test_strategy_benchmarked_on_a_delisted_strategy_is_held_for_review(tmp_path) -> None:
    store, engine, service, _sig, first_id, _ = _published_setup(tmp_path)
    second_id = _publish_second(store, service, strategy_ids=[first_id])
    reruns = _Reruns(store, engine, {
        ("turnover_rate", "rsi_14"): (_factor_definition(), _FAILING, "succeeded"),
        ("rsi_14",): (_SINGLE_FACTOR, _PASSING, "succeeded"),
    })

    report = PublishedStrategyReevaluator(service, reruns).run()

    by_id = {item["strategy_id"]: item for item in report["items"]}
    assert by_id[first_id]["status"] == "delisted"
    assert by_id[second_id]["status"] == "needs_review"
    assert any(first_id in reason for reason in by_id[second_id]["reasons"])
    assert _source(tmp_path, second_id).is_file()
    assert len(reruns.started) == 1


def test_composite_dependents_defer_the_delisting(tmp_path, monkeypatch) -> None:
    store, engine, service, _sig, strategy_id, _ = _published_setup(tmp_path)
    monkeypatch.setattr(
        engine, "find_dependents", lambda sid: ["my_combo"] if sid == strategy_id else []
    )
    reruns = _Reruns(store, engine, {
        ("turnover_rate", "rsi_14"): (_factor_definition(), _FAILING, "succeeded"),
    })

    report = PublishedStrategyReevaluator(service, reruns).run()

    [item] = report["items"]
    assert item["status"] == "needs_review"
    assert any("my_combo" in reason for reason in item["reasons"])
    assert _source(tmp_path, strategy_id).is_file()
    assert _origin_row(store)["published_strategy_id"] == strategy_id


@pytest.fixture
def restore_monitor_ids():
    previous = preferences.get_strategy_monitor_ids()
    yield
    preferences.set_realtime_monitor_config({"strategy_monitor_ids": previous})


def test_delisting_disables_strategy_monitoring(tmp_path, restore_monitor_ids) -> None:
    store, engine, service, _sig, strategy_id, _ = _published_setup(tmp_path)
    preferences.set_realtime_monitor_config({"strategy_monitor_ids": [strategy_id, "kept"]})
    rule_id = monitor_rules.strategy_rule_id(strategy_id)
    monitor_rules.save_one(tmp_path, {
        "id": rule_id,
        "name": "mined monitor",
        "type": "strategy",
        "strategy_id": strategy_id,
        "enabled": True,
    })
    reruns = _Reruns(store, engine, {
        ("turnover_rate", "rsi_14"): (_factor_definition(), _FAILING, "succeeded"),
    })

    [item] = PublishedStrategyReevaluator(service, reruns).run()["items"]

    assert item["status"] == "delisted"
    assert preferences.get_strategy_monitor_ids() == ["kept"]
    assert monitor_rules.load_one(tmp_path, rule_id)["enabled"] is False
    record = json.loads(
        (tmp_path / item["quarantine"] / "delisting.json").read_text(encoding="utf-8")
    )
    assert record["disabled_monitor_rules"] == [rule_id]
    assert record["removed_from_monitor_ids"] is True


def test_only_one_rerun_is_outstanding_at_a_time(tmp_path) -> None:
    store, engine, service, _sig, first_id, _ = _published_setup(tmp_path)
    second_id = _publish_second(store, service, strategy_ids=[])
    reruns = _Reruns(store, engine, {
        ("turnover_rate", "rsi_14"): (_factor_definition(), _PASSING, "queued"),
        ("rsi_14",): (_SINGLE_FACTOR, _PASSING, "queued"),
    })

    first = PublishedStrategyReevaluator(service, reruns).run()
    second = PublishedStrategyReevaluator(service, reruns).run()

    statuses = {item["strategy_id"]: item["status"] for item in first["items"]}
    assert statuses == {first_id: "pending", second_id: "waiting"}
    assert {item["strategy_id"]: item["status"] for item in second["items"]} == statuses
    assert len(reruns.started) == 1

    store.transition_status("rerun-1", "running")
    _complete_run(
        store, "rerun-1", _factor_definition(), _PASSING,
        algorithm_version=MINING_ALGORITHM_VERSION, status="succeeded", data_as_of="2026-01-09",
    )
    third = PublishedStrategyReevaluator(service, reruns).run()

    assert {item["strategy_id"]: item["status"] for item in third["items"]} == {
        first_id: "passed",
        second_id: "pending",
    }
    assert len(reruns.started) == 2


def test_restore_moves_the_source_back_and_repairs_only_its_backlink(tmp_path) -> None:
    store, engine, service, signature, strategy_id, _ = _published_setup(tmp_path)
    source = _source(tmp_path, strategy_id).read_bytes()
    reruns = _Reruns(store, engine, {
        ("turnover_rate", "rsi_14"): (_factor_definition(), _FAILING, "succeeded"),
    })
    reevaluator = PublishedStrategyReevaluator(service, reruns)
    [delisted] = reevaluator.run()["items"]
    path = store.artifact_path("origin-run", "candidates")
    pl.read_parquet(path).with_columns(
        pl.lit("later-candidate").alias("promoted_candidate_id")
    ).write_parquet(path)

    restored = reevaluator.restore(strategy_id)

    assert restored["status"] == "restored"
    assert _source(tmp_path, strategy_id).read_bytes() == source
    assert engine.has(strategy_id)
    row = _origin_row(store)
    assert row["published_strategy_id"] == strategy_id
    assert row["promoted_candidate_id"] == "later-candidate"
    assert (tmp_path / delisted["quarantine"] / "candidates.parquet").is_file()

    again = reevaluator.run()

    assert [item["status"] for item in again["items"]] == ["restored"]
    assert len(reruns.started) == 1
    assert _source(tmp_path, strategy_id).is_file()
    with pytest.raises(ValueError, match="not delisted"):
        reevaluator.restore(strategy_id)
    del signature


def test_restore_endpoint(tmp_path) -> None:
    store, engine, service, _sig, strategy_id, _ = _published_setup(tmp_path)
    reruns = _Reruns(store, engine, {
        ("turnover_rate", "rsi_14"): (_factor_definition(), _FAILING, "succeeded"),
    })
    PublishedStrategyReevaluator(service, reruns).run()
    client, _manager = _api_client(tmp_path, store, engine, service)

    response = client.post(f"/api/backtest/mining/published/{strategy_id}/restore")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "restored"
    assert _source(tmp_path, strategy_id).is_file()


def test_runtime_slippage_default_follows_the_shared_mining_constant(monkeypatch) -> None:
    monkeypatch.setattr(mining_runtime, "MINING_DEFAULT_SLIPPAGE_BPS", 7.5)
    captured: dict[str, Any] = {}

    def stop(value, name, *_args, **_kwargs):
        if name != "slippage_bps":
            return float(value)
        captured[name] = value
        raise _StopError

    class _StopError(Exception):
        pass

    monkeypatch.setattr(mining_runtime, "_bounded_float", stop)
    monkeypatch.setattr(
        mining_runtime,
        "enriched_partition_dates",
        lambda *_args, **_kwargs: [date(2020, 1, 1) + timedelta(days=i) for i in range(900)],
    )
    with pytest.raises(_StopError):
        mining_runtime._decode_runtime_request(
            {"run_id": "r", "request": {"factor_names": ["rsi_14"], "budget_profile": "exploratory"}},
            Path("."),
            None,
        )
    assert captured == {"slippage_bps": 7.5}


@pytest.mark.parametrize("profile", ["exploratory", "balanced"])
def test_profile_fold_geometry_never_scores_inside_selection_or_purge(profile) -> None:
    validation = validation_config_for_profile(profile)
    start = date(2020, 1, 1)
    labels = [(start + timedelta(days=index)).isoformat() for index in range(1200)]
    nested = generate_nested_folds(labels, validation)
    boundary = nested[1].outer.test_start
    rows = []
    for day_id, label in enumerate(labels):
        for asset in range(3):
            rows.append({
                "symbol": f"{asset}.TWSE",
                "date": date.fromisoformat(label),
                "good": float(asset + (day_id % 2) * 0.1),
                "noise": float((asset * 2 + day_id) % 3),
                "_next_return": float(asset),
            })

    class Evaluator:
        def evaluate_candidate(self, *_args):
            raise AssertionError("label evaluation is expected")

        def evaluate_candidate_labels(self, _train, test_labels, definition):
            prefer_good = test_labels[0] < boundary
            is_good = definition.get("factor_names", [None])[0] == "good"
            return {"score": 2.0 if is_good == prefer_good else 1.0}

    request = MiningRequest(
        factor_names=("good", "noise"),
        budget=MiningBudget(max_combination_size=1, max_proxy_trials=256, max_trials=256),
        validation=validation,
        profile=profile,
    )
    result = MiningService().run(
        pl.DataFrame(rows),
        request,
        metric_provider=lambda _train, _names: (
            FactorMetric("good", 1.0, 1.0, 1.0, 0.1),
            FactorMetric("noise", 0.9, 0.9, 1.0, 0.1),
        ),
        evaluator=Evaluator(),
    )

    first_selected: dict[str, int] = {}
    for index, fold in enumerate(result.folds):
        if fold.selected_candidate_id is not None:
            first_selected.setdefault(fold.selected_candidate_id, index)
    assert len(first_selected) == 2
    checked = 0
    for index, fold in enumerate(result.folds):
        test_labels = set(nested[index].outer.test_labels)
        for candidate_id, _ in fold.cross_evaluations:
            origin = nested[first_selected[candidate_id]].outer
            assert not test_labels & set(origin.train_labels)
            assert not test_labels & set(origin.purge_labels)
            checked += 1
    assert checked > 0
