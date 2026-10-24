from __future__ import annotations

import types
import warnings
from datetime import date

import polars as pl
import pytest

from app.backtest.engine import BacktestEngine, _with_taiwan_limit_flags


def _engine(tmp_path) -> BacktestEngine:
    repo = types.SimpleNamespace(
        store=types.SimpleNamespace(data_dir=tmp_path),
        get_enriched_range=lambda *a, **k: None,
    )
    return BacktestEngine(repo)


def _write_partition(tmp_path, day: date) -> None:
    partition = tmp_path / "kline_daily_enriched" / f"date={day.isoformat()}"
    partition.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "date": [day],
        "open": [10.0],
        "high": [10.0],
        "low": [10.0],
        "close": [10.0],
        "volume": [1_000.0],
    }).write_parquet(partition / "part.parquet")


# B097: the panel scan uses the supported collect API and never turns a failure into no data.

def test_panel_scan_emits_no_deprecation_and_returns_the_rows(tmp_path) -> None:
    _write_partition(tmp_path, date(2024, 1, 2))

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        panel = _engine(tmp_path)._load_panel_inner(
            None, date(2024, 1, 1), date(2024, 1, 3), ["symbol", "date", "close"], "stock",
        )

    assert panel["close"].to_list() == [10.0]


def test_panel_scan_failure_is_raised_instead_of_an_empty_panel(tmp_path) -> None:
    _write_partition(tmp_path, date(2024, 1, 2))
    broken = tmp_path / "kline_daily_enriched" / "date=2024-01-03"
    broken.mkdir()
    (broken / "part.parquet").write_bytes(b"not a parquet file")

    with pytest.raises(pl.exceptions.PolarsError):
        _engine(tmp_path)._load_panel_inner(
            None, date(2024, 1, 1), date(2024, 1, 3), ["symbol", "date", "close"], "stock",
        )


def test_taiwan_limit_flag_symbol_filter_emits_no_deprecation() -> None:
    frame = pl.DataFrame({
        "symbol": ["A", "A"],
        "date": [date(2024, 1, 1), date(2024, 1, 2)],
        "close": [10.0, 10.5],
        "signal_limit_up": [False, None],
    })

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        flagged = _with_taiwan_limit_flags(frame, ["signal_limit_up"])

    assert flagged.height == 2
