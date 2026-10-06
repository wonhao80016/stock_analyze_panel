"""稳健性指标测试 — Sortino + 蒙特卡罗回撤分位 + per-trade 明细。

被测新增:
- BacktestEngine._sortino_ratio(returns, periods_per_year, mar): 下行波动调整收益比
- BacktestEngine._mc_drawdown_percentiles(pnls, n_sims): 自助重抽样估计最大回撤分布
- _calc_portfolio_stats 输出新增 sortino / mc_maxdd_p50 / mc_maxdd_p95 /
  median_pnl / best / worst / avg_holding_days 字段
"""
from __future__ import annotations

from datetime import date

import numpy as np

from app.backtest.engine import BacktestEngine, SimulationOptions, TradeRecord

# ---------------------------------------------------------------
# Sortino
# ---------------------------------------------------------------

def test_sortino_all_losses_is_exact():
    """全亏损序列: mean/downside_dev * sqrt(252) 可手算校验。"""
    r = np.array([-0.1, -0.2])
    # mean=-0.15; downside_dev=sqrt(mean(0.01,0.04))=sqrt(0.025)
    # sortino = -0.15/sqrt(0.025) * sqrt(252) = -15.0599
    got = BacktestEngine._sortino_ratio(r, 252)
    assert abs(got - (-0.15 / np.sqrt(0.025) * np.sqrt(252))) < 1e-6
    assert round(got, 4) == -15.0599


def test_sortino_constant_returns_match_sharpe_zero():
    """Zero volatility: like Sharpe, Sortino reports 0.0 instead of -sqrt(P) from a constant loss."""
    assert BacktestEngine._sortino_ratio(np.array([-0.1, -0.1]), 252) == 0.0
    assert BacktestEngine._sortino_ratio(np.zeros(5), 252, mar=0.0001) == 0.0


def test_sortino_no_downside_returns_none():
    """无负收益 → 下行波动为 0, Sortino 未定义, 约定返回 None (不虚报 inf/0)。"""
    r = np.array([0.05, 0.10, 0.02])
    assert BacktestEngine._sortino_ratio(r, 252) is None


def test_sortino_exceeds_sharpe_when_downside_is_tamer():
    """下行波动小于总波动时, Sortino 应高于 Sharpe (只惩罚下行的优势)。"""
    # 大涨小跌: 上行贡献总波动但不进下行 → sortino > sharpe
    r = np.array([0.20, -0.02, 0.20, -0.02])
    mean = float(np.mean(r))
    sharpe = mean / float(np.std(r)) * np.sqrt(252)
    sortino = BacktestEngine._sortino_ratio(r, 252)
    assert sortino is not None
    assert sortino > sharpe


def test_sortino_too_few_points():
    assert BacktestEngine._sortino_ratio(np.array([0.1]), 252) == 0.0
    assert BacktestEngine._sortino_ratio(np.array([]), 252) == 0.0


# ---------------------------------------------------------------
# 蒙特卡罗最大回撤分位
# ---------------------------------------------------------------

# 固定种子 (42) + 固定输入下的快照值; 一旦有人改种子或算法, 立即红。
# Values from _reference_mc below: seed 42, explicit loop, peak starts at the initial 1.0.
_MC_INPUT = np.array([0.05, -0.03, 0.08, -0.06, 0.02, -0.04, 0.10, -0.05])
_MC_P50 = -0.107
_MC_P95 = -0.2196


def _reference_mc(pnls: np.ndarray, n_sims: int = 1000) -> tuple[float, float]:
    sims = min(n_sims, max(200, 2_000_000 // len(pnls)))
    samples = np.random.default_rng(42).choice(pnls, size=(sims, len(pnls)), replace=True)
    maxdds = []
    for row in samples:
        equity, peak, worst = 1.0, 1.0, 0.0
        for pnl in row:
            equity *= 1.0 + pnl
            peak = max(peak, equity)
            worst = min(worst, equity / peak - 1.0)
        maxdds.append(worst)
    return round(float(np.percentile(maxdds, 50)), 4), round(float(np.percentile(maxdds, 5)), 4)


def test_mc_drawdown_is_deterministic_snapshot():
    """固定种子 → 结果既跨调用一致, 又等于钉死的快照值 (防有人把种子改成系统熵)。"""
    a = BacktestEngine._mc_drawdown_percentiles(_MC_INPUT)
    b = BacktestEngine._mc_drawdown_percentiles(_MC_INPUT)
    assert a == b
    assert a["mc_maxdd_p50"] == _MC_P50
    assert a["mc_maxdd_p95"] == _MC_P95
    assert (a["mc_maxdd_p50"], a["mc_maxdd_p95"]) == _reference_mc(_MC_INPUT)


def test_mc_drawdown_p95_strictly_worse_and_negative():
    """含亏损输入: 中位场景必有回撤 (p50<0), 且 P95 严格差于 P50 (非恒真的 <=)。"""
    r = BacktestEngine._mc_drawdown_percentiles(_MC_INPUT)
    assert r["mc_maxdd_p50"] < 0.0
    assert r["mc_maxdd_p95"] < r["mc_maxdd_p50"]


def test_mc_drawdown_ignores_non_finite():
    """含 nan/inf 的收益应被剔除, 结果与纯净输入完全一致 (不污染分位/序列化)。"""
    dirty = np.concatenate([_MC_INPUT, [np.nan, np.inf, -np.inf]])
    assert BacktestEngine._mc_drawdown_percentiles(dirty) == BacktestEngine._mc_drawdown_percentiles(_MC_INPUT)


def test_mc_drawdown_clips_sub_minus_100pct_pnl():
    """防御: 单笔 pnl <= -100% 会让 (1+pnl)<=0 使 cumprod 符号翻转; clip 后分位仍有限。"""
    pnls = np.array([0.05, -1.5, 0.08, -0.06, 0.02, -0.04])  # -1.5 = -150%, 现实不会有
    r = BacktestEngine._mc_drawdown_percentiles(pnls)
    assert r["mc_maxdd_p50"] is not None
    for v in (r["mc_maxdd_p50"], r["mc_maxdd_p95"]):
        assert v == v  # 非 nan
        assert -1.0 <= v <= 0.0  # 回撤有界在 (-100%, 0], 未因符号翻转失真


def test_mc_drawdown_all_positive_has_zero_drawdown():
    """全正收益: 任何重排都无回撤 → 分位均为 0。"""
    pnls = np.array([0.01, 0.02, 0.03, 0.04, 0.05])
    r = BacktestEngine._mc_drawdown_percentiles(pnls)
    assert r["mc_maxdd_p50"] == 0.0
    assert r["mc_maxdd_p95"] == 0.0


def test_mc_drawdown_too_few_trades():
    r = BacktestEngine._mc_drawdown_percentiles(np.array([0.1, -0.1]))
    assert r["mc_maxdd_p50"] is None
    assert r["mc_maxdd_p95"] is None


# ---------------------------------------------------------------
# 集成: stats 输出新字段
# ---------------------------------------------------------------

def _trades(pnls: list[float], durations: list[int]) -> list[TradeRecord]:
    out = []
    for p, d in zip(pnls, durations, strict=True):
        out.append(TradeRecord(
            symbol="A", entry_date=date(2024, 1, 1), exit_date=date(2024, 1, 1 + d),
            entry_price=10.0, exit_price=10.0 * (1 + p), pnl_pct=p, duration=d,
            exit_reason="signal",
        ))
    return out


def test_portfolio_stats_emits_robustness_fields():
    """portfolio 分支同样输出 sortino / mc / per-trade 字段。"""
    equity_curve = [
        {"date": "2024-01-01", "value": 100_000.0, "exposure": 0.0},
        {"date": "2024-01-02", "value": 103_000.0, "exposure": 0.5},
        {"date": "2024-01-03", "value": 101_000.0, "exposure": 0.5},
        {"date": "2024-01-04", "value": 105_000.0, "exposure": 0.5},
    ]
    trades = _trades([0.06, -0.02, 0.04], [2, 1, 3])
    stats = BacktestEngine._calc_portfolio_stats(equity_curve, trades, 100_000)
    for k in ("sortino", "mc_maxdd_p50", "mc_maxdd_p95", "median_pnl", "best", "worst", "avg_holding_days"):
        assert k in stats, f"缺字段 {k}"


def test_independent_candidate_stats_emits_sortino_and_mc():
    """full 模式主路径 (_calc_independent_candidate_result) 必须输出 sortino / mc 字段。

    这是前端 full 模式指标卡的真实数据来源, 若漏拼字典展开会导致 UI 显示空值。
    """
    # 构造足量交易 (>=3) 以触发 mc; 用引擎产出真实结果而非直接调私有函数
    trades = _trades([0.10, -0.05, 0.08, -0.06, 0.03], [2, 1, 3, 2, 4])
    result = BacktestEngine._calc_independent_candidate_result(
        trades, n_candidates=5, execution_stats={},
    )
    for k in ("sortino", "mc_maxdd_p50", "mc_maxdd_p95"):
        assert k in result.stats, f"independent 分支缺字段 {k}"
    # mc 应为有效数值 (n=5>=3)
    assert result.stats["mc_maxdd_p50"] is not None


def test_lightweight_candidate_stats_do_not_call_monte_carlo(monkeypatch):
    trades = _trades([0.10, -0.05, 0.08, -0.06, 0.03], [2, 1, 3, 2, 4])

    def unexpected(_pnls):
        raise AssertionError("Monte Carlo should not run")

    monkeypatch.setattr(BacktestEngine, "_mc_drawdown_percentiles", unexpected)
    result = BacktestEngine._calc_independent_candidate_result(
        trades,
        n_candidates=5,
        execution_stats={},
        options=SimulationOptions(
            include_monte_carlo=False,
            include_curves=False,
            include_trades=False,
            include_per_symbol_stats=False,
            include_return_distribution=False,
        ),
    )

    assert "mc_maxdd_p50" not in result.stats
    assert result.equity_curve == []
    assert result.drawdown_curve == []
    assert result.trades == []
    assert result.per_symbol_stats == []
