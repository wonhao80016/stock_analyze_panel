"""Taiwan point-in-time fundamentals with strict availability semantics.

Official MOPS open data currently exposes a report date (``出表日期``), not a
verifiable publication time.  Such live records are retained with provenance but
``available_at=None`` and therefore cannot enter an as-of backtest.
"""
# ruff: noqa: RUF001 -- official field names contain full-width punctuation.
from __future__ import annotations

import atexit
import re
import threading
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from app.taiwan.providers.snapshot_provider import get_with_retry
from app.taiwan.providers.taiwan_values import MISSING_VALUES, TAIPEI, parse_number, parse_taiwan_date
from app.taiwan.realtime.calendar import taipei_today

STATEMENT_CATEGORIES = ("ci", "basi", "bd", "fh", "ins", "mim")
NORMALIZED_STATUSES = {
    "official", "third_party", "third_party_fallback", "unsupported",
    "data_insufficient", "stale", "schema_changed", "error",
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(value)
    if result is not None and result.tzinfo is None:
        raise ValueError("fundamental timestamps must be timezone-aware")
    return result


def thousand_twd(raw: object) -> int | None:
    value = parse_number(raw)
    return None if value is None else round(value * 1000)


def _whole_number(raw: object) -> int | None:
    value = parse_number(raw)
    return None if value is None else round(value)


# Currency labels seen in the official par-value text (TDR and KY listings use foreign ones).
_PAR_VALUE_CURRENCIES = (
    ("新台幣", "TWD"), ("新臺幣", "TWD"), ("美金", "USD"), ("美元", "USD"), ("USD", "USD"),
    ("港幣", "HKD"), ("HKD", "HKD"), ("泰銖", "THB"), ("人民幣", "CNY"), ("日圓", "JPY"),
)


def _par_value(raw: object) -> dict[str, Any]:
    """Par value as the official text, its currency, and the amount only when it is in NTD.

    Text without a currency label (some TDRs publish a bare "0.1") stays unpriced: it is not
    stated in NTD.
    """
    text = str(raw or "").strip()
    if text in MISSING_VALUES:
        return {"par_value_twd": None, "par_value_raw": None, "par_value_currency": None}
    currency = next((code for label, code in _PAR_VALUE_CURRENCIES if label in text), None)
    match = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)", text)
    amount = parse_number(match.group(1)) if match else None
    return {
        "par_value_twd": amount if currency == "TWD" else None,
        "par_value_raw": text,
        "par_value_currency": currency,
    }


def roc_month(raw: object) -> str:
    value = str(raw).strip()
    if len(value) not in (5, 6) or not value.isdigit():
        raise ValueError(f"invalid ROC month: {raw!r}")
    year, month = int(value[:-2]) + 1911, int(value[-2:])
    if not 1 <= month <= 12:
        raise ValueError(f"invalid ROC month: {raw!r}")
    return f"{year:04d}-{month:02d}"


@dataclass(frozen=True)
class FundamentalRecord:
    symbol: str
    dataset: str
    period_start: str | None
    period_end: str
    published_at: datetime | None
    available_at: datetime | None
    retrieved_at: datetime
    revision: str
    provider: str
    source: str
    source_url: str
    status: str
    normalized_unit: str
    raw_unit: str | None = None
    values: dict[str, Any] | None = None
    accounting_category: str | None = None
    statement_type: str = "unknown"

    def __post_init__(self) -> None:
        for value in (self.published_at, self.available_at, self.retrieved_at):
            _datetime(value)
        if self.status not in NORMALIZED_STATUSES:
            raise ValueError(f"invalid fundamental status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for name in ("published_at", "available_at", "retrieved_at"):
            result[name] = _iso(getattr(self, name))
        return result

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FundamentalRecord:
        data = {field.name: raw.get(field.name) for field in fields(cls)}
        for name in ("published_at", "available_at", "retrieved_at"):
            data[name] = _datetime(data[name])
        return cls(**data)


_shared_client: httpx.Client | None = None
_shared_client_lock = threading.Lock()


def _official_client() -> httpx.Client:
    global _shared_client
    with _shared_client_lock:
        if _shared_client is None:
            _shared_client = httpx.Client(timeout=30.0)
            atexit.register(_shared_client.close)
        return _shared_client


class TaiwanOfficialFundamentals:
    """Official TWSE/TPEx fundamentals feeding the Taiwan contract model.

    Each market-wide list is downloaded at most once per Taipei date per instance, so a list the
    exchange republishes later that day is not seen until the next date. Transport errors and HTTP
    5xx are retried (get_with_retry).
    """

    TWSE = "https://openapi.twse.com.tw/v1"
    TPEX = "https://www.tpex.org.tw/openapi/v1"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or _official_client()
        self._rows_cache: dict[tuple[str, date], tuple[list[dict[str, Any]], datetime]] = {}
        self._rows_cache_lock = threading.Lock()

    def monthly_revenue(self, symbol: str, exchange: str, *, security_type: str = "stock") -> FundamentalRecord:
        if security_type == "etf":
            return self._unsupported(symbol, "monthly_revenue")
        path = "opendata/t187ap05_L" if exchange == "TWSE" else "mopsfin_t187ap05_O"
        url, row, retrieved = self._company_row(symbol, exchange, path)
        values = {
            "revenue": thousand_twd(row.get("營業收入-當月營收")),
            "previous_month": thousand_twd(row.get("營業收入-上月營收")),
            "previous_year_month": thousand_twd(row.get("營業收入-去年當月營收")),
            "mom": parse_number(row.get("營業收入-上月比較增減(%)")),
            "yoy": parse_number(row.get("營業收入-去年同月增減(%)")),
            "cumulative": thousand_twd(row.get("累計營業收入-當月累計營收")),
        }
        period = roc_month(row.get("資料年月"))
        return self._record(symbol, "monthly_revenue", period, row, exchange, url, retrieved, values, "TWD", "thousand_TWD")

    def financial_statement(self, symbol: str, exchange: str, *, security_type: str = "stock") -> FundamentalRecord:
        if security_type == "etf":
            return self._unsupported(symbol, "financial_statement")
        base = "opendata/t187ap06_L_" if exchange == "TWSE" else "mopsfin_t187ap06_O_"
        balance = "opendata/t187ap07_L_" if exchange == "TWSE" else "mopsfin_t187ap07_O_"
        matches: list[tuple[str, str, dict[str, Any], datetime]] = []
        for category in STATEMENT_CATEGORIES:
            try:
                url, row, retrieved = self._company_row(symbol, exchange, base + category)
                matches.append((category, url, row, retrieved))
            except LookupError:
                continue
        if len(matches) != 1:
            raise LookupError(f"accounting category data_insufficient: matches={len(matches)}")
        category, income_url, income, retrieved = matches[0]
        balance_url, sheet, _ = self._company_row(symbol, exchange, balance + category)
        year = int(str(income.get("年度") or income.get("Year") or "").strip()) + 1911
        quarter = int(str(income.get("季別") or income.get("Season") or "").strip())
        if not 1 <= quarter <= 4:
            raise ValueError("invalid fiscal quarter")
        next_month = date(year + (quarter == 4), 1 if quarter == 4 else quarter * 3 + 1, 1)
        period_end = (next_month - timedelta(days=1)).isoformat()
        def amount(rows: dict[str, Any], *names: str) -> int | None:
            return thousand_twd(next((rows[name] for name in names if str(rows.get(name, "")).strip()), None))
        values = {
            "revenue": amount(income, "營業收入", "收益合計", "淨收益"),
            "gross_profit": amount(income, "營業毛利（毛損）淨額", "營業毛利（毛損）"),
            "operating_income": amount(income, "營業利益（損失）"),
            "net_income": amount(income, "本期淨利（淨損）", "本期稅後淨利（淨損）"),
            "cumulative_eps": parse_number(income.get("基本每股盈餘（元）")),
            "assets": amount(sheet, "資產總計", "資產總額"),
            "liabilities": amount(sheet, "負債總計", "負債總額"),
            "equity": amount(sheet, "權益總計", "權益總額"),
        }
        record = self._record(symbol, "financial_statement", period_end, income, exchange, income_url + " | " + balance_url, retrieved, values, "TWD", "thousand_TWD")
        return FundamentalRecord(**{**record.to_dict(), "published_at": record.published_at, "available_at": record.available_at, "retrieved_at": record.retrieved_at, "period_start": f"{year:04d}-01-01", "accounting_category": category})

    def valuation(self, symbol: str, exchange: str, *, security_type: str = "stock") -> FundamentalRecord:
        if security_type == "etf":
            return self._insufficient(symbol, "valuation", "official ETF valuation capability unverified")
        path = "exchangeReport/BWIBBU_ALL" if exchange == "TWSE" else "tpex_mainboard_peratio_analysis"
        url, row, retrieved = self._company_row(symbol, exchange, path)
        raw_date = row.get("Date") or row.get("日期")
        trade_date = parse_taiwan_date(str(raw_date)).isoformat()
        values = {
            "pe": parse_number(row.get("PEratio") or row.get("PriceEarningRatio")),
            "pb": parse_number(row.get("PBratio") or row.get("PriceBookRatio")),
            "dividend_yield": parse_number(row.get("DividendYield") or row.get("YieldRatio")),
        }
        return self._record(symbol, "valuation", trade_date, row, exchange, url, retrieved, values, "ratio", None)

    def share_capital(self, symbol: str, exchange: str, *, security_type: str = "stock") -> FundamentalRecord:
        """Return the official current/reference capital snapshot without historical inference."""
        if security_type == "etf":
            return self._unsupported(symbol, "share_capital_record")
        path = "opendata/t187ap03_L" if exchange == "TWSE" else "mopsfin_t187ap03_O"
        url, row, retrieved = self._company_row(symbol, exchange, path)
        report_date = parse_taiwan_date(str(row.get("出表日期") or row.get("Date"))).isoformat()
        values = {
            "total_shares": None,
            "issued_shares": _whole_number(
                row.get("已發行普通股數或TDR原股發行股數") or row.get("IssueShares")
            ),
            "float_shares": None,
            "capital_twd": _whole_number(
                row.get("實收資本額") or row.get("Paidin.Capital.NTDollars")
            ),
            **_par_value(row.get("普通股每股面額") or row.get("ParValueOfCommonStock")),
        }
        return self._record(
            symbol, "share_capital_record", report_date, row, exchange, url,
            retrieved, values, "structured", None,
        )

    def dividend_lifecycle_events(
        self,
        symbol: str,
        exchange: str,
        start_date: date,
        end_date: date,
        *,
        security_type: str = "stock",
    ):
        """Return exact-timestamp MOPS events, separate from dividend aggregates."""
        from app.taiwan.dividend_events import MOPSDividendLifecycleProvider

        return MOPSDividendLifecycleProvider(self.client).events(
            symbol,
            exchange,
            start_date,
            end_date,
            security_type=security_type,
        )

    def _record(self, symbol: str, dataset: str, period_end: str, raw: dict[str, Any], exchange: str, url: str, retrieved: datetime, values: dict[str, Any], unit: str, raw_unit: str | None) -> FundamentalRecord:
        revision = str(raw.get("出表日期") or raw.get("Date") or "").strip()
        # Date-only metadata cannot prove when during that date the record became available.
        return FundamentalRecord(symbol=symbol, dataset=dataset, period_start=None, period_end=period_end, published_at=None, available_at=None, retrieved_at=retrieved, revision=revision, provider="TWSE" if exchange == "TWSE" else "TPEx", source=f"{exchange.lower()}:{dataset}", source_url=url, status="data_insufficient", normalized_unit=unit, raw_unit=raw_unit, values=values)

    def _unsupported(self, symbol: str, dataset: str) -> FundamentalRecord:
        now = datetime.now(TAIPEI)
        return FundamentalRecord(symbol, dataset, None, "", None, None, now, "", "Taiwan", "taiwan:capability", "", "unsupported", "unavailable")

    def _insufficient(self, symbol: str, dataset: str, reason: str) -> FundamentalRecord:
        now = datetime.now(TAIPEI)
        return FundamentalRecord(symbol, dataset, None, "", None, None, now, "", "Taiwan", "taiwan:capability", "", "data_insufficient", "unavailable", values={"reason": reason})

    def _company_row(self, symbol: str, exchange: str, path: str) -> tuple[str, dict[str, Any], datetime]:
        url, rows, retrieved = self._rows(exchange, path)
        for row in rows:
            code = str(row.get("公司代號") or row.get("SecuritiesCompanyCode") or row.get("Code") or "").strip()
            if code == symbol.split(".")[0]:
                return url, row, retrieved
        raise LookupError(f"official {path} row not found for {symbol}")

    def _rows(self, exchange: str, path: str) -> tuple[str, list[dict[str, Any]], datetime]:
        url = f"{self.TWSE if exchange == 'TWSE' else self.TPEX}/{path}"
        key = (url, taipei_today())
        with self._rows_cache_lock:
            cached = self._rows_cache.get(key)
        if cached is not None:
            return url, cached[0], cached[1]
        response = get_with_retry(self.client, url)
        retrieved = datetime.now(TAIPEI)
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError(f"official {path} schema changed")
        if not rows:
            # Not cached, so an empty answer is asked for again on the next call instead of all day.
            return url, rows, retrieved
        with self._rows_cache_lock:
            for stale in [k for k in self._rows_cache if k[1] != key[1]]:
                del self._rows_cache[stale]
            self._rows_cache[key] = (rows, retrieved)
        return url, rows, retrieved
