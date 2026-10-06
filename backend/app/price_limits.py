"""Price-limit rules shared by indicators, backtests, and APIs.

Covers A-share board rates directly; Taiwan symbols are delegated to
``app.taiwan.market_rules.PriceLimitModel`` (see ``taiwan_price_limit_pct``,
``board_limit_pct``, ``price_limit_pct``) or, where the per-instrument class
matters, to ``app.taiwan.universe.models.MarketProfileBridge`` (see
``numpy_limit_pct_vectors``). ``polars_price_limit_pct`` is the lone
exception: as a pure Polars expression it cannot reach Security Master
data, so it returns null for Taiwan symbols and relies on callers to
coalesce with an authoritative source (see
``app.indicators.pipeline.taiwan_limit_profile``).

Absence of a limit is NaN in the NumPy vectors and null in the Polars
expressions; 0.0 is never used, because a zero band would read as
"locked at every price" to the limit-lock consumers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

import numpy as np
import polars as pl

MAIN_BOARD_ST_LIMIT_CHANGE_DATE = date(2026, 7, 6)

MAIN_BOARD_LIMIT = 0.10
LEGACY_MAIN_BOARD_ST_LIMIT = 0.05
GROWTH_BOARD_LIMIT = 0.20
BEIJING_BOARD_LIMIT = 0.30


def is_risk_warning_name(name: str | None) -> bool:
    return "ST" in str(name or "").upper()


def taiwan_price_limit_pct(
    symbol: str,
    *,
    limit_class: Any | None = None,
) -> float | None:
    """Delegate Taiwan price limit percentages strictly to Taiwan PriceLimitModel.

    PriceLimitModel is the single authoritative source of truth.
    Does not let symbol suffix alone dictate 10%.
    """
    from app.taiwan.market_rules import PriceLimitClass, PriceLimitModel

    if limit_class is not None:
        if isinstance(limit_class, str):
            limit_class = PriceLimitClass(limit_class)
        return PriceLimitModel.get_limit_pct(limit_class)

    return PriceLimitModel.get_limit_pct(PriceLimitClass.ORDINARY_TEN_PERCENT)


def board_limit_pct(
    symbol: str,
    *,
    limit_class: Any | None = None,
) -> float | None:
    if symbol.endswith((".TWSE", ".TPEX")):
        return taiwan_price_limit_pct(symbol, limit_class=limit_class)
    if symbol.endswith(".BJ"):
        return BEIJING_BOARD_LIMIT
    if symbol.startswith(("300", "301", "688", "689")):
        return GROWTH_BOARD_LIMIT
    return MAIN_BOARD_LIMIT


def price_limit_pct(
    symbol: str,
    trade_date: date,
    *,
    is_risk_warning: bool = False,
    limit_class: Any | None = None,
) -> float | None:
    if symbol.endswith((".TWSE", ".TPEX")):
        return taiwan_price_limit_pct(symbol, limit_class=limit_class)
    base = board_limit_pct(symbol)
    if (
        base == MAIN_BOARD_LIMIT
        and is_risk_warning
        and trade_date < MAIN_BOARD_ST_LIMIT_CHANGE_DATE
    ):

        return LEGACY_MAIN_BOARD_ST_LIMIT
    return base


def polars_price_limit_pct(
    symbol: pl.Expr,
    trade_date: pl.Expr,
    is_risk_warning: pl.Expr,
) -> pl.Expr:
    """Return a vectorized Polars expression for the effective daily limit.

    Taiwan symbols (``.TWSE``/``.TPEX``) return null: this expression has no
    access to Security Master ETF leverage/underlying-scope data, so it
    cannot derive a correct percentage here. Callers must coalesce with an
    authoritative Taiwan source (e.g. ``taiwan_limit_profile``) and treat
    null as fail-closed (NO_LIMIT), never as a silent 10%.
    """
    is_taiwan = symbol.str.ends_with(".TWSE") | symbol.str.ends_with(".TPEX")
    is_growth = symbol.str.starts_with("300") | symbol.str.starts_with("301")
    is_star = symbol.str.starts_with("688") | symbol.str.starts_with("689")
    is_beijing = symbol.str.ends_with(".BJ")
    is_non_main = is_growth | is_star | is_beijing
    base = (
        pl.when(is_growth | is_star).then(GROWTH_BOARD_LIMIT)
        .when(is_beijing).then(BEIJING_BOARD_LIMIT)
        .otherwise(MAIN_BOARD_LIMIT)
    )
    legacy_main_st = (
        is_risk_warning.fill_null(False)
        & ~is_non_main
        & (trade_date < pl.lit(MAIN_BOARD_ST_LIMIT_CHANGE_DATE))
    )
    a_share_pct = pl.when(legacy_main_st).then(LEGACY_MAIN_BOARD_ST_LIMIT).otherwise(base)
    return (
        pl.when(is_taiwan).then(pl.lit(None, dtype=pl.Float64))
        .otherwise(a_share_pct)
        .cast(pl.Float64)
    )


def polars_is_risk_warning_name(name: pl.Expr) -> pl.Expr:
    """Return whether an instrument name contains the ST risk-warning marker."""
    return name.fill_null("").str.to_uppercase().str.contains("ST", literal=True)


def polars_limit_price(previous: pl.Expr, limit_pct: pl.Expr, *, up: bool) -> pl.Expr:
    """Calculate exchange half-up prices with integer-cent arithmetic."""
    sign = 1 if up else -1
    numerator = ((1 + sign * limit_pct) * 100).round(0).cast(pl.Int64)
    cents = (previous * 100 + 0.5).floor().cast(pl.Int64)
    return ((cents * numerator + 50) // 100) / 100


TAIWAN_SYMBOL_SUFFIXES = (".TWSE", ".TPEX")


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
        if str(symbol).endswith(TAIWAN_SYMBOL_SUFFIXES)
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
        if str(symbol).endswith(TAIWAN_SYMBOL_SUFFIXES)
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


def _limit_pct_value(symbol: str, taiwan_pct: Mapping[str, float | None]) -> float:
    pct = taiwan_pct[symbol] if symbol in taiwan_pct else board_limit_pct(symbol)
    return np.nan if pct is None else float(pct)


def numpy_limit_pct_vectors(
    symbols: Sequence[str],
    names: Sequence[str],
) -> tuple[np.ndarray, np.ndarray]:
    """Return pre/post-change vectors once; callers select one per date."""
    taiwan_pct = taiwan_limit_pct_by_symbol(symbols)
    current = np.fromiter(
        (_limit_pct_value(str(symbol), taiwan_pct) for symbol in symbols),
        dtype=np.float64,
        count=len(symbols),
    )
    legacy = current.copy()
    for asset_id, (symbol, name) in enumerate(zip(symbols, names, strict=True)):
        if str(symbol) in taiwan_pct:
            continue
        if current[asset_id] == MAIN_BOARD_LIMIT and is_risk_warning_name(name):
            legacy[asset_id] = LEGACY_MAIN_BOARD_ST_LIMIT
    return legacy, current


def numpy_price_limit_matrix(
    trading_dates: Sequence[date],
    symbols: Sequence[str],
    names: Sequence[str],
) -> np.ndarray:
    """Build a float32 time-by-asset matrix only for strategies that request it."""
    result = np.empty((len(trading_dates), len(symbols)), dtype=np.float32)
    return write_numpy_price_limit_matrix(result, trading_dates, symbols, names)


def write_numpy_price_limit_matrix(
    target: np.ndarray,
    trading_dates: Sequence[date],
    symbols: Sequence[str],
    names: Sequence[str],
    *,
    valid: np.ndarray | None = None,
) -> np.ndarray:
    """Write date-aware limits directly into an existing matrix or memmap."""
    expected_shape = (len(trading_dates), len(symbols))
    if target.shape != expected_shape:
        raise ValueError("price-limit output shape mismatch")
    if valid is not None and valid.shape != expected_shape:
        raise ValueError("price-limit validity mask shape mismatch")

    legacy, current = numpy_limit_pct_vectors(symbols, names)
    target[:] = current.astype(np.float32, copy=False)
    legacy_rows = np.fromiter(
        (value < MAIN_BOARD_ST_LIMIT_CHANGE_DATE for value in trading_dates),
        dtype=bool,
        count=len(trading_dates),
    )
    if legacy_rows.any():
        target[legacy_rows] = legacy.astype(np.float32, copy=False)
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


def numpy_limit_price(
    previous: np.ndarray,
    limit_pct: np.ndarray,
    *,
    up: bool,
) -> np.ndarray:
    """NumPy counterpart of :func:`polars_limit_price`."""
    sign = 1 if up else -1
    result = np.full(previous.shape, np.nan, dtype=np.float64)
    # A NaN percentage casts to INT64_MIN instead of propagating, so a
    # no-limit instrument must be excluded rather than arithmetically carried.
    usable = np.isfinite(previous) & np.isfinite(limit_pct)
    if not usable.any():
        return result
    numerator = np.rint((1.0 + sign * limit_pct[usable]) * 100.0).astype(np.int64)
    cents = np.floor(previous[usable] * 100.0 + 0.5).astype(np.int64)
    result[usable] = ((cents * numerator + 50) // 100).astype(np.float64) / 100.0
    return result
