"""Backtest API: strategy backtests."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import settings

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

FACTOR_DEFAULT_DAYS = 180
BACKTEST_MAX_SERVER_DAYS = 186
BACKTEST_SERVER_GUARD_MESSAGE = (
    "当前服务器内存约 1.8GB，回测区间最多支持 6 个月；"
    "更长周期容易触发 OOM，建议在 8GB 以上内存环境或本机运行。"
)


def _resolve_start(req: BaseModel, end: date, default_days: int) -> date:
    """未传 start 使用默认区间；显式传 null/空值表示全部历史。"""
    start = getattr(req, "start")
    if start is not None:
        return start
    if "start" in req.model_fields_set:
        return date(1900, 1, 1)
    return end - timedelta(days=default_days)


def _guard_server_backtest_range(start: date, end: date):
    if not settings.backtest_range_guard:
        return
    days = (end - start).days + 1
    if days > BACKTEST_MAX_SERVER_DAYS:
        raise HTTPException(status_code=400, detail=BACKTEST_SERVER_GUARD_MESSAGE)


def _reject_taiwan_stamp_tax_override(
    symbols: list[str] | None,
    stamp_tax_pct: float | None,
) -> None:
    """Reject explicit Taiwan universes early; implicit universes fail in the matcher."""
    if stamp_tax_pct is None:
        return
    from app.backtest.strategy import TAIWAN_STAMP_TAX_OVERRIDE_ERROR
    from app.taiwan.symbol import is_taiwan_symbol

    if any(is_taiwan_symbol(symbol) for symbol in symbols or ()):
        raise HTTPException(status_code=400, detail=TAIWAN_STAMP_TAX_OVERRIDE_ERROR)


def _reject_unreasonable_risk_free_rate(risk_free_rate: float) -> None:
    from app.backtest.engine import validate_risk_free_rate

    try:
        validate_risk_free_rate(risk_free_rate)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ================================================================
# 策略回测
# ================================================================

class StrategyBacktestRequest(BaseModel):
    strategy_id: str
    symbols: list[str] | None = None
    start: date | None = None
    end: date | None = None
    params: dict | None = None
    overrides: dict | None = None
    # matching 向后兼容; 显式传 entry_fill/exit_fill 时以二者为准。
    matching: Literal["close_t", "open_t+1"] = "open_t+1"
    entry_fill: Literal["close_t", "open_t+1"] | None = None
    exit_fill: Literal["close_t", "open_t+1", "signal_next_minute"] | None = None
    fees_pct: float = 0.0002
    commission_pct: float | None = None
    commission_discount: float = Field(1.0, ge=0.0, le=1.0)
    # None selects the broker floor for lot_mode (see StrategyBacktestConfig).
    minimum_commission: float | None = Field(None, ge=0.0)
    stamp_tax_pct: float | None = None
    slippage_bps: float = 5.0
    max_positions: int = 10
    max_exposure_pct: float = 1.0
    initial_capital: float = 1_000_000.0
    position_sizing: Literal["equal", "score_weight"] = "equal"
    mode: Literal["position", "full"] = "position"
    holding_days: int = 5
    minimum_holding_sessions: int = Field(1, ge=0)
    day_trade_eligible_symbols: list[str] = Field(default_factory=list)
    asset_type: str = "stock"
    lot_mode: Literal["board_lot", "odd_lot"] = "board_lot"
    minute_fill: bool = False
    regime_filter: dict | None = None
    # Annual decimal rate; range is checked in the route so violations return 400.
    risk_free_rate: float = 0.0


@router.post("/strategy/run")
def strategy_run(req: StrategyBacktestRequest, request: Request):
    """策略回测 — 复用 StrategyDef 体系做全周期回测。"""
    from app.backtest.strategy import StrategyBacktestConfig
    from app.backtest.worker import make_worker_task, run_worker_task

    _reject_taiwan_stamp_tax_override(req.symbols, req.stamp_tax_pct)
    _reject_unreasonable_risk_free_rate(req.risk_free_rate)
    end = req.end or date.today()
    start = _resolve_start(req, end, FACTOR_DEFAULT_DAYS)
    _guard_server_backtest_range(start, end)

    cfg = StrategyBacktestConfig(
        strategy_id=req.strategy_id,
        symbols=req.symbols if req.symbols else None,
        start=start,
        end=end,
        params=req.params,
        overrides=req.overrides,
        matching=req.matching,
        entry_fill=req.entry_fill,
        exit_fill=req.exit_fill,
        fees_pct=req.fees_pct,
        commission_pct=req.commission_pct,
        commission_discount=req.commission_discount,
        minimum_commission=req.minimum_commission,
        stamp_tax_pct=req.stamp_tax_pct,
        slippage_bps=req.slippage_bps,
        max_positions=req.max_positions,
        max_exposure_pct=req.max_exposure_pct,
        initial_capital=req.initial_capital,
        position_sizing=req.position_sizing,
        mode=req.mode,
        holding_days=req.holding_days,
        minimum_holding_sessions=req.minimum_holding_sessions,
        day_trade_eligible_symbols=tuple(req.day_trade_eligible_symbols),
        asset_type=req.asset_type,
        lot_mode=req.lot_mode,
        minute_fill=req.minute_fill,
        regime_filter=req.regime_filter,
        risk_free_rate=req.risk_free_rate,
    )
    task = make_worker_task("backtest", settings.data_dir, cfg)
    from app.services.heavy_job_limiter import shared_heavy_job_limiter

    with shared_heavy_job_limiter.slot("normal"):
        return run_worker_task(task)
