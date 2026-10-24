"""High turnover surge: turnover above 2% of issued shares net of private placements and a gain above 3%."""

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    cross_below,
    make_signal_matrix,
    matrix_feature,
)

DEFAULT_MIN_TURNOVER = 2.0

META = {
    "id": "high_turnover_surge",
    "name": "高換手拉升",
    "description": "換手率(成交股數÷已發行普通股扣除私募股) > 2% 且漲幅 > 3%, 資金活躍; 換手率自每日發布起保留, 不能用於更早的日期或回測",
    "tags": ["換手率", "放量", "資金"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [
        {"id": "use_turnover_filter", "label": "啟用換手率過濾", "type": "bool", "default": True},
        {
            "id": "min_turnover",
            "label": "最低換手率%",
            "type": "float",
            "default": DEFAULT_MIN_TURNOVER,
            "min": 1.0,
            "max": 20.0,
            "step": 0.5,
        },
        {"id": "use_change_filter", "label": "啟用漲幅過濾", "type": "bool", "default": True},
        {
            "id": "min_change",
            "label": "最低漲幅%",
            "type": "float",
            "default": 3.0,
            "min": 1.0,
            "max": 10.0,
            "step": 0.5,
        },
    ],
    "scoring": {"turnover_rate": 0.4, "change_pct": 0.3, "momentum_5d": 0.3},
    "order_by": "score",
    "descending": True,
    "limit": 500,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_high_turnover_surge_entry"]
EXIT_SIGNALS = ["signal_ma20_breakdown"]
STOP_LOSS = -0.05
MAX_HOLD_DAYS = 10


class HighTurnoverSurgeMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close", "turnover_rate"})

    def required_coverage_fields(self, params: dict) -> frozenset[str]:
        """Fields an entry needs; a screened or backtested session failing session_coverage is refused."""
        return (
            frozenset({"turnover_rate"}) if params.get("use_turnover_filter", True) else frozenset()
        )

    def required_warmup_bars(self, params: dict) -> int:
        del params
        return 60

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        entry = np.ones(market.shape, dtype=bool)
        if params.get("use_turnover_filter", True):
            entry &= matrix_feature(market, "turnover_rate") > float(
                params.get("min_turnover", DEFAULT_MIN_TURNOVER)
            )
        if params.get("use_change_filter", True):
            entry &= (
                matrix_feature(market, "change_pct") > float(params.get("min_change", 3.0)) / 100.0
            )
        ma20 = matrix_feature(market, "ma20")
        exit_ = cross_below(market.close, ma20, market.close)
        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            entry_signal_ids=("signal_high_turnover_surge_entry",),
            exit_signal_ids=("signal_ma20_breakdown",),
        )


MATRIX_STRATEGY = HighTurnoverSurgeMatrixStrategy()
