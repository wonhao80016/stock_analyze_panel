"""Watchlist screenshot import must read Taiwan codes and resolve them in the security master."""
from __future__ import annotations

from pathlib import Path

import polars as pl

from app.services.watchlist_ocr.pipeline import extract_codes, import_watchlist_image
from app.services.watchlist_ocr.provider import OcrProvider


class _FakeOcr(OcrProvider):
    name = "fake"

    def __init__(self, text: str) -> None:
        self._text = text

    def available(self) -> bool:
        return True

    def extract_text(self, image_bytes: bytes) -> str:
        return self._text


_TAIWAN_SCREENSHOT_TEXT = (
    "自選清單 2026/08/28\n"
    "台積電 2330 1050.00 +1.45%\n"
    "鴻海 2317 205.5 -0.24%\n"
    "元大台灣50 0050 180.35\n"
    "國泰永續高股息 00878 22.18\n"
    "富邦台50 006208 105.20\n"
    "元大台灣50正2 00631L 230.10\n"
    "台積電 2330 重複\n"
)


def _run(text: str, data_dir: Path, existing: set[str] | None = None) -> dict:
    return import_watchlist_image(
        b"fake-bytes", data_dir, existing_symbols=existing or set(), provider=_FakeOcr(text)
    )


def test_taiwan_codes_of_four_to_six_characters_are_extracted_and_matched(tmp_path: Path):
    result = _run(_TAIWAN_SCREENSHOT_TEXT, tmp_path)

    assert result["codes"] == ["2330", "2317", "0050", "00878", "006208", "00631L"]
    by_code = {c["code"]: c for c in result["candidates"]}
    assert by_code["2330"]["symbol"] == "2330.TWSE"
    assert by_code["2330"]["name"] == "台積電"
    assert by_code["00878"]["symbol"] == "00878.TWSE"
    assert by_code["00631L"]["symbol"] == "00631L.TWSE"
    assert by_code["00631L"]["name"] == "元大台灣50正2"
    assert result["matched_count"] == 6
    assert result["unmatched_count"] == 0


def test_tpex_code_resolves_to_tpex_symbol(tmp_path: Path):
    result = _run("環球晶 6488 450.0\n元太 8069", tmp_path)

    assert [c["symbol"] for c in result["candidates"]] == ["6488.TPEX", "8069.TPEX"]


def test_decimal_prices_are_not_read_as_codes(tmp_path: Path):
    result = _run("台積電 2330 1050.00\n成交價 1101.5", tmp_path)

    assert result["codes"] == ["2330"]


def test_existing_watchlist_symbol_is_flagged(tmp_path: Path):
    result = _run("2330\n2317", tmp_path, existing={"2330.TWSE"})

    flags = {c["code"]: c["already_in_watchlist"] for c in result["candidates"]}
    assert flags == {"2330": True, "2317": False}


def test_legacy_a_share_instruments_are_not_consulted(tmp_path: Path):
    inst = tmp_path / "instruments"
    inst.mkdir()
    pl.DataFrame(
        {"code": ["600036"], "symbol": ["600036.SH"], "name": ["招商银行"]}
    ).write_parquet(inst / "instruments.parquet")

    result = _run("招商银行 600036\n台積電 2330", tmp_path)

    by_code = {c["code"]: c for c in result["candidates"]}
    assert by_code["600036"]["matched"] is False
    assert by_code["2330"]["symbol"] == "2330.TWSE"


def test_split_fragments_join_only_into_a_known_code(tmp_path: Path):
    assert _run("國泰永續高股息 008 78", tmp_path)["codes"] == ["00878"]
    assert _run("台積電 2330 15 張", tmp_path)["codes"] == ["2330"]


def test_numbers_followed_by_a_unit_are_not_codes():
    assert extract_codes("2025年10月24日 自選\n2330") == ["2330"]
    assert extract_codes("成交量 2025 張\n2330") == ["2330"]
    assert extract_codes("1050 張\n2330") == ["2330"]
    assert extract_codes("漲幅 1234% 2330") == ["2330"]
    assert extract_codes("2330\n元大台灣50") == ["2330"]


_NAMES_STARTING_WITH_A_UNIT = [
    ("0050 元大台灣50", "0050"),
    ("00631L 元大台灣50正2", "00631L"),
    ("8069 元太", "8069"),
    ("2885 元大金", "2885"),
    ("3711 日月光投控", "3711"),
    ("1451 年興", "1451"),
    ("3680 家登", "3680"),
]


def test_code_followed_by_a_name_starting_with_a_unit_is_kept():
    for text, code in _NAMES_STARTING_WITH_A_UNIT:
        assert extract_codes(text) == [code], text


def test_master_listed_names_starting_with_a_unit_are_matched(tmp_path: Path):
    result = _run("元大台灣50 0050 元大台灣50\n00631L 元大台灣50正2\n8069 元太", tmp_path)

    assert [c["symbol"] for c in result["candidates"]] == ["0050.TWSE", "00631L.TWSE", "8069.TPEX"]


def test_master_name_after_the_code_overrides_the_unit_rule():
    assert extract_codes("1234 元1號", code_names={"1234": "元1號"}) == ["1234"]
    assert extract_codes("1234 元1號") == []


def test_dates_and_times_stay_excluded():
    assert extract_codes("2025-10-24 2025/10/24 1030:15 2330") == ["2330"]


def test_dash_before_a_name_does_not_block_the_code():
    assert extract_codes("2330-台積電") == ["2330"]


def test_colon_before_the_code_does_not_block_it():
    assert extract_codes("代號:2330") == ["2330"]
    assert extract_codes("TWSE:2330") == ["2330"]


def test_code_glued_to_an_english_word_is_extracted():
    assert extract_codes("2330TSMC") == ["2330"]


def test_single_letter_after_digits_stays_a_suffix():
    assert extract_codes("元大台灣50正2 00631L") == ["00631L"]


def test_split_fragment_with_letter_suffix_joins_into_a_known_code(tmp_path: Path):
    assert _run("元大台灣50正2 0063 1L", tmp_path)["codes"] == ["00631L"]
