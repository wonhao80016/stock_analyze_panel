"""Low-base consolidation breakout: a tight, volume-dried range near the 60-day
low, broken upward on expanding volume."""

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    make_signal_matrix,
    matrix_feature,
    valid_rolling_max,
    valid_rolling_mean,
    valid_rolling_min,
)
from app.backtest.matrix import (
    valid_shift as shift,
)

META = {
    "id": "consolidation_breakout",
    "name": "底部整理突破",
    "description": "低檔量縮橫盤整理後, 帶量突破區間上緣",
    "tags": ["整理", "突破", "底部"],
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "params": [
        {
            "id": "bottom_zone_max",
            "label": "距60日低點最大漲幅",
            "type": "float",
            "default": 0.45,
            "min": 0.10,
            "max": 1.00,
            "step": 0.05,
        },
        {
            "id": "range_max",
            "label": "整理區間最大振幅",
            "type": "float",
            "default": 0.30,
            "min": 0.05,
            "max": 0.60,
            "step": 0.05,
        },
        {
            "id": "base_window",
            "label": "整理區間天數",
            "type": "int",
            "default": 20,
            "min": 10,
            "max": 60,
            "step": 5,
        },
        {
            "id": "volume_contraction_max",
            "label": "整理期量縮比上限",
            "type": "float",
            "default": 0.9,
            "min": 0.50,
            "max": 1.50,
            "step": 0.05,
        },
        {
            "id": "breakout_vol_ratio",
            "label": "突破日最低量倍數",
            "type": "float",
            "default": 1.2,
            "min": 1.0,
            "max": 4.0,
            "step": 0.1,
        },
    ],
    "scoring": {"vol_ratio_5d": 0.4, "change_pct": 0.3, "distance_from_low_60d": 0.3},
    "scoring_directions": {"distance_from_low_60d": "low"},
    "order_by": "score",
    "descending": True,
    "limit": 500,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_consolidation_breakout"]
EXIT_SIGNALS = ["signal_ma20_break"]
STOP_LOSS = -0.08
MAX_HOLD_DAYS = 25


class ConsolidationBreakoutMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"open", "high", "low", "close", "volume"})

    def required_warmup_bars(self, params: dict) -> int:
        # Own bars read (MA60 five bars back, or two stacked base windows) plus 40 that absorb
        # sessions the symbol missed inside the loaded window of shared sessions.
        return max(65, 2 * int(params.get("base_window", 20)) + 1) + 40

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        base_window = int(params.get("base_window", 20))

        low_60 = matrix_feature(market, "low_60d")
        above_low = (market.close - low_60) / low_60
        in_bottom_zone = (above_low >= 0.0) & (
            above_low <= float(params.get("bottom_zone_max", 0.45))
        )

        close_valid = np.isfinite(market.close)
        # Range is measured excluding today so a breakout bar cannot widen its own base.
        base_high = shift(valid_rolling_max(market.high, close_valid, base_window), 1)
        base_low = shift(valid_rolling_min(market.low, close_valid, base_window), 1)
        compact = (base_high / base_low - 1.0) <= float(params.get("range_max", 0.30))

        volume_valid = np.isfinite(market.close) & np.isfinite(market.volume)
        base_volume = shift(valid_rolling_mean(market.volume, volume_valid, base_window), 1)
        prior_volume = shift(base_volume, base_window)
        dried_up = base_volume <= prior_volume * float(
            params.get("volume_contraction_max", 0.9)
        )

        ma20 = matrix_feature(market, "ma20")
        ma60 = matrix_feature(market, "ma60")
        ma20_holding = (market.close > ma20) & (ma20 >= shift(ma20, 5) * 0.995)
        ma60_intact = ma60 >= shift(ma60, 5) * 0.97

        breakout = market.close > base_high
        volume_ok = market.volume >= base_volume * float(
            params.get("breakout_vol_ratio", 1.2)
        )

        entry = (
            in_bottom_zone
            & compact
            & dried_up
            & ma20_holding
            & ma60_intact
            & breakout
            & volume_ok
            & (market.close > market.open)
            & (market.close > shift(market.high, 1))
        )

        exit_ = market.close < ma20

        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            entry_signal_ids=("signal_consolidation_breakout",),
            exit_signal_ids=("signal_ma20_break",),
        )


MATRIX_STRATEGY = ConsolidationBreakoutMatrixStrategy()
