"""Historical Institutional Flow and Margin Trading Query Service.

Serves the "history" panel behind each chip card on the stock detail page:
a bounded backward lookup over the persisted per-date parquet partitions
(TaiwanInstitutionalStore / TaiwanMarginStore), scoped to N trading days for
a single symbol. Read-only; never triggers a network refresh, so coverage
gaps left by the settings-page backfill flow surface as missing dates
rather than errors.

Canonical units: shares (股), matching TaiwanInstitutionalData / TaiwanMarginData
in detail_models.py. The stock detail cards convert shares to lots (張) for
display; this service intentionally does not pre-convert so the contract
stays uniform across the Taiwan API surface.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from pydantic import BaseModel, Field

from app.taiwan.benchmarks import get_benchmark
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_today
from app.taiwan.symbol import parse_symbol
from app.taiwan.universe import get_security_master

SymbolExistsFn = Callable[[str], bool]


class TaiwanInstitutionalHistoryRow(BaseModel):
    date: str = Field(..., description="交易日 (YYYY-MM-DD)")
    foreign_net: int | None = Field(None, description="外資及陸資買賣超 (股)")
    investment_trust_net: int | None = Field(None, description="投信買賣超 (股)")
    dealer_net: int | None = Field(None, description="自營商合計買賣超 (股)")
    total_net: int | None = Field(None, description="三大法人合計買賣超 (股)")


class TaiwanInstitutionalHistoryResponse(BaseModel):
    symbol: str
    rows: list[TaiwanInstitutionalHistoryRow] = Field(default_factory=list)


class TaiwanMarginHistoryRow(BaseModel):
    date: str = Field(..., description="交易日 (YYYY-MM-DD)")
    margin_balance: int | None = Field(None, description="融資餘額 (股)")
    margin_change: int | None = Field(None, description="融資增減 (股)")
    short_balance: int | None = Field(None, description="融券餘額 (股)")
    short_change: int | None = Field(None, description="融券增減 (股)")
    short_margin_ratio: float | None = Field(None, description="券資比 (%)")


class TaiwanMarginHistoryResponse(BaseModel):
    symbol: str
    rows: list[TaiwanMarginHistoryRow] = Field(default_factory=list)


def _default_symbol_exists(symbol_str: str) -> bool:
    if get_benchmark(symbol_str) is not None:
        return True
    return get_security_master().get_instrument(symbol_str) is not None


def _trading_days_start(calendar: TaiwanTradingCalendar, end: date, days: int) -> date:
    """Walk back from *end*, counting days not confirmed as non-trading, until *days* are counted.

    A weekday holiday in a year without a cached TWSE schedule still counts toward
    the window; that only widens the query range slightly and read_range() naturally
    returns no row for a date nothing was ever written to.
    """
    cursor = end
    counted = 0
    while counted < days:
        cursor -= timedelta(days=1)
        if calendar.is_trading_day(cursor) is not False:
            counted += 1
    return cursor


class TaiwanChipHistoryService:
    """Read-only historical query over persisted institutional/margin parquet stores."""

    def __init__(
        self,
        institutional_store: TaiwanInstitutionalStore | None = None,
        margin_store: TaiwanMarginStore | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        symbol_exists: SymbolExistsFn | None = None,
    ) -> None:
        self._institutional_store = institutional_store or TaiwanInstitutionalStore()
        self._margin_store = margin_store or TaiwanMarginStore()
        self._calendar = calendar or TaiwanTradingCalendar()
        self._symbol_exists = symbol_exists or _default_symbol_exists

    def get_institutional_history(self, raw_symbol: str, days: int) -> TaiwanInstitutionalHistoryResponse:
        symbol_str = parse_symbol(raw_symbol).canonical
        if not self._symbol_exists(symbol_str):
            raise LookupError(f"unknown Taiwan symbol: {symbol_str}")

        end = taipei_today()
        start = _trading_days_start(self._calendar, end, days)
        df = self._institutional_store.read_range([symbol_str], start, end)

        rows: list[TaiwanInstitutionalHistoryRow] = []
        if not df.is_empty():
            df = df.sort("date").tail(days)
            for r in df.iter_rows(named=True):
                computed_net = r.get("computed_net")
                total_net = computed_net if computed_net is not None else r.get("official_net")
                rows.append(
                    TaiwanInstitutionalHistoryRow(
                        date=r["date"].isoformat(),
                        foreign_net=r.get("foreign_net"),
                        investment_trust_net=r.get("investment_trust_net"),
                        dealer_net=r.get("dealer_net"),
                        total_net=total_net,
                    )
                )
        return TaiwanInstitutionalHistoryResponse(symbol=symbol_str, rows=rows)

    def get_margin_history(self, raw_symbol: str, days: int) -> TaiwanMarginHistoryResponse:
        symbol_str = parse_symbol(raw_symbol).canonical
        if not self._symbol_exists(symbol_str):
            raise LookupError(f"unknown Taiwan symbol: {symbol_str}")

        end = taipei_today()
        start = _trading_days_start(self._calendar, end, days)
        df = self._margin_store.read_range([symbol_str], start, end)

        rows: list[TaiwanMarginHistoryRow] = []
        if not df.is_empty():
            df = df.sort("date").tail(days)
            for r in df.iter_rows(named=True):
                rows.append(
                    TaiwanMarginHistoryRow(
                        date=r["date"].isoformat(),
                        margin_balance=r.get("margin_balance"),
                        margin_change=r.get("margin_change"),
                        short_balance=r.get("short_balance"),
                        short_change=r.get("short_change"),
                        short_margin_ratio=r.get("short_margin_ratio"),
                    )
                )
        return TaiwanMarginHistoryResponse(symbol=symbol_str, rows=rows)


_service: TaiwanChipHistoryService | None = None


def get_taiwan_chip_history_service() -> TaiwanChipHistoryService:
    global _service
    if _service is None:
        _service = TaiwanChipHistoryService()
    return _service
