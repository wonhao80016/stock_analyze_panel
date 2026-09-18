"""Pullback resumption: an uptrend that pulled back on drying volume while
holding MA20, then reclaims MA5 and takes out the prior high on rising volume.

The pullback window is a fixed lookback rather than "bars since the last swing
high"; a per-symbol variable window cannot be vectorised across the matrix, and
the fixed window is exposed as a tunable parameter instead.
"""

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    make_signal_matrix,
    matrix_feature,
    rolling_min,
    rolling_sum,
    valid_rolling_mean,
)
from app.backtest.matrix import (
    valid_shift as shift,
)

META = {
    "id": "pullback_resume",
    "name": "強勢回檔再起漲",
    "description": "多頭回檔量縮守MA20後, 站回MA5並突破前高",
    "tags": ["回檔", "起漲", "多頭"],
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "params": [
        {
            "id": "pullback_window",
            "label": "回檔觀察天數",
            "type": "int",
            "default": 10,
            "min": 3,
            "max": 30,
            "step": 1,
        },
        {
            "id": "pullback_vol_ratio_max",
            "label": "回檔期量縮比上限",
            "type": "float",
            "default": 1.0,
            "min": 0.3,
            "max": 1.5,
            "step": 0.05,
        },
        {
            "id": "require_break_prev_high",
            "label": "要求突破前一日高點",
            "type": "bool",
            "default": True,
        },
        {
            "id": "min_volume",
            "label": "最低成交量(股)",
            "type": "float",
            "default": 2000000.0,
            "min": 0.0,
            "max": 50000000.0,
            "step": 500000.0,
        },
    ],
    "scoring": {"momentum_20d": 0.4, "vol_ratio_5d": 0.35, "change_pct": 0.25},
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_pullback_resume"]
EXIT_SIGNALS = ["signal_ma20_break"]
STOP_LOSS = -0.06
MAX_HOLD_DAYS = 15


class PullbackResumeMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"open", "high", "low", "close", "volume"})

    def required_warmup_bars(self, params: dict) -> int:
        return 60 + 2 * int(params.get("pullback_window", 10))

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        window = int(params.get("pullback_window", 10))

        ma5 = matrix_feature(market, "ma5")
        ma10 = matrix_feature(market, "ma10")
        ma20 = matrix_feature(market, "ma20")
        ma60 = matrix_feature(market, "ma60")

        below_ma5 = shift((market.close < ma5).astype(np.float32), 1)
        had_pullback = rolling_sum(below_ma5, window) > 0.0

        held_ma20 = shift(rolling_min(market.close - ma20, window), 1) >= 0.0

        volume_valid = np.isfinite(market.close) & np.isfinite(market.volume)
        pullback_volume = shift(valid_rolling_mean(market.volume, volume_valid, window), 1)
        prior_volume = shift(pullback_volume, window)
        dried_up = pullback_volume < prior_volume * float(
            params.get("pullback_vol_ratio_max", 1.0)
        )

        reclaim_ma5 = (market.close > ma5) & (shift(market.close, 1) <= shift(ma5, 1))
        volume_expanding = market.volume > pullback_volume

        entry = (
            had_pullback
            & held_ma20
            & dried_up
            & reclaim_ma5
            & volume_expanding
            & (ma20 > ma60)
            & (ma10 > ma20)
            & (market.close > market.open)
            & (market.volume >= float(params.get("min_volume", 2000000.0)))
        )
        if params.get("require_break_prev_high", True):
            entry &= market.close > shift(market.high, 1)

        exit_ = market.close < ma20

        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            entry_signal_ids=("signal_pullback_resume",),
            exit_signal_ids=("signal_ma20_break",),
        )


MATRIX_STRATEGY = PullbackResumeMatrixStrategy()
