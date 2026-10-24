"""Stage 5D: AI research evidence, diagnostics status and output guards.

Covers B043 (unevaluated diagnostics are not sent as "no signals"), B180 (market and
industry context during the margin-publication gap), B181 (one snapshot computation per
request) and B186 (ranking and recommendation language guards).
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_taiwan_stock_comparison import _make_stub_service, build_context

from app.taiwan.abnormal_diagnostics import (
    CompactIndustryContext,
    CompactMarketContext,
    DiagnosticsDataQuality,
    DiagnosticSignalEvidence,
    TaiwanAbnormalDiagnosticItem,
    TaiwanAbnormalDiagnosticsSnapshot,
)
from app.taiwan.ai_research import (
    SYSTEM_PROMPT,
    TaiwanAIResearchService,
    build_evidence_registry,
    detect_forbidden_language,
)
from app.taiwan.comparison_ai_research import (
    SYSTEM_PROMPT_COMPARISON,
    TaiwanComparisonAIResearchService,
)

AS_OF = date(2026, 9, 30)
PRICED = date(2026, 9, 29)
SYMBOL = "2330.TWSE"


def _item(symbol: str, signals: list[DiagnosticSignalEvidence]) -> TaiwanAbnormalDiagnosticItem:
    return TaiwanAbnormalDiagnosticItem(
        symbol=symbol,
        code=symbol.split(".")[0],
        name=symbol,
        exchange="TWSE",
        signal_count=len(signals),
        signals=signals,
        market_context=CompactMarketContext(trade_date=str(PRICED)),
        industry_context=CompactIndustryContext(),
    )


def _volume_spike() -> DiagnosticSignalEvidence:
    return DiagnosticSignalEvidence(
        type="VOLUME_SPIKE", observed=300.0, baseline=100.0, ratio=3.0, threshold=2.0,
        formula="f", lookback_sessions=5, valid_sessions=5, source="taiwan_daily_store",
    )


def _snapshot(day: date, items: list[TaiwanAbnormalDiagnosticItem], status: str) -> TaiwanAbnormalDiagnosticsSnapshot:
    return TaiwanAbnormalDiagnosticsSnapshot(
        trade_date=str(day),
        generated_at="2026-09-30T17:00:00+08:00",
        universe_count=len(items),
        diagnostic_count=len(items),
        items=items,
        data_quality=DiagnosticsDataQuality(
            target_trade_date=str(day),
            universe_supported_count=len(items),
            evaluated_symbol_count=len(items),
            diagnostic_symbol_count=len(items),
            daily_status="current" if items else "unavailable",
            institutional_status="current",
            margin_status="current",
            overall_status=status,
        ),
        provenance=[],
    )


def _ctx_priced_a_session_earlier(symbol: str = SYMBOL, code: str = "2330"):
    ctx = build_context(symbol, code, symbol, trade_date=PRICED)
    return ctx.model_copy(update={"as_of_date": str(AS_OF)})


def _diag_by_date(diag_svc: MagicMock, symbols: list[str]) -> None:
    def get_diagnostics(target_date=None, **kwargs):
        if target_date == PRICED:
            return _snapshot(PRICED, [_item(s, [_volume_spike()]) for s in symbols], "complete")
        return _snapshot(target_date, [], "unavailable")

    diag_svc.get_diagnostics.side_effect = get_diagnostics


_VALID_REPORT = {
    "overview": "客觀摘要。",
    "key_observations": [],
    "risk_factors": [],
    "missing_information": [],
}


# ── B043 ──────────────────────────────────────────────────────


def test_missing_diagnostic_item_is_reported_as_not_evaluated():
    ctx = build_context(SYMBOL, "2330", SYMBOL)
    payload, keys, missing = build_evidence_registry(ctx, None)

    assert payload["abnormal_diagnostics_status"] == "not_evaluated"
    assert payload.get("abnormal_signals") is None
    assert "abnormal_diagnostics" in missing
    assert "abnormal.signal_count" not in keys


def test_evaluated_item_without_signals_reports_a_zero_signal_count():
    ctx = build_context(SYMBOL, "2330", SYMBOL)
    payload, keys, missing = build_evidence_registry(ctx, _item(SYMBOL, []), diag_status="complete")

    assert payload["abnormal_diagnostics_status"] == "complete"
    assert payload["abnormal_signal_count"] == 0
    assert payload["abnormal_signals"] == []
    assert "abnormal.signal_count" in keys
    assert "abnormal_diagnostics" not in missing


def test_partial_diagnostics_are_flagged_as_incomplete():
    ctx = build_context(SYMBOL, "2330", SYMBOL)
    payload, _, missing = build_evidence_registry(ctx, _item(SYMBOL, []), diag_status="partial")

    assert payload["abnormal_diagnostics_status"] == "partial"
    assert "abnormal_diagnostics" in missing


@pytest.mark.asyncio
async def test_single_stock_research_diagnoses_the_session_its_prices_belong_to():
    research = MagicMock()
    research.get_research_context.return_value = _ctx_priced_a_session_earlier()
    diag = MagicMock()
    _diag_by_date(diag, [SYMBOL])
    svc = TaiwanAIResearchService(research_svc=research, diag_svc=diag)

    with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(_VALID_REPORT)
        resp = await svc.generate_report(SYMBOL, target_date=AS_OF)

    assert resp.status == "success"
    assert diag.get_diagnostics.call_args.kwargs["target_date"] == PRICED
    assert "abnormal.volume_spike" in resp.evidence_registry_keys
    assert "abnormal_diagnostics" not in resp.report.missing_information


def test_comparison_diagnoses_the_session_its_prices_belong_to():
    svc = _make_stub_service({
        "2330.TWSE": _ctx_priced_a_session_earlier("2330.TWSE", "2330"),
        "2881.TWSE": _ctx_priced_a_session_earlier("2881.TWSE", "2881"),
    })
    _diag_by_date(svc.diag_svc, ["2330.TWSE", "2881.TWSE"])

    resp = svc.compare(["2330.TWSE", "2881.TWSE"], target_date=AS_OF)

    assert svc.diag_svc.get_diagnostics.call_args.kwargs["target_date"] == PRICED
    assert all(i.diagnostic_item is not None for i in resp.instruments)


def test_prompts_forbid_reading_unevaluated_diagnostics_as_no_signals():
    for prompt in (SYSTEM_PROMPT, SYSTEM_PROMPT_COMPARISON):
        assert "not_evaluated" in prompt


# ── B180 ──────────────────────────────────────────────────────


def test_partial_market_and_industry_context_are_sent_with_their_status():
    ctx = build_context(SYMBOL, "2330", SYMBOL)
    ctx = ctx.model_copy(update={
        "market_context": ctx.market_context.model_copy(update={"status": "partial"}),
        "industry_context": ctx.industry_context.model_copy(update={
            "status": "partial", "industry": "半導體業", "relative_strength_5d": 0.01,
        }),
    })
    payload, keys, missing = build_evidence_registry(ctx, None)

    assert payload["market_context"]["status"] == "partial"
    assert payload["market_context"]["advance_count"] == 500
    assert "market_context.status" in keys
    assert payload["industry_context"]["status"] == "partial"
    assert "market_context" not in missing
    assert "industry_context" not in missing
    for prompt in (SYSTEM_PROMPT, SYSTEM_PROMPT_COMPARISON):
        assert "market_context.status" in prompt


def test_unavailable_market_context_is_still_reported_missing():
    ctx = build_context(SYMBOL, "2330", SYMBOL)
    ctx = ctx.model_copy(update={
        "market_context": ctx.market_context.model_copy(update={"status": "unavailable"}),
    })
    payload, _, missing = build_evidence_registry(ctx, None)

    assert "market_context" not in payload
    assert "market_context" in missing


# ── B181 ──────────────────────────────────────────────────────


def test_single_stock_research_shares_one_snapshot_cache_between_context_and_diagnostics():
    svc = TaiwanAIResearchService()

    assert svc.research_svc.market_intel_svc is svc.diag_svc.market_intel_svc
    assert svc.research_svc.industry_intel_svc is svc.diag_svc.industry_intel_svc


def test_comparison_shares_one_snapshot_cache_between_context_and_diagnostics():
    research_svc, diag_svc = TaiwanComparisonAIResearchService().comparison_svc._build_services()

    assert research_svc.market_intel_svc is diag_svc.market_intel_svc
    assert research_svc.industry_intel_svc is diag_svc.industry_intel_svc


# ── B186 ──────────────────────────────────────────────────────


def _comparison_service() -> TaiwanComparisonAIResearchService:
    stub = _make_stub_service({
        "2330.TWSE": build_context("2330.TWSE", "2330", "台積電"),
        "2881.TWSE": build_context("2881.TWSE", "2881", "富邦金"),
    })
    return TaiwanComparisonAIResearchService(comparison_svc=stub)


@pytest.mark.asyncio
async def test_comparison_rejects_ranking_language_in_missing_information():
    report = {"comparison_overview": "客觀比較。", "key_observations": [], "risk_factors": [],
              "missing_information": ["2330.TWSE 為首選標的"]}
    with patch("app.taiwan.comparison_ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(report, ensure_ascii=False)
        resp = await _comparison_service().generate_comparison(["2330.TWSE", "2881.TWSE"])

    assert resp.status == "unavailable"
    assert resp.error_code == "invalid_output"


@pytest.mark.asyncio
async def test_comparison_rejects_recommendation_and_target_price_language():
    report = {"comparison_overview": "2330.TWSE 目標價 1200 元。", "key_observations": [],
              "risk_factors": [], "missing_information": []}
    with patch("app.taiwan.comparison_ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(report, ensure_ascii=False)
        resp = await _comparison_service().generate_comparison(["2330.TWSE", "2881.TWSE"])

    assert resp.error_code == "invalid_output"


def _single_service() -> TaiwanAIResearchService:
    research = MagicMock()
    research.get_research_context.return_value = build_context(SYMBOL, "2330", "台積電")
    diag = MagicMock()
    diag.get_diagnostics.return_value = _snapshot(date(2026, 8, 28), [_item(SYMBOL, [])], "complete")
    return TaiwanAIResearchService(research_svc=research, diag_svc=diag)


@pytest.mark.asyncio
@pytest.mark.parametrize("field, text", [
    ("overview", "數據顯示台積電建議買進。"),
    ("overview", "合理目標價為 1,200 元。"),
    ("abnormal_diagnostics_interpretation", "此標的為首選。"),
    ("missing_information", "最值得投資的標的"),
])
async def test_single_stock_report_rejects_recommendation_language(field, text):
    report = dict(_VALID_REPORT)
    report[field] = [text] if field == "missing_information" else text
    with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(report, ensure_ascii=False)
        resp = await _single_service().generate_report(SYMBOL)

    assert resp.status == "unavailable"
    assert resp.error_code == "invalid_output"


@pytest.mark.asyncio
async def test_single_stock_report_allows_stating_that_no_target_price_is_given():
    report = dict(_VALID_REPORT)
    report["overview"] = "本報告不提供目標價或買賣建議, 近 5 日報酬為正。"
    with patch("app.taiwan.ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps(report, ensure_ascii=False)
        resp = await _single_service().generate_report(SYMBOL)

    assert resp.status == "success"


# ── Review follow-ups: comparison diagnostics status, guard false positives ──


@pytest.mark.asyncio
async def test_comparison_flags_partial_diagnostics_as_incomplete():
    svc = _make_stub_service({
        "2330.TWSE": build_context("2330.TWSE", "2330", "台積電"),
        "2881.TWSE": build_context("2881.TWSE", "2881", "富邦金"),
    })
    svc.diag_svc.get_diagnostics.return_value = _snapshot(
        date(2026, 8, 28), [_item("2330.TWSE", []), _item("2881.TWSE", [])], "partial",
    )
    with patch("app.taiwan.comparison_ai_research.generate_ai_text", new_callable=AsyncMock) as mock_ai:
        mock_ai.return_value = json.dumps({"comparison_overview": "客觀比較。", "key_observations": [],
                                           "risk_factors": [], "missing_information": []})
        resp = await TaiwanComparisonAIResearchService(comparison_svc=svc).generate_comparison(
            ["2330.TWSE", "2881.TWSE"]
        )

    assert resp.status == "success"
    assert "2330.TWSE.abnormal_diagnostics" in resp.report.missing_information
    prompt = mock_ai.call_args.args[0][1]["content"]
    assert '"abnormal_diagnostics_status": "partial"' in prompt


@pytest.mark.parametrize("text", [
    "目標價一律不予提供",
    "不建議買進或賣出",
    "並非建議持有",
    "not a buy rating",
    "合理價位在 20 日區間",
    "本報告不構成建議買進之依據",
])
def test_negated_or_non_price_phrases_are_not_flagged(text):
    assert detect_forbidden_language(text) is None


@pytest.mark.parametrize("text", [
    "數據顯示台積電建議買進。",
    "合理目標價為 1,200 元。",
    "2330.TWSE 目標價 1200 元。",
    "目標價為 1200。",
    "此標的為首選。",
    "最值得投資的標的",
    "analysts keep a buy rating",
    "a target price of $50.",
    "目標價 1200 附近",
    "合理價 95~100 元",
    "合理價約 95 至 100 元",
    "target price of $120 by year end",
    "合理價 3 倍本益比",
    "目標價 15 倍本益比推算為 900 元",
    "目標價 20 日內上看 150 元",
])
def test_recommendation_phrases_are_still_flagged(text):
    assert detect_forbidden_language(text) is not None


def test_market_and_industry_context_carry_their_trade_date():
    ctx = build_context(SYMBOL, "2330", SYMBOL)
    ctx = ctx.model_copy(update={
        "industry_context": ctx.industry_context.model_copy(update={
            "status": "complete", "industry": "半導體業",
            "meta": ctx.industry_context.meta.model_copy(update={"as_of": "2026-08-28"}),
        }),
    })
    payload, keys, _ = build_evidence_registry(ctx, None)

    assert payload["market_context"]["trade_date"] == "2026-08-28"
    assert payload["industry_context"]["trade_date"] == "2026-08-28"
    assert {"market_context.trade_date", "industry_context.trade_date"} <= keys
