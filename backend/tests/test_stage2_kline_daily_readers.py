"""Taiwan decisions take their trading days from the Taiwan daily store, never from kline_daily.

kline_daily is an A-share table the Taiwan deployment no longer writes; the few partitions
left in it hold misdated watchlist rows. A backtest without a start, the minute chart's
trading-day guess and the data status must read the same with or without them.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from app.api import backtest as backtest_api
from app.api import data as data_api
from app.api import kline
from app.tickflow.repository import DataStore, KlineRepository

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


class _ImmediateThread:
    def __init__(self, *, target, daemon):
        self.target = target

    def start(self):
        self.target()


class _NoopTimer:
    daemon = False

    def __init__(self, interval, callback):
        pass

    def start(self):
        return None


class _Request:
    def __init__(self, repo):
        self.app = SimpleNamespace(state=SimpleNamespace(repo=repo))

    async def is_disconnected(self):
        return False


async def _consume(response) -> None:
    async for _ in response.body_iterator:
        pass


@pytest.fixture
def capture_worker(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "app.backtest.worker.make_worker_task",
        lambda kind, data_dir, config: captured.append((kind, config)) or {"kind": kind},
    )
    monkeypatch.setattr(
        "app.backtest.worker.run_worker_task",
        lambda task, progress_cb=None, cancel_event=None: {"ok": True},
    )
    monkeypatch.setattr(backtest_api.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(backtest_api.threading, "Timer", _NoopTimer)
    backtest_api._running_jobs.clear()
    yield captured
    backtest_api._running_jobs.clear()


@pytest.mark.asyncio
async def test_backtest_streams_without_start_begin_at_the_first_taiwan_session(
    repo, capture_worker
):
    request = _Request(repo)
    await _consume(await backtest_api.strategy_stream(request, strategy_id="s8", end="2026-10-07"))
    await _consume(
        await backtest_api.optimize_stream(
            request,
            strategy_id="s8",
            param_grid='{"window":[5]}',
            end="2026-10-07",
        )
    )
    await _consume(
        await backtest_api.walkforward_stream(
            request,
            strategy_id="s8",
            param_grid='{"window":[5]}',
            end="2026-10-07",
            objective="total_return",
            train_days=2,
            test_days=1,
            step_days=1,
        )
    )

    starts = {kind: config.start for kind, config in capture_worker}
    assert starts == {
        "backtest": FIRST_SESSION,
        "optimize": FIRST_SESSION,
        "walkforward": FIRST_SESSION,
    }


@pytest.fixture
def minute_request(repo, monkeypatch):
    monkeypatch.setattr(kline, "_get_stock_info", lambda _repo, _symbol: {})
    monkeypatch.setattr(kline, "_session_price_limit", lambda *a, **k: (None, None))
    monkeypatch.setattr(kline.kline_sync, "fetch_minute_single", lambda *a, **k: pl.DataFrame())
    monkeypatch.setattr(kline.kline_sync, "sync_minute_batch", lambda *a, **k: pl.DataFrame())
    capabilities = SimpleNamespace(has=lambda _cap: True, limits=lambda _cap: None)
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repo=repo, capabilities=capabilities))
    )


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

    def spy(symbols, trade_date, asset_type="stock"):
        seen.append(trade_date)
        return real_batch(symbols, trade_date, asset_type=asset_type)

    monkeypatch.setattr(repo, "get_minute_batch", spy)

    kline.get_minute_batch(minute_request, {"symbols": ["2330.TWSE"]})

    assert seen == [expected]


def test_data_status_reports_the_taiwan_daily_store(repo):
    stats = data_api._safe_aggregate_daily(repo)

    assert stats["earliest_date"] == FIRST_SESSION.isoformat()
    assert stats["latest_date"] == LAST_SESSION.isoformat()
    assert stats["trading_days"] == len(SESSIONS)


def test_storage_counts_the_taiwan_daily_store(repo):
    storage = data_api._compute_storage(repo.store.data_dir)

    assert storage["taiwan_daily_files"] == len(SESSIONS)
    assert storage["taiwan_daily_size_mb"] >= 0.0


def test_adj_factor_stats_span_the_taiwan_sessions(tmp_path, monkeypatch, deny_network):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    for d in SESSIONS:
        _write_partition(tmp_path, "taiwan/daily", d)
    _write_partition(tmp_path, "kline_daily", LEFTOVER)
    factors = tmp_path / "adj_factor"
    factors.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["2330.TWSE", "2330.TWSE"],
            "trade_date": [date(2024, 6, 1), LEFTOVER],
            "adj_factor": [1.0, 1.1],
        }
    ).write_parquet(factors / "all.parquet")
    repo = KlineRepository(DataStore(tmp_path))

    stats = data_api._safe_aggregate_adj_factor(repo)

    assert stats["earliest_date"] == FIRST_SESSION.isoformat()
    assert stats["latest_date"] == LAST_SESSION.isoformat()
    assert stats["rows"] == 2


TODAY = date(2026, 10, 7)


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
    """A repository still warming up: no recursive state and no latest enriched day yet."""

    def __init__(self, data_dir: Path) -> None:
        self.store = SimpleNamespace(data_dir=data_dir)
        self.enriched_writes: list[pl.DataFrame] = []

    def get_live_agg(self) -> pl.DataFrame:
        return pl.DataFrame()

    def get_enriched_latest(self) -> tuple[pl.DataFrame, date | None]:
        return pl.DataFrame(), None

    def get_instruments(self) -> pl.DataFrame:
        return pl.DataFrame()

    def get_historical_shares(self):
        return None

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
    service = quote_service.QuoteService()
    service._repo = _WarmingRepo(tmp_path)
    return service


def test_warm_up_fallback_does_not_build_taiwan_bars_from_kline_daily(warming_service):
    warming_service._flush_live_enriched(_live_bar("2330.TWSE"))

    assert warming_service._repo.enriched_writes == []


def test_warm_up_fallback_never_scans_kline_daily_for_taiwan(warming_service, monkeypatch):
    from app.services import quote_service

    scanned: list[str] = []

    def spy(glob):
        scanned.append(glob)
        return pl.LazyFrame(schema={"symbol": pl.String, "date": pl.Date})

    monkeypatch.setattr(quote_service, "scan_daily_parquet", spy)

    warming_service._flush_live_enriched(_live_bar("2330.TWSE"))
    warming_service._flush_live_enriched(_live_bar("0050.TWSE"))
    assert scanned == []

    warming_service._flush_live_enriched(_live_bar("600000.SH"))
    assert len(scanned) == 1 and "kline_daily" in scanned[0]


def test_warm_up_skip_is_logged_once(warming_service, caplog):
    import logging

    with caplog.at_level(logging.DEBUG, logger="app.services.quote_service"):
        for _ in range(3):
            warming_service._flush_live_enriched(_live_bar("2330.TWSE"))

    skipped = [r for r in caplog.records if "full recompute skipped" in r.getMessage()]
    assert len(skipped) == 1


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
    monkeypatch.setattr(
        repo, "get_etf_instruments", lambda: pl.DataFrame({"symbol": ["0050.TWSE"]})
    )
    service = quote_service.QuoteService()
    service._repo = repo
    enriched_calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        service,
        "_flush_live_enriched",
        lambda df, extra=None, asset_type="stock", merge=False: enriched_calls.append(
            (asset_type, df["symbol"].to_list())
        ),
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
    assert not list((root / "kline_etf_daily").rglob("*.parquet"))
    assert enriched_calls == [("stock", ["2330.TWSE"]), ("etf", ["0050.TWSE"])]


def test_non_taiwan_poll_still_writes_kline_daily(post_close_service):
    service, root, _ = post_close_service

    service._process_full_market_records([_quote("600000.SH")], t0=0.0, now_ts=0.0)

    written = pl.read_parquet(root / "kline_daily" / f"date={TODAY.isoformat()}" / "part.parquet")
    assert written["symbol"].to_list() == ["600000.SH"]


class _IncrementalStockRepo(_WarmingRepo):
    """Stock has its incremental state; index has no latest enriched day and always falls back."""

    def get_live_agg(self) -> pl.DataFrame:
        return pl.DataFrame({"symbol": ["2330.TWSE"]})

    def get_enriched_latest(self) -> tuple[pl.DataFrame, date | None]:
        return pl.DataFrame({"symbol": ["2330.TWSE"]}), date(2026, 10, 6)

    def get_enriched_latest_asset(self, asset_type) -> tuple[pl.DataFrame, date | None]:
        return pl.DataFrame(), None


def test_index_fallback_does_not_repeat_the_skip_log(warming_service, monkeypatch, caplog):
    import logging

    warming_service._repo = _IncrementalStockRepo(warming_service._repo.store.data_dir)
    monkeypatch.setattr(warming_service, "_enrich_today_incremental", lambda daily, *a, **k: daily)

    with caplog.at_level(logging.DEBUG, logger="app.services.quote_service"):
        for _ in range(3):
            warming_service._flush_live_enriched(_live_bar("2330.TWSE"), asset_type="stock")
            warming_service._flush_live_enriched(_live_bar("t00.TWSE"), asset_type="index")

    skipped = [r for r in caplog.records if "full recompute skipped" in r.getMessage()]
    assert len(skipped) <= 1
    assert [len(df) for df in warming_service._repo.enriched_writes] == [1, 1, 1]


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
