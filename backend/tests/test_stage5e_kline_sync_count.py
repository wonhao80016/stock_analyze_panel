"""sync_daily_batch must turn *count* into a date window and honour a custom daily source."""
from __future__ import annotations

from datetime import datetime

import polars as pl

from app.services import kline_sync


class _RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_daily(self, symbols, start_time=None, end_time=None, on_chunk_done=None, **_kw):
        self.calls.append({"symbols": symbols, "start_time": start_time, "end_time": end_time})
        return pl.DataFrame({"symbol": symbols, "date": [datetime(2026, 10, 1).date()] * len(symbols)})


def test_taiwan_branch_converts_count_into_a_start_date(monkeypatch):
    provider = _RecordingProvider()
    monkeypatch.setattr("app.services.preferences.get_daily_data_provider", lambda: "taiwan")
    monkeypatch.setattr("app.data_providers.registry.get_provider", lambda _name: provider)

    kline_sync.sync_daily_batch(["2330.TW"], count=150)

    call = provider.calls[0]
    assert call["start_time"] is not None and call["end_time"] is not None
    span_days = (call["end_time"] - call["start_time"]).days
    # 150 trading days need at least 150 * 7 / 5 calendar days.
    assert span_days >= 210


def test_explicit_window_is_passed_through(monkeypatch):
    provider = _RecordingProvider()
    monkeypatch.setattr("app.services.preferences.get_daily_data_provider", lambda: "taiwan")
    monkeypatch.setattr("app.data_providers.registry.get_provider", lambda _name: provider)
    start, end = datetime(2026, 1, 5), datetime(2026, 3, 5)

    kline_sync.sync_daily_batch(["2330.TW"], count=150, start_time=start, end_time=end)

    assert provider.calls[0]["start_time"] == start
    assert provider.calls[0]["end_time"] == end


def test_stored_custom_daily_source_still_fetches_from_the_official_provider(monkeypatch):
    provider = _RecordingProvider()
    monkeypatch.setattr("app.services.preferences.load", lambda: {"daily_data_provider": "my_source"})
    monkeypatch.setattr("app.data_providers.custom.provider_has_dataset", lambda name, ds: True)

    def _no_custom(_name):
        raise AssertionError("Taiwan daily K must not be fetched from a custom source")

    monkeypatch.setattr("app.data_providers.custom.get_provider", _no_custom)
    monkeypatch.setattr("app.data_providers.registry.get_provider", lambda name: provider if name == "taiwan" else None)

    df = kline_sync.sync_daily_batch(["2330.TW"], count=60)

    assert not df.is_empty()
    call = provider.calls[0]
    assert call["start_time"] is not None
    assert (call["end_time"] - call["start_time"]).days >= 84
