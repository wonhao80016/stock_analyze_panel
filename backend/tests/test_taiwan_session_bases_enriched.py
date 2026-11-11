"""The enriched publish takes the exchange's base on a session after one the symbol did not trade.

Official figures come from the stored TWT84U / dailyQuotes tables (app.taiwan.session_bases):
1435.TWSE did not trade on 2025-04-07 and TWT84U(2025-04-08) prints base 33.65, limits
37.00 / 30.30; it then closed at 31.50 (STOCK_DAY 2025-04: open 31.00, high 31.50, low 31.00).
8101.TWSE last traded on 2024-08-21 at 1.90 and resumed on 2024-11-19 with TWT84U base 9.50,
limits 10.45 / 8.55, trading all day at 10.45.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.indicators import pipeline
from app.indicators.pipeline import (
    add_limit_distance_columns,
    invalidate_custom_signals,
    invalidate_taiwan_limit_profile_cache,
)
from app.parquet import ENRICHED_STORAGE_SCHEMA
from app.taiwan import enriched_refresh, universe
from app.taiwan.corporate_actions import TaiwanCorporateActionStore, corporate_action_schema
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import (
    FACTOR_SET_FILE,
    TaiwanEnrichedRefreshService,
    compute_taiwan_enriched,
)
from app.taiwan.enrichment.corporate_actions import EXDIV
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.session_bases import TPEX, TWSE, TaiwanSessionBaseStore, session_base_schema
from app.taiwan.share_capital_store import SCHEMA as SHARE_SCHEMA
from app.taiwan.share_capital_store import TaiwanShareCapitalStore
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

GAP_1435 = date(2025, 4, 8)
RESUMED_8101 = date(2024, 11, 19)
LIMIT_FLAGS = ("signal_limit_up", "signal_limit_down", "signal_broken_limit_up", "signal_limit_down_recovery")
BASED_ON_REFERENCE = ("prev_close", "change_pct", "change_amount", "amplitude")
HOLIDAYS = {date(2025, 4, 3), date(2025, 4, 4), date(2025, 5, 1)}


def _sessions(first: date, last: date) -> list[date]:
    days, day = [], first
    while day <= last:
        if day.weekday() < 5 and day not in HOLIDAYS:
            days.append(day)
        day += timedelta(days=1)
    return days


def _bar(symbol: str, day: date, close: float | None, *, open_: float | None = None,
         high: float | None = None, low: float | None = None, volume: float = 100_000.0) -> dict:
    """A one-session bar; close None is an untraded session, stored with null prices."""
    if close is None:
        return {"symbol": symbol, "date": day, "open": None, "high": None, "low": None, "close": None,
                "volume": 0.0, "amount": 0.0, "quote_ts": None}
    o, h, lo = open_ or close, high or close, low or close
    return {"symbol": symbol, "date": day, "open": o, "high": h, "low": lo, "close": close,
            "volume": volume, "amount": close * volume, "quote_ts": None}


def _frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema={
        "symbol": pl.String, "date": pl.Date, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64,
        "close": pl.Float64, "volume": pl.Float64, "amount": pl.Float64, "quote_ts": pl.Int64,
    })


def _bars_1435(sessions: list[date]) -> list[dict]:
    """1435.TWSE around 2025-04-08 per STOCK_DAY; earlier sessions are flat at 39.10."""
    known = {
        date(2025, 4, 1): (41.45, {}),
        date(2025, 4, 2): (37.35, {"open_": 40.50, "high": 40.50, "low": 37.35}),
        date(2025, 4, 7): (None, {}),
        GAP_1435: (31.50, {"open_": 31.00, "high": 31.50, "low": 31.00, "volume": 657_100.0}),
        date(2025, 4, 9): (32.00, {}),
        date(2025, 4, 10): (32.00, {}),
    }
    rows = []
    for day in sessions:
        close, extra = known.get(day, (39.10 if day < date(2025, 4, 1) else 32.00, {}))
        rows.append(_bar("1435.TWSE", day, close, **extra))
    return rows


def _bars_2330(sessions: list[date]) -> list[dict]:
    return [_bar("2330.TWSE", day, 900.0 + (k % 7), volume=20_000_000.0) for k, day in enumerate(sessions)]


def _limits(symbols: list[str], ipo: dict[str, tuple[date, date]] | None = None) -> pl.DataFrame:
    ipo = ipo or {}
    return pl.DataFrame(
        [
            {"symbol": s, "limit_pct": 0.1, "is_etf_tick": False,
             "ipo_start": ipo.get(s, (None, None))[0], "ipo_end": ipo.get(s, (None, None))[1]}
            for s in symbols
        ],
        schema={"symbol": pl.Utf8, "limit_pct": pl.Float64, "is_etf_tick": pl.Boolean,
                "ipo_start": pl.Date, "ipo_end": pl.Date},
    )


def _base_row(symbol: str, source_date: date, ref: float, up: float | None, down: float | None) -> dict:
    return {
        "symbol": symbol, "source_date": source_date, "ref_price": ref, "limit_up": up, "limit_down": down,
        "prev_close": None, "last_bid": None, "last_ask": None,
        "source": "twse:twt84u" if symbol.endswith(".TWSE") else "tpex:dailyquotes",
        "source_url": "https://example.invalid", "retrieved_at": datetime(2026, 11, 11, 16, 30, tzinfo=TAIPEI),
    }


def _store(tmp_path, rows: list[dict]) -> TaiwanSessionBaseStore:
    store = TaiwanSessionBaseStore(tmp_path / "session_bases")
    frame = pl.DataFrame(rows, schema=session_base_schema()) if rows else pl.DataFrame(schema=session_base_schema())
    for (source_date,), part in frame.partition_by("source_date", as_dict=True).items():
        source = TWSE if part["symbol"][0].endswith(".TWSE") else TPEX
        store.write(source, source_date, part)
    return store


def _no_events() -> pl.DataFrame:
    return pl.DataFrame(schema=corporate_action_schema())


def _enriched(raw: pl.DataFrame, sessions: list[date], store, *, events=None, limits=None) -> pl.DataFrame:
    symbols = raw["symbol"].unique().sort().to_list()
    return compute_taiwan_enriched(
        raw,
        _no_events() if events is None else events,
        sessions[-1],
        trading_days=sessions,
        limits=_limits(symbols) if limits is None else limits,
        instruments=pl.DataFrame(),
        today=sessions[-1],
        session_bases=store,
    )


def _row(frame: pl.DataFrame, symbol: str, day: date) -> dict:
    rows = frame.filter((pl.col("symbol") == symbol) & (pl.col("date") == day)).to_dicts()
    assert len(rows) == 1
    return rows[0]


def _instrument(symbol: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol, code=code, exchange=exchange, name=code, instrument_type="stock",
        listing_status="active", listing_date="2000-01-01", isin=None, industry=None, cfi_code=None,
        raw_category="股票", is_supported=True, source="TWSE_ISIN", updated_at="2025-04-01",
    )


def _install_master(taiwan, monkeypatch, symbols) -> None:
    master_path = taiwan / "security_master.parquet"
    master_path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame([_instrument(s).to_dict() for s in symbols]).write_parquet(master_path)
    master = TaiwanSecurityMaster(cache_path=master_path)
    assert master.load_cache()
    monkeypatch.setattr(universe, "_default_master", master)
    invalidate_taiwan_limit_profile_cache()


@pytest.fixture
def master(tmp_path, monkeypatch):
    _install_master(tmp_path / "taiwan", monkeypatch, ["1435.TWSE", "2330.TWSE"])
    yield
    invalidate_taiwan_limit_profile_cache()


@pytest.fixture
def april_2025():
    sessions = _sessions(date(2025, 2, 3), date(2025, 4, 10))
    return sessions, _frame(_bars_1435(sessions) + _bars_2330(sessions))


def test_a_session_after_an_untraded_one_takes_the_official_base(tmp_path, april_2025, master):
    sessions, raw = april_2025
    store = _store(tmp_path, [_base_row("1435.TWSE", GAP_1435, 33.65, 37.00, 30.30)])

    row = _row(_enriched(raw, sessions, store), "1435.TWSE", GAP_1435)

    assert (row["ref_prev_close"], row["ref_limit_up"], row["ref_limit_down"]) == (33.65, 37.00, 30.30)
    assert row["ref_base_missing"] is False
    assert row["prev_close"] == pytest.approx(33.65)
    assert row["change_pct"] == pytest.approx(31.50 / 33.65 - 1)
    assert row["change_pct"] == pytest.approx(-0.0639, abs=1e-4)
    assert row["change_amount"] == pytest.approx(31.50 - 33.65)
    assert row["amplitude"] == pytest.approx((31.50 - 31.00) / 33.65)
    assert row["signal_limit_down"] is False
    assert row["signal_limit_up"] is False
    assert row["consecutive_limit_downs"] == 0
    distance = _row(add_limit_distance_columns(_enriched(raw, sessions, store)), "1435.TWSE", GAP_1435)
    assert distance["distance_to_lower_limit"] == pytest.approx((31.50 - 30.30) / 30.30)


def test_a_session_resuming_after_a_halt_takes_the_official_base(tmp_path):
    sessions = _sessions(date(2024, 8, 1), date(2024, 11, 20))
    rows = [_bar("8101.TWSE", d, 1.90) for d in sessions if d <= date(2024, 8, 21)]
    rows += [_bar("8101.TWSE", RESUMED_8101, 10.45), _bar("8101.TWSE", date(2024, 11, 20), 11.45)]
    raw = _frame(rows + _bars_2330(sessions))
    store = _store(tmp_path, [_base_row("8101.TWSE", RESUMED_8101, 9.50, 10.45, 8.55)])

    out = _enriched(raw, sessions, store)
    row = _row(out, "8101.TWSE", RESUMED_8101)

    assert (row["ref_prev_close"], row["ref_limit_up"], row["ref_limit_down"]) == (9.50, 10.45, 8.55)
    assert row["change_pct"] == pytest.approx(10.45 / 9.50 - 1)
    assert row["signal_limit_up"] is True
    assert row["consecutive_limit_ups"] == 1
    assert _row(out, "8101.TWSE", date(2024, 11, 20))["consecutive_limit_ups"] == 2


def test_consecutive_untraded_sessions_use_the_official_base_not_the_last_close(tmp_path):
    """Synthetic TPEx figures: the base of session D3 is dailyQuotes(D2), the session before it."""
    sessions = _sessions(date(2025, 2, 3), date(2025, 3, 7))
    d1, d2, d3 = sessions[-3], sessions[-2], sessions[-1]
    rows = [_bar("6488.TPEX", d, 500.0) for d in sessions[:-3]]
    rows += [_bar("6488.TPEX", d1, None), _bar("6488.TPEX", d2, None), _bar("6488.TPEX", d3, 480.0)]
    raw = _frame(rows + _bars_2330(sessions))
    store = _store(tmp_path, [
        _base_row("6488.TPEX", d1, 497.0, 546.0, 447.5),
        _base_row("6488.TPEX", d2, 495.0, 544.0, 445.5),
    ])

    row = _row(_enriched(raw, sessions, store), "6488.TPEX", d3)

    assert (row["ref_prev_close"], row["ref_limit_up"], row["ref_limit_down"]) == (495.0, 544.0, 445.5)
    assert row["change_pct"] == pytest.approx(480.0 / 495.0 - 1)
    assert row["ref_base_missing"] is False


def test_without_an_official_base_the_change_and_the_limit_flags_are_unknown(tmp_path, april_2025, master):
    sessions, raw = april_2025
    store = _store(tmp_path, [])

    out = _enriched(raw, sessions, store)
    row = _row(out, "1435.TWSE", GAP_1435)

    assert row["ref_base_missing"] is True
    assert (row["ref_prev_close"], row["ref_limit_up"], row["ref_limit_down"]) == (None, None, None)
    assert {name: row[name] for name in BASED_ON_REFERENCE} == dict.fromkeys(BASED_ON_REFERENCE)
    assert {name: row[name] for name in LIMIT_FLAGS} == dict.fromkeys(LIMIT_FLAGS)
    assert (row["consecutive_limit_ups"], row["consecutive_limit_downs"]) == (0, 0)
    distance = _row(add_limit_distance_columns(out), "1435.TWSE", GAP_1435)
    assert (distance["distance_to_upper_limit"], distance["distance_to_lower_limit"]) == (None, None)
    assert out["ref_base_missing"].sum() == 1


def test_a_corporate_action_reference_price_outranks_the_official_base(tmp_path, april_2025):
    sessions, raw = april_2025
    store = _store(tmp_path, [_base_row("1435.TWSE", GAP_1435, 33.65, 37.00, 30.30)])
    events = pl.DataFrame(
        [{"symbol": "1435.TWSE", "date": GAP_1435, "kind": EXDIV, "pre_close": 37.35,
          "ref_price": 33.00, "limit_up": 36.30, "limit_down": 29.70}],
    ).select([
        pl.col(name).cast(dtype) if name in ("symbol", "date", "kind", "pre_close", "ref_price", "limit_up", "limit_down")
        else pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in corporate_action_schema().items()
    ])

    row = _row(_enriched(raw, sessions, store, events=events), "1435.TWSE", GAP_1435)

    assert (row["ref_prev_close"], row["ref_limit_up"], row["ref_limit_down"]) == (33.00, 36.30, 29.70)
    assert row["ref_base_missing"] is False
    assert row["change_pct"] == pytest.approx(31.50 / 33.00 - 1)


def test_an_initial_listing_session_without_limits_keeps_its_previous_close(tmp_path, april_2025):
    sessions, raw = april_2025
    store = _store(tmp_path, [])
    limits = _limits(["1435.TWSE", "2330.TWSE"], ipo={"1435.TWSE": (date(2025, 4, 2), GAP_1435)})

    row = _row(_enriched(raw, sessions, store, limits=limits), "1435.TWSE", GAP_1435)

    assert row["ref_base_missing"] is False
    assert row["change_pct"] == pytest.approx(31.50 / 37.35 - 1)


def test_sessions_after_a_traded_one_are_published_as_before(tmp_path, april_2025):
    """Official rows of traded-through sessions are present, one with a sentinel base, and unused."""
    sessions, raw = april_2025
    store = _store(tmp_path, [
        _base_row("1435.TWSE", GAP_1435, 33.65, 37.00, 30.30),
        _base_row("1435.TWSE", date(2025, 4, 9), 99.0, 108.5, 89.1),
        _base_row("2330.TWSE", sessions[-1], 1.0, 1.1, 0.9),
    ])
    symbols = ["1435.TWSE", "2330.TWSE"]
    before = compute_taiwan_enriched(
        raw, _no_events(), sessions[-1], trading_days=sessions, limits=_limits(symbols),
        instruments=pl.DataFrame(), today=sessions[-1],
    )

    after = _enriched(raw, sessions, store)

    not_gap = (pl.col("symbol") != "1435.TWSE") | (pl.col("date") != GAP_1435)
    keys = ["symbol", "date"]
    assert after.filter(not_gap).sort(keys).equals(before.filter(not_gap).sort(keys))
    assert after.filter(not_gap)["ref_base_missing"].to_list() == [False] * (after.height - 1)
    assert _row(before, "1435.TWSE", GAP_1435)["change_pct"] == pytest.approx(31.50 / 37.35 - 1)


# --- the publish ------------------------------------------------------------------------------

SHARES = {"1435.TWSE": 100_000_000, "2330.TWSE": 25_930_380_458}
FIRST_DAY = date(2025, 4, 30)
SECOND_DAY = date(2025, 5, 2)


@pytest.fixture
def market(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    taiwan = tmp_path / "taiwan"
    _install_master(taiwan, monkeypatch, list(SHARES))
    invalidate_custom_signals()
    pl.DataFrame(
        {
            "symbol": list(SHARES),
            "issued_shares": list(SHARES.values()),
            "report_date": ["2025-03-31"] * len(SHARES),
            "retrieved_at": ["2025-04-01T00:00:00"] * len(SHARES),
        },
        schema=SHARE_SCHEMA,
    ).write_parquet(taiwan / "share_capital.parquet")
    daily = TaiwanDailyStore(data_dir=taiwan / "daily")
    sessions = _sessions(date(2025, 2, 3), FIRST_DAY)
    daily.write_batch(_frame(_bars_1435(sessions) + _bars_2330(sessions)))
    store = _store(taiwan, [_base_row("1435.TWSE", GAP_1435, 33.65, 37.00, 30.30)])
    root = tmp_path / "kline_daily_enriched"

    def service(clock: date) -> TaiwanEnrichedRefreshService:
        monkeypatch.setattr(pipeline, "cn_today", lambda: clock)
        return TaiwanEnrichedRefreshService(
            daily_store=daily,
            enriched_root=root,
            share_capital=TaiwanShareCapitalStore(taiwan),
            corporate_actions=TaiwanCorporateActionStore(taiwan / "corporate_actions"),
            session_bases=store,
        )

    yield SimpleNamespace(root=root, daily=daily, service=service, data_dir=tmp_path)
    invalidate_taiwan_limit_profile_cache()
    invalidate_custom_signals()


def _read(root) -> dict[date, pl.DataFrame]:
    return {
        date.fromisoformat(p.parent.name.split("=", 1)[1]): pl.read_parquet(p).sort("symbol")
        for p in root.glob("date=*/part.parquet")
    }


def _record(market) -> dict:
    return json.loads((market.data_dir / FACTOR_SET_FILE).read_text(encoding="utf-8"))


def test_the_publish_stores_the_official_base_and_the_storage_schema_has_the_flag(market):
    market.service(FIRST_DAY).refresh()

    row = _row(_read(market.root)[GAP_1435], "1435.TWSE", GAP_1435)
    assert row["change_pct"] == pytest.approx(31.50 / 33.65 - 1)
    assert row["signal_limit_down"] is False
    assert ENRICHED_STORAGE_SCHEMA["ref_base_missing"] == pl.Boolean
    assert "ref_base_missing" in pipeline.ENRICHED_STORAGE_COLS


def test_the_version_4_release_rewrites_a_version_3_table_and_keeps_its_turnover(market, monkeypatch):
    monkeypatch.setattr(enriched_refresh, "INDICATOR_DEFINITION_VERSION", 3)
    market.service(FIRST_DAY).refresh()
    for day, frame in _read(market.root).items():
        stale = frame.drop("ref_base_missing")
        if day == GAP_1435:
            stale = stale.with_columns(
                pl.when(pl.col("symbol") == "1435.TWSE").then(31.50 / 37.35 - 1).otherwise(pl.col("change_pct"))
                .alias("change_pct")
            )
        stale.write_parquet(market.root / f"date={day.isoformat()}" / "part.parquet")
    published_turnover = dict(_read(market.root)[FIRST_DAY].select("symbol", "turnover_rate").iter_rows())
    assert _record(market)["indicator_version"] == 3
    monkeypatch.setattr(enriched_refresh, "INDICATOR_DEFINITION_VERSION", pipeline.INDICATOR_DEFINITION_VERSION)
    market.daily.write_batch(_frame([_bar("1435.TWSE", SECOND_DAY, 32.50), *_bars_2330([SECOND_DAY])]))

    market.service(SECOND_DAY).refresh_recent(days=5)

    after = _read(market.root)
    assert pipeline.INDICATOR_DEFINITION_VERSION == 4
    assert _record(market)["indicator_version"] == 4
    assert all("ref_base_missing" in frame.columns for frame in after.values())
    assert _row(after[GAP_1435], "1435.TWSE", GAP_1435)["change_pct"] == pytest.approx(31.50 / 33.65 - 1)
    kept = dict(after[FIRST_DAY].select("symbol", "turnover_rate").iter_rows())
    assert all(value is not None for value in published_turnover.values())
    assert kept == pytest.approx(published_turnover)


# --- readers of the published rows ------------------------------------------------------------


@pytest.fixture
def unknown_base_published(tmp_path, monkeypatch, april_2025, master):
    """The 2025-04-08 partition published without an official base for 1435.TWSE, under settings.data_dir."""
    from app.config import settings

    sessions, raw = april_2025
    out = _enriched(raw, sessions, _store(tmp_path, []))
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    partition = tmp_path / "kline_daily_enriched" / f"date={GAP_1435.isoformat()}"
    partition.mkdir(parents=True)
    out.filter(pl.col("date") == GAP_1435).write_parquet(partition / "part.parquet")
    return out


def test_storage_only_rows_recompute_unknown_flags_as_null(tmp_path, april_2025, master):
    from app.repository import read_enriched_window

    sessions, raw = april_2025
    out = _enriched(raw, sessions, _store(tmp_path, []))
    root = tmp_path / "kline_daily_enriched"
    for (day,), part in out.select(list(ENRICHED_STORAGE_SCHEMA)).partition_by("date", as_dict=True).items():
        (root / f"date={day.isoformat()}").mkdir(parents=True)
        part.write_parquet(root / f"date={day.isoformat()}" / "part.parquet")

    window = read_enriched_window(tmp_path, "kline_daily_enriched", date(2025, 4, 1), sessions[-1])

    row = _row(window, "1435.TWSE", GAP_1435)
    assert row["ref_base_missing"] is True
    assert row["change_pct"] is None
    assert {name: row[name] for name in LIMIT_FLAGS} == dict.fromkeys(LIMIT_FLAGS)
    assert _row(window, "1435.TWSE", date(2025, 4, 9))["signal_limit_down"] is False


def test_unknown_limit_flags_neither_fail_nor_trigger_the_signal_readers(tmp_path, unknown_base_published):
    from app.services.pick_grades import assign_grades
    from app.taiwan import current_signals, watchlist_enrichment

    section = current_signals.read_signals("1435.TWSE", tmp_path)
    assert section.status == "ok"
    assert not {signal.id for signal in section.triggered} & set(LIMIT_FLAGS)

    indicators, as_of = watchlist_enrichment._taiwan_indicator_map(["1435.TWSE"])
    assert as_of == GAP_1435.isoformat()
    assert indicators["1435.TWSE"]["signal_limit_down"] is None

    row = _row(unknown_base_published, "1435.TWSE", GAP_1435)
    graded = assign_grades({"s": {"rows": [{**row, "score": 1.0}]}}, None, {"s"})
    assert graded["s"]["rows"][0]["limit_up_locked"] is False


def test_the_watchlist_mini_chart_leaves_an_unknown_base_empty(monkeypatch, unknown_base_published):
    from app.api import kline as kline_api

    published = unknown_base_published.filter(pl.col("symbol") == "1435.TWSE")

    class _Repo:
        def resolve_asset_type(self, symbol):
            return "stock"

        def get_daily_batch(self, symbols, start, end, columns=None):
            return published.select([c for c in columns if c in published.columns])

    monkeypatch.setattr(kline_api, "_published_scale_through", lambda: None)
    monkeypatch.setattr(kline_api, "_frame_onto_todays_scale", lambda df, *_args: df)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=_Repo())))

    rows = kline_api.get_daily_batch(request, {"symbols": ["1435.TWSE"], "days": 60})["data"]["1435.TWSE"]

    by_day = {row["date"]: row["prev_close"] for row in rows}
    assert by_day[GAP_1435] is None
    assert by_day[date(2025, 4, 9)] == pytest.approx(31.50)


def test_an_unknown_change_never_passes_a_matrix_strategy_threshold(unknown_base_published):
    from app.backtest.matrix import build_market_data_matrix, matrix_feature

    panel = unknown_base_published.filter(pl.col("symbol") == "1435.TWSE")
    market = build_market_data_matrix(panel, field_columns={"change_pct"})

    change = matrix_feature(market, "change_pct")[:, market.symbols.index("1435.TWSE")]
    gap = panel["date"].to_list().index(GAP_1435)
    assert not (change[gap] > -1.0)
    assert change[gap + 1] == pytest.approx(32.00 / 31.50 - 1, rel=1e-6)
