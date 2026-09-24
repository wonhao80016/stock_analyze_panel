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
from app.taiwan.daily_store import REPAIR_WINDOW_PARTITIONS, TaiwanDailyStore
from app.taiwan.share_capital_store import TaiwanShareCapitalStore

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
        share_capital: TaiwanShareCapitalStore | None = None,
    ) -> None:
        self.daily_store = daily_store or TaiwanDailyStore()
        self._root = enriched_root or _enriched_root()
        self.share_capital = share_capital or TaiwanShareCapitalStore()

    @staticmethod
    def _write_partition(partition_dir: Path, df: pl.DataFrame) -> int:
        partition_dir.mkdir(parents=True, exist_ok=True)
        target = partition_dir / "part.parquet"
        # The suffix must not end in .parquet: readers glob **/*.parquet.
        tmp_fd, tmp_path = tempfile.mkstemp(dir=str(partition_dir), suffix=".parquet.tmp")
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

        # Issued shares are the turnover denominator; without them the column is
        # absent entirely and any strategy scoring on turnover_rate cannot run.
        # compute_enriched nulls it for every Taiwan row that is not today, so the
        # current count never reaches a historical date.
        instruments = self.share_capital.as_instruments()
        if instruments.is_empty():
            logger.warning(
                "taiwan enriched refresh: no share capital on disk, turnover_rate will be null"
            )
        enriched = compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)
        if enriched.is_empty():
            return 0
        enriched = self._add_limit_signals(enriched)
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

    @staticmethod
    def _tick_size_expr(price: pl.Expr, is_etf: pl.Expr) -> pl.Expr:
        """Taiwan tick ladders, mirroring TickSizeModel.get_tick_size."""
        etf_tick = pl.when(price < 50.0).then(0.01).otherwise(0.05)
        stock_tick = (
            pl.when(price < 10.0).then(0.01)
            .when(price < 50.0).then(0.05)
            .when(price < 100.0).then(0.10)
            .when(price < 500.0).then(0.50)
            .when(price < 1000.0).then(1.00)
            .otherwise(5.00)
        )
        return pl.when(is_etf).then(etf_tick).otherwise(stock_tick)

    def _limit_profile(self, symbols: list[str]) -> pl.DataFrame:
        """Per-symbol statutory limit percentage and tick ladder."""
        from app.taiwan.universe import get_security_master
        from app.taiwan.universe.models import MarketProfileBridge

        master = get_security_master()
        rows = []
        for sym in symbols:
            inst = master.get_instrument(sym)
            if inst is None:
                continue
            try:
                pct = MarketProfileBridge.get_price_limit_pct(inst)
            except ValueError:
                # Unconfirmed regulatory profile: leave the limits undefined.
                continue
            if pct is None:
                continue
            rows.append({
                "symbol": sym,
                "limit_pct": float(pct),
                "is_etf_tick": inst.instrument_type == "etf",
            })
        if not rows:
            return pl.DataFrame(schema={"symbol": pl.Utf8, "limit_pct": pl.Float64, "is_etf_tick": pl.Boolean})
        return pl.DataFrame(rows)

    def _add_limit_signals(self, enriched: pl.DataFrame) -> pl.DataFrame:
        """Derive the Taiwan limit-board signals from the statutory daily limits.

        The shared pipeline skips these because it wants an A-share instrument
        table for the limit prices; Taiwan carries its own rules instead.
        """
        cols = ["signal_limit_up", "signal_limit_down", "signal_broken_limit_up", "signal_limit_down_recovery"]
        profile = self._limit_profile(enriched["symbol"].unique().to_list())
        if profile.is_empty():
            return enriched.with_columns([pl.lit(False).alias(c) for c in cols])

        out = enriched.join(profile, on="symbol", how="left")
        ref = pl.col("prev_close")
        up_raw = ref * (1.0 + pl.col("limit_pct"))
        down_raw = pl.max_horizontal(pl.lit(0.01), ref * (1.0 - pl.col("limit_pct")))
        up_tick = self._tick_size_expr(up_raw, pl.col("is_etf_tick").fill_null(False))
        down_tick = self._tick_size_expr(down_raw, pl.col("is_etf_tick").fill_null(False))
        limit_up = ((up_raw / up_tick + 1e-9).floor() * up_tick).round(4)
        limit_down = ((down_raw / down_tick - 1e-9).ceil() * down_tick).round(4)

        out = out.with_columns([
            limit_up.alias("_limit_up"),
            limit_down.alias("_limit_down"),
        ])
        usable = pl.col("limit_pct").is_not_null() & pl.col("prev_close").is_not_null()
        return out.with_columns([
            (usable & (pl.col("close") >= pl.col("_limit_up"))).fill_null(False).alias("signal_limit_up"),
            (usable & (pl.col("close") <= pl.col("_limit_down"))).fill_null(False).alias("signal_limit_down"),
            (usable & (pl.col("high") >= pl.col("_limit_up")) & (pl.col("close") < pl.col("_limit_up")))
            .fill_null(False).alias("signal_broken_limit_up"),
            (usable & (pl.col("low") <= pl.col("_limit_down")) & (pl.col("close") > pl.col("_limit_down")))
            .fill_null(False).alias("signal_limit_down_recovery"),
        ]).drop(["limit_pct", "is_etf_tick", "_limit_up", "_limit_down"])

    def refresh_recent(self, days: int = 5) -> int:
        """Republish the newest partitions, for the daily update job.

        Reaches back to the earliest recent day whose bars were rewritten after its
        enriched partition, or that has no enriched partition, and republishes from
        there: every later day was computed from the old bars.
        """
        available = sorted(self.daily_store.available_dates())
        if not available:
            return 0
        start = available[-days:][0]
        for d in available[-REPAIR_WINDOW_PARTITIONS:]:
            if d >= start:
                break
            if self._published_before_bars(d):
                start = d
                break
        return self.refresh(start=start, end=available[-1])

    def _published_before_bars(self, d: date) -> bool:
        bars_written = self.daily_store.partition_mtime(d)
        try:
            published = (self._root / f"date={d.isoformat()}" / "part.parquet").stat().st_mtime
        except OSError:
            return True
        return bars_written is not None and bars_written > published


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
