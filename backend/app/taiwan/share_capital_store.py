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

The number is issued shares, not free float. Every Taiwan turnover figure is
therefore a share of issued capital: it reads lower than a float-based figure,
and the more of a company its insiders hold, the larger that gap. Taiwan market
data conventionally uses issued shares for the same reason — there is nothing
else published — so figures stay comparable between listings even though none of
them is a true float turnover.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from app.taiwan.providers.taiwan_values import TAIPEI, parse_taiwan_date

logger = logging.getLogger(__name__)

TWSE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"

SCHEMA = {
    "symbol": pl.Utf8,
    "issued_shares": pl.Int64,
    "report_date": pl.Utf8,
    "retrieved_at": pl.Utf8,
}


def _whole_shares(raw: Any) -> int | None:
    text = str(raw or "").replace(",", "").strip()
    if not text:
        return None
    try:
        value = int(float(text))
    except ValueError:
        return None
    return value if value > 0 else None


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

    def _fetch(self, url: str, suffix: str, code_keys: tuple[str, ...], share_keys: tuple[str, ...],
               date_keys: tuple[str, ...]) -> list[dict[str, Any]]:
        rows = self.client.get(url).json()
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
            out.append({
                "symbol": f"{code}.{suffix}",
                "issued_shares": shares,
                "report_date": report_date,
                "retrieved_at": retrieved,
            })
        return out

    def refresh(self) -> int:
        """Fetch both exchanges and replace the file. Returns rows written.

        One exchange failing does not discard the other: a partial refresh still
        prices the listings it covers, and the previous file is only replaced
        once at least one exchange answered.
        """
        rows: list[dict[str, Any]] = []
        for url, suffix, code_keys, share_keys, date_keys in (
            (TWSE_URL, "TWSE", ("公司代號",), ("已發行普通股數或TDR原股發行股數",), ("出表日期",)),
            (TPEX_URL, "TPEX", ("SecuritiesCompanyCode", "公司代號"),
             ("IssueShares", "已發行普通股數或TDR原股發行股數"), ("Date", "出表日期")),
        ):
            try:
                fetched = self._fetch(url, suffix, code_keys, share_keys, date_keys)
                logger.info("taiwan share capital: %s returned %d rows", suffix, len(fetched))
                rows.extend(fetched)
            except Exception as e:  # noqa: BLE001
                logger.warning("taiwan share capital fetch failed for %s: %s", suffix, e)

        if not rows:
            logger.warning("taiwan share capital refresh wrote nothing: both exchanges failed")
            return 0

        df = pl.DataFrame(rows, schema=SCHEMA).unique(subset=["symbol"], keep="last").sort("symbol")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".parquet.tmp")
        df.write_parquet(tmp)
        tmp.replace(self.path)
        logger.info("taiwan share capital refreshed: %d symbols -> %s", df.height, self.path)
        return df.height

    def load(self) -> pl.DataFrame:
        if not self.path.exists():
            return pl.DataFrame(schema=SCHEMA)
        try:
            return pl.read_parquet(self.path)
        except Exception as e:  # noqa: BLE001
            logger.warning("taiwan share capital read failed: %s", e)
            return pl.DataFrame(schema=SCHEMA)

    def as_instruments(self) -> pl.DataFrame:
        """The frame compute_enriched expects, with issued shares as the denominator.

        The column is named float_shares because that is the pipeline's name for
        the turnover denominator; see this module's docstring for what the number
        actually counts.
        """
        df = self.load()
        # A file whose columns have drifted must read as "no denominator", not raise:
        # the caller publishes the whole Taiwan enriched table and would otherwise
        # stop over a cosmetic field, losing indicators and signals too.
        if df.is_empty() or not {"symbol", "issued_shares"} <= set(df.columns):
            return pl.DataFrame(schema={"symbol": pl.Utf8, "float_shares": pl.Float64})
        return df.select(
            pl.col("symbol"),
            pl.col("issued_shares").cast(pl.Float64).alias("float_shares"),
        )
