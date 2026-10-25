"""Price-limit rules shared by backtests and APIs.

Taiwan symbols resolve their limit through ``app.taiwan.universe.models.MarketProfileBridge``
(see ``numpy_limit_pct_vector``). Other symbols have no limit rule.

Absence of a limit is NaN in the NumPy vectors; 0.0 is never used, because a zero band
would read as "locked at every price" to the limit-lock consumers.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import numpy as np

from app.volume_units import TAIWAN_SUFFIXES


def taiwan_limit_pct_by_symbol(symbols: Sequence[str]) -> dict[str, float | None]:
    """Resolve each Taiwan symbol's own limit through the Security Master.

    ``None`` means either a genuine no-limit instrument or one the master
    refuses to classify; the two are indistinguishable here and consumers do
    not agree on what to do with it - the screener drops such rows, the
    backtest treats the bar as unlocked. Non-Taiwan symbols are absent from
    the result, so the master is never loaded for them.
    """
    taiwan = sorted({
        str(symbol)
        for symbol in symbols
        if str(symbol).endswith(TAIWAN_SUFFIXES)
    })
    if not taiwan:
        return {}

    from app.taiwan.universe import get_security_master
    from app.taiwan.universe.models import MarketProfileBridge

    master = get_security_master()
    resolved: dict[str, float | None] = {}
    for symbol in taiwan:
        instrument = master.get_instrument(symbol)
        if instrument is None:
            resolved[symbol] = None
            continue
        try:
            resolved[symbol] = MarketProfileBridge.get_price_limit_pct(instrument)
        except ValueError:
            resolved[symbol] = None
    return resolved


def taiwan_ipo_windows(symbols: Sequence[str]) -> dict[str, tuple[date, date]]:
    """First and last no-limit session of each Taiwan initial listing among *symbols*, on the stored calendar.

    A symbol without a window, or whose rule is unknown, is absent.
    """
    taiwan = sorted({
        str(symbol)
        for symbol in symbols
        if str(symbol).endswith(TAIWAN_SUFFIXES)
    })
    if not taiwan:
        return {}

    from app.taiwan.daily_store import listing_sessions
    from app.taiwan.universe import get_security_master
    from app.taiwan.universe.models import MarketProfileBridge

    master = get_security_master()
    sessions = None
    windows: dict[str, tuple[date, date]] = {}
    for symbol in taiwan:
        instrument = master.get_instrument(symbol)
        if instrument is None or instrument.instrument_type != "stock":
            continue
        if sessions is None:
            sessions = listing_sessions()
        try:
            window = MarketProfileBridge.ipo_no_limit_window(instrument, sessions)
        except ValueError:
            continue
        if window is not None:
            windows[symbol] = window
    return windows


def numpy_limit_pct_vector(symbols: Sequence[str]) -> np.ndarray:
    """Each symbol's daily limit percentage, NaN without a limit."""
    taiwan_pct = taiwan_limit_pct_by_symbol(symbols)
    return np.fromiter(
        (np.nan if (pct := taiwan_pct.get(str(symbol))) is None else float(pct) for symbol in symbols),
        dtype=np.float64,
        count=len(symbols),
    )


def write_numpy_price_limit_matrix(
    target: np.ndarray,
    trading_dates: Sequence[date],
    symbols: Sequence[str],
    *,
    valid: np.ndarray | None = None,
) -> np.ndarray:
    """Write date-aware limits directly into an existing matrix or memmap."""
    expected_shape = (len(trading_dates), len(symbols))
    if target.shape != expected_shape:
        raise ValueError("price-limit output shape mismatch")
    if valid is not None and valid.shape != expected_shape:
        raise ValueError("price-limit validity mask shape mismatch")

    target[:] = numpy_limit_pct_vector(symbols).astype(np.float32, copy=False)
    windows = taiwan_ipo_windows(symbols)
    for asset_id, symbol in enumerate(symbols):
        window = windows.get(str(symbol))
        if window is not None:
            rows = np.fromiter(
                (window[0] <= value <= window[1] for value in trading_dates), dtype=bool, count=len(trading_dates)
            )
            target[rows, asset_id] = np.nan
    if valid is not None:
        target[~valid] = np.nan
    return target
