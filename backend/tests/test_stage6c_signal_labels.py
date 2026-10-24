"""Signal ids and labels: the high-turnover entry id, and backend labels matching the frontend."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import polars as pl

from app.backtest.matrix import build_market_data_matrix
from app.strategy.builtin import high_turnover_surge
from app.strategy.field_labels import FIELD_LABELS

SIGNALS_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "signals.ts"


def _frontend_labels() -> dict[str, str]:
    text = SIGNALS_TS.read_text(encoding="utf-8")
    labels = dict(re.findall(r"id: '(signal_[a-z0-9_]+)',\s*name: '([^']+)'", text))
    labels.update(re.findall(r"^\s+(signal_[a-z0-9_]+): '([^']+)',", text, flags=re.MULTILINE))
    return labels


# ── B163 ────────────────────────────────────────────────


def test_high_turnover_entry_has_its_own_signal_id():
    assert high_turnover_surge.ENTRY_SIGNALS == ["signal_high_turnover_surge_entry"]
    panel = pl.DataFrame({
        "symbol": ["2330.TWSE"] * 2,
        "date": [date(2026, 10, 21), date(2026, 10, 22)],
        "open": [100.0, 104.0], "high": [100.0, 104.0], "low": [100.0, 104.0],
        "close": [100.0, 104.0], "volume": [1000.0, 1000.0], "turnover_rate": [5.0, 5.0],
    })
    market = build_market_data_matrix(panel, field_columns={"turnover_rate"})

    signals = high_turnover_surge.MATRIX_STRATEGY.compute_signals(
        market, {"use_turnover_filter": True, "min_turnover": 2.0, "use_change_filter": False}
    )

    assert signals.entry_signal_ids == ("signal_high_turnover_surge_entry",)


def test_high_turnover_entry_is_labelled_on_both_sides():
    assert FIELD_LABELS["signal_high_turnover_surge_entry"] == "高換手拉升"
    assert _frontend_labels()["signal_high_turnover_surge_entry"] == "高換手拉升"


# ── B170 ────────────────────────────────────────────────


def test_every_signal_label_matches_the_frontend():
    frontend = _frontend_labels()
    shared = sorted(set(frontend) & {k for k in FIELD_LABELS if k.startswith("signal_")})

    assert "signal_broken_limit_up" in shared
    assert "signal_limit_down_recovery" in shared
    mismatched = {k: (FIELD_LABELS[k], frontend[k]) for k in shared if FIELD_LABELS[k] != frontend[k]}
    assert mismatched == {}


def test_backend_signal_labels_all_exist_in_the_frontend():
    frontend = _frontend_labels()
    missing = sorted(k for k in FIELD_LABELS if k.startswith("signal_") and k not in frontend)

    assert missing == []
