"""Keeps the literal benchmark-index copies in sync with app.taiwan.benchmarks.

preferences / quote_service / sector_monitor each repeat the benchmark symbols
instead of importing app.taiwan.benchmarks, so none of them imports app.taiwan at
module scope. Nothing but this test enforces the mirroring, and drift there
silently marks a user's index monitor targets unavailable. api/overview.py and
services/market_overview_builder.py still carry the pre-Taiwan A-share list and are
deliberately out of scope here.
"""
from __future__ import annotations

from app.services import preferences, sector_monitor
from app.services.quote_service import QuoteService
from app.taiwan.benchmarks import BENCHMARK_INDICES

CANONICAL = "app.taiwan.benchmarks.BENCHMARK_INDICES"
CANONICAL_SYMBOLS = set(BENCHMARK_INDICES)
CANONICAL_NAMES = {symbol: index.name for symbol, index in BENCHMARK_INDICES.items()}


def _drift(copy: str, actual: set[str]) -> str:
    return (
        f"{copy} drifted from {CANONICAL}: "
        f"missing={sorted(CANONICAL_SYMBOLS - actual)} "
        f"unexpected={sorted(actual - CANONICAL_SYMBOLS)} "
        f"(copy={sorted(actual)}, canonical={sorted(CANONICAL_SYMBOLS)})"
    )


def test_preferences_sidebar_index_symbols_default_mirrors_benchmarks():
    actual = set(preferences.SIDEBAR_INDEX_SYMBOLS_DEFAULT)

    assert actual == CANONICAL_SYMBOLS, _drift(
        "app.services.preferences.SIDEBAR_INDEX_SYMBOLS_DEFAULT", actual
    )


def test_quote_service_core_index_symbols_mirrors_benchmarks():
    actual = set(QuoteService.CORE_INDEX_SYMBOLS)

    assert actual == CANONICAL_SYMBOLS, _drift(
        "app.services.quote_service.QuoteService.CORE_INDEX_SYMBOLS", actual
    )


def test_sector_monitor_core_indices_mirrors_benchmark_symbols_and_names():
    copy = "app.services.sector_monitor.CORE_INDICES"
    actual = set(sector_monitor.CORE_INDICES)

    assert actual == CANONICAL_SYMBOLS, _drift(copy, actual)

    renamed = {
        symbol: {"copy": name, "canonical": CANONICAL_NAMES[symbol]}
        for symbol, name in sector_monitor.CORE_INDICES.items()
        if name != CANONICAL_NAMES[symbol]
    }
    assert not renamed, f"{copy} names drifted from {CANONICAL}: {renamed}"
