"""Bollinger squeeze breakout: band width compressed to a low percentile, then
the first volume-confirmed close above the upper band."""

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    make_signal_matrix,
    matrix_feature,
    valid_rolling_mean,
    valid_rolling_quantile,
    valid_rolling_sum,
)
from app.backtest.matrix import (
    valid_shift as shift,
)

META = {
    "id": "bollinger_squeeze",
    "name": "布林收斂突破",
    "description": "布林帶寬壓縮至近80日低分位後, 首根帶量突破上軌",
    "tags": ["布林", "突破", "波動收斂"],
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "params": [
        {
            "id": "squeeze_percentile",
            "label": "帶寬分位門檻",
            "type": "float",
            "default": 0.20,
            "min": 0.05,
            "max": 0.50,
            "step": 0.05,
        },
        {
            "id": "squeeze_lookback",
            "label": "帶寬分位回看天數",
            "type": "int",
            "default": 80,
            "min": 20,
            "max": 200,
            "step": 10,
        },
        {
            "id": "vol_ratio_min",
            "label": "突破日最低量倍數",
            "type": "float",
            "default": 1.5,
            "min": 1.0,
            "max": 5.0,
            "step": 0.1,
        },
        {
            "id": "close_position_min",
            "label": "收盤位於K棒高檔比例",
            "type": "float",
            "default": 0.7,
            "min": 0.0,
            "max": 1.0,
            "step": 0.05,
        },
        {
            "id": "first_breakout_window",
            "label": "首根突破認定窗口",
            "type": "int",
            "default": 5,
            "min": 1,
            "max": 20,
            "step": 1,
        },
    ],
    "scoring": {"momentum_20d": 0.4, "vol_ratio_5d": 0.3, "change_pct": 0.3},
    "order_by": "score",
    "descending": True,
    "limit": 500,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_boll_squeeze_breakout"]
EXIT_SIGNALS = ["signal_boll_mid_break"]
STOP_LOSS = -0.07
MAX_HOLD_DAYS = 20


class BollingerSqueezeMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"open", "high", "low", "close", "volume"})

    def required_warmup_bars(self, params: dict) -> int:
        # The squeeze needs lookback + 20 of the symbol's own bars; the other 40 absorb sessions
        # it missed inside the loaded window of shared sessions.
        return int(params.get("squeeze_lookback", 80)) + 60

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        close_valid = np.isfinite(market.close)
        width = matrix_feature(market, "boll_width")
        lookback = int(params.get("squeeze_lookback", 80))
        percentile = float(params.get("squeeze_percentile", 0.20))
        threshold = valid_rolling_quantile(width, close_valid, lookback, percentile)
        squeezed = shift(width, 1) <= shift(threshold, 1)

        upper = matrix_feature(market, "boll_upper")
        breakout = market.close > upper

        # A squeeze releases once; later bars above the band are continuation, not entry.
        window = int(params.get("first_breakout_window", 5))
        prior_breakouts = valid_rolling_sum(
            shift(breakout.astype(np.float32), 1, close_valid),
            close_valid,
            window,
        )
        first_breakout = prior_breakouts == 0.0

        volume_valid = np.isfinite(market.close) & np.isfinite(market.volume)
        vol_ma = valid_rolling_mean(market.volume, volume_valid, 20)
        volume_ok = market.volume >= vol_ma * float(params.get("vol_ratio_min", 1.5))

        close_position = matrix_feature(market, "close_position")
        position_ok = close_position >= float(params.get("close_position_min", 0.7))

        entry = (
            squeezed
            & breakout
            & first_breakout
            & volume_ok
            & position_ok
            & (market.close > market.open)
            & (market.close > shift(market.high, 1))
            & (market.close > shift(market.close, 1))
        )

        exit_ = market.close < matrix_feature(market, "ma20")

        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            entry_signal_ids=("signal_boll_squeeze_breakout",),
            exit_signal_ids=("signal_boll_mid_break",),
        )


MATRIX_STRATEGY = BollingerSqueezeMatrixStrategy()
