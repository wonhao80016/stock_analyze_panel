"""Corporate-action sessions measure change and limits from the exchange reference price.

2379.TWSE uses its official 2026-09-08 figures: close 729 before the ex-dividend
session, close 692 on it, TWT49U reference 704 with limits 774 / 634. 6196.TWSE
carries its 2026-06-24 figures (541 -> 540 against a reference of 534.55) moved onto
the same session. 2454.TWSE is synthetic: its published limit up (1500) sits below the
statutory 10% so a test can tell the published limit from the computed one. 2330.TWSE
has no event and must come out exactly as before.

Events are stored where every consumer reads them by default, so each "without events"
baseline is taken before _publish_events() runs.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from app.api import kline
from app.indicators.pipeline import compute_enriched
from app.taiwan.abnormal_diagnostics import TaiwanAbnormalDiagnosticsService
from app.taiwan.corporate_actions import TaiwanCorporateActionStore, attach_reference_prices
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.industry_intelligence import TaiwanIndustryIntelligenceService
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.market_institutional_store import TaiwanMarketInstitutionalStore
from app.taiwan.market_intelligence import TaiwanMarketIntelligenceService
from app.taiwan.research_context import TaiwanStockResearchContextService
from app.taiwan.universe import get_security_master
from app.taiwan.watchlist_enrichment import _taiwan_daily_fallback_map

SESSIONS = [date(2026, 9, d) for d in (1, 2, 3, 4, 7, 8, 9)]
EVENT = date(2026, 9, 8)
BEFORE = date(2026, 9, 7)

CLOSES = {
    "2330.TWSE": [1000.0, 1005.0, 1010.0, 1008.0, 1012.0, 1020.0, 1015.0],
    "2379.TWSE": [740.0, 735.0, 731.0, 730.0, 729.0, 692.0, 700.0],
    "2454.TWSE": [1500.0, 1510.0, 1515.0, 1518.0, 1520.0, 1500.0, 1490.0],
    "6196.TWSE": [545.0, 544.0, 543.0, 542.0, 541.0, 540.0, 542.0],
}
EVENT_BARS = {
    "2379.TWSE": (717.0, 721.0, 680.0),
    "2454.TWSE": (1450.0, 1500.0, 1440.0),
    "6196.TWSE": (537.0, 543.0, 526.0),
}
EVENTS = [
    ("2379.TWSE", 729.0, 704.0, 774.0, 634.0),
    ("6196.TWSE", 541.0, 534.55, 588.0, 481.5),
    ("2454.TWSE", 1520.0, 1400.0, 1500.0, 1260.0),
]
EVENT_SYMBOLS = [e[0] for e in EVENTS]
CHANGED_ON_EVENTS = {
    "prev_close", "change_pct", "change_amount", "amplitude",
    "signal_limit_up", "signal_limit_down", "signal_broken_limit_up", "signal_limit_down_recovery",
    "consecutive_limit_ups", "consecutive_limit_downs", "ref_prev_close",
}


def _daily_frame() -> pl.DataFrame:
    rows = []
    for symbol, closes in CLOSES.items():
        for d, close in zip(SESSIONS, closes, strict=True):
            o, h, lo = EVENT_BARS[symbol] if d == EVENT and symbol in EVENT_BARS else (close, close + 5.0, close - 5.0)
            rows.append({
                "symbol": symbol, "date": d, "open": o, "high": h, "low": lo, "close": close,
                "volume": 1_000_000.0, "amount": close * 1_000_000.0, "quote_ts": None,
            })
    return pl.DataFrame(rows, schema_overrides={"quote_ts": pl.Int64}).sort(["symbol", "date"])


def _event_frame() -> pl.DataFrame:
    schema = {
        "symbol": pl.String, "date": pl.Date, "kind": pl.String, "pre_close": pl.Float64,
        "ref_price": pl.Float64, "limit_up": pl.Float64, "limit_down": pl.Float64, "open_ref": pl.Float64,
        "ex_factor": pl.Float64, "official_label": pl.String, "provider": pl.String, "source": pl.String,
        "source_url": pl.String, "retrieved_at": pl.Datetime("us", "Asia/Taipei"), "trade_date": pl.Date,
        "status": pl.String,
    }
    return pl.DataFrame(
        [
            {
                "symbol": s, "date": EVENT, "kind": "exdiv", "pre_close": pre, "ref_price": ref,
                "limit_up": up, "limit_down": down, "open_ref": ref, "ex_factor": pre / ref,
                "official_label": None, "provider": "twse", "source": "twse:twt49u", "source_url": "u",
                "retrieved_at": None, "trade_date": EVENT, "status": "official",
            }
            for s, pre, ref, up, down in EVENTS
        ],
        schema=schema,
    )


def _instruments() -> pl.DataFrame:
    return pl.DataFrame({"symbol": list(CLOSES), "float_shares": [1e9] * len(CLOSES)})


@pytest.fixture(autouse=True)
def _cached_security_master(monkeypatch) -> None:
    """Serve the shared master from the configured cache; a master missing its cache fetches the directory."""
    from app.taiwan import universe
    from app.taiwan.data_root import taiwan_data_root

    master = universe.TaiwanSecurityMaster(cache_path=taiwan_data_root() / "security_master.parquet")
    assert master.load_cache(), "tests need the security master cache under DATA_DIR/taiwan"
    monkeypatch.setattr(universe, "_default_master", master)


@pytest.fixture
def daily(tmp_path, monkeypatch) -> TaiwanDailyStore:
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    store = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
    store.write_batch(_daily_frame())
    return store


def _publish_events() -> None:
    TaiwanCorporateActionStore().upsert(_event_frame())


def _row(df: pl.DataFrame, symbol: str, d: date) -> dict:
    return df.filter((pl.col("symbol") == symbol) & (pl.col("date") == d)).row(0, named=True)


def test_an_event_session_changes_from_the_reference_price():
    raw = attach_reference_prices(_daily_frame(), _event_frame())

    row = _row(compute_enriched(raw, factors=pl.DataFrame()), "2379.TWSE", EVENT)

    assert row["ref_prev_close"] == 704.0
    assert row["prev_close"] == 704.0
    assert row["change_pct"] == 692.0 / 704.0 - 1
    assert round(row["change_pct"] * 100, 2) == -1.70
    assert row["change_amount"] == -12.0
    assert row["amplitude"] == (721.0 - 680.0) / 704.0


def test_sessions_without_a_reference_price_compute_exactly_as_before():
    """Regression guard: rows without a reference price keep their previous values."""
    raw = _daily_frame()
    before = compute_enriched(raw, factors=pl.DataFrame(), instruments=_instruments())
    after = compute_enriched(attach_reference_prices(raw, _event_frame()), factors=pl.DataFrame(), instruments=_instruments())
    unchanged = [c for c in before.columns if c not in CHANGED_ON_EVENTS]
    is_event = (pl.col("date") == EVENT) & pl.col("symbol").is_in(EVENT_SYMBOLS)

    assert_frame_equal(after.filter(~is_event).select(before.columns), before.filter(~is_event))
    assert_frame_equal(after.filter(is_event).select(unchanged), before.filter(is_event).select(unchanged))

    all_null = compute_enriched(
        attach_reference_prices(raw, _event_frame().clear()), factors=pl.DataFrame(), instruments=_instruments()
    )
    assert_frame_equal(all_null.drop("ref_prev_close"), before)


def test_the_reference_base_stays_right_on_a_forward_adjusted_close():
    raw = attach_reference_prices(_daily_frame(), _event_frame())
    later_split = pl.DataFrame({"symbol": ["2379.TWSE"], "trade_date": [date(2026, 9, 9)], "ex_factor": [2.0]})

    row = _row(compute_enriched(raw, factors=later_split), "2379.TWSE", EVENT)

    assert row["close"] == pytest.approx(692.0 / 2.0)
    assert row["change_pct"] == pytest.approx(692.0 / 704.0 - 1, abs=1e-12)
    assert row["prev_close"] == pytest.approx(704.0 / 2.0)


def test_the_pipeline_limit_base_of_an_event_session_is_the_reference_price():
    raw = _daily_frame().with_columns(
        pl.when((pl.col("symbol") == "2379.TWSE") & (pl.col("date") == EVENT))
        .then(650.0).otherwise(pl.col(c)).alias(c)
        for c in ("close", "low")
    )
    before = _row(compute_enriched(raw, factors=pl.DataFrame(), instruments=_instruments()), "2379.TWSE", EVENT)
    after = _row(
        compute_enriched(attach_reference_prices(raw, _event_frame()), factors=pl.DataFrame(), instruments=_instruments()),
        "2379.TWSE",
        EVENT,
    )

    assert before["signal_limit_down"] is True
    assert after["signal_limit_down"] is False
    assert after["consecutive_limit_downs"] == 0


def _publisher(daily: TaiwanDailyStore, root) -> TaiwanEnrichedRefreshService:
    share_capital = MagicMock()
    share_capital.as_instruments.return_value = _instruments()
    return TaiwanEnrichedRefreshService(daily_store=daily, enriched_root=root, share_capital=share_capital)


def _partition(root, d: date) -> pl.DataFrame:
    return pl.read_parquet(root / f"date={d.isoformat()}" / "part.parquet")


def test_publish_stores_the_reference_price_and_uses_the_published_limits(daily, tmp_path):
    root = tmp_path / "published"
    _publish_events()

    _publisher(daily, root).refresh()

    part = _partition(root, EVENT)
    assert part.schema.get("ref_prev_close") == pl.Float64
    realtek = _row(part, "2379.TWSE", EVENT)
    assert (realtek["ref_prev_close"], realtek["prev_close"]) == (704.0, 704.0)
    assert realtek["change_pct"] == 692.0 / 704.0 - 1
    assert realtek["signal_limit_down"] is False
    assert _row(part, "2454.TWSE", EVENT)["signal_limit_up"] is True
    assert _row(part, "2330.TWSE", EVENT)["ref_prev_close"] is None
    assert not any(c.startswith("_ref") for c in part.columns)


def test_publish_leaves_symbols_without_events_as_they_were(daily, tmp_path):
    """Regression guard: a symbol with no event keeps every value; event symbols keep their raw prices."""
    without, with_events = tmp_path / "without", tmp_path / "with_events"
    _publisher(daily, without).refresh()
    _publish_events()

    _publisher(daily, with_events).refresh()

    raw = ["symbol", "date", "raw_close", "raw_high", "raw_low", "amount"]
    for d in SESSIONS:
        after = _partition(with_events, d).sort("symbol")
        before = _partition(without, d).sort("symbol")
        quiet = ~pl.col("symbol").is_in(EVENT_SYMBOLS)
        assert_frame_equal(after.filter(quiet).select(before.columns), before.filter(quiet))
        assert_frame_equal(after.select(raw), before.select(raw))


def test_a_full_republish_adds_the_column_to_partitions_published_before_it(daily, tmp_path):
    root = tmp_path / "published"
    publisher = _publisher(daily, root)
    publisher.refresh()
    for d in SESSIONS:
        old = _partition(root, d)
        TaiwanEnrichedRefreshService._write_partition(
            root / f"date={d.isoformat()}", old.drop("ref_prev_close") if "ref_prev_close" in old.columns else old
        )
    _publish_events()

    written = publisher.refresh()

    assert written == len(SESSIONS) * len(CLOSES)
    assert all("ref_prev_close" in _partition(root, d).columns for d in SESSIONS)
    assert _row(_partition(root, EVENT), "2379.TWSE", EVENT)["ref_prev_close"] == 704.0


def test_the_repository_recomputes_an_event_session_from_the_stored_reference(daily, tmp_path):
    from app.tickflow.repository import DataStore, KlineRepository

    _publish_events()
    _publisher(daily, tmp_path / "kline_daily_enriched").refresh()
    repo = KlineRepository(DataStore(tmp_path))
    repo.get_enriched_latest = lambda: (pl.DataFrame(), None)

    rows = repo.get_daily("2379.TWSE", SESSIONS[0], SESSIONS[-1])

    event_row = _row(rows, "2379.TWSE", EVENT)
    assert event_row["prev_close"] == 704.0
    assert event_row["change_pct"] == 692.0 / 704.0 - 1
    assert _row(rows, "2379.TWSE", SESSIONS[-1])["change_pct"] == 700.0 / 692.0 - 1


def test_the_repository_history_cache_reads_the_stored_reference(daily, tmp_path):
    from app.tickflow.repository import DataStore, KlineRepository

    _publish_events()
    _publisher(daily, tmp_path / "kline_daily_enriched").refresh()
    repo = KlineRepository(DataStore(tmp_path))
    repo.rebuild_views()

    repo._refresh_enriched()

    history = repo._enriched_history_cache
    assert history is not None
    assert _row(history, "2379.TWSE", EVENT)["change_pct"] == 692.0 / 704.0 - 1
    assert _row(history, "2330.TWSE", EVENT)["change_pct"] == 1020.0 / 1012.0 - 1


def _side_stores(daily: TaiwanDailyStore) -> dict:
    tmp = daily._data_dir.parent
    return {
        "daily_store": daily,
        "inst_store": TaiwanInstitutionalStore(data_dir=tmp / "institutional"),
        "margin_store": TaiwanMarginStore(data_dir=tmp / "margin"),
        "security_master": get_security_master(),
    }


def _market(daily: TaiwanDailyStore) -> TaiwanMarketIntelligenceService:
    tmp = daily._data_dir.parent
    return TaiwanMarketIntelligenceService(
        **_side_stores(daily), market_inst_store=TaiwanMarketInstitutionalStore(data_dir=tmp / "market_institutional")
    )


def test_market_breadth_compares_an_event_session_with_the_reference_price(daily):
    before = _market(daily).get_snapshot(EVENT).market_totals
    _publish_events()

    after = _market(daily).get_snapshot(EVENT).market_totals

    assert (before.advance_count, before.decline_count, before.upper_limit_count) == (1, 3, 0)
    assert (after.advance_count, after.decline_count, after.upper_limit_count) == (3, 1, 1)


def test_market_breadth_of_a_day_without_events_is_unchanged(daily):
    """Regression guard: rows without a reference price keep their previous values."""
    before = _market(daily).get_snapshot(BEFORE)
    _publish_events()

    after = _market(daily).get_snapshot(BEFORE)

    assert after.market_totals == before.market_totals
    assert after.change_distribution == before.change_distribution


def _mover_changes(snapshot) -> dict[str, float]:
    return {m.symbol: m.change_pct for ind in snapshot.industries for m in ind.top_turnover}


def test_an_industry_one_day_return_starts_from_the_reference_price(daily):
    before = _mover_changes(TaiwanIndustryIntelligenceService(**_side_stores(daily)).get_snapshot(EVENT))
    _publish_events()

    after = _mover_changes(TaiwanIndustryIntelligenceService(**_side_stores(daily)).get_snapshot(EVENT))

    assert after["2379.TWSE"] == round(692.0 / 704.0 - 1, 6)
    assert after["6196.TWSE"] == round(540.0 / 534.55 - 1, 6)
    assert before["6196.TWSE"] == round(540.0 / 541.0 - 1, 6)
    assert after["2330.TWSE"] == before["2330.TWSE"]


def _price_moves(snapshot) -> dict[str, float]:
    return {
        item.symbol: next(s.observed for s in item.signals if s.type == "PRICE_MOVE")
        for item in snapshot.items
        if any(s.type == "PRICE_MOVE" for s in item.signals)
    }


def _diagnostics(daily: TaiwanDailyStore, d: date):
    return TaiwanAbnormalDiagnosticsService(**_side_stores(daily)).get_diagnostics(d, include_all=True)


def test_an_ex_dividend_gap_is_not_a_price_move(daily):
    before = _diagnostics(daily, EVENT)
    _publish_events()

    after = _diagnostics(daily, EVENT)

    assert _price_moves(before)["2379.TWSE"] == round((692.0 / 729.0 - 1) * 100, 2)
    assert "2379.TWSE" not in _price_moves(after)
    realtek = next(i for i in after.items if i.symbol == "2379.TWSE")
    assert (realtek.previous_close, realtek.change) == (704.0, -12.0)


def test_diagnostics_of_symbols_without_events_are_unchanged(daily):
    """Regression guard: rows without a reference price keep their previous values."""
    before = {d: {i.symbol: i for i in _diagnostics(daily, d).items} for d in (BEFORE, EVENT)}
    _publish_events()

    for d in (BEFORE, EVENT):
        after = {i.symbol: i for i in _diagnostics(daily, d).items}
        assert after["2330.TWSE"].signals == before[d]["2330.TWSE"].signals
        assert after["2330.TWSE"].change_pct == before[d]["2330.TWSE"].change_pct


def test_research_context_uses_the_reference_price_and_published_limits(daily):
    _publish_events()

    ctx = TaiwanStockResearchContextService(**_side_stores(daily)).get_research_context("2379", EVENT)

    assert ctx.price_context.previous_close == 704.0
    assert ctx.price_context.change_pct == round(692.0 / 704.0 - 1, 6)
    assert (ctx.market_rules_context.limit_up, ctx.market_rules_context.limit_down) == (774.0, 634.0)


def test_research_context_takes_a_published_limit_the_reference_price_would_not_give(daily):
    """Published limits can differ from the computed ones: TWT49U set 2442.TWSE's 2026-01-06 limit up at 27.25, where 10% of its 24.12 reference gives 26.5."""
    from app.taiwan.universe.models import MarketProfileBridge

    _publish_events()
    computed = MarketProfileBridge.calc_limits(1400.0, get_security_master().get_instrument("2454.TWSE"))

    ctx = TaiwanStockResearchContextService(**_side_stores(daily)).get_research_context("2454", EVENT)

    assert computed == (1540.0, 1260.0)
    assert ctx.price_context.previous_close == 1400.0
    assert (ctx.market_rules_context.limit_up, ctx.market_rules_context.limit_down) == (1500.0, 1260.0)


def test_research_context_measures_multi_day_moves_on_the_forward_adjusted_scale(daily):
    _publish_events()
    factor = 729.0 / 704.0

    price = TaiwanStockResearchContextService(**_side_stores(daily)).get_research_context("2379", EVENT).price_context

    assert price.return_5d == round(692.0 / (740.0 / factor) - 1.0, 6)
    assert price.high_20d == pytest.approx(740.0 / factor)
    assert price.low_20d == 692.0
    assert price.close == 692.0


def test_industry_five_day_returns_cross_the_ex_dividend_gap_on_the_adjusted_scale(daily):
    _publish_events()
    expected = [
        1020.0 / 1000.0 - 1,
        692.0 / (740.0 / (729.0 / 704.0)) - 1,
        1500.0 / (1500.0 / (1520.0 / 1400.0)) - 1,
        540.0 / (545.0 / (541.0 / 534.55)) - 1,
    ]

    snapshot = TaiwanIndustryIntelligenceService(**_side_stores(daily)).get_snapshot(EVENT)

    assert snapshot.market_reference.market_equal_weight_return_5d == round(sum(expected) / 4, 6)


def test_market_trend_compares_closes_with_averages_on_the_adjusted_scale(daily):
    """2454 and 6196 close above their five-session averages only once the ex-dividend gap is removed."""
    _publish_events()

    trend = _market(daily).get_snapshot(EVENT).trend

    assert (trend.ma5_comparable_count, trend.above_ma5_count) == (4, 3)


def test_research_averages_share_the_target_sessions_scale(daily, tmp_path):
    """The day before the ex-dividend session: published averages hold the later scale, the close does not."""
    _publish_events()
    _publisher(daily, tmp_path / "kline_daily_enriched").refresh()
    service = TaiwanStockResearchContextService(**_side_stores(daily))

    before = service.get_research_context("2379", BEFORE).technical_context
    on_event = service.get_research_context("2379", EVENT).technical_context

    assert _row(_partition(tmp_path / "kline_daily_enriched", BEFORE), "2379.TWSE", BEFORE)["ma5"] != 733.0
    assert (before.ma5, before.above_ma5) == (733.0, False)
    adjusted = [c * 704.0 / 729.0 for c in (735.0, 731.0, 730.0, 729.0)]
    assert on_event.ma5 == round((sum(adjusted) + 692.0) / 5, 4)


def test_the_watchlist_daily_fallback_uses_the_reference_price(daily):
    _publish_events()
    rows = _taiwan_daily_fallback_map(["2379.TWSE", "2330.TWSE"], daily)

    assert rows["2379.TWSE"]["date"] == SESSIONS[-1]
    assert rows["2379.TWSE"]["prev_close"] == 692.0

    event_only = MagicMock()
    event_only.latest_date.return_value = EVENT
    event_only.read_range.return_value = daily.read_range(["2379.TWSE", "2330.TWSE"], SESSIONS[0], EVENT)
    rows = _taiwan_daily_fallback_map(["2379.TWSE", "2330.TWSE"], event_only)

    assert rows["2379.TWSE"]["prev_close"] == 704.0
    assert rows["2379.TWSE"]["change"] == -12.0
    assert rows["2330.TWSE"]["prev_close"] == 1012.0


def _repo_with_daily(frame: pl.DataFrame):
    repo = MagicMock()

    def daily_asset(asset_type, symbol, start, end, columns=None):
        out = frame.filter((pl.col("symbol") == symbol) & (pl.col("date") >= start) & (pl.col("date") <= end))
        return out.select([c for c in columns if c in out.columns]) if columns else out

    repo.get_daily_asset.side_effect = daily_asset
    return repo


def test_the_minute_chart_previous_close_is_the_reference_on_an_event_session(daily):
    _publish_events()
    forward_adjusted = daily.read_range(None, SESSIONS[0], SESSIONS[-1]).with_columns(
        pl.col("close").alias("raw_close"), (pl.col("close") / 2).alias("close")
    )

    got = kline._get_previous_closes(_repo_with_daily(forward_adjusted), "2379.TWSE", [BEFORE, EVENT, SESSIONS[-1]], "stock")

    assert got == {BEFORE: 730.0, EVENT: 704.0, SESSIONS[-1]: 692.0}


def test_the_minute_chart_previous_close_of_other_markets_is_unchanged():
    """Regression guard: rows without a reference price keep their previous values."""
    frame = pl.DataFrame({
        "symbol": ["600000.SH"] * 2, "date": [BEFORE, EVENT], "close": [10.0, 10.5], "raw_close": [20.0, 21.0],
    })

    assert kline._get_previous_closes(_repo_with_daily(frame), "600000.SH", [EVENT], "stock") == {EVENT: 10.0}


def test_local_taiwan_bars_carry_the_reference_price_into_the_daily_api(daily):
    _publish_events()

    enriched = compute_enriched(
        kline._with_reference_prices("2379.TWSE", daily.scan_range(["2379.TWSE"], None, None)),
        factors=pl.DataFrame(),
    )

    assert _row(enriched, "2379.TWSE", EVENT)["change_pct"] == 692.0 / 704.0 - 1


def test_the_daily_api_forward_adjusts_local_taiwan_bars(daily):
    _publish_events()
    repo = MagicMock()
    repo.resolve_asset_type.return_value = "stock"
    repo.get_daily_asset.return_value = pl.DataFrame()
    request = MagicMock()
    request.app.state.repo = repo
    request.app.state.quote_service = None

    resp = kline.get_daily(request, "2379.TWSE", days=30, start_date=str(SESSIONS[0]), end_date=str(SESSIONS[-1]),
                           ext_columns=None)

    rows = {str(r["date"]): r for r in resp["rows"]}
    assert resp["source"] == "taiwan_local"
    assert rows[str(BEFORE)]["close"] == pytest.approx(729.0 * 704.0 / 729.0)
    assert rows[str(BEFORE)]["raw_close"] == 729.0
    assert rows[str(EVENT)]["close"] == 692.0


def test_the_strategy_screener_takes_the_published_limits_of_a_corporate_action_session(daily, tmp_path):
    """2454's published limit up (1500) is below the 1540 the reference price gives; it closed at 1500."""
    from app.services.screener import ScreenerService

    _publish_events()
    _publisher(daily, tmp_path / "kline_daily_enriched").refresh()
    repo = MagicMock()
    repo.store.data_dir = tmp_path
    repo.get_instruments_asset.return_value = pl.DataFrame({"symbol": list(CLOSES), "name": ["s"] * len(CLOSES)})
    repo.get_historical_shares.return_value = pl.DataFrame()
    target = pl.read_parquet(tmp_path / "kline_daily_enriched" / f"date={EVENT.isoformat()}" / "part.parquet")

    full = ScreenerService(repo)._compute_enriched_full(target, EVENT)

    assert _row(full, "2454.TWSE", EVENT)["signal_limit_up"] is True
    assert _row(full, "2330.TWSE", EVENT)["signal_limit_up"] is False


def _batch_request(stock_frame: pl.DataFrame, asked: list[list[str]]):
    repo = MagicMock()
    repo.resolve_asset_type.side_effect = lambda s: "index" if s == "TAIEX.TWSE" else "stock"

    def daily_batch(symbols, start, end, columns=None):
        asked.append(list(columns))
        return stock_frame.filter(pl.col("symbol").is_in(symbols))

    repo.get_daily_batch.side_effect = daily_batch
    repo.get_index_daily.side_effect = lambda symbol, start, end, columns=None: pl.DataFrame({
        "symbol": [symbol] * 2, "date": [BEFORE, EVENT], "open": [1.0, 1.0], "high": [1.0, 1.0],
        "low": [1.0, 1.0], "close": [20000.0, 20100.0], "volume": [1.0, 1.0],
    })
    request = MagicMock()
    request.app.state.repo = repo
    return request


def test_the_mini_candle_batch_carries_the_reference_price_on_an_event_session():
    raw = attach_reference_prices(_daily_frame(), _event_frame()).with_columns(pl.col("close").alias("raw_close"))
    asked: list[list[str]] = []

    out = kline.get_daily_batch(_batch_request(raw, asked), {"symbols": ["2379.TWSE", "2330.TWSE"], "days": 12})["data"]

    assert asked == [["symbol", "date", "open", "high", "low", "close", "volume", "raw_close", "ref_prev_close"]]
    realtek = {str(r["date"]): r for r in out["2379.TWSE"]}
    assert realtek[str(EVENT)]["prev_close"] == 704.0
    assert realtek[str(SESSIONS[-1])]["prev_close"] == 692.0
    assert set(realtek[str(EVENT)]) == {"symbol", "date", "open", "high", "low", "close", "volume", "prev_close"}


def test_the_mini_candle_batch_previous_close_elsewhere_is_the_previous_bar():
    """Without a reference price the new field equals the previous bar's close, the mini candle's old base."""
    out = kline.get_daily_batch(
        _batch_request(_daily_frame(), []), {"symbols": ["2330.TWSE", "TAIEX.TWSE"], "days": 12}
    )["data"]

    tsmc = out["2330.TWSE"]
    assert [r["prev_close"] for r in tsmc[1:]] == [r["close"] for r in tsmc[:-1]]
    assert out["TAIEX.TWSE"][1]["prev_close"] == 20000.0


def test_the_live_candle_carries_the_quote_previous_close():
    today = date.today()
    quote = pl.DataFrame({
        "symbol": ["2379.TWSE"], "open": [717.0], "high": [721.0], "low": [680.0], "close": [692.0],
        "volume": [1.0], "amount": [1.0], "change_pct": [692.0 / 704.0 - 1], "prev_close": [704.0],
    })
    qs = MagicMock()
    qs.get_enriched_today.return_value = (quote, today)
    request = MagicMock()
    request.app.state.quote_service = qs

    out = kline._maybe_inject_live_candle(request, "2379.TWSE", [{"date": str(BEFORE), "close": 729.0}])

    assert out[-1]["date"] == str(today)
    assert out[-1]["prev_close"] == 704.0
