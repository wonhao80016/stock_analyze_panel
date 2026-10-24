"""A window publish failing midway is retried once in full, and every publish reaches back to the first fetched day."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.enriched_generation import (
    EnrichedPublication,
    enriched_publication_incomplete,
    get_enriched_generation,
)
from app.taiwan import bootstrap
from app.taiwan.bootstrap import TaiwanBootstrapService
from app.taiwan.daily_store import TaiwanDailyStore
from tests.test_stage4g_bootstrap_publish import _run, bundle_job  # noqa: F401
from tests.test_stage4g_enriched_partial_publication import (  # noqa: F401
    SESSIONS,
    _cached_security_master,
    _refetch_newest_day,
    published,
)


def _failing_writes(monkeypatch, fails) -> list:
    real = EnrichedPublication.write_parquet
    calls: list = []

    def write(self, df, out):
        calls.append(out)
        if fails(len(calls)):
            raise PermissionError(13, "in use")
        return real(self, df, out)

    monkeypatch.setattr(EnrichedPublication, "write_parquet", write)
    return calls


def test_a_window_publish_failing_midway_is_republished_in_full(published, tmp_path, monkeypatch):  # noqa: F811
    _refetch_newest_day(published, tmp_path, 1234.0)
    calls = _failing_writes(monkeypatch, lambda n: n == 2)

    published.refresh_recent(days=3)

    assert len(calls) > 3
    assert enriched_publication_incomplete(tmp_path) is False
    assert get_enriched_generation(tmp_path)
    newest = pl.read_parquet(tmp_path / "kline_daily_enriched" / f"date={SESSIONS[-1].isoformat()}" / "part.parquet")
    assert newest["raw_close"].to_list() == [1234.0]


def test_the_full_republish_is_tried_only_once(published, tmp_path, monkeypatch):  # noqa: F811
    _refetch_newest_day(published, tmp_path, 1234.0)
    calls = _failing_writes(monkeypatch, lambda n: n >= 2)

    with pytest.raises(PermissionError):
        published.refresh_recent(days=3)

    assert len(calls) == 3
    assert enriched_publication_incomplete(tmp_path) is True


def test_a_bootstrap_that_only_fetches_publishes_from_the_first_fetched_day(bundle_job, monkeypatch):  # noqa: F811
    svc, _order = bundle_job
    _run(svc)
    seen: list = []
    monkeypatch.setattr(
        bootstrap, "publish_enriched", lambda only_if_behind=False, since=None: seen.append(since) or True
    )

    job = _run(svc)

    assert job.status == "success", job.error
    assert seen == [date(2024, 1, 3)]


def test_update_to_latest_publishes_from_the_first_refetched_day(tmp_path):
    daily_dir = tmp_path / "taiwan" / "daily"
    store = TaiwanDailyStore(data_dir=daily_dir)
    store.write_batch(pl.DataFrame({
        "symbol": ["2330.TWSE", "6488.TPEX"], "date": [date(2024, 1, 2)] * 2, "open": [1.0, 1.0],
        "high": [1.0, 1.0], "low": [1.0, 1.0], "close": [593.0, 420.0], "volume": [1.0, 1.0],
        "amount": [1.0, 1.0], "quote_ts": pl.Series([None, None], dtype=pl.Int64),
    }))
    refresh = MagicMock()
    refresh.refresh_dates.return_value = {"dates_fetched": 30}
    seen: list = []
    svc = TaiwanBootstrapService(
        store=store, refresh_service=refresh,
        publish=lambda only_if_behind, since=None: seen.append((only_if_behind, since)) or True,
    )

    svc.update_to_latest()

    assert seen == [(False, refresh.refresh_dates.call_args.kwargs["start_date"])]
