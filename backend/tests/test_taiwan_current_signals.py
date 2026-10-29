from __future__ import annotations

from types import SimpleNamespace

import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.api import screener as screener_api
from app.main import app
from app.services import strategy_cache
from app.strategy import custom_signals
from app.taiwan import current_signals
from app.taiwan.current_signals import BUILTIN_SIGNAL_COLUMNS, TaiwanCurrentSignalsService
from app.taiwan.volume_patterns import VOLUME_PATTERN_COLUMNS

SYMBOL = "2330.TWSE"
OTHER = "2317.TWSE"


class _Master:
    def __init__(self, instrument_type="stock"):
        self.instrument_type = instrument_type

    def get_instrument(self, symbol):
        if self.instrument_type is None:
            return None
        return SimpleNamespace(symbol=symbol, instrument_type=self.instrument_type, security_class=None)


class _Monitor:
    def __init__(self, results=None):
        self.results = results or {}

    def latest_strategy_results(self):
        return self.results


def _state(tmp_path, realtime=None):
    return SimpleNamespace(
        repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
        monitor_engine=_Monitor(realtime),
        strategy_engine=None,
    )


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return tmp_path


def _write_partition(data_dir, day, rows, flag_columns):
    """One partition holding close/ma5 plus *flag_columns*; a flag a row omits is null."""
    frame = pl.DataFrame(
        {
            "symbol": [r["symbol"] for r in rows],
            "close": [100.0] * len(rows),
            "ma5": [99.0] * len(rows),
            **{c: pl.Series(c, [r.get(c) for r in rows], dtype=pl.Boolean) for c in flag_columns},
        }
    )
    path = data_dir / "kline_daily_enriched" / f"date={day}" / "part.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)


def _custom(data_dir, signal_id, name, enabled=True):
    custom_signals.save_one(
        data_dir,
        {"id": signal_id, "name": name, "enabled": enabled,
         "conditions": [{"left": "close", "op": ">", "right": 1}]},
    )


ALL_FLAGS = [*BUILTIN_SIGNAL_COLUMNS, *VOLUME_PATTERN_COLUMNS]


def _ids(items):
    return [item.id for item in items]


# --- signals ---------------------------------------------------------------------------------


def test_only_true_flags_are_listed_and_a_missing_column_is_not_evaluated(data_dir):
    _custom(data_dir, "mine", "我的訊號")
    _custom(data_dir, "fresh", "剛新增")
    _custom(data_dir, "off", "停用", enabled=False)
    row = {
        "symbol": SYMBOL,
        "signal_macd_golden": True,
        "signal_ma_dead_5_20": False,
        "signal_n_day_low": None,
        "signal_limit_down": True,
        "vp_double": True,
        "vp_low": None,
        "csg_mine": True,
        "csg_off": True,
    }
    _write_partition(data_dir, "2026-10-28", [row, {"symbol": OTHER, "signal_ma_dead_5_20": True}],
                     [*ALL_FLAGS, "csg_mine", "csg_off"])

    section = current_signals.read_signals(SYMBOL, data_dir)

    assert section.status == "ok" and section.as_of == "2026-10-28"
    assert _ids(section.triggered) == ["signal_macd_golden", "signal_limit_down", "vp_double", "csg_mine"]
    assert [(i.source, i.direction) for i in section.triggered] == [
        ("builtin", "bullish"), ("builtin", "bearish"), ("volume_pattern", "neutral"), ("custom", "neutral"),
    ]
    assert section.triggered[-1].name == "我的訊號"
    assert section.triggered[0].name is None
    assert _ids(section.not_evaluated) == ["csg_fresh"]
    assert section.not_evaluated[0].name == "剛新增"


def test_a_partition_published_before_the_volume_patterns_reports_them_not_evaluated(data_dir):
    _write_partition(data_dir, "2026-10-28", [{"symbol": SYMBOL, "signal_volume_surge": True}],
                     list(BUILTIN_SIGNAL_COLUMNS))

    section = current_signals.read_signals(SYMBOL, data_dir)

    assert _ids(section.triggered) == ["signal_volume_surge"]
    assert _ids(section.not_evaluated) == list(VOLUME_PATTERN_COLUMNS)


def test_a_newer_partition_without_published_indicators_is_skipped(data_dir):
    _write_partition(data_dir, "2026-10-27", [{"symbol": SYMBOL, "signal_n_day_high": True}], ALL_FLAGS)
    live = data_dir / "kline_daily_enriched" / "date=2026-10-28" / "part.parquet"
    live.parent.mkdir(parents=True)
    pl.DataFrame({"symbol": [SYMBOL], "close": [101.0]}).write_parquet(live)

    section = current_signals.read_signals(SYMBOL, data_dir)

    assert section.as_of == "2026-10-27"
    assert _ids(section.triggered) == ["signal_n_day_high"]


def test_a_symbol_absent_from_the_partition_and_an_unpublished_table(data_dir):
    assert current_signals.read_signals(SYMBOL, data_dir).status == "unpublished"

    _write_partition(data_dir, "2026-10-28", [{"symbol": OTHER, "signal_macd_golden": True}], ALL_FLAGS)
    section = current_signals.read_signals(SYMBOL, data_dir)
    assert section.status == "no_row" and section.as_of == "2026-10-28"
    assert section.triggered == [] and section.not_evaluated == []


def test_directions_cover_every_flag_once():
    bullish, bearish = current_signals._BULLISH, current_signals._BEARISH
    assert not bullish & bearish
    assert (bullish | bearish) <= set(ALL_FLAGS)
    assert current_signals.signal_direction("signal_volume_surge") == "neutral"


# --- strategy selections ---------------------------------------------------------------------


def _picks(symbols, as_of):
    return {"as_of": as_of, "total": len(symbols),
            "rows": [{"symbol": s, "score": float(10 - i)} for i, s in enumerate(symbols)]}


def test_selections_use_the_same_overlaid_payload_and_dates_as_screener_cached(tmp_path, monkeypatch):
    strategy_cache.write_cache(
        tmp_path, "2026-10-28",
        {"post": _picks([OTHER, SYMBOL], "2026-10-28"), "live": _picks([OTHER], "2026-10-28"),
         "absent": _picks([OTHER], "2026-10-28")},
    )
    realtime = {"live": _picks([SYMBOL], "2026-10-29")}
    state = _state(tmp_path, realtime)
    monkeypatch.setattr(screener_api, "_attach_instrument_names", lambda _repo, payload: payload)

    section = current_signals.read_strategy_selections(SYMBOL, state)
    cached = screener_api.get_cached(SimpleNamespace(app=SimpleNamespace(state=state)), ext_columns=None)

    assert section.status == "ok" and section.as_of == cached["as_of"] == "2026-10-28"
    by_id = {s.strategy_id: s for s in section.selections}
    assert set(by_id) == {"post", "live"}
    for sid, selection in by_id.items():
        assert selection.as_of == cached["results"][sid]["as_of"]
        row = next(r for r in cached["results"][sid]["rows"] if r["symbol"] == SYMBOL)
        assert (selection.grade, selection.grade_reason) == (row["grade"], row["grade_reason"])
    assert by_id["live"].as_of == "2026-10-29"
    assert (by_id["post"].rank, by_id["post"].total, by_id["post"].score) == (2, 2, 9.0)
    assert section.grading == cached["grading"]
    assert section.market_state == cached["market_state"]


def test_without_a_strategy_cache_the_section_says_so(tmp_path):
    section = current_signals.read_strategy_selections(SYMBOL, _state(tmp_path))
    assert section.status == "no_cache" and section.selections == []


# --- API -------------------------------------------------------------------------------------


URL = "/api/taiwan/stocks/{}/current-signals"


@pytest.fixture
def client():
    return TestClient(app, client=("127.0.0.1", 50000))


def _serve(monkeypatch, state, master=None):
    monkeypatch.setattr(
        "app.api.taiwan.get_taiwan_current_signals_service",
        lambda _app_state: TaiwanCurrentSignalsService(state, master or _Master()),
    )


def test_api_returns_signals_and_selections_with_their_own_dates(data_dir, monkeypatch, client):
    _write_partition(data_dir, "2026-10-28", [{"symbol": SYMBOL, "signal_ma20_breakout": True}], ALL_FLAGS)
    strategy_cache.write_cache(data_dir, "2026-10-28", {"s": _picks([SYMBOL], "2026-10-28")})
    _serve(monkeypatch, _state(data_dir, {"s": _picks([SYMBOL], "2026-10-29")}))

    response = client.get(URL.format(SYMBOL))

    assert response.status_code == 200
    payload = response.json()
    assert payload["applicable"] is True and payload["not_applicable_reason"] is None
    assert payload["signals"]["as_of"] == "2026-10-28"
    assert payload["signals"]["triggered"] == [
        {"id": "signal_ma20_breakout", "source": "builtin", "direction": "bullish", "name": None}
    ]
    strategies = payload["strategies"]
    assert strategies["status"] == "ok" and strategies["as_of"] == "2026-10-28"
    assert strategies["grading"] == {"top_n": 5, "market_filter": "unavailable"}
    [selection] = strategies["selections"]
    assert selection["as_of"] == "2026-10-29" and selection["grade"] == "buy"


def test_api_reports_no_data(data_dir, monkeypatch, client):
    _serve(monkeypatch, _state(data_dir))
    payload = client.get(URL.format(SYMBOL)).json()
    assert payload["signals"] == {"status": "unpublished", "as_of": None, "triggered": [], "not_evaluated": []}
    assert payload["strategies"]["status"] == "no_cache"


def test_api_serves_an_etf_like_a_stock(data_dir, monkeypatch, client):
    _write_partition(data_dir, "2026-10-28", [{"symbol": "0050.TWSE", "vp_step_up": True}], ALL_FLAGS)
    _serve(monkeypatch, _state(data_dir), _Master("etf"))
    payload = client.get(URL.format("0050.TWSE")).json()
    assert payload["applicable"] is True
    assert [i["id"] for i in payload["signals"]["triggered"]] == ["vp_step_up"]


def test_api_marks_an_index_not_applicable_without_reading(data_dir, monkeypatch, client):
    def unreadable(*_args, **_kwargs):
        raise AssertionError("an index must not read the enriched table")

    monkeypatch.setattr(current_signals, "read_latest_enriched", unreadable)
    _serve(monkeypatch, _state(data_dir), _Master(None))
    payload = client.get(URL.format("t00.TWSE")).json()
    assert payload["applicable"] is False and payload["not_applicable_reason"] == "index"
    assert payload["signals"] is None and payload["strategies"] is None


def test_api_errors(data_dir, monkeypatch, client):
    def broken(*_args, **_kwargs):
        raise OSError(f"cannot open {data_dir / 'kline_daily_enriched' / 'part.parquet'}")

    monkeypatch.setattr(current_signals, "read_latest_enriched", broken)
    _serve(monkeypatch, _state(data_dir))
    failed = client.get(URL.format(SYMBOL))
    assert failed.status_code == 500
    assert failed.json()["detail"] == "目前訊號讀取失敗"
    assert str(data_dir) not in failed.text and "parquet" not in failed.text.lower()

    _serve(monkeypatch, _state(data_dir), _Master(None))
    assert client.get(URL.format(SYMBOL)).status_code == 404
    assert client.get(URL.format("not-a-symbol")).status_code == 400
