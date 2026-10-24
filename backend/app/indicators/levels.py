"""Key price levels: pure functions over forward-adjusted daily bars, with no IO.

Each group of LEVEL_TYPES yields points {value, label, type, side, strength?, rank?} for the chart
and price alerts. A group that cannot be computed is empty without affecting the others, and
non-finite or non-positive values are dropped.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Any

import numpy as np
import polars as pl

from app.indicators.pipeline import ema_values, wilder_values
from app.taiwan.market_rules import TickSizeClass, TickSizeModel

logger = logging.getLogger(__name__)


# ================================================================
# 输出结构
# ================================================================

class PriceLevel:
    """单个价位点的数据结构(用 dict 表达,这里只作文档说明)。

    {
      "value": 12.34,          # 价格
      "label": "壓力位 R1",     # 显示标签
      "type": "pivot",         # 类型分组(同类型用一个开关按钮控制显隐)
      "side": "resistance",    # 方向:resistance(压力) / support(支撑) / neutral
      "strength": "medium",    # 强度:strong / medium / weak(可选,影响线型)
      "rank": 1,               # 档位(仅 pivot 有):0=P,1=R1/S1,2=R2/S2,3=R3/S3
                               #   前端按"显示到第几档"过滤,非 pivot 点位无此字段
    }
    """


# 价位分组 → 开关 key。前端按这个 type 显隐。
LEVEL_TYPES = {
    "sr": "壓力支撐",        # 成交密集区(价量:Volume Profile POC + 高成交密集区)
    "pivot": "樞軸點",        # 经典 Pivot P/R/S
    "extreme": "前高前低",    # 60/250 日极值 + 近期 swing 高低点
    "boll": "布林帶",         # MA20 ± 2σ,标准差波动带(参考性,非真实支撑压力)
    "keltner_s": "Keltner短期",  # EMA20 ± 2×ATR10
    "keltner_m": "Keltner中期",  # EMA60 ± 2.5×ATR10
    "keltner_l": "Keltner長期",  # EMA120 ± 3×ATR10
    "atr_stop": "ATR波動通道",    # close±nATR 动态波动带(中性命名,非操作指令)
    "gap": "缺口位",          # 未回补跳空缺口
    "fib": "斐波那契",        # 回撤位 0.236~0.786
    "round": "整數關口",      # 心理整数位
}

# Keltner tiers: group key -> (EMA window, ATR multiple, label prefix).
KELTNER_TIERS: dict[str, tuple[int, float, str]] = {
    "keltner_s": (20, 2.0, "短期"),
    "keltner_m": (60, 2.5, "中期"),
    "keltner_l": (120, 3.0, "長期"),
}
KELTNER_ATR_WINDOW = 10


# ================================================================
# 1. 压力位 / 支撑位 —— 成交量分布 (Volume Profile)
# ================================================================

def _support_resistance(df: pl.DataFrame, bins: int = 40) -> list[dict]:
    """Chip distribution (turnover-decay model), the mainstream A-share support/resistance method.

    Based on the "chip distribution" patent model of Dazhihui / Tonghuashun / Tongdaxin
    (CN109711994A), it estimates how many shares are still held at each price (the holding-cost
    distribution), unlike an overseas Volume Profile, which piles up historical volume. The two
    differ in decay:
      - Volume Profile: no decay; volume accumulates forever (where shares once changed hands)
      - Chip distribution: decays by turnover (who still holds now, the true holding cost)

    Daily recurrence, oldest to newest:
        chips at each price after today's close = yesterday's chips at that price * (1 - turnover)
                                                  + today's volume spread over its high~low buckets

    A turnover_rate of x% today means x% of yesterday's chips at every price was sold,
    while today's new chips spread over today's price range. Hence:
      - low-turnover stocks shed old chips slowly (a long quiet range keeps its trapped holders)
      - high-turnover stocks shed old chips quickly (recent costs dominate)
    Unlike a fixed time decay, a suspended stock has zero turnover and keeps its chips, which a
    time decay would wrongly erode.

    Volume is spread over each bar's high~low buckets rather than placed at the (high+low)/2
    midpoint, so a wide-range bar does not distort the midpoint price.

    Dense-zone output:
      - POC (point of control): the bucket holding the most chips (the most common cost), strong
      - other high-chip zones: above the mean, medium, at most 2

    Without a finite turnover on every bar no decay is applied, and the levels are labelled as an
    undecayed volume profile over the whole window instead of a chip distribution.
    """
    if df.is_empty() or "volume" not in df.columns or df.height < 20:
        return []

    hi = float(df["high"].max())
    lo = float(df["low"].min())
    if not (hi > lo > 0):
        return []

    n = df.height

    # 桶边界:bins 个桶需要 bins-1 个内部 break
    step = (hi - lo) / bins
    edges = [lo + i * step for i in range(bins + 1)]

    # turnover_rate is a percentage: 1.23 means 1.23%.
    turnovers: list[float | None] = (
        df["turnover_rate"].cast(pl.Float64, strict=False).fill_nan(None).to_list()
        if "turnover_rate" in df.columns
        else [None] * n
    )
    chip_model = all(t is not None for t in turnovers)

    highs = df["high"].to_list()
    lows = df["low"].to_list()
    vols = df["volume"].to_list()

    # 逐日迭代: 保留各桶的"当前筹码量"。
    # 顺序: 从老(i=0)到新(i=n-1)。每日先把存量按 (1 - turnover) 衰减, 再叠加当日新增。
    chips = [0.0] * bins
    for i in range(n):
        # 换手率正常 0~100, 异常值(负/超大)钳制到 [0, 1] 区间比例
        decay_ratio = 1.0 - max(0.0, min(turnovers[i], 100.0)) / 100.0 if chip_model else 1.0

        # 1) 存量筹码按今日换手率衰减(被卖掉的部分移除)
        if decay_ratio < 1.0:
            for k in range(bins):
                chips[k] *= decay_ratio

        # 2) 当日新增成交按 high~low 区间分摊到对应价位桶
        v = vols[i] or 0
        if v > 0:
            k_low = min(int((lows[i] - lo) / step), bins - 1)
            k_high = min(int((highs[i] - lo) / step), bins - 1)
            if k_low > k_high:
                k_low, k_high = k_high, k_low
            if k_high < 0 or k_low >= bins:
                continue
            k_low = max(k_low, 0)
            k_high = min(k_high, bins - 1)
            share = v / (k_high - k_low + 1)
            for k in range(k_low, k_high + 1):
                chips[k] += share

    # 只保留有筹码的桶
    bin_ids = [k for k in range(bins) if chips[k] > 0]
    if not bin_ids:
        return []
    vals = [chips[k] for k in bin_ids]
    mean_val = sum(vals) / len(vals) if vals else 0

    def bin_mid(bin_id: int) -> float:
        return (edges[bin_id] + edges[bin_id + 1]) / 2

    close = float(df.tail(1)["close"][0])
    if chip_model:
        poc_label, zone_label = "成交密集區(POC)", "成交密集區"
    else:
        poc_label, zone_label = "量能POC", "量能密集區"

    out: list[dict] = []
    # POC: 当前筹码量最大的桶(最多人持仓的成本区)
    poc_pos = max(range(len(vals)), key=lambda i: vals[i])
    poc_mid = bin_mid(bin_ids[poc_pos])
    out.append({"value": poc_mid, "label": poc_label,
                "type": "sr", "side": _side(poc_mid, close), "strength": "strong"})

    # 其他高筹码区(高于均值, 排除 POC), 按筹码量降序取 2 个
    candidates = [(i, v) for i, v in enumerate(vals) if v > mean_val and i != poc_pos]
    candidates.sort(key=lambda x: x[1], reverse=True)
    for i, _v in candidates[:2]:
        mid = bin_mid(bin_ids[i])
        out.append({"value": mid, "label": zone_label,
                    "type": "sr", "side": _side(mid, close), "strength": "medium"})
    return out


# ================================================================
# 2. 枢轴点 (Pivot Point) —— 经典公式,基于最近完整交易日
# ================================================================

def _pivot_points(df: pl.DataFrame, last_bar_live: bool = False) -> list[dict]:
    """Classic pivots P = (H+L+C)/3 with R1-R3 and S1-S3, from the newest completed session.

    A live last bar is still forming, so the bar before it is the base.
    """
    if df.height < (2 if last_bar_live else 1):
        return []
    base = df.slice(-2, 1) if last_bar_live else df.tail(1)
    h = base["high"][0]
    l = base["low"][0]
    c = base["close"][0]
    if not _ok(h) or not _ok(l) or not _ok(c):
        return []

    h, l, c = float(h), float(l), float(c)
    p = (h + l + c) / 3
    r1 = 2 * p - l
    s1 = 2 * p - h
    r2 = p + (h - l)
    s2 = p - (h - l)
    r3 = h + 2 * (p - l)
    s3 = l - 2 * (h - p)

    def lv(v: float, label: str, side: str, strength: str, rank: int) -> dict:
        # rank:档位标记,前端据此按"显示到第几档"过滤
        #   0 = 枢轴位 P(始终显示)
        #   1 = R1/S1(第一档压力/支撑)
        #   2 = R2/S2(第二档)
        #   3 = R3/S3(第三档,极端,实际很少触及)
        return {"value": v, "label": label, "type": "pivot",
                "side": side, "strength": strength, "rank": rank}

    return [
        lv(p, "樞軸位 P", "neutral", "strong", 0),
        lv(r1, "壓力位 R1", "resistance", "medium", 1),
        lv(r2, "壓力位 R2", "resistance", "medium", 2),
        lv(r3, "壓力位 R3", "resistance", "weak", 3),
        lv(s1, "支撐位 S1", "support", "medium", 1),
        lv(s2, "支撐位 S2", "support", "medium", 2),
        lv(s3, "支撐位 S3", "support", "weak", 3),
    ]


# ================================================================
# 3. 前高 / 前低 —— 60 / 120 / 250 日极值
# ================================================================

def _extreme_levels(df: pl.DataFrame) -> list[dict]:
    """关键前高 / 前低 —— 历史极值 + 近期 swing 高低点(收敛后)。

    设计:把所有"前高前低"类点位集中在本组,与 sr(通道)区分:
      - 60 日极值:近一季度高低点(短期参照)
      - 250 日极值:年度高低点(牛熊分界参照);跳过 120 日(被 250 日包含,信息冗余)
      - swing 高低点:近期局部转折点,每侧只取距当前价最近的 2 个
    """
    if df.is_empty():
        return []
    close = float(df.tail(1)["close"][0]) if "close" in df.columns else None
    out: list[dict] = []

    # —— 历史极值(只取 60 / 250,避免中间档冗余)——
    for n in (60, 250):
        if df.height < n:
            continue
        sub = df.tail(n)
        hi = sub["high"].max()
        lo = sub["low"].min()
        if _ok(hi):
            out.append({"value": float(hi), "label": f"{n}日新高",
                        "type": "extreme", "side": "resistance", "strength": "strong"})
        if _ok(lo):
            out.append({"value": float(lo), "label": f"{n}日新低",
                        "type": "extreme", "side": "support", "strength": "strong"})

    # —— 近期 swing 高低点(每侧只取距当前价最近的 2 个,避免点位爆炸)——
    win = 5
    if df.height > win * 2 and close:
        highs = df["high"].to_list()
        lows = df["low"].to_list()
        swing_highs: list[float] = []
        swing_lows: list[float] = []
        for i in range(win, len(highs) - win):
            if highs[i] == max(highs[i - win:i + win + 1]):
                swing_highs.append(float(highs[i]))
            if lows[i] == min(lows[i - win:i + win + 1]):
                swing_lows.append(float(lows[i]))

        # Filter by side before merging, so a group straddling the close cannot hide a valid level.
        agg_h = _aggregate_levels([v for v in swing_highs if v > close * 1.001], 0.01)
        agg_h.sort(key=lambda v: abs(v - close))
        for v in agg_h[:2]:
            out.append({"value": v, "label": "前高",
                        "type": "extreme", "side": "resistance", "strength": "medium"})

        agg_l = _aggregate_levels([v for v in swing_lows if v < close * 0.999], 0.01)
        agg_l.sort(key=lambda v: abs(v - close))
        for v in agg_l[:2]:
            out.append({"value": v, "label": "前低",
                        "type": "extreme", "side": "support", "strength": "medium"})

    return out


# ================================================================
# 4. 波动通道 —— 布林带 + Keltner 三档,各自独立开关
# ================================================================

def keltner_bands(df: pl.DataFrame, window: int, mult: float) -> tuple[pl.Series, pl.Series] | None:
    """Per-bar Keltner (upper, lower): EMA(window) ± mult * ATR10, Linda Raschke's modern form.

    The 20-day tier at 2 ATR is Raschke's standard; the 60- and 120-day tiers are this project's
    longer variants of the same construction. EMA is SMA-seeded and ATR is Wilder's, as in the
    pipeline; a published ema{window} column is preferred over one computed on this window.
    Returns None without high, low and close.
    """
    if df.is_empty() or not {"high", "low", "close"} <= set(df.columns):
        return None
    offsets = np.array([0, df.height], dtype=np.int64)
    close = df["close"].cast(pl.Float64).to_numpy()
    high = df["high"].cast(pl.Float64).to_numpy()
    low = df["low"].cast(pl.Float64).to_numpy()
    ema_col = f"ema{window}"
    ema = (
        df[ema_col].cast(pl.Float64).to_numpy()
        if ema_col in df.columns
        else ema_values(close, offsets, window)
    )
    prev_close = np.concatenate(([np.nan], close[:-1]))
    true_range = np.maximum.reduce(
        [high - low, np.abs(high - prev_close), np.abs(low - prev_close)]
    )
    atr = wilder_values(true_range, offsets, KELTNER_ATR_WINDOW)
    return (
        pl.Series("upper", ema + mult * atr, dtype=pl.Float64, nan_to_null=True),
        pl.Series("lower", ema - mult * atr, dtype=pl.Float64, nan_to_null=True),
    )


def _keltner_band(df: pl.DataFrame, type_key: str) -> list[dict]:
    window, mult, label_short = KELTNER_TIERS[type_key]
    bands = keltner_bands(df, window, mult)
    close = df.tail(1)["close"][0] if "close" in df.columns else None
    if bands is None or not _ok(close):
        return []
    close = float(close)
    out: list[dict] = []
    for series, edge in zip(bands, ("上軌", "下軌"), strict=True):
        v = series[-1]
        if _ok(v):
            out.append({"value": float(v), "label": f"{label_short}通道{edge}",
                        "type": type_key, "side": _side(float(v), close), "strength": "medium"})
    return out


def _boll_channel(df: pl.DataFrame) -> list[dict]:
    """布林带上下轨(MA20 ± 2σ)。

    基于标准差的波动带,反映价格相对均线的统计偏离;非真实支撑压力,
    仅作波动边界参考。数据直接取预计算列 boll_upper/boll_lower。
    """
    if df.is_empty() or "boll_upper" not in df.columns or "boll_lower" not in df.columns:
        return []
    last = df.tail(1)
    close = float(last["close"][0]) if "close" in df.columns else 0
    if not close:
        return []
    bu = last["boll_upper"][0]
    bl = last["boll_lower"][0]
    if not _ok(bu) or not _ok(bl):
        return []
    bu, bl = float(bu), float(bl)
    out = [
        {"value": bu, "label": "布林上軌",
         "type": "boll", "side": _side(bu, close), "strength": "medium"},
        {"value": bl, "label": "布林下軌",
         "type": "boll", "side": _side(bl, close), "strength": "medium"},
    ]
    # 布林中轨 = MA20(多空平衡线,价格在其上下分强弱);数据层已预计算 ma20
    if "ma20" in df.columns:
        mid = last["ma20"][0]
        if _ok(mid):
            mid = float(mid)
            out.append({"value": mid, "label": "布林中軌",
                        "type": "boll", "side": _side(mid, close), "strength": "medium"})
    return out


def _keltner_short(df: pl.DataFrame) -> list[dict]:
    return _keltner_band(df, "keltner_s")


def _keltner_mid(df: pl.DataFrame) -> list[dict]:
    return _keltner_band(df, "keltner_m")


def _keltner_long(df: pl.DataFrame) -> list[dict]:
    return _keltner_band(df, "keltner_l")


# ================================================================
# 5. ATR 波动通道 —— close ± n × ATR,动态波动带
# ================================================================

def _atr_stops(df: pl.DataFrame) -> list[dict]:
    """基于 ATR 的动态波动通道(close ± n×ATR)。

    ATR 衡量平均真实波幅,close ± n×ATR 刻画围绕现价的波动带边界:
      - 上轨:close + 2×ATR  (波动上沿)
      - 下轨:close - 2×ATR  (波动下沿)
      - 近端波动带:close ± 1.5×ATR (中短期波动参考)
    命名采用中性"轨道"措辞,客观描述波动范围,不含操作指令。
    """
    if df.is_empty() or "atr_14" not in df.columns:
        return []
    last = df.tail(1)
    close = last["close"][0]
    atr = last["atr_14"][0]
    if not _ok(close) or not _ok(atr):
        return []
    close, atr = float(close), float(atr)

    def lv(v: float, label: str, side: str, strength: str) -> dict:
        return {"value": v, "label": label, "type": "atr_stop",
                "side": side, "strength": strength}

    return [
        lv(close + 2 * atr, "ATR 上軌(+2)", "resistance", "medium"),
        lv(close + 1.5 * atr, "ATR 上軌(+1.5)", "resistance", "weak"),
        lv(close - 1.5 * atr, "ATR 下軌(-1.5)", "support", "weak"),
        lv(close - 2 * atr, "ATR 下軌(-2)", "support", "medium"),
    ]


# ================================================================
# 6. 缺口位 (Gap) —— 未回补的跳空缺口
# ================================================================

def _gap_levels(df: pl.DataFrame, lookback: int = 120) -> list[dict]:
    """Unfilled price gaps of the last *lookback* bars, at most 3 per direction nearest the close.

    An upward gap lies between a bar's high and the next bar's higher low. A later low at or below
    its lower edge fills it, and it is dropped; a later low inside it fills it partially, and only
    the part below that low stays open. A gap left behind by a later gap in the same direction
    stays open. Downward gaps mirror this. Each level is the middle of the open part, and nearby
    levels within 0.5% are merged.
    """
    if df.is_empty() or df.height < 5:
        return []
    sub = df.tail(lookback) if df.height > lookback else df
    close = float(df.tail(1)["close"][0])
    highs = sub["high"].to_list()
    lows = sub["low"].to_list()

    # (bar where the gap formed, lower edge, upper edge)
    up_gaps: list[tuple[int, float, float]] = []
    dn_gaps: list[tuple[int, float, float]] = []
    for i in range(1, len(highs)):
        if _ok(highs[i]) and _ok(lows[i]) and _ok(highs[i - 1]) and _ok(lows[i - 1]):
            if lows[i] > highs[i - 1]:
                up_gaps.append((i, float(highs[i - 1]), float(lows[i])))
            elif highs[i] < lows[i - 1]:
                dn_gaps.append((i, float(highs[i]), float(lows[i - 1])))

    def _open_part(i: int, g_lo: float, g_hi: float, upward: bool) -> tuple[float, float] | None:
        for j in range(i + 1, len(highs)):
            if upward:
                if not _ok(lows[j]):
                    continue
                if lows[j] <= g_lo:
                    return None
                g_hi = min(g_hi, float(lows[j]))
            else:
                if not _ok(highs[j]):
                    continue
                if highs[j] >= g_hi:
                    return None
                g_lo = max(g_lo, float(highs[j]))
        return g_lo, g_hi

    def _unfilled(gaps: list[tuple[int, float, float]], upward: bool) -> list[float]:
        mids: list[float] = []
        for i, g_lo, g_hi in gaps:
            part = _open_part(i, g_lo, g_hi, upward)
            if part is not None:
                mids.append((part[0] + part[1]) / 2)
        agg = _aggregate_levels(mids, 0.005)
        agg.sort(key=lambda v: abs(v - close))
        return agg[:3]

    out: list[dict] = []
    for mid in _unfilled(up_gaps, upward=True):
        out.append({"value": mid, "label": "向上缺口",
                    "type": "gap", "side": _side(mid, close), "strength": "medium"})
    for mid in _unfilled(dn_gaps, upward=False):
        out.append({"value": mid, "label": "向下缺口",
                    "type": "gap", "side": _side(mid, close), "strength": "medium"})
    return out


# ================================================================
# 7. 斐波那契回撤 —— 基于近期波段的回撤位
# ================================================================

def _fibonacci_levels(df: pl.DataFrame, window: int = 120) -> list[dict]:
    """基于近期一段明确趋势的斐波那契回撤位。

    取近 window 个交易日的最高/最低点:
      - 若高点出现在低点之后(上涨波段):从低到高,回撤 = high - range × ratio
      - 若低点出现在高点之后(下跌波段):从高到低,回撤 = low + range × ratio
    比率:0.236 / 0.382 / 0.5 / 0.618 / 0.786
    """
    if df.is_empty() or df.height < 10:
        return []
    sub = df.tail(window) if df.height > window else df
    close = float(df.tail(1)["close"][0])

    highs = sub["high"].to_list()
    lows = sub["low"].to_list()
    hi_pos = highs.index(max(highs))
    lo_pos = lows.index(min(lows))
    hi_val = float(highs[hi_pos])
    lo_val = float(lows[lo_pos])
    if not _ok(hi_val) or not _ok(lo_val) or hi_val <= lo_val:
        return []

    ratios = [0.236, 0.382, 0.5, 0.618, 0.786]
    rng = hi_val - lo_val

    out: list[dict] = []
    # 判断波段方向:高点在低点之后 = 上涨波段(从低回撤)
    up_trend = hi_pos > lo_pos
    for r in ratios:
        if up_trend:
            val = hi_val - rng * r          # 从高点向下回撤
        else:
            val = lo_val + rng * r          # 从低点向上回撤
        out.append({"value": val, "label": f"Fib {int(r * 1000) / 10:.1f}%",
                    "type": "fib", "side": _side(val, close), "strength": "medium"})
    return out


# ================================================================
# 8. 整数关口 —— 心理支撑/阻力位
# ================================================================

# Ordinary-stock tick -> round-number step in ticks.
_ROUND_STEP_TICKS = {0.01: 50, 0.05: 20, 0.10: 50, 0.50: 20, 1.00: 50, 5.00: 20}


def _round_numbers(df: pl.DataFrame, pct: float = 0.10, max_count: int = 8) -> list[dict]:
    """Psychological round-number levels within *pct* of the close, at most *max_count*.

    The step follows the Taiwan ordinary-stock tick tier of the close: tick * _ROUND_STEP_TICKS,
    i.e. 0.5 under 10, 1 for 10-50, 5 for 50-100, 10 for 100-500, 50 for 500-1000 and 100 from
    1000. Levels within 1% of the close are dropped.
    """
    if df.is_empty():
        return []
    close = float(df.tail(1)["close"][0])
    if not _ok(close):
        return []

    tick = TickSizeModel.get_tick_size(close, TickSizeClass.ORDINARY_STOCK)
    step = round(tick * _ROUND_STEP_TICKS[tick], 4)

    lo = close * (1 - pct)
    hi = close * (1 + pct)
    # 找区间 [lo, hi] 内所有 step 的整数倍(严格限定在区间内)
    start = (int(lo / step) + (1 if lo % step > 0 else 0)) * step
    candidates: list[float] = []
    v = start
    while v <= hi:
        if v > 0:
            candidates.append(round(v, 2))
        v += step

    # 按距当前价从近到远排序,取前 max_count 个
    candidates.sort(key=lambda x: abs(x - close))
    out: list[dict] = []
    for v in candidates[:max_count]:
        # 过滤距当前价 <1% 的(太近,无分析价值)
        if abs(v - close) / close < 0.01:
            continue
        out.append({"value": v, "label": f"整數關口 {v:g}",
                    "type": "round", "side": _side(v, close), "strength": "weak"})
    return out


def compute_levels(
    df: pl.DataFrame,
    *,
    last_bar_live: bool = False,
    tick_class: TickSizeClass | None = None,
) -> dict[str, list[dict]]:
    """Compute every LEVEL_TYPES group, returning {group key: [points]}.

    last_bar_live marks the newest bar as a session still trading, which pivots skip. With a
    tick_class each value is snapped to the Taiwan tick of its raw-price equivalent: resistance
    rounds up, support down and neutral to the nearest tick. Without one, values keep 2 decimals.
    A group that raises is left empty.
    """
    if df.is_empty():
        return {k: [] for k in LEVEL_TYPES}

    builders = {
        "sr": _support_resistance,
        "pivot": lambda frame: _pivot_points(frame, last_bar_live),
        "extreme": _extreme_levels,
        "boll": _boll_channel,
        "keltner_s": _keltner_short,
        "keltner_m": _keltner_mid,
        "keltner_l": _keltner_long,
        "atr_stop": _atr_stops,
        "gap": _gap_levels,
        "fib": _fibonacci_levels,
        "round": _round_numbers,
    }
    snap = _tick_snapper(df, tick_class)
    out: dict[str, list[dict]] = {}
    for key, build in builders.items():
        try:
            points = build(df)
        except Exception as e:
            logger.warning("compute_levels %s failed: %s", key, e, exc_info=True)
            points = []
        snapped = ({**p, "value": snap(p["value"], p["side"])} for p in points if _ok(p["value"]))
        out[key] = [p for p in snapped if _ok(p["value"])]
    return out


def _tick_snapper(df: pl.DataFrame, tick_class: TickSizeClass | None):
    """Rounding of adjusted values, on the raw scale of the newest bar when a tick class is given."""
    if tick_class is None:
        return lambda value, side: round(value, 2)
    last = df.tail(1)
    raw, adjusted = (last[c][0] if c in df.columns else None for c in ("raw_close", "close"))
    factor = float(raw) / float(adjusted) if _ok(raw) and _ok(adjusted) else 1.0
    trade_date = last["date"][0] if "date" in df.columns else None
    if isinstance(trade_date, datetime):
        trade_date = trade_date.date()
    if not isinstance(trade_date, date):
        trade_date = None
    modes = {"resistance": "ceil", "support": "floor"}

    def snap(value: float, side: str) -> float:
        raw_value = TickSizeModel.round_order_price(
            value * factor, tick_class, mode=modes.get(side, "nearest"), trade_date=trade_date
        )
        return round(raw_value / factor, 4)

    return snap


def summarize_levels(levels: dict[str, list[dict]], close: float | None) -> str:
    """生成给 AI 提示词的价位摘要文本(紧凑,供上下文)。"""
    if not close:
        return "無價位數據"
    parts: list[str] = []
    # 当前价
    parts.append(f"當前價 {close:.2f}")
    # 每组取前 2 个最相关的(距当前价近的优先)
    for key, label in LEVEL_TYPES.items():
        pts = levels.get(key, [])
        if not pts:
            continue
        # 按距当前价排序,取前 2
        ranked = sorted(pts, key=lambda p: abs(p["value"] - close))[:2]
        desc = "、".join(
            f"{p['label']}={p['value']}" for p in ranked
        )
        parts.append(f"{label}: {desc}")
    return " · ".join(parts)


# ================================================================
# 内部工具
# ================================================================

def _ok(v: Any) -> bool:
    """数值有效(非空/非 NaN/非 Inf/正数)。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and f > 0


def _side(level: float, close: float) -> str:
    """价位相对当前价的方向。"""
    if level > close * 1.001:
        return "resistance"
    if level < close * 0.999:
        return "support"
    return "neutral"


def _aggregate_levels(values: list[float], tol: float) -> list[float]:
    """Merge sorted values lying within *tol* of their group's lowest value; each group yields its mean.

    Comparing with the group's first value keeps a chain of close values from drifting into one group.
    """
    groups: list[list[float]] = []
    for v in sorted(values):
        if groups and (v - groups[-1][0]) / groups[-1][0] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) / len(g) for g in groups]
