"""截图 → OCR 文本 → 抽代码 → 台股证券主档校验。"""
from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.services.watchlist_ocr.provider import OcrProvider, get_ocr_provider
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master

# Taiwan codes: 4-digit stocks, 5-6 digit ETFs/warrants, one optional letter suffix (00631L);
# two or more trailing letters are a separate word (2330TSMC).
_LETTER_SUFFIX = r"(?:[A-Za-z](?![A-Za-z]))?"
# Digits joined to more digits by "-", ":", "/", "." or "," are dates, times, prices or volumes.
_CODE_RE = re.compile(
    rf"(?<![0-9A-Za-z.,/-])(\d{{4,6}}{_LETTER_SUFFIX})"
    rf"(?![0-9]|[A-Za-z](?![A-Za-z])|[-:/.,]\d)"
)
_SPLIT_CODE_RE = re.compile(rf"(?<!\d)(\d{{3,5}})\s+(\d{{1,3}}{_LETTER_SUFFIX})(?![0-9A-Za-z])")
_SAME_LINE_GAP_RE = re.compile(r"[^\S\r\n]*")
# A unit counts only when it ends the word: names such as 元太 or 日月光 start with a unit character.
_TRAILING_UNIT_RE = re.compile(
    r"[^\S\r\n]*[年月日張张股元%\N{FULLWIDTH PERCENT SIGN}倍筆笔次檔档家](?![^\W\d])"
)
_NAME_PREFIX_LEN = 2


@dataclass
class ImportCandidate:
    code: str
    symbol: str | None
    name: str | None
    matched: bool
    already_in_watchlist: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_followed_by_name(text: str, end: int, name: str) -> bool:
    start = _SAME_LINE_GAP_RE.match(text, end).end()
    return bool(name) and text.startswith(name[:_NAME_PREFIX_LEN], start)


def extract_codes(
    text: str,
    known_codes: Collection[str] = (),
    code_names: Mapping[str, str] | None = None,
) -> list[str]:
    """Extract unique Taiwan codes from OCR text in order of appearance.

    A split fragment such as "008 78" is rejoined only when the joined code is in known_codes and
    the head is not, so "2330 15" (code plus quantity) stays "2330". A number followed by a unit
    (2025年, 1050 張) is dropped unless the text after it starts with the code's name in code_names.
    """
    names = code_names or {}
    if not text:
        return []

    def _join_split(m: re.Match[str]) -> str:
        head, joined = m.group(1), (m.group(1) + m.group(2)).upper()
        if joined in known_codes and head not in known_codes:
            return joined
        return m.group(0)

    normalized = _SPLIT_CODE_RE.sub(_join_split, text)

    seen: set[str] = set()
    codes: list[str] = []
    for m in _CODE_RE.finditer(normalized):
        code = m.group(1).upper()
        if code in seen:
            continue
        if not _is_followed_by_name(normalized, m.end(), names.get(code, "")) and _TRAILING_UNIT_RE.match(
            normalized, m.end()
        ):
            continue
        seen.add(code)
        codes.append(code)
    return codes


def build_instrument_lookups(
    security_master: TaiwanSecurityMaster,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build code->symbol and symbol->name from the supported Taiwan security master entries."""
    code_to_symbol: dict[str, str] = {}
    symbol_to_name: dict[str, str] = {}
    frame = security_master.to_dataframe(supported_only=True)
    for row in frame.select("symbol", "code", "name").iter_rows(named=True):
        symbol = row["symbol"]
        code_to_symbol.setdefault(row["code"].upper(), symbol)
        if row["name"]:
            symbol_to_name[symbol] = row["name"]
    return code_to_symbol, symbol_to_name


def resolve_candidates(
    codes: list[str],
    code_to_symbol: dict[str, str],
    symbol_to_name: dict[str, str],
    existing_symbols: set[str] | None = None,
) -> list[ImportCandidate]:
    existing = existing_symbols or set()
    out: list[ImportCandidate] = []
    for code in codes:
        symbol = code_to_symbol.get(code)
        matched = symbol is not None
        name = symbol_to_name.get(symbol) if symbol else None
        out.append(
            ImportCandidate(
                code=code,
                symbol=symbol,
                name=name,
                matched=matched,
                already_in_watchlist=bool(symbol and symbol in existing),
            )
        )
    return out


def import_watchlist_image(
    image_bytes: bytes,
    data_dir: Path | None = None,
    *,
    existing_symbols: set[str] | None = None,
    provider: OcrProvider | None = None,
    security_master: TaiwanSecurityMaster | None = None,
) -> dict[str, Any]:
    """识别截图并返回候选列表（不写入自选）。

    data_dir is kept for caller compatibility only; codes resolve against the Taiwan security master.
    """
    ocr = provider or get_ocr_provider()
    if not ocr.available():
        raise RuntimeError(
            f"OCR 引擎「{ocr.name}」不可用。请安装 Tesseract："
            "macOS 执行 brew install tesseract；"
            "Windows 可安装 UB Mannheim 发行版或执行 choco install tesseract；"
            "Linux/Docker 安装 tesseract-ocr（官方镜像已内置）。"
        )

    text = ocr.extract_text(image_bytes)
    code_to_symbol, symbol_to_name = build_instrument_lookups(security_master or get_security_master())
    code_names = {code: symbol_to_name.get(symbol, "") for code, symbol in code_to_symbol.items()}
    codes = extract_codes(text, code_to_symbol.keys(), code_names)
    candidates = resolve_candidates(codes, code_to_symbol, symbol_to_name, existing_symbols)

    matched = [c for c in candidates if c.matched]
    unmatched = [c for c in candidates if not c.matched]

    return {
        "provider": ocr.name,
        "raw_text": text,
        "codes": codes,
        "candidates": [c.to_dict() for c in candidates],
        "matched_count": len(matched),
        "unmatched_count": len(unmatched),
    }
