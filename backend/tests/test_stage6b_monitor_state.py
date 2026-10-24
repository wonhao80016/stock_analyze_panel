"""Monitor engine state: rule edits, strategy batch cooldown, restart baseline and alert text."""
from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.market_time import CN_TZ
from app.services import alert_store
from app.strategy import monitor_rules
from app.strategy.engine import StrategyResult
from app.strategy.monitor import MonitorRuleEngine


def _price_rule(**overrides) -> dict:
    rule = {
        "id": "r_price", "name": "r_price", "type": "price", "scope": "symbols",
        "symbols": ["A"], "logic": "and",
        "conditions": [{"field": "close", "op": ">", "value": 10}],
        "cooldown_seconds": 3600,
    }
    rule.update(overrides)
    return monitor_rules.normalize(rule)


def _df(close: float) -> pl.DataFrame:
    return pl.DataFrame({"symbol": ["A"], "close": [close]})


# ── B038: editing conditions resets edge and cooldown state ──

@pytest.mark.parametrize("trigger_mode", ["edge", "level"])
def test_edited_conditions_fire_immediately(trigger_mode):
    engine = MonitorRuleEngine()
    engine.set_rules([_price_rule(trigger_mode=trigger_mode)])
    assert len(engine.evaluate(_df(15.0))) == 1

    engine.set_rules([_price_rule(
        trigger_mode=trigger_mode,
        conditions=[{"field": "close", "op": "<", "value": 20}],
    )])

    assert len(engine.evaluate(_df(15.0))) == 1


@pytest.mark.parametrize("overrides", [
    {"logic": "or"},
    {"trigger_mode": "level"},
    {"conditions": [{"field": "close", "op": ">", "value": 10, "rearm_value": 5}]},
])
def test_other_condition_settings_reset_state(overrides):
    engine = MonitorRuleEngine()
    engine.set_rules([_price_rule()])
    assert len(engine.evaluate(_df(15.0))) == 1

    engine.set_rules([_price_rule(**overrides)])

    assert len(engine.evaluate(_df(15.0))) == 1


def test_unchanged_conditions_keep_state():
    engine = MonitorRuleEngine()
    engine.set_rules([_price_rule()])
    assert len(engine.evaluate(_df(15.0))) == 1

    engine.set_rules([_price_rule(message="renamed only")])

    assert engine.evaluate(_df(15.0)) == []


# ── B039: strategy batch events cool down per symbol ──

class _SequenceStrategyEngine:
    def __init__(self, pools):
        self.pools = list(pools)
        self.strategy = SimpleNamespace(
            meta={"id": "demo", "name": "示例策略"},
            execution_backend="polars_expr",
            filter_history_fn=None,
        )

    def get(self, strategy_id):
        return self.strategy

    def run(self, strategy_id, context, **kwargs):
        pool = self.pools.pop(0)
        return StrategyResult(
            as_of=date(2026, 7, 24), strategy_id="demo",
            rows=[{"symbol": s, "close": 10.0, "change_pct": 0.01} for s in pool],
            total=len(pool), scores={}, entry_signal_hits=[], exit_signal_hits=[],
        )


def _strategy_quotes(symbols: list[str]) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": symbols,
        "close": [10.0] * len(symbols),
        "change_pct": [0.01] * len(symbols),
    })


def test_second_batch_of_new_symbols_is_not_lost_to_batch_cooldown():
    first = [f"A{i}" for i in range(6)]
    second = [f"B{i}" for i in range(6)]
    engine = MonitorRuleEngine()
    engine.set_strategy_engine(_SequenceStrategyEngine([[], first, first + second]))
    engine.set_rules([monitor_rules.normalize({
        "id": "r_strategy", "name": "r_strategy", "type": "strategy", "scope": "all",
        "strategy_id": "demo", "notify_events": ["pool_entry"], "cooldown_seconds": 3600,
    })])
    quotes = _strategy_quotes(first + second)

    assert engine.evaluate(quotes) == []
    batch_one = engine.evaluate(quotes)
    batch_two = engine.evaluate(quotes)

    assert len(batch_one) == 1 and batch_one[0]["symbol"] == ""
    assert "A0" in batch_one[0]["message"]
    assert len(batch_two) == 1
    assert "B0" in batch_two[0]["message"]
    assert "A0" not in batch_two[0]["message"]


def test_symbol_in_cooldown_is_dropped_from_a_later_batch():
    engine = MonitorRuleEngine()
    pools = [[], ["A0"], [], ["A0", "B0", "B1", "B2", "B3", "B4"]]
    engine.set_strategy_engine(_SequenceStrategyEngine(pools))
    engine.set_rules([monitor_rules.normalize({
        "id": "r_strategy", "name": "r_strategy", "type": "strategy", "scope": "all",
        "strategy_id": "demo", "notify_events": ["pool_entry"], "cooldown_seconds": 3600,
    })])
    quotes = _strategy_quotes(["A0", "B0", "B1", "B2", "B3", "B4"])

    engine.evaluate(quotes)
    single = engine.evaluate(quotes)
    engine.evaluate(quotes)
    later = engine.evaluate(quotes)

    assert [e["symbol"] for e in single] == ["A0"]
    assert sorted(e["symbol"] for e in later) == ["B0", "B1", "B2", "B3", "B4"]


# ── B171: a restart resumes today's fired state from alerts.jsonl ──

_YESTERDAY = (datetime.now(UTC) - timedelta(days=1)).isoformat()


def _taipei_midnight_ms() -> int:
    now = datetime.now(CN_TZ)
    return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def _record_alert(data_dir, symbol: str, ts_ms: int, rule_id: str = "r_price", ev_type: str = "price") -> None:
    alert_store.append(data_dir, {
        "ts": ts_ms, "rule_id": rule_id, "source": ev_type, "type": ev_type,
        "symbol": symbol, "message": "x",
    })


def _restarted_engine(data_dir, rule: dict) -> MonitorRuleEngine:
    engine = MonitorRuleEngine()
    engine.set_data_dir(data_dir)
    engine.set_rules([rule])
    return engine


def _now_ms() -> int:
    return max(int(time.time() * 1000) - 1000, _taipei_midnight_ms() + 1)


def test_pre_open_restart_fires_on_an_overnight_gap(tmp_path):
    _record_alert(tmp_path, "A", _taipei_midnight_ms() - 3_600_000)
    rule = _price_rule(created_at=_YESTERDAY, conditions=[{"field": "close", "op": ">=", "value": 600}])
    engine = _restarted_engine(tmp_path, rule)

    events = engine.evaluate(_df(610.0))

    assert [e["symbol"] for e in events] == ["A"]


def test_intraday_restart_does_not_refire_what_already_fired_today(tmp_path):
    _record_alert(tmp_path, "A", _now_ms())
    engine = _restarted_engine(tmp_path, _price_rule(created_at=_YESTERDAY, cooldown_seconds=0))

    assert engine.evaluate(_df(15.0)) == []
    assert engine.evaluate(_df(5.0)) == []
    assert len(engine.evaluate(_df(15.0))) == 1


def test_symbol_missing_from_the_first_round_keeps_todays_fired_state(tmp_path):
    _record_alert(tmp_path, "B", _now_ms())
    engine = _restarted_engine(tmp_path, _price_rule(created_at=_YESTERDAY, symbols=["A", "B"]))

    first = engine.evaluate(_df(15.0))
    second = engine.evaluate(pl.DataFrame({"symbol": ["A", "B"], "close": [15.0, 15.0]}))

    assert [e["symbol"] for e in first] == ["A"]
    assert second == []


@pytest.mark.parametrize(("minutes_ago", "fires"), [(10, False), (120, True)])
def test_level_mode_resumes_cooldown_from_todays_last_fire(tmp_path, minutes_ago, fires):
    ts = int(time.time() * 1000) - minutes_ago * 60_000
    if ts < _taipei_midnight_ms():
        pytest.skip("too close to Taipei midnight for this offset")
    _record_alert(tmp_path, "A", ts)
    engine = _restarted_engine(tmp_path, _price_rule(created_at=_YESTERDAY, trigger_mode="level"))

    assert len(engine.evaluate(_df(15.0))) == (1 if fires else 0)


def test_alerts_of_other_rules_do_not_suppress(tmp_path):
    _record_alert(tmp_path, "A", _now_ms(), rule_id="r_other")
    engine = _restarted_engine(tmp_path, _price_rule(created_at=_YESTERDAY))

    assert len(engine.evaluate(_df(15.0))) == 1


def test_rule_created_after_start_fires_on_first_round():
    engine = MonitorRuleEngine()
    engine.set_rules([])
    engine.set_rules([_price_rule()])

    assert len(engine.evaluate(_df(15.0))) == 1


# ── B172: default alert text uses percent and Chinese operators ──

def test_condition_text_converts_decimal_fields_and_operators():
    text = MonitorRuleEngine._format_conditions_text(
        {"logic": "and"},
        [
            {"field": "change_pct", "op": ">=", "value": 0.03},
            {"field": "distance_to_upper_limit", "op": "<=", "value": 0.015},
            {"field": "close", "op": "<", "value": 100},
        ],
    )

    assert text == "漲跌幅大於等於3% 且 距漲停距離小於等於1.5% 且 收盤價小於100"


@pytest.mark.parametrize(("op", "word"), [
    (">", "大於"), (">=", "大於等於"), ("<", "小於"),
    ("<=", "小於等於"), ("==", "等於"), ("!=", "不等於"),
])
def test_every_stored_operator_has_a_chinese_word(op, word):
    text = MonitorRuleEngine._format_conditions_text(
        {"logic": "and"}, [{"field": "vol_ratio_5d", "op": op, "value": 2}],
    )
    assert text.endswith(f"{word}2")
    assert op not in text


def _record_snapshot_alert(data_dir, conditions: list[dict], logic: str = "and") -> None:
    alert_store.append(data_dir, {
        "ts": _now_ms(), "rule_id": "r_price", "source": "price", "type": "price",
        "symbol": "A", "message": "x", "conditions": conditions, "logic": logic,
    })


def test_alert_of_an_edited_condition_does_not_suppress_the_new_one(tmp_path):
    _record_snapshot_alert(tmp_path, [{"field": "close", "op": ">", "value": 10}])
    edited = _price_rule(created_at=_YESTERDAY, conditions=[{"field": "close", "op": "<", "value": 20}])
    engine = _restarted_engine(tmp_path, edited)

    assert len(engine.evaluate(_df(15.0))) == 1


def test_alert_with_the_same_condition_snapshot_still_suppresses(tmp_path):
    _record_snapshot_alert(tmp_path, [{"field": "close", "op": ">", "value": 10}])
    engine = _restarted_engine(tmp_path, _price_rule(created_at=_YESTERDAY))

    assert engine.evaluate(_df(15.0)) == []


def test_alert_with_a_different_logic_snapshot_does_not_suppress(tmp_path):
    _record_snapshot_alert(tmp_path, [{"field": "close", "op": ">", "value": 10}], logic="or")
    engine = _restarted_engine(tmp_path, _price_rule(created_at=_YESTERDAY))

    assert len(engine.evaluate(_df(15.0))) == 1
