"""factor_rank_research scores cross-sectional average-rank percentiles."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.backtest.matrix import (
    MatrixPipelineConfig,
    MatrixStrategyPipeline,
    build_market_data_matrix,
    validate_signal_matrix,
)
from app.strategy.builtin import factor_rank_research
from app.strategy.builtin.factor_rank_research import MATRIX_STRATEGY

pytestmark = pytest.mark.usefixtures("deny_network")


def _reference_average_rank(values: np.ndarray) -> np.ndarray:
    """1-based ranks where tied values share their mean rank."""
    return np.array([
        1.0 + np.sum(values < value) + (np.sum(values == value) - 1) / 2.0
        for value in values
    ])


def _market(factors: dict[str, list[list[float | None]]], closes: list[float] | None = None):
    """``factors[name][t][i]`` is asset i's factor value on session t; None is a missing value."""
    first = next(iter(factors.values()))
    sessions, assets = len(first), len(first[0])
    closes = closes or [10.0] * assets
    rows = []
    for t in range(sessions):
        for i in range(assets):
            rows.append(
                {
                    "symbol": f"{1101 + i}.TWSE",
                    "date": date(2025, 1, 6) + timedelta(days=t),
                    "open": closes[i],
                    "high": closes[i] * 1.01,
                    "low": closes[i] * 0.99,
                    "close": closes[i],
                    "raw_close": closes[i],
                    "volume": 1_000.0,
                    **{name: values[t][i] for name, values in factors.items()},
                }
            )
    return build_market_data_matrix(pl.DataFrame(rows), field_columns=set(factors))


def _signals(market, scoring: dict, directions: dict, **params):
    signals = MATRIX_STRATEGY.compute_signals(
        market,
        {
            "scoring": scoring,
            "directions": directions,
            "entry_score": params.get("entry_score", 70.0),
            "exit_score": params.get("exit_score", 40.0),
            "top_rank": params.get("top_rank", 100),
        },
    )
    validate_signal_matrix(signals, market.shape)
    return signals


# One extreme value: min-max would squeeze every other asset towards one end of the scale.
EXTREME_10 = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 1_000_000.0]
EXTREME_20 = [float(value) for value in range(1, 20)] + [1e9]


@pytest.mark.parametrize("values", [EXTREME_10, EXTREME_20], ids=["10_assets", "20_assets"])
@pytest.mark.parametrize("direction", ["high", "low"])
def test_extreme_value_leaves_exactly_the_top_30_percent_at_70(values, direction):
    market = _market({"amount": [values]})
    count = len(values)

    signals = _signals(market, {"amount": 1.0}, {"amount": direction})

    by_preference = sorted(range(count), key=lambda i: values[i], reverse=direction == "high")
    entered = set(np.flatnonzero(signals.entry[0]).tolist())
    exited = set(np.flatnonzero(signals.exit[0]).tolist())
    assert entered == set(by_preference[: count * 3 // 10])
    assert exited == set(by_preference[-(count * 4 // 10) :])
    assert np.nanmin(signals.score) == 0.0
    assert np.nanmax(signals.score) == 100.0


def test_scores_are_average_rank_percentiles_and_ties_share_a_rank():
    market = _market({"amount": [[5.0, 5.0, 1.0, 9.0, 10.0], [3.0, None, None, None, None]]})

    signals = _signals(market, {"amount": 1.0}, {"amount": "high"})

    np.testing.assert_allclose(signals.score[0], [37.5, 37.5, 0.0, 75.0, 100.0])
    # A cross-section of one scored asset sits in the middle.
    assert signals.score[1, 0] == 50.0


def test_factor_combination_ranks_the_weighted_rank_composite():
    market = _market(
        {
            "amount": [[1.0, 2.0, 3.0, 4.0, 5.0]],
            "turnover_rate": [[50.0, 10.0, 30.0, 20.0, 40.0]],
        }
    )

    signals = _signals(
        market,
        {"amount": 3.0, "turnover_rate": 1.0},
        {"amount": "high", "turnover_rate": "low"},
    )

    # Composite 3 * rank(amount) - rank(turnover): [-2, 5, 6, 10, 11].
    np.testing.assert_allclose(signals.score[0], [0.0, 25.0, 50.0, 75.0, 100.0])


def test_high_and_low_directions_mirror_each_other_with_ties_and_several_factors():
    market = _market(
        {
            "amount": [[3.0, 1.0, 3.0, 2.0, 5.0, 2.0]],
            "turnover_rate": [[1.0, 1.0, 2.0, 2.0, 3.0, 3.0]],
        }
    )
    scoring = {"amount": 2.0, "turnover_rate": 1.0}

    high = _signals(market, scoring, {"amount": "high", "turnover_rate": "high"})
    low = _signals(market, scoring, {"amount": "low", "turnover_rate": "low"})

    np.testing.assert_allclose(high.score + low.score, 100.0, atol=1e-4)


def test_missing_factor_is_unscored_and_never_triggers_the_factor_exit():
    # Asset 0 ranks last on sessions 0 and 2 and has no factor value on session 1.
    market = _market(
        {
            "amount": [
                [1.0, 2.0, 3.0, 4.0, 5.0],
                [None, 2.0, 3.0, 4.0, 5.0],
                [1.0, 2.0, 3.0, 4.0, 5.0],
            ]
        }
    )

    signals = _signals(market, {"amount": 1.0}, {"amount": "high"})

    assert signals.exit[:, 0].tolist() == [1, 0, 1]
    assert signals.entry[:, 0].tolist() == [0, 0, 0]
    assert np.isnan(signals.score[1, 0])
    # The other four rank among themselves.
    np.testing.assert_allclose(signals.score[1, 1:], [0.0, 100.0 / 3, 200.0 / 3, 100.0], rtol=1e-6)


@pytest.mark.parametrize("seed", range(5))
def test_average_ranks_match_the_reference_rank(seed):
    rng = np.random.default_rng(seed)
    values = np.concatenate([rng.integers(0, 6, 40).astype(np.float64), rng.normal(size=25)])

    np.testing.assert_array_equal(
        factor_rank_research._average_ranks(values), _reference_average_rank(values)
    )


def test_integer_weights_give_the_reference_rank_composite_with_its_ties():
    rng = np.random.default_rng(7)
    assets = 40
    factors = {
        "amount": rng.integers(0, 5, assets).astype(float),
        "turnover_rate": rng.integers(0, 4, assets).astype(float),
        "vol_ratio_5d": rng.integers(0, 3, assets).astype(float),
    }
    scoring = {"amount": 1.0, "turnover_rate": 2.0, "vol_ratio_5d": 3.0}
    directions = {"amount": "high", "turnover_rate": "low", "vol_ratio_5d": "high"}
    market = _market({name: [values.tolist()] for name, values in factors.items()})

    signals = _signals(market, scoring, directions)

    ranks = np.column_stack([_reference_average_rank(values) for values in factors.values()])
    signs = np.array([1.0 if directions[name] == "high" else -1.0 for name in factors])
    composite = (ranks * signs) @ np.array([scoring[name] for name in factors])
    expected = (_reference_average_rank(composite) - 1.0) * 100.0 / (assets - 1)
    np.testing.assert_array_equal(signals.score[0], expected.astype(np.float32))
    assert len(np.unique(expected)) < assets


def test_cells_without_a_bar_have_no_score():
    gapped = build_market_data_matrix(
        pl.DataFrame(
            {
                "symbol": ["1101.TWSE", "1102.TWSE", "1103.TWSE", "1101.TWSE", "1102.TWSE"],
                "date": [date(2025, 1, 6)] * 3 + [date(2025, 1, 7)] * 2,
                "open": [10.0] * 5,
                "high": [10.1] * 5,
                "low": [9.9] * 5,
                "close": [10.0] * 5,
                "volume": [1_000.0] * 5,
                "amount": [1.0, 2.0, 3.0, 1.0, 2.0],
            }
        ),
        field_columns={"amount"},
    )

    signals = _signals(gapped, {"amount": 1.0}, {"amount": "high"})

    assert np.isnan(signals.score[1, 2])
    np.testing.assert_allclose(signals.score[1, :2], [0.0, 100.0])


def test_top_rank_cut_applies_after_the_basic_filter():
    # The two best scores belong to assets priced under the filter's minimum.
    market = _market(
        {"amount": [[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]]},
        closes=[10.0, 10.0, 10.0, 10.0, 2.0, 2.0],
    )
    params = {
        "scoring": {"amount": 1.0},
        "directions": {"amount": "high"},
        "entry_score": 0.0,
        "exit_score": 0.0,
        "top_rank": 2,
    }
    config = MatrixPipelineConfig(
        basic_filter={"enabled": True, "price_min": 5.0},
        scoring={},
        order_by="score",
        descending=True,
    )

    signals = MatrixStrategyPipeline().run(MATRIX_STRATEGY, market, params, config)

    assert np.flatnonzero(signals.entry[0]).tolist() == [2, 3]
