"""lorentzian_knn classifies a bar from neighbours in a fixed window of the symbol's own bars.

Neighbours are sampled every sample_stride valid bars back from the newest labelled bar, inside
120 valid bars, and only bars whose normalised features all exist vote or get classified. A bar
is classified only when everything it reads lies within its declared warm-up of common sessions,
so its signal does not depend on how much history is loaded and the screener's short history
gives the backtest's signals.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.backtest.engine import SimResult
from app.backtest.matrix import (
    MarketDataMatrix,
    MatrixPipelineConfig,
    MatrixStrategyPipeline,
    build_market_data_matrix,
    matrix_feature,
)
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.strategy.builtin import lorentzian_knn
from app.strategy.engine import StrategyDataContext, StrategyEngine
from app.strategy.scoring import recursive_warmup_bars
from app.taiwan import daily_store, universe
from app.taiwan.market_rules import ListingSessions
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

BUILTIN_DIR = Path(lorentzian_knn.__file__).resolve().parent
STRATEGY = lorentzian_knn.MATRIX_STRATEGY
CANDIDATE_WINDOW = 120
NO_FILTER = MatrixPipelineConfig(
    basic_filter={"enabled": False},
    scoring={},
    order_by=None,
    descending=True,
)
# A neutral or missing prediction passes min_prediction 0, so any vote by a bar that cannot be
# classified shows up as an entry.
SENSITIVE = {"min_prediction": 0.0, "use_volatility_filter": False, "use_regime_filter": False}
SMALL = {"neighbors": 5, "label_horizon": 2, "sample_stride": 3, "norm_window": 30, **SENSITIVE}
# 120 is no multiple of 7: the oldest neighbour is still inside the last 120 bars.
ODD_STRIDE = {"label_horizon": 3, "sample_stride": 7, **SENSITIVE}
SYMBOLS = tuple(f"{1101 + index}.TWSE" for index in range(10))
GAPPED = "1107.TWSE"
FLAT = "1108.TWSE"
LONG_GAP = "1109.TWSE"
SPARSE = "1110.TWSE"


def _sessions(count: int) -> list[date]:
    out, day = [], date(2023, 1, 2)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _panel(sessions: int) -> pl.DataFrame:
    """Random walks in rising and falling regimes.

    GAPPED misses 35 of its last 200 sessions, fewer than the allowance; FLAT stops moving for
    70 sessions; LONG_GAP misses sessions 60-119; SPARSE trades every other session from 300 on.
    """
    rng = np.random.default_rng(20261009)
    days = _sessions(sessions)
    drift = np.array([0.004, 0.0, -0.006, 0.002])[(np.arange(sessions) // 30) % 4]
    frames = []
    for symbol in SYMBOLS:
        close = 50.0 * np.cumprod(1.0 + drift + rng.normal(0.0, 0.018, sessions))
        if symbol == FLAT:
            close[330:400] = close[329]
        previous = np.concatenate(([close[0]], close[:-1]))
        open_ = previous * (1.0 + rng.normal(0.0, 0.006, sessions))
        high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.008, sessions)))
        low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.008, sessions)))
        if symbol == FLAT:
            open_[330:400] = high[330:400] = low[330:400] = close[329]
        frame = pl.DataFrame(
            {
                "symbol": [symbol] * sessions,
                "name": [symbol[:4]] * sessions,
                "date": days,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "raw_close": close,
                "raw_high": high,
                "raw_low": low,
                "volume": rng.lognormal(14.0, 0.35, sessions),
                "amount": np.full(sessions, 1e9),
            }
        )
        if symbol == GAPPED:
            missing = set(days[-60:-30:2]) | set(days[-200:-180])
            frame = frame.filter(~pl.col("date").is_in(sorted(missing)))
        if symbol == LONG_GAP:
            frame = frame.filter(~pl.col("date").is_in(days[60:120]))
        if symbol == SPARSE:
            frame = frame.filter(~pl.col("date").is_in(days[300::2]))
        frames.append(frame)
    return pl.concat(frames).sort(["symbol", "date"])


def _run(panel: pl.DataFrame, params: dict):
    market = build_market_data_matrix(panel)
    return market, MatrixStrategyPipeline().run(STRATEGY, market, params, NO_FILTER)


def _reference_prediction(market: MarketDataMatrix, params: dict) -> np.ndarray:
    """k-NN vote over each symbol's own bars, written out bar by bar."""
    k = int(params.get("neighbors", 8))
    horizon = int(params.get("label_horizon", 4))
    stride = int(params.get("sample_stride", 4))
    window = int(params.get("norm_window", 60))
    offsets = range(horizon, CANDIDATE_WINDOW + 1, stride)
    take = min(k, len(offsets))
    warmup = STRATEGY.required_warmup_bars(params)
    own_span = warmup - lorentzian_knn.MISSED_SESSION_ALLOWANCE
    raw = np.stack([matrix_feature(market, name) for name in lorentzian_knn.FEATURE_NAMES], -1)
    prediction = np.full(market.shape, np.nan, dtype=np.float32)
    for asset in range(market.shape[1]):
        rows = np.flatnonzero(np.isfinite(market.close[:, asset]))
        own = raw[rows, asset, :]
        closes = market.close[rows, asset]
        sessions = market.session_ids[rows]
        features = np.full(own.shape, np.nan, dtype=np.float32)
        for position in range(window - 1, rows.size):
            span = own[position - window + 1 : position + 1]
            low, high = span.min(axis=0), span.max(axis=0)
            computable = np.isfinite(span).all(axis=0) & (high > low)
            with np.errstate(invalid="ignore", divide="ignore"):
                scaled = (own[position] - low) / (high - low)
            features[position, computable] = scaled[computable]
        usable = np.isfinite(features).all(axis=1)
        wide = features.astype(np.float64)
        for position in np.flatnonzero(usable):
            if position < own_span or sessions[position] - sessions[position - own_span] > warmup:
                continue
            candidates = np.array(
                [position - o for o in offsets if usable[position - o]], dtype=np.int64
            )
            if candidates.size < take:
                continue
            distance = np.zeros(candidates.size)
            for column in range(wide.shape[1]):
                distance += np.log1p(np.abs(wide[position, column] - wide[candidates, column]))
            nearest = candidates[np.argsort(distance, kind="stable")[:take]]
            votes = np.sign(closes[nearest + horizon] - closes[nearest])
            prediction[rows[position], asset] = votes.sum()
    return prediction


@pytest.mark.parametrize(
    "params", [{}, SMALL, ODD_STRIDE], ids=["defaults", "small_windows", "odd_stride"]
)
def test_signals_do_not_depend_on_how_much_history_is_loaded(params):
    panel = _panel(500)
    days = panel["date"].unique().sort().to_list()
    loaded = STRATEGY.required_warmup_bars(params) + 1
    full_market, full = _run(panel, params)

    compared_entries = 0
    for target in days[-25:]:
        end = days.index(target)
        for sessions in (loaded, loaded + 50):
            window = panel.filter(pl.col("date").is_in(days[end - sessions + 1 : end + 1]))
            market, recent = _run(window, params)
            assert market.symbols == full_market.symbols
            message = f"{target} with {sessions} sessions"
            np.testing.assert_array_equal(recent.entry[-1], full.entry[end], err_msg=message)
            np.testing.assert_array_equal(recent.exit[-1], full.exit[end], err_msg=message)
        compared_entries += int(full.entry[end].sum())

    assert compared_entries > 0


@pytest.mark.parametrize(
    "params", [SENSITIVE, SMALL, ODD_STRIDE], ids=["defaults", "small_windows", "odd_stride"]
)
def test_only_bars_with_every_normalised_feature_vote_or_get_classified(params):
    panel = _panel(500)
    market, signals = _run(panel, params)

    prediction = _reference_prediction(market, params)
    ma20 = matrix_feature(market, "ma20")
    expected_entry = (prediction >= 0.0) & (market.close > ma20)
    expected_exit = (prediction < 0.0) | (market.close < ma20)

    unclassified = np.isfinite(market.close) & ~np.isfinite(prediction)
    flat = market.symbols.index(FLAT)
    assert unclassified[:, flat].sum() > unclassified[:, 0].sum()
    assert not signals.entry[unclassified].any()
    np.testing.assert_array_equal(signals.entry, expected_entry.astype(np.uint8))
    np.testing.assert_array_equal(signals.exit, expected_exit.astype(np.uint8))


def test_symbols_that_missed_more_sessions_than_the_allowance_are_not_classified():
    market, signals = _run(_panel(500), SENSITIVE)
    below_ma20 = market.close < matrix_feature(market, "ma20")
    # Without filters an unclassified bar never enters and exits only below MA20.
    classified = (signals.entry == 1) | ((signals.exit == 1) & ~below_ma20)
    long_gap = market.symbols.index(LONG_GAP)
    sparse = market.symbols.index(SPARSE)
    warmup = STRATEGY.required_warmup_bars(SENSITIVE)
    own_span = warmup - lorentzian_knn.MISSED_SESSION_ALLOWANCE

    # LONG_GAP has own_span own bars from session own_span + 60 on, but they stretch over 60
    # missed sessions until its oldest read bar comes after the gap.
    assert not classified[: own_span + 60 + 60, long_gap].any()
    assert classified[own_span + 60 + 60 :, long_gap].any()
    assert not classified[:, sparse].any()
    assert classified[:, 0].any()


@pytest.mark.parametrize(
    ("params", "oldest_neighbour"),
    [({}, 120), (SMALL, 119), (ODD_STRIDE, 115)],
    ids=["defaults", "small_windows", "odd_stride"],
)
def test_declared_warmup_is_the_oldest_read_bar_plus_the_allowance(params, oldest_neighbour):
    norm_window = int(params.get("norm_window", 60))
    feature_bars = max(20, *(recursive_warmup_bars(name) for name in lorentzian_knn.FEATURE_NAMES))

    assert STRATEGY.required_warmup_bars(params) == (
        oldest_neighbour
        + (norm_window - 1)
        + (feature_bars - 1)
        + lorentzian_knn.MISSED_SESSION_ALLOWANCE
    )
    assert feature_bars >= recursive_warmup_bars("macd_hist_pct")


def _instrument(symbol: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=code,
        instrument_type="stock",
        listing_status="active",
        listing_date="2000-01-04",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="股票",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-10-07",
    )


@pytest.fixture
def security_master(tmp_path, monkeypatch):
    """Every panel symbol is an established stock, so neither path drops one."""
    path = tmp_path / "security_master.parquet"
    pl.DataFrame([_instrument(symbol).to_dict() for symbol in SYMBOLS]).write_parquet(path)
    master = TaiwanSecurityMaster(cache_path=path)
    assert master.load_cache()
    monkeypatch.setattr(universe, "_default_master", master)
    monkeypatch.setattr(daily_store, "listing_sessions", lambda store=None: ListingSessions(()))


class _BacktestEngineStub:
    def __init__(self, panel: pl.DataFrame) -> None:
        self.panel = panel
        self.repo = SimpleNamespace(store=SimpleNamespace(data_dir=None))
        self.matrix = None

    def load_panel_for_backtest(self, symbols, start, end, feature_plan, asset_type="stock"):
        return self.panel

    def load_market_data_matrix_for_backtest(
        self, symbols, start, end, feature_plan, asset_type="stock", **kwargs
    ):
        fields = (
            set(feature_plan.base_columns)
            | set(feature_plan.instrument_columns)
            | set(feature_plan.matrix_columns)
        )
        return build_market_data_matrix(self.panel, field_columns=fields)

    def simulate_market_matrix(
        self, matrix, config, progress_cb=None, cancel_event=None, options=None
    ):
        self.matrix = matrix
        last = str(self.panel["date"].max())
        return SimResult(
            equity_curve=[{"date": last, "value": config.initial_capital}],
            drawdown_curve=[{"date": last, "value": 0.0}],
            trades=[],
            per_symbol_stats=[],
            stats={"total_return": 0.0, "n_trades": 0},
        )


def _backtest(engine: StrategyEngine, panel: pl.DataFrame, start: date, end: date):
    backtest_engine = _BacktestEngineStub(panel)
    result = StrategyBacktestService(engine=backtest_engine, strategy_engine=engine).run(
        StrategyBacktestConfig(
            strategy_id="lorentzian_knn",
            symbols=None,
            start=start,
            end=end,
            matching="close_t",
            mode="position",
        )
    )
    assert result.error is None, result.error
    return backtest_engine.matrix


@pytest.mark.usefixtures("security_master")
def test_screener_selection_matches_backtest_entries():
    panel = _panel(500)
    days = panel["date"].unique().sort().to_list()
    engine = StrategyEngine(strategy_dirs=[BUILTIN_DIR])
    matrix = _backtest(engine, panel, days[-25], days[-1])
    labels = [label[:10] for label in matrix.timestamp_labels]
    loaded = engine.required_history_bars(["lorentzian_knn"]) + 1

    selected_total = 0
    for target in days[-25:]:
        end = days.index(target)
        window = panel.filter(pl.col("date").is_in(days[end - loaded + 1 : end + 1]))
        context = StrategyDataContext(
            asset_type="stock",
            timeframe="1d",
            as_of=target,
            current=window.filter(pl.col("date") == target),
            history=window,
        )
        selected = {str(row["symbol"]) for row in engine.run("lorentzian_knn", context).rows}
        row = matrix.entry[labels.index(target.isoformat())]
        assert selected == {matrix.symbols[i] for i in np.flatnonzero(row)}, target
        selected_total += len(selected)

    assert selected_total > 0


@pytest.mark.usefixtures("security_master")
def test_backtest_signals_do_not_change_with_fifty_more_sessions_before_the_start():
    panel = _panel(500)
    days = panel["date"].unique().sort().to_list()
    engine = StrategyEngine(strategy_dirs=[BUILTIN_DIR])
    first = len(days) - 25 - engine.required_history_bars(["lorentzian_knn"])

    short = _backtest(engine, panel.filter(pl.col("date") >= days[first]), days[-25], days[-1])
    longer = _backtest(
        engine, panel.filter(pl.col("date") >= days[first - 50]), days[-25], days[-1]
    )

    assert short.timestamp_labels == longer.timestamp_labels
    assert short.entry.any()
    np.testing.assert_array_equal(short.entry, longer.entry)
    np.testing.assert_array_equal(short.exit, longer.exit)
