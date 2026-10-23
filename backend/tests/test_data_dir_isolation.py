"""The conftest keeps every test away from the project's data directory and the live security master."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import Settings, settings, temp_root

REPO_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _outside_repo_data(path: Path) -> bool:
    return not path.resolve().is_relative_to(REPO_DATA_DIR.resolve())


def test_settings_point_outside_the_project_data_dir():
    assert _outside_repo_data(settings.data_dir)
    assert _outside_repo_data(temp_root())
    assert _outside_repo_data(Settings().data_dir)


def test_backend_log_is_written_under_the_session_data_dir():
    import logging

    import app.main  # noqa: F401  # attaches the backend.log handler

    log_files = [
        Path(h.baseFilename) for h in logging.getLogger().handlers if getattr(h, "baseFilename", None)
    ]
    assert any(p.name == "backend.log" for p in log_files)
    assert all(_outside_repo_data(p) for p in log_files)


def test_the_shared_security_master_is_synthetic_and_never_refreshes():
    from app.taiwan.universe import get_security_master

    master = get_security_master()
    assert _outside_repo_data(master.cache_path)
    assert master.get_instrument("2330.TWSE").name == "台積電"
    with pytest.raises(RuntimeError, match="must not refresh"):
        master.refresh()


BACKEND_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent


def _run_isolation(code: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    script = f"import sys; sys.path[:0] = [{str(BACKEND_DIR)!r}, {str(TESTS_DIR)!r}]\n" + code
    return subprocess.run(
        [sys.executable, "-c", script], cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=300
    )


def test_isolation_overrides_a_data_dir_and_env_file_set_before_the_session(tmp_path):
    preset = tmp_path / "preset-data"
    env_file = tmp_path / "developer.env"
    env_file.write_text(f"DATA_DIR={tmp_path / 'from-env-file'}\nAUTH_PASSWORD=leaked-secret\n", encoding="utf-8")
    env = {**os.environ, "DATA_DIR": str(preset), "CACHE_DIR": str(preset / "tmp"), "TICKFLOW_ENV_FILE": str(env_file)}
    code = (
        "import json, shutil, conftest\n"
        "conftest._isolate_data_dir()\n"
        "from app.config import settings\n"
        "print(json.dumps({'root': str(conftest._session_root), 'data_dir': str(settings.data_dir),\n"
        "                  'cache_dir': str(settings.cache_dir), 'auth_password': settings.auth_password}))\n"
        "shutil.rmtree(conftest._session_root, ignore_errors=True)\n"
    )

    completed = _run_isolation(code, env)

    assert completed.returncode == 0, completed.stderr[-3000:]
    seen = json.loads(completed.stdout.strip().splitlines()[-1])
    root = Path(seen["root"])
    assert Path(seen["data_dir"]) == root / "data"
    assert Path(seen["cache_dir"]).is_relative_to(root)
    assert seen["auth_password"] == ""
    assert not preset.exists()


def test_isolation_refuses_to_start_once_app_config_is_imported():
    completed = _run_isolation("import app.config, conftest\nconftest._isolate_data_dir()\n", dict(os.environ))

    assert completed.returncode != 0
    assert "app.config was imported before tests/conftest.py could isolate DATA_DIR" in completed.stderr


def test_custom_source_tokens_never_read_a_dotenv_outside_the_session_dir(monkeypatch):
    import pathlib

    from app.data_providers.custom import provider

    session_root = settings.data_dir.parent.resolve()
    consulted: list[Path] = []
    real_exists = pathlib.Path.exists

    def recording_exists(self, *args, **kwargs):
        consulted.append(Path(self))
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "exists", recording_exists)
    monkeypatch.delenv("STAGE1_PROBE_TOKEN", raising=False)

    assert provider._token_from_env("STAGE1_PROBE_TOKEN") is None
    assert len(consulted) == 3
    assert all(p.resolve().is_relative_to(session_root) for p in consulted), consulted
    assert Path(os.getcwd()).parent / ".env" not in consulted


def test_numba_caches_outside_the_watched_backend_tree():
    numba_config = pytest.importorskip("numba.core.config")

    assert os.environ["NUMBA_CACHE_DIR"] == numba_config.CACHE_DIR
    assert not Path(numba_config.CACHE_DIR).resolve().is_relative_to(BACKEND_DIR)
