from __future__ import annotations

from datetime import date

import pytest

from app.backtest.regime_alignment import (
    align_regime_t_minus_one,
    build_regime_filter_mask,
)


def test_align_first_day_boundary_flag_only_tolerates_first_day():
    labels = ("2026-01-05", "2026-01-06", "2026-01-07")
    regimes = {
        "2026-01-05": ("weak", 20),
        "2026-01-06": ("strong", 80),
    }

    # 默认 (过滤场景): 正式首日=labels[0] 无前驱 → fail-closed
    with pytest.raises(ValueError, match="正式首日"):
        align_regime_t_minus_one(
            labels, regimes, required_start=date(2026, 1, 5), required_end=None,
        )

    # 统计场景: 首日容差 → 首日 None, 其余正常 T-1 对齐
    aligned = align_regime_t_minus_one(
        labels, regimes,
        required_start=date(2026, 1, 5), required_end=None,
        first_day_boundary_ok=True,
    )
    assert aligned == [None, ("weak", 20.0), ("strong", 80.0)]

    # 统计场景内部缺口仍 fail-closed (次日 T-1 缺环境)
    with pytest.raises(ValueError, match="缺少前一交易日环境"):
        align_regime_t_minus_one(
            labels, {"2026-01-05": ("weak", 20)},
            required_start=date(2026, 1, 5), required_end=None,
            first_day_boundary_ok=True,
        )


def test_align_regime_t_minus_one_is_pure_and_fail_closed_in_required_range():
    labels = ("2026-01-05", "2026-01-06", "2026-01-07")
    regimes = {
        "2026-01-05": ("weak", 20),
        "2026-01-06": {"state": "strong", "score": 80},
    }

    aligned = align_regime_t_minus_one(
        labels,
        regimes,
        required_start=date(2026, 1, 6),
        required_end=date(2026, 1, 7),
    )
    mask = build_regime_filter_mask(
        labels,
        {"states": ["strong"], "min_score": 60},
        regimes,
        required_start=date(2026, 1, 6),
        required_end=date(2026, 1, 7),
    )

    assert aligned == [None, ("weak", 20.0), ("strong", 80.0)]
    assert mask is not None
    assert mask.tolist() == [True, False, True]

    with pytest.raises(ValueError, match="正式首日"):
        align_regime_t_minus_one(
            labels,
            regimes,
            required_start=date(2026, 1, 5),
            required_end=date(2026, 1, 7),
        )

    lean_regimes = {
        "2026-01-05": ("lean_strong", 60),
        "2026-01-06": ("range", 50),
    }
    strong_only_mask = build_regime_filter_mask(
        labels,
        {"states": ["strong"]},
        lean_regimes,
        required_start=date(2026, 1, 6),
        required_end=date(2026, 1, 7),
    )
    assert strong_only_mask is not None
    assert strong_only_mask.tolist() == [True, False, False]

    aggregated_mask = build_regime_filter_mask(
        labels,
        {"states": ["strong", "lean_strong"]},
        lean_regimes,
        required_start=date(2026, 1, 6),
        required_end=date(2026, 1, 7),
    )
    assert aggregated_mask is not None
    assert aggregated_mask.tolist() == [True, True, False]

    with pytest.raises(ValueError, match="缺少前一交易日环境"):
        align_regime_t_minus_one(
            labels,
            {"2026-01-05": ("weak", 20)},
            required_start=date(2026, 1, 6),
            required_end=date(2026, 1, 7),
        )


def test_clamp_formal_start_for_regime():
    from app.backtest.regime_alignment import clamp_formal_start_for_regime

    # 复现「全部」范围: 正式起点 = 面板首日 (数据边界), 环境过滤启用 → 顺延到次日
    labels = ("2025-08-18", "2025-08-19", "2025-08-20")
    filt = {"states": ["strong", "lean_strong"]}
    assert clamp_formal_start_for_regime(labels, date(2025, 8, 18), filt) == date(2025, 8, 19)
    # 正式起点早于面板首日 (用户选的日期早于数据) 同样顺延
    assert clamp_formal_start_for_regime(labels, date(2025, 1, 1), filt) == date(2025, 8, 19)

    # 面板首日早于正式起点 (已有预热日) → 不动
    assert clamp_formal_start_for_regime(labels, date(2025, 8, 19), filt) == date(2025, 8, 19)

    # 过滤未启用 / 空 filter (无 states 无 min_score) / 起点 None → 不动
    assert clamp_formal_start_for_regime(labels, date(2025, 8, 18), None) == date(2025, 8, 18)
    assert clamp_formal_start_for_regime(labels, date(2025, 8, 18), {}) == date(2025, 8, 18)
    assert clamp_formal_start_for_regime(labels, None, filt) is None

    # 面板只有一天, 无法顺延 → 原样返回 (由 fail-closed 校验兜底)
    assert clamp_formal_start_for_regime(("2025-08-18",), date(2025, 8, 18), filt) == date(2025, 8, 18)

    # 顺延后 T-1 对齐不再报「正式首日」错误: 首日环境成为次日 T-1
    regimes = {"2025-08-18": ("weak", 20), "2025-08-19": ("strong", 80)}
    clamped = clamp_formal_start_for_regime(labels, date(2025, 8, 18), filt)
    aligned = align_regime_t_minus_one(labels, regimes, required_start=clamped, required_end=None)
    assert aligned[0] is None and aligned[1] == ("weak", 20.0)
