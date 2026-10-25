"""AI 策略 META 规范化回归测试。"""
from __future__ import annotations

from app.strategy.ai_generator import AIStrategyGenerator

RAW_CODE = '''"""模型返回的策略"""
import polars as pl

META = {
    "id": "custom_wrong_id",
    "name": "English Placeholder",
    "description": "model desc",
    "tags": ["AI"],
    "params": [],
    "scoring": {},
}

ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.05
MAX_HOLD_DAYS = 20

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    return pl.lit(True)
'''


# --- 回归: LLM 偏移写法 -------------------------------------------------
# 模型常给 META 加类型注解 (META: dict = {...}, ast.AnnAssign 节点)。
# 旧版匹配器只遍历 ast.Assign, 漏掉注解形式 → 报「找不到 META 字典」。

ANNOTATED_CODE = '''"""模型返回的策略 (带类型注解的 META — LLM 常见偏移)"""
import polars as pl

META: dict = {
    "id": "annotated_wrong_id",
    "name": "Placeholder",
    "description": "model desc",
    "tags": ["AI"],
    "params": [],
    "scoring": {},
}

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    return pl.lit(True)
'''


def test_extract_meta_accepts_type_annotated_form():
    from app.strategy.ai_generator import AIStrategyGenerator

    meta = AIStrategyGenerator._extract_meta(ANNOTATED_CODE)
    assert meta["id"] == "annotated_wrong_id"
    assert meta["name"] == "Placeholder"


def test_validate_code_rejects_missing_meta():
    from app.strategy.ai_generator import AIStrategyGenerator

    code = """import polars as pl

def filter(df, params):
    return pl.lit(True)
"""

    result = AIStrategyGenerator().validate_code(code)

    assert result["valid"] is False
    assert "找不到 META 字典" in result["error"]


def test_validate_code_ignores_meta_inside_function():
    from app.strategy.ai_generator import AIStrategyGenerator

    code = """import polars as pl

def filter(df, params):
    META = {"id": "nested"}
    return pl.lit(True)
"""

    result = AIStrategyGenerator().validate_code(code)

    assert result["valid"] is False
    assert "找不到 META 字典" in result["error"]


def test_validate_code_rejects_missing_strategy_entrypoint():
    from app.strategy.ai_generator import AIStrategyGenerator

    result = AIStrategyGenerator().validate_code('META = {"id": "no_filter"}')

    assert result["valid"] is False
    assert result["error"] == "找不到策略入口函数 filter() 或 filter_history()"


def test_validate_code_accepts_matrix_strategy_entrypoint():
    from app.strategy.ai_generator import AIStrategyGenerator

    code = '''META = {"id": "matrix", "execution_backend": "matrix_native"}
EXECUTION_BACKEND = "matrix_native"
MATRIX_STRATEGY = object()
'''

    result = AIStrategyGenerator().validate_code(code)

    assert result["valid"] is True
    assert result["error"] is None


def test_validate_code_accepts_controlled_virtual_scoring_field():
    code = RAW_CODE.replace(
        '"scoring": {},',
        '"scoring": {"ma20_bias": 0.6, "vol_ratio_5d": 0.4},',
    )

    result = AIStrategyGenerator().validate_code(code)

    assert result["valid"] is True


def test_validate_code_rejects_unknown_scoring_field():
    code = RAW_CODE.replace(
        '"scoring": {},',
        '"scoring": {"close_above_ma20": 1.0},',
    )

    result = AIStrategyGenerator().validate_code(code)

    assert result["valid"] is False
    assert "close_above_ma20" in result["error"]


def test_validate_code_rejects_param_without_id():
    code = RAW_CODE.replace(
        '"params": [],',
        '"params": [{"name": "volume_ratio", "default": 1.5}],',
    )

    result = AIStrategyGenerator().validate_code(code)

    assert result["valid"] is False
    assert result["error"] == "META.params[0] 缺少非空 id"


def test_validate_code_rejects_missing_matrix_strategy_entrypoint():
    from app.strategy.ai_generator import AIStrategyGenerator

    code = '''META = {"id": "matrix", "execution_backend": "matrix_native"}
EXECUTION_BACKEND = "matrix_native"
'''

    result = AIStrategyGenerator().validate_code(code)

    assert result["valid"] is False
    assert result["error"] == "找不到 Matrix 策略入口 MATRIX_STRATEGY"


def test_extract_code_block_prefers_complete_strategy():
    from app.strategy.ai_generator import AIStrategyGenerator

    content = f"""```python
print("draft")
```

```python
{RAW_CODE}
```
"""

    assert AIStrategyGenerator._extract_code_block(content) == RAW_CODE.strip()
