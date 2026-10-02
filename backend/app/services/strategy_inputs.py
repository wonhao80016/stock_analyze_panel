"""What the strategy data loaders report about their inputs; dependency-free on purpose."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

HISTORY = "history"
CURRENT = "current"


@dataclass(frozen=True)
class InputStatus:
    """What one loader returned: state is ok|missing|error|empty|provided|unverified.

    exchanges_at_t: exchange suffixes among rows dated T; dates: distinct dates loaded.
    """

    state: str = "unverified"
    rows: int = 0
    exchanges_at_t: frozenset[str] = frozenset()
    dates: tuple[date, ...] = ()
