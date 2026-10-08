"""The intraday incremental row of a session equals the row the evening publish writes for it.

Both paths see the same bars and corporate actions of two stocks: a 1:4 par-value change 30
sessions back and an 8% cash dividend on the quoted session itself, before the evening publish;
the "closure" scenario also leaves ten weekdays untraded inside the last 90 calendar days. The
history is longer than the 300-calendar-day window the repository recomputes on start-up. The
batch row comes from TaiwanEnrichedRefreshService.refresh through that session; the intraday row
from the repository state published through the previous session (KlineRepository._refresh_enriched)
and QuoteService._enrich_today_incremental, fed the provider's prev_close, change_pct,
change_amount and amplitude as the realtime loop does.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.indicators.pipeline import (
    INDICATOR_COLUMNS,
    LIMIT_SIGNAL_OUTPUTS,
    SIGNAL_DEPENDENCIES,
    invalidate_custom_signals,
    invalidate_taiwan_limit_profile_cache,
)
from app.services import quote_service
from app.services.quote_service import QuoteService
from app.taiwan import universe
from app.taiwan.corporate_actions import TaiwanCorporateActionStore, corporate_action_schema
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.enrichment.corporate_actions import EXDIV, PAR_CHANGE
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument
from app.tickflow.repository import DataStore, KlineRepository

pytestmark = pytest.mark.usefixtures("deny_network")

SPLIT = "2327.TWSE"
DIVIDEND = "1101.TWSE"
SYMBOLS = (DIVIDEND, SPLIT)
SESSIONS = 400
SPLIT_RATIO = 4.0
DIVIDEND_FACTOR = 1.08
# Mirrors KlineRepository: the start-up recompute reads 300 calendar days.
CACHE_DAYS = 300
# Ten weekdays without trading, as around the Lunar New Year, inside the last 90 calendar days.
CLOSURE = frozenset(date(2026, 2, 9) + timedelta(days=i) for i in range(12))


def _sessions(closed: frozenset[date]) -> list[date]:
    out, day = [], date(2024, 9, 2)
    while len(out) < SESSIONS:
        if day.weekday() < 5 and day not in closed:
            out.append(day)
        day += timedelta(days=1)
    return out


def _bars(base: float) -> list[dict]:
    bars, prev_close = [], None
    for t in range(SESSIONS):
        close = base * (1 + 0.08 * math.sin(t / 6) + 0.04 * math.sin(t / 17)) + 0.02 * t
        open_ = close if prev_close is None else prev_close
        bars.append(
            {
                "open": open_,
                "high": max(open_, close) + 0.4 + 0.2 * abs(math.sin(t / 3)),
                "low": min(open_, close) - 0.4 - 0.2 * abs(math.cos(t / 4)),
                "close": close,
                "volume": 2_000_000.0 * (1 + 0.3 * math.sin(t / 5)),
            }
        )
        prev_close = close
    return bars


EVENT_AT = {SPLIT: SESSIONS - 30, DIVIDEND: SESSIONS - 1}
PRICE_FACTOR = {SPLIT: SPLIT_RATIO, DIVIDEND: DIVIDEND_FACTOR}
VOLUME_FACTOR = {SPLIT: SPLIT_RATIO, DIVIDEND: 1.0}


def _raw(symbol: str) -> list[dict]:
    out = []
    for t, bar in enumerate(_bars(50.0 if symbol == SPLIT else 60.0)):
        p, v = (PRICE_FACTOR[symbol], VOLUME_FACTOR[symbol]) if t < EVENT_AT[symbol] else (1.0, 1.0)
        out.append(
            {k: bar[k] * p for k in ("open", "high", "low", "close")}
            | {"volume": bar["volume"] / v}
        )
    return out


RAW = {symbol: _raw(symbol) for symbol in SYMBOLS}


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


def _reference_price(symbol: str) -> float:
    return RAW[symbol][EVENT_AT[symbol] - 1]["close"] / PRICE_FACTOR[symbol]


def _events(days: list[date]) -> pl.DataFrame:
    rows = []
    for symbol in SYMBOLS:
        at = EVENT_AT[symbol]
        pre = RAW[symbol][at - 1]["close"]
        ref = _reference_price(symbol)
        rows.append(
            {
                "symbol": symbol,
                "date": days[at],
                "kind": PAR_CHANGE if symbol == SPLIT else EXDIV,
                "pre_close": pre,
                "ref_price": ref,
                "limit_up": None,
                "limit_down": None,
                "open_ref": ref,
                "ex_factor": pre / ref,
                "official_label": None,
                "provider": "twse",
                "source": "test",
                "source_url": "u",
                "retrieved_at": None,
                "trade_date": days[at],
                "status": "official",
            }
        )
    return pl.DataFrame(rows, schema=corporate_action_schema())


def _daily(days: list[date], through: date) -> pl.DataFrame:
    rows = [
        {
            "symbol": symbol,
            "date": d,
            **bar,
            "amount": bar["close"] * bar["volume"],
            "quote_ts": None,
        }
        for symbol in SYMBOLS
        for d, bar in zip(days, RAW[symbol], strict=True)
        if d <= through
    ]
    return pl.DataFrame(rows, schema_overrides={"quote_ts": pl.Int64})


def _publish(root: Path, days: list[date], through: date) -> None:
    daily = TaiwanDailyStore(data_dir=root / "taiwan" / "daily")
    daily.write_batch(_daily(days, through))
    actions = TaiwanCorporateActionStore(data_dir=root / "taiwan" / "corporate_actions")
    actions.upsert(_events(days))
    TaiwanEnrichedRefreshService(
        daily_store=daily,
        enriched_root=root / "kline_daily_enriched",
        share_capital=MagicMock(as_instruments=MagicMock(return_value=pl.DataFrame())),
        corporate_actions=actions,
    ).refresh()


def _quote_extra(symbol: str) -> dict:
    """What the quote provider adds: raw prev_close (the reference price on an event day) and decimal ratios."""
    bar = RAW[symbol][-1]
    prev = (
        _reference_price(symbol) if EVENT_AT[symbol] == SESSIONS - 1 else RAW[symbol][-2]["close"]
    )
    return {
        "symbol": symbol,
        "prev_close": prev,
        "change_pct": bar["close"] / prev - 1,
        "change_amount": bar["close"] - prev,
        "amplitude": (bar["high"] - bar["low"]) / prev,
    }


def _intraday(
    root: Path, days: list[date], mp: pytest.MonkeyPatch
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The live row of the last session and the previous row it was compared with."""
    from app.config import settings

    today = days[-1]
    mp.setattr(settings, "data_dir", root)
    mp.setattr(quote_service, "cn_today", lambda: today)
    repo = KlineRepository(DataStore(root))
    repo._refresh_enriched()
    assert repo.get_live_agg_date() == days[-2]
    live_agg = repo.get_live_agg()
    prev_enriched, prev_date = repo.get_enriched_latest()
    assert prev_date == days[-2]

    closing = int(
        datetime(today.year, today.month, today.day, 13, 30, tzinfo=TAIPEI).timestamp() * 1000
    )
    bars = (
        _daily(days, today)
        .filter(pl.col("date") == today)
        .with_columns(pl.lit(closing).alias("quote_ts"))
    )
    quote_extra = pl.DataFrame([_quote_extra(symbol) for symbol in SYMBOLS])
    service = SimpleNamespace(_repo=repo)
    live = QuoteService._enrich_today_incremental(
        service, bars, quote_extra, live_agg, prev_enriched
    )
    return live, prev_enriched


_SCENARIOS: dict[str, dict] = {}


@pytest.fixture
def scenario(deny_network, tmp_path_factory, request) -> dict:
    """batch, live and prev rows (sorted by symbol), the published history and the sessions."""
    name = request.param
    if name not in _SCENARIOS:
        days = _sessions(CLOSURE if name == "closure" else frozenset())
        assert days[-2] - days[0] > timedelta(days=CACHE_DAYS)
        root = tmp_path_factory.mktemp(f"stage6_intraday_{name}")
        master_path = root / "security_master.parquet"
        pl.DataFrame([_instrument(s).to_dict() for s in SYMBOLS]).write_parquet(master_path)
        master = TaiwanSecurityMaster(cache_path=master_path)
        assert master.load_cache()
        with pytest.MonkeyPatch.context() as mp:
            from app.config import settings

            mp.setattr(universe, "_default_master", master)
            invalidate_taiwan_limit_profile_cache()
            invalidate_custom_signals()
            try:
                mp.setattr(settings, "data_dir", root / "batch")
                _publish(root / "batch", days, days[-1])
                _publish(root / "live", days, days[-2])
                live, prev = _intraday(root / "live", days, mp)
            finally:
                invalidate_taiwan_limit_profile_cache()
                invalidate_custom_signals()
        published = pl.read_parquet(root / "batch" / "kline_daily_enriched" / "**" / "*.parquet")
        _SCENARIOS[name] = {
            "batch": published.filter(pl.col("date") == days[-1]).sort("symbol"),
            "live": live.sort("symbol"),
            "prev": prev.sort("symbol"),
            "history": published.sort(["symbol", "date"]),
            "days": days,
        }
    return _SCENARIOS[name]


COMPARED = sorted(
    {"open", "high", "low", "close", "volume", "ex_rights"}
    | set(INDICATOR_COLUMNS)
    | set(SIGNAL_DEPENDENCIES)
    | LIMIT_SIGNAL_OUTPUTS
)


@pytest.mark.parametrize(
    ("scenario", "column"),
    [pytest.param(name, c, id=f"{name}-{c}") for name in ("events", "closure") for c in COMPARED],
    indirect=["scenario"],
)
def test_the_intraday_row_equals_the_published_row(scenario, column):
    batch, live = scenario["batch"], scenario["live"]
    assert batch["symbol"].to_list() == live["symbol"].to_list() == sorted(SYMBOLS)
    assert column in live.columns
    expected, got = batch[column].to_list(), live[column].to_list()
    if batch[column].dtype == pl.Boolean:
        assert got == expected
    else:
        assert got == pytest.approx(expected, rel=1e-9, abs=1e-9)


def _cross(a: float, b: float, prev_a: float, prev_b: float, up: bool) -> bool:
    return (a > b and prev_a <= prev_b) if up else (a < b and prev_a >= prev_b)


@pytest.mark.parametrize("scenario", ["events", "closure"], indirect=True)
def test_intraday_signals_follow_the_intraday_indicators(scenario):
    for symbol in SYMBOLS:
        row = scenario["live"].filter(pl.col("symbol") == symbol).row(0, named=True)
        prev = (
            scenario["history"]
            .filter((pl.col("symbol") == symbol) & (pl.col("date") == scenario["days"][-2]))
            .row(0, named=True)
        )
        expected = {
            "signal_n_day_high": row["close"] >= row["high_60d"],
            "signal_n_day_low": row["close"] <= row["low_60d"],
            "signal_boll_breakout_upper": row["close"] > row["boll_upper"],
            "signal_boll_breakdown_lower": row["close"] < row["boll_lower"],
            "signal_volume_surge": row["vol_ratio_5d"] >= 2.0,
        }
        for signal, (a, b, up) in {
            "signal_ma_golden_5_20": ("ma5", "ma20", True),
            "signal_ma_dead_5_20": ("ma5", "ma20", False),
            "signal_ma_golden_20_60": ("ma20", "ma60", True),
            "signal_macd_golden": ("macd_dif", "macd_dea", True),
            "signal_macd_dead": ("macd_dif", "macd_dea", False),
            "signal_ma20_breakout": ("close", "ma20", True),
            "signal_ma20_breakdown": ("close", "ma20", False),
            "signal_ma5_breakout": ("close", "ma5", True),
            "signal_ma5_breakdown": ("close", "ma5", False),
            "signal_ma10_breakout": ("close", "ma10", True),
            "signal_ma10_breakdown": ("close", "ma10", False),
        }.items():
            expected[signal] = _cross(row[a], row[b], prev[a], prev[b], up)
        assert {s: row[s] for s in expected} == expected, symbol


@pytest.mark.parametrize("scenario", ["events"], indirect=True)
def test_the_event_session_is_on_the_traded_scale_in_both_paths(scenario):
    for frame in (scenario["batch"], scenario["live"]):
        row = frame.filter(pl.col("symbol") == DIVIDEND).row(0, named=True)
        assert row["close"] == pytest.approx(RAW[DIVIDEND][-1]["close"], rel=1e-12)
        assert row["ex_rights"] is True
        assert row["change_pct"] == pytest.approx(
            DIVIDEND_FACTOR * RAW[DIVIDEND][-1]["close"] / RAW[DIVIDEND][-2]["close"] - 1
        )
