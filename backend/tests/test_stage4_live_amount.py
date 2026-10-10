"""Intraday Taiwan rows carry an estimated traded value for strategies; displays keep showing none."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from app.backtest.matrix import build_market_data_matrix
from app.indicators import pipeline
from app.indicators.pipeline import invalidate_custom_signals, invalidate_taiwan_limit_profile_cache
from app.strategy.builtin import swing_trend_confirmation
from app.strategy.engine import StrategyEngine
from app.strategy.scoring import AMOUNT_ESTIMATED
from app.taiwan import universe
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

LIQUID, THIN = "2330.TWSE", "2317.TWSE"
LIVE_VOLUME = {LIQUID: 3_000_000.0, THIN: 100_000.0}
LIVE_CLOSE = 100.0


def _sessions(count: int) -> list[date]:
    out, day = [], date(2026, 5, 4)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


DAYS = _sessions(81)
TODAY = DAYS[-1]
QUOTE_TS = int(
    datetime(TODAY.year, TODAY.month, TODAY.day, 10, 0, tzinfo=TAIPEI).timestamp() * 1000
)
AFTER_CLOSE_TS = int(
    datetime(TODAY.year, TODAY.month, TODAY.day, 14, 0, tzinfo=TAIPEI).timestamp() * 1000
)
AMOUNT_FIELDS = {"vwap_bias": 1.0, "amount_ratio_5d": 1.0, "amihud_20d": 1.0}
SCHEMA = {
    "symbol": pl.String,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "amount": pl.Float64,
    "quote_ts": pl.Int64,
}


def _instrument(symbol: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=code,
        instrument_type="stock",
        listing_status="active",
        listing_date="2000-01-01",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="股票",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-10-07",
    )


def _history() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "symbol": symbol,
                "date": day,
                "open": 99.0 + 0.01 * t,
                "high": 101.0 + 0.01 * t,
                "low": 98.0 + 0.01 * t,
                "close": 100.0 + 0.01 * t,
                "volume": volume,
                "amount": (100.0 + 0.01 * t) * volume,
                "quote_ts": None,
            }
            for symbol, volume in LIVE_VOLUME.items()
            for t, day in enumerate(DAYS[:-1])
        ],
        schema=SCHEMA,
    )


@pytest.fixture
def live_root(tmp_path: Path, monkeypatch) -> Path:
    from app.config import settings

    master_path = tmp_path / "security_master.parquet"
    pl.DataFrame([_instrument(s).to_dict() for s in LIVE_VOLUME]).write_parquet(master_path)
    master = TaiwanSecurityMaster(cache_path=master_path)
    assert master.load_cache()
    TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily").write_batch(_history())
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(universe, "_default_master", master)
    monkeypatch.setattr(pipeline, "cn_today", lambda: TODAY)
    invalidate_taiwan_limit_profile_cache()
    invalidate_custom_signals()
    yield tmp_path
    invalidate_taiwan_limit_profile_cache()
    invalidate_custom_signals()


def _live_rows(
    root: Path, *, amounts: dict[str, float] | None = None, quote_ts: int = QUOTE_TS
) -> pl.DataFrame:
    """Today's rows from MIS-like bars: board-lot volume, and no amount unless *amounts* quotes one."""
    from app.taiwan.live_enriched import TaiwanLiveEnriched

    bars = pl.DataFrame(
        [
            {
                "symbol": symbol,
                "date": TODAY,
                "open": 99.5,
                "high": 100.5,
                "low": 99.0,
                "close": LIVE_CLOSE,
                "volume": volume,
                "amount": (amounts or {}).get(symbol),
                "quote_ts": quote_ts,
            }
            for symbol, volume in LIVE_VOLUME.items()
        ],
        schema=SCHEMA,
    )
    engine = TaiwanLiveEnriched(root)
    engine.prepare()
    rows = engine.rows(bars)
    assert rows is not None
    return rows.sort("symbol")


def test_intraday_rows_estimate_amount_as_close_times_board_lot_volume(live_root):
    rows = _live_rows(live_root)

    amounts = dict(zip(rows["symbol"], rows["amount"], strict=True))
    assert amounts == {symbol: LIVE_CLOSE * volume for symbol, volume in LIVE_VOLUME.items()}


def test_an_intraday_row_passes_the_default_amount_minimum_on_its_estimate(live_root):
    rows = _live_rows(live_root)

    passed = rows.filter(StrategyEngine._basic_filter_expr(rows, {"amount_min": 0.2e8}))

    assert passed["symbol"].to_list() == [LIQUID]


def test_an_intraday_row_passes_a_strategy_min_amount_on_its_estimate(live_root):
    rows = _live_rows(live_root)
    market = build_market_data_matrix(rows, field_columns={"amount"})

    liquid = swing_trend_confirmation._liquid(market, {"min_amount": 1e8, "min_volume": 0.0})

    assert dict(zip(market.symbols, liquid[-1].tolist(), strict=True)) == {
        LIQUID: True,
        THIN: False,
    }


def test_the_live_candle_still_shows_no_amount(live_root):
    from app.api.kline import _maybe_inject_live_candle

    rows = _live_rows(live_root)
    service = SimpleNamespace(
        get_enriched_today=lambda: (rows, date.today()), fetch_live_bar=lambda symbol: None
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(quote_service=service)))

    (candle,) = _maybe_inject_live_candle(request, LIQUID, [], "stock")

    assert candle["amount"] is None
    assert candle["board_lot_volume"] is True


def _scored_today(rows: pl.DataFrame) -> pl.DataFrame:
    """Today's amount scoring fields as a realtime monitor run computes them: history plus live rows."""
    history = pl.concat([_history(), rows], how="diagonal_relaxed")
    current, _ = StrategyEngine._materialize_scoring_frames(rows, history, AMOUNT_FIELDS)
    return current.sort("symbol")


def _expected_amount_fields(symbol: str, amount: float) -> list[float]:
    """vwap_bias, amount_ratio_5d and amihud_20d of today's bar quoted at *amount*."""
    past = _history().filter(pl.col("symbol") == symbol)
    closes = [*past["close"], LIVE_CLOSE]
    amounts = [*past["amount"], amount]
    illiquidity = [
        abs(closes[i] / closes[i - 1] - 1.0) / (amounts[i] / 1e8)
        for i in range(len(closes) - 20, len(closes))
    ]
    return [
        LIVE_CLOSE / (amount / LIVE_VOLUME[symbol]) - 1.0,
        amount / (sum(amounts[-6:-1]) / 5.0) - 1.0,
        sum(illiquidity) / 20.0,
    ]


@pytest.mark.parametrize("quote_ts", [QUOTE_TS, AFTER_CLOSE_TS], ids=["intraday", "after_close"])
def test_amount_ratio_fields_are_null_on_an_estimated_amount(live_root, quote_ts):
    rows = _live_rows(live_root, quote_ts=quote_ts)

    scored = _scored_today(rows)
    alone, _ = StrategyEngine._materialize_scoring_frames(rows, None, AMOUNT_FIELDS)

    assert scored.select(*AMOUNT_FIELDS).to_dicts() == [dict.fromkeys(AMOUNT_FIELDS)] * 2
    assert alone["vwap_bias"].to_list() == [None, None]
    passed = scored.filter(StrategyEngine._basic_filter_expr(scored, {"amount_min": 0.2e8}))
    assert passed["symbol"].to_list() == [LIQUID]
    assert rows[AMOUNT_ESTIMATED].to_list() == [True, True]


@pytest.mark.parametrize("quote_ts", [QUOTE_TS, AFTER_CLOSE_TS], ids=["intraday", "after_close"])
def test_amount_ratio_fields_are_computed_from_a_quoted_amount(live_root, quote_ts):
    amounts = {symbol: 1.002 * LIVE_CLOSE * volume for symbol, volume in LIVE_VOLUME.items()}
    rows = _live_rows(live_root, amounts=amounts, quote_ts=quote_ts)

    scored = _scored_today(rows)

    for symbol, *values in scored.select("symbol", *AMOUNT_FIELDS).iter_rows():
        assert values == pytest.approx(_expected_amount_fields(symbol, amounts[symbol]))
    assert rows[AMOUNT_ESTIMATED].to_list() == [False, False]


def test_a_forced_live_write_keeps_the_estimate_flag_out_of_the_partition(live_root, monkeypatch):
    from app.tickflow.repository import DataStore, KlineRepository

    rows = _live_rows(live_root)
    repo = KlineRepository(DataStore(live_root))
    monkeypatch.setattr(repo, "_official_stock_dates", lambda: frozenset({TODAY}), raising=False)

    repo.merge_live_enriched_asset("stock", rows, force=True)

    partition = live_root / "kline_daily_enriched" / f"date={TODAY.isoformat()}" / "part.parquet"
    assert "amount" in pl.read_parquet_schema(partition)
    assert AMOUNT_ESTIMATED not in pl.read_parquet_schema(partition)
    cached, _ = repo.get_enriched_latest()
    assert cached[AMOUNT_ESTIMATED].to_list() == [True, True]
