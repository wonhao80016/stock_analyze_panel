"""策略实时监控 — 订阅行情更新，检查监控规则并推送告警。

MonitorRuleEngine 是通用规则引擎,覆盖 signal/price/market/strategy 四类,
支持 scope (symbols/all/sector) + 多条件 AND/OR + cooldown 去重。
"""
from __future__ import annotations

import datetime as _dt
import logging
import math
import threading
import time
from typing import Any, Callable

import polars as pl

from app.market_time import cn_today
from app.repository import HISTORY_WARMUP_SESSIONS
from app.strategy import config as _strategy_config
from app.strategy.custom_signals import _OP_BUILDERS  # type: ignore  # 复用运算符构造器
from app.strategy.field_labels import field_label
from app.strategy.intraday_signals import uses_intraday_signals

logger = logging.getLogger(__name__)


# ================================================================
# 通用监控规则引擎 MonitorRuleEngine
# ================================================================

_CONDITION_RULE_TYPES = frozenset({"signal", "price", "market"})


# ── 自选分组作用域: group_id → 成员集合解析 (进程内缓存) ────
# 缓存按 watchlist 数据版本号失效: 版本不变时零磁盘 IO; 自选页任何增删
# 分组/成员的操作都会 bump 版本号, 下一轮评估立即拿到新成员 (无需等 TTL)。
_group_cache_lock = threading.Lock()
_group_cache: dict[str, Any] = {}
# 已告警过的「分组已删除」(rule_id, group_id), 防止每轮评估刷日志
_warned_missing_groups: set[tuple[str, str]] = set()


def _watchlist_groups_snapshot() -> dict[str, frozenset[str]]:
    """返回 {group_id: 成员symbol集}。读前后版本一致才写缓存, 避免缓存住写竞态下的旧数据。"""
    from app.services import watchlist

    rev_before = watchlist.revision()
    with _group_cache_lock:
        cached = _group_cache.get("groups")
        if cached is not None and _group_cache.get("_rev") == rev_before:
            return cached
    groups: dict[str, set[str]] = {g["id"]: set() for g in watchlist.list_groups()}
    for row in watchlist.list_symbols():
        for gid in row.get("group_ids") or []:
            members = groups.get(gid)
            if members is not None:
                members.add(str(row["symbol"]))
    frozen = {gid: frozenset(syms) for gid, syms in groups.items()}
    if watchlist.revision() == rev_before:
        with _group_cache_lock:
            _group_cache["_rev"] = rev_before
            _group_cache["groups"] = frozen
    return frozen


def _group_members_or_none(rule: dict) -> frozenset[str] | None:
    """解析规则绑定的分组成员; 分组已删除返回 None, 解析异常返回 None 并记日志。"""
    group_id = str(rule.get("group_id") or "")
    try:
        groups = _watchlist_groups_snapshot()
    except Exception as exc:  # noqa: BLE001
        logger.warning("自选分组数据读取失败, 规则 %s 本轮跳过: %s", rule.get("id"), exc)
        return None
    members = groups.get(group_id)
    if members is None:
        key = (str(rule.get("id") or ""), group_id)
        if key not in _warned_missing_groups:
            _warned_missing_groups.add(key)
            logger.warning(
                "monitor rule %s is bound to deleted watchlist group %s and will not fire again (fail-closed); "
                "a recreated group gets a new id, so rebind the rule",
                rule.get("id"), group_id,
            )
    return members


def _condition_mask_expr(cols: set[str], conditions: list[dict], logic: str) -> pl.Expr | None:
    """构建 conditions + logic 的命中判定表达式。字段缺失或无合法条件时返回 None。"""
    parts: list[pl.Expr] = []
    for c in conditions:
        field = c["field"]
        if field not in cols:
            return None
        op = c["op"]
        if op == "truth":
            parts.append(pl.col(field).fill_null(False))
        elif op in _OP_BUILDERS:
            parts.append(_OP_BUILDERS[op](pl.col(field), c["value"]))
        else:
            return None
    if not parts:
        return None
    return pl.any_horizontal(parts) if logic == "or" else pl.all_horizontal(parts)


def _build_condition_mask(df: pl.DataFrame, conditions: list[dict], logic: str) -> pl.DataFrame:
    """根据 conditions + logic 构建过滤后的命中 DataFrame。

    conditions: [{"field","op","value"?}] — op=truth 为布尔信号, 否则阈值比较
    logic: "and" | "or"
    返回命中行 (含 symbol/name/close/change_pct + 各信号列)
    """
    expr = _condition_mask_expr(set(df.columns), conditions, logic)
    if expr is None:
        return df.head(0)  # 字段缺失或条件非法 → 空结果
    return df.filter(expr)


def _rearm_crossed(row: dict, conditions: list[dict]) -> bool:
    """判断某行是否已满足所有 rearm_value 门槛的反向穿越 (据此可重新 armed)。

    未设置 rearm_value 的规则退化为纯边缘触发: 条件一旦不命中即视为已穿越。
    == / != 无「反向」方向语义, 其 rearm_value 恒视为已满足 (不阻塞重新 armed)。
    """
    gated = [c for c in conditions if c.get("rearm_value") is not None and c.get("op") in _OP_BUILDERS]
    if not gated:
        return True
    for c in gated:
        value = row.get(c["field"])
        if value is None:
            return False
        rearm = c["rearm_value"]
        op = c["op"]
        if op in (">", ">=") and value > rearm:
            return False
        if op in ("<", "<=") and value < rearm:
            return False
    return True


def _conditions_signature(conditions: Any) -> tuple[Any, ...]:
    return tuple(
        (c.get("field"), c.get("op"), c.get("value"), c.get("rearm_value"))
        for c in (conditions if isinstance(conditions, list) else [])
        if isinstance(c, dict)
    )


_OP_WORDS = {
    ">": "大於", ">=": "大於等於", "<": "小於", "<=": "小於等於", "==": "等於", "!=": "不等於",
}
# Stored as decimals (0.03 = 3%); keep in sync with PERCENT_DECIMAL_FIELDS in GenericRulesList.tsx.
_PERCENT_DECIMAL_FIELDS = frozenset({"change_pct", "distance_to_upper_limit", "distance_to_lower_limit"})


def _format_condition_value(field: str, value: Any) -> str:
    if field in _PERCENT_DECIMAL_FIELDS and isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value * 100:.2f}".rstrip("0").rstrip(".") + "%"
    return str(value)


class MonitorRuleEngine:
    """通用监控规则引擎 — 接收实时行情 DataFrame,评估所有规则,返回 AlertEvent。

    规则来自 monitor_rules 存储; 同一 (rule_id, symbol, event_type) 在冷却期内不重复触发。
    """

    def __init__(self, alert_handler: Callable[[dict], None] | None = None):
        self._alert_handler = alert_handler
        self._rules: dict[str, dict] = {}  # rule_id → rule
        # (rule_id, symbol, event_type) → 上次触发时间戳(秒)。用于 cooldown 去重。
        self._last_fire: dict[tuple[str, str, str], float] = {}
        self._strategy_engine = None  # 延迟注入, type=strategy 规则用它跑选股
        # symbol → 股票名 (enriched DataFrame 已 drop name 列, 触发时从此映射回填)
        self._name_map: dict[str, str] = {}
        # 策略选股池状态: (rule_id, strategy_id, asset_type) → 上期选股符号集合
        self._strategy_pools: dict[tuple[str, str, str], set[str]] = {}
        # 策略信号状态: (rule_id, strategy_id, asset_type, event_type) → (K线日期, 命中集合)
        self._strategy_signal_state: dict[tuple[str, str, str, str], tuple[str, set[str]]] = {}
        # 同一根 K 线的信号即使盘中回落后再次命中也只通知一次。
        self._strategy_signal_seen: dict[tuple[str, str, str, str, str], str] = {}
        # 数据目录 (用于加载策略 overrides)
        self._data_dir = None
        # 历史窗口加载器: (target_date, lookback_days) → 多日 enriched DataFrame。
        # 用于声明 filter_history 的策略 (如反包), 实时监控时拼历史窗口 + 今日行情跑选股。
        # 为 None 时, filter_history 策略仍会被跳过 (保持旧行为, 不破坏无历史场景)。
        self._history_loader: Callable[[_dt.date, int], "pl.DataFrame"] | None = None
        # ETF 版历史窗口加载器 (asset_type=etf 的规则用)。为 None 时 ETF filter_history 策略跳过。
        self._history_loader_etf: Callable[[_dt.date, int], "pl.DataFrame"] | None = None
        self._active_matrix_snapshots: dict[str, Any] = {}
        # 本轮 evaluate() 产出的策略选股结果: strategy_id → {rows, total, as_of}
        # 供策略页实时回显复用 (/api/screener/cached 端点直接读取, 避免重跑)。
        # 注意: 始终是「完整」的 dict —— evaluate 重算时先写到 _building_strategy_results,
        # 算完后整体替换此属性, 保证 /cached 并发读取永远拿到完整结果, 不会读到空中间态。
        self._latest_strategy_results: dict[str, dict] = {}
        # 本轮重算的临时容器 (_match_strategy 写入它); reset 轮开始时初始化为空 dict,
        # evaluate 结束后一次性替换 _latest_strategy_results。
        self._building_strategy_results: dict[str, dict] = {}
        # 本轮成功写入股票策略实时结果的策略 ID, 供 QuoteService 在计算完成后精确通知策略页。
        self._latest_strategy_result_ids: set[str] = set()
        self._sector_monitor_service = None
        self._sector_condition_state: dict[tuple[str, str], bool] = {}
        # (rule_id, symbol, event_type) → 是否已重新 armed (可再次触发)。
        # 用于 signal/price/market 的边缘触发去重 (trigger_mode="edge"), 键形状比照 _last_fire。
        self._condition_state: dict[tuple[str, str, str], bool] = {}
        self._initial_rules_loaded = False

    def set_strategy_engine(self, engine) -> None:
        """注入 StrategyEngine, type=strategy 规则据此跑选股。"""
        self._strategy_engine = engine

    def set_data_dir(self, data_dir) -> None:
        """注入数据目录, 用于加载策略的用户覆盖配置。"""
        self._data_dir = data_dir

    def set_sector_monitor_service(self, service) -> None:
        self._sector_monitor_service = service

    def invalidate_strategy_state(self) -> None:
        """策略注册表变更后清除选股池、结果和矩阵快照。"""
        self._strategy_pools.clear()
        self._strategy_signal_state.clear()
        self._strategy_signal_seen.clear()
        self._latest_strategy_results = {}
        self._building_strategy_results = {}
        self._latest_strategy_result_ids.clear()
        self._active_matrix_snapshots.clear()

    def set_history_loader(self, fn) -> None:
        """注入历史窗口加载器, 用于声明 filter_history 的策略跑实时监控。

        loader 签名: (target_date, lookback_days) → 多日 enriched DataFrame。
        复用 ScreenerService._load_enriched_history (三级缓存, 命中 ~0ms)。
        为 None 时 filter_history 策略退回到跳过逻辑 (不破坏无历史场景)。
        """
        self._history_loader = fn

    def set_history_loader_etf(self, fn) -> None:
        """注入 ETF 版历史窗口加载器 (asset_type=etf 的 strategy 型规则用)。

        签名同 set_history_loader; 复用 ScreenerService(asset_type='etf')._load_enriched_history。
        为 None 时 ETF filter_history 策略退回到跳过逻辑。
        """
        self._history_loader_etf = fn

    def _history_loader_for(self, rule: dict):
        """按规则的 asset_type 选历史加载器。etf → ETF 加载器, 否则股票加载器。"""
        if rule.get("asset_type") == "etf":
            return self._history_loader_etf
        return self._history_loader

    def set_name_map(self, name_map: dict[str, str]) -> None:
        """注入 symbol → 股票名 映射, 用于在告警事件里回填 name 字段。

        enriched DataFrame 在 pipeline 计算后不含 name 列 (见 indicators/pipeline.py),
        触发时从 instruments 表预构建此映射, 保证 AlertEvent.name 有值。
        """
        self._name_map = name_map or {}

    # ── 规则管理 ───────────────────────────────────────
    @staticmethod
    def _rule_state_signature(rule: dict) -> tuple[Any, ...]:
        return (
            rule.get("type"),
            rule.get("strategy_id"),
            rule.get("score_min"),
            rule.get("score_max"),
            rule.get("asset_type", "stock"),
            rule.get("scope", "symbols"),
            tuple(sorted(str(symbol) for symbol in rule.get("symbols", []))),
            rule.get("sector"),
            rule.get("sector_kind"),
            tuple(sorted(str(target.get("key")) for target in rule.get("sector_targets", []))),
            rule.get("sector_trigger"),
            rule.get("direction"),
            rule.get("threshold_pct"),
            rule.get("window_minutes"),
            rule.get("group_id"),
            _conditions_signature(rule.get("conditions")),
            rule.get("logic", "and"),
            rule.get("trigger_mode", "edge"),
        )

    def set_rules(self, rules: list[dict]) -> None:
        """批量设置规则 (覆盖)。用于启动时 reload。

        先构建完整 dict 再原子替换 self._rules, 避免评估线程 (行情轮询)
        读到装载了一半的规则表。
        """
        new_rules: dict[str, dict] = {}
        for r in rules:
            if r.get("enabled") is not False:
                new_rules[r["id"]] = r
        changed_ids = {
            rule_id
            for rule_id, rule in new_rules.items()
            if rule_id in self._rules
            and self._rule_state_signature(self._rules[rule_id])
            != self._rule_state_signature(rule)
        }
        self._rules = new_rules
        active_ids = set(new_rules) - changed_ids
        self._last_fire = {
            key: value for key, value in list(self._last_fire.items()) if key[0] in active_ids
        }
        self._strategy_pools = {
            key: value for key, value in list(self._strategy_pools.items()) if key[0] in active_ids
        }
        self._strategy_signal_state = {
            key: value
            for key, value in list(self._strategy_signal_state.items())
            if key[0] in active_ids
        }
        self._strategy_signal_seen = {
            key: value
            for key, value in list(self._strategy_signal_seen.items())
            if key[0] in active_ids
        }
        self._sector_condition_state = {
            key: value
            for key, value in list(self._sector_condition_state.items())
            if key[0] in active_ids
        }
        self._condition_state = {
            key: value
            for key, value in list(self._condition_state.items())
            if key[0] in active_ids
        }
        if not self._initial_rules_loaded:
            self._initial_rules_loaded = True
            self._restore_todays_condition_fires(new_rules)
        logger.info("MonitorRuleEngine: 装载 %d 条规则", len(self._rules))

    def _restore_todays_condition_fires(self, rules: dict[str, dict]) -> None:
        """Resume edge and cooldown state of condition rules from today's (Taipei) alert log.

        Only fires already recorded today are suppressed; anything not yet fired today still fires.
        """
        if self._data_dir is None:
            return
        from app.market_time import CN_TZ
        from app.services import alert_store

        today = cn_today()
        try:
            events = alert_store.list_recent(self._data_dir, days=1)
        except Exception as exc:
            logger.warning("今日告警记录读取失败, 重启后不回填监控状态: %s", exc)
            return
        for event in events:
            rule = rules.get(str(event.get("rule_id") or ""))
            ts = event.get("ts")
            if rule is None or rule.get("type") not in _CONDITION_RULE_TYPES or not isinstance(ts, (int, float)):
                continue
            if _dt.datetime.fromtimestamp(ts / 1000, CN_TZ).date() != today:
                continue
            # Records predating the snapshot fields are taken as fired under the current conditions.
            if "conditions" in event and _conditions_signature(event.get("conditions")) != _conditions_signature(
                rule.get("conditions")
            ):
                continue
            if "logic" in event and event.get("logic") != rule.get("logic", "and"):
                continue
            key = (rule["id"], str(event.get("symbol") or ""), str(event.get("type") or ""))
            fired_at = ts / 1000
            if fired_at > self._last_fire.get(key, 0.0):
                self._last_fire[key] = fired_at
            if rule.get("trigger_mode", "edge") == "edge":
                self._condition_state[key] = False

    def add_rule(self, rule: dict) -> None:
        if rule.get("enabled") is not False:
            self._rules[rule["id"]] = rule
        else:
            self._rules.pop(rule["id"], None)

    @property
    def rules(self) -> dict[str, dict]:
        return dict(self._rules)

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    def latest_strategy_results(self) -> dict[str, dict]:
        """返回本轮 evaluate() 产出的策略选股结果 (strategy_id → {rows, total, as_of})。

        供策略页实时回显复用: /api/screener/cached 端点直接读取此内存结果,
        避免对被监控的策略重跑第二遍。无 type=strategy 规则时返回空 dict。
        """
        return self._latest_strategy_results

    def consume_strategy_result_updates(self) -> bool:
        """返回并清除本轮成功写入的股票策略实时结果标记。"""
        updated = bool(self._latest_strategy_result_ids)
        self._latest_strategy_result_ids.clear()
        return updated

    def has_rule_type(self, rtype: str) -> bool:
        """是否存在指定类型的 (已启用) 规则。供 quote_service 判断是否需要注入特殊数据。"""
        if not self._rules:
            return False
        # list() 快照: API 线程可能并发增删规则, 直接迭代 dict 会抛 RuntimeError
        return any(
            r.get("enabled", True) and r.get("type") == rtype
            for r in list(self._rules.values())
        )

    def intraday_signal_symbols(self, asset_type: str) -> set[str]:
        """返回启用的分时信号规则所需标的并集。"""
        symbols: set[str] = set()
        for rule in list(self._rules.values()):
            if (
                rule.get("enabled", True)
                and rule.get("asset_type", "stock") == asset_type
                and rule.get("scope") == "symbols"
                and uses_intraday_signals(rule)
            ):
                symbols.update(str(symbol) for symbol in rule.get("symbols", []) if symbol)
        return symbols

    # ── 评估 ───────────────────────────────────────────
    def has_asset_rules(self, asset_type: str) -> bool:
        """是否存在指定资产类型的 (已启用) 规则。供 quote_service 判断是否需要 ETF 评估轮。"""
        if not self._rules:
            return False
        return any(
            r.get("enabled", True) and r.get("asset_type", "stock") == asset_type
            for r in list(self._rules.values())
        )

    def evaluate(self, df: pl.DataFrame, asset_type: str = "stock",
                 reset_strategy_results: bool = True) -> list[dict]:
        """行情更新后评估规则。

        按 asset_type 只评估匹配资产类型的规则; ETF 规则应传 ETF enriched 快照。
        股票/ETF 分两轮评估时, 仅股票轮重置 _latest_strategy_results (它供股票策略页
        /cached 回显; ETF 策略页走实时单跑, 不依赖它)。

        Args:
            df: 实时 enriched 数据 (含 signal_/csg_/指标列)
            asset_type: 只评估该资产类型的规则 (默认 stock, 向后兼容)
            reset_strategy_results: 是否重置策略结果缓存 (多轮评估时仅首轮 True)
        Returns:
            触发的 AlertEvent dict 列表 (含 ts/rule_id/source/type/symbol/...)
        """
        if not self._rules or df.is_empty():
            return []

        now = time.time()
        events: list[dict] = []
        # 原子化: reset 轮 (股票轮) 时先把本轮结果写到临时容器, 算完后一次性替换
        # _latest_strategy_results。这样 /cached 并发读取永远拿到完整结果,
        # 不会在「清空 → 逐个回填」窗口里读到空中间态 (曾导致策略页闪烁)。
        # 非 reset 轮 (ETF 轮) 继续往同一临时容器追加 (_match_strategy 仅写 stock, 实际不追加)。
        if reset_strategy_results:
            self._building_strategy_results = {}
            self._latest_strategy_result_ids.clear()

        matrix_rules: list[dict] = []
        params_map: dict[str, dict] = {}
        overrides_map: dict[str, dict] = {}
        if self._strategy_engine is not None:
            for rule in list(self._rules.values()):
                if (
                    not rule.get("enabled", True)
                    or rule.get("type") != "strategy"
                    or rule.get("asset_type", "stock") != asset_type
                ):
                    continue
                sid = rule.get("strategy_id")
                if not sid:
                    continue
                try:
                    strategy = self._strategy_engine.get(sid)
                except Exception:
                    continue
                if getattr(strategy, "execution_backend", "polars_expr") != "matrix_native":
                    continue
                overrides = {}
                if self._data_dir:
                    overrides = _strategy_config.load_override(self._data_dir, sid)
                matrix_rules.append(rule)
                overrides_map[sid] = overrides
                params_map[sid] = dict(overrides.get("params") or {})
        if matrix_rules:
            try:
                history_loader = self._history_loader_for(matrix_rules[0])
                if history_loader is None:
                    raise ValueError("matrix strategy monitor requires history loader")
                matrix_ids = [str(rule["strategy_id"]) for rule in matrix_rules]
                history_bars = self._strategy_engine.required_history_bars(
                    matrix_ids,
                    params_map=params_map,
                    overrides_map=overrides_map,
                )
                from app.strategy.engine import StrategyDataContext
                context = StrategyDataContext(
                    asset_type=asset_type,
                    timeframe="1d",
                    as_of=cn_today(),
                    current=df,
                    cache_key=f"monitor:{asset_type}",
                )
                try:
                    snapshot = self._strategy_engine.prepare_realtime_matrix(
                        context,
                        matrix_ids,
                        params_map=params_map,
                        overrides_map=overrides_map,
                    )
                except ValueError as exc:
                    if "requires history data" not in str(exc):
                        raise
                    history = history_loader(cn_today(), history_bars + HISTORY_WARMUP_SESSIONS)
                    snapshot = self._strategy_engine.prepare_realtime_matrix(
                        StrategyDataContext(
                            asset_type=asset_type,
                            timeframe="1d",
                            as_of=cn_today(),
                            current=df,
                            history=history,
                            cache_key=f"monitor:{asset_type}",
                        ),
                        matrix_ids,
                        params_map=params_map,
                        overrides_map=overrides_map,
                    )
                self._active_matrix_snapshots[asset_type] = snapshot
            except Exception as e:
                self._active_matrix_snapshots.pop(asset_type, None)
                logger.warning("%s 矩阵策略实时缓存准备失败: %s", asset_type, e)

        # list() 快照: 本方法跑在行情轮询线程, API 线程同时 add/remove 规则
        # 会触发 "dictionary changed size during iteration", 整轮告警丢失
        for rule_id, rule in list(self._rules.items()):
            if rule.get("asset_type", "stock") != asset_type:
                continue
            if rule.get("type") in ("sector", "abnormal"):
                continue
            try:
                events.extend(self._evaluate_rule(df, rule, now))
            except Exception as e:
                logger.warning("规则评估失败 %s: %s", rule_id, e)

        # 一次性提交本轮结果 (原子替换): /cached 读方要么拿到上一轮完整结果,
        # 要么拿到本轮完整结果, 不会读到空中间态。
        self._latest_strategy_results = self._building_strategy_results
        self._active_matrix_snapshots.pop(asset_type, None)

        return events

    def evaluate_sectors(
        self,
        stock_df: pl.DataFrame,
        index_df: pl.DataFrame,
        *,
        now: float | None = None,
    ) -> list[dict]:
        """按板块聚合快照评估 type=sector 规则。"""
        if self._sector_monitor_service is None:
            return []
        rules = [
            rule for rule in list(self._rules.values())
            if rule.get("enabled", True) and rule.get("type") == "sector"
        ]
        if not rules:
            return []

        targets_by_key: dict[str, dict] = {}
        windows: set[int] = set()
        for rule in rules:
            for target in rule.get("sector_targets", []):
                if target.get("key"):
                    targets_by_key[str(target["key"])] = target
            if rule.get("sector_trigger") == "momentum":
                windows.add(int(rule.get("window_minutes", 5)))

        timestamp = time.time() if now is None else now
        snapshots = self._sector_monitor_service.build_snapshots(
            stock_df,
            index_df,
            list(targets_by_key.values()),
            windows,
            now=timestamp,
        )
        events: list[dict] = []
        for rule in rules:
            try:
                events.extend(self._evaluate_sector_rule(rule, snapshots, timestamp))
            except Exception as exc:  # noqa: BLE001
                logger.warning("板块规则评估失败 %s: %s", rule.get("id"), exc)
        return events

    def _evaluate_sector_rule(self, rule: dict, snapshots: dict[str, dict], now: float) -> list[dict]:
        events: list[dict] = []
        direction = rule.get("direction", "up")
        trigger = rule.get("sector_trigger", "change_pct")
        threshold = float(rule.get("threshold_pct", 1.0)) / 100
        window = int(rule.get("window_minutes", 5))

        for target in rule.get("sector_targets", []):
            target_key = str(target.get("key") or "")
            snapshot = snapshots.get(target_key)
            if not snapshot or not snapshot.get("valid"):
                continue
            value = (
                snapshot.get("change_pct")
                if trigger == "change_pct"
                else snapshot.get("window_changes", {}).get(window)
            )
            condition = value is not None and (
                value >= threshold if direction == "up" else value <= -threshold
            )
            state_key = (rule["id"], target_key)
            previous = self._sector_condition_state.get(state_key)
            self._sector_condition_state[state_key] = condition
            if previous is None or previous or not condition:
                continue

            event_type = f"sector_{trigger}_{direction}"
            cooldown_key = (rule["id"], target_key, event_type)
            last = self._last_fire.get(cooldown_key)
            cooldown = int(rule.get("cooldown_seconds", 3600))
            if last is not None and now - last < cooldown:
                continue
            self._last_fire[cooldown_key] = now
            message = rule.get("message", "") or self._sector_message(
                snapshot, trigger, direction, threshold, window, value,
            )
            event = {
                "ts": int(now * 1000),
                "rule_id": rule["id"],
                "rule_name": rule.get("name", ""),
                "strategy_id": None,
                "source": "sector",
                "type": event_type,
                "symbol": snapshot.get("symbol") if snapshot.get("kind") == "index" else "",
                "name": snapshot.get("name"),
                "message": message,
                "price": snapshot.get("price"),
                "change_pct": snapshot.get("change_pct"),
                "window_change_pct": value if trigger == "momentum" else None,
                "signals": [],
                "severity": rule.get("severity", "info"),
                "conditions": [],
                "logic": "and",
                "sector_kind": snapshot.get("kind"),
                "sector_key": target_key,
                "sector_name": snapshot.get("name"),
                "sector_source_field": snapshot.get("source_field"),
                "sector_value": snapshot.get("value"),
                "sector_level": snapshot.get("level"),
                "coverage_ratio": snapshot.get("coverage_ratio"),
                "valid_count": snapshot.get("valid_count"),
                "total_count": snapshot.get("total_count"),
                "up_count": snapshot.get("up_count"),
                "down_count": snapshot.get("down_count"),
                "leader": snapshot.get("leader"),
            }
            events.append(event)
            if self._alert_handler:
                try:
                    self._alert_handler(event)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("alert handler failed: %s", exc)
        return events

    @staticmethod
    def _sector_message(
        snapshot: dict,
        trigger: str,
        direction: str,
        threshold: float,
        window: int,
        value: float | None,
    ) -> str:
        kind_label = {
            "index": "指數", "concept": "概念", "industry": "行業",
        }.get(snapshot.get("kind"), "板塊")
        current = float(snapshot.get("change_pct") or 0)
        if trigger == "momentum":
            action = "快速拉升" if direction == "up" else "快速下跌"
            head = (
                f"{kind_label}「{snapshot.get('name')}」{window}分鐘{action} "
                f"{float(value or 0) * 100:+.2f}%"
            )
        else:
            action = "漲幅上穿" if direction == "up" else "跌幅下穿"
            head = f"{kind_label}「{snapshot.get('name')}」{action} {threshold * 100:.2f}%"
        parts = [head, f"當前 {current * 100:+.2f}%"]
        if snapshot.get("kind") != "index":
            parts.append(f"上漲 {snapshot.get('up_count', 0)}/{snapshot.get('valid_count', 0)}")
            parts.append(f"覆蓋 {float(snapshot.get('coverage_ratio') or 0) * 100:.0f}%")
            leader = snapshot.get("leader") or {}
            if leader.get("name") or leader.get("symbol"):
                parts.append(
                    f"領漲 {leader.get('name') or leader.get('symbol')} "
                    f"{float(leader.get('change_pct') or 0) * 100:+.2f}%"
                )
        return "｜".join(parts)

    def _evaluate_rule(self, df: pl.DataFrame, rule: dict, now: float) -> list[dict]:
        """评估单条规则,返回触发的 events。"""
        # 1. 按 scope 过滤作用域
        scoped = self._apply_scope(df, rule)
        if scoped.is_empty():
            return []

        # 2. 根据 type 构建命中集
        #    元组格式: (event_type, symbol, name, price, pct, signals)
        hit_rows: list[tuple[str, str, Any, Any, Any, list[str]]] = []

        rtype = rule.get("type", "signal")
        if rtype == "strategy":
            # 策略类型: 跑策略选股, 同时产出所选的信号和结果池变更事件
            hit_rows = self._match_strategy(scoped, rule, now)
        else:
            # signal / price / market: 通用条件匹配
            if rule.get("trigger_mode", "edge") == "edge":
                matched = self._match_conditions_edge(rule["id"], rtype, scoped, rule)
            else:
                matched = self._match_conditions(scoped, rule)
            for sym, name, price, pct, hit_sigs in matched:
                hit_rows.append((rtype, sym, name, price, pct, hit_sigs))

        if not hit_rows:
            return []

        # 3. cooldown 去重 + 生成 events
        cooldown = rule.get("cooldown_seconds", 3600)
        severity = rule.get("severity", "info")
        source = rtype

        events: list[dict] = []
        for ev_type, sym, name, price, pct, hit_sigs in hit_rows:
            is_batch = sym == "_batch"
            # Strategy rows were already cooled down per symbol in _match_strategy.
            if rtype != "strategy":
                key = (rule["id"], sym, ev_type)
                last = self._last_fire.get(key)
                if last is not None and (now - last) < cooldown:
                    continue
                self._last_fire[key] = now

            # 批量事件: name 存放预构建的消息文本
            if is_batch:
                resolved_name = ""
                message = name  # name 字段即批量消息
            else:
                resolved_name = name if name else self._name_map.get(sym)
                message = rule.get("message", "") or self._default_message(
                    rule, ev_type=ev_type, name=resolved_name,
                    pct=pct, price=price,
                    conditions=list(rule.get("conditions", [])) if rule.get("type") != "strategy" else None,
                )

            ev = {
                "ts": int(now * 1000),
                "rule_id": rule["id"],
                "rule_name": rule.get("name", ""),
                "strategy_id": rule.get("strategy_id") if rtype == "strategy" else None,
                "source": source,
                "type": ev_type,
                "symbol": "" if is_batch else sym,
                "name": resolved_name,
                "message": message,
                "price": price,
                "change_pct": pct,
                "signals": hit_sigs,
                "severity": severity,
                # 触发条件快照 (signal/price/market 类型): 用于触发记录展示
                # 「命中了什么条件」。strategy 类型靠策略选股池 diff, 不写条件。
                "conditions": list(rule.get("conditions", [])) if rtype != "strategy" else [],
                "logic": rule.get("logic", "and") if rtype != "strategy" else "and",
            }
            events.append(ev)
            if self._alert_handler:
                try:
                    self._alert_handler(ev)
                except Exception as e:
                    logger.warning("alert handler failed: %s", e)

        return events

    @staticmethod
    def _apply_scope(df: pl.DataFrame, rule: dict) -> pl.DataFrame:
        """按 scope 过滤 DataFrame。"""
        scope = rule.get("scope", "symbols")
        if scope == "all":
            return df
        if scope == "symbols":
            syms = rule.get("symbols", [])
            if not syms:
                return df.head(0)
            return df.filter(pl.col("symbol").is_in(syms))
        if scope == "watchlist_group":
            # 动态绑定自选分组: 每轮评估按分组当前成员过滤 (带版本号缓存)。
            # 分组已删除/暂时为空 → fail-closed 返回空, 绝不退化为全市场。
            members = _group_members_or_none(rule)
            if not members:
                return df.head(0)
            return df.filter(pl.col("symbol").is_in(list(members)))
        if scope == "sector":
            # sector 过滤需 df 含板块列 (后续接入 ext_data JOIN)。在 JOIN 落地前
            # fail-closed 返回空 —— 绝不退化为「全市场」误触发 (旧行为 return df 会让
            # 一条板块规则对全市场每只命中都告警)。新建 sector 规则已在 validate 拦截,
            # 此处兜底任何历史遗留的 sector 规则。
            logger.warning("scope=sector 规则 %s 暂不支持(板块 JOIN 未实现), 本轮跳过",
                           rule.get("id"))
            return df.head(0)
        return df

    def _match_strategy(
        self, df: pl.DataFrame, rule: dict, now: float,
    ) -> list[tuple[str, str, Any, Any, Any, list[str]]]:
        """策略类型评估: 一次执行同时产出交易信号和结果池变更事件。

        返回 [(event_type, symbol, name, price, pct, signals)]
        event_type: buy_signal | sell_signal | pool_entry | pool_exit
        同类事件超过 5 只时合并为一条批量事件 (symbol="_batch")
        Cooldown is keyed per (rule_id, symbol, event_type) and applied before merging into a batch.
        """
        if self._strategy_engine is None:
            return []
        sid = rule.get("strategy_id")
        if not sid:
            return []
        at = rule.get("asset_type", "stock")
        pool_key = (str(rule.get("id", sid)), sid, at)
        try:
            s = self._strategy_engine.get(sid)
        except Exception:
            return []
        if s is None:
            return []

        # 运行策略选股: 复用当前 enriched DataFrame 跳过数据加载
        overrides = {}
        if self._data_dir:
            try:
                overrides = _strategy_config.load_override(self._data_dir, sid)
            except Exception:
                pass

        # 声明 filter_history 的策略 (如反包) 需要多日历史窗口才能判定形态。
        # 旧实现因"实时监控不支持 history loader"直接跳过 → 反包等策略盘中永不触发。
        # 现接入 history_loader, 拼历史窗口 + 今日实时行情, 经 precomputed_history 喂给引擎。
        # loader 为 None (未装配) 时退回跳过, 保持旧行为, 不破坏无历史场景。
        from app.services.screener import taiwan_symbol_asset_types
        from app.strategy.engine import StrategyDataContext

        symbol_asset_types = taiwan_symbol_asset_types(at, df)
        current_context = StrategyDataContext(
            asset_type=at,
            timeframe="1d",
            as_of=cn_today(),
            current=df,
            symbol_asset_types=symbol_asset_types,
        )
        if getattr(s, "execution_backend", "polars_expr") == "composite":
            # 叠加策略首版不支持实时监控: 各子策略需独立预热实时矩阵, 热路径成本为 N 倍,
            # 违反"实时热路径不得随历史数据量线性增长"约束。fail-closed 跳过本轮,
            # /cached 端点回退到盘后 strategy_cache.json 的批量结果。
            logger.debug("叠加策略 %s 暂不支持实时监控, 跳过", sid)
            return []
        if getattr(s, "execution_backend", "polars_expr") == "matrix_native":
            matrix = self._active_matrix_snapshots.get(at)
            if matrix is None:
                logger.debug("策略 %s 缺少本轮实时矩阵快照, 跳过", sid)
                return []
            current_context = StrategyDataContext(
                asset_type=at,
                timeframe="1d",
                as_of=cn_today(),
                current=df,
                market=matrix,
                symbol_asset_types=symbol_asset_types,
            )
        required_history_bars = 1
        history_resolver = getattr(self._strategy_engine, "required_history_bars", None)
        if callable(history_resolver):
            required_history_bars = history_resolver(
                [sid],
                overrides_map={sid: overrides},
            )
        if getattr(s, "execution_backend", "polars_expr") not in {"composite", "matrix_native"} and (
            s.filter_history_fn or required_history_bars > 1
        ):
            history_loader = self._history_loader_for(rule)
            if history_loader is None:
                logger.debug("策略 %s 需要历史数据但未注入 history_loader (asset_type=%s), 跳过实时监控",
                             sid, rule.get("asset_type", "stock"))
                return []
            try:
                today = cn_today()
                lookback = max(1, getattr(s, "lookback_days", 1), required_history_bars)
                hist_df = history_loader(today, lookback + HISTORY_WARMUP_SESSIONS)
                if hist_df is None or hist_df.is_empty():
                    logger.debug("策略 %s 历史数据为空, 跳过本轮实时监控", sid)
                    return []
                # 历史窗口可能与今日已落盘数据重叠: 排掉 hist_df 中 date==today 的行,
                # 今日行情始终以实时 df 为准 (盘中逐轮更新, 最接近收盘真相)。
                # 否则 today 行重复会污染 filter_history 的 .over("symbol") 窗口判定。
                if "date" in hist_df.columns:
                    hist_df = hist_df.filter(pl.col("date") != today)
                # 拼接历史窗口 + 今日实时行情 (filter_history 用 .over("symbol") 窗口, 多日天然可用)
                current_context = StrategyDataContext(
                    asset_type=at,
                    timeframe="1d",
                    as_of=today,
                    current=df,
                    history=pl.concat(
                        [hist_df, df], how="diagonal_relaxed"
                    ),
                    symbol_asset_types=symbol_asset_types,
                )
            except Exception as e:
                logger.warning("策略 %s 加载历史窗口失败, 跳过: %s", sid, e)
                return []
        try:
            result = self._strategy_engine.run(
                sid,
                current_context,
                pool=(df["symbol"].cast(pl.Utf8).to_list()
                      if getattr(s, "execution_backend", "polars_expr") == "matrix_native"
                      else None),
                overrides=overrides,
                params=dict(overrides.get("params") or {}),
            )
        except Exception as e:
            logger.warning("策略 %s 选股执行失败: %s", sid, e)
            return []

        # 记录本轮完整选股结果 (供策略页实时回显: /cached 端点直接读取, 不落盘)。
        # 与下面的事件无关, 无论是否产生通知结果都用于策略页实时回显。
        # 策略结果缓存仅用于股票策略页 /cached 回显; ETF 策略页走实时单跑, 不写入。
        # 写到 evaluate 提供的临时容器 (_building_strategy_results), 算完后整体替换,
        # 避免并发读到半填充状态。
        # Only a whole-market rule stands in for the post-close cache; a scoped one would shrink it.
        if at == "stock" and rule.get("scope") == "all":
            try:
                self._building_strategy_results[sid] = {
                    "total": result.total,
                    "as_of": str(cn_today()),
                    "rows": [
                        {k: (None if isinstance(v, float) and not math.isfinite(v) else v)
                         for k, v in row.items()}
                        for row in result.rows
                    ],
                }
                self._latest_strategy_result_ids.add(sid)
            except Exception:  # noqa: BLE001
                pass

        score_min = rule.get("score_min")
        score_max = rule.get("score_max")
        score_filter_enabled = score_min is not None or score_max is not None
        eligible_symbols: set[str] = set()
        if score_filter_enabled:
            for row in result.rows:
                symbol = str(row.get("symbol", ""))
                score = row.get("score", result.scores.get(symbol))
                if isinstance(score, bool) or not isinstance(score, (int, float)):
                    continue
                if not math.isfinite(score):
                    continue
                if score_min is not None and score < score_min:
                    continue
                if score_max is not None and score > score_max:
                    continue
                eligible_symbols.add(symbol)
        else:
            eligible_symbols = {str(row["symbol"]) for row in result.rows}

        current_pool = eligible_symbols
        prev_pool = self._strategy_pools.get(pool_key)
        self._strategy_pools[pool_key] = current_pool

        notify_events = set(rule.get("notify_events") or ("pool_entry", "pool_exit"))
        sname = s.meta.get("name", "") or s.meta.get("id", sid)
        row_map: dict[str, dict] = {r["symbol"]: r for r in result.rows}
        try:
            for row in df.iter_rows(named=True):
                row_map.setdefault(str(row.get("symbol", "")), row)
        except Exception:
            pass

        entry_signal_hits = result.entry_signal_hits
        if score_filter_enabled:
            entry_signal_hits = [
                hit for hit in entry_signal_hits
                if str(hit.get("symbol", "")) in eligible_symbols
            ]
        changes: dict[str, set[str]] = {
            "buy_signal": self._new_strategy_signals(
                pool_key, "buy_signal", result.as_of, entry_signal_hits,
            ),
            "sell_signal": self._new_strategy_signals(
                pool_key, "sell_signal", result.as_of, result.exit_signal_hits,
            ),
            "pool_entry": set() if prev_pool is None else current_pool - prev_pool,
            "pool_exit": set() if prev_pool is None else prev_pool - current_pool,
        }

        results: list[tuple[str, str, Any, Any, Any, list[str]]] = []
        signal_map = {
            "buy_signal": {
                str(hit["symbol"]): list(hit.get("signals") or [])
                for hit in entry_signal_hits
            },
            "sell_signal": {
                str(hit["symbol"]): list(hit.get("signals") or [])
                for hit in result.exit_signal_hits
            },
        }
        action_labels = {
            "buy_signal": "買入訊號",
            "sell_signal": "賣出訊號",
            "pool_entry": "進入選股結果",
            "pool_exit": "移出選股結果",
        }
        cooldown = rule.get("cooldown_seconds", 3600)
        for event_type, symbols in changes.items():
            if event_type not in notify_events or not symbols:
                continue
            symbol_list = []
            for symbol in sorted(symbols):
                key = (rule["id"], symbol, event_type)
                last = self._last_fire.get(key)
                if last is not None and (now - last) < cooldown:
                    continue
                self._last_fire[key] = now
                symbol_list.append(symbol)
            if not symbol_list:
                continue
            if len(symbol_list) > 5:
                names = [
                    str(row_map.get(symbol, {}).get("name") or self._name_map.get(symbol, symbol))
                    for symbol in symbol_list
                ]
                message = (
                    f"策略「{sname}」{action_labels[event_type]} {len(symbol_list)} 檔: "
                    f"{'、'.join(names)}"
                )
                hit_signals = sorted({
                    signal
                    for symbol in symbol_list
                    for signal in signal_map.get(event_type, {}).get(symbol, [])
                })
                results.append((event_type, "_batch", message, None, None, hit_signals))
                continue
            for symbol in symbol_list:
                row = row_map.get(symbol, {})
                name = row.get("name") or self._name_map.get(symbol, symbol)
                results.append((
                    event_type,
                    symbol,
                    name,
                    row.get("close"),
                    row.get("change_pct"),
                    signal_map.get(event_type, {}).get(symbol, []),
                ))

        return results

    def _new_strategy_signals(
        self,
        pool_key: tuple[str, str, str],
        event_type: str,
        as_of: Any,
        hits: list[dict],
    ) -> set[str]:
        rule_id, strategy_id, asset_type = pool_key
        state_key = (rule_id, strategy_id, asset_type, event_type)
        date_key = str(as_of)
        current = {str(hit["symbol"]) for hit in hits}
        previous = self._strategy_signal_state.get(state_key)
        self._strategy_signal_state[state_key] = (date_key, current)

        if previous is None:
            for symbol in current:
                self._strategy_signal_seen[(*state_key, symbol)] = date_key
            return set()

        previous_date, previous_symbols = previous
        candidates = current if previous_date != date_key else current - previous_symbols
        fresh = {
            symbol
            for symbol in candidates
            if self._strategy_signal_seen.get((*state_key, symbol)) != date_key
        }
        for symbol in fresh:
            self._strategy_signal_seen[(*state_key, symbol)] = date_key
        return fresh

    @staticmethod
    def _match_conditions(
        df: pl.DataFrame, rule: dict,
    ) -> list[tuple[str, Any, Any, Any, list[str]]]:
        """按 conditions + logic 匹配,返回命中行 [(symbol,name,price,pct,signals)]。"""
        conditions = rule.get("conditions", [])
        logic = rule.get("logic", "and")
        if not conditions:
            return []
        hit_df = _build_condition_mask(df, conditions, logic)
        results = []
        for row in hit_df.iter_rows(named=True):
            sym = row.get("symbol", "")
            name = row.get("name")
            price = row.get("close")
            pct = row.get("change_pct")
            # 收集命中的信号列名 (仅 op=truth 且为真的)
            hit_sigs = [
                c["field"] for c in conditions
                if c.get("op") == "truth" and row.get(c["field"])
            ]
            results.append((sym, name, price, pct, hit_sigs))
        return results

    def _match_conditions_edge(
        self, rule_id: str, ev_type: str, df: pl.DataFrame, rule: dict,
    ) -> list[tuple[str, Any, Any, Any, list[str]]]:
        """边缘触发版 _match_conditions: 仅命中状态由「未命中」转为「命中」时才返回该行。

        必须遍历 scope 内的全部行 (命中与未命中), 而不能只看命中列 —
        否则未命中的 symbol 状态永远无法归零, 规则会在触发一次后永久失效。
        """
        conditions = rule.get("conditions", [])
        logic = rule.get("logic", "and")
        if not conditions:
            return []
        expr = _condition_mask_expr(set(df.columns), conditions, logic)
        if expr is None:
            return []
        annotated = df.with_columns(expr.alias("_cond_hit"))
        results: list[tuple[str, Any, Any, Any, list[str]]] = []
        for row in annotated.iter_rows(named=True):
            sym = row.get("symbol", "")
            hit = bool(row.get("_cond_hit"))
            key = (rule_id, sym, ev_type)
            armed = self._condition_state.get(key, True)
            fire = False
            if hit:
                if armed:
                    fire = True
                    armed = False
            elif not armed and _rearm_crossed(row, conditions):
                armed = True
            self._condition_state[key] = armed
            if not fire:
                continue
            hit_sigs = [
                c["field"] for c in conditions
                if c.get("op") == "truth" and row.get(c["field"])
            ]
            results.append((sym, row.get("name"), row.get("close"), row.get("change_pct"), hit_sigs))
        return results

    def _default_message(self, rule: dict, ev_type: str = "",
                          name: str = "", pct: Any = None, price: Any = None,
                          conditions: list[dict] | None = None) -> str:
        """生成默认 message。

        - strategy: 按变更方向生成 (进入/移出 + 涨跌幅)
        - signal/price/market: 条件摘要 + 现价 + 涨跌幅 (避免笼统的「信号触发」)
        """
        rtype = rule.get("type", "signal")
        if rtype == "strategy":
            # 从 StrategyEngine 取策略名; 失败则退化为 rule_name 里截取的部分
            sname = ""
            sid = rule.get("strategy_id")
            if sid and self._strategy_engine is not None:
                try:
                    s = self._strategy_engine.get(sid)
                    sname = s.meta.get("name", "") or s.meta.get("id", "")
                except Exception:  # noqa: BLE001
                    sname = ""
            if not sname:
                rn = rule.get("name", "")
                sname = rn.split(" · ", 1)[1] if " · " in rn else (rn or "策略")

            action = {
                "buy_signal": "買入訊號",
                "sell_signal": "賣出訊號",
                "pool_entry": "進入選股結果",
                "pool_exit": "移出選股結果",
            }.get(ev_type)
            if action:
                pct_text = ""
                if pct is not None:
                    sign = "+" if pct >= 0 else ""
                    pct_text = f" {sign}{pct * 100:.1f}%"
                return f"策略「{sname}」{action} {name}{pct_text}"
            return f"策略「{sname}」事件"

        # signal / price / market: 条件摘要 + 现价 + 涨跌幅
        # 条件摘要: 把 conditions (truth/比较) 拼成可读串, 如 "MA20金叉 且 量比>2"
        cond_text = self._format_conditions_text(rule, conditions)
        price_text = f"現價 {price}" if price is not None else ""
        pct_text = ""
        if pct is not None:
            sign = "+" if pct >= 0 else ""
            pct_text = f"{sign}{pct * 100:.1f}%"
        tail = " · ".join(s for s in (price_text, pct_text) if s)
        if cond_text and tail:
            return f"{cond_text} · {tail}"
        return cond_text or tail or "監控觸發"

    @staticmethod
    def _format_conditions_text(rule: dict, conditions: list[dict] | None) -> str:
        """把 rule.conditions 拼成可读文本 (用于 message / 推送)。

        op=truth: 直接用信号中文名 (如 "MA20金叉")
        op=比较: 字段中文名 + 操作符 + 值 (如 "涨跌幅≥5")
        logic: and → "且", or → "或"
        """
        conds = conditions if conditions is not None else list(rule.get("conditions", []))
        if not conds:
            return ""
        logic_word = "且" if rule.get("logic", "and") == "and" else "或"
        parts: list[str] = []
        for c in conds:
            field = c.get("field", "")
            op = c.get("op", "truth")
            value = c.get("value")
            label = field_label(field) or field
            if op == "truth":
                parts.append(label)
            else:
                parts.append(f"{label}{_OP_WORDS.get(op, op)}{_format_condition_value(field, value)}")
        return f" {logic_word} ".join(parts)
