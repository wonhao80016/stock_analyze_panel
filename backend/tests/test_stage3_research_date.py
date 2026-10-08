"""The research card prices the latest stored session when the requested date has no bars yet.

From 16:00 the default target is today, but the evening update stores today's bars later, and
a holiday has no bars at all. The price row then comes from the latest stored session on or
before the target, its previous close from the session before that row, and trade_date names
the session the figures belong to.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan.ai_research import build_evidence_registry
from app.taiwan.research_context import TaiwanStockResearchContextService

pytestmark = pytest.mark.usefixtures("deny_network")

SYMBOL = "TEST.TWSE"
DATES = [
    date(2026, 10, 1),
    date(2026, 10, 2),
    date(2026, 10, 5),
    date(2026, 10, 6),
    date(2026, 10, 7),
]
CLOSES = [100.0, 101.0, 102.0, 104.0, 107.0]


def _service() -> TaiwanStockResearchContextService:
    daily = MagicMock()
    daily.available_dates.return_value = list(DATES)

    def read_range(symbols, start, end):
        return pl.DataFrame(
            {
                "symbol": [SYMBOL] * len(DATES),
                "date": DATES,
                "open": CLOSES,
                "high": [c + 1.0 for c in CLOSES],
                "low": [c - 1.0 for c in CLOSES],
                "close": CLOSES,
                "volume": [10_000.0 + i for i in range(len(DATES))],
                "amount": [1_000_000.0] * len(DATES),
            }
        ).filter((pl.col("date") >= start) & (pl.col("date") <= end))

    daily.read_range.side_effect = read_range
    inst = MagicMock()
    inst.symbol = SYMBOL
    inst.code = "TEST"
    inst.name = "Test"
    inst.exchange = "TWSE"
    inst.instrument_type = "stock"
    inst.industry = "Semiconductors"
    inst.currency = "TWD"
    inst.listing_status = "active"
    inst.listing_date = "2020-01-01"
    inst.updated_at = "2026-10-07"
    master = MagicMock()
    master.get_instrument.return_value = inst
    master.to_dataframe.return_value = pl.DataFrame(
        {
            "symbol": [SYMBOL],
            "name": ["Test"],
            "exchange": ["TWSE"],
            "instrument_type": ["stock"],
            "industry": ["Semiconductors"],
            "listing_status": ["active"],
        }
    )
    return TaiwanStockResearchContextService(daily_store=daily, security_master=master)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


def _publish_indicators(root, day: date, **values) -> None:
    from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService

    TaiwanEnrichedRefreshService._write_partition(
        root / "kline_daily_enriched" / f"date={day.isoformat()}",
        pl.DataFrame([{"symbol": SYMBOL, **values}]),
    )


@pytest.mark.parametrize(
    "target",
    [
        pytest.param(date(2026, 10, 8), id="after-16:00-before-the-update"),
        pytest.param(date(2026, 10, 9), id="holiday"),
    ],
)
def test_a_target_without_bars_prices_the_latest_stored_session(target):
    ctx = _service().get_research_context(SYMBOL, target)
    pc = ctx.price_context

    assert pc.trade_date == "2026-10-07"
    assert pc.close == 107.0
    assert pc.previous_close == 104.0
    assert pc.change == 3.0
    assert pc.change_pct == round(107.0 / 104.0 - 1.0, 6)
    assert pc.return_5d is None
    assert pc.meta.classification == "KNOWN"
    assert pc.meta.as_of == "2026-10-07"
    assert ctx.as_of_date == target.isoformat()
    assert ctx.data_quality.target_trade_date == target.isoformat()


def test_the_technical_context_reads_the_same_session(_data_dir):
    _publish_indicators(
        _data_dir,
        date(2026, 10, 7),
        ma5=103.0,
        ma20=101.0,
        ma60=99.0,
        vol_ratio_5d=1.2,
        rsi_14=60.0,
    )
    tech = _service().get_research_context(SYMBOL, date(2026, 10, 8)).technical_context

    assert tech.ma20 == 101.0
    assert tech.distance_to_ma20 == round(107.0 / 101.0 - 1.0, 6)
    assert tech.meta.as_of == "2026-10-07"


def test_a_stored_target_is_unchanged():
    pc = _service().get_research_context(SYMBOL, date(2026, 10, 6)).price_context

    assert pc.trade_date == "2026-10-06"
    assert pc.close == 104.0
    assert pc.previous_close == 102.0


def test_the_ai_gating_still_follows_the_target():
    ctx = _service().get_research_context(SYMBOL, date(2026, 10, 8))
    daily = next(s for s in ctx.data_quality.sections if s.section == "daily")

    assert daily.status == "stale"
    assert daily.as_of == "2026-10-07"
    assert ctx.data_quality.overall_status == "partial"
    payload, _keys, missing = build_evidence_registry(ctx, None)
    assert payload["price_context"]["trade_date"] == "2026-10-07"
    assert payload["price_context"]["close"] == 107.0
    assert "institutional_context" in missing
