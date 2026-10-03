from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from app.taiwan import index_daily
from app.taiwan.benchmarks import TAIEX
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.index_daily import TaiwanIndexDailyService

AUGUST = date(2026, 8, 1)
AUGUST_END = date(2026, 8, 31)


def _weekdays(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


AUGUST_TRADING_DAYS = _weekdays(AUGUST, AUGUST_END)


def _rows(days: list[date]) -> list[list[str]]:
    return [[d.strftime("%Y/%m/%d"), "100.0", "101.0", "99.0", "100.5"] for d in days]


class _FakeClient:
    def __init__(self, months: dict[str, list[list[str]]], failures: int = 0) -> None:
        self.months = months
        self.failures = failures
        self.calls: list[str] = []

    def get(self, url, params):
        month = params["date"][:6]
        self.calls.append(month)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("connection timed out")
        rows = self.months.get(month, [])
        return SimpleNamespace(json=lambda: {"data": rows})


def _service(tmp_path, client: _FakeClient) -> TaiwanIndexDailyService:
    calendar_dir = tmp_path / "daily"
    for d in AUGUST_TRADING_DAYS:
        partition = calendar_dir / f"date={d.isoformat()}"
        partition.mkdir(parents=True)
        (partition / "part.parquet").touch()
    service = TaiwanIndexDailyService(store=TaiwanDailyStore(tmp_path / "index_daily"), client=client)
    service.calendar = TaiwanDailyStore(calendar_dir)
    return service


def _read_august(service: TaiwanIndexDailyService) -> list[date]:
    return service.read_range(TAIEX.symbol, AUGUST, AUGUST_END)["date"].to_list()


def _freeze_clock(monkeypatch) -> list[float]:
    clock = [1000.0]
    monkeypatch.setattr(index_daily, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    return clock


def test_failed_month_is_retried_after_the_cooldown(tmp_path, monkeypatch):
    clock = _freeze_clock(monkeypatch)
    client = _FakeClient({"202608": _rows(AUGUST_TRADING_DAYS)}, failures=1)
    service = _service(tmp_path, client)

    assert _read_august(service) == []
    clock[0] += 601
    assert _read_august(service) == AUGUST_TRADING_DAYS


def test_failed_month_is_not_refetched_within_the_cooldown(tmp_path, monkeypatch):
    clock = _freeze_clock(monkeypatch)
    client = _FakeClient({"202608": _rows(AUGUST_TRADING_DAYS)}, failures=1)
    service = _service(tmp_path, client)

    _read_august(service)
    clock[0] += 60
    _read_august(service)

    assert client.calls == ["202608"]


def test_empty_past_month_is_not_refetched(tmp_path):
    client = _FakeClient({})
    service = _service(tmp_path, client)

    _read_august(service)
    _read_august(service)

    assert client.calls == ["202608"]


def test_partial_past_month_is_refetched_once_up_to_its_last_trading_day(tmp_path):
    client = _FakeClient({"202608": _rows(AUGUST_TRADING_DAYS)})
    service = _service(tmp_path, client)
    partial = [d for d in AUGUST_TRADING_DAYS if d.day <= 17]
    service.store.write_batch(service._rows_to_frame(TAIEX.symbol, _rows(partial)))

    assert _read_august(service) == AUGUST_TRADING_DAYS
    _read_august(service)
    assert client.calls == ["202608"]


def test_complete_past_month_is_not_refetched(tmp_path):
    client = _FakeClient({"202608": _rows(AUGUST_TRADING_DAYS)})
    service = _service(tmp_path, client)
    service.store.write_batch(service._rows_to_frame(TAIEX.symbol, _rows(AUGUST_TRADING_DAYS)))

    _read_august(service)

    assert client.calls == []


def test_month_empty_while_current_is_fetched_once_it_has_passed(tmp_path, monkeypatch):
    today = [date(2026, 8, 3)]
    monkeypatch.setattr(index_daily, "_today", lambda: today[0], raising=False)
    client = _FakeClient({})
    service = _service(tmp_path, client)

    assert _read_august(service) == []
    client.months["202608"] = _rows(AUGUST_TRADING_DAYS)
    today[0] = date(2026, 9, 7)
    assert _read_august(service) == AUGUST_TRADING_DAYS


def _current_august(tmp_path, monkeypatch) -> tuple[TaiwanIndexDailyService, _FakeClient, list[float]]:
    clock = _freeze_clock(monkeypatch)
    monkeypatch.setattr(index_daily, "_today", lambda: date(2026, 8, 20), raising=False)
    client = _FakeClient({"202608": _rows(AUGUST_TRADING_DAYS)})
    service = _service(tmp_path, client)
    _read_august(service)
    clock[0] += 60
    return service, client, clock


def test_current_month_is_cached_by_default(tmp_path, monkeypatch):
    service, client, _ = _current_august(tmp_path, monkeypatch)

    _read_august(service)

    assert client.calls == ["202608"]


def test_refresh_current_month_refetches_within_the_cache_period(tmp_path, monkeypatch):
    service, client, _ = _current_august(tmp_path, monkeypatch)

    service.read_range(TAIEX.symbol, AUGUST, AUGUST_END, refresh_current_month=True)

    assert client.calls == ["202608", "202608"]


def test_refresh_current_month_leaves_past_months_alone(tmp_path, monkeypatch):
    clock = _freeze_clock(monkeypatch)
    monkeypatch.setattr(index_daily, "_today", lambda: date(2026, 9, 7), raising=False)
    client = _FakeClient({"202608": _rows(AUGUST_TRADING_DAYS)})
    service = _service(tmp_path, client)
    service.store.write_batch(service._rows_to_frame(TAIEX.symbol, _rows(AUGUST_TRADING_DAYS)))
    clock[0] += 60

    service.read_range(TAIEX.symbol, AUGUST, AUGUST_END, refresh_current_month=True)

    assert client.calls == []
