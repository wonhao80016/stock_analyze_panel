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
    {"id": "momentum_5d", "label": "5日动量"},
    {"id": "momentum_10d", "label": "10日动量"},
    {"id": "momentum_20d", "label": "20日动量"},
    {"id": "momentum_30d", "label": "30日动量"},
    {"id": "momentum_60d", "label": "60日动量"},
    {"id": "change_pct", "label": "日涨跌幅"},

    {"id": "ma5_bias", "label": "MA5乖离"},
    {"id": "ma10_bias", "label": "MA10乖离"},
    {"id": "ma20_bias", "label": "MA20乖离"},
    {"id": "ma30_bias", "label": "MA30乖离"},
    {"id": "ma60_bias", "label": "MA60乖离"},
    {"id": "ema5_bias", "label": "EMA5乖离"},
    {"id": "ema10_bias", "label": "EMA10乖离"},
    {"id": "ema20_bias", "label": "EMA20乖离"},
    {"id": "ema30_bias", "label": "EMA30乖离"},
    {"id": "ema60_bias", "label": "EMA60乖离"},

    {"id": "rsi_6", "label": "RSI(6)"},
    {"id": "rsi_14", "label": "RSI(14)"},
    {"id": "rsi_24", "label": "RSI(24)"},

    {"id": "macd_hist", "label": "MACD柱(原值)"},
    {"id": "macd_dif_pct", "label": "MACD DIF强度"},
    {"id": "macd_dea_pct", "label": "MACD DEA强度"},
    {"id": "macd_hist_pct", "label": "MACD柱强度"},
    {"id": "kdj_k", "label": "KDJ-K"},
    {"id": "kdj_d", "label": "KDJ-D"},
    {"id": "kdj_j", "label": "KDJ-J"},
    {"id": "boll_position", "label": "布林位置"},

    {"id": "annual_vol_20d", "label": "20日波动率"},
    {"id": "atr_14", "label": "ATR(14)原值"},
    {"id": "atr_pct", "label": "ATR相对波动"},
    {"id": "amplitude", "label": "日振幅"},
    {"id": "boll_width", "label": "布林带宽"},

    {"id": "vol_ratio_5d", "label": "5日量比"},
    {"id": "vol_ratio_10d", "label": "10日量比"},
    {"id": "vol_trend_5_10", "label": "成交量趋势"},
    {"id": "turnover_rate", "label": "换手率"},
    {"id": "turnover_ratio_5d", "label": "换手率放大"},
    {"id": "log_amount", "label": "成交额对数"},
    {"id": "amount_ratio_5d", "label": "成交额放大"},

    {"id": "gap_return", "label": "开盘跳空"},
    {"id": "intraday_return", "label": "日内收益"},
    {"id": "close_position", "label": "收盘位置"},
    {"id": "distance_to_high_60d", "label": "距60日高点"},
    {"id": "distance_from_low_60d", "label": "距60日低点"},
    {"id": "vwap_bias", "label": "VWAP乖离"},

    {"id": "max_ret_20d", "label": "20日最大单日涨幅"},
    {"id": "ret_skew_20d", "label": "20日收益偏度"},
    {"id": "up_days_20d", "label": "20日上涨天数"},

    {"id": "amihud_20d", "label": "20日Amihud非流动性"},
    {"id": "turnover_z_60d", "label": "换手率60日z分"},

    {"id": "vol_price_corr_20d", "label": "20日量价相关"},
    {"id": "vol_trend_5_60", "label": "量能趋势(5/60)"},

    {"id": "limit_up_count_20d", "label": "涨停基因(20日)"},
    {"id": "limit_up_count_60d", "label": "涨停基因(60日)"},

    {"id": "pb_latest", "label": "市净率(最新公告)"},
    {"id": "roe_latest", "label": "ROE(最新公告)"},
    {"id": "gross_margin_latest", "label": "毛利率(最新公告)"},
    {"id": "net_margin_latest", "label": "净利率(最新公告)"},
    {"id": "revenue_yoy_latest", "label": "营收增速(最新公告)"},
    {"id": "net_income_yoy_latest", "label": "净利增速(最新公告)"},
    {"id": "debt_ratio_latest", "label": "资产负债率(最新公告)"},
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
