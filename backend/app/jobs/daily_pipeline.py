"""台股调度管道 — 周期性能力重探 + 台股盘后增量更新。

A 股日K/enriched 盘后管道(run_now)、盘前维表同步(pre_market_instruments)
与其配套调度、job 追踪、市场环境/主线计算触发已随 A 股功能整体下线。
_refresh_single_view 仍被 api/kline.py 的个股分时"获取数据"按钮真实使用,
故保留。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.tickflow.repository import KlineRepository, enriched_dirname

logger = logging.getLogger(__name__)


def _refresh_single_view(repo: KlineRepository, name: str) -> None:
    """刷新单个 DuckDB 视图。"""
    d = repo.store.data_dir.as_posix()
    paths = {
        "kline_daily": f"{d}/kline_daily/**/*.parquet",
        "kline_enriched": f"{d}/kline_daily_enriched/**/*.parquet",
        "kline_index_daily": f"{d}/kline_index_daily/**/*.parquet",
        "kline_index_enriched": f"{d}/kline_index_enriched/**/*.parquet",
        "kline_etf_daily": f"{d}/kline_etf_daily/**/*.parquet",
        "kline_etf_enriched": f"{d}/kline_etf_enriched/**/*.parquet",
        "kline_etf_minute": f"{d}/kline_etf_minute/**/*.parquet",
        "kline_minute": f"{d}/kline_minute/**/*.parquet",
        "adj_factor": f"{d}/adj_factor/**/*.parquet",
        "adj_factor_etf": f"{d}/adj_factor_etf/**/*.parquet",
        "instruments": f"{d}/instruments/**/*.parquet",
        "instruments_index": f"{d}/instruments_index/**/*.parquet",
        "instruments_etf": f"{d}/instruments_etf/**/*.parquet",
    }
    path = paths.get(name)
    if not path:
        return
    try:
        repo.db.execute(
            f"CREATE OR REPLACE VIEW {name} AS "
            f"SELECT * FROM read_parquet('{path}', union_by_name=true)"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("refresh view %s failed: %s", name, e)


def refresh_enriched_cache(label: str) -> None:
    """Reload the in-memory enriched cache from what the update just published.

    run_update publishes indicators to disk but never refreshes this cache, so
    it can still hold the intraday realtime write — a handful of watchlist
    symbols stamped with today's date. Screening reads that cache whenever its
    date matches the basis date and does not check how many rows are in it, so
    without this reload a market-wide scan can run against those few symbols.
    """
    app_state = _get_app_state()
    repo = getattr(app_state, "repo", None) if app_state is not None else None
    if repo is None:
        logger.warning("enriched cache refresh (%s) skipped: repo unavailable", label)
        return
    try:
        repo.rebuild_views()
        repo.refresh_cache()
        logger.info(
            "enriched cache refreshed (%s): as_of=%s", label, repo.enriched_latest_date()
        )
    except Exception as e:
        logger.exception("enriched cache refresh (%s) failed: %s", label, e)

def refresh_strategy_cache(label: str) -> None:
    """Recompute the dashboard strategy cache for the latest day, then record its pick snapshot.

    A record failure is only logged and leaves the written cache intact.
    """
    app_state = _get_app_state()
    if app_state is None:
        logger.warning("strategy cache refresh (%s) skipped: app state unavailable", label)
        return
    engine = getattr(app_state, "strategy_engine", None)
    repo = getattr(app_state, "repo", None)
    if engine is None or repo is None:
        logger.warning("strategy cache refresh (%s) skipped: engine/repo unavailable", label)
        return
    try:
        from app.api.screener import compute_and_cache_all
        from app.services.screener import ScreenerService
        from app.services.strategy_pick_history import RunReport

        signal_date = ScreenerService(repo).latest_date()
        if signal_date is None:
            logger.warning("strategy cache refresh (%s) skipped: no basis date", label)
            return
        ScreenerService.clear_history_cache()
        data_dir = repo.store.data_dir
        input_mtime = _enriched_partition_mtime(data_dir, signal_date)
        report = RunReport()
        as_of, results = compute_and_cache_all(
            repo, engine, as_of=signal_date, run_report=report
        )
        logger.info(
            "strategy cache refreshed (%s): as_of=%s, strategies=%d",
            label, as_of, len(results),
        )
    except Exception as e:
        logger.exception("strategy cache refresh (%s) failed: %s", label, e)
        return
    if as_of != signal_date:
        logger.warning(
            "strategy picks not recorded (%s): computed as_of %s != %s", label, as_of, signal_date
        )
        return
    try:
        from app.services import strategy_pick_history

        strategy_pick_history.record(data_dir, signal_date, results, report, label, input_mtime)
    except Exception:
        logger.exception("strategy picks record (%s) failed for %s", label, signal_date)


BENCHMARK_REFRESH_DAYS = 60


def refresh_benchmark_index(label: str) -> date | None:
    """Refetch recent TAIEX bars, bypassing the current-month cache period.

    Returns the latest stored index date in the window afterwards. Never raises.
    """
    try:
        from app.taiwan.benchmarks import TAIEX
        from app.taiwan.index_daily import get_index_daily_service
        from app.taiwan.realtime.calendar import taipei_today

        today = taipei_today()
        frame = get_index_daily_service().read_range(
            TAIEX.symbol,
            today - timedelta(days=BENCHMARK_REFRESH_DAYS),
            today,
            refresh_current_month=True,
        )
        latest = frame["date"].max() if not frame.is_empty() else None
        logger.info("benchmark index refreshed (%s): latest=%s", label, latest)
        return latest
    except Exception as e:
        logger.exception("benchmark index refresh (%s) failed: %s", label, e)
        return None


def _enriched_partition_mtime(data_dir: Path, day: date) -> float | None:
    """Never raises: a missing or unreadable mtime only disables the stale-input check."""
    try:
        path = Path(data_dir) / enriched_dirname("stock") / f"date={day.isoformat()}" / "part.parquet"
        return path.stat().st_mtime
    except FileNotFoundError:
        return None
    except Exception:
        logger.warning("enriched partition mtime unreadable for %s", day, exc_info=True)
        return None


def start_scheduler() -> AsyncIOScheduler:
    """启动调度器 — 周期性能力重探 + 台股盘后增量更新。"""
    scheduler = AsyncIOScheduler(timezone="Asia/Taipei")

    # 周期性能力重探: 付费 Key 中途过期/续费无需重启即可被发现。
    # 只热更新 app.state.capabilities(API 端点均读它);
    # 档位变化记 WARNING, 让「Key 失效」在日志/前端可见, 不再静默按旧档位打 403 端点。
    def _reprobe_capabilities():
        from app.tickflow.policy import detect_capabilities, tier_label
        app_state = _get_app_state()
        if app_state is None:
            return
        try:
            old = getattr(app_state, "capabilities", None)
            old_n = len(old.all()) if old else -1
            new_capset = detect_capabilities(force=True)
            app_state.capabilities = new_capset
            new_n = len(new_capset.all())
            if old_n != new_n:
                logger.warning(
                    "能力集变化: %d → %d capabilities (档位=%s)。Key 过期/续费或端点波动, "
                    "已热更新 app.state.capabilities。", old_n, new_n, tier_label(),
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("周期能力重探失败(保留现有能力集): %s", e)

    scheduler.add_job(
        _reprobe_capabilities,
        trigger=IntervalTrigger(minutes=60),
        id="reprobe_capabilities",
        misfire_grace_time=600,
        replace_existing=True,
    )

    # 台股盤後增量更新, 分兩班。官方全市場快照端點: Daily (TWSE 1 + TPEx 1) +
    # Inst (2) + Margin (2) = ~6 次 HTTP 請求 / 日。
    # 拆班原因: 交易所公布資券餘額的時間晚於三大法人, 單一 16:30 班次抓不到當日
    # 資券, 於是資券永遠落後一個交易日, 資料狀態也就天天顯示「尚未完全更新」。
    def _scheduled_taiwan_update(
        label: str,
        *,
        refresh_daily: bool,
        refresh_institutional: bool,
        refresh_margin: bool,
    ) -> None:
        refresh_benchmark_index(label)
        try:
            from app.taiwan.daily_update import TaiwanDailyUpdateService
            svc = TaiwanDailyUpdateService()
            result = svc.run_update(
                refresh_daily=refresh_daily,
                refresh_institutional=refresh_institutional,
                refresh_margin=refresh_margin,
            )
            logger.info(
                "Scheduled Taiwan update (%s) finished: overall=%s, daily=%s, inst=%s, margin=%s",
                label, result.overall_status, result.daily.status,
                result.institutional.status, result.margin.status,
            )
            if result.daily.note:
                logger.info("Scheduled Taiwan update (%s) daily note: %s", label, result.daily.note)
            if result.institutional.note:
                logger.info("Scheduled Taiwan update (%s) institutional note: %s", label, result.institutional.note)
        except Exception as e:
            logger.exception("Scheduled Taiwan update (%s) job failed: %s", label, e)
            return
        # run_update reports a soft failure by status rather than by raising, so an
        # exception handler alone would recompute picks off stale prices.
        # Gate on the daily bars specifically, not overall_status: a failed daily fetch
        # alongside a successful institutional one aggregates to "partial"
        # (daily_update.py: any failed + any success -> partial), which would let
        # exactly the stale-price case through. A later dataset lagging is fine here
        # because the strategies read daily bars.
        if result.daily.status == "failed":
            logger.warning(
                "strategy cache refresh (%s) skipped: daily bars failed to update", label
            )
            return
        refresh_enriched_cache(label)
        refresh_strategy_cache(label)

    # 16:30 — 日線與三大法人 (兩者在此時已公布)
    scheduler.add_job(
        _scheduled_taiwan_update,
        trigger=CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone="Asia/Taipei"),
        id="taiwan_daily_update",
        kwargs={
            "label": "daily+institutional",
            "refresh_daily": True,
            "refresh_institutional": True,
            "refresh_margin": False,
        },
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )

    # 21:40 - margin, which the exchange publishes later. Daily bars and institutional flows run
    # again so a failed or early 16:30 run gets a second chance: provisional days are refetched
    # here, after the daily final cutoff, which finalizes them; other stored days are skipped.
    scheduler.add_job(
        _scheduled_taiwan_update,
        trigger=CronTrigger(day_of_week="mon-fri", hour=21, minute=40, timezone="Asia/Taipei"),
        id="taiwan_margin_update",
        kwargs={
            "label": "margin",
            "refresh_daily": True,
            "refresh_institutional": True,
            "refresh_margin": True,
        },
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )

    def _refresh_holiday_schedule() -> None:
        from app.taiwan.realtime import holidays

        holidays.refresh_upcoming_years()

    # The store fetches each year at most once per day, so a short interval only bounds how
    # long a fresh install or a new year stays unverified.
    scheduler.add_job(
        _refresh_holiday_schedule,
        trigger=IntervalTrigger(hours=3),
        id="taiwan_holiday_refresh",
        next_run_time=datetime.now().astimezone() + timedelta(seconds=30),
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "scheduler started; reprobe@60min, taiwan@16:30 (daily+inst) & 21:40 (margin) mon-fri, "
        "holiday schedule@3h"
    )
    return scheduler


# app_state 延迟引用(start_scheduler 在 lifespan 早期调用, app.state 可能还没就绪)
_app_state_ref = None


def set_app_state(app_state) -> None:
    """lifespan 注册 app.state 引用, 供 scheduled job (如周期能力重探) 访问单例。"""
    global _app_state_ref
    _app_state_ref = app_state


def _get_app_state():
    return _app_state_ref
