"""Unit and Integration tests for Taiwan Screener Service (Phase 6B).

Covers:
  - Supported universe only (warrants/unsupported excluded)
  - TWSE vs TPEx filtering
  - Stock vs ETF filtering
  - change_pct canonical decimal representation (0.05 = 5%)
  - Volume canonical representation in shares
  - Missing data is None/null, never converted to 0
  - Price limit proximity & NO_LIMIT not_applicable (distance is null)
  - Technical indicator calculations & filtering (MA, RSI, Momentum, VolRatio)
  - Institutional & Margin joins
  - Deterministic sort with symbol ASC tie-breaker
  - Pagination applied after filtering
  - Same request produces 100% deterministic results
  - Zero external provider / HTTP calls during screening
  - POST /api/taiwan/screener/run router integration
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.screener import (
    TaiwanScreenerRequest,
    TaiwanScreenerResponse,
    TaiwanScreenerRunRequest,
    TaiwanScreenerService,
)
from app.taiwan.universe import get_security_master
from app.taiwan.volume_patterns import VOLUME_PATTERN_COLUMNS


def _seed_daily_store(store: TaiwanDailyStore, days: int = 30):
    """Seed synthetic daily data for key test symbols."""
    symbols = [
        ("2330.TWSE", 2000.0, 50000.0),    # TSMC: ~2000 TWD, 50k shares
        ("0050.TWSE", 100.0, 200000.0),    # ETF: ~100 TWD, 200k shares
        ("00631L.TWSE", 35.0, 1000000.0),  # Leveraged ETF 2x: ~35 TWD
        ("00632R.TWSE", 10.0, 800000.0),   # Inverse ETF 1x: ~10 TWD
        ("00646.TWSE", 75.0, 20000.0),     # Foreign ETF (NO_LIMIT): ~75 TWD
        ("8069.TPEX", 150.0, 30000.0),     # TPEx stock: ~150 TWD
    ]

    start_date = date(2026, 7, 20)
    for sym, base_price, base_vol in symbols:
        rows = []
        for i in range(days):
            d = start_date + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            # Progressive trend so change_pct and RSI have distinct values
            factor = 1.0 + (i * 0.005)
            p = round(base_price * factor, 2)
            rows.append({
                "symbol": sym,
                "date": d.isoformat(),
                "open": p - 1.0,
                "high": p + 2.0,
                "low": p - 2.0,
                "close": p,
                "volume": base_vol * (1.0 + (i % 3) * 0.1),
                "amount": p * base_vol,
                "quote_ts": None,
            })
        store.write_batch(pl.DataFrame(rows))


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    from app.config import settings
    from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
    from app.taiwan.share_capital_store import TaiwanShareCapitalStore

    get_security_master()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    _seed_daily_store(store, days=30)
    TaiwanEnrichedRefreshService(
        daily_store=store,
        enriched_root=tmp_path / "kline_daily_enriched",
        share_capital=TaiwanShareCapitalStore(data_dir=tmp_path),
    ).refresh()
    yield store


def _partition_path(store: TaiwanDailyStore) -> Path:
    from app.config import settings

    latest = store.latest_date()
    return Path(settings.data_dir) / "kline_daily_enriched" / f"date={latest.isoformat()}" / "part.parquet"


@pytest.fixture
def screener_service(tmp_store):
    return TaiwanScreenerService(daily_store=tmp_store)


class TestTaiwanScreenerCore:
    def test_default_run_returns_supported_symbols(self, screener_service):
        req = TaiwanScreenerRequest()
        res = screener_service.run(req)

        assert res.total >= 5
        assert len(res.items) >= 5
        symbols = [item.symbol for item in res.items]
        # Core symbols should be present
        assert "2330.TWSE" in symbols
        assert "0050.TWSE" in symbols
        assert "8069.TPEX" in symbols

    def test_warrants_and_unsupported_excluded(self, screener_service):
        master = get_security_master()
        req = TaiwanScreenerRequest(exchange="ALL", instrument="ALL")
        res = screener_service.run(req)

        for item in res.items:
            assert item.instrument_type in {"stock", "etf"}
            inst = master.get_instrument(item.symbol)
            assert inst is not None
            assert inst.is_supported is True
            itype = inst.instrument_type.value if hasattr(inst.instrument_type, "value") else inst.instrument_type
            assert itype in {"stock", "etf"}

    def test_exchange_filter(self, screener_service):
        # TWSE only
        req_twse = TaiwanScreenerRequest(exchange="TWSE")
        res_twse = screener_service.run(req_twse)
        for item in res_twse.items:
            assert item.exchange == "TWSE"
            assert item.symbol.endswith(".TWSE")

        # TPEX only
        req_tpex = TaiwanScreenerRequest(exchange="TPEX")
        res_tpex = screener_service.run(req_tpex)
        for item in res_tpex.items:
            assert item.exchange == "TPEX"
            assert item.symbol.endswith(".TPEX")

    def test_instrument_filter(self, screener_service):
        # Stock only
        req_stock = TaiwanScreenerRequest(instrument="stock")
        res_stock = screener_service.run(req_stock)
        for item in res_stock.items:
            assert item.instrument_type == "stock"

        # ETF only
        req_etf = TaiwanScreenerRequest(instrument="etf")
        res_etf = screener_service.run(req_etf)
        for item in res_etf.items:
            assert item.instrument_type == "etf"

    def test_price_range_filter(self, screener_service):
        req = TaiwanScreenerRequest(price_min=50.0, price_max=200.0)
        res = screener_service.run(req)

        for item in res.items:
            assert 50.0 <= item.close <= 200.0

    def test_volume_shares_filter(self, screener_service):
        # Filter for volume >= 50,000 shares
        req = TaiwanScreenerRequest(volume_min=50000.0)
        res = screener_service.run(req)

        for item in res.items:
            assert item.volume >= 50000.0

    def test_indicators_computed_and_filterable(self, screener_service):
        req = TaiwanScreenerRequest()
        res = screener_service.run(req)

        # Confirm indicators are not null
        tsmc = next(item for item in res.items if item.symbol == "2330.TWSE")
        assert tsmc.ma5 is not None
        assert tsmc.ma10 is not None
        assert tsmc.ma20 is not None
        assert tsmc.rsi_14 is not None
        assert tsmc.momentum_5d is not None
        assert tsmc.vol_ratio_5d is not None
        # change_pct is decimal (0.05 = 5%)
        assert abs(tsmc.change_pct) < 0.5

    def test_indicators_equal_the_published_partition(self, screener_service, tmp_store):
        published = pl.read_parquet(_partition_path(tmp_store)).filter(pl.col("symbol") == "2330.TWSE").row(0, named=True)
        res = screener_service.run(TaiwanScreenerRequest())
        tsmc = next(item for item in res.items if item.symbol == "2330.TWSE")
        for field in ("change_pct", "ma5", "ma10", "ma20", "rsi_14", "momentum_5d", "vol_ratio_5d"):
            assert published[field] is not None
            assert getattr(tsmc, field) == published[field], field
        assert "indicators" not in res.degraded_sections

    def test_indicators_follow_the_partition_not_a_recomputation(self, screener_service, tmp_store):
        path = _partition_path(tmp_store)
        frame = pl.read_parquet(path).with_columns(
            pl.when(pl.col("symbol") == "2330.TWSE").then(99.0).otherwise(pl.col("rsi_14")).alias("rsi_14"),
            pl.when(pl.col("symbol") == "2330.TWSE").then(7.5).otherwise(pl.col("vol_ratio_5d")).alias("vol_ratio_5d"),
        )
        frame.write_parquet(path)
        res = screener_service.run(TaiwanScreenerRequest(rsi_14_min=98.0, vol_ratio_5d_min=7.0))
        assert [item.symbol for item in res.items] == ["2330.TWSE"]
        assert res.items[0].rsi_14 == 99.0

    def test_missing_partition_nulls_indicators_and_flags_degraded(self, screener_service, tmp_store):
        _partition_path(tmp_store).unlink()
        res = screener_service.run(TaiwanScreenerRequest())
        assert res.total >= 5
        assert "indicators" in res.degraded_sections
        for item in res.items:
            for field in ("change_pct", "ma5", "ma10", "ma20", "rsi_14", "momentum_5d", "vol_ratio_5d"):
                assert getattr(item, field) is None, field

    def test_basic_columns_only_partition_is_degraded(self, screener_service, tmp_store):
        path = _partition_path(tmp_store)
        pl.read_parquet(path).select(["symbol", "date", "open", "high", "low", "close", "volume"]).write_parquet(path)
        res = screener_service.run(TaiwanScreenerRequest())
        assert "indicators" in res.degraded_sections
        assert all(item.rsi_14 is None and item.vol_ratio_5d is None for item in res.items)

    def test_unreadable_partition_is_degraded_not_an_error(self, screener_service, tmp_store):
        _partition_path(tmp_store).write_bytes(b"not a parquet file")
        res = screener_service.run(TaiwanScreenerRequest())
        assert "indicators" in res.degraded_sections
        assert all(item.rsi_14 is None for item in res.items)

    def test_above_ma5_filter(self, screener_service):
        req = TaiwanScreenerRequest(above_ma5=True)
        res = screener_service.run(req)
        for item in res.items:
            assert item.close > item.ma5

    def test_price_limits_and_no_limit_handling(self, screener_service):
        req = TaiwanScreenerRequest()
        res = screener_service.run(req)

        # 2330.TWSE -> ±10%
        tsmc = next(item for item in res.items if item.symbol == "2330.TWSE")
        assert tsmc.price_limit_pct == 0.10
        assert tsmc.is_no_limit is False
        assert tsmc.limit_up is not None
        assert tsmc.distance_to_upper_limit is not None

        # 00631L.TWSE -> ±20%
        leveraged = next(item for item in res.items if item.symbol == "00631L.TWSE")
        assert leveraged.price_limit_pct == 0.20
        assert leveraged.is_no_limit is False

        # 00646.TWSE -> NO_LIMIT
        foreign = next(item for item in res.items if item.symbol == "00646.TWSE")
        assert foreign.is_no_limit is True
        assert foreign.price_limit_pct is None
        assert foreign.limit_up is None
        assert foreign.distance_to_upper_limit is None

    def test_no_limit_not_affected_by_near_limit_filter(self, screener_service):
        """Foreign ETFs with NO_LIMIT must not be matched by near_upper_limit."""
        req = TaiwanScreenerRequest(near_upper_limit=True)
        res = screener_service.run(req)
        symbols = [item.symbol for item in res.items]
        assert "00646.TWSE" not in symbols

    def test_unconfirmed_market_profile_does_not_fallback_to_10pct(self, screener_service, monkeypatch):
        """When MarketProfileBridge raises ValueError for unconfirmed profile, limit fields must be None (never 10%)."""
        from app.taiwan.universe.models import MarketProfileBridge

        orig_get_price_limit_pct = MarketProfileBridge.get_price_limit_pct

        def _mock_get_price_limit_pct(inst):
            if inst.symbol == "2330.TWSE":
                raise ValueError("Refusing to apply regulatory market rules to unconfirmed ETF")
            return orig_get_price_limit_pct(inst)

        monkeypatch.setattr(MarketProfileBridge, "get_price_limit_pct", _mock_get_price_limit_pct)

        # Run screener without filter
        req = TaiwanScreenerRequest()
        res = screener_service.run(req)
        item_2330 = next(i for i in res.items if i.symbol == "2330.TWSE")

        # Must be None, NEVER 0.10 or manual math
        assert item_2330.price_limit_pct is None
        assert item_2330.limit_up is None
        assert item_2330.limit_down is None
        assert item_2330.distance_to_upper_limit is None
        assert item_2330.distance_to_lower_limit is None

        # Near limit filter must NOT match this row
        req_limit = TaiwanScreenerRequest(near_upper_limit=True)
        res_limit = screener_service.run(req_limit)
        limit_symbols = [i.symbol for i in res_limit.items]
        assert "2330.TWSE" not in limit_symbols

    def test_deterministic_sort_with_tie_breaker(self, screener_service):
        # Sort by volume desc
        req = TaiwanScreenerRequest(sort_by="volume", sort_order="desc")
        res1 = screener_service.run(req)
        res2 = screener_service.run(req)

        assert [i.symbol for i in res1.items] == [i.symbol for i in res2.items]

        # Verify descending order
        vols = [i.volume for i in res1.items]
        assert vols == sorted(vols, reverse=True)

    def test_pagination_after_filtering(self, screener_service):
        req_p1 = TaiwanScreenerRequest(page=1, page_size=2)
        res_p1 = screener_service.run(req_p1)
        assert len(res_p1.items) == 2
        assert res_p1.total >= 5

        req_p2 = TaiwanScreenerRequest(page=2, page_size=2)
        res_p2 = screener_service.run(req_p2)
        assert len(res_p2.items) == 2

        # P1 and P2 symbols must not overlap
        p1_syms = {i.symbol for i in res_p1.items}
        p2_syms = {i.symbol for i in res_p2.items}
        assert p1_syms.isdisjoint(p2_syms)

    def test_zero_provider_http_calls_during_run(self, monkeypatch, screener_service):
        """Running the screener MUST NOT make any provider or HTTP calls."""
        from urllib.request import urlopen

        def _forbidden_urlopen(*args, **kwargs):
            pytest.fail("Network call forbidden during screener execution!")

        monkeypatch.setattr("urllib.request.urlopen", _forbidden_urlopen)

        req = TaiwanScreenerRequest()
        res = screener_service.run(req)
        assert res.total > 0

    def test_api_route_integration(self, tmp_store, monkeypatch):
        """Test POST /api/taiwan/screener/run via FastAPI TestClient."""
        # Monkeypatch TaiwanDailyStore inside the service to use tmp_store
        from app.taiwan import screener
        monkeypatch.setattr(screener, "TaiwanDailyStore", lambda *args, **kwargs: tmp_store)

        client = TestClient(app, client=("127.0.0.1", 50000))
        payload = {
            "exchange": "ALL",
            "instrument": "ALL",
            "price_min": 10.0,
            "page": 1,
            "page_size": 10,
            "sort_by": "close",
            "sort_order": "desc",
        }
        resp = client.post("/api/taiwan/screener/run", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "data_dates" in data
        assert data["total"] >= 5
        assert len(data["items"]) >= 5


def _set_patterns(store: TaiwanDailyStore, values: dict[str, dict[str, bool | None]]) -> None:
    """Overwrite the published pattern columns: every pattern False except the given values."""
    path = _partition_path(store)
    frame = pl.read_parquet(path)
    frame = frame.with_columns([
        pl.Series(c, [values.get(s, {}).get(c, False) for s in frame["symbol"]], dtype=pl.Boolean)
        for c in VOLUME_PATTERN_COLUMNS
    ])
    frame.write_parquet(path)


class TestTaiwanScreenerVolumePatterns:
    def test_the_nl_whitelist_stays_without_volume_patterns(self):
        assert "volume_patterns" not in TaiwanScreenerRequest.model_fields
        assert "volume_patterns" in TaiwanScreenerRunRequest.model_fields

    def test_published_partition_carries_every_pattern(self, screener_service, tmp_store):
        assert set(VOLUME_PATTERN_COLUMNS) <= set(pl.read_parquet(_partition_path(tmp_store)).columns)
        res = screener_service.run(TaiwanScreenerRequest())
        assert "volume_patterns" not in res.degraded_sections

    def test_rows_list_their_patterns_and_a_base_request_still_runs(self, screener_service, tmp_store):
        _set_patterns(tmp_store, {"2330.TWSE": {"vp_double": True, "vp_low": True}, "0050.TWSE": {"vp_flat": None}})
        res = screener_service.run(TaiwanScreenerRequest())
        by_symbol = {item.symbol: item for item in res.items}
        assert by_symbol["2330.TWSE"].volume_patterns == ["vp_double", "vp_low"]
        assert by_symbol["0050.TWSE"].volume_patterns == []
        assert all(not p.startswith("vp_") for item in res.items for p in item.signals)

    def test_several_patterns_must_all_hit(self, screener_service, tmp_store):
        _set_patterns(tmp_store, {
            "2330.TWSE": {"vp_double": True, "vp_low": True},
            "0050.TWSE": {"vp_double": True},
            "8069.TPEX": {"vp_double": None, "vp_low": True},
        })
        both = screener_service.run(TaiwanScreenerRunRequest(volume_patterns=["vp_double", "vp_low"]))
        assert [item.symbol for item in both.items] == ["2330.TWSE"]
        single = screener_service.run(TaiwanScreenerRunRequest(volume_patterns=["vp_double"]))
        assert [item.symbol for item in single.items] == ["0050.TWSE", "2330.TWSE"]

    def test_an_undecidable_pattern_never_matches(self, screener_service, tmp_store):
        _set_patterns(tmp_store, {s: {"vp_up_stall": None} for s in pl.read_parquet(_partition_path(tmp_store))["symbol"]})
        res = screener_service.run(TaiwanScreenerRunRequest(volume_patterns=["vp_up_stall"]))
        assert res.total == 0

    def test_an_unknown_pattern_matches_nothing(self, screener_service, tmp_store):
        _set_patterns(tmp_store, {"2330.TWSE": {"vp_double": True}})
        for unknown in (["vp_unknown"], ["signal_limit_up"], ["vp_double", "close"]):
            assert screener_service.run(TaiwanScreenerRunRequest(volume_patterns=unknown)).total == 0

    def test_a_partition_published_before_the_patterns_is_degraded(self, screener_service, tmp_store):
        path = _partition_path(tmp_store)
        pl.read_parquet(path).drop(list(VOLUME_PATTERN_COLUMNS)).write_parquet(path)

        res = screener_service.run(TaiwanScreenerRequest())
        assert res.total >= 5
        assert "volume_patterns" in res.degraded_sections
        assert "indicators" not in res.degraded_sections
        assert all(item.volume_patterns == [] for item in res.items)
        filtered = screener_service.run(TaiwanScreenerRunRequest(volume_patterns=["vp_double"]))
        assert filtered.total == 0
        assert "volume_patterns" in filtered.degraded_sections

    def test_a_missing_partition_is_degraded(self, screener_service, tmp_store):
        _partition_path(tmp_store).unlink()
        res = screener_service.run(TaiwanScreenerRequest())
        assert {"indicators", "volume_patterns"} <= set(res.degraded_sections)

    def test_rebuilding_rows_keeps_sparse_pattern_columns(self, screener_service):
        rows = pl.DataFrame({
            "symbol": [f"T{i:04d}.TWSE" for i in range(150)],
            "close": [None] * 150,
            "vp_double": [None] * 149 + [True],
        }, schema={"symbol": pl.String, "close": pl.Float64, "vp_double": pl.Boolean})
        rebuilt = screener_service._enrich_price_limits(rows)
        assert rebuilt["vp_double"].to_list() == [None] * 149 + [True]

    def test_api_route_accepts_volume_patterns(self, tmp_store, monkeypatch):
        from app.taiwan import screener

        monkeypatch.setattr(screener, "TaiwanDailyStore", lambda *args, **kwargs: tmp_store)
        _set_patterns(tmp_store, {"8069.TPEX": {"vp_step_up": True}})
        client = TestClient(app, client=("127.0.0.1", 50000))

        resp = client.post("/api/taiwan/screener/run", json={"volume_patterns": ["vp_step_up"]})
        assert resp.status_code == 200
        data = resp.json()
        assert [item["symbol"] for item in data["items"]] == ["8069.TPEX"]
        assert data["items"][0]["volume_patterns"] == ["vp_step_up"]
