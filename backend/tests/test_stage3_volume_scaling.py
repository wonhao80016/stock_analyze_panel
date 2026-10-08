"""Stage 3 S7: opening volume-ratio scaling is floored at 15 minutes of trading.

Partial-session volume is extrapolated to the 270-minute session by whole minutes elapsed,
never fewer than 15, so the factor stays at or below 18. Continuous trading stops at 13:25 for
the closing auction and the 13:30 auction result is the full session.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import polars as pl
import pytest

TAIPEI = timezone(timedelta(hours=8))
TODAY = date(2026, 10, 7)
SYMBOL = "2330.TWSE"


def _ts(hour: int, minute: int, second: int = 0) -> int:
    moment = datetime(TODAY.year, TODAY.month, TODAY.day, hour, minute, second, tzinfo=TAIPEI)
    return int(moment.timestamp() * 1000)


@pytest.mark.parametrize(
    ("clock", "factor"),
    [
        ((9, 0, 5), 18.0),
        ((9, 1, 0), 18.0),
        ((9, 15, 0), 18.0),
        ((10, 0, 0), 4.5),
        ((10, 0, 59), 4.5),
        ((11, 15, 0), 2.0),
        ((13, 24, 0), 270 / 264),
        ((13, 25, 0), 270 / 265),
        ((13, 29, 59), 270 / 265),
        ((13, 30, 0), 1.0),
        ((14, 0, 0), 1.0),
        ((8, 59, 0), 1.0),
    ],
)
def test_the_volume_time_factor_at_the_quote_time(deny_network, clock, factor):
    from app.taiwan.live_enriched import _volume_time_factor

    assert _volume_time_factor(_ts(*clock)) == pytest.approx(factor, rel=1e-12)


def test_no_opening_quote_is_scaled_more_than_eighteen_fold(deny_network):
    from app.taiwan.live_enriched import _volume_time_factor

    factors = [_volume_time_factor(_ts(9, s // 60, s % 60)) for s in range(0, 20 * 60, 5)]

    assert max(factors) == pytest.approx(18.0)


def test_session_progress_floors_the_opening_minutes(deny_network):
    from app.taiwan.realtime.calendar import session_minutes_elapsed, session_progress

    opening = datetime(2026, 10, 7, 9, 0, 5, tzinfo=TAIPEI)

    assert session_minutes_elapsed(opening) == 0.0
    assert session_progress(opening) == pytest.approx(15 / 270)
    assert session_progress(datetime(2026, 10, 7, 8, 59, tzinfo=TAIPEI)) == 0.0


@pytest.fixture
def no_custom_signals(deny_network, tmp_path, monkeypatch):
    from app.config import settings
    from app.indicators.pipeline import invalidate_custom_signals

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    invalidate_custom_signals()
    yield
    invalidate_custom_signals()


def _enriched(today_ratio: float) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [SYMBOL, SYMBOL],
            "date": [TODAY - timedelta(days=1), TODAY],
            "vol_ratio_5d": [1.2, today_ratio],
            "signal_volume_surge": [False, False],
        }
    )


def _live(quote_ts: int) -> pl.DataFrame:
    return pl.DataFrame({"symbol": [SYMBOL], "quote_ts": [quote_ts]})


def test_an_opening_quote_scales_the_ratio_and_surge_at_most_eighteen_fold(no_custom_signals):
    from app.taiwan.live_enriched import _scale_volume_ratio

    out = _scale_volume_ratio(_enriched(0.1), _live(_ts(9, 0, 5)), TODAY)

    today = out.filter(pl.col("date") == TODAY).row(0, named=True)
    before = out.filter(pl.col("date") < TODAY).row(0, named=True)
    assert today["vol_ratio_5d"] == pytest.approx(1.8)
    assert today["signal_volume_surge"] is False
    assert today["volume_time_factor"] == pytest.approx(18.0)
    assert before["vol_ratio_5d"] == 1.2
    assert before["volume_time_factor"] == 1.0


def test_a_scaled_ratio_of_two_still_surges(no_custom_signals):
    from app.taiwan.live_enriched import _scale_volume_ratio

    out = _scale_volume_ratio(_enriched(0.5), _live(_ts(10, 0)), TODAY)

    today = out.filter(pl.col("date") == TODAY).row(0, named=True)
    assert today["vol_ratio_5d"] == pytest.approx(2.25)
    assert today["signal_volume_surge"] is True
    assert today["volume_time_factor"] == pytest.approx(4.5)


def test_the_closing_auction_result_is_not_an_estimate(no_custom_signals):
    from app.taiwan.live_enriched import _scale_volume_ratio

    out = _scale_volume_ratio(_enriched(0.9), _live(_ts(13, 30)), TODAY)

    today = out.filter(pl.col("date") == TODAY).row(0, named=True)
    assert today["vol_ratio_5d"] == 0.9
    assert today["volume_time_factor"] == 1.0


def _request(snapshot: pl.DataFrame):
    service = SimpleNamespace(
        get_enriched_today=lambda: (snapshot, date.today()), fetch_live_bar=lambda s: None
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(quote_service=service)))


def test_the_live_candle_carries_the_volume_time_factor(deny_network):
    from app.api.kline import _maybe_inject_live_candle

    snapshot = pl.DataFrame(
        {
            "symbol": [SYMBOL],
            "open": [1400.0],
            "high": [1410.0],
            "low": [1395.0],
            "close": [1405.0],
            "volume": [500_000.0],
            "amount": [None],
            "vol_ratio_5d": [0.9],
            "volume_time_factor": [18.0],
        },
        schema_overrides={"amount": pl.Float64},
    )

    rows = _maybe_inject_live_candle(_request(snapshot), SYMBOL, [], "stock")

    assert rows[-1]["volume_time_factor"] == 18.0
    assert rows[-1]["vol_ratio_5d"] == 0.9
