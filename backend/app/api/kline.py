"""K 线 / 同步 API。

Phase 8B-5.19: 移除已确认 zero 前端/后端/scheduler/脚本消费者的全市场批量维护
端点 sync_minute(全市场分钟K批量同步)、clear_minute(清空分钟K)、
extend_history(向前扩展历史日K)、repair_daily(修正日K)、rebuild_enriched
(全量重算 enriched)——均原供已删除的 Data.tsx 使用。

sync_minute_single(个股分时图"获取数据"按钮用, 与被删的批量 sync_minute 是
不同端点)完全未动。kline_sync.py 的 sync_and_persist_minute 仍被
sync_minute_single 与 daily_pipeline.py 真实使用; run_repair_daily
(backend/app/services/repair_daily.py)仍被 data_integrity.py 的启动自愈
(launch_integrity_repair → boot_integrity_check)真实使用; run_pipeline
(app/indicators/pipeline.py)仍被 daily_pipeline.py / quote_service.py 真实
使用——均完全未动。extend_history.py 保留(其 run_extend_history 现无其他
调用方, 留待后续独立的 service-level orphan 稽核, 本 phase 不处理)。
_long_task_executor 仍被 sync_minute_single 使用, 完整保留。

Phase 8B-5.21: 移除已确认 zero 前端/后端/scheduler/脚本/测试消费者的相邻维护
端点 sync_batch(全市场批量日K同步, 原供已删除的 Data.tsx 使用)、
refresh_views(手动刷新 DuckDB 视图)。sync_and_persist_daily_batch 仍被
daily_pipeline.py / extend_history.py / 本文件的 sync(单股同步)真实使用;
daily_pipeline._refresh_views → repository.rebuild_views() 仍被 daily_pipeline
盘后管道每次运行时直接调用(非 HTTP 路径), 完全未动。

Phase 8B-FINAL: 移除已确认 zero 前端/后端/scheduler/脚本/测试消费者、且可
对呼叫者提供的任意 symbol 触发真实网络拉取 + parquet 落盘的孤儿端点 sync
(单股同步, 与 8B-5.21 已移除的 sync_batch 同源同险, 仅批量/单股之别)。
sync_and_persist_daily_batch 仍被 daily_pipeline.py / extend_history.py 真实
使用, 完全未动。
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from app.indicators.pipeline import compute_enriched, compute_enriched_single
from app.market_time import cn_now, cn_today
from app.price_limits import is_risk_warning_name, price_limit_pct
from app.db_safe import is_valid_ext_ident
from app.services import kline_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kline", tags=["kline"])


def _minute_allowed(capset) -> bool:
    """是否有分钟K权限 (TickFlow Pro+ 或 custom minute 源)。"""
    from app.tickflow.capabilities import Cap
    if capset.has(Cap.KLINE_MINUTE_BATCH):
        return True
    from app.services import preferences
    provider = preferences.get_minute_data_provider()
    _, fallback, error = kline_sync._resolve_minute_provider(provider)
    if error is not None:
        logger.warning("minute provider resolution failed while checking access: %s", error)
    return not fallback


def _search_taiwan_instruments(q: str, limit: int) -> list[dict]:
    """仅查 TaiwanSecurityMaster, 不触碰 repo.get_instruments*() 的 legacy A 股缓存。

    canonical symbol 形如 2330.TWSE / 6488.TPEX; 支持代码/canonical symbol/
    繁体中文名称查询, 排序/评分逻辑在 TaiwanSecurityMaster.search() 内完成。
    只保留 is_supported 的 stock/etf(权证等未支援标的不该出现在自选搜索里)。
    """
    from app.taiwan.universe import get_security_master

    hits = get_security_master().search(q.strip(), limit=limit)
    return [
        {
            "symbol": hit["symbol"],
            "name": hit["name"],
            "code": hit["code"],
            "asset_type": hit["instrument_type"],
            "market": "taiwan",
        }
        for hit in hits
        if hit.get("is_supported") and hit.get("instrument_type") in ("stock", "etf")
    ][:limit]


@router.get("/instruments/search")
def search_instruments(
    q: str = Query("", min_length=0, max_length=50, description="搜索关键词"),
    limit: int = Query(20, ge=1, le=50),
    asset_types: str = Query("stock", description="逗号分隔的资产类型: stock,etf"),
):
    """模糊搜索台股标的 (代码 / 繁体中文名称), 查 TaiwanSecurityMaster。

    结果统一附带 market: "taiwan" 字段, symbol 形如 2330.TWSE / 6488.TPEX。
    """
    if not q.strip():
        return {"results": []}
    return {"results": _search_taiwan_instruments(q, limit)}


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
    """
    import polars as pl
    try:
        df = repo.get_instruments()
        needed = ("symbol", "name", "total_shares", "float_shares")
        if df.is_empty() or not all(c in df.columns for c in needed):
            return {}
        hit = df.filter(pl.col("symbol") == symbol).head(1)
        if hit.is_empty():
            return {}
        return {
            "name": hit["name"][0],
            "total_shares": hit["total_shares"][0],
            "float_shares": hit["float_shares"][0],
        }
    except Exception:  # noqa: BLE001
        return {}


def _get_asset_info(repo, symbol: str, asset_type: str) -> dict:
    """非股票标的 (ETF / 指数) 的名称信息 — 从对应 instruments 缓存查, 无股本概念。"""
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
    repo,
    symbol: str,
    trade_date: date,
    asset_type: str,
    instrument_name: str | None,
) -> dict | None:
    """Return the date-aware limit rule and today's authoritative prices."""
    if asset_type == "index":
        return None

    info = {
        "rate": price_limit_pct(
            symbol,
            trade_date,
            is_risk_warning=(
                asset_type == "stock" and is_risk_warning_name(instrument_name)
            ),
        ),
        "limit_up": None,
        "limit_down": None,
        "source": "rule",
    }
    if trade_date != cn_today():
        return info

    try:
        import polars as pl

        instruments = repo.get_instruments_asset(asset_type)
        available = [
            column
            for column in ("symbol", "limit_up", "limit_down")
            if column in instruments.columns
        ]
        if "symbol" not in available or len(available) == 1:
            return info
        hit = instruments.filter(pl.col("symbol") == symbol).select(available).head(1)
        row = hit.to_dicts()[0] if not hit.is_empty() else None
    except Exception:
        return info
    if row is None:
        return info

    has_authoritative_price = False
    for field in ("limit_up", "limit_down"):
        value = row.get(field)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric) and 0 < numeric < 10_000:
            info[field] = numeric
            has_authoritative_price = True
    if has_authoritative_price:
        info["source"] = "instrument"
    return info


def _get_previous_closes(
    repo,
    symbol: str,
    trade_dates: list[date],
    asset_type: str,
) -> dict[date, float | None]:
    """Return the previous trading day's adjusted close for each session."""
    if not trade_dates:
        return {}
    start = min(trade_dates) - timedelta(days=45)
    end = max(trade_dates)
    try:
        daily = repo.get_daily_asset(
            asset_type,
            symbol,
            start,
            end,
            columns=["date", "close"],
        ).sort("date")
    except Exception:
        daily = None
    if daily is None or daily.is_empty():
        return {trade_date: None for trade_date in trade_dates}

    closes: list[tuple[date, float]] = []
    for daily_date, close in daily.select(["date", "close"]).iter_rows():
        if close is None:
            continue
        numeric = float(close)
        if math.isfinite(numeric) and numeric > 0:
            closes.append((daily_date, numeric))

    result: dict[date, float | None] = {}
    for trade_date in trade_dates:
        result[trade_date] = next(
            (close for daily_date, close in reversed(closes) if daily_date < trade_date),
            None,
        )
    return result


_TAIWAN_INDICATOR_WARMUP_DAYS = 150


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
    """Enriched rows may cover only the live candle, which cannot satisfy a range query."""
    if df.is_empty():
        return True
    if "date" not in df.columns:
        return False
    earliest = df["date"].min()
    if earliest is None:
        return True
    try:
        return earliest > start
    except TypeError:
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

    # 从 enriched 表读取 (已含前复权 OHLCV + 技术指标 + 信号); ETF/指数走独立存储
    df = repo.get_daily_asset(asset_type, symbol, start, end)

    if _needs_local_history(df, start):
        local = _read_taiwan_local_daily(symbol, start, end)
        if local is not None and not local.is_empty():
            enriched = compute_enriched(local, factors=pl.DataFrame())
            rows = enriched.filter(pl.col("date") >= start).to_dicts()
            rows = _maybe_inject_live_candle(request, symbol, rows, asset_type)
            resp = {"symbol": symbol, "name": stock_name, "stock_info": stock_info,
                    "rows": rows, "source": "taiwan_local"}
            return _attach_ext(resp, repo, symbol, ext_columns)

    if df.is_empty():
        try:
            raw = kline_sync.sync_daily_batch([symbol], count=days + 30)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"TickFlow fetch failed: {e}") from e
        if raw.is_empty():
            return {"symbol": symbol, "name": stock_name, "stock_info": stock_info, "rows": []}
        # 拉除权因子做前复权 (Starter+ 有权限), 否则空 df → compute_enriched 退回未复权
        factors = pl.DataFrame()
        capset = getattr(request.app.state, "capabilities", None)
        try:
            from app.tickflow.capabilities import Cap
            if capset and capset.has(Cap.ADJ_FACTOR):
                factors = kline_sync.fetch_adj_factor_single(symbol)
        except Exception as e:  # noqa: BLE001
            logger.debug("单股除权因子拉取失败 %s: %s", symbol, e)
        enriched = compute_enriched(raw, factors=factors)
        rows = enriched.tail(days).to_dicts()
        # 即使 live 模式也尝试追加实时蜡烛
        rows = _maybe_inject_live_candle(request, symbol, rows, asset_type)
        resp = {"symbol": symbol, "name": stock_name, "stock_info": stock_info, "rows": rows, "source": "live"}
        return _attach_ext(resp, repo, symbol, ext_columns)

    rows = df.to_dicts()

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


def _maybe_inject_live_candle(request: Request, symbol: str, rows: list[dict], asset_type: str = "stock") -> list[dict]:
    """如果有当日实时 enriched 数据, 用实时数据生成今日蜡烛并追加/覆盖。

    stock 走 QuoteService 的股票实时缓存; etf 走 ETF enriched 缓存 (开启实时 ETF
    拉取时为盘中数据, 否则为磁盘最新日, 由下方"非今日不注入"守卫自然跳过)。
    """
    if asset_type == "stock":
        qs = getattr(request.app.state, "quote_service", None)
        if not qs:
            return rows
        df_today, enriched_date = qs.get_enriched_today()
    elif asset_type == "etf":
        df_today, enriched_date = request.app.state.repo.get_enriched_latest_asset("etf")
    else:
        return rows
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
    elif asset_type == "stock":
        # The poll covers only watchlist and rule symbols; any other stock opened
        # from a list would otherwise never get today's candle.
        q = qs.fetch_live_bar(symbol)
        if not q:
            return rows
    else:
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
    # 补上 enriched 的技术指标字段
    for key in ("ma5", "ma10", "ma20", "ma30", "ma60",
                "macd_dif", "macd_dea", "macd_hist",
                "kdj_k", "kdj_d", "kdj_j",
                "boll_upper", "boll_lower",
                "rsi_6", "rsi_14", "rsi_24",
                "atr_14", "vol_ratio_5d"):
        if key in q and q[key] is not None:
            live_row[key] = q[key]

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


class DailyBatchRequest:
    """批量日K请求。"""
    symbols: list[str]
    days: int = 12


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

    # 按资产类型分组: stock 走批量缓存; etf/index 逐只查独立存储 (数量少, 成本可忽略)
    stock_symbols: list[str] = []
    etf_symbols: list[str] = []
    index_symbols: list[str] = []
    for s in symbols:
        t = repo.resolve_asset_type(s)
        if t == "etf":
            etf_symbols.append(s)
        elif t == "index":
            index_symbols.append(s)
        else:
            stock_symbols.append(s)

    frames: list[pl.DataFrame] = []
    if stock_symbols:
        df_stock = repo.get_daily_batch(stock_symbols, start, end, columns=cols)
        if not df_stock.is_empty():
            frames.append(df_stock)
    for sym in etf_symbols:
        sub = repo.get_etf_daily(sym, start, end, columns=cols)
        if not sub.is_empty():
            frames.append(sub)
    for sym in index_symbols:
        sub = repo.get_index_daily(sym, start, end, columns=cols)
        if not sub.is_empty():
            frames.append(sub)

    if not frames:
        return {"data": {}}
    df = pl.concat(frames, how="diagonal_relaxed")

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
    - 需 Pro+ 权限 (kline.minute.batch)
    """
    import polars as pl
    from app.tickflow.capabilities import Cap

    symbols: list[str] = body.get("symbols", [])
    trade_date_str: str | None = body.get("date")
    if not symbols:
        return {"data": {}}

    repo = request.app.state.repo
    capset = request.app.state.capabilities

    # 权限守卫: 分钟K批量是 Pro+ 能力
    if not capset.has(Cap.KLINE_MINUTE_BATCH):
        raise HTTPException(status_code=403, detail="需要 Pro+ 权限 (kline.minute.batch)")

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

    # Step 1: 本地优先 — 一次 scan 读全部 symbol 当日分钟K (股票 / ETF 分钟数据分开存储)
    etf_set = repo.get_etf_symbol_set()
    stock_syms = [s for s in symbols if s not in etf_set]
    etf_syms = [s for s in symbols if s in etf_set]
    df_local = repo.get_minute_batch(stock_syms, trade_date)
    if etf_syms:
        df_etf = repo.get_minute_batch(etf_syms, trade_date, asset_type="etf")
        if df_local.is_empty():
            df_local = df_etf
        elif not df_etf.is_empty():
            df_local = pl.concat([df_local, df_etf], how="diagonal_relaxed")

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
        lim = capset.limits(Cap.KLINE_MINUTE_BATCH)
        # etf_set 已在上方获取, 直接复用 — 按 asset_type 拆分调用 sync_minute_batch
        # (自定义源 / TickFlow 路由均依赖 asset_type 正确传递)
        # 契约: 本端点只接受 stock/ETF (指数分钟K已随 Phase 8B-5.1 一并移除),
        # 故两分支已覆盖全部 incomplete。若未来重新支持指数, 需额外加 index 分支
        # 以避免被误路由为 stock。
        stock_incomplete = [s for s in incomplete if s not in etf_set]
        etf_incomplete = [s for s in incomplete if s in etf_set]
        live_parts: list[pl.DataFrame] = []
        if stock_incomplete:
            df_s = kline_sync.sync_minute_batch(
                stock_incomplete,
                start_time=start_time,
                end_time=end_time,
                batch_size=lim.batch if lim else None,
                rpm=lim.rpm if lim else None,
                asset_type="stock",
            )
            if not df_s.is_empty():
                live_parts.append(df_s)
        if etf_incomplete:
            df_e = kline_sync.sync_minute_batch(
                etf_incomplete,
                start_time=start_time,
                end_time=end_time,
                batch_size=lim.batch if lim else None,
                rpm=lim.rpm if lim else None,
                asset_type="etf",
            )
            if not df_e.is_empty():
                live_parts.append(df_e)
        if live_parts:
            live_df = pl.concat(live_parts, how="diagonal_relaxed")
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
    minute = repo.get_minute_range([symbol], start, end, asset_type=asset_type)
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
            "datetime", "open", "high", "low", "close", "volume", "amount"
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
    - 本地无数据或不完整 → 从 TickFlow 实时拉取返回（不写入）
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
            recent = repo.latest_minute_date(symbol, asset_type=asset_type)
            if recent is None:
                recent = repo.latest_daily_date()
            trade_date = recent if recent is not None else today
        else:
            trade_date = today
    if trade_date is None:
        # 本地无任何分钟K，尝试从 TickFlow 拉取当天
        trade_date = cn_today()
        df = kline_sync.fetch_minute_single(symbol, trade_date, asset_type=asset_type)
        price_limit = _get_price_limit_info(
            repo, symbol, trade_date, asset_type, stock_name,
        )
        prev_close = _get_previous_closes(
            repo, symbol, [trade_date], asset_type,
        ).get(trade_date)
        return {
            "symbol": symbol, "name": stock_name, "stock_info": stock_info,
            "date": str(trade_date), "rows": df.to_dicts(), "source": "live",
            "asset_type": asset_type,
            "price_limit": price_limit,
            "prev_close": prev_close,
        }

    prev_close = _get_previous_closes(
        repo, symbol, [trade_date], asset_type,
    ).get(trade_date)
    price_limit = _get_price_limit_info(
        repo, symbol, trade_date, asset_type, stock_name,
    )
    df = repo.get_minute(symbol, trade_date, asset_type=asset_type)

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
    capset = request.app.state.capabilities

    # 指数分钟K无本地存储, 落库会污染股票分钟表 kline_minute, 此端点显式拒绝。
    # (Phase 8B-5.1: 指数分钟K只读端点 /api/index/minute 已随 Indices.tsx 一并删除,
    # 无实时读取替代路径。)
    if repo.resolve_asset_type(symbol) == "index":
        raise HTTPException(status_code=400, detail="指数分钟K不支持落库同步")

    if not _minute_allowed(capset):
        raise HTTPException(status_code=403, detail="需要 Pro+ 权限")

    days = requested_days if requested_days is not None else get_minute_sync_days()
    loop = asyncio.get_event_loop()

    def _run():
        return kline_sync.sync_and_persist_minute([symbol], repo, capset, days=days, force_full_days=True)

    written = await loop.run_in_executor(_long_task_executor, _run)

    # 刷新视图
    from app.jobs.daily_pipeline import _refresh_single_view
    _refresh_single_view(repo, "kline_minute")

    return {"status": "ok", "symbol": symbol, "rows": written}


# 长时间任务专用线程池（隔离于 FastAPI 默认线程池，防止阻塞请求处理）
import concurrent.futures as _cf
_long_task_executor = _cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="long-task")
