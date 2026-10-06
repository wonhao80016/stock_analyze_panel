"""Days stored on their own trading day are provisional and are refetched.

The exchanges revise volume, amount and institutional flows after the evening
publication, so a partition last written on day D (Taipei time) is not final.
A refetch on D itself (the 21:40 run) leaves it provisional; the first run on a
later day rewrites it and finalizes it.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan import enriched_refresh, share_capital_store
from app.taiwan.corporate_actions import TaiwanCorporateActionRefreshService
from app.taiwan.daily_refresh import TaiwanDailyRefreshService
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import TaiwanDailyUpdateService
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.institutional_margin_refresh import TaiwanInstitutionalRefreshService
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.market_institutional_refresh import TaiwanMarketInstitutionalRefreshService
from app.taiwan.providers.snapshot_provider import OfficialDailySnapshotAdapter
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar

D = date(2026, 9, 23)
NEXT = date(2026, 9, 24)


@pytest.fixture(autouse=True)
def _no_market_amount_requests(monkeypatch):
    monkeypatch.setattr(TaiwanMarketInstitutionalRefreshService, "refresh_through", lambda self, target: {})
    monkeypatch.setattr(TaiwanCorporateActionRefreshService, "refresh_publish_window", lambda self, first, target: {})


def _taipei(d: date, hour: int, minute: int = 0) -> float:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=TAIPEI_TZ).timestamp()


def _daily_rows(symbols: list[str], d: date, volume: float = 1000.0) -> pl.DataFrame:
    n = len(symbols)
    return pl.DataFrame({
        "symbol": symbols,
        "date": [d] * n,
        "open": [10.0] * n,
        "high": [10.5] * n,
        "low": [9.5] * n,
        "close": [10.0] * n,
        "volume": [volume] * n,
        "amount": [10000.0] * n,
        "quote_ts": pl.Series([None] * n, dtype=pl.Int64),
    })


def _inst_rows(d: date, trust_net: int = 100) -> pl.DataFrame:
    return pl.DataFrame([
        {"symbol": "2330.TWSE", "date": d, "foreign_net": 1, "investment_trust_net": trust_net, "dealer_net": 1},
        {"symbol": "8069.TPEX", "date": d, "foreign_net": 1, "investment_trust_net": trust_net, "dealer_net": 1},
    ])


def _set_mtime(root: Path, d: date, when: float) -> None:
    os.utime(root / f"date={d.isoformat()}" / "part.parquet", (when, when))


def _both_exchanges(store: TaiwanDailyStore, d: date, when: float, tmp_path: Path) -> None:
    store.write_batch(_daily_rows(["2330.TWSE", "6488.TPEX"], d))
    _set_mtime(tmp_path / "daily", d, when)


# provisional_dates ----------------------------------------------------------------------------


def _daily_store(tmp_path: Path):
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    return store, lambda d: store.write_batch(_daily_rows(["2330.TWSE", "6488.TPEX"], d)), tmp_path / "daily"


def _inst_store(tmp_path: Path):
    store = TaiwanInstitutionalStore(data_dir=tmp_path / "institutional")
    return store, lambda d: store.write_batch(_inst_rows(d)), tmp_path / "institutional"


@pytest.mark.parametrize("make_store", [_daily_store, _inst_store], ids=["daily", "institutional"])
def test_provisional_dates_follow_the_taipei_calendar_day_of_the_write(tmp_path, make_store):
    store, write, root = make_store(tmp_path)
    days = [date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17)]
    for d in days:
        write(d)
    _set_mtime(root, days[0], _taipei(days[0], 0, 0))
    _set_mtime(root, days[1], _taipei(days[1], 23, 59))
    _set_mtime(root, days[2], _taipei(date(2026, 9, 17), 0, 0))
    _set_mtime(root, days[3], _taipei(date(2026, 9, 18), 8, 0))

    assert store.provisional_dates(days) == [days[0], days[1]]


@pytest.mark.parametrize("make_store", [_daily_store, _inst_store], ids=["daily", "institutional"])
def test_provisional_dates_ignore_missing_partitions_and_return_sorted(tmp_path, make_store):
    store, write, root = make_store(tmp_path)
    write(NEXT)
    write(D)
    _set_mtime(root, NEXT, _taipei(NEXT, 18))
    _set_mtime(root, D, _taipei(D, 18))

    assert store.provisional_dates([NEXT, date(2026, 9, 22), D]) == [D, NEXT]


def test_repair_dates_combine_half_snapshots_and_provisional_days_up_to_the_target(tmp_path):
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    root = tmp_path / "daily"
    half, final, provisional, after = date(2026, 9, 21), date(2026, 9, 22), D, NEXT
    store.write_batch(_daily_rows(["2330.TWSE"], half))
    for d in (final, provisional, after):
        store.write_batch(_daily_rows(["2330.TWSE", "6488.TPEX"], d))
    _set_mtime(root, half, _taipei(NEXT, 9))
    _set_mtime(root, final, _taipei(NEXT, 9))
    _set_mtime(root, provisional, _taipei(provisional, 21, 40))
    _set_mtime(root, after, _taipei(after, 21, 40))

    assert store.repair_dates(D) == [half, provisional]


def test_repair_dates_only_look_at_the_recent_window(tmp_path, monkeypatch):
    monkeypatch.setattr("app.taiwan.daily_store.REPAIR_WINDOW_PARTITIONS", 2)
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    old, recent, latest = date(2026, 9, 18), date(2026, 9, 22), D
    store.write_batch(_daily_rows(["2330.TWSE"], old))
    for d in (recent, latest):
        store.write_batch(_daily_rows(["2330.TWSE", "6488.TPEX"], d))
        _set_mtime(tmp_path / "daily", d, _taipei(NEXT, 9))

    assert store.repair_dates(latest) == []


# run_update, daily ----------------------------------------------------------------------------


@pytest.fixture
def quiet_publish(monkeypatch):
    monkeypatch.setattr(share_capital_store.TaiwanShareCapitalStore, "refresh", lambda self: 0)
    monkeypatch.setattr(enriched_refresh.TaiwanEnrichedRefreshService, "refresh_recent", lambda self, days=5: 0)


def _dated_store(latest: date) -> MagicMock:
    store = MagicMock()
    store.available_dates.return_value = [latest]
    store.provisional_dates.return_value = []
    return store


def _daily_update(daily_store, daily_svc, inst_store=None, inst_svc=None) -> TaiwanDailyUpdateService:
    return TaiwanDailyUpdateService(
        daily_store=daily_store,
        inst_store=inst_store or _dated_store(NEXT),
        margin_store=_dated_store(NEXT),
        daily_service=daily_svc,
        inst_service=inst_svc or MagicMock(),
        margin_service=MagicMock(),
    )


def _fetch_stats(**extra):
    return {"dates_requested": 1, "dates_fetched": 1, "dates_skipped": 0, "total_rows_written": 2,
            "failed_dates": [], **extra}


def test_run_update_refetches_a_day_written_on_its_own_trading_day(tmp_path, quiet_publish):
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    _both_exchanges(store, D, _taipei(D, 16, 30), tmp_path)
    svc_daily = MagicMock()
    svc_daily.refresh_dates.return_value = _fetch_stats()

    _daily_update(store, svc_daily).run_update(target_date=NEXT, refresh_institutional=False, refresh_margin=False)

    svc_daily.refresh_dates.assert_called_once_with(start_date=D, end_date=NEXT, force=False)


def test_run_update_leaves_a_day_rewritten_after_its_trading_day(tmp_path, quiet_publish):
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    _both_exchanges(store, D, _taipei(NEXT, 16, 30), tmp_path)
    svc_daily = MagicMock()
    svc_daily.refresh_dates.return_value = _fetch_stats()

    _daily_update(store, svc_daily).run_update(target_date=NEXT, refresh_institutional=False, refresh_margin=False)

    svc_daily.refresh_dates.assert_called_once_with(start_date=NEXT, end_date=NEXT, force=False)


def test_the_evening_run_refetches_the_day_the_afternoon_run_stored(tmp_path, quiet_publish):
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    _both_exchanges(store, D, _taipei(D, 16, 30), tmp_path)
    svc_daily = MagicMock()
    svc_daily.refresh_dates.return_value = _fetch_stats()

    result = _daily_update(store, svc_daily, inst_store=_dated_store(D)).run_update(
        target_date=D, refresh_institutional=False, refresh_margin=False
    )

    svc_daily.refresh_dates.assert_called_once_with(start_date=D, end_date=D, force=False)
    assert result.daily.note != "Daily store already current"


# refresh_dates, daily -------------------------------------------------------------------------


@pytest.mark.parametrize("failure", [RuntimeError("TPEx down"), None], ids=["error", "no-rows"])
def test_a_provisional_daily_refetch_that_fails_keeps_the_stored_day_and_moves_on(tmp_path, failure):
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    _both_exchanges(store, D, _taipei(D, 16, 30), tmp_path)
    adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    first = failure if failure is not None else pl.DataFrame()
    adapter.fetch_date.side_effect = [first, _daily_rows(["2330.TWSE", "6488.TPEX"], NEXT)]
    svc = TaiwanDailyRefreshService(store=store, snapshot_adapter=adapter, calendar=TaiwanTradingCalendar())

    stats = svc.refresh_dates(D, NEXT)

    assert stats["failed_dates"] == []
    assert [f["date"] for f in stats["repair_failed_dates"]] == [str(D)]
    assert stats["dates_fetched"] == 1
    assert store.available_dates() == [D, NEXT]
    assert store.read_range(None, D, D).height == 2


def test_a_provisional_daily_day_is_replaced_by_the_refetched_figures(tmp_path):
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    _both_exchanges(store, D, _taipei(D, 16, 30), tmp_path)
    adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    adapter.fetch_date.return_value = _daily_rows(["2330.TWSE", "6488.TPEX"], D, volume=2000.0)
    svc = TaiwanDailyRefreshService(store=store, snapshot_adapter=adapter, calendar=TaiwanTradingCalendar())

    stats = svc.refresh_dates(D, D)

    assert stats["dates_fetched"] == 1
    assert store.read_range(None, D, D)["volume"].to_list() == [2000.0, 2000.0]
    assert store.provisional_dates([D]) == []
    adapter.fetch_date.reset_mock()
    assert svc.refresh_dates(D, D)["dates_skipped"] == 1
    adapter.fetch_date.assert_not_called()


# institutional --------------------------------------------------------------------------------


class _Flow:
    def __init__(self, symbol: str, d: date, trust_net: int):
        self.symbol = symbol
        self.trade_date = d
        self.foreign_buy = self.foreign_sell = self.foreign_net = 1
        self.investment_trust_buy = self.investment_trust_sell = 0
        self.investment_trust_net = trust_net
        self.dealer_buy = self.dealer_sell = self.dealer_net = 1
        self.dealer_proprietary_buy = self.dealer_proprietary_sell = self.dealer_proprietary_net = 0
        self.dealer_hedge_buy = self.dealer_hedge_sell = self.dealer_hedge_net = 0
        self.official_net = self.computed_net = 0
        self.has_discrepancy = False
        self.meta = None


class _Side:
    def __init__(self, symbol: str, outcome):
        self._symbol = symbol
        self._outcome = outcome
        self.calls: list[date] = []

    def build_url(self, d):
        return f"mock://{self._symbol}/{d}"

    def parse_payload(self, payload, d, url):
        self.calls.append(d)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return [_Flow(self._symbol, d, self._outcome)] if self._outcome is not None else []


_SAME = object()


class _Provider:
    def __init__(self, outcome, tpex_outcome=_SAME):
        self.twse = _Side("2330.TWSE", outcome)
        self.tpex = _Side("8069.TPEX", outcome if tpex_outcome is _SAME else tpex_outcome)


def _inst_service(store, outcome, tpex_outcome=_SAME) -> tuple[TaiwanInstitutionalRefreshService, _Provider]:
    provider = _Provider(outcome, tpex_outcome)
    svc = TaiwanInstitutionalRefreshService(store=store, provider=provider, calendar=TaiwanTradingCalendar())
    svc._fetch_json = lambda url: {}
    return svc, provider


def _provisional_inst_store(tmp_path: Path) -> TaiwanInstitutionalStore:
    store = TaiwanInstitutionalStore(data_dir=tmp_path / "institutional")
    store.write_batch(_inst_rows(D, trust_net=100))
    _set_mtime(tmp_path / "institutional", D, _taipei(D, 16, 30))
    return store


def test_a_provisional_institutional_day_is_refetched_once(tmp_path):
    store = _provisional_inst_store(tmp_path)
    svc, provider = _inst_service(store, outcome=250)

    stats = svc.refresh_dates(D, D)

    assert stats["dates_fetched"] == 1
    assert provider.twse.calls == [D]
    assert store.read_range(None, D, D)["investment_trust_net"].to_list() == [250, 250]
    assert svc.refresh_dates(D, D)["dates_skipped"] == 1
    assert provider.twse.calls == [D]


@pytest.mark.parametrize("outcome", [RuntimeError("TWSE down"), None], ids=["error", "no-rows"])
def test_a_provisional_institutional_refetch_that_fails_keeps_the_stored_rows(tmp_path, outcome):
    store = _provisional_inst_store(tmp_path)
    svc, _ = _inst_service(store, outcome=outcome)

    stats = svc.refresh_dates(D, D)

    assert stats["failed_dates"] == []
    assert [f["date"] for f in stats["repair_failed_dates"]] == [str(D)]
    assert store.read_range(None, D, D)["investment_trust_net"].to_list() == [100, 100]


def test_a_failed_new_institutional_day_is_still_a_failure(tmp_path):
    store = _provisional_inst_store(tmp_path)
    svc, _ = _inst_service(store, outcome=RuntimeError("TWSE down"))

    stats = svc.refresh_dates(D, NEXT)

    assert [f["date"] for f in stats["failed_dates"]] == [str(NEXT)]
    assert [f["date"] for f in stats["repair_failed_dates"]] == [str(D)]


def test_run_update_refetches_a_provisional_institutional_day(tmp_path, quiet_publish):
    store = _provisional_inst_store(tmp_path)
    inst_svc = MagicMock()
    inst_svc.refresh_dates.return_value = {
        "dates_requested": 2, "dates_fetched": 2, "dates_skipped": 0, "total_rows_written": 4, "failed_dates": [],
    }

    _daily_update(_dated_store(NEXT), MagicMock(), inst_store=store, inst_svc=inst_svc).run_update(
        target_date=NEXT, refresh_daily=False, refresh_margin=False
    )

    inst_svc.refresh_dates.assert_called_once_with(D, NEXT, force=False)


def test_run_update_leaves_a_finalized_institutional_day(tmp_path, quiet_publish):
    store = _provisional_inst_store(tmp_path)
    _set_mtime(tmp_path / "institutional", D, _taipei(NEXT, 16, 30))
    inst_svc = MagicMock()
    inst_svc.refresh_dates.return_value = {
        "dates_requested": 1, "dates_fetched": 1, "dates_skipped": 0, "total_rows_written": 2, "failed_dates": [],
    }

    _daily_update(_dated_store(NEXT), MagicMock(), inst_store=store, inst_svc=inst_svc).run_update(
        target_date=NEXT, refresh_daily=False, refresh_margin=False
    )

    inst_svc.refresh_dates.assert_called_once_with(NEXT, NEXT, force=False)


@pytest.mark.parametrize("outcome", [RuntimeError("TWSE down"), None], ids=["error", "no-rows"])
def test_a_failed_institutional_repair_leaves_the_run_successful_and_is_noted(tmp_path, quiet_publish, outcome):
    store = _provisional_inst_store(tmp_path)
    svc_inst, _ = _inst_service(store, outcome=outcome)

    result = _daily_update(_dated_store(D), MagicMock(), inst_store=store, inst_svc=svc_inst).run_update(
        target_date=D, refresh_daily=False, refresh_margin=False
    )

    assert result.institutional.status == "success"
    assert result.institutional.failed_dates == []
    assert str(D) in (result.institutional.note or "")
    assert store.read_range(None, D, D)["investment_trust_net"].to_list() == [100, 100]


# enriched republish ---------------------------------------------------------------------------


def test_refetching_a_provisional_daily_day_makes_its_enriched_partition_stale(tmp_path):
    daily = TaiwanDailyStore(data_dir=tmp_path / "daily")
    root = tmp_path / "enriched"
    for d in (D, NEXT):
        daily.write_batch(_daily_rows(["2330.TWSE", "6488.TPEX"], d))
        TaiwanEnrichedRefreshService._write_partition(root / f"date={d.isoformat()}", pl.DataFrame({"symbol": ["2330.TWSE"]}))
        _set_mtime(tmp_path / "daily", d, _taipei(d, 16, 30))
        _set_mtime(root, d, _taipei(d, 16, 45))
    enriched = TaiwanEnrichedRefreshService(daily_store=daily, enriched_root=root, share_capital=MagicMock())
    assert enriched.stale_since() is None

    adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    adapter.fetch_date.return_value = _daily_rows(["2330.TWSE", "6488.TPEX"], D, volume=2000.0)
    svc = TaiwanDailyRefreshService(store=daily, snapshot_adapter=adapter, calendar=TaiwanTradingCalendar())
    assert svc.refresh_dates(D, D)["dates_fetched"] == 1

    assert enriched.stale_since() == D


# one-sided refetch and failures inside the stored range --------------------------------------


@pytest.mark.parametrize(
    ("twse", "tpex", "missing"),
    [(250, None, "TPEx"), (None, 250, "TWSE"), (None, None, "TWSE and TPEx")],
    ids=["tpex-empty", "twse-empty", "both-empty"],
)
def test_a_one_sided_institutional_refetch_writes_nothing_and_stays_provisional(tmp_path, twse, tpex, missing):
    store = _provisional_inst_store(tmp_path)
    svc, _ = _inst_service(store, outcome=twse, tpex_outcome=tpex)

    stats = svc.refresh_dates(D, D)

    assert stats["dates_fetched"] == 0
    assert stats["total_rows_written"] == 0
    assert stats["failed_dates"] == []
    assert [f["date"] for f in stats["repair_failed_dates"]] == [str(D)]
    assert missing in stats["repair_failed_dates"][0]["error"]
    assert store.read_range(None, D, D)["investment_trust_net"].to_list() == [100, 100]
    assert store.provisional_dates([D]) == [D]


def test_a_one_sided_institutional_fetch_of_a_new_day_is_still_written(tmp_path):
    store = _provisional_inst_store(tmp_path)
    svc, _ = _inst_service(store, outcome=250, tpex_outcome=None)

    stats = svc.refresh_dates(NEXT, NEXT)

    assert stats["dates_fetched"] == 1
    assert stats["repair_failed_dates"] == []


def _finalized_inst_store(tmp_path: Path, days: list[date]) -> TaiwanInstitutionalStore:
    store = TaiwanInstitutionalStore(data_dir=tmp_path / "institutional")
    for d in days:
        store.write_batch(_inst_rows(d))
        _set_mtime(tmp_path / "institutional", d, _taipei(date(2026, 9, 29), 12))
    return store


def test_an_error_on_a_day_inside_the_stored_range_is_a_repair_failure(tmp_path):
    store = _finalized_inst_store(tmp_path, [date(2026, 9, 22), date(2026, 9, 28)])
    svc, _ = _inst_service(store, outcome=RuntimeError("TWSE down"))

    stats = svc.refresh_dates(date(2026, 9, 22), date(2026, 9, 28))

    assert stats["failed_dates"] == []
    assert str(D) in [f["date"] for f in stats["repair_failed_dates"]]


def test_an_error_on_a_day_past_the_latest_stored_one_is_a_failure(tmp_path):
    store = _finalized_inst_store(tmp_path, [date(2026, 9, 22)])
    svc, _ = _inst_service(store, outcome=RuntimeError("TWSE down"))

    stats = svc.refresh_dates(D, NEXT)

    assert [f["date"] for f in stats["failed_dates"]] == [str(D), str(NEXT)]
    assert stats["repair_failed_dates"] == []


def test_a_forced_error_inside_the_stored_range_stays_a_failure(tmp_path):
    store = _finalized_inst_store(tmp_path, [date(2026, 9, 22), date(2026, 9, 28)])
    svc, _ = _inst_service(store, outcome=RuntimeError("TWSE down"))

    stats = svc.refresh_dates(date(2026, 9, 22), date(2026, 9, 28), force=True)

    assert str(D) in [f["date"] for f in stats["failed_dates"]]
    assert stats["repair_failed_dates"] == []


def test_an_empty_day_inside_the_stored_range_without_a_partition_is_only_skipped(tmp_path):
    store = _finalized_inst_store(tmp_path, [date(2026, 9, 22), date(2026, 9, 28)])
    svc, _ = _inst_service(store, outcome=None)

    stats = svc.refresh_dates(date(2026, 9, 23), date(2026, 9, 24))

    assert stats["failed_dates"] == []
    assert stats["repair_failed_dates"] == []
    assert stats["dates_skipped"] == 2


# repair window and target bound ---------------------------------------------------------------


def _long_history(store_write, root: Path, days: int = 22) -> tuple[list[date], date]:
    history = [date(2026, 8, 1).fromordinal(date(2026, 8, 1).toordinal() + i) for i in range(days)]
    for i, d in enumerate(history):
        store_write(d)
        _set_mtime(root, d, _taipei(d, 16, 30) if i == 0 else _taipei(date(2026, 9, 1), 12))
    return history, history[-1]


def test_a_provisional_daily_day_outside_the_newest_twenty_is_not_refetched(tmp_path, quiet_publish):
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    history, _ = _long_history(
        lambda d: store.write_batch(_daily_rows(["2330.TWSE", "6488.TPEX"], d)), tmp_path / "daily"
    )
    assert store.provisional_dates(history) == [history[0]]
    svc_daily = MagicMock()
    svc_daily.refresh_dates.return_value = _fetch_stats()
    after = date(2026, 8, 24)

    _daily_update(store, svc_daily, inst_store=_dated_store(after)).run_update(
        target_date=after, refresh_institutional=False, refresh_margin=False
    )

    svc_daily.refresh_dates.assert_called_once_with(start_date=after, end_date=after, force=False)


def test_a_provisional_institutional_day_outside_the_newest_twenty_is_not_refetched(tmp_path, quiet_publish):
    store = TaiwanInstitutionalStore(data_dir=tmp_path / "institutional")
    history, _ = _long_history(lambda d: store.write_batch(_inst_rows(d)), tmp_path / "institutional")
    assert store.provisional_dates(history) == [history[0]]
    inst_svc = MagicMock()
    inst_svc.refresh_dates.return_value = _fetch_stats()
    after = date(2026, 8, 24)

    _daily_update(_dated_store(after), MagicMock(), inst_store=store, inst_svc=inst_svc).run_update(
        target_date=after, refresh_daily=False, refresh_margin=False
    )

    inst_svc.refresh_dates.assert_called_once_with(after, after, force=False)


def test_a_provisional_daily_day_after_the_target_neither_extends_nor_triggers_a_fetch(tmp_path, quiet_publish):
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    _both_exchanges(store, D, _taipei(NEXT, 12), tmp_path)
    _both_exchanges(store, NEXT, _taipei(NEXT, 16, 30), tmp_path)
    svc_daily = MagicMock()

    result = _daily_update(store, svc_daily, inst_store=_dated_store(D)).run_update(
        target_date=D, refresh_institutional=False, refresh_margin=False
    )

    svc_daily.refresh_dates.assert_not_called()
    assert result.daily.note == "Daily store already current"


def test_a_provisional_institutional_day_after_the_target_neither_extends_nor_triggers_a_fetch(tmp_path, quiet_publish):
    store = _finalized_inst_store(tmp_path, [D])
    store.write_batch(_inst_rows(NEXT))
    _set_mtime(tmp_path / "institutional", NEXT, _taipei(NEXT, 16, 30))
    inst_svc = MagicMock()

    result = _daily_update(_dated_store(D), MagicMock(), inst_store=store, inst_svc=inst_svc).run_update(
        target_date=D, refresh_daily=False, refresh_margin=False
    )

    inst_svc.refresh_dates.assert_not_called()
    assert result.institutional.note == "Institutional store already current"


def test_a_forced_one_sided_refetch_of_a_provisional_institutional_day_is_still_written(tmp_path):
    store = _provisional_inst_store(tmp_path)
    svc, _ = _inst_service(store, outcome=250, tpex_outcome=None)

    stats = svc.refresh_dates(D, D, force=True)

    assert stats["dates_fetched"] == 1
    assert stats["repair_failed_dates"] == []
    rows = store.read_range(None, D, D).sort("symbol")
    assert rows["symbol"].to_list() == ["2330.TWSE", "8069.TPEX"]
    assert rows["investment_trust_net"].to_list() == [250, 100]


@pytest.mark.parametrize(
    ("twse", "tpex", "missing"), [(250, None, "TPEx"), (None, 250, "TWSE")], ids=["tpex-empty", "twse-empty"]
)
def test_a_one_sided_fetch_of_a_gap_day_inside_the_stored_range_is_not_written(tmp_path, twse, tpex, missing):
    store = _finalized_inst_store(tmp_path, [date(2026, 9, 22), date(2026, 9, 28)])
    svc, _ = _inst_service(store, outcome=twse, tpex_outcome=tpex)

    stats = svc.refresh_dates(D, D)

    assert stats["dates_fetched"] == 0
    assert stats["failed_dates"] == []
    assert [f["date"] for f in stats["repair_failed_dates"]] == [str(D)]
    assert missing in stats["repair_failed_dates"][0]["error"]
    assert D not in store.available_dates()


def test_a_one_sided_fetch_of_a_day_past_the_latest_stored_one_is_written(tmp_path):
    store = _finalized_inst_store(tmp_path, [date(2026, 9, 22)])
    svc, _ = _inst_service(store, outcome=250, tpex_outcome=None)

    stats = svc.refresh_dates(D, D)

    assert stats["dates_fetched"] == 1
    assert stats["repair_failed_dates"] == []
    assert D in store.available_dates()
