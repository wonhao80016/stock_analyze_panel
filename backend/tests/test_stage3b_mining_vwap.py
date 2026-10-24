from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.backtest.engine import BacktestEngine
from app.backtest.factor import FactorBacktestService
from app.backtest.mining_runtime import _load_compact_factor_panel

_FIRST = date(2024, 6, 3)
_EX_DIVIDEND_INDEX = 5
_ADJUSTMENT = 0.95


def _ex_dividend_rows(*, with_raw_close: bool) -> pl.DataFrame:
    """One Taiwan symbol whose forward-adjusted close shrinks every session before the ex-date."""
    rows = []
    for index in range(10):
        raw_close = 100.0 if index < _EX_DIVIDEND_INDEX else 95.0
        close = raw_close * (_ADJUSTMENT if index < _EX_DIVIDEND_INDEX else 1.0)
        volume = 1_000.0 + index
        row = {
            "symbol": "2330.TWSE",
            "date": _FIRST + timedelta(days=index),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": volume,
            "amount": raw_close * volume,
            "turnover_rate": 1.0,
        }
        if with_raw_close:
            row["raw_close"] = raw_close
        rows.append(row)
    return pl.DataFrame(rows)


class _ColumnSelectingEngine:
    """Return only the requested columns the stored data actually has, like the enriched loader."""

    def __init__(self, panel: pl.DataFrame) -> None:
        self.panel = panel
        self.requested: list[str] = []

    def load_panel(self, _symbols, _start, _end, columns=None, **_kwargs):
        self.requested = list(columns or [])
        return self.panel.select([name for name in self.requested if name in self.panel.columns])


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        symbols=None,
        start=_FIRST,
        end=_FIRST + timedelta(days=9),
        asset_type="stock",
    )


def test_mining_vwap_bias_uses_raw_close_across_an_ex_dividend_date() -> None:
    engine = _ColumnSelectingEngine(_ex_dividend_rows(with_raw_close=True))

    compact = _load_compact_factor_panel(
        FactorBacktestService(engine),
        _config(),
        ("vwap_bias",),
        expected_generation="generation",
        cancel_check=None,
    )

    assert "raw_close" in engine.requested
    assert compact.height == 10
    assert compact.get_column("vwap_bias").to_list() == pytest.approx([0.0] * 10, abs=1e-6)


def test_mining_panel_loads_enriched_partitions_written_before_raw_close(tmp_path) -> None:
    legacy = _ex_dividend_rows(with_raw_close=False)
    partition_root = tmp_path / "kline_daily_enriched"
    for day in legacy.partition_by("date"):
        partition = partition_root / f"date={day.item(0, 'date').isoformat()}"
        partition.mkdir(parents=True)
        day.write_parquet(partition / "part.parquet")
    repo = SimpleNamespace(
        store=SimpleNamespace(data_dir=tmp_path),
        get_matrix_data_generation=lambda _asset_type="stock": "generation",
    )

    compact = _load_compact_factor_panel(
        FactorBacktestService(BacktestEngine(repo)),
        _config(),
        ("vwap_bias",),
        expected_generation="generation",
        cancel_check=None,
    )

    assert compact.height == 10
    assert compact.get_column("vwap_bias").null_count() == 10
