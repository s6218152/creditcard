from contextlib import nullcontext
from types import SimpleNamespace
from pathlib import Path
import subprocess

import pytest

import chrome_session


def test_wait_for_debug_port_fails_when_chrome_exits():
    with pytest.raises(ValueError, match="Chrome 啟動失敗"):
        chrome_session.wait_for_debug_port(12345, SimpleNamespace(poll=lambda: 1))


def test_wait_for_debug_port_times_out():
    with pytest.raises(ValueError, match="逾時"):
        chrome_session.wait_for_debug_port(12345, SimpleNamespace(poll=lambda: None), timeout=0)


def test_context_cleans_profile_and_process_on_error(monkeypatch):
    calls = []
    commands = []
    process = SimpleNamespace(
        poll=lambda: None,
        terminate=lambda: calls.append("terminate"),
        wait=lambda **kwargs: calls.append("wait"),
    )
    def launch(command, **kwargs):
        commands.append(command)
        return process
    monkeypatch.setattr(chrome_session, "chrome_executable", lambda: "/installed/chrome")
    monkeypatch.setattr(chrome_session.subprocess, "Popen", launch)
    monkeypatch.setattr(chrome_session, "wait_for_debug_port", lambda port, process: port)
    context = object()
    browser = SimpleNamespace(
        contexts=[context],
        new_browser_cdp_session=lambda: SimpleNamespace(send=lambda message: calls.append(message)),
        close=lambda: calls.append("disconnect"),
    )
    endpoints = []
    engine = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=lambda url, **kwargs: endpoints.append(url) or browser))
    with pytest.raises(RuntimeError, match="cancelled"):
        with chrome_session.system_chrome_context(engine) as actual:
            assert actual is context
            raise RuntimeError("cancelled")
    profile_argument = next(arg for arg in commands[0] if arg.startswith("--user-data-dir="))
    assert not Path(profile_argument.split("=", 1)[1]).exists()
    assert endpoints[0].startswith("http://127.0.0.1:")
    assert "--remote-debugging-address=127.0.0.1" in commands[0]
    assert "--window-size=1440,1000" in commands[0]
    assert not any("no-sandbox" in arg or "disable" in arg for arg in commands[0])
    assert calls == ["Browser.close", "disconnect", "terminate", "wait"]
