"""New-low reversal: the intraday low breaks the prior 60-day low, the close recovers above it,
on a bullish bar with expanding volume."""

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    make_signal_matrix,
    matrix_feature,
    valid_rolling_min,
)
from app.backtest.matrix import (
    valid_shift as shift,
)

META = {
    "id": "n_day_low_reversal",
    "name": "新低反轉",
    "description": "盤中跌破前60日低點後收盤站回其上, 當日收陽放量, 反轉訊號",
    "tags": ["反轉", "新低"],
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "params": [
        {
            "id": "require_n_day_low",
            "label": "要求跌破60日低點後收回",
            "type": "bool",
            "default": True,
        },
        {"id": "require_bullish_candle", "label": "要求收陽", "type": "bool", "default": True},
        {"id": "use_volume_filter", "label": "啟用量比過濾", "type": "bool", "default": True},
        {
            "id": "vol_ratio_min",
            "label": "最低量比",
            "type": "float",
            "default": 1.5,
            "min": 0.5,
            "max": 5.0,
            "step": 0.1,
        },
    ],
    "scoring": {"change_pct": 0.4, "vol_ratio_5d": 0.3, "momentum_5d": 0.3},
    "scoring_directions": {"rsi_14": "low", "momentum_5d": "low"},
    "order_by": "score",
    "descending": True,
    "limit": 500,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_n_day_low_reclaim"]
EXIT_SIGNALS = ["signal_ma20_breakdown"]
STOP_LOSS = -0.06
MAX_HOLD_DAYS = 15


class NDayLowReversalMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"open", "low", "close", "volume"})

    def required_warmup_bars(self, params: dict) -> int:
        del params
        # Own bars read (the prior 60-bar low and today) plus 40 that absorb sessions the symbol
        # missed inside the loaded window of shared sessions.
        return 61 + 40

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        entry = np.ones(market.shape, dtype=bool)
        if params.get("require_n_day_low", True):
            close_valid = np.isfinite(market.close)
            prior_low = shift(valid_rolling_min(market.low, close_valid, 60), 1)
            entry &= (market.low < prior_low) & (market.close > prior_low)
        if params.get("require_bullish_candle", True):
            entry &= market.close > market.open
        if params.get("use_volume_filter", True):
            entry &= matrix_feature(market, "vol_ratio_5d") >= float(
                params.get("vol_ratio_min", 1.5)
            )
        ma20 = matrix_feature(market, "ma20")
        exit_ = (market.close < ma20) & (shift(market.close, 1) >= shift(ma20, 1))
        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            entry_signal_ids=("signal_n_day_low_reclaim",),
            exit_signal_ids=("signal_ma20_breakdown",),
        )


MATRIX_STRATEGY = NDayLowReversalMatrixStrategy()
