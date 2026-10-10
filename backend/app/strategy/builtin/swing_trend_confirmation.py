"""Swing structure trend confirmation: higher highs and higher lows on confirmed
swing points, backed by moving-average alignment."""

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    make_signal_matrix,
    matrix_feature,
    valid_rolling_max,
    valid_rolling_min,
)
from app.backtest.matrix import (
    valid_shift as shift,
)


def _forward_fill(values: np.ndarray, mask: np.ndarray) -> np.ndarray:
    rows = np.arange(values.shape[0], dtype=np.int64)[:, None]
    idx = np.where(mask, rows, -1)
    np.maximum.accumulate(idx, axis=0, out=idx)
    seen = idx >= 0
    picked = np.take_along_axis(values, np.where(seen, idx, 0), axis=0)
    return np.where(seen, picked, np.nan).astype(np.float32)


def _confirmed_swings(
    values: np.ndarray, radius: int, *, peak: bool
) -> tuple[np.ndarray, np.ndarray]:
    """Return (most recent, second most recent) confirmed swing prices; NaN on rows without a bar.

    A swing at bar t is only knowable at t+radius, so the candidate is read from
    a shifted series and compared against a window that ends at the current bar.
    Aligning it to t instead would let the strategy see the future.
    """
    valid = np.isfinite(values)
    span = 2 * radius + 1
    window = (
        valid_rolling_max(values, valid, span) if peak else valid_rolling_min(values, valid, span)
    )
    candidate = shift(values, radius, valid)
    is_swing = np.isfinite(candidate) & (
        candidate >= window if peak else candidate <= window
    )
    last = _forward_fill(candidate, is_swing)
    shifted = np.vstack(
        [np.full((1, last.shape[1]), np.nan, dtype=np.float32), last[:-1]]
    )
    previous = _forward_fill(shifted, is_swing)
    return np.where(valid, last, np.nan), np.where(valid, previous, np.nan)


def _liquid(market: MarketDataMatrix, params: dict) -> np.ndarray:
    liquid = np.ones(market.shape, dtype=bool)
    min_amount = float(params.get("min_amount", 100_000_000.0))
    if min_amount > 0:
        liquid &= market.field("amount") >= min_amount
    # A share-count minimum saved before the traded-value rule still applies on top of it.
    min_volume = float(params.get("min_volume", 0.0))
    if min_volume > 0:
        liquid &= market.volume >= min_volume
    return liquid


META = {
    "id": "swing_trend_confirmation",
    "name": "波段結構確認",
    "description": "頭頭高底底高的波段結構 + 均線多頭排列",
    "tags": ["波段", "結構", "趨勢"],
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "params": [
        {
            "id": "swing_radius",
            "label": "波峰波谷認定半徑",
            "type": "int",
            "default": 2,
            "min": 1,
            "max": 6,
            "step": 1,
        },
        {
            "id": "min_amount",
            "label": "最低成交金額(元)",
            "type": "float",
            "default": 100000000.0,
            "min": 0.0,
            "max": 5000000000.0,
            "step": 10000000.0,
        },
        {
            "id": "min_volume",
            "label": "最低成交量(股)",
            "type": "float",
            "default": 0.0,
            "min": 0.0,
            "max": 50000000.0,
            "step": 500000.0,
        },
        {
            "id": "require_ma_alignment",
            "label": "要求MA5>MA10>MA20",
            "type": "bool",
            "default": True,
        },
        {
            "id": "ma20_slope_bars",
            "label": "MA20 斜率比較天數",
            "type": "int",
            "default": 5,
            "min": 1,
            "max": 20,
            "step": 1,
        },
        {
            "id": "edge_triggered",
            "label": "僅在結構剛成立時觸發",
            "type": "bool",
            "default": True,
        },
    ],
    "scoring": {"momentum_20d": 0.5, "change_pct": 0.25, "vol_ratio_5d": 0.25},
    "order_by": "score",
    "descending": True,
    "limit": 500,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_swing_structure_bullish"]
EXIT_SIGNALS = ["signal_ma20_break", "signal_swing_structure_exit"]
STOP_LOSS = -0.07
MAX_HOLD_DAYS = 20


class SwingTrendConfirmationMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"high", "low", "close", "volume", "amount"})

    def required_warmup_bars(self, params: dict) -> int:
        del params
        return 90

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        radius = int(params.get("swing_radius", 2))
        last_high, prev_high = _confirmed_swings(market.high, radius, peak=True)
        last_low, prev_low = _confirmed_swings(market.low, radius, peak=False)

        higher_highs = last_high > prev_high
        higher_lows = last_low > prev_low

        ma5 = matrix_feature(market, "ma5")
        ma10 = matrix_feature(market, "ma10")
        ma20 = matrix_feature(market, "ma20")

        structure = (
            higher_highs
            & higher_lows
            & (market.close > ma20)
            & (ma20 > shift(ma20, int(params.get("ma20_slope_bars", 5))))
        )
        if params.get("require_ma_alignment", True):
            structure &= (ma5 > ma10) & (ma10 > ma20)

        entry = structure & _liquid(market, params)
        # The structure persists for as long as the trend runs; without an edge
        # trigger every bar of an uptrend is an entry and the book never turns over.
        if params.get("edge_triggered", True):
            entry &= shift(structure.astype(np.float32), 1, np.isfinite(market.close)) == 0.0

        ma20_break = market.close < ma20
        structure_break = last_low < prev_low
        exit_ = ma20_break | structure_break
        exit_code = np.where(ma20_break, 0, np.where(structure_break, 1, -1))

        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=exit_code.astype(np.int16),
            entry_signal_ids=("signal_swing_structure_bullish",),
            exit_signal_ids=("signal_ma20_break", "signal_swing_structure_exit"),
        )


MATRIX_STRATEGY = SwingTrendConfirmationMatrixStrategy()
