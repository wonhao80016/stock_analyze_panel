"""Coverage status and background historical backfill for Taiwan Institutional
and Margin datasets (三大法人 / 融資融券).

Background: the historical bootstrap zip (see bootstrap.py) only ships daily
OHLCV. Institutional and margin data only accumulate one day at a time via the
daily scheduled pipeline, so a fresh install (or an install that missed some
days) has near-empty coverage until this backfill is run.

Design mirrors TaiwanBootstrapService (bootstrap.py): a single background
thread per job, a process-wide single-active-job guard, and a polled job
state endpoint.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from app.taiwan.daily_update import resolve_target_latest_trading_date
from app.taiwan.institutional_margin_refresh import (
    TaiwanInstitutionalRefreshService,
    TaiwanMarginRefreshService,
)
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.realtime.calendar import TaiwanTradingCalendar

logger = logging.getLogger(__name__)

DEFAULT_BACKFILL_DAYS = 60
MAX_BACKFILL_DAYS = 250

_DATASET_LABEL = {"institutional": "三大法人", "margin": "融資融券"}


class TaiwanEnrichmentDatasetStatus(BaseModel):
    has_data: bool
    day_count: int
    earliest: str | None = None
    latest: str | None = None


class TaiwanEnrichmentStatus(BaseModel):
    institutional: TaiwanEnrichmentDatasetStatus
    margin: TaiwanEnrichmentDatasetStatus


def _dataset_status(dates: list[date]) -> TaiwanEnrichmentDatasetStatus:
    return TaiwanEnrichmentDatasetStatus(
        has_data=len(dates) > 0,
        day_count=len(dates),
        earliest=str(dates[0]) if dates else None,
        latest=str(dates[-1]) if dates else None,
    )


def get_enrichment_status(
    inst_store: TaiwanInstitutionalStore | None = None,
    margin_store: TaiwanMarginStore | None = None,
) -> TaiwanEnrichmentStatus:
    """Inspect local storage and report institutional/margin coverage."""
    i_store = inst_store or TaiwanInstitutionalStore()
    m_store = margin_store or TaiwanMarginStore()
    return TaiwanEnrichmentStatus(
        institutional=_dataset_status(i_store.available_dates()),
        margin=_dataset_status(m_store.available_dates()),
    )


class TaiwanEnrichmentBackfillRequest(BaseModel):
    days: int = Field(DEFAULT_BACKFILL_DAYS, ge=1, le=MAX_BACKFILL_DAYS)


class TaiwanEnrichmentBackfillJobState(BaseModel):
    job_id: str
    status: str = "pending"  # pending, running, success, failed
    stage: str = "準備中"
    progress: int = 0  # 0 to 100
    current_dataset: str | None = None  # institutional | margin
    current_date: str | None = None
    day_index: int = 0
    total_days: int = 0
    message: str = ""
    error: str | None = None
    result: dict[str, Any] | None = None
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


_jobs_lock = threading.Lock()
_jobs: dict[str, TaiwanEnrichmentBackfillJobState] = {}
_active_job_id: str | None = None


class TaiwanEnrichmentBackfillService:
    """Orchestrates a combined institutional + margin backfill as one background job."""

    def __init__(
        self,
        inst_service: TaiwanInstitutionalRefreshService | None = None,
        margin_service: TaiwanMarginRefreshService | None = None,
        calendar: TaiwanTradingCalendar | None = None,
    ) -> None:
        self.calendar = calendar or TaiwanTradingCalendar()
        self.inst_service = inst_service or TaiwanInstitutionalRefreshService(calendar=self.calendar)
        self.margin_service = margin_service or TaiwanMarginRefreshService(calendar=self.calendar)

    def _resolve_backfill_range(self, days: int) -> tuple[date, date, int]:
        """Walk back from the latest resolvable trading date to find the start of an
        N-trading-day window, using the same is_trading_day(...) is not False
        convention as refresh_dates itself (weekday with unconfirmed status still
        counts as an attempt)."""
        target = resolve_target_latest_trading_date(self.calendar)
        cur = target
        earliest = target
        count = 0
        while count < days:
            if self.calendar.is_trading_day(cur) is not False:
                count += 1
                earliest = cur
            if count >= days:
                break
            cur -= timedelta(days=1)
        return earliest, target, count

    def start_backfill(self, days: int = DEFAULT_BACKFILL_DAYS) -> str:
        """Start (or reuse) a background backfill job. Returns the job_id."""
        global _active_job_id
        with _jobs_lock:
            if _active_job_id and _active_job_id in _jobs:
                cur_job = _jobs[_active_job_id]
                if cur_job.status not in ("success", "failed"):
                    return cur_job.job_id

            job_id = str(uuid.uuid4())
            job = TaiwanEnrichmentBackfillJobState(
                job_id=job_id,
                status="pending",
                stage="準備中",
                message=f"準備回補近 {days} 個交易日的三大法人與融資融券資料...",
            )
            _jobs[job_id] = job
            _active_job_id = job_id

        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, days),
            daemon=True,
            name=f"taiwan-enrich-backfill-{job_id[:8]}",
        )
        thread.start()
        return job_id

    def get_job(self, job_id: str) -> TaiwanEnrichmentBackfillJobState | None:
        with _jobs_lock:
            return _jobs.get(job_id)

    def _update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        progress: int | None = None,
        current_dataset: str | None = None,
        current_date: str | None = None,
        day_index: int | None = None,
        total_days: int | None = None,
        message: str | None = None,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if not job:
                return
            if status is not None:
                job.status = status
            if stage is not None:
                job.stage = stage
            if progress is not None:
                job.progress = max(0, min(100, progress))
            if current_dataset is not None:
                job.current_dataset = current_dataset
            if current_date is not None:
                job.current_date = current_date
            if day_index is not None:
                job.day_index = day_index
            if total_days is not None:
                job.total_days = total_days
            if message is not None:
                job.message = message
            if error is not None:
                job.error = error
            if result is not None:
                job.result = result
            job.updated_at = datetime.now().isoformat()

    def _make_progress_cb(self, job_id: str, dataset: str, base_pct: int, span_pct: int, total_days: int):
        label = _DATASET_LABEL[dataset]

        def _cb(cur_date: date, idx: int) -> None:
            pct = base_pct + int(span_pct * idx / total_days) if total_days > 0 else base_pct
            self._update_job(
                job_id,
                current_dataset=dataset,
                current_date=str(cur_date),
                day_index=idx,
                progress=min(base_pct + span_pct, pct),
                stage=f"回補{label}",
                message=f"正在回補{label}: {cur_date} ({idx}/{total_days})",
            )

        return _cb

    def _run_job(self, job_id: str, days: int) -> None:
        logger.info("Starting Taiwan enrichment backfill job %s (days=%d)", job_id, days)
        try:
            start_date, end_date, total_days = self._resolve_backfill_range(days)
            self._update_job(
                job_id,
                status="running",
                stage="計算回補範圍",
                progress=2,
                total_days=total_days,
                message=f"回補範圍: {start_date} ~ {end_date} (共 {total_days} 個交易日)",
            )

            self._update_job(job_id, current_dataset="institutional", stage="回補三大法人", progress=5)
            inst_stats = self.inst_service.refresh_dates(
                start_date,
                end_date,
                on_progress=self._make_progress_cb(job_id, "institutional", 5, 45, total_days),
            )

            self._update_job(job_id, current_dataset="margin", stage="回補融資融券", progress=50)
            margin_stats = self.margin_service.refresh_dates(
                start_date,
                end_date,
                on_progress=self._make_progress_cb(job_id, "margin", 50, 45, total_days),
            )

            result = {"institutional": inst_stats, "margin": margin_stats}
            message = (
                f"回補完成。三大法人: 成功 {inst_stats['dates_fetched']} / "
                f"跳過 {inst_stats['dates_skipped']} / 失敗 {len(inst_stats['failed_dates'])}；"
                f"融資融券: 成功 {margin_stats['dates_fetched']} / "
                f"跳過 {margin_stats['dates_skipped']} / 失敗 {len(margin_stats['failed_dates'])}。"
            )
            self._update_job(
                job_id,
                status="success",
                stage="已完成",
                progress=100,
                current_dataset=None,
                message=message,
                result=result,
            )
            logger.info("Taiwan enrichment backfill job %s completed: %s", job_id, message)
        except Exception as exc:
            logger.exception("Taiwan enrichment backfill job %s failed: %s", job_id, exc)
            self._update_job(
                job_id,
                status="failed",
                stage="失敗",
                message=f"回補失敗: {exc}",
                error=str(exc),
            )
