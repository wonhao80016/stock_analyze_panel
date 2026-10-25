"""A strategy file that fails to load never blocks reloading the others."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

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
