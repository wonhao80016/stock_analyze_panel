"""Published Taiwan indicators across a split and a dividend equal the textbook values of the adjusted series.

The raw bars carry a 1:4 split and an 8% cash dividend and the corporate-action table holds
both events; MA/EMA/MACD, KD/RSI, ATR, Bollinger, momentum and 60-day extremes must equal what a
plain reference computation gets from the continuous (forward-adjusted) series.

Reference formulas follow the published definitions: SMA; EMA and MACD 12/26/9 (StockCharts);
Wilder RSI and ATR seeded with the simple average of the first n values; Taiwan KD 9/3/3 with
K0 = D0 = 50 (MoneyDJ). The cross-event checks compare from WARM onward, where every seed has
decayed below the tolerance; the warm-up tests cover the first WARMUP sessions, where it has not.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.indicators.pipeline import (
    invalidate_custom_signals,
    invalidate_taiwan_limit_profile_cache,
)
from app.taiwan import universe
from app.taiwan.corporate_actions import TaiwanCorporateActionStore, corporate_action_schema
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.enrichment.corporate_actions import ETF_SPLIT, EXDIV
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

SPLIT = "0050.TWSE"
DIVIDEND = "1101.TWSE"
SESSIONS = 700
SPLIT_AT = 643
DIVIDEND_AT = 680
SPLIT_RATIO = 4.0
DIVIDEND_FACTOR = 1.08
WARM = 580


def _sessions(count: int) -> list[date]:
    out, day = [], date(2023, 7, 3)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


DAYS = _sessions(SESSIONS)


def _adjusted_bars(base: float) -> list[dict]:
    """The continuous series; each open is the previous close, so it never gaps."""
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


ADJUSTED = {SPLIT: _adjusted_bars(50.0), DIVIDEND: _adjusted_bars(60.0)}


def _raw_bars(symbol: str) -> list[dict]:
    """What the exchange printed: prices before the event scaled up, split volume scaled down."""
    at, price_factor, volume_factor = (
        (SPLIT_AT, SPLIT_RATIO, SPLIT_RATIO)
        if symbol == SPLIT
        else (DIVIDEND_AT, DIVIDEND_FACTOR, 1.0)
    )
    out = []
    for t, bar in enumerate(ADJUSTED[symbol]):
        p, v = (price_factor, volume_factor) if t < at else (1.0, 1.0)
        out.append(
            {
                "open": bar["open"] * p,
                "high": bar["high"] * p,
                "low": bar["low"] * p,
                "close": bar["close"] * p,
                "volume": bar["volume"] / v,
            }
        )
    return out


RAW = {symbol: _raw_bars(symbol) for symbol in ADJUSTED}


def _event(symbol: str) -> dict:
    at, kind = (SPLIT_AT, ETF_SPLIT) if symbol == SPLIT else (DIVIDEND_AT, EXDIV)
    pre = RAW[symbol][at - 1]["close"]
    ref = ADJUSTED[symbol][at - 1]["close"]
    return {
        "symbol": symbol,
        "date": DAYS[at],
        "kind": kind,
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


def _instrument(symbol: str, kind: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=code,
        instrument_type=kind,
        listing_status="active",
        listing_date="2000-01-01",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="ETF" if kind == "etf" else "股票",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-10-07",
        etf_category="equity" if kind == "etf" else None,
        classification_source="official_metadata" if kind == "etf" else None,
        underlying_scope="domestic" if kind == "etf" else None,
    )


def _frame(bars: dict[str, list[dict]]) -> pl.DataFrame:
    rows = [
        {
            "symbol": symbol,
            "date": DAYS[t],
            **bar,
            "amount": bar["close"] * bar["volume"],
            "quote_ts": None,
        }
        for symbol, series in bars.items()
        for t, bar in enumerate(series)
    ]
    return pl.DataFrame(rows, schema_overrides={"quote_ts": pl.Int64})


_PUBLISHED: dict[str, pl.DataFrame] = {}


@pytest.fixture
def published(deny_network, tmp_path_factory) -> pl.DataFrame:
    """Every published row, built once through TaiwanEnrichedRefreshService.refresh."""
    if "frame" not in _PUBLISHED:
        root = tmp_path_factory.mktemp("stage6_values")
        master_path = root / "security_master.parquet"
        pl.DataFrame(
            [_instrument(SPLIT, "etf").to_dict(), _instrument(DIVIDEND, "stock").to_dict()]
        ).write_parquet(master_path)
        master = TaiwanSecurityMaster(cache_path=master_path)
        assert master.load_cache()
        with pytest.MonkeyPatch.context() as mp:
            from app.config import settings

            mp.setattr(settings, "data_dir", root)
            mp.setattr(universe, "_default_master", master)
            invalidate_taiwan_limit_profile_cache()
            invalidate_custom_signals()
            try:
                daily = TaiwanDailyStore(data_dir=root / "taiwan" / "daily")
                daily.write_batch(_frame(RAW))
                actions = TaiwanCorporateActionStore(data_dir=root / "taiwan" / "corporate_actions")
                actions.upsert(
                    pl.DataFrame(
                        [_event(SPLIT), _event(DIVIDEND)], schema=corporate_action_schema()
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
        _PUBLISHED["frame"] = pl.read_parquet(
            Path(root) / "kline_daily_enriched" / "**" / "*.parquet"
        ).sort(["symbol", "date"])
    return _PUBLISHED["frame"]


def _column(frame: pl.DataFrame, symbol: str, name: str) -> list:
    return frame.filter(pl.col("symbol") == symbol).sort("date")[name].to_list()


# ---- reference formulas -------------------------------------------------------------------


def _sma(xs: list[float], n: int) -> list[float | None]:
    return [None if t + 1 < n else sum(xs[t + 1 - n : t + 1]) / n for t in range(len(xs))]


def _ema(xs: list[float | None], n: int) -> list[float | None]:
    """StockCharts EMA: the first value is the SMA of the first n inputs, then alpha = 2 / (n + 1)."""
    alpha = 2.0 / (n + 1)
    out: list[float | None] = [None] * len(xs)
    start = next(i for i, x in enumerate(xs) if x is not None)
    seed_end = start + n - 1
    out[seed_end] = sum(xs[start : seed_end + 1]) / n
    for t in range(seed_end + 1, len(xs)):
        out[t] = alpha * xs[t] + (1 - alpha) * out[t - 1]
    return out


def _wilder(xs: list[float], n: int, first: int) -> list[float | None]:
    """Wilder smoothing: the average of xs[first:first + n], then (prev * (n - 1) + x) / n."""
    out: list[float | None] = [None] * len(xs)
    seed = first + n - 1
    out[seed] = sum(xs[first : seed + 1]) / n
    for t in range(seed + 1, len(xs)):
        out[t] = (out[t - 1] * (n - 1) + xs[t]) / n
    return out


def _rsi(closes: list[float], n: int) -> list[float | None]:
    gains = [0.0] + [max(closes[t] - closes[t - 1], 0.0) for t in range(1, len(closes))]
    losses = [0.0] + [max(closes[t - 1] - closes[t], 0.0) for t in range(1, len(closes))]
    avg_gain, avg_loss = _wilder(gains, n, 1), _wilder(losses, n, 1)
    return [
        None if gain is None else 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
        for gain, loss in zip(avg_gain, avg_loss, strict=True)
    ]


def _kd(bars: list[dict]) -> tuple[list[float], list[float], list[float]]:
    k_prev = d_prev = 50.0
    ks, ds, js = [], [], []
    for t in range(len(bars)):
        if t < 8:
            ks.append(None)
            ds.append(None)
            js.append(None)
            continue
        window = bars[t - 8 : t + 1]
        low9, high9 = min(b["low"] for b in window), max(b["high"] for b in window)
        rsv = 100 * (bars[t]["close"] - low9) / (high9 - low9)
        k_prev = k_prev * 2 / 3 + rsv / 3
        d_prev = d_prev * 2 / 3 + k_prev / 3
        ks.append(k_prev)
        ds.append(d_prev)
        js.append(3 * k_prev - 2 * d_prev)
    return ks, ds, js


def _atr(bars: list[dict], n: int = 14) -> list[float | None]:
    tr = [bars[0]["high"] - bars[0]["low"]] + [
        max(
            bars[t]["high"] - bars[t]["low"],
            abs(bars[t]["high"] - bars[t - 1]["close"]),
            abs(bars[t]["low"] - bars[t - 1]["close"]),
        )
        for t in range(1, len(bars))
    ]
    return _wilder(tr, n, 1)


def _reference(bars: list[dict]) -> dict[str, list]:
    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]
    ref: dict[str, list] = {f"ma{n}": _sma(closes, n) for n in (5, 10, 20, 30, 60)}
    ref.update({f"ema{n}": _ema(closes, n) for n in (5, 10, 20, 30, 60)})
    ema12, ema26 = _ema(closes, 12), _ema(closes, 26)
    dif = [None if a is None or b is None else a - b for a, b in zip(ema12, ema26, strict=True)]
    dea = _ema(dif, 9)
    ref["macd_dif"], ref["macd_dea"] = dif, dea
    ref["macd_hist"] = [None if d is None else f - d for f, d in zip(dif, dea, strict=True)]
    for n in (6, 14, 24):
        ref[f"rsi_{n}"] = _rsi(closes, n)
    ref["kdj_k"], ref["kdj_d"], ref["kdj_j"] = _kd(bars)
    ref["atr_14"] = _atr(bars)
    ma20 = ref["ma20"]
    pstd = [
        None if t < 19 else statistics.pstdev(closes[t - 19 : t + 1]) for t in range(len(closes))
    ]
    ref["boll_upper"] = [None if m is None else m + 2 * s for m, s in zip(ma20, pstd, strict=True)]
    ref["boll_lower"] = [None if m is None else m - 2 * s for m, s in zip(ma20, pstd, strict=True)]
    for n in (5, 10, 20, 30, 60):
        ref[f"momentum_{n}d"] = [
            None if t < n else closes[t] / closes[t - n] - 1 for t in range(len(closes))
        ]
    ref["high_60d"] = [None if t < 59 else max(closes[t - 59 : t + 1]) for t in range(len(closes))]
    ref["low_60d"] = [None if t < 59 else min(closes[t - 59 : t + 1]) for t in range(len(closes))]
    ref["vol_ma5"], ref["vol_ma10"] = _sma(volumes, 5), _sma(volumes, 10)
    ref["vol_ratio_5d"] = [
        None if t < 5 else volumes[t] / (sum(volumes[t - 5 : t]) / 5) for t in range(len(volumes))
    ]
    returns = [None] + [closes[t] / closes[t - 1] - 1 for t in range(1, len(closes))]
    ref["change_pct"] = returns
    log_returns = [None] + [math.log(closes[t] / closes[t - 1]) for t in range(1, len(closes))]
    ref["annual_vol_20d"] = [
        None if t < 20 else statistics.stdev(log_returns[t - 19 : t + 1]) * math.sqrt(250)
        for t in range(len(closes))
    ]
    return ref


REFERENCE = {symbol: _reference(bars) for symbol, bars in ADJUSTED.items()}
RAW_REFERENCE = {symbol: _reference(bars) for symbol, bars in RAW.items()}

# Absolute tolerance per column, far above float noise and far below any scale error.
PRICE_COLUMNS = (
    [f"ma{n}" for n in (5, 10, 20, 30, 60)]
    + [f"ema{n}" for n in (5, 10, 20, 30, 60)]
    + [
        "atr_14",
        "high_60d",
        "low_60d",
    ]
)
RATIO_COLUMNS = [f"momentum_{n}d" for n in (5, 10, 20, 30, 60)] + [
    "vol_ratio_5d",
    "change_pct",
    "annual_vol_20d",
]
OSCILLATOR_COLUMNS = [
    "rsi_6",
    "rsi_14",
    "rsi_24",
    "kdj_k",
    "kdj_d",
    "kdj_j",
    "macd_dif",
    "macd_dea",
    "macd_hist",
]
VOLUME_COLUMNS = ["vol_ma5", "vol_ma10"]
TOLERANCE = {
    **dict.fromkeys(PRICE_COLUMNS, 1e-6),
    **dict.fromkeys(RATIO_COLUMNS, 1e-9),
    **dict.fromkeys(OSCILLATOR_COLUMNS, 1e-6),
    **dict.fromkeys(VOLUME_COLUMNS, 1e-3),
}


def _assert_matches(published: pl.DataFrame, symbol: str, name: str) -> None:
    got = _column(published, symbol, name)[WARM:]
    expected = REFERENCE[symbol][name][WARM:]
    assert len(got) == len(expected) == SESSIONS - WARM
    for offset, (g, e) in enumerate(zip(got, expected, strict=True)):
        assert g == pytest.approx(e, abs=TOLERANCE[name]), (symbol, name, DAYS[WARM + offset])


def test_the_fixture_puts_each_event_inside_every_compared_window():
    assert WARM + 60 <= SPLIT_AT < DIVIDEND_AT < SESSIONS
    for symbol in ADJUSTED:
        assert abs(RAW_REFERENCE[symbol]["ma60"][-1] - REFERENCE[symbol]["ma60"][-1]) > 1.0


@pytest.mark.parametrize("symbol", [SPLIT, DIVIDEND])
def test_published_prices_are_the_adjusted_series_and_raw_prices_stay_as_traded(published, symbol):
    for name in ("open", "high", "low", "close"):
        assert _column(published, symbol, name) == pytest.approx(
            [b[name] for b in ADJUSTED[symbol]], rel=1e-12
        )
    for name, field in (("raw_close", "close"), ("raw_high", "high"), ("raw_low", "low")):
        assert _column(published, symbol, name) == pytest.approx(
            [b[field] for b in RAW[symbol]], rel=1e-12
        )


def test_split_volume_is_restated_and_dividend_volume_is_not(published):
    assert _column(published, SPLIT, "volume") == pytest.approx(
        [b["volume"] for b in ADJUSTED[SPLIT]], rel=1e-12
    )
    assert _column(published, DIVIDEND, "volume") == pytest.approx(
        [b["volume"] for b in RAW[DIVIDEND]], rel=1e-12
    )


@pytest.mark.parametrize("symbol", [SPLIT, DIVIDEND])
@pytest.mark.parametrize(
    "name", [f"ma{n}" for n in (5, 10, 20, 30, 60)] + [f"ema{n}" for n in (5, 10, 20, 30, 60)]
)
def test_moving_averages_match_the_adjusted_series(published, symbol, name):
    _assert_matches(published, symbol, name)


@pytest.mark.parametrize("symbol", [SPLIT, DIVIDEND])
@pytest.mark.parametrize("name", ["macd_dif", "macd_dea"])
def test_macd_matches_the_adjusted_series(published, symbol, name):
    _assert_matches(published, symbol, name)


@pytest.mark.parametrize("symbol", [SPLIT, DIVIDEND])
@pytest.mark.parametrize("name", ["rsi_6", "rsi_14", "rsi_24", "kdj_k", "kdj_d", "kdj_j"])
def test_rsi_and_kd_match_the_adjusted_series(published, symbol, name):
    _assert_matches(published, symbol, name)


PRICE_SCALE_COLUMNS = ["atr_14", "high_60d", "low_60d", "change_pct"] + [
    f"momentum_{n}d" for n in (5, 10, 20, 30, 60)
]


@pytest.mark.parametrize(
    ("symbol", "name"),
    [(symbol, name) for symbol in (SPLIT, DIVIDEND) for name in PRICE_SCALE_COLUMNS]
    + [(SPLIT, name) for name in ("vol_ma5", "vol_ma10", "vol_ratio_5d")],
)
def test_volatility_momentum_and_volume_match_the_adjusted_series(published, symbol, name):
    _assert_matches(published, symbol, name)


def _from_warm(values: list) -> list:
    return values[WARM:]


@pytest.mark.parametrize("symbol", [SPLIT, DIVIDEND])
def test_macd_hist_keeps_the_doubled_convention(published, symbol):
    # Pins the current 2 * (DIF - DEA) of A02#4 (open); the corrected OSC is the xfail below.
    dif, dea = REFERENCE[symbol]["macd_dif"], REFERENCE[symbol]["macd_dea"]
    expected = [2 * (f - d) for f, d in zip(_from_warm(dif), _from_warm(dea), strict=True)]
    assert _from_warm(_column(published, symbol, "macd_hist")) == pytest.approx(expected, abs=1e-6)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="A02#4: macd_hist is 2 x (DIF - DEA), the Taiwan OSC is DIF - DEA",
)
@pytest.mark.parametrize("symbol", [SPLIT, DIVIDEND])
def test_macd_hist_is_dif_minus_dea(published, symbol):
    _assert_matches(published, symbol, "macd_hist")


def _simple_return_vol(closes: list[float], t: int) -> float:
    returns = [closes[i] / closes[i - 1] - 1 for i in range(t - 19, t + 1)]
    return statistics.stdev(returns) * math.sqrt(252)


@pytest.mark.parametrize("symbol", [SPLIT, DIVIDEND])
def test_annual_vol_keeps_the_declared_simple_return_convention(published, symbol):
    # Pins simple returns, ddof=1 and sqrt(252) of A04#8 / A09#10 (open); the corrected one is the xfail below.
    closes = [b["close"] for b in ADJUSTED[symbol]]
    expected = [_simple_return_vol(closes, t) for t in range(WARM, SESSIONS)]
    got = _from_warm(_column(published, symbol, "annual_vol_20d"))
    assert got == pytest.approx(expected, rel=1e-9)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "A04#8/A09#10: simple returns by sqrt(252), not log returns by sqrt(250); A09#10 suggests"
        " sqrt(242) and both accept documenting the current convention, so the target may change"
    ),
)
@pytest.mark.parametrize("symbol", [SPLIT, DIVIDEND])
def test_annual_vol_uses_log_returns_and_the_taiwan_session_count(published, symbol):
    _assert_matches(published, symbol, "annual_vol_20d")


def _sample_bands(closes: list[float]) -> tuple[list, list]:
    upper, lower = [], []
    for t in range(len(closes)):
        if t < 19:
            upper.append(None)
            lower.append(None)
            continue
        window = closes[t - 19 : t + 1]
        mid, sigma = sum(window) / 20, statistics.stdev(window)
        upper.append(mid + 2 * sigma)
        lower.append(mid - 2 * sigma)
    return upper, lower


@pytest.mark.parametrize("symbol", [SPLIT, DIVIDEND])
def test_bollinger_bands_follow_the_adjusted_series_with_the_sample_sigma(published, symbol):
    # Pins the sample sigma of A08#6 (RC30, open) on every session, the event and the warm-up included.
    upper, lower = _sample_bands([b["close"] for b in ADJUSTED[symbol]])
    for name, expected in (("boll_upper", upper), ("boll_lower", lower)):
        got = _column(published, symbol, name)
        assert got[:19] == [None] * 19, name
        assert got[19:] == pytest.approx(expected[19:], abs=1e-6), name


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="A08#6 (RC30): Bollinger bands use the sample standard deviation (ddof=1)",
)
@pytest.mark.parametrize("symbol", [SPLIT, DIVIDEND])
def test_bollinger_bands_match_the_population_definition(published, symbol):
    for name in ("boll_upper", "boll_lower"):
        got = _from_warm(_column(published, symbol, name))
        assert got == pytest.approx(_from_warm(REFERENCE[symbol][name]), abs=1e-6), name


# ---- warm-up: the first sessions of a listing ------------------------------------------------

WARMUP = 100
WARMUP_NULLS = {
    **{f"ma{n}": n - 1 for n in (5, 10, 20, 30, 60)},
    **{f"momentum_{n}d": n for n in (5, 10, 20, 30, 60)},
    "high_60d": 59,
    "low_60d": 59,
    "boll_upper": 19,
    "vol_ratio_5d": 5,
    "annual_vol_20d": 20,
    **{f"ema{n}": n - 1 for n in (5, 10, 20, 30, 60)},
    "macd_dif": 25,
    "macd_dea": 33,
    **{f"rsi_{n}": n for n in (6, 14, 24)},
    "kdj_k": 8,
    "kdj_d": 8,
    "kdj_j": 8,
    "atr_14": 14,
}
A02_6 = "A02#6: EMA/MACD have no warm-up threshold and seed at the first close"
A03_2 = "A03#2: RSI and KD seed at the first bar instead of Wilder's average and KD's 50"
A04_7 = "A04#7: ATR seeds at the first high - low instead of the 14-session average TR"
WARMUP_NULL_FINDINGS = {
    **dict.fromkeys([f"ema{n}" for n in (5, 10, 20, 30, 60)], A02_6),
    "macd_dif": A02_6,
    "macd_dea": A02_6,
    "atr_14": A04_7,
}
WARMUP_VALUE_FINDINGS = {
    **dict.fromkeys([f"ema{n}" for n in (5, 10, 20, 30, 60)] + ["macd_dif", "macd_dea"], A02_6),
    **dict.fromkeys([f"rsi_{n}" for n in (6, 14, 24)] + ["kdj_k", "kdj_d", "kdj_j"], A03_2),
    "atr_14": A04_7,
}


def _warmup_params(columns, findings: dict[str, str]) -> list:
    return [
        pytest.param(
            name,
            marks=[pytest.mark.xfail(strict=True, raises=AssertionError, reason=findings[name])]
            if name in findings
            else [],
            id=name,
        )
        for name in columns
    ]


@pytest.mark.parametrize("name", _warmup_params(WARMUP_NULLS, WARMUP_NULL_FINDINGS))
def test_warmup_sessions_are_null_until_the_definition_has_enough_bars(published, name):
    for symbol in (SPLIT, DIVIDEND):
        values = _column(published, symbol, name)
        nulls = WARMUP_NULLS[name]
        assert values[:nulls] == [None] * nulls, symbol
        assert values[nulls] is not None, symbol


@pytest.mark.parametrize(
    "name",
    _warmup_params(
        [f"ma{n}" for n in (5, 10, 20, 30, 60)] + sorted(WARMUP_VALUE_FINDINGS),
        WARMUP_VALUE_FINDINGS,
    ),
)
def test_warmup_values_follow_the_published_definition(published, name):
    for symbol in (SPLIT, DIVIDEND):
        got = _column(published, symbol, name)[:WARMUP]
        expected = REFERENCE[symbol][name][:WARMUP]
        first = next(t for t, value in enumerate(expected) if value is not None)
        assert got[first:] == pytest.approx(expected[first:], abs=TOLERANCE[name]), symbol


def _seeded_ewm(xs: list, alpha: float) -> list:
    """EWM seeded at the first non-null input, null before it, as the pipeline computes today."""
    out, prev = [], None
    for x in xs:
        if x is not None:
            prev = x if prev is None else alpha * x + (1 - alpha) * prev
        out.append(prev)
    return out


def _current_warmup(bars: list[dict]) -> dict[str, list]:
    closes = [b["close"] for b in bars]
    out: dict[str, list] = {
        f"ema{n}": _seeded_ewm(closes, 2 / (n + 1)) for n in (5, 10, 20, 30, 60)
    }
    dif = [
        a - b for a, b in zip(_seeded_ewm(closes, 2 / 13), _seeded_ewm(closes, 2 / 27), strict=True)
    ]
    out["macd_dif"], out["macd_dea"] = dif, _seeded_ewm(dif, 2 / 10)
    deltas = [0.0] + [closes[t] - closes[t - 1] for t in range(1, len(closes))]
    for n in (6, 14, 24):
        gain = _seeded_ewm([max(d, 0.0) for d in deltas], 1 / n)
        loss = _seeded_ewm([max(-d, 0.0) for d in deltas], 1 / n)
        out[f"rsi_{n}"] = [
            None if t < n else 100 - 100 / (1 + g / (lo if lo != 0 else 1e-12))
            for t, (g, lo) in enumerate(zip(gain, loss, strict=True))
        ]
    lows = [min(b["low"] for b in bars[t - 8 : t + 1]) for t in range(8, len(bars))]
    highs = [max(b["high"] for b in bars[t - 8 : t + 1]) for t in range(8, len(bars))]
    rsv = [None] * 8 + [
        100 * (bars[t]["close"] - lo) / (hi - lo)
        for t, lo, hi in zip(range(8, len(bars)), lows, highs, strict=True)
    ]
    k = _seeded_ewm(rsv, 1 / 3)
    d = _seeded_ewm(k, 1 / 3)
    out["kdj_k"], out["kdj_d"] = k, d
    out["kdj_j"] = [None if a is None else 3 * a - 2 * b for a, b in zip(k, d, strict=True)]
    tr = [bars[0]["high"] - bars[0]["low"]] + [
        max(
            bars[t]["high"] - bars[t]["low"],
            abs(bars[t]["high"] - closes[t - 1]),
            abs(bars[t]["low"] - closes[t - 1]),
        )
        for t in range(1, len(bars))
    ]
    out["atr_14"] = _seeded_ewm(tr, 1 / 14)
    return out


CURRENT_WARMUP = {symbol: _current_warmup(bars[:WARMUP]) for symbol, bars in ADJUSTED.items()}


@pytest.mark.parametrize("name", sorted(WARMUP_VALUE_FINDINGS))
def test_warmup_values_keep_their_known_seeding(published, name):
    # Pins today's seeding of A02#6 / A03#2 / A04#7 (open), the xfails above.
    for symbol in (SPLIT, DIVIDEND):
        got = _column(published, symbol, name)[:WARMUP]
        expected = CURRENT_WARMUP[symbol][name]
        for t, (g, e) in enumerate(zip(got, expected, strict=True)):
            if e is None:
                assert g is None, (symbol, t)
            else:
                assert g == pytest.approx(e, rel=1e-9, abs=1e-9), (symbol, t)


SIGNALS = {
    "signal_ma_golden_5_20": ("ma5", "ma20", "up"),
    "signal_ma_dead_5_20": ("ma5", "ma20", "down"),
    "signal_ma_golden_20_60": ("ma20", "ma60", "up"),
    "signal_macd_golden": ("macd_dif", "macd_dea", "up"),
    "signal_macd_dead": ("macd_dif", "macd_dea", "down"),
    "signal_ma20_breakout": ("close", "ma20", "up"),
    "signal_ma20_breakdown": ("close", "ma20", "down"),
    "signal_ma5_breakout": ("close", "ma5", "up"),
    "signal_ma5_breakdown": ("close", "ma5", "down"),
    "signal_ma10_breakout": ("close", "ma10", "up"),
    "signal_ma10_breakdown": ("close", "ma10", "down"),
}


def _crosses(ref: dict[str, list], closes: list[float], name: str) -> list[bool]:
    a_name, b_name, direction = SIGNALS[name]
    a = closes if a_name == "close" else ref[a_name]
    b = ref[b_name]
    out = []
    for t in range(WARM, len(closes)):
        if direction == "up":
            out.append(a[t] > b[t] and a[t - 1] <= b[t - 1])
        else:
            out.append(a[t] < b[t] and a[t - 1] >= b[t - 1])
    return out


def _reference_signals(
    symbol: str, reference: dict[str, list], bars: list[dict]
) -> dict[str, list[bool]]:
    closes = [b["close"] for b in bars]
    out = {name: _crosses(reference, closes, name) for name in SIGNALS}
    out["signal_n_day_low"] = [closes[t] <= reference["low_60d"][t] for t in range(WARM, SESSIONS)]
    out["signal_n_day_high"] = [
        closes[t] >= reference["high_60d"][t] for t in range(WARM, SESSIONS)
    ]
    return out


@pytest.mark.parametrize("symbol", [SPLIT, DIVIDEND])
def test_cross_and_extreme_signals_follow_the_adjusted_series(published, symbol):
    """No false breakdown, dead cross or 60-day low on the event session or after it."""
    expected = _reference_signals(symbol, REFERENCE[symbol], ADJUSTED[symbol])
    for name, flags in expected.items():
        assert _column(published, symbol, name)[WARM:] == flags, name


@pytest.mark.parametrize(
    ("symbol", "at", "false_signals"),
    [
        (SPLIT, SPLIT_AT, {"signal_ma5_breakdown", "signal_ma20_breakdown", "signal_n_day_low"}),
        (DIVIDEND, DIVIDEND_AT, {"signal_ma5_breakdown", "signal_ma20_breakdown"}),
    ],
)
def test_the_unadjusted_series_would_fire_false_signals_on_the_event_session(
    symbol, at, false_signals
):
    """Control: the fixture is sharp enough that a publish on raw prices would fail the test above."""
    raw = _reference_signals(symbol, RAW_REFERENCE[symbol], RAW[symbol])
    adjusted = _reference_signals(symbol, REFERENCE[symbol], ADJUSTED[symbol])
    fired = {
        name for name, flags in raw.items() if flags[at - WARM] and not adjusted[name][at - WARM]
    }
    assert false_signals <= fired
    assert RAW_REFERENCE[symbol]["rsi_14"][at] < REFERENCE[symbol]["rsi_14"][at] - 10
