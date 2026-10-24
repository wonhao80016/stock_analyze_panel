"""Deterministic Taiwan Abnormal Moves & Capital Flow Diagnostics Service (Phase 7D).

Answers:
  "Which Taiwan stocks are behaving abnormally today, and what objective evidence explains the abnormality?"
using only deterministic, traceable, locally persisted data.

CORE RULES:
- 100% Deterministic: NO AI recommendations, opinions, heat scores, or composite scores.
- Zero-HTTP at request time: Reads exclusively from local persisted Daily, Institutional, Margin stores.
- Point-In-Time No Look-Ahead: Target day is strictly excluded from historical baselines.
- Explicit Evidence & Formulas: Every triggered signal provides observed, baseline, ratio/delta, threshold, formula, and coverage.
- Purely Objective Classifications:
    * VOLUME_SPIKE: target_volume / mean(previous_5_traded_volumes) >= 2.0
      (the last 5 traded sessions within the previous 20; halted rows with null OHLC or no volume are skipped)
    * TURNOVER_SPIKE: target_amount / mean(previous_5_traded_amounts) >= 2.0 (same traded-session baseline)
    * PRICE_MOVE: abs(change_pct) / abs(leverage_multiplier) >= 0.05
      (previous_close is the latest traded session's close when the previous session was halted)
    * FOREIGN_FLOW_SPIKE: abs(foreign_net) >= 100,000 shares AND abs(foreign_net) / mean(abs(previous_20_foreign_net)) >= 3.0
      (a published session without a row for the symbol counts as 0, its halted sessions are skipped;
      needs 15 such sessions since listing, and that mean itself >= 10,000 shares)
    * TRUST_FLOW_SPIKE: abs(trust_net) >= 50,000 shares AND abs(trust_net) / mean(abs(previous_20_trust_net)) >= 3.0
      (same zero-filled baseline; that mean itself >= 5,000 shares)
    * MARGIN_SURGE: abs(margin_change) >= 500,000 shares AND abs(margin_change) / median(abs(previous_20)) >= 3.0
      (needs 15 of the previous 20 sessions; a zero median triggers on the absolute floor alone with
      status="zero_baseline" and ratio=None)
    * SHORT_SURGE: abs(short_change) >= 100,000 shares AND abs(short_change) / median(abs(previous_20)) >= 3.0
      (needs 15 of the previous 20 sessions; a zero median is reported as for MARGIN_SURGE)
    * SHORT_MARGIN_RATIO_SPIKE: (target_sm_ratio - median_previous_20_sm_ratio) >= 5.0 percentage points
      (needs 15 of the previous 20 sessions)
    * PRICE_FLOW_DIVERGENCE: abs(change_pct) / abs(leverage_multiplier) >= 0.03 AND an opposite foreign_net
      of >= 200,000 shares at >= 2x its own 20-session mean (inherits FOREIGN_FLOW_SPIKE's coverage gates)
    * RELATIVE_STRENGTH_OUTLIER: abs(stock_5d_return - industry_equal_weight_5d_return) >= 0.10
      (the industry return averages the other members and excludes the stock itself)
- Thresholds are compared on unrounded values; evidence ratios and deltas are rounded for display only.
- market_context.advance_ratio counts stocks only, the same population as industry_context.advance_ratio.

ETF COVERAGE (Phase 7J):
- get_diagnostics() accepts an opt-in `include_etfs: bool = False` parameter. Default False
  preserves the exact pre-Phase-7J stock-only universe and behavior — the public
  GET /api/taiwan/abnormal-diagnostics route and TaiwanScreener.tsx never pass this flag,
  so they are unaffected. Only TaiwanStockComparisonService and TaiwanAIResearchService
  opt in with include_etfs=True.
- When include_etfs=True, the universe is the EXPLICIT union of TAIWAN_STOCKS + TAIWAN_ETFS
  (never UniverseType.TAIWAN_ALL, which would silently include any future new instrument type).
- Signal applicability for ETFs (normal/leveraged/inverse alike):
    * VOLUME_SPIKE, TURNOVER_SPIKE, FOREIGN_FLOW_SPIKE, TRUST_FLOW_SPIKE, MARGIN_SURGE,
      SHORT_SURGE, SHORT_MARGIN_RATIO_SPIKE: self-normalized per-symbol ratios/deltas —
      apply unchanged to ETFs, real institutional/margin/short data confirmed present.
    * PRICE_MOVE, PRICE_FLOW_DIVERGENCE: the raw change_pct is normalized by
      abs(leverage_multiplier) (from the Security Master; 1.0 when None/absent, which is a
      no-op for stocks and normal 1x ETFs) BEFORE the existing 5%/3% thresholds are applied.
      `observed` in the evidence always remains the RAW change_pct — never the normalized
      value — so the true market move is never hidden.
    * RELATIVE_STRENGTH_OUTLIER: genuinely not applicable to any ETF (keyed on company
      `industry`, which is always None for ETFs — a different classification axis entirely,
      not something this phase invents a substitute for). Never evaluated as
      triggered/not-triggered for an ETF; always listed in `not_applicable_signals` instead.
- `TaiwanAbnormalDiagnosticItem.not_applicable_signals: list[str]` — [] for stocks with a
  domestic industry; ["RELATIVE_STRENGTH_OUTLIER"] for ETFs, TDRs and unclassified stocks.
  This does NOT change the meaning of `signals` (still only triggered signals) or
  `signal_count` (still only the count of triggered applicable signals, never "how many
  diagnostic types were evaluated").
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Literal
from pydantic import BaseModel, Field
import polars as pl

from app.taiwan.corporate_actions import (
    TaiwanCorporateActionStore,
    forward_adjusted,
    read_events_or_empty,
    reference_prices,
)
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import DatasetFreshnessStatus, resolve_target_latest_trading_date
from app.taiwan.industry_intelligence import UNCLASSIFIED_INDUSTRY, TaiwanIndustryIntelligenceService
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.market_intelligence import (
    TaiwanMarketIntelligenceService,
    daily_coverage_complete,
    delisted_after,
    listed_by,
    stored_partition_status,
)
from app.taiwan.realtime.calendar import TaiwanTradingCalendar, taipei_now
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master
from app.taiwan.universe.service import UniverseType

logger = logging.getLogger(__name__)


# ── Strong Typing & Pydantic Schemas ──────────────────────────


class DiagnosticSignalEvidence(BaseModel):
    """Evidence details supporting a triggered abnormal signal."""

    type: str = Field(..., description="訊號名稱: VOLUME_SPIKE, PRICE_MOVE, FOREIGN_FLOW_SPIKE 等")
    subtype: str | None = Field(None, description="子類型: 如 BUY / SELL 或 PRICE_UP_FOREIGN_SELL")
    severity: Literal["low", "moderate", "high", "extreme"] = Field("moderate", description="確定性嚴重度判定")
    observed: float = Field(..., description="當日觀測值")
    baseline: float | None = Field(None, description="基期比較值 (如 5D/20D 均值或中位數)")
    ratio: float | None = Field(None, description="觀測值相對於基期之倍數")
    delta: float | None = Field(None, description="差異差值 (如券資比變化點數)")
    threshold: float = Field(..., description="觸發門檻值")
    formula: str = Field(..., description="客觀透明計算公式")
    lookback_sessions: int = Field(..., description="基期交易日數")
    valid_sessions: int = Field(..., description="有效回溯日數")
    source: str = Field(..., description="來源持久化資料庫標識")
    status: str = Field("active", description="狀態: active 或 partial")


class CompactMarketContext(BaseModel):
    """Compact broad market breadth for context."""

    trade_date: str
    advance_ratio: float | None = Field(None, description="上漲家數比例 (僅計股票, 與產業脈絡同母體)")
    market_turnover: float | None = None
    overall_status: str = "unavailable"


class CompactIndustryContext(BaseModel):
    """Compact industry peer context."""

    industry: str | None = None
    turnover_share: float | None = None
    advance_ratio: float | None = None
    relative_strength_5d: float | None = None
    relative_strength_20d: float | None = None


class TaiwanAbnormalDiagnosticItem(BaseModel):
    """Deterministic diagnostic results and triggered signals for an individual stock."""

    symbol: str
    code: str
    name: str
    exchange: str
    industry: str | None = None
    close: float | None = None
    previous_close: float | None = None
    change: float | None = None
    change_pct: float | None = None  # decimal e.g. 0.05
    volume: float | None = None  # in shares
    amount: float | None = None  # in TWD (元)
    volume_ratio_5d: float | None = None
    amount_ratio_5d: float | None = None
    foreign_net: float | None = None  # in shares
    investment_trust_net: float | None = None  # in shares
    margin_balance_change: float | None = None  # in shares
    short_balance_change: float | None = None  # in shares
    short_margin_ratio: float | None = None  # percentage
    signal_count: int = Field(0, description="觸發之客觀異常訊號數量 (僅計入適用且觸發之訊號，非已評估之訊號種類數)")
    signals: list[DiagnosticSignalEvidence] = Field(default_factory=list)
    not_applicable_signals: list[str] = Field(
        default_factory=list,
        description="因標的類別而結構性不適用之訊號類型 (如 ETF、存託憑證或未分類股票之 RELATIVE_STRENGTH_OUTLIER); 有國內產業之股票為空清單",
    )
    market_context: CompactMarketContext
    industry_context: CompactIndustryContext


class DiagnosticsDataQuality(BaseModel):
    """Data quality and coverage metadata for abnormal diagnostics."""

    target_trade_date: str
    universe_supported_count: int
    evaluated_symbol_count: int
    diagnostic_symbol_count: int
    daily_status: DatasetFreshnessStatus
    institutional_status: DatasetFreshnessStatus
    margin_status: DatasetFreshnessStatus
    overall_status: Literal["complete", "provisional", "partial", "unavailable"]


class TaiwanAbnormalDiagnosticsSnapshot(BaseModel):
    """Unified strongly-typed snapshot response for Taiwan abnormal moves diagnostics."""

    trade_date: str
    generated_at: str
    universe_count: int
    diagnostic_count: int
    items: list[TaiwanAbnormalDiagnosticItem]
    data_quality: DiagnosticsDataQuality
    provenance: list[str]


# ── Service Implementation ────────────────────────────────────


def _has_industry(inst: Any) -> bool:
    """A null or blank industry is the UNCLASSIFIED bucket of the industry snapshot; TDRs have none."""
    return bool((inst.industry or "").strip())


# Exact boundaries such as 105 / 100 - 1 land a few ulps below the threshold in floating point.
_THRESHOLD_EPSILON = 1e-9


def _at_least(value: float, threshold: float) -> bool:
    return value >= threshold - _THRESHOLD_EPSILON


def _traded_condition(df: pl.DataFrame) -> pl.Expr:
    """Halted or no-trade rows carry null OHLC or zero volume."""
    condition = pl.col("volume").is_not_null() & (pl.col("volume") > 0)
    for col in (c for c in ("open", "high", "low", "close") if c in df.columns):
        condition = condition & pl.col(col).is_not_null()
    return condition


def _traded_rows(df: pl.DataFrame) -> pl.DataFrame:
    return df if df.is_empty() else df.filter(_traded_condition(df))


def _exchange_of(symbol: str) -> str:
    return symbol.rsplit(".", 1)[-1]


def _listing_day(inst: Any) -> date | None:
    raw = str(getattr(inst, "listing_date", None) or "").strip()
    try:
        return date.fromisoformat(raw[:10]) if raw else None
    except ValueError:
        return None


class TaiwanAbnormalDiagnosticsService:
    """Computes deterministic abnormal move & capital flow diagnostics via batch store processing."""

    def __init__(
        self,
        daily_store: TaiwanDailyStore | None = None,
        inst_store: TaiwanInstitutionalStore | None = None,
        margin_store: TaiwanMarginStore | None = None,
        calendar: TaiwanTradingCalendar | None = None,
        security_master: TaiwanSecurityMaster | None = None,
        market_intel_svc: TaiwanMarketIntelligenceService | None = None,
        industry_intel_svc: TaiwanIndustryIntelligenceService | None = None,
        corporate_actions: TaiwanCorporateActionStore | None = None,
    ) -> None:
        self.calendar = calendar or TaiwanTradingCalendar()
        self.daily_store = daily_store or TaiwanDailyStore()
        self.inst_store = inst_store or TaiwanInstitutionalStore()
        self.margin_store = margin_store or TaiwanMarginStore()
        self.security_master = security_master or get_security_master()
        self.corporate_actions = corporate_actions or TaiwanCorporateActionStore()
        self.market_intel_svc = market_intel_svc or TaiwanMarketIntelligenceService(
            daily_store=self.daily_store,
            inst_store=self.inst_store,
            margin_store=self.margin_store,
            calendar=self.calendar,
            security_master=self.security_master,
            corporate_actions=self.corporate_actions,
        )
        self.industry_intel_svc = industry_intel_svc or TaiwanIndustryIntelligenceService(
            daily_store=self.daily_store,
            inst_store=self.inst_store,
            margin_store=self.margin_store,
            calendar=self.calendar,
            security_master=self.security_master,
            corporate_actions=self.corporate_actions,
        )

    def _resolve_prior_sessions(self, target_date: date) -> tuple[list[date], list[date]]:
        """Resolve prior 5 and prior 20 trading dates strictly < target_date (no look-ahead)."""
        available = sorted([d for d in self.daily_store.available_dates() if d < target_date])
        prior_5 = available[-5:] if len(available) >= 5 else available
        prior_20 = available[-20:] if len(available) >= 20 else available
        return prior_5, prior_20

    def get_diagnostics(
        self,
        target_date: date | None = None,
        include_all: bool = False,
        signal_filter: str | None = None,
        industry_filter: str | None = None,
        exchange_filter: str | None = None,
        include_etfs: bool = False,
    ) -> TaiwanAbnormalDiagnosticsSnapshot:
        """Run batch diagnostics across Taiwan universe for target_date with zero request-time HTTP.

        include_etfs: opt-in only (default False preserves the exact pre-Phase-7J stock-only
        universe — the public GET /api/taiwan/abnormal-diagnostics route and
        TaiwanScreener.tsx never pass this flag). When True, the universe is the explicit
        union of TAIWAN_STOCKS + TAIWAN_ETFS (see module docstring "ETF COVERAGE" section).
        """
        target = target_date or resolve_target_latest_trading_date(self.calendar)
        self.security_master.ensure_loaded()

        # 1. Base Universe: Active supported stocks, plus ETFs when explicitly opted in
        #    (Phase 7J). Explicit union, never UniverseType.TAIWAN_ALL, so this module
        #    controls exactly which instrument types participate, not whatever the
        #    Security Master's "all" definition happens to include in the future.
        stock_symbols = self.security_master.get_universe(UniverseType.TAIWAN_STOCKS)
        universe_types = ["stock"]
        if include_etfs:
            stock_symbols = stock_symbols + self.security_master.get_universe(UniverseType.TAIWAN_ETFS)
            universe_types.append("etf")
        # A historical target also covers symbols delisted after it.
        delisted = delisted_after(self.security_master.to_dataframe(supported_only=True), target)
        if not delisted.is_empty():
            stock_symbols = stock_symbols + sorted(
                delisted.filter(pl.col("instrument_type").is_in(universe_types))["symbol"].to_list()
            )
        universe_count = len(stock_symbols)
        if not stock_symbols:
            return self._empty_snapshot(target)

        # 2. Prior sessions resolution (strictly < target)
        prior_5_dates, prior_20_dates = self._resolve_prior_sessions(target)

        # 3. Batch Read: DailyStore
        df_target_daily = self.daily_store.read_range(stock_symbols, target, target)
        prev_date = prior_5_dates[-1] if prior_5_dates else None
        df_prev_daily = self.daily_store.read_range(stock_symbols, prev_date, prev_date) if prev_date else pl.DataFrame()

        # 20-session history: the traded-session volume baseline and the 5D return base
        df_p20_daily = self.daily_store.read_range(stock_symbols, prior_20_dates[0], prior_20_dates[-1]) if prior_20_dates else pl.DataFrame()
        events = read_events_or_empty(self.corporate_actions, end=target)
        # Baselines are restated on target's scale.
        df_p20_daily = forward_adjusted(df_p20_daily, events, target)
        df_p20_traded = (
            _traded_rows(df_p20_daily).sort(["symbol", "date"]) if not df_p20_daily.is_empty() else pl.DataFrame()
        )
        df_p5_traded = (
            df_p20_traded.group_by("symbol", maintain_order=True).tail(5) if not df_p20_traded.is_empty() else pl.DataFrame()
        )

        # 4. Batch Read: InstitutionalStore
        df_target_inst = self.inst_store.read_range(stock_symbols, target, target)
        inst_p20_dates = sorted([d for d in self.inst_store.available_dates() if d < target])[-20:]
        df_p20_inst = self.inst_store.read_range(stock_symbols, inst_p20_dates[0], inst_p20_dates[-1]) if inst_p20_dates else pl.DataFrame()

        # 5. Batch Read: MarginStore
        df_target_margin = self.margin_store.read_range(stock_symbols, target, target)
        margin_p20_dates = sorted([d for d in self.margin_store.available_dates() if d < target])[-20:]
        df_p20_margin = self.margin_store.read_range(stock_symbols, margin_p20_dates[0], margin_p20_dates[-1]) if margin_p20_dates else pl.DataFrame()

        # 6. Context Caches (Phase 7A & 7B)
        market_snap = self.market_intel_svc.get_snapshot(target)
        ind_snap = self.industry_intel_svc.get_snapshot(target)
        ind_metrics_map: dict[str, Any] = {im.industry: im for im in ind_snap.industries}

        stock_breadth = market_snap.by_instrument.stock
        tot_adv = stock_breadth.advance_count
        tot_dec = stock_breadth.decline_count
        tot_flt = stock_breadth.flat_count
        tot_compared = tot_adv + tot_dec + tot_flt
        adv_ratio = round(tot_adv / tot_compared, 4) if tot_compared > 0 else None

        compact_market = CompactMarketContext(
            trade_date=market_snap.trade_date,
            advance_ratio=adv_ratio,
            market_turnover=market_snap.market_totals.turnover,
            overall_status=market_snap.data_quality.overall_status,
        )

        # 7. Aggregate Baseline Statistics with Polars
        p5_vol_stats = df_p5_traded.group_by("symbol").agg([
            pl.col("volume").mean().alias("mean_vol_5d"),
            pl.col("volume").count().alias("count_vol_5d"),
            pl.col("amount").mean().alias("mean_amt_5d"),
            pl.col("amount").count().alias("count_amt_5d"),
        ]) if not df_p5_traded.is_empty() else pl.DataFrame()
        last_traded_close_map = {
            r["symbol"]: float(r["close"])
            for r in df_p20_traded.group_by("symbol", maintain_order=True).tail(1).iter_rows(named=True)
        } if not df_p20_traded.is_empty() and "close" in df_p20_traded.columns else {}

        # T86 and TPEx list only securities the institutions traded, so a published session
        # without a row for the symbol is a zero-flow day rather than a missing one.
        # A halted session is no flow day of the symbol; it leaves both the sum and the count.
        halted_sessions: dict[str, set[date]] = {}
        if not df_p20_daily.is_empty():
            for sym, d in df_p20_daily.filter(~_traded_condition(df_p20_daily)).select(["symbol", "date"]).iter_rows():
                halted_sessions.setdefault(sym, set()).add(d)
        df_p20_flows = df_p20_inst
        if halted_sessions and not df_p20_inst.is_empty() and "date" in df_p20_inst.columns:
            halted_df = pl.DataFrame(
                [(sym, d) for sym, ds in halted_sessions.items() for d in ds],
                schema={"symbol": df_p20_inst.schema["symbol"], "date": df_p20_inst.schema["date"]},
                orient="row",
            )
            df_p20_flows = df_p20_inst.join(halted_df, on=["symbol", "date"], how="anti")
        p20_inst_stats = df_p20_flows.group_by("symbol").agg([
            pl.col("foreign_net").abs().sum().alias("sum_abs_foreign_20d"),
            pl.col("investment_trust_net").abs().sum().alias("sum_abs_trust_20d"),
        ]) if not df_p20_flows.is_empty() else pl.DataFrame()
        inst_published: dict[str, list[date]] = {}
        if not df_p20_inst.is_empty() and "date" in df_p20_inst.columns:
            for d, sym in df_p20_inst.select(["date", "symbol"]).iter_rows():
                inst_published.setdefault(_exchange_of(sym), []).append(d)
            inst_published = {ex: sorted(set(ds)) for ex, ds in inst_published.items()}

        p20_margin_stats = df_p20_margin.group_by("symbol").agg([
            pl.col("margin_change").abs().median().alias("median_abs_margin_change_20d"),
            pl.col("short_change").abs().median().alias("median_abs_short_change_20d"),
            pl.col("short_margin_ratio").median().alias("median_sm_ratio_20d"),
            pl.col("margin_change").count().alias("count_margin_20d"),
        ]) if not df_p20_margin.is_empty() else pl.DataFrame()

        # 5-day return baseline for stocks: close at target vs close at 5 sessions ago
        c5_base_map: dict[str, float] = {}
        if not df_p20_daily.is_empty():
            earliest_p5 = df_p20_daily.filter(pl.col("date") == prior_5_dates[0])
            for row in earliest_p5.iter_rows(named=True):
                if row["close"] is not None and row["close"] > 0:
                    c5_base_map[row["symbol"]] = float(row["close"])

        # Construct lookup maps for fast row generation
        target_daily_map = {r["symbol"]: r for r in df_target_daily.iter_rows(named=True)} if not df_target_daily.is_empty() else {}
        prev_daily_map = {r["symbol"]: r for r in df_prev_daily.iter_rows(named=True)} if not df_prev_daily.is_empty() else {}
        # A corporate-action session moves from the exchange reference price, not the previous close.
        ref_price_map = {
            r["symbol"]: r["ref_price"]
            for r in reference_prices(events, prev_date, target).iter_rows(named=True)
        }
        p5_vol_map = {r["symbol"]: r for r in p5_vol_stats.iter_rows(named=True)} if not p5_vol_stats.is_empty() else {}
        target_inst_map = {r["symbol"]: r for r in df_target_inst.iter_rows(named=True)} if not df_target_inst.is_empty() else {}
        p20_inst_map = {r["symbol"]: r for r in p20_inst_stats.iter_rows(named=True)} if not p20_inst_stats.is_empty() else {}
        target_margin_map = {r["symbol"]: r for r in df_target_margin.iter_rows(named=True)} if not df_target_margin.is_empty() else {}
        p20_margin_map = {r["symbol"]: r for r in p20_margin_stats.iter_rows(named=True)} if not p20_margin_stats.is_empty() else {}

        items: list[TaiwanAbnormalDiagnosticItem] = []

        # 8. Evaluate Diagnostic Signals Per Symbol
        for sym in stock_symbols:
            inst_master = self.security_master.get_instrument(sym)
            if inst_master is None:
                continue

            if exchange_filter and inst_master.exchange != exchange_filter:
                continue
            has_industry = _has_industry(inst_master)
            if industry_filter == UNCLASSIFIED_INDUSTRY:
                if inst_master.instrument_type != "stock" or has_industry:
                    continue
            elif industry_filter and inst_master.industry != industry_filter:
                continue

            t_daily = target_daily_map.get(sym)
            if not t_daily:
                continue  # untraded / missing in daily snapshot

            p_daily = prev_daily_map.get(sym)
            t_inst = target_inst_map.get(sym)
            t_margin = target_margin_map.get(sym)
            v_stat = p5_vol_map.get(sym)
            i_stat = p20_inst_map.get(sym)
            m_stat = p20_margin_map.get(sym)

            close_p = float(t_daily["close"]) if t_daily["close"] is not None else None
            prev_close_p = float(p_daily["close"]) if (p_daily and p_daily["close"] is not None) else None
            if prev_close_p is None:
                prev_close_p = last_traded_close_map.get(sym)
            if sym in ref_price_map:
                prev_close_p = float(ref_price_map[sym])
            vol = float(t_daily["volume"]) if t_daily["volume"] is not None else None
            amt = float(t_daily["amount"]) if t_daily["amount"] is not None else None

            chg = round(close_p - prev_close_p, 4) if (close_p is not None and prev_close_p is not None) else None
            raw_chg_pct = (close_p / prev_close_p) - 1.0 if (close_p is not None and prev_close_p is not None and prev_close_p > 0) else None
            chg_pct = round(raw_chg_pct, 6) if raw_chg_pct is not None else None

            # Leverage normalization (Phase 7J) — for PRICE_MOVE / PRICE_FLOW_DIVERGENCE
            # threshold checks ONLY. `observed` in the evidence always stays the RAW
            # chg_pct; only the threshold comparison uses the normalized value, so a
            # leveraged/inverse ETF's structural amplification doesn't by itself trip a
            # false "abnormal" signal. Divisor is 1.0 (no-op) for stocks and normal 1x ETFs.
            leverage_divisor = abs(inst_master.leverage_multiplier) if inst_master.leverage_multiplier else 1.0
            normalized_chg_pct = (raw_chg_pct / leverage_divisor) if raw_chg_pct is not None else None

            vol_ratio = None
            amt_ratio = None
            f_net = float(t_inst["foreign_net"]) if (t_inst and t_inst["foreign_net"] is not None) else None
            it_net = float(t_inst["investment_trust_net"]) if (t_inst and t_inst["investment_trust_net"] is not None) else None
            m_chg = float(t_margin["margin_change"]) if (t_margin and "margin_change" in t_margin and t_margin["margin_change"] is not None) else None
            s_chg = float(t_margin["short_change"]) if (t_margin and "short_change" in t_margin and t_margin["short_change"] is not None) else None
            sm_ratio = float(t_margin["short_margin_ratio"]) if (t_margin and "short_margin_ratio" in t_margin and t_margin["short_margin_ratio"] is not None) else None

            signals: list[DiagnosticSignalEvidence] = []

            # ── Signal 1: VOLUME_SPIKE ──
            if v_stat and v_stat["count_vol_5d"] == 5 and v_stat["mean_vol_5d"] > 0 and vol is not None:
                mean_v5 = float(v_stat["mean_vol_5d"])
                raw_vol_ratio = vol / mean_v5
                vol_ratio = round(raw_vol_ratio, 2)
                if _at_least(raw_vol_ratio, 2.0):
                    sev: Literal["low", "moderate", "high", "extreme"] = "moderate"
                    if _at_least(raw_vol_ratio, 5.0): sev = "extreme"
                    elif _at_least(raw_vol_ratio, 3.0): sev = "high"
                    signals.append(DiagnosticSignalEvidence(
                        type="VOLUME_SPIKE",
                        severity=sev,
                        observed=vol,
                        baseline=round(mean_v5, 1),
                        ratio=vol_ratio,
                        threshold=2.0,
                        formula="target_volume / mean(previous_5_traded_volumes)",
                        lookback_sessions=5,
                        valid_sessions=5,
                        source="taiwan_daily_store",
                    ))

            # ── Signal 2: TURNOVER_SPIKE ──
            if v_stat and v_stat["count_amt_5d"] == 5 and v_stat["mean_amt_5d"] > 0 and amt is not None:
                mean_a5 = float(v_stat["mean_amt_5d"])
                raw_amt_ratio = amt / mean_a5
                amt_ratio = round(raw_amt_ratio, 2)
                if _at_least(raw_amt_ratio, 2.0):
                    sev: Literal["low", "moderate", "high", "extreme"] = "moderate"
                    if _at_least(raw_amt_ratio, 5.0): sev = "extreme"
                    elif _at_least(raw_amt_ratio, 3.0): sev = "high"
                    signals.append(DiagnosticSignalEvidence(
                        type="TURNOVER_SPIKE",
                        severity=sev,
                        observed=amt,
                        baseline=round(mean_a5, 1),
                        ratio=amt_ratio,
                        threshold=2.0,
                        formula="target_amount / mean(previous_5_traded_amounts)",
                        lookback_sessions=5,
                        valid_sessions=5,
                        source="taiwan_daily_store",
                    ))

            # ── Signal 3: PRICE_MOVE ──
            # Leverage-normalized threshold check (Phase 7J): a 2x leveraged ETF's raw
            # move is divided by 2 before comparison so structural amplification alone
            # never trips a false signal; `observed`/`delta` stay the RAW change_pct.
            if normalized_chg_pct is not None and _at_least(abs(normalized_chg_pct), 0.05):
                sev: Literal["low", "moderate", "high", "extreme"] = "extreme" if _at_least(abs(normalized_chg_pct), 0.095) else ("high" if _at_least(abs(normalized_chg_pct), 0.07) else "moderate")
                signals.append(DiagnosticSignalEvidence(
                    type="PRICE_MOVE",
                    subtype="UP" if chg_pct > 0 else "DOWN",
                    severity=sev,
                    observed=round(chg_pct * 100.0, 2),
                    baseline=0.0,
                    delta=round(chg_pct * 100.0, 2),
                    threshold=5.0,
                    formula=(
                        "abs(close / previous_close - 1) / abs(leverage_multiplier) >= 0.05"
                        f" (raw={round(chg_pct * 100.0, 2)}%, leverage_multiplier={inst_master.leverage_multiplier},"
                        f" normalized={round(normalized_chg_pct * 100.0, 2)}%)"
                    ),
                    lookback_sessions=1,
                    valid_sessions=1,
                    source="taiwan_daily_store",
                ))

            # ── Signal 4: FOREIGN_FLOW_SPIKE ──
            listed_on = _listing_day(inst_master)
            inst_sessions = sum(
                1 for d in inst_published.get(_exchange_of(sym), [])
                if (listed_on is None or d >= listed_on) and d not in halted_sessions.get(sym, ())
            )
            f_mult = None
            raw_f_mult = None
            if f_net is not None and inst_sessions >= 15:
                mean_abs_f20 = float((i_stat["sum_abs_foreign_20d"] or 0) if i_stat else 0) / inst_sessions
                if mean_abs_f20 >= 10000 and abs(f_net) >= 100000:  # at least 100張 (100,000 shares)
                    raw_f_mult = abs(f_net) / mean_abs_f20
                    f_mult = round(raw_f_mult, 2)
                    if _at_least(raw_f_mult, 3.0):
                        sev = "extreme" if _at_least(raw_f_mult, 6.0) else "high"
                        signals.append(DiagnosticSignalEvidence(
                            type="FOREIGN_FLOW_SPIKE",
                            subtype="BUY" if f_net > 0 else "SELL",
                            severity=sev,
                            observed=f_net,
                            baseline=round(mean_abs_f20, 1),
                            ratio=f_mult,
                            threshold=3.0,
                            formula="abs(foreign_net) / mean(abs(previous_20_foreign_net), unlisted_sessions_as_0) >= 3.0",
                            lookback_sessions=20,
                            valid_sessions=inst_sessions,
                            source="taiwan_institutional_store",
                        ))

            # ── Signal 5: TRUST_FLOW_SPIKE ──
            if it_net is not None and inst_sessions >= 15:
                mean_abs_it20 = float((i_stat["sum_abs_trust_20d"] or 0) if i_stat else 0) / inst_sessions
                if mean_abs_it20 >= 5000 and abs(it_net) >= 50000:  # at least 50張
                    raw_it_mult = abs(it_net) / mean_abs_it20
                    if _at_least(raw_it_mult, 3.0):
                        sev = "extreme" if _at_least(raw_it_mult, 6.0) else "high"
                        signals.append(DiagnosticSignalEvidence(
                            type="TRUST_FLOW_SPIKE",
                            subtype="BUY" if it_net > 0 else "SELL",
                            severity=sev,
                            observed=it_net,
                            baseline=round(mean_abs_it20, 1),
                            ratio=round(raw_it_mult, 2),
                            threshold=3.0,
                            formula="abs(trust_net) / mean(abs(previous_20_trust_net), unlisted_sessions_as_0) >= 3.0",
                            lookback_sessions=20,
                            valid_sessions=inst_sessions,
                            source="taiwan_institutional_store",
                        ))

            # ── Signals 6 & 7: MARGIN_SURGE / SHORT_SURGE ──
            if m_stat and m_stat["count_margin_20d"] >= 15:
                for sig_type, change, median_key, floor, floor_label in (
                    ("MARGIN_SURGE", m_chg, "median_abs_margin_change_20d", 500000, "500k"),  # 500張
                    ("SHORT_SURGE", s_chg, "median_abs_short_change_20d", 100000, "100k"),  # 100張
                ):
                    if change is None or abs(change) < floor:
                        continue
                    field = "margin_change" if sig_type == "MARGIN_SURGE" else "short_change"
                    median_abs = float(m_stat[median_key] or 0)
                    subtype = "INCREASE" if change > 0 else "DECREASE"
                    if median_abs <= 0:
                        # A ratio to a zero median is undefined; the absolute floor alone decides.
                        signals.append(DiagnosticSignalEvidence(
                            type=sig_type,
                            subtype=subtype,
                            severity="high",
                            observed=change,
                            baseline=0.0,
                            ratio=None,
                            threshold=float(floor),
                            formula=f"abs({field}) >= {floor_label} (median(abs(previous_20)) == 0, ratio undefined)",
                            lookback_sessions=20,
                            valid_sessions=int(m_stat["count_margin_20d"]),
                            source="taiwan_margin_store",
                            status="zero_baseline",
                        ))
                        continue
                    raw_mult = abs(change) / median_abs
                    if _at_least(raw_mult, 3.0):
                        signals.append(DiagnosticSignalEvidence(
                            type=sig_type,
                            subtype=subtype,
                            severity="high" if _at_least(raw_mult, 6.0) else "moderate",
                            observed=change,
                            baseline=round(median_abs, 1),
                            ratio=round(raw_mult, 2),
                            threshold=3.0,
                            formula=f"abs({field}) >= {floor_label} AND ratio_to_20d_median >= 3.0",
                            lookback_sessions=20,
                            valid_sessions=int(m_stat["count_margin_20d"]),
                            source="taiwan_margin_store",
                        ))

            # ── Signal 8: SHORT_MARGIN_RATIO_SPIKE ──
            if sm_ratio is not None and m_stat and m_stat["count_margin_20d"] >= 15:
                med_sm20 = float(m_stat["median_sm_ratio_20d"] or 0)
                raw_sm_delta = sm_ratio - med_sm20
                if _at_least(raw_sm_delta, 5.0):  # >= 5% percentage point increase
                    signals.append(DiagnosticSignalEvidence(
                        type="SHORT_MARGIN_RATIO_SPIKE",
                        severity="high" if _at_least(raw_sm_delta, 10.0) else "moderate",
                        observed=sm_ratio,
                        baseline=med_sm20,
                        delta=round(raw_sm_delta, 2),
                        threshold=5.0,
                        formula="(short_margin_ratio - median_20d_ratio) >= 5.0 pct",
                        lookback_sessions=20,
                        valid_sessions=int(m_stat["count_margin_20d"]),
                        source="taiwan_margin_store",
                    ))

            # ── Signal 9: PRICE_FLOW_DIVERGENCE ──
            # Same leverage-normalization principle as PRICE_MOVE: the price-magnitude
            # gate (3%) is evaluated on normalized_chg_pct; observed/baseline stay raw.
            strong_foreign = raw_f_mult is not None and _at_least(raw_f_mult, 2.0)
            if normalized_chg_pct is not None and _at_least(abs(normalized_chg_pct), 0.03):
                # Up price with strong foreign sell
                if normalized_chg_pct > 0 and f_net is not None and f_net <= -200000 and strong_foreign:
                    signals.append(DiagnosticSignalEvidence(
                        type="PRICE_FLOW_DIVERGENCE",
                        subtype="PRICE_UP_FOREIGN_SELL",
                        severity="high",
                        observed=chg_pct * 100.0,
                        baseline=f_net,
                        threshold=3.0,
                        formula=(
                            "(change_pct / abs(leverage_multiplier)) >= 3% AND foreign_net <= -200k with multiple >= 2x"
                            f" (raw={round(chg_pct * 100.0, 2)}%, leverage_multiplier={inst_master.leverage_multiplier},"
                            f" normalized={round(normalized_chg_pct * 100.0, 2)}%)"
                        ),
                        lookback_sessions=20,
                        valid_sessions=inst_sessions,
                        source="taiwan_daily_store+taiwan_institutional_store",
                    ))
                # Down price with strong foreign buy
                elif normalized_chg_pct < 0 and f_net is not None and f_net >= 200000 and strong_foreign:
                    signals.append(DiagnosticSignalEvidence(
                        type="PRICE_FLOW_DIVERGENCE",
                        subtype="PRICE_DOWN_FOREIGN_BUY",
                        severity="high",
                        observed=chg_pct * 100.0,
                        baseline=f_net,
                        threshold=3.0,
                        formula=(
                            "(change_pct / abs(leverage_multiplier)) <= -3% AND foreign_net >= 200k with multiple >= 2x"
                            f" (raw={round(chg_pct * 100.0, 2)}%, leverage_multiplier={inst_master.leverage_multiplier},"
                            f" normalized={round(normalized_chg_pct * 100.0, 2)}%)"
                        ),
                        lookback_sessions=20,
                        valid_sessions=inst_sessions,
                        source="taiwan_daily_store+taiwan_institutional_store",
                    ))

            # ── Signal 10: RELATIVE_STRENGTH_OUTLIER ──
            ind_m = ind_metrics_map.get(inst_master.industry) if has_industry else None
            c5_base = c5_base_map.get(sym)
            # The peer return leaves the stock itself out, or a small industry dilutes its own outlier.
            peer_return_5d = ind_m.average_return_5d_excluding(sym) if ind_m else None
            if peer_return_5d is not None and c5_base and close_p is not None:
                ret_5d = (close_p / c5_base) - 1.0
                # Both sides span the same five sessions: the industry's own equal-weight
                # 5D return, not relative_strength_5d (which is already net of the market)
                # and not average_change_pct (a single session).
                raw_diff_5d = ret_5d - peer_return_5d
                if _at_least(abs(raw_diff_5d), 0.10):
                    diff_5d = round(raw_diff_5d, 4)
                    signals.append(DiagnosticSignalEvidence(
                        type="RELATIVE_STRENGTH_OUTLIER",
                        subtype="LEADER" if raw_diff_5d > 0 else "LAGGARD",
                        severity="high" if _at_least(abs(raw_diff_5d), 0.15) else "moderate",
                        observed=round(diff_5d * 100.0, 2),
                        baseline=round(peer_return_5d * 100.0, 2),
                        delta=round(diff_5d * 100.0, 2),
                        threshold=10.0,
                        formula="abs(stock_5d_return - industry_peer_equal_weight_5d_return) >= 10% (peers exclude the stock)",
                        lookback_sessions=5,
                        valid_sessions=5,
                        source="taiwan_daily_store+taiwan_industry_intelligence",
                    ))

            # Structurally not-applicable signals (Phase 7J) — explicit, not inferred from
            # absence. RELATIVE_STRENGTH_OUTLIER is keyed on company `industry`, which ETFs,
            # TDRs and unclassified stocks lack — never evaluated for them, only listed here.
            not_applicable_signals: list[str] = []
            if inst_master.instrument_type == "etf" or not has_industry:
                not_applicable_signals.append("RELATIVE_STRENGTH_OUTLIER")

            # Filter by specific signal type if requested
            if signal_filter:
                signals = [s for s in signals if s.type == signal_filter or s.subtype == signal_filter]

            if not include_all and len(signals) == 0:
                continue

            compact_ind = CompactIndustryContext(
                industry=inst_master.industry,
                turnover_share=ind_m.turnover_share if ind_m else None,
                advance_ratio=ind_m.advance_ratio if ind_m else None,
                relative_strength_5d=ind_m.relative_strength_5d if ind_m else None,
                relative_strength_20d=ind_m.relative_strength_20d if ind_m else None,
            )

            items.append(TaiwanAbnormalDiagnosticItem(
                symbol=inst_master.symbol,
                code=inst_master.code,
                name=inst_master.name,
                exchange=inst_master.exchange,
                industry=inst_master.industry,
                close=close_p,
                previous_close=prev_close_p,
                change=chg,
                change_pct=chg_pct,
                volume=vol,
                amount=amt,
                volume_ratio_5d=vol_ratio,
                amount_ratio_5d=amt_ratio,
                foreign_net=f_net,
                investment_trust_net=it_net,
                margin_balance_change=m_chg,
                short_balance_change=s_chg,
                short_margin_ratio=sm_ratio,
                signal_count=len(signals),
                signals=signals,
                not_applicable_signals=not_applicable_signals,
                market_context=compact_market,
                industry_context=compact_ind,
            ))

        # 9. Deterministic Multi-key Sorting:
        # signal_count desc -> abs(change_pct) desc -> amount desc -> symbol asc
        items.sort(
            key=lambda x: (
                -x.signal_count,
                -abs(x.change_pct or 0.0),
                -(x.amount or 0.0),
                x.symbol,
            )
        )

        # 10. Data Quality Compilation
        d_status: DatasetFreshnessStatus = "current" if len(df_target_daily) > 0 else "unavailable"
        i_status: DatasetFreshnessStatus = "current" if len(df_target_inst) > 0 else "unavailable"
        m_status: DatasetFreshnessStatus = "current" if len(df_target_margin) > 0 else "unavailable"
        # Must match market intelligence's status precedence.
        universe_df = self.security_master.to_dataframe(supported_only=True).filter(
            pl.col("symbol").is_in(stock_symbols)
        )
        coverage_complete = daily_coverage_complete(
            listed_by(universe_df, target), set(target_daily_map)
        )
        partitions = stored_partition_status(self.daily_store, [target, *prior_5_dates])
        overall: Literal["complete", "provisional", "partial", "unavailable"]
        if d_status != "current":
            overall = "unavailable"
        elif not coverage_complete or partitions == "partial":
            overall = "partial"
        elif partitions == "provisional" and i_status == "current" and m_status == "current":
            overall = "provisional"
        elif i_status == "current" and m_status == "current":
            overall = "complete"
        else:
            overall = "partial"

        dq = DiagnosticsDataQuality(
            target_trade_date=str(target),
            universe_supported_count=universe_count,
            evaluated_symbol_count=len(target_daily_map),
            diagnostic_symbol_count=len(items),
            daily_status=d_status,
            institutional_status=i_status,
            margin_status=m_status,
            overall_status=overall,
        )

        return TaiwanAbnormalDiagnosticsSnapshot(
            trade_date=str(target),
            generated_at=taipei_now().isoformat(),
            universe_count=universe_count,
            diagnostic_count=len(items),
            items=items,
            data_quality=dq,
            provenance=[
                "taiwan_security_master",
                "taiwan_daily_store",
                "taiwan_institutional_store",
                "taiwan_margin_store",
                "taiwan_market_intelligence",
                "taiwan_industry_intelligence",
            ],
        )

    def _empty_snapshot(self, target: date) -> TaiwanAbnormalDiagnosticsSnapshot:
        return TaiwanAbnormalDiagnosticsSnapshot(
            trade_date=str(target),
            generated_at=taipei_now().isoformat(),
            universe_count=0,
            diagnostic_count=0,
            items=[],
            data_quality=DiagnosticsDataQuality(
                target_trade_date=str(target),
                universe_supported_count=0,
                evaluated_symbol_count=0,
                diagnostic_symbol_count=0,
                daily_status="unavailable",
                institutional_status="unavailable",
                margin_status="unavailable",
                overall_status="unavailable",
            ),
            provenance=["taiwan_security_master"],
        )
