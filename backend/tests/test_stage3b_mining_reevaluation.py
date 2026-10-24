from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config as app_config
from app.api.mining import router
from app.backtest.candidates import CandidateStore
from app.backtest.mining import compute_candidate_signature
from app.services.mining_candidates import MiningCandidateService
from app.services.mining_jobs import MiningRunStore
from app.services.mining_reevaluation import PublishedStrategyReevaluator
from app.services.mining_schedule import MINING_ALGORITHM_VERSION
from app.strategy.engine import StrategyEngine

_OUTDATED_VERSION = "mining-v2"
_PASSING = {
    "oos_sharpe": 0.9,
    "oos_return": 0.08,
    "oos_max_drawdown": -0.12,
    "oos_positive_fold_ratio": 1.0,
    "oos_n_trades": 70,
    "valid_folds": 3,
    "confidence": "standard",
}
_FAILING = {**_PASSING, "oos_positive_fold_ratio": 0.5, "valid_folds": 1}


def _factor_definition() -> dict:
    return {
        "kind": "factor_rank",
        "factor_names": ["turnover_rate", "rsi_14"],
        "scoring": {"turnover_rate": 1.0, "rsi_14": 2.0},
        "directions": {"turnover_rate": "high", "rsi_14": "low"},
    }


def _existing_definition() -> dict:
    return {"kind": "existing_strategy", "strategy_id": "ma_golden_cross"}


def _request() -> dict[str, Any]:
    return {
        "factor_names": ["turnover_rate", "rsi_14"],
        "strategy_ids": ["ma_golden_cross"],
        "asset_type": "stock",
        "start": "2025-01-01",
        "end": None,
        "budget_profile": "balanced",
        "slippage_bps": 5.0,
    }


def _write_run(
    store: MiningRunStore,
    run_id: str,
    definition: dict,
    metrics: dict[str, Any],
    *,
    algorithm_version: str,
    status: str = "succeeded",
    request: dict[str, Any] | None = None,
    data_as_of: str = "2026-01-09",
) -> str:
    store.create(request or _request(), {"generation": run_id}, run_id=run_id)
    return _complete_run(
        store,
        run_id,
        definition,
        metrics,
        algorithm_version=algorithm_version,
        status=status,
        data_as_of=data_as_of,
    )


def _complete_run(
    store: MiningRunStore,
    run_id: str,
    definition: dict,
    metrics: dict[str, Any],
    *,
    algorithm_version: str,
    status: str,
    data_as_of: str,
) -> str:
    signature = compute_candidate_signature(definition)
    is_factor = definition["kind"] == "factor_rank"
    pl.DataFrame({
        "signature": [signature],
        "name": ["因子组合候选" if is_factor else "已有策略 · ma_golden_cross"],
        "kind": ["factor_combination" if is_factor else "existing_strategy"],
        "factor_names_json": [json.dumps(definition.get("factor_names", []))],
        "strategy_id": [definition.get("strategy_id")],
        "definition_json": [json.dumps(definition, sort_keys=True)],
        "regime_state": ["overall"],
        "score": [metrics["oos_sharpe"]],
        "oos_return": [metrics["oos_return"]],
        "oos_sharpe": [metrics["oos_sharpe"]],
        "oos_max_drawdown": [metrics["oos_max_drawdown"]],
        "oos_positive_fold_ratio": [metrics["oos_positive_fold_ratio"]],
        "oos_n_trades": [metrics["oos_n_trades"]],
        "confidence": [metrics["confidence"]],
        "valid_folds": [metrics["valid_folds"]],
        "skipped_folds": [0],
        "promoted_candidate_id": [None],
        "published_strategy_id": [None],
    }, schema_overrides={
        "strategy_id": pl.String,
        "promoted_candidate_id": pl.String,
        "published_strategy_id": pl.String,
    }).write_parquet(store.artifact_path(run_id, "candidates"))
    store.register_artifact(run_id, "candidates")
    store.write_summary(run_id, {
        "data_as_of": data_as_of,
        "algorithm_version": algorithm_version,
        "methodology_version": "factor_v2",
    })
    if status != "queued":
        store.transition_status(run_id, "running")
        store.transition_status(run_id, status)
    return signature


class _Rerunner:
    """Stand in for the job manager: each call creates one rerun with fixed metrics."""

    def __init__(self, store: MiningRunStore, metrics: dict[str, Any], *, status: str = "succeeded"):
        self.store = store
        self.metrics = metrics
        self.status = status
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(dict(request))
        run_id = f"rerun-{len(self.requests)}"
        definition = (
            _existing_definition()
            if request.get("_definition") == "existing"
            else _factor_definition()
        )
        _write_run(
            self.store,
            run_id,
            definition,
            self.metrics,
            algorithm_version=MINING_ALGORITHM_VERSION,
            status=self.status,
        )
        return self.store.get(run_id)


def _published_setup(
    tmp_path: Path,
    definition: dict | None = None,
    *,
    request: dict[str, Any] | None = None,
    data_as_of: str = "2026-01-09",
):
    store = MiningRunStore(tmp_path)
    builtin_dir = Path(__file__).resolve().parents[1] / "app" / "strategy" / "builtin"
    custom_dir = tmp_path / "strategies" / "custom"
    engine = StrategyEngine(strategy_dirs=[builtin_dir, custom_dir])
    invalidations: list[object] = []
    service = MiningCandidateService(
        tmp_path,
        store,
        CandidateStore(tmp_path),
        engine,
        strategy_cache_invalidator=lambda data_dir: invalidations.append(data_dir),
        monitor_state_invalidator=lambda: invalidations.append("monitor"),
    )
    signature = _write_run(
        store,
        "origin-run",
        definition or _factor_definition(),
        _PASSING,
        algorithm_version=MINING_ALGORITHM_VERSION,
        request=request,
        data_as_of=data_as_of,
    )
    published = service.publish("origin-run", signature)
    store.write_summary("origin-run", {
        "data_as_of": data_as_of,
        "algorithm_version": _OUTDATED_VERSION,
        "methodology_version": "factor_v2",
    })
    invalidations.clear()
    return store, engine, service, signature, published["strategy_id"], invalidations


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _assert_nothing_lost(before: dict[str, bytes], after: dict[str, bytes]) -> None:
    surviving = set(after.values())
    lost = [path for path, content in before.items() if content not in surviving]
    assert lost == []


def _origin_row(store: MiningRunStore) -> dict[str, Any]:
    return pl.read_parquet(store.artifact_path("origin-run", "candidates")).row(0, named=True)


def test_failing_factor_strategy_is_backed_up_moved_and_unpublished_once(
    tmp_path, caplog
) -> None:
    store, engine, service, signature, strategy_id, invalidations = _published_setup(tmp_path)
    strategy_path = tmp_path / "strategies" / "custom" / f"{strategy_id}.py"
    source = strategy_path.read_bytes()
    before = _snapshot(tmp_path)
    rerunner = _Rerunner(store, _FAILING)

    with caplog.at_level(logging.WARNING, logger="app.services.mining_reevaluation"):
        report = PublishedStrategyReevaluator(service, rerunner).run()

    assert report["algorithm_version"] == MINING_ALGORITHM_VERSION
    [item] = report["items"]
    assert item["strategy_id"] == strategy_id
    assert item["status"] == "delisted"
    assert item["rerun_id"] == "rerun-1"
    assert any("valid outer folds" in reason for reason in item["reasons"])
    assert any("positive-return fold ratio" in reason for reason in item["reasons"])
    assert rerunner.requests[0]["end"] == "2026-01-09"
    assert not strategy_path.exists()
    assert not engine.has(strategy_id)
    quarantine = tmp_path / item["quarantine"]
    assert quarantine.is_relative_to(tmp_path / "research" / "mining" / "reevaluation")
    assert (quarantine / strategy_path.name).read_bytes() == source
    backup = pl.read_parquet(quarantine / "candidates.parquet").row(0, named=True)
    assert backup["published_strategy_id"] == strategy_id
    record = json.loads((quarantine / "delisting.json").read_text(encoding="utf-8"))
    assert record["strategy_id"] == strategy_id
    assert record["origin_run_id"] == "origin-run"
    assert record["candidate_signature"] == signature
    assert _origin_row(store)["published_strategy_id"] is None
    assert invalidations == [tmp_path, "monitor"]
    assert any(strategy_id in message for message in caplog.messages)
    _assert_nothing_lost(before, _snapshot(tmp_path))

    with pytest.raises(ValueError, match="algorithm"):
        service.publish("origin-run", signature)

    after_first = _snapshot(tmp_path)
    second = PublishedStrategyReevaluator(service, rerunner).run()

    assert len(rerunner.requests) == 1
    assert [entry["status"] for entry in second["items"]] == ["delisted"]
    assert second["items"][0]["quarantine"] == item["quarantine"]
    assert _snapshot(tmp_path) == after_first
    assert invalidations == [tmp_path, "monitor"]


def test_passing_factor_strategy_stays_published_and_is_not_rerun_again(tmp_path) -> None:
    store, engine, service, _signature, strategy_id, invalidations = _published_setup(tmp_path)
    strategy_path = tmp_path / "strategies" / "custom" / f"{strategy_id}.py"
    rerunner = _Rerunner(store, _PASSING)

    first = PublishedStrategyReevaluator(service, rerunner).run()
    second = PublishedStrategyReevaluator(service, rerunner).run()

    assert [item["status"] for item in first["items"]] == ["passed"]
    assert [item["status"] for item in second["items"]] == ["passed"]
    assert len(rerunner.requests) == 1
    assert strategy_path.is_file()
    assert engine.has(strategy_id)
    assert _origin_row(store)["published_strategy_id"] == strategy_id
    assert invalidations == []
    assert not (tmp_path / "research" / "mining" / "reevaluation" / "delisted").exists()


def test_pending_rerun_is_reused_until_it_finishes(tmp_path) -> None:
    store, _engine, service, _signature, strategy_id, _ = _published_setup(tmp_path)
    rerunner = _Rerunner(store, _FAILING, status="queued")
    strategy_path = tmp_path / "strategies" / "custom" / f"{strategy_id}.py"

    first = PublishedStrategyReevaluator(service, rerunner).run()
    second = PublishedStrategyReevaluator(service, rerunner).run()

    assert [item["status"] for item in first["items"]] == ["pending"]
    assert [item["status"] for item in second["items"]] == ["pending"]
    assert len(rerunner.requests) == 1
    assert strategy_path.is_file()

    store.transition_status("rerun-1", "running")
    store.transition_status("rerun-1", "succeeded")
    third = PublishedStrategyReevaluator(service, rerunner).run()

    assert [item["status"] for item in third["items"]] == ["delisted"]
    assert len(rerunner.requests) == 1
    assert not strategy_path.exists()


def test_candidate_missing_from_the_fixed_rerun_is_delisted(tmp_path) -> None:
    store, _engine, service, _signature, strategy_id, _ = _published_setup(tmp_path)

    def rerun_without_candidate(request: dict[str, Any]) -> dict[str, Any]:
        _write_run(
            store,
            "rerun-other",
            {**_factor_definition(), "scoring": {"turnover_rate": 3.0, "rsi_14": 1.0}},
            _PASSING,
            algorithm_version=MINING_ALGORITHM_VERSION,
        )
        return store.get("rerun-other")

    report = PublishedStrategyReevaluator(service, rerun_without_candidate).run()

    [item] = report["items"]
    assert item["status"] == "delisted"
    assert any("not selected" in reason for reason in item["reasons"])
    assert not (tmp_path / "strategies" / "custom" / f"{strategy_id}.py").exists()


def test_existing_strategy_publication_only_loses_its_backlink(tmp_path) -> None:
    store, engine, service, _signature, strategy_id, invalidations = _published_setup(
        tmp_path, _existing_definition()
    )
    builtin_file = engine.get(strategy_id).file_path
    builtin_source = builtin_file.read_bytes()

    def rerun(request: dict[str, Any]) -> dict[str, Any]:
        return _Rerunner(store, _FAILING)({**request, "_definition": "existing"})

    report = PublishedStrategyReevaluator(service, rerun).run()

    [item] = report["items"]
    assert item["status"] == "delisted"
    assert builtin_file.read_bytes() == builtin_source
    assert engine.has(strategy_id)
    assert _origin_row(store)["published_strategy_id"] is None
    quarantine = tmp_path / item["quarantine"]
    assert (quarantine / "candidates.parquet").is_file()
    assert not list(quarantine.glob("*.py"))
    assert invalidations == []


def test_current_version_publications_are_not_reevaluated(tmp_path) -> None:
    store, _engine, service, _signature, _strategy_id, _ = _published_setup(tmp_path)
    store.write_summary("origin-run", {
        "data_as_of": "2026-01-09",
        "algorithm_version": MINING_ALGORITHM_VERSION,
        "methodology_version": "factor_v2",
    })
    rerunner = _Rerunner(store, _FAILING)

    report = PublishedStrategyReevaluator(service, rerunner).run()

    assert report["items"] == []
    assert rerunner.requests == []


class _ApiRepo:
    def __init__(self, data_dir: Path) -> None:
        self.store = SimpleNamespace(data_dir=data_dir)

    @staticmethod
    def get_matrix_data_generation(asset_type="stock"):
        return f"generation-{asset_type}"

    @staticmethod
    def latest_enriched_date(asset_type="stock"):
        del asset_type
        return None

    @staticmethod
    def get_instruments_asset(asset_type):
        del asset_type
        return pl.DataFrame({"symbol": ["2330.TWSE"], "name": ["台積電"]})


class _ApiManager:
    def __init__(self, store: MiningRunStore) -> None:
        self.store = store
        self.started: list[tuple[dict[str, Any], str]] = []

    def start(self, request, fingerprint, force=False, source="manual", run_id=None):
        del force
        self.started.append((dict(request), source))
        manifest = self.store.create(request, fingerprint, run_id=run_id)
        self.store.append_event(
            manifest["run_id"], "queued", {"status": "queued", "source": source}
        )
        return manifest


def test_reevaluate_endpoint_reruns_once_then_delists_the_failed_publication(tmp_path) -> None:
    first_day = date(2025, 1, 1)
    enriched = [first_day + timedelta(days=index) for index in range(219)]
    for value in enriched:
        partition = tmp_path / "kline_daily_enriched" / f"date={value.isoformat()}"
        partition.mkdir(parents=True, exist_ok=True)
        (partition / "part.parquet").touch()
    origin_request = {
        "factor_names": ["turnover_rate", "rsi_14"],
        "strategy_ids": [],
        "asset_type": "stock",
        "budget_profile": "exploratory",
        "correlation_threshold": 0.75,
    }
    store, engine, service, _signature, strategy_id, _ = _published_setup(
        tmp_path, request=origin_request, data_as_of=enriched[-1].isoformat()
    )
    app = FastAPI()
    app.include_router(router)
    app.state.repo = _ApiRepo(tmp_path)
    app.state.mining_manager = _ApiManager(store)
    app.state.strategy_engine = engine
    app.state.mining_candidate_service = service
    client = TestClient(app)

    pending = client.post("/api/backtest/mining/published/reevaluate")

    assert pending.status_code == 200, pending.text
    [item] = pending.json()["items"]
    assert item["status"] == "pending"
    [(rerun_request, source)] = app.state.mining_manager.started
    assert source == "reevaluation"
    assert rerun_request["end"] == enriched[-1].isoformat()
    assert rerun_request["factor_names"] == origin_request["factor_names"]

    rerun_id = item["rerun_id"]
    store.transition_status(rerun_id, "running")
    _complete_run(
        store,
        rerun_id,
        _factor_definition(),
        _FAILING,
        algorithm_version=MINING_ALGORITHM_VERSION,
        status="succeeded",
        data_as_of=enriched[-1].isoformat(),
    )
    delisted = client.post("/api/backtest/mining/published/reevaluate")
    repeated = client.post("/api/backtest/mining/published/reevaluate")

    assert [entry["status"] for entry in delisted.json()["items"]] == ["delisted"]
    assert repeated.json()["items"] == delisted.json()["items"]
    assert len(app.state.mining_manager.started) == 1
    assert not (tmp_path / "strategies" / "custom" / f"{strategy_id}.py").exists()
    assert not engine.has(strategy_id)
    assert _origin_row(store)["published_strategy_id"] is None


def test_reevaluate_endpoint_is_local_only_when_user_strategies_are_enabled(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(app_config.settings, "allow_user_python_strategies", True)
    app = FastAPI()
    app.include_router(router)
    app.state.mining_manager = _ApiManager(MiningRunStore(tmp_path))
    client = TestClient(app, base_url="http://127.0.0.1:3018", client=("192.168.1.20", 50000))

    response = client.post("/api/backtest/mining/published/reevaluate")

    assert response.status_code == 403
