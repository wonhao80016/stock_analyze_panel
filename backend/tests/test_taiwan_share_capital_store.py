"""Issued shares as the Taiwan turnover denominator.

The number is issued common shares, not free float, and it is current-only.
Both properties are load-bearing: the first is why every Taiwan turnover figure
reads low, the second is why a historical one must stay absent.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan.share_capital_store import TaiwanShareCapitalStore

TWSE_ROWS = [
    {"公司代號": "1101", "已發行普通股數或TDR原股發行股數": "7523181742", "出表日期": "1150921"},
    {"公司代號": "2330", "已發行普通股數或TDR原股發行股數": "25930380458", "出表日期": "1150921"},
]
TPEX_ROWS = [
    {"SecuritiesCompanyCode": "1240", "IssueShares": "44232373", "Date": "1150921"},
]


def _client(twse=TWSE_ROWS, tpex=TPEX_ROWS, twse_error=None, tpex_error=None):
    client = MagicMock()

    def get(url):
        if "twse" in url:
            if twse_error:
                raise twse_error
            return MagicMock(json=lambda: twse)
        if tpex_error:
            raise tpex_error
        return MagicMock(json=lambda: tpex)

    client.get.side_effect = get
    return client


def _store(tmp_path, **kw) -> TaiwanShareCapitalStore:
    return TaiwanShareCapitalStore(data_dir=tmp_path, client=_client(**kw))


def test_both_exchanges_land_with_canonical_symbols(tmp_path):
    store = _store(tmp_path)
    assert store.refresh() == 3
    rows = {r["symbol"]: r for r in store.load().to_dicts()}
    assert set(rows) == {"1101.TWSE", "2330.TWSE", "1240.TPEX"}
    assert rows["2330.TWSE"]["issued_shares"] == 25930380458
    assert rows["1240.TPEX"]["report_date"] == "2026-09-21"


def test_one_exchange_failing_still_prices_the_other(tmp_path):
    """A partial refresh beats none: the listings it covers can still be screened."""
    store = _store(tmp_path, tpex_error=RuntimeError("tpex down"))
    assert store.refresh() == 2
    assert set(store.load()["symbol"]) == {"1101.TWSE", "2330.TWSE"}


def test_an_exchange_that_fails_keeps_its_rows_from_the_previous_refresh(tmp_path):
    """Dropping them would blank that exchange's turnover and market cap until the next good refresh."""
    _store(tmp_path).refresh()
    partial = TaiwanShareCapitalStore(data_dir=tmp_path, client=_client(tpex_error=RuntimeError("tpex down")))

    assert partial.refresh() == 3
    assert set(partial.load()["symbol"]) == {"1101.TWSE", "2330.TWSE", "1240.TPEX"}


def test_both_failing_leaves_the_previous_file_untouched(tmp_path):
    store = _store(tmp_path)
    store.refresh()
    before = store.load()

    broken = TaiwanShareCapitalStore(
        data_dir=tmp_path,
        client=_client(twse_error=RuntimeError("down"), tpex_error=RuntimeError("down")),
    )
    assert broken.refresh() == 0
    assert broken.load().equals(before)


@pytest.mark.parametrize("bad", ["", "0", "-5", "n/a", None])
def test_a_row_without_a_usable_count_is_dropped_rather_than_zeroed(tmp_path, bad):
    """A zero denominator would make turnover_rate either infinite or silently null."""
    store = _store(tmp_path, twse=[{"公司代號": "1101", "已發行普通股數或TDR原股發行股數": bad,
                                    "出表日期": "1150921"}], tpex=[])
    assert store.refresh() == 0


def test_as_instruments_names_the_column_the_pipeline_joins_on(tmp_path):
    store = _store(tmp_path)
    store.refresh()
    inst = store.as_instruments()
    assert inst.columns == ["symbol", "float_shares"]
    assert inst.filter(pl.col("symbol") == "1101.TWSE")["float_shares"][0] == 7523181742.0


def test_a_missing_file_reads_as_empty_rather_than_raising(tmp_path):
    store = TaiwanShareCapitalStore(data_dir=tmp_path / "nothing-here")
    assert store.load().is_empty()
    assert store.as_instruments().is_empty()


# The reason this store is allowed to exist at all: docs/taiwan-share-capital-
# phase-6h.md rules out applying a current share count to a past date, and the
# pipeline enforces that through apply_historical_float_shares. Supplying a
# denominator must not quietly reopen it.
def test_the_current_count_never_reaches_a_historical_date():
    from datetime import date

    from app.share_capital import apply_historical_float_shares

    rows = pl.DataFrame({
        "symbol": ["2330.TWSE", "2330.TWSE", "600000.SH"],
        "date": [date(2026, 9, 22), date(2026, 9, 18), date(2026, 9, 18)],
        "float_shares": [25930380458.0, 25930380458.0, 1.0],
    })
    out = apply_historical_float_shares(rows, None, today=date(2026, 9, 22))
    got = {
        (s, d): v
        for s, d, v in zip(out["symbol"], out["date"], out["float_shares"])
    }

    assert got[("2330.TWSE", date(2026, 9, 22))] == 25930380458.0
    # Same Taiwan symbol, previous session: the denominator is withdrawn.
    assert got[("2330.TWSE", date(2026, 9, 18))] is None
    # Non-Taiwan markets have a point-in-time source, so they keep theirs.
    assert got[("600000.SH", date(2026, 9, 18))] == 1.0


def test_the_enriched_publish_asks_the_store_for_its_denominator(tmp_path):
    """Wiring check: the publisher must pass instruments, or the column never exists."""
    import inspect

    from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService

    src = inspect.getsource(TaiwanEnrichedRefreshService.refresh)
    assert "share_capital.as_instruments()" in src
    assert "instruments=instruments" in src


def test_a_column_drifted_file_reads_as_no_denominator_rather_than_raising(tmp_path):
    """The caller publishes the whole enriched table; it must not stop over this."""
    path = tmp_path / "share_capital.parquet"
    pl.DataFrame({"symbol": ["2330.TWSE"], "shares": [1]}).write_parquet(path)
    assert TaiwanShareCapitalStore(data_dir=tmp_path).as_instruments().is_empty()


# ── mtime-keyed read cache: kline's per-second hot path and quote_service's
# ~6s poll both call load()/as_instruments() repeatedly for an unchanged file. ──


def test_load_is_cached_across_instances_until_the_file_changes(tmp_path):
    import os

    store = _store(tmp_path)
    store.refresh()
    path = tmp_path / "share_capital.parquet"
    original_mtime = path.stat().st_mtime
    first = TaiwanShareCapitalStore(data_dir=tmp_path).load()

    # Corrupt the file but pin its mtime back to the original value: only a real
    # mtime-keyed cache survives this unchanged — a fresh read would blow up on
    # the bad bytes (or at least return different rows).
    path.write_bytes(b"not a parquet file")
    os.utime(path, (original_mtime, original_mtime))

    cached = TaiwanShareCapitalStore(data_dir=tmp_path).load()
    assert cached.equals(first)


def test_load_reflects_a_refresh_that_changes_the_file(tmp_path):
    store = TaiwanShareCapitalStore(data_dir=tmp_path, client=_client(twse=TWSE_ROWS, tpex=[]))
    store.refresh()
    before = TaiwanShareCapitalStore(data_dir=tmp_path).load()

    # A disjoint symbol set (TPEx-only) proves the reload picked up new content,
    # reusing the module's existing ASCII-keyed TPEX_ROWS fixture rather than a
    # one-off row (TWSE_ROWS/TPEX_ROWS mirror the exchanges' own field names).
    other = TaiwanShareCapitalStore(data_dir=tmp_path, client=_client(twse=[], tpex=TPEX_ROWS))
    assert other.refresh() == 1
    after = TaiwanShareCapitalStore(data_dir=tmp_path).load()

    assert not after.equals(before)
    assert set(after["symbol"]) == {"1240.TPEX"}


# The order matters and nothing else pins it: publishing first would price today's
# partition with yesterday's share counts, and the mistake is invisible - every
# number still looks plausible.
def test_the_daily_update_refreshes_share_capital_before_publishing():
    import inspect
    import re

    from app.taiwan.daily_update import TaiwanDailyUpdateService

    src = inspect.getsource(TaiwanDailyUpdateService.run_update)
    capital = re.search(r"TaiwanShareCapitalStore\(\)\.refresh\(\)", src)
    publish = re.search(r"TaiwanEnrichedRefreshService\(\)\.refresh_recent\(\)", src)
    assert capital and publish, "both steps must run in the daily update"
    assert capital.start() < publish.start()


def test_a_broadcast_share_count_never_prices_Taiwan_history():
    """The backtest matrix does not go through apply_historical_float_shares.

    A vector float_shares is one current count stretched over the whole time axis,
    so letting it through would price every past bar with a count announced later.
    """
    import numpy as np

    from app.backtest.matrix import _populate_matrix_derived_arrays

    bars, assets = 4, 2
    arrays = {"volume": np.full((bars, assets), 1000.0, dtype=np.float32)}
    fields = {
        "turnover_rate": np.zeros((bars, assets), dtype=np.float32),
        "float_shares": np.zeros((bars, assets), dtype=np.float32),
    }
    instruments = pl.DataFrame({
        "symbol": ["2330.TWSE", "1101.TWSE"],
        "float_shares": [1_000_000.0, 2_000_000.0],
    })
    _populate_matrix_derived_arrays(
        ["2330.TWSE", "1101.TWSE"],
        arrays,
        fields,
        frozenset({"turnover_rate"}),
        instruments,
        np.zeros((bars, assets), dtype=bool),
        parquet_fields=[],
        vector_fields=["float_shares"],
    )
    assert not np.isfinite(fields["turnover_rate"]).any()
