"""Screener request validation: whitelisted fields with strict types and ranges.

The run endpoint answers 422 for an out-of-range or mistyped field before reading any store;
the natural-language translator turns an LLM reply of the wrong shape or type into a
clarification instead of an exception.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.taiwan.screener import TaiwanScreenerRequest
from app.taiwan.screener_nl import TaiwanScreenerTranslator


@pytest.fixture
def client():
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.mark.parametrize(
    "body",
    [
        {"change_pct_min": 5},
        {"change_pct_max": -1.5},
        {"rsi_14_max": 150},
        {"rsi_14_min": -1},
        {"price_min": -10},
        {"volume_min": -1},
        {"vol_ratio_5d_min": -0.5},
        {"short_margin_ratio_max": -2},
        {"distance_to_upper_limit_max": -0.1},
        {"signals": ["DROP TABLE"]},
        {"signals": "signal_limit_up"},
        {"above_ma5": "maybe"},
    ],
)
def test_run_rejects_invalid_fields_with_422(client, body):
    with patch("app.taiwan.screener.TaiwanScreenerService.run") as run:
        resp = client.post("/api/taiwan/screener/run", json=body)
    assert resp.status_code == 422
    run.assert_not_called()


def test_valid_builtin_signal_ids_are_accepted():
    req = TaiwanScreenerRequest(signals=["signal_limit_up", "signal_ma_golden_5_20"], change_pct_min=-0.1)
    assert req.signals == ["signal_limit_up", "signal_ma_golden_5_20"]


def test_range_bounds_are_inclusive():
    req = TaiwanScreenerRequest(change_pct_min=-1.0, change_pct_max=1.0, rsi_14_min=0, rsi_14_max=100)
    assert (req.change_pct_min, req.rsi_14_max) == (-1.0, 100)
    with pytest.raises(ValidationError):
        TaiwanScreenerRequest(change_pct_max=1.01)


async def _translate(reply: dict | list | str):
    text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
    with patch("app.taiwan.screener_nl.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = text
        return await TaiwanScreenerTranslator().translate("任意條件")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        {"request_fields": ["price_min", 10]},
        {"request_fields": "price_min=10"},
        {"request_fields": {"price_min": 10}, "recognized_conditions": "股價 ≥ 10"},
        {"request_fields": {"price_min": 10}, "unsupported_conditions": {"a": 1}},
        {"request_fields": {"price_min": 10}, "recognized_conditions": [1, 2]},
        {"request_fields": {"price_min": 10}, "clarification_message": ["x"]},
    ],
)
async def test_translation_of_the_wrong_shape_asks_for_clarification(reply):
    res = await _translate(reply)
    assert res.request is None
    assert res.clarification_needed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields",
    [
        {"price_min": "100"},
        {"above_ma5": "false"},
        {"above_ma20": 1},
        {"change_pct_min": 5},
        {"rsi_14_max": 120},
        {"signals": ["signal_limit_up; rm"]},
        {"page_size": 10_000},
    ],
)
async def test_mistyped_or_out_of_range_llm_fields_are_rejected(fields):
    res = await _translate({"request_fields": fields, "recognized_conditions": [], "clarification_needed": False})
    assert res.request is None
    assert res.clarification_needed is True
    assert any("檢驗失敗" in s for s in res.unsupported_conditions)


@pytest.mark.asyncio
async def test_well_typed_llm_fields_still_translate():
    res = await _translate({
        "request_fields": {"price_min": 10, "change_pct_min": 0.03, "above_ma5": True, "exchange": "TWSE"},
        "recognized_conditions": ["股價 ≥ 10"],
        "unsupported_conditions": [],
        "clarification_needed": False,
    })
    assert res.request is not None
    assert res.request.price_min == 10
    assert res.request.change_pct_min == 0.03
    assert res.request.above_ma5 is True


def test_translate_route_answers_a_malformed_reply_without_500(client):
    with patch("app.taiwan.screener_nl.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps({"request_fields": ["oops"]})
        resp = client.post("/api/taiwan/screener/translate", json={"query": "外資買超"})
    assert resp.status_code == 200
    assert resp.json()["clarification_needed"] is True
