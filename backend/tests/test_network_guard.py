"""The conftest network guard refuses DNS lookups and outbound connections on every path."""
from __future__ import annotations

import asyncio
import socket
import sys

import httpx
import pytest

DENIED = "network access denied in tests"
# TEST-NET-3 (RFC 5737) is never routed, so a guard failure cannot reach a real host.
UNROUTED_IP = "203.0.113.10"


def test_dns_lookups_are_refused():
    with pytest.raises(socket.gaierror, match=DENIED):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(socket.gaierror, match=DENIED):
        socket.gethostbyname("example.com")
    with pytest.raises(socket.gaierror, match=DENIED):
        socket.gethostbyname_ex("example.com")


def test_connections_to_an_ip_literal_are_refused():
    with pytest.raises(OSError, match=DENIED):
        socket.create_connection((UNROUTED_IP, 80), timeout=1)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock, pytest.raises(OSError, match=DENIED):
        sock.connect_ex((UNROUTED_IP, 80))


def test_http_clients_are_refused():
    with pytest.raises(httpx.ConnectError, match=DENIED):
        httpx.get("https://example.com", timeout=1)


@pytest.mark.parametrize(
    "loop_factory",
    [
        pytest.param(
            getattr(asyncio, "ProactorEventLoop", None),
            id="proactor",
            marks=pytest.mark.skipif(sys.platform != "win32", reason="Proactor is Windows-only"),
        ),
        pytest.param(asyncio.SelectorEventLoop, id="selector"),
    ],
)
@pytest.mark.parametrize("host", [UNROUTED_IP, "example.com"])
def test_asyncio_connections_are_refused(loop_factory, host):
    loop = loop_factory()
    try:
        with pytest.raises(OSError, match=DENIED):
            loop.run_until_complete(asyncio.wait_for(asyncio.open_connection(host, 80), timeout=2))
    finally:
        loop.close()


def test_loopback_stays_available():
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        assert socket.getaddrinfo("localhost", port)
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
