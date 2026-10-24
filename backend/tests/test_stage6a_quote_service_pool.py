"""QuoteService full-market round: custom sources without realtime, and the index part of the pool."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from app.market_time import cn_today
from app.services import quote_service as qs_mod
from app.services.quote_service import QuoteService


def _service() -> QuoteService:
    svc = QuoteService()
    svc._app_state = None
    svc._repo = MagicMock()
    svc._repo.get_index_symbol_set.return_value = set()
    svc._repo.get_etf_instruments.return_value = pl.DataFrame()
    return svc


# ── B033: a custom source without a realtime dataset ──


def test_custom_source_without_realtime_reports_it_and_skips_the_round(caplog):
    svc = _service()
    taiwan_provider = MagicMock()

    with (
        caplog.at_level(logging.WARNING, logger=qs_mod.__name__),
        patch("app.services.preferences.get_realtime_data_provider", return_value="my_source"),
        patch("app.data_providers.custom.provider_has_dataset", return_value=False),
        patch("app.data_providers.registry.get_provider", return_value=taiwan_provider),
        patch.object(QuoteService, "_process_full_market_records") as process,
    ):
        svc._fetch_full_market_quotes()
        svc._fetch_full_market_quotes()

    messages = [r.getMessage() for r in caplog.records]
    assert not any("is not defined" in m for m in messages), messages
    missing = [m for m in messages if "my_source" in m and "realtime" in m]
    assert len(missing) == 1, messages
    process.assert_not_called()
    taiwan_provider.get_realtime.assert_not_called()


def test_builtin_taiwan_source_still_polls():
    svc = _service()
    provider = MagicMock()
    provider.get_realtime.return_value = pl.DataFrame()

    with (
        patch("app.services.preferences.get_realtime_data_provider", return_value="taiwan"),
        patch("app.data_providers.registry.get_provider", return_value=provider),
        patch("app.services.watchlist.list_symbols", return_value=[{"symbol": "2330.TWSE"}]),
    ):
        svc._fetch_full_market_quotes()

    provider.get_realtime.assert_called_once()


# ── B153: the Taiwan pool asks for the index symbols and keeps their cache ──


def _captured_pool(index_symbols: list[str], pull_index: bool = True) -> list[str]:
    svc = _service()
    captured: dict = {}

    class _Provider:
        def get_realtime(self, symbols):
            captured["symbols"] = list(symbols)
            return pl.DataFrame()

    with (
        patch("app.services.preferences.get_realtime_data_provider", return_value="taiwan"),
        patch("app.services.preferences.get_realtime_pull_index", return_value=pull_index),
        patch("app.services.preferences.get_realtime_index_symbols", return_value=index_symbols),
        patch("app.data_providers.registry.get_provider", return_value=_Provider()),
        patch("app.services.watchlist.list_symbols", return_value=[{"symbol": "2330.TWSE"}]),
    ):
        svc._fetch_full_market_quotes()
    return captured["symbols"]


def test_taiwan_pool_requests_the_core_index_symbols():
    pool = _captured_pool([])

    assert set(QuoteService.CORE_INDEX_SYMBOLS) <= set(pool)
    assert "2330.TWSE" in pool


def test_taiwan_pool_follows_the_configured_index_list():
    assert set(_captured_pool(["t00.TWSE"])) == {"t00.TWSE", "2330.TWSE"}


def test_taiwan_pool_skips_indices_when_index_pull_is_off():
    assert _captured_pool([], pull_index=False) == ["2330.TWSE"]


def test_round_without_index_quotes_keeps_the_index_cache():
    svc = _service()
    cached = pl.DataFrame({"symbol": ["t00.TWSE"], "last_price": [23000.0], "change_pct": [0.5]})
    svc._index_quotes_cache = cached
    records = [{
        "symbol": "2330.TWSE", "last_price": 1000.0, "prev_close": 990.0, "open": 995.0,
        "high": 1001.0, "low": 990.0, "volume": 1000.0, "status": "realtime",
    }]

    with (
        patch.object(qs_mod, "_persist_last_fetch"),
        patch.object(QuoteService, "_flush_live_enriched"),
        patch.object(QuoteService, "_evaluate_monitors"),
    ):
        svc._process_full_market_records(records, t0=0.0, now_ts=0.0)

    assert svc.get_index_quotes().equals(cached)


def _hybrid_index_frame(symbols: list[str]) -> pl.DataFrame:
    """Rows shaped as TaiwanHybridProvider._live_quote_frame emits them: close, percentage change_pct."""
    today = cn_today()
    rows = {
        "t00.TWSE": (23100.0, 23000.0, 4_000_000.0),
        "o00.TPEX": (251.0, 250.0, 900_000.0),
        "2330.TWSE": (1010.0, 1000.0, 20_000_000.0),
    }
    return pl.DataFrame([
        {
            "symbol": s, "date": today, "open": prev, "high": close, "low": prev, "close": close,
            "volume": volume, "amount": None, "prev_close": prev,
            "change_pct": round((close / prev - 1) * 100, 4), "quote_ts": 1_761_000_000_000,
            "status": "realtime",
        }
        for s, (close, prev, volume) in rows.items() if s in symbols
    ])


class _HybridFeed:
    def __init__(self) -> None:
        self.requested: list[list[str]] = []

    def get_realtime(self, symbols):
        self.requested.append(list(symbols))
        return _hybrid_index_frame(list(symbols))


def _poll_once(svc: QuoteService, feed: _HybridFeed, *, pull_index: bool = True,
               index_symbols: list[str] | None = None, watchlist: list[str] | None = None):
    with (
        patch("app.services.preferences.get_realtime_data_provider", return_value="taiwan"),
        patch("app.services.preferences.get_realtime_pull_index", return_value=pull_index),
        patch("app.services.preferences.get_realtime_index_symbols",
              return_value=["t00.TWSE", "o00.TPEX"] if index_symbols is None else index_symbols),
        patch("app.data_providers.registry.get_provider", return_value=feed),
        patch("app.services.watchlist.list_symbols",
              return_value=[{"symbol": s} for s in (watchlist or [])]),
        patch.object(qs_mod, "_persist_last_fetch"),
        patch.object(QuoteService, "_flush_live_enriched"),
        patch.object(QuoteService, "_evaluate_monitors"),
        patch.object(QuoteService, "_record_minute_bars") as minute,
        patch.object(QuoteService, "_track_closing_quotes"),
    ):
        svc._fetch_full_market_quotes()
    return minute


def test_round_with_index_quotes_replaces_the_index_cache():
    svc = _service()
    svc._index_quotes_cache = pl.DataFrame({"symbol": ["t00.TWSE"], "last_price": [1.0]})

    _poll_once(svc, _HybridFeed())

    row = svc.get_index_quotes(["t00.TWSE"]).row(0, named=True)
    assert row["close"] == pytest.approx(23100.0)
    assert row["last_price"] == pytest.approx(23100.0)
    assert row["change_amount"] == pytest.approx(100.0)
    assert row["change_pct"] == pytest.approx(0.4348, abs=1e-4)
    assert row.get("volume") is None


def test_index_cache_price_reaches_the_indices_endpoint_and_the_sector_snapshot():
    from app.api.intraday import index_quotes
    from app.services.sector_monitor import SectorMonitorService

    svc = _service()
    _poll_once(svc, _HybridFeed())

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(quote_service=svc)))
    body = index_quotes(request, symbols="t00.TWSE")
    assert body["source"] == "realtime"
    assert body["rows"][0]["last_price"] == pytest.approx(23100.0)
    assert body["rows"][0]["close"] == pytest.approx(23100.0)

    index_rows = SectorMonitorService._row_map(svc.get_index_quotes(), index_values_are_percent=True)
    snapshot = SectorMonitorService._index_snapshot({"symbol": "t00.TWSE"}, index_rows)
    assert snapshot["price"] == pytest.approx(23100.0)
    assert snapshot["change_pct"] == pytest.approx(0.004348, abs=1e-6)


# ── F3: one index set for classification, minute bars and the closing pool ──


def _index_rule_engine() -> MagicMock:
    engine = MagicMock()
    engine.rules = {
        "idx": {"enabled": True, "asset_type": "index", "scope": "symbols", "symbols": ["t00.TWSE"]},
    }
    engine.has_asset_rules.return_value = False
    return engine


@pytest.mark.parametrize(
    ("pull_index", "index_symbols"),
    [(False, ["t00.TWSE", "o00.TPEX"]), (True, ["o00.TPEX"])],
    ids=["index_pull_off", "t00_not_in_preferences"],
)
def test_rule_index_symbol_is_an_index_everywhere(pull_index, index_symbols):
    svc = _service()
    svc._app_state = SimpleNamespace(monitor_engine=_index_rule_engine())
    feed = _HybridFeed()

    minute = _poll_once(
        svc, feed, pull_index=pull_index, index_symbols=index_symbols, watchlist=["2330.TWSE"],
    )

    assert "t00.TWSE" in feed.requested[0]
    assert svc.get_index_quotes(["t00.TWSE"])["close"].to_list() == [23100.0]
    assert svc._symbol_count == 1
    assert "t00.TWSE" not in svc._round_symbols
    assert "2330.TWSE" in svc._round_symbols
    minute_symbols = {r["symbol"] for r in minute.call_args.args[0]}
    assert "t00.TWSE" not in minute_symbols
    assert "2330.TWSE" in minute_symbols
