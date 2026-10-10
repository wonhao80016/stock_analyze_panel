"""The limit_entries hook can only remove entries the basic filter and asset mask kept."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.backtest.matrix import (
    MarketDataMatrix,
    MatrixPipelineConfig,
    MatrixStrategyPipeline,
    SignalMatrix,
    build_market_data_matrix,
    make_signal_matrix,
)

pytestmark = pytest.mark.usefixtures("deny_network")

# Assets 0-1 trade under the price floor; asset 5 is outside the asset mask.
CLOSES = [2.0, 2.0, 10.0, 11.0, 12.0, 13.0]
PRICE_FLOOR = {"enabled": True, "price_min": 5.0}
ASSET_MASK = np.array([True, True, True, True, True, False])
ALLOWED = [2, 3, 4]


def _market(sessions: int = 3) -> MarketDataMatrix:
    rows = [
        {
            "symbol": f"{1101 + i}.TWSE",
            "date": date(2025, 1, 6) + timedelta(days=t),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1_000.0,
        }
        for t in range(sessions)
        for i, close in enumerate(CLOSES)
    ]
    return build_market_data_matrix(pl.DataFrame(rows))


class _EveryAsset:
    """Enters every asset on every bar and hands its entries to *hook*."""

    def __init__(self, hook) -> None:
        self.hook = hook

    def required_fields(self) -> frozenset[str]:
        return frozenset({"close"})

    def required_warmup_bars(self, params: dict) -> int:
        return 1

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        return make_signal_matrix(
            market.shape,
            entry=np.ones(market.shape, dtype=np.uint8),
            score=np.tile(np.arange(market.shape[1], dtype=np.float32), (market.shape[0], 1)),
            entry_signal_code=np.zeros(market.shape, dtype=np.int16),
            entry_signal_ids=("signal_test_entry",),
        )

    def limit_entries(self, entry: np.ndarray, score: np.ndarray, params: dict) -> np.ndarray:
        return self.hook(entry, score)


def _entered(hook) -> list[list[int]]:
    config = MatrixPipelineConfig(
        basic_filter=PRICE_FLOOR,
        scoring={},
        order_by="score",
        descending=True,
        asset_mask=ASSET_MASK,
    )
    signals = MatrixStrategyPipeline().run(_EveryAsset(hook), _market(), {}, config)
    return [np.flatnonzero(row).tolist() for row in signals.entry]


def test_extra_true_cells_returned_by_the_hook_add_no_entry():
    entered = _entered(lambda entry, score: np.ones(entry.shape, dtype=bool))

    assert entered == [ALLOWED] * 3


def test_the_hook_can_remove_entries():
    def best_only(entry, score):
        kept = np.zeros(entry.shape, dtype=bool)
        kept[:, 4] = True
        return kept

    assert _entered(best_only) == [[4]] * 3


def test_setting_the_entries_in_place_cannot_bypass_the_filter_or_the_asset_mask():
    def widen(entry, score):
        entry[:] = True
        return entry

    assert _entered(widen) == [ALLOWED] * 3


def test_widening_the_entries_in_place_and_returning_another_array_adds_nothing():
    def widen_then_keep_all(entry, score):
        entry |= True
        return np.ones(entry.shape, dtype=bool)

    assert _entered(widen_then_keep_all) == [ALLOWED] * 3


@pytest.mark.parametrize(
    "result",
    [
        lambda entry: np.ones(entry.shape[1], dtype=bool),
        lambda entry: np.ones((entry.shape[0] + 1, entry.shape[1]), dtype=bool),
        lambda entry: None,
    ],
    ids=["one_row", "extra_row", "none"],
)
def test_a_hook_result_of_another_shape_raises_naming_the_hook_and_the_shapes(result):
    with pytest.raises(ValueError, match=r"limit_entries.*\(3, 6\)"):
        _entered(lambda entry, score: result(entry))
