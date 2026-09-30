"""Market-wide institutional buy/sell amounts from the exchanges' official daily summaries.

Adapts:
  - TWSE BFI82U: https://www.twse.com.tw/rwd/zh/fund/BFI82U
  - TPEx insti/summary: https://www.tpex.org.tw/www/zh-tw/insti/summary

Every official category row is kept with its SourceMeta. Amounts are normalized to
TWD. A response with a non-OK status, no rows, or a missing or different trading-date
stamp yields no rows; a response that cannot be read (unit other than TWD or thousand
TWD, a required category missing, malformed numbers or dates) raises ValueError.
Official figures are never adjusted: a row whose buy minus sell differs from its net,
or a subtotal or total that differs from the sum of its official components, is kept
as published and flagged with has_discrepancy.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.taiwan.enrichment.models import DatasetType, SourceMeta, StalePolicy
from app.taiwan.providers.taiwan_values import TAIPEI, parse_integer, parse_taiwan_date

FOREIGN_EXCL_DEALER = "foreign_excl_dealer"
FOREIGN_DEALER = "foreign_dealer"
FOREIGN_TOTAL = "foreign_total"
INVESTMENT_TRUST = "investment_trust"
DEALER_PROPRIETARY = "dealer_proprietary"
DEALER_HEDGE = "dealer_hedge"
DEALER_TOTAL = "dealer_total"
TOTAL = "total"

REQUIRED_CATEGORIES = frozenset({FOREIGN_EXCL_DEALER, INVESTMENT_TRUST, DEALER_PROPRIETARY, DEALER_HEDGE, TOTAL})

# Foreign dealers are already counted under the dealer rows, so the official total excludes them.
TOTAL_COMPONENTS = (FOREIGN_EXCL_DEALER, INVESTMENT_TRUST, DEALER_PROPRIETARY, DEALER_HEDGE)
SUBTOTAL_COMPONENTS: dict[str, tuple[str, ...]] = {
    TOTAL: TOTAL_COMPONENTS,
    FOREIGN_TOTAL: (FOREIGN_EXCL_DEALER, FOREIGN_DEALER),
    DEALER_TOTAL: (DEALER_PROPRIETARY, DEALER_HEDGE),
}

TWSE_CATEGORIES: dict[str, str] = {
    "外資及陸資(不含外資自營商)": FOREIGN_EXCL_DEALER,
    "外資自營商": FOREIGN_DEALER,
    "投信": INVESTMENT_TRUST,
    "自營商(自行買賣)": DEALER_PROPRIETARY,
    "自營商(避險)": DEALER_HEDGE,
    "合計": TOTAL,
}

TPEX_CATEGORIES: dict[str, str] = {
    "外資及陸資合計": FOREIGN_TOTAL,
    "外資及陸資(不含自營商)": FOREIGN_EXCL_DEALER,
    "外資自營商": FOREIGN_DEALER,
    "投信": INVESTMENT_TRUST,
    "自營商合計": DEALER_TOTAL,
    "自營商(自行買賣)": DEALER_PROPRIETARY,
    "自營商(避險)": DEALER_HEDGE,
    "三大法人合計": TOTAL,
}

AMOUNT_UNIT_SCALE: dict[str, int] = {"元": 1, "千元": 1000}

_TRAILING_UNIT = re.compile(r"\(([^()]*)\)$")


@dataclass(frozen=True)
class MarketInstitutionalAmount:
    exchange: str
    category: str | None
    official_name: str
    buy_amount: int
    sell_amount: int
    net_amount: int
    has_discrepancy: bool
    meta: SourceMeta


def _normalized(raw: Any) -> str:
    # NFKC folds full-width parentheses, colons and the U+3000 indent TPEx uses into ASCII.
    return unicodedata.normalize("NFKC", str(raw)).strip()


def _official_name(raw: Any) -> str:
    return _normalized(raw).rstrip("*").strip()


def _amount_scale(unit: str) -> int:
    scale = AMOUNT_UNIT_SCALE.get(_normalized(unit))
    if scale is None:
        raise ValueError(f"unsupported amount unit {unit!r}")
    return scale


def _twse_unit(payload: dict[str, Any]) -> str:
    # BFI82U states the unit once, as hints "<label>:<unit>".
    return _normalized(payload.get("hints") or "").rpartition(":")[2]


def _tpex_unit(fields: list[Any]) -> str:
    units = set()
    for field in fields[1:4]:
        m = _TRAILING_UNIT.search(_normalized(field))
        units.add(m.group(1).strip() if m else "")
    if len(units) != 1:
        raise ValueError(f"inconsistent amount units in {fields!r}")
    return units.pop()


def _stamped_for(stamp: Any, trade_date: date) -> bool:
    text = str(stamp or "").strip()
    return bool(text) and parse_taiwan_date(text) == trade_date


def _flag_discrepancies(rows: list[dict[str, Any]]) -> None:
    by_category = {r["category"]: r for r in rows if r["category"] is not None}
    for r in rows:
        if r["buy_amount"] - r["sell_amount"] != r["net_amount"]:
            r["has_discrepancy"] = True
    for parent, components in SUBTOTAL_COMPONENTS.items():
        if parent not in by_category or any(c not in by_category for c in components):
            continue
        for key in ("buy_amount", "sell_amount", "net_amount"):
            if by_category[parent][key] != sum(by_category[c][key] for c in components):
                by_category[parent]["has_discrepancy"] = True


def _parse_rows(
    rows: list[list[Any]],
    categories: dict[str, str],
    exchange: str,
    scale: int,
    meta: SourceMeta,
) -> list[MarketInstitutionalAmount]:
    parsed: list[dict[str, Any]] = []
    for r in rows:
        if len(r) < 4:
            raise ValueError(f"{exchange} institutional summary row has {len(r)} columns; expected 4")
        values = [parse_integer(v) for v in r[1:4]]
        if any(v is None for v in values):
            raise ValueError(f"{exchange} institutional summary row {r[0]!r} has a missing amount")
        buy, sell, net = (v * scale for v in values)
        name = _official_name(r[0])
        parsed.append({
            "exchange": exchange,
            "category": categories.get(name),
            "official_name": name,
            "buy_amount": buy,
            "sell_amount": sell,
            "net_amount": net,
            "has_discrepancy": False,
        })
    missing = REQUIRED_CATEGORIES - {r["category"] for r in parsed}
    if missing:
        raise ValueError(f"{exchange} institutional summary lacks {', '.join(sorted(missing))}")
    _flag_discrepancies(parsed)
    return [MarketInstitutionalAmount(**r, meta=meta) for r in parsed]


def _source_meta(source: str, provider: str, source_url: str, trade_date: date, fetched_at: datetime | None) -> SourceMeta:
    retrieved = fetched_at or datetime.now(TAIPEI)
    # StalePolicy compares naive wall-clock times; feed it Taipei time rather than the server's zone.
    is_stale = StalePolicy.is_stale(
        DatasetType.INSTITUTIONAL, trade_date, retrieved, now=retrieved.astimezone(TAIPEI).replace(tzinfo=None)
    )
    return SourceMeta(
        source=source,
        source_url=source_url,
        fetched_at=retrieved,
        trade_date=trade_date,
        status="stale" if is_stale else "official",
        provider=provider,
        is_realtime=False,
        available_fields=("foreign", "investment_trust", "dealer", "dealer_proprietary", "dealer_hedge", "total"),
        is_stale=is_stale,
        source_type="official_open_data",
        freshness_class="eod_snapshot",
    )


class TwseMarketInstitutionalAdapter:
    """Parses the TWSE BFI82U daily institutional amount summary."""

    BASE_URL = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"
    SOURCE = "twse:bfi82u"
    PROVIDER = "twse"
    EXCHANGE = "TWSE"

    def build_url(self, trade_date: date) -> str:
        return f"{self.BASE_URL}?dayDate={trade_date.strftime('%Y%m%d')}&type=day&response=json"

    def parse_payload(
        self, payload: dict[str, Any], trade_date: date, source_url: str, fetched_at: datetime | None = None
    ) -> list[MarketInstitutionalAmount]:
        if str(payload.get("stat", "")).strip().upper() != "OK":
            return []
        if not _stamped_for(payload.get("date"), trade_date):
            return []
        rows = payload.get("data") or []
        if not rows:
            return []
        meta = _source_meta(self.SOURCE, self.PROVIDER, source_url, trade_date, fetched_at)
        return _parse_rows(rows, TWSE_CATEGORIES, self.EXCHANGE, _amount_scale(_twse_unit(payload)), meta)


class TpexMarketInstitutionalAdapter:
    """Parses the TPEx daily institutional amount summary."""

    BASE_URL = "https://www.tpex.org.tw/www/zh-tw/insti/summary"
    SOURCE = "tpex:insti_summary"
    PROVIDER = "tpex"
    EXCHANGE = "TPEX"

    def build_url(self, trade_date: date) -> str:
        return f"{self.BASE_URL}?type=Daily&date={trade_date.strftime('%Y/%m/%d')}&id=&response=json"

    def parse_payload(
        self, payload: dict[str, Any], trade_date: date, source_url: str, fetched_at: datetime | None = None
    ) -> list[MarketInstitutionalAmount]:
        if str(payload.get("stat", "")).strip().upper() != "OK":
            return []
        if not _stamped_for(payload.get("date"), trade_date):
            return []
        tables = payload.get("tables") or []
        if not tables:
            return []
        table = tables[0]
        if not _stamped_for(table.get("date"), trade_date):
            return []
        rows = table.get("data") or []
        if not rows:
            return []
        meta = _source_meta(self.SOURCE, self.PROVIDER, source_url, trade_date, fetched_at)
        return _parse_rows(rows, TPEX_CATEGORIES, self.EXCHANGE, _amount_scale(_tpex_unit(table.get("fields") or [])), meta)
