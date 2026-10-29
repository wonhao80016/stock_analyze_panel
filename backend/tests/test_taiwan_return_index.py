"""TWSE total return index archived from the MI_INDEX response the daily snapshot already fetches."""
import logging
from datetime import date, datetime, timedelta, timezone

import httpx
import polars as pl
import pytest

from app.taiwan import return_index
from app.taiwan.daily_refresh import TaiwanDailyRefreshService
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.providers.snapshot_provider import OfficialDailySnapshotAdapter
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.return_index import (
    TAIEX_TOTAL_RETURN,
    ArchiveOutcome,
    TaiwanReturnIndexStore,
    archive_mi_index_return_index,
    read_return_index_series,
)
from tests.test_taiwan_snapshot_daily import _tpex_payload, mock_security_master

DAY = date(2026, 9, 24)
STAMP = "20260924"
URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20260924&type=ALL&response=json"
RETRIEVED = datetime(2026, 9, 24, 16, 30, tzinfo=timezone(timedelta(hours=8)))
INDEX_FIELDS = ["報酬指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]
UP = "<p style ='color:red'>+</p>"


def _price_table() -> dict:
    return {
        "title": "價格指數(臺灣證券交易所)",
        "fields": ["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"],
        "data": [["發行量加權股價指數", "28,123.45", UP, "580.12", "2.11", ""]],
    }


def _return_table(title: str = "報酬指數(臺灣證券交易所)", fields=None, rows=None) -> dict:
    return {
        "title": title,
        "fields": INDEX_FIELDS if fields is None else fields,
        "data": rows if rows is not None else [
            ["寶島股價報酬指數", "80,974.14", UP, "1,720.72", "2.17", ""],
            [TAIEX_TOTAL_RETURN, "108,763.96", UP, "2,172.18", "2.04", ""],
            ["臺灣50報酬指數", "--", "", "", "", ""],
        ],
    }


def _cross_market_return_table() -> dict:
    return {
        "title": "報酬指數(跨市場)",
        "fields": INDEX_FIELDS,
        "data": [["臺灣生技報酬指數", "4,656.02", UP, "51.30", "1.11", ""]],
    }


def _tip_return_table() -> dict:
    return {
        "title": "報酬指數(臺灣指數公司)",
        "fields": INDEX_FIELDS,
        "data": [["漲升股利150報酬指數", "35,344.32", UP, "612.40", "1.76", ""]],
    }


def _quote_table() -> dict:
    return {
        "title": "每日收盤行情(全部)",
        "fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
        "data": [["2330", "台積電", "15,025,832", "52,492", "36,465,015,980", "2,440.00", "2,445.00", "2,410.00", "2,420.00"]],
    }


def _mi_index(*tables: dict, stamped: str = STAMP) -> dict:
    return {"stat": "OK", "date": stamped, "tables": list(tables)}


def _full_mi_index(stamped: str = STAMP) -> dict:
    """Layout of the 2026-10-28 MI_INDEX response: no date prefix in titles, three return index tables."""
    return _mi_index(
        _price_table(), _return_table(), _cross_market_return_table(), _tip_return_table(), _quote_table(),
        stamped=stamped,
    )


@pytest.fixture
def store(tmp_path) -> TaiwanReturnIndexStore:
    return TaiwanReturnIndexStore(tmp_path / "return_index")


def _adapter(store, payloads: dict[str, dict]):
    """Adapter whose client answers TWSE with payloads["twse"] and TPEx with payloads["tpex"], counting calls."""
    calls = {"twse": 0, "tpex": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        side = "tpex" if "tpex" in request.url.host else "twse"
        calls[side] += 1
        return httpx.Response(200, json=payloads[side])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = OfficialDailySnapshotAdapter(
        security_master=mock_security_master(), client=client, return_index_store=store
    )
    return adapter, calls


# ── Table present ─────────────────────────────────────────────────

def test_present_table_is_archived_with_every_numeric_row(store):
    outcome = archive_mi_index_return_index(_full_mi_index(), DAY, URL, store, RETRIEVED)

    assert outcome is ArchiveOutcome.ADDED
    assert store.partition_path(DAY).exists()
    rows = store.read_range(DAY, DAY).to_dicts()
    assert [(r["index_name"], r["close"]) for r in rows] == [
        ("寶島股價報酬指數", 80974.14),
        (TAIEX_TOTAL_RETURN, 108763.96),
    ]
    assert {r["source"] for r in rows} == {"twse_mi_index"}
    assert {r["source_url"] for r in rows} == {URL}
    assert {r["date"] for r in rows} == {DAY}
    assert rows[0]["retrieved_at"] == RETRIEVED


def test_only_the_twse_return_index_table_is_read(store):
    archive_mi_index_return_index(_full_mi_index(), DAY, URL, store, RETRIEVED)

    names = set(store.read_range(DAY, DAY)["index_name"].to_list())
    assert names == {"寶島股價報酬指數", TAIEX_TOTAL_RETURN}


def test_columns_are_located_by_name_not_position(store):
    fields = ["報酬指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記", "收盤指數"]
    table = _return_table(fields=fields, rows=[[TAIEX_TOTAL_RETURN, UP, "2,172.18", "2.04", "", "108,763.96"]])

    outcome = archive_mi_index_return_index(_mi_index(table), DAY, URL, store, RETRIEVED)

    assert outcome is ArchiveOutcome.ADDED
    assert read_return_index_series(DAY, DAY, store=store)["close"].to_list() == [108763.96]


# ── Table missing ─────────────────────────────────────────────────

def test_missing_table_is_logged_and_stores_nothing(store, caplog):
    with caplog.at_level(logging.WARNING, logger=return_index.__name__):
        outcome = archive_mi_index_return_index(_mi_index(_price_table(), _quote_table()), DAY, URL, store)

    assert outcome is None
    assert not store.partition_path(DAY).exists()
    assert store.read_range(DAY, DAY).is_empty()
    assert any("no 報酬指數" in r.getMessage() for r in caplog.records)


# ── Title or columns changed ──────────────────────────────────────

@pytest.mark.parametrize("table, expected", [
    (_return_table(title="Total Return Index(臺灣證券交易所)"), "no 報酬指數"),
    (_return_table(fields=["報酬指數", "收盤", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]),
     "unexpected return index fields"),
    (_return_table(fields=["名稱", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]),
     "unexpected return index fields"),
    (_return_table(rows=[["發行量加權股價報酬指數(新)", "108,763.96", UP, "2,172.18", "2.04", ""]]),
     f"{TAIEX_TOTAL_RETURN} row is missing"),
    (_return_table(rows=[[TAIEX_TOTAL_RETURN, "--", "", "", "", ""]]), "close value is missing"),
])
def test_a_changed_table_fails_loudly_instead_of_storing_a_wrong_value(store, caplog, table, expected):
    with caplog.at_level(logging.WARNING, logger=return_index.__name__):
        outcome = archive_mi_index_return_index(_mi_index(_price_table(), table), DAY, URL, store)

    assert outcome is None
    assert not store.partition_path(DAY).exists()
    assert any(expected in r.getMessage() for r in caplog.records)


# ── Date stamp ────────────────────────────────────────────────────

@pytest.mark.parametrize("stamped", ["20260923", ""])
def test_a_response_not_stamped_with_the_requested_day_is_not_archived(store, stamped):
    assert archive_mi_index_return_index(_full_mi_index(stamped), DAY, URL, store) is None
    assert not store.root.exists()


def test_fetch_twse_does_not_archive_a_response_stamped_with_another_day(store):
    adapter, _ = _adapter(store, {"twse": _full_mi_index("20260923"), "tpex": _tpex_payload(STAMP)})

    assert adapter.fetch_twse(DAY).is_empty()
    assert not store.root.exists()


# ── Idempotency ───────────────────────────────────────────────────

def test_rewriting_the_same_closes_leaves_the_partition_untouched(store, monkeypatch):
    archive_mi_index_return_index(_full_mi_index(), DAY, URL, store, RETRIEVED)
    writes: list = []
    monkeypatch.setattr(return_index, "write_atomically", lambda *args: writes.append(args))

    later = RETRIEVED + timedelta(hours=5)
    assert archive_mi_index_return_index(_full_mi_index(), DAY, URL, store, later) is ArchiveOutcome.UNCHANGED
    assert writes == []
    assert store.read_range(DAY, DAY)["retrieved_at"].to_list() == [RETRIEVED, RETRIEVED]


def test_different_closes_replace_the_day_and_log_a_warning(store, caplog):
    archive_mi_index_return_index(_full_mi_index(), DAY, URL, store, RETRIEVED)
    revised = _mi_index(_return_table(rows=[[TAIEX_TOTAL_RETURN, "108,800.00", UP, "2,208.22", "2.07", ""]]))
    later = RETRIEVED + timedelta(hours=5)

    with caplog.at_level(logging.WARNING, logger=return_index.__name__):
        outcome = archive_mi_index_return_index(revised, DAY, URL, store, later)

    assert outcome is ArchiveOutcome.REVISED
    rows = store.read_range(DAY, DAY).to_dicts()
    assert [(r["index_name"], r["close"], r["retrieved_at"]) for r in rows] == [(TAIEX_TOTAL_RETURN, 108800.0, later)]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("108763.96 -> 108800.0" in m for m in warnings)


# ── Failure isolation ─────────────────────────────────────────────

class _BrokenStore(TaiwanReturnIndexStore):
    def write_day(self, *args, **kwargs):
        raise PermissionError("replace blocked")


@pytest.mark.parametrize("twse_payload, broken_store", [
    (_full_mi_index(), True),
    (_mi_index(_return_table(fields=["?"]), _quote_table()), False),
    (_mi_index(_return_table(rows="not rows"), _quote_table()), False),
])
def test_an_archive_failure_does_not_affect_the_closing_quotes(tmp_path, twse_payload, broken_store):
    store = (_BrokenStore if broken_store else TaiwanReturnIndexStore)(tmp_path / "return_index")
    adapter, _ = _adapter(store, {"twse": twse_payload, "tpex": _tpex_payload(STAMP)})
    daily = TaiwanDailyStore(tmp_path / "daily")
    service = TaiwanDailyRefreshService(store=daily, snapshot_adapter=adapter, calendar=TaiwanTradingCalendar())

    stats = service.refresh_dates(DAY, DAY)

    assert stats["dates_fetched"] == 1
    assert stats["failed_dates"] == []
    assert sorted(daily.read_range(None, DAY, DAY)["symbol"].to_list()) == ["2330.TWSE", "6488.TPEX"]
    assert not store.partition_path(DAY).exists()


# ── Request budget ────────────────────────────────────────────────

def test_archiving_adds_no_request(store):
    without_table, calls_before = _adapter(store, {"twse": _mi_index(_quote_table()), "tpex": _tpex_payload(STAMP)})
    with_table, calls_after = _adapter(store, {"twse": _full_mi_index(), "tpex": _tpex_payload(STAMP)})

    without_table.fetch_date(DAY)
    assert not store.partition_path(DAY).exists()
    with_table.fetch_date(DAY)

    assert store.partition_path(DAY).exists()
    assert calls_before == calls_after == {"twse": 1, "tpex": 1}


# ── Series read ───────────────────────────────────────────────────

def test_series_read_returns_only_archived_days_within_the_period(store):
    for day, close in [(date(2026, 9, 22), "52,000.00"), (DAY, "52,345.67"), (date(2026, 9, 25), "52,500.00")]:
        payload = _mi_index(_return_table(rows=[
            [TAIEX_TOTAL_RETURN, close, "", "", "", ""],
            ["寶島股價指數", "30,000.00", "", "", "", ""],
        ]), stamped=day.strftime("%Y%m%d"))
        archive_mi_index_return_index(payload, day, URL, store, RETRIEVED)

    series = read_return_index_series(date(2026, 9, 23), date(2026, 9, 25), store=store)

    assert series.columns == ["date", "close"]
    assert series.to_dicts() == [{"date": DAY, "close": 52345.67}, {"date": date(2026, 9, 25), "close": 52500.0}]
    other = read_return_index_series(date(2026, 9, 22), date(2026, 9, 22), index_name="寶島股價指數", store=store)
    assert other["close"].to_list() == [30000.0]
    empty = read_return_index_series(date(2026, 10, 1), date(2026, 10, 31), store=store)
    assert empty.is_empty()
    assert empty.schema == pl.Schema({"date": pl.Date, "close": pl.Float64})
