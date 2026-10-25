"""/api/kline/minute-range must carry unattributed_volume for every past session, read from real partitions."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.api import kline as kline_api
from app.repository import DataStore, KlineRepository
from tests.test_stage3_minute_gaps import DAY, SYMBOL, _record_gap_day, _write_old_partition

pytestmark = pytest.mark.usefixtures("deny_network")


def test_minute_range_serves_unattributed_volume_of_a_past_session(tmp_path, monkeypatch):
    _record_gap_day(tmp_path)
    later = DAY + timedelta(days=1)
    _write_old_partition(tmp_path, "kline_minute", later)
    repo = KlineRepository(DataStore(tmp_path))
    monkeypatch.setattr(kline_api, "cn_today", lambda: later + timedelta(days=3))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo)))

    result = kline_api.get_minute_range(request, symbol=SYMBOL, days=5)

    sessions = {session["date"]: session for session in result["sessions"]}
    gap_day_rows = sessions[DAY.isoformat()]["rows"]
    assert [row.get("unattributed_volume") for row in gap_day_rows] == [1_000.0, 3_000.0]
    assert [row.get("unattributed_volume") for row in sessions[later.isoformat()]["rows"]] == [None]
