"""backend.log must keep every record when Windows refuses to rotate a file another process holds open."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest


def _backend_log_handler_type() -> type[RotatingFileHandler]:
    import app.main  # noqa: F401  (attaches the backend.log handler on import)

    for handler in logging.getLogger().handlers:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename).name == "backend.log":
            return type(handler)
    raise AssertionError("backend.log handler is not attached")


def _make_handler(log_path: Path) -> RotatingFileHandler:
    handler = _backend_log_handler_type()(log_path, maxBytes=100, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord("app.tests.log_rotation", logging.INFO, __file__, 0, message, None, None)


@pytest.fixture
def live_log_held_open(monkeypatch, tmp_path):
    """Refuse renaming backend.log the way Windows does while another process has it open."""
    log_path = tmp_path / "backend.log"
    state = {"held": True, "refused": 0}

    def refuse_live_file(rename):
        def wrapper(src, dst, *args, **kwargs):
            if state["held"] and Path(src) == log_path:
                state["refused"] += 1
                raise PermissionError(
                    13, "The process cannot access the file because it is being used by another process", str(src)
                )
            return rename(src, dst, *args, **kwargs)

        return wrapper

    monkeypatch.setattr(os, "rename", refuse_live_file(os.rename))
    monkeypatch.setattr(os, "replace", refuse_live_file(os.replace))
    return log_path, state


def test_records_and_backups_survive_a_refused_rotation(live_log_held_open, capsys):
    log_path, state = live_log_held_open
    for index in (1, 2, 3):
        Path(f"{log_path}.{index}").write_text(f"backup {index}\n", encoding="utf-8")
    log_path.write_text("x" * 200 + "\n", encoding="utf-8")

    handler = _make_handler(log_path)
    try:
        handler.handle(_record("first after refused rotation"))
        handler.handle(_record("second after refused rotation"))
    finally:
        handler.close()

    written = log_path.read_text(encoding="utf-8")
    assert written.startswith("x" * 200)
    assert "first after refused rotation" in written
    assert "second after refused rotation" in written
    for index in (1, 2, 3):
        assert Path(f"{log_path}.{index}").read_text(encoding="utf-8") == f"backup {index}\n"
    assert state["refused"] == 1
    reported = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert len(reported) == 1
    assert str(log_path) in reported[0]


def test_rotation_resumes_once_the_live_file_is_released(live_log_held_open):
    log_path, state = live_log_held_open
    log_path.write_text("old " * 50 + "\n", encoding="utf-8")

    handler = _make_handler(log_path)
    handler.rollover_retry_seconds = 0.0
    try:
        handler.handle(_record("written while held"))
        state["held"] = False
        handler.handle(_record("written after release"))
    finally:
        handler.close()

    rotated = Path(f"{log_path}.1").read_text(encoding="utf-8")
    assert rotated.startswith("old ")
    assert "written while held" in rotated
    assert log_path.read_text(encoding="utf-8") == "written after release\n"
    assert not Path(f"{log_path}.rotating").exists()
