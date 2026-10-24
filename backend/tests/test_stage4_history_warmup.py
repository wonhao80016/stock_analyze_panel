"""Strategy history sized in trading sessions, scoring warm-ups and macd_golden window independence."""

from __future__ import annotations

import math
import types
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from app.backtest.matrix import build_market_data_matrix, matrix_feature
from app.indicators import pipeline
from app.repository import HISTORY_WARMUP_SESSIONS, KlineRepository
from app.services import screener
from app.services.screener import ScreenerService
from app.strategy.engine import StrategyDataContext, StrategyEngine
from app.strategy.scoring import scoring_warmup_bars

pytestmark = pytest.mark.usefixtures("deny_network")

BUILTIN_DIR = Path(screener.__file__).resolve().parents[1] / "strategy" / "builtin"
SYMBOLS = ("1101.TWSE", "6488.TPEX")


def _weekdays(start: date, count: int, weekdays=(0, 1, 2, 3, 4)) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() in weekdays:
            days.append(day)
        day += timedelta(days=1)
    return days


def _storage_rows(days: list[date], symbols=SYMBOLS) -> pl.DataFrame:
    rows = []
    for index, symbol in enumerate(symbols):
        for offset, day in enumerate(days):
            close = 50.0 + index + 5.0 * math.sin(offset / 9.0) + offset * 0.01
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "open": close - 0.1,
                    "high": close + 0.3,
                    "low": close - 0.3,
                    "close": close,
                    "volume": 1_000_000.0 + 1_000.0 * (offset % 7),
                    "amount": 5e7,
                    "raw_close": close,
                    "raw_high": close + 0.3,
                    "raw_low": close - 0.3,
                }
            )
    return pl.DataFrame(rows).sort(["symbol", "date"])


def _write_partitions(data_dir: Path, frame: pl.DataFrame) -> None:
    for (day,), part in frame.partition_by("date", as_dict=True).items():
        path = data_dir / "kline_daily_enriched" / f"date={day.isoformat()}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        part.write_parquet(path)


class _DiskRepo:
    """Serves nothing from caches, so the screener reads and computes the partitions."""

    def __init__(self, data_dir: Path) -> None:
        self.store = types.SimpleNamespace(data_dir=data_dir)

    def get_enriched_latest_asset(self, asset_type):
        return pl.DataFrame(), None

    def get_enriched_history(self, target_date, lookback_days):
        return None

    def get_instruments_asset(self, asset_type):
        return pl.DataFrame()


@pytest.fixture(autouse=True)
def _clean_history_cache():
    ScreenerService.clear_history_cache()
    yield
    ScreenerService.clear_history_cache()


def test_history_for_260_bars_holds_its_window_and_warmup_margin_with_warm_indicators(tmp_path):
    days = _weekdays(date(2025, 1, 6), 400)
    _write_partitions(tmp_path, _storage_rows(days))
    target = days[-1]
    engine = types.SimpleNamespace(required_history_bars=lambda ids, **kwargs: 260)

    context = ScreenerService(_DiskRepo(tmp_path)).build_strategy_context(
        engine, target, ["needs_260"], current=_storage_rows([target])
    )

    sessions = context.history["date"].unique().sort().to_list()
    window = 261 + HISTORY_WARMUP_SESSIONS
    assert len(sessions) == window
    assert sessions[0] == days[-window]
    first = context.history.filter(pl.col("date") == sessions[0])
    assert first["ma60"].null_count() == 0


def _cache_repo(frame: pl.DataFrame) -> KlineRepository:
    repo = KlineRepository.__new__(KlineRepository)
    repo._enriched_history_cache = frame
    repo._enriched_history_start = frame["date"].min()
    return repo


def test_repo_cache_counts_history_back_from_a_historical_target():
    days = _weekdays(date(2025, 1, 6), 300)
    repo = _cache_repo(_storage_rows(days))

    history = repo.get_enriched_history(days[200], 61)

    assert history["date"].unique().sort().to_list() == days[200 - 61 : 201]


def test_repo_cache_serves_a_window_from_its_first_session():
    days = _weekdays(date(2025, 1, 6), 300)
    repo = _cache_repo(_storage_rows(days))

    # The cache holds published indicators, so a 62-session window needs no sessions before it.
    assert repo.get_enriched_history(days[60], 61) is None
    served = repo.get_enriched_history(days[61], 61)
    assert served is not None
    assert served["date"].unique().sort().to_list() == days[:62]


def test_recomputed_current_day_reads_its_warmup_in_sessions(tmp_path, monkeypatch):
    # Three sessions a week: 150 calendar days hold only about 64 sessions.
    days = _weekdays(date(2024, 6, 3), 200, weekdays=(0, 2, 4))
    _write_partitions(tmp_path, _storage_rows(days))
    target = days[-1]
    real = pipeline.compute_indicators
    seen: dict[str, int] = {}

    def spy(df, *args, **kwargs):
        seen["sessions"] = df["date"].n_unique()
        return real(df, *args, **kwargs)

    monkeypatch.setattr(pipeline, "compute_indicators", spy)

    current = ScreenerService(_DiskRepo(tmp_path))._load_enriched_for_date(target)

    assert current["date"].unique().to_list() == [target]
    assert seen["sessions"] >= 101


def _random_panel(n_days: int, n_symbols: int = 3, seed: int = 3) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    days = _weekdays(date(2022, 1, 3), n_days)
    frames = []
    for index in range(n_symbols):
        close = 40.0 * np.exp(np.cumsum(rng.normal(0.0, 0.02, n_days)))
        spread = close * rng.uniform(0.005, 0.03, n_days)
        frames.append(
            pl.DataFrame(
                {
                    "symbol": [f"{1101 + index}.TWSE"] * n_days,
                    "date": days,
                    "open": close + rng.normal(0.0, 0.2, n_days),
                    "high": close + spread,
                    "low": close - spread,
                    "close": close,
                    "volume": rng.uniform(5e5, 3e6, n_days),
                    "amount": np.full(n_days, 1e9),
                }
            )
        )
    return pl.concat(frames).sort(["symbol", "date"])


RECURSIVE_FIELDS = [
    "ema20_bias",
    "ema60_bias",
    "rsi_6",
    "rsi_14",
    "rsi_24",
    "atr_pct",
    "macd_dif_pct",
    "macd_dea_pct",
    "macd_hist_pct",
    "kdj_k",
    "kdj_d",
    "kdj_j",
]


@pytest.mark.parametrize("field", RECURSIVE_FIELDS)
def test_scoring_warmup_makes_the_matrix_value_independent_of_loaded_history(field):
    bars = scoring_warmup_bars({field: 1.0})
    full = _random_panel(bars + 400)
    days = full["date"].unique().sort()
    window = full.filter(pl.col("date") >= days[-bars])

    def last_row(panel: pl.DataFrame) -> np.ndarray:
        return matrix_feature(build_market_data_matrix(panel), field)[-1]

    np.testing.assert_allclose(last_row(window), last_row(full), rtol=1e-3, atol=1e-4)


def test_required_history_covers_the_warmup_of_parameter_scoring_fields():
    engine = StrategyEngine(strategy_dirs=[BUILTIN_DIR])

    bars = engine.required_history_bars(
        ["factor_rank_research"],
        params_map={"factor_rank_research": {"scoring": {"ema60_bias": 1.0}}},
    )

    seed_weight = (1.0 - 2.0 / 61.0) ** (bars - 1)
    assert seed_weight < 1e-4


def _macd_panel(n_days: int = 600, n_symbols: int = 160, seed: int = 11) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    days = _weekdays(date(2023, 1, 2), n_days)
    frames = []
    for index in range(n_symbols):
        close = 50.0 * np.exp(np.cumsum(rng.normal(0.0, 0.02, n_days)))
        frames.append(
            pl.DataFrame(
                {
                    "symbol": [f"{1101 + index}.TWSE"] * n_days,
                    "name": [f"{1101 + index}"] * n_days,
                    "date": days,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "raw_close": close,
                    "volume": rng.uniform(5e5, 3e6, n_days),
                    "amount": np.full(n_days, 1e9),
                }
            )
        )
    return pl.concat(frames).sort(["symbol", "date"])


def _macd_picks(engine: StrategyEngine, panel: pl.DataFrame, sessions: int | None) -> list:
    days = panel["date"].unique().sort()
    target = days[-1]
    window = panel if sessions is None else panel.filter(pl.col("date") >= days[-sessions])
    context = StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=target,
        current=window.filter(pl.col("date") == target),
        history=window,
    )
    result = engine.run("macd_golden", context, params={"use_volume_filter": False})
    return [(row["symbol"], round(float(row["score"]), 4)) for row in result.rows]


def test_macd_golden_picks_do_not_depend_on_the_loaded_history():
    engine = StrategyEngine(strategy_dirs=[BUILTIN_DIR])
    panel = _macd_panel()
    loaded = engine.required_history_bars(["macd_golden"]) + 1

    picks = _macd_picks(engine, panel, loaded)

    assert picks
    assert picks == _macd_picks(engine, panel, loaded + 120)
    assert picks == _macd_picks(engine, panel, None)


def test_historical_current_day_is_the_published_row(tmp_path):
    days = _weekdays(date(2025, 1, 6), 300)
    published = pipeline.compute_signals(pipeline.compute_indicators(_storage_rows(days)))
    _write_partitions(tmp_path, published)
    target = days[200]
    columns = ["symbol", *sorted(pipeline.INDICATOR_COLUMNS), "signal_macd_golden"]

    current = ScreenerService(_DiskRepo(tmp_path))._load_enriched_for_date(target)

    stored = published.filter(pl.col("date") == target)
    assert_frame_equal(
        current.select(columns).sort("symbol"), stored.select(columns).sort("symbol")
    )


def test_history_ttl_cache_keeps_a_bounded_number_of_windows():
    frame = pl.DataFrame({"x": [1.0]})
    keys = [("stock", date(2026, 1, 1) + timedelta(days=offset), 121) for offset in range(6)]

    for offset, key in enumerate(keys):
        screener._history_cache_put(key, float(offset), frame)

    assert len(screener._history_cache) <= 4
    assert keys[-1] in screener._history_cache


def test_history_ttl_cache_keeps_its_bytes_bounded(monkeypatch):
    frame = pl.DataFrame({"x": np.arange(10_000, dtype=np.float64)})
    monkeypatch.setattr(screener, "_HISTORY_CACHE_MAX_BYTES", 2 * frame.estimated_size())
    keys = [("stock", date(2026, 1, 1) + timedelta(days=offset), 121) for offset in range(3)]

    for offset, key in enumerate(keys):
        screener._history_cache_put(key, float(offset), frame)

    assert list(screener._history_cache) == keys[1:]


WINDOW_STRATEGY = """
import numpy as np

from app.backtest.matrix import make_signal_matrix

META = {"id": "%(sid)s", "name": "%(sid)s", "asset_types": ["stock"], "limit": 0}
BASIC_FILTER = {"enabled": False}
EXECUTION_BACKEND = "matrix_native"


class _Recorder:
    def __init__(self):
        self.rows = []

    def required_fields(self):
        return frozenset({"close"})

    def required_warmup_bars(self, params):
        return %(warmup)d

    def compute_signals(self, market, params):
        self.rows.append(market.shape[0])
        return make_signal_matrix(market.shape, entry=np.ones(market.shape, dtype=np.uint8))


MATRIX_STRATEGY = _Recorder()
"""


def test_a_shared_market_is_cut_to_each_strategys_own_window(tmp_path):
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir()
    for sid, warmup in (("short_window", 10), ("long_window", 150)):
        source = WINDOW_STRATEGY % {"sid": sid, "warmup": warmup}
        (strategy_dir / f"{sid}.py").write_text(source, encoding="utf-8")
    engine = StrategyEngine(strategy_dirs=[strategy_dir])
    short, long = (engine.get(sid).matrix_strategy for sid in ("short_window", "long_window"))
    panel = _storage_rows(_weekdays(date(2025, 1, 6), 250))
    target = panel["date"].max()
    current = panel.filter(pl.col("date") == target)

    def own_sessions(sid: str) -> int:
        return 1 + HISTORY_WARMUP_SESSIONS + engine.required_history_bars([sid])

    context = StrategyDataContext(
        asset_type="stock", timeframe="1d", as_of=target, current=current, history=panel
    )
    batch = engine.run_all(context, strategy_ids=["short_window", "long_window"])
    realtime = engine.run(
        "short_window", replace(context, history=None, market=build_market_data_matrix(panel))
    )

    assert short.rows == [own_sessions("short_window")] * 2
    assert long.rows == [own_sessions("long_window")]
    assert {row["symbol"] for row in batch["short_window"].rows} == set(SYMBOLS)
    assert {row["symbol"] for row in realtime.rows} == set(SYMBOLS)
