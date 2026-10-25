"""User Python strategies stay off unless ALLOW_USER_PYTHON_STRATEGIES enables them.

Strategy files run in the backend process and the AST check can be bypassed, so by
default no file under data/strategies runs.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app import config as app_config
from app.api import strategy as strategy_api
from app.strategy.engine import StrategyEngine

BUILTIN_DIR = Path(strategy_api.__file__).resolve().parents[1] / "strategy" / "builtin"
SWITCH = "ALLOW_USER_PYTHON_STRATEGIES"


def _user_dirs(data_dir: Path) -> list[Path]:
    return [data_dir / "strategies" / source for source in ("custom", "ai", "composite")]


def _strategy_code(strategy_id: str, prelude: str = "") -> str:
    return f'''import polars as pl
{prelude}
META = {{
    "id": "{strategy_id}",
    "name": "{strategy_id}",
    "description": "gate test",
    "asset_types": ["stock"],
    "params": [],
    "scoring": {{}},
}}


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    return pl.lit(True)
'''


def _marker_strategy(strategy_id: str, marker: Path) -> str:
    # polars I/O passes the AST check, so the marker proves the module body ran.
    prelude = f'pl.DataFrame({{"ran": [1]}}).write_csv({marker.as_posix()!r})\n'
    return _strategy_code(strategy_id, prelude)


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_config.settings, "allow_user_python_strategies", True)


def test_settings_default_disables_user_python_strategies(monkeypatch) -> None:
    monkeypatch.delenv(SWITCH, raising=False)

    assert app_config.Settings(_env_file=None).allow_user_python_strategies is False


def test_user_strategy_files_do_not_run_by_default(tmp_path, caplog) -> None:
    custom_dir = tmp_path / "strategies" / "custom"
    custom_dir.mkdir(parents=True)
    marker = tmp_path / "marker.txt"
    strategy_file = custom_dir / "custom_marker.py"
    strategy_file.write_text(_marker_strategy("custom_marker", marker), encoding="utf-8")
    user_dirs = _user_dirs(tmp_path)

    with caplog.at_level(logging.WARNING, logger="app.strategy.engine"):
        engine = StrategyEngine(strategy_dirs=[BUILTIN_DIR, *user_dirs], disabled_dirs=user_dirs)
        engine.reload()

    assert not marker.exists()
    assert not engine.has("custom_marker")
    errors = [item for item in engine.load_errors() if item["file"] == str(strategy_file)]
    assert len(errors) == 1
    assert SWITCH in errors[0]["error"]
    assert any("custom_marker.py" in r.getMessage() and SWITCH in r.getMessage() for r in caplog.records)
    assert engine.file_disabled(custom_dir / "custom_other.py")
    assert not engine.file_disabled(BUILTIN_DIR / "any.py")


def test_builtin_strategies_load_while_user_strategies_are_disabled(tmp_path) -> None:
    custom_dir = tmp_path / "strategies" / "custom"
    custom_dir.mkdir(parents=True)
    (custom_dir / "custom_marker.py").write_text(
        _marker_strategy("custom_marker", tmp_path / "marker.txt"), encoding="utf-8"
    )
    user_dirs = _user_dirs(tmp_path)

    builtin_only = StrategyEngine(strategy_dirs=[BUILTIN_DIR])
    gated = StrategyEngine(strategy_dirs=[BUILTIN_DIR, *user_dirs], disabled_dirs=user_dirs)

    builtin_ids = {s.meta["id"] for s in builtin_only.strategy_definitions()}
    assert builtin_ids
    assert {s.meta["id"] for s in gated.strategy_definitions()} == builtin_ids
    assert all(s.source == "builtin" for s in gated.strategy_definitions())
    assert [Path(item["file"]).name for item in gated.load_errors()] == ["custom_marker.py"]


def test_enabled_engine_runs_user_strategy_files(tmp_path, enabled) -> None:
    custom_dir = tmp_path / "strategies" / "custom"
    custom_dir.mkdir(parents=True)
    marker = tmp_path / "marker.txt"
    (custom_dir / "custom_marker.py").write_text(
        _marker_strategy("custom_marker", marker), encoding="utf-8"
    )

    engine = StrategyEngine(strategy_dirs=_user_dirs(tmp_path), disabled_dirs=())

    assert marker.exists()
    assert engine.get("custom_marker").source == "custom"


@pytest.mark.parametrize("allowed", [False, True])
def test_main_wires_the_switch_into_the_strategy_engine(tmp_path, monkeypatch, allowed) -> None:
    from app import main as app_main

    monkeypatch.setattr(app_config.settings, "allow_user_python_strategies", allowed)
    custom_dir = tmp_path / "strategies" / "custom"
    custom_dir.mkdir(parents=True)
    marker = tmp_path / "marker.txt"
    strategy_file = custom_dir / "custom_marker.py"
    strategy_file.write_text(_marker_strategy("custom_marker", marker), encoding="utf-8")

    engine = app_main._build_strategy_engine(tmp_path)

    assert marker.exists() is allowed
    assert engine.has("custom_marker") is allowed
    assert engine.file_disabled(strategy_file) is not allowed
    assert any(s.source == "builtin" for s in engine.strategy_definitions())
