"""A spawned worker must exit on its own once its parent process is killed."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import psutil

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_START = date(2024, 1, 1)
_DAYS = 3

_BUSY_STRATEGY = """import polars as pl

META = {
    "id": "worker_busy",
    "name": "busy",
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [],
    "scoring": {},
    "required_features": ["open", "high", "low", "close", "volume"],
}
EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 1

class Busy:
    def required_fields(self):
        return frozenset({"open", "high", "low", "close", "volume"})

    def required_warmup_bars(self, params):
        return 1

    def compute_signals(self, market, params):
        pl.DataFrame({"busy": [1]}).write_csv(MARKER)
        while True:
            pass

MATRIX_STRATEGY = Busy()
"""

_PARENT_SCRIPT = """import os
import sys
from datetime import date, timedelta
from pathlib import Path

backend_dir, data_dir, pid_file = sys.argv[1:4]
sys.path.insert(0, backend_dir)

from app.backtest.strategy import StrategyBacktestConfig
from app.backtest.worker import make_worker_task, run_worker_task

start = date(2024, 1, 1)
config = StrategyBacktestConfig(
    strategy_id="worker_busy",
    symbols=["600000.SH"],
    start=start,
    end=start + timedelta(days=2),
    overrides={"basic_filter": {"enabled": False}},
    matching="close_t",
    fees_pct=0,
    slippage_bps=0,
    max_positions=1,
)
Path(pid_file).write_text(str(os.getpid()))
run_worker_task(make_worker_task("backtest", Path(data_dir), config))
"""


def _write_data(data_dir: Path, marker: Path) -> None:
    strategy_dir = data_dir / "strategies" / "custom"
    strategy_dir.mkdir(parents=True)
    (strategy_dir / "worker_busy.py").write_text(
        f"MARKER = {marker.as_posix()!r}\n" + _BUSY_STRATEGY, encoding="utf-8"
    )
    for offset in range(_DAYS):
        current = _START + timedelta(days=offset)
        close = 10.0 + offset
        partition = data_dir / "kline_daily_enriched" / f"date={current.isoformat()}"
        partition.mkdir(parents=True)
        pl.DataFrame([{
            "symbol": "600000.SH", "date": current,
            "open": close, "high": close, "low": close, "close": close,
            "volume": 1000.0, "amount": close * 100000.0,
            "raw_close": close, "raw_high": close, "raw_low": close,
            "turnover_rate": 1.0, "consecutive_limit_ups": 0, "consecutive_limit_downs": 0,
        }]).write_parquet(partition / "part.parquet")
    instruments_dir = data_dir / "instruments"
    instruments_dir.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["600000.SH"],
        "name": ["test"],
        "total_shares": [1_000_000_000.0],
        "float_shares": [1_000_000_000.0],
    }).write_parquet(instruments_dir / "part.parquet")


def _wait_for(predicate, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


def test_worker_exits_after_parent_is_killed(tmp_path):
    data_dir = tmp_path / "data"
    marker = tmp_path / "busy.csv"
    pid_file = tmp_path / "parent.pid"
    _write_data(data_dir, marker)
    env = {**os.environ, "ALLOW_USER_PYTHON_STRATEGIES": "true"}
    launcher = subprocess.Popen(
        [sys.executable, "-c", _PARENT_SCRIPT, str(_BACKEND_DIR), str(data_dir), str(pid_file)],
        env=env,
        cwd=str(tmp_path),
    )
    children: list[psutil.Process] = []
    try:
        # The venv launcher on Windows may sit between Popen and the interpreter.
        parent_pid = int(_wait_for(
            lambda: pid_file.exists() and pid_file.read_text().strip(), 60, "parent pid"
        ))
        parent = psutil.Process(parent_pid)
        _wait_for(marker.exists, 90, "worker to start computing")
        children = parent.children(recursive=True)
        assert children, "parent has no spawned worker"

        parent.kill()
        parent.wait(timeout=10)

        _, alive = psutil.wait_procs(children, timeout=15)
        assert not alive, f"orphaned workers still running: {[p.pid for p in alive]}"
    finally:
        for proc in children:
            if proc.is_running():
                proc.kill()
        if launcher.poll() is None:
            launcher.kill()
        launcher.wait(timeout=10)


def test_watchdog_exits_with_parent_gone_code_when_parent_join_returns():
    from app.backtest import worker as worker_module

    exits: list[int] = []

    class GoneParent:
        def join(self, timeout=None):
            return None

    thread = worker_module._start_parent_watchdog(GoneParent(), exit_fn=exits.append)
    thread.join(timeout=5)

    assert exits == [worker_module._PARENT_GONE_EXITCODE]


def test_watchdog_is_not_started_outside_a_spawned_child():
    from app.backtest import worker as worker_module

    assert worker_module._start_parent_watchdog(exit_fn=lambda _code: None) is None
