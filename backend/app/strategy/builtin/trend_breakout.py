"""趋势突破 — MA60上方 + 60日收盘新高 + 放量"""

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    cross_below,
    make_signal_matrix,
    matrix_feature,
)

META = {
    "id": "trend_breakout",
    "name": "趨勢突破",
    "description": "MA60上方 + 60日收盘新高 + 量能 ≥ 2倍均量",
    "tags": ["趨勢", "突破", "放量"],
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3,
        "amount_min": 0.2e8,
    },
    "params": [
        {
            "id": "require_above_ma60",
            "label": "要求收盤價在MA60上方",
            "type": "bool",
            "default": True,
        },
        {"id": "require_n_day_high", "label": "要求60日收盤新高", "type": "bool", "default": True},
        {"id": "use_volume_filter", "label": "啟用量比過濾", "type": "bool", "default": True},
        {
            "id": "vol_ratio_min",
            "label": "最低量比",
            "type": "float",
            "default": 2.0,
            "min": 0.5,
            "max": 10.0,
            "step": 0.1,
        },
    ],
    "scoring": {"momentum_60d": 0.4, "vol_ratio_5d": 0.3, "change_pct": 0.3},
    "order_by": "score",
    "descending": True,
    "limit": 500,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_n_day_high"]
EXIT_SIGNALS = ["signal_ma20_breakdown"]
STOP_LOSS = -0.08
MAX_HOLD_DAYS = 20


class TrendBreakoutMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close", "volume"})

    def required_warmup_bars(self, params: dict) -> int:
        del params
        # Own bars read (MA60, the 60-day high and the scored 60-day momentum) plus 40 that
        # absorb sessions the symbol missed inside the loaded window of shared sessions.
        return 61 + 40

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        entry = np.ones(market.shape, dtype=bool)
        if params.get("require_above_ma60", True):
            entry &= market.close > matrix_feature(market, "ma60")
        if params.get("require_n_day_high", True):
            entry &= market.close >= matrix_feature(market, "high_60d")
        if params.get("use_volume_filter", True):
            entry &= matrix_feature(market, "vol_ratio_5d") >= float(
                params.get("vol_ratio_min", 2.0)
            )
        ma20 = matrix_feature(market, "ma20")
        exit_ = cross_below(market.close, ma20, market.close)
        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            entry_signal_ids=("signal_n_day_high",),
            exit_signal_ids=("signal_ma20_breakdown",),
        )


MATRIX_STRATEGY = TrendBreakoutMatrixStrategy()
