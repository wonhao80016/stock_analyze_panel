"""The settings update button must also catch up institutional flows, market amounts and margin."""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan import bootstrap, daily_update
from app.taiwan.bootstrap import TaiwanBootstrapService
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import TaiwanDailyUpdateService, resolve_margin_target
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.market_institutional_refresh import TaiwanMarketInstitutionalRefreshService
from app.taiwan.market_institutional_store import TaiwanMarketInstitutionalStore
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar

FRI = date(2026, 10, 16)
MON = date(2026, 10, 19)
TUE = date(2026, 10, 20)
WED = date(2026, 10, 21)
CAL = TaiwanTradingCalendar()


def _taipei(d: date, hour: int, minute: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=TAIPEI_TZ)


def _touch(part: Path, written: datetime) -> None:
    stamp = written.timestamp()
    os.utime(part, (stamp, stamp))


def _daily_day(store_dir: Path, d: date, written: datetime) -> None:
    part = store_dir / f"date={d.isoformat()}" / "part.parquet"
    part.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {"symbol": ["2330.TWSE", "6488.TPEX"], "date": [d, d], "close": [593.0, 420.0]}
    ).write_parquet(str(part))
    _touch(part, written)


def _stored_day(store_dir: Path, d: date) -> None:
    part = store_dir / f"date={d.isoformat()}" / "part.parquet"
    part.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"symbol": ["2330.TWSE"], "date": [d]}).write_parquet(str(part))
    _touch(part, _taipei(MON, 9))


def _fetch_stats(days: int = 1) -> dict:
    return {
        "dates_requested": days,
        "dates_fetched": days,
        "dates_skipped": 0,
        "total_rows_written": 2 * days,
        "failed_dates": [],
        "repair_failed_dates": [],
    }


class _RecordingMarket(TaiwanMarketInstitutionalRefreshService):
    """Real catch-up date selection; only the exchange requests are replaced."""

    def __init__(self, order: list[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.order = order
        self.requested: list[list[date]] = []
        self.error: Exception | None = None

    def refresh_days(self, days: list[date]) -> dict:
        self.order.append("market_institutional")
        self.requested.append(list(days))
        if self.error is not None:
            raise self.error
        return _fetch_stats(len(days))


class _Setup:
    def __init__(self, tmp_path: Path, *, daily_latest: date, daily_written: datetime) -> None:
        root = tmp_path / "taiwan"
        self.order: list[str] = []
        self.daily_store = TaiwanDailyStore(data_dir=root / "daily")
        _daily_day(root / "daily", FRI, _taipei(MON, 9))
        if daily_latest != FRI:
            _daily_day(root / "daily", daily_latest, daily_written)
        for name in ("institutional", "margin", "market_institutional"):
            _stored_day(root / name, FRI)

        self.daily_refresh = MagicMock()
        self.daily_refresh.refresh_dates.side_effect = self._record("daily", _fetch_stats(3))
        self.inst_service = MagicMock()
        self.inst_service.refresh_dates.side_effect = self._record("institutional", _fetch_stats(3))
        self.margin_service = MagicMock()
        self.margin_service.refresh_dates.side_effect = self._record("margin", _fetch_stats(2))
        self.market = _RecordingMarket(
            self.order,
            store=TaiwanMarketInstitutionalStore(data_dir=root / "market_institutional"),
            calendar=CAL,
            fetch_json=lambda url: pytest.fail(f"no exchange request expected: {url}"),
            daily_store=self.daily_store,
        )
        self.updater = TaiwanDailyUpdateService(
            daily_store=self.daily_store,
            inst_store=TaiwanInstitutionalStore(data_dir=root / "institutional"),
            margin_store=TaiwanMarginStore(data_dir=root / "margin"),
            daily_service=self.daily_refresh,
            inst_service=self.inst_service,
            margin_service=self.margin_service,
            calendar=CAL,
            market_inst_service=self.market,
            corporate_action_service=MagicMock(),
        )
        self.service = TaiwanBootstrapService(
            store=self.daily_store,
            refresh_service=self.daily_refresh,
            publish=lambda _only_if_behind: self.order.append("publish") or False,
            chip_service=self.updater,
        )

    def _record(self, name: str, stats: dict):
        def side_effect(*_args, **_kwargs):
            self.order.append(name)
            return stats

        return side_effect


@pytest.fixture
def at(monkeypatch):
    def freeze(now: datetime) -> None:
        monkeypatch.setattr(bootstrap, "taipei_now", lambda: now)
        monkeypatch.setattr(daily_update, "taipei_now", lambda: now)

    return freeze


def test_current_bars_still_catch_up_market_amounts_and_institutional_days(
    tmp_path, at, deny_network
):
    """The backend stopped after Friday: bars were caught up later, chip data stayed on Friday."""
    at(_taipei(WED, 22))
    setup = _Setup(tmp_path, daily_latest=WED, daily_written=_taipei(WED, 21, 45))

    res = setup.service.update_to_latest()

    assert res["already_current"] is True
    setup.daily_refresh.refresh_dates.assert_not_called()
    setup.inst_service.refresh_dates.assert_called_once_with(MON, WED, force=False)
    assert setup.market.requested == [[MON, TUE, WED]]
    setup.margin_service.refresh_dates.assert_called_once_with(MON, WED, force=False)
    chip = res["chip_data"]
    assert chip["institutional"]["status"] == "success"
    assert chip["institutional"]["dates_fetched"] == 3
    assert chip["market_institutional"]["status"] == "success"
    assert chip["market_institutional"]["dates_fetched"] == 3
    assert chip["margin"]["status"] == "success"
    assert "籌碼資料" in res["message"]


def test_new_bars_run_daily_then_institutional_then_market_amounts_then_margin(
    tmp_path, at, deny_network
):
    at(_taipei(WED, 22))
    setup = _Setup(tmp_path, daily_latest=FRI, daily_written=_taipei(MON, 9))

    res = setup.service.update_to_latest()

    assert res["already_current"] is False
    assert res["dates_fetched"] == 3
    assert setup.order == ["daily", "institutional", "market_institutional", "margin", "publish"]


def test_a_chip_data_failure_keeps_the_update_and_reports_the_failure(tmp_path, at, deny_network):
    at(_taipei(WED, 22))
    setup = _Setup(tmp_path, daily_latest=FRI, daily_written=_taipei(MON, 9))
    setup.inst_service.refresh_dates.side_effect = RuntimeError("T86 throttled")
    setup.market.error = RuntimeError("BFI82U down")

    res = setup.service.update_to_latest()

    assert res["ok"] is True
    assert res["message"].startswith("成功更新 3 個交易日")
    chip = res["chip_data"]
    assert chip["institutional"]["status"] == "failed"
    assert chip["institutional"]["failed_dates"] == [{"error": "T86 throttled"}]
    assert chip["market_institutional"]["status"] == "failed"
    assert "BFI82U down" in chip["market_institutional"]["note"]
    assert chip["margin"]["status"] == "success"
    assert "三大法人、大盤法人金額未完整更新" in res["message"]


def test_the_endpoint_answers_200_when_chip_data_fails(tmp_path, at, deny_network, monkeypatch):
    from starlette.testclient import TestClient

    from app.main import app

    at(_taipei(WED, 22))
    setup = _Setup(tmp_path, daily_latest=WED, daily_written=_taipei(WED, 21, 45))
    setup.margin_service.refresh_dates.side_effect = RuntimeError("MI_MARGN down")
    monkeypatch.setattr("app.api.taiwan.TaiwanBootstrapService", lambda *a, **kw: setup.service)

    resp = TestClient(app, client=("127.0.0.1", 50000)).post("/api/taiwan/bootstrap/update-latest")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["chip_data"]["margin"]["status"] == "failed"
    assert body["chip_data"]["margin"]["failed_dates"] == [{"error": "MI_MARGN down"}]
    assert body["chip_data"]["institutional"]["status"] != "failed"
    assert body["chip_data"]["market_institutional"]["status"] != "failed"
    assert "融資融券未完整更新" in body["message"]


@pytest.mark.parametrize(
    ("target", "now", "expected"),
    [
        (WED, _taipei(WED, 16, 5), TUE),
        (WED, _taipei(WED, 21, 39), TUE),
        (WED, _taipei(WED, 21, 40), WED),
        (TUE, _taipei(WED, 10), TUE),
        (date(2026, 10, 23), _taipei(date(2026, 10, 24), 9), date(2026, 10, 23)),
    ],
)
def test_margin_stops_before_a_session_whose_balances_are_not_published(target, now, expected):
    assert resolve_margin_target(target, now, CAL) == expected


def test_update_latest_before_the_margin_publication_leaves_today_for_later(
    tmp_path, at, deny_network
):
    """A stored margin day is never refetched, so an early half-published day would stay wrong."""
    at(_taipei(WED, 17))
    setup = _Setup(tmp_path, daily_latest=FRI, daily_written=_taipei(MON, 9))

    res = setup.service.update_to_latest()

    setup.daily_refresh.refresh_dates.assert_called_once_with(
        start_date=date(2026, 10, 17), end_date=WED
    )
    setup.inst_service.refresh_dates.assert_called_once_with(MON, WED, force=False)
    assert setup.market.requested == [[MON, TUE, WED]]
    setup.margin_service.refresh_dates.assert_called_once_with(MON, TUE, force=False)
    assert "2026-10-21" in res["chip_data"]["margin"]["note"]


def test_update_latest_before_the_post_close_cutoff_targets_the_previous_session(
    tmp_path, at, deny_network
):
    at(_taipei(WED, 10))
    setup = _Setup(tmp_path, daily_latest=TUE, daily_written=_taipei(WED, 9))

    res = setup.service.update_to_latest()

    assert res["already_current"] is True
    setup.inst_service.refresh_dates.assert_called_once_with(MON, TUE, force=False)
    assert setup.market.requested == [[MON, TUE]]
    setup.margin_service.refresh_dates.assert_called_once_with(MON, TUE, force=False)


def test_the_default_service_refreshes_chip_data_against_its_own_daily_store(deny_network):
    svc = TaiwanBootstrapService()

    updater = svc._chip_updater()

    assert updater is not None
    assert updater.daily_store is svc.store
    assert updater.market_inst_service._daily_store is svc.store


def test_an_injected_store_without_a_chip_service_leaves_the_configured_chip_stores_alone(
    tmp_path, deny_network
):
    daily_dir = tmp_path / "taiwan" / "daily"
    _daily_day(daily_dir, WED, _taipei(WED, 21, 45))

    svc = TaiwanBootstrapService(
        store=TaiwanDailyStore(data_dir=daily_dir), refresh_service=MagicMock()
    )

    assert svc._chip_updater() is None
