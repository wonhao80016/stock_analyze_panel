"""Custom signals reading the limit streaks or turnover see the values the Taiwan publish keeps.

The fixture stores two symbols through T-1 and publishes them, prices T on the intraday engine,
then stores T and publishes the recent window, as the daily job does. 2603 closes limit-up on its
last three sessions; both symbols have share capital, so the newest session gets a turnover_rate.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.indicators import pipeline
from app.indicators.pipeline import invalidate_custom_signals, invalidate_taiwan_limit_profile_cache
from app.strategy import custom_signals
from app.taiwan import universe
from app.taiwan.corporate_actions import TaiwanCorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.live_enriched import TaiwanLiveEnriched
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.share_capital_store import TaiwanShareCapitalStore
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

LIMIT = "2603.TWSE"
PLAIN = "2330.TWSE"
SYMBOLS = (LIMIT, PLAIN)
SESSIONS = 80
LIMIT_CLOSES = (50.0, 55.0, 60.5, 66.5)


def _sessions() -> list[date]:
    out, day = [], date(2026, 3, 2)
    while len(out) < SESSIONS:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


DAYS = _sessions()
TODAY = DAYS[-1]
CLOSING_TS = int(datetime(TODAY.year, TODAY.month, TODAY.day, 13, 30, tzinfo=TAIPEI).timestamp() * 1000)

SIGNALS = {
    "streak": [{"left": "consecutive_limit_ups", "op": ">=", "right": 2}],
    "busy": [{"left": "turnover_rate", "op": ">=", "right": 0}],
    "rising": [{"left": "turnover_rate", "op": ">", "right": "field:turnover_rate", "rightDays": 1}],
    "early": [{"left": "close", "op": ">", "right": "field:ma5"}],
}
LATE = ("streak", "busy", "rising")


def _closes(symbol: str) -> list[float]:
    phase = SYMBOLS.index(symbol) * 0.9
    closes = [
        round(60.0 * (1 + 0.08 * math.sin(t / 6 + phase)) + 0.02 * t, 1) for t in range(SESSIONS)
    ]
    if symbol == LIMIT:
        closes[-len(LIMIT_CLOSES) :] = LIMIT_CLOSES
    return closes


def _bars() -> pl.DataFrame:
    rows = []
    for symbol in SYMBOLS:
        closes, prev = _closes(symbol), None
        for t, close in enumerate(closes):
            open_ = close if prev is None else prev
            pinned = symbol == LIMIT and t >= SESSIONS - len(LIMIT_CLOSES) + 1
            volume = 2_000_000.0 * (1 + 0.4 * math.sin(t / 3 + SYMBOLS.index(symbol)))
            rows.append(
                {
                    "symbol": symbol,
                    "date": DAYS[t],
                    "open": open_,
                    "high": close if pinned else max(open_, close) + 0.3,
                    "low": open_ if pinned else min(open_, close) - 0.3,
                    "close": close,
                    "volume": volume,
                    "amount": close * volume,
                    "quote_ts": CLOSING_TS if t == SESSIONS - 1 else None,
                }
            )
            prev = close
    return pl.DataFrame(
        rows,
        schema={
            "symbol": pl.String,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "amount": pl.Float64,
            "quote_ts": pl.Int64,
        },
    )


BARS = _bars()


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
        updated_at="2026-10-07",
    )


def _publisher(root: Path) -> TaiwanEnrichedRefreshService:
    return TaiwanEnrichedRefreshService(
        daily_store=TaiwanDailyStore(data_dir=root / "taiwan" / "daily"),
        enriched_root=root / "kline_daily_enriched",
        share_capital=TaiwanShareCapitalStore(data_dir=root / "taiwan"),
        corporate_actions=TaiwanCorporateActionStore(data_dir=root / "taiwan" / "corporate_actions"),
    )


@pytest.fixture(scope="module")
def published(tmp_path_factory) -> dict:
    """The live rows of T from the store through T-1, then the rows published after storing T."""
    from app.config import settings

    root = tmp_path_factory.mktemp("late_custom_signals")
    master_path = root / "security_master.parquet"
    pl.DataFrame([_instrument(s).to_dict() for s in SYMBOLS]).write_parquet(master_path)
    master = TaiwanSecurityMaster(cache_path=master_path)
    assert master.load_cache()
    for signal_id, conditions in SIGNALS.items():
        custom_signals.save_one(
            root, {"id": signal_id, "name": signal_id, "kind": "entry", "conditions": conditions}
        )
    (root / "taiwan").mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {
            "symbol": list(SYMBOLS),
            "issued_shares": [1_000_000_000, 25_000_000_000],
            "report_date": ["1150930"] * 2,
            "retrieved_at": ["2026-10-07"] * 2,
        },
        schema={"symbol": pl.Utf8, "issued_shares": pl.Int64, "report_date": pl.Utf8, "retrieved_at": pl.Utf8},
    ).write_parquet(root / "taiwan" / "share_capital.parquet")

    handler = _Records()
    logging.getLogger().addHandler(handler)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "data_dir", root)
        mp.setattr(universe, "_default_master", master)
        mp.setattr(pipeline, "cn_today", lambda: TODAY)
        invalidate_taiwan_limit_profile_cache()
        invalidate_custom_signals()
        try:
            daily = TaiwanDailyStore(data_dir=root / "taiwan" / "daily")
            daily.write_batch(BARS.filter(pl.col("date") < TODAY))
            _publisher(root).refresh()
            TaiwanShareCapitalStore(data_dir=root / "taiwan").save_session_denominators(DAYS[-2])

            engine = TaiwanLiveEnriched(root)
            engine.prepare()
            live = engine.rows(BARS.filter(pl.col("date") == TODAY))

            daily.write_batch(BARS.filter(pl.col("date") == TODAY))
            _publisher(root).refresh_recent()
        finally:
            logging.getLogger().removeHandler(handler)
            invalidate_taiwan_limit_profile_cache()
            invalidate_custom_signals()
    rows = pl.read_parquet(root / "kline_daily_enriched" / "**" / "*.parquet").sort(["symbol", "date"])
    return {"rows": rows, "live": live, "warnings": handler.messages}


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _column(signal_id: str) -> str:
    return custom_signals.column_name(signal_id)


def _row(frame: pl.DataFrame, symbol: str, day: date) -> dict:
    return frame.filter((pl.col("symbol") == symbol) & (pl.col("date") == day)).row(0, named=True)


def test_a_streak_signal_follows_the_published_limit_streak(published):
    rows = published["rows"]

    assert rows[_column("streak")].to_list() == (rows["consecutive_limit_ups"] >= 2).to_list()
    assert _row(rows, LIMIT, TODAY)["consecutive_limit_ups"] == 3
    assert [_row(rows, LIMIT, d)[_column("streak")] for d in DAYS[-4:]] == [False, False, True, True]


def test_a_turnover_signal_reads_the_turnover_each_session_keeps(published):
    rows = published["rows"]
    yesterday, today = _row(rows, PLAIN, DAYS[-2]), _row(rows, PLAIN, TODAY)

    assert yesterday["turnover_rate"] is not None and today["turnover_rate"] is not None
    assert yesterday[_column("busy")] is True and today[_column("busy")] is True
    assert today[_column("rising")] is (today["turnover_rate"] > yesterday["turnover_rate"])
    older = rows.filter(pl.col("date") < DAYS[-2])
    assert older["turnover_rate"].null_count() == older.height
    assert older[_column("busy")].null_count() == older.height


def test_the_live_row_of_a_session_is_its_published_row(published):
    live = published["live"]

    assert live is not None
    for symbol in SYMBOLS:
        got, expected = _row(live, symbol, TODAY), _row(published["rows"], symbol, TODAY)
        for name in ("turnover_rate", "consecutive_limit_ups", *map(_column, SIGNALS)):
            assert got[name] == expected[name], (symbol, name)


def test_a_signal_on_early_columns_keeps_its_place_and_values(published):
    rows = published["rows"]

    assert rows[_column("early")].to_list() == (rows["close"] > rows["ma5"]).to_list()
    assert rows.columns.index(_column("early")) < rows.columns.index("consecutive_limit_ups")


def test_no_custom_signal_is_skipped(published):
    assert not [m for m in published["warnings"] if "custom signal" in m]
    assert all(_column(signal_id) in published["rows"].columns for signal_id in LATE)


def test_rows_recomputed_from_storage_read_the_streak_the_window_returns(tmp_path, monkeypatch):
    """read_enriched_window keeps the stored streak over the one *limit_signals* writes; so does the signal."""
    from app.config import settings
    from app.parquet import ENRICHED_STORAGE_SCHEMA
    from app.repository import read_enriched_window

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    custom_signals.save_one(
        tmp_path, {"id": "streak", "name": "streak", "kind": "entry", "conditions": SIGNALS["streak"]}
    )
    invalidate_custom_signals()
    stored = (
        BARS.filter(pl.col("symbol") == PLAIN)
        .with_columns(
            pl.col("close").alias("raw_close"),
            pl.col("high").alias("raw_high"),
            pl.col("low").alias("raw_low"),
            pl.lit(3, dtype=pl.UInt32).alias("consecutive_limit_ups"),
        )
    )
    stored = stored.select(
        [
            pl.col(c).cast(t) if c in stored.columns else pl.lit(None, dtype=t).alias(c)
            for c, t in ENRICHED_STORAGE_SCHEMA.items()
        ]
    )
    for (day,), part in stored.partition_by("date", as_dict=True).items():
        path = tmp_path / "kline_daily_enriched" / f"date={day.isoformat()}" / "part.parquet"
        path.parent.mkdir(parents=True)
        part.write_parquet(path)

    try:
        window = read_enriched_window(
            tmp_path,
            "kline_daily_enriched",
            DAYS[-5],
            TODAY,
            limit_signals=lambda frame: frame.with_columns(
                pl.lit(0, dtype=pl.UInt32).alias("consecutive_limit_ups")
            ),
        )
    finally:
        invalidate_custom_signals()

    assert window["consecutive_limit_ups"].to_list() == [3] * 5
    assert window[_column("streak")].to_list() == [True] * 5
