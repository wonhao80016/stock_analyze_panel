"""Matrix change fields use the official base the enriched publish uses after an untraded session."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from app.backtest import matrix as matrix_module
from app.backtest.matrix import load_market_data_matrix_from_parquet, matrix_feature
from app.indicators.pipeline import compute_indicators

DAYS = [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28), date(2026, 10, 29)]
GAP = "00625K.TWSE"
UNKNOWN = "2330.TWSE"
TRADED = "2317.TWSE"
CHANGE_FIELDS = ("prev_close", "change_pct", "change_amount", "amplitude")


def _rows(symbol: str, days: list[date], raw: list[float], factor: float, **extra) -> pl.DataFrame:
    adjusted = [value * factor for value in raw]
    count = len(days)
    return pl.DataFrame({
        "symbol": [symbol] * count,
        "date": days,
        "open": adjusted,
        "high": [value * 1.01 for value in adjusted],
        "low": [value * 0.98 for value in adjusted],
        "close": adjusted,
        "volume": [1_000_000.0] * count,
        "raw_close": raw,
        "ref_prev_close": extra.get("ref_prev_close", [None] * count),
        "ref_base_missing": extra.get("ref_base_missing", [False] * count),
        "signal_limit_up": [False] * count,
        "signal_limit_down": [False] * count,
    }, schema_overrides={"ref_prev_close": pl.Float64, "ref_base_missing": pl.Boolean})


def _enriched() -> pl.DataFrame:
    """GAP and UNKNOWN have no row on DAYS[2]; on DAYS[3] GAP has an official base and UNKNOWN none."""
    gap_days = [DAYS[0], DAYS[1], DAYS[3]]
    return pl.concat([
        # The official base 19.40 is below the last close 20.00, so the session is up, not down.
        _rows(GAP, gap_days, [19.80, 20.00, 19.50], 0.9, ref_prev_close=[None, None, 19.40]),
        _rows(UNKNOWN, gap_days, [100.0, 101.0, 99.0], 1.0, ref_base_missing=[False, False, True]),
        _rows(TRADED, DAYS, [50.0, 51.0, 50.5, 52.0], 0.95),
    ])


def _write_partitions(root: Path, frame: pl.DataFrame) -> None:
    for (trade_date,), part in frame.partition_by("date", as_dict=True).items():
        partition = root / f"date={trade_date.isoformat()}"
        partition.mkdir(parents=True, exist_ok=True)
        part.write_parquet(partition / "part.parquet")


def _load(tmp_path: Path, frame: pl.DataFrame, *, use_cache: bool):
    root = tmp_path / "enriched"
    _write_partitions(root, frame)
    return load_market_data_matrix_from_parquet(
        root,
        DAYS[0],
        DAYS[-1],
        field_columns=frozenset({"raw_close"}),
        cache_root=(tmp_path / "cache") if use_cache else None,
    )


def _published(frame: pl.DataFrame, symbol: str, name: str) -> float | None:
    published = compute_indicators(frame, needed=set(CHANGE_FIELDS))
    return published.filter((pl.col("symbol") == symbol) & (pl.col("date") == DAYS[3]))[name].item()


@pytest.mark.parametrize("use_cache", [False, True])
def test_change_fields_after_an_untraded_session_match_enriched(tmp_path, use_cache):
    frame = _enriched()
    market = _load(tmp_path, frame, use_cache=use_cache)
    column = {symbol: index for index, symbol in enumerate(market.symbols)}
    gap, unknown, traded = column[GAP], column[UNKNOWN], column[TRADED]

    for name in CHANGE_FIELDS:
        values = matrix_feature(market, name)
        assert values[3, gap] == pytest.approx(_published(frame, GAP, name), rel=1e-5), name
        assert np.isnan(values[3, unknown]), name
    assert matrix_feature(market, "change_pct")[3, gap] == pytest.approx(19.50 / 19.40 - 1, rel=1e-5)

    close = market.close
    change = matrix_feature(market, "change_pct")
    for row in (1, 2, 3):
        expected = close[row, traded] / close[row - 1, traded] - np.float32(1.0)
        assert change[row, traded] == expected
    assert change[1, gap] == close[1, gap] / close[0, gap] - np.float32(1.0)
    assert change[1, unknown] == close[1, unknown] / close[0, unknown] - np.float32(1.0)


def test_matrix_cache_of_the_previous_loader_version_is_rebuilt_with_the_official_base(
    tmp_path, monkeypatch
):
    frame = _enriched()
    root = tmp_path / "enriched"
    _write_partitions(root, frame)

    def load():
        return load_market_data_matrix_from_parquet(
            root, DAYS[0], DAYS[-1], field_columns=frozenset({"raw_close"}), cache_root=tmp_path / "cache",
        )

    with monkeypatch.context() as patch:
        patch.setattr(matrix_module, "_DIRECT_MATRIX_LOADER_VERSION", 12)
        stale = load()
        stale_name = Path(stale.cache_path).name
        del stale

    market = load()

    assert stale_name.startswith("v12-")
    assert market.cache_status == "built"
    column = market.symbols.index(GAP)
    assert matrix_feature(market, "change_pct")[3, column] == pytest.approx(19.50 / 19.40 - 1, rel=1e-5)
