"""系统通知适配器 — 三平台原生通知中心。

职责: 把后端产生的告警事件推送到操作系统通知中心。
     窗口最小化 / 被遮挡 / 后台运行时都能弹通知 (不依赖前端 WebView)。

平台实现:
  - Windows: winotify, raised through PowerShell
  - macOS:   osascript (系统已内置, 无需额外依赖)
  - Linux:   notify-send; disabled when it is not on PATH (no other fallback)

设计: 失败静默降级, 绝不因通知失败阻断告警主流程 (落盘 / SSE 推送)。
     通知去重不在本层做, 复用 MonitorRuleEngine 的 cooldown 逻辑。
"""
from __future__ import annotations

import base64
import logging
import queue
import shutil
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

# 单次通知最长字符 (避免某些平台截断报错)
_MAX_LEN = 200

# Toast app_id, kept identical to the product name quote_service puts in notification
# titles. winotify's Registry is not called: it only installs a URL-protocol handler so
# a toast click can launch back into the app, and no launch action is set here. Whether
# Windows shows a toast for an app id it does not know is untested.
_WINDOWS_APP_ID = "TickFlow"

# 避免重复探测平台能力, 缓存一次
_backend_cache: str | None = None

# osascript/notify-send may each block up to 5s; a burst beyond this many is dropped.
_QUEUE_MAX = 50
_queue: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=_QUEUE_MAX)
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


def _detect_backend() -> str | None:
    """探测当前平台可用的通知后端。"""
    global _backend_cache
    if _backend_cache is not None:
        return _backend_cache if _backend_cache != "none" else None

    backend = None
    if sys.platform == "win32":
        try:
            from winotify import Notification  # type: ignore[import-not-found]  # noqa: F401

            backend = "winotify"
        except ImportError:
            logger.debug("winotify unavailable, Windows notifications disabled")
            backend = None
    elif sys.platform == "darwin":
        backend = "osascript"
    elif sys.platform.startswith("linux"):
        if shutil.which("notify-send"):
            backend = "notify-send"
        else:
            logger.debug("notify-send not found on PATH, Linux notifications disabled")

    _backend_cache = backend or "none"
    return backend


def _truncate(text: str) -> str:
    """截断超长文本, 避免平台通知上限报错。"""
    text = (text or "").strip()
    return text[:_MAX_LEN] + ("…" if len(text) > _MAX_LEN else "")


def notify(title: str, message: str) -> bool:
    """推送一条系统通知。

    Args:
        title:   通知标题
        message: 通知正文

    Returns:
        True = handed to the platform backend, False = failed or no backend available.
        Windows spawns the toast asynchronously, so True does not mean it was displayed;
        macOS and Linux wait on the process and report its exit status.
        失败静默, 不抛异常 (通知是辅助通道, 不能阻断告警主流程)。
    """
    title = _truncate(title)
    message = _truncate(message)
    if not title:
        return False

    backend = _detect_backend()
    if backend is None:
        return False

    try:
        if backend == "winotify":
            return _notify_winotify(title, message)
        if backend == "osascript":
            return _notify_osascript(title, message)
        if backend == "notify-send":
            return _notify_notify_send(title, message)
    except Exception as e:  # noqa: BLE001
        logger.debug("系统通知失败 (%s): %s", backend, e)
        return False

    return False


def notify_async(title: str, message: str) -> bool:
    """Queue a notification for the background sender without waiting on the platform backend.

    Returns False when the title is empty or the queue is full (the notification is dropped).
    """
    if not _truncate(title):
        return False
    _ensure_worker()
    try:
        _queue.put_nowait((title, message))
    except queue.Full:
        logger.warning("system notification queue full, dropped: %s", _truncate(title))
        return False
    return True


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(
                target=_drain, args=(_queue,), name="system-notify", daemon=True,
            )
            _worker.start()


def _drain(pending: queue.Queue[tuple[str, str]]) -> None:
    while True:
        title, message = pending.get()
        try:
            notify(title, message)
        except Exception as e:
            logger.debug("system notification failed: %s", e)
        finally:
            pending.task_done()


def _powershell_text(text: str) -> str:
    """PowerShell subexpression that evaluates to `text` without parsing any of it."""
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"$([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')))"


def _cdata_text(text: str) -> str:
    return text.replace("]]>", "]]]]><![CDATA[>")


def _notify_winotify(title: str, message: str) -> bool:
    """Windows toast through winotify's Notification API."""
    from winotify import Notification  # type: ignore[import-not-found]

    # duration only accepts "short" or "long"; launch stays unset (no clickable action).
    notification = Notification(
        app_id=_WINDOWS_APP_ID,
        title=title,
        msg=message,
        duration="short",
    )
    # winotify 1.1.0 str.formats title/msg into CDATA inside an expandable PowerShell
    # here-string and tag into a double-quoted string, all run via -Command unescaped.
    # Only base64 decode expressions may reach that script, or alert text becomes code.
    notification.title = _powershell_text(_cdata_text(title))
    notification.msg = _powershell_text(_cdata_text(message))
    notification.tag = _powershell_text(title)
    notification.show()
    return True


def _notify_osascript(title: str, message: str) -> bool:
    """macOS 通知 (osascript) — 调用系统 AppleScript。"""
    # 转义双引号, 避免 AppleScript 注入
    safe_title = title.replace('"', '\\"')
    safe_msg = message.replace('"', '\\"')
    script = (
        f'display notification "{safe_msg}" with title "{safe_title}"'
    )
    result = subprocess.run(  # noqa: S603, S607
        ["osascript", "-e", script],
        capture_output=True,
        timeout=5,
    )
    return result.returncode == 0


def _notify_notify_send(title: str, message: str) -> bool:
    """Linux 通知 (notify-send) — freedesktop.org 标准。"""
    args = ["notify-send", title, message]
    result = subprocess.run(  # noqa: S603
        args,
        capture_output=True,
        timeout=5,
    )
    return result.returncode == 0
