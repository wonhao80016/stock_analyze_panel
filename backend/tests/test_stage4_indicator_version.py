"""A change of indicator definitions republishes every enriched partition once (D7).

The full publication records the indicator-definition version next to its factor set. A table
published under another version, or without one, is rewritten in full by the next refresh, even
a recent one; that rewrite keeps each earlier day's turnover, changes the generation and the
partition fingerprints the derived caches key on, and later refreshes go back to the window.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.backtest.matrix import _partition_fingerprints
from app.enriched_generation import get_enriched_generation
from app.indicators import pipeline
from app.indicators.pipeline import invalidate_custom_signals, invalidate_taiwan_limit_profile_cache
from app.taiwan import universe
from app.taiwan.corporate_actions import TaiwanCorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import FACTOR_SET_FILE, TaiwanEnrichedRefreshService
from app.taiwan.share_capital_store import SCHEMA as SHARE_SCHEMA
from app.taiwan.share_capital_store import TaiwanShareCapitalStore
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

SHARES = {"2330.TWSE": 25_930_380_458, "6488.TPEX": 182_000_000}
SESSIONS = 70
FIRST_DAY = date(2026, 10, 7)
SECOND_DAY = date(2026, 10, 8)
STALE = 1e6
VERSION_KEY = "indicator_version"


def _sessions(last: date) -> list[date]:
    out, day = [], last
    while len(out) < SESSIONS:
        if day.weekday() < 5:
            out.append(day)
        day -= timedelta(days=1)
    return out[::-1]


def _instrument(symbol: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=code,
        instrument_type="stock",
        listing_status="active",
        listing_date="2000-01-01",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="股票",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-10-01",
    )


def _bars(days: list[date]) -> pl.DataFrame:
    rows = []
    for k, symbol in enumerate(SHARES):
        for day in days:
            t = day.toordinal()
            close = (100.0 + 40 * k) * (1 + 0.04 * math.sin(t / 5))
            volume = float(round(20_000_000 * (1 + 0.3 * math.sin(t / 3 + k))))
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "open": close * 0.99,
                    "high": close * 1.01,
                    "low": close * 0.98,
                    "close": close,
                    "volume": volume,
                    "amount": close * volume,
                    "quote_ts": None,
                }
            )
    return pl.DataFrame(rows, schema_overrides={"quote_ts": pl.Int64})


@pytest.fixture
def market(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    taiwan = tmp_path / "taiwan"
    master_path = taiwan / "security_master.parquet"
    master_path.parent.mkdir(parents=True)
    pl.DataFrame([_instrument(s).to_dict() for s in SHARES]).write_parquet(master_path)
    master = TaiwanSecurityMaster(cache_path=master_path)
    assert master.load_cache()
    monkeypatch.setattr(universe, "_default_master", master)
    invalidate_taiwan_limit_profile_cache()
    invalidate_custom_signals()
    pl.DataFrame(
        {
            "symbol": list(SHARES),
            "issued_shares": list(SHARES.values()),
            "report_date": ["2026-09-30"] * len(SHARES),
            "retrieved_at": ["2026-10-01T00:00:00"] * len(SHARES),
        },
        schema=SHARE_SCHEMA,
    ).write_parquet(taiwan / "share_capital.parquet")
    daily = TaiwanDailyStore(data_dir=taiwan / "daily")
    daily.write_batch(_bars(_sessions(FIRST_DAY)))
    root = tmp_path / "kline_daily_enriched"

    def service(clock: date) -> TaiwanEnrichedRefreshService:
        monkeypatch.setattr(pipeline, "cn_today", lambda: clock)
        return TaiwanEnrichedRefreshService(
            daily_store=daily,
            enriched_root=root,
            share_capital=TaiwanShareCapitalStore(taiwan),
            corporate_actions=TaiwanCorporateActionStore(taiwan / "corporate_actions"),
        )

    yield SimpleNamespace(root=root, daily=daily, service=service, data_dir=tmp_path)
    invalidate_taiwan_limit_profile_cache()
    invalidate_custom_signals()


def _read(root) -> dict[date, pl.DataFrame]:
    return {
        date.fromisoformat(p.parent.name.split("=", 1)[1]): pl.read_parquet(p).sort("symbol")
        for p in root.glob("date=*/part.parquet")
    }


def _mtimes(root) -> dict[str, int]:
    return {p.parent.name: p.stat().st_mtime_ns for p in root.glob("date=*/part.parquet")}


def _record(market) -> dict:
    return json.loads((market.data_dir / FACTOR_SET_FILE).read_text(encoding="utf-8"))


def _publish_under_old_definitions(market) -> dict[date, pl.DataFrame]:
    """A full publish whose rows and record look as if an earlier release wrote them."""
    market.service(FIRST_DAY).refresh()
    for day, frame in _read(market.root).items():
        frame.with_columns(pl.lit(STALE).alias("ema20")).write_parquet(
            market.root / f"date={day.isoformat()}" / "part.parquet"
        )
    record = _record(market)
    record.pop(VERSION_KEY, None)
    (market.data_dir / FACTOR_SET_FILE).write_text(json.dumps(record), encoding="utf-8")
    market.daily.write_batch(_bars([SECOND_DAY]))
    return _read(market.root)


def _turnover(partitions: dict[date, pl.DataFrame], day: date) -> dict[str, float | None]:
    return dict(partitions[day].select("symbol", "turnover_rate").iter_rows())


def test_a_full_publish_records_the_indicator_definition_version(market):
    market.service(FIRST_DAY).refresh()

    assert _record(market).get(VERSION_KEY) == getattr(
        pipeline, "INDICATOR_DEFINITION_VERSION", "missing"
    )


def test_a_table_from_older_definitions_is_rewritten_once_by_the_next_recent_refresh(market):
    before = _publish_under_old_definitions(market)
    old_days = sorted(before)
    generation = get_enriched_generation(market.data_dir)
    fingerprints = _partition_fingerprints(market.root, old_days[0], old_days[-1])

    market.service(SECOND_DAY).refresh_recent(days=5)

    after = _read(market.root)
    assert all(after[day]["ema20"].ne(STALE).all() for day in old_days)
    assert after[old_days[-1]]["ema20"].null_count() == 0
    assert _turnover(after, FIRST_DAY) == pytest.approx(_turnover(before, FIRST_DAY))
    assert all(v is not None for v in _turnover(after, FIRST_DAY).values())
    assert _record(market).get(VERSION_KEY) == pipeline.INDICATOR_DEFINITION_VERSION
    assert get_enriched_generation(market.data_dir) != generation
    changed = _partition_fingerprints(market.root, old_days[0], old_days[-1])
    assert all(changed[key] != fingerprints[key] for key in fingerprints)

    published = _mtimes(market.root)
    market.service(SECOND_DAY).refresh_recent(days=5)

    again = _mtimes(market.root)
    untouched = [name for name in published if name < f"date={old_days[-5].isoformat()}"]
    assert untouched
    assert all(again[name] == published[name] for name in untouched)


def test_a_failed_forced_republish_is_retried_in_full_by_the_next_refresh(market, monkeypatch):
    _publish_under_old_definitions(market)
    original = TaiwanEnrichedRefreshService._publish_partitions

    def fail_after_one(self, enriched, publication=None):
        first = enriched["date"].min()
        original(self, enriched.filter(pl.col("date") == first), publication)
        raise OSError("disk full")

    monkeypatch.setattr(TaiwanEnrichedRefreshService, "_publish_partitions", fail_after_one)
    with pytest.raises(OSError):
        market.service(SECOND_DAY).refresh_recent(days=5)
    monkeypatch.setattr(TaiwanEnrichedRefreshService, "_publish_partitions", original)

    market.service(SECOND_DAY).refresh_recent(days=5)

    after = _read(market.root)
    assert all(frame["ema20"].ne(STALE).all() for frame in after.values())
    assert _record(market).get(VERSION_KEY) == pipeline.INDICATOR_DEFINITION_VERSION
