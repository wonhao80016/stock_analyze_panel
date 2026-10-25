from __future__ import annotations

import queue
import threading
from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.backtest import worker as worker_module
from app.backtest.strategy import StrategyBacktestConfig
from app.backtest.worker import BacktestWorkerError, make_worker_task, run_worker_task


@pytest.fixture(autouse=True)
def allow_user_strategies_in_worker(monkeypatch):
    # Spawned workers read their own settings; these tests run a strategy from strategies/custom.
    monkeypatch.setenv("ALLOW_USER_PYTHON_STRATEGIES", "true")


def _write_worker_strategy(data_dir, marker=None) -> None:
    strategy_dir = data_dir / "strategies" / "custom"
    strategy_dir.mkdir(parents=True)
    # polars I/O passes the AST check, so the marker proves the module body ran.
    prelude = (
        f"import polars as pl\npl.DataFrame({{'ran': [1]}}).write_csv({marker.as_posix()!r})\n"
        if marker is not None
        else ""
    )
    (strategy_dir / "worker_always_entry.py").write_text(
        prelude
        + """import numpy as np
from app.backtest.matrix import make_signal_matrix

META = {
    "id": "worker_always_entry",
    "name": "worker",
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [
        {"id": "gate", "type": "int", "default": 1, "min": 1, "max": 2, "step": 1},
    ],
    "scoring": {},
    "required_features": ["open", "high", "low", "close", "volume"],
}
EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 1

class AlwaysEntry:
    def required_fields(self):
        return frozenset({"open", "high", "low", "close", "volume"})

    def required_warmup_bars(self, params):
        return 1

    def compute_signals(self, market, params):
        return make_signal_matrix(
            market.shape,
            entry=np.ones(market.shape, dtype=np.uint8),
        )

MATRIX_STRATEGY = AlwaysEntry()
""",
        encoding="utf-8",
    )


def _write_market_data(data_dir, start: date, days: int = 3) -> None:
    rows = []
    for offset in range(days):
        close = 10.0 + offset
        current = start + timedelta(days=offset)
        rows.append({
            "symbol": "600000.SH",
            "date": current,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000.0,
            "amount": close * 100000.0,
            "raw_close": close,
            "raw_high": close,
            "raw_low": close,
            "turnover_rate": 1.0,
            "consecutive_limit_ups": 0,
            "consecutive_limit_downs": 0,
        })
        partition = data_dir / "kline_daily_enriched" / f"date={current.isoformat()}"
        partition.mkdir(parents=True)
        pl.DataFrame([rows[-1]]).write_parquet(partition / "part.parquet")

    instruments_dir = data_dir / "instruments"
    instruments_dir.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["600000.SH"],
        "name": ["浦发银行"],
        "total_shares": [1_000_000_000.0],
        "float_shares": [1_000_000_000.0],
    }).write_parquet(instruments_dir / "part.parquet")


def test_spawn_worker_returns_compact_result_and_memory_metrics(tmp_path):
    start = date(2024, 1, 1)
    data_dir = tmp_path / "data"
    _write_worker_strategy(data_dir)
    _write_market_data(data_dir, start)
    config = StrategyBacktestConfig(
        strategy_id="worker_always_entry",
        symbols=["600000.SH"],
        start=start,
        end=start + timedelta(days=2),
        overrides={"basic_filter": {"enabled": False}},
        matching="close_t",
        fees_pct=0,
        slippage_bps=0,
        max_positions=1,
    )

    result = run_worker_task(make_worker_task("backtest", data_dir, config))

    assert result["error"] is None
    assert result["stats"]["execution_backend"] == "matrix_native"
    worker = result["stats"]["worker"]
    assert worker["peak_rss_bytes"] > 0
    assert worker["serialized_result_bytes"] > 0
    assert worker["worker_exitcode"] == 0
    assert worker["parent_rss_after_worker_exit_bytes"] > 0


@pytest.mark.parametrize("allowed", ["false", "true"])
def test_spawn_worker_runs_user_strategies_only_when_enabled(tmp_path, monkeypatch, allowed):
    monkeypatch.setenv("ALLOW_USER_PYTHON_STRATEGIES", allowed)
    start = date(2024, 1, 1)
    data_dir = tmp_path / "data"
    marker = tmp_path / "marker.txt"
    _write_worker_strategy(data_dir, marker)
    _write_market_data(data_dir, start)
    config = StrategyBacktestConfig(
        strategy_id="worker_always_entry",
        symbols=["600000.SH"],
        start=start,
        end=start + timedelta(days=2),
        overrides={"basic_filter": {"enabled": False}},
        matching="close_t",
        fees_pct=0,
        slippage_bps=0,
        max_positions=1,
    )

    result = run_worker_task(make_worker_task("backtest", data_dir, config))

    if allowed == "true":
        assert result["error"] is None
        assert marker.exists()
    else:
        assert result["error"] == "unknown strategy: worker_always_entry"
        assert not marker.exists()


def test_worker_terminates_child_after_cancel_grace(monkeypatch, tmp_path):
    class FakeQueue:
        def get(self, timeout):
            raise queue.Empty

        def close(self):
            pass

        def join_thread(self):
            pass

    class FakeEvent:
        def set(self):
            pass

    class FakeProcess:
        def __init__(self):
            self.alive = True
            self.exitcode = None

        def start(self):
            pass

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            pass

        def terminate(self):
            self.alive = False
            self.exitcode = -15

    process = FakeProcess()
    context = SimpleNamespace(
        Queue=FakeQueue,
        Event=FakeEvent,
        Process=lambda **_kwargs: process,
    )
    clock = iter([0.0, 0.0, 0.0, 6.0])
    monkeypatch.setattr(worker_module.mp, "get_context", lambda _method: context)
    monkeypatch.setattr(worker_module.time, "monotonic", lambda: next(clock))
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(BacktestWorkerError, match="after cancellation"):
        run_worker_task(
            {"kind": "backtest", "data_dir": str(tmp_path), "config": {}},
            cancel_event=cancel_event,
        )

    assert process.exitcode == -15


def test_worker_accepts_delivered_result_when_child_exit_is_slow(monkeypatch, tmp_path):
    """终态消息已送达但子进程退出收尾超时: 应强杀后采纳结果, 而非丢弃报错。"""

    class FakeQueue:
        def __init__(self):
            self._messages = [{"type": "result", "payload": {"status": "ok"}}]

        def get(self, timeout):
            if self._messages:
                return self._messages.pop(0)
            raise queue.Empty

        def close(self):
            pass

        def join_thread(self):
            pass

    class FakeEvent:
        def set(self):
            pass

    class FakeProcess:
        def __init__(self):
            self.alive = True
            self.exitcode = None

        def start(self):
            pass

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            pass

        def terminate(self):
            self.alive = False
            self.exitcode = -15

    process = FakeProcess()
    context = SimpleNamespace(
        Queue=FakeQueue,
        Event=FakeEvent,
        Process=lambda **_kwargs: process,
    )
    monkeypatch.setattr(worker_module.mp, "get_context", lambda _method: context)

    result = run_worker_task({"kind": "backtest", "data_dir": str(tmp_path), "config": {}})

    assert result["status"] == "ok"
    assert result["stats"]["worker"]["worker_exit_forcibly"] is True
    assert result["stats"]["worker"]["worker_exitcode"] == -15
    assert process.exitcode == -15
