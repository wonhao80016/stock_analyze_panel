"""Lorentzian-distance k-NN direction classifier.

Bars are counted in the symbol's own valid bars. Each feature is scaled to its min-max position
over the last norm_window bars, and a bar is classified by the k nearest neighbours under the
Lorentzian metric sum(log1p(|a-b|)): the prediction is the sum of their labels
sign(close[j + label_horizon] - close[j]).

Neighbours are sampled every sample_stride bars back from the newest bar whose label is known,
among the last CANDIDATE_WINDOW bars before the classified one. A bar is classified only when
the oldest bar it reads (neighbour window, normalisation window and feature warm-up) exists and
lies at most required_warmup_bars sessions of the matrix's session axis before it, so the
symbol may miss up to MISSED_SESSION_ALLOWANCE of those sessions. A prediction then depends only
on the last required_warmup_bars + 1 sessions (recursive features up to a seed weight below
1e-4), so the screener's short history and a full-market backtest agree; a symbol with less
history or more missed sessions is not classified in either. A bar whose normalisation window
holds a missing value or no range for any feature neither votes nor is classified, and a bar
with fewer usable neighbours than k (capped by the window's candidates) is not classified.
Equal distances prefer the more recent neighbour.

Not a port of jdehorty's TradingView "Machine Learning: Lorentzian Classification": the features
(CCI, ADX and WaveTrend are not in the matrix layer), normalisation and filters differ, labels
look forward, the neighbour search is an exact k-NN over a fixed window, and entries do not
wait for a signal flip.

Look-ahead safety: neighbour j is consulted from bar t only when j + label_horizon <= t;
loosening that silently turns the backtest into a look-ahead oracle.
"""

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    knn_direction_votes,
    make_signal_matrix,
    matrix_feature,
    recursive_warmup_bars,
    run_numba_parallel,
)
from app.backtest.matrix import (
    valid_shift as shift,
)

FEATURE_NAMES = ("rsi_14", "rsi_9", "kdj_k", "boll_position", "macd_hist_pct")
CANDIDATE_WINDOW = 120
MISSED_SESSION_ALLOWANCE = 40
_BOLLINGER_BARS = 20
_ATR_LOOKBACK = 10

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
    "limit": 500,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_lorentzian_bullish"]
EXIT_SIGNALS = ["signal_lorentzian_exit"]
STOP_LOSS = -0.06
MAX_HOLD_DAYS = 4


def _candidate_offsets(horizon: int, stride: int) -> np.ndarray:
    """Valid-bar distances from the classified bar to its candidate neighbours, newest first."""
    # Starting at horizon keeps every candidate's label known at the classified bar.
    return np.arange(horizon, CANDIDATE_WINDOW + 1, stride, dtype=np.int64)


def _own_bars_read(horizon: int, stride: int, norm_window: int) -> int:
    """Own valid bars a classified bar reads before itself, feature warm-ups included."""
    feature_bars = max(
        _BOLLINGER_BARS,
        *(recursive_warmup_bars(name) for name in FEATURE_NAMES),
    )
    oldest_neighbour = int(_candidate_offsets(horizon, stride)[-1])
    return max(
        oldest_neighbour + norm_window - 1 + feature_bars - 1,
        _ATR_LOOKBACK + recursive_warmup_bars("atr_pct") - 1,
    )


def _knn_prediction(market: MarketDataMatrix, params: dict, session_span: int) -> np.ndarray:
    """Sum of the nearest neighbours' labels per valid bar; NaN where a bar is not classified."""
    horizon = int(params.get("label_horizon", 4))
    stride = int(params.get("sample_stride", 4))
    norm_window = int(params.get("norm_window", 60))
    offsets = _candidate_offsets(horizon, stride)
    raw_features = np.stack(
        [matrix_feature(market, name) for name in FEATURE_NAMES], axis=-1
    ).astype(np.float32, copy=False)
    valid_bars = market.valid_bars
    return run_numba_parallel(
        lambda: knn_direction_votes(
            raw_features,
            market.close,
            market.session_ids,
            valid_bars.offsets,
            valid_bars.rows,
            offsets,
            min(int(params.get("neighbors", 8)), offsets.size),
            horizon,
            norm_window,
            _own_bars_read(horizon, stride, norm_window),
            session_span,
        )
    )


class LorentzianKnnMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"high", "low", "close"})

    def required_warmup_bars(self, params: dict) -> int:
        own_bars = _own_bars_read(
            int(params.get("label_horizon", 4)),
            int(params.get("sample_stride", 4)),
            int(params.get("norm_window", 60)),
        )
        return own_bars + MISSED_SESSION_ALLOWANCE

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        close = market.close
        prediction = _knn_prediction(market, params, self.required_warmup_bars(params))

        bullish = prediction >= float(params.get("min_prediction", 1.0))

        ma20 = matrix_feature(market, "ma20")
        entry = bullish & (close > ma20)

        if params.get("use_volatility_filter", True):
            atr_pct = matrix_feature(market, "atr_pct")
            entry &= atr_pct > shift(atr_pct, _ATR_LOOKBACK)
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
