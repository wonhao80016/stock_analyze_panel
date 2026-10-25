"""Taiwan decisions take their trading days from the Taiwan daily store, never from kline_daily.

kline_daily is an A-share table the Taiwan deployment no longer writes; the few partitions
left in it hold misdated watchlist rows. A backtest without a start, the minute chart's
trading-day guess must read the same with or without them.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from app.api import kline
from app.repository import DataStore, KlineRepository
from app.taiwan import universe

TAIPEI = timezone(timedelta(hours=8))
FIRST_SESSION = date(2024, 1, 2)
LAST_SESSION = date(2026, 10, 6)
SESSIONS = (FIRST_SESSION, date(2024, 1, 3), LAST_SESSION)
LEFTOVER = date(2026, 9, 16)


def _bars(d: date) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["2330.TWSE", "6488.TPEX"],
            "date": [d, d],
            "open": [10.0, 20.0],
            "high": [11.0, 21.0],
            "low": [9.0, 19.0],
            "close": [10.5, 20.5],
            "volume": [1000.0, 2000.0],
            "amount": [10500.0, 41000.0],
        }
    )


def _write_partition(root: Path, table: str, d: date) -> None:
    partition = root / table / f"date={d.isoformat()}"
    partition.mkdir(parents=True, exist_ok=True)
    _bars(d).write_parquet(partition / "part.parquet")


@pytest.fixture(params=["leftover", "no_kline_daily"])
def repo(request, tmp_path, monkeypatch, deny_network) -> KlineRepository:
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    for d in SESSIONS:
        _write_partition(tmp_path, "taiwan/daily", d)
    if request.param == "leftover":
        _write_partition(tmp_path, "kline_daily", LEFTOVER)
    return KlineRepository(DataStore(tmp_path))


def test_daily_dates_come_from_the_taiwan_store(repo):
    assert repo.earliest_daily_date() == FIRST_SESSION
    assert repo.latest_daily_date() == LAST_SESSION


def test_daily_dates_are_none_without_taiwan_sessions(tmp_path, monkeypatch, deny_network):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _write_partition(tmp_path, "kline_daily", LEFTOVER)
    repo = KlineRepository(DataStore(tmp_path))

    assert repo.earliest_daily_date() is None
    assert repo.latest_daily_date() is None


@pytest.fixture
def minute_request(repo, monkeypatch):
    monkeypatch.setattr(kline, "_get_stock_info", lambda _repo, _symbol: {})
    monkeypatch.setattr(kline, "_session_price_limit", lambda *a, **k: (None, None))
    monkeypatch.setattr(kline.kline_sync, "fetch_minute_single", lambda *a, **k: pl.DataFrame())
    monkeypatch.setattr(kline.kline_sync, "sync_minute_batch", lambda *a, **k: pl.DataFrame())
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo)))


def _at(monkeypatch, now: datetime) -> None:
    monkeypatch.setattr(kline, "cn_now", lambda: now)
    monkeypatch.setattr(kline, "cn_today", lambda: now.date())


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # The stored session for today proves the market traded.
        (datetime(2026, 10, 6, 17, 0, tzinfo=TAIPEI), LAST_SESSION),
        # No stored session for today after the close: the latest stored session.
        (datetime(2026, 10, 7, 15, 0, tzinfo=TAIPEI), LAST_SESSION),
        (datetime(2026, 10, 10, 11, 0, tzinfo=TAIPEI), LAST_SESSION),
        (datetime(2026, 10, 7, 10, 0, tzinfo=TAIPEI), date(2026, 10, 7)),
    ],
)
def test_minute_chart_trading_day_follows_the_taiwan_store(
    minute_request, monkeypatch, now, expected
):
    _at(monkeypatch, now)

    got = kline.get_minute(minute_request, symbol="2330.TWSE", trade_date=None)

    assert got["date"] == expected.isoformat()


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 10, 6, 17, 0, tzinfo=TAIPEI), LAST_SESSION),
        (datetime(2026, 10, 7, 15, 0, tzinfo=TAIPEI), LAST_SESSION),
    ],
)
def test_minute_batch_trading_day_follows_the_taiwan_store(
    minute_request, monkeypatch, now, expected
):
    _at(monkeypatch, now)
    repo = minute_request.app.state.repo
    seen: list[date] = []
    real_batch = repo.get_minute_batch

    def spy(symbols, trade_date):
        seen.append(trade_date)
        return real_batch(symbols, trade_date)

    monkeypatch.setattr(repo, "get_minute_batch", spy)

    kline.get_minute_batch(minute_request, {"symbols": ["2330.TWSE"]})

    assert seen == [expected]


TODAY = date(2026, 10, 7)
TAIWAN_PREVIOUS_SESSION = date(2026, 10, 6)


def _live_bar(symbol: str, d: date = TODAY) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [symbol],
            "date": [d],
            "open": [500.0],
            "high": [510.0],
            "low": [495.0],
            "close": [505.0],
            "volume": [1_000_000.0],
            "amount": [505_000_000.0],
            "quote_ts": [1_791_351_000_000],
        }
    )


class _WarmingRepo:
    """A repository still warming up: no latest enriched day yet.

    Its Taiwan live history is prepared first, as KlineRepository._refresh_enriched does.
    """

    def __init__(self, data_dir: Path) -> None:
        from app.taiwan.live_enriched import TaiwanLiveEnriched

        self.store = SimpleNamespace(data_dir=data_dir)
        self.enriched_writes: list[pl.DataFrame] = []
        self.taiwan_live = TaiwanLiveEnriched(data_dir)

    def get_taiwan_live_enriched(self):
        return self.taiwan_live

    def get_enriched_latest(self) -> tuple[pl.DataFrame, date | None]:
        return pl.DataFrame(), None

    def get_instruments(self) -> pl.DataFrame:
        return pl.DataFrame()

    def flush_live_enriched_asset(self, asset_type, df, *, force=False) -> None:
        self.enriched_writes.append(df)

    merge_live_enriched_asset = flush_live_enriched_asset


@pytest.fixture
def warming_service(tmp_path, monkeypatch, deny_network):
    from app.config import settings
    from app.services import quote_service

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(quote_service, "cn_today", lambda: TODAY)
    for d in (date(2026, 9, 15), date(2026, 9, 21)):
        _write_partition(tmp_path, "kline_daily", d + timedelta(days=1))
    stored = tmp_path / "taiwan" / "daily" / f"date={TAIWAN_PREVIOUS_SESSION.isoformat()}"
    stored.mkdir(parents=True)
    _bars(TAIWAN_PREVIOUS_SESSION).with_columns(
        pl.lit(None, dtype=pl.Int64).alias("quote_ts")
    ).write_parquet(stored / "part.parquet")
    monkeypatch.setattr(
        universe, "_default_master", SimpleNamespace(get_instrument=lambda _symbol: None)
    )
    service = quote_service.QuoteService()
    service._repo = _WarmingRepo(tmp_path)
    service._repo.taiwan_live.prepare()
    return service


def test_warm_up_builds_taiwan_rows_from_the_taiwan_store(warming_service):
    """During the warm-up Taiwan rows come from the publish pipeline over the Taiwan daily store."""
    warming_service._flush_live_enriched(_live_bar("2330.TWSE"))

    [written] = warming_service._repo.enriched_writes
    row = written.row(0, named=True)
    assert (row["symbol"], row["date"], row["close"]) == ("2330.TWSE", TODAY, 505.0)
    assert row["prev_close"] == 10.5
    assert row["signal_limit_up"] is not None and "vp_double" in written.columns


def test_taiwan_rows_wait_for_a_prepared_history(warming_service, monkeypatch):
    engine = warming_service._repo.taiwan_live
    engine.invalidate()
    scheduled: list[bool] = []
    monkeypatch.setattr(engine, "prepare_in_background", lambda _key: scheduled.append(True))

    warming_service._flush_live_enriched(_live_bar("2330.TWSE"))

    assert warming_service._repo.enriched_writes == []
    assert scheduled == [True]


def test_a_non_taiwan_bar_gets_no_live_enriched_row(warming_service):
    warming_service._flush_live_enriched(_live_bar("600000.SH"))

    assert warming_service._repo.enriched_writes == []


@pytest.fixture
def post_close_service(tmp_path, monkeypatch, deny_network):
    """A poll after 16:30: today's official partition is stored, so the live-write gate is open."""
    from app.config import settings
    from app.services import quote_service

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(quote_service, "cn_today", lambda: TODAY)
    monkeypatch.setattr(quote_service, "_persist_last_fetch", lambda _ms: None)
    _write_partition(tmp_path, "taiwan/daily", TODAY)
    repo = KlineRepository(DataStore(tmp_path))
    service = quote_service.QuoteService()
    service._repo = repo
    enriched_calls: list[list[str]] = []
    monkeypatch.setattr(
        service,
        "_flush_live_enriched",
        lambda df, extra=None: enriched_calls.append(df["symbol"].to_list()),
    )
    monkeypatch.setattr(service, "_evaluate_monitors", lambda *a, **k: None)
    monkeypatch.setattr(service, "_broadcast_quote_updated", lambda: None)
    return service, tmp_path, enriched_calls


def _quote(symbol: str, **extra) -> dict:
    return {
        "symbol": symbol,
        "close": 505.0,
        "open": 500.0,
        "high": 510.0,
        "low": 495.0,
        "volume": 1_000_000.0,
        "amount": 505_000_000.0,
        "quote_ts": 1_791_351_000_000,
        **extra,
    }


def test_taiwan_poll_with_official_day_stored_writes_nothing_to_kline_daily(post_close_service):
    service, root, enriched_calls = post_close_service
    records = [_quote("2330.TWSE", status="realtime"), _quote("0050.TWSE", status="realtime")]

    service._process_full_market_records(records, t0=0.0, now_ts=0.0)

    assert not list((root / "kline_daily").rglob("*.parquet"))
    # Taiwan ETFs are polled as stock rows; the ETF monitor round picks them out of these.
    assert enriched_calls == [["2330.TWSE", "0050.TWSE"]]


def test_a_non_taiwan_quote_writes_no_daily_partition_either(post_close_service):
    service, root, _ = post_close_service

    service._process_full_market_records([_quote("600000.SH")], t0=0.0, now_ts=0.0)

    assert not list((root / "kline_daily").rglob("*.parquet"))


def test_taiwan_daily_dates_read_the_repository_data_dir_without_creating_it(
    tmp_path, monkeypatch, deny_network
):
    from app.config import settings

    elsewhere = tmp_path / "settings_root"
    monkeypatch.setattr(settings, "data_dir", elsewhere)
    empty = KlineRepository(DataStore(tmp_path / "empty"))
    assert empty.taiwan_daily_dates() == []
    assert not (tmp_path / "empty" / "taiwan").exists()

    root = tmp_path / "repo_root"
    _write_partition(root, "taiwan/daily", LAST_SESSION)
    repo = KlineRepository(DataStore(root))
    assert repo.taiwan_daily_dates() == [LAST_SESSION]
    assert not (elsewhere / "taiwan").exists()
