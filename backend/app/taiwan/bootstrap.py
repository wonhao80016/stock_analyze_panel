"""Taiwan Market Historical Daily Data Bootstrap Service.

Provides one-click download, SHA256 integrity verification, safe extraction
(anti-Zip Slip), Parquet structure validation, atomic import to data/taiwan,
and automatic post-bootstrap incremental sync to the latest trading day.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import httpx
import polars as pl
from pydantic import BaseModel, Field

from app.config import temp_root
from app.taiwan.daily_refresh import TaiwanDailyRefreshService
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.daily_update import (
    ChipDataRefreshResult,
    DatasetRefreshStats,
    MasterRefreshOutcome,
    TaiwanDailyUpdateService,
    master_refresh_due,
    pending_listing_dates,
    publish_enriched,
    refresh_security_master,
    resolve_margin_target,
    resolve_target_latest_trading_date,
)
from app.taiwan.data_root import taiwan_data_root
from app.taiwan.realtime.calendar import TAIPEI_TZ, TaiwanTradingCalendar, taipei_now
from app.taiwan.universe import TaiwanSecurityMaster, get_security_master
from app.taiwan.universe.service import write_atomically

logger = logging.getLogger(__name__)

# Canonical GitHub Release metadata
GITHUB_REPO = "Itoy1212/TWstockfor_tick-stock-panel"
RELEASE_TAG = "data-daily-2026-09-10"
ASSET_NAME = "Itoy-tw-daily-2024-01-02_to_2026-09-10.zip"
EXPECTED_SHA256 = "42e9535214197309f515750e3bc69c58eb6a81b7d8f4eed06e1d363216fa8358"
DOWNLOAD_URL = f"https://github.com/{GITHUB_REPO}/releases/download/{RELEASE_TAG}/{ASSET_NAME}"
ASSET_APPROX_SIZE_MB = 31
DATASET_DATE_RANGE = "2024-01-02 ~ 2026-09-10"


class TaiwanHistoryStatus(BaseModel):
    has_data: bool
    earliest_date: str | None = None
    latest_date: str | None = None
    trading_days: int = 0
    needs_bootstrap: bool = True
    asset_info: dict[str, Any] = Field(default_factory=dict)


class BootstrapJobState(BaseModel):
    job_id: str
    status: str = "pending"  # pending, downloading, verifying, extracting, importing, syncing, success, failed
    stage: str = "準備中"
    progress: int = 0  # 0 to 100
    message: str = ""
    error: str | None = None
    bytes_downloaded: int = 0
    total_bytes: int = 0
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


_jobs_lock = threading.Lock()
_jobs: dict[str, BootstrapJobState] = {}
_active_job_id: str | None = None


def get_history_status(store: TaiwanDailyStore | None = None) -> TaiwanHistoryStatus:
    """Inspect local storage and return historical daily data status."""
    st = store or TaiwanDailyStore()
    dates = st.available_dates()
    has_data = len(dates) > 0
    earliest = str(dates[0]) if dates else None
    latest = str(dates[-1]) if dates else None
    trading_days = len(dates)

    # Consider bootstrap needed if no data or very few trading days (< 20)
    needs_bootstrap = (not has_data) or (trading_days < 20)

    asset_info = {
        "repo": GITHUB_REPO,
        "tag": RELEASE_TAG,
        "asset": ASSET_NAME,
        "sha256": EXPECTED_SHA256,
        "download_url": DOWNLOAD_URL,
        "approx_size_mb": ASSET_APPROX_SIZE_MB,
        "date_range": DATASET_DATE_RANGE,
    }

    return TaiwanHistoryStatus(
        has_data=has_data,
        earliest_date=earliest,
        latest_date=latest,
        trading_days=trading_days,
        needs_bootstrap=needs_bootstrap,
        asset_info=asset_info,
    )


def safe_extract_zip(zip_path: Path, extract_dir: Path) -> None:
    """Extract zip archive with strict Zip Slip / Path Traversal protection."""
    import zipfile

    resolved_extract = extract_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as z:
        for member in z.infolist():
            filename = member.filename
            # Disallow absolute paths and root prefixes
            if os.path.isabs(filename) or filename.startswith(("/", "\\")):
                raise ValueError(f"Zip Slip attack detected: absolute path {filename}")
            if ":" in filename:  # Windows drive letter
                raise ValueError(f"Zip Slip attack detected: drive letter in {filename}")

            target_path = (extract_dir / filename).resolve()
            # Ensure path is strictly within target directory
            try:
                if not target_path.is_relative_to(resolved_extract):
                    raise ValueError(f"Zip Slip attack detected: path traversal in {filename}")
            except AttributeError:
                # Fallback for older python Path
                if not str(target_path).startswith(str(resolved_extract)):
                    raise ValueError(f"Zip Slip attack detected: path traversal in {filename}")

            if member.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as src, open(target_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)


def verify_extracted_taiwan_data(taiwan_dir: Path) -> tuple[Path, Path]:
    """Verify essential Parquet files exist and are uncorrupted.

    Returns:
        (security_master_path, daily_dir)
    """
    sec_path = taiwan_dir / "security_master.parquet"
    if not sec_path.exists():
        raise ValueError(f"Missing security_master.parquet in {taiwan_dir}")

    # Test reading security_master
    try:
        df_sec = pl.scan_parquet(sec_path).limit(5).collect()
        if df_sec.is_empty():
            raise ValueError("security_master.parquet is empty")
    except Exception as exc:
        raise ValueError(f"Failed to read security_master.parquet: {exc}") from exc

    daily_dir = taiwan_dir / "daily"
    if not daily_dir.exists():
        raise ValueError(f"Missing daily directory in {taiwan_dir}")

    partitions = sorted(daily_dir.glob("date=*/part.parquet"))
    if not partitions:
        raise ValueError("No date partitions found in daily directory")

    # Test reading a sample partition
    try:
        sample_df = pl.scan_parquet(partitions[0]).limit(5).collect()
        if sample_df.is_empty():
            raise ValueError(f"Sample partition {partitions[0].parent.name} is empty")
    except Exception as exc:
        raise ValueError(f"Failed to read sample partition: {exc}") from exc

    return sec_path, daily_dir


def locate_taiwan_in_extracted(extract_dir: Path) -> Path:
    """Find the root of data/taiwan inside extracted folder."""
    candidates = [
        extract_dir / "data" / "taiwan",
        extract_dir / "taiwan",
        extract_dir,
    ]
    for c in candidates:
        if (c / "security_master.parquet").exists() and (c / "daily").exists():
            return c
    raise ValueError(f"Could not locate taiwan data directory in {extract_dir}")


def _read_master_frame(path: Path) -> pl.DataFrame | None:
    try:
        return pl.read_parquet(path)
    except Exception:
        return None


def _latest_master_stamp(frame: pl.DataFrame) -> datetime | None:
    """Newest updated_at in a security master frame, naive ones read as Asia/Taipei; None if unknown."""
    if "updated_at" not in frame.columns:
        return None
    stamps: list[datetime] = []
    for value in frame["updated_at"].drop_nulls().to_list():
        try:
            stamp = datetime.fromisoformat(str(value))
        except ValueError:
            continue
        stamps.append(stamp if stamp.tzinfo else stamp.replace(tzinfo=TAIPEI_TZ))
    return max(stamps, default=None)


def _with_local_knowledge(
    bundled: pl.DataFrame, local: pl.DataFrame, delisted_on: str | None
) -> pl.DataFrame:
    """*bundled* plus the delisted rows and ETF management styles that only *local* holds.

    A locally active instrument missing from *bundled* is kept as delisted on *delisted_on*.
    """
    if {"symbol", "etf_management_style"} <= set(local.columns):
        styles = local.filter(pl.col("etf_management_style").is_not_null()).select(
            "symbol", pl.col("etf_management_style").cast(pl.String).alias("_local_style")
        )
        if "etf_management_style" not in bundled.columns:
            bundled = bundled.with_columns(
                pl.lit(None, dtype=pl.String).alias("etf_management_style")
            )
        bundled = (
            bundled.join(styles, on="symbol", how="left")
            .with_columns(
                pl.coalesce(pl.col("etf_management_style").cast(pl.String), "_local_style").alias(
                    "etf_management_style"
                )
            )
            .drop("_local_style")
        )
    if "listing_status" in local.columns:
        missing = local.filter(~pl.col("symbol").is_in(bundled["symbol"].to_list()))
        gone = missing.filter(pl.col("listing_status") == "active").with_columns(
            pl.lit("delisted").alias("listing_status"),
            pl.lit(delisted_on, dtype=pl.String).alias("delisted_at"),
        )
        carried = pl.concat(
            [missing.filter(pl.col("listing_status") == "delisted"), gone], how="diagonal_relaxed"
        )
        if not carried.is_empty():
            bundled = pl.concat([bundled, carried], how="diagonal_relaxed")
    return bundled


def import_security_master(source: Path, dest: Path) -> bool:
    """Install the bundled master *source* at *dest* unless *dest* is as recent; True when replaced.

    Runs under the master's refresh lock and keeps *dest* while a refresh holds it. Delisted rows
    and ETF management styles only *dest* knows are kept, and the in-process master reloads at once.
    """
    with TaiwanSecurityMaster(cache_path=dest).refresh_lock() as held:
        if not held:
            logger.warning("Keeping the local security master; a refresh holds its lock")
            return False
        bundled = pl.read_parquet(source)
        local = _read_master_frame(dest) if dest.exists() else None
        local_stamp = _latest_master_stamp(local) if local is not None else None
        bundled_stamp = _latest_master_stamp(bundled)
        if local_stamp is not None and (bundled_stamp is None or bundled_stamp <= local_stamp):
            logger.info(
                "Keeping the local security master updated %s; the bundled one is from %s",
                local_stamp,
                bundled_stamp,
            )
            return False
        if local is not None:
            delisted_on = (
                bundled_stamp.astimezone(TAIPEI_TZ).date().isoformat() if bundled_stamp else None
            )
            bundled = _with_local_knowledge(bundled, local, delisted_on)
        write_atomically(dest, bundled.write_parquet)
    get_security_master().reload_if_changed()
    return True


def _publish_default(only_if_behind: bool, since: date | None = None) -> bool:
    return publish_enriched(only_if_behind=only_if_behind, since=since)


def _refresh_caches_default(label: str) -> None:
    from app.jobs.daily_pipeline import refresh_enriched_cache, refresh_strategy_cache

    refresh_enriched_cache(label)
    refresh_strategy_cache(label)


_CHIP_LABELS = {
    "institutional": "三大法人",
    "market_institutional": "大盤法人金額",
    "margin": "融資融券",
}


def _chip_message(chip: dict[str, Any]) -> str:
    as_of = chip.get("as_of") or {}
    reached = "、".join(
        f"{label}至 {as_of.get(name) or '無資料'}" for name, label in _CHIP_LABELS.items()
    )
    incomplete = [
        label
        for name, label in _CHIP_LABELS.items()
        if chip[name]["status"] in ("failed", "partial")
    ]
    message = f", 籌碼資料: {reached}"
    if incomplete:
        message += f", {'、'.join(incomplete)}未完整更新, 請稍後再試"
    return message


class TaiwanBootstrapService:
    """Orchestrates bootstrap download, checksum, extraction, and sync."""

    def __init__(
        self,
        download_url: str = DOWNLOAD_URL,
        expected_sha256: str = EXPECTED_SHA256,
        store: TaiwanDailyStore | None = None,
        refresh_service: TaiwanDailyRefreshService | None = None,
        publish: Callable[..., bool] | None = None,
        security_master: TaiwanSecurityMaster | None = None,
        chip_service: TaiwanDailyUpdateService | None = None,
        refresh_caches: Callable[[str], None] | None = None,
    ) -> None:
        self.download_url = download_url
        self.expected_sha256 = expected_sha256.lower()
        self.store = store or TaiwanDailyStore()
        self.refresh_service = refresh_service or TaiwanDailyRefreshService(store=self.store)
        # The configured enriched table belongs to the default store; an injected store
        # must not publish into it unless the caller supplies a publisher. A publisher takes
        # only_if_behind and, from the history bootstrap, since (the earliest imported day or None).
        self._publish = publish or (
            _publish_default if store is None else (lambda _only_if_behind, since=None: False)
        )
        self._refresh_caches = refresh_caches or (
            _refresh_caches_default if store is None else (lambda _label: None)
        )
        # Likewise the default security master is refreshed only when the default store is used.
        self._security_master = security_master
        self._uses_default_master = security_master is None and store is None
        # Likewise the configured chip stores are refreshed only when the default store is used.
        self._chip_service = chip_service
        self._uses_default_chip_stores = chip_service is None and store is None

    def start_bootstrap(self, force: bool = False) -> str:
        """Start bootstrap process in a background thread."""
        global _active_job_id
        with _jobs_lock:
            if _active_job_id and _active_job_id in _jobs:
                cur = _jobs[_active_job_id]
                if cur.status not in ("success", "failed"):
                    return cur.job_id

            job_id = str(uuid.uuid4())
            job = BootstrapJobState(
                job_id=job_id,
                status="pending",
                stage="準備中",
                progress=0,
                message="初始化下載任務...",
            )
            _jobs[job_id] = job
            _active_job_id = job_id

        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, force),
            daemon=True,
            name=f"taiwan-bootstrap-{job_id[:8]}",
        )
        thread.start()
        return job_id

    def get_job(self, job_id: str) -> BootstrapJobState | None:
        """Get state of a bootstrap job."""
        with _jobs_lock:
            return _jobs.get(job_id)

    def _update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        progress: int | None = None,
        message: str | None = None,
        error: str | None = None,
        bytes_downloaded: int | None = None,
        total_bytes: int | None = None,
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
            if message is not None:
                job.message = message
            if error is not None:
                job.error = error
            if bytes_downloaded is not None:
                job.bytes_downloaded = bytes_downloaded
            if total_bytes is not None:
                job.total_bytes = total_bytes
            job.updated_at = datetime.now().isoformat()

    def _run_job(self, job_id: str, force: bool = False) -> None:
        logger.info("Starting Taiwan historical bootstrap job %s", job_id)
        with tempfile.TemporaryDirectory(prefix="Itoy_tw_bootstrap_", dir=str(temp_root())) as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            zip_file_path = tmp_dir / ASSET_NAME
            extract_dir = tmp_dir / "extracted"
            extract_dir.mkdir(parents=True, exist_ok=True)

            try:
                # Step 1: Download to temp directory with progress
                self._update_job(
                    job_id,
                    status="downloading",
                    stage="下載中",
                    progress=0,
                    message="正在從 GitHub Release 下載歷史資料包 (約 31 MiB)...",
                )

                hasher = hashlib.sha256()
                downloaded = 0
                total = 0

                try:
                    with httpx.Client(follow_redirects=True, timeout=120.0) as client:
                        with client.stream("GET", self.download_url) as response:
                            if response.status_code != 200:
                                raise RuntimeError(
                                    f"下載失敗: HTTP {response.status_code} {response.reason_phrase}"
                                )

                            total = int(response.headers.get("content-length", 0))
                            self._update_job(job_id, total_bytes=total)

                            with open(zip_file_path, "wb") as f:
                                for chunk in response.iter_bytes(chunk_size=128 * 1024):
                                    if chunk:
                                        f.write(chunk)
                                        hasher.update(chunk)
                                        downloaded += len(chunk)
                                        pct = int(downloaded / total * 60) if total > 0 else 30
                                        mb = downloaded / (1024 * 1024)
                                        total_mb = total / (1024 * 1024) if total > 0 else ASSET_APPROX_SIZE_MB
                                        self._update_job(
                                            job_id,
                                            progress=pct,
                                            bytes_downloaded=downloaded,
                                            message=f"正在下載: {mb:.1f} / {total_mb:.1f} MiB ({pct * 100 // 60}%)",
                                        )
                except httpx.RequestError as exc:
                    raise RuntimeError(f"無法連線至 GitHub Release: {exc}") from exc

                # Step 2: Verify SHA256 checksum
                self._update_job(
                    job_id,
                    status="verifying",
                    stage="驗證校驗碼",
                    progress=65,
                    message="正在校驗 SHA256 完整性...",
                )
                actual_sha256 = hasher.hexdigest().lower()
                if actual_sha256 != self.expected_sha256:
                    raise ValueError(
                        f"SHA256 校驗失敗! 預期: {self.expected_sha256}, 實際: {actual_sha256}"
                    )

                # Step 3: Extract ZIP with Zip Slip prevention
                self._update_job(
                    job_id,
                    status="extracting",
                    stage="解壓縮檔案",
                    progress=75,
                    message="正在解壓縮資料包 (安全性路徑檢查)...",
                )
                safe_extract_zip(zip_file_path, extract_dir)

                # Step 4: Validate Parquet data integrity
                taiwan_extracted = locate_taiwan_in_extracted(extract_dir)
                sec_src, daily_src = verify_extracted_taiwan_data(taiwan_extracted)

                # Step 5: Atomic import into data/taiwan
                self._update_job(
                    job_id,
                    status="importing",
                    stage="匯入資料庫",
                    progress=85,
                    message="正在匯入至本機台股資料庫...",
                )
                dest_root = taiwan_data_root().resolve()
                dest_root.mkdir(parents=True, exist_ok=True)

                # Explicit security check: NEVER touch user_data
                if "user_data" in [p.lower() for p in dest_root.parts]:
                    raise RuntimeError("安全違規: 目標路徑不得包含 user_data")

                import_security_master(sec_src, dest_root / "security_master.parquet")

                # Merge daily partitions (never delete existing partitions)
                dest_daily = dest_root / "daily"
                dest_daily.mkdir(parents=True, exist_ok=True)

                extracted_partitions = sorted(daily_src.glob("date=*/part.parquet"))
                imported: set[str] = set()
                for p in extracted_partitions:
                    part_name = p.parent.name
                    target_part_dir = dest_daily / part_name
                    if not target_part_dir.exists():
                        target_part_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(p), str(target_part_dir / "part.parquet"))
                        imported.add(part_name)
                    else:
                        target_file = target_part_dir / "part.parquet"
                        if not target_file.exists():
                            shutil.copy2(str(p), str(target_file))
                            imported.add(part_name)

                # Step 6: Post-bootstrap incremental sync to latest trading day
                self._update_job(
                    job_id,
                    status="syncing",
                    stage="補充最新行情",
                    progress=90,
                    message="正在自動補充最新交易日資料...",
                )

                st = TaiwanDailyStore(data_dir=dest_daily)
                latest_stored = st.latest_date()
                target_date = resolve_target_latest_trading_date()

                fetched = 0
                sync_start: date | None = None
                if latest_stored and latest_stored < target_date:
                    sync_start = latest_stored + timedelta(days=1)
                    if sync_start <= target_date:
                        try:
                            refresh_svc = TaiwanDailyRefreshService(store=st)
                            res = refresh_svc.refresh_dates(start_date=sync_start, end_date=target_date)
                            fetched = res.get("dates_fetched", 0)
                            logger.info("Post-bootstrap incremental sync: %s", res)
                        except Exception as sync_exc:
                            logger.warning("Post-bootstrap sync warning (non-fatal): %s", sync_exc)

                # Screening, strategies and monitor signals read the enriched table, not these bars, and
                # imported days can be older than the recent window a publish rewrites.
                changed = bool(imported or fetched)
                earliest = [d for d in st.available_dates() if f"date={d.isoformat()}" in imported]
                if fetched and sync_start is not None:
                    earliest.append(sync_start)
                since = min(earliest, default=None)
                published = self._publish(not changed, since=since)
                if changed or published:
                    self._refresh_caches("history bootstrap")

                # Success!
                final_dates = st.available_dates()
                final_count = len(final_dates)
                latest_str = str(final_dates[-1]) if final_dates else "未知"
                self._update_job(
                    job_id,
                    status="success",
                    stage="已完成",
                    progress=100,
                    message=f"歷史日 K 資料包匯入成功! 共 {final_count} 個交易日, 資料最新至 {latest_str}。",
                )
                logger.info("Taiwan historical bootstrap job %s completed successfully", job_id)

            except Exception as exc:
                logger.exception("Taiwan bootstrap job %s failed: %s", job_id, exc)
                self._update_job(
                    job_id,
                    status="failed",
                    stage="失敗",
                    progress=0,
                    message=f"資料包匯入失敗: {exc}",
                    error=str(exc),
                )
            finally:
                # Temp dir is automatically cleaned up by TemporaryDirectory context manager
                pass

    def _master(self) -> TaiwanSecurityMaster | None:
        if self._security_master is not None:
            return self._security_master
        return get_security_master() if self._uses_default_master else None

    def _chip_updater(self) -> TaiwanDailyUpdateService | None:
        if self._chip_service is not None:
            return self._chip_service
        if not self._uses_default_chip_stores:
            return None
        return TaiwanDailyUpdateService(daily_store=self.store, daily_service=self.refresh_service)

    def _refresh_chip_data(self, target: date, now: datetime) -> dict[str, Any] | None:
        """Never raises; a failure is reported per dataset."""
        try:
            updater = self._chip_updater()
            if updater is None:
                return None
            margin_target = resolve_margin_target(target, now, updater.calendar)
            chip = updater.refresh_chip_data(target, margin_target=margin_target).model_dump()
            chip["as_of"] = updater.chip_data_as_of()
            chip["margin_target_date"] = margin_target.isoformat()
            return chip
        except Exception as exc:
            logger.exception("Chip data refresh failed: %s", exc)
            failed = DatasetRefreshStats(
                status="failed",
                failed_dates=[{"error": str(exc)}],
                note=f"Chip data not refreshed: {exc}",
            )
            return ChipDataRefreshResult(
                institutional=failed, market_institutional=failed, margin=failed
            ).model_dump()

    def update_to_latest(self) -> dict[str, Any]:
        """Directly update existing local store to latest trading day (no zip download).

        Refreshes the security master first under the daily update's rules and refetches the stored
        days missing a recent listing (pending_listing_dates), so a later click retries a failed
        backfill; a changed master also republishes the enriched table.

        After the daily bars, even when they are already current, institutional flows, market-wide
        institutional amounts and margin catch up to the same target as the scheduled run does;
        margin stops at the previous session until MARGIN_PUBLISH (resolve_margin_target). Their
        failures never fail the update and are reported in "chip_data".
        """
        st = self.store
        dates = st.available_dates()
        if not dates:
            raise ValueError("本地尚無資料，請先下載歷史資料包")

        latest = dates[-1]
        target = resolve_target_latest_trading_date()
        master = self._master()
        master_outcome: MasterRefreshOutcome | None = None
        now = taipei_now()
        if master is not None and master_refresh_due(target, now, TaiwanTradingCalendar()):
            master_outcome = refresh_security_master(master, now=now, automatic=True)
        backfill = pending_listing_dates(st, master, target) if master is not None else []
        republish = master_outcome is not None and master_outcome.changed
        master_info: dict[str, Any] = {}
        master_suffix = ""
        if master_outcome is not None:
            master_info["security_master"] = master_outcome.summary
            if master_outcome.note:
                master_suffix = ", 證券主檔未更新, 沿用原有清單"
        repairs = sorted(set(st.repair_dates(target)) | set(backfill))

        if latest >= target and not repairs:
            chip = self._refresh_chip_data(target, now)
            published = self._publish(not republish)
            chip_info: dict[str, Any] = {"chip_data": chip} if chip is not None else {}
            return {
                "ok": True,
                "already_current": True,
                "message": f"本地資料已是最新 ({latest})"
                + (", 已補算技術指標" if published else "")
                + master_suffix
                + (_chip_message(chip) if chip is not None else ""),
                "dates_fetched": 0,
                "enriched_published": published,
                **master_info,
                **chip_info,
            }

        start = min([latest + timedelta(days=1), *repairs])
        refetch_kwargs = {"refetch": backfill} if backfill else {}
        res = self.refresh_service.refresh_dates(
            start_date=start, end_date=target, **refetch_kwargs
        )
        fetched = res.get("dates_fetched", 0)
        failures = [
            (f"{f['date']} 行情抓取失敗", f["error"]) for f in res.get("failed_dates") or []
        ] + [
            (f"{f['date']} 重抓失敗, 保留原有資料", f["error"]) for f in res.get("repair_failed_dates") or []
        ]
        if failures and not fetched:
            raise RuntimeError(f"{failures[0][0]}: {failures[0][1]}")
        chip = self._refresh_chip_data(target, now)
        # Screening takes its basis day from these bars, so their indicators must be published too.
        published = self._publish(not fetched and not republish, since=start if fetched else None)
        message = f"成功更新 {fetched} 個交易日 (最新至 {target})"
        if failures:
            message = f"已更新 {fetched} 個交易日, {failures[0][0]}, 請稍後再試"
        chip_info = {"chip_data": chip} if chip is not None else {}
        return {
            "ok": True,
            "already_current": False,
            "message": message + master_suffix + (_chip_message(chip) if chip is not None else ""),
            "dates_fetched": fetched,
            "enriched_published": published,
            "stats": res,
            **master_info,
            **chip_info,
        }


_bootstrap_service_instance: TaiwanBootstrapService | None = None


def get_bootstrap_service() -> TaiwanBootstrapService:
    """Return singleton instance of TaiwanBootstrapService."""
    global _bootstrap_service_instance
    if _bootstrap_service_instance is None:
        _bootstrap_service_instance = TaiwanBootstrapService()
    return _bootstrap_service_instance
