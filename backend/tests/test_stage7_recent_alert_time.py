"""Detail page alert times carry the Taipei offset instead of the server's local time (B314)."""
from __future__ import annotations

from datetime import datetime, timezone

from app.taiwan.detail_service import TaiwanStockDetailService


def test_triggered_at_is_an_aware_taipei_time():
    ts = int(datetime(2026, 10, 21, 18, 30, tzinfo=timezone.utc).timestamp() * 1000)

    alert = TaiwanStockDetailService._recent_alert({"ts": ts, "rule_id": "r1", "message": "m"})

    assert alert.triggered_at == "2026-10-22T02:30:00+08:00"


def test_missing_timestamp_stays_empty():
    assert TaiwanStockDetailService._recent_alert({"rule_id": "r1"}).triggered_at == ""
