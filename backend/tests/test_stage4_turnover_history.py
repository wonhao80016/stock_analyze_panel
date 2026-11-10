"""Each published day keeps the turnover denominator of its own day (stage 4, D5).

Turnover divides by the issued shares current on the day it is published, which only the newest
stored session may use. The daily publish saves that denominator per session, so a later republish
recomputes such a day from its current bars; a day published without one keeps the value it was
published with instead of erasing it.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.indicators import pipeline
from app.indicators.pipeline import invalidate_custom_signals, invalidate_taiwan_limit_profile_cache
from app.taiwan import daily_update, universe
from app.taiwan.corporate_actions import TaiwanCorporateActionStore, corporate_action_schema
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.enrichment.corporate_actions import PAR_CHANGE
from app.taiwan.share_capital_store import SCHEMA as SHARE_SCHEMA
from app.taiwan.share_capital_store import TaiwanShareCapitalStore
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

SHARES = {"2330.TWSE": 25_930_380_458, "6488.TPEX": 182_000_000}
SESSIONS = 40
FIRST_DAY = date(2026, 10, 7)
SECOND_DAY = date(2026, 10, 8)
THIRD_DAY = date(2026, 10, 9)
FINAL_VOLUME_RATIO = 1.02


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


def _bars(days: list[date], symbols: tuple[str, ...] = tuple(SHARES)) -> pl.DataFrame:
    rows = []
    for k, symbol in enumerate(symbols):
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

    def set_shares(shares: dict[str, int], private: dict[str, int] | None = None) -> None:
        frame = pl.DataFrame(
            {
                "symbol": list(shares),
                "issued_shares": list(shares.values()),
                "report_date": ["2026-09-30"] * len(shares),
                "retrieved_at": ["2026-10-01T00:00:00"] * len(shares),
            },
            schema=SHARE_SCHEMA,
        )
        if private is not None:
            frame = frame.with_columns(
                pl.Series("private_shares", [private.get(s, 0) for s in shares], dtype=pl.Int64)
            )
        frame.write_parquet(taiwan / "share_capital.parquet")

    def add_listings(*extra: TaiwanInstrument) -> None:
        listed = [_instrument(s) for s in SHARES] + list(extra)
        pl.DataFrame([inst.to_dict() for inst in listed]).write_parquet(master_path)
        reloaded = TaiwanSecurityMaster(cache_path=master_path)
        assert reloaded.load_cache()
        monkeypatch.setattr(universe, "_default_master", reloaded)
        invalidate_taiwan_limit_profile_cache()

    set_shares(SHARES)
    monkeypatch.setattr(TaiwanShareCapitalStore, "refresh", lambda self: 0)
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

    def publish(clock: date, *, recent: bool = False) -> dict[date, pl.DataFrame]:
        refresher = service(clock)
        refresher.refresh_recent(days=5) if recent else refresher.refresh()
        return _read(root)

    def publish_window(clock: date, start: date, end: date) -> dict[date, pl.DataFrame]:
        service(clock).refresh(start, end)
        return _read(root)

    def publish_daily() -> dict[date, pl.DataFrame]:
        notes: list[str] = []
        assert daily_update.publish_enriched(daily, corporate_actions=_NoReferencePrices(), notes=notes)
        assert notes == []
        return _read(root)

    yield SimpleNamespace(
        root=root,
        taiwan=taiwan,
        daily=daily,
        publish=publish,
        publish_window=publish_window,
        publish_daily=publish_daily,
        set_shares=set_shares,
        add_listings=add_listings,
    )
    invalidate_taiwan_limit_profile_cache()
    invalidate_custom_signals()


class _NoReferencePrices:
    def refresh_publish_window(self, first: date, last: date) -> dict:
        return {}


def _read(root) -> dict[date, pl.DataFrame]:
    return {
        date.fromisoformat(p.parent.name.split("=", 1)[1]): pl.read_parquet(p).sort("symbol")
        for p in root.glob("date=*/part.parquet")
    }


def _turnover(partitions: dict[date, pl.DataFrame], day: date) -> dict[str, float | None]:
    return dict(partitions[day].select("symbol", "turnover_rate").iter_rows())


def _expected(daily: TaiwanDailyStore, day: date, shares: dict[str, int]) -> dict[str, float]:
    bars = daily.scan_range(None, day, day)
    return {
        s: v * 100 / shares[s] for s, v in bars.select("symbol", "volume").iter_rows() if s in shares
    }


def _add_second_day(market) -> None:
    market.daily.write_batch(_bars([SECOND_DAY]))


def _finalize(market, day: date) -> None:
    """Rewrite *day* with the higher volume the exchange finalizes after a provisional fetch."""
    bars = market.daily.scan_range(None, day, day)
    market.daily.write_batch(bars.with_columns(pl.col("volume") * FINAL_VOLUME_RATIO))


@pytest.mark.parametrize("recent", [False, True], ids=["full-republish", "recent-republish"])
def test_a_republish_keeps_the_turnover_an_earlier_day_was_published_with(market, recent):
    first = market.publish(FIRST_DAY)
    published_first = _turnover(first, FIRST_DAY)
    assert published_first == pytest.approx(_expected(market.daily, FIRST_DAY, SHARES))

    _add_second_day(market)
    later = market.publish(SECOND_DAY, recent=recent)

    assert _turnover(later, FIRST_DAY) == pytest.approx(published_first)
    assert _turnover(later, SECOND_DAY) == pytest.approx(
        _expected(market.daily, SECOND_DAY, SHARES)
    )
    for day, frame in later.items():
        if day < FIRST_DAY:
            assert frame["turnover_rate"].null_count() == frame.height, day


def test_an_earlier_day_keeps_its_own_share_count_after_the_count_changes(market):
    market.publish(FIRST_DAY)
    doubled = {symbol: count * 2 for symbol, count in SHARES.items()}
    market.set_shares(doubled)
    _add_second_day(market)

    later = market.publish(SECOND_DAY)

    assert _turnover(later, FIRST_DAY) == pytest.approx(_expected(market.daily, FIRST_DAY, SHARES))
    assert _turnover(later, SECOND_DAY) == pytest.approx(
        _expected(market.daily, SECOND_DAY, doubled)
    )


def test_the_newest_session_is_recomputed_rather_than_kept(market):
    market.publish(FIRST_DAY)
    doubled = {symbol: count * 2 for symbol, count in SHARES.items()}
    market.set_shares(doubled)

    again = market.publish(FIRST_DAY)

    assert _turnover(again, FIRST_DAY) == pytest.approx(_expected(market.daily, FIRST_DAY, doubled))


def test_a_full_republish_forced_by_an_incomplete_publication_keeps_earlier_turnover(market):
    first = market.publish(FIRST_DAY)
    _add_second_day(market)
    marker = market.root.parent / ".matrix_generation_stock.json"
    marker.write_text(
        json.dumps(
            {
                "state": "publishing",
                "generation": "previous",
                "publication_id": "crashed",
                "owner_pid": 2_000_000_000,
            }
        ),
        encoding="utf-8",
    )

    later = market.publish(SECOND_DAY, recent=True)

    assert json.loads(marker.read_text(encoding="utf-8"))["state"] == "ready"
    assert _turnover(later, FIRST_DAY) == pytest.approx(_turnover(first, FIRST_DAY))


def test_an_unreadable_partition_loses_only_its_own_turnover(market, caplog):
    first = market.publish(FIRST_DAY)
    _add_second_day(market)
    broken_day = sorted(first)[0]
    (market.root / f"date={broken_day.isoformat()}" / "part.parquet").write_bytes(b"not parquet")

    later = market.publish(SECOND_DAY)

    assert f"date={broken_day.isoformat()} unreadable" in caplog.text
    assert _turnover(later, FIRST_DAY) == pytest.approx(_turnover(first, FIRST_DAY))
    assert later[broken_day]["turnover_rate"].null_count() == later[broken_day].height


def test_the_published_turnover_divides_by_issued_shares_minus_private_placements(market):
    market.set_shares(SHARES, private={"6488.TPEX": 32_000_000})

    published = market.publish(FIRST_DAY)

    tradable = {**SHARES, "6488.TPEX": 150_000_000}
    assert _turnover(published, FIRST_DAY) == pytest.approx(
        _expected(market.daily, FIRST_DAY, tradable)
    )


def test_a_window_ending_before_the_newest_day_keeps_the_turnover_of_its_last_day(market):
    first = market.publish(FIRST_DAY)
    _add_second_day(market)
    later = market.publish(SECOND_DAY)

    again = market.publish_window(SECOND_DAY, FIRST_DAY, FIRST_DAY)

    assert _turnover(again, FIRST_DAY) == pytest.approx(_turnover(first, FIRST_DAY))
    assert _turnover(again, SECOND_DAY) == pytest.approx(_turnover(later, SECOND_DAY))


@pytest.mark.parametrize("dtype", [pl.Float32, pl.Int64])
def test_a_partition_storing_turnover_as_another_number_type_keeps_it(market, caplog, dtype):
    market.publish(FIRST_DAY)
    path = market.root / f"date={FIRST_DAY.isoformat()}" / "part.parquet"
    stored = pl.read_parquet(path).with_columns(pl.col("turnover_rate").cast(dtype))
    stored.write_parquet(path)
    _add_second_day(market)

    later = market.publish(SECOND_DAY)

    assert "unreadable" not in caplog.text
    expected = dict(stored.select("symbol", pl.col("turnover_rate").cast(pl.Float64)).iter_rows())
    assert _turnover(later, FIRST_DAY) == pytest.approx(expected)


def test_a_session_finalized_after_the_next_one_is_recomputed_from_its_saved_shares(market):
    first = market.publish_daily()
    provisional = _turnover(first, FIRST_DAY)
    _add_second_day(market)
    _finalize(market, FIRST_DAY)

    later = market.publish_daily()

    final = _expected(market.daily, FIRST_DAY, SHARES)
    assert final != pytest.approx(provisional)
    assert _turnover(later, FIRST_DAY) == pytest.approx(final)
    assert _turnover(later, SECOND_DAY) == pytest.approx(
        _expected(market.daily, SECOND_DAY, SHARES)
    )


def test_a_recomputed_session_keeps_its_saved_shares_after_the_count_changes(market):
    market.publish_daily()
    doubled = {symbol: count * 2 for symbol, count in SHARES.items()}
    market.set_shares(doubled)
    _add_second_day(market)
    _finalize(market, FIRST_DAY)

    later = market.publish_daily()

    assert _turnover(later, FIRST_DAY) == pytest.approx(_expected(market.daily, FIRST_DAY, SHARES))
    assert _turnover(later, SECOND_DAY) == pytest.approx(
        _expected(market.daily, SECOND_DAY, doubled)
    )


def test_a_session_published_without_saved_shares_keeps_its_published_turnover(market):
    first = market.publish(FIRST_DAY)
    _add_second_day(market)
    market.publish_daily()
    market.daily.write_batch(_bars([THIRD_DAY]))
    _finalize(market, FIRST_DAY)
    _finalize(market, SECOND_DAY)

    later = market.publish_daily()

    assert _turnover(later, FIRST_DAY) == pytest.approx(_turnover(first, FIRST_DAY))
    assert _turnover(later, SECOND_DAY) == pytest.approx(
        _expected(market.daily, SECOND_DAY, SHARES)
    )
    for day, frame in later.items():
        if day < FIRST_DAY:
            assert frame["turnover_rate"].null_count() == frame.height, day


def test_a_recomputed_session_divides_its_stored_volume_not_the_restated_one(market):
    market.publish_daily()
    _add_second_day(market)
    _finalize(market, FIRST_DAY)
    par_change = {
        "symbol": "2330.TWSE", "date": SECOND_DAY, "kind": PAR_CHANGE, "pre_close": 400.0,
        "ref_price": 100.0, "limit_up": None, "limit_down": None, "open_ref": 100.0,
        "ex_factor": 4.0, "official_label": None, "provider": "twse", "source": "test",
        "source_url": "u", "retrieved_at": None, "trade_date": SECOND_DAY, "status": "official",
    }
    TaiwanCorporateActionStore(market.taiwan / "corporate_actions").upsert(
        pl.DataFrame([par_change], schema=corporate_action_schema())
    )

    later = market.publish_daily()

    stored = market.daily.scan_range(None, FIRST_DAY, FIRST_DAY).filter(pl.col("symbol") == "2330.TWSE")
    restated = later[FIRST_DAY].filter(pl.col("symbol") == "2330.TWSE")
    assert restated["volume"].item() == pytest.approx(stored["volume"].item() * 4)
    assert _turnover(later, FIRST_DAY) == pytest.approx(_expected(market.daily, FIRST_DAY, SHARES))


def test_etfs_and_tdrs_stay_null_when_a_session_is_recomputed(market):
    etf, tdr = "0050.TWSE", "912000.TWSE"
    market.add_listings(
        replace(_instrument(etf), instrument_type="etf", raw_category="ETF"),
        replace(_instrument(tdr), security_class="tdr"),
    )
    market.set_shares({**SHARES, tdr: 50_000_000})
    market.daily.write_batch(_bars(_sessions(FIRST_DAY), (etf, tdr)))
    market.publish_daily()
    market.daily.write_batch(_bars([SECOND_DAY], (*SHARES, etf, tdr)))
    _finalize(market, FIRST_DAY)

    later = market.publish_daily()

    first = _turnover(later, FIRST_DAY)
    assert {s: first[s] for s in SHARES} == pytest.approx(_expected(market.daily, FIRST_DAY, SHARES))
    for day, frame in later.items():
        held = dict(frame.select("symbol", "turnover_rate").iter_rows())
        assert held[etf] is None and held[tdr] is None, day


def test_an_unreadable_saved_denominator_keeps_the_published_turnover(market, caplog):
    first = market.publish_daily()
    saved = market.taiwan / "share_capital_sessions" / f"date={FIRST_DAY.isoformat()}" / "part.parquet"
    assert saved.exists()
    saved.write_bytes(b"not parquet")
    _add_second_day(market)
    _finalize(market, FIRST_DAY)

    later = market.publish_daily()

    assert f"taiwan share capital for {FIRST_DAY.isoformat()} unreadable" in caplog.text
    assert _turnover(later, FIRST_DAY) == pytest.approx(_turnover(first, FIRST_DAY))
