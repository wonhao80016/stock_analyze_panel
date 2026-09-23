from __future__ import annotations

from pathlib import Path

import polars as pl

from app.taiwan.enriched_refresh import TaiwanEnrichedRefreshService


def test_temp_file_is_invisible_to_the_enriched_reader_glob(tmp_path, monkeypatch):
    seen: list[list[str]] = []
    real_replace = Path.replace

    def _spy(self, target):
        seen.append(sorted(p.name for p in Path(target).parent.glob("*.parquet")))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", _spy)
    partition = tmp_path / "date=2026-09-23"

    TaiwanEnrichedRefreshService._write_partition(partition, pl.DataFrame({"symbol": ["2330.TWSE"], "close": [1.0]}))
    TaiwanEnrichedRefreshService._write_partition(partition, pl.DataFrame({"symbol": ["2330.TWSE"], "close": [2.0]}))

    assert seen == [[], ["part.parquet"]]
    assert sorted(p.name for p in partition.iterdir()) == ["part.parquet"]
