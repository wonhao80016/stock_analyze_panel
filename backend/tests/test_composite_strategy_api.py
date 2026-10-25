"""_strategy_detail 对非叠加策略的 composite_children 回显。"""
from __future__ import annotations

from types import SimpleNamespace

from app.api.strategy import _strategy_detail
from app.strategy.engine import StrategyEngine


def _filter_code(strategy_id: str) -> str:
    meta = (
        f'{{"id": "{strategy_id}", "name": "{strategy_id}", '
        '"asset_types": ["stock"], "timeframes": ["1d"]}'
    )
    return f'''import polars as pl
META = {meta}
EXECUTION_BACKEND = "polars_expr"
def filter(df, params):
    return pl.lit(True)
'''


def _setup_engine(tmp_path):
    """构造含 custom + composite 目录的 engine 和假 request。"""
    custom_dir = tmp_path / "strategies" / "custom"
    comp_dir = tmp_path / "strategies" / "composite"
    custom_dir.mkdir(parents=True)
    comp_dir.mkdir(parents=True)
    engine = StrategyEngine(strategy_dirs=[custom_dir, comp_dir])
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repo=repo, strategy_engine=engine))
    )
    return engine, request, custom_dir, comp_dir


# ───────────────────────── 渲染 ─────────────────────────


# ───────────────────────── 保存 ─────────────────────────


# ───────────────────────── _strategy_detail ─────────────────────────


def test_strategy_detail_non_composite_has_null_children(tmp_path):
    engine, _request, custom_dir, _ = _setup_engine(tmp_path)
    (custom_dir / "child_a.py").write_text(_filter_code("child_a"), encoding="utf-8")
    engine.reload()

    detail = _strategy_detail(engine.get("child_a"))
    assert detail["composite_children"] is None


# ───────────────────────── 删除防护 ─────────────────────────
