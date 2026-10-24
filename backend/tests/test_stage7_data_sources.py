"""Taiwan daily K and adjustment factors stay on the official source; custom sources are created and
updated through separate endpoints so a create never overwrites an existing config."""
from __future__ import annotations

import polars as pl
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import settings as settings_api
from app.config import settings
from app.data_providers import custom as custom_sources
from app.data_providers import registry
from app.data_providers.custom import loader
from app.services import kline_sync, preferences

LEGACY_PREFS = {"daily_data_provider": "mysrc", "adj_factor_provider": "mysrc"}

REALTIME = {
    "url": "https://example.test/rt", "method": "GET", "response_path": "",
    "field_map": {"code": "symbol", "price": "last_price", "pre_close": "prev_close", "open": "open",
                  "high": "high", "low": "low", "vol": "volume", "turnover": "turnover_rate"},
    "units": {"turnover_rate": "percent"},
}


@pytest.fixture
def sources_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    directory = tmp_path / "data_sources"
    directory.mkdir()
    yield directory
    loader.load_all(tmp_path / "none")


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(settings_api.router)
    monkeypatch.setattr(settings_api, "detect_capabilities", lambda: None)
    return TestClient(app)


def test_legacy_custom_routing_reads_back_as_official(monkeypatch):
    monkeypatch.setattr(preferences, "load", lambda: dict(LEGACY_PREFS))
    monkeypatch.setattr(custom_sources, "names", lambda: {"mysrc"})

    assert preferences.get_daily_data_provider() == "taiwan"
    assert preferences.get_adj_factor_provider() == "same_as_daily"


def test_daily_fallback_fetch_uses_the_official_provider_under_legacy_prefs(monkeypatch):
    monkeypatch.setattr(preferences, "load", lambda: dict(LEGACY_PREFS))
    monkeypatch.setattr(custom_sources, "names", lambda: {"mysrc"})
    monkeypatch.setattr(custom_sources, "provider_has_dataset", lambda name, dataset: True)
    custom_calls: list = []
    monkeypatch.setattr(custom_sources, "get_provider", lambda name: custom_calls.append(name))
    official_calls: list = []

    class OfficialProvider:
        def get_daily(self, symbols, **kwargs):
            official_calls.append(list(symbols))
            return pl.DataFrame()

    monkeypatch.setattr(registry, "get_provider", lambda name="taiwan": official_calls.append(name) or OfficialProvider())

    kline_sync.sync_daily_batch(["2330.TWSE"], count=30)

    assert official_calls == ["taiwan", ["2330.TWSE"]]
    assert custom_calls == []
    assert kline_sync.fetch_adj_factor_single("2330.TWSE").is_empty()
    assert custom_calls == []


@pytest.mark.parametrize("body", [
    {"daily_data_provider": "mysrc"},
    {"adj_factor_provider": "mysrc"},
])
def test_custom_daily_or_adj_routing_is_refused(client, monkeypatch, body):
    saved: list = []
    monkeypatch.setattr(preferences, "save", lambda updates: saved.append(updates))

    resp = client.put("/api/settings/preferences/data-providers", json=body)

    assert resp.status_code == 400
    assert saved == []


def test_official_daily_routing_and_custom_realtime_are_accepted(client, monkeypatch):
    saved: list = []
    monkeypatch.setattr(preferences, "save", lambda updates: saved.append(updates))

    resp = client.put("/api/settings/preferences/data-providers", json={
        "daily_data_provider": "taiwan",
        "adj_factor_provider": "same_as_daily",
        "realtime_data_provider": "mysrc",
    })

    assert resp.status_code == 200
    assert saved == [{"daily_data_provider": "taiwan", "adj_factor_provider": "same_as_daily",
                      "realtime_data_provider": "mysrc"}]


def test_create_refuses_an_existing_name_without_touching_its_file(client, sources_dir):
    assert client.post("/api/settings/data-sources", json={"name": "mysrc", "display_name": "v1",
                                                            "datasets": {"realtime": REALTIME}}).status_code == 200
    before = (sources_dir / "mysrc.yaml").read_text(encoding="utf-8")

    resp = client.post("/api/settings/data-sources", json={"name": "MySrc", "display_name": "v2",
                                                           "datasets": {"realtime": REALTIME}})

    assert resp.status_code == 409
    assert (sources_dir / "mysrc.yaml").read_text(encoding="utf-8") == before


def test_create_refuses_a_name_whose_file_failed_to_load(client, sources_dir):
    (sources_dir / "broken.yaml").write_text("name: broken\ndatasets: {}\n", encoding="utf-8")
    loader.load_all()

    resp = client.post("/api/settings/data-sources", json={"name": "broken", "datasets": {"realtime": REALTIME}})

    assert resp.status_code == 409


@pytest.mark.parametrize("name", ["taiwan", "tickflow", "same_as_daily"])
def test_create_refuses_reserved_names(client, sources_dir, name):
    resp = client.post("/api/settings/data-sources", json={"name": name, "datasets": {"realtime": REALTIME}})

    assert resp.status_code == 409
    assert not (sources_dir / f"{name}.yaml").exists()


def test_update_rewrites_an_existing_source(client, sources_dir):
    client.post("/api/settings/data-sources", json={"name": "mysrc", "display_name": "v1",
                                                     "datasets": {"realtime": REALTIME}})

    resp = client.put("/api/settings/data-sources/mysrc", json={"name": "mysrc", "display_name": "v2",
                                                                "datasets": {"realtime": REALTIME}})

    assert resp.status_code == 200
    assert settings_api.get_data_source("mysrc")["display_name"] == "v2"


def test_update_of_an_unknown_source_is_404(client, sources_dir):
    resp = client.put("/api/settings/data-sources/ghost", json={"name": "ghost", "datasets": {"realtime": REALTIME}})

    assert resp.status_code == 404
    assert not (sources_dir / "ghost.yaml").exists()


def test_update_cannot_rename(client, sources_dir):
    client.post("/api/settings/data-sources", json={"name": "mysrc", "datasets": {"realtime": REALTIME}})

    resp = client.put("/api/settings/data-sources/mysrc", json={"name": "other", "datasets": {"realtime": REALTIME}})

    assert resp.status_code == 400
    assert not (sources_dir / "other.yaml").exists()


def test_update_rewrites_a_yml_file_whose_name_differs_from_the_source(client, sources_dir):
    (sources_dir / "legacy.yml").write_text(
        yaml.safe_dump({"name": "mysrc", "display_name": "v1", "datasets": {"realtime": REALTIME}}),
        encoding="utf-8",
    )
    loader.load_all()
    assert "mysrc" in loader.names()

    resp = client.put("/api/settings/data-sources/mysrc", json={"name": "mysrc", "display_name": "v2",
                                                                "datasets": {"realtime": REALTIME}})

    assert resp.status_code == 200
    assert settings_api.get_data_source("mysrc")["display_name"] == "v2"
    assert sorted(p.name for p in sources_dir.iterdir()) == ["legacy.yml"]
    assert yaml.safe_load((sources_dir / "legacy.yml").read_text(encoding="utf-8"))["display_name"] == "v2"


def test_create_refuses_a_name_declared_by_another_file_that_failed_to_load(client, sources_dir):
    (sources_dir / "other.yaml").write_text(
        "name: mysrc\ndatasets:\n  realtime:\n    url: https://example.test/rt\n"
        "    field_map: {code: symbol, turnover: turnover_rate}\n",
        encoding="utf-8",
    )
    loader.load_all()
    assert "mysrc" not in loader.names()

    resp = client.post("/api/settings/data-sources", json={"name": "mysrc", "datasets": {"realtime": REALTIME}})

    assert resp.status_code == 409
    assert not (sources_dir / "mysrc.yaml").exists()


def test_names_are_validated_before_any_file_lookup(client, sources_dir):
    client.post("/api/settings/data-sources", json={"name": "mysrc", "datasets": {"realtime": REALTIME}})

    create = client.post("/api/settings/data-sources", json={"name": "nope/../mysrc", "datasets": {"realtime": REALTIME}})
    update = client.put("/api/settings/data-sources/bad.name", json={"name": "bad.name", "datasets": {"realtime": REALTIME}})

    assert create.status_code == 400
    assert update.status_code == 400
    assert sorted(p.name for p in sources_dir.iterdir()) == ["mysrc.yaml"]


def _write_source(path, name, display_name="v1"):
    path.write_text(yaml.safe_dump({"name": name, "display_name": display_name, "datasets": {"realtime": REALTIME}}),
                    encoding="utf-8")


def test_update_ignores_a_file_named_after_the_source_that_declares_another(client, sources_dir):
    _write_source(sources_dir / "mysrc.yaml", "other", "other source")
    _write_source(sources_dir / "legacy.yml", "mysrc")
    loader.load_all()
    other_before = (sources_dir / "mysrc.yaml").read_text(encoding="utf-8")

    resp = client.put("/api/settings/data-sources/mysrc", json={"name": "mysrc", "display_name": "v2",
                                                                "datasets": {"realtime": REALTIME}})

    assert resp.status_code == 200
    assert (sources_dir / "mysrc.yaml").read_text(encoding="utf-8") == other_before
    assert yaml.safe_load((sources_dir / "legacy.yml").read_text(encoding="utf-8"))["display_name"] == "v2"


def test_update_refuses_when_several_files_declare_the_source(client, sources_dir):
    _write_source(sources_dir / "a.yaml", "mysrc", "a")
    _write_source(sources_dir / "b.yml", "mysrc", "b")
    loader.load_all()
    before = {p.name: p.read_text(encoding="utf-8") for p in sources_dir.iterdir()}

    resp = client.put("/api/settings/data-sources/mysrc", json={"name": "mysrc", "display_name": "v2",
                                                                "datasets": {"realtime": REALTIME}})

    assert resp.status_code == 409
    assert {p.name: p.read_text(encoding="utf-8") for p in sources_dir.iterdir()} == before


def test_create_refuses_when_its_file_name_is_taken_by_another_source(client, sources_dir):
    _write_source(sources_dir / "mysrc.yaml", "other", "other source")
    loader.load_all()
    before = (sources_dir / "mysrc.yaml").read_text(encoding="utf-8")

    resp = client.post("/api/settings/data-sources", json={"name": "mysrc", "datasets": {"realtime": REALTIME}})

    assert resp.status_code == 409
    assert (sources_dir / "mysrc.yaml").read_text(encoding="utf-8") == before


def test_a_failed_write_leaves_the_previous_config_intact(client, sources_dir, monkeypatch):
    _write_source(sources_dir / "mysrc.yaml", "mysrc")
    loader.load_all()
    before = (sources_dir / "mysrc.yaml").read_text(encoding="utf-8")

    def refuse_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("app.taiwan.universe.service.os.replace", refuse_replace)
    with pytest.raises(OSError):
        client.put("/api/settings/data-sources/mysrc", json={"name": "mysrc", "display_name": "v2",
                                                             "datasets": {"realtime": REALTIME}})

    assert (sources_dir / "mysrc.yaml").read_text(encoding="utf-8") == before
    assert sorted(p.name for p in sources_dir.iterdir()) == ["mysrc.yaml"]
