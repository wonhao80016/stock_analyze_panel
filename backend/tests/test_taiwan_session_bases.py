"""The session-base store holds the exchanges' official change bases and limit prices per session.

Fixtures are official responses saved on 2026-11-10. twt84u_20250408_excerpt.json keeps the
2025-04-08 TWT84U header and seven of its rows, and tpex_dq_20241001_excerpt.json the 2024-10-01
dailyQuotes header and seven rows of its quote table; in both, total / totalCount was set to the
rows kept. twt84u_20250406_no_data.json (a Sunday) and tpex_dq_20241002_closed.json (the
2024-10-02 typhoon closure) are unchanged.
"""
from __future__ import annotations

import copy
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.taiwan import session_bases
from app.taiwan.daily_store import TaiwanDailyStore, daily_final_cutoff
from app.taiwan.institutional_margin_refresh import ExchangeThrottledError
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.session_bases import (
    TPEX,
    TWSE,
    ListedCodes,
    TaiwanSessionBaseRefreshService,
    TaiwanSessionBaseStore,
    TpexSessionBaseAdapter,
    TwseSessionBaseAdapter,
    official_session_bases,
)

FIXTURES = Path(__file__).parent / "fixtures" / "taiwan_session_bases"
TWSE_DAY = date(2025, 4, 8)
TPEX_DAY = date(2024, 10, 1)
TWSE_CODES = {"0050", "0061", "1101", "1435", "2330"}
TPEX_CODES = {"00679B", "00720B", "1240", "2235", "5274", "6488"}


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _twse(payload: dict | None = None, day: date = TWSE_DAY, codes: set[str] | None = TWSE_CODES) -> pl.DataFrame:
    adapter = TwseSessionBaseAdapter()
    payload = _load("twt84u_20250408_excerpt.json") if payload is None else payload
    return adapter.parse_payload(payload, day, adapter.build_url(day), codes=codes)


def _tpex(payload: dict | None = None, day: date = TPEX_DAY, codes: set[str] | None = TPEX_CODES) -> pl.DataFrame:
    adapter = TpexSessionBaseAdapter()
    payload = _load("tpex_dq_20241001_excerpt.json") if payload is None else payload
    return adapter.parse_payload(payload, day, adapter.build_url(day), codes=codes)


def _row(frame: pl.DataFrame, symbol: str) -> dict:
    rows = frame.filter(pl.col("symbol") == symbol).to_dicts()
    assert len(rows) == 1
    return rows[0]


def _prices(row: dict) -> tuple:
    return tuple(row[c] for c in ("ref_price", "limit_up", "limit_down", "prev_close", "last_bid", "last_ask"))


def _restamped(payload: dict, day: date) -> dict:
    payload = copy.deepcopy(payload)
    payload["date"] = f"{day:%Y%m%d}"
    return payload


def _daily_store(root: Path, sessions: list[date], symbols: tuple[str, ...] = ("2330.TWSE", "6488.TPEX")) -> TaiwanDailyStore:
    store = TaiwanDailyStore(root / "daily")
    store.write_batch(pl.DataFrame([
        {"symbol": s, "date": d, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
         "volume": 1.0, "amount": 10.0, "quote_ts": None}
        for d in sessions for s in symbols
    ], schema_overrides={"quote_ts": pl.Int64}))
    return store


def _set_mtime(path: Path, when: datetime) -> None:
    ts = when.timestamp()
    os.utime(path, (ts, ts))


# --- TWT84U ---------------------------------------------------------------------------------


def test_twt84u_takes_the_bases_of_its_own_session_from_the_today_group():
    payload = _load("twt84u_20250408_excerpt.json")
    frame = _twse(payload)

    assert [g["title"] for g in payload["groups"]] == ["", "本日", "前日", ""]
    assert payload["fields"].count("開盤競價基準") == 2
    row = _row(frame, "1435.TWSE")
    # No trade and no bid on 2025-04-07; the ask below the previous base 37.35 became the base.
    assert _prices(row) == (33.65, 37.00, 30.30, None, None, 33.65)
    assert row["source_date"] == TWSE_DAY
    assert (row["source"], row["retrieved_at"].tzinfo is not None) == ("twse:twt84u", True)
    assert row["source_url"] == (
        "https://www.twse.com.tw/rwd/zh/variation/TWT84U?date=20250408&selectType=ALL&response=json"
    )
    assert _prices(_row(frame, "2330.TWSE")) == (848.0, 932.0, 764.0, 848.0, None, 848.0)


def test_twt84u_stores_the_no_limit_sentinels_as_null():
    row = _row(_twse(), "0061.TWSE")

    assert _prices(row) == (17.73, None, None, 17.73, 17.69, 17.73)


def test_twt84u_dash_cells_are_null():
    frame = _twse(codes=None)

    assert _prices(_row(frame, "03963C.TWSE")) == (1.46, 2.42, 0.50, None, None, None)


def test_twt84u_keeps_only_the_listed_codes():
    assert sorted(_twse()["symbol"]) == ["0050.TWSE", "0061.TWSE", "1101.TWSE", "1435.TWSE", "2330.TWSE"]
    assert _twse(codes=None).height == 7


def test_twt84u_answer_for_another_date_fails():
    with pytest.raises(ValueError, match="answered '20250408'"):
        _twse(day=date(2025, 4, 9))


def test_twt84u_no_data_answer_fails():
    with pytest.raises(ValueError, match="rejected"):
        _twse(_load("twt84u_20250406_no_data.json"), day=date(2025, 4, 6))


def test_twt84u_without_its_today_group_fails():
    payload = _load("twt84u_20250408_excerpt.json")
    payload["groups"][1]["title"] = "前日"

    with pytest.raises(ValueError, match="column groups titled"):
        _twse(payload)


def test_twt84u_row_count_must_match_its_total():
    payload = _load("twt84u_20250408_excerpt.json")
    payload["data"].pop()

    with pytest.raises(ValueError, match="reports total"):
        _twse(payload)


def test_twt84u_with_none_of_the_listed_codes_fails():
    with pytest.raises(ValueError, match="none of the listed codes"):
        _twse(codes={"9999"})


# --- dailyQuotes ----------------------------------------------------------------------------


def test_daily_quotes_store_the_next_session_columns_with_the_spaced_titles():
    payload = _load("tpex_dq_20241001_excerpt.json")
    frame = _tpex(payload)

    assert payload["tables"][0]["fields"][16:] == ["次日 參考價", "次日 漲停價", "次日 跌停價"]
    row = _row(frame, "6488.TPEX")
    assert _prices(row) == (452.50, 497.50, 407.50, 452.50, 452.50, 453.00)
    assert (row["source_date"], row["source"]) == (TPEX_DAY, "tpex:dailyquotes")
    assert row["source_url"].endswith("dailyQuotes?date=113/10/01&response=json")


def test_daily_quotes_titles_match_without_the_space_too():
    payload = _load("tpex_dq_20241001_excerpt.json")
    for table in payload["tables"]:
        table["fields"] = [f.replace(" ", "") for f in table["fields"]]

    assert _tpex(payload).drop("retrieved_at").equals(_tpex().drop("retrieved_at"))


def test_daily_quotes_no_trade_close_and_sentinels_are_null():
    frame = _tpex()

    assert _prices(_row(frame, "1240.TPEX")) == (53.10, 58.40, 47.80, None, 53.10, 53.40)
    assert _prices(_row(frame, "2235.TPEX")) == (48.70, 53.50, 43.85, None, 48.40, 49.80)
    assert _prices(_row(frame, "00720B.TPEX")) == (36.94, None, None, 36.94, 36.94, 36.95)
    assert "70001U.TPEX" not in frame["symbol"].to_list()


def test_daily_quotes_of_a_closed_day_fail():
    with pytest.raises(ValueError, match="no rows"):
        _tpex(_load("tpex_dq_20241002_closed.json"), day=date(2024, 10, 2))


def test_daily_quotes_answer_for_another_date_fails():
    with pytest.raises(ValueError, match="answered '20241001'"):
        _tpex(day=date(2024, 10, 4))


def test_daily_quotes_without_the_next_session_columns_fail():
    payload = _load("tpex_dq_20241001_excerpt.json")
    for table in payload["tables"]:
        table["fields"][16] = "參考價"

    with pytest.raises(ValueError, match="next-session reference columns"):
        _tpex(payload)


# --- Store and alignment --------------------------------------------------------------------


def test_twt84u_bases_apply_to_their_own_session(tmp_path):
    store = TaiwanSessionBaseStore(tmp_path)
    store.write(TWSE, TWSE_DAY, _twse())

    out = official_session_bases(
        ["1435.TWSE", "9999.TWSE"], [TWSE_DAY, date(2025, 4, 9)], store=store, sessions=[]
    )

    assert out.columns == ["symbol", "trade_date", "ref_price", "limit_up", "limit_down"]
    assert out.rows() == [
        ("1435.TWSE", TWSE_DAY, 33.65, 37.00, 30.30),
        ("1435.TWSE", date(2025, 4, 9), None, None, None),
        ("9999.TWSE", TWSE_DAY, None, None, None),
        ("9999.TWSE", date(2025, 4, 9), None, None, None),
    ]


def test_daily_quotes_apply_to_the_next_stored_session_across_a_typhoon_closure(tmp_path):
    store = TaiwanSessionBaseStore(tmp_path)
    store.write(TPEX, TPEX_DAY, _tpex())
    sessions = [date(2024, 9, 30), TPEX_DAY, date(2024, 10, 4), date(2024, 10, 7)]
    asked = [TPEX_DAY, date(2024, 10, 2), date(2024, 10, 4), date(2024, 10, 7), date(2024, 10, 8)]

    out = official_session_bases(["6488.TPEX", "2330.TWSE"], asked, store=store, sessions=sessions)

    by_day = {r["trade_date"]: r for r in out.filter(pl.col("symbol") == "6488.TPEX").to_dicts()}
    assert (by_day[date(2024, 10, 4)]["ref_price"], by_day[date(2024, 10, 4)]["limit_up"]) == (452.50, 497.50)
    for missing in (TPEX_DAY, date(2024, 10, 2), date(2024, 10, 7), date(2024, 10, 8)):
        assert by_day[missing]["ref_price"] is None
    assert out.filter(pl.col("symbol") == "2330.TWSE")["ref_price"].null_count() == len(asked)


def test_default_sessions_come_from_the_daily_store(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _daily_store(tmp_path / "taiwan", [TPEX_DAY, date(2024, 10, 4)])
    store = TaiwanSessionBaseStore()
    store.write(TPEX, TPEX_DAY, _tpex())

    out = official_session_bases(["6488.TPEX"], [date(2024, 10, 4)])

    assert store.path(TPEX, TPEX_DAY).is_relative_to(tmp_path / "taiwan" / "session_bases" / "tpex")
    assert out["ref_price"].to_list() == [452.50]


def test_empty_requests_return_an_empty_frame(tmp_path):
    out = official_session_bases([], [TWSE_DAY], store=TaiwanSessionBaseStore(tmp_path), sessions=[])

    assert out.is_empty()
    assert out.columns == ["symbol", "trade_date", "ref_price", "limit_up", "limit_down"]


def test_an_unreadable_file_reads_as_missing(tmp_path):
    store = TaiwanSessionBaseStore(tmp_path)
    path = store.path(TWSE, TWSE_DAY)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not parquet")

    out = official_session_bases(["1435.TWSE"], [TWSE_DAY], store=store, sessions=[])

    assert out["ref_price"].to_list() == [None]


def test_daily_quotes_written_before_the_cutoff_on_their_day_are_provisional(tmp_path):
    store = TaiwanSessionBaseStore(tmp_path)
    store.write(TPEX, TPEX_DAY, _tpex())
    store.write(TWSE, TWSE_DAY, _twse())
    path = store.path(TPEX, TPEX_DAY)
    cutoff = datetime.combine(TPEX_DAY, daily_final_cutoff(), TAIPEI)

    _set_mtime(path, cutoff - timedelta(minutes=1))
    assert not store.is_final(TPEX, TPEX_DAY)
    _set_mtime(path, cutoff)
    assert store.is_final(TPEX, TPEX_DAY)
    _set_mtime(path, datetime.combine(TPEX_DAY + timedelta(days=1), datetime.min.time(), TAIPEI))
    assert store.is_final(TPEX, TPEX_DAY)

    _set_mtime(store.path(TWSE, TWSE_DAY), datetime.combine(TWSE_DAY, datetime.min.time(), TAIPEI))
    assert store.is_final(TWSE, TWSE_DAY)
    assert not store.is_final(TWSE, date(2025, 4, 9))
    assert store.stored_dates(TPEX) == [TPEX_DAY]


# --- Refresh and CLI ------------------------------------------------------------------------


class _Exchange:
    """Answers each URL with its payload, or raises its exception, and records the URLs asked."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def __call__(self, url: str):
        self.asked.append(url)
        answer = self.answers[url]
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer)


def _codes(exchange: str, _day: date) -> set[str]:
    return TWSE_CODES if exchange == "TWSE" else TPEX_CODES


def _twse_answers(days: list[date]) -> dict[str, object]:
    adapter, payload = TwseSessionBaseAdapter(), _load("twt84u_20250408_excerpt.json")
    return {adapter.build_url(d): _restamped(payload, d) for d in days}


def _tpex_answers(days: list[date]) -> dict[str, object]:
    adapter, payload = TpexSessionBaseAdapter(), _load("tpex_dq_20241001_excerpt.json")
    return {adapter.build_url(d): _restamped(payload, d) for d in days}


def _service(tmp_path, sessions, answers, sleeps=None):
    exchange = _Exchange(answers)
    service = TaiwanSessionBaseRefreshService(
        store=TaiwanSessionBaseStore(tmp_path / "session_bases"),
        daily_store=_daily_store(tmp_path, sessions),
        fetch_json=exchange,
        codes=_codes,
        sleep=(sleeps.append if sleeps is not None else lambda _s: None),
    )
    return service, exchange


def test_refresh_fetches_both_tables_for_the_stored_sessions_only(tmp_path):
    sessions = [date(2024, 9, 30), TPEX_DAY, date(2024, 10, 4)]
    sleeps: list[float] = []
    service, exchange = _service(
        tmp_path, sessions, {**_twse_answers(sessions), **_tpex_answers(sessions)}, sleeps
    )

    stats = service.refresh(TPEX_DAY, date(2024, 10, 8), delay=3.0)

    assert (stats["sessions"], stats["requests"], stats["written"], stats["skipped"]) == (2, 4, 4, 0)
    assert (stats["failed"], stats["stopped_at"]) == ([], None)
    assert sleeps == [3.0, 3.0, 3.0]
    assert len(exchange.asked) == 4
    store = TaiwanSessionBaseStore(tmp_path / "session_bases")
    assert store.stored_dates(TWSE) == store.stored_dates(TPEX) == [TPEX_DAY, date(2024, 10, 4)]


def test_refresh_skips_final_days_and_fetches_provisional_ones_again(tmp_path):
    sessions = [date(2024, 9, 30), TPEX_DAY]
    service, exchange = _service(tmp_path, sessions, {**_twse_answers(sessions), **_tpex_answers(sessions)})
    assert service.refresh(sessions[0], sessions[-1])["written"] == 4
    store = TaiwanSessionBaseStore(tmp_path / "session_bases")
    _set_mtime(store.path(TPEX, TPEX_DAY), datetime.combine(TPEX_DAY, daily_final_cutoff(), TAIPEI) - timedelta(hours=2))
    _set_mtime(store.path(TPEX, sessions[0]), datetime.combine(TPEX_DAY, daily_final_cutoff(), TAIPEI))
    exchange.asked.clear()

    stats = service.refresh(sessions[0], sessions[-1])

    assert (stats["requests"], stats["written"], stats["skipped"]) == (1, 1, 3)
    assert exchange.asked == [TpexSessionBaseAdapter().build_url(TPEX_DAY)]
    assert store.is_final(TPEX, TPEX_DAY)


def test_refresh_reports_a_failed_day_and_goes_on(tmp_path):
    sessions = [date(2024, 9, 30), TPEX_DAY, date(2024, 10, 4)]
    answers = _twse_answers(sessions)
    answers[TwseSessionBaseAdapter().build_url(TPEX_DAY)] = _load("twt84u_20250408_excerpt.json")
    service, _ = _service(tmp_path, sessions, answers)

    stats = service.refresh(sessions[0], sessions[-1], sources=[TWSE])

    assert (stats["requests"], stats["written"], stats["stopped_at"]) == (3, 2, None)
    assert [(f["source"], f["date"]) for f in stats["failed"]] == [("twse:twt84u", "2024-10-01")]
    assert TaiwanSessionBaseStore(tmp_path / "session_bases").stored_dates(TWSE) == [sessions[0], sessions[2]]


def test_refresh_stops_after_consecutive_failures(tmp_path):
    sessions = [date(2024, 9, 23) + timedelta(days=i) for i in range(5)]
    answers = {url: ValueError("boom") for url in _tpex_answers(sessions)}
    service, exchange = _service(tmp_path, sessions, answers)

    stats = service.refresh(sessions[0], sessions[-1], sources=[TPEX])

    assert len(exchange.asked) == session_bases.MAX_CONSECUTIVE_FAILURES
    assert stats["stopped_at"] == {"source": "tpex:dailyquotes", "date": "2024-09-25", "reason": "consecutive_failures"}
    assert stats["not_attempted"] == 2
    assert [f["date"] for f in stats["failed"]] == ["2024-09-23", "2024-09-24", "2024-09-25"]


def test_refresh_stops_at_once_when_throttled(tmp_path):
    sessions = [date(2024, 9, 30), TPEX_DAY, date(2024, 10, 4)]
    answers = {**_twse_answers(sessions), **_tpex_answers(sessions)}
    answers[TpexSessionBaseAdapter().build_url(TPEX_DAY)] = ExchangeThrottledError("HTTP 307")
    service, exchange = _service(tmp_path, sessions, answers)

    stats = service.refresh(sessions[0], sessions[-1])

    assert stats["stopped_at"] == {"source": "tpex:dailyquotes", "date": "2024-10-01", "reason": "throttled"}
    assert (stats["written"], stats["not_attempted"], len(exchange.asked)) == (3, 2, 4)


def test_refresh_rejects_an_unknown_source_or_reversed_range(tmp_path):
    service, _ = _service(tmp_path, [TPEX_DAY], {})

    with pytest.raises(ValueError, match="unknown"):
        service.refresh(TPEX_DAY, TPEX_DAY, sources=["otc"])
    with pytest.raises(ValueError, match="before"):
        service.refresh(TPEX_DAY, TPEX_DAY - timedelta(days=1))


def test_listed_codes_join_the_master_with_the_stored_bars_of_the_day(tmp_path):
    daily = _daily_store(tmp_path, [TWSE_DAY], symbols=("1435.TWSE", "6488.TPEX"))
    codes = ListedCodes(daily)

    twse = codes("TWSE", TWSE_DAY)
    assert {"1435", "2330", "0050"} <= twse
    assert "6488" not in twse and "1435" not in codes("TWSE", date(2025, 4, 9))
    assert "6488" in codes("TPEX", TWSE_DAY)


def _run_cli(monkeypatch, tmp_path, argv, answers, sessions):
    service, exchange = _service(tmp_path, sessions, answers)
    monkeypatch.setattr(session_bases, "TaiwanSessionBaseRefreshService", lambda: service)
    session_bases.main(argv)
    return exchange


def test_cli_prints_the_stats_and_exits_zero_on_success(monkeypatch, tmp_path, capsys):
    sessions = [TPEX_DAY]
    _run_cli(
        monkeypatch, tmp_path,
        ["--since", "2024-10-01", "--until", "2024-10-01", "--source", "tpex", "--delay", "0"],
        _tpex_answers(sessions), sessions,
    )

    stats = json.loads(capsys.readouterr().out)
    assert (stats["sources"], stats["written"], stats["failed"]) == (["tpex"], 1, [])


def test_cli_lists_the_failed_dates_and_exits_non_zero(monkeypatch, tmp_path, capsys):
    sessions = [date(2024, 9, 30), TPEX_DAY]
    answers = _twse_answers(sessions)
    answers[TwseSessionBaseAdapter().build_url(TPEX_DAY)] = ExchangeThrottledError("HTTP 428")

    with pytest.raises(SystemExit) as exited:
        _run_cli(
            monkeypatch, tmp_path,
            ["--since", "2024-09-30", "--until", "2024-10-01", "--source", "twse", "--delay", "0"],
            answers, sessions,
        )

    assert exited.value.code == 1
    stats = json.loads(capsys.readouterr().out)
    assert [f["date"] for f in stats["failed"]] == ["2024-10-01"]
    assert stats["stopped_at"]["reason"] == "throttled"


def test_cli_rejects_a_negative_delay(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as exited:
        _run_cli(monkeypatch, tmp_path, ["--since", "2024-10-01", "--delay", "-1"], {}, [TPEX_DAY])

    assert exited.value.code == 2
