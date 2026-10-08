"""全局实时行情服务。

集中管理全市场行情拉取 + enriched 缓存，供盘中选股、自选股等所有模块复用。

架构:
  - 后台线程轮询 TickFlow get_by_universes(["CN_Equity_A", "CN_Index"])
  - 拉取行情 → 写 kline_daily (不复权) + 增量计算 enriched → 写盘 + 更新缓存
  - _enriched_cache 是唯一的盘中数据源 (OHLCV + 全套技术指标)
  - _live_agg_cache 是递推状态 (只加载一次, 盘中不变)

数据流 (每轮 ~15s):
  1. API 拉取 → raw_records (临时变量)
  2. raw_records → 写 kline_daily (不复权原始价格)
  3. raw_records → 更新 _enriched_cache 的 OHLCV
  4. 增量计算 enriched 指标 (~50ms)
  5. 写 kline_daily_enriched + 替换 _enriched_cache
  6. 通知 SSE

生命周期:
  - 服务启动时读取 preferences，若 enabled 则自动启动线程
  - 运行中可通过 API 切换开关
  - 关闭时停止线程
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from datetime import time as dt_time

import polars as pl

from app.market_time import cn_now, cn_today
from app.parquet import scan_daily_parquet
from app.strategy.intraday_signals import IntradaySignalEvaluator
from app.volume_units import TAIWAN_SUFFIXES

logger = logging.getLogger(__name__)

# Record statuses that may date today's bar: continuous trading and the closing-auction result.
_LIVE_RECORD_STATUSES = frozenset({"realtime", "close_final"})
# A symbol that traded today but had no closing-auction trade (e.g. halted); see TaiwanHybridProvider.
_SETTLED_NO_CLOSE = "settled_no_close"

# No today-dated quote by this time on a day the calendar has not confirmed suggests a closure.
_SUSPECT_CLOSURE_AFTER = dt_time(9, 5)
_SUSPECT_CLOSURE_PROBE_INTERVAL = timedelta(minutes=5)

# 外部推播专用线程池 —— 与行情轮询线程隔离。
# 若在 _poll_loop 上同步投递, API 慢/宕机会逐条累加,拖垮整条实时行情+告警轮询。
# 这里 fire-and-forget,
# 失败由 webhook_adapter 记 WARNING(可见), 但绝不阻塞热路径。
_WEBHOOK_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notification-push")


def _is_taiwan_symbol() -> pl.Expr:
    return pl.any_horizontal([pl.col("symbol").str.ends_with(suffix) for suffix in TAIWAN_SUFFIXES])


class QuoteSubscriber:
    """一个 SSE 连接对应一个订阅者: 独立事件 + 独立队列。

    此前四个通道共用服务级 Event + pending 列表, pop 是「取走」语义:
    多客户端 (多标签页/多设备) 时告警只会被先醒来的连接消费, 其余永远
    收不到; 共享 Event 的 clear/wait 也存在互相吞信号的竞态。
    改为每连接独立订阅者后, 事件对所有客户端广播。
    """

    def __init__(self, max_alerts: int = 1000, max_reviews: int = 200) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._max_alerts = max_alerts
        self._max_reviews = max_reviews
        self._quote_updated = False
        self._strategy_results_updated = False
        self._depth_updated = False
        self._alerts: list[dict] = []
        self._reviews: list[str] = []

    # ── 消费侧 (SSE generator 线程) ──────────────────────
    def wait(self, timeout: float = 5.0) -> bool:
        """阻塞等待任一通道有新信号。"""
        return self._event.wait(timeout=timeout)

    def pop(self) -> dict:
        """原子取走全部待推送内容并复位事件。"""
        with self._lock:
            out = {
                "quote_updated": self._quote_updated,
                "strategy_results_updated": self._strategy_results_updated,
                "depth_updated": self._depth_updated,
                "alerts": self._alerts,
                "reviews": self._reviews,
            }
            self._quote_updated = False
            self._strategy_results_updated = False
            self._depth_updated = False
            self._alerts = []
            self._reviews = []
            self._event.clear()
            return out

    # ── 生产侧 (行情轮询 / depth / 复盘线程) ─────────────
    def push_alerts(self, alerts: list[dict]) -> None:
        with self._lock:
            self._alerts.extend(alerts)
            if len(self._alerts) > self._max_alerts:  # 背压: 丢弃最旧
                self._alerts = self._alerts[-self._max_alerts:]
            self._event.set()

    def push_review(self, event_json: str) -> None:
        with self._lock:
            self._reviews.append(event_json)
            if len(self._reviews) > self._max_reviews:
                self._reviews = self._reviews[-self._max_reviews:]
            self._event.set()

    def clear_alerts(self) -> None:
        with self._lock:
            self._alerts = []
            if (
                not self._quote_updated
                and not self._strategy_results_updated
                and not self._depth_updated
                and not self._reviews
            ):
                self._event.clear()

    def notify_quote(self) -> None:
        with self._lock:
            self._quote_updated = True
            self._event.set()

    def notify_strategy_results(self) -> None:
        with self._lock:
            self._strategy_results_updated = True
            self._event.set()

    def notify_depth(self) -> None:
        with self._lock:
            self._depth_updated = True
            self._event.set()


# 落盘节流间隔: last_fetch_ms 仅在进程重启后用于显示"最后获取时间"(运行中读内存值),
# 每 30s 持久化一次足够, 避免 expert 档每秒一轮的全量 preferences 重写磁盘。
_LAST_FETCH_WRITE_INTERVAL_MS = 30_000.0
_last_fetch_written_at_ms: float = 0.0


def _persist_last_fetch(fetched_at_ms: float) -> None:
    """把"最后获取"时间戳持久化到 preferences, 使进程重启后仍可显示。

    放在锁外调用 (IO); 失败不影响主流程 (内存值已更新, 下次 fetch 再写)。
    距上次成功落盘不足 30s 时跳过 (节流只影响落盘频率, 内存值不受影响)。
    """
    global _last_fetch_written_at_ms
    if (fetched_at_ms - _last_fetch_written_at_ms) < _LAST_FETCH_WRITE_INTERVAL_MS:
        return
    try:
        from app.services import preferences
        preferences.save({"last_fetch_ms": round(fetched_at_ms, 0)})
        _last_fetch_written_at_ms = fetched_at_ms
    except Exception as e:  # noqa: BLE001
        logger.debug("last_fetch_ms 持久化失败 (不影响行情): %s", e)


def _positive(value: object) -> bool:
    try:
        return float(value) > 0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _monitor_name_map(repo) -> dict[str, str]:
    """监控回填用的 symbol → name 映射 (股票 + ETF + 指数, 股票优先)。

    走 repo.get_name_map() 的进程内 memo (三份 instruments 维表刷新时失效),
    避免每轮监控对 ~7000 行维表 iter_rows 重建。过滤空名称与旧行为一致。
    """
    return {s: n for s, n in repo.get_name_map().items() if n}


def _taiwan_live_rows(
    repo, bars: pl.DataFrame, quote_extra: pl.DataFrame | None = None
) -> pl.DataFrame:
    """Today's enriched rows of the Taiwan *bars* from the publish pipeline, empty until it is ready.

    The prev_close of *quote_extra* stands in for a corporate action the table does not list yet.
    """
    if repo is None or bars.is_empty():
        return pl.DataFrame()
    if quote_extra is not None and {"symbol", "prev_close"} <= set(quote_extra.columns):
        bars = bars.drop([c for c in ("prev_close",) if c in bars.columns]).join(
            quote_extra.select("symbol", "prev_close").unique(subset=["symbol"], keep="last"),
            on="symbol",
            how="left",
        )
    rows = repo.get_taiwan_live_enriched().rows(bars)
    if rows is None:
        logger.debug("taiwan live history not prepared yet, %d intraday rows skipped", bars.height)
        return pl.DataFrame()
    return rows


class QuoteService:
    """全局实时行情服务 — 单例。"""

    # Mirrors app.taiwan.benchmarks.BENCHMARK_INDICES; kept literal because every other
    # app.taiwan import in this module is deliberately function-local.
    CORE_INDEX_SYMBOLS = ("t00.TWSE", "o00.TPEX")

    # TWSE MIS builds one unbounded query string (no server-side batching); cap the
    # merged watchlist+monitor-rule symbol pool to keep that URL bounded.
    TAIWAN_REALTIME_MAX_SYMBOLS = 300

    # 档位 → 最小轮询间隔 (秒)
    TIER_MIN_INTERVAL = {
        "expert": 1.0,
        "pro": 3.0,
        "starter": 6.0,
        "free": 6.0,
    }
    DEFAULT_INTERVAL = 6.0
    MAX_INTERVAL = 60.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 串行化行情拉取: 手动 POST /refresh 与后台轮询线程可能并发调用
        # _fetch_quotes, 两者同时写同一批 parquet/缓存会互相覆盖
        self._fetch_lock = threading.Lock()
        self._running = False
        self._enabled = False      # 全局开关 (持久化到 preferences)
        # 暂停态: 盘后管道/数据修正运行期间临时暂停取数, 防止与管道写同一批 parquet 竞态。
        # 与 _enabled 不同 — pause 不改 preferences、不 stop 线程, 仅让轮询循环跳过取数;
        # 进程重启后 _paused 归零, 从 preferences 恢复真实开关态, 无"假关闭"副作用。
        self._paused = False
        self._interval = self.DEFAULT_INTERVAL
        self._thread: threading.Thread | None = None
        self._repo = None          # 延迟注入, 避免循环导入
        # SSE 订阅者集合: 每个 /stream 连接一个 QuoteSubscriber, 事件广播到所有订阅者
        self._subscribers: set[QuoteSubscriber] = set()
        self._strategy_monitor = None            # 延迟注入
        self._app_state = None                   # 延迟注入 (FastAPI app.state)

        # 拉取元信息 (给 SSE / status 用)
        self._fetch_time: float = 0.0       # perf_counter (用于计算 quote_age_ms)
        self._fetch_ms: float = 0.0         # 拉取耗时 (毫秒)
        # _fetched_at 持久化到 preferences: 进程重启后仍能显示"最后获取"时间,
        # 不因关闭开关/重启而归零 (数据页卡片常驻显示, 方便判断上次拉取时刻)。
        try:
            from app.services import preferences as _prefs
            self._fetched_at: float = float(_prefs.load().get("last_fetch_ms", 0.0))
        except Exception:  # noqa: BLE001
            self._fetched_at = 0.0      # 拉取完成的 Unix 时间戳 (毫秒)
        self._symbol_count: int = 0
        self._index_symbol_count: int = 0
        self._etf_symbol_count: int = 0
        self._index_quotes_cache: pl.DataFrame | None = None
        self._intraday_signal_evaluator = IntradaySignalEvaluator()
        self._intraday_signal_bucket: dict[str, str] = {}
        # Close-boundary sync state: one quote pull must succeed after the close
        # before the poll loop is allowed to go idle for the day.
        self._final_sync_done: set[tuple[date, str]] = set()
        self._final_sync_failed: dict[tuple[date, str], str] = {}
        # Today's closing-auction bookkeeping (see _track_closing_quotes). False when the last
        # round's records carried no status, on which no close can be checked.
        self._closing_day: date | None = None
        self._intraday_symbols_today: set[str] = set()
        self._traded_symbols_today: set[str] = set()
        self._close_final_symbols: set[str] = set()
        self._settled_no_close_symbols: set[str] = set()
        # The Taiwan pool requested in the latest round; None for providers without one.
        self._round_symbols: set[str] | None = None
        self._last_closure_probe: datetime | None = None
        self._closing_status_seen = False
        self._closing_evaluated = False
        # Whether this round's quote records came from a live source. None = no
        # status signal (non-Taiwan providers today) -> gate does not apply.
        self._quote_source_realtime: bool | None = None

    # ================================================================
    # 生命周期
    # ================================================================

    def start(self, interval: float = 0.0) -> None:
        """启动后台行情轮询线程。"""
        if self._running:
            return
        if interval <= 0:
            from app.services import preferences
            interval = preferences.get_realtime_quote_interval()
        self._interval = self._clamp_interval(interval)
        self._running = True
        self._enabled = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        self._save_enabled(True)
        logger.info("行情服务已启动, 轮询间隔 %.1fs", self._interval)

    def stop(self, *, persist: bool = True) -> None:
        """停止后台行情轮询线程。

        persist=False when the process is shutting down: a restart is not the user
        switching realtime off, and saving it there leaves the feature disabled after
        every reload until someone flips the toggle by hand.
        """
        self._running = False
        self._enabled = False
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        if persist:
            self._save_enabled(False)
        logger.info("行情服务已停止")

    def enable(self) -> bool:
        """开启自动行情 (不立即启动线程，等下一个交易时段)。

        none 档无实时行情权限,拒绝开启并返回 False;
        free 档开启自选股实时,starter+ 开启全市场实时。返回值表示是否真正开启。
        """
        if not self.is_realtime_allowed():
            logger.warning("实时行情开启被拒:当前档位(none)无实时行情权限")
            return False
        self._enabled = True
        self._save_enabled(True)
        if not self._running:
            from app.services import preferences
            self._interval = self._clamp_interval(preferences.get_realtime_quote_interval())
            self._running = True
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()
        logger.info("行情服务已启用, 轮询间隔 %.1fs", self._interval)
        return True

    def disable(self) -> None:
        """关闭自动行情。"""
        self.stop()
        logger.info("行情服务已关闭")

    # ================================================================
    # 临时暂停 (盘后管道/数据修正期间, 防止写盘竞态)
    # ================================================================

    def pause(self) -> None:
        """临时暂停行情轮询取数 (不关闭线程、不改 preferences)。

        用于盘后管道/数据修正运行期间, 防止实时行情覆写管道正在写的 parquet。
        与 stop() 的区别: 线程继续存活但跳过 _fetch_quotes; preferences 开关态不变,
        管道结束调用 resume() 即恢复。线程级检查, 即时生效, 无 join 等待。
        """
        self._paused = True
        logger.info("行情轮询已临时暂停 (管道/修正运行中)")

    def resume(self) -> None:
        """恢复暂停的行情轮询取数 (对应 pause)。"""
        self._paused = False
        logger.info("行情轮询已恢复")

    def is_paused(self) -> bool:
        """是否处于临时暂停态 (管道运行期间)。"""
        return self._paused

    @contextmanager
    def paused(self):
        """上下文管理器: 进入时暂停轮询取数, 退出时(含异常)自动恢复。

        用法:
            with quote_service.paused():
                run_pipeline(...)
        无论正常结束还是异常/crash, finally 都会 resume (除非进程直接被 kill)。
        """
        self.pause()
        try:
            yield
        finally:
            self.resume()

    def boot_check(self) -> None:
        """启动时检查 preferences，若 enabled 则自动启动。

        none 档无实时行情权限:即使 preferences 标记为 enabled,
        也不启动,并同步 preferences 为关闭(避免 UI 误显示已开启)。
        """
        from app.services import preferences
        if not self.is_realtime_allowed():
            if preferences.get_realtime_quotes_enabled():
                self._save_enabled(False)
            logger.info("实时行情未启动:当前档位(none)无实时行情权限")
            return
        if preferences.get_realtime_quotes_enabled():
            self.start()

    def set_repo(self, repo) -> None:
        """注入 KlineRepository, 用于实时落盘。"""
        self._repo = repo

    def set_app_state(self, app_state) -> None:
        """注入 FastAPI app.state, 用于获取 strategy_monitor 等单例。"""
        self._app_state = app_state

    def set_interval(self, interval: float) -> float:
        """运行时更新轮询间隔（立即生效）。"""
        clamped = self._clamp_interval(interval)
        self._interval = clamped
        from app.services import preferences
        preferences.set_realtime_quote_interval(clamped)
        logger.info("轮询间隔已更新为 %.1fs", clamped)
        return clamped

    def get_min_interval(self) -> float:
        """返回当前档位允许的最小间隔。"""
        return self._tier_min_interval()

    # ================================================================
    # SSE 订阅管理 — 每个 /stream 连接一个订阅者, 事件广播
    # ================================================================

    def subscribe(self) -> QuoteSubscriber:
        """注册一个 SSE 订阅者 (连接建立时调用)。"""
        sub = QuoteSubscriber()
        with self._lock:
            self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: QuoteSubscriber) -> None:
        """注销订阅者 (连接断开时调用)。"""
        with self._lock:
            self._subscribers.discard(sub)

    def _snapshot_subscribers(self) -> list[QuoteSubscriber]:
        with self._lock:
            return list(self._subscribers)

    def _broadcast_quote_updated(self) -> None:
        for sub in self._snapshot_subscribers():
            sub.notify_quote()

    def notify_strategy_results_updated(self) -> None:
        """策略监控完成实时结果更新后调用，仅刷新策略页结果缓存。"""
        for sub in self._snapshot_subscribers():
            sub.notify_strategy_results()

    def notify_depth_updated(self) -> None:
        """五档盘口修正完成后调用: 通知 SSE 推送 depth_updated, 触发连板梯队刷新。

        与行情/告警通道独立 — 只刷新连板梯队, 不连带刷新 watchlist 等。
        """
        for sub in self._snapshot_subscribers():
            sub.notify_depth()

    def _broadcast_alerts(self, alerts: list[dict]) -> None:
        for sub in self._snapshot_subscribers():
            sub.push_alerts(alerts)

    def push_alerts(self, alerts: list[dict]) -> None:
        self._broadcast_alerts(alerts)

    def clear_pending_alerts(self) -> None:
        for sub in self._snapshot_subscribers():
            sub.clear_alerts()

    def push_review_event(self, event_json: str) -> None:
        """广播一条复盘进度事件(JSON 字符串), 唤醒所有 SSE generator。

        事件格式与 recap_market_stream 的产出一致(meta/delta/error/done),
        前端 reviewStore 直接消费。背压在订阅者队列内做 (丢弃最旧)。
        """
        for sub in self._snapshot_subscribers():
            sub.push_review(event_json)

    # ================================================================
    # 档位感知间隔限制
    # ================================================================

    @staticmethod
    def _current_tier() -> str:
        """获取当前档位名（小写）。"""
        from app.tickflow.policy import tier_label
        # Taiwan official mode has no tier, so tier_label() is empty.
        return (tier_label().split() or [""])[0].split("+")[0].strip().lower()

    @classmethod
    def realtime_mode(cls) -> str:
        """当前实时行情模式: 台湾官方模式下全功能开放。"""
        return "full_market"

    @classmethod
    def is_realtime_allowed(cls) -> bool:
        """当前档位是否允许使用实时行情。"""
        return cls.realtime_mode() != "none"

    @classmethod
    def _tier_min_interval(cls) -> float:
        tier = cls._current_tier()
        return cls.TIER_MIN_INTERVAL.get(tier, cls.DEFAULT_INTERVAL)

    def _clamp_interval(self, interval: float) -> float:
        return max(self._tier_min_interval(), min(self.MAX_INTERVAL, interval))

    # ================================================================
    # 行情数据访问
    # ================================================================

    def get_enriched_today(self) -> tuple[pl.DataFrame, date | None]:
        """返回今天 enriched 数据 + 日期 (线程安全)。

        所有页面统一通过此方法获取实时行情 + 技术指标。
        """
        if not self._repo:
            return pl.DataFrame(), None
        return self._repo.get_enriched_latest()

    def get_quotes_compat(self) -> pl.DataFrame:
        """兼容接口: 返回行情 DataFrame (用于盘中选股等需要 last_price/prev_close 的场景)。

        从 _enriched_cache 取 today 的数据, 只选行情基础列, 补上 last_price 别名。
        不返回指标列, 避免 JOIN live_agg 时列名冲突。
        """
        df, _ = self.get_enriched_today()
        if df.is_empty():
            return df

        # 只取盘中选股需要的行情基础列
        keep = [c for c in [
            "symbol", "close", "open", "high", "low", "volume", "amount",
            "prev_close", "change_pct", "change_amount", "amplitude", "turnover_rate",
        ] if c in df.columns]
        df = df.select(keep)

        # enriched 的 close 等价于 last_price
        if "close" in df.columns and "last_price" not in df.columns:
            df = df.with_columns(pl.col("close").alias("last_price"))
        return df

    def get_index_quotes(self, symbols: list[str] | None = None) -> pl.DataFrame:
        """返回实时指数行情缓存。不会触发 TickFlow 请求。"""
        with self._lock:
            df = self._index_quotes_cache.clone() if self._index_quotes_cache is not None else pl.DataFrame()
        if df.is_empty():
            return df
        if symbols:
            return df.filter(pl.col("symbol").is_in(symbols))
        return df

    def status(self) -> dict:
        """返回行情服务状态。"""
        from app.services import preferences
        age = (time.perf_counter() - self._fetch_time) * 1000 if self._fetch_time else -1
        mode = self.realtime_mode()
        phase = self._market_phase()
        final_key = self._final_sync_key(phase)
        final_done = bool(final_key and final_key in self._final_sync_done)
        final_failed = self._final_sync_failed.get(final_key) if final_key else None
        return {
            "enabled": self._enabled,
            "running": self._running,
            "paused": self._paused,
            "mode": mode,
            "realtime_allowed": mode != "none",
            "watchlist_symbol_count": len(preferences.get_realtime_watchlist_symbols()),
            "interval_s": self._interval,
            "symbol_count": self._symbol_count,
            "index_symbol_count": self._index_symbol_count,
            "etf_symbol_count": self._etf_symbol_count,
            "quote_age_ms": round(age, 0) if age >= 0 else None,
            # Regular session only; polling_window is reported separately so the
            # pre-open warm-up and the post-close capture do not read as "trading".
            "is_trading_hours": self._is_continuous_trading(),
            "is_polling_window": self._should_poll_for_phase(phase),
            "market_phase": phase,
            "final_sync_done": final_done,
            "final_sync_failed": final_failed,
            "last_fetch_ms": round(self._fetched_at, 0) if self._fetched_at else None,
        }

    def refresh(self) -> dict:
        """手动触发一次行情拉取。"""
        self._fetch_quotes()
        return self.status()

    # ================================================================
    # 后台轮询
    # ================================================================

    def _poll_loop(self) -> None:
        while self._running and self._enabled:
            try:
                # 管道/数据修正运行期间临时暂停取数, 防止与管道写同一批 parquet 竞态。
                # 线程继续存活 + 分片 sleep, resume() 后即时恢复, 无需重启线程。
                if not self._paused:
                    phase = self._market_phase()
                    if self._should_fetch_for_phase(phase):
                        is_final = phase == "close_final"
                        if phase == "regular" and self._holding_for_suspected_closure():
                            logger.debug("no quote dated today yet, probing for a closure every 5 minutes")
                        elif is_final and self._close_settled_elsewhere():
                            self._final_sync_done.add(self._final_sync_key(phase))
                            logger.info("close already settled for today, skipping final sync")
                        else:
                            ok = self._fetch_quotes(final=is_final)
                            if is_final:
                                self._settle_final_sync(phase, ok)
                    else:
                        logger.debug("非轮询阶段(%s), 跳过行情轮询", phase)
            except Exception as e:  # noqa: BLE001
                logger.warning("行情轮询异常: %s", e)

            waited = 0.0
            while self._running and self._enabled and waited < self._interval:
                time.sleep(0.5)
                waited += 0.5

    def _settle_final_sync(self, phase: str, ok: bool) -> None:
        """Close the day once every symbol that traded today has its closing-auction result.

        Retries until the post-close session ends (14:30), then gives up with
        "closing_quote_missing" so the day is never closed on a fallback snapshot; the
        closing monitor pass still runs for the symbols whose result did arrive.
        """
        from app.taiwan.realtime.calendar import POST_CLOSE_END, taipei_now

        key = self._final_sync_key(phase)
        if not key:
            return
        if ok and self._closing_sync_complete():
            self._final_sync_done.add(key)
            self._final_sync_failed.pop(key, None)
            self._flush_minute_bars()
            logger.info("close final sync completed, entering closed state")
        elif taipei_now().time() > POST_CLOSE_END:
            self._final_sync_failed[key] = "closing_quote_missing"
            self._flush_minute_bars()
            logger.warning(
                "close final sync gave up: no closing quote for %s",
                sorted(self._closing_required_symbols() - self._close_final_symbols),
            )
            self._evaluate_monitors(pl.DataFrame(), None, closing_partial=True)
        elif not ok:
            self._final_sync_failed[key] = "fetch_failed"
            logger.warning("close final sync failed, will keep retrying")
        else:
            logger.info("close final sync waiting for closing quotes, will keep retrying")

    def _track_closing_quotes(self, records: list[dict]) -> None:
        """Remember which symbols traded today and which have their closing result."""
        from app.taiwan.realtime.calendar import taipei_today

        today = taipei_today()
        if self._closing_day != today:
            self._closing_day = today
            self._intraday_symbols_today = set()
            self._traded_symbols_today = set()
            self._close_final_symbols = set()
            self._settled_no_close_symbols = set()
            self._closing_status_seen = False
            self._closing_evaluated = False
        self._closing_status_seen = any(r.get("status") for r in records)
        for r in records:
            symbol, status = r.get("symbol"), r.get("status")
            if not symbol:
                continue
            if status == "realtime":
                self._intraday_symbols_today.add(symbol)
                if _positive(r.get("volume")):
                    self._traded_symbols_today.add(symbol)
            elif status == "close_final":
                self._close_final_symbols.add(symbol)
            elif status == _SETTLED_NO_CLOSE:
                self._settled_no_close_symbols.add(symbol)

    def _holding_for_suspected_closure(self) -> bool:
        """Whether to skip this regular-session round while an unscheduled closure is suspected.

        On a weekday the calendar has not confirmed, no today-dated live quote by 09:05 suggests
        an unannounced closure (e.g. typhoon); the pool is then probed every 5 minutes until one
        arrives.
        """
        from app.services import preferences
        from app.taiwan.realtime.calendar import is_trading_day, taipei_now

        now = taipei_now()
        if (
            now.time() < _SUSPECT_CLOSURE_AFTER
            or preferences.get_realtime_data_provider() not in ("taiwan", "tickflow")
            or is_trading_day(now.date()) is True
            or self._session_quote_seen(now.date())
        ):
            return False
        last = self._last_closure_probe
        if last is not None and last.date() == now.date() and now - last < _SUSPECT_CLOSURE_PROBE_INTERVAL:
            return True
        self._last_closure_probe = now
        return False

    def _session_quote_seen(self, day: date) -> bool:
        """A live quote dated *day* has arrived in this process."""
        return self._closing_day == day and bool(
            self._intraday_symbols_today or self._close_final_symbols
        )

    def _closing_required_symbols(self) -> set[str]:
        """Symbols that traded today, are still in the pool requested this round and may yet close."""
        required = self._traded_symbols_today - self._settled_no_close_symbols
        if self._round_symbols is None:
            return required
        return required & self._round_symbols

    def _closing_sync_complete(self) -> bool:
        """Every pool symbol that traded today has a closing-auction result.

        An untraded symbol never gets one, so it does not block the close. A fallback
        snapshot (STOCK_DAY_ALL or daily K) never counts. Records without any status come from
        providers that report none, where a successful round is all that can be checked.
        """
        if not self._closing_status_seen:
            return True
        return bool(self._close_final_symbols) and self._closing_required_symbols() <= self._close_final_symbols

    def _official_data_stored_today(self) -> bool:
        from app.taiwan.realtime.calendar import taipei_today

        try:
            return bool(self._repo.date_has_official_stock_data(taipei_today()))
        except Exception:
            return False

    def _close_settled_elsewhere(self) -> bool:
        """Today's official data is already stored, so the closing quote must not replace it."""
        from app.services import preferences

        if preferences.get_realtime_data_provider() not in ("taiwan", "tickflow"):
            return False
        return self._official_data_stored_today()

    def _without_settled_closing_quotes(self, records: list[dict]) -> list[dict]:
        """Drop closing results once today's official data is stored (see _close_settled_elsewhere)."""
        if not any(r.get("status") == "close_final" for r in records):
            return records
        if not self._official_data_stored_today():
            return records
        return [r for r in records if r.get("status") != "close_final"]

    def _fetch_quotes(self, *, final: bool = False) -> bool:
        """按当前档位拉取行情。加锁串行化 (后台轮询 vs 手动 refresh)。返回本轮是否成功更新。"""
        with self._fetch_lock:
            before = self._fetched_at
            if final:
                logger.info("最终行情同步开始")
            if self.realtime_mode() == "watchlist":
                self._fetch_watchlist_quotes()
            else:
                self._fetch_full_market_quotes()
            return self._fetched_at > before

    def _fetch_full_market_quotes(self) -> None:
        """拉取全市场行情 → 写 daily + 计算 enriched + 更新缓存。"""
        from app.services import preferences

        provider_name = preferences.get_realtime_data_provider()
        if provider_name in ("taiwan", "tickflow"):
            try:
                t0 = time.perf_counter()
                now_ts = time.perf_counter()
                from app.services import watchlist
                symbols = watchlist.list_symbols()
                watch_syms = [str((s or {}).get("symbol") or "").strip() for s in symbols]
                # Monitor rules may target symbols outside the watchlist; without merging
                # them in, such a symbol never receives a live quote and its rule can
                # never fire (mirrors the index-symbol merge below).
                # Rule symbols go first so the cap below can only drop watchlist symbols.
                rule_syms: list[str] = []
                engine = getattr(self._app_state, "monitor_engine", None) if self._app_state else None
                if engine:
                    for _r in list(engine.rules.values()):
                        if _r.get("enabled", True) and _r.get("scope") == "symbols":
                            rule_syms.extend(_r.get("symbols", []))
                sym_list = list(dict.fromkeys(s for s in (*rule_syms, *watch_syms) if s))
                if len(sym_list) > self.TAIWAN_REALTIME_MAX_SYMBOLS:
                    logger.warning(
                        "台股实时标的池超限 (%d > %d), 已截断; MIS 端点单次请求无内建分批",
                        len(sym_list), self.TAIWAN_REALTIME_MAX_SYMBOLS,
                    )
                    sym_list = sym_list[: self.TAIWAN_REALTIME_MAX_SYMBOLS]
                if not sym_list:
                    return
                records = self._without_settled_closing_quotes(
                    self._taiwan_realtime_records(sym_list)
                )
                self._round_symbols = set(sym_list)
                if records:
                    self._record_minute_bars(records)
                    self._process_full_market_records(records, t0=t0, now_ts=now_ts)
                return
            except Exception as e:
                logger.warning("台湾官方实时行情拉取失败: %s", e)
                return

        from app.data_providers import custom as custom_sources
        if custom_sources.provider_has_dataset(provider_name, "realtime"):
            try:
                t0 = time.perf_counter()
                now_ts = time.perf_counter()
                records = custom_sources.get_provider(provider_name).get_realtime()
                self._process_full_market_records(records, t0=t0, now_ts=now_ts)
                return
            except Exception as e:  # noqa: BLE001
                logger.warning("自定义实时行情拉取失败: %s", e)
                return
        t0 = time.perf_counter()
        now_ts = time.perf_counter()

        try:
            from app.services import preferences
            all_index_symbols = set(self._repo.get_index_symbol_set()) if self._repo else set()
            core_index_symbols = set(preferences.get_realtime_index_symbols() or self.CORE_INDEX_SYMBOLS)
            all_index_symbols.update(core_index_symbols)
            # 指数监控规则标的并入轮询 (mode=core 时 quotes.get 显式拉取覆盖; mode=all 被 CN_Index 全覆盖)
            monitor_index_symbols: set[str] = set()
            engine = getattr(self._app_state, "monitor_engine", None) if self._app_state else None
            if engine:
                for _r in list(engine.rules.values()):
                    if _r.get("enabled", True) and _r.get("asset_type") == "index" and _r.get("scope") == "symbols":
                        monitor_index_symbols.update(s for s in _r.get("symbols", []) if s)
            all_index_symbols.update(monitor_index_symbols)
            all_etf_symbols = set()
            if self._repo:
                etf_inst = self._repo.get_etf_instruments()
                if not etf_inst.is_empty() and "symbol" in etf_inst.columns:
                    all_etf_symbols = set(etf_inst["symbol"].cast(pl.Utf8).to_list())

            universes: list[str] = []
            if preferences.get_realtime_pull_stock():
                universes.append("CN_Equity_A")
            if preferences.get_realtime_pull_etf() and all_etf_symbols:
                universes.append("CN_ETF")
            if preferences.get_realtime_pull_index() and preferences.get_realtime_index_mode() == "all":
                universes.append("CN_Index")

            resp = []
            if universes:
                _u0 = time.perf_counter()
                logger.info("拉取全市场行情 (universes=%s, SDK超时=30s×重试3)", universes)
                resp.extend(tf.quotes.get_by_universes(universes=universes) or [])
                logger.info("全市场行情拉取完成: %d 条 (%.2fs)", len(resp), time.perf_counter() - _u0)
            if preferences.get_realtime_pull_index() and preferences.get_realtime_index_mode() == "core":
                _i0 = time.perf_counter()
                _core_syms = sorted(core_index_symbols | monitor_index_symbols)
                resp.extend(tf.quotes.get(symbols=_core_syms) or [])
                logger.info("核心指数行情拉取完成: %d 只 (%.2fs)", len(_core_syms), time.perf_counter() - _i0)
        except Exception as e:  # noqa: BLE001
            logger.warning("行情拉取失败 (%.2fs): %s", time.perf_counter() - t0, e)
            return

        if not resp:
            logger.warning("行情数据为空")
            return

        # ---- 解析 API 响应 (临时变量, 用完丢弃) ----
        records = []
        for q in resp:
            ext = q.get("ext") or {}
            last_price = q.get("last_price")
            prev_close = q.get("prev_close")
            change_amount = ext.get("change_amount")
            change_pct = ext.get("change_pct")
            if change_amount is None and last_price is not None and prev_close is not None:
                change_amount = float(last_price) - float(prev_close)
            if change_pct is None and change_amount is not None and prev_close not in (None, 0):
                # 与 API ext.change_pct 同为小数制 (0.0366 = 3.66%),
                # enriched 全项目约定小数 (见 pipeline.py), 此处不可乘 100
                change_pct = float(change_amount) / float(prev_close)
            records.append({
                "symbol": q.get("symbol"),
                "name": q.get("name") or ext.get("name"),
                "last_price": last_price,
                "prev_close": prev_close,
                "open": q.get("open"),
                "high": q.get("high"),
                "low": q.get("low"),
                "volume": q.get("volume"),
                "amount": q.get("amount"),
                "change_pct": change_pct,
                "change_amount": change_amount,
                "amplitude": ext.get("amplitude"),
                "turnover_rate": ext.get("turnover_rate"),
                "timestamp": q.get("timestamp"),
                "session": q.get("session"),
            })

        self._process_full_market_records(records, t0=t0, now_ts=now_ts)

    @staticmethod
    def _taiwan_realtime_records(symbols: list[str]) -> list[dict]:
        from app.data_providers.registry import get_provider

        df = get_provider("taiwan").get_realtime(symbols=symbols)
        if df.is_empty():
            return []
        if "change_pct" in df.columns:
            # mis/yahoo/enrichment providers report change_pct as a percentage
            # number (1.30 = 1.30%); the enriched pipeline is decimal-scale
            # (0.05 = 5%, see ENRICHED_COLUMNS in pipeline.py). Normalize here
            # so quote_extra/enriched/rules/alerts all agree.
            df = df.with_columns((pl.col("change_pct") / 100.0).alias("change_pct"))
        return df.to_dicts()

    def fetch_live_bar(self, symbol: str) -> dict | None:
        """Today's row for a symbol outside the polling pool, fetched on demand.

        Same provider, realtime gate, change_pct scale and indicator pipeline as the
        poll, so the row matches what the symbol would show once it joins the pool.
        Without the prepared history (Taiwan) or recursive state it is the bare OHLCV bar.
        """
        from app.services import preferences

        if preferences.get_realtime_data_provider() not in ("taiwan", "tickflow"):
            return None
        try:
            records = self._records_datable_today(self._taiwan_realtime_records([symbol]))
            bar = self._build_daily(records)
        except Exception as e:
            logger.debug("On-demand quote failed for %s: %s", symbol, e)
            return None
        if bar.is_empty():
            return None
        live_bar = {**bar.row(0, named=True), "change_pct": records[0].get("change_pct")}
        try:
            if bar.filter(_is_taiwan_symbol()).is_empty():
                prev_enriched, _ = self._repo.get_enriched_latest()
                enriched = self._enrich_today_incremental(
                    bar, self._build_quote_extra(records), self._repo.get_live_agg(), prev_enriched,
                )
            else:
                enriched = _taiwan_live_rows(self._repo, bar, self._build_quote_extra(records))
        except Exception as e:
            logger.debug("On-demand indicators failed for %s: %s", symbol, e)
            return live_bar
        return enriched.row(0, named=True) if not enriched.is_empty() else live_bar

    def _process_full_market_records(self, records: list[dict], *, t0: float, now_ts: float) -> None:
        """把全市场 records 写盘并增量计算 enriched。"""
        from app.services import preferences
        # TaiwanHybridProvider.get_realtime() falls back to official.fetch_quote /
        # get_daily (status != "realtime") when no live quote exists; that batch
        # must not be evaluated by monitor rules (see _evaluate_monitors), so the
        # status is captured here before quote_extra drops the column.
        self._quote_source_realtime = self._records_realtime_status(records)
        self._track_closing_quotes(records)
        all_index_symbols = set(self._repo.get_index_symbol_set()) if self._repo else set()
        core_index_symbols = set(preferences.get_realtime_index_symbols() or self.CORE_INDEX_SYMBOLS)
        all_index_symbols.update(core_index_symbols)
        all_etf_symbols = set()
        if self._repo:
            etf_inst = self._repo.get_etf_instruments()
            if not etf_inst.is_empty() and "symbol" in etf_inst.columns:
                all_etf_symbols = set(etf_inst["symbol"].cast(pl.Utf8).to_list())

        if not records:
            logger.warning("行情数据为空")
            return

        index_records = [r for r in records if r.get("symbol") in all_index_symbols]
        etf_records = [r for r in records if r.get("symbol") in all_etf_symbols]
        stock_records = [
            r for r in records
            if r.get("symbol") not in all_index_symbols and r.get("symbol") not in all_etf_symbols
        ]

        fetch_ms = (time.perf_counter() - t0) * 1000
        fetched_at = time.time() * 1000

        # ---- 更新元信息 ----
        with self._lock:
            self._fetch_time = now_ts
            self._fetch_ms = fetch_ms
            self._fetched_at = fetched_at
            self._symbol_count = len(stock_records)
            self._index_symbol_count = len(index_records)
            self._etf_symbol_count = len(etf_records)
            self._index_quotes_cache = self._build_index_quotes(index_records)

        _persist_last_fetch(fetched_at)
        logger.info("行情刷新: %d 只股票, %d 只ETF, %d 只指数, 耗时 %.0fms", len(stock_records), len(etf_records), len(index_records), fetch_ms)

        # Only realtime quotes become today's bar (see _records_datable_today); the
        # quote cache above still keeps the others for display.
        daily_df = self._build_daily(self._records_datable_today(stock_records))
        etf_daily_df = self._build_daily(self._records_datable_today(etf_records))
        # Taiwan bars are owned by TaiwanDailyStore; kline_daily/kline_etf_daily take non-Taiwan rows only.
        for asset_type, bars in (("stock", daily_df), ("etf", etf_daily_df)):
            legacy_bars = bars.filter(~_is_taiwan_symbol()) if not bars.is_empty() else bars
            if legacy_bars.is_empty() or not self._repo:
                continue
            try:
                self._repo.flush_live_daily_asset(asset_type, legacy_bars)
            except Exception as e:
                logger.warning("live %s daily write failed: %s", asset_type, e)

        # ---- 构建 API 直接值的补充表 (不写 daily, 只用于 enriched 计算) ----
        quote_extra = self._build_quote_extra(stock_records)
        etf_quote_extra = self._build_quote_extra(etf_records)

        # ---- 增量计算 enriched + 写盘 + 更新缓存 ----
        if not daily_df.is_empty() and self._repo:
            self._flush_live_enriched(daily_df, quote_extra, asset_type="stock")
        if not etf_daily_df.is_empty() and self._repo:
            self._flush_live_enriched(etf_daily_df, etf_quote_extra, asset_type="etf")
        # ---- 指数: 仅有指数监控规则时才写盘 (无规则零成本) ----
        # mode=all (完整 CN_Index universe) → flush 覆盖; mode=core (部分标的) → merge 不截断分区
        engine = getattr(self._app_state, "monitor_engine", None) if self._app_state else None
        if engine and engine.has_asset_rules("index") and self._repo:
            index_daily_df = self._build_daily(self._records_datable_today(index_records))
            if not index_daily_df.is_empty():
                use_flush = preferences.get_realtime_index_mode() == "all"
                try:
                    if use_flush:
                        self._repo.flush_live_daily_asset("index", index_daily_df)
                    else:
                        self._repo.merge_live_daily_asset("index", index_daily_df)
                except Exception as e:  # noqa: BLE001
                    logger.warning("指数日K写盘失败: %s", e)
                self._flush_live_enriched(index_daily_df, self._build_quote_extra(index_records), asset_type="index", merge=not use_flush)

        # ---- 通知 SSE ----
        self._broadcast_quote_updated()

        # ---- 策略监控 + 告警评估 ----
        self._evaluate_monitors(daily_df, quote_extra)

    def _fetch_watchlist_quotes(self) -> None:
        """Free 档自选股实时: 按 capability batch 上限分批拉取。"""
        from app.services import preferences
        from app.tickflow.client import get_paid_realtime_client
        from app.tickflow.capabilities import Cap
        from app.tickflow.policy import detect_capabilities
        from app.tickflow.rate_limits import chunked, resolve_limit, sleep_between_batches

        symbols = preferences.get_realtime_watchlist_symbols()
        # 指数监控规则标的并入轮询 (与股票共享 batch 额度)
        engine = getattr(self._app_state, "monitor_engine", None) if self._app_state else None
        if engine:
            for _r in list(engine.rules.values()):
                if _r.get("enabled", True) and _r.get("asset_type") == "index" and _r.get("scope") == "symbols":
                    for _s in _r.get("symbols", []):
                        if _s and _s not in symbols:
                            symbols.append(_s)
        if not symbols:
            logger.info("自选实时未配置标的, 跳过行情拉取")
            return

        tf = get_paid_realtime_client()
        if tf is None:
            logger.warning("自选实时拉取失败:未配置付费服务器 API Key")
            return

        # 按 capability batch 上限分批: 股票+指数共享额度, 超过上限会导致整轮失败
        capset = detect_capabilities()
        lim = resolve_limit(capset, Cap.QUOTE_BY_SYMBOL, default_batch=5)
        batches = chunked(symbols, lim.batch)

        t0 = time.perf_counter()
        now_ts = time.perf_counter()
        resp = []
        for i, batch in enumerate(batches):
            sleep_between_batches(i, lim.rpm)
            try:
                resp.extend(tf.quotes.get(symbols=batch) or [])
            except Exception as e:  # noqa: BLE001
                logger.warning("自选实时批次 %d/%d 拉取失败: %s", i + 1, len(batches), e)

        if not resp:
            logger.warning("自选实时行情数据为空")
            return

        records = []
        for q in resp:
            ext = q.get("ext") or {}
            last_price = q.get("last_price")
            prev_close = q.get("prev_close")
            change_amount = ext.get("change_amount")
            change_pct = ext.get("change_pct")
            if change_amount is None and last_price is not None and prev_close is not None:
                change_amount = float(last_price) - float(prev_close)
            if change_pct is None and change_amount is not None and prev_close not in (None, 0):
                # 小数制, 与 ext.change_pct / enriched 口径一致 (不乘 100)
                change_pct = float(change_amount) / float(prev_close)
            records.append({
                "symbol": q.get("symbol"),
                "name": q.get("name") or ext.get("name"),
                "last_price": last_price,
                "prev_close": prev_close,
                "open": q.get("open"),
                "high": q.get("high"),
                "low": q.get("low"),
                "volume": q.get("volume"),
                "amount": q.get("amount"),
                "change_pct": change_pct,
                "change_amount": change_amount,
                "amplitude": ext.get("amplitude"),
                "turnover_rate": ext.get("turnover_rate"),
                "timestamp": q.get("timestamp"),
                "session": q.get("session"),
            })

        index_set = self._repo.get_index_symbol_set() if self._repo else set()
        etf_set = self._repo.get_etf_symbol_set() if self._repo else set()
        index_records, etf_records, stock_records = self._split_records_by_asset(records, index_set, etf_set)

        fetch_ms = (time.perf_counter() - t0) * 1000
        fetched_at = time.time() * 1000
        with self._lock:
            self._fetch_time = now_ts
            self._fetch_ms = fetch_ms
            self._fetched_at = fetched_at
            self._symbol_count = len(stock_records)
            self._index_symbol_count = len(index_records)
            self._etf_symbol_count = len(etf_records)
            self._index_quotes_cache = self._build_index_quotes(index_records) if index_records else None

        _persist_last_fetch(fetched_at)
        logger.info("自选实时刷新: %d 只股票, %d 只ETF, %d 只指数, 耗时 %.0fms",
                    len(stock_records), len(etf_records), len(index_records), fetch_ms)

        daily_df = self._build_daily(stock_records)
        quote_extra = self._build_quote_extra(stock_records)
        if not daily_df.is_empty() and self._repo:
            try:
                self._repo.merge_live_daily_asset("stock", daily_df)
            except Exception as e:  # noqa: BLE001
                logger.warning("自选实时日K写盘失败: %s", e)
            self._flush_live_enriched(daily_df, quote_extra, asset_type="stock", merge=True)

        # ETF/指数进自选前5时按各自资产落盘, 不污染股票表
        etf_daily_df = self._build_daily(etf_records)
        if not etf_daily_df.is_empty() and self._repo:
            try:
                self._repo.merge_live_daily_asset("etf", etf_daily_df)
            except Exception as e:  # noqa: BLE001
                logger.warning("自选实时 ETF 日K写盘失败: %s", e)
            self._flush_live_enriched(etf_daily_df, self._build_quote_extra(etf_records), asset_type="etf", merge=True)
        index_daily_df = self._build_daily(index_records)
        if not index_daily_df.is_empty() and self._repo:
            try:
                self._repo.merge_live_daily_asset("index", index_daily_df)
            except Exception as e:  # noqa: BLE001
                logger.warning("自选实时指数日K写盘失败: %s", e)
            self._flush_live_enriched(index_daily_df, self._build_quote_extra(index_records), asset_type="index", merge=True)

        self._broadcast_quote_updated()
        # This path never goes through _process_full_market_records, so clear any
        # stale flag left by a previous Taiwan-provider round.
        self._quote_source_realtime = None
        self._evaluate_monitors(daily_df, quote_extra)

    # ================================================================
    # 工具
    # ================================================================

    @staticmethod
    def _split_records_by_asset(
        records: list[dict], index_set: set[str], etf_set: set[str],
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """把行情 records 按资产拆成 (index, etf, stock)。判定顺序与 resolve_asset_type 一致: 先 ETF 后指数。"""
        index_records: list[dict] = []
        etf_records: list[dict] = []
        stock_records: list[dict] = []
        for r in records:
            sym = r.get("symbol")
            if sym in etf_set:
                etf_records.append(r)
            elif sym in index_set:
                index_records.append(r)
            else:
                stock_records.append(r)
        return index_records, etf_records, stock_records

    @staticmethod
    def _records_datable_today(records: list[dict]) -> list[dict]:
        """Records fit to be stamped with today's date.

        The Taiwan provider falls back to the official endpoint, which publishes the
        PREVIOUS session's OHLC during trading hours. _build_daily stamps whatever it
        is given with cn_today(), so letting those through writes yesterday's close as
        today's bar - the price a screen then disagrees with. Only rows the provider
        marked realtime or close_final (today's closing-auction result) qualify.

        Records with no status at all come from a provider that does not report one
        (the TickFlow SDK); the gate does not apply and they pass through unchanged.
        """
        if not any(r.get("status") for r in records):
            return records
        return [r for r in records if r.get("status") in _LIVE_RECORD_STATUSES]

    @staticmethod
    def _quote_ts_expr(df: pl.DataFrame) -> pl.Expr | None:
        """The quote timestamp in milliseconds, which is what the column contracts.

        The provider already emits a millisecond quote_ts; casting its datetime
        "timestamp" instead yields microseconds, a silent factor of 1000 that makes
        session_progress_from_ts read every intraday bar as a finished session.
        """
        if "quote_ts" in df.columns:
            return pl.col("quote_ts").cast(pl.Int64, strict=False).alias("quote_ts")
        if "timestamp" not in df.columns:
            return None
        if df.schema["timestamp"] == pl.Datetime:
            return pl.col("timestamp").dt.epoch(time_unit="ms").alias("quote_ts")
        return pl.col("timestamp").cast(pl.Int64, strict=False).alias("quote_ts")

    @staticmethod
    def _build_daily(records: list[dict]) -> pl.DataFrame:
        """将 API records 转为日K格式 DataFrame (OHLCV + quote_ts, 写 kline_daily 用)。"""
        if not records:
            return pl.DataFrame()
        df = pl.DataFrame(records)
        cols_map = {
            "symbol": "symbol",
            "last_price": "close",
            "open": "open",
            "high": "high",
            "low": "low",
            "volume": "volume",
            "amount": "amount",
        }
        # Taiwan official provider emits "close" where TickFlow emits "last_price".
        if "last_price" not in df.columns and "close" in df.columns:
            cols_map["close"] = "close"
        select_exprs = [pl.col(src).alias(dst) for src, dst in cols_map.items() if src in df.columns]
        quote_ts = QuoteService._quote_ts_expr(df)
        if quote_ts is not None:
            select_exprs.append(quote_ts)
        if not select_exprs:
            return pl.DataFrame()
        result = df.select(select_exprs).with_columns(
            pl.lit(cn_today()).cast(pl.Date).alias("date"),
        )
        # 修复: API 在非交易时段可能返回 open/high/low=0 或 null,
        # 导致蜡烛从 0 开始。用 close 填充这些异常值。
        for col in ("open", "high", "low"):
            if col in result.columns:
                result = result.with_columns(
                    pl.when((pl.col(col) == 0) | pl.col(col).is_null())
                    .then(pl.col("close"))
                    .otherwise(pl.col(col))
                    .alias(col)
                )
        return result

    @staticmethod
    def _records_realtime_status(records: list[dict]) -> bool | None:
        """Whether every record of the round is live (status realtime or close_final).

        None when the records carry no status (providers other than the Taiwan hybrid): no gate applies.
        """
        statuses = {r.get("status") for r in records if r.get("status")} - {_SETTLED_NO_CLOSE}
        if not statuses:
            return None
        return statuses <= _LIVE_RECORD_STATUSES

    @staticmethod
    def _build_quote_extra(records: list[dict]) -> pl.DataFrame:
        """构建 API 直接提供的补充字段 (不写 daily, 只传给 enriched 计算)。

        包含: prev_close, change_pct, change_amount, amplitude, turnover_rate。
        """
        if not records:
            return pl.DataFrame()
        df = pl.DataFrame(records)
        keep = [c for c in [
            "symbol", "prev_close", "change_pct", "change_amount",
            "amplitude", "turnover_rate",
        ] if c in df.columns]
        if not keep or "symbol" not in keep:
            return pl.DataFrame()
        out = df.select(keep)
        # 实时 API 的 turnover_rate 入口契约为小数制(0.05 = 5%).
        # enriched 内部统一存百分数值(5 = 5%), 后续页面/筛选直接展示和比较。
        if "turnover_rate" in out.columns:
            out = out.with_columns((pl.col("turnover_rate").cast(pl.Float64, strict=False) * 100).alias("turnover_rate"))
        return out

    @staticmethod
    def _build_index_quotes(records: list[dict]) -> pl.DataFrame:
        """构建指数实时行情缓存，不落股票 parquet。

        注意: API 返回的 change_pct/amplitude 是小数 (0.0366 = 3.66%),
        统一转成百分比输出, 与 _fallback_index_quotes_from_daily 口径一致
        (前端指数侧不×100, 直接 toFixed(2)% 展示)。
        """
        if not records:
            return pl.DataFrame()
        df = pl.DataFrame(records)
        keep = [c for c in [
            "symbol", "name", "last_price", "prev_close", "open", "high", "low",
            "volume", "amount", "change_pct", "change_amount", "amplitude", "timestamp", "session",
        ] if c in df.columns]
        if not keep or "symbol" not in keep:
            return pl.DataFrame()
        df = df.select(keep)
        # 自定义源可能不提供 change_pct/change_amount, 按 last_price/prev_close 补算
        # (TickFlow 路径在 _fetch_full_market_quotes 已算好, 此处只补缺失的)
        if "change_pct" not in df.columns and "last_price" in df.columns and "prev_close" in df.columns:
            # prev_close=0 → inf (非合法 JSON), prev_close=null → null; 用 when 守护
            df = df.with_columns(
                pl.when(pl.col("prev_close") != 0)
                .then((pl.col("last_price") - pl.col("prev_close")) / pl.col("prev_close"))
                .otherwise(None)
                .alias("change_pct")
            )
        if "change_amount" not in df.columns and "last_price" in df.columns and "prev_close" in df.columns:
            df = df.with_columns(
                (pl.col("last_price") - pl.col("prev_close")).alias("change_amount")
            )
        # change_pct / amplitude: 小数 → 百分比 (统一指数展示口径)
        for col in ("change_pct", "amplitude"):
            if col in df.columns:
                df = df.with_columns((pl.col(col).cast(pl.Float64) * 100).alias(col))
        if "last_price" in df.columns and "close" not in df.columns:
            df = df.with_columns(pl.col("last_price").alias("close"))
        return df

    @staticmethod
    def _market_phase() -> str:
        """Taiwan quote polling phase (Asia/Taipei).

        The regular session runs 09:00-13:30 with no lunch break (continuous trading to 13:25,
        then the closing auction), so the close is the only final-sync boundary: "close_final" keeps polling until the
        closing-auction results are in (see _settle_final_sync). A published exchange holiday or a
        closure the user marked for today is "closed"; an unverified weekday polls.
        """
        from app.services import preferences
        from app.taiwan.realtime.calendar import (
            PRE_OPEN_START,
            REGULAR_OPEN_END,
            REGULAR_OPEN_START,
            is_trading_day,
            taipei_now,
        )

        now = taipei_now()
        if is_trading_day(now.date()) is False:
            return "closed"
        if preferences.get_taiwan_market_closed_date() == now.date().isoformat():
            return "closed"
        t = now.time()
        if PRE_OPEN_START <= t < REGULAR_OPEN_START:
            return "preopen"
        if REGULAR_OPEN_START <= t <= REGULAR_OPEN_END:
            return "regular"
        if t > REGULAR_OPEN_END:
            return "close_final"
        return "closed"

    @staticmethod
    def _final_sync_key(phase: str) -> tuple[date, str] | None:
        if phase == "close_final":
            from app.taiwan.realtime.calendar import taipei_today

            return (taipei_today(), "close")
        return None

    def _should_poll_for_phase(self, phase: str) -> bool:
        """Whether the phase polls quotes; the close stops once its final sync completes or gives up."""
        if phase in {"preopen", "regular"}:
            return True
        key = self._final_sync_key(phase)
        return bool(
            key
            and key not in self._final_sync_done
            and self._final_sync_failed.get(key) != "closing_quote_missing"
        )

    def _should_fetch_for_phase(self, phase: str) -> bool:
        return self._should_poll_for_phase(phase)

    @staticmethod
    def _is_continuous_trading() -> bool:
        """Taiwan continuous trading (Asia/Taipei): 09:00-13:25 on weekdays.

        Stricter than the polling window, which also spans the 08:30-09:00 and
        13:25-13:30 call auctions and the post-close capture: alerts must not fire on indicative or
        stale prices. Published holidays come from the calendar and a closure the user
        marked for today matches _market_phase; unscheduled closures are covered by the
        "snapshot date == today" freshness check in _evaluate_monitors.
        """
        from app.services import preferences
        from app.taiwan.realtime.calendar import get_market_status, taipei_now
        from app.taiwan.realtime.models import MarketStatus

        now = taipei_now()
        if get_market_status(now) != MarketStatus.OPEN:
            return False
        return preferences.get_taiwan_market_closed_date() != now.date().isoformat()

    def _take_closing_evaluation(self, *, partial: bool = False) -> bool:
        """True once per day: when a live round completes the closing results, or at give-up.

        Never in a process that saw no continuous trading today: after a restart, the process
        that watched the session may already have alerted on the close.
        """
        if (
            self._closing_evaluated
            or not self._closing_status_seen
            or not self._close_final_symbols
            or not self._intraday_symbols_today
        ):
            return False
        if not partial and (
            self._quote_source_realtime is not True or not self._closing_sync_complete()
        ):
            return False
        self._closing_evaluated = True
        return True

    def _closing_scope(self, enriched: pl.DataFrame) -> pl.DataFrame:
        """Rows the closing pass may alert on: symbols whose closing result arrived."""
        if enriched.is_empty() or "symbol" not in enriched.columns:
            return enriched
        return enriched.filter(pl.col("symbol").is_in(sorted(self._close_final_symbols)))

    @staticmethod
    def _save_enabled(enabled: bool) -> None:
        from app.services import preferences
        preferences.save({"realtime_quotes_enabled": enabled})

    # ================================================================
    # 策略监控
    # ================================================================

    def _evaluate_monitors(
        self,
        daily_df: pl.DataFrame,
        quote_extra: pl.DataFrame | None,
        *,
        closing_partial: bool = False,
    ) -> None:
        """行情更新后评估统一监控规则引擎,并刷新策略结果缓存。"""
        try:
            # Alerts only during continuous trading, plus one pass on the closing-auction
            # result: the pre-open and 13:25-13:30 auctions only carry trial prices.
            closing_pass = False
            if closing_partial or not self._is_continuous_trading():
                closing_pass = self._take_closing_evaluation(partial=closing_partial)
                if not closing_pass:
                    return
            if not closing_partial and getattr(self, "_quote_source_realtime", None) is False:
                logger.debug(
                    "跳过监控评估: 本轮行情来源非即时回退 (status!=realtime), "
                    "避免用官方回退/昨收数据误发告警",
                )
                return
            # 获取 enriched 数据 (刚算好的)
            enriched_today, enriched_date = self.get_enriched_today()
            if closing_pass:
                enriched_today = self._closing_scope(enriched_today)
            # 股票快照就绪 = 非空 + 日期为当日。未就绪时仅跳过股票轮,
            # ETF/指数轮有各自的空表+日期守卫, 不受影响 (纯指数行情/自选场景可独立评估)。
            stock_ready = (not enriched_today.is_empty()) and (enriched_date == cn_today())
            if not stock_ready:
                logger.debug("股票快照未就绪(空=%s, 日期=%s), 跳过股票轮",
                             enriched_today.is_empty(), enriched_date)

            all_alerts: list[dict] = []
            rule_events: list[dict] = []
            engine = None

            # 通用监控规则评估 (统一引擎: signal/price/market/strategy)
            if self._app_state:
                engine = getattr(self._app_state, "monitor_engine", None)
                if engine and engine.rule_count > 0:
                    # 预构建 symbol → name 映射 (enriched 已 drop name 列, 引擎触发时回填用)。
                    # 股票 + ETF + 指数三表合并走 _monitor_name_map -> repo.get_name_map()
                    # 的进程内 memo, 避免每轮监控对 ~7000 行维表 iter_rows 重建。
                    try:
                        name_map = _monitor_name_map(self._app_state.repo)
                        if name_map:
                            engine.set_name_map(name_map)
                    except Exception as e:  # noqa: BLE001
                        logger.debug("name_map 构建失败 (不影响监控): %s", e)
                    # 股票轮: 快照未就绪时跳过
                    if stock_ready:
                        eval_df = self._inject_intraday_signals(enriched_today, engine, "stock")
                        rule_events = engine.evaluate(eval_df, asset_type="stock")
                        if engine.consume_strategy_result_updates():
                            self.notify_strategy_results_updated()
                    if engine.has_rule_type("sector"):
                        rule_events += engine.evaluate_sectors(
                            enriched_today if stock_ready else pl.DataFrame(),
                            self.get_index_quotes(),
                        )
                    # ETF 规则轮: 股票快照不含 ETF, 用 ETF enriched 快照单独评估。
                    # 独立 try —— ETF 轮任何异常都不得丢弃本轮已算出的股票告警。
                    # refresh=False —— 不在轮询线程上触发 ETF 冷缓存的同步重算 (缓存由 ETF 实时
                    # flush 焐热; 未焐热说明无 ETF 实时数据, 跳过本轮 ETF 评估)。
                    if engine.has_asset_rules("etf") and self._repo is not None:
                        try:
                            etf_enriched, _ = self._repo.get_enriched_latest_asset("etf", refresh=False)
                            if closing_pass:
                                etf_enriched = self._closing_scope(etf_enriched)
                            if not etf_enriched.is_empty():
                                etf_enriched = self._inject_intraday_signals(etf_enriched, engine, "etf")
                                rule_events = rule_events + engine.evaluate(
                                    etf_enriched, asset_type="etf", reset_strategy_results=False,
                                )
                        except Exception as e:  # noqa: BLE001
                            logger.warning("ETF 监控评估失败 (不影响股票告警): %s", e)
                    # 指数规则轮: 复刻 ETF 轮。快照由指数实时 flush 焐热;
                    # refresh=False 冷缓存不同步重算; 显式日期守卫防陈旧 parquet 误告警
                    # (ETF 轮靠空表隐式跳过, 指数轮更显式, 行为等价)。
                    if engine.has_asset_rules("index") and self._repo is not None:
                        try:
                            index_enriched, index_date = self._repo.get_enriched_latest_asset("index", refresh=False)
                            if not index_enriched.is_empty() and index_date == cn_today():
                                index_enriched = self._inject_intraday_signals(index_enriched, engine, "index")
                                rule_events = rule_events + engine.evaluate(
                                    index_enriched, asset_type="index", reset_strategy_results=False,
                                )
                        except Exception as e:  # noqa: BLE001
                            logger.warning("指数监控评估失败 (不影响股票/ETF 告警): %s", e)

                    if rule_events:
                        rule_events = self._format_extension_notifications(rule_events)
                        # 落盘到 alerts.jsonl
                        try:
                            from app.services import alert_store
                            alert_store.append_many(
                                self._app_state.repo.store.data_dir, rule_events,
                            )
                        except Exception as e:  # noqa: BLE001
                            logger.warning("告警落盘失败: %s", e)
                        # 转为 SSE 推送格式 (兼容旧 alert schema)
                        for ev in rule_events:
                            alert = {
                                "source": ev["source"],
                                "type": ev["type"],
                                "rule_id": ev.get("rule_id"),
                                "strategy_id": ev.get("strategy_id") if ev["source"] == "strategy" else None,
                                "symbol": ev["symbol"],
                                "name": ev["name"],
                                "message": ev["message"],
                                "price": ev["price"],
                                "change_pct": ev["change_pct"],
                                "signals": ev["signals"],
                                "severity": ev.get("severity", "info"),
                                "conditions": ev.get("conditions") or [],
                                "logic": ev.get("logic") or "and",
                            }
                            for key in (
                                "sector_kind", "sector_key", "sector_name",
                                "sector_source_field", "sector_value", "sector_level",
                                "window_change_pct", "coverage_ratio", "valid_count",
                                "total_count", "up_count", "down_count", "leader",
                            ):
                                if key in ev:
                                    alert[key] = ev[key]
                            all_alerts.append(alert)

            # 策略页实时回显: 不写文件 (实时行情每轮更新 enriched, 写文件会被 read_cache
            # 的 mtime 校验判过期, 反复读不到)。监控引擎本轮已算出的结果存在内存
            # (latest_strategy_results), 由 /api/screener/cached 端点直接叠加读取。

            # 广播到所有 SSE 订阅者 (背压保护在订阅者队列内做)
            if all_alerts:
                # 按 symbol 富化行业/概念 ext 字段, 使 toast + 触发记录统一展示板块标签。
                self._enrich_alerts_ext(all_alerts)
                self._broadcast_alerts(all_alerts)
                logger.info("监控评估完成: %d 条通知", len(all_alerts))

                # 系统通知 (可选通道, 由 preferences 开关控制)。
                # cooldown 去重已在 MonitorRuleEngine 做过, 这里只负责转发。
                self._maybe_send_system_notifications(all_alerts)

            # 外部推播 (由规则 webhook_channels 指定渠道)。
            # 紧随系统通知, 同样静默降级不阻断主流程。
            if rule_events:
                self._maybe_send_webhook(rule_events, engine)

        except Exception as e:  # noqa: BLE001
            logger.warning("监控评估失败: %s", e)

    def _format_extension_notifications(self, events: list[dict]) -> list[dict]:
        """Apply optional copy formatters after evaluation and before every output channel."""
        registry = (
            getattr(self._app_state, "extension_registry", None)
            if self._app_state is not None
            else None
        )
        if registry is None or not registry.has_notification_formatters:
            return events

        from app.extensions.contracts import (
            BACKEND_EXTENSION_API_VERSION,
            NotificationFormatContext,
        )

        formatted_events: list[dict] = []
        for event in events:
            formatted = dict(event)
            context = NotificationFormatContext(
                api_version=BACKEND_EXTENSION_API_VERSION,
            )
            for registered in registry.notification_formatters():
                try:
                    message = registered.implementation.format_message(dict(formatted), context)
                    if not isinstance(message, str):
                        raise TypeError("notification formatter must return str")
                    formatted["message"] = message
                except Exception as exc:
                    logger.warning(
                        "notification formatter failed %s: %s",
                        registered.implementation_id,
                        exc,
                    )
            formatted_events.append(formatted)
        return formatted_events

    def _enrich_alerts_ext(self, alerts: list[dict]) -> None:
        """就地给告警事件按 symbol 追加行业/概念 ext 字段。

        读 preferences.get_monitor_ext_fields() 取字段配置, 用 screener._load_ext_value_maps
        (带 parquet mtime 缓存) 富化。富化失败静默降级 (告警照常推送, 只是没标签)。
        每条事件新增 {configId}__{fieldName} 键 (与 watchlist/screener 输出约定一致)。
        """
        if not alerts or not self._app_state or self._repo is None:
            return
        try:
            from app.services import preferences
            fields = preferences.get_monitor_ext_fields()
            # 新结构 {field, maxTags, hiddenIndices}, 后端只需 .field
            parts = []
            for key in ("concept", "industry"):
                item = fields.get(key)
                if isinstance(item, dict) and item.get("field"):
                    parts.append(item["field"])
                elif isinstance(item, str) and item:
                    parts.append(item)  # 兼容旧格式
            if not parts:
                return
            ext_columns = ",".join(parts)
            from app.api.screener import _load_ext_value_maps
            value_maps = _load_ext_value_maps(self._repo, ext_columns)
            if not value_maps:
                return
            for ev in alerts:
                sym = ev.get("symbol")
                if not sym:
                    continue
                for out_col, vmap in value_maps.items():
                    ev[out_col] = vmap.get(str(sym))
        except Exception as e:  # noqa: BLE001
            logger.debug("告警 ext 富化失败 (不影响推送): %s", e)

    def _inject_intraday_signals(self, enriched: pl.DataFrame, engine, asset_type: str) -> pl.DataFrame:
        """每分钟为分时信号规则批量获取一次数据并注入临时布尔列。"""
        get_symbols = getattr(engine, "intraday_signal_symbols", None)
        if not callable(get_symbols):
            return enriched
        symbols = get_symbols(asset_type)
        if not symbols:
            return enriched

        now = cn_now()
        bucket = now.strftime("%Y%m%d%H%M")
        if self._intraday_signal_bucket.get(asset_type) == bucket:
            return self._intraday_signal_evaluator.inject(enriched, [])
        self._intraday_signal_bucket[asset_type] = bucket

        from app.services.kline_sync import (
            fetch_intraday_monitor_batch,
            intraday_monitor_support,
        )

        capset = getattr(self._app_state, "capabilities", None)
        support = intraday_monitor_support(capset)
        if not support["available"] or len(symbols) > int(support["max_symbols"]):
            return self._intraday_signal_evaluator.inject(enriched, [])

        minute_df = fetch_intraday_monitor_batch(sorted(symbols), capset, now=now)
        prev_close: dict[str, float] = {}
        available_cols = set(enriched.columns)
        for row in enriched.filter(pl.col("symbol").is_in(sorted(symbols))).iter_rows(named=True):
            symbol = str(row.get("symbol") or "")
            reference = row.get("prev_close") if "prev_close" in available_cols else None
            if reference is None and "close" in available_cols and "change_pct" in available_cols:
                close = row.get("close")
                change_pct = row.get("change_pct")
                if close is not None and change_pct is not None and float(change_pct) > -1:
                    reference = float(close) / (1.0 + float(change_pct))
            if symbol and reference is not None:
                prev_close[symbol] = float(reference)

        signals = self._intraday_signal_evaluator.evaluate(
            minute_df,
            symbols=symbols,
            prev_close=prev_close,
            asset_type=asset_type,
            now=now,
        )
        return self._intraday_signal_evaluator.inject(enriched, signals)

    def _record_minute_bars(self, records: list[dict]) -> None:
        """Fold live quotes into minute bars — Taiwan has no first-party minute feed.

        Snapshot rows are skipped: the official endpoint publishes the previous
        close during the session, which would be written as a flat bar at the wrong
        price for every minute the realtime source is unavailable.
        """
        live = [r for r in records if r.get("status") in _LIVE_RECORD_STATUSES]
        if not live:
            return
        try:
            from app.taiwan.realtime.minute_recorder import get_minute_recorder
            resolver = getattr(self._repo, "resolve_asset_type", None) if self._repo else None
            get_minute_recorder().record(live, resolve_asset_type=resolver)
        except Exception as e:  # noqa: BLE001
            logger.debug("minute bar recording skipped: %s", e)

    def _flush_minute_bars(self) -> None:
        """Persist the bar still open at the close; nothing rolls it over afterwards."""
        try:
            from app.taiwan.realtime.minute_recorder import get_minute_recorder
            resolver = getattr(self._repo, "resolve_asset_type", None) if self._repo else None
            get_minute_recorder().flush(resolve_asset_type=resolver)
        except Exception as e:  # noqa: BLE001
            logger.warning("minute bar flush failed: %s", e)

    def _maybe_send_webhook(self, rule_events: list[dict], engine) -> None:
        """把告警推送到外部 IM (由规则 webhook_channels 指定渠道)。

        - LINE / Telegram 任一已配置即生效 (两个都没配才跳过)
        - 仅推送 webhook_channels 非空的规则触发的告警, 且只投递被勾选的渠道
        - 失败静默, 不阻断主流程
        - 去重: 复用 MonitorRuleEngine 的 cooldown, 此处不重复去重

        注意: 用 rule_events (含 rule_id) 而非重建后的 all_alerts,
        以便反查引擎规则判断是否启用推送。
        """
        try:
            from app.services import preferences
            from app.services import webhook_adapter

            line_token = preferences.get_line_channel_access_token()
            line_target = preferences.get_line_target_id()
            telegram_token = preferences.get_telegram_bot_token()
            telegram_chat = preferences.get_telegram_chat_id()
            if not (line_token and line_target) and not (telegram_token and telegram_chat):
                return

            # 反查规则, 过滤出启用推送的事件
            source_labels = {
                "strategy": "策略", "signal": "訊號",
                "price": "價格", "market": "異動",
                "sector": "板塊",
            }
            rules = engine.rules if engine is not None else {}
            enqueued = 0
            for ev in rule_events:
                rule = rules.get(ev.get("rule_id"))
                # webhook_channels 指定命中的渠道 (['line'] / ['telegram'] / 两者 / []).
                # 空列表 = 该规则不推送。仅推送「渠道已选 + 对应地址已配置」的组合。
                channels = rule.get("webhook_channels") if rule else None
                if not channels:
                    continue
                source = ev.get("source", "")
                source_label = source_labels.get(source, source or "通知")
                symbol = ev.get("symbol") or ""
                name = ev.get("name") or ""
                message = ev.get("message") or ""
                title = source_label
                body = f"{symbol} {name} {message}".strip() if symbol else (message or name)
                # 提交到独立线程池, 不阻塞行情轮询线程 (webhook 慢/重试不拖累实时行情+告警)。
                # 按渠道独立投递: LINE / Telegram 谁被勾选且已配置就推谁。
                # 应用内 alerts.jsonl 记录与 SSE 已在前面完成, 不依赖 webhook 成败,
                # 失败由 webhook_adapter 记 WARNING(可见)。
                if line_token and line_target and "line" in channels:
                    _WEBHOOK_EXECUTOR.submit(webhook_adapter.send_line, line_token, line_target, title, body)
                    enqueued += 1
                if telegram_token and telegram_chat and "telegram" in channels:
                    _WEBHOOK_EXECUTOR.submit(webhook_adapter.send_telegram, telegram_token, telegram_chat, title, body)
                    enqueued += 1
            if enqueued:
                logger.info("Webhook 已提交 %d 条 (异步投递, 按渠道独立投递, 失败记 WARNING)", enqueued)
        except Exception as e:  # noqa: BLE001
            logger.warning("Webhook 提交异常 (不影响告警主流程): %s", e)

    def _maybe_send_system_notifications(self, all_alerts: list[dict]) -> None:
        """把告警转发到操作系统通知中心 (由 preferences 开关控制)。

        - 开关关闭: 直接返回
        - 开关开启: 逐条发系统通知; 失败静默, 不阻断主流程
        - 去重: 复用 MonitorRuleEngine 的 cooldown, 此处不重复去重
        """
        try:
            from app.services import preferences
            from app.services import notify_adapter

            if not preferences.get_system_notify_enabled():
                return

            for ev in all_alerts:
                # 通知标题: 用 source 分类 (策略/信号/价格/异动)
                source = ev.get("source", "")
                source_label = {
                    "strategy": "策略", "signal": "訊號",
                    "price": "價格", "market": "異動", "sector": "板塊",
                }.get(source, source or "通知")

                name = ev.get("name") or ""
                symbol = ev.get("symbol") or ""
                message = ev.get("message") or ""

                # 正文: 优先用现成 message, 拼上 symbol/name 让用户一眼定位
                if symbol:
                    body = f"{symbol} {name} {message}".strip()
                else:
                    body = message or name

                title = f"TickFlow · {source_label}"
                notify_adapter.notify(title, body)
        except Exception as e:  # noqa: BLE001
            logger.debug("系统通知发送异常 (不影响告警主流程): %s", e)

    @staticmethod
    def _get_strategy_monitor():
        """获取 StrategyMonitorService — 不再使用, 改用 _app_state 注入。"""
        return None

    # ================================================================
    # enriched 增量计算
    # ================================================================

    def _enrich_today_incremental(
        self,
        daily_df: pl.DataFrame,
        quote_extra: pl.DataFrame | None,
        live_agg: pl.DataFrame,
        prev_enriched: pl.DataFrame,
    ) -> pl.DataFrame:
        """Today's enriched rows: Taiwan ones from the publish pipeline, others from yesterday's recursive state."""
        from app.indicators.pipeline import compute_enriched_today
        if daily_df.is_empty():
            return pl.DataFrame()
        taiwan_today = _taiwan_live_rows(
            self._repo, daily_df.filter(_is_taiwan_symbol()), quote_extra
        )
        daily_df = daily_df.filter(~_is_taiwan_symbol())
        if daily_df.is_empty():
            return taiwan_today
        instruments = self._repo.get_instruments()
        today_ohlcv = daily_df
        if quote_extra is not None and not quote_extra.is_empty():
            today_ohlcv = daily_df.join(quote_extra, on="symbol", how="left")
        # The only session clock here is Taiwan's, which does not fit other markets' volume.
        enriched = compute_enriched_today(
            live_agg=live_agg,
            prev_enriched=prev_enriched,
            today_ohlcv=today_ohlcv,
            instruments=instruments,
            session_progress=None,
        )
        frames = [frame for frame in (taiwan_today, enriched) if not frame.is_empty()]
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def _flush_live_enriched(self, daily_df: pl.DataFrame, quote_extra: pl.DataFrame = None, asset_type: str = "stock", merge: bool = False) -> None:
        """增量计算今天的 enriched: 用昨天的递推状态 + 今天 OHLCV → 只算今天 5500 行。

        quote_extra: API 直接提供的补充字段 (prev_close, change_pct 等),
                     不写 daily, 直接传给 compute_enriched_today 避免重复计算。
        Taiwan stock rows come from the publish pipeline instead and ignore quote_extra.
        """
        try:
            today = cn_today()
            t0 = time.perf_counter()

            taiwan_today = pl.DataFrame()
            if asset_type == "stock" and not daily_df.is_empty():
                taiwan_today = _taiwan_live_rows(
                    self._repo, daily_df.filter(_is_taiwan_symbol()), quote_extra
                )
                daily_df = daily_df.filter(~_is_taiwan_symbol())
            enriched_today, use_incremental = pl.DataFrame(), False
            if not daily_df.is_empty():
                enriched_today, use_incremental = self._enrich_live_rows(daily_df, quote_extra, asset_type, today)
            mode_label = "增量" if use_incremental else "全量"
            if enriched_today.is_empty():
                mode_label = "taiwan"
            elif not taiwan_today.is_empty():
                mode_label += "+taiwan"
            frames = [frame for frame in (taiwan_today, enriched_today) if not frame.is_empty()]
            if not frames:
                return
            enriched_today = pl.concat(frames, how="diagonal_relaxed")

            # ---- 写盘 + 更新缓存 ----
            if merge:
                self._repo.merge_live_enriched_asset(asset_type, enriched_today)
            else:
                self._repo.flush_live_enriched_asset(asset_type, enriched_today)

            elapsed = time.perf_counter() - t0
            logger.info("enriched %s: %d 只, %s, 耗时 %.0fms",
                        mode_label, len(enriched_today), today, elapsed * 1000)
        except Exception as e:  # noqa: BLE001
            logger.warning("enriched 计算失败: %s", e)

    def _enrich_live_rows(
        self, daily_df: pl.DataFrame, quote_extra: pl.DataFrame | None, asset_type: str, today: date
    ) -> tuple[pl.DataFrame, bool]:
        """Today's rows of *daily_df*, Taiwan stocks excluded, and whether the incremental path made them."""
        # ---- 尝试增量路径 ----
        live_agg = self._repo.get_live_agg() if asset_type == "stock" else pl.DataFrame()
        prev_enriched, prev_date = (
            self._repo.get_enriched_latest()
            if asset_type == "stock"
            else self._repo.get_enriched_latest_asset(asset_type)
        )

        use_incremental = (
            asset_type == "stock"
            and not live_agg.is_empty()
            and not prev_enriched.is_empty()
            and prev_date is not None
        )

        if use_incremental:
            enriched_today = self._enrich_today_incremental(daily_df, quote_extra, live_agg, prev_enriched)
            if enriched_today.is_empty():
                logger.warning("增量计算结果为空, 回退到全量计算")
                use_incremental = False

        # ---- 全量回退路径 ----
        if not use_incremental:
            from datetime import timedelta
            from app.indicators.pipeline import compute_enriched

            # kline_daily holds no Taiwan history: Taiwan etf/index rows have no rebuild path here.
            non_taiwan = daily_df.filter(~_is_taiwan_symbol())
            if non_taiwan.is_empty():
                return pl.DataFrame(), False
            daily_df = non_taiwan

            logger.info("enriched 全量计算 (live_agg=%s, 上次日期=%s)",
                        "ok" if not live_agg.is_empty() else "空", prev_date)

            cutoff = today - timedelta(days=90)
            table = {"etf": "kline_etf_daily", "index": "kline_index_daily"}.get(asset_type, "kline_daily")
            daily_glob = str(self._repo.store.data_dir / table / "**" / "*.parquet")
            ohlcv_cols = ["symbol", "date", "open", "high", "low", "close", "volume", "amount", "quote_ts"]
            hist_df = (
                scan_daily_parquet(daily_glob)
                .filter(pl.col("date") >= cutoff)
                .sort(["symbol", "date"])
                .collect()
            )
            if hist_df.is_empty():
                return pl.DataFrame(), False

            hist_cols = [c for c in ohlcv_cols if c in hist_df.columns]
            hist_df = hist_df.select(hist_cols).filter(pl.col("date") != today)
            daily_ohlcv = daily_df.select([c for c in ohlcv_cols if c in daily_df.columns])
            full_df = pl.concat([hist_df, daily_ohlcv], how="diagonal_relaxed")
            full_df = full_df.sort(["symbol", "date"])

            factor_dir = {"stock": "adj_factor", "etf": "adj_factor_etf"}.get(asset_type)
            factor_path = self._repo.store.data_dir / factor_dir / "all.parquet" if factor_dir else None
            factors = pl.DataFrame()
            if factor_path and factor_path.exists():
                try:
                    factors = pl.read_parquet(factor_path)
                except Exception:
                    pass
            instruments = self._repo.get_instruments() if asset_type == "stock" else None

            enriched_full = compute_enriched(
                full_df,
                factors=factors,
                instruments=instruments,
                historical_shares=(
                    self._repo.get_historical_shares()
                    if asset_type == "stock"
                    else None
                ),
            )
            enriched_today = enriched_full.filter(pl.col("date") == today)
        return enriched_today, use_incremental
