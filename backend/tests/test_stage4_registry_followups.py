"""Strategy registry follow-ups: builtin ids and concurrent reloads."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from app.api.strategy import list_strategies
from app.strategy.engine import StrategyEngine

pytestmark = pytest.mark.usefixtures("deny_network")
JOIN_TIMEOUT = 30


def _custom(sid: str, *, descending: bool = True) -> str:
    return f'''import polars as pl
META = {{"id": "{sid}", "name": "{sid}", "asset_types": ["stock"], "timeframes": ["1d"],
        "scoring": {{"amount": 1.0}}, "descending": {descending}}}
EXECUTION_BACKEND = "polars_expr"
def filter(df, params):
    return pl.lit(True)
'''


class _MonitorEngine:
    def __init__(self) -> None:
        self.invalidations = 0

    def invalidate_strategy_state(self) -> None:
        self.invalidations += 1


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "strategies"
    dirs = {source: root / source for source in ("builtin", "custom", "ai", "composite")}
    for directory in dirs.values():
        directory.mkdir(parents=True)
    (dirs["builtin"] / "shared_id.py").write_text(_custom("shared_id"), encoding="utf-8")
    (dirs["custom"] / "custom_high.py").write_text(_custom("custom_high"), encoding="utf-8")
    (dirs["custom"] / "custom_low.py").write_text(
        _custom("custom_low", descending=False), encoding="utf-8"
    )
    engine = StrategyEngine(strategy_dirs=list(dirs.values()))
    monitor = _MonitorEngine()
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(repo=repo, strategy_engine=engine, monitor_engine=monitor)
        )
    )
    return SimpleNamespace(
        engine=engine, request=request, dirs=dirs, data_dir=tmp_path, monitor=monitor
    )


def _errors_by_file(engine: StrategyEngine) -> dict[str, str]:
    return {item["file"]: item["error"] for item in engine.load_errors()}


# --------------------------------------------------------------------------------------------
# A user file reusing a builtin id
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("builtin_first", [True, False], ids=["builtin_first", "user_first"])
def test_a_user_file_reusing_a_builtin_id_keeps_the_builtin_and_reports_the_file(
    workspace, builtin_first
):
    shadows = [
        workspace.dirs["custom"] / "custom_shadow.py",
        workspace.dirs["ai"] / "shared_id.py",
    ]
    for shadow in shadows:
        shadow.write_text(_custom("shared_id"), encoding="utf-8")
    dirs = [workspace.dirs[source] for source in ("builtin", "custom", "ai")]
    engine = StrategyEngine(strategy_dirs=dirs if builtin_first else dirs[::-1])

    assert engine.get("shared_id").source == "builtin"
    errors = _errors_by_file(engine)
    assert set(errors) == {str(shadow) for shadow in shadows}
    assert all("already used by the builtin strategy shared_id.py" in e for e in errors.values())


def test_two_user_files_with_one_id_still_drop_out_together(workspace):
    for source in ("custom", "ai"):
        (workspace.dirs[source] / "custom_twice.py").write_text(
            _custom("custom_twice"), encoding="utf-8"
        )

    workspace.engine.reload()

    assert not workspace.engine.has("custom_twice")
    assert all("duplicate strategy id" in e for e in _errors_by_file(workspace.engine).values())


# --------------------------------------------------------------------------------------------
# Concurrent reloads
# --------------------------------------------------------------------------------------------


def test_the_list_survives_a_reload_that_drops_a_strategy_while_it_is_built(workspace, monkeypatch):
    from app.api import strategy as strategy_api

    real_detail = strategy_api._strategy_detail
    dropped = workspace.dirs["custom"] / "custom_low.py"

    def detail_during_reload(strategy, overrides=None, engine=None):
        if dropped.exists():
            dropped.unlink()
            workspace.engine.reload()
        return real_detail(strategy, overrides, engine)

    monkeypatch.setattr(strategy_api, "_strategy_detail", detail_during_reload)

    response = list_strategies(workspace.request)

    assert {item["id"] for item in response["strategies"]} == {
        "shared_id",
        "custom_high",
        "custom_low",
    }


class _SignallingLock:
    """A lock that reports a caller about to wait for it."""

    def __init__(self, on_wait) -> None:
        self._lock = threading.Lock()
        self._on_wait = on_wait

    def __enter__(self):
        if not self._lock.acquire(blocking=False):
            self._on_wait()
            self._lock.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self._lock.release()


def test_a_slower_earlier_reload_never_replaces_a_newer_registry(workspace, monkeypatch):
    """The earlier reload listed the files before a save wrote a new one and finishes last."""
    engine = workspace.engine
    earlier_inside = threading.Event()
    earlier_may_finish = threading.Event()
    real_load_file = StrategyEngine._load_file

    def load_file(path):
        if threading.current_thread().name == "earlier-reload" and path.name == "custom_high.py":
            earlier_inside.set()
            assert earlier_may_finish.wait(JOIN_TIMEOUT)
        return real_load_file(path)

    monkeypatch.setattr(StrategyEngine, "_load_file", staticmethod(load_file))
    monkeypatch.setattr(
        engine, "_reload_lock", _SignallingLock(earlier_may_finish.set), raising=False
    )

    def later_reload():
        engine.reload()
        earlier_may_finish.set()

    earlier = threading.Thread(target=engine.reload, name="earlier-reload")
    earlier.start()
    assert earlier_inside.wait(JOIN_TIMEOUT)
    (workspace.dirs["custom"] / "custom_new.py").write_text(_custom("custom_new"), encoding="utf-8")
    later = threading.Thread(target=later_reload, name="later-reload")
    later.start()
    later.join(JOIN_TIMEOUT)
    earlier.join(JOIN_TIMEOUT)

    assert not earlier.is_alive() and not later.is_alive()
    assert engine.has("custom_new")
