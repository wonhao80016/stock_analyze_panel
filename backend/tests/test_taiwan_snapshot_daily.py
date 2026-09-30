"""Tests for OfficialDailySnapshotAdapter and daily snapshot refresh orchestration.

Validates:
  1. TWSE normal row parsing
  2. TWSE ETF row parsing
  3. TWSE "--" no-trade row parsing
  4. TWSE comma and whitespace numeric parsing
  5. TPEx normal row parsing
  6. TPEx ETF row parsing
  7. TPEx "--" no-trade row parsing
  8. TPEx comma numeric parsing
  9. Schema mismatch handling (fails safely, returns empty)
  10. Security Master allowlist filtering (rejects warrants, rights, unknown codes)
  11. 6488.TPEX 2026-08-28 exact regression (preserves 11_772_540 shares, 11_478_474_997 TWD)
  12. One-day full-market refresh HTTP budget (exactly 1 TWSE + 1 TPEx = 2 requests)
  13. Multi-day catch-up HTTP budget (3 missing dates = 6 requests)
  14. Idempotency (already-persisted date skips network requests)
  15. Failure isolation (a failed date is recorded, not written and not taken for a holiday)
  16. A one-sided snapshot (TWSE traded, TPEx failed or empty) raises instead of returning half a market
  17. A day without TWSE rows is treated as closed and TPEx is not requested
  18. A payload stamped with another trading date yields no rows
  19. A stored partition missing one exchange is refetched
  20. refresh_dates stops at the first failed new date; failures inside the stored range do not block new days
"""
from datetime import date
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from app.taiwan.daily_refresh import TaiwanDailyRefreshService
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import TaiwanDailyUpdateService
from app.taiwan.market_institutional_refresh import TaiwanMarketInstitutionalRefreshService
from app.taiwan.providers.snapshot_provider import OfficialDailySnapshotAdapter
from app.taiwan.realtime.calendar import TaiwanTradingCalendar
from app.taiwan.universe import TaiwanSecurityMaster


@pytest.fixture(autouse=True)
def _no_market_amount_requests(monkeypatch):
    monkeypatch.setattr(TaiwanMarketInstitutionalRefreshService, "refresh_through", lambda self, target: {})


# ── Sample Fixtures ───────────────────────────────────────────────

def mock_security_master():
    master = MagicMock(spec=TaiwanSecurityMaster)
    # Provide supported stocks and ETFs
    df_stocks = pl.DataFrame({
        "symbol": ["2330.TWSE", "6488.TPEX", "8069.TPEX"],
        "exchange": ["TWSE", "TPEX", "TPEX"],
        "name": ["台積電", "環球晶", "元太"],
        "instrument_type": ["stock", "stock", "stock"],
    })
    df_etfs = pl.DataFrame({
        "symbol": ["0050.TWSE", "00679B.TPEX"],
        "exchange": ["TWSE", "TPEX"],
        "name": ["元大台灣50", "元大美債20年"],
        "instrument_type": ["etf", "etf"],
    })
    master.to_provider_dataframe.side_effect = lambda asset_type: df_stocks if asset_type == "stock" else df_etfs
    return master


def _rows(symbols: list[str], d: date = date(2026, 8, 28)) -> pl.DataFrame:
    n = len(symbols)
    return pl.DataFrame({
        "symbol": symbols,
        "date": [d] * n,
        "open": [10.0] * n,
        "high": [10.5] * n,
        "low": [9.5] * n,
        "close": [10.0] * n,
        "volume": [1000.0] * n,
        "amount": [10000.0] * n,
        "quote_ts": pl.Series([None] * n, dtype=pl.Int64),
    })


def _twse_payload(stamped: str) -> dict:
    return {"stat": "OK", "date": stamped, "tables": [{
        "title": "每日收盤行情(全部)",
        "fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
        "data": [["2330", "台積電", "15,025,832", "52,492", "36,465,015,980", "2,440.00", "2,445.00", "2,410.00", "2,420.00"]],
    }]}


def _tpex_payload(stamped: str) -> dict:
    return {"stat": "ok", "date": stamped, "tables": [{
        "title": "上櫃股票行情",
        "fields": ["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數", "成交金額(元)"],
        "data": [["6488", "環球晶", "972.00", "+14.00", "967.00", "1015.00", "949.00", "975.02", "11,772,540", "11,478,474,997"]],
    }]}


# ── Parser Unit Tests ─────────────────────────────────────────────

def test_twse_parsing_normal_and_etf():
    master = mock_security_master()
    adapter = OfficialDailySnapshotAdapter(security_master=master)

    fields = [
        "證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額",
        "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差",
        "最後揭示買價", "最後揭示買量", "最後揭示賣價", "最後揭示賣量", "本益比",
    ]
    rows = [
        # Normal stock: 2330
        ["2330", "台積電", "15,025,832", "52,492", "36,465,015,980", "2,440.00", "2,445.00", "2,410.00", "2,420.00", "+", "10.00", "2,420.00", "30", "2,425.00", "200", "28.05"],
        # ETF: 0050
        ["0050", "元大台灣50", "78,158,934", "70,286", "8,369,173,993", "107.10", "107.35", "106.70", "106.95", "+", "0.90", "106.90", "266", "106.95", "17", "0.00"],
        # Warrant: 041589 (should be filtered out by allowlist)
        ["041589", "台積電群益5A購02", "10,000", "1", "35,900", "3.59", "3.59", "3.59", "3.59", "+", "0.10", "3.50", "10", "3.60", "10", "0.00"],
    ]

    target_d = date(2026, 8, 28)
    df = adapter._parse_twse_table(rows, fields, target_d)

    assert df.height == 2
    symbols = df["symbol"].to_list()
    assert "2330.TWSE" in symbols
    assert "0050.TWSE" in symbols
    assert "041589.TWSE" not in symbols

    r_2330 = df.filter(pl.col("symbol") == "2330.TWSE").to_dicts()[0]
    assert r_2330["open"] == 2440.0
    assert r_2330["high"] == 2445.0
    assert r_2330["low"] == 2410.0
    assert r_2330["close"] == 2420.0
    assert r_2330["volume"] == 15025832.0
    assert r_2330["amount"] == 36465015980.0
    assert r_2330["quote_ts"] is None


def test_twse_parsing_no_trade_row():
    master = mock_security_master()
    adapter = OfficialDailySnapshotAdapter(security_master=master)

    fields = ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"]
    rows = [
        # Suspended / no trade
        ["2330", "台積電", "0", "0", "0", "--", "--", "--", "--"],
    ]
    target_d = date(2026, 8, 28)
    df = adapter._parse_twse_table(rows, fields, target_d)

    assert df.height == 1
    r = df.to_dicts()[0]
    assert r["symbol"] == "2330.TWSE"
    assert r["open"] is None
    assert r["high"] is None
    assert r["low"] is None
    assert r["close"] is None
    assert r["volume"] == 0.0
    assert r["amount"] == 0.0


def test_tpex_parsing_normal_and_etf():
    master = mock_security_master()
    adapter = OfficialDailySnapshotAdapter(security_master=master)

    fields = [
        "代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價",
        "成交股數", "成交金額(元)", "成交筆數", "最後買價", "最後買量(張數)",
        "最後賣價", "最後賣量(張數)", "發行股數", "次日 參考價", "次日 漲停價", "次日 跌停價"
    ]
    rows = [
        # 6488 (regression check for exact numbers)
        ["6488", "環球晶", "972.00", "+14.00", "967.00", "1015.00", "949.00", "975.02", "11,772,540", "11,478,474,997", "22,806", "971.00", "1", "972.00", "11", "478,113,725", "972.00", "1065.00", "875.00"],
        # 00679B ETF
        ["00679B", "元大美債20年", "25.85", "-0.05", "25.92", "25.92", "25.81", "25.86", "12,808,955", "331,260,338", "5,123", "25.84", "10", "25.85", "50", "1,000,000,000", "25.85", "28.43", "23.27"],
        # Unsupported warrant or code
        ["71234P", "環球晶凱基5A", "1.20", "+0.02", "1.18", "1.22", "1.18", "1.20", "50,000", "60,000", "10", "1.19", "5", "1.21", "5", "0", "1.20", "1.32", "1.08"],
    ]

    target_d = date(2026, 8, 28)
    df = adapter._parse_tpex_table(rows, fields, target_d)

    assert df.height == 2
    symbols = df["symbol"].to_list()
    assert "6488.TPEX" in symbols
    assert "00679B.TPEX" in symbols
    assert "71234P.TPEX" not in symbols

    r_6488 = df.filter(pl.col("symbol") == "6488.TPEX").to_dicts()[0]
    assert r_6488["open"] == 967.0
    assert r_6488["high"] == 1015.0
    assert r_6488["low"] == 949.0
    assert r_6488["close"] == 972.0
    # Must preserve exact shares and amount (not truncated to lots or thousand TWD)
    assert r_6488["volume"] == 11772540.0
    assert r_6488["amount"] == 11478474997.0


def test_tpex_parsing_no_trade_row():
    master = mock_security_master()
    adapter = OfficialDailySnapshotAdapter(security_master=master)

    fields = ["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數", "成交金額(元)"]
    rows = [
        ["6488", "環球晶", "---", "0.00", "---", "---", "---", "---", "0", "0"],
    ]
    target_d = date(2026, 8, 28)
    df = adapter._parse_tpex_table(rows, fields, target_d)

    assert df.height == 1
    r = df.to_dicts()[0]
    assert r["symbol"] == "6488.TPEX"
    assert r["open"] is None
    assert r["high"] is None
    assert r["low"] is None
    assert r["close"] is None
    assert r["volume"] == 0.0
    assert r["amount"] == 0.0


def test_schema_mismatch_fails_safely():
    master = mock_security_master()
    adapter = OfficialDailySnapshotAdapter(security_master=master)

    # Missing mandatory column
    broken_fields = ["代號", "名稱", "收盤"]
    broken_rows = [["6488", "環球晶", "972.00"]]
    df = adapter._parse_tpex_table(broken_rows, broken_fields, date(2026, 8, 28))
    assert df.is_empty()


# ── Snapshot Completeness ─────────────────────────────────────────

def test_fetch_date_rejects_twse_rows_when_tpex_fails():
    """TWSE traded, so the day is only complete with TPEx; half a market stored as the day hides the gap."""
    adapter = OfficialDailySnapshotAdapter(security_master=mock_security_master())
    with (
        patch.object(adapter, "fetch_twse", return_value=_rows(["2330.TWSE"])),
        patch.object(adapter, "fetch_tpex", side_effect=RuntimeError("incomplete chunked read")),
        pytest.raises(RuntimeError, match="TPEx"),
    ):
        adapter.fetch_date(date(2026, 8, 28))


def test_fetch_date_rejects_twse_rows_when_tpex_has_none():
    adapter = OfficialDailySnapshotAdapter(security_master=mock_security_master())
    with (
        patch.object(adapter, "fetch_twse", return_value=_rows(["2330.TWSE"])),
        patch.object(adapter, "fetch_tpex", return_value=pl.DataFrame()),
        pytest.raises(RuntimeError, match="TPEx"),
    ):
        adapter.fetch_date(date(2026, 8, 28))


def test_fetch_date_treats_a_day_without_twse_rows_as_closed():
    """TWSE decides whether the day traded, so whatever TPEx answers for a holiday is never stored."""
    adapter = OfficialDailySnapshotAdapter(security_master=mock_security_master())
    with (
        patch.object(adapter, "fetch_twse", return_value=pl.DataFrame()),
        patch.object(adapter, "fetch_tpex", return_value=_rows(["6488.TPEX"])) as tpex,
    ):
        assert adapter.fetch_date(date(2026, 9, 25)).is_empty()
    tpex.assert_not_called()


def test_fetch_date_propagates_a_twse_failure():
    adapter = OfficialDailySnapshotAdapter(security_master=mock_security_master())
    with (
        patch.object(adapter, "fetch_twse", side_effect=RuntimeError("TWSE down")),
        patch.object(adapter, "fetch_tpex", return_value=_rows(["6488.TPEX"])),
        pytest.raises(RuntimeError, match="TWSE down"),
    ):
        adapter.fetch_date(date(2026, 8, 28))


def test_fetch_date_combines_both_exchanges():
    adapter = OfficialDailySnapshotAdapter(security_master=mock_security_master())
    with (
        patch.object(adapter, "fetch_twse", return_value=_rows(["2330.TWSE"])),
        patch.object(adapter, "fetch_tpex", return_value=_rows(["6488.TPEX"])),
    ):
        df = adapter.fetch_date(date(2026, 8, 28))
    assert df["symbol"].to_list() == ["2330.TWSE", "6488.TPEX"]


@pytest.mark.parametrize("fetch, payload", [("fetch_twse", _twse_payload), ("fetch_tpex", _tpex_payload)])
def test_a_payload_stamped_with_another_date_yields_no_rows(fetch, payload):
    """Rows the exchange published for another trading day must not be relabelled as the requested day."""
    adapter = OfficialDailySnapshotAdapter(security_master=mock_security_master())
    with patch.object(adapter, "_request_json", return_value=payload("20260923")):
        assert getattr(adapter, fetch)(date(2026, 9, 24)).is_empty()
        assert getattr(adapter, fetch)(date(2026, 9, 23)).height == 1


# ── Refresh Service & HTTP Budget Tests ───────────────────────────

def test_refresh_dates_one_day_budget():
    """One day full-market refresh calls TWSE once and TPEx once."""
    mock_store = MagicMock(spec=TaiwanDailyStore)
    mock_store.available_dates.return_value = []
    mock_store.write_batch.return_value = 2330

    mock_adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    mock_adapter.fetch_date.return_value = pl.DataFrame({
        "symbol": ["2330.TWSE", "6488.TPEX"],
        "date": [date(2026, 8, 28), date(2026, 8, 28)],
        "open": [2440.0, 967.0],
        "high": [2445.0, 1015.0],
        "low": [2410.0, 949.0],
        "close": [2420.0, 972.0],
        "volume": [15025832.0, 11772540.0],
        "amount": [36465015980.0, 11478474997.0],
        "quote_ts": [None, None],
    })

    svc = TaiwanDailyRefreshService(
        store=mock_store,
        snapshot_adapter=mock_adapter,
        calendar=TaiwanTradingCalendar(),
    )

    stats = svc.refresh_dates(date(2026, 8, 28), date(2026, 8, 28))
    assert stats["dates_requested"] == 1
    assert stats["dates_fetched"] == 1
    assert stats["dates_skipped"] == 0
    assert stats["total_rows_written"] == 2330
    assert stats["failed_dates"] == []

    # Exactly one call to snapshot fetch_date
    mock_adapter.fetch_date.assert_called_once_with(date(2026, 8, 28))
    mock_store.write_batch.assert_called_once()


def test_refresh_dates_catchup_three_days():
    """Three missing trading days catch-up dispatches exactly 3 snapshot calls."""
    mock_store = MagicMock(spec=TaiwanDailyStore)
    mock_store.available_dates.return_value = []
    mock_store.write_batch.return_value = 2330

    mock_adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    mock_adapter.fetch_date.return_value = pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "date": [date(2026, 8, 28)],
        "open": [2440.0], "high": [2445.0], "low": [2410.0], "close": [2420.0],
        "volume": [15025832.0], "amount": [36465015980.0], "quote_ts": [None],
    })

    svc = TaiwanDailyRefreshService(
        store=mock_store,
        snapshot_adapter=mock_adapter,
        calendar=TaiwanTradingCalendar(),
    )

    # 2026-08-26 (Wed) to 2026-08-28 (Fri) = 3 trading days
    stats = svc.refresh_dates(date(2026, 8, 26), date(2026, 8, 28))
    assert stats["dates_requested"] == 3
    assert stats["dates_fetched"] == 3
    assert mock_adapter.fetch_date.call_count == 3


def test_refresh_dates_idempotency():
    """Already-persisted dates are skipped with 0 HTTP calls."""
    mock_store = MagicMock(spec=TaiwanDailyStore)
    mock_store.available_dates.return_value = [date(2026, 8, 28)]

    mock_adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    svc = TaiwanDailyRefreshService(
        store=mock_store,
        snapshot_adapter=mock_adapter,
        calendar=TaiwanTradingCalendar(),
    )

    stats = svc.refresh_dates(date(2026, 8, 28), date(2026, 8, 28))
    assert stats["dates_requested"] == 1
    assert stats["dates_skipped"] == 1
    assert stats["dates_fetched"] == 0
    mock_adapter.fetch_date.assert_not_called()
    mock_store.write_batch.assert_not_called()


def test_failure_isolation_does_not_crash_or_mark_holiday():
    """A date whose snapshot fetch fails is recorded as failed: nothing is written and it is not counted as skipped."""
    mock_store = MagicMock(spec=TaiwanDailyStore)
    mock_store.available_dates.return_value = []

    mock_adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    mock_adapter.fetch_date.side_effect = RuntimeError("TPEx snapshot failed for 2026-08-28")

    svc = TaiwanDailyRefreshService(
        store=mock_store,
        snapshot_adapter=mock_adapter,
        calendar=TaiwanTradingCalendar(),
    )

    stats = svc.refresh_dates(date(2026, 8, 28), date(2026, 8, 28))
    assert stats["dates_requested"] == 1
    assert stats["dates_fetched"] == 0
    assert stats["dates_skipped"] == 0
    assert stats["failed_dates"] == [{"date": "2026-08-28", "error": "TPEx snapshot failed for 2026-08-28"}]
    mock_store.write_batch.assert_not_called()


def test_refresh_dates_refetches_a_partition_holding_one_exchange(tmp_path):
    """A partition written from half a snapshot is not a finished day and must not be skipped as one."""
    store = TaiwanDailyStore(data_dir=tmp_path)
    day = date(2026, 8, 28)
    store.write_batch(_rows(["2330.TWSE"], day))
    adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    adapter.fetch_date.return_value = _rows(["2330.TWSE", "6488.TPEX"], day)
    svc = TaiwanDailyRefreshService(store=store, snapshot_adapter=adapter, calendar=TaiwanTradingCalendar())

    stats = svc.refresh_dates(day, day)

    adapter.fetch_date.assert_called_once_with(day)
    assert stats["dates_fetched"] == 1
    assert sorted(store.read_range(None, day, day)["symbol"]) == ["2330.TWSE", "6488.TPEX"]


def test_refresh_dates_stops_at_the_first_failed_date():
    """Storing the later dates would move the latest stored date past the gap, and runs only fetch after it."""
    mock_store = MagicMock(spec=TaiwanDailyStore)
    mock_store.available_dates.return_value = []
    adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    adapter.fetch_date.side_effect = [
        RuntimeError("TPEx down"),
        _rows(["2330.TWSE", "6488.TPEX"], date(2026, 8, 27)),
        _rows(["2330.TWSE", "6488.TPEX"], date(2026, 8, 28)),
    ]
    svc = TaiwanDailyRefreshService(store=mock_store, snapshot_adapter=adapter, calendar=TaiwanTradingCalendar())

    stats = svc.refresh_dates(date(2026, 8, 26), date(2026, 8, 28))

    assert [f["date"] for f in stats["failed_dates"]] == ["2026-08-26"]
    assert adapter.fetch_date.call_count == 1
    mock_store.write_batch.assert_not_called()


def test_refresh_dates_moves_on_when_a_repair_fails(tmp_path):
    """A stored half snapshot that cannot be refetched must not hold back new days forever."""
    store = TaiwanDailyStore(data_dir=tmp_path)
    store.write_batch(_rows(["2330.TWSE"], date(2026, 8, 27)))
    adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    adapter.fetch_date.side_effect = [
        RuntimeError("TPEx down"),
        _rows(["2330.TWSE", "6488.TPEX"], date(2026, 8, 28)),
    ]
    svc = TaiwanDailyRefreshService(store=store, snapshot_adapter=adapter, calendar=TaiwanTradingCalendar())

    stats = svc.refresh_dates(date(2026, 8, 27), date(2026, 8, 28))

    assert stats["failed_dates"] == []
    assert stats["repair_failed_dates"] == [{"date": "2026-08-27", "error": "TPEx down"}]
    assert stats["dates_fetched"] == 1
    assert store.available_dates() == [date(2026, 8, 27), date(2026, 8, 28)]
    assert store.incomplete_dates([date(2026, 8, 27)]) == [date(2026, 8, 27)]


def test_a_failed_day_inside_the_stored_range_does_not_hold_back_new_days(tmp_path):
    """The latest stored date is already past that day, so moving on cannot open a new gap."""
    store = TaiwanDailyStore(data_dir=tmp_path)
    store.write_batch(_rows(["2330.TWSE", "6488.TPEX"], date(2026, 8, 26)))
    store.write_batch(_rows(["2330.TWSE", "6488.TPEX"], date(2026, 8, 28)))
    adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    adapter.fetch_date.side_effect = [
        RuntimeError("TPEx down"),
        _rows(["2330.TWSE", "6488.TPEX"], date(2026, 8, 31)),
    ]
    svc = TaiwanDailyRefreshService(store=store, snapshot_adapter=adapter, calendar=TaiwanTradingCalendar())

    stats = svc.refresh_dates(date(2026, 8, 27), date(2026, 8, 31))

    assert stats["failed_dates"] == []
    assert stats["repair_failed_dates"] == [{"date": "2026-08-27", "error": "TPEx down"}]
    assert date(2026, 8, 31) in store.available_dates()


@pytest.mark.parametrize("force", [False, True])
def test_a_repair_that_returns_no_rows_is_reported(tmp_path, force):
    store = TaiwanDailyStore(data_dir=tmp_path)
    store.write_batch(_rows(["2330.TWSE"], date(2026, 8, 28)))
    adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    adapter.fetch_date.return_value = pl.DataFrame()
    svc = TaiwanDailyRefreshService(store=store, snapshot_adapter=adapter, calendar=TaiwanTradingCalendar())

    stats = svc.refresh_dates(date(2026, 8, 28), date(2026, 8, 28), force=force)

    assert stats["repair_failed_dates"] == [{"date": "2026-08-28", "error": "no rows"}]


def test_force_refresh_moves_past_a_stored_day_that_fails(tmp_path):
    """A forced refetch keeps a stored day's bars when it fails, so the later days are still refetched."""
    store = TaiwanDailyStore(data_dir=tmp_path)
    for d in (date(2026, 8, 26), date(2026, 8, 27), date(2026, 8, 28)):
        store.write_batch(_rows(["2330.TWSE", "6488.TPEX"], d))
    adapter = MagicMock(spec=OfficialDailySnapshotAdapter)
    adapter.fetch_date.side_effect = [
        RuntimeError("TPEx down"),
        _rows(["2330.TWSE", "6488.TPEX"], date(2026, 8, 27)),
        _rows(["2330.TWSE", "6488.TPEX"], date(2026, 8, 28)),
    ]
    svc = TaiwanDailyRefreshService(store=store, snapshot_adapter=adapter, calendar=TaiwanTradingCalendar())

    stats = svc.refresh_dates(date(2026, 8, 26), date(2026, 8, 28), force=True)

    assert [f["date"] for f in stats["failed_dates"]] == ["2026-08-26"]
    assert adapter.fetch_date.call_count == 3
    assert stats["dates_fetched"] == 2


def test_incomplete_dates_flags_partitions_missing_an_exchange(tmp_path):
    store = TaiwanDailyStore(data_dir=tmp_path)
    store.write_batch(_rows(["2330.TWSE", "6488.TPEX"], date(2026, 8, 27)))
    store.write_batch(_rows(["2330.TWSE"], date(2026, 8, 28)))

    checked = [date(2026, 8, 27), date(2026, 8, 28), date(2026, 8, 31)]
    assert store.incomplete_dates(checked) == [date(2026, 8, 28)]


def test_daily_update_orchestration_with_snapshot():
    """TaiwanDailyUpdateService seamlessly updates Daily, Inst, and Margin."""
    mock_daily_store = MagicMock()
    mock_daily_store.available_dates.return_value = [date(2026, 8, 27)]

    mock_inst_store = MagicMock()
    mock_inst_store.available_dates.return_value = [date(2026, 8, 27)]

    mock_margin_store = MagicMock()
    mock_margin_store.available_dates.return_value = [date(2026, 8, 27)]

    mock_daily_svc = MagicMock()
    mock_daily_svc.refresh_dates.return_value = {
        "dates_requested": 1, "dates_fetched": 1, "dates_skipped": 0, "total_rows_written": 2330, "failed_dates": []
    }

    mock_inst_svc = MagicMock()
    mock_inst_svc.refresh_dates.return_value = {
        "dates_requested": 1, "dates_fetched": 1, "dates_skipped": 0, "total_rows_written": 2330, "failed_dates": []
    }

    mock_margin_svc = MagicMock()
    mock_margin_svc.refresh_dates.return_value = {
        "dates_requested": 1, "dates_fetched": 1, "dates_skipped": 0, "total_rows_written": 2330, "failed_dates": []
    }

    svc = TaiwanDailyUpdateService(
        daily_store=mock_daily_store,
        inst_store=mock_inst_store,
        margin_store=mock_margin_store,
        daily_service=mock_daily_svc,
        inst_service=mock_inst_svc,
        margin_service=mock_margin_svc,
    )

    # Run default update (refresh_daily=True)
    res = svc.run_update(target_date=date(2026, 8, 28))
    assert res.overall_status == "success"
    assert res.daily.status == "success"
    assert res.institutional.status == "success"
    assert res.margin.status == "success"

    # Confirmed mock_daily_svc.refresh_dates was called, NOT refresh_symbols
    mock_daily_svc.refresh_dates.assert_called_once_with(
        start_date=date(2026, 8, 28), end_date=date(2026, 8, 28), force=False
    )
    mock_daily_svc.refresh_symbols.assert_not_called()
