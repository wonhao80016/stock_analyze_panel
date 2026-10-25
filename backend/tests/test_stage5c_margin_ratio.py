"""Short-margin ratio on the detail page's live margin path (B045, B050, B194)."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from app.taiwan import detail_service
from app.taiwan.detail_models import SectionMeta, TaiwanInstitutionalData
from app.taiwan.detail_service import TaiwanStockDetailService
from app.taiwan.enrichment import factors as chip_factors
from app.taiwan.enrichment.models import MarginTrading, SourceMeta
from app.taiwan.symbol import parse_symbol

DAY = date(2026, 10, 22)


def _margin(code: str, margin_balance: int, short_balance: int) -> MarginTrading:
    return MarginTrading(
        symbol=f"{code}.TWSE", trade_date=DAY, unit="shares", source_unit="lots", lot_multiplier=1000,
        margin_previous_balance=margin_balance, margin_buy=0, margin_sell=0, margin_cash_redemption=0,
        margin_balance=margin_balance, margin_change=0,
        short_previous_balance=short_balance, short_sell=0, short_cover=0, short_stock_redemption=0,
        short_balance=short_balance, short_change=0, short_margin_ratio=None,
        meta=SourceMeta(
            source="twse:mi_margn", source_url="https://example.invalid/mi_margn",
            fetched_at=datetime(2026, 10, 22, 21, 50), trade_date=DAY, status="official",
        ),
    )


class _LiveMargin:
    def __init__(self, rows: list[MarginTrading]) -> None:
        self.rows = rows

    def fetch_live_day(self, exchange, trade_date, target_code=None, timeout=10.0):
        if trade_date != DAY:
            return []
        return [r for r in self.rows if target_code is None or r.symbol.split(".")[0] == target_code]


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr(detail_service, "taipei_now", lambda: datetime(2026, 10, 22, 22, 0))
    return TaiwanStockDetailService()


def test_live_margin_reports_short_balance_over_margin_balance_in_percent(svc, monkeypatch):
    monkeypatch.setattr(svc, "margin_provider", _LiveMargin([_margin("2454", 10_300_000, 73_000)]))

    out = svc._aggregate_margin(parse_symbol("2454.TWSE"))

    assert out.status == "available"
    assert out.meta.source == "twse:mi_margn"
    assert out.short_margin_ratio == 0.71


def test_live_margin_factor_carries_the_same_ratio(svc, monkeypatch):
    monkeypatch.setattr(svc, "margin_provider", _LiveMargin([_margin("2454", 10_300_000, 73_000)]))
    margin = svc._aggregate_margin(parse_symbol("2454.TWSE"))
    inst = TaiwanInstitutionalData(status="unavailable", meta=SectionMeta(source="x", status="unavailable"))

    factor_data = svc._aggregate_factors(inst, margin)

    assert factor_data.short_margin_ratio == 0.71


def test_live_margin_ratio_is_unknown_without_margin_balance(svc, monkeypatch):
    monkeypatch.setattr(svc, "margin_provider", _LiveMargin([_margin("2454", 0, 5_000)]))

    out = svc._aggregate_margin(parse_symbol("2454.TWSE"))

    assert out.status == "available"
    assert out.short_margin_ratio is None


@pytest.mark.parametrize(
    ("short_balance", "margin_balance", "expected"),
    [(73_000, 10_300_000, 0.71), (5_000, 0, None), (0, 0, None), (5_000, None, None), (None, 1_000, None)],
)
def test_short_margin_ratio_helper(short_balance, margin_balance, expected):
    assert chip_factors.short_margin_ratio(short_balance, margin_balance) == expected
