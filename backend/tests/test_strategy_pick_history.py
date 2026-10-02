from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import types
from datetime import UTC, date, datetime, time
from pathlib import Path

import polars as pl
import pytest

from app.api import screener as screener_api
from app.services import strategy_cache
from app.services import strategy_pick_history as sph
from app.strategy import config as strategy_config
from app.strategy.engine import CompositeChild, CompositeSpec, StrategyResult
from app.taiwan.realtime.calendar import TAIPEI_TZ

WED = date(2026, 9, 30)
THU = date(2026, 10, 1)
FRI = date(2026, 10, 2)
SUN = date(2026, 10, 4)
MON = date(2026, 10, 5)
T = WED


def at(d: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(d, time(hour, minute), TAIPEI_TZ)


def utc(d: date, hour: int, minute: int = 0) -> datetime:
    return at(d, hour, minute).astimezone(UTC)


def write_daily(data_dir: Path, d: date, exchanges=("TWSE", "TPEX")) -> None:
    part = data_dir / "taiwan" / "daily" / f"date={d.isoformat()}" / "part.parquet"
    part.parent.mkdir(parents=True, exist_ok=True)
    symbols = [f"{1000 + i}.{ex}" for i, ex in enumerate(exchanges)]
    pl.DataFrame({"symbol": symbols, "date": [d] * len(symbols)}).write_parquet(part)


def status(state="ok", exchanges=("TWSE", "TPEX"), dates=(T,), rows=10) -> sph.InputStatus:
    return sph.InputStatus(
        state=state, rows=rows, exchanges_at_t=frozenset(exchanges), dates=tuple(dates)
    )


def report(
    groups: dict[str, str | frozenset[str]],
    fingerprints: dict[str, str | None] | None = None,
    *,
    current: sph.InputStatus | None = None,
    history: sph.InputStatus | None = None,
) -> sph.RunReport:
    return sph.RunReport(
        current=current or status(),
        history=history or status(dates=(date(2026, 9, 1), T)),
        input_groups={
            sid: g if isinstance(g, frozenset) else frozenset({g}) for sid, g in groups.items()
        },
        fingerprints=fingerprints if fingerprints is not None else {sid: f"fp-{sid}" for sid in groups},
    )


def result(symbols, as_of: date = T, total: int | None = None) -> dict:
    return {
        "total": len(symbols) if total is None else total,
        "as_of": str(as_of),
        "rows": [
            {"symbol": s, "date": as_of, "score": float(10 - i), "raw_close": 100.0 + i}
            for i, s in enumerate(symbols)
        ],
    }


def rec(data_dir, results, run_report, now, label="16:30", mtime=100.0, as_of=T):
    return sph.record(data_dir, as_of, results, run_report, label, mtime, now)


def picks_of(data_dir: Path, sid: str) -> pl.DataFrame:
    return sph.read_picks(data_dir).picks.filter(pl.col("strategy_id") == sid).sort("rank")


def symbols_of(data_dir: Path, sid: str) -> list[str]:
    return picks_of(data_dir, sid)["symbol"].to_list()


def run_of(data_dir: Path, sid: str) -> dict | None:
    runs = sph.read_picks(data_dir).runs.filter(pl.col("strategy_id") == sid)
    return runs.row(0, named=True) if runs.height else None


def part_path(data_dir: Path, d: date = T) -> Path:
    return data_dir / "user_data" / "strategy_picks" / f"date={d.isoformat()}" / "part.parquet"


def runs_path(data_dir: Path, d: date = T) -> Path:
    return part_path(data_dir, d).with_name("runs.parquet")


def partition_bytes(data_dir: Path, d: date = T) -> dict[str, bytes]:
    folder = part_path(data_dir, d).parent
    return {p.name: p.read_bytes() for p in folder.iterdir()} if folder.is_dir() else {}


def fail_replace_for(monkeypatch, filename: str) -> None:
    real_replace = os.replace

    def fake_replace(src, dst):
        if Path(dst).name == filename:
            raise OSError(f"injected replace failure for {filename}")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", fake_replace)


def test_scenario1_evening_rerun_overwrites_before_open(tmp_path):
    write_daily(tmp_path, T)
    rep = report({"s1": sph.HISTORY})
    rec(tmp_path, {"s1": result(["A.TWSE", "B.TWSE"])}, rep, at(T, 16, 30))
    rep2 = report({"s1": sph.HISTORY}, {"s1": "changed"})
    out = rec(tmp_path, {"s1": result(["C.TPEX"])}, rep2, at(T, 21, 40), label="21:40")

    assert out.written == ("s1",)
    assert symbols_of(tmp_path, "s1") == ["C.TPEX"]
    run = run_of(tmp_path, "s1")
    assert run["first_recorded_at"] == utc(T, 16, 30)
    assert run["last_recorded_at"] == utc(T, 21, 40)
    assert run["run_label"] == "21:40"
    assert run["strategy_fingerprint"] == "changed"


def test_scenario2_first_write_after_unforeseen_holiday_is_kept_and_on_time(tmp_path):
    write_daily(tmp_path, T)
    out = rec(tmp_path, {"s1": result(["A.TWSE"])}, report({"s1": sph.HISTORY}), at(THU, 10))
    assert out.written == ("s1",)

    write_daily(tmp_path, FRI)
    run = run_of(tmp_path, "s1")
    assert run["recorded_late"] is False
    assert run["manifest_missing"] is False


def test_scenario3_holiday_repair_with_same_fingerprint_overwrites(tmp_path):
    write_daily(tmp_path, T, exchanges=("TWSE",))
    half = report({"s1": sph.CURRENT}, current=status(exchanges=("TWSE",)))
    rec(tmp_path, {"s1": result(["A.TWSE"])}, half, at(T, 16, 30))
    assert run_of(tmp_path, "s1")["universe_complete"] is False

    write_daily(tmp_path, T)
    out = rec(
        tmp_path, {"s1": result(["A.TWSE", "B.TPEX"])}, report({"s1": sph.CURRENT}), at(THU, 16, 30)
    )
    assert out.written == ("s1",)
    assert symbols_of(tmp_path, "s1") == ["A.TWSE", "B.TPEX"]
    assert run_of(tmp_path, "s1")["universe_complete"] is True


def test_scenario4_late_boot_is_late_and_changed_rule_is_rejected(tmp_path):
    write_daily(tmp_path, T)
    rec(tmp_path, {"s1": result(["A.TWSE"])}, report({"s1": sph.HISTORY}), at(THU, 10))

    changed = report({"s1": sph.HISTORY}, {"s1": "edited-during-session"})
    out = rec(tmp_path, {"s1": result(["Z.TWSE"])}, changed, at(THU, 11))
    assert out.rejected == {"s1": sph.REJECT_SOFT_FROZEN}
    assert symbols_of(tmp_path, "s1") == ["A.TWSE"]

    write_daily(tmp_path, THU)
    assert run_of(tmp_path, "s1")["recorded_late"] is True


def test_scenario5_hard_freeze_rejects_overwrite(tmp_path):
    write_daily(tmp_path, T)
    rep = report({"s1": sph.HISTORY})
    rec(tmp_path, {"s1": result(["A.TWSE"])}, rep, at(T, 16, 30))
    write_daily(tmp_path, THU)

    out = rec(tmp_path, {"s1": result(["B.TWSE"])}, rep, at(THU, 21, 40))
    assert out.rejected == {"s1": sph.REJECT_HARD_FROZEN}
    assert symbols_of(tmp_path, "s1") == ["A.TWSE"]


def test_hard_freeze_ignores_empty_enriched_directory_and_uses_daily_store(tmp_path):
    write_daily(tmp_path, T)
    (tmp_path / "kline_daily_enriched" / f"date={THU.isoformat()}").mkdir(parents=True)
    (tmp_path / "taiwan" / "daily" / f"date={FRI.isoformat()}").mkdir(parents=True)
    rep = report({"s1": sph.HISTORY})
    rec(tmp_path, {"s1": result(["A.TWSE"])}, rep, at(T, 16, 30))

    out = rec(tmp_path, {"s1": result(["B.TWSE"])}, rep, at(T, 21, 40))
    assert out.written == ("s1",)


def test_scenario6_friday_signal_weekend_recompute_overwrites(tmp_path):
    write_daily(tmp_path, FRI)
    rec(
        tmp_path, {"s1": result(["A.TWSE"], as_of=FRI)}, report({"s1": sph.HISTORY}, history=status(dates=(FRI,))),
        at(FRI, 16, 30), as_of=FRI,
    )
    assert sph.predicted_open(FRI) == at(MON, 9)
    out = rec(
        tmp_path, {"s1": result(["B.TWSE"], as_of=FRI)},
        report({"s1": sph.HISTORY}, {"s1": "other"}, history=status(dates=(FRI,))),
        at(SUN, 12), as_of=FRI,
    )
    assert out.written == ("s1",)
    assert sph.read_picks(tmp_path).picks["symbol"].to_list() == ["B.TWSE"]


def test_scenario7_partial_repair_keeps_first_recorded_at_and_is_on_time(tmp_path):
    write_daily(tmp_path, T)
    rep = report({"s1": sph.HISTORY})
    rec(tmp_path, {"s1": result(["A.TWSE"])}, rep, at(T, 16, 30))

    out = rec(tmp_path, {"s1": result(["A.TWSE", "B.TPEX"])}, rep, at(THU, 16, 30), mtime=200.0)
    assert out.written == ("s1",)
    write_daily(tmp_path, FRI)
    run = run_of(tmp_path, "s1")
    assert run["first_recorded_at"] == utc(T, 16, 30)
    assert run["last_recorded_at"] == utc(THU, 16, 30)
    assert run["recorded_late"] is False


def test_scenario8_unpublished_twse_skip_same_as_partial(tmp_path):
    write_daily(tmp_path, T)
    rep = report({"s1": sph.HISTORY})
    rec(tmp_path, {"s1": result(["A.TWSE"])}, rep, at(T, 21, 40), label="21:40")

    out = rec(tmp_path, {"s1": result(["A.TWSE"])}, rep, at(THU, 16, 30))
    assert out.written == ("s1",)
    write_daily(tmp_path, THU)
    run = run_of(tmp_path, "s1")
    assert run["first_recorded_at"] == utc(T, 21, 40)
    assert run["recorded_late"] is False


def test_scenario9_evening_history_failure_keeps_history_group(tmp_path):
    write_daily(tmp_path, T)
    groups = {"mx": sph.HISTORY, "px": sph.CURRENT}
    rec(tmp_path, {"mx": result(["A.TWSE"]), "px": result(["P.TWSE"])}, report(groups), at(T, 16, 30))
    before_mx = picks_of(tmp_path, "mx")
    before_mx_run = run_of(tmp_path, "mx")

    failed = report(groups, history=status(state="error", rows=0, exchanges=(), dates=()))
    out = rec(
        tmp_path, {"mx": result([]), "px": result(["Q.TPEX"])}, failed, at(T, 21, 40), label="21:40"
    )
    assert out.rejected == {"mx": sph.REJECT_INPUT_UNAVAILABLE}
    assert out.written == ("px",)
    assert picks_of(tmp_path, "mx").equals(before_mx)
    assert run_of(tmp_path, "mx") == before_mx_run
    assert symbols_of(tmp_path, "px") == ["Q.TPEX"]
    assert run_of(tmp_path, "px")["history_degraded"] is True


def test_soft_freeze_rejects_when_either_fingerprint_is_none(tmp_path):
    write_daily(tmp_path, T)
    rec(tmp_path, {"a": result(["A.TWSE"]), "b": result(["B.TWSE"])},
        report({"a": sph.HISTORY, "b": sph.HISTORY}, {"a": "fa", "b": None}), at(T, 16, 30))

    out = rec(tmp_path, {"a": result(["X.TWSE"]), "b": result(["Y.TWSE"])},
              report({"a": sph.HISTORY, "b": sph.HISTORY}, {"a": None, "b": None}), at(THU, 16, 30))
    assert out.rejected == {"a": sph.REJECT_SOFT_FROZEN, "b": sph.REJECT_SOFT_FROZEN}
    assert symbols_of(tmp_path, "a") == ["A.TWSE"]
    assert symbols_of(tmp_path, "b") == ["B.TWSE"]


def test_absent_strategy_keeps_previous_rows_and_manifest(tmp_path):
    write_daily(tmp_path, T)
    groups = {"a": sph.HISTORY, "b": sph.HISTORY}
    rec(tmp_path, {"a": result(["A.TWSE"]), "b": result(["B.TWSE"])}, report(groups), at(T, 16, 30))
    before_b = run_of(tmp_path, "b")

    rec(tmp_path, {"a": result(["C.TWSE"])}, report(groups), at(T, 21, 40))
    assert symbols_of(tmp_path, "b") == ["B.TWSE"]
    assert run_of(tmp_path, "b") == before_b
    assert symbols_of(tmp_path, "a") == ["C.TWSE"]


def test_zero_row_rerun_clears_rows_and_manifest_keeps_fingerprint(tmp_path):
    write_daily(tmp_path, T)
    rep = report({"s1": sph.HISTORY})
    rec(tmp_path, {"s1": result(["A.TWSE", "B.TWSE"])}, rep, at(T, 16, 30))
    rec(tmp_path, {"s1": result([])}, rep, at(T, 21, 40))

    assert picks_of(tmp_path, "s1").height == 0
    run = run_of(tmp_path, "s1")
    assert run["rows_written"] == 0
    assert run["total"] == 0
    assert run["strategy_fingerprint"] == "fp-s1"
    assert run["manifest_missing"] is False


def test_a_part_replace_failure_keeps_previous_state(tmp_path, monkeypatch):
    write_daily(tmp_path, T)
    rep = report({"s1": sph.HISTORY})
    rec(tmp_path, {"s1": result(["A.TWSE"])}, rep, at(T, 16, 30))
    before = sph.read_picks(tmp_path)

    fail_replace_for(monkeypatch, "part.parquet")
    with pytest.raises(OSError, match="injected"):
        rec(tmp_path, {"s1": result(["B.TWSE"])}, rep, at(T, 21, 40))
    monkeypatch.undo()

    after = sph.read_picks(tmp_path)
    assert after.picks.equals(before.picks)
    assert after.runs.equals(before.runs)
    assert after.picks["manifest_missing"].to_list() == [False]
    assert sorted(p.name for p in part_path(tmp_path).parent.iterdir()) == ["part.parquet", "runs.parquet"]


def test_b_manifest_failure_on_first_write(tmp_path, monkeypatch):
    write_daily(tmp_path, T)
    rep = report({"s1": sph.HISTORY})
    fail_replace_for(monkeypatch, "runs.parquet")
    with pytest.raises(OSError, match="injected"):
        rec(tmp_path, {"s1": result(["A.TWSE"])}, rep, at(T, 16, 30))
    monkeypatch.undo()

    picks = picks_of(tmp_path, "s1")
    assert picks["manifest_missing"].to_list() == [True]
    assert run_of(tmp_path, "s1") is None

    frozen = rec(tmp_path, {"s1": result(["B.TWSE"])}, rep, at(THU, 10))
    assert frozen.rejected == {"s1": sph.REJECT_SOFT_FROZEN}

    out = rec(tmp_path, {"s1": result(["B.TWSE"])}, rep, at(T, 21, 40))
    assert out.written == ("s1",)
    run = run_of(tmp_path, "s1")
    assert run["manifest_missing"] is False
    assert run["first_recorded_at"] == utc(T, 21, 40)


def test_c_manifest_failure_on_overwrite(tmp_path, monkeypatch):
    write_daily(tmp_path, T)
    rep = report({"s1": sph.HISTORY})
    rec(tmp_path, {"s1": result(["A.TWSE"])}, rep, at(T, 16, 30))

    fail_replace_for(monkeypatch, "runs.parquet")
    with pytest.raises(OSError, match="injected"):
        rec(tmp_path, {"s1": result(["B.TWSE"])}, rep, at(T, 21, 40))
    monkeypatch.undo()

    assert picks_of(tmp_path, "s1")["manifest_missing"].to_list() == [True]
    assert run_of(tmp_path, "s1")["manifest_missing"] is True

    frozen = rec(tmp_path, {"s1": result(["C.TWSE"])}, rep, at(THU, 16, 30))
    assert frozen.rejected == {"s1": sph.REJECT_SOFT_FROZEN}

    out = rec(tmp_path, {"s1": result(["C.TWSE"])}, rep, at(T, 22))
    assert out.written == ("s1",)
    run = run_of(tmp_path, "s1")
    assert run["manifest_missing"] is False
    assert run["first_recorded_at"] == utc(T, 16, 30)


def test_d_zero_row_first_write_with_manifest_failure_is_a_later_first_write(tmp_path, monkeypatch):
    write_daily(tmp_path, T)
    rep = report({"s1": sph.HISTORY})
    fail_replace_for(monkeypatch, "runs.parquet")
    with pytest.raises(OSError, match="injected"):
        rec(tmp_path, {"s1": result([])}, rep, at(T, 16, 30))
    monkeypatch.undo()

    history = sph.read_picks(tmp_path)
    assert history.picks.height == 0
    assert history.runs.height == 0

    write_daily(tmp_path, THU)
    out = rec(tmp_path, {"s1": result(["A.TWSE"])}, rep, at(THU, 17))
    assert out.written == ("s1",)
    assert run_of(tmp_path, "s1")["recorded_late"] is True


def test_run_id_mismatch_is_manifest_missing(tmp_path):
    write_daily(tmp_path, T)
    rep = report({"s1": sph.HISTORY})
    rec(tmp_path, {"s1": result(["A.TWSE", "B.TWSE"])}, rep, at(T, 16, 30))
    picks = pl.read_parquet(part_path(tmp_path))
    picks.with_columns(
        pl.when(pl.col("symbol") == "B.TWSE").then(pl.lit("other")).otherwise(pl.col("run_id")).alias("run_id")
    ).write_parquet(part_path(tmp_path))

    assert picks_of(tmp_path, "s1")["manifest_missing"].to_list() == [True, True]
    out = rec(tmp_path, {"s1": result(["C.TWSE"])}, rep, at(THU, 16, 30))
    assert out.rejected == {"s1": sph.REJECT_SOFT_FROZEN}


def test_rows_written_mismatch_is_manifest_missing(tmp_path):
    write_daily(tmp_path, T)
    rec(tmp_path, {"s1": result(["A.TWSE"])}, report({"s1": sph.HISTORY}), at(T, 16, 30))
    runs = pl.read_parquet(runs_path(tmp_path))
    runs.with_columns(pl.lit(5, dtype=pl.Int64).alias("rows_written")).write_parquet(runs_path(tmp_path))

    run = run_of(tmp_path, "s1")
    assert run["manifest_missing"] is True
    assert run["strategy_fingerprint"] is None
    assert picks_of(tmp_path, "s1")["manifest_missing"].to_list() == [True]


def test_history_failure_zero_row_matrix_results_keep_earlier_snapshot(tmp_path):
    write_daily(tmp_path, T)
    groups = {"m1": sph.HISTORY, "m2": sph.HISTORY}
    rec(tmp_path, {"m1": result(["A.TWSE"]), "m2": result(["B.TPEX", "C.TPEX"])}, report(groups), at(T, 16, 30))
    before = sph.read_picks(tmp_path)

    failed = report(groups, history=status(state="error", rows=0, exchanges=(), dates=()))
    out = rec(tmp_path, {"m1": result([]), "m2": result([])}, failed, at(T, 21, 40))
    assert out.written == ()
    after = sph.read_picks(tmp_path)
    assert after.picks.equals(before.picks)
    assert after.runs.equals(before.runs)


def test_current_failure_keeps_current_group_and_writes_history_group(tmp_path):
    write_daily(tmp_path, T)
    groups = {"mx": sph.HISTORY, "px": sph.CURRENT}
    rec(tmp_path, {"mx": result(["A.TWSE"]), "px": result(["P.TWSE"])}, report(groups), at(T, 16, 30))
    before_px = run_of(tmp_path, "px")

    failed = report(groups, current=status(state="missing", rows=0, exchanges=(), dates=()))
    out = rec(tmp_path, {"mx": result(["B.TWSE"]), "px": result([])}, failed, at(T, 21, 40))
    assert out.rejected == {"px": sph.REJECT_INPUT_UNAVAILABLE}
    assert symbols_of(tmp_path, "px") == ["P.TWSE"]
    assert run_of(tmp_path, "px") == before_px
    assert symbols_of(tmp_path, "mx") == ["B.TWSE"]
    assert run_of(tmp_path, "mx")["history_degraded"] is False


def test_single_exchange_input_is_written_as_universe_incomplete(tmp_path):
    write_daily(tmp_path, T)
    rep = report(
        {"mx": sph.HISTORY, "px": sph.CURRENT},
        history=status(exchanges=("TWSE",)),
        current=status(exchanges=("TWSE", "TPEX")),
    )
    rec(tmp_path, {"mx": result(["A.TWSE"]), "px": result(["B.TWSE"])}, rep, at(T, 16, 30))
    assert run_of(tmp_path, "mx")["universe_complete"] is False
    assert run_of(tmp_path, "px")["universe_complete"] is True
    assert picks_of(tmp_path, "mx")["universe_complete"].to_list() == [False]


def test_composite_needs_every_group_and_is_incomplete_if_any_is(tmp_path):
    write_daily(tmp_path, T)
    both = frozenset({sph.HISTORY, sph.CURRENT})
    rep = report({"cx": both}, current=status(exchanges=("TPEX",)))
    rec(tmp_path, {"cx": result(["A.TWSE"])}, rep, at(T, 16, 30))
    assert run_of(tmp_path, "cx")["universe_complete"] is False

    failed = report({"cx": both}, current=status(state="empty", rows=0, exchanges=(), dates=()))
    out = rec(tmp_path, {"cx": result([])}, failed, at(T, 21, 40))
    assert out.rejected == {"cx": sph.REJECT_INPUT_UNAVAILABLE}


def test_unverified_state_is_not_written(tmp_path):
    write_daily(tmp_path, T)
    rep = sph.RunReport(input_groups={"px": frozenset({sph.CURRENT}), "mx": frozenset({sph.HISTORY})})
    out = rec(tmp_path, {"px": result(["A.TWSE"]), "mx": result(["B.TWSE"])}, rep, at(T, 16, 30))
    assert out.written == ()
    assert set(out.rejected) == {"px", "mx"}
    assert not part_path(tmp_path).exists()


def test_short_history_with_ok_state_is_written(tmp_path):
    write_daily(tmp_path, T)
    short = status(dates=(date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 28), date(2026, 9, 29), T))
    out = rec(tmp_path, {"mx": result(["A.TWSE"])}, report({"mx": sph.HISTORY}, history=short), at(T, 16, 30))
    assert out.written == ("mx",)


@pytest.mark.parametrize(
    "history",
    [
        status(state="missing", rows=0, exchanges=(), dates=()),
        status(state="error", rows=0, exchanges=(), dates=()),
        status(state="empty", rows=0, exchanges=(), dates=()),
        status(dates=(date(2026, 9, 29),)),
        None,
    ],
    ids=["missing", "error", "empty", "no_rows_at_t", "not_loaded"],
)
def test_failed_history_read_is_not_written(tmp_path, history):
    write_daily(tmp_path, T)
    rep = report({"mx": sph.HISTORY})
    rep.history = history
    out = rec(tmp_path, {"mx": result(["A.TWSE"])}, rep, at(T, 16, 30))
    assert out.rejected == {"mx": sph.REJECT_INPUT_UNAVAILABLE}
    assert not part_path(tmp_path).exists()


def test_strategy_without_input_group_is_not_written(tmp_path):
    write_daily(tmp_path, T)
    rec(tmp_path, {"s1": result(["A.TWSE"])}, report({"s1": sph.HISTORY}), at(T, 16, 30))
    before = partition_bytes(tmp_path)

    out = rec(tmp_path, {"orphan": result(["B.TWSE"])}, report({}), at(T, 21, 40))
    assert out.rejected == {"orphan": sph.REJECT_INPUT_UNAVAILABLE}
    assert partition_bytes(tmp_path) == before


@pytest.mark.parametrize("broken", ["part.parquet", "runs.parquet"])
def test_unreadable_existing_file_aborts_without_writing(tmp_path, broken):
    write_daily(tmp_path, T)
    rep = report({"s1": sph.HISTORY})
    rec(tmp_path, {"s1": result(["A.TWSE"])}, rep, at(T, 16, 30))
    target = part_path(tmp_path).with_name(broken)
    target.write_bytes(b"not a parquet file")
    snapshot = {p.name: p.read_bytes() for p in target.parent.iterdir()}

    with pytest.raises(sph.PickHistoryReadError):
        rec(tmp_path, {"s1": result(["B.TWSE"])}, rep, at(T, 21, 40))
    assert {p.name: p.read_bytes() for p in target.parent.iterdir()} == snapshot
    with pytest.raises(sph.PickHistoryReadError):
        sph.read_picks(tmp_path)


def test_regressed_input_mtime_is_rejected(tmp_path):
    write_daily(tmp_path, T)
    rep = report({"s1": sph.HISTORY})
    rec(tmp_path, {"s1": result(["A.TWSE"])}, rep, at(T, 16, 30), mtime=200.0)

    out = rec(tmp_path, {"s1": result(["OLD.TWSE"])}, rep, at(T, 21, 40), mtime=150.0)
    assert out.rejected == {"s1": sph.REJECT_STALE_INPUT}
    assert symbols_of(tmp_path, "s1") == ["A.TWSE"]
    assert run_of(tmp_path, "s1")["input_mtime"] == 200.0

    out = rec(tmp_path, {"s1": result(["NEW.TWSE"])}, rep, at(T, 21, 45), mtime=200.0)
    assert out.written == ("s1",)


@pytest.mark.parametrize(
    ("next_partition", "fingerprint", "reason"),
    [
        (True, "fp-s1", sph.REJECT_HARD_FROZEN),
        (False, "edited", sph.REJECT_SOFT_FROZEN),
        (False, "fp-s1", sph.REJECT_STALE_INPUT),
    ],
)
def test_freeze_outranks_regressed_input_mtime(tmp_path, next_partition, fingerprint, reason):
    write_daily(tmp_path, T)
    rec(tmp_path, {"s1": result(["A.TWSE"])}, report({"s1": sph.HISTORY}), at(T, 16, 30), mtime=200.0)
    if next_partition:
        write_daily(tmp_path, THU)

    out = rec(
        tmp_path, {"s1": result(["OLD.TWSE"])}, report({"s1": sph.HISTORY}, {"s1": fingerprint}),
        at(THU, 10), mtime=150.0,
    )
    assert out.rejected == {"s1": reason}
    assert symbols_of(tmp_path, "s1") == ["A.TWSE"]


_RESTART_CHILD = """
import json, sys
from datetime import date, datetime, time
from pathlib import Path
from app.services import strategy_pick_history as sph
from app.taiwan.realtime.calendar import TAIPEI_TZ

data_dir = Path(sys.argv[1])
t, thu = date(2026, 9, 30), date(2026, 10, 1)
rows = lambda s: {"total": 1, "as_of": str(t), "rows": [{"symbol": s, "date": t}]}
rep = sph.RunReport(
    history=sph.InputStatus(state="ok", rows=5, exchanges_at_t=frozenset({"TWSE", "TPEX"}), dates=(t,)),
    input_groups={"s1": frozenset({sph.HISTORY})},
    fingerprints={"s1": sys.argv[2]},
)
out = sph.record(data_dir, t, {"s1": rows(sys.argv[3])}, rep, "restart", 100.0,
                 datetime.combine(thu, time(16, 30), TAIPEI_TZ))
print(json.dumps({"written": list(out.written), "rejected": out.rejected}))
"""


def run_in_fresh_process(data_dir: Path, fingerprint: str, symbol: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-c", _RESTART_CHILD, str(data_dir), fingerprint, symbol],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_restart_judges_only_from_disk(tmp_path):
    write_daily(tmp_path, T)
    rec(tmp_path, {"s1": result(["A.TWSE"])}, report({"s1": sph.HISTORY}), at(T, 16, 30))

    assert run_in_fresh_process(tmp_path, "fp-s1", "B.TWSE") == {"written": ["s1"], "rejected": {}}
    assert symbols_of(tmp_path, "s1") == ["B.TWSE"]
    assert run_of(tmp_path, "s1")["first_recorded_at"] == utc(T, 16, 30)

    before = partition_bytes(tmp_path)
    out = run_in_fresh_process(tmp_path, "different", "C.TWSE")
    assert out == {"written": [], "rejected": {"s1": sph.REJECT_SOFT_FROZEN}}
    assert partition_bytes(tmp_path) == before


def test_concurrent_writers_and_reader_never_see_a_torn_snapshot(tmp_path):
    write_daily(tmp_path, T)
    rep = report({"s1": sph.HISTORY, "s2": sph.HISTORY})
    payloads = {
        "x": {"s1": result([f"X{i}.TWSE" for i in range(40)]), "s2": result(["XX.TPEX"])},
        "y": {"s1": result([f"Y{i}.TPEX" for i in range(25)]), "s2": result([])},
    }
    expected = {
        name: {sid: [row["symbol"] for row in res["rows"]] for sid, res in payload.items()}
        for name, payload in payloads.items()
    }
    errors: list[BaseException] = []
    stop = threading.Event()
    start = threading.Barrier(3)

    def writer(name: str) -> None:
        try:
            start.wait()
            for _ in range(15):
                rec(tmp_path, payloads[name], rep, at(T, 16, 30))
        except BaseException as exc:
            errors.append(exc)

    def snapshot_state() -> dict[str, list[str]]:
        picks = sph.read_picks(tmp_path).picks
        assert not picks["manifest_missing"].any()
        state = {}
        for sid in ("s1", "s2"):
            rows = picks.filter(pl.col("strategy_id") == sid).sort("rank")
            assert rows["run_id"].n_unique() <= 1
            state[sid] = rows["symbol"].to_list()
        return state

    def reader() -> None:
        try:
            start.wait()
            while not stop.is_set():
                if part_path(tmp_path).exists():
                    snapshot_state()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in payloads]
    reader_thread = threading.Thread(target=reader)
    for t in [*threads, reader_thread]:
        t.start()
    for t in threads:
        t.join(timeout=120)
    stop.set()
    reader_thread.join(timeout=60)

    assert not errors, errors
    assert snapshot_state() in list(expected.values())


def test_reader_waits_until_inflight_manifest_is_replaced(tmp_path, monkeypatch):
    write_daily(tmp_path, T)
    rep = report({"s1": sph.HISTORY})
    rec(tmp_path, {"s1": result(["A.TWSE"])}, rep, at(T, 16, 30))
    real_write = sph._write_atomic
    seen: list[pl.DataFrame] = []
    reader_done_early: list[bool] = []

    def reader() -> None:
        seen.append(sph.read_picks(tmp_path).picks)

    def write_then_probe(frame, target):
        real_write(frame, target)
        if target.name == "part.parquet":
            thread = threading.Thread(target=reader)
            thread.start()
            thread.join(timeout=0.5)
            reader_done_early.append(not thread.is_alive())
            write_then_probe.thread = thread

    monkeypatch.setattr(sph, "_write_atomic", write_then_probe)
    rec(tmp_path, {"s1": result(["B.TWSE", "C.TWSE"])}, rep, at(T, 21, 40))
    write_then_probe.thread.join(timeout=30)

    assert reader_done_early == [False]
    assert seen[0]["symbol"].to_list() == ["B.TWSE", "C.TWSE"]
    assert not seen[0]["manifest_missing"].any()


@pytest.mark.parametrize(("first_write", "late"), [(at(FRI, 8, 59), False), (at(FRI, 9), True)])
def test_first_write_at_or_after_actual_next_open_is_late(tmp_path, first_write, late):
    write_daily(tmp_path, T)
    rec(tmp_path, {"s1": result(["A.TWSE"])}, report({"s1": sph.HISTORY}), first_write)
    assert run_of(tmp_path, "s1")["recorded_late"] is False

    write_daily(tmp_path, FRI)
    assert run_of(tmp_path, "s1")["recorded_late"] is late
    assert picks_of(tmp_path, "s1")["recorded_late"].to_list() == [late]


def test_as_date_accepts_date_datetime_and_iso_strings():
    assert sph._as_date(T) == T
    assert sph._as_date(datetime(2026, 9, 30, 13, 30)) == T
    assert sph._as_date("2026-09-30") == T
    assert sph._as_date("2026-09-30T00:00:00") == T
    with pytest.raises(TypeError):
        sph._as_date(None)


def test_rows_not_dated_as_of_are_dropped(tmp_path):
    write_daily(tmp_path, T)
    res = result(["A.TWSE", "B.TWSE", "C.TWSE"])
    res["rows"][1]["date"] = date(2026, 9, 29)
    del res["rows"][2]["date"]
    rec(tmp_path, {"s1": res}, report({"s1": sph.HISTORY}), at(T, 16, 30))
    assert symbols_of(tmp_path, "s1") == ["A.TWSE"]
    run = run_of(tmp_path, "s1")
    assert run["total"] == 3
    assert run["rows_written"] == 1


def test_result_with_other_as_of_is_not_written(tmp_path):
    write_daily(tmp_path, T)
    rep = report({"s1": sph.HISTORY})
    rec(tmp_path, {"s1": result(["A.TWSE"])}, rep, at(T, 16, 30))
    before = partition_bytes(tmp_path)

    out = rec(tmp_path, {"s1": result(["B.TWSE"], as_of=THU)}, rep, at(T, 21, 40))
    assert out.rejected == {"s1": sph.REJECT_AS_OF_MISMATCH}
    assert partition_bytes(tmp_path) == before


class _StubRunEngine:
    def list_strategies(self):
        return [{"id": "s1"}, {"id": "s2"}]

    def run_all(self, context, *, params_map=None, overrides_map=None, strategy_ids=None, skip_failed=False):
        rows = {
            "s1": [
                {"symbol": "2330.TWSE", "date": context.as_of, "raw_close": 1000.0, "score": 2.0},
                {"symbol": "6488.TPEX", "date": context.as_of, "raw_close": 500.5, "score": 1.0},
            ],
            "s2": [{"symbol": "2317.TWSE", "date": context.as_of, "raw_close": 200.0, "score": None}],
        }
        return {
            sid: StrategyResult(as_of=context.as_of, strategy_id=sid, rows=rows[sid], total=len(rows[sid]))
            for sid in strategy_ids
        }


def test_compute_and_cache_all_output_and_json_shapes_keep_all_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(
        screener_api.ScreenerService,
        "build_strategy_context",
        lambda self, engine, as_of, ids, **kwargs: types.SimpleNamespace(as_of=as_of),
    )
    repo = types.SimpleNamespace(store=types.SimpleNamespace(data_dir=tmp_path))
    as_of, results = screener_api.compute_and_cache_all(repo, _StubRunEngine(), as_of=T)
    assert isinstance(as_of, date)
    assert isinstance(results["s1"]["as_of"], str)
    assert isinstance(results["s1"]["rows"][0]["date"], date)

    write_daily(tmp_path, T)
    rep = report({"s1": sph.HISTORY, "s2": sph.HISTORY})
    out = rec(tmp_path, results, rep, at(T, 16, 30), as_of=as_of)
    assert out.written == ("s1", "s2")
    picks = sph.read_picks(tmp_path).picks.sort(["strategy_id", "rank"])
    assert picks["symbol"].to_list() == ["2330.TWSE", "6488.TPEX", "2317.TWSE"]
    assert picks["rank"].to_list() == [1, 2, 1]
    assert picks["signal_raw_close"].to_list() == [1000.0, 500.5, 200.0]
    assert picks["score"].to_list() == [2.0, 1.0, None]

    as_json = json.loads(json.dumps(results, default=strategy_cache._json_default))
    assert as_json["s1"]["rows"][0]["date"] == "2026-09-30"
    out = rec(tmp_path, as_json, rep, at(T, 21, 40), as_of=str(as_of))
    assert out.written == ("s1", "s2")
    assert sph.read_picks(tmp_path).runs["rows_written"].to_list() == [2, 1]


def _stub_strategy(path: Path | None, meta=None, *, backend="matrix_native", children=None, history_fn=None):
    return types.SimpleNamespace(
        meta=meta if meta is not None else {"id": "x", "params": []},
        basic_filter={"price_min": 3},
        file_path=path,
        execution_backend=backend,
        filter_history_fn=history_fn,
        composite=CompositeSpec(children=tuple(CompositeChild(c, 1.0) for c in children)) if children else None,
    )


class _StubDefEngine:
    def __init__(self, strategies):
        self.strategies = strategies

    def get(self, sid):
        if sid not in self.strategies:
            raise ValueError(f"unknown strategy: {sid}")
        return self.strategies[sid]


def test_fingerprint_changes_with_override_and_file(tmp_path):
    path = tmp_path / "s1.py"
    path.write_text("META = {}\n", encoding="utf-8")
    engine = _StubDefEngine({"s1": _stub_strategy(path)})

    base = sph.strategy_fingerprints(engine, {}, ["s1"])["s1"]
    assert base is not None
    assert sph.strategy_fingerprints(engine, {}, ["s1"])["s1"] == base
    overridden = sph.strategy_fingerprints(engine, {"s1": {"params": {"n": 5}}}, ["s1"])["s1"]
    assert overridden != base
    path.write_text("META = {}\nX = 1\n", encoding="utf-8")
    assert sph.strategy_fingerprints(engine, {}, ["s1"])["s1"] not in (base, overridden)


def test_fingerprint_without_override_files_does_not_raise(tmp_path):
    path = tmp_path / "s1.py"
    path.write_text("META = {}\n", encoding="utf-8")
    engine = _StubDefEngine({"s1": _stub_strategy(path)})
    overrides = strategy_config.list_overrides(tmp_path)
    assert overrides == {}
    assert sph.strategy_fingerprints(engine, overrides, ["s1"])["s1"] is not None


def test_unfingerprintable_strategies_are_none_and_others_still_record(tmp_path):
    good = tmp_path / "good.py"
    good.write_text("META = {}\n", encoding="utf-8")
    deleted = tmp_path / "deleted.py"
    engine = _StubDefEngine({
        "good": _stub_strategy(good),
        "set_meta": _stub_strategy(good, meta={"tags": {"a", "b"}}),
        "deleted": _stub_strategy(deleted),
        "no_file": _stub_strategy(None),
        "combo_ok": _stub_strategy(good, backend="composite", children=["good"]),
        "combo_bad": _stub_strategy(good, backend="composite", children=["good", "deleted"]),
        "unknown_child": _stub_strategy(good, backend="composite", children=["missing"]),
    })
    ids = list(engine.strategies)
    fps = sph.strategy_fingerprints(engine, {}, [*ids, "missing"])
    assert fps["good"] is not None
    assert fps["combo_ok"] is not None
    assert fps["combo_ok"] != fps["good"]
    for sid in ("set_meta", "deleted", "no_file", "combo_bad", "unknown_child", "missing"):
        assert fps[sid] is None, sid

    write_daily(tmp_path, T)
    rep = report(dict.fromkeys(ids, sph.HISTORY), fps)
    out = rec(tmp_path, {sid: result([f"{sid}.TWSE"]) for sid in ids}, rep, at(T, 16, 30))
    assert set(out.written) == set(ids)
    runs = sph.read_picks(tmp_path).runs
    assert runs.filter(pl.col("strategy_id") == "good")["strategy_fingerprint"].to_list() == [fps["good"]]
    assert runs.filter(pl.col("strategy_id") == "deleted")["strategy_fingerprint"].to_list() == [None]


def test_strategy_input_groups_follow_execution_backend():
    engine = _StubDefEngine({
        "mx": _stub_strategy(None, backend="matrix_native"),
        "legacy": _stub_strategy(None, backend="python_history_legacy", history_fn=lambda df, p: df),
        "px": _stub_strategy(None, backend="polars_expr"),
        "px_hist": _stub_strategy(None, backend="polars_expr", history_fn=lambda df, p: df),
        "combo": _stub_strategy(None, backend="composite", children=["mx", "px"]),
        "combo_px": _stub_strategy(None, backend="composite", children=["px"]),
    })
    groups = sph.strategy_input_groups(engine, [*engine.strategies, "missing"])
    assert groups == {
        "mx": frozenset({sph.HISTORY}),
        "legacy": frozenset({sph.HISTORY}),
        "px": frozenset({sph.CURRENT}),
        "px_hist": frozenset({sph.HISTORY}),
        "combo": frozenset({sph.HISTORY, sph.CURRENT}),
        "combo_px": frozenset({sph.CURRENT}),
    }


def test_read_picks_without_snapshots_returns_empty_frames(tmp_path):
    history = sph.read_picks(tmp_path)
    assert history.picks.height == 0
    assert history.runs.height == 0
    assert "manifest_missing" in history.picks.columns
    assert "recorded_late" in history.runs.columns


def test_read_picks_filters_by_signal_date_range(tmp_path):
    write_daily(tmp_path, T)
    write_daily(tmp_path, THU)
    rec(tmp_path, {"s1": result(["A.TWSE"])}, report({"s1": sph.HISTORY}), at(T, 16, 30))
    rec(
        tmp_path, {"s1": result(["B.TWSE"], as_of=THU)},
        report({"s1": sph.HISTORY}, history=status(dates=(THU,))), at(THU, 16, 30), as_of=THU,
    )
    assert sph.read_picks(tmp_path).picks.height == 2
    only_thu = sph.read_picks(tmp_path, start=THU).picks
    assert only_thu["signal_date"].to_list() == [THU]
