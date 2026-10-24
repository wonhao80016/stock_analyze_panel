"""AI prompts speak Traditional Chinese, describe Taiwan data and state every field unit."""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from app.strategy import ai_generator
from app.strategy.ai_generator import _SYSTEM_PREFIX, AIStrategyGenerator
from app.strategy.custom_signals_ai import build_messages

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "backend" / "app" / "strategy" / "prompts"
COMPACT = (PROMPTS / "strategy-guide-compact.md").read_text(encoding="utf-8")
STEP2 = (PROMPTS / "strategy-builder-step2.md").read_text(encoding="utf-8")

# Characters whose Simplified form differs from the Traditional one.
SIMPLIFIED = set("们这说时发设计数据价场规则输选择简态该应须参项单买卖涨线统额换动资码档级类标条务种门间问题处实现过还进连产业员长东车马号")


def _signal_prompt() -> str:
    return build_messages("回踩MA20且放量")[0]["content"]


def _simplified(text: str) -> list[str]:
    return sorted({ch for ch in text if ch in SIMPLIFIED})


# ── B159 ────────────────────────────────────────────────


def test_module_doc_names_the_guide_it_loads():
    doc = ai_generator.__doc__ or ""
    assert "strategy-guide-compact.md" in doc
    assert "prompts/strategy-guide.md →" not in doc


# ── B162 ────────────────────────────────────────────────


def test_desktop_build_bundles_the_prompt_guides():
    spec = (ROOT / "packaging" / "tickflow.spec").read_text(encoding="utf-8")
    assert '"strategy" / "prompts"' in spec
    assert '(STRATEGY_PROMPTS, "app/strategy/prompts")' in spec


def test_a_missing_guide_is_logged(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(ai_generator, "GUIDE_PATH", tmp_path / "absent.md")
    with caplog.at_level(logging.WARNING, logger="app.strategy.ai_generator"):
        assert AIStrategyGenerator()._get_guide() == ""
    assert "strategy guide not found" in caplog.text


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


@pytest.mark.parametrize(
    "prompt",
    [_SYSTEM_PREFIX, COMPACT, STEP2, None],
    ids=["strategy_prefix", "compact_guide", "step2", "signal_prompt"],
)
def test_prompts_are_taiwan_and_traditional(prompt):
    text = _signal_prompt() if prompt is None else prompt
    assert "A股" not in text and "A 股" not in text
    assert "台股" in text
    assert "繁體中文" in text
    assert _simplified(text) == []


def test_signal_prompt_states_taiwan_volume_and_amount_units():
    text = _signal_prompt()
    assert "1 張 = 1000 股" in text
    assert "amount" in text and "以元計" in text


# ── B174 / B175 ─────────────────────────────────────────


def test_compact_guide_states_decimal_and_percent_units():
    assert "0.03 = 3%" in COMPACT
    for name in ("change_pct", "amplitude", "momentum_5d"):
        line = next(line for line in COMPACT.splitlines() if name in line and "小數" in line)
        assert line
    assert "`turnover_rate`" in COMPACT and "百分數" in COMPACT and "5 = 5%" in COMPACT


def test_compact_guide_states_taiwan_conventions():
    assert "2330.TWSE" in COMPACT and "6488.TPEX" in COMPACT
    assert "basic_filter.boards" in COMPACT
    unavailable = COMPACT.split("## 不可直接引用的資料", 1)[1]
    for item in ("三大法人", "融資融券", "借券"):
        assert item in unavailable
    assert "人氣排名" not in COMPACT and "资金流向" not in COMPACT


def test_compact_guide_signal_names_match_the_labels():
    assert "跌停翻紅" in COMPACT and "漲停打開" in COMPACT
    assert "炸板" not in COMPACT and "翘板" not in COMPACT


def test_compact_guide_stays_short():
    assert len(COMPACT) < 5000
