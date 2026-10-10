"""Traditional-Chinese display labels of signal and field ids for user-facing text."""

from __future__ import annotations

import math

from app.strategy.intraday_signals import INTRADAY_SIGNAL_LABELS

# Keys are ids saved in monitor rule conditions; values match the frontend cnSignal (lib/signals.ts).
FIELD_LABELS: dict[str, str] = {
    "signal_ma_golden_5_20": "MA5上穿MA20",
    "signal_ma_dead_5_20": "MA5下穿MA20",
    "signal_ma_golden_20_60": "MA20上穿MA60",
    "signal_macd_golden": "MACD金叉",
    "signal_macd_dead": "MACD死叉",
    "signal_ma20_breakout": "突破MA20",
    "signal_ma20_breakdown": "跌破MA20",
    "signal_ma5_breakout": "突破MA5",
    "signal_ma5_breakdown": "跌破MA5",
    "signal_ma10_breakout": "突破MA10",
    "signal_ma10_breakdown": "跌破MA10",
    "signal_n_day_high": "60日新高",
    "signal_n_day_low": "60日新低",
    "signal_boll_breakout_upper": "突破布林上軌",
    "signal_boll_breakdown_lower": "跌破布林下軌",
    "signal_volume_surge": "放量",
    "signal_limit_up": "漲停",
    "signal_limit_down": "跌停",
    "signal_limit_down_recovery": "跌停翹板",
    "signal_broken_limit_up": "炸板",
    **INTRADAY_SIGNAL_LABELS,
    # Ids a builtin strategy emits for its own condition; no enriched column carries them.
    "signal_bullish_alignment_entry": "均線多頭排列",
    "signal_pullback_ma20_bounce_entry": "回踩MA20反彈",
    "signal_pullback_to_support_entry": "縮量回踩MA20",
    "signal_boll_squeeze_breakout": "布林收斂突破",
    "signal_boll_mid_break": "收盤低於布林中軌",
    "signal_capitulation_reversal": "爆量止跌反轉",
    "signal_close_20d_low": "收盤創20日新低",
    "signal_consolidation_breakout": "整理區間突破",
    "signal_ma20_break": "收盤低於MA20",
    "signal_factor_rank_entry": "因子排名進場",
    "signal_factor_rank_exit": "因子排名出場",
    "signal_lorentzian_bullish": "Lorentzian看多",
    "signal_lorentzian_exit": "Lorentzian出場",
    "signal_pullback_resume": "回檔再起漲",
    "signal_swing_structure_bullish": "波段結構轉多",
    "signal_swing_structure_exit": "波段結構轉弱",
    "signal_n_day_low_reclaim": "跌破60日低點後收回",
    "signal_low_volatility_leader_entry": "低波動多頭",
    "close": "收盤價",
    "open": "開盤價",
    "high": "最高價",
    "low": "最低價",
    "change_pct": "漲跌幅",
    "change_amount": "漲跌額",
    "amplitude": "振幅",
    "turnover_rate": "換手率",
    "volume": "成交量",
    "amount": "成交額",
    "ma5": "MA5",
    "ma10": "MA10",
    "ma20": "MA20",
    "ma30": "MA30",
    "ma60": "MA60",
    "ema5": "EMA5",
    "ema10": "EMA10",
    "ema20": "EMA20",
    "macd_dif": "MACD-DIF",
    "macd_dea": "MACD-DEA",
    "macd_hist": "MACD柱",
    "boll_upper": "布林上軌",
    "boll_lower": "布林下軌",
    "kdj_k": "KDJ-K",
    "kdj_d": "KDJ-D",
    "kdj_j": "KDJ-J",
    "rsi_6": "RSI6",
    "rsi_14": "RSI14",
    "rsi_24": "RSI24",
    "vol_ratio_5d": "5日量比",
    "vol_ma5": "5日均量",
    "vol_ma10": "10日均量",
    "high_60d": "60日最高",
    "low_60d": "60日最低",
    "distance_from_low_60d": "距60日低點",
    "momentum_5d": "5日動量",
    "momentum_20d": "20日動量",
    "momentum_60d": "60日動量",
    "atr_14": "ATR14",
    "annual_vol_20d": "20日年化波動",
    "consecutive_limit_ups": "連續漲停",
    "consecutive_limit_downs": "連續跌停",
    "distance_to_upper_limit": "距漲停距離",
    "distance_to_lower_limit": "距跌停距離",
}


def field_label(name: str) -> str:
    """The display label of a signal or field id; the id itself when it has none."""
    return FIELD_LABELS.get(name, name)


def whole_percent(share: float) -> str:
    """*share* as a whole percent rounded down, so a share just below a threshold never prints as it."""
    return f"{math.floor(round(share * 100, 6))}%"
