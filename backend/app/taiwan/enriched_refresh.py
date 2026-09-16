"""Publishes Taiwan daily bars into the enriched table the shared engines read.

The indicator pipeline only sources ``kline_daily``, which the Taiwan
deployment never populates, so the monitor engine and the screener would see
an empty table and no signal would ever fire. This service computes the same
enriched frame from ``TaiwanDailyStore`` and writes it to the layout those
engines already scan.

Limit-board signals stay absent: they need per-instrument limit prices that
only the A-share instrument table supplies.
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import date
from pathlib import Path

import polars as pl

from app.indicators.pipeline import compute_enriched
from app.taiwan.daily_store import TaiwanDailyStore

logger = logging.getLogger(__name__)

ENRICHED_DIR_NAME = "kline_daily_enriched"


def _enriched_root() -> Path:
    from app.config import settings

    return Path(settings.data_dir) / ENRICHED_DIR_NAME


class TaiwanEnrichedRefreshService:
    """Computes indicators and signals for Taiwan symbols and publishes them."""

    def __init__(
        self,
        daily_store: TaiwanDailyStore | None = None,
        enriched_root: Path | None = None,
    ) -> None:
        self.daily_store = daily_store or TaiwanDailyStore()
        self._root = enriched_root or _enriched_root()

    @staticmethod
    def _write_partition(partition_dir: Path, df: pl.DataFrame) -> int:
        partition_dir.mkdir(parents=True, exist_ok=True)
        target = partition_dir / "part.parquet"
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(partition_dir), suffix=".parquet")
        try:
            os.close(tmp_fd)
            df.write_parquet(tmp_path)
            Path(tmp_path).replace(target)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        return df.height

    def refresh(self, start: date | None = None, end: date | None = None) -> int:
        """Publish the partitions in the window. Returns rows written.

        Indicators are always computed from the whole stored history, never from a
        truncated warmup: MACD's recursive EMA never fully converges, so a shorter
        window would give a republished day slightly different values from a full
        rebuild, which is enough to flip a borderline cross. The full market costs
        about a second, so there is nothing to save by truncating.
        """
        available = self.daily_store.available_dates()
        if not available:
            logger.info("taiwan enriched refresh skipped: no daily data")
            return 0

        end = end or max(available)
        start = start or min(available)
        raw = self.daily_store.scan_range(None, None, end)
        if raw.is_empty():
            logger.info("taiwan enriched refresh skipped: empty window")
            return 0

        enriched = compute_enriched(raw, factors=pl.DataFrame())
        if enriched.is_empty():
            return 0
        enriched = enriched.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        if enriched.is_empty():
            return 0

        written = 0
        for part_date in enriched["date"].unique().sort().to_list():
            group = enriched.filter(pl.col("date") == part_date)
            written += self._write_partition(
                self._root / f"date={part_date.isoformat()}", group
            )

        logger.info(
            "taiwan enriched refresh: %d rows across %s..%s",
            written, start.isoformat(), end.isoformat(),
        )
        return written

    def refresh_recent(self, days: int = 5) -> int:
        """Republish only the newest partitions, for the daily update job."""
        available = self.daily_store.available_dates()
        if not available:
            return 0
        end = max(available)
        recent = sorted(available)[-days:]
        return self.refresh(start=recent[0], end=end)


def refresh_taiwan_enriched(full: bool = False, repo=None) -> int:
    """Publish the enriched table, then let the shared repository pick it up."""
    service = TaiwanEnrichedRefreshService()
    written = service.refresh() if full else service.refresh_recent()
    if written and repo is not None:
        try:
            repo.rebuild_views()
            repo.refresh_cache()
        except Exception as exc:
            logger.warning("enriched cache refresh failed after publish: %s", exc)
    return written


def main() -> None:
    """Manual entry point for python -m app.taiwan.enriched_refresh."""
    import argparse

    parser = argparse.ArgumentParser(description="Publish the Taiwan enriched table")
    parser.add_argument("--full", action="store_true", help="Rebuild every partition, not just the recent ones")
    parser.add_argument("--days", type=int, default=5, help="How many recent partitions to rebuild")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    service = TaiwanEnrichedRefreshService()
    written = service.refresh() if args.full else service.refresh_recent(args.days)
    print(f"rows written: {written}")


if __name__ == "__main__":
    main()
