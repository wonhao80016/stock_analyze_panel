"""User Python strategies stay off unless ALLOW_USER_PYTHON_STRATEGIES enables them.

Strategy files run in the backend process and the AST check can be bypassed, so by
default no endpoint writes a user strategy file and no file under data/strategies runs.
Once enabled, only loopback requests may save one.
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config as app_config
from app.api import strategy as strategy_api
from app.strategy.engine import StrategyEngine

BUILTIN_DIR = Path(strategy_api.__file__).resolve().parents[1] / "strategy" / "builtin"
SWITCH = "ALLOW_USER_PYTHON_STRATEGIES"
LOCAL_PEER = ("127.0.0.1", 50000)
LAN_PEER = ("192.168.1.20", 50000)


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


def _bypass_payloads(marker: Path) -> dict[str, str]:
    return {
        "plain": "",
        "exec_alias": f'_run = exec\n_run("open({marker.as_posix()!r}, \'w\').close()")\n',
        "numpy_ctypeslib": "import numpy.ctypeslib as _c\n_cdll = _c.ctypes.CDLL\n",
        "polars_write_csv": f'pl.DataFrame({{"ran": [1]}}).write_csv({marker.as_posix()!r})\n',
    }


def _app(data_dir: Path, engine: StrategyEngine | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(strategy_api.router)
    app.state.strategy_engine = engine or StrategyEngine(strategy_dirs=_user_dirs(data_dir))
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=data_dir))
    return app


def _client(app: FastAPI, peer: tuple[str, int] = LOCAL_PEER) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1:3018", client=peer)


def _py_files(data_dir: Path) -> list[Path]:
    return sorted((data_dir / "strategies").rglob("*.py"))


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_config.settings, "allow_user_python_strategies", True)


@pytest.fixture
def disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_config.settings, "allow_user_python_strategies", False)


def test_settings_default_disables_user_python_strategies(monkeypatch) -> None:
    monkeypatch.delenv(SWITCH, raising=False)

    assert app_config.Settings(_env_file=None).allow_user_python_strategies is False


@pytest.mark.parametrize("payload", ["plain", "exec_alias", "numpy_ctypeslib", "polars_write_csv"])
def test_code_save_is_refused_by_default_and_writes_nothing(tmp_path, disabled, payload) -> None:
    marker = tmp_path / "marker.txt"
    code = _strategy_code("custom_gate", _bypass_payloads(marker)[payload])

    res = _client(_app(tmp_path)).post(
        "/api/strategies/code/save",
        json={"code": code, "strategy_id": "custom_gate", "target_source": "custom"},
    )

    assert res.status_code == 403
    assert SWITCH in res.json()["detail"]
    assert _py_files(tmp_path) == []
    assert not marker.exists()


def test_ai_save_is_refused_by_default_and_writes_nothing(tmp_path, disabled) -> None:
    marker = tmp_path / "marker.txt"

    res = _client(_app(tmp_path)).post(
        "/api/strategies/ai/save",
        json={"code": _marker_strategy("ai_gate", marker), "strategy_id": "ai_gate"},
    )

    assert res.status_code == 403
    assert SWITCH in res.json()["detail"]
    assert _py_files(tmp_path) == []
    assert not marker.exists()


def test_composite_save_is_refused_by_default_and_writes_nothing(tmp_path, disabled) -> None:
    res = _client(_app(tmp_path)).post(
        "/api/strategies/composite/save",
        json={"strategy_id": "composite_gate", "children": [{"strategy_id": "any"}]},
    )

    assert res.status_code == 403
    assert SWITCH in res.json()["detail"]
    assert _py_files(tmp_path) == []


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


def test_disabled_files_stay_listed_through_the_reload_endpoint(tmp_path) -> None:
    ai_dir = tmp_path / "strategies" / "ai"
    ai_dir.mkdir(parents=True)
    marker = tmp_path / "marker.txt"
    (ai_dir / "ai_marker.py").write_text(_marker_strategy("ai_marker", marker), encoding="utf-8")
    user_dirs = _user_dirs(tmp_path)
    engine = StrategyEngine(strategy_dirs=user_dirs, disabled_dirs=user_dirs)

    res = _client(_app(tmp_path, engine)).post("/api/strategies/reload")

    assert res.status_code == 200
    assert not marker.exists()
    assert [Path(item["file"]).name for item in res.json()["load_errors"]] == ["ai_marker.py"]


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


def test_enabled_local_request_saves_and_loads(tmp_path, enabled) -> None:
    app = _app(tmp_path)
    client = _client(app)

    code_res = client.post(
        "/api/strategies/code/save",
        json={"code": _strategy_code("custom_local"), "strategy_id": "custom_local"},
    )
    ai_res = client.post(
        "/api/strategies/ai/save",
        json={"code": _strategy_code("ai_local"), "strategy_id": "ai_local"},
    )

    assert code_res.status_code == 200, code_res.text
    assert ai_res.status_code == 200, ai_res.text
    engine = app.state.strategy_engine
    assert engine.get("custom_local").source == "custom"
    assert engine.get("ai_local").source == "ai"
    assert (tmp_path / "strategies" / "custom" / "custom_local.py").exists()
    assert (tmp_path / "strategies" / "ai" / "ai_local.py").exists()


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


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/strategies/code/save", {"code": _strategy_code("custom_lan"), "strategy_id": "custom_lan"}),
        ("/api/strategies/ai/save", {"code": _strategy_code("ai_lan"), "strategy_id": "ai_lan"}),
        (
            "/api/strategies/composite/save",
            {"strategy_id": "composite_lan", "children": [{"strategy_id": "any"}]},
        ),
    ],
)
def test_enabled_non_local_request_is_refused(tmp_path, enabled, path, body) -> None:
    res = _client(_app(tmp_path), peer=LAN_PEER).post(path, json=body)

    assert res.status_code == 403
    assert "本机" in res.json()["detail"]
    assert _py_files(tmp_path) == []


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
