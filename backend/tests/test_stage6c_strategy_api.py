"""Strategy API: overrides invalidate runtime state; creating never overwrites a strategy file."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import strategy as strategy_api
from app.config import settings
from app.services import strategy_cache
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyEngine


def _code(strategy_id: str, name: str = "測試策略", meta_name: str = "META") -> str:
    return f'''"""測試策略"""
import polars as pl

{meta_name} = {{
    "id": "{strategy_id}",
    "name": "{name}",
    "description": "測試描述",
    "params": [],
    "scoring": {{}},
}}

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    return pl.lit(True)
'''


class _Monitor:
    def __init__(self) -> None:
        self.invalidations = 0

    def invalidate_strategy_state(self) -> None:
        self.invalidations += 1


def _request(tmp_path):
    dirs = [tmp_path / "strategies" / s for s in ("custom", "ai")]
    engine = StrategyEngine(strategy_dirs=dirs)
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    state = SimpleNamespace(repo=repo, strategy_engine=engine, monitor_engine=_Monitor())
    client = SimpleNamespace(host="127.0.0.1")
    return SimpleNamespace(app=SimpleNamespace(state=state), client=client, headers={})


def _save(request, sid: str, *, source: str = "custom", mode: str = "create", name: str = "測試策略"):
    return strategy_api._save_strategy_code(
        strategy_api.StrategyCodeSaveRequest(
            strategy_id=sid, target_source=source, mode=mode, code=_code(sid, name)
        ),
        request,
    )


# ── B095 ────────────────────────────────────────────────


@pytest.fixture
def saved_strategy(tmp_path):
    request = _request(tmp_path)
    _save(request, "custom_params")
    calls: list = []
    request.app.state.monitor_engine.invalidations = 0
    return request, calls


@pytest.mark.parametrize("action", ["save", "patch", "reset"])
def test_override_writes_invalidate_strategy_results_and_monitor_state(saved_strategy, monkeypatch, tmp_path, action):
    request, calls = saved_strategy
    monkeypatch.setattr(strategy_cache, "clear_cache", lambda data_dir: calls.append(data_dir))
    strategy_config.save_override(tmp_path, "custom_params", {"params": {"x": 1}})
    req = strategy_api.SaveConfigRequest(strategy_id="custom_params", overrides={"stop_loss": -0.03})

    if action == "save":
        strategy_api.save_config(req, request)
    elif action == "patch":
        strategy_api.patch_config(req, request)
    else:
        strategy_api.reset_config("custom_params", request)

    assert calls == [tmp_path]
    assert request.app.state.monitor_engine.invalidations == 1


# ── B096 ────────────────────────────────────────────────


def test_create_with_a_registered_id_is_a_conflict(tmp_path):
    request = _request(tmp_path)
    _save(request, "custom_taken", name="原本")

    with pytest.raises(strategy_api.StrategyIdConflictError):
        _save(request, "custom_taken", name="覆蓋")

    text = (tmp_path / "strategies" / "custom" / "custom_taken.py").read_text(encoding="utf-8")
    assert '"name": "原本"' in text


def test_create_never_overwrites_a_file_that_failed_to_load(tmp_path):
    request = _request(tmp_path)
    broken = tmp_path / "strategies" / "custom" / "custom_broken.py"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("META = {'id': 'custom_broken'}\nthis is not python\n", encoding="utf-8")
    request.app.state.strategy_engine.reload()

    with pytest.raises(strategy_api.StrategyIdConflictError):
        _save(request, "custom_broken")

    assert "this is not python" in broken.read_text(encoding="utf-8")


def test_code_save_endpoint_answers_a_conflict_with_409(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allow_user_python_strategies", True)
    monkeypatch.setattr(strategy_api, "_is_local_request", lambda request: True)
    request = _request(tmp_path)
    _save(request, "custom_taken")

    with pytest.raises(HTTPException) as exc:
        strategy_api.save_strategy_code(
            strategy_api.StrategyCodeSaveRequest(strategy_id="custom_taken", code=_code("custom_taken")),
            request,
        )

    assert exc.value.status_code == 409


def test_legacy_ai_save_does_not_overwrite_an_existing_ai_strategy(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "allow_user_python_strategies", True)
    monkeypatch.setattr(strategy_api, "_is_local_request", lambda request: True)
    request = _request(tmp_path)
    _save(request, "ai_taken", source="ai", name="原本")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(strategy_api.ai_save(
            strategy_api.AISaveRequest(code=_code("ai_taken", "覆蓋"), strategy_id="ai_taken"),
            request,
        ))

    assert exc.value.status_code == 409
    text = (tmp_path / "strategies" / "ai" / "ai_taken.py").read_text(encoding="utf-8")
    assert '"name": "原本"' in text


def test_update_mode_still_rewrites_the_file(tmp_path):
    request = _request(tmp_path)
    _save(request, "custom_edit", name="原本")

    _save(request, "custom_edit", mode="update", name="更新")

    text = (tmp_path / "strategies" / "custom" / "custom_edit.py").read_text(encoding="utf-8")
    assert '"name": "更新"' in text


# ── B168 (save side) ────────────────────────────────────


def test_saving_a_meta_alias_renames_it_to_meta(tmp_path):
    prepared = strategy_api._prepare_strategy_code(strategy_api.StrategyCodeValidateRequest(
        strategy_id="custom_alias", code=_code("custom_alias", meta_name="STRATEGY_META"),
    ))

    assert "\nMETA = {" in prepared["code"]
    assert "STRATEGY_META" not in prepared["code"]
