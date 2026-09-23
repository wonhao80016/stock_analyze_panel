"""kline 每秒热路径测试 — _get_stock_info 走内存缓存, _attach_ext 复用签名缓存 value_map。

契约等值: 输出与旧的 DuckDB 扫描 / 逐配置读 parquet 路径一致。
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl

from app.api import screener
from app.api.kline import (
    _attach_ext,
    _get_stock_info,
    _maybe_inject_live_candle,
    _needs_local_history,
)
from app.services.ext_data import ExtConfig, ExtConfigStore, ExtField


class _FakeRepo:
    """最小 repo 替身: _get_stock_info/_load_ext_value_maps 只用到这些成员。"""

    def __init__(self, instruments: pl.DataFrame, data_dir) -> None:
        self._instruments = instruments
        self.store = SimpleNamespace(data_dir=data_dir, db=None)
        self.execute_one_calls = 0

    def get_instruments(self) -> pl.DataFrame:
        return self._instruments

    def execute_one(self, *args, **kwargs):
        self.execute_one_calls += 1
        raise AssertionError("热路径不得再走 DuckDB 扫 instruments parquet")


def _instruments_df() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["600000.SH", "000001.SZ", "510300.SH"],
        "name": ["浦发银行", "平安银行", "沪深300ETF"],
        "total_shares": [1.0e9, 2.0e9, None],
        "float_shares": [5.0e8, 1.0e9, None],
    })


# ---------------------------------------------------------------- _get_stock_info

def test_get_stock_info_reads_memory_cache():
    repo = _FakeRepo(_instruments_df(), None)
    assert _get_stock_info(repo, "600000.SH") == {
        "name": "浦发银行", "total_shares": 1.0e9, "float_shares": 5.0e8,
    }
    assert repo.execute_one_calls == 0


def test_get_stock_info_null_shares_become_none():
    repo = _FakeRepo(_instruments_df(), None)
    info = _get_stock_info(repo, "510300.SH")
    assert info["name"] == "沪深300ETF"
    assert info["total_shares"] is None
    assert info["float_shares"] is None


def test_get_stock_info_missing_symbol_returns_empty():
    repo = _FakeRepo(_instruments_df(), None)
    assert _get_stock_info(repo, "999999.SH") == {}


def test_get_stock_info_missing_columns_returns_empty_like_sql_error():
    df = pl.DataFrame({"symbol": ["600000.SH"], "name": ["浦发银行"]})
    repo = _FakeRepo(df, None)
    assert _get_stock_info(repo, "600000.SH") == {}


def test_get_stock_info_repo_failure_returns_empty():
    repo = SimpleNamespace()
    repo.get_instruments = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    assert _get_stock_info(repo, "600000.SH") == {}


# ------------------------------------------------- _get_stock_info (Taiwan fallback)
# The legacy A-share instruments cache is always empty under a pure-Taiwan deployment
# (no data/instruments/), so a .TWSE/.TPEX symbol must fall back to the Taiwan security
# master (name) and TaiwanShareCapitalStore (issued shares, display-only market cap).


def test_get_stock_info_taiwan_uses_security_master_and_share_capital(monkeypatch):
    repo = _FakeRepo(pl.DataFrame(), None)

    class _FakeInst:
        name = "TSMC"
        instrument_type = "stock"

    monkeypatch.setattr(
        "app.taiwan.universe.get_security_master",
        lambda: SimpleNamespace(get_instrument=lambda s: _FakeInst() if s == "2330.TWSE" else None),
    )

    class _FakeShareStore:
        def load(self):
            return pl.DataFrame({"symbol": ["2330.TWSE"], "issued_shares": [25_930_380_458]})

    monkeypatch.setattr("app.taiwan.share_capital_store.TaiwanShareCapitalStore", _FakeShareStore)

    assert _get_stock_info(repo, "2330.TWSE") == {
        "name": "TSMC", "total_shares": 25_930_380_458, "float_shares": None,
    }


def test_get_stock_info_taiwan_missing_share_capital_leaves_total_shares_none(monkeypatch):
    repo = _FakeRepo(pl.DataFrame(), None)

    class _FakeInst:
        name = "Test Stock"
        instrument_type = "stock"

    monkeypatch.setattr(
        "app.taiwan.universe.get_security_master",
        lambda: SimpleNamespace(get_instrument=lambda s: _FakeInst()),
    )

    class _EmptyShareStore:
        def load(self):
            return pl.DataFrame(schema={"symbol": pl.Utf8, "issued_shares": pl.Int64})

    monkeypatch.setattr("app.taiwan.share_capital_store.TaiwanShareCapitalStore", _EmptyShareStore)

    info = _get_stock_info(repo, "9999.TWSE")
    assert info == {"name": "Test Stock", "total_shares": None, "float_shares": None}


def test_get_stock_info_taiwan_etf_has_no_share_count(monkeypatch):
    """ETF units are not company share capital (docs/taiwan-share-capital-phase-6h.md)."""
    repo = _FakeRepo(pl.DataFrame(), None)

    class _FakeInst:
        name = "Yuanta Taiwan 50"
        instrument_type = "etf"

    monkeypatch.setattr(
        "app.taiwan.universe.get_security_master",
        lambda: SimpleNamespace(get_instrument=lambda s: _FakeInst()),
    )

    class _FakeShareStore:
        def load(self):
            return pl.DataFrame({"symbol": ["0050.TWSE"], "issued_shares": [100_000_000]})

    monkeypatch.setattr("app.taiwan.share_capital_store.TaiwanShareCapitalStore", _FakeShareStore)

    assert _get_stock_info(repo, "0050.TWSE") == {
        "name": "Yuanta Taiwan 50", "total_shares": None, "float_shares": None,
    }


def test_get_stock_info_taiwan_lookup_failure_returns_none_fields_not_raise(monkeypatch):
    repo = _FakeRepo(pl.DataFrame(), None)
    monkeypatch.setattr(
        "app.taiwan.universe.get_security_master",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        "app.taiwan.share_capital_store.TaiwanShareCapitalStore",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert _get_stock_info(repo, "2330.TWSE") == {
        "name": None, "total_shares": None, "float_shares": None,
    }


def test_get_stock_info_a_share_unaffected_by_taiwan_fallback():
    """A .SH/.SZ symbol never matches the Taiwan suffix check, so a miss still returns {}."""
    repo = _FakeRepo(_instruments_df(), None)
    assert _get_stock_info(repo, "300999.SZ") == {}


# ---------------------------------------------------------------- _attach_ext

def test_attach_ext_empty_columns_untouched():
    resp = {"symbol": "600000.SH", "stock_info": {"name": "x"}, "rows": []}
    repo = _FakeRepo(_instruments_df(), None)
    out = _attach_ext(resp, repo, "600000.SH", None)
    assert out is resp
    assert "ext" not in out["stock_info"]
    out = _attach_ext(resp, repo, "600000.SH", "   ")
    assert "ext" not in out["stock_info"]


def test_attach_ext_invalid_spec_untouched():
    resp = {"symbol": "600000.SH", "stock_info": {"name": "x"}, "rows": []}
    repo = _FakeRepo(_instruments_df(), None)
    out = _attach_ext(resp, repo, "600000.SH", "foo_bar")  # 无 '.' 分隔
    assert out is resp
    assert "ext" not in out["stock_info"]


def test_attach_ext_maps_values_from_loader(monkeypatch):
    resp = {"symbol": "600000.SH", "stock_info": {"name": "x"}, "rows": []}
    original_info = resp["stock_info"]
    repo = _FakeRepo(_instruments_df(), None)
    monkeypatch.setattr(
        screener, "_load_ext_value_maps",
        lambda r, cols: {"cfg1__score": {"600000.SH": 88.5}, "cfg1__note": {}},
    )
    out = _attach_ext(resp, repo, "600000.SH", "cfg1.score,cfg1.note")
    assert out["stock_info"]["ext"] == {"cfg1__score": 88.5, "cfg1__note": None}
    # 原地更新 resp 是该函数契约; 但原 stock_info 字典对象不被修改 (与旧实现一致)
    assert out is resp
    assert "ext" not in original_info
    assert out["stock_info"] is not original_info


def test_attach_ext_loader_failure_yields_none_values(monkeypatch):
    resp = {"symbol": "600000.SH", "stock_info": {}, "rows": []}
    repo = _FakeRepo(_instruments_df(), None)

    def _boom(r, cols):
        raise RuntimeError("boom")

    monkeypatch.setattr(screener, "_load_ext_value_maps", _boom)
    out = _attach_ext(resp, repo, "600000.SH", "cfg1.score")
    assert out["stock_info"]["ext"] == {"cfg1__score": None}


# ------------------------------------------- _attach_ext 与真实 _load_ext_value_maps 集成

def _setup_ext_snapshot(tmp_path):
    store = ExtConfigStore(tmp_path)
    store.upsert(ExtConfig(
        id="benchx", label="基准扩展", mode="snapshot",
        fields=[ExtField(name="score", dtype="float")],
    ))
    cfg_dir = tmp_path / "ext_data" / "benchx"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": [f"{600000 + i}.SH" for i in range(50)],
        "score": [float(i) for i in range(50)],
    }).write_parquet(cfg_dir / "part.parquet")
    return store


def test_attach_ext_real_loader_returns_parquet_values(tmp_path):
    _setup_ext_snapshot(tmp_path)
    repo = _FakeRepo(_instruments_df(), tmp_path)
    resp = {"symbol": "600005.SH", "stock_info": {"name": "x"}, "rows": []}
    out = _attach_ext(resp, repo, "600005.SH", "benchx.score")
    assert out["stock_info"]["ext"] == {"benchx__score": 5.0}
    assert isinstance(out["stock_info"]["ext"]["benchx__score"], float)


def test_attach_ext_real_loader_unknown_symbol_is_none(tmp_path):
    _setup_ext_snapshot(tmp_path)
    repo = _FakeRepo(_instruments_df(), tmp_path)
    resp = {"symbol": "999999.SH", "stock_info": {}, "rows": []}
    out = _attach_ext(resp, repo, "999999.SH", "benchx.score")
    assert out["stock_info"]["ext"] == {"benchx__score": None}


# ---------------------------------------------------------------- _maybe_inject_live_candle
# A5: a symbol outside the polling pool snapshot must still get today's live candle via
# QuoteService.fetch_live_bar, instead of being silently skipped because it is absent
# from the full-market poll's enriched-today DataFrame.


class _FakeQuoteServiceForInject:
    def __init__(self, enriched_today: pl.DataFrame, enriched_date, live_bar: dict | None) -> None:
        self._enriched_today = enriched_today
        self._enriched_date = enriched_date
        self._live_bar = live_bar
        self.fetch_live_bar_calls: list[str] = []

    def get_enriched_today(self):
        return self._enriched_today, self._enriched_date

    def fetch_live_bar(self, symbol):
        self.fetch_live_bar_calls.append(symbol)
        return self._live_bar


def _fake_request(quote_service=None, repo=None):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(quote_service=quote_service, repo=repo)))


def test_maybe_inject_live_candle_falls_back_to_live_bar_for_symbol_outside_pool():
    today = date.today()
    yesterday = today - timedelta(days=1)
    snapshot = pl.DataFrame({"symbol": ["2330.TWSE"], "close": [500.0]})
    live_bar = {
        "symbol": "6196.TWSE", "date": today, "open": 100.0, "high": 105.0,
        "low": 99.0, "close": 104.0, "volume": 1000.0, "amount": 104000.0,
        "change_pct": 0.0297,
    }
    qs = _FakeQuoteServiceForInject(snapshot, today, live_bar)
    request = _fake_request(quote_service=qs)
    rows = [{"date": str(yesterday), "symbol": "6196.TWSE", "open": 90.0, "high": 91.0, "low": 89.0, "close": 90.5}]

    out = _maybe_inject_live_candle(request, "6196.TWSE", rows, "stock")

    assert len(out) == 2
    injected = out[-1]
    assert injected["date"] == str(today)
    assert injected["close"] == 104.0
    assert injected["is_live"] is True
    assert injected["change_pct"] == 0.0297


def test_maybe_inject_live_candle_skips_stale_snapshot_without_calling_fetch_live_bar():
    """A snapshot not dated today (realtime off, or a non-trading day) must not trigger a fetch."""
    today = date.today()
    yesterday = today - timedelta(days=1)
    snapshot = pl.DataFrame({"symbol": ["6196.TWSE"], "close": [500.0]})
    live_bar = {"symbol": "6196.TWSE", "date": today, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                "volume": 1.0, "amount": 1.0, "change_pct": 0.0}
    qs = _FakeQuoteServiceForInject(snapshot, yesterday, live_bar)
    request = _fake_request(quote_service=qs)
    rows = [{"date": str(yesterday), "symbol": "6196.TWSE", "open": 90.0, "high": 91.0, "low": 89.0, "close": 90.5}]

    out = _maybe_inject_live_candle(request, "6196.TWSE", rows, "stock")

    assert out == rows
    assert qs.fetch_live_bar_calls == []


def test_maybe_inject_live_candle_prefers_snapshot_when_symbol_already_polled():
    today = date.today()
    snapshot = pl.DataFrame({
        "symbol": ["6196.TWSE"], "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5],
        "volume": [100.0], "amount": [1000.0], "change_pct": [0.01],
    })
    live_bar = {"symbol": "6196.TWSE", "date": today, "open": 1.0, "high": 1.0, "low": 1.0, "close": 999.0,
                "volume": 1.0, "amount": 1.0, "change_pct": 0.0}
    qs = _FakeQuoteServiceForInject(snapshot, today, live_bar)
    request = _fake_request(quote_service=qs)
    rows: list[dict] = []

    out = _maybe_inject_live_candle(request, "6196.TWSE", rows, "stock")

    assert len(out) == 1
    assert out[0]["close"] == 10.5
    assert qs.fetch_live_bar_calls == []


def test_maybe_inject_live_candle_etf_path_never_touches_quote_service_fetch_live_bar():
    today = date.today()
    snapshot = pl.DataFrame({
        "symbol": ["0050.TWSE"], "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5],
        "volume": [100.0], "amount": [1000.0], "change_pct": [0.01],
    })

    class _FakeRepoForInject:
        def get_enriched_latest_asset(self, asset_type):
            assert asset_type == "etf"
            return snapshot, today

    qs = _FakeQuoteServiceForInject(pl.DataFrame(), today, {"symbol": "0050.TWSE"})
    request = _fake_request(quote_service=qs, repo=_FakeRepoForInject())
    rows: list[dict] = []

    out = _maybe_inject_live_candle(request, "0050.TWSE", rows, "etf")

    assert len(out) == 1
    assert out[0]["close"] == 10.5
    assert qs.fetch_live_bar_calls == []


def test_maybe_inject_live_candle_carries_turnover_rate():
    """Taiwan info-bar/watchlist turnover reads from the live candle's own row."""
    today = date.today()
    snapshot = pl.DataFrame({
        "symbol": ["6196.TWSE"], "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5],
        "volume": [100.0], "amount": [1000.0], "change_pct": [0.01], "turnover_rate": [1.23],
    })
    qs = _FakeQuoteServiceForInject(snapshot, today, None)
    request = _fake_request(quote_service=qs)

    out = _maybe_inject_live_candle(request, "6196.TWSE", [], "stock")

    assert len(out) == 1
    assert out[0]["turnover_rate"] == 1.23


# ── A6: a weekend or holiday start is not a gap in the enriched rows ──


def _patch_trading_days(monkeypatch, *days: date) -> None:
    class _Store:
        def has_date(self, d: date) -> bool:
            return d in days

    monkeypatch.setattr("app.taiwan.daily_store.TaiwanDailyStore", _Store)


def _enriched_rows(*days: date) -> pl.DataFrame:
    return pl.DataFrame({"date": list(days), "close": [1.0] * len(days)})


def test_needs_local_history_accepts_enriched_rows_after_a_weekend_start(monkeypatch):
    friday, sunday, monday = date(2026, 5, 22), date(2026, 5, 24), date(2026, 5, 25)
    _patch_trading_days(monkeypatch, friday, monday)

    assert _needs_local_history(_enriched_rows(monday, date(2026, 5, 26)), sunday) is False


def test_needs_local_history_accepts_enriched_rows_after_a_holiday_start(monkeypatch):
    start, reopen = date(2026, 2, 16), date(2026, 2, 23)
    _patch_trading_days(monkeypatch, date(2026, 2, 13), reopen)

    assert _needs_local_history(_enriched_rows(reopen), start) is False


def test_needs_local_history_falls_back_when_enriched_misses_trading_days(monkeypatch):
    start, today = date(2026, 5, 25), date(2026, 9, 23)
    _patch_trading_days(monkeypatch, start, date(2026, 5, 26), today)

    assert _needs_local_history(_enriched_rows(today), start) is True


def test_needs_local_history_accepts_enriched_rows_starting_on_the_start_date(monkeypatch):
    start = date(2026, 5, 25)
    _patch_trading_days(monkeypatch, start)

    assert _needs_local_history(_enriched_rows(start), start) is False


def test_needs_local_history_falls_back_without_enriched_rows():
    assert _needs_local_history(pl.DataFrame(), date(2026, 5, 25)) is True


def test_needs_local_history_falls_back_without_probing_a_gap_longer_than_any_closure(monkeypatch):
    class _Store:
        def has_date(self, d: date) -> bool:
            raise AssertionError("a gap this long must not be probed day by day")

    monkeypatch.setattr("app.taiwan.daily_store.TaiwanDailyStore", _Store)
    earliest = date(2026, 5, 25)

    assert _needs_local_history(_enriched_rows(earliest), earliest - timedelta(days=60)) is True
