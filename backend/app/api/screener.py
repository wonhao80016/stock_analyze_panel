"""Screener API。"""
from __future__ import annotations

import glob as _glob
import json
import logging
import math
import os
import re
import time
from dataclasses import asdict
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.db_safe import is_valid_ext_ident, quote_ident
from app.services import (
    pick_grades,
    strategy_cache,
    strategy_pick_backfill,
    strategy_pick_history,
    strategy_pick_performance,
)
from app.services.screener import ScreenerService
from app.strategy import config as strategy_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screener", tags=["screener"])


class PresetRequest(BaseModel):
    strategy_id: str
    pool: Optional[list[str]] = None
    as_of: Optional[date] = None
    ext_columns: Optional[str] = None
    asset_type: str = "stock"
    timeframe: str = "1d"


def _safe(result_dict: dict) -> dict:
    """sanitize for JSON(NaN / Inf → None)."""
    rows = result_dict.get("rows", [])
    for r in rows:
        for k, v in list(r.items()):
            if isinstance(v, float) and not math.isfinite(v):
                r[k] = None
    return result_dict


def _safe_ext_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


# 标识符安全原语 (转义 + 白名单) 集中在 app.db_safe, 见 Issue #150 注入防护。


# ── 扩展列 value_map 缓存 ────────────────────────────────────────────
# 每次请求 _load_ext_value_maps 都会重新从磁盘读 ext parquet 并重建 {symbol: value}。
# 用底层 parquet 文件的 (路径, mtime) 签名做 memoize: 文件未变则复用上次的 map,
# parquet 被重写 (mtime 变化) 时自动失效重算。仅缓存基于 config 的快照/时序路径,
# 无 config 的 DuckDB view 回退路径不缓存 (少见)。
_ext_value_map_cache: dict[tuple[str, str], tuple[Any, dict[str, Any]]] = {}


def _ext_parquet_signature(cfg, data_dir) -> Optional[tuple]:
    """该扩展配置底层 parquet 文件的 (路径, mtime) 签名; 出错返回 None (禁用缓存)。"""
    try:
        from app.api.ext_data import _parquet_glob
        pattern = _parquet_glob(cfg, data_dir)
        files = sorted(_glob.glob(pattern, recursive=True))
        if not files:
            return None
        return tuple((f, os.path.getmtime(f)) for f in files)
    except Exception:  # noqa: BLE001
        return None


def _load_ext_value_maps(repo, ext_columns: Optional[str]) -> dict[str, dict[str, Any]]:
    """按请求加载扩展列，返回 {输出列名: {symbol: value}}。

    策略结果缓存是共享文件，不能被不同 ext_columns 组合污染；因此扩展列只在
    返回前通过该投影映射追加到结果副本中。

    基于 config 的路径按 parquet 文件 mtime 签名 memoize, 文件未变时跳过磁盘重读。
    """
    ext_specs = _parse_ext_columns(ext_columns) if ext_columns else []
    if not ext_specs:
        return {}

    import polars as pl

    from app.api.ext_data import _read_ext_dataframe
    from app.services.ext_data import ExtConfigStore

    db = repo.store.db
    data_dir = repo.store.data_dir
    ext_store = ExtConfigStore(data_dir)
    configs = {c.id: c for c in ext_store.load_all()}
    value_maps: dict[str, dict[str, Any]] = {}

    for config_id, field_name in ext_specs:
        out_col = f"{config_id}__{field_name}"
        cfg = configs.get(config_id)
        cache_key = (config_id, field_name)
        sig = _ext_parquet_signature(cfg, data_dir) if cfg else None
        try:
            if cfg:
                # 命中缓存 (文件签名一致) → 复用, 免去磁盘重读
                cached = _ext_value_map_cache.get(cache_key)
                if cached is not None and sig is not None and cached[0] == sig:
                    value_maps[out_col] = cached[1]
                    continue
                # 时序扩展表只取最新分区，避免历史分区把同一 symbol JOIN 放大。
                ext_df, _ = _read_ext_dataframe(cfg, data_dir)
            else:
                view_name = f"ext_{config_id}"
                ext_df = pl.from_arrow(db.query(
                    f"SELECT symbol, {quote_ident(field_name)} FROM {view_name}"
                ).arrow())

            if ext_df.is_empty() or "symbol" not in ext_df.columns or field_name not in ext_df.columns:
                continue

            ext_df = ext_df.select(["symbol", field_name]).unique(subset=["symbol"], keep="last")
            vmap = {
                str(row["symbol"]): _safe_ext_value(row.get(field_name))
                for row in ext_df.to_dicts()
                if row.get("symbol")
            }
            value_maps[out_col] = vmap
            if cfg and sig is not None:
                _ext_value_map_cache[cache_key] = (sig, vmap)
        except Exception as e:  # noqa: BLE001
            logger.debug("screener ext column join skipped for %s.%s: %s", config_id, field_name, e)

    return value_maps


def _row_with_ext(row: dict, ext_values: dict[str, dict[str, Any]], symbol: Optional[str] = None) -> dict:
    next_row = dict(row)
    sym = symbol or next_row.get("symbol")
    for out_col, value_map in ext_values.items():
        next_row[out_col] = value_map.get(str(sym)) if sym else None
    return next_row


def _rows_with_ext(rows: list[dict], ext_values: dict[str, dict[str, Any]]) -> list[dict]:
    if not ext_values:
        return rows
    return [_row_with_ext(r, ext_values) for r in rows]


def _result_with_ext(result_dict: dict, ext_values: dict[str, dict[str, Any]]) -> dict:
    if not ext_values:
        return result_dict
    return {**result_dict, "rows": _rows_with_ext(result_dict.get("rows", []), ext_values)}


def _results_with_ext(results: dict[str, dict], ext_values: dict[str, dict[str, Any]]) -> dict[str, dict]:
    if not ext_values:
        return results
    return {sid: _result_with_ext(r, ext_values) for sid, r in results.items()}


def _cache_payload_with_ext(cached: dict, ext_values: dict[str, dict[str, Any]]) -> dict:
    if not ext_values:
        return cached

    payload = dict(cached)
    payload["results"] = _results_with_ext(cached.get("results", {}), ext_values)

    ever_rows = cached.get("today_ever_rows")
    if isinstance(ever_rows, dict):
        enriched_ever: dict[str, dict[str, dict]] = {}
        for sid, sym_map in ever_rows.items():
            if not isinstance(sym_map, dict):
                continue
            enriched_ever[sid] = {
                sym: _row_with_ext(row, ext_values, symbol=sym)
                for sym, row in sym_map.items()
                if isinstance(row, dict)
            }
        payload["today_ever_rows"] = enriched_ever

    return payload


def _update_cache_strategy(data_dir, as_of: str, strategy_id: str, safe_data: dict) -> None:
    """单跑后更新缓存中该策略的结果，保持缓存与最新计算一致。"""
    from app.services import strategy_cache
    cached = strategy_cache.read_cache(data_dir)
    if cached and cached.get("as_of") == as_of:
        results = cached.get("results", {})
        results[strategy_id] = {
            "total": safe_data.get("total", 0),
            "as_of": as_of,
            "rows": safe_data.get("rows", []),
        }
        strategy_cache.write_cache(data_dir, as_of, results)


@router.get("/strategies")
def strategies(
    request: Request,
    asset_type: str = Query("stock"),
    timeframe: str = Query("1d"),
):
    """兼容策略清单端点；唯一数据源为 StrategyEngine。"""
    data_dir = request.app.state.repo.store.data_dir
    engine = getattr(request.app.state, "strategy_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="策略引擎未初始化")
    presets = []
    for meta in engine.list_strategies():
        if meta.get("research_only"):
            continue
        if asset_type not in meta.get("asset_types", ["stock"]):
            continue
        if timeframe not in meta.get("timeframes", ["1d"]):
            continue
        sid = meta["id"]
        overrides = strategy_config.load_override(data_dir, sid)
        presets.append({
            **meta,
            "name": overrides.get("name") or meta["name"],
            "description": overrides.get("description") or meta.get("description", ""),
        })

    return {"presets": presets, "load_errors": engine.load_errors()}


@router.post("/run_preset")
def run_preset(req: PresetRequest, request: Request):
    repo = request.app.state.repo
    svc = ScreenerService(repo, asset_type=req.asset_type)
    as_of = req.as_of or svc.latest_date()
    if not as_of:
        raise HTTPException(status_code=400, detail="无可用数据日期")

    # 加载用户保存的策略配置
    data_dir = request.app.state.repo.store.data_dir
    ext_values = _load_ext_value_maps(repo, req.ext_columns)
    overrides = strategy_config.load_override(data_dir, req.strategy_id)
    engine = getattr(request.app.state, "strategy_engine", None)
    if not engine:
        raise HTTPException(status_code=404, detail=f"策略引擎未初始化或策略 {req.strategy_id} 不存在")

    try:
        if not engine.has(req.strategy_id):
            raise ValueError(f"unknown strategy: {req.strategy_id}")
        if engine.get(req.strategy_id).meta.get("research_only"):
            raise ValueError(f"unknown strategy: {req.strategy_id}")
        params = dict(overrides.get("params") or {})
        context = svc.build_strategy_context(
            engine,
            as_of,
            [req.strategy_id],
            timeframe=req.timeframe,
            params_map={req.strategy_id: params},
            overrides_map={req.strategy_id: overrides or {}},
        )
        result = engine.run(
            req.strategy_id,
            context,
            pool=req.pool,
            params=params,
            overrides=overrides or None,
        )
    except ValueError as e:
        status_code = 404 if "unknown strategy" in str(e) else 400
        raise HTTPException(status_code=status_code, detail=str(e)) from e

    safe_data = _safe(asdict(result))
    _update_cache_strategy(data_dir, str(as_of), req.strategy_id, safe_data)

    return _result_with_ext(safe_data, ext_values)


def _realtime_overrides(cached: dict, realtime_results: dict[str, dict]) -> dict[str, dict]:
    """Pick the monitor results that are newer than the post-close cache entry of the same strategy.

    Monitor results cover only the polling pool, so a post-close result of the same or a later
    session must win; a strategy the cache has no result for is filled only from the same or a later session.
    """
    cached_results = cached.get("results") or {}
    cached_as_of = cached.get("as_of")
    picked: dict[str, dict] = {}
    for sid, result in realtime_results.items():
        realtime_as_of = result.get("as_of") if isinstance(result, dict) else None
        cached_result = cached_results.get(sid)
        if isinstance(cached_result, dict):
            post_close_as_of = cached_result.get("as_of") or cached_as_of
            newer = isinstance(realtime_as_of, str) and (
                not isinstance(post_close_as_of, str) or realtime_as_of > post_close_as_of
            )
        else:
            newer = not isinstance(cached_as_of, str) or (
                isinstance(realtime_as_of, str) and realtime_as_of >= cached_as_of
            )
        if newer:
            picked[sid] = result
    return picked


def _cached_with_realtime(request: Request) -> dict:
    """读取盘后缓存，并用监控引擎较新交易日的实时结果覆盖同策略。"""
    data_dir = request.app.state.repo.store.data_dir
    return _overlay_realtime(request, strategy_cache.read_cache(data_dir))


def _overlay_realtime(request: Request, cached: dict | None) -> dict:
    if cached is None:
        cached = {"as_of": None, "results": {}, "updated_at": None}

    monitor_engine = getattr(request.app.state, "monitor_engine", None)
    if monitor_engine is not None:
        realtime_results = _realtime_overrides(cached, monitor_engine.latest_strategy_results() or {})
        if realtime_results:
            results = dict(cached.get("results") or {})
            results.update(realtime_results)
            cached = dict(cached)
            cached["results"] = results
            if cached.get("failures"):
                cached["failures"] = {
                    sid: failure
                    for sid, failure in cached["failures"].items()
                    if sid not in realtime_results
                }
            # 有实时数据时, 以最新时间戳为准
            import time as _time
            cached["updated_at"] = int(_time.time() * 1000)

    return cached


def _attach_instrument_names(repo, payload: dict) -> dict:
    """Give every cached row its instrument name.

    The rows are enriched frames, which carry no name, and data/instruments is
    empty on a Taiwan-only install, so the security master is the only source
    that resolves a TWSE/TPEX symbol. Resolved here rather than at write time so
    caches already on disk gain the name without being recomputed.
    """
    results = payload.get("results") or {}
    symbols = {
        r.get("symbol")
        for result in results.values()
        for r in (result.get("rows") or [])
        if r.get("symbol")
    }
    if not symbols:
        return payload

    names: dict[str, str] = {}
    try:
        from app.taiwan.universe import get_security_master

        # Per-symbol dict lookup, not to_dataframe(): the master holds a few thousand
        # instruments and rebuilds the frame on every call, which costs far more than
        # the handful of symbols a pick list actually asks about.
        master = get_security_master()
        for sym in symbols:
            inst = master.get_instrument(sym)
            if inst is not None and inst.name:
                names[sym] = inst.name
    except Exception as e:  # noqa: BLE001
        logger.debug("security master name lookup failed: %s", e)

    missing = symbols - names.keys()
    if missing and repo is not None:
        try:
            import polars as pl

            inst = repo.get_instruments()
            if not inst.is_empty() and {"symbol", "name"} <= set(inst.columns):
                hit = inst.filter(pl.col("symbol").is_in(list(missing)))
                names.update(
                    {row["symbol"]: row["name"]
                     for row in hit.select(["symbol", "name"]).iter_rows(named=True)
                     if row["name"]}
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("instruments name lookup failed: %s", e)

    if not names:
        return payload
    for result in results.values():
        for r in result.get("rows") or []:
            name = names.get(r.get("symbol"))
            if name:
                r["name"] = name
    return payload


@router.get("/cached")
def get_cached(
    request: Request,
    ext_columns: Optional[str] = Query(None, description="逗号分隔: config_id.field_name"),
):
    """读取策略结果缓存, 并叠加监控引擎本轮实时算出的结果。

    - 盘后缓存 (strategy_cache.json): 非监控策略 / 页面秒加载用, run_all 写入。
    - 监控引擎内存结果 (latest_strategy_results): 实时行情每轮对「加入监控的策略」算出,
      不落盘 (避免与 read_cache 的 mtime 校验冲突), 在此直接叠加覆盖盘后结果。
      被监控的策略拿到新鲜数据, 非监控策略仍用盘后缓存。
    """
    cached = _cached_with_realtime(request)
    engine = getattr(request.app.state, "strategy_engine", None)
    data_dir = request.app.state.repo.store.data_dir

    # 无任何数据 (盘后缓存空 + 无实时结果) → 返回空标记, 前端据此提示
    if not cached.get("results") and cached.get("as_of") is None:
        empty = {"as_of": None, "results": {}, "updated_at": None}
        return pick_grades.grade_cached_payload(empty, engine, data_dir)

    ext_values = _load_ext_value_maps(request.app.state.repo, ext_columns)
    payload = _cache_payload_with_ext(cached, ext_values)
    # Rows are shared with the monitor engine and the cache reader; name them on copies.
    payload = {
        **payload,
        "results": {
            sid: {**result, "rows": [dict(row) for row in result.get("rows") or []]}
            if isinstance(result, dict) else result
            for sid, result in (payload.get("results") or {}).items()
        },
    }
    payload = _attach_instrument_names(request.app.state.repo, payload)
    return pick_grades.grade_cached_payload(payload, engine, data_dir)


@router.get("/cached-summary")
def get_cached_summary(request: Request):
    """返回策略卡片所需的轻量摘要，不序列化股票明细。"""
    on_disk = strategy_cache.read_cache(request.app.state.repo.store.data_dir)
    cached = _overlay_realtime(request, on_disk)
    results = cached.get("results") or {}
    summary = {
        sid: {
            "total": int(result.get("total") or 0),
            "as_of": result.get("as_of"),
        }
        for sid, result in results.items()
        if isinstance(result, dict)
    }

    cached_as_of = cached.get("as_of")
    ever_rows = cached.get("today_ever_rows") or {}
    ever_counts = {}
    for sid, result in results.items():
        if not isinstance(result, dict) or result.get("as_of") != cached_as_of:
            continue
        current_symbols = {
            str(row["symbol"])
            for row in result.get("rows") or []
            if isinstance(row, dict) and row.get("symbol")
        }
        ever_counts[sid] = len(set((ever_rows.get(sid) or {}).keys()) | current_symbols)
    return {
        "as_of": cached_as_of,
        "results": summary,
        "today_ever_counts": ever_counts,
        "updated_at": cached.get("updated_at"),
        # Changes whenever the cache file is rewritten: the scheduled post-close runs, run_all and
        # single-strategy runs. Realtime overlays restamp updated_at on every read but never this.
        "cache_updated_at": on_disk.get("updated_at") if on_disk else None,
    }


@router.get("/cached-result/{strategy_id}")
def get_cached_result(
    strategy_id: str,
    request: Request,
    ext_columns: Optional[str] = Query(None, description="逗号分隔: config_id.field_name"),
):
    """按需返回单个策略的完整明细及其今日失效行。"""
    cached = _cached_with_realtime(request)
    raw_result = (cached.get("results") or {}).get(strategy_id)
    if not isinstance(raw_result, dict):
        return {
            "result": None,
            "today_ever_rows": None,
            "strategy_ids_by_symbol": {},
            "updated_at": cached.get("updated_at"),
        }

    ext_values = _load_ext_value_maps(request.app.state.repo, ext_columns)
    result = {
        "as_of": raw_result.get("as_of"),
        "strategy": strategy_id,
        "rows": _rows_with_ext(raw_result.get("rows") or [], ext_values),
        "total": int(raw_result.get("total") or 0),
        "elapsed_ms": 0.0,
    }

    ever_rows = None
    if cached.get("as_of") == result["as_of"]:
        strategy_ever_rows = (cached.get("today_ever_rows") or {}).get(strategy_id)
        if isinstance(strategy_ever_rows, dict):
            ever_rows = {
                symbol: _row_with_ext(row, ext_values, symbol=symbol)
                for symbol, row in strategy_ever_rows.items()
                if isinstance(row, dict)
            }

    selected_symbols = {
        str(row["symbol"])
        for row in raw_result.get("rows") or []
        if isinstance(row, dict) and row.get("symbol")
    }
    strategy_ids_by_symbol: dict[str, list[str]] = {symbol: [] for symbol in selected_symbols}
    for sid, cached_result in (cached.get("results") or {}).items():
        if not isinstance(cached_result, dict) or cached_result.get("as_of") != result["as_of"]:
            continue
        for row in cached_result.get("rows") or []:
            symbol = str(row.get("symbol")) if isinstance(row, dict) and row.get("symbol") else None
            if symbol in strategy_ids_by_symbol:
                strategy_ids_by_symbol[symbol].append(sid)

    return {
        "result": result,
        "today_ever_rows": ever_rows,
        "strategy_ids_by_symbol": strategy_ids_by_symbol,
        "updated_at": cached.get("updated_at"),
    }


PICK_HISTORY_UNREADABLE = "pick_history_unreadable"
PICK_PRICES_UNREADABLE = "pick_prices_unreadable"


def _pick_limit_profile(symbols: list[str]):
    """Statutory limit per symbol; a lookup failure leaves every limit_check unknown instead of failing."""
    import polars as pl

    from app.indicators.pipeline import taiwan_limit_profile

    try:
        return taiwan_limit_profile(symbols)
    except Exception as e:
        logger.warning("pick performance: limit profile lookup failed: %s", e)
        return pl.DataFrame(
            {"symbol": symbols, "limit_pct": [None] * len(symbols), "is_etf_tick": [False] * len(symbols)},
            schema={"symbol": pl.Utf8, "limit_pct": pl.Float64, "is_etf_tick": pl.Boolean},
        )


def _pick_outcomes(request: Request, strategy_id: str | None = None):
    import polars as pl

    data_dir = request.app.state.repo.store.data_dir
    try:
        snapshots = strategy_pick_history.read_picks(data_dir)
        picks, runs = snapshots.picks, snapshots.runs
    except (strategy_pick_history.PickHistoryReadError, OSError) as e:
        logger.warning("pick performance: snapshot read failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail={"code": PICK_HISTORY_UNREADABLE, "message": "Strategy pick snapshots could not be read."},
        ) from e
    if strategy_id is not None:
        picks = picks.filter(pl.col("strategy_id") == strategy_id)
        runs = runs.filter(pl.col("strategy_id") == strategy_id)
    try:
        inputs = strategy_pick_performance.load_inputs(data_dir, picks, _pick_limit_profile)
    except OSError as e:
        logger.warning("pick performance: price inputs read failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail={"code": PICK_PRICES_UNREADABLE, "message": "Daily prices could not be read."},
        ) from e
    outcomes = strategy_pick_performance.compute_outcomes(
        picks, inputs.bars, inputs.events, inputs.adjustment, inputs.calendar, inputs.limit_profile
    )
    return outcomes, inputs, runs


def _finite(value: Any) -> Any:
    return None if isinstance(value, float) and not math.isfinite(value) else value


def _iso_or_none(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


@router.get("/pick-performance")
def get_pick_performance(request: Request):
    """Gross decimal returns of recorded picks per strategy; computed on every request."""
    outcomes, inputs, runs = _pick_outcomes(request)
    payload = strategy_pick_performance.summarize(outcomes, inputs.adjustment, inputs.calendar.prices_through, runs)
    for stats in payload["strategies"]:
        for horizon in stats["horizons"].values():
            for name in ("mean_return", "median_return", "positive_ratio"):
                horizon[name] = _finite(horizon[name])
    return payload


class PickBackfillRequest(BaseModel):
    days: int = Field(default=5, ge=1, le=250)


@router.post("/pick-performance/backfill", status_code=202)
def start_pick_backfill(body: PickBackfillRequest, request: Request):
    engine = getattr(request.app.state, "strategy_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Strategy engine is unavailable")
    try:
        return strategy_pick_backfill.start(request.app.state.repo, engine, body.days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/pick-performance/backfill/{job_id}")
def get_pick_backfill(job_id: str, request: Request):
    job = strategy_pick_backfill.get(request.app.state.repo.store.data_dir, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Historical reconstruction job was not found")
    return job


@router.get("/pick-performance/{strategy_id}")
def get_pick_performance_detail(
    strategy_id: str,
    request: Request,
    limit: int = Query(200, ge=1, le=2000),
):
    """Most recent picks of one strategy, newest signal day first; an unknown id yields no rows."""
    outcomes, inputs, runs = _pick_outcomes(request, strategy_id)
    horizons = strategy_pick_performance.HORIZONS
    latest = outcomes.sort(["signal_date", "rank"], descending=[True, False]).head(limit)
    rows = []
    for row in latest.iter_rows(named=True):
        rows.append({
            "signal_date": _iso_or_none(row["signal_date"]),
            "symbol": row["symbol"],
            "name": None,
            "rank": row["rank"],
            "entry_date": _iso_or_none(row["entry_date"]),
            "entry_price_raw": _finite(row["entry_price_raw"]),
            "status": row["status"],
            "limit_check": row["limit_check"],
            "absent_since": _iso_or_none(row["absent_since"]),
            "returns": {
                str(k): {
                    "value": _finite(row[f"ret_{k}"]),
                    "reason": row[f"reason_{k}"],
                    "stale_exit": bool(row[f"stale_{k}"]),
                    "stale_days": int(row[f"stale_days_{k}"] or 0),
                }
                for k in horizons
            },
            "excluded_reason": row["excluded_reason"],
            "recorded_late": row["recorded_late"],
            "manifest_missing": row["manifest_missing"],
            "universe_complete": row["universe_complete"],
            "history_degraded": row["history_degraded"],
            "provenance": row["provenance"],
            "calculation_status": row["calculation_status"],
            "missing_fields": json.loads(row["missing_fields"] or "[]"),
        })
    _attach_instrument_names(request.app.state.repo, {"results": {strategy_id: {"rows": rows}}})
    header = strategy_pick_performance.summarize(
        outcomes.clear(), inputs.adjustment, inputs.calendar.prices_through, runs
    )
    return {
        "strategy_id": strategy_id,
        "method": header["method"],
        "adjustment": header["adjustment"],
        "prices_through": header["prices_through"],
        "total": outcomes.height,
        "signal_days": outcomes.get_column("signal_date").n_unique(),
        "calculation_issues": header["calculation_issues"],
        "rows": rows,
    }


@router.get("/market-snapshot")
def market_snapshot(request: Request):
    """最新全市场轻量行情快照，供板块/概念聚合分析使用。"""
    import polars as pl

    repo = request.app.state.repo
    svc = ScreenerService(repo)
    as_of = svc.latest_date()
    if not as_of:
        return {"as_of": None, "rows": []}

    df = svc._load_enriched_for_date(as_of)
    if df.is_empty():
        return {"as_of": str(as_of), "rows": []}

    if "close" in df.columns and "total_shares" in df.columns and "market_cap" not in df.columns:
        df = df.with_columns((pl.col("close") * pl.col("total_shares")).alias("market_cap"))
    if "close" in df.columns and "float_shares" in df.columns and "float_market_cap" not in df.columns:
        df = df.with_columns((pl.col("close") * pl.col("float_shares")).alias("float_market_cap"))

    cols = [
        "symbol", "name", "close", "change_pct", "amount", "volume",
        "turnover_rate", "vol_ratio_5d", "total_shares", "float_shares",
        "market_cap", "float_market_cap", "consecutive_limit_ups",
    ]
    df = df.select([c for c in cols if c in df.columns])
    rows = df.to_dicts()
    for r in rows:
        for k, v in list(r.items()):
            if isinstance(v, float) and not math.isfinite(v):
                r[k] = None

    return {"as_of": str(as_of), "rows": rows}


class UnknownStrategiesError(LookupError):
    """Raised when explicitly requested strategy ids are absent or research-only."""


_FAILURE_MESSAGE_LIMIT = 500


def compute_and_cache_all(
    repo,
    engine,
    *,
    asset_type: str = "stock",
    timeframe: str = "1d",
    as_of: date | None = None,
    strategy_ids: list[str] | None = None,
    run_report: strategy_pick_history.RunReport | None = None,
) -> tuple[date | None, dict[str, dict]]:
    """Run every eligible strategy for ``as_of`` and persist the cache GET /cached reads.

    Callers must not swallow a write failure; ``run_report``, when given, is filled in place.
    """
    t_total = time.perf_counter()
    svc = ScreenerService(repo, asset_type=asset_type)
    if as_of is None:
        as_of = svc.latest_date()
    if not as_of:
        return None, {}

    data_dir = repo.store.data_dir

    if strategy_ids:
        all_ids = [str(sid) for sid in strategy_ids]
        unknown = [
            sid
            for sid in all_ids
            if not engine.has(sid) or engine.get(sid).meta.get("research_only")
        ]
        if unknown:
            raise UnknownStrategiesError(f"unknown strategies: {unknown}")
    else:
        all_ids = [
            meta["id"]
            for meta in engine.list_strategies()
            if not meta.get("research_only")
            and asset_type in meta.get("asset_types", ["stock"])
            and timeframe in meta.get("timeframes", ["1d"])
        ]

    if not all_ids:
        return as_of, {}

    t0 = time.perf_counter()
    all_overrides = strategy_config.list_overrides(data_dir)
    logger.info("run_all: list_overrides took %.1fms (%d overrides)", (time.perf_counter() - t0) * 1000, len(all_overrides))
    if run_report is not None:
        # Same overrides snapshot the strategies run with, so fingerprints describe this run.
        run_report.fingerprints = strategy_pick_history.strategy_fingerprints(engine, all_overrides, all_ids)
        run_report.input_groups = strategy_pick_history.strategy_input_groups(engine, all_ids)

    params_map = {
        sid: dict((all_overrides.get(sid) or {}).get("params") or {})
        for sid in all_ids
    }
    overrides_map = {sid: all_overrides.get(sid, {}) for sid in all_ids}
    context = svc.build_strategy_context(
        engine,
        as_of,
        all_ids,
        timeframe=timeframe,
        params_map=params_map,
        overrides_map=overrides_map,
    )
    if run_report is not None:
        input_status = getattr(context, "input_status", None) or {}
        run_report.current = input_status.get(strategy_pick_history.CURRENT) or strategy_pick_history.InputStatus()
        run_report.history = input_status.get(strategy_pick_history.HISTORY)
    # One strategy that cannot run must not cost the page every other one: the
    # post-close refresh writes this cache unattended, so a single raise would
    # freeze the card on whatever day it last succeeded.
    skipped: dict[str, str] = {}
    engine_results = engine.run_all(
        context,
        params_map=params_map,
        overrides_map=overrides_map,
        strategy_ids=all_ids,
        skip_failed=True,
        failures=skipped,
    )
    failures = {
        sid: {"error": message[:_FAILURE_MESSAGE_LIMIT]} for sid, message in skipped.items()
    }

    results: dict[str, dict] = {}
    for sid, result in engine_results.items():
        safe_rows = _safe(asdict(result)).get("rows", [])
        results[sid] = {
            "total": result.total,
            "as_of": str(as_of),
            "rows": safe_rows,
        }

    elapsed = (time.perf_counter() - t_total) * 1000
    logger.info("run_all: total took %.1fms (%d strategies)", elapsed, len(all_ids))

    if results or failures:
        try:
            strategy_cache.write_cache(data_dir, str(as_of), results, failures)
        except Exception:
            logger.exception("write strategy cache failed for %s", as_of)

    return as_of, results


@router.post("/run_all")
def run_all(request: Request, body: Optional[dict] = None):
    """批量运行指定策略；注册、路由和执行均由 StrategyEngine 负责。"""
    from datetime import date as date_type

    body = body or {}
    repo = request.app.state.repo
    asset_type = str(body.get("asset_type") or "stock")
    timeframe = str(body.get("timeframe") or "1d")
    engine = getattr(request.app.state, "strategy_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="策略引擎未初始化")

    raw_date = body.get("as_of")
    requested_as_of: date | None = None
    if raw_date:
        requested_as_of = (
            date_type.fromisoformat(str(raw_date)) if isinstance(raw_date, str) else raw_date
        )

    requested_ids = body.get("strategy_ids")
    strategy_ids = (
        [str(sid) for sid in requested_ids]
        if requested_ids and isinstance(requested_ids, list)
        else None
    )

    try:
        as_of, results = compute_and_cache_all(
            repo,
            engine,
            asset_type=asset_type,
            timeframe=timeframe,
            as_of=requested_as_of,
            strategy_ids=strategy_ids,
        )
    except UnknownStrategiesError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if as_of is None:
        return {"as_of": None, "results": {}}
    if not results:
        return {"as_of": str(as_of), "results": {}}

    if body.get("summary_only"):
        return {
            "as_of": str(as_of),
            "results": {
                sid: {"total": result["total"], "as_of": result["as_of"]}
                for sid, result in results.items()
            },
        }

    ext_values = _load_ext_value_maps(repo, body.get("ext_columns"))
    return {"as_of": str(as_of), "results": _results_with_ext(results, ext_values)}


def _parse_ext_columns(ext_columns: str) -> list[tuple[str, str]]:
    """解析 'config_id1.field1,config_id2.field2' 为 [(config_id, field_name), ...]。"""
    result = []
    for part in ext_columns.split(","):
        part = part.strip()
        if "." not in part:
            continue
        config_id, field_name = part.split(".", 1)
        config_id = config_id.strip()
        field_name = field_name.strip()
        if not config_id or not field_name:
            continue
        if not is_valid_ext_ident(config_id) or "\x00" in field_name:
            continue
        result.append((config_id, field_name))
    return result
