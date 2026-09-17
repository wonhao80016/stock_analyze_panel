"""Shared test setup.

The API suite drives endpoints through TestClient without a session cookie.
The auth middleware lets unauthenticated local requests through only while no
panel password is set, so the moment a developer sets one in the UI (it lands in
data/user_data/auth.json) every API test starts returning 401. Whether the
machine running the tests happens to have a password configured must not decide
whether the suite passes, so neutralise both conditions here.

Tests that exercise the auth logic itself opt out with @pytest.mark.real_auth.
"""
from __future__ import annotations

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_auth: keep the real auth state instead of the unauthenticated-local stub"
    )


@pytest.fixture(autouse=True)
def _api_tests_are_unauthenticated_local(request, monkeypatch):
    if request.node.get_closest_marker("real_auth"):
        return

    from app.api import auth as auth_api
    from app.services import auth as auth_service

    monkeypatch.setattr(auth_service, "is_configured", lambda: False)
    monkeypatch.setattr(auth_api, "_is_local_network", lambda _ip: True)
