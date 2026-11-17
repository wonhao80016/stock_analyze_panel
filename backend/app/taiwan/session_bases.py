"""Official per-session change bases and limit prices of TWSE and TPEx: storage, refresh and lookups.

Adapts:
  - TWSE TWT84U price limits: https://www.twse.com.tw/rwd/zh/variation/TWT84U
  - TPEx dailyQuotes closing quotes: https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes

Storage: <settings.data_dir>/taiwan/session_bases/{twse,tpex}/date=<source date>/part.parquet,
one file per official table, each rewritten atomically. Prices are raw TWD. A "--" cell, a zero
price and the 9999.95 / 0.01 limits printed for a security without price limits are stored as
null. Only codes of the security master's stocks and ETFs, and of the daily store's partition for
the source date, are kept.

TWT84U(D) holds the bases of session D itself; dailyQuotes(D) holds those of the first session
after D. official_session_bases aligns dailyQuotes on the daily store's sessions, so a day the
market closed unexpectedly is passed over. prev_close, last_bid, last_ask and prev_change belong to
the session before the one the bases apply to; prev_change, the official change against that
session's base, comes from dailyQuotes only and is null where its cell is no plain signed number.
Files stored before prev_change existed read it as null.

A dailyQuotes file written before daily_final_cutoff() on its own day is provisional (as in
is_provisional_mtime) and the next refresh fetches it again.

Backfill CLI: python -m app.taiwan.session_bases --since 2024-01-01 --source all --delay 3
Resumption refetch CLI: python -m app.taiwan.session_bases --since 2024-01-01 --resumptions [--dry-run]
"""
from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from bisect import bisect_left
from collections.abc import Callable, Collection, Iterable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, ClassVar

import polars as pl

from app.taiwan import institutional_margin_refresh
from app.taiwan.daily_store import TaiwanDailyStore, daily_final_cutoff, is_provisional_mtime
from app.taiwan.providers.snapshot_provider import TPEX_DAILY_QUOTES_URL
from app.taiwan.providers.taiwan_values import TAIPEI, parse_number
from app.taiwan.universe.service import write_atomically

logger = logging.getLogger(__name__)

TWSE = "twse"
TPEX = "tpex"
SOURCES = (TWSE, TPEX)

TWT84U_URL = "https://www.twse.com.tw/rwd/zh/variation/TWT84U"

DEFAULT_DELAY_SEC = 3.0
MAX_CONSECUTIVE_FAILURES = 3
FETCH_TIMEOUT_SEC = 30.0

NO_LIMIT_UP = 9999.95
NO_LIMIT_DOWN = 0.01

BASE_COLUMNS = ("ref_price", "limit_up", "limit_down")
RETRIEVED_AT_TYPE = pl.Datetime("us", "Asia/Taipei")

_SIGNED_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")


def session_base_schema() -> dict[str, pl.DataType]:
    return {
        "symbol": pl.String,
        "source_date": pl.Date,
        "ref_price": pl.Float64,
        "limit_up": pl.Float64,
        "limit_down": pl.Float64,
        "prev_close": pl.Float64,
        "last_bid": pl.Float64,
        "last_ask": pl.Float64,
        "prev_change": pl.Float64,
        "source": pl.String,
        "source_url": pl.String,
        "retrieved_at": RETRIEVED_AT_TYPE,
    }


def _bases_schema(columns: Sequence[str] = BASE_COLUMNS) -> dict[str, pl.DataType]:
    return {"symbol": pl.String, "trade_date": pl.Date, **{c: pl.Float64 for c in columns}}


def _conform(df: pl.DataFrame) -> pl.DataFrame:
    return df.select([
        pl.col(name).cast(dtype) if name in df.columns else pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in session_base_schema().items()
    ])


def _compact(raw: Any) -> str:
    """NFKC text with all whitespace removed, so the spaced dailyQuotes next-session titles match."""
    return "".join(unicodedata.normalize("NFKC", str(raw)).split())


def _price(raw: Any, column: str, source: str) -> float | None:
    """An official price; a dash-only cell (TPEx prints --- for no trade) and zero are None."""
    text = _compact(raw)
    if not text.strip("-"):
        return None
    value = parse_number(text)
    if value is None:
        return None
    if value < 0:
        raise ValueError(f"{source} {column} is negative: {raw!r}")
    return value if value > 0 else None


def _change(raw: Any) -> float | None:
    """An official signed change; a cell holding anything but a plain signed number is None."""
    text = _compact(raw).replace(",", "")
    return float(text) if _SIGNED_NUMBER.fullmatch(text) else None


def _unique_index(names: list[str], title: str, positions: Iterable[int], source: str) -> int:
    hits = [i for i in positions if names[i] == title]
    if len(hits) != 1:
        raise ValueError(f"{source} has {len(hits)} columns titled {title!r} where one was expected")
    return hits[0]


class TaiwanSessionBaseStore:
    """Parquet persistence for the official session bases, one file per source table and date."""

    FILE_NAME = "part.parquet"

    def __init__(self, data_dir: Path | None = None) -> None:
        if data_dir is None:
            from app.taiwan.data_root import taiwan_data_root

            data_dir = taiwan_data_root() / "session_bases"
        self._data_dir = Path(data_dir)

    def path(self, source: str, source_date: date) -> Path:
        if source not in SOURCES:
            raise ValueError(f"unknown session base source {source!r}")
        return self._data_dir / source / f"date={source_date.isoformat()}" / self.FILE_NAME

    def write(self, source: str, source_date: date, frame: pl.DataFrame) -> None:
        frame = _conform(frame)
        if frame.filter(pl.col("source_date") != source_date).height:
            raise ValueError(f"{source} rows for {source_date} carry another source_date")
        write_atomically(self.path(source, source_date), frame.sort("symbol").write_parquet)

    def stored_dates(self, source: str) -> list[date]:
        dates: list[date] = []
        for path in (self._data_dir / source).glob(f"date=*/{self.FILE_NAME}"):
            try:
                dates.append(date.fromisoformat(path.parent.name.split("=", 1)[1]))
            except (ValueError, IndexError):
                continue
        return sorted(dates)

    def is_final(self, source: str, source_date: date) -> bool:
        """Whether a stored file for *source_date* needs no refetch; a missing one is not final."""
        try:
            mtime = self.path(source, source_date).stat().st_mtime
        except OSError:
            return False
        return source != TPEX or not is_provisional_mtime(source_date, mtime, daily_final_cutoff())

    def read(self, source: str, dates: Iterable[date], symbols: Iterable[str] | None = None) -> pl.DataFrame:
        """Stored rows of *source* for *dates*; absent and unreadable files are skipped."""
        wanted = None if symbols is None else sorted(set(symbols))
        paths = [path for path in (self.path(source, d) for d in sorted(set(dates))) if path.exists()]

        def scan(sources: list[Path]) -> pl.DataFrame:
            if len(sources) > 1:
                lf = pl.scan_parquet(sources, schema=session_base_schema(), missing_columns="insert")
            else:
                lf = pl.scan_parquet(sources)
            if wanted is not None:
                lf = lf.filter(pl.col("symbol").is_in(wanted))
            return _conform(lf.collect())

        if not paths:
            return pl.DataFrame(schema=session_base_schema())
        try:
            return scan(paths)
        except Exception:
            # A bad file fails the whole scan, so the files are read one by one to skip only it.
            frames: list[pl.DataFrame] = []
            for path in paths:
                try:
                    frames.append(scan([path]))
                except Exception as exc:
                    logger.warning("Session base file %s unreadable, its bases are missing: %s", path, exc)
        if not frames:
            return pl.DataFrame(schema=session_base_schema())
        return pl.concat(frames)


class _SessionBaseAdapter:
    SOURCE: ClassVar[str]
    STORE_KEY: ClassVar[str]
    EXCHANGE: ClassVar[str]

    def build_url(self, source_date: date) -> str:
        raise NotImplementedError

    def _records(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """One dict per row: code plus the parsed prices of session_base_schema."""
        raise NotImplementedError

    def parse_payload(
        self,
        payload: Any,
        source_date: date,
        source_url: str,
        *,
        codes: set[str] | None = None,
        fetched_at: datetime | None = None,
    ) -> pl.DataFrame:
        """The rows of a response for *source_date* whose code is in *codes* (every row without it).

        Raises ValueError for a stat other than OK, a response stamped with another date, a
        missing or duplicated column, a row count that disagrees with the response's own total,
        a duplicated code, malformed numbers, and a table left without rows.
        """
        if not isinstance(payload, dict):
            raise ValueError(f"{self.SOURCE} response is not a JSON object")
        stat = str(payload.get("stat", "")).strip()
        if stat.upper() != "OK":
            raise ValueError(f"{self.SOURCE} rejected the query for {source_date}: stat {stat!r}")
        stamped = str(payload.get("date") or "").strip()
        if stamped != f"{source_date:%Y%m%d}":
            raise ValueError(f"{self.SOURCE} answered {stamped!r} to a query for {source_date:%Y%m%d}")
        records = self._records(payload)
        if not records:
            raise ValueError(f"{self.SOURCE} returned no rows for {source_date}")
        retrieved = fetched_at or datetime.now(TAIPEI)
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for record in records:
            code = record.pop("code")
            if code in seen:
                raise ValueError(f"{self.SOURCE} lists {code} twice for {source_date}")
            seen.add(code)
            if codes is not None and code not in codes:
                continue
            if record["limit_up"] is not None and record["limit_up"] >= NO_LIMIT_UP:
                record["limit_up"] = None
            if record["limit_down"] is not None and record["limit_down"] <= NO_LIMIT_DOWN:
                record["limit_down"] = None
            rows.append({
                "symbol": f"{code}.{self.EXCHANGE}",
                "source_date": source_date,
                **record,
                "source": self.SOURCE,
                "source_url": source_url,
                "retrieved_at": retrieved,
            })
        if not rows:
            raise ValueError(f"{self.SOURCE} has none of the listed codes among its {len(records)} rows")
        return pl.DataFrame(rows, schema=session_base_schema()).sort("symbol")

    def _parse_rows(
        self, rows: Any, fields: list[Any], index: dict[str, int]
    ) -> list[dict[str, Any]]:
        if not isinstance(rows, list):
            raise ValueError(f"{self.SOURCE} table lacks its row list")
        records: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < len(fields):
                raise ValueError(f"{self.SOURCE} row {row!r} does not have the {len(fields)} listed columns")
            code = _compact(row[index["code"]])
            if not code:
                raise ValueError(f"{self.SOURCE} row without a security code: {row!r}")
            records.append({
                "code": code,
                **{
                    key: _change(row[i]) if key == "prev_change" else _price(row[i], key, self.SOURCE)
                    for key, i in index.items()
                    if key != "code"
                },
            })
        return records


class TwseSessionBaseAdapter(_SessionBaseAdapter):
    """TWT84U(D): the bases and limits of session D, and the close and best quotes of the session before it."""

    SOURCE = "twse:twt84u"
    STORE_KEY = TWSE
    EXCHANGE = "TWSE"
    CODE_COLUMN = "證券代號"
    TODAY_GROUP = "本日"
    PRIOR_GROUP = "前日"
    # The base title heads a column in both groups, so each title is looked up inside its group only.
    TODAY_COLUMNS: ClassVar[dict[str, str]] = {
        "limit_up": "漲停價",
        "ref_price": "開盤競價基準",
        "limit_down": "跌停價",
    }
    PRIOR_COLUMNS: ClassVar[dict[str, str]] = {
        "prev_close": "收盤價",
        "last_bid": "買進揭示價",
        "last_ask": "賣出揭示價",
    }

    def build_url(self, source_date: date) -> str:
        return f"{TWT84U_URL}?date={source_date:%Y%m%d}&selectType=ALL&response=json"

    def _group(self, groups: Any, title: str, width: int) -> range:
        if not isinstance(groups, list):
            raise ValueError(f"{self.SOURCE} response lacks its column groups")
        hits = [g for g in groups if isinstance(g, dict) and _compact(g.get("title", "")) == title]
        if len(hits) != 1:
            raise ValueError(f"{self.SOURCE} has {len(hits)} column groups titled {title!r}")
        start, span = hits[0].get("start"), hits[0].get("span")
        if not isinstance(start, int) or not isinstance(span, int) or start < 0 or span < 1 or start + span > width:
            raise ValueError(f"{self.SOURCE} column group {title!r} spans {start!r}+{span!r} of {width} columns")
        return range(start, start + span)

    def _records(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        fields, rows = payload.get("fields"), payload.get("data")
        if not isinstance(fields, list) or not isinstance(rows, list):
            raise ValueError(f"{self.SOURCE} response lacks its fields or data list")
        total = payload.get("total")
        if isinstance(total, bool) or not isinstance(total, int) or total != len(rows):
            raise ValueError(f"{self.SOURCE} returned {len(rows)} rows but reports total {total!r}")
        names = [_compact(f) for f in fields]
        today = self._group(payload.get("groups"), self.TODAY_GROUP, len(names))
        prior = self._group(payload.get("groups"), self.PRIOR_GROUP, len(names))
        index = {"code": _unique_index(names, self.CODE_COLUMN, range(len(names)), self.SOURCE)}
        index.update({k: _unique_index(names, t, today, self.SOURCE) for k, t in self.TODAY_COLUMNS.items()})
        index.update({k: _unique_index(names, t, prior, self.SOURCE) for k, t in self.PRIOR_COLUMNS.items()})
        return self._parse_rows(rows, fields, index)


class TpexSessionBaseAdapter(_SessionBaseAdapter):
    """dailyQuotes(D): the close and last quotes of session D, and the bases and limits of the session after it."""

    SOURCE = "tpex:dailyquotes"
    STORE_KEY = TPEX
    EXCHANGE = "TPEX"
    COLUMNS: ClassVar[dict[str, str]] = {
        "code": "代號",
        "prev_close": "收盤",
        "last_bid": "最後買價",
        "last_ask": "最後賣價",
        "ref_price": "次日參考價",
        "limit_up": "次日漲停價",
        "limit_down": "次日跌停價",
    }
    CHANGE_COLUMN = "漲跌"

    def build_url(self, source_date: date) -> str:
        roc = f"{source_date.year - 1911}/{source_date.month:02d}/{source_date.day:02d}"
        return f"{TPEX_DAILY_QUOTES_URL}?date={roc}&response=json"

    def _records(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        tables = payload.get("tables")
        if not isinstance(tables, list):
            raise ValueError(f"{self.SOURCE} response lacks its table list")
        records: list[dict[str, Any]] = []
        quote_tables = 0
        for table in tables:
            fields = table.get("fields") if isinstance(table, dict) else None
            if not isinstance(fields, list):
                continue
            names = [_compact(f) for f in fields]
            if not set(self.COLUMNS.values()) <= set(names):
                continue
            quote_tables += 1
            rows, total = table.get("data"), table.get("totalCount")
            if not isinstance(rows, list) or isinstance(total, bool) or not isinstance(total, int) or total != len(rows):
                raise ValueError(f"{self.SOURCE} table {table.get('title')!r} disagrees with its totalCount {total!r}")
            index = {k: _unique_index(names, t, range(len(names)), self.SOURCE) for k, t in self.COLUMNS.items()}
            if self.CHANGE_COLUMN in names:
                index["prev_change"] = _unique_index(names, self.CHANGE_COLUMN, range(len(names)), self.SOURCE)
            records.extend(self._parse_rows(rows, fields, index))
        if not quote_tables:
            raise ValueError(f"{self.SOURCE} response has no table with the next-session reference columns")
        return records


def default_adapters() -> list[_SessionBaseAdapter]:
    return [TwseSessionBaseAdapter(), TpexSessionBaseAdapter()]


def _previous_sessions(sessions: Sequence[date], trade_dates: Iterable[date]) -> dict[date, date]:
    """For each trade date that is one of *sessions* (sorted) and not the first, the session before it."""
    previous: dict[date, date] = {}
    for d in trade_dates:
        i = bisect_left(sessions, d)
        if 0 < i < len(sessions) and sessions[i] == d:
            previous[d] = sessions[i - 1]
    return previous


def official_session_bases(
    symbols: Iterable[str],
    trade_dates: Iterable[date],
    *,
    store: TaiwanSessionBaseStore | None = None,
    sessions: Iterable[date] | None = None,
) -> pl.DataFrame:
    """The official ref_price, limit_up and limit_down of every (symbol, trade_date) pair asked for.

    A .TWSE symbol takes TWT84U(trade_date). A .TPEX symbol takes dailyQuotes of the session
    before trade_date in *sessions*, so a trade_date outside *sessions* or first among them gets
    nulls. One that stored dailyQuotes leaves out, as after a halt, gets the close less the
    prev_change of dailyQuotes(trade_date) as ref_price and null limits. *sessions* defaults to
    TaiwanDailyStore().available_dates(). A pair without an official figure stays null. Columns:
    symbol, trade_date, ref_price, limit_up, limit_down; one row per pair, sorted by symbol and
    trade_date.
    """
    wanted_symbols = sorted(set(symbols))
    wanted_dates = sorted(set(trade_dates))
    if not wanted_symbols or not wanted_dates:
        return pl.DataFrame(schema=_bases_schema())
    grid = pl.DataFrame({"symbol": wanted_symbols}, schema={"symbol": pl.String}).join(
        pl.DataFrame({"trade_date": wanted_dates}, schema={"trade_date": pl.Date}), how="cross"
    )
    return official_bases_of(grid, store=store, sessions=sessions)


def official_bases_of(
    pairs: pl.DataFrame,
    *,
    store: TaiwanSessionBaseStore | None = None,
    sessions: Iterable[date] | None = None,
    columns: Sequence[str] = BASE_COLUMNS,
) -> pl.DataFrame:
    """official_session_bases of the distinct (symbol, trade_date) rows of *pairs* only.

    *columns* may add prev_close, last_bid and last_ask, the figures of the session before trade_date.
    """
    store = store or TaiwanSessionBaseStore()
    pairs = pairs.select(pl.col("symbol").cast(pl.String), pl.col("trade_date").cast(pl.Date)).unique()
    if pairs.is_empty():
        return pl.DataFrame(schema=_bases_schema(columns))
    found: list[pl.DataFrame] = []
    twse = pairs.filter(pl.col("symbol").str.ends_with(".TWSE"))
    if not twse.is_empty():
        rows = store.read(TWSE, twse["trade_date"].unique().to_list(), twse["symbol"].unique().to_list())
        found.append(rows.select("symbol", pl.col("source_date").alias("trade_date"), *columns))
    tpex = pairs.filter(pl.col("symbol").str.ends_with(".TPEX"))
    if not tpex.is_empty():
        calendar = sorted(set(sessions if sessions is not None else TaiwanDailyStore().available_dates()))
        source_for = _previous_sessions(calendar, tpex["trade_date"].unique().to_list())
        if source_for:
            aligned = pl.DataFrame(
                {"trade_date": list(source_for), "source_date": list(source_for.values())},
                schema={"trade_date": pl.Date, "source_date": pl.Date},
            )
            rows = store.read(TPEX, source_for.values(), tpex["symbol"].unique().to_list())
            listed = rows.join(aligned, on="source_date").select("symbol", "trade_date", *columns)
            stored = [d for d in set(source_for.values()) if store.path(TPEX, d).exists()]
            left_out = (
                tpex.join(aligned.filter(pl.col("source_date").is_in(stored)), on="trade_date")
                .join(listed, on=["symbol", "trade_date"], how="anti")
                .select("symbol", "trade_date")
            )
            found.append(listed)
            if not left_out.is_empty():
                found.append(_resumption_bases(store, left_out, columns))
    if not found:
        return pairs.with_columns([pl.lit(None, dtype=pl.Float64).alias(c) for c in columns]).sort(
            ["symbol", "trade_date"]
        )
    return pairs.join(pl.concat(found), on=["symbol", "trade_date"], how="left").sort(["symbol", "trade_date"])


def _resumption_bases(store: TaiwanSessionBaseStore, pairs: pl.DataFrame, columns: Sequence[str]) -> pl.DataFrame:
    """Bases of TPEx *pairs* from dailyQuotes of their own trade_date: the close less the official change.

    The change is taken against the session's base, so the difference is that base. The limits and
    the figures of the session before stay null.
    """
    own = store.read(TPEX, pairs["trade_date"].unique().to_list(), pairs["symbol"].unique().to_list())
    base = (pl.col("prev_close") - pl.col("prev_change")).round(2)
    return (
        own.rename({"source_date": "trade_date"})
        .join(pairs, on=["symbol", "trade_date"], how="semi")
        .select(
            "symbol",
            "trade_date",
            *[
                (pl.when(base > 0).then(base) if c == "ref_price" else pl.lit(None, dtype=pl.Float64)).alias(c)
                for c in columns
            ],
        )
    )


class ListedCodes:
    """Codes kept for an exchange and source date: the master's stocks and ETFs and that day's stored bars."""

    def __init__(self, daily_store: TaiwanDailyStore) -> None:
        self._daily_store = daily_store
        self._master: dict[str, set[str]] | None = None

    def _master_codes(self) -> dict[str, set[str]]:
        if self._master is None:
            from app.taiwan.universe import get_security_master

            master = get_security_master()
            codes: dict[str, set[str]] = {}
            for asset_type in ("stock", "etf"):
                frame = master.to_provider_dataframe(asset_type=asset_type)
                for symbol in frame.get_column("symbol").to_list() if "symbol" in frame.columns else []:
                    code, _, exchange = symbol.partition(".")
                    codes.setdefault(exchange, set()).add(code)
            self._master = codes
        return self._master

    def __call__(self, exchange: str, source_date: date) -> set[str]:
        suffix = f".{exchange}"
        stored = self._daily_store.read_dates([source_date]).get_column("symbol").to_list()
        return self._master_codes().get(exchange, set()) | {
            s[: -len(suffix)] for s in stored if s.endswith(suffix)
        }


class TaiwanSessionBaseRefreshService:
    """Fetches TWT84U and dailyQuotes for the daily store's sessions into TaiwanSessionBaseStore."""

    def __init__(
        self,
        store: TaiwanSessionBaseStore | None = None,
        daily_store: TaiwanDailyStore | None = None,
        adapters: list[_SessionBaseAdapter] | None = None,
        fetch_json: Callable[[str], Any] | None = None,
        codes: Callable[[str, date], set[str]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._store = store or TaiwanSessionBaseStore()
        self._daily_store = daily_store or TaiwanDailyStore()
        self._adapters = adapters if adapters is not None else default_adapters()
        self._fetch_json = fetch_json or (
            lambda url: institutional_margin_refresh._fetch_json_with_retry(url, timeout=FETCH_TIMEOUT_SEC)
        )
        self._codes = codes or ListedCodes(self._daily_store)
        self._sleep = sleep

    @property
    def store(self) -> TaiwanSessionBaseStore:
        return self._store

    def resumption_dates(self, since: date, until: date) -> list[date]:
        """Stored sessions in [since, until] whose own dailyQuotes a TPEx resumption base still needs.

        On such a session a .TPEX symbol traded again that the stored dailyQuotes of the session
        before leaves out, and its own stored dailyQuotes lists it without a prev_change. A first
        bar does not count: no consumer takes a base for it.
        """
        sessions = self._daily_store.available_dates()
        previous = {
            d: p
            for d, p in _previous_sessions(sessions, [d for d in sessions if since <= d <= until]).items()
            if self._store.path(TPEX, p).exists() and self._store.path(TPEX, d).exists()
        }
        if not previous:
            return []
        aligned = pl.DataFrame(
            {"trade_date": list(previous), "_prior": list(previous.values())},
            schema={"trade_date": pl.Date, "_prior": pl.Date},
        )
        traded = (
            self._daily_store.scan_range(None, None, max(previous))
            .filter(
                pl.col("symbol").str.ends_with(".TPEX")
                & (pl.col("close") > 0)
                & ~((pl.col("open") == 0) & (pl.col("high") == 0)).fill_null(False)
            )
            .sort(["symbol", "date"])
            .filter(pl.col("date").shift(1).over("symbol").is_not_null())
            .select("symbol", pl.col("date").alias("trade_date"))
        )
        prior = self._store.read(TPEX, previous.values()).select("symbol", pl.col("source_date").alias("_prior"))
        own = self._store.read(TPEX, previous).select("symbol", pl.col("source_date").alias("trade_date"), "prev_change")
        needed = (
            traded.join(aligned, on="trade_date")
            .join(prior, on=["symbol", "_prior"], how="anti")
            .join(own, on=["symbol", "trade_date"])
            .filter(pl.col("prev_change").is_null())
        )
        return sorted(needed["trade_date"].unique().to_list())

    def refresh(
        self,
        since: date,
        until: date,
        sources: Sequence[str] = SOURCES,
        delay: float = DEFAULT_DELAY_SEC,
        force: Collection[date] = (),
    ) -> dict[str, Any]:
        """Fetch every stored session in [since, until] whose file is missing, provisional or in *force*, *delay* apart.

        A throttled request, or MAX_CONSECUTIVE_FAILURES failures in a row from one source, stops
        the run; stopped_at names that request and not_attempted counts the requests after it.
        """
        if until < since:
            raise ValueError(f"until {until} is before since {since}")
        unknown = set(sources) - set(SOURCES)
        if unknown:
            raise ValueError(f"unknown session base sources {sorted(unknown)}")
        adapters = [a for a in self._adapters if a.STORE_KEY in sources]
        sessions = [d for d in self._daily_store.available_dates() if since <= d <= until]
        planned = [(d, a) for d in sessions for a in adapters]
        stats: dict[str, Any] = {
            "since": str(since),
            "until": str(until),
            "sources": [a.STORE_KEY for a in adapters],
            "sessions": len(sessions),
            "requests": 0,
            "written": 0,
            "skipped": 0,
            "failed": [],
            "stopped_at": None,
            "not_attempted": 0,
        }
        consecutive = dict.fromkeys((a.STORE_KEY for a in adapters), 0)
        for position, (d, adapter) in enumerate(planned):
            if d not in force and self._store.is_final(adapter.STORE_KEY, d):
                stats["skipped"] += 1
                continue
            if stats["requests"]:
                self._sleep(delay)
            stats["requests"] += 1
            url = adapter.build_url(d)
            try:
                frame = adapter.parse_payload(
                    self._fetch_json(url), d, url, codes=self._codes(adapter.EXCHANGE, d)
                )
                self._store.write(adapter.STORE_KEY, d, frame)
            except Exception as exc:
                logger.warning("Session base fetch failed for %s %s: %s", adapter.SOURCE, d, exc)
                stats["failed"].append({"source": adapter.SOURCE, "date": str(d), "error": str(exc)})
                consecutive[adapter.STORE_KEY] += 1
                if isinstance(exc, institutional_margin_refresh.ExchangeThrottledError):
                    reason = "throttled"
                elif consecutive[adapter.STORE_KEY] >= MAX_CONSECUTIVE_FAILURES:
                    reason = "consecutive_failures"
                else:
                    continue
                stats["stopped_at"] = {"source": adapter.SOURCE, "date": str(d), "reason": reason}
                stats["not_attempted"] = len(planned) - position - 1
                logger.warning(
                    "Session base refresh stopped at %s %s (%s); %d requests not attempted",
                    adapter.SOURCE, d, reason, stats["not_attempted"],
                )
                break
            consecutive[adapter.STORE_KEY] = 0
            stats["written"] += 1
        return stats


def _taipei_today() -> date:
    from app.taiwan.realtime.calendar import taipei_today

    return taipei_today()


def main(argv: Sequence[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Backfill the official Taiwan session bases")
    parser.add_argument("--since", required=True, help="First source date YYYY-MM-DD")
    parser.add_argument("--until", help="Last source date YYYY-MM-DD (defaults to today)")
    parser.add_argument("--source", choices=(*SOURCES, "all"), default="all")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SEC, help="Seconds between requests")
    parser.add_argument(
        "--resumptions", action="store_true", help="Also refetch the TPEx files resumption bases need (resumption_dates)"
    )
    parser.add_argument("--dry-run", action="store_true", help="With --resumptions, list those dates without fetching")
    args = parser.parse_args(argv)
    if args.delay < 0:
        parser.error("--delay must not be negative")
    if args.dry_run and not args.resumptions:
        parser.error("--dry-run needs --resumptions")
    if args.resumptions and args.source == TWSE:
        parser.error("--resumptions refetches TPEx files only")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    since = date.fromisoformat(args.since)
    until = date.fromisoformat(args.until) if args.until else _taipei_today()
    service = TaiwanSessionBaseRefreshService()
    if args.resumptions:
        dates = service.resumption_dates(since, until)
        if args.dry_run:
            print(json.dumps({"requests": len(dates), "dates": [str(d) for d in dates]}, indent=2))
            return
        stats = service.refresh(since, until, sources=(TPEX,), delay=args.delay, force=dates)
    else:
        sources = SOURCES if args.source == "all" else (args.source,)
        stats = service.refresh(since, until, sources=sources, delay=args.delay)
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    if stats["failed"] or stats["stopped_at"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
