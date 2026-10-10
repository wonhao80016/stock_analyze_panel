"""high_turnover_surge screens a session only under the backtest's turnover coverage rule (stage 4, D3).

The framework applies the share-capital rule to the fields a strategy declares in
required_coverage_fields, so the screen and the backtest judge a session the same way; the matrix
module still exposes the rule and its threshold to strategy files.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from app.backtest import matrix
from app.backtest.matrix import build_market_data_matrix
from app.strategy.builtin import high_turnover_surge
from app.strategy.engine import StrategyDataContext, StrategyEngine
from app.taiwan import share_capital_store

pytestmark = pytest.mark.usefixtures("deny_network")

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "app" / "strategy" / "builtin"
ENGINE = StrategyEngine(strategy_dirs=[BUILTIN_DIR])
NO_BASIC_FILTER = {"basic_filter": {"enabled": False}}
DAYS = 70
START = date(2024, 1, 1)
LAST = START + timedelta(days=DAYS - 1)


def _frame(turnover_on_last_day: dict[str, float | None]) -> pl.DataFrame:
    """Steady prices with a 4% rise on the last day; turnover only where given for that day."""
    rows = []
    for rank, (symbol, last_turnover) in enumerate(turnover_on_last_day.items()):
        for offset in range(DAYS):
            close = 50.0 + rank + 0.05 * math.sin(offset / 4.0)
            if offset == DAYS - 1:
                close = rows[-1]["close"] * 1.04
            rows.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "date": START + timedelta(days=offset),
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1_000_000.0,
                    "amount": 1e9,
                    "raw_close": close,
                    "raw_high": close,
                    "raw_low": close,
                    "turnover_rate": last_turnover if offset == DAYS - 1 else None,
                    "signal_limit_up": False,
                    "signal_limit_down": False,
                }
            )
    return (
        pl.DataFrame(rows, infer_schema_length=None)
        .with_columns(pl.col("turnover_rate").cast(pl.Float64))
        .sort(["symbol", "date"])
    )


def _context(frame: pl.DataFrame, as_of: date = LAST) -> StrategyDataContext:
    history = frame.filter(pl.col("date") <= as_of)
    return StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=as_of,
        current=history.filter(pl.col("date") == as_of),
        history=history,
    )


@pytest.fixture
def share_capital(tmp_path, monkeypatch):
    """Writes the current share-capital file; *tdrs* are the security master's TDRs."""
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "taiwan").mkdir()

    def write(issued: dict[str, int], tdrs: frozenset[str] = frozenset()) -> None:
        monkeypatch.setattr(share_capital_store, "_master_tdr_symbols", lambda: tdrs)
        pl.DataFrame(
            {
                "symbol": list(issued),
                "issued_shares": list(issued.values()),
                "report_date": ["2024-01-01"] * len(issued),
                "retrieved_at": ["2024-01-01T00:00:00"] * len(issued),
            },
            schema=share_capital_store.SCHEMA,
        ).write_parquet(tmp_path / "taiwan" / "share_capital.parquet")

    return write


def test_strategies_and_backtests_share_one_coverage_threshold():
    assert matrix.MIN_SESSION_COVERAGE == share_capital_store.MIN_SESSION_COVERAGE == 0.5


def test_the_matrix_rule_is_the_share_capital_rule_per_session(share_capital):
    share_capital({"HIT.TWSE": 100_000_000, "A.TWSE": 100_000_000, "B.TWSE": 100_000_000})
    frame = _frame({"HIT.TWSE": 2.1, "A.TWSE": None, "B.TWSE": 1.0, "0050.TWSE": None})
    market = build_market_data_matrix(frame, field_columns={"turnover_rate"})

    coverage = matrix.session_coverage(market, "turnover_rate")

    expected = share_capital_store.session_coverage(
        "turnover_rate", market.symbols, market.close, market.fields["turnover_rate"]
    )
    np.testing.assert_array_equal(coverage, expected)
    assert coverage[-1] == pytest.approx(2 / 3)
    assert matrix.session_coverage(market, "turnover_rate", slice(-1, None)).tolist() == [
        pytest.approx(2 / 3)
    ]


def test_a_refused_screen_reports_the_session_coverage():
    frame = _frame({"HIT.TWSE": 2.1, "A.TWSE": None, "B.TWSE": None, "C.TWSE": None})

    with pytest.raises(ValueError, match="只有 25% 的股票有換手率資料"):
        ENGINE.run("high_turnover_surge", _context(frame), overrides=NO_BASIC_FILTER)


def test_a_session_at_exactly_the_threshold_is_screened():
    frame = _frame({"HIT.TWSE": 2.1, "LOW.TWSE": None})

    result = ENGINE.run("high_turnover_surge", _context(frame), overrides=NO_BASIC_FILTER)

    assert [row["symbol"] for row in result.rows] == ["HIT.TWSE"]


@pytest.mark.parametrize(
    "pool", [("9103.TWSE",), ("0050.TWSE", "00878.TWSE")], ids=["tdr_only", "etf_only"]
)
def test_a_screen_without_any_stock_able_to_hold_turnover_is_refused_with_a_reason(
    share_capital, pool
):
    share_capital({"HIT.TWSE": 100_000_000, "9103.TWSE": 50_000_000}, tdrs=frozenset({"9103.TWSE"}))
    frame = _frame(dict.fromkeys(pool))

    with pytest.raises(ValueError, match="沒有可計算換手率的股票"):
        ENGINE.run("high_turnover_surge", _context(frame), overrides=NO_BASIC_FILTER)


def test_without_the_turnover_filter_a_pool_without_turnover_is_screened(share_capital):
    share_capital({"HIT.TWSE": 100_000_000}, tdrs=frozenset())
    frame = _frame({"0050.TWSE": None, "00878.TWSE": None})
    overrides = {**NO_BASIC_FILTER, "params": {"use_turnover_filter": False}}

    result = ENGINE.run("high_turnover_surge", _context(frame), overrides=overrides)

    assert sorted(row["symbol"] for row in result.rows) == ["0050.TWSE", "00878.TWSE"]


def test_the_framework_owns_the_rule_and_the_strategy_only_declares_its_field():
    source = Path(high_turnover_surge.__file__).read_text(encoding="utf-8")

    assert "app.taiwan" not in source
    assert "session_coverage(" not in source
    assert "MIN_SESSION_COVERAGE" not in source
    assert high_turnover_surge.MATRIX_STRATEGY.required_coverage_fields({}) == {"turnover_rate"}
