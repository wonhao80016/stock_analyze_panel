"""策略回测服务 — 复用 StrategyDef 体系做全周期回测。

核心优化: 向量化 filter_fn，不逐日调用 StrategyEngine.run()。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import numpy as np
import polars as pl

from app.backtest.engine import (
    BacktestEngine,
    MatcherConfig,
    validate_risk_free_rate,
)
from app.backtest.fundamentals import FUNDAMENTAL_FACTOR_NAMES
from app.backtest.matrix import (
    BASE_MISSING_COLUMN,
    MarketDataMatrix,
    MatrixCacheProfile,
    MatrixPipelineConfig,
    MatrixPrewarmCancelledError,
    MatrixStrategyPipeline,
    apply_time_masks,
    asset_mask_without,
    build_market_matrix,
    build_market_matrix_from_signals,
    partition_session_dates,
    rolling_mean,
    slice_market_data_matrix,
    slice_signal_matrix,
    taiwan_limit_rule_exclusions,
)
from app.backtest.minute_trigger import unsupported_minute_exit_signals
from app.config import settings
from app.indicators.pipeline import (
    ENRICHED_STORAGE_COLS,
    INDICATOR_COLUMNS,
    LIMIT_SIGNAL_OUTPUTS,
    get_signal_dependencies,
)
from app.strategy.engine import (
    RAW_PRICE_BOUND_KEYS,
    StrategyDataContext,
    StrategyDef,
    StrategyEngine,
    finite_or_null,
    frame_has_values,
    normalized_scoring_part,
    ranks_higher_first,
    resolve_scoring_values,
    without_unavailable_share_bounds,
)
from app.strategy.field_labels import field_label, whole_percent
from app.strategy.scoring import (
    CALENDAR_DAYS_PER_SESSION,
    SCORING_DIRECTION_LOW,
    effective_scoring,
    effective_scoring_directions,
    indicators_warmup_bars,
    materialize_scoring_columns,
    scoring_dependencies,
    scoring_warmup_bars,
)
from app.taiwan.benchmarks import TAIEX
from app.taiwan.enriched_reader import ENRICHED_DIR_NAME
from app.taiwan.share_capital_store import (
    MIN_SESSION_COVERAGE,
    coverage_eligible,
    eligible_coverage,
)
from app.taiwan.symbol import extract_exchange
from app.volume_units import TAIWAN_SUFFIXES

logger = logging.getLogger(__name__)

TAIWAN_STAMP_TAX_OVERRIDE_ERROR = "stamp_tax_pct cannot override per-instrument Taiwan tax rules"
_EXECUTION_COLUMNS = frozenset({
    "symbol", "date", "open", "high", "low", "close", "volume", "raw_close",
    "name", "score", "signal_limit_up", "signal_limit_down",
})
_LIMIT_BASE_COLUMNS = frozenset({"raw_close", "raw_high", "raw_low"})
_INSTRUMENT_COLUMNS = frozenset({"name", "total_shares", "float_shares"})


def warmup_calendar_days(bars: int) -> int:
    """Calendar days loaded before a backtest window for a warm-up of *bars* sessions."""
    return max(120, int(max(bars, 1) * CALENDAR_DAYS_PER_SESSION))


def _read_local_taiex(start: date, end: date) -> pl.DataFrame:
    """TAIEX closes from the local index store only; a backtest never backfills them online."""
    from app.taiwan.daily_store import TaiwanDailyStore
    from app.taiwan.data_root import taiwan_data_root

    index_dir = taiwan_data_root() / "index_daily"
    # TaiwanDailyStore creates its directory on construction; a read must not.
    if not index_dir.is_dir():
        return pl.DataFrame(schema={"date": pl.Date, "close": pl.Float64})
    return TaiwanDailyStore(index_dir).scan_range([TAIEX.symbol], start, end).select("date", "close")


@dataclass(frozen=True)
class ResolvedFeaturePlan:
    base_columns: frozenset[str]
    indicator_columns: frozenset[str]
    signal_columns: frozenset[str]
    matrix_columns: frozenset[str]
    instrument_columns: frozenset[str]
    warmup_bars: int
    full_feature_fallback: bool = False
    execution_backend: str = "polars_expr"
    # 财务因子列不落 enriched 存储, 由 engine 在加载口按公告日门控附加。
    fundamental_columns: frozenset[str] = frozenset()


def _merge_resolved_feature_plans(
    plans: list[ResolvedFeaturePlan],
) -> ResolvedFeaturePlan:
    if not plans:
        raise ValueError("cannot merge an empty feature plan list")
    backends = {plan.execution_backend for plan in plans}
    if backends != {"matrix_native"}:
        raise ValueError("shared MarketDataMatrix preparation only supports matrix_native")

    def _union(field: str) -> frozenset[str]:
        merged: set[str] = set()
        for plan in plans:
            merged.update(getattr(plan, field))
        return frozenset(merged)

    return ResolvedFeaturePlan(
        base_columns=_union("base_columns"),
        indicator_columns=_union("indicator_columns"),
        signal_columns=_union("signal_columns"),
        matrix_columns=_union("matrix_columns"),
        instrument_columns=_union("instrument_columns"),
        warmup_bars=max(plan.warmup_bars for plan in plans),
        full_feature_fallback=any(plan.full_feature_fallback for plan in plans),
        execution_backend="matrix_native",
        fundamental_columns=_union("fundamental_columns"),
    )


class StrategyDependencyResolver:
    """Resolve all backtest field dependencies once before loading market data."""

    def resolve(
        self,
        strategy: StrategyDef,
        *,
        params: dict,
        basic_filter: dict,
        entry_signals: list[str],
        exit_signals: list[str],
        overrides: dict | None = None,
        minute_fill: bool = False,
        asset_type: str = "stock",
    ) -> ResolvedFeaturePlan:
        overrides = overrides or {}
        basic_filter = _basic_filter_for_asset(basic_filter, asset_type)
        if strategy.execution_backend == "matrix_native":
            return self._resolve_matrix_native(
                strategy,
                params=params,
                basic_filter=basic_filter,
                overrides=overrides,
            )

        required_features = set(strategy.required_features)
        required_signals = {
            _normalize_signal_name(signal)
            for signal in [*entry_signals, *exit_signals]
            if signal
        }
        required_signals.update({"signal_limit_up", "signal_limit_down"})

        scoring = effective_scoring(strategy.meta.get("scoring"), overrides)
        required_features.update(scoring_dependencies(scoring))
        order_by = strategy.meta.get("order_by")
        if order_by and order_by != "score":
            required_features.add(str(order_by))

        required_features.update(_basic_filter_dependencies(basic_filter))
        filter_features, filter_resolved = _filter_dependencies(strategy, params)
        required_features.update(filter_features)
        embedded_signals = {
            feature
            for feature in required_features
            if feature.startswith(("signal_", "csg_"))
        }
        required_signals.update(embedded_signals)
        required_features.difference_update(embedded_signals)

        full_fallback = bool(strategy.filter_history_fn and not strategy.required_features)
        full_fallback = full_fallback or not filter_resolved
        signal_dependencies = get_signal_dependencies()
        if full_fallback:
            logger.warning(
                "strategy %s has dynamic Python dependencies without REQUIRED_FEATURES; "
                "backtest falls back to full feature computation",
                strategy.meta.get("id", "<unknown>"),
            )
            required_features.update(INDICATOR_COLUMNS)
            required_signals.update(signal_dependencies)
            required_signals.update(LIMIT_SIGNAL_OUTPUTS)

        unknown_signals = required_signals - set(signal_dependencies) - set(LIMIT_SIGNAL_OUTPUTS)
        if unknown_signals:
            raise ValueError(f"策略引用了不存在的信号: {sorted(unknown_signals)}")
        for signal in required_signals:
            required_features.update(signal_dependencies.get(signal, ()))

        indicator_columns = frozenset(required_features & set(INDICATOR_COLUMNS))
        base_columns = _resolve_base_columns(required_features | set(_EXECUTION_COLUMNS))
        if required_signals & set(LIMIT_SIGNAL_OUTPUTS):
            base_columns = frozenset(set(base_columns) | set(_LIMIT_BASE_COLUMNS))

        instrument_columns = frozenset(required_features & set(_INSTRUMENT_COLUMNS))
        instrument_columns = frozenset(set(instrument_columns) | {"name"})
        matrix_columns = set(_EXECUTION_COLUMNS) | required_signals
        if minute_fill:
            indicator_columns = frozenset(set(indicator_columns) | {"ma5", "ma10", "ma20"})
            matrix_columns.update({"ma5", "ma10", "ma20"})
            base_columns = frozenset(set(base_columns) | {"close"})

        warmup_bars = max(
            60,
            int(strategy.lookback_days or 1),
            scoring_warmup_bars(scoring),
            # The panel recomputes these on the loaded window; signals also read the bar before.
            indicators_warmup_bars(indicator_columns) + 1,
        )
        return ResolvedFeaturePlan(
            base_columns=base_columns,
            indicator_columns=indicator_columns,
            signal_columns=frozenset(required_signals),
            matrix_columns=frozenset(matrix_columns),
            instrument_columns=instrument_columns,
            warmup_bars=warmup_bars,
            full_feature_fallback=full_fallback,
            execution_backend=strategy.execution_backend,
            fundamental_columns=frozenset(
                required_features & FUNDAMENTAL_FACTOR_NAMES
            ),
        )

    @staticmethod
    def _resolve_matrix_native(
        strategy: StrategyDef,
        *,
        params: dict,
        basic_filter: dict,
        overrides: dict,
    ) -> ResolvedFeaturePlan:
        if strategy.matrix_strategy is None:
            raise ValueError(
                f"matrix_native strategy {strategy.meta.get('id', '<unknown>')} "
                "must declare MATRIX_STRATEGY"
            )

        required_features = set(strategy.required_features)
        required_features.update(strategy.matrix_strategy.required_fields())
        parameter_fields = getattr(
            strategy.matrix_strategy,
            "required_fields_for_params",
            None,
        )
        parameter_scoring: dict[str, float] = {}
        if callable(parameter_fields):
            parameter_scoring = {
                str(name): 1.0
                for name in parameter_fields(params)
            }
            required_features.update(scoring_dependencies(parameter_scoring))
        required_features.update(_basic_filter_dependencies(basic_filter))
        scoring = effective_scoring(strategy.meta.get("scoring"), overrides)
        required_features.update(scoring_dependencies(scoring))
        order_by = strategy.meta.get("order_by")
        if order_by and order_by != "score":
            required_features.add(str(order_by))

        base_columns = _resolve_base_columns(required_features | set(_EXECUTION_COLUMNS))
        base_columns = frozenset(set(base_columns) | set(_LIMIT_BASE_COLUMNS))
        instrument_columns = frozenset(required_features & set(_INSTRUMENT_COLUMNS))
        instrument_columns = frozenset(set(instrument_columns) | {"name"})
        warmup_bars = max(
            60,
            int(strategy.matrix_strategy.required_warmup_bars(params)),
            scoring_warmup_bars(scoring),
            scoring_warmup_bars(parameter_scoring),
        )
        matrix_columns = set(base_columns) | set(instrument_columns) | {
            "signal_limit_up",
            "signal_limit_down",
        }
        return ResolvedFeaturePlan(
            base_columns=base_columns,
            indicator_columns=frozenset(),
            signal_columns=frozenset({"signal_limit_up", "signal_limit_down"}),
            matrix_columns=frozenset(matrix_columns),
            instrument_columns=instrument_columns,
            warmup_bars=warmup_bars,
            full_feature_fallback=False,
            execution_backend="matrix_native",
            fundamental_columns=frozenset(
                required_features & FUNDAMENTAL_FACTOR_NAMES
            ),
        )


def build_matrix_cache_profile(
    strategy_engine: StrategyEngine,
    asset_type: str,
    *,
    requested_plan: ResolvedFeaturePlan | None = None,
    requested_forward_bars: int = 0,
    max_disk_bytes: int = 512 * 1024 * 1024,
    backtest_engine: BacktestEngine | None = None,
) -> MatrixCacheProfile:
    """Merge registered matrix dependencies into one strategy-agnostic cache profile.

    With ``backtest_engine``, a registered strategy's share-cap bound adds no share field the
    market lacks, so it cannot make every other strategy's matrix load fail.
    """
    resolver = StrategyDependencyResolver()
    plans: list[ResolvedFeaturePlan] = []
    if requested_plan is not None:
        plans.append(requested_plan)
    forward_bars = max(0, int(requested_forward_bars))
    common_filter = {
        "enabled": True,
        "amount_min": 0.0,
        "turnover_min": 0.0,
        "exclude_st": True,
    }
    definitions = (
        strategy_engine.strategy_definitions()
        if hasattr(strategy_engine, "strategy_definitions")
        else ()
    )
    for strategy in definitions:
        if strategy.execution_backend != "matrix_native":
            continue
        if asset_type not in strategy.meta.get("asset_types", ["stock"]):
            continue
        if "1d" not in strategy.meta.get("timeframes", ["1d"]):
            continue
        params = StrategyEngine.resolve_params(strategy)
        for item in strategy.meta.get("params", []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if item.get("type") in {"int", "float"} and item.get("max") is not None:
                params[str(item["id"])] = item["max"]
        plans.append(resolver.resolve(
            strategy,
            params=params,
            basic_filter=_basic_filter_for_data(
                {**dict(strategy.basic_filter or {}), **common_filter},
                backtest_engine,
                asset_type,
            ),
            entry_signals=strategy.entry_signals,
            exit_signals=strategy.exit_signals,
            overrides={},
            minute_fill=False,
            asset_type=asset_type,
        ))
        forward_bars = max(forward_bars, int(strategy.max_hold_days or 0))

    if not plans:
        raise ValueError(f"no matrix-native cache profile available for asset_type={asset_type!r}")
    merged = _merge_resolved_feature_plans(plans)
    fields = frozenset(
        set(merged.base_columns)
        | set(merged.instrument_columns)
        | set(merged.matrix_columns)
    )
    generation_payload = json.dumps(
        {
            "asset_type": asset_type,
            "fields": sorted(fields),
            "warmup_bars": merged.warmup_bars,
            "forward_bars": forward_bars,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    generation = hashlib.blake2b(
        generation_payload.encode("utf-8"),
        digest_size=12,
    ).hexdigest()
    return MatrixCacheProfile(
        field_columns=fields,
        warmup_bars=merged.warmup_bars,
        forward_bars=forward_bars,
        max_disk_bytes=int(max_disk_bytes),
        generation=generation,
    )


def prewarm_matrix_cache(
    engine: BacktestEngine,
    strategy_engine: StrategyEngine,
    *,
    asset_type: str,
    latest_date: date,
    years: int = 5,
    cancel_event: threading.Event | None = None,
) -> dict[str, object]:
    """Build the shared full-universe mmap outside a user backtest request."""
    if years <= 0:
        raise ValueError("matrix cache prewarm years must be positive")
    if cancel_event is not None and cancel_event.is_set():
        raise MatrixPrewarmCancelledError("matrix cache prewarm cancelled")
    profile = build_matrix_cache_profile(
        strategy_engine,
        asset_type,
        max_disk_bytes=settings.backtest_matrix_cache_max_mb * 1024 * 1024,
        backtest_engine=engine,
    )
    formal_start = date(max(1, latest_date.year - years + 1), 1, 1)
    coverage_start = formal_start - timedelta(days=warmup_calendar_days(profile.warmup_bars))
    prewarm_columns = frozenset({
        "symbol",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "raw_close",
        "raw_high",
    })
    plan = ResolvedFeaturePlan(
        base_columns=prewarm_columns,
        indicator_columns=frozenset(),
        signal_columns=frozenset(),
        matrix_columns=prewarm_columns,
        instrument_columns=frozenset({"name"}),
        warmup_bars=profile.warmup_bars,
        full_feature_fallback=False,
        execution_backend="matrix_native",
    )
    started = time.perf_counter()
    market = engine.load_market_data_matrix_for_backtest(
        None,
        coverage_start,
        latest_date,
        plan,
        asset_type=asset_type,
        cache_profile=profile,
        coverage_start=coverage_start,
        coverage_end=latest_date,
        cancel_event=cancel_event,
    )
    result = {
        "asset_type": asset_type,
        "start": coverage_start.isoformat(),
        "end": latest_date.isoformat(),
        "cache_status": market.cache_status,
        "cache_path": market.cache_path,
        "bytes": market.nbytes,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    del market
    return result


def _normalize_signal_name(signal: str) -> str:
    if signal.startswith(("signal_", "csg_")):
        return signal
    return f"signal_{signal}"


def _filter_dependencies(strategy: StrategyDef, params: dict) -> tuple[set[str], bool]:
    if strategy.filter_history_fn:
        return set(strategy.required_features), bool(strategy.required_features)
    if not strategy.filter_fn:
        return set(), True
    try:
        expr = strategy.filter_fn(pl.DataFrame(), params)
        if expr is None:
            return set(), True
        return set(expr.meta.root_names()), True
    except Exception as exc:
        logger.warning("strategy filter dependency resolution failed: %s", exc)
        return set(strategy.required_features), bool(strategy.required_features)


def _basic_filter_dependencies(config: dict) -> set[str]:
    if not config or not config.get("enabled", True):
        return set()
    dependencies = {"symbol", "close"}
    if any(config.get(key) is not None for key in RAW_PRICE_BOUND_KEYS):
        dependencies.add("raw_close")
    if any(config.get(key) is not None for key in ("amount_min", "amount_max")):
        dependencies.add("amount")
    if any(config.get(key) is not None for key in ("turnover_min", "turnover_max")):
        dependencies.add("turnover_rate")
    if any(config.get(key) is not None for key in ("market_cap_min", "market_cap_max")):
        dependencies.add("total_shares")
    if any(config.get(key) is not None for key in ("float_cap_min", "float_cap_max")):
        dependencies.add("float_shares")
    if config.get("exclude_st"):
        dependencies.add("name")
    return dependencies


_SHARE_CAP_FILTER_KEYS = (
    "market_cap_min",
    "market_cap_max",
    "float_cap_min",
    "float_cap_max",
)


def _basic_filter_for_asset(basic_filter: dict, asset_type: str) -> dict:
    """Clear the share-cap bounds for non-stock assets, which carry no share counts.

    The etf/index tables hold only symbol/name, so market-cap and float-cap bounds are meaningless
    and unsatisfiable there. Clearing them before dependency resolution keeps total_shares and
    float_shares out of the requirements, which would otherwise fail the matrix load.

    Runtime filtering must use the sanitized filter too: a bound on a field the
    data lacks raises in both the polars and the matrix filter.
    """
    if asset_type == "stock" or not basic_filter:
        return basic_filter
    sanitized = dict(basic_filter)
    for key in _SHARE_CAP_FILTER_KEYS:
        sanitized[key] = None
    return sanitized


def _basic_filter_for_data(basic_filter: dict, engine: object, asset_type: str) -> dict:
    """``basic_filter`` without the share-cap bounds the market's share counts cannot apply.

    A backtest reads share counts only from the asset type's instruments; selection drops the
    same bounds when its data holds none (without_unavailable_share_bounds).
    """
    basic_filter = _basic_filter_for_asset(basic_filter, asset_type)
    instruments = getattr(getattr(engine, "repo", None), "get_instruments_asset", None)
    if not callable(instruments):
        return basic_filter
    return without_unavailable_share_bounds(
        basic_filter, lambda name: frame_has_values(instruments(asset_type), name)
    )


_ALWAYS_PRESENT_FIELDS = frozenset({"symbol", "name", "open", "high", "low", "close", "volume"})
# Below this share of rows with data, a ranking mostly orders missing scores by symbol.
_MIN_SCORING_COVERAGE = 0.5


def _filter_fields(basic_filter: dict) -> set[str]:
    return set(_basic_filter_dependencies(basic_filter)) - _ALWAYS_PRESENT_FIELDS


def _scoring_fields(scorings: list[Mapping]) -> set[str]:
    fields: set[str] = set()
    for scoring in scorings:
        fields.update(scoring_dependencies(scoring))
    return fields - _ALWAYS_PRESENT_FIELDS


def _parameter_scoring_fields(strategies: list[tuple[StrategyDef, dict]]) -> set[str]:
    """Scoring fields a matrix strategy takes from its parameters, such as factor_rank_research."""
    names: set[str] = set()
    for strategy, params in strategies:
        parameter_fields = getattr(strategy.matrix_strategy, "required_fields_for_params", None)
        if callable(parameter_fields):
            names.update(str(name) for name in parameter_fields(params))
    return _scoring_fields([{name: 1.0 for name in names}])


def _required_coverage_fields(strategies: list[tuple[StrategyDef, dict]]) -> set[str]:
    """Fields a matrix strategy cannot decide an entry without (required_coverage_fields hook)."""
    names: set[str] = set()
    for strategy, params in strategies:
        hook = getattr(strategy.matrix_strategy, "required_coverage_fields", None)
        if callable(hook):
            names.update(str(name) for name in hook(params))
    return names


_WARNING_SESSIONS_LISTED = 5


def _entry_coverage_check(
    market: MarketDataMatrix,
    fields: set[str],
    sessions: np.ndarray,
    scope: np.ndarray | None = None,
) -> tuple[str | None, list[dict]]:
    """The refusal for the first of *fields* that *sessions* cannot screen, else exchange warnings.

    Counts only the instruments in *scope*, the strategy's asset scope. Refused: no instrument
    able to hold the field has a close in *sessions*, or a session's coverage is below
    MIN_SESSION_COVERAGE. A warning names the sessions that pass while one exchange's own
    coverage is below it; turnover is never back-calculated.
    """
    exchanges = [
        exchange.value if (exchange := extract_exchange(str(symbol))) else ""
        for symbol in market.symbols
    ]
    warnings: list[dict] = []
    for name in sorted(fields):
        eligible = coverage_eligible(name, market.symbols)
        if scope is not None:
            eligible = eligible & scope
        label = field_label(name)
        if sessions.any() and not (np.isfinite(market.close[sessions]) & eligible).any():
            refusal = f"回測區間內所選範圍沒有可計算{label}的股票"
            if name == "turnover_rate":
                refusal += (
                    f"（ETF、TDR 沒有{label}，停牌股停牌期間沒有收盤價），請改選含普通股的標的"  # noqa: RUF001
                )
            return refusal, []
        values = market.fields.get(name)
        coverage = (
            np.zeros(market.shape[0])
            if values is None
            else eligible_coverage(market.close, values, eligible)
        )
        failing = np.flatnonzero(sessions & (coverage < MIN_SESSION_COVERAGE))
        if failing.size:
            first = int(failing[0])
            return (
                f"{market.timestamp_labels[first][:10]} 只有 {whole_percent(coverage[first])} "
                f"的股票有{label}資料（至少需 {whole_percent(MIN_SESSION_COVERAGE)}；"  # noqa: RUF001
                f"回測區間內共 {failing.size} 個交易日不足），"  # noqa: RUF001
                f"無法依{label}回測，請把回測區間設在有{label}資料的交易日",  # noqa: RUF001
                [],
            )
        if values is None:
            continue
        for exchange in sorted(set(exchanges) - {""}):
            on_exchange = np.array([value == exchange for value in exchanges], dtype=bool)
            exchange_coverage = eligible_coverage(market.close, values, eligible & on_exchange)
            short = np.flatnonzero(sessions & (exchange_coverage < MIN_SESSION_COVERAGE))
            if short.size:
                warnings.append(
                    {
                        "field": name,
                        "exchange": exchange,
                        "threshold": MIN_SESSION_COVERAGE,
                        "sessions": [
                            market.timestamp_labels[int(row)][:10]
                            for row in short[:_WARNING_SESSIONS_LISTED]
                        ],
                        "session_count": int(short.size),
                    }
                )
    return None, warnings


def _matrix_field_coverage(
    market: MarketDataMatrix,
    fields: set[str],
    rows: np.ndarray,
) -> dict[str, float]:
    """Share of the ``rows`` cells holding a finite value, per field stored in ``market``."""
    total = int(np.count_nonzero(rows))
    if total == 0:
        return {}
    coverage: dict[str, float] = {}
    for name in sorted(fields):
        values = market.fields.get(name)
        if values is not None:
            coverage[name] = int(np.count_nonzero(np.isfinite(values[rows]))) / total
    return coverage


def _panel_field_coverage(rows: pl.DataFrame, fields: set[str]) -> dict[str, float]:
    """Share of ``rows`` holding a finite value, per column."""
    names = sorted(name for name in fields if name in rows.columns)
    if not names or rows.is_empty():
        return {}
    expressions = []
    for name in names:
        value = finite_or_null(pl.col(name)) if rows.schema[name].is_numeric() else pl.col(name)
        expressions.append(value.is_not_null().sum().alias(name))
    counts = rows.select(expressions).row(0, named=True)
    return {name: int(counts[name]) / rows.height for name in names}


def _low_scoring_coverage_error(coverage: Mapping[str, float]) -> str | None:
    low = sorted((name, ratio) for name, ratio in coverage.items() if ratio < _MIN_SCORING_COVERAGE)
    if not low:
        return None
    listed = "、".join(f"{field_label(name)} {whole_percent(ratio)}" for name, ratio in low)
    return f"以下評分欄位的資料覆蓋率低於 {whole_percent(_MIN_SCORING_COVERAGE)}：{listed}"  # noqa: RUF001


def _resolve_base_columns(features: set[str]) -> frozenset[str]:
    storage = set(ENRICHED_STORAGE_COLS)
    base = {"symbol", "date"} | (features & storage)
    close_indicators = set(INDICATOR_COLUMNS) - {
        "atr_14", "amplitude", "kdj_k", "kdj_d", "kdj_j",
        "vol_ma5", "vol_ma10", "vol_ratio_5d",
    }
    if features & close_indicators:
        base.add("close")
    if features & {"atr_14", "amplitude", "kdj_k", "kdj_d", "kdj_j"}:
        base.update({"high", "low", "close"})
    if features & {"vol_ma5", "vol_ma10", "vol_ratio_5d"}:
        base.add("volume")
    base.update({"open", "high", "low", "close", "volume"})
    return frozenset(base & storage)


def _minute_fill_reference(close: np.ndarray, entry_fill: str) -> np.ndarray:
    result = np.full(close.shape, np.nan, dtype=np.float32)
    if entry_fill == "open_t+1" and close.shape[0] > 1:
        daily_mean = rolling_mean(close, 5)
        result[1:] = daily_mean[:-1]
    result.setflags(write=False)
    return result


def _taiwan_share_events(
    symbols: list[str],
    corporate_actions=None,
) -> dict[str, tuple[tuple[str, float], ...]]:
    from app.taiwan.corporate_actions import TaiwanCorporateActionStore, adjustment_factors

    store = corporate_actions or TaiwanCorporateActionStore()
    try:
        events = store.read(symbols=symbols)
    except Exception as exc:
        raise ValueError(f"Taiwan corporate actions unavailable: {exc}") from exc
    share_events: dict[str, list[tuple[str, float]]] = {}
    factors = adjustment_factors(events, date.max).filter(pl.col("volume_factor") != 1.0)
    for row in factors.iter_rows(named=True):
        share_events.setdefault(row["symbol"], []).append(
            (row["trade_date"].isoformat(), float(row["volume_factor"]))
        )
    return {symbol: tuple(items) for symbol, items in share_events.items()}


def _configure_taiwan_matcher(
    matcher_config: MatcherConfig,
    symbols: list[str] | tuple[str, ...],
    security_master=None,
    corporate_actions=None,
) -> None:
    all_symbols = sorted({str(symbol) for symbol in symbols})
    taiwan_symbols = sorted({
        str(symbol)
        for symbol in all_symbols
        if str(symbol).endswith((".TWSE", ".TPEX"))
    })
    if not taiwan_symbols:
        return
    if len(taiwan_symbols) != len(all_symbols):
        raise ValueError("mixed Taiwan and non-Taiwan backtest universes are unsupported")
    if matcher_config.stamp_tax_pct is not None:
        raise ValueError(TAIWAN_STAMP_TAX_OVERRIDE_ERROR)

    from app.taiwan.market_rules import (
        TAIWAN_SIMULATION_COMMISSION_RATE,
        BacktestMode,
        LotModel,
        TaiwanMarketProfile,
        TradingCostModel,
    )
    from app.taiwan.universe import get_security_master
    from app.taiwan.universe.models import MarketProfileBridge

    master = security_master or get_security_master()
    tax_classes = {}
    tick_classes = {}
    lot_sizes = {}
    for symbol in taiwan_symbols:
        try:
            instrument = master.get_instrument(symbol)
        except Exception as exc:
            raise ValueError(f"Taiwan Security Master unavailable for {symbol}: {exc}") from exc
        if instrument is None:
            raise ValueError(f"Taiwan Security Master has no execution profile for {symbol}")
        lot_size = int(instrument.lot_size)
        if lot_size <= 0:
            raise ValueError(f"Taiwan Security Master has invalid trading unit for {symbol}")
        tax_classes[symbol] = MarketProfileBridge.get_tax_class(instrument)
        tick_classes[symbol] = MarketProfileBridge.get_tick_size_class(instrument)
        lot_sizes[symbol] = lot_size
    share_events = _taiwan_share_events(taiwan_symbols, corporate_actions)

    matcher_config.market_profile = TaiwanMarketProfile(
        mode=(
            BacktestMode.DAY_TRADING
            if matcher_config.minimum_holding_sessions == 0
            else BacktestMode.STANDARD_CASH
        ),
        cost=TradingCostModel(
            commission_rate=(
                matcher_config.commission_pct
                if matcher_config.commission_pct is not None
                else TAIWAN_SIMULATION_COMMISSION_RATE
            ),
            discount=matcher_config.commission_discount,
            minimum_commission=matcher_config.minimum_commission,
        ),
        lot=LotModel(mode=matcher_config.lot_mode),
    )
    matcher_config.tax_class_by_symbol = tax_classes
    matcher_config.tick_class_by_symbol = tick_classes
    matcher_config.lot_size_by_symbol = lot_sizes
    matcher_config.share_events_by_symbol = share_events
    eligible_symbols = set(matcher_config.day_trade_eligible_symbols)
    matcher_config.day_trade_eligible_by_symbol = {
        symbol: symbol in eligible_symbols
        for symbol in taiwan_symbols
    }


def _asset_scope_mask(symbols: tuple[str, ...] | list[str], asset_types: list[str]) -> np.ndarray:
    """Which of *symbols* the strategy may pick, by the rule of StrategyEngine._supported_symbols.

    A Taiwan symbol counts only when the security master types it in *asset_types*.
    """
    taiwan = [str(symbol) for symbol in symbols if str(symbol).endswith(TAIWAN_SUFFIXES)]
    instruments = {}
    if taiwan:
        from app.taiwan.universe import get_security_master

        try:
            master = get_security_master()
            instruments = {symbol: master.get_instrument(symbol) for symbol in taiwan}
        except Exception as exc:
            raise ValueError(f"Taiwan Security Master unavailable: {exc}") from exc
    supported = set(asset_types)
    scope = np.ones(len(symbols), dtype=bool)
    for asset_id, symbol in enumerate(symbols):
        if str(symbol) in instruments:
            instrument = instruments[str(symbol)]
            scope[asset_id] = instrument is not None and instrument.instrument_type in supported
    return scope


def _unsupported_asset_type_symbols(
    symbols: list[str] | tuple[str, ...],
    asset_types: list[str],
) -> set[str]:
    """Taiwan symbols the security master types outside *asset_types*.

    Symbols missing from the master are left to taiwan_limit_rule_exclusions, which excludes them.
    """
    taiwan_symbols = [
        str(symbol) for symbol in symbols if str(symbol).endswith(TAIWAN_SUFFIXES)
    ]
    if not taiwan_symbols:
        return set()
    from app.taiwan.universe import get_security_master

    supported = set(asset_types)
    try:
        master = get_security_master()
        instruments = {symbol: master.get_instrument(symbol) for symbol in taiwan_symbols}
    except Exception as exc:
        raise ValueError(f"Taiwan Security Master unavailable: {exc}") from exc
    return {
        symbol
        for symbol, instrument in instruments.items()
        if instrument is not None and instrument.instrument_type not in supported
    }


BOARD_LOT_MINIMUM_COMMISSION = 20.0
ODD_LOT_MINIMUM_COMMISSION = 1.0


def default_minimum_commission(lot_mode: str) -> float:
    """Common Taiwan broker floor per order: 20 TWD board lot, 1 TWD odd lot."""
    if lot_mode == "odd_lot":
        return ODD_LOT_MINIMUM_COMMISSION
    return BOARD_LOT_MINIMUM_COMMISSION


@dataclass
class StrategyBacktestConfig:
    strategy_id: str
    symbols: list[str] | None
    start: date
    end: date
    params: dict | None = None
    overrides: dict | None = None
    # matching 为向后兼容入口; 显式传 entry_fill/exit_fill 时以二者为准。
    matching: Literal["close_t", "open_t+1"] = "open_t+1"
    entry_fill: Literal["close_t", "open_t+1"] | None = None
    exit_fill: Literal["close_t", "open_t+1", "signal_next_minute"] | None = None
    fees_pct: float = 0.0002
    commission_pct: float | None = None
    commission_discount: float = 1.0
    # None resolves to the broker floor for lot_mode; callers that omit it keep no floor.
    minimum_commission: float | None = 0.0
    stamp_tax_pct: float | None = None
    slippage_bps: float = 5.0
    max_positions: int = 10
    max_exposure_pct: float = 1.0
    initial_capital: float = 1_000_000.0
    position_sizing: Literal["equal", "score_weight"] = "equal"
    mode: Literal["position", "full"] = "position"
    asset_type: str = "stock"
    lot_mode: Literal["board_lot", "odd_lot"] = "board_lot"
    holding_days: int = 5
    minimum_holding_sessions: int = 1
    day_trade_eligible_symbols: tuple[str, ...] = ()
    # 分钟K精确成交: 开启后用当日分钟K确定穿越价/VWAP (需 Pro+ 分钟K能力)
    minute_fill: bool = False
    # 市场环境过滤: {"states": ["strong",...], "min_score": 60}。
    # 强制 T-1: regime[T-1] 决定 entry[T](防未来函数)。None=不过滤。
    regime_filter: dict | None = None
    # Annual decimal rate for Sharpe/Sortino; does not change the market matrix.
    risk_free_rate: float = 0.0

    def __post_init__(self) -> None:
        if self.entry_fill is None:
            self.entry_fill = self.matching
        if self.exit_fill is None:
            self.exit_fill = self.matching
        self.risk_free_rate = validate_risk_free_rate(self.risk_free_rate)
        if self.minimum_commission is None:
            self.minimum_commission = default_minimum_commission(self.lot_mode)
        if self.minimum_holding_sessions < 0:
            raise ValueError("minimum_holding_sessions must be non-negative")
        if not 0 <= self.commission_discount <= 1:
            raise ValueError("commission_discount must be between 0 and 1")


@dataclass
class StrategyBacktestResult:
    run_id: str
    config: dict
    stats: dict = field(default_factory=dict)
    equity_curve: list[dict] = field(default_factory=list)
    drawdown_curve: list[dict] = field(default_factory=list)
    benchmark_curve: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    per_symbol_stats: list[dict] = field(default_factory=list)
    strategy_info: dict = field(default_factory=dict)
    elapsed_ms: float = 0.0
    error: str | None = None


class StrategyBacktestService:
    def __init__(
        self,
        engine: BacktestEngine,
        strategy_engine: StrategyEngine,
    ) -> None:
        self.engine = engine
        self.strategy_engine = strategy_engine

    def _resolve_composite_feature_plan(
        self,
        strategy: StrategyDef,
        *,
        params: dict,
        basic_filter: dict,
        overrides: dict,
        asset_type: str = "stock",
    ) -> tuple[ResolvedFeaturePlan, list[tuple[StrategyDef, dict, dict]]]:
        """解析 composite 回测的特征计划: 所有子策略 feature_plan 的并集。

        返回 (合并 feature_plan, [(子策略定义, 子params, 子pipeline_config_dict), ...])。
        子策略必须全为 matrix_native, 否则 fail-closed(首版硬约束)。
        """
        assert strategy.composite is not None
        children = self.strategy_engine.composite_children(strategy, overrides)

        resolver = StrategyDependencyResolver()
        plans: list[ResolvedFeaturePlan] = []
        resolved_children: list[tuple[StrategyDef, dict, dict]] = []
        for child in children:
            child_def = self.strategy_engine.get(child.strategy_id)
            if child_def.execution_backend != "matrix_native":
                raise ValueError(
                    f"叠加回测暂仅支持矩阵子策略; {child.strategy_id!r} "
                    f"是 {child_def.execution_backend}"
                )
            if child_def.matrix_strategy is None:
                raise ValueError(f"子策略 {child.strategy_id!r} 未注册矩阵策略")
            # 加载子策略的用户 override(参数/评分等), 保证回测与单独跑子策略同口径。
            child_override: dict = {}
            loader = getattr(self.strategy_engine, "_override_loader", None)
            if loader is not None:
                try:
                    loaded = loader(child.strategy_id)
                    if isinstance(loaded, dict):
                        child_override = dict(loaded)
                except Exception:  # noqa: BLE001
                    pass
            child_params = self.strategy_engine.resolve_params(child_def, overrides=child_override)
            child_plan = resolver.resolve(
                child_def,
                params=child_params,
                basic_filter=basic_filter,  # 统一 basic_filter(计划 §3.3)
                entry_signals=[],
                exit_signals=[],
                overrides=child_override,
                asset_type=asset_type,
            )
            plans.append(child_plan)
            # pipeline 用 composite 统一的 basic_filter; scoring 用子策略自己的
            # (默认 + 用户 override), 因为子策略内部排序影响合并器的排名融合。
            child_scoring = effective_scoring(child_def.meta.get("scoring"), child_override)
            child_pipeline_cfg = MatrixPipelineConfig(
                basic_filter=basic_filter,
                scoring=child_scoring,
                scoring_directions=effective_scoring_directions(
                    child_override, defaults=child_def.meta.get("scoring_directions")
                ),
                order_by=child_def.meta.get("order_by"),
                descending=bool(child_def.meta.get("descending", True)),
            )
            resolved_children.append((child_def, child_params, child_pipeline_cfg))

        merged_plan = _merge_resolved_feature_plans(plans)
        # composite 模块用于合并时读取权重列表(顺序对齐 resolved_children)。
        self._composite_children_weights = [(c.strategy_id, c.weight) for c in children]
        return merged_plan, resolved_children

    def _generate_composite_signal_matrix(
        self,
        resolved_children: list[tuple[StrategyDef, dict, dict]],
        market_data: MarketDataMatrix,
        merge_mode: str,
        min_confirm: int,
        max_hold: int,
        timing_ms: dict[str, float],
        asset_mask: np.ndarray | None = None,
    ):
        """逐子策略计算 SignalMatrix, 再合并为单个 SignalMatrix(回测合并)。

        合并语义见 app.strategy.composite.merge_signal_matrices:
        - entry: union/intersect
        - exit: 来源投影(每个子的 exit 仅在自己持仓窗口生效, 不串平)
        - score: 标准化排名加权
        """
        from app.strategy import composite as composite_mod

        t_signals = time.perf_counter()
        sigs = []
        for child_def, child_params, child_pipeline_cfg in resolved_children:
            if asset_mask is not None:
                child_pipeline_cfg = replace(child_pipeline_cfg, asset_mask=asset_mask)
            try:
                child_sig = MatrixStrategyPipeline().run(
                    child_def.matrix_strategy,
                    market_data,
                    child_params,
                    child_pipeline_cfg,
                )
            except (TypeError, ValueError) as e:
                raise ValueError(f"子策略 {child_def.meta.get('id')} 信号计算失败: {e}") from e
            sigs.append(child_sig)
        timing_ms["strategy_signals"] = round((time.perf_counter() - t_signals) * 1000, 1)

        children_weights = getattr(self, "_composite_children_weights", None) or [
            (cd.meta.get("id", ""), 1.0) for cd, _, _ in resolved_children
        ]
        return composite_mod.merge_signal_matrices(
            market_data.shape,
            sigs,
            children_weights,
            merge_mode,
            min_confirm,
            max_hold,
        )

    def run(
        self,
        config: StrategyBacktestConfig,
        progress_cb: Callable[[dict], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> StrategyBacktestResult:
        t0 = time.perf_counter()
        run_id = uuid.uuid4().hex[:10]

        def _err(msg: str) -> StrategyBacktestResult:
            return StrategyBacktestResult(
                run_id=run_id,
                config=self._config_to_dict(config),
                error=msg,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
            )

        # 获取策略定义
        try:
            s = self.strategy_engine.get(config.strategy_id)
            StrategyEngine.validate_context(
                s,
                StrategyDataContext(
                    asset_type=config.asset_type,
                    timeframe="1d",
                    as_of=config.end,
                ),
            )
        except ValueError as e:
            return _err(str(e))

        params = self._normalize_params(config.params or {}, s)
        overrides = config.overrides or {}
        basic_filter = self._backtest_basic_filter(s, overrides, config.asset_type)
        entry_signals = self._effective_signals(overrides, "entry_signals", s.entry_signals)
        exit_signals = self._effective_signals(overrides, "exit_signals", s.exit_signals)
        if config.exit_fill == "signal_next_minute":
            if not config.minute_fill:
                return _err("触发后下一分钟成交需要先开启分钟成交")
            if not exit_signals:
                return _err("当前策略没有卖出信号，无法使用触发后下一分钟成交")
            unsupported = unsupported_minute_exit_signals(exit_signals)
            if unsupported:
                return _err(f"以下卖出信号暂不支持分钟触发回放: {', '.join(unsupported)}")
        stop_loss = self._override_value(overrides, "stop_loss", s.stop_loss)
        take_profit = self._normalize_pct(
            self._override_value(overrides, "take_profit", getattr(s, "take_profit", None)),
            0.01,
            5.0,
        )
        trailing_stop = self._normalize_pct(
            self._override_value(overrides, "trailing_stop", getattr(s, "trailing_stop", None)),
            0.005,
            0.5,
        )
        trailing_take_profit_activate = self._normalize_pct(
            self._override_value(overrides, "trailing_take_profit_activate", getattr(s, "trailing_take_profit_activate", None)),
            0.01,
            2.0,
        )
        trailing_take_profit_drawdown = self._normalize_pct(
            self._override_value(overrides, "trailing_take_profit_drawdown", getattr(s, "trailing_take_profit_drawdown", None)),
            0.005,
            0.5,
        )
        if trailing_take_profit_activate is not None and trailing_take_profit_drawdown is not None:
            trailing_take_profit_drawdown = min(trailing_take_profit_drawdown, trailing_take_profit_activate)
        max_hold_days = self._override_value(overrides, "max_hold_days", s.max_hold_days)
        score_min, score_max = self._normalize_score_range(
            overrides.get("score_min"),
            overrides.get("score_max"),
        )

        try:
            if s.execution_backend == "composite":
                # composite 回测: 子策略必须全为 matrix_native(否则 fail-closed),
                # feature_plan 取所有子策略计划的并集(_merge_resolved_feature_plans)。
                feature_plan, composite_children_resolved = self._resolve_composite_feature_plan(
                    s,
                    params=params,
                    basic_filter=basic_filter,
                    overrides=overrides,
                    asset_type=config.asset_type,
                )
            else:
                composite_children_resolved = None
                feature_plan = StrategyDependencyResolver().resolve(
                    s,
                    params=params,
                    basic_filter=basic_filter,
                    entry_signals=entry_signals,
                    exit_signals=exit_signals,
                    overrides=overrides,
                    minute_fill=config.minute_fill,
                    asset_type=config.asset_type,
                )
        except ValueError as e:
            return _err(str(e))

        timing_ms: dict[str, float] = {}
        matrix_data_cache_hit = False
        matrix_data_cache_status = "none"
        matrix_data_cache_timing_ms: Mapping[str, float] = {}

        # 加载 warmup + 正式区间。矩阵策略的 warmup 由协议解析，不再依赖策略名称。
        load_start = config.start - timedelta(days=warmup_calendar_days(feature_plan.warmup_bars))

        # 全量模式: entries 只在正式区间触发, exits 需要 end 之后的尾部数据继续执行策略卖点。
        # 若策略有 max_hold_days, 用它决定尾部窗口；否则 holding_days 只作为兜底观察上限。
        full_horizon_days = int(max_hold_days or config.holding_days or 5)
        full_horizon_days = max(full_horizon_days, 1)
        load_end = config.end
        if config.mode == "full":
            fwd_buffer = full_horizon_days + 5  # 多取几天, 容错停牌缺口/open_t+1
            load_end = config.end + timedelta(days=fwd_buffer * 2)  # 日历日放宽, 确保覆盖 N 个交易日

        sim_end = load_end if config.mode == "full" else config.end
        panel: pl.DataFrame | None = None
        formal_range: pl.Series | None = None
        market_data: MarketDataMatrix | None = None
        if s.execution_backend in ("matrix_native", "composite"):
            t_load = time.perf_counter()
            max_hold_for_profile = self._override_value(
                overrides,
                "max_hold_days",
                s.max_hold_days,
            )
            profile_forward = max(int(max_hold_for_profile or config.holding_days or 5), 1)
            cache_profile = build_matrix_cache_profile(
                self.strategy_engine,
                config.asset_type,
                requested_plan=feature_plan,
                requested_forward_bars=profile_forward,
                max_disk_bytes=settings.backtest_matrix_cache_max_mb * 1024 * 1024,
                backtest_engine=self.engine,
            )
            coverage_start = config.start - timedelta(
                days=warmup_calendar_days(cache_profile.warmup_bars)
            )
            coverage_end = config.end
            if config.mode == "full":
                coverage_end = config.end + timedelta(
                    days=(cache_profile.forward_bars + 5) * 2
                )
            try:
                market_data = self.engine.load_market_data_matrix_for_backtest(
                    config.symbols,
                    load_start,
                    load_end,
                    feature_plan,
                    asset_type=config.asset_type,
                    cache_profile=cache_profile,
                    coverage_start=coverage_start,
                    coverage_end=coverage_end,
                )
            except (ValueError, OSError) as e:
                return _err(f"回测矩阵准备失败: {e}")
            direct_load_ms = round((time.perf_counter() - t_load) * 1000, 1)
            timing_ms["load_panel"] = direct_load_ms
            timing_ms["market_data_matrix_build"] = 0.0
            timing_ms["market_data_direct_load"] = direct_load_ms
            matrix_data_cache_status = market_data.cache_status
            matrix_data_cache_hit = matrix_data_cache_status in {"exact", "covering"}
            matrix_data_cache_timing_ms = market_data.cache_timing_ms
            # 环境过滤下正式起点=矩阵首日时顺延 (首日让渡为预热)
            if config.regime_filter:
                config = self._clamp_regime_formal_start(
                    config, market_data.timestamp_labels
                )
            formal_time_mask = self._matrix_date_range_mask(
                market_data.timestamp_labels,
                config.start,
                config.end,
            )
            if not formal_time_mask.any():
                return _err("正式回测区间内无数据")
            feature_width = len(feature_plan.matrix_columns)
        else:
            t_load = time.perf_counter()
            try:
                panel = self.engine.load_panel_for_backtest(
                    config.symbols,
                    load_start,
                    load_end,
                    feature_plan,
                    asset_type=config.asset_type,
                )
            except (ValueError, pl.exceptions.PolarsError) as e:
                return _err(f"回测特征准备失败: {e}")
            timing_ms["load_panel"] = round((time.perf_counter() - t_load) * 1000, 1)
            if panel.is_empty():
                return _err("无数据，请检查日期范围或先运行盘后管道")
            # 环境过滤下正式起点=面板首日时顺延 (首日让渡为预热)
            if config.regime_filter:
                date_labels = tuple(
                    str(value)[:10]
                    for value in panel.get_column("date").unique().sort().to_list()
                )
                config = self._clamp_regime_formal_start(config, date_labels)
            formal_range = self._date_range_mask(panel, config.start, config.end)
            if not formal_range.any():
                return _err("正式回测区间内无数据")
            feature_width = int(panel.width)

        if composite_children_resolved is not None:
            scoring_fields = _scoring_fields(
                [child_config.scoring for _, _, child_config in composite_children_resolved]
            )
            parameter_scoring_fields = _parameter_scoring_fields(
                [(child_def, child_params) for child_def, child_params, _ in composite_children_resolved]
            )
            entry_coverage_fields = _required_coverage_fields(
                [
                    (child_def, child_params)
                    for child_def, child_params, _ in composite_children_resolved
                ]
            )
        else:
            scoring_fields = _scoring_fields([effective_scoring(s.meta.get("scoring"), overrides)])
            parameter_scoring_fields = _parameter_scoring_fields(
                [(s, params)] if s.execution_backend == "matrix_native" else []
            )
            entry_coverage_fields = _required_coverage_fields(
                [(s, params)] if s.execution_backend == "matrix_native" else []
            )
        filter_fields = _filter_fields(basic_filter)
        exchange_coverage_warnings: list[dict] = []
        if market_data is not None:
            formal_sessions = self._matrix_date_range_mask(
                market_data.timestamp_labels, config.start, config.end
            )
            try:
                scope = (
                    _asset_scope_mask(
                        market_data.symbols,
                        StrategyEngine.universe_asset_types(s, config.asset_type),
                    )
                    if entry_coverage_fields
                    else None
                )
            except ValueError as e:
                return _err(str(e))
            uncovered_session, exchange_coverage_warnings = _entry_coverage_check(
                market_data, entry_coverage_fields, formal_sessions, scope
            )
            if uncovered_session:
                return _err(uncovered_session)
            formal_rows = np.isfinite(market_data.close) & formal_sessions[:, None]
            universe_coverage = _matrix_field_coverage(
                market_data, filter_fields | parameter_scoring_fields, formal_rows
            )
        elif panel is not None and formal_range is not None:
            universe_coverage = _panel_field_coverage(
                panel.filter(formal_range).filter(finite_or_null(pl.col("close")).is_not_null()),
                filter_fields | parameter_scoring_fields,
            )
        else:
            universe_coverage = {}
        uncovered = sorted(name for name in filter_fields if universe_coverage.get(name) == 0)
        if uncovered:
            listed = "、".join(field_label(name) for name in uncovered)
            return _err(f"以下過濾欄位在回測區間內沒有資料：{listed}")  # noqa: RUF001
        low_coverage = _low_scoring_coverage_error(
            {
                name: ratio
                for name, ratio in universe_coverage.items()
                if name in parameter_scoring_fields
            }
        )
        if low_coverage:
            return _err(low_coverage)

        matcher_config = MatcherConfig(
            matching=config.matching,
            entry_fill=config.entry_fill,
            exit_fill=config.exit_fill,
            fees_pct=config.fees_pct,
            commission_pct=config.commission_pct,
            commission_discount=config.commission_discount,
            minimum_commission=config.minimum_commission,
            stamp_tax_pct=config.stamp_tax_pct,
            slippage_bps=config.slippage_bps,
            stop_loss_pct=stop_loss,
            take_profit_pct=take_profit,
            trailing_stop_pct=trailing_stop,
            trailing_take_profit_activate_pct=trailing_take_profit_activate,
            trailing_take_profit_drawdown_pct=trailing_take_profit_drawdown,
            max_hold_days=max_hold_days,
            minimum_holding_sessions=config.minimum_holding_sessions,
            day_trade_eligible_symbols=tuple(config.day_trade_eligible_symbols),
            max_positions=config.max_positions,
            max_exposure_pct=config.max_exposure_pct,
            score_min=score_min,
            score_max=score_max,
            score_higher_first=(
                bool(s.meta.get("descending", True))
                if s.execution_backend == "composite"
                else ranks_higher_first(s.meta, effective_scoring(s.meta.get("scoring"), overrides))
            ),
            initial_capital=config.initial_capital,
            position_sizing=config.position_sizing,
            minute_fill=config.minute_fill,
            asset_type=config.asset_type,
            lot_mode=config.lot_mode,
            risk_free_rate=config.risk_free_rate,
        )
        if market_data is not None:
            execution_symbols = list(market_data.symbols)
        elif panel is not None:
            execution_symbols = [
                str(value)
                for value in panel.get_column("symbol").unique().to_list()
            ]
        else:
            return _err("回测缺少可执行标的")
        try:
            excluded_symbols = taiwan_limit_rule_exclusions(execution_symbols)
            excluded = {item["symbol"] for item in excluded_symbols}
            if excluded and excluded >= set(execution_symbols):
                return _err(f"回测标的均缺少已确认的涨跌幅规则: {', '.join(sorted(excluded))}")
            asset_types = StrategyEngine.universe_asset_types(s, config.asset_type)
            wrong_type = _unsupported_asset_type_symbols(execution_symbols, asset_types)
            excluded |= wrong_type
            if wrong_type and excluded >= set(execution_symbols):
                listed = sorted(wrong_type)
                shown = ", ".join(listed[:5]) + (f" 等 {len(listed)} 檔" if len(listed) > 5 else "")
                return _err(f"回測僅含 {'/'.join(asset_types)} 類型標的, 以下標的類型不符: {shown}")
            _configure_taiwan_matcher(matcher_config, [s for s in execution_symbols if s not in excluded])
        except ValueError as e:
            return _err(str(e))
        t_signal = time.perf_counter()
        selection_stats: dict[str, int | bool]

        if s.execution_backend == "composite":
            # composite 回测信号生成: 复用 matrix 数据加载, 逐子策略算信号后合并。
            # 退出采用来源投影(composite.merge_signal_matrices), 不串平其他子策略仓位。
            if composite_children_resolved is None:
                return _err("叠加策略子策略解析失败")
            if market_data is None:
                return _err("矩阵回测缺少基础行情矩阵")
            entry_time_mask = self._matrix_date_range_mask(
                market_data.timestamp_labels,
                config.start,
                config.end,
            )
            # 市场环境过滤(强制 T-1): 只叠加 entry, 不影响 exit
            try:
                _rm = self._build_regime_mask(
                    market_data.timestamp_labels, config.regime_filter,
                    getattr(getattr(self.engine.repo, "store", None), "data_dir", None),
                    required_start=config.start,
                    required_end=config.end,
                )
            except ValueError as e:
                return _err(str(e))
            if _rm is not None:
                entry_time_mask = entry_time_mask & _rm
            exit_time_mask = self._matrix_date_range_mask(
                market_data.timestamp_labels,
                config.start,
                load_end if config.mode == "full" else config.end,
            )
            sim_time_mask = self._matrix_date_range_mask(
                market_data.timestamp_labels,
                config.start,
                sim_end,
            )
            time_ids = np.flatnonzero(sim_time_mask)
            if time_ids.size == 0:
                return _err("正式回测区间内无数据")
            start_id = int(time_ids[0])
            stop_id = int(time_ids[-1]) + 1
            panel_rows = int(np.isfinite(market_data.close[start_id:stop_id]).sum())
            panel_columns = len(feature_plan.matrix_columns)
            entry_reference_price = (
                _minute_fill_reference(
                    market_data.close,
                    str(matcher_config.entry_fill),
                )[start_id:stop_id]
                if matcher_config.minute_fill
                else None
            )
            exit_reference_price = (
                _minute_fill_reference(
                    market_data.close,
                    str(matcher_config.exit_fill),
                )[start_id:stop_id]
                if matcher_config.minute_fill
                else None
            )

            merge_mode = str(params.get("merge_mode") or "union")
            min_confirm = int(params.get("min_confirm") or 0)
            # max_hold 用于退出投影窗口封顶; 无值时给一个足够大的兜底(仅靠信号退出)。
            composite_max_hold = max(int(max_hold_days or 0), 1) if max_hold_days else 250
            try:
                signal_matrix = self._generate_composite_signal_matrix(
                    composite_children_resolved,
                    market_data,
                    merge_mode,
                    min_confirm,
                    composite_max_hold,
                    timing_ms,
                    asset_mask=asset_mask_without(market_data.symbols, excluded),
                )
            except ValueError as e:
                return _err(str(e))

            sim_market_data = slice_market_data_matrix(market_data, start_id, stop_id)
            sim_signal_matrix = slice_signal_matrix(signal_matrix, start_id, stop_id)
            sim_signal_matrix = apply_time_masks(
                sim_signal_matrix,
                entry_time_mask[start_id:stop_id],
                exit_time_mask[start_id:stop_id],
            )
            timing_ms["signals_score"] = round((time.perf_counter() - t_signal) * 1000, 1)
            if not sim_signal_matrix.entry.any():
                return _err("在指定区间内未产生买入信号")

            raw_candidates = int(sim_signal_matrix.entry.sum())
            selection_stats = {
                "strategy_matches": raw_candidates,
                "entry_candidates": raw_candidates,
                "entry_trigger_filtered": 0,
                "entry_trigger_enabled": False,
            }
            scoring_coverage = _matrix_field_coverage(
                sim_market_data, scoring_fields, sim_signal_matrix.entry != 0
            )
            del market_data, signal_matrix

            t_matrix = time.perf_counter()
            market_matrix = build_market_matrix_from_signals(
                sim_market_data,
                sim_signal_matrix,
                entry_delay_bars=1 if matcher_config.entry_fill == "open_t+1" else 0,
                exit_delay_bars=1 if matcher_config.exit_fill == "open_t+1" else 0,
                entry_reference_price=entry_reference_price,
                exit_reference_price=exit_reference_price,
                minute_exit_trigger=matcher_config.exit_fill == "signal_next_minute",
            )
            timing_ms["matrix_build"] = round((time.perf_counter() - t_matrix) * 1000, 1)
            del sim_market_data, sim_signal_matrix
        elif s.execution_backend == "matrix_native":
            if s.matrix_strategy is None:
                return _err("矩阵策略未注册")
            if self._has_matrix_signal_override(s, overrides):
                return _err("matrix_native 策略的进出场信号由策略协议生成，不支持列信号覆盖")

            if market_data is None:
                return _err("矩阵回测缺少基础行情矩阵")
            entry_time_mask = self._matrix_date_range_mask(
                market_data.timestamp_labels,
                config.start,
                config.end,
            )
            try:
                _rm = self._build_regime_mask(
                    market_data.timestamp_labels, config.regime_filter,
                    getattr(getattr(self.engine.repo, "store", None), "data_dir", None),
                    required_start=config.start,
                    required_end=config.end,
                )
            except ValueError as e:
                return _err(str(e))
            if _rm is not None:
                entry_time_mask = entry_time_mask & _rm
            exit_time_mask = self._matrix_date_range_mask(
                market_data.timestamp_labels,
                config.start,
                load_end if config.mode == "full" else config.end,
            )
            sim_time_mask = self._matrix_date_range_mask(
                market_data.timestamp_labels,
                config.start,
                sim_end,
            )
            time_ids = np.flatnonzero(sim_time_mask)
            if time_ids.size == 0:
                return _err("正式回测区间内无数据")
            start_id = int(time_ids[0])
            stop_id = int(time_ids[-1]) + 1
            panel_rows = int(np.isfinite(market_data.close[start_id:stop_id]).sum())
            panel_columns = len(feature_plan.matrix_columns)
            entry_reference_price = (
                _minute_fill_reference(
                    market_data.close,
                    str(matcher_config.entry_fill),
                )[start_id:stop_id]
                if matcher_config.minute_fill
                else None
            )
            exit_reference_price = (
                _minute_fill_reference(
                    market_data.close,
                    str(matcher_config.exit_fill),
                )[start_id:stop_id]
                if matcher_config.minute_fill
                else None
            )

            scoring = effective_scoring(s.meta.get("scoring"), overrides)
            try:
                pipeline_config = MatrixPipelineConfig(
                    basic_filter=basic_filter,
                    scoring=scoring,
                    scoring_directions=effective_scoring_directions(
                        overrides, defaults=s.meta.get("scoring_directions")
                    ),
                    order_by=s.meta.get("order_by"),
                    descending=bool(s.meta.get("descending", True)),
                    asset_mask=asset_mask_without(market_data.symbols, excluded),
                )
                signal_matrix = MatrixStrategyPipeline().run(
                    s.matrix_strategy,
                    market_data,
                    params,
                    pipeline_config,
                    timing_ms,
                )
            except (TypeError, ValueError) as e:
                return _err(f"矩阵策略信号计算失败: {e}")

            sim_market_data = slice_market_data_matrix(market_data, start_id, stop_id)
            sim_signal_matrix = slice_signal_matrix(signal_matrix, start_id, stop_id)
            sim_signal_matrix = apply_time_masks(
                sim_signal_matrix,
                entry_time_mask[start_id:stop_id],
                exit_time_mask[start_id:stop_id],
            )
            timing_ms["signals_score"] = round((time.perf_counter() - t_signal) * 1000, 1)
            if not sim_signal_matrix.entry.any():
                return _err("在指定区间内未产生买入信号")

            raw_candidates = int(sim_signal_matrix.entry.sum())
            selection_stats = {
                "strategy_matches": raw_candidates,
                "entry_candidates": raw_candidates,
                "entry_trigger_filtered": 0,
                "entry_trigger_enabled": False,
            }
            scoring_coverage = _matrix_field_coverage(
                sim_market_data, scoring_fields, sim_signal_matrix.entry != 0
            )
            del market_data, signal_matrix

            t_matrix = time.perf_counter()
            market_matrix = build_market_matrix_from_signals(
                sim_market_data,
                sim_signal_matrix,
                entry_delay_bars=1 if matcher_config.entry_fill == "open_t+1" else 0,
                exit_delay_bars=1 if matcher_config.exit_fill == "open_t+1" else 0,
                entry_reference_price=entry_reference_price,
                exit_reference_price=exit_reference_price,
                minute_exit_trigger=matcher_config.exit_fill == "signal_next_minute",
            )
            timing_ms["matrix_build"] = round((time.perf_counter() - t_matrix) * 1000, 1)
            del sim_market_data, sim_signal_matrix
        else:
            if panel is None or formal_range is None:
                return _err("非矩阵策略不能使用共享基础矩阵")
            # basic_filter 只影响买入候选，不能删除持仓估值和卖出所需行情。
            basic_mask = pl.Series("_basic", [True] * len(panel), dtype=pl.Boolean)
            if basic_filter and basic_filter.get("enabled", True):
                try:
                    expr = StrategyEngine._basic_filter_expr(panel, basic_filter)
                    if expr is not None:
                        basic_mask = panel.select(expr.alias("_basic"))["_basic"].fill_null(False).cast(pl.Boolean)
                except Exception as e:  # noqa: BLE001
                    logger.warning("basic_filter mask failed: %s", e)
                    return _err(f"基础过滤计算失败: {e}")

            candidate_filter_mask = self._build_candidate_filter_mask(panel, s, params)
            candidate_mask = basic_mask & candidate_filter_mask & ~panel.get_column("symbol").is_in(sorted(excluded))
            entry_candidate_mask = self._build_entry_mask_from_candidate(panel, candidate_mask, s, entry_signals)
            # Scores rank a session's entry candidates, as the matrix pipeline scores its entries.
            try:
                panel = self._apply_score(panel, s, overrides, universe_mask=entry_candidate_mask)
            except ValueError as e:
                return _err(f"评分计算失败: {e}")
            formal_candidate_mask = candidate_mask & formal_range
            entry_mask = entry_candidate_mask & formal_range
            if config.regime_filter:
                date_values = panel.get_column("date").unique().sort().to_list()
                date_labels = tuple(str(value)[:10] for value in date_values)
                try:
                    regime_time_mask = self._build_regime_mask(
                        date_labels,
                        config.regime_filter,
                        getattr(getattr(self.engine.repo, "store", None), "data_dir", None),
                        required_start=config.start,
                        required_end=config.end,
                    )
                except ValueError as e:
                    return _err(str(e))
                if regime_time_mask is not None:
                    allowed_dates = [
                        value for value, allowed in zip(date_values, regime_time_mask, strict=True)
                        if allowed
                    ]
                    regime_row_mask = panel.get_column("date").is_in(allowed_dates).fill_null(False)
                    formal_candidate_mask = formal_candidate_mask & regime_row_mask
                    entry_mask = entry_mask & regime_row_mask
            raw_exit_mask = self._build_signal_mask(panel, exit_signals, "_exit")
            exit_range = self._date_range_mask(panel, config.start, load_end) if config.mode == "full" else formal_range
            exit_mask = raw_exit_mask & exit_range
            timing_ms["signals_score"] = round((time.perf_counter() - t_signal) * 1000, 1)
            if not entry_mask.any():
                return _err("在指定区间内未产生买入信号")
            scoring_coverage = _panel_field_coverage(panel.filter(entry_mask), scoring_fields)

            sim_range = self._date_range_mask(panel, config.start, sim_end)
            sim_columns = [
                column
                for column in feature_plan.matrix_columns | {BASE_MISSING_COLUMN}
                if column in panel.columns
            ]
            sim_panel = panel.filter(sim_range).select(sorted(sim_columns))
            sim_entry_mask = entry_mask.filter(sim_range)
            sim_exit_mask = exit_mask.filter(sim_range)
            if sim_panel.is_empty():
                return _err("正式回测区间内无数据")
            panel_rows = int(sim_panel.height)
            panel_columns = int(sim_panel.width)
            raw_candidates = int(sim_entry_mask.sum())
            strategy_matches = int(formal_candidate_mask.sum())
            selection_stats = {
                "strategy_matches": strategy_matches,
                "entry_candidates": raw_candidates,
                "entry_trigger_filtered": max(strategy_matches - raw_candidates, 0),
                "entry_trigger_enabled": bool(entry_signals),
            }

            t_matrix = time.perf_counter()
            data_dir = getattr(getattr(self.engine.repo, "store", None), "data_dir", None)
            market_matrix = build_market_matrix(
                sim_panel,
                sim_entry_mask,
                sim_exit_mask,
                entry_delay_bars=1 if matcher_config.entry_fill == "open_t+1" else 0,
                exit_delay_bars=1 if matcher_config.exit_fill == "open_t+1" else 0,
                entry_signal_ids=entry_signals,
                exit_signal_ids=exit_signals,
                minute_exit_trigger=matcher_config.exit_fill == "signal_next_minute",
                session_dates=(
                    partition_session_dates(Path(data_dir) / ENRICHED_DIR_NAME, config.start, sim_end)
                    if isinstance(data_dir, (str, Path)) else None
                ),
            )
            timing_ms["matrix_build"] = round((time.perf_counter() - t_matrix) * 1000, 1)
            del panel, sim_panel, sim_entry_mask, sim_exit_mask

        low_coverage = _low_scoring_coverage_error(scoring_coverage)
        if low_coverage:
            return _err(low_coverage)
        field_coverage = {
            name: round(ratio, 4)
            for name, ratio in sorted({**universe_coverage, **scoring_coverage}.items())
        }

        t_sim = time.perf_counter()

        # 撮合 — 两条生产路径共享同一只读 MarketMatrix。
        if config.mode == "full":
            result = self.engine.simulate_independent_market_matrix(
                market_matrix,
                raw_candidates,
                matcher_config,
                progress_cb,
                cancel_event,
                formal_end=config.end,
            )
        else:
            result = self.engine.simulate_market_matrix(
                market_matrix,
                matcher_config,
                progress_cb,
                cancel_event,
            )
        timing_ms["simulate"] = round((time.perf_counter() - t_sim) * 1000, 1)
        timing_ms["statistics"] = float(result.stats.pop("statistics_ms", 0.0))

        # 检查是否被取消
        if cancel_event is not None and cancel_event.is_set():
            return StrategyBacktestResult(
                run_id=run_id,
                config=self._config_to_dict(config),
                error="cancelled",
                elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
            )

        if result.stats.get("error"):
            return _err(result.stats["error"])

        timing_ms["total"] = round((time.perf_counter() - t0) * 1000, 1)
        result.stats["timing_ms"] = timing_ms
        result.stats["panel_rows"] = panel_rows
        result.stats["panel_columns"] = panel_columns
        result.stats["feature_columns"] = feature_width
        result.stats["full_feature_fallback"] = feature_plan.full_feature_fallback
        result.stats["execution_backend"] = s.execution_backend
        result.stats["selection"] = selection_stats
        result.stats["field_coverage"] = field_coverage
        result.stats["exchange_coverage_warnings"] = exchange_coverage_warnings
        result.stats["excluded_symbols"] = excluded_symbols
        result.stats["minimum_commission"] = config.minimum_commission
        result.stats["matrix_data_cache_hit"] = matrix_data_cache_hit
        result.stats["matrix_data_cache_status"] = matrix_data_cache_status
        result.stats["matrix_data_cache_timing_ms"] = dict(matrix_data_cache_timing_ms)

        start_text, end_text = str(config.start), str(config.end)
        sessions = [
            label[:10]
            for label in market_matrix.timestamp_labels
            if start_text <= label[:10] <= end_text
        ]
        benchmark_curve, benchmark_info, benchmark_return = self._build_benchmark(
            config.start, config.end, sessions,
        )
        total_return = result.stats.get("total_return")
        # Full-mode total_return compounds candidate samples, not an account, so it has no excess.
        excess = (
            round(float(total_return) - benchmark_return, 4)
            if config.mode != "full" and benchmark_return is not None and total_return is not None
            else None
        )
        result.stats["benchmark"] = benchmark_info
        result.stats["benchmark_return"] = benchmark_return
        result.stats["excess"] = excess

        # 构建策略信息
        strategy_info = {
            "id": s.meta.get("id", config.strategy_id),
            "name": s.meta.get("name", config.strategy_id),
            "description": s.meta.get("description", ""),
            "entry_signals": entry_signals,
            "exit_signals": exit_signals,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "trailing_stop": trailing_stop,
            "trailing_take_profit_activate": trailing_take_profit_activate,
            "trailing_take_profit_drawdown": trailing_take_profit_drawdown,
            "max_hold_days": max_hold_days,
            "full_horizon_days": full_horizon_days,
            "score_min": score_min,
            "score_max": score_max,
            "source": s.source,
            "execution_backend": s.execution_backend,
            **(
                {
                    "composite_children": [
                        {
                            "id": cid,
                            "weight": cw,
                        }
                        for cid, cw in getattr(self, "_composite_children_weights", [])
                    ]
                }
                if s.execution_backend == "composite"
                else {}
            ),
        }

        elapsed = (time.perf_counter() - t0) * 1000

        return StrategyBacktestResult(
            run_id=run_id,
            config=self._config_to_dict(config),
            stats=result.stats,
            equity_curve=result.equity_curve,
            drawdown_curve=result.drawdown_curve,
            benchmark_curve=benchmark_curve,
            trades=[self._trade_to_dict(t) for t in result.trades],
            per_symbol_stats=result.per_symbol_stats,
            strategy_info=strategy_info,
            elapsed_ms=round(elapsed, 1),
        )

    # ── 向量化信号生成 ──

    @staticmethod
    def _date_range_mask(panel: pl.DataFrame, start: date, end: date) -> pl.Series:
        return panel.select(
            ((pl.col("date") >= start) & (pl.col("date") <= end)).alias("_range")
        )["_range"].fill_null(False).cast(pl.Boolean)

    @staticmethod
    def _matrix_date_range_mask(
        timestamp_labels: tuple[str, ...],
        start: date,
        end: date,
    ) -> np.ndarray:
        start_text = str(start)
        end_text = str(end)
        return np.fromiter(
            (start_text <= label[:10] <= end_text for label in timestamp_labels),
            dtype=bool,
            count=len(timestamp_labels),
        )

    @staticmethod
    def _clamp_regime_formal_start(
        config: StrategyBacktestConfig, labels: tuple[str, ...] | list[str]
    ) -> StrategyBacktestConfig:
        """环境过滤下正式起点=面板首日 (无前驱交易日) 时, 顺延到第二个交易日。

        数据边界即正式起点 (如「全部」范围) 时, T-1 环境校验会 fail-closed 拒绝;
        首日降级为预热后, 其环境即成为次日的 T-1, 仅损失 1 个正式交易日。
        """
        from app.backtest.regime_alignment import clamp_formal_start_for_regime

        shifted = clamp_formal_start_for_regime(labels, config.start, config.regime_filter)
        if shifted is not None and shifted != config.start:
            return replace(config, start=shifted)
        return config

    @staticmethod
    def _build_regime_mask(
        timestamp_labels: tuple[str, ...],
        regime_filter: dict | None,
        data_dir: Path | None,
        *,
        required_start: date | None = None,
        required_end: date | None = None,
    ) -> np.ndarray | None:
        """构造逐日 T-1 regime mask, 保留历史静态入口兼容调用方。"""
        if not regime_filter:
            return None
        allowed_states = set(regime_filter.get("states") or [])
        min_score = regime_filter.get("min_score")
        if not allowed_states and min_score is None:
            return None
        if data_dir is None:
            raise ValueError("市场环境过滤不可用: 未找到环境数据目录")

        from app.backtest.regime_alignment import build_regime_filter_mask
        from app.services import regime_builder

        regime_df = regime_builder.load_regime_history(data_dir)
        regime_by_date = {
            row["date"]: {
                "state": row.get("state", ""),
                "score": row.get("score", 0),
            }
            for row in regime_df.iter_rows(named=True)
            if row.get("date") is not None
        }
        return build_regime_filter_mask(
            timestamp_labels,
            regime_filter,
            regime_by_date,
            required_start=required_start,
            required_end=required_end,
        )

    def _build_candidate_filter_mask(
        self,
        panel: pl.DataFrame,
        s: StrategyDef,
        params: dict,
    ) -> pl.Series:
        """生成策略候选层 mask。filter_history/filter 决定候选池, 不包含 entry_signals。"""
        false_mask = pl.Series("_candidate_filter", [False] * len(panel), dtype=pl.Boolean)
        true_mask = pl.Series("_candidate_filter", [True] * len(panel), dtype=pl.Boolean)

        history_failed = False
        # 优先: filter_history_fn 策略 (涨停/反包等多日形态, 与选股路径共用同一逻辑)
        if s.filter_history_fn:
            try:
                hit_df = s.filter_history_fn(panel, params)
                if hit_df is None or hit_df.is_empty():
                    return false_mask
                # 命中行 (symbol,date) → 转 panel 等长布尔 mask
                hits = hit_df.select(["symbol", "date"]).unique()
                marked = (
                    panel.select(["symbol", "date"])
                    .join(
                        hits.with_columns(pl.lit(True).alias("_hit")),
                        on=["symbol", "date"],
                        how="left",
                    )
                )
                return marked["_hit"].fill_null(False).cast(pl.Boolean)
            except Exception as e:
                history_failed = True
                logger.warning("strategy filter_history_fn failed: %s", e)
                # 失败则回退到 filter_fn (若存在)

        # 策略 filter_fn: 候选层 (filter_history 不可用或失败时)
        if s.filter_fn:
            try:
                expr = s.filter_fn(panel, params)
                if expr is not None:
                    result = panel.select(expr.alias("_candidate_filter"))
                    if not result.is_empty():
                        return result["_candidate_filter"].fill_null(False).cast(pl.Boolean)
            except Exception as e:
                logger.warning("strategy filter_fn failed: %s", e)
                return false_mask

        if history_failed:
            return false_mask

        # 没有策略候选层时, 由 entry_signals 直接决定买点。
        return true_mask

    def _build_entry_mask_from_candidate(
        self,
        panel: pl.DataFrame,
        candidate_mask: pl.Series,
        s: StrategyDef,
        entry_signals: list[str],
    ) -> pl.Series:
        """向量化生成买入掩码：候选层 AND 买点层；无买点时只用策略候选层。"""
        signal_mask = self._build_signal_mask(panel, entry_signals, "_entry_signal")
        if entry_signals:
            return candidate_mask & signal_mask
        if s.filter_history_fn or s.filter_fn:
            return candidate_mask
        return pl.Series("_entry", [False] * len(panel), dtype=pl.Boolean)

    @staticmethod
    def _build_signal_mask(panel: pl.DataFrame, signals: list[str], name: str) -> pl.Series:
        """向量化合并信号列，多个信号 OR。支持内置 signal_ 与自定义 csg_ 前缀。"""
        masks: list[pl.Series] = []
        for sig in signals:
            # csg_ (自定义信号) 直接用；否则按 signal_ 解析
            col = sig if (sig.startswith("signal_") or sig.startswith("csg_")) else f"signal_{sig}"
            if col in panel.columns:
                masks.append(panel[col].fill_null(False).cast(pl.Boolean))

        if not masks:
            return pl.Series(name, [False] * len(panel), dtype=pl.Boolean)

        combined = masks[0]
        for m in masks[1:]:
            combined = combined | m
        return combined

    @staticmethod
    def _load_benchmark_closes(start: date, end: date) -> dict[str, float]:
        try:
            df = _read_local_taiex(start, end)
        except Exception as e:
            logger.warning("load benchmark %s failed: %s", TAIEX.symbol, e)
            return {}

        if df.is_empty() or "close" not in df.columns:
            return {}

        df = df.filter(pl.col("close").is_not_null() & (pl.col("close") > 0)).sort("date")
        return {
            str(day)[:10]: round(float(close), 4)
            for day, close in zip(df["date"].to_list(), df["close"].to_list(), strict=True)
        }

    def _build_benchmark(
        self,
        start: date,
        end: date,
        sessions: list[str],
    ) -> tuple[list[dict], dict, float | None]:
        """Benchmark curve on the given sessions, its metadata, and its period return.

        The return runs from the close of base_date, the first session, because a
        strategy holds nothing before that close. It is None unless the index has a
        close on the first and the last session; a session missing in between is left
        out of the curve. coverage_start and coverage_end bound the index rows found
        inside the queried period, not the whole local store.
        """
        symbol, name = TAIEX.symbol, TAIEX.name
        closes = self._load_benchmark_closes(start, end)
        curve = [
            {"date": day, "value": closes[day], "close": closes[day], "name": name, "symbol": symbol}
            for day in sessions
            if day in closes
        ]
        info = {
            "symbol": symbol,
            "name": name,
            "price_basis": "price_index",
            "includes_dividends": False,
            "base_date": sessions[0] if sessions else None,
            "coverage_start": min(closes) if closes else None,
            "coverage_end": max(closes) if closes else None,
            "missing_sessions": len(sessions) - len(curve),
        }
        benchmark_return = None
        if sessions and sessions[0] in closes and sessions[-1] in closes:
            benchmark_return = round(closes[sessions[-1]] / closes[sessions[0]] - 1, 4)
        return curve, info, benchmark_return

    # ── 工具 ──

    @staticmethod
    def _effective_basic_filter(s: StrategyDef, overrides: dict) -> dict:
        basic_filter = dict(s.basic_filter or {})
        override_filter = overrides.get("basic_filter")
        if isinstance(override_filter, dict):
            basic_filter.update(override_filter)
        return basic_filter

    def _backtest_basic_filter(self, s: StrategyDef, overrides: dict, asset_type: str) -> dict:
        return _basic_filter_for_data(
            self._effective_basic_filter(s, overrides), self.engine, asset_type
        )

    @staticmethod
    def _effective_signals(overrides: dict, key: str, default: list[str]) -> list[str]:
        value = overrides.get(key)
        if isinstance(value, list):
            return [str(v) for v in value if v]
        return list(default or [])

    @classmethod
    def _has_matrix_signal_override(cls, strategy: StrategyDef, overrides: dict) -> bool:
        """Allow legacy persisted defaults, but reject a real Matrix signal replacement."""
        for key, default in (
            ("entry_signals", strategy.entry_signals),
            ("exit_signals", strategy.exit_signals),
        ):
            if key not in overrides:
                continue
            actual = cls._effective_signals(overrides, key, default)
            expected = [_normalize_signal_name(str(signal)) for signal in (default or [])]
            normalized_actual = [_normalize_signal_name(signal) for signal in actual]
            if normalized_actual != expected:
                return True
        return False

    @staticmethod
    def _override_value(overrides: dict, key: str, default):
        if key in overrides:
            return overrides.get(key)
        return default

    @staticmethod
    def _normalize_pct(value, min_value: float, max_value: float) -> float | None:
        if value is None or value == "":
            return None
        try:
            pct = abs(float(value))
        except (TypeError, ValueError):
            return None
        return min(max(pct, min_value), max_value)

    @staticmethod
    def _normalize_score_range(min_value, max_value) -> tuple[float | None, float | None]:
        def _bound(value) -> float | None:
            if value is None or value == "":
                return None
            try:
                score = float(value)
            except (TypeError, ValueError):
                return None
            if not np.isfinite(score):
                return None
            return min(max(score, 0.0), 100.0)

        score_min = _bound(min_value)
        score_max = _bound(max_value)
        if score_min is not None and score_max is not None and score_min > score_max:
            score_min, score_max = score_max, score_min
        return score_min, score_max

    @staticmethod
    def _normalize_params(params: dict, s: StrategyDef) -> dict:
        normalized = dict(params)
        for param in s.meta.get("params", []):
            pid = param.get("id")
            if not pid:
                continue
            value = normalized.get(pid, param.get("default"))
            p_type = param.get("type")
            if p_type in {"float", "int"}:
                try:
                    num = float(value)
                except (TypeError, ValueError):
                    num = float(param.get("default", 0) or 0)
                if param.get("min") is not None:
                    num = max(num, float(param["min"]))
                if param.get("max") is not None:
                    num = min(num, float(param["max"]))
                normalized[pid] = int(num) if p_type == "int" else num
            elif p_type == "select" and param.get("options"):
                normalized[pid] = value if value in param["options"] else param.get("default")
            elif p_type == "bool":
                if isinstance(value, bool):
                    normalized[pid] = value
                elif isinstance(value, str):
                    normalized[pid] = value.lower() == "true"
                else:
                    normalized[pid] = bool(param.get("default", False))
            else:
                normalized[pid] = value
        return normalized

    @staticmethod
    def _trade_to_dict(t) -> dict:
        return {
            "symbol": t.symbol,
            "name": t.name,
            "entry_date": str(t.entry_date) if isinstance(t.entry_date, date) else str(t.entry_date),
            "exit_date": str(t.exit_date) if isinstance(t.exit_date, date) else str(t.exit_date),
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl_pct": t.pnl_pct,
            "duration": t.duration,
            "exit_reason": t.exit_reason,
            "shares": t.shares,
            "lots": t.lots,
            "position_pct": t.position_pct,
            "entry_value": t.entry_value,
            "exit_value": t.exit_value,
            "pnl_amount": t.pnl_amount,
            "entry_score": getattr(t, "entry_score", None),
            "entry_signal_date": str(t.entry_signal_date) if getattr(t, "entry_signal_date", None) is not None else None,
            "exit_signal_date": str(t.exit_signal_date) if getattr(t, "exit_signal_date", None) is not None else None,
            "blocked_exit_days": getattr(t, "blocked_exit_days", 0),
            "entry_signal_id": getattr(t, "entry_signal_id", None),
            "exit_signal_id": getattr(t, "exit_signal_id", None),
            "entry_price_adj": getattr(t, "entry_price_adj", None),
            "exit_price_adj": getattr(t, "exit_price_adj", None),
            "exit_shares": getattr(t, "exit_shares", None),
        }

    @staticmethod
    def _config_to_dict(c: StrategyBacktestConfig) -> dict:
        score_min, score_max = StrategyBacktestService._normalize_score_range(
            (c.overrides or {}).get("score_min"),
            (c.overrides or {}).get("score_max"),
        )
        return {
            "strategy_id": c.strategy_id,
            "symbols": c.symbols,
            "start": str(c.start),
            "end": str(c.end),
            "params": c.params,
            "overrides": c.overrides,
            "score_min": score_min,
            "score_max": score_max,
            "matching": c.matching,
            "entry_fill": c.entry_fill,
            "exit_fill": c.exit_fill,
            "timing_mode": (
                "strict"
                if c.entry_fill == "open_t+1" and c.exit_fill == "open_t+1"
                else "custom"
            ),
            "fees_pct": c.fees_pct,
            "commission_pct": c.commission_pct,
            "commission_discount": c.commission_discount,
            "minimum_commission": c.minimum_commission,
            "stamp_tax_pct": c.stamp_tax_pct,
            "slippage_bps": c.slippage_bps,
            "max_positions": c.max_positions,
            "max_exposure_pct": c.max_exposure_pct,
            "initial_capital": c.initial_capital,
            "position_sizing": c.position_sizing,
            "mode": c.mode,
            "holding_days": c.holding_days,
            "minimum_holding_sessions": c.minimum_holding_sessions,
            "day_trade_eligible_symbols": list(c.day_trade_eligible_symbols),
            "asset_type": c.asset_type,
            "lot_mode": c.lot_mode,
            "minute_fill": c.minute_fill,
            "regime_filter": c.regime_filter,
            "risk_free_rate": c.risk_free_rate,
        }

    @staticmethod
    def _apply_score(
        panel: pl.DataFrame,
        s: StrategyDef,
        overrides: dict | None,
        universe_mask: pl.Series | None = None,
    ) -> pl.DataFrame:
        """Candidates missing a scoring input score null; rows outside the universe score 0."""
        scoring = effective_scoring(s.meta.get("scoring"), overrides)
        directions = effective_scoring_directions(
            overrides, defaults=s.meta.get("scoring_directions")
        )

        work = materialize_scoring_columns(panel, scoring.keys())
        temporary_scoring_columns = [name for name in scoring if name not in panel.columns and name in work.columns]
        has_universe = universe_mask is not None and len(universe_mask) == len(panel)
        if has_universe:
            work = work.with_columns(universe_mask.rename("_score_universe"))

        def _value_in_universe(value: pl.Expr) -> pl.Expr:
            if has_universe:
                return pl.when(pl.col("_score_universe")).then(value).otherwise(None)
            return value

        def _finish(df: pl.DataFrame) -> pl.DataFrame:
            temporary = [
                name
                for name in ["_score_universe", *temporary_scoring_columns]
                if name in df.columns
            ]
            return df.drop(temporary) if temporary else df

        active = {str(name): weight for name, weight in scoring.items() if weight}
        total_weight = sum(active.values())
        if active and total_weight > 0:
            score_expr: pl.Expr | None = None
            for name, value, weight in resolve_scoring_values(work.columns, active):
                candidate_value = _value_in_universe(value)
                part = normalized_scoring_part(
                    value,
                    candidate_value.min().over("date"),
                    candidate_value.max().over("date"),
                    low_first=directions.get(name) == SCORING_DIRECTION_LOW,
                ) * (weight / total_weight)
                if has_universe:
                    part = pl.when(pl.col("_score_universe")).then(part).otherwise(0.0)
                score_expr = part if score_expr is None else score_expr + part
            return _finish(work.with_columns((score_expr * 100).alias("score")))

        order_by = s.meta.get("order_by")
        if order_by and order_by != "score" and order_by in work.columns:
            direction = 1 if s.meta.get("descending", True) else -1
            score_expr = finite_or_null(pl.col(order_by)) * direction
            if has_universe:
                score_expr = pl.when(pl.col("_score_universe")).then(score_expr).otherwise(0.0)
            return _finish(work.with_columns(score_expr.alias("score")))
        return _finish(work.with_columns(pl.lit(0.0).alias("score")))
