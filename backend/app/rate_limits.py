"""Batch pacing helpers for custom data-source requests."""
from __future__ import annotations

import threading
import time
from typing import TypeVar

T = TypeVar("T")

# Process-local next free slot per rpm bucket; not shared across processes.
# index=0 calls only reserve a slot, so concurrent first batches can still burst past rpm.
_slot_lock = threading.Lock()
_next_slot: dict[int, float] = {}


def _reserve_slot(rpm: int, interval: float) -> float:
    """在共享时间轴上为一次请求预约一个发包槽, 返回需等待的秒数 (>=0)。

    interval = 60/rpm。now 早于该 rpm 桶的 next_slot 时排到 next_slot, 否则排到 now;
    随后把该桶 next_slot 后移 interval。持锁仅做时间账目, 不在锁内 sleep。
    """
    key = rpm if rpm and rpm > 0 else -1
    with _slot_lock:
        now = time.monotonic()
        scheduled = max(now, _next_slot.get(key, now))
        _next_slot[key] = scheduled + interval
        return scheduled - now


def batch_interval(rpm: int | None, *, default: float = 0.0) -> float:
    """Return the existing uniform batch interval formula: 60 / rpm."""
    return 60.0 / rpm if rpm and rpm > 0 else default


def chunked(items: list[T], batch_size: int | None) -> list[list[T]]:
    """Split items by batch size, preserving the existing None-as-one-batch behavior."""
    if batch_size is None:
        return [items]
    return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]


def sleep_between_batches(index: int, rpm: int | None, *, default_interval: float = 0.0) -> None:
    """Pace batches via the process-local shared slot table.

    index == 0 reserves a slot without sleeping; index > 0 waits until its reserved slot.
    """
    interval = batch_interval(rpm, default=default_interval)
    if interval <= 0:
        return
    if index <= 0:
        _reserve_slot(rpm or -1, interval)
        return
    wait = _reserve_slot(rpm or -1, interval)
    if wait > 0:
        time.sleep(wait)
