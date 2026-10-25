from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest

from app.backtest.matrix import load_market_data_matrix_from_parquet
from app.indicators import pipeline
from app.price_limits import numpy_limit_pct_vector
from app.taiwan.universe.models import TaiwanInstrument


def test_limit_down_recovery_uses_raw_low_under_later_ex_div():
    """除权事件之后重算历史时, 跌停翻红"曾触及跌停"必须用原始价 low 判断。

    day2 (历史日): 原始 low 9.30 未触及跌停价 9.00, 不应触发;
    但 day3 除权 (ex_factor=2) 使 day2 前复权 low 变为 4.65,
    若误用复权 low 对比原始口径跌停价会误报。
    day2 收盘 10.05 高于昨收, 只差 low 未触及。
    day3 (除权日, 最新日不复权): 涨跌停基准切换为前复权昨收 5.025 → 跌停价 4.53 (台股跳动单位),
    原始 low 4.52 触及跌停, 收盘 5.10 高于基准昨收 5.025 → 跌停翻红。
    """
    raw = pl.DataFrame({
        "symbol": ["2330.TWSE"] * 3,
        "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
        "open": [10.00, 9.60, 4.60],
        "high": [10.10, 10.10, 5.15],
        "low": [9.90, 9.30, 4.52],
        "close": [10.00, 10.05, 5.10],
        "volume": [10000.0, 10000.0, 10000.0],
        "amount": [1.0e7, 1.0e7, 1.0e7],
    })
    factors = pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "trade_date": [date(2024, 1, 4)],
        "ex_factor": [2.0],
    })
    instruments = pl.DataFrame({
        "symbol": ["2330.TWSE"],
        "name": ["普通股"],
        "float_shares": [1.0e8],
    })

    df = pipeline.compute_enriched(raw, factors=factors, instruments=instruments)

    day2 = df.filter(pl.col("date") == date(2024, 1, 3))
    assert day2["signal_limit_down_recovery"][0] is False
    day3 = df.filter(pl.col("date") == date(2024, 1, 4))
    assert day3["signal_limit_down_recovery"][0] is True


# ── Taiwan limit rules in compute_limit_signals ──────────


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


def _two_sessions(prev_close: float, close: float, *, symbol: str) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [symbol, symbol],
        "date": [date(2026, 9, 16), date(2026, 9, 17)],
        "open": [prev_close, prev_close],
        "high": [prev_close, close],
        "low": [prev_close, prev_close],
        "close": [prev_close, close],
        "raw_close": [prev_close, close],
        "raw_high": [prev_close, close],
        "raw_low": [prev_close, prev_close],
        "volume": [1000.0, 1000.0],
    })


def _last_flags(rows: pl.DataFrame) -> dict:
    out = pipeline.compute_limit_signals(
        rows, pl.DataFrame({"symbol": rows["symbol"].unique().to_list()}),
        needed={"signal_limit_up", "signal_limit_down"},
    )
    return out.sort("date").row(-1, named=True)


def test_compute_limit_signals_taiwan_ordinary_stock_uses_10pct(monkeypatch):
    symbol = "2330.TWSE"
    _patch_taiwan_master(monkeypatch, {symbol: _taiwan_instrument(symbol)})

    assert _last_flags(_two_sessions(100.0, 111.0, symbol=symbol))["signal_limit_up"] is True


def test_compute_limit_signals_taiwan_leveraged_etf_needs_20pct_for_a_limit_up(monkeypatch):
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
    assert _last_flags(_two_sessions(100.0, 115.0, symbol=symbol))["signal_limit_up"] is False


def test_compute_limit_signals_taiwan_no_limit_etf_stays_null(monkeypatch):
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
    flags = _last_flags(_two_sessions(100.0, 200.0, symbol=symbol))

    assert flags["signal_limit_up"] is None
    assert flags["signal_limit_down"] is None


@pytest.mark.parametrize("symbol", ["3008.TWSE", "6889.TPEX"])
def test_compute_limit_signals_taiwan_growth_star_prefixed_code_not_misjudged(monkeypatch, symbol):
    _patch_taiwan_master(monkeypatch, {symbol: _taiwan_instrument(symbol)})
    # +15%: above the real 10% ordinary-stock cap. The pre-fix code matched
    # these code prefixes against the A-share growth/STAR board (20%) and
    # silently stayed quiet here.
    assert _last_flags(_two_sessions(100.0, 115.0, symbol=symbol))["signal_limit_up"] is True


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


def test_numpy_limit_pct_vector_resolves_taiwan_per_instrument(monkeypatch):
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

    current = numpy_limit_pct_vector(symbols)

    assert current[0] == pytest.approx(0.10)
    assert current[1] == pytest.approx(0.20)
    assert np.isnan(current[2])
    assert np.isnan(current[3])
    assert np.isnan(current[4])


def test_numpy_limit_pct_vector_has_no_limit_for_non_taiwan_symbols(monkeypatch):
    def _explode():
        raise AssertionError("Security Master must not load for non-Taiwan symbols")

    monkeypatch.setattr("app.taiwan.universe.get_security_master", _explode)

    assert np.isnan(numpy_limit_pct_vector(["600001.SH", "300001.SZ"])).all()


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
