"""回归测试: 本轮修复的几处高风险行为(sector fail-closed 等)。

均为纯逻辑, 不触网, 不依赖真实数据源。
"""
from __future__ import annotations

from types import SimpleNamespace

import polars as pl
import pytest

from app.api.monitor_rules import get_options
from app.services import kline_sync, quote_service
from app.services.quote_service import QuoteService
from app.strategy import monitor_rules
from app.strategy.monitor import MonitorRuleEngine

# ── 监控 sector fail-closed ──────────────────────────────────────────────

def _base_price_rule(scope: str) -> dict:
    return {
        "id": "r_test",
        "name": "t",
        "type": "price",
        "conditions": [{"field": "close", "op": ">", "value": 10}],
        "logic": "and",
        "scope": scope,
    }


def test_validate_rejects_sector_scope():
    with pytest.raises(ValueError):
        monitor_rules.validate(_base_price_rule("sector"))


def test_validate_accepts_symbols_scope():
    rule = _base_price_rule("symbols")
    rule["symbols"] = ["600000.SH"]
    monitor_rules.validate(rule)  # 不应抛


def test_apply_scope_sector_fails_closed():
    """历史遗留 sector 规则在评估时应返回空(绝不退化为全市场)。"""
    df = pl.DataFrame({"symbol": ["600000.SH", "000001.SZ"], "close": [10.0, 20.0]})
    out = MonitorRuleEngine._apply_scope(df, {"id": "r_old", "scope": "sector"})
    assert out.is_empty()

    # 对照: scope=all 返回全量, symbols 过滤子集
    assert MonitorRuleEngine._apply_scope(df, {"scope": "all"}).height == 2
    picked = MonitorRuleEngine._apply_scope(
        df, {"scope": "symbols", "symbols": ["600000.SH"]}
    )
    assert picked.height == 1


def test_sector_notification_uses_chinese_title_without_brand(monkeypatch):
    calls = []

    class CaptureExecutor:
        def submit(self, fn, *args):
            calls.append((fn, args))

    monkeypatch.setattr(quote_service, "_WEBHOOK_EXECUTOR", CaptureExecutor())
    monkeypatch.setattr("app.services.preferences.get_line_channel_access_token", lambda: "line-token")
    monkeypatch.setattr("app.services.preferences.get_line_target_id", lambda: "line-target")
    monkeypatch.setattr("app.services.preferences.get_telegram_bot_token", lambda: "telegram-token")
    monkeypatch.setattr("app.services.preferences.get_telegram_chat_id", lambda: "telegram-chat")

    engine = type("Engine", (), {
        "rules": {"r_sector": {"webhook_channels": ["line", "telegram"]}},
    })()
    QuoteService._maybe_send_webhook(
        object.__new__(QuoteService),
        [{
            "rule_id": "r_sector",
            "source": "sector",
            "symbol": "600000.SH",
            "name": "浦发银行",
            "message": "板塊異動預警",
        }],
        engine,
    )

    assert [args[2] for _, args in calls] == ["板塊", "板塊"]
    assert all("TickFlow" not in args[2] for _, args in calls)


# ── Options API contract ────────────────────────────────────────────────

def test_options_hides_sector_scope_but_keeps_sector_type(monkeypatch):
    """Scope sector is withdrawn (saving one always 400s); rule type sector must stay."""
    monkeypatch.setattr(kline_sync, "intraday_monitor_support", lambda: {})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    options = get_options(request)

    scope_keys = {s["key"] for s in options["scopes"]}
    assert "sector" not in scope_keys
    assert {"symbols", "watchlist_group", "all"} <= scope_keys
    assert "sector" in {t["key"] for t in options["types"]}
