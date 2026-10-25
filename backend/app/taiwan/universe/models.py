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
from app.taiwan.universe.industry_classification import resolve_industry_name


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
    "006201.TPEX": OfficialEtfRuleProfile(
        "domestic_equity", "domestic", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=domestic&code=006201&response=json",
    ),
    "00888.TPEX": OfficialEtfRuleProfile(
        "domestic_equity", "domestic", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=domestic&code=00888&response=json",
    ),
    "00928.TPEX": OfficialEtfRuleProfile(
        "domestic_equity", "domestic", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=domestic&code=00928&response=json",
    ),
    "00877.TPEX": OfficialEtfRuleProfile(
        "foreign_equity", "foreign", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=foreign&code=00877&response=json",
    ),
    "00886.TPEX": OfficialEtfRuleProfile(
        "foreign_equity", "foreign", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=foreign&code=00886&response=json",
    ),
    "00887.TPEX": OfficialEtfRuleProfile(
        "foreign_equity", "foreign", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=foreign&code=00887&response=json",
    ),
    "009822.TPEX": OfficialEtfRuleProfile(
        "foreign_equity", "foreign", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=foreign&code=009822&response=json",
    ),
    "00411A.TPEX": OfficialEtfRuleProfile(
        "foreign_equity", "foreign", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=foreign&code=00411A&response=json",
    ),
    "00858.TPEX": OfficialEtfRuleProfile(
        "foreign_equity", "foreign", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=foreign&code=00858&response=json",
    ),
    "00955.TPEX": OfficialEtfRuleProfile(
        "foreign_equity", "foreign", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=foreign&code=00955&response=json",
    ),
    "009806.TPEX": OfficialEtfRuleProfile(
        "foreign_equity", "foreign", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=foreign&code=009806&response=json",
    ),
    "009807.TPEX": OfficialEtfRuleProfile(
        "foreign_equity", "foreign", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=foreign&code=009807&response=json",
    ),
    "009814.TPEX": OfficialEtfRuleProfile(
        "foreign_equity", "foreign", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=foreign&code=009814&response=json",
    ),
    "009815.TPEX": OfficialEtfRuleProfile(
        "foreign_equity", "foreign", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=foreign&code=009815&response=json",
    ),
    "009823.TPEX": OfficialEtfRuleProfile(
        "foreign_equity", "foreign", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=foreign&code=009823&response=json",
    ),
    "009825.TPEX": OfficialEtfRuleProfile(
        "foreign_equity", "foreign", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=foreign&code=009825&response=json",
    ),
    "00998A.TPEX": OfficialEtfRuleProfile(
        "foreign_equity", "foreign", 1.0,
        "https://www.tpex.org.tw/www/zh-tw/ETF/detail?type=foreign&code=00998A&response=json",
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
    listing_date: str | None  # ISO "YYYY-MM-DD" or None
    isin: str | None          # e.g. "TW0002330008"
    industry: str | None      # e.g. "半導體業", "光電業"
    cfi_code: str | None      # ISO 10962 CFI code (e.g. "ESVUFR", "CEOJEU", "CEOIEU")
    raw_category: str         # Official category string ("股票", "ETF", "特別股", etc.)
    is_supported: bool        # True only for tradable stock/etf in current phase
    source: str               # "TWSE_ISIN" | "TPEX_ISIN"
    updated_at: str           # ISO timestamp string
    etf_category: str | None = None  # EtfCategory value if instrument_type == "etf"
    classification_source: str | None = None  # official_metadata, cfi_code, name_heuristic or unknown
    underlying_scope: str | None = None       # "domestic" | "foreign" | "unknown"
    leverage_multiplier: float = 1.0          # e.g. 1.0, 2.0, -1.0
    etf_management_style: str | None = None   # "active" | "passive" | None
    currency: str = "TWD"
    lot_size: int = 1000
    security_class: str | None = None         # "tdr" for Taiwan depositary receipts, else None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


TDR_SECURITY_CLASS = "tdr"
# t187ap03_L industry code "91" is used only by the ISIN TDR category; it is not an industry.
TDR_INDUSTRY_CODE = "91"


def is_tdr_industry(industry: str | None) -> bool:
    """Whether a raw or resolved industry value is the TDR marker "91"."""
    if industry is None:
        return False
    return resolve_industry_name(industry) == resolve_industry_name(TDR_INDUSTRY_CODE)


def derive_security_class(code: str | None, name: str | None, industry: str | None) -> str | None:
    """Return "tdr" for the 91 industry marker, a six-digit 91 code or a -DR name; else None.

    TWSE assigns 910101-919999 to TDRs listed since 2009-12-15; older TDRs keep four-digit codes.
    """
    code = code or ""
    if (
        is_tdr_industry(industry)
        or (len(code) == 6 and code.startswith("91"))
        or (name or "").strip().upper().endswith("-DR")
    ):
        return TDR_SECURITY_CLASS
    return None


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
            # Only passive bond ETFs with multiplier 1 are BOND_ETF, taxed 0% only inside the statutory
            # window (SecuritiesTaxModel); leveraged/inverse bond ETFs (00688L/00689R) pay 0.1% under
            # MOF ruling No. 10504709410.
            if (
                cat == EtfCategory.BOND.value
                and instrument.etf_management_style == "passive"
                and abs(instrument.leverage_multiplier - 1.0) < 1e-6
            ):
                return TaxClass.BOND_ETF
            return TaxClass.DOMESTIC_ETF
        # Duck-typed instruments carry no code/name to derive from, only an explicit class.
        is_tdr = (
            cls.is_tdr(instrument)
            if isinstance(instrument, TaiwanInstrument)
            else getattr(instrument, "security_class", None) == TDR_SECURITY_CLASS
        )
        return TaxClass.TDR if is_tdr else TaxClass.ORDINARY_STOCK

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
    def is_tdr(instrument: TaiwanInstrument) -> bool:
        """Whether the instrument is a Taiwan depositary receipt.

        An explicit security_class decides; without one, the code, name and industry marker decide.
        """
        if instrument.security_class is not None:
            return instrument.security_class == TDR_SECURITY_CLASS
        derived = derive_security_class(instrument.code, instrument.name, instrument.industry)
        return derived == TDR_SECURITY_CLASS

    @classmethod
    def initial_listing_rule_applies(cls, instrument: TaiwanInstrument) -> bool:
        """Whether the no-limit window of an initial listing applies: ordinary stocks, not TDRs."""
        return instrument.instrument_type == "stock" and not cls.is_tdr(instrument)

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
        Foreign-component ETF, incl. bond and futures trust (00646, 00720B, 00633L, 00635U): None
        Domestic-component bond ETF: 0.10, as for any domestic-component ETF

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

            scope = instrument.underlying_scope or "unknown"
            if scope == "unknown":
                raise ValueError(
                    f"Cannot determine price limit for ETF {instrument.symbol} with UNKNOWN underlying scope. "
                    "Confirmed underlying scope (domestic or foreign) is required."
                )

            # TWSE/TPEx limits follow component scope, not asset class: a bond ETF is no-limit only if foreign.
            if scope == "foreign":
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



