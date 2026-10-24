"""list_recent tolerates legacy records whose ts is not a number."""
from __future__ import annotations

import json
import logging
import time

from app.services import alert_store


def test_list_recent_skips_records_with_a_non_numeric_ts(tmp_path, caplog):
    path = tmp_path / "user_data" / "alerts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = int(time.time() * 1000)
    lines = [
        {"id": "a", "ts": now, "rule_id": "r", "type": "price"},
        {"id": "b", "ts": "2026-10-24T09:00:00", "rule_id": "r", "type": "price"},
        {"id": "c", "ts": None, "rule_id": "r", "type": "price"},
        {"id": "d", "ts": now - 1000, "rule_id": "r", "type": "price"},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="app.services.alert_store"):
        events = alert_store.list_recent(tmp_path, days=1)

    assert [e["id"] for e in events] == ["a", "d"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
