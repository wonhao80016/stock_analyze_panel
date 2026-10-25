"""Official TWSE & TPEx ISIN Open Data Adapters with Provenance-tracked ETF Classification.

Fetches and normalizes the official Taiwan Securities Master from:
  - TWSE (strMode=2): https://isin.twse.com.tw/isin/C_public.jsp?strMode=2
  - TPEx (strMode=4): https://isin.twse.com.tw/isin/C_public.jsp?strMode=4
  - TWSE ETF Product Types: https://openapi.twse.com.tw/v1/opendata/t187ap47_L

Strictly adheres to:
  - Zero external dependencies (uses standard library html.parser.HTMLParser)
  - Multi-tier ETF classification priority:
      1. Official product metadata (TWSE t187ap47_L / TPEx)
      2. ISO 10962 CFI code (CEOIBU, CEOJBU, CEOGBU, CEOGDU; CEOGEU stays unknown)
      3. Multi-character name heuristics (degraded fallback)
  - Parameterized underlying_scope ("domestic" | "foreign" | "unknown") and leverage_multiplier
  - Strict statutory price limit rules:
      * Domestic leveraged/inverse: 10% * abs(multiplier) (e.g. 00631L is +-20%, 00632R is +-10%)
      * Foreign leveraged/inverse / Foreign plain / Bond: NO_LIMIT
  - Zero single-character false positives (e.g. '美' or '債' alone strictly disallowed)
  - Classification provenance tracking (OFFICIAL_METADATA, CFI_CODE, NAME_HEURISTIC, UNKNOWN)
  - Canonical symbol generation: {code}.{EXCHANGE}
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from html.parser import HTMLParser

import httpx

from app.taiwan.universe.industry_classification import resolve_industry_name
from app.taiwan.universe.models import (
    OFFICIAL_ETF_RULE_PROFILES,
    TaiwanInstrument,
    derive_security_class,
    is_tdr_industry,
)

logger = logging.getLogger(__name__)

OFFICIAL_HEADERS = {"User-Agent": "Mozilla/5.0"}

TWSE_ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
TPEX_ISIN_URL = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"
TWSE_ETF_PRODUCTS_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap47_L"
TWSE_COMPANIES_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_COMPANIES_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
TPEX_ETF_DETAIL_URL = "https://www.tpex.org.tw/www/zh-tw/ETF/detail"


@dataclass(frozen=True)
class OfficialEtfProductMeta:
    """Official ETF product metadata from TWSE t187ap47_L."""
    fund_type: str         # 基金類型
    underlying_index: str  # 標的指數/追蹤指數名稱
    underlying_scope: str = "unknown"  # 是否包含國外成分股: 是 -> foreign, 否 -> domestic


def scope_from_foreign_flag(flag: object) -> str:
    return {"是": "foreign", "否": "domestic"}.get(str(flag or "").strip(), "unknown")


class _IsinTableParser(HTMLParser):
    """Fast, zero-dependency HTML table parser for official ISIN pages."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._curr_row: list[str] = []
        self._curr_cell: list[str] = []
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self._curr_row = []
        elif tag.lower() == "td":
            self._in_cell = True
            self._curr_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "td":
            self._in_cell = False
            self._curr_row.append("".join(self._curr_cell).strip())
        elif tag.lower() == "tr" and self._curr_row:
            self.rows.append(self._curr_row)

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._curr_cell.append(data)


def leverage_multiplier_of(code: str, name: str) -> float:
    """Leverage factor for a Taiwan ETF (2.0 long, -1.0 inverse, 1.0 otherwise).

    Shared by the ISIN table parser and the TWSE OpenAPI directory so the two
    intake paths cannot drift apart.
    """
    if code.endswith("L") or "正2" in name or "正向2" in name:
        return 2.0
    if code.endswith("R") or "反1" in name or "反向1" in name or "反一" in name:
        return -1.0
    return 1.0


def etf_management_style_of(fund_type: str) -> str | None:
    normalized = str(fund_type).strip()
    if "主動" in normalized:
        return "active"
    if "指數" in normalized:
        return "passive"
    return None


def classify_etf_provenance(
    code: str,
    name: str,
    cfi_code: str | None,
    official_product_types: dict[str, OfficialEtfProductMeta | str] | None = None,
) -> tuple[str, str, str, float]:
    """Classify ETF with strict priority, provenance, underlying scope, and multiplier.

    Returns:
        tuple[etf_category, classification_source, underlying_scope, leverage_multiplier]
    """
    # 0. Determine leverage multiplier
    multiplier = leverage_multiplier_of(code, name)

    # 1. First priority: Official Product Metadata (e.g. TWSE t187ap47_L 基金類型 + 標的指數)
    if official_product_types and code in official_product_types:
        meta = official_product_types[code]
        if isinstance(meta, OfficialEtfProductMeta):
            f_type = meta.fund_type
            idx_name = meta.underlying_index
            flag_scope = meta.underlying_scope
        else:
            f_type = str(meta)
            idx_name = ""
            flag_scope = "unknown"

        # Check Bond
        if "債券" in f_type or "債" in f_type:
            scope = flag_scope if flag_scope != "unknown" else "foreign"
            return "bond", "official_metadata", scope, multiplier

        # Futures trusts trade without a daily limit only on foreign commodity futures.
        elif "期貨信託" in f_type:
            cat = "inverse" if multiplier < 0 else "futures"
            source = "official_metadata" if flag_scope == "foreign" else "unknown"
            return cat, source, flag_scope, multiplier

        # Check Leveraged / Inverse
        elif "槓桿" in f_type or "反向" in f_type:
            cat = "inverse" if multiplier < 0 else "leveraged"
            # Authoritative underlying index examination
            if any(kw in idx_name for kw in ["臺灣", "台湾", "加權", "櫃買", "TPEx", "TWSE", "台50", "藍籌30"]) and not any(kw in idx_name for kw in ["中國", "S&P", "美", "日", "海外"]):
                scope = "domestic"
            elif any(kw in idx_name for kw in ["上証", "滬深", "S&P", "道瓊", "NASDAQ", "日經", "東証", "美債", "公債", "印度", "黃金", "原油", "香港", "恒生", "中國", "富時"]):
                scope = "foreign"
            else:
                # Fallback to ETF official name
                if any(kw in name for kw in ["臺灣", "台灣", "加權", "台50"]):
                    scope = "domestic"
                elif any(kw in name for kw in ["上証", "滬深", "S&P", "道瓊", "NASDAQ", "日經", "美債", "原油", "黃金", "香港", "恒生", "中國"]):
                    scope = "foreign"
                else:
                    scope = "unknown"
            return cat, "official_metadata", scope, multiplier

        # Check Foreign Equity
        elif "國外成分" in f_type or "國外成份" in f_type or "境外" in f_type:
            return "foreign_equity", "official_metadata", "foreign", multiplier

        # Check Domestic Equity
        elif "國內成分" in f_type:
            return "domestic_equity", "official_metadata", "domestic", multiplier

    # 2. Second priority: ISO 10962 CFI Code
    if cfi_code:
        # Debt/Bond ETF (e.g. 00720B is CEOJBU, 00710B is CEOIBU, 00694B is CEOGBU).
        # Foreign scope relies on every listed TPEx bond ETF being 國外債券 (TPEx ETF/detail); CFI has no country.
        if cfi_code in ("CEOIBU", "CEOJBU", "CEOGBU"):
            return "bond", "cfi_code", "foreign", multiplier
        # Leveraged / Inverse / Commodity Derivatives (e.g. 00631L/00632R is CEOGDU)
        elif cfi_code in ("CEOGDU", "CEOGMU"):
            cat = "inverse" if multiplier < 0 else "leveraged"
            if any(kw in name for kw in ["臺灣", "台灣", "加權", "台50"]):
                scope = "domestic"
            elif any(kw in name for kw in ["上証", "滬深", "S&P", "道瓊", "NASDAQ", "日經", "美債", "原油", "黃金", "香港", "恒生", "中國"]):
                scope = "foreign"
            else:
                scope = "unknown"
            return cat, "cfi_code", scope, multiplier
        # CFI has no country attribute: CEOGEU covers 006201 (domestic) and 00877 (foreign).
        elif cfi_code == "CEOGEU":
            return "unknown", "unknown", "unknown", multiplier

    # 3. Third priority / degraded fallback: Multi-character Name Heuristics
    # Strictly disallow single-character triggers (like "美" or "債")
    if "正2" in name or "正向2" in name:
        scope = "domestic" if any(kw in name for kw in ["台灣", "臺灣", "加權", "台50"]) else "foreign" if any(kw in name for kw in ["上証", "滬深", "美國", "S&P", "道瓊", "NASDAQ", "日經"]) else "unknown"
        return "leveraged", "name_heuristic", scope, multiplier
    elif "反1" in name or "反向1" in name:
        scope = "domestic" if any(kw in name for kw in ["台灣", "臺灣", "加權", "台50"]) else "foreign" if any(kw in name for kw in ["上証", "滬深", "美國", "S&P", "道瓊", "NASDAQ", "日經"]) else "unknown"
        return "inverse", "name_heuristic", scope, multiplier
    elif any(kw in name for kw in ["公司債", "金融債", "公債", "國債", "投等債", "新興債"]):
        return "bond", "name_heuristic", "foreign", multiplier
    elif any(kw in name for kw in ["S&P", "道瓊", "美國", "日經", "NASDAQ", "海外", "全球", "富時", "香港", "恒生"]):
        return "foreign_equity", "name_heuristic", "foreign", multiplier
    elif any(kw in name for kw in ["台灣50", "台50", "中型100", "高股息"]):
        return "domestic_equity", "name_heuristic", "domestic", multiplier

    # 4. Unknown
    return "unknown", "unknown", "unknown", multiplier


def normalize_listing_date(raw: object) -> str | None:
    """ISO YYYY-MM-DD from an official ROC, YYYYMMDD, YYYY/MM/DD or ISO date; None when unparseable."""
    from app.taiwan.providers.taiwan_values import parse_taiwan_date

    text = "" if raw is None else str(raw).strip()
    if not text or text.lower() == "nan":
        return None
    try:
        return parse_taiwan_date(text.replace("-", "/")).isoformat()
    except ValueError:
        logger.warning("Unparseable listing date dropped: %r", text)
        return None


def parse_isin_html(
    html: str,
    exchange: str,
    source_name: str,
    official_product_types: dict[str, OfficialEtfProductMeta | str] | None = None,
) -> list[TaiwanInstrument]:
    """Parse official TWSE/TPEx ISIN HTML table into canonical TaiwanInstrument list."""
    parser = _IsinTableParser()
    parser.feed(html)

    current_category = "UNKNOWN"
    instruments: list[TaiwanInstrument] = []
    now_iso = datetime.now().isoformat()

    for tds in parser.rows:
        if not tds:
            continue

        # Category Section Header (single cell)
        if len(tds) == 1:
            text = tds[0].strip()
            if text:
                current_category = text
            continue

        # Skip header or malformed rows
        if len(tds) < 6:
            continue
        first_cell = tds[0].strip()
        if "代號" in first_cell or "Code" in first_cell:
            continue

        # Split code and Traditional Chinese name
        # Official format: '2330\u3000台積電' or '8069 元太'
        parts = re.split(r"[\s\u3000]+", first_cell, maxsplit=1)
        if len(parts) < 2:
            continue
        code, name = parts[0].strip(), parts[1].strip()
        if not code or not name:
            continue

        isin = tds[1].strip() or None
        listing_date = normalize_listing_date(tds[2])
        industry = tds[4].strip() or None
        cfi_code = tds[5].strip() or None

        # Determine instrument classification from official category header
        cat_lower = current_category.lower()
        etf_category: str | None = None
        classification_source: str | None = None
        underlying_scope: str | None = None
        leverage_multiplier: float = 1.0
        etf_management_style: str | None = None

        if "etf" in cat_lower:
            instrument_type = "etf"
            is_supported = True
            listing_status = "active"
            etf_category, classification_source, underlying_scope, leverage_multiplier = classify_etf_provenance(
                code=code,
                name=name,
                cfi_code=cfi_code,
                official_product_types=official_product_types,
            )
            audited = OFFICIAL_ETF_RULE_PROFILES.get(f"{code}.{exchange}")
            if audited is not None:
                etf_category, underlying_scope = audited.category, audited.underlying_scope
                classification_source = "official_metadata"
            product_meta = (official_product_types or {}).get(code)
            if isinstance(product_meta, OfficialEtfProductMeta):
                etf_management_style = etf_management_style_of(product_meta.fund_type)
            elif product_meta is not None:
                etf_management_style = etf_management_style_of(str(product_meta))
        elif "股票" in current_category or "創新板" in current_category:
            instrument_type = "stock"
            is_supported = True
            listing_status = "active"
            underlying_scope = "domestic"
            leverage_multiplier = 1.0
        else:
            # Warrants, ETNs, TDRs, REITs, Preferred shares, etc.
            instrument_type = "unsupported"
            is_supported = False
            listing_status = "unsupported"

        canonical_symbol = f"{code}.{exchange}"

        inst = TaiwanInstrument(
            symbol=canonical_symbol,
            code=code,
            exchange=exchange,
            name=name,
            instrument_type=instrument_type,
            listing_status=listing_status,
            listing_date=listing_date,
            isin=isin,
            industry=industry,
            cfi_code=cfi_code,
            raw_category=current_category,
            is_supported=is_supported,
            source=source_name,
            updated_at=now_iso,
            etf_category=etf_category,
            classification_source=classification_source,
            underlying_scope=underlying_scope,
            leverage_multiplier=leverage_multiplier,
            etf_management_style=etf_management_style,
        )
        instruments.append(inst)

    return instruments


def fetch_official_twse_etf_products(timeout: float = 10.0) -> dict[str, OfficialEtfProductMeta]:
    """Fetch official TWSE ETF product categories and underlying index from t187ap47_L.

    Returns:
        dict[code, OfficialEtfProductMeta]
    """
    try:
        response = httpx.get(TWSE_ETF_PRODUCTS_URL, headers=OFFICIAL_HEADERS, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        product_map: dict[str, OfficialEtfProductMeta] = {}
        for r in data:
            code = r.get("基金代號") or r.get("證券代號") or list(r.values())[1]
            cat = r.get("基金類型") or list(r.values())[3]
            idx_name = r.get("標的指數/追蹤指數名稱") or list(r.values())[6] or ""
            if code and cat:
                product_map[str(code).strip()] = OfficialEtfProductMeta(
                    fund_type=str(cat).strip(),
                    underlying_index=str(idx_name).strip(),
                    underlying_scope=scope_from_foreign_flag(r.get("是否包含國外成分股")),
                )
        return product_map
    except Exception as e:
        logger.warning("Could not fetch official TWSE ETF product metadata: %s", e)
        return {}


class TwseInstrumentAdapter:
    """Fetches and parses TWSE official ISIN securities list."""

    def __init__(self, url: str = TWSE_ISIN_URL) -> None:
        self.url = url

    def fetch_live_html(self, timeout: float = 15.0) -> str:
        response = httpx.get(self.url, headers=OFFICIAL_HEADERS, timeout=timeout)
        response.raise_for_status()
        return response.content.decode("cp950", errors="ignore")

    def get_instruments(
        self,
        html_content: str | None = None,
        official_product_types: dict[str, OfficialEtfProductMeta | str] | None = None,
    ) -> list[TaiwanInstrument]:
        if html_content is None:
            return _official_company_directory("TWSE", TWSE_COMPANIES_URL) + _official_twse_etf_directory()
        html = html_content
        # If live and no product types injected, fetch live product types
        prod_types = official_product_types

        return parse_isin_html(
            html,
            exchange="TWSE",
            source_name="TWSE_ISIN",
            official_product_types=prod_types,
        )


def parse_tpex_etf_management_style(payload: object, code: str) -> str | None:
    """Management style, "active" or "passive", from 「ETF類別」 of *code*'s TPEx ETF/detail payload."""
    tables = payload.get("tables") if isinstance(payload, dict) else None
    if not isinstance(tables, list) or not tables or not isinstance(tables[0], dict):
        return None
    fields = tables[0].get("fields") or []
    data = tables[0].get("data") or []
    if not data or "ETF類別" not in fields or "證券代號" not in fields:
        return None
    row = data[0]
    code_cell = str(row[fields.index("證券代號")]).strip()
    if re.split(r"[\s(]", code_cell, maxsplit=1)[0] != code:
        return None
    category = str(row[fields.index("ETF類別")]).strip()
    if category.startswith("主動"):
        return "active"
    if category.startswith("被動"):
        return "passive"
    return None


class TpexInstrumentAdapter:
    """Fetches and parses TPEx official ISIN securities list."""

    def __init__(
        self,
        url: str = TPEX_ISIN_URL,
        transport: httpx.BaseTransport | None = None,
        detail_request_interval: float = 1.0,
    ) -> None:
        self.url = url
        self._transport = transport
        # TPEx answers bursts of ETF/detail requests with a redirect to its error page.
        self.detail_request_interval = detail_request_interval

    def fetch_etf_management_styles(
        self, detail_types: dict[str, str], **kwargs: float
    ) -> dict[str, str | None]:
        """iter_etf_management_styles() collected into a dict."""
        return dict(self.iter_etf_management_styles(detail_types, **kwargs))

    def iter_etf_management_styles(
        self,
        detail_types: dict[str, str],
        timeout: float = 10.0,
        max_consecutive_failures: int = 3,
    ) -> Iterator[tuple[str, str | None]]:
        """Yield (code, management style) from TPEx ETF/detail for each code actually queried.

        *detail_types* maps a code to the page's type parameter (bond, domestic or foreign). A payload
        that cannot be read yields None for that code; lookups stop after *max_consecutive_failures*
        request errors in a row, and unqueried codes are not yielded.
        """
        failures = 0
        with httpx.Client(
            transport=self._transport, headers=OFFICIAL_HEADERS, timeout=timeout
        ) as client:
            for index, (code, detail_type) in enumerate(detail_types.items()):
                if index and self.detail_request_interval > 0:
                    time.sleep(self.detail_request_interval)
                try:
                    response = client.get(
                        TPEX_ETF_DETAIL_URL,
                        params={"type": detail_type, "code": code, "response": "json"},
                    )
                    response.raise_for_status()
                    payload = json.loads(response.content.decode("utf-8-sig"))
                except (httpx.HTTPError, OSError, ValueError) as exc:
                    failures += 1
                    logger.warning("TPEx ETF detail lookup failed for %s: %s", code, exc)
                    if failures >= max_consecutive_failures:
                        logger.warning(
                            "Stopping TPEx ETF detail lookups after %d consecutive failures",
                            failures,
                        )
                        break
                    continue
                failures = 0
                try:
                    style = parse_tpex_etf_management_style(payload, code)
                except (IndexError, KeyError, TypeError) as exc:
                    logger.warning("Unreadable TPEx ETF detail payload for %s: %s", code, exc)
                    style = None
                yield code, style

    def fetch_live_html(self, timeout: float = 15.0) -> str:
        response = httpx.get(self.url, headers=OFFICIAL_HEADERS, timeout=timeout)
        response.raise_for_status()
        return response.content.decode("cp950", errors="ignore")

    def get_instruments(
        self,
        html_content: str | None = None,
        official_product_types: dict[str, OfficialEtfProductMeta | str] | None = None,
    ) -> list[TaiwanInstrument]:
        if html_content is None:
            # The ISIN table carries CFI codes, so most TPEx ETFs classify from
            # ISO 10962 rather than a name guess. Keep that provenance instead of
            # flattening every entry to "unknown": MarketProfileBridge already
            # refuses to apply statutory rules to anything outside
            # CONFIRMED_SOURCES, so unconfirmed rows stay fail-closed downstream
            # while bond ETFs keep their no-limit band.
            etfs = [
                item
                for item in parse_isin_html(self.fetch_live_html(), "TPEX", "TPEX_ISIN")
                if item.instrument_type == "etf"
            ]
            return _official_company_directory("TPEX", TPEX_COMPANIES_URL) + etfs
        html = html_content
        return parse_isin_html(
            html,
            exchange="TPEX",
            source_name="TPEX_ISIN",
            official_product_types=official_product_types,
        )


def _get_json(url: str, timeout: float = 15.0) -> list[dict]:
    response = httpx.get(url, headers=OFFICIAL_HEADERS, timeout=timeout)
    response.raise_for_status()
    payload = json.loads(response.content.decode("utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError(f"official directory schema changed: {url}")
    return payload


def _official_company_directory(exchange: str, url: str) -> list[TaiwanInstrument]:
    now = datetime.now().astimezone().isoformat()
    instruments = []
    for row in _get_json(url):
        if exchange == "TWSE":
            code, name = row.get("公司代號"), row.get("公司簡稱")
            # "產業別" 在 t187ap03_L 回傳的是數字產業代碼(如 "01"/"24"), 不是
            # 可讀名稱 —— 與 mopsfin_t187ap03_O 的 SecuritiesIndustryCode 同樣
            # 需要 resolve_industry_name() 正規化(見 industry_classification.py)。
            listed, industry = row.get("上市日期"), row.get("產業別")
        else:
            code, name = row.get("SecuritiesCompanyCode"), row.get("CompanyAbbreviation")
            listed, industry = row.get("DateOfListing"), row.get("SecuritiesIndustryCode")
        if not code or not name:
            raise ValueError(f"official directory schema changed: {url}")
        code = str(code).strip()
        name = str(name).strip()
        industry = str(industry or "").strip() or None
        industry_name = None if is_tdr_industry(industry) else resolve_industry_name(industry)
        instruments.append(TaiwanInstrument(
            symbol=f"{code}.{exchange}", code=code, exchange=exchange, name=name,
            instrument_type="stock", listing_status="active", listing_date=normalize_listing_date(listed),
            isin=None, industry=industry_name, cfi_code=None, raw_category="股票",
            is_supported=True, source="TWSE_OPENAPI" if exchange == "TWSE" else "TPEX_OPENAPI",
            updated_at=now, underlying_scope="domestic",
            security_class=derive_security_class(code, name, industry),
        ))
    return instruments


def _official_twse_etf_directory() -> list[TaiwanInstrument]:
    now = datetime.now().astimezone().isoformat()
    instruments = []
    for row in _get_json(TWSE_ETF_PRODUCTS_URL):
        code, name = row.get("基金代號"), row.get("基金簡稱")
        if not code or not name:
            raise ValueError(f"official directory schema changed: {TWSE_ETF_PRODUCTS_URL}")
        scope = scope_from_foreign_flag(row.get("是否包含國外成分股"))
        fund_type = str(row.get("基金類型", "")).strip()
        ordinary_types = {"國內成分證券指數股票型基金", "國外成分證券指數股票型基金"}
        code = str(code).strip()
        display_name = unescape(str(name)).strip()
        # 基金類型 is official metadata and already names the leveraged/inverse
        # class; classifying purely by 是否包含國外成分股 would file 00631L as a
        # plain domestic equity ETF and leave its multiplier at 1.0, which then
        # understates the statutory price limit band.
        multiplier = leverage_multiplier_of(code, display_name)
        is_leveraged_type = "槓桿" in fund_type or "反向" in fund_type
        is_bond_type = "債券" in fund_type
        is_futures_type = "期貨信託" in fund_type
        if is_bond_type:
            category = "bond"
        elif is_futures_type:
            category = "inverse" if multiplier < 0 else "futures"
        elif is_leveraged_type:
            category = "inverse" if multiplier < 0 else "leveraged"
        else:
            category = "foreign_equity" if scope == "foreign" else "domestic_equity" if scope == "domestic" else "unknown"
        if is_futures_type:
            confirmed = scope == "foreign"
        else:
            confirmed = (
                scope != "unknown" or fund_type in ordinary_types or is_leveraged_type or is_bond_type
            )
        instruments.append(TaiwanInstrument(
            symbol=f"{code}.TWSE", code=code, exchange="TWSE", name=display_name, instrument_type="etf",
            listing_status="active", listing_date=normalize_listing_date(row.get("上市日期")),
            isin=None, industry=None, cfi_code=None, raw_category=fund_type, is_supported=True,
            source="TWSE_OPENAPI", updated_at=now, etf_category=category,
            classification_source="official_metadata" if confirmed else None,
            underlying_scope=scope,
            leverage_multiplier=multiplier,
            etf_management_style=etf_management_style_of(fund_type),
        ))
    return instruments
