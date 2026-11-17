"""Enriched table computation pipeline.

Storage (enriched parquet):
  The Taiwan publish (app.taiwan.enriched_refresh) stores the whole compute_enriched output,
  indicators and signals included, and rewrites it when INDICATOR_DEFINITION_VERSION changes.
  Backtests and rows without indicators (app.parquet.storage_only_rows) are recomputed with the
  functions here.

Design:
  - Polars expressions, except the recursive smoothing of EMA, MACD, RSI, ATR and KD (seeded_ewm)
  - each symbol is computed independently (`.over("symbol")`)
  - with an adj_factor, prices are forward-adjusted before the indicators; without one, raw is used
  - streaming collect bounds memory
"""
from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from app.config import settings
from app.market_time import cn_today
from app.share_capital import apply_historical_float_shares
from app.volume_units import TAIWAN_SUFFIXES, turnover_rate_pct_expr

if TYPE_CHECKING:
    from app.taiwan.market_rules import ListingSessions
    from app.taiwan.universe.models import TaiwanInstrument

try:
    from numba import njit
except ImportError:
    def njit(*args, **kwargs):
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]

        def decorator(func):
            return func

        return decorator

logger = logging.getLogger(__name__)

# Bump whenever a published indicator's definition changes: the Taiwan enriched publish
# records it and rewrites every partition once when the recorded version differs.
INDICATOR_DEFINITION_VERSION = 5


# ── 自定义信号缓存 ─────────────────────────────────────
# 从 data/user_data/custom_signals/*.json 加载并编译为 Polars 表达式。
# 模块级缓存：首次调用时加载，invalidate_custom_signals() 后下次重载。
_custom_signal_exprs: dict[str, pl.Expr] | None = None


def _get_custom_signal_exprs() -> dict[str, pl.Expr]:
    """懒加载自定义信号表达式（带模块级缓存）。"""
    global _custom_signal_exprs
    if _custom_signal_exprs is None:
        from app.strategy import custom_signals
        try:
            sigs = custom_signals.load_all(settings.data_dir)
            _custom_signal_exprs = custom_signals.build_expressions(sigs)
        except Exception as e:
            logger.warning("custom signals load failed: %s", e)
            _custom_signal_exprs = {}
    return _custom_signal_exprs


def invalidate_custom_signals() -> None:
    """失效自定义信号缓存（保存/删除信号后调用，下次计算重新加载）。"""
    global _custom_signal_exprs
    _custom_signal_exprs = None


# Storage columns of an enriched partition; the Taiwan publish also stores the indicators.
ENRICHED_STORAGE_COLS = [
    "symbol", "date",
    "open", "high", "low", "close",          # 前复权
    "volume", "amount",
    "raw_close", "raw_high", "raw_low",       # 不复权原始价
    "turnover_rate",                           # 依赖当时的 float_shares, 不可回推
    "consecutive_limit_ups",                   # 递推状态, 需从历史 cum_sum
    "consecutive_limit_downs",
    "quote_ts",                                # 行情时间戳(ms): 盘后校验/量比折算/跨天完整性
    # Raw-scale official base and limits on event sessions and after untraded ones (Taiwan only), else null.
    "ref_prev_close",
    "ref_limit_up",
    "ref_limit_down",
    # True on a Taiwan session after an untraded one whose official base is unknown.
    "ref_base_missing",
]


# ================================================================
# enriched 完整列清单 (存储 + 运行时计算)
# 供 AI 审查代码时参考: 策略/筛选/回测 可直接使用以下列名。
# 分类: 存储列 → 指标列 → 信号列 → JOIN 列
# ================================================================
ENRICHED_COLUMNS: dict[str, dict[str, str]] = {
    # ── 存储列 (parquet 持久化) ──────────────────────────
    "symbol":                  "股票代码",
    "date":                    "交易日期",
    "open":                    "前复权开盘价",
    "high":                    "前复权最高价",
    "low":                     "前复权最低价",
    "close":                   "前复权收盘价",
    "volume":                  "成交量",
    "amount":                  "成交额",
    "raw_close":               "原始收盘价(未复权)",
    "raw_high":                "原始最高价(未复权)",
    "raw_low":                 "原始最低价(未复权)",
    "turnover_rate":           "换手率(台股分母为已发行普通股数扣除私募股数, 非流通股数; 只保留自每日发布当天起)",
    "consecutive_limit_ups":   "连板数",
    "consecutive_limit_downs": "连跌数",
    # ── 基础指标 ─────────────────────────────────────────
    "prev_close":              "前收盘价",
    "change_pct":              "日涨跌幅(小数, 如 0.05 = 5%)",
    "change_amount":           "日涨跌额",
    "amplitude":               "日振幅 (最高-最低)/昨收",
    "distance_to_upper_limit": "距涨停距离(小数, 台股)",
    "distance_to_lower_limit": "距跌停距离(小数, 台股)",
    # ── 均线 MA ──────────────────────────────────────────
    "ma5":                     "5日简单均线",
    "ma10":                    "10日简单均线",
    "ma20":                    "20日简单均线",
    "ma30":                    "30日简单均线",
    "ma60":                    "60日简单均线(季线)",
    # ── 指数均线 EMA ─────────────────────────────────────
    "ema5":                    "5日指数均线",
    "ema10":                   "10日指数均线",
    "ema20":                   "20日指数均线",
    "ema30":                   "30日指数均线",
    "ema60":                   "60日指数均线",
    # ── MACD ─────────────────────────────────────────────
    "macd_dif":                "MACD DIF线(快线-慢线)",
    "macd_dea":                "MACD DEA线(信号线)",
    "macd_hist":               "MACD柱状图 (DIF-DEA)",
    # ── 布林带 BOLL ──────────────────────────────────────
    "boll_upper":              "布林带上轨 MA20+2σ (总体标准差)",
    "boll_lower":              "布林带下轨 MA20-2σ (总体标准差)",
    # ── KDJ ──────────────────────────────────────────────
    "kdj_k":                   "KDJ K值",
    "kdj_d":                   "KDJ D值",
    "kdj_j":                   "KDJ J值 (3K-2D)",
    # ── ATR ──────────────────────────────────────────────
    "atr_14":                  "14日平均真实波幅",
    # ── 量价 ─────────────────────────────────────────────
    "vol_ma5":                 "5日成交均量",
    "vol_ma10":                "10日成交均量",
    "vol_ratio_5d":            "量比 (成交量/5日均量)",
    # ── 极值 ─────────────────────────────────────────────
    "high_60d":                "60日收盘最高价 (含当日)",
    "low_60d":                 "60日收盘最低价 (含当日)",
    # ── 动量 ─────────────────────────────────────────────
    "momentum_5d":             "5日动量(涨跌幅小数)",
    "momentum_10d":            "10日动量",
    "momentum_20d":            "20日动量",
    "momentum_30d":            "30日动量",
    "momentum_60d":            "60日动量",
    # ── 波动率 ───────────────────────────────────────────
    "annual_vol_20d":          "20日年化波动率 (对数收益样本标准差乘以√252)",
    # ── RSI ──────────────────────────────────────────────
    "rsi_6":                   "6日相对强弱指标",
    "rsi_14":                  "14日相对强弱指标",
    "rsi_24":                  "24日相对强弱指标",
    # ── 信号列 (bool) ────────────────────────────────────
    "signal_ma_golden_5_20":   "MA5上穿MA20 (金叉)",
    "signal_ma_dead_5_20":     "MA5下穿MA20 (死叉)",
    "signal_ma_golden_20_60":  "MA20上穿MA60",
    "signal_macd_golden":      "MACD金叉 (DIF上穿DEA)",
    "signal_macd_dead":        "MACD死叉 (DIF下穿DEA)",
    "signal_ma20_breakout":    "收盘突破MA20上方",
    "signal_ma20_breakdown":   "收盘跌破MA20下方",
    "signal_ma5_breakout":     "收盘突破MA5上方",
    "signal_ma5_breakdown":    "收盘跌破MA5下方",
    "signal_ma10_breakout":    "收盘突破MA10上方",
    "signal_ma10_breakdown":   "收盘跌破MA10下方",
    "signal_n_day_high":       "收盘达60日收盘最高",
    "signal_n_day_low":        "收盘达60日收盘最低",
    "signal_boll_breakout_upper": "突破布林上轨",
    "signal_boll_breakdown_lower": "跌破布林下轨",
    "signal_volume_surge":     "放量 (量比≥2.0)",
    "signal_limit_up":         "涨停",
    "signal_limit_down":       "跌停",
    "signal_limit_down_recovery": "跌停翻红(盘中触及跌停, 收盘高于昨收)",
    "signal_broken_limit_up":  "炸板(最高触及涨停但收盘未封住)",
    # ── JOIN 列 (由 repository 从 instruments 表补充) ───
    "name":                    "股票名称 (来自 instruments)",
    "total_shares":            "总股本 (来自 instruments)",
    "float_shares":            "流通股本 (来自 instruments)",
}

# 仅供 AI/开发者快速索引: 按类别的列名列表
ENRICHED_COLUMNS_BY_CATEGORY: dict[str, list[str]] = {
    "basic":    ["prev_close", "change_pct", "change_amount", "amplitude"],
    "ma":       ["ma5", "ma10", "ma20", "ma30", "ma60"],
    "ema":      ["ema5", "ema10", "ema20", "ema30", "ema60"],
    "macd":     ["macd_dif", "macd_dea", "macd_hist"],
    "boll":     ["boll_upper", "boll_lower"],
    "kdj":      ["kdj_k", "kdj_d", "kdj_j"],
    "atr":      ["atr_14"],
    "volume":   ["vol_ma5", "vol_ma10", "vol_ratio_5d"],
    "extremes": ["high_60d", "low_60d"],
    "momentum": ["momentum_5d", "momentum_10d", "momentum_20d", "momentum_30d", "momentum_60d"],
    "volatility": ["annual_vol_20d"],
    "rsi":      ["rsi_6", "rsi_14", "rsi_24"],
}


def _ema_alpha(span: int) -> float:
    return 2.0 / (span + 1)


# Taiwan KD convention: K and D start from 50 before the first RSV.
KD_PRIOR = 50.0


@njit(cache=True, nogil=True)
def _seeded_ewm_kernel(
    values: np.ndarray,
    offsets: np.ndarray,
    alpha: float,
    seed: int,
    prior: float,
    hold: np.ndarray,
) -> np.ndarray:
    out = np.full(values.shape[0], np.nan)
    decay = 1.0 - alpha
    for group in range(offsets.shape[0] - 1):
        seeded = np.isfinite(prior)
        state = prior
        total = 0.0
        seen = 0
        for i in range(offsets[group], offsets[group + 1]):
            if hold[i] and seeded:
                out[i] = state
                continue
            value = values[i]
            if not np.isfinite(value):
                continue
            if seeded:
                state = decay * state + alpha * value
            else:
                seen += 1
                total += value
                if seen < seed:
                    continue
                state = total / seed
                seeded = True
            out[i] = state
    return out


def seeded_ewm(
    values: np.ndarray,
    offsets: np.ndarray,
    alpha: float,
    *,
    seed: int = 1,
    prior: float | None = None,
    hold: np.ndarray | None = None,
) -> np.ndarray:
    """adjust=False EWM of each group ``values[offsets[g]:offsets[g + 1]]`` over its finite values.

    The state starts at *prior* when given, otherwise at the mean of the group's first *seed*
    finite values, with NaN before it. Non-finite values are skipped and give NaN. Where *hold*
    is true and the state exists, the state is kept and output whatever the value.
    """
    values = np.ascontiguousarray(values, dtype=np.float64)
    return _seeded_ewm_kernel(
        values,
        np.ascontiguousarray(offsets, dtype=np.int64),
        float(alpha),
        int(seed),
        np.nan if prior is None else float(prior),
        np.zeros(values.shape[0], dtype=np.bool_)
        if hold is None
        else np.ascontiguousarray(hold, dtype=np.bool_),
    )


def ema_values(values: np.ndarray, offsets: np.ndarray, span: int) -> np.ndarray:
    """EMA seeded with the SMA of its first *span* values (StockCharts)."""
    return seeded_ewm(values, offsets, _ema_alpha(span), seed=span)


def wilder_values(values: np.ndarray, offsets: np.ndarray, n: int) -> np.ndarray:
    """Wilder's average: the mean of the first *n* values, then (prev * (n - 1) + value) / n."""
    return seeded_ewm(values, offsets, 1.0 / n, seed=n)


def macd_values(close: np.ndarray, offsets: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """MACD 12/26/9: DIF = EMA12 - EMA26 and DEA, the EMA9 of DIF."""
    dif = ema_values(close, offsets, 12) - ema_values(close, offsets, 26)
    return dif, ema_values(dif, offsets, 9)


def kd_values(
    rsv: np.ndarray, offsets: np.ndarray, flat: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """KD 9/3/3: K = 2/3 K + 1/3 RSV and D = 2/3 D + 1/3 K, both from KD_PRIOR.

    On a *flat* bar (9-bar high equal to the 9-bar low) RSV is the previous K, so K is unchanged.
    """
    k = seeded_ewm(rsv, offsets, 1.0 / 3.0, prior=KD_PRIOR, hold=flat)
    return k, seeded_ewm(k, offsets, 1.0 / 3.0, prior=KD_PRIOR)


def rsi_from_averages(average_gain: np.ndarray, average_loss: np.ndarray) -> np.ndarray:
    """RSI of Wilder averages: 50 with neither gains nor losses, 100 with gains only."""
    with np.errstate(divide="ignore", invalid="ignore"):
        rsi = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    rsi = np.where(average_loss == 0, np.where(average_gain == 0, 50.0, 100.0), rsi)
    return np.where(np.isfinite(average_gain) & np.isfinite(average_loss), rsi, np.nan)


def rsi_values(gains: np.ndarray, losses: np.ndarray, offsets: np.ndarray, n: int) -> np.ndarray:
    """Wilder RSI from per-bar gains and losses, NaN where the change is missing."""
    return rsi_from_averages(wilder_values(gains, offsets, n), wilder_values(losses, offsets, n))


def _symbol_offsets(df: pl.DataFrame) -> np.ndarray:
    """Row offsets of each symbol's run in *df*, which is sorted by symbol."""
    starts = np.flatnonzero((df["symbol"] != df["symbol"].shift(1)).fill_null(True).to_numpy())
    return np.append(starts, df.height).astype(np.int64)


def _float_values(df: pl.DataFrame, name: str) -> np.ndarray:
    return df[name].cast(pl.Float64).to_numpy()


def _series(name: str, values: np.ndarray) -> pl.Series:
    return pl.Series(name, values, dtype=pl.Float64, nan_to_null=True)


def _apply_adj_factor(raw: pl.DataFrame, factors: pl.DataFrame) -> pl.DataFrame:
    """对 raw K 线应用前复权 (forward adjustment)。

    adj_factor 结构: symbol, trade_date, ex_factor
    ex_factor 含义: 每次除权事件的 pre/post 比值(个股级,非累积)。

    前复权原理:
      - 保持最新价格不变,将历史价格向下调整以消除除权缺口
      - adjusted = raw × cumprod_at_D / total_cumprod
      - 等价于: adjusted = raw / (该日期之后所有事件的 ex_factor 乘积)

    An optional volume_factor column restates volume the same way in the other direction:
    volume times the product of volume_factor over the events after the bar. Without it volume
    is untouched.
    """
    if factors.is_empty():
        return raw

    has_volume_factor = "volume_factor" in factors.columns
    factor_cols = ["ex_factor", *(["volume_factor"] if has_volume_factor else [])]

    # 确保类型一致
    factors = factors.with_columns(
        pl.col("trade_date").cast(pl.Date, strict=False),
        *[pl.col(c).cast(pl.Float64, strict=False) for c in factor_cols],
    ).select("symbol", "trade_date", *factor_cols).drop_nulls()

    if factors.is_empty():
        return raw

    # 去重 + 排序 + 累积乘积 (一趟完成)
    factors_sorted = (
        factors.sort(["symbol", "trade_date"])
        .unique(subset=["symbol", "trade_date"])
        .sort(["symbol", "trade_date"])
        .with_columns(
            pl.col("ex_factor").cum_prod().over("symbol").alias("cum_factor"),
            *(
                [pl.col("volume_factor").cum_prod().over("symbol").alias("cum_volume_factor")]
                if has_volume_factor else []
            ),
        )
    )
    cum_cols = ["cum_factor", *(["cum_volume_factor"] if has_volume_factor else [])]

    # 每个 symbol 的总累积因子
    total_factors = (
        factors_sorted
        .group_by("symbol")
        .agg(
            pl.col("cum_factor").last().alias("total_factor"),
            *(
                [pl.col("cum_volume_factor").last().alias("total_volume_factor")]
                if has_volume_factor else []
            ),
        )
    )

    raw_sorted = raw.sort(["symbol", "date"])

    # join_asof backward: 每根 K 线取 <= 其 date 的最新累积因子
    # 同时带 trade_date 列用于判断除权日标记
    df = raw_sorted.join_asof(
        factors_sorted.select("symbol", "trade_date", *cum_cols),
        left_on="date",
        right_on="trade_date",
        by="symbol",
        strategy="backward",
    )

    # 补充 total_factor + 前复权 + 除权标记,一次 with_columns 完成
    df = df.join(total_factors, on="symbol", how="left")

    is_ex = (pl.col("trade_date") == pl.col("date")).fill_null(False)
    ratio = pl.col("cum_factor").fill_null(1.0) / pl.col("total_factor").fill_null(1.0)
    price_cols = [c for c in ("open", "high", "low", "close") if c in df.columns]
    volume_exprs = []
    if has_volume_factor and "volume" in df.columns:
        volume_ratio = pl.col("total_volume_factor").fill_null(1.0) / pl.col("cum_volume_factor").fill_null(1.0)
        volume_exprs.append(pl.col("volume") * volume_ratio)

    df = df.with_columns(
        [pl.col(c) * ratio for c in price_cols]
        + volume_exprs
        + [
            is_ex.alias("ex_rights"),
        ]
    ).drop(["trade_date", *cum_cols, "total_factor", *(["total_volume_factor"] if has_volume_factor else [])])

    return df


# ================================================================
# 技术指标计算 (从 OHLCV 计算)
# ================================================================

# ── compute_indicators 的列依赖关系 (供 needed 裁剪时求闭包) ────────────
# target -> 其计算所依赖的中间/指标列 (仅列出依赖非原始 OHLCV 的项)
_INDICATOR_DEPS: dict[str, set[str]] = {
    "boll_upper": {"ma20", "_boll_std"},
    "boll_lower": {"ma20", "_boll_std"},
    "macd_dea": {"macd_dif"},
    "macd_hist": {"macd_dif", "macd_dea"},
    "kdj_k": {"_kdj_ln", "_kdj_hn"},
    "kdj_d": {"kdj_k"},
    "kdj_j": {"kdj_k", "kdj_d"},
    "atr_14": {"_tr"},
    "annual_vol_20d": {"_daily_log_return"},
}

# compute_indicators 可产出的全部指标/临时列 (needed=None 时即为此全集, 行为不变)
_ALL_INDICATOR_COLS: frozenset[str] = frozenset({
    "prev_close", "ma5", "ma10", "ma20", "ma30", "ma60",
    "ema5", "ema10", "ema20", "ema30", "ema60",
    "_boll_std", "_kdj_ln", "_kdj_hn", "_tr", "vol_ma5", "vol_ma10",
    "high_60d", "low_60d",
    "macd_dif", "boll_upper", "boll_lower", "macd_dea", "macd_hist",
    "kdj_k", "kdj_d", "kdj_j",
    "atr_14", "vol_ratio_5d",
    "momentum_5d", "momentum_10d", "momentum_20d", "momentum_30d", "momentum_60d",
    "change_pct", "change_amount", "amplitude", "_daily_log_return", "annual_vol_20d",
    "rsi_6", "rsi_14", "rsi_24",
})


def _resolve_needed(needed: set[str] | None) -> set[str]:
    """把 needed 展开为闭包 (含所依赖的中间列)。needed=None → 全集。"""
    if needed is None:
        return set(_ALL_INDICATOR_COLS)
    want = set(needed)
    changed = True
    while changed:
        changed = False
        for target in list(want):
            deps = _INDICATOR_DEPS.get(target)
            if deps and not deps <= want:
                want |= deps
                changed = True
    return want


def _reference_prev_close(df: pl.DataFrame, prev_close: pl.Expr) -> pl.Expr:
    """The previous close, replaced by the exchange reference price where a row carries one.

    ref_prev_close is on the raw scale; close / raw_close moves it onto the scale of close,
    so the base stays right once close is forward-adjusted. A ref_base_missing row has no base.
    """
    base = prev_close
    if "ref_prev_close" in df.columns and "raw_close" in df.columns:
        on_close_scale = (
            pl.when(pl.col("raw_close") > 0)
            .then(pl.col("ref_prev_close") * (pl.col("close") / pl.col("raw_close")))
            .otherwise(None)
        )
        base = pl.coalesce(on_close_scale, prev_close)
    if "ref_base_missing" in df.columns:
        base = pl.when(pl.col("ref_base_missing").fill_null(False)).then(None).otherwise(base)
    return base


def compute_indicators(
    df: pl.DataFrame,
    needed: set[str] | None = None,
    *,
    assume_sorted: bool = False,
) -> pl.DataFrame:
    """从 OHLCV 数据计算全套技术指标。

    输入必须包含: symbol, date, open, high, low, close, volume
    返回添加了所有指标列的 DataFrame。

    needed:
        None (默认) — 计算全部指标, 行为与历史逐位一致 (所有 gate 为真, 表达式/顺序不变)。
        列名集合   — 仅计算这些列及其依赖闭包, 跳过无关的 EMA/KDJ/RSI 等 pass;
                     输出保留输入列 + 所需指标列。被保留列的数值与全量计算逐位一致
                     (逐列 window/rolling 相互独立, 跳过其它列不影响保留列)。
    """
    if df.is_empty():
        return df

    import time as _time
    _t0 = _time.perf_counter()

    want = _resolve_needed(needed)

    df = df if assume_sorted else df.sort(["symbol", "date"])
    offsets = _symbol_offsets(df)

    # Pass 1: 均线 + EMA + MACD 基础 + BOLL 基础 + KDJ 基础 + ATR 基础 + 量价 + 极值
    prev_close = pl.col("close").shift(1).over("symbol")
    reference_prev_close = _reference_prev_close(df, prev_close)
    _p1: list[pl.Expr | pl.Series] = []
    if "prev_close" in want:
        _p1.append(reference_prev_close.alias("prev_close"))
    if "ma5" in want:
        _p1.append(pl.col("close").rolling_mean(5).over("symbol").alias("ma5"))
    if "ma10" in want:
        _p1.append(pl.col("close").rolling_mean(10).over("symbol").alias("ma10"))
    if "ma20" in want:
        _p1.append(pl.col("close").rolling_mean(20).over("symbol").alias("ma20"))
    if "ma30" in want:
        _p1.append(pl.col("close").rolling_mean(30).over("symbol").alias("ma30"))
    if "ma60" in want:
        _p1.append(pl.col("close").rolling_mean(60).over("symbol").alias("ma60"))
    close = (
        _float_values(df, "close")
        if want & {"ema5", "ema10", "ema20", "ema30", "ema60", "macd_dif"}
        else None
    )
    for span in (5, 10, 20, 30, 60):
        if f"ema{span}" in want:
            _p1.append(_series(f"ema{span}", ema_values(close, offsets, span)))
    if "_boll_std" in want:
        _p1.append(pl.col("close").rolling_std(20, ddof=0).over("symbol").alias("_boll_std"))
    if "_kdj_ln" in want:
        _p1.append(pl.col("low").rolling_min(9).over("symbol").alias("_kdj_ln"))
    if "_kdj_hn" in want:
        _p1.append(pl.col("high").rolling_max(9).over("symbol").alias("_kdj_hn"))
    if "_tr" in want:
        _p1.append(
            pl.when(prev_close.is_not_null())
            .then(
                pl.max_horizontal(
                    pl.col("high") - pl.col("low"),
                    (pl.col("high") - prev_close).abs(),
                    (pl.col("low") - prev_close).abs(),
                )
            )
            .alias("_tr")
        )
    if "vol_ma5" in want:
        _p1.append(pl.col("volume").rolling_mean(5).over("symbol").alias("vol_ma5"))
    if "vol_ma10" in want:
        _p1.append(pl.col("volume").rolling_mean(10).over("symbol").alias("vol_ma10"))
    if "vol_ratio_5d" in want:
        # 前5日平均成交量(不含当天), 标准量比分母: volume.shift(1).rolling_mean(5)
        _p1.append(pl.col("volume").shift(1).rolling_mean(5).over("symbol").alias("_vol_ma5_prev"))
    if "high_60d" in want:
        _p1.append(pl.col("close").rolling_max(60).over("symbol").alias("high_60d"))
    if "low_60d" in want:
        _p1.append(pl.col("close").rolling_min(60).over("symbol").alias("low_60d"))
    if _p1:
        df = df.with_columns(_p1)

    # Pass 2: MACD + BOLL (基于 Pass 1 基础列)
    _p2: list[pl.Expr | pl.Series] = []
    if "macd_dif" in want:
        macd_dif, macd_dea = macd_values(close, offsets)
        _p2.append(_series("macd_dif", macd_dif))
    if "boll_upper" in want:
        _p2.append((pl.col("ma20") + 2 * pl.col("_boll_std")).alias("boll_upper"))
    if "boll_lower" in want:
        _p2.append((pl.col("ma20") - 2 * pl.col("_boll_std")).alias("boll_lower"))
    if _p2:
        df = df.with_columns(_p2)
    if "macd_dea" in want:
        df = df.with_columns(_series("macd_dea", macd_dea))
    if "macd_hist" in want:
        df = df.with_columns(
            (pl.col("macd_dif") - pl.col("macd_dea")).alias("macd_hist"),
        )

    # Pass 3: KDJ
    if "kdj_k" in want:
        _kdj_range = pl.col("_kdj_hn") - pl.col("_kdj_ln")
        _kdj = df.select(
            pl.when(_kdj_range > 0)
            .then(100 * (pl.col("close") - pl.col("_kdj_ln")) / _kdj_range)
            .cast(pl.Float64)
            .alias("rsv"),
            (_kdj_range == 0).fill_null(False).alias("flat"),
        )
        kdj_k, kdj_d = kd_values(
            _kdj["rsv"].to_numpy(), offsets, _kdj["flat"].to_numpy()
        )
        df = df.with_columns(_series("kdj_k", kdj_k))
    if "kdj_d" in want:
        df = df.with_columns(_series("kdj_d", kdj_d))
    if "kdj_j" in want:
        df = df.with_columns([
            (3 * pl.col("kdj_k") - 2 * pl.col("kdj_d")).alias("kdj_j"),
        ])

    # Pass 4: ATR + 量比 + 动量 + 波动 + 涨跌幅 + 涨跌额 + 振幅
    if "atr_14" in want:
        df = df.with_columns(
            _series("atr_14", wilder_values(_float_values(df, "_tr"), offsets, 14))
        )
    if "vol_ratio_5d" in want:
        # 标准量比(同花顺/东财): 今日成交量 / 前5日均量(不含当天)
        # 盘后全量路径: 当日 volume 是完整全天量, 无需时间折算
        df = df.with_columns(
            (pl.col("volume") / pl.col("_vol_ma5_prev")).alias("vol_ratio_5d"),
        )
    _p4mom: list[pl.Expr] = []
    if "momentum_5d" in want:
        _p4mom.append((pl.col("close") / pl.col("close").shift(5).over("symbol") - 1).alias("momentum_5d"))
    if "momentum_10d" in want:
        _p4mom.append((pl.col("close") / pl.col("close").shift(10).over("symbol") - 1).alias("momentum_10d"))
    if "momentum_20d" in want:
        _p4mom.append((pl.col("close") / pl.col("close").shift(20).over("symbol") - 1).alias("momentum_20d"))
    if "momentum_30d" in want:
        _p4mom.append((pl.col("close") / pl.col("close").shift(30).over("symbol") - 1).alias("momentum_30d"))
    if "momentum_60d" in want:
        _p4mom.append((pl.col("close") / pl.col("close").shift(60).over("symbol") - 1).alias("momentum_60d"))
    if "change_pct" in want:
        _p4mom.append((pl.col("close") / reference_prev_close - 1).alias("change_pct"))
    if _p4mom:
        df = df.with_columns(_p4mom)
    if "change_amount" in want:
        df = df.with_columns(
            (pl.col("close") - reference_prev_close).alias("change_amount"),
        )
    if "amplitude" in want:
        df = df.with_columns(
            pl.when(reference_prev_close > 0)
              .then((pl.col("high") - pl.col("low")) / reference_prev_close)
              .otherwise(None)
              .alias("amplitude"),
        )
    if "_daily_log_return" in want:
        df = df.with_columns(
            (pl.col("close") / pl.col("close").shift(1)).log().over("symbol").alias("_daily_log_return"),
        )
    if "annual_vol_20d" in want:
        df = df.with_columns(
            (pl.col("_daily_log_return").rolling_std(20, ddof=1).over("symbol") * (252 ** 0.5))
                .alias("annual_vol_20d"),
        )

    # Pass 5: RSI
    if want & {"rsi_6", "rsi_14", "rsi_24"}:
        delta = (
            df.select(pl.col("close").diff().over("symbol").cast(pl.Float64)).to_series().to_numpy()
        )
        gains, losses = np.fmax(delta, 0.0), np.fmax(-delta, 0.0)
        gains[np.isnan(delta)] = np.nan
        losses[np.isnan(delta)] = np.nan
        df = df.with_columns(
            [
                _series(f"rsi_{n}", rsi_values(gains, losses, offsets, n))
                for n in (6, 14, 24)
                if f"rsi_{n}" in want
            ]
        )

    # Pass 6: 换手率 (需要 float_shares, 后续在 compute_all 中 JOIN instruments 后补充)

    # 清理临时列 (只丢弃实际存在的临时列)
    _temp_cols = ["_boll_std", "_tr",
                  "_kdj_ln", "_kdj_hn", "_vol_ma5_prev", "_daily_log_return"]
    df = df.drop([c for c in _temp_cols if c in df.columns])

    _elapsed = (_time.perf_counter() - _t0) * 1000
    import logging as _logging
    _logging.getLogger(__name__).debug("compute_indicators: %.1fms, %d rows", _elapsed, len(df))

    return df


SIGNAL_DEPENDENCIES: dict[str, frozenset[str]] = {
    "signal_ma_golden_5_20": frozenset({"ma5", "ma20"}),
    "signal_ma_dead_5_20": frozenset({"ma5", "ma20"}),
    "signal_ma_golden_20_60": frozenset({"ma20", "ma60"}),
    "signal_macd_golden": frozenset({"macd_dif", "macd_dea"}),
    "signal_macd_dead": frozenset({"macd_dif", "macd_dea"}),
    "signal_ma20_breakout": frozenset({"close", "ma20"}),
    "signal_ma20_breakdown": frozenset({"close", "ma20"}),
    "signal_ma5_breakout": frozenset({"close", "ma5"}),
    "signal_ma5_breakdown": frozenset({"close", "ma5"}),
    "signal_ma10_breakout": frozenset({"close", "ma10"}),
    "signal_ma10_breakdown": frozenset({"close", "ma10"}),
    "signal_n_day_high": frozenset({"close", "high_60d"}),
    "signal_n_day_low": frozenset({"close", "low_60d"}),
    "signal_boll_breakout_upper": frozenset({"close", "boll_upper"}),
    "signal_boll_breakdown_lower": frozenset({"close", "boll_lower"}),
    "signal_volume_surge": frozenset({"vol_ratio_5d"}),
}

LIMIT_SIGNAL_OUTPUTS: frozenset[str] = frozenset({
    "signal_limit_up",
    "signal_limit_down",
    "signal_limit_down_recovery",
    "signal_broken_limit_up",
    "consecutive_limit_ups",
    "consecutive_limit_downs",
    "turnover_rate",
})

# Written after compute_signals, and the Taiwan publish restates them after compute_enriched.
LATE_SIGNAL_INPUTS: frozenset[str] = frozenset(
    {"turnover_rate", "consecutive_limit_ups", "consecutive_limit_downs"}
)

INDICATOR_COLUMNS: frozenset[str] = frozenset(
    col for col in _ALL_INDICATOR_COLS if not col.startswith("_")
)


def get_signal_dependencies() -> dict[str, frozenset[str]]:
    """返回内置与 JSON 自定义信号的唯一依赖映射。"""
    from app.strategy import custom_signals

    return {
        **SIGNAL_DEPENDENCIES,
        **{
            name: limit_distance_inputs(deps)
            for name, deps in custom_signals.expression_dependencies(_get_custom_signal_exprs()).items()
        },
    }


CROSS_TOLERANCE = 1e-9


def cross_side(left: pl.Expr, right: pl.Expr, scale: pl.Expr) -> pl.Expr:
    """1.0 above, -1.0 below, 0.0 within max(1e-9, 1e-9 * |scale|) of *right*; null when either is null."""
    diff = left - right
    tolerance = pl.max_horizontal(pl.lit(CROSS_TOLERANCE), CROSS_TOLERANCE * scale.abs())
    return (
        pl.when(diff >= tolerance).then(1.0)
        .when(diff <= -tolerance).then(-1.0)
        .when(diff.is_not_null()).then(0.0)
    )


def _previous_side(side: pl.Expr) -> pl.Expr:
    """The last nonzero side before each row of the symbol; zero-side and null rows are skipped."""
    return pl.when(side != 0).then(side).forward_fill().shift(1).over("symbol")


def cross_above(left: pl.Expr, right: pl.Expr, scale: pl.Expr) -> pl.Expr:
    """*left* is strictly above *right* and was strictly below on the last bar on either side.

    Rows must be sorted by symbol and date.
    """
    side = cross_side(left, right, scale)
    return (side == 1) & (_previous_side(side) == -1)


def cross_below(left: pl.Expr, right: pl.Expr, scale: pl.Expr) -> pl.Expr:
    """The mirror of cross_above."""
    side = cross_side(left, right, scale)
    return (side == -1) & (_previous_side(side) == 1)


def compute_signals(df: pl.DataFrame, needed: set[str] | None = None) -> pl.DataFrame:
    """从已有指标列计算原子信号布尔列。

    输入必须包含 compute_indicators() 产出的指标列。
    """
    if df.is_empty():
        return df

    want = set(SIGNAL_DEPENDENCIES) if needed is None else set(needed) & set(SIGNAL_DEPENDENCIES)
    expressions: dict[str, pl.Expr] = {
        "signal_ma_golden_5_20": cross_above(pl.col("ma5"), pl.col("ma20"), pl.col("close")).alias("signal_ma_golden_5_20"),
        "signal_ma_dead_5_20": cross_below(pl.col("ma5"), pl.col("ma20"), pl.col("close")).alias("signal_ma_dead_5_20"),
        "signal_ma_golden_20_60": cross_above(pl.col("ma20"), pl.col("ma60"), pl.col("close")).alias("signal_ma_golden_20_60"),
        "signal_macd_golden": cross_above(pl.col("macd_dif"), pl.col("macd_dea"), pl.col("close")).alias("signal_macd_golden"),
        "signal_macd_dead": cross_below(pl.col("macd_dif"), pl.col("macd_dea"), pl.col("close")).alias("signal_macd_dead"),
        "signal_ma20_breakout": cross_above(pl.col("close"), pl.col("ma20"), pl.col("close")).alias("signal_ma20_breakout"),
        "signal_ma20_breakdown": cross_below(pl.col("close"), pl.col("ma20"), pl.col("close")).alias("signal_ma20_breakdown"),
        "signal_ma5_breakout": cross_above(pl.col("close"), pl.col("ma5"), pl.col("close")).alias("signal_ma5_breakout"),
        "signal_ma5_breakdown": cross_below(pl.col("close"), pl.col("ma5"), pl.col("close")).alias("signal_ma5_breakdown"),
        "signal_ma10_breakout": cross_above(pl.col("close"), pl.col("ma10"), pl.col("close")).alias("signal_ma10_breakout"),
        "signal_ma10_breakdown": cross_below(pl.col("close"), pl.col("ma10"), pl.col("close")).alias("signal_ma10_breakdown"),
        "signal_n_day_high": (cross_side(pl.col("close"), pl.col("high_60d"), pl.col("close")) >= 0).alias("signal_n_day_high"),
        "signal_n_day_low": (cross_side(pl.col("close"), pl.col("low_60d"), pl.col("close")) <= 0).alias("signal_n_day_low"),
        "signal_boll_breakout_upper": (cross_side(pl.col("close"), pl.col("boll_upper"), pl.col("close")) == 1).alias("signal_boll_breakout_upper"),
        "signal_boll_breakdown_lower": (cross_side(pl.col("close"), pl.col("boll_lower"), pl.col("close")) == -1).alias("signal_boll_breakdown_lower"),
        "signal_volume_surge": (pl.col("vol_ratio_5d") >= 2.0).alias("signal_volume_surge"),
    }
    if want:
        df = df.with_columns([expressions[name] for name in SIGNAL_DEPENDENCIES if name in want])

    # 自定义信号（用户配置的字段+运算符+值组合，编译为布尔列）
    df = inject_custom_signals(df, _custom_signals_by_stage(late=False), needed=needed)

    return df


def _custom_signals_by_stage(*, late: bool) -> dict[str, pl.Expr]:
    """The custom signals that read a LATE_SIGNAL_INPUTS column (*late*), or those reading none."""
    from app.strategy import custom_signals

    exprs = _get_custom_signal_exprs()
    dependencies = custom_signals.expression_dependencies(exprs)
    return {
        name: expr for name, expr in exprs.items()
        if dependencies[name].isdisjoint(LATE_SIGNAL_INPUTS) != late
    }


def inject_late_custom_signals(df: pl.DataFrame, needed: set[str] | None = None) -> pl.DataFrame:
    """Inject the custom signals compute_signals leaves out: those reading LATE_SIGNAL_INPUTS.

    Every path running compute_signals calls it once, after its last write of those columns.
    """
    return inject_custom_signals(df, _custom_signals_by_stage(late=True), needed=needed)


def with_limit_streak(df: pl.DataFrame, signal: str, streak: str) -> pl.DataFrame:
    """Set *streak* to the number of consecutive sessions, per symbol by date, ending at a true *signal*; 0 elsewhere."""
    hit = pl.col(signal).fill_null(False)
    run = f"_{streak}_run"
    return (
        df.with_columns((~hit).cast(pl.UInt32).cum_sum().over("symbol", order_by="date").alias(run))
        .with_columns(
            pl.when(hit)
            .then(hit.cast(pl.UInt32).cum_sum().over(["symbol", run], order_by="date"))
            .otherwise(0)
            .cast(pl.UInt32)
            .alias(streak)
        )
        .drop(run)
    )


def _is_taiwan_symbol() -> pl.Expr:
    return pl.col("symbol").str.ends_with(TAIWAN_SUFFIXES[0]) | pl.col("symbol").str.ends_with(TAIWAN_SUFFIXES[1])


def _join_taiwan_limit_profile(df: pl.DataFrame, trade_date: pl.Expr) -> pl.DataFrame:
    """Join _tw_limit_pct, the statutory limit on each row's *trade_date*, _tw_is_etf and _tw_ipo_session."""
    from app.taiwan.market_rules import PriceLimitModel

    profile = taiwan_limit_profile(df["symbol"].unique().to_list())
    return df.join(
        profile.select(
            "symbol",
            pl.col("limit_pct").alias("_tw_limit_pct"),
            pl.col("is_etf_tick").alias("_tw_is_etf"),
            pl.col("ipo_start").alias("_tw_ipo_start"),
            pl.col("ipo_end").alias("_tw_ipo_end"),
        ),
        on="symbol",
        how="left",
    ).with_columns(
        PriceLimitModel.polars_session_limit_pct(
            pl.col("_tw_limit_pct"), trade_date, pl.col("_tw_ipo_start"), pl.col("_tw_ipo_end")
        ).alias("_tw_limit_pct"),
        PriceLimitModel.polars_in_ipo_window(
            trade_date, pl.col("_tw_ipo_start"), pl.col("_tw_ipo_end")
        ).alias("_tw_ipo_session"),
    ).drop("_tw_ipo_start", "_tw_ipo_end")


LIMIT_TOUCH_TOLERANCE = 0.005


def limit_down_recovery(
    raw_low: pl.Expr, raw_close: pl.Expr, limit_down: pl.Expr, prev_raw_close: pl.Expr
) -> pl.Expr:
    """Limit-down recovery (跌停翻紅): the raw low touched the limit-down price and the raw close
    ended above the previous close, both on the raw scale.

    The one definition of signal_limit_down_recovery; a close between the limit and the
    previous close is only an opened limit, not a recovery.
    """
    return (raw_low <= limit_down + LIMIT_TOUCH_TOLERANCE) & (raw_close > prev_raw_close)


def compute_limit_signals(
    df: pl.DataFrame,
    instruments: pl.DataFrame,
    needed: set[str] | None = None,
    today: date | None = None,
) -> pl.DataFrame:
    """计算涨跌停相关信号。

    产出:
      signal_limit_up, consecutive_limit_ups
      signal_limit_down, consecutive_limit_downs
      signal_limit_down_recovery (跌停翘板)
      signal_broken_limit_up (炸板: 最高价触及涨停价但收盘未封住)

    输入必须包含: symbol, date, raw_close, raw_high, raw_low, open, high, low, close,
                  change_pct, vol_ratio_5d。
    Limits follow the Security Master rules and the official ref_limit_* of corporate-action
    sessions; *instruments* only supplies float_shares, the turnover denominator, which only
    *today* (default cn_today()) may use.
    """
    if df.is_empty():
        return df

    want = set(LIMIT_SIGNAL_OUTPUTS) if needed is None else set(needed) & set(LIMIT_SIGNAL_OUTPUTS)
    if not want:
        return df

    need_up = bool(want & {"signal_limit_up", "consecutive_limit_ups", "signal_broken_limit_up"})
    need_down = bool(want & {"signal_limit_down", "consecutive_limit_downs", "signal_limit_down_recovery"})
    need_price_limits = need_up or need_down

    inst_cols = ["symbol"]
    if "turnover_rate" in want and "float_shares" in instruments.columns:
        inst_cols.append("float_shares")
    df = df.join(instruments.select(inst_cols).unique(subset=["symbol"]), on="symbol", how="left", suffix="_inst")

    if "turnover_rate" in want:
        df = apply_historical_float_shares(df, today=today or cn_today())

    if "turnover_rate" in want and "float_shares" in df.columns and "volume" in df.columns:
        df = df.with_columns(
            pl.when(pl.col("float_shares") > 0)
              .then(turnover_rate_pct_expr())
              .otherwise(None)
              .alias("turnover_rate")
        )
    elif "turnover_rate" in want and "turnover_rate" not in df.columns:
        df = df.with_columns(pl.lit(None).cast(pl.Float64).alias("turnover_rate"))

    # 前一日参考收盘价（交易所涨跌停基准价）
    # 仅在 adj_factor 发生变化（除权除息 XD/DR）时使用前复权昨收作为交易所参考价;
    # 否则使用原始 raw_close.shift(1) 以避免浮点精度误差。
    if not need_price_limits:
        cleanup = [c for c in ("name", "float_shares", "limit_up", "limit_down") if c in df.columns]
        return df.drop(cleanup)

    _adj_today = pl.col("close") / pl.col("raw_close")
    _adj_yesterday = pl.col("close").shift(1).over("symbol") / pl.col("raw_close").shift(1).over("symbol")
    _adj_changed = (_adj_today - _adj_yesterday).abs() > 1e-6
    # The limit base must stay on today's raw scale, whatever events follow today.
    prev_raw_close = (
        pl.when(_adj_changed & (pl.col("close") > 0))
        .then(pl.col("close").shift(1).over("symbol") * pl.col("raw_close") / pl.col("close"))
        .otherwise(pl.col("raw_close").shift(1).over("symbol"))  # 正常: 使用原始昨收
    )
    if "ref_prev_close" in df.columns:
        prev_raw_close = pl.coalesce(pl.col("ref_prev_close"), prev_raw_close)
    # On these rows the exchange's base is not an earlier close, so no fallback base applies.
    if "ref_base_missing" in df.columns:
        prev_raw_close = pl.when(pl.col("ref_base_missing").fill_null(False)).then(None).otherwise(prev_raw_close)
    df = df.with_columns(prev_raw_close.alias("_prev_raw_close"))

    from app.taiwan.market_rules import PriceLimitModel

    df = _join_taiwan_limit_profile(df, pl.col("date"))
    effective_limit_up, effective_limit_down = PriceLimitModel.polars_limit_prices(
        pl.col("_prev_raw_close"), pl.col("_tw_limit_pct"), pl.col("_tw_is_etf").fill_null(False)
    )
    # Taiwan corporate-action bars carry the exchange's own limits (raw scale), except inside an
    # initial listing's no-limit window.
    if "ref_limit_up" in df.columns:
        effective_limit_up = pl.coalesce(
            pl.when(~pl.col("_tw_ipo_session")).then(pl.col("ref_limit_up")), effective_limit_up
        )
    if "ref_limit_down" in df.columns:
        effective_limit_down = pl.coalesce(
            pl.when(~pl.col("_tw_ipo_session")).then(pl.col("ref_limit_down")), effective_limit_down
        )
    effective_exprs: list[pl.Expr] = []
    if need_up:
        effective_exprs.append(effective_limit_up.alias("_effective_limit_up"))
    if need_down:
        effective_exprs.append(effective_limit_down.alias("_effective_limit_down"))
    df = df.with_columns(effective_exprs)

    # ── signal_limit_up ──
    if need_up:
        df = df.with_columns(
        pl.when(
            pl.col("_prev_raw_close").is_not_null()
            & (pl.col("_prev_raw_close") > 0)
            & (pl.col("raw_close") > 0)
        ).then(
            pl.col("raw_close") >= (pl.col("_effective_limit_up") - 0.005)
        ).otherwise(None).cast(pl.Boolean)
        .alias("signal_limit_up")
        )

    if "consecutive_limit_ups" in want:
        df = with_limit_streak(df, "signal_limit_up", "consecutive_limit_ups")

    # ── signal_limit_down ──
    if need_down:
        df = df.with_columns(
        pl.when(
            pl.col("_prev_raw_close").is_not_null()
            & (pl.col("_prev_raw_close") > 0)
            & (pl.col("raw_close") > 0)
        ).then(
            pl.col("raw_close") <= (pl.col("_effective_limit_down") + 0.005)
        ).otherwise(None).cast(pl.Boolean)
        .alias("signal_limit_down")
        )

    if "consecutive_limit_downs" in want:
        df = with_limit_streak(df, "signal_limit_down", "consecutive_limit_downs")

    # ── signal_limit_down_recovery (跌停翘板) ──
    if "signal_limit_down_recovery" in want:
        df = df.with_columns(
        pl.when(
            pl.col("_prev_raw_close").is_not_null()
            & (pl.col("_prev_raw_close") > 0)
            & (pl.col("raw_low") > 0)
        ).then(
            limit_down_recovery(
                pl.col("raw_low"),
                pl.col("raw_close"),
                pl.col("_effective_limit_down"),
                pl.col("_prev_raw_close"),
            )
        ).otherwise(None).cast(pl.Boolean)
        .alias("signal_limit_down_recovery")
        )

    # ── signal_broken_limit_up (炸板) ──
    # 条件: 最高价曾触及涨停价 + 最终没有封住涨停
    if "signal_broken_limit_up" in want:
        df = df.with_columns(
        pl.when(
            pl.col("_prev_raw_close").is_not_null()
            & (pl.col("_prev_raw_close") > 0)
            & (pl.col("raw_high") > 0)
        ).then(
            (~pl.col("signal_limit_up").fill_null(False))               # 最终没封住涨停
            & (pl.col("raw_high") >= pl.col("_effective_limit_up") - 0.005)  # 曾触及涨停价
        ).otherwise(None).cast(pl.Boolean)
        .alias("signal_broken_limit_up")
        )

    # 清理临时列 + JOIN 引入的 instruments 列 (不存入 enriched)
    cleanup = ["_prev_raw_close", "_tw_limit_pct", "_tw_is_etf", "_tw_ipo_session",
               "_effective_limit_up", "_effective_limit_down"]
    # 清理 join 产生的重复列
    for c in df.columns:
        if c.endswith("_inst"):
            cleanup.append(c)
    # Instrument columns never reach the enriched schema.
    for c in ["name", "float_shares", "limit_up", "limit_down"]:
        if c in df.columns and c != "turnover_rate":
            cleanup.append(c)
    internal_outputs = {"signal_limit_up", "signal_limit_down"} - want
    cleanup.extend(c for c in internal_outputs if c in df.columns)
    df = df.drop([c for c in cleanup if c in df.columns])

    return df


def compute_all(
    df: pl.DataFrame,
    instruments: pl.DataFrame | None = None,
    today: date | None = None,
    *,
    late_custom_signals: bool = True,
) -> pl.DataFrame:
    """从 OHLCV 计算全套指标 + 信号。一站式调用。

    输入: symbol, date, open, high, low, close, volume, amount, raw_close
    late_custom_signals=False leaves inject_late_custom_signals to a caller that restates its inputs.
    """
    df = compute_indicators(df)
    df = compute_signals(df)
    if instruments is not None and not instruments.is_empty():
        df = compute_limit_signals(df, instruments, today=today)
    if late_custom_signals:
        df = inject_late_custom_signals(df)

    # 清理 NaN / Inf
    float_cols = [c for c in df.columns if df[c].dtype.is_float()]
    if float_cols:
        df = df.with_columns([
            pl.when(pl.col(c).is_nan() | pl.col(c).is_infinite())
              .then(None)
              .otherwise(pl.col(c))
              .alias(c)
            for c in float_cols
        ])

    return df


def filter_halt_days(df: pl.DataFrame) -> pl.DataFrame:
    """过滤停牌日。

    停牌日的 open/high 必然为 0 (无集合竞价)。注意 close 可能被数据源
    填充为前收盘价而非 0, 因此不能用 "OHLC 全零" 判断, 否则会漏过这类
    停牌记录 (如 *ST 撤销风险警示的停牌日), 污染 MA/ATR 等指标。
    """
    if df.is_empty() or "open" not in df.columns or "high" not in df.columns:
        return df
    return df.filter(~((pl.col("open") == 0) & (pl.col("high") == 0)))


# ================================================================
# Pipeline: 盘后全量计算 + 写入
# ================================================================

def compute_enriched(
    raw: pl.DataFrame,
    factors: pl.DataFrame | None = None,
    instruments: pl.DataFrame | None = None,
    today: date | None = None,
    *,
    late_custom_signals: bool = True,
) -> pl.DataFrame:
    """对原始日 K 应用前复权 + 全量计算指标 + 信号, 产出完整 enriched (含全部指标列)。

    输入应包含至少: symbol, date, open, high, low, close, volume (可选 amount)。
    如果提供了 factors, 先应用前复权再算指标。
    如果提供了 instruments, 计算涨跌停信号和换手率。
    """
    if raw.is_empty():
        return raw

    # 过滤停牌日 (会污染指标计算)
    raw = filter_halt_days(raw)

    if raw.is_empty():
        return raw

    # 保留不复权原始价格（涨停/炸板/跌停判断需用不复权价格）
    raw = raw.with_columns(
        pl.col("close").alias("raw_close"),
        pl.col("high").alias("raw_high"),
        pl.col("low").alias("raw_low"),
    )

    # 应用前复权（只改 open/high/low/close，raw_close 不受影响）
    if factors is not None and not factors.is_empty():
        raw = _apply_adj_factor(raw, factors)

    # 排序
    df = raw.sort(["symbol", "date"])

    # 全量计算指标 + 信号
    df = compute_all(df, instruments=instruments, today=today, late_custom_signals=late_custom_signals)

    return df


def compute_enriched_single(daily_for_symbol: pl.DataFrame) -> pl.DataFrame:
    """单股版本 — Free 用户用,拉下来单股 K 后即时计算全部指标+信号返回给前端。"""
    if daily_for_symbol.is_empty():
        return daily_for_symbol

    # 过滤停牌
    daily_for_symbol = filter_halt_days(daily_for_symbol)
    if daily_for_symbol.is_empty():
        return daily_for_symbol

    # 保留 raw_close 用于涨停判断
    daily_for_symbol = daily_for_symbol.with_columns(pl.col("close").alias("raw_close"))

    # 即时计算全套指标 + 信号 (无复权因子, 无 instruments)
    return compute_all(daily_for_symbol)


# Enriched columns that carry a price, so forward adjustment rescales them; volume ones scale inversely.
PRICE_COLUMNS = (
    "open", "high", "low", "close", "prev_close", "change_amount",
    "ema5", "ema10", "ema20", "ema30", "ema60", "macd_dif", "macd_dea", "macd_hist",
    "ma5", "ma10", "ma20", "ma30", "ma60", "boll_upper", "boll_lower",
    "atr_14", "high_60d", "low_60d",
)
VOLUME_COLUMNS = ("volume", "vol_ma5", "vol_ma10")


# 台股法定涨跌幅逐 symbol 缓存: Security Master 不随盘中轮询变动,
# 避免每轮都重新查 get_instrument + get_price_limit_pct。
# Holds no initial-listing window: the window depends on the session calendar, which grows daily.
_taiwan_limit_profile_cache: dict[str, tuple[float | None, bool, TaiwanInstrument | None]] = {}


def invalidate_taiwan_limit_profile_cache() -> None:
    """清空台股涨跌幅缓存 (Security Master 重新加载后调用)。"""
    _taiwan_limit_profile_cache.clear()


def taiwan_limit_profile(symbols: list[str], sessions: ListingSessions | None = None) -> pl.DataFrame:
    """按 symbol 查询台股法定涨跌幅比例与跳动单位分类 (逐 symbol 缓存)。

    非台股 symbol (Security Master 查无) 或 ETF underlying_scope 未确认
    (MarketProfileBridge.get_price_limit_pct 抛 ValueError) 一律记为
    limit_pct=None, 由调用方按 NO_LIMIT 处理 (对应列置 null)。

    ipo_start / ipo_end bound an ordinary stock's initial-listing window on *sessions* (default: the
    stored calendar), null without one; apply them with PriceLimitModel.polars_session_limit_pct.
    """
    from app.taiwan.universe import get_security_master
    from app.taiwan.universe.models import MarketProfileBridge

    # Reloading a master replaced by another process clears the profiles cached from the old one.
    if any(s in _taiwan_limit_profile_cache for s in symbols):
        getattr(get_security_master(), "reload_if_changed", lambda: None)()
    # A lookup may load the master, which clears the shared cache, so results are read from this local copy.
    profiles = {s: _taiwan_limit_profile_cache.get(s) for s in symbols}
    missing = [s for s, profile in profiles.items() if profile is None]
    if missing:
        master = get_security_master()
        for sym in missing:
            inst = master.get_instrument(sym)
            if inst is None:
                profiles[sym] = (None, False, None)
            else:
                try:
                    pct = MarketProfileBridge.get_price_limit_pct(inst)
                except ValueError:
                    pct = None
                profiles[sym] = (pct, inst.instrument_type == "etf", inst if pct is not None else None)
            _taiwan_limit_profile_cache[sym] = profiles[sym]

    windows: dict[str, tuple[date, date] | None] = {}
    limited = {s: profile[2] for s, profile in profiles.items() if profile[2] is not None}
    if limited:
        if sessions is None:
            from app.taiwan.daily_store import listing_sessions

            sessions = listing_sessions()
        windows = {s: MarketProfileBridge.ipo_no_limit_window(inst, sessions) for s, inst in limited.items()}

    schema = {
        "symbol": pl.Utf8, "limit_pct": pl.Float64, "is_etf_tick": pl.Boolean,
        "ipo_start": pl.Date, "ipo_end": pl.Date,
    }
    rows = []
    for s in symbols:
        window = windows.get(s)
        rows.append({
            "symbol": s,
            "limit_pct": profiles[s][0],
            "is_etf_tick": profiles[s][1],
            "ipo_start": window[0] if window else None,
            "ipo_end": window[1] if window else None,
        })
    return pl.DataFrame(rows, schema=schema)


LIMIT_DISTANCE_COLUMNS = ("distance_to_upper_limit", "distance_to_lower_limit")
_LIMIT_DISTANCE_INPUTS = frozenset({"close", "prev_close", "raw_close", "ref_limit_up", "ref_limit_down"})


def limit_distance_inputs(dependencies: frozenset[str]) -> frozenset[str]:
    """*dependencies* with the limit-distance columns replaced by the columns they are computed from."""
    if dependencies.isdisjoint(LIMIT_DISTANCE_COLUMNS):
        return dependencies
    return (dependencies - set(LIMIT_DISTANCE_COLUMNS)) | _LIMIT_DISTANCE_INPUTS


def inject_custom_signals(
    df: pl.DataFrame,
    exprs: dict[str, pl.Expr],
    needed: set[str] | None = None,
) -> pl.DataFrame:
    """custom_signals.inject, computing the limit-distance columns the injected signals read.

    Columns computed here are dropped again, so the enriched schema stays the same.
    """
    from app.strategy import custom_signals

    wanted = exprs if needed is None else {name: e for name, e in exprs.items() if name in needed}
    read = set().union(*custom_signals.expression_dependencies(wanted).values()) if wanted else set()
    missing = [c for c in LIMIT_DISTANCE_COLUMNS if c in read and c not in df.columns]
    if df.is_empty() or not missing:
        return custom_signals.inject(df, exprs, needed=needed)
    added = [c for c in LIMIT_DISTANCE_COLUMNS if c not in df.columns]
    return custom_signals.inject(add_limit_distance_columns(df), exprs, needed=needed).drop(added)


def add_limit_distance_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Add distance_to_upper_limit / distance_to_lower_limit (decimal, 0.01 = 1%).

    The limit base is prev_close, the statutory reference, never close.
    No-limit symbols (foreign-component ETFs incl. bond ETFs, unconfirmed underlying_scope) get
    null in both columns, so filters on them drop those rows (fail-closed).
    """
    from app.taiwan.corporate_actions import REF_LIMIT_DOWN, REF_LIMIT_UP
    from app.taiwan.market_rules import PriceLimitModel

    out_cols = ("distance_to_upper_limit", "distance_to_lower_limit")
    if df.is_empty() or "symbol" not in df.columns or "close" not in df.columns or "prev_close" not in df.columns:
        return df.with_columns([pl.lit(None).cast(pl.Float64).alias(c) for c in out_cols])

    profile = taiwan_limit_profile(df["symbol"].unique().to_list())
    trade_date = pl.col("date") if "date" in df.columns else pl.lit(cn_today())
    df = df.join(profile, on="symbol", how="left", maintain_order="left").with_columns(
        PriceLimitModel.polars_session_limit_pct(
            pl.col("limit_pct"), trade_date, pl.col("ipo_start"), pl.col("ipo_end")
        ).alias("limit_pct")
    )

    # Limits and ticks live on the raw price scale; prev_close and close may be forward-adjusted.
    if "raw_close" in df.columns:
        to_raw = pl.when(pl.col("close") > 0).then(pl.col("raw_close") / pl.col("close")).otherwise(1.0)
        ref = pl.col("prev_close") * to_raw
        close = pl.col("raw_close")
    else:
        ref = pl.col("prev_close")
        close = pl.col("close")
    limit_up, limit_down = PriceLimitModel.polars_limit_prices(
        ref, pl.col("limit_pct"), pl.col("is_etf_tick").fill_null(False)
    )
    # A corporate-action session uses the limits the exchange published, as signal_limit_up does.
    if REF_LIMIT_UP in df.columns:
        limit_up = pl.coalesce(pl.col(REF_LIMIT_UP), limit_up)
    if REF_LIMIT_DOWN in df.columns:
        limit_down = pl.coalesce(pl.col(REF_LIMIT_DOWN), limit_down)

    usable = (
        pl.col("limit_pct").is_not_null()
        & ref.is_not_null() & (ref > 0)
        & close.is_not_null()
    )
    raw_up_dist = (limit_up - close) / limit_up
    raw_down_dist = (close - limit_down) / limit_down
    df = df.with_columns([
        pl.when(usable & (limit_up > 0))
          .then(pl.when(raw_up_dist > 0).then(raw_up_dist).otherwise(0.0))
          .otherwise(None)
          .alias("distance_to_upper_limit"),
        pl.when(usable & (limit_down > 0))
          .then(pl.when(raw_down_dist > 0).then(raw_down_dist).otherwise(0.0))
          .otherwise(None)
          .alias("distance_to_lower_limit"),
    ])
    return df.drop(["limit_pct", "is_etf_tick", "ipo_start", "ipo_end"])
