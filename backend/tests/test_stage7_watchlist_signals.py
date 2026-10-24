"""Stage 7: Taiwan watchlist rows carry the published signals; deleted-group rule warning."""
from __future__ import annotations

import logging
from datetime import date

from tests.test_taiwan_watchlist_enrichment import _enrich_one, _enriched_row, _publish

SIGNALS = {
    "signal_limit_up": True,
    "signal_limit_down": False,
    "signal_volume_surge": True,
    "signal_ma_golden_5_20": False,
    "signal_macd_golden": True,
    "signal_n_day_high": False,
    "signal_boll_breakout_upper": False,
    "signal_ma20_breakout": True,
    "signal_ma_dead_5_20": False,
    "signal_macd_dead": False,
    "signal_n_day_low": False,
    "signal_boll_breakdown_lower": False,
    "signal_ma20_breakdown": False,
}


def _published_with_signals(symbol: str) -> dict:
    return {**_enriched_row(symbol, 10.0), **SIGNALS, "consecutive_limit_ups": 2, "consecutive_limit_downs": 0}


def test_taiwan_row_carries_the_published_signals_and_limit_streaks(tmp_path, monkeypatch):
    _publish(tmp_path, monkeypatch, {date(2026, 9, 30): [_published_with_signals("2330.TWSE")]})
    row = _enrich_one("2330.TWSE", "TSMC")
    for field, value in SIGNALS.items():
        assert row[field] is value, field
    assert row["consecutive_limit_ups"] == 2
    assert row["consecutive_limit_downs"] == 0


def test_signals_come_from_the_partition_that_dates_the_indicators(tmp_path, monkeypatch):
    _publish(tmp_path, monkeypatch, {
        date(2026, 9, 29): [_published_with_signals("2330.TWSE")],
        date(2026, 9, 30): [{"symbol": "2330.TWSE", "close": 1100.0, "volume": 5.0}],
    })
    row = _enrich_one("2330.TWSE", "TSMC")
    assert row["indicator_date"] == "2026-09-29"
    assert row["signal_macd_golden"] is True


def test_partition_without_signal_columns_keeps_the_indicators(tmp_path, monkeypatch):
    published = _enriched_row("2330.TWSE", 10.0)
    _publish(tmp_path, monkeypatch, {date(2026, 9, 30): [published]})
    row = _enrich_one("2330.TWSE", "TSMC")
    assert row["rsi_14"] == published["rsi_14"]
    assert row["signal_macd_golden"] is None
    assert row["consecutive_limit_ups"] is None


def test_deleted_group_warning_does_not_promise_recovery(monkeypatch, caplog):
    from app.strategy import monitor

    monkeypatch.setattr(monitor, "_watchlist_groups_snapshot", lambda: {})
    monkeypatch.setattr(monitor, "_warned_missing_groups", set())
    with caplog.at_level(logging.WARNING, logger=monitor.logger.name):
        assert monitor._group_members_or_none({"id": "r1", "group_id": "gone"}) is None
    message = caplog.records[-1].getMessage()
    assert "自动生效" not in message
    assert "r1" in message and "gone" in message


def test_indicators_and_signals_share_one_partition_read(tmp_path, monkeypatch):
    import polars as pl

    from app.taiwan import enriched_reader

    _publish(tmp_path, monkeypatch, {date(2026, 9, 30): [_published_with_signals("2330.TWSE")]})
    reads = []
    real_read = pl.read_parquet

    def counting_read(source, *args, **kwargs):
        reads.append(str(source))
        return real_read(source, *args, **kwargs)

    monkeypatch.setattr(enriched_reader.pl, "read_parquet", counting_read)
    row = _enrich_one("2330.TWSE", "TSMC")
    assert row["signal_macd_golden"] is True
    assert len([r for r in reads if "kline_daily_enriched" in r]) == 1
