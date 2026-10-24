from __future__ import annotations

import json
import os
from datetime import date

import numpy as np
import polars as pl
import pytest

from app.backtest import matrix as matrix_module
from app.backtest.matrix import (
    RealtimeMarketDataMatrix,
    _prune_matrix_disk_cache,
    load_market_data_matrix_from_parquet,
)


def _write_enriched(root) -> None:
    rows = {
        date(2024, 1, 2): [
            # Zero volume with a moving price, a bar with a missing open, and an ordinary bar.
            ("A.SZ", 10.0, 10.5, 9.8, 10.2, 0.0),
            ("B.SZ", None, 10.5, 9.8, 10.2, 1_000.0),
            ("C.SZ", 10.0, 10.5, 9.8, 10.2, 1_000.0),
        ],
        date(2024, 1, 3): [
            ("A.SZ", 10.0, 10.0, 10.0, 10.0, 0.0),
            ("B.SZ", 10.0, 10.0, 10.0, 10.0, float("nan")),
            ("C.SZ", 10.0, 10.5, 9.8, 10.2, 1_000.0),
        ],
    }
    for day, bars in rows.items():
        partition = root / f"date={day.isoformat()}"
        partition.mkdir(parents=True)
        pl.DataFrame(
            {
                "symbol": [bar[0] for bar in bars],
                "date": [day] * len(bars),
                "open": [bar[1] for bar in bars],
                "high": [bar[2] for bar in bars],
                "low": [bar[3] for bar in bars],
                "close": [bar[4] for bar in bars],
                "raw_close": [bar[4] for bar in bars],
                "volume": [bar[5] for bar in bars],
                "signal_limit_up": [False] * len(bars),
                "signal_limit_down": [False] * len(bars),
            },
            schema_overrides={"open": pl.Float64, "volume": pl.Float64},
        ).write_parquet(partition / "part.parquet")


# B106: the disk-cache build and the in-memory build agree on tradable bars.

def test_disk_cache_and_memory_builds_share_the_tradable_rule(tmp_path) -> None:
    root = tmp_path / "kline_daily_enriched"
    _write_enriched(root)
    load = dict(field_columns=set(), symbols=None)

    memory = load_market_data_matrix_from_parquet(root, date(2024, 1, 2), date(2024, 1, 3), **load)
    disk = load_market_data_matrix_from_parquet(
        root, date(2024, 1, 2), date(2024, 1, 3), cache_root=tmp_path / "cache", **load,
    )

    assert disk.cache_status == "built"
    np.testing.assert_array_equal(np.asarray(disk.tradable), np.asarray(memory.tradable))


# B107: the axis index files and directories of other loader versions are pruned.

AXIS_INDEX_KEEP = 16

def _cache_dir(root, name: str, version: int) -> None:
    path = root / name
    path.mkdir()
    (path / "manifest.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    (path / "matrix.bin").write_bytes(b"0" * 16)


def test_prune_removes_stale_axis_indexes_and_other_loader_versions(tmp_path) -> None:
    root = tmp_path / "cache"
    root.mkdir()
    current = matrix_module._DIRECT_MATRIX_LOADER_VERSION
    _cache_dir(root, f"v{current}-keep", current)
    _cache_dir(root, f"v{current - 1}-old", current - 1)
    keep = root / f"v{current}-keep"
    (root / ".axes-v0-stale.json").write_text("{}", encoding="utf-8")
    axis_version = matrix_module._MATRIX_AXIS_INDEX_VERSION
    for index in range(AXIS_INDEX_KEEP + 5):
        path = root / f".axes-v{axis_version}-{index:04d}.json"
        path.write_text("{}", encoding="utf-8")
        os.utime(path, ns=(index * 1_000_000_000, index * 1_000_000_000))

    _prune_matrix_disk_cache(root, keep=keep, max_bytes=1 << 30)

    assert keep.exists()
    assert not (root / f"v{current - 1}-old").exists()
    assert not (root / ".axes-v0-stale.json").exists()
    remaining = sorted(path.name for path in root.glob(".axes-*.json"))
    assert len(remaining) == AXIS_INDEX_KEEP
    assert remaining[-1] == f".axes-v{axis_version}-{AXIS_INDEX_KEEP + 4:04d}.json"


# B108: the live buffer refuses to carry its history into another session.

def _history() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["A.SZ", "A.SZ"],
        "date": [date(2024, 1, 1), date(2024, 1, 2)],
        "open": [10.0, 10.5],
        "high": [10.2, 10.7],
        "low": [9.8, 10.3],
        "close": [10.1, 10.6],
        "raw_close": [10.1, 10.6],
        "volume": [1_000.0, 1_100.0],
    })


def test_live_update_from_a_new_session_requires_a_rebuild() -> None:
    live = RealtimeMarketDataMatrix(_history(), field_columns={"raw_close"})
    quote = _history().filter(pl.col("date") == date(2024, 1, 2)).with_columns(
        pl.lit(date(2024, 1, 3)).alias("date"),
    )

    with pytest.raises(ValueError, match="rebuild"):
        live.update(quote)
    assert live.snapshot().shape == (2, 1)


def test_live_update_of_the_current_session_still_overwrites_the_last_row() -> None:
    live = RealtimeMarketDataMatrix(_history(), field_columns={"raw_close"})
    quote = _history().filter(pl.col("date") == date(2024, 1, 2)).with_columns(
        pl.lit(11.0).alias("close"),
    )

    live.update(quote)

    assert float(live.snapshot().close[-1, 0]) == pytest.approx(11.0)
