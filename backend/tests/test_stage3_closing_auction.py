"""The 13:30 closing-auction result must reach the live path and gate the close final sync.

Built on the real 2026-10-08 MIS response fetched at 14:56:59 (t=13:30:00, ts=0, z=2550, v=20154).
"""

from __future__ import annotations

import copy
from datetime import datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.services import quote_service as qs_module
from app.services.quote_service import QuoteService
from app.taiwan.realtime import service as service_module
from app.taiwan.realtime.calendar import TAIPEI_TZ
from app.taiwan.realtime.minute_recorder import MinuteBarRecorder
from app.taiwan.realtime.mis_provider import TwseMisRealtimeProvider
from app.taiwan.realtime.models import RealtimeStatus
from tests.test_stage3_session_model import (
    SESSION,
    TSMC,
    at,
    mis_sample,
    only,
    realtime_service,
    tsmc_closing_trial,
    tsmc_continuous_1324,
    wall_clock,
)

pytestmark = pytest.mark.usefixtures("deny_network")


def closing_result() -> dict:
    return only(mis_sample("145659.json"), "2330")


def test_closing_result_is_classified_close_final():
    with wall_clock(at(13, 30, 5)):
        quote = TwseMisRealtimeProvider().fetch_quotes([TSMC], mock_response_json=closing_result())[
            TSMC
        ]
    assert quote.source_meta.status == RealtimeStatus.CLOSE_FINAL.value
    assert quote.source_meta.is_realtime is True
    assert quote.source_meta.is_stale is False
    assert quote.last_price == 2550.0
    assert quote.volume == 20_154_000


def test_closing_result_takes_z_not_the_trial_price():
    """8069 closed at z=144 while pz still read 144.5."""
    with wall_clock(at(13, 30, 5)):
        quote = TwseMisRealtimeProvider().fetch_quotes(
            ["8069.TPEX"],
            mock_response_json=only(mis_sample("145659.json"), "8069"),
        )["8069.TPEX"]
    assert quote.last_price == 144.0
    assert quote.source_meta.status == RealtimeStatus.CLOSE_FINAL.value


def test_previous_session_close_is_not_close_final_the_next_day():
    with wall_clock(at(8, 45, 0, day=SESSION.replace(day=12))):
        quote = TwseMisRealtimeProvider().fetch_quotes([TSMC], mock_response_json=closing_result())[
            TSMC
        ]
    assert quote.source_meta.status != RealtimeStatus.CLOSE_FINAL.value
    assert quote.source_meta.is_realtime is False


def test_closing_result_stays_live_after_the_post_close_session():
    with wall_clock(at(14, 56, 59)):
        quote = realtime_service(closing_result()).get_quotes([TSMC])[TSMC]
    assert quote.source_meta.status == RealtimeStatus.CLOSE_FINAL.value
    assert quote.source_meta.is_stale is False


def test_volume_time_factor_is_one_for_the_closing_quote():
    from app.taiwan.live_enriched import _volume_time_factor

    assert _volume_time_factor(1791441000000) == 1.0


class FakeRepo:
    def get_index_symbol_set(self):
        return set()

    def get_etf_instruments(self):
        return pl.DataFrame()

    def resolve_asset_type(self, symbol):
        return "stock"


class FakeOfficial:
    """STOCK_DAY_ALL during the close: still the previous session."""

    def __init__(self) -> None:
        self.calls = 0

    def fetch_quote(self, symbols):
        self.calls += 1
        return pl.DataFrame(
            [
                {
                    "symbol": TSMC,
                    "date": SESSION.replace(day=7),
                    "open": 2570.0,
                    "high": 2600.0,
                    "low": 2565.0,
                    "close": 2585.0,
                    "volume": 25_000_000.0,
                    "amount": None,
                    "provider": "twse",
                    "source": "twse:stock_day_all",
                    "status": "official",
                }
            ]
        )


class FakeEngine:
    """Monitor engine stub recording which symbols each stock evaluation saw."""

    rule_count = 1

    def __init__(self) -> None:
        self.rules: dict = {}
        self.evaluated: list[list[str]] = []

    def set_name_map(self, name_map):
        pass

    def has_rule_type(self, rule_type):
        return False

    def has_asset_rules(self, asset_type):
        return False

    def evaluate(self, df, asset_type="stock", **kwargs):
        self.evaluated.append(sorted(df["symbol"].to_list()))
        return []

    def consume_strategy_result_updates(self):
        return False


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """A QuoteService polling 2330 through the real hybrid provider and MIS parser."""
    from app.data_providers import registry
    from app.services import preferences, watchlist
    from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
    from app.taiwan.realtime import minute_recorder

    provider = TaiwanHybridProvider()
    official = FakeOfficial()
    provider.official = official
    monkeypatch.setattr(registry, "get_provider", lambda name: provider)
    monkeypatch.setattr(preferences, "get_realtime_data_provider", lambda: "taiwan")
    pool = [TSMC]
    monkeypatch.setattr(watchlist, "list_symbols", lambda: [{"symbol": s} for s in pool])
    monkeypatch.setattr("app.services.quote_service._persist_last_fetch", lambda ms: None)
    recorder = MinuteBarRecorder(data_dir=tmp_path)
    monkeypatch.setattr(minute_recorder, "get_minute_recorder", lambda: recorder)

    svc = QuoteService()
    svc._repo = FakeRepo()
    bars: list[pl.DataFrame] = []
    svc._flush_live_enriched = lambda daily_df, *a, **k: bars.append(daily_df)
    evaluations: list[int] = []

    def enriched_today():
        evaluations.append(1)
        return pl.DataFrame({"symbol": list(pool)}), qs_module.cn_today()

    svc.get_enriched_today = enriched_today
    engine = FakeEngine()
    svc._app_state = SimpleNamespace(monitor_engine=engine, repo=svc._repo)
    svc._inject_intraday_signals = lambda df, *a: df

    class Harness:
        pass

    h = Harness()
    h.svc, h.bars, h.official, h.evaluations, h.tmp_path = (
        svc,
        bars,
        official,
        evaluations,
        tmp_path,
    )
    h.pool, h.engine = pool, engine

    def poll(now: datetime, payload: dict) -> None:
        with wall_clock(now), pytest.MonkeyPatch.context() as mp:
            mp.setattr(service_module, "get_realtime_service", lambda: realtime_service(payload))
            # A round counts as fetched when _fetched_at advances; the Windows clock can repeat.
            svc._fetched_at = 0.0
            run_one_poll(svc)

    h.poll = poll
    return h


def run_one_poll(svc: QuoteService) -> None:
    """Drive exactly one iteration of the real poll loop."""
    svc._running = True
    svc._enabled = True
    svc._interval = 0.0
    should_fetch = QuoteService._should_fetch_for_phase

    def last_iteration(phase):
        svc._running = False
        return should_fetch(svc, phase)

    svc._should_fetch_for_phase = last_iteration
    try:
        svc._poll_loop()
    finally:
        del svc._should_fetch_for_phase


def minute_bars(tmp_path) -> list[dict]:
    target = tmp_path / "kline_minute" / f"date={SESSION.isoformat()}" / "part.parquet"
    if not target.exists():
        return []
    return pl.read_parquet(target).sort("datetime").to_dicts()


def test_closing_result_completes_the_close(harness):
    harness.poll(at(13, 24, 59), tsmc_continuous_1324())
    harness.poll(at(13, 27, 0), tsmc_continuous_1324())
    assert harness.evaluations == [1]

    harness.poll(at(13, 30, 5), closing_result())

    today_bar = harness.bars[-1].row(0, named=True)
    assert today_bar["date"] == SESSION
    assert today_bar["close"] == 2550.0
    assert today_bar["volume"] == 20_154_000.0
    assert harness.official.calls == 0

    key = (SESSION, "close")
    assert key in harness.svc._final_sync_done
    assert harness.svc._final_sync_failed.get(key) is None
    assert harness.evaluations == [1, 1]

    closing_bar = [
        b for b in minute_bars(harness.tmp_path) if b["datetime"].astimezone(TAIPEI_TZ).minute == 30
    ]
    assert len(closing_bar) == 1
    assert closing_bar[0]["close"] == 2550.0
    assert closing_bar[0]["volume"] == 6_030_000.0


def test_fallback_alone_never_completes_the_close(harness):
    harness.poll(at(13, 24, 59), tsmc_continuous_1324())
    harness.poll(at(13, 30, 5), tsmc_closing_trial())

    key = (SESSION, "close")
    assert harness.official.calls == 1
    assert key not in harness.svc._final_sync_done
    assert harness.svc._final_sync_failed.get(key) is None
    with wall_clock(at(13, 30, 11)):
        assert harness.svc._should_poll_for_phase(QuoteService._market_phase()) is True

    harness.poll(at(14, 0, 0), tsmc_closing_trial())
    assert key not in harness.svc._final_sync_done

    harness.poll(at(14, 30, 6), tsmc_closing_trial())
    assert key not in harness.svc._final_sync_done
    assert harness.svc._final_sync_failed.get(key) == "closing_quote_missing"
    with wall_clock(at(14, 30, 12)):
        assert harness.svc._should_poll_for_phase(QuoteService._market_phase()) is False
    assert harness.evaluations == [1]


HON_HAI = "2317.TWSE"


def hon_hai(**fields) -> dict:
    item = copy.deepcopy(only(mis_sample("145659.json"), "2317")["msgArray"][0])
    item.update(fields)
    for key in ("ov", "oz", "ot", "fv"):
        item.pop(key, None)
    return item


def merged(*payloads: dict, extra: tuple[dict, ...] = ()) -> dict:
    return {"msgArray": [m for p in payloads for m in p["msgArray"]] + list(extra)}


HALTED = hon_hai(t="09:00:00", z="-", tv="-", v="0", pz="-", ps="-", ts="0", o="-", h="-", l="-")
UNTRADED_WITH_PRICE = hon_hai(t="13:24:50", z="252.0000", tv="-", v="0", pz="-", ps="-", ts="0")
HON_HAI_1324 = hon_hai(t="13:24:57", z="249.0000", tv="3", v="33159", pz="-", ps="-", ts="0")
HON_HAI_TRIAL = hon_hai(t="13:26:57", z="-", tv="-", v="33159", pz="249.0000", ps="5000", ts="1")


@pytest.mark.parametrize("idle", [HALTED, UNTRADED_WITH_PRICE], ids=["halted", "untraded"])
def test_symbol_without_trades_does_not_block_the_close(harness, idle):
    harness.pool.append(HON_HAI)
    harness.poll(at(13, 24, 59), merged(tsmc_continuous_1324(), extra=(idle,)))
    harness.poll(at(13, 30, 5), merged(closing_result(), extra=(idle,)))

    key = (SESSION, "close")
    assert key in harness.svc._final_sync_done
    assert harness.svc._final_sync_failed.get(key) is None
    assert harness.engine.evaluated[-1] == [TSMC]


def test_partial_close_fails_but_still_evaluates_the_closed_symbols(harness):
    harness.pool.append(HON_HAI)
    harness.poll(at(13, 24, 59), merged(tsmc_continuous_1324(), extra=(HON_HAI_1324,)))
    assert harness.engine.evaluated == [[HON_HAI, TSMC]]

    harness.poll(at(13, 30, 5), merged(closing_result(), extra=(HON_HAI_TRIAL,)))
    key = (SESSION, "close")
    assert key not in harness.svc._final_sync_done
    assert harness.svc._final_sync_failed.get(key) is None
    assert harness.engine.evaluated == [[HON_HAI, TSMC]]

    harness.poll(at(14, 30, 6), merged(closing_result(), extra=(HON_HAI_TRIAL,)))
    assert key not in harness.svc._final_sync_done
    assert harness.svc._final_sync_failed.get(key) == "closing_quote_missing"
    assert harness.engine.evaluated == [[HON_HAI, TSMC], [TSMC]]

    harness.poll(at(14, 30, 12), merged(closing_result(), extra=(HON_HAI_TRIAL,)))
    assert harness.engine.evaluated == [[HON_HAI, TSMC], [TSMC]]
