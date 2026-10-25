"""Daily and minute K fetch and persistence.

Bars come only from the Taiwan registry provider or a custom data source plugin; without the
matching dataset the call returns an empty result or raises.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta

import polars as pl

from app.data_providers.base import AssetType
from app.market_time import CN_TZ, cn_now
from app.services import preferences
from app.repository import KlineRepository


_MINUTE_WINDOW_MARGIN = timedelta(minutes=5)


def intraday_window(trade_date: date) -> tuple[datetime, datetime]:
    """Minute-bar pull window: the Taiwan regular session plus a small margin.

    The margin covers providers that stamp a bar at its close rather than its open,
    so the 09:00 and 13:30 bars are never dropped at the edges.
    The window must be timezone-aware: a naive datetime is read by .timestamp() in
    the server local timezone, which shifts the window by 8 hours on a UTC container
    and makes every intraday pull come back empty.
    """
    from app.taiwan.realtime.calendar import REGULAR_OPEN_END, REGULAR_OPEN_START

    start = datetime.combine(trade_date, REGULAR_OPEN_START, tzinfo=CN_TZ) - _MINUTE_WINDOW_MARGIN
    end = datetime.combine(trade_date, REGULAR_OPEN_END, tzinfo=CN_TZ) + _MINUTE_WINDOW_MARGIN
    return start, end

logger = logging.getLogger(__name__)


def _atomic_write_parquet(df: pl.DataFrame, out) -> None:
    """Write to a temporary file and atomically replace *out*, so an interrupted write leaves no torn parquet.

    Same contract as repository._atomic_write_parquet; the .tmp suffix never matches a *.parquet glob.
    """
    tmp = out.with_name(out.name + ".tmp")
    df.write_parquet(tmp)
    tmp.replace(out)  # 同目录 rename, POSIX/NTFS 均为原子操作


def _window_for_count(
    count: int | None, start_time: datetime | None, end_time: datetime | None
) -> tuple[datetime | None, datetime | None]:
    """Fill a missing start from *count* trading days; date-range providers ignore a bare count."""
    if start_time is not None or not count:
        return start_time, end_time
    end_time = end_time or datetime.now()
    # About 245 Taiwan sessions a year (1.49 calendar days each); 1.6 plus 60 days covers holiday runs.
    calendar_days = count * 8 // 5 + 60
    return end_time - timedelta(days=calendar_days), end_time


def sync_daily_batch(symbols: list[str],
                     count: int | None = None,
                     start_time: datetime | None = None,
                     end_time: datetime | None = None,
                     on_chunk_done: Callable[[int, int], None] | None = None) -> pl.DataFrame:
    """Daily K for *symbols* over start_time..end_time; a bare *count* becomes a date window.

    Raises RuntimeError when the configured daily source has no daily dataset.
    """
    provider_name = preferences.get_daily_data_provider()
    if provider_name == "taiwan":
        from app.data_providers.registry import get_provider
        provider = get_provider(provider_name)
        start_time, end_time = _window_for_count(count, start_time, end_time)
        return provider.get_daily(symbols, start_time=start_time, end_time=end_time, on_chunk_done=on_chunk_done)

    from app.data_providers import custom as custom_sources
    if custom_sources.provider_has_dataset(provider_name, "daily"):
        provider = custom_sources.get_provider(provider_name)
        start_time, end_time = _window_for_count(count, start_time, end_time)
        return provider.get_daily(symbols, start_time=start_time, end_time=end_time, on_chunk_done=on_chunk_done)

    raise RuntimeError(f"daily data source {provider_name!r} has no daily dataset")


# ===== 分钟 K 同步 =====

def _write_minute_partition(df: pl.DataFrame, minute_dir) -> int:
    """按 _trade_date 分区落盘分钟 K (读旧→concat→unique→原子写)。返回写入行数。

    抽自原 sync_and_persist_minute 末尾的循环, 供流式落盘 (每段一次) 与一次性迁移共用。
    """
    if df.is_empty():
        return 0
    df = df.with_columns(pl.col("datetime").dt.date().alias("_trade_date"))
    written = 0
    for day_df in df.partition_by("_trade_date"):
        trade_date = day_df["_trade_date"][0]
        out = minute_dir / f"date={trade_date}" / "part.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            existing = pl.read_parquet(out)
            if "datetime" in existing.columns:
                existing = existing.filter(pl.col("datetime").is_not_null())
            day_df = pl.concat([existing, day_df.drop("_trade_date")], how="diagonal_relaxed").unique(
                subset=["symbol", "datetime"], keep="last",
            )
        else:
            day_df = day_df.drop("_trade_date")
        day_df = day_df.sort("symbol", "datetime")
        _atomic_write_parquet(day_df, out)
        written += day_df.height
    return written


def _resolve_minute_provider(
    provider_name: str,
) -> tuple[object | None, bool, str | None]:
    """Resolve the custom minute provider inside one exception boundary.

    Returns (provider, no_custom_source, error_msg): (None, True, None) when no source has a
    minute dataset, (None, True, str(e)) when resolution raised, (provider, False, None) otherwise.
    Callers run provider.get_minute() in their own try block.
    """
    from app.data_providers import custom as custom_sources
    try:
        if not custom_sources.provider_has_dataset(provider_name, "minute"):
            return (None, True, None)
        provider = custom_sources.get_provider(provider_name)
        return (provider, False, None)
    except Exception as e:  # noqa: BLE001
        return (None, True, str(e))


def _try_custom_minute(
    symbols: list[str],
    start_time: datetime | None,
    end_time: datetime | None,
    asset_type: AssetType,
    freq: str = "1m",
    on_chunk_done: Callable[[int, int, str], None] | None = None,
) -> tuple[pl.DataFrame | None, bool]:
    """Fetch from the custom minute source; returns (df, no_custom_result).

    (None, True) when no source resolves or the call fails; (df, False) on success, df possibly empty.
    The provider reports progress as (cur, total); it is forwarded as (cur, total, "custom").
    """
    provider_name = preferences.get_minute_data_provider()
    provider, fallback, err = _resolve_minute_provider(provider_name)
    if fallback:
        if err is not None:
            logger.warning("custom minute provider %s resolution failed: %s", provider_name, err)
        return (None, True)

    # 包装 on_chunk_done: provider 调 2 参 → 补 seg_label="custom" → 转发上层 3 参
    wrapped_cb: Callable[[int, int], None] | None = None
    if on_chunk_done is not None:
        def _wrapped_cb(cur: int, total: int) -> None:
            on_chunk_done(cur, total, "custom")
        wrapped_cb = _wrapped_cb

    try:
        df = provider.get_minute(
            symbols, start_time=start_time, end_time=end_time,
            asset_type=asset_type, freq=freq, on_chunk_done=wrapped_cb,
        )
        return (df, False)
    except Exception as e:  # noqa: BLE001
        logger.warning("custom minute provider %s call failed: %s", provider_name, e)
        return (None, True)


def sync_minute_batch(
    symbols: list[str],
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    on_chunk_done: Callable[[int, int, str], None] | None = None,
    on_segment: Callable[[pl.DataFrame], None] | None = None,
    asset_type: AssetType = "stock",
) -> pl.DataFrame:
    """Minute K for *symbols* from the custom minute source; empty without one.

    With *on_segment* the bars go to it and an empty frame is returned.
    """
    df, fallback = _try_custom_minute(
        symbols, start_time=start_time, end_time=end_time,
        asset_type=asset_type, freq="1m", on_chunk_done=on_chunk_done,
    )
    if fallback or df is None:
        return pl.DataFrame()
    if on_segment and not df.is_empty():
        on_segment(df)
        return pl.DataFrame()
    return df


_NO_MINUTE_SOURCE_REASON = "台股沒有分鐘資料源, 分時訊號需要設定分鐘資料插件"


def intraday_monitor_support() -> dict[str, object]:
    """返回分时信号监控可用的数据能力和单轮标的上限。

    Only a resolvable custom minute plugin supplies intraday bars.
    """
    provider_name = preferences.get_minute_data_provider()
    _, fallback, error = _resolve_minute_provider(provider_name)
    if not fallback:
        return {
            "available": True, "source": "custom_minute", "max_symbols": 100,
            "reason": "使用已配置的分钟数据插件",
        }
    if error is not None:
        logger.warning("minute provider resolution failed while checking monitor support: %s", error)
    return {
        "available": False, "source": None, "max_symbols": 0,
        "reason": _NO_MINUTE_SOURCE_REASON,
    }


def fetch_intraday_monitor_batch(
    symbols: list[str], *, now: datetime | None = None,
) -> pl.DataFrame:
    """获取分时信号所需的当日分钟数据，不落盘。"""
    if not symbols:
        return pl.DataFrame()
    support = intraday_monitor_support()
    if not support["available"] or len(symbols) > int(support["max_symbols"]):
        return pl.DataFrame()

    now = now or cn_now()
    start_time, _ = intraday_window(now.date())
    return sync_minute_batch(symbols, start_time=start_time, end_time=now)


def fetch_minute_single(
    symbol: str,
    trade_date: date,
    asset_type: AssetType = "stock",
) -> pl.DataFrame:
    """One symbol's minute K for *trade_date*, not stored; empty without a custom minute source."""
    start_time, end_time = intraday_window(trade_date)
    df, fallback = _try_custom_minute(
        [symbol], start_time=start_time, end_time=end_time,
        asset_type=asset_type, freq="1m",
    )
    if fallback or df is None:
        return pl.DataFrame()
    return df


def _latest_minute_datetime(repo: KlineRepository) -> datetime | None:
    """本地分钟 K 数据的最新时间。"""
    try:
        res = repo.execute_one("SELECT max(datetime) FROM kline_minute")
        if res and res[0]:
            d = res[0]
            if isinstance(d, datetime):
                return d
            return datetime.fromisoformat(str(d))
    except Exception:  # noqa: BLE001
        pass
    return None


def _cleanup_null_datetime_minute(repo: KlineRepository) -> None:
    """检测并清除 datetime 全为 null 的旧版分钟 K 数据(迁移用)。"""
    minute_dir = repo.store.data_dir / "kline_minute"
    if not minute_dir.exists():
        return
    try:
        row = repo.execute_one(
            "SELECT count(*) AS total, count(datetime) AS non_null FROM kline_minute"
        )
        if row and row[0] > 0 and (row[1] is None or row[1] == 0):
            # 全部 datetime 为 null — 清除所有分钟 K parquet
            n = 0
            for f in minute_dir.rglob("*.parquet"):
                f.unlink()
                n += 1
            logger.info("cleaned %d corrupted minute-K parquet files (null datetime)", n)
    except Exception as e:  # noqa: BLE001
        logger.debug("minute cleanup check failed: %s", e)


def _migrate_symbol_to_date_partition(repo: KlineRepository) -> None:
    """将旧版 symbol= 分区迁移为 date= 分区。迁移完成后删除旧目录。"""
    minute_dir = repo.store.data_dir / "kline_minute"
    if not minute_dir.exists():
        return

    old_dirs = [d for d in minute_dir.iterdir() if d.is_dir() and d.name.startswith("symbol=")]
    if not old_dirs:
        return

    logger.info("migrating %d symbol-partitioned minute-K dirs to date partition…", len(old_dirs))

    all_frames: list[pl.DataFrame] = []
    for sym_dir in old_dirs:
        for pq in sym_dir.glob("*.parquet"):
            try:
                df = pl.read_parquet(pq)
                if "datetime" in df.columns:
                    df = df.filter(pl.col("datetime").is_not_null())
                if not df.is_empty():
                    all_frames.append(df)
            except Exception:  # noqa: BLE001
                pass

    if not all_frames:
        # 数据全部不可用，直接删旧目录
        for d in old_dirs:
            d.mkdir(parents=True, exist_ok=True)
            for f in d.rglob("*"):
                if f.is_file():
                    f.unlink()
            d.rmdir()
        return

    combined = pl.concat(all_frames, how="diagonal_relaxed")
    combined = combined.unique(subset=["symbol", "datetime"], keep="last")

    # 按日期写新分区
    combined = combined.with_columns(pl.col("datetime").dt.date().alias("_trade_date"))
    for day_df in combined.partition_by("_trade_date"):
        trade_date = day_df["_trade_date"][0]
        out = minute_dir / f"date={trade_date}" / "part.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        day_df = day_df.drop("_trade_date").sort("symbol", "datetime")
        _atomic_write_parquet(day_df, out)

    # 删旧目录
    for d in old_dirs:
        for f in d.rglob("*"):
            if f.is_file():
                f.unlink()
        # 移除空目录
        try:
            d.rmdir()
        except OSError:
            pass

    logger.info("minute-K migration done: %d rows migrated", combined.height)


def sync_and_persist_minute(
    symbols: list[str],
    repo: KlineRepository,
    days: int = 5,
    on_chunk_done: Callable[[int, int, str], None] | None = None,
    force_full_days: bool = False,
) -> int:
    """Store minute K from the custom minute source; returns rows written, 0 without a source.

    force_full_days=True refetches the last *days* trading days instead of filling from the latest bar.
    """
    minute_provider = preferences.get_minute_data_provider()
    _, fallback, resolve_err = _resolve_minute_provider(minute_provider)
    if resolve_err is not None:
        logger.warning("custom minute provider %s resolution failed at sync_and_persist_minute: %s",
                       minute_provider, resolve_err)
    if not symbols or fallback:
        return 0

    # Legacy minute files whose datetime column is entirely null are unusable and are removed.
    _cleanup_null_datetime_minute(repo)

    # 迁移:旧版按 symbol= 分区转为 date= 分区
    _migrate_symbol_to_date_partition(repo)

    now = datetime.now()

    # 默认增量模式: 首次拉取回溯 N 天, 已有数据则从最新时间增量补到今天
    # force_full_days=True: 强制回溯 days 自然日 (个股补齐历史, 不增量)
    last_dt = _latest_minute_datetime(repo)
    if force_full_days:
        # 按交易日换算自然日 (7/5 系数), 确保覆盖足够交易日
        calendar_days = int(days * 7 / 5) + 5
        start_time = now - timedelta(days=calendar_days)
    elif last_dt:
        start_time = last_dt
    else:
        start_time = now - timedelta(days=days)
    end_time = now

    minute_dir = repo.store.data_dir / "kline_minute"
    written_box = [0]  # list 闭包, 绕过 Python 闭包外层赋值

    def _persist(seg_df: pl.DataFrame) -> None:
        # 单股自动补齐可能与另一个补齐请求同时写同一日期分区。Windows 不允许
        # 替换仍被另一写入占用的临时文件,因此读-改-写必须复用仓库写锁。
        with repo._write_lock:
            written_box[0] += _write_minute_partition(seg_df, minute_dir)

    sync_minute_batch(
        symbols, start_time=start_time, end_time=end_time,
        on_chunk_done=on_chunk_done,
        on_segment=_persist,
        asset_type="stock",
    )

    if written_box[0] == 0:
        return 0
    written = written_box[0]

    # 刷新视图
    try:
        d = repo.store.data_dir.as_posix()
        repo.db.execute(
            f"""CREATE OR REPLACE VIEW kline_minute AS
                SELECT * FROM read_parquet('{d}/kline_minute/**/*.parquet', union_by_name=true)"""
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("refresh kline_minute view failed: %s", e)

    logger.info("minute K synced: %d rows (%d symbols)", written, len(symbols))
    return written
