"""An initial listing of an ordinary stock trades without a daily limit for its first five sessions.

TWSE Operating Rules art. 63 (TPEx has the same rule): no limit from the listing session through
the fifth session, the listing session counting as the first; a TPEx stock moving to TWSE keeps the
ordinary limit. Sessions are counted on the stored daily-bar calendar.
"""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.indicators import pipeline
from app.taiwan.daily_store import TaiwanDailyStore, listing_sessions
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.market_rules import ListingSessions, ipo_no_limit_until
from app.taiwan.universe.models import (
    ListingDateUnknownError,
    MarketProfileBridge,
    TaiwanInstrument,
)

SESSIONS = (
    date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 14),
    date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17), date(2026, 9, 18),
)
LISTED = SESSIONS[2]
WINDOW = SESSIONS[2:7]
SIXTH = SESSIONS[7]
CALENDAR = ListingSessions(SESSIONS)
IPO = "7799.TWSE"


def _instrument(
    symbol: str = IPO,
    listing_date: str | None = "20260910",
    *,
    instrument_type: str = "stock",
) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    etf = instrument_type == "etf"
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name="新上市",
        instrument_type=instrument_type,
        listing_status="active",
        listing_date=listing_date,
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="ETF" if etf else "股票",
        is_supported=True,
        source="TWSE_OPENAPI",
        updated_at="2026-09-17",
        etf_category="equity" if etf else None,
        classification_source="official_metadata" if etf else None,
        underlying_scope="domestic" if etf else None,
    )


class _Master:
    def __init__(self, instruments: dict[str, TaiwanInstrument]) -> None:
        self._instruments = instruments

    def get_instrument(self, symbol: str) -> TaiwanInstrument | None:
        return self._instruments.get(symbol)


@pytest.fixture
def master(monkeypatch):
    instruments = {IPO: _instrument()}
    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: _Master(instruments))
    pipeline.invalidate_taiwan_limit_profile_cache()
    yield instruments
    pipeline.invalidate_taiwan_limit_profile_cache()


@pytest.fixture
def stored_sessions(tmp_path, monkeypatch):
    """Store one bar per calendar session under DATA_DIR, the calendar the default loader reads."""
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)

    def write(days: tuple[date, ...] = SESSIONS) -> TaiwanDailyStore:
        store = TaiwanDailyStore(tmp_path / "taiwan" / "daily")
        store.write_batch(pl.DataFrame([
            {"symbol": "2330.TWSE", "date": d.isoformat(), "open": 1000.0, "high": 1000.0, "low": 1000.0,
             "close": 1000.0, "volume": 1.0, "amount": 1000.0, "quote_ts": None}
            for d in days
        ]))
        return store

    return write


class TestWindow:
    def test_the_listing_session_is_the_first_of_five(self):
        assert ipo_no_limit_until(LISTED, SESSIONS) == date(2026, 9, 16)

    def test_a_listing_date_without_a_session_starts_at_the_next_session(self):
        """2024-10-02 and 10-03 were typhoon closures; 6919.TWSE first traded on 10-04."""
        calendar = (date(2024, 10, 1), date(2024, 10, 4), date(2024, 10, 7), date(2024, 10, 8),
                    date(2024, 10, 9), date(2024, 10, 11), date(2024, 10, 14))
        assert ipo_no_limit_until(date(2024, 10, 2), calendar) == date(2024, 10, 11)

    def test_a_window_still_short_of_five_recorded_sessions_stays_open(self):
        assert ipo_no_limit_until(LISTED, SESSIONS[:5]) == date.max
        assert ipo_no_limit_until(SIXTH + (SIXTH - LISTED), SESSIONS) == date.max

    def test_a_listing_before_the_calendar_cannot_be_placed(self):
        assert ipo_no_limit_until(date(2026, 9, 7), SESSIONS) is None
        assert ipo_no_limit_until(LISTED, ()) is None


class TestBridge:
    @pytest.mark.parametrize("symbol", [IPO, "7799.TPEX"])
    def test_no_limit_through_the_fifth_session_then_ten_percent(self, symbol):
        inst = _instrument(symbol)
        pct = [MarketProfileBridge.get_price_limit_pct(inst, trade_date=d, sessions=CALENDAR) for d in SESSIONS]
        assert pct == [0.10, 0.10, None, None, None, None, None, 0.10, 0.10]
        assert MarketProfileBridge.ipo_no_limit_window(inst, CALENDAR) == (LISTED, date(2026, 9, 16))

    def test_limits_are_absent_inside_the_window(self):
        inst = _instrument()
        assert MarketProfileBridge.calc_limits(100.0, inst, trade_date=WINDOW[1], sessions=CALENDAR) == (None, None)
        assert MarketProfileBridge.calc_limits(220.0, inst, trade_date=SIXTH, sessions=CALENDAR) == (242.0, 198.0)

    def test_a_transfer_from_tpex_keeps_the_ten_percent_limit(self):
        calendar = ListingSessions(SESSIONS, {"7799.TPEX": SESSIONS[0]})
        inst = _instrument()
        assert MarketProfileBridge.ipo_no_limit_window(inst, calendar) is None
        assert [MarketProfileBridge.get_price_limit_pct(inst, trade_date=d, sessions=calendar) for d in WINDOW] == [0.10] * 5

    def test_history_stored_under_the_current_exchange_is_not_an_initial_listing(self):
        """6873.TWSE moved from TPEx on 2024-09-26; its earlier TPEx bars are stored as 6873.TWSE."""
        calendar = ListingSessions(SESSIONS, {IPO: SESSIONS[0]})
        inst = _instrument()
        assert MarketProfileBridge.ipo_no_limit_window(inst, calendar) is None
        assert [MarketProfileBridge.get_price_limit_pct(inst, trade_date=d, sessions=calendar) for d in WINDOW] == [0.10] * 5

    def test_a_listing_without_earlier_bars_is_an_initial_listing(self):
        calendar = ListingSessions(SESSIONS, {IPO: LISTED, "2330.TWSE": SESSIONS[0], "7798.TPEX": SESSIONS[0]})
        inst = _instrument()
        assert MarketProfileBridge.ipo_no_limit_window(inst, calendar) == (LISTED, date(2026, 9, 16))
        assert MarketProfileBridge.get_price_limit_pct(inst, trade_date=WINDOW[2], sessions=calendar) is None

    def test_a_tdr_keeps_the_ordinary_rule(self):
        """Six-digit codes starting with 91 are depositary receipts, outside the initial-listing rule."""
        tdr = _instrument("910861.TWSE")
        assert MarketProfileBridge.ipo_no_limit_window(tdr, CALENDAR) is None
        assert [MarketProfileBridge.get_price_limit_pct(tdr, trade_date=d, sessions=CALENDAR) for d in WINDOW] == [0.10] * 5
        assert MarketProfileBridge.get_price_limit_pct(_instrument("910861.TWSE", None), trade_date=SIXTH) == 0.10

    def test_a_four_digit_tdr_named_dr_keeps_the_ordinary_rule(self):
        """9103 美德醫療-DR has a four-digit code; its -DR name marks it as a depositary receipt."""
        from dataclasses import replace

        tdr = replace(_instrument("9103.TWSE"), name="美德醫療-DR")
        assert MarketProfileBridge.ipo_no_limit_window(tdr, CALENDAR) is None
        assert [MarketProfileBridge.get_price_limit_pct(tdr, trade_date=d, sessions=CALENDAR) for d in WINDOW] == [0.10] * 5
        assert MarketProfileBridge.ipo_no_limit_window(_instrument("9103.TWSE"), CALENDAR) == (LISTED, date(2026, 9, 16))

    def test_tpex_bars_from_the_listing_session_on_are_not_a_transfer(self):
        calendar = ListingSessions(SESSIONS, {"7799.TPEX": LISTED})
        assert MarketProfileBridge.get_price_limit_pct(_instrument(), trade_date=LISTED, sessions=calendar) is None

    @pytest.mark.parametrize("raw", [None, "", "not-a-date", "20261340"])
    def test_an_unknown_listing_date_is_an_unknown_rule(self, raw):
        inst = _instrument(listing_date=raw)
        with pytest.raises(ListingDateUnknownError):
            MarketProfileBridge.get_price_limit_pct(inst, trade_date=SIXTH, sessions=CALENDAR)
        with pytest.raises(ListingDateUnknownError):
            MarketProfileBridge.get_price_limit_pct(inst)
        assert issubclass(ListingDateUnknownError, ValueError)

    @pytest.mark.parametrize("raw", ["20260910", "2026/09/10", "1150910", "2026-09-10"])
    def test_every_master_date_format_places_the_window(self, raw):
        inst = _instrument(listing_date=raw)
        assert MarketProfileBridge.get_price_limit_pct(inst, trade_date=WINDOW[0], sessions=CALENDAR) is None

    def test_a_new_etf_keeps_its_own_rule(self):
        etf = _instrument("00999.TWSE", instrument_type="etf")
        assert [MarketProfileBridge.get_price_limit_pct(etf, trade_date=d, sessions=CALENDAR) for d in WINDOW] == [0.10] * 5
        assert MarketProfileBridge.get_price_limit_pct(_instrument("00999.TWSE", None, instrument_type="etf")) == 0.10

    def test_the_default_calendar_is_the_stored_daily_bars(self, stored_sessions):
        stored_sessions()
        inst = _instrument()
        assert MarketProfileBridge.get_price_limit_pct(inst, trade_date=WINDOW[-1]) is None
        assert MarketProfileBridge.get_price_limit_pct(inst, trade_date=SIXTH) == 0.10


def _one_price(closes: list[float], days: tuple[date, ...] = SESSIONS[2:]) -> pl.DataFrame:
    days = days[:len(closes)]
    return pl.DataFrame({
        "symbol": [IPO] * len(closes),
        "date": list(days),
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "volume": [1_000_000.0] * len(closes),
        "amount": [c * 1_000_000.0 for c in closes],
    })


UP = [100.0, 125.0, 150.0, 180.0, 220.0, 242.0, 266.0]
DOWN = [100.0, 80.0, 64.0, 51.2, 41.0, 36.9, 33.25]


def _published(raw: pl.DataFrame) -> pl.DataFrame:
    enriched = pipeline.compute_enriched(raw, factors=pl.DataFrame(), instruments=pl.DataFrame())
    service = object.__new__(TaiwanEnrichedRefreshService)
    return service._add_limit_signals(enriched, sessions=CALENDAR).sort("date")


class TestPublishedSignals:
    def test_one_price_limit_ups_inside_the_window_are_not_limit_boards(self, master):
        out = _published(_one_price(UP))
        assert out["signal_limit_up"].to_list() == [False] * 5 + [True, True]
        assert out["consecutive_limit_ups"].to_list() == [0] * 5 + [1, 2]
        assert out["signal_broken_limit_up"].to_list() == [False] * 7

    def test_one_price_limit_downs_inside_the_window_are_not_limit_boards(self, master):
        out = _published(_one_price(DOWN))
        assert out["signal_limit_down"].to_list() == [False] * 5 + [True, True]
        assert out["consecutive_limit_downs"].to_list() == [0] * 5 + [1, 2]
        assert out["signal_limit_down_recovery"].to_list() == [False] * 7

    def test_a_transfer_from_tpex_is_flagged_from_its_first_twse_session(self, master):
        enriched = pipeline.compute_enriched(_one_price(UP), factors=pl.DataFrame(), instruments=pl.DataFrame())
        service = object.__new__(TaiwanEnrichedRefreshService)
        calendar = ListingSessions(SESSIONS, {"7799.TPEX": SESSIONS[0]})
        out = service._add_limit_signals(enriched, sessions=calendar).sort("date")
        assert out["signal_limit_up"].to_list() == [False] + [True] * 6
        assert out["consecutive_limit_ups"].to_list() == [0, 1, 2, 3, 4, 5, 6]

    def test_an_unknown_listing_date_publishes_no_limit_board(self, master):
        master[IPO] = _instrument(listing_date=None)
        out = _published(_one_price(UP))
        assert out["signal_limit_up"].to_list() == [False] * 7

    def test_refresh_counts_sessions_on_its_own_daily_store(self, master, tmp_path, monkeypatch):
        from app.config import settings
        from app.taiwan.share_capital_store import TaiwanShareCapitalStore

        monkeypatch.setattr(settings, "data_dir", tmp_path)
        store = TaiwanDailyStore(tmp_path / "daily")
        store.write_batch(pl.DataFrame([
            {"symbol": IPO, "date": d.isoformat(), "open": c, "high": c, "low": c, "close": c,
             "volume": 1_000_000.0, "amount": c * 1_000_000.0, "quote_ts": None}
            for d, c in zip(SESSIONS[2:], UP, strict=True)
        ]))
        TaiwanEnrichedRefreshService(
            daily_store=store,
            enriched_root=tmp_path / "kline_daily_enriched",
            share_capital=TaiwanShareCapitalStore(data_dir=tmp_path),
        ).refresh()
        out = pl.read_parquet(tmp_path / "kline_daily_enriched" / "**" / "*.parquet").sort("date")
        assert out["signal_limit_up"].to_list() == [False] * 5 + [True, True]
        assert out["consecutive_limit_ups"].to_list() == [0] * 5 + [1, 2]


class TestPipelinePaths:
    def test_batch_signals_skip_the_window(self, master, stored_sessions):
        stored_sessions()
        out = pipeline.compute_enriched(
            _one_price(UP), factors=pl.DataFrame(), instruments=pl.DataFrame({"symbol": [IPO], "float_shares": [1e9]})
        ).sort("date")
        assert True not in out["signal_limit_up"].to_list()[:5]
        assert out["signal_limit_up"].to_list()[5:] == [True, True]
        assert out["consecutive_limit_ups"].to_list() == [0] * 5 + [1, 2]

    def _today(self, day: date, prev_close: float, close: float) -> pl.DataFrame:
        return pl.DataFrame({
            "symbol": [IPO], "date": [day], "open": [close], "high": [close], "low": [close], "close": [close],
            "raw_close": [close], "raw_high": [close], "raw_low": [close],
            "_prev_close_raw": pl.Series([prev_close], dtype=pl.Float64), "volume": [1000.0],
        })

    def test_listing_session_quoted_against_the_offer_price_is_not_a_limit_board(self, master, stored_sessions):
        """The quote's previous close on the listing session is the offer price; the session itself is not stored yet."""
        stored_sessions(SESSIONS[:2])
        out = pipeline._compute_limit_signals_today(self._today(LISTED, 100.0, 130.0), pl.DataFrame({"symbol": [IPO]}))
        assert out["signal_limit_up"][0] is not True
        assert out["consecutive_limit_ups"][0] == 0

    def test_sixth_session_intraday_is_a_limit_board(self, master, stored_sessions):
        stored_sessions()
        out = pipeline._compute_limit_signals_today(self._today(SIXTH, 220.0, 242.0), pl.DataFrame({"symbol": [IPO]}))
        assert out["signal_limit_up"][0] is True

    def test_batch_signals_ignore_published_limits_inside_the_window(self, master, stored_sessions):
        stored_sessions()
        prices = [125.0, 242.0]
        df = pl.DataFrame({
            "symbol": [IPO, IPO], "date": [WINDOW[1], SIXTH],
            **{c: prices for c in ("open", "high", "low", "close", "raw_close", "raw_high", "raw_low")},
            "ref_prev_close": [100.0, 220.0], "ref_limit_up": [110.0, 242.0], "ref_limit_down": [90.0, 198.0],
        })
        out = pipeline.compute_limit_signals(df, pl.DataFrame({"symbol": [IPO]})).sort("date")
        assert out["signal_limit_up"].to_list()[0] is not True
        assert out["signal_limit_up"].to_list()[1] is True
        assert out["consecutive_limit_ups"].to_list() == [0, 1]

    def test_distance_to_limits_is_null_inside_the_window(self, master, stored_sessions):
        stored_sessions()
        df = pl.DataFrame({
            "symbol": [IPO, IPO], "date": [WINDOW[1], SIXTH], "close": [120.0, 240.0], "prev_close": [100.0, 220.0],
        })
        out = pipeline.add_limit_distance_columns(df)
        assert out["distance_to_upper_limit"].to_list()[0] is None
        assert out["distance_to_upper_limit"].to_list()[1] == pytest.approx((242.0 - 240.0) / 242.0)

    def test_the_profile_window_follows_the_growing_calendar(self, master):
        open_window = pipeline.taiwan_limit_profile([IPO], sessions=ListingSessions(SESSIONS[:4]))
        closed = pipeline.taiwan_limit_profile([IPO], sessions=CALENDAR)
        assert open_window.row(0, named=True)["ipo_end"] == date.max
        assert closed.row(0, named=True)["ipo_end"] == date(2026, 9, 16)
        assert closed.row(0, named=True)["limit_pct"] == pytest.approx(0.10)


def test_a_partition_written_elsewhere_appears_once_the_entry_expires(stored_sessions, monkeypatch):
    """A write from another process does not pass through this process's store, so only expiry reveals it."""
    import shutil

    from app.taiwan import daily_store

    store = stored_sessions(SESSIONS[:3])
    assert listing_sessions(store).trading_dates == SESSIONS[:3]
    other = store._data_dir / f"date={SESSIONS[3].isoformat()}"
    other.mkdir()
    shutil.copy(store._data_dir / f"date={SESSIONS[0].isoformat()}" / "part.parquet", other / "part.parquet")
    assert listing_sessions(store).trading_dates == SESSIONS[:3]
    monkeypatch.setattr(daily_store, "_LISTING_SESSIONS_TTL_SECONDS", 0.0)
    assert listing_sessions(store).trading_dates == SESSIONS[:4]


def test_the_default_calendar_picks_up_a_new_session(stored_sessions):
    store = stored_sessions(SESSIONS[:3])
    assert listing_sessions(store).trading_dates == SESSIONS[:3]
    stored_sessions(SESSIONS[3:4])
    assert listing_sessions(store).trading_dates == SESSIONS[:4]
    assert listing_sessions().trading_dates == SESSIONS[:4]
