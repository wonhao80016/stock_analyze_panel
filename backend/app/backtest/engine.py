"""回测引擎 — 共享数据加载 + 撮合 + 统计计算。

纯 Polars/NumPy 实现，不依赖 pandas。
"""
from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

import numpy as np
import polars as pl
import pyarrow as pa

from app.backtest.matrix import (
    MarketDataMatrix,
    MarketMatrix,
    MatrixCacheProfile,
    build_market_matrix,
    load_market_data_matrix_from_parquet,
)
from app.config import settings
from app.enriched_generation import EnrichedGenerationUnavailableError
from app.parquet import scan_enriched_parquet
from app.tickflow.repository import KlineRepository

logger = logging.getLogger(__name__)

_REFERENCE_LIMIT_COLUMNS = frozenset({"ref_prev_close", "ref_limit_up", "ref_limit_down"})

STATS_METHODOLOGY_VERSION = "stats_v3"
# Annual return, Sharpe, Sortino and Calmar are null for shorter series.
MIN_ANNUALIZED_SESSIONS = 60
ANNUALIZED_STATS = frozenset({"annual_return", "sharpe", "sortino", "calmar"})
RISK_FREE_RATE_MIN = -0.05
RISK_FREE_RATE_MAX = 0.2
_DAYS_PER_YEAR = 365.25
# Return std at or below this is float noise from equity ratios, treated as zero volatility.
_FLAT_RETURN_STD = 1e-12


def validate_risk_free_rate(rate: float) -> float:
    """Return the annual decimal risk-free rate, or raise ValueError outside [-5%, 20%]."""
    value = float(rate)
    if not math.isfinite(value) or not RISK_FREE_RATE_MIN <= value <= RISK_FREE_RATE_MAX:
        raise ValueError(
            "risk_free_rate must be an annual decimal rate between "
            f"{RISK_FREE_RATE_MIN} and {RISK_FREE_RATE_MAX}"
        )
    return value


def _matrix_entry_score(matrix: MarketMatrix, time_id: int, asset_id: int) -> float:
    source_time = int(matrix.entry_signal_time[time_id, asset_id])
    if source_time < 0:
        return 0.0
    return float(matrix.score[source_time, asset_id])


def _traded_price(matrix: MarketMatrix, time_id: int, asset_id: int, adjusted: float) -> float:
    scale = float(matrix.raw_scale[time_id, asset_id])
    if scale == 1.0:
        return adjusted
    # Taiwan ticks are multiples of 0.01; rescaling a float32 bar only adds noise.
    return round(adjusted * scale, 2)


# Relative error of float32 adjusted prices times raw_scale; a line this close to a tick is on it.
_TRIGGER_TICK_TOLERANCE = 1e-6


def _trigger_fill(
    config: MatcherConfig,
    matrix: MarketMatrix,
    time_id: int,
    asset_id: int,
    line: float,
    mode: Literal["floor", "ceil"],
) -> float:
    """Adjusted fill of an intrabar trigger at ``line``, on the tick grid of the bar's raw price.

    Sell stops floor and take-profit limits ceil; the fill never leaves the bar's raw low/high.
    """
    tick_class = (config.tick_class_by_symbol or {}).get(matrix.symbols[asset_id])
    scale = float(matrix.raw_scale[time_id, asset_id])
    # An unusable scale is rejected later as sell_invalid_price.
    if tick_class is None or not (np.isfinite(scale) and scale > 0):
        return line
    from app.taiwan.market_rules import TickSizeModel

    nudge = 1 + _TRIGGER_TICK_TOLERANCE if mode == "floor" else 1 - _TRIGGER_TICK_TOLERANCE
    raw_fill = TickSizeModel.round_order_price(
        line * scale * nudge,
        tick_class=tick_class,
        mode=mode,
        trade_date=date.fromisoformat(matrix.timestamp_labels[time_id][:10]),
    )
    if mode == "floor":
        raw_fill = max(raw_fill, round(float(matrix.low[time_id, asset_id]) * scale, 2))
    else:
        raw_fill = min(raw_fill, round(float(matrix.high[time_id, asset_id]) * scale, 2))
    return raw_fill / scale


# ================================================================
# 数据结构
# ================================================================

@dataclass
class MatcherConfig:
    # matching 为向后兼容入口: 仅传 matching 时, entry_fill/exit_fill 都取 matching 的值。
    # 显式传入 entry_fill/exit_fill 时以二者为准 (允许建仓/清仓口径不同)。
    matching: Literal["close_t", "open_t+1"] = "close_t"
    entry_fill: Literal["close_t", "open_t+1"] | None = None
    exit_fill: Literal["close_t", "open_t+1", "signal_next_minute"] | None = None
    # 成本模型: 优先使用拆分口径 (佣金双边 + 印花税仅卖出 + 滑点双边)。
    # 未设 commission_pct 时回退到 fees_pct 作为双边佣金 (向后兼容, 无印花税)。
    fees_pct: float = 0.0002
    commission_pct: float | None = None
    commission_discount: float = 1.0
    minimum_commission: float = 0.0
    stamp_tax_pct: float | None = None
    slippage_bps: float = 5.0
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None
    trailing_take_profit_activate_pct: float | None = None
    trailing_take_profit_drawdown_pct: float | None = None
    max_hold_days: int | None = None
    minimum_holding_sessions: int = 1
    max_positions: int = 10
    max_exposure_pct: float = 1.0
    score_min: float | None = None
    score_max: float | None = None
    initial_capital: float = 1_000_000.0
    position_sizing: Literal["equal", "score_weight"] = "equal"
    # 分钟K精确成交: 开启后, 信号触发日的成交价用当日分钟K优化
    # (有参考线→穿越价, 无参考线→VWAP)。数据缺失时降级为日K口径。
    minute_fill: bool = False
    asset_type: str = "stock"
    lot_mode: Literal["board_lot", "odd_lot"] = "board_lot"
    market_profile: Any | None = None
    tax_class_by_symbol: Mapping[str, Any] | None = None
    lot_size_by_symbol: Mapping[str, int] | None = None
    day_trade_eligible_symbols: tuple[str, ...] = ()
    day_trade_eligible_by_symbol: Mapping[str, bool] | None = None
    # symbol -> ((ISO event date, volume_factor), ...) for splits, par changes and reductions.
    share_events_by_symbol: Mapping[str, tuple[tuple[str, float], ...]] | None = None
    # Symbols listed here have intrabar trigger fills rounded to their exchange tick grid.
    tick_class_by_symbol: Mapping[str, Any] | None = None
    # Annual decimal rate; affects statistics only, never fills.
    risk_free_rate: float = 0.0

    def __post_init__(self) -> None:
        # 解析最终口径: 优先 entry_fill/exit_fill, 否则回退到 matching (向后兼容)。
        if self.entry_fill is None:
            self.entry_fill = self.matching
        if self.exit_fill is None:
            self.exit_fill = self.matching
        self.risk_free_rate = validate_risk_free_rate(self.risk_free_rate)
        if self.minimum_holding_sessions < 0:
            raise ValueError("minimum_holding_sessions must be non-negative")
        if self.minimum_commission < 0:
            raise ValueError("minimum_commission must be non-negative")
        if not 0 <= self.commission_discount <= 1:
            raise ValueError("commission_discount must be between 0 and 1")
        if self.lot_mode not in {"board_lot", "odd_lot"}:
            raise ValueError("lot_mode must be board_lot or odd_lot")

    def _commission_rate(self) -> float:
        if self.market_profile is not None and hasattr(self.market_profile, "cost"):
            return self.market_profile.cost.commission_rate
        return self.commission_pct if self.commission_pct is not None else self.fees_pct

    def _commission_pct(self) -> float:
        if self.market_profile is not None and hasattr(self.market_profile, "cost"):
            return self._commission_rate() * self.market_profile.cost.discount
        return self._commission_rate() * self.commission_discount

    def buy_cost_pct(self) -> float:
        # 买入腿: 佣金 + 滑点。
        return self._commission_pct() + self.slippage_bps / 10000.0

    def lot_size(self, symbol: str) -> int:
        if (
            self.market_profile is not None
            and hasattr(self.market_profile, "lot")
            and getattr(self.market_profile.lot, "mode", None) == "odd_lot"
        ):
            return int(self.market_profile.lot.lot_size)
        if self.lot_size_by_symbol is not None and symbol in self.lot_size_by_symbol:
            return max(int(self.lot_size_by_symbol[symbol]), 1)
        if self.market_profile is not None and hasattr(self.market_profile, "lot"):
            return max(int(self.market_profile.lot.lot_size), 1)
        return 100

    def buy_transaction_cost(self, trade_value: float) -> float:
        if trade_value <= 0:
            return 0.0
        if self.market_profile is not None and hasattr(self.market_profile, "cost"):
            commission = float(self.market_profile.cost.calc_commission(trade_value))
        else:
            commission = trade_value * self._commission_pct()
        return commission + trade_value * self.slippage_bps / 10000.0

    def sell_transaction_cost(
        self,
        trade_value: float,
        symbol: str,
        trade_date: date | str | None,
        is_day_trade: bool = False,
    ) -> float:
        if trade_value <= 0:
            return 0.0
        if self.market_profile is not None and hasattr(self.market_profile, "cost"):
            commission = float(self.market_profile.cost.calc_commission(trade_value))
        else:
            commission = trade_value * self._commission_pct()
        if self.stamp_tax_pct is not None:
            tax = trade_value * self.stamp_tax_pct
        elif (
            self.market_profile is not None
            and hasattr(self.market_profile, "tax")
            and self.tax_class_by_symbol is not None
            and symbol in self.tax_class_by_symbol
        ):
            parsed_date = date.fromisoformat(trade_date[:10]) if isinstance(trade_date, str) else trade_date
            tax_class = self.tax_class_by_symbol[symbol]
            eligible_day_trade = bool(
                is_day_trade
                and self.day_trade_eligible_by_symbol is not None
                and self.day_trade_eligible_by_symbol.get(symbol, False)
                and getattr(tax_class, "value", tax_class) == "ordinary_stock"
            )
            tax = float(self.market_profile.tax.calc_tax(
                trade_value,
                tax_class=tax_class,
                is_day_trade=eligible_day_trade,
                trade_date=parsed_date,
            ))
        else:
            tax = 0.0
        return commission + tax + trade_value * self.slippage_bps / 10000.0

    def can_exit_after_sessions(self, symbol: str, holding_sessions: int) -> bool:
        if holding_sessions < self.minimum_holding_sessions:
            return False
        if holding_sessions > 0:
            return True
        eligible = bool(
            symbol in self.day_trade_eligible_symbols
            or (
                self.day_trade_eligible_by_symbol is not None
                and self.day_trade_eligible_by_symbol.get(symbol, False)
            )
        )
        if not eligible:
            return False
        if self.market_profile is None:
            return True
        return bool(self.market_profile.can_same_day_exit(eligible))

    def held_share_factor(self, symbol: str, entry_date: str, exit_date: str) -> float:
        """Shares held at exit per share bought: events after the entry session, up to the exit."""
        factor = 1.0
        for event_date, volume_factor in (self.share_events_by_symbol or {}).get(symbol, ()):
            if entry_date < event_date <= exit_date:
                factor *= volume_factor
        return factor

    def align_shares(self, allocation: float, price: float, symbol: str) -> float:
        if allocation <= 0 or price <= 0:
            return 0.0
        lot_size = self.lot_size(symbol)
        estimated = int(allocation / (price * (1 + self.buy_cost_pct())) // lot_size) * lot_size
        while estimated > 0:
            trade_value = estimated * price
            if trade_value + self.buy_transaction_cost(trade_value) <= allocation + 1e-9:
                break
            estimated -= lot_size
        return float(estimated)



@dataclass
class TradeRecord:
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    pnl_pct: float
    duration: int
    exit_reason: str  # "signal" | "stop_loss" | "take_profit" | "trailing_stop" | "trailing_take_profit" | "max_hold" | "end"
    # 退出优先级 (高→低): pending_exit(历史挂单) > 风控(止损/移动止损/移动止盈) > signal(卖点) > max_hold(到期) > end
    name: str = ""
    shares: float = 0.0
    lots: float = 0.0
    position_pct: float = 0.0
    entry_value: float = 0.0
    exit_value: float = 0.0
    pnl_amount: float = 0.0
    entry_score: float | None = None
    entry_signal_date: date | str | None = None
    exit_signal_date: date | str | None = None
    blocked_exit_days: int = 0
    # 触发买入/卖出的具体信号列名 (如 signal_ma_golden_5_20 / csg_xxx);
    # 仅当该腿由信号触发时填充, 止损/止盈/到期等非信号退出时 exit_signal_id 为 None。
    entry_signal_id: str | None = None
    exit_signal_id: str | None = None
    # entry_price/exit_price and shares are as traded; *_adj are on the run's forward-adjusted scale.
    entry_price_adj: float | None = None
    exit_price_adj: float | None = None
    exit_shares: float | None = None


@dataclass
class SimResult:
    equity_curve: list[dict]       # [{date, value}]
    drawdown_curve: list[dict]     # [{date, value}]
    trades: list[TradeRecord]
    per_symbol_stats: list[dict]
    stats: dict


@dataclass(frozen=True)
class SimulationOptions:
    """Controls expensive result materialization without changing matching semantics."""

    include_monte_carlo: bool = True
    include_curves: bool = True
    include_trades: bool = True
    include_per_symbol_stats: bool = True
    include_return_distribution: bool = True
    min_annualized_sessions: int = MIN_ANNUALIZED_SESSIONS


# ================================================================
# PanelCache — 避免重复 scan_parquet + compute_all
# ================================================================

class _CacheEntry:
    __slots__ = ("df", "ts")

    def __init__(self, df: pl.DataFrame, ts: float):
        self.df = df
        self.ts = ts


class _InFlight:
    """同 key 正在计算的占位: leader 算完通过 done 唤醒所有跟随者复用结果。"""

    __slots__ = ("done", "df", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.df: pl.DataFrame | None = None
        self.error: BaseException | None = None


class PanelCache:
    """LRU + TTL 数据面板缓存。"""

    def __init__(self, max_size: int = 2, ttl_seconds: int = 180):
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds
        # 跨请求单例, SSE 回测在各自 daemon 线程并发访问 OrderedDict。
        # 无锁的 move_to_end/del/popitem check-then-act 会抛 "OrderedDict mutated"。
        # 用实例锁守护所有 OrderedDict 变更; compute_fn (重扫盘) 放锁外避免串行化。
        self._lock = threading.Lock()
        # single-flight: 同 key 只让一个线程 compute, 其余等其结果复用。
        # 否则优化器/walk-forward 的 max_workers 个线程冷启动同时 miss, 会并行加载 N 份同一面板。
        self._inflight: dict[str, _InFlight] = {}
        # 轻量遥测: 累加真实扫盘耗时与命中/复用次数, 用于量化 IO 占比 (是否值得进一步优化)。
        # compute_seconds 只计 leader 的实际 compute_fn 耗时, follower 复用不计 —— 反映真实 IO。
        self._compute_seconds = 0.0
        self._compute_count = 0   # 实际扫盘次数
        self._hit_count = 0       # 缓存命中 (未扫盘) 次数
        self._reuse_count = 0     # single-flight 跟随者复用次数

    def get_or_compute(
        self,
        symbols: list[str] | None,
        start: date,
        end: date,
        columns: list[str] | None,
        compute_fn,
        asset_type: str = "stock",
        generation: str | None = None,
    ) -> pl.DataFrame:
        key = self._make_key(
            symbols, start, end, columns, asset_type, generation
        )
        now = time.monotonic()

        with self._lock:
            entry = self._cache.get(key)
            if entry is not None:
                if now - entry.ts < self._ttl:
                    self._cache.move_to_end(key)
                    self._hit_count += 1
                    return entry.df
                del self._cache[key]  # 过期, 丢弃后重算
            # single-flight: 同 key 若已有线程在算, 登记为跟随者; 否则本线程当 leader。
            flight = self._inflight.get(key)
            leader = flight is None
            if leader:
                flight = _InFlight()
                self._inflight[key] = flight

        if not leader:
            # 跟随者: 等 leader 算完直接复用, 不重复 compute (消除缓存踩踏)。
            flight.done.wait()
            with self._lock:
                self._reuse_count += 1
            if flight.error is not None:
                raise flight.error
            return flight.df

        # leader: compute 放锁外 (不同 key 仍可并发, 保留原设计优点)。
        t_compute = time.perf_counter()
        try:
            df = compute_fn(symbols, start, end, columns, asset_type)
        except BaseException as e:
            # 失败不缓存: 摘除 inflight 让后续线程重试, 并把异常透传给已在等的跟随者。
            with self._lock:
                self._compute_seconds += time.perf_counter() - t_compute  # 失败也花了 IO, 计入
                self._compute_count += 1
                self._inflight.pop(key, None)
            flight.error = e
            flight.done.set()
            raise
        with self._lock:
            self._compute_seconds += time.perf_counter() - t_compute
            self._compute_count += 1
            self._cache[key] = _CacheEntry(df=df, ts=now)
            if len(self._cache) > self._max_size:
                self._cache.popitem(last=False)
            self._inflight.pop(key, None)
        flight.df = df
        flight.done.set()
        return df

    def stats(self) -> dict:
        """遥测快照: 累计扫盘耗时/次数与命中/复用次数。首尾快照取差即区间内 IO 开销。"""
        with self._lock:
            return {
                "compute_seconds": round(self._compute_seconds, 4),
                "compute_count": self._compute_count,
                "hit_count": self._hit_count,
                "reuse_count": self._reuse_count,
            }

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    @staticmethod
    def _make_key(
        symbols: list[str] | None,
        start: date,
        end: date,
        columns: list[str] | None,
        asset_type: str = "stock",
        generation: str | None = None,
    ) -> str:
        if symbols is None:
            h = "all"
        else:
            h = hashlib.md5(",".join(sorted(symbols)).encode()).hexdigest()[:12]
        cols = "all" if columns is None else hashlib.md5(",".join(sorted(columns)).encode()).hexdigest()[:8]
        return f"{asset_type}:{generation or 'unmanaged'}:{h}:{start}:{end}:{cols}"


# ================================================================
# BacktestEngine
# ================================================================


@dataclass(frozen=True)
class _Annualization:
    """Calendar annualization of a per-session series: P = return periods / (calendar days / 365.25)."""

    sessions: int
    return_periods: int
    calendar_days: int
    periods_per_year: float | None
    risk_free_rate: float
    risk_free_rate_per_period: float | None
    min_sessions: int
    unavailable_reason: str | None

    @classmethod
    def from_sessions(
        cls,
        session_dates: Sequence[str],
        risk_free_rate: float,
        min_sessions: int,
    ) -> _Annualization:
        sessions = len(session_dates)
        return_periods = max(sessions - 1, 0)
        calendar_days = (
            (date.fromisoformat(str(session_dates[-1])[:10])
             - date.fromisoformat(str(session_dates[0])[:10])).days
            if sessions
            else 0
        )
        periods_per_year = (
            return_periods / (calendar_days / _DAYS_PER_YEAR)
            if return_periods > 0 and calendar_days > 0
            else None
        )
        if sessions < min_sessions:
            reason = f"fewer_than_{min_sessions}_sessions"
        elif periods_per_year is None:
            reason = "no_calendar_span"
        else:
            reason = None
        return cls(
            sessions=sessions,
            return_periods=return_periods,
            calendar_days=calendar_days,
            periods_per_year=periods_per_year,
            risk_free_rate=risk_free_rate,
            risk_free_rate_per_period=(
                (1 + risk_free_rate) ** (1 / periods_per_year) - 1
                if periods_per_year
                else None
            ),
            min_sessions=min_sessions,
            unavailable_reason=reason,
        )

    def sharpe(self, returns: np.ndarray) -> float | None:
        if self.unavailable_reason is not None:
            return None
        if len(returns) < 2 or np.std(returns) <= _FLAT_RETURN_STD:
            return 0.0
        excess = returns - self.risk_free_rate_per_period
        return float(np.mean(excess) / np.std(returns) * np.sqrt(self.periods_per_year))

    def sortino(self, returns: np.ndarray) -> float | None:
        if self.unavailable_reason is not None:
            return None
        return BacktestEngine._sortino_ratio(
            returns,
            self.periods_per_year,
            mar=self.risk_free_rate_per_period,
        )

    def methodology(self, *, profit_factor_basis: str, monte_carlo_basis: str) -> dict:
        return {
            "version": STATS_METHODOLOGY_VERSION,
            "annualization": "calendar_cagr",
            "sessions": self.sessions,
            "return_periods": self.return_periods,
            "calendar_days": self.calendar_days,
            "periods_per_year": (
                round(self.periods_per_year, 4) if self.periods_per_year is not None else None
            ),
            "min_annualized_sessions": self.min_sessions,
            "annualization_unavailable_reason": self.unavailable_reason,
            "risk_free_rate": self.risk_free_rate,
            "risk_free_rate_per_period": self.risk_free_rate_per_period,
            "profit_factor_basis": profit_factor_basis,
            "monte_carlo_basis": monte_carlo_basis,
        }


def _gross_profit_factor(wins, losses) -> float | None:
    """Gross profit / gross loss.

    Not avg_win/avg_loss: that is the payoff ratio, which omits the win-rate term
    and reads above 1.0 for a strategy whose many small losses outweigh its few
    large wins. Exposed separately as payoff_ratio.
    """
    gross_loss = abs(float(np.sum(losses)))
    if gross_loss <= 0:
        return None
    return float(np.sum(wins)) / gross_loss


class BacktestEngine:
    """回测引擎 — 数据加载 + 撮合模拟 + 统计计算。"""

    def __init__(self, repo: KlineRepository) -> None:
        self.repo = repo
        self._cache = PanelCache()

    # ── 数据加载 ──────────────────────────────────────

    def data_generation(self, asset_type: str = "stock") -> str | None:
        loader = getattr(self.repo, "get_matrix_data_generation", None)
        return loader(asset_type) if callable(loader) else None

    def assert_data_generation(
        self,
        asset_type: str,
        expected: str | None,
    ) -> None:
        if expected is None:
            return
        current = self.data_generation(asset_type)
        if current != expected:
            raise EnrichedGenerationUnavailableError(
                "enriched data changed while the snapshot was being read"
            )

    def load_panel(
        self,
        symbols: list[str] | None,
        start: date,
        end: date,
        columns: list[str] | None = None,
        asset_type: str = "stock",
        *,
        expected_generation: str | None = None,
    ) -> pl.DataFrame:
        """加载 enriched 数据面板，带缓存。asset_type='etf' 时读 ETF enriched。"""
        attempts = 1 if expected_generation is not None else 2
        for attempt in range(attempts):
            generation = (
                expected_generation
                if expected_generation is not None
                else self.data_generation(asset_type)
            )
            panel = self._cache.get_or_compute(
                symbols,
                start,
                end,
                columns,
                self._load_panel_inner,
                asset_type=asset_type,
                generation=generation,
            )
            try:
                self.assert_data_generation(asset_type, generation)
            except EnrichedGenerationUnavailableError:
                if attempt + 1 >= attempts:
                    raise
                continue
            return panel
        raise EnrichedGenerationUnavailableError(
            "unable to read a stable enriched data snapshot"
        )

    def load_panel_for_backtest(
        self,
        symbols: list[str] | None,
        start: date,
        end: date,
        feature_plan,
        asset_type: str = "stock",
    ) -> pl.DataFrame:
        """按解析后的依赖加载窄基础列并计算回测所需特征。"""
        from app.indicators.pipeline import (
            compute_indicators,
            compute_limit_signals,
            compute_signals,
        )

        columns = set(feature_plan.base_columns)
        if "raw_close" in columns:
            # Optional: only Taiwan partitions carry the exchange's own corporate-action limits.
            columns |= _REFERENCE_LIMIT_COLUMNS
        df = self.load_panel(
            symbols,
            start,
            end,
            columns=sorted(columns),
            asset_type=asset_type,
        )
        if df.is_empty():
            return df

        from app.backtest.fundamentals import (
            attach_fundamental_factors,
            load_fundamental_snapshot,
        )

        fundamental_names = sorted(
            getattr(feature_plan, "fundamental_columns", frozenset())
            or frozenset()
        )
        if fundamental_names:
            # 财务因子列不落 enriched 存储, 在加载口按公告日门控并入。
            df = attach_fundamental_factors(
                df,
                load_fundamental_snapshot(
                    self.repo.store.data_dir if self.repo is not None else None
                ),
                fundamental_names,
            )

        instruments = (
            self.repo.get_instruments_asset(asset_type)
            if self.repo is not None
            else pl.DataFrame()
        )
        matrix_native = feature_plan.execution_backend == "matrix_native"
        if not matrix_native:
            df = compute_indicators(df, needed=set(feature_plan.indicator_columns))
            df = compute_signals(df, needed=set(feature_plan.signal_columns))
        if not instruments.is_empty():
            df = compute_limit_signals(
                df,
                instruments,
                needed={"signal_limit_up", "signal_limit_down"}
                if matrix_native
                else set(feature_plan.signal_columns),
                historical_shares=(
                    self.repo.get_historical_shares()
                    if asset_type == "stock" and self.repo is not None
                    else None
                ),
            )
            join_cols = ["symbol"] if "symbol" in instruments.columns else []
            join_cols.extend(
                col for col in sorted(feature_plan.instrument_columns)
                if col in instruments.columns and col not in df.columns
            )
            if len(join_cols) > 1:
                df = df.join(
                    instruments.select(join_cols).unique(subset=["symbol"]),
                    on="symbol",
                    how="left",
                )

        required_columns = set(feature_plan.base_columns) | set(feature_plan.instrument_columns)
        required_columns |= set(feature_plan.signal_columns)
        if not matrix_native:
            required_columns |= set(feature_plan.indicator_columns)
        missing_columns = required_columns - set(df.columns)
        if missing_columns:
            raise ValueError(f"回测字段依赖未生成: {sorted(missing_columns)}")

        float_cols = [c for c in df.columns if df[c].dtype.is_float()]
        if float_cols:
            df = df.with_columns([
                pl.when(pl.col(c).is_nan() | pl.col(c).is_infinite())
                .then(None)
                .otherwise(pl.col(c))
                .alias(c)
                for c in float_cols
            ])
        return df

    def load_market_data_matrix_for_backtest(
        self,
        symbols: list[str] | None,
        start: date,
        end: date,
        feature_plan,
        asset_type: str = "stock",
        *,
        cache_profile: MatrixCacheProfile | None = None,
        coverage_start: date | None = None,
        coverage_end: date | None = None,
        expected_generation: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> MarketDataMatrix:
        """Load a matrix-native backtest directly from projected parquet batches."""
        if feature_plan.execution_backend != "matrix_native":
            raise ValueError("direct market matrix loading requires matrix_native backend")
        from app.tickflow.repository import enriched_dirname

        parquet_root = self.repo.store.data_dir / enriched_dirname(asset_type)
        instruments = self.repo.get_instruments_asset(asset_type)
        field_columns = (
            set(feature_plan.base_columns)
            | set(feature_plan.instrument_columns)
            | set(feature_plan.matrix_columns)
        )
        cache_root = (
            self.repo.store.data_dir / ".backtest_matrix_cache"
            if settings.backtest_matrix_disk_cache_enabled
            else None
        )
        cache_fields = (
            cache_profile.field_columns
            if cache_profile is not None
            else frozenset(field_columns)
        )
        cache_max_bytes = (
            cache_profile.max_disk_bytes
            if cache_profile is not None
            else settings.backtest_matrix_cache_max_mb * 1024 * 1024
        )
        generation_loader = getattr(self.repo, "get_matrix_data_generation", None)
        source_generation = (
            expected_generation
            if expected_generation is not None
            else (
                generation_loader(asset_type)
                if callable(generation_loader)
                else None
            )
        )
        attempts = 1 if expected_generation is not None else 2
        for attempt in range(attempts):
            try:
                market = load_market_data_matrix_from_parquet(
                    parquet_root,
                    start,
                    end,
                    field_columns=field_columns,
                    symbols=symbols,
                    instruments=instruments,
                    cache_root=cache_root,
                    coverage_start=coverage_start,
                    coverage_end=coverage_end,
                    cache_field_columns=cache_fields,
                    cache_max_bytes=cache_max_bytes,
                    profile_generation=(
                        cache_profile.generation if cache_profile is not None else "request"
                    ),
                    source_generation=source_generation,
                    cancel_event=cancel_event,
                )
                self.assert_data_generation(asset_type, source_generation)
                from app.backtest.fundamentals import attach_matrix_fundamental_fields

                fundamental_names = sorted(
                    getattr(feature_plan, "fundamental_columns", frozenset())
                    or frozenset()
                )
                if fundamental_names:
                    # 财务因子不落 enriched 存储: 矩阵加载后按公告日门控附加字段。
                    market = attach_matrix_fundamental_fields(
                        market,
                        self.repo.store.data_dir if self.repo is not None else None,
                        fundamental_names,
                    )
                return market
            except EnrichedGenerationUnavailableError:
                if attempt + 1 >= attempts:
                    raise
                source_generation = self.data_generation(asset_type)
            except pa.ArrowException as exc:
                raise ValueError(f"direct market matrix parquet scan failed: {exc}") from exc
        raise EnrichedGenerationUnavailableError(
            "unable to read a stable enriched matrix snapshot"
        )

    def cache_stats(self) -> dict:
        """暴露 PanelCache 遥测快照 (扫盘耗时/次数/命中/复用), 供上层量化 IO 占比。"""
        return self._cache.stats()

    def clear_panel_cache(self) -> None:
        self._cache.invalidate()

    def _load_panel_inner(
        self,
        symbols: list[str] | None,
        start: date,
        end: date,
        columns: list[str] | None = None,
        asset_type: str = "stock",
    ) -> pl.DataFrame:
        t0 = time.perf_counter()

        # 近期区间优先复用 repository 的预计算 enriched 历史缓存 (仅 stock: 该缓存为股票专用)。
        try:
            if columns is None and asset_type == "stock" and self.repo is not None and hasattr(self.repo, "get_enriched_range"):
                cached = self.repo.get_enriched_range(start, end, symbols=symbols, columns=columns)
                if cached is not None and not cached.is_empty():
                    elapsed = (time.perf_counter() - t0) * 1000
                    logger.info("load_panel(cache): %.0fms, %d rows, %d columns", elapsed, len(cached), len(cached.columns))
                    return cached
        except Exception as e:  # noqa: BLE001
            logger.debug("backtest load panel cache miss: %s", e)

        from app.tickflow.repository import enriched_dirname
        enriched_glob = str(self.repo.store.data_dir / enriched_dirname(asset_type) / "**" / "*.parquet")

        try:
            lf = scan_enriched_parquet(enriched_glob)
            if symbols is not None:
                lf = lf.filter(pl.col("symbol").is_in(symbols))
            if columns is not None:
                available = set(lf.collect_schema().names())
                selected = [c for c in columns if c in available]
                if "symbol" not in selected and "symbol" in available:
                    selected.insert(0, "symbol")
                if "date" not in selected and "date" in available:
                    selected.insert(1, "date")
                lf = lf.select(selected)
            df = (
                lf.filter(
                    (pl.col("date") >= start)
                    & (pl.col("date") <= end)
                )
                .sort(["symbol", "date"])
                .collect(streaming=True)
            )
        except Exception as e:
            logger.warning("backtest load panel failed: %s", e)
            return pl.DataFrame()

        if df.is_empty():
            return df

        if columns is not None:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.info("load_panel: %.0fms, %d rows, %d columns", elapsed, len(df), len(df.columns))
            return df

        from app.indicators.pipeline import compute_all
        # 按 asset_type 取维表: ETF 回测须用 ETF 维表, 否则名称 JOIN 失败(全 null)、
        # 涨停信号算在错误的 instruments 上。
        instruments = self.repo.get_instruments_asset(asset_type)
        df = compute_all(df, instruments=instruments)
        if not instruments.is_empty() and "name" not in df.columns:
            inst_cols = [c for c in ["symbol", "name"] if c in instruments.columns]
            if len(inst_cols) == 2:
                df = df.join(
                    instruments.select(inst_cols).unique(subset=["symbol"]),
                    on="symbol",
                    how="left",
                )

        elapsed = (time.perf_counter() - t0) * 1000
        logger.info("load_panel: %.0fms, %d rows", elapsed, len(df))
        return df

    # ── 撮合模拟 ──────────────────────────────────────

    def simulate_independent_candidates(
        self,
        panel: pl.DataFrame,
        entries: pl.Series | None,
        exits: pl.Series | None,
        config: MatcherConfig,
        progress_cb: "Callable[[dict], None] | None" = None,
        cancel_event: "threading.Event | None" = None,
        entry_signal_ids: list[str] | None = None,
        exit_signal_ids: list[str] | None = None,
    ) -> SimResult:
        """Execute every candidate independently on MarketMatrix asset columns."""
        if panel.is_empty():
            return self._empty_result()
        raw_candidates = int(entries.fill_null(False).sum()) if entries is not None and len(entries) == len(panel) else 0
        if raw_candidates <= 0:
            return self._empty_result()
        matrix = build_market_matrix(
            panel,
            entries,
            exits,
            entry_delay_bars=1 if config.entry_fill == "open_t+1" else 0,
            exit_delay_bars=1 if config.exit_fill == "open_t+1" else 0,
            entry_signal_ids=entry_signal_ids,
            exit_signal_ids=exit_signal_ids,
            minute_exit_trigger=config.exit_fill == "signal_next_minute",
        )
        return self._simulate_independent_matrix(
            matrix, raw_candidates, config, progress_cb, cancel_event,
        )

    def _simulate_independent_matrix(
        self,
        matrix: MarketMatrix,
        raw_candidates: int,
        config: MatcherConfig,
        progress_cb: "Callable[[dict], None] | None",
        cancel_event: "threading.Event | None",
        options: SimulationOptions | None = None,
        *,
        formal_end: date | None = None,
    ) -> SimResult:
        options = options or SimulationOptions()
        entry_prices = matrix.open if config.entry_fill == "open_t+1" else matrix.close
        exit_prices = matrix.open if config.exit_fill == "open_t+1" else matrix.close
        trades: list[TradeRecord] = []
        execution_stats = {
            "buy_invalid_price": 0,
            "buy_suspended": 0,
            "buy_limit_up": 0,
            "buy_score_filter": 0,
            "buy_no_next_bar": max(raw_candidates - int(matrix.entry.sum()), 0),
            "sell_invalid_price": 0,
            "sell_suspended": 0,
            "sell_limit_down": 0,
            "sell_minimum_holding": 0,
            "sell_no_future": 0,
            "pending_exit": 0,
        }

        minute_cache: dict = {}
        if config.minute_fill:
            trigger_times, trigger_assets = np.nonzero(matrix.entry | matrix.exit)
            dates = {matrix.timestamp_labels[int(t)][:10] for t in trigger_times}
            symbols = {matrix.symbols[int(a)] for a in trigger_assets}
            if dates and symbols:
                loaded = self._load_minute_for_fills(
                    self.repo,
                    list(symbols),
                    dates,
                    config.asset_type,
                )
                minute_cache = {key: value for key, value in loaded.items() if value is not None and len(value) > 0}

        def _count(key: str) -> None:
            execution_stats[key] = execution_stats.get(key, 0) + 1

        def _valid_price(value) -> bool:
            return bool(np.isfinite(value) and value > 0)

        def _present(time_id: int, asset_id: int) -> bool:
            return bool(np.isfinite([
                matrix.open[time_id, asset_id], matrix.high[time_id, asset_id],
                matrix.low[time_id, asset_id], matrix.close[time_id, asset_id],
            ]).any())

        def _signal_id(code: int, ids: tuple[str, ...]) -> str | None:
            return ids[code] if 0 <= code < len(ids) else None

        def _signal_date(signal_time: int, fallback: str) -> str:
            return matrix.timestamp_labels[signal_time][:10] if signal_time >= 0 else fallback

        def _refill(time_id: int, asset_id: int, side: str, daily_price: float) -> float:
            if not config.minute_fill or not minute_cache:
                return daily_price
            if side == "buy" and config.entry_fill != "open_t+1":
                return daily_price
            if side == "sell" and config.exit_fill == "close_t":
                return daily_price
            rows = minute_cache.get((matrix.symbols[asset_id], matrix.timestamp_labels[time_id][:10]))
            if rows is None:
                return daily_price
            reference_matrix = (
                matrix.entry_reference_price if side == "buy" else matrix.exit_reference_price
            )
            reference = float(reference_matrix[time_id, asset_id])
            scale = float(matrix.raw_scale[time_id, asset_id])
            if not _valid_price(reference) or not _valid_price(scale):
                return daily_price
            # Minute bars are raw prices; the reference line is adjusted.
            precise = self._resolve_minute_fill(
                rows, reference * scale, side,
            )
            return precise / scale if precise is not None else daily_price

        def _minute_trigger_price(time_id: int, asset_id: int) -> float | None:
            if not config.minute_fill or not minute_cache:
                return None
            rows = minute_cache.get((matrix.symbols[asset_id], matrix.timestamp_labels[time_id][:10]))
            if rows is None:
                return None
            reference = float(matrix.exit_reference_price[time_id, asset_id])
            scale = float(matrix.raw_scale[time_id, asset_id])
            if not _valid_price(reference) or not _valid_price(scale):
                return None
            trigger = self._resolve_minute_exit_trigger(rows, reference * scale)
            return trigger / scale if trigger is not None else None

        def _one_price_limit(time_id: int, asset_id: int, direction: str) -> bool:
            if not matrix.tradable[time_id, asset_id]:
                return False
            prices = [
                float(matrix.open[time_id, asset_id]), float(matrix.high[time_id, asset_id]),
                float(matrix.low[time_id, asset_id]), float(matrix.close[time_id, asset_id]),
            ]
            if not all(_valid_price(value) for value in prices):
                return False
            same = max(prices) - min(prices) <= max(abs(prices[3]) * 1e-4, 0.01)
            flags = matrix.limit_up_locked if direction == "up" else matrix.limit_down_locked
            return bool(flags[time_id, asset_id]) and same

        def _can_buy(time_id: int, asset_id: int) -> tuple[bool, str]:
            if not matrix.tradable[time_id, asset_id]:
                return False, "buy_suspended"
            if not _valid_price(entry_prices[time_id, asset_id]) or not _valid_price(
                matrix.raw_scale[time_id, asset_id]
            ):
                return False, "buy_invalid_price"
            if _one_price_limit(time_id, asset_id, "up"):
                return False, "buy_limit_up"
            return True, ""

        def _can_sell(time_id: int, asset_id: int, override: float | None) -> tuple[bool, str]:
            if not matrix.tradable[time_id, asset_id]:
                return False, "sell_suspended"
            price = override if override is not None else exit_prices[time_id, asset_id]
            if not _valid_price(price) or not _valid_price(matrix.raw_scale[time_id, asset_id]):
                return False, "sell_invalid_price"
            if _one_price_limit(time_id, asset_id, "down"):
                return False, "sell_limit_down"
            return True, ""

        def _risk_exit(pos: dict, time_id: int, asset_id: int) -> tuple[str | None, float | None]:
            if pos.get("pending_exit_reason") or pos["entry_time"] == time_id:
                return None, None
            entry_price = float(pos["entry_price"])
            open_price = float(matrix.open[time_id, asset_id])
            low_price = float(matrix.low[time_id, asset_id])
            high_price = float(matrix.high[time_id, asset_id])
            peak_price = float(pos["max_high"])
            lines: list[tuple[float, str]] = []
            if config.stop_loss_pct is not None:
                lines.append((entry_price * (1 - abs(config.stop_loss_pct)), "stop_loss"))
            if config.trailing_stop_pct is not None:
                lines.append((peak_price * (1 - abs(config.trailing_stop_pct)), "trailing_stop"))
            activate = config.trailing_take_profit_activate_pct
            drawdown = config.trailing_take_profit_drawdown_pct
            if activate is not None and drawdown is not None and peak_price > entry_price:
                if peak_price / entry_price - 1 >= abs(float(activate)):
                    lines.append((peak_price * (1 - abs(float(drawdown))), "trailing_take_profit"))
            valid_lines = [(line, reason) for line, reason in lines if _valid_price(line)]
            if valid_lines:
                stop_price, reason = max(valid_lines, key=lambda item: item[0])
                if _valid_price(open_price) and open_price <= stop_price:
                    return reason, open_price
                if _valid_price(low_price) and low_price <= stop_price:
                    return reason, _trigger_fill(config, matrix, time_id, asset_id, stop_price, "floor")
            if config.take_profit_pct is not None:
                take_profit = entry_price * (1 + abs(float(config.take_profit_pct)))
                if _valid_price(open_price) and open_price >= take_profit:
                    return "take_profit", open_price
                if _valid_price(high_price) and high_price >= take_profit:
                    return "take_profit", _trigger_fill(config, matrix, time_id, asset_id, take_profit, "ceil")
            return None, None

        def _try_close(
            pos: dict,
            time_id: int,
            asset_id: int,
            reason: str,
            signal_date: str,
            override: float | None = None,
        ) -> bool:
            symbol = matrix.symbols[asset_id]
            if not config.can_exit_after_sessions(symbol, int(pos["hold_days"])):
                if not pos.get("pending_exit_reason"):
                    pos["pending_exit_reason"] = reason
                    pos["pending_exit_signal_date"] = signal_date
                    pos["pending_exit_signal_id"] = (
                        _signal_id(
                            int(matrix.exit_signal_code[time_id, asset_id]),
                            matrix.exit_signal_ids,
                        )
                        if reason == "signal" else None
                    )
                    _count("pending_exit")
                pos["blocked_exit_days"] += 1
                _count("sell_minimum_holding")
                return False
            signal_id = (
                _signal_id(int(matrix.exit_signal_code[time_id, asset_id]), matrix.exit_signal_ids)
                if reason == "signal" else None
            )
            minute_trigger = config.exit_fill == "signal_next_minute" and reason == "signal"
            if minute_trigger and override is None:
                if pos.get("pending_exit_next_open"):
                    open_price = float(matrix.open[time_id, asset_id])
                    override = open_price if _valid_price(open_price) else None
                else:
                    override = _minute_trigger_price(time_id, asset_id)
                    if override is None:
                        if not pos.get("pending_exit_reason"):
                            pos["pending_exit_reason"] = reason
                            pos["pending_exit_signal_date"] = signal_date
                            pos["pending_exit_signal_id"] = signal_id
                            pos["pending_exit_next_open"] = True
                            _count("pending_exit")
                        pos["blocked_exit_days"] += 1
                        _count("sell_minute_trigger_fallback")
                        return False
            ok, blocked = _can_sell(time_id, asset_id, override)
            if not ok:
                if not pos.get("pending_exit_reason"):
                    pos["pending_exit_reason"] = reason
                    pos["pending_exit_signal_date"] = signal_date
                    pos["pending_exit_signal_id"] = signal_id
                    _count("pending_exit")
                if minute_trigger:
                    pos["pending_exit_next_open"] = True
                pos["blocked_exit_days"] += 1
                _count(blocked)
                return False
            exit_price = float(override) if override is not None else _refill(
                time_id, asset_id, "sell", float(exit_prices[time_id, asset_id])
            )
            exit_date = matrix.timestamp_labels[time_id][:10]
            traded_exit = _traded_price(matrix, time_id, asset_id, exit_price)
            shares = float(config.lot_size(symbol))
            exit_shares = shares * config.held_share_factor(symbol, pos["entry_date"], exit_date)
            entry_gross = shares * pos["traded_entry_price"]
            traded_exit_gross = exit_shares * traded_exit
            entry_value = entry_gross + config.buy_transaction_cost(entry_gross)
            # Valued on adjusted prices so dividends paid while held count as return.
            exit_value = pos["adjusted_units"] * exit_price - config.sell_transaction_cost(
                traded_exit_gross,
                symbol,
                exit_date,
                is_day_trade=pos["entry_date"] == exit_date,
            )
            pnl_amount = exit_value - entry_value
            trades.append(TradeRecord(
                symbol=symbol,
                name=matrix.names[asset_id],
                entry_date=pos["entry_date"],
                exit_date=exit_date,
                entry_price=round(float(pos["traded_entry_price"]), 4),
                exit_price=round(traded_exit, 4),
                entry_price_adj=round(float(pos["entry_price"]), 4),
                exit_price_adj=round(exit_price, 4),
                pnl_pct=round(float(pnl_amount / entry_value), 6) if entry_value > 0 else 0.0,
                duration=int(pos["hold_days"]),
                exit_reason=reason,
                shares=shares,
                exit_shares=round(float(exit_shares), 4),
                lots=1.0,
                entry_value=round(float(entry_value), 2),
                exit_value=round(float(exit_value), 2),
                pnl_amount=round(float(pnl_amount), 2),
                entry_score=round(float(pos["entry_score"]), 2),
                entry_signal_date=pos["entry_signal_date"],
                exit_signal_date=signal_date,
                blocked_exit_days=int(pos["blocked_exit_days"]),
                entry_signal_id=pos["entry_signal_id"],
                exit_signal_id=(pos.get("pending_exit_signal_id") or signal_id) if reason == "signal" else None,
            ))
            return True

        entry_times, entry_assets = np.nonzero(matrix.entry)
        order = np.lexsort((entry_times, entry_assets))
        for seq, order_pos in enumerate(order, start=1):
            time_id = int(entry_times[order_pos])
            asset_id = int(entry_assets[order_pos])
            if cancel_event is not None and cancel_event.is_set():
                break
            if progress_cb is not None and (seq == 1 or seq % 500 == 0):
                try:
                    progress_cb({
                        "day": seq,
                        "total": len(order),
                        "date": matrix.timestamp_labels[time_id][:10],
                        "equity": 0,
                    })
                except Exception:
                    pass
            ok, blocked = _can_buy(time_id, asset_id)
            if not ok:
                _count(blocked)
                continue
            score = _matrix_entry_score(matrix, time_id, asset_id)
            if config.score_min is not None and score < config.score_min:
                _count("buy_score_filter")
                continue
            if config.score_max is not None and score > config.score_max:
                _count("buy_score_filter")
                continue
            entry_price = _refill(time_id, asset_id, "buy", float(entry_prices[time_id, asset_id]))
            traded_entry = _traded_price(matrix, time_id, asset_id, entry_price)
            entry_date = matrix.timestamp_labels[time_id][:10]
            pos = {
                "entry_time": time_id,
                "entry_date": entry_date,
                "entry_signal_date": _signal_date(
                    int(matrix.entry_signal_time[time_id, asset_id]), entry_date
                ),
                "entry_signal_id": _signal_id(
                    int(matrix.entry_signal_code[time_id, asset_id]), matrix.entry_signal_ids
                ),
                "entry_price": entry_price,
                "traded_entry_price": traded_entry,
                "adjusted_units": config.lot_size(matrix.symbols[asset_id]) * (traded_entry / entry_price),
                "entry_score": score,
                "hold_days": 0,
                "max_high": (
                    max(entry_price, float(matrix.high[time_id, asset_id]))
                    if config.entry_fill == "open_t+1"
                    else entry_price
                ),
                "pending_exit_reason": None,
                "pending_exit_signal_date": None,
                "pending_exit_signal_id": None,
                "pending_exit_next_open": False,
                "blocked_exit_days": 0,
            }
            if matrix.exit[time_id, asset_id]:
                signal_date = _signal_date(
                    int(matrix.exit_signal_time[time_id, asset_id]),
                    entry_date,
                )
                if _try_close(pos, time_id, asset_id, "signal", signal_date):
                    continue
            future_times = [
                future for future in range(time_id + 1, matrix.shape[0])
                if _present(future, asset_id)
            ]
            if not future_times:
                _count("sell_no_future")
                continue
            closed = False
            for future in future_times:
                pos["hold_days"] += 1
                date_text = matrix.timestamp_labels[future][:10]
                reason, override = _risk_exit(pos, future, asset_id)
                if reason and _try_close(pos, future, asset_id, reason, date_text, override):
                    closed = True
                    break
                reason = None
                signal_date = date_text
                if pos.get("pending_exit_reason"):
                    reason = str(pos["pending_exit_reason"])
                    signal_date = str(pos.get("pending_exit_signal_date") or date_text)
                elif matrix.exit[future, asset_id]:
                    reason = "signal"
                    signal_date = _signal_date(int(matrix.exit_signal_time[future, asset_id]), date_text)
                elif config.max_hold_days is not None and pos["hold_days"] >= config.max_hold_days:
                    reason = "max_hold"
                elif future == future_times[-1]:
                    reason = "end"
                if reason and _try_close(pos, future, asset_id, reason, signal_date):
                    closed = True
                    break
                high_price = float(matrix.high[future, asset_id])
                if _valid_price(high_price):
                    pos["max_high"] = max(float(pos["max_high"]), high_price)
            if not closed and not pos.get("pending_exit_reason"):
                last_time = future_times[-1]
                _try_close(
                    pos, last_time, asset_id, "end", matrix.timestamp_labels[last_time][:10]
                )

        result = self._calc_independent_candidate_result(
            trades,
            raw_candidates,
            execution_stats,
            options=options,
            session_dates=matrix.timestamp_labels,
            risk_free_rate=config.risk_free_rate,
            formal_end=formal_end,
        )
        result.stats["market_matrix_shape"] = list(matrix.shape)
        result.stats["market_matrix_bytes"] = matrix.nbytes
        return result

    def simulate_independent_market_matrix(
        self,
        matrix: MarketMatrix,
        raw_candidates: int,
        config: MatcherConfig,
        progress_cb: "Callable[[dict], None] | None" = None,
        cancel_event: "threading.Event | None" = None,
        options: SimulationOptions | None = None,
        *,
        formal_end: date | None = None,
    ) -> SimResult:
        """Run independent-candidate simulation on a prebuilt MarketMatrix.

        formal_end is the last session of the formal window; the matrix may extend past it
        so open samples can exit, and statistics cover sessions through both.
        """
        return self._simulate_independent_matrix(
            matrix, raw_candidates, config, progress_cb, cancel_event, options,
            formal_end=formal_end,
        )

    # ── 分钟K精确成交 ──────────────────────────────────

    @staticmethod
    def _resolve_minute_fill(
        minute_arr: np.ndarray,
        ref_price: float | None,
        side: str,
    ) -> float | None:
        """用当日分钟K确定精确成交价。

        Args:
            minute_arr: float64 2D 数组, 列顺序 = _MINUTE_NUMERIC_COLS
                        [open(0), high(1), low(2), close(3), volume(4), amount(5)]
                        (缺失的尾部列直接不存在, 用 shape 判断)
            ref_price: 信号参考线价格 (如 MA5 值); None 表示无参考线
            side: "buy" 或 "sell", 决定穿越方向

        Returns:
            精确成交价, 或 None (降级到日K口径)
        """
        if minute_arr is None or len(minute_arr) == 0:
            return None

        ncols = minute_arr.shape[1] if minute_arr.ndim == 2 else 1
        opens = minute_arr[:, 0]
        highs = minute_arr[:, 1] if ncols > 1 else opens
        lows = minute_arr[:, 2] if ncols > 2 else opens
        closes = minute_arr[:, 3] if ncols > 3 else opens
        volumes = minute_arr[:, 4] if ncols > 4 else None
        amounts = minute_arr[:, 5] if ncols > 5 else None

        # 有参考线 → 穿越价成交 (逻辑同止损: 找价格穿越参考线的时刻)
        if ref_price is not None and ref_price > 0 and np.isfinite(ref_price):
            if side == "sell":
                # 卖出: 价格跌破参考线 → 开盘已低于则按开盘; 否则按参考线 (低点触及)
                if np.isfinite(opens[0]) and opens[0] <= ref_price:
                    return float(opens[0])
                if np.any(np.isfinite(lows) & (lows <= ref_price)):
                    return float(ref_price)
            else:
                # 买入: 价格涨破参考线 → 开盘已高于则按开盘; 否则按参考线 (高点触及)
                if np.isfinite(opens[0]) and opens[0] >= ref_price:
                    return float(opens[0])
                if np.any(np.isfinite(highs) & (highs >= ref_price)):
                    return float(ref_price)
            # 参考线存在但当日分钟K未穿越 → 用收盘 (信号确认)
            return float(closes[-1]) if np.isfinite(closes[-1]) else None

        # 无参考线 → VWAP (成交额/成交量), 退化到收盘价
        if volumes is not None and amounts is not None:
            total_vol = float(np.nansum(volumes))
            total_amt = float(np.nansum(amounts))
            if total_vol > 0 and total_amt > 0:
                return total_amt / total_vol

        return float(closes[-1]) if np.isfinite(closes[-1]) else None

    @staticmethod
    def _resolve_minute_exit_trigger(
        minute_arr: np.ndarray,
        ref_price: float | None,
    ) -> float | None:
        """分钟收盘确认向下穿越后，返回下一分钟开盘价。"""
        if minute_arr is None or len(minute_arr) < 2:
            return None
        if ref_price is None or not np.isfinite(ref_price) or ref_price <= 0:
            return None

        ncols = minute_arr.shape[1] if minute_arr.ndim == 2 else 1
        if ncols < 4:
            return None
        opens = minute_arr[:, 0]
        closes = minute_arr[:, 3]
        below = np.isfinite(closes) & (closes < ref_price)
        previous_above = np.empty(len(closes), dtype=bool)
        previous_above[0] = True
        previous_above[1:] = np.isfinite(closes[:-1]) & (closes[:-1] >= ref_price)
        crossings = np.flatnonzero(below & previous_above)
        if crossings.size == 0:
            return None
        next_idx = int(crossings[0]) + 1
        if next_idx >= len(opens) or not np.isfinite(opens[next_idx]) or opens[next_idx] <= 0:
            return None
        return float(opens[next_idx])

    # 分钟K cache 存储的数值列及固定顺序 (_resolve_minute_fill 按此顺序整数索引)。
    _MINUTE_NUMERIC_COLS = ["open", "high", "low", "close", "volume", "amount"]

    @staticmethod
    def _load_minute_for_fills(
        repo,
        symbols: list[str],
        dates_needed: set,
        asset_type: str,
    ) -> dict:
        """按触发日加载分钟K, 返回 {(symbol, date_str): float64 2D ndarray}。

        dates_needed: 需要分钟数据的日期集合 (set of date strings "YYYY-MM-DD")

        按触发日分批读取对应分区文件 (get_minute_by_dates), 而非扫描整个区间
        (get_minute_range)。内存与回测区间长度解耦, 只随触发日数量增长 ——
        触发日稀疏时避免读取区间内大量无关日期导致爆内存。

        cache 值为 float64 紧凑 2D 数组 (列顺序见 _MINUTE_NUMERIC_COLS), 而非
        完整 DataFrame, 避免每条记录携带 polars 元数据开销导致内存膨胀。
        """
        if not symbols or not dates_needed:
            return {}
        from datetime import date as _date
        sorted_date_strs = sorted(dates_needed)
        date_objs = [_date.fromisoformat(s) for s in sorted_date_strs]

        cache: dict = {}
        # 分批读取: 每批 50 个交易日, 处理完拼进 cache, 避免单批过大。
        BATCH = 50
        numeric_cols = BacktestEngine._MINUTE_NUMERIC_COLS
        for i in range(0, len(date_objs), BATCH):
            batch = date_objs[i:i + BATCH]
            try:
                df = repo.get_minute_by_dates(symbols, batch, asset_type=asset_type)
            except Exception as e:  # noqa: BLE001
                logger.warning("minute fill data load failed (batch %d-%d): %s", i, i + len(batch), e)
                continue
            if df.is_empty():
                continue
            # 按 (symbol, 日期) 分组, 每组转紧凑 float64 数组存入 cache
            df = df.sort(["symbol", "datetime"]).with_columns(
                pl.col("datetime").dt.strftime("%Y-%m-%d").alias("_d_str")
            )
            for sub in df.partition_by(["symbol", "_d_str"], as_dict=False):
                if sub.is_empty():
                    continue
                sym = sub["symbol"][0]
                d_str = sub["_d_str"][0]
                cols = [c for c in numeric_cols if c in sub.columns]
                cache[(sym, d_str)] = sub.select(
                    [pl.col(c).cast(pl.Float64) for c in cols]
                ).to_numpy()
        return cache

    def simulate_portfolio(
        self,
        panel: pl.DataFrame,
        entries: pl.Series | None,
        exits: pl.Series | None,
        config: MatcherConfig,
        progress_cb: "Callable[[dict], None] | None" = None,
        cancel_event: "threading.Event | None" = None,
        entry_signal_ids: list[str] | None = None,
        exit_signal_ids: list[str] | None = None,
    ) -> SimResult:
        """Account-level matcher backed by immutable ``time x asset`` arrays."""
        if panel.is_empty():
            return self._empty_result()

        matrix = build_market_matrix(
            panel,
            entries,
            exits,
            entry_delay_bars=1 if config.entry_fill == "open_t+1" else 0,
            exit_delay_bars=1 if config.exit_fill == "open_t+1" else 0,
            entry_signal_ids=entry_signal_ids,
            exit_signal_ids=exit_signal_ids,
            minute_exit_trigger=config.exit_fill == "signal_next_minute",
        )
        if not matrix.entry.any():
            return self._empty_result()
        return self._simulate_portfolio_matrix(matrix, config, progress_cb, cancel_event)

    def simulate_market_matrix(
        self,
        matrix: MarketMatrix,
        config: MatcherConfig,
        progress_cb: "Callable[[dict], None] | None" = None,
        cancel_event: "threading.Event | None" = None,
        options: SimulationOptions | None = None,
    ) -> SimResult:
        """Run the production Python matcher on a prebuilt MarketMatrix."""
        if not matrix.entry.any():
            return self._empty_result()
        return self._simulate_portfolio_matrix(matrix, config, progress_cb, cancel_event, options)

    def _simulate_portfolio_matrix(
        self,
        matrix: MarketMatrix,
        config: MatcherConfig,
        progress_cb: "Callable[[dict], None] | None",
        cancel_event: "threading.Event | None",
        options: SimulationOptions | None = None,
    ) -> SimResult:
        options = options or SimulationOptions()
        time_count, asset_count = matrix.shape
        entry_prices = matrix.open if config.entry_fill == "open_t+1" else matrix.close
        exit_prices = matrix.open if config.exit_fill == "open_t+1" else matrix.close
        cash = float(config.initial_capital)
        peak = cash
        max_positions = max(int(config.max_positions), 0)
        max_exposure_pct = min(max(float(config.max_exposure_pct), 0.0), 1.0)
        positions: dict[int, dict] = {}
        last_close = np.full(asset_count, np.nan, dtype=np.float64)
        trades: list[TradeRecord] = []
        equity_curve: list[dict] = []
        drawdown_curve: list[dict] = []
        equity_values: list[float] = []
        exposure_values: list[float] = []
        execution_stats = {
            "buy_invalid_price": 0,
            "buy_suspended": 0,
            "buy_limit_up": 0,
            "buy_no_slot": 0,
            "buy_cash": 0,
            "buy_lot_size": 0,
            "buy_same_day_reentry": 0,
            "buy_exposure": 0,
            "buy_score_filter": 0,
            "sell_invalid_price": 0,
            "sell_suspended": 0,
            "sell_limit_down": 0,
            "sell_minimum_holding": 0,
            "pending_exit": 0,
        }

        minute_cache: dict = {}
        if config.minute_fill:
            trigger_times, trigger_assets = np.nonzero(matrix.entry | matrix.exit)
            trigger_dates = {matrix.timestamp_labels[int(t)][:10] for t in trigger_times}
            trigger_symbols = {matrix.symbols[int(a)] for a in trigger_assets}
            if trigger_dates and trigger_symbols:
                loaded = self._load_minute_for_fills(
                    self.repo, list(trigger_symbols), trigger_dates, config.asset_type,
                )
                minute_cache = {key: value for key, value in loaded.items() if value is not None and len(value) > 0}

        def _count(key: str) -> None:
            execution_stats[key] = execution_stats.get(key, 0) + 1

        def _valid_price(value) -> bool:
            return bool(np.isfinite(value) and value > 0)

        def _signal_id(code: int, signal_ids: tuple[str, ...]) -> str | None:
            return signal_ids[code] if 0 <= code < len(signal_ids) else None

        def _signal_date(signal_time: int, fallback: str) -> str:
            return matrix.timestamp_labels[signal_time][:10] if signal_time >= 0 else fallback

        def _market_value() -> float:
            total = 0.0
            for asset, pos in positions.items():
                mark = last_close[asset]
                if not _valid_price(mark):
                    mark = pos["entry_price"]
                total += pos["adjusted_units"] * mark
            return total

        def _refill_price(time_id: int, asset_id: int, side: str, daily_price: float) -> float:
            if not config.minute_fill or not minute_cache:
                return daily_price
            if side == "buy" and config.entry_fill != "open_t+1":
                return daily_price
            if side == "sell" and config.exit_fill == "close_t":
                return daily_price
            key = (matrix.symbols[asset_id], matrix.timestamp_labels[time_id][:10])
            minute_rows = minute_cache.get(key)
            if minute_rows is None:
                return daily_price
            reference_matrix = (
                matrix.entry_reference_price if side == "buy" else matrix.exit_reference_price
            )
            reference = float(reference_matrix[time_id, asset_id])
            scale = float(matrix.raw_scale[time_id, asset_id])
            if not _valid_price(reference) or not _valid_price(scale):
                return daily_price
            # Minute bars are raw prices; the reference line is adjusted.
            precise = self._resolve_minute_fill(
                minute_rows, reference * scale, side,
            )
            return precise / scale if precise is not None else daily_price

        def _minute_trigger_price(time_id: int, asset_id: int) -> float | None:
            if not config.minute_fill or not minute_cache:
                return None
            key = (matrix.symbols[asset_id], matrix.timestamp_labels[time_id][:10])
            minute_rows = minute_cache.get(key)
            if minute_rows is None:
                return None
            reference = float(matrix.exit_reference_price[time_id, asset_id])
            scale = float(matrix.raw_scale[time_id, asset_id])
            if not _valid_price(reference) or not _valid_price(scale):
                return None
            trigger = self._resolve_minute_exit_trigger(minute_rows, reference * scale)
            return trigger / scale if trigger is not None else None

        def _one_price_limit(time_id: int, asset_id: int, direction: str) -> bool:
            if not matrix.tradable[time_id, asset_id]:
                return False
            prices = (
                float(matrix.open[time_id, asset_id]),
                float(matrix.high[time_id, asset_id]),
                float(matrix.low[time_id, asset_id]),
                float(matrix.close[time_id, asset_id]),
            )
            if not all(_valid_price(value) for value in prices):
                return False
            same_price = max(prices) - min(prices) <= max(abs(prices[3]) * 1e-4, 0.01)
            flag = matrix.limit_up_locked if direction == "up" else matrix.limit_down_locked
            return bool(flag[time_id, asset_id]) and same_price

        def _can_buy(time_id: int, asset_id: int) -> tuple[bool, str]:
            if not matrix.tradable[time_id, asset_id]:
                return False, "buy_suspended"
            if not _valid_price(entry_prices[time_id, asset_id]) or not _valid_price(
                matrix.raw_scale[time_id, asset_id]
            ):
                return False, "buy_invalid_price"
            if _one_price_limit(time_id, asset_id, "up"):
                return False, "buy_limit_up"
            return True, ""

        def _can_sell(time_id: int, asset_id: int, override: float | None = None) -> tuple[bool, str]:
            if not matrix.tradable[time_id, asset_id]:
                return False, "sell_suspended"
            price = override if override is not None else exit_prices[time_id, asset_id]
            if not _valid_price(price) or not _valid_price(matrix.raw_scale[time_id, asset_id]):
                return False, "sell_invalid_price"
            if _one_price_limit(time_id, asset_id, "down"):
                return False, "sell_limit_down"
            return True, ""

        def _mark_pending(
            asset_id: int,
            reason: str,
            signal_date: str,
            signal_id: str | None = None,
            next_open: bool = False,
        ) -> None:
            pos = positions[asset_id]
            if not pos.get("pending_exit_reason"):
                pos["pending_exit_reason"] = reason
                pos["pending_exit_signal_date"] = signal_date
                pos["pending_exit_signal_id"] = signal_id
                _count("pending_exit")
            if next_open:
                pos["pending_exit_next_open"] = True
            pos["blocked_exit_days"] += 1

        def _sell(
            time_id: int,
            asset_id: int,
            reason: str,
            signal_date: str,
            sold_today: set[int],
            override: float | None = None,
        ) -> None:
            nonlocal cash
            pos = positions.pop(asset_id)
            exit_price = float(override) if override is not None else _refill_price(
                time_id, asset_id, "sell", float(exit_prices[time_id, asset_id])
            )
            symbol = matrix.symbols[asset_id]
            exit_date = matrix.timestamp_labels[time_id][:10]
            traded_exit = _traded_price(matrix, time_id, asset_id, exit_price)
            exit_shares = pos["shares"] * config.held_share_factor(symbol, pos["entry_date"], exit_date)
            # Valued on adjusted prices so dividends paid while held count as return.
            exit_value = pos["adjusted_units"] * exit_price - config.sell_transaction_cost(
                exit_shares * traded_exit,
                symbol,
                exit_date,
                is_day_trade=pos["entry_date"] == exit_date,
            )
            cash += exit_value
            pnl_amount = exit_value - pos["entry_value"]
            pnl_pct = pnl_amount / pos["entry_value"] if pos["entry_value"] > 0 else 0.0
            sold_today.add(asset_id)
            trades.append(TradeRecord(
                symbol=matrix.symbols[asset_id],
                name=matrix.names[asset_id],
                entry_date=pos["entry_date"],
                exit_date=exit_date,
                entry_price=round(float(pos["traded_entry_price"]), 4),
                exit_price=round(traded_exit, 4),
                entry_price_adj=round(float(pos["entry_price"]), 4),
                exit_price_adj=round(exit_price, 4),
                pnl_pct=round(float(pnl_pct), 6),
                duration=int(pos["hold_days"]),
                exit_reason=reason,
                shares=round(float(pos["shares"]), 4),
                exit_shares=round(float(exit_shares), 4),
                lots=round(float(pos["lots"]), 2),
                position_pct=round(float(pos["position_pct"]), 6),
                entry_value=round(float(pos["entry_value"]), 2),
                exit_value=round(float(exit_value), 2),
                pnl_amount=round(float(pnl_amount), 2),
                entry_score=round(float(pos["entry_score"]), 2),
                entry_signal_date=pos["entry_signal_date"],
                exit_signal_date=signal_date,
                blocked_exit_days=int(pos["blocked_exit_days"]),
                entry_signal_id=pos["entry_signal_id"],
                exit_signal_id=(
                    pos.get("pending_exit_signal_id")
                    or _signal_id(int(matrix.exit_signal_code[time_id, asset_id]), matrix.exit_signal_ids)
                ) if reason == "signal" else None,
            ))

        def _try_sell(
            time_id: int,
            asset_id: int,
            reason: str,
            signal_date: str,
            sold_today: set[int],
            override: float | None = None,
        ) -> bool:
            pos = positions[asset_id]
            symbol = matrix.symbols[asset_id]
            if not config.can_exit_after_sessions(symbol, int(pos["hold_days"])):
                _mark_pending(asset_id, reason, signal_date)
                _count("sell_minimum_holding")
                return False
            signal_id = (
                _signal_id(int(matrix.exit_signal_code[time_id, asset_id]), matrix.exit_signal_ids)
                if reason == "signal" else None
            )
            minute_trigger = config.exit_fill == "signal_next_minute" and reason == "signal"
            if minute_trigger and override is None:
                pos = positions[asset_id]
                if pos.get("pending_exit_next_open"):
                    override = float(matrix.open[time_id, asset_id])
                else:
                    override = _minute_trigger_price(time_id, asset_id)
                    if override is None:
                        _mark_pending(asset_id, reason, signal_date, signal_id, next_open=True)
                        _count("sell_minute_trigger_fallback")
                        return False
            ok, blocked = _can_sell(time_id, asset_id, override)
            if not ok:
                _mark_pending(
                    asset_id,
                    reason,
                    signal_date,
                    signal_id,
                    next_open=minute_trigger,
                )
                _count(blocked)
                return False
            _sell(time_id, asset_id, reason, signal_date, sold_today, override)
            return True

        for time_id, date_label in enumerate(matrix.timestamp_labels):
            date_text = date_label[:10]
            if time_id % 20 == 0:
                if cancel_event is not None and cancel_event.is_set():
                    logger.info("回测被用户取消 (第 %d/%d 天)", time_id, time_count)
                    break
                if progress_cb is not None:
                    try:
                        progress_cb({
                            "day": time_id + 1,
                            "total": time_count,
                            "date": date_text,
                            "equity": round(cash + _market_value(), 2),
                        })
                    except Exception:
                        pass

            sold_today: set[int] = set()
            for pos in positions.values():
                pos["hold_days"] += 1

            for asset_id in list(positions):
                pos = positions.get(asset_id)
                if pos is None or pos.get("pending_exit_reason") or pos["entry_date"] == date_text:
                    continue
                if not matrix.tradable[time_id, asset_id] or pos["entry_price"] <= 0:
                    continue
                open_price = float(matrix.open[time_id, asset_id])
                low_price = float(matrix.low[time_id, asset_id])
                high_price = float(matrix.high[time_id, asset_id])
                entry_price = float(pos["entry_price"])
                peak_price = float(pos["max_high"])
                risk_lines: list[tuple[float, str]] = []
                if config.stop_loss_pct is not None:
                    risk_lines.append((entry_price * (1 - abs(config.stop_loss_pct)), "stop_loss"))
                if config.trailing_stop_pct is not None:
                    risk_lines.append((peak_price * (1 - abs(config.trailing_stop_pct)), "trailing_stop"))
                activate = config.trailing_take_profit_activate_pct
                drawdown = config.trailing_take_profit_drawdown_pct
                if activate is not None and drawdown is not None and peak_price > entry_price:
                    if peak_price / entry_price - 1 >= abs(float(activate)):
                        risk_lines.append((peak_price * (1 - abs(float(drawdown))), "trailing_take_profit"))
                valid_lines = [(line, reason) for line, reason in risk_lines if _valid_price(line)]
                if valid_lines:
                    stop_price, reason = max(valid_lines, key=lambda item: item[0])
                    override = None
                    if _valid_price(open_price) and open_price <= stop_price:
                        override = open_price
                    elif _valid_price(low_price) and low_price <= stop_price:
                        override = _trigger_fill(config, matrix, time_id, asset_id, stop_price, "floor")
                    if override is not None:
                        _try_sell(time_id, asset_id, reason, date_text, sold_today, override)
                        continue
                if config.take_profit_pct is not None:
                    take_profit = entry_price * (1 + abs(float(config.take_profit_pct)))
                    if _valid_price(open_price) and open_price >= take_profit:
                        _try_sell(time_id, asset_id, "take_profit", date_text, sold_today, open_price)
                    elif _valid_price(high_price) and high_price >= take_profit:
                        _try_sell(
                            time_id,
                            asset_id,
                            "take_profit",
                            date_text,
                            sold_today,
                            _trigger_fill(config, matrix, time_id, asset_id, take_profit, "ceil"),
                        )

            for asset_id in list(positions):
                pos = positions.get(asset_id)
                if pos is None:
                    continue
                reason = ""
                signal_date = date_text
                if pos.get("pending_exit_reason"):
                    reason = str(pos["pending_exit_reason"])
                    signal_date = str(pos.get("pending_exit_signal_date") or date_text)
                elif matrix.exit[time_id, asset_id]:
                    reason = "signal"
                    signal_date = _signal_date(int(matrix.exit_signal_time[time_id, asset_id]), date_text)
                elif config.max_hold_days is not None and pos["hold_days"] >= config.max_hold_days:
                    reason = "max_hold"
                elif time_id == time_count - 1:
                    reason = "end"
                if reason:
                    _try_sell(time_id, asset_id, reason, signal_date, sold_today)

            if time_id < time_count - 1 and max_positions > 0:
                candidates: list[tuple[int, float]] = []
                for asset_id in np.flatnonzero(matrix.entry[time_id]):
                    asset = int(asset_id)
                    if asset in positions:
                        continue
                    if asset in sold_today:
                        _count("buy_same_day_reentry")
                        continue
                    ok, blocked = _can_buy(time_id, asset)
                    if not ok:
                        _count(blocked)
                        continue
                    score = _matrix_entry_score(matrix, time_id, asset)
                    if config.score_min is not None and score < config.score_min:
                        _count("buy_score_filter")
                        continue
                    if config.score_max is not None and score > config.score_max:
                        _count("buy_score_filter")
                        continue
                    candidates.append((asset, score))
                candidates.sort(key=lambda item: item[1], reverse=True)
                slots = max_positions - len(positions)
                if slots <= 0:
                    execution_stats["buy_no_slot"] += len(candidates)
                elif candidates:
                    selected = candidates[:slots]
                    market_value_before = _market_value()
                    equity_before = cash + market_value_before
                    target_value = equity_before * max_exposure_pct / max_positions
                    exposure_capacity = equity_before * max_exposure_pct - market_value_before
                    if equity_before <= 0 or exposure_capacity <= 0 or max_exposure_pct <= 0:
                        execution_stats["buy_exposure"] += len(selected)
                    else:
                        weights = np.repeat(1 / len(selected), len(selected))
                        if config.position_sizing == "score_weight":
                            raw_weights = np.array([max(item[1], 0.0) for item in selected])
                            if raw_weights.sum() > 0:
                                weights = raw_weights / raw_weights.sum()
                        total_budget = min(cash, exposure_capacity, target_value * len(selected))
                        for (asset_id, entry_score), weight in zip(selected, weights):
                            if len(positions) >= max_positions:
                                _count("buy_no_slot")
                                break
                            market_value = _market_value()
                            equity = cash + market_value
                            capacity = equity * max_exposure_pct - market_value
                            allocation = min(total_budget * float(weight), target_value, cash, capacity)
                            if allocation <= 0:
                                _count("buy_exposure")
                                continue
                            entry_price = _refill_price(
                                time_id, asset_id, "buy", float(entry_prices[time_id, asset_id])
                            )
                            symbol = matrix.symbols[asset_id]
                            lot_size = config.lot_size(symbol)
                            traded_entry = _traded_price(matrix, time_id, asset_id, entry_price)
                            shares = config.align_shares(allocation, traded_entry, symbol)
                            entry_gross = shares * traded_entry
                            entry_value = entry_gross + config.buy_transaction_cost(entry_gross)
                            if shares <= 0:
                                _count("buy_lot_size")
                                continue
                            if entry_value > cash + 1e-6:
                                _count("buy_cash")
                                continue
                            if entry_value > capacity + 1e-6:
                                _count("buy_exposure")
                                continue
                            cash -= entry_value
                            positions[asset_id] = {
                                "entry_date": date_text,
                                "entry_signal_date": _signal_date(
                                    int(matrix.entry_signal_time[time_id, asset_id]), date_text
                                ),
                                "entry_signal_id": _signal_id(
                                    int(matrix.entry_signal_code[time_id, asset_id]), matrix.entry_signal_ids
                                ),
                                "entry_price": entry_price,
                                "traded_entry_price": traded_entry,
                                "entry_value": entry_value,
                                "shares": shares,
                                "adjusted_units": shares * (traded_entry / entry_price),
                                "lots": shares / lot_size,
                                "position_pct": entry_value / equity_before if equity_before > 0 else 0.0,
                                "entry_score": entry_score,
                                "max_high": entry_price,
                                "hold_days": 0,
                                "pending_exit_reason": None,
                                "pending_exit_signal_date": None,
                                "pending_exit_signal_id": None,
                                "pending_exit_next_open": False,
                                "blocked_exit_days": 0,
                            }
                            if (
                                config.minimum_holding_sessions == 0
                                and matrix.exit[time_id, asset_id]
                            ):
                                _try_sell(
                                    time_id,
                                    asset_id,
                                    "signal",
                                    _signal_date(
                                        int(matrix.exit_signal_time[time_id, asset_id]),
                                        date_text,
                                    ),
                                    sold_today,
                                )

            for asset_id, pos in positions.items():
                high_price = float(matrix.high[time_id, asset_id])
                if (
                    config.entry_fill == "open_t+1"
                    or pos["entry_date"] != date_text
                ) and _valid_price(high_price):
                    pos["max_high"] = max(float(pos["max_high"]), high_price)
            valid_closes = np.isfinite(matrix.close[time_id]) & (matrix.close[time_id] > 0)
            last_close[valid_closes] = matrix.close[time_id, valid_closes]

            market_value = _market_value()
            equity = cash + market_value
            peak = max(peak, equity)
            drawdown = (equity - peak) / peak if peak > 0 else 0.0
            exposure = market_value / equity if equity > 0 else 0.0
            equity_value = round(float(equity), 2)
            exposure_value = round(float(exposure), 4)
            equity_values.append(equity_value)
            exposure_values.append(exposure_value)
            if options.include_curves:
                equity_curve.append({
                    "date": date_text,
                    "value": equity_value,
                    "cash": round(float(cash), 2),
                    "positions": len(positions),
                    "exposure": exposure_value,
                })
                drawdown_curve.append({
                    "date": date_text,
                    "value": round(float(drawdown), 4),
                })

        statistics_started = time.perf_counter()
        stats = self._calc_portfolio_stats_from_values(
            equity_values,
            exposure_values,
            trades,
            config.initial_capital,
            session_dates=matrix.timestamp_labels[:len(equity_values)],
            risk_free_rate=config.risk_free_rate,
            min_annualized_sessions=options.min_annualized_sessions,
            include_monte_carlo=options.include_monte_carlo,
        )
        stats["statistics_ms"] = round(
            (time.perf_counter() - statistics_started) * 1000,
            1,
        )
        stats["execution"] = execution_stats
        stats["pending_exit_positions"] = sum(1 for pos in positions.values() if pos.get("pending_exit_reason"))
        stats["market_matrix_shape"] = [time_count, asset_count]
        stats["market_matrix_bytes"] = matrix.nbytes
        return SimResult(
            equity_curve=equity_curve if options.include_curves else [],
            drawdown_curve=drawdown_curve if options.include_curves else [],
            trades=trades if options.include_trades else [],
            per_symbol_stats=(
                self._calc_per_symbol(trades)
                if options.include_per_symbol_stats
                else []
            ),
            stats=stats,
        )

    # ── 统计计算 ──────────────────────────────────────

    @staticmethod
    def _sortino_ratio(returns: np.ndarray, periods_per_year: float, *, mar: float = 0.0) -> float | None:
        """Annualized mean(r - mar) / sqrt(mean(min(r - mar, 0)^2)), with mar per period.

        The downside deviation averages over all samples. Returns None without downside
        (undefined, like profit_factor), and 0.0 like Sharpe for fewer than 2 samples or
        zero volatility, where a constant shortfall below mar would otherwise read -sqrt(P).
        """
        returns = returns[np.isfinite(returns)]  # 剔除 inf/nan, 防止污染均值/序列化出非法 JSON
        if len(returns) < 2 or np.std(returns) <= _FLAT_RETURN_STD:
            return 0.0
        returns = returns - mar
        mean = float(np.mean(returns))
        downside = np.minimum(returns, 0.0)
        downside_dev = float(np.sqrt(np.mean(downside ** 2)))
        if downside_dev <= 0:
            return None
        return mean / downside_dev * float(np.sqrt(periods_per_year))

    @staticmethod
    def _mc_drawdown_percentiles(pnls: np.ndarray, n_sims: int = 1000) -> dict:
        """自助重抽样交易序列, 估计最大回撤的分布 — 回答"仅因成交顺序运气, 回撤能有多坏"。

        pnls are per-trade contributions to equity: portfolio callers scale by entry position fraction.
        对每笔收益有放回重抽样 n_sims 次, 各自算最大回撤, 取分位:
        - mc_maxdd_p50: 中位场景最大回撤
        - mc_maxdd_p95: 95% 置信最坏场景 (= 分布 5 分位, 更负)

        固定种子保证可复现/可测。样本 <3 无统计意义, 返回 None。
        大样本 (如 full 模式数千笔) 时按 2M 单元上限压降模拟次数, 防止瞬时数组 OOM。
        """
        pnls = pnls[np.isfinite(pnls)]  # 剔除 inf/nan, 否则 cumprod 传播 nan 导致分位为 nan
        # 防御: 单笔 pnl <= -100% 时 (1+pnl) <= 0 会让 cumprod 符号翻转/得非正净值, 回撤失真。
        # 回测有止损, 实际不会发生; 兜底 clip 到 -99.99% 保证 (1+pnl) 恒正。
        pnls = np.clip(pnls, -0.9999, None)
        n = len(pnls)
        if n < 3:
            return {"mc_maxdd_p50": None, "mc_maxdd_p95": None}
        # 内存护栏: samples/equity/peak/dd 各占 eff_sims*n*8B, 控总单元 <= 2M (~64MB 峰值)
        eff_sims = min(n_sims, max(200, 2_000_000 // n))
        rng = np.random.default_rng(42)
        samples = rng.choice(pnls, size=(eff_sims, n), replace=True)
        equity = np.cumprod(1.0 + samples, axis=1)
        # The initial capital (1.0) is the first peak, so an opening loss counts as drawdown.
        peak = np.maximum(np.maximum.accumulate(equity, axis=1), 1.0)
        dd = (equity - peak) / peak
        maxdds = dd.min(axis=1)
        return {
            "mc_maxdd_p50": round(float(np.percentile(maxdds, 50)), 4),
            "mc_maxdd_p95": round(float(np.percentile(maxdds, 5)), 4),
        }

    @staticmethod
    def _per_trade_block(pnls: np.ndarray, durations: np.ndarray) -> dict:
        """per-trade 明细字段: best/worst/median_pnl/avg_holding_days。"""
        pnls = pnls[np.isfinite(pnls)]  # 剔除 inf/nan, 防 best/worst 出非法值
        durations = durations[np.isfinite(durations)] if len(durations) else durations
        if not len(pnls):
            return {"best": 0.0, "worst": 0.0, "median_pnl": 0.0, "avg_holding_days": 0.0}
        return {
            "best": round(float(np.max(pnls)), 4),
            "worst": round(float(np.min(pnls)), 4),
            "median_pnl": round(float(np.median(pnls)), 4),
            "avg_holding_days": round(float(np.mean(durations)), 1) if len(durations) else 0.0,
        }

    @staticmethod
    def _calc_per_symbol(trades: list[TradeRecord]) -> list[dict]:
        if not trades:
            return []
        by_sym: dict[str, dict] = {}
        for t in trades:
            s = t.symbol
            d = by_sym.setdefault(s, {
                "symbol": s, "n_trades": 0, "total_return": 1.0,
                "best": -999.0, "worst": 999.0, "wins": 0, "pnls": [],
            })
            d["n_trades"] += 1
            d["pnls"].append(t.pnl_pct)
            d["total_return"] *= (1 + t.pnl_pct)
            d["best"] = max(d["best"], t.pnl_pct)
            d["worst"] = min(d["worst"], t.pnl_pct)
            if t.pnl_pct > 0:
                d["wins"] += 1

        result = []
        for d in by_sym.values():
            result.append({
                "symbol": d["symbol"],
                "n_trades": d["n_trades"],
                "total_return": round(d["total_return"] - 1.0, 4),
                "win_rate": round(d["wins"] / d["n_trades"], 4) if d["n_trades"] > 0 else 0.0,
                "best": round(d["best"], 4),
                "worst": round(d["worst"], 4),
            })
        return sorted(result, key=lambda x: x["total_return"], reverse=True)

    @staticmethod
    def _calc_independent_candidate_result(
        trades: list[TradeRecord],
        n_candidates: int,
        execution_stats: dict[str, int],
        *,
        options: SimulationOptions | None = None,
        session_dates: Sequence[str] | None = None,
        risk_free_rate: float = 0.0,
        formal_end: date | str | None = None,
    ) -> SimResult:
        """全量独立候选统计：按每个候选样本的实际执行收益聚合。

        Sharpe/Sortino use one return per session from the first session through the later of
        formal_end and the last exit, zero on sessions without exits; without session_dates
        only exit days are used.
        """
        options = options or SimulationOptions()
        if not trades:
            return SimResult(
                equity_curve=[],
                drawdown_curve=[],
                trades=[],
                per_symbol_stats=[],
                stats={
                    "mode": "full",
                    "full_kind": "candidate_execution",
                    "error": "no executable trades",
                    "n_candidates": int(n_candidates),
                    "n_trades": 0,
                    "execution": execution_stats,
                },
            )

        pnls = np.array([t.pnl_pct for t in trades], dtype=float)
        durations = np.array([t.duration for t in trades], dtype=float)
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        avg_win = float(np.mean(wins)) if len(wins) else 0.0
        avg_loss = abs(float(np.mean(losses))) if len(losses) else 0.0
        _pf = _gross_profit_factor(wins, losses)

        # 按退出日聚合已实现样本收益, 构造“样本收益曲线”。它不是账户净值。
        daily_returns: dict[str, list[float]] = {}
        for t in trades:
            daily_returns.setdefault(str(t.exit_date)[:10], []).append(float(t.pnl_pct))

        equity_curve: list[dict] = []
        drawdown_curve: list[dict] = []
        equity_values: list[float] = []
        equity = 1.0
        peak = 1.0
        daily_avg: list[float] = []
        for d_str in sorted(daily_returns.keys()):
            values = daily_returns[d_str]
            avg_ret = float(np.mean(values)) if values else 0.0
            daily_avg.append(avg_ret)
            equity *= (1 + avg_ret)
            peak = max(peak, equity)
            dd = (equity - peak) / peak if peak > 0 else 0.0
            equity_value = round(float(equity), 4)
            equity_values.append(equity_value)
            if options.include_curves:
                equity_curve.append({
                    "date": d_str,
                    "value": equity_value,
                    "positions": len(values),
                })
                drawdown_curve.append({
                    "date": d_str,
                    "value": round(float(dd), 4),
                })

        statistics_started = time.perf_counter()
        values = np.array(equity_values, dtype=float)
        total_return = float(values[-1] - 1.0) if len(values) else 0.0
        # The sample curve starts at 1.0, as drawdown_curve does.
        peaks = np.maximum(np.maximum.accumulate(values), 1.0) if len(values) else np.array([])
        drawdowns = values / peaks - 1 if len(values) else np.array([])
        max_drawdown = float(drawdowns.min()) if len(drawdowns) else 0.0
        exit_days = sorted(daily_returns)
        if session_dates is None:
            timeline = exit_days
        else:
            timeline_end = max(exit_days[-1], str(formal_end)[:10]) if formal_end else exit_days[-1]
            timeline = sorted(
                {str(label)[:10] for label in session_dates if str(label)[:10] <= timeline_end}
                | set(exit_days)
            )
        exit_day_avg = dict(zip(exit_days, daily_avg, strict=True))
        daily = np.array([exit_day_avg.get(day, 0.0) for day in timeline], dtype=float)
        annualization = _Annualization.from_sessions(
            timeline,
            risk_free_rate,
            options.min_annualized_sessions,
        )
        sharpe = annualization.sharpe(daily)
        sortino = annualization.sortino(daily)

        stats = {
            "mode": "full",
            "full_kind": "candidate_execution",
            "n_candidates": int(n_candidates),
            "n_trades": int(len(trades)),
            "n_days": int(len(daily_returns)),
            "avg_daily_candidates": round(float(len(trades) / max(len(daily_returns), 1)), 1),
            "avg_return": round(float(np.mean(pnls)), 4),
            "median_return": round(float(np.median(pnls)), 4),
            "win_rate": round(float(len(wins) / len(pnls)), 4) if len(pnls) else 0.0,
            "profit_factor": round(_pf, 2) if _pf is not None else None,
            "payoff_ratio": round(float(avg_win / avg_loss), 2) if avg_loss > 0 else None,
            "best": round(float(np.max(pnls)), 4),
            "worst": round(float(np.min(pnls)), 4),
            "avg_duration": round(float(np.mean(durations)), 1) if len(durations) else 0.0,
            "total_return": round(float(total_return), 4),
            "max_drawdown": round(float(max_drawdown), 4),
            "sharpe": round(float(sharpe), 2) if sharpe is not None else None,
            "sortino": round(float(sortino), 2) if sortino is not None else None,
            "execution": execution_stats,
            "risk_free_rate": risk_free_rate,
            "methodology": annualization.methodology(
                profit_factor_basis="pnl_pct",
                monte_carlo_basis="pnl_pct",
            ),
        }
        if options.include_return_distribution:
            lo, hi, nbins = -0.20, 0.20, 20
            clipped = np.clip(pnls, lo, hi)
            counts, edges = np.histogram(clipped, bins=nbins, range=(lo, hi))
            stats["return_distribution"] = [
                {
                    "range": f"{(edges[i]*100):+.0f}~{(edges[i+1]*100):+.0f}%",
                    "count": int(counts[i]),
                    "ratio": round(float(counts[i] / pnls.size), 4) if pnls.size else 0.0,
                }
                for i in range(nbins)
            ]
        if options.include_monte_carlo:
            stats.update(BacktestEngine._mc_drawdown_percentiles(pnls))
        stats["statistics_ms"] = round(
            (time.perf_counter() - statistics_started) * 1000,
            1,
        )

        return SimResult(
            equity_curve=equity_curve if options.include_curves else [],
            drawdown_curve=drawdown_curve if options.include_curves else [],
            trades=trades if options.include_trades else [],
            per_symbol_stats=(
                BacktestEngine._calc_per_symbol(trades)
                if options.include_per_symbol_stats
                else []
            ),
            stats=stats,
        )

    @staticmethod
    def _calc_portfolio_stats(
        equity_curve: list[dict],
        trades: list[TradeRecord],
        initial_capital: float,
        *,
        risk_free_rate: float = 0.0,
        include_monte_carlo: bool = True,
    ) -> dict:
        equity_values = [float(row["value"]) for row in equity_curve]
        exposure_values = [float(row.get("exposure", 0.0)) for row in equity_curve]
        return BacktestEngine._calc_portfolio_stats_from_values(
            equity_values,
            exposure_values,
            trades,
            initial_capital,
            session_dates=[str(row["date"]) for row in equity_curve],
            risk_free_rate=risk_free_rate,
            include_monte_carlo=include_monte_carlo,
        )

    @staticmethod
    def _calc_portfolio_stats_from_values(
        equity_values: list[float],
        exposure_values: list[float],
        trades: list[TradeRecord],
        initial_capital: float,
        *,
        session_dates: Sequence[str],
        risk_free_rate: float = 0.0,
        min_annualized_sessions: int = MIN_ANNUALIZED_SESSIONS,
        include_monte_carlo: bool = True,
    ) -> dict:
        """Account statistics; session_dates label each equity value (one per session)."""
        if not equity_values:
            return {"total_return": 0, "n_trades": 0}
        if len(session_dates) != len(equity_values):
            raise ValueError("session_dates must label every equity value")
        final_equity = float(equity_values[-1])
        total_return = final_equity / initial_capital - 1 if initial_capital > 0 else 0.0
        values = np.array(equity_values, dtype=float)
        daily = values[1:] / values[:-1] - 1 if len(values) > 1 else np.array([])
        # Initial capital is the first peak, as in drawdown_curve, so entry costs count as drawdown.
        peaks = np.maximum(np.maximum.accumulate(values), max(float(initial_capital), 0.0))
        drawdowns = values / peaks - 1
        max_drawdown = float(drawdowns.min()) if len(drawdowns) else 0.0
        annualization = _Annualization.from_sessions(
            session_dates,
            risk_free_rate,
            min_annualized_sessions,
        )
        annual_return: float | None = None
        calmar: float | None = None
        if annualization.unavailable_reason is None:
            annual_return = (
                (1 + total_return) ** (_DAYS_PER_YEAR / annualization.calendar_days) - 1
                if total_return > -1
                else total_return
            )
            calmar = round(float(annual_return / abs(max_drawdown)), 2) if abs(max_drawdown) > 0.001 else 0.0
        sharpe = annualization.sharpe(daily)
        sortino = annualization.sortino(daily)
        pnls = np.array([t.pnl_pct for t in trades], dtype=float) if trades else np.array([])
        amounts = np.array([t.pnl_amount for t in trades], dtype=float) if trades else np.array([])
        position_fractions = np.array([t.position_pct for t in trades], dtype=float) if trades else np.array([])
        durations = np.array([t.duration for t in trades], dtype=float) if trades else np.array([])
        exposures = np.array(exposure_values, dtype=float)
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        avg_win = float(np.mean(wins)) if len(wins) else 0.0
        avg_loss = abs(float(np.mean(losses))) if len(losses) else 0.0
        _pf = _gross_profit_factor(amounts[amounts > 0], amounts[amounts <= 0])
        stats = {
            "total_return": round(float(total_return), 4),
            "annual_return": round(float(annual_return), 4) if annual_return is not None else None,
            "max_drawdown": round(float(max_drawdown), 4),
            "sharpe": round(float(sharpe), 2) if sharpe is not None else None,
            "sortino": round(float(sortino), 2) if sortino is not None else None,
            "calmar": calmar,
            "win_rate": round(float(len(wins) / len(pnls)), 4) if len(pnls) else 0.0,
            "profit_factor": round(_pf, 2) if _pf is not None else None,
            "payoff_ratio": round(float(avg_win / avg_loss), 2) if avg_loss > 0 else None,
            "n_trades": len(trades),
            "avg_pnl": round(float(np.mean(pnls)), 4) if len(pnls) else 0.0,
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            **BacktestEngine._per_trade_block(pnls, durations),
            "final_equity": round(final_equity, 2),
            "initial_capital": round(float(initial_capital), 2),
            "avg_exposure": round(float(np.mean(exposures)), 4) if len(exposures) else 0.0,
            "max_exposure": round(float(np.max(exposures)), 4) if len(exposures) else 0.0,
            "risk_free_rate": risk_free_rate,
            "methodology": annualization.methodology(
                profit_factor_basis="pnl_amount",
                monte_carlo_basis="pnl_pct_x_position_pct",
            ),
        }
        if include_monte_carlo:
            stats.update(BacktestEngine._mc_drawdown_percentiles(pnls * position_fractions))
        return stats

    @staticmethod
    def _empty_result() -> SimResult:
        return SimResult(
            equity_curve=[], drawdown_curve=[], trades=[],
            per_symbol_stats=[], stats={"error": "no data or no signals"},
        )
