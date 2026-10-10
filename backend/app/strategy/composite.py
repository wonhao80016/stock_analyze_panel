"""Composite strategy merger: pure functions shared by selection and backtest.

Selection (StrategyEngine._run_composite_strategy) and backtest (StrategyBacktestService) both
merge child results and must share one definition; otherwise selection and backtest would follow
different logic, a financial error (CONTRIBUTING §5.1).

Two merge entry points:
- merge_results: selection. Merges the children's StrategyResult into one StrategyResult.
- merge_signal_matrices: backtest. Merges the children's SignalMatrix into one SignalMatrix.

Merge semantics:
- entry: union=OR(entries); intersect=Σ(entries) >= min_confirm
- score: both paths use rank_normalize per child (average rank for ties, 0.5 for a single
         candidate, missing scores unranked), weighted and divided by the weight of all children.
         A candidate no child could score keeps a missing score and ranks last.
- exit (backtest): source projection. Child i's exit applies only within the holding window
                   after its own entry, so B's exit never closes A's position; the global
                   max_hold caps the window.

The matching engine reads the global matrix.exit without regard to source, so a plain OR(exit)
would cause phantom exits (B selling a position A picked). Projecting sources in the merger,
before matching, keeps the matching engine source-agnostic.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.strategy.engine import StrategyResult

_NEUTRAL_NORM = 0.5


def rank_normalize(values: np.ndarray) -> np.ndarray:
    """Average-rank position of each finite value in [0, 1], highest = 1.

    A single finite value maps to 0.5; non-finite values stay NaN (unranked).
    """
    values = np.asarray(values, dtype=np.float64)
    normalized = np.full(values.shape, np.nan, dtype=np.float64)
    finite = np.isfinite(values)
    count = int(finite.sum())
    if count == 0:
        return normalized
    if count == 1:
        normalized[finite] = _NEUTRAL_NORM
        return normalized
    ranked = values[finite]
    order = np.argsort(ranked, kind="stable")
    ordered = ranked[order]
    group_starts = np.flatnonzero(np.r_[True, ordered[1:] != ordered[:-1]])
    group_ends = np.r_[group_starts[1:], count]
    average_ranks = (group_starts + group_ends - 1) / 2.0
    ranks = np.empty(count, dtype=np.float64)
    ranks[order] = np.repeat(average_ranks, group_ends - group_starts)
    normalized[finite] = ranks / (count - 1)
    return normalized


def _blend_weights(children_weights: list[float]) -> tuple[np.ndarray, float]:
    """Child weights and the denominator of every merged score: the weight of all children.

    A child that did not pick or could not score a symbol therefore counts as 0 for it.
    """
    weights = np.asarray(children_weights, dtype=np.float64)
    total = float(weights.sum())
    if total <= 0:
        weights = np.ones_like(weights)
        total = float(len(weights))
    return weights, total


def _effective_min_confirm(min_confirm: int, n_children: int) -> int:
    return max(min_confirm, 1) if min_confirm and min_confirm > 0 else n_children


def _child_rank_norms(result: StrategyResult) -> tuple[set[str], dict[str, float]]:
    """A child's candidates and the rank position of each candidate it could score."""
    row_symbols = {str(row["symbol"]) for row in result.rows if row.get("symbol")}
    if result.scores:
        candidates = sorted(set(result.scores) | row_symbols)
        values = [result.scores.get(symbol) for symbol in candidates]
    else:
        # A child without scoring ranks all of its candidates equally.
        candidates = sorted(row_symbols)
        values = [0.0] * len(candidates)
    raw = np.array([np.nan if value is None else value for value in values], dtype=np.float64)
    norms = rank_normalize(raw)
    return set(candidates), {
        symbol: float(norm)
        for symbol, norm in zip(candidates, norms, strict=True)
        if np.isfinite(norm)
    }


def merge_results(
    results: list[StrategyResult],
    children_weights: list[float],
    merge_mode: str,
    min_confirm: int,
    *,
    as_of: date,
    strategy_id: str,
    elapsed_ms: float = 0.0,
) -> StrategyResult:
    """选股合并: 按 symbol 聚合各子结果, 标准化排名加权融合 score。

    Args:
        results: 各子策略的 StrategyResult(顺序与 children_weights 对齐)
        children_weights: 各子权重(顺序对齐)
        merge_mode: "union"(任一命中即入选) | "intersect"(至少 min_confirm 个命中)
        min_confirm: intersect 模式下命中的最少子策略数; <=0 视为全部子策略
        as_of / strategy_id: 合并结果归属(composite 自身)
    """
    from app.strategy.engine import StrategyResult

    n_children = len(results)
    if n_children == 0:
        return StrategyResult(as_of=as_of, strategy_id=strategy_id, elapsed_ms=elapsed_ms)

    child_candidates: list[set[str]] = []
    child_norms: list[dict[str, float]] = []
    for res in results:
        candidates, norms = _child_rank_norms(res)
        child_candidates.append(candidates)
        child_norms.append(norms)

    weights, total_weight = _blend_weights(children_weights)
    effective_min = _effective_min_confirm(min_confirm, n_children)
    scores: dict[str, float | None] = {}
    for sym in sorted(set().union(*child_candidates)):
        hits = [i for i in range(n_children) if sym in child_candidates[i]]
        if merge_mode == "intersect" and len(hits) < effective_min:
            continue
        scored = [i for i in hits if sym in child_norms[i]]
        if not scored:
            scores[sym] = None
            continue
        blended = sum(float(weights[i]) * child_norms[i][sym] for i in scored)
        scores[sym] = round(blended / total_weight * 100, 4)

    total = len(scores)
    return StrategyResult(
        as_of=as_of,
        strategy_id=strategy_id,
        rows=[],
        total=total,
        elapsed_ms=elapsed_ms,
        scores=scores,
    )


def _hold_masks_from_entries(entries: list[np.ndarray], max_hold: int) -> list[np.ndarray]:
    """计算每个子策略的持仓窗口掩码。

    hold_mask_i[t, a] = True 当且仅当存在 t' <= t 使 entry_i[t', a] 触发,
    且 t - t' < max_hold(即仍在最长持仓期内, 未被 max_hold 强制平仓)。
    实现用前向填充: 从每个 entry 起向前扩展 max_hold-1 个 bar 为 True。
    """
    if max_hold <= 0:
        max_hold = 1
    masks: list[np.ndarray] = []
    for entry in entries:
        raw = entry.astype(bool, copy=False)
        # 对每个 asset 列, 把 True 向前传播 max_hold 个 bar。
        # 用按行位移 OR 实现: mask[t] |= raw[t-k] for k in [0, max_hold-1]
        mask = np.zeros_like(raw)
        window = raw.copy()
        mask |= window
        for _k in range(1, max_hold):
            window = np.roll(window, 1, axis=0)
            window[0, :] = False  # roll 会在顶部环绕, 置零防未来泄漏
            mask |= window
        masks.append(mask)
    return masks


def merge_signal_matrices(
    shape: tuple[int, int],
    sigs: list,
    children: list[tuple[str, float]],
    merge_mode: str,
    min_confirm: int,
    max_hold: int,
):
    """回测合并: 产出合并 entry/exit/score/entry_signal_code 矩阵。

    Args:
        shape: (n_times, n_assets)
        sigs: 各子策略 SignalMatrix(顺序与 children 对齐)
        children: [(strategy_id, weight), ...]
        merge_mode / min_confirm: 同 merge_results
        max_hold: 全局最长持仓天数, 用于退出投影窗口封顶

    返回合并后的 SignalMatrix。
    """
    from app.backtest.matrix import make_signal_matrix

    n_times, n_assets = shape
    n_children = len(sigs)
    if n_children == 0:
        return make_signal_matrix(shape)

    # ── entry ──
    entries = [np.asarray(s.entry, dtype=bool) for s in sigs]
    entry_stack = np.stack(entries, axis=0)  # (n_children, n_times, n_assets)
    if merge_mode == "intersect":
        effective_min = _effective_min_confirm(min_confirm, n_children)
        confirm_count = entry_stack.sum(axis=0)  # (n_times, n_assets)
        merged_entry = confirm_count >= effective_min
    else:  # union
        merged_entry = entry_stack.any(axis=0)

    # ── exit 来源投影 ──
    # 每个子策略的 exit 仅在自己持仓窗口内生效, 不串平其他子的仓位。
    hold_masks = _hold_masks_from_entries(entries, max_hold)
    merged_exit = np.zeros(shape, dtype=bool)
    for i, s in enumerate(sigs):
        child_exit = np.asarray(s.exit, dtype=bool)
        merged_exit |= child_exit & hold_masks[i]

    # ── score: same rank normalisation and weighting as merge_results ──
    weights, total_weight = _blend_weights([w for _, w in children])
    norm_scores = np.full((n_children, n_times, n_assets), np.nan, dtype=np.float64)
    for i, s in enumerate(sigs):
        raw = np.asarray(s.score, dtype=np.float64)
        for t in range(n_times):
            hit_idx = np.flatnonzero(entries[i][t])
            if hit_idx.size:
                norm_scores[i, t, hit_idx] = rank_normalize(raw[t, hit_idx])

    ranked = np.isfinite(norm_scores)
    blended = (np.where(ranked, norm_scores, 0.0) * weights.reshape(-1, 1, 1)).sum(axis=0)
    merged_score = np.where(
        merged_entry,
        np.where(ranked.any(axis=0), blended / total_weight * 100, np.nan),
        0.0,
    )

    # ── entry_signal_code: 来源标记 ──
    # code = 命中的第一个子策略索引; -1 表示无命中。归因用。
    entry_codes = np.full(shape, -1, dtype=np.int16)
    for i in range(n_children):
        # 只给尚未标记的命中点打 code(第一个命中优先), 避免覆盖。
        untagged = (entry_codes == -1) & entries[i]
        entry_codes[untagged] = i

    # exit_signal_code 沿用 -1(无独立信号来源); 回测按 "signal" reason 归因即可。
    exit_codes = np.full(shape, -1, dtype=np.int16)

    # entry_signal_ids 映射表: code i → "composite:child_id"
    entry_signal_ids = tuple(f"composite:{cid}" for cid, _ in children)

    return make_signal_matrix(
        shape,
        entry=merged_entry.astype(np.uint8),
        exit=merged_exit.astype(np.uint8),
        score=merged_score.astype(np.float32),
        entry_signal_code=entry_codes,
        exit_signal_code=exit_codes,
        entry_signal_ids=entry_signal_ids,
    )
