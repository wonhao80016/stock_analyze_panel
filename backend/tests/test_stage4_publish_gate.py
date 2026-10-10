"""The update button's publish gate also sees an indicator-definition change (stage 4, D7).

publish_enriched(only_if_behind=True) runs for bootstrap and "update latest" when the stored bars
are current; it must still trigger the one full republish a new definition version requires.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.indicators.pipeline import INDICATOR_DEFINITION_VERSION
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import publish_enriched
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.share_capital_store import TaiwanShareCapitalStore

pytestmark = pytest.mark.usefixtures("deny_network")

SESSIONS = [date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)]


@pytest.fixture
def publishes(tmp_path, monkeypatch) -> list[str]:
    """Records each enriched publish; the enriched root and factor record live under tmp_path."""
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(TaiwanShareCapitalStore, "refresh", lambda self: 0)
    monkeypatch.setattr(
        TaiwanEnrichedRefreshService, "stale_since", lambda self, available=None: None
    )
    calls: list[str] = []
    monkeypatch.setattr(
        TaiwanEnrichedRefreshService,
        "refresh_recent",
        lambda self, days=5: calls.append("publish") or 0,
    )
    return calls


def _store(root) -> TaiwanDailyStore:
    store = TaiwanDailyStore(data_dir=root)
    store.write_batch(
        pl.DataFrame(
            {
                "symbol": ["2330.TWSE"] * len(SESSIONS),
                "date": SESSIONS,
                "open": [1.0] * len(SESSIONS),
                "high": [1.0] * len(SESSIONS),
                "low": [1.0] * len(SESSIONS),
                "close": [1.0] * len(SESSIONS),
                "volume": [1.0] * len(SESSIONS),
                "amount": [1.0] * len(SESSIONS),
                "quote_ts": pl.Series([None] * len(SESSIONS), dtype=pl.Int64),
            }
        )
    )
    return store


def _record(version: int | None) -> None:
    payload = {"fingerprint": "f", "factors": 0, "through": SESSIONS[-1].isoformat()}
    if version is not None:
        payload["indicator_version"] = version
    path = TaiwanEnrichedRefreshService().factor_set_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _corporate_actions() -> MagicMock:
    svc = MagicMock()
    svc.refresh_publish_window.return_value = {"failed": []}
    return svc


@pytest.mark.parametrize(
    "version", [INDICATOR_DEFINITION_VERSION - 1, None], ids=["older_version", "no_version"]
)
def test_the_update_button_republishes_when_the_indicator_definitions_changed(
    tmp_path, publishes, version
):
    store = _store(tmp_path / "daily")
    _record(version)

    assert publish_enriched(store, only_if_behind=True, corporate_actions=_corporate_actions())

    assert publishes == ["publish"]


def test_the_update_button_skips_a_current_table_under_the_current_definitions(tmp_path, publishes):
    store = _store(tmp_path / "daily")
    _record(INDICATOR_DEFINITION_VERSION)
    actions = _corporate_actions()

    assert publish_enriched(store, only_if_behind=True, corporate_actions=actions) is False

    assert publishes == []
    actions.refresh_publish_window.assert_not_called()
