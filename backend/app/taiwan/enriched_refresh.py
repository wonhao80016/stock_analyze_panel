"""Publishes Taiwan daily bars into the enriched table the shared engines read.

The indicator pipeline only sources ``kline_daily``, which the Taiwan
deployment never populates, so the monitor engine and the screener would see
an empty table and no signal would ever fire. This service computes the same
enriched frame from ``TaiwanDailyStore`` and writes it to the layout those
engines already scan.

Open, high, low and close are forward-adjusted with the corporate-action factors dated
on or before the newest stored bar, so the newest close equals the stored price;
raw_close, raw_high and raw_low keep the stored prices. A new factor rescales all
history, so whenever the factor set differs from the one last published every
partition is rewritten under an EnrichedPublication, which makes readers fail
instead of mixing scales until the rewrite commits.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import date, datetime
from pathlib import Path

import polars as pl

from app.enriched_generation import EnrichedPublication, enriched_publication_incomplete
from app.indicators.pipeline import compute_enriched, filter_halt_days, with_limit_streak
from app.taiwan.corporate_actions import (
    REF_LIMIT_DOWN,
    REF_LIMIT_UP,
    TaiwanCorporateActionStore,
    adjustment_factors,
    attach_reference_prices,
    today_factors,
)
from app.taiwan.daily_store import REPAIR_WINDOW_PARTITIONS, TaiwanDailyStore, listing_sessions
from app.taiwan.market_rules import ListingSessions, PriceLimitModel
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.share_capital_store import TaiwanShareCapitalStore
from app.taiwan.volume_patterns import add_volume_patterns

logger = logging.getLogger(__name__)

ENRICHED_DIR_NAME = "kline_daily_enriched"
FACTOR_SET_FILE = ".taiwan_enriched_factors.json"


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
        corporate_actions: TaiwanCorporateActionStore | None = None,
    ) -> None:
        self.daily_store = daily_store or TaiwanDailyStore()
        self._root = enriched_root or _enriched_root()
        self.share_capital = share_capital or TaiwanShareCapitalStore()
        self.corporate_actions = corporate_actions or TaiwanCorporateActionStore()

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

        When the factor set through *end* differs from the last published one, or the last
        full publication never committed, the window widens to every stored day: partitions
        outside it would keep the old scale, and only a full publication can commit.
        """
        available = self.daily_store.available_dates()
        if not available:
            logger.info("taiwan enriched refresh skipped: no daily data")
            return 0

        end = end or max(available)
        start = start or min(available)
        events = self.corporate_actions.read(end=max(available))
        if (start > min(available) or end < max(available)) and (
            self.factors_changed(events, end) or self.publication_incomplete()
        ):
            logger.info(
                "taiwan enriched: factors changed or last publication incomplete, republishing every partition"
            )
            start, end = min(available), max(available)
        full = start <= min(available) and end >= max(available)
        factors = adjustment_factors(events, end)

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
        raw = attach_reference_prices(
            filter_halt_days(raw),
            events.filter(pl.col("date") <= end),
            with_limits=True,
        )
        enriched = compute_enriched(raw, factors=factors, instruments=instruments)
        if enriched.is_empty():
            return 0
        enriched = self._add_limit_signals(enriched, listing_sessions(self.daily_store))
        enriched = add_volume_patterns(enriched, available)
        enriched = enriched.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        if enriched.is_empty():
            return 0

        written = (
            self._publish_all(enriched, factors, end) if full else self._publish_partitions(enriched)
        )
        logger.info(
            "taiwan enriched refresh: %d rows across %s..%s%s",
            written, start.isoformat(), end.isoformat(), " (every partition)" if full else "",
        )
        return written

    def _publish_partitions(self, enriched: pl.DataFrame, publication: EnrichedPublication | None = None) -> int:
        written = 0
        for part_date in enriched["date"].unique().sort().to_list():
            group = enriched.filter(pl.col("date") == part_date)
            partition = self._root / f"date={part_date.isoformat()}"
            if publication is None:
                written += self._write_partition(partition, group)
            else:
                publication.write_parquet(group, partition / "part.parquet")
                written += group.height
        return written

    def _publish_all(self, enriched: pl.DataFrame, factors: pl.DataFrame, through: date) -> int:
        """Rewrite every partition as one publication and record its factor set before committing.

        A record that cannot be saved leaves the publication incomplete, so readers never pair
        the new prices with the previous record and apply pending factors twice.
        A commit failing after the record is saved leaves event symbols' cached history
        unadjusted, an ex-rights gap in the daily K, until the next publication commits.
        """
        publication = EnrichedPublication(self._root.parent, "stock", recover=True)
        publication.begin()
        try:
            written = self._publish_partitions(enriched, publication)
            self._record_factor_set(factors, through)
            publication.commit()
        except BaseException:
            publication.abandon()
            raise
        return written

    def publication_incomplete(self) -> bool:
        """Whether the last full publication of this table never committed."""
        return enriched_publication_incomplete(self._root.parent)

    @property
    def factor_set_path(self) -> Path:
        return self._root.parent / FACTOR_SET_FILE

    @staticmethod
    def _fingerprint(factors: pl.DataFrame) -> str:
        digest = hashlib.sha256()
        ordered = factors.sort(["symbol", "trade_date"]).select("symbol", "trade_date", "ex_factor", "volume_factor")
        for row in ordered.iter_rows():
            digest.update(repr(row).encode("utf-8"))
        return digest.hexdigest()

    def _published_fingerprint(self) -> str | None:
        """The factor set of the last full publish; a table never published with factors counts as none."""
        try:
            payload = json.loads(self.factor_set_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._fingerprint(adjustment_factors(pl.DataFrame(), date.min))
        except (OSError, ValueError) as exc:
            logger.warning("taiwan enriched factor record unreadable, republishing everything: %s", exc)
            return None
        return payload.get("fingerprint") if isinstance(payload, dict) else None

    def factors_changed(self, events: pl.DataFrame, through: date) -> bool:
        """Whether the factors dated on or before *through* differ from the last full publish."""
        return self._fingerprint(adjustment_factors(events, through)) != self._published_fingerprint()

    def _record_factor_set(self, factors: pl.DataFrame, through: date) -> None:
        payload = {
            "fingerprint": self._fingerprint(factors),
            "factors": factors.height,
            "through": through.isoformat(),
            "published_at": datetime.now(TAIPEI).isoformat(),
        }
        target = self.factor_set_path
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), prefix=".taiwan_enriched_factors_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream)
            Path(tmp_path).replace(target)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def _limit_profile(self, symbols: list[str], sessions: ListingSessions) -> pl.DataFrame:
        """Per-symbol statutory limit percentage, tick ladder and initial-listing no-limit window."""
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
            window = MarketProfileBridge.ipo_no_limit_window(inst, sessions)
            rows.append({
                "symbol": sym,
                "limit_pct": float(pct),
                "is_etf_tick": inst.instrument_type == "etf",
                "ipo_start": window[0] if window else None,
                "ipo_end": window[1] if window else None,
            })
        return pl.DataFrame(rows, schema={
            "symbol": pl.Utf8, "limit_pct": pl.Float64, "is_etf_tick": pl.Boolean,
            "ipo_start": pl.Date, "ipo_end": pl.Date,
        })

    def _add_limit_signals(self, enriched: pl.DataFrame, sessions: ListingSessions | None = None) -> pl.DataFrame:
        """Derive the Taiwan limit-board signals from the statutory daily limits.

        The shared pipeline skips these because it wants an A-share instrument
        table for the limit prices; Taiwan carries its own rules instead. Limits and
        ticks are raw-scale prices, so the comparison uses raw_close / raw_high / raw_low
        and the previous close moved onto the raw scale; a corporate-action session
        uses the limits the exchange published. The limit streaks are counted from these signals.
        A session inside an initial listing's no-limit window, placed on *sessions* (default: the
        stored calendar), has no limit board.
        """
        cols = ["signal_limit_up", "signal_limit_down", "signal_broken_limit_up", "signal_limit_down_recovery"]
        enriched = enriched.with_columns([
            pl.lit(None, dtype=pl.Float64).alias(c)
            for c in (REF_LIMIT_UP, REF_LIMIT_DOWN) if c not in enriched.columns
        ])
        profile = self._limit_profile(
            enriched["symbol"].unique().to_list(), sessions if sessions is not None else listing_sessions()
        )
        if profile.is_empty():
            out = enriched.with_columns([pl.lit(False).alias(c) for c in cols])
        else:
            out = self._limit_signals(enriched.join(profile, on="symbol", how="left"))
        out = with_limit_streak(out, "signal_limit_up", "consecutive_limit_ups")
        return with_limit_streak(out, "signal_limit_down", "consecutive_limit_downs")

    @staticmethod
    def _limit_signals(out: pl.DataFrame) -> pl.DataFrame:
        ref = pl.col("prev_close") * pl.when(pl.col("close") > 0).then(pl.col("raw_close") / pl.col("close"))
        limit_pct = PriceLimitModel.polars_session_limit_pct(
            pl.col("limit_pct"), pl.col("date"), pl.col("ipo_start"), pl.col("ipo_end")
        )
        limit_up, limit_down = PriceLimitModel.polars_limit_prices(
            ref, limit_pct, pl.col("is_etf_tick").fill_null(False)
        )

        in_ipo_window = PriceLimitModel.polars_in_ipo_window(pl.col("date"), pl.col("ipo_start"), pl.col("ipo_end"))
        official = pl.col(REF_LIMIT_UP).is_not_null() & pl.col(REF_LIMIT_DOWN).is_not_null() & ~in_ipo_window
        close, high, low = pl.col("raw_close"), pl.col("raw_high"), pl.col("raw_low")
        out = out.with_columns([
            pl.when(official).then(pl.col(REF_LIMIT_UP)).otherwise(limit_up).alias("_limit_up"),
            pl.when(official).then(pl.col(REF_LIMIT_DOWN)).otherwise(limit_down).alias("_limit_down"),
        ])
        usable = official | (limit_pct.is_not_null() & (ref > 0))
        return out.with_columns([
            (usable & (close >= pl.col("_limit_up"))).fill_null(False).alias("signal_limit_up"),
            (usable & (close <= pl.col("_limit_down"))).fill_null(False).alias("signal_limit_down"),
            (usable & (high >= pl.col("_limit_up")) & (close < pl.col("_limit_up")))
            .fill_null(False).alias("signal_broken_limit_up"),
            (usable & (low <= pl.col("_limit_down")) & (close > pl.col("_limit_down")))
            .fill_null(False).alias("signal_limit_down_recovery"),
        ]).drop(["limit_pct", "is_etf_tick", "ipo_start", "ipo_end", "_limit_up", "_limit_down"])

    def refresh_recent(self, days: int = 5) -> int:
        """Republish the newest partitions, for the daily update job.

        Starts at stale_since() instead when that is earlier: every day from there
        on was published before its current bars, or not at all. A changed factor set or
        an incomplete last publication turns it into a rewrite of every partition (see refresh).
        """
        available = sorted(self.daily_store.available_dates())
        if not available:
            return 0
        return self.refresh(start=self._recent_start(available, days), end=available[-1])

    def recent_window(self, days: int = 5) -> tuple[date, date] | None:
        """First and last day refresh_recent(days) would republish, or None without stored bars."""
        available = sorted(self.daily_store.available_dates())
        return (self._recent_start(available, days), available[-1]) if available else None

    def _recent_start(self, available: list[date], days: int) -> date:
        start = available[-days:][0]
        stale = self.stale_since(available)
        return stale if stale is not None and stale < start else start

    def stale_since(self, available: list[date] | None = None) -> date | None:
        """Earliest recent day whose enriched partition is missing or older than its bars."""
        if available is None:
            available = sorted(self.daily_store.available_dates())
        for d in available[-REPAIR_WINDOW_PARTITIONS:]:
            if self._published_before_bars(d):
                return d
        return None

    def _published_before_bars(self, d: date) -> bool:
        bars_written = self.daily_store.partition_mtime(d)
        try:
            published = (self._root / f"date={d.isoformat()}" / "part.parquet").stat().st_mtime
        except OSError:
            return True
        return bars_written is not None and bars_written > published


def published_factor_through(data_dir: Path | None = None) -> date | None:
    """Newest session whose events the published enriched scale includes, from the last full publish."""
    path = (Path(data_dir) if data_dir is not None else _enriched_root().parent) / FACTOR_SET_FILE
    try:
        return date.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["through"])
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("taiwan enriched factor record unreadable: %s", exc)
        return None


def unpublished_factors(today: date, fallback_after: date | None = None) -> pl.DataFrame:
    """Per symbol, the factors of events the published enriched scale does not include yet.

    Those are the events after the last full publish's through date and on or before
    *today*; a price quoted today is on their scale, published history is not. Without a
    record the published table carries no factors and *fallback_after* bounds the window.
    Columns: symbol, _today_factor, _today_volume_factor.
    """
    return today_factors(published_factor_through() or fallback_after, today)


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
