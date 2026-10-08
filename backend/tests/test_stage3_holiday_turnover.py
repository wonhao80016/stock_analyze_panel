"""A publish that runs after the latest stored session keeps that session's Taiwan turnover.

Turnover divides by the current issued shares, which only the newest session may use. A
republish on a holiday or after midnight must still treat the latest stored session as that
newest session instead of the wall-clock date, and a holiday's scheduled run must finalize it.
"""

from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.indicators import pipeline
from app.indicators.pipeline import invalidate_custom_signals, invalidate_taiwan_limit_profile_cache
from app.taiwan import universe
from app.taiwan.corporate_actions import TaiwanCorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.realtime.calendar import TAIPEI_TZ
from app.taiwan.share_capital_store import SCHEMA as SHARE_SCHEMA
from app.taiwan.share_capital_store import TaiwanShareCapitalStore
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

SHARES = {"2330.TWSE": 25_930_380_458, "6488.TPEX": 182_000_000}
SESSION_COUNT = 40
HOLIDAY = date(2026, 10, 9)


def _sessions(last: date) -> list[date]:
    out, day = [], last
    while len(out) < SESSION_COUNT:
        if day.weekday() < 5:
            out.append(day)
        day -= timedelta(days=1)
    return out[::-1]


def _instrument(symbol: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=code,
        instrument_type="stock",
        listing_status="active",
        listing_date="2000-01-01",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="股票",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-10-07",
    )


def _rows(sessions: list[date]) -> pl.DataFrame:
    rows = []
    for k, symbol in enumerate(SHARES):
        for t, day in enumerate(sessions):
            close = (100.0 + 40 * k) * (1 + 0.04 * math.sin(t / 5))
            volume = 20_000_000.0 * (1 + 0.3 * math.sin(t / 3 + k))
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "open": close * 0.99,
                    "high": close * 1.01,
                    "low": close * 0.98,
                    "close": close,
                    "volume": volume,
                    "amount": close * volume,
                    "quote_ts": None,
                }
            )
    return pl.DataFrame(rows, schema_overrides={"quote_ts": pl.Int64})


@pytest.fixture
def market(tmp_path, monkeypatch):
    """A data dir whose daily store ends on *last*; returns a builder."""
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    master_path = tmp_path / "taiwan" / "security_master.parquet"
    master_path.parent.mkdir(parents=True)
    pl.DataFrame([_instrument(s).to_dict() for s in SHARES]).write_parquet(master_path)
    master = TaiwanSecurityMaster(cache_path=master_path)
    assert master.load_cache()
    monkeypatch.setattr(universe, "_default_master", master)
    pl.DataFrame(
        {
            "symbol": list(SHARES),
            "issued_shares": list(SHARES.values()),
            "report_date": ["2026-09-30"] * len(SHARES),
            "retrieved_at": ["2026-10-01T00:00:00"] * len(SHARES),
        },
        schema=SHARE_SCHEMA,
    ).write_parquet(tmp_path / "taiwan" / "share_capital.parquet")
    invalidate_taiwan_limit_profile_cache()
    invalidate_custom_signals()

    def build(last: date) -> TaiwanDailyStore:
        daily = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
        daily.write_batch(_rows(_sessions(last)))
        # Partitions rewritten after their day are final, so no run refetches them.
        final = datetime(2026, 10, 1, tzinfo=TAIPEI_TZ).timestamp()
        for part in (tmp_path / "taiwan" / "daily").glob("date=*/part.parquet"):
            day = date.fromisoformat(part.parent.name.split("=", 1)[1])
            stamp = max(
                final,
                datetime.combine(
                    day + timedelta(days=1), datetime.min.time(), TAIPEI_TZ
                ).timestamp(),
            )
            os.utime(part, (stamp, stamp))
        return daily

    yield SimpleNamespace(root=tmp_path, build=build)
    invalidate_taiwan_limit_profile_cache()
    invalidate_custom_signals()


def _publish(market, daily: TaiwanDailyStore, clock: date, monkeypatch) -> dict[date, pl.DataFrame]:
    monkeypatch.setattr(pipeline, "cn_today", lambda: clock)
    root = market.root / "kline_daily_enriched"
    TaiwanEnrichedRefreshService(
        daily_store=daily,
        enriched_root=root,
        share_capital=TaiwanShareCapitalStore(market.root / "taiwan"),
        corporate_actions=TaiwanCorporateActionStore(market.root / "taiwan" / "corporate_actions"),
    ).refresh()
    return _read(root)


def _read(root) -> dict[date, pl.DataFrame]:
    return {
        date.fromisoformat(p.parent.name.split("=", 1)[1]): pl.read_parquet(p).sort("symbol")
        for p in root.glob("date=*/part.parquet")
    }


def _turnover(partitions: dict[date, pl.DataFrame], day: date) -> dict[str, float | None]:
    return dict(partitions[day].select("symbol", "turnover_rate").iter_rows())


def _expected_turnover(daily: TaiwanDailyStore, day: date) -> dict[str, float]:
    bars = daily.scan_range(None, day, day)
    return {s: v / SHARES[s] * 100 for s, v in bars.select("symbol", "volume").iter_rows()}


@pytest.mark.parametrize(
    ("latest", "clock"),
    [
        pytest.param(date(2026, 10, 8), HOLIDAY, id="holiday-republish"),
        pytest.param(date(2026, 10, 7), date(2026, 10, 8), id="after-midnight"),
    ],
)
def test_a_later_republish_keeps_the_latest_session_turnover(market, monkeypatch, latest, clock):
    daily = market.build(latest)
    same_day = _publish(market, daily, latest, monkeypatch)
    later = _publish(market, daily, clock, monkeypatch)

    expected = _expected_turnover(daily, latest)
    assert _turnover(same_day, latest) == pytest.approx(expected)
    assert _turnover(later, latest) == pytest.approx(expected)
    assert later.keys() == same_day.keys()
    for day, frame in same_day.items():
        assert later[day].equals(frame), day


def test_earlier_sessions_keep_null_turnover(market, monkeypatch):
    daily = market.build(date(2026, 10, 8))
    published = _publish(market, daily, HOLIDAY, monkeypatch)

    earlier = [d for d in published if d < date(2026, 10, 8)]
    assert earlier
    for day in earlier:
        assert published[day]["turnover_rate"].null_count() == len(SHARES), day


def test_a_window_ending_before_the_latest_session_publishes_no_current_turnover(
    market, monkeypatch
):
    daily = market.build(date(2026, 10, 8))
    monkeypatch.setattr(pipeline, "cn_today", lambda: HOLIDAY)
    root = market.root / "kline_daily_enriched"
    service = TaiwanEnrichedRefreshService(
        daily_store=daily,
        enriched_root=root,
        share_capital=TaiwanShareCapitalStore(market.root / "taiwan"),
        corporate_actions=TaiwanCorporateActionStore(market.root / "taiwan" / "corporate_actions"),
    )
    service.refresh()
    service.refresh(start=date(2026, 10, 6), end=date(2026, 10, 7))

    published = _read(root)
    assert published[date(2026, 10, 7)]["turnover_rate"].null_count() == len(SHARES)
    assert _turnover(published, date(2026, 10, 8)) == pytest.approx(
        _expected_turnover(daily, date(2026, 10, 8))
    )


def _holiday_store(root) -> object:
    from app.taiwan.realtime import holidays

    root.mkdir(parents=True, exist_ok=True)
    (root / "2026.json").write_text(
        json.dumps({"year": 2026, "holidays": ["2026-10-09", "2026-10-10"], "trading_days": []}),
        encoding="utf-8",
    )

    def no_fetch(year: int) -> object:
        raise AssertionError(f"lookups must not fetch ({year})")

    return holidays.TaiwanHolidayStore(root=root, fetcher=no_fetch, today=lambda: HOLIDAY)


def test_a_holiday_scheduled_run_finalizes_the_previous_session(market, monkeypatch):
    import asyncio

    from app.jobs import daily_pipeline
    from app.taiwan import daily_update
    from app.taiwan.realtime import calendar, holidays

    daily = market.build(date(2026, 10, 8))
    monkeypatch.setattr(holidays, "_default_store", _holiday_store(market.root / "holiday_cache"))
    run_at = datetime(2026, 10, 9, 16, 30, tzinfo=TAIPEI_TZ)
    monkeypatch.setattr(calendar, "taipei_today", lambda: HOLIDAY)
    monkeypatch.setattr(daily_update, "taipei_now", lambda: run_at)
    monkeypatch.setattr(pipeline, "cn_today", lambda: HOLIDAY)
    monkeypatch.setattr(TaiwanShareCapitalStore, "refresh", lambda self: 0)
    master = MagicMock()
    monkeypatch.setattr(daily_update, "get_security_master", lambda: master)

    daily_service = MagicMock()
    stores = {}
    for name in ("inst_store", "margin_store"):
        store = MagicMock()
        store.available_dates.return_value = _sessions(date(2026, 10, 8))
        store.provisional_dates.return_value = []
        stores[name] = store
    actions = MagicMock()
    actions.refresh_publish_window.return_value = {}
    market_inst = MagicMock()
    market_inst.refresh_through.return_value = {}

    service_class = daily_update.TaiwanDailyUpdateService

    def service() -> daily_update.TaiwanDailyUpdateService:
        return service_class(
            daily_store=daily,
            daily_service=daily_service,
            inst_service=MagicMock(),
            margin_service=MagicMock(),
            market_inst_service=market_inst,
            corporate_action_service=actions,
            security_master=master,
            **stores,
        )

    results = []

    def run_update(self, **kwargs):
        result = original(self, **kwargs)
        results.append(result)
        return result

    original = service_class.run_update
    monkeypatch.setattr(service_class, "run_update", run_update)
    monkeypatch.setattr(daily_update, "TaiwanDailyUpdateService", lambda: service())
    order: list[str] = []
    monkeypatch.setattr(
        daily_pipeline, "refresh_benchmark_index", lambda _l: order.append("benchmark")
    )
    monkeypatch.setattr(
        daily_pipeline, "refresh_enriched_cache", lambda _l: order.append("enriched")
    )
    monkeypatch.setattr(
        daily_pipeline, "refresh_strategy_cache", lambda _l: order.append("strategy")
    )

    async def build():
        scheduler = daily_pipeline.start_scheduler()
        return scheduler, {j.id: j for j in scheduler.get_jobs()}

    loop = asyncio.new_event_loop()
    try:
        scheduler, jobs = loop.run_until_complete(build())
        try:
            job = jobs["taiwan_daily_update"]
            job.func(**job.kwargs)
        finally:
            scheduler.shutdown(wait=False)
    finally:
        loop.close()

    assert order == ["benchmark", "enriched", "strategy"]
    assert len(results) == 1
    assert results[0].target_latest_trading_date == "2026-10-08"
    master.refresh.assert_not_called()
    for call in daily_service.refresh_dates.call_args_list:
        assert call.kwargs["end_date"] < HOLIDAY
    published = _read(market.root / "kline_daily_enriched")
    assert HOLIDAY not in published
    assert _turnover(published, date(2026, 10, 8)) == pytest.approx(
        _expected_turnover(daily, date(2026, 10, 8))
    )
