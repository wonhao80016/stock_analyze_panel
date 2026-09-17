from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings, _PROJECT_ROOT


@pytest.fixture(autouse=True)
def _clear_dir_env(monkeypatch):
    monkeypatch.delenv("CACHE_DIR", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)


def test_cache_dir_defaults_under_data_dir(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "E:/stock-data" if Path("E:/").drive else "/tmp/stock-data")
    s = Settings()
    assert s.cache_dir == s.data_dir / "tmp"


def test_blank_cache_dir_is_treated_as_unset(monkeypatch):
    """`CACHE_DIR=` in .env must not resolve to Path('.') (the project root)."""
    monkeypatch.setenv("CACHE_DIR", "")
    s = Settings()
    assert s.cache_dir == s.data_dir / "tmp"
    assert s.cache_dir != _PROJECT_ROOT


def test_whitespace_cache_dir_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("CACHE_DIR", "   ")
    s = Settings()
    assert s.cache_dir == s.data_dir / "tmp"


def test_absolute_cache_dir_is_honoured(monkeypatch):
    target = "E:/stock-cache" if Path("E:/").drive else "/mnt/stock-cache"
    monkeypatch.setenv("CACHE_DIR", target)
    s = Settings()
    assert s.cache_dir == Path(target)
    assert s.cache_dir.is_absolute()


def test_relative_cache_dir_resolves_against_project_root(monkeypatch):
    monkeypatch.setenv("CACHE_DIR", "./scratch")
    s = Settings()
    assert s.cache_dir == (_PROJECT_ROOT / "scratch").resolve()


def test_cache_dir_follows_data_dir_relocation(monkeypatch):
    """Moving DATA_DIR off the system drive must take the temp files with it."""
    base = "E:/relocated" if Path("E:/").drive else "/mnt/relocated"
    monkeypatch.setenv("DATA_DIR", base)
    s = Settings()
    assert s.cache_dir == Path(base) / "tmp"


def test_temp_root_creates_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "custom-cache"))
    import app.config as config

    monkeypatch.setattr(config, "settings", Settings())
    root = config.temp_root()
    assert root == tmp_path / "custom-cache"
    assert root.is_dir()
