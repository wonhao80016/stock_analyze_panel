from __future__ import annotations

import json
import os
import threading
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polars as pl
import pytest

import app.services.mining_jobs as mining_jobs_module
import app.services.mining_manager as mining_manager_module
from app.backtest.candidates import CandidateStore
from app.backtest.mining import compute_candidate_signature
from app.backtest.mining_runtime import MatcherCandidateEvaluator
from app.services.heavy_job_limiter import HeavyJobLimiter
from app.services.mining_candidates import MiningCandidateService
from app.services.mining_jobs import TERMINAL_RUN_STATUSES, MiningRunStore
from app.services.mining_manager import MiningJobManager
from app.services.mining_schedule import MINING_ALGORITHM_VERSION

_COST_FIELDS = ("commission_pct", "minimum_commission", "stamp_tax_pct", "slippage_bps", "fees_pct")


def _factor_definition() -> dict:
    return {
        "kind": "factor_rank",
        "factor_names": ["turnover_rate", "rsi_14"],
        "scoring": {"turnover_rate": 1.0, "rsi_14": 2.0},
        "directions": {"turnover_rate": "high", "rsi_14": "low"},
    }


def _create_run(tmp_path: Path, request_overrides: dict[str, Any]) -> tuple[MiningRunStore, str, str]:
    definition = _factor_definition()
    signature = compute_candidate_signature(definition)
    store = MiningRunStore(tmp_path)
    request = {
        "factor_names": ["turnover_rate", "rsi_14"],
        "strategy_ids": [],
        "asset_type": "stock",
        "start": "2025-01-01",
        "end": "2026-01-09",
        "budget_profile": "balanced",
        **request_overrides,
    }
    run_id = "cost-run"
    store.create(request, {"generation": "test"}, run_id=run_id)
    pl.DataFrame({
        "signature": [signature],
        "name": ["因子组合候选"],
        "kind": ["factor_combination"],
        "factor_names_json": [json.dumps(definition["factor_names"])],
        "strategy_id": [None],
        "definition_json": [json.dumps(definition, sort_keys=True)],
        "regime_state": ["overall"],
        "score": [0.9],
        "oos_return": [0.08],
        "oos_sharpe": [0.9],
        "oos_max_drawdown": [-0.12],
        "oos_positive_fold_ratio": [1.0],
        "oos_n_trades": [70],
        "confidence": ["standard"],
        "valid_folds": [3],
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
        "data_as_of": "2026-01-09",
        "algorithm_version": MINING_ALGORITHM_VERSION,
        "methodology_version": "factor_v2",
    })
    store.transition_status(run_id, "running")
    store.transition_status(run_id, "succeeded")
    return store, run_id, signature


def _runtime_cost_fields(request: dict[str, Any]) -> dict[str, Any]:
    runtime_request = SimpleNamespace(
        symbols=None,
        asset_type="stock",
        forward_horizon=5,
        commission_pct=request.get("commission_pct"),
        slippage_bps=request.get("slippage_bps", 5.0),
    )
    evaluator = MatcherCandidateEvaluator(
        service=None,
        strategy_engine=None,
        data_dir=Path("."),
        request=runtime_request,
        base_market=None,
        cancel_check=None,
    )
    config = evaluator._backtest_config(
        _factor_definition(),
        date(2025, 1, 2),
        date(2025, 2, 3),
        "overall",
    )
    return {field: getattr(config, field) for field in _COST_FIELDS}


@pytest.mark.parametrize(
    "request_overrides",
    [{}, {"commission_pct": 0.001425, "slippage_bps": 8.0}],
)
def test_promotion_costs_match_the_taiwan_costs_mining_evaluated(
    tmp_path, request_overrides
) -> None:
    store, run_id, signature = _create_run(tmp_path, request_overrides)
    service = MiningCandidateService(
        tmp_path,
        store,
        CandidateStore(tmp_path),
        SimpleNamespace(),
        strategy_cache_invalidator=lambda _data_dir: None,
    )

    promoted = service.promote(run_id, signature)

    config = promoted["config"]
    assert config["stamp_tax_pct"] is None
    assert config["commission_pct"] == request_overrides.get("commission_pct")
    assert {field: config.get(field) for field in _COST_FIELDS} == _runtime_cost_fields(
        request_overrides
    )


def test_manifest_replace_retries_while_a_reader_holds_the_file(tmp_path, monkeypatch) -> None:
    store = MiningRunStore(tmp_path)
    store.create({"factor_names": ["rsi_14"]}, {"generation": "g"}, run_id="busy-run")
    real_replace = os.replace
    blocked: list[str] = []

    def replace_blocked_twice(source, target, *args, **kwargs):
        if Path(target).name == "manifest.json" and len(blocked) < 2:
            blocked.append(str(target))
            raise PermissionError(13, "Access is denied", str(target))
        return real_replace(source, target, *args, **kwargs)

    monkeypatch.setattr(os, "replace", replace_blocked_twice)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    manifest = store.transition_status("busy-run", "running")

    assert len(blocked) == 2
    assert manifest["status"] == "running"
    assert store.get("busy-run")["status"] == "running"
    assert not list((tmp_path / "research" / "mining" / "runs" / "busy-run").glob(".*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows rejects replacing a file another handle holds open")
def test_manifest_write_survives_a_concurrent_open_reader_on_windows(tmp_path) -> None:
    store = MiningRunStore(tmp_path)
    store.create({"factor_names": ["rsi_14"]}, {"generation": "g"}, run_id="open-run")
    manifest_path = tmp_path / "research" / "mining" / "runs" / "open-run" / "manifest.json"
    reader = manifest_path.open("rb")
    timer = threading.Timer(0.15, reader.close)
    timer.start()
    try:
        manifest = store.transition_status("open-run", "running")
    finally:
        timer.join()
        reader.close()

    assert manifest["status"] == "running"
    assert store.get("open-run")["status"] == "running"


def _task_factory(kind: str, data_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return {"kind": kind, "data_dir": str(data_dir), "payload": payload}


@pytest.fixture
def isolated_limiter(monkeypatch: pytest.MonkeyPatch) -> HeavyJobLimiter:
    limiter = HeavyJobLimiter(capacity=2, cancel_poll_interval=0.005)
    monkeypatch.setattr(mining_manager_module, "shared_heavy_job_limiter", limiter)
    return limiter


def _watch_terminal_writes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
    """Record, at the moment a terminal manifest lands, whether its event is already readable."""
    observations: list[tuple[str, bool]] = []
    original = mining_jobs_module._atomic_write_json

    def observe(path: Path, value: Any) -> None:
        original(path, value)
        if path.name != "manifest.json" or value.get("status") not in TERMINAL_RUN_STATUSES:
            return
        lines = (path.parent / "events.jsonl").read_text(encoding="utf-8").splitlines()
        statuses = [json.loads(line).get("payload", {}).get("status") for line in lines if line]
        observations.append((value["status"], value["status"] in statuses))

    monkeypatch.setattr(mining_jobs_module, "_atomic_write_json", observe)
    return observations


def _wait_terminal(manager: MiningJobManager, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        manifest = manager.store.get(run_id)
        if manifest["status"] in TERMINAL_RUN_STATUSES:
            return manifest
        time.sleep(0.005)
    pytest.fail(f"run {run_id} did not finish")


@pytest.mark.parametrize(
    ("runner_result", "expected"),
    [
        ({"status": "succeeded"}, "succeeded"),
        ({"status": "succeeded_with_budget_exhausted"}, "succeeded_with_budget_exhausted"),
        (RuntimeError("worker crashed"), "failed"),
    ],
)
def test_terminal_event_is_readable_before_the_terminal_status(
    tmp_path, monkeypatch, isolated_limiter, runner_result, expected
) -> None:
    def runner(_task, _progress, _cancel_event):
        if isinstance(runner_result, Exception):
            raise runner_result
        return dict(runner_result)

    manager = MiningJobManager(tmp_path, worker_runner=runner, task_factory=_task_factory)
    observations = _watch_terminal_writes(monkeypatch)
    try:
        manifest = manager.start({"factor_names": ["rsi_14"]}, {"generation": "g"})
        finished = _wait_terminal(manager, manifest["run_id"])
    finally:
        manager.shutdown()

    assert finished["status"] == expected
    assert observations == [(expected, True)]


def test_cancelling_a_queued_run_records_its_event_before_the_status(
    tmp_path, monkeypatch, isolated_limiter
) -> None:
    manager = MiningJobManager(
        tmp_path,
        worker_runner=lambda *_args: {"status": "succeeded"},
        task_factory=_task_factory,
    )
    manager.store.create({"factor_names": ["rsi_14"]}, {"generation": "g"}, run_id="queued-run")
    observations = _watch_terminal_writes(monkeypatch)
    try:
        cancelled = manager.cancel("queued-run")
    finally:
        manager.shutdown()

    assert cancelled["status"] == "cancelled"
    assert observations == [("cancelled", True)]
