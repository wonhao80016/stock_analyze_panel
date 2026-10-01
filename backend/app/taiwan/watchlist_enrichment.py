"""Taiwan Watchlist Enrichment (Phase 8B-5.0.4, extended in Phase 8B-5.0.5).

Thin adapter that fills in `/api/watchlist/enriched` rows for canonical Taiwan
symbols (``2330.TWSE`` / ``6488.TPEX``) by reusing the *existing* Taiwan
realtime/daily stack — the same one already powering TaiwanStockDetail,
TaiwanScreener and Monitor. This module does not fetch anything from the
network itself; it only orchestrates already-built services:

  - ``app.taiwan.universe.get_security_master()``   — name / instrument_type
  - ``app.taiwan.realtime.get_realtime_service()``  — realtime/snapshot quote
    with its own 4-tier fallback chain (cache -> MIS -> Yahoo -> official
    close snapshot), extended here with a 5th tier: latest Taiwan daily close
    (``TaiwanDailyStore``), so a closed market / provider outage still shows
    the last known price instead of turning the whole row blank.
  - ``app.taiwan.enriched_reader`` — indicators are read from the newest
    published enriched partition (one read for the whole watchlist), never
    recomputed here. Each row carries ``indicator_date``, the partition date.

signal_* (limit-up/board-related) stay ``None``.

``turnover_rate`` is filled for today's quotes only; see ``_taiwan_turnover_rate``.
"""
from __future__ import annotations

import logging
import math
from datetime import timedelta
from typing import Any

import polars as pl

logger = logging.getLogger(__name__)

# Row keys share their names with api/watchlist.py::_WATCHLIST_COLS; the indicator
# fields are added from the enriched partition below.
_TAIWAN_ROW_FIELDS = (
    "symbol", "name", "asset_type",
    "close", "open", "high", "low", "prev_close",
    "change_amount", "change_pct", "amount", "turnover_rate",
)

_TAIWAN_INDICATOR_FIELDS = (
    "amplitude", "annual_vol_20d", "vol_ratio_5d",
    "ma5", "ma10", "ma20", "ma60", "vol_ma5", "vol_ma10",
    "high_60d", "low_60d",
    "rsi_6", "rsi_14", "rsi_24",
    "macd_dif", "macd_dea", "macd_hist",
    "kdj_k", "kdj_d", "kdj_j",
    "boll_upper", "boll_lower", "atr_14",
    "momentum_5d", "momentum_10d", "momentum_20d", "momentum_30d", "momentum_60d",
)


def _sanitize_float(value: Any) -> float | None:
    """NaN/Inf -> None; 其余原样返回 (与 api/watchlist.py 对 A 股列的处理口径一致)。"""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _to_fraction_pct(value: Any) -> float | None:
    """把 Taiwan 的「已乘 100」涨跌幅换成 watchlist 契约的小数涨跌幅。

    TaiwanRealtimeQuote.change_pct 在 mis_provider / yahoo_provider /
    enrichment.quote 三处 provider 里统一是 round(change/prev_close*100, N)
    (即 0.21 代表 0.21%); 但 A 股 enriched 管线的 change_pct 列是纯小数
    (pipeline.py: change_pct = close/prev_close - 1, 0.05 代表 5%), 前端
    fmtPct() 也是按小数 * 100 来渲染 (frontend/src/lib/format.ts)。两边单位
    不同, 直接透传会让 Watchlist 显示成 100 倍的涨跌幅 —— 这里做单位换算,
    不改 Taiwan 侧任何既有 provider 的既定 contract。
    """
    value = _sanitize_float(value)
    if value is None:
        return None
    return value / 100.0


def _taiwan_live_date(q: Any) -> str | None:
    """A non-realtime quote's trade_date may be a stale fallback session, so only
    a realtime quote (source_meta.is_realtime) may date today's mini candle."""
    if q is None or q.source_meta is None or not q.source_meta.is_realtime:
        return None
    if q.trade_date is None:
        return None
    return q.trade_date.isoformat()


def _taiwan_turnover_rate(
    q: Any, asset_type: str, issued_shares: Any, today: Any,
) -> float | None:
    """Issued-share turnover for today's quote only.

    Taiwan has no historical share-count source (docs/taiwan-share-capital-phase-6h.md),
    so a fallback quote from a previous session (q.trade_date != today) must stay null
    rather than being priced with today's issued-share count.
    """
    if q is None or asset_type != "stock":
        return None
    if q.trade_date is None or q.trade_date != today:
        return None
    volume = q.volume
    if volume is None or issued_shares is None or issued_shares <= 0:
        return None
    return _sanitize_float(volume * 100.0 / issued_shares)


def _taiwan_daily_fallback_map(symbols: list[str], daily_store: Any, corporate_actions: Any = None) -> dict[str, dict]:
    """一次性批量读取最近窗口的台股日K, 算出「最新收盘 + 前收盘 + 涨跌」映射。

    只读一次 (最近 14 个日历天的分区, 足够跨过连假拿到 >= 2 个交易日), 不对每个
    symbol 各自打开 parquet —— 避免 N+1。返回的 dict 直接喂给
    TaiwanRealtimeService.get_quotes(..., daily_kline_fallback_fn=...), 该函数
    对每个 symbol 只是一次 dict.get, 没有额外 IO。
    """
    latest = daily_store.latest_date()
    if latest is None:
        return {}
    start = latest - timedelta(days=14)
    df = daily_store.read_range(symbols, start, latest)
    if df.is_empty():
        return {}

    from app.taiwan.corporate_actions import (
        REF_PREV_CLOSE,
        TaiwanCorporateActionStore,
        attach_reference_prices,
        read_events_or_empty,
    )

    events = read_events_or_empty(corporate_actions or TaiwanCorporateActionStore(), start, latest, symbols)
    df = attach_reference_prices(df, events).with_columns(
        pl.coalesce(pl.col(REF_PREV_CLOSE), pl.col("close").shift(1).over("symbol")).alias("prev_close")
    ).drop(REF_PREV_CLOSE)
    latest_rows = (
        df.with_columns(pl.col("date").max().over("symbol").alias("_max_date"))
        .filter(pl.col("date") == pl.col("_max_date"))
        .drop("_max_date")
    )

    result: dict[str, dict] = {}
    for row in latest_rows.to_dicts():
        close = row.get("close")
        prev_close = row.get("prev_close")
        change = None
        change_pct = None
        if close is not None and prev_close not in (None, 0):
            change = close - prev_close
            change_pct = change / prev_close * 100
        result[row["symbol"]] = {**row, "change": change, "change_pct": change_pct}
    return result


def _taiwan_indicator_map(symbols: list[str]) -> tuple[dict[str, dict], str | None]:
    """Indicators of the newest partition that carries them, plus that partition's date."""
    from app.taiwan.enriched_reader import read_latest_enriched

    latest = read_latest_enriched(list(_TAIWAN_INDICATOR_FIELDS), symbols)
    if latest is None:
        return {}, None
    as_of, frame = latest
    return {row["symbol"]: row for row in frame.to_dicts()}, as_of.isoformat()


def enrich_taiwan_watchlist_rows(
    symbols: list[str],
    *,
    security_master: Any | None = None,
    realtime_service: Any | None = None,
    daily_store: Any | None = None,
    share_capital: Any | None = None,
    corporate_actions: Any | None = None,
) -> list[dict]:
    """返回 canonical Taiwan symbol 的自选股 enriched 行, 字段对齐 legacy A 股口径。

    单一批次调用 realtime_service.get_quotes(symbols, ...) —— 不逐 symbol 各打
    一次 provider, 由既有 TaiwanRealtimeService 自己的批次/缓存机制处理。
    某个 symbol 若查无任何来源(未知/下市/尚无本地数据), 该行仍会返回, 除
    symbol/name/asset_type 外全部字段为 None —— 与既有 A 股 LEFT JOIN 语义
    (find nothing -> null, 不丢行) 一致, 不让整个端点因单一标的失败而 500。
    """
    if not symbols:
        return []

    from app.taiwan.daily_store import TaiwanDailyStore
    from app.taiwan.realtime import get_realtime_service
    from app.taiwan.realtime.calendar import taipei_now
    from app.taiwan.share_capital_store import TaiwanShareCapitalStore
    from app.taiwan.universe import get_security_master

    sec_master = security_master or get_security_master()
    rt_service = realtime_service or get_realtime_service()
    store = daily_store or TaiwanDailyStore()
    capital_store = share_capital or TaiwanShareCapitalStore()

    today = taipei_now().date()
    try:
        issued_shares_map = {
            row["symbol"]: row["issued_shares"] for row in capital_store.load().to_dicts()
        }
    except Exception as e:
        logger.warning("taiwan watchlist share capital read failed: %s", e)
        issued_shares_map = {}

    fallback_map = _taiwan_daily_fallback_map(symbols, store, corporate_actions)

    def _daily_fallback(canonical: str) -> dict | None:
        return fallback_map.get(canonical)

    try:
        quote_map = rt_service.get_quotes(symbols, daily_kline_fallback_fn=_daily_fallback)
    except Exception as e:  # noqa: BLE001
        # 任何 provider 层异常都不该让整个 /enriched 端点 500 —— 降级为全部标的
        # 缺行情, 名称/资产类型仍照常返回 (见下方 inst 查询, 与行情来源独立)。
        logger.warning("taiwan watchlist realtime quote fetch failed: %s", e)
        quote_map = {}

    try:
        indicator_map, indicator_date = _taiwan_indicator_map(symbols)
    except Exception as e:  # noqa: BLE001
        # An indicator read failure must not blank the price fields.
        logger.warning("taiwan watchlist indicator read failed: %s", e)
        indicator_map, indicator_date = {}, None

    rows: list[dict] = []
    for symbol in symbols:
        inst = None
        try:
            inst = sec_master.get_instrument(symbol)
        except Exception as e:  # noqa: BLE001
            logger.debug("taiwan security master lookup failed for %s: %s", symbol, e)

        q = quote_map.get(symbol)
        ind = indicator_map.get(symbol) or {}
        asset_type = (
            inst.instrument_type if inst and inst.instrument_type in ("stock", "etf") else "stock"
        )
        rows.append({
            "symbol": symbol,
            "name": inst.name if inst else None,
            "asset_type": asset_type,
            "close": _sanitize_float(q.last_price) if q else None,
            "open": _sanitize_float(q.open) if q else None,
            "high": _sanitize_float(q.high) if q else None,
            "low": _sanitize_float(q.low) if q else None,
            "prev_close": _sanitize_float(q.prev_close) if q else None,
            "change_amount": _sanitize_float(q.change) if q else None,
            "change_pct": _to_fraction_pct(q.change_pct) if q else None,
            "amount": _sanitize_float(q.amount) if q else None,
            # Data Freshness & Source Labels batch (Post-8C follow-up): 透传既有
            # TaiwanRealtimeQuote 的 provenance/staleness metadata, 不新造 schema —
            # source_meta 直接重用 SourceMeta.to_dict()（Monitor /api/intraday/quotes
            # 与 StockDetail 已经在用同一份数据, 这里之前漏掉了透传）。quote_time 额外
            # 摊平出来方便前端直接读, 不需要为了一个字段解一层 source_meta。
            # quote 查无(q is None)时两者都是 None, 不伪造新鲜度状态。
            "quote_time": q.quote_time.isoformat() if q and q.quote_time else None,
            "source_meta": q.source_meta.to_dict() if q and q.source_meta else None,
            "live_date": _taiwan_live_date(q),
            "indicator_date": indicator_date if ind else None,
            **{f: _sanitize_float(ind.get(f)) for f in _TAIWAN_INDICATOR_FIELDS},
            "turnover_rate": _taiwan_turnover_rate(q, asset_type, issued_shares_map.get(symbol), today),
        })
    return rows
