"""Missing values in the screener sort, the screener sort whitelist and the chip-distribution levels."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.indicators.levels import compute_levels
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService
from app.taiwan.universe import get_security_master

pytestmark = pytest.mark.usefixtures("deny_network")

_PREV_SESSION = date(2026, 10, 5)
_QUOTE_SESSION = date(2026, 10, 6)


def _stub_service() -> TaiwanScreenerService:
    return TaiwanScreenerService(
        security_master=object(),
        daily_store=object(),
        institutional_store=object(),
        margin_store=object(),
    )


@pytest.mark.parametrize(("order", "ranked"), [("desc", ["E", "A", "C"]), ("asc", ["C", "A", "E"])])
def test_missing_values_sort_last_in_both_directions(order, ranked):
    frame = pl.DataFrame(
        {
            "symbol": ["D", "B", "E", "A", "C"],
            "change_pct": [float("nan"), None, 0.03, 0.01, -0.02],
        }
    )

    out = _stub_service()._apply_sort(frame, "change_pct", order)

    assert out["symbol"].to_list() == [*ranked, "B", "D"]


def _bar(symbol: str, day: date, close: float) -> dict:
    return {
        "symbol": symbol,
        "date": day.isoformat(),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000.0,
        "amount": close * 1000.0,
        "quote_ts": None,
    }


@pytest.fixture
def two_session_store(tmp_path, monkeypatch):
    """Three stocks with two sessions and 0050 with only the quote session, so its change_pct is null."""
    from app.config import settings
    from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
    from app.taiwan.share_capital_store import TaiwanShareCapitalStore

    get_security_master()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")
    store.write_batch(
        pl.DataFrame(
            [
                _bar("2330.TWSE", _PREV_SESSION, 100.0),
                _bar("2330.TWSE", _QUOTE_SESSION, 108.5),
                _bar("8069.TPEX", _PREV_SESSION, 100.0),
                _bar("8069.TPEX", _QUOTE_SESSION, 91.5),
                _bar("2317.TWSE", _PREV_SESSION, 100.0),
                _bar("2317.TWSE", _QUOTE_SESSION, 102.0),
                _bar("0050.TWSE", _QUOTE_SESSION, 108.5),
            ]
        )
    )
    TaiwanEnrichedRefreshService(
        daily_store=store,
        enriched_root=tmp_path / "kline_daily_enriched",
        share_capital=TaiwanShareCapitalStore(data_dir=tmp_path),
    ).refresh()
    yield store


@pytest.mark.parametrize("order", ["desc", "asc"])
def test_a_screener_run_lists_the_null_change_row_last(two_session_store, order):
    req = TaiwanScreenerRequest(instrument="ALL", sort_by="change_pct", sort_order=order)

    items = TaiwanScreenerService(daily_store=two_session_store).run(req).items

    changes = {i.symbol: i.change_pct for i in items}
    assert changes["0050.TWSE"] is None
    assert [i.symbol for i in items][-1] == "0050.TWSE"
    ranked = [i.change_pct for i in items[:-1]]
    assert ranked == sorted(ranked, reverse=order == "desc")


@pytest.mark.parametrize("field", ["foreign_net_5d", "investment_trust_net_5d"])
def test_the_always_null_five_day_fields_are_not_sortable(field, tmp_path, monkeypatch):
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with pytest.raises(ValidationError):
        TaiwanScreenerRequest(sort_by=field)

    client = TestClient(app, client=("127.0.0.1", 50000))
    resp = client.post("/api/taiwan/screener/run", json={"sort_by": field, "sort_order": "desc"})

    assert resp.status_code == 422
    assert any(err["loc"][-1] == "sort_by" for err in resp.json()["detail"])


_ABSENT = object()
_SESSIONS = 40


def _levels_frame(turnover) -> pl.DataFrame:
    """Heavy old trading near 10 and lighter recent trading near 20: decay moves the POC to 20."""
    start = date(2026, 1, 5)
    rows = []
    for i in range(_SESSIONS):
        old = i < _SESSIONS // 2
        mid = 10.0 if old else 20.0
        rows.append(
            {
                "date": start + timedelta(days=i),
                "open": mid,
                "high": mid + 0.5,
                "low": mid - 0.5,
                "close": mid,
                "volume": 1000.0 if old else 300.0,
            }
        )
    frame = pl.DataFrame(rows)
    if turnover is not _ABSENT:
        frame = frame.with_columns(pl.Series("turnover_rate", turnover, dtype=pl.Float64))
    return frame


def _sr(turnover) -> list[dict]:
    return compute_levels(_levels_frame(turnover))["sr"]


CHIP_POC_LABEL = "成交密集区(POC)"


def _poc(levels: list[dict]) -> dict:
    return next(p for p in levels if p["strength"] == "strong")


def test_full_turnover_history_keeps_the_decayed_chip_distribution():
    levels = _sr([10.0] * _SESSIONS)

    poc = _poc(levels)
    assert poc["value"] > 19
    assert poc["label"] == CHIP_POC_LABEL
    assert {p["label"] for p in levels if p is not poc} == {"成交密集区"}


@pytest.mark.parametrize(
    "turnover",
    [
        _ABSENT,
        [None] * _SESSIONS,
        [None] * (_SESSIONS - 1) + [10.0],
        [10.0] * (_SESSIONS - 1) + [None],
        [10.0] * (_SESSIONS // 2) + [float("nan")] + [10.0] * (_SESSIONS // 2 - 1),
    ],
    ids=["no_column", "all_null", "only_newest", "newest_missing", "one_nan"],
)
def test_missing_turnover_falls_back_to_a_labelled_volume_profile(turnover):
    levels = _sr(turnover)

    assert levels, "the sr group must not be empty"
    assert levels == _sr(_ABSENT)
    poc = _poc(levels)
    assert poc["value"] < 11
    assert poc["label"] == "量能POC"
    assert {p["label"] for p in levels if p is not poc} == {"量能密集區"}
    assert all(len(p["label"]) <= len(CHIP_POC_LABEL) for p in levels)
