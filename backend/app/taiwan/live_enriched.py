"""Taiwan intraday enriched rows, computed by the pipeline that publishes the enriched table.

Today's row of a symbol is the row the evening publish writes for today from the same bars and
events: the stored history before today plus today's live bar, run through
compute_taiwan_enriched. Like the published newest row it is on today's traded price scale, so a
corporate action on today needs no conversion. The exchange lists today's corporate actions only
after the session, so until then a quoted previous close that differs from the stored close
stands in for the event's reference price, provided the store holds the previous session. Only
a stand-in above SPLIT_FACTOR takes the publish's official_ratio; any other par-value change
uses pre_close / reference and can differ from the published row by the tick rounding of the
reference price.
Intraday the volume ratio, and every signal reading it, is scaled to a full session by the time
elapsed at the quote.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from app.taiwan.corporate_actions import corporate_action_schema
from app.taiwan.daily_store import REPAIR_WINDOW_PARTITIONS, TAIWAN_DAILY_COLUMNS, TaiwanDailyStore
from app.taiwan.enriched_refresh import FACTOR_SET_FILE, compute_taiwan_enriched, limit_profile
from app.taiwan.enrichment.corporate_actions import EXDIV, PAR_CHANGE, REDUCTION
from app.taiwan.market_rules import ListingSessions

logger = logging.getLogger(__name__)

# The span-60 EMA, the slowest recursive state, keeps (59/61)**1200 = 4e-18 of its seed.
LIVE_HISTORY_SESSIONS = 1200
PREPARE_RETRY_SECONDS = 60.0
# Half the smallest Taiwan tick: a quoted previous close this close to the stored one is that close.
SAME_PRICE_TOLERANCE = 0.005
# Above this pre_close / reference a quoted event is taken for a split rather than a dividend.
SPLIT_FACTOR = 1.5
# Sessions whose exchanges are recorded, enough to find the previous session after a holiday.
RECENT_SESSIONS = 5

BAR_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.Utf8,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "amount": pl.Float64,
    "quote_ts": pl.Int64,
}
LIVE_SCHEMA: dict[str, pl.DataType] = {**BAR_SCHEMA, "prev_close": pl.Float64}
QUOTE_TS_AT = list(LIVE_SCHEMA).index("quote_ts")
VOLUME_TIME_FACTOR = "volume_time_factor"


@dataclass(frozen=True)
class _History:
    key: tuple
    bars: dict[str, pl.DataFrame]
    trading_days: tuple[date, ...]
    first_sessions: dict[str, date]
    events: pl.DataFrame
    instruments: pl.DataFrame
    exchanges: dict[date, frozenset[str]]


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _recent_partitions(daily_dir: Path) -> tuple[tuple[str, tuple[int, int]], ...]:
    """Name and signature of the newest stored partitions a daily repair may rewrite."""
    try:
        names = sorted(
            (e.name for e in os.scandir(daily_dir) if e.name.startswith("date=")), reverse=True
        )
    except OSError:
        return ()
    recent = []
    for name in names:
        signature = _signature(daily_dir / name / "part.parquet")
        if signature is not None:
            recent.append((name, signature))
            if len(recent) == REPAIR_WINDOW_PARTITIONS:
                break
    return tuple(recent)


def _conform(bars: pl.DataFrame) -> pl.DataFrame:
    return bars.select(
        [
            pl.col(c).cast(dtype, strict=False)
            if c in bars.columns
            else pl.lit(None, dtype=dtype).alias(c)
            for c, dtype in LIVE_SCHEMA.items()
        ]
    )


def _volume_time_factor(quote_ts: int | None) -> float:
    from app.taiwan.realtime.calendar import session_progress, session_progress_from_ts

    progress = session_progress_from_ts(quote_ts) if quote_ts else session_progress()
    return 1.0 / progress if 0.0 < progress < 1.0 else 1.0


def _exchange(symbol: str) -> str:
    return symbol.rpartition(".")[2]


def _previous_session(today: date) -> date | None:
    """The last day before *today* the default calendar does not rule out as a trading day."""
    from app.taiwan.realtime.calendar import is_trading_day

    day = today - timedelta(days=1)
    for _ in range(31):
        if is_trading_day(day) is not False:
            return day
        day -= timedelta(days=1)
    return None


def _current_exchanges(history: _History, today: date) -> frozenset[str]:
    """Exchanges the store holds rows of for the session before *today*; none when it lacks it."""
    previous = _previous_session(today)
    stored = [d for d in history.trading_days if d < today]
    if previous is None or not stored or stored[-1] != previous:
        return frozenset()
    return history.exchanges.get(previous, frozenset())


def _quoted_events(
    history: _History,
    bars: pl.DataFrame,
    events: pl.DataFrame,
    today: date,
    exchanges: frozenset[str],
) -> pl.DataFrame:
    """Today's corporate actions the table does not list yet, from the quoted previous closes.

    A symbol of *exchanges* qualifies when its quoted previous close differs from its last stored
    close and no listed event falls after that session. The kind is unknown: a factor below 1 is
    taken for a capital reduction and above SPLIT_FACTOR for a par-value change, both restating
    volume; anything else for a dividend.
    """
    schema = corporate_action_schema()
    listed = dict(
        events.filter(pl.col("date") <= today)
        .group_by("symbol")
        .agg(pl.col("date").max())
        .iter_rows()
    )
    rows = []
    for symbol, reference in bars.select("symbol", "prev_close").iter_rows():
        stored = history.bars.get(symbol)
        if reference is None or reference <= 0 or stored is None:
            continue
        if _exchange(symbol) not in exchanges:
            continue
        traded = stored.filter(
            (pl.col("date") < today)
            & pl.col("close").is_not_null()
            & ~((pl.col("open") == 0) & (pl.col("high") == 0)).fill_null(False)
        )
        if traded.is_empty():
            continue
        last_session, last_close = traded["date"][-1], traded["close"][-1]
        if abs(reference - last_close) < SAME_PRICE_TOLERANCE:
            continue
        if listed.get(symbol) is not None and listed[symbol] > last_session:
            continue
        factor = last_close / reference
        kind = REDUCTION if factor < 1.0 else PAR_CHANGE if factor > SPLIT_FACTOR else EXDIV
        rows.append(
            {
                **dict.fromkeys(schema),
                "symbol": symbol,
                "date": today,
                "kind": kind,
                "pre_close": last_close,
                "ref_price": reference,
                "open_ref": reference,
                "ex_factor": factor,
                "source": "realtime_quote",
                "trade_date": today,
                "status": "quoted",
            }
        )
    return pl.DataFrame(rows, schema=schema)


class TaiwanLiveEnriched:
    """Today's Taiwan enriched rows from a prepared stored history, cached per unchanged bar.

    prepare() reads the history and must run off the quote thread; rows() never reads it and
    answers None until a history matching the files on disk is prepared. A failed preparation
    is retried for the same files after PREPARE_RETRY_SECONDS. Thread-safe.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._history: _History | None = None
        self._rows: dict[str, tuple[tuple, pl.DataFrame]] = {}
        self._lock = threading.Lock()
        self._build_lock = threading.Lock()
        self._preparing = False
        self._failure: tuple[tuple, float] | None = None
        self._behind_logged: date | None = None

    @property
    def _taiwan_dir(self) -> Path:
        return self._data_dir / "taiwan"

    def _key(self) -> tuple:
        """Signatures of the recent stored sessions, factor record, master, share capital and events."""
        return (
            _recent_partitions(self._taiwan_dir / "daily"),
            _signature(self._data_dir / FACTOR_SET_FILE),
            _signature(self._taiwan_dir / "security_master.parquet"),
            _signature(self._taiwan_dir / "share_capital.parquet"),
            _signature(self._taiwan_dir / "corporate_actions" / "events.parquet"),
        )

    def invalidate(self) -> None:
        with self._lock:
            self._history = None
            self._rows.clear()

    def prepare(self) -> None:
        """Load the stored history unless the prepared one is current, then swap it in."""
        with self._build_lock:
            key = self._key()
            current = self._history
            if current is not None and current.key == key:
                return
            history = self._load(key)
            with self._lock:
                self._history = history
                self._rows.clear()
                self._failure = None
            logger.info(
                "taiwan live history prepared: %d symbols, %d sessions",
                len(history.bars),
                len(history.trading_days),
            )

    def prepare_in_background(self, key: tuple) -> None:
        """Prepare on a daemon thread, unless one runs or the history of *key* failed recently."""
        with self._lock:
            if self._preparing:
                return
            failure = self._failure
            if (
                failure is not None
                and failure[0] == key
                and time.monotonic() - failure[1] < PREPARE_RETRY_SECONDS
            ):
                return
            self._preparing = True

        def run() -> None:
            try:
                self.prepare()
            except Exception as exc:
                with self._lock:
                    repeated = self._failure is not None and self._failure[0] == key
                    self._failure = (key, time.monotonic())
                log = logger.debug if repeated else logger.warning
                log("taiwan live history preparation failed: %s", exc)
            finally:
                with self._lock:
                    self._preparing = False

        threading.Thread(target=run, name="taiwan-live-history", daemon=True).start()

    def _load(self, key: tuple) -> _History:
        from app.taiwan.corporate_actions import TaiwanCorporateActionStore, read_events_or_empty
        from app.taiwan.share_capital_store import TaiwanShareCapitalStore

        daily_dir = self._taiwan_dir / "daily"
        bars: dict[str, pl.DataFrame] = {}
        trading_days: tuple[date, ...] = ()
        first_sessions: dict[str, date] = {}
        exchanges: dict[date, frozenset[str]] = {}
        if daily_dir.is_dir():
            store = TaiwanDailyStore(daily_dir)
            trading_days = tuple(store.available_dates())
            if trading_days:
                start = trading_days[-(LIVE_HISTORY_SESSIONS + 1) :][0]
                raw = store.scan_range(None, start, None).select(TAIWAN_DAILY_COLUMNS)
                bars = {
                    sym: frame for (sym,), frame in raw.partition_by("symbol", as_dict=True).items()
                }
                first_sessions = store.first_sessions()
                recent = raw.filter(pl.col("date") >= trading_days[-RECENT_SESSIONS:][0])
                for (day,), frame in recent.partition_by("date", as_dict=True).items():
                    exchanges[day] = frozenset(_exchange(s) for s in frame["symbol"].unique())
        events = read_events_or_empty(
            TaiwanCorporateActionStore(self._taiwan_dir / "corporate_actions")
        )
        instruments = TaiwanShareCapitalStore(self._taiwan_dir).as_instruments()
        return _History(key, bars, trading_days, first_sessions, events, instruments, exchanges)

    def rows(self, bars: pl.DataFrame) -> pl.DataFrame | None:
        """Today's enriched rows of the Taiwan *bars*, all dated today; None without a current history.

        *bars* may carry the provider's prev_close. A missing or stale history is prepared on a
        background thread. Only symbols whose bar changed since the last call are recomputed;
        a halted bar yields no row.
        """
        key = self._key()
        history = self._history
        if history is None or history.key != key:
            self.prepare_in_background(key)
            return None
        bars = _conform(bars).unique(subset=["symbol"], keep="last", maintain_order=True)
        if bars.is_empty():
            return pl.DataFrame()

        from app.indicators.pipeline import _get_custom_signal_exprs

        signals = id(_get_custom_signal_exprs())
        cached: list[pl.DataFrame] = []
        changed: dict[str, tuple] = {}
        for bar in bars.iter_rows():
            cache_key = (history.key, signals, bar, _volume_time_factor(bar[QUOTE_TS_AT]))
            with self._lock:
                hit = self._rows.get(bar[0])
            if hit is not None and hit[0] == cache_key:
                cached.append(hit[1])
            else:
                changed[bar[0]] = cache_key

        if changed:
            computed = self._compute(history, bars.filter(pl.col("symbol").is_in(list(changed))))
            with self._lock:
                for symbol, cache_key in changed.items():
                    self._rows[symbol] = (cache_key, computed.filter(pl.col("symbol") == symbol))
            cached.append(computed)
        frames = [frame for frame in cached if not frame.is_empty()]
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def _compute(self, history: _History, live: pl.DataFrame) -> pl.DataFrame:
        from app.indicators.pipeline import add_limit_distance_columns

        today = live["date"].max()
        symbols = live["symbol"].to_list()
        bars = live.select(list(BAR_SCHEMA))
        before = [d for d in history.trading_days if d < today]
        window_start = before[-LIVE_HISTORY_SESSIONS:][0] if before else today
        stored = [history.bars[s] for s in symbols if s in history.bars]
        if stored:
            history_bars = pl.concat(stored).filter(
                (pl.col("date") >= window_start) & (pl.col("date") < today)
            )
            raw = pl.concat([history_bars, bars], how="vertical_relaxed")
        else:
            raw = bars
        calendar = (*before, today)
        first_sessions = {**history.first_sessions}
        for symbol in symbols:
            first_sessions[symbol] = min(first_sessions.get(symbol, today), today)
        limits = limit_profile(symbols, ListingSessions(calendar, first_sessions))
        events = history.events.filter(pl.col("symbol").is_in(symbols))
        exchanges = _current_exchanges(history, today)
        missing = {_exchange(s) for s in symbols} - exchanges
        if missing and self._behind_logged != today:
            self._behind_logged = today
            logger.warning(
                "taiwan daily store is behind the session before %s for %s; quoted reference "
                "prices are not applied, so live rows of a corporate-action day stay unadjusted",
                today,
                ", ".join(sorted(missing)),
            )
        quoted = _quoted_events(history, live, events, today, exchanges)
        events = pl.concat([events, quoted], how="vertical_relaxed")
        enriched = compute_taiwan_enriched(
            raw, events, today, calendar, limits, history.instruments, today=today
        )
        if enriched.is_empty():
            return enriched
        enriched = _scale_volume_ratio(enriched, live, today)
        out = enriched.filter(pl.col("date") == today)
        if "ex_rights" not in out.columns:
            out = out.with_columns(pl.lit(False).alias("ex_rights"))
        return add_limit_distance_columns(out)


def _scale_volume_ratio(enriched: pl.DataFrame, live: pl.DataFrame, today: date) -> pl.DataFrame:
    """Scale today's partial-session vol_ratio_5d to a full session and recompute what reads it.

    The factor applied is kept in VOLUME_TIME_FACTOR; above 1.0 the volume figures are estimates.
    Rows quoted at or after the close keep the published values. Custom signals are evaluated
    again on the whole history, so their date offsets still read earlier sessions.
    """
    from app.indicators.pipeline import _get_custom_signal_exprs
    from app.strategy import custom_signals

    factors = {
        symbol: _volume_time_factor(quote_ts)
        for symbol, quote_ts in live.select("symbol", "quote_ts").iter_rows()
    }
    enriched = enriched.with_columns(
        pl.when(pl.col("date") == today)
        .then(pl.col("symbol").replace_strict(factors, default=1.0, return_dtype=pl.Float64))
        .otherwise(1.0)
        .alias(VOLUME_TIME_FACTOR)
    )
    if all(f == 1.0 for f in factors.values()):
        return enriched
    factor = pl.col(VOLUME_TIME_FACTOR)
    scaled_rows = factor != 1.0
    scaled = enriched.with_columns((pl.col("vol_ratio_5d") * factor).alias("vol_ratio_5d"))
    exprs = _get_custom_signal_exprs()
    reevaluated = custom_signals.inject(scaled, exprs)
    recomputed = {
        "signal_volume_surge": pl.col("vol_ratio_5d") >= 2.0,
        **{
            name: pl.lit(reevaluated[name])
            for name in exprs
            if name in reevaluated.columns and name in scaled.columns
        },
    }
    return scaled.with_columns(
        [
            pl.when(scaled_rows).then(value).otherwise(pl.col(name)).alias(name)
            for name, value in recomputed.items()
        ]
    )
