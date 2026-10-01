"""Taiwan forward adjustment: enriched prices are divided by the corporate-action factors.

Direction: every bar's close / raw_close is 1 / (product of ex_factor over the events dated
after it), so the newest bar keeps its traded price and history moves onto its scale.
Fixtures are the official responses of test_taiwan_corporate_actions.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.enriched_generation import enriched_publication_incomplete
from app.indicators.pipeline import _apply_adj_factor, compute_enriched, compute_limit_signals
from app.taiwan.abnormal_diagnostics import TaiwanAbnormalDiagnosticsService
from app.taiwan.corporate_actions import (
    TaiwanCorporateActionStore,
    actions_frame,
    adjustment_factors,
    corporate_action_schema,
    factors_between,
    forward_adjusted,
    official_ratio,
    today_factors,
)
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.enrichment.corporate_actions import (
    ETF_SPLIT,
    EXDIV,
    PAR_CHANGE,
    REDUCTION,
    TwseCapitalReductionAdapter,
    TwseEtfSplitAdapter,
    TwseExRightsAdapter,
    TwseParValueChangeAdapter,
)
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
from app.taiwan.universe import get_security_master

FIXTURES = Path(__file__).parent / "fixtures" / "taiwan_corporate_actions"
Y2024, Y2026_END = date(2024, 1, 1), date(2026, 12, 31)


@pytest.fixture(autouse=True)
def _cached_security_master(monkeypatch) -> None:
    """Serve the shared master from the configured cache; a master missing its cache fetches the directory."""
    from app.taiwan import universe
    from app.taiwan.data_root import taiwan_data_root

    master = universe.TaiwanSecurityMaster(cache_path=taiwan_data_root() / "security_master.parquet")
    assert master.load_cache(), "tests need the security master cache under DATA_DIR/taiwan"
    monkeypatch.setattr(universe, "_default_master", master)


def _official(adapter, name: str, start: date = Y2024) -> pl.DataFrame:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return actions_frame(adapter.parse_payload(payload, start, Y2026_END, "u"))


def _factor(factors: pl.DataFrame, symbol: str, d: date) -> dict:
    return factors.filter((pl.col("symbol") == symbol) & (pl.col("trade_date") == d)).row(0, named=True)


def _event(symbol: str, d: date, kind: str, pre: float, ref: float, up: float | None = None, down: float | None = None) -> dict:
    return {
        "symbol": symbol, "date": d, "kind": kind, "pre_close": pre, "ref_price": ref,
        "limit_up": up, "limit_down": down, "open_ref": ref, "ex_factor": pre / ref, "official_label": None,
        "provider": "twse", "source": "test", "source_url": "u", "retrieved_at": None, "trade_date": d,
        "status": "official",
    }


def _events(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(list(rows), schema=corporate_action_schema())


def test_splits_and_par_value_changes_use_the_official_ratio():
    splits = adjustment_factors(_official(TwseEtfSplitAdapter(), "twtcau_2024_2026.json"), Y2026_END)
    par = adjustment_factors(_official(TwseParValueChangeAdapter(), "twtb8u_2024_2026.json"), Y2026_END)

    assert 188.65 / 47.16 != 4.0
    assert _factor(splits, "0050.TWSE", date(2025, 6, 18)) == {
        "symbol": "0050.TWSE", "trade_date": date(2025, 6, 18), "ex_factor": 4.0, "volume_factor": 4.0,
    }
    assert _factor(par, "2327.TWSE", date(2025, 8, 25))["ex_factor"] == 4.0
    assert _factor(par, "4763.TWSE", date(2025, 6, 30))["ex_factor"] == 10.0


def test_the_official_ratio_stays_within_one_reference_tick_of_the_published_prices():
    frames = [
        _official(TwseEtfSplitAdapter(), "twtcau_2024_2026.json"),
        _official(TwseParValueChangeAdapter(), "twtb8u_2024_2026.json"),
    ]
    rows = pl.concat(frames).select("pre_close", "ref_price").rows()

    assert len(rows) == 18
    for pre, ref in rows:
        quotient = pre / ref
        assert abs(official_ratio(pre, ref) - quotient) / quotient <= 0.01 / (ref - 0.01)


def test_official_ratio_prefers_the_simplest_fraction():
    assert official_ratio(188.65, 47.16) == 4.0
    assert official_ratio(10.0, 40.0) == 0.25
    assert official_ratio(100.0, 33.37) == pytest.approx(100.0 / 33.37)


def test_dividends_and_reductions_divide_by_pre_close_over_reference():
    dividends = adjustment_factors(_official(TwseExRightsAdapter(), "twt49u_2026_excerpt.json", date(2026, 1, 1)), Y2026_END)
    reductions = adjustment_factors(_official(TwseCapitalReductionAdapter(), "twtauu_2024_2026.json"), Y2026_END)

    realtek = _factor(dividends, "2379.TWSE", date(2026, 9, 8))
    assert (realtek["ex_factor"], realtek["volume_factor"]) == (729.0 / 704.0, 1.0)
    reduction = _factor(reductions, "3432.TWSE", date(2024, 1, 22))
    assert reduction["ex_factor"] == reduction["volume_factor"] == 10.65 / 19.69


def test_events_after_the_target_session_are_not_factors():
    events = _events(
        _event("2379.TWSE", date(2026, 9, 8), EXDIV, 729.0, 704.0),
        _event("9927.TWSE", date(2026, 10, 1), EXDIV, 70.10, 65.10),
    )

    assert adjustment_factors(events, date(2026, 9, 30))["symbol"].to_list() == ["2379.TWSE"]
    assert adjustment_factors(events, date(2026, 10, 1)).height == 2


def test_a_same_day_reduction_and_dividend_give_one_factor_from_the_resumption():
    events = _events(
        _event("3356.TWSE", date(2026, 9, 21), EXDIV, 70.0, 60.0),
        _event("3356.TWSE", date(2026, 9, 21), REDUCTION, 70.0, 68.04),
    )

    factors = adjustment_factors(events, date(2026, 9, 30))

    assert factors.rows() == [("3356.TWSE", date(2026, 9, 21), 70.0 / 68.04, 70.0 / 68.04)]


def test_the_provider_serves_the_factors_from_the_corporate_action_table(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    TaiwanCorporateActionStore().upsert(_events(
        _event("0050.TWSE", date(2025, 6, 18), ETF_SPLIT, 188.65, 47.16),
        _event("2379.TWSE", date(2026, 9, 8), EXDIV, 729.0, 704.0),
    ))
    from datetime import datetime

    provider = TaiwanHybridProvider()
    got = provider.get_adj_factors(["0050.TWSE", "2379.TWSE"], end_time=datetime(2026, 9, 7))

    assert provider.capabilities.adj_factor is True
    assert got.rows() == [("0050.TWSE", date(2025, 6, 18), 4.0, 4.0)]
    assert not (tmp_path / "adj_factor").exists()


D = [date(2026, 3, d) for d in (2, 3, 4, 5, 6, 9, 10)]


def _bars(symbol: str, closes: list[float], volume: float = 1000.0) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [symbol] * len(closes), "date": D[: len(closes)],
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [volume] * len(closes), "amount": [c * volume for c in closes],
    })


def test_close_over_raw_close_is_the_inverse_product_of_the_later_factors():
    factors = pl.DataFrame({
        "symbol": ["0050.TWSE", "0050.TWSE"], "trade_date": [D[1], D[3]],
        "ex_factor": [1.02, 4.0], "volume_factor": [1.0, 4.0],
    })

    out = compute_enriched(_bars("0050.TWSE", [200.0, 196.0, 198.0, 50.0, 51.0]), factors=factors).sort("date")

    ratios = (out["close"] / out["raw_close"]).to_list()
    assert ratios == pytest.approx([1 / (1.02 * 4.0), 1 / 4.0, 1 / 4.0, 1.0, 1.0])
    assert out["close"][-1] == out["raw_close"][-1] == 51.0
    assert out["ex_rights"].to_list() == [False, True, False, True, False]


def test_volume_is_restated_only_across_share_count_events():
    factors = pl.DataFrame({
        "symbol": ["0050.TWSE", "0050.TWSE"], "trade_date": [D[1], D[3]],
        "ex_factor": [1.02, 4.0], "volume_factor": [1.0, 4.0],
    })

    adjusted = _apply_adj_factor(_bars("0050.TWSE", [200.0, 196.0, 198.0, 50.0, 51.0]), factors).sort("date")

    assert adjusted["volume"].to_list() == [4000.0, 4000.0, 4000.0, 1000.0, 1000.0]
    assert adjusted["amount"].to_list() == [200000.0, 196000.0, 198000.0, 50000.0, 51000.0]


def test_factors_without_a_volume_column_leave_volume_alone():
    """Regression guard: A-share adj_factor tables carry only ex_factor."""
    factors = pl.DataFrame({"symbol": ["600000.SH"], "trade_date": [D[2]], "ex_factor": [2.0]})

    adjusted = _apply_adj_factor(_bars("600000.SH", [20.0, 20.0, 10.0, 10.0]), factors).sort("date")

    assert adjusted["volume"].to_list() == [1000.0] * 4
    assert adjusted["close"].to_list() == [10.0, 10.0, 10.0, 10.0]


def _limit_frame(symbol: str, raw: list[float], factors: pl.DataFrame) -> pl.DataFrame:
    return compute_enriched(_bars(symbol, raw), factors=factors).sort("date")


def test_every_past_ex_rights_bar_takes_its_limits_from_its_own_reference_price():
    """Three events of 1.05 (00878 had eleven): each session closes at the limit down from its reference."""
    raw = [105.0, 90.0, 94.5, 81.0, 84.0, 72.0, 75.0]
    factors = pl.DataFrame({
        "symbol": ["600000.SH"] * 3, "trade_date": [D[1], D[3], D[5]], "ex_factor": [1.05, 1.05, 1.05],
    })
    enriched = _limit_frame("600000.SH", raw, factors)

    signals = compute_limit_signals(enriched, pl.DataFrame({"symbol": ["600000.SH"], "name": ["PF Bank"]}))

    by_date = dict(zip(signals["date"].to_list(), signals["signal_limit_down"].to_list(), strict=True))
    assert [by_date[D[1]], by_date[D[3]], by_date[D[5]]] == [True, True, True]
    assert not any(by_date[d] for d in (D[2], D[4], D[6]))


def test_published_limits_win_on_corporate_action_bars():
    enriched = compute_enriched(_bars("600000.SH", [100.0, 99.0, 100.0]), factors=pl.DataFrame()).with_columns(
        pl.when(pl.col("date") == D[1]).then(99.0).otherwise(None).alias("ref_limit_up"),
        pl.lit(None, dtype=pl.Float64).alias("ref_limit_down"),
    )

    signals = compute_limit_signals(enriched, pl.DataFrame({"symbol": ["600000.SH"], "name": ["PF Bank"]})).sort("date")

    assert signals["signal_limit_up"].to_list() == [None, True, False]


def test_bars_without_factors_compute_as_before():
    """Regression guard: no factors leave every price raw and every limit base the previous raw close."""
    raw = _bars("600000.SH", [10.0, 11.0, 9.9, 10.89])

    out = compute_enriched(raw, factors=pl.DataFrame()).sort("date")
    signals = compute_limit_signals(out, pl.DataFrame({"symbol": ["600000.SH"], "name": ["PF Bank"]})).sort("date")

    assert out["close"].to_list() == out["raw_close"].to_list() == [10.0, 11.0, 9.9, 10.89]
    assert out["prev_close"].to_list() == [None, 10.0, 11.0, 9.9]
    assert signals["signal_limit_up"].to_list() == [None, True, False, True]
    assert signals["signal_limit_down"].to_list() == [None, False, True, False]


SESSIONS = [date(2026, 8, 31) + timedelta(days=i) for i in range(12) if (date(2026, 8, 31) + timedelta(days=i)).weekday() < 5]


def _daily(root: Path, closes: dict[str, list[float]], volume: float = 1_000_000.0) -> TaiwanDailyStore:
    rows = []
    for symbol, series in closes.items():
        for d, c in zip(SESSIONS, series, strict=True):
            rows.append({
                "symbol": symbol, "date": d, "open": c, "high": c, "low": c, "close": c,
                "volume": volume, "amount": c * volume, "quote_ts": None,
            })
    store = TaiwanDailyStore(data_dir=root / "taiwan" / "daily")
    store.write_batch(pl.DataFrame(rows, schema_overrides={"quote_ts": pl.Int64}))
    return store


def _publisher(tmp_path: Path, daily: TaiwanDailyStore, actions: TaiwanCorporateActionStore) -> TaiwanEnrichedRefreshService:
    return TaiwanEnrichedRefreshService(
        daily_store=daily, enriched_root=tmp_path / "kline_daily_enriched",
        share_capital=MagicMock(as_instruments=MagicMock(return_value=pl.DataFrame())), corporate_actions=actions,
    )


def _partition(tmp_path: Path, d: date) -> pl.DataFrame:
    return pl.read_parquet(tmp_path / "kline_daily_enriched" / f"date={d.isoformat()}" / "part.parquet")


def _mtimes(tmp_path: Path) -> dict[date, float]:
    return {
        d: (tmp_path / "kline_daily_enriched" / f"date={d.isoformat()}" / "part.parquet").stat().st_mtime_ns
        for d in SESSIONS
    }


PRICES = {"2330.TWSE": [1000.0 + i for i in range(len(SESSIONS))], "2379.TWSE": [700.0] * 7 + [680.0, 690.0, 700.0]}


@pytest.fixture
def world(tmp_path):
    daily = _daily(tmp_path, PRICES)
    actions = TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions")
    return tmp_path, daily, actions


def _age_partitions(tmp_path: Path) -> None:
    old = time.time() - 3600
    for d in SESSIONS:
        path = tmp_path / "kline_daily_enriched" / f"date={d.isoformat()}" / "part.parquet"
        os.utime(path, (old, old))
    daily_old = old - 60
    for d in SESSIONS:
        for part in (tmp_path / "taiwan" / "daily" / f"date={d.isoformat()}").glob("*.parquet"):
            os.utime(part, (daily_old, daily_old))


def test_a_new_event_republishes_every_partition_under_a_publication(world, monkeypatch):
    tmp_path, daily, actions = world
    service = _publisher(tmp_path, daily, actions)
    service.refresh()
    _age_partitions(tmp_path)
    before = _mtimes(tmp_path)
    actions.upsert(_events(_event("2379.TWSE", SESSIONS[7], EXDIV, 700.0, 672.0, 739.0, 605.0)))
    seen_publishing = []
    original = service._publish_partitions

    def spy(enriched, publication=None):
        seen_publishing.append(enriched_publication_incomplete(tmp_path))
        return original(enriched, publication)

    monkeypatch.setattr(service, "_publish_partitions", spy)

    service.refresh_recent(days=2)

    after = _mtimes(tmp_path)
    assert all(after[d] > before[d] for d in SESSIONS)
    assert seen_publishing == [True]
    assert enriched_publication_incomplete(tmp_path) is False
    record = json.loads(service.factor_set_path.read_text(encoding="utf-8"))
    assert (record["factors"], record["through"]) == (1, SESSIONS[-1].isoformat())
    early = _partition(tmp_path, SESSIONS[0]).filter(pl.col("symbol") == "2379.TWSE").row(0, named=True)
    assert early["close"] == pytest.approx(700.0 / (700.0 / 672.0))
    assert early["raw_close"] == 700.0


def test_unchanged_factors_republish_only_the_recent_partitions(world):
    tmp_path, daily, actions = world
    actions.upsert(_events(_event("2379.TWSE", SESSIONS[7], EXDIV, 700.0, 672.0, 739.0, 605.0)))
    service = _publisher(tmp_path, daily, actions)
    service.refresh()
    _age_partitions(tmp_path)
    before = _mtimes(tmp_path)

    service.refresh_recent(days=2)

    after = _mtimes(tmp_path)
    assert [d for d in SESSIONS if after[d] != before[d]] == SESSIONS[-2:]


def test_an_event_after_the_newest_bar_does_not_rescale_history(world):
    tmp_path, daily, actions = world
    service = _publisher(tmp_path, daily, actions)
    service.refresh()
    _age_partitions(tmp_path)
    before = _mtimes(tmp_path)
    actions.upsert(_events(_event("2379.TWSE", SESSIONS[-1] + timedelta(days=3), EXDIV, 700.0, 672.0)))

    service.refresh_recent(days=2)

    after = _mtimes(tmp_path)
    assert [d for d in SESSIONS if after[d] != before[d]] == SESSIONS[-2:]
    assert _partition(tmp_path, SESSIONS[0]).filter(pl.col("symbol") == "2379.TWSE")["close"].to_list() == [700.0]


def test_a_full_publish_that_fails_midway_leaves_readers_blocked_and_is_redone(world, monkeypatch):
    tmp_path, daily, actions = world
    service = _publisher(tmp_path, daily, actions)
    service.refresh()
    actions.upsert(_events(_event("2379.TWSE", SESSIONS[7], EXDIV, 700.0, 672.0)))
    from app.enriched_generation import EnrichedPublication

    written = []
    original = EnrichedPublication.write_parquet

    def failing_write(self, df, out):
        if written:
            raise OSError("disk full")
        written.append(out)
        return original(self, df, out)

    monkeypatch.setattr(EnrichedPublication, "write_parquet", failing_write)
    with pytest.raises(OSError, match="disk full"):
        service.refresh_recent(days=2)

    assert enriched_publication_incomplete(tmp_path) is True
    assert json.loads(service.factor_set_path.read_text(encoding="utf-8"))["factors"] == 0
    monkeypatch.setattr(EnrichedPublication, "write_parquet", original)
    service.refresh_recent(days=2)
    assert enriched_publication_incomplete(tmp_path) is False
    assert json.loads(service.factor_set_path.read_text(encoding="utf-8"))["factors"] == 1


def test_a_full_publish_succeeds_after_the_repository_cache_read_the_table(world):
    """Windows refuses to replace a partition any reader in the process still holds open."""
    tmp_path, daily, actions = world
    service = _publisher(tmp_path, daily, actions)
    service.refresh()
    from app.tickflow.repository import DataStore, KlineRepository

    repo = KlineRepository(DataStore(tmp_path))
    repo.refresh_cache()
    assert repo.enriched_latest_date() == SESSIONS[-1]
    actions.upsert(_events(_event("2379.TWSE", SESSIONS[7], EXDIV, 700.0, 672.0)))

    try:
        service.refresh_recent(days=2)
    finally:
        repo.db.close()

    assert enriched_publication_incomplete(tmp_path) is False
    early = _partition(tmp_path, SESSIONS[0]).filter(pl.col("symbol") == "2379.TWSE").row(0, named=True)
    assert early["close"] == pytest.approx(672.0)


def test_a_factor_record_that_cannot_be_saved_leaves_the_publication_incomplete(world, monkeypatch):
    tmp_path, daily, actions = world
    service = _publisher(tmp_path, daily, actions)
    service.refresh()
    actions.upsert(_events(_event("2379.TWSE", SESSIONS[7], EXDIV, 700.0, 672.0)))

    def failing_record(factors, through):
        raise OSError("record not writable")

    monkeypatch.setattr(service, "_record_factor_set", failing_record)
    with pytest.raises(OSError, match="record not writable"):
        service.refresh_recent(days=2)

    assert enriched_publication_incomplete(tmp_path) is True
    assert json.loads(service.factor_set_path.read_text(encoding="utf-8"))["factors"] == 0
    monkeypatch.delattr(service, "_record_factor_set")
    service.refresh_recent(days=2)
    assert enriched_publication_incomplete(tmp_path) is False
    assert json.loads(service.factor_set_path.read_text(encoding="utf-8"))["factors"] == 1


def test_a_publication_that_never_committed_is_redone_in_full(world, monkeypatch):
    tmp_path, daily, actions = world
    service = _publisher(tmp_path, daily, actions)
    service.refresh()
    actions.upsert(_events(_event("2379.TWSE", SESSIONS[7], EXDIV, 700.0, 672.0)))
    from app.enriched_generation import EnrichedPublication

    original = EnrichedPublication.commit

    def failing_commit(self):
        raise OSError("marker not writable")

    monkeypatch.setattr(EnrichedPublication, "commit", failing_commit)
    with pytest.raises(OSError, match="marker not writable"):
        service.refresh_recent(days=2)

    assert enriched_publication_incomplete(tmp_path) is True
    assert json.loads(service.factor_set_path.read_text(encoding="utf-8"))["factors"] == 1
    monkeypatch.setattr(EnrichedPublication, "commit", original)
    _age_partitions(tmp_path)
    before = _mtimes(tmp_path)
    service.refresh_recent(days=2)
    after = _mtimes(tmp_path)
    assert all(after[d] > before[d] for d in SESSIONS)
    assert enriched_publication_incomplete(tmp_path) is False


def test_the_update_button_finishes_a_publication_that_never_committed(tmp_path, monkeypatch):
    """Every partition is newer than its bars after the failed commit, so only the marker says to publish."""
    from app.config import settings
    from app.enriched_generation import EnrichedPublication
    from app.taiwan.daily_update import publish_enriched
    from app.taiwan.share_capital_store import TaiwanShareCapitalStore

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(TaiwanShareCapitalStore, "refresh", lambda self: 0)
    monkeypatch.setattr(TaiwanShareCapitalStore, "as_instruments", lambda self: pl.DataFrame())
    daily = _daily(tmp_path, PRICES)
    service = TaiwanEnrichedRefreshService(daily_store=daily)
    service.refresh()
    TaiwanCorporateActionStore().upsert(_events(_event("2379.TWSE", SESSIONS[7], EXDIV, 700.0, 672.0)))
    original = EnrichedPublication.commit

    def failing_commit(self):
        raise OSError("marker not writable")

    monkeypatch.setattr(EnrichedPublication, "commit", failing_commit)
    with pytest.raises(OSError, match="marker not writable"):
        service.refresh_recent(days=2)
    monkeypatch.setattr(EnrichedPublication, "commit", original)
    assert service.stale_since() is None
    assert enriched_publication_incomplete(tmp_path) is True
    corporate_actions = MagicMock()
    corporate_actions.refresh_publish_window.return_value = {"failed": []}

    assert publish_enriched(daily, only_if_behind=True, corporate_actions=corporate_actions) is True

    assert enriched_publication_incomplete(tmp_path) is False
    assert json.loads(service.factor_set_path.read_text(encoding="utf-8"))["factors"] == 1


def test_published_rows_keep_raw_prices_and_the_official_limits(world):
    tmp_path, daily, actions = world
    actions.upsert(_events(_event("2379.TWSE", SESSIONS[7], EXDIV, 700.0, 672.0, 739.0, 605.0)))

    _publisher(tmp_path, daily, actions).refresh()

    event_row = _partition(tmp_path, SESSIONS[7]).filter(pl.col("symbol") == "2379.TWSE").row(0, named=True)
    assert (event_row["ref_prev_close"], event_row["ref_limit_up"], event_row["ref_limit_down"]) == (672.0, 739.0, 605.0)
    assert event_row["close"] == event_row["raw_close"] == 680.0
    assert event_row["change_pct"] == pytest.approx(680.0 / 672.0 - 1)
    quiet = _partition(tmp_path, SESSIONS[6]).filter(pl.col("symbol") == "2330.TWSE").row(0, named=True)
    assert (quiet["ref_prev_close"], quiet["ref_limit_up"], quiet["ref_limit_down"]) == (None, None, None)


def test_taiwan_limit_signals_compare_raw_prices_with_raw_limits(tmp_path):
    """A 4-for-1 par change after the bars: 1105 is the limit up of 1008 on the raw ladder (tick 5),
    but 276.25 is below the 277.0 the adjusted ladder (tick 0.5) would give; 1008 is an ordinary
    0.8% day, far from a limit on either scale unless the adjusted base meets a raw price."""
    closes = [1000.0, 1008.0, 1105.0, 276.25] + [276.25] * (len(SESSIONS) - 4)
    daily = _daily(tmp_path, {"2330.TWSE": closes})
    actions = TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions")
    actions.upsert(_events(_event("2330.TWSE", SESSIONS[3], PAR_CHANGE, 1105.0, 276.25, 303.5, 248.75)))

    _publisher(tmp_path, daily, actions).refresh()

    quiet_day = _partition(tmp_path, SESSIONS[1]).row(0, named=True)
    limit_day = _partition(tmp_path, SESSIONS[2]).row(0, named=True)
    assert limit_day["close"] == pytest.approx(276.25)
    assert (quiet_day["signal_limit_up"], limit_day["signal_limit_up"]) == (False, True)


def _side_stores(tmp_path: Path, daily: TaiwanDailyStore, actions: TaiwanCorporateActionStore) -> dict:
    return {
        "daily_store": daily,
        "inst_store": TaiwanInstitutionalStore(data_dir=tmp_path / "institutional"),
        "margin_store": TaiwanMarginStore(data_dir=tmp_path / "margin"),
        "security_master": get_security_master(),
        "corporate_actions": actions,
    }


def test_a_par_value_change_does_not_look_like_a_volume_spike(tmp_path):
    """2327 traded the same value before and after its 4-for-1 par change: four times the shares."""
    closes = [546.0] * 6 + [136.5] * (len(SESSIONS) - 6)
    rows = []
    for d, c in zip(SESSIONS, closes, strict=True):
        volume = 1_000_000.0 if c > 200 else 4_000_000.0
        rows.append({"symbol": "2327.TWSE", "date": d, "open": c, "high": c, "low": c, "close": c,
                     "volume": volume, "amount": c * volume, "quote_ts": None})
    daily = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    daily.write_batch(pl.DataFrame(rows, schema_overrides={"quote_ts": pl.Int64}))
    actions = TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions")
    actions.upsert(_events(_event("2327.TWSE", SESSIONS[6], PAR_CHANGE, 546.0, 136.5)))

    snapshot = TaiwanAbnormalDiagnosticsService(**_side_stores(tmp_path, daily, actions)).get_diagnostics(
        SESSIONS[7], include_all=True
    )

    item = next(i for i in snapshot.items if i.symbol == "2327.TWSE")
    assert item.volume_ratio_5d == 1.0
    assert all(s.type != "VOLUME_SPIKE" for s in item.signals)


def test_forward_adjusted_daily_bars_match_the_published_scale():
    events = _events(_event("0050.TWSE", D[2], ETF_SPLIT, 188.65, 47.16))
    daily = _bars("0050.TWSE", [190.0, 188.65, 47.5, 48.0])

    adjusted = forward_adjusted(daily, events, D[3]).sort("date")

    assert adjusted.columns == daily.columns
    assert adjusted["close"].to_list() == [47.5, 188.65 / 4, 47.5, 48.0]
    assert adjusted["volume"].to_list() == [4000.0, 4000.0, 1000.0, 1000.0]
    assert forward_adjusted(daily, events, D[1]).equals(daily)


def test_factors_between_two_sessions_cover_only_the_events_in_between():
    events = _events(
        _event("2379.TWSE", D[2], EXDIV, 729.0, 704.0),
        _event("2379.TWSE", D[5], EXDIV, 704.0, 700.0),
        _event("0050.TWSE", D[4], ETF_SPLIT, 188.65, 47.16),
    )

    got = factors_between(events, D[1], D[4])

    assert got.rows() == [("0050.TWSE", 4.0, 4.0), ("2379.TWSE", 729.0 / 704.0, 1.0)]
    assert factors_between(events, D[2], D[3]).is_empty()


def test_todays_factors_never_include_a_later_event(tmp_path):
    store = TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions")
    store.upsert(_events(
        _event("2379.TWSE", D[3], EXDIV, 729.0, 704.0),
        _event("9927.TWSE", D[4], EXDIV, 70.10, 65.10),
    ))

    assert today_factors(D[2], D[3], store).rows() == [("2379.TWSE", 729.0 / 704.0, 1.0)]
    assert today_factors(D[3], D[3], store).is_empty()


def _record(data_dir: Path, through: date) -> None:
    """The factor record a full publish through *through* leaves next to the enriched table."""
    data_dir.mkdir(parents=True, exist_ok=True)
    record = data_dir / ".taiwan_enriched_factors.json"
    record.write_text(json.dumps({"through": through.isoformat()}), encoding="utf-8")


FACTOR = 729.0 / 704.0
HISTORY = {"2379.TWSE": [731.0, 730.0, 729.0], "2330.TWSE": [1000.0, 1001.0, 1002.0]}
EVENT_DAY_CLOSE = {"2379.TWSE": 700.0, "2330.TWSE": 1003.0}
EVENT_DAY_LIVE = {"2379.TWSE": 692.0, "2330.TWSE": 1004.0}
ON_TODAYS_SCALE = {"2379.TWSE": [c / FACTOR for c in HISTORY["2379.TWSE"]], "2330.TWSE": HISTORY["2330.TWSE"]}
MOMENTS = ("before_open", "intraday", "closed_publish_pending", "published", "publish_failed_next_day")


def _raw_bars(days: list[date], closes: dict[str, list[float]]) -> pl.DataFrame:
    rows = [
        {"symbol": symbol, "date": d, "open": c, "high": c, "low": c, "close": c,
         "volume": 1000.0, "amount": c * 1000.0, "quote_ts": None}
        for symbol, series in closes.items()
        for d, c in zip(days, series, strict=True)
    ]
    return pl.DataFrame(rows, schema_overrides={"quote_ts": pl.Int64})


def _day_rows(d: date, closes: dict[str, float]) -> pl.DataFrame:
    """The latest-day cache a live quote flush leaves in the repository."""
    return pl.DataFrame({
        "symbol": list(closes), "date": [d] * len(closes),
        **{c: list(closes.values()) for c in ("open", "high", "low", "close", "raw_close")},
        "volume": [1000.0] * len(closes),
    })


def _quotes(closes: dict[str, float]) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": list(closes), **{c: list(closes.values()) for c in ("open", "high", "low", "close")},
        "volume": [3000.0] * len(closes), "amount": [1.0] * len(closes), "change_pct": [0.0] * len(closes),
    })


def _published_repo(data_dir: Path, history_start: date, latest: tuple[pl.DataFrame, date | None]):
    """A repository whose history cache holds every published partition."""
    from app.tickflow.repository import DataStore, KlineRepository

    repo = KlineRepository(DataStore(data_dir))
    partitions = sorted((data_dir / "kline_daily_enriched").glob("date=*/part.parquet"))
    repo._enriched_history_cache = pl.concat([pl.read_parquet(p) for p in partitions], how="diagonal_relaxed")
    repo._enriched_history_start = history_start
    repo.get_enriched_latest = lambda: latest
    return repo


@pytest.fixture(params=MOMENTS)
def moment(request, tmp_path, monkeypatch):
    """2379 goes ex-dividend (729 -> 704) on the event day; history was published the evening before.

    Moments: before the open; during the session; after the close with the day's bars stored
    and the publish still running; after the publish; the next morning after that publish
    failed midway.
    """
    from app.api import kline
    from app.config import settings
    from app.enriched_generation import EnrichedPublication

    name = request.param
    today = date.today()
    event_day = today - timedelta(days=1) if name == "publish_failed_next_day" else today
    sessions = [event_day - timedelta(days=n) for n in (3, 2, 1)]
    data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(kline, "cn_today", lambda: today)
    daily, actions = TaiwanDailyStore(), TaiwanCorporateActionStore()
    publisher = _publisher(data_dir, daily, actions)
    daily.write_batch(_raw_bars(sessions, HISTORY))
    publisher.refresh()
    actions.upsert(_events(_event("2379.TWSE", event_day, EXDIV, 729.0, 704.0)))
    if name != "before_open" and name != "intraday":
        daily.write_batch(_raw_bars([event_day], {s: [c] for s, c in EVENT_DAY_CLOSE.items()}))
    if name == "published":
        publisher.refresh()
    if name in ("closed_publish_pending", "publish_failed_next_day"):
        EnrichedPublication(data_dir, "stock", recover=True).begin()

    last_close = {s: series[-1] for s, series in HISTORY.items()}
    event_day_close = {"before_open": None, "intraday": EVENT_DAY_LIVE}.get(name, EVENT_DAY_CLOSE)
    if event_day_close is None:
        latest, quotes = (_day_rows(sessions[-1], last_close), sessions[-1]), (_quotes(last_close), sessions[-1])
    else:
        latest, quotes = (_day_rows(event_day, event_day_close), event_day), (_quotes(event_day_close), event_day)
    quote_service = MagicMock()
    quote_service.get_enriched_today.return_value = quotes
    return SimpleNamespace(
        name=name, today=today, event_day=event_day, sessions=sessions, event_day_close=event_day_close,
        repo=_published_repo(data_dir, sessions[0], latest), quote_service=quote_service,
    )


def _closes(rows: list[dict]) -> dict[str, float]:
    return {str(r["date"])[:10]: r["close"] for r in rows}


def _on_todays_scale(m, symbol: str, event_day_close: dict[str, float] | None) -> dict[str, float]:
    closes = dict(zip(map(str, m.sessions), ON_TODAYS_SCALE[symbol], strict=True))
    if event_day_close is not None:
        closes[str(m.event_day)] = event_day_close[symbol]
    return closes


def _daily_k(m, repo, symbol: str) -> dict:
    from app.api import kline

    request = MagicMock()
    request.app.state.repo = repo
    request.app.state.quote_service = m.quote_service
    return kline.get_daily(request, symbol, days=30, start_date=str(m.sessions[0]), end_date=str(m.today),
                           ext_columns=None)


def test_long_range_daily_k_is_on_todays_scale_at_every_moment(moment):
    repo = MagicMock()
    repo.resolve_asset_type.return_value = "stock"
    repo.get_daily_asset.return_value = pl.DataFrame()

    for symbol in HISTORY:
        resp = _daily_k(moment, repo, symbol)

        assert resp["source"] == "taiwan_local"
        assert _closes(resp["rows"]) == pytest.approx(_on_todays_scale(moment, symbol, moment.event_day_close))
        assert [r["raw_close"] for r in resp["rows"][:3]] == HISTORY[symbol]
        assert [r["volume"] for r in resp["rows"][:3]] == [1000.0] * 3


def test_cached_daily_k_is_on_todays_scale_at_every_moment(moment):
    repo = MagicMock()
    repo.resolve_asset_type.return_value = "stock"
    repo.get_daily_asset.side_effect = lambda _asset, symbol, start, end: moment.repo.get_daily(symbol, start, end)

    for symbol in HISTORY:
        resp = _daily_k(moment, repo, symbol)

        assert resp["source"] == "enriched"
        assert _closes(resp["rows"]) == pytest.approx(_on_todays_scale(moment, symbol, moment.event_day_close))
        assert [r["raw_close"] for r in resp["rows"][:3]] == HISTORY[symbol]
        assert [r["volume"] for r in resp["rows"][:3]] == [1000.0] * 3


def test_mini_candle_history_is_on_todays_scale_at_every_moment(moment):
    from app.api import kline

    repo = MagicMock()
    repo.resolve_asset_type.return_value = "stock"
    repo.get_daily_batch.side_effect = moment.repo.get_daily_batch
    request = MagicMock()
    request.app.state.repo = repo

    out = kline.get_daily_batch(request, {"symbols": list(HISTORY), "days": 12})["data"]

    stored = EVENT_DAY_CLOSE if moment.name == "published" else None
    for symbol in HISTORY:
        assert _closes(out[symbol]) == pytest.approx(_on_todays_scale(moment, symbol, stored))


def test_an_event_learned_after_its_session_was_published_moves_only_the_earlier_rows(tmp_path, monkeypatch):
    """The event table missed 2379's ex-dividend on its day, so that evening's publish kept the old factors."""
    from app.api import kline
    from app.config import settings

    today = date.today()
    event_day = today - timedelta(days=1)
    sessions = [event_day - timedelta(days=n) for n in (3, 2, 1)]
    data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(kline, "cn_today", lambda: today)
    daily, actions = TaiwanDailyStore(), TaiwanCorporateActionStore()
    publisher = _publisher(data_dir, daily, actions)
    daily.write_batch(_raw_bars(sessions, HISTORY))
    publisher.refresh()
    daily.write_batch(_raw_bars([event_day], {s: [c] for s, c in EVENT_DAY_CLOSE.items()}))
    publisher.refresh_recent(days=2)
    actions.upsert(_events(_event("2379.TWSE", event_day, EXDIV, 729.0, 704.0)))
    m = SimpleNamespace(today=today, event_day=event_day, sessions=sessions, quote_service=None,
                        repo=_published_repo(data_dir, sessions[0], (pl.DataFrame(), None)))
    repo = MagicMock()
    repo.resolve_asset_type.return_value = "stock"
    repo.get_daily_asset.side_effect = lambda _asset, symbol, start, end: m.repo.get_daily(symbol, start, end)
    repo.get_daily_batch.side_effect = m.repo.get_daily_batch
    request = MagicMock()
    request.app.state.repo = repo

    batch = kline.get_daily_batch(request, {"symbols": list(HISTORY), "days": 12})["data"]

    for symbol in HISTORY:
        expected = _on_todays_scale(m, symbol, EVENT_DAY_CLOSE)
        assert _closes(_daily_k(m, repo, symbol)["rows"]) == pytest.approx(expected)
        assert _closes(batch[symbol]) == pytest.approx(expected)


def test_a_share_count_event_today_multiplies_earlier_volumes(tmp_path, monkeypatch):
    """2327 changes par value 4-for-1 today: earlier rows show four times the shares at a quarter of the price."""
    from app.api import kline
    from app.config import settings

    today = date.today()
    days = [today - timedelta(days=2), today - timedelta(days=1)]
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(kline, "cn_today", lambda: today)
    TaiwanCorporateActionStore().upsert(_events(_event("2327.TWSE", today, PAR_CHANGE, 546.0, 136.5)))
    _record(tmp_path / "data", days[-1])
    published = pl.DataFrame({
        "symbol": ["2327.TWSE"] * 2, "date": days,
        **{c: [548.0, 546.0] for c in ("open", "high", "low", "close", "raw_close")},
        "volume": [1000.0, 1000.0], "vol_ma5": [1000.0, 1000.0], "ref_prev_close": [None, None],
    }, schema_overrides={"ref_prev_close": pl.Float64})
    repo = MagicMock()
    repo.resolve_asset_type.return_value = "stock"
    repo.get_daily_asset.return_value = published
    repo.get_daily_batch.return_value = published
    request = MagicMock()
    request.app.state.repo = repo
    request.app.state.quote_service = None

    rows = kline.get_daily(request, "2327.TWSE", days=30, start_date=str(days[0]), end_date=str(today),
                           ext_columns=None)["rows"]
    batch = kline.get_daily_batch(request, {"symbols": ["2327.TWSE"], "days": 12})["data"]["2327.TWSE"]

    assert [r["close"] for r in rows] == [137.0, 136.5]
    assert [r["volume"] for r in rows] == [r["vol_ma5"] for r in rows] == [4000.0, 4000.0]
    assert [r["raw_close"] for r in rows] == [548.0, 546.0]
    assert [r["close"] for r in batch] == [137.0, 136.5]
    assert [r["volume"] for r in batch] == [4000.0, 4000.0]


def _cached_repo(tmp_path: Path):
    from app.tickflow.repository import DataStore, KlineRepository

    repo = KlineRepository(DataStore(tmp_path))
    repo._enriched_history_cache = pl.DataFrame({
        "symbol": ["2379.TWSE", "2379.TWSE"], "date": [D[1], D[2]], "close": [704.0, 692.0], "open": [1.0, 1.0],
    })
    repo._enriched_history_start = D[1]
    repo.get_enriched_latest = lambda: (pl.DataFrame(), None)
    return repo


def test_daily_reads_use_only_the_caches_while_a_rewrite_is_half_done(tmp_path, monkeypatch):
    from app.enriched_generation import EnrichedPublication

    repo = _cached_repo(tmp_path)
    scans: list[str] = []
    monkeypatch.setattr(repo, "_scan_daily_symbol", lambda *a, **k: scans.append("symbol") or pl.DataFrame())
    monkeypatch.setattr(repo, "_scan_daily_batch", lambda *a, **k: scans.append("batch") or pl.DataFrame())
    publication = EnrichedPublication(tmp_path, "stock", recover=True)
    publication.begin()

    single = repo.get_daily("2379.TWSE", D[0], D[3])
    narrow = repo.get_daily("2379.TWSE", D[0], D[3], columns=["date", "close"])
    batch = repo.get_daily_batch(["2379.TWSE"], D[0], D[3], columns=["symbol", "date", "close"])

    assert scans == []
    assert single["close"].to_list() == narrow["close"].to_list() == batch["close"].to_list() == [704.0, 692.0]
    publication.abandon()
    repo.get_daily("2379.TWSE", D[0], D[3])
    assert scans == ["symbol"]


def test_the_screener_slow_paths_refuse_to_read_mid_rewrite(tmp_path, monkeypatch):
    from app.enriched_generation import EnrichedGenerationUnavailableError, EnrichedPublication
    from app.services import screener

    repo = MagicMock()
    repo.store.data_dir = tmp_path
    repo.get_enriched_latest_asset.return_value = (pl.DataFrame(), None)
    repo.get_enriched_history.return_value = None
    partition = tmp_path / "kline_daily_enriched" / f"date={D[2].isoformat()}"
    partition.mkdir(parents=True)
    _bars("2379.TWSE", [729.0, 692.0, 700.0]).write_parquet(partition / "part.parquet")
    monkeypatch.setattr(screener, "scan_enriched_parquet", lambda *a, **k: pytest.fail("scanned mid-rewrite"))
    service = screener.ScreenerService(repo)
    monkeypatch.setattr(service, "_compute_enriched_full", lambda *a, **k: pytest.fail("read mid-rewrite"))
    EnrichedPublication(tmp_path, "stock", recover=True).begin()

    with pytest.raises(EnrichedGenerationUnavailableError):
        service._load_enriched_for_date(D[2])
    with pytest.raises(EnrichedGenerationUnavailableError):
        service._load_enriched_history(D[2], 20)


def test_run_all_answers_503_mid_rewrite_and_keeps_the_last_strategy_cache(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.enriched_generation import EnrichedPublication
    from app.main import app
    from app.services import strategy_cache
    from app.services.screener import ScreenerResult

    strategy_cache.write_cache(tmp_path, "2026-09-29", {"s1": {"total": 1, "as_of": "2026-09-29", "rows": [
        {"symbol": "2330.TWSE"}]}})
    last = strategy_cache.read_cache(tmp_path)
    repo = MagicMock()
    repo.store.data_dir = tmp_path
    repo.get_enriched_latest_asset.return_value = (pl.DataFrame(), None)
    repo.get_enriched_history.return_value = None
    engine = MagicMock()
    engine.list_strategies.return_value = [{"id": "s1"}]
    engine.required_history_bars.return_value = 20
    engine.run_all.return_value = {"s1": ScreenerResult(as_of=date(2026, 9, 30), strategy="s1")}
    monkeypatch.setattr(app.state, "repo", repo, raising=False)
    monkeypatch.setattr(app.state, "strategy_engine", engine, raising=False)
    EnrichedPublication(tmp_path, "stock", recover=True).begin()

    response = TestClient(app, client=("127.0.0.1", 50000)).post("/api/screener/run_all", json={"as_of": "2026-09-30"})

    assert response.status_code == 503
    assert strategy_cache.read_cache(tmp_path) == last
    engine.run_all.assert_not_called()
