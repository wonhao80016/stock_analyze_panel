"""Stage 6C second review: helper discovery, repairing a source that fails to load, realtime-only
unit enforcement, merged rule warnings, plugin provider lifetime."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import monitor_rules as monitor_rules_api
from app.api import settings as settings_api
from app.config import settings
from app.data_providers.custom import loader
from app.data_providers.custom.config import config_from_dict
from app.data_providers.custom.provider import GenericHTTPProvider
from app.strategy import custom_signals
from app.strategy.engine import StrategyEngine

BUILTIN_DIR = Path(__file__).resolve().parents[1] / "app" / "strategy" / "builtin"
DOC = Path(__file__).resolve().parents[2] / "docs" / "custom-data-source.md"


# ── R1 ──────────────────────────────────────────────────


def test_builtins_load_while_a_bare_init_module_is_importable(tmp_path, monkeypatch):
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(bare))
    expected = len([p for p in BUILTIN_DIR.glob("*.py") if not p.name.startswith("_")])

    engine = StrategyEngine(strategy_dirs=[BUILTIN_DIR])

    assert engine.load_errors() == []
    assert len(engine.strategy_definitions()) == expected


# ── R2 ──────────────────────────────────────────────────


REALTIME = {
    "url": "https://example.test/rt", "method": "GET", "response_path": "",
    "field_map": {"code": "symbol", "price": "last_price", "pre_close": "prev_close", "open": "open",
                  "high": "high", "low": "low", "vol": "volume", "turnover": "turnover_rate"},
}


@pytest.fixture
def sources_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    directory = tmp_path / "data_sources"
    directory.mkdir()
    yield directory
    loader.load_all(tmp_path / "none")


def test_get_opens_a_source_that_failed_to_load_with_its_error(sources_dir):
    (sources_dir / "broken.yaml").write_text(
        "name: broken\ndatasets:\n  realtime:\n    url: https://example.test/rt\n"
        "    field_map: {code: symbol, price: last_price, pre_close: prev_close, open: open,"
        " high: high, low: low, vol: volume, turnover: turnover_rate}\n",
        encoding="utf-8",
    )
    loader.load_all()

    cfg = settings_api.get_data_source("broken")

    assert cfg["datasets"]["realtime"]["field_map"]["turnover"] == "turnover_rate"
    assert any("turnover_rate" in e for e in cfg["load_error"])


def test_saving_an_invalid_source_is_refused_without_writing(sources_dir):
    req = settings_api.CustomSourceIn(name="fresh", datasets={"realtime": REALTIME})

    with pytest.raises(HTTPException) as exc:
        settings_api.save_data_source(req)

    assert exc.value.status_code == 400
    assert "turnover_rate" in str(exc.value.detail)
    assert not (sources_dir / "fresh.yaml").exists()


def test_saving_declared_units_keeps_them(sources_dir):
    req = settings_api.CustomSourceIn(
        name="fixed", datasets={"realtime": {**REALTIME, "units": {"turnover_rate": "percent"}}},
    )

    settings_api.save_data_source(req)

    assert "fixed" in loader.names()
    assert settings_api.get_data_source("fixed")["datasets"]["realtime"]["units"] == {"turnover_rate": "percent"}


# ── R3 ──────────────────────────────────────────────────


@pytest.mark.parametrize("dataset", ["daily", "minute"])
def test_only_realtime_requires_declared_units(dataset):
    field_map = {
        "code": "symbol", "d": "date" if dataset == "daily" else "datetime", "o": "open", "h": "high",
        "l": "low", "c": "close", "v": "volume", "a": "amount", "pct": "change_pct",
    }
    provider = GenericHTTPProvider(config_from_dict(
        {"name": "x", "datasets": {dataset: {"url": "https://example.test/k", "field_map": field_map}}}
    ))
    try:
        assert provider.validate() == []
    finally:
        provider.close()


# ── R4 ──────────────────────────────────────────────────


def test_doc_says_realtime_must_declare_and_never_defaults():
    doc = DOC.read_text(encoding="utf-8")

    assert "視為小數" not in doc
    assert "視為 `decimal`" not in doc
    assert "realtime" in doc and "拒絕載入" in doc


# ── R5 ──────────────────────────────────────────────────


def test_rule_warnings_are_merged_not_overwritten(tmp_path, monkeypatch):
    from app.services import kline_sync
    from app.strategy import monitor_rules

    monkeypatch.setattr(kline_sync, "intraday_monitor_support",
                        lambda caps: {"available": True, "reason": "", "max_symbols": 100})
    monkeypatch.setattr(monitor_rules, "taiwan_index_condition_error", lambda rule: "指數條件不支援")
    rules_dir = tmp_path / "user_data" / "monitor_rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "r.json").write_text(json.dumps({
        "id": "r", "name": "r", "type": "signal", "scope": "all",
        "conditions": [{"field": "csg_gone", "op": "truth"}],
    }), encoding="utf-8")
    custom_signals.load_all(tmp_path)
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path), resolve_asset_type=lambda s: "stock")

    rule = monitor_rules_api.list_rules(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo))))["rules"][0]

    assert "指數條件不支援" in rule["runtime_warning"]
    assert "csg_gone" in rule["runtime_warning"]
    assert "; " in rule["runtime_warning"]


# ── R6 ──────────────────────────────────────────────────


class _Plugin:
    closed = 0

    def close(self) -> None:
        type(self).closed += 1


def test_a_retired_plugin_provider_is_closed_once(monkeypatch, tmp_path):
    _Plugin.closed = 0

    def register(providers):
        plugin = _Plugin()
        plugin.builtin = True
        providers["fake_plugin"] = plugin

    monkeypatch.setattr(loader, "_load_builtin_plugins", register)
    loader.load_all(tmp_path)
    assert _Plugin.closed == 0

    loader.load_all(tmp_path)
    assert _Plugin.closed == 1

    monkeypatch.undo()
    loader.load_all(tmp_path / "none")
    assert _Plugin.closed == 2


def test_a_retired_http_provider_is_not_closed_by_retirement(tmp_path):
    (tmp_path / "s.yaml").write_text(
        "name: s\ndatasets:\n  daily:\n    url: https://example.test/d\n"
        "    field_map: {c: symbol, d: date, o: open, h: high, l: low, cl: close, v: volume, a: amount}\n",
        encoding="utf-8",
    )
    loader.load_all(tmp_path)
    held = loader.get_provider("s")
    try:
        loader.load_all(tmp_path / "none")
        assert not held._client.is_closed
    finally:
        held.close()
