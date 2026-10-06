"""Market-wide institutional amounts come from the exchanges' official summaries, in TWD.

The fixtures are the TWSE BFI82U and TPEx insti/summary responses for 2026-09-30,
saved verbatim. Synthetic summaries reuse the official category names from the adapter.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan import (
    enriched_refresh,
    institutional_margin_refresh,
    market_institutional_refresh,
    market_intelligence,
    share_capital_store,
)
from app.taiwan.ai_research import build_evidence_registry
from app.taiwan.corporate_actions import TaiwanCorporateActionRefreshService
from app.taiwan.daily_update import TaiwanDailyUpdateService
from app.taiwan.enrichment.market_institutional import (
    TPEX_CATEGORIES,
    TWSE_CATEGORIES,
    MarketInstitutionalAmount,
    TpexMarketInstitutionalAdapter,
    TwseMarketInstitutionalAdapter,
)
from app.taiwan.enrichment.models import SourceMeta
from app.taiwan.market_institutional_refresh import TaiwanMarketInstitutionalRefreshService
from app.taiwan.market_institutional_store import TaiwanMarketInstitutionalStore, amounts_frame
from app.taiwan.market_intelligence import TaiwanMarketIntelligenceService
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar
from app.taiwan.research_context import TaiwanStockResearchContextService

FIXTURES = Path(__file__).parent / "fixtures" / "taiwan_market_institutional"
OCT01 = date(2026, 10, 1)
SEP30 = date(2026, 9, 30)
SEP29 = date(2026, 9, 29)
SEP28 = date(2026, 9, 28)
SEP25 = date(2026, 9, 25)
SEP24 = date(2026, 9, 24)
SEP23 = date(2026, 9, 23)
SEP22 = date(2026, 9, 22)

TWSE_FOREIGN = 30_856_555_666
TPEX_FOREIGN = 8_159_658_452
TWSE_TRUST = 8_290_786_559
TPEX_TRUST = -2_203_631_373
TWSE_DEALER = 1_381_211_230 - 140_858_608
TPEX_DEALER = -179_392_981 + 835_677_204
TWSE_TOTAL = 40_387_694_847
TPEX_TOTAL = 6_612_311_302

# Foreign dealers trade 30 net; they are already inside the dealer rows, so the total leaves them out.
SYNTHETIC_TWSE = {
    "foreign_excl_dealer": (1000, 900, 100),
    "foreign_dealer": (50, 20, 30),
    "investment_trust": (300, 280, 20),
    "dealer_proprietary": (500, 460, 40),
    "dealer_hedge": (200, 190, 10),
    "total": (2000, 1830, 170),
}
SYNTHETIC_TPEX = {
    "foreign_total": (1050, 920, 130),
    **{k: v for k, v in SYNTHETIC_TWSE.items() if k != "total"},
    "dealer_total": (700, 650, 50),
    "total": (2000, 1830, 170),
}


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _twse_payload(d: date = SEP30) -> dict:
    payload = _load("bfi_20260930.json")
    payload["date"] = d.strftime("%Y%m%d")
    return payload


def _tpex_payload(d: date = SEP30) -> dict:
    payload = _load("tpex_20260930.json")
    payload["date"] = d.strftime("%Y%m%d")
    payload["tables"][0]["date"] = f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"
    return payload


def _rows(categories: dict[str, str], amounts: dict[str, tuple[int, int, int]]) -> list[list[str]]:
    names = {key: name for name, key in categories.items()}
    return [[names[c], f"{b:,}", f"{s:,}", f"{n:,}"] for c, (b, s, n) in amounts.items()]


def _synthetic_twse(amounts: dict[str, tuple[int, int, int]] = SYNTHETIC_TWSE, d: date = SEP30) -> dict:
    payload = _twse_payload(d)
    payload["data"] = _rows(TWSE_CATEGORIES, amounts)
    return payload


def _synthetic_tpex(amounts: dict[str, tuple[int, int, int]] = SYNTHETIC_TPEX, d: date = SEP30) -> dict:
    payload = _tpex_payload(d)
    payload["tables"][0]["data"] = _rows(TPEX_CATEGORIES, amounts)
    return payload


def _taipei(d: date, hour: int, minute: int = 0) -> float:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=TAIPEI_TZ).timestamp()


def _set_mtime(store_root: Path, d: date, when: float) -> None:
    os.utime(store_root / f"date={d.isoformat()}" / "part.parquet", (when, when))


def _net(rows, category: str) -> int:
    return next(a.net_amount for a in rows if a.category == category)


def _flagged(rows) -> set[tuple[str, str]]:
    return {(a.exchange, a.category) for a in rows if a.has_discrepancy}


def test_the_official_summaries_parse_into_twd_for_every_category():
    twse = TwseMarketInstitutionalAdapter().parse_payload(_load("bfi_20260930.json"), SEP30, "u")
    tpex = TpexMarketInstitutionalAdapter().parse_payload(_load("tpex_20260930.json"), SEP30, "u")

    assert len(twse) == 6
    assert len(tpex) == 8
    assert all(a.category is not None for a in twse + tpex)
    assert _net(twse, "foreign_excl_dealer") == TWSE_FOREIGN
    assert _net(tpex, "foreign_excl_dealer") == TPEX_FOREIGN
    assert _net(twse, "investment_trust") == TWSE_TRUST
    assert _net(tpex, "investment_trust") == TPEX_TRUST
    assert _net(twse, "dealer_proprietary") + _net(twse, "dealer_hedge") == TWSE_DEALER
    assert _net(tpex, "dealer_proprietary") + _net(tpex, "dealer_hedge") == TPEX_DEALER
    assert _net(twse, "total") == TWSE_TOTAL
    assert _net(tpex, "total") == TPEX_TOTAL
    foreign = next(a for a in twse if a.category == "foreign_excl_dealer")
    assert (foreign.buy_amount, foreign.sell_amount) == (395_761_657_462, 364_905_101_796)
    assert _flagged(twse + tpex) == set()
    # 308.57 + 81.60 in the official tables, 390.16 once added before rounding.
    assert round((TWSE_FOREIGN + TPEX_FOREIGN) / 1e8, 2) == 390.16


def test_every_row_carries_its_source_meta():
    fetched = datetime(2026, 9, 30, 17, 5, tzinfo=TAIPEI_TZ)
    rows = TwseMarketInstitutionalAdapter().parse_payload(_twse_payload(), SEP30, "https://twse/bfi", fetched_at=fetched)

    meta = rows[0].meta
    assert isinstance(meta, SourceMeta)
    assert (meta.provider, meta.source, meta.source_url) == ("twse", "twse:bfi82u", "https://twse/bfi")
    assert meta.retrieved_at == fetched
    assert meta.trade_date == SEP30
    assert meta.status == "official"
    assert all(r.meta is meta for r in rows)


def test_a_day_fetched_long_after_it_traded_uses_the_stale_status():
    fetched = datetime(2026, 10, 6, 9, 0, tzinfo=TAIPEI_TZ)
    rows = TpexMarketInstitutionalAdapter().parse_payload(_tpex_payload(), SEP30, "u", fetched_at=fetched)

    assert {r.meta.status for r in rows} == {"stale"}


@pytest.mark.parametrize(("unit", "scale"), [("元", 1), ("千元", 1000)], ids=["twd", "thousand-twd"])
def test_the_twse_unit_whitelist_scales_to_twd(unit, scale):
    payload = _twse_payload()
    payload["hints"] = unit

    rows = TwseMarketInstitutionalAdapter().parse_payload(payload, SEP30, "u")

    assert _net(rows, "foreign_excl_dealer") == TWSE_FOREIGN * scale


def test_amounts_published_in_thousands_are_scaled_to_twd():
    payload = _tpex_payload()
    table = payload["tables"][0]
    table["fields"] = [f.replace("(元)", "(千元)") for f in table["fields"]]

    rows = TpexMarketInstitutionalAdapter().parse_payload(payload, SEP30, "u")

    assert _net(rows, "foreign_excl_dealer") == TPEX_FOREIGN * 1000


@pytest.mark.parametrize("unit", ["萬元", "百萬元", "億元", ""], ids=["ten-thousand", "million", "hundred-million", "empty"])
def test_the_twse_summary_in_any_other_unit_is_rejected(unit):
    payload = _twse_payload()
    payload["hints"] = unit

    with pytest.raises(ValueError, match="unit"):
        TwseMarketInstitutionalAdapter().parse_payload(payload, SEP30, "u")


@pytest.mark.parametrize("unit", ["萬元", "百萬元", "億元", ""], ids=["ten-thousand", "million", "hundred-million", "empty"])
def test_the_tpex_summary_in_any_other_unit_is_rejected(unit):
    payload = _tpex_payload()
    table = payload["tables"][0]
    table["fields"] = [f.replace("(元)", f"({unit})") for f in table["fields"]]

    with pytest.raises(ValueError, match="unit"):
        TpexMarketInstitutionalAdapter().parse_payload(payload, SEP30, "u")


def test_a_tpex_summary_whose_amount_columns_disagree_on_units_is_rejected():
    payload = _tpex_payload()
    table = payload["tables"][0]
    table["fields"][2] = table["fields"][2].replace("(元)", "(千元)")

    with pytest.raises(ValueError, match="inconsistent amount units"):
        TpexMarketInstitutionalAdapter().parse_payload(payload, SEP30, "u")


def test_a_twse_summary_without_a_unit_is_rejected():
    payload = _twse_payload()
    del payload["hints"]

    with pytest.raises(ValueError, match="unit"):
        TwseMarketInstitutionalAdapter().parse_payload(payload, SEP30, "u")


@pytest.mark.parametrize("where", ["top", "table"])
def test_a_tpex_summary_stamped_with_another_day_has_no_rows(where):
    payload = _tpex_payload()
    if where == "top":
        payload["date"] = "20260929"
    else:
        payload["tables"][0]["date"] = "115/09/29"

    assert TpexMarketInstitutionalAdapter().parse_payload(payload, SEP30, "u") == []


@pytest.mark.parametrize("where", ["top", "table"])
def test_a_tpex_summary_without_a_date_stamp_has_no_rows(where):
    payload = _tpex_payload()
    if where == "top":
        del payload["date"]
    else:
        payload["tables"][0]["date"] = ""

    assert TpexMarketInstitutionalAdapter().parse_payload(payload, SEP30, "u") == []


def test_a_twse_summary_stamped_with_another_day_has_no_rows():
    assert TwseMarketInstitutionalAdapter().parse_payload(_twse_payload(SEP29), SEP30, "u") == []


def test_a_twse_summary_without_a_date_stamp_has_no_rows():
    payload = _twse_payload()
    del payload["date"]

    assert TwseMarketInstitutionalAdapter().parse_payload(payload, SEP30, "u") == []


def test_a_closed_day_answer_has_no_rows():
    assert TwseMarketInstitutionalAdapter().parse_payload({"stat": "no data"}, SEP30, "u") == []
    empty = _tpex_payload()
    empty["tables"][0]["data"] = []
    assert TpexMarketInstitutionalAdapter().parse_payload(empty, SEP30, "u") == []


@pytest.mark.parametrize("category", ["investment_trust", "total"])
def test_a_summary_missing_a_required_category_is_rejected(category):
    payload = _twse_payload()
    payload["data"] = [r for r in payload["data"] if TWSE_CATEGORIES.get(r[0]) != category]

    with pytest.raises(ValueError, match=category):
        TwseMarketInstitutionalAdapter().parse_payload(payload, SEP30, "u")


def test_an_unknown_official_category_is_kept_without_a_category_key():
    payload = _twse_payload()
    payload["data"].append(["NEW", "1", "0", "1"])

    rows = TwseMarketInstitutionalAdapter().parse_payload(payload, SEP30, "u")

    assert [(a.official_name, a.category) for a in rows][-1] == ("NEW", None)


def test_foreign_dealers_stay_out_of_foreign_and_out_of_the_total():
    twse = TwseMarketInstitutionalAdapter().parse_payload(_synthetic_twse(), SEP30, "u")
    tpex = TpexMarketInstitutionalAdapter().parse_payload(_synthetic_tpex(), SEP30, "u")

    assert _flagged(twse + tpex) == set()
    for rows in (twse, tpex):
        assert _net(rows, "foreign_dealer") == 30
        assert _net(rows, "total") == 170
        assert _net(rows, "total") == sum(
            _net(rows, c) for c in ("foreign_excl_dealer", "investment_trust", "dealer_proprietary", "dealer_hedge")
        )
    assert _net(tpex, "foreign_total") == _net(tpex, "foreign_excl_dealer") + _net(tpex, "foreign_dealer")


def test_inconsistent_official_figures_are_kept_and_flagged():
    twse_amounts = {**SYNTHETIC_TWSE, "investment_trust": (300, 280, 25)}
    tpex_amounts = {**SYNTHETIC_TPEX, "foreign_total": (1050, 920, 131)}

    twse = TwseMarketInstitutionalAdapter().parse_payload(_synthetic_twse(twse_amounts), SEP30, "u")
    tpex = TpexMarketInstitutionalAdapter().parse_payload(_synthetic_tpex(tpex_amounts), SEP30, "u")

    assert _flagged(twse) == {("TWSE", "investment_trust"), ("TWSE", "total")}
    assert _flagged(tpex) == {("TPEX", "foreign_total")}
    assert _net(twse, "investment_trust") == 25
    assert _net(twse, "total") == 170
    assert _net(tpex, "foreign_total") == 131


def test_a_total_that_disagrees_only_on_buy_amounts_is_flagged():
    amounts = {**SYNTHETIC_TWSE, "total": (2001, 1831, 170)}

    rows = TwseMarketInstitutionalAdapter().parse_payload(_synthetic_twse(amounts), SEP30, "u")

    assert _flagged(rows) == {("TWSE", "total")}


class _Exchange:
    """Answers one exchange's summary requests per day with a payload, None (closed) or an error."""

    def __init__(self, make_payload, outcomes: dict[date, object] | None = None):
        self._make = make_payload
        self.outcomes = outcomes or {}
        self.calls: list[date] = []

    def answer(self, d: date) -> dict:
        self.calls.append(d)
        outcome = self.outcomes.get(d, "ok")
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            return {"stat": "no data"}
        return self._make(d)


class _Market:
    def __init__(self, twse=None, tpex=None):
        self.twse = _Exchange(_twse_payload, twse)
        self.tpex = _Exchange(_tpex_payload, tpex)

    def fetch(self, url: str) -> dict:
        if url.startswith(TwseMarketInstitutionalAdapter.BASE_URL):
            return self.twse.answer(datetime.strptime(url.split("dayDate=")[1][:8], "%Y%m%d").date())
        return self.tpex.answer(datetime.strptime(url.split("date=")[1][:10], "%Y/%m/%d").date())


class _DailyDays:
    def __init__(self, days: list[date] | None = None):
        self.days = sorted(days or [])

    def available_dates(self) -> list[date]:
        return list(self.days)


@pytest.fixture(autouse=True)
def _no_request_delay(monkeypatch):
    monkeypatch.setattr(institutional_margin_refresh, "INTER_REQUEST_DELAY_SEC", 0)


def _service(
    tmp_path: Path, market: _Market, trading_days: list[date] | None = None
) -> tuple[TaiwanMarketInstitutionalRefreshService, TaiwanMarketInstitutionalStore]:
    store = TaiwanMarketInstitutionalStore(data_dir=tmp_path / "market_institutional")
    svc = TaiwanMarketInstitutionalRefreshService(
        store=store, calendar=TaiwanTradingCalendar(), fetch_json=market.fetch, daily_store=_DailyDays(trading_days)
    )
    return svc, store


def _stored(store: TaiwanMarketInstitutionalStore, tmp_path: Path, d: date, when: float, trust_net: int = 100) -> None:
    amounts = []
    for exchange in ("TWSE", "TPEX"):
        meta = SourceMeta(source="test", source_url="u", fetched_at=datetime.fromtimestamp(when, TAIPEI_TZ),
                          trade_date=d, status="official", provider=exchange.lower())
        for category in ("foreign_excl_dealer", "investment_trust", "dealer_proprietary", "dealer_hedge", "total"):
            net = trust_net if category == "investment_trust" else 1
            amounts.append(MarketInstitutionalAmount(exchange, category, category, net, 0, net, False, meta))
    store.write_day(d, amounts_frame(d, amounts))
    _set_mtime(tmp_path / "market_institutional", d, when)


def _trust_nets(store: TaiwanMarketInstitutionalStore, d: date) -> list[int]:
    df = store.read_day(d).filter(pl.col("category") == "investment_trust").sort("exchange")
    return df["net_amount"].to_list()


def test_a_day_with_both_exchanges_is_stored_with_every_category_and_its_provenance(tmp_path):
    svc, store = _service(tmp_path, _Market())

    stats = svc.refresh_days([SEP23])

    assert stats["dates_fetched"] == 1
    assert stats["failed_dates"] == [] and stats["repair_failed_dates"] == []
    df = store.read_day(SEP23)
    assert df.height == 14
    assert df.schema["retrieved_at"] == pl.Datetime("us", "Asia/Taipei")
    assert df["retrieved_at"].null_count() == 0
    assert set(df["provider"].to_list()) == {"twse", "tpex"}
    assert set(df["source"].to_list()) == {"twse:bfi82u", "tpex:insti_summary"}
    assert df["source_url"].str.contains("BFI82U|insti/summary").all()
    assert df["trade_date"].unique().to_list() == [SEP23]
    assert set(df["status"].to_list()) <= {"official", "stale"}
    assert not df["has_discrepancy"].any()
    assert _trust_nets(store, SEP23) == [TPEX_TRUST, TWSE_TRUST]


@pytest.mark.parametrize(
    ("twse", "tpex", "missing"), [({SEP23: None}, None, "TWSE"), (None, {SEP23: None}, "TPEx")],
    ids=["twse-empty", "tpex-empty"],
)
def test_a_new_day_from_one_exchange_only_is_not_stored_and_is_a_failure(tmp_path, twse, tpex, missing):
    svc, store = _service(tmp_path, _Market(twse, tpex))

    stats = svc.refresh_days([SEP23])

    assert stats["dates_fetched"] == 0
    assert [f["date"] for f in stats["failed_dates"]] == [str(SEP23)]
    assert missing in stats["failed_dates"][0]["error"]
    assert store.available_dates() == []


def test_a_new_day_without_daily_bars_and_no_rows_is_a_closed_day(tmp_path):
    svc, store = _service(tmp_path, _Market({SEP25: None}, {SEP25: None}), trading_days=[SEP24, SEP29])

    stats = svc.refresh_days([SEP25])

    assert stats["dates_skipped"] == 1
    assert stats["failed_dates"] == [] and stats["repair_failed_dates"] == []
    assert store.available_dates() == []


def test_a_new_trading_day_with_no_rows_from_either_exchange_is_a_failure(tmp_path):
    svc, store = _service(tmp_path, _Market({SEP23: None}, {SEP23: None}), trading_days=[SEP23])

    stats = svc.refresh_days([SEP23])

    assert [f["date"] for f in stats["failed_dates"]] == [str(SEP23)]
    assert stats["repair_failed_dates"] == []
    assert store.available_dates() == []


def test_a_trading_day_inside_the_stored_range_with_no_rows_is_a_repair_failure(tmp_path):
    svc, store = _service(tmp_path, _Market({SEP23: None}, {SEP23: None}), trading_days=[SEP22, SEP23, SEP24])
    _stored(store, tmp_path, SEP22, _taipei(SEP29, 12))
    _stored(store, tmp_path, SEP24, _taipei(SEP29, 12))

    stats = svc.refresh_days([SEP23])

    assert stats["failed_dates"] == []
    assert [f["date"] for f in stats["repair_failed_dates"]] == [str(SEP23)]
    assert SEP23 not in store.available_dates()


def test_answers_stamped_with_another_day_count_as_no_rows(tmp_path):
    market = _Market()
    market.twse._make = lambda d: _twse_payload(SEP22)
    svc, store = _service(tmp_path, market)

    stats = svc.refresh_days([SEP23])

    assert [f["date"] for f in stats["failed_dates"]] == [str(SEP23)]
    assert "no TWSE rows" in stats["failed_dates"][0]["error"]
    assert store.available_dates() == []


def test_an_error_on_a_new_day_is_a_failure(tmp_path):
    svc, store = _service(tmp_path, _Market(tpex={SEP24: RuntimeError("TPEx down")}))
    _stored(store, tmp_path, SEP23, _taipei(SEP24, 12))

    stats = svc.refresh_days([SEP24])

    assert [f["date"] for f in stats["failed_dates"]] == [str(SEP24)]
    assert stats["repair_failed_dates"] == []


def test_a_unit_the_adapter_rejects_is_a_failure_and_nothing_is_stored(tmp_path):
    market = _Market()
    market.twse._make = lambda d: {**_twse_payload(d), "hints": "億元"}
    svc, store = _service(tmp_path, market)

    stats = svc.refresh_days([SEP23])

    assert [f["date"] for f in stats["failed_dates"]] == [str(SEP23)]
    assert "unit" in stats["failed_dates"][0]["error"]
    assert store.available_dates() == []


@pytest.mark.parametrize(
    "tpex", [{SEP23: RuntimeError("TPEx down")}, {SEP23: None}], ids=["error", "one-sided"]
)
def test_a_failed_refetch_of_a_stored_day_is_a_repair_failure_and_keeps_the_rows(tmp_path, tpex):
    svc, store = _service(tmp_path, _Market(tpex=tpex))
    _stored(store, tmp_path, SEP23, _taipei(SEP23, 16, 30))
    _stored(store, tmp_path, SEP24, _taipei(SEP24, 16, 30))

    stats = svc.refresh_days([SEP23])

    assert stats["failed_dates"] == []
    assert [f["date"] for f in stats["repair_failed_dates"]] == [str(SEP23)]
    assert _trust_nets(store, SEP23) == [100, 100]
    assert store.provisional_dates([SEP23]) == [SEP23]


def test_a_stored_day_that_now_has_no_rows_is_a_repair_failure(tmp_path):
    svc, store = _service(tmp_path, _Market({SEP23: None}, {SEP23: None}))
    _stored(store, tmp_path, SEP23, _taipei(SEP23, 16, 30))

    stats = svc.refresh_days([SEP23])

    assert [f["date"] for f in stats["repair_failed_dates"]] == [str(SEP23)]
    assert _trust_nets(store, SEP23) == [100, 100]


def test_a_gap_day_inside_the_stored_range_from_one_exchange_is_a_repair_failure(tmp_path):
    svc, store = _service(tmp_path, _Market(twse={SEP23: None}))
    _stored(store, tmp_path, SEP22, _taipei(SEP29, 12))
    _stored(store, tmp_path, SEP24, _taipei(SEP29, 12))

    stats = svc.refresh_days([SEP23])

    assert stats["failed_dates"] == []
    assert [f["date"] for f in stats["repair_failed_dates"]] == [str(SEP23)]
    assert SEP23 not in store.available_dates()


def test_a_provisional_day_is_replaced_by_the_refetched_summary(tmp_path):
    svc, store = _service(tmp_path, _Market())
    _stored(store, tmp_path, SEP23, _taipei(SEP23, 16, 30))

    stats = svc.refresh_days([SEP23])

    assert stats["dates_fetched"] == 1
    assert _trust_nets(store, SEP23) == [TPEX_TRUST, TWSE_TRUST]
    assert store.provisional_dates([SEP23]) == []


def test_an_empty_store_asks_for_the_target_only(tmp_path):
    svc, _ = _service(tmp_path, _Market())

    assert svc.dates_to_refresh(SEP30) == [SEP30]


def test_a_run_asks_for_days_after_the_latest_and_provisional_days_up_to_the_target(tmp_path):
    svc, store = _service(tmp_path, _Market())
    _stored(store, tmp_path, SEP22, _taipei(SEP22, 16, 30))
    _stored(store, tmp_path, SEP23, _taipei(SEP24, 12))
    _stored(store, tmp_path, SEP24, _taipei(SEP24, 21, 40))

    assert svc.dates_to_refresh(SEP29) == [SEP22, SEP24, SEP25, SEP28, SEP29]
    assert svc.dates_to_refresh(SEP23) == [SEP22]


def test_a_provisional_day_outside_the_newest_twenty_partitions_is_not_asked_for(tmp_path):
    svc, store = _service(tmp_path, _Market())
    history = [date(2026, 8, 3 + i) for i in range(22)]
    for i, d in enumerate(history):
        _stored(store, tmp_path, d, _taipei(d, 16, 30) if i == 0 else _taipei(date(2026, 9, 1), 12))
    assert store.provisional_dates(history) == [history[0]]

    assert svc.dates_to_refresh(history[-1]) == []


def test_a_backfill_skips_stored_final_days_refetches_provisional_ones_and_fails_empty_trading_days(tmp_path):
    closed = {SEP25: None, SEP28: None, SEP30: None}
    market = _Market(dict(closed), dict(closed))
    svc, store = _service(tmp_path, market, trading_days=[SEP24, SEP29, SEP30])
    _stored(store, tmp_path, SEP29, _taipei(SEP30, 12))
    _stored(store, tmp_path, SEP24, _taipei(SEP24, 16, 30))

    stats = svc.backfill(5, SEP30)

    assert market.twse.calls == [SEP24, SEP25, SEP28, SEP30]
    assert stats["dates_fetched"] == 1
    assert stats["dates_skipped"] == 4
    assert [f["date"] for f in stats["failed_dates"]] == [str(SEP30)]
    assert stats["repair_failed_dates"] == []
    assert store.available_dates() == [SEP24, SEP29]
    assert _trust_nets(store, SEP24) == [TPEX_TRUST, TWSE_TRUST]
    assert _trust_nets(store, SEP29) == [100, 100]


def test_the_backfill_cli_passes_days_and_end(monkeypatch, capsys):
    seen = {}

    def fake_backfill(self, days, end):
        seen["args"] = (days, end)
        return {"dates_fetched": 0}

    monkeypatch.setattr(TaiwanMarketInstitutionalRefreshService, "backfill", fake_backfill)
    monkeypatch.setattr(sys, "argv", ["market_institutional_refresh", "--days", "60", "--end", "2026-09-30"])

    market_institutional_refresh.main()

    assert seen["args"] == (60, SEP30)
    assert json.loads(capsys.readouterr().out) == {"dates_fetched": 0}


@pytest.fixture
def quiet_publish(monkeypatch):
    monkeypatch.setattr(share_capital_store.TaiwanShareCapitalStore, "refresh", lambda self: 0)
    monkeypatch.setattr(enriched_refresh.TaiwanEnrichedRefreshService, "refresh_recent", lambda self, days=5: 0)
    monkeypatch.setattr(TaiwanCorporateActionRefreshService, "refresh_publish_window", lambda self, first, target: {})


def _dated_store(latest: date) -> MagicMock:
    store = MagicMock()
    store.available_dates.return_value = [latest]
    store.provisional_dates.return_value = []
    return store


def _update(market_svc, inst_note_dates: list[str] | None = None, daily_store=None) -> TaiwanDailyUpdateService:
    inst_svc = MagicMock()
    inst_svc.refresh_dates.return_value = {
        "dates_requested": 1, "dates_fetched": 1, "dates_skipped": 0, "total_rows_written": 2, "failed_dates": [],
        "repair_failed_dates": [{"date": d, "error": "x"} for d in inst_note_dates or []],
    }
    return TaiwanDailyUpdateService(
        daily_store=daily_store or _dated_store(SEP29),
        inst_store=_dated_store(SEP29),
        margin_store=_dated_store(SEP30),
        daily_service=MagicMock(),
        inst_service=inst_svc,
        margin_service=MagicMock(),
        market_inst_service=market_svc,
    )


def test_run_update_stores_the_new_day_and_leaves_the_institutional_result_alone(tmp_path, quiet_publish):
    market = _Market()
    svc, store = _service(tmp_path, market)
    _stored(store, tmp_path, SEP29, _taipei(SEP30, 12))

    result = _update(svc).run_update(target_date=SEP30, refresh_daily=False, refresh_margin=False)

    assert market.twse.calls == [SEP30]
    assert store.available_dates() == [SEP29, SEP30]
    assert result.institutional.status == "success"
    assert result.institutional.note is None


def test_run_update_refetches_a_provisional_day_of_the_new_store(tmp_path, quiet_publish):
    market = _Market()
    svc, store = _service(tmp_path, market)
    _stored(store, tmp_path, SEP29, _taipei(SEP29, 16, 30))

    _update(svc).run_update(target_date=SEP30, refresh_daily=False, refresh_margin=False)

    assert market.twse.calls == [SEP29, SEP30]
    assert _trust_nets(store, SEP29) == [TPEX_TRUST, TWSE_TRUST]


def test_run_update_notes_failed_days_without_changing_the_institutional_status(tmp_path, quiet_publish):
    market = _Market(tpex={SEP29: RuntimeError("TPEx down"), SEP30: None})
    svc, store = _service(tmp_path, market)
    _stored(store, tmp_path, SEP29, _taipei(SEP29, 16, 30))

    result = _update(svc, inst_note_dates=["2026-09-26"]).run_update(
        target_date=SEP30, refresh_daily=False, refresh_margin=False
    )

    assert result.institutional.status == "success"
    assert result.institutional.failed_dates == []
    assert result.institutional.note == (
        "Earlier days not refreshed: 2026-09-26; "
        "Market institutional amounts not stored: 2026-09-30; "
        "Earlier market institutional amount days not refreshed: 2026-09-29"
    )
    assert store.available_dates() == [SEP29]
    assert _trust_nets(store, SEP29) == [100, 100]


def test_run_update_uses_the_daily_bars_it_just_stored_to_fail_an_empty_answer(tmp_path, quiet_publish):
    daily_store = MagicMock()
    daily_store.available_dates.return_value = [SEP29]
    daily_store.provisional_dates.return_value = []
    daily_store.incomplete_dates.return_value = []
    daily_store.repair_dates.return_value = []
    daily_svc = MagicMock()

    def store_the_target_day(**_kwargs):
        daily_store.available_dates.return_value = [SEP29, SEP30]
        return {"dates_requested": 1, "dates_fetched": 1, "dates_skipped": 0, "total_rows_written": 2, "failed_dates": []}

    daily_svc.refresh_dates.side_effect = store_the_target_day
    market = _Market({SEP30: None}, {SEP30: None})
    store = TaiwanMarketInstitutionalStore(data_dir=tmp_path / "market_institutional")
    _stored(store, tmp_path, SEP29, _taipei(SEP30, 12))
    svc = _update(None, daily_store=daily_store)
    svc.daily_service = daily_svc
    svc.market_inst_service = TaiwanMarketInstitutionalRefreshService(
        store=store, calendar=TaiwanTradingCalendar(), fetch_json=market.fetch, daily_store=svc.daily_store
    )

    result = svc.run_update(target_date=SEP30, refresh_margin=False)

    assert result.institutional.status == "success"
    assert result.institutional.note == "Market institutional amounts not stored: 2026-09-30"


def test_run_update_survives_a_market_amount_step_that_raises(quiet_publish):
    market_svc = MagicMock()
    market_svc.refresh_through.side_effect = RuntimeError("disk full")

    result = _update(market_svc).run_update(target_date=SEP30, refresh_daily=False, refresh_margin=False)

    assert result.institutional.status == "success"
    assert "Market institutional amounts not refreshed: disk full" in result.institutional.note


def test_run_update_skips_market_amounts_when_institutional_is_off(quiet_publish):
    market_svc = MagicMock()

    _update(market_svc).run_update(
        target_date=SEP30, refresh_daily=False, refresh_institutional=False, refresh_margin=False
    )

    market_svc.refresh_through.assert_not_called()


def _fixture_store(tmp_path: Path) -> TaiwanMarketInstitutionalStore:
    svc, store = _service(tmp_path, _Market())
    assert svc.refresh_days([SEP30])["dates_fetched"] == 1
    return store


def _current_store(d: date) -> MagicMock:
    store = MagicMock()
    store.available_dates.return_value = [d]
    store.read_range.return_value = pl.DataFrame()
    return store


def _intel(store: TaiwanMarketInstitutionalStore, current_on: date | None = None) -> TaiwanMarketIntelligenceService:
    if current_on is None:
        empty = MagicMock()
        empty.available_dates.return_value = []
        empty.read_range.return_value = pl.DataFrame()
        daily = inst = margin = empty
        security_master = MagicMock()
        security_master.to_dataframe.return_value = pl.DataFrame()
    else:
        daily = _current_store(current_on)
        daily.provisional_dates.return_value = []
        daily.read_range.return_value = pl.DataFrame({
            "symbol": ["A.TWSE", "B.TPEX"],
            "date": [current_on, current_on],
            "open": [100.0, 100.0],
            "high": [101.0, 101.0],
            "low": [99.0, 99.0],
            "close": [100.0, 100.0],
            "volume": [1000.0, 1000.0],
            "amount": [100000.0, 100000.0],
            "quote_ts": [None, None],
        })
        inst, margin = _current_store(current_on), _current_store(current_on)
        security_master = MagicMock()
        security_master.to_dataframe.return_value = pl.DataFrame({
            "symbol": ["A.TWSE", "B.TPEX"],
            "exchange": ["TWSE", "TPEX"],
            "instrument_type": ["stock", "stock"],
            "listing_status": ["active", "active"],
            "listing_date": [str(current_on), str(current_on)],
            "updated_at": [
                f"{current_on.isoformat()}T08:00:00+08:00",
                f"{current_on.isoformat()}T08:00:00+08:00",
            ],
        })
        security_master.get_instrument.return_value = None
    corporate_actions = MagicMock()
    corporate_actions.read.return_value = pl.DataFrame()
    return TaiwanMarketIntelligenceService(
        daily_store=daily,
        inst_store=inst,
        margin_store=margin,
        market_inst_store=store,
        security_master=security_master,
        corporate_actions=corporate_actions,
    )


def test_the_market_aggregate_reads_the_official_amounts(tmp_path):
    snapshot = _intel(_fixture_store(tmp_path)).get_snapshot(SEP30)
    inst = snapshot.institutional

    assert inst.trade_date == str(SEP30)
    assert inst.status == "current"
    assert inst.foreign_net_amount == TWSE_FOREIGN + TPEX_FOREIGN
    assert inst.investment_trust_net_amount == TWSE_TRUST + TPEX_TRUST
    assert inst.dealer_net_amount == TWSE_DEALER + TPEX_DEALER
    assert inst.total_net_amount == TWSE_TOTAL + TPEX_TOTAL
    assert inst.has_discrepancy is False
    assert inst.twse.foreign_net_amount == TWSE_FOREIGN
    assert inst.tpex.investment_trust_net_amount == TPEX_TRUST
    assert round(inst.foreign_net_amount / 1e8, 2) == 390.16
    assert round(inst.investment_trust_net_amount / 1e8, 2) == 60.87
    assert snapshot.data_quality.institutional_amount.status == "current"
    assert snapshot.data_quality.institutional_amount.as_of == str(SEP30)


def test_the_market_aggregate_leaves_foreign_dealers_out_of_foreign(tmp_path):
    market = _Market()
    market.twse._make = lambda d: _synthetic_twse(d=d)
    market.tpex._make = lambda d: _synthetic_tpex(d=d)
    svc, store = _service(tmp_path, market)
    assert svc.refresh_days([SEP30])["dates_fetched"] == 1

    inst = _intel(store).get_snapshot(SEP30).institutional

    assert inst.foreign_net_amount == 200
    assert inst.investment_trust_net_amount == 40
    assert inst.dealer_net_amount == 100
    assert inst.total_net_amount == 340
    assert inst.foreign_net_amount + inst.investment_trust_net_amount + inst.dealer_net_amount == inst.total_net_amount
    assert inst.has_discrepancy is False


def test_an_inconsistent_summary_keeps_the_official_total_and_is_flagged(tmp_path):
    market = _Market()
    market.twse._make = lambda d: _synthetic_twse({**SYNTHETIC_TWSE, "investment_trust": (300, 280, 25)}, d)
    market.tpex._make = lambda d: _synthetic_tpex(d=d)
    svc, store = _service(tmp_path, market)
    assert svc.refresh_days([SEP30])["dates_fetched"] == 1

    inst = _intel(store).get_snapshot(SEP30).institutional

    assert inst.has_discrepancy is True
    assert inst.twse.has_discrepancy is True and inst.tpex.has_discrepancy is False
    assert inst.investment_trust_net_amount == 45
    assert inst.total_net_amount == 340
    flagged = store.read_day(SEP30).filter(pl.col("has_discrepancy"))
    assert sorted(flagged["category"].to_list()) == ["investment_trust", "total"]


def test_a_day_without_a_stored_summary_has_null_amounts_but_can_still_be_complete(tmp_path):
    store = _fixture_store(tmp_path)

    before = _intel(store, current_on=SEP29).get_snapshot(SEP29)
    after = _intel(store, current_on=OCT01).get_snapshot(OCT01)

    for snapshot in (before, after):
        inst = snapshot.institutional
        assert inst.foreign_net_amount is None
        assert inst.investment_trust_net_amount is None
        assert inst.dealer_net_amount is None
        assert inst.total_net_amount is None
        assert inst.twse is None and inst.tpex is None
        assert inst.trade_date is None
        assert snapshot.data_quality.overall_status == "complete"
    assert before.institutional.status == "unavailable"
    assert before.data_quality.institutional_amount.status == "unavailable"
    assert before.data_quality.institutional_amount.as_of is None
    assert after.institutional.status == "stale"
    assert after.data_quality.institutional_amount.status == "stale"
    assert after.data_quality.institutional_amount.as_of == str(SEP30)


def test_a_target_day_with_every_dataset_and_amounts_is_complete(tmp_path):
    snapshot = _intel(_fixture_store(tmp_path), current_on=SEP30).get_snapshot(SEP30)

    assert snapshot.data_quality.overall_status == "complete"
    assert snapshot.data_quality.institutional_amount.status == "current"


def test_a_stored_day_missing_one_exchange_has_null_amounts(tmp_path):
    store = TaiwanMarketInstitutionalStore(data_dir=tmp_path / "market_institutional")
    rows = TwseMarketInstitutionalAdapter().parse_payload(_twse_payload(), SEP30, "u")
    store.write_day(SEP30, amounts_frame(SEP30, rows))

    snapshot = _intel(store, current_on=SEP30).get_snapshot(SEP30)

    assert snapshot.institutional.foreign_net_amount is None
    assert snapshot.institutional.twse is None
    assert snapshot.institutional.status == "stale"
    assert snapshot.data_quality.institutional_amount.status == "stale"
    assert snapshot.data_quality.overall_status == "complete"


def test_missing_amounts_keep_the_market_context_in_ai_research(tmp_path):
    empty = TaiwanMarketInstitutionalStore(data_dir=tmp_path / "market_institutional")
    ctx = TaiwanStockResearchContextService(market_intel_svc=_intel(empty, current_on=SEP30)).get_research_context(
        "2330", SEP30
    )

    payload, _, missing = build_evidence_registry(ctx, None)

    assert ctx.market_context.institutional_market_net_amount is None
    assert ctx.market_context.status == "complete"
    assert "market_context" in payload
    assert "market_context" not in missing


def test_the_share_count_fields_are_gone_from_the_aggregate():
    fields = set(market_intelligence.InstitutionalMarketAggregate.model_fields)

    assert {"foreign_net", "investment_trust_net", "dealer_net", "total_net", "row_count"}.isdisjoint(fields)


def test_the_research_context_carries_the_market_total_amount(tmp_path):
    store = _fixture_store(tmp_path)
    ctx = TaiwanStockResearchContextService(market_intel_svc=_intel(store)).get_research_context("2330", SEP30)

    assert ctx.market_context.institutional_market_net_amount == TWSE_TOTAL + TPEX_TOTAL
    assert "institutional_market_net" not in ctx.market_context.model_dump()


def test_the_api_serves_amounts_and_no_share_counts(tmp_path, monkeypatch):
    store = _fixture_store(tmp_path)
    monkeypatch.setattr(market_intelligence, "TaiwanMarketInstitutionalStore", lambda: store)
    client = TestClient(app, client=("127.0.0.1", 50000))

    body = client.get("/api/taiwan/market-intelligence?date=2026-09-30").json()

    inst = body["institutional"]
    assert inst["foreign_net_amount"] == TWSE_FOREIGN + TPEX_FOREIGN
    assert inst["tpex"]["dealer_net_amount"] == TPEX_DEALER
    assert inst["has_discrepancy"] is False
    assert "foreign_net" not in inst and "total_net" not in inst
    assert body["data_quality"]["institutional_amount"]["status"] == "current"
