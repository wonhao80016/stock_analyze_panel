"""Builtin matrix strategies measure rolling windows over each symbol's own bars.

The matrix time axis is the union of every symbol's sessions, so a symbol that misses a session has
an empty row there. A raw row window is NaN for a whole window after such a row and then hands the
next bar a stale range; a builtin's signals on the shared axis must instead equal the signals it
computes from that symbol's bars alone.
"""

from __future__ import annotations

import ast
import importlib
import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from app.backtest import matrix as matrix_module
from app.backtest.matrix import (
    MarketDataMatrix,
    MatrixPipelineConfig,
    MatrixStrategyPipeline,
    SignalMatrix,
    build_market_data_matrix,
    rolling_quantile,
)

pytestmark = pytest.mark.usefixtures("deny_network")

BUILTIN_DIR = Path(__file__).resolve().parents[2] / "app" / "strategy" / "builtin"
NO_FILTER = MatrixPipelineConfig(
    basic_filter={"enabled": False},
    scoring={},
    order_by=None,
    descending=True,
)
SESSIONS = 220
TWSE = tuple(f"{1101 + offset}.TWSE" for offset in range(8))
TPEX = tuple(f"{6101 + offset}.TPEX" for offset in range(8))
# Loose thresholds so every strategy fires often enough on a random-walk panel.
PERMISSIVE_PARAMS = {
    "consolidation_breakout": {
        "bottom_zone_max": 1.0,
        "range_max": 0.6,
        "base_window": 10,
        "volume_contraction_max": 1.5,
        "breakout_vol_ratio": 1.0,
    },
    "swing_trend_confirmation": {
        "swing_radius": 2,
        "min_amount": 0.0,
        "min_volume": 0.0,
        "require_ma_alignment": False,
        "ma20_slope_bars": 1,
        "edge_triggered": False,
    },
    "bollinger_squeeze": {
        "squeeze_percentile": 0.5,
        "squeeze_lookback": 20,
        "vol_ratio_min": 1.0,
        "close_position_min": 0.0,
        "first_breakout_window": 5,
    },
    "bottom_reversal": {
        "min_drawdown": 0.05,
        "stop_bar_vol_ratio": 1.2,
        "near_low_tolerance": 0.15,
        "min_down_days": 2,
        "fast_drop_5d": 0.02,
    },
    "pullback_resume": {
        "pullback_window": 5,
        "pullback_vol_ratio_max": 1.5,
        "require_break_prev_high": False,
        "min_amount": 0.0,
        "min_volume": 0.0,
    },
    "n_day_low_reversal": {
        "require_bullish_candle": False,
        "use_volume_filter": False,
    },
}


# --------------------------------------------------------------------------
# valid_rolling_quantile
# --------------------------------------------------------------------------


def _dropna_rolling_quantile(
    values: np.ndarray,
    valid: np.ndarray,
    window: int,
    quantile: float,
) -> np.ndarray:
    expected = np.full(values.shape, np.nan, dtype=np.float64)
    for asset in range(values.shape[1]):
        rows = np.flatnonzero(valid[:, asset] & np.isfinite(values[:, asset]))
        series = values[rows, asset].astype(np.float64)
        for position in range(window - 1, rows.size):
            expected[rows[position], asset] = np.quantile(
                series[position - window + 1 : position + 1],
                quantile,
            )
    return expected


def _gapped_values() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(424242)
    # Few distinct levels so windows hold ties and evictions remove duplicated values.
    values = (10.0 + rng.integers(0, 12, size=(90, 6)) * 0.25).astype(np.float32)
    values[rng.random(values.shape) < 0.15] = np.nan
    values[30:36, 1] = np.nan
    values[:, 2] = np.nan
    values[:84, 3] = np.nan
    valid = np.ones(values.shape, dtype=bool)
    valid[10:13, 4] = False
    valid[50, 5] = False
    return values, valid


@pytest.mark.parametrize("window", [1, 4, 9])
@pytest.mark.parametrize("quantile", [0.0, 0.2, 0.5, 0.95, 1.0])
def test_valid_rolling_quantile_matches_per_asset_dropna_quantile(window: int, quantile: float):
    values, valid = _gapped_values()

    actual = matrix_module.valid_rolling_quantile(values, valid, window, quantile)

    assert actual.dtype == np.float32
    assert actual.shape == values.shape
    np.testing.assert_array_equal(
        actual,
        _dropna_rolling_quantile(values, valid, window, quantile).astype(np.float32),
    )
    assert np.isnan(actual[~(valid & np.isfinite(values))]).all()
    assert np.isnan(actual[:, 2]).all()


def test_valid_rolling_quantile_compiled_and_pure_python_kernels_agree_bit_for_bit(monkeypatch):
    kernel = matrix_module._valid_rolling_quantile_kernel
    if not hasattr(kernel, "py_func"):
        pytest.skip("numba is unavailable, so the kernel already runs as plain Python")
    rng = np.random.default_rng(31)
    # Values around zero, so a float32 difference of two window values would round.
    values = rng.normal(0.0, 10.0, size=(160, 6)).astype(np.float32)
    values[rng.random(values.shape) < 0.1] = np.nan
    valid = np.ones(values.shape, dtype=bool)
    cases = ((5, 0.2), (7, 0.5), (13, 0.37), (40, 0.95))
    compiled = [matrix_module.valid_rolling_quantile(values, valid, *case) for case in cases]

    monkeypatch.setattr(matrix_module, "_valid_rolling_quantile_kernel", kernel.py_func)

    for case, expected in zip(cases, compiled, strict=True):
        actual = matrix_module.valid_rolling_quantile(values, valid, *case)
        np.testing.assert_array_equal(actual.view(np.uint32), expected.view(np.uint32))
        np.testing.assert_array_equal(
            expected,
            _dropna_rolling_quantile(values, valid, *case).astype(np.float32),
        )


def test_valid_rolling_quantile_equals_raw_quantile_without_gaps():
    rng = np.random.default_rng(7)
    values = rng.normal(50.0, 5.0, size=(120, 5)).astype(np.float32)

    for window, quantile in ((5, 0.25), (20, 0.2), (37, 0.5)):
        np.testing.assert_allclose(
            matrix_module.valid_rolling_quantile(
                values,
                np.ones(values.shape, dtype=bool),
                window,
                quantile,
            ),
            rolling_quantile(values, window, quantile),
            rtol=1e-6,
            atol=1e-6,
            equal_nan=True,
        )


@pytest.mark.parametrize(("window", "quantile"), [(0, 0.5), (5, -0.1), (5, 1.5)])
def test_valid_rolling_quantile_rejects_invalid_arguments(window: int, quantile: float):
    values = np.ones((10, 2), dtype=np.float32)

    with pytest.raises(ValueError):
        matrix_module.valid_rolling_quantile(
            values,
            np.ones(values.shape, dtype=bool),
            window,
            quantile,
        )


# --------------------------------------------------------------------------
# Builtin signals on a gapped matrix
# --------------------------------------------------------------------------


def _sessions(count: int) -> list[date]:
    out, day = [], date(2025, 1, 6)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


@lru_cache(maxsize=1)
def _random_walk_panel() -> pl.DataFrame:
    rng = np.random.default_rng(20261009)
    days = _sessions(SESSIONS)
    drift = np.array([0.004, 0.0, -0.006, 0.002])[(np.arange(SESSIONS) // 30) % 4]
    frames = []
    for symbol in TWSE + TPEX:
        close = 50.0 * np.cumprod(1.0 + drift + rng.normal(0.0, 0.018, SESSIONS))
        previous = np.concatenate(([50.0], close[:-1]))
        open_ = previous * (1.0 + rng.normal(0.0, 0.006, SESSIONS))
        high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.008, SESSIONS)))
        low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.008, SESSIONS)))
        spikes = np.where(rng.random(SESSIONS) < 0.08, 2.5, 1.0)
        frames.append(
            pl.DataFrame(
                {
                    "symbol": [symbol] * SESSIONS,
                    "date": days,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": rng.lognormal(13.0, 0.35, SESSIONS) * spikes,
                }
            )
        )
    return pl.concat(frames)


def _run(name: str, frame: pl.DataFrame, params: dict) -> tuple[MarketDataMatrix, SignalMatrix]:
    strategy = importlib.import_module(f"app.strategy.builtin.{name}").MATRIX_STRATEGY
    market = build_market_data_matrix(frame)
    return market, MatrixStrategyPipeline().run(strategy, market, params, NO_FILTER)


def _own_bar_signals(
    name: str,
    frame: pl.DataFrame,
    symbol: str,
    params: dict,
) -> tuple[np.ndarray, np.ndarray]:
    _, signals = _run(name, frame.filter(pl.col("symbol") == symbol), params)
    return signals.entry[:, 0], signals.exit[:, 0]


def _session_before_latest_entry(name: str, panel: pl.DataFrame, symbols: tuple[str, ...]):
    """Return a symbol and the session three bars before its latest entry.

    That session lies inside every rolling window the entry bar reads under PERMISSIVE_PARAMS.
    """
    days = _sessions(SESSIONS)
    for symbol in symbols:
        entry, _ = _own_bar_signals(name, panel, symbol, PERMISSIVE_PARAMS[name])
        hits = np.flatnonzero(entry)
        if hits.size:
            return symbol, days[int(hits[-1]) - 3]
    raise AssertionError(f"{name} fires no entry on {symbols}; the fixture is too strict")


@lru_cache(maxsize=1)
def _sparse_panel() -> pl.DataFrame:
    """The random-walk panel with 30% of its symbol-days missing."""
    panel = _random_walk_panel()
    return panel.filter(pl.Series(np.random.default_rng(4).random(panel.height) >= 0.3))


def _gapped_panel(name: str, case: str) -> pl.DataFrame:
    panel = _random_walk_panel()
    if case == "thirty_percent_of_symbol_days":
        return _sparse_panel()
    if case == "one_symbol_one_day":
        symbol, day = _session_before_latest_entry(name, panel, TWSE)
        dropped = (pl.col("symbol") == symbol) & (pl.col("date") == day)
        expected_dropped = 1
    else:
        _, day = _session_before_latest_entry(name, panel, TPEX)
        dropped = pl.col("symbol").str.ends_with(".TPEX") & (pl.col("date") == day)
        expected_dropped = len(TPEX)
    gapped = panel.filter(~dropped)
    assert panel.height - gapped.height == expected_dropped
    return gapped


def _mismatches_against_own_bars(
    name: str,
    gapped: pl.DataFrame,
    params: dict,
) -> tuple[dict[str, dict[str, int]], int]:
    market, signals = _run(name, gapped, params)
    mismatches: dict[str, dict[str, int]] = {}
    reference_entries = 0
    for asset, symbol in enumerate(market.symbols):
        present = np.isfinite(market.close[:, asset])
        entry = signals.entry[present, asset]
        exit_ = signals.exit[present, asset]
        expected_entry, expected_exit = _own_bar_signals(name, gapped, symbol, params)
        reference_entries += int(expected_entry.sum())
        counts = {
            "extra_entries": int(((entry == 1) & (expected_entry == 0)).sum()),
            "missing_entries": int(((entry == 0) & (expected_entry == 1)).sum()),
            "exit_differences": int((exit_ != expected_exit).sum()),
            "entries_on_empty_rows": int(signals.entry[~present, asset].sum()),
        }
        if any(counts.values()):
            mismatches[symbol] = counts
    return mismatches, reference_entries


@pytest.mark.parametrize("name", sorted(PERMISSIVE_PARAMS))
@pytest.mark.parametrize(
    "case",
    ["one_symbol_one_day", "one_exchange_one_day", "thirty_percent_of_symbol_days"],
)
def test_builtin_signals_on_gapped_matrix_match_each_symbols_own_bars(name: str, case: str):
    mismatches, reference_entries = _mismatches_against_own_bars(
        name,
        _gapped_panel(name, case),
        PERMISSIVE_PARAMS[name],
    )

    assert reference_entries > 0
    assert mismatches == {}


OUTSIDE_PIPELINE_PARAMS = {
    **PERMISSIVE_PARAMS,
    "swing_trend_confirmation": {
        **PERMISSIVE_PARAMS["swing_trend_confirmation"],
        "edge_triggered": True,
    },
}


@pytest.mark.parametrize("name", sorted(OUTSIDE_PIPELINE_PARAMS))
def test_builtin_signals_outside_the_pipeline_match_the_pipeline(name: str):
    params = OUTSIDE_PIPELINE_PARAMS[name]
    panel = _sparse_panel()
    _, piped = _run(name, panel, params)
    strategy = importlib.import_module(f"app.strategy.builtin.{name}").MATRIX_STRATEGY

    direct = strategy.compute_signals(build_market_data_matrix(panel), params)

    np.testing.assert_array_equal(direct.entry, piped.entry)
    np.testing.assert_array_equal(direct.exit, piped.exit)


# Builtin warm-ups leave about 40 bars beyond what a strategy reads for sessions a symbol missed.
SCREENER_MISSING_SESSIONS = 40


def _late_entry_with_missing_sessions(
    name: str,
    params: dict,
    loaded: int,
) -> tuple[str, int, pl.DataFrame]:
    """Find a symbol whose entry survives missing the first sessions of the loaded window."""
    panel = _random_walk_panel()
    days = _sessions(SESSIONS)
    for symbol in TWSE + TPEX:
        entry, _ = _own_bar_signals(name, panel, symbol, params)
        for end in (int(hit) for hit in np.flatnonzero(entry)[::-1]):
            start = end - loaded + 1
            if start < 0:
                break
            missing = days[start + 1 : start + 1 + SCREENER_MISSING_SESSIONS]
            gapped = panel.filter(~((pl.col("symbol") == symbol) & pl.col("date").is_in(missing)))
            market, signals = _run(name, gapped, params)
            if signals.entry[end, market.symbols.index(symbol)]:
                return symbol, end, gapped
    raise AssertionError(f"{name} has no late entry that survives missing sessions")


SCREENER_CASES = [
    *(pytest.param(name, PERMISSIVE_PARAMS[name], id=name) for name in sorted(PERMISSIVE_PARAMS)),
    pytest.param(
        "pullback_resume",
        {**PERMISSIVE_PARAMS["pullback_resume"], "pullback_window": 3},
        id="pullback_resume-window3",
    ),
    pytest.param(
        "consolidation_breakout",
        {**PERMISSIVE_PARAMS["consolidation_breakout"], "base_window": 60},
        id="consolidation_breakout-window60",
    ),
    # Builtins reading 60-bar windows; loose filters only so the random walk fires them.
    pytest.param("bullish_alignment", {}, id="bullish_alignment"),
    pytest.param("trend_breakout", {"use_volume_filter": False}, id="trend_breakout"),
    pytest.param("ma_golden_cross", {"use_volume_filter": False}, id="ma_golden_cross"),
    pytest.param("pullback_ma20_bounce", {}, id="pullback_ma20_bounce"),
    pytest.param("pullback_to_support", {"use_volume_filter": False}, id="pullback_to_support"),
    pytest.param(
        "low_volatility_leader", {"use_volatility_filter": False}, id="low_volatility_leader"
    ),
]


@pytest.mark.parametrize(("name", "params"), SCREENER_CASES)
def test_screener_length_history_with_a_gapped_symbol_matches_full_history(
    name: str,
    params: dict,
):
    strategy = importlib.import_module(f"app.strategy.builtin.{name}").MATRIX_STRATEGY
    # The screener and the monitor load required_warmup_bars + 2 union sessions.
    loaded = strategy.required_warmup_bars(params) + 2
    symbol, end, gapped = _late_entry_with_missing_sessions(name, params, loaded)
    recent_days = _sessions(SESSIONS)[end - loaded + 1 : end + 1]

    full_market, full = _run(name, gapped, params)
    recent_market, recent = _run(name, gapped.filter(pl.col("date").is_in(recent_days)), params)

    assert recent_market.symbols == full_market.symbols
    assert recent.entry[-1, recent_market.symbols.index(symbol)] == 1
    np.testing.assert_array_equal(recent.entry[-1], full.entry[end])
    np.testing.assert_array_equal(recent.exit[-1], full.exit[end])


@pytest.mark.parametrize("name", sorted(PERMISSIVE_PARAMS))
def test_builtin_exits_stay_off_rows_without_a_bar(name: str):
    gapped = _gapped_panel(name, "one_exchange_one_day")
    market, signals = _run(name, gapped, PERMISSIVE_PARAMS[name])

    assert not signals.exit[~np.isfinite(market.close)].any()


def _consolidation_bars(*, spike: bool) -> dict[str, list[float]]:
    """A slow uptrend on drying volume whose last bar jumps above the old base."""
    closes = [10.0 + 0.004 * index for index in range(125)] + [11.0]
    opens = [close - 0.02 for close in closes[:-1]] + [10.5]
    highs = [close + 0.05 for close in closes]
    lows = [close - 0.05 for close in closes]
    volumes = [2000.0] * 85 + [1000.0] * 40 + [3000.0]
    if spike:
        highs[110] = 11.5
    return {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes}


def test_consolidation_breakout_does_not_break_out_of_a_stale_base_after_a_missing_bar():
    days = _sessions(126)
    gap_day = days[105]
    flat = [20.0] * len(days)
    frames = [
        pl.DataFrame(
            {
                "symbol": [symbol] * len(days),
                "date": days,
                **_consolidation_bars(spike=spike),
            }
        ).filter(pl.col("date") != gap_day)
        for symbol, spike in (("2301.TWSE", True), ("2302.TWSE", False))
    ]
    frames.append(
        pl.DataFrame(
            {
                "symbol": ["2303.TWSE"] * len(days),
                "date": days,
                "open": flat,
                "high": flat,
                "low": flat,
                "close": flat,
                "volume": [1000.0] * len(days),
            }
        )
    )
    gapped = pl.concat(frames)
    # The fixture's base volume sits just above its prior window, so the contraction bound is pinned.
    params = {"base_window": 20, "volume_contraction_max": 1.05}

    mismatches, _ = _mismatches_against_own_bars("consolidation_breakout", gapped, params)
    market, signals = _run("consolidation_breakout", gapped, params)
    last = len(days) - 1

    # 2301's base still holds the 11.5 spike printed after the missing bar, so 11.0 is no breakout.
    assert mismatches == {}
    assert signals.entry[last, market.symbols.index("2301.TWSE")] == 0
    assert signals.entry[last, market.symbols.index("2302.TWSE")] == 1
    assert signals.entry.sum() == 1


# --------------------------------------------------------------------------
# Raw row windows stay out of builtin strategies
# --------------------------------------------------------------------------

RAW_ROW_HELPERS = frozenset(
    name for name in vars(matrix_module) if re.fullmatch(r"_?rolling_[a-z_]+", name)
) | {"shift", "ewm_adjust_false"}
BUILTIN_FILES = tuple(
    sorted(path for path in BUILTIN_DIR.glob("*.py") if path.name != "__init__.py")
)
RAW_WINDOW_EXCEPTIONS: frozenset[str] = frozenset()


def _raw_helper_references(source: str) -> set[str]:
    """Raw row helpers a module binds from app.backtest.matrix, whatever local name it uses."""
    tree = ast.parse(source)
    found: set[str] = set()
    module_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.backtest.matrix":
            found.update(
                alias.name
                for alias in node.names
                if alias.name in RAW_ROW_HELPERS or alias.name == "*"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "app.backtest":
            module_names.update(
                alias.asname or alias.name for alias in node.names if alias.name == "matrix"
            )
        elif isinstance(node, ast.Import):
            module_names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "app.backtest.matrix"
            )
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in RAW_ROW_HELPERS
            and ast.unparse(node.value) in module_names
        ):
            found.add(node.attr)
    return found


def _builtin_raw_helper_references(path: Path) -> set[str]:
    return _raw_helper_references(path.read_text(encoding="utf-8"))


def test_raw_row_helper_set_covers_every_raw_primitive():
    assert {
        "rolling_min",
        "rolling_max",
        "rolling_mean",
        "rolling_sum",
        "rolling_std",
        "rolling_quantile",
        "_rolling_reduce",
        "shift",
        "ewm_adjust_false",
    } <= RAW_ROW_HELPERS
    assert not any(name.startswith("valid_") for name in RAW_ROW_HELPERS)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from app.backtest.matrix import shift\n", {"shift"}),
        ("from app.backtest.matrix import rolling_max as highest\n", {"rolling_max"}),
        ("from app.backtest.matrix import *\n", {"*"}),
        (
            "from app.backtest import matrix as m\nm.ewm_adjust_false(x, span=3)\n",
            {"ewm_adjust_false"},
        ),
        ("import app.backtest.matrix\napp.backtest.matrix.rolling_sum(x, 3)\n", {"rolling_sum"}),
        (
            "from app.backtest.matrix import valid_shift as shift\n"
            "from app.backtest.matrix import valid_ewm_adjust_false as ewm_adjust_false\n"
            "shift(x, 1)\newm_adjust_false(x, valid, span=3)\n",
            set(),
        ),
        ("import polars as pl\npl.col('close').shift(1).rolling_max(3)\n", set()),
    ],
)
def test_raw_helper_scan_follows_import_bindings(source: str, expected: set[str]):
    assert _raw_helper_references(source) == expected


@pytest.mark.parametrize(
    "path",
    [path for path in BUILTIN_FILES if path.name not in RAW_WINDOW_EXCEPTIONS],
    ids=lambda path: path.stem,
)
def test_builtin_strategy_uses_no_raw_row_helper(path: Path):
    assert _builtin_raw_helper_references(path) == set()


def test_raw_window_exceptions_are_exactly_the_remaining_users():
    users = {path.name for path in BUILTIN_FILES if _builtin_raw_helper_references(path)}

    assert users == RAW_WINDOW_EXCEPTIONS
