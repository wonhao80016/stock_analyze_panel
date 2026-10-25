"""策略 API 路由 — HTTP 请求 → 调用策略模块 → 返回响应。

只做胶水，不含业务逻辑。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.backtest.minute_trigger import MINUTE_EXIT_TRIGGER_SIGNALS
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyDef, StrategyEngine
from app.strategy.scoring import (
    effective_scoring,
    effective_scoring_directions,
)

router = APIRouter(prefix="/api/strategies", tags=["strategies"])

USER_STRATEGIES_LOCAL_ONLY_DETAIL = "保存用户 Python 策略只接受本机请求 (127.0.0.1 / localhost)。"

# ── Helpers ──────────────────────────────────────────────────────────


def _get_engine(request: Request) -> StrategyEngine:
    engine = getattr(request.app.state, "strategy_engine", None)
    if not engine:
        raise HTTPException(status_code=503, detail="策略引擎未初始化")
    return engine


def _data_dir(request: Request) -> Path:
    return request.app.state.repo.store.data_dir


def _child_meta(engine: StrategyEngine | None, child_id: str) -> dict:
    """查询子策略的可读名称与来源; engine 缺失或子策略不存在时回退为 id/unknown。"""
    if engine is None:
        return {"name": child_id, "source": "unknown"}
    try:
        child = engine.get(child_id)
    except ValueError:
        return {"name": child_id, "source": "unknown"}
    return {"name": str(child.meta.get("name") or child_id), "source": child.source}


def _strategy_detail(
    s: StrategyDef,
    overrides: dict | None = None,
    engine: StrategyEngine | None = None,
) -> dict:
    """策略详情（含用户覆盖）"""
    bf = {**s.basic_filter}
    scoring = effective_scoring(s.meta.get("scoring"), overrides)
    scoring_directions = effective_scoring_directions(
        overrides, defaults=s.meta.get("scoring_directions")
    )
    params_defaults = {p["id"]: p["default"] for p in s.meta.get("params", [])}

    if overrides:
        if overrides.get("basic_filter"):
            bf.update(overrides["basic_filter"])
        # 用户保存的参数覆盖默认值: 合并进 params_defaults, 前端据此回显
        if overrides.get("params"):
            params_defaults.update(overrides["params"])

    # 名称/描述可被用户覆盖
    name = overrides.get("name", s.meta.get("name", "")) if overrides else s.meta.get("name", "")
    description = overrides.get("description", s.meta.get("description", "")) if overrides else s.meta.get("description", "")

    def effective(key: str, default):
        """The value a run would actually use.

        backtest/strategy.py resolves every risk field through the same override
        lookup, so reporting the module constant here would describe a run that
        does not happen.
        """
        if overrides and key in overrides:
            return overrides[key]
        return default

    return {
        "id": s.meta["id"],
        "name": name or s.meta.get("name", ""),
        "description": description or s.meta.get("description", ""),
        "tags": s.meta.get("tags", []),
        "source": s.source,
        "execution_backend": s.execution_backend,
        "asset_types": s.meta.get("asset_types", ["stock"]),
        "timeframes": s.meta.get("timeframes", ["1d"]),
        "version": s.meta.get("version", "1.0.0"),
        "basic_filter": bf,
        "params": s.meta.get("params", []),
        "params_defaults": params_defaults,
        "scoring": scoring,
        "scoring_directions": {
            name: direction
            for name, direction in scoring_directions.items()
            if name in scoring
        },
        "entry_signals": overrides.get("entry_signals", s.entry_signals) if overrides else s.entry_signals,
        "exit_signals": overrides.get("exit_signals", s.exit_signals) if overrides else s.exit_signals,
        "minute_exit_trigger_supported_signals": sorted(MINUTE_EXIT_TRIGGER_SIGNALS),
        "stop_loss": effective("stop_loss", s.stop_loss),
        "take_profit": effective("take_profit", getattr(s, "take_profit", None)),
        "trailing_stop": effective("trailing_stop", getattr(s, "trailing_stop", None)),
        "trailing_take_profit_activate": effective(
            "trailing_take_profit_activate", getattr(s, "trailing_take_profit_activate", None)
        ),
        "trailing_take_profit_drawdown": effective(
            "trailing_take_profit_drawdown", getattr(s, "trailing_take_profit_drawdown", None)
        ),
        "max_hold_days": effective("max_hold_days", s.max_hold_days),
        "order_by": s.meta.get("order_by", "score"),
        "descending": s.meta.get("descending", True),
        "limit": s.meta.get("limit", 30),
        "display_limit": overrides.get("display_limit") if overrides and "display_limit" in overrides else None,
        # 叠加策略: 子策略列表与权重(供前端展示)。override.children 可覆盖 META 固化值。
        "composite_children": (
            [
                {
                    "id": c["strategy_id"],
                    **_child_meta(engine, c["strategy_id"]),
                    "weight": c.get("weight", 1.0),
                }
                for c in (overrides.get("children") if overrides and isinstance(overrides.get("children"), list) else s.meta.get("children", []))
            ]
            if s.execution_backend == "composite"
            else None
        ),
    }



# ── 列表 / 详情 ─────────────────────────────────────────────────────


@router.get("")
def list_strategies(
    request: Request,
    asset_type: str | None = None,
    timeframe: str | None = None,
):
    engine = _get_engine(request)
    data_dir = _data_dir(request)
    all_overrides = strategy_config.list_overrides(data_dir)

    result = []
    for s in engine.strategy_definitions():
        meta = s.meta
        if meta.get("research_only"):
            continue
        if asset_type and asset_type not in meta.get("asset_types", ["stock"]):
            continue
        if timeframe and timeframe not in meta.get("timeframes", ["1d"]):
            continue
        overrides = all_overrides.get(meta["id"])
        result.append(_strategy_detail(s, overrides, engine))
    return {"strategies": result, "load_errors": engine.load_errors()}


# ── AI ──────────────────────────────────────────────────────────────


@router.get("/ai/status")
def ai_status(request: Request):
    """Check whether the selected AI provider is configured."""
    from app import secrets_store
    from app.services.ai_provider import ai_configured, current_ai_model, current_ai_provider

    has_key = bool(secrets_store.get_ai_key())
    model = current_ai_model()
    provider = current_ai_provider()
    return {
        "configured": ai_configured(provider) and bool(model or provider == "codex_cli"),
        "has_key": has_key,
        "has_model": bool(model),
        "provider": provider,
    }


@router.post("/ai/test")
async def ai_test(request: Request):
    """Send a small prompt through the selected AI provider."""
    from app.services.ai_provider import current_ai_model, current_ai_provider, generate_ai_text

    try:
        text = await generate_ai_text(
            [{"role": "user", "content": "Reply exactly: OK"}],
            temperature=0,
            max_tokens=8,
            timeout=15,
        )
        return {"ok": True, "model": current_ai_model() or current_ai_provider(), "response": text[:80]}
    except Exception as e:
        return {
            "ok": False,
            "error": str(e) or repr(e),
            "error_type": type(e).__name__,
        }
