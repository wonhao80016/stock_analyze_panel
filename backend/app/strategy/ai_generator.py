"""Static strategy-source checks: safety lint, META parsing and the execution entrypoint.

Code is never executed here. The engine lints every strategy file it loads.
"""
from __future__ import annotations

import ast
import math
import re

from app.indicators.pipeline import ENRICHED_COLUMNS
from app.strategy.scoring import VIRTUAL_SCORING_DEPENDENCIES

# Accepted META assignment names, in lookup order; the engine reads the same names.
META_NAMES = ("META", "STRATEGY_META", "meta")
_FENCED_CODE_RE = re.compile(
    r"```(?P<language>[^\n`]*)\r?\n(?P<code>.*?)```",
    re.DOTALL,
)
_POLARS_ENTRYPOINT_ERROR = "找不到策略入口函数 filter() 或 filter_history()"
_MATRIX_ENTRYPOINT_ERROR = "找不到 Matrix 策略入口 MATRIX_STRATEGY"
_ENTRYPOINT_ERRORS = {
    "matrix_native": "matrix_native 策略只能定义 MATRIX_STRATEGY, 不得定义 filter() 或 filter_history()",
    "polars_expr": "polars_expr 策略只能定义 filter(), 不得定义 filter_history()",
    "python_history_legacy": "python_history_legacy 策略只能定义 filter_history(), 不得定义 filter()",
}
_BACKEND_ERROR_PREFIX = "不支持的执行后端"

# Neither JOIN columns nor the live-only limit distances exist on the stored rows a Polars score reads.
_POLARS_SCORING_FIELDS = frozenset(
    name
    for name in ENRICHED_COLUMNS
    if name not in {"symbol", "date", "name", "total_shares", "float_shares",
                    "distance_to_upper_limit", "distance_to_lower_limit"}
    and not name.startswith("signal_")
) | frozenset(VIRTUAL_SCORING_DEPENDENCIES)

_MATRIX_STORED_SCORING_FIELDS = frozenset({
    "open", "high", "low", "close", "volume", "amount", "turnover_rate",
    "consecutive_limit_ups", "consecutive_limit_downs", "ma20_bias",
})
# Keep in step with the name families app.backtest.matrix.matrix_feature computes.
_MATRIX_FEATURE_FAMILY_RE = re.compile(r"(?:ma|ema)\d+(?:_bias)?|rsi_\d+|momentum_\d+d")


class _MatrixScoringFields:
    """Membership test for the fields a matrix_native score can read through matrix_feature."""

    def __contains__(self, name: object) -> bool:
        from app.backtest.matrix import _MATRIX_COMPUTED_FEATURES

        return isinstance(name, str) and (
            name in _MATRIX_STORED_SCORING_FIELDS
            or name in _MATRIX_COMPUTED_FEATURES
            or _MATRIX_FEATURE_FAMILY_RE.fullmatch(name) is not None
        )


_MATRIX_SCORING_FIELDS = _MatrixScoringFields()


def _top_level_assignment(
    tree: ast.Module,
    name: str,
) -> tuple[ast.Name, ast.expr | None] | None:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            target = next(
                (item for item in node.targets
                 if isinstance(item, ast.Name) and item.id == name),
                None,
            )
            if target is not None:
                return target, node.value
        elif isinstance(node, ast.AnnAssign) \
                and isinstance(node.target, ast.Name) \
                and node.target.id == name:
            return node.target, node.value
    return None


def find_meta_assignment(code: str) -> tuple[ast.Name, ast.Dict] | None:
    """Find a supported module-level META assignment without executing code."""
    tree = ast.parse(code)
    for name in META_NAMES:
        found = _top_level_assignment(tree, name)
        if found is not None:
            target, value = found
            if not isinstance(value, ast.Dict):
                raise ValueError(f"{name} 必须是字面量字典")
            return target, value
    return None


def _strategy_execution_backend(tree: ast.Module, meta: dict | None = None) -> str:
    found = _top_level_assignment(tree, "EXECUTION_BACKEND")
    if found is not None:
        try:
            value = ast.literal_eval(found[1])
        except (ValueError, SyntaxError):
            value = None
        if isinstance(value, str):
            return value
    if isinstance(meta, dict) and isinstance(meta.get("execution_backend"), str):
        return meta["execution_backend"]
    if any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "filter_history"
        for node in tree.body
    ):
        return "python_history_legacy"
    return "polars_expr"


def _strategy_entrypoint_error(code: str, meta: dict | None = None) -> str | None:
    """The entry-point rule StrategyEngine._load_file enforces for the resolved backend, or None."""
    tree = ast.parse(code)
    backend = _strategy_execution_backend(tree, meta)
    functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    has_filter = "filter" in functions
    has_history = "filter_history" in functions
    if backend == "matrix_native":
        if not _top_level_assignment(tree, "MATRIX_STRATEGY"):
            return _MATRIX_ENTRYPOINT_ERROR
        return _ENTRYPOINT_ERRORS[backend] if has_filter or has_history else None
    if backend not in _ENTRYPOINT_ERRORS:
        return f"{_BACKEND_ERROR_PREFIX} {backend!r}, 请使用 polars_expr、python_history_legacy 或 matrix_native"
    if not has_filter and not has_history:
        return _POLARS_ENTRYPOINT_ERROR
    expected_filter = backend == "polars_expr"
    return None if has_filter == expected_filter and has_history != expected_filter else _ENTRYPOINT_ERRORS[backend]


class AIStrategyGenerator:
    """Static checks for strategy source: safety lint, META and the execution entrypoint."""

    def validate_code(self, code: str) -> dict:
        code = self._extract_code_block(code)

        # 验证
        try:
            self._validate_safety(code)
        except SyntaxError as e:
            return {
                "code": code,
                "meta": {},
                "valid": False,
                "error": f"Python 语法错误: {e.msg}",
            }
        except ValueError as e:
            return {
                "code": code,
                "meta": {},
                "valid": False,
                "error": str(e),
            }

        # 试加载获取 META
        try:
            meta = self._extract_meta(code)
        except Exception as e:
            return {
                "code": code,
                "meta": {},
                "valid": False,
                "error": f"解析META失败: {e}",
            }

        entrypoint_error = _strategy_entrypoint_error(code, meta)
        if entrypoint_error:
            return {
                "code": code,
                "meta": meta,
                "valid": False,
                "error": entrypoint_error,
            }

        try:
            self._validate_meta_semantics(code, meta)
        except ValueError as e:
            return {
                "code": code,
                "meta": meta,
                "valid": False,
                "error": str(e),
            }

        return {
            "code": code,
            "meta": meta,
            "valid": True,
            "error": None,
        }

    @staticmethod
    def _validate_meta_semantics(code: str, meta: dict) -> None:
        from app.strategy.engine import STRATEGY_ASSET_TYPES
        from app.strategy.scoring import meta_scoring_directions

        params = meta.get("params", [])
        if isinstance(params, (list, tuple)):
            for index, item in enumerate(params):
                if isinstance(item, dict) and not str(item.get("id") or "").strip():
                    raise ValueError(f"META.params[{index}] 缺少非空 id")

        for field_name in ("asset_types", "timeframes"):
            values = meta.get(field_name)
            if values is None:
                continue
            if not isinstance(values, (list, tuple)) or not values \
                    or any(not isinstance(value, str) or not value for value in values):
                raise ValueError(f"META.{field_name} 必须是非空字符串列表")
        unknown_assets = sorted(set(meta.get("asset_types") or ()) - STRATEGY_ASSET_TYPES)
        if unknown_assets:
            raise ValueError(
                f"META.asset_types 含不支持的值 {unknown_assets}; 只能用 {sorted(STRATEGY_ASSET_TYPES)}"
            )
        if "scoring_directions" in meta:
            try:
                meta_scoring_directions(meta["scoring_directions"])
            except ValueError as e:
                raise ValueError(f"META.scoring_directions 无效: {e}") from e

        scoring = meta.get("scoring", {})
        if not isinstance(scoring, dict):
            raise ValueError("META.scoring 必须是字典")
        if not scoring:
            return

        for name, weight in scoring.items():
            if not isinstance(name, str) or not name:
                raise ValueError("META.scoring 字段名必须是非空字符串")
            if isinstance(weight, bool) or not isinstance(weight, (int, float)) \
                    or not math.isfinite(float(weight)) or weight < 0:
                raise ValueError(f"META.scoring[{name!r}] 权重必须是非负有限数值")
        total_weight = sum(float(weight) for weight in scoring.values())
        if not math.isclose(total_weight, 1.0, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError("META.scoring 权重总和必须为 1.0")

        backend = _strategy_execution_backend(ast.parse(code), meta)
        if backend == "python_history_legacy":
            return
        allowed = _MATRIX_SCORING_FIELDS if backend == "matrix_native" else _POLARS_SCORING_FIELDS
        unknown = sorted(name for name in scoring if name not in allowed)
        if unknown:
            raise ValueError(
                f"META.scoring 引用了不可用字段: {unknown}; "
                "请使用真实数值字段或受控虚拟字段 ma20_bias"
            )

    @staticmethod
    def _extract_code_block(content: str) -> str:
        blocks = list(_FENCED_CODE_RE.finditer(content))
        for match in blocks:
            candidate = match.group("code").strip()
            try:
                found = find_meta_assignment(candidate)
                if found is not None:
                    meta = ast.literal_eval(found[1])
                    if isinstance(meta, dict) and _strategy_entrypoint_error(candidate, meta) is None:
                        return candidate
            except (SyntaxError, ValueError):
                continue
        for match in blocks:
            if match.group("language").strip().lower() in {"python", "py"}:
                return match.group("code").strip()
        if blocks:
            return blocks[0].group("code").strip()
        return content.strip()

    # Modules the Polars and matrix strategy contracts use; a lint hint, not a sandbox.
    _ALLOWED_IMPORT_MODULES = frozenset({
        "polars",
        "numpy",
        "app.backtest.matrix",
        "app.strategy.builtin.factor_rank_research",
        "datetime",
        "__future__",
    })

    @classmethod
    def _validate_safety(
        cls,
        code: str,
        *,
        extra_allowed_import_modules: frozenset[str] = frozenset(),
        extra_allowed_calls: frozenset[str] = frozenset(),
    ) -> None:
        """Lint strategy code for imports, builtin calls and dunder access outside the strategy contract.

        Not a security boundary: aliases and attribute chains pass it, and code that passes runs
        with full backend privileges. ALLOW_USER_PYTHON_STRATEGIES decides whether user files run.
        """
        tree = ast.parse(code)

        allowed_import_modules = cls._ALLOWED_IMPORT_MODULES | extra_allowed_import_modules
        forbidden_calls = {
            "open", "exec", "eval", "compile", "__import__",
            "globals", "locals", "vars", "dir", "getattr",
            "setattr", "delattr", "type", "input", "breakpoint",
        } - extra_allowed_calls

        def _module_allowed(module: str) -> bool:
            return (
                module in allowed_import_modules
                or module.split(".", 1)[0] in allowed_import_modules
            )

        forbidden_dunder_attrs = {
            "__globals__", "__builtins__", "__class__", "__subclasses__",
            "__mro__", "__bases__", "__base__", "__dict__", "__code__",
            "__import__", "__loader__", "__spec__", "__wrapped__",
        }
        forbidden_subscript_strs = {
            "__builtins__", "__import__", "__globals__",
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not _module_allowed(alias.name):
                        raise ValueError(f"禁止 import {alias.name} (策略只支持 polars、numpy 等策略协议模块)")
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if not _module_allowed(mod):
                    raise ValueError(f"禁止 from {node.module} import (策略只支持 polars、numpy 等策略协议模块)")
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
                    raise ValueError(f"禁止调用 {node.func.id}() (策略代码不使用此内建函数)")
            if isinstance(node, ast.Attribute) and node.attr in forbidden_dunder_attrs:
                raise ValueError(f"禁止访问属性 {node.attr} (策略代码不使用 dunder 属性)")
            if isinstance(node, ast.Subscript):
                sl = node.slice
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str) \
                        and sl.value in forbidden_subscript_strs:
                    raise ValueError(f"禁止下标访问 {sl.value} (策略代码不使用 dunder 名称)")

    @staticmethod
    def _extract_meta(code: str) -> dict:
        """从代码字符串中提取 META 字典（不执行代码, 仅接受字面量）

        兼容两种声明: META = {...} (Assign) 和 META: dict = {...} (AnnAssign)。
        与 api.strategy._find_meta_dict 保持同一套匹配逻辑。
        """
        found = find_meta_assignment(code)
        if found is None:
            raise ValueError("找不到 META 字典")
        _, value = found
        try:
            meta = ast.literal_eval(value)
        except (ValueError, SyntaxError) as e:
            raise ValueError(f"META 必须是纯字面量字典: {e}") from e
        if not isinstance(meta, dict):
            raise ValueError("META 必须是纯字面量字典")
        return meta
