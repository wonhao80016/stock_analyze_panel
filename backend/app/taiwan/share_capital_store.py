"""Issued common shares per Taiwan listing, for the turnover-rate denominator.

Taiwan publishes issued common shares (TWSE ``opendata/t187ap03_L``, TPEx
``mopsfin_t187ap03_O``) but not free float. docs/taiwan-share-capital-phase-6h.md
records why ``float_shares`` stayed null rather than being filled with a guess:
no official source gives historical snapshots, so applying today's count to a
past date would silently rewrite history.

This store does not reopen that decision. It supplies the *current* count only,
which is what a same-day screen needs — today's share count against today's
volume involves no look-ahead. Historical turnover stays fail-closed through
``share_capital.apply_historical_float_shares``, which nulls the denominator for
any Taiwan row that is not today.

The denominator is issued shares minus the privately placed shares the same
reports list (TWSE ``私募股數``, TPEx ``PrivateStock.shares``), which cannot trade
freely; a row without that field uses its issued shares. It is still not free
float: insiders' holdings stay in, so turnover reads lower than a float-based figure.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import polars as pl

from app.taiwan.providers.snapshot_provider import get_with_retry
from app.taiwan.providers.taiwan_values import TAIPEI, parse_taiwan_date
from app.taiwan.universe.models import TDR_SECURITY_CLASS, derive_security_class
from app.taiwan.universe.service import write_atomically

logger = logging.getLogger(__name__)

TWSE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"

SCHEMA = {
    "symbol": pl.Utf8,
    "issued_shares": pl.Int64,
    "report_date": pl.Utf8,
    "retrieved_at": pl.Utf8,
}
# Null when the report lacks the field; files written before it existed lack the column.
PRIVATE_SHARES = "private_shares"
STORED_SCHEMA = {**SCHEMA, PRIVATE_SHARES: pl.Int64}

# Keyed by path and mtime, so a fresh store instance reuses an unchanged file's frame.
_load_cache: dict[str, tuple[float, pl.DataFrame]] = {}


def _whole_shares(raw: Any) -> int | None:
    text = str(raw or "").replace(",", "").strip()
    if not text:
        return None
    try:
        value = int(float(text))
    except ValueError:
        return None
    return value if value > 0 else None


def _share_count(raw: Any) -> int | None:
    """A whole share count that may be zero; None when blank, unparsable or negative."""
    text = str(raw if raw is not None else "").replace(",", "").strip()
    try:
        value = int(float(text))
    except ValueError:
        return None
    return value if value >= 0 else None


# Keyed by the master cache file's (path, mtime_ns, size): realtime polls call as_instruments()
# every few seconds and must not rescan the master each time.
_master_tdrs: tuple[tuple[str, int, int], frozenset[str]] | None = None
_master_tdrs_lock = threading.Lock()


def _master_tdr_symbols() -> frozenset[str]:
    """TDR symbols of the security master, recomputed only when its cache file changes."""
    global _master_tdrs
    from app.taiwan.universe import get_security_master

    master = get_security_master()
    stat = os.stat(master.cache_path)
    signature = (str(master.cache_path), stat.st_mtime_ns, stat.st_size)
    with _master_tdrs_lock:
        cached = _master_tdrs
    if cached is not None and cached[0] == signature:
        return cached[1]
    frame = master.to_dataframe()
    tdrs = frozenset(frame.filter(pl.col("security_class") == TDR_SECURITY_CLASS)["symbol"].to_list())
    with _master_tdrs_lock:
        _master_tdrs = (signature, tdrs)
    return tdrs


def _tdr_symbols(symbols: list[str]) -> set[str]:
    """TDRs among *symbols*; without a readable master only six-digit 91xxxx codes are recognized."""
    try:
        return set(symbols) & _master_tdr_symbols()
    except Exception as e:
        logger.warning("taiwan share capital: no security master, TDRs matched by code only: %s", e)
        return {
            symbol for symbol in symbols
            if derive_security_class(symbol.split(".")[0], None, None) == TDR_SECURITY_CLASS
        }


class TaiwanShareCapitalStore:
    """Current issued-share counts, refreshed from the two official endpoints.

    Layout: <settings.data_dir>/taiwan/share_capital.parquet
    """

    def __init__(self, data_dir: Path | None = None, client: httpx.Client | None = None) -> None:
        if data_dir is None:
            from app.taiwan.data_root import taiwan_data_root

            data_dir = taiwan_data_root()
        self.path = Path(data_dir) / "share_capital.parquet"
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=30.0)
        return self._client

    def _fetch(
        self,
        url: str,
        suffix: str,
        code_keys: tuple[str, ...],
        share_keys: tuple[str, ...],
        private_keys: tuple[str, ...],
        date_keys: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        rows = get_with_retry(self.client, url).json()
        if not isinstance(rows, list):
            raise ValueError(f"official share capital schema changed: {url}")
        retrieved = datetime.now(TAIPEI).isoformat()
        out: list[dict[str, Any]] = []
        for row in rows:
            code = next((str(row.get(k) or "").strip() for k in code_keys if row.get(k)), "")
            shares = next((_whole_shares(row.get(k)) for k in share_keys if row.get(k)), None)
            if not code or shares is None:
                continue
            raw_date = next((row.get(k) for k in date_keys if row.get(k)), None)
            try:
                report_date = parse_taiwan_date(str(raw_date)).isoformat() if raw_date else ""
            except Exception:  # noqa: BLE001
                report_date = ""
            private = next((_share_count(row[k]) for k in private_keys if k in row), None)
            out.append({
                "symbol": f"{code}.{suffix}",
                "issued_shares": shares,
                "report_date": report_date,
                "retrieved_at": retrieved,
                PRIVATE_SHARES: private,
            })
        unknown = sum(row[PRIVATE_SHARES] is None for row in out)
        if unknown:
            logger.warning(
                "taiwan share capital: %d %s rows have no usable private placement count, "
                "their turnover divides by issued shares",
                unknown,
                suffix,
            )
        return out

    def refresh(self) -> int:
        """Fetch both exchanges and replace the file. Returns rows written.

        An exchange that fails, or answers without a usable row, keeps its rows from the
        previous file, so one outage does not drop that exchange's listings; the file is
        only replaced once at least one exchange returned rows.
        """
        rows: list[dict[str, Any]] = []
        failed: list[str] = []
        for url, suffix, code_keys, share_keys, private_keys, date_keys in (
            (
                TWSE_URL,
                "TWSE",
                ("公司代號",),
                ("已發行普通股數或TDR原股發行股數",),
                ("私募股數",),
                ("出表日期",),
            ),
            (
                TPEX_URL,
                "TPEX",
                ("SecuritiesCompanyCode", "公司代號"),
                ("IssueShares", "已發行普通股數或TDR原股發行股數"),
                ("PrivateStock.shares", "私募股數"),
                ("Date", "出表日期"),
            ),
        ):
            try:
                fetched = self._fetch(url, suffix, code_keys, share_keys, private_keys, date_keys)
                if not fetched:
                    # An empty list or renamed fields would otherwise drop that exchange's denominators.
                    raise ValueError(f"{suffix} returned no usable share counts")
                logger.info("taiwan share capital: %s returned %d rows", suffix, len(fetched))
                rows.extend(fetched)
            except Exception as e:  # noqa: BLE001
                logger.warning("taiwan share capital fetch failed for %s: %s", suffix, e)
                failed.append(suffix)

        if not rows:
            logger.warning("taiwan share capital refresh wrote nothing: both exchanges failed")
            return 0

        df = pl.DataFrame(rows, schema=STORED_SCHEMA)
        if failed:
            previous = self.load()
            if set(SCHEMA) <= set(previous.columns):
                if PRIVATE_SHARES not in previous.columns:
                    previous = previous.with_columns(pl.lit(None).alias(PRIVATE_SHARES))
                kept = previous.filter(
                    pl.any_horizontal([pl.col("symbol").str.ends_with(f".{s}") for s in failed])
                ).select(list(STORED_SCHEMA)).cast(STORED_SCHEMA)
                logger.info("taiwan share capital: kept %d previous rows for %s", kept.height, failed)
                df = pl.concat([df, kept])
        df = df.unique(subset=["symbol"], keep="last").sort("symbol")
        write_atomically(self.path, df.write_parquet)
        logger.info("taiwan share capital refreshed: %d symbols -> %s", df.height, self.path)
        return df.height

    def load(self) -> pl.DataFrame:
        if not self.path.exists():
            return pl.DataFrame(schema=SCHEMA)
        key = str(self.path)
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = None
        if mtime is not None:
            cached = _load_cache.get(key)
            if cached is not None and cached[0] == mtime:
                return cached[1]
        try:
            df = pl.read_parquet(self.path)
        except Exception as e:  # noqa: BLE001
            logger.warning("taiwan share capital read failed: %s", e)
            return pl.DataFrame(schema=SCHEMA)
        if mtime is not None:
            _load_cache[key] = (mtime, df)
        return df

    def as_instruments(self) -> pl.DataFrame:
        """The frame compute_enriched expects, with turnover_denominators as float_shares.

        The column is named float_shares because that is the pipeline's name for
        the turnover denominator; see this module's docstring for what the number
        actually counts. TDRs are left out: their official count is the underlying
        company's shares, not receipts, so their turnover stays null.
        """
        denominators = turnover_denominators(self.load())
        if denominators.is_empty():
            return denominators
        tdrs = _tdr_symbols(denominators["symbol"].to_list())
        return denominators.filter(~pl.col("symbol").is_in(list(tdrs)))


def turnover_denominators(frame: pl.DataFrame) -> pl.DataFrame:
    """symbol and float_shares: issued minus privately placed shares of a share-capital *frame*.

    Null unless positive; a row without a private placement count uses its issued shares.
    """
    # A file whose columns have drifted must read as "no denominator", not raise:
    # the publish would otherwise stop over a cosmetic field, losing indicators and signals too.
    if frame.is_empty() or not {"symbol", "issued_shares"} <= set(frame.columns):
        return pl.DataFrame(schema={"symbol": pl.Utf8, "float_shares": pl.Float64})
    private = pl.col(PRIVATE_SHARES) if PRIVATE_SHARES in frame.columns else pl.lit(None)
    private = private.cast(pl.Float64).fill_null(0.0)
    tradable = pl.col("issued_shares").cast(pl.Float64) - private
    return frame.select(
        pl.col("symbol"),
        pl.when(tradable > 0).then(tradable).otherwise(None).alias("float_shares"),
    )


# Below this share a session counts as lacking the field: turnover exists only from each
# session's own publish onward.
MIN_SESSION_COVERAGE = 0.5


def session_coverage(
    field: str,
    symbols: Sequence[str],
    close: np.ndarray,
    values: np.ndarray,
    store: TaiwanShareCapitalStore | None = None,
) -> np.ndarray:
    """Per session (row), the share of instruments able to hold *field* with a close and a value.

    NaN marks a session where none of them has a close.
    """
    return eligible_coverage(close, values, coverage_eligible(field, symbols, store))


def coverage_eligible(
    field: str,
    symbols: Sequence[str],
    store: TaiwanShareCapitalStore | None = None,
) -> np.ndarray:
    """Which of *symbols* can hold *field*.

    Only instruments with a turnover denominator in the current share-capital file can hold
    turnover_rate; without any denominator on file every instrument counts, as for any other field.
    """
    eligible = np.ones(len(symbols), dtype=bool)
    if field == "turnover_rate":
        denominators = (store or TaiwanShareCapitalStore()).as_instruments()
        known = set(denominators.filter(pl.col("float_shares").is_not_null())["symbol"].to_list())
        if known:
            eligible = np.fromiter((s in known for s in symbols), dtype=bool, count=len(symbols))
    return eligible


def eligible_coverage(close: np.ndarray, values: np.ndarray, eligible: np.ndarray) -> np.ndarray:
    """Per session (row), the share of *eligible* instruments with a close that have a value.

    NaN marks a session where none of them has a close.
    """
    listed = np.isfinite(close) & eligible
    counts = listed.sum(axis=1)
    held = (listed & np.isfinite(values)).sum(axis=1)
    return np.divide(held, counts, out=np.full(counts.shape, np.nan), where=counts > 0)
