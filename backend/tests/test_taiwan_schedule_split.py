"""盤後更新拆成兩班的排程與資料集開關。

交易所公布資券餘額晚於三大法人, 單一 16:30 班次抓不到當日資券, 於是資券永遠
落後一個交易日, 資料狀態天天顯示「尚未完全更新」。改為 16:30 抓日線+法人、
21:40 再抓資券。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.jobs import daily_pipeline
from app.taiwan.corporate_actions import TaiwanCorporateActionRefreshService
from app.taiwan.daily_update import TaiwanDailyUpdateService
from app.taiwan.market_institutional_refresh import TaiwanMarketInstitutionalRefreshService
from app.taiwan.share_capital_store import TaiwanShareCapitalStore


@pytest.fixture(autouse=True)
def _no_market_amount_requests(monkeypatch):
    monkeypatch.setattr(TaiwanMarketInstitutionalRefreshService, "refresh_through", lambda self, target: {})
    monkeypatch.setattr(TaiwanCorporateActionRefreshService, "refresh_publish_window", lambda self, first, target: {})
    monkeypatch.setattr(TaiwanShareCapitalStore, "refresh", lambda self: 0)


@pytest.fixture
def jobs():
    # AsyncIOScheduler needs a *running* loop when it starts, so build it inside one.
    async def _build():
        scheduler = daily_pipeline.start_scheduler()
        return scheduler, {j.id: j for j in scheduler.get_jobs()}

    loop = asyncio.new_event_loop()
    try:
        scheduler, mapping = loop.run_until_complete(_build())
        try:
            yield mapping
        finally:
            scheduler.shutdown(wait=False)
    finally:
        loop.close()


def test_both_taiwan_jobs_are_registered(jobs):
    assert "taiwan_daily_update" in jobs
    assert "taiwan_margin_update" in jobs


def test_first_shift_runs_at_1630_without_margin(jobs):
    job = jobs["taiwan_daily_update"]
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "16"
    assert fields["minute"] == "30"
    assert job.kwargs["refresh_daily"] is True
    assert job.kwargs["refresh_institutional"] is True
    assert job.kwargs["refresh_margin"] is False, "margin is not published yet at 16:30"


def test_second_shift_runs_at_2140_with_margin(jobs):
    job = jobs["taiwan_margin_update"]
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "21"
    assert fields["minute"] == "40"
    assert job.kwargs["refresh_margin"] is True


def test_second_shift_also_retries_the_earlier_datasets(jobs):
    """Already-stored dates are skipped, so retrying costs nothing and covers a failed 16:30."""
    job = jobs["taiwan_margin_update"]
    assert job.kwargs["refresh_daily"] is True
    assert job.kwargs["refresh_institutional"] is True


def test_both_jobs_run_weekdays_only(jobs):
    for jid in ("taiwan_daily_update", "taiwan_margin_update"):
        fields = {f.name: str(f) for f in jobs[jid].trigger.fields}
        assert fields["day_of_week"] == "mon-fri"


def _stub_services(svc, monkeypatch, calls):
    for name, attr in (
        ("daily", "daily_service"),
        ("institutional", "inst_service"),
        ("margin", "margin_service"),
    ):
        target = getattr(svc, attr)

        def _record(*a, _n=name, **k):
            calls.append(_n)
            return {"dates_requested": 1, "dates_fetched": 1, "dates_skipped": 0,
                    "total_rows_written": 1, "failed_dates": []}

        monkeypatch.setattr(target, "refresh_dates", _record)


def test_margin_toggle_skips_only_margin(monkeypatch):
    svc = TaiwanDailyUpdateService()
    calls: list[str] = []
    _stub_services(svc, monkeypatch, calls)

    res = svc.run_update(refresh_daily=True, refresh_margin=False, force=True)
    assert "margin" not in calls
    assert "institutional" in calls, "the other datasets must still run"
    assert res.margin.status == "skipped"
    assert res.institutional.status != "skipped"


def test_institutional_toggle_skips_only_institutional(monkeypatch):
    svc = TaiwanDailyUpdateService()
    calls: list[str] = []
    _stub_services(svc, monkeypatch, calls)

    res = svc.run_update(refresh_daily=False, refresh_institutional=False, force=True)
    assert "institutional" not in calls
    assert "margin" in calls, "the other datasets must still run"
    assert res.institutional.status == "skipped"
    assert res.margin.status != "skipped"


def test_defaults_still_refresh_everything(monkeypatch):
    """Existing callers pass neither flag and must keep refreshing all three."""
    svc = TaiwanDailyUpdateService()
    calls: list[str] = []
    _stub_services(svc, monkeypatch, calls)

    svc.run_update(force=True)
    assert set(calls) == {"daily", "institutional", "margin"}


class _UpdateService:
    def __init__(self, order: list[str], daily_status: str = "success", error: Exception | None = None):
        self.order = order
        self.daily_status = daily_status
        self.error = error

    def __call__(self):
        return self

    def run_update(self, **_kwargs):
        self.order.append("update")
        if self.error is not None:
            raise self.error
        status = SimpleNamespace(status=self.daily_status, note=None)
        return SimpleNamespace(
            overall_status=self.daily_status, daily=status, institutional=status, margin=status
        )


def _run_first_shift(jobs, monkeypatch, service: _UpdateService, order: list[str]) -> None:
    from app.taiwan import daily_update

    monkeypatch.setattr(daily_update, "TaiwanDailyUpdateService", service)
    monkeypatch.setattr(daily_pipeline, "refresh_enriched_cache", lambda _l: order.append("enriched"))
    monkeypatch.setattr(daily_pipeline, "refresh_strategy_cache", lambda _l: order.append("strategy"))
    job = jobs["taiwan_daily_update"]
    job.func(**job.kwargs)


def _record_benchmark(monkeypatch, order: list[str]) -> None:
    monkeypatch.setattr(
        daily_pipeline, "refresh_benchmark_index", lambda _l: order.append("benchmark")
    )


def test_scheduled_update_refreshes_the_index_before_the_update(jobs, monkeypatch):
    order: list[str] = []
    _record_benchmark(monkeypatch, order)

    _run_first_shift(jobs, monkeypatch, _UpdateService(order), order)

    assert order == ["benchmark", "update", "enriched", "strategy"]


def test_scheduled_update_refreshes_the_index_even_when_the_update_raises(jobs, monkeypatch):
    order: list[str] = []
    _record_benchmark(monkeypatch, order)

    _run_first_shift(jobs, monkeypatch, _UpdateService(order, error=RuntimeError("boom")), order)

    assert order == ["benchmark", "update"]


def test_scheduled_update_refreshes_the_index_when_daily_bars_failed(jobs, monkeypatch):
    order: list[str] = []
    _record_benchmark(monkeypatch, order)

    _run_first_shift(jobs, monkeypatch, _UpdateService(order, daily_status="failed"), order)

    assert order == ["benchmark", "update"]


@pytest.mark.real_benchmark_refresh
def test_a_failed_index_refresh_still_recomputes_the_picks(jobs, monkeypatch):
    from app.taiwan import index_daily

    def unreachable():
        raise RuntimeError("exchange unreachable")

    monkeypatch.setattr(index_daily, "get_index_daily_service", unreachable)
    order: list[str] = []

    _run_first_shift(jobs, monkeypatch, _UpdateService(order), order)

    assert order == ["update", "enriched", "strategy"]
