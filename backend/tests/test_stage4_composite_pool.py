"""Composite selection honours the requested pool exactly as its backtest does (stage 4).

A composite backtest loads only the pool's symbols, so its children rank and judge coverage on the
pool; selection runs the children on the pool too instead of the whole market.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from app.backtest.matrix import asset_mask_without, build_market_data_matrix
from app.backtest.strategy import (
    StrategyBacktestService,
    _basic_filter_for_asset,
    _unsupported_asset_type_symbols,
)
from app.strategy.engine import StrategyDataContext, StrategyEngine
from app.taiwan import share_capital_store, universe
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "app" / "strategy" / "builtin"
STOCKS = tuple(f"{1101 + index}.TWSE" for index in range(6))
ETFS = ("0050.TWSE", "0056.TWSE")
TYPES = {**dict.fromkeys(STOCKS, "stock"), **dict.fromkeys(ETFS, "etf")}
DAYS = [date(2026, 3, 2) + timedelta(days=offset) for offset in range(60)]
AS_OF = DAYS[-1]
POOL = [STOCKS[1], STOCKS[4], STOCKS[5], ETFS[0]]

CHILD = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {
    "id": %(sid)r,
    "name": %(sid)r,
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "scoring": %(scoring)r,
    "limit": 2,
}
EXECUTION_BACKEND = "matrix_native"


class _Child:
    def required_fields(self):
        return frozenset({"close", "amount"})

    def required_warmup_bars(self, params):
        return 5

    def compute_signals(self, market, params):
        entry = np.isfinite(market.close) & (market.volume >= %(min_volume)r)
        return make_signal_matrix(market.shape, entry=entry.astype(np.uint8))


MATRIX_STRATEGY = _Child()
"""

COMPOSITE = """
META = {
    "id": %(sid)r,
    "name": %(sid)r,
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": %(basic_filter)r,
    "params": [
        {"id": "merge_mode", "type": "select", "options": ["union", "intersect"], "default": "union"},
        {"id": "min_confirm", "type": "int", "default": 0},
    ],
    "limit": 0,
    "children": %(children)r,
}
EXECUTION_BACKEND = "composite"
"""


def _history() -> pl.DataFrame:
    rows = []
    specs = [
        *(
            (symbol, 30.0 + 8.0 * index, 1e8 * (index + 1), 2e6 if index % 2 else 1e6)
            for index, symbol in enumerate(STOCKS)
        ),
        *((symbol, 25.0 + 3.0 * index, 2e9, 2e6) for index, symbol in enumerate(ETFS)),
    ]
    for symbol, base, amount, volume in specs:
        for offset, day in enumerate(DAYS):
            close = base + 0.01 * offset
            rows.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "date": day,
                    "open": close,
                    "high": close + 0.1,
                    "low": close - 0.1,
                    "close": close,
                    "raw_close": close,
                    "volume": volume,
                    "amount": amount,
                }
            )
    return pl.DataFrame(rows).sort(["symbol", "date"])


def _context(history: pl.DataFrame, as_of: date = AS_OF, types=TYPES) -> StrategyDataContext:
    history = history.filter(pl.col("date") <= as_of)
    return StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=as_of,
        current=history.filter(pl.col("date") == as_of),
        history=history,
        symbol_asset_types=types,
    )


def _blend_engine(tmp_path: Path) -> StrategyEngine:
    child_dir = tmp_path / "custom"
    composite_dir = tmp_path / "composite"
    child_dir.mkdir()
    composite_dir.mkdir()
    children = {
        "by_amount": {"scoring": {"amount": 1.0}, "min_volume": 0.0},
        "by_close": {"scoring": {"close": 1.0}, "min_volume": 1.5e6},
    }
    for sid, spec in children.items():
        (child_dir / f"{sid}.py").write_text(CHILD % {"sid": sid, **spec}, encoding="utf-8")
    refs = [
        {"strategy_id": "by_amount", "weight": 2.0},
        {"strategy_id": "by_close", "weight": 1.0},
    ]
    (composite_dir / "blend.py").write_text(
        COMPOSITE % {"sid": "blend", "basic_filter": {"price_min": 20}, "children": refs},
        encoding="utf-8",
    )
    engine = StrategyEngine(strategy_dirs=[child_dir, composite_dir])
    assert engine.load_errors() == []
    return engine


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


def _backtest_scores(engine: StrategyEngine, history: pl.DataFrame) -> dict[str, float]:
    """Composite entry scores on AS_OF over *history*, the symbols a backtest of them loads."""
    service = StrategyBacktestService(None, engine)
    blend = engine.get("blend")
    params = engine.resolve_params(blend)
    basic_filter = _basic_filter_for_asset(service._effective_basic_filter(blend, {}), "stock")
    plan, resolved = service._resolve_composite_feature_plan(
        blend, params=params, basic_filter=basic_filter, overrides={}, asset_type="stock"
    )
    market = build_market_data_matrix(history, field_columns=set(plan.matrix_columns))
    excluded = _unsupported_asset_type_symbols(market.symbols, blend.meta["asset_types"])
    signals = service._generate_composite_signal_matrix(
        resolved,
        market,
        str(params["merge_mode"]),
        int(params["min_confirm"]),
        250,
        {},
        asset_mask=asset_mask_without(market.symbols, excluded),
    )
    target = market.timestamp_labels.index(str(AS_OF))
    return {
        market.symbols[asset]: float(signals.score[target, asset])
        for asset in np.flatnonzero(signals.entry[target] != 0)
    }


def test_composite_selection_on_a_pool_scores_what_a_backtest_of_the_pool_scores(tmp_path, master):
    engine = _blend_engine(tmp_path)
    history = _history()

    selected = engine.run("blend", _context(history), pool=POOL)
    backtest = _backtest_scores(engine, history.filter(pl.col("symbol").is_in(POOL)))

    assert set(backtest) == {STOCKS[1], STOCKS[4], STOCKS[5]}
    assert set(selected.scores) == set(backtest)
    for symbol, score in backtest.items():
        assert selected.scores[symbol] == pytest.approx(score, abs=1e-3), symbol
    assert [row["symbol"] for row in selected.rows] == sorted(
        backtest, key=lambda symbol: (-backtest[symbol], symbol)
    )


def test_run_all_restricts_every_strategy_to_the_pool(tmp_path, master):
    engine = _blend_engine(tmp_path)

    results = engine.run_all(
        _context(_history()), strategy_ids=["by_amount", "blend"], pool=POOL[:2]
    )

    assert {row["symbol"] for row in results["by_amount"].rows} == set(POOL[:2])
    assert {row["symbol"] for row in results["blend"].rows} == set(POOL[:2])


COVERED = ("2301.TWSE", "2302.TWSE", "2303.TWSE")
UNCOVERED = ("2304.TWSE", "2305.TWSE")
TDR = "9103.TWSE"
TURNOVER_DAYS = 80
TURNOVER_START = date(2024, 1, 1)
TURNOVER_LAST = TURNOVER_START + timedelta(days=TURNOVER_DAYS - 1)


def _turnover_market() -> pl.DataFrame:
    """Every symbol rises 4% on the last day; only COVERED hold turnover on it."""
    rows = []
    for rank, symbol in enumerate((*COVERED, *UNCOVERED, TDR)):
        for offset in range(TURNOVER_DAYS):
            close = 50.0 + rank + 0.05 * math.sin(offset / 4.0)
            if offset == TURNOVER_DAYS - 1:
                close = rows[-1]["close"] * 1.04
            last = offset == TURNOVER_DAYS - 1
            rows.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "date": TURNOVER_START + timedelta(days=offset),
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1_000_000.0,
                    "amount": 1e9,
                    "raw_close": close,
                    "raw_high": close,
                    "raw_low": close,
                    "turnover_rate": 2.5 if last and symbol in COVERED else None,
                    "signal_limit_up": False,
                    "signal_limit_down": False,
                }
            )
    return (
        pl.DataFrame(rows, infer_schema_length=None)
        .with_columns(pl.col("turnover_rate").cast(pl.Float64))
        .sort(["symbol", "date"])
    )


@pytest.fixture
def share_capital(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "taiwan").mkdir()
    monkeypatch.setattr(share_capital_store, "_master_tdr_symbols", lambda: frozenset({TDR}))
    issued = dict.fromkeys([*COVERED, *UNCOVERED, TDR], 100_000_000)
    pl.DataFrame(
        {
            "symbol": list(issued),
            "issued_shares": list(issued.values()),
            "report_date": ["2024-01-01"] * len(issued),
            "retrieved_at": ["2024-01-01T00:00:00"] * len(issued),
        },
        schema=share_capital_store.SCHEMA,
    ).write_parquet(tmp_path / "taiwan" / "share_capital.parquet")


def _turnover_engine(tmp_path: Path) -> StrategyEngine:
    composite_dir = tmp_path / "composite"
    composite_dir.mkdir()
    refs = [{"strategy_id": "high_turnover_surge", "weight": 1.0}]
    (composite_dir / "turnover_combo.py").write_text(
        COMPOSITE % {"sid": "turnover_combo", "basic_filter": {"enabled": False}, "children": refs},
        encoding="utf-8",
    )
    engine = StrategyEngine(strategy_dirs=[BUILTIN_DIR, composite_dir])
    assert engine.load_errors() == []
    return engine


def test_a_composite_child_judges_turnover_coverage_on_the_pool(tmp_path, share_capital):
    engine = _turnover_engine(tmp_path)
    types = dict.fromkeys((*COVERED, *UNCOVERED, TDR), "stock")
    context = _context(_turnover_market(), TURNOVER_LAST, types)

    whole_market = engine.run("turnover_combo", context)
    with pytest.raises(ValueError) as tdr_only:
        engine.run("turnover_combo", context, pool=[TDR])
    with pytest.raises(ValueError) as uncovered:
        engine.run("turnover_combo", context, pool=list(UNCOVERED))

    assert {row["symbol"] for row in whole_market.rows} == set(COVERED)
    assert str(tdr_only.value).startswith(
        f"子策略「高換手拉升」無法執行：{TURNOVER_LAST.isoformat()} 所選範圍內沒有可計算換手率的股票"  # noqa: RUF001
    )
    assert str(uncovered.value).startswith(
        f"子策略「高換手拉升」無法執行：{TURNOVER_LAST.isoformat()} 只有 0% 的股票有換手率資料"  # noqa: RUF001
    )
