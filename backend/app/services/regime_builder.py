"""Market regime history reader.

The regime_history table is read by the backtest regime filter.
Nothing in the Taiwan deployment computes it, so it exists only where an earlier build wrote it.
"""
from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

REGIME_DIR = "regime_history"


def regime_path(data_dir: Path) -> Path:
    return data_dir / REGIME_DIR / "part.parquet"


def load_regime_history(data_dir: Path) -> pl.DataFrame:
    """读取全部 regime 时序; 不存在返回空 DataFrame。"""
    p = regime_path(data_dir)
    if not p.exists():
        return pl.DataFrame()
    try:
        return pl.read_parquet(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("load_regime_history failed: %s", e)
        return pl.DataFrame()
