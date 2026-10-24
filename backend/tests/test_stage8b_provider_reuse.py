"""The quote poll and /api/taiwan/data reuse one Taiwan provider and its HTTP client."""
from __future__ import annotations

import httpx
import pytest

from app.data_providers import registry
from app.services.quote_service import QuoteService
from app.taiwan import etf_data, fundamentals
from app.taiwan.providers import hybrid_provider
from app.taiwan.providers.hybrid_provider import TaiwanHybridProvider
from app.taiwan.providers.official_provider import OfficialTaiwanAdapter

pytestmark = pytest.mark.usefixtures("deny_network")

_REAL_CLIENT = httpx.Client


class _Clients:
    """httpx.Client factory counting the clients built; every request is answered with an empty list."""

    def __init__(self) -> None:
        self.built: list[httpx.Client] = []

    def __call__(self, *args, **kwargs) -> httpx.Client:
        client = _REAL_CLIENT(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=[])))
        self.built.append(client)
        return client


class _NoQuotes:
    def get_quotes(self, symbols):
        return {}


@pytest.fixture
def clients(monkeypatch):
    factory = _Clients()
    monkeypatch.setattr(httpx, "Client", factory)
    monkeypatch.setattr(registry, "_instances", {}, raising=False)
    monkeypatch.setattr(fundamentals, "_shared_client", None, raising=False)
    monkeypatch.setattr(etf_data, "_shared_client", None, raising=False)
    monkeypatch.setattr(hybrid_provider, "_official_fundamentals_provider", None, raising=False)
    return factory


@pytest.fixture
def quiet_market(monkeypatch):
    """No live quote and no official fallback, as in the trading-hours poll."""
    from app.taiwan.realtime import service as realtime_service

    monkeypatch.setattr(realtime_service, "get_realtime_service", lambda: _NoQuotes())
    monkeypatch.setattr(TaiwanHybridProvider, "_official_fallback_allowed", staticmethod(lambda: False))


def test_get_provider_returns_one_instance_per_name(clients):
    first = registry.get_provider("taiwan")

    assert registry.get_provider("taiwan") is first
    assert registry.get_provider("TAIWAN") is first
    assert isinstance(first, TaiwanHybridProvider)


def test_quote_poll_rounds_build_one_provider_client(clients, quiet_market):
    for _ in range(5):
        assert QuoteService._taiwan_realtime_records(["2330.TWSE"]) == []

    assert len(clients.built) == 1


def test_current_data_requests_build_no_client_after_the_first(clients, monkeypatch):
    from app.api.taiwan import get_taiwan_current_data

    get_taiwan_current_data("2330.TWSE")
    after_first = len(clients.built)
    for _ in range(4):
        response = get_taiwan_current_data("2330.TWSE")

    assert len(clients.built) == after_first
    assert set(response.sections) == {"monthly_revenue", "financial_statement", "valuation", "share_capital_record"}


def test_current_data_uses_the_registry_provider(clients):
    from app.taiwan.current_data import get_taiwan_current_data_service

    assert get_taiwan_current_data_service().provider is registry.get_provider("taiwan")


def test_close_providers_closes_the_shared_client(clients):
    provider = registry.get_provider("taiwan")
    client = provider.official.client

    registry.close_providers()

    assert client.is_closed
    assert registry.get_provider("taiwan") is not provider


def test_an_injected_client_is_left_open(clients):
    injected = _REAL_CLIENT(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=[])))
    adapter = OfficialTaiwanAdapter(client=injected)

    adapter.close()

    assert not injected.is_closed
    injected.close()


def test_a_patched_provider_class_is_built_again(clients, monkeypatch):
    """Tests and plugins that swap the class behind a name get an instance of the new class."""
    original = registry.get_provider("taiwan")

    class _Other(TaiwanHybridProvider):
        pass

    monkeypatch.setattr(hybrid_provider, "TaiwanHybridProvider", _Other)

    swapped = registry.get_provider("taiwan")
    assert isinstance(swapped, _Other) and swapped is not original
    assert registry.get_provider("taiwan") is swapped


def test_concurrent_first_calls_build_one_instance(clients, monkeypatch):
    import threading
    import time

    built: list[object] = []

    class _Slow:
        def __init__(self) -> None:
            time.sleep(0.05)
            built.append(self)

    monkeypatch.setattr(hybrid_provider, "TaiwanHybridProvider", _Slow)
    start = threading.Barrier(8)
    got: list[object] = []

    def call() -> None:
        start.wait()
        got.append(registry.get_provider("taiwan"))

    threads = [threading.Thread(target=call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(built) == 1
    assert len(got) == 8 and all(p is built[0] for p in got)


def test_a_failing_close_does_not_stop_the_others(clients, caplog):
    closed: list[str] = []

    class _Broken:
        def close(self) -> None:
            raise RuntimeError("boom")

    class _Fine:
        def close(self) -> None:
            closed.append("fine")

    registry._instances.update({_Broken: _Broken(), _Fine: _Fine(), object: object()})

    registry.close_providers()

    assert closed == ["fine"]
    assert registry._instances == {}
    assert "boom" in caplog.text


def test_shared_providers_are_closed_at_interpreter_exit():
    import subprocess
    import sys
    from pathlib import Path

    script = (
        "from app.data_providers import registry\n"
        "class P:\n"
        "    def close(self):\n"
        "        print('closed', flush=True)\n"
        "registry._instances[P] = P()\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=60, check=True,
    )

    assert out.stdout.strip() == "closed"
