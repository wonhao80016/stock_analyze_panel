"""Share capital refresh treats an empty or renamed exchange answer as a failure; the newest enriched date
counts only partitions that hold a part.parquet."""
from __future__ import annotations

import os
from datetime import date

import polars as pl
import pytest

from app.repository import DataStore, KlineRepository
from app.taiwan.share_capital_store import TaiwanShareCapitalStore
from tests.test_taiwan_share_capital_store import TPEX_ROWS, TWSE_ROWS, _client


def _refreshed(tmp_path) -> TaiwanShareCapitalStore:
    store = TaiwanShareCapitalStore(data_dir=tmp_path, client=_client())
    assert store.refresh() == 3
    return store


@pytest.mark.parametrize(
    "tpex",
    [
        [],
        [{"CompanyCode": "1240", "Shares": "44232373", "Date": "1150921"}],
    ],
    ids=["empty-array", "renamed-fields"],
)
def test_an_exchange_answer_without_rows_keeps_its_previous_rows(tmp_path, tpex):
    _refreshed(tmp_path)

    again = TaiwanShareCapitalStore(data_dir=tmp_path, client=_client(tpex=tpex))
    assert again.refresh() == 3

    assert set(again.load()["symbol"]) == {"1101.TWSE", "2330.TWSE", "1240.TPEX"}


def test_both_exchanges_without_rows_leave_the_file_untouched(tmp_path):
    store = _refreshed(tmp_path)
    before = store.path.read_bytes()

    empty = TaiwanShareCapitalStore(data_dir=tmp_path, client=_client(twse=[], tpex=[]))
    assert empty.refresh() == 0

    assert store.path.read_bytes() == before


def test_the_share_capital_replace_is_retried_while_a_reader_holds_the_file(tmp_path, monkeypatch):
    store = _refreshed(tmp_path)
    real_replace = os.replace
    attempts: list[str] = []

    def held_once(src, dst):
        attempts.append(str(dst))
        if len(attempts) == 1:
            raise PermissionError(13, "The process cannot access the file because it is being used")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", held_once)
    monkeypatch.setattr("app.taiwan.universe.service.time.sleep", lambda _s: None)

    other = TaiwanShareCapitalStore(data_dir=tmp_path, client=_client(twse=TWSE_ROWS[:1], tpex=TPEX_ROWS))
    assert other.refresh() == 2
    assert len(attempts) == 2
    assert set(pl.read_parquet(store.path)["symbol"]) == {"1101.TWSE", "1240.TPEX"}


def test_latest_enriched_date_ignores_a_partition_without_part_parquet(tmp_path):
    root = tmp_path / "kline_daily_enriched"
    published = root / "date=2026-10-22" / "part.parquet"
    published.parent.mkdir(parents=True)
    pl.DataFrame({"symbol": ["2330.TWSE"], "date": [date(2026, 10, 22)]}).write_parquet(published)
    (root / "date=2026-10-23").mkdir()

    repo = KlineRepository(DataStore(tmp_path))

    assert repo.latest_enriched_date("stock") == date(2026, 10, 22)
