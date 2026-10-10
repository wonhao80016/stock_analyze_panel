"""Selection and the realtime monitor refuse an unscreenable session as the backtest does (stage 4, D3).

The API builds a whole-market context and narrows it to the requested pool afterwards, so the
framework judges the screened session on the pool and the strategy's asset scope, never on the
whole market, with the backtest's eligibility, NaN rule and threshold.
"""

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

NO_ELIGIBLE = f"{LAST.isoformat()} 所選範圍內沒有可計算換手率的股票"
UNCOVERED_POOL = f"{LAST.isoformat()} 只有 0% 的股票有換手率資料"

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


class _Repo:
    """The enriched caches the screener service reads first, holding the whole market."""

    def __init__(self, data_dir: Path, frame: pl.DataFrame, as_of: date) -> None:
        self.store = SimpleNamespace(data_dir=data_dir)
        self._frame = frame.filter(pl.col("date") <= as_of)
        self._as_of = as_of

    def get_enriched_latest_asset(self, asset_type):
        return self._frame.filter(pl.col("date") == self._as_of), self._as_of

    def get_enriched_history(self, target_date, lookback_days):
        return self._frame.filter(pl.col("date") <= target_date)

    def get_instruments_asset(self, asset_type):
        return pl.DataFrame()


@pytest.fixture
def client(taiwan, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    def serve(frame: pl.DataFrame, as_of: date = LAST, strategy_dirs=None) -> TestClient:
        engine = StrategyEngine(strategy_dirs=strategy_dirs or [BUILTIN_DIR])
        monkeypatch.setattr(app.state, "repo", _Repo(taiwan, frame, as_of), raising=False)
        monkeypatch.setattr(app.state, "strategy_engine", engine, raising=False)
        return TestClient(app)

    return serve


def _run(client, pool=None, as_of: date = LAST):
    body = {"strategy_id": "high_turnover_surge", "as_of": as_of.isoformat()}
    if pool is not None:
        body["pool"] = list(pool)
    return client.post("/api/strategies/run", json=body)


def _run_preset(client, pool=None, as_of: date = LAST):
    body = {"strategy_id": "high_turnover_surge", "as_of": as_of.isoformat()}
    if pool is not None:
        body["pool"] = list(pool)
    return client.post("/api/screener/run_preset", json=body)


def _picks(response) -> list[str]:
    assert response.status_code == 200, response.text
    return sorted(row["symbol"] for row in response.json()["rows"])


@pytest.mark.parametrize("run", [_run, _run_preset], ids=["run", "run_preset"])
def test_a_tdr_only_pool_is_refused_although_the_market_is_covered(client, run):
    response = run(client(_market()), pool=[TDR])

    assert response.status_code == 400
    assert response.json()["detail"].startswith(NO_ELIGIBLE)


@pytest.mark.parametrize("run", [_run, _run_preset], ids=["run", "run_preset"])
def test_an_etf_and_stock_pool_counts_only_the_stock(client, run):
    served = client(_market())

    covered = run(served, pool=[ETF, COVERED[0]])
    uncovered = run(served, pool=[ETF, UNCOVERED[0]])

    assert _picks(covered) == [COVERED[0]]
    assert uncovered.status_code == 400
    assert uncovered.json()["detail"].startswith(UNCOVERED_POOL)


def test_a_pool_of_stocks_without_turnover_is_refused_on_a_covered_market(client):
    response = _run(client(_market()), pool=UNCOVERED)

    assert response.status_code == 400
    assert response.json()["detail"].startswith(UNCOVERED_POOL)


@pytest.mark.parametrize("run", [_run, _run_preset], ids=["run", "run_preset"])
def test_the_whole_market_is_judged_on_its_eligible_stocks(client, run):
    covered = run(client(_market()))
    thin = run(client(_market(dict.fromkeys(COVERED[1:]))))

    assert _picks(covered) == sorted(COVERED)
    assert thin.status_code == 400
    assert thin.json()["detail"].startswith(f"{LAST.isoformat()} 只有 20% 的股票有換手率資料")


def test_run_all_reports_the_refusal_as_a_failure_and_runs_the_rest(client):
    earlier = LAST - timedelta(days=1)

    response = client(_market(), as_of=earlier).post(
        "/api/strategies/run-all", json={"as_of": earlier.isoformat()}
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["failures"]["high_turnover_surge"]["error"].startswith(
        f"{earlier.isoformat()} 只有 0% 的股票有換手率資料"
    )
    assert "trend_breakout" in payload["results"]


def test_a_strategy_declaring_coverage_fields_is_checked_by_the_framework(client, tmp_path):
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "custom_needs_turnover.py").write_text(NEEDS_TURNOVER, encoding="utf-8")
    served = client(_market(), strategy_dirs=[BUILTIN_DIR, custom])

    def run(pool):
        return served.post(
            "/api/strategies/run",
            json={"strategy_id": "custom_needs_turnover", "as_of": LAST.isoformat(), "pool": pool},
        )

    assert _picks(run([COVERED[0], UNCOVERED[0]])) == [COVERED[0], UNCOVERED[0]]
    refused = run([TDR])
    assert refused.status_code == 400
    assert refused.json()["detail"].startswith(NO_ELIGIBLE)


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
