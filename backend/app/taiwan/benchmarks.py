"""Benchmark market indices (TAIEX and the TPEx index).

These are not listed instruments, so the security master does not carry them.
They are addressed with the same canonical symbol shape as stocks so that the
existing MIS realtime channel mapping (tse_t00.tw / otc_o00.tw) resolves them.
"""
from __future__ import annotations

from typing import NamedTuple


class BenchmarkIndex(NamedTuple):
    symbol: str
    code: str
    name: str
    exchange: str


TAIEX = BenchmarkIndex("t00.TWSE", "t00", "發行量加權股價指數", "TWSE")
TPEX_INDEX = BenchmarkIndex("o00.TPEX", "o00", "櫃買指數", "TPEX")

BENCHMARK_INDICES: dict[str, BenchmarkIndex] = {
    TAIEX.symbol: TAIEX,
    TPEX_INDEX.symbol: TPEX_INDEX,
}


_BY_UPPER: dict[str, BenchmarkIndex] = {s.upper(): b for s, b in BENCHMARK_INDICES.items()}


def get_benchmark(symbol: str) -> BenchmarkIndex | None:
    """Callers upper-case symbols, but the MIS channel code is lower-case."""
    return _BY_UPPER.get(symbol.upper())


def is_benchmark(symbol: str) -> bool:
    return symbol.upper() in _BY_UPPER
