"""Reconstruct missing strategy-day snapshots without changing the current screener cache."""
from __future__ import annotations

import copy
import logging
import threading
import uuid
from dataclasses import asdict
from datetime import date

import polars as pl

from app.services import strategy_pick_history as history
from app.services.screener import ScreenerService
from app.strategy import config as strategy_config
from app.strategy.scoring import effective_scoring, scoring_dependencies
from app.taiwan.daily_store import TaiwanDailyStore

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_jobs: dict[str, dict] = {}


def input_quality(engine, context, sid: str, overrides: dict, params: dict) -> tuple[list[str], list[str]]:
    strategy = engine.get(sid)
    if strategy.execution_backend != "matrix_native":
        return [], []
    source = context.history
    if source is None or source.is_empty():
        return ["history"], []
    required = engine.required_history_bars(
        [sid], params_map={sid: params}, overrides_map={sid: overrides})
    dates = source.filter(pl.col("date") <= context.as_of).get_column("date").drop_nulls().n_unique()
    if dates < required:
        return ["history"], []
    target = source.filter(pl.col("date") == context.as_of)
    scoring_fields = scoring_dependencies(effective_scoring(strategy.meta.get("scoring"), overrides))
    intrinsic = set(strategy.matrix_strategy.required_fields())
    parameter_fields = getattr(strategy.matrix_strategy, "required_fields_for_params", None)
    if callable(parameter_fields):
        intrinsic.update(scoring_dependencies({name: 1 for name in parameter_fields(params)}))
    filter_fields = intrinsic | (engine._matrix_field_columns(strategy, overrides, params) - scoring_fields)

    def unavailable(fields):
        missing = []
        for name in sorted(fields):
            if name in target.columns:
                valid = target.select(pl.col(name).cast(pl.Float64, strict=False).is_finite().sum()).item()
            else:
                import numpy as np

                from app.backtest.matrix import build_market_data_matrix, matrix_feature

                if context.market is None:
                    context.market = build_market_data_matrix(
                        source, field_columns=engine._matrix_field_columns(strategy, overrides, params))
                times = [i for i, label in enumerate(context.market.timestamp_labels) if label[:10] == str(context.as_of)]
                try:
                    valid = bool(times) and np.isfinite(matrix_feature(context.market, name)[times[-1]]).any()
                except (KeyError, ValueError):
                    valid = False
            if not valid:
                missing.append(name)
        return missing

    return unavailable(filter_fields), unavailable(scoring_fields)


def scoring_candidates_safe(engine, sid: str, overrides: dict, row_count: int) -> bool:
    strategy = engine.get(sid)
    # All returned candidates must fit before the engine's ranking limit.
    limit = engine._result_limit(strategy, overrides)
    return limit is None or row_count < limit


def _dates(repo, days: int) -> list[date]:
    if not 1 <= days <= 250:
        raise ValueError("days must be between 1 and 250")
    latest = ScreenerService(repo).latest_date()
    store = TaiwanDailyStore(repo.store.data_dir / "taiwan" / "daily")
    available = [d for d in store.available_dates() if latest is not None and d <= latest]
    latest_complete = next((d for d in reversed(available) if not store.incomplete_dates([d])), None)
    if latest_complete is None:
        raise ValueError("No complete local daily prices are available")
    dates = [d for d in available if d < latest_complete][-days:]
    if not dates:
        raise ValueError("No prior local daily trading dates are available")
    return dates


def run(repo, engine, days: int = 5, progress=None, *, dates: list[date] | None = None) -> dict:
    dates = _dates(repo, days) if dates is None else dates
    data_dir = repo.store.data_dir
    overrides = strategy_config.list_overrides(data_dir)
    metas = [m for m in engine.list_strategies() if not m.get("research_only")
             and "1d" in m.get("timeframes", ["1d"])
             and set(m.get("asset_types", ["stock"])) & {"stock", "etf"}]
    ids = [m["id"] for m in metas]
    groups = history.strategy_input_groups(engine, ids)
    fingerprints = history.strategy_fingerprints(engine, overrides, ids)
    versions = history.strategy_versions(engine, overrides, ids)
    saved = history.read_picks(data_dir, dates[0], dates[-1])
    retryable = set(saved.runs.filter(
        (pl.col("provenance") == "reconstructed") & (pl.col("calculation_status") == "input_unavailable")
    ).select("signal_date", "strategy_id").iter_rows())
    existing = set(saved.runs.select("signal_date", "strategy_id").iter_rows())
    existing.update(saved.picks.select("signal_date", "strategy_id").iter_rows())
    existing -= retryable
    result = {"dates": [str(d) for d in dates], "requested_days": days, "completed_days": 0,
              "written_runs": 0, "existing_runs": 0, "zero_signal_runs": 0, "scoring_unavailable_runs": 0, "failures": []}
    store = TaiwanDailyStore(data_dir / "taiwan" / "daily")
    incomplete = set(store.incomplete_dates(dates))

    def fail(d, sid, reason):
        result["failures"].append({"date": str(d), "strategy_id": sid, "reason": reason})

    for d in dates:
        remaining = []
        for meta in metas:
            sid = meta["id"]
            if (d, sid) in existing:
                result["existing_runs"] += 1
            elif d in incomplete:
                fail(d, sid, "daily_incomplete")
            elif not fingerprints.get(sid):
                fail(d, sid, "fingerprint_unavailable")
            elif not groups.get(sid):
                fail(d, sid, "input_unavailable")
            else:
                remaining.append(meta)
        for asset in ("stock", "etf"):
            selected = [m["id"] for m in remaining if asset == ("stock" if "stock" in m.get("asset_types", ["stock"]) else "etf")]
            if not selected:
                continue
            params_map = {sid: dict((overrides.get(sid) or {}).get("params") or {}) for sid in selected}
            overrides_map = {sid: overrides.get(sid, {}) for sid in selected}
            try:
                context = ScreenerService(repo, asset_type=asset).build_strategy_context(
                    engine, d, selected, params_map=params_map, overrides_map=overrides_map)
                frames = [f for f in (context.current, context.history) if f is not None and not f.is_empty()]
                if any("date" not in f.columns or f["date"].max() > d for f in frames):
                    for sid in selected:
                        fail(d, sid, "future_input")
                    continue
                status = context.input_status or {}
                report = history.RunReport(
                    current=status.get(history.CURRENT) or history.InputStatus(),
                    history=status.get(history.HISTORY), input_groups=groups, fingerprints=fingerprints,
                    versions=versions)
                native = [sid for sid in selected if engine.get(sid).execution_backend == "matrix_native"]
                if native and context.history is not None and not context.history.is_empty():
                    from app.backtest.matrix import build_market_data_matrix

                    fields = set().union(*(engine._matrix_field_columns(
                        engine.get(sid), overrides_map[sid], params_map[sid]) for sid in native))
                    context.market = build_market_data_matrix(context.history, field_columns=fields)
                runnable = []
                scoring_missing = {}
                for sid in selected:
                    if not all(history._group_available(report, group, d) for group in groups[sid]):
                        fail(d, sid, "input_unavailable")
                    elif not history._universe_complete(report, groups[sid]):
                        fail(d, sid, "universe_incomplete")
                    else:
                        missing_filter, missing_scoring = input_quality(
                            engine, context, sid, overrides_map[sid], params_map[sid])
                        if missing_filter:
                            fail(d, sid, "input_unavailable")
                            result["failures"][-1]["missing_fields"] = missing_filter
                        else:
                            runnable.append(sid)
                            if missing_scoring:
                                scoring_missing[sid] = missing_scoring
                if not runnable:
                    continue
                engine_results = engine.run_all(context, params_map=params_map, overrides_map=overrides_map,
                                                strategy_ids=runnable, skip_failed=True)
                results = {sid: asdict(value) for sid, value in engine_results.items()}
                for sid in runnable:
                    if sid not in results:
                        fail(d, sid, "strategy_failed")
                        continue
                    value = results[sid]
                    rows, dropped = history._rows_for_date(value.get("rows") or [], d)
                    if dropped or (not rows and value.get("total", 0) > 0):
                        fail(d, sid, "invalid_result_rows")
                        del results[sid]
                    elif sid in scoring_missing:
                        if not scoring_candidates_safe(engine, sid, overrides_map[sid], len(rows)):
                            fail(d, sid, "scoring_selection_unavailable")
                            result["failures"][-1]["missing_fields"] = scoring_missing[sid]
                            del results[sid]
                        else:
                            for row in value["rows"]:
                                row["score"] = None
                            report.calculation_statuses[sid] = "scoring_unavailable"
                            report.missing_fields[sid] = scoring_missing[sid]
                outcome = history.record(data_dir, d, results, report, "history_backfill", None,
                                         provenance="reconstructed")
                result["written_runs"] += len(outcome.written)
                result["zero_signal_runs"] += sum(not results[sid].get("rows") for sid in outcome.written)
                result["scoring_unavailable_runs"] += sum(sid in scoring_missing for sid in outcome.written)
                for sid, reason in outcome.rejected.items():
                    if reason == "existing_snapshot":
                        result["existing_runs"] += 1
                    else:
                        fail(d, sid, reason)
            except (history.PickHistoryReadError, OSError):
                raise
            except Exception:
                logger.exception("pick backfill failed for %s %s", d, asset)
                for sid in selected:
                    fail(d, sid, "strategy_failed")
        result["completed_days"] += 1
        if progress:
            progress(copy.deepcopy(result))
    return result


def start(repo, engine, days: int = 5) -> dict:
    dates = _dates(repo, days)
    root = str(repo.store.data_dir.resolve())
    with _lock:
        for job in _jobs.values():
            if job["root"] == root and job["state"]["status"] == "running":
                return copy.deepcopy(job["state"])
        job_id = uuid.uuid4().hex
        state = {"job_id": job_id, "status": "running", "requested_days": days,
                 "dates": [str(d) for d in dates], "completed_days": 0, "written_runs": 0,
                 "existing_runs": 0, "zero_signal_runs": 0, "failures": [], "error": None}
        state["scoring_unavailable_runs"] = 0
        _jobs[job_id] = {"root": root, "state": state}
        finished = [key for key, value in _jobs.items() if value["state"]["status"] != "running"]
        for key in finished[:-20]:
            del _jobs[key]

    def update(payload):
        with _lock:
            state.update(payload)

    def work():
        try:
            payload = run(repo, engine, days, update, dates=dates)
            update({**payload, "status": "completed"})
        except Exception:
            logger.exception("strategy pick backfill job failed")
            update({"status": "failed", "error": "Historical reconstruction failed; completed snapshots are retained."})

    threading.Thread(target=work, name=f"pick-backfill-{job_id[:8]}", daemon=True).start()
    return get(repo.store.data_dir, job_id)


def get(data_dir, job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job["root"] != str(data_dir.resolve()):
            return None
        return copy.deepcopy(job["state"])
