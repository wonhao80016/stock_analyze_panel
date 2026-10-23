"""app/__init__.py must find VERSION in the dev tree, the Docker image and a PyInstaller bundle."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

APP_INIT = Path(__file__).resolve().parents[1] / "app" / "__init__.py"


def _install_package(package_parent: Path) -> None:
    target = package_parent / "app"
    target.mkdir(parents=True)
    shutil.copyfile(APP_INIT, target / "__init__.py")


def _import_version(package_parent: Path, prelude: str = "") -> subprocess.CompletedProcess:
    code = f"import sys; sys.path.insert(0, {str(package_parent)!r}); {prelude}import app; print(app.__version__)"
    return subprocess.run(
        [sys.executable, "-c", code], cwd=package_parent, capture_output=True, text=True, timeout=60
    )


def test_dev_layout_reads_version_from_repo_root(tmp_path):
    repo = tmp_path / "repo"
    _install_package(repo / "backend")
    (repo / "VERSION").write_text("v1.2.3\n", encoding="utf-8")

    result = _import_version(repo / "backend")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1.2.3"


def test_docker_layout_reads_version_next_to_the_package(tmp_path):
    # Dockerfile: WORKDIR /app, COPY backend/app ./app, COPY VERSION /app/VERSION.
    image_app_dir = tmp_path / "app"
    _install_package(image_app_dir)
    (image_app_dir / "VERSION").write_text("v2.0.0\n", encoding="utf-8")

    result = _import_version(image_app_dir)

    assert not (tmp_path / "VERSION").exists()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "2.0.0"


def test_frozen_bundle_reads_version_from_meipass(tmp_path):
    bundle = tmp_path / "_internal"
    bundle.mkdir()
    (bundle / "VERSION").write_text("v3.4.5\n", encoding="utf-8")
    package_parent = tmp_path / "pyz" / "lib"
    _install_package(package_parent)

    result = _import_version(package_parent, prelude=f"sys._MEIPASS = {str(bundle)!r}; ")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "3.4.5"


def test_frozen_bundle_prefers_meipass_over_source_tree(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "VERSION").write_text("v9.9.9\n", encoding="utf-8")
    image_app_dir = tmp_path / "app"
    _install_package(image_app_dir)
    (image_app_dir / "VERSION").write_text("v0.0.1\n", encoding="utf-8")

    result = _import_version(image_app_dir, prelude=f"sys._MEIPASS = {str(bundle)!r}; ")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "9.9.9"


def test_missing_version_names_every_searched_location(tmp_path):
    image_app_dir = tmp_path / "app"
    _install_package(image_app_dir)

    result = _import_version(image_app_dir)

    assert result.returncode != 0
    assert "FileNotFoundError" in result.stderr
    assert str(image_app_dir / "VERSION") in result.stderr
    assert str(tmp_path / "VERSION") in result.stderr
