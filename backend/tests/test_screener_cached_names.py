"""Instrument names on the cached strategy picks.

The cached rows are enriched frames, which carry no name, so without this the
dashboard card can only print the symbol twice.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import polars as pl

from app.api.screener import _attach_instrument_names


def _master(rows: list[tuple[str, str]]) -> MagicMock:
    known = {sym: SimpleNamespace(name=name) for sym, name in rows}
    sm = MagicMock()
    sm.get_instrument.side_effect = known.get
    return sm


def _payload(*symbols: str) -> dict:
    return {
        "as_of": "2026-09-21",
        "results": {
            "bollinger_squeeze": {
                "total": len(symbols),
                "rows": [{"symbol": s, "close": 10.0} for s in symbols],
            }
        },
    }


def test_names_come_from_the_security_master():
    payload = _payload("3467.TPEX", "2221.TPEX")
    with patch(
        "app.taiwan.universe.get_security_master",
        return_value=_master([("3467.TPEX", "台灣類比"), ("2221.TPEX", "大同")]),
    ):
        out = _attach_instrument_names(None, payload)

    rows = out["results"]["bollinger_squeeze"]["rows"]
    assert [r["name"] for r in rows] == ["台灣類比", "大同"]


def test_symbol_absent_from_the_master_keeps_no_name_rather_than_a_placeholder():
    """The card falls back to the symbol itself; a fabricated name would be worse."""
    payload = _payload("3467.TPEX", "9999.TWSE")
    with patch(
        "app.taiwan.universe.get_security_master",
        return_value=_master([("3467.TPEX", "台灣類比")]),
    ):
        out = _attach_instrument_names(None, payload)

    rows = out["results"]["bollinger_squeeze"]["rows"]
    assert rows[0]["name"] == "台灣類比"
    assert "name" not in rows[1]


def test_repo_instruments_cover_what_the_master_does_not():
    payload = _payload("3467.TPEX", "600000.SH")
    repo = MagicMock()
    repo.get_instruments.return_value = pl.DataFrame(
        {"symbol": ["600000.SH"], "name": ["浦发银行"]}
    )
    with patch(
        "app.taiwan.universe.get_security_master",
        return_value=_master([("3467.TPEX", "台灣類比")]),
    ):
        out = _attach_instrument_names(repo, payload)

    rows = out["results"]["bollinger_squeeze"]["rows"]
    assert [r["name"] for r in rows] == ["台灣類比", "浦发银行"]


def test_a_failing_lookup_returns_the_payload_untouched():
    """A name is cosmetic; losing the picks over it would not be."""
    payload = _payload("3467.TPEX")
    repo = MagicMock()
    repo.get_instruments.side_effect = RuntimeError("parquet unreadable")
    with patch(
        "app.taiwan.universe.get_security_master", side_effect=RuntimeError("no master")
    ):
        out = _attach_instrument_names(repo, payload)

    rows = out["results"]["bollinger_squeeze"]["rows"]
    assert rows[0]["symbol"] == "3467.TPEX"
    assert "name" not in rows[0]


def test_empty_results_are_left_alone():
    assert _attach_instrument_names(None, {"as_of": None, "results": {}}) == {
        "as_of": None,
        "results": {},
    }


def test_the_master_wins_when_both_sources_know_the_symbol():
    """The master is the Taiwan authority; repo instruments only fill its gaps.

    Nothing else pins the order, so a refactor that let the repo overwrite the
    master would rename Taiwan listings from a stale A-share table in silence.
    """
    payload = _payload("3467.TPEX")
    repo = MagicMock()
    repo.get_instruments.return_value = pl.DataFrame(
        {"symbol": ["3467.TPEX"], "name": ["過期名稱"]}
    )
    with patch(
        "app.taiwan.universe.get_security_master",
        return_value=_master([("3467.TPEX", "台灣類比")]),
    ):
        out = _attach_instrument_names(repo, payload)

    assert out["results"]["bollinger_squeeze"]["rows"][0]["name"] == "台灣類比"


def test_the_master_is_asked_only_about_symbols_the_picks_contain():
    """A pick list is a handful of symbols; the master holds thousands."""
    payload = _payload("3467.TPEX", "2221.TPEX")
    master = _master([("3467.TPEX", "台灣類比"), ("2221.TPEX", "大同")])
    with patch("app.taiwan.universe.get_security_master", return_value=master):
        _attach_instrument_names(None, payload)

    assert master.to_dataframe.call_count == 0
    assert sorted(c.args[0] for c in master.get_instrument.call_args_list) == [
        "2221.TPEX",
        "3467.TPEX",
    ]
