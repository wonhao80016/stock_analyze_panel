"""策略评分字段解析。"""
from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Mapping
from typing import Any

import polars as pl

from app.volume_units import volume_shares_expr

SCORING_DIRECTION_HIGH = "high"
SCORING_DIRECTION_LOW = "low"
SCORING_DIRECTIONS = frozenset({SCORING_DIRECTION_HIGH, SCORING_DIRECTION_LOW})

VIRTUAL_SCORING_DEPENDENCIES: dict[str, frozenset[str]] = {
    **{
        f"ma{period}_bias": frozenset({"close", f"ma{period}"})
        for period in (5, 10, 20, 30, 60)
    },
    **{
        f"ema{period}_bias": frozenset({"close", f"ema{period}"})
        for period in (5, 10, 20, 30, 60)
    },
    "macd_dif_pct": frozenset({"close", "macd_dif"}),
    "macd_dea_pct": frozenset({"close", "macd_dea"}),
    "macd_hist_pct": frozenset({"close", "macd_hist"}),
    "boll_position": frozenset({"close", "boll_upper", "boll_lower"}),
    "atr_pct": frozenset({"close", "atr_14"}),
    "boll_width": frozenset({"ma20", "boll_upper", "boll_lower"}),
    "vol_ratio_10d": frozenset({"volume"}),
    "vol_trend_5_10": frozenset({"vol_ma5", "vol_ma10"}),
    "turnover_ratio_5d": frozenset({"turnover_rate"}),
    "log_amount": frozenset({"amount"}),
    "amount_ratio_5d": frozenset({"amount"}),
    "gap_return": frozenset({"open", "prev_close"}),
    "intraday_return": frozenset({"open", "close"}),
    "close_position": frozenset({"high", "low", "close"}),
    "distance_to_high_60d": frozenset({"close", "high_60d"}),
    "distance_from_low_60d": frozenset({"close", "low_60d"}),
    "max_ret_20d": frozenset({"close"}),
    "ret_skew_20d": frozenset({"close"}),
    "up_days_20d": frozenset({"close"}),
    "amihud_20d": frozenset({"close", "amount"}),
    "turnover_z_60d": frozenset({"turnover_rate"}),
    "vol_price_corr_20d": frozenset({"close", "volume"}),
    "vwap_bias": frozenset({"close", "volume", "amount"}),
    "vol_trend_5_60": frozenset({"volume"}),
    "limit_up_count_20d": frozenset({"consecutive_limit_ups"}),
    "limit_up_count_60d": frozenset({"consecutive_limit_ups"}),
}

# Boolean column, true on a live row whose amount is close * board-lot volume, not the traded value.
AMOUNT_ESTIMATED = "amount_estimated"
# A VWAP or a ratio to full-session amounts is not computable from that partial estimate.
TRADED_AMOUNT_FIELDS = frozenset({"vwap_bias", "amount_ratio_5d", "amihud_20d"})

_ROLLING_SCORING_WARMUP: dict[str, int] = {
    "vol_ratio_10d": 11,
    "turnover_ratio_5d": 6,
    "amount_ratio_5d": 6,
    "max_ret_20d": 21,
    "ret_skew_20d": 21,
    "up_days_20d": 21,
    "amihud_20d": 21,
    "turnover_z_60d": 61,
    "vol_price_corr_20d": 21,
    "vol_trend_5_60": 60,
    "limit_up_count_20d": 21,
    "limit_up_count_60d": 61,
}

# A recursive feature is independent of where its history starts once its seed weighs less than this.
_SEED_WEIGHT_LIMIT = 1e-4
# Calendar days loaded per warm-up session; the Taiwan calendar averages about 1.5 with its holidays.
CALENDAR_DAYS_PER_SESSION = 1.6


def _ewm_seed_bars(alpha: float) -> int:
    """Bars after the seed until an adjust=False EWM with ``alpha`` weighs the seed below the limit."""
    return math.ceil(math.log(_SEED_WEIGHT_LIMIT) / math.log(1.0 - alpha))


_MACD_DIF_BARS = 26 + _ewm_seed_bars(2.0 / 27.0)
_MACD_DEA_BARS = _MACD_DIF_BARS + _ewm_seed_bars(2.0 / 10.0)
_KDJ_K_BARS = 9 + _ewm_seed_bars(1.0 / 3.0)
_KDJ_D_BARS = _KDJ_K_BARS + _ewm_seed_bars(1.0 / 3.0)


def recursive_warmup_bars(name: str) -> int:
    """Bars, the current one included, before a recursive feature stops depending on where its data starts.

    Covers EMA, MACD, RSI, ATR and KDJ values and the scoring fields derived from them; 1 for others.
    The seed is the first value of the definitions in app.indicators.pipeline, e.g. bar n of an EMA n.
    """
    name = str(name)
    if name.startswith("ema"):
        period = name[3:].removesuffix("_bias")
        if period.isdigit() and int(period) > 0:
            return int(period) + _ewm_seed_bars(2.0 / (int(period) + 1.0))
    if name.startswith("rsi_") and name[4:].isdigit() and int(name[4:]) > 1:
        return int(name[4:]) + 1 + _ewm_seed_bars(1.0 / int(name[4:]))
    if name in {"atr_14", "atr_pct"}:
        return 15 + _ewm_seed_bars(1.0 / 14.0)
    if name in {"macd_dif", "macd_dif_pct"}:
        return _MACD_DIF_BARS
    if name in {"macd_dea", "macd_dea_pct", "macd_hist", "macd_hist_pct"}:
        return _MACD_DEA_BARS
    if name == "kdj_k":
        return _KDJ_K_BARS
    if name in {"kdj_d", "kdj_j"}:
        return _KDJ_D_BARS
    return 1


def indicators_warmup_bars(names: Iterable[str]) -> int:
    """The largest recursive_warmup_bars of the indicators in *names*."""
    return max((recursive_warmup_bars(name) for name in names), default=1)


def scoring_field_warmup_bars(name: str) -> int:
    """Bars, the scored one included, a scoring field needs from the loaded history."""
    return max(_ROLLING_SCORING_WARMUP.get(str(name), 1), recursive_warmup_bars(name))


def effective_scoring(
    defaults: Mapping[str, Any] | None,
    overrides: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """解析有效评分；新配置可完整替换，历史配置保持局部覆盖。"""
    override_values = (overrides or {}).get("scoring")
    if (overrides or {}).get("scoring_replace") is True:
        return dict(override_values) if isinstance(override_values, Mapping) else {}
    scoring = dict(defaults or {})
    if isinstance(override_values, Mapping):
        scoring.update(override_values)
    return scoring


def meta_scoring_directions(value: Any) -> dict[str, str]:
    """Validated META["scoring_directions"]; raises on anything but a field-to-high/low mapping."""
    if not isinstance(value, Mapping):
        raise ValueError("META['scoring_directions'] must map scoring fields to 'high' or 'low'")
    invalid = sorted(
        str(name) for name, direction in value.items() if direction not in SCORING_DIRECTIONS
    )
    if invalid:
        raise ValueError(
            f"META['scoring_directions'] has invalid directions for {invalid}; expected 'high' or 'low'"
        )
    return {str(name): str(direction) for name, direction in value.items()}


def effective_scoring_directions(
    overrides: Mapping[str, Any] | None,
    *,
    defaults: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Direction per scoring field: the strategy's META defaults, replaced field by field by the user's.

    A field named in neither ranks high values first.
    """
    directions = {
        str(name): str(direction)
        for name, direction in (defaults or {}).items()
        if direction in SCORING_DIRECTIONS
    }
    values = (overrides or {}).get("scoring_directions")
    if isinstance(values, Mapping):
        directions.update(
            (str(name), str(direction))
            for name, direction in values.items()
            if direction in SCORING_DIRECTIONS
        )
    return directions


def scoring_warmup_bars(scoring: Mapping[str, Any]) -> int:
    return max(
        (scoring_field_warmup_bars(name) for name, weight in scoring.items() if weight),
        default=1,
    )


def scoring_dependencies(scoring: Mapping[str, Any]) -> set[str]:
    """把受控虚拟评分字段展开为实际数据依赖。"""
    dependencies: set[str] = set()
    for name, weight in scoring.items():
        if not weight:
            continue
        dependencies.update(VIRTUAL_SCORING_DEPENDENCIES.get(str(name), {str(name)}))
    return dependencies


def scoring_value_expr(columns: Collection[str], name: str) -> pl.Expr | None:
    """返回评分值表达式；依赖不完整时返回 None。"""
    available = set(columns)
    if name in available:
        return pl.col(name)
    dependencies = VIRTUAL_SCORING_DEPENDENCIES.get(name)
    if dependencies is None or not dependencies.issubset(available):
        return None
    if name in TRADED_AMOUNT_FIELDS and AMOUNT_ESTIMATED in available:
        value = scoring_value_expr(available - {AMOUNT_ESTIMATED}, name)
        return pl.when(pl.col(AMOUNT_ESTIMATED).fill_null(False)).then(None).otherwise(value)
    if name.startswith("ma") and name.endswith("_bias"):
        period = name.removeprefix("ma").removesuffix("_bias")
        if period.isdigit():
            return _relative(pl.col("close"), pl.col(f"ma{period}"))
    if name.startswith("ema") and name.endswith("_bias"):
        period = name.removeprefix("ema").removesuffix("_bias")
        if period.isdigit():
            return _relative(pl.col("close"), pl.col(f"ema{period}"))
    if name in {"macd_dif_pct", "macd_dea_pct", "macd_hist_pct"}:
        source = name.removesuffix("_pct")
        return _ratio(pl.col(source), pl.col("close"))
    if name == "atr_pct":
        return _ratio(pl.col("atr_14"), pl.col("close"))
    if name == "boll_position":
        return _ratio(
            pl.col("close") - pl.col("boll_lower"),
            pl.col("boll_upper") - pl.col("boll_lower"),
        )
    if name == "boll_width":
        return _ratio(pl.col("boll_upper") - pl.col("boll_lower"), pl.col("ma20"))
    if name == "vol_ratio_10d":
        return _ratio(
            pl.col("volume"),
            pl.col("volume").shift(1).rolling_mean(10).over("symbol"),
        )
    if name == "vol_trend_5_10":
        return _relative(pl.col("vol_ma5"), pl.col("vol_ma10"))
    if name == "turnover_ratio_5d":
        return _relative(
            pl.col("turnover_rate"),
            pl.col("turnover_rate").shift(1).rolling_mean(5).over("symbol"),
        )
    if name == "log_amount":
        return pl.when(pl.col("amount") >= 0).then((pl.col("amount") + 1).log()).otherwise(None)
    if name == "amount_ratio_5d":
        return _relative(
            pl.col("amount"),
            pl.col("amount").shift(1).rolling_mean(5).over("symbol"),
        )
    if name == "gap_return":
        return _relative(pl.col("open"), pl.col("prev_close"))
    if name == "intraday_return":
        return _relative(pl.col("close"), pl.col("open"))
    if name == "close_position":
        return _ratio(pl.col("close") - pl.col("low"), pl.col("high") - pl.col("low"))
    if name == "distance_to_high_60d":
        return _relative(pl.col("close"), pl.col("high_60d"))
    if name == "distance_from_low_60d":
        return _relative(pl.col("close"), pl.col("low_60d"))
    if name in {
        "max_ret_20d", "ret_skew_20d", "up_days_20d",
        "amihud_20d", "vol_price_corr_20d",
    }:
        change = _daily_change_expr()
        if name == "max_ret_20d":
            return change.rolling_max(20, min_samples=20).over("symbol")
        if name == "ret_skew_20d":
            return change.rolling_skew(20, bias=True).over("symbol")
        if name == "up_days_20d":
            return (
                (change > 0).cast(pl.Float64)
                .rolling_sum(20, min_samples=20).over("symbol")
            )
        if name == "amihud_20d":
            illiquidity = _ratio(change.abs(), pl.col("amount") / 1e8)
            return illiquidity.rolling_mean(20, min_samples=20).over("symbol")
        volume = pl.col("volume")
        product = change * volume
        return _rolling_corr_expr(change, volume, product, 20).over("symbol")
    if name == "turnover_z_60d":
        baseline = pl.col("turnover_rate").shift(1)
        mean = baseline.rolling_mean(60, min_samples=60)
        std = baseline.rolling_std(60, min_samples=60)
        return (
            pl.when(std > 0).then((pl.col("turnover_rate") - mean) / std)
            .otherwise(None)
            .over("symbol")
        )
    if name == "vwap_bias":
        vwap = _ratio(pl.col("amount"), volume_shares_expr())
        return _relative(pl.col("close"), vwap)
    if name == "vol_trend_5_60":
        fast = pl.col("volume").rolling_mean(5)
        slow = pl.col("volume").rolling_mean(60)
        return _relative(fast, slow).over("symbol")
    if name in {"limit_up_count_20d", "limit_up_count_60d"}:
        window = 20 if name == "limit_up_count_20d" else 60
        hit = (pl.col("consecutive_limit_ups").fill_null(0) > 0).cast(pl.Float64)
        return hit.rolling_sum(window, min_samples=window).over("symbol")
    return None


def _daily_change_expr() -> pl.Expr:
    previous = pl.col("close").shift(1)
    return _ratio(pl.col("close"), previous) - 1.0


def _rolling_corr_expr(
    left: pl.Expr, right: pl.Expr, product: pl.Expr, window: int
) -> pl.Expr:
    """Pearson correlation over a rolling window, matching the matrix kernel formula."""
    mean_left = left.rolling_mean(window, min_samples=window)
    mean_right = right.rolling_mean(window, min_samples=window)
    mean_product = product.rolling_mean(window, min_samples=window)
    mean_left_sq = (left * left).rolling_mean(window, min_samples=window)
    mean_right_sq = (right * right).rolling_mean(window, min_samples=window)
    covariance = mean_product - mean_left * mean_right
    variance_left = mean_left_sq - mean_left * mean_left
    variance_right = mean_right_sq - mean_right * mean_right
    return pl.when(
        (variance_left > 0) & (variance_right > 0)
    ).then(
        covariance / (variance_left * variance_right).sqrt()
    ).otherwise(None)


def materialize_scoring_columns(
    frame: pl.DataFrame,
    names: Collection[str],
) -> pl.DataFrame:
    expressions = [
        expression.alias(name)
        for name in names
        if name not in frame.columns
        and (expression := scoring_value_expr(frame.columns, str(name))) is not None
    ]
    return frame.with_columns(expressions) if expressions else frame


def _ratio(numerator: pl.Expr, denominator: pl.Expr) -> pl.Expr:
    return pl.when(denominator.is_not_null() & (denominator != 0)).then(
        numerator / denominator
    ).otherwise(None)


def _relative(numerator: pl.Expr, denominator: pl.Expr) -> pl.Expr:
    return _ratio(numerator, denominator) - 1.0
