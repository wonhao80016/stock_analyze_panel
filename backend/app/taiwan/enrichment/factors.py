"""Taiwan margin (資券) indicators."""
from __future__ import annotations


def short_margin_ratio(short_balance: int | None, margin_balance: int | None) -> float | None:
    """Short balance over margin balance in percent, rounded to 2 places; None without a positive margin balance."""
    if short_balance is None or margin_balance is None or margin_balance <= 0:
        return None
    return round(short_balance / margin_balance * 100.0, 2)
