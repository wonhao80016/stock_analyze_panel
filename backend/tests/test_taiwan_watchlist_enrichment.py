"""Taiwan Watchlist enrichment (Phase 8B-5.0.4) — 纯 unit test, 无即时网络。

覆盖 app.taiwan.watchlist_enrichment.enrich_taiwan_watchlist_rows 本身;
market dispatch (legacy A 股 vs Taiwan 分流) 的整合测试见
test_watchlist_market_dispatch.py。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.taiwan.watchlist_enrichment import enrich_taiwan_watchlist_rows


@dataclass
class _FakeInstrument:
    name: str
    instrument_type: str


class _FakeSecurityMaster:
    def __init__(self, instruments: dict[str, _FakeInstrument]) -> None:
        self._instruments = instruments

    def get_instrument(self, symbol: str):
        return self._instruments.get(symbol)


@dataclass
class _FakeSourceMeta:
    """镜像 app.taiwan.enrichment.models.SourceMeta 的最小 fake, 只为测试
    watchlist_enrichment 对 source_meta 的透传, 不依赖真实 provider 模型。"""
    source: str = "twse:mis"
    is_stale: bool = False
    freshness_class: str = "best_effort_near_realtime"
    fallback_reason: str | None = None
    trade_date: date | None = None
    is_realtime: bool = False

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "is_stale": self.is_stale,
            "freshness_class": self.freshness_class,
            "fallback_reason": self.fallback_reason,
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
        }


@dataclass
class _FakeQuote:
    last_price: float | None
    prev_close: float | None
    open: float | None
    high: float | None
    low: float | None
    change: float | None
    change_pct: float | None
    volume: int | None
    amount: float | None
    quote_time: object | None = None
    source_meta: _FakeSourceMeta | None = None
    trade_date: date | None = None


class _FakeRealtimeService:
    """模拟 TaiwanRealtimeService.get_quotes: 有 quote 直接给, 没有则尝试
    daily_kline_fallback_fn(与真实 service 的 tier-5 daily fallback 语义一致)。
    """

    def __init__(self, quotes: dict[str, _FakeQuote] | None = None, raise_error: bool = False) -> None:
        self._quotes = quotes or {}
        self._raise_error = raise_error

    def get_quotes(self, symbols, daily_kline_fallback_fn=None):
        if self._raise_error:
            raise RuntimeError("simulated provider outage")
        result: dict[str, _FakeQuote] = {}
        for s in symbols:
            if s in self._quotes:
                result[s] = self._quotes[s]
            elif daily_kline_fallback_fn is not None:
                d = daily_kline_fallback_fn(s)
                if d:
                    result[s] = _FakeQuote(
                        last_price=d.get("close"),
                        prev_close=d.get("prev_close"),
                        open=d.get("open"),
                        high=d.get("high"),
                        low=d.get("low"),
                        change=d.get("change"),
                        change_pct=d.get("change_pct"),
                        volume=d.get("volume"),
                        amount=d.get("amount"),
                    )
        return result


class _FakeShareCapitalStore:
    """Mirrors TaiwanShareCapitalStore.load(): symbol -> issued_shares."""

    def __init__(self, issued_shares: dict[str, int] | None = None) -> None:
        self._issued_shares = issued_shares or {}

    def load(self) -> pl.DataFrame:
        if not self._issued_shares:
            return pl.DataFrame(schema={"symbol": pl.Utf8, "issued_shares": pl.Int64})
        return pl.DataFrame({
            "symbol": list(self._issued_shares.keys()),
            "issued_shares": list(self._issued_shares.values()),
        })


class _FakeDailyStore:
    """模拟 TaiwanDailyStore: 固定 fixture, 不落盘不读盘。"""

    def __init__(self, df: pl.DataFrame | None = None, latest: date | None = None) -> None:
        self._df = df if df is not None else pl.DataFrame(schema={
            "symbol": pl.Utf8, "date": pl.Date, "open": pl.Float64, "high": pl.Float64,
            "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64, "amount": pl.Float64,
        })
        self._latest = latest

    def latest_date(self):
        return self._latest

    def read_range(self, symbols, start, end):
        df = self._df
        if symbols is not None:
            df = df.filter(pl.col("symbol").is_in(symbols))
        return df

    def available_dates(self):
        if self._df.is_empty():
            return []
        return sorted(self._df["date"].unique().to_list())


def test_twse_symbol_with_realtime_quote():
    """2330.TWSE 有 realtime quote 时, 各字段正确落到 watchlist 行契约上。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            last_price=1100.0, prev_close=1080.0, open=1085.0, high=1105.0, low=1082.0,
            change=20.0, change_pct=1.85, volume=25000000, amount=27500000000.0,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "2330.TWSE"
    assert row["name"] == "台積電"
    assert row["asset_type"] == "stock"
    assert row["close"] == 1100.0
    assert row["prev_close"] == 1080.0
    assert row["open"] == 1085.0
    assert row["high"] == 1105.0
    assert row["low"] == 1082.0
    assert row["change_amount"] == 20.0
    # TaiwanRealtimeQuote.change_pct 约定是「已乘 100」(1.85 代表 1.85%), watchlist
    # 契约(与 fmtPct 对齐)要小数(0.0185) —— 见 watchlist_enrichment._to_fraction_pct。
    assert row["change_pct"] == pytest.approx(0.0185)
    assert row["amount"] == 27500000000.0


def test_tpex_symbol_with_realtime_quote():
    """6488.TPEX(上柜)同样正常 enrichment, 不是只有 TWSE work。"""
    sec_master = _FakeSecurityMaster({
        "6488.TPEX": _FakeInstrument(name="環球晶", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService({
        "6488.TPEX": _FakeQuote(
            last_price=680.0, prev_close=675.0, open=676.0, high=685.0, low=674.0,
            change=5.0, change_pct=0.74, volume=3000000, amount=2040000000.0,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["6488.TPEX"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "6488.TPEX"
    assert row["name"] == "環球晶"
    assert row["close"] == 680.0
    assert row["change_pct"] == pytest.approx(0.0074)


def test_taiwan_etf_goes_through_same_flow():
    """Taiwan ETF(如 0050.TWSE)必须走同一条 enrichment flow, 不是只对 stock 生效。"""
    sec_master = _FakeSecurityMaster({
        "0050.TWSE": _FakeInstrument(name="元大台灣50", instrument_type="etf"),
    })
    rt_service = _FakeRealtimeService({
        "0050.TWSE": _FakeQuote(
            last_price=185.5, prev_close=184.0, open=184.2, high=186.0, low=184.0,
            change=1.5, change_pct=0.82, volume=8000000, amount=1480000000.0,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["0050.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["asset_type"] == "etf"
    assert row["close"] == 185.5
    assert row["name"] == "元大台灣50"


def test_closed_market_falls_back_to_latest_daily_close():
    """无 realtime quote 但本地有日K 时, 应 fallback 到最新收盘价并算出涨跌。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService(quotes={})  # 全部 provider 都无法取得 -> 走 daily fallback
    daily_df = pl.DataFrame({
        "symbol": ["2330.TWSE", "2330.TWSE"],
        "date": [date(2026, 8, 28), date(2026, 8, 31)],
        "open": [1070.0, 1085.0],
        "high": [1082.0, 1105.0],
        "low": [1065.0, 1082.0],
        "close": [1080.0, 1100.0],
        "volume": [20000000.0, 25000000.0],
        "amount": [21600000000.0, 27500000000.0],
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(df=daily_df, latest=date(2026, 8, 31)),
    )
    assert len(rows) == 1
    row = rows[0]
    # 最新一笔 (8/31) 收盘 1100, 前一笔 (8/28) 收盘 1080 作为 prev_close
    assert row["close"] == 1100.0
    assert row["prev_close"] == 1080.0
    assert row["change_amount"] == pytest.approx(20.0)
    # _taiwan_daily_fallback_map 内部按 Taiwan 既有 provider 惯例算成「已乘 100」,
    # 最终经 _to_fraction_pct 换回小数, 两次换算抵消后应等于最朴素的小数涨跌幅。
    assert row["change_pct"] == pytest.approx(20.0 / 1080.0)
    assert row["open"] == 1085.0


def test_missing_data_symbol_still_returns_row_with_nulls():
    """完全没有 current/daily 数据的台股 symbol: 仍返回一行, 市场字段为 null, 不 500。"""
    sec_master = _FakeSecurityMaster({})  # 连 security master 都查无此 symbol
    rt_service = _FakeRealtimeService(quotes={})
    rows = enrich_taiwan_watchlist_rows(
        ["9999.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "9999.TWSE"
    assert row["name"] is None
    assert row["asset_type"] == "stock"  # 默认值, 不报错
    assert row["close"] is None
    assert row["change_pct"] is None


def test_empty_symbol_list_returns_empty():
    rows = enrich_taiwan_watchlist_rows([])
    assert rows == []


def test_batch_call_avoids_n_plus_one_provider_calls():
    """多个 symbol 一次性传给 realtime_service.get_quotes, 不逐个调用 (N+1 防护)。"""
    calls: list[list[str]] = []

    class _CountingRealtimeService(_FakeRealtimeService):
        def get_quotes(self, symbols, daily_kline_fallback_fn=None):
            calls.append(list(symbols))
            return super().get_quotes(symbols, daily_kline_fallback_fn)

    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
        "6488.TPEX": _FakeInstrument(name="環球晶", instrument_type="stock"),
        "0050.TWSE": _FakeInstrument(name="元大台灣50", instrument_type="etf"),
    })
    rt_service = _CountingRealtimeService({
        "2330.TWSE": _FakeQuote(1100.0, 1080.0, 1085.0, 1105.0, 1082.0, 20.0, 1.85, 1, 1.0),
        "6488.TPEX": _FakeQuote(680.0, 675.0, 676.0, 685.0, 674.0, 5.0, 0.74, 1, 1.0),
        "0050.TWSE": _FakeQuote(185.5, 184.0, 184.2, 186.0, 184.0, 1.5, 0.82, 1, 1.0),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE", "6488.TPEX", "0050.TWSE"],
        security_master=sec_master, realtime_service=rt_service, daily_store=_FakeDailyStore(),
    )
    assert len(rows) == 3
    assert len(calls) == 1, "应只调用一次 get_quotes (批次), 不逐 symbol 各打一次"
    assert sorted(calls[0]) == ["0050.TWSE", "2330.TWSE", "6488.TPEX"]


def test_realtime_provider_exception_does_not_raise():
    """provider 层整体抛异常时, enrich_taiwan_watchlist_rows 本身不应向上抛 —— 由
    调用方(api/watchlist.py)的 try/except 兜底; 这里只验证不是"半途崩溃丢数据"。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService(raise_error=True)
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    # 内部已 catch 住 provider 异常, 仍返回一行(名称正常, 行情为 null)
    assert len(rows) == 1
    assert rows[0]["name"] == "台積電"
    assert rows[0]["close"] is None


# ===== Indicators are read from the published enriched partition =====

_INDICATOR_FIELDS = (
    "amplitude", "annual_vol_20d", "vol_ratio_5d",
    "ma5", "ma10", "ma20", "ma60", "vol_ma5", "vol_ma10",
    "high_60d", "low_60d",
    "rsi_6", "rsi_14", "rsi_24",
    "macd_dif", "macd_dea", "macd_hist",
    "kdj_k", "kdj_d", "kdj_j",
    "boll_upper", "boll_lower", "atr_14",
    "momentum_5d", "momentum_10d", "momentum_20d", "momentum_30d", "momentum_60d",
)


def _enriched_row(symbol: str, base: float) -> dict:
    return {"symbol": symbol, **{f: base + i * 0.137 for i, f in enumerate(_INDICATOR_FIELDS)}}


def _publish(tmp_path, monkeypatch, partitions: dict[date, list[dict]]) -> None:
    from app.config import settings
    from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    for d, rows in partitions.items():
        TaiwanEnrichedRefreshService._write_partition(
            tmp_path / "kline_daily_enriched" / f"date={d.isoformat()}", pl.DataFrame(rows)
        )


def _enrich_one(symbol: str, name: str, instrument_type: str = "stock") -> dict:
    sec_master = _FakeSecurityMaster({symbol: _FakeInstrument(name=name, instrument_type=instrument_type)})
    rt_service = _FakeRealtimeService({
        symbol: _FakeQuote(1100.0, 1080.0, 1085.0, 1105.0, 1082.0, 20.0, 1.85, 1, 1.0),
    })
    return enrich_taiwan_watchlist_rows(
        [symbol], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )[0]


def test_twse_stock_indicators_equal_the_published_partition(tmp_path, monkeypatch):
    published = _enriched_row("2330.TWSE", 10.0)
    _publish(tmp_path, monkeypatch, {date(2026, 9, 30): [published]})
    row = _enrich_one("2330.TWSE", "TSMC")
    for field in _INDICATOR_FIELDS:
        assert row[field] == published[field], field
    assert row["indicator_date"] == "2026-09-30"


def test_tpex_stock_indicators_equal_the_published_partition(tmp_path, monkeypatch):
    published = _enriched_row("6488.TPEX", 20.0)
    _publish(tmp_path, monkeypatch, {date(2026, 9, 30): [published]})
    row = _enrich_one("6488.TPEX", "GlobalWafers")
    assert row["kdj_k"] == published["kdj_k"]
    assert row["rsi_24"] == published["rsi_24"]


def test_etf_indicators_equal_the_published_partition(tmp_path, monkeypatch):
    published = _enriched_row("0050.TWSE", 30.0)
    _publish(tmp_path, monkeypatch, {date(2026, 9, 30): [published]})
    row = _enrich_one("0050.TWSE", "Yuanta 50", "etf")
    assert row["asset_type"] == "etf"
    assert row["vol_ratio_5d"] == published["vol_ratio_5d"]
    assert row["amplitude"] == published["amplitude"]


def test_newest_published_partition_wins_and_dates_the_indicators(tmp_path, monkeypatch):
    older = _enriched_row("2330.TWSE", 1.0)
    newer = _enriched_row("2330.TWSE", 50.0)
    _publish(tmp_path, monkeypatch, {date(2026, 9, 29): [older], date(2026, 9, 30): [newer]})
    row = _enrich_one("2330.TWSE", "TSMC")
    assert row["rsi_14"] == newer["rsi_14"]
    assert row["indicator_date"] == "2026-09-30"


def test_newer_basic_columns_partition_is_skipped_for_the_last_real_publication(tmp_path, monkeypatch):
    real = _enriched_row("2330.TWSE", 10.0)
    _publish(tmp_path, monkeypatch, {
        date(2026, 9, 29): [real],
        date(2026, 9, 30): [{"symbol": "2330.TWSE", "close": 1100.0, "volume": 5.0}],
    })
    row = _enrich_one("2330.TWSE", "TSMC")
    assert row["indicator_date"] == "2026-09-29"
    assert row["rsi_14"] == real["rsi_14"]


def test_only_basic_columns_partition_leaves_indicators_and_date_null(tmp_path, monkeypatch):
    _publish(tmp_path, monkeypatch, {date(2026, 9, 30): [{"symbol": "2330.TWSE", "close": 1100.0}]})
    row = _enrich_one("2330.TWSE", "TSMC")
    assert row["indicator_date"] is None
    assert row["rsi_14"] is None


def test_all_null_indicator_partition_is_not_published(tmp_path, monkeypatch):
    empty = {"symbol": "2330.TWSE", **{f: None for f in _INDICATOR_FIELDS}}
    _publish(tmp_path, monkeypatch, {date(2026, 9, 30): [empty]})
    row = _enrich_one("2330.TWSE", "TSMC")
    assert row["indicator_date"] is None


def test_no_published_partition_leaves_indicators_null(tmp_path, monkeypatch):
    _publish(tmp_path, monkeypatch, {})
    row = _enrich_one("2330.TWSE", "TSMC")
    assert row["close"] == 1100.0
    assert row["change_pct"] == pytest.approx(0.0185)
    assert row["indicator_date"] is None
    for field in _INDICATOR_FIELDS:
        assert row[field] is None, field


def test_symbol_missing_from_the_partition_has_null_indicators_and_no_date(tmp_path, monkeypatch):
    _publish(tmp_path, monkeypatch, {date(2026, 9, 30): [_enriched_row("2317.TWSE", 5.0)]})
    row = _enrich_one("2330.TWSE", "TSMC")
    assert row["indicator_date"] is None
    assert row["rsi_14"] is None


def test_indicators_are_read_from_one_partition_for_the_whole_batch(tmp_path, monkeypatch):
    _publish(tmp_path, monkeypatch, {
        date(2026, 9, 30): [_enriched_row("2330.TWSE", 10.0), _enriched_row("6488.TPEX", 20.0)],
    })
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="TSMC", instrument_type="stock"),
        "6488.TPEX": _FakeInstrument(name="GlobalWafers", instrument_type="stock"),
    })
    read_range_calls: list[list[str]] = []

    class _CountingDailyStore(_FakeDailyStore):
        def read_range(self, symbols, start, end):
            read_range_calls.append(list(symbols) if symbols is not None else [])
            return super().read_range(symbols, start, end)

    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE", "6488.TPEX"], security_master=sec_master,
        realtime_service=_FakeRealtimeService(), daily_store=_CountingDailyStore(latest=date(2026, 9, 30)),
    )
    assert all(r["ma20"] is not None for r in rows)
    assert len(read_range_calls) == 1


# ===== Data Freshness & Source Labels batch (Post-8C follow-up): =====
# Monitor (/api/intraday/quotes) 与 StockDetail 一直都完整透传
# TaiwanRealtimeQuote.source_meta / quote_time; enrich_taiwan_watchlist_rows
# 之前组 row dict 时把这两个字段漏掉了, 导致 Watchlist 前端完全拿不到新鲜度/
# 来源信息(见 Data Source Consistency & Freshness Audit report §D)。以下测试
# 覆盖补上的透传逻辑本身, 不重新验证 realtime_service 的 fallback chain(那部分
# 已由 test_taiwan_realtime_service.py 等既有测试覆盖)。

def test_watchlist_row_preserves_source_meta():
    """有 quote 时, row["source_meta"] 应是 q.source_meta.to_dict() 的原样结果。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    meta = _FakeSourceMeta(source="twse:mis", is_stale=False, freshness_class="best_effort_near_realtime")
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            1100.0, 1080.0, 1085.0, 1105.0, 1082.0, 20.0, 1.85, 1, 1.0,
            source_meta=meta,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    row = rows[0]
    assert row["source_meta"] == meta.to_dict()
    assert row["source_meta"]["source"] == "twse:mis"
    assert row["source_meta"]["freshness_class"] == "best_effort_near_realtime"


def test_watchlist_row_is_stale_survives_serialization():
    """is_stale=True 的 quote, row["source_meta"]["is_stale"] 必须原样是 True
    (不能因为序列化被静默丢掉或换成假的 False)。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    meta = _FakeSourceMeta(source="yahoo:chart", is_stale=True, freshness_class="delayed_15m")
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            2480.0, 2470.0, 2490.0, 2490.0, 2475.0, 10.0, 0.40, 1, 1.0,
            source_meta=meta,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    row = rows[0]
    assert row["source_meta"]["is_stale"] is True
    assert row["source_meta"]["freshness_class"] == "delayed_15m"


def test_watchlist_row_quote_time_survives_serialization():
    """quote_time 应以 ISO 字串落到 row["quote_time"], 与 q.quote_time.isoformat() 一致。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    qt = datetime(2026, 9, 9, 9, 53, 58)
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            2480.0, 2470.0, 2490.0, 2490.0, 2475.0, 10.0, 0.40, 1, 1.0,
            quote_time=qt, source_meta=_FakeSourceMeta(),
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    row = rows[0]
    assert row["quote_time"] == qt.isoformat()


def test_watchlist_row_fallback_reason_survives_serialization():
    """降级 fallback 时的 fallback_reason 应保留在 source_meta 内, 供前端 tooltip 使用。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    meta = _FakeSourceMeta(
        source="yahoo:chart", is_stale=True, freshness_class="delayed_15m",
        fallback_reason="Primary MIS unavailable or missing symbol",
    )
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            2480.0, 2470.0, 2490.0, 2490.0, 2475.0, 10.0, 0.40, 1, 1.0,
            source_meta=meta,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    row = rows[0]
    assert row["source_meta"]["fallback_reason"] == "Primary MIS unavailable or missing symbol"


def test_watchlist_row_missing_metadata_does_not_crash():
    """quote 存在但 quote_time/source_meta 都是 None (旧 fixture / mock 不完整) 时,
    row 仍正常返回, 不因缺 metadata 而抛异常; 价格字段不受影响。"""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="台積電", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            2480.0, 2470.0, 2490.0, 2490.0, 2475.0, 10.0, 0.40, 1, 1.0,
            quote_time=None, source_meta=None,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    row = rows[0]
    assert row["quote_time"] is None
    assert row["source_meta"] is None
    assert row["close"] == 2480.0  # 价格字段不受 metadata 缺失影响


def test_watchlist_row_no_quote_at_all_has_no_fake_metadata():
    """完全查无 quote 的 symbol (q is None): quote_time/source_meta 应是 None,
    不得伪造出「即時」之类假新鲜度状态。"""
    sec_master = _FakeSecurityMaster({
        "9999.TWSE": _FakeInstrument(name="測試無資料股", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService(quotes={})
    rows = enrich_taiwan_watchlist_rows(
        ["9999.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    row = rows[0]
    assert row["quote_time"] is None
    assert row["source_meta"] is None


# ===== A7: live_date lets the frontend patch today's mini daily candle
# only from a realtime quote; a fallback quote's trade_date must not qualify. =====

def test_realtime_quote_sets_live_date_from_its_own_trade_date():
    """A realtime quote (source_meta.is_realtime=True) yields live_date == its own trade_date."""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="TSMC", instrument_type="stock"),
    })
    meta = _FakeSourceMeta(source="twse:mis", is_realtime=True)
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            1100.0, 1080.0, 1085.0, 1105.0, 1082.0, 20.0, 1.85, 1, 1.0,
            source_meta=meta, trade_date=date(2026, 9, 23),
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    assert rows[0]["live_date"] == "2026-09-23"


def test_non_realtime_quote_has_no_live_date():
    """A non-realtime quote (e.g. daily-close fallback) must not date today's candle."""
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="TSMC", instrument_type="stock"),
    })
    meta = _FakeSourceMeta(source="twse:stock_day_all", is_realtime=False)
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            1080.0, 1060.0, 1065.0, 1085.0, 1062.0, 20.0, 1.85, 1, 1.0,
            source_meta=meta, trade_date=date(2026, 9, 22),
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    assert rows[0]["live_date"] is None


def test_no_quote_at_all_has_no_live_date():
    """A symbol with no quote (q is None) must not fake a live_date."""
    sec_master = _FakeSecurityMaster({})
    rt_service = _FakeRealtimeService(quotes={})
    rows = enrich_taiwan_watchlist_rows(
        ["9999.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
    )
    assert rows[0]["live_date"] is None


# ===== Watchlist turnover_rate: issued shares * volume, today's realtime quote only
# (see docs/taiwan-share-capital-phase-6h.md — no historical Taiwan share count exists,
# so a fallback quote from a previous session must not be priced). =====


def test_taiwan_turnover_rate_computed_for_todays_realtime_quote():
    from app.taiwan.realtime.calendar import taipei_now
    today = taipei_now().date()
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="TSMC", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            1100.0, 1080.0, 1085.0, 1105.0, 1082.0, 20.0, 1.85,
            volume=1_000_000, amount=1.0, trade_date=today,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
        share_capital=_FakeShareCapitalStore({"2330.TWSE": 100_000_000}),
    )
    assert rows[0]["turnover_rate"] == pytest.approx(1.0)


def test_taiwan_turnover_rate_none_for_previous_session_fallback_quote():
    from app.taiwan.realtime.calendar import taipei_now
    yesterday = taipei_now().date() - timedelta(days=1)
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="TSMC", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            1080.0, 1060.0, 1065.0, 1085.0, 1062.0, 20.0, 1.85,
            volume=1_000_000, amount=1.0, trade_date=yesterday,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
        share_capital=_FakeShareCapitalStore({"2330.TWSE": 100_000_000}),
    )
    assert rows[0]["turnover_rate"] is None


def test_taiwan_turnover_rate_none_for_etf():
    from app.taiwan.realtime.calendar import taipei_now
    today = taipei_now().date()
    sec_master = _FakeSecurityMaster({
        "0050.TWSE": _FakeInstrument(name="Yuanta Taiwan 50 ETF", instrument_type="etf"),
    })
    rt_service = _FakeRealtimeService({
        "0050.TWSE": _FakeQuote(
            185.5, 184.0, 184.2, 186.0, 184.0, 1.5, 0.82,
            volume=8_000_000, amount=1.0, trade_date=today,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["0050.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
        share_capital=_FakeShareCapitalStore({"0050.TWSE": 900_000_000}),
    )
    assert rows[0]["turnover_rate"] is None


def test_taiwan_turnover_rate_none_without_share_capital_row():
    from app.taiwan.realtime.calendar import taipei_now
    today = taipei_now().date()
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="TSMC", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            1100.0, 1080.0, 1085.0, 1105.0, 1082.0, 20.0, 1.85,
            volume=1_000_000, amount=1.0, trade_date=today,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
        share_capital=_FakeShareCapitalStore({}),
    )
    assert rows[0]["turnover_rate"] is None


def test_taiwan_turnover_rate_uses_shares_not_lots():
    """volume is already shares for Taiwan (mis_provider.py); a stray *1000 lot
    conversion would give 2000% instead of 2%."""
    from app.taiwan.realtime.calendar import taipei_now
    today = taipei_now().date()
    sec_master = _FakeSecurityMaster({
        "2330.TWSE": _FakeInstrument(name="TSMC", instrument_type="stock"),
    })
    rt_service = _FakeRealtimeService({
        "2330.TWSE": _FakeQuote(
            1100.0, 1080.0, 1085.0, 1105.0, 1082.0, 20.0, 1.85,
            volume=2_000_000, amount=1.0, trade_date=today,
        ),
    })
    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE"], security_master=sec_master, realtime_service=rt_service,
        daily_store=_FakeDailyStore(),
        share_capital=_FakeShareCapitalStore({"2330.TWSE": 100_000_000}),
    )
    assert rows[0]["turnover_rate"] == pytest.approx(2.0)
