"""Custom data sources: declared realtime units, reload lifetime, documented datasets."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.data_providers.custom import loader
from app.data_providers.custom.config import CustomSourceConfig, DatasetConfig, config_from_dict
from app.data_providers.custom.provider import GenericHTTPProvider

DOC = Path(__file__).resolve().parents[2] / "docs" / "custom-data-source.md"

FIELD_MAP = {
    "code": "symbol", "price": "last_price", "pre_close": "prev_close", "open": "open",
    "high": "high", "low": "low", "vol": "volume",
    "pct": "change_pct", "amp": "amplitude", "turnover": "turnover_rate",
}
# Taiwan percent-quoted turnover: |median| 0.15, below the old 0.31 heuristic cutoff.
ROWS = [
    {"code": f"{code}.TWSE", "price": 10.0, "pre_close": 10.0, "open": 10.0, "high": 10.0,
     "low": 10.0, "vol": 1000, "pct": 0.1, "amp": 0.2, "turnover": t}
    for code, t in zip(("2330", "2317", "2454", "2303", "1101", "2881"), (0.12, 0.15, 0.2, 0.1, 0.18, 0.14), strict=True)
]


def _provider(units: dict | None = None) -> GenericHTTPProvider:
    raw = {"name": "units_source", "datasets": {"realtime": {
        "url": "https://example.test/realtime", "field_map": FIELD_MAP,
        **({"units": units} if units is not None else {}),
    }}}
    provider = GenericHTTPProvider(config_from_dict(raw))
    provider._request_rows = lambda cfg, **kwargs: [dict(r) for r in ROWS]
    return provider


def _realtime(units: dict | None) -> dict[str, dict]:
    provider = _provider(units)
    try:
        return {r["symbol"]: r for r in provider.get_realtime()}
    finally:
        provider.close()


# ── B019 ────────────────────────────────────────────────


def test_declared_percent_turnover_is_divided_even_below_the_old_cutoff():
    rows = _realtime({"turnover_rate": "percent", "change_pct": "percent", "amplitude": "percent"})

    assert rows["2317.TWSE"]["turnover_rate"] == pytest.approx(0.0015)
    assert rows["2317.TWSE"]["change_pct"] == pytest.approx(0.001)
    assert rows["2317.TWSE"]["amplitude"] == pytest.approx(0.002)


def test_undeclared_units_take_the_decimal_contract_default_without_guessing():
    big = [dict(r, turnover=5.0, pct=3.0) for r in ROWS]
    provider = _provider(None)
    provider._request_rows = lambda cfg, **kwargs: [dict(r) for r in big]
    try:
        rows = {r["symbol"]: r for r in provider.get_realtime()}
    finally:
        provider.close()

    assert rows["2330.TWSE"]["turnover_rate"] == pytest.approx(5.0)
    assert rows["2330.TWSE"]["change_pct"] == pytest.approx(3.0)


def test_invalid_unit_declarations_are_validation_errors():
    provider = _provider({"turnover_rate": "basis_points", "volume": "percent"})
    try:
        errors = provider.validate()
    finally:
        provider.close()

    assert any("turnover_rate" in e and "basis_points" in e for e in errors)
    assert any("volume" in e for e in errors)


def test_units_round_trip_through_the_saved_yaml(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "data_sources_dir", lambda: tmp_path)
    raw = {"name": "units_source", "datasets": {"realtime": {
        "url": "https://example.test/realtime", "field_map": FIELD_MAP,
        "units": {"turnover_rate": "percent", "change_pct": "decimal", "amplitude": "decimal"},
    }}}

    loader.save_config("units_source", raw)
    loader.load_all(tmp_path)
    try:
        saved = loader.get_config_dict("units_source")
    finally:
        loader.load_all(tmp_path / "empty")

    assert saved["datasets"]["realtime"]["units"] == {
        "turnover_rate": "percent", "change_pct": "decimal", "amplitude": "decimal",
    }


def test_dataset_test_defaults_to_a_taiwan_symbol():
    provider = GenericHTTPProvider(CustomSourceConfig(
        name="daily_source", display_name="d",
        datasets={"daily": DatasetConfig(url="https://example.test/daily")},
    ))
    seen: dict = {}

    def fake_rows(cfg, **kwargs):
        seen.update(kwargs)
        return []

    provider._request_rows = fake_rows
    try:
        provider.test_dataset("daily")
    finally:
        provider.close()

    assert seen["symbols"] == ["2330.TWSE"]


def test_doc_describes_declared_units_and_no_heuristic():
    doc = DOC.read_text(encoding="utf-8")

    assert "units:" in doc
    assert "中位數" not in doc
    assert "自動歸一" not in doc


# ── B115 ────────────────────────────────────────────────


def _write_source(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "live_source.yaml").write_text(
        "name: live_source\n"
        "datasets:\n"
        "  realtime:\n"
        "    url: https://example.test/realtime\n"
        "    field_map: {code: symbol, price: last_price, pre_close: prev_close, open: open,"
        " high: high, low: low, vol: volume}\n",
        encoding="utf-8",
    )


def test_reload_keeps_the_provider_in_use_open(tmp_path):
    _write_source(tmp_path)
    loader.load_all(tmp_path)
    in_use = loader.get_provider("live_source")
    try:
        loader.load_all(tmp_path)

        assert loader.get_provider("live_source") is not in_use
        assert not in_use._client.is_closed
    finally:
        loader.load_all(tmp_path / "empty")
        in_use.close()


# ── B116 ────────────────────────────────────────────────


def test_doc_does_not_claim_the_financial_dataset_is_synced():
    doc = DOC.read_text(encoding="utf-8")
    support = doc.split("## 配置位置", 1)[0]

    assert "| 財務數據 | `financial` |" not in support
    assert "financial" in doc and "沒有任何同步流程會呼叫" in doc
