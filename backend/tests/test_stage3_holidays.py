"""Taiwan exchange holidays from the TWSE schedule: parsing, per-year cache, polling and scheduled updates."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.jobs import daily_pipeline
from app.services.quote_service import QuoteService
from app.taiwan.realtime import calendar, holidays
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar

pytestmark = pytest.mark.usefixtures("deny_network")

TWSE_2026_PAYLOAD = {
    "stat": "ok",
    "date": "20260101",
    "title": "115 年市場開休市日期",
    "fields": ["日期", "名稱", "說明"],
    "queryYear": 2026,
    "total": 27,
    "data": [
        ["2026-01-01", "中華民國開國紀念日", ""],
        ["2026-01-02", "國曆新年開始交易日", ""],
        ["2026-02-11", "農曆春節前最後交易日", ""],
        ["2026-02-12", "市場無交易，僅辦理結算交割作業", ""],  # noqa: RUF001
        ["2026-02-13", "市場無交易，僅辦理結算交割作業", ""],  # noqa: RUF001
        ["2026-02-15", "農曆除夕及春節", ""],
        ["2026-02-16", "農曆除夕及春節", ""],
        ["2026-02-17", "農曆除夕及春節", ""],
        ["2026-02-18", "農曆除夕及春節", ""],
        ["2026-02-19", "農曆除夕及春節", ""],
        ["2026-02-20", "農曆除夕及春節", ""],
        ["2026-02-23", "農曆春節後開始交易日", ""],
        ["2026-02-27", "和平紀念日", ""],
        ["2026-02-28", "和平紀念日", ""],
        ["2026-04-03", "兒童節及民族掃墓節", ""],
        ["2026-04-04", "兒童節及民族掃墓節", ""],
        ["2026-04-05", "兒童節及民族掃墓節", ""],
        ["2026-04-06", "兒童節及民族掃墓節", ""],
        ["2026-05-01", "勞動節", ""],
        ["2026-06-19", "端午節", ""],
        ["2026-09-25", "中秋節", ""],
        ["2026-09-28", "孔子誕辰紀念日/ 教師節", ""],
        ["2026-10-09", "國慶日", ""],
        ["2026-10-10", "國慶日", ""],
        ["2026-10-25", "臺灣光復暨金門古寧頭大捷紀念日", ""],
        ["2026-10-26", "臺灣光復暨金門古寧頭大捷紀念日", ""],
        ["2026-12-25", "行憲紀念日", ""],
    ],
}

UNPUBLISHED_PAYLOAD = {
    "stat": "ok",
    "fields": ["日期", "名稱", "說明"],
    "queryYear": 2027,
    "data": [],
    "total": 0,
}


class FakeFetcher:
    def __init__(self, responses: dict[int, object]):
        self.responses = responses
        self.calls: list[int] = []

    def __call__(self, year: int) -> dict:
        self.calls.append(year)
        response = self.responses.get(year, UNPUBLISHED_PAYLOAD)
        if isinstance(response, Exception):
            raise response
        return response


class Clock:
    def __init__(self, day: date):
        self.day = day

    def __call__(self) -> date:
        return self.day


def _store(tmp_path, fetcher, day=date(2026, 10, 8)):
    return holidays.TaiwanHolidayStore(
        root=tmp_path / "holidays", fetcher=fetcher, today=Clock(day)
    )


@pytest.fixture
def shared_store(tmp_path, monkeypatch):
    """Install a store with the 2026 schedule already cached as the process-wide default."""
    store = _store(tmp_path, FakeFetcher({2026: TWSE_2026_PAYLOAD}), day=date(2026, 10, 1))
    store.refresh(2026)
    tripwire = FakeFetcher({2026: AssertionError("lookups must not fetch")})
    store._fetcher = tripwire
    monkeypatch.setattr(holidays, "_default_store", store)
    return SimpleNamespace(store=store, fetcher=tripwire)


def _set_now(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(calendar, "taipei_now", lambda: moment)
    monkeypatch.setattr(calendar, "taipei_today", lambda: moment.date())


# ── parsing ─────────────────────────────────────────────────────────


def test_parse_2026_schedule_splits_holidays_and_trading_days():
    schedule = holidays.parse_schedule(2026, TWSE_2026_PAYLOAD)

    assert schedule is not None
    for day in ("2026-10-09", "2026-10-26", "2026-01-01", "2026-02-12", "2026-02-13", "2026-02-20"):
        assert date.fromisoformat(day) in schedule.holidays, day
    for day in ("2026-01-02", "2026-02-11", "2026-02-23"):
        assert date.fromisoformat(day) in schedule.trading_days, day
        assert date.fromisoformat(day) not in schedule.holidays, day
    assert date(2026, 10, 8) not in schedule.holidays
    assert len(schedule.holidays) + len(schedule.trading_days) == 27


@pytest.mark.parametrize(
    "payload",
    [
        UNPUBLISHED_PAYLOAD,
        {"stat": "很抱歉, 沒有符合條件的資料!"},
        {"stat": "ok", "data": [["2026-10-09", "國慶日", ""]]},
        {"stat": "ok", "fields": ["Date", "Name"], "data": [["2027-01-01", "開國紀念日"]]},
        None,
    ],
)
def test_unpublished_year_parses_to_none(payload):
    assert holidays.parse_schedule(2027, payload) is None


# ── per-year cache ──────────────────────────────────────────────────


def test_refresh_caches_the_year_atomically(tmp_path):
    fetcher = FakeFetcher({2026: TWSE_2026_PAYLOAD})
    store = _store(tmp_path, fetcher)

    assert store.refresh(2026) is not None

    cached = json.loads((tmp_path / "holidays" / "2026.json").read_text(encoding="utf-8"))
    assert "2026-10-09" in cached["holidays"]
    assert not list((tmp_path / "holidays").glob("*.tmp"))
    reloaded = _store(tmp_path, FakeFetcher({}))
    assert reloaded.is_holiday(date(2026, 10, 26)) is True
    assert reloaded.is_holiday(date(2026, 10, 8)) is False


def test_lookups_never_fetch(tmp_path):
    fetcher = FakeFetcher({2026: TWSE_2026_PAYLOAD})
    store = _store(tmp_path, fetcher)

    assert store.is_holiday(date(2026, 10, 9)) is None
    assert fetcher.calls == []


def test_refresh_fetches_at_most_once_per_day_per_year(tmp_path):
    fetcher = FakeFetcher({2026: TWSE_2026_PAYLOAD})
    store = _store(tmp_path, fetcher)

    store.refresh(2026)
    store.refresh(2026)
    store.refresh(2027)
    store.refresh(2027)
    assert fetcher.calls == [2026, 2027]

    restarted = _store(tmp_path, fetcher)
    restarted.refresh(2026)
    assert fetcher.calls == [2026, 2027], "a cache written today is not refetched after a restart"

    store._today = Clock(date(2026, 10, 9))
    store.refresh(2026)
    assert fetcher.calls == [2026, 2027, 2026]


def test_failed_fetch_keeps_the_cached_year(tmp_path):
    store = _store(tmp_path, FakeFetcher({2026: TWSE_2026_PAYLOAD}), day=date(2026, 10, 7))
    store.refresh(2026)
    path = tmp_path / "holidays" / "2026.json"
    before = path.read_bytes()

    for day, failure in (
        (date(2026, 10, 8), OSError("exchange unreachable")),
        (date(2026, 10, 9), UNPUBLISHED_PAYLOAD),
    ):
        store._today = Clock(day)
        store._fetcher = FakeFetcher({2026: failure})
        store.refresh(2026)
        assert path.read_bytes() == before
        assert store.is_holiday(date(2026, 10, 9)) is True


def test_columns_are_located_by_field_name():
    payload = {
        "stat": "ok",
        "fields": ["名稱", "說明", "日期"],
        "data": [["國慶日", "", "2026-10-09"], ["國曆新年開始交易日", "", "2026-01-02"]],
    }
    schedule = holidays.parse_schedule(2026, payload)

    assert schedule.holidays == frozenset({date(2026, 10, 9)})
    assert schedule.trading_days == frozenset({date(2026, 1, 2)})


@pytest.mark.parametrize(
    "failure",
    [
        OSError("exchange unreachable"),
        {"stat": "ok", "fields": ["Date", "Name"], "data": [["2026-10-09", "國慶日"]]},
        {"stat": "很抱歉, 沒有符合條件的資料!"},
    ],
)
def test_failed_fetch_is_retried_on_the_next_run(tmp_path, failure):
    fetcher = FakeFetcher({2026: failure})
    store = _store(tmp_path, fetcher)

    assert store.refresh(2026) is None
    assert store.is_holiday(date(2026, 10, 9)) is None

    fetcher.responses[2026] = TWSE_2026_PAYLOAD
    assert store.refresh(2026) is not None
    assert store.refresh(2026) is not None
    assert fetcher.calls == [2026, 2026]
    assert store.is_holiday(date(2026, 10, 9)) is True


def test_unpublished_year_stays_unverified(tmp_path):
    fetcher = FakeFetcher({2026: TWSE_2026_PAYLOAD})
    store = _store(tmp_path, fetcher, day=date(2026, 12, 1))

    assert store.refresh(2027) is None
    assert store.is_holiday(date(2027, 1, 4)) is None
    assert not (tmp_path / "holidays" / "2027.json").exists()


def test_refresh_upcoming_years_adds_next_year_late_in_the_year(tmp_path):
    fetcher = FakeFetcher({2026: TWSE_2026_PAYLOAD})
    holidays.refresh_upcoming_years(_store(tmp_path / "a", fetcher, day=date(2026, 10, 8)))
    assert fetcher.calls == [2026]

    fetcher.calls.clear()
    holidays.refresh_upcoming_years(_store(tmp_path / "b", fetcher, day=date(2026, 12, 1)))
    assert fetcher.calls == [2026, 2027]


# ── calendar ────────────────────────────────────────────────────────


def test_default_calendar_uses_the_shared_holidays(shared_store):
    cal = TaiwanTradingCalendar()

    assert cal.is_trading_day(date(2026, 10, 9)) is False
    assert cal.is_trading_day(date(2026, 10, 26)) is False
    assert cal.is_trading_day(date(2026, 10, 8)) is None
    assert cal.is_trading_day(date(2027, 1, 4)) is None, "an unpublished year is never 'trading'"
    assert calendar.is_trading_day(date(2026, 10, 9)) is False
    assert shared_store.fetcher.calls == []


def test_explicit_calendar_fixture_ignores_the_shared_holidays(shared_store):
    assert (
        TaiwanTradingCalendar(known_holidays={date(2026, 1, 5)}).is_trading_day(date(2026, 10, 9))
        is None
    )


def test_market_status_on_a_holiday_is_non_trading(shared_store):
    from app.taiwan.realtime.models import MarketStatus

    moment = datetime(2026, 10, 9, 9, 30, tzinfo=TAIPEI_TZ)
    assert calendar.get_market_status(moment) == MarketStatus.NON_TRADING_DAY


def test_latest_trading_date_skips_the_holiday(shared_store):
    from app.taiwan.daily_update import resolve_target_latest_trading_date

    target = resolve_target_latest_trading_date(
        as_of_dt=datetime(2026, 10, 9, 17, 0, tzinfo=TAIPEI_TZ)
    )
    assert target == date(2026, 10, 8)


# ── quote polling ───────────────────────────────────────────────────


@pytest.fixture
def no_manual_closure(monkeypatch):
    from app.services import preferences

    monkeypatch.setattr(preferences, "load", lambda: {})


def test_holiday_morning_is_closed_without_fetching(shared_store, monkeypatch, no_manual_closure):
    _set_now(monkeypatch, datetime(2026, 10, 9, 9, 30, tzinfo=TAIPEI_TZ))

    assert QuoteService._market_phase() == "closed"
    assert QuoteService._is_continuous_trading() is False
    assert shared_store.fetcher.calls == []


@pytest.mark.parametrize("hour", [8, 10, 14])
def test_holiday_is_closed_all_day(shared_store, monkeypatch, no_manual_closure, hour):
    _set_now(monkeypatch, datetime(2026, 10, 26, hour, 45, tzinfo=TAIPEI_TZ))
    assert QuoteService._market_phase() == "closed"


def test_trading_day_still_polls(shared_store, monkeypatch, no_manual_closure):
    _set_now(monkeypatch, datetime(2026, 10, 8, 9, 30, tzinfo=TAIPEI_TZ))
    assert QuoteService._market_phase() == "regular"


def test_unpublished_year_keeps_polling(shared_store, monkeypatch, no_manual_closure):
    _set_now(monkeypatch, datetime(2027, 1, 4, 9, 30, tzinfo=TAIPEI_TZ))
    assert QuoteService._market_phase() == "regular"


def test_manual_closure_applies_only_to_its_date(shared_store, monkeypatch):
    from app.services import preferences

    monkeypatch.setattr(preferences, "load", lambda: {"taiwan_market_closed_date": "2026-10-08"})

    _set_now(monkeypatch, datetime(2026, 10, 8, 9, 30, tzinfo=TAIPEI_TZ))
    assert QuoteService._market_phase() == "closed"
    assert QuoteService._is_continuous_trading() is False

    _set_now(monkeypatch, datetime(2026, 10, 12, 9, 30, tzinfo=TAIPEI_TZ))
    assert QuoteService._market_phase() == "regular"
    assert QuoteService._is_continuous_trading() is True


def test_manual_closure_api_round_trip(shared_store, monkeypatch, tmp_path):
    from app.api import settings as settings_api
    from app.services import preferences

    monkeypatch.setattr(preferences, "_path", lambda: tmp_path / "preferences.json")
    preferences._invalidate_cache()
    monkeypatch.setattr(calendar, "taipei_today", lambda: date(2026, 10, 8))
    app = FastAPI()
    app.include_router(settings_api.router)
    client = TestClient(app)

    try:
        assert client.get("/api/settings/preferences").json()["taiwan_market_closed_today"] is False
        res = client.put("/api/settings/preferences/market-closed-today", json={"closed": True})
        assert res.status_code == 200
        assert res.json() == {"taiwan_market_closed_today": True}
        assert preferences.load()["taiwan_market_closed_date"] == "2026-10-08"
        assert client.get("/api/settings/preferences").json()["taiwan_market_closed_today"] is True

        monkeypatch.setattr(calendar, "taipei_today", lambda: date(2026, 10, 12))
        assert client.get("/api/settings/preferences").json()["taiwan_market_closed_today"] is False

        res = client.put("/api/settings/preferences/market-closed-today", json={"closed": False})
        assert res.json() == {"taiwan_market_closed_today": False}
        assert preferences.load().get("taiwan_market_closed_date") is None
    finally:
        preferences._invalidate_cache()


# ── scheduled updates ───────────────────────────────────────────────


@pytest.fixture
def jobs():
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


def _run_job(jobs, monkeypatch, job_id: str, today: date) -> list[str]:
    from app.taiwan import daily_update

    order: list[str] = []

    class _Update:
        def __call__(self):
            return self

        def run_update(self, **_kwargs):
            order.append("update")
            status = SimpleNamespace(status="success", note=None)
            return SimpleNamespace(
                overall_status="success", daily=status, institutional=status, margin=status
            )

    monkeypatch.setattr(calendar, "taipei_today", lambda: today)
    monkeypatch.setattr(
        daily_pipeline, "refresh_benchmark_index", lambda _l: order.append("benchmark")
    )
    monkeypatch.setattr(daily_update, "TaiwanDailyUpdateService", _Update())
    monkeypatch.setattr(
        daily_pipeline, "refresh_enriched_cache", lambda _l: order.append("enriched")
    )
    monkeypatch.setattr(
        daily_pipeline, "refresh_strategy_cache", lambda _l: order.append("strategy")
    )
    job = jobs[job_id]
    job.func(**job.kwargs)
    return order


@pytest.mark.parametrize("job_id", ["taiwan_daily_update", "taiwan_margin_update"])
def test_scheduled_update_runs_on_a_holiday(shared_store, jobs, monkeypatch, job_id):
    """A holiday run finalizes the previous session; run_update targets it, not the holiday."""
    order = _run_job(jobs, monkeypatch, job_id, date(2026, 10, 9))
    assert order == ["benchmark", "update", "enriched", "strategy"]


def test_scheduled_update_runs_on_a_trading_day(shared_store, jobs, monkeypatch):
    order = _run_job(jobs, monkeypatch, "taiwan_daily_update", date(2026, 10, 8))
    assert order == ["benchmark", "update", "enriched", "strategy"]


def test_scheduled_update_runs_when_the_year_is_unverified(shared_store, jobs, monkeypatch):
    order = _run_job(jobs, monkeypatch, "taiwan_daily_update", date(2027, 1, 4))
    assert order == ["benchmark", "update", "enriched", "strategy"]


def test_holiday_refresh_job_is_registered(jobs, monkeypatch):
    job = jobs["taiwan_holiday_refresh"]
    calls: list[str] = []
    monkeypatch.setattr(holidays, "refresh_upcoming_years", lambda: calls.append("refresh"))

    job.func(**job.kwargs)

    assert calls == ["refresh"]
