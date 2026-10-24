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
    cross_above,
    make_signal_matrix,
    matrix_feature,
    valid_rolling_mean,
    valid_rolling_min,
    valid_rolling_sum,
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
    ],
    "scoring": {"momentum_20d": 0.4, "vol_ratio_5d": 0.35, "change_pct": 0.25},
    "order_by": "score",
    "descending": True,
    "limit": 500,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_pullback_resume"]
EXIT_SIGNALS = ["signal_ma20_break"]
STOP_LOSS = -0.06
MAX_HOLD_DAYS = 15


def _traded_volume(market: MarketDataMatrix) -> np.ndarray:
    """Volume on each bar's own share basis, so a later split or capital reduction cannot move it.

    Share-unit (Taiwan) volume is restated by later share events; amount / raw_close is that day's.
    Duplicated in swing_trend_confirmation: the engine loads strategy files outside their package.
    """
    if market.volume_unit != "shares":
        return market.volume
    raw_close = market.field("raw_close")
    shares = np.full(market.shape, np.nan, dtype=np.float64)
    np.divide(
        market.field("amount"), raw_close, out=shares, where=np.isfinite(raw_close) & (raw_close > 0)
    )
    return shares


def _liquid(market: MarketDataMatrix, params: dict) -> np.ndarray:
    liquid = np.ones(market.shape, dtype=bool)
    min_amount = float(params.get("min_amount", 100_000_000.0))
    if min_amount > 0:
        liquid &= market.field("amount") >= min_amount
    # A share-count minimum saved before the traded-value rule still applies on top of it.
    min_volume = float(params.get("min_volume", 0.0))
    if min_volume > 0:
        liquid &= _traded_volume(market) >= min_volume
    return liquid


class PullbackResumeMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"open", "high", "low", "close", "volume", "amount", "raw_close"})

    def required_warmup_bars(self, params: dict) -> int:
        # Own bars read (MA60, or two stacked pullback windows) plus 40 that absorb sessions the
        # symbol missed inside the loaded window of shared sessions.
        return max(60, 2 * int(params.get("pullback_window", 10)) + 1) + 40

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        window = int(params.get("pullback_window", 10))

        ma5 = matrix_feature(market, "ma5")
        ma10 = matrix_feature(market, "ma10")
        ma20 = matrix_feature(market, "ma20")
        ma60 = matrix_feature(market, "ma60")

        close_valid = np.isfinite(market.close)
        below_ma5 = shift((market.close < ma5).astype(np.float32), 1, close_valid)
        had_pullback = valid_rolling_sum(below_ma5, close_valid, window) > 0.0

        held_ma20 = shift(valid_rolling_min(market.close - ma20, close_valid, window), 1) >= 0.0

        volume_valid = np.isfinite(market.close) & np.isfinite(market.volume)
        pullback_volume = shift(valid_rolling_mean(market.volume, volume_valid, window), 1)
        prior_volume = shift(pullback_volume, window)
        dried_up = pullback_volume < prior_volume * float(
            params.get("pullback_vol_ratio_max", 1.0)
        )

        reclaim_ma5 = cross_above(market.close, ma5, market.close)
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
            & _liquid(market, params)
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
