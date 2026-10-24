"""Watchlist turnover labels: not applicable for ETFs and TDRs, a board-lot estimate before the official row.

turnover_rate_status is "not_applicable" when the instrument has no turnover denominator,
"board_lot_estimate" while the numerator is MIS board-lot volume (any MIS quote, live or not,
until the official daily row of its trade date is stored), and None otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from tests.test_stage3_volume_convention import (
    ISSUED_SHARES,
    MIS_VOLUME,
    OFFICIAL_AMOUNT,
    OFFICIAL_VOLUME,
    SYMBOL,
    TAIPEI,
    TRADE_DATE,
    _Daily,
    _Meta,
    _Quote,
    _Realtime,
    _ShareCapital,
)


@pytest.fixture
def isolated(deny_network, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)


@dataclass
class _Instrument:
    name: str
    instrument_type: str


class _Master:
    def __init__(self, instrument_type: str) -> None:
        self._type = instrument_type

    def get_instrument(self, symbol):
        return _Instrument(name="x", instrument_type=self._type)


def _row(monkeypatch, quote, *, instrument_type="stock", stored_through=TRADE_DATE - timedelta(days=1), hour=10):
    from app.taiwan.realtime import calendar
    from app.taiwan.watchlist_enrichment import enrich_taiwan_watchlist_rows

    monkeypatch.setattr(calendar, "taipei_now", lambda: datetime(2026, 10, 7, hour, 0, tzinfo=TAIPEI))
    (row,) = enrich_taiwan_watchlist_rows(
        [SYMBOL],
        security_master=_Master(instrument_type),
        realtime_service=_Realtime(quote),
        daily_store=_Daily(stored_through),
        share_capital=_ShareCapital(),
    )
    return row


def _mis(**meta) -> _Quote:
    return _Quote(volume=MIS_VOLUME, amount=None, trade_date=TRADE_DATE, source_meta=_Meta(**meta))


def test_etf_turnover_is_marked_not_applicable(isolated, monkeypatch):
    row = _row(monkeypatch, _mis(), instrument_type="etf")
    assert row["turnover_rate"] is None
    assert row["turnover_rate_status"] == "not_applicable"


def test_etf_without_any_quote_is_still_not_applicable(isolated, monkeypatch):
    row = _row(monkeypatch, None, instrument_type="etf")
    assert row["turnover_rate_status"] == "not_applicable"


def test_intraday_mis_turnover_is_a_board_lot_estimate(isolated, monkeypatch):
    row = _row(monkeypatch, _mis())
    assert row["turnover_rate"] == pytest.approx(MIS_VOLUME * 100.0 / ISSUED_SHARES)
    assert row["turnover_rate_status"] == "board_lot_estimate"


def test_a_stale_mis_quote_after_the_close_is_still_board_lot(isolated, monkeypatch):
    row = _row(monkeypatch, _mis(is_realtime=False), hour=15)
    assert row["turnover_rate"] == pytest.approx(MIS_VOLUME * 100.0 / ISSUED_SHARES)
    assert row["board_lot_volume"] is True
    assert row["turnover_rate_status"] == "board_lot_estimate"


def test_the_official_row_clears_the_estimate(isolated, monkeypatch):
    row = _row(monkeypatch, _mis(), stored_through=TRADE_DATE, hour=20)
    assert row["turnover_rate"] == pytest.approx(OFFICIAL_VOLUME * 100.0 / ISSUED_SHARES)
    assert row["turnover_rate_status"] is None


def test_an_official_snapshot_is_not_an_estimate(isolated, monkeypatch):
    quote = _Quote(
        volume=OFFICIAL_VOLUME, amount=OFFICIAL_AMOUNT, trade_date=TRADE_DATE,
        source_meta=_Meta(is_realtime=False, source="twse:official"),
    )
    row = _row(monkeypatch, quote, hour=14)
    assert row["board_lot_volume"] is False
    assert row["turnover_rate_status"] is None


def test_watchlist_volume_ratio_is_the_published_official_value(isolated, monkeypatch):
    """vol_ratio_5d is read from the published partition only, never from the MIS volume."""
    from app.taiwan import watchlist_enrichment

    monkeypatch.setattr(
        watchlist_enrichment, "_taiwan_indicator_map",
        lambda symbols: ({SYMBOL: {"symbol": SYMBOL, "vol_ratio_5d": 1.25}}, "2026-10-06"),
    )
    row = _row(monkeypatch, _mis())
    assert row["vol_ratio_5d"] == 1.25
    assert row["indicator_date"] == "2026-10-06"
