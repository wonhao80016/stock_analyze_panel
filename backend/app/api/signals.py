"""自定义信号 API 路由 — HTTP 请求 → 调用 custom_signals 模块 → 返回响应。

只做胶水：校验 → 持久化 → 失效缓存。不含表达式编译逻辑。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.strategy import custom_signals

router = APIRouter(prefix="/api/custom-signals", tags=["custom-signals"])


def _data_dir(request: Request) -> Path:
    return request.app.state.repo.store.data_dir


def _invalidate(request: Request) -> None:
    """失效自定义信号表达式缓存, 并清掉含旧信号列的计算缓存。

    信号增删会改变注入列集合: 只清表达式缓存不够, repo 内存缓存 /
    strategy 磁盘缓存里算好的历史窗口仍不含新 csg_ 列 (或仍含已删列),
    需要一并清除, 否则创建信号后立即运行策略仍会报缺列。
    """
    from app.indicators.pipeline import invalidate_custom_signals
    invalidate_custom_signals()
    from app.services import strategy_cache
    strategy_cache.clear_cache(_data_dir(request))
    repo = request.app.state.repo
    if hasattr(repo, "clear_cache"):
        repo.clear_cache()


class ConditionModel(BaseModel):
    left: str        # 字段名（须在白名单）
    op: str          # > >= < <= == !=
    right: str       # "field:xxx" 或数字字符串
    leftDays: int = 0    # 左字段取几日前 (0=当日, 默认)
    rightDays: int = 0   # 右字段取几日前 (仅 right 为字段时有意义)


class SignalModel(BaseModel):
    id: str
    name: str
    kind: str        # entry | exit | both
    conditions: list[ConditionModel]
    enabled: bool = True


class AIGenerateRequest(BaseModel):
    description: str


# ── 字段选项 / 运算符 ───────────────────────────────────


@router.get("/options")
def get_options():
    """返回可选字段与运算符，供前端下拉框使用。"""
    # 字段带中文标签（取自 ENRICHED_COLUMNS，回退为字段名本身）
    from app.indicators.pipeline import ENRICHED_COLUMNS, ENRICHED_COLUMNS_BY_CATEGORY

    allowed = custom_signals.ALLOWED_FIELDS

    def _field(f: str) -> dict:
        return {"key": f, "label": ENRICHED_COLUMNS.get(f, f), "unit": custom_signals.FIELD_UNITS[f]}

    fields = [_field(f) for f in sorted(allowed)]
    # 字段分组 (只包含白名单内的字段, 供前端 optoptgroup 渲染)
    _GROUP_LABELS = {
        "basic": "基础", "ma": "均线 MA", "ema": "指数均线 EMA",
        "macd": "MACD", "boll": "布林带 BOLL", "kdj": "KDJ",
        "atr": "ATR", "volume": "量价", "extremes": "极值",
        "momentum": "动量", "volatility": "波动率", "rsi": "RSI",
    }
    # 行情类字段不在 ENRICHED_COLUMNS_BY_CATEGORY 里, 单独归一组
    quote_fields = {"open", "high", "low", "close", "volume", "amount",
                    "turnover_rate", "consecutive_limit_ups", "consecutive_limit_downs"}
    groups = [{"key": "quote", "label": "行情",
               "fields": [_field(f) for f in sorted(allowed & quote_fields)]}]
    for cat, label in _GROUP_LABELS.items():
        cat_fields = [f for f in ENRICHED_COLUMNS_BY_CATEGORY.get(cat, []) if f in allowed]
        if cat_fields:
            groups.append({"key": cat, "label": label,
                           "fields": [_field(f) for f in cat_fields]})

    return {
        "fields": fields,
        "groups": groups,
        "maxDays": custom_signals.MAX_DAYS,
        "units": custom_signals.UNIT_LABELS,
        "operators": [">", ">=", "<", "<=", "==", "!="],
    }


# ── 列表 ───────────────────────────────────────────────


@router.get("")
def list_signals(request: Request):
    sigs = custom_signals.load_all(_data_dir(request))
    return {"signals": sigs}


# ── 新建 / 更新 / 引用 ──────────────────────────────────


def _validated(req: SignalModel) -> dict:
    sig = req.model_dump()
    try:
        custom_signals.validate(sig)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return sig


def _mentions(value: object, column: str) -> bool:
    if isinstance(value, str):
        return value == column
    if isinstance(value, dict):
        return any(_mentions(item, column) for item in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_mentions(item, column) for item in value)
    return False


def _references(request: Request, signal_id: str) -> dict[str, list[dict]]:
    """Monitor rules and strategies that read this signal's csg_ column."""
    from app.strategy import config as strategy_config
    from app.strategy import monitor_rules

    data_dir = _data_dir(request)
    column = custom_signals.column_name(signal_id)
    rules = [
        {"id": rule.get("id"), "name": rule.get("name")}
        for rule in monitor_rules.load_all(data_dir)
        if any(
            isinstance(c, dict) and c.get("field") == column
            for c in rule.get("conditions") or []
        )
    ]
    engine = getattr(request.app.state, "strategy_engine", None)
    definitions = {str(s.meta["id"]): s for s in engine.strategy_definitions()} if engine else {}
    using = {
        sid for sid, s in definitions.items()
        if _mentions([s.entry_signals, s.exit_signals, s.required_features], column)
    }
    using.update(
        sid for sid, overrides in strategy_config.list_overrides(data_dir).items()
        if _mentions(overrides, column)
    )
    strategies = [
        {"id": sid, "name": str(definitions[sid].meta.get("name") or sid) if sid in definitions else sid}
        for sid in sorted(using)
    ]
    return {"monitor_rules": rules, "strategies": strategies}


def _refuse_if_referenced(request: Request, signal_id: str, action: str) -> None:
    references = _references(request, signal_id)
    count = len(references["monitor_rules"]) + len(references["strategies"])
    if count:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"訊號 {signal_id} 仍被 {count} 條監控規則或策略引用, {action}後它們讀不到這個訊號",
                **references,
            },
        )


@router.post("")
def create_signal(req: SignalModel, request: Request):
    sig = _validated(req)
    data_dir = _data_dir(request)
    if custom_signals.exists(data_dir, sig["id"]):
        raise HTTPException(status_code=409, detail=f"訊號標識 {sig['id']} 已存在, 請換一個標識或改用編輯")
    custom_signals.save_one(data_dir, sig)
    _invalidate(request)
    return {"ok": True, "signal": sig}


@router.put("/{signal_id}")
def update_signal(signal_id: str, req: SignalModel, request: Request, force: bool = False):
    sig = _validated(req)
    if sig["id"] != signal_id:
        raise HTTPException(status_code=400, detail="訊號標識不可修改")
    data_dir = _data_dir(request)
    existing = custom_signals.load_one(data_dir, signal_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="信号不存在")
    if existing.get("enabled") is not False and sig["enabled"] is False and not force:
        _refuse_if_referenced(request, signal_id, "停用")
    custom_signals.save_one(data_dir, sig)
    _invalidate(request)
    return {"ok": True, "signal": sig}


@router.get("/{signal_id}/references")
def signal_references(signal_id: str, request: Request):
    if not custom_signals.ID_RE.match(signal_id):
        raise HTTPException(status_code=400, detail="信号 id 非法")
    return _references(request, signal_id)


# ── AI 生成 ─────────────────────────────────────────────


@router.post("/ai/generate")
async def ai_generate_signal(req: AIGenerateRequest):
    """AI 根据自然语言描述生成自定义信号条件。

    不落盘：只返回 {name, conditions} 供前端回填表单，由用户确认后走
    常规 save 流程。校验复用 custom_signals.validate()（白名单安全闸门）。
    """
    from app.services.ai_provider import generate_ai_text
    from app.strategy import custom_signals_ai

    description = req.description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="请先描述信号思路")
    if len(description) > 500:
        raise HTTPException(status_code=400, detail="描述过长（最多 500 字）")

    messages = custom_signals_ai.build_messages(description)
    try:
        # max_tokens=None 不传上限: 推理模型思考 token 计入预算, 显式限制
        # 会挤占正文导致 JSON 截断/0 字 (与四个分析器同因, 见 0ee3aa8)
        text = await generate_ai_text(messages, temperature=0.2, max_tokens=None)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"AI 生成失败: {e}") from e

    try:
        return custom_signals_ai.parse_and_validate(text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ── 删除 ───────────────────────────────────────────────


@router.delete("/{signal_id}")
def delete_signal(signal_id: str, request: Request, force: bool = False):
    if not custom_signals.ID_RE.match(signal_id):
        raise HTTPException(status_code=400, detail="信号 id 非法")
    data_dir = _data_dir(request)
    if not force and custom_signals.exists(data_dir, signal_id):
        _refuse_if_referenced(request, signal_id, "刪除")
    deleted = custom_signals.delete_one(data_dir, signal_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="信号不存在")
    _invalidate(request)
    return {"ok": True}
