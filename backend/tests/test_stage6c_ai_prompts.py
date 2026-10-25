"""AI prompts speak Traditional Chinese, describe Taiwan data and state every field unit."""
from __future__ import annotations

import re

import pytest

from app.strategy.custom_signals_ai import build_messages

# Characters whose Simplified form differs from the Traditional one.
SIMPLIFIED = set("们这说时发设计数据价场规则输选择简态该应须参项单买卖涨线统额换动资码档级类标条务种门间问题处实现过还进连产业员长东车马号")


def _signal_prompt() -> str:
    return build_messages("回踩MA20且放量")[0]["content"]


def _simplified(text: str) -> list[str]:
    return sorted({ch for ch in text if ch in SIMPLIFIED})


# ── B164 / B093 ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "unit"),
    [
        ("turnover_rate", "百分數"),
        ("change_pct", "小數"),
        ("amplitude", "小數"),
        ("momentum_10d", "小數"),
        ("momentum_60d", "小數"),
        ("annual_vol_20d", "小數"),
        ("distance_to_upper_limit", "小數"),
        ("volume", "股"),
        ("amount", "元"),
    ],
)
def test_signal_prompt_states_each_field_unit(field, unit):
    entry = re.search(rf"(?<![a-z0-9_]){field}\(([^)]*)\)", _signal_prompt())
    assert entry is not None
    assert f"單位: {unit}" in entry.group(1)


# ── B165 / B166 ─────────────────────────────────────────


def test_signal_prompt_is_taiwan_and_traditional():
    text = _signal_prompt()
    assert "A股" not in text and "A 股" not in text
    assert "台股" in text
    assert "繁體中文" in text
    assert _simplified(text) == []


def test_signal_prompt_states_taiwan_volume_and_amount_units():
    text = _signal_prompt()
    assert "1 張 = 1000 股" in text
    assert "amount" in text and "以元計" in text
