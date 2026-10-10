"""超跌反弹 — RSI14 < 30 + 涨幅 > 1% + 站上 MA5, 超卖反弹信号"""

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    make_signal_matrix,
    matrix_feature,
    recursive_warmup_bars,
)
from app.backtest.matrix import (
    valid_shift as shift,
)

META = {
    "id": "oversold_reversal",
    "name": "超跌反轉",
    "description": "RSI14 < 30超賣 + 漲幅 > 1% + 站上MA5, 超賣反轉訊號",
    "tags": ["超跌", "反彈", "RSI"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [
        {"id": "use_rsi_filter", "label": "啟用RSI過濾", "type": "bool", "default": True},
        {
            "id": "rsi_max",
            "label": "RSI上限",
            "type": "float",
            "default": 30.0,
            "min": 10.0,
            "max": 50.0,
            "step": 1.0,
        },
        {"id": "use_change_filter", "label": "啟用漲幅過濾", "type": "bool", "default": True},
        {
            "id": "min_change",
            "label": "最低漲幅%",
            "type": "float",
            "default": 1.0,
            "min": 0.5,
            "max": 5.0,
            "step": 0.5,
        },
        {
            "id": "require_above_ma5",
            "label": "要求收盤價在MA5上方",
            "type": "bool",
            "default": True,
        },
    ],
    "scoring": {"change_pct": 0.4, "rsi_14": 0.3, "vol_ratio_5d": 0.3},
    "scoring_directions": {"rsi_14": "low", "momentum_5d": "low"},
    "order_by": "score",
    "descending": True,
    "limit": 500,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = []
EXIT_SIGNALS = ["signal_ma20_breakdown"]
STOP_LOSS = -0.05
MAX_HOLD_DAYS = 15


class OversoldReversalMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close"})

    def required_warmup_bars(self, params: dict) -> int:
        if params.get("use_rsi_filter", True):
            return max(60, recursive_warmup_bars("rsi_14"))
        return 60

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        entry = np.ones(market.shape, dtype=bool)
        if params.get("use_rsi_filter", True):
            entry &= matrix_feature(market, "rsi_14") < float(params.get("rsi_max", 30.0))
        if params.get("use_change_filter", True):
            entry &= (
                matrix_feature(market, "change_pct") > float(params.get("min_change", 1.0)) / 100.0
            )
        if params.get("require_above_ma5", True):
            entry &= market.close > matrix_feature(market, "ma5")
        ma20 = matrix_feature(market, "ma20")
        exit_ = (market.close < ma20) & (shift(market.close, 1) >= shift(ma20, 1))
        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            exit_signal_ids=("signal_ma20_breakdown",),
        )


MATRIX_STRATEGY = OversoldReversalMatrixStrategy()
