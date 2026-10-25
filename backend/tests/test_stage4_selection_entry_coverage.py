"""The realtime monitor refuses an unscreenable session as the backtest does (stage 4, D3)."""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from app.strategy import monitor_rules
from app.strategy.engine import StrategyEngine
from app.strategy.monitor import MonitorRuleEngine
from app.taiwan import share_capital_store

pytestmark = pytest.mark.usefixtures("deny_network")

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "app" / "strategy" / "builtin"
DAYS = 80
START = date(2024, 1, 1)
LAST = START + timedelta(days=DAYS - 1)

COVERED = ("1101.TWSE", "1102.TWSE", "1103.TWSE")
UNCOVERED = ("1104.TWSE", "1105.TWSE")
TDR = "9103.TWSE"
ETF = "0050.TWSE"
TURNOVER = {"1101.TWSE": 2.1, "1102.TWSE": 2.2, "1103.TWSE": 2.3}

NEEDS_TURNOVER = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {"id": "custom_needs_turnover", "name": "needs turnover", "asset_types": ["stock"],
        "limit": 0}
BASIC_FILTER = {"enabled": False}
EXECUTION_BACKEND = "matrix_native"


class _EveryBar:
    def required_fields(self):
        return frozenset({"close", "turnover_rate"})

    def required_coverage_fields(self, params):
        return frozenset({"turnover_rate"})

    def required_warmup_bars(self, params):
        return 1

    def compute_signals(self, market, params):
        return make_signal_matrix(market.shape, entry=np.ones(market.shape, dtype=np.uint8))


MATRIX_STRATEGY = _EveryBar()
"""


def _frame(turnover_on_last_day: dict[str, float | None]) -> pl.DataFrame:
    """Steady prices with a 4% rise on the last day; turnover only where given for that day."""
    rows = []
    for rank, (symbol, last_turnover) in enumerate(turnover_on_last_day.items()):
        for offset in range(DAYS):
            close = 50.0 + rank + 0.05 * math.sin(offset / 4.0)
            if offset == DAYS - 1:
                close = rows[-1]["close"] * 1.04
            rows.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "date": START + timedelta(days=offset),
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1_000_000.0,
                    "amount": 1e9,
                    "raw_close": close,
                    "raw_high": close,
                    "raw_low": close,
                    "turnover_rate": last_turnover if offset == DAYS - 1 else None,
                    "signal_limit_up": False,
                    "signal_limit_down": False,
                }
            )
    return (
        pl.DataFrame(rows, infer_schema_length=None)
        .with_columns(pl.col("turnover_rate").cast(pl.Float64))
        .sort(["symbol", "date"])
    )


def _market(turnover: dict[str, float | None] | None = None) -> pl.DataFrame:
    """Covered and uncovered stocks, a TDR and an ETF; 3 of the 5 eligible stocks hold turnover."""
    cells = {**dict.fromkeys(COVERED + UNCOVERED + (TDR, ETF)), **TURNOVER, **(turnover or {})}
    return _frame(cells)


@pytest.fixture
def taiwan(tmp_path, monkeypatch):
    """Security master and share-capital files for the symbols above."""
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "taiwan").mkdir()
    master_path = tmp_path / "master_types.parquet"
    pl.DataFrame(
        {
            "symbol": [*COVERED, *UNCOVERED, TDR, ETF],
            "instrument_type": ["stock"] * (len(COVERED) + len(UNCOVERED) + 1) + ["etf"],
        }
    ).write_parquet(master_path)
    monkeypatch.setattr(
        "app.taiwan.universe.get_security_master", lambda: SimpleNamespace(cache_path=master_path)
    )
    monkeypatch.setattr(share_capital_store, "_master_tdr_symbols", lambda: frozenset({TDR}))
    issued = dict.fromkeys([*COVERED, *UNCOVERED, TDR], 100_000_000)
    pl.DataFrame(
        {
            "symbol": list(issued),
            "issued_shares": list(issued.values()),
            "report_date": ["2024-01-01"] * len(issued),
            "retrieved_at": ["2024-01-01T00:00:00"] * len(issued),
        },
        schema=share_capital_store.SCHEMA,
    ).write_parquet(tmp_path / "taiwan" / "share_capital.parquet")
    return tmp_path


# --------------------------------------------------------------------------------------------
# Realtime monitor: the live row is the screened session
# --------------------------------------------------------------------------------------------


def _rule(sid: str) -> dict:
    return monitor_rules.normalize(
        {
            "id": f"{sid}_rule",
            "name": sid,
            "type": "strategy",
            "asset_type": "stock",
            "scope": "all",
            "symbols": [],
            "strategy_id": sid,
            "notify_events": ["pool_entry"],
            "conditions": [],
            "cooldown_seconds": 0,
        }
    )


def _monitor(engine: StrategyEngine, frame: pl.DataFrame, monkeypatch) -> MonitorRuleEngine:
    monkeypatch.setattr("app.strategy.monitor.cn_today", lambda: LAST)
    monitor = MonitorRuleEngine()
    monitor.set_strategy_engine(engine)
    monitor.set_history_loader(lambda target, lookback: frame.filter(pl.col("date") <= target))
    return monitor


@pytest.mark.parametrize(
    ("sid", "expected"),
    [
        ("high_turnover_surge", sorted(COVERED)),
        ("custom_needs_turnover", sorted(COVERED + UNCOVERED + (TDR,))),
    ],
)
def test_the_monitor_screens_a_covered_live_row(taiwan, tmp_path, monkeypatch, sid, expected):
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "custom_needs_turnover.py").write_text(NEEDS_TURNOVER, encoding="utf-8")
    frame = _market()
    monitor = _monitor(StrategyEngine(strategy_dirs=[BUILTIN_DIR, custom]), frame, monkeypatch)
    monitor.set_rules([_rule(sid)])

    monitor.evaluate(frame.filter(pl.col("date") == LAST))

    rows = monitor.latest_strategy_results()[sid]["rows"]
    assert sorted(row["symbol"] for row in rows) == expected


@pytest.mark.parametrize("sid", ["high_turnover_surge", "custom_needs_turnover"])
def test_the_monitor_warns_and_skips_a_strategy_whose_live_row_is_uncovered(
    taiwan, tmp_path, monkeypatch, caplog, sid
):
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "custom_needs_turnover.py").write_text(NEEDS_TURNOVER, encoding="utf-8")
    frame = _market(dict.fromkeys(COVERED[1:]))
    monitor = _monitor(StrategyEngine(strategy_dirs=[BUILTIN_DIR, custom]), frame, monkeypatch)
    monitor.set_rules([_rule(sid)])

    with caplog.at_level(logging.WARNING, logger="app.strategy.monitor"):
        monitor.evaluate(frame.filter(pl.col("date") == LAST))

    assert sid not in monitor.latest_strategy_results()
    expected = f"策略 {sid} 选股执行失败: {LAST.isoformat()} 只有 20% 的股票有換手率資料"
    assert any(record.getMessage().startswith(expected) for record in caplog.records)
