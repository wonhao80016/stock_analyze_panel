"""The corporate-action table holds the exchanges' official reference prices.

Fixtures are official responses saved on 2026-09-30, unchanged except
twt49u_2026_excerpt.json, which keeps the 2026 TWT49U header and seven of its rows.
The *_empty.json fixtures are the exchanges' answers to ranges without events; the
20260926_20260927 pair was saved on 2026-10-01.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
import time
import urllib.error
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan import corporate_actions, institutional_margin_refresh
from app.taiwan.bootstrap import TaiwanBootstrapService
from app.taiwan.corporate_actions import (
    REF_LIMIT_DOWN,
    REF_LIMIT_UP,
    REF_PREV_CLOSE,
    TaiwanCorporateActionRefreshService,
    TaiwanCorporateActionStore,
    actions_frame,
    attach_reference_prices,
    reference_prices,
    year_segments,
)
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import TaiwanDailyUpdateService, publish_enriched
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.enrichment.corporate_actions import (
    ETF_SPLIT,
    EXDIV,
    PAR_CHANGE,
    REDUCTION,
    TWSE_NO_DATA_STAT,
    TpexCapitalReductionAdapter,
    TpexExRightsAdapter,
    TwseCapitalReductionAdapter,
    TwseEtfSplitAdapter,
    TwseExRightsAdapter,
    TwseParValueChangeAdapter,
    default_adapters,
    parse_event_date,
)
from app.taiwan.market_institutional_refresh import TaiwanMarketInstitutionalRefreshService
from app.taiwan.realtime.calendar import taipei_today
from app.taiwan.share_capital_store import TaiwanShareCapitalStore

FIXTURES = Path(__file__).parent / "fixtures" / "taiwan_corporate_actions"
Y2024 = date(2024, 1, 1)
Y2026_END = date(2026, 12, 31)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _parse(adapter, name: str, start: date, end: date):
    return adapter.parse_payload(_load(name), start, end, adapter.build_url(start, end))


def _by_key(actions):
    return {(a.symbol, a.date): a for a in actions}


@pytest.fixture(autouse=True)
def _no_request_delay(monkeypatch):
    monkeypatch.setattr(institutional_margin_refresh, "INTER_REQUEST_DELAY_SEC", 0)


def test_twse_ex_rights_rows_carry_the_official_reference_prices():
    actions = _parse(TwseExRightsAdapter(), "twt49u_2026_excerpt.json", date(2026, 1, 1), Y2026_END)

    assert len(actions) == 7
    a = _by_key(actions)[("2379.TWSE", date(2026, 9, 8))]
    assert (a.kind, a.pre_close, a.ref_price, a.limit_up, a.limit_down, a.open_ref) == (EXDIV, 729.0, 704.0, 774.0, 634.0, 704.0)
    assert a.ex_factor == pytest.approx(729.0 / 704.0)
    assert a.official_label == _load("twt49u_2026_excerpt.json")["data"][-2][6]
    assert (a.meta.provider, a.meta.source, a.meta.status, a.meta.trade_date) == (
        "twse", "twse:twt49u", "official", date(2026, 9, 8)
    )
    assert a.meta.source_url.startswith("https://www.twse.com.tw/rwd/zh/exRight/TWT49U?")
    assert "startDate=20260101&endDate=20261231" in a.meta.source_url
    assert a.meta.fetched_at.tzinfo is not None


def test_a_session_is_listed_with_its_reference_price_the_evening_before():
    actions = _parse(TwseExRightsAdapter(), "twt49u_20260929_20261009.json", date(2026, 9, 29), date(2026, 10, 9))

    assert len(actions) == 4
    a = _by_key(actions)[("9927.TWSE", date(2026, 10, 1))]
    assert (a.pre_close, a.ref_price, a.limit_up, a.limit_down) == (70.10, 65.10, 71.60, 58.60)


def test_an_etf_split_resumes_at_the_official_reference_price():
    actions = _parse(TwseEtfSplitAdapter(), "twtcau_2024_2026.json", Y2024, Y2026_END)

    assert len(actions) == 11
    a = _by_key(actions)[("0050.TWSE", date(2025, 6, 18))]
    assert (a.kind, a.pre_close, a.ref_price, a.limit_up, a.limit_down) == (ETF_SPLIT, 188.65, 47.16, 51.85, 42.45)
    assert a.ex_factor == pytest.approx(188.65 / 47.16)


def test_reduction_rows_the_exchange_has_not_priced_yet_are_skipped():
    payload = _load("twtauu_2024_2026.json")
    actions = _parse(TwseCapitalReductionAdapter(), "twtauu_2024_2026.json", Y2024, Y2026_END)

    assert len(payload["data"]) == 46
    assert len(actions) == 44
    symbols = {a.symbol for a in actions}
    assert "4155.TWSE" not in symbols and "6142.TWSE" not in symbols
    a = _by_key(actions)[("3432.TWSE", date(2024, 1, 22))]
    assert (a.kind, a.pre_close, a.ref_price, a.limit_up, a.limit_down) == (REDUCTION, 10.65, 19.69, 21.65, 17.75)


def test_a_par_value_change_echoes_its_range_only_inside_params():
    actions = _parse(TwseParValueChangeAdapter(), "twtb8u_2024_2026.json", Y2024, Y2026_END)

    assert len(actions) == 7
    a = _by_key(actions)[("2327.TWSE", date(2025, 8, 25))]
    assert (a.kind, a.pre_close, a.ref_price, a.official_label) == (PAR_CHANGE, 546.0, 136.5, None)


def test_tpex_ex_rights_keep_the_no_limit_sentinels_as_published():
    actions = _parse(TpexExRightsAdapter(), "tpex_exdailyq_202609.json", date(2026, 9, 1), date(2026, 9, 30))

    assert len(actions) == 100
    by_key = _by_key(actions)
    a = by_key[("8440.TPEX", date(2026, 9, 30))]
    assert (a.kind, a.pre_close, a.ref_price, a.limit_up, a.limit_down) == (EXDIV, 20.95, 20.60, 22.65, 18.55)
    assert a.meta.source_url == (
        "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?response=json&startDate=2026/09/01&endDate=2026/09/30"
    )
    bond = by_key[("00966B.TPEX", date(2026, 9, 1))]
    assert (bond.limit_up, bond.limit_down) == (9999.95, 0.01)


def test_tpex_reductions_are_read_by_column_title():
    actions = _parse(TpexCapitalReductionAdapter(), "tpex_revivt_2024_2026.json", Y2024, Y2026_END)

    assert len(actions) == 34
    a = _by_key(actions)[("3064.TPEX", date(2024, 2, 5))]
    assert (a.kind, a.pre_close, a.ref_price, a.limit_up, a.limit_down, a.open_ref) == (
        REDUCTION, 10.65, 35.5, 39.05, 31.95, 35.5
    )


def test_official_event_dates_normalize_from_every_roc_form():
    twt49u_form = next(r[0] for r in _load("twt49u_2026_excerpt.json")["data"] if r[1] == "2379")

    assert parse_event_date(twt49u_form) == date(2026, 9, 8)
    assert parse_event_date("115/09/08") == date(2026, 9, 8)
    assert parse_event_date("1150908") == date(2026, 9, 8)


@pytest.mark.parametrize(("adapter", "name", "start", "end"), [
    (TwseParValueChangeAdapter(), "twtb8u_1990_2012_empty.json", date(1990, 1, 1), date(2012, 12, 31)),
    (TwseEtfSplitAdapter(), "twtcau_1990_2012_empty.json", date(1990, 1, 1), date(2012, 12, 31)),
    (TpexExRightsAdapter(), "tpex_exdailyq_200301_empty.json", date(2003, 1, 1), date(2003, 1, 28)),
])
def test_an_official_empty_answer_is_no_data(adapter, name, start, end):
    payload = _load(name)
    assert payload["stat"].upper() == "OK"

    assert adapter.parse_payload(payload, start, end, "u") == []


_TWSE_NO_DATA = {
    "twse:twt49u": "twt49u_20260926_20260927_empty.json",
    "twse:twtauu": "twtauu_20260926_20260927_empty.json",
}


@pytest.mark.parametrize(("adapter", "name"), [
    (TwseExRightsAdapter(), _TWSE_NO_DATA["twse:twt49u"]),
    (TwseCapitalReductionAdapter(), _TWSE_NO_DATA["twse:twtauu"]),
])
def test_the_twse_no_data_answer_is_no_data(adapter, name):
    payload = _load(name)
    assert list(payload) == ["stat"]

    assert adapter.parse_payload(payload, date(2026, 9, 26), date(2026, 9, 27), "u") == []


@pytest.mark.parametrize("stat", [
    _load("twtauu_2010.json")["stat"],
    TWSE_NO_DATA_STAT[:-1],
    f"{TWSE_NO_DATA_STAT} retry later",
])
def test_any_other_twse_stat_still_raises(stat):
    with pytest.raises(ValueError, match="rejected the query"):
        TwseCapitalReductionAdapter().parse_payload({"stat": stat}, date(2026, 9, 26), date(2026, 9, 27), "u")


@pytest.mark.parametrize("extra", [
    {"fields": _load("twtauu_2024_2026.json")["fields"], "data": _load("twtauu_2024_2026.json")["data"][:2]},
    {"data": _load("twtauu_2024_2026.json")["data"][:2]},
    {"fields": _load("twtauu_2024_2026.json")["fields"]},
])
def test_the_no_data_stat_next_to_a_table_raises(extra):
    payload = {**_load(_TWSE_NO_DATA["twse:twtauu"]), **extra}

    with pytest.raises(ValueError, match="answered no data but still sent"):
        TwseCapitalReductionAdapter().parse_payload(payload, date(2024, 1, 1), Y2026_END, "u")


def test_tpex_does_not_take_the_twse_no_data_answer():
    with pytest.raises(ValueError, match="rejected the query"):
        TpexCapitalReductionAdapter().parse_payload(
            _load(_TWSE_NO_DATA["twse:twtauu"]), date(2026, 9, 26), date(2026, 9, 27), "u"
        )


def test_an_official_rejection_raises():
    payload = _load("twtauu_2010.json")

    with pytest.raises(ValueError, match="rejected the query") as raised:
        TwseCapitalReductionAdapter().parse_payload(payload, date(2010, 1, 1), date(2010, 12, 31), "u")
    assert payload["stat"] in str(raised.value)


def test_a_tpex_stat_other_than_ok_raises():
    payload = _load("tpex_exdailyq_202609.json")
    payload["stat"] = "fail"

    with pytest.raises(ValueError, match="stat 'fail'"):
        TpexExRightsAdapter().parse_payload(payload, date(2026, 9, 1), date(2026, 9, 30), "u")


def test_a_response_without_a_stat_raises():
    with pytest.raises(ValueError, match="stat ''"):
        TwseExRightsAdapter().parse_payload({}, date(2026, 1, 1), Y2026_END, "u")


def test_a_twse_response_for_another_range_raises():
    with pytest.raises(ValueError, match="answered range '20260101~20261231'"):
        TwseExRightsAdapter().parse_payload(_load("twt49u_2026_excerpt.json"), date(2025, 1, 1), date(2025, 12, 31), "u")


def test_a_twse_response_without_a_range_echo_raises():
    payload = _load("twt49u_2026_excerpt.json")
    del payload["strDate"]

    with pytest.raises(ValueError, match="answered range '~20261231'"):
        TwseExRightsAdapter().parse_payload(payload, date(2026, 1, 1), Y2026_END, "u")


def test_a_tpex_response_for_another_range_raises():
    with pytest.raises(ValueError, match="answered range '20260901~20260930'"):
        TpexExRightsAdapter().parse_payload(_load("tpex_exdailyq_202609.json"), date(2026, 8, 1), date(2026, 8, 31), "u")


def _tpex_without(mutate) -> dict:
    payload = _load("tpex_exdailyq_202609.json")
    mutate(payload)
    return payload


@pytest.mark.parametrize(("payload", "message"), [
    (_tpex_without(lambda p: p.pop("tables")), "exactly one table"),
    (_tpex_without(lambda p: p.update(tables=[])), "exactly one table"),
    (_tpex_without(lambda p: p["tables"].append(copy.deepcopy(p["tables"][0]))), "exactly one table"),
    (_tpex_without(lambda p: p["tables"][0].pop("data")), "fields or data list"),
    (_tpex_without(lambda p: p["tables"][0].pop("fields")), "fields or data list"),
    (_tpex_without(lambda p: p["tables"][0].pop("totalCount")), "totalCount None"),
])
def test_a_tpex_response_without_its_table_raises(payload, message):
    with pytest.raises(ValueError, match=message):
        TpexExRightsAdapter().parse_payload(payload, date(2026, 9, 1), date(2026, 9, 30), "u")


@pytest.mark.parametrize("key", ["data", "fields"])
def test_a_twse_response_without_its_rows_or_fields_raises(key):
    payload = _load("twt49u_2026_excerpt.json")
    del payload[key]

    with pytest.raises(ValueError, match="fields or data list"):
        TwseExRightsAdapter().parse_payload(payload, date(2026, 1, 1), Y2026_END, "u")


def _with_title(payload: dict, old: str, new: str) -> dict:
    payload = copy.deepcopy(payload)
    payload["fields"] = [new if f == old else f for f in payload["fields"]]
    return payload


def test_a_price_column_in_thousands_raises():
    payload = _load("twt49u_2026_excerpt.json")
    title = TwseExRightsAdapter.COLUMNS["ref_price"]
    bad = _with_title(payload, title, f"{title}(千元)")

    with pytest.raises(ValueError, match="unsupported unit"):
        TwseExRightsAdapter().parse_payload(bad, date(2026, 1, 1), Y2026_END, "u")


def test_a_price_column_stated_in_twd_is_accepted():
    payload = _load("twt49u_2026_excerpt.json")
    title = TwseExRightsAdapter.COLUMNS["ref_price"]
    stated = _with_title(payload, title, f"{title}(元)")

    assert len(TwseExRightsAdapter().parse_payload(stated, date(2026, 1, 1), Y2026_END, "u")) == 7


def test_a_missing_column_raises():
    payload = _load("twt49u_2026_excerpt.json")
    bad = _with_title(payload, TwseExRightsAdapter.COLUMNS["limit_up"], "renamed")

    with pytest.raises(ValueError, match="lacks column"):
        TwseExRightsAdapter().parse_payload(bad, date(2026, 1, 1), Y2026_END, "u")


def test_a_malformed_price_raises():
    payload = _load("twt49u_2026_excerpt.json")
    payload["data"][0][4] = "12..5"

    with pytest.raises(ValueError, match="malformed number"):
        TwseExRightsAdapter().parse_payload(payload, date(2026, 1, 1), Y2026_END, "u")


def test_a_malformed_date_raises():
    payload = _load("twtcau_2024_2026.json")
    payload["data"][0][0] = "113/13/40"

    with pytest.raises(ValueError, match="invalid Taiwan date"):
        TwseEtfSplitAdapter().parse_payload(payload, Y2024, Y2026_END, "u")


def test_a_row_outside_the_echoed_range_raises():
    payload = _load("twt49u_20260929_20261009.json")
    payload["data"][0][0] = "1151102"

    with pytest.raises(ValueError, match="outside"):
        TwseExRightsAdapter().parse_payload(payload, date(2026, 9, 29), date(2026, 10, 9), "u")


def test_a_tpex_row_count_that_disagrees_with_its_total_raises():
    payload = _load("tpex_exdailyq_202609.json")
    payload["tables"][0]["data"] = payload["tables"][0]["data"][:-1]

    with pytest.raises(ValueError, match="totalCount"):
        TpexExRightsAdapter().parse_payload(payload, date(2026, 9, 1), date(2026, 9, 30), "u")


def test_the_store_keeps_one_row_per_event_and_replaces_restated_figures(tmp_path, caplog):
    store = TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions")
    actions = _parse(TwseExRightsAdapter(), "twt49u_2026_excerpt.json", date(2026, 1, 1), Y2026_END)

    assert store.upsert(actions_frame(actions)) == {"inserted": 7, "updated": 0, "unchanged": 0}
    assert store.upsert(actions_frame(actions + actions)) == {"inserted": 0, "updated": 0, "unchanged": 7}

    restated = actions_frame(actions).with_columns(
        pl.when(pl.col("symbol") == "2379.TWSE").then(700.0).otherwise(pl.col("ref_price")).alias("ref_price")
    )
    with caplog.at_level(logging.WARNING, logger="app.taiwan.corporate_actions"):
        assert store.upsert(restated) == {"inserted": 0, "updated": 1, "unchanged": 6}
    assert "2379.TWSE 2026-09-08 exdiv restated" in caplog.text
    assert "ref_price 704.0->700.0" in caplog.text

    df = store.read()
    assert df.height == 7
    assert df.filter(pl.col("symbol") == "2379.TWSE")["ref_price"].to_list() == [700.0]
    assert df.schema["retrieved_at"] == pl.Datetime("us", "Asia/Taipei")
    assert list(store.path.parent.iterdir()) == [store.path]


def test_the_store_reads_a_date_range(tmp_path):
    store = TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions")
    actions = _parse(TwseEtfSplitAdapter(), "twtcau_2024_2026.json", Y2024, Y2026_END)
    store.upsert(actions_frame(actions))
    june = sorted(a.symbol for a in actions if date(2025, 6, 1) <= a.date <= date(2025, 6, 30))

    df = store.read(start=date(2025, 6, 1), end=date(2025, 6, 30))

    assert "0050.TWSE" in june
    assert sorted(df["symbol"].to_list()) == june
    assert store.read(symbols=["0050.TWSE"])["date"].to_list() == [date(2025, 6, 18)]


def _year_response(name: str, start: date, end: date) -> dict:
    """A saved multi-year response narrowed to one query year, as the exchange would answer it."""
    payload = _load(name)
    adapter = next(a for a in default_adapters() if _SOURCE_OF[name] == a.SOURCE)
    if "tables" in payload:
        table = payload["tables"][0]
        idx = [str(f).strip() for f in table["fields"]].index(adapter.COLUMNS["date"])
        table["data"] = [r for r in table["data"] if start <= parse_event_date(r[idx]) <= end]
        table["totalCount"] = len(table["data"])
        payload["date"] = f"{start:%Y%m%d}~{end:%Y%m%d}"
        return payload
    idx = [str(f).strip() for f in payload["fields"]].index(adapter.COLUMNS["date"])
    payload["data"] = [r for r in payload["data"] if start <= parse_event_date(r[idx]) <= end]
    for key in ("strDate", "startDate"):
        if key in payload:
            payload[key] = f"{start:%Y%m%d}"
    payload["endDate"] = f"{end:%Y%m%d}"
    if "params" in payload:
        payload["params"].update(startDate=f"{start:%Y%m%d}", endDate=f"{end:%Y%m%d}")
    return payload


_SOURCE_OF = {
    "twt49u_2026_excerpt.json": "twse:twt49u",
    "twtauu_2024_2026.json": "twse:twtauu",
    "twtb8u_2024_2026.json": "twse:twtb8u",
    "twtcau_2024_2026.json": "twse:twtcau",
    "tpex_exdailyq_202609.json": "tpex:exdailyq",
    "tpex_revivt_2024_2026.json": "tpex:revivt",
}
_ANSWERED_EMPTY = {"twse:twt49u", "tpex:exdailyq"}


def _without_rows(payload: dict) -> dict:
    table = payload["tables"][0] if "tables" in payload else payload
    table["data"] = []
    if "totalCount" in table:
        table["totalCount"] = 0
    return payload


class _Exchange:
    """Answers each table's yearly query from the saved responses; TWT49U and exDailyQ answer with no rows."""

    def __init__(
        self, fail_source: str | None = None, reject_source: str | None = None, twse_no_data: bool = False
    ) -> None:
        self.urls: list[str] = []
        self.fail_source = fail_source
        self.reject_source = reject_source
        self.twse_no_data = twse_no_data

    def fetch(self, url: str) -> dict:
        self.urls.append(url)
        adapter = next(a for a in default_adapters() if url.startswith(a.BASE_URL))
        if self.fail_source == adapter.SOURCE:
            raise urllib.error.HTTPError(url, 500, "Internal Server Error", None, None)
        if self.reject_source == adapter.SOURCE:
            return _load("twtauu_2010.json")
        if self.twse_no_data and adapter.SOURCE in _TWSE_NO_DATA:
            return _load(_TWSE_NO_DATA[adapter.SOURCE])
        params = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))
        start, end = (parse_event_date(params[k].replace("/", "")) for k in ("startDate", "endDate"))
        name = next(n for n, s in _SOURCE_OF.items() if s == adapter.SOURCE)
        payload = _year_response(name, start, end)
        return _without_rows(payload) if adapter.SOURCE in _ANSWERED_EMPTY else payload

    def sources_asked(self) -> list[str]:
        return [next(a.SOURCE for a in default_adapters() if u.startswith(a.BASE_URL)) for u in self.urls]


def test_a_backfill_asks_each_table_once_per_year_and_stores_every_priced_event(tmp_path):
    exchange = _Exchange()
    store = TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions")
    svc = TaiwanCorporateActionRefreshService(store=store, fetch_json=exchange.fetch)

    stats = svc.refresh_range(Y2024, Y2026_END)

    assert stats["requests"] == 18 and len(exchange.urls) == 18
    assert sum("TWT49U" in u for u in exchange.urls) == 3
    assert stats["failed"] == []
    assert stats["inserted"] == 44 + 7 + 11 + 34
    df = store.read()
    assert df.group_by("source").len().sort("source").rows() == [
        ("tpex:revivt", 34), ("twse:twtauu", 44), ("twse:twtb8u", 7), ("twse:twtcau", 11),
    ]
    assert df.filter((pl.col("symbol") == "0050.TWSE") & (pl.col("date") == date(2025, 6, 18)))["ref_price"].to_list() == [47.16]


def test_a_failing_table_is_reported_and_the_other_tables_are_stored(tmp_path):
    exchange = _Exchange(fail_source="twse:twtcau")
    store = TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions")
    svc = TaiwanCorporateActionRefreshService(store=store, fetch_json=exchange.fetch)

    stats = svc.refresh_range(date(2025, 1, 1), date(2025, 12, 31))

    assert [(f["source"], f["start"], f["end"]) for f in stats["failed"]] == [("twse:twtcau", "2025-01-01", "2025-12-31")]
    assert "HTTP Error 500" in stats["failed"][0]["error"]
    assert "twse:twtcau" not in store.read()["source"].to_list()
    assert store.read().height == stats["inserted"] > 0


def test_an_official_rejection_is_reported_as_a_failed_table(tmp_path, caplog):
    exchange = _Exchange(reject_source="twse:twtauu")
    store = TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions")
    svc = TaiwanCorporateActionRefreshService(store=store, fetch_json=exchange.fetch)

    with caplog.at_level(logging.WARNING, logger="app.taiwan.corporate_actions"):
        stats = svc.refresh_range(date(2025, 1, 1), date(2025, 12, 31))

    assert [(f["source"], f["start"], f["end"]) for f in stats["failed"]] == [("twse:twtauu", "2025-01-01", "2025-12-31")]
    assert _load("twtauu_2010.json")["stat"] in stats["failed"][0]["error"]
    assert "Corporate action fetch failed for twse:twtauu" in caplog.text
    assert "twse:twtauu" not in store.read()["source"].to_list()


def test_the_publish_window_ends_at_the_target_session():
    svc = TaiwanCorporateActionRefreshService(store=MagicMock(), adapters=[])
    svc.refresh_range = MagicMock(return_value={})

    svc.refresh_publish_window(date(2026, 9, 21), date(2026, 9, 29))

    assert svc.refresh_range.call_args.args == (date(2026, 9, 11), date(2026, 9, 29))
    assert svc.refresh_range.call_args.kwargs == {"skip_covered": True}


def _covered_service(store: TaiwanCorporateActionStore, exchange: _Exchange, today: date = date(2026, 9, 30)):
    return TaiwanCorporateActionRefreshService(store=store, fetch_json=exchange.fetch, today=lambda: today)


def test_a_publish_skips_the_tables_already_fetched_through_its_target(tmp_path):
    store = TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions")
    exchange = _Exchange()
    svc = _covered_service(store, exchange)

    first = svc.refresh_publish_window(date(2026, 9, 21), date(2026, 9, 29))
    again = svc.refresh_publish_window(date(2026, 9, 21), date(2026, 9, 29))

    assert (first["requests"], first["skipped"]) == (6, [])
    assert (again["requests"], len(again["skipped"])) == (0, 6)
    assert len(exchange.urls) == 6
    state = json.loads(store.fetch_state_path.read_text(encoding="utf-8"))
    assert sorted(state) == sorted(a.SOURCE for a in default_adapters())
    assert {(s["covered_from"], s["covered_through"]) for s in state.values()} == {("2026-09-11", "2026-09-29")}

    later = svc.refresh_publish_window(date(2026, 9, 22), date(2026, 9, 30))

    assert later["requests"] == 6
    assert {s["covered_through"] for s in store.fetch_state().values()} == {"2026-09-30"}
    assert {s["covered_from"] for s in store.fetch_state().values()} == {"2026-09-11"}


def test_a_failed_table_is_not_recorded_and_is_asked_again(tmp_path):
    store = TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions")
    failing = _Exchange(reject_source="tpex:revivt")

    first = _covered_service(store, failing).refresh_publish_window(date(2026, 9, 21), date(2026, 9, 29))
    retry_exchange = _Exchange()
    retry = _covered_service(store, retry_exchange).refresh_publish_window(date(2026, 9, 21), date(2026, 9, 29))

    assert [f["source"] for f in first["failed"]] == ["tpex:revivt"]
    assert retry_exchange.sources_asked() == ["tpex:revivt"]
    assert retry["failed"] == [] and "tpex:revivt" in store.fetch_state()


def test_the_twse_no_data_answer_is_recorded_as_fetched(tmp_path):
    store = TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions")
    exchange = _Exchange(twse_no_data=True)

    first = _covered_service(store, exchange).refresh_publish_window(date(2026, 9, 21), date(2026, 9, 29))
    again = _covered_service(store, exchange).refresh_publish_window(date(2026, 9, 21), date(2026, 9, 29))

    assert first["failed"] == [] and first["requests"] == 6
    state = store.fetch_state()
    assert {(state[s]["covered_from"], state[s]["covered_through"]) for s in _TWSE_NO_DATA} == {("2026-09-11", "2026-09-29")}
    assert again["requests"] == 0 and set(_TWSE_NO_DATA) <= set(again["skipped"])


def test_a_backfill_covers_its_tables_only_through_today(tmp_path):
    store = TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions")
    backfill = _Exchange()
    _covered_service(store, backfill).refresh_range(Y2024, Y2026_END)

    assert backfill.urls and {s["covered_through"] for s in store.fetch_state().values()} == {"2026-09-30"}

    daily = _Exchange()
    covered = _covered_service(store, daily).refresh_publish_window(date(2026, 9, 21), date(2026, 9, 30))
    beyond = _covered_service(store, daily, today=date(2026, 10, 1)).refresh_publish_window(
        date(2026, 9, 22), date(2026, 10, 1)
    )

    assert covered["requests"] == 0
    assert beyond["requests"] == 6


def test_an_unreadable_fetch_state_fetches_every_table(tmp_path, caplog):
    store = TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions")
    store.fetch_state_path.parent.mkdir(parents=True)
    store.fetch_state_path.write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="app.taiwan.corporate_actions"):
        stats = _covered_service(store, _Exchange()).refresh_publish_window(date(2026, 9, 21), date(2026, 9, 29))

    assert stats["requests"] == 6
    assert "fetch state unreadable" in caplog.text
    assert len(store.fetch_state()) == 6


def test_year_segments_split_at_new_year():
    assert year_segments(date(2025, 12, 22), date(2026, 1, 11)) == [
        (date(2025, 12, 22), date(2025, 12, 31)), (date(2026, 1, 1), date(2026, 1, 11)),
    ]


def test_the_backfill_cli_takes_a_start_date(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(TaiwanCorporateActionRefreshService, "__init__", lambda self: None)
    monkeypatch.setattr(
        TaiwanCorporateActionRefreshService, "refresh_range", lambda self, s, e: calls.append((s, e)) or {"ok": 1}
    )
    monkeypatch.setattr(sys, "argv", ["corporate_actions", "--since", "2024-01-01", "--until", "2026-10-11"])

    corporate_actions.main()

    assert calls == [(date(2024, 1, 1), date(2026, 10, 11))]
    assert '"ok": 1' in capsys.readouterr().out


def test_the_backfill_cli_exits_non_zero_when_a_table_fails(monkeypatch, capsys):
    failed = {"failed": [{"source": "twse:twtauu", "start": "2025-01-01", "end": "2025-12-31", "error": "HTTP 500"}]}
    monkeypatch.setattr(TaiwanCorporateActionRefreshService, "__init__", lambda self: None)
    monkeypatch.setattr(TaiwanCorporateActionRefreshService, "refresh_range", lambda self, s, e: failed)
    monkeypatch.setattr(sys, "argv", ["corporate_actions", "--since", "2025-01-01", "--until", "2025-12-31"])

    with pytest.raises(SystemExit) as exited:
        corporate_actions.main()

    assert exited.value.code == 1
    assert "twse:twtauu" in capsys.readouterr().out


def test_the_backfill_cli_defaults_to_today(monkeypatch):
    calls = []
    monkeypatch.setattr(TaiwanCorporateActionRefreshService, "__init__", lambda self: None)
    monkeypatch.setattr(
        TaiwanCorporateActionRefreshService, "refresh_range", lambda self, s, e: calls.append((s, e)) or {"failed": []}
    )
    monkeypatch.setattr(sys, "argv", ["corporate_actions", "--since", "2024-01-01"])

    corporate_actions.main()

    assert calls == [(date(2024, 1, 1), taipei_today())]


SESSIONS = [date(2026, 9, d) for d in (21, 22, 23, 24, 25, 28, 29)]


def _daily_store(
    root: Path, sessions: list[date] = SESSIONS, symbols: tuple[str, ...] = ("2330.TWSE",)
) -> TaiwanDailyStore:
    store = TaiwanDailyStore(data_dir=root)
    n = len(sessions) * len(symbols)
    store.write_batch(pl.DataFrame({
        "symbol": [s for s in symbols for _ in sessions], "date": [d for _ in symbols for d in sessions],
        "open": [1.0] * n, "high": [1.0] * n, "low": [1.0] * n,
        "close": [1.0] * n, "volume": [1.0] * n, "amount": [1.0] * n,
        "quote_ts": pl.Series([None] * n, dtype=pl.Int64),
    }))
    return store


def _cached_security_master(monkeypatch) -> None:
    """Serve the shared master from the configured cache; a master missing its cache fetches the directory."""
    from app.taiwan import universe
    from app.taiwan.data_root import taiwan_data_root

    master = universe.TaiwanSecurityMaster(cache_path=taiwan_data_root() / "security_master.parquet")
    assert master.load_cache(), "tests need the security master cache under DATA_DIR/taiwan"
    monkeypatch.setattr(universe, "_default_master", master)


@pytest.fixture
def quiet_publish(tmp_path, monkeypatch) -> list[str]:
    """Record the publish instead of computing it; the enriched root is a scratch folder."""
    from app.config import settings

    _cached_security_master(monkeypatch)
    order: list[str] = []
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(TaiwanShareCapitalStore, "refresh", lambda self: 0)
    monkeypatch.setattr(
        TaiwanEnrichedRefreshService, "refresh_recent", lambda self, days=5: order.append("publish") or 0
    )
    return order


def _corporate_service(order: list[str], result=None, error: Exception | None = None) -> MagicMock:
    svc = MagicMock()

    def refresh_publish_window(first_published, target):
        order.append("actions")
        if error is not None:
            raise error
        return result or {"failed": []}

    svc.refresh_publish_window.side_effect = refresh_publish_window
    return svc


def test_publishing_fetches_the_reference_prices_of_the_republished_sessions_first(tmp_path, quiet_publish):
    store = _daily_store(tmp_path / "daily")
    svc = _corporate_service(quiet_publish)

    assert publish_enriched(store, corporate_actions=svc) is True

    assert quiet_publish == ["actions", "publish"]
    assert svc.refresh_publish_window.call_args.args == (SESSIONS[0], SESSIONS[-1])
    assert TaiwanEnrichedRefreshService(daily_store=store).recent_window() == (SESSIONS[0], SESSIONS[-1])


def test_publishing_without_stored_bars_fetches_nothing(tmp_path, quiet_publish):
    svc = _corporate_service(quiet_publish)

    assert publish_enriched(TaiwanDailyStore(data_dir=tmp_path / "empty"), corporate_actions=svc) is True

    svc.refresh_publish_window.assert_not_called()
    assert quiet_publish == ["publish"]


def test_an_official_rejection_reaches_the_publish_note(tmp_path, quiet_publish, caplog):
    exchange = _Exchange(reject_source="twse:twtauu")
    svc = _covered_service(TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions"), exchange)
    notes: list[str] = []

    with caplog.at_level(logging.WARNING, logger="app.taiwan.daily_update"):
        publish_enriched(_daily_store(tmp_path / "daily"), corporate_actions=svc, notes=notes)

    assert notes == ["Corporate action tables not refreshed: twse:twtauu 2026-09-11..2026-09-29"]
    assert notes[0] in caplog.text
    assert quiet_publish == ["publish"]


def test_the_window_spans_the_sessions_refresh_recent_republishes(tmp_path):
    store = _daily_store(tmp_path / "daily")
    root = tmp_path / "enriched"
    service = TaiwanEnrichedRefreshService(daily_store=store, enriched_root=root, share_capital=MagicMock())
    later = time.time() + 60
    for d in SESSIONS:
        TaiwanEnrichedRefreshService._write_partition(root / f"date={d.isoformat()}", pl.DataFrame({"symbol": ["2330.TWSE"]}))
        os.utime(root / f"date={d.isoformat()}" / "part.parquet", (later, later))
    published: list[tuple[date, date]] = []
    service.refresh = lambda start=None, end=None: published.append((start, end)) or 0

    service.refresh_recent(days=2)
    assert service.recent_window(days=2) == (SESSIONS[-2], SESSIONS[-1]) == published[-1]

    refetched = tmp_path / "daily" / f"date={SESSIONS[1].isoformat()}" / "part.parquet"
    os.utime(refetched, (later + 60, later + 60))
    service.refresh_recent(days=2)
    assert service.recent_window(days=2) == (SESSIONS[1], SESSIONS[-1]) == published[-1]


def test_a_failed_reference_table_is_logged_and_noted_and_the_publish_still_runs(tmp_path, quiet_publish, caplog):
    failed = {"failed": [{"source": "tpex:revivt", "start": "2026-09-11", "end": "2026-09-29", "error": "HTTP 500"}]}
    notes: list[str] = []

    with caplog.at_level(logging.WARNING, logger="app.taiwan.daily_update"):
        published = publish_enriched(
            _daily_store(tmp_path / "daily"), corporate_actions=_corporate_service(quiet_publish, failed), notes=notes
        )

    assert published is True
    assert quiet_publish == ["actions", "publish"]
    assert notes == ["Corporate action tables not refreshed: tpex:revivt 2026-09-11..2026-09-29"]
    assert notes[0] in caplog.text


def test_a_reference_fetch_that_raises_is_logged_and_the_publish_still_runs(tmp_path, quiet_publish, caplog):
    with caplog.at_level(logging.WARNING, logger="app.taiwan.daily_update"):
        published = publish_enriched(
            _daily_store(tmp_path / "daily"),
            corporate_actions=_corporate_service(quiet_publish, error=RuntimeError("disk full")),
        )

    assert published is True
    assert quiet_publish == ["actions", "publish"]
    assert "Corporate action refresh failed: disk full" in caplog.text


def test_a_failed_publish_is_logged_and_noted(tmp_path, quiet_publish, monkeypatch, caplog):
    def failing_publish(self, days=5):
        raise OSError("marker not writable")

    monkeypatch.setattr(TaiwanEnrichedRefreshService, "refresh_recent", failing_publish)
    notes: list[str] = []

    with caplog.at_level(logging.WARNING, logger="app.taiwan.daily_update"):
        published = publish_enriched(
            _daily_store(tmp_path / "daily"), corporate_actions=_corporate_service(quiet_publish), notes=notes
        )

    assert published is True
    assert notes == ["Enriched publish failed: marker not writable"]
    assert "Taiwan enriched publish failed: marker not writable" in caplog.text


def test_nothing_is_fetched_when_the_update_button_finds_the_table_current(tmp_path, quiet_publish, monkeypatch):
    store = _daily_store(tmp_path / "daily")
    monkeypatch.setattr(TaiwanEnrichedRefreshService, "stale_since", lambda self, available=None: None)
    monkeypatch.setattr(
        TaiwanEnrichedRefreshService, "indicator_definitions_changed", lambda self: False
    )
    svc = _corporate_service(quiet_publish)

    assert publish_enriched(store, only_if_behind=True, corporate_actions=svc) is False

    svc.refresh_publish_window.assert_not_called()
    assert quiet_publish == []


def test_the_update_button_publishes_when_the_last_publication_never_committed(tmp_path, quiet_publish, monkeypatch):
    from app.enriched_generation import EnrichedPublication

    store = _daily_store(tmp_path / "daily")
    monkeypatch.setattr(TaiwanEnrichedRefreshService, "stale_since", lambda self, available=None: None)
    EnrichedPublication(tmp_path, "stock", recover=True).begin()

    assert publish_enriched(store, only_if_behind=True, corporate_actions=_corporate_service(quiet_publish)) is True

    assert quiet_publish == ["actions", "publish"]


def _dated_store(latest: date) -> MagicMock:
    store = MagicMock()
    store.available_dates.return_value = [latest]
    store.provisional_dates.return_value = []
    store.incomplete_dates.return_value = []
    store.repair_dates.return_value = []
    return store


def _update_service(corporate_svc) -> TaiwanDailyUpdateService:
    daily_svc = MagicMock()
    daily_svc.refresh_dates.return_value = {
        "dates_requested": 1, "dates_fetched": 1, "dates_skipped": 0, "total_rows_written": 5, "failed_dates": [],
    }
    return TaiwanDailyUpdateService(
        daily_store=_dated_store(date(2026, 9, 29)),
        inst_store=_dated_store(date(2026, 9, 30)),
        margin_store=_dated_store(date(2026, 9, 30)),
        daily_service=daily_svc,
        inst_service=MagicMock(),
        margin_service=MagicMock(),
        corporate_action_service=corporate_svc,
    )


def test_the_scheduled_update_fetches_reference_prices_in_the_publish_step(quiet_publish, monkeypatch):
    monkeypatch.setattr(TaiwanMarketInstitutionalRefreshService, "refresh_through", lambda self, target: {})
    svc = _corporate_service(quiet_publish)

    result = _update_service(svc).run_update(target_date=date(2026, 9, 30), refresh_institutional=False, refresh_margin=False)

    assert quiet_publish == ["actions", "publish"]
    assert result.daily.status == "success"
    assert result.daily.note is None


def test_the_scheduled_update_notes_a_failed_table_without_changing_the_daily_status(quiet_publish, monkeypatch):
    monkeypatch.setattr(TaiwanMarketInstitutionalRefreshService, "refresh_through", lambda self, target: {})
    failed = {"failed": [{"source": "tpex:revivt", "start": "2026-09-19", "end": "2026-09-29", "error": "HTTP 500"}]}

    result = _update_service(_corporate_service(quiet_publish, failed)).run_update(
        target_date=date(2026, 9, 30), refresh_institutional=False, refresh_margin=False
    )

    assert result.daily.status == "success"
    assert result.daily.note == "Corporate action tables not refreshed: tpex:revivt 2026-09-19..2026-09-29"
    assert quiet_publish == ["actions", "publish"]


def test_the_scheduled_update_survives_a_reference_fetch_that_raises(quiet_publish, monkeypatch):
    monkeypatch.setattr(TaiwanMarketInstitutionalRefreshService, "refresh_through", lambda self, target: {})

    result = _update_service(_corporate_service(quiet_publish, error=RuntimeError("disk full"))).run_update(
        target_date=date(2026, 9, 30), refresh_institutional=False, refresh_margin=False
    )

    assert result.daily.status == "success"
    assert result.daily.note == "Corporate action reference prices not refreshed: disk full"
    assert quiet_publish == ["actions", "publish"]


def test_the_update_button_fetches_reference_prices_through_the_shared_publish(tmp_path, quiet_publish, monkeypatch, caplog):
    _daily_store(tmp_path / "taiwan" / "daily", symbols=("2330.TWSE", "6488.TPEX"))
    monkeypatch.setattr("app.taiwan.bootstrap.resolve_target_latest_trading_date", lambda: SESSIONS[-1])
    calls: list[tuple[date, date]] = []

    def failing_refresh(self, first_published, target):
        calls.append((first_published, target))
        raise RuntimeError("TWSE throttled")

    monkeypatch.setattr(TaiwanCorporateActionRefreshService, "refresh_publish_window", failing_refresh)

    with caplog.at_level(logging.WARNING, logger="app.taiwan.daily_update"):
        res = TaiwanBootstrapService().update_to_latest()

    assert calls == [(SESSIONS[0], SESSIONS[-1])]
    assert quiet_publish == ["publish"]
    assert res["ok"] is True and res["enriched_published"] is True
    assert "Corporate action refresh failed: TWSE throttled" in caplog.text


def test_a_bootstrap_service_with_an_injected_store_sends_no_corporate_action_request(tmp_path, quiet_publish, monkeypatch):
    """Regression guard: an injected store never publishes, so it never reaches the exchanges."""
    requests: list[str] = []
    monkeypatch.setattr("app.taiwan.bootstrap.resolve_target_latest_trading_date", lambda: date(2026, 9, 30))
    monkeypatch.setattr(TaiwanCorporateActionRefreshService, "refresh_range", lambda self, s, e, **kw: requests.append("range"))
    monkeypatch.setattr(
        institutional_margin_refresh, "_fetch_json_with_retry", lambda url, **kw: requests.append(url) or {}
    )
    refresh = MagicMock()
    refresh.refresh_dates.return_value = {"dates_fetched": 1}
    svc = TaiwanBootstrapService(store=_daily_store(tmp_path / "injected"), refresh_service=refresh)

    res = svc.update_to_latest()

    assert requests == []
    assert quiet_publish == []
    assert res["enriched_published"] is False


def _events(rows: list[tuple[str, date, str, float, float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"symbol": s, "date": d, "kind": k, "ref_price": r, "limit_up": up, "limit_down": dn}
            for s, d, k, r, up, dn in rows
        ],
        schema={
            "symbol": pl.String, "date": pl.Date, "kind": pl.String,
            "ref_price": pl.Float64, "limit_up": pl.Float64, "limit_down": pl.Float64,
        },
    )


def _bars(symbol: str, dates: list[date]) -> pl.DataFrame:
    return pl.DataFrame({"symbol": [symbol] * len(dates), "date": dates, "close": [10.0] * len(dates)})


def test_an_event_sets_the_base_of_the_session_it_opens():
    bars = _bars("3034.TWSE", [date(2026, 7, 8), date(2026, 7, 9), date(2026, 7, 13), date(2026, 7, 14)])
    events = _events([
        ("3034.TWSE", date(2026, 7, 10), EXDIV, 519.0, 570.0, 467.5),
        ("3034.TWSE", date(2026, 7, 20), EXDIV, 1.0, 2.0, 0.5),
        ("2330.TWSE", date(2026, 7, 13), EXDIV, 9.0, 9.9, 8.1),
    ])

    out = attach_reference_prices(bars, events, with_limits=True).sort("date")

    assert out[REF_PREV_CLOSE].to_list() == [None, None, 519.0, None]
    assert out[REF_LIMIT_UP].to_list() == [None, None, 570.0, None]
    assert out[REF_LIMIT_DOWN].to_list() == [None, None, 467.5, None]


def test_the_first_bar_takes_only_an_event_on_its_own_date():
    events = _events([("0050.TWSE", date(2025, 6, 18), ETF_SPLIT, 47.16, 51.85, 42.45)])

    assert attach_reference_prices(_bars("0050.TWSE", [date(2025, 6, 18)]), events)[REF_PREV_CLOSE].to_list() == [47.16]
    assert attach_reference_prices(_bars("0050.TWSE", [date(2025, 6, 19)]), events)[REF_PREV_CLOSE].to_list() == [None]


def test_bars_without_events_get_null_columns():
    out = attach_reference_prices(_bars("2330.TWSE", [date(2026, 9, 7)]), _events([]), with_limits=True)

    assert out.columns == ["symbol", "date", "close", REF_PREV_CLOSE, REF_LIMIT_UP, REF_LIMIT_DOWN]
    assert out[REF_PREV_CLOSE].dtype == pl.Float64 and out[REF_PREV_CLOSE].to_list() == [None]


def test_a_resumption_wins_a_same_day_tie_with_a_dividend():
    events = _events([
        ("3356.TWSE", date(2026, 9, 21), EXDIV, 60.0, 66.0, 54.0),
        ("3356.TWSE", date(2026, 9, 21), REDUCTION, 68.04, 74.8, 61.3),
    ])

    out = attach_reference_prices(_bars("3356.TWSE", [date(2026, 9, 18), date(2026, 9, 21)]), events)

    assert out[REF_PREV_CLOSE].to_list() == [None, 68.04]
    assert reference_prices(events, date(2026, 9, 18), date(2026, 9, 21))["ref_price"].to_list() == [68.04]


def test_reference_prices_pick_the_event_between_two_sessions():
    events = _events([
        ("2379.TWSE", date(2026, 9, 8), EXDIV, 704.0, 774.0, 634.0),
        ("6196.TWSE", date(2026, 6, 24), EXDIV, 534.55, 588.0, 481.5),
    ])

    got = reference_prices(events, date(2026, 9, 7), date(2026, 9, 8))
    assert got.rows() == [("2379.TWSE", 704.0, 774.0, 634.0)]
    assert reference_prices(events, date(2026, 9, 8), date(2026, 9, 9)).is_empty()
    assert reference_prices(events, None, date(2026, 9, 8))["symbol"].to_list() == ["2379.TWSE"]
