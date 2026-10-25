"""Two silent corruptions on the way from a quote to today's bar.

Both write something that looks plausible: a timestamp 1000x too large, and
yesterday's close dated today. Neither raises, so only an assertion catches them.
"""
from __future__ import annotations

from datetime import datetime, timezone

import polars as pl

from app.services.quote_service import QuoteService

DT = datetime(2026, 9, 22, 13, 30, tzinfo=timezone.utc)
MS = int(DT.timestamp() * 1000)


def _row(**kw) -> dict:
    return {"symbol": "2330.TWSE", "close": 1000.0, "volume": 1.0, **kw}


# ── B2: quote_ts must be milliseconds ─────────────────────────


def test_the_providers_millisecond_column_is_used_as_is():
    out = QuoteService._build_daily([_row(quote_ts=MS, timestamp=DT)])
    assert out["quote_ts"][0] == MS


def test_a_datetime_is_converted_to_milliseconds_not_microseconds():
    """Casting a polars Datetime to Int64 yields microseconds - the original bug."""
    out = QuoteService._build_daily([_row(timestamp=DT)])
    assert out["quote_ts"][0] == MS
    assert out["quote_ts"][0] != MS * 1000


def test_an_integer_timestamp_is_left_alone():
    out = QuoteService._build_daily([_row(timestamp=MS)])
    assert out["quote_ts"][0] == MS


def test_no_timestamp_at_all_yields_no_column_rather_than_a_wrong_one():
    out = QuoteService._build_daily([_row()])
    assert "quote_ts" not in out.columns


def test_the_millisecond_value_survives_the_session_progress_reader():
    """The unit bug surfaced here: microseconds read as a finished session."""
    from app.taiwan.realtime.calendar import session_progress_from_ts

    out = QuoteService._build_daily([_row(timestamp=DT)])
    progress = session_progress_from_ts(int(out["quote_ts"][0]))
    assert 0.0 <= progress <= 1.0


# ── B3: only a realtime quote may be stamped with today ───────


def test_a_realtime_batch_passes_through_whole():
    records = [{"symbol": "A", "status": "realtime"}, {"symbol": "B", "status": "realtime"}]
    assert QuoteService._records_datable_today(records) == records


def test_a_mixed_batch_keeps_only_the_realtime_rows():
    records = [{"symbol": "A", "status": "realtime"}, {"symbol": "B", "status": "official"}]
    assert [r["symbol"] for r in QuoteService._records_datable_today(records)] == ["A"]


def test_an_entirely_stale_batch_writes_nothing():
    """The official endpoint serves the previous session during trading hours."""
    records = [
        {"symbol": "A", "status": "official"},
        {"symbol": "B", "status": "third_party_fallback"},
        {"symbol": "C", "status": "stale"},
    ]
    assert QuoteService._records_datable_today(records) == []


def test_a_provider_without_a_status_signal_is_not_gated():
    """The TickFlow SDK reports no status; gating it would write nothing at all."""
    records = [{"symbol": "600000.SH", "close": 10.0}, {"symbol": "000001.SZ", "close": 9.0}]
    assert QuoteService._records_datable_today(records) == records


def test_a_stale_quote_never_reaches_a_daily_bar():
    """End to end: the gate feeds _build_daily, so the bar is simply not built."""
    stale = [_row(symbol="2330.TWSE", status="official", timestamp=DT)]
    assert QuoteService._build_daily(QuoteService._records_datable_today(stale)).is_empty()


def test_the_gate_is_wired_into_every_daily_build():
    """A gate nobody calls stops nothing; the only daily build must use it."""
    import inspect

    src = inspect.getsource(QuoteService._process_full_market_records)
    assert src.count("_records_datable_today(") == 1
    assert "_build_daily(stock_records)" not in src


def test_the_gate_does_not_touch_the_quote_cache():
    """Screens keep showing the fallback price; it just is not today's bar."""
    import inspect

    src = inspect.getsource(QuoteService._process_full_market_records)
    build_index = src.index("_build_index_quotes(index_records)")
    first_gate = src.index("_records_datable_today(")
    assert build_index < first_gate


def test_build_daily_still_stamps_today(monkeypatch):
    """The date stamp itself is unchanged; only which rows reach it."""
    out = QuoteService._build_daily([_row(status="realtime", timestamp=DT)])
    assert out["date"].dtype == pl.Date
    assert out.height == 1
