"""The post-close strategy cache overlaid with the monitor engine's newer realtime results."""
from __future__ import annotations

import time
from typing import Any

from app.services import strategy_cache


def realtime_overrides(cached: dict, realtime_results: dict[str, dict]) -> dict[str, dict]:
    """Pick the monitor results that are newer than the post-close cache entry of the same strategy.

    Monitor results cover only the polling pool, so a post-close result of the same or a later
    session must win; a strategy the cache has no result for is filled only from the same or a later session.
    """
    cached_results = cached.get("results") or {}
    cached_as_of = cached.get("as_of")
    picked: dict[str, dict] = {}
    for sid, result in realtime_results.items():
        realtime_as_of = result.get("as_of") if isinstance(result, dict) else None
        cached_result = cached_results.get(sid)
        if isinstance(cached_result, dict):
            post_close_as_of = cached_result.get("as_of") or cached_as_of
            newer = isinstance(realtime_as_of, str) and (
                not isinstance(post_close_as_of, str) or realtime_as_of > post_close_as_of
            )
        else:
            newer = not isinstance(cached_as_of, str) or (
                isinstance(realtime_as_of, str) and realtime_as_of >= cached_as_of
            )
        if newer:
            picked[sid] = result
    return picked


def cached_with_realtime(app_state: Any) -> dict:
    """The post-close cache with each strategy replaced by its newer monitor result, if any."""
    data_dir = app_state.repo.store.data_dir
    return overlay_realtime(app_state, strategy_cache.read_cache(data_dir))


def overlay_realtime(app_state: Any, cached: dict | None) -> dict:
    if cached is None:
        cached = {"as_of": None, "results": {}, "updated_at": None}

    monitor_engine = getattr(app_state, "monitor_engine", None)
    if monitor_engine is not None:
        realtime_results = realtime_overrides(cached, monitor_engine.latest_strategy_results() or {})
        if realtime_results:
            results = dict(cached.get("results") or {})
            results.update(realtime_results)
            cached = dict(cached)
            cached["results"] = results
            if cached.get("failures"):
                cached["failures"] = {
                    sid: failure
                    for sid, failure in cached["failures"].items()
                    if sid not in realtime_results
                }
            cached["updated_at"] = int(time.time() * 1000)

    return cached
