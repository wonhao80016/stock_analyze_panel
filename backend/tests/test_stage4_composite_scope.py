"""Composite selection ranks the pool its backtest ranks, rejects ascending children and names a failing child."""

from __future__ import annotations

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
from app.taiwan import universe
from app.taiwan.universe import TaiwanSecurityMaster
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

STOCKS = tuple(f"{1101 + index}.TWSE" for index in range(6))
CHEAP = "1201.TWSE"
ETFS = ("0050.TWSE", "0056.TWSE", "00878.TWSE")
TYPES = {**dict.fromkeys((*STOCKS, CHEAP), "stock"), **dict.fromkeys(ETFS, "etf")}
DAYS = [date(2026, 3, 2) + timedelta(days=offset) for offset in range(60)]
AS_OF = DAYS[-1]
CHILD_LIMIT = 2

CHILD = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {
    "id": %(sid)r,
    "name": %(sid)r,
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "scoring": %(scoring)r,
    "descending": %(descending)r,
    "limit": %(limit)d,
}
EXECUTION_BACKEND = "matrix_native"


class _Child:
    def required_fields(self):
        return frozenset({"close", "amount"})

    def required_warmup_bars(self, params):
        return 5

    def compute_signals(self, market, params):
        if %(fail)r:
            raise ValueError("child data unavailable")
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
    "basic_filter": {"price_min": 20},
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
        (CHEAP, 10.0, 9e8, 2e6),
        *(
            (symbol, 25.0 + 3.0 * index, 2e9 + 1e8 * index, 2e6)
            for index, symbol in enumerate(ETFS)
        ),
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


def _context(history: pl.DataFrame) -> StrategyDataContext:
    return StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=AS_OF,
        current=history.filter(pl.col("date") == AS_OF),
        history=history,
        symbol_asset_types=TYPES,
    )


def _child(sid: str, *, scoring, min_volume=0.0, descending=True, fail=False) -> str:
    return CHILD % {
        "sid": sid,
        "scoring": scoring,
        "descending": descending,
        "limit": CHILD_LIMIT,
        "min_volume": min_volume,
        "fail": fail,
    }


def _engine(
    tmp_path: Path, children: dict[str, str], composites: dict[str, list]
) -> StrategyEngine:
    child_dir = tmp_path / "custom"
    composite_dir = tmp_path / "composite"
    child_dir.mkdir(exist_ok=True)
    composite_dir.mkdir(exist_ok=True)
    for sid, source in children.items():
        (child_dir / f"{sid}.py").write_text(source, encoding="utf-8")
    for sid, refs in composites.items():
        source = COMPOSITE % {"sid": sid, "children": refs}
        (composite_dir / f"{sid}.py").write_text(source, encoding="utf-8")
    return StrategyEngine(strategy_dirs=[child_dir, composite_dir])


def _blend_engine(tmp_path: Path) -> StrategyEngine:
    engine = _engine(
        tmp_path,
        {
            "by_amount": _child("by_amount", scoring={"amount": 1.0}),
            "by_close": _child("by_close", scoring={"close": 1.0}, min_volume=1.5e6),
        },
        {
            "blend": [
                {"strategy_id": "by_amount", "weight": 2.0},
                {"strategy_id": "by_close", "weight": 1.0},
            ]
        },
    )
    assert engine.load_errors() == []
    return engine


def _instrument(symbol: str, instrument_type: str) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=code,
        instrument_type=instrument_type,
        listing_status="active",
        listing_date="2000-01-01",
        isin=None,
        industry=None,
        cfi_code=None,
        raw_category="ETF" if instrument_type == "etf" else "股票",
        is_supported=True,
        source="TWSE_ISIN",
        updated_at="2026-10-07",
        etf_category="equity" if instrument_type == "etf" else None,
        classification_source="official_metadata" if instrument_type == "etf" else None,
        underlying_scope="domestic" if instrument_type == "etf" else None,
    )


@pytest.fixture
def master(tmp_path, monkeypatch) -> TaiwanSecurityMaster:
    path = tmp_path / "security_master.parquet"
    pl.DataFrame(
        [_instrument(symbol, kind).to_dict() for symbol, kind in TYPES.items()]
    ).write_parquet(path)
    loaded = TaiwanSecurityMaster(cache_path=path)
    assert loaded.load_cache()
    monkeypatch.setattr(universe, "_default_master", loaded)
    return loaded


def _backtest_scores(
    engine: StrategyEngine, history: pl.DataFrame, overrides=None
) -> dict[str, float]:
    overrides = overrides or {}
    service = StrategyBacktestService(None, engine)
    blend = engine.get("blend")
    params = engine.resolve_params(blend, overrides=overrides)
    basic_filter = _basic_filter_for_asset(
        service._effective_basic_filter(blend, overrides), "stock"
    )
    plan, resolved = service._resolve_composite_feature_plan(
        blend, params=params, basic_filter=basic_filter, overrides=overrides, asset_type="stock"
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


def test_composite_selection_scores_the_backtest_candidates_with_etfs_and_beyond_child_limits(
    tmp_path, master
):
    engine = _blend_engine(tmp_path)
    history = _history()

    selected = engine.run("blend", _context(history))
    batch = engine.run_all(_context(history), strategy_ids=["blend"])["blend"]
    backtest = _backtest_scores(engine, history)

    assert set(backtest) == set(STOCKS)
    assert len(backtest) > CHILD_LIMIT
    assert set(selected.scores) == set(backtest)
    for symbol, score in backtest.items():
        assert selected.scores[symbol] == pytest.approx(score, abs=1e-3), symbol
    assert batch.scores == selected.scores
    assert [row["symbol"] for row in selected.rows] == sorted(
        backtest, key=lambda symbol: (-backtest[symbol], symbol)
    )


def test_composite_basic_filter_override_bounds_both_paths_alike(tmp_path, master):
    engine = _blend_engine(tmp_path)
    history = _history()
    overrides = {"basic_filter": {"price_min": 5}}

    selected = engine.run("blend", _context(history), overrides=overrides)
    backtest = _backtest_scores(engine, history, overrides)

    assert CHEAP in backtest
    assert set(selected.scores) == set(backtest)
    for symbol, score in backtest.items():
        assert selected.scores[symbol] == pytest.approx(score, abs=1e-3), symbol


def test_composite_with_an_ascending_child_does_not_load(tmp_path):
    engine = _engine(
        tmp_path,
        {
            "by_amount": _child("by_amount", scoring={"amount": 1.0}),
            "low_first": _child("low_first", scoring={"amount": 1.0}, descending=False),
        },
        {
            "blend": [{"strategy_id": "by_amount", "weight": 1.0}],
            "blend_low": [
                {"strategy_id": "by_amount", "weight": 1.0},
                {"strategy_id": "low_first", "weight": 1.0},
            ],
        },
    )

    assert engine.has("blend") and engine.has("low_first")
    assert not engine.has("blend_low")
    [error] = engine.load_errors()
    assert "low_first" in error["error"] and "descending" in error["error"]


def test_saved_children_with_an_ascending_child_are_refused_in_selection_and_backtest(tmp_path):
    engine = _engine(
        tmp_path,
        {
            "by_amount": _child("by_amount", scoring={"amount": 1.0}),
            "low_first": _child("low_first", scoring={"amount": 1.0}, descending=False),
        },
        {"blend": [{"strategy_id": "by_amount", "weight": 1.0}]},
    )
    overrides = {"children": [{"strategy_id": "low_first", "weight": 1.0}]}
    blend = engine.get("blend")
    service = StrategyBacktestService(None, engine)

    with pytest.raises(ValueError, match=r"low_first.*descending"):
        engine.run("blend", _context(_history()), overrides=overrides)
    with pytest.raises(ValueError, match=r"low_first.*descending"):
        service._resolve_composite_feature_plan(
            blend,
            params=engine.resolve_params(blend),
            basic_filter=service._effective_basic_filter(blend, overrides),
            overrides=overrides,
        )


def test_run_all_reports_the_failure_of_each_skipped_strategy(tmp_path):
    engine = _engine(
        tmp_path,
        {
            "by_amount": _child("by_amount", scoring={"amount": 1.0}),
            "broken": _child("broken", scoring={"amount": 1.0}, fail=True),
        },
        {},
    )
    failures: dict[str, str] = {}

    results = engine.run_all(
        _context(_history()),
        strategy_ids=["by_amount", "broken"],
        skip_failed=True,
        failures=failures,
    )

    assert set(results) == {"by_amount"}
    assert failures == {"broken": "child data unavailable"}


def test_a_failing_child_is_named_in_the_composite_failure(tmp_path):
    engine = _engine(
        tmp_path,
        {
            "by_amount": _child("by_amount", scoring={"amount": 1.0}),
            "broken": _child("broken", scoring={"amount": 1.0}, fail=True),
        },
        {
            "blend": [
                {"strategy_id": "by_amount", "weight": 1.0},
                {"strategy_id": "broken", "weight": 1.0},
            ]
        },
    )
    failures: dict[str, str] = {}

    engine.run_all(
        _context(_history()), strategy_ids=["blend"], skip_failed=True, failures=failures
    )

    assert failures == {"blend": "子策略「broken」無法執行：child data unavailable"}  # noqa: RUF001
