"""Corporate actions no official table lists are inferred from the official session bases.

The two cases are the ones the 2024-01-02 onward scan of the stored bases found: 8101.TWSE last
traded on 2024-08-21 at 1.90 (TWT84U base 2.11) and resumed on 2024-11-19 with TWT84U base 9.50
after a loss-offset reduction TWTAUU does not list; 910322.TWSE closed at 19.55 on 2024-06-11
(last bid 19.50, ask 19.70) and went ex-dividend on 2024-06-12 with base 18.30, which TWT49U
does not list.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.taiwan import corporate_actions
from app.taiwan.corporate_actions import (
    INFERRED_SOURCE,
    INFERRED_STATUS,
    TaiwanCorporateActionRefreshService,
    TaiwanCorporateActionStore,
    adjustment_factors,
    corporate_action_schema,
    infer_actions,
    inference_lookups,
    reference_prices,
)
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import publish_enriched
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.enrichment.corporate_actions import EXDIV, INFERRED, REDUCTION
from app.taiwan.providers.taiwan_values import TAIPEI
from app.taiwan.session_bases import (
    TWSE,
    TaiwanSessionBaseStore,
    official_bases_of,
    session_base_schema,
)
from app.taiwan.share_capital_store import TaiwanShareCapitalStore

pytestmark = pytest.mark.usefixtures("deny_network")

KY = "910322.TWSE"
HUAGUAN = "8101.TWSE"
PLAIN = "2330.TWSE"
EX_DAY = date(2024, 6, 12)
RESUMED = date(2024, 11, 19)
SESSIONS = [
    date(2024, 6, 7), date(2024, 6, 11), EX_DAY, date(2024, 6, 13),
    date(2024, 8, 20), date(2024, 8, 21), date(2024, 8, 22), date(2024, 11, 18), RESUMED,
]
RETRIEVED = datetime(2024, 11, 19, 16, 30, tzinfo=TAIPEI)


def _bar(symbol: str, day: date, close: float, volume: float = 1000.0) -> dict:
    return {"symbol": symbol, "date": day, "open": close, "high": close, "low": close, "close": close,
            "volume": volume, "amount": close * volume, "quote_ts": None}


def _base(symbol: str, day: date, ref: float, up: float | None, down: float | None,
          bid: float | None = None, ask: float | None = None) -> dict:
    return {"symbol": symbol, "source_date": day, "ref_price": ref, "limit_up": up, "limit_down": down,
            "prev_close": None, "last_bid": bid, "last_ask": ask, "source": "twse:twt84u",
            "source_url": "https://example.invalid", "retrieved_at": RETRIEVED}


CASE_BARS = [
    _bar(KY, date(2024, 6, 7), 20.0), _bar(KY, date(2024, 6, 11), 19.55),
    _bar(KY, EX_DAY, 18.65), _bar(KY, date(2024, 6, 13), 19.2),
    _bar(HUAGUAN, date(2024, 8, 20), 2.11), _bar(HUAGUAN, date(2024, 8, 21), 1.90),
    _bar(HUAGUAN, RESUMED, 10.45, volume=64874.0),
    *[_bar(PLAIN, d, 100.0) for d in SESSIONS],
]
CASE_BASES = [
    _base(KY, date(2024, 6, 11), 20.0, 22.0, 18.0, 20.0, 20.1),
    _base(KY, EX_DAY, 18.3, 20.15, 16.5, 19.5, 19.7),
    _base(KY, date(2024, 6, 13), 18.65, 20.5, 16.8, 18.65, 18.85),
    _base(HUAGUAN, date(2024, 8, 21), 2.11, 2.32, 1.9, None, 2.11),
    _base(HUAGUAN, RESUMED, 9.5, 10.45, 8.55),
    *[_base(PLAIN, d, 100.0, 110.0, 90.0, 100.0, 100.5) for d in SESSIONS],
]


def _stores(root, bars: list[dict] = CASE_BARS, bases: list[dict] = CASE_BASES):
    daily = TaiwanDailyStore(root / "daily")
    daily.write_batch(pl.DataFrame(bars, schema_overrides={"quote_ts": pl.Int64}))
    session_bases = TaiwanSessionBaseStore(root / "session_bases")
    frame = pl.DataFrame(bases, schema=session_base_schema())
    for (day,), part in frame.partition_by("source_date", as_dict=True).items():
        session_bases.write(TWSE, day, part)
    return daily, session_bases, TaiwanCorporateActionStore(root / "corporate_actions")


def _service(events: TaiwanCorporateActionStore) -> TaiwanCorporateActionRefreshService:
    return TaiwanCorporateActionRefreshService(store=events, adapters=[], fetch_json=MagicMock())


def _official(symbol: str, day: date, kind: str, pre: float, ref: float) -> dict:
    return {**dict.fromkeys(corporate_action_schema()), "symbol": symbol, "date": day, "kind": kind,
            "pre_close": pre, "ref_price": ref, "limit_up": ref * 1.1, "limit_down": ref * 0.9,
            "ex_factor": pre / ref, "source": "twse:twt49u", "trade_date": day, "status": "official",
            "retrieved_at": RETRIEVED}


def _events(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(list(rows), schema=corporate_action_schema())


def _infer(bars: list[dict], bases: list[dict], events: pl.DataFrame | None = None,
           start: date = SESSIONS[0], end: date = SESSIONS[-1]) -> pl.DataFrame:
    daily = pl.DataFrame(bars, schema_overrides={"quote_ts": pl.Int64})
    sessions = sorted({b["date"] for b in bars})
    pairs = inference_lookups(daily, sessions, start, end)
    frame = pl.DataFrame(bases, schema=session_base_schema())
    found = pairs.join(
        frame.select("symbol", pl.col("source_date").alias("trade_date"), "ref_price", "limit_up", "limit_down",
                     "last_bid", "last_ask"),
        on=["symbol", "trade_date"], how="left",
    )
    return infer_actions(daily, found, events if events is not None else _events(), start, end,
                         retrieved_at=RETRIEVED)


# --- The two missed events -------------------------------------------------------------------


def test_the_reduction_and_the_dividend_no_table_lists_are_inferred(tmp_path):
    daily, bases, events = _stores(tmp_path)

    stats = _service(events).infer_range(SESSIONS[0], RESUMED, daily_store=daily, session_bases=bases)

    stored = events.read().sort("date")
    assert stored.select("symbol", "date", "kind", "pre_close", "ref_price", "limit_up", "limit_down").rows() == [
        (KY, EX_DAY, INFERRED, 19.55, 18.3, 20.15, 16.5),
        (HUAGUAN, RESUMED, INFERRED, 1.90, 9.5, 10.45, 8.55),
    ]
    assert set(stored["source"]) == {INFERRED_SOURCE}
    assert set(stored["status"]) == {INFERRED_STATUS}
    assert stored["provider"].to_list() == ["twse", "twse"]
    assert stored["open_ref"].to_list() == [18.3, 9.5]
    assert (stats["inserted"], stats["stored"]) == (2, True)
    assert [(e["symbol"], e["presumed_kind"]) for e in stats["inferred"]] == [(KY, EXDIV), (HUAGUAN, REDUCTION)]


def test_the_inferred_reduction_restates_volume_and_the_inferred_dividend_does_not(tmp_path):
    daily, bases, events = _stores(tmp_path)
    _service(events).infer_range(SESSIONS[0], RESUMED, daily_store=daily, session_bases=bases)

    factors = {r["symbol"]: r for r in adjustment_factors(events.read(), RESUMED).iter_rows(named=True)}

    assert factors[HUAGUAN]["trade_date"] == RESUMED
    assert factors[HUAGUAN]["ex_factor"] == pytest.approx(0.2)
    assert factors[HUAGUAN]["volume_factor"] == pytest.approx(0.2)
    assert factors[KY]["trade_date"] == EX_DAY
    assert factors[KY]["ex_factor"] == pytest.approx(19.55 / 18.3)
    assert factors[KY]["volume_factor"] == 1.0


def test_an_inferred_split_takes_the_official_ratio_like_a_listed_par_value_change():
    split = _events({**_official("6415.TWSE", EX_DAY, INFERRED, 188.65, 47.16), "source": INFERRED_SOURCE})

    row = adjustment_factors(split, EX_DAY).row(0, named=True)

    assert (row["ex_factor"], row["volume_factor"]) == (4.0, 4.0)


def test_a_rerun_leaves_the_stored_events_unchanged(tmp_path):
    daily, bases, events = _stores(tmp_path)
    service = _service(events)
    service.infer_range(SESSIONS[0], RESUMED, daily_store=daily, session_bases=bases)
    mtime = events.path.stat().st_mtime_ns

    stats = service.infer_range(SESSIONS[0], RESUMED, daily_store=daily, session_bases=bases)

    assert (stats["inserted"], stats["updated"], stats["unchanged"]) == (0, 0, 2)
    assert events.path.stat().st_mtime_ns == mtime


def test_only_the_sessions_of_the_range_are_inferred(tmp_path):
    daily, bases, events = _stores(tmp_path)

    stats = _service(events).infer_range(date(2024, 8, 20), RESUMED, daily_store=daily, session_bases=bases)

    assert [e["symbol"] for e in stats["inferred"]] == [HUAGUAN]


# --- What a session base without an event looks like ------------------------------------------


GAP_BARS = [_bar("1101.TWSE", date(2024, 6, 7), 10.0), _bar("1101.TWSE", date(2024, 6, 11), 10.0)]


def _gap_bases(ref: float) -> list[dict]:
    return [
        _base("1101.TWSE", date(2024, 6, 7), 12.0, 13.2, 10.8),
        _base("1101.TWSE", date(2024, 6, 11), ref, ref * 1.1, ref * 0.9, 9.0, 11.0),
    ]


@pytest.mark.parametrize(
    ("ref", "inferred"),
    [
        (10.0, False),  # the previous traded close
        (9.0, False),  # the last bid of the session before
        (11.0, False),  # the last ask of the session before
        (12.0, False),  # the base carried from the session before
        (10.04, False),  # within MIN_BASE_DEPARTURE of the close
        (12.07, True),
        (15.0, True),
    ],
)
def test_a_base_on_a_no_event_figure_is_not_inferred(ref, inferred):
    found = _infer(GAP_BARS, _gap_bases(ref))

    assert found.height == (1 if inferred else 0)


def test_the_carried_base_is_the_latest_one_since_the_previous_traded_close():
    bars = [_bar("1101.TWSE", date(2024, 6, 7), 10.0), _bar("1101.TWSE", date(2024, 6, 13), 10.0),
            _bar(PLAIN, date(2024, 6, 11), 1.0), _bar(PLAIN, EX_DAY, 1.0)]
    bases = [
        _base("1101.TWSE", date(2024, 6, 7), 9.0, 9.9, 8.1),
        _base("1101.TWSE", EX_DAY, 12.0, 13.2, 10.8),
        _base("1101.TWSE", date(2024, 6, 13), 12.0, 13.2, 10.8),
    ]

    assert _infer(bars, bases).is_empty()


def test_a_listed_event_on_the_session_blocks_the_inference_and_keeps_one_factor():
    listed = _events(_official(KY, EX_DAY, EXDIV, 19.55, 18.3))

    assert _infer(CASE_BARS, CASE_BASES, listed)["symbol"].to_list() == [HUAGUAN]


def test_a_listed_event_on_an_untraded_session_before_blocks_the_inference():
    listed = _events(_official(HUAGUAN, date(2024, 11, 18), REDUCTION, 1.90, 9.5))

    assert _infer(CASE_BARS, CASE_BASES, listed)["symbol"].to_list() == [KY]


def test_an_already_inferred_event_does_not_block_its_own_inference():
    first = _infer(CASE_BARS, CASE_BASES)

    again = _infer(CASE_BARS, CASE_BASES, first)

    assert again.drop("retrieved_at").equals(first.drop("retrieved_at"))


def test_a_first_bar_is_never_inferred():
    bars = [_bar("6999.TWSE", EX_DAY, 30.0), _bar(PLAIN, date(2024, 6, 11), 1.0)]
    bases = [_base("6999.TWSE", EX_DAY, 25.0, 27.5, 22.5)]

    assert _infer(bars, bases).is_empty()


@pytest.mark.parametrize(("up", "down"), [(None, None), (None, 16.5), (20.15, None)])
def test_a_session_without_both_official_limits_is_never_inferred(up, down):
    bases = [b for b in CASE_BASES if not (b["symbol"] == KY and b["source_date"] == EX_DAY)]
    bases.append(_base(KY, EX_DAY, 18.3, up, down, 19.5, 19.7))

    assert _infer(CASE_BARS, bases)["symbol"].to_list() == [HUAGUAN]


def test_a_session_without_an_official_base_is_never_inferred():
    bases = [b for b in CASE_BASES if b["symbol"] != HUAGUAN or b["source_date"] != RESUMED]

    assert _infer(CASE_BARS, bases)["symbol"].to_list() == [KY]


# --- An official event that arrives later ------------------------------------------------------


def test_an_official_event_stored_later_replaces_the_inferred_one_in_every_lookup(tmp_path):
    daily, bases, events = _stores(tmp_path)
    _service(events).infer_range(SESSIONS[0], RESUMED, daily_store=daily, session_bases=bases)

    events.upsert(_events(_official(KY, EX_DAY, EXDIV, 19.55, 18.25), _official(HUAGUAN, RESUMED, REDUCTION, 1.9, 9.48)))

    stored = events.read()
    assert stored.height == 4
    factors = adjustment_factors(stored, RESUMED).sort("symbol")
    assert factors["symbol"].to_list() == [HUAGUAN, KY]
    assert factors["ex_factor"].to_list() == pytest.approx([1.9 / 9.48, 19.55 / 18.25])
    assert factors["volume_factor"].to_list() == pytest.approx([1.9 / 9.48, 1.0])
    refs = reference_prices(stored, date(2024, 6, 11), EX_DAY)
    assert refs.select("symbol", "ref_price").rows() == [(KY, 18.25)]


def test_an_official_event_of_another_kind_outranks_the_inferred_one():
    inferred = _infer(CASE_BARS, CASE_BASES).filter(pl.col("symbol") == HUAGUAN)
    official = _events(_official(HUAGUAN, RESUMED, EXDIV, 1.9, 1.8))

    factors = adjustment_factors(pl.concat([inferred, official]), RESUMED)

    assert factors.rows() == [(HUAGUAN, RESUMED, pytest.approx(1.9 / 1.8), 1.0)]


# --- Daily publish ---------------------------------------------------------------------------------


@pytest.fixture
def publish_window(tmp_path, monkeypatch):
    """Pins the publish window to the 8101 resumption and records each enriched publish."""
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(TaiwanShareCapitalStore, "refresh", lambda self: 0)
    monkeypatch.setattr(TaiwanShareCapitalStore, "save_session_denominators", lambda self, d: None)
    monkeypatch.setattr(TaiwanEnrichedRefreshService, "recent_window", lambda self, days=5: (date(2024, 11, 18), RESUMED))
    calls: list[str] = []
    monkeypatch.setattr(TaiwanEnrichedRefreshService, "refresh_recent", lambda self, days=5: calls.append("publish") or 0)
    return calls


def _quiet_session_bases(store: TaiwanSessionBaseStore) -> MagicMock:
    return MagicMock(store=store, **{"refresh.return_value": {"failed": []}})


def test_the_daily_publish_stores_the_inferred_events_of_its_window_before_publishing(tmp_path, publish_window):
    daily, bases, events = _stores(tmp_path)
    service = _service(events)
    service.refresh_publish_window = MagicMock(return_value={"failed": []})
    notes: list[str] = []

    assert publish_enriched(daily, corporate_actions=service, session_bases=_quiet_session_bases(bases), notes=notes)

    assert notes == []
    assert publish_window == ["publish"]
    assert events.read().select("symbol", "date", "kind").rows() == [(HUAGUAN, RESUMED, INFERRED)]
    # The new factor differs from the recorded set, so the publish rewrites every partition.
    assert TaiwanEnrichedRefreshService(daily_store=daily).factors_changed(events.read(), RESUMED)


def test_a_failed_inference_is_noted_and_the_publish_goes_on(tmp_path, publish_window):
    daily, bases, _events_store = _stores(tmp_path)
    service = MagicMock(**{"refresh_publish_window.return_value": {"failed": []}})
    service.infer_range.side_effect = RuntimeError("session bases unreadable")
    notes: list[str] = []

    assert publish_enriched(daily, corporate_actions=service, session_bases=_quiet_session_bases(bases), notes=notes)

    assert notes == ["Corporate actions not inferred from the session bases: session bases unreadable"]
    assert publish_window == ["publish"]
    service.infer_range.assert_called_once_with(date(2024, 11, 18), RESUMED, daily_store=daily, session_bases=bases)


# --- CLI ---------------------------------------------------------------------------------------------


@pytest.fixture
def default_root(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _stores(tmp_path / "taiwan")
    return tmp_path / "taiwan"


def test_the_inference_dry_run_lists_the_events_and_writes_nothing(default_root, capsys):
    corporate_actions.main(["--infer", "--since", "2024-06-07", "--until", "2024-11-19", "--dry-run"])

    out = json.loads(capsys.readouterr().out)
    assert [(e["symbol"], e["date"], e["presumed_kind"]) for e in out["inferred"]] == [
        (KY, "2024-06-12", EXDIV), (HUAGUAN, "2024-11-19", REDUCTION),
    ]
    assert out["inferred"][1]["ex_factor"] == pytest.approx(0.2)
    assert out["inferred"][1]["volume_factor"] == pytest.approx(0.2)
    assert (out["stored"], out["inserted"]) == (False, 0)
    assert not (default_root / "corporate_actions").exists()


def test_the_inference_cli_stores_the_events_without_the_dry_run(default_root, capsys):
    corporate_actions.main(["--infer", "--since", "2024-06-07", "--until", "2024-11-19"])

    assert json.loads(capsys.readouterr().out)["inserted"] == 2
    assert TaiwanCorporateActionStore(default_root / "corporate_actions").read().height == 2


def test_the_dry_run_needs_the_inference(default_root):
    with pytest.raises(SystemExit):
        corporate_actions.main(["--since", "2024-06-07", "--dry-run"])


def test_inference_reads_the_tpex_bases_aligned_like_the_publish(tmp_path):
    """A .TPEX base comes from dailyQuotes of the session before, as official_bases_of aligns it."""
    from app.taiwan.session_bases import TPEX

    otc = "6488.TPEX"
    bars = [_bar(otc, date(2024, 6, 7), 450.0), _bar(otc, date(2024, 6, 11), 452.5), _bar(otc, EX_DAY, 440.0)]
    store = TaiwanSessionBaseStore(tmp_path)
    for source_date, ref in ((date(2024, 6, 7), 452.5), (date(2024, 6, 11), 440.0)):
        row = {**_base(otc, source_date, ref, ref * 1.1, ref * 0.9, 452.5, 453.0), "source": "tpex:dailyquotes"}
        store.write(TPEX, source_date, pl.DataFrame([row], schema=session_base_schema()))
    daily = pl.DataFrame(bars, schema_overrides={"quote_ts": pl.Int64})
    sessions = [date(2024, 6, 7), date(2024, 6, 11), EX_DAY]
    bases = official_bases_of(
        inference_lookups(daily, sessions, EX_DAY, EX_DAY), store=store, sessions=sessions,
        columns=("ref_price", "limit_up", "limit_down", "last_bid", "last_ask"),
    )

    found = infer_actions(daily, bases, _events(), EX_DAY, EX_DAY)

    assert found.select("symbol", "date", "pre_close", "ref_price", "provider").rows() == [
        (otc, EX_DAY, 452.5, 440.0, "tpex"),
    ]
