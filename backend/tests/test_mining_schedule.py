from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from app.services import mining_schedule


class FakeRepo:
    def __init__(self, data_dir: Path, *, latest: date = date(2026, 8, 14)) -> None:
        self.store = SimpleNamespace(data_dir=data_dir)
        self.latest = latest
        self.generation = "generation-1"

    def latest_enriched_date(self, asset_type: str = "stock") -> date | None:
        assert asset_type == "stock"
        return self.latest

    def get_matrix_data_generation(self, asset_type: str = "stock") -> str:
        assert asset_type == "stock"
        return self.generation

    def get_instruments_asset(self, asset_type: str = "stock") -> pl.DataFrame:
        assert asset_type == "stock"
        return pl.DataFrame({
            "symbol": ["000001.SZ"],
            "name": ["示例"],
            "total_shares": [1_000_000.0],
            "float_shares": [800_000.0],
        })


def test_fingerprint_retries_generation_change_and_returns_stable_token(
    tmp_path,
    monkeypatch,
) -> None:
    repo = FakeRepo(tmp_path)
    state = SimpleNamespace(strategy_engine=None)
    generations = iter([
        "generation-1",
        "generation-2",
        "generation-2",
        "generation-2",
    ])
    monkeypatch.setattr(repo, "get_matrix_data_generation", lambda _asset: next(generations))

    fingerprint = mining_schedule.build_data_fingerprint(
        repo,
        state,
        {"asset_type": "stock", "strategy_ids": []},
    )

    assert fingerprint["generation"] == "generation-2"


def test_implementation_metadata_is_recursive_content_based_and_root_independent(
    tmp_path,
) -> None:
    roots = [tmp_path / "first" / "app", tmp_path / "second" / "app"]
    for root in roots:
        nested = root / "backtest"
        nested.mkdir(parents=True)
        (root / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
        (nested / "runtime.py").write_text("RESULT = 1\n", encoding="utf-8")
        (nested / "ignored.txt").write_text("ignored\n", encoding="utf-8")

    first = mining_schedule._implementation_metadata(roots[0])
    second = mining_schedule._implementation_metadata(roots[1])
    (roots[1] / "backtest" / "runtime.py").write_text("RESULT = 2\n", encoding="utf-8")
    changed = mining_schedule._implementation_metadata(roots[1])

    assert first == second
    assert first["file_count"] == 2
    assert str(tmp_path) not in str(first)
    assert first["digest"] != changed["digest"]


def test_fingerprint_covers_result_implementation_digest(
    tmp_path,
    monkeypatch,
) -> None:
    repo = FakeRepo(tmp_path)
    state = SimpleNamespace(strategy_engine=None)
    first = mining_schedule.build_data_fingerprint(
        repo,
        state,
        {"asset_type": "stock", "strategy_ids": []},
    )
    monkeypatch.setattr(
        mining_schedule,
        "_implementation_metadata",
        lambda _root: {"file_count": 1, "digest": "changed-runtime"},
    )
    second = mining_schedule.build_data_fingerprint(
        repo,
        state,
        {"asset_type": "stock", "strategy_ids": []},
    )

    assert first["implementation"] != second["implementation"]
    assert first["digest"] != second["digest"]


def test_selected_strategy_metadata_changes_with_same_size_source_edit(tmp_path) -> None:
    source = tmp_path / "strategies" / "custom" / "demo.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    strategy = SimpleNamespace(execution_backend="matrix_native", file_path=source)
    state = SimpleNamespace(
        strategy_engine=SimpleNamespace(get=lambda _strategy_id: strategy)
    )

    first = mining_schedule._selected_strategy_metadata(
        state,
        ["demo"],
        tmp_path,
    )
    source.write_text("VALUE = 2\n", encoding="utf-8")
    second = mining_schedule._selected_strategy_metadata(
        state,
        ["demo"],
        tmp_path,
    )

    assert first[0]["source"]["size"] == second[0]["source"]["size"]
    assert first[0]["source"]["sha256"] != second[0]["source"]["sha256"]
    assert first[0]["source_tree"]["digest"] != second[0]["source_tree"]["digest"]


def test_fingerprint_rejects_continuously_changing_generation(
    tmp_path,
    monkeypatch,
) -> None:
    repo = FakeRepo(tmp_path)
    state = SimpleNamespace(strategy_engine=None)
    generations = iter(["a", "b", "c", "d"])
    monkeypatch.setattr(repo, "get_matrix_data_generation", lambda _asset: next(generations))

    with pytest.raises(ValueError, match="changed"):
        mining_schedule.build_data_fingerprint(
            repo,
            state,
            {"asset_type": "stock", "strategy_ids": []},
        )
