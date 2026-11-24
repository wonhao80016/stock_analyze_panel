"""监控规则 API 路由 — HTTP 请求 → 调用 monitor_rules 模块 → 同步引擎内存态。

只做胶水: 校验 → 持久化 → 失效引擎内存态。不含评估逻辑。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from app.strategy import monitor_rules
from app.strategy.intraday_signals import INTRADAY_SIGNAL_LABELS, uses_intraday_signals

router = APIRouter(prefix="/api/monitor-rules", tags=["monitor-rules"])


def _data_dir(request: Request) -> Path:
    return request.app.state.repo.store.data_dir


def _reconcile_index_asset_type(rule: dict, repo) -> dict:
    """纠正误存为 stock 的指数规则 (asset_type → index)。

    个股弹窗加监控 / 点位提醒等入口未传 asset_type, 指数 symbol 的规则被存成
    stock, 导致监控中心显示「个股」、引擎在股票轮评估 (指数 symbol 永不命中)。
    仅当规则全部 symbols 都 resolve 为指数时纠正 (股票+指数混合池不动)。
    """
    if rule.get("asset_type", "stock") != "stock" or rule.get("scope") != "symbols":
        return rule
    symbols = [s for s in rule.get("symbols", []) if s]
    if not symbols:
        return rule
    try:
        if all(repo.resolve_asset_type(s) == "index" for s in symbols):
            rule["asset_type"] = "index"
    except Exception:  # noqa: BLE001
        pass
    return rule


def _validate_taiwan_symbols(symbols: list[str]) -> None:
    """驗證 scope=symbols 規則綁定的台股標的存在且可交易。

    只覆蓋能解析成台股 canonical 格式 (`{code}.{TWSE|TPEX}`) 的 symbol;
    其他市場格式 (如 A 股 `600000.SH`) 不受 Security Master 管轄, 略過。
    """
    from app.taiwan.benchmarks import is_benchmark
    from app.taiwan.symbol import parse_symbol
    from app.taiwan.universe import get_security_master

    sec_master = get_security_master()
    for sym in symbols:
        if not sym or is_benchmark(sym):
            continue
        try:
            parse_symbol(sym)
        except Exception:
            continue
        inst = sec_master.get_instrument(sym)
        if inst is None:
            raise HTTPException(status_code=400, detail=f"标的 {sym} 在证券主档中不存在")
        if not inst.is_supported:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"标的 {sym} 目前不可交易 "
                    f"(类型: {inst.instrument_type}, 上市状态: {inst.listing_status})"
                ),
            )


def _apply_benchmark_symbols(rule: dict) -> None:
    """Rewrite benchmark index symbols to their canonical case and keep them out of non-index rules."""
    from app.taiwan.benchmarks import get_benchmark

    symbols: list[str] = []
    for sym in rule.get("symbols", []):
        benchmark = get_benchmark(sym) if isinstance(sym, str) else None
        symbols.append(benchmark.symbol if benchmark else sym)
    rule["symbols"] = symbols


def _reject_benchmarks_outside_index_rules(rule: dict) -> None:
    from app.taiwan.benchmarks import is_benchmark

    if rule.get("asset_type", "stock") == "index":
        return
    benchmarks = [s for s in rule.get("symbols", []) if isinstance(s, str) and is_benchmark(s)]
    if benchmarks:
        raise HTTPException(
            status_code=400,
            detail=f"{'、'.join(benchmarks)} 是指數代號, 只能用於資產類型為「指數」的規則",
        )


def _sync_engine(request: Request) -> None:
    """保存/删除后,把最新规则集 reload 到引擎内存态。"""
    engine = getattr(request.app.state, "monitor_engine", None)
    if engine is not None:
        repo = request.app.state.repo
        rules = [
            _reconcile_index_asset_type(r, repo)
            for r in monitor_rules.load_all(_data_dir(request))
        ]
        engine.set_rules(rules)


# ── Pydantic 模型 ───────────────────────────────────────
class ConditionModel(BaseModel):
    field: str
    op: str            # truth | > >= < <= == !=
    value: float | None = None   # op 非 truth 时必填
    # 遲滯重新啟用門檻: 命中後需等 field 反向穿越此值才重新允許觸發; None 退化為純邊緣觸發
    rearm_value: float | None = None


class SectorTargetModel(BaseModel):
    key: str
    kind: str
    name: str
    symbol: str | None = None
    source_id: str | None = None
    field: str | None = None
    source_field: str | None = None
    value: str | None = None
    level: int | None = None
    available: bool = True
    member_count: int = 0


class RuleModel(BaseModel):
    id: str
    name: str
    enabled: bool = True
    type: str          # strategy | signal | price | market | sector
    asset_type: str = "stock"   # stock | etf (etf: strategy 型走 ETF 历史加载器)
    scope: str = "symbols"   # symbols | all | sector | watchlist_group
    symbols: list[str] = []
    # watchlist_group 作用域: 绑定的自选分组 id (成员动态解析, 增删自选自动生效)
    group_id: str | None = None
    sector: str | None = None
    sector_kind: str | None = None  # index | concept | industry
    sector_targets: list[SectorTargetModel] = []
    sector_trigger: str = "change_pct"  # change_pct | momentum
    threshold_pct: float = 1.0
    window_minutes: int = 5
    strategy_id: str | None = None
    direction: str = "entry"  # entry | exit | both | (sector: up|down)
    notify_events: list[str] | None = None
    # Deprecated: scores are min-max normalized per day, so a stored range is only kept or removed.
    score_min: float | None = None
    score_max: float | None = None
    max_rank: Annotated[int, Field(strict=True, ge=1)] | None = None
    conditions: list[ConditionModel] = []
    logic: str = "and"        # and | or
    trigger_mode: str = "edge"  # edge=未命中转命中才触发 | level=命中期间每 cooldown 重播
    cooldown_seconds: int = 3600
    severity: str = "info"    # info | warn | critical
    webhook_url: str = ""     # Webhook 推送地址 (推送到 QMT 等外部软件, 待定)
    webhook_enabled: bool = False  # 兼容老规则 (已由 webhook_channels 取代, 仅做向后兼容读)
    webhook_channels: list[str] = []  # 命中时推播的外部渠道 (合法值 'line' | 'telegram')
    message: str = ""


# ── 字段选项 ─────────────────────────────────────────────
@router.get("/options")
def get_options(request: Request):
    """返回可选字段、信号列、运算符、枚举,供前端表单使用。"""
    from app.indicators.pipeline import ENRICHED_COLUMNS
    from app.services.kline_sync import intraday_monitor_support
    from app.strategy.custom_signals import ALLOWED_FIELDS, load_all as load_csg

    # 阈值字段 (带中文标签)
    threshold_fields = [
        {"key": f, "label": ENRICHED_COLUMNS.get(f, f)}
        for f in sorted(ALLOWED_FIELDS)
    ]
    # 内置信号列 (布尔, 用于 op=truth)
    builtin_signals = [
        {"key": k, "label": v}
        for k, v in ENRICHED_COLUMNS.items()
        if k.startswith("signal_")
    ]
    builtin_signals.extend(
        {"key": key, "label": label}
        for key, label in INTRADAY_SIGNAL_LABELS.items()
    )
    # 自定义信号列 (csg_)
    custom_sigs = []
    try:
        for cs in load_csg(_data_dir(request)):
            if cs.get("enabled") is not False:
                custom_sigs.append({
                    "key": f"csg_{cs['id']}",
                    "label": cs.get("name", cs["id"]),
                })
    except Exception:
        pass

    sector_service = getattr(request.app.state, "sector_monitor_service", None)
    sector_targets = sector_service.list_targets() if sector_service is not None else {
        "index": [], "concept": [], "industry": [],
    }
    return {
        "threshold_fields": threshold_fields,
        "builtin_signals": builtin_signals,
        "custom_signals": custom_sigs,
        "operators": [">", ">=", "<", "<=", "==", "!="],
        "types": [
            {"key": "signal", "label": "訊號"},
            {"key": "price", "label": "價格/漲跌"},
            {"key": "market", "label": "市場異動"},
            {"key": "strategy", "label": "策略監控"},
            {"key": "sector", "label": "板塊監控"},
        ],
        "scopes": [
            {"key": "symbols", "label": "指定標的"},
            {"key": "watchlist_group", "label": "自選分組"},
            {"key": "all", "label": "全市場"},
        ],
        "logics": [
            {"key": "and", "label": "全部滿足 (AND)"},
            {"key": "or", "label": "任一滿足 (OR)"},
        ],
        "severities": [
            {"key": "info", "label": "普通"},
            {"key": "warn", "label": "警告"},
            {"key": "critical", "label": "重要"},
        ],
        "directions": [
            {"key": "entry", "label": "入场"},
            {"key": "exit", "label": "出场"},
            {"key": "both", "label": "出入都报"},
        ],
        "intraday_signal_support": intraday_monitor_support(),
        "sector_targets": sector_targets,
    }


# ── 列表 ───────────────────────────────────────────────
@router.get("")
def list_rules(request: Request):
    repo = request.app.state.repo

    def _add_runtime_warning(rule: dict, warning: str) -> None:
        # Several checks can apply to one rule; keep every reason instead of the last one.
        reasons = [r for r in str(rule.get("runtime_warning") or "").split("; ") if r]
        if warning not in reasons:
            reasons.append(warning)
        rule["runtime_warning"] = "; ".join(reasons)

    rules = [
        _reconcile_index_asset_type(r, repo)
        for r in monitor_rules.load_all(_data_dir(request))
    ]
    from app.services.kline_sync import intraday_monitor_support

    support = intraday_monitor_support()
    intraday_rules = [
        rule for rule in rules
        if rule.get("enabled", True) and uses_intraday_signals(rule)
    ]
    pooled_symbols = {
        str(symbol)
        for rule in intraday_rules
        for symbol in rule.get("symbols", [])
        if symbol
    }
    runtime_warning = ""
    if intraday_rules and not support["available"]:
        runtime_warning = str(support["reason"])
    elif len(pooled_symbols) > int(support["max_symbols"]):
        runtime_warning = (
            f"分时监听标的池已超限: {len(pooled_symbols)}/{support['max_symbols']}"
        )
    if runtime_warning:
        for rule in intraday_rules:
            _add_runtime_warning(rule, runtime_warning)
    sector_service = getattr(request.app.state, "sector_monitor_service", None)
    if sector_service is not None:
        for rule in rules:
            if rule.get("type") != "sector":
                continue
            missing = sector_service.missing_target_keys(rule.get("sector_targets", []))
            unavailable = sector_service.unavailable_target_keys(rule.get("sector_targets", []))
            if missing:
                _add_runtime_warning(rule, "部分板块数据已不存在, 请重新选择监控对象")
            elif unavailable:
                _add_runtime_warning(rule, "所选指数未加入实时指数池, 请先在实时监控设置中启用")
    for rule in rules:
        index_error = monitor_rules.taiwan_index_condition_error(rule)
        if index_error:
            _add_runtime_warning(rule, index_error)
    # A csg_ condition on a deleted or disabled custom signal never fires.
    from app.strategy import custom_signals

    signal_enabled = {
        sig.get("id"): sig.get("enabled") is not False
        for sig in custom_signals.load_all(_data_dir(request))
    }
    for rule in rules:
        for cond in rule.get("conditions") or []:
            field = cond.get("field", "") if isinstance(cond, dict) else ""
            if not field.startswith(custom_signals.PREFIX):
                continue
            state = signal_enabled.get(field[len(custom_signals.PREFIX):])
            if state is None:
                _add_runtime_warning(rule, f"引用的自訂訊號 {field} 不存在, 這條規則不會觸發")
                break
            if state is False:
                _add_runtime_warning(rule, f"引用的自訂訊號 {field} 已停用, 這條規則不會觸發")
                break
    # 分组作用域规则: 绑定的分组被删除 → 标注运行时警告 (引擎侧已 fail-closed 跳过)
    group_rules = [rule for rule in rules if rule.get("scope") == "watchlist_group"]
    if group_rules:
        from app.services import watchlist as watchlist_service

        try:
            existing_ids = {g["id"] for g in watchlist_service.list_groups()}
            for rule in group_rules:
                if rule.get("group_id") not in existing_ids:
                    _add_runtime_warning(rule, "绑定的自选分组已删除, 规则已暂停监控, 编辑可重新选择")
        except Exception:  # noqa: BLE001
            pass
    # 按 created_at 倒序
    rules.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return {"rules": rules}


# ── 新建 / 更新 ────────────────────────────────────────
@router.post("")
def save_rule(req: RuleModel, request: Request):
    rule = monitor_rules.normalize(req.model_dump())
    _apply_benchmark_symbols(rule)
    rule = _reconcile_index_asset_type(rule, request.app.state.repo)
    _reject_benchmarks_outside_index_rules(rule)
    if rule.get("type") == "strategy":
        from app.strategy.engine import StrategyDataContext

        strategy_engine = getattr(request.app.state, "strategy_engine", None)
        if strategy_engine is None:
            raise HTTPException(status_code=503, detail="策略引擎未初始化")
        try:
            strategy = strategy_engine.get(str(rule.get("strategy_id")))
            strategy_engine.validate_context(
                strategy,
                StrategyDataContext(
                    asset_type=str(rule.get("asset_type") or "stock"),
                    timeframe="1d",
                    as_of=date.today(),
                ),
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    # 编辑现有规则时, 保留原 created_at (避免按时间排序时位置跳动)
    existing = monitor_rules.load_one(_data_dir(request), rule["id"])
    if existing and existing.get("created_at"):
        rule["created_at"] = existing["created_at"]
    for key in ("score_min", "score_max"):
        if rule.get(key) is not None and rule.get(key) != (existing or {}).get(key):
            raise HTTPException(
                status_code=400,
                detail="評分範圍是當天候選內的相對分數, 已停用; 請改用「排名前 N 名」",
            )
    try:
        monitor_rules.validate(rule)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if rule.get("scope") == "symbols":
        _validate_taiwan_symbols(rule.get("symbols", []))
    if rule.get("scope") == "watchlist_group":
        # 绑定的分组必须存在 (strategy 层校验形状, 存在性在本层校验)
        from app.services import watchlist as watchlist_service

        group_id = str(rule.get("group_id") or "")
        try:
            group_ids = {g["id"] for g in watchlist_service.list_groups()}
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"自选分组读取失败: {e}") from e
        if group_id not in group_ids:
            raise HTTPException(status_code=400, detail="自选分组不存在或已被删除, 请重新选择")
    if rule.get("type") == "sector":
        sector_service = getattr(request.app.state, "sector_monitor_service", None)
        if sector_service is None:
            raise HTTPException(status_code=503, detail="板块监控服务未初始化")
        targets = rule.get("sector_targets", [])
        if sector_service.missing_target_keys(targets):
            raise HTTPException(status_code=400, detail="所选板块数据已变化, 请重新选择")
        if sector_service.unavailable_target_keys(targets):
            raise HTTPException(status_code=400, detail="所选指数未加入实时指数池, 请先在实时监控设置中启用")
    if rule.get("enabled", True) and uses_intraday_signals(rule):
        from app.services.kline_sync import intraday_monitor_support

        support = intraday_monitor_support()
        if not support["available"]:
            raise HTTPException(status_code=403, detail=str(support["reason"]))
        symbols = set(str(symbol) for symbol in rule.get("symbols", []) if symbol)
        for saved in monitor_rules.load_all(_data_dir(request)):
            if (
                saved.get("id") != rule.get("id")
                and saved.get("enabled", True)
                and uses_intraday_signals(saved)
            ):
                symbols.update(str(symbol) for symbol in saved.get("symbols", []) if symbol)
        max_symbols = int(support["max_symbols"])
        if len(symbols) > max_symbols:
            raise HTTPException(
                status_code=400,
                detail=f"当前分时数据能力最多监听 {max_symbols} 只标的,当前规则合计 {len(symbols)} 只",
            )
    monitor_rules.save_one(_data_dir(request), rule)
    _sync_engine(request)
    return {"ok": True, "rule": rule}


class EnabledModel(BaseModel):
    enabled: bool


@router.put("/{rule_id}/enabled")
def set_rule_enabled(rule_id: str, req: EnabledModel, request: Request):
    """Toggle a rule; disabling skips binding validation so a rule with a deleted group,
    strategy or symbol can still be paused, enabling goes through the full save path."""
    if not monitor_rules.is_valid_rule_id(rule_id):
        raise HTTPException(status_code=400, detail="規則 id 非法")
    existing = monitor_rules.load_one(_data_dir(request), rule_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="規則不存在")
    if req.enabled:
        try:
            model = RuleModel(**{**existing, "enabled": True})
        except ValidationError as e:
            raise HTTPException(status_code=400, detail="規則內容無法通過校驗, 請編輯後再啟用") from e
        return save_rule(model, request)
    existing["enabled"] = False
    monitor_rules.save_one(_data_dir(request), existing)
    _sync_engine(request)
    return {"ok": True, "rule": existing}


# ── 删除 ───────────────────────────────────────────────
@router.delete("/{rule_id}")
def delete_rule(rule_id: str, request: Request):
    if not monitor_rules.is_valid_rule_id(rule_id):
        raise HTTPException(status_code=400, detail="规则 id 非法")
    deleted = monitor_rules.delete_one(_data_dir(request), rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="规则不存在")
    _sync_engine(request)
    return {"ok": True}

