"""The daily publish fetches the official session bases of its window before the enriched publish.

Payloads are the saved TWT84U and dailyQuotes excerpts of test_taiwan_session_bases, restamped
with each requested date.
"""
from __future__ import annotations

import copy
import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import TaiwanDailyUpdateService, publish_enriched
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.session_bases import (
    TPEX,
    TWSE,
    TaiwanSessionBaseRefreshService,
    TaiwanSessionBaseStore,
    TpexSessionBaseAdapter,
    TwseSessionBaseAdapter,
)
from app.taiwan.share_capital_store import TaiwanShareCapitalStore

pytestmark = pytest.mark.usefixtures("deny_network")

FIXTURES = Path(__file__).parent / "fixtures" / "taiwan_session_bases"
SESSIONS = [
    date(2025, 9, 1), date(2025, 9, 2), date(2025, 9, 3), date(2025, 9, 4),
    date(2025, 9, 5), date(2025, 9, 8), date(2025, 9, 9),
]
WINDOW = (SESSIONS[2], SESSIONS[-1])
TARGET = SESSIONS[-1]
TWSE_CODES = {"0050", "0061", "1101", "1435", "2330"}
TPEX_CODES = {"00679B", "00720B", "1240", "2235", "5274", "6488"}
ADAPTERS = {TWSE: TwseSessionBaseAdapter(), TPEX: TpexSessionBaseAdapter()}
PAYLOADS = {TWSE: "twt84u_20250408_excerpt.json", TPEX: "tpex_dq_20241001_excerpt.json"}


def _payload(source: str, day: date) -> dict:
    payload = json.loads((FIXTURES / PAYLOADS[source]).read_text(encoding="utf-8"))
    payload["date"] = f"{day:%Y%m%d}"
    return payload


def _url(source: str, day: date) -> str:
    return ADAPTERS[source].build_url(day)


def _codes(exchange: str, _day: date) -> set[str]:
    return TWSE_CODES if exchange == "TWSE" else TPEX_CODES


class _Exchange:
    """Answers every TWT84U and dailyQuotes URL of SESSIONS unless *failing* maps it to an error."""

    def __init__(self, events: list[str], failing: dict[str, Exception] | None = None) -> None:
        self.events = events
        self.failing = failing or {}
        self.asked: list[str] = []
        self.answers = {_url(s, d): _payload(s, d) for s in (TWSE, TPEX) for d in SESSIONS}

    def __call__(self, url: str):
        self.asked.append(url)
        self.events.append("fetch")
        if url in self.failing:
            raise self.failing[url]
        return copy.deepcopy(self.answers[url])


@pytest.fixture
def events(tmp_path, monkeypatch) -> list[str]:
    """Records each enriched publish; the publish window is pinned to WINDOW."""
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(TaiwanShareCapitalStore, "refresh", lambda self: 0)
    monkeypatch.setattr(TaiwanShareCapitalStore, "save_session_denominators", lambda self, d: None)
    monkeypatch.setattr(TaiwanEnrichedRefreshService, "recent_window", lambda self, days=5: WINDOW)
    calls: list[str] = []
    monkeypatch.setattr(
        TaiwanEnrichedRefreshService,
        "refresh_recent",
        lambda self, days=5: calls.append("publish") or 0,
    )
    return calls


def _daily_store(root: Path) -> TaiwanDailyStore:
    store = TaiwanDailyStore(root / "daily")
    store.write_batch(pl.DataFrame([
        {"symbol": "2330.TWSE", "date": d, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
         "volume": 1.0, "amount": 10.0, "quote_ts": None}
        for d in SESSIONS
    ], schema_overrides={"quote_ts": pl.Int64}))
    return store


def _service(tmp_path, exchange: _Exchange) -> TaiwanSessionBaseRefreshService:
    return TaiwanSessionBaseRefreshService(
        store=TaiwanSessionBaseStore(tmp_path / "session_bases"),
        daily_store=_daily_store(tmp_path),
        fetch_json=exchange,
        codes=_codes,
        sleep=lambda _s: None,
    )


def _store_bases(tmp_path, days: list[date], written_at: dict[tuple[str, date], datetime] | None = None):
    """Store both tables for *days*, written the morning after each session unless *written_at* differs."""
    store = TaiwanSessionBaseStore(tmp_path / "session_bases")
    for d in days:
        for source, adapter in ADAPTERS.items():
            url = adapter.build_url(d)
            store.write(source, d, adapter.parse_payload(_payload(source, d), d, url, codes=_codes(adapter.EXCHANGE, d)))
            when = (written_at or {}).get((source, d), datetime.combine(d + timedelta(days=1), time(9), TAIPEI))
            os.utime(store.path(source, d), (when.timestamp(), when.timestamp()))
    return store


def _publish(tmp_path, exchange: _Exchange, notes: list[str] | None = None) -> None:
    corporate = MagicMock()
    corporate.refresh_publish_window.return_value = {"failed": []}
    assert publish_enriched(
        _daily_store(tmp_path),
        corporate_actions=corporate,
        session_bases=_service(tmp_path, exchange),
        notes=notes,
    )


def test_publish_fetches_the_window_and_the_session_before_it_ahead_of_the_enriched_publish(tmp_path, events):
    exchange = _Exchange(events)
    notes: list[str] = []

    _publish(tmp_path, exchange, notes)

    assert exchange.asked == [_url(s, d) for d in SESSIONS[1:] for s in (TWSE, TPEX)]
    assert events == ["fetch"] * 12 + ["publish"]
    assert notes == []
    store = TaiwanSessionBaseStore(tmp_path / "session_bases")
    assert store.stored_dates(TWSE) == store.stored_dates(TPEX) == SESSIONS[1:]


def test_an_after_close_run_requests_only_the_new_session(tmp_path, events):
    _store_bases(tmp_path, SESSIONS[1:-1])
    exchange = _Exchange(events)

    _publish(tmp_path, exchange)

    assert exchange.asked == [_url(TWSE, TARGET), _url(TPEX, TARGET)]
    assert events == ["fetch", "fetch", "publish"]


def test_the_evening_run_refetches_only_the_provisional_daily_quotes(tmp_path, events):
    after_close = datetime.combine(TARGET, time(16, 31), TAIPEI)
    store = _store_bases(
        tmp_path, SESSIONS[1:], {(TWSE, TARGET): after_close, (TPEX, TARGET): after_close}
    )
    assert not store.is_final(TPEX, TARGET)
    exchange = _Exchange(events)

    _publish(tmp_path, exchange)

    assert exchange.asked == [_url(TPEX, TARGET)]
    assert events == ["fetch", "publish"]
    assert store.is_final(TPEX, TARGET)

    exchange.asked.clear()
    _publish(tmp_path, exchange)

    assert exchange.asked == []


def test_a_failed_fetch_is_noted_and_the_publish_goes_on(tmp_path, events):
    _store_bases(tmp_path, SESSIONS[1:-1])
    exchange = _Exchange(events, {_url(TWSE, TARGET): ValueError("twse:twt84u returned no rows")})
    notes: list[str] = []

    _publish(tmp_path, exchange, notes)

    assert notes == ["Official session bases not stored: twse:twt84u 2025-09-09"]
    assert events == ["fetch", "fetch", "publish"]
    store = TaiwanSessionBaseStore(tmp_path / "session_bases")
    assert (store.is_final(TWSE, TARGET), TARGET in store.stored_dates(TPEX)) == (False, True)


def test_a_raising_refresh_is_noted_and_the_publish_goes_on(tmp_path, events):
    service = MagicMock()
    service.refresh.side_effect = RuntimeError("store unavailable")
    notes: list[str] = []

    assert publish_enriched(
        _daily_store(tmp_path),
        corporate_actions=MagicMock(**{"refresh_publish_window.return_value": {}}),
        session_bases=service,
        notes=notes,
    )

    assert notes == ["Official session bases not refreshed: store unavailable"]
    assert events == ["publish"]


def test_run_update_fetches_the_bases_before_publishing_and_keeps_the_daily_status(events):
    service = MagicMock()
    service.refresh.side_effect = lambda since, until: events.append(f"bases {since}..{until}") or {
        "failed": [{"source": "tpex:dailyquotes", "date": str(TARGET), "error": "HTTP 503"}],
        "stopped_at": None,
    }
    daily_store = MagicMock()
    daily_store.available_dates.return_value = SESSIONS
    daily_store.repair_dates.return_value = []
    chip_store = MagicMock()
    chip_store.available_dates.return_value = [TARGET]
    fetched = {"dates_requested": 1, "dates_fetched": 1, "total_rows_written": 10, "failed_dates": []}
    services = {name: MagicMock(**{"refresh_dates.return_value": fetched}) for name in ("daily", "inst", "margin")}

    result = TaiwanDailyUpdateService(
        daily_store=daily_store,
        inst_store=chip_store,
        margin_store=chip_store,
        daily_service=services["daily"],
        inst_service=services["inst"],
        margin_service=services["margin"],
        market_inst_service=MagicMock(**{"refresh_through.return_value": {}}),
        corporate_action_service=MagicMock(**{"refresh_publish_window.return_value": {}}),
        session_base_service=service,
    ).run_update(target_date=TARGET)

    assert events == [f"bases {SESSIONS[1]}..{TARGET}", "publish"]
    assert (result.daily.status, result.overall_status) == ("success", "success")
    assert result.daily.note == (
        "Daily store already current; Official session bases not stored: tpex:dailyquotes 2025-09-09"
    )
