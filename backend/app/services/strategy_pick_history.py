"""Post-close strategy pick snapshots: user_data/strategy_picks/date=YYYY-MM-DD/.

part.parquet holds pick rows; runs.parquet holds one manifest row per strategy.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from app.enriched_generation import _replace_with_retry
from app.services.strategy_inputs import CURRENT, HISTORY, InputStatus
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.realtime.calendar import REGULAR_OPEN_START, TAIPEI_TZ, taipei_now

logger = logging.getLogger(__name__)

REQUIRED_EXCHANGES = frozenset({"TWSE", "TPEX"})
_HISTORY_BACKENDS = frozenset({"matrix_native", "python_history_legacy"})

REJECT_AS_OF_MISMATCH = "as_of_mismatch"
REJECT_INPUT_UNAVAILABLE = "input_unavailable"
REJECT_HARD_FROZEN = "hard_frozen"
REJECT_SOFT_FROZEN = "soft_frozen"
REJECT_STALE_INPUT = "stale_input"

PICKS_FILENAME = "part.parquet"
RUNS_FILENAME = "runs.parquet"

PICK_SCHEMA: dict[str, pl.DataType] = {
    "signal_date": pl.Date(),
    "strategy_id": pl.Utf8(),
    "symbol": pl.Utf8(),
    "run_id": pl.Utf8(),
    "rank": pl.Int32(),
    "score": pl.Float64(),
    "signal_raw_close": pl.Float64(),
}

RUN_SCHEMA: dict[str, pl.DataType] = {
    "strategy_id": pl.Utf8(),
    "run_id": pl.Utf8(),
    "total": pl.Int64(),
    "rows_written": pl.Int64(),
    "strategy_fingerprint": pl.Utf8(),
    "input_mtime": pl.Float64(),
    "universe_complete": pl.Boolean(),
    "history_degraded": pl.Boolean(),
    "first_recorded_at": pl.Datetime("us", "UTC"),
    "last_recorded_at": pl.Datetime("us", "UTC"),
    "run_label": pl.Utf8(),
    "provenance": pl.Utf8(),
    "calculation_status": pl.Utf8(),
    "missing_fields": pl.Utf8(),
}

_MANIFEST_COLUMNS = (
    "strategy_fingerprint",
    "universe_complete",
    "history_degraded",
    "first_recorded_at",
    "recorded_late",
    "provenance",
    "calculation_status",
    "missing_fields",
)

# Readers take this lock per partition so they never pair a new part.parquet with an old runs.parquet.
_lock = threading.Lock()


class PickHistoryReadError(RuntimeError):
    """An existing snapshot partition could not be read."""


@dataclass
class RunReport:
    """Inputs of one compute_and_cache_all run; history is None when it was not requested.

    input_groups maps each strategy id to the loaders it reads ({HISTORY}, {CURRENT} or both).
    """

    current: InputStatus = field(default_factory=InputStatus)
    history: InputStatus | None = None
    input_groups: dict[str, frozenset[str]] = field(default_factory=dict)
    fingerprints: dict[str, str | None] = field(default_factory=dict)
    calculation_statuses: dict[str, str] = field(default_factory=dict)
    missing_fields: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class RecordOutcome:
    written: tuple[str, ...]
    rejected: dict[str, str]


@dataclass(frozen=True)
class PickHistory:
    """PICK_SCHEMA/RUN_SCHEMA plus flags; manifest-derived columns are null where manifest_missing."""

    picks: pl.DataFrame
    runs: pl.DataFrame


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return datetime.fromisoformat(value).date()
    raise TypeError(f"cannot interpret {type(value).__name__} as a date")


def strategy_input_groups(engine, ids: Iterable[str]) -> dict[str, frozenset[str]]:
    """Map each strategy to the loaders its execution backend reads; unresolvable ids are omitted."""
    groups: dict[str, frozenset[str]] = {}
    for sid in ids:
        try:
            groups[sid] = _input_groups_for(engine, sid, frozenset())
        except Exception:
            logger.exception("strategy pick history: cannot resolve input groups for %s", sid)
    return groups


def _input_groups_for(engine, sid: str, visiting: frozenset[str]) -> frozenset[str]:
    if sid in visiting:
        raise ValueError(f"composite cycle at {sid}")
    strategy = engine.get(sid)
    backend = strategy.execution_backend
    if backend == "composite":
        children = strategy.composite.children
        result: frozenset[str] = frozenset()
        for child in children:
            result |= _input_groups_for(engine, child.strategy_id, visiting | {sid})
        return result
    if backend in _HISTORY_BACKENDS or strategy.filter_history_fn is not None:
        return frozenset({HISTORY})
    return frozenset({CURRENT})


def strategy_fingerprints(
    engine, all_overrides: Mapping[str, Any], ids: Iterable[str]
) -> dict[str, str | None]:
    """sha256 over META, basic_filter, overrides, file hash and children; None on any failure."""
    fingerprints: dict[str, str | None] = {}
    for sid in ids:
        try:
            fingerprints[sid] = _fingerprint(engine, all_overrides, sid, frozenset())
        except Exception:
            logger.exception("strategy pick history: fingerprint failed for %s", sid)
            fingerprints[sid] = None
    return fingerprints


def _fingerprint(
    engine, all_overrides: Mapping[str, Any], sid: str, visiting: frozenset[str]
) -> str | None:
    if sid in visiting:
        raise ValueError(f"composite cycle at {sid}")
    strategy = engine.get(sid)
    if strategy.file_path is None:
        return None
    file_sha = hashlib.sha256(Path(strategy.file_path).read_bytes()).hexdigest()
    children: dict[str, str] | None = None
    if strategy.composite is not None:
        children = {}
        for child in strategy.composite.children:
            child_fp = _fingerprint(engine, all_overrides, child.strategy_id, visiting | {sid})
            if child_fp is None:
                return None
            children[child.strategy_id] = child_fp
    payload = {
        "meta": strategy.meta,
        "basic_filter": strategy.basic_filter,
        "overrides": all_overrides.get(sid, {}),
        "file_sha256": file_sha,
        "children": children,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def predicted_open(signal_date: date) -> datetime:
    """First weekday after the signal day at the regular open, Taipei time."""
    d = signal_date + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return datetime.combine(d, REGULAR_OPEN_START, TAIPEI_TZ)


def _partition_dir(data_dir: Path, signal_date: date) -> Path:
    return Path(data_dir) / "user_data" / "strategy_picks" / f"date={signal_date.isoformat()}"


def _daily_dates(data_dir: Path) -> list[date]:
    daily_dir = Path(data_dir) / "taiwan" / "daily"
    if not daily_dir.is_dir():
        return []
    return TaiwanDailyStore(daily_dir).available_dates()


def _empty(schema: Mapping[str, pl.DataType]) -> pl.DataFrame:
    return pl.DataFrame(schema=dict(schema))


def _read_frame(path: Path, schema: Mapping[str, pl.DataType]) -> pl.DataFrame:
    if not path.exists():
        return _empty(schema)
    try:
        frame = pl.read_parquet(path)
        for name, default in (("provenance", "recorded"), ("calculation_status", "ok"), ("missing_fields", "[]")):
            if name in schema and name not in frame.columns:
                frame = frame.with_columns(pl.lit(default).alias(name))
        missing = [name for name in schema if name not in frame.columns]
        if missing:
            raise ValueError(f"missing columns {missing}")
        return frame.select([pl.col(name).cast(dtype) for name, dtype in schema.items()])
    except Exception as exc:
        raise PickHistoryReadError(f"cannot read {path}: {exc}") from exc


def _read_partition(part_dir: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    return (
        _read_frame(part_dir / PICKS_FILENAME, PICK_SCHEMA),
        _read_frame(part_dir / RUNS_FILENAME, RUN_SCHEMA),
    )


def _manifest_missing(picks: pl.DataFrame, runs: pl.DataFrame) -> pl.DataFrame:
    """Per strategy in one partition: True unless all pick rows carry the manifest run_id and count matches."""
    counts = picks.group_by("strategy_id").agg(
        pl.len().cast(pl.Int64).alias("_n"),
        pl.col("run_id").n_unique().alias("_ids"),
        pl.col("run_id").first().alias("_pick_run_id"),
    )
    joined = runs.select("strategy_id", "run_id", "rows_written").join(
        counts, on="strategy_id", how="full", coalesce=True
    )
    n = pl.col("_n").fill_null(0)
    mismatch = (
        pl.col("run_id").is_null()
        | (n != pl.col("rows_written"))
        | ((n > 0) & ((pl.col("_ids") != 1) | (pl.col("_pick_run_id") != pl.col("run_id"))))
    )
    return joined.select("strategy_id", mismatch.fill_null(True).alias("manifest_missing"))


def _write_atomic(frame: pl.DataFrame, target: Path) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.stem}.", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        frame.write_parquet(tmp)
        _replace_with_retry(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rows_for_date(rows: Iterable[Any], signal_date: date) -> tuple[list[dict], int]:
    kept: list[dict] = []
    dropped = 0
    for row in rows:
        try:
            matches = (
                isinstance(row, Mapping)
                and row.get("symbol")
                and _as_date(row.get("date")) == signal_date
            )
        except (TypeError, ValueError):
            matches = False
        if matches:
            kept.append(row)
        else:
            dropped += 1
    return kept, dropped


def _group_available(report: RunReport, group: str, signal_date: date) -> bool:
    if group == HISTORY:
        status = report.history
        return status is not None and status.state == "ok" and signal_date in status.dates
    if group == CURRENT:
        status = report.current
        return status.state in ("ok", "provided") and status.rows > 0
    return False


def _status_for(report: RunReport, group: str) -> InputStatus | None:
    return report.history if group == HISTORY else report.current


def _universe_complete(report: RunReport, groups: frozenset[str]) -> bool:
    for group in groups:
        status = _status_for(report, group)
        if status is None or not set(status.exchanges_at_t) >= REQUIRED_EXCHANGES:
            return False
    return True


def _history_degraded(report: RunReport, groups: frozenset[str], signal_date: date) -> bool:
    return (
        HISTORY not in groups
        and report.history is not None
        and not _group_available(report, HISTORY, signal_date)
    )


def _overwrite_rejection(
    old_run: Mapping[str, Any] | None,
    valid: bool,
    new_fingerprint: str | None,
    input_mtime: float | None,
    hard_frozen: bool,
    soft_frozen: bool,
) -> str | None:
    if hard_frozen:
        return REJECT_HARD_FROZEN
    if soft_frozen:
        old_fingerprint = old_run.get("strategy_fingerprint") if old_run and valid else None
        if new_fingerprint is None or old_fingerprint is None or new_fingerprint != old_fingerprint:
            return REJECT_SOFT_FROZEN
    old_mtime = old_run.get("input_mtime") if old_run else None
    if input_mtime is not None and old_mtime is not None and input_mtime < old_mtime:
        return REJECT_STALE_INPUT
    return None


def record(
    data_dir: Path,
    as_of: Any,
    results: Mapping[str, Mapping[str, Any]],
    run_report: RunReport,
    run_label: str,
    input_mtime: float | None,
    now: datetime | None = None,
    *,
    provenance: str = "recorded",
) -> RecordOutcome:
    """Replace (signal day, strategy) snapshots that pass availability and freeze rules.

    Raises PickHistoryReadError on unreadable files and OSError on a failed replace.
    """
    signal_date = _as_date(as_of)
    if provenance not in ("recorded", "reconstructed"):
        raise ValueError("unknown snapshot provenance")
    now = taipei_now() if now is None else now
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(UTC)
    run_id = uuid.uuid4().hex
    rejected: dict[str, str] = {}

    candidates: dict[str, tuple[int, list[dict], frozenset[str]]] = {}
    for raw_sid, result in results.items():
        sid = str(raw_sid)
        result_as_of = result.get("as_of")
        if result_as_of is not None and _as_date(result_as_of) != signal_date:
            rejected[sid] = REJECT_AS_OF_MISMATCH
            logger.warning(
                "strategy pick history: %s result as_of %s != %s", sid, result_as_of, signal_date
            )
            continue
        groups = frozenset(run_report.input_groups.get(sid) or ())
        if provenance == "reconstructed" and not run_report.fingerprints.get(sid):
            rejected[sid] = "fingerprint_unavailable"
            continue
        if not groups or not all(_group_available(run_report, g, signal_date) for g in groups):
            rejected[sid] = REJECT_INPUT_UNAVAILABLE
            logger.warning(
                "strategy pick history: %s kept previous snapshot for %s, inputs %s unavailable",
                sid, signal_date, sorted(groups),
            )
            continue
        rows, dropped = _rows_for_date(result.get("rows") or [], signal_date)
        if dropped:
            logger.warning(
                "strategy pick history: %s dropped %d rows not dated %s", sid, dropped, signal_date
            )
        total = result.get("total")
        candidates[sid] = (int(total) if total is not None else len(rows), rows, groups)

    if not candidates:
        return RecordOutcome(written=(), rejected=rejected)

    hard_frozen = any(d > signal_date for d in _daily_dates(data_dir))
    soft_frozen = now >= predicted_open(signal_date)
    part_dir = _partition_dir(data_dir, signal_date)

    with _lock:
        old_picks, old_runs = _read_partition(part_dir)
        missing = dict(_manifest_missing(old_picks, old_runs).iter_rows())
        old_by_sid = {row["strategy_id"]: row for row in old_runs.iter_rows(named=True)}

        new_picks: list[dict] = []
        new_runs: list[dict] = []
        written: list[str] = []
        for sid, (total, rows, groups) in candidates.items():
            old_run = old_by_sid.get(sid)
            fingerprint = run_report.fingerprints.get(sid)
            reconstruct_retry = (
                provenance == "reconstructed" and old_run is not None and not missing.get(sid, True)
                and old_run.get("provenance") == "reconstructed"
                and old_run.get("calculation_status") == "input_unavailable"
            )
            if provenance == "reconstructed" and sid in missing and not reconstruct_retry:
                rejected[sid] = "existing_snapshot"
                continue
            if sid in missing and not reconstruct_retry:
                reason = _overwrite_rejection(
                    old_run, not missing[sid], fingerprint, input_mtime, hard_frozen, soft_frozen
                )
                if reason is not None:
                    rejected[sid] = reason
                    logger.warning(
                        "strategy pick history: rejected overwrite of %s on %s (%s)",
                        sid, signal_date, reason,
                    )
                    continue
            for rank, row in enumerate(rows, start=1):
                new_picks.append({
                    "signal_date": signal_date,
                    "strategy_id": sid,
                    "symbol": str(row["symbol"]),
                    "run_id": run_id,
                    "rank": rank,
                    "score": _float_or_none(row.get("score")),
                    "signal_raw_close": _float_or_none(row.get("raw_close")),
                })
            new_runs.append({
                "strategy_id": sid,
                "run_id": run_id,
                "total": total,
                "rows_written": len(rows),
                "strategy_fingerprint": fingerprint,
                "input_mtime": input_mtime,
                "universe_complete": _universe_complete(run_report, groups),
                "history_degraded": _history_degraded(run_report, groups, signal_date),
                "first_recorded_at": old_run["first_recorded_at"] if old_run else now_utc,
                "last_recorded_at": now_utc,
                "run_label": run_label,
                "provenance": provenance,
                "calculation_status": run_report.calculation_statuses.get(sid, "ok"),
                "missing_fields": json.dumps(run_report.missing_fields.get(sid, [])),
            })
            written.append(sid)

        if written:
            replaced = pl.col("strategy_id").is_in(written)
            picks = pl.concat([
                old_picks.filter(~replaced),
                pl.DataFrame(new_picks, schema=PICK_SCHEMA),
            ])
            runs = pl.concat([
                old_runs.filter(~replaced),
                pl.DataFrame(new_runs, schema=RUN_SCHEMA),
            ])
            part_dir.mkdir(parents=True, exist_ok=True)
            _write_atomic(picks.sort(["strategy_id", "rank"]), part_dir / PICKS_FILENAME)
            _write_atomic(runs.sort("strategy_id"), part_dir / RUNS_FILENAME)

    logger.info(
        "strategy pick history: %s %s wrote %d strategies, rejected %d",
        signal_date, run_label, len(written), len(rejected),
    )
    return RecordOutcome(written=tuple(written), rejected=rejected)


def read_picks(
    data_dir: Path, start: date | None = None, end: date | None = None
) -> PickHistory:
    """Read snapshots; raises PickHistoryReadError on unreadable files.

    recorded_late: first_recorded_at >= 09:00 Taipei of the first daily partition after T.
    """
    root = Path(data_dir) / "user_data" / "strategy_picks"
    signal_dates: list[date] = []
    if root.is_dir():
        for child in root.glob("date=*"):
            try:
                d = date.fromisoformat(child.name.split("=", 1)[1])
            except (ValueError, IndexError):
                continue
            if (start is None or d >= start) and (end is None or d <= end):
                signal_dates.append(d)
    signal_dates.sort()
    daily_dates = _daily_dates(data_dir) if signal_dates else []

    pick_frames: list[pl.DataFrame] = []
    run_frames: list[pl.DataFrame] = []
    for signal_date in signal_dates:
        part_dir = _partition_dir(data_dir, signal_date)
        with _lock:
            picks, runs = _read_partition(part_dir)
        status = _manifest_missing(picks, runs)
        next_day = next((d for d in daily_dates if d > signal_date), None)
        late_cutoff = (
            datetime.combine(next_day, REGULAR_OPEN_START, TAIPEI_TZ) if next_day else None
        )
        late = (
            pl.col("first_recorded_at")
            >= pl.lit(late_cutoff.astimezone(UTC), dtype=pl.Datetime("us", "UTC"))
            if late_cutoff is not None
            else pl.lit(False)
        )
        runs_out = (
            runs.join(status, on="strategy_id", how="left")
            .with_columns(
                pl.lit(signal_date).alias("signal_date"),
                pl.col("manifest_missing").fill_null(True),
                late.alias("recorded_late"),
            )
            .with_columns(
                [
                    pl.when(pl.col("manifest_missing")).then(None).otherwise(pl.col(name)).alias(name)
                    for name in _MANIFEST_COLUMNS
                ]
            )
        )
        picks_out = (
            picks.join(
                runs_out.select("strategy_id", *_MANIFEST_COLUMNS),
                on="strategy_id",
                how="left",
            )
            .join(status, on="strategy_id", how="left")
            .with_columns(pl.col("manifest_missing").fill_null(True))
        )
        pick_frames.append(picks_out)
        run_frames.append(runs_out)

    if not pick_frames:
        empty_picks = _empty(PICK_SCHEMA).with_columns(
            pl.lit(None, dtype=pl.Utf8).alias("strategy_fingerprint"),
            pl.lit(None, dtype=pl.Boolean).alias("universe_complete"),
            pl.lit(None, dtype=pl.Boolean).alias("history_degraded"),
            pl.lit(None, dtype=pl.Datetime("us", "UTC")).alias("first_recorded_at"),
            pl.lit(None, dtype=pl.Boolean).alias("recorded_late"),
            pl.lit(None, dtype=pl.Boolean).alias("manifest_missing"),
            pl.lit(None, dtype=pl.Utf8).alias("provenance"),
            pl.lit(None, dtype=pl.Utf8).alias("calculation_status"),
            pl.lit(None, dtype=pl.Utf8).alias("missing_fields"),
        )
        empty_runs = _empty(RUN_SCHEMA).with_columns(
            pl.lit(None, dtype=pl.Boolean).alias("manifest_missing"),
            pl.lit(None, dtype=pl.Date).alias("signal_date"),
            pl.lit(None, dtype=pl.Boolean).alias("recorded_late"),
        )
        return PickHistory(picks=empty_picks, runs=empty_runs)
    return PickHistory(
        picks=pl.concat(pick_frames, how="vertical_relaxed"),
        runs=pl.concat(run_frames, how="vertical_relaxed"),
    )
