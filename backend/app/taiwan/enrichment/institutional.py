"""Three Major Institutional Investors (三大法人) Provider for Taiwan Markets.

Adapts and normalizes:
  - TWSE T86: https://www.twse.com.tw/rwd/zh/fund/T86
  - TPEx dailyTrade: https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade

Integrity Guarantees:
  - Strict unit: all volumes normalized to shares (股)
  - Validation: compares official_net against computed_net (buy - sell)
  - Zero silent overwrite on discrepancy; flags discrepancy status
  - Dataset-specific StalePolicy
  - Full offline unit-test capability via mock injection
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.taiwan.enrichment.models import (
    DatasetType,
    InstitutionalFlow,
    SourceMeta,
    StalePolicy,
)
from app.taiwan.providers.taiwan_values import parse_integer, parse_taiwan_date

logger = logging.getLogger(__name__)


def _required_int(val: Any, name: str) -> int:
    parsed = parse_integer(val)
    if parsed is None:
        raise ValueError(f"missing required institutional field {name}")
    return parsed


@dataclass(frozen=True)
class InstitutionalDay:
    """One day's institutional table.

    The exchanges list only securities the institutions traded, so a present table
    without the target's row means zero flows, while an absent table means no data.
    """
    table_present: bool
    flows: list[InstitutionalFlow] = field(default_factory=list)


_AVAILABLE_FIELDS = ("foreign", "investment_trust", "dealer", "dealer_proprietary", "dealer_hedge")


_ROC_CHINESE_DATE = re.compile(r"^(\d{2,3})年(\d{1,2})月(\d{1,2})日$")


def _stated_date(text: str) -> date:
    """A response date stamp: ROC or CE digits, or the ROC Chinese form such as 115年10月21日."""
    m = _ROC_CHINESE_DATE.match(text)
    if m:
        return date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)))
    return parse_taiwan_date(text)


def stamped_for(stamps: list[Any], trade_date: date) -> bool:
    """Whether the response's stated dates are *trade_date*.

    A response stating no date is accepted; one whose stamps all fail to parse is not.
    """
    texts = [str(stamp).strip() for stamp in stamps if stamp is not None and str(stamp).strip()]
    parsed: list[date] = []
    for text in texts:
        try:
            parsed.append(_stated_date(text))
        except ValueError:
            continue
    if texts and not parsed:
        return False
    return all(d == trade_date for d in parsed)


def _meta(source: str, provider: str, source_url: str, trade_date: date, fetched_at: datetime) -> SourceMeta:
    is_stale = StalePolicy.is_stale(DatasetType.INSTITUTIONAL, trade_date, fetched_at)
    return SourceMeta(
        source=source,
        source_url=source_url,
        fetched_at=fetched_at,
        trade_date=trade_date,
        status="stale" if is_stale else "official",
        provider=provider,
        is_realtime=False,
        available_fields=_AVAILABLE_FIELDS,
        is_stale=is_stale,
        source_type="official_open_data",
        freshness_class="eod_snapshot",
    )


def _zero_flow(symbol: str, trade_date: date, meta: SourceMeta) -> InstitutionalFlow:
    """The flows of a security missing from a present institutional table."""
    return InstitutionalFlow(
        symbol=symbol,
        trade_date=trade_date,
        foreign_buy=0,
        foreign_sell=0,
        foreign_net=0,
        investment_trust_buy=0,
        investment_trust_sell=0,
        investment_trust_net=0,
        dealer_buy=0,
        dealer_sell=0,
        dealer_net=0,
        meta=meta,
    )


class TwseInstitutionalAdapter:
    """Parses TWSE T86 official institutional trading table."""

    BASE_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
    SOURCE = "twse:t86"
    PROVIDER = "twse"
    SUFFIX = "TWSE"

    def build_url(self, trade_date: date) -> str:
        date_str = trade_date.strftime("%Y%m%d")
        return f"{self.BASE_URL}?response=json&selectType=ALLBUT0999&date={date_str}"

    def parse_payload(
        self,
        payload: dict[str, Any],
        trade_date: date,
        source_url: str,
        target_code: str | None = None,
    ) -> list[InstitutionalFlow]:
        return self.parse_day(payload, trade_date, source_url, target_code).flows

    def parse_day(
        self,
        payload: dict[str, Any],
        trade_date: date,
        source_url: str,
        target_code: str | None = None,
    ) -> InstitutionalDay:
        # TWSE response contains 'data' or 'tables[0].data'
        rows = payload.get("data")
        stamps = [payload.get("date")]
        if not rows and "tables" in payload and payload["tables"]:
            rows = payload["tables"][0].get("data")
            stamps.append(payload["tables"][0].get("date"))
        if not rows or not stamped_for(stamps, trade_date):
            return InstitutionalDay(table_present=False)

        meta = _meta(self.SOURCE, self.PROVIDER, source_url, trade_date, datetime.now())
        results: list[InstitutionalFlow] = []

        for r in rows:
            if len(r) < 19:
                raise ValueError(f"TWSE institutional row has {len(r)} columns; expected at least 19")
            code = str(r[0]).strip()
            if target_code and code != target_code:
                continue

            fb = _required_int(r[2], "foreign_buy")
            fs = _required_int(r[3], "foreign_sell")
            fn = _required_int(r[4], "foreign_net")

            tb = _required_int(r[8], "investment_trust_buy")
            ts = _required_int(r[9], "investment_trust_sell")
            tn = _required_int(r[10], "investment_trust_net")

            official_dealer_net = _required_int(r[11], "dealer_net")
            dpb = _required_int(r[12], "dealer_proprietary_buy")
            dps = _required_int(r[13], "dealer_proprietary_sell")
            dpn = _required_int(r[14], "dealer_proprietary_net")
            dhb = _required_int(r[15], "dealer_hedge_buy")
            dhs = _required_int(r[16], "dealer_hedge_sell")
            dhn = _required_int(r[17], "dealer_hedge_net")
            db = dpb + dhb
            ds = dps + dhs
            dn = dpn + dhn

            official_net = _required_int(r[18], "total_net")
            computed_net = fn + tn + dn
            discrepancy = any((fn != fb - fs, tn != tb - ts, dpn != dpb - dps,
                               dhn != dhb - dhs, official_dealer_net != dn,
                               official_net != computed_net))

            flow = InstitutionalFlow(
                symbol=f"{code}.{self.SUFFIX}",
                trade_date=trade_date,
                foreign_buy=fb,
                foreign_sell=fs,
                foreign_net=fn,
                investment_trust_buy=tb,
                investment_trust_sell=ts,
                investment_trust_net=tn,
                dealer_buy=db,
                dealer_sell=ds,
                dealer_net=dn,
                dealer_proprietary_buy=dpb,
                dealer_proprietary_sell=dps,
                dealer_proprietary_net=dpn,
                dealer_hedge_buy=dhb,
                dealer_hedge_sell=dhs,
                dealer_hedge_net=dhn,
                official_net=official_net,
                computed_net=computed_net,
                has_discrepancy=discrepancy,
                meta=meta,
            )
            results.append(flow)

        return InstitutionalDay(table_present=True, flows=results)


class TpexInstitutionalAdapter:
    """Parses TPEx dailyTrade official institutional trading table."""

    BASE_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
    SOURCE = "tpex:daily_trade"
    PROVIDER = "tpex"
    SUFFIX = "TPEX"

    def build_url(self, trade_date: date) -> str:
        date_str = trade_date.strftime("%Y/%m/%d")
        return f"{self.BASE_URL}?response=json&type=Daily&sect=AL&date={date_str}"

    def parse_payload(
        self,
        payload: dict[str, Any],
        trade_date: date,
        source_url: str,
        target_code: str | None = None,
    ) -> list[InstitutionalFlow]:
        return self.parse_day(payload, trade_date, source_url, target_code).flows

    def parse_day(
        self,
        payload: dict[str, Any],
        trade_date: date,
        source_url: str,
        target_code: str | None = None,
    ) -> InstitutionalDay:
        rows = payload.get("aaData")
        stamps = [payload.get("date"), payload.get("reportDate")]
        if not rows and "tables" in payload and payload["tables"]:
            rows = payload["tables"][0].get("data")
            stamps.append(payload["tables"][0].get("date"))
        if not rows or not stamped_for(stamps, trade_date):
            return InstitutionalDay(table_present=False)

        meta = _meta(self.SOURCE, self.PROVIDER, source_url, trade_date, datetime.now())
        results: list[InstitutionalFlow] = []

        for r in rows:
            if len(r) < 24:
                raise ValueError(f"TPEx institutional row has {len(r)} columns; expected at least 24")
            code = str(r[0]).strip()
            if target_code and code != target_code:
                continue

            fb = _required_int(r[2], "foreign_buy")
            fs = _required_int(r[3], "foreign_sell")
            fn = _required_int(r[4], "foreign_net")

            tb = _required_int(r[11], "investment_trust_buy")
            ts = _required_int(r[12], "investment_trust_sell")
            tn = _required_int(r[13], "investment_trust_net")

            dpb = _required_int(r[14], "dealer_proprietary_buy")
            dps = _required_int(r[15], "dealer_proprietary_sell")
            dpn = _required_int(r[16], "dealer_proprietary_net")
            dhb = _required_int(r[17], "dealer_hedge_buy")
            dhs = _required_int(r[18], "dealer_hedge_sell")
            dhn = _required_int(r[19], "dealer_hedge_net")
            db = _required_int(r[20], "dealer_buy")
            ds = _required_int(r[21], "dealer_sell")
            official_dealer_net = _required_int(r[22], "dealer_net")
            dn = dpn + dhn

            official_net = _required_int(r[23], "total_net")
            computed_net = fn + tn + dn
            discrepancy = any((fn != fb - fs, tn != tb - ts, dpn != dpb - dps,
                               dhn != dhb - dhs, official_dealer_net != dn,
                               official_net != computed_net))

            flow = InstitutionalFlow(
                symbol=f"{code}.{self.SUFFIX}",
                trade_date=trade_date,
                foreign_buy=fb,
                foreign_sell=fs,
                foreign_net=fn,
                investment_trust_buy=tb,
                investment_trust_sell=ts,
                investment_trust_net=tn,
                dealer_buy=db,
                dealer_sell=ds,
                dealer_net=dn,
                dealer_proprietary_buy=dpb,
                dealer_proprietary_sell=dps,
                dealer_proprietary_net=dpn,
                dealer_hedge_buy=dhb,
                dealer_hedge_sell=dhs,
                dealer_hedge_net=dhn,
                official_net=official_net,
                computed_net=computed_net,
                has_discrepancy=discrepancy,
                meta=meta,
            )
            results.append(flow)

        return InstitutionalDay(table_present=True, flows=results)


class TaiwanInstitutionalProvider:
    """Unified service for querying Taiwan Three Major Institutional flows."""

    def __init__(
        self,
        twse_adapter: TwseInstitutionalAdapter | None = None,
        tpex_adapter: TpexInstitutionalAdapter | None = None,
    ) -> None:
        self.twse = twse_adapter or TwseInstitutionalAdapter()
        self.tpex = tpex_adapter or TpexInstitutionalAdapter()

    def fetch_live_day(
        self,
        exchange: str,
        trade_date: date,
        target_code: str | None = None,
        timeout: float = 10.0,
    ) -> list[InstitutionalFlow]:
        """The day's flows; with *target_code*, zero flows when the day's table lacks it, empty without a table."""
        ex = exchange.upper()
        adapter: TwseInstitutionalAdapter | TpexInstitutionalAdapter
        if ex == "TWSE":
            adapter = self.twse
        elif ex == "TPEX":
            adapter = self.tpex
        else:
            raise ValueError(f"Unsupported exchange for institutional flows: {exchange}")
        url = adapter.build_url(trade_date)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        day = adapter.parse_day(data, trade_date, source_url=url, target_code=target_code)
        if target_code and day.table_present and not day.flows:
            meta = _meta(adapter.SOURCE, adapter.PROVIDER, url, trade_date, datetime.now())
            return [_zero_flow(f"{target_code}.{adapter.SUFFIX}", trade_date, meta)]
        return day.flows
