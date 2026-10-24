"""Coverage refusals name fields by their display labels and judge the strategy's asset scope (stage 4).

Selection, the realtime monitor and backtests show these messages on the traditional-Chinese UI, so
they use the shared field labels instead of raw field ids; the backtest counts the instruments the
strategy may pick, as selection does.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from app.backtest.engine import SimResult
from app.backtest.matrix import build_market_data_matrix
from app.backtest.strategy import (
    StrategyBacktestConfig,
    StrategyBacktestService,
    _entry_coverage_check,
    _low_scoring_coverage_error,
)
from app.strategy.engine import StrategyDataContext, StrategyEngine
from app.strategy.field_labels import field_label
from app.strategy.monitor import MonitorRuleEngine
from app.taiwan import share_capital_store, universe
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "app" / "strategy" / "builtin"
ENGINE = StrategyEngine(strategy_dirs=[BUILTIN_DIR])
NO_BASIC_FILTER = {"basic_filter": {"enabled": False}}
DAYS = 70
START = date(2024, 1, 1)
LAST = START + timedelta(days=DAYS - 1)
SIMPLIFIED = ("进", "场", "数", "据", "标", "请", "选", "设", "间", "该", "评", "过", "滤", "这")


def _frame(
    values_on_last_day: dict[str, float | None], field: str = "turnover_rate"
) -> pl.DataFrame:
    """Steady prices with a 4% rise on the last day; *field* only where given for that day."""
    rows = []
    for rank, (symbol, last_value) in enumerate(values_on_last_day.items()):
        for offset in range(DAYS):
            close = 50.0 + rank + 0.05 * math.sin(offset / 4.0)
            if offset == DAYS - 1:
                close = rows[-1]["close"] * 1.04
            row = {
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
                "turnover_rate": None,
                "signal_limit_up": False,
                "signal_limit_down": False,
            }
            row[field] = last_value if offset == DAYS - 1 else None
            rows.append(row)
    return (
        pl.DataFrame(rows, infer_schema_length=None)
        .with_columns(pl.col("turnover_rate").cast(pl.Float64), pl.col("amount").cast(pl.Float64))
        .sort(["symbol", "date"])
    )


def _context(frame: pl.DataFrame, symbol_asset_types=None) -> StrategyDataContext:
    return StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=LAST,
        current=frame.filter(pl.col("date") == LAST),
        history=frame,
        symbol_asset_types=symbol_asset_types,
    )


def _assert_traditional_without_ids(message: str) -> None:
    assert "turnover_rate" not in message and "amount" not in message
    assert not [char for char in SIMPLIFIED if char in message], message


@pytest.fixture
def share_capital(tmp_path, monkeypatch):
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


def test_field_labels_are_the_monitor_s_labels_with_the_id_as_fallback():
    assert field_label("turnover_rate") == "換手率"
    assert field_label("amount") == "成交額"
    assert field_label("my_factor") == "my_factor"
    text = MonitorRuleEngine._format_conditions_text(
        {"logic": "and"}, [{"field": "turnover_rate", "op": ">=", "value": 2}]
    )
    assert text == "換手率大於等於2"


def test_a_thin_session_is_refused_with_the_label_and_the_share_of_stocks():
    frame = _frame({"HIT.TWSE": 2.1, "A.TWSE": None, "B.TWSE": None, "C.TWSE": None})

    with pytest.raises(ValueError) as refused:
        ENGINE.run("high_turnover_surge", _context(frame), overrides=NO_BASIC_FILTER)

    assert str(refused.value) == (
        f"{LAST.isoformat()} 只有 25% 的股票有換手率資料（至少需 50%），"  # noqa: RUF001
        "無法依換手率選股，請改選有換手率資料的交易日"  # noqa: RUF001
    )
    _assert_traditional_without_ids(str(refused.value))


def test_a_scope_without_eligible_stocks_is_refused_naming_etfs_tdrs_and_suspensions(share_capital):
    share_capital({"HIT.TWSE": 100_000_000, "9103.TWSE": 50_000_000}, tdrs=frozenset({"9103.TWSE"}))

    with pytest.raises(ValueError) as refused:
        ENGINE.run(
            "high_turnover_surge", _context(_frame({"9103.TWSE": None})), overrides=NO_BASIC_FILTER
        )

    assert str(refused.value) == (
        f"{LAST.isoformat()} 所選範圍內沒有可計算換手率的股票"
        "（ETF、TDR 沒有換手率，停牌股當日沒有收盤價），請改選含普通股的標的"  # noqa: RUF001
    )
    _assert_traditional_without_ids(str(refused.value))


def _market(cells: dict[str, list[float | None]]):
    days = [START + timedelta(days=offset) for offset in range(3)]
    frame = pl.DataFrame(
        [
            {
                "symbol": symbol,
                "date": day,
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 1.0,
                "turnover_rate": value,
            }
            for symbol, values in cells.items()
            for day, value in zip(days, values, strict=True)
        ],
        schema_overrides={"turnover_rate": pl.Float64},
    )
    return build_market_data_matrix(frame, field_columns={"turnover_rate"}), days


def test_a_backtest_refusal_names_the_first_short_session_and_counts_them():
    market, days = _market({"A": [1.0, 1.0, None], "B": [1.0, None, None], "C": [1.0, None, None]})

    refusal, warnings = _entry_coverage_check(market, {"turnover_rate"}, np.ones(3, dtype=bool))

    assert refusal == (
        f"{days[1].isoformat()} 只有 33% 的股票有換手率資料"
        "（至少需 50%；回測區間內共 2 個交易日不足），"  # noqa: RUF001
        "無法依換手率回測，請把回測區間設在有換手率資料的交易日"  # noqa: RUF001
    )
    assert warnings == []
    _assert_traditional_without_ids(refusal)


def test_a_backtest_scope_without_eligible_instruments_is_refused():
    market, _days = _market({"A": [1.0, 1.0, 1.0], "B": [1.0, 1.0, 1.0]})

    refusal, _warnings = _entry_coverage_check(
        market, {"turnover_rate"}, np.ones(3, dtype=bool), np.zeros(2, dtype=bool)
    )

    assert refusal == (
        "回測區間內所選範圍沒有可計算換手率的股票"
        "（ETF、TDR 沒有換手率，停牌股停牌期間沒有收盤價），請改選含普通股的標的"  # noqa: RUF001
    )


def test_low_scoring_coverage_lists_labels_and_whole_percentages():
    message = _low_scoring_coverage_error({"turnover_rate": 1 / 3, "momentum_20d": 0.9})

    assert message == "以下評分欄位的資料覆蓋率低於 50%：換手率 33%"  # noqa: RUF001


CHILD = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {"id": "broken_child", "name": "斷線子策略", "asset_types": ["stock"], "timeframes": ["1d"]}
EXECUTION_BACKEND = "matrix_native"


class _Broken:
    def required_fields(self):
        return frozenset({"close"})

    def required_warmup_bars(self, params):
        return 1

    def compute_signals(self, market, params):
        raise ValueError("child data unavailable")


MATRIX_STRATEGY = _Broken()
"""

COMPOSITE = """
META = {
    "id": "blend",
    "name": "blend",
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {"enabled": False},
    "children": [{"strategy_id": "broken_child", "weight": 1.0}],
}
EXECUTION_BACKEND = "composite"
"""


def test_a_failing_composite_child_is_named_by_its_display_name(tmp_path):
    for name, source in (("custom", CHILD), ("composite", COMPOSITE)):
        (tmp_path / name).mkdir()
        sid = "broken_child" if name == "custom" else "blend"
        (tmp_path / name / f"{sid}.py").write_text(source, encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[tmp_path / "custom", tmp_path / "composite"])

    with pytest.raises(ValueError) as refused:
        engine.run("blend", _context(_frame({"HIT.TWSE": 1.0})))

    assert str(refused.value) == "子策略「斷線子策略」無法執行：child data unavailable"  # noqa: RUF001


# --------------------------------------------------------------------------------------------
# Backtest coverage counts the strategy's asset scope, as selection does
# --------------------------------------------------------------------------------------------

STOCKS = ("1101.TWSE", "1102.TWSE")
ETFS = ("0050.TWSE", "0056.TWSE", "00878.TWSE")
TYPES = {**dict.fromkeys(STOCKS, "stock"), **dict.fromkeys(ETFS, "etf")}

NEEDS_AMOUNT = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {"id": "needs_amount", "name": "needs amount", "asset_types": ["stock"], "limit": 0}
BASIC_FILTER = {"enabled": False}
EXECUTION_BACKEND = "matrix_native"


class _EveryBar:
    def required_fields(self):
        return frozenset({"close", "amount"})

    def required_coverage_fields(self, params):
        return frozenset({"amount"})

    def required_warmup_bars(self, params):
        return 1

    def compute_signals(self, market, params):
        return make_signal_matrix(market.shape, entry=np.ones(market.shape, dtype=np.uint8))


MATRIX_STRATEGY = _EveryBar()
"""


def _instrument(symbol: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    etf = TYPES[symbol] == "etf"
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=code,
        instrument_type=TYPES[symbol],
        listing_status="active",
        listing_date="2000-01-01",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="ETF" if etf else "股票",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-10-10",
        etf_category="equity" if etf else None,
        classification_source="official_metadata" if etf else None,
        underlying_scope="domestic" if etf else None,
    )


@pytest.fixture
def master(tmp_path, monkeypatch) -> TaiwanSecurityMaster:
    path = tmp_path / "security_master.parquet"
    pl.DataFrame([_instrument(symbol).to_dict() for symbol in TYPES]).write_parquet(path)
    loaded = TaiwanSecurityMaster(cache_path=path)
    assert loaded.load_cache()
    monkeypatch.setattr(universe, "_default_master", loaded)
    return loaded


class _BacktestEngineStub:
    def __init__(self, panel: pl.DataFrame) -> None:
        self.panel = panel
        self.repo = SimpleNamespace(store=SimpleNamespace(data_dir=None))
        self.simulated = False

    def load_market_data_matrix_for_backtest(
        self, symbols, start, end, feature_plan, asset_type="stock", **kwargs
    ):
        fields = (
            set(feature_plan.base_columns)
            | set(feature_plan.instrument_columns)
            | set(feature_plan.matrix_columns)
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


@pytest.mark.parametrize(
    ("stock_amount", "etf_amount", "refused"),
    [(1e9, None, False), (None, 1e9, True)],
    ids=["stocks_covered_etfs_not", "etfs_covered_stocks_not"],
)
def test_backtest_and_selection_judge_coverage_on_the_strategy_s_asset_scope(
    tmp_path, master, stock_amount, etf_amount, refused
):
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "needs_amount.py").write_text(NEEDS_AMOUNT, encoding="utf-8")
    strategies = StrategyEngine(strategy_dirs=[custom])
    frame = _frame(
        {**dict.fromkeys(STOCKS, stock_amount), **dict.fromkeys(ETFS, etf_amount)}, field="amount"
    )

    engine = _BacktestEngineStub(frame)
    result = StrategyBacktestService(engine=engine, strategy_engine=strategies).run(
        StrategyBacktestConfig(
            strategy_id="needs_amount",
            symbols=None,
            start=LAST,
            end=LAST,
            matching="close_t",
            mode="position",
        )
    )
    try:
        selected = strategies.run("needs_amount", _context(frame, TYPES))
        selection_error = None
    except ValueError as exc:
        selected, selection_error = None, str(exc)

    assert (result.error is not None) is refused, result.error
    assert (selection_error is not None) is refused, selection_error
    if refused:
        assert result.error.startswith(f"{LAST.isoformat()} 只有 0% 的股票有成交額資料")
        assert selection_error.startswith(f"{LAST.isoformat()} 只有 0% 的股票有成交額資料")
        assert not engine.simulated
    else:
        assert engine.simulated
        assert sorted(row["symbol"] for row in selected.rows) == sorted(STOCKS)


# --------------------------------------------------------------------------------------------
# Only turnover has an ETF/TDR exclusion; shares print rounded down
# --------------------------------------------------------------------------------------------


def test_a_scope_without_eligible_stocks_for_another_field_gets_the_neutral_refusal(
    tmp_path, master
):
    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "needs_amount.py").write_text(NEEDS_AMOUNT, encoding="utf-8")
    strategies = StrategyEngine(strategy_dirs=[custom])
    frame = _frame(dict.fromkeys(ETFS, 1e9), field="amount")

    with pytest.raises(ValueError) as refused:
        strategies.run("needs_amount", _context(frame, TYPES))
    backtest_refusal, _warnings = _entry_coverage_check(
        build_market_data_matrix(frame, field_columns={"amount"}),
        {"amount"},
        np.ones(DAYS, dtype=bool),
        np.zeros(len(ETFS), dtype=bool),
    )

    assert str(refused.value) == f"{LAST.isoformat()} 所選範圍內沒有可計算成交額的股票"
    assert backtest_refusal == "回測區間內所選範圍沒有可計算成交額的股票"


THIN = {f"{index:04d}.TWSE": (2.1 if index < 199 else None) for index in range(400)}


def test_a_share_just_below_the_minimum_prints_rounded_down_in_selection():
    with pytest.raises(ValueError) as refused:
        ENGINE.run("high_turnover_surge", _context(_frame(THIN)), overrides=NO_BASIC_FILTER)

    assert str(refused.value).startswith(
        f"{LAST.isoformat()} 只有 49% 的股票有換手率資料（至少需 50%）"  # noqa: RUF001
    )


def test_a_share_just_below_the_minimum_prints_rounded_down_in_backtests():
    market, days = _market({symbol: [value] * 3 for symbol, value in THIN.items()})

    refusal, _warnings = _entry_coverage_check(market, {"turnover_rate"}, np.ones(3, dtype=bool))

    assert refusal.startswith(f"{days[0].isoformat()} 只有 49% 的股票有換手率資料（至少需 50%；")  # noqa: RUF001
    assert _low_scoring_coverage_error({"turnover_rate": 199 / 400}) == (
        "以下評分欄位的資料覆蓋率低於 50%：換手率 49%"  # noqa: RUF001
    )
