"""邊緣觸發 + 遲滯防抖 — MonitorRuleEngine 條件路徑 (type=signal/price/market)。

覆蓋: 條件持續為真只發一次、轉假再轉真重發、多 symbol 狀態互不干擾、
trigger_mode=level 維持舊的 cooldown 重播行為、per-condition rearm_value 遲滯、
severity 相容映射、規則刪除/更新後狀態清理、strategy 路徑不受影響回歸。
"""
from __future__ import annotations

import polars as pl
import pytest

from app.strategy import monitor_rules
from app.strategy.monitor import MonitorRuleEngine


def _price_rule(rid: str = "r_price", symbols: list[str] | None = None, **overrides) -> dict:
    rule = {
        "id": rid,
        "name": rid,
        "type": "price",
        "scope": "symbols",
        "symbols": symbols if symbols is not None else ["A"],
        "logic": "and",
        "conditions": [{"field": "close", "op": ">", "value": 10}],
        "cooldown_seconds": 0,
    }
    rule.update(overrides)
    return monitor_rules.normalize(rule)


def _df(rows: dict[str, float]) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": list(rows.keys()),
        "close": list(rows.values()),
    })


# ── 核心: 邊緣觸發去重 ────────────────────────────────────

def test_sustained_condition_fires_only_once():
    engine = MonitorRuleEngine()
    engine.set_rules([_price_rule()])

    r1 = engine.evaluate(_df({"A": 12.0}))
    r2 = engine.evaluate(_df({"A": 13.0}))
    r3 = engine.evaluate(_df({"A": 14.0}))

    assert len(r1) == 1
    assert r2 == []
    assert r3 == []


def test_condition_false_then_true_fires_second_event():
    """R4 防呆: 未命中的 symbol 狀態必須歸零, 否則規則觸發一次後永久失效。"""
    engine = MonitorRuleEngine()
    engine.set_rules([_price_rule()])

    r1 = engine.evaluate(_df({"A": 12.0}))
    r2 = engine.evaluate(_df({"A": 5.0}))   # 轉假
    r3 = engine.evaluate(_df({"A": 12.0}))  # 轉真

    assert len(r1) == 1
    assert r2 == []
    assert len(r3) == 1


def test_multiple_symbols_track_edge_state_independently():
    engine = MonitorRuleEngine()
    engine.set_rules([_price_rule(symbols=["A", "B"])])

    # A 持续为真; B 真→假→真
    r1 = engine.evaluate(_df({"A": 12.0, "B": 12.0}))
    r2 = engine.evaluate(_df({"A": 13.0, "B": 5.0}))
    r3 = engine.evaluate(_df({"A": 14.0, "B": 12.0}))

    assert {e["symbol"] for e in r1} == {"A", "B"}
    assert r2 == []
    assert {e["symbol"] for e in r3} == {"B"}


def test_trigger_mode_level_keeps_legacy_cooldown_replay():
    engine = MonitorRuleEngine()
    engine.set_rules([_price_rule(trigger_mode="level")])

    r1 = engine.evaluate(_df({"A": 12.0}))
    r2 = engine.evaluate(_df({"A": 13.0}))
    r3 = engine.evaluate(_df({"A": 14.0}))

    assert len(r1) == 1
    assert len(r2) == 1
    assert len(r3) == 1


# ── rearm_value 遲滯 ──────────────────────────────────────

def test_rearm_value_blocks_refire_until_crossed():
    rule = _price_rule(conditions=[{"field": "close", "op": ">", "value": 10, "rearm_value": 5}])
    engine = MonitorRuleEngine()
    engine.set_rules([rule])

    fire1 = engine.evaluate(_df({"A": 12.0}))       # 命中 → 发第1次
    dip = engine.evaluate(_df({"A": 8.0}))           # 不命中, 但未穿越 rearm(5)
    refire_blocked = engine.evaluate(_df({"A": 12.0}))  # 命中, 尚未 rearm → 不重发
    crossed = engine.evaluate(_df({"A": 3.0}))       # 穿越 rearm(<=5)
    fire2 = engine.evaluate(_df({"A": 12.0}))        # 命中 → 发第2次

    assert len(fire1) == 1
    assert dip == []
    assert refire_blocked == []
    assert crossed == []
    assert len(fire2) == 1


# ── severity 相容映射 ─────────────────────────────────────

def test_normalize_maps_legacy_warning_severity_to_warn():
    rule = monitor_rules.normalize({
        "id": "r_sev", "type": "price", "scope": "symbols", "symbols": ["A"],
        "conditions": [{"field": "close", "op": ">", "value": 1}],
        "severity": "warning",
    })
    assert rule["severity"] == "warn"


def test_normalize_defaults_trigger_mode_and_severity():
    rule = monitor_rules.normalize({
        "id": "r_def", "type": "price", "scope": "symbols", "symbols": ["A"],
        "conditions": [{"field": "close", "op": ">", "value": 1}],
    })
    assert rule["trigger_mode"] == "edge"
    assert rule["severity"] == "info"


def test_validate_rejects_invalid_trigger_mode():
    rule = _price_rule(trigger_mode="bogus")
    with pytest.raises(ValueError, match="trigger_mode"):
        monitor_rules.validate(rule)


def test_validate_rejects_rearm_value_on_truth_condition():
    rule = monitor_rules.normalize({
        "id": "r_truth", "name": "r_truth", "type": "signal", "scope": "symbols", "symbols": ["A"],
        "conditions": [{"field": "signal_limit_up", "op": "truth", "rearm_value": 1.0}],
    })
    with pytest.raises(ValueError, match="rearm_value"):
        monitor_rules.validate(rule)


def test_validate_rejects_non_numeric_rearm_value():
    rule = monitor_rules.normalize({
        "id": "r_bad_rearm", "name": "r_bad_rearm", "type": "price", "scope": "symbols", "symbols": ["A"],
        "conditions": [{"field": "close", "op": ">", "value": 10, "rearm_value": "x"}],
    })
    with pytest.raises(ValueError, match="rearm_value"):
        monitor_rules.validate(rule)


# ── 狀態清理: 避免記憶體洩漏與跨規則污染 ────────────────────

def test_remove_rule_clears_condition_state():
    engine = MonitorRuleEngine()
    engine.set_rules([_price_rule()])
    engine.evaluate(_df({"A": 12.0}))
    assert any(key[0] == "r_price" for key in engine._condition_state)

    engine.remove_rule("r_price")
    assert not any(key[0] == "r_price" for key in engine._condition_state)

    # 重新装载同一条规则: 状态已清空, 相同的持续为真条件应重新触发一次
    engine.set_rules([_price_rule()])
    refired = engine.evaluate(_df({"A": 12.0}))
    assert len(refired) == 1


def test_set_rules_edit_resets_condition_state():
    engine = MonitorRuleEngine()
    rule = _price_rule()
    engine.set_rules([rule])
    first = engine.evaluate(_df({"A": 12.0}))
    assert len(first) == 1

    # 语义变更 (symbols 集合改变) 触发状态重置, 即使条件持续为真也应重新触发
    edited = _price_rule(symbols=["A", "B"])
    engine.set_rules([edited])
    second = engine.evaluate(_df({"A": 12.0, "B": 1.0}))
    assert any(e["symbol"] == "A" for e in second)


def test_clear_wipes_condition_state():
    engine = MonitorRuleEngine()
    engine.set_rules([_price_rule()])
    engine.evaluate(_df({"A": 12.0}))
    assert engine._condition_state
    engine.clear()
    assert engine._condition_state == {}


# ── 回归: strategy 路径不套边缘触发, 行为不变 ────────

def test_strategy_rule_pool_diff_dedup_unaffected_and_no_condition_state():
    from datetime import date
    from types import SimpleNamespace

    from app.strategy.engine import StrategyResult

    class _SequenceStrategyEngine:
        def __init__(self, results):
            self.results = list(results)
            self.strategy = SimpleNamespace(
                meta={"id": "demo", "name": "示例策略"},
                execution_backend="polars_expr",
                filter_history_fn=None,
            )

        def get(self, strategy_id):
            return self.strategy

        def run(self, strategy_id, context, **kwargs):
            return self.results.pop(0)

    day = date(2026, 7, 24)

    def _result(pool):
        return StrategyResult(
            as_of=day, strategy_id="demo",
            rows=[{"symbol": s, "close": 10.0, "change_pct": 0.01} for s in pool],
            total=len(pool), scores={},
            entry_signal_hits=[{"symbol": s, "signals": ["signal_buy"]} for s in pool],
            exit_signal_hits=[],
        )

    engine = MonitorRuleEngine()
    engine.set_strategy_engine(_SequenceStrategyEngine([
        _result(("A",)), _result(("A", "B")), _result(("A", "B")),
    ]))
    engine.set_rules([monitor_rules.normalize({
        "id": "r_strategy", "type": "strategy", "scope": "all",
        "strategy_id": "demo", "notify_events": ["buy_signal"],
        "cooldown_seconds": 3600,
    })])

    quotes = pl.DataFrame({"symbol": ["A", "B"], "close": [10.0, 20.0], "change_pct": [0.01, 0.01]})
    r1 = engine.evaluate(quotes)
    r2 = engine.evaluate(quotes)
    r3 = engine.evaluate(quotes)

    # 首轮只建立基线 (既有行为); 第二轮 B 新进入池才发信号; 第三轮池不变不再重复
    assert r1 == []
    assert [e["symbol"] for e in r2] == ["B"]
    assert r3 == []
    assert engine._condition_state == {}
