"""Display-only quarterly statements: archiving the official latest quarter and deriving its figures.

The archive (TaiwanQuarterlyFinancialStore) has no availability time, so nothing here may feed
backtests, historical screens or any as-of query; responses carry usage_scope
``historical_reference`` and ``available_at=None``.
"""
# ruff: noqa: RUF001 -- official field names contain full-width punctuation.
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.taiwan.fundamentals import STATEMENT_CATEGORIES, TaiwanOfficialFundamentals, thousand_twd
from app.taiwan.providers.taiwan_values import parse_number
from app.taiwan.quarterly_financials_store import TaiwanQuarterlyFinancialStore
from app.taiwan.symbol import parse_symbol
from app.taiwan.universe.models import MarketProfileBridge

logger = logging.getLogger(__name__)

INCOME_PATHS = {"TWSE": "opendata/t187ap06_L_", "TPEX": "mopsfin_t187ap06_O_"}
BALANCE_PATHS = {"TWSE": "opendata/t187ap07_L_", "TPEX": "mopsfin_t187ap07_O_"}
SOURCES = {"TWSE": "twse_openapi", "TPEX": "tpex_openapi"}

# Official names per field across the six accounting categories, first non-blank wins.
INCOME_FIELDS: dict[str, tuple[str, ...]] = {
    "revenue": ("營業收入", "收益合計", "淨收益", "收益", "收入"),
    "gross_profit": ("營業毛利（毛損）淨額", "營業毛利（毛損）"),
    "operating_income": ("營業利益（損失）", "營業利益"),
    "net_income": ("本期淨利（淨損）", "本期稅後淨利（淨損）"),
    "parent_net_income": ("淨利（淨損）歸屬於母公司業主", "淨利（損）歸屬於母公司業主"),
}
BALANCE_FIELDS: dict[str, tuple[str, ...]] = {
    "assets": ("資產總計", "資產總額"),
    "liabilities": ("負債總計", "負債總額"),
    "equity": ("權益總計", "權益總額"),
    "parent_equity": ("歸屬於母公司業主之權益合計", "歸屬於母公司業主權益合計", "歸屬於母公司業主之權益"),
}
EPS_FIELD = "基本每股盈餘（元）"
TARGET_QUARTERS = 8


def _text(row: dict[str, Any], *names: str) -> str:
    return next((str(row[name]).strip() for name in names if str(row.get(name) or "").strip()), "")


def _first_amount(row: dict[str, Any], names: tuple[str, ...]) -> int | None:
    return thousand_twd(_text(row, *names) or None)


def _period(row: dict[str, Any]) -> tuple[int, int] | None:
    year, quarter = _text(row, "年度", "Year"), _text(row, "季別", "Season")
    if not (year.isdigit() and quarter.isdigit()) or not 1 <= int(quarter) <= 4:
        return None
    return int(year) + 1911, int(quarter)


def _period_end(year: int, quarter: int) -> str:
    next_month = date(year + (quarter == 4), 1 if quarter == 4 else quarter * 3 + 1, 1)
    return (next_month - timedelta(days=1)).isoformat()


def _code(row: dict[str, Any]) -> str:
    return _text(row, "公司代號", "SecuritiesCompanyCode", "Code")


def parse_statement_rows(
    exchange: str,
    category: str,
    income_rows: list[dict[str, Any]],
    balance_rows: list[dict[str, Any]],
    *,
    income_url: str,
    balance_url: str,
    retrieved_at: datetime,
) -> list[dict[str, Any]]:
    """Store rows for one category's income and balance lists, amounts converted to TWD.

    Rows without a code or a valid period (placeholder rows of an empty category) and rows with a
    malformed number are skipped. A balance row of another period counts as missing.
    """
    balances = {_code(row): row for row in balance_rows if _code(row)}
    out: list[dict[str, Any]] = []
    for income in income_rows:
        code, period = _code(income), _period(income)
        if not code or period is None:
            continue
        balance = balances.get(code)
        if balance is not None and _period(balance) != period:
            balance = None
        try:
            values = {name: _first_amount(income, names) for name, names in INCOME_FIELDS.items()}
            values["cumulative_eps"] = parse_number(income.get(EPS_FIELD))
            values.update(
                {name: _first_amount(balance or {}, names) for name, names in BALANCE_FIELDS.items()}
            )
        except ValueError as exc:
            logger.warning("taiwan quarterly financials: %s.%s skipped: %s", code, exchange, exc)
            continue
        out.append({
            "symbol": f"{code}.{exchange}",
            "exchange": exchange,
            "accounting_category": category,
            "fiscal_year": period[0],
            "fiscal_quarter": period[1],
            "period_end": _period_end(*period),
            **values,
            "source": SOURCES[exchange],
            "source_url": f"{income_url} | {balance_url}",
            "last_retrieved_at": retrieved_at.isoformat(),
        })
    return out


@dataclass
class QuarterlyRefreshReport:
    added: int = 0
    unchanged: int = 0
    revised: int = 0
    ambiguous: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def refresh_latest_quarter(
    fundamentals: TaiwanOfficialFundamentals | None = None,
    store: TaiwanQuarterlyFinancialStore | None = None,
) -> QuarterlyRefreshReport:
    """Upsert the latest official quarter of both exchanges into the display-only archive.

    Lists come through TaiwanOfficialFundamentals' per-day cache, so a day already holding them
    sends no request. A category whose income or balance list fails is skipped; a symbol listed
    under more than one category is left out. A failed store write raises.
    """
    if fundamentals is None:
        from app.taiwan.providers.hybrid_provider import _official_fundamentals

        fundamentals = _official_fundamentals()
    store = store or TaiwanQuarterlyFinancialStore()
    report = QuarterlyRefreshReport()
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for exchange in ("TWSE", "TPEX"):
        for category in STATEMENT_CATEGORIES:
            try:
                income_url, income, retrieved = fundamentals.market_rows(
                    exchange, INCOME_PATHS[exchange] + category
                )
                balance_url, balance, _ = fundamentals.market_rows(
                    exchange, BALANCE_PATHS[exchange] + category
                )
            except Exception as exc:
                note = f"{exchange} {category}: {type(exc).__name__}: {exc}"
                logger.warning("taiwan quarterly financials: list skipped: %s", note)
                report.failures.append(note)
                continue
            rows = parse_statement_rows(
                exchange, category, income, balance,
                income_url=income_url, balance_url=balance_url, retrieved_at=retrieved,
            )
            for row in rows:
                by_symbol[row["symbol"]].append(row)
    report.ambiguous = sorted(symbol for symbol, rows in by_symbol.items() if len(rows) > 1)
    if report.ambiguous:
        logger.warning("taiwan quarterly financials: several categories list %s", report.ambiguous)
    unique = [rows[0] for rows in by_symbol.values() if len(rows) == 1]
    result = store.upsert(unique)
    report.added, report.unchanged, report.revised = result.added, result.unchanged, len(result.revised)
    return report


class QuarterlyFigures(BaseModel):
    """Amounts in TWD, EPS in TWD per share, ratios in percent (53.2 means 53.2%)."""

    revenue: int | None = None
    gross_profit: int | None = None
    operating_income: int | None = None
    net_income: int | None = None
    parent_net_income: int | None = None
    eps: float | None = None
    gross_margin_pct: float | None = None
    operating_margin_pct: float | None = None
    net_margin_pct: float | None = None
    roe_pct: float | None = None


class QuarterlyBalance(BaseModel):
    assets: int | None = None
    liabilities: int | None = None
    equity: int | None = None
    parent_equity: int | None = None


class QuarterlyYearAgo(BaseModel):
    fiscal_year: int
    fiscal_quarter: int
    cumulative: QuarterlyFigures
    single: QuarterlyFigures


class QuarterlyPeriod(BaseModel):
    fiscal_year: int
    fiscal_quarter: int
    period_end: str
    accounting_category: str | None = None
    cumulative: QuarterlyFigures
    single: QuarterlyFigures
    single_missing_reason: Literal["previous_quarter_missing"] | None = None
    roe_missing_reason: Literal["opening_equity_missing", "closing_equity_missing"] | None = None
    balance: QuarterlyBalance
    year_ago: QuarterlyYearAgo | None = None
    source: str | None = None
    source_url: str | None = None
    first_retrieved_at: str | None = None
    last_retrieved_at: str | None = None
    revised_at: str | None = None


class AnnualEps(BaseModel):
    fiscal_year: int
    eps: float | None = None


class TaiwanQuarterlyFinancialsResponse(BaseModel):
    symbol: str
    exchange: str
    applicable: bool
    not_applicable_reason: Literal["etf", "tdr"] | None = None
    usage_scope: Literal["historical_reference"] = "historical_reference"
    display_only: Literal[True] = True
    available_at: None = None
    target_quarters: int = TARGET_QUARTERS
    accumulating: bool = True
    quarters: list[QuarterlyPeriod] = Field(default_factory=list)
    annual_eps: list[AnnualEps] = Field(default_factory=list)


def _ratio_pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator * 100


def _difference(current: float | None, previous: float | None) -> float | None:
    return None if current is None or previous is None else current - previous


def _figures(amounts: dict[str, Any]) -> QuarterlyFigures:
    revenue = amounts.get("revenue")
    return QuarterlyFigures(
        revenue=revenue,
        gross_profit=amounts.get("gross_profit"),
        operating_income=amounts.get("operating_income"),
        net_income=amounts.get("net_income"),
        parent_net_income=amounts.get("parent_net_income"),
        eps=amounts.get("eps"),
        gross_margin_pct=_ratio_pct(amounts.get("gross_profit"), revenue),
        operating_margin_pct=_ratio_pct(amounts.get("operating_income"), revenue),
        net_margin_pct=_ratio_pct(amounts.get("net_income"), revenue),
    )


_INCOME = ("revenue", "gross_profit", "operating_income", "net_income", "parent_net_income")


def _roe_missing_reason(opening: int | None, closing: int | None) -> str | None:
    if opening is None:
        return "opening_equity_missing"
    return "closing_equity_missing" if closing is None else None


def _previous_quarter(year: int, quarter: int) -> tuple[int, int]:
    return (year - 1, 4) if quarter == 1 else (year, quarter - 1)


def derive_periods(rows: list[dict[str, Any]]) -> list[QuarterlyPeriod]:
    """Cumulative, single-quarter and year-ago figures for stored rows, oldest first.

    Single quarter = this quarter's cumulative minus the previous one of the same fiscal year
    (Q1 is its own cumulative); without that previous quarter every single figure is null. ROE is
    single-quarter parent net income over the mean of opening and closing parent equity, not
    annualized; the opening equity is the previous quarter's closing one.
    """
    by_period = {(row["fiscal_year"], row["fiscal_quarter"]): row for row in rows}
    periods: dict[tuple[int, int], QuarterlyPeriod] = {}
    for key in sorted(by_period):
        row = by_period[key]
        year, quarter = key
        cumulative = {name: row.get(name) for name in _INCOME} | {"eps": row.get("cumulative_eps")}
        previous = by_period.get(_previous_quarter(year, quarter))
        single_missing = None
        if quarter == 1:
            single_amounts = dict(cumulative)
        elif previous is not None:
            single_amounts = {
                name: _difference(row.get(name), previous.get(name)) for name in _INCOME
            } | {"eps": _difference(row.get("cumulative_eps"), previous.get("cumulative_eps"))}
        else:
            single_amounts = {}
            single_missing = "previous_quarter_missing"
        single = _figures(single_amounts)
        opening = previous.get("parent_equity") if previous is not None else None
        closing = row.get("parent_equity")
        if opening is not None and closing is not None:
            single.roe_pct = _ratio_pct(single.parent_net_income, (opening + closing) / 2)
        periods[key] = QuarterlyPeriod(
            fiscal_year=year,
            fiscal_quarter=quarter,
            period_end=row["period_end"],
            accounting_category=row.get("accounting_category"),
            cumulative=_figures(cumulative),
            single=single,
            single_missing_reason=single_missing,
            roe_missing_reason=_roe_missing_reason(opening, closing),
            balance=QuarterlyBalance(**{name: row.get(name) for name in QuarterlyBalance.model_fields}),
            source=row.get("source"),
            source_url=row.get("source_url"),
            first_retrieved_at=row.get("first_retrieved_at"),
            last_retrieved_at=row.get("last_retrieved_at"),
            revised_at=row.get("revised_at"),
        )
    for (year, quarter), period in periods.items():
        prior = periods.get((year - 1, quarter))
        if prior is not None:
            period.year_ago = QuarterlyYearAgo(
                fiscal_year=prior.fiscal_year,
                fiscal_quarter=prior.fiscal_quarter,
                cumulative=prior.cumulative,
                single=prior.single,
            )
    return list(periods.values())


class TaiwanQuarterlyFinancialsService:
    """Reads the display-only archive for one listing; never fetches."""

    def __init__(self, store: TaiwanQuarterlyFinancialStore | None = None, security_master=None) -> None:
        self.store = store or TaiwanQuarterlyFinancialStore()
        if security_master is None:
            from app.taiwan.universe import get_security_master

            security_master = get_security_master()
        self.security_master = security_master

    def get(self, raw_symbol: str) -> TaiwanQuarterlyFinancialsResponse:
        symbol = parse_symbol(raw_symbol)
        instrument = self.security_master.get_instrument(symbol.canonical)
        instrument_type = getattr(instrument, "instrument_type", None)
        if instrument_type not in {"stock", "etf"}:
            raise LookupError(f"supported security type not found for {symbol.canonical}")
        base = {"symbol": symbol.canonical, "exchange": symbol.exchange.value}
        if instrument_type == "etf":
            return TaiwanQuarterlyFinancialsResponse(**base, applicable=False, not_applicable_reason="etf")
        if MarketProfileBridge.is_tdr(instrument):
            return TaiwanQuarterlyFinancialsResponse(**base, applicable=False, not_applicable_reason="tdr")
        periods = derive_periods(self.store.rows_for(symbol.canonical))
        annual = [
            AnnualEps(fiscal_year=period.fiscal_year, eps=period.cumulative.eps)
            for period in periods
            if period.fiscal_quarter == 4
        ]
        return TaiwanQuarterlyFinancialsResponse(
            **base,
            applicable=True,
            accumulating=len(periods) < TARGET_QUARTERS,
            quarters=periods,
            annual_eps=annual,
        )


def get_taiwan_quarterly_financials_service() -> TaiwanQuarterlyFinancialsService:
    return TaiwanQuarterlyFinancialsService()
