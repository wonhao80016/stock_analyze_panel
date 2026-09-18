"""Lorentzian-distance k-NN direction classifier.

Each bar is classified by finding its k nearest historical neighbours under the
Lorentzian metric sum(log1p(|a-b|)) over normalised oscillator features, then
summing those neighbours' realised forward direction labels.

Clean-room implementation from the published algorithm description. The feature
set uses this project's own matrix features rather than the original indicator's
(CCI/ADX/WaveTrend are not available in the matrix layer), so this is a strategy
of the same family, not a port of any specific implementation.

Look-ahead safety: labels are built from close[t+horizon], so a neighbour j may
only be consulted from bar t when j + horizon <= t. The candidate mask below
enforces that; loosening it silently turns the backtest into a look-ahead oracle.
"""

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    make_signal_matrix,
    matrix_feature,
    rolling_max,
    rolling_min,
)
from app.backtest.matrix import (
    valid_shift as shift,
)

FEATURE_NAMES = ("rsi_14", "rsi_9", "kdj_k", "boll_position", "macd_hist_pct")

META = {
    "id": "lorentzian_knn",
    "name": "Lorentzian KNN 分類",
    "description": "以 Lorentzian 距離對震盪指標做 K 近鄰方向分類",
    "tags": ["機器學習", "KNN", "分類"],
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "params": [
        {
            "id": "neighbors",
            "label": "近鄰數 K",
            "type": "int",
            "default": 8,
            "min": 3,
            "max": 32,
            "step": 1,
        },
        {
            "id": "label_horizon",
            "label": "標籤前瞻根數",
            "type": "int",
            "default": 4,
            "min": 1,
            "max": 20,
            "step": 1,
        },
        {
            "id": "sample_stride",
            "label": "候選鄰居取樣間隔",
            "type": "int",
            "default": 4,
            "min": 1,
            "max": 20,
            "step": 1,
        },
        {
            "id": "norm_window",
            "label": "特徵正規化視窗",
            "type": "int",
            "default": 60,
            "min": 20,
            "max": 240,
            "step": 10,
        },
        {
            "id": "min_prediction",
            "label": "最低看多分數",
            "type": "float",
            "default": 1.0,
            "min": 0.0,
            "max": 8.0,
            "step": 1.0,
        },
        {
            "id": "use_volatility_filter",
            "label": "啟用波動率濾網",
            "type": "bool",
            "default": True,
        },
        {
            "id": "use_regime_filter",
            "label": "啟用趨勢濾網",
            "type": "bool",
            "default": True,
        },
    ],
    "scoring": {"momentum_20d": 0.4, "rsi_14": 0.3, "vol_ratio_5d": 0.3},
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_lorentzian_bullish"]
EXIT_SIGNALS = ["signal_lorentzian_exit"]
STOP_LOSS = -0.06
MAX_HOLD_DAYS = 4


def _normalise(values: np.ndarray, window: int) -> np.ndarray:
    low = rolling_min(values, window)
    high = rolling_max(values, window)
    # A flat window has zero span; NaN routes it to the 0.5 neutral fallback
    # below instead of dividing by zero.
    span = np.where(high > low, high - low, np.nan)
    scaled = (values - low) / span
    return np.where(np.isfinite(scaled), scaled, 0.5).astype(np.float32)


class LorentzianKnnMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"high", "low", "close"})

    def required_warmup_bars(self, params: dict) -> int:
        return int(params.get("norm_window", 60)) + 60

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        bars, assets = market.shape
        k = int(params.get("neighbors", 8))
        horizon = int(params.get("label_horizon", 4))
        stride = int(params.get("sample_stride", 4))
        norm_window = int(params.get("norm_window", 60))

        features = np.stack(
            [_normalise(matrix_feature(market, n), norm_window) for n in FEATURE_NAMES],
            axis=-1,
        )

        close = market.close
        future = np.full_like(close, np.nan)
        if horizon < bars:
            future[:-horizon] = close[horizon:]
        labels = np.sign(future - close).astype(np.float32)
        labels[~np.isfinite(labels)] = 0.0

        candidate_idx = np.arange(0, bars, stride, dtype=np.int64)
        bar_idx = np.arange(bars, dtype=np.int64)
        # A neighbour's label is only knowable once horizon bars have elapsed.
        usable = candidate_idx[None, :] + horizon <= bar_idx[:, None]

        prediction = np.zeros((bars, assets), dtype=np.float32)
        for asset in range(assets):
            asset_features = features[:, asset, :]
            candidates = asset_features[candidate_idx]
            deltas = np.abs(asset_features[:, None, :] - candidates[None, :, :])
            distance = np.log1p(deltas).sum(axis=-1)
            distance = np.where(usable, distance, np.inf)

            take = min(k, candidates.shape[0])
            nearest = np.argpartition(distance, take - 1, axis=1)[:, :take]
            chosen = labels[candidate_idx[nearest], asset]
            reachable = np.take_along_axis(distance, nearest, axis=1)
            prediction[:, asset] = np.where(np.isfinite(reachable), chosen, 0.0).sum(axis=1)

        bullish = prediction >= float(params.get("min_prediction", 1.0))

        ma20 = matrix_feature(market, "ma20")
        entry = bullish & (close > ma20)

        if params.get("use_volatility_filter", True):
            atr_pct = matrix_feature(market, "atr_pct")
            entry &= atr_pct > shift(atr_pct, 10)
        if params.get("use_regime_filter", True):
            entry &= ma20 >= shift(ma20, 5) * 0.999

        exit_ = (prediction < 0.0) | (close < ma20)

        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(exit_, 0, -1).astype(np.int16),
            entry_signal_ids=("signal_lorentzian_bullish",),
            exit_signal_ids=("signal_lorentzian_exit",),
        )


MATRIX_STRATEGY = LorentzianKnnMatrixStrategy()
