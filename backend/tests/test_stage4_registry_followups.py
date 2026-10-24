"""Strategy registry follow-ups: unloadable sources, builtin ids, failed saves and concurrent reloads."""

from __future__ import annotations

import threading
from pathlib import Path
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
    get_strategy_source,
    list_strategies,
)
from app.strategy.engine import StrategyEngine

pytestmark = pytest.mark.usefixtures("deny_network")

BROKEN = "this is not valid python"
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


def _status(call) -> int:
    with pytest.raises(HTTPException) as raised:
        call()
    return raised.value.status_code


# --------------------------------------------------------------------------------------------
# GET /{id}/source for a file that failed to load
# --------------------------------------------------------------------------------------------


def test_the_source_of_an_unloadable_user_file_is_served_with_its_load_error(workspace):
    path = workspace.dirs["custom"] / "custom_broken.py"
    path.write_text(BROKEN, encoding="utf-8")
    workspace.engine.reload()

    response = get_strategy_source("custom_broken", workspace.request)

    assert response["code"] == BROKEN
    assert response["source"] == "custom"
    assert response["load_failed"] is True
    assert response["load_error"] == _errors_by_file(workspace.engine)[str(path)]


def test_the_source_of_a_loaded_strategy_is_flagged_as_loaded(workspace):
    response = get_strategy_source("custom_high", workspace.request)

    assert response["code"] == _custom("custom_high")
    assert response["source"] == "custom"
    assert response["load_failed"] is False
    assert response["load_error"] is None


def test_an_unreadable_byte_does_not_hide_the_source_of_an_unloadable_file(workspace):
    path = workspace.dirs["ai"] / "ai_bytes.py"
    path.write_bytes(b"META = {'id': 'ai_bytes'}\n\xff\xfe broken\n")
    workspace.engine.reload()

    response = get_strategy_source("ai_bytes", workspace.request)

    assert response["load_failed"] is True
    assert response["source"] == "ai"
    assert response["code"].startswith("META = {'id': 'ai_bytes'}")


@pytest.mark.parametrize(
    "strategy_id",
    [
        "../custom/custom_broken",
        "..\\custom\\custom_broken",
        "custom/custom_broken",
        "custom_broken.py",
        "CUSTOM_BROKEN",
        "custom_missing",
    ],
)
def test_the_source_lookup_never_builds_a_path_from_the_id(workspace, strategy_id):
    (workspace.dirs["custom"] / "custom_broken.py").write_text(BROKEN, encoding="utf-8")
    workspace.engine.reload()

    assert _status(lambda: get_strategy_source(strategy_id, workspace.request)) == 404


def test_the_source_of_an_unloadable_file_outside_the_user_directories_is_not_served(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "custom_stray.py").write_text(BROKEN, encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[outside])
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
                strategy_engine=engine,
            )
        )
    )

    assert _status(lambda: get_strategy_source("custom_stray", request)) == 404


def test_two_unloadable_files_with_one_id_are_not_served(workspace):
    for source in ("custom", "ai"):
        (workspace.dirs[source] / "custom_twice.py").write_text(BROKEN, encoding="utf-8")
    workspace.engine.reload()

    assert _status(lambda: get_strategy_source("custom_twice", workspace.request)) == 409


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


def test_delete_of_a_builtin_id_removes_only_the_user_file_reusing_it(workspace):
    shadow = workspace.dirs["custom"] / "shared_id.py"
    shadow.write_text(_custom("shared_id"), encoding="utf-8")
    workspace.engine.reload()
    override = workspace.data_dir / "user_data" / "strategy_overrides" / "shared_id.json"
    override.parent.mkdir(parents=True)
    override.write_text("{}", encoding="utf-8")

    result = delete_strategy("shared_id", workspace.request)

    assert result["ok"] is True
    assert not shadow.exists()
    assert (workspace.dirs["builtin"] / "shared_id.py").exists()
    assert override.exists()
    assert workspace.engine.get("shared_id").source == "builtin"
    assert workspace.engine.load_errors() == []


def test_delete_of_a_builtin_without_a_user_file_reusing_its_id_is_still_forbidden(workspace):
    assert _status(lambda: delete_strategy("shared_id", workspace.request)) == 403
    assert (workspace.dirs["builtin"] / "shared_id.py").exists()


# --------------------------------------------------------------------------------------------
# A failed save that changes the registry
# --------------------------------------------------------------------------------------------


def _failing_code_save(workspace) -> None:
    _save_strategy_code(
        StrategyCodeSaveRequest(
            code=_custom("custom_bad") + "\n_LOAD_FAILS = 1 / 0\n",
            strategy_id="custom_bad",
        ),
        workspace.request,
    )


def _failing_composite_save(workspace) -> None:
    _save_composite_strategy(
        StrategyCompositeSaveRequest(
            strategy_id="composite_bad", children=[CompositeChildItem(strategy_id="custom_low")]
        ),
        workspace.request,
    )


def _strategy_cache(workspace) -> Path:
    from app.services import strategy_cache

    path = strategy_cache._cache_path(workspace.data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"as_of": "2024-03-01", "results": {}}', encoding="utf-8")
    return path


@pytest.mark.parametrize("save", [_failing_code_save, _failing_composite_save])
def test_a_failed_save_whose_reload_drops_a_strategy_invalidates_the_runtime(workspace, save):
    cache = _strategy_cache(workspace)
    (workspace.dirs["custom"] / "custom_high.py").write_text(BROKEN, encoding="utf-8")

    with pytest.raises(ValueError):
        save(workspace)

    assert not workspace.engine.has("custom_high")
    assert not cache.exists()
    assert workspace.monitor.invalidations == 1


@pytest.mark.parametrize("save", [_failing_code_save, _failing_composite_save])
def test_a_failed_save_that_leaves_the_registry_unchanged_keeps_the_runtime(workspace, save):
    cache = _strategy_cache(workspace)

    with pytest.raises(ValueError):
        save(workspace)

    assert workspace.engine.has("custom_high")
    assert cache.exists()
    assert workspace.monitor.invalidations == 0


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


def test_a_save_is_judged_on_the_errors_of_its_own_reload(workspace, monkeypatch):
    """Another save's reload lands between this save's reload and its error check."""
    engine = workspace.engine
    real_reload = engine.reload
    other = workspace.dirs["custom"] / "custom_other.py"

    def reload_then_another_save_reloads():
        own = real_reload()
        if not other.exists():
            other.write_text(BROKEN, encoding="utf-8")
            real_reload()
        return own

    monkeypatch.setattr(engine, "reload", reload_then_another_save_reloads)

    saved = _save_strategy_code(
        StrategyCodeSaveRequest(code=_custom("custom_saved"), strategy_id="custom_saved"),
        workspace.request,
    )

    assert saved["ok"] is True
    assert engine.has("custom_saved")


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
