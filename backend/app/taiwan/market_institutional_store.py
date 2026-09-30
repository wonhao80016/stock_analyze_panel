"""Market-wide institutional buy/sell amounts per exchange and official category.

Partition layout:
    <settings.data_dir>/taiwan/market_institutional/date=YYYY-MM-DD/part.parquet
One row per (exchange, official_name) for the day. Amounts are in TWD. Each row carries
the SourceMeta provenance flattened to provider, source, source_url, retrieved_at
(Asia/Taipei), trade_date and status, plus the adapter's has_discrepancy flag.
A partition always holds both TWSE and TPEx; writes replace the whole day.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from datetime import date
from pathlib import Path

import polars as pl

from app.taiwan.daily_store import is_provisional_mtime
from app.taiwan.enrichment.market_institutional import MarketInstitutionalAmount

RETRIEVED_AT_TYPE = pl.Datetime("us", "Asia/Taipei")


def market_institutional_schema() -> dict[str, pl.DataType]:
    return {
        "date": pl.Date,
        "exchange": pl.String,
        "category": pl.String,
        "official_name": pl.String,
        "buy_amount": pl.Int64,
        "sell_amount": pl.Int64,
        "net_amount": pl.Int64,
        "has_discrepancy": pl.Boolean,
        "provider": pl.String,
        "source": pl.String,
        "source_url": pl.String,
        "retrieved_at": RETRIEVED_AT_TYPE,
        "trade_date": pl.Date,
        "status": pl.String,
    }


def amounts_frame(d: date, amounts: list[MarketInstitutionalAmount]) -> pl.DataFrame:
    """Flatten parsed summary rows and their SourceMeta into the store schema."""
    return pl.DataFrame(
        [
            {
                "date": d,
                "exchange": a.exchange,
                "category": a.category,
                "official_name": a.official_name,
                "buy_amount": a.buy_amount,
                "sell_amount": a.sell_amount,
                "net_amount": a.net_amount,
                "has_discrepancy": a.has_discrepancy,
                "provider": a.meta.provider,
                "source": a.meta.source,
                "source_url": a.meta.source_url,
                "retrieved_at": a.meta.fetched_at,
                "trade_date": a.meta.trade_date,
                "status": a.meta.status,
            }
            for a in amounts
        ],
        schema=market_institutional_schema(),
    )


class TaiwanMarketInstitutionalStore:
    """Parquet persistence for the official market-wide institutional amount summaries."""

    def __init__(self, data_dir: Path | None = None) -> None:
        if data_dir is None:
            from app.taiwan.data_root import taiwan_data_root

            data_dir = taiwan_data_root() / "market_institutional"
        self._data_dir = Path(data_dir)
        self._write_lock = threading.Lock()

    def _partition(self, d: date) -> Path:
        return self._data_dir / f"date={d.isoformat()}" / "part.parquet"

    def write_day(self, d: date, df: pl.DataFrame) -> int:
        """Atomically replace the partition for *d* with *df*."""
        if df.is_empty():
            return 0
        schema = market_institutional_schema()
        df = (
            df.with_columns(pl.lit(d).alias("date"))
            .select([pl.col(name).cast(dtype) for name, dtype in schema.items()])
            .sort(["exchange", "official_name"])
        )
        target = self._partition(d)
        with self._write_lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix=f"part_{d.isoformat()}_", suffix=".tmp", dir=str(target.parent))
            os.close(fd)
            try:
                df.write_parquet(tmp_path, compression="snappy")
                for attempt in range(3):
                    try:
                        os.replace(tmp_path, target)
                        break
                    except OSError:
                        if attempt == 2:
                            raise
                        time.sleep(0.05 * (attempt + 1))
            except BaseException:
                Path(tmp_path).unlink(missing_ok=True)
                raise
        return df.height

    def read_day(self, d: date) -> pl.DataFrame:
        path = self._partition(d)
        if not path.exists():
            return pl.DataFrame(schema=market_institutional_schema())
        return pl.read_parquet(path)

    def available_dates(self) -> list[date]:
        dates: list[date] = []
        for p in self._data_dir.glob("date=*"):
            if not (p / "part.parquet").exists():
                continue
            try:
                dates.append(date.fromisoformat(p.name.split("=", 1)[1]))
            except (IndexError, ValueError):
                continue
        return sorted(dates)

    def provisional_dates(self, dates: list[date]) -> list[date]:
        """Return the stored dates among *dates* whose partition was last written on the day itself."""
        provisional: list[date] = []
        for d in dates:
            try:
                mtime = self._partition(d).stat().st_mtime
            except OSError:
                continue
            if is_provisional_mtime(d, mtime):
                provisional.append(d)
        return sorted(provisional)

    def latest_date(self) -> date | None:
        dates = self.available_dates()
        return dates[-1] if dates else None
