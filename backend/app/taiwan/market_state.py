"""Whether TAIEX closed below its moving average, judged from local stores only."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from app.taiwan.benchmarks import TAIEX
from app.taiwan.daily_store import TaiwanDailyStore

MA_WINDOW = 20

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"

REASON_INDEX_MISSING = "index_missing"
REASON_INSUFFICIENT_HISTORY = "insufficient_history"
REASON_INDEX_GAP = "index_gap"


def compute_market_state(data_dir: Path, as_of: date, window: int = MA_WINDOW) -> dict[str, Any]:
    """TAIEX close on as_of against its simple average over the last *window* trading days.

    Trading days come from the stock daily partitions and the window includes as_of.
    A close equal to the average is not below it.
    """
    state: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "index_symbol": TAIEX.symbol,
        "close": None,
        "ma": None,
        "window": window,
        "below_ma": None,
        "status": STATUS_UNAVAILABLE,
        "reason": None,
    }
    root = Path(data_dir) / "taiwan"
    index_dir = root / "index_daily"
    daily_dir = root / "daily"
    # TaiwanDailyStore creates its directory on construction; a read must not.
    trading_days = (
        [d for d in TaiwanDailyStore(daily_dir).available_dates() if d <= as_of]
        if daily_dir.is_dir()
        else []
    )
    window_days = trading_days[-window:]
    closes: dict[date, float] = {}
    if index_dir.is_dir():
        start = window_days[0] if window_days else as_of
        frame = TaiwanDailyStore(index_dir).scan_range([TAIEX.symbol], start, as_of)
        closes = {
            d: float(c)
            for d, c in zip(frame["date"].to_list(), frame["close"].to_list(), strict=True)
            if c is not None
        }

    if as_of not in closes:
        state["reason"] = REASON_INDEX_MISSING
        return state
    if len(trading_days) < window:
        state["reason"] = REASON_INSUFFICIENT_HISTORY
        return state
    expected = set(window_days)
    if not expected.issubset(closes) or not set(closes).issubset(expected):
        state["reason"] = REASON_INDEX_GAP
        return state

    close = closes[as_of]
    ma = sum(closes[d] for d in window_days) / window
    state.update(close=close, ma=ma, below_ma=close < ma, status=STATUS_OK)
    return state
