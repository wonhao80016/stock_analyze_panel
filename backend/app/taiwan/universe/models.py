"""Data models and universe definitions for Taiwan Security Master.

Responsibilities:
  - Canonical representation of Taiwan financial instruments
  - Instrument classification (stock, etf, unsupported)
  - Official metadata tracking (ISIN, listing date, industry, CFI code)
  - Predefined screener universes
  - Bridge to Phase 3 TaiwanMarketProfile rule classes
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any, ClassVar

from app.taiwan.enrichment.models import EtfCategory
from app.taiwan.market_rules import (
    ListingSessions,
    PriceLimitClass,
    TaxClass,
    TickSizeClass,
    ipo_no_limit_until,
)


@dataclass(frozen=True)
class OfficialEtfRuleProfile:
    category: str
    underlying_scope: str
    price_limit_multiplier: float
    evidence_url: str


# Explicit audited product rules. This is not ticker/name inference and does not
# populate the separate Phase 6E ETF structured metadata model.
OFFICIAL_ETF_RULE_PROFILES = {
    "00631L.TWSE": OfficialEtfRuleProfile(
        "leveraged", "domestic", 2.0,
        "https://www.twse.com.tw/zh/ETFortune/etfInfo/00631L",
    ),
    "00632R.TWSE": OfficialEtfRuleProfile(
        "inverse", "domestic", -1.0,
        "https://www.twse.com.tw/zh/ETFortune/etfInfo/00632R",
    ),
}


class UniverseType(StrEnum):
    """Predefined market universes for Taiwan securities."""
    TAIWAN_ALL = "taiwan_all"        # All active TWSE + TPEx stocks & ETFs
    TWSE_ALL = "twse_all"            # All active TWSE stocks & ETFs
    TPEX_ALL = "tpex_all"            # All active TPEx stocks & ETFs
    TAIWAN_STOCKS = "taiwan_stocks"  # All active TWSE + TPEx ordinary stocks
    TAIWAN_ETFS = "taiwan_etfs"      # All active TWSE + TPEx ETFs


@dataclass(frozen=True)
class TaiwanInstrument:
    """Canonical representation of a Taiwan financial security."""
    symbol: str               # e.g. "2330.TWSE", "8069.TPEX", "0050.TWSE"
    code: str                 # e.g. "2330", "8069", "0050"
    exchange: str             # "TWSE" | "TPEX"
    name: str                 # Official Traditional Chinese name (e.g. "台積電")
    instrument_type: str      # "stock" | "etf" | "unsupported"
    listing_status: str       # "active" | "delisted" | "suspended" | "unsupported"
    listing_date: str | None  # "YYYY/MM/DD" or None
    isin: str | None          # e.g. "TW0002330008"
    industry: str | None      # e.g. "半導體業", "光電業"
    cfi_code: str | None      # ISO 10962 CFI code (e.g. "ESVUFR", "CEOJEU", "CEOIEU")
    raw_category: str         # Official category string ("股票", "ETF", "特別股", etc.)
    is_supported: bool        # True only for tradable stock/etf in current phase
    source: str               # "TWSE_ISIN" | "TPEX_ISIN"
    updated_at: str           # ISO timestamp string
    etf_category: str | None = None  # EtfCategory value if instrument_type == "etf"
    classification_source: str | None = None  # ClassificationSource value
    underlying_scope: str | None = None       # "domestic" | "foreign" | "unknown"
    leverage_multiplier: float = 1.0          # e.g. 1.0, 2.0, -1.0
    etf_management_style: str | None = None   # "active" | "passive" | None
    currency: str = "TWD"
    lot_size: int = 1000

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ListingDateUnknownError(ValueError):
    """An ordinary stock's listing date is missing or unparseable, so its daily limit is unknown."""


class MarketProfileBridge:
    """Translates TaiwanInstrument metadata into Phase 3 Market Profile rule classes.

    Strictly refuses to apply real trading regulations (tax, price limits) if an ETF's
    classification is unconfirmed or derived solely from name heuristics, or if
    underlying_scope cannot be reliably confirmed.
    """

    CONFIRMED_SOURCES: ClassVar[set[str]] = {
        "official_metadata",
        "cfi_code",
    }

    @classmethod
    def verify_confirmed_etf(cls, instrument: TaiwanInstrument) -> None:
        if instrument.instrument_type == "etf":
            if instrument.symbol in OFFICIAL_ETF_RULE_PROFILES:
                return
            source = instrument.classification_source or "unknown"
            if source not in cls.CONFIRMED_SOURCES:
                raise ValueError(
                    f"Refusing to apply regulatory market rules to unconfirmed ETF {instrument.symbol} "
                    f"(source: {source!r}). Confirmed official product metadata or CFI code is required."
                )

    @classmethod
    def get_tax_class(cls, instrument: TaiwanInstrument) -> TaxClass:
        if instrument.instrument_type == "etf":
            cls.verify_confirmed_etf(instrument)
            cat = instrument.etf_category or EtfCategory.UNKNOWN.value
            # Strictly audit: Only plain-vanilla bond ETFs (multiplier == 1.0) are exempt (0% tax).
            # Leveraged/Inverse bond ETFs (e.g. 00688L/00689R) are subject to statutory 0.1% tax
            # under Ministry of Finance ruling (台財稅字第10504709410號).
            if (
                cat == EtfCategory.BOND.value
                and instrument.etf_management_style == "passive"
                and abs(instrument.leverage_multiplier - 1.0) < 1e-6
            ):
                return TaxClass.BOND_ETF
            return TaxClass.DOMESTIC_ETF
        return TaxClass.ORDINARY_STOCK

    @classmethod
    def get_tick_size_class(cls, instrument: TaiwanInstrument) -> TickSizeClass:
        if instrument.instrument_type == "etf":
            return TickSizeClass.ETF
        return TickSizeClass.ORDINARY_STOCK

    @staticmethod
    def listing_date(instrument: TaiwanInstrument) -> date:
        """The instrument's listing date; raises ListingDateUnknownError when missing or unparseable."""
        from app.taiwan.providers.taiwan_values import parse_taiwan_date

        raw = str(instrument.listing_date or "").strip()
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
        try:
            return parse_taiwan_date(raw)
        except ValueError as exc:
            raise ListingDateUnknownError(
                f"Cannot determine the daily limit of {instrument.symbol}: listing date {instrument.listing_date!r}"
            ) from exc

    @staticmethod
    def initial_listing_rule_applies(instrument: TaiwanInstrument) -> bool:
        """Whether the no-limit window of an initial listing applies: ordinary stocks, not TDRs.

        Taiwan depositary receipts have six-digit codes starting with 91, or names ending in -DR.
        """
        code = instrument.code or ""
        is_tdr = (len(code) == 6 and code.startswith("91")) or (instrument.name or "").strip().upper().endswith("-DR")
        return instrument.instrument_type == "stock" and not is_tdr

    @classmethod
    def ipo_no_limit_window(cls, instrument: TaiwanInstrument, sessions: ListingSessions) -> tuple[date, date] | None:
        """First and last session an ordinary stock's initial listing trades without a daily limit.

        TWSE Operating Rules art. 63 and the TPEx counterpart: five sessions from the listing session,
        except a stock that already traded, inferred from bars of the same code under either exchange
        stored before listing; stored bars carry the current exchange, so a transfer's history may be .TWSE.
        """
        if not cls.initial_listing_rule_applies(instrument):
            return None
        listed = cls.listing_date(instrument)
        for exchange in ("TWSE", "TPEX"):
            first = sessions.first_sessions.get(f"{instrument.code}.{exchange}")
            if first is not None and first < listed:
                return None
        until = ipo_no_limit_until(listed, sessions.trading_dates)
        return None if until is None else (listed, until)

    @classmethod
    def in_ipo_no_limit_window(
        cls,
        instrument: TaiwanInstrument,
        trade_date: date,
        sessions: ListingSessions | None = None,
    ) -> bool:
        """Whether *trade_date* falls inside the instrument's initial-listing window on *sessions*.

        *sessions* defaults to the stored daily-bar calendar. Raises ListingDateUnknownError when the
        rule applies but the listing date is unknown.
        """
        if not cls.initial_listing_rule_applies(instrument):
            return False
        if isinstance(trade_date, datetime):
            trade_date = trade_date.date()
        if sessions is None:
            from app.taiwan.daily_store import listing_sessions

            sessions = listing_sessions()
        window = cls.ipo_no_limit_window(instrument, sessions)
        return window is not None and window[0] <= trade_date <= window[1]

    @classmethod
    def get_price_limit_pct(
        cls,
        instrument: TaiwanInstrument,
        trade_date: date | None = None,
        sessions: ListingSessions | None = None,
    ) -> float | None:
        """Get authoritative price limit percentage for an instrument.

        Ordinary stocks: 0.10 (10%); None on *trade_date* inside the initial-listing window
        Domestic plain ETF (0050, 006208): 0.10 (10%)
        Domestic leveraged ETF (00631L, 2X): 0.20 (20%)
        Domestic inverse ETF (00632R, -1X): 0.10 (10%)
        Foreign / Bond ETF (00646, 00720B, 00633L): None (NO_LIMIT)

        Without *trade_date* the initial-listing window is not applied. *sessions* defaults to the
        stored daily-bar calendar.
        """
        if instrument.instrument_type == "stock":
            if cls.initial_listing_rule_applies(instrument):
                cls.listing_date(instrument)
                if trade_date is not None and cls.in_ipo_no_limit_window(instrument, trade_date, sessions):
                    return None
            return 0.10

        if instrument.instrument_type == "etf":
            cls.verify_confirmed_etf(instrument)
            official_profile = OFFICIAL_ETF_RULE_PROFILES.get(instrument.symbol)
            if official_profile is not None:
                if official_profile.underlying_scope == "foreign":
                    return None
                return round(0.10 * abs(official_profile.price_limit_multiplier), 4)
            cat = instrument.etf_category or EtfCategory.UNKNOWN.value

            scope = instrument.underlying_scope or "unknown"
            if scope == "unknown":
                raise ValueError(
                    f"Cannot determine price limit for ETF {instrument.symbol} with UNKNOWN underlying scope. "
                    "Confirmed underlying scope (domestic or foreign) is required."
                )

            # Foreign-component ETFs and bond ETFs have no price limit in Taiwan
            if scope == "foreign" or cat == EtfCategory.BOND.value:
                return None

            if scope == "domestic":
                # Domestic leveraged / inverse ETF: 10% * abs(multiplier)
                return round(0.10 * abs(instrument.leverage_multiplier), 4)

            raise ValueError(f"Unrecognized underlying scope {scope!r} for ETF {instrument.symbol}.")

        return 0.10

    @classmethod
    def get_price_limit_class(cls, instrument: TaiwanInstrument) -> PriceLimitClass:
        pct = cls.get_price_limit_pct(instrument)
        if pct is None:
            return PriceLimitClass.NO_LIMIT
        if pct == 0.10 and abs(instrument.leverage_multiplier - 1.0) < 1e-6:
            return PriceLimitClass.ORDINARY_TEN_PERCENT
        return PriceLimitClass.LEVERAGED_DOMESTIC

    @classmethod
    def calc_limits(
        cls,
        ref_price: float,
        instrument: TaiwanInstrument,
        trade_date: date | None = None,
        sessions: ListingSessions | None = None,
    ) -> tuple[float | None, float | None]:
        """Calculate authoritative price limit boundary prices for an instrument, aligned to valid ticks."""
        pct = cls.get_price_limit_pct(instrument, trade_date, sessions)
        tick_class = cls.get_tick_size_class(instrument)
        from app.taiwan.market_rules import PriceLimitModel
        return PriceLimitModel.calc_limits_for_pct(ref_price=ref_price, limit_pct=pct, tick_class=tick_class)



