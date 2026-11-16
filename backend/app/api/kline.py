"""K 线 / 同步 API。"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from app.indicators.pipeline import (
    PRICE_COLUMNS,
    VOLUME_COLUMNS,
    compute_enriched,
    compute_indicators,
)
from app.market_time import cn_now, cn_today
from app.db_safe import is_valid_ext_ident
from app.repository import indicator_warmup_days
from app.services import kline_sync
from app.volume_units import TAIWAN_SUFFIXES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kline", tags=["kline"])


_SEARCHABLE_ASSET_TYPES = frozenset({"stock", "etf", "index"})
# The asset-type filter runs after the master's ranking cut, so the pool must exceed
# the whole supported stock + ETF universe or a filtered search can come back short.
_MASTER_SEARCH_POOL = 5000


def _parse_search_asset_types(raw: str) -> frozenset[str]:
    requested = frozenset(part.strip().lower() for part in raw.split(",") if part.strip())
    unknown = requested - _SEARCHABLE_ASSET_TYPES
    if not requested or unknown:
        raise HTTPException(400, f"不支持的资产类型: {raw}")
    return requested


def _search_taiwan_instruments(q: str, limit: int, asset_types: frozenset[str]) -> list[dict]:
    """仅查 TaiwanSecurityMaster, 不触碰 repo.get_instruments*() 的 legacy A 股缓存。

    canonical symbol 形如 2330.TWSE / 6488.TPEX; 支持代码/canonical symbol/
    繁体中文名称查询, 排序/评分逻辑在 TaiwanSecurityMaster.search() 内完成。
    只保留 is_supported 且属于 asset_types 的 stock/etf(权证等未支援标的不该出现在搜索里)。
    """
    from app.taiwan.universe import get_security_master

    hits = get_security_master().search(q.strip(), limit=_MASTER_SEARCH_POOL)
    return [
        {
            "symbol": hit["symbol"],
            "name": hit["name"],
            "code": hit["code"],
            "asset_type": hit["instrument_type"],
            "market": "taiwan",
        }
        for hit in hits
        if hit.get("is_supported") and hit.get("instrument_type") in asset_types
    ][:limit]


def _search_benchmark_indices(q: str) -> list[dict]:
    """Benchmark indices are not in the security master; match them by symbol, code or name."""
    from app.taiwan.benchmarks import BENCHMARK_INDICES

    query = q.strip().upper()
    return [
        {
            "symbol": index.symbol,
            "name": index.name,
            "code": index.code,
            "asset_type": "index",
            "market": "taiwan",
        }
        for index in BENCHMARK_INDICES.values()
        if query in index.symbol.upper() or query in index.name.upper()
    ]


@router.get("/instruments/search")
def search_instruments(
    q: str = Query("", min_length=0, max_length=50, description="搜索关键词"),
    limit: int = Query(20, ge=1, le=50),
    asset_types: Annotated[str, Query(description="逗号分隔的资产类型: stock,etf,index")] = "stock",
):
    """模糊搜索台股标的 (代码 / 繁体中文名称), 查 TaiwanSecurityMaster; index 查基准指数。

    结果统一附带 market: "taiwan" 字段, symbol 形如 2330.TWSE / 6488.TPEX / t00.TWSE。
    """
    requested = _parse_search_asset_types(asset_types)
    if not q.strip():
        return {"results": []}
    results = _search_benchmark_indices(q) if "index" in requested else []
    listed_types = requested - {"index"}
    if listed_types and len(results) < limit:
        results += _search_taiwan_instruments(q, limit - len(results), listed_types)
    return {"results": results[:limit]}


@router.post("/instruments/names")
def instruments_names(request: Request, symbols: list[str]):
    """批量查标的名称 (股票 + ETF + 指数)。传入 symbol 列表, 返回 {symbol: name}。

    台股名称的正本是 TaiwanSecurityMaster; repo.get_name_map() 读的是 legacy
    A 股 instruments 维表, 对 .TWSE/.TPEX 一律查不到, 只作为其余 symbol 的兜底。
    """
    if not symbols:
        return {"names": {}}
    from app.taiwan.benchmarks import get_benchmark
    from app.taiwan.universe import get_security_master

    master = get_security_master()
    names: dict[str, str] = {}
    unresolved: list[str] = []
    for sym in symbols:
        benchmark = get_benchmark(sym)
        if benchmark is not None:
            names[sym] = benchmark.name
            continue
        inst = master.get_instrument(sym)
        if inst is not None and inst.name:
            names[sym] = inst.name
        else:
            unresolved.append(sym)
    if unresolved:
        names.update(request.app.state.repo.get_name_map(unresolved))
    return {"names": names}


def _get_stock_info(repo, symbol: str) -> dict:
    """从 instruments 内存缓存查标的名称 + 股本。

    该接口在个股弹窗打开时每秒被调用 (SSE invalidate 触发重拉), 走
    repo.get_instruments() 的 Polars 内存缓存按 symbol 过滤, 不再每请求
    DuckDB 扫 instruments parquet。列缺失时返回空 dict, 与旧 SQL 报错路径一致。

    Taiwan symbols are absent from this legacy cache and fall back to
    _get_taiwan_stock_info.
    """
    import polars as pl
    info: dict = {}
    try:
        df = repo.get_instruments()
        needed = ("symbol", "name", "total_shares", "float_shares")
        if not df.is_empty() and all(c in df.columns for c in needed):
            hit = df.filter(pl.col("symbol") == symbol).head(1)
            if not hit.is_empty():
                info = {
                    "name": hit["name"][0],
                    "total_shares": hit["total_shares"][0],
                    "float_shares": hit["float_shares"][0],
                }
    except Exception:  # noqa: BLE001
        info = {}
    if not info and symbol.endswith(TAIWAN_SUFFIXES):
        info = _get_taiwan_stock_info(symbol)
    return info


def _get_taiwan_stock_info(symbol: str) -> dict:
    """Name from the Taiwan security master; total_shares is issued common shares,
    display-only market cap and stocks only (docs/taiwan-share-capital-phase-6h.md).
    float_shares stays null: Taiwan has no public-float source, and none may be substituted.
    A TDR has none either: its official count is the underlying company's shares, not receipts.
    """
    name = None
    is_stock = False
    try:
        from app.taiwan.universe import get_security_master
        from app.taiwan.universe.models import MarketProfileBridge, TaiwanInstrument

        inst = get_security_master().get_instrument(symbol)
        if inst is not None:
            name = inst.name
            is_tdr = isinstance(inst, TaiwanInstrument) and MarketProfileBridge.is_tdr(inst)
            is_stock = getattr(inst, "instrument_type", None) == "stock" and not is_tdr
    except Exception:
        pass

    total_shares = None
    if is_stock:
        try:
            import polars as pl

            from app.taiwan.share_capital_store import TaiwanShareCapitalStore

            shares = TaiwanShareCapitalStore().load()
            if not shares.is_empty() and "symbol" in shares.columns:
                hit = shares.filter(pl.col("symbol") == symbol).head(1)
                if not hit.is_empty():
                    total_shares = hit["issued_shares"][0]
        except Exception:
            pass

    return {"name": name, "total_shares": total_shares, "float_shares": None}


def _get_asset_info(repo, symbol: str, asset_type: str) -> dict:
    """非股票标的 (指数) 的名称信息 — 从对应 instruments 缓存查, 无股本概念。"""
    import polars as pl
    try:
        df = repo.get_instruments_asset(asset_type)
        if df.is_empty() or "symbol" not in df.columns or "name" not in df.columns:
            return {}
        hit = df.filter(pl.col("symbol") == symbol).head(1)
        if hit.is_empty():
            return {}
        return {"name": hit["name"][0]}
    except Exception:
        return {}


def _get_price_limit_info(
    symbol: str,
    trade_date: date,
    asset_type: str,
    prev_close: float | None = None,
    official_limits: tuple[float, float] | None = None,
) -> dict | None:
    """Return the session's limit rule and limit prices on the scale of its minute bars.

    rate is None when the instrument has no limit (source "no_limit") or its rule is
    unconfirmed (source "unknown"); consumers must then draw no limit at all.
    """
    if asset_type == "index":
        return None
    return _taiwan_price_limit_info(symbol, trade_date, prev_close, official_limits)


def _taiwan_price_limit_info(
    symbol: str,
    trade_date: date,
    prev_close: float | None,
    official_limits: tuple[float, float] | None,
) -> dict:
    from app.taiwan.universe import get_security_master
    from app.taiwan.universe.models import MarketProfileBridge

    info = {"rate": None, "limit_up": None, "limit_down": None, "source": "unknown"}
    try:
        instrument = get_security_master().get_instrument(symbol)
    except Exception as exc:
        logger.warning("Security master unavailable, price limit of %s unknown: %s", symbol, exc)
        return info
    if instrument is None:
        return info
    try:
        rate = MarketProfileBridge.get_price_limit_pct(instrument, trade_date)
    except ValueError:
        return info
    if rate is None:
        return {**info, "source": "no_limit"}
    if official_limits is not None:
        limit_up, limit_down = official_limits
        return {"rate": rate, "limit_up": limit_up, "limit_down": limit_down, "source": "official"}
    if prev_close is None:
        return {**info, "rate": rate, "source": "rule"}
    limit_up, limit_down = MarketProfileBridge.calc_limits(prev_close, instrument, trade_date)
    return {"rate": rate, "limit_up": limit_up, "limit_down": limit_down, "source": "model"}


def _session_price_limit(
    repo, symbol: str, trade_date: date, asset_type: str
) -> tuple[float | None, dict | None]:
    closes, official_limits = _get_session_bases(repo, symbol, [trade_date], asset_type)
    prev_close = closes.get(trade_date)
    price_limit = _get_price_limit_info(
        symbol, trade_date, asset_type,
        prev_close=prev_close, official_limits=official_limits.get(trade_date),
    )
    return prev_close, price_limit


def _get_previous_closes(
    repo,
    symbol: str,
    trade_dates: list[date],
    asset_type: str,
) -> dict[date, float | None]:
    """Return each session's previous close on the scale of its minute bars.

    Taiwan minute bars are raw prices, so Taiwan sessions use raw_close, and a
    corporate-action session uses the exchange reference price instead. Other
    markets keep the adjusted close.
    """
    return _get_session_bases(repo, symbol, trade_dates, asset_type)[0]


def _get_session_bases(
    repo,
    symbol: str,
    trade_dates: list[date],
    asset_type: str,
) -> tuple[dict[date, float | None], dict[date, tuple[float, float]]]:
    """Each session's previous close (see _get_previous_closes) and the exchange's published
    (limit_up, limit_down) of the Taiwan sessions a corporate action opens."""
    if not trade_dates:
        return {}, {}
    taiwan = symbol.endswith(TAIWAN_SUFFIXES) and asset_type != "index"
    start = min(trade_dates) - timedelta(days=45)
    end = max(trade_dates)
    try:
        daily = repo.get_daily_asset(
            asset_type,
            symbol,
            start,
            end,
            columns=["date", "close", "raw_close"] if taiwan else ["date", "close"],
        ).sort("date")
    except Exception:
        daily = None
    if daily is None or daily.is_empty():
        return {trade_date: None for trade_date in trade_dates}, {}

    import polars as pl

    price = pl.col("close")
    if taiwan and "raw_close" in daily.columns:
        price = pl.coalesce(pl.col("raw_close"), price)
    closes: list[tuple[date, float]] = []
    for daily_date, close in daily.select(pl.col("date"), price).iter_rows():
        if close is None:
            continue
        numeric = float(close)
        if math.isfinite(numeric) and numeric > 0:
            closes.append((daily_date, numeric))

    result: dict[date, float | None] = {}
    previous_dates: dict[date, date | None] = {}
    for trade_date in trade_dates:
        previous = next(
            ((daily_date, close) for daily_date, close in reversed(closes) if daily_date < trade_date),
            None,
        )
        previous_dates[trade_date] = previous[0] if previous else None
        result[trade_date] = previous[1] if previous else None
    official_limits: dict[date, tuple[float, float]] = {}
    if taiwan:
        for trade_date, (ref_price, limits) in _taiwan_reference_prices(
            symbol, previous_dates, start, end
        ).items():
            result[trade_date] = ref_price
            if limits is not None:
                official_limits[trade_date] = limits
    return result, official_limits


def _taiwan_reference_prices(
    symbol: str, previous_dates: dict[date, date | None], start: date, end: date
) -> dict[date, tuple[float, tuple[float, float] | None]]:
    """Exchange reference price and published limits of the sessions that open after a corporate action."""
    from app.taiwan.corporate_actions import (
        TaiwanCorporateActionStore,
        read_events_or_empty,
        reference_prices,
    )

    events = read_events_or_empty(TaiwanCorporateActionStore(), start=start, end=end, symbols=[symbol])
    found: dict[date, tuple[float, tuple[float, float] | None]] = {}
    if events.is_empty():
        return found
    for trade_date, previous in previous_dates.items():
        ref = reference_prices(events, previous, trade_date)
        if ref.is_empty():
            continue
        row = ref.row(0, named=True)
        limits = (
            (float(row["limit_up"]), float(row["limit_down"]))
            if row["limit_up"] is not None and row["limit_down"] is not None
            else None
        )
        found[trade_date] = (float(row["ref_price"]), limits)
    return found


_TAIWAN_INDICATOR_WARMUP_DAYS = indicator_warmup_days()
# Longer than any Taiwan market closure, so a wider gap means missing trading days.
_MAX_MARKET_CLOSURE_DAYS = 31


_CLOSE_FALLBACK_MARGIN = timedelta(minutes=30)


def _past_close_with_margin(now: datetime) -> bool:
    """True once the close plus a settling margin has passed.

    The margin gives the close sync time to land today's daily row, which is what
    tells a holiday apart from a session that has only just ended.
    """
    from app.taiwan.realtime.calendar import REGULAR_OPEN_END

    cutoff = datetime.combine(now.date(), REGULAR_OPEN_END) + _CLOSE_FALLBACK_MARGIN
    return now.time() >= cutoff.time()


def _expected_minute_rows(trade_date: date) -> int:
    """Minute bars a complete local copy should hold for this date.

    Today is still filling in, so only the elapsed part of the session is expected;
    any earlier date should already hold the whole session.
    """
    from app.taiwan.realtime.calendar import SESSION_TOTAL_MINUTES, session_minutes_elapsed

    if trade_date != cn_today():
        return int(SESSION_TOTAL_MINUTES)
    return int(session_minutes_elapsed())


def _needs_local_history(df, start: date) -> bool:
    """Enriched rows may cover only the live candle, which cannot satisfy a range query.

    ``start`` is a calendar date and often a weekend or holiday, so the rows fall short
    only if the local store traded on a day between ``start`` and their earliest date,
    or that gap is longer than any market closure.
    """
    if df.is_empty():
        return True
    if "date" not in df.columns:
        return False
    earliest = df["date"].min()
    if earliest is None:
        return True
    try:
        if earliest <= start:
            return False
        if (earliest - start).days > _MAX_MARKET_CLOSURE_DAYS:
            return True
    except TypeError:
        return False
    from app.taiwan.daily_store import TaiwanDailyStore

    store = TaiwanDailyStore()
    day = start
    while day < earliest:
        if store.has_date(day):
            return True
        day += timedelta(days=1)
    return False


def _read_taiwan_local_daily(symbol: str, start: date, end: date):
    """Read daily bars from TaiwanDailyStore, extended back to warm up indicator windows.

    Returns None when the store is unreadable so the caller falls back to a live fetch.
    """
    from app.taiwan.benchmarks import get_benchmark
    from app.taiwan.daily_store import TaiwanDailyStore

    warmup_start = start - timedelta(days=_TAIWAN_INDICATOR_WARMUP_DAYS)
    try:
        if (bench := get_benchmark(symbol)) is not None:
            from app.taiwan.index_daily import get_index_daily_service

            return get_index_daily_service().read_range(bench.symbol, warmup_start, end)
        return TaiwanDailyStore().scan_range([symbol], warmup_start, end)
    except Exception as e:
        logger.debug("taiwan local daily read failed %s: %s", symbol, e)
        return None


def _with_reference_prices(symbol: str, local):
    """Local Taiwan bars with the reference prices and official bases the enriched publish gives them."""
    if not symbol.endswith(TAIWAN_SUFFIXES) or not {"symbol", "date"} <= set(local.columns):
        return local
    from app.taiwan.corporate_actions import TaiwanCorporateActionStore, read_events_or_empty
    from app.taiwan.enriched_refresh import with_published_bases

    events = read_events_or_empty(TaiwanCorporateActionStore(), end=local["date"].max(), symbols=[symbol])
    return with_published_bases(local, events)


def _taiwan_local_factors(symbol: str, local):
    """Forward-adjustment factors for local Taiwan bars, matching the published enriched scale."""
    import polars as pl

    if not symbol.endswith(TAIWAN_SUFFIXES) or "date" not in local.columns or local.is_empty():
        return pl.DataFrame()
    from app.taiwan.corporate_actions import (
        TaiwanCorporateActionStore,
        adjustment_factors,
        read_events_or_empty,
    )

    through = local["date"].max()
    return adjustment_factors(read_events_or_empty(TaiwanCorporateActionStore(), end=through, symbols=[symbol]), through)


@router.get("/daily")
def get_daily(
    request: Request,
    symbol: str = Query(..., description="标的代码,如 000001.SZ"),
    days: int = Query(120, ge=10, le=2000),
    start_date: Optional[str] = Query(None, description="起始日期 YYYY-MM-DD, 优先于 days"),
    end_date: Optional[str] = Query(None, description="截止日期 YYYY-MM-DD, 默认今天"),
    ext_columns: Optional[str] = Query(None, description="逗号分隔的 ext 列: config_id.field_name"),
):
    """读取本地 enriched 表中某只股票的日 K。

    - 若 QuoteService 有实时行情, 追加/覆盖今日实时蜡烛
    - Free 用户: 若 enriched 表里没有该股票, 实时拉取 + 本地算 enriched 返回
    - ext_columns: 可选，动态 LEFT JOIN 扩展数据表，结果平铺到 stock_info.ext 下
      (key 为 "{config_id}__{field_name}")，供日K信息条等场景展示自定义字段
    """
    import polars as pl

    repo = request.app.state.repo
    end = date.fromisoformat(end_date) if end_date else date.today()
    if start_date:
        start = date.fromisoformat(start_date)
    else:
        start = end - timedelta(days=days)

    asset_type = repo.resolve_asset_type(symbol)
    stock_info = _get_stock_info(repo, symbol) if asset_type == "stock" else _get_asset_info(repo, symbol, asset_type)
    stock_name = stock_info.get("name")

    # 从 enriched 表读取 (已含前复权 OHLCV + 技术指标 + 信号); 指数走独立存储
    df = repo.get_daily_asset(asset_type, symbol, start, end)

    if _needs_local_history(df, start):
        local = _read_taiwan_local_daily(symbol, start, end)
        if local is not None and not local.is_empty():
            enriched = compute_enriched(
                _with_reference_prices(symbol, local), factors=_taiwan_local_factors(symbol, local)
            )
            rows = enriched.filter(pl.col("date") >= start).to_dicts()
            rows = _onto_todays_scale(symbol, rows, local["date"].max(), cn_today())
            rows = _maybe_inject_live_candle(request, symbol, rows, asset_type)
            resp = {"symbol": symbol, "name": stock_name, "stock_info": stock_info,
                    "rows": rows, "source": "taiwan_local"}
            return _attach_ext(resp, repo, symbol, ext_columns)

    if df.is_empty():
        try:
            raw = kline_sync.sync_daily_batch([symbol], count=days + 30)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"daily data fetch failed: {e}") from e
        if raw.is_empty():
            return {"symbol": symbol, "name": stock_name, "stock_info": stock_info, "rows": []}
        enriched = compute_enriched(raw)
        rows = enriched.tail(days).to_dicts()
        # 即使 live 模式也尝试追加实时蜡烛
        rows = _maybe_inject_live_candle(request, symbol, rows, asset_type)
        resp = {"symbol": symbol, "name": stock_name, "stock_info": stock_info, "rows": rows, "source": "live"}
        return _attach_ext(resp, repo, symbol, ext_columns)

    rows = _onto_todays_scale(symbol, df.to_dicts(), _published_scale_through(), cn_today())

    # 追加/覆盖今日实时蜡烛
    rows = _maybe_inject_live_candle(request, symbol, rows, asset_type)

    resp = {"symbol": symbol, "name": stock_name, "stock_info": stock_info, "rows": rows, "source": "enriched"}
    return _attach_ext(resp, repo, symbol, ext_columns)


def _attach_ext(resp: dict, repo, symbol: str, ext_columns: Optional[str]) -> dict:
    """按 ext_columns 规格为单只股票 LEFT JOIN 扩展数据，平铺到 stock_info['ext']。

    key 形如 "{config_id}__{field_name}"，与自选列表 enriched 接口保持一致。
    委托 screener._load_ext_value_maps 取值: 复用其 (路径,mtime) 签名缓存,
    个股弹窗每秒重拉时不再重复读 ext parquet; 任何 ext 表/字段缺失都静默跳过。
    """
    if not ext_columns or not ext_columns.strip():
        return resp

    specs: list[tuple[str, str]] = []
    for part in ext_columns.split(","):
        part = part.strip()
        if "." not in part:
            continue
        config_id, field_name = part.split(".", 1)
        config_id, field_name = config_id.strip(), field_name.strip()
        if config_id and field_name and is_valid_ext_ident(config_id):
            specs.append((config_id, field_name))
    if not specs:
        return resp

    try:
        from app.api.screener import _load_ext_value_maps
        value_maps = _load_ext_value_maps(repo, ext_columns)
    except Exception:  # noqa: BLE001
        value_maps = {}

    ext_values: dict = {}
    for config_id, field_name in specs:
        ext_col_name = f"{config_id}__{field_name}"
        vmap = value_maps.get(ext_col_name) or {}
        ext_values[ext_col_name] = vmap.get(symbol)

    stock_info = dict(resp.get("stock_info") or {})
    stock_info["ext"] = ext_values
    resp["stock_info"] = stock_info
    return resp


def _published_scale_through() -> date | None:
    """The newest session whose events the published Taiwan enriched scale includes."""
    from app.taiwan.enriched_refresh import published_factor_through

    return published_factor_through()


def _pending_events(scale_through: date | None, today: date):
    """Taiwan events a scale ending on *scale_through* lacks, up to *today*; none if unreadable."""
    import polars as pl

    from app.taiwan.corporate_actions import FACTOR_SCHEMA, pending_factors

    try:
        return pending_factors(scale_through, today)
    except Exception as e:
        logger.warning("pending corporate-action factors unavailable: %s", e)
        return pl.DataFrame(schema=FACTOR_SCHEMA)


def _onto_todays_scale(symbol: str, rows: list[dict], scale_through: date | None, today: date) -> list[dict]:
    """Move Taiwan rows priced on a scale ending on *scale_through* onto the scale of *today*.

    A row is divided by the factors of the pending events dated after it, and its volumes
    multiplied by their volume factors; raw_* and the reference columns stay as stored.
    """
    if not symbol.endswith(TAIWAN_SUFFIXES) or not rows:
        return rows
    import polars as pl

    events = (
        _pending_events(scale_through, today)
        .filter(pl.col("symbol") == symbol)
        .select("trade_date", "ex_factor", "volume_factor")
        .rows()
    )
    if not events:
        return rows
    for row in rows:
        day = str(row.get("date"))[:10]
        factor = volume_factor = 1.0
        for event_day, event_factor, event_volume_factor in events:
            if event_day.isoformat() > day:
                factor *= event_factor
                volume_factor *= event_volume_factor
        if factor == volume_factor == 1.0:
            continue
        for key in PRICE_COLUMNS:
            if isinstance(row.get(key), (int, float)):
                row[key] = row[key] / factor
        for key in VOLUME_COLUMNS:
            if isinstance(row.get(key), (int, float)):
                row[key] = row[key] * volume_factor
    return rows


def _frame_onto_todays_scale(df, scale_through: date | None, today: date):
    """_onto_todays_scale for a frame of several symbols."""
    import polars as pl

    if df.is_empty() or not any(df["symbol"].str.ends_with(suffix).any() for suffix in TAIWAN_SUFFIXES):
        return df
    events = _pending_events(scale_through, today)
    if events.is_empty():
        return df
    later = pl.col("_event_date") > pl.col("date")
    per_row = (
        df.select("symbol", "date")
        .unique()
        .join(events.rename({"trade_date": "_event_date"}), on="symbol")
        .group_by("symbol", "date")
        .agg(
            pl.when(later).then(pl.col("ex_factor")).otherwise(1.0).product().alias("_factor"),
            pl.when(later).then(pl.col("volume_factor")).otherwise(1.0).product().alias("_volume_factor"),
        )
    )
    factor = pl.col("_factor").fill_null(1.0)
    volume_factor = pl.col("_volume_factor").fill_null(1.0)
    return (
        df.join(per_row, on=["symbol", "date"], how="left", maintain_order="left")
        .with_columns(
            [(pl.col(c) / factor).alias(c) for c in PRICE_COLUMNS if c in df.columns]
            + [(pl.col(c) * volume_factor).alias(c) for c in VOLUME_COLUMNS if c in df.columns]
        )
        .drop(["_factor", "_volume_factor"])
    )


def _mark_taiwan_live_volume(live_row: dict, q: dict, *, polled: bool) -> None:
    """Flag a Taiwan candle whose volume is the intraday quote's rather than the official row's.

    Intraday quotes count board-lot trades only and carry no amount; the intraday pipeline
    stamps its rows with volume_time_factor, the published row has none. board_lot_volume
    tells the two apart, and volume_time_factor scales the candle's volume to a full session.
    """
    factor = q.get("volume_time_factor")
    if factor is None and not polled:
        from app.taiwan.live_enriched import _volume_time_factor

        factor = _volume_time_factor(q.get("quote_ts"))
    live_row["board_lot_volume"] = factor is not None
    if factor is not None:
        live_row["volume_time_factor"] = factor
        live_row["amount"] = None


def _maybe_inject_live_candle(request: Request, symbol: str, rows: list[dict], asset_type: str = "stock") -> list[dict]:
    """如果有当日实时 enriched 数据, 用实时数据生成今日蜡烛并追加/覆盖。

    Only stocks have a live cache (QuoteService); Taiwan ETFs are polled as stocks.
    """
    if asset_type != "stock":
        return rows
    qs = getattr(request.app.state, "quote_service", None)
    if not qs:
        return rows
    df_today, enriched_date = qs.get_enriched_today()
    if df_today.is_empty():
        return rows

    # 非交易日（周末/假日）缓存的行情日期 != 今天，跳过注入避免产生重复蜡烛
    if not enriched_date or enriched_date != date.today():
        return rows

    # 查找该 symbol 的实时 enriched 行
    import polars as pl
    try:
        matches = df_today.filter(pl.col("symbol") == symbol).to_dicts()
    except Exception:  # noqa: BLE001
        return rows
    if matches:
        q = matches[0]
    else:
        # The poll covers only watchlist and rule symbols; any other stock opened
        # from a list would otherwise never get today's candle.
        q = qs.fetch_live_bar(symbol)
        if not q:
            return rows

    close_price = q.get("close")
    if not close_price or close_price <= 0:
        return rows

    today_str = str(enriched_date)

    # enriched 行已包含 OHLCV + 全套指标, 直接用它
    # 修复: API 在非交易时段可能返回 open/high/low=0, 用 close 填充避免异常蜡烛
    raw_open = q.get("open")
    raw_high = q.get("high")
    raw_low = q.get("low")
    live_row: dict = {
        "date": today_str,
        "symbol": symbol,
        "open": raw_open if raw_open and raw_open > 0 else close_price,
        "high": raw_high if raw_high and raw_high > 0 else close_price,
        "low": raw_low if raw_low and raw_low > 0 else close_price,
        "close": close_price,
        "volume": q.get("volume"),
        "amount": q.get("amount"),
        "change_pct": q.get("change_pct"),
        "is_live": True,
    }
    # The quote's previous close is the exchange reference, which differs from the
    # last daily close on a corporate-action session.
    live_prev_close = q.get("prev_close")
    if isinstance(live_prev_close, (int, float)) and math.isfinite(live_prev_close) and live_prev_close > 0:
        live_row["prev_close"] = live_prev_close
    # 补上 enriched 的技术指标字段
    for key in ("ma5", "ma10", "ma20", "ma30", "ma60",
                "macd_dif", "macd_dea", "macd_hist",
                "kdj_k", "kdj_d", "kdj_j",
                "boll_upper", "boll_lower",
                "rsi_6", "rsi_14", "rsi_24",
                "atr_14", "vol_ratio_5d", "turnover_rate"):
        if key in q and q[key] is not None:
            live_row[key] = q[key]
    if symbol.endswith(TAIWAN_SUFFIXES):
        _mark_taiwan_live_volume(live_row, q, polled=bool(matches))

    # 如果已有今天的 enriched 行, 覆盖; 否则追加
    found = False
    for i, r in enumerate(rows):
        if str(r.get("date")) == today_str:
            r.update(live_row)
            found = True
            break

    if not found:
        rows.append(live_row)

    return rows


@router.post("/daily-batch")
def get_daily_batch(request: Request, body: dict):
    """批量获取多只股票最近 N 天日K (OHLCV)。

    用于自选列表迷你蜡烛图等场景，只返回基础列，不返回全部 enriched 指标。
    """
    symbols = body.get("symbols", [])
    days = body.get("days", 12)
    if not symbols:
        return {"data": {}}
    days = max(5, min(60, days))

    repo = request.app.state.repo
    import polars as pl
    from datetime import date, timedelta

    end = date.today()
    start = end - timedelta(days=days * 2)  # 多取一些确保交易日够

    cols = ["symbol", "date", "open", "high", "low", "close", "volume"]

    # 按资产类型分组: stock 走批量缓存; index 逐只查独立存储 (数量少, 成本可忽略)
    stock_symbols: list[str] = []
    index_symbols: list[str] = []
    for s in symbols:
        if repo.resolve_asset_type(s) == "index":
            index_symbols.append(s)
        else:
            stock_symbols.append(s)

    frames: list[pl.DataFrame] = []
    if stock_symbols:
        df_stock = repo.get_daily_batch(
            stock_symbols, start, end, columns=[*cols, "raw_close", "ref_prev_close", "ref_base_missing"]
        )
        if not df_stock.is_empty():
            frames.append(df_stock)
    for sym in index_symbols:
        sub = repo.get_index_daily(sym, start, end, columns=cols)
        if not sub.is_empty():
            frames.append(sub)

    if not frames:
        return {"data": {}}
    df = _frame_onto_todays_scale(pl.concat(frames, how="diagonal_relaxed"), _published_scale_through(), cn_today())
    # The pipeline's own base, so a corporate-action session carries the exchange reference price.
    df = compute_indicators(df, needed={"prev_close"}).select(
        [*[c for c in cols if c in df.columns], "prev_close"]
    )

    # 按 symbol 分组, 每只取最近 N 条。
    # partition_by 一次切分, 避免 N 只自选时对同一批数据做 N 次全帧过滤。
    result: dict[str, list[dict]] = {}
    for part in df.partition_by("symbol", maintain_order=True):
        sub = part.sort("date").tail(days)
        if not sub.is_empty():
            result[sub["symbol"][0]] = sub.to_dicts()

    return {"data": result}


@router.post("/minute-batch")
def get_minute_batch(request: Request, body: dict):
    """批量获取多只股票某天的分钟K (分时图用)。

    - 本地优先: 先从 kline_minute parquet 读, 完整的直接用
    - 缺失补拉: 本地不完整的 symbol 用 sync_minute_batch 批量实时拉 (不落库)
    """
    import polars as pl

    symbols: list[str] = body.get("symbols", [])
    trade_date_str: str | None = body.get("date")
    if not symbols:
        return {"data": {}}

    repo = request.app.state.repo

    trade_date = date.fromisoformat(trade_date_str) if trade_date_str else cn_today()

    # 非交易日(周末/节假日)才回退到最近有数据的交易日; 否则盘中会显示昨天而非今天。
    # 注意: 不能用 latest_minute_date_global() 判断盘中是否为交易日 —— 批量实时补拉
    # 不落库 (见下方 sync_minute_batch 无 on_segment), 盘中它恒返回上次全量同步日,
    # 用它做判据会导致 trade_date 永久回退到昨天, 再因当日期望条数判定昨日"完整"
    # 而不再补拉今天, 形成永远显示昨日的死循环。
    # 判据改为: 周末必回退; 工作日收盘+缓冲后仍无今日日K → 节假日, 回退。
    if not trade_date_str:
        today = cn_today()
        need_fallback = today.weekday() >= 5  # 周六/周日必非交易日
        if not need_fallback:
            now_cn = cn_now()
            after_close = _past_close_with_margin(now_cn)
            if after_close:
                latest_daily = repo.latest_daily_date()
                if latest_daily is None or latest_daily < today:
                    need_fallback = True
        if need_fallback:
            recent_date = repo.latest_minute_date_global()
            if recent_date is None:
                recent_date = repo.latest_daily_date()
            if recent_date is not None:
                trade_date = recent_date

    # Step 1: 本地优先 — 一次 scan 读全部 symbol 当日分钟K
    df_local = repo.get_minute_batch(symbols, trade_date)

    expected = _expected_minute_rows(trade_date)

    # 按 symbol 分组, 判定哪些不完整需要补拉 (partition_by 一次切分, 同 daily-batch)
    result: dict[str, list[dict]] = {}
    incomplete: list[str] = []
    local_parts: dict[str, pl.DataFrame] = {}
    if not df_local.is_empty():
        for part in df_local.partition_by("symbol", maintain_order=True):
            local_parts[part["symbol"][0]] = part.sort("datetime")
    for sym in symbols:
        sub = local_parts.get(sym, pl.DataFrame())
        if expected > 0 and (sub.is_empty() or len(sub) < expected * 0.9):
            incomplete.append(sym)
        elif not sub.is_empty():
            result[sym] = sub.to_dicts()

    # Step 2: 缺失的 symbol 批量实时拉取 (不落库)
    if incomplete:
        start_time, end_time = kline_sync.intraday_window(trade_date)
        # 契约: 本端点只接受股票与 ETF (指数分钟K已随 Phase 8B-5.1 一并移除);
        # 若未来重新支持指数, 需拆出 index 分支以免被误路由为 stock。
        live_df = kline_sync.sync_minute_batch(
            incomplete,
            start_time=start_time,
            end_time=end_time,
            asset_type="stock",
        )
        if not live_df.is_empty():
            live_map: dict[str, pl.DataFrame] = {
                part["symbol"][0]: part.sort("datetime")
                for part in live_df.partition_by("symbol", maintain_order=True)
            }
            for sym in incomplete:
                sub = live_map.get(sym)
                if sub is not None and not sub.is_empty():
                    result[sym] = sub.to_dicts()

        # Taiwan minute bars are accumulated locally from the realtime poll, so the
        # local copy only ever covers the part of the session this process ran for
        # and never clears the completeness bar. Partial real bars beat an empty
        # chart when no source can fill the gap.
        for sym in incomplete:
            if sym in result:
                continue
            sub = local_parts.get(sym)
            if sub is not None and not sub.is_empty():
                result[sym] = sub.to_dicts()

    return {"data": result}


@router.get("/minute-range")
def get_minute_range(
    request: Request,
    symbol: str = Query(..., description="标的代码"),
    days: int = Query(10, ge=1, le=20, description="最近交易日数量"),
):
    """读取单只标的最近 N 个已落库交易日的分钟 K。"""
    import polars as pl

    repo = request.app.state.repo
    asset_type = repo.resolve_asset_type(symbol)
    stock_info = (
        _get_stock_info(repo, symbol)
        if asset_type == "stock"
        else _get_asset_info(repo, symbol, asset_type)
    )
    base_response = {
        "symbol": symbol,
        "name": stock_info.get("name"),
        "asset_type": asset_type,
        "requested_days": days,
    }

    # 指数分钟 K 不落本地仓库 (Phase 8B-5.1: 只读实时端点已随 Indices.tsx 一并删除)。
    if asset_type == "index":
        return {**base_response, "sessions": [], "source": "none"}

    end = cn_today()
    start = end - timedelta(days=days * 3 + 20)
    minute = repo.get_minute_range([symbol], start, end)
    if minute.is_empty() or "datetime" not in minute.columns:
        return {**base_response, "sessions": [], "source": "none"}

    minute = minute.with_columns(
        pl.col("datetime").dt.date().alias("_trade_date"),
    )
    trade_dates = sorted(minute["_trade_date"].unique().to_list())[-days:]
    previous_closes = _get_previous_closes(repo, symbol, trade_dates, asset_type)
    row_columns = [
        column
        for column in (
            "datetime", "open", "high", "low", "close", "volume", "amount", "unattributed_volume"
        )
        if column in minute.columns
    ]
    sessions = []
    for trade_date in trade_dates:
        rows = (
            minute.filter(pl.col("_trade_date") == trade_date)
            .sort("datetime")
            .select(row_columns)
            .to_dicts()
        )
        if rows:
            sessions.append({
                "date": trade_date.isoformat(),
                "prev_close": previous_closes.get(trade_date),
                "rows": rows,
            })

    return {
        **base_response,
        "sessions": sessions,
        "source": "local" if sessions else "none",
    }


@router.get("/minute")
def get_minute(
    request: Request,
    symbol: str = Query(..., description="标的代码"),
    trade_date: date | None = Query(None, alias="date", description="交易日期, 默认最新"),
):
    """读取某只股票某天的分钟 K 线。

    - 本地有完整数据(当日已交易分钟数, 盘后为全时段 270 条) → 直接返回
    - 本地无数据或不完整 → 从自定义分钟源实时拉取返回（不写入）
    """
    repo = request.app.state.repo
    asset_type = repo.resolve_asset_type(symbol)
    stock_info = _get_stock_info(repo, symbol) if asset_type == "stock" else _get_asset_info(repo, symbol, asset_type)
    stock_name = stock_info.get("name")

    if trade_date is None:
        # 默认看今天, 而不是本地落盘的最近日 (盘中后者是昨天)。
        # 非交易日(周末/节假日)才回退到本地最近有数据的交易日。
        today = cn_today()
        need_fallback = today.weekday() >= 5  # 周六/周日必非交易日
        if not need_fallback:
            now_cn = cn_now()
            after_close = _past_close_with_margin(now_cn)
            if after_close:
                latest_daily = repo.latest_daily_date()
                if latest_daily is None or latest_daily < today:
                    need_fallback = True
        if need_fallback:
            recent = repo.latest_minute_date(symbol)
            if recent is None:
                recent = repo.latest_daily_date()
            trade_date = recent if recent is not None else today
        else:
            trade_date = today

    prev_close, price_limit = _session_price_limit(repo, symbol, trade_date, asset_type)
    df = repo.get_minute(symbol, trade_date)

    expected = _expected_minute_rows(trade_date)

    is_complete = not df.is_empty() and len(df) >= expected * 0.9  # 允许 10% 容差

    if is_complete:
        return {
            "symbol": symbol, "name": stock_name, "stock_info": stock_info,
            "date": str(trade_date), "rows": df.to_dicts(), "source": "local",
            "asset_type": asset_type,
            "price_limit": price_limit,
            "prev_close": prev_close,
        }

    # 本地不完整或无数据 → 从数据源实时拉取
    live_df = kline_sync.fetch_minute_single(symbol, trade_date, asset_type=asset_type)
    if live_df.is_empty() and not df.is_empty():
        # Taiwan minute bars are accumulated locally from the realtime poll, so the
        # local copy only ever covers the part of the session this process ran for.
        # Partial real bars beat an empty chart when no source can fill the gap.
        return {
            "symbol": symbol, "name": stock_name, "stock_info": stock_info,
            "date": str(trade_date), "rows": df.to_dicts(), "source": "local_partial",
            "asset_type": asset_type,
            "price_limit": price_limit,
            "prev_close": prev_close,
        }
    return {
        "symbol": symbol, "name": stock_name, "stock_info": stock_info,
        "date": str(trade_date), "rows": live_df.to_dicts(),
        "source": "live" if not live_df.is_empty() else "none",
        "asset_type": asset_type,
        "price_limit": price_limit,
        "prev_close": prev_close,
    }


@router.post("/sync_minute_single")
async def sync_minute_single(request: Request, body: dict):
    """手动拉取单只股票的分钟K并落库 (前复权)。

    body: { "symbol": "000001.SZ" }
    用于个股分时图"获取数据"按钮: 本地无数据时单独拉取并持久化。
    """
    import asyncio

    from app.services.preferences import get_minute_sync_days

    symbol = body.get("symbol", "").strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol 不能为空")

    requested_days = body.get("days")
    if requested_days is not None:
        if isinstance(requested_days, bool) or not isinstance(requested_days, int):
            raise HTTPException(status_code=400, detail="days 必须是整数")
        if requested_days < 1 or requested_days > 30:
            raise HTTPException(status_code=400, detail="days 必须在 1 到 30 之间")

    repo = request.app.state.repo

    # 指数分钟K无本地存储, 落库会污染股票分钟表 kline_minute, 此端点显式拒绝。
    # (Phase 8B-5.1: 指数分钟K只读端点 /api/index/minute 已随 Indices.tsx 一并删除,
    # 无实时读取替代路径。)
    if repo.resolve_asset_type(symbol) == "index":
        raise HTTPException(status_code=400, detail="指数分钟K不支持落库同步")

    days = requested_days if requested_days is not None else get_minute_sync_days()
    loop = asyncio.get_event_loop()

    def _run():
        return kline_sync.sync_and_persist_minute([symbol], repo, days=days, force_full_days=True)

    written = await loop.run_in_executor(_long_task_executor, _run)

    repo.refresh_view("kline_minute")

    return {"status": "ok", "symbol": symbol, "rows": written}


# 长时间任务专用线程池（隔离于 FastAPI 默认线程池，防止阻塞请求处理）
import concurrent.futures as _cf
_long_task_executor = _cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="long-task")
