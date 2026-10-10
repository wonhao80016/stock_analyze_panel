"""Factor catalog and factor computation shared by strategy mining."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import polars as pl

from app.backtest.engine import BacktestEngine
from app.strategy.scoring import (
    CALENDAR_DAYS_PER_SESSION,
    materialize_scoring_columns,
    scoring_field_warmup_bars,
)
from app.strategy.scoring import (
    VIRTUAL_SCORING_DEPENDENCIES as DERIVED_FACTOR_DEPENDENCIES,
)

logger = logging.getLogger(__name__)

# 可研究因子目录。保留历史 ID 兼容已有候选方案; 价格尺度相关指标优先提供归一化版本。
FACTOR_COLUMNS: list[dict] = [
    {"id": "momentum_5d", "label": "5日动量", "group": "动量", "desc": "5个交易日累计收益率"},
    {"id": "momentum_10d", "label": "10日动量", "group": "动量", "desc": "10个交易日累计收益率"},
    {"id": "momentum_20d", "label": "20日动量", "group": "动量", "desc": "20个交易日累计收益率"},
    {"id": "momentum_30d", "label": "30日动量", "group": "动量", "desc": "30个交易日累计收益率"},
    {"id": "momentum_60d", "label": "60日动量", "group": "动量", "desc": "60个交易日累计收益率"},
    {"id": "change_pct", "label": "日涨跌幅", "group": "动量", "desc": "当日收盘相对前收盘的收益率"},

    {"id": "ma5_bias", "label": "MA5乖离", "group": "均线偏离", "desc": "收盘价 / MA5 - 1"},
    {"id": "ma10_bias", "label": "MA10乖离", "group": "均线偏离", "desc": "收盘价 / MA10 - 1"},
    {"id": "ma20_bias", "label": "MA20乖离", "group": "均线偏离", "desc": "收盘价 / MA20 - 1"},
    {"id": "ma30_bias", "label": "MA30乖离", "group": "均线偏离", "desc": "收盘价 / MA30 - 1"},
    {"id": "ma60_bias", "label": "MA60乖离", "group": "均线偏离", "desc": "收盘价 / MA60 - 1"},
    {"id": "ema5_bias", "label": "EMA5乖离", "group": "均线偏离", "desc": "收盘价 / EMA5 - 1"},
    {"id": "ema10_bias", "label": "EMA10乖离", "group": "均线偏离", "desc": "收盘价 / EMA10 - 1"},
    {"id": "ema20_bias", "label": "EMA20乖离", "group": "均线偏离", "desc": "收盘价 / EMA20 - 1"},
    {"id": "ema30_bias", "label": "EMA30乖离", "group": "均线偏离", "desc": "收盘价 / EMA30 - 1"},
    {"id": "ema60_bias", "label": "EMA60乖离", "group": "均线偏离", "desc": "收盘价 / EMA60 - 1"},

    {"id": "rsi_6", "label": "RSI(6)", "group": "超买超卖", "desc": "6日相对强弱指标"},
    {"id": "rsi_14", "label": "RSI(14)", "group": "超买超卖", "desc": "14日相对强弱指标"},
    {"id": "rsi_24", "label": "RSI(24)", "group": "超买超卖", "desc": "24日相对强弱指标"},

    {"id": "macd_hist", "label": "MACD柱(原值)", "group": "趋势", "desc": "兼容历史研究; 跨股票比较建议优先使用MACD柱强度"},
    {"id": "macd_dif_pct", "label": "MACD DIF强度", "group": "趋势", "desc": "MACD DIF / 收盘价"},
    {"id": "macd_dea_pct", "label": "MACD DEA强度", "group": "趋势", "desc": "MACD DEA / 收盘价"},
    {"id": "macd_hist_pct", "label": "MACD柱强度", "group": "趋势", "desc": "MACD柱 / 收盘价, 消除股价尺度影响"},
    {"id": "kdj_k", "label": "KDJ-K", "group": "趋势", "desc": "KDJ指标K值"},
    {"id": "kdj_d", "label": "KDJ-D", "group": "趋势", "desc": "KDJ指标D值"},
    {"id": "kdj_j", "label": "KDJ-J", "group": "趋势", "desc": "KDJ指标J值"},
    {"id": "boll_position", "label": "布林位置", "group": "趋势", "desc": "收盘价在布林带下轨到上轨之间的位置"},

    {"id": "annual_vol_20d", "label": "20日波动率", "group": "波动率", "desc": "20日收益率年化标准差"},
    {"id": "atr_14", "label": "ATR(14)原值", "group": "波动率", "desc": "兼容历史研究; 跨股票比较建议优先使用ATR相对波动"},
    {"id": "atr_pct", "label": "ATR相对波动", "group": "波动率", "desc": "ATR(14) / 收盘价"},
    {"id": "amplitude", "label": "日振幅", "group": "波动率", "desc": "当日高低价差 / 前收盘价"},
    {"id": "boll_width", "label": "布林带宽", "group": "波动率", "desc": "布林带上下轨宽度 / MA20"},

    {"id": "vol_ratio_5d", "label": "5日量比", "group": "量价", "desc": "当日成交量 / 前5日平均成交量"},
    {"id": "vol_ratio_10d", "label": "10日量比", "group": "量价", "desc": "当日成交量 / 前10日平均成交量"},
    {"id": "vol_trend_5_10", "label": "成交量趋势", "group": "量价", "desc": "5日平均成交量 / 10日平均成交量 - 1"},
    {"id": "turnover_rate", "label": "换手率", "group": "量价", "desc": "当日成交股数 / 当日已发行普通股扣除私募股数(%), 自每日发布当天起保留; ETF 无换手率"},
    {"id": "turnover_ratio_5d", "label": "换手率放大", "group": "量价", "desc": "当日换手率 / 前5日平均换手率 - 1"},
    {"id": "log_amount", "label": "成交额对数", "group": "量价", "desc": "ln(成交额 + 1), 降低极端规模影响"},
    {"id": "amount_ratio_5d", "label": "成交额放大", "group": "量价", "desc": "当日成交额 / 前5日平均成交额 - 1"},

    {"id": "gap_return", "label": "开盘跳空", "group": "价格位置", "desc": "开盘价 / 前收盘价 - 1"},
    {"id": "intraday_return", "label": "日内收益", "group": "价格位置", "desc": "收盘价 / 开盘价 - 1"},
    {"id": "close_position", "label": "收盘位置", "group": "价格位置", "desc": "收盘价在当日最低价到最高价之间的位置"},
    {"id": "distance_to_high_60d", "label": "距60日高点", "group": "价格位置", "desc": "收盘价 / 60日最高收盘价 - 1"},
    {"id": "distance_from_low_60d", "label": "距60日低点", "group": "价格位置", "desc": "收盘价 / 60日最低收盘价 - 1"},
    {"id": "vwap_bias", "label": "VWAP乖离", "group": "价格位置", "desc": "收盘价 / 当日成交均价 - 1, 成交均价 = 成交额 / (成交量x100)"},

    {"id": "max_ret_20d", "label": "20日最大单日涨幅", "group": "收益形态", "desc": "近20个交易日单日涨幅最大值(彩票效应, 高值代表博彩型特征强)"},
    {"id": "ret_skew_20d", "label": "20日收益偏度", "group": "收益形态", "desc": "近20个交易日日收益偏度, 高值代表右偏(偶发大涨)"},
    {"id": "up_days_20d", "label": "20日上涨天数", "group": "收益形态", "desc": "近20个交易日中上涨天数(0~20)"},

    {"id": "amihud_20d", "label": "20日Amihud非流动性", "group": "流动性", "desc": "近20日平均 |日涨跌幅| / 成交额(亿元), 高值代表流动性差"},
    {"id": "turnover_z_60d", "label": "换手率60日z分", "group": "流动性", "desc": "(当日换手率 - 前60日均值) / 前60日标准差, 衡量换手异动"},

    {"id": "vol_price_corr_20d", "label": "20日量价相关", "group": "量价", "desc": "近20个交易日日涨跌幅与成交量的相关系数, 高值代表量价同向"},
    {"id": "vol_trend_5_60", "label": "量能趋势(5/60)", "group": "量价", "desc": "5日平均成交量 / 60日平均成交量 - 1"},

    {"id": "limit_up_count_20d", "label": "涨停基因(20日)", "group": "涨停基因", "desc": "近20个交易日涨停次数"},
    {"id": "limit_up_count_60d", "label": "涨停基因(60日)", "group": "涨停基因", "desc": "近60个交易日涨停次数"},

    {"id": "pb_latest", "label": "市净率(最新公告)", "group": "财务", "desc": "收盘价 / 最新已公告每股净资产; 无财务数据或公告前为空"},
    {"id": "roe_latest", "label": "ROE(最新公告)", "group": "财务", "desc": "最新已公告净资产收益率(%); 无财务数据或公告前为空"},
    {"id": "gross_margin_latest", "label": "毛利率(最新公告)", "group": "财务", "desc": "最新已公告销售毛利率(%)"},
    {"id": "net_margin_latest", "label": "净利率(最新公告)", "group": "财务", "desc": "最新已公告销售净利率(%)"},
    {"id": "revenue_yoy_latest", "label": "营收增速(最新公告)", "group": "财务", "desc": "最新已公告营业收入同比(%)"},
    {"id": "net_income_yoy_latest", "label": "净利增速(最新公告)", "group": "财务", "desc": "最新已公告归母净利润同比(%)"},
    {"id": "debt_ratio_latest", "label": "资产负债率(最新公告)", "group": "财务", "desc": "最新已公告资产负债率(%)"},
]

FACTOR_WARMUP_DAYS = 120
FACTOR_METHODOLOGY_VERSION = "factor_v2"


def factor_warmup_days(factor_names: Iterable[str]) -> int:
    """Calendar days of history loaded before a mining window for the factors in *factor_names*.

    At least FACTOR_WARMUP_DAYS, which covers the 60-session rolling factors.
    """
    bars = max((scoring_field_warmup_bars(name) for name in factor_names), default=1)
    return max(FACTOR_WARMUP_DAYS, int(bars * CALENDAR_DAYS_PER_SESSION))


@dataclass
class FactorBatchConfig:
    symbols: list[str] | None
    start: date
    end: date
    asset_type: str = "stock"


class FactorBacktestService:
    def __init__(self, engine: BacktestEngine) -> None:
        self.engine = engine

    def _data_generation(self, asset_type: str) -> str | None:
        loader = getattr(self.engine, "data_generation", None)
        return loader(asset_type) if callable(loader) else None

    def _assert_data_generation(
        self,
        asset_type: str,
        expected: str | None,
    ) -> None:
        verifier = getattr(self.engine, "assert_data_generation", None)
        if callable(verifier):
            verifier(asset_type, expected)

    @staticmethod
    def _compute_missing_factors(
        panel: pl.DataFrame,
        factor_cols: set[str],
        *,
        assume_sorted: bool = False,
    ) -> pl.DataFrame:
        required = {"symbol", "date", "open", "high", "low", "close", "volume"}
        if not required.issubset(panel.columns):
            missing = sorted(required - set(panel.columns))
            logger.warning("factors %s cannot be computed, missing columns: %s", factor_cols, missing)
            return panel

        from app.indicators.pipeline import compute_indicators

        derived = factor_cols & set(DERIVED_FACTOR_DEPENDENCIES)
        indicator_columns = factor_cols - derived
        for factor_name in derived:
            indicator_columns.update(DERIVED_FACTOR_DEPENDENCIES[factor_name])
        panel = compute_indicators(
            panel,
            needed=indicator_columns,
            assume_sorted=assume_sorted,
        )
        return FactorBacktestService._compute_derived_factors(panel, derived)

    @staticmethod
    def _compute_derived_factors(panel: pl.DataFrame, factor_cols: set[str]) -> pl.DataFrame:
        return materialize_scoring_columns(panel, factor_cols)
