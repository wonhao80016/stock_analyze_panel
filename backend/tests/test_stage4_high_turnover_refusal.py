"""high_turnover_surge on Taiwan turnover (stage 4, D3).

Turnover exists only from the day each session was published, so a date or backtest window
without it cannot be screened: the strategy must refuse with a reason instead of reporting no
picks. The default threshold is 2% of issued shares net of private placements.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from app.backtest.engine import SimResult
from app.backtest.matrix import build_market_data_matrix
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.strategy.builtin import high_turnover_surge
from app.strategy.engine import StrategyDataContext, StrategyEngine
from app.taiwan import share_capital_store

pytestmark = pytest.mark.usefixtures("deny_network")

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "app" / "strategy" / "builtin"
ENGINE = StrategyEngine(strategy_dirs=[BUILTIN_DIR])
NO_BASIC_FILTER = {"basic_filter": {"enabled": False}}
DAYS = 80
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
    return pl.DataFrame(rows, infer_schema_length=None).sort(["symbol", "date"])


def _with_turnover(frame: pl.DataFrame, cells: dict[tuple[str, date], float]) -> pl.DataFrame:
    """*frame* with turnover_rate also set on the given (symbol, date) cells."""
    patch = pl.DataFrame(
        [{"symbol": symbol, "date": day, "_cell": value} for (symbol, day), value in cells.items()]
    )
    return (
        frame.join(patch, on=["symbol", "date"], how="left")
        .with_columns(pl.coalesce("turnover_rate", "_cell").alias("turnover_rate"))
        .drop("_cell")
        .sort(["symbol", "date"])
    )


def _rise(frame: pl.DataFrame, symbol: str, day: date) -> pl.DataFrame:
    """*frame* with *symbol* 4% above its previous close on *day*."""
    prices = ("open", "high", "low", "close", "raw_close", "raw_high", "raw_low")
    on_day = (pl.col("symbol") == symbol) & (pl.col("date") == day)
    return frame.with_columns(
        pl.when(on_day).then(pl.col(name) * 1.04).otherwise(pl.col(name)).alias(name)
        for name in prices
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


def _context(frame: pl.DataFrame, as_of: date) -> StrategyDataContext:
    history = frame.filter(pl.col("date") <= as_of)
    return StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=as_of,
        current=history.filter(pl.col("date") == as_of),
        history=history,
    )


def test_the_default_threshold_is_two_percent_of_issued_shares_net_of_private_placements():
    params = {p["id"]: p for p in high_turnover_surge.META["params"]}

    assert params["min_turnover"]["default"] == 2.0
    assert "2%" in high_turnover_surge.META["description"]
    assert "私募" in high_turnover_surge.META["description"]


def test_the_newest_session_picks_turnover_above_the_default_threshold():
    frame = _frame({"HIT.TWSE": 2.1, "LOW.TWSE": 1.9, "ETF.TWSE": None})

    result = ENGINE.run("high_turnover_surge", _context(frame, LAST), overrides=NO_BASIC_FILTER)

    assert [row["symbol"] for row in result.rows] == ["HIT.TWSE"]


def test_a_date_without_turnover_is_refused_with_a_reason():
    frame = _frame({"HIT.TWSE": 2.1, "LOW.TWSE": 1.9})
    historical = LAST - timedelta(days=1)

    with pytest.raises(ValueError, match="換手率"):
        ENGINE.run("high_turnover_surge", _context(frame, historical), overrides=NO_BASIC_FILTER)


def test_a_batch_on_a_date_without_turnover_reports_the_refusal_and_runs_the_rest():
    frame = _frame({"HIT.TWSE": 2.1, "LOW.TWSE": 1.9})
    failures: dict[str, str] = {}

    results = ENGINE.run_all(
        _context(frame, LAST - timedelta(days=1)),
        overrides_map={"high_turnover_surge": NO_BASIC_FILTER, "trend_breakout": NO_BASIC_FILTER},
        strategy_ids=["high_turnover_surge", "trend_breakout"],
        skip_failed=True,
        failures=failures,
    )

    assert "換手率" in failures["high_turnover_surge"]
    assert "trend_breakout" in results


def test_one_stray_turnover_on_an_old_date_does_not_make_that_date_screenable():
    old = LAST - timedelta(days=10)
    frame = _with_turnover(
        _frame({"HIT.TWSE": 2.1, "LOW.TWSE": 1.9, "MID.TWSE": 2.5}), {("HIT.TWSE", old): 0.5}
    )

    with pytest.raises(ValueError, match="換手率"):
        ENGINE.run("high_turnover_surge", _context(frame, old), overrides=NO_BASIC_FILTER)


def test_without_share_capital_every_listed_stock_counts_toward_turnover_coverage():
    frame = _frame({"HIT.TWSE": 2.1, "A.TWSE": None, "B.TWSE": None, "C.TWSE": None})

    with pytest.raises(ValueError, match="換手率"):
        ENGINE.run("high_turnover_surge", _context(frame, LAST), overrides=NO_BASIC_FILTER)


def test_etfs_and_tdrs_without_a_turnover_denominator_do_not_count_toward_coverage(share_capital):
    share_capital({"HIT.TWSE": 100_000_000, "9103.TWSE": 50_000_000}, tdrs=frozenset({"9103.TWSE"}))
    frame = _frame({"HIT.TWSE": 2.1, "0050.TWSE": None, "00878.TWSE": None, "9103.TWSE": None})

    result = ENGINE.run("high_turnover_surge", _context(frame, LAST), overrides=NO_BASIC_FILTER)

    assert [row["symbol"] for row in result.rows] == ["HIT.TWSE"]


def test_the_turnover_filter_switched_off_does_not_need_turnover():
    frame = _frame({"HIT.TWSE": None, "LOW.TWSE": None})
    market = build_market_data_matrix(
        frame, field_columns=high_turnover_surge.MATRIX_STRATEGY.required_fields()
    )

    signals = high_turnover_surge.MATRIX_STRATEGY.compute_signals(
        market, {"use_turnover_filter": False}
    )

    assert signals.entry[-1].all()


class _StrategyEngineStub:
    def __init__(self, strategy) -> None:
        self.strategy = strategy

    def get(self, strategy_id: str):
        return self.strategy


class _BacktestEngineStub:
    def __init__(self, panel: pl.DataFrame) -> None:
        self.panel = panel
        self.repo = SimpleNamespace(store=SimpleNamespace(data_dir=None))
        self.simulated = False

    def load_panel_for_backtest(self, symbols, start, end, feature_plan, asset_type="stock"):
        return self.panel

    def load_market_data_matrix_for_backtest(
        self, symbols, start, end, feature_plan, asset_type="stock", **kwargs
    ):
        fields = (
            set(feature_plan.base_columns)
            | set(feature_plan.instrument_columns)
            | set(feature_plan.matrix_columns)
            | {"turnover_rate"}
        )
        return build_market_data_matrix(self.panel, field_columns=fields)

    def simulate_market_matrix(
        self, matrix, config, progress_cb=None, cancel_event=None, options=None
    ):
        self.simulated = True
        return SimResult(
            equity_curve=[{"date": "2024-01-01", "value": config.initial_capital}],
            drawdown_curve=[{"date": "2024-01-01", "value": 0.0}],
            trades=[],
            per_symbol_stats=[],
            stats={"total_return": 0.0, "n_trades": 0},
        )

    def simulate_independent_market_matrix(self, matrix, raw_candidates, config, *args, **kwargs):
        return self.simulate_market_matrix(matrix, config)


def _backtest(
    panel: pl.DataFrame,
    end: date,
    *,
    start: date | None = None,
    mode: str = "position",
    strategy_id: str = "high_turnover_surge",
    strategies=None,
):
    """The stub loads the whole panel, as the real loader does past *end* in full mode."""
    if strategies is None:
        strategies = _StrategyEngineStub(
            dataclasses.replace(ENGINE.get("high_turnover_surge"), basic_filter={"enabled": False})
        )
    engine = _BacktestEngineStub(panel)
    service = StrategyBacktestService(engine=engine, strategy_engine=strategies)
    result = service.run(
        StrategyBacktestConfig(
            strategy_id=strategy_id,
            symbols=None,
            start=start or START + timedelta(days=30),
            end=end,
            matching="close_t",
            mode=mode,
        )
    )
    return result, engine


def test_a_backtest_without_turnover_history_is_refused_with_a_reason():
    panel = _frame({"HIT": 2.1, "LOW": 1.9}).filter(pl.col("date") < LAST)

    result, engine = _backtest(panel, LAST - timedelta(days=1))

    assert result.error is not None
    assert "換手率" in result.error
    assert not engine.simulated


def test_a_backtest_whose_window_mostly_predates_turnover_is_refused():
    result, engine = _backtest(_frame({"HIT": 2.1, "LOW": 1.9}), LAST)

    assert result.error is not None
    assert "換手率" in result.error
    assert (START + timedelta(days=30)).isoformat() in result.error
    assert not engine.simulated


def test_a_full_mode_backtest_ending_before_turnover_is_refused_although_it_loads_that_day():
    result, engine = _backtest(
        _frame({"HIT": 2.1, "LOW": 1.9}), LAST - timedelta(days=1), mode="full"
    )

    assert result.error is not None
    assert "換手率" in result.error
    assert (START + timedelta(days=30)).isoformat() in result.error
    assert not engine.simulated


def test_a_backtest_over_a_date_with_one_stray_turnover_is_refused():
    old = LAST - timedelta(days=10)
    panel = _with_turnover(_frame({"HIT": 2.1, "LOW": 1.9, "MID": 2.5}), {("HIT", old): 2.5})

    result, engine = _backtest(panel, old, start=old)

    assert result.error is not None
    assert "換手率" in result.error
    assert old.isoformat() in result.error
    assert not engine.simulated


def test_a_backtest_whose_formal_range_has_turnover_runs_although_its_warmup_has_none():
    start = LAST - timedelta(days=5)
    frame = _frame({"HIT": 2.1, "LOW": 1.9})
    cells = {
        (symbol, START + timedelta(days=offset)): 1.0
        for symbol in ("HIT", "LOW")
        for offset in range((start - START).days, DAYS - 1)
    }

    result, engine = _backtest(_with_turnover(frame, cells), LAST, start=start)

    assert result.error is None
    assert engine.simulated


def test_a_full_mode_backtest_is_judged_on_its_formal_sessions_not_on_the_loaded_tail():
    """The full-mode loader reads past *end*; the newest loaded session lacks turnover."""
    start, end = LAST - timedelta(days=5), LAST - timedelta(days=1)
    cells = {
        (symbol, start + timedelta(days=offset)): 2.5
        for symbol in ("HIT", "LOW", "MID")
        for offset in range((end - start).days + 1)
    }
    panel = _with_turnover(_frame({"HIT": 2.1, "LOW": None, "MID": None}), cells)

    result, engine = _backtest(_rise(panel, "HIT", end), end, start=start, mode="full")

    assert result.error is None, result.error
    assert engine.simulated


def test_a_backtest_runs_over_a_session_on_which_no_eligible_stock_traded(share_capital):
    share_capital({"X": 100_000_000})
    prev = LAST - timedelta(days=1)
    panel = _with_turnover(
        _frame({"X": None, "0050": None}).with_columns(pl.col("turnover_rate").cast(pl.Float64)),
        {("X", prev): 2.5},
    ).filter(~((pl.col("symbol") == "X") & (pl.col("date") == LAST)))

    result, engine = _backtest(_rise(panel, "X", prev), LAST, start=prev)

    assert result.error is None, result.error
    assert engine.simulated


def test_a_backtest_counts_only_stocks_with_a_turnover_denominator(share_capital):
    share_capital({"HIT": 100_000_000, "9103": 50_000_000}, tdrs=frozenset({"9103"}))
    panel = _frame({"HIT": 2.1, "0050": None, "00878": None, "9103": None})

    result, engine = _backtest(panel, LAST, start=LAST)

    assert result.error is None
    assert engine.simulated


def test_a_backtest_without_share_capital_counts_every_listed_stock():
    panel = _frame({"HIT": 2.1, "0050": None, "00878": None, "9103": None})

    result, engine = _backtest(panel, LAST, start=LAST)

    assert result.error is not None
    assert "換手率" in result.error
    assert LAST.isoformat() in result.error
    assert not engine.simulated


COMPOSITE = """
META = {
    "id": "turnover_combo",
    "name": "turnover combo",
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {"enabled": False},
    "children": [{"strategy_id": "high_turnover_surge", "weight": 1.0}],
    "limit": 0,
}
EXECUTION_BACKEND = "composite"
"""


def test_a_composite_backtest_checks_the_turnover_its_children_need(tmp_path):
    composite_dir = tmp_path / "composite"
    composite_dir.mkdir()
    (composite_dir / "turnover_combo.py").write_text(COMPOSITE, encoding="utf-8")
    strategies = StrategyEngine(strategy_dirs=[BUILTIN_DIR, composite_dir])

    result, engine = _backtest(
        _frame({"HIT": 2.1, "LOW": 1.9}), LAST, strategy_id="turnover_combo", strategies=strategies
    )

    assert result.error is not None
    assert "換手率" in result.error
    assert (START + timedelta(days=30)).isoformat() in result.error
    assert not engine.simulated


TWSE_STOCKS = ("2330.TWSE", "2317.TWSE", "2454.TWSE")
TPEX_STOCKS = ("8069.TPEX", "5483.TPEX")


def _exchange_frame(tpex_turnover: float | None, twse_only_days: int = 1) -> pl.DataFrame:
    """Every stock rises on the last day; TWSE stocks have turnover on the last *twse_only_days* days."""
    frame = _frame({**dict.fromkeys(TWSE_STOCKS, 2.1), **dict.fromkeys(TPEX_STOCKS, tpex_turnover)})
    earlier = {
        (symbol, LAST - timedelta(days=offset)): 1.0
        for symbol in TWSE_STOCKS
        for offset in range(1, twse_only_days)
    }
    return _with_turnover(frame, earlier) if earlier else frame


def test_a_session_with_one_exchange_below_coverage_passes_with_an_exchange_warning():
    result, engine = _backtest(_exchange_frame(None), LAST, start=LAST)

    assert result.error is None, result.error
    assert engine.simulated
    assert result.stats["exchange_coverage_warnings"] == [
        {
            "field": "turnover_rate",
            "exchange": "TPEX",
            "threshold": 0.5,
            "sessions": [LAST.isoformat()],
            "session_count": 1,
        }
    ]


def test_the_exchange_warning_lists_the_first_sessions_and_counts_them_all():
    start = LAST - timedelta(days=7)

    result, engine = _backtest(_exchange_frame(None, twse_only_days=8), LAST, start=start)

    assert result.error is None, result.error
    assert engine.simulated
    [warning] = result.stats["exchange_coverage_warnings"]
    assert warning["exchange"] == "TPEX"
    assert warning["session_count"] == 8
    assert warning["sessions"] == [
        (start + timedelta(days=offset)).isoformat() for offset in range(5)
    ]


def test_sessions_covered_on_both_exchanges_carry_no_exchange_warning():
    result, engine = _backtest(_exchange_frame(2.1), LAST, start=LAST)

    assert result.error is None, result.error
    assert engine.simulated
    assert result.stats["exchange_coverage_warnings"] == []


@pytest.mark.parametrize("pool", [("9103",), ("0050", "00878")], ids=["tdr_only", "etf_only"])
def test_a_pool_without_any_stock_able_to_hold_turnover_is_refused_with_a_reason(
    share_capital, pool
):
    share_capital({"HIT": 100_000_000, "9103": 50_000_000}, tdrs=frozenset({"9103"}))
    panel = _frame(dict.fromkeys(pool)).with_columns(pl.col("turnover_rate").cast(pl.Float64))

    result, engine = _backtest(panel, LAST, start=LAST)

    assert result.error is not None
    assert "換手率" in result.error
    assert "沒有可計算換手率" in result.error
    assert not engine.simulated
