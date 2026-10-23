"""Windows toast text must never be parsed as PowerShell code.

Drives the real winotify package with subprocess.Popen intercepted, so the
PowerShell command is inspected but never executed and no toast is shown.
"""
from __future__ import annotations

import base64
import re
import sys
import xml.etree.ElementTree as ET

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="winotify is Windows-only")

winotify = pytest.importorskip("winotify")

from app.services import notify_adapter  # noqa: E402

_QUOTES = '"“”„'
_DECODE_EXPR = re.compile(
    r"\$\(\[Text\.Encoding\]::UTF8\.GetString\(\[Convert\]::FromBase64String\('([A-Za-z0-9+/=]*)'\)\)\)"
)

_PAYLOADS = [
    "$(Set-Content -Path pwned.txt -Value 1)",
    "${env:USERNAME} $env:TEMP",
    "`$(whoami)`",
    'quote " break $(whoami) "',
    "“smart” $(whoami) „",
    '\n"@\nSet-Content pwned.txt 1\n$x = @"\n',
    "]]><text id=\"3\">x</text><![CDATA[",
]


@pytest.fixture(autouse=True)
def _reset_backend_cache(monkeypatch):
    monkeypatch.setattr(notify_adapter, "_backend_cache", None)


@pytest.fixture()
def ps_commands(monkeypatch):
    commands: list[list[str]] = []

    def _fake_popen(args, *_a, **_kw):
        commands.append(list(args))

    monkeypatch.setattr(winotify.subprocess, "Popen", _fake_popen)
    return commands


def _command_for(ps_commands, title: str, message: str) -> str:
    assert notify_adapter.notify(title, message) is True
    argv = ps_commands.pop()
    return argv[argv.index("-Command") + 1]


def _active_tokens(script: str) -> list[str]:
    """Unescaped `$` and double-quote characters, i.e. what PowerShell would act on."""
    tokens = []
    escaped = False
    for ch in script:
        if escaped:
            escaped = False
        elif ch == "`":
            escaped = True
        elif ch == "$" or ch in _QUOTES:
            tokens.append(ch)
    return tokens


def _expand(script: str) -> str:
    return _DECODE_EXPR.sub(
        lambda m: base64.b64decode(m.group(1)).decode("utf-8"), script
    )


def _toast_texts(script: str) -> tuple[str, str]:
    body = script.split('$Template = @"\n', 1)[1].split('\n"@', 1)[0]
    root = ET.fromstring(_expand(body))
    texts = {t.get("id"): t.text or "" for t in root.iter("text")}
    assert set(texts) == {"1", "2"}
    return texts["1"], texts["2"]


def _toast_tag(script: str) -> str:
    match = re.search(r'^\$Toast\.Tag = "(.*)"$', script, re.MULTILINE)
    assert match is not None
    return _expand(match.group(1))


@pytest.mark.parametrize("payload", _PAYLOADS)
def test_alert_text_adds_no_powershell_tokens(ps_commands, payload):
    baseline = _command_for(ps_commands, "TickFlow", "2330")
    attacked = _command_for(ps_commands, f"TickFlow {payload}", f"2330 {payload}")
    assert _active_tokens(attacked) == _active_tokens(baseline)
    assert "$(Set-Content" not in attacked
    assert "$(whoami)" not in attacked


@pytest.mark.parametrize("payload", _PAYLOADS)
def test_alert_text_is_rendered_verbatim(ps_commands, payload):
    title = f"TickFlow {payload}".strip()
    message = f"2330 {payload}".strip()
    script = _command_for(ps_commands, title, message)
    assert _toast_texts(script) == (title, message)
    assert _toast_tag(script) == title


def test_title_ending_in_backtick_keeps_the_tag_string_closed(ps_commands):
    baseline = _command_for(ps_commands, "TickFlow", "x")
    attacked = _command_for(ps_commands, "TickFlow`", "x`")
    assert _active_tokens(attacked) == _active_tokens(baseline)


def test_normal_chinese_alert_is_rendered_unchanged(ps_commands):
    title = "TickFlow · 策略"
    message = "2330 台積電 漲幅 +5.23% 突破月線 (成交量 12,345 張)"
    script = _command_for(ps_commands, title, message)
    assert _toast_texts(script) == (title, message)
    assert _toast_tag(script) == title
    assert 'duration="short"' in script
    assert 'CreateToastNotifier("TickFlow")' in script
