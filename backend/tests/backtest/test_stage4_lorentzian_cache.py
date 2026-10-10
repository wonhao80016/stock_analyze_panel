"""lorentzian_knn works from a numba cache whichever way the strategy module was loaded first.

The strategy engine loads builtins by file path under their bare file name, while tests and
other code import them by package name. A cached njit kernel records the module it was defined
in, so a kernel defined inside a builtin breaks the other import path once the cache exists.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("deny_network")

BACKEND_DIR = Path(__file__).resolve().parents[2]
BUILTIN_DIR = BACKEND_DIR / "app" / "strategy" / "builtin"

_RUN_LORENTZIAN = r"""
import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from app.backtest.matrix import build_market_data_matrix

days, day = [], date(2023, 1, 2)
while len(days) < 450:
    if day.weekday() < 5:
        days.append(day)
    day += timedelta(days=1)
rng = np.random.default_rng(1)
close = 50 * np.cumprod(1 + rng.normal(0, 0.02, (450, 3)), axis=0)
panel = pl.DataFrame({
    "symbol": np.repeat(["1101.TWSE", "1102.TWSE", "1103.TWSE"], 450),
    "date": days * 3,
    "open": close.T.ravel(),
    "high": close.T.ravel() * 1.01,
    "low": close.T.ravel() * 0.99,
    "close": close.T.ravel(),
    "volume": np.full(1350, 1e6),
})
market = build_market_data_matrix(panel)
if sys.argv[1] == "engine":
    from app.strategy.engine import StrategyEngine

    path = Path(sys.argv[2]) / "lorentzian_knn.py"
    strategy = StrategyEngine._load_file(path).matrix_strategy
else:
    from app.strategy.builtin.lorentzian_knn import MATRIX_STRATEGY as strategy
params = {"min_prediction": 0.0, "use_volatility_filter": False, "use_regime_filter": False}
signals = strategy.compute_signals(market, params)
digest = hashlib.sha256(signals.entry.tobytes() + signals.exit.tobytes()).hexdigest()
print(json.dumps({"entries": int(signals.entry.sum()), "digest": digest}))
"""


def _run(how: str, cache_dir: Path, data_dir: Path) -> dict:
    env = {**os.environ, "NUMBA_CACHE_DIR": str(cache_dir), "DATA_DIR": str(data_dir)}
    completed = subprocess.run(
        [sys.executable, "-c", _RUN_LORENTZIAN, how, str(BUILTIN_DIR)],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stderr[-3000:]
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_a_cache_written_through_the_engine_loader_serves_a_package_import(tmp_path):
    cache_dir = tmp_path / "numba_cache"
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    through_engine = _run("engine", cache_dir, data_dir)
    assert list(cache_dir.rglob("*.nbi"))
    through_package = _run("package", cache_dir, data_dir)

    assert through_engine["entries"] > 0
    assert through_package == through_engine


def _cached_jit_decorators(path: Path) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if any(
                keyword.arg == "cache"
                and not (isinstance(keyword.value, ast.Constant) and keyword.value.value is False)
                for keyword in decorator.keywords
            ):
                lines.append(decorator.lineno)
    return lines


def test_no_builtin_strategy_defines_a_cached_numba_kernel():
    offenders = {
        path.name: lines
        for path in sorted(BUILTIN_DIR.glob("*.py"))
        if (lines := _cached_jit_decorators(path))
    }

    assert offenders == {}
