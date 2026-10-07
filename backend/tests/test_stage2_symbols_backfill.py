"""The --symbols daily backfill stores TPEx bars on the dailyQuotes basis and never rewrites stored rows."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs

import httpx
import polars as pl
import pytest

from app.taiwan.daily_refresh import TaiwanDailyRefreshService
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.providers.snapshot_provider import OfficialDailySnapshotAdapter
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar
from app.taiwan.universe import TaiwanSecurityMaster

SNAPSHOT_6488_0915 = 8_270_423.0
QUOTES_6488_0923 = ("28,714,838", "27,580,042,174")
QUOTES_8069_0923 = ("5,796,924", "841,677,554")
TRADING_STOCK_ROUNDED_VOLUME = 7_583_000.0
DELISTED_TPEX = "6129.TPEX"
UNSUPPORTED_TPEX = "7777.TPEX"


def _master() -> MagicMock:
    master = MagicMock(spec=TaiwanSecurityMaster)
    stocks = pl.DataFrame(
        {
            "symbol": ["2330.TWSE", "6488.TPEX", "8069.TPEX"],
            "exchange": ["TWSE", "TPEX", "TPEX"],
            "instrument_type": ["stock", "stock", "stock"],
        }
    )
    etfs = pl.DataFrame(schema={"symbol": pl.Utf8, "exchange": pl.Utf8, "instrument_type": pl.Utf8})
    master.to_provider_dataframe.side_effect = lambda asset_type: (
        stocks if asset_type == "stock" else etfs
    )
    instruments = {
        **{
            symbol: SimpleNamespace(
                instrument_type="stock", is_supported=True, listing_status="active"
            )
            for symbol in stocks["symbol"]
        },
        DELISTED_TPEX: SimpleNamespace(
            instrument_type="stock", is_supported=True, listing_status="delisted"
        ),
        UNSUPPORTED_TPEX: SimpleNamespace(
            instrument_type="warrant", is_supported=False, listing_status="active"
        ),
    }
    master.get_instrument.side_effect = instruments.get
    return master


def _quote_row(code: str, volume: str, amount: str) -> list[str]:
    return [code, code, "950.00", "-21.00", "960.00", "970.00", "940.00", "955.00", volume, amount]


TPEX_FIELDS = [
    "代號",
    "名稱",
    "收盤",
    "漲跌",
    "開盤",
    "最高",
    "最低",
    "均價",
    "成交股數",
    "成交金額(元)",
]
TWSE_FIELDS = [
    "證券代號",
    "證券名稱",
    "成交股數",
    "成交筆數",
    "成交金額",
    "開盤價",
    "最高價",
    "最低價",
    "收盤價",
]
TWSE_2330_ROW = [
    "2330",
    "2330",
    "15,025,832",
    "52,492",
    "36,465,015,980",
    "2,440.00",
    "2,445.00",
    "2,410.00",
    "2,420.00",
]


class FakeDailyQuotes:
    """Serves TWSE MI_INDEX and TPEx dailyQuotes per date and records every request.

    TWSE trades on every requested day except *closed_days*; TPEx answers *rows_by_date*,
    and for *undated_days* returns rows without a date stamp, as a closed-day answer might.
    """

    def __init__(
        self,
        rows_by_date: dict[date, list[list[str]]],
        closed_days: set[date] = frozenset(),
        undated_days: set[date] = frozenset(),
        tpex_status: int = 200,
    ) -> None:
        self.rows_by_date = rows_by_date
        self.closed_days = closed_days
        self.undated_days = undated_days
        self.tpex_status = tpex_status
        self.requested: list[date] = []
        self.twse_requested: list[date] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        if "MI_INDEX" in request.url.path:
            requested = datetime.strptime(query["date"][0], "%Y%m%d").date()
            self.twse_requested.append(requested)
            if requested in self.closed_days:
                return httpx.Response(200, json={"stat": "很抱歉 沒有符合條件的資料!"})
            return httpx.Response(
                200,
                json={
                    "stat": "OK",
                    "date": requested.strftime("%Y%m%d"),
                    "tables": [
                        {
                            "title": "每日收盤行情(全部)",
                            "fields": TWSE_FIELDS,
                            "data": [TWSE_2330_ROW],
                        },
                    ],
                },
            )
        assert "dailyQuotes" in request.url.path
        year, month, day = (int(p) for p in query["date"][0].split("/"))
        requested = date(year + 1911, month, day)
        self.requested.append(requested)
        if self.tpex_status != 200:
            return httpx.Response(self.tpex_status)
        payload = {
            "stat": "ok",
            "tables": [
                {
                    "title": "上櫃股票行情",
                    "fields": TPEX_FIELDS,
                    "data": self.rows_by_date.get(requested, []),
                },
            ],
        }
        if requested not in self.undated_days:
            payload["date"] = requested.strftime("%Y%m%d")
        return httpx.Response(200, json=payload)


class TradingStockLikeProvider:
    """Per-symbol provider answering every weekday with rounded lot-based volume, like tradingStock x 1000."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], date, date]] = []

    def get_daily(self, symbols, start_time=None, end_time=None, **kwargs) -> pl.DataFrame:
        self.calls.append((list(symbols), start_time, end_time))
        rows = []
        cur = start_time
        while cur <= end_time:
            if cur.weekday() < 5:
                for sym in symbols:
                    rows.append(
                        {
                            "symbol": sym,
                            "date": cur,
                            "open": 1.0,
                            "high": 1.0,
                            "low": 1.0,
                            "close": 1.0,
                            "volume": TRADING_STOCK_ROUNDED_VOLUME,
                            "amount": 1.0,
                            "quote_ts": None,
                        }
                    )
            cur += timedelta(days=1)
        return pl.DataFrame(rows) if rows else pl.DataFrame()


def _bar(symbol: str, d: date, volume: float) -> dict:
    return {
        "symbol": symbol,
        "date": d,
        "open": 900.0,
        "high": 910.0,
        "low": 890.0,
        "close": 905.0,
        "volume": volume,
        "amount": volume * 905.0,
        "quote_ts": None,
    }


@pytest.fixture
def store(tmp_path) -> TaiwanDailyStore:
    return TaiwanDailyStore(data_dir=tmp_path / "daily")


def _service(
    store, quotes: FakeDailyQuotes, provider: TradingStockLikeProvider
) -> TaiwanDailyRefreshService:
    adapter = OfficialDailySnapshotAdapter(
        security_master=_master(),
        client=httpx.Client(transport=httpx.MockTransport(quotes)),
    )
    return TaiwanDailyRefreshService(
        store=store,
        provider=provider,
        calendar=TaiwanTradingCalendar(),
        snapshot_adapter=adapter,
    )


def _stored(store: TaiwanDailyStore, symbol: str, d: date) -> dict:
    rows = store.read_range([symbol], d, d)
    assert rows.height == 1, f"{symbol} {d}: {rows.height} rows"
    return rows.row(0, named=True)


def test_existing_tpex_snapshot_row_is_not_rewritten(deny_network, store):
    """Only the missing 9/14 and 9/16 are requested; the stored 9/15 snapshot row keeps its shares."""
    store.write_batch(pl.DataFrame([_bar("6488.TPEX", date(2026, 9, 15), SNAPSHOT_6488_0915)]))
    quotes = FakeDailyQuotes(
        {
            date(2026, 9, 14): [_quote_row("6488", "6,000,001", "5,400,000,900")],
            date(2026, 9, 16): [_quote_row("6488", "9,000,003", "8,100,002,700")],
        }
    )
    provider = TradingStockLikeProvider()

    _service(store, quotes, provider).refresh_symbols(
        ["6488.TPEX"], start=date(2026, 9, 14), end=date(2026, 9, 16)
    )

    assert _stored(store, "6488.TPEX", date(2026, 9, 15))["volume"] == SNAPSHOT_6488_0915
    assert _stored(store, "6488.TPEX", date(2026, 9, 14))["volume"] == 6_000_001
    assert _stored(store, "6488.TPEX", date(2026, 9, 16))["volume"] == 9_000_003
    assert sorted(quotes.requested) == [date(2026, 9, 14), date(2026, 9, 16)]
    assert provider.calls == []


def test_tpex_gap_day_gets_exact_daily_quotes_shares(deny_network, store):
    d = date(2026, 9, 23)
    quotes = FakeDailyQuotes(
        {d: [_quote_row("6488", *QUOTES_6488_0923), _quote_row("8069", *QUOTES_8069_0923)]}
    )
    provider = TradingStockLikeProvider()

    res = _service(store, quotes, provider).refresh_symbols(
        ["6488.TPEX", "8069.TPEX"], start=d, end=d
    )

    row = _stored(store, "6488.TPEX", d)
    assert row["volume"] == 28_714_838
    assert row["amount"] == 27_580_042_174
    assert _stored(store, "8069.TPEX", d)["volume"] == 5_796_924
    assert quotes.requested == [d]
    assert provider.calls == []
    assert res["rows_written"] == 2
    assert res["symbols_fetched"] == 2
    assert res["failed"] == []


def test_delisted_tpex_symbol_is_backfilled_from_daily_quotes(deny_network, store):
    d = date(2026, 9, 23)
    quotes = FakeDailyQuotes(
        {d: [_quote_row("6129", *QUOTES_8069_0923), _quote_row("6488", *QUOTES_6488_0923)]}
    )

    res = _service(store, quotes, TradingStockLikeProvider()).refresh_symbols(
        [DELISTED_TPEX, UNSUPPORTED_TPEX], start=d, end=d
    )

    assert _stored(store, DELISTED_TPEX, d)["volume"] == 5_796_924
    assert store.read_range(["6488.TPEX"], d, d).is_empty()
    assert res["per_symbol"][DELISTED_TPEX]["status"] == "ok"
    assert res["failed"] == [UNSUPPORTED_TPEX]
    assert res["per_symbol"][UNSUPPORTED_TPEX]["status"] == "unsupported"


def test_full_market_snapshot_still_skips_delisted_tpex_symbols(deny_network, store):
    d = date(2026, 9, 23)
    quotes = FakeDailyQuotes(
        {d: [_quote_row("6129", *QUOTES_8069_0923), _quote_row("6488", *QUOTES_6488_0923)]}
    )

    df = _service(store, quotes, TradingStockLikeProvider())._snapshot_adapter.fetch_date(d)

    assert "6488.TPEX" in df["symbol"].to_list()
    assert DELISTED_TPEX not in df["symbol"].to_list()


def test_tpex_symbol_absent_from_daily_quotes_writes_nothing(deny_network, store):
    d = date(2026, 9, 23)
    quotes = FakeDailyQuotes({d: [_quote_row("8069", *QUOTES_8069_0923)]})

    res = _service(store, quotes, TradingStockLikeProvider()).refresh_symbols(
        ["6488.TPEX"], start=d, end=d
    )

    assert store.read_range(["6488.TPEX"], d, d).is_empty()
    assert res["rows_written"] == 0
    assert res["symbols_fetched"] == 0


def test_trading_day_check_failure_is_reported(deny_network, store):
    """Both exchanges refuse, so the TWSE trading-day check fails and the TPEx symbol is reported."""
    d = date(2026, 9, 23)

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    adapter = OfficialDailySnapshotAdapter(
        security_master=_master(),
        client=httpx.Client(transport=httpx.MockTransport(refuse)),
    )
    service = TaiwanDailyRefreshService(
        store=store,
        provider=TradingStockLikeProvider(),
        calendar=TaiwanTradingCalendar(),
        snapshot_adapter=adapter,
    )

    res = service.refresh_symbols(["6488.TPEX"], start=d, end=d)

    assert res["failed"] == ["6488.TPEX"]
    assert "error" in res["per_symbol"]["6488.TPEX"]
    assert store.read_range(["6488.TPEX"], d, d).is_empty()


def test_twse_symbols_keep_the_per_symbol_provider(deny_network, store):
    """TWSE still fetches the coalesced range per symbol, but a stored day inside it keeps its values."""
    store.write_batch(pl.DataFrame([_bar("2330.TWSE", date(2026, 9, 15), 15_025_832.0)]))
    quotes = FakeDailyQuotes({})
    provider = TradingStockLikeProvider()

    res = _service(store, quotes, provider).refresh_symbols(
        ["2330.TWSE"], start=date(2026, 9, 14), end=date(2026, 9, 16)
    )

    assert provider.calls == [(["2330.TWSE"], date(2026, 9, 14), date(2026, 9, 16))]
    assert quotes.requested == []
    assert quotes.twse_requested == []
    assert _stored(store, "2330.TWSE", date(2026, 9, 15))["volume"] == 15_025_832.0
    assert _stored(store, "2330.TWSE", date(2026, 9, 14))["volume"] == TRADING_STOCK_ROUNDED_VOLUME
    assert _stored(store, "2330.TWSE", date(2026, 9, 16))["volume"] == TRADING_STOCK_ROUNDED_VOLUME
    assert res["rows_written"] == 2


def test_lowercase_tpex_symbol_is_canonicalized(deny_network, store):
    store.write_batch(pl.DataFrame([_bar("6488.TPEX", date(2026, 9, 15), SNAPSHOT_6488_0915)]))
    quotes = FakeDailyQuotes(
        {
            date(2026, 9, 14): [_quote_row("6488", "6,000,001", "5,400,000,900")],
            date(2026, 9, 16): [_quote_row("6488", "9,000,003", "8,100,002,700")],
        }
    )
    provider = TradingStockLikeProvider()

    res = _service(store, quotes, provider).refresh_symbols(
        [" 6488.tpex"], start=date(2026, 9, 14), end=date(2026, 9, 16)
    )

    assert provider.calls == []
    assert _stored(store, "6488.TPEX", date(2026, 9, 15))["volume"] == SNAPSHOT_6488_0915
    assert _stored(store, "6488.TPEX", date(2026, 9, 16))["volume"] == 9_000_003
    assert list(res["per_symbol"]) == ["6488.TPEX"]


def test_duplicate_symbols_are_fetched_and_counted_once(deny_network, store):
    d = date(2026, 9, 23)
    quotes = FakeDailyQuotes({d: [_quote_row("6488", *QUOTES_6488_0923)]})
    provider = TradingStockLikeProvider()

    res = _service(store, quotes, provider).refresh_symbols(
        ["6488.TPEX", "6488.TPEX", "6488.tpex", "2330.TWSE", "2330.twse"], start=d, end=d
    )

    assert quotes.requested == [d]
    assert provider.calls == [(["2330.TWSE"], d, d)]
    assert res["rows_written"] == 2
    assert res["symbols_total"] == 2
    assert res["per_symbol"]["6488.TPEX"]["ranges"] == [
        {"start": "2026-09-23", "end": "2026-09-23", "rows": 1}
    ]


def test_closed_day_tpex_answer_is_not_stored(deny_network, store):
    """TWSE reports no trading on 9/25, so an undated TPEx answer for that day is never requested or stored."""
    d = date(2026, 9, 25)
    quotes = FakeDailyQuotes(
        {d: [_quote_row("6488", *QUOTES_6488_0923)]}, closed_days={d}, undated_days={d}
    )

    res = _service(store, quotes, TradingStockLikeProvider()).refresh_symbols(
        ["6488.TPEX"], start=d, end=d
    )

    assert quotes.twse_requested == [d]
    assert quotes.requested == []
    assert store.available_dates() == []
    assert res["rows_written"] == 0
    assert res["failed"] == []


def test_unsupported_and_invalid_symbols_are_reported(deny_network, store):
    d = date(2026, 9, 23)
    quotes = FakeDailyQuotes({d: [_quote_row("9999", *QUOTES_6488_0923)]})

    res = _service(store, quotes, TradingStockLikeProvider()).refresh_symbols(
        ["9999.TPEX", "6488.TWO", " 6488.TWO", "9999.tpex"], start=d, end=d
    )

    assert res["failed"] == ["6488.TWO", "9999.TPEX"]
    assert res["per_symbol"]["9999.TPEX"]["status"] == "unsupported"
    assert res["per_symbol"]["6488.TWO"]["status"] == "invalid"
    assert quotes.requested == []
    assert store.available_dates() == []


def test_provider_rows_outside_the_requested_range_are_not_written(deny_network, store):
    class WideProvider(TradingStockLikeProvider):
        def get_daily(self, symbols, start_time=None, end_time=None, **kwargs):
            return super().get_daily(
                symbols, start_time - timedelta(days=7), end_time + timedelta(days=7)
            )

    d = date(2026, 9, 23)
    res = _service(store, FakeDailyQuotes({}), WideProvider()).refresh_symbols(
        ["2330.TWSE"], start=d, end=d
    )

    assert store.available_dates() == [d]
    assert res["rows_written"] == 1


def test_write_batch_skip_existing_keeps_stored_rows(deny_network, store):
    d = date(2026, 9, 15)
    store.write_batch(pl.DataFrame([_bar("6488.TPEX", d, SNAPSHOT_6488_0915)]))
    incoming = pl.DataFrame(
        [_bar("6488.TPEX", d, TRADING_STOCK_ROUNDED_VOLUME), _bar("8069.TPEX", d, 1_000.0)]
    )

    store.write_batch(incoming, skip_existing=True)

    assert _stored(store, "6488.TPEX", d)["volume"] == SNAPSHOT_6488_0915
    assert _stored(store, "8069.TPEX", d)["volume"] == 1_000.0
    store.write_batch(incoming)
    assert _stored(store, "6488.TPEX", d)["volume"] == TRADING_STOCK_ROUNDED_VOLUME


def test_write_batch_skip_existing_leaves_a_fully_stored_partition_untouched(deny_network, store):
    d = date(2026, 9, 15)
    store.write_batch(pl.DataFrame([_bar("6488.TPEX", d, SNAPSHOT_6488_0915)]))
    part = next(store._data_dir.glob("date=2026-09-15/part.parquet"))
    before = part.stat().st_mtime_ns

    store.write_batch(
        pl.DataFrame([_bar("6488.TPEX", d, TRADING_STOCK_ROUNDED_VOLUME)]), skip_existing=True
    )

    assert part.stat().st_mtime_ns == before
    assert _stored(store, "6488.TPEX", d)["volume"] == SNAPSHOT_6488_0915


@pytest.mark.parametrize("rows_written, expected", [(3, "partial"), (0, "failed")])
def test_daily_update_reports_failed_symbols(deny_network, monkeypatch, rows_written, expected):
    from app.taiwan import enriched_refresh, share_capital_store
    from app.taiwan.corporate_actions import TaiwanCorporateActionRefreshService
    from app.taiwan.daily_update import TaiwanDailyUpdateService
    from app.taiwan.market_institutional_refresh import TaiwanMarketInstitutionalRefreshService

    monkeypatch.setattr(
        TaiwanMarketInstitutionalRefreshService, "refresh_through", lambda self, target: {}
    )
    monkeypatch.setattr(
        TaiwanCorporateActionRefreshService,
        "refresh_publish_window",
        lambda self, first, target: {},
    )
    monkeypatch.setattr(share_capital_store.TaiwanShareCapitalStore, "refresh", lambda self: 0)
    monkeypatch.setattr(
        enriched_refresh.TaiwanEnrichedRefreshService, "refresh_recent", lambda self, days=5: 0
    )
    stores = []
    for _ in range(3):
        s = MagicMock()
        s.available_dates.return_value = [date(2026, 8, 28)]
        s.repair_dates.return_value = []
        stores.append(s)
    daily_svc = MagicMock()
    daily_svc.refresh_symbols.return_value = {
        "symbols_fetched": 1 if rows_written else 0,
        "rows_written": rows_written,
        "failed": ["9999.TPEX", "6488.TPEX"],
        "per_symbol": {
            "9999.TPEX": {"status": "unsupported", "error": "not a supported stock or ETF"},
            "6488.TPEX": {"status": "error", "error": "HTTP 500"},
        },
    }
    ok = {"dates_requested": 1, "dates_fetched": 1, "total_rows_written": 1, "failed_dates": []}
    inst_svc, margin_svc = MagicMock(), MagicMock()
    inst_svc.refresh_dates.return_value = ok
    margin_svc.refresh_dates.return_value = ok

    result = TaiwanDailyUpdateService(
        daily_store=stores[0],
        inst_store=stores[1],
        margin_store=stores[2],
        daily_service=daily_svc,
        inst_service=inst_svc,
        margin_service=margin_svc,
    ).run_update(
        target_date=date(2026, 8, 31), refresh_daily=True, daily_symbols=["9999.TPEX", "6488.TPEX"]
    )

    assert result.daily.status == expected
    assert (
        result.daily.note == "Symbols not refreshed: 9999.TPEX (unsupported), 6488.TPEX (HTTP 500)"
    )
    assert result.overall_status == "partial"


def _symbols_update(monkeypatch, refresh_result: dict) -> list[dict]:
    from app.taiwan import daily_update

    published: list[dict] = []
    monkeypatch.setattr(
        daily_update, "publish_enriched", lambda *a, **k: published.append(k) or True
    )
    stores = []
    for _ in range(3):
        s = MagicMock()
        s.available_dates.return_value = [date(2026, 8, 28)]
        s.repair_dates.return_value = []
        stores.append(s)
    daily_svc = MagicMock()
    daily_svc.refresh_symbols.return_value = refresh_result
    daily_update.TaiwanDailyUpdateService(
        daily_store=stores[0],
        inst_store=stores[1],
        margin_store=stores[2],
        daily_service=daily_svc,
        inst_service=MagicMock(),
        margin_service=MagicMock(),
    ).run_update(
        target_date=date(2026, 8, 31),
        refresh_daily=True,
        refresh_institutional=False,
        refresh_margin=False,
        daily_symbols=["6488.TPEX", "2330.TWSE"],
    )
    return published


def test_symbols_run_republishes_enriched_from_its_earliest_written_day(deny_network, monkeypatch):
    def ranges(*spans):
        return [{"start": a, "end": b, "rows": rows} for a, b, rows in spans]

    published = _symbols_update(
        monkeypatch,
        {
            "symbols_fetched": 2,
            "rows_written": 5,
            "failed": [],
            "per_symbol": {
                "6488.TPEX": {
                    "status": "ok",
                    "rows": 2,
                    "ranges": ranges(
                        ("2026-08-12", "2026-08-12", 0),
                        ("2026-08-20", "2026-08-20", 1),
                        ("2026-08-25", "2026-08-25", 1),
                    ),
                },
                "2330.TWSE": {
                    "status": "ok",
                    "rows": 3,
                    "ranges": ranges(("2026-08-14", "2026-08-19", 3)),
                },
            },
        },
    )

    assert [call.get("since") for call in published] == [date(2026, 8, 14)]


def test_symbols_run_without_new_rows_publishes_only_the_recent_window(deny_network, monkeypatch):
    published = _symbols_update(
        monkeypatch,
        {"message": "all dates up to date", "symbols_fetched": 0, "rows_written": 0},
    )

    assert [call.get("since") for call in published] == [None]


def test_publish_enriched_since_an_older_day_republishes_from_that_day(
    deny_network, monkeypatch, tmp_path
):
    from app.taiwan import enriched_refresh, share_capital_store
    from app.taiwan.daily_update import publish_enriched

    calls: list[tuple] = []
    service = enriched_refresh.TaiwanEnrichedRefreshService
    monkeypatch.setattr(share_capital_store.TaiwanShareCapitalStore, "refresh", lambda self: 0)
    monkeypatch.setattr(
        service, "recent_window", lambda self, days=5: (date(2026, 8, 25), date(2026, 8, 31))
    )
    monkeypatch.setattr(
        service, "refresh", lambda self, start=None, end=None: calls.append((start, end)) or 0
    )
    monkeypatch.setattr(service, "refresh_recent", lambda self, days=5: calls.append("recent") or 0)
    corporate = MagicMock()
    corporate.refresh_publish_window.return_value = {}
    store = TaiwanDailyStore(data_dir=tmp_path / "daily")

    publish_enriched(store, corporate_actions=corporate, since=date(2026, 8, 14))
    publish_enriched(store, corporate_actions=corporate, since=date(2026, 8, 27))
    publish_enriched(store, corporate_actions=corporate)

    assert calls == [(date(2026, 8, 14), date(2026, 8, 31)), "recent", "recent"]


def test_tpex_server_error_is_reported(deny_network, monkeypatch, store):
    from app.taiwan.providers import snapshot_provider

    monkeypatch.setattr(snapshot_provider.time, "sleep", lambda _s: None)
    d = date(2026, 9, 23)
    quotes = FakeDailyQuotes({d: [_quote_row("6488", *QUOTES_6488_0923)]}, tpex_status=500)

    res = _service(store, quotes, TradingStockLikeProvider()).refresh_symbols(
        ["6488.TPEX"], start=d, end=d
    )

    assert quotes.twse_requested == [d]
    assert quotes.requested == [d] * snapshot_provider.DEFAULT_MAX_ATTEMPTS
    assert res["failed"] == ["6488.TPEX"]
    assert res["per_symbol"]["6488.TPEX"]["status"] == "error"
    assert store.available_dates() == []


def test_traded_day_with_an_empty_tpex_table_is_reported(deny_network, store):
    """TWSE traded, so an empty dailyQuotes table is a missing half snapshot, not a quiet day."""
    d = date(2026, 9, 23)
    quotes = FakeDailyQuotes({})

    res = _service(store, quotes, TradingStockLikeProvider()).refresh_symbols(
        ["6488.TPEX", "8069.TPEX"], start=d, end=d
    )

    assert sorted(res["failed"]) == ["6488.TPEX", "8069.TPEX"]
    assert "no rows" in res["per_symbol"]["6488.TPEX"]["error"]
    assert store.available_dates() == []


def test_stored_twse_rows_confirm_the_trading_day(deny_network, store):
    d = date(2026, 9, 23)
    store.write_batch(pl.DataFrame([_bar("2330.TWSE", d, 15_025_832.0)]))
    quotes = FakeDailyQuotes({d: [_quote_row("6488", *QUOTES_6488_0923)]})

    _service(store, quotes, TradingStockLikeProvider()).refresh_symbols(
        ["6488.TPEX"], start=d, end=d
    )

    assert quotes.twse_requested == []
    assert quotes.requested == [d]
    assert _stored(store, "6488.TPEX", d)["volume"] == 28_714_838


def _set_mtime(store: TaiwanDailyStore, d: date, at: datetime) -> float:
    part = store._data_dir / f"date={d.isoformat()}" / "part.parquet"
    os.utime(part, (at.timestamp(), at.timestamp()))
    return at.timestamp()


def test_skip_existing_write_keeps_a_provisional_partition_provisional(deny_network, store):
    d = date(2026, 9, 15)
    store.write_batch(pl.DataFrame([_bar("2330.TWSE", d, 15_025_832.0)]))
    original = _set_mtime(store, d, datetime(2026, 9, 15, 16, 40, tzinfo=TAIPEI_TZ))
    assert store.provisional_dates([d]) == [d]

    store.write_batch(pl.DataFrame([_bar("6488.TPEX", d, SNAPSHOT_6488_0915)]), skip_existing=True)

    assert store.provisional_dates([d]) == [d]
    assert store.partition_mtime(d) > original
    assert _stored(store, "6488.TPEX", d)["volume"] == SNAPSHOT_6488_0915


def test_skip_existing_write_leaves_a_final_partition_final(deny_network, store):
    d = date(2026, 9, 15)
    store.write_batch(pl.DataFrame([_bar("2330.TWSE", d, 15_025_832.0)]))
    original = _set_mtime(store, d, datetime(2026, 9, 16, 9, 0, tzinfo=TAIPEI_TZ))

    store.write_batch(pl.DataFrame([_bar("6488.TPEX", d, SNAPSHOT_6488_0915)]), skip_existing=True)

    assert store.provisional_dates([d]) == []
    assert store.partition_mtime(d) > original
