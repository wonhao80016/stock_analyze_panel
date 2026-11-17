"""A TPEx symbol resuming after a halt is missing from the previous session's dailyQuotes, so its
base is recovered from dailyQuotes of the resumption session itself: close less change.

1591.TPEX last traded on 2026-06-10 at 31.15 and resumed on 2026-07-22; dailyQuotes(2026-07-22)
prints close 34.25 and change +3.10, so the exchange's base was 31.15.
"""
from __future__ import annotations

import copy
import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.taiwan import session_bases
from app.taiwan.corporate_actions import REF_LIMIT_UP, REF_PREV_CLOSE, corporate_action_schema
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import REF_BASE_MISSING, with_official_bases
from app.taiwan.enrichment.corporate_actions import REDUCTION
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.session_bases import (
    TPEX,
    TaiwanSessionBaseRefreshService,
    TaiwanSessionBaseStore,
    TpexSessionBaseAdapter,
    official_bases_of,
    session_base_schema,
)

pytestmark = pytest.mark.usefixtures("deny_network")

FIXTURES = Path(__file__).parent / "fixtures" / "taiwan_session_bases"
OTC = "1591.TPEX"
PEER = "6488.TPEX"
LAST_TRADED = date(2026, 7, 20)
HALTED = date(2026, 7, 21)
RESUMED = date(2026, 7, 22)
NEXT = date(2026, 7, 23)
SESSIONS = [LAST_TRADED, HALTED, RESUMED, NEXT]
RETRIEVED = datetime(2026, 7, 22, 21, 40, tzinfo=TAIPEI)


def _payload() -> dict:
    return json.loads((FIXTURES / "tpex_dq_20241001_excerpt.json").read_text(encoding="utf-8"))


def _parse(payload: dict) -> pl.DataFrame:
    adapter = TpexSessionBaseAdapter()
    day = date(2024, 10, 1)
    return adapter.parse_payload(payload, day, adapter.build_url(day), codes=None)


def _change(frame: pl.DataFrame, symbol: str) -> float | None:
    return frame.filter(pl.col("symbol") == symbol)["prev_change"].item()


def _row(symbol: str, source_date: date, close: float | None, change: float | None, ref: float) -> dict:
    return {"symbol": symbol, "source_date": source_date, "ref_price": ref, "limit_up": ref * 1.1,
            "limit_down": ref * 0.9, "prev_close": close, "last_bid": close, "last_ask": None,
            "prev_change": change, "source": "tpex:dailyquotes", "source_url": "https://example.invalid",
            "retrieved_at": RETRIEVED}


def _store(tmp_path, change: float | None = 3.10, rows: dict[date, list[dict]] | None = None) -> TaiwanSessionBaseStore:
    """dailyQuotes of SESSIONS: OTC trades on LAST_TRADED and RESUMED and is left out of HALTED's."""
    store = TaiwanSessionBaseStore(tmp_path / "session_bases")
    days = rows if rows is not None else {
        LAST_TRADED: [_row(OTC, LAST_TRADED, 31.15, 0.0, 31.15), _row(PEER, LAST_TRADED, 450.0, 0.0, 450.0)],
        HALTED: [_row(PEER, HALTED, 452.5, 2.5, 452.5)],
        RESUMED: [_row(OTC, RESUMED, 34.25, change, 34.25), _row(PEER, RESUMED, 452.5, 0.0, 452.5)],
    }
    for day, part in days.items():
        store.write(TPEX, day, pl.DataFrame(part, schema=session_base_schema()))
    return store


def _bases(store: TaiwanSessionBaseStore, symbol: str = OTC, day: date = RESUMED) -> dict:
    out = official_bases_of(pl.DataFrame({"symbol": [symbol], "trade_date": [day]}), store=store, sessions=SESSIONS)
    return out.row(0, named=True)


# --- dailyQuotes change column --------------------------------------------------------------------


def test_daily_quotes_store_the_official_change():
    frame = _parse(_payload())

    assert _change(frame, "6488.TPEX") == -2.50
    assert _change(frame, "5274.TPEX") == 70.00
    assert _change(frame, "00679B.TPEX") == 0.12
    assert _change(frame, "1240.TPEX") is None


@pytest.mark.parametrize("cell", ["除息", "X0.50", "+", "1.2.3", "", "---"])
def test_a_change_cell_that_is_no_signed_number_is_stored_as_null(cell):
    payload = _payload()
    table = payload["tables"][0]
    row = next(r for r in table["data"] if r[0] == "6488")
    row[table["fields"].index("漲跌")] = cell

    assert _change(_parse(payload), "6488.TPEX") is None


def test_daily_quotes_without_a_change_column_still_parse():
    payload = _payload()
    for table in payload["tables"]:
        table["fields"] = ["漲跌幅" if f == "漲跌" else f for f in table["fields"]]

    frame = _parse(payload)

    assert frame["prev_change"].null_count() == frame.height
    assert frame.filter(pl.col("symbol") == "6488.TPEX")["ref_price"].item() == 452.50


def test_files_stored_before_the_change_column_read_it_as_null(tmp_path):
    store = TaiwanSessionBaseStore(tmp_path)
    old = pl.DataFrame([_row(PEER, LAST_TRADED, 450.0, None, 450.0)], schema=session_base_schema()).drop("prev_change")
    path = store.path(TPEX, LAST_TRADED)
    path.parent.mkdir(parents=True)
    old.write_parquet(path)
    store.write(TPEX, HALTED, pl.DataFrame([_row(PEER, HALTED, 452.5, 2.5, 452.5)], schema=session_base_schema()))

    assert store.read(TPEX, [LAST_TRADED])["prev_change"].to_list() == [None]
    both = store.read(TPEX, [LAST_TRADED, HALTED]).sort("source_date")
    assert both.columns == list(session_base_schema())
    assert both.select("ref_price", "prev_change").rows() == [(450.0, None), (452.5, 2.5)]


# --- Base of the resumption session -----------------------------------------------------------------


def test_a_symbol_left_out_of_the_previous_daily_quotes_takes_close_less_change(tmp_path):
    store = _store(tmp_path)

    row = _bases(store)
    assert (row["ref_price"], row["limit_up"], row["limit_down"]) == (31.15, None, None)
    listed = _bases(store, PEER)
    assert (listed["ref_price"], listed["limit_up"]) == (452.5, pytest.approx(497.75))


def test_an_unparsed_change_leaves_the_resumption_base_missing(tmp_path):
    assert _bases(_store(tmp_path / "parsed"))["ref_price"] == 31.15
    assert _bases(_store(tmp_path / "unparsed", change=None))["ref_price"] is None


def test_without_the_previous_daily_quotes_no_base_is_recovered(tmp_path):
    store = _store(tmp_path)
    assert _bases(store)["ref_price"] == 31.15

    store.path(TPEX, HALTED).unlink()

    assert _bases(store)["ref_price"] is None


def test_the_recovered_base_leaves_the_requested_quote_columns_null(tmp_path):
    pairs = pl.DataFrame({"symbol": [OTC], "trade_date": [RESUMED]})

    out = official_bases_of(
        pairs, store=_store(tmp_path), sessions=SESSIONS, columns=("ref_price", "limit_up", "last_bid", "last_ask")
    )

    assert out.row(0) == (OTC, RESUMED, 31.15, None, None, None)


def _limits() -> pl.DataFrame:
    return pl.DataFrame({"symbol": [OTC], "ipo_start": [None], "ipo_end": [None]},
                        schema={"symbol": pl.String, "ipo_start": pl.Date, "ipo_end": pl.Date})


def _resumed_bars() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [OTC, OTC], "date": [LAST_TRADED, RESUMED], "close": [31.15, 34.25],
        REF_PREV_CLOSE: [None, None], REF_LIMIT_UP: [None, None], "ref_limit_down": [None, None],
    }, schema_overrides={REF_PREV_CLOSE: pl.Float64, REF_LIMIT_UP: pl.Float64, "ref_limit_down": pl.Float64})


def test_the_publish_takes_the_recovered_base_unless_an_event_is_dated_on_the_resumption(tmp_path):
    store = _store(tmp_path)
    no_events = pl.DataFrame(schema=corporate_action_schema())

    out = with_official_bases(_resumed_bars(), SESSIONS, _limits(), store, no_events)

    resumed = out.filter(pl.col("date") == RESUMED).row(0, named=True)
    assert (resumed[REF_PREV_CLOSE], resumed[REF_LIMIT_UP], resumed[REF_BASE_MISSING]) == (31.15, None, False)

    event = {**dict.fromkeys(corporate_action_schema()), "symbol": OTC, "date": RESUMED, "kind": REDUCTION,
             "pre_close": 31.15, "ref_price": 28.0, "limit_up": 30.8, "limit_down": 25.2}
    events = pl.DataFrame([event], schema=corporate_action_schema())
    bars = _resumed_bars().with_columns(
        pl.when(pl.col("date") == RESUMED).then(28.0).alias(REF_PREV_CLOSE),
        pl.when(pl.col("date") == RESUMED).then(30.8).alias(REF_LIMIT_UP),
    )

    out = with_official_bases(bars, SESSIONS, _limits(), store, events)

    resumed = out.filter(pl.col("date") == RESUMED).row(0, named=True)
    assert (resumed[REF_PREV_CLOSE], resumed[REF_LIMIT_UP], resumed[REF_BASE_MISSING]) == (28.0, 30.8, False)


# --- Refetching the files resumption bases need ----------------------------------------------------


def _daily(root: Path) -> TaiwanDailyStore:
    store = TaiwanDailyStore(root / "daily")
    bars = [
        (OTC, LAST_TRADED, 31.15), (OTC, RESUMED, 34.25), (OTC, NEXT, 34.0),
        *[(PEER, d, 450.0) for d in SESSIONS],
        ("6999.TPEX", RESUMED, 20.0),
    ]
    store.write_batch(pl.DataFrame([
        {"symbol": s, "date": d, "open": c, "high": c, "low": c, "close": c, "volume": 1.0, "amount": c,
         "quote_ts": None}
        for s, d, c in bars
    ], schema_overrides={"quote_ts": pl.Int64}))
    return store


def _final(store: TaiwanSessionBaseStore) -> None:
    for day in store.stored_dates(TPEX):
        when = datetime.combine(day + timedelta(days=1), time(9), TAIPEI).timestamp()
        os.utime(store.path(TPEX, day), (when, when))


def _refresh_service(tmp_path, store: TaiwanSessionBaseStore, asked: list[str]) -> TaiwanSessionBaseRefreshService:
    payload = _payload()

    def fetch(url: str) -> dict:
        asked.append(url)
        answer = copy.deepcopy(payload)
        answer["date"] = f"{RESUMED:%Y%m%d}"
        answer["tables"][0]["data"][0][0] = "1591"
        return answer

    return TaiwanSessionBaseRefreshService(
        store=store, daily_store=_daily(tmp_path), adapters=[TpexSessionBaseAdapter()], fetch_json=fetch,
        codes=lambda _exchange, _day: {"1591", "6488"}, sleep=lambda _s: None,
    )


def test_resumption_dates_name_the_sessions_whose_own_file_lacks_the_change(tmp_path):
    store = _store(tmp_path, change=None)
    service = _refresh_service(tmp_path, store, [])

    assert service.resumption_dates(LAST_TRADED, NEXT) == [RESUMED]
    assert service.resumption_dates(NEXT, NEXT) == []


def test_resumption_dates_skip_a_file_that_has_the_change(tmp_path):
    service = _refresh_service(tmp_path, _store(tmp_path), [])

    assert service.resumption_dates(LAST_TRADED, NEXT) == []


def test_a_forced_session_is_refetched_although_its_file_is_final(tmp_path):
    store = _store(tmp_path, change=None)
    _final(store)
    asked: list[str] = []
    service = _refresh_service(tmp_path, store, asked)

    stats = service.refresh(LAST_TRADED, RESUMED, sources=(TPEX,), force=[RESUMED])

    assert asked == [TpexSessionBaseAdapter().build_url(RESUMED)]
    assert (stats["requests"], stats["written"], stats["skipped"]) == (1, 1, 2)
    assert store.read(TPEX, [RESUMED]).filter(pl.col("symbol") == OTC)["prev_change"].item() == 0.12


def test_the_resumption_dry_run_lists_the_dates_without_fetching(tmp_path, monkeypatch, capsys):
    store = _store(tmp_path, change=None)
    asked: list[str] = []
    service = _refresh_service(tmp_path, store, asked)
    monkeypatch.setattr(session_bases, "TaiwanSessionBaseRefreshService", lambda: service)

    session_bases.main(["--since", "2026-07-20", "--until", "2026-07-23", "--resumptions", "--dry-run"])

    assert json.loads(capsys.readouterr().out) == {"requests": 1, "dates": ["2026-07-22"]}
    assert asked == []
