"""Key price levels read forward-adjusted bars, so a split or dividend leaves no stale level or gap.

The levels come from GET /api/stock-analysis/levels over a repository reading the partitions
published by TaiwanEnrichedRefreshService.refresh. Each symbol has a twin with the same printed
bars and no corporate action; the twin shows the stale level and the gap, so the test would
notice a lost adjustment.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.api import stock_analysis
from app.indicators.pipeline import invalidate_custom_signals, invalidate_taiwan_limit_profile_cache
from app.repository import DataStore, KlineRepository
from app.taiwan import universe
from app.taiwan.corporate_actions import TaiwanCorporateActionStore, corporate_action_schema
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.enrichment.corporate_actions import ETF_SPLIT, EXDIV
from app.taiwan.market_rules import TickSizeClass, TickSizeModel
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

SPLIT = "0050.TWSE"
DIVIDEND = "1101.TWSE"
EX_RIGHTS = "6669.TWSE"
UNADJUSTED = {SPLIT: "0051.TWSE", DIVIDEND: "1102.TWSE", EX_RIGHTS: "6670.TWSE"}
SYMBOLS = [SPLIT, DIVIDEND, EX_RIGHTS]
SESSIONS = 300
# The dividend falls on the last session, so no later bar can fill or shrink the unadjusted twin's gap.
EVENT_AT = {SPLIT: SESSIONS - 40, DIVIDEND: SESSIONS - 1, EX_RIGHTS: SESSIONS - 60}
PRICE_FACTOR = {SPLIT: 4.0, DIVIDEND: 1.10, EX_RIGHTS: 2.98}
VOLUME_FACTOR = {SPLIT: 4.0, DIVIDEND: 1.0, EX_RIGHTS: 1.0}
DAYS_PARAM = 300


def _sessions() -> list[date]:
    out, day = [], date(2025, 3, 3)
    while len(out) < SESSIONS:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


DAYS = _sessions()
TODAY = DAYS[-1] + timedelta(days=1)


def _adjusted(base: float) -> list[dict]:
    """Bars whose open is the previous close: a continuous series has no price gap at all."""
    bars, prev_close = [], None
    for t in range(SESSIONS):
        close = base * (1 + 0.06 * math.sin(t / 7) + 0.03 * math.sin(t / 23)) + 0.01 * t
        open_ = close if prev_close is None else prev_close
        bars.append(
            {
                "open": open_,
                "high": max(open_, close) + 0.3,
                "low": min(open_, close) - 0.3,
                "close": close,
                "volume": 3_000_000.0 * (1 + 0.25 * math.sin(t / 4)),
            }
        )
        prev_close = close
    return bars


ADJUSTED = {SPLIT: _adjusted(50.0), DIVIDEND: _adjusted(60.0), EX_RIGHTS: _adjusted(80.0)}


def _raw(symbol: str) -> list[dict]:
    out = []
    for t, bar in enumerate(ADJUSTED[symbol]):
        p, v = (PRICE_FACTOR[symbol], VOLUME_FACTOR[symbol]) if t < EVENT_AT[symbol] else (1.0, 1.0)
        out.append(
            {
                **{k: bar[k] * p for k in ("open", "high", "low", "close")},
                "volume": bar["volume"] / v,
            }
        )
    return out


RAW = {symbol: _raw(symbol) for symbol in ADJUSTED}


def _instrument(symbol: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    etf = code.startswith("00")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=code,
        instrument_type="etf" if etf else "stock",
        listing_status="active",
        listing_date="2000-01-01",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="ETF" if etf else "股票",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-10-07",
        etf_category="equity" if etf else None,
        classification_source="official_metadata" if etf else None,
        underlying_scope="domestic" if etf else None,
    )


def _event(symbol: str) -> dict:
    at = EVENT_AT[symbol]
    pre = RAW[symbol][at - 1]["close"]
    ref = pre / PRICE_FACTOR[symbol]
    return {
        "symbol": symbol,
        "date": DAYS[at],
        "kind": ETF_SPLIT if symbol == SPLIT else EXDIV,
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
        "trade_date": DAYS[at],
        "status": "official",
    }


class _Today(date):
    @classmethod
    def today(cls) -> date:
        return TODAY


_STATE: dict[str, object] = {}


@pytest.fixture
def levels(deny_network, tmp_path_factory, monkeypatch):
    """Returns symbol -> the /levels response."""
    if "root" not in _STATE:
        root = tmp_path_factory.mktemp("stage6_levels")
        master_path = root / "security_master.parquet"
        pl.DataFrame(
            [_instrument(s).to_dict() for s in (*RAW, *UNADJUSTED.values())]
        ).write_parquet(master_path)
        master = TaiwanSecurityMaster(cache_path=master_path)
        assert master.load_cache()
        rows = [
            {
                "symbol": published_as,
                "date": DAYS[t],
                **bar,
                "amount": bar["close"] * bar["volume"],
                "quote_ts": None,
            }
            for symbol, bars in RAW.items()
            for published_as in (symbol, UNADJUSTED[symbol])
            for t, bar in enumerate(bars)
        ]
        with pytest.MonkeyPatch.context() as mp:
            from app.config import settings

            mp.setattr(settings, "data_dir", root)
            mp.setattr(universe, "_default_master", master)
            invalidate_taiwan_limit_profile_cache()
            invalidate_custom_signals()
            try:
                daily = TaiwanDailyStore(data_dir=root / "taiwan" / "daily")
                daily.write_batch(pl.DataFrame(rows, schema_overrides={"quote_ts": pl.Int64}))
                actions = TaiwanCorporateActionStore(data_dir=root / "taiwan" / "corporate_actions")
                actions.upsert(
                    pl.DataFrame(
                        [_event(symbol) for symbol in SYMBOLS], schema=corporate_action_schema()
                    )
                )
                TaiwanEnrichedRefreshService(
                    daily_store=daily,
                    enriched_root=root / "kline_daily_enriched",
                    share_capital=MagicMock(as_instruments=MagicMock(return_value=pl.DataFrame())),
                    corporate_actions=actions,
                ).refresh()
            finally:
                invalidate_taiwan_limit_profile_cache()
                invalidate_custom_signals()
        _STATE["root"] = root

    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", _STATE["root"])
    monkeypatch.setattr(stock_analysis, "date", _Today)
    request = MagicMock()
    request.app.state.repo = KlineRepository(DataStore(_STATE["root"]))

    def get(symbol: str) -> dict:
        return stock_analysis.get_levels(request, symbol=symbol, days=DAYS_PARAM)

    return get


def _window(bars: list[dict]) -> list[dict]:
    first = TODAY - timedelta(days=DAYS_PARAM * 2)
    return [bar for d, bar in zip(DAYS, bars, strict=True) if d >= first]


def _values(response: dict, group: str, label: str | None = None) -> list[float]:
    return [p["value"] for p in response["levels"][group] if label is None or p["label"] == label]


def _tick(value: float, mode: str) -> float:
    """The repository resolves every fixture symbol as a stock, and the newest bar is unadjusted."""
    return TickSizeModel.round_order_price(value, TickSizeClass.ORDINARY_STOCK, mode=mode)


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_the_requested_window_covers_the_event_and_250_sessions(symbol):
    window = _window(ADJUSTED[symbol])
    assert len(window) >= 250
    assert DAYS[EVENT_AT[symbol]] >= TODAY - timedelta(days=DAYS_PARAM * 2)


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_extreme_levels_are_the_adjusted_highs_and_lows(levels, symbol):
    response = levels(symbol)
    window = _window(ADJUSTED[symbol])
    assert response["close"] == pytest.approx(ADJUSTED[symbol][-1]["close"], rel=1e-12)
    for n in (60, 250):
        recent = window[-n:]
        assert _values(response, "extreme", f"{n}日新高") == [
            _tick(max(b["high"] for b in recent), "ceil")
        ]
        assert _values(response, "extreme", f"{n}日新低") == [
            _tick(min(b["low"] for b in recent), "floor")
        ]


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_chip_and_fibonacci_levels_stay_inside_the_adjusted_range(levels, symbol):
    """No level is built from the pre-event price scale."""
    response = levels(symbol)
    window = _window(ADJUSTED[symbol])
    low, high = min(b["low"] for b in window), max(b["high"] for b in window)
    for group in ("sr", "extreme", "fib"):
        values = _values(response, group)
        assert values, group
        assert all(_tick(low, "floor") <= v <= _tick(high, "ceil") for v in values), (
            group,
            values,
        )


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_the_unadjusted_twin_shows_the_stale_scale(levels, symbol):
    """Control: the same bars without the corporate action keep the pre-event high."""
    raw_high = _tick(max(b["high"] for b in _window(RAW[symbol])[-250:]), "ceil")
    adjusted_high = _tick(max(b["high"] for b in _window(ADJUSTED[symbol])[-250:]), "ceil")
    assert raw_high > adjusted_high * 1.05
    assert _values(levels(UNADJUSTED[symbol]), "extreme", "250日新高") == [raw_high]


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_an_ex_rights_drop_is_not_a_gap(levels, symbol):
    """The adjusted series never gaps, so no gap level is reported."""
    assert levels(symbol)["levels"]["gap"] == []


def test_the_unadjusted_twin_reports_the_dividend_drop_as_a_downward_gap(levels):
    """Control: the same bars without the corporate action report the drop as a downward gap."""
    raw, at = RAW[DIVIDEND], EVENT_AT[DIVIDEND]
    assert raw[at]["high"] < raw[at - 1]["low"]
    gaps = levels(UNADJUSTED[DIVIDEND])["levels"]["gap"]
    middle = (raw[at]["high"] + raw[at - 1]["low"]) / 2
    assert middle > raw[at]["close"] * 1.001
    assert [(p["label"], p["value"]) for p in gaps] == [("向下缺口", _tick(middle, "ceil"))]
