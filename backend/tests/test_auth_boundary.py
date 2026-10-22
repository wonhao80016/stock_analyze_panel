"""Access boundary of the panel API: who may call it before and after a password is set.

Without a password only loopback clients that address the panel by a loopback
host name are trusted, and a browser page from another origin may never call
the API. These tests drive the real app (no lifespan) with explicit peers.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import config as app_config
from app.api import auth as auth_api
from app.main import app
from app.services import auth as auth_service

pytestmark = pytest.mark.real_auth

# Unrouted API path: anything but 401/403 means the auth middleware let the request through.
PROBE = "/api/_auth_boundary_probe"
LOCAL_V4 = ("127.0.0.1", 50000)
LOCAL_V6 = ("::1", 50000)
LAN_PEER = ("192.168.1.20", 50000)
DOCKER_GATEWAY = ("172.17.0.1", 50000)
REMOTE_PEER = ("203.0.113.9", 50000)
EVIL_ORIGIN = "https://evil.example"


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


def _client(
    peer: tuple[str, int],
    base_url: str = "http://127.0.0.1:3018",
    asgi=app,
    host: str | None = None,
) -> TestClient:
    headers = {"host": host} if host else None
    return TestClient(asgi, base_url=base_url, client=peer, headers=headers)


def _passed(res) -> bool:
    return res.status_code not in (401, 403)


def test_preflight_from_foreign_origin_is_rejected_without_cors_grant() -> None:
    res = _client(LOCAL_V4).options(
        "/api/settings/data-sources/test",
        headers={
            "Origin": EVIL_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert res.status_code == 403
    assert "access-control-allow-origin" not in res.headers


def test_simple_cross_origin_post_cannot_take_over_setup() -> None:
    res = _client(LOCAL_V4).post(
        "/api/auth/setup",
        json={"password": "attacker-pw"},
        headers={"Origin": EVIL_ORIGIN},
    )
    assert res.status_code == 403
    assert "access-control-allow-origin" not in res.headers
    assert auth_service.is_configured() is False


def test_cross_origin_api_call_is_rejected_even_from_loopback() -> None:
    res = _client(LOCAL_V4).post(PROBE, content="x", headers={"Origin": EVIL_ORIGIN})
    assert res.status_code == 403


@pytest.mark.parametrize("origin", ["null", "http://[::1", "http://127.0.0.1:99999", "file://"])
def test_null_or_malformed_origin_is_rejected(origin: str) -> None:
    res = _client(LOCAL_V4).post(PROBE, content="x", headers={"Origin": origin})
    assert res.status_code == 403


@pytest.mark.parametrize("host", ["[::1", "localhost.", "localhost.:3018"])
def test_malformed_or_trailing_dot_host_is_not_treated_as_local(host: str) -> None:
    res = _client(LOCAL_V4, host=host).get(PROBE)
    assert res.status_code == 403


@pytest.mark.parametrize(
    ("host", "origin"),
    [
        ("panel.example.com", "https://panel.example.com:8443"),
        ("panel.example.com:8443", "https://panel.example.com"),
        ("localhost:3018", "http://localhost:3011"),
    ],
)
def test_origin_with_a_different_port_is_rejected(host: str, origin: str) -> None:
    auth_service.set_password("owner-password")
    client = _client(REMOTE_PEER, host=host)
    res = client.post("/api/auth/login", json={"password": "owner-password"}, headers={"Origin": origin})
    assert res.status_code == 403
    assert auth_api.COOKIE_NAME not in client.cookies


def test_origin_with_the_same_non_default_port_passes() -> None:
    auth_service.set_password("owner-password")
    client = _client(REMOTE_PEER, host="panel.example.com:8443")
    res = client.post(
        "/api/auth/login",
        json={"password": "owner-password"},
        headers={"Origin": "https://panel.example.com:8443"},
    )
    assert res.status_code == 200


def test_lan_client_is_rejected_without_password_with_guidance() -> None:
    client = _client(LAN_PEER, base_url="http://192.168.1.10:3018")
    res = client.get(PROBE)
    assert res.status_code == 403
    body = res.json()
    assert body["code"] == "NOT_INITIALIZED"
    assert "尚未初始化访问密码" in body["detail"]
    assert "AUTH_PASSWORD" in body["detail"]

    setup = client.post("/api/auth/setup", json={"password": "lan-password"})
    assert setup.status_code == 403
    assert "AUTH_PASSWORD" in setup.json()["detail"]
    assert auth_service.is_configured() is False


def test_docker_bridge_gateway_counts_as_non_local() -> None:
    res = _client(DOCKER_GATEWAY, base_url="http://localhost:3018").get(PROBE)
    assert res.status_code == 403


@pytest.mark.parametrize(
    ("peer", "host"),
    [
        (LOCAL_V4, "127.0.0.1:3018"),
        (LOCAL_V4, "localhost:3018"),
        (LOCAL_V4, "LOCALHOST"),
        (LOCAL_V6, "[::1]:3018"),
        (("::ffff:127.0.0.1", 50000), "127.0.0.1:3018"),
    ],
)
def test_loopback_client_with_loopback_host_passes(peer: tuple[str, int], host: str) -> None:
    client = _client(peer, host=host)
    assert _passed(client.get(PROBE))
    assert _passed(client.post(PROBE, content="x", headers={"Origin": f"http://{host}"}))


def test_vite_proxy_same_origin_request_passes() -> None:
    # Vite forwards /api without changeOrigin, so Host and Origin both keep the dev server address.
    client = _client(LOCAL_V4, base_url="http://localhost:3011")
    res = client.post(PROBE, content="x", headers={"Origin": "http://localhost:3011"})
    assert _passed(res)

    setup = client.post(
        "/api/auth/setup",
        json={"password": "owner-password"},
        headers={"Origin": "http://localhost:3011"},
    )
    assert setup.status_code == 200
    assert auth_service.is_configured() is True


@pytest.mark.parametrize(
    ("forwarded_for", "allowed"),
    [("127.0.0.1", True), ("192.168.1.20", False), ("127.0.0.1, 192.168.1.20", False)],
)
def test_vite_proxy_with_xfwd_exposes_the_real_dev_client(forwarded_for: str, allowed: bool) -> None:
    # The dev proxy appends its own peer to X-Forwarded-For and keeps the browser's Host.
    proxied = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")
    client = _client(LOCAL_V4, asgi=proxied, host="localhost:3011")
    res = client.get(PROBE, headers={"X-Forwarded-For": forwarded_for})
    assert _passed(res) is allowed


@pytest.mark.parametrize("peer", [("::ffff:127.0.0.1", 50000), LOCAL_V6])
@pytest.mark.parametrize(
    ("forwarded_for", "allowed"),
    [("127.0.0.1", True), ("::1", True), ("192.168.1.20", False), ("127.0.0.1, 192.168.1.20", False)],
)
def test_untrusted_loopback_proxy_cannot_hide_a_lan_client(
    peer: tuple[str, int], forwarded_for: str, allowed: bool
) -> None:
    # Dev proxy reaching a dual-stack backend from an address outside FORWARDED_ALLOW_IPS:
    # uvicorn leaves X-Forwarded-For unapplied, so the peer alone looks like loopback.
    proxied = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")
    client = _client(peer, asgi=proxied, host="localhost:3011")
    res = client.get(PROBE, headers={"X-Forwarded-For": forwarded_for})
    assert _passed(res) is allowed


@pytest.mark.parametrize(
    ("headers", "allowed"),
    [
        ([("X-Forwarded-For", "127.0.0.1"), ("X-Forwarded-For", "::1")], True),
        ([("X-Forwarded-For", "127.0.0.1"), ("X-Forwarded-For", "192.168.1.20")], False),
        ([("X-Forwarded-For", "192.168.1.20"), ("X-Forwarded-For", "127.0.0.1")], False),
    ],
)
def test_every_repeated_forwarded_for_header_is_checked(
    headers: list[tuple[str, str]], allowed: bool
) -> None:
    client = _client(LOCAL_V6, host="localhost:3011")
    assert _passed(client.get(PROBE, headers=headers)) is allowed


def test_rebinding_host_name_is_rejected_without_password() -> None:
    client = _client(LOCAL_V4, base_url="http://rebind.evil.example:3018")
    assert client.get(PROBE).status_code == 403

    setup = client.post(
        "/api/auth/setup",
        json={"password": "attacker-pw"},
        headers={"Origin": "http://rebind.evil.example:3018"},
    )
    assert setup.status_code == 403
    assert auth_service.is_configured() is False


def test_forged_forwarded_for_does_not_make_a_proxied_client_local() -> None:
    # Panel behind Docker with a proxy in front: the peer is the bridge gateway, which is not
    # in FORWARDED_ALLOW_IPS, so uvicorn keeps it and a client-supplied XFF must not be read.
    proxied = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")
    client = _client(DOCKER_GATEWAY, base_url="http://localhost:3018", asgi=proxied)
    headers = {"X-Forwarded-For": "127.0.0.1"}

    assert client.get(PROBE, headers=headers).status_code == 403
    setup = client.post("/api/auth/setup", json={"password": "attacker-pw"}, headers=headers)
    assert setup.status_code == 403
    assert auth_service.is_configured() is False


def test_rotating_forwarded_for_does_not_reset_login_rate_limit() -> None:
    auth_service.set_password("owner-password")
    client = _client(DOCKER_GATEWAY, base_url="http://localhost:3018")

    statuses = [
        client.post(
            "/api/auth/login",
            json={"password": "wrong"},
            headers={"X-Forwarded-For": f"10.0.0.{i}"},
        ).status_code
        for i in range(auth_api._MAX_FAILS + 1)
    ]
    assert statuses == [401] * auth_api._MAX_FAILS + [429]


def test_login_flow_with_password_is_unaffected() -> None:
    auth_service.set_password("owner-password")
    origin = "https://panel.example.com"
    client = _client(REMOTE_PEER, base_url=origin)

    assert client.get(PROBE).status_code == 401
    login = client.post("/api/auth/login", json={"password": "owner-password"}, headers={"Origin": origin})
    assert login.status_code == 200
    assert auth_api.COOKIE_NAME in client.cookies

    assert _passed(client.get(PROBE))
    assert _passed(client.post(PROBE, content="x", headers={"Origin": origin}))
    assert client.get("/api/auth/status").json() == {"configured": True, "authenticated": True}


def test_session_cookie_does_not_bypass_origin_check() -> None:
    auth_service.set_password("owner-password")
    client = _client(LOCAL_V4)
    assert client.post("/api/auth/login", json={"password": "owner-password"}).status_code == 200

    res = client.post(PROBE, content="x", headers={"Origin": EVIL_ORIGIN})
    assert res.status_code == 403
