"""Plugin names must be plain directory names before any plugin path is built from them."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import secrets_store
from app.api import settings as settings_api
from app.data_providers import custom as custom_sources
from app.data_providers.custom import loader

UNSAFE_NAMES = [
    "../x",
    "..\\x",
    "..",
    "\\\\host\\share",
    "//host/share",
    "C:\\x",
    "C:x",
    "a/b",
    "Demo",
    "demo\n",
    "demo ",
    "",
    ".",
    "de\x00mo",
]


@pytest.fixture
def plugin_root(monkeypatch, tmp_path) -> Path:
    root = tmp_path / "plugins"
    demo = root / "demo"
    demo.mkdir(parents=True)
    (demo / "plugin.yaml").write_text(
        "name: demo\napi_key_env: DEMO_KEY\nentry: demo_plugin_module:Provider\n", encoding="utf-8",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "plugin.yaml").write_text(
        "name: outside\napi_key_env: OUTSIDE_KEY\nentry: outside_plugin_module:Provider\n", encoding="utf-8",
    )
    monkeypatch.setattr(loader, "plugins_dir", lambda: root)
    return root


@pytest.fixture
def no_filesystem(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("plugin path was built from an unvalidated name")

    monkeypatch.setattr(loader, "plugins_dir", refuse)


@pytest.fixture
def recorded_imports(monkeypatch) -> list[str]:
    imported: list[str] = []

    class FakeModule:
        @staticmethod
        def probe_api_key(key):
            return True, "ok"

    def fake_import(module_path):
        imported.append(module_path)
        return FakeModule

    monkeypatch.setattr(loader.importlib, "import_module", fake_import)
    return imported


@pytest.mark.parametrize("name", UNSAFE_NAMES)
def test_manifest_of_unsafe_name_is_none_without_filesystem_access(no_filesystem, name):
    assert loader.plugin_manifest(name) is None


@pytest.mark.parametrize("name", UNSAFE_NAMES)
def test_dir_of_unsafe_name_raises(no_filesystem, name):
    with pytest.raises(ValueError, match="invalid plugin name"):
        loader.plugin_dir_of(name)


def test_valid_name_reads_its_own_manifest(plugin_root):
    assert loader.plugin_manifest("demo")["entry"] == "demo_plugin_module:Provider"
    assert loader.plugin_dir_of("demo") == plugin_root / "demo"
    assert loader.plugin_manifest("missing") is None


def test_probe_does_not_import_entry_of_manifest_outside_plugins_dir(plugin_root, recorded_imports):
    ok, _ = loader.probe_plugin_key("../outside", "candidate-key")

    assert ok is False
    assert recorded_imports == []


def test_probe_of_valid_plugin_still_imports_its_entry(plugin_root, recorded_imports):
    assert loader.probe_plugin_key("demo", "candidate-key") == (True, "ok")
    assert recorded_imports == ["demo_plugin_module"]


@pytest.mark.parametrize("name", ["../outside", "\\\\host\\share", "demo\n"])
def test_install_and_uninstall_reject_unsafe_names(no_filesystem, monkeypatch, name):
    def refuse_run(*_args, **_kwargs):
        raise AssertionError("no command may run for an unsafe plugin name")

    monkeypatch.setattr(loader.subprocess, "run", refuse_run)
    assert loader.install_plugin(name)[0] is False
    assert loader.uninstall_plugin(name)[0] is False


def test_source_name_with_trailing_newline_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(loader, "data_sources_dir", lambda: tmp_path)

    with pytest.raises(ValueError, match="invalid data source name"):
        loader.save_config("demo\n", {"name": "demo"})
    with pytest.raises(ValueError, match="invalid data source name"):
        loader.delete_config("demo\n")
    assert list(tmp_path.iterdir()) == []


def test_manifest_with_trailing_newline_name_is_not_registered(monkeypatch):
    monkeypatch.setattr(loader, "_PLUGIN_STATUS", {})
    providers: dict = {}

    loader._register_one_plugin({"name": "demo\n", "entry": "demo_plugin_module:Provider"}, providers)

    assert loader._PLUGIN_STATUS == {}
    assert providers == {}


@pytest.fixture
def client(monkeypatch) -> TestClient:
    writes: list = []
    monkeypatch.setattr(secrets_store, "save", lambda updates: writes.append(updates))
    monkeypatch.setattr(secrets_store, "clear", lambda *keys: writes.append(keys))
    monkeypatch.setattr(custom_sources, "load_all", lambda: None)
    app = FastAPI()
    app.include_router(settings_api.router)
    test_client = TestClient(app)
    test_client.writes = writes
    return test_client


@pytest.mark.parametrize("name", ["../outside", "..\\outside", "\\\\host\\share", "C:\\x", "a/b", " de mo "])
def test_save_plugin_key_rejects_unsafe_name(client, no_filesystem, recorded_imports, name):
    resp = client.post("/api/settings/plugin-key", json={"plugin": name, "api_key": "candidate-key"})

    assert resp.status_code == 400
    assert "插件名稱" in resp.json()["detail"]
    assert recorded_imports == []
    assert client.writes == []


@pytest.mark.parametrize("encoded", ["..%5Coutside", "%5C%5Chost%5Cshare", "C%3A%5Cx", "demo%0A"])
def test_clear_plugin_key_rejects_unsafe_name(client, no_filesystem, encoded):
    resp = client.delete(f"/api/settings/plugin-key/{encoded}")

    assert 400 <= resp.status_code < 500
    assert client.writes == []


def test_save_plugin_key_for_valid_plugin_still_saves(client, plugin_root, recorded_imports):
    resp = client.post("/api/settings/plugin-key", json={"plugin": " Demo ", "api_key": "candidate-key"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert client.writes == [{"demo_api_key": "candidate-key"}]


@pytest.mark.parametrize("encoded", ["..%5Coutside", "%5C%5Chost%5Cshare", "demo%0A", "de%00mo"])
@pytest.mark.parametrize("method", ["post", "delete"])
def test_install_routes_reject_unsafe_name(client, no_filesystem, monkeypatch, method, encoded):
    monkeypatch.setattr(custom_sources, "is_builtin", lambda _name: True)

    resp = getattr(client, method)(f"/api/settings/plugins/{encoded}/install")

    assert resp.status_code == 400
