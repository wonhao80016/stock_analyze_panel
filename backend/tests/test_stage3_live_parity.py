"""The Taiwan intraday enriched row of a session is the row the evening publish writes for it.

One fixture daily store holds an ex-dividend event and a par-value change on the quoted session,
a par-value change 30 sessions back, an initial listing inside its five-session no-limit window,
a halted session, histories shorter than 60 and 24 sessions, a ten-weekday market closure, a
third straight limit-up session and an MA5 breakout. The batch row comes from
TaiwanEnrichedRefreshService.refresh through session T; the live row from the history stored
through T-1 plus the bar of T. The "pending" root lacks the events of T, which the exchange
table only lists after the session, so the live row takes them from the quoted reference price.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.indicators import pipeline
from app.indicators.pipeline import (
    compute_enriched,
    filter_halt_days,
    invalidate_custom_signals,
    invalidate_taiwan_limit_profile_cache,
)
from app.repository import DataStore, KlineRepository
from app.services import quote_service
from app.services.quote_service import QuoteService
from app.taiwan import universe
from app.taiwan.corporate_actions import (
    TaiwanCorporateActionStore,
    adjustment_factors,
    attach_reference_prices,
    corporate_action_schema,
)
from app.taiwan.daily_store import TaiwanDailyStore, listing_sessions
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.enrichment.corporate_actions import EXDIV, PAR_CHANGE, REDUCTION
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.share_capital_store import TaiwanShareCapitalStore
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument
from app.taiwan.volume_patterns import VOLUME_PATTERN_COLUMNS, add_volume_patterns

pytestmark = pytest.mark.usefixtures("deny_network")

SESSIONS = 260
CLOSURE = frozenset(date(2026, 8, 10) + timedelta(days=i) for i in range(12))

DIVIDEND = "1101.TWSE"
SPLIT = "2327.TWSE"
PLAIN = "2330.TWSE"
HALTED = "3545.TWSE"
SHORT = "4000.TWSE"
TINY = "4001.TWSE"
IPO = "6999.TWSE"
LIMIT = "2603.TWSE"
BREAKOUT = "2002.TWSE"
PAR_TODAY = "2409.TWSE"
SYMBOLS = (DIVIDEND, SPLIT, PLAIN, HALTED, SHORT, TINY, IPO, LIMIT, BREAKOUT, PAR_TODAY)

DIVIDEND_FACTOR = 1.08
SPLIT_RATIO = 4.0
SPLIT_AT = SESSIONS - 30
PAR_TODAY_RATIO = 2.0
HALT_AT = SESSIONS - 6
FIRST_ROW = {SHORT: SESSIONS - 40, TINY: SESSIONS - 12, IPO: SESSIONS - 3}
LIMIT_CLOSES = (50.0, 55.0, 60.5, 66.5)
BREAKOUT_CLOSES = (100.0, 99.0, 98.0, 97.0, 96.0)
BREAKOUT_LIVE_CLOSES = (100.0, 101.0)


def _sessions() -> list[date]:
    out, day = [], date(2025, 8, 4)
    while len(out) < SESSIONS:
        if day.weekday() < 5 and day not in CLOSURE:
            out.append(day)
        day += timedelta(days=1)
    return out


DAYS = _sessions()
TODAY = DAYS[-1]
CLOSING_TS = int(
    datetime(TODAY.year, TODAY.month, TODAY.day, 13, 30, tzinfo=TAIPEI).timestamp() * 1000
)


def _wave(t: int, base: float, phase: float) -> float:
    return base * (1 + 0.08 * math.sin(t / 6 + phase) + 0.04 * math.sin(t / 17 + phase)) + 0.02 * t


def _closes(symbol: str) -> list[float]:
    phase = SYMBOLS.index(symbol) * 0.7
    closes = [_wave(t, 60.0 + 10 * SYMBOLS.index(symbol), phase) for t in range(SESSIONS)]
    if symbol == LIMIT:
        closes[-len(LIMIT_CLOSES) :] = LIMIT_CLOSES
    if symbol == BREAKOUT:
        closes = (
            [100.0] * (SESSIONS - len(BREAKOUT_CLOSES))
            + list(BREAKOUT_CLOSES[1:])
            + [BREAKOUT_LIVE_CLOSES[0]]
        )
    if symbol == DIVIDEND:
        closes = [c * DIVIDEND_FACTOR for c in closes[:-1]] + closes[-1:]
    if symbol == SPLIT:
        closes = [c * SPLIT_RATIO for c in closes[:SPLIT_AT]] + closes[SPLIT_AT:]
    if symbol == PAR_TODAY:
        closes = [c * PAR_TODAY_RATIO for c in closes[:-1]] + closes[-1:]
    return closes


def _bar(symbol: str, t: int, close: float, prev: float | None) -> dict:
    open_ = close if prev is None else prev
    pinned = symbol == LIMIT and t >= SESSIONS - len(LIMIT_CLOSES) + 1
    volume = 2_000_000.0 * (1 + 0.3 * math.sin(t / 5 + SYMBOLS.index(symbol)))
    if symbol == SPLIT and t < SPLIT_AT:
        volume /= SPLIT_RATIO
    if symbol == PAR_TODAY and t < SESSIONS - 1:
        volume /= PAR_TODAY_RATIO
    return {
        "symbol": symbol,
        "date": DAYS[t],
        "open": open_,
        "high": close if pinned else max(open_, close) + 0.3 + 0.1 * abs(math.sin(t / 3)),
        "low": open_ if pinned else min(open_, close) - 0.3 - 0.1 * abs(math.cos(t / 4)),
        "close": close,
        "volume": volume,
        "amount": close * volume,
        "quote_ts": CLOSING_TS if t == SESSIONS - 1 else None,
    }


def _rows(symbol: str) -> list[dict]:
    closes, rows, prev = _closes(symbol), [], None
    for t in range(FIRST_ROW.get(symbol, 0), SESSIONS):
        if symbol == HALTED and t == HALT_AT:
            rows.append(
                {
                    **dict.fromkeys(("open", "high", "low", "close"), None),
                    "symbol": symbol,
                    "date": DAYS[t],
                    "volume": 0.0,
                    "amount": 0.0,
                    "quote_ts": None,
                }
            )
            continue
        rows.append(_bar(symbol, t, closes[t], prev))
        prev = closes[t]
    return rows


SCHEMA = {
    "symbol": pl.String,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "amount": pl.Float64,
    "quote_ts": pl.Int64,
}
ALL_BARS = pl.DataFrame([row for s in SYMBOLS for row in _rows(s)], schema=SCHEMA)


def _quoted_prev_close(symbol: str) -> float:
    """The provider's previous close: the exchange reference price on an event session."""
    if symbol in REFERENCE_PRICES:
        return REFERENCE_PRICES[symbol]
    return ALL_BARS.filter(
        (pl.col("symbol") == symbol) & (pl.col("date") < TODAY) & pl.col("close").is_not_null()
    )["close"][-1]


def _quote_extra() -> pl.DataFrame:
    return pl.DataFrame(
        {"symbol": list(SYMBOLS), "prev_close": [_quoted_prev_close(s) for s in SYMBOLS]}
    )


def _live_bars(quoted: bool = False, **closes: float) -> pl.DataFrame:
    """Today's bars as QuoteService._build_daily emits them; *closes* override a symbol's close by name.

    quoted=True adds the provider's prev_close, as the quote path passes it to the engine.
    """
    today = ALL_BARS.filter(pl.col("date") == TODAY)
    overrides = {globals()[name]: value for name, value in closes.items()}
    if overrides:
        close = pl.coalesce(
            pl.col("symbol").replace_strict(overrides, default=None, return_dtype=pl.Float64),
            pl.col("close"),
        )
        today = today.with_columns(
            close.alias("close"), pl.max_horizontal("high", close).alias("high")
        )
    today = today.select(
        "symbol", "close", "open", "high", "low", "volume", "amount", "quote_ts", "date"
    )
    return today.join(_quote_extra(), on="symbol", how="left") if quoted else today


def _instrument(symbol: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=code,
        instrument_type="stock",
        listing_status="active",
        listing_date=DAYS[FIRST_ROW[IPO]].isoformat() if symbol == IPO else "2000-01-01",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="股票",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-10-07",
    )


EVENTS = (
    (DIVIDEND, SESSIONS - 1, EXDIV, DIVIDEND_FACTOR),
    (SPLIT, SPLIT_AT, PAR_CHANGE, SPLIT_RATIO),
    (PAR_TODAY, SESSIONS - 1, PAR_CHANGE, PAR_TODAY_RATIO),
)
REFERENCE_PRICES = {
    symbol: _closes(symbol)[at - 1] / ratio for symbol, at, _, ratio in EVENTS if at == SESSIONS - 1
}


def _events() -> pl.DataFrame:
    rows = []
    for symbol, at, kind, ratio in EVENTS:
        pre = _closes(symbol)[at - 1]
        ref = pre / ratio
        rows.append(
            {
                "symbol": symbol,
                "date": DAYS[at],
                "kind": kind,
                "pre_close": pre,
                "ref_price": ref,
                "limit_up": None,
                "limit_down": None,
                "open_ref": ref,
                "ex_factor": pre / ref,
                "official_label": None,
                "provider": "twse",
                "source": "test",
                "source_url": "u",
                "retrieved_at": None,
                "trade_date": DAYS[at],
                "status": "official",
            }
        )
    return pl.DataFrame(rows, schema=corporate_action_schema())


def _seed(root: Path, through: date, events_through: date) -> None:
    TaiwanDailyStore(data_dir=root / "taiwan" / "daily").write_batch(
        ALL_BARS.filter(pl.col("date") <= through)
    )
    TaiwanCorporateActionStore(data_dir=root / "taiwan" / "corporate_actions").upsert(
        _events().filter(pl.col("date") <= events_through)
    )
    pl.DataFrame(
        {
            "symbol": [PLAIN, LIMIT, DIVIDEND],
            "issued_shares": [25_000_000_000, 1_000_000_000, 7_500_000_000],
            "report_date": ["1150930"] * 3,
            "retrieved_at": ["2026-10-07"] * 3,
        },
        schema={
            "symbol": pl.Utf8,
            "issued_shares": pl.Int64,
            "report_date": pl.Utf8,
            "retrieved_at": pl.Utf8,
        },
    ).write_parquet(root / "taiwan" / "share_capital.parquet")


def _publisher(root: Path) -> TaiwanEnrichedRefreshService:
    return TaiwanEnrichedRefreshService(
        daily_store=TaiwanDailyStore(data_dir=root / "taiwan" / "daily"),
        enriched_root=root / "kline_daily_enriched",
        share_capital=TaiwanShareCapitalStore(data_dir=root / "taiwan"),
        corporate_actions=TaiwanCorporateActionStore(
            data_dir=root / "taiwan" / "corporate_actions"
        ),
    )


@pytest.fixture(scope="module")
def stores(tmp_path_factory) -> dict:
    """A batch root published through T; live and pending roots stored and published through T-1.

    The live root holds the events of T, the pending root does not.
    """
    base = tmp_path_factory.mktemp("stage3_live_parity")
    master_path = base / "security_master.parquet"
    pl.DataFrame([_instrument(s).to_dict() for s in SYMBOLS]).write_parquet(master_path)
    master = TaiwanSecurityMaster(cache_path=master_path)
    assert master.load_cache()
    with pytest.MonkeyPatch.context() as mp:
        from app.config import settings

        mp.setattr(universe, "_default_master", master)
        mp.setattr(pipeline, "cn_today", lambda: TODAY)
        invalidate_taiwan_limit_profile_cache()
        invalidate_custom_signals()
        try:
            for name, through, events_through in (
                ("batch", TODAY, TODAY),
                ("live", DAYS[-2], TODAY),
                ("pending", DAYS[-2], DAYS[-2]),
            ):
                mp.setattr(settings, "data_dir", base / name)
                _seed(base / name, through, events_through)
                _publisher(base / name).refresh()
        finally:
            invalidate_taiwan_limit_profile_cache()
            invalidate_custom_signals()
    published = pl.read_parquet(base / "batch" / "kline_daily_enriched" / "**" / "*.parquet")
    return {"base": base, "master": master, "published": published}


def _use_root(stores: dict, monkeypatch, root: Path):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", root)
    monkeypatch.setattr(universe, "_default_master", stores["master"])
    monkeypatch.setattr(pipeline, "cn_today", lambda: TODAY)
    monkeypatch.setattr(quote_service, "cn_today", lambda: TODAY)
    monkeypatch.setattr(quote_service, "_persist_last_fetch", lambda _ms: None)
    invalidate_taiwan_limit_profile_cache()
    invalidate_custom_signals()
    yield root
    invalidate_taiwan_limit_profile_cache()
    invalidate_custom_signals()


@pytest.fixture
def live_root(stores, monkeypatch) -> Path:
    yield from _use_root(stores, monkeypatch, stores["base"] / "live")


@pytest.fixture
def pending_root(stores, monkeypatch) -> Path:
    yield from _use_root(stores, monkeypatch, stores["base"] / "pending")


def _batch_row(stores: dict, symbol: str) -> dict:
    return (
        stores["published"]
        .filter((pl.col("symbol") == symbol) & (pl.col("date") == TODAY))
        .row(0, named=True)
    )


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12) or (math.isnan(a) and math.isnan(b))
    return a == b


def _diff(expected: dict, got: dict) -> dict:
    return {
        c: (expected[c], got.get(c, "<missing>"))
        for c in expected
        if c not in got or not _same(expected[c], got[c])
    }


def test_the_live_engine_row_is_the_published_row_on_every_column(stores, live_root):
    from app.taiwan.live_enriched import TaiwanLiveEnriched

    engine = TaiwanLiveEnriched(live_root)
    engine.prepare()
    live = engine.rows(_live_bars())

    assert live is not None
    assert sorted(live["symbol"].to_list()) == sorted(SYMBOLS)
    for symbol in SYMBOLS:
        expected = _batch_row(stores, symbol)
        got = live.filter(pl.col("symbol") == symbol).row(0, named=True)
        assert _diff(expected, got) == {}, symbol


def test_the_fixture_covers_every_case(stores):
    rows = {s: _batch_row(stores, s) for s in SYMBOLS}
    assert rows[DIVIDEND]["ex_rights"] is True and rows[DIVIDEND]["ref_prev_close"] is not None
    assert rows[IPO]["signal_limit_up"] is False and rows[IPO]["ma5"] is None
    assert rows[SHORT]["ma60"] is None and rows[SHORT]["rsi_24"] is not None
    assert rows[TINY]["rsi_14"] is None and rows[TINY]["rsi_6"] is not None
    assert rows[LIMIT]["consecutive_limit_ups"] == 3
    assert rows[BREAKOUT]["signal_ma5_breakout"] is True
    assert rows[PLAIN]["turnover_rate"] is not None
    halted = stores["published"].filter(pl.col("symbol") == HALTED)
    assert DAYS[HALT_AT] not in halted["date"].to_list() and halted.height == SESSIONS - 1


def test_the_publish_writes_what_the_composed_pipeline_computes(stores):
    """refresh() publishes exactly the frame its steps compute when composed by hand."""
    root = stores["base"] / "batch"
    service = _publisher(root)
    with pytest.MonkeyPatch.context() as mp:
        from app.config import settings

        mp.setattr(settings, "data_dir", root)
        mp.setattr(universe, "_default_master", stores["master"])
        mp.setattr(pipeline, "cn_today", lambda: TODAY)
        invalidate_taiwan_limit_profile_cache()
        invalidate_custom_signals()
        try:
            events = service.corporate_actions.read(end=TODAY)
            sessions = listing_sessions(service.daily_store)
            raw = attach_reference_prices(
                filter_halt_days(service.daily_store.scan_range(None, None, TODAY)),
                events,
                with_limits=True,
            )
            composed = compute_enriched(
                raw,
                factors=adjustment_factors(events, TODAY),
                instruments=service.share_capital.as_instruments(),
            )
            composed = service._add_limit_signals(
                composed,
                sessions,
                service._limit_profile(sorted(sessions.first_sessions), sessions),
            )
            composed = add_volume_patterns(composed, service.daily_store.available_dates())
        finally:
            invalidate_taiwan_limit_profile_cache()
            invalidate_custom_signals()

    published = stores["published"].sort(["date", "symbol"])
    assert published.columns == composed.columns
    assert published.equals(composed.sort(["date", "symbol"]))


def _repo_and_service(root: Path) -> tuple[KlineRepository, QuoteService]:
    repo = KlineRepository(DataStore(root))
    repo.refresh_cache()
    service = QuoteService()
    service._repo = repo
    return repo, service


def _today_row(repo: KlineRepository, symbol: str) -> dict:
    latest, latest_date = repo.get_enriched_latest()
    assert latest_date == TODAY
    return latest.filter(pl.col("symbol") == symbol).row(0, named=True)


def test_every_poll_compares_with_the_previous_session(live_root):
    """The second poll of the day still crosses against yesterday's row, not the first poll's."""
    repo, service = _repo_and_service(live_root)

    for close in BREAKOUT_LIVE_CLOSES:
        service._flush_live_enriched(_live_bars(BREAKOUT=close))
        row = _today_row(repo, BREAKOUT)
        assert row["close"] == close
        assert row["signal_ma5_breakout"] is True, close


def test_a_third_limit_up_session_counts_three_without_an_instrument_table(live_root):
    assert not list((live_root / "instruments").rglob("*.parquet"))
    repo, service = _repo_and_service(live_root)

    service._flush_live_enriched(_live_bars())

    row = _today_row(repo, LIMIT)
    assert row["signal_limit_up"] is True
    assert row["consecutive_limit_ups"] == 3


def test_the_live_row_carries_the_volume_patterns(stores, live_root):
    repo, service = _repo_and_service(live_root)

    service._flush_live_enriched(_live_bars())

    for symbol in (PLAIN, HALTED, IPO):
        row = _today_row(repo, symbol)
        assert {c: row.get(c, "<missing>") for c in VOLUME_PATTERN_COLUMNS} == {
            c: _batch_row(stores, symbol)[c] for c in VOLUME_PATTERN_COLUMNS
        }, symbol


def test_short_histories_keep_the_warm_up_nulls(live_root):
    repo, service = _repo_and_service(live_root)

    service._flush_live_enriched(_live_bars())

    tiny, short = _today_row(repo, TINY), _today_row(repo, SHORT)
    assert (tiny["rsi_6"] is not None, tiny["rsi_14"], tiny["rsi_24"]) == (True, None, None)
    assert (short["rsi_24"] is not None, short["ma60"], short["momentum_60d"]) == (True, None, None)


def test_the_quote_path_writes_the_published_row(stores, live_root):
    repo, service = _repo_and_service(live_root)

    service._flush_live_enriched(_live_bars())

    for symbol in SYMBOLS:
        expected = _batch_row(stores, symbol)
        assert _diff(expected, _today_row(repo, symbol)) == {}, symbol


def test_an_event_the_table_lacks_is_taken_from_the_quoted_reference(stores, pending_root):
    """A two-for-one par-value change is taken for a split, restating volume like the publish."""
    from app.taiwan.live_enriched import TaiwanLiveEnriched

    engine = TaiwanLiveEnriched(pending_root)
    engine.prepare()
    live = engine.rows(_live_bars(quoted=True))

    for symbol in SYMBOLS:
        got = live.filter(pl.col("symbol") == symbol).row(0, named=True)
        assert _diff(_batch_row(stores, symbol), got) == {}, symbol


def test_the_quote_path_passes_the_quoted_reference(stores, pending_root):
    repo, service = _repo_and_service(pending_root)

    service._flush_live_enriched(_live_bars(), _quote_extra())

    row = _today_row(repo, DIVIDEND)
    assert _diff(_batch_row(stores, DIVIDEND), row) == {}
    assert row["ref_prev_close"] == pytest.approx(REFERENCE_PRICES[DIVIDEND])


@pytest.fixture
def published_root(stores, tmp_path, monkeypatch) -> Path:
    root = tmp_path / "published"
    shutil.copytree(stores["base"] / "batch", root)
    yield from _use_root(stores, monkeypatch, root)


def test_a_poll_after_the_publish_keeps_the_published_partition(stores, published_root):
    partition = (
        published_root / "kline_daily_enriched" / f"date={TODAY.isoformat()}" / "part.parquet"
    )
    before = pl.read_parquet(partition)
    repo, service = _repo_and_service(published_root)

    service._flush_live_enriched(_live_bars(), _quote_extra())

    assert pl.read_parquet(partition).equals(before)
    assert _diff(_batch_row(stores, PLAIN), _today_row(repo, PLAIN)) == {}


def test_custom_signals_see_the_scaled_volume_ratio(stores, live_root, tmp_path, monkeypatch):
    from app.config import settings
    from app.strategy import custom_signals
    from app.taiwan.live_enriched import TaiwanLiveEnriched
    from app.taiwan.realtime.calendar import session_progress_from_ts

    signals_root = tmp_path / "signals"
    custom_signals.save_one(
        signals_root,
        {
            "id": "busy",
            "name": "busy",
            "kind": "entry",
            "conditions": [{"left": "vol_ratio_5d", "op": ">=", "right": 1.5}],
        },
    )
    monkeypatch.setattr(settings, "data_dir", signals_root)
    invalidate_custom_signals()
    midday = int(
        datetime(TODAY.year, TODAY.month, TODAY.day, 11, 15, tzinfo=TAIPEI).timestamp() * 1000
    )
    unscaled = 0.9
    scaled = unscaled / session_progress_from_ts(midday)
    assert scaled >= 1.5 > unscaled
    full_ratio = _batch_row(stores, PLAIN)["vol_ratio_5d"]
    bars = _live_bars().with_columns(
        pl.when(pl.col("symbol") == PLAIN)
        .then(pl.col("volume") * unscaled / full_ratio)
        .otherwise(pl.col("volume"))
        .alias("volume"),
        pl.lit(midday, dtype=pl.Int64).alias("quote_ts"),
    )
    engine = TaiwanLiveEnriched(live_root)
    engine.prepare()

    row = engine.rows(bars).filter(pl.col("symbol") == PLAIN).row(0, named=True)

    assert row["vol_ratio_5d"] == pytest.approx(scaled)
    assert row[custom_signals.column_name("busy")] is True
    assert row["signal_volume_surge"] is False


def test_a_repaired_older_partition_makes_the_history_stale(live_root, tmp_path, monkeypatch):
    from app.taiwan.live_enriched import TaiwanLiveEnriched

    root = tmp_path / "repair"
    shutil.copytree(live_root / "taiwan", root / "taiwan")
    engine = TaiwanLiveEnriched(root)
    engine.prepare()
    assert engine.rows(_live_bars()) is not None

    older = root / "taiwan" / "daily" / f"date={DAYS[-5].isoformat()}" / "part.parquet"
    pl.read_parquet(older).with_columns(pl.col("close") * 1.01).write_parquet(older)
    stamp = time.time() + 5
    os.utime(older, (stamp, stamp))
    scheduled: list[tuple] = []
    monkeypatch.setattr(engine, "prepare_in_background", lambda *args: scheduled.append(args))

    assert engine.rows(_live_bars()) is None
    assert len(scheduled) == 1


def _wait_until_idle(engine) -> None:
    deadline = time.monotonic() + 10
    while engine._preparing and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not engine._preparing


def test_a_failing_preparation_backs_off_and_warns_once(live_root, tmp_path, monkeypatch, caplog):
    from app.taiwan.live_enriched import TaiwanLiveEnriched

    engine = TaiwanLiveEnriched(tmp_path)
    attempts: list[tuple] = []

    def unreadable(key):
        attempts.append(key)
        raise OSError("history unreadable")

    monkeypatch.setattr(engine, "_load", unreadable)
    with caplog.at_level(logging.WARNING, logger="app.taiwan.live_enriched"):
        for _ in range(3):
            assert engine.rows(_live_bars()) is None
            _wait_until_idle(engine)

    assert len(attempts) == 1
    assert len([r for r in caplog.records if "preparation failed" in r.getMessage()]) == 1


LONG_EXTRA_SESSIONS = 150


def _long_days(count: int) -> list[date]:
    out, day = [], date(2020, 1, 1)
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


@pytest.fixture(scope="module")
def long_stores(stores, tmp_path_factory) -> dict:
    """PLAIN with 150 sessions more than the live cap, every one before its window tripled."""
    from app.taiwan.live_enriched import LIVE_HISTORY_SESSIONS

    days = _long_days(LIVE_HISTORY_SESSIONS + LONG_EXTRA_SESSIONS)
    last = days[-1]
    closing = int(
        datetime(last.year, last.month, last.day, 13, 30, tzinfo=TAIPEI).timestamp() * 1000
    )
    rows, prev = [], None
    for t, d in enumerate(days):
        close = _wave(t, 60.0, 0.3) * (3.0 if t < LONG_EXTRA_SESSIONS - 1 else 1.0)
        open_ = close if prev is None else prev
        volume = 2_000_000.0 * (1 + 0.3 * math.sin(t / 5))
        rows.append(
            {
                "symbol": PLAIN,
                "date": d,
                "open": open_,
                "high": max(open_, close) + 0.3,
                "low": min(open_, close) - 0.3,
                "close": close,
                "volume": volume,
                "amount": close * volume,
                "quote_ts": closing if d == last else None,
            }
        )
        prev = close
    bars = pl.DataFrame(rows, schema=SCHEMA)
    base = tmp_path_factory.mktemp("stage3_live_long")
    with pytest.MonkeyPatch.context() as mp:
        from app.config import settings

        mp.setattr(universe, "_default_master", stores["master"])
        invalidate_taiwan_limit_profile_cache()
        invalidate_custom_signals()
        try:
            for name, through in (("batch", last), ("live", days[-2])):
                mp.setattr(settings, "data_dir", base / name)
                TaiwanDailyStore(data_dir=base / name / "taiwan" / "daily").write_batch(
                    bars.filter(pl.col("date") <= through)
                )
                _publisher(base / name).refresh()
        finally:
            invalidate_taiwan_limit_profile_cache()
            invalidate_custom_signals()
    partition = base / "batch" / "kline_daily_enriched" / f"date={last.isoformat()}"
    return {
        "base": base,
        "bar": bars.filter(pl.col("date") == last),
        "published": pl.read_parquet(partition / "part.parquet"),
    }


def test_a_history_longer_than_the_live_cap_matches_the_publish(stores, long_stores, monkeypatch):
    from app.taiwan.live_enriched import TaiwanLiveEnriched

    root = long_stores["base"] / "live"
    for _ in _use_root(stores, monkeypatch, root):
        engine = TaiwanLiveEnriched(root)
        engine.prepare()
        got = engine.rows(long_stores["bar"]).row(0, named=True)

    assert _diff(long_stores["published"].row(0, named=True), got) == {}


def _copy_taiwan(source: Path, target: Path) -> Path:
    shutil.copytree(source / "taiwan", target / "taiwan")
    return target


def _rewrite_daily(root: Path, change) -> None:
    """Apply change(frame, day) to every stored daily partition of *root*; an empty result drops it."""
    for partition in sorted((root / "taiwan" / "daily").glob("date=*/part.parquet")):
        day = date.fromisoformat(partition.parent.name[5:])
        frame = change(pl.read_parquet(partition), day)
        if frame.is_empty():
            shutil.rmtree(partition.parent)
        else:
            frame.write_parquet(partition)


def _quoted_rows(root: Path, bars: pl.DataFrame) -> pl.DataFrame:
    from app.taiwan.live_enriched import TaiwanLiveEnriched

    engine = TaiwanLiveEnriched(root)
    engine.prepare()
    return engine.rows(bars)


def test_a_store_one_session_behind_takes_no_quoted_reference(pending_root, tmp_path, caplog):
    root = _copy_taiwan(pending_root, tmp_path / "behind")
    _rewrite_daily(root, lambda frame, day: frame.clear() if day == DAYS[-2] else frame)

    from app.taiwan.live_enriched import TaiwanLiveEnriched

    engine = TaiwanLiveEnriched(root)
    engine.prepare()
    with caplog.at_level(logging.WARNING, logger="app.taiwan.live_enriched"):
        live = engine.rows(_live_bars(quoted=True))
        engine.rows(_live_bars(quoted=True).with_columns(pl.col("close") + 0.01))

    assert live["ex_rights"].to_list() == [False] * live.height
    assert live["ref_prev_close"].null_count() == live.height
    assert len([r for r in caplog.records if "behind" in r.getMessage()]) == 1


def test_a_previous_session_without_tpex_takes_no_quoted_reference_for_tpex(pending_root, tmp_path):
    otc = SHORT.replace(".TWSE", ".TPEX")
    root = _copy_taiwan(pending_root, tmp_path / "half")

    def move_to_tpex(frame: pl.DataFrame, day: date) -> pl.DataFrame:
        frame = frame.with_columns(pl.col("symbol").replace(SHORT, otc))
        return frame.filter(pl.col("symbol") != otc) if day == DAYS[-2] else frame

    _rewrite_daily(root, move_to_tpex)
    bars = _live_bars(quoted=True).with_columns(pl.col("symbol").replace(SHORT, otc))

    live = _quoted_rows(root, bars)

    otc_row = live.filter(pl.col("symbol") == otc).row(0, named=True)
    assert (otc_row["ex_rights"], otc_row["ref_prev_close"]) == (False, None)
    dividend = live.filter(pl.col("symbol") == DIVIDEND).row(0, named=True)
    assert dividend["ref_prev_close"] == pytest.approx(REFERENCE_PRICES[DIVIDEND])


REDUCTION_FACTOR = 0.8


def _halted_through_yesterday(frame: pl.DataFrame, day: date) -> pl.DataFrame:
    return frame.filter(~((pl.col("symbol") == HALTED) & (pl.col("date") >= DAYS[-5])))


def test_a_halted_symbol_resuming_with_a_capital_reduction_takes_the_quoted_reference(
    stores, pending_root, tmp_path
):
    """The quoted reduction restates the history's volume as the published event does."""
    root = _copy_taiwan(pending_root, tmp_path / "resume")
    _rewrite_daily(root, _halted_through_yesterday)
    pre = ALL_BARS.filter(
        (pl.col("symbol") == HALTED) & (pl.col("date") < DAYS[-5]) & pl.col("close").is_not_null()
    )["close"][-1]
    reference = pre / REDUCTION_FACTOR

    batch = tmp_path / "resume_batch"
    shutil.copytree(stores["base"] / "batch" / "taiwan", batch / "taiwan")
    _rewrite_daily(
        batch, lambda frame, day: frame if day == TODAY else _halted_through_yesterday(frame, day)
    )
    schema = corporate_action_schema()
    TaiwanCorporateActionStore(data_dir=batch / "taiwan" / "corporate_actions").upsert(
        pl.DataFrame(
            [
                {
                    **dict.fromkeys(schema),
                    "symbol": HALTED,
                    "date": TODAY,
                    "kind": REDUCTION,
                    "pre_close": pre,
                    "ref_price": reference,
                    "ex_factor": REDUCTION_FACTOR,
                    "trade_date": TODAY,
                    "status": "official",
                }
            ],
            schema=schema,
        )
    )
    _publisher(batch).refresh()
    published = pl.read_parquet(
        batch / "kline_daily_enriched" / f"date={TODAY.isoformat()}" / "part.parquet"
    )

    bars = _live_bars(quoted=True).with_columns(
        pl.when(pl.col("symbol") == HALTED)
        .then(reference)
        .otherwise(pl.col("prev_close"))
        .alias("prev_close")
    )
    got = _quoted_rows(root, bars).filter(pl.col("symbol") == HALTED).row(0, named=True)

    assert got["ex_rights"] is True
    assert _diff(published.filter(pl.col("symbol") == HALTED).row(0, named=True), got) == {}
