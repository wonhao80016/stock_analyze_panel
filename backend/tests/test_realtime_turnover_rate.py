from __future__ import annotations

import pytest

from app.services.quote_service import QuoteService


def test_quote_extra_normalizes_realtime_turnover_fraction_to_percent_value():
    out = QuoteService._build_quote_extra([{"symbol": "2330.TWSE", "turnover_rate": 0.008}])

    assert out["turnover_rate"][0] == pytest.approx(0.8)
