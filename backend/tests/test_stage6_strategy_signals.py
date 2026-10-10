"""Strategies on the published Taiwan enriched table do not mistake an ex-dividend gap for a fall.

A dividend gap must not fire an MA20 breakdown or a dead cross or turn momentum negative, and a
large ex-rights gap must not look like a 60-day low with an oversold RSI. Each case runs through both strategy paths on rows
published by TaiwanEnrichedRefreshService.refresh:
- matrix: the builtin strategy, whose features are recomputed from the published close;
- polars: a polars_expr strategy with the same rule over the published indicator and signal columns.
The expected answer comes from a plain reference computation on the forward-adjusted series.
Each symbol has a twin with the same printed bars and no corporate action, published unadjusted:
both paths must give the twin the raw-price answer, so a lost adjustment cannot pass unnoticed.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.indicators.pipeline import invalidate_custom_signals, invalidate_taiwan_limit_profile_cache
from app.strategy.engine import StrategyDataContext, StrategyEngine
from app.taiwan import universe
from app.taiwan.corporate_actions import TaiwanCorporateActionStore, corporate_action_schema
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.enrichment.corporate_actions import EXDIV
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "app" / "strategy" / "builtin"
TREND = "2330.TWSE"
MOMENTUM = "2454.TWSE"
PULLBACK = "2412.TWSE"
REVERSAL = "6669.TWSE"
RECLAIM = "3008.TWSE"
UNADJUSTED = {
    TREND: "2331.TWSE",
    MOMENTUM: "2455.TWSE",
    PULLBACK: "2413.TWSE",
    REVERSAL: "6670.TWSE",
    RECLAIM: "3009.TWSE",
}
SESSIONS = 200
DIVIDEND_AT = SESSIONS - 3
DIVIDEND_FACTOR = 1.10
EX_RIGHTS_AT = SESSIONS - 8
EX_RIGHTS_FACTOR = 2.98
BOUNCE_AT = SESSIONS - 1


def _sessions() -> list[date]:
    out, day = [], date(2025, 7, 1)
    while len(out) < SESSIONS:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


DAYS = _sessions()


def _bars(closes: list[float], volumes: list[float]) -> list[dict]:
    """Bars whose open is the previous close, so the adjusted series has no gaps."""
    bars = []
    for t, (close, volume) in enumerate(zip(closes, volumes, strict=True)):
        open_ = close if t == 0 else closes[t - 1]
        bars.append(
            {
                "open": open_,
                "high": max(open_, close) + 0.5,
                "low": min(open_, close) - 0.5,
                "close": close,
                "volume": volume,
            }
        )
    return bars


def _trend() -> list[dict]:
    closes = [100 + 0.25 * t + 1.2 * math.sin(t / 3) for t in range(SESSIONS)]
    return _bars(closes, [2_000_000.0 * (1 + 0.2 * math.sin(t / 5)) for t in range(SESSIONS)])


def _pullback() -> list[dict]:
    """The trend, then a quiet pullback to MA20 after the dividend and an up day off it."""
    closes = [100 + 0.25 * t + 1.2 * math.sin(t / 3) for t in range(SESSIONS)]
    closes[DIVIDEND_AT] = closes[DIVIDEND_AT - 1] - 1.5
    closes[DIVIDEND_AT + 1] = closes[DIVIDEND_AT] - 1.0
    closes[DIVIDEND_AT + 2] = closes[DIVIDEND_AT + 1] + 0.4
    volumes = [2_000_000.0 * (1 + 0.2 * math.sin(t / 5)) for t in range(SESSIONS)]
    volumes[DIVIDEND_AT + 1 :] = [v * 0.5 for v in volumes[DIVIDEND_AT + 1 :]]
    return _bars(closes, volumes)


def _momentum() -> list[dict]:
    """Sideways under MA20, then a breakout on volume on the ex-dividend session."""
    closes = [200 + 2 * math.sin(t / 4) for t in range(SESSIONS)]
    closes[DIVIDEND_AT - 1] = 197.0
    for t in range(DIVIDEND_AT, SESSIONS):
        closes[t] = 204.0 + 0.5 * (t - DIVIDEND_AT)
    volumes = [2_000_000.0 * (3.0 if t == DIVIDEND_AT else 1.0) for t in range(SESSIONS)]
    return _bars(closes, volumes)


def _reversal() -> list[dict]:
    closes, volumes = [], []
    for t in range(SESSIONS):
        close = 300 + 6 * math.sin(t / 5) + 3 * math.sin(t / 11)
        if t == EX_RIGHTS_AT:
            close = closes[-1] + 2.0
        if t == BOUNCE_AT:
            close = closes[-1] * 1.02
        closes.append(close)
        volumes.append(2_000_000.0 * (3.0 if t == EX_RIGHTS_AT else 1.0))
    return _bars(closes, volumes)


def _reclaim() -> list[dict]:
    """The reversal wave; on the ex-rights session the low undercuts the 60-day low and the
    close recovers above it on a bullish bar with three times the volume."""
    closes = [300 + 6 * math.sin(t / 5) + 3 * math.sin(t / 11) for t in range(SESSIONS)]
    volumes = [2_000_000.0 * (3.0 if t == EX_RIGHTS_AT else 1.0) for t in range(SESSIONS)]
    prior_low = min(bar["low"] for bar in _bars(closes, volumes)[EX_RIGHTS_AT - 60 : EX_RIGHTS_AT])
    closes[EX_RIGHTS_AT] = prior_low + 4.0
    bars = _bars(closes, volumes)
    bars[EX_RIGHTS_AT].update(
        open=prior_low + 0.5, low=prior_low - 3.0, high=closes[EX_RIGHTS_AT] + 0.5
    )
    return bars


ADJUSTED = {
    TREND: _trend(),
    MOMENTUM: _momentum(),
    PULLBACK: _pullback(),
    REVERSAL: _reversal(),
    RECLAIM: _reclaim(),
}
EVENT = {
    TREND: (DIVIDEND_AT, DIVIDEND_FACTOR),
    MOMENTUM: (DIVIDEND_AT, DIVIDEND_FACTOR),
    PULLBACK: (DIVIDEND_AT, DIVIDEND_FACTOR),
    REVERSAL: (EX_RIGHTS_AT, EX_RIGHTS_FACTOR),
    RECLAIM: (EX_RIGHTS_AT, EX_RIGHTS_FACTOR),
}


def _raw(symbol: str) -> list[dict]:
    at, factor = EVENT[symbol]
    return [
        {**bar, **{k: bar[k] * factor for k in ("open", "high", "low", "close")}}
        if t < at
        else dict(bar)
        for t, bar in enumerate(ADJUSTED[symbol])
    ]


RAW = {symbol: _raw(symbol) for symbol in ADJUSTED}


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


def _event(symbol: str) -> dict:
    at, factor = EVENT[symbol]
    pre = RAW[symbol][at - 1]["close"]
    ref = pre / factor
    return {
        "symbol": symbol,
        "date": DAYS[at],
        "kind": EXDIV,
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


POLARS_STRATEGIES = {
    "stage6_trend.py": """
import polars as pl

META = {"id": "stage6_trend", "name": "trend mirror", "asset_types": ["stock"], "limit": 0}
EXIT_SIGNALS = ["signal_ma_dead_5_20", "signal_ma20_breakdown"]


def filter(df, params):
    return (
        (pl.col("ma5") > pl.col("ma10")) & (pl.col("ma10") > pl.col("ma20")) & (pl.col("ma20") > pl.col("ma60"))
        & (pl.col("momentum_20d") > 0)
    )
""",
    "stage6_momentum.py": """
import polars as pl

META = {"id": "stage6_momentum", "name": "momentum mirror", "asset_types": ["stock"], "limit": 0}
EXIT_SIGNALS = ["signal_ma20_breakdown"]


def filter(df, params):
    return pl.col("signal_ma20_breakout") & (pl.col("vol_ratio_5d") >= 2.0) & (pl.col("close") > pl.col("open"))
""",
    "stage6_pullback.py": """
import polars as pl

META = {"id": "stage6_pullback", "name": "pullback mirror", "asset_types": ["stock"], "limit": 0}
EXIT_SIGNALS = ["signal_ma20_breakdown", "signal_ma_dead_5_20"]


def filter(df, params):
    return (
        (pl.col("close") > pl.col("ma20") * 0.98) & (pl.col("close") < pl.col("ma20") * 1.02)
        & (pl.col("ma5") > pl.col("ma20")) & (pl.col("ma20") > pl.col("ma60")) & (pl.col("change_pct") > 0)
    )
""",
    "stage6_new_low.py": """
import polars as pl

META = {"id": "stage6_new_low", "name": "new low mirror", "asset_types": ["stock"], "limit": 0}


def filter(df, params):
    return pl.col("signal_n_day_low") & (pl.col("close") > pl.col("open")) & (pl.col("vol_ratio_5d") >= 1.5)
""",
    "stage6_oversold.py": """
import polars as pl

META = {"id": "stage6_oversold", "name": "oversold mirror", "asset_types": ["stock"], "limit": 0}


def filter(df, params):
    return (pl.col("rsi_14") < 30) & (pl.col("change_pct") > 0.01) & (pl.col("close") > pl.col("ma5"))
""",
}

_STATE: dict[str, object] = {}


@pytest.fixture
def world(deny_network, tmp_path_factory) -> tuple[pl.DataFrame, StrategyEngine]:
    """Published rows of both symbols and an engine with the builtin and the polars strategies."""
    if "published" not in _STATE:
        root = tmp_path_factory.mktemp("stage6_strategies")
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
                        [_event(symbol) for symbol in RAW], schema=corporate_action_schema()
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
        _STATE["published"] = pl.read_parquet(
            root / "kline_daily_enriched" / "**" / "*.parquet"
        ).sort(["symbol", "date"])
        polars_dir = root / "polars_strategies"
        polars_dir.mkdir()
        for name, source in POLARS_STRATEGIES.items():
            (polars_dir / name).write_text(source, encoding="utf-8")
        _STATE["engine"] = StrategyEngine(strategy_dirs=[BUILTIN_DIR, polars_dir])
    engine = _STATE["engine"]
    assert not engine.load_errors(), engine.load_errors()
    return _STATE["published"], engine


def _run(world, strategy_id: str, as_of: date):
    published, engine = world
    history = published.filter(pl.col("date") <= as_of)
    context = StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=as_of,
        current=history.filter(pl.col("date") == as_of),
        history=history,
    )
    return engine.run(strategy_id, context)


def _picked(result) -> set[str]:
    return {str(row["symbol"]) for row in result.rows}


def _exits(result) -> set[str]:
    return {hit["symbol"] for hit in result.exit_signal_hits}


# ---- reference -----------------------------------------------------------------------------


def _sma(xs: list[float], n: int, t: int) -> float:
    return sum(xs[t + 1 - n : t + 1]) / n


def _rsi14(closes: list[float], t: int) -> float:
    """Wilder RSI(14) at t, seeded with the average of the first 14 changes."""
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, t + 1)]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, t + 1)]
    avg_gain, avg_loss = sum(gains[:14]) / 14, sum(losses[:14]) / 14
    for g, lo in zip(gains[14:], losses[14:], strict=True):
        avg_gain, avg_loss = (avg_gain * 13 + g) / 14, (avg_loss * 13 + lo) / 14
    return 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)


def _closes(bars: list[dict]) -> list[float]:
    return [b["close"] for b in bars]


def _aligned(bars: list[dict], t: int) -> bool:
    c = _closes(bars)
    ma5, ma10, ma20, ma60 = (_sma(c, n, t) for n in (5, 10, 20, 60))
    return ma5 > ma10 > ma20 > ma60 and c[t] / c[t - 20] - 1 > 0


def _ma20_breakdown(bars: list[dict], t: int) -> bool:
    c = _closes(bars)
    return c[t] < _sma(c, 20, t) and c[t - 1] >= _sma(c, 20, t - 1)


def _ma_dead(bars: list[dict], t: int) -> bool:
    c = _closes(bars)
    return _sma(c, 5, t) < _sma(c, 20, t) and _sma(c, 5, t - 1) >= _sma(c, 20, t - 1)


def _breakout_entry(bars: list[dict], t: int) -> bool:
    c, v = _closes(bars), [b["volume"] for b in bars]
    breakout = c[t] > _sma(c, 20, t) and c[t - 1] <= _sma(c, 20, t - 1)
    return breakout and v[t] / (sum(v[t - 5 : t]) / 5) >= 2.0 and c[t] > bars[t]["open"]


def _ma20_bounce_entry(bars: list[dict], t: int) -> bool:
    c = _closes(bars)
    ma5, ma20, ma60 = (_sma(c, n, t) for n in (5, 20, 60))
    return ma20 * 0.98 < c[t] < ma20 * 1.02 and ma5 > ma20 > ma60 and c[t] > c[t - 1]


def _support_entry(bars: list[dict], t: int) -> bool:
    c, v = _closes(bars), [b["volume"] for b in bars]
    ma20 = _sma(c, 20, t)
    return (
        ma20 <= c[t] < ma20 * 1.02
        and v[t] / (sum(v[t - 5 : t]) / 5) < 0.8
        and c[t] > _sma(c, 60, t)
        and c[t] / c[t - 20] - 1 > 0
    )


def _closing_new_low_entry(bars: list[dict], t: int) -> bool:
    """The polars mirror's rule over the published signal_n_day_low (close at its 60-day low)."""
    c, v = _closes(bars), [b["volume"] for b in bars]
    return (
        c[t] <= min(c[t - 59 : t + 1])
        and c[t] > bars[t]["open"]
        and v[t] / (sum(v[t - 5 : t]) / 5) >= 1.5
    )


def _new_low_entry(bars: list[dict], t: int) -> bool:
    """n_day_low_reversal: the low breaks the prior 60-day low and the close recovers above it."""
    c, v = _closes(bars), [b["volume"] for b in bars]
    prior_low = min(b["low"] for b in bars[t - 60 : t])
    return (
        bars[t]["low"] < prior_low < c[t]
        and c[t] > bars[t]["open"]
        and v[t] / (sum(v[t - 5 : t]) / 5) >= 1.5
    )


def _oversold_entry(bars: list[dict], t: int) -> bool:
    c = _closes(bars)
    return _rsi14(c, t) < 30 and c[t] / c[t - 1] - 1 > 0.01 and c[t] > _sma(c, 5, t)


TREND_DAYS = range(DIVIDEND_AT, SESSIONS)


def test_each_fixture_fires_the_false_signal_on_raw_prices():
    raw, adjusted = RAW[TREND], ADJUSTED[TREND]
    assert _ma20_breakdown(raw, DIVIDEND_AT) and not _ma20_breakdown(adjusted, DIVIDEND_AT)
    assert not _aligned(raw, DIVIDEND_AT) and all(_aligned(adjusted, t) for t in TREND_DAYS)
    assert _closes(raw)[DIVIDEND_AT] / _closes(raw)[DIVIDEND_AT - 20] - 1 < 0
    assert _breakout_entry(ADJUSTED[MOMENTUM], DIVIDEND_AT)
    assert not _breakout_entry(RAW[MOMENTUM], DIVIDEND_AT)
    raw, adjusted = RAW[PULLBACK], ADJUSTED[PULLBACK]
    assert any(
        _ma20_bounce_entry(adjusted, t) and not _ma20_bounce_entry(raw, t) for t in TREND_DAYS
    )
    assert any(_support_entry(adjusted, t) and not _support_entry(raw, t) for t in TREND_DAYS)
    raw, adjusted = RAW[REVERSAL], ADJUSTED[REVERSAL]
    assert _closing_new_low_entry(raw, EX_RIGHTS_AT)
    assert not _closing_new_low_entry(adjusted, EX_RIGHTS_AT)
    assert _oversold_entry(raw, BOUNCE_AT) and not _oversold_entry(adjusted, BOUNCE_AT)
    assert _new_low_entry(ADJUSTED[RECLAIM], EX_RIGHTS_AT)
    assert not _new_low_entry(RAW[RECLAIM], EX_RIGHTS_AT)


@pytest.mark.parametrize("strategy_id", ["bullish_alignment", "stage6_trend"])
@pytest.mark.parametrize("t", TREND_DAYS)
def test_trend_strategies_keep_the_entry_and_fire_no_exit_across_the_dividend(
    world, strategy_id, t
):
    result = _run(world, strategy_id, DAYS[t])
    for symbol, bars in ((TREND, ADJUSTED[TREND]), (UNADJUSTED[TREND], RAW[TREND])):
        assert (symbol in _picked(result)) is _aligned(bars, t), symbol
        assert (symbol in _exits(result)) is (_ma20_breakdown(bars, t) or _ma_dead(bars, t)), symbol


@pytest.mark.parametrize("strategy_id", ["volume_price_surge", "stage6_momentum"])
@pytest.mark.parametrize("t", TREND_DAYS)
def test_momentum_strategies_keep_the_breakout_and_fire_no_breakdown_across_the_dividend(
    world, strategy_id, t
):
    """The adjusted breakout on volume is picked; the unadjusted twin misses it."""
    result = _run(world, strategy_id, DAYS[t])
    for symbol in (TREND, MOMENTUM):
        for published_as, bars in ((symbol, ADJUSTED[symbol]), (UNADJUSTED[symbol], RAW[symbol])):
            assert (published_as in _picked(result)) is _breakout_entry(bars, t), published_as
            assert (published_as in _exits(result)) is _ma20_breakdown(bars, t), published_as


PULLBACK_RULES = {
    "pullback_ma20_bounce": (_ma20_bounce_entry, True),
    "stage6_pullback": (_ma20_bounce_entry, True),
    "pullback_to_support": (_support_entry, False),
}


@pytest.mark.parametrize("strategy_id", sorted(PULLBACK_RULES))
@pytest.mark.parametrize("t", TREND_DAYS)
def test_pullback_strategies_follow_the_adjusted_series_after_the_dividend(world, strategy_id, t):
    """A pullback to MA20 after the dividend is picked; the unadjusted twin misses it."""
    entry, exits_on_dead_cross = PULLBACK_RULES[strategy_id]
    result = _run(world, strategy_id, DAYS[t])
    for symbol, bars in ((PULLBACK, ADJUSTED[PULLBACK]), (UNADJUSTED[PULLBACK], RAW[PULLBACK])):
        assert (symbol in _picked(result)) is entry(bars, t), symbol
        exit_expected = _ma20_breakdown(bars, t) or (exits_on_dead_cross and _ma_dead(bars, t))
        assert (symbol in _exits(result)) is exit_expected, symbol


def test_published_momentum_stays_positive_across_the_dividend(world):
    published, _ = world
    rows = published.filter(
        (pl.col("symbol") == TREND) & (pl.col("date") >= DAYS[DIVIDEND_AT])
    ).sort("date")
    c = _closes(ADJUSTED[TREND])
    for n in (5, 10, 20, 60):
        expected = [c[t] / c[t - n] - 1 for t in TREND_DAYS]
        assert rows[f"momentum_{n}d"].to_list() == pytest.approx(expected, rel=1e-9)
    assert all(
        value > 0 for value in rows["momentum_20d"].to_list() + rows["momentum_60d"].to_list()
    )


@pytest.mark.parametrize(
    ("strategy_id", "entry"),
    [("n_day_low_reversal", _new_low_entry), ("stage6_new_low", _closing_new_low_entry)],
    ids=["n_day_low_reversal", "stage6_new_low"],
)
def test_reversal_strategies_see_no_60_day_low_on_the_ex_rights_session(world, strategy_id, entry):
    """Each path follows its own rule on the adjusted series; the unadjusted twin follows raw prices."""
    result = _run(world, strategy_id, DAYS[EX_RIGHTS_AT])
    assert REVERSAL not in _picked(result)
    for symbol, bars in ((REVERSAL, ADJUSTED[REVERSAL]), (UNADJUSTED[REVERSAL], RAW[REVERSAL])):
        assert (symbol in _picked(result)) is entry(bars, EX_RIGHTS_AT), symbol


def test_n_day_low_reversal_takes_the_adjusted_reclaim_and_not_the_raw_twin(world):
    """On raw prices the ex-rights session closes far below every earlier low, so it never recovers."""
    result = _run(world, "n_day_low_reversal", DAYS[EX_RIGHTS_AT])
    assert RECLAIM in _picked(result)
    assert UNADJUSTED[RECLAIM] not in _picked(result)


@pytest.mark.parametrize("strategy_id", ["oversold_reversal", "stage6_oversold"])
def test_reversal_strategies_see_no_oversold_bounce_after_the_ex_rights_gap(world, strategy_id):
    result = _run(world, strategy_id, DAYS[BOUNCE_AT])
    assert REVERSAL not in _picked(result)
    assert UNADJUSTED[REVERSAL] in _picked(result)
