"""OS notification adapter tests — backend detection, winotify call shape, truncation.

The Windows path is driven through a fake `winotify` module injected into
sys.modules, so these run on any platform. The fake mirrors winotify 1.1.0's
real signatures (`Notification(app_id, title, msg, ...)`, `Notifier(registry)`),
which is what makes a call against a constructor the library does not offer
fail here instead of silently in production.
"""
from __future__ import annotations

import sys
import types

import pytest

from app.services import notify_adapter


@pytest.fixture(autouse=True)
def _reset_backend_cache(monkeypatch):
    monkeypatch.setattr(notify_adapter, "_backend_cache", None)
    yield
    notify_adapter._backend_cache = None


@pytest.fixture()
def winotify_calls(monkeypatch):
    calls: list[dict] = []

    class Registry:
        def __init__(self, app_id, executable="", script_path="", *, force_override=False):
            raise AssertionError("notify_adapter must not write to the Windows registry")

    class Notifier:
        def __init__(self, registry: Registry):
            self.registry = registry

    class Notification:
        def __init__(
            self,
            app_id: str,
            title: str,
            msg: str = "",
            icon: str = "",
            duration: str = "short",
            launch: str = "",
        ):
            if duration not in ("short", "long"):
                raise ValueError(f"invalid duration: {duration}")
            self.payload = {
                "app_id": app_id,
                "title": title,
                "msg": msg,
                "icon": icon,
                "duration": duration,
                "launch": launch,
                "shown": False,
            }
            calls.append(self.payload)

        def show(self) -> None:
            self.payload["shown"] = True

    module = types.ModuleType("winotify")
    module.Registry = Registry
    module.Notifier = Notifier
    module.Notification = Notification

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winotify", module)
    return calls


class TestWindowsPath:
    def test_notification_is_built_and_shown(self, winotify_calls):
        assert notify_adapter.notify("TickFlow · 策略", "2330 台積電 突破月線") is True
        assert len(winotify_calls) == 1
        call = winotify_calls[0]
        assert call["title"] == "TickFlow · 策略"
        assert call["msg"] == "2330 台積電 突破月線"
        assert call["duration"] == "short"
        assert call["shown"] is True

    def test_app_id_matches_the_product_name_used_in_titles(self, winotify_calls):
        notify_adapter.notify("標題", "內容")
        assert winotify_calls[0]["app_id"] == "TickFlow"

    def test_missing_winotify_returns_false(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setitem(sys.modules, "winotify", None)
        assert notify_adapter.notify("標題", "內容") is False

    def test_backend_failure_is_swallowed(self, winotify_calls, monkeypatch):
        def _boom(title: str, message: str) -> bool:
            raise RuntimeError("toast pipeline down")

        monkeypatch.setattr(notify_adapter, "_notify_winotify", _boom)
        assert notify_adapter.notify("標題", "內容") is False


class TestBackendDetection:
    def test_unknown_platform_has_no_backend(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "sunos5")
        assert notify_adapter._detect_backend() is None
        assert notify_adapter.notify("標題", "內容") is False

    def test_linux_requires_notify_send_on_path(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(notify_adapter.shutil, "which", lambda _name: None)
        assert notify_adapter._detect_backend() is None
        assert notify_adapter.notify("標題", "內容") is False

    def test_linux_detects_installed_notify_send(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(notify_adapter.shutil, "which", lambda name: f"/usr/bin/{name}")
        assert notify_adapter._detect_backend() == "notify-send"


class TestTruncation:
    def test_long_title_and_message_are_cut_with_ellipsis(self, winotify_calls):
        limit = notify_adapter._MAX_LEN
        assert notify_adapter.notify("標" * (limit + 50), "訊" * (limit + 1)) is True
        call = winotify_calls[0]
        assert call["title"] == "標" * limit + "…"
        assert call["msg"] == "訊" * limit + "…"

    def test_text_at_the_limit_is_kept_intact(self, winotify_calls):
        exact = "標" * notify_adapter._MAX_LEN
        assert notify_adapter.notify(exact, "") is True
        assert winotify_calls[0]["title"] == exact
        assert winotify_calls[0]["msg"] == ""

    def test_blank_title_is_dropped(self, winotify_calls):
        assert notify_adapter.notify("   ", "內容") is False
        assert winotify_calls == []
