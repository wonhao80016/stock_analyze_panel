"""策略引擎 — 加载、执行、评分。

职责: 从文件系统加载策略 Python 模块，执行两阶段过滤(基础+策略)，
     通用评分排序。
不知道: AI、API、前端、配置持久化、回测。
"""
from __future__ import annotations

import importlib.util
import logging
import math
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from app.config import settings
from app.enriched_generation import EnrichedGenerationUnavailableError, get_enriched_generation
from app.strategy.field_labels import field_label, whole_percent
from app.strategy.listing_age import eligible_from, exclude_new_days
from app.strategy.scoring import (
    AMOUNT_ESTIMATED,
    SCORING_DIRECTION_LOW,
    effective_scoring,
    effective_scoring_directions,
    materialize_scoring_columns,
    meta_scoring_directions,
    scoring_dependencies,
    scoring_value_expr,
    scoring_warmup_bars,
)
from app.volume_units import TAIWAN_SUFFIXES

logger = logging.getLogger(__name__)

# Engine-wide basic filter defaults; a strategy's BASIC_FILTER is merged on top of them.
# Taiwan instruments never populate total_shares (see app/taiwan/universe), so a
# market_cap_min is ignored for them (without_unavailable_share_bounds) — keep None.
# exclude_st's "ST|*ST|退" name match is an A-share convention with no Taiwan
# equivalent data source, so it is disabled rather than left as a no-op default.
DEFAULT_BASIC_FILTER: dict = {
    "price_min": 3,
    "price_max": None,
    "market_cap_min": None,
    "float_cap_min": None,
    "float_cap_max": None,
    "amount_min": 0.2e8,
    "amount_max": None,
    "turnover_min": None,
    "turnover_max": None,
    "exclude_st": False,
    "exclude_new_days": 30,
    "boards": ["TWSE", "TPEX"],
}

_SHARE_CAP_BOUNDS: tuple[tuple[str, str], ...] = (
    ("market_cap", "total_shares"),
    ("float_cap", "float_shares"),
)
_BOUNDED_FIELDS: tuple[tuple[str, str], ...] = (
    *_SHARE_CAP_BOUNDS,
    ("amount", "amount"),
    ("turnover", "turnover_rate"),
)
# Basic-filter bounds on a price or a market value, which compare raw_close.
RAW_PRICE_BOUND_KEYS = (
    "price_min", "price_max",
    "market_cap_min", "market_cap_max",
    "float_cap_min", "float_cap_max",
)

STRATEGY_ASSET_TYPES = frozenset({"stock", "etf", "index"})

# Result-row keys set only on a row whose weighted score is missing.
MISSING_SCORING_FIELDS = "missing_scoring_fields"
MISSING_SCORING_CHILDREN = "missing_scoring_children"

# 叠加策略硬上限：子策略数量。控制信号计算成本与字段并集膨胀，避免 OOM。
MAX_COMPOSITE_CHILDREN = 8

USER_STRATEGY_SOURCES = ("custom", "ai", "composite")
USER_STRATEGIES_DISABLED_ERROR = (
    "user Python strategies are disabled, so this file was not run; "
    "set ALLOW_USER_PYTHON_STRATEGIES=true and restart the backend to load it"
)


def user_strategy_dirs(data_dir: Path) -> list[Path]:
    return [data_dir / "strategies" / source for source in USER_STRATEGY_SOURCES]


def _normalize_param_defs(params: Any) -> list[dict]:
    """把 META["params"] 归一化为标准 list[dict] (每项含 id/label/type/default).

    支持的输入格式:
    - list[dict] (标准): 保持, 补齐缺失的 id/label/type/default 字段
    - dict ({"lookback": 20} 或 {"lookback": {"default": 20, "type": "int"}}):
      按 key 作参数 id 转换
    - list[str] (["lookback", "threshold"]): 每项作 id, default=None
    - 其他类型 / 不可识别项: 丢弃并 warning 记录; 整体异常则返回空 list (降级而非崩溃)

    保证下游 {p["id"]: p["default"] for p in params} 永远不会因格式问题抛 TypeError.
    """
    if params is None:
        return []

    # dict 格式: {"lookback": 20} 或 {"lookback": {"default": 20, "type": "int"}}
    if isinstance(params, dict):
        items: list[dict] = []
        for key, val in params.items():
            if not isinstance(key, str) or not key:
                continue
            if isinstance(val, dict):
                item = {"id": key, **val}
            else:
                item = {"id": key, "default": val}
            items.append(item)
        return [_normalize_param_item(item) for item in items]

    # 期望是 list/tuple, 其他类型直接降级
    if not isinstance(params, (list, tuple)):
        logger.warning("strategy params 非标准格式 (%s), 已降级为空 list", type(params).__name__)
        return []

    result: list[dict] = []
    for i, p in enumerate(params):
        if isinstance(p, str):
            result.append({"id": p, "default": None})
        elif isinstance(p, dict):
            item = _normalize_param_item(p)
            if item:  # 缺 id 等异常项 _normalize_param_item 返回空 dict, 丢弃
                result.append(item)
        else:
            logger.warning("strategy params[%d] 不可识别 (%s), 已丢弃", i, type(p).__name__)
    return result


def _normalize_param_item(item: dict) -> dict:
    """补齐单个参数定义的默认字段, 保证 id/label/type/default 都存在."""
    norm = dict(item)
    if "id" not in norm or not norm["id"]:
        logger.warning("strategy param 定义缺少 id, 已丢弃: %s", item)
        return {}
    norm.setdefault("label", str(norm["id"]))
    norm.setdefault("type", "float")
    norm.setdefault("default", None)
    return norm


def _parse_composite_children(raw: Any) -> CompositeSpec:
    """解析 META["children"] 为 CompositeSpec。

    每项形如 {"strategy_id": "xxx", "weight": 0.4}。仅做结构和权重校验:
    - 非空 list, 每项含合法 strategy_id 与非负 weight
    - 数量 <= MAX_COMPOSITE_CHILDREN (超出在加载期拒绝, 避免信号计算成本爆炸)
    子策略的存在性/非嵌套/asset_types 一致性由 _load_all 两阶段校验保证。
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("composite strategy META['children'] must be a non-empty list")
    if len(raw) > MAX_COMPOSITE_CHILDREN:
        raise ValueError(
            f"composite strategy children count {len(raw)} exceeds limit {MAX_COMPOSITE_CHILDREN}"
        )
    children: list[CompositeChild] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"composite children[{i}] must be a dict")
        cid = item.get("strategy_id")
        if not isinstance(cid, str) or not cid:
            raise ValueError(f"composite children[{i}] missing non-empty 'strategy_id'")
        if cid in seen:
            raise ValueError(f"composite children[{i}] duplicate strategy_id {cid!r}")
        seen.add(cid)
        weight = item.get("weight", 1.0)
        try:
            weight = float(weight)
        except (TypeError, ValueError) as e:
            raise ValueError(f"composite children[{i}] weight must be a number") from e
        if weight < 0:
            raise ValueError(f"composite children[{i}] weight must be >= 0")
        children.append(CompositeChild(strategy_id=cid, weight=weight))
    return CompositeSpec(children=tuple(children))


@dataclass
class StrategyDataContext:
    """一次策略调用所需的标准数据上下文。"""

    asset_type: str
    timeframe: str
    as_of: date
    current: pl.DataFrame | None = None
    history: pl.DataFrame | None = None
    market: Any | None = None
    cache_key: str | None = None
    # Loader outcome per input ("current"/"history"); diagnostics only, strategies never read it.
    input_status: dict[str, Any] | None = None
    # Taiwan symbol -> asset type; restricts picks of Taiwan symbols to the strategy's asset_types.
    symbol_asset_types: Mapping[str, str] | None = None
    # Symbol -> values published for as_of; they replace the same keys on that symbol's result rows.
    published_row_fields: Mapping[str, Mapping[str, Any]] | None = None

    def __post_init__(self) -> None:
        # Index bars are never adjusted, so their close is the raw price absolute bounds compare.
        if self.asset_type == "index":
            self.current = index_with_raw_close(self.current)
            self.history = index_with_raw_close(self.history)


def index_with_raw_close(frame: pl.DataFrame | None) -> pl.DataFrame | None:
    """Index bars with raw_close set to close; only for index data, which carries no adjustment."""
    if frame is None or "raw_close" in frame.columns or "close" not in frame.columns:
        return frame
    return frame.with_columns(pl.col("close").alias("raw_close"))


@dataclass(frozen=True)
class CompositeChild:
    """叠加策略的一个子策略引用。"""

    strategy_id: str
    weight: float


@dataclass(frozen=True)
class CompositeSpec:
    """叠加策略的子策略声明（无业务代码，仅引用与权重）。

    引用合法性与一致性由 _load_all 两阶段校验保证：子策略必须存在、
    非嵌套、asset_types 一致、数量 ≤ MAX_COMPOSITE_CHILDREN。
    """

    children: tuple[CompositeChild, ...]


@dataclass
class StrategyDef:
    """加载后的策略定义（只读数据 + filter 函数引用）"""
    meta: dict
    basic_filter: dict
    entry_signals: list[str]
    exit_signals: list[str]
    stop_loss: float | None
    trailing_stop: float | None
    trailing_take_profit_activate: float | None
    trailing_take_profit_drawdown: float | None
    max_hold_days: int | None
    filter_fn: Callable[[pl.DataFrame, dict], pl.Expr] | None
    filter_history_fn: Callable[[pl.DataFrame, dict], pl.DataFrame] | None
    lookback_days: int
    source: str  # "builtin" | "custom" | "ai" | "composite"
    required_features: frozenset[str] = field(default_factory=frozenset)
    file_path: Path | None = None
    execution_backend: str = "polars_expr"
    matrix_strategy: Any | None = None
    composite: CompositeSpec | None = None  # 仅 backend=="composite" 时非空


@dataclass
class StrategyResult:
    """策略执行结果"""
    as_of: date
    strategy_id: str
    rows: list[dict] = field(default_factory=list)
    total: int = 0
    elapsed_ms: float = 0.0
    scores: dict[str, float | None] = field(default_factory=dict)
    entry_signal_hits: list[dict] = field(default_factory=list)
    exit_signal_hits: list[dict] = field(default_factory=list)


@dataclass
class _RealtimeMatrixEntry:
    fingerprint: tuple[Any, ...]
    buffer: Any
    enriched_generation: str | None = None


def _published_enriched_generation(asset_type: str) -> str | None:
    """The ready enriched generation, or None while it is being published or was never marked."""
    try:
        return get_enriched_generation(settings.data_dir, asset_type, initialize=False)
    except EnrichedGenerationUnavailableError:
        return None


class _AssetScope(Mapping[str, str]):
    """The asset types of ``types`` that are in ``allowed``; other symbols are absent.

    Lookups go through to ``types``, so a lazily resolved mapping stays lazy.
    """

    def __init__(self, types: Mapping[str, str], allowed: Iterable[str]) -> None:
        self._types = types
        self._allowed = frozenset(allowed)

    def __getitem__(self, symbol: str) -> str:
        asset_type = self._types[symbol]
        if asset_type not in self._allowed:
            raise KeyError(symbol)
        return asset_type

    def __iter__(self) -> Iterator[str]:
        return (symbol for symbol, asset_type in self._types.items() if asset_type in self._allowed)

    def __len__(self) -> int:
        return sum(1 for _ in self)


class StrategyEngine:
    """策略引擎 — 策略加载 + 执行 + 评分"""

    _module_load_lock = threading.RLock()

    def __init__(
        self,
        strategy_dirs: list[Path] | None = None,
        *,
        disabled_dirs: Iterable[Path] = (),
        override_loader: Callable[[str], dict] | None = None,
    ):
        """``disabled_dirs`` are strategy dirs whose files are listed in load_errors, never run."""
        self._strategies: dict[str, StrategyDef] = {}
        self._disabled_dirs = frozenset(Path(d).resolve() for d in disabled_dirs)
        self._load_errors: list[dict] = []  # 加载失败的策略 [{file, error}]
        # Serializes reloads so a slower earlier scan never replaces a newer registry.
        self._reload_lock = threading.Lock()
        self._strategy_dirs = strategy_dirs or []
        self._realtime_matrices: dict[str, _RealtimeMatrixEntry] = {}
        self._realtime_matrix_lock = threading.RLock()
        # 可选的 override 加载器: 叠加策略执行时用它查子策略的用户覆盖配置,
        # 保证 composite 内跑子策略与单独跑子策略使用同一口径(CONTRIBUTING §5.1)。
        # None 时(测试/无 data_dir) 子策略用默认参数, 不报错。
        self._override_loader = override_loader
        self._load_all()

    # ================================================================
    # 加载
    # ================================================================

    def _load_all(self) -> None:
        """Register every loadable strategy file; the others are listed in load_errors().

        Files sharing an id all drop out, except that a builtin keeps its id and the others are
        listed.
        """
        loaded: dict[str, list[tuple[Path, StrategyDef]]] = {}
        errors: list[dict] = []
        for d in self._strategy_dirs:
            if not d.exists():
                continue
            disabled = d.resolve() in self._disabled_dirs
            for f in sorted(d.glob("*.py")):
                if f.name.startswith("_"):
                    continue
                if disabled:
                    logger.warning("skip strategy %s: %s", f, USER_STRATEGIES_DISABLED_ERROR)
                    errors.append({"file": str(f), "error": USER_STRATEGIES_DISABLED_ERROR})
                    continue
                try:
                    s = self._load_file(f)
                    loaded.setdefault(str(s.meta["id"]), []).append((f, s))
                except Exception as e:
                    logger.warning("load strategy %s failed: %s", f.name, e)
                    errors.append({"file": str(f), "error": str(e)})

        candidates: dict[str, StrategyDef] = {}
        for strategy_id, files in loaded.items():
            builtins = [(f, s) for f, s in files if s.source == "builtin"]
            if len(files) == 1:
                candidates[strategy_id] = files[0][1]
            elif len(builtins) == 1:
                builtin_path, candidates[strategy_id] = builtins[0]
                message = (
                    f"strategy id {strategy_id!r} is already used by the builtin strategy "
                    f"{builtin_path.name}; give this file another id"
                )
                errors.extend(
                    {"file": str(f), "error": message} for f, s in files if s.source != "builtin"
                )
            else:
                message = f"duplicate strategy id {strategy_id!r}"
                errors.extend({"file": str(f), "error": message} for f, _ in files)

        # 第二阶段: 校验 composite 引用合法性。
        # _load_file 是 staticmethod, 加载单个文件时无法判断 child 是否存在;
        # 此处已拿到全部 candidates, 可做引用、嵌套、asset_types 与数量校验。
        # 孤儿 composite (引用不合法) 被移出 candidates 并记入 errors,
        # 但不触发整体 reload 失败 —— 不波及其他正常策略(插件隔离原则)。
        for sid in list(candidates):
            strategy = candidates[sid]
            if strategy.execution_backend != "composite" or strategy.composite is None:
                continue
            error = self._validate_composite_references(sid, strategy, candidates)
            if error is not None:
                logger.warning("load strategy %s failed: %s", sid, error)
                errors.append({
                    "file": str(strategy.file_path) if strategy.file_path else sid,
                    "error": error,
                })
                candidates.pop(sid, None)

        self._load_errors = errors
        self._strategies = candidates
        for strategy_id, strategy in candidates.items():
            logger.debug("loaded strategy: %s (%s)", strategy_id, strategy.source)

    def load_errors(self) -> list[dict]:
        """返回最近一次 _load_all 中加载失败的策略 [{file, error}]。"""
        return list(self._load_errors)

    def file_disabled(self, path: Path) -> bool:
        """Whether a strategy file at ``path`` would be listed in load_errors instead of run."""
        return path.parent.resolve() in self._disabled_dirs

    @staticmethod
    def _validate_composite_references(
        sid: str,
        strategy: StrategyDef,
        candidates: Mapping[str, StrategyDef],
        children: tuple[CompositeChild, ...] | None = None,
    ) -> str | None:
        """Validate a composite strategy's references; return an error description or None.

        Hard constraints:
        - every child is loaded (present in candidates)
        - no composite nests a composite (children must be leaves)
        - a child supports every asset_type / timeframe of the parent composite
        - child ranks higher scores first, as the merge does
        - at most MAX_COMPOSITE_CHILDREN children
        On any violation _load_all drops only this orphaned composite, sparing other strategies.
        ``children`` defaults to the META children.
        """
        assert strategy.composite is not None
        if children is None:
            children = strategy.composite.children
        if len(children) > MAX_COMPOSITE_CHILDREN:
            return (
                f"composite strategy {sid} children count {len(children)} "
                f"exceeds limit {MAX_COMPOSITE_CHILDREN}"
            )
        parent_assets = list(strategy.meta.get("asset_types", ["stock"]))
        parent_timeframes = list(strategy.meta.get("timeframes", ["1d"]))
        for child in children:
            child_def = candidates.get(child.strategy_id)
            if child_def is None:
                return f"composite strategy {sid} 引用的子策略 {child.strategy_id!r} 不存在"
            if child_def.execution_backend == "composite":
                return (
                    f"composite strategy {sid} 引用的子策略 {child.strategy_id!r} "
                    f"也是叠加策略; 首版禁止嵌套叠加"
                )
            child_assets = list(child_def.meta.get("asset_types", ["stock"]))
            if not set(parent_assets).issubset(set(child_assets)):
                return (
                    f"composite strategy {sid} 的 asset_types {parent_assets} "
                    f"未被子策略 {child.strategy_id!r} 完全支持(支持 {child_assets})"
                )
            child_timeframes = list(child_def.meta.get("timeframes", ["1d"]))
            if not set(parent_timeframes).issubset(set(child_timeframes)):
                return (
                    f"composite strategy {sid} 的 timeframes {parent_timeframes} "
                    f"未被子策略 {child.strategy_id!r} 完全支持(支持 {child_timeframes})"
                )
            if not bool(child_def.meta.get("descending", True)):
                return (
                    f"composite strategy {sid}: child {child.strategy_id!r} ranks lower scores "
                    "first (descending=False), but composites rank every child's scores "
                    "higher-first; give the child scoring_directions 'low' for those fields instead"
                )
        return None

    @staticmethod
    def _load_file(path: Path) -> StrategyDef:
        """从 Python 文件加载策略定义"""
        # Same lint as the save API so hand-edited files get the same hints; not a security boundary.
        dependency_paths = [
            candidate
            for candidate in path.parent.glob("_*.py")
            if candidate != path
        ]
        dependency_names = frozenset(candidate.stem for candidate in dependency_paths)
        try:
            code = path.read_text(encoding="utf-8")
            from app.strategy.ai_generator import AIStrategyGenerator
            AIStrategyGenerator._validate_safety(
                code,
                extra_allowed_import_modules=dependency_names,
            )
            for dependency_path in dependency_paths:
                AIStrategyGenerator._validate_safety(
                    dependency_path.read_text(encoding="utf-8"),
                    extra_allowed_import_modules=frozenset({
                        "collections.abc",
                        "types",
                        "typing",
                    }),
                    extra_allowed_calls=frozenset({"vars"}),
                )
        except ValueError:
            raise
        except Exception:  # noqa: BLE001
            # 文件读不到/语法错等: 不阻断, 让下方 exec_module 抛原样错误
            pass

        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"cannot load module from {path}")
        mod = importlib.util.module_from_spec(spec)
        with StrategyEngine._module_load_lock:
            previous_module = sys.modules.get(spec.name)
            sys.modules[spec.name] = mod
            inserted_path = str(path.parent)
            sys.path.insert(0, inserted_path)
            try:
                for dependency_name in dependency_names:
                    sys.modules.pop(dependency_name, None)
                spec.loader.exec_module(mod)
            except Exception:
                if previous_module is None:
                    sys.modules.pop(spec.name, None)
                else:
                    sys.modules[spec.name] = previous_module
                raise
            finally:
                try:
                    sys.path.remove(inserted_path)
                except ValueError:
                    pass

        meta = dict(getattr(mod, "META", {}) or {})
        meta.setdefault("id", path.stem)
        meta.setdefault("name", path.stem)
        meta.setdefault("description", "")
        meta.setdefault("tags", [])
        meta.setdefault("params", [])
        meta.setdefault("scoring", {})
        meta.setdefault("order_by", "score")
        meta.setdefault("descending", True)
        meta.setdefault("limit", 100)

        source = "custom"
        normalized_path = str(path).replace("\\", "/")
        if "/builtin/" in normalized_path:
            source = "builtin"
        elif "/ai/" in normalized_path:
            source = "ai"
        elif "/composite/" in normalized_path:
            source = "composite"

        if source == "builtin" and "asset_types" not in meta:
            raise ValueError("builtin strategy META must declare asset_types")
        meta.setdefault("asset_types", ["stock"])
        meta.setdefault("timeframes", ["1d"])
        for field_name in ("asset_types", "timeframes"):
            values = meta.get(field_name)
            if (
                not isinstance(values, (list, tuple))
                or not values
                or any(not isinstance(value, str) or not value for value in values)
            ):
                raise ValueError(f"META[{field_name!r}] must be a non-empty string list")
            meta[field_name] = list(dict.fromkeys(values))
        unknown_asset_types = sorted(set(meta["asset_types"]) - STRATEGY_ASSET_TYPES)
        if unknown_asset_types:
            raise ValueError(
                f"META['asset_types'] has unsupported values {unknown_asset_types}; "
                f"expected values from {sorted(STRATEGY_ASSET_TYPES)}"
            )
        if "scoring_directions" in meta:
            meta["scoring_directions"] = meta_scoring_directions(meta["scoring_directions"])

        # 归一化 params 为标准 list[dict]: custom/AI 策略的 META["params"] 可能是
        # dict / list[str] 等非标准格式 (LLM 偶发漂移 / 用户手改), 不归一化的话会在
        # _strategy_detail() 的 {p["id"]: p["default"] for p in params} 处抛 TypeError,
        # 导致整个 /api/strategies 列表 500. 降级为空 list 而非崩溃, 策略仍可见可用.
        meta["params"] = _normalize_param_defs(meta.get("params"))

        # 合并默认基础过滤
        bf = {**DEFAULT_BASIC_FILTER}
        strat_bf = getattr(mod, "BASIC_FILTER", None)
        if strat_bf:
            bf.update(strat_bf)
        # meta 里的 basic_filter 也合并（优先级最高）
        meta_bf = meta.get("basic_filter")
        if meta_bf:
            bf.update(meta_bf)

        filter_fn = getattr(mod, "filter", None)
        filter_history_fn = getattr(mod, "filter_history", None)
        execution_backend = str(
            getattr(
                mod,
                "EXECUTION_BACKEND",
                meta.get(
                    "execution_backend",
                    "python_history_legacy" if filter_history_fn else "polars_expr",
                ),
            )
        )
        valid_backends = {"polars_expr", "matrix_native", "python_history_legacy", "composite"}
        if execution_backend not in valid_backends:
            raise ValueError(
                f"unsupported execution backend {execution_backend!r}; "
                f"expected one of {sorted(valid_backends)}"
            )

        matrix_strategy = getattr(mod, "MATRIX_STRATEGY", None)
        composite_spec: CompositeSpec | None = None
        if execution_backend == "matrix_native":
            from app.backtest.matrix import MatrixStrategy

            if matrix_strategy is None:
                raise ValueError("matrix_native strategy must declare MATRIX_STRATEGY")
            if not isinstance(matrix_strategy, MatrixStrategy):
                raise TypeError("MATRIX_STRATEGY must implement MatrixStrategy")
            if filter_fn is not None or filter_history_fn is not None:
                raise ValueError("matrix_native strategy must not declare filter or filter_history")
        elif execution_backend == "polars_expr":
            if filter_fn is None or filter_history_fn is not None:
                raise ValueError("polars_expr strategy must declare only filter")
        elif execution_backend == "composite":
            # 叠加策略是声明式的: 不含业务代码, 仅通过 META["children"] 引用其他策略。
            # 引用合法性(子策略存在/非嵌套/asset_types 一致/数量上限)延后到
            # _load_all 两阶段校验 —— 因为此时注册表尚未加载完, 无法判断 child 是否存在。
            if (
                filter_fn is not None
                or filter_history_fn is not None
                or matrix_strategy is not None
            ):
                raise ValueError(
                    "composite strategy must not declare filter, filter_history or MATRIX_STRATEGY"
                )
            composite_spec = _parse_composite_children(meta.get("children"))
        elif filter_history_fn is None or filter_fn is not None:
            raise ValueError("python_history_legacy strategy must declare only filter_history")

        return StrategyDef(
            meta=meta,
            basic_filter=bf,
            entry_signals=getattr(mod, "ENTRY_SIGNALS", []),
            exit_signals=getattr(mod, "EXIT_SIGNALS", []),
            stop_loss=getattr(mod, "STOP_LOSS", None),
            trailing_stop=getattr(mod, "TRAILING_STOP", None),
            trailing_take_profit_activate=getattr(mod, "TRAILING_TAKE_PROFIT_ACTIVATE", None),
            trailing_take_profit_drawdown=getattr(mod, "TRAILING_TAKE_PROFIT_DRAWDOWN", None),
            max_hold_days=getattr(mod, "MAX_HOLD_DAYS", None),
            filter_fn=filter_fn,
            filter_history_fn=filter_history_fn,
            required_features=frozenset(meta.get("required_features", []) or [])
            | frozenset(getattr(mod, "REQUIRED_FEATURES", []) or []),
            lookback_days=int(getattr(mod, "LOOKBACK_DAYS", meta.get("lookback_days", 1)) or 1),
            source=source,
            file_path=path,
            execution_backend=execution_backend,
            matrix_strategy=matrix_strategy,
            composite=composite_spec,
        )

    def reload(self) -> list[dict]:
        """Replace the registry with every file that loads now, as at startup; returns its load errors.

        A file that fails is left out and listed, so it never blocks the others. Callers that wrote
        a file check its entry in the returned list, which a concurrent reload cannot replace.
        """
        with self._reload_lock:
            self._load_all()
            errors = list(self._load_errors)
        with self._realtime_matrix_lock:
            self._realtime_matrices.clear()
        return errors

    # ================================================================
    # 查询
    # ================================================================

    def list_strategies(self, *, include_research: bool = False) -> list[dict]:
        """Return public strategy metadata unless research templates are requested."""
        result = []
        for s in self._strategies.values():
            if s.meta.get("research_only") and not include_research:
                continue
            result.append({
                **s.meta,
                "source": s.source,
                "execution_backend": s.execution_backend,
            })
        return result

    def strategy_definitions(self) -> tuple[StrategyDef, ...]:
        """Return the immutable registry snapshot for framework dependency planning."""
        return tuple(self._strategies.values())

    def get(self, strategy_id: str) -> StrategyDef:
        s = self._strategies.get(strategy_id)
        if not s:
            raise ValueError(f"unknown strategy: {strategy_id}")
        return s

    def has(self, strategy_id: str) -> bool:
        return strategy_id in self._strategies

    def unregister(self, strategy_id: str) -> bool:
        """从运行时注册表移除单个策略, 不重新加载其他策略文件。"""
        with self._reload_lock:
            if strategy_id not in self._strategies:
                return False
            strategies = dict(self._strategies)
            strategies.pop(strategy_id)
            self._strategies = strategies
        with self._realtime_matrix_lock:
            self._realtime_matrices.clear()
        return True

    def find_dependents(self, strategy_id: str) -> list[str]:
        """返回引用了 strategy_id 作为子策略的所有 composite 策略 id。

        供删除校验使用: 删除被引用的子策略会令 composite 加载失败,
        删除前应阻止(fail-closed)或提示用户先解除引用。策略数量通常很小,
        线性遍历注册表即可, 无需维护反向索引。
        """
        dependents: list[str] = []
        for sid, strategy in self._strategies.items():
            if strategy.execution_backend != "composite" or strategy.composite is None:
                continue
            if any(c.strategy_id == strategy_id for c in strategy.composite.children):
                dependents.append(sid)
        return dependents

    def composite_children(
        self, strategy: StrategyDef, overrides: dict | None
    ) -> tuple[CompositeChild, ...]:
        """The children a composite runs: saved override children first, else META.

        Both are held to the load-time reference rules; a violation raises ValueError.
        """
        assert strategy.composite is not None
        override_children = (overrides or {}).get("children")
        if isinstance(override_children, list) and override_children:
            children = _parse_composite_children(override_children).children
        else:
            children = strategy.composite.children
        error = self._validate_composite_references(
            str(strategy.meta.get("id")), strategy, self._strategies, children
        )
        if error is not None:
            raise ValueError(error)
        return children

    def _saved_overrides(self, strategy_id: str) -> dict:
        """The user's saved overrides of a composite child; {} without a loader or on a read error."""
        if self._override_loader is None:
            return {}
        try:
            loaded = self._override_loader(strategy_id)
        except Exception:  # noqa: BLE001
            return {}
        return dict(loaded) if isinstance(loaded, dict) else {}

    @staticmethod
    def validate_context(strategy: StrategyDef, context: StrategyDataContext) -> None:
        asset_types = strategy.meta.get("asset_types", ["stock"])
        if context.asset_type not in asset_types:
            raise ValueError(
                f"strategy {strategy.meta['id']} does not support asset_type "
                f"{context.asset_type!r}; supported={asset_types}"
            )
        timeframes = strategy.meta.get("timeframes", ["1d"])
        if context.timeframe not in timeframes:
            raise ValueError(
                f"strategy {strategy.meta['id']} does not support timeframe "
                f"{context.timeframe!r}; supported={timeframes}"
            )

    @staticmethod
    def _supported_symbols(
        strategy: StrategyDef,
        context: StrategyDataContext,
        symbols: Iterable[str],
    ) -> set[str] | None:
        """The given symbols the strategy may pick, or None when the context carries no asset types.

        Only Taiwan symbols are checked; one missing from context.symbol_asset_types has no known
        asset type and is never picked.
        """
        if context.symbol_asset_types is None:
            return None
        asset_types = set(strategy.meta.get("asset_types", ["stock"]))
        return {
            symbol
            for symbol in symbols
            if not str(symbol).endswith(TAIWAN_SUFFIXES)
            or context.symbol_asset_types.get(symbol) in asset_types
        }

    @classmethod
    def _keep_supported_symbols(
        cls,
        df: pl.DataFrame,
        strategy: StrategyDef,
        context: StrategyDataContext,
    ) -> pl.DataFrame:
        if context.symbol_asset_types is None or df.is_empty():
            return df
        candidates = df.get_column("symbol").cast(pl.Utf8).unique().to_list()
        supported = cls._supported_symbols(strategy, context, candidates) or set()
        return df.filter(pl.col("symbol").cast(pl.Utf8).is_in(sorted(supported)))

    @staticmethod
    def resolve_params(
        strategy: StrategyDef,
        params: dict | None = None,
        overrides: dict | None = None,
    ) -> dict:
        """Resolve one parameter source of truth for every strategy consumer."""
        resolved = {
            item["id"]: item.get("default")
            for item in strategy.meta.get("params", [])
            if isinstance(item, dict) and item.get("id")
        }
        saved = (overrides or {}).get("params")
        if isinstance(saved, dict):
            resolved.update(saved)
        if params:
            resolved.update(params)
        return resolved

    @staticmethod
    def _result_limit(strategy: StrategyDef, overrides: dict | None) -> int | None:
        if overrides and "display_limit" in overrides:
            value = overrides.get("display_limit")
            if value in (None, 0):
                return None
            return max(0, int(value))
        value = strategy.meta.get("limit", 100)
        if value in (None, 0):
            return None
        return max(0, int(value))

    def required_history_bars(
        self,
        strategy_ids: list[str],
        *,
        params_map: dict[str, dict] | None = None,
        overrides_map: dict[str, dict] | None = None,
    ) -> int:
        params_map = params_map or {}
        overrides_map = overrides_map or {}
        required = 1
        for strategy_id in strategy_ids:
            strategy = self.get(strategy_id)
            overrides = overrides_map.get(strategy_id) or {}
            scoring = effective_scoring(strategy.meta.get("scoring"), overrides)
            required = max(required, scoring_warmup_bars(scoring))
            if strategy.execution_backend == "matrix_native":
                params = self.resolve_params(
                    strategy,
                    params_map.get(strategy_id),
                    overrides,
                )
                required = max(
                    required,
                    int(strategy.matrix_strategy.required_warmup_bars(params)) + 1,
                    scoring_warmup_bars(_parameter_scoring(strategy, params)),
                )
            elif strategy.execution_backend == "composite":
                # composite 预热 = 各子策略预热的 max。
                # 子策略已通过加载期校验(非嵌套叶子), 这里展开一层即可。
                if strategy.composite is None:
                    continue
                try:
                    children = self.composite_children(strategy, overrides)
                except ValueError:
                    # The composite's own run reports invalid saved children; a batch still loads.
                    children = strategy.composite.children
                child_ids = [c.strategy_id for c in children]
                required = max(
                    required,
                    self.required_history_bars(
                        child_ids,
                        overrides_map={cid: self._saved_overrides(cid) for cid in child_ids},
                    ),
                )
            elif strategy.filter_history_fn:
                # lookback_days 优先取自解析后的参数(默认值/保存覆盖/本次调用),
                # 静态 LOOKBACK_DAYS 兜底。策略可能只把窗口声明为参数
                # (如 AI 生成策略), 此时 strategy.lookback_days 回退到 1,
                # 不解析参数会低估历史需求 → build_strategy_context 跳过加载 → 运行时报错。
                params = self.resolve_params(
                    strategy,
                    params_map.get(strategy_id),
                    overrides_map.get(strategy_id),
                )
                required = max(required, _history_lookback(strategy, params))
        return required

    def prepare_realtime_matrix(
        self,
        context: StrategyDataContext,
        strategy_ids: list[str],
        *,
        params_map: dict[str, dict] | None = None,
        overrides_map: dict[str, dict] | None = None,
    ):
        """Build once, then update only the latest live bar for matrix strategies."""
        from app.backtest.matrix import RealtimeMarketDataMatrix

        current = context.current
        if current is None:
            raise ValueError("realtime matrix context requires current data")
        if current.is_empty() or not strategy_ids:
            return None
        params_map = params_map or {}
        overrides_map = overrides_map or {}
        field_columns: set[str] = set()
        max_warmup = 1
        matrix_ids: list[str] = []
        for strategy_id in strategy_ids:
            strategy = self.get(strategy_id)
            self.validate_context(strategy, context)
            if strategy.execution_backend != "matrix_native":
                continue
            params = self.resolve_params(
                strategy,
                params_map.get(strategy_id),
                overrides_map.get(strategy_id),
            )
            matrix_ids.append(strategy_id)
            max_warmup = max(
                max_warmup,
                int(strategy.matrix_strategy.required_warmup_bars(params)) + 1,
                scoring_warmup_bars(
                    effective_scoring(
                        strategy.meta.get("scoring"),
                        overrides_map.get(strategy_id),
                    )
                ),
                scoring_warmup_bars(_parameter_scoring(strategy, params)),
            )
            field_columns.update(
                self._matrix_field_columns(
                    strategy,
                    overrides_map.get(strategy_id),
                    params,
                )
            )
        if not matrix_ids:
            return None
        # Not in _matrix_field_columns: pick backfill checks each of those on flagless history.
        if "amount" in field_columns:
            field_columns.add(AMOUNT_ESTIMATED)

        timestamp_col = "datetime" if "datetime" in current.columns else "date"
        if timestamp_col not in current.columns:
            raise ValueError("realtime matrix current data requires date or datetime")
        latest_value = current[timestamp_col].max()
        as_of = latest_value.date() if hasattr(latest_value, "date") else latest_value
        if not isinstance(as_of, date):
            raise ValueError("realtime matrix timestamp cannot be converted to date")
        symbols = tuple(current["symbol"].cast(pl.Utf8).unique().sort().to_list())
        # A new session or enriched publication replaces the history rows, so the buffer is rebuilt.
        fingerprint = (
            tuple(sorted(field_columns)),
            max_warmup,
            symbols,
            as_of,
        )
        generation = _published_enriched_generation(context.asset_type)

        with self._realtime_matrix_lock:
            cache_key = context.cache_key or f"{context.asset_type}:{context.timeframe}"
            entry = self._realtime_matrices.get(cache_key)
            if entry is not None and generation is None:
                # Keep serving the consistent prior history while a publication is in progress.
                generation = entry.enriched_generation
            if (
                entry is not None
                and entry.fingerprint == fingerprint
                and entry.enriched_generation == generation
            ):
                try:
                    entry.buffer.update(current)
                    return entry.buffer.snapshot()
                except ValueError as exc:
                    logger.info("realtime matrix %s invalidated: %s", cache_key, exc)

            history = context.history
            if history is None:
                raise ValueError("matrix strategy realtime context requires history data")
            if history is None or history.is_empty():
                raise ValueError("matrix strategy realtime history is empty")
            if timestamp_col in history.columns:
                history = history.filter(pl.col(timestamp_col) != latest_value)
            elif "date" in history.columns:
                history = history.filter(pl.col("date") != as_of)
            panel = pl.concat([history, current], how="diagonal_relaxed")
            previous_builds = entry.buffer.build_count if entry is not None else 0
            buffer = RealtimeMarketDataMatrix(
                panel,
                field_columns=field_columns,
                build_count=previous_builds + 1,
            )
            self._realtime_matrices[cache_key] = _RealtimeMatrixEntry(
                fingerprint=fingerprint,
                buffer=buffer,
                enriched_generation=generation,
            )
            return buffer.snapshot()

    def realtime_matrix_stats(self, cache_key: str) -> dict[str, int]:
        with self._realtime_matrix_lock:
            entry = self._realtime_matrices.get(cache_key)
            if entry is None:
                return {"generation": 0, "build_count": 0, "update_count": 0}
            return {
                "generation": int(entry.buffer.generation),
                "build_count": int(entry.buffer.build_count),
                "update_count": int(entry.buffer.update_count),
            }

    # ================================================================
    # 执行
    # ================================================================

    def run(
        self,
        strategy_id: str,
        context: StrategyDataContext,
        pool: list[str] | None = None,
        params: dict | None = None,
        overrides: dict | None = None,
    ) -> StrategyResult:
        """执行策略: 基础过滤 → 策略过滤 → 评分排序

        Args:
            strategy_id:        策略 ID
            context:            调用级行情、资产和周期上下文
            pool:               限定股票池
            params:             本次执行显式传入的策略参数
            overrides:          用户覆盖配置 (params/basic_filter/scoring/stop_loss 等)
        """
        t0 = time.perf_counter()

        s = self.get(strategy_id)
        self.validate_context(s, context)
        as_of = context.as_of
        overrides = overrides or {}
        params = self.resolve_params(s, params, overrides)
        entry_signals = self._effective_signals(overrides, "entry_signals", s.entry_signals)
        exit_signals = self._effective_signals(overrides, "exit_signals", s.exit_signals)

        if s.execution_backend == "matrix_native":
            return self._run_matrix_strategy(
                strategy_id,
                s,
                as_of,
                pool=pool,
                params=params,
                overrides=overrides,
                context=context,
                started_at=t0,
            )

        if s.execution_backend == "composite":
            return self._run_composite_strategy(
                strategy_id,
                s,
                context,
                pool=pool,
                params=params,
                overrides=overrides,
                started_at=t0,
            )

        scoring = effective_scoring(s.meta.get("scoring"), overrides)
        scoring_directions = effective_scoring_directions(
            overrides, defaults=s.meta.get("scoring_directions")
        )
        current, history = self._materialize_scoring_frames(
            context.current,
            context.history,
            scoring,
        )

        signal_df = current if current is not None else history
        if signal_df is None:
            signal_df = pl.DataFrame()
        if not signal_df.is_empty() and "date" in signal_df.columns:
            signal_df = signal_df.filter(pl.col("date") == as_of)
        if pool and not signal_df.is_empty():
            signal_df = signal_df.filter(pl.col("symbol").is_in(pool))
        exit_signal_hits = self._collect_signal_hits(signal_df, exit_signals)

        # 普通策略只读目标日期；历史策略读取调用方注入的历史窗口。
        if s.filter_history_fn:
            if history is None:
                raise ValueError(f"strategy {strategy_id} requires history data")
            df = history
            if df.is_empty():
                return StrategyResult(
                    as_of=as_of,
                    strategy_id=strategy_id,
                    exit_signal_hits=exit_signal_hits,
                )
            # 自定义信号前置校验: REQUIRED_FEATURES 引用的 csg_ 列未注入时,
            # 给出明确指引, 而不是让策略代码抛 polars 缺列错 (500)。
            # 盘中单日路径不在此校验 (该路径对带偏移信号本就优雅降级)。
            missing_csg = [
                name for name in s.required_features
                if name.startswith("csg_") and name not in df.columns
            ]
            if missing_csg:
                raise ValueError(
                    "策略引用了未定义的自定义信号: "
                    + ", ".join(sorted(missing_csg))
                    + " — 请先在「自定义信号」管理中创建对应信号后再运行"
                )
            # Callers load different history lengths (alone, batch, monitor); the strategy sees its own.
            df = _lookback_window(df, as_of, _history_lookback(s, params))
            df = s.filter_history_fn(df, params)
            if "date" in df.columns:
                df = df.filter(pl.col("date") == as_of)
        else:
            if current is None:
                raise ValueError(f"strategy {strategy_id} requires current data")
            df = current

        if df.is_empty():
            return StrategyResult(
                as_of=as_of,
                strategy_id=strategy_id,
                exit_signal_hits=exit_signal_hits,
            )

        # 基础过滤: 策略默认 basic_filter 兜底, 用户 override 优先覆盖。
        # 这样策略文件里写的 exclude_st/price_min 等默认值即使前端没保存也能生效。
        bf = dict(s.basic_filter) if s.basic_filter else {}
        if overrides and overrides.get("basic_filter"):
            bf.update(overrides["basic_filter"])

        # Stage 1: 基础过滤（enabled 默认开启; 显式 enabled=false 才跳过）
        if bf and bf.get("enabled", True):
            bf = without_unavailable_share_bounds(bf, lambda name: frame_has_values(df, name))
            df = self._apply_basic_filter(df, bf)

        # Pool 过滤
        if pool:
            df = df.filter(pl.col("symbol").is_in(pool))
        df = self._keep_supported_symbols(df, s, context)

        # Stage 2: 策略过滤
        if s.filter_fn:
            expr = s.filter_fn(df, params)
            df = df.filter(expr)

        # Stage 3: 评分
        df = self._apply_scoring(df, scoring, scoring_directions)
        entry_signal_hits = self._collect_signal_hits(df, entry_signals)
        if not entry_signals and (s.filter_history_fn or s.filter_fn):
            entry_signal_hits = [
                {"symbol": str(symbol), "signals": []}
                for symbol in df["symbol"].cast(pl.Utf8).unique().to_list()
            ]

        # 排序 + 限制
        limit = self._result_limit(s, overrides)
        order_desc = bool(s.meta.get("descending", True))
        if "score" in df.columns:
            df = self._sort_ranked(df, "score", order_desc)
        elif s.meta.get("order_by") and s.meta["order_by"] != "score":
            ob = s.meta["order_by"]
            if ob in df.columns:
                df = self._sort_ranked(df, ob, order_desc)
        if limit is not None:
            df = df.head(limit)

        # 输出
        rows = _with_published_fields(_sanitize(df.to_dicts()), context.published_row_fields)
        if "score" in df.columns:
            # Scoring columns are materialized above, and _sanitize turned NaN into None.
            fields = _weighted_scoring_fields(scoring)
            _flag_missing_scoring_fields(
                rows, fields, lambda row: [name for name in fields if row.get(name) is None]
            )
        elapsed = (time.perf_counter() - t0) * 1000

        scores: dict[str, float | None] = {}
        if "score" in df.columns:
            for r in rows:
                scores[r["symbol"]] = _score_or_none(r.get("score"))

        return StrategyResult(
            as_of=as_of,
            strategy_id=strategy_id,
            rows=rows,
            total=len(rows),
            elapsed_ms=elapsed,
            scores=scores,
            entry_signal_hits=entry_signal_hits,
            exit_signal_hits=exit_signal_hits,
        )

    @staticmethod
    def _effective_signals(overrides: dict, key: str, default: list[str]) -> list[str]:
        value = overrides.get(key)
        if isinstance(value, list):
            return [str(signal) for signal in value if signal]
        return list(default or [])

    @staticmethod
    def _collect_signal_hits(df: pl.DataFrame, signals: list[str]) -> list[dict]:
        if df.is_empty() or not signals or "symbol" not in df.columns:
            return []
        resolved = [
            signal if signal.startswith(("signal_", "csg_")) else f"signal_{signal}"
            for signal in signals
        ]
        available = [
            (signal, column)
            for signal, column in zip(signals, resolved, strict=True)
            if column in df.columns
        ]
        if not available:
            return []
        hit_df = df.filter(pl.any_horizontal(pl.col(column).fill_null(False) for _, column in available))
        return [
            {
                "symbol": str(row["symbol"]),
                "signals": [signal for signal, column in available if row.get(column)],
            }
            for row in hit_df.iter_rows(named=True)
        ]

    def run_all(
        self,
        context: StrategyDataContext,
        params_map: dict | None = None,
        overrides_map: dict | None = None,
        *,
        strategy_ids: list[str] | None = None,
        skip_failed: bool = False,
        failures: dict[str, str] | None = None,
        pool: list[str] | None = None,
    ) -> dict[str, StrategyResult]:
        """Run strategies in a batch whose current data, history and matrices share one context.

        skip_failed: log and drop a strategy that raises instead of failing the
        batch, and record its message in ``failures`` when given. Opt-in because
        callers such as the page cache are better off holding the strategies that
        did run than nothing at all.
        pool: the symbols every strategy may pick from, as in run().
        """
        if context.current is None:
            raise ValueError("strategy run_all context requires current data")
        df = context.current
        params_map = params_map or {}
        overrides_map = overrides_map or {}
        selected_ids = list(self._strategies) if strategy_ids is None else strategy_ids
        selected = [(sid, self.get(sid)) for sid in selected_ids]
        for _, strategy in selected:
            self.validate_context(strategy, context)

        history_strats = [
            (sid, strategy)
            for sid, strategy in selected
            if strategy.filter_history_fn or strategy.execution_backend == "matrix_native"
        ]
        shared_history = context.history
        if history_strats and shared_history is None:
            raise ValueError("selected strategies require history data")

        shared_matrix = context.market
        matrix_strats = [
            (sid, strategy)
            for sid, strategy in selected
            if strategy.execution_backend == "matrix_native"
        ]
        if (
            shared_matrix is None
            and matrix_strats
            and shared_history is not None
            and not shared_history.is_empty()
        ):
            from app.backtest.matrix import build_market_data_matrix

            field_columns: set[str] = set()
            for sid, strategy in matrix_strats:
                field_columns.update(
                    self._matrix_field_columns(
                        strategy,
                        overrides_map.get(sid),
                        params_map.get(sid),
                    )
                )
            shared_matrix = build_market_data_matrix(
                shared_history,
                field_columns=field_columns,
            )

        results: dict[str, StrategyResult] = {}

        for sid, _ in selected:
            try:
                results[sid] = self.run(
                    sid,
                    replace(
                        context,
                        current=df,
                        history=shared_history,
                        market=shared_matrix,
                    ),
                    pool=pool,
                    params=params_map.get(sid),
                    overrides=overrides_map.get(sid),
                )
            except Exception as exc:
                if not skip_failed:
                    raise
                logger.exception("run_all: strategy %s failed, skipped", sid)
                if failures is not None:
                    failures[sid] = str(exc) or type(exc).__name__

        return results

    @staticmethod
    def _matrix_field_columns(
        strategy: StrategyDef,
        overrides: dict | None = None,
        params: dict | None = None,
    ) -> set[str]:
        fields = set(strategy.matrix_strategy.required_fields())
        # 参数评分字段 (如挖掘策略的因子组合) 需展开为实际数据依赖,
        # 与 backtest._resolve_matrix_native 保持同一语义, 否则虚拟因子
        # (limit_up_count_* -> consecutive_limit_ups) 在矩阵里缺字段。
        fields.update(scoring_dependencies(_parameter_scoring(strategy, params)))
        basic_filter = dict(strategy.basic_filter or {})
        if (overrides or {}).get("basic_filter"):
            basic_filter.update(overrides["basic_filter"])
        if not basic_filter.get("enabled", True):
            basic_filter = {}
        for prefix, field_name in (
            ("market_cap", "total_shares"),
            ("float_cap", "float_shares"),
            ("amount", "amount"),
            ("turnover", "turnover_rate"),
        ):
            if (
                basic_filter.get(f"{prefix}_min") is not None
                or basic_filter.get(f"{prefix}_max") is not None
            ):
                fields.add(field_name)
        if any(basic_filter.get(key) is not None for key in RAW_PRICE_BOUND_KEYS):
            fields.add("raw_close")
        scoring = effective_scoring(strategy.meta.get("scoring"), overrides)
        fields.update(scoring_dependencies(scoring))
        order_by = strategy.meta.get("order_by")
        if order_by and order_by != "score":
            fields.add(str(order_by))
        return fields

    def _run_matrix_strategy(
        self,
        strategy_id: str,
        strategy: StrategyDef,
        as_of: date,
        *,
        pool: list[str] | None,
        params: dict,
        overrides: dict,
        context: StrategyDataContext,
        started_at: float,
    ) -> StrategyResult:
        from app.backtest.matrix import (
            MatrixPipelineConfig,
            MatrixStrategyPipeline,
            build_market_data_matrix,
        )

        source_panel = context.history
        market = context.market
        if market is None:
            if source_panel is None:
                raise ValueError(f"matrix strategy {strategy_id} requires history data")
            if source_panel is None or source_panel.is_empty():
                return StrategyResult(as_of=as_of, strategy_id=strategy_id)
            market = build_market_data_matrix(
                source_panel,
                field_columns=self._matrix_field_columns(strategy, overrides, params),
            )
        # A run alone, run_all and the realtime monitor load different history lengths.
        market = self._own_history_window(strategy_id, market, params, overrides, as_of)

        if source_panel is None or source_panel.is_empty():
            source_panel = context.current
        if source_panel is None or source_panel.is_empty():
            return StrategyResult(as_of=as_of, strategy_id=strategy_id)

        basic_filter = dict(strategy.basic_filter or {})
        if overrides.get("basic_filter"):
            basic_filter.update(overrides["basic_filter"])
        basic_filter = without_unavailable_share_bounds(
            basic_filter,
            lambda name: name in market.fields and bool(np.isfinite(market.fields[name]).any()),
        )
        scoring = effective_scoring(strategy.meta.get("scoring"), overrides)
        asset_mask = None
        if pool:
            pool_set = set(pool)
            asset_mask = np.fromiter(
                (symbol in pool_set for symbol in market.symbols),
                dtype=bool,
                count=len(market.symbols),
            )
        entry_mask = asset_mask
        supported_symbols = self._supported_symbols(strategy, context, market.symbols)
        if supported_symbols is not None:
            supported_mask = np.fromiter(
                (symbol in supported_symbols for symbol in market.symbols),
                dtype=bool,
                count=len(market.symbols),
            )
            entry_mask = supported_mask if asset_mask is None else asset_mask & supported_mask

        target_ids = [
            time_id
            for time_id, label in enumerate(market.timestamp_labels)
            if label[:10] == str(as_of)
        ]
        if target_ids:
            refusal = _uncovered_entry_session(
                strategy.matrix_strategy, params, market, target_ids[-1], entry_mask
            )
            if refusal:
                raise ValueError(refusal)
        signals = MatrixStrategyPipeline().run(
            strategy.matrix_strategy,
            market,
            params,
            MatrixPipelineConfig(
                basic_filter=basic_filter,
                scoring=scoring,
                scoring_directions=effective_scoring_directions(
                    overrides, defaults=strategy.meta.get("scoring_directions")
                ),
                order_by=strategy.meta.get("order_by"),
                descending=bool(strategy.meta.get("descending", True)),
                asset_mask=entry_mask,
            ),
        )
        if not target_ids:
            return StrategyResult(as_of=as_of, strategy_id=strategy_id)
        target_time = target_ids[-1]
        entry_active = signals.entry[target_time]
        exit_active = signals.exit[target_time]
        if entry_mask is not None:
            entry_active = entry_active & entry_mask
        if asset_mask is not None:
            exit_active = exit_active & asset_mask
        entry_signal_hits = self._matrix_signal_hits(
            entry_active,
            signals.entry_signal_code[target_time],
            signals.entry_signal_ids,
            market.symbols,
        )
        exit_signal_hits = self._matrix_signal_hits(
            exit_active,
            signals.exit_signal_code[target_time],
            signals.exit_signal_ids,
            market.symbols,
        )
        selected_assets = np.flatnonzero(entry_active != 0)
        if selected_assets.size == 0:
            return StrategyResult(
                as_of=as_of,
                strategy_id=strategy_id,
                elapsed_ms=(time.perf_counter() - started_at) * 1000,
                entry_signal_hits=entry_signal_hits,
                exit_signal_hits=exit_signal_hits,
            )

        target_frame = self._matrix_target_frame(source_panel, as_of)
        row_by_symbol = {
            str(row["symbol"]): row
            for row in target_frame.iter_rows(named=True)
        }
        descending = ranks_higher_first(strategy.meta, scoring)
        ranked: list[tuple[tuple[bool, float, str], dict]] = []
        for asset_id in selected_assets:
            symbol = market.symbols[int(asset_id)]
            row = row_by_symbol.get(symbol)
            if row is None:
                continue
            score = float(signals.score[target_time, int(asset_id)])
            ranked.append((_rank_key(score, symbol, descending), {**row, "score": score}))
        ranked.sort(key=lambda item: item[0])
        limit = self._result_limit(strategy, overrides)
        selected_rows = ranked if limit is None else ranked[:limit]
        rows = _with_published_fields(
            _sanitize([row for _, row in selected_rows]), context.published_row_fields
        )
        if any(row.get("score") is None for row in rows):
            from app.backtest.matrix import matrix_feature

            fields = _weighted_scoring_fields(scoring)
            asset_ids = {symbol: asset_id for asset_id, symbol in enumerate(market.symbols)}
            values = {name: matrix_feature(market, name)[target_time] for name in fields}
            _flag_missing_scoring_fields(
                rows,
                fields,
                lambda row: [
                    name
                    for name in fields
                    if not np.isfinite(values[name][asset_ids[str(row["symbol"])]])
                ],
            )
        scores = {str(row["symbol"]): _score_or_none(row.get("score")) for row in rows}
        return StrategyResult(
            as_of=as_of,
            strategy_id=strategy_id,
            rows=rows,
            total=len(rows),
            elapsed_ms=(time.perf_counter() - started_at) * 1000,
            scores=scores,
            entry_signal_hits=entry_signal_hits,
            exit_signal_hits=exit_signal_hits,
        )

    def _own_history_window(
        self, strategy_id: str, market, params: dict, overrides: dict, as_of: date
    ):
        """The sessions of ``market`` through as_of that this strategy computes on.

        Its own required bars plus the history loader's warm-up margin, so neither a longer
        strategy sharing the market nor a longer loaded history changes its window.
        """
        from app.backtest.matrix import slice_market_data_matrix
        from app.repository import HISTORY_WARMUP_SESSIONS

        through = str(as_of)
        stop = sum(1 for label in market.timestamp_labels if label[:10] <= through)
        required = self.required_history_bars(
            [strategy_id],
            params_map={strategy_id: params},
            overrides_map={strategy_id: overrides},
        )
        start = max(0, stop - (1 + HISTORY_WARMUP_SESSIONS + required))
        if stop == 0 or (start == 0 and stop == market.shape[0]):
            return market
        return slice_market_data_matrix(market, start, stop)

    def _run_composite_strategy(
        self,
        strategy_id: str,
        strategy: StrategyDef,
        context: StrategyDataContext,
        *,
        pool: list[str] | None = None,
        params: dict | None = None,
        overrides: dict | None = None,
        started_at: float,
    ) -> StrategyResult:
        """Composite selection: run the children on a shared context, then merge their results.

        Children reuse run_all's shared current/history/market so none reloads the data.
        Children rank the candidates the composite backtest ranks: the pool within the composite's
        asset types and basic filter, and every candidate rather than the child's display limit.
        """
        from app.strategy import composite as composite_mod

        assert strategy.composite is not None
        overrides = overrides or {}
        children = self.composite_children(strategy, overrides)

        child_ids = [c.strategy_id for c in children]
        child_weights = [c.weight for c in children]
        merge_mode = str(params.get("merge_mode") or "union")
        min_confirm = int(params.get("min_confirm") or 0)

        # Each child override starts from that child's saved user config (params, scoring, signals),
        # then the composite's basic_filter replaces each child's, so all children screen one pool.
        composite_filter = dict(strategy.basic_filter or {})
        composite_filter.update(overrides.get("basic_filter") or {})
        overrides_map: dict[str, dict] = {}
        for cid in child_ids:
            child_override = self._saved_overrides(cid)
            # Keys only the child sets are cleared, since run() merges this onto the child's filter.
            child_override["basic_filter"] = {
                **dict.fromkeys(self.get(cid).basic_filter or {}),
                "enabled": True,
                **composite_filter,
            }
            child_override["display_limit"] = 0
            overrides_map[cid] = child_override

        child_context = context
        if context.symbol_asset_types is not None:
            child_context = replace(
                context,
                symbol_asset_types=_AssetScope(
                    context.symbol_asset_types, strategy.meta.get("asset_types", ["stock"])
                ),
            )
        # 共享 context 跑所有子策略。run_all 内部对 matrix_native 子策略会
        # 合并 field_columns 构建超集矩阵, 一次加载。
        child_failures: dict[str, str] = {}
        child_results = self.run_all(
            child_context,
            params_map={},
            overrides_map=overrides_map,
            strategy_ids=child_ids,
            skip_failed=True,
            failures=child_failures,
            pool=pool,
        )
        for cid in child_ids:
            if cid in child_failures:
                child_name = self.get(cid).meta.get("name") or cid
                raise ValueError(f"子策略「{child_name}」無法執行：{child_failures[cid]}")  # noqa: RUF001
        ordered_results = [child_results[cid] for cid in child_ids]

        merged = composite_mod.merge_results(
            ordered_results,
            child_weights,
            merge_mode,
            min_confirm,
            as_of=context.as_of,
            strategy_id=strategy_id,
        )

        # 构造展示行: 按 symbol 从各子结果取首个命中的行(含 name/价格等展示字段),
        # 融合 score。子策略间 schema 可能不同, 保留首个命中子的字段即可。
        row_by_symbol: dict[str, dict] = {}
        for res in ordered_results:
            for row in res.rows:
                sym = str(row.get("symbol"))
                if sym and sym not in row_by_symbol and sym in merged.scores:
                    row_by_symbol[sym] = row

        order_desc = bool(strategy.meta.get("descending", True))
        ranked_symbols = sorted(
            merged.scores,
            key=lambda s: _rank_key(merged.scores[s], s, order_desc),
        )
        limit = self._result_limit(strategy, overrides)
        if limit is not None:
            ranked_symbols = ranked_symbols[:limit]

        rows = _sanitize([
            {**row_by_symbol[sym], "score": merged.scores[sym]}
            for sym in ranked_symbols
            if sym in row_by_symbol
        ])
        _flag_unscored_composite_rows(
            rows,
            [
                (cid, self.get(cid).meta.get("name") or cid, result)
                for cid, result in zip(child_ids, ordered_results, strict=True)
            ],
        )
        scores = {str(row["symbol"]): _score_or_none(row.get("score")) for row in rows}

        return StrategyResult(
            as_of=context.as_of,
            strategy_id=strategy_id,
            rows=rows,
            total=len(rows),
            elapsed_ms=(time.perf_counter() - started_at) * 1000,
            scores=scores,
        )

    @staticmethod
    def _matrix_signal_hits(
        active: np.ndarray,
        codes: np.ndarray,
        signal_ids: tuple[str, ...],
        symbols: tuple[str, ...],
    ) -> list[dict]:
        hits = []
        for asset_id in np.flatnonzero(active != 0):
            code = int(codes[int(asset_id)])
            signals = [signal_ids[code]] if 0 <= code < len(signal_ids) else []
            hits.append({"symbol": symbols[int(asset_id)], "signals": signals})
        return hits

    @staticmethod
    def _matrix_target_frame(panel: pl.DataFrame, as_of: date) -> pl.DataFrame:
        if "datetime" in panel.columns:
            target = panel.filter(pl.col("datetime").cast(pl.Date) == as_of)
            if target.is_empty():
                return target
            latest = target["datetime"].max()
            target = target.filter(pl.col("datetime") == latest)
        elif "date" in panel.columns:
            target = panel.filter(pl.col("date") == as_of)
        else:
            return panel.head(0)
        return target.unique(subset=["symbol"], keep="last")

    # ================================================================
    # 内部: 基础过滤
    # ================================================================

    @staticmethod
    def _basic_filter_expr(df: pl.DataFrame, bf: dict) -> pl.Expr | None:
        """Build the basic-filter expression.

        A backtest reuses it as a buy-candidate mask instead of dropping quote rows.
        A bound excludes rows whose value is missing and raises when its column is absent.
        """
        exprs: list[pl.Expr] = []
        # Absolute bounds compare the price traded that day, so they need raw_close.
        raw_bounds = sorted(key for key in RAW_PRICE_BOUND_KEYS if bf.get(key) is not None)
        if raw_bounds and "raw_close" not in df.columns:
            raise ValueError(f"basic filter bounds {raw_bounds} need column raw_close, which the data lacks")
        raw_close = finite_or_null(pl.col("raw_close"))
        if bf.get("price_min") is not None:
            exprs.append((raw_close >= bf["price_min"]).fill_null(False))
        if bf.get("price_max") is not None:
            exprs.append((raw_close <= bf["price_max"]).fill_null(False))
        for prefix, field_name in _BOUNDED_FIELDS:
            minimum = bf.get(f"{prefix}_min")
            maximum = bf.get(f"{prefix}_max")
            if minimum is None and maximum is None:
                continue
            if field_name not in df.columns:
                raise ValueError(
                    f"basic filter {prefix} bound needs column {field_name}, which the data lacks"
                )
            value = finite_or_null(pl.col(field_name))
            if field_name in {"total_shares", "float_shares"}:
                value = raw_close * value
            if minimum is not None:
                exprs.append((value >= minimum).fill_null(False))
            if maximum is not None:
                exprs.append((value <= maximum).fill_null(False))
        if bf.get("exclude_st") and "name" in df.columns:
            exprs.append(~pl.col("name").str.contains("(?i)ST|\\*ST|退"))
        # 板块过滤
        boards = bf.get("boards")
        if boards and isinstance(boards, list) and len(boards) > 0:
            board_exprs: list[pl.Expr] = []
            for b in boards:
                if b in ("TWSE", "上市"):
                    board_exprs.append(pl.col("symbol").str.ends_with(".TWSE"))
                elif b in ("TPEX", "上櫃", "柜买"):
                    board_exprs.append(pl.col("symbol").str.ends_with(".TPEX"))
                elif b == "沪主板":
                    board_exprs.append(pl.col("symbol").str.starts_with("60"))
                elif b == "深主板":
                    board_exprs.append(
                        pl.col("symbol").str.starts_with("00")
                        | pl.col("symbol").str.starts_with("001")
                    )
                elif b == "创业板":
                    board_exprs.append(
                        pl.col("symbol").str.starts_with("300")
                        | pl.col("symbol").str.starts_with("301")
                    )
                elif b == "科创板":
                    board_exprs.append(pl.col("symbol").str.starts_with("688"))
                elif b == "北交所":
                    board_exprs.append(pl.col("symbol").str.contains(r"\.BJ$"))
            if board_exprs:
                exprs.append(pl.any_horizontal(board_exprs))
        min_sessions = exclude_new_days(bf)
        if min_sessions and {"symbol", "date"} <= set(df.columns):
            symbol = pl.col("symbol").cast(pl.Utf8)
            eligible = eligible_from(df.select(symbol.unique()).to_series().to_list(), min_sessions)
            if eligible:
                first = symbol.replace_strict(eligible, default=None, return_dtype=pl.Date)
                exprs.append(first.is_null() | (pl.col("date").cast(pl.Date) >= first))
        if exprs:
            return pl.all_horizontal(exprs)
        return None

    @staticmethod
    def _apply_basic_filter(df: pl.DataFrame, bf: dict) -> pl.DataFrame:
        """Stage 1: 基础参数过滤"""
        expr = StrategyEngine._basic_filter_expr(df, bf)
        if expr is not None:
            return df.filter(expr)
        return df

    # ================================================================
    # 内部: 评分
    # ================================================================

    @staticmethod
    def _apply_scoring(
        df: pl.DataFrame,
        weights: dict,
        directions: Mapping[str, str] | None = None,
    ) -> pl.DataFrame:
        """Generic scoring: min-max normalize each input, then weight and sum into a 0-100 score.

        A row missing any scoring input gets a null score.
        """
        active = {str(name): weight for name, weight in (weights or {}).items() if weight}
        total_weight = sum(active.values())
        if not active or total_weight <= 0:
            return df

        score_expr: pl.Expr | None = None
        for name, value, weight in resolve_scoring_values(df.columns, active):
            part = normalized_scoring_part(
                value,
                value.min(),
                value.max(),
                low_first=(directions or {}).get(name) == SCORING_DIRECTION_LOW,
            ) * (weight / total_weight)
            score_expr = part if score_expr is None else score_expr + part
        return df.with_columns((score_expr * 100).alias("score"))

    @staticmethod
    def _sort_ranked(df: pl.DataFrame, column: str, descending: bool) -> pl.DataFrame:
        """Sort by ``column`` with missing values last and ties broken by symbol."""
        key = pl.col(column)
        if df.schema[column].is_float():
            key = finite_or_null(key)
        by = [key]
        order = [descending]
        if "symbol" in df.columns and column != "symbol":
            by.append(pl.col("symbol"))
            order.append(False)
        return df.sort(by, descending=order, nulls_last=True)

    @staticmethod
    def _materialize_scoring_frames(
        current: pl.DataFrame | None,
        history: pl.DataFrame | None,
        scoring: Mapping[str, Any],
    ) -> tuple[pl.DataFrame | None, pl.DataFrame | None]:
        names = [str(name) for name, weight in scoring.items() if weight]
        if not names:
            return current, history
        if history is None or history.is_empty():
            return (
                materialize_scoring_columns(current, names) if current is not None else None,
                history,
            )

        scored_history = materialize_scoring_columns(history, names)
        if current is None or current.is_empty():
            return current, scored_history
        join_keys = [key for key in ("symbol", "date", "datetime") if key in current.columns and key in scored_history.columns]
        added = [name for name in names if name not in current.columns and name in scored_history.columns]
        if not join_keys or not added:
            return materialize_scoring_columns(current, names), scored_history
        values = scored_history.select([*join_keys, *added]).unique(subset=join_keys, keep="last")
        return current.join(values, on=join_keys, how="left"), scored_history


def without_unavailable_share_bounds(
    basic_filter: dict,
    has_values: Callable[[str], bool],
) -> dict:
    """Drop market-cap and float-cap bounds whose share field holds no value at all.

    Assets without share capital (ETFs, indices, Taiwan listings without published shares)
    cannot satisfy these bounds, so they are ignored rather than excluding every row.
    """
    unavailable = [
        prefix
        for prefix, field_name in _SHARE_CAP_BOUNDS
        if (
            basic_filter.get(f"{prefix}_min") is not None
            or basic_filter.get(f"{prefix}_max") is not None
        )
        and not has_values(field_name)
    ]
    if not unavailable:
        return basic_filter
    sanitized = dict(basic_filter)
    for prefix in unavailable:
        sanitized[f"{prefix}_min"] = None
        sanitized[f"{prefix}_max"] = None
    return sanitized


def frame_has_values(df: pl.DataFrame, name: str) -> bool:
    if name not in df.columns:
        return False
    values = df.get_column(name)
    if values.dtype.is_float():
        values = values.fill_nan(None)
    return values.null_count() < values.len()


def finite_or_null(value: pl.Expr) -> pl.Expr:
    """``value`` as Float64 with NaN and infinities replaced by null."""
    value = value.cast(pl.Float64)
    return pl.when(value.is_finite()).then(value)


def resolve_scoring_values(
    columns: Iterable[str],
    weights: Mapping[str, Any],
) -> list[tuple[str, pl.Expr, Any]]:
    """Value expression per weighted scoring field; raises when a field cannot be computed."""
    available = list(columns)
    resolved: list[tuple[str, pl.Expr, Any]] = []
    missing: list[str] = []
    for name, weight in weights.items():
        value = scoring_value_expr(available, str(name))
        if value is None:
            missing.append(str(name))
        else:
            resolved.append((str(name), finite_or_null(value), weight))
    if missing:
        raise ValueError(f"scoring fields unavailable in the data: {', '.join(sorted(missing))}")
    return resolved


def normalized_scoring_part(
    value: pl.Expr,
    minimum: pl.Expr,
    maximum: pl.Expr,
    *,
    low_first: bool,
) -> pl.Expr:
    """Min-max position of a scoring value in [0, 1], 0.5 for a constant input, null when missing."""
    spread = maximum - minimum
    normalized = (
        pl.when(value.is_not_null() & (spread > 0))
        .then((value - minimum) / spread)
        .when(value.is_not_null())
        .then(pl.lit(0.5))
    )
    return 1.0 - normalized if low_first else normalized


def ranks_higher_first(meta: Mapping[str, Any], scoring: Mapping[str, float]) -> bool:
    """Whether the pipeline score ranks higher first: the list order and the backtest buy order.

    A score taken from the order_by field already carries META descending as its sign.
    """
    from app.backtest.matrix import scores_by_order_field

    return bool(meta.get("descending", True)) or scores_by_order_field(
        scoring, meta.get("order_by")
    )


_NO_ELIGIBLE_SESSION = "{day} 所選範圍內沒有可計算{label}的股票"
_NO_ELIGIBLE_TURNOVER = "（ETF、TDR 沒有{label}，停牌股當日沒有收盤價），請改選含普通股的標的"  # noqa: RUF001
_UNCOVERED_SESSION = (
    "{day} 只有 {coverage} 的股票有{label}資料（至少需 {minimum}），"  # noqa: RUF001
    "無法依{label}選股，請改選有{label}資料的交易日"  # noqa: RUF001
)


def _uncovered_entry_session(
    matrix_strategy: Any, params: dict, market, row: int, assets: np.ndarray | None
) -> str | None:
    """The refusal when session ``row`` cannot screen required_coverage_fields(params), else None.

    The backtest's rule (backtest.strategy._entry_coverage_check) on one session, counting only
    ``assets``: the pool within the strategy's asset scope.
    """
    hook = getattr(matrix_strategy, "required_coverage_fields", None)
    if not callable(hook):
        return None
    from app.taiwan.share_capital_store import (
        MIN_SESSION_COVERAGE,
        coverage_eligible,
        eligible_coverage,
    )

    day = market.timestamp_labels[row][:10]
    close = market.close[row : row + 1]
    for name in sorted(str(name) for name in hook(params)):
        eligible = coverage_eligible(name, market.symbols)
        if assets is not None:
            eligible = eligible & assets
        label = field_label(name)
        if not (np.isfinite(close) & eligible).any():
            refusal = _NO_ELIGIBLE_SESSION.format(day=day, label=label)
            if name == "turnover_rate":
                refusal += _NO_ELIGIBLE_TURNOVER.format(label=label)
            return refusal
        values = market.fields.get(name)
        coverage = (
            0.0
            if values is None
            else float(eligible_coverage(close, values[row : row + 1], eligible)[0])
        )
        if coverage < MIN_SESSION_COVERAGE:
            return _UNCOVERED_SESSION.format(
                label=label,
                day=day,
                coverage=whole_percent(coverage),
                minimum=whole_percent(MIN_SESSION_COVERAGE),
            )
    return None


def _history_lookback(strategy: StrategyDef, params: dict) -> int:
    """LOOKBACK_DAYS, or the resolved lookback_days parameter when that is longer."""
    lookback = int(strategy.lookback_days)
    param_lookback = params.get("lookback_days")
    if isinstance(param_lookback, (int, float)) and param_lookback > 0:
        lookback = max(lookback, int(param_lookback))
    return lookback


def _lookback_window(history: pl.DataFrame, as_of: date, lookback: int) -> pl.DataFrame:
    """The as_of session and the ``lookback`` sessions before it: the filter_history contract.

    A datetime or ISO-string date column is read as dates; any other date column raises ValueError.
    """
    dtype = history.schema.get("date")
    try:
        if dtype == pl.Datetime:
            history = history.with_columns(pl.col("date").cast(pl.Date))
        elif dtype == pl.Utf8:
            history = history.with_columns(pl.col("date").str.to_date("%Y-%m-%d"))
        elif dtype != pl.Date:
            raise ValueError(f"strategy history needs a date column, got {dtype}")
    except pl.exceptions.PolarsError as exc:
        raise ValueError(f"strategy history date column is not ISO dates: {exc}") from exc
    through = history.filter(pl.col("date") <= as_of)
    sessions = through.get_column("date").unique().sort()
    if sessions.len() <= lookback + 1:
        return through
    return through.filter(pl.col("date") >= sessions[-(lookback + 1)])


def _rank_key(score: float | None, symbol: str, descending: bool) -> tuple[bool, float, str]:
    missing = score is None or not math.isfinite(score)
    value = 0.0 if missing else (-score if descending else score)
    return (missing, value, symbol)


def _score_or_none(value: Any) -> float | None:
    if value is None:
        return None
    score = float(value)
    return score if math.isfinite(score) else None


def _weighted_scoring_fields(scoring: Mapping[str, Any]) -> list[str]:
    """The fields a weighted score reads, in scoring order; none when the weights give no score."""
    active = {str(name): weight for name, weight in (scoring or {}).items() if weight}
    return list(active) if active and sum(active.values()) > 0 else []


def _flag_missing_scoring_fields(
    rows: list[dict], fields: list[str], missing: Callable[[dict], list[str]]
) -> None:
    """Set MISSING_SCORING_FIELDS to ``missing(row)`` on each row without a score."""
    if not fields:
        return
    for row in rows:
        if row.get("score") is None:
            row[MISSING_SCORING_FIELDS] = missing(row)


def _flag_unscored_composite_rows(
    rows: list[dict], children: list[tuple[str, str, StrategyResult]]
) -> None:
    """Name, on each merged row without a score, every child that could not score it.

    The merged rows start as copies of a child's row, so a child's own flags are dropped first.
    """
    by_symbol: list[dict[str, dict]] | None = None
    for row in rows:
        row.pop(MISSING_SCORING_FIELDS, None)
        row.pop(MISSING_SCORING_CHILDREN, None)
        if row.get("score") is not None:
            continue
        if by_symbol is None:
            by_symbol = [
                {str(child_row.get("symbol")): child_row for child_row in result.rows}
                for _, _, result in children
            ]
        symbol = str(row.get("symbol"))
        unscored = [
            {
                "strategy_id": child_id,
                "name": name,
                "fields": list(child_rows[symbol][MISSING_SCORING_FIELDS]),
            }
            for (child_id, name, _), child_rows in zip(children, by_symbol, strict=True)
            if MISSING_SCORING_FIELDS in child_rows.get(symbol, {})
        ]
        if unscored:
            row[MISSING_SCORING_CHILDREN] = unscored
            row[MISSING_SCORING_FIELDS] = list(
                dict.fromkeys(name for child in unscored for name in child["fields"])
            )


def _sanitize(rows: list[dict]) -> list[dict]:
    for r in rows:
        for k, v in list(r.items()):
            if isinstance(v, float) and (v != v or abs(v) == float("inf")):
                r[k] = None
    return rows


def _with_published_fields(
    rows: list[dict], published: Mapping[str, Mapping[str, Any]] | None
) -> list[dict]:
    if published:
        for row in rows:
            values = published.get(str(row.get("symbol")))
            if values:
                row.update(values)
    return rows


def _parameter_scoring(strategy: StrategyDef, params: dict | None) -> dict[str, float]:
    """Scoring fields a matrix strategy takes from its parameters, such as a mined factor set."""
    parameter_fields = getattr(strategy.matrix_strategy, "required_fields_for_params", None)
    if not callable(parameter_fields):
        return {}
    return {str(name): 1.0 for name in parameter_fields(params or {})}
