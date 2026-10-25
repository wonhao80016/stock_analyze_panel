"""Taiwan limit-board signals and streaks use the exchange tick ladder on every pipeline path.

2454.TWSE closed one-price at 4315 on 2026-09-01 against a previous close of 3925; the
statutory limit 4317.5 rounds down to the 5-dollar tick, while A-share cent rounding gives
4317.50 and misses the board. 6225.TWSE closed one-price at its 46.2 limit down on 2026-09-23.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.indicators import pipeline
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

DAYS = [date(2026, 8, 27), date(2026, 8, 28), date(2026, 8, 31), date(2026, 9, 1)]
SHARES = 1.0e9


def _instrument(
    symbol: str,
    *,
    instrument_type: str = "stock",
    etf_category: str | None = None,
    underlying_scope: str | None = None,
) -> TaiwanInstrument:
    return TaiwanInstrument(
        symbol=symbol,
        code=symbol.split(".")[0],
        exchange="TWSE" if symbol.endswith(".TWSE") else "TPEX",
        name="測試標的",
        instrument_type=instrument_type,
        listing_status="active",
        listing_date="2020/01/01",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="股票" if instrument_type == "stock" else "ETF",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-09-17",
        etf_category=etf_category,
        classification_source="official_metadata" if instrument_type == "etf" else None,
        underlying_scope=underlying_scope,
    )


class _FakeMaster:
    def __init__(self, instruments: dict[str, TaiwanInstrument]):
        self._instruments = instruments

    def get_instrument(self, symbol: str) -> TaiwanInstrument | None:
        return self._instruments.get(symbol)


@pytest.fixture(autouse=True)
def _ordinary_stocks(monkeypatch):
    symbols = ["2454.TWSE", "6225.TWSE", "2330.TWSE", "2379.TWSE"]
    master = _FakeMaster({s: _instrument(s) for s in symbols})
    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: master)
    pipeline.invalidate_taiwan_limit_profile_cache()
    yield
    pipeline.invalidate_taiwan_limit_profile_cache()


def _one_price_bars(symbol: str, closes: list[float]) -> pl.DataFrame:
    days = DAYS[-len(closes):]
    return pl.DataFrame({
        "symbol": [symbol] * len(closes),
        "date": days,
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "volume": [1_000_000.0] * len(closes),
        "amount": [c * 1_000_000.0 for c in closes],
    })


def _share_capital(symbol: str) -> pl.DataFrame:
    return pl.DataFrame({"symbol": [symbol], "float_shares": [SHARES]})


def _publish_signals(enriched: pl.DataFrame) -> pl.DataFrame:
    service = TaiwanEnrichedRefreshService(
        daily_store=MagicMock(),
        enriched_root=Path("unused"),
        share_capital=MagicMock(),
        corporate_actions=MagicMock(),
    )
    return service._add_limit_signals(enriched).sort(["symbol", "date"])


def _published(raw: pl.DataFrame, *, with_share_capital: bool, factors: pl.DataFrame | None = None) -> pl.DataFrame:
    instruments = _share_capital(raw["symbol"][0]) if with_share_capital else pl.DataFrame()
    enriched = pipeline.compute_enriched(raw, factors=factors if factors is not None else pl.DataFrame(),
                                         instruments=instruments)
    return _publish_signals(enriched)


@pytest.mark.parametrize("with_share_capital", [True, False])
def test_published_streak_counts_consecutive_one_price_limit_ups(with_share_capital):
    out = _published(_one_price_bars("2454.TWSE", [3925.0, 4315.0, 4745.0]), with_share_capital=with_share_capital)

    assert out["signal_limit_up"].to_list() == [False, True, True]
    assert out["consecutive_limit_ups"].to_list() == [0, 1, 2]
    assert out["consecutive_limit_downs"].to_list() == [0, 0, 0]
    assert out.schema["consecutive_limit_ups"] == pl.UInt32


@pytest.mark.parametrize("with_share_capital", [True, False])
def test_published_streak_counts_consecutive_one_price_limit_downs(with_share_capital):
    out = _published(_one_price_bars("6225.TWSE", [51.3, 46.2, 41.6]), with_share_capital=with_share_capital)

    assert out["signal_limit_down"].to_list() == [False, True, True]
    assert out["consecutive_limit_downs"].to_list() == [0, 1, 2]
    assert out["consecutive_limit_ups"].to_list() == [0, 0, 0]


def test_published_streak_restarts_after_a_session_off_the_board():
    out = _published(_one_price_bars("2454.TWSE", [3925.0, 4315.0, 4300.0, 4730.0]), with_share_capital=True)

    assert out["signal_limit_up"].to_list() == [False, True, False, True]
    assert out["consecutive_limit_ups"].to_list() == [0, 1, 0, 1]


def test_pipeline_streak_uses_the_taiwan_tick_ladder():
    raw = _one_price_bars("2454.TWSE", [3925.0, 4315.0, 4745.0])

    out = pipeline.compute_enriched(raw, factors=pl.DataFrame(), instruments=_share_capital("2454.TWSE"))

    assert out["signal_limit_up"].to_list() == [None, True, True]
    assert out["consecutive_limit_ups"].to_list() == [0, 1, 2]


def test_event_session_uses_the_published_raw_scale_limits():
    """The 2026-08-31 ex-dividend session limits come from a 3925 reference, not the 4000 close before it.

    A split on 2026-09-01 forward-adjusts every earlier close, so the limits must be compared on the raw scale.
    """
    event, split = DAYS[2], DAYS[3]
    raw = (
        _one_price_bars("2454.TWSE", [4000.0, 4315.0, 2160.0])
        .with_columns(
            pl.when(pl.col("date") == event).then(pl.lit(value)).otherwise(None).cast(pl.Float64).alias(name)
            for name, value in (("ref_prev_close", 3925.0), ("ref_limit_up", 4315.0), ("ref_limit_down", 3535.0))
        )
    )
    factors = pl.DataFrame({
        "symbol": ["2454.TWSE", "2454.TWSE"],
        "trade_date": [event, split],
        "ex_factor": [4000.0 / 3925.0, 2.0],
    })

    pipeline_out = pipeline.compute_enriched(raw, factors=factors, instruments=_share_capital("2454.TWSE"))
    published = _publish_signals(pipeline_out)

    assert pipeline_out.filter(pl.col("date") == event)["close"][0] == pytest.approx(4315.0 / 2.0)
    for out in (pipeline_out, published):
        assert out["signal_limit_up"].to_list()[1:] == [True, False]
        assert out["consecutive_limit_ups"].to_list() == [0, 1, 0]


def test_event_session_streak_continues_onto_the_next_tick_rounded_limit():
    event = DAYS[2]
    raw = (
        _one_price_bars("2454.TWSE", [4000.0, 4315.0, 4745.0])
        .with_columns(
            pl.when(pl.col("date") == event).then(pl.lit(value)).otherwise(None).cast(pl.Float64).alias(name)
            for name, value in (("ref_prev_close", 3925.0), ("ref_limit_up", 4315.0), ("ref_limit_down", 3535.0))
        )
    )
    factors = pl.DataFrame({"symbol": ["2454.TWSE"], "trade_date": [event], "ex_factor": [4000.0 / 3925.0]})

    out = _published(raw, with_share_capital=True, factors=factors)

    assert out["consecutive_limit_ups"].to_list() == [0, 1, 2]


def _published_like(prev_close: list[float | None], close: float) -> pl.DataFrame:
    n = len(prev_close)
    return pl.DataFrame({
        "symbol": ["2330.TWSE"] * n,
        "date": DAYS[:n],
        "prev_close": pl.Series(prev_close, dtype=pl.Float64),
        "close": [close] * n,
        "raw_close": [close] * n,
        "raw_high": [close] * n,
        "raw_low": [close] * n,
    })


def test_published_signals_skip_rows_without_a_positive_reference():
    out = _publish_signals(_published_like([0.0, -1.0, None], 10.0))

    for column in ("signal_limit_up", "signal_limit_down", "signal_broken_limit_up", "signal_limit_down_recovery"):
        assert out[column].to_list() == [False, False, False], column
    assert out["consecutive_limit_ups"].to_list() == [0, 0, 0]
    assert out["consecutive_limit_downs"].to_list() == [0, 0, 0]


def test_pipeline_signals_skip_rows_without_a_positive_reference():
    df = pl.DataFrame({
        "symbol": ["2330.TWSE"] * 3,
        "date": DAYS[:3],
        "open": [0.0, 10.0, 10.0],
        "high": [0.0, 10.0, 10.0],
        "low": [0.0, 10.0, 10.0],
        "close": [0.0, 10.0, 10.0],
        "raw_close": [0.0, 10.0, 10.0],
        "raw_high": [0.0, 10.0, 10.0],
        "raw_low": [0.0, 10.0, 10.0],
        "ref_prev_close": pl.Series([None, None, -1.0], dtype=pl.Float64),
    })

    out = pipeline.compute_limit_signals(df, pl.DataFrame({"symbol": ["2330.TWSE"]}))

    for column in ("signal_limit_up", "signal_limit_down", "signal_broken_limit_up", "signal_limit_down_recovery"):
        assert True not in out[column].to_list(), column
    assert out["consecutive_limit_ups"].to_list() == [0, 0, 0]


def _above_ten_thousand(symbol: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    daily = pl.DataFrame({
        "symbol": [symbol, symbol],
        "date": DAYS[-2:],
        "open": [11264.0, 12390.0],
        "high": [11264.0, 12390.0],
        "low": [11264.0, 12390.0],
        "close": [11264.0, 12390.0],
        "raw_close": [11264.0, 12390.0],
        "raw_high": [11264.0, 12390.0],
        "raw_low": [11264.0, 12390.0],
    })
    instruments = pl.DataFrame({
        "symbol": [symbol],
        "name": ["普通股"],
        "limit_up": [12390.0],
        "limit_down": [10140.0],
        "as_of": [DAYS[-1]],
    })
    return daily, instruments


def test_a_limit_above_ten_thousand_is_a_real_limit():
    daily, instruments = _above_ten_thousand("2454.TWSE")

    batch = pipeline.compute_limit_signals(daily, instruments)

    assert batch["signal_limit_up"][-1] is True
    assert batch["consecutive_limit_ups"][-1] == 1


def test_taiwan_limits_ignore_instrument_table_prices():
    daily, instruments = _above_ten_thousand("2454.TWSE")
    stale = instruments.with_columns(pl.lit(13000.0).alias("limit_up"), pl.lit(9000.0).alias("limit_down"))

    batch = pipeline.compute_limit_signals(daily, stale)

    assert batch["signal_limit_up"][-1] is True


class _Adapter:
    def __init__(self, instruments: list[TaiwanInstrument]):
        self.instruments = instruments

    def get_instruments(self, _html=None) -> list[TaiwanInstrument]:
        return self.instruments


def test_reloading_the_security_master_drops_cached_limit_profiles(tmp_path, monkeypatch):
    symbol = "00646.TWSE"
    stock = _instrument(symbol)
    foreign_etf = _instrument(symbol, instrument_type="etf", etf_category="foreign_equity", underlying_scope="foreign")
    master = TaiwanSecurityMaster(
        cache_path=tmp_path / "security_master.parquet",
        twse_adapter=_Adapter([foreign_etf]),
        tpex_adapter=_Adapter([]),
    )
    master.load_from_adapters()
    master.save_cache()
    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: master)

    master.twse_adapter = _Adapter([stock])
    master.load_from_adapters()
    assert pipeline.taiwan_limit_profile([symbol])["limit_pct"][0] == pytest.approx(0.10)

    assert master.load_cache()
    assert pipeline.taiwan_limit_profile([symbol])["limit_pct"][0] is None

    master.load_from_adapters()
    assert pipeline.taiwan_limit_profile([symbol])["limit_pct"][0] == pytest.approx(0.10)
