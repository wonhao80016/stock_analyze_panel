"""A halted session (null OHLC) is skipped by the published indicators and by every reader of them.

The watchlist and the screener read the published partition, and the publish drops the halted
row before computing, so MA, momentum and volume averages never turn null after a halt.
Expected values are the plain averages and returns over the traded sessions only.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from unittest.mock import MagicMock

import polars as pl
import pytest

from app.indicators.pipeline import invalidate_custom_signals, invalidate_taiwan_limit_profile_cache
from app.taiwan import universe
from app.taiwan.corporate_actions import TaiwanCorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument
from app.taiwan.watchlist_enrichment import enrich_taiwan_watchlist_rows

pytestmark = pytest.mark.usefixtures("deny_network")

HALTED = "3545.TWSE"
SESSIONS = 120
HALT_AT = SESSIONS - 10


def _sessions() -> list[date]:
    out, day = [], date(2026, 3, 2)
    while len(out) < SESSIONS:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


DAYS = _sessions()
TRADED = [t for t in range(SESSIONS) if t != HALT_AT]
CLOSES = {t: 50 + 3 * math.sin(t / 4) + 0.05 * t for t in TRADED}
VOLUMES = {t: 1_500_000.0 * (1 + 0.4 * math.sin(t / 3)) for t in TRADED}


def _daily_rows() -> list[dict]:
    rows = []
    for t, d in enumerate(DAYS):
        if t == HALT_AT:
            rows.append(
                {
                    "symbol": HALTED,
                    "date": d,
                    "open": None,
                    "high": None,
                    "low": None,
                    "close": None,
                    "volume": 0.0,
                    "amount": 0.0,
                    "quote_ts": None,
                }
            )
            continue
        c = CLOSES[t]
        rows.append(
            {
                "symbol": HALTED,
                "date": d,
                "open": c - 0.2,
                "high": c + 0.5,
                "low": c - 0.5,
                "close": c,
                "volume": VOLUMES[t],
                "amount": c * VOLUMES[t],
                "quote_ts": None,
            }
        )
    return rows


def _reference(t: int) -> dict[str, float]:
    """Indicators at session t over the traded sessions only."""
    k = TRADED.index(t)
    closes = [CLOSES[i] for i in TRADED[: k + 1]]
    volumes = [VOLUMES[i] for i in TRADED[: k + 1]]
    out = {f"ma{n}": sum(closes[-n:]) / n for n in (5, 10, 20, 60)}
    out.update({f"momentum_{n}d": closes[-1] / closes[-1 - n] - 1 for n in (5, 10, 20, 60)})
    out.update({f"vol_ma{n}": sum(volumes[-n:]) / n for n in (5, 10)})
    out["vol_ratio_5d"] = volumes[-1] / (sum(volumes[-6:-1]) / 5)
    return out


INDICATORS = list(_reference(SESSIONS - 1))


def _instrument() -> TaiwanInstrument:
    return TaiwanInstrument(
        symbol=HALTED,
        code="3545",
        exchange="TWSE",
        name="3545",
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


class _NoQuotes:
    def get_quotes(self, symbols, daily_kline_fallback_fn=None):
        return {}


class _NoShareCapital:
    def load(self) -> pl.DataFrame:
        return pl.DataFrame(schema={"symbol": pl.Utf8, "issued_shares": pl.Int64})


_STATE: dict[str, object] = {}


@pytest.fixture
def world(deny_network, tmp_path_factory, monkeypatch):
    from app.config import settings

    if "root" not in _STATE:
        root = tmp_path_factory.mktemp("stage6_halt")
        master_path = root / "security_master.parquet"
        pl.DataFrame([_instrument().to_dict()]).write_parquet(master_path)
        master = TaiwanSecurityMaster(cache_path=master_path)
        assert master.load_cache()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "data_dir", root)
            mp.setattr(universe, "_default_master", master)
            invalidate_taiwan_limit_profile_cache()
            invalidate_custom_signals()
            try:
                daily = TaiwanDailyStore(data_dir=root / "taiwan" / "daily")
                daily.write_batch(
                    pl.DataFrame(_daily_rows(), schema_overrides={"quote_ts": pl.Int64})
                )
                actions = TaiwanCorporateActionStore(data_dir=root / "taiwan" / "corporate_actions")
                TaiwanEnrichedRefreshService(
                    daily_store=daily,
                    enriched_root=root / "kline_daily_enriched",
                    share_capital=MagicMock(as_instruments=MagicMock(return_value=pl.DataFrame())),
                    corporate_actions=actions,
                ).refresh()
            finally:
                invalidate_taiwan_limit_profile_cache()
                invalidate_custom_signals()
        _STATE.update(root=root, daily=daily, master=master, actions=actions)

    monkeypatch.setattr(settings, "data_dir", _STATE["root"])
    monkeypatch.setattr(universe, "_default_master", _STATE["master"])
    invalidate_taiwan_limit_profile_cache()
    yield _STATE["root"], _STATE["daily"], _STATE["master"], _STATE["actions"]
    invalidate_taiwan_limit_profile_cache()


def _partition(root, d: date) -> pl.DataFrame:
    return pl.read_parquet(root / "kline_daily_enriched" / f"date={d.isoformat()}" / "part.parquet")


def test_the_halted_session_is_not_published(world):
    root, *_ = world
    assert not (root / "kline_daily_enriched" / f"date={DAYS[HALT_AT].isoformat()}").exists()
    assert (root / "kline_daily_enriched" / f"date={DAYS[HALT_AT + 1].isoformat()}").exists()


@pytest.mark.parametrize("t", range(HALT_AT + 1, SESSIONS))
def test_published_indicators_after_the_halt_skip_the_halted_session(world, t):
    """Every MA stays non-null and equals the average over traded sessions."""
    root, *_ = world
    row = _partition(root, DAYS[t]).filter(pl.col("symbol") == HALTED).row(0, named=True)
    for name, expected in _reference(t).items():
        assert row[name] == pytest.approx(expected, rel=1e-12), name


def test_watchlist_indicators_skip_the_halted_session(world):
    """The watchlist row carries the published values and their date."""
    _, daily, master, actions = world
    row = enrich_taiwan_watchlist_rows(
        [HALTED],
        security_master=master,
        realtime_service=_NoQuotes(),
        daily_store=daily,
        share_capital=_NoShareCapital(),
        corporate_actions=actions,
    )[0]
    assert row["indicator_date"] == DAYS[-1].isoformat()
    for name, expected in _reference(SESSIONS - 1).items():
        assert row[name] == pytest.approx(expected, rel=1e-12), name


def test_screener_indicators_and_ma_filters_skip_the_halted_session(world):
    """The screener keeps the symbol in the MA filters and shows the published MA."""
    root, daily, master, _ = world
    service = TaiwanScreenerService(
        security_master=master,
        daily_store=daily,
        institutional_store=TaiwanInstitutionalStore(data_dir=root / "taiwan" / "institutional"),
        margin_store=TaiwanMarginStore(data_dir=root / "taiwan" / "margin"),
    )
    expected = _reference(SESSIONS - 1)
    close = CLOSES[SESSIONS - 1]
    assert close != pytest.approx(expected["ma5"]) and close != pytest.approx(expected["ma20"])

    item = next(i for i in service.run(TaiwanScreenerRequest()).items if i.symbol == HALTED)
    for name in ("ma5", "ma10", "ma20", "momentum_5d", "vol_ratio_5d"):
        assert getattr(item, name) == pytest.approx(expected[name], rel=1e-12), name

    for flag, ma in (("above_ma5", "ma5"), ("above_ma20", "ma20")):
        above = close > expected[ma]
        for wanted in (True, False):
            symbols = [i.symbol for i in service.run(TaiwanScreenerRequest(**{flag: wanted})).items]
            assert (HALTED in symbols) is (wanted is above), (flag, wanted)


COMPANION = "2330.TWSE"


def _latest_halt_rows() -> list[dict]:
    """HALTED trades every session but the newest; COMPANION trades every session."""
    rows = []
    for t, d in enumerate(DAYS[:70]):
        for symbol, base in ((HALTED, 50.0), (COMPANION, 900.0)):
            if symbol == HALTED and t == 69:
                bar = dict.fromkeys(("open", "high", "low", "close"))
                volume = 0.0
            else:
                c = base + 3 * math.sin(t / 4)
                bar = {"open": c - 0.2, "high": c + 0.5, "low": c - 0.5, "close": c}
                volume = 1_500_000.0
            rows.append(
                {
                    "symbol": symbol,
                    "date": d,
                    **bar,
                    "volume": volume,
                    "amount": (bar["close"] or 0.0) * volume,
                    "quote_ts": None,
                }
            )
    return rows


def _watchlist_after_a_newest_session_halt(tmp_path, monkeypatch) -> tuple[dict, dict]:
    """Watchlist rows of HALTED and COMPANION after publishing _latest_halt_rows."""
    from app.config import settings

    master_path = tmp_path / "security_master.parquet"
    companion = TaiwanInstrument(
        **{**_instrument().to_dict(), "symbol": COMPANION, "code": "2330", "name": "2330"}
    )
    pl.DataFrame([_instrument().to_dict(), companion.to_dict()]).write_parquet(master_path)
    master = TaiwanSecurityMaster(cache_path=master_path)
    assert master.load_cache()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(universe, "_default_master", master)
    invalidate_taiwan_limit_profile_cache()
    invalidate_custom_signals()
    try:
        daily = TaiwanDailyStore(data_dir=tmp_path / "taiwan" / "daily")
        daily.write_batch(
            pl.DataFrame(_latest_halt_rows(), schema_overrides={"quote_ts": pl.Int64})
        )
        actions = TaiwanCorporateActionStore(data_dir=tmp_path / "taiwan" / "corporate_actions")
        TaiwanEnrichedRefreshService(
            daily_store=daily,
            enriched_root=tmp_path / "kline_daily_enriched",
            share_capital=MagicMock(as_instruments=MagicMock(return_value=pl.DataFrame())),
            corporate_actions=actions,
        ).refresh()
        assert _partition(tmp_path, DAYS[69])["symbol"].to_list() == [COMPANION]
        halted, traded = enrich_taiwan_watchlist_rows(
            [HALTED, COMPANION],
            security_master=master,
            realtime_service=_NoQuotes(),
            daily_store=daily,
            share_capital=_NoShareCapital(),
            corporate_actions=actions,
        )
    finally:
        invalidate_taiwan_limit_profile_cache()
        invalidate_custom_signals()
    assert traded["indicator_date"] == DAYS[69].isoformat()
    assert traded["ma60"] == pytest.approx(
        sum(900.0 + 3 * math.sin(t / 4) for t in range(10, 70)) / 60
    )
    return halted, traded


def test_a_symbol_halted_on_the_newest_session_has_no_indicators_that_day(tmp_path, monkeypatch):
    # Pins the residual recorded for A02#7: the newest partition has no row for a symbol halted that
    # session, so its watchlist indicators and indicator_date are null. Delete with the xfail below.
    halted, _ = _watchlist_after_a_newest_session_halt(tmp_path, monkeypatch)
    assert halted["indicator_date"] is None
    assert all(halted[name] is None for name in INDICATORS)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="A02#7: a symbol halted on the newest session shows no indicators instead of its last traded ones",
)
def test_a_symbol_halted_on_the_newest_session_keeps_its_last_traded_indicators(
    tmp_path, monkeypatch
):
    halted, _ = _watchlist_after_a_newest_session_halt(tmp_path, monkeypatch)
    closes = [50.0 + 3 * math.sin(t / 4) for t in range(69)]
    assert halted["indicator_date"] == DAYS[68].isoformat()
    assert halted["ma60"] == pytest.approx(sum(closes[-60:]) / 60)
