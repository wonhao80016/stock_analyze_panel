"""Deep-pullback capitulation reversal: a climax-volume stabilising bar near the
20-day low, confirmed by the next bar taking out its high.

Counter-trend by construction: entries fire while price is still below the
falling averages, so position sizing and the stop matter more here than in the
trend-following strategies.
"""

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    make_signal_matrix,
    matrix_feature,
    rolling_max,
    rolling_min,
    rolling_sum,
)
from app.backtest.matrix import (
    valid_shift as shift,
)

META = {
    "id": "bottom_reversal",
    "name": "深跌止穩反彈",
    "description": "深跌後爆量止跌K, 隔日突破止跌K高點確認",
    "tags": ["反轉", "搶反彈", "逆勢"],
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "params": [
        {
            "id": "min_drawdown",
            "label": "近20日最小回落幅度",
            "type": "float",
            "default": 0.15,
            "min": 0.05,
            "max": 0.50,
            "step": 0.01,
        },
        {
            "id": "stop_bar_vol_ratio",
            "label": "止跌K最低量倍數",
            "type": "float",
            "default": 2.0,
            "min": 1.2,
            "max": 5.0,
            "step": 0.1,
        },
        {
            "id": "near_low_tolerance",
            "label": "止跌K貼近20日低點容許度",
            "type": "float",
            "default": 0.03,
            "min": 0.0,
            "max": 0.15,
            "step": 0.01,
        },
        {
            "id": "min_down_days",
            "label": "最少連續下跌天數",
            "type": "int",
            "default": 3,
            "min": 1,
            "max": 10,
            "step": 1,
        },
        {
            "id": "fast_drop_5d",
            "label": "5日急跌幅度門檻",
            "type": "float",
            "default": 0.08,
            "min": 0.02,
            "max": 0.30,
            "step": 0.01,
        },
    ],
    "scoring": {"change_pct": 0.4, "vol_ratio_5d": 0.35, "rsi_14": 0.25},
    "order_by": "score",
    "descending": True,
    "limit": 500,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_capitulation_reversal"]
EXIT_SIGNALS = ["signal_ma20_break"]
STOP_LOSS = -0.06
MAX_HOLD_DAYS = 12


class BottomReversalMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"open", "high", "low", "close", "volume"})

    def required_warmup_bars(self, params: dict) -> int:
        del params
        return 60

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        high_20 = rolling_max(market.high, 20)
        drawdown = (high_20 - market.close) / high_20
        deep_enough = drawdown >= float(params.get("min_drawdown", 0.15))

        down_bar = shift((market.close < shift(market.close, 1)).astype(np.float32), 1)
        min_down = int(params.get("min_down_days", 3))
        consecutive_down = rolling_sum(down_bar, min_down) >= float(min_down)
        fast_drop = (shift(market.close, 5) - market.close) / shift(
            market.close, 5
        ) >= float(params.get("fast_drop_5d", 0.08))
        falling = consecutive_down | fast_drop

        stop_open = shift(market.open, 1)
        stop_high = shift(market.high, 1)
        stop_low = shift(market.low, 1)
        stop_close = shift(market.close, 1)
        stop_volume = shift(market.volume, 1)
        stop_range = stop_high - stop_low

        climax_volume = stop_volume >= shift(market.volume, 2) * float(
            params.get("stop_bar_vol_ratio", 2.0)
        )

        # Limit-locked bars have zero range; NaN keeps them out of every shape test.
        safe_range = np.where(stop_range > 0, stop_range, np.nan)
        body = np.abs(stop_close - stop_open)
        lower_wick = np.minimum(stop_open, stop_close) - stop_low
        long_lower_wick = lower_wick / safe_range >= 0.5
        doji = body / safe_range <= 0.1
        bullish_high_close = (stop_close > stop_open) & (
            (stop_close - stop_low) / safe_range >= 0.7
        )
        stabilising_shape = long_lower_wick | doji | bullish_high_close

        # Measured as of the stop bar, not the confirmation bar, so the test asks
        # "was that bar near the low when it printed" rather than mixing in a later low.
        low_20 = shift(rolling_min(market.low, 20), 1)
        near_low = stop_low <= low_20 * (
            1.0 + float(params.get("near_low_tolerance", 0.03))
        )

        entry = (
            deep_enough
            & falling
            & climax_volume
            & stabilising_shape
            & near_low
            & (market.close > stop_close)
            & (market.close > stop_high)
        )

        exit_ = market.close < matrix_feature(market, "ma20")

        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            entry_signal_ids=("signal_capitulation_reversal",),
            exit_signal_ids=("signal_ma20_break",),
        )


MATRIX_STRATEGY = BottomReversalMatrixStrategy()
