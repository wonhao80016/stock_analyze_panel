"""Process-local batch pacing shared by custom data-source requests."""

from __future__ import annotations

import time

from app.rate_limits import (
    _next_slot,
    _slot_lock,
    sleep_between_batches,
)


def test_two_first_batches_do_not_wait_even_when_slot_reserved() -> None:
    """Known limitation: concurrent/serial index=0 calls do not sleep (first-batch burst).

    Aggregate rpm is therefore not guaranteed when many pipelines start with index=0.
    """
    with _slot_lock:
        _next_slot.clear()
    t0 = time.perf_counter()
    sleep_between_batches(0, rpm=60)
    sleep_between_batches(0, rpm=60)
    assert time.perf_counter() - t0 < 0.05


def test_second_batch_waits_for_shared_slot() -> None:
    with _slot_lock:
        _next_slot.clear()
    t0 = time.perf_counter()
    sleep_between_batches(0, rpm=60)
    sleep_between_batches(1, rpm=60)
    assert time.perf_counter() - t0 >= 0.9
