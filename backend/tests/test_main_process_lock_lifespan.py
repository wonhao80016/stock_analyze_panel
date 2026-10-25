from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import app.main as main_module


def test_lifespan_holds_data_dir_process_lock_around_application(monkeypatch) -> None:
    events: list[str] = []

    class LockStub:
        def __init__(self, data_dir) -> None:
            del data_dir
            events.append("lock_created")

        def acquire(self) -> None:
            events.append("lock_acquired")

        def release(self) -> None:
            events.append("lock_released")

    @asynccontextmanager
    async def application_lifespan(_app):
        events.append("application_started")
        try:
            yield
        finally:
            events.append("application_stopped")

    monkeypatch.setattr(main_module, "DataDirProcessLock", LockStub)
    monkeypatch.setattr(main_module, "_application_lifespan", application_lifespan)

    async def exercise() -> None:
        async with main_module.lifespan(SimpleNamespace()):
            events.append("request_serving")

    asyncio.run(exercise())

    assert events == [
        "lock_created",
        "lock_acquired",
        "application_started",
        "request_serving",
        "application_stopped",
        "lock_released",
    ]


def test_lifespan_releases_lock_when_application_shutdown_raises(monkeypatch) -> None:
    events: list[str] = []

    class LockStub:
        def __init__(self, data_dir) -> None:
            del data_dir

        def acquire(self) -> None:
            events.append("acquired")

        def release(self) -> None:
            events.append("released")

    @asynccontextmanager
    async def application_lifespan(_app):
        try:
            yield
        finally:
            raise RuntimeError("shutdown failed")

    monkeypatch.setattr(main_module, "DataDirProcessLock", LockStub)
    monkeypatch.setattr(main_module, "_application_lifespan", application_lifespan)

    async def exercise() -> None:
        async with main_module.lifespan(SimpleNamespace()):
            pass

    with pytest.raises(RuntimeError, match="shutdown failed"):
        asyncio.run(exercise())

    assert events == ["acquired", "released"]


def test_second_backend_fails_before_startup_when_data_dir_is_owned(
    monkeypatch, tmp_path
) -> None:
    from app.services.data_dir_process_lock import DataDirProcessLock, DataDirProcessLockError

    started: list[str] = []

    @asynccontextmanager
    async def application_lifespan(_app):
        started.append("application_started")
        yield

    monkeypatch.setattr(main_module.settings, "data_dir", tmp_path)
    monkeypatch.setattr(main_module, "_application_lifespan", application_lifespan)
    owner = DataDirProcessLock(tmp_path)
    owner.acquire()

    async def exercise() -> None:
        async with main_module.lifespan(SimpleNamespace()):
            pass

    try:
        with pytest.raises(DataDirProcessLockError, match="already using this data directory"):
            asyncio.run(exercise())
    finally:
        owner.release()

    assert started == []
    asyncio.run(exercise())
    assert started == ["application_started"]
