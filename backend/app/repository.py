"""Repository 层(§7.4)。

数据分层:
  - DuckDB 视图: 冷查询(统计、元数据、用户自定义SQL)
  - Polars 缓存: 热路径(enriched 最新日 ~5500行 + instruments ~5500行)
  - Polars scan_parquet: 分钟K/历史日K (predicate pushdown)

缓存生命周期:
  - startup 时不加载(数据可能为空)
  - pipeline 完成后调用 refresh_cache()
  - 服务层通过 get_enriched_latest() / get_instruments() 获取缓存
"""
from __future__ import annotations

import bisect
import json
import logging
import os
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from app.config import settings
from app.enriched_generation import (
    EnrichedGenerationUnavailableError,
    EnrichedPublication,
    enriched_publication_incomplete,
    get_enriched_generation,
)
from app.parquet import (
    ENRICHED_STORAGE_SCHEMA,
    scan_enriched_parquet,
    scan_parquet_compat,
    storage_only_rows,
)
from app.strategy.scoring import CALENDAR_DAYS_PER_SESSION, indicators_warmup_bars
from app.volume_units import TAIWAN_SUFFIXES

logger = logging.getLogger(__name__)


def indicator_warmup_days() -> int:
    """Calendar days read before a window whose indicators are all recomputed, for their warm-ups."""
    from app.indicators.pipeline import INDICATOR_COLUMNS

    return int(indicators_warmup_bars(INDICATOR_COLUMNS) * CALENDAR_DAYS_PER_SESSION)


def _stored_partitions(directory: Path, end: date) -> list[tuple[date, Path]]:
    """(date, file) of each ``date=YYYY-MM-DD/part.parquet`` partition dated up to *end*, oldest first."""
    partitions: list[tuple[date, Path]] = []
    try:
        with os.scandir(directory) as entries:
            names = [entry.name for entry in entries if entry.is_dir()]
    except FileNotFoundError:
        return partitions
    for name in names:
        try:
            day = date.fromisoformat(name.removeprefix("date="))
        except ValueError:
            continue
        path = directory / name / "part.parquet"
        if day <= end and path.is_file():
            partitions.append((day, path))
    return sorted(partitions)


def taiwan_limit_signals(frame: pl.DataFrame) -> pl.DataFrame:
    """*frame* with the Taiwan publish's limit flags and streaks on its Taiwan rows; needs prev_close.

    The publish's own rule (add_taiwan_limit_signals on limit_profile): a row whose limit is unknown
    gets False and a ref_base_missing row null, as published. Rows of other symbols are returned
    unchanged, in another order.
    """
    taiwan = pl.col("symbol").str.ends_with(TAIWAN_SUFFIXES[0]) | pl.col("symbol").str.ends_with(
        TAIWAN_SUFFIXES[1]
    )
    rows = frame.filter(taiwan)
    if rows.is_empty():
        return frame
    from app.taiwan.daily_store import listing_sessions
    from app.taiwan.enriched_refresh import add_taiwan_limit_signals, limit_profile

    profile = limit_profile(rows["symbol"].unique().to_list(), listing_sessions())
    flagged = add_taiwan_limit_signals(rows, profile)
    return pl.concat([flagged, frame.filter(~taiwan)], how="diagonal_relaxed")


def read_enriched_window(
    data_dir: Path,
    dirname: str,
    start: date | None,
    end: date,
    *,
    limit_signals: Callable[[pl.DataFrame], pl.DataFrame] | None = None,
) -> pl.DataFrame:
    """Every symbol's enriched rows of the partitions dated within [start, end], by symbol and date.

    Published rows are returned as stored. Storage-only rows, or every row when the partitions lack an
    indicator column, are recomputed from enough earlier sessions for each recursive indicator to
    leave its warm-up; *limit_signals* adds the limit flags of those rows, by default the Taiwan
    publish's (taiwan_limit_signals).
    """
    from app.indicators.pipeline import INDICATOR_COLUMNS, compute_indicators, compute_signals

    stored = _stored_partitions(data_dir / dirname, end)
    first = 0 if start is None else bisect.bisect_left([day for day, _ in stored], start)
    window = [path for _, path in stored[first:]]
    if not window:
        return pl.DataFrame()
    cast = pl.ScanCastOptions(integer_cast="allow-float", float_cast="upcast")
    published = (
        scan_parquet_compat(
            window,
            schema={**pl.read_parquet_schema(window[-1]), **ENRICHED_STORAGE_SCHEMA},
            cast_options=cast,
        )
        .collect()
        .sort(["symbol", "date"])
    )
    pending = (
        storage_only_rows(published.columns)
        if INDICATOR_COLUMNS.issubset(published.columns)
        else pl.lit(True)
    )
    symbols = published.filter(pending)["symbol"].unique().to_list()
    if not symbols:
        return published
    warmup_start = max(0, first - indicators_warmup_bars(INDICATOR_COLUMNS) + 1)
    history = [path for _, path in stored[warmup_start:]]
    computed = (
        scan_enriched_parquet(history, cast_options=cast)
        .filter(pl.col("symbol").is_in(symbols))
        .collect()
        .sort(["symbol", "date"])
    )
    computed = (limit_signals or taiwan_limit_signals)(
        compute_signals(compute_indicators(computed))
    )
    derived = [c for c in computed.columns if c not in ENRICHED_STORAGE_SCHEMA]
    merged = published.join(
        computed.select(["symbol", "date", *derived]).rename({c: f"__computed_{c}" for c in derived}),
        on=["symbol", "date"],
        how="left",
        maintain_order="left",
    )
    return merged.with_columns(
        (
            pl.when(pending)
            .then(pl.col(f"__computed_{c}").cast(published.schema[c], strict=False))
            .otherwise(pl.col(c))
            if c in published.columns
            else pl.col(f"__computed_{c}")
        ).alias(c)
        for c in derived
    ).drop([f"__computed_{c}" for c in derived])


# DuckDB view name -> parquet directory under the data dir.
_PARQUET_VIEWS = {
    "kline_daily": "kline_daily",
    "kline_enriched": "kline_daily_enriched",
    "kline_index_daily": "kline_index_daily",
    "kline_index_enriched": "kline_index_enriched",
    "kline_minute": "kline_minute",
    "adj_factor": "adj_factor",
    "instruments": "instruments",
    "instruments_index": "instruments_index",
}


def _view_sql(data_dir: Path, name: str, directory: str) -> str:
    return (
        f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM "
        f"read_parquet('{Path(data_dir).as_posix()}/{directory}/**/*.parquet', union_by_name=true)"
    )


class DataStore:
    """唯一的存储入口 — 进程启动时创建。"""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir or settings.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 关键子目录(§7.2)
        for sub in (
            "kline_daily",
            "kline_daily_enriched",
            "kline_index_daily",
            "kline_index_enriched",
            "kline_minute",
            "adj_factor",
            "financials",
            "instruments",
            "instruments_index",
            "instruments_ext",
            "kline_ext",
            "pools",
            "backtest_results",
            "screener_results",
            "ai_cache",
            "user_data",
        ):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)

        # 财务数据子目录
        for sub in ("metrics", "income", "balance_sheet", "cash_flow", "shares"):
            (self.data_dir / "financials" / sub).mkdir(parents=True, exist_ok=True)

        # DuckDB 内存模式 — 不建 .db 文件(§7.1)
        self.db = duckdb.connect(database=":memory:")
        self._register_views()

    def _register_views(self) -> None:
        """把 Parquet 目录挂载为 DuckDB 视图(§7.3)。"""
        views = {**_PARQUET_VIEWS, "instruments_ext": "instruments_ext", "kline_ext": "kline_ext"}
        for sql in (_view_sql(self.data_dir, name, directory) for name, directory in views.items()):
            try:
                self.db.execute(sql)
            except Exception as e:  # noqa: BLE001
                # 空数据目录(首次启动)或权限问题时 DuckDB 会抛 IOException;
                # 跨版本/平台也可能抛 CatalogException 等。空目录缺视图不影响启动
                # (后续同步写入数据后会重新刷新视图),这里一律降级为 debug 日志。
                logger.debug("view registration skipped (no parquet yet): %s", sql[:60])


_MINUTE_FILL_COLUMNS = (
    "symbol", "datetime", "open", "high", "low", "close", "volume", "amount", "unattributed_volume",
)


def _available_minute_columns(lf: pl.LazyFrame) -> list[str]:
    available = set(lf.collect_schema().names())
    return [name for name in _MINUTE_FILL_COLUMNS if name in available]


# Stored enriched columns that cannot be recomputed from OHLCV; strategies score and filter on them.
_STORED_STATE_COLUMNS = ("turnover_rate", "consecutive_limit_ups", "consecutive_limit_downs")

# Sessions computed ahead of a history window, so its first session has complete 60-bar indicators.
HISTORY_WARMUP_SESSIONS = 60
_PUBLISHED_DAILY_CACHE_ENTRIES = 64


def _index_with_raw_close(df: pl.DataFrame) -> pl.DataFrame:
    """Index bars carry no forward adjustment, so raw_close is their close."""
    if df.is_empty() or "raw_close" in df.columns or "close" not in df.columns:
        return df
    return df.with_columns(pl.col("close").alias("raw_close"))


class KlineRepository:
    """日 K / 分钟 K 的读写入口。"""

    def __init__(self, store: DataStore) -> None:
        self.store = store
        self.db = store.db
        self._lock = threading.Lock()
        # 序列化 parquet 分区的读-改-写: 实时轮询线程、手动 refresh、盘后管道
        # 可能并发 merge/flush 同一分区文件, 无锁会互相覆盖丢数据
        self._write_lock = threading.Lock()

        # ---- Polars 缓存 ----
        self._enriched_cache: pl.DataFrame | None = None       # 最新一天 (~5500行)
        self._enriched_cache_date: date | None = None
        # Dates the exchange has actually published, used to gate realtime writes.
        # Invalidated by refresh_cache(); see _official_stock_dates().
        self._official_dates: frozenset[date] | None = None
        self._instruments_cache: pl.DataFrame | None = None
        # 完整 enriched 历史 (含所有指标, 供 filter_history 策略使用)
        self._enriched_history_cache: pl.DataFrame | None = None  # ~100万行
        self._enriched_history_start: date | None = None
        self._enriched_history_generation: str | None = None
        self._index_instruments_cache: pl.DataFrame | None = None
        # symbol 集合 memo (随对应 instruments 缓存失效): 供每请求资产分流用
        self._index_symbol_set_cache: set[str] | None = None
        # 三表合并的 symbol → name memo; 只在 clear_cache() 里重置会让
        # get_name_map 在缓存从未失效过的进程里撞上 AttributeError。
        self._name_map_cache: dict[str, str] | None = None
        self._index_enriched_cache: pl.DataFrame | None = None
        self._index_enriched_cache_date: date | None = None
        # (enriched generation, symbol, start, end) -> published daily rows, least recently used first.
        self._published_daily_cache: OrderedDict[tuple, pl.DataFrame] = OrderedDict()
        self._published_daily_lock = threading.Lock()
        from app.taiwan.live_enriched import TaiwanLiveEnriched

        self._taiwan_live = TaiwanLiveEnriched(store.data_dir)

        # ---- enriched 后台预热 ----
        # 启动时 compute_indicators (107万行, 低配机 50s+) 移出 lifespan 关键路径,
        # 推到 daemon 线程异步完成。预热期间 get_enriched_latest
        # 返回空表 (上层优雅降级), 不触发同步重算 (否则会抵消异步化收益)。
        self._enriched_warming: bool = False
        self._warmup_thread: threading.Thread | None = None
        self._warmup_lock = threading.Lock()
        # 预热完成后的回调 (lifespan 注入, 用于设置 app.state.indicators_ready)
        self._on_warmup_done: Callable[[], None] | None = None
        # parquet/instruments 同步刷新完成后的轻量回调；用于调度派生缓存预热。
        self._on_refresh_done: Callable[[], None] | None = None

        # parquet glob 路径
        self._enriched_glob = str(store.data_dir / "kline_daily_enriched" / "**" / "*.parquet")
        self._index_enriched_glob = str(store.data_dir / "kline_index_enriched" / "**" / "*.parquet")
        self._minute_glob = str(store.data_dir / "kline_minute" / "**" / "*.parquet")
        self._inst_glob = str(store.data_dir / "instruments" / "**" / "*.parquet")
        self._index_inst_glob = str(store.data_dir / "instruments_index" / "**" / "*.parquet")

    def execute_all(self, sql: str, params: list | None = None) -> list[tuple]:
        """线程安全的 SELECT → fetchall。DuckDB 单 connection 非线程安全，所有读路径须走此方法。"""
        with self._lock:
            cursor = self.db.cursor()
            try:
                return cursor.execute(sql, params or []).fetchall()
            finally:
                cursor.close()

    def execute_one(self, sql: str, params: list | None = None) -> tuple | None:
        """线程安全的 SELECT → fetchone。"""
        with self._lock:
            cursor = self.db.cursor()
            try:
                return cursor.execute(sql, params or []).fetchone()
            finally:
                # A result left unfinished keeps a parquet file open, which blocks replacing it on Windows.
                cursor.close()

    # ================================================================
    # Polars 缓存管理
    # ================================================================

    def refresh_cache(self, background: bool = False) -> None:
        """刷新 Polars 缓存。在 pipeline 完成后、服务启动时调用。

        background=True (启动时): instruments/index 同步刷新 (毫秒级),
        enriched 的重计算 (compute_indicators, 107万行) 推到 daemon 线程,
        不阻塞 FastAPI lifespan。预热期间上层走空表降级。
        background=False (盘后管道/手动刷新): 全部同步, 保证数据即时一致。
        """
        started = time.perf_counter()
        logger.info("cache refresh start (background=%s)", background)

        # The post-close publish is what makes a new day official, and this runs
        # right after it; a stale set here would keep gating out writes for a day
        # whose data has just landed.
        self._official_dates = None

        step = time.perf_counter()
        logger.info("cache refresh step start: instruments")
        self._refresh_instruments()
        logger.info("cache refresh step done: instruments (%.2fs)", time.perf_counter() - step)

        step = time.perf_counter()
        logger.info("cache refresh step start: index instruments")
        self._refresh_index_instruments()
        logger.info("cache refresh step done: index instruments (%.2fs)", time.perf_counter() - step)

        # 指数 enriched 只失效不重建 (懒加载)
        self._index_enriched_cache = None
        self._index_enriched_cache_date = None

        if background:
            logger.info("cache refresh: enriched 推后台线程预热")
            self._start_enriched_warmup()
        else:
            step = time.perf_counter()
            logger.info("cache refresh step start: enriched")
            self._refresh_enriched()
            logger.info("cache refresh step done: enriched (%.2fs)", time.perf_counter() - step)
            self._notify_refresh_done()

        logger.info("cache refresh done (%.2fs)", time.perf_counter() - started)

    def _start_enriched_warmup(self) -> None:
        """启动后台 daemon 线程预热 enriched 缓存 (compute_indicators)。

        仿 QuoteService 的线程模式: 设 warming 标志 → 起 daemon → 完成后清标志 +
        触发回调。重复调用时若已有线程在跑则跳过 (避免重复预热)。
        """
        with self._warmup_lock:
            if self._enriched_warming:
                logger.info("enriched warmup already in progress, skip")
                return
            self._enriched_warming = True

        def _warmup() -> None:
            t0 = time.perf_counter()
            try:
                logger.info("enriched warmup thread started")
                self._refresh_enriched()
                logger.info("enriched warmup thread done (%.1fs)", time.perf_counter() - t0)
                self._notify_refresh_done()
            except Exception:  # noqa: BLE001
                logger.exception("enriched warmup thread failed")
            finally:
                with self._warmup_lock:
                    self._enriched_warming = False
                cb = self._on_warmup_done
                if cb is not None:
                    try:
                        cb()
                    except Exception:  # noqa: BLE001
                        logger.warning("enriched warmup callback failed", exc_info=True)

        self._warmup_thread = threading.Thread(
            target=_warmup, name="enriched-warmup", daemon=True,
        )
        self._warmup_thread.start()

    def _notify_refresh_done(self) -> None:
        callback = self._on_refresh_done
        if callback is None:
            return
        try:
            callback()
        except Exception:  # noqa: BLE001
            logger.warning("repository refresh callback failed", exc_info=True)

    @property
    def enriched_ready(self) -> bool:
        """enriched 缓存是否已就绪 (非 None 且不在后台预热中)。"""
        return not self._enriched_warming and self._enriched_cache is not None

    def clear_cache(self) -> None:
        """清空所有 Polars 内存缓存。

        与 refresh_cache 的区别: refresh_cache 在磁盘无数据时会提前 return,
        导致内存里的旧缓存残留 (clear 数据后看板仍显示旧数据的根因)。
        本方法无条件清空, 供清除数据/重置场景调用。
        """
        self._enriched_cache = None
        self._enriched_cache_date = None
        self._enriched_history_cache = None
        self._enriched_history_start = None
        self._enriched_history_generation = None
        self._instruments_cache = None
        self._index_instruments_cache = None
        self._index_symbol_set_cache = None
        self._name_map_cache = None
        self._index_enriched_cache = None
        self._index_enriched_cache_date = None
        with self._published_daily_lock:
            self._published_daily_cache.clear()
        self._taiwan_live.invalidate()

    def _refresh_enriched(self) -> None:
        """Load the latest enriched day and about 300 days of history into memory.

        The history keeps published rows as stored and recomputes only rows written without the
        publish (read_enriched_window); filter_history strategies and backtests reuse it.
        """
        self._prepare_taiwan_live()
        try:
            started = time.perf_counter()
            refresh_generation = self.get_matrix_data_generation("stock")
            logger.info("enriched refresh start")

            step = time.perf_counter()
            logger.info("enriched refresh step start: latest date")
            latest = self._latest_enriched_date_duckdb()
            logger.info("enriched refresh step done: latest date=%s (%.2fs)", latest, time.perf_counter() - step)
            if not latest:
                # 磁盘已无数据: 必须清空内存缓存, 否则旧数据会残留
                # (清数据后看板仍显示旧数据的根因)
                self.clear_cache()
                logger.info("enriched refresh skipped: no latest date (%.2fs)", time.perf_counter() - started)
                return

            # Step 1: 直接读最新日期的分区文件 (仅 14 列)
            enriched_dir = self.store.data_dir / "kline_daily_enriched"
            ds = latest.isoformat() if hasattr(latest, "isoformat") else str(latest)
            target_parquet = enriched_dir / f"date={ds}" / "part.parquet"

            if not target_parquet.exists():
                logger.info("enriched refresh skipped: %s not found (%.2fs)", target_parquet, time.perf_counter() - started)
                return

            step = time.perf_counter()
            logger.info("enriched refresh step start: read latest parquet %s", target_parquet)
            df_latest = pl.read_parquet(target_parquet)
            logger.info("enriched refresh step done: read latest parquet rows=%d (%.2fs)", len(df_latest), time.perf_counter() - step)
            if df_latest.is_empty():
                logger.info("enriched refresh skipped: latest parquet empty (%.2fs)", time.perf_counter() - started)
                return

            # Read the last 300 calendar days into the history cache; the latest day is cached too.
            # About 200 sessions: get_enriched_history serves lookbacks up to one less; the
            # screener reads longer strategy windows itself.
            try:
                from datetime import timedelta
                from app.indicators.pipeline import compute_limit_signals
                start_full = latest - timedelta(days=300)
                instruments = self._instruments_cache if self._instruments_cache is not None else pl.DataFrame()

                def limit_signals(frame: pl.DataFrame) -> pl.DataFrame:
                    return compute_limit_signals(frame, instruments)

                step = time.perf_counter()
                logger.info("enriched refresh step start: read history from %s", start_full)
                df_full = read_enriched_window(
                    self.store.data_dir,
                    "kline_daily_enriched",
                    start_full,
                    latest,
                    limit_signals=None if instruments.is_empty() else limit_signals,
                )
                logger.info("enriched refresh step done: read history rows=%d (%.2fs)", len(df_full), time.perf_counter() - step)
                if not df_full.is_empty():
                    # JOIN instruments 到完整历史 (filter_history/basic_filter 需要 name/股本等列)
                    if instruments is not None and not instruments.is_empty():
                        inst_cols = [c for c in ["name", "total_shares", "float_shares"]
                                     if c in instruments.columns and c not in df_full.columns]
                        if inst_cols:
                            step = time.perf_counter()
                            logger.info("enriched refresh step start: join instruments")
                            df_full = df_full.join(
                                instruments.select(["symbol", *inst_cols]).unique(subset=["symbol"]),
                                on="symbol",
                                how="left",
                            )
                            logger.info("enriched refresh step done: join instruments (%.2fs)", time.perf_counter() - step)

                    # 缓存完整历史 (含指标+必要基础信息) 供 filter_history/backtest 直接复用
                    if self.get_matrix_data_generation("stock") != refresh_generation:
                        raise EnrichedGenerationUnavailableError(
                            "enriched data changed while refreshing its history cache"
                        )
                    self._enriched_history_cache = df_full
                    self._enriched_history_start = df_full["date"].min()
                    self._enriched_history_generation = refresh_generation
                    logger.info("enriched 历史缓存: %d rows, %s ~ %s",
                                len(df_full), self._enriched_history_start, latest)

                    # 只取最新一天作为 enriched_cache
                    df_today = df_full.filter(pl.col("date") == latest)
                    if not df_today.is_empty():
                        self._enriched_cache = df_today
                        self._enriched_cache_date = latest
                        logger.info("enriched 缓存已计算: %d 只, 日期 %s (即时计算)", len(df_today), latest)
                        logger.info("enriched refresh done (%.2fs)", time.perf_counter() - started)
                        return
            except EnrichedGenerationUnavailableError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("enriched 即时计算失败, 使用原始 14 列缓存: %s", e)

            # 降级: 直接使用 14 列数据
            self._enriched_cache = df_latest
            self._enriched_cache_date = latest

            logger.info("enriched 缓存已加载: %d 只, 日期 %s", len(df_latest), latest)
            logger.info("enriched refresh done fallback (%.2fs)", time.perf_counter() - started)
        except Exception as e:  # noqa: BLE001
            logger.warning("enriched 缓存刷新失败: %s", e)

    def _prepare_taiwan_live(self) -> None:
        """Rebuild the Taiwan intraday history when the stored bars, events or master changed."""
        try:
            self._taiwan_live.prepare()
        except Exception as e:
            logger.warning("taiwan live history preparation failed: %s", e)

    def get_taiwan_live_enriched(self):
        """The TaiwanLiveEnriched computing today's Taiwan rows from this data dir's stored history."""
        return self._taiwan_live

    def _refresh_index_enriched(self) -> None:
        """从指数 enriched parquet 加载最新日到内存缓存 (300天重算通用指标)。

        磁盘窄表无指标列, 必须 scan 近 300 天重算, 否则监控信号规则无列可评估。
        指数无复权需求, 不读 raw_close/raw_high/raw_low。
        """
        try:
            enriched_dir = self.store.data_dir / "kline_index_enriched"
            dates = sorted(
                p.name[5:] for p in enriched_dir.glob("date=*")
                if p.is_dir() and p.name.startswith("date=")
            ) if enriched_dir.exists() else []
            if not dates:
                self._index_enriched_cache = None
                self._index_enriched_cache_date = None
                return
            latest = date.fromisoformat(dates[-1])
            target_parquet = enriched_dir / f"date={dates[-1]}" / "part.parquet"
            df_latest = pl.read_parquet(target_parquet)
            if df_latest.is_empty():
                return

            from datetime import timedelta
            start_full = latest - timedelta(days=300)
            read_cols = [c for c in ["symbol", "date", "open", "high", "low", "close",
                                     "volume", "amount"]
                         if c in df_latest.columns]
            df_hist = (
                scan_enriched_parquet(
                    self._index_enriched_glob,
                    cast_options=pl.ScanCastOptions(
                        integer_cast="allow-float", float_cast="upcast"
                    ),
                )
                .filter(pl.col("date") >= start_full)
                .select(read_cols)
                .sort(["symbol", "date"])
                .collect()
            )
            if df_hist.is_empty():
                self._index_enriched_cache = _index_with_raw_close(df_latest.sort(["symbol"]))
            else:
                df_full = self._compute_index_enriched_range(df_hist)
                self._index_enriched_cache = _index_with_raw_close(
                    df_full.filter(pl.col("date") == latest).sort(["symbol"])
                )
            self._index_enriched_cache_date = latest
        except Exception as e:  # noqa: BLE001
            logger.debug("指数 enriched 缓存刷新跳过: %s", e)

    def _refresh_instruments(self) -> None:
        """加载 instruments 到内存。"""
        if not any((self.store.data_dir / "instruments").rglob("*.parquet")):
            if self._instruments_cache is None:
                self._instruments_cache = pl.DataFrame()
            return
        try:
            df = pl.scan_parquet(self._inst_glob).collect()
            if not df.is_empty():
                self._instruments_cache = df
                self._name_map_cache = None
                logger.info("instruments 缓存已加载: %d 只", len(df))
        except Exception as e:  # noqa: BLE001
            logger.warning("instruments 缓存刷新失败: %s", e)

    def _refresh_index_instruments(self) -> None:
        """加载指数 instruments 到内存。"""
        try:
            df = pl.scan_parquet(self._index_inst_glob).collect()
            if not df.is_empty():
                self._index_instruments_cache = df
                self._index_symbol_set_cache = None
                self._name_map_cache = None
                logger.info("index instruments 缓存已加载: %d 只", len(df))
        except Exception as e:  # noqa: BLE001
            logger.debug("index instruments 缓存刷新跳过: %s", e)

    def get_enriched_latest(self) -> tuple[pl.DataFrame, date | None]:
        """返回缓存的 enriched 最新日 DataFrame + 日期。如无缓存则懒加载。

        后台预热期间 (_enriched_warming=True) 返回空表, 不触发同步重算 ——
        否则首次访问会把异步化想避免的 50s+ 计算拉回同步路径。
        """
        if self._enriched_cache is None:
            if self._enriched_warming:
                return pl.DataFrame(), None
            self._refresh_enriched()
        if self._enriched_cache is None:
            return pl.DataFrame(), self._enriched_cache_date
        return self._enriched_cache, self._enriched_cache_date

    def get_enriched_latest_asset(self, asset_type: str, refresh: bool = True) -> tuple[pl.DataFrame, date | None]:
        """按资产类型返回最新 enriched 缓存。stock 保持旧缓存语义。

        refresh=False: 缓存冷时不触发同步 _refresh_index_enriched(300 天 scan+compute),
        供行情轮询线程使用。Taiwan ETFs are stock rows, so the etf asset type has no cache.
        """
        if asset_type == "stock":
            return self.get_enriched_latest()
        if asset_type == "index":
            if self._index_enriched_cache is None and refresh:
                self._refresh_index_enriched()
            if self._index_enriched_cache is None:
                return pl.DataFrame(), self._index_enriched_cache_date
            return self._index_enriched_cache, self._index_enriched_cache_date
        return pl.DataFrame(), None

    def get_enriched_history(self, target_date: date, lookback_days: int) -> pl.DataFrame | None:
        """The cached enriched rows of the last lookback_days + 1 sessions through target_date.

        None unless the cache reaches target_date and holds that many sessions. Cached rows carry the
        published indicators (read_enriched_window), so the window's first session needs no warm-up.
        """
        cache = self._enriched_history_cache
        if cache is None or cache.is_empty() or "date" not in cache.columns:
            return None
        if cache["date"].max() < target_date:
            return None
        sessions = cache.select(
            pl.col("date").filter(pl.col("date") <= target_date).unique().sort()
        ).to_series()
        window = lookback_days + 1
        if sessions.len() < window:
            return None
        return cache.filter((pl.col("date") >= sessions[-window]) & (pl.col("date") <= target_date))

    def get_enriched_range(
        self,
        start: date,
        end: date,
        symbols: list[str] | None = None,
        columns: list[str] | None = None,
    ) -> pl.DataFrame | None:
        """从预计算 enriched 历史缓存返回完整区间；缓存不覆盖时返回 None。"""
        if self._enriched_history_cache is None:
            if self._enriched_warming:
                # 后台预热中: 返回 None (缓存不覆盖), 调用方各自走慢路径;
                # 否则请求线程会与预热线程并发重复 300 天全量重算
                # (同 get_enriched_latest 的守卫语义)。
                return None
            self._refresh_enriched()
        cache = self._enriched_history_cache
        data_dir = getattr(getattr(self, "store", None), "data_dir", None)
        if data_dir is not None:
            try:
                current_generation = self.get_matrix_data_generation("stock")
            except EnrichedGenerationUnavailableError:
                return None
            if self._enriched_history_generation != current_generation:
                return None
        if cache is None or cache.is_empty() or "date" not in cache.columns:
            return None

        cache_min = cache["date"].min()
        cache_max = cache["date"].max()
        if cache_min > start or cache_max < end:
            return None

        df = cache.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        if symbols is not None:
            df = df.filter(pl.col("symbol").is_in(symbols))
        if columns and not df.is_empty():
            existing = [c for c in columns if c in df.columns]
            if "symbol" not in existing and "symbol" in df.columns:
                existing.insert(0, "symbol")
            if "date" not in existing and "date" in df.columns:
                existing.insert(1, "date")
            df = df.select(existing)
        return df.sort(["symbol", "date"])

    def get_instruments(self) -> pl.DataFrame:
        """返回缓存的 instruments DataFrame。如无缓存则懒加载。"""
        if self._instruments_cache is None:
            self._refresh_instruments()
        if self._instruments_cache is None:
            return pl.DataFrame()
        return self._instruments_cache

    def get_index_instruments(self) -> pl.DataFrame:
        """返回缓存的指数 instruments DataFrame。如无缓存则懒加载。"""
        if self._index_instruments_cache is None:
            self._refresh_index_instruments()
        if self._index_instruments_cache is None:
            return pl.DataFrame()
        return self._index_instruments_cache

    def get_instruments_asset(self, asset_type: str) -> pl.DataFrame:
        """按资产类型返回 instruments；老 stock 路径保持原样。"""
        if asset_type == "stock":
            return self.get_instruments()
        if asset_type == "index":
            df = self.get_index_instruments()
            if not df.is_empty() and "asset_type" in df.columns:
                return df.filter(pl.col("asset_type") != "etf")
            return df
        return pl.DataFrame()

    def get_index_symbol_set(self) -> set[str]:
        """返回已缓存指数 symbol 集合 (memo, 随 instruments 缓存失效)。"""
        if self._index_symbol_set_cache is None:
            df = self.get_index_instruments()
            if df.is_empty() or "symbol" not in df.columns:
                return set()
            self._index_symbol_set_cache = set(df["symbol"].cast(pl.Utf8).to_list())
        return self._index_symbol_set_cache

    def resolve_asset_type(self, symbol: str) -> str:
        """按 symbol 判定资产类型: index / stock(默认)。

        供 API 层对单标的查询做资产分流 (get_daily_asset 等)。Taiwan ETFs resolve to stock: their
        bars share the stock tables. 指数集合为 memo, 每请求查询成本可忽略。
        """
        if symbol in self.get_index_symbol_set():
            return "index"
        return "stock"

    def get_name_map(self, symbols: list[str] | None = None) -> dict[str, str]:
        """返回 {symbol: name} 映射, 合并股票 + 指数 instruments (股票优先去重)。

        自选列表/名称批查等场景的统一名称解析入口, 避免各调用方自行合并两份缓存。
        symbols 非 None 时只返回命中的条目。
        全量结果缓存在 _name_map_cache (随两份 instruments 维表刷新失效),
        避免每请求对 ~7000 行维表做 iter_rows 重建。
        """
        if self._name_map_cache is not None:
            if symbols is None:
                return dict(self._name_map_cache)
            wanted = set(symbols)
            return {s: n for s, n in self._name_map_cache.items() if s in wanted}
        # 只构建并缓存全量映射; symbols 过滤只作用于返回值。
        # 若把过滤后的结果写入缓存, 后续不同 symbols 的查询会命中残缺缓存,
        # 导致新加入自选的标的查不到名称。
        name_map: dict[str, str] = {}
        for df in (self.get_instruments(), self.get_instruments_asset("index")):
            if df.is_empty() or "symbol" not in df.columns or "name" not in df.columns:
                continue
            for symbol, name in df.select(["symbol", "name"]).iter_rows():
                name_map.setdefault(symbol, name)
        self._name_map_cache = name_map
        if symbols is None:
            return dict(name_map)
        wanted = set(symbols)
        return {s: n for s, n in name_map.items() if s in wanted}

    def enriched_latest_date(self) -> date | None:
        """返回缓存中的 enriched 最新日期。"""
        return self._enriched_cache_date

    def _official_stock_dates(self) -> frozenset[date]:
        """Dates the exchange has published, cached until refresh_cache().

        Deliberately reads TaiwanDailyStore rather than the enriched partitions:
        a realtime write creates an enriched partition for a day the exchange has
        not published, so trusting those would let existing contamination vouch
        for itself and admit more.
        """
        if self._official_dates is None:
            from app.taiwan.daily_store import TaiwanDailyStore

            self._official_dates = frozenset(TaiwanDailyStore().available_dates())
        return self._official_dates

    def _live_write_allowed(self, dt, *, force: bool) -> bool:
        """Whether a realtime write for *dt* may touch disk.

        The live poller fetches only the watchlist, so letting it create a
        partition for a day the exchange has not published yields a day that
        looks complete but holds a handful of symbols. Writes for such a day are
        kept in memory only, where "today" is what the live readers want anyway.
        """
        if force:
            return True
        try:
            d = dt if isinstance(dt, date) else date.fromisoformat(str(dt))
        except (TypeError, ValueError):
            return False
        return self.date_has_official_stock_data(d)

    def date_has_official_stock_data(self, d: date) -> bool:
        """Whether *d* is a day the exchange has published data for."""
        return d in self._official_stock_dates()

    def taiwan_official_latest_date(self) -> date | None:
        """Most recent trading day that has official exchange data.

        Deliberately distinct from enriched_latest_date(), which tracks "today"
        for the live quote path and advances on a single realtime tick. Anything
        scanning the whole market must use this instead: a nine-symbol realtime
        write would otherwise make an unfinished day look like a complete one.

        A day whose bars are stored but not yet published to the enriched table
        has no indicators to screen, so the latest published day before it is used.
        """
        from app.taiwan.daily_store import TaiwanDailyStore

        dates = TaiwanDailyStore().available_dates()
        if not dates:
            return None
        enriched = Path(self.store.data_dir) / "kline_daily_enriched"
        for d in reversed(dates):
            if (enriched / f"date={d.isoformat()}" / "part.parquet").exists():
                return d
        return dates[-1]

    # ================================================================
    # 热路径: Polars 查询 (Chart / Screener / Signals / Intraday)
    # ================================================================

    def get_daily(
        self,
        symbol: str,
        start: date,
        end: date,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        """One symbol's daily K-line from its published enriched columns.

        Only indicators a partition lacks are computed.
        """
        from datetime import timedelta

        # Fast path when parquet stores every requested column (e.g. OHLCV for a mini candlestick):
        # a narrow scan skips the warm-up and _compute_enriched_range, enriched_latest overrides the
        # latest day (fresher intraday), and a missing column falls back to the full path below.
        # While a rewrite is half done the partitions mix price scales; read the caches only.
        publishing = self._enriched_publishing()
        if columns and not publishing:
            df = self._scan_daily_symbol(symbol, start, end, columns)
            if not df.is_empty() and all(c in df.columns for c in columns):
                cached, cache_date = self.get_enriched_latest()
                if cached is not None and not cached.is_empty() and cache_date:
                    if start <= cache_date <= end:
                        cached_part = self._filter_cached(cached, symbol, columns)
                        if not cached_part.is_empty():
                            df = df.filter(pl.col("date") != cache_date)
                            common_cols = [c for c in df.columns if c in cached_part.columns]
                            df = pl.concat([df.select(common_cols), cached_part.select(common_cols)])
                return df

        warmup_start = start - timedelta(days=indicator_warmup_days())

        # Published indicators come from the whole stored history; a short recompute window
        # would seed EMA/MACD/RSI/KD at its own first bar and disagree with them.
        df = pl.DataFrame() if publishing else self._published_daily_symbol(symbol, start, end)
        from_published = not df.is_empty()
        if not from_published:
            hist = self._enriched_history_cache
            if hist is not None and not hist.is_empty() and "date" in hist.columns:
                hist_min = self._enriched_history_start
                hist_max = hist["date"].max()
                if hist_min is not None and (publishing or hist_min <= start) and hist_max >= start:
                    df = hist.filter(
                        (pl.col("symbol") == symbol)
                        & (pl.col("date") >= start)
                        & (pl.col("date") <= end)
                    )
        if df.is_empty() and not publishing:
            # 扫描14列 parquet
            df = self._scan_daily_symbol(symbol, warmup_start, end, None)
            if not df.is_empty():
                df = self._compute_enriched_range(df)

        # The live cache supplies the latest day unless a published partition already holds it.
        cached, cache_date = self.get_enriched_latest()
        if not df.is_empty() and cached is not None and not cached.is_empty() and cache_date:
            if start <= cache_date <= end and not (
                from_published and (df["date"] == cache_date).any()
            ):
                cached_part = self._filter_cached(cached, symbol, None)
                if not cached_part.is_empty():
                    df = df.filter(pl.col("date") != cache_date)
                    common_cols = [c for c in df.columns if c in cached_part.columns]
                    df = pl.concat([df.select(common_cols), cached_part.select(common_cols)])

        # 裁剪到请求范围
        if not df.is_empty():
            df = df.filter((pl.col("date") >= start) & (pl.col("date") <= end))

        if columns and not df.is_empty():
            existing = [c for c in columns if c in df.columns]
            df = df.select(existing)

        return df

    def get_daily_batch(
        self,
        symbols: list[str],
        start: date,
        end: date,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        """批量日K查询。"""
        cached, cache_date = self.get_enriched_latest()
        if cached is not None and not cached.is_empty() and cache_date:
            if start >= cache_date:
                return self._filter_cached_batch(cached, symbols, columns)
        if self._enriched_publishing():
            hist = self._enriched_history_cache
            if hist is None or hist.is_empty():
                return pl.DataFrame()
            df = hist.filter(
                pl.col("symbol").is_in(symbols) & (pl.col("date") >= start) & (pl.col("date") <= end)
            ).sort(["symbol", "date"])
            return df.select([c for c in columns if c in df.columns]) if columns else df

        # 回退 scan_parquet
        return self._scan_daily_batch(symbols, start, end, columns)

    def _enriched_publishing(self) -> bool:
        """Whether a stock enriched publication is rewriting partitions right now."""
        store = getattr(self, "store", None)
        return store is not None and enriched_publication_incomplete(store.data_dir, "stock")

    def get_index_daily(
        self,
        symbol: str,
        start: date,
        end: date,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        """指数日K查询 — 从独立指数 enriched parquet 读取后即时计算通用指标。"""
        from datetime import timedelta

        # Fast path when parquet stores every requested column (e.g. OHLCV for a mini candlestick):
        # one narrow scan skips the warm-up and _compute_index_enriched_range; a missing column
        # falls back to the full path below, wasting only that scan.
        if columns:
            df = self._scan_index_daily_symbol(symbol, start, end, columns)
            if "raw_close" in columns:
                df = _index_with_raw_close(df)
            if not df.is_empty() and all(c in df.columns for c in columns):
                return df

        warmup_start = start - timedelta(days=indicator_warmup_days())
        df = self._scan_index_daily_symbol(symbol, warmup_start, end, None)
        if not df.is_empty():
            df = _index_with_raw_close(self._compute_index_enriched_range(df))
            df = df.filter((pl.col("date") >= start) & (pl.col("date") <= end))
        if columns and not df.is_empty():
            existing = [c for c in columns if c in df.columns]
            df = df.select(existing)
        return df

    def get_daily_asset(
        self,
        asset_type: str,
        symbol: str,
        start: date,
        end: date,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        if asset_type == "stock":
            return self.get_daily(symbol, start, end, columns)
        if asset_type == "index":
            return self.get_index_daily(symbol, start, end, columns)
        return pl.DataFrame()

    @staticmethod
    def _scan_minute(source: str | list[str]) -> pl.LazyFrame:
        """Scan minute partitions, whether or not they carry the later unattributed_volume column."""
        schema = dict(pl.scan_parquet(source).collect_schema())
        schema.setdefault("unattributed_volume", pl.Float64)
        return pl.scan_parquet(source, schema=schema, missing_columns="insert", extra_columns="ignore")

    def get_minute(self, symbol: str, trade_date: date) -> pl.DataFrame:
        """分钟K查询 — Polars scan_parquet + predicate pushdown。"""
        try:
            return self._scan_minute(self._minute_glob).filter(
                (pl.col("symbol") == symbol)
                & (pl.col("datetime").dt.date() == trade_date)
            ).sort("datetime").collect()
        except Exception as e:  # noqa: BLE001
            logger.warning("分钟K查询失败: %s", e)
            return pl.DataFrame()

    def get_minute_batch(self, symbols: list[str], trade_date: date) -> pl.DataFrame:
        """批量分钟K查询 — 多 symbol 一次 scan_parquet。

        用于自选列表分时图: 一次 predicate pushdown 读多只股票当日分钟K,
        避免逐只查询的 N 次 I/O。
        """
        if not symbols:
            return pl.DataFrame()
        try:
            return self._scan_minute(self._minute_glob).filter(
                pl.col("symbol").is_in(symbols)
                & (pl.col("datetime").dt.date() == trade_date)
            ).sort(["symbol", "datetime"]).collect()
        except Exception as e:  # noqa: BLE001
            logger.warning("批量分钟K查询失败: %s", e)
            return pl.DataFrame()

    def get_minute_range(self, symbols: list[str], start: date, end: date) -> pl.DataFrame:
        """多 symbol × 日期范围的分钟K查询 (分钟K精确回测用)。

        一次 scan_parquet + predicate pushdown 读多只股票在 [start, end] 内的所有分钟K。
        返回列: symbol, datetime, open, high, low, close, volume, amount, unattributed_volume。
        无分钟K分区时返回空表; 分区不可读时抛出异常。
        """
        if not symbols:
            return pl.DataFrame()
        base = Path(self.store.data_dir) / "kline_minute"
        if next(base.rglob("*.parquet"), None) is None:
            return pl.DataFrame()
        lf = self._scan_minute(self._minute_glob)
        return (
            lf.select(_available_minute_columns(lf))
            .filter(
                pl.col("symbol").is_in(symbols)
                & (pl.col("datetime").dt.date() >= start)
                & (pl.col("datetime").dt.date() <= end)
            )
            .sort(["symbol", "datetime"])
            .collect(engine="streaming")
        )

    def get_minute_by_dates(self, symbols: list[str], dates: list[date]) -> pl.DataFrame:
        """按日期列表精确读取分钟K分区文件 (分钟K精确回测用)。

        与 get_minute_range 的区别: 后者扫描 [start, end] 区间全部日期的 parquet
        (触发日稀疏时会读大量无关日期 → 爆内存); 本方法只读 dates 里列出的日期
        对应的分区文件 (date=YYYY-MM-DD/part.parquet), 内存与回测区间长度解耦,
        只随触发日数量增长。

        缺失的日期分区直接跳过 (该日无分钟K数据); 分区不可读时抛出异常。
        返回列: symbol, datetime, open, high, low, close, volume, amount, unattributed_volume
        (旧分区没有 unattributed_volume 时为空值)。
        """
        if not symbols or not dates:
            return pl.DataFrame()
        base = Path(self.store.data_dir) / "kline_minute"
        # 收集存在的分区文件路径, 避免对不存在的文件 scan 报错
        parts: list[str] = []
        for d in dates:
            p = base / f"date={d.isoformat()}" / "part.parquet"
            if p.exists():
                parts.append(str(p))
        if not parts:
            return pl.DataFrame()
        lf = self._scan_minute(parts)
        return (
            lf.select(_available_minute_columns(lf))
            .filter(pl.col("symbol").is_in(symbols))
            .sort(["symbol", "datetime"])
            .collect(engine="streaming")
        )

    # ================================================================
    # Polars 查询内部方法
    # ================================================================

    def _compute_enriched_range(self, df: pl.DataFrame) -> pl.DataFrame:
        """对14列enriched数据即时计算完整指标+信号。输入应含足够预热行数。"""
        from app.indicators.pipeline import compute_indicators, compute_signals, compute_limit_signals, filter_halt_days
        if df.is_empty() or df.height < 2:
            return df
        # 兜底过滤历史脏数据中的停牌日 (close 可能被填充为前收盘价)
        df = filter_halt_days(df)
        if df.is_empty() or df.height < 2:
            return df
        try:
            df = compute_indicators(df)
            df = compute_signals(df)
            instruments = self.get_instruments()
            df = compute_limit_signals(df, instruments)
        except Exception as e:  # noqa: BLE001
            logger.warning("on-demand compute failed: %s", e)
        return df

    def _compute_index_enriched_range(self, df: pl.DataFrame) -> pl.DataFrame:
        """指数只计算通用技术指标和通用信号，跳过涨跌停/股本/市值逻辑。"""
        from app.indicators.pipeline import compute_indicators, compute_signals
        if df.is_empty() or df.height < 2:
            return df
        try:
            df = compute_indicators(df)
            df = compute_signals(df)
        except Exception as e:  # noqa: BLE001
            logger.warning("index on-demand compute failed: %s", e)
        return df

    def _filter_cached(self, cached: pl.DataFrame, symbol: str, columns: list[str] | None) -> pl.DataFrame:
        df = cached.filter(pl.col("symbol") == symbol)
        if columns and not df.is_empty():
            existing = [c for c in columns if c in df.columns]
            df = df.select(existing)
        return df

    def _filter_cached_batch(self, cached: pl.DataFrame, symbols: list[str], columns: list[str] | None) -> pl.DataFrame:
        df = cached.filter(pl.col("symbol").is_in(symbols))
        if columns and not df.is_empty():
            existing = [c for c in columns if c in df.columns]
            df = df.select(existing)
        return df.sort(["symbol", "date"])

    def _published_partitions(self, start: date, end: date) -> list[Path]:
        """Stock enriched partition files dated within [start, end], oldest first."""
        enriched_dir = self.store.data_dir / "kline_daily_enriched"
        low, high = f"date={start.isoformat()}", f"date={end.isoformat()}"
        try:
            with os.scandir(enriched_dir) as entries:
                names = sorted(e.name for e in entries if low <= e.name <= high and e.is_dir())
        except FileNotFoundError:
            return []
        files = [enriched_dir / name / "part.parquet" for name in names]
        return [path for path in files if path.is_file()]

    def _published_daily_symbol(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        """The symbol's published enriched rows in [start, end]; empty when they cannot be read.

        Results are kept per enriched generation, which every committed publication replaces, so
        every enriched partition write must go through EnrichedPublication: a write outside it
        keeps the generation and these cached rows go stale.
        """
        try:
            key = (self.get_matrix_data_generation("stock"), symbol, start, end)
            with self._published_daily_lock:
                cached = self._published_daily_cache.get(key)
                if cached is not None:
                    self._published_daily_cache.move_to_end(key)
                    return cached
            files = self._published_partitions(start, end)
            if not files:
                return pl.DataFrame()
            df = (
                scan_parquet_compat(
                    files,
                    schema={**pl.read_parquet_schema(files[-1]), **ENRICHED_STORAGE_SCHEMA},
                    cast_options=pl.ScanCastOptions(
                        integer_cast="allow-float", float_cast="upcast"
                    ),
                )
                .filter(pl.col("symbol") == symbol)
                .sort("date")
                .collect()
            )
        except Exception as e:
            logger.warning("published daily read failed for %s: %s", symbol, e)
            return pl.DataFrame()
        if not df.is_empty():
            df = self._with_unpublished_indicators(df, symbol, start, end)
        with self._published_daily_lock:
            self._published_daily_cache[key] = df
            while len(self._published_daily_cache) > _PUBLISHED_DAILY_CACHE_ENTRIES:
                self._published_daily_cache.popitem(last=False)
        return df

    def _with_unpublished_indicators(
        self, df: pl.DataFrame, symbol: str, start: date, end: date
    ) -> pl.DataFrame:
        """Compute indicator columns the partitions lack, and the rows of storage-only writes.

        A partition written without indicators (a storage-only live merge) reads back as such rows.
        """
        from datetime import timedelta

        from app.indicators.pipeline import INDICATOR_COLUMNS

        published = [c for c in sorted(INDICATOR_COLUMNS) if c in df.columns]
        unpublished_dates = df.filter(storage_only_rows(df.columns))["date"]
        if len(published) == len(INDICATOR_COLUMNS) and unpublished_dates.is_empty():
            return df
        computed = self._scan_daily_symbol(symbol, start - timedelta(days=indicator_warmup_days()), end, None)
        if computed.is_empty():
            return df
        computed = self._compute_enriched_range(computed)
        derived = [
            c for c in computed.columns if c not in ENRICHED_STORAGE_SCHEMA and c != "symbol"
        ]
        merged = df.join(
            computed.select(["date", *derived]).rename({c: f"__computed_{c}" for c in derived}),
            on="date",
            how="left",
        )
        fill = pl.col("date").is_in(unpublished_dates.to_list())
        merged = merged.with_columns(
            (
                pl.when(fill)
                .then(pl.col(f"__computed_{c}").cast(df.schema[c], strict=False))
                .otherwise(pl.col(c))
                if c in df.columns
                else pl.col(f"__computed_{c}")
            ).alias(c)
            for c in derived
        )
        return merged.drop([f"__computed_{c}" for c in derived])

    def _scan_daily_symbol(self, symbol: str, start: date, end: date, columns: list[str] | None) -> pl.DataFrame:
        try:
            lf = (
                scan_enriched_parquet(
                    self._enriched_glob,
                    cast_options=pl.ScanCastOptions(
                        integer_cast="allow-float", float_cast="upcast"
                    ),
                )
                .filter(
                    (pl.col("symbol") == symbol)
                    & (pl.col("date") >= start)
                    & (pl.col("date") <= end)
                )
                .sort("date")
            )
            if columns:
                schema_names = lf.collect_schema().names()
                existing = [c for c in columns if c in schema_names]
                lf = lf.select(existing)
            return lf.collect()
        except Exception as e:  # noqa: BLE001
            logger.warning("日K查询失败: %s", e)
            return pl.DataFrame()

    def _scan_daily_batch(self, symbols: list[str], start: date, end: date, columns: list[str] | None) -> pl.DataFrame:
        try:
            lf = (
                scan_enriched_parquet(
                    self._enriched_glob,
                    cast_options=pl.ScanCastOptions(
                        integer_cast="allow-float", float_cast="upcast"
                    ),
                )
                .filter(
                    (pl.col("symbol").is_in(symbols))
                    & (pl.col("date") >= start)
                    & (pl.col("date") <= end)
                )
                .sort(["symbol", "date"])
            )
            if columns:
                schema_names = lf.collect_schema().names()
                existing = [c for c in columns if c in schema_names]
                lf = lf.select(existing)
            return lf.collect()
        except Exception as e:  # noqa: BLE001
            logger.warning("日K批量查询失败: %s", e)
            return pl.DataFrame()

    def _scan_index_daily_symbol(self, symbol: str, start: date, end: date, columns: list[str] | None) -> pl.DataFrame:
        try:
            lf = (
                scan_enriched_parquet(
                    self._index_enriched_glob,
                    cast_options=pl.ScanCastOptions(
                        integer_cast="allow-float", float_cast="upcast"
                    ),
                )
                .filter(
                    (pl.col("symbol") == symbol)
                    & (pl.col("date") >= start)
                    & (pl.col("date") <= end)
                )
                .sort("date")
            )
            if columns:
                schema_names = lf.collect_schema().names()
                existing = [c for c in columns if c in schema_names]
                lf = lf.select(existing)
            return lf.collect()
        except Exception as e:  # noqa: BLE001
            logger.warning("指数日K查询失败: %s", e)
            return pl.DataFrame()

    # ================================================================
    # DuckDB 查询 (冷路径: 统计/元数据/自定义SQL)
    # ================================================================

    def latest_minute_date(self, symbol: str) -> date | None:
        try:
            row = self.execute_one(
                "SELECT max(CAST(datetime AS DATE)) FROM kline_minute WHERE symbol = ?",
                [symbol],
            )
            if row and row[0]:
                return row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0]))
        except duckdb.CatalogException:
            pass
        return None

    def latest_minute_date_global(self) -> date | None:
        """全市场最近分钟K日期 (不分 symbol)。用于非交易日回退到上一交易日。"""
        try:
            row = self.execute_one(
                "SELECT max(CAST(datetime AS DATE)) FROM kline_minute",
            )
            if row and row[0]:
                return row[0] if isinstance(row[0], date) else date.fromisoformat(str(row[0]))
        except Exception:  # noqa: BLE001
            return None

    def taiwan_daily_dates(self) -> list[date]:
        """Sessions stored in TaiwanDailyStore, the Taiwan daily source of truth (not kline_daily)."""
        from app.taiwan.daily_store import TaiwanDailyStore

        root = Path(self.store.data_dir) / "taiwan" / "daily"
        if not root.is_dir():
            return []
        return TaiwanDailyStore(root).available_dates()

    def earliest_daily_date(self) -> date | None:
        """Earliest stored Taiwan daily session."""
        dates = self.taiwan_daily_dates()
        return dates[0] if dates else None

    def earliest_minute_date(self) -> date | None:
        """本地分钟K数据的最早日期。"""
        try:
            res = self.execute_one(
                "SELECT min(CAST(datetime AS DATE)) FROM kline_minute",
            )
            if res and res[0]:
                d = res[0]
                return d if isinstance(d, date) else date.fromisoformat(str(d))
        except Exception:
            return None
        return None

    def latest_daily_date(self) -> date | None:
        """Latest stored Taiwan daily session; a partition exists only for a day the market traded."""
        dates = self.taiwan_daily_dates()
        return dates[-1] if dates else None

    def latest_enriched_date(self, asset_type: str = "stock") -> date | None:
        """Return the newest partition available to matrix-native consumers.

        Stocks and ETFs share the daily enriched partitions, so every asset type reads the same root.
        """
        root = self.store.data_dir / "kline_daily_enriched"
        latest: date | None = None
        if not root.exists():
            return None
        # A date directory without part.parquet was created but never published.
        for part in root.glob("date=*/part.parquet"):
            try:
                value = date.fromisoformat(part.parent.name.removeprefix("date="))
            except ValueError:
                continue
            if latest is None or value > latest:
                latest = value
        return latest

    def get_matrix_data_generation(self, asset_type: str = "stock") -> str:
        """Return the stable generation for managed enriched readers."""
        return get_enriched_generation(self.store.data_dir, asset_type)

    def _latest_enriched_date_duckdb(self) -> date | None:
        try:
            res = self.execute_one(
                "SELECT max(date) FROM kline_enriched",
            )
            if res and res[0]:
                d = res[0]
                return d if isinstance(d, date) else date.fromisoformat(str(d))
        except Exception:  # noqa: BLE001
            return None
        return None

    # ================================================================
    # 写入 (Pipeline / Sync)
    # ================================================================

    def rebuild_views(self) -> None:
        """Re-register every _PARQUET_VIEWS view; an emptied directory (after a clear) remounts safely."""
        for name in _PARQUET_VIEWS:
            self.refresh_view(name)

    def refresh_view(self, name: str) -> None:
        """Re-register one _PARQUET_VIEWS view so DuckDB sees partitions written since it was created."""
        try:
            with self._lock:
                self.db.execute(_view_sql(self.store.data_dir, name, _PARQUET_VIEWS[name]))
        except Exception as e:  # noqa: BLE001
            logger.warning("rebuild view %s failed: %s", name, e)

    @staticmethod
    def _atomic_write_parquet(df: pl.DataFrame, out: Path) -> None:
        """先写临时文件再原子替换, 避免进程中断留下损坏的 parquet。

        直接 write_parquet(out) 在进程被 kill (dev.sh 清端口用 kill -9)
        或断电时会留下半截文件, 之后 scan_parquet glob 整条链路报错。
        临时文件后缀 .tmp 不匹配 *.parquet glob, 不会被扫描误读。
        """
        tmp = out.with_name(out.name + ".tmp")
        df.write_parquet(tmp)
        tmp.replace(out)  # 同目录 rename, POSIX/NTFS 均为原子操作

    def _with_instrument_metadata(self, asset_type: str, df: pl.DataFrame) -> pl.DataFrame:
        """补齐实时内存缓存所需的维表字段；这些字段不会写入 enriched 分区。"""
        if asset_type != "stock" or df.is_empty():
            return df
        instruments = self.get_instruments_asset(asset_type)
        if instruments.is_empty() or "symbol" not in instruments.columns:
            return df
        metadata_cols = [
            c for c in ("name", "total_shares", "float_shares")
            if c in instruments.columns and c not in df.columns
        ]
        if not metadata_cols:
            return df
        metadata = instruments.select(["symbol", *metadata_cols]).unique(
            subset=["symbol"], keep="last",
        )
        return df.join(metadata, on="symbol", how="left")

    def merge_live_enriched_asset(
        self, asset_type: str, df: pl.DataFrame, *, force: bool = False
    ) -> None:
        """按 symbol 合并当天 enriched 分区和内存缓存。用于少量自选实时。

        When the exchange has not published *dt* yet the merge stays in memory:
        the live readers still need today, but a partition for that day would
        make a watchlist-sized slice look like a full trading session to anything
        scanning the market. Taiwan stock rows always stay in memory unless *force*.
        """
        if df.is_empty() or "date" not in df.columns:
            return
        dt = df["date"][0]
        write_to_disk = self._live_write_allowed(dt, force=force)
        if asset_type == "stock":
            table = "kline_daily_enriched"
            existing_cache = self._enriched_cache if self._enriched_cache_date == dt else pl.DataFrame()
        elif asset_type == "index":
            table = "kline_index_enriched"
            existing_cache = self._index_enriched_cache if self._index_enriched_cache_date == dt else pl.DataFrame()
        else:
            return

        cache_df = self._with_instrument_metadata(asset_type, df)
        merged_cache = cache_df
        if existing_cache is not None and not existing_cache.is_empty():
            merged_cache = pl.concat([existing_cache, cache_df], how="diagonal_relaxed").unique(
                subset=["symbol", "date"], keep="last"
            )
        merged_cache = merged_cache.sort(["symbol"])

        from app.indicators.pipeline import ENRICHED_STORAGE_COLS
        storage_cols = [c for c in ENRICHED_STORAGE_COLS if c in df.columns]
        df_storage = df.select(storage_cols).sort(["symbol"])
        if asset_type == "stock" and not force:
            from app.volume_units import TAIWAN_SUFFIXES

            # The evening publish owns these partitions; a live row would replace its full row.
            df_storage = df_storage.filter(
                ~pl.any_horizontal([pl.col("symbol").str.ends_with(s) for s in TAIWAN_SUFFIXES])
            )
            write_to_disk = write_to_disk and not df_storage.is_empty()
        base = self.store.data_dir / table
        ds = dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
        out = base / f"date={ds}" / "part.parquet"
        if write_to_disk:
            out.parent.mkdir(parents=True, exist_ok=True)
            publication = (
                EnrichedPublication(self.store.data_dir, asset_type, recover=True)
                if asset_type == "stock"
                else None
            )
            with self._write_lock:
                existing = pl.DataFrame()
                if out.exists():
                    existing = pl.read_parquet(out)
                    df_storage = pl.concat([existing, df_storage], how="diagonal_relaxed").unique(
                        subset=["symbol", "date"], keep="last"
                    )
                df_storage = df_storage.sort(["symbol"])
                if existing.is_empty() or not existing.equals(df_storage):
                    if publication is None:
                        self._atomic_write_parquet(df_storage, out)
                    else:
                        publication.write_parquet(df_storage, out)
                if publication is not None:
                    publication.commit()

        if asset_type == "stock":
            self._enriched_cache = merged_cache
            self._enriched_cache_date = dt
        elif asset_type == "index":
            self._index_enriched_cache = merged_cache
            self._index_enriched_cache_date = dt

    def flush_live_enriched_asset(
        self, asset_type: str, df: pl.DataFrame, *, force: bool = False
    ) -> None:
        """合并当天指定资产 enriched 分区与内存缓存。

        Formerly a whole-partition overwrite that also replaced the in-memory
        cache outright. Under Taiwan the live feed carries only the watchlist, so
        replacing meant the cache held those few symbols and the partition lost
        every other one; merging keeps both intact.
        """
        self.merge_live_enriched_asset(asset_type, df, force=force)
