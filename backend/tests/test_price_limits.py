from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.api import kline
from app.backtest.matrix import load_market_data_matrix_from_parquet
from app.indicators import pipeline
from app.price_limits import (
    MAIN_BOARD_ST_LIMIT_CHANGE_DATE,
    numpy_limit_pct_vectors,
    numpy_limit_price,
    numpy_price_limit_matrix,
    polars_is_risk_warning_name,
    polars_limit_price,
    polars_price_limit_pct,
    price_limit_pct,
)
from app.taiwan.universe.models import TaiwanInstrument


@pytest.mark.parametrize(
    ("symbol", "trade_date", "is_st", "expected"),
    [
        ("600001.SH", date(2026, 7, 3), True, 0.05),
        ("600001.SH", date(2026, 7, 6), True, 0.10),
        ("000001.SZ", date(2026, 7, 3), False, 0.10),
        ("300001.SZ", date(2026, 7, 3), True, 0.20),
        ("688001.SH", date(2026, 7, 3), True, 0.20),
        ("689001.SH", date(2026, 7, 3), True, 0.20),
        ("830001.BJ", date(2026, 7, 3), True, 0.30),
    ],
)
def test_scalar_price_limit_rules(symbol, trade_date, is_st, expected):
    assert price_limit_pct(
        symbol,
        trade_date,
        is_risk_warning=is_st,
    ) == pytest.approx(expected)


def test_polars_and_numpy_price_limit_rules_match():
    """A-share boards only: both variants derive the rate from the symbol alone.

    The Taiwan half of this relationship is pinned separately by
    test_polars_null_and_numpy_nan_agree_on_taiwan_no_limit, where the two
    variants legitimately differ.
    """
    dates = [date(2026, 7, 3), date(2026, 7, 6)]
    symbols = ["600001.SH", "300001.SZ", "689001.SH", "830001.BJ"]
    names = ["*st主板", "*ST创业", "科创ST", "北交ST"]
    panel = pl.DataFrame({
        "date": [value for value in dates for _ in symbols],
        "symbol": symbols * len(dates),
        "name": names * len(dates),
    }).with_columns(
        polars_is_risk_warning_name(pl.col("name")).alias("is_st")
    ).with_columns(
        polars_price_limit_pct(
            pl.col("symbol"), pl.col("date"), pl.col("is_st"),
        ).alias("limit_pct")
    )
    polars_values = panel["limit_pct"].to_numpy().reshape(len(dates), len(symbols))
    numpy_values = numpy_price_limit_matrix(dates, symbols, names)
    np.testing.assert_allclose(polars_values, numpy_values)


def test_polars_and_numpy_limit_prices_use_identical_half_up_rounding():
    previous = np.array([18.90, 10.00], dtype=np.float64)
    limits = np.array([0.05, 0.10], dtype=np.float64)
    frame = pl.DataFrame({"previous": previous, "limit": limits})

    for up in (True, False):
        polars_values = frame.select(
            polars_limit_price(
                pl.col("previous"), pl.col("limit"), up=up,
            ).alias("price")
        )["price"].to_numpy()
        numpy_values = numpy_limit_price(previous, limits, up=up)
        np.testing.assert_allclose(polars_values, numpy_values)
    assert numpy_limit_price(previous, limits, up=False)[0] == pytest.approx(17.96)


def test_matrix_uses_date_specific_st_limits_across_change(tmp_path):
    root = tmp_path / "market"
    rows = [
        (date(2026, 7, 2), 10.0),
        (date(2026, 7, 3), 10.5),
        (date(2026, 7, 6), 11.03),
    ]
    for trade_date, close in rows:
        partition = root / f"date={trade_date.isoformat()}"
        partition.mkdir(parents=True)
        pl.DataFrame({
            "symbol": ["600001.SH"],
            "date": [trade_date],
            "open": [close],
            "high": [close],
            "low": [close],
            "close": [close],
            "raw_close": [close],
            "volume": [1000.0],
        }).write_parquet(partition / "part.parquet")

    market = load_market_data_matrix_from_parquet(
        root,
        rows[0][0],
        rows[-1][0],
        field_columns={"raw_close", "price_limit_pct"},
        instruments=pl.DataFrame({
            "symbol": ["600001.SH"],
            "name": ["*ST主板"],
        }),
        cache_root=tmp_path / "cache",
    )
    np.testing.assert_allclose(
        market.field("price_limit_pct")[:, 0],
        np.array([0.05, 0.05, 0.10], dtype=np.float32),
    )
    assert market.limit_up_locked[:, 0].tolist() == [0, 1, 0]


class _InstrumentRepo:
    def get_instruments_asset(self, asset_type: str) -> pl.DataFrame:
        assert asset_type == "stock"
        return pl.DataFrame({
            "symbol": ["600001.SH"],
            "limit_up": [10.88],
            "limit_down": [8.90],
        })


def test_minute_price_limit_prefers_authoritative_prices_only_today(monkeypatch):
    today = date(2026, 7, 18)
    monkeypatch.setattr(kline, "cn_today", lambda: today)
    current = kline._get_price_limit_info(
        _InstrumentRepo(), "600001.SH", today, "stock", "*ST主板",
    )
    historical = kline._get_price_limit_info(
        _InstrumentRepo(), "600001.SH", date(2026, 7, 3), "stock", "*ST主板",
    )

    assert current == {
        "rate": 0.10,
        "limit_up": 10.88,
        "limit_down": 8.90,
        "source": "instrument",
    }
    assert historical == {
        "rate": 0.05,
        "limit_up": None,
        "limit_down": None,
        "source": "rule",
    }


class _HighPriceInstrumentRepo:
    def get_instruments_asset(self, asset_type: str) -> pl.DataFrame:
        return pl.DataFrame({"symbol": ["688001.SH"], "limit_up": [12_000.0], "limit_down": [8_000.0]})


def test_minute_price_limit_keeps_authoritative_prices_above_ten_thousand(monkeypatch):
    today = date(2026, 7, 18)
    monkeypatch.setattr(kline, "cn_today", lambda: today)

    got = kline._get_price_limit_info(_HighPriceInstrumentRepo(), "688001.SH", today, "stock", "科創板")

    assert got == {"rate": 0.20, "limit_up": 12_000.0, "limit_down": 8_000.0, "source": "instrument"}


def test_minute_price_limit_of_a_past_session_comes_from_its_previous_close():
    got = kline._get_price_limit_info(
        _InstrumentRepo(), "600001.SH", date(2026, 7, 3), "stock", "*ST主板", prev_close=9.87,
    )

    assert got == {"rate": 0.05, "limit_up": 10.36, "limit_down": 9.38, "source": "model"}


def _daily_limit_rows(current_close: float) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["600001.SH", "600001.SH"],
        "date": [date(2026, 7, 17), date(2026, 7, 20)],
        "open": [10.0, current_close],
        "high": [10.0, current_close],
        "low": [10.0, current_close],
        "close": [10.0, current_close],
        "raw_close": [10.0, current_close],
        "raw_high": [10.0, current_close],
    })


@pytest.mark.parametrize(
    ("instrument_as_of", "expected"),
    [
        (date(2026, 7, 17), False),
        (date(2026, 7, 20), True),
        (None, True),
    ],
)
def test_daily_limit_prices_require_matching_instrument_date(instrument_as_of, expected):
    instrument_data = {
        "symbol": ["600001.SH"],
        "name": ["普通股"],
        "limit_up": [10.90],
        "limit_down": [9.10],
    }
    if instrument_as_of is not None:
        instrument_data["as_of"] = [instrument_as_of]

    result = pipeline.compute_limit_signals(
        _daily_limit_rows(9.10),
        pl.DataFrame(instrument_data),
        needed={"signal_limit_down"},
    )

    assert result["signal_limit_down"][-1] is expected
    assert "_instrument_as_of" not in result.columns


def test_realtime_limit_prices_ignore_stale_instrument_date():
    today = date(2026, 7, 20)
    rows = pl.DataFrame({
        "symbol": ["600001.SH"],
        "date": [today],
        "open": [9.10],
        "high": [9.10],
        "low": [9.10],
        "close": [9.10],
        "raw_close": [9.10],
        "raw_high": [9.10],
        "raw_low": [9.10],
        "_prev_close_raw": [10.0],
        "volume": [1000.0],
    })
    instruments = pl.DataFrame({
        "symbol": ["600001.SH"],
        "name": ["普通股"],
        "limit_up": [10.90],
        "limit_down": [9.10],
        "as_of": [date(2026, 7, 17)],
    })

    result = pipeline._compute_limit_signals_today(rows, instruments)

    assert result["signal_limit_down"][0] is False
    assert "_instrument_as_of" not in result.columns


def test_limit_down_recovery_uses_raw_low_under_later_ex_div():
    """除权事件之后重算历史时, 跌停翻红"曾触及跌停"必须用原始价 low 判断。

    day2 (历史日): 原始 low 9.30 未触及跌停价 9.00, 不应触发;
    但 day3 除权 (ex_factor=2) 使 day2 前复权 low 变为 4.65,
    若误用复权 low 对比原始口径跌停价会误报。
    day2 收盘 10.05 高于昨收, 只差 low 未触及。
    day3 (除权日, 最新日不复权): 涨跌停基准切换为前复权昨收 5.025 → 跌停价 4.52,
    原始 low 4.52 触及跌停, 收盘 5.10 高于基准昨收 5.025 → 跌停翻红。
    """
    raw = pl.DataFrame({
        "symbol": ["600001.SH"] * 3,
        "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
        "open": [10.00, 9.60, 4.60],
        "high": [10.10, 10.10, 5.15],
        "low": [9.90, 9.30, 4.52],
        "close": [10.00, 10.05, 5.10],
        "volume": [10000.0, 10000.0, 10000.0],
        "amount": [1.0e7, 1.0e7, 1.0e7],
    })
    factors = pl.DataFrame({
        "symbol": ["600001.SH"],
        "trade_date": [date(2024, 1, 4)],
        "ex_factor": [2.0],
    })
    instruments = pl.DataFrame({
        "symbol": ["600001.SH"],
        "name": ["普通股"],
        "float_shares": [1.0e8],
    })

    df = pipeline.compute_enriched(raw, factors=factors, instruments=instruments)

    day2 = df.filter(pl.col("date") == date(2024, 1, 3))
    assert day2["signal_limit_down_recovery"][0] is False
    day3 = df.filter(pl.col("date") == date(2024, 1, 4))
    assert day3["signal_limit_down_recovery"][0] is True


# ── Taiwan branch of polars_price_limit_pct (Step 7: must not silently fall
# back to the A-share 10%/20% board logic for .TWSE/.TPEX symbols) ──────────


def test_polars_price_limit_pct_returns_null_for_taiwan_symbols():
    symbols = ["2330.TWSE", "3008.TWSE", "6889.TPEX", "00631L.TWSE"]
    df = pl.DataFrame({
        "symbol": symbols,
        "date": [date(2026, 9, 17)] * len(symbols),
        "is_st": [False] * len(symbols),
    })

    out = df.with_columns(
        polars_price_limit_pct(pl.col("symbol"), pl.col("date"), pl.col("is_st")).alias("pct")
    )

    assert out["pct"].is_null().all()


def test_polars_price_limit_pct_a_share_boards_unchanged_by_taiwan_branch():
    dates = [MAIN_BOARD_ST_LIMIT_CHANGE_DATE - timedelta(days=1), MAIN_BOARD_ST_LIMIT_CHANGE_DATE]
    symbols = ["600001.SH", "000001.SZ", "300001.SZ", "301001.SZ", "688001.SH", "689001.SH", "830001.BJ"]
    rows = [(sym, d, st) for sym in symbols for d in dates for st in (True, False)]
    df = pl.DataFrame({
        "symbol": [r[0] for r in rows],
        "date": [r[1] for r in rows],
        "is_st": [r[2] for r in rows],
    })

    out = df.with_columns(
        polars_price_limit_pct(pl.col("symbol"), pl.col("date"), pl.col("is_st")).alias("pct")
    ).with_columns(
        pl.Series(
            "expected",
            [price_limit_pct(r[0], r[1], is_risk_warning=r[2]) for r in rows],
            dtype=pl.Float64,
        )
    )

    assert (out["pct"] == out["expected"]).all()


# ── Taiwan coalesce in the pipeline call sites (Step 7 §2) ─────────────────


def _taiwan_instrument(
    symbol: str,
    *,
    instrument_type: str = "stock",
    etf_category: str | None = None,
    classification_source: str | None = None,
    underlying_scope: str | None = None,
    leverage_multiplier: float = 1.0,
) -> TaiwanInstrument:
    return TaiwanInstrument(
        symbol=symbol,
        code=symbol.split(".")[0],
        exchange="TWSE" if symbol.endswith(".TWSE") else "TPEX",
        name="測試標的",
        instrument_type=instrument_type,
        listing_status="active",
        listing_date="2020/01/01",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="股票" if instrument_type == "stock" else "ETF",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-09-17",
        etf_category=etf_category,
        classification_source=classification_source,
        underlying_scope=underlying_scope,
        leverage_multiplier=leverage_multiplier,
    )


class _TaiwanFakeSecurityMaster:
    def __init__(self, instruments: dict[str, TaiwanInstrument]):
        self._instruments = instruments

    def get_instrument(self, symbol: str) -> TaiwanInstrument | None:
        return self._instruments.get(symbol)


def _patch_taiwan_master(monkeypatch, instruments: dict[str, TaiwanInstrument]) -> None:
    monkeypatch.setattr(
        "app.taiwan.universe.get_security_master",
        lambda: _TaiwanFakeSecurityMaster(instruments),
    )


@pytest.fixture(autouse=True)
def _clear_taiwan_limit_profile_cache():
    pipeline.invalidate_taiwan_limit_profile_cache()
    yield
    pipeline.invalidate_taiwan_limit_profile_cache()


def _taiwan_today_rows(prev_close: float, close: float, *, symbol: str) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [symbol],
        "date": [date(2026, 9, 17)],
        "open": [prev_close],
        "high": [close],
        "low": [prev_close],
        "close": [close],
        "raw_close": [close],
        "raw_high": [close],
        "raw_low": [prev_close],
        "_prev_close_raw": [prev_close],
        "volume": [1000.0],
    })


def test_compute_limit_signals_today_taiwan_ordinary_stock_uses_10pct(monkeypatch):
    symbol = "2330.TWSE"
    _patch_taiwan_master(monkeypatch, {symbol: _taiwan_instrument(symbol)})
    rows = _taiwan_today_rows(100.0, 111.0, symbol=symbol)
    instruments = pl.DataFrame({"symbol": [symbol]})

    out = pipeline._compute_limit_signals_today(rows, instruments)

    assert out["signal_limit_up"][0] is True


def test_compute_limit_signals_today_taiwan_leveraged_etf_uses_20pct_not_10pct(monkeypatch):
    symbol = "00631L.TWSE"
    inst = _taiwan_instrument(
        symbol,
        instrument_type="etf",
        etf_category="leveraged",
        classification_source="official_metadata",
        underlying_scope="domestic",
        leverage_multiplier=2.0,
    )
    _patch_taiwan_master(monkeypatch, {symbol: inst})
    # +15%: above the pre-fix 10% fallback, but still under the real 20% cap.
    rows = _taiwan_today_rows(100.0, 115.0, symbol=symbol)
    instruments = pl.DataFrame({"symbol": [symbol]})

    out = pipeline._compute_limit_signals_today(rows, instruments)

    assert out["signal_limit_up"][0] is False


def test_compute_limit_signals_today_taiwan_no_limit_etf_stays_null(monkeypatch):
    symbol = "00646.TWSE"
    inst = _taiwan_instrument(
        symbol,
        instrument_type="etf",
        etf_category="foreign_equity",
        classification_source="official_metadata",
        underlying_scope="foreign",
    )
    _patch_taiwan_master(monkeypatch, {symbol: inst})
    # A move large enough to trip any finite percentage cap.
    rows = _taiwan_today_rows(100.0, 200.0, symbol=symbol)
    instruments = pl.DataFrame({"symbol": [symbol]})

    out = pipeline._compute_limit_signals_today(rows, instruments)

    assert out["signal_limit_up"][0] is None
    assert out["signal_limit_down"][0] is None


@pytest.mark.parametrize("symbol", ["3008.TWSE", "6889.TPEX"])
def test_compute_limit_signals_today_taiwan_growth_star_prefixed_code_not_misjudged(monkeypatch, symbol):
    _patch_taiwan_master(monkeypatch, {symbol: _taiwan_instrument(symbol)})
    # +15%: above the real 10% ordinary-stock cap. The pre-fix code matched
    # these code prefixes against the A-share growth/STAR board (20%) and
    # silently stayed quiet here.
    rows = _taiwan_today_rows(100.0, 115.0, symbol=symbol)
    instruments = pl.DataFrame({"symbol": [symbol]})

    out = pipeline._compute_limit_signals_today(rows, instruments)

    assert out["signal_limit_up"][0] is True


def test_compute_limit_signals_batch_taiwan_leveraged_etf_uses_20pct_not_10pct(monkeypatch):
    symbol = "00631L.TWSE"
    inst = _taiwan_instrument(
        symbol,
        instrument_type="etf",
        etf_category="leveraged",
        classification_source="official_metadata",
        underlying_scope="domestic",
        leverage_multiplier=2.0,
    )
    _patch_taiwan_master(monkeypatch, {symbol: inst})
    df = pl.DataFrame({
        "symbol": [symbol, symbol],
        "date": [date(2026, 9, 16), date(2026, 9, 17)],
        "open": [100.0, 115.0],
        "high": [100.0, 115.0],
        "low": [100.0, 115.0],
        "close": [100.0, 115.0],
        "raw_close": [100.0, 115.0],
        "raw_high": [100.0, 115.0],
    })
    instruments = pl.DataFrame({"symbol": [symbol], "name": ["測試ETF"]})

    result = pipeline.compute_limit_signals(df, instruments, needed={"signal_limit_up"})

    assert result["signal_limit_up"][-1] is False


def test_compute_limit_signals_batch_mixed_market_a_share_unaffected_by_taiwan_join(monkeypatch):
    tw_symbol = "00631L.TWSE"
    a_symbol = "600001.SH"
    inst = _taiwan_instrument(
        tw_symbol,
        instrument_type="etf",
        etf_category="leveraged",
        classification_source="official_metadata",
        underlying_scope="domestic",
        leverage_multiplier=2.0,
    )
    _patch_taiwan_master(monkeypatch, {tw_symbol: inst})
    df = pl.DataFrame({
        "symbol": [tw_symbol, tw_symbol, a_symbol, a_symbol],
        "date": [date(2026, 9, 16), date(2026, 9, 17)] * 2,
        "open": [100.0, 115.0, 10.0, 11.0],
        "high": [100.0, 115.0, 10.0, 11.0],
        "low": [100.0, 115.0, 10.0, 11.0],
        "close": [100.0, 115.0, 10.0, 11.0],
        "raw_close": [100.0, 115.0, 10.0, 11.0],
        "raw_high": [100.0, 115.0, 10.0, 11.0],
    })
    instruments = pl.DataFrame({
        "symbol": [tw_symbol, a_symbol],
        "name": ["測試ETF", "普通股"],
    })

    result = pipeline.compute_limit_signals(df, instruments, needed={"signal_limit_up"})

    a_row = result.filter(pl.col("symbol") == a_symbol).sort("date")
    tw_row = result.filter(pl.col("symbol") == tw_symbol).sort("date")
    assert a_row["signal_limit_up"][-1] is True
    assert tw_row["signal_limit_up"][-1] is False


# -- Taiwan branch of the NumPy vectors: the per-instrument limit must reach
# -- the backtest matrix, which cannot read the Polars expression's null. --


def _no_limit_etf(symbol: str) -> TaiwanInstrument:
    return _taiwan_instrument(
        symbol,
        instrument_type="etf",
        etf_category="foreign_equity",
        classification_source="official_metadata",
        underlying_scope="foreign",
    )


def _leveraged_etf(symbol: str) -> TaiwanInstrument:
    return _taiwan_instrument(
        symbol,
        instrument_type="etf",
        etf_category="leveraged",
        classification_source="official_metadata",
        underlying_scope="domestic",
        leverage_multiplier=2.0,
    )


def test_numpy_limit_pct_vectors_resolve_taiwan_per_instrument(monkeypatch):
    ordinary = "2330.TWSE"
    leveraged = "00631L.TWSE"
    foreign = "00646.TWSE"
    unconfirmed = "00888.TWSE"
    absent = "9999.TPEX"
    _patch_taiwan_master(monkeypatch, {
        ordinary: _taiwan_instrument(ordinary),
        leveraged: _leveraged_etf(leveraged),
        foreign: _no_limit_etf(foreign),
        unconfirmed: _taiwan_instrument(
            unconfirmed,
            instrument_type="etf",
            etf_category="equity",
            classification_source="official_metadata",
            underlying_scope="unknown",
        ),
    })
    symbols = [ordinary, leveraged, foreign, unconfirmed, absent]

    _legacy, current = numpy_limit_pct_vectors(symbols, ["測試"] * len(symbols))

    assert current[0] == pytest.approx(0.10)
    assert current[1] == pytest.approx(0.20)
    assert np.isnan(current[2])
    assert np.isnan(current[3])
    assert np.isnan(current[4])


def test_numpy_limit_pct_vectors_do_not_apply_a_share_st_rule_to_taiwan(monkeypatch):
    symbol = "2330.TWSE"
    _patch_taiwan_master(monkeypatch, {symbol: _taiwan_instrument(symbol)})

    legacy, current = numpy_limit_pct_vectors([symbol, "600001.SH"], ["ST測試", "*ST主板"])

    assert legacy[0] == pytest.approx(0.10)
    assert current[0] == pytest.approx(0.10)
    assert legacy[1] == pytest.approx(0.05)


def test_numpy_limit_pct_vectors_skip_security_master_for_a_shares(monkeypatch):
    def _explode():
        raise AssertionError("Security Master must not load for A-share-only symbols")

    monkeypatch.setattr("app.taiwan.universe.get_security_master", _explode)

    legacy, current = numpy_limit_pct_vectors(["600001.SH", "300001.SZ"], ["*ST主板", "普通"])

    assert current.tolist() == [0.10, 0.20]
    assert legacy.tolist() == [0.05, 0.20]


def test_polars_null_and_numpy_nan_agree_on_taiwan_no_limit(monkeypatch):
    """Pins what the two variants now share, and where they deliberately part.

    Shared: an instrument with no limit produces no limit price at all --
    null from polars_limit_price, NaN from numpy_limit_price. The two absence
    markers are not interchangeable: polars_limit_price casts to Int64 and
    rejects a NaN percentage, so the Polars side must stay null.
    Different: polars_price_limit_pct has no Security Master access and stays
    null for every Taiwan symbol, while numpy_limit_pct_vectors resolves the
    real per-instrument rate (0.10 / 0.20 / NaN).
    """
    ordinary = "2330.TWSE"
    foreign = "00646.TWSE"
    _patch_taiwan_master(monkeypatch, {
        ordinary: _taiwan_instrument(ordinary),
        foreign: _no_limit_etf(foreign),
    })
    symbols = [ordinary, foreign]

    polars_pct = pl.DataFrame({
        "symbol": symbols,
        "date": [date(2026, 9, 17)] * len(symbols),
        "is_st": [False] * len(symbols),
    }).with_columns(
        polars_price_limit_pct(pl.col("symbol"), pl.col("date"), pl.col("is_st")).alias("pct")
    )["pct"]
    _legacy, numpy_pct = numpy_limit_pct_vectors(symbols, ["測試"] * len(symbols))

    assert polars_pct.is_null().all()
    assert numpy_pct[0] == pytest.approx(0.10)
    assert np.isnan(numpy_pct[1])

    previous = np.array([100.0, 100.0], dtype=np.float64)
    frame = pl.DataFrame({"previous": previous, "limit": polars_pct})
    for up in (True, False):
        polars_price = frame.select(
            polars_limit_price(pl.col("previous"), pl.col("limit"), up=up).alias("price")
        )["price"]
        numpy_price = numpy_limit_price(previous, numpy_pct, up=up)
        assert polars_price.is_null().all()
        assert np.isfinite(numpy_price[0])
        assert np.isnan(numpy_price[1])

    with pytest.raises(pl.exceptions.InvalidOperationError):
        frame.with_columns(pl.col("limit").fill_null(float("nan"))).select(
            polars_limit_price(pl.col("previous"), pl.col("limit"), up=True)
        )


def test_numpy_limit_price_is_nan_where_the_limit_is_nan():
    previous = np.array([100.0, 100.0, np.nan], dtype=np.float64)
    limits = np.array([0.10, np.nan, 0.10], dtype=np.float64)

    up_price = numpy_limit_price(previous, limits, up=True)
    down_price = numpy_limit_price(previous, limits, up=False)

    assert up_price[0] == pytest.approx(110.0)
    assert down_price[0] == pytest.approx(90.0)
    assert np.isnan(up_price[1]) and np.isnan(down_price[1])
    assert np.isnan(up_price[2]) and np.isnan(down_price[2])


def test_market_matrix_never_marks_a_no_limit_taiwan_etf_as_limit_locked(tmp_path, monkeypatch):
    ordinary = "2330.TWSE"
    foreign = "00646.TWSE"
    _patch_taiwan_master(monkeypatch, {
        ordinary: _taiwan_instrument(ordinary),
        foreign: _no_limit_etf(foreign),
    })
    from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService

    root = tmp_path / "market"
    rows = [
        (date(2026, 9, 16), 100.0, 100.0, None, None),
        (date(2026, 9, 17), 110.0, 200.0, 100.0, 100.0),
    ]
    bars = pl.DataFrame([
        {
            "symbol": symbol,
            "date": trade_date,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "raw_close": close,
            "raw_high": close,
            "raw_low": close,
            "prev_close": prev_close,
            "volume": 1000.0,
        }
        for trade_date, stock_close, etf_close, stock_prev, etf_prev in rows
        for symbol, close, prev_close in (
            (ordinary, stock_close, stock_prev),
            (foreign, etf_close, etf_prev),
        )
    ], schema_overrides={"prev_close": pl.Float64})
    published = object.__new__(TaiwanEnrichedRefreshService)._add_limit_signals(bars)
    for (trade_date,), part in published.partition_by("date", as_dict=True).items():
        partition = root / f"date={trade_date.isoformat()}"
        partition.mkdir(parents=True)
        part.write_parquet(partition / "part.parquet")

    market = load_market_data_matrix_from_parquet(
        root,
        rows[0][0],
        rows[-1][0],
        field_columns={"raw_close", "price_limit_pct"},
        instruments=pl.DataFrame({
            "symbol": [ordinary, foreign],
            "name": ["台積電", "測試ETF"],
        }),
        cache_root=tmp_path / "cache",
    )

    column = {symbol: index for index, symbol in enumerate(market.symbols)}
    limits = market.field("price_limit_pct")
    assert limits[-1, column[ordinary]] == pytest.approx(0.10)
    assert np.isnan(limits[-1, column[foreign]])
    for symbol in (ordinary, foreign):
        sessions = published.filter(pl.col("symbol") == symbol).sort("date")
        assert market.limit_up_locked[:, column[symbol]].tolist() == sessions["signal_limit_up"].cast(pl.UInt8).to_list()
        assert market.limit_down_locked[:, column[symbol]].tolist() == sessions["signal_limit_down"].cast(pl.UInt8).to_list()
    assert market.limit_up_locked[-1, column[ordinary]] == 1
    assert market.limit_up_locked[-1, column[foreign]] == 0
    assert market.limit_down_locked[-1, column[foreign]] == 0
