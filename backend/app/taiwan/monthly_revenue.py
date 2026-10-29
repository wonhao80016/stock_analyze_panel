"""Display-only monthly revenue: archiving the official latest month and serving the stored series.

The archive (TaiwanMonthlyRevenueStore) has no availability time, so nothing here may feed
backtests, historical screens or any as-of query; responses carry usage_scope
``historical_reference`` and ``available_at=None``.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.taiwan.fundamentals import TaiwanOfficialFundamentals, roc_month, thousand_twd
from app.taiwan.monthly_revenue_store import TaiwanMonthlyRevenueStore
from app.taiwan.providers.taiwan_values import parse_number, parse_taiwan_date
from app.taiwan.symbol import parse_symbol
from app.taiwan.universe.models import MarketProfileBridge

logger = logging.getLogger(__name__)

# The same lists TaiwanOfficialFundamentals.monthly_revenue reads, so both share one download.
REVENUE_PATHS = {"TWSE": "opendata/t187ap05_L", "TPEX": "mopsfin_t187ap05_O"}
SOURCES = {"TWSE": "twse_openapi", "TPEX": "tpex_openapi"}
# Official amounts are thousand TWD; the (%) fields are already percent.
AMOUNT_FIELDS = {
    "revenue": "營業收入-當月營收",
    "previous_month_revenue": "營業收入-上月營收",
    "previous_year_revenue": "營業收入-去年當月營收",
    "cumulative_revenue": "累計營業收入-當月累計營收",
    "previous_year_cumulative_revenue": "累計營業收入-去年累計營收",
}
PERCENT_FIELDS = {
    "mom_pct": "營業收入-上月比較增減(%)",
    "yoy_pct": "營業收入-去年同月增減(%)",
    "cumulative_yoy_pct": "累計營業收入-前期比較增減(%)",
}
TARGET_MONTHS = 12


def _text(row: dict[str, Any], *names: str) -> str:
    return next((str(row[name]).strip() for name in names if str(row.get(name) or "").strip()), "")


def _report_date(raw: str) -> str | None:
    try:
        return parse_taiwan_date(raw).isoformat() if raw else None
    except ValueError:
        return None


def parse_revenue_rows(
    exchange: str, rows: list[dict[str, Any]], *, source_url: str, retrieved_at: datetime
) -> list[dict[str, Any]]:
    """Store rows for one official monthly revenue list, amounts converted to TWD.

    Rows without a code or a valid revenue month and rows with a malformed number are skipped; a
    blank or absent field is stored as null.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        code = _text(row, "公司代號", "SecuritiesCompanyCode", "Code")
        try:
            year, month = (int(part) for part in roc_month(_text(row, "資料年月")).split("-"))
        except ValueError:
            continue
        if not code:
            continue
        try:
            values: dict[str, Any] = {
                name: thousand_twd(row.get(official)) for name, official in AMOUNT_FIELDS.items()
            }
            values.update({name: parse_number(row.get(official)) for name, official in PERCENT_FIELDS.items()})
        except ValueError as exc:
            logger.warning("taiwan monthly revenue: %s.%s skipped: %s", code, exchange, exc)
            continue
        out.append({
            "symbol": f"{code}.{exchange}",
            "exchange": exchange,
            "year": year,
            "month": month,
            **values,
            "report_date": _report_date(_text(row, "出表日期", "Date")),
            "source": SOURCES[exchange],
            "source_url": source_url,
            "last_retrieved_at": retrieved_at.isoformat(),
        })
    return out


@dataclass
class MonthlyRefreshReport:
    added: int = 0
    unchanged: int = 0
    revised: int = 0
    ambiguous: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def refresh_latest_month(
    fundamentals: TaiwanOfficialFundamentals | None = None,
    store: TaiwanMonthlyRevenueStore | None = None,
) -> MonthlyRefreshReport:
    """Upsert both exchanges' latest official monthly revenue into the display-only archive.

    Lists come through TaiwanOfficialFundamentals' per-day cache, so a day already holding them
    sends no request. A failed list is skipped; a symbol listed twice for one month is left out.
    A failed store write raises.
    """
    if fundamentals is None:
        from app.taiwan.providers.hybrid_provider import _official_fundamentals

        fundamentals = _official_fundamentals()
    store = store or TaiwanMonthlyRevenueStore()
    report = MonthlyRefreshReport()
    by_key: dict[tuple[str, int, int], list[dict[str, Any]]] = defaultdict(list)
    for exchange in ("TWSE", "TPEX"):
        try:
            url, rows, retrieved = fundamentals.market_rows(exchange, REVENUE_PATHS[exchange])
        except Exception as exc:
            note = f"{exchange}: {type(exc).__name__}: {exc}"
            logger.warning("taiwan monthly revenue: list skipped: %s", note)
            report.failures.append(note)
            continue
        for row in parse_revenue_rows(exchange, rows, source_url=url, retrieved_at=retrieved):
            by_key[(row["symbol"], row["year"], row["month"])].append(row)
    report.ambiguous = sorted({key[0] for key, rows in by_key.items() if len(rows) > 1})
    if report.ambiguous:
        logger.warning("taiwan monthly revenue: listed more than once %s", report.ambiguous)
    unique = [rows[0] for rows in by_key.values() if len(rows) == 1]
    result = store.upsert(unique)
    report.added, report.unchanged, report.revised = result.added, result.unchanged, len(result.revised)
    return report


class MonthlyRevenuePeriod(BaseModel):
    """Amounts in TWD, *_pct in percent (12.5 means 12.5%), all as the official list stated."""

    year: int
    month: int
    period: str
    revenue: int | None = None
    previous_month_revenue: int | None = None
    previous_year_revenue: int | None = None
    cumulative_revenue: int | None = None
    previous_year_cumulative_revenue: int | None = None
    mom_pct: float | None = None
    yoy_pct: float | None = None
    cumulative_yoy_pct: float | None = None
    report_date: str | None = None
    source: str | None = None
    source_url: str | None = None
    first_retrieved_at: str | None = None
    last_retrieved_at: str | None = None
    revised_at: str | None = None


class MonthlyRevenueComparison(BaseModel):
    """The previous-month and year-ago revenue printed in the latest stored month's own report."""

    period: str
    previous_month: str
    previous_month_revenue: int | None = None
    previous_year_month: str
    previous_year_revenue: int | None = None


class TaiwanMonthlyRevenueResponse(BaseModel):
    symbol: str
    exchange: str
    applicable: bool
    not_applicable_reason: Literal["etf", "tdr"] | None = None
    usage_scope: Literal["historical_reference"] = "historical_reference"
    display_only: Literal[True] = True
    available_at: None = None
    target_months: int = TARGET_MONTHS
    accumulating: bool = True
    months: list[MonthlyRevenuePeriod] = Field(default_factory=list)
    latest_comparison: MonthlyRevenueComparison | None = None


def _period(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _comparison(latest: MonthlyRevenuePeriod) -> MonthlyRevenueComparison:
    previous = (latest.year - 1, 12) if latest.month == 1 else (latest.year, latest.month - 1)
    return MonthlyRevenueComparison(
        period=latest.period,
        previous_month=_period(*previous),
        previous_month_revenue=latest.previous_month_revenue,
        previous_year_month=_period(latest.year - 1, latest.month),
        previous_year_revenue=latest.previous_year_revenue,
    )


class TaiwanMonthlyRevenueService:
    """Reads the display-only archive for one listing; never fetches."""

    def __init__(self, store: TaiwanMonthlyRevenueStore | None = None, security_master=None) -> None:
        self.store = store or TaiwanMonthlyRevenueStore()
        if security_master is None:
            from app.taiwan.universe import get_security_master

            security_master = get_security_master()
        self.security_master = security_master

    def get(self, raw_symbol: str) -> TaiwanMonthlyRevenueResponse:
        symbol = parse_symbol(raw_symbol)
        instrument = self.security_master.get_instrument(symbol.canonical)
        instrument_type = getattr(instrument, "instrument_type", None)
        if instrument_type not in {"stock", "etf"}:
            raise LookupError(f"supported security type not found for {symbol.canonical}")
        base = {"symbol": symbol.canonical, "exchange": symbol.exchange.value}
        if instrument_type == "etf":
            return TaiwanMonthlyRevenueResponse(**base, applicable=False, not_applicable_reason="etf")
        if MarketProfileBridge.is_tdr(instrument):
            return TaiwanMonthlyRevenueResponse(**base, applicable=False, not_applicable_reason="tdr")
        months = [
            MonthlyRevenuePeriod(
                period=_period(row["year"], row["month"]),
                **{name: row.get(name) for name in MonthlyRevenuePeriod.model_fields if name != "period"},
            )
            for row in self.store.rows_for(symbol.canonical)
        ]
        return TaiwanMonthlyRevenueResponse(
            **base,
            applicable=True,
            accumulating=len(months) < TARGET_MONTHS,
            months=months,
            latest_comparison=_comparison(months[0]) if months else None,
        )


def get_taiwan_monthly_revenue_service() -> TaiwanMonthlyRevenueService:
    return TaiwanMonthlyRevenueService()
