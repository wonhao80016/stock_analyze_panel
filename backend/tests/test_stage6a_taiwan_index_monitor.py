"""A Taiwan index rule (t00.TWSE) fires from the polled index quote, which never reaches a store."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from app.market_time import cn_today
from app.services import quote_service as qs_mod
from app.services.quote_service import QuoteService
from app.strategy import monitor_rules
from app.strategy.monitor import MonitorRuleEngine


def _rule(rid: str, field: str, op: str, value: float) -> dict:
    return monitor_rules.normalize({
        "id": rid,
        "name": rid,
        "type": "price",
        "asset_type": "index",
        "scope": "symbols",
        "symbols": ["t00.TWSE"],
        "logic": "and",
        "conditions": [{"field": field, "op": op, "value": value}],
        "cooldown_seconds": 0,
    })


class _IndexFeed:
    """Hybrid-shaped live rows: change_pct is a percentage number, as the provider emits it."""

    def __init__(self) -> None:
        self.close = 23000.0
        self.requested: list[list[str]] = []

    def get_realtime(self, symbols):
        self.requested.append(list(symbols))
        prev_close = 23000.0
        return pl.DataFrame([{
            "symbol": "t00.TWSE", "date": cn_today(), "open": prev_close, "high": max(self.close, prev_close),
            "low": min(self.close, prev_close), "close": self.close, "volume": 5_000_000.0,
            "amount": None, "prev_close": prev_close,
            "change_pct": round((self.close / prev_close - 1) * 100, 4), "status": "realtime",
        }])


@pytest.fixture
def harness(tmp_path):
    fired: list[dict] = []
    engine = MonitorRuleEngine(alert_handler=fired.append)
    engine.set_rules([])
    repo = MagicMock()
    repo.get_index_symbol_set.return_value = set()
    repo.get_enriched_latest.return_value = (pl.DataFrame(), None)
    repo.get_enriched_latest_asset.return_value = (pl.DataFrame(), None)
    repo.get_name_map.return_value = {"t00.TWSE": "發行量加權股價指數"}
    repo.store.data_dir = tmp_path
    svc = QuoteService()
    svc.set_repo(repo)
    svc._app_state = SimpleNamespace(monitor_engine=engine, repo=repo)
    feed = _IndexFeed()

    def poll(close: float) -> list[dict]:
        feed.close = close
        before = len(fired)
        with (
            patch("app.services.preferences.get_realtime_data_provider", return_value="taiwan"),
            patch("app.services.preferences.get_realtime_pull_index", return_value=True),
            patch("app.services.preferences.get_realtime_index_symbols", return_value=["t00.TWSE"]),
            patch("app.data_providers.registry.get_provider", return_value=feed),
            patch("app.services.watchlist.list_symbols", return_value=[]),
            patch.object(QuoteService, "_is_continuous_trading", return_value=True),
            patch.object(QuoteService, "_record_minute_bars"),
            patch.object(qs_mod, "_persist_last_fetch"),
        ):
            svc._fetch_full_market_quotes()
        return fired[before:]

    return SimpleNamespace(svc=svc, engine=engine, repo=repo, feed=feed, poll=poll)


def _assert_nothing_stored(repo: MagicMock) -> None:
    for writer in (repo.flush_live_enriched_asset, repo.merge_live_enriched_asset):
        assert not any(call.args and call.args[0] == "index" for call in writer.call_args_list), writer


def test_change_pct_rule_fires_only_once_the_index_reaches_it(harness):
    harness.engine.add_rule(_rule("idx_up", "change_pct", ">=", 0.01))

    below = harness.poll(23115.0)
    above = harness.poll(23276.0)

    assert below == []
    assert [(e["rule_id"], e["symbol"]) for e in above] == [("idx_up", "t00.TWSE")]
    assert above[0]["change_pct"] == pytest.approx(0.012)
    assert above[0]["price"] == pytest.approx(23276.0)
    _assert_nothing_stored(harness.repo)


def test_close_rule_never_fires_below_its_level(harness):
    harness.engine.add_rule(_rule("idx_level", "close", ">=", 24000.0))

    assert harness.poll(23100.0) == []
    assert harness.poll(23900.0) == []
    assert [e["rule_id"] for e in harness.poll(24010.0)] == ["idx_level"]
    _assert_nothing_stored(harness.repo)


def test_index_rows_carry_decimal_change_pct_and_today(harness):
    harness.poll(23230.0)

    rows = harness.svc._taiwan_index_live
    row = rows.row(0, named=True)
    assert row["symbol"] == "t00.TWSE"
    assert row["date"] == cn_today()
    assert row["close"] == pytest.approx(23230.0)
    assert row["prev_close"] == pytest.approx(23000.0)
    assert row["change_pct"] == pytest.approx(0.01)
    assert row["change_amount"] == pytest.approx(230.0)
    assert "volume" not in rows.columns
