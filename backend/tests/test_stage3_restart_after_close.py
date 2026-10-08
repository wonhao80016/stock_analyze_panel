"""A process that (re)starts after the close must not redo the day's close from memory it lost.

The 13:30 minute bar, the published official rows and the one closing monitor pass were all settled
by the process that watched the session; a restart only sees the closing-auction quote again.
"""

from __future__ import annotations

from datetime import datetime

import polars as pl
import pytest

from app.taiwan.realtime import minute_recorder
from app.taiwan.realtime.calendar import TAIPEI_TZ
from app.taiwan.realtime.minute_recorder import MinuteBarRecorder
from tests.test_stage3_closing_auction import (  # noqa: F401
    HON_HAI,
    HON_HAI_1324,
    closing_result,
    harness,
    hon_hai,
    merged,
    minute_bars,
)
from tests.test_stage3_session_model import (
    SESSION,
    TSMC,
    at,
    tsmc_closing_trial,
    tsmc_continuous_1324,
    wall_clock,
)

pytestmark = pytest.mark.usefixtures("deny_network")

CLOSE_KEY = (SESSION, "close")


def _quote(clock: tuple[int, int, int], cum: float, seen: tuple[int, int, int]) -> dict:
    quote_time = datetime(SESSION.year, SESSION.month, SESSION.day, *clock, tzinfo=TAIPEI_TZ)
    fetched = datetime(SESSION.year, SESSION.month, SESSION.day, *seen, tzinfo=TAIPEI_TZ)
    return {
        "symbol": TSMC,
        "close": 2550.0,
        "volume": cum,
        "quote_ts": int(quote_time.timestamp() * 1000),
        "retrieved_at": fetched.isoformat(),
        "status": "close_final",
    }


def _bar_at(tmp_path, hour: int, minute: int) -> dict:
    bars = [
        b
        for b in minute_bars(tmp_path)
        if b["datetime"].astimezone(TAIPEI_TZ).strftime("%H:%M") == f"{hour:02d}:{minute:02d}"
    ]
    assert len(bars) == 1
    return bars[0]


def test_a_restart_after_the_close_keeps_the_1330_bar(tmp_path):
    session = MinuteBarRecorder(tmp_path)
    session.record([_quote((13, 24, 50), 14_000_000, (13, 24, 55))])
    session.record([_quote((13, 24, 58), 14_100_000, (13, 25, 1))])
    session.record([_quote((13, 30, 0), 20_154_000, (13, 30, 6))])
    session.flush()
    assert _bar_at(tmp_path, 13, 30)["volume"] == 6_054_000.0

    restarted = MinuteBarRecorder(tmp_path)
    restarted.record([_quote((13, 30, 0), 20_154_000, (20, 0, 0))])
    restarted.record([_quote((13, 30, 0), 20_154_000, (20, 0, 6))])
    restarted.flush()

    assert _bar_at(tmp_path, 13, 30)["volume"] == 6_054_000.0


def test_a_restart_mid_session_keeps_the_written_bars_and_the_day_total(tmp_path):
    session = MinuteBarRecorder(tmp_path)
    session.record([_quote((10, 0, 0), 1_000, (10, 0, 2))])
    session.record([_quote((10, 0, 30), 1_500, (10, 0, 32))])
    session.record([_quote((10, 1, 0), 2_000, (10, 1, 2))])

    restarted = MinuteBarRecorder(tmp_path)
    restarted.record([_quote((10, 0, 50), 2_000, (10, 1, 5))])
    restarted.record([_quote((10, 5, 0), 5_000, (10, 5, 2))])
    restarted.flush()

    assert _bar_at(tmp_path, 10, 0)["volume"] == 500.0
    bars = minute_bars(tmp_path)
    recorded = sum(b["volume"] + (b["unattributed_volume"] or 0.0) for b in bars)
    assert recorded == 5_000.0


def test_the_first_snapshot_counts_the_unseen_volume_as_unattributed(tmp_path):
    recorder = MinuteBarRecorder(tmp_path)
    recorder.record([_quote((11, 0, 0), 500_000, (11, 0, 2))])
    recorder.record([_quote((11, 0, 20), 500_400, (11, 0, 22))])
    recorder.flush()

    bar = _bar_at(tmp_path, 11, 0)
    assert bar["volume"] == 400.0
    assert bar["unattributed_volume"] == 500_000.0


def _restart(harness, monkeypatch, *, official_stored: bool = False) -> None:  # noqa: F811
    svc = harness.svc
    repo, app_state = svc._repo, svc._app_state
    svc.__init__()
    svc._repo, svc._app_state = repo, app_state
    recorder = MinuteBarRecorder(harness.tmp_path)
    monkeypatch.setattr(minute_recorder, "get_minute_recorder", lambda: recorder)
    if official_stored:
        repo.date_has_official_stock_data = lambda d: d == SESSION


def _close_the_day(harness) -> None:  # noqa: F811
    harness.poll(at(13, 24, 59), tsmc_continuous_1324())
    harness.poll(at(13, 30, 5), closing_result())
    assert CLOSE_KEY in harness.svc._final_sync_done
    assert harness.evaluations == [1, 1]


def test_a_restart_after_the_close_does_not_redo_the_close(harness, monkeypatch):  # noqa: F811
    _close_the_day(harness)
    closing_bar = _bar_at(harness.tmp_path, 13, 30)

    _restart(harness, monkeypatch)
    harness.poll(at(20, 0, 0), closing_result())
    harness.poll(at(20, 0, 6), closing_result())

    assert harness.bars[-1]["close"].to_list() == [2550.0]
    assert harness.evaluations == [1, 1]
    assert CLOSE_KEY in harness.svc._final_sync_done
    assert _bar_at(harness.tmp_path, 13, 30) == closing_bar


def test_a_restart_after_the_publish_does_not_fetch_the_close_again(harness, monkeypatch):  # noqa: F811
    _close_the_day(harness)
    _restart(harness, monkeypatch, official_stored=True)
    harness.svc._closing_day = SESSION
    harness.svc._intraday_symbols_today = {TSMC}

    harness.poll(at(20, 0, 0), closing_result())

    assert CLOSE_KEY in harness.svc._final_sync_done
    assert harness.evaluations == [1, 1]


def test_a_refresh_after_the_publish_keeps_the_official_rows(harness, monkeypatch):  # noqa: F811
    from app.taiwan.realtime import service as service_module

    _close_the_day(harness)
    bars_before = len(harness.bars)
    harness.svc._repo.date_has_official_stock_data = lambda d: d == SESSION

    with wall_clock(at(17, 0, 0)), pytest.MonkeyPatch.context() as mp:
        from tests.test_stage3_session_model import realtime_service

        mp.setattr(
            service_module, "get_realtime_service", lambda: realtime_service(closing_result())
        )
        harness.svc.refresh()

    assert len(harness.bars) == bars_before
    assert harness.evaluations == [1, 1]


def test_a_symbol_dropped_from_the_pool_does_not_block_the_close(harness):  # noqa: F811
    harness.pool.append(HON_HAI)
    harness.poll(at(13, 24, 59), merged(tsmc_continuous_1324(), extra=(HON_HAI_1324,)))
    harness.pool.remove(HON_HAI)

    harness.poll(at(13, 30, 5), closing_result())

    assert CLOSE_KEY in harness.svc._final_sync_done
    assert harness.svc._final_sync_failed.get(CLOSE_KEY) is None


def test_closing_bar_survives_an_empty_rewrite(tmp_path):
    session = MinuteBarRecorder(tmp_path)
    session.record([_quote((13, 24, 58), 14_100_000, (13, 25, 1))])
    session.record([_quote((13, 30, 0), 20_154_000, (13, 30, 6))])
    session.flush()

    empty = pl.DataFrame(
        [
            {
                "symbol": TSMC,
                "datetime": datetime(2026, 10, 8, 13, 30, tzinfo=TAIPEI_TZ),
                "open": 2550.0,
                "high": 2550.0,
                "low": 2550.0,
                "close": 2550.0,
                "volume": 0.0,
                "amount": 0.0,
                "unattributed_volume": None,
            }
        ]
    )
    session._write_partition("stock", SESSION, empty)

    assert _bar_at(tmp_path, 13, 30)["volume"] == 6_054_000.0


def test_a_process_started_in_the_closing_auction_still_takes_the_close(harness):  # noqa: F811
    harness.poll(at(13, 28, 0), tsmc_closing_trial())
    harness.poll(at(13, 30, 5), closing_result())

    today_bar = harness.bars[-1].row(0, named=True)
    assert today_bar["date"] == SESSION
    assert today_bar["close"] == 2550.0
    assert _bar_at(harness.tmp_path, 13, 30)["close"] == 2550.0
    assert CLOSE_KEY in harness.svc._final_sync_done
    assert harness.evaluations == []


HALTED_AFTER_TRADING = hon_hai(
    t="11:02:00", z="249.0000", tv="2", v="20000", pz="-", ps="-", ts="0"
)


def test_a_symbol_halted_after_trading_does_not_block_the_close(harness):  # noqa: F811
    harness.pool.append(HON_HAI)
    harness.poll(at(13, 24, 59), merged(tsmc_continuous_1324(), extra=(HON_HAI_1324,)))
    harness.poll(at(13, 30, 5), merged(closing_result(), extra=(HALTED_AFTER_TRADING,)))

    assert CLOSE_KEY in harness.svc._final_sync_done
    assert harness.svc._final_sync_failed.get(CLOSE_KEY) is None
    assert harness.engine.evaluated[-1] == [TSMC]
