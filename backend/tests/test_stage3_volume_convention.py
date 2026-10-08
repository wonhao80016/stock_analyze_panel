"""Stage 3 S8: intraday Taiwan volume is MIS board-lot volume; the official row replaces it.

MIS reports only board-lot trades (no odd-lot, block or fixed-price trades) and no amount. Until
the official daily row of the trade date is stored, volume-derived figures are flagged as
board-lot and amount is null; once it is stored, its volume and amount are used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import polars as pl
import pytest

TAIPEI = timezone(timedelta(hours=8))
TRADE_DATE = date(2026, 10, 7)
SYMBOL = "2330.TWSE"
OFFICIAL_VOLUME = 16_941_183.0
OFFICIAL_AMOUNT = 23_905_400_000.0
MIS_VOLUME = 14_383_000
ISSUED_SHARES = 25_932_070_000


@dataclass
class _Instrument:
    name: str
    instrument_type: str


class _Master:
    def get_instrument(self, symbol):
        return _Instrument(name="台積電", instrument_type="stock")


@dataclass
class _Meta:
    is_realtime: bool = True
    source: str = "twse:mis"

    def to_dict(self) -> dict:
        return {"source": self.source, "is_realtime": self.is_realtime}


@dataclass
class _Quote:
    volume: int | float | None
    amount: float | None
    trade_date: date
    source_meta: _Meta | None
    last_price: float = 1410.0
    prev_close: float = 1400.0
    open: float = 1405.0
    high: float = 1415.0
    low: float = 1400.0
    change: float = 10.0
    change_pct: float = 0.71
    quote_time: object | None = None


class _Realtime:
    def __init__(self, quote: _Quote) -> None:
        self._quote = quote

    def get_quotes(self, symbols, daily_kline_fallback_fn=None):
        return {SYMBOL: self._quote}


class _ShareCapital:
    def load(self) -> pl.DataFrame:
        return pl.DataFrame({"symbol": [SYMBOL], "issued_shares": [ISSUED_SHARES]})


class _Daily:
    def __init__(self, through: date) -> None:
        days = [TRADE_DATE - timedelta(days=1), TRADE_DATE]
        days = [d for d in days if d <= through]
        self._df = pl.DataFrame(
            {
                "symbol": [SYMBOL] * len(days),
                "date": days,
                "open": [1395.0] * len(days),
                "high": [1405.0] * len(days),
                "low": [1390.0] * len(days),
                "close": [1400.0, 1410.0][: len(days)],
                "volume": [20_000_000.0, OFFICIAL_VOLUME][: len(days)],
                "amount": [28_000_000_000.0, OFFICIAL_AMOUNT][: len(days)],
            }
        )

    def latest_date(self):
        return self._df["date"].max()

    def read_range(self, symbols, start, end):
        return self._df


@pytest.fixture
def isolated(deny_network, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)


def _row(monkeypatch, clock: datetime, stored_through: date, quote: _Quote) -> dict:
    from app.taiwan.realtime import calendar
    from app.taiwan.watchlist_enrichment import enrich_taiwan_watchlist_rows

    monkeypatch.setattr(calendar, "taipei_now", lambda: clock)
    (row,) = enrich_taiwan_watchlist_rows(
        [SYMBOL],
        security_master=_Master(),
        realtime_service=_Realtime(quote),
        daily_store=_Daily(stored_through),
        share_capital=_ShareCapital(),
    )
    return row


def _mis_quote() -> _Quote:
    return _Quote(volume=MIS_VOLUME, amount=None, trade_date=TRADE_DATE, source_meta=_Meta())


def test_intraday_turnover_uses_board_lot_volume_and_is_flagged(isolated, monkeypatch):
    clock = datetime(2026, 10, 7, 10, 0, tzinfo=TAIPEI)

    row = _row(monkeypatch, clock, TRADE_DATE - timedelta(days=1), _mis_quote())

    assert row["turnover_rate"] == pytest.approx(MIS_VOLUME * 100.0 / ISSUED_SHARES)
    assert row["board_lot_volume"] is True
    assert row["amount"] is None


def test_intraday_amount_from_a_realtime_quote_stays_null(isolated, monkeypatch):
    clock = datetime(2026, 10, 7, 10, 0, tzinfo=TAIPEI)
    quote = _Quote(volume=MIS_VOLUME, amount=1.0, trade_date=TRADE_DATE, source_meta=_Meta())

    row = _row(monkeypatch, clock, TRADE_DATE - timedelta(days=1), quote)

    assert row["amount"] is None
    assert row["board_lot_volume"] is True


def test_the_official_row_replaces_mis_volume_and_amount(isolated, monkeypatch):
    clock = datetime(2026, 10, 7, 20, 0, tzinfo=TAIPEI)

    row = _row(monkeypatch, clock, TRADE_DATE, _mis_quote())

    assert row["turnover_rate"] == pytest.approx(OFFICIAL_VOLUME * 100.0 / ISSUED_SHARES)
    assert row["amount"] == OFFICIAL_AMOUNT
    assert row["board_lot_volume"] is False


def test_a_non_realtime_snapshot_is_not_flagged(isolated, monkeypatch):
    clock = datetime(2026, 10, 7, 14, 0, tzinfo=TAIPEI)
    quote = _Quote(
        volume=OFFICIAL_VOLUME,
        amount=OFFICIAL_AMOUNT,
        trade_date=TRADE_DATE,
        source_meta=_Meta(is_realtime=False, source="twse:official"),
    )

    row = _row(monkeypatch, clock, TRADE_DATE - timedelta(days=1), quote)

    assert row["amount"] == OFFICIAL_AMOUNT
    assert row["board_lot_volume"] is False


def _request(snapshot: pl.DataFrame, live_bar: dict | None = None):
    service = SimpleNamespace(
        get_enriched_today=lambda: (snapshot, date.today()), fetch_live_bar=lambda s: live_bar
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(quote_service=service)))


def _snapshot(**extra) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [SYMBOL],
            "open": [1405.0],
            "high": [1415.0],
            "low": [1400.0],
            "close": [1410.0],
            "volume": [float(MIS_VOLUME)],
            "turnover_rate": [MIS_VOLUME * 100.0 / ISSUED_SHARES],
            **{k: [v] for k, v in extra.items()},
        }
    )


def test_a_live_candle_from_the_intraday_pipeline_is_board_lot(deny_network):
    from app.api.kline import _maybe_inject_live_candle

    snapshot = _snapshot(volume_time_factor=4.5, amount=None)

    (row,) = _maybe_inject_live_candle(_request(snapshot), SYMBOL, [], "stock")

    assert row["board_lot_volume"] is True
    assert row["amount"] is None
    assert row["volume"] == MIS_VOLUME


def test_a_live_candle_from_the_published_row_is_official(deny_network):
    from app.api.kline import _maybe_inject_live_candle

    snapshot = _snapshot(amount=OFFICIAL_AMOUNT).with_columns(
        pl.lit(OFFICIAL_VOLUME).alias("volume"),
        pl.lit(OFFICIAL_VOLUME * 100.0 / ISSUED_SHARES).alias("turnover_rate"),
    )

    (row,) = _maybe_inject_live_candle(_request(snapshot), SYMBOL, [], "stock")

    assert row["board_lot_volume"] is False
    assert row.get("volume_time_factor") is None
    assert row["volume"] == OFFICIAL_VOLUME
    assert row["amount"] == OFFICIAL_AMOUNT
    assert row["turnover_rate"] == pytest.approx(OFFICIAL_VOLUME * 100.0 / ISSUED_SHARES)


def test_an_on_demand_bar_is_board_lot_and_estimated(deny_network):
    from app.api.kline import _maybe_inject_live_candle

    quote_ts = int(datetime(2026, 10, 7, 10, 0, tzinfo=TAIPEI).timestamp() * 1000)
    live_bar = {
        "symbol": SYMBOL,
        "open": 1405.0,
        "high": 1415.0,
        "low": 1400.0,
        "close": 1410.0,
        "volume": float(MIS_VOLUME),
        "amount": None,
        "quote_ts": quote_ts,
    }
    other = _snapshot().with_columns(pl.lit("2317.TWSE").alias("symbol"))

    (row,) = _maybe_inject_live_candle(_request(other, live_bar), SYMBOL, [], "stock")

    assert row["board_lot_volume"] is True
    assert row["volume_time_factor"] == pytest.approx(4.5)
