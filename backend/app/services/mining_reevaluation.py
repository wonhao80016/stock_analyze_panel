"""Re-evaluate mined publications whose evidence predates the current mining algorithm."""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.mining_candidates import (
    DelistingDeferredError,
    MiningCandidateService,
    candidate_gate,
)
from app.services.mining_jobs import (
    ACTIVE_RUN_STATUSES,
    SUCCESS_RUN_STATUSES,
    MiningRunStoreError,
)
from app.services.mining_schedule import MINING_ALGORITHM_VERSION
from app.taiwan.universe.service import write_atomically

RerunStarter = Callable[[dict[str, Any]], Mapping[str, Any]]

# passed/delisted are verdicts; needs_review waits for a person; restored is a person's override.
_FINAL_STATUSES = frozenset({"passed", "delisted", "needs_review", "restored"})
_RESTART_STATUSES = frozenset({"cancelled", "interrupted", "skipped_prerequisite"})
_LEDGER_SCHEMA_VERSION = 1
_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


class RerunUnavailableError(RuntimeError):
    """A rerun cannot start right now for a transient reason; retry on a later call."""


class PublishedStrategyReevaluator:
    """Rerun every outdated mined publication and delist it only when the rerun rejects it.

    A publication is delisted only after a successful rerun in which its candidate fails the
    publication gate or is no longer selected. A refused or failed rerun, a missing benchmark
    strategy or a composite dependent is recorded as ``needs_review`` instead; transient start
    failures become ``retry``. At most one rerun is outstanding; others wait as ``waiting``.
    Verdicts live in a ledger keyed by origin run, signature and algorithm version, so repeated
    calls never rerun or delist the same publication twice.
    """

    def __init__(
        self,
        candidate_service: MiningCandidateService,
        start_rerun: RerunStarter,
    ) -> None:
        self.service = candidate_service
        self.store = candidate_service.run_store
        self.start_rerun = start_rerun
        self.ledger_path = (
            candidate_service.data_dir / "research" / "mining" / "reevaluation" / "ledger.json"
        )

    def run(self, *, retry_review: bool = False) -> dict[str, Any]:
        with _LOCK:
            ledger = self._read_ledger()
            entries: dict[str, dict[str, Any]] = ledger["entries"]
            items: list[dict[str, Any]] = []
            seen: set[str] = set()
            busy = any(self._outstanding(entry) for entry in entries.values())
            for publication in self._outdated_publications():
                key = f"{publication['run_id']}/{publication['signature']}"
                seen.add(key)
                previous = entries.get(key)
                try:
                    entry = self._advance(
                        publication,
                        previous,
                        busy=busy,
                        delisted_ids=_delisted_ids(entries),
                        retry_review=retry_review,
                    )
                except Exception as exc:
                    logger.exception(
                        "Re-evaluating mined strategy %s failed", publication["strategy_id"]
                    )
                    entry = _entry(publication, "retry", [str(exc)[:500]])
                if entry != previous:
                    entries[key] = entry
                    self._write_ledger(ledger)
                busy = any(self._outstanding(value) for value in entries.values())
                items.append(_item(entry))
            for key in sorted(set(entries) - seen):
                entry = entries[key]
                if (
                    entry.get("status") == "delisted"
                    and entry.get("algorithm_version") == MINING_ALGORITHM_VERSION
                ):
                    items.append(_item(entry))
            return {"algorithm_version": MINING_ALGORITHM_VERSION, "items": items}

    def restore(self, strategy_id: str) -> dict[str, Any]:
        """Put back the latest delisting of ``strategy_id`` and keep it out of re-evaluation."""
        with _LOCK:
            ledger = self._read_ledger()
            matches = [
                (key, entry)
                for key, entry in ledger["entries"].items()
                if entry.get("strategy_id") == strategy_id
                and entry.get("status") == "delisted"
                and entry.get("quarantine")
            ]
            if not matches:
                raise ValueError(f"strategy {strategy_id} is not delisted")
            key, entry = max(matches, key=lambda item: str(item[1].get("updated_at")))
            record_path = self.service.data_dir / str(entry["quarantine"]) / "delisting.json"
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"delisting record is unreadable: {exc}") from exc
            self.service.restore_publication(record)
            restored = {
                **entry,
                "status": "restored",
                "updated_at": datetime.now(UTC).isoformat(),
            }
            ledger["entries"][key] = restored
            self._write_ledger(ledger)
            return _item(restored)

    def _advance(
        self,
        publication: Mapping[str, Any],
        previous: Mapping[str, Any] | None,
        *,
        busy: bool,
        delisted_ids: set[str],
        retry_review: bool,
    ) -> dict[str, Any]:
        current = (
            previous is not None
            and previous.get("algorithm_version") == MINING_ALGORITHM_VERSION
        )
        if current and previous.get("status") in _FINAL_STATUSES:
            if not (retry_review and previous.get("status") == "needs_review"):
                return dict(previous)
            current = False
        rerun_id = previous.get("rerun_id") if current else None
        rerun = self.store.get(rerun_id) if isinstance(rerun_id, str) else None
        if rerun is None or rerun.get("status") in _RESTART_STATUSES:
            if busy:
                return _entry(publication, "waiting", ["another mining rerun is outstanding"])
            unavailable = sorted(
                strategy_id
                for strategy_id in publication["request"].get("strategy_ids") or []
                if strategy_id in delisted_ids
                or not self.service.strategy_engine.has(strategy_id)
            )
            if unavailable:
                return _entry(publication, "needs_review", [
                    f"benchmark strategies {unavailable} are delisted or missing; "
                    "the origin request cannot be rerun as is"
                ])
            try:
                rerun = dict(self.start_rerun(self._rerun_request(publication)))
            except RerunUnavailableError as exc:
                return _entry(publication, "retry", [str(exc)])
            except ValueError as exc:
                return _entry(publication, "needs_review", [
                    f"current mining refused the origin request: {exc}"
                ])
        rerun_id = str(rerun["run_id"])
        status = rerun.get("status")
        if status in ACTIVE_RUN_STATUSES:
            return _entry(publication, "pending", [], rerun_id)
        if status in _RESTART_STATUSES:
            return _entry(publication, "retry", [f"mining rerun {rerun_id} ended as {status}"])
        if status not in SUCCESS_RUN_STATUSES:
            return _entry(publication, "needs_review", [
                f"mining rerun {rerun_id} ended as {status}: {rerun.get('error')}"
            ], rerun_id)
        row = next(
            (
                candidate
                for candidate in self.service.read_candidate_rows(rerun)
                if candidate.get("signature") == publication["signature"]
            ),
            None,
        )
        if row is None:
            return self._delist(
                publication,
                ["candidate was not selected by the current mining rerun"],
                rerun_id,
            )
        gate = candidate_gate(row)
        if not gate.qualified:
            return self._delist(publication, list(gate.reasons), rerun_id)
        logger.info(
            "Mined strategy %s passed re-evaluation with rerun %s",
            publication["strategy_id"],
            rerun_id,
        )
        return _entry(publication, "passed", [], rerun_id)

    def _delist(
        self,
        publication: Mapping[str, Any],
        reasons: list[str],
        rerun_id: str,
    ) -> dict[str, Any]:
        try:
            record = self.service.delist_publication(
                publication["run_id"],
                publication["signature"],
                reasons=reasons,
                rerun_id=rerun_id,
            )
        except DelistingDeferredError as exc:
            return _entry(publication, "needs_review", [*reasons, str(exc)], rerun_id)
        entry = _entry(publication, "delisted", reasons, rerun_id)
        entry["quarantine"] = record.get("quarantine") if record else None
        return entry

    def _outstanding(self, entry: Mapping[str, Any]) -> bool:
        if (
            entry.get("status") != "pending"
            or entry.get("algorithm_version") != MINING_ALGORITHM_VERSION
            or not isinstance(entry.get("rerun_id"), str)
        ):
            return False
        rerun = self.store.get(entry["rerun_id"])
        return rerun is not None and rerun.get("status") in ACTIVE_RUN_STATUSES

    def _outdated_publications(self) -> Iterator[dict[str, Any]]:
        try:
            paths = sorted(self.store.runs_root.glob("*/manifest.json"))
        except OSError as exc:
            raise MiningRunStoreError("failed to scan mining run manifests") from exc
        for path in paths:
            run_id = path.parent.name
            try:
                manifest = self.store.get(run_id)
                if (
                    manifest is None
                    or manifest.get("status") not in SUCCESS_RUN_STATUSES
                    or "candidates" not in manifest.get("artifacts", {})
                ):
                    continue
                summary = self.store.read_summary(run_id)
                if summary.get("algorithm_version") == MINING_ALGORITHM_VERSION:
                    continue
                rows = self.service.read_candidate_rows(manifest)
            except (MiningRunStoreError, ValueError, RuntimeError) as exc:
                logger.warning("Skipping unreadable mining run %s: %s", run_id, exc)
                continue
            for row in rows:
                strategy_id = row.get("published_strategy_id")
                if not isinstance(strategy_id, str) or not strategy_id:
                    continue
                yield {
                    "run_id": run_id,
                    "signature": str(row["signature"]),
                    "strategy_id": strategy_id,
                    "request": manifest["request"],
                    "data_as_of": summary.get("data_as_of"),
                }

    @staticmethod
    def _rerun_request(publication: Mapping[str, Any]) -> dict[str, Any]:
        request = dict(publication["request"])
        # Pin an open-ended origin range so the rerun scores the same history it was mined on.
        if request.get("end") is None and isinstance(publication.get("data_as_of"), str):
            request["end"] = publication["data_as_of"]
        return request

    def _read_ledger(self) -> dict[str, Any]:
        if not self.ledger_path.exists():
            return {"schema_version": _LEDGER_SCHEMA_VERSION, "entries": {}}
        try:
            value = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"mining re-evaluation ledger is unreadable: {exc}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("entries"), dict):
            raise RuntimeError("mining re-evaluation ledger is invalid")
        return value

    def _write_ledger(self, ledger: Mapping[str, Any]) -> None:
        text = json.dumps(ledger, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        write_atomically(
            self.ledger_path,
            lambda temporary: Path(temporary).write_text(text, encoding="utf-8"),
        )


def _delisted_ids(entries: Mapping[str, Mapping[str, Any]]) -> set[str]:
    return {
        str(entry.get("strategy_id"))
        for entry in entries.values()
        if entry.get("status") == "delisted"
        and entry.get("algorithm_version") == MINING_ALGORITHM_VERSION
    }


def _entry(
    publication: Mapping[str, Any],
    status: str,
    reasons: list[str],
    rerun_id: str | None = None,
) -> dict[str, Any]:
    return {
        "strategy_id": publication["strategy_id"],
        "run_id": publication["run_id"],
        "signature": publication["signature"],
        "algorithm_version": MINING_ALGORITHM_VERSION,
        "status": status,
        "reasons": reasons,
        "rerun_id": rerun_id,
        "quarantine": None,
        "updated_at": datetime.now(UTC).isoformat(),
    }


def _item(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field: entry.get(field)
        for field in (
            "strategy_id",
            "run_id",
            "signature",
            "status",
            "reasons",
            "rerun_id",
            "quarantine",
            "updated_at",
        )
    }
