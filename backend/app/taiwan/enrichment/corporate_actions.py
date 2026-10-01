"""Official reference prices that the exchanges set for corporate-action sessions.

Adapts:
  - TWSE TWT49U ex-rights/ex-dividend results: https://www.twse.com.tw/rwd/zh/exRight/TWT49U
  - TWSE TWTAUU capital-reduction resumption: https://www.twse.com.tw/rwd/zh/reducation/TWTAUU
  - TWSE TWTB8U par-value-change resumption: https://www.twse.com.tw/rwd/zh/change/TWTB8U
  - TWSE TWTCAU ETF split/reverse-split resumption: https://www.twse.com.tw/rwd/zh/split/TWTCAU
  - TPEx exDailyQ ex-rights/ex-dividend results: https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ
  - TPEx revivt capital-reduction resumption: https://www.tpex.org.tw/www/zh-tw/bulletin/revivt

One record per event: ``date`` is the session the reference price applies to, and
``ref_price`` replaces the previous close as that session's change and limit base.
Prices are TWD per share or unit. The tables print no unit, so a column title that
carries a unit outside ACCEPTED_PRICE_UNITS raises. Columns are found by their official
titles, not by position: TPEx has added and dropped columns over the years.

Two answers mean the range has no events. One is stat OK, an echo of exactly the
requested range and an empty row list; TWSE TWTB8U and TWTCAU, TPEx exDailyQ and revivt
answer an empty range that way. The other, sent by TWSE TWT49U and TWTAUU, is a stat
equal to TWSE_NO_DATA_STAT after NFKC with no data or fields key; that stat next to
either key raises. Every other answer raises ValueError: any other stat (the exchanges
reject a query with a message in it), a missing or different range echo, a missing
table, fields or row list, a missing column, a row dated outside the requested range, a
row count that disagrees with the response's own total, and malformed numbers or dates.
A row whose reference price is still blank (the exchange lists some resumptions before
pricing them) is skipped; a later fetch picks it up once priced.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, ClassVar

from app.taiwan.enrichment.models import SourceMeta
from app.taiwan.providers.taiwan_values import TAIPEI, parse_number, parse_taiwan_date

logger = logging.getLogger(__name__)

EXDIV = "exdiv"
REDUCTION = "reduction"
PAR_CHANGE = "par_change"
ETF_SPLIT = "etf_split"

ACCEPTED_PRICE_UNITS = frozenset({"元"})
TWSE_NO_DATA_STAT = "很抱歉，沒有符合條件的資料!"  # noqa: RUF001

_TRAILING_UNIT = re.compile(r"^\(([^()]*)\)$")
_ROC_CJK_DATE = re.compile(r"^(\d{2,3})年(\d{1,2})月(\d{1,2})日$")


@dataclass(frozen=True)
class CorporateAction:
    symbol: str
    date: date
    kind: str
    pre_close: float | None
    ref_price: float
    limit_up: float | None
    limit_down: float | None
    open_ref: float | None
    ex_factor: float | None
    official_label: str | None
    meta: SourceMeta


def _normalized(raw: Any) -> str:
    return unicodedata.normalize("NFKC", str(raw)).strip()


def parse_event_date(raw: Any) -> date:
    """Official event dates: ROC year with year/month/day markers (TWT49U), ``113/01/22``, ``1130205`` or ``20240104``."""
    text = _normalized(raw)
    m = _ROC_CJK_DATE.match(text)
    if m is None:
        return parse_taiwan_date(text)
    year, month, day = (int(g) for g in m.groups())
    try:
        return date(year + 1911, month, day)
    except ValueError as exc:
        raise ValueError(f"invalid Taiwan date: {raw!r}") from exc


def _column_index(fields: list[Any], title: str, source: str) -> int:
    names = [_normalized(f) for f in fields]
    if title in names:
        return names.index(title)
    for i, name in enumerate(names):
        if not name.startswith(title):
            continue
        m = _TRAILING_UNIT.match(name[len(title):].strip())
        if m is None:
            continue
        unit = m.group(1).strip()
        if unit not in ACCEPTED_PRICE_UNITS:
            raise ValueError(f"{source} column {title!r} is in unsupported unit {unit!r}")
        return i
    raise ValueError(f"{source} response lacks column {title!r}")


def _price(raw: Any, column: str, source: str) -> float | None:
    value = parse_number(raw)
    if value is not None and value < 0:
        raise ValueError(f"{source} {column} is negative: {raw!r}")
    return value


class _CorporateActionAdapter:
    BASE_URL: str
    SOURCE: str
    PROVIDER: str
    EXCHANGE: str
    KIND: str
    COLUMNS: ClassVar[dict[str, str]]
    LABEL_COLUMN: str | None = None

    def build_url(self, start: date, end: date) -> str:
        raise NotImplementedError

    def _echoed_range(self, payload: dict[str, Any]) -> str:
        """The query range the response says it answers, as YYYYMMDD~YYYYMMDD."""
        raise NotImplementedError

    def _table(self, payload: dict[str, Any]) -> tuple[list[Any], list[Any]]:
        raise NotImplementedError

    def _says_no_data(self, payload: dict[str, Any]) -> bool:
        """Whether a response whose stat is not OK is the exchange's own no-events answer."""
        return False

    def _meta(self, source_url: str, event_date: date, fetched_at: datetime) -> SourceMeta:
        return SourceMeta(
            source=self.SOURCE,
            source_url=source_url,
            fetched_at=fetched_at,
            trade_date=event_date,
            status="official",
            provider=self.PROVIDER,
            is_realtime=False,
            available_fields=("pre_close", "ref_price", "limit_up", "limit_down", "open_ref"),
            source_type="official_open_data",
            freshness_class="eod_snapshot",
        )

    def parse_payload(
        self,
        payload: dict[str, Any],
        start: date,
        end: date,
        source_url: str,
        fetched_at: datetime | None = None,
    ) -> list[CorporateAction]:
        if not isinstance(payload, dict):
            raise ValueError(f"{self.SOURCE} response is not a JSON object")
        stat = str(payload.get("stat", "")).strip()
        if stat.upper() != "OK":
            if self._says_no_data(payload):
                return []
            raise ValueError(f"{self.SOURCE} rejected the query {start}..{end}: stat {stat!r}")
        requested = f"{start:%Y%m%d}~{end:%Y%m%d}"
        echoed = self._echoed_range(payload)
        if echoed != requested:
            raise ValueError(f"{self.SOURCE} answered range {echoed!r} to a query for {requested!r}")
        fields, rows = self._table(payload)
        index = {key: _column_index(fields, title, self.SOURCE) for key, title in self.COLUMNS.items()}
        label_index = _column_index(fields, self.LABEL_COLUMN, self.SOURCE) if self.LABEL_COLUMN else None
        retrieved = fetched_at or datetime.now(TAIPEI)

        actions: list[CorporateAction] = []
        pending = 0
        for row in rows:
            if not isinstance(row, list) or len(row) < len(fields):
                raise ValueError(f"{self.SOURCE} row {row!r} does not have the {len(fields)} listed columns")
            code = _normalized(row[index["code"]])
            if not code:
                raise ValueError(f"{self.SOURCE} row without a security code: {row!r}")
            event_date = parse_event_date(row[index["date"]])
            if not start <= event_date <= end:
                raise ValueError(f"{self.SOURCE} row dated {event_date} is outside {start}..{end}")
            ref_price = _price(row[index["ref_price"]], "ref_price", self.SOURCE)
            if ref_price is None:
                pending += 1
                continue
            if ref_price <= 0:
                raise ValueError(f"{self.SOURCE} reference price for {code} on {event_date} is not positive")
            pre_close = _price(row[index["pre_close"]], "pre_close", self.SOURCE)
            label = _normalized(row[label_index]) if label_index is not None else None
            actions.append(CorporateAction(
                symbol=f"{code}.{self.EXCHANGE}",
                date=event_date,
                kind=self.KIND,
                pre_close=pre_close,
                ref_price=ref_price,
                limit_up=_price(row[index["limit_up"]], "limit_up", self.SOURCE),
                limit_down=_price(row[index["limit_down"]], "limit_down", self.SOURCE),
                open_ref=_price(row[index["open_ref"]], "open_ref", self.SOURCE),
                ex_factor=pre_close / ref_price if pre_close is not None and pre_close > 0 else None,
                official_label=label or None,
                meta=self._meta(source_url, event_date, retrieved),
            ))
        if pending:
            logger.info("%s %s..%s: %d rows not priced yet", self.SOURCE, start, end, pending)
        return actions


class _TwseAdapter(_CorporateActionAdapter):
    PROVIDER = "twse"
    EXCHANGE = "TWSE"

    def build_url(self, start: date, end: date) -> str:
        return f"{self.BASE_URL}?response=json&startDate={start:%Y%m%d}&endDate={end:%Y%m%d}"

    def _echoed_range(self, payload: dict[str, Any]) -> str:
        # TWT49U and TWTAUU echo strDate, TWTCAU startDate, TWTB8U only inside params.
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        echoed_start = payload.get("strDate") or payload.get("startDate") or params.get("startDate")
        echoed_end = payload.get("endDate") or params.get("endDate")
        return f"{str(echoed_start or '').strip()}~{str(echoed_end or '').strip()}"

    def _table(self, payload: dict[str, Any]) -> tuple[list[Any], list[Any]]:
        fields, rows = payload.get("fields"), payload.get("data")
        if not isinstance(fields, list) or not isinstance(rows, list):
            raise ValueError(f"{self.SOURCE} response lacks its fields or data list")
        return fields, rows

    def _says_no_data(self, payload: dict[str, Any]) -> bool:
        if _normalized(payload.get("stat", "")) != _normalized(TWSE_NO_DATA_STAT):
            return False
        if "data" in payload or "fields" in payload:
            raise ValueError(f"{self.SOURCE} answered no data but still sent a data or fields key")
        return True


class _TpexAdapter(_CorporateActionAdapter):
    PROVIDER = "tpex"
    EXCHANGE = "TPEX"

    def build_url(self, start: date, end: date) -> str:
        return f"{self.BASE_URL}?response=json&startDate={start:%Y/%m/%d}&endDate={end:%Y/%m/%d}"

    def _echoed_range(self, payload: dict[str, Any]) -> str:
        return str(payload.get("date") or "").strip()

    def _table(self, payload: dict[str, Any]) -> tuple[list[Any], list[Any]]:
        tables = payload.get("tables")
        if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], dict):
            raise ValueError(f"{self.SOURCE} response does not hold exactly one table")
        table = tables[0]
        fields, rows, total = table.get("fields"), table.get("data"), table.get("totalCount")
        if not isinstance(fields, list) or not isinstance(rows, list):
            raise ValueError(f"{self.SOURCE} table lacks its fields or data list")
        if isinstance(total, bool) or not isinstance(total, int) or total != len(rows):
            raise ValueError(f"{self.SOURCE} returned {len(rows)} rows but reports totalCount {total!r}")
        return fields, rows


class TwseExRightsAdapter(_TwseAdapter):
    BASE_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT49U"
    SOURCE = "twse:twt49u"
    KIND = EXDIV
    COLUMNS: ClassVar[dict[str, str]] = {
        "date": "資料日期",
        "code": "股票代號",
        "pre_close": "除權息前收盤價",
        "ref_price": "除權息參考價",
        "limit_up": "漲停價格",
        "limit_down": "跌停價格",
        "open_ref": "開盤競價基準",
    }
    LABEL_COLUMN = "權/息"


class TwseCapitalReductionAdapter(_TwseAdapter):
    BASE_URL = "https://www.twse.com.tw/rwd/zh/reducation/TWTAUU"
    SOURCE = "twse:twtauu"
    KIND = REDUCTION
    COLUMNS: ClassVar[dict[str, str]] = {
        "date": "恢復買賣日期",
        "code": "股票代號",
        "pre_close": "停止買賣前收盤價格",
        "ref_price": "恢復買賣參考價",
        "limit_up": "漲停價格",
        "limit_down": "跌停價格",
        "open_ref": "開盤競價基準",
    }
    LABEL_COLUMN = "減資原因"


class TwseParValueChangeAdapter(_TwseAdapter):
    BASE_URL = "https://www.twse.com.tw/rwd/zh/change/TWTB8U"
    SOURCE = "twse:twtb8u"
    KIND = PAR_CHANGE
    COLUMNS: ClassVar[dict[str, str]] = {
        "date": "恢復買賣日期",
        "code": "股票代號",
        "pre_close": "停止買賣前收盤價格",
        "ref_price": "恢復買賣參考價",
        "limit_up": "漲停價格",
        "limit_down": "跌停價格",
        "open_ref": "開盤競價基準",
    }


class TwseEtfSplitAdapter(_TwseAdapter):
    BASE_URL = "https://www.twse.com.tw/rwd/zh/split/TWTCAU"
    SOURCE = "twse:twtcau"
    KIND = ETF_SPLIT
    COLUMNS: ClassVar[dict[str, str]] = {
        "date": "恢復買賣日期",
        "code": "ETF代號",
        "pre_close": "停止買賣前收盤價格",
        "ref_price": "恢復買賣參考價",
        "limit_up": "漲停價格",
        "limit_down": "跌停價格",
        "open_ref": "開盤競價基準",
    }
    LABEL_COLUMN = "分割(反分割)"


class TpexExRightsAdapter(_TpexAdapter):
    BASE_URL = "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ"
    SOURCE = "tpex:exdailyq"
    KIND = EXDIV
    COLUMNS: ClassVar[dict[str, str]] = {
        "date": "除權息日期",
        "code": "代號",
        "pre_close": "除權息前收盤價",
        "ref_price": "除權息參考價",
        "limit_up": "漲停價",
        "limit_down": "跌停價",
        "open_ref": "開始交易基準價",
    }
    LABEL_COLUMN = "權/息"


class TpexCapitalReductionAdapter(_TpexAdapter):
    BASE_URL = "https://www.tpex.org.tw/www/zh-tw/bulletin/revivt"
    SOURCE = "tpex:revivt"
    KIND = REDUCTION
    COLUMNS: ClassVar[dict[str, str]] = {
        "date": "恢復買賣日期",
        "code": "股票代號",
        "pre_close": "最後交易日之收盤價格",
        "ref_price": "減資恢復買賣開始日參考價格",
        "limit_up": "漲停價格",
        "limit_down": "跌停價格",
        "open_ref": "開始交易基準價",
    }
    LABEL_COLUMN = "減資原因"


def default_adapters() -> list[_CorporateActionAdapter]:
    return [
        TwseExRightsAdapter(),
        TwseCapitalReductionAdapter(),
        TwseParValueChangeAdapter(),
        TwseEtfSplitAdapter(),
        TpexExRightsAdapter(),
        TpexCapitalReductionAdapter(),
    ]
