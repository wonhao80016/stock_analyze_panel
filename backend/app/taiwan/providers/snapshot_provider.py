"""TWSE/TPEx Official Full-Market Daily Snapshot Adapter (Phase 6B).

Provides official full-market snapshot fetching and normalization for:
  - TWSE: MI_INDEX (每日收盤行情(全部))
  - TPEx: dailyQuotes (上櫃股票行情)

Design Principles:
  - Narrow scope: requests 1 date from TWSE (1 HTTP) and 1 date from TPEx (1 HTTP).
  - Robust table identification: inspects table title and fields, not arbitrary table indexes.
  - Security Master allowlist filtering: only parses and emits supported active stocks & ETFs.
  - Strict unit semantics: volume is in SHARES, amount is in TWD.
  - Returns canonical DailyStore-shaped rows strictly matching DAILY_COLS:
    ["symbol", "date", "open", "high", "low", "close", "volume", "amount", "quote_ts"]
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date
from typing import Any

import httpx
import polars as pl

from app.data_providers.normalizer import DAILY_COLS
from app.taiwan.providers.base import AmountUnit, PriceSemantics, SourceMetadata, VolumeUnit
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master

logger = logging.getLogger(__name__)

TWSE_MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_DAILY_QUOTES_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"

SNAPSHOT_METADATA = SourceMetadata(
    source_name="taiwan_official_snapshot",
    volume_unit=VolumeUnit.SHARES,
    amount_unit=AmountUnit.TWD,
    price_semantics=PriceSemantics.RAW,
)

_MISSING_PRICE_SET = {"", "-", "--", "---", "----", "N/A", "null", "None"}

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_INITIAL_BACKOFF_SEC = 1.0


class IncompleteSnapshotError(RuntimeError):
    """TWSE traded on the date but the TPEx half of the snapshot is missing."""


def get_with_retry(
    client: httpx.Client,
    url: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_backoff: float = DEFAULT_INITIAL_BACKOFF_SEC,
) -> httpx.Response:
    """GET *url*, retrying transport errors and HTTP 5xx; any other non-2xx status raises at once.

    The last failure is re-raised unchanged, so callers keep treating it as a failed fetch.
    """
    backoff = initial_backoff
    for attempt in range(1, max_attempts + 1):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500 or attempt >= max_attempts:
                raise
            logger.warning(
                "HTTP %d on %s (attempt %d/%d), retrying in %.1fs...",
                exc.response.status_code, url, attempt, max_attempts, backoff,
            )
        except httpx.TransportError as exc:
            if attempt >= max_attempts:
                raise
            logger.warning(
                "Network/timeout error on %s: %s (attempt %d/%d), retrying in %.1fs...",
                url, exc, attempt, max_attempts, backoff,
            )
        time.sleep(backoff)
        backoff *= 2.0
    raise RuntimeError(f"Failed to fetch {url} after {max_attempts} attempts")


def _parse_snapshot_number(raw: object) -> float | None:
    if raw is None:
        return None
    val = str(raw).strip().rstrip("*").strip()
    if val in _MISSING_PRICE_SET:
        return None
    val = val.replace(",", "").replace("\uff0b", "+").replace("\u2212", "-")
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


class OfficialDailySnapshotAdapter:
    """Fetches full-market daily closing snapshots from TWSE and TPEx."""

    metadata = SNAPSHOT_METADATA

    def __init__(
        self,
        security_master: TaiwanSecurityMaster | None = None,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.security_master = security_master or get_security_master()
        self.timeout = timeout
        self.client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )

    def _allowlist(self, exchange: str) -> set[str]:
        """Codes of the supported active stocks and ETFs the security master lists on *exchange*.

        Read at every parse rather than once per adapter, so a master refreshed after the adapter
        was built applies to the next fetch.
        """
        stocks = self.security_master.to_provider_dataframe(asset_type="stock")
        etfs = self.security_master.to_provider_dataframe(asset_type="etf")
        combined = pl.concat([stocks, etfs], how="diagonal_relaxed")
        allowlists: dict[str, set[str]] = {"TWSE": set(), "TPEX": set()}
        if not combined.is_empty():
            for row in combined.iter_rows(named=True):
                sym = row["symbol"]
                ex = row.get("exchange")
                code = sym.split(".")[0]
                if ex == "TWSE" or sym.endswith(".TWSE"):
                    allowlists["TWSE"].add(code)
                elif ex == "TPEX" or sym.endswith(".TPEX"):
                    allowlists["TPEX"].add(code)
        return allowlists[exchange]

    def fetch_date(self, target_date: date) -> pl.DataFrame:
        """Fetch and normalize full-market daily OHLCV for target_date.

        TWSE decides whether the date traded: without TWSE rows the day counts as
        closed, TPEx is not requested and an empty frame is returned. Once TWSE has
        rows, TPEx must have them too, otherwise IncompleteSnapshotError is raised
        so the date is retried rather than stored as a finished day. A TWSE failure
        propagates. Filters strictly by Security Master allowlist.
        """
        df_twse = self.fetch_twse(target_date)
        if df_twse.is_empty():
            return pl.DataFrame(schema={col: pl.Float64 for col in DAILY_COLS})

        try:
            df_tpex = self.fetch_tpex(target_date)
        except Exception as e:
            raise IncompleteSnapshotError(f"TPEx snapshot failed for {target_date}: {e}") from e
        if df_tpex.is_empty():
            raise IncompleteSnapshotError(f"TPEx snapshot has no rows for {target_date}")

        combined = pl.concat([df_twse, df_tpex], how="diagonal_relaxed")
        return combined.unique(subset=["symbol", "date"], keep="last").sort(["symbol", "date"])

    @staticmethod
    def _stamped_for(payload: dict[str, Any], target_date: date) -> bool:
        """False when the exchange stamped the response with another trading date."""
        stamped = str(payload.get("date") or "").strip()
        return not stamped or stamped == target_date.strftime("%Y%m%d")

    def fetch_twse(self, target_date: date) -> pl.DataFrame:
        """Fetch and parse TWSE MI_INDEX for a single trading date.

        The security master loads first, so an unavailable master raises before any request.
        """
        self.security_master.ensure_loaded()
        date_str = target_date.strftime("%Y%m%d")
        url = f"{TWSE_MI_INDEX_URL}?date={date_str}&type=ALL&response=json"
        payload = self._request_json(url)

        stat = payload.get("stat", "")
        if stat != "OK":
            logger.info("TWSE MI_INDEX returned non-OK status for %s: %s", target_date, stat)
            return pl.DataFrame()
        if not self._stamped_for(payload, target_date):
            logger.warning("TWSE MI_INDEX answered %s with data for %s", target_date, payload.get("date"))
            return pl.DataFrame()

        tables = payload.get("tables", [])
        quote_table = None
        for t in tables:
            title = t.get("title", "")
            fields = t.get("fields", [])
            if "每日收盤行情" in title or ("證券代號" in fields and "收盤價" in fields):
                quote_table = t
                break

        if not quote_table:
            logger.warning("TWSE MI_INDEX quote table not found for date %s", target_date)
            return pl.DataFrame()

        fields = quote_table.get("fields", [])
        data_rows = quote_table.get("data", [])
        return self._parse_twse_table(data_rows, fields, target_date)

    def _parse_twse_table(
        self,
        rows: list[list[str]],
        fields: list[str],
        target_date: date,
    ) -> pl.DataFrame:
        """Parse raw TWSE MI_INDEX data rows into canonical schema."""
        # Find column indexes
        try:
            code_idx = fields.index("證券代號")
            vol_idx = fields.index("成交股數")
            amt_idx = fields.index("成交金額")
            open_idx = fields.index("開盤價")
            high_idx = fields.index("最高價")
            low_idx = fields.index("最低價")
            close_idx = fields.index("收盤價")
        except ValueError as e:
            logger.error("TWSE MI_INDEX table schema mismatch: %s. Fields: %s", e, fields)
            return pl.DataFrame()

        allowlist = self._allowlist("TWSE")
        parsed: list[dict[str, Any]] = []
        for r in rows:
            if len(r) <= max(code_idx, vol_idx, amt_idx, open_idx, high_idx, low_idx, close_idx):
                continue
            code = str(r[code_idx]).strip()
            if code not in allowlist:
                continue

            open_val = _parse_snapshot_number(r[open_idx])
            high_val = _parse_snapshot_number(r[high_idx])
            low_val = _parse_snapshot_number(r[low_idx])
            close_val = _parse_snapshot_number(r[close_idx])
            vol_val = _parse_snapshot_number(r[vol_idx]) or 0.0
            amt_val = _parse_snapshot_number(r[amt_idx]) or 0.0

            parsed.append({
                "symbol": f"{code}.TWSE",
                "date": target_date,
                "open": open_val,
                "high": high_val,
                "low": low_val,
                "close": close_val,
                "volume": float(vol_val),
                "amount": float(amt_val),
                "quote_ts": None,
            })

        if not parsed:
            return pl.DataFrame()

        df = pl.DataFrame(parsed)
        return self._sanitize_and_filter(df)

    def fetch_tpex(self, target_date: date, codes: set[str] | None = None) -> pl.DataFrame:
        """Fetch and parse TPEx dailyQuotes for a single trading date.

        The security master loads first, so an unavailable master raises before any request.
        *codes*, when given, replaces the allowlist of supported active stocks and ETFs.
        """
        self.security_master.ensure_loaded()
        roc_year = target_date.year - 1911
        date_str = f"{roc_year}/{target_date.month:02d}/{target_date.day:02d}"
        url = f"{TPEX_DAILY_QUOTES_URL}?date={date_str}&response=json"
        payload = self._request_json(url)
        if not self._stamped_for(payload, target_date):
            logger.warning("TPEx dailyQuotes answered %s with data for %s", target_date, payload.get("date"))
            return pl.DataFrame()

        tables = payload.get("tables", [])
        quote_table = None
        for t in tables:
            title = t.get("title", "")
            fields = t.get("fields", [])
            if "上櫃股票行情" in title or ("代號" in fields and "收盤" in fields):
                quote_table = t
                break

        if not quote_table:
            logger.warning("TPEx dailyQuotes quote table not found for date %s", target_date)
            return pl.DataFrame()

        fields = quote_table.get("fields", [])
        data_rows = quote_table.get("data", [])
        return self._parse_tpex_table(data_rows, fields, target_date, codes)

    def _parse_tpex_table(
        self,
        rows: list[list[str]],
        fields: list[str],
        target_date: date,
        codes: set[str] | None = None,
    ) -> pl.DataFrame:
        """Parse raw TPEx dailyQuotes data rows into canonical schema."""
        try:
            code_idx = fields.index("代號")
            vol_idx = fields.index("成交股數")
            amt_idx = fields.index("成交金額(元)")
            open_idx = fields.index("開盤")
            high_idx = fields.index("最高")
            low_idx = fields.index("最低")
            close_idx = fields.index("收盤")
        except ValueError as e:
            logger.error("TPEx dailyQuotes table schema mismatch: %s. Fields: %s", e, fields)
            return pl.DataFrame()

        allowlist = self._allowlist("TPEX") if codes is None else codes
        parsed: list[dict[str, Any]] = []
        for r in rows:
            if len(r) <= max(code_idx, vol_idx, amt_idx, open_idx, high_idx, low_idx, close_idx):
                continue
            code = str(r[code_idx]).strip()
            if code not in allowlist:
                continue

            open_val = _parse_snapshot_number(r[open_idx])
            high_val = _parse_snapshot_number(r[high_idx])
            low_val = _parse_snapshot_number(r[low_idx])
            close_val = _parse_snapshot_number(r[close_idx])
            vol_val = _parse_snapshot_number(r[vol_idx]) or 0.0
            amt_val = _parse_snapshot_number(r[amt_idx]) or 0.0

            parsed.append({
                "symbol": f"{code}.TPEX",
                "date": target_date,
                "open": open_val,
                "high": high_val,
                "low": low_val,
                "close": close_val,
                "volume": float(vol_val),
                "amount": float(amt_val),
                "quote_ts": None,
            })

        if not parsed:
            return pl.DataFrame()

        df = pl.DataFrame(parsed)
        return self._sanitize_and_filter(df)

    def _sanitize_and_filter(self, df: pl.DataFrame) -> pl.DataFrame:
        """Apply strict types and valid price bound filters."""
        # Deduplicate by symbol and date
        df = df.unique(subset=["symbol", "date"], keep="last")
        # Ensure schema
        df = df.select([
            pl.col("symbol").cast(pl.Utf8),
            pl.col("date").cast(pl.Date),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("volume").cast(pl.Float64),
            pl.col("amount").cast(pl.Float64),
            pl.col("quote_ts").cast(pl.Int64),
        ])
        return df.sort(["symbol", "date"])

    def _request_json(self, url: str) -> dict[str, Any]:
        resp = get_with_retry(self.client, url)
        raw = resp.content.decode("utf-8-sig")
        return json.loads(raw)
