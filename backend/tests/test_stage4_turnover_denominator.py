"""Taiwan turnover divides by issued common shares minus private-placement shares (stage 4, D4).

TWSE t187ap03_L carries 私募股數 and TPEx mopsfin_t187ap03_O PrivateStock.shares next to the issued
count; privately placed shares cannot trade freely, so they leave the denominator. Sample rows are
copied from the official responses of 2026-10-08 (TWSE) and 2026-10-09 (TPEx).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan.share_capital_store import TaiwanShareCapitalStore

pytestmark = pytest.mark.usefixtures("deny_network")

TWSE_ROWS = [
    {
        "公司代號": "2330",
        "已發行普通股數或TDR原股發行股數": "25932370067",
        "私募股數": "0",
        "出表日期": "1151008",
    },
    {
        "公司代號": "3055",
        "已發行普通股數或TDR原股發行股數": "114974918",
        "私募股數": "16300000",
        "出表日期": "1151008",
    },
]
TPEX_ROWS = [
    {
        "SecuritiesCompanyCode": "3105",
        "IssueShares": "423940384",
        "PrivateStock.shares": "0",
        "Date": "1151009",
    },
    {
        "SecuritiesCompanyCode": "6488",
        "IssueShares": "182000000",
        "PrivateStock.shares": "2000000",
        "Date": "1151009",
    },
]


def _client(twse=TWSE_ROWS, tpex=TPEX_ROWS, tpex_error=None):
    client = MagicMock()

    def get(url):
        if "twse" in url:
            return MagicMock(json=lambda: twse)
        if tpex_error:
            raise tpex_error
        return MagicMock(json=lambda: tpex)

    client.get.side_effect = get
    return client


def _denominators(store: TaiwanShareCapitalStore) -> dict[str, float | None]:
    return dict(store.as_instruments().select("symbol", "float_shares").iter_rows())


def test_private_placement_shares_leave_the_turnover_denominator(tmp_path):
    store = TaiwanShareCapitalStore(data_dir=tmp_path, client=_client())
    assert store.refresh() == 4

    rows = {row["symbol"]: row for row in store.load().to_dicts()}
    assert rows["3055.TWSE"]["issued_shares"] == 114_974_918
    assert rows["3055.TWSE"]["private_shares"] == 16_300_000
    assert rows["6488.TPEX"]["private_shares"] == 2_000_000
    assert rows["2330.TWSE"]["private_shares"] == 0
    assert _denominators(store) == {
        "2330.TWSE": 25_932_370_067.0,
        "3055.TWSE": 98_674_918.0,
        "3105.TPEX": 423_940_384.0,
        "6488.TPEX": 180_000_000.0,
    }


def test_a_row_without_the_private_placement_field_uses_issued_shares(tmp_path):
    twse = [
        {"公司代號": "3055", "已發行普通股數或TDR原股發行股數": "114974918", "出表日期": "1151008"}
    ]
    store = TaiwanShareCapitalStore(data_dir=tmp_path, client=_client(twse=twse, tpex=[]))
    store.refresh()

    assert store.load()["private_shares"].to_list() == [None]
    assert _denominators(store) == {"3055.TWSE": 114_974_918.0}


def test_a_file_written_before_private_placements_were_stored_uses_issued_shares(tmp_path):
    pl.DataFrame(
        {
            "symbol": ["3055.TWSE"],
            "issued_shares": [114_974_918],
            "report_date": ["2026-10-08"],
            "retrieved_at": ["2026-10-08T16:00:00+08:00"],
        }
    ).write_parquet(tmp_path / "share_capital.parquet")

    assert _denominators(TaiwanShareCapitalStore(data_dir=tmp_path)) == {"3055.TWSE": 114_974_918.0}


def test_private_placements_covering_every_issued_share_leave_no_denominator(tmp_path):
    twse = [
        {
            "公司代號": "9999",
            "已發行普通股數或TDR原股發行股數": "1000",
            "私募股數": "1000",
            "出表日期": "1151008",
        }
    ]
    store = TaiwanShareCapitalStore(data_dir=tmp_path, client=_client(twse=twse, tpex=[]))
    store.refresh()

    assert _denominators(store) == {"9999.TWSE": None}


@pytest.mark.parametrize("bad", ["", "n/a", "-5"])
def test_an_unusable_private_placement_count_is_unknown_rather_than_zero(tmp_path, bad):
    twse = [
        {
            "公司代號": "3055",
            "已發行普通股數或TDR原股發行股數": "114974918",
            "私募股數": bad,
            "出表日期": "1151008",
        }
    ]
    store = TaiwanShareCapitalStore(data_dir=tmp_path, client=_client(twse=twse, tpex=[]))
    store.refresh()

    assert store.load()["private_shares"].to_list() == [None]


def test_a_failed_exchange_keeps_its_previous_private_placements(tmp_path):
    TaiwanShareCapitalStore(data_dir=tmp_path, client=_client()).refresh()
    partial = TaiwanShareCapitalStore(
        data_dir=tmp_path, client=_client(tpex_error=RuntimeError("tpex down"))
    )

    assert partial.refresh() == 4
    assert _denominators(partial)["6488.TPEX"] == 180_000_000.0


def test_a_failed_exchange_keeps_rows_of_a_file_written_before_private_placements(tmp_path):
    pl.DataFrame(
        {
            "symbol": ["6488.TPEX"],
            "issued_shares": [182_000_000],
            "report_date": ["2026-10-08"],
            "retrieved_at": ["2026-10-08T16:00:00+08:00"],
        }
    ).write_parquet(tmp_path / "share_capital.parquet")
    partial = TaiwanShareCapitalStore(
        data_dir=tmp_path, client=_client(tpex_error=RuntimeError("tpex down"))
    )

    assert partial.refresh() == 3
    assert _denominators(partial)["6488.TPEX"] == 182_000_000.0


class _Instrument:
    def __init__(self, name: str) -> None:
        self.name = name
        self.instrument_type = "stock"


class _Master:
    def get_instrument(self, symbol):
        return _Instrument(symbol)


class _Meta:
    is_realtime = True

    def to_dict(self) -> dict:
        return {"source": "twse:mis", "is_realtime": True}


class _Quote:
    def __init__(self, volume: float, trade_date) -> None:
        self.volume = volume
        self.amount = None
        self.trade_date = trade_date
        self.source_meta = _Meta()
        self.last_price = self.open = self.high = self.low = self.prev_close = 50.0
        self.change = 0.0
        self.change_pct = 0.0
        self.quote_time = None


class _Realtime:
    def __init__(self, quotes: dict[str, _Quote]) -> None:
        self._quotes = quotes

    def get_quotes(self, symbols, daily_kline_fallback_fn=None):
        return self._quotes


class _NoDaily:
    def latest_date(self):
        return None

    def read_range(self, symbols, start, end):
        return pl.DataFrame()


def test_the_watchlist_divides_by_the_denominator_the_publish_uses(tmp_path, monkeypatch):
    from datetime import date, datetime, timedelta, timezone

    from app.config import settings
    from app.taiwan import share_capital_store
    from app.taiwan.realtime import calendar
    from app.taiwan.watchlist_enrichment import enrich_taiwan_watchlist_rows

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(share_capital_store, "_master_tdr_symbols", lambda: frozenset())
    today = date(2026, 10, 8)
    clock = datetime(2026, 10, 8, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(calendar, "taipei_now", lambda: clock)
    store = TaiwanShareCapitalStore(data_dir=tmp_path / "taiwan")
    (tmp_path / "taiwan").mkdir()
    pl.DataFrame(
        {
            "symbol": ["2434.TWSE", "9999.TWSE"],
            "issued_shares": [37_000_000, 1_000],
            "report_date": ["2026-10-08"] * 2,
            "retrieved_at": ["2026-10-08T16:00:00+08:00"] * 2,
            "private_shares": [27_489_119, 1_000],
        }
    ).write_parquet(store.path)
    volume = 154_832.0

    rows = enrich_taiwan_watchlist_rows(
        ["2434.TWSE", "9999.TWSE"],
        security_master=_Master(),
        realtime_service=_Realtime({s: _Quote(volume, today) for s in ("2434.TWSE", "9999.TWSE")}),
        daily_store=_NoDaily(),
        share_capital=store,
    )

    turnover = {row["symbol"]: row["turnover_rate"] for row in rows}
    denominator = _denominators(store)["2434.TWSE"]
    assert denominator == 9_510_881.0
    assert turnover["2434.TWSE"] == pytest.approx(volume * 100 / denominator)
    assert turnover["9999.TWSE"] is None
