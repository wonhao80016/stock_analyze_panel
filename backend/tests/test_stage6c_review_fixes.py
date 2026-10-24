"""Stage 6C review follow-ups: fail-closed source units, provider lifetime, distance columns,
live re-injection, signal references from strategies, strategy helper module names."""
from __future__ import annotations

import gc
import json
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import monitor_rules as monitor_rules_api
from app.api import signals as signals_api
from app.data_providers.custom import loader
from app.data_providers.custom.config import config_from_dict
from app.data_providers.custom.provider import GenericHTTPProvider
from app.indicators import pipeline
from app.strategy import config as strategy_config
from app.strategy import custom_signals
from app.strategy.engine import StrategyEngine

DOC = Path(__file__).resolve().parents[2] / "docs" / "custom-data-source.md"
REALTIME_MAP = {
    "code": "symbol", "price": "last_price", "pre_close": "prev_close", "open": "open",
    "high": "high", "low": "low", "vol": "volume", "turnover": "turnover_rate",
}


def _source(units: dict | None = None, transforms: dict | None = None) -> dict:
    realtime = {"url": "https://example.test/realtime", "field_map": REALTIME_MAP}
    if units is not None:
        realtime["units"] = units
    if transforms is not None:
        realtime["transforms"] = transforms
    return {"name": "units_source", "datasets": {"realtime": realtime}}


def _validate(raw: dict) -> list[str]:
    provider = GenericHTTPProvider(config_from_dict(raw))
    try:
        return provider.validate()
    finally:
        provider.close()


# ── 1. B019 fail-closed ─────────────────────────────────


def test_a_mapped_ratio_field_without_a_declared_unit_is_an_error():
    errors = _validate(_source())

    assert any("turnover_rate" in e and "percent 或 decimal" in e for e in errors)


def test_a_declared_unit_clears_the_error():
    assert _validate(_source({"turnover_rate": "decimal"})) == []


def test_dividing_in_transforms_and_declaring_percent_is_a_double_conversion():
    errors = _validate(_source({"turnover_rate": "percent"}, {"turnover_rate": "value / 100"}))

    assert any("turnover_rate" in e and "重複換算" in e for e in errors)


def test_an_undeclared_source_is_listed_in_load_errors(tmp_path):
    (tmp_path / "old_source.yaml").write_text(
        "name: old_source\ndatasets:\n  realtime:\n    url: https://example.test/rt\n"
        "    field_map: {code: symbol, price: last_price, pre_close: prev_close, open: open,"
        " high: high, low: low, vol: volume, pct: change_pct}\n",
        encoding="utf-8",
    )
    try:
        loader.load_all(tmp_path)
        assert "old_source" not in loader.names()
        errors = [e for item in loader.errors() if item.get("name") == "old_source" for e in item["errors"]]
        assert any("change_pct" in e and "units" in e for e in errors)
    finally:
        loader.load_all(tmp_path / "empty")


def test_dataset_test_previews_the_converted_values():
    provider = GenericHTTPProvider(config_from_dict(_source({"turnover_rate": "percent"})))
    provider._request_rows = lambda cfg, **kwargs: [{
        "code": "2330.TWSE", "price": 10.0, "pre_close": 10.0, "open": 10.0,
        "high": 10.0, "low": 10.0, "vol": 1000, "turnover": 0.15,
    }]
    try:
        result = provider.test_dataset("realtime")
    finally:
        provider.close()

    assert result["preview"][0]["turnover_rate"] == pytest.approx(0.0015)


def test_doc_tells_old_configs_to_add_units():
    doc = DOC.read_text(encoding="utf-8")
    assert "遷移" in doc
    migration = doc.split("遷移", 1)[1]
    assert "units" in migration and "percent" in migration and "decimal" in migration


# ── 2. B115 provider lifetime ───────────────────────────


def _write_source(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "live_source.yaml").write_text(
        "name: live_source\ndatasets:\n  realtime:\n    url: https://example.test/realtime\n"
        "    field_map: {code: symbol, price: last_price, pre_close: prev_close, open: open,"
        " high: high, low: low, vol: volume}\n",
        encoding="utf-8",
    )


def test_a_job_holding_the_old_provider_keeps_it_open_past_any_delay(tmp_path, monkeypatch):
    _write_source(tmp_path)
    loader.load_all(tmp_path)
    held = loader.get_provider("live_source")
    client = held._client
    try:
        loader.load_all(tmp_path)
        gc.collect()

        assert not client.is_closed
    finally:
        loader.load_all(tmp_path / "empty")
        held.close()


def test_the_old_provider_closes_once_nothing_holds_it(tmp_path):
    _write_source(tmp_path)
    loader.load_all(tmp_path)
    client = loader.get_provider("live_source")._client

    loader.load_all(tmp_path / "empty")
    gc.collect()

    assert client.is_closed


def test_reloads_are_serialized(tmp_path, monkeypatch):
    _write_source(tmp_path)
    seen: list[bool] = []
    original = loader.load_config

    def recording(path):
        seen.append(loader._LOAD_LOCK.locked())
        return original(path)

    monkeypatch.setattr(loader, "load_config", recording)
    try:
        threads = [threading.Thread(target=loader.load_all, args=(tmp_path,)) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        monkeypatch.setattr(loader, "load_config", original)
        loader.load_all(tmp_path / "empty")

    assert seen and all(seen)


# ── 3. one-sided distance ───────────────────────────────


SYMBOL = "2330.TWSE"


def _raw() -> pl.DataFrame:
    closes = [100.0, 105.0, 95.0]
    return pl.DataFrame({
        "symbol": [SYMBOL] * 3,
        "date": [date(2026, 10, d) for d in (20, 21, 22)],
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1_000_000.0] * 3, "amount": [c * 1_000_000.0 for c in closes],
    })


def _signal(signal_id: str, conditions: list[dict]) -> dict:
    return {"id": signal_id, "name": signal_id, "kind": "entry", "enabled": True, "conditions": conditions}


def _cond(left: str, op: str, right: str) -> dict:
    return {"left": left, "op": op, "right": right, "leftDays": 0, "rightDays": 0}


def test_a_signal_on_one_distance_leaves_neither_distance_column(monkeypatch):
    pipeline.invalidate_taiwan_limit_profile_cache()
    monkeypatch.setattr(pipeline, "_custom_signal_exprs", custom_signals.build_expressions([
        _signal("near_up", [_cond("distance_to_upper_limit", "<", "0.05")]),
    ]))

    out = pipeline.compute_enriched(_raw())

    assert "csg_near_up" in out.columns
    assert "distance_to_upper_limit" not in out.columns
    assert "distance_to_lower_limit" not in out.columns


# ── 4. live re-injection ────────────────────────────────


TAIPEI = timezone(timedelta(hours=8))
TODAY = date(2026, 10, 7)


def test_live_volume_scaling_reevaluates_a_distance_signal(monkeypatch, tmp_path):
    from app.config import settings
    from app.taiwan.live_enriched import _scale_volume_ratio

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    pipeline.invalidate_taiwan_limit_profile_cache()
    monkeypatch.setattr(pipeline, "_custom_signal_exprs", custom_signals.build_expressions([
        _signal("surge_near_up", [
            _cond("vol_ratio_5d", ">=", "2"),
            _cond("distance_to_upper_limit", "<", "0.05"),
        ]),
    ]))
    enriched = pl.DataFrame({
        "symbol": [SYMBOL, SYMBOL],
        "date": [TODAY - timedelta(days=1), TODAY],
        "close": [100.0, 107.0], "raw_close": [100.0, 107.0], "prev_close": [99.0, 100.0],
        "vol_ratio_5d": [1.0, 0.5],
        "signal_volume_surge": [False, False],
        "csg_surge_near_up": [False, False],
    })
    quote_ts = int(datetime(2026, 10, 7, 10, 0, tzinfo=TAIPEI).timestamp() * 1000)

    out = _scale_volume_ratio(enriched, pl.DataFrame({"symbol": [SYMBOL], "quote_ts": [quote_ts]}), TODAY)

    today = out.filter(pl.col("date") == TODAY)
    assert today["vol_ratio_5d"][0] == pytest.approx(2.25)
    assert today["csg_surge_near_up"][0] is True
    assert "distance_to_upper_limit" not in out.columns


# ── 6. B014 follow-ups ──────────────────────────────────


def _rule(rule_id: str, field: str) -> dict:
    return {"id": rule_id, "name": rule_id, "type": "signal", "scope": "all",
            "conditions": [{"field": field, "op": "truth"}]}


def _write_rule(data_dir: Path, rule: dict) -> None:
    d = data_dir / "user_data" / "monitor_rules"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rule['id']}.json").write_text(json.dumps(rule), encoding="utf-8")


def _list_request(data_dir: Path):
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=data_dir), resolve_asset_type=lambda s: "stock")
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo)))


def test_rule_list_warns_about_a_missing_or_disabled_custom_signal(tmp_path, monkeypatch):
    from app.services import kline_sync

    monkeypatch.setattr(kline_sync, "intraday_monitor_support",
                        lambda caps: {"available": True, "reason": "", "max_symbols": 100})
    custom_signals.save_one(tmp_path, _signal("on", [_cond("close", ">", "1")]))
    custom_signals.save_one(tmp_path, {**_signal("off", [_cond("close", ">", "1")]), "enabled": False})
    _write_rule(tmp_path, _rule("uses_on", "csg_on"))
    _write_rule(tmp_path, _rule("uses_off", "csg_off"))
    _write_rule(tmp_path, _rule("uses_gone", "csg_gone"))

    rules = {r["id"]: r for r in monitor_rules_api.list_rules(_list_request(tmp_path))["rules"]}

    assert "runtime_warning" not in rules["uses_on"]
    assert "停用" in rules["uses_off"]["runtime_warning"]
    assert "不存在" in rules["uses_gone"]["runtime_warning"]


STRATEGY = '''import polars as pl

META = {"id": "custom_uses_sig", "name": "用到訊號的策略", "params": [], "scoring": {}}
ENTRY_SIGNALS = ["csg_low_touches_ma5"]

def filter(df, params):
    return pl.lit(True)
'''


@pytest.fixture
def client(tmp_path):
    custom_dir = tmp_path / "strategies" / "custom"
    custom_dir.mkdir(parents=True)
    (custom_dir / "custom_uses_sig.py").write_text(STRATEGY, encoding="utf-8")
    app = FastAPI()
    app.include_router(signals_api.router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path), clear_cache=lambda: None)
    app.state.strategy_engine = StrategyEngine(strategy_dirs=[custom_dir])
    custom_signals.save_one(tmp_path, _signal("low_touches_ma5", [_cond("low", "<=", "field:ma5")]))
    custom_signals.save_one(tmp_path, _signal("in_override", [_cond("low", "<=", "field:ma5")]))
    with TestClient(app) as test_client:
        test_client.data_dir = tmp_path
        yield test_client


def test_references_include_strategy_signals(client):
    refs = client.get("/api/custom-signals/low_touches_ma5/references").json()

    assert refs["strategies"] == [{"id": "custom_uses_sig", "name": "用到訊號的策略"}]
    assert client.delete("/api/custom-signals/low_touches_ma5").status_code == 409


def test_references_include_strategy_parameter_overrides(client):
    strategy_config.save_override(client.data_dir, "custom_uses_sig", {"exit_signals": ["csg_in_override"]})

    refs = client.get("/api/custom-signals/in_override/references").json()

    assert refs["strategies"] == [{"id": "custom_uses_sig", "name": "用到訊號的策略"}]
    assert client.delete("/api/custom-signals/in_override").json()["detail"]["strategies"]


# ── 7. helper module names ──────────────────────────────


@pytest.fixture
def _keep_stdlib_json():
    saved = sys.modules.get("_json")
    yield
    if saved is not None:
        sys.modules["_json"] = saved


def test_a_helper_module_named_like_a_python_module_is_refused(tmp_path, _keep_stdlib_json):
    custom = tmp_path / "strategies" / "custom"
    custom.mkdir(parents=True)
    (custom / "_json.py").write_text("VALUE = 1\n", encoding="utf-8")
    (custom / "custom_uses_helper.py").write_text(
        STRATEGY.replace("custom_uses_sig", "custom_uses_helper").replace('ENTRY_SIGNALS = ["csg_low_touches_ma5"]', ""),
        encoding="utf-8",
    )
    stdlib_json = sys.modules.get("_json")

    engine = StrategyEngine(strategy_dirs=[custom])

    assert sys.modules.get("_json") is stdlib_json
    assert not engine.has("custom_uses_helper")
    assert any("_json" in item["error"] for item in engine.load_errors())


def test_the_documented_mock_source_example_validates():
    from app.data_providers.custom.config import load_config

    example = DOC.parent / "examples" / "custom-data-source" / "mock_source.yaml"
    provider = GenericHTTPProvider(load_config(example))
    try:
        assert provider.validate() == []
        assert provider.config.datasets["realtime"].units == {
            "change_pct": "decimal", "amplitude": "decimal", "turnover_rate": "decimal",
        }
    finally:
        provider.close()
