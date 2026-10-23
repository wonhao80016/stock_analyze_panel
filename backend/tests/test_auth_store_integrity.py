"""Integrity of the persisted panel password (user_data/auth.json).

Only a missing file means "no password"; a file that exists but cannot be read or
parsed must lock the API instead of reopening the panel, and a failed write must
leave the previous file intact.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config as app_config
from app.api import auth as auth_api
from app.main import app
from app.services import auth as auth_service

pytestmark = pytest.mark.real_auth

PROBE = "/api/_auth_store_probe"
LOCAL_PEER = ("127.0.0.1", 50000)
REMOTE_PEER = ("203.0.113.9", 50000)
CORRUPT_CODE = "AUTH_STORE_CORRUPT"

CORRUPT_CONTENTS = {
    "truncated_json": b'{"password_hash": "ab',
    "empty_file": b"",
    "not_an_object": b"[]",
    "empty_object": b"{}",
    "hash_only": b'{"password_hash": "abcd"}',
    "salt_only": b'{"password_salt": "abcd"}',
    "missing_salt": b'{"password_hash": "abcd", "sessions": {}}',
    "missing_hash": b'{"password_salt": "abcd", "sessions": {}}',
    "sessions_with_unknown_key": b'{"sessions": {}, "note": "x"}',
    "non_hex_hash": b'{"password_hash": "zz", "password_salt": "abcd"}',
    "sessions_not_object": b'{"password_hash": "abcd", "password_salt": "abcd", "sessions": []}',
    "invalid_utf8": b"\xff\xfe\x00{",
}


@pytest.fixture(autouse=True)
def isolated_auth_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setattr(app_config.settings, "data_dir", tmp_path)
    auth_service._sessions.clear()
    auth_service._configured_cache = None
    auth_api._fail_counter.clear()
    yield
    auth_service._sessions.clear()
    auth_service._configured_cache = None
    auth_api._fail_counter.clear()


@pytest.fixture
def auth_file(tmp_path: Path) -> Path:
    return tmp_path / "user_data" / "auth.json"


def _client(peer: tuple[str, int], base_url: str = "http://127.0.0.1:3018") -> TestClient:
    return TestClient(app, base_url=base_url, client=peer)


def _simulate_restart() -> None:
    auth_service._sessions.clear()
    auth_service._configured_cache = None
    auth_service._restore_sessions()


def _corrupt(auth_file: Path, content: bytes) -> None:
    auth_file.write_bytes(content)
    auth_service._configured_cache = None


def _assert_corrupt_response(res) -> None:
    assert res.status_code == 503, res.text
    body = res.json()
    assert body["code"] == CORRUPT_CODE
    assert "auth.json" in body["detail"]
    assert "AUTH_PASSWORD" in body["detail"]


@pytest.mark.parametrize("content", CORRUPT_CONTENTS.values(), ids=CORRUPT_CONTENTS.keys())
@pytest.mark.parametrize("peer", [LOCAL_PEER, REMOTE_PEER], ids=["local", "remote"])
def test_corrupt_auth_file_locks_every_api_request(
    auth_file: Path, content: bytes, peer: tuple[str, int]
) -> None:
    auth_service.set_password("owner-password")
    _corrupt(auth_file, content)

    client = _client(peer)
    _assert_corrupt_response(client.get(PROBE))
    _assert_corrupt_response(client.get("/api/auth/status"))
    _assert_corrupt_response(client.post("/api/auth/login", json={"password": "owner-password"}))
    assert auth_file.read_bytes() == content


@pytest.mark.parametrize("content", CORRUPT_CONTENTS.values(), ids=CORRUPT_CONTENTS.keys())
def test_setup_cannot_overwrite_a_corrupt_auth_file(auth_file: Path, content: bytes) -> None:
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    _corrupt(auth_file, content)

    res = _client(LOCAL_PEER).post("/api/auth/setup", json={"password": "attacker-pw"})

    _assert_corrupt_response(res)
    assert auth_file.read_bytes() == content


def test_unreadable_auth_file_is_not_treated_as_unconfigured(
    auth_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_service.set_password("owner-password")
    auth_service._configured_cache = None
    real_read_text = Path.read_text

    def deny_auth_file(self: Path, *args, **kwargs) -> str:
        if self.name == "auth.json":
            raise PermissionError(13, "Access is denied", str(self))
        return real_read_text(self, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(Path, "read_text", deny_auth_file)
        client = _client(LOCAL_PEER)
        _assert_corrupt_response(client.get(PROBE))
        _assert_corrupt_response(client.post("/api/auth/setup", json={"password": "attacker-pw"}))
    assert auth_service.verify_and_create_session("owner-password") is not None


def test_logout_after_corruption_does_not_rewrite_the_file(auth_file: Path) -> None:
    auth_service.set_password("owner-password")
    client = _client(LOCAL_PEER)
    assert client.post("/api/auth/login", json={"password": "owner-password"}).status_code == 200
    _corrupt(auth_file, CORRUPT_CONTENTS["truncated_json"])

    res = client.post("/api/auth/logout")

    _assert_corrupt_response(res)
    assert auth_file.read_bytes() == CORRUPT_CONTENTS["truncated_json"]


def test_logout_without_password_does_not_create_auth_file(auth_file: Path) -> None:
    client = _client(LOCAL_PEER)
    client.cookies.set(auth_api.COOKIE_NAME, "stale-token")

    assert client.post("/api/auth/logout").status_code == 200
    assert not auth_file.exists()
    assert client.get("/api/auth/status").json() == {"configured": False, "authenticated": False}


LEGACY_UNCONFIGURED_CONTENTS = {
    "empty_sessions": b'{"sessions": {}}',
    "stale_sessions": b'{"sessions": {"x": 4102444800}}',
}


@pytest.mark.parametrize(
    "content", LEGACY_UNCONFIGURED_CONTENTS.values(), ids=LEGACY_UNCONFIGURED_CONTENTS.keys()
)
def test_legacy_sessions_only_file_means_unconfigured(auth_file: Path, content: bytes) -> None:
    auth_file.parent.mkdir(parents=True, exist_ok=True)
    _corrupt(auth_file, content)
    _simulate_restart()
    assert auth_service._sessions == {}

    client = _client(LOCAL_PEER)
    assert client.get("/api/auth/status").json() == {"configured": False, "authenticated": False}
    assert client.get(PROBE).status_code not in (401, 403, 503)
    assert client.post("/api/auth/logout").status_code == 200
    assert auth_file.read_bytes() == content

    assert client.post("/api/auth/setup", json={"password": "owner-password"}).status_code == 200
    _simulate_restart()
    assert auth_service._sessions == {}
    assert client.post("/api/auth/login", json={"password": "owner-password"}).status_code == 200
    assert client.get("/api/auth/status").json() == {"configured": True, "authenticated": True}


def test_missing_auth_file_still_means_unconfigured(auth_file: Path) -> None:
    client = _client(LOCAL_PEER)

    assert client.get("/api/auth/status").json() == {"configured": False, "authenticated": False}
    assert client.get(PROBE).status_code not in (401, 403, 503)
    assert not auth_file.exists()


def test_setup_login_and_restart_keep_working(auth_file: Path) -> None:
    client = _client(LOCAL_PEER)
    assert client.post("/api/auth/setup", json={"password": "owner-password"}).status_code == 200
    assert client.post("/api/auth/setup", json={"password": "second-password"}).status_code == 409
    assert client.post("/api/auth/login", json={"password": "owner-password"}).status_code == 200
    assert client.get("/api/auth/status").json() == {"configured": True, "authenticated": True}

    _simulate_restart()

    assert client.get("/api/auth/status").json() == {"configured": True, "authenticated": True}
    assert client.get(PROBE).status_code not in (401, 403, 503)
    assert _client(LOCAL_PEER).get(PROBE).status_code == 401


def test_change_password_persists_and_invalidates_sessions() -> None:
    client = _client(LOCAL_PEER)
    assert client.post("/api/auth/setup", json={"password": "owner-password"}).status_code == 200
    assert client.post("/api/auth/login", json={"password": "owner-password"}).status_code == 200

    res = client.post(
        "/api/auth/change-password",
        json={"old_password": "owner-password", "new_password": "rotated-password"},
    )

    assert res.status_code == 200
    _simulate_restart()
    assert auth_service.verify_and_create_session("owner-password") is None
    assert auth_service.verify_and_create_session("rotated-password") is not None


def test_interrupted_write_keeps_the_previous_password(
    auth_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_service.set_password("first-password")
    before = auth_file.read_bytes()
    real_write_text = Path.write_text

    def crash_midway(self: Path, data: str, *args, **kwargs) -> int:
        real_write_text(self, data[: len(data) // 2], *args, **kwargs)
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as patched:
        patched.setattr(Path, "write_text", crash_midway)
        with pytest.raises(OSError):
            auth_service.set_password("second-password")

    assert auth_file.read_bytes() == before
    assert sorted(p.name for p in (tmp_path / "user_data").iterdir()) == ["auth.json"]
    _simulate_restart()
    assert auth_service.is_configured() is True
    assert auth_service.verify_and_create_session("first-password") is not None


def test_failed_replace_keeps_the_previous_password(
    auth_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_service.set_password("first-password")
    before = auth_file.read_bytes()

    def refuse_replace(src, dst, *args, **kwargs) -> None:
        raise OSError(5, "Input/output error")

    with monkeypatch.context() as patched:
        patched.setattr(os, "replace", refuse_replace)
        with pytest.raises(OSError):
            auth_service.set_password("second-password")

    assert auth_file.read_bytes() == before
    assert sorted(p.name for p in (tmp_path / "user_data").iterdir()) == ["auth.json"]
    _simulate_restart()
    assert auth_service.verify_and_create_session("first-password") is not None
    assert auth_service.verify_and_create_session("second-password") is None


def test_concurrent_setup_requests_have_a_single_winner(monkeypatch: pytest.MonkeyPatch) -> None:
    both_hashing = threading.Barrier(2, timeout=5)
    real_hash = auth_service._hash_password

    def hash_in_lockstep(password: str, salt: bytes | None = None) -> tuple[str, str]:
        if salt is None:
            both_hashing.wait()
        return real_hash(password, salt)

    monkeypatch.setattr(auth_service, "_hash_password", hash_in_lockstep)
    results: dict[str, int] = {}

    def run_setup(password: str) -> None:
        res = _client(LOCAL_PEER).post("/api/auth/setup", json={"password": password})
        results[password] = res.status_code

    threads = [threading.Thread(target=run_setup, args=(pw,)) for pw in ("alpha-password", "bravo-password")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(results.values()) == [200, 409]
    winner = next(pw for pw, status in results.items() if status == 200)
    loser = next(pw for pw, status in results.items() if status == 409)
    monkeypatch.setattr(auth_service, "_hash_password", real_hash)
    assert auth_service.verify_and_create_session(winner) is not None
    assert auth_service.verify_and_create_session(loser) is None
