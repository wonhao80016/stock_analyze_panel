"""Integrity report for the recent Taiwan daily partitions.

The checked days are the dates with a stored TAIEX bar or a stored daily partition,
before today and from the first stored daily partition on, so holidays never count
as gaps and a lagging index does not hide the newest partitions. Each day is checked for:

- missing: a TAIEX date without a daily partition
- one_exchange: the partition lacks TWSE or TPEx rows (or is unreadable)
- provisional: the partition was last written on the day itself before the daily final
  cutoff (settings.taiwan_daily_final_cutoff, Taipei time), except the newest stored day,
  which stays provisional until an update rewrites it at or after the cutoff
- enriched_stale: the enriched partition is absent or older than the daily partition

The report only feeds logs: nothing is repaired or blocked here. The legacy A-share
kline_* daily tables are not read, since Taiwan bars live in taiwan/daily.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from app.taiwan.benchmarks import TAIEX
from app.taiwan.daily_store import REPAIR_WINDOW_PARTITIONS, TaiwanDailyStore
from app.taiwan.enriched_reader import ENRICHED_DIR_NAME
from app.taiwan.realtime.calendar import taipei_today

logger = logging.getLogger(__name__)

SCAN_WINDOW_SESSIONS = REPAIR_WINDOW_PARTITIONS

DAILY_TABLE = "taiwan_daily"
ENRICHED_TABLE = ENRICHED_DIR_NAME

KIND_ORDER = ("missing", "one_exchange", "provisional", "enriched_stale")


@dataclass(frozen=True)
class IntegrityIssue:
    day: date
    table: str
    kind: str


@dataclass(frozen=True)
class IntegrityReport:
    checked: tuple[date, ...]
    index_through: date | None
    issues: tuple[IntegrityIssue, ...]


def _has_taiex(index_dir: Path, d: date) -> bool:
    path = index_dir / f"date={d.isoformat()}" / "part.parquet"
    try:
        symbols = pl.read_parquet(path, columns=["symbol"]).get_column("symbol")
    except Exception as exc:
        logger.warning("integrity calendar skipped %s: %s", path, exc)
        return False
    return bool((symbols == TAIEX.symbol).any())


def _checked_days(
    daily_dates: list[date], index_dir: Path, today: date, sessions: int
) -> tuple[list[date], date | None]:
    """The newest *sessions* days in [first daily partition, today) and the newest TAIEX date among them."""
    # TaiwanDailyStore creates its directory on construction; a report must not.
    index_dates = TaiwanDailyStore(index_dir).available_dates() if index_dir.is_dir() else []
    daily_set = set(daily_dates)
    index_set = set(index_dates)
    first = daily_dates[0]
    candidates = sorted({d for d in daily_set | index_set if first <= d < today}, reverse=True)
    checked: list[date] = []
    index_through: date | None = None
    for d in candidates:
        if len(checked) >= sessions:
            break
        on_index = d in index_set and _has_taiex(index_dir, d)
        if on_index and index_through is None:
            index_through = d
        if on_index or d in daily_set:
            checked.append(d)
    return sorted(checked), index_through


def _enriched_mtime(enriched_dir: Path, d: date) -> float | None:
    try:
        return (enriched_dir / f"date={d.isoformat()}" / "part.parquet").stat().st_mtime
    except OSError:
        return None


def integrity_report(
    data_dir: Path,
    *,
    today: date | None = None,
    sessions: int = SCAN_WINDOW_SESSIONS,
) -> IntegrityReport:
    """Check the newest *sessions* trading days before *today* (Taipei); issues sorted by day."""
    data_dir = Path(data_dir)
    today = today or taipei_today()
    daily_dir = data_dir / "taiwan" / "daily"
    if not daily_dir.is_dir():
        return IntegrityReport((), None, ())
    daily_store = TaiwanDailyStore(daily_dir)
    stored = daily_store.available_dates()
    if not stored:
        return IntegrityReport((), None, ())

    checked, index_through = _checked_days(
        stored, data_dir / "taiwan" / "index_daily", today, sessions
    )
    if not checked:
        return IntegrityReport((), index_through, ())
    stored_set = set(stored)
    present = [d for d in checked if d in stored_set]
    issues = [IntegrityIssue(d, DAILY_TABLE, "missing") for d in checked if d not in stored_set]
    issues += [
        IntegrityIssue(d, DAILY_TABLE, "one_exchange")
        for d in daily_store.incomplete_dates(present)
    ]
    if present:
        issues += [
            IntegrityIssue(d, DAILY_TABLE, "provisional")
            for d in daily_store.provisional_dates(present[:-1])
        ]

    enriched_dir = data_dir / ENRICHED_DIR_NAME
    for d in present:
        bars_written = daily_store.partition_mtime(d)
        published = _enriched_mtime(enriched_dir, d)
        if published is None or (bars_written is not None and bars_written > published):
            issues.append(IntegrityIssue(d, ENRICHED_TABLE, "enriched_stale"))

    issues.sort(key=lambda i: (i.day, KIND_ORDER.index(i.kind)))
    return IntegrityReport(tuple(checked), index_through, tuple(issues))


def describe_issues(issues: list[IntegrityIssue]) -> str:
    """One line per kind present, e.g. "missing: 2026-09-21; provisional: 2026-09-22"."""
    parts = []
    for kind in KIND_ORDER:
        days = sorted({i.day for i in issues if i.kind == kind})
        if days:
            parts.append(f"{kind}: {', '.join(d.isoformat() for d in days)}")
    return "; ".join(parts)


def boot_integrity_check(app_state, *, today: date | None = None) -> None:
    """Log the integrity report at startup; nothing is repaired or blocked."""
    repo = getattr(app_state, "repo", None)
    if repo is None:
        return
    try:
        report = integrity_report(repo.store.data_dir, today=today)
    except Exception as e:
        logger.warning("boot integrity scan failed: %s", e)
        return
    if not report.checked:
        logger.info(
            "boot integrity check: no stored Taiwan daily partitions before today, nothing checked"
        )
        return
    scope = (
        f"{len(report.checked)} Taiwan trading days "
        f"{report.checked[0].isoformat()}..{report.checked[-1].isoformat()}, "
        + (
            f"TAIEX calendar through {report.index_through.isoformat()}"
            if report.index_through is not None
            else "no TAIEX bars in the checked range, missing days not detectable"
        )
    )
    if report.issues:
        logger.warning(
            "boot integrity check (%s): %d issue(s): %s",
            scope,
            len(report.issues),
            describe_issues(list(report.issues)),
        )
    else:
        logger.info("boot integrity check (%s): no issues found", scope)
