"""POST /api/strategies/run-all runs every strategy it can and reports the ones that fail."""

from __future__ import annotations

import types
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.strategy.engine import StrategyEngine

pytestmark = pytest.mark.usefixtures("deny_network")

AS_OF = date(2024, 3, 1)

STRATEGY = """
import polars as pl

META = {{"id": "{sid}", "name": "{sid}", "limit": 0}}
BASIC_FILTER = {{"enabled": False}}


def filter(df, params):
    {body}
"""


class _Repo:
    def __init__(self, data_dir: Path, current: pl.DataFrame) -> None:
        self.store = types.SimpleNamespace(data_dir=data_dir)
        self._current = current

    def get_enriched_latest_asset(self, asset_type):
        return self._current, AS_OF

    def get_instruments_asset(self, asset_type):
        return pl.DataFrame()

    def get_enriched_history(self, target_date, lookback_days):
        return None


def _engine(tmp_path: Path) -> StrategyEngine:
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir()
    for sid, body in (
        ("fine", "return pl.lit(True)"),
        ("refused", 'raise ValueError("2024-03-01 has no turnover")'),
    ):
        (strategy_dir / f"{sid}.py").write_text(
            STRATEGY.format(sid=sid, body=body), encoding="utf-8"
        )
    engine = StrategyEngine(strategy_dirs=[strategy_dir])
    assert engine.load_errors() == []
    return engine


def test_run_all_returns_the_strategies_that_ran_and_the_failures(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    current = pl.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "name": ["AAA", "BBB"],
            "date": [AS_OF, AS_OF],
            "open": [10.0, 20.0],
            "high": [10.0, 20.0],
            "low": [10.0, 20.0],
            "close": [10.0, 20.0],
            "volume": [1_000.0, 1_000.0],
        }
    )
    monkeypatch.setattr(app.state, "repo", _Repo(tmp_path, current), raising=False)
    monkeypatch.setattr(app.state, "strategy_engine", _engine(tmp_path), raising=False)

    response = TestClient(app).post("/api/strategies/run-all", json={"as_of": AS_OF.isoformat()})

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"] == {"fine": {"total": 2, "as_of": AS_OF.isoformat()}}
    assert payload["failures"] == {"refused": {"error": "2024-03-01 has no turnover"}}
