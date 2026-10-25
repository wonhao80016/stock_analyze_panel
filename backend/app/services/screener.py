"""Screener 服务(§6.3)。

性能优化:
  - enriched parquet 仅存 14 列基础数据, 指标和信号即时计算
  - preset 策略: 从内存缓存或即时计算获取完整指标, ~10-50ms
"""
from __future__ import annotations

import contextvars
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import polars as pl

from app.enriched_generation import (
    EnrichedGenerationUnavailableError,
    enriched_publication_incomplete,
)
from app.parquet import storage_only_rows
from app.repository import HISTORY_WARMUP_SESSIONS, read_enriched_window
from app.services.strategy_inputs import CURRENT, HISTORY, InputStatus
from app.tickflow.repository import KlineRepository

logger = logging.getLogger(__name__)

_INSTRUMENT_COLUMNS = ("symbol", "name", "total_shares", "float_shares")
# Published limit flags of the target day, attached to every strategy result row of that day.
_PUBLISHED_LIMIT_FLAGS = (
    "signal_limit_up",
    "signal_limit_down",
    "signal_broken_limit_up",
    "signal_limit_down_recovery",
)

# ── 进程级历史数据缓存 (避免 run_all 每次重新扫描 parquet + 计算指标) ──
_history_cache: dict[tuple[str, date, int], tuple[float, pl.DataFrame]] = {}
_HISTORY_CACHE_TTL = 120.0  # 秒
# One market-wide window is a few hundred MB; a backfill over many dates must not keep them all.
_HISTORY_CACHE_MAX_ENTRIES = 4
_HISTORY_CACHE_MAX_BYTES = 512 * 1024 * 1024
# Every _history_cache access goes through this lock: refreshes clear it from other threads.
_history_cache_lock = threading.Lock()


def _history_cache_get(key: tuple[str, date, int], now: float) -> pl.DataFrame | None:
    with _history_cache_lock:
        entry = _history_cache.get(key)
        if entry is None:
            return None
        ts, cached_df = entry
        if now - ts < _HISTORY_CACHE_TTL:
            return cached_df
        del _history_cache[key]
        return None


def _history_cache_put(key: tuple[str, date, int], now: float, df: pl.DataFrame) -> None:
    """Store ``df``, then drop expired entries and the oldest others beyond the entry and byte caps."""
    with _history_cache_lock:
        _history_cache[key] = (now, df)
        for k in [k for k, (ts, _) in _history_cache.items() if now - ts > _HISTORY_CACHE_TTL]:
            del _history_cache[k]
        older = sorted((k for k in _history_cache if k != key), key=lambda k: _history_cache[k][0])
        while older and (
            len(_history_cache) > _HISTORY_CACHE_MAX_ENTRIES
            or _history_cache_bytes() > _HISTORY_CACHE_MAX_BYTES
        ):
            del _history_cache[older.pop(0)]


def _history_cache_bytes() -> int:
    return sum(
        frame.estimated_size()
        for _, frame in _history_cache.values()
        if isinstance(frame, pl.DataFrame)
    )

# Loader outcomes collected by build_strategy_context; None outside that call.
_input_sink: contextvars.ContextVar[dict[str, InputStatus] | None] = contextvars.ContextVar(
    "screener_input_sink", default=None
)


def _input_status(state: str, frame: pl.DataFrame | None, target_date: date) -> InputStatus:
    if frame is None or frame.is_empty():
        return InputStatus(state=state)
    at_target = frame
    dates: tuple[date, ...] = ()
    if "date" in frame.columns:
        day = pl.col("date").cast(pl.Date)
        dates = tuple(frame.select(day.unique().sort()).to_series().drop_nulls().to_list())
        at_target = frame.filter(day == target_date)
    exchanges: frozenset[str] = frozenset()
    if "symbol" in at_target.columns:
        suffix = pl.col("symbol").cast(pl.Utf8).str.extract(r"\.([^.]+)$", 1)
        exchanges = frozenset(at_target.select(suffix.drop_nulls().unique()).to_series().to_list())
    return InputStatus(state=state, rows=frame.height, exchanges_at_t=exchanges, dates=dates)


def _report_input(
    group: str,
    state: str,
    frame: pl.DataFrame | None,
    target_date: date,
    *,
    overwrite: bool = True,
) -> None:
    """Never raises: status reporting must not change what the loaders return."""
    sink = _input_sink.get()
    if sink is None or (not overwrite and group in sink):
        return
    try:
        sink[group] = _input_status(state, frame, target_date)
    except Exception:
        logger.exception("input status for %s on %s could not be derived", group, target_date)
        sink[group] = InputStatus(state="unverified")


_TAIWAN_SYMBOL = r"\.(TWSE|TPEX)$"


def _has_taiwan_symbols(*frames: pl.DataFrame | None) -> bool:
    return any(
        frame is not None
        and "symbol" in frame.columns
        and bool(
            frame.select(pl.col("symbol").cast(pl.Utf8).str.contains(_TAIWAN_SYMBOL).any()).item()
        )
        for frame in frames
    )


# Re-read only on a new (path, mtime, size): open readers block os.replace of the file on Windows.
_master_asset_types: tuple[tuple[str, int, int], dict[str, str]] | None = None
_master_asset_types_lock = threading.Lock()


def _read_master_asset_types(cache_path) -> dict[str, str]:
    global _master_asset_types
    stat = os.stat(cache_path)
    signature = (str(cache_path), stat.st_mtime_ns, stat.st_size)
    with _master_asset_types_lock:
        cached = _master_asset_types
    if cached is not None and cached[0] == signature:
        return cached[1]
    frame = pl.read_parquet(cache_path, columns=["symbol", "instrument_type"])
    types = dict(frame.iter_rows())
    with _master_asset_types_lock:
        _master_asset_types = (signature, types)
    return types


class _SecurityMasterAssetTypes(Mapping[str, str]):
    """instrument_type per symbol from the security master cache file, resolved on first lookup.

    Never loads the master itself, so it cannot trigger an exchange download or rewrite the cache.
    A missing or unreadable cache raises SecurityMasterUnavailableError on every lookup without
    reading the file again.
    """

    def __init__(self) -> None:
        self._types: dict[str, str] | None = None
        self._error: str | None = None

    def _resolved(self) -> dict[str, str]:
        from app.taiwan.universe.service import SecurityMasterUnavailableError

        if self._error is not None:
            raise SecurityMasterUnavailableError(self._error)
        if self._types is None:
            from app.taiwan.universe import get_security_master

            try:
                self._types = _read_master_asset_types(get_security_master().cache_path)
            except Exception as exc:
                self._error = f"Taiwan Security Master unavailable: {exc}"
                raise SecurityMasterUnavailableError(self._error) from exc
        return self._types

    def __getitem__(self, symbol: str) -> str:
        return self._resolved()[symbol]

    def __iter__(self) -> Iterator[str]:
        return iter(self._resolved())

    def __len__(self) -> int:
        return len(self._resolved())


def taiwan_symbol_asset_types(
    asset_type: str, *frames: pl.DataFrame | None
) -> Mapping[str, str] | None:
    """Security master asset types for stock/ETF data holding Taiwan symbols; None for anything else."""
    if asset_type not in ("stock", "etf") or not _has_taiwan_symbols(*frames):
        return None
    return _SecurityMasterAssetTypes()


@dataclass
class ScreenerResult:
    as_of: date
    strategy: str | None
    rows: list[dict] = field(default_factory=list)
    total: int = 0
    elapsed_ms: float = 0.0


class ScreenerService:
    def __init__(self, repo: KlineRepository, asset_type: str = "stock") -> None:
        self.repo = repo
        self.asset_type = asset_type
        from app.tickflow.repository import enriched_dirname
        self._enriched_dirname = enriched_dirname(asset_type)

    def _raise_if_publishing(self) -> None:
        """Refuse to read partitions while an enriched rewrite is half done; they then mix price scales."""
        if self.asset_type in ("stock", "etf") and enriched_publication_incomplete(
            self.repo.store.data_dir, self.asset_type
        ):
            raise EnrichedGenerationUnavailableError(
                "enriched data is being published; retry after the update finishes"
            )

    @staticmethod
    def clear_history_cache() -> None:
        """清空进程级 _history_cache (TTL 缓存)。

        清除数据后调用, 避免内存里的旧历史窗口残留导致策略/看板仍命中旧数据。
        """
        with _history_cache_lock:
            _history_cache.clear()

    def _load_enriched_for_date(self, target_date: date) -> pl.DataFrame:
        """The full enriched rows of target_date: indicators, signals and limit flags.

        A published partition holding every indicator is served as stored. Otherwise the
        in-memory caches serve the day, and a stored day is recomputed from its warm-up history.
        """
        published = self._published_day(target_date)
        if published is not None:
            df_i = self.repo.get_instruments_asset(self.asset_type)
            if not df_i.is_empty() and "name" not in published.columns:
                inst_cols = [c for c in _INSTRUMENT_COLUMNS if c in df_i.columns]
                published = published.join(df_i.select(inst_cols), on="symbol", how="left")
            _report_input(CURRENT, "ok", published, target_date)
            return published

        # 优先使用 repo 最新日缓存
        cache, cache_date = self.repo.get_enriched_latest_asset(self.asset_type)
        if cache is not None and not cache.is_empty() and cache_date == target_date:
            df = cache
            # JOIN instruments
            df_i = self.repo.get_instruments_asset(self.asset_type)
            if not df_i.is_empty():
                inst_cols = [c for c in ["symbol", "name", "total_shares", "float_shares"] if c in df_i.columns]
                if "name" not in df.columns:
                    df = df.join(df_i.select(inst_cols), on="symbol", how="left")
            _report_input(CURRENT, "ok", df, target_date)
            return df

        # 尝试从 repo 级预计算历史缓存中提取目标日期 (仅 stock: 该缓存为股票专用)
        if self.asset_type == "stock":
            cached_hist = self.repo.get_enriched_history(target_date, 1)
            if cached_hist is not None and not cached_hist.is_empty() and "date" in cached_hist.columns:
                df = cached_hist.filter(pl.col("date") == target_date)
                if not df.is_empty():
                    logger.debug("_load_enriched_for_date: repo history cache for %s", target_date)
                    # JOIN instruments
                    df_i = self.repo.get_instruments_asset(self.asset_type)
                    if not df_i.is_empty():
                        inst_cols = [c for c in ["symbol", "name", "total_shares", "float_shares"] if c in df_i.columns]
                        if "name" not in df.columns:
                            df = df.join(df_i.select(inst_cols), on="symbol", how="left")
                    _report_input(CURRENT, "ok", df, target_date)
                    return df

        self._raise_if_publishing()

        # 历史日期: 从 parquet 读取 14 列, 即时计算指标 (慢路径)
        enriched_dir = self.repo.store.data_dir / self._enriched_dirname
        ds = target_date.isoformat()
        target_parquet = enriched_dir / f"date={ds}" / "part.parquet"

        if not target_parquet.exists():
            _report_input(CURRENT, "missing", None, target_date)
            return pl.DataFrame()

        try:
            df = pl.read_parquet(target_parquet)
        except Exception as e:  # noqa: BLE001
            logger.warning("load_enriched_for_date failed: %s", e)
            _report_input(CURRENT, "error", None, target_date)
            return pl.DataFrame()

        if df.is_empty():
            _report_input(CURRENT, "empty", df, target_date)
            return df

        # 即时计算指标: 需要加载历史窗口作 warmup
        df_full = self._compute_enriched_full(df, target_date)
        _report_input(CURRENT, "ok", df_full, target_date, overwrite=False)
        return df_full

    def _published_day(self, target_date: date) -> pl.DataFrame | None:
        """The partition of target_date when it has every indicator column and no storage-only row, else None."""
        from app.indicators.pipeline import INDICATOR_COLUMNS

        try:
            data_dir = self.repo.store.data_dir
            if self.asset_type in ("stock", "etf") and enriched_publication_incomplete(
                data_dir, self.asset_type
            ):
                return None
            partition = data_dir / self._enriched_dirname / f"date={target_date.isoformat()}"
            path = partition / "part.parquet"
            if not path.exists() or not INDICATOR_COLUMNS.issubset(pl.read_parquet_schema(path)):
                return None
            df = pl.read_parquet(path)
        except Exception as e:
            logger.warning("published enriched day %s unreadable: %s", target_date, e)
            return None
        if df.is_empty() or df.select(storage_only_rows(df.columns).any()).item():
            return None
        return df

    def _first_of_last_sessions(self, target_date: date, sessions: int) -> date | None:
        """First of the last ``sessions`` stored sessions through target_date; None if fewer are stored."""
        enriched_dir = self.repo.store.data_dir / self._enriched_dirname
        stored: list[date] = []
        for path in enriched_dir.glob("date=*/part.parquet"):
            try:
                day = date.fromisoformat(path.parent.name.removeprefix("date="))
            except ValueError:
                continue
            if day <= target_date:
                stored.append(day)
        if len(stored) < sessions:
            return None
        stored.sort()
        return stored[-sessions]

    def _published_limit_flags(self, as_of: date) -> dict[str, dict[str, Any]] | None:
        """Limit flags of each symbol in the published partition of as_of; None without one."""
        try:
            enriched_dir = self.repo.store.data_dir / self._enriched_dirname
            path = enriched_dir / f"date={as_of.isoformat()}" / "part.parquet"
            if not path.exists():
                return None
            names = pl.read_parquet_schema(path)
            flags = [name for name in _PUBLISHED_LIMIT_FLAGS if name in names]
            if "symbol" not in names or not flags:
                return None
            frame = pl.read_parquet(path, columns=["symbol", *flags]).unique("symbol", keep="last")
        except Exception as e:
            logger.warning("published limit flags for %s unavailable: %s", as_of, e)
            return None
        return {str(row.pop("symbol")): row for row in frame.iter_rows(named=True)}

    def _limit_signals(
        self, instruments: pl.DataFrame
    ) -> Callable[[pl.DataFrame], pl.DataFrame] | None:
        """Limit flags for the rows read_enriched_window recomputes; None without stock instruments."""
        if self.asset_type != "stock" or instruments is None or instruments.is_empty():
            return None
        from app.indicators.pipeline import compute_limit_signals

        def compute(frame: pl.DataFrame) -> pl.DataFrame:
            return compute_limit_signals(
                frame, instruments, historical_shares=self.repo.get_historical_shares()
            )

        return compute

    def _compute_enriched_full(self, df_target: pl.DataFrame, target_date: date) -> pl.DataFrame:
        """The enriched rows of target_date: published rows as stored, the others recomputed."""
        from app.indicators.pipeline import compute_indicators, compute_signals

        instruments = self.repo.get_instruments_asset(self.asset_type)
        limit_signals = self._limit_signals(instruments)
        try:
            df_result = read_enriched_window(
                self.repo.store.data_dir,
                self._enriched_dirname,
                target_date,
                target_date,
                limit_signals=limit_signals,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("warmup history load failed: %s", e)
            df_result = pl.DataFrame()

        if df_result.is_empty():
            # Indicators from this single day differ from warmed-up ones, so the result is not ok.
            _report_input(CURRENT, "error", df_target, target_date)
            df_result = compute_signals(compute_indicators(df_target))
            if limit_signals is not None:
                df_result = limit_signals(df_result)
            df_result = df_result.filter(pl.col("date") == target_date)

        # JOIN instruments (name, total_shares, float_shares)
        if not instruments.is_empty():
            inst_cols = [c for c in ["symbol", "name", "total_shares", "float_shares"] if c in instruments.columns]
            if "name" not in df_result.columns:
                df_result = df_result.join(instruments.select(inst_cols), on="symbol", how="left")

        return df_result

    def _load_enriched_history(self, target_date: date, lookback_days: int) -> pl.DataFrame:
        """The enriched rows of the last lookback_days + 1 sessions through target_date.

        Served from the repository cache when it covers them, else read with read_enriched_window.
        """
        # 优先级 1: repo 级预计算缓存 (启动时 _refresh_enriched 已计算完整历史; 仅 stock)
        t0 = time.perf_counter()
        if self.asset_type == "stock":
            cached = self.repo.get_enriched_history(target_date, lookback_days)
            if cached is not None and not cached.is_empty():
                # JOIN instruments (repo 缓存不含 name 等列)
                instruments = self.repo.get_instruments_asset(self.asset_type)
                if instruments is not None and not instruments.is_empty() and "name" not in cached.columns:
                    inst_cols = [c for c in ["symbol", "name", "total_shares", "float_shares"]
                                 if c in instruments.columns]
                    cached = cached.join(instruments.select(inst_cols), on="symbol", how="left")
                elapsed = (time.perf_counter() - t0) * 1000
                logger.info("_load_enriched_history(%s, %d): repo cache hit, %.1fms, %d rows",
                            target_date, lookback_days, elapsed, len(cached))
                _report_input(HISTORY, "ok", cached, target_date)
                return cached

        # 优先级 2: 进程级 history_cache (之前的 TTL 缓存)
        cache_key = (self.asset_type, target_date, lookback_days)
        now = time.monotonic()
        cached_df = _history_cache_get(cache_key, now)
        if cached_df is not None:
            logger.debug("history TTL cache hit: %s lookback=%d", target_date, lookback_days)
            _report_input(HISTORY, "ok", cached_df, target_date)
            return cached_df

        self._raise_if_publishing()

        # Priority 3 (slow path): read the window's enriched partitions.
        logger.warning("_load_enriched_history cache miss, reading partitions (%s, %d)...",
                       target_date, lookback_days)
        instruments = self.repo.get_instruments_asset(self.asset_type)
        try:
            df_full = read_enriched_window(
                self.repo.store.data_dir,
                self._enriched_dirname,
                self._first_of_last_sessions(target_date, lookback_days + 1),
                target_date,
                limit_signals=self._limit_signals(instruments),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("load_enriched_history failed: %s", e)
            _report_input(HISTORY, "error", None, target_date)
            return pl.DataFrame()

        if df_full.is_empty():
            _report_input(HISTORY, "empty", None, target_date)
            return pl.DataFrame()

        if instruments is not None and not instruments.is_empty():
            inst_cols = [c for c in ["symbol", "name", "total_shares", "float_shares"] if c in instruments.columns]
            if "name" not in df_full.columns:
                df_full = df_full.join(instruments.select(inst_cols), on="symbol", how="left")

        df_full = df_full.sort(["symbol", "date"])

        elapsed = (time.perf_counter() - t0) * 1000
        logger.info("_load_enriched_history(%s, %d): read in %.1fms, %d rows",
                    target_date, lookback_days, elapsed, len(df_full))

        _history_cache_put(cache_key, now, df_full)

        _report_input(HISTORY, "ok", df_full, target_date)
        return df_full

    def build_strategy_context(
        self,
        engine,
        as_of: date,
        strategy_ids: list[str],
        *,
        timeframe: str = "1d",
        params_map: dict[str, dict] | None = None,
        overrides_map: dict[str, dict] | None = None,
        current: pl.DataFrame | None = None,
        market=None,
        cache_key: str | None = None,
    ):
        """按调用方要求装配标准策略数据上下文，不解释策略公式。"""
        from app.strategy.engine import StrategyDataContext

        sink: dict[str, InputStatus] = {}
        token = _input_sink.set(sink)
        try:
            if current is None:
                current = self._load_enriched_for_date(as_of)
            else:
                _report_input(CURRENT, "provided", current, as_of)
            history_bars = engine.required_history_bars(
                strategy_ids,
                params_map=params_map,
                overrides_map=overrides_map,
            )
            history = None
            if history_bars > 1:
                # Each matrix strategy computes on its required bars plus this margin, sliced from
                # whatever is loaded, so a run alone and a batch run see the same sessions.
                history = self._load_enriched_history(as_of, history_bars + HISTORY_WARMUP_SESSIONS)
        finally:
            _input_sink.reset(token)
        return StrategyDataContext(
            asset_type=self.asset_type,
            timeframe=timeframe,
            as_of=as_of,
            current=current,
            history=history,
            market=market,
            cache_key=cache_key,
            input_status={
                CURRENT: sink.get(CURRENT, InputStatus()),
                HISTORY: sink.get(HISTORY, InputStatus()) if history_bars > 1 else None,
            },
            # Taiwan daily enriched data holds stocks and ETFs together; only the master tells them apart.
            symbol_asset_types=taiwan_symbol_asset_types(self.asset_type, current, history),
            published_row_fields=self._published_limit_flags(as_of),
        )

    def latest_date(self) -> date | None:
        """Basis date for screening: the last trading day with official data.

        Not "today". The live quote path advances the enriched cache date on a
        single realtime tick, so during a session that cache can point at a day
        holding only the handful of watchlist symbols that ticked. Screening off
        that returns nothing at all, which is how this read silently broke.
        """
        if self.asset_type != "stock":
            _, d = self.repo.get_enriched_latest_asset(self.asset_type)
            return d
        d = self.repo.taiwan_official_latest_date()
        if d:
            return d
        # 回退 DuckDB
        try:
            res = self.repo.execute_one(
                "SELECT max(date) FROM kline_enriched",
            )
            if res and res[0]:
                d = res[0]
                return d if isinstance(d, date) else date.fromisoformat(str(d))
        except Exception:  # noqa: BLE001
            return None
        return None
