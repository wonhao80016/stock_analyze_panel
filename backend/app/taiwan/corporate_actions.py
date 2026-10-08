"""Taiwan corporate-action table: storage, refresh and reference-price lookups.

Storage: <settings.data_dir>/taiwan/corporate_actions/events.parquet, one row per
(symbol, date, kind), where date is the session the official reference price applies
to. Prices are TWD. Each row keeps its provenance as provider, source, source_url,
retrieved_at (Asia/Taipei), trade_date (the event session) and status. The file is
small (about four thousand rows a year) and is rewritten atomically as a whole.

A refetch that returns different figures for a stored (symbol, date, kind) replaces
them and logs the change. Rows are never deleted by a refetch.

fetch_state.json, next to events.parquet, keeps per source table the date range its
successful fetches cover and when the last one ran. Coverage ends no later than the
fetch day, and a failed fetch records nothing. The publish step skips a table whose
coverage already spans its window; the backfill CLI always fetches.

Consumers take the reference price of the session that follows an event: a bar
whose previous bar is dated before the event and whose own date is on or after it.
An event on a day the symbol did not trade therefore sets the base of its next session.

Backfill CLI: python -m app.taiwan.corporate_actions --since 2024-01-01
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from app.taiwan import institutional_margin_refresh
from app.taiwan.enrichment.corporate_actions import (
    ETF_SPLIT,
    EXDIV,
    PAR_CHANGE,
    REDUCTION,
    CorporateAction,
    default_adapters,
)
from app.taiwan.providers.taiwan_values import TAIPEI

logger = logging.getLogger(__name__)

REF_PREV_CLOSE = "ref_prev_close"
REF_LIMIT_UP = "ref_limit_up"
REF_LIMIT_DOWN = "ref_limit_down"

LOOKBACK_DAYS = 10

KEY = ["symbol", "date", "kind"]
VALUE_COLUMNS = ("pre_close", "ref_price", "limit_up", "limit_down", "open_ref", "official_label")

# TWT49U's notes leave out dividends paid together with a capital-return or split
# reduction, and TWTAUU prices those resumptions as (close - dividend - refund) / ratio,
# so the resumption price already carries the dividend. TPEx states no such rule; the
# same order is assumed there. The reduction tables' ex-rights reference column is not
# read: TWTAUU's formula uses it only for a loss-offset reduction paired with a cash
# capital increase, and it is blank in every 2024-2026 row.
_KIND_PRIORITY = {REDUCTION: 0, PAR_CHANGE: 1, ETF_SPLIT: 2, EXDIV: 3}

# Kinds that change the share count, so volume before them is restated in new shares.
SHARE_COUNT_KINDS = frozenset({ETF_SPLIT, PAR_CHANGE, REDUCTION})
# TWTB8U and TWTCAU publish pre_close / ratio rounded to 0.01 but not the ratio itself.
_RATIO_KINDS = frozenset({ETF_SPLIT, PAR_CHANGE})
_REF_PRICE_STEP = 0.01
_RATIO_MAX_DENOMINATOR = 20

FACTOR_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.String,
    "trade_date": pl.Date,
    "ex_factor": pl.Float64,
    "volume_factor": pl.Float64,
}

RETRIEVED_AT_TYPE = pl.Datetime("us", "Asia/Taipei")

_WRITE_LOCK = threading.Lock()


def corporate_action_schema() -> dict[str, pl.DataType]:
    return {
        "symbol": pl.String,
        "date": pl.Date,
        "kind": pl.String,
        "pre_close": pl.Float64,
        "ref_price": pl.Float64,
        "limit_up": pl.Float64,
        "limit_down": pl.Float64,
        "open_ref": pl.Float64,
        "ex_factor": pl.Float64,
        "official_label": pl.String,
        "provider": pl.String,
        "source": pl.String,
        "source_url": pl.String,
        "retrieved_at": RETRIEVED_AT_TYPE,
        "trade_date": pl.Date,
        "status": pl.String,
    }


def _conform(df: pl.DataFrame) -> pl.DataFrame:
    return df.select([pl.col(name).cast(dtype) for name, dtype in corporate_action_schema().items()])


def actions_frame(actions: Iterable[CorporateAction]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "symbol": a.symbol,
                "date": a.date,
                "kind": a.kind,
                "pre_close": a.pre_close,
                "ref_price": a.ref_price,
                "limit_up": a.limit_up,
                "limit_down": a.limit_down,
                "open_ref": a.open_ref,
                "ex_factor": a.ex_factor,
                "official_label": a.official_label,
                "provider": a.meta.provider,
                "source": a.meta.source,
                "source_url": a.meta.source_url,
                "retrieved_at": a.meta.fetched_at,
                "trade_date": a.meta.trade_date,
                "status": a.meta.status,
            }
            for a in actions
        ],
        schema=corporate_action_schema(),
    )


class TaiwanCorporateActionStore:
    """Parquet persistence for the official corporate-action reference prices."""

    FILE_NAME = "events.parquet"
    FETCH_STATE_FILE = "fetch_state.json"

    def __init__(self, data_dir: Path | None = None) -> None:
        if data_dir is None:
            from app.taiwan.data_root import taiwan_data_root

            data_dir = taiwan_data_root() / "corporate_actions"
        self._data_dir = Path(data_dir)

    @property
    def path(self) -> Path:
        return self._data_dir / self.FILE_NAME

    @property
    def fetch_state_path(self) -> Path:
        return self._data_dir / self.FETCH_STATE_FILE

    def fetch_state(self) -> dict[str, Any]:
        """Per source: covered_from, covered_through and fetched_at of its successful fetches."""
        try:
            state = json.loads(self.fetch_state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("Corporate action fetch state unreadable, every table will be fetched: %s", exc)
            return {}
        return state if isinstance(state, dict) else {}

    def covers(self, source: str, start: date, end: date, state: dict[str, Any] | None = None) -> bool:
        coverage = _coverage((self.fetch_state() if state is None else state).get(source))
        return coverage is not None and coverage[0] <= start and end <= coverage[1]

    def record_fetch(self, sources: Iterable[str], start: date, through: date, fetched_at: datetime) -> None:
        """Mark [start, through] fetched for *sources*, joined to their coverage when the ranges touch."""
        with _WRITE_LOCK:
            state = self.fetch_state()
            for source in sources:
                lo, hi = start, through
                prev = _coverage(state.get(source))
                if prev is not None and prev[0] <= through + timedelta(days=1) and start <= prev[1] + timedelta(days=1):
                    lo, hi = min(prev[0], start), max(prev[1], through)
                state[source] = {
                    "covered_from": lo.isoformat(),
                    "covered_through": hi.isoformat(),
                    "fetched_at": fetched_at.isoformat(),
                }
            text = json.dumps(state, indent=2, sort_keys=True)
            self._atomic_write(self.fetch_state_path, lambda tmp: Path(tmp).write_text(text, encoding="utf-8"))

    def read(
        self,
        start: date | None = None,
        end: date | None = None,
        symbols: list[str] | None = None,
    ) -> pl.DataFrame:
        if not self.path.exists():
            return pl.DataFrame(schema=corporate_action_schema())
        df = _conform(pl.read_parquet(self.path))
        if start is not None:
            df = df.filter(pl.col("date") >= start)
        if end is not None:
            df = df.filter(pl.col("date") <= end)
        if symbols is not None:
            df = df.filter(pl.col("symbol").is_in(symbols))
        return df

    def upsert(self, incoming: pl.DataFrame) -> dict[str, int]:
        """Insert new events and replace restated ones; returns inserted/updated/unchanged counts."""
        counts = {"inserted": 0, "updated": 0, "unchanged": 0}
        incoming = _conform(incoming).unique(subset=KEY, keep="last", maintain_order=True)
        if incoming.is_empty():
            return counts
        with _WRITE_LOCK:
            existing = self.read()
            paired = incoming.join(
                existing.select([*KEY, *VALUE_COLUMNS]), on=KEY, how="left", suffix="_stored"
            )
            is_new = pl.col("ref_price_stored").is_null()
            restated = pl.any_horizontal([pl.col(c).ne_missing(pl.col(f"{c}_stored")) for c in VALUE_COLUMNS])
            updated = paired.filter(~is_new & restated)
            counts["inserted"] = paired.filter(is_new).height
            counts["updated"] = updated.height
            counts["unchanged"] = incoming.height - counts["inserted"] - counts["updated"]
            for row in updated.iter_rows(named=True):
                changes = ", ".join(
                    f"{c} {row[f'{c}_stored']!r}->{row[c]!r}"
                    for c in VALUE_COLUMNS
                    if row[c] != row[f"{c}_stored"]
                )
                logger.warning(
                    "Corporate action %s %s %s restated by %s: %s",
                    row["symbol"], row["date"], row["kind"], row["source"], changes,
                )
            if counts["inserted"] or counts["updated"]:
                merged = pl.concat([existing.join(incoming.select(KEY), on=KEY, how="anti"), incoming])
                self._write(merged.sort(["date", "symbol", "kind"]))
        return counts

    def _write(self, df: pl.DataFrame) -> None:
        self._atomic_write(self.path, lambda tmp: df.write_parquet(tmp, compression="snappy"))

    def _atomic_write(self, target: Path, write: Callable[[str], Any]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=f"{target.stem}_", suffix=".tmp", dir=str(self._data_dir))
        os.close(fd)
        try:
            write(tmp_path)
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


def _coverage(entry: Any) -> tuple[date, date] | None:
    try:
        return date.fromisoformat(entry["covered_from"]), date.fromisoformat(entry["covered_through"])
    except (KeyError, TypeError, ValueError):
        return None


def read_events_or_empty(
    store: TaiwanCorporateActionStore,
    start: date | None = None,
    end: date | None = None,
    symbols: list[str] | None = None,
) -> pl.DataFrame:
    """Events for a consumer that must keep working without them; an unreadable table is logged."""
    try:
        return store.read(start=start, end=end, symbols=symbols)
    except Exception as exc:
        logger.warning("Corporate action table unreadable, reference prices skipped: %s", exc)
        return pl.DataFrame(schema=corporate_action_schema())


def _one_event_per_session(events: pl.DataFrame) -> pl.DataFrame:
    return (
        events.with_columns(
            pl.col("kind").replace_strict(_KIND_PRIORITY, default=len(_KIND_PRIORITY)).alias("_priority")
        )
        .sort(["symbol", "date", "_priority"], descending=[False, False, True])
        .unique(subset=["symbol", "date"], keep="last", maintain_order=True)
        .select(["symbol", "date", "ref_price", "limit_up", "limit_down"])
    )


def attach_reference_prices(
    daily: pl.DataFrame,
    events: pl.DataFrame,
    *,
    with_limits: bool = False,
) -> pl.DataFrame:
    """Add ref_prev_close (and, with_limits, the official limit prices) to each bar an event opens.

    *daily* must already exclude halted sessions. Bars without an event get nulls. The
    result is sorted by symbol and date.
    """
    out_cols = [REF_PREV_CLOSE, *([REF_LIMIT_UP, REF_LIMIT_DOWN] if with_limits else [])]
    base = daily.drop([c for c in out_cols if c in daily.columns]).sort(["symbol", "date"])
    per_session = _one_event_per_session(events) if not events.is_empty() else pl.DataFrame()
    if base.is_empty() or per_session.is_empty():
        return base.with_columns([pl.lit(None, dtype=pl.Float64).alias(c) for c in out_cols])

    per_session = per_session.rename({"date": "_event_date"}).sort(["symbol", "_event_date"])
    joined = (
        base.with_columns(pl.col("date").shift(1).over("symbol").alias("_prev_bar_date"))
        .join_asof(
            per_session,
            left_on="date",
            right_on="_event_date",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        )
    )
    opens_session = pl.col("_event_date").is_not_null() & pl.when(pl.col("_prev_bar_date").is_null()).then(
        pl.col("_event_date") == pl.col("date")
    ).otherwise(pl.col("_event_date") > pl.col("_prev_bar_date"))
    picked = {REF_PREV_CLOSE: "ref_price", REF_LIMIT_UP: "limit_up", REF_LIMIT_DOWN: "limit_down"}
    return joined.with_columns([
        pl.when(opens_session).then(pl.col(picked[c])).otherwise(None).cast(pl.Float64).alias(c)
        for c in out_cols
    ]).drop(["_prev_bar_date", "_event_date", "ref_price", "limit_up", "limit_down"])


def reference_prices(events: pl.DataFrame, after: date | None, through: date) -> pl.DataFrame:
    """Per symbol, the event that sets the base of the session on *through*.

    *after* is the symbol's previous session; the latest event dated after it and on or
    before *through* applies. Without a previous session only an event on *through* counts.
    Columns: symbol, ref_price, limit_up, limit_down.
    """
    schema = {"symbol": pl.String, "ref_price": pl.Float64, "limit_up": pl.Float64, "limit_down": pl.Float64}
    if events.is_empty():
        return pl.DataFrame(schema=schema)
    window = pl.col("date") <= through
    window = window & ((pl.col("date") > after) if after is not None else (pl.col("date") == through))
    picked = _one_event_per_session(events.filter(window))
    if picked.is_empty():
        return pl.DataFrame(schema=schema)
    return picked.sort(["symbol", "date"]).group_by("symbol", maintain_order=True).last().select(list(schema))


def official_ratio(pre_close: float, ref_price: float) -> float:
    """The share ratio behind a split or par-value reference price.

    The exchange publishes ref_price = pre_close / ratio rounded to 0.01. The ratio is taken
    as the fraction with the smallest denominator (up to 20) that lands within 0.01 of
    ref_price, so 188.65 / 47.16 gives 4, not 4.00021; its relative distance from
    pre_close / ref_price is at most 0.01 / (ref_price - 0.01). Without such a fraction the
    unrounded quotient is returned.
    """
    lo = pre_close / (ref_price + _REF_PRICE_STEP)
    hi = pre_close / (ref_price - _REF_PRICE_STEP) if ref_price > _REF_PRICE_STEP else float("inf")
    for denominator in range(1, _RATIO_MAX_DENOMINATOR + 1):
        numerator = math.ceil(lo * denominator - 1e-9)
        if numerator > 0 and numerator / denominator <= hi:
            return numerator / denominator
    return pre_close / ref_price


def adjustment_factors(events: pl.DataFrame, through: date) -> pl.DataFrame:
    """Forward-adjustment factors, one per symbol and event session, in pipeline._apply_adj_factor form.

    Only events dated on or before *through* count; a later one would scale the newest bar
    too. ex_factor divides every earlier price: the official ratio for splits and par-value
    changes, pre_close / ref_price for dividends and reductions. volume_factor multiplies
    every earlier volume: ex_factor for share-count kinds, 1 for dividends. For a reduction
    that also returns cash the price factor carries the refund, so the restated volume is
    approximate.
    """
    if events.is_empty():
        return pl.DataFrame(schema=FACTOR_SCHEMA)
    usable = events.filter(
        (pl.col("date") <= through) & (pl.col("pre_close") > 0) & (pl.col("ref_price") > 0)
    )
    if usable.is_empty():
        return pl.DataFrame(schema=FACTOR_SCHEMA)
    picked = (
        usable.with_columns(
            pl.col("kind").replace_strict(_KIND_PRIORITY, default=len(_KIND_PRIORITY)).alias("_priority")
        )
        .sort(["symbol", "date", "_priority"])
        .unique(subset=["symbol", "date"], keep="first", maintain_order=True)
    )
    rows = []
    for row in picked.iter_rows(named=True):
        pre, ref = row["pre_close"], row["ref_price"]
        factor = official_ratio(pre, ref) if row["kind"] in _RATIO_KINDS else pre / ref
        rows.append({
            "symbol": row["symbol"],
            "trade_date": row["date"],
            "ex_factor": factor,
            "volume_factor": factor if row["kind"] in SHARE_COUNT_KINDS else 1.0,
        })
    return pl.DataFrame(rows, schema=FACTOR_SCHEMA).sort(["symbol", "trade_date"])


def forward_adjusted(daily: pl.DataFrame, events: pl.DataFrame, through: date) -> pl.DataFrame:
    """Raw daily bars forward-adjusted like the enriched publish, with the events on or before *through*.

    The bar on *through* keeps its stored prices; earlier bars move onto its scale.
    """
    from app.indicators.pipeline import _apply_adj_factor

    factors = adjustment_factors(events, through)
    if daily.is_empty() or factors.is_empty():
        return daily
    adjusted = _apply_adj_factor(daily, factors)
    return adjusted if "ex_rights" in daily.columns else adjusted.drop("ex_rights")


def factors_between(events: pl.DataFrame, after: date | None, through: date) -> pl.DataFrame:
    """Per symbol, the product of the factors of events dated after *after* and on or before *through*.

    A price from the session *after* divided by ex_factor is on the scale of *through*. Without
    *after* only events on *through* count. Columns: symbol, ex_factor, volume_factor.
    """
    window = events.filter(pl.col("date") > after) if after is not None else events.filter(pl.col("date") == through)
    return (
        adjustment_factors(window, through)
        .group_by("symbol")
        .agg(pl.col("ex_factor").product(), pl.col("volume_factor").product())
        .sort("symbol")
    )


_pending_factor_cache: dict[tuple[str, int, date | None, date], pl.DataFrame] = {}
_PENDING_FACTOR_CACHE_LOCK = threading.Lock()
_PENDING_FACTOR_CACHE_SIZE = 8


def pending_factors(
    after: date | None,
    through: date,
    store: TaiwanCorporateActionStore | None = None,
) -> pl.DataFrame:
    """The factors of the events dated after *after* and on or before *through*, one row per event.

    A price on a scale that ends on *after* reaches the scale of *through* once divided by
    the factors of the events after its own session. Without *after* only events on
    *through* count; a later event is never included. Cached per table version, since
    intraday callers ask every few seconds. Columns: symbol, trade_date, ex_factor, volume_factor.
    """
    store = store or TaiwanCorporateActionStore()
    try:
        version = store.path.stat().st_mtime_ns
    except OSError:
        return pl.DataFrame(schema=FACTOR_SCHEMA)
    key = (str(store.path), version, after, through)
    with _PENDING_FACTOR_CACHE_LOCK:
        cached = _pending_factor_cache.get(key)
    if cached is not None:
        return cached
    events = read_events_or_empty(store, start=after + timedelta(days=1) if after else through, end=through)
    result = adjustment_factors(events, through)
    with _PENDING_FACTOR_CACHE_LOCK:
        if len(_pending_factor_cache) >= _PENDING_FACTOR_CACHE_SIZE:
            _pending_factor_cache.clear()
        _pending_factor_cache[key] = result
    return result


def year_segments(start: date, end: date) -> list[tuple[date, date]]:
    """Split [start, end] at calendar-year boundaries, one query per table per year."""
    segments: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        seg_end = min(end, date(cur.year, 12, 31))
        segments.append((cur, seg_end))
        cur = seg_end + timedelta(days=1)
    return segments


class TaiwanCorporateActionRefreshService:
    """Fetches the official corporate-action tables into TaiwanCorporateActionStore."""

    def __init__(
        self,
        store: TaiwanCorporateActionStore | None = None,
        adapters: list[Any] | None = None,
        fetch_json: Callable[[str], dict[str, Any]] | None = None,
        today: Callable[[], date] | None = None,
    ) -> None:
        self._store = store or TaiwanCorporateActionStore()
        self._adapters = adapters if adapters is not None else default_adapters()
        self._fetch_json = fetch_json or (
            lambda url: institutional_margin_refresh._fetch_json_with_retry(url, timeout=15.0)
        )
        self._today = today or _taipei_today

    def refresh_range(self, start: date, end: date, *, skip_covered: bool = False) -> dict[str, Any]:
        """Fetch every table for [start, end]. A failing table is reported and the others are stored.

        With skip_covered, a table whose recorded coverage already spans [start, end] is not asked.
        """
        if end < start:
            raise ValueError(f"end {end} is before start {start}")
        state = self._store.fetch_state() if skip_covered else {}
        adapters = [
            a for a in self._adapters if not (skip_covered and self._store.covers(a.SOURCE, start, end, state))
        ]
        stats: dict[str, Any] = {
            "start": str(start),
            "end": str(end),
            "requests": 0,
            "rows_fetched": 0,
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped": [a.SOURCE for a in self._adapters if a not in adapters],
            "failed": [],
        }
        fetched_at = datetime.now(TAIPEI)
        actions: list[CorporateAction] = []
        for seg_start, seg_end in year_segments(start, end):
            for adapter in adapters:
                time.sleep(institutional_margin_refresh.INTER_REQUEST_DELAY_SEC)
                url = adapter.build_url(seg_start, seg_end)
                stats["requests"] += 1
                try:
                    actions.extend(adapter.parse_payload(self._fetch_json(url), seg_start, seg_end, url))
                except Exception as exc:
                    logger.warning(
                        "Corporate action fetch failed for %s %s..%s: %s", adapter.SOURCE, seg_start, seg_end, exc
                    )
                    stats["failed"].append({
                        "source": adapter.SOURCE,
                        "start": str(seg_start),
                        "end": str(seg_end),
                        "error": str(exc),
                    })
        stats["rows_fetched"] = len(actions)
        if actions:
            stats.update(self._store.upsert(actions_frame(actions)))
        failed = {f["source"] for f in stats["failed"]}
        succeeded = [a.SOURCE for a in adapters if a.SOURCE not in failed]
        through = min(end, self._today())
        if succeeded and through >= start:
            try:
                self._store.record_fetch(succeeded, start, through, fetched_at)
            except OSError as exc:
                logger.warning("Corporate action fetch state not saved, the next publish fetches again: %s", exc)
        return stats

    def refresh_publish_window(self, first_published: date, target: date) -> dict[str, Any]:
        """LOOKBACK_DAYS before the first session a publish rewrites, through its target session."""
        return self.refresh_range(first_published - timedelta(days=LOOKBACK_DAYS), target, skip_covered=True)


def _taipei_today() -> date:
    from app.taiwan.realtime.calendar import taipei_today

    return taipei_today()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Backfill the Taiwan corporate-action reference prices")
    parser.add_argument("--since", required=True, help="First event date YYYY-MM-DD")
    parser.add_argument("--until", help="Last event date YYYY-MM-DD (defaults to today)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until) if args.until else _taipei_today()
    stats = TaiwanCorporateActionRefreshService().refresh_range(since, until)
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    if stats.get("failed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
