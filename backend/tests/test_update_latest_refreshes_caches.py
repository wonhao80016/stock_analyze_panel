"""The settings update button has to move the panels, not just the store.

New bars on disk are invisible to a running process: the repo caches and the
strategy picks file are built once and never reread, which is why a fetch could
succeed while every panel kept showing the previous trading day.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.taiwan import update_taiwan_history_to_latest


def _run(update_result: dict):
    svc = MagicMock()
    svc.update_to_latest.return_value = update_result
    with (
        patch("app.api.taiwan.TaiwanBootstrapService", return_value=svc),
        patch("app.jobs.daily_pipeline.refresh_enriched_cache") as enriched,
        patch("app.jobs.daily_pipeline.refresh_strategy_cache") as strategy,
    ):
        return update_taiwan_history_to_latest(), enriched, strategy


def test_new_trading_days_refresh_both_caches():
    result, enriched, strategy = _run(
        {"ok": True, "already_current": False, "message": "成功更新 1 個交易日", "dates_fetched": 1}
    )
    assert enriched.call_count == 1
    assert strategy.call_count == 1
    assert result["caches_refreshed"] is True


def test_a_publish_without_new_days_refreshes_both_caches():
    """Indicators caught up for bars fetched earlier must reach the panels too."""
    result, enriched, strategy = _run({
        "ok": True, "already_current": True, "message": "本地資料已是最新",
        "dates_fetched": 0, "enriched_published": True,
    })
    assert enriched.call_count == 1
    assert strategy.call_count == 1
    assert result["caches_refreshed"] is True


def test_nothing_fetched_leaves_the_caches_alone():
    """Recomputing every strategy over an unchanged store is pure cost."""
    result, enriched, strategy = _run(
        {"ok": True, "already_current": True, "message": "本地資料已是最新", "dates_fetched": 0}
    )
    assert enriched.call_count == 0
    assert strategy.call_count == 0
    assert "caches_refreshed" not in result


def test_the_caches_are_refreshed_in_order():
    """Strategy picks are computed from the enriched cache, so it reloads first."""
    order: list[str] = []
    svc = MagicMock()
    svc.update_to_latest.return_value = {"ok": True, "dates_fetched": 2}
    with (
        patch("app.api.taiwan.TaiwanBootstrapService", return_value=svc),
        patch(
            "app.jobs.daily_pipeline.refresh_enriched_cache",
            side_effect=lambda _l: order.append("enriched"),
        ),
        patch(
            "app.jobs.daily_pipeline.refresh_strategy_cache",
            side_effect=lambda _l: order.append("strategy"),
        ),
    ):
        update_taiwan_history_to_latest()

    assert order == ["enriched", "strategy"]


def test_a_failed_fetch_still_raises_and_refreshes_nothing():
    svc = MagicMock()
    svc.update_to_latest.side_effect = ValueError("本地尚無資料，請先下載歷史資料包")
    with (
        patch("app.api.taiwan.TaiwanBootstrapService", return_value=svc),
        patch("app.jobs.daily_pipeline.refresh_enriched_cache") as enriched,
        patch("app.jobs.daily_pipeline.refresh_strategy_cache") as strategy,
        pytest.raises(HTTPException) as exc,
    ):
        update_taiwan_history_to_latest()

    assert exc.value.status_code == 400
    assert enriched.call_count == 0
    assert strategy.call_count == 0


# The endpoint deliberately does not guard the two refresh calls: it relies on
# them logging and swallowing. Pinned here because the fetch has already written
# to disk by then, so letting a refresh error surface would report a failure for
# work that succeeded.
def test_enriched_refresh_swallows_a_broken_repo():
    from app.jobs import daily_pipeline

    repo = MagicMock()
    repo.rebuild_views.side_effect = RuntimeError("duckdb is gone")
    with patch.object(daily_pipeline, "_get_app_state", return_value=MagicMock(repo=repo)):
        daily_pipeline.refresh_enriched_cache("test")


def test_strategy_refresh_swallows_a_broken_engine():
    from app.jobs import daily_pipeline

    state = MagicMock(repo=MagicMock(), strategy_engine=MagicMock())
    with (
        patch.object(daily_pipeline, "_get_app_state", return_value=state),
        patch(
            "app.api.screener.compute_and_cache_all",
            side_effect=RuntimeError("parquet unreadable"),
        ),
    ):
        daily_pipeline.refresh_strategy_cache("test")


def test_refreshes_are_skipped_rather_than_crashing_without_app_state():
    from app.jobs import daily_pipeline

    with patch.object(daily_pipeline, "_get_app_state", return_value=None):
        daily_pipeline.refresh_enriched_cache("test")
        daily_pipeline.refresh_strategy_cache("test")
