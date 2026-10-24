from __future__ import annotations

import warnings

import pytest

from app.indicators import pipeline
from tests.test_enriched_full_rebuild import _fake_compute_enriched, _write_daily

# B097: the enriched publish collects with the supported streaming engine.

@pytest.mark.parametrize(
    "mode",
    [
        pytest.param({}, id="full"),
        pytest.param({"new_dates_only": True, "symbols": ["600000.SH"]}, id="forward_with_symbols"),
    ],
)
def test_pipeline_emits_no_streaming_deprecation(tmp_path, monkeypatch, mode: dict) -> None:
    _write_daily(tmp_path, "2026-07-14", 14.0)
    _write_daily(tmp_path, "2026-07-15", 15.0)
    monkeypatch.setattr(pipeline, "compute_enriched", _fake_compute_enriched)

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        written = pipeline.run_pipeline(data_dir=tmp_path, **mode)

    assert written >= 2
