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
instead of mixing scales until the rewrite commits. A changed price-limit rule of a symbol
with rows outside the refreshed window, a historical partition whose schema differs
from the current output, or indicators published under another INDICATOR_DEFINITION_VERSION
rewrite every partition the same way.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from pathlib import Path

import polars as pl

from app.enriched_generation import (
    EnrichedGenerationUnavailableError,
    EnrichedPublication,
    _exclusive_generation_lock,
    enriched_publication_incomplete,
)
from app.indicators.pipeline import (
    INDICATOR_DEFINITION_VERSION,
    compute_enriched,
    filter_halt_days,
    inject_late_custom_signals,
    limit_down_recovery,
    with_limit_streak,
)
from app.taiwan.corporate_actions import (
    REF_LIMIT_DOWN,
    REF_LIMIT_UP,
    REF_PREV_CLOSE,
    TaiwanCorporateActionStore,
    adjustment_factors,
    attach_reference_prices,
)
from app.taiwan.daily_store import REPAIR_WINDOW_PARTITIONS, TaiwanDailyStore, listing_sessions
from app.taiwan.enriched_reader import _enriched_root
from app.taiwan.market_rules import ListingSessions, PriceLimitModel
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.session_bases import TaiwanSessionBaseStore, official_bases_of
from app.taiwan.share_capital_store import TaiwanShareCapitalStore
from app.taiwan.volume_patterns import add_volume_patterns
from app.volume_units import turnover_rate_pct_expr

logger = logging.getLogger(__name__)

FACTOR_SET_FILE = ".taiwan_enriched_factors.json"
LIMIT_RULES_KEY = "limit_rules"
INDICATOR_VERSION_KEY = "indicator_version"
NO_LIMIT_RULE = "none"
OPEN_WINDOW = "open"
LOGGED_SYMBOLS = 10
PUBLISHED_TURNOVER = "_published_turnover_rate"
SESSION_TURNOVER = "_session_turnover_rate"
REF_BASE_MISSING = "ref_base_missing"


class TaiwanEnrichedRefreshService:
    """Computes indicators and signals for Taiwan symbols and publishes them."""

    def __init__(
        self,
        daily_store: TaiwanDailyStore | None = None,
        enriched_root: Path | None = None,
        share_capital: TaiwanShareCapitalStore | None = None,
        corporate_actions: TaiwanCorporateActionStore | None = None,
        session_bases: TaiwanSessionBaseStore | None = None,
    ) -> None:
        self.daily_store = daily_store or TaiwanDailyStore()
        self._root = enriched_root or _enriched_root()
        self.share_capital = share_capital or TaiwanShareCapitalStore()
        self.corporate_actions = corporate_actions or TaiwanCorporateActionStore()
        self.session_bases = session_bases or TaiwanSessionBaseStore()

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

        When the factor set through *end* or the indicator definitions differ from the last full
        publish, a symbol with rows outside the window changed its price-limit rule, the last full
        publication never committed, or a partition outside the window has another schema than this
        run's output, the window widens to every stored day: partitions outside it would keep the
        old scale, values, limits or columns, and only a full publication can commit.

        Turnover needs the share count of its own day. The newest session uses the current count;
        an earlier day divides its current stored volume by the denominators saved for it
        (TaiwanShareCapitalStore.session_denominators), and a day without them keeps the
        turnover_rate it was published with.
        """
        available = self.daily_store.available_dates()
        if not available:
            logger.info("taiwan enriched refresh skipped: no daily data")
            return 0

        end = end or max(available)
        start = start or min(available)
        events = self.corporate_actions.read(end=max(available))
        sessions = listing_sessions(self.daily_store)
        stored_symbols = sorted(sessions.first_sessions)
        limits = self._limit_profile(stored_symbols, sessions)
        unlisted = self._missing_from_master(stored_symbols)
        limit_rules = self._limit_rules(limits, sorted(set(stored_symbols) - set(unlisted)))
        rewritten_since = start if end >= max(available) else None
        if (start > min(available) or end < max(available)) and (
            self.factors_changed(events, end)
            or self.publication_incomplete()
            or self.indicator_definitions_changed()
            or self.limit_rules_changed(
                limit_rules, sessions.first_sessions, rewritten_since, unlisted
            )
        ):
            logger.info(
                "taiwan enriched: factors, indicator definitions or limit rules changed, or last "
                "publication incomplete, republishing every partition"
            )
            start, end = min(available), max(available)
        full = start <= min(available) and end >= max(available)
        factors = adjustment_factors(events, end)

        raw = self.daily_store.scan_range(None, None, end)
        if raw.is_empty():
            logger.info("taiwan enriched refresh skipped: empty window")
            return 0

        instruments = self.share_capital.as_instruments()
        if instruments.is_empty():
            logger.warning(
                "taiwan enriched refresh: no share capital on disk, turnover_rate will be null"
            )
        newest = max(available)
        enriched = compute_taiwan_enriched(
            raw,
            events,
            end,
            trading_days=available,
            limits=limits,
            instruments=instruments,
            today=newest,
            session_bases=self.session_bases,
            restate_turnover=lambda frame: with_session_turnover(
                frame, raw, newest=newest, share_capital=self.share_capital, enriched_root=self._root
            ),
        )
        if enriched.is_empty():
            return 0
        if not full and self._history_schema_differs(enriched, start, end):
            logger.info(
                "taiwan enriched: a historical partition has another schema, republishing every partition"
            )
            if end < max(available):
                return self.refresh(min(available), max(available))
            start, full = min(available), True
        enriched = enriched.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        if enriched.is_empty():
            return 0

        if full:
            published_rules = {**limit_rules, **dict.fromkeys(unlisted, NO_LIMIT_RULE)}
            written = self._publish_all(enriched, factors, end, published_rules)
        else:
            try:
                written = self._publish_window(enriched)
            except Exception as exc:
                if not self.publication_incomplete():
                    raise
                logger.warning(
                    "taiwan enriched: window publish failed midway, republishing every partition once: %s", str(exc)
                )
                written = None
            if written is None:
                # Retry outside the except block and log only the message: anything holding the traceback
                # keeps the failed publication registered as active, and the retry would refuse to start.
                return self.refresh(min(available), max(available))
            self._save_limit_rules(limit_rules, keep=unlisted)
        logger.info(
            "taiwan enriched refresh: %d rows across %s..%s%s",
            written, start.isoformat(), end.isoformat(), " (every partition)" if full else "",
        )
        return written

    def _publish_partitions(self, enriched: pl.DataFrame, publication: EnrichedPublication) -> int:
        written = 0
        for part_date in enriched["date"].unique().sort().to_list():
            group = enriched.filter(pl.col("date") == part_date)
            publication.write_parquet(group, self._root / f"date={part_date.isoformat()}" / "part.parquet")
            written += group.height
        return written

    def _publish_window(self, enriched: pl.DataFrame) -> int:
        """Rewrite the partitions of *enriched* as one publication, which replaces the generation on commit.

        A write failing midway leaves the publication incomplete, so readers fail instead of serving a
        half-replaced window until a full republish (refresh retries one at once) commits.
        """
        publication = EnrichedPublication(self._root.parent, "stock")
        publication.begin()
        try:
            written = self._publish_partitions(enriched, publication)
            publication.commit()
        except BaseException:
            publication.abandon()
            raise
        return written

    def _publish_all(
        self,
        enriched: pl.DataFrame,
        factors: pl.DataFrame,
        through: date,
        limit_rules: dict[str, str],
    ) -> int:
        """Rewrite every partition as one publication and record its factor set, indicator definition
        version and limit rules before committing.

        A record that cannot be saved leaves the publication incomplete, so readers never pair
        the new prices with the previous record and apply pending factors twice.
        A commit failing after the record is saved leaves event symbols' cached history
        unadjusted, an ex-rights gap in the daily K, until the next publication commits.
        """
        recovering = self.publication_incomplete()
        publication = EnrichedPublication(self._root.parent, "stock", recover=True)
        publication.begin()
        try:
            if recovering:
                # Partitions of the unfinished publication may already differ, so abandoning must not restore its generation.
                publication.mark_changed()
            written = self._publish_partitions(enriched, publication)
            self._record_factor_set(factors, through)
            self._record_limit_rules(limit_rules)
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

    def indicator_definitions_changed(self) -> bool:
        """Whether the last full publish computed indicators under another definition version.

        No record, an unreadable one or one without a version counts as changed: those rows were
        published by an unknown or earlier release.
        """
        payload = self._read_record()
        return payload is None or payload.get(INDICATOR_VERSION_KEY) != INDICATOR_DEFINITION_VERSION

    def _record_factor_set(self, factors: pl.DataFrame, through: date) -> None:
        self._write_record(
            {
                "fingerprint": self._fingerprint(factors),
                "factors": factors.height,
                "through": through.isoformat(),
                INDICATOR_VERSION_KEY: INDICATOR_DEFINITION_VERSION,
                "published_at": datetime.now(TAIPEI).isoformat(),
            }
        )

    @staticmethod
    def _limit_rules(profile: pl.DataFrame, symbols: list[str]) -> dict[str, str]:
        """Per symbol, a key of the limit inputs that change only with its rule; NO_LIMIT_RULE without a board.

        The initial-listing window's last session is OPEN_WINDOW until five sessions are stored.
        """
        keys = {}
        for row in profile.iter_rows(named=True):
            ipo_start, ipo_end = row["ipo_start"], row["ipo_end"]
            if ipo_end is None:
                window_end = "-"
            else:
                window_end = OPEN_WINDOW if ipo_end == date.max else ipo_end.isoformat()
            keys[row["symbol"]] = "|".join(
                (
                    repr(row["limit_pct"]),
                    "etf" if row["is_etf_tick"] else "stock",
                    ipo_start.isoformat() if ipo_start is not None else "-",
                    window_end,
                )
            )
        return {sym: keys.get(sym, NO_LIMIT_RULE) for sym in symbols}

    @staticmethod
    def _rule_changed(published: object, current: str) -> bool:
        """Whether *current* would change rows published under *published*; a malformed one always does.

        Rows published under an open initial-listing window all lie inside it, so its closing
        on the fifth session changes none of them.
        """
        if not isinstance(published, str):
            return True
        if published == current:
            return False
        head, _, last = published.rpartition("|")
        return not (last == OPEN_WINDOW and current.rpartition("|")[0] == head)

    @staticmethod
    def _missing_from_master(symbols: list[str]) -> list[str]:
        from app.taiwan.universe import get_security_master

        master = get_security_master()
        return [sym for sym in symbols if master.get_instrument(sym) is None]

    def _read_record(self) -> dict | None:
        try:
            payload = json.loads(self.factor_set_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def limit_rules_changed(
        self,
        rules: dict[str, str],
        first_sessions: Mapping[str, date],
        rewritten_since: date | None,
        unlisted: list[str] | tuple[str, ...] = (),
    ) -> bool:
        """Whether a symbol with rows before *rewritten_since* (None: any symbol) changed its limit rule.

        A symbol of *rules* missing from the record counts as changed once it has such rows: its
        rule was never saved. A record saved before limit rules existed adopts *rules*, and
        NO_LIMIT_RULE for *unlisted*, as its baseline without a rebuild; no record or an
        unreadable one leaves the decision to the factor check.
        """
        payload = self._read_record()
        if payload is None:
            return False
        published = payload.get(LIMIT_RULES_KEY)
        if not isinstance(published, dict):
            logger.info(
                "taiwan enriched: factor record has no limit rules, "
                "adopting the current limit rules as baseline without republishing"
            )
            self._save_limit_rules({**rules, **dict.fromkeys(unlisted, NO_LIMIT_RULE)})
            return False
        changed = sorted(
            sym
            for sym, key in rules.items()
            if (sym not in published or self._rule_changed(published[sym], key))
            and (rewritten_since is None or first_sessions[sym] < rewritten_since)
        )
        if changed:
            logger.info(
                "taiwan enriched: limit rules changed for %d symbols with published history: %s%s",
                len(changed),
                ", ".join(changed[:LOGGED_SYMBOLS]),
                " ..." if len(changed) > LOGGED_SYMBOLS else "",
            )
        return bool(changed)

    def _record_limit_rules(self, rules: dict[str, str]) -> None:
        payload = json.loads(self.factor_set_path.read_text(encoding="utf-8"))
        self._write_record({**payload, LIMIT_RULES_KEY: rules})

    def _save_limit_rules(
        self, rules: dict[str, str], keep: list[str] | tuple[str, ...] = ()
    ) -> None:
        """Merge *rules* and the published rules of *keep* into the record outside any publication.

        Best effort: a symbol left unsaved counts as changed once it has rows before the window.
        """
        try:
            with _exclusive_generation_lock(self._root.parent, "stock"):
                if self.publication_incomplete():
                    return
                payload = self._read_record()
                if payload is None:
                    return
                published = payload.get(LIMIT_RULES_KEY)
                published = published if isinstance(published, dict) else {}
                merged = {**{sym: published[sym] for sym in keep if sym in published}, **rules}
                if merged != published:
                    self._write_record({**payload, LIMIT_RULES_KEY: merged})
        except (OSError, EnrichedGenerationUnavailableError) as exc:
            logger.warning("taiwan enriched: limit rules not saved: %s", exc)

    def _history_schema_differs(self, enriched: pl.DataFrame, start: date, end: date) -> bool:
        """Whether a published partition outside [start, end] has another schema than *enriched*.

        Reads only the parquet footers. Only days *enriched* covers count, so a full publish
        always clears the condition.
        """
        expected = dict(enriched.schema)
        days = enriched.select(pl.col("date").unique().sort()).to_series()
        for d in days.filter((days < start) | (days > end)).to_list():
            path = self._root / f"date={d.isoformat()}" / "part.parquet"
            try:
                schema = pl.read_parquet_schema(path)
            except FileNotFoundError:
                continue
            except (OSError, pl.exceptions.PolarsError) as exc:
                logger.warning("taiwan enriched partition %s unreadable: %s", path.parent.name, exc)
                return True
            if dict(schema) != expected:
                return True
        return False

    def _write_record(self, payload: dict) -> None:
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
        return limit_profile(symbols, sessions)

    def _add_limit_signals(
        self,
        enriched: pl.DataFrame,
        sessions: ListingSessions | None = None,
        profile: pl.DataFrame | None = None,
    ) -> pl.DataFrame:
        """add_taiwan_limit_signals with *profile* (default: _limit_profile on *sessions*, the stored calendar)."""
        if profile is None:
            profile = self._limit_profile(
                enriched["symbol"].unique().to_list(),
                sessions if sessions is not None else listing_sessions(),
            )
        return add_taiwan_limit_signals(enriched, profile)

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
        unknown = _base_unknown(out)
        flags = {
            "signal_limit_up": usable & (close >= pl.col("_limit_up")),
            "signal_limit_down": usable & (close <= pl.col("_limit_down")),
            "signal_broken_limit_up": usable & (high >= pl.col("_limit_up")) & (close < pl.col("_limit_up")),
            "signal_limit_down_recovery": usable & limit_down_recovery(low, close, pl.col("_limit_down"), ref),
        }
        return out.with_columns([
            pl.when(unknown).then(None).otherwise(flag.fill_null(False)).alias(name)
            for name, flag in flags.items()
        ]).drop(["limit_pct", "is_etf_tick", "ipo_start", "ipo_end", "_limit_up", "_limit_down"])

    def refresh_recent(self, days: int = 5) -> int:
        """Republish the newest partitions, for the daily update job.

        Starts at stale_since() instead when that is earlier: every day from there
        on was published before its current bars, or not at all. A changed factor set or limit
        rule, an incomplete last publication or a historical partition with another schema turns
        it into a rewrite of every partition (see refresh).
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


def compute_taiwan_enriched(
    raw: pl.DataFrame,
    events: pl.DataFrame,
    end: date,
    trading_days: Iterable[date],
    limits: pl.DataFrame,
    instruments: pl.DataFrame,
    *,
    today: date,
    session_bases: TaiwanSessionBaseStore | pl.DataFrame | None = None,
    restate_turnover: Callable[[pl.DataFrame], pl.DataFrame] | None = None,
) -> pl.DataFrame:
    """Every enriched row the publish writes for the raw daily bars *raw* dated on or before *end*.

    Prices are forward-adjusted with the *events* dated on or before *end*; *trading_days* is the
    market calendar, *limits* a limit_profile frame and *instruments* the share-capital frame.
    Issued shares are the turnover denominator; without them turnover_rate stays null, so the
    schema never depends on the share-capital file. Only rows dated *today*, the newest stored
    session rather than the wall-clock date, get it: the current count never reaches an earlier
    session, and a republish on a holiday or after midnight keeps the newest one.
    With *session_bases*, a store or a frame of official bases, a session after an untraded one
    takes its official base (with_official_bases); without it such a session keeps the last traded
    close as its base and ref_base_missing is false throughout.
    *restate_turnover* gives the rows before *today* their own session's turnover_rate
    (with_session_turnover); custom signals reading the limit streaks or turnover see the final values.
    """
    events = events.filter(pl.col("date") <= end)
    raw = attach_reference_prices(filter_halt_days(raw), events, with_limits=True)
    if session_bases is not None:
        raw = with_official_bases(raw, trading_days, limits, session_bases, events)
    else:
        raw = raw.with_columns(pl.lit(False).alias(REF_BASE_MISSING))
    enriched = compute_enriched(
        raw,
        factors=adjustment_factors(events, end),
        instruments=instruments,
        today=today,
        late_custom_signals=False,
    )
    if enriched.is_empty():
        return enriched
    if "turnover_rate" not in enriched.columns:
        enriched = enriched.with_columns(pl.lit(None, dtype=pl.Float64).alias("turnover_rate"))
    enriched = add_taiwan_limit_signals(enriched, limits)
    if restate_turnover is not None:
        enriched = restate_turnover(enriched)
    enriched = inject_late_custom_signals(enriched)
    return add_volume_patterns(enriched, trading_days)


def with_official_bases(
    bars: pl.DataFrame,
    trading_days: Iterable[date],
    limits: pl.DataFrame,
    store: TaiwanSessionBaseStore | pl.DataFrame,
    events: pl.DataFrame,
) -> pl.DataFrame:
    """*bars* (attach_reference_prices output on *events*) with the official base on each session
    after an untraded one.

    Such a session's previous bar is older than its previous session in *trading_days*, and the
    exchange, not the last traded close, sets its base. A session with the reference price of an
    event dated on it, a symbol's first bar and a session inside the no-limit initial-listing
    window of *limits* keep their ref_* columns. Any other such session takes the official
    ref_price, limit_up and limit_down of *store*. Without an official ref_price, a session whose
    reference comes from an event dated on an earlier untraded session keeps it; otherwise its
    ref_* columns stay null and ref_base_missing is true. ref_base_missing is false on every other row.
    A frame with official_bases_of columns, one row per pair, may stand in for *store*; a session
    whose pair it lacks is then left as if its previous session had traded.
    """
    calendar = sorted(set(trading_days))
    previous_session = pl.DataFrame(
        {"date": calendar, "_previous_session": [None, *calendar[:-1]] if calendar else []},
        schema={"date": pl.Date, "_previous_session": pl.Date},
    )
    windows = limits.select(
        "symbol", pl.col("ipo_start").alias("_ipo_start"), pl.col("ipo_end").alias("_ipo_end")
    )
    in_ipo_window = PriceLimitModel.polars_in_ipo_window(
        pl.col("date"), pl.col("_ipo_start"), pl.col("_ipo_end")
    )
    own_events = events.select("symbol", "date").unique().with_columns(pl.lit(True).alias("_own_event"))
    carried_event = pl.col(REF_PREV_CLOSE).is_not_null() & pl.col("_own_event").is_null()
    framed = (
        bars.sort(["symbol", "date"])
        .with_columns(pl.col("date").shift(1).over("symbol").alias("_previous_bar"))
        .join(previous_session, on="date", how="left", maintain_order="left")
        .join(windows, on="symbol", how="left", maintain_order="left")
        .join(own_events, on=["symbol", "date"], how="left", maintain_order="left")
        .with_columns(
            (
                (pl.col("_previous_bar") < pl.col("_previous_session"))
                & (pl.col(REF_PREV_CLOSE).is_null() | carried_event)
                & ~in_ipo_window
            ).fill_null(False).alias("_after_untraded"),
            carried_event.alias("_carried_event"),
        )
        .drop("_previous_bar", "_previous_session", "_ipo_start", "_ipo_end", "_own_event")
    )
    if isinstance(store, pl.DataFrame):
        covered = store.select("symbol", pl.col("trade_date").alias("date"), pl.lit(True).alias("_covered"))
        framed = (
            framed.join(covered, on=["symbol", "date"], how="left", maintain_order="left")
            .with_columns((pl.col("_after_untraded") & pl.col("_covered").fill_null(False)).alias("_after_untraded"))
            .drop("_covered")
        )
    gaps = framed.filter(pl.col("_after_untraded"))
    if gaps.is_empty():
        return framed.drop("_after_untraded", "_carried_event").with_columns(
            pl.lit(False).alias(REF_BASE_MISSING)
        )
    if isinstance(store, pl.DataFrame):
        official = store
    else:
        official = official_bases_of(
            gaps.select("symbol", pl.col("date").alias("trade_date")), store=store, sessions=calendar
        )
    official = official.select(
        "symbol",
        pl.col("trade_date").alias("date"),
        pl.col("ref_price").alias("_official_ref"),
        pl.col("limit_up").alias("_official_up"),
        pl.col("limit_down").alias("_official_down"),
    )
    after_untraded = pl.col("_after_untraded")
    known = after_untraded & pl.col("_official_ref").is_not_null()
    return (
        framed.join(official, on=["symbol", "date"], how="left", maintain_order="left")
        .with_columns(
            pl.when(known).then(pl.col("_official_ref")).otherwise(pl.col(REF_PREV_CLOSE)).alias(REF_PREV_CLOSE),
            pl.when(known).then(pl.col("_official_up")).otherwise(pl.col(REF_LIMIT_UP)).alias(REF_LIMIT_UP),
            pl.when(known).then(pl.col("_official_down")).otherwise(pl.col(REF_LIMIT_DOWN)).alias(REF_LIMIT_DOWN),
            (after_untraded & ~known & ~pl.col("_carried_event")).alias(REF_BASE_MISSING),
        )
        .drop("_after_untraded", "_carried_event", "_official_ref", "_official_up", "_official_down")
    )


def with_published_bases(
    bars: pl.DataFrame,
    events: pl.DataFrame,
    *,
    daily_store: TaiwanDailyStore | None = None,
    session_bases: TaiwanSessionBaseStore | None = None,
) -> pl.DataFrame:
    """The traded stored *bars* with the reference prices the publish gives them: those of *events*,
    then with_official_bases on the calendar of *daily_store* (default: the default store)."""
    bars = attach_reference_prices(filter_halt_days(bars), events, with_limits=True)
    sessions = listing_sessions(daily_store)
    limits = limit_profile(bars["symbol"].unique().to_list(), sessions)
    store = session_bases or TaiwanSessionBaseStore()
    return with_official_bases(bars, sessions.trading_dates, limits, store, events)


def with_session_turnover(
    enriched: pl.DataFrame,
    raw: pl.DataFrame,
    *,
    newest: date,
    share_capital: TaiwanShareCapitalStore,
    enriched_root: Path,
) -> pl.DataFrame:
    """*enriched* with the turnover_rate of each row dated before *newest* from its own session.

    A day with denominators saved in *share_capital* divides the stored volume of *raw*, the daily
    bars before any volume_factor restatement, by them; any other day takes the turnover_rate its
    partition under *enriched_root* holds.
    """
    days = enriched.filter(pl.col("date") < newest)["date"].unique().sort().to_list()
    denominators = share_capital.session_denominators(days)
    saved_days = set(denominators["date"].to_list())
    published = _published_turnover(enriched_root, [d for d in days if d not in saved_days])
    if not published.is_empty():
        enriched = (
            enriched.join(published, on=["symbol", "date"], how="left", maintain_order="left")
            .with_columns(pl.coalesce("turnover_rate", PUBLISHED_TURNOVER).alias("turnover_rate"))
            .drop(PUBLISHED_TURNOVER)
        )
    if not saved_days:
        return enriched
    session = (
        raw.select("symbol", pl.col("date").cast(pl.Date), "volume")
        .join(denominators, on=["symbol", "date"], how="inner")
        .select(
            "symbol",
            "date",
            pl.when(pl.col("float_shares") > 0)
            .then(turnover_rate_pct_expr())
            .cast(pl.Float64)
            .alias(SESSION_TURNOVER),
        )
    )
    return (
        enriched.join(session, on=["symbol", "date"], how="left", maintain_order="left")
        .with_columns(
            pl.when(pl.col("date").is_in(sorted(saved_days)))
            .then(pl.col(SESSION_TURNOVER))
            .otherwise(pl.col("turnover_rate"))
            .alias("turnover_rate")
        )
        .drop(SESSION_TURNOVER)
    )


def _published_turnover(root: Path, days: list[date]) -> pl.DataFrame:
    """Finite turnover_rate published under *root* for *days*, by symbol and date; an unreadable partition adds none."""
    paths = [root / f"date={d.isoformat()}" / "part.parquet" for d in days]
    paths = [path for path in paths if path.exists()]
    if not paths:
        return pl.DataFrame()
    try:
        return _read_published_turnover(paths)
    except (OSError, pl.exceptions.PolarsError):
        frames = []
        for path in paths:
            try:
                frames.append(_read_published_turnover([path]))
            except (OSError, pl.exceptions.PolarsError) as exc:
                logger.warning(
                    "taiwan enriched partition %s unreadable, its turnover_rate is not kept: %s",
                    path.parent.name,
                    exc,
                )
        return pl.concat(frames) if frames else pl.DataFrame()


def _read_published_turnover(paths: list[Path]) -> pl.DataFrame:
    """symbol, date and the finite turnover_rate of the enriched partitions at *paths*."""
    return (
        pl.scan_parquet(
            paths,
            schema={"symbol": pl.Utf8, "date": pl.Date, "turnover_rate": pl.Float64},
            missing_columns="insert",
            extra_columns="ignore",
            cast_options=pl.ScanCastOptions(integer_cast="allow-float", float_cast="upcast"),
        )
        .filter(pl.col("turnover_rate").is_finite())
        .unique(subset=["symbol", "date"], keep="last")
        .rename({"turnover_rate": PUBLISHED_TURNOVER})
        .collect()
    )


def limit_profile(symbols: list[str], sessions: ListingSessions) -> pl.DataFrame:
    """Per-symbol statutory limit percentage, tick ladder and initial-listing no-limit window on *sessions*."""
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


def add_taiwan_limit_signals(enriched: pl.DataFrame, profile: pl.DataFrame) -> pl.DataFrame:
    """Derive the Taiwan limit-board signals from the statutory daily limits.

    The shared pipeline skips these because it wants an A-share instrument
    table for the limit prices; Taiwan carries its own rules instead. Limits and
    ticks are raw-scale prices, so the comparison uses raw_close / raw_high / raw_low
    and the previous close moved onto the raw scale; a session carrying official limits
    (ref_limit_up / ref_limit_down) uses them. The limit streaks are counted from these signals.
    A session inside an initial listing's no-limit window of *profile* (a limit_profile frame)
    has no limit board, and a ref_base_missing session gets null flags.
    """
    cols = ["signal_limit_up", "signal_limit_down", "signal_broken_limit_up", "signal_limit_down_recovery"]
    enriched = enriched.with_columns([
        pl.lit(None, dtype=pl.Float64).alias(c)
        for c in (REF_LIMIT_UP, REF_LIMIT_DOWN) if c not in enriched.columns
    ])
    if profile.is_empty():
        unknown = _base_unknown(enriched)
        out = enriched.with_columns([pl.when(unknown).then(None).otherwise(False).alias(c) for c in cols])
    else:
        out = TaiwanEnrichedRefreshService._limit_signals(enriched.join(profile, on="symbol", how="left"))
    out = with_limit_streak(out, "signal_limit_up", "consecutive_limit_ups")
    return with_limit_streak(out, "signal_limit_down", "consecutive_limit_downs")


def _base_unknown(frame: pl.DataFrame) -> pl.Expr:
    return pl.col(REF_BASE_MISSING).fill_null(False) if REF_BASE_MISSING in frame.columns else pl.lit(False)


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
