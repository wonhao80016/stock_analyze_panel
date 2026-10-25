"""Mining algorithm version and the deterministic data fingerprint of a mining run."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from app.backtest.factor import FACTOR_METHODOLOGY_VERSION
from app.services.regime_builder import regime_path

MINING_ALGORITHM_VERSION = "mining-v3"
FINGERPRINT_VERSION = "weekly-mining-data-v2"


def build_data_fingerprint(
    repo: Any,
    app_state: Any,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Hash one stable managed generation plus source metadata."""
    for _attempt in range(2):
        fingerprint = _build_data_fingerprint_once(repo, app_state, request)
        if repo.get_matrix_data_generation(fingerprint["asset_type"]) == fingerprint["generation"]:
            return fingerprint
    raise ValueError("enriched data changed while building the mining fingerprint")


def _build_data_fingerprint_once(
    repo: Any,
    app_state: Any,
    request: dict[str, Any],
) -> dict[str, Any]:
    data_dir = Path(repo.store.data_dir)
    asset_type = str(request.get("asset_type") or "stock")
    enriched_root = (
        data_dir / "kline_daily_enriched"
        if asset_type == "stock"
        else data_dir / f"kline_{asset_type}_enriched"
    )
    module_root = Path(__file__).resolve().parents[1]
    components = {
        "version": FINGERPRINT_VERSION,
        "asset_type": asset_type,
        "generation": repo.get_matrix_data_generation(asset_type),
        "latest_enriched_date": _iso_or_none(repo.latest_enriched_date(asset_type)),
        "enriched": _enriched_metadata(enriched_root),
        "instruments": _instrument_metadata(repo, asset_type),
        "regime": _path_metadata(regime_path(data_dir), root=data_dir),
        "algorithm_version": MINING_ALGORITHM_VERSION,
        "methodology_version": FACTOR_METHODOLOGY_VERSION,
        "implementation": _implementation_metadata(module_root),
        "strategies": _selected_strategy_metadata(
            app_state,
            request.get("strategy_ids") or [],
            data_dir,
        ),
    }
    payload = _canonical_json(components)
    return {
        **components,
        "digest": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def _instrument_metadata(repo: Any, asset_type: str) -> dict[str, Any]:
    instruments = repo.get_instruments_asset(asset_type)
    if instruments is None or instruments.is_empty() or "symbol" not in instruments.columns:
        return {"rows": 0, "digest": "no-instruments"}
    columns = [
        name
        for name in (
            "symbol",
            "name",
            "total_shares",
            "float_shares",
            "limit_up",
            "limit_down",
        )
        if name in instruments.columns
    ]
    payload = instruments.select(columns).sort("symbol").to_dicts()
    digest = hashlib.blake2b(
        json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
        digest_size=20,
    ).hexdigest()
    return {"rows": instruments.height, "columns": columns, "digest": digest}


def _enriched_metadata(root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for partition in sorted(root.glob("date=*"), key=lambda item: item.name):
        try:
            date.fromisoformat(partition.name.removeprefix("date="))
        except ValueError:
            continue
        records.append(
            {
                "partition": partition.name,
                "file": _path_metadata(partition / "part.parquet", root=root),
            }
        )
    return {
        "partition_count": len(records),
        "first_partition": records[0]["partition"] if records else None,
        "last_partition": records[-1]["partition"] if records else None,
        "metadata_digest": hashlib.sha256(_canonical_json(records).encode("utf-8")).hexdigest(),
    }


def _selected_strategy_metadata(
    app_state: Any,
    strategy_ids: list[str],
    data_dir: Path,
) -> list[dict[str, Any]]:
    if not strategy_ids:
        return []
    engine = getattr(app_state, "strategy_engine", None)
    if engine is None:
        raise RuntimeError("strategy engine is unavailable for scheduled mining fingerprint")
    metadata: list[dict[str, Any]] = []
    for strategy_id in sorted(strategy_ids):
        strategy = engine.get(strategy_id)
        if strategy.execution_backend != "matrix_native":
            raise ValueError(f"scheduled mining strategy is not matrix-native: {strategy_id}")
        source_path = Path(strategy.file_path) if strategy.file_path is not None else None
        override_path = data_dir / "user_data" / "strategy_overrides" / f"{strategy_id}.json"
        metadata.append(
            {
                "strategy_id": strategy_id,
                "source": _content_metadata(
                    source_path, root=source_path.parent if source_path else data_dir
                ),
                "source_tree": (
                    _implementation_metadata(source_path.parent)
                    if source_path is not None
                    else None
                ),
                "override": _content_metadata(override_path, root=data_dir),
            }
        )
    return metadata


def _path_metadata(path: Path | None, *, root: Path) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return {"path": _relative_path(path, root), "exists": False}
    return {
        "path": _relative_path(path, root),
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _content_metadata(path: Path | None, *, root: Path) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        content = path.read_bytes()
    except OSError:
        return {"path": _relative_path(path, root), "exists": False}
    return {
        "path": _relative_path(path, root),
        "exists": True,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _implementation_metadata(module_root: Path) -> dict[str, Any]:
    records = []
    for path in sorted(module_root.rglob("*.py"), key=lambda item: item.as_posix()):
        try:
            content = path.read_bytes()
        except OSError:
            continue
        records.append({
            "path": path.relative_to(module_root).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
        })
    return {
        "file_count": len(records),
        "digest": hashlib.sha256(_canonical_json(records).encode("utf-8")).hexdigest(),
    }


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _iso_or_none(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
