"""Strategy file loading: module names, META aliases, source classification."""
from __future__ import annotations

import json
import sys

import pytest

from app.strategy.engine import StrategyEngine


def _code(strategy_id: str, meta_name: str = "META") -> str:
    return f'''import polars as pl

{meta_name} = {{"id": "{strategy_id}", "name": "別名策略", "params": [], "scoring": {{}}}}

def filter(df, params):
    return pl.lit(True)
'''


# ── B167 ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _restore_shadowed_modules():
    saved = {name: sys.modules.get(name) for name in ("json", "statistics")}
    yield
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


@pytest.mark.parametrize("stem", ["json", "statistics"])
def test_a_strategy_file_named_like_a_module_does_not_replace_it(tmp_path, stem):
    custom = tmp_path / "strategies" / "custom"
    custom.mkdir(parents=True)
    (custom / f"{stem}.py").write_text(_code(f"custom_{stem}"), encoding="utf-8")
    before = sys.modules.get(stem)

    engine = StrategyEngine(strategy_dirs=[custom])

    assert sys.modules.get(stem) is before
    assert f"custom_{stem}" not in {s.meta["id"] for s in engine.strategy_definitions()}
    assert any(f"{stem}.py" in item["file"] for item in engine.load_errors())
    assert json.dumps({"ok": 1}) == '{"ok": 1}'


def test_reloading_the_same_strategy_file_still_works(tmp_path):
    custom = tmp_path / "strategies" / "custom"
    custom.mkdir(parents=True)
    (custom / "custom_reload_me.py").write_text(_code("custom_reload_me"), encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[custom])

    engine.reload()

    assert engine.get("custom_reload_me").meta["id"] == "custom_reload_me"
    assert engine.load_errors() == []


# ── B168 ────────────────────────────────────────────────


@pytest.mark.parametrize("alias", ["STRATEGY_META", "meta"])
def test_meta_aliases_load_the_declared_meta(tmp_path, alias):
    custom = tmp_path / "strategies" / "custom"
    custom.mkdir(parents=True)
    (custom / "custom_alias_file.py").write_text(_code("custom_alias", alias), encoding="utf-8")

    engine = StrategyEngine(strategy_dirs=[custom])

    loaded = engine.get("custom_alias")
    assert loaded.meta["name"] == "別名策略"


# ── B169 ────────────────────────────────────────────────


@pytest.mark.parametrize("marker", ["ai", "builtin", "composite"])
def test_source_follows_the_strategy_directory_not_a_path_substring(tmp_path, marker):
    custom = tmp_path / marker / "data" / "strategies" / "custom"
    custom.mkdir(parents=True)
    (custom / "custom_where.py").write_text(
        _code("custom_where").replace('"params": []', '"asset_types": ["stock"], "params": []'),
        encoding="utf-8",
    )

    engine = StrategyEngine(strategy_dirs=[custom])

    assert engine.get("custom_where").source == "custom"


def test_ai_directory_is_still_classified_as_ai(tmp_path):
    ai = tmp_path / "strategies" / "ai"
    ai.mkdir(parents=True)
    (ai / "ai_here.py").write_text(_code("ai_here"), encoding="utf-8")

    engine = StrategyEngine(strategy_dirs=[ai])

    assert engine.get("ai_here").source == "ai"
