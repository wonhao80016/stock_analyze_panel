"""A strategy file that fails to load never blocks reloading, saving or deleting the others."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.strategy import (
    CompositeChildItem,
    StrategyCodeSaveRequest,
    StrategyCompositeSaveRequest,
    _save_composite_strategy,
    _save_strategy_code,
    delete_strategy,
    reload_strategies,
)
from app.strategy.engine import StrategyEngine

pytestmark = pytest.mark.usefixtures("deny_network")

ASCENDING_ERROR = "ranks lower scores first (descending=False)"


def _custom(sid: str, *, descending: bool = True) -> str:
    return f'''import polars as pl
META = {{"id": "{sid}", "name": "{sid}", "asset_types": ["stock"], "timeframes": ["1d"],
        "scoring": {{"amount": 1.0}}, "descending": {descending}}}
EXECUTION_BACKEND = "polars_expr"
def filter(df, params):
    return pl.lit(True)
'''


def _composite(sid: str, children: list[str]) -> str:
    refs = [{"strategy_id": child, "weight": 1.0} for child in children]
    return f'''META = {{"id": "{sid}", "name": "{sid}", "asset_types": ["stock"], "timeframes": ["1d"],
        "children": {refs!r}}}
EXECUTION_BACKEND = "composite"
'''


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "strategies"
    dirs = {source: root / source for source in ("custom", "ai", "composite")}
    for directory in dirs.values():
        directory.mkdir(parents=True)
    (dirs["custom"] / "custom_high.py").write_text(_custom("custom_high"), encoding="utf-8")
    (dirs["custom"] / "custom_low.py").write_text(
        _custom("custom_low", descending=False), encoding="utf-8"
    )
    (dirs["composite"] / "composite_low.py").write_text(
        _composite("composite_low", ["custom_high", "custom_low"]), encoding="utf-8"
    )
    engine = StrategyEngine(strategy_dirs=list(dirs.values()))
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repo=repo, strategy_engine=engine))
    )
    return SimpleNamespace(engine=engine, request=request, dirs=dirs, data_dir=tmp_path)


def _errors_by_file(engine: StrategyEngine) -> dict[str, str]:
    return {item["file"]: item["error"] for item in engine.load_errors()}


def _broken_file(ws) -> str:
    path = ws.dirs["custom"] / "custom_broken.py"
    path.write_text("this is not valid python", encoding="utf-8")
    return str(path)


def test_the_ascending_child_composite_is_reported_and_the_rest_loads(workspace):
    engine = workspace.engine

    assert engine.has("custom_high") and engine.has("custom_low")
    assert not engine.has("composite_low")
    [message] = _errors_by_file(engine).values()
    assert ASCENDING_ERROR in message


@pytest.mark.parametrize("cause", ["ascending_child", "syntax_error"])
def test_reload_registers_new_strategies_beside_an_unloadable_file(workspace, cause):
    engine = workspace.engine
    broken = (
        str(workspace.dirs["composite"] / "composite_low.py")
        if cause == "ascending_child"
        else _broken_file(workspace)
    )
    (workspace.dirs["custom"] / "custom_new.py").write_text(_custom("custom_new"), encoding="utf-8")

    engine.reload()

    assert engine.has("custom_new") and engine.has("custom_high")
    assert broken in _errors_by_file(engine)


def test_the_reload_endpoint_succeeds_and_reports_load_errors(workspace):
    _broken_file(workspace)

    response = reload_strategies(workspace.request)

    assert response["ok"] is True
    assert response["count"] == 2
    assert {item["file"] for item in response["load_errors"]} == set(
        _errors_by_file(workspace.engine)
    )


@pytest.mark.parametrize("cause", ["ascending_child", "syntax_error"])
def test_saving_another_strategy_is_not_blocked(workspace, cause):
    if cause == "syntax_error":
        _broken_file(workspace)
        workspace.engine.reload()

    saved = _save_strategy_code(
        StrategyCodeSaveRequest(code=_custom("custom_saved"), strategy_id="custom_saved"),
        workspace.request,
    )
    composite = _save_composite_strategy(
        StrategyCompositeSaveRequest(
            strategy_id="composite_ok",
            children=[CompositeChildItem(strategy_id="custom_high")],
        ),
        workspace.request,
    )

    assert saved["ok"] and composite["ok"]
    assert workspace.engine.has("custom_saved") and workspace.engine.has("composite_ok")


def test_a_save_whose_own_file_fails_to_load_is_rolled_back_with_its_error(workspace):
    with pytest.raises(ValueError, match="custom_bad") as raised:
        _save_strategy_code(
            StrategyCodeSaveRequest(
                code=_custom("custom_bad") + "\n_LOAD_FAILS = 1 / 0\n",
                strategy_id="custom_bad",
            ),
            workspace.request,
        )

    assert "division by zero" in str(raised.value)
    assert not (workspace.dirs["custom"] / "custom_bad.py").exists()
    assert not workspace.engine.has("custom_bad")


def test_a_save_that_makes_a_composite_unloadable_is_rejected(workspace):
    _save_composite_strategy(
        StrategyCompositeSaveRequest(
            strategy_id="composite_ok",
            children=[CompositeChildItem(strategy_id="custom_high")],
        ),
        workspace.request,
    )
    original = (workspace.dirs["custom"] / "custom_high.py").read_text(encoding="utf-8")

    with pytest.raises(ValueError, match=ASCENDING_ERROR.replace("(", r"\(").replace(")", r"\)")):
        _save_strategy_code(
            StrategyCodeSaveRequest(
                code=_custom("custom_high", descending=False),
                strategy_id="custom_high",
                mode="update",
            ),
            workspace.request,
        )

    assert (workspace.dirs["custom"] / "custom_high.py").read_text(encoding="utf-8") == original
    assert workspace.engine.has("composite_ok")


@pytest.mark.parametrize("cause", ["ascending_child", "syntax_error"])
def test_delete_removes_a_strategy_file_that_failed_to_load(workspace, cause):
    if cause == "ascending_child":
        sid, path = "composite_low", workspace.dirs["composite"] / "composite_low.py"
    else:
        sid, path = "custom_broken", workspace.dirs["custom"] / "custom_broken.py"
        _broken_file(workspace)
        workspace.engine.reload()
    override = workspace.data_dir / "user_data" / "strategy_overrides" / f"{sid}.json"
    override.parent.mkdir(parents=True)
    override.write_text("{}", encoding="utf-8")

    result = delete_strategy(sid, workspace.request)

    assert result["ok"] is True
    assert not path.exists()
    assert not override.exists()
    assert str(path) not in _errors_by_file(workspace.engine)
    assert workspace.engine.has("custom_high")


def test_delete_of_an_unknown_id_is_still_404(workspace):
    _broken_file(workspace)
    workspace.engine.reload()

    with pytest.raises(HTTPException) as raised:
        delete_strategy("custom_missing", workspace.request)

    assert raised.value.status_code == 404
    assert (workspace.dirs["custom"] / "custom_broken.py").exists()


def test_delete_never_removes_an_unloadable_file_outside_the_user_directories(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "custom_stray.py").write_text("this is not valid python", encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[outside])
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repo=repo, strategy_engine=engine))
    )

    with pytest.raises(HTTPException) as raised:
        delete_strategy("custom_stray", request)

    assert raised.value.status_code == 404
    assert (outside / "custom_stray.py").exists()
