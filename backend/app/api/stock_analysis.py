"""个股分析 API — 关键价位。

路由前缀: /api/stock-analysis

端点:
  GET  /levels?symbol=         11 类关键价位(图表 markLine 数据源)

Phase 8B-5.6B: 移除 AI 长篇个股研究报告 workflow(/analyze、/reports* 及其
report CRUD 持久化)—— 产品方向确认本产品不做长篇 AI 个股研究报告 / 分析
历史 / 背景任务 bubble,AI 仅用于解释既有选股 / 市场讯号。/levels 端点本身
供 PriceAlertDialog(价格点位提醒 + K 线关键价位)持续使用,予以保留。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import polars as pl
from fastapi import APIRouter, HTTPException, Query, Request

from app.indicators.levels import compute_levels
from app.taiwan.market_rules import TickSizeClass
from app.taiwan.realtime.calendar import get_market_status, taipei_now
from app.taiwan.realtime.models import MarketStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stock-analysis", tags=["stock-analysis"])


_TICK_CLASSES = {"stock": TickSizeClass.ORDINARY_STOCK, "etf": TickSizeClass.ETF}
_COMPLETED_SESSION_STATUSES = (
    MarketStatus.POST_CLOSE,
    MarketStatus.CLOSED,
    MarketStatus.NON_TRADING_DAY,
)


def _last_bar_is_live(df: pl.DataFrame) -> bool:
    """Whether the newest bar is today's session bar, still forming from the pre-open to the close."""
    if "date" not in df.columns:
        return False
    last = df["date"][-1]
    if isinstance(last, datetime):
        last = last.date()
    now = taipei_now()
    return last == now.date() and get_market_status(now) not in _COMPLETED_SESSION_STATUSES


@router.get("/levels")
def get_levels(
    request: Request,
    symbol: str = Query(..., description="标的代码,如 000001.SZ"),
    days: int = Query(120, ge=30, le=500, description="计算样本天数"),
):
    """计算 11 类关键价位(成交密集区压力支撑 / 枢轴点 / 前高前低 /
    布林带 / Keltner短中长 / ATR止损 / 缺口 / 斐波那契 / 整数关口)。

    返回 {levels: {sr, pivot, extreme, boll, keltner_s, keltner_m, keltner_l,
    atr_stop, gap, fib, round}, close, symbol}。
    """
    if not symbol:
        raise HTTPException(400, "symbol 不能为空")

    repo = request.app.state.repo
    end = date.today()
    start = end - timedelta(days=days * 2)
    asset_type = repo.resolve_asset_type(symbol)
    # 按资产类型分流: ETF/指数走独立 enriched 存储, 股票保持原路径
    df = repo.get_daily_asset(asset_type, symbol, start, end)
    if df.is_empty():
        return {"levels": {"sr": [], "pivot": [], "extreme": [],
                           "boll": [], "keltner_s": [], "keltner_m": [], "keltner_l": [],
                           "atr_stop": [], "gap": [], "fib": [], "round": []},
                "close": None, "symbol": symbol}

    levels = compute_levels(
        df, last_bar_live=_last_bar_is_live(df), tick_class=_TICK_CLASSES.get(asset_type)
    )
    close = float(df.tail(1)["close"][0]) if "close" in df.columns else None
    return {"levels": levels, "close": close, "symbol": symbol}
