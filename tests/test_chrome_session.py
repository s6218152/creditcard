from types import SimpleNamespace
from pathlib import Path
import io
import json
import errno

import pytest

import chrome_session


def test_profile_cleanup_retries_only_transient_directory_races(monkeypatch):
    calls = []
    sleeps = []
    def cleanup():
        calls.append(True)
        if len(calls) < 3:
            raise OSError(errno.ENOTEMPTY, 'Directory not empty', 'Default')
    monkeypatch.setattr(chrome_session.time, 'sleep', sleeps.append)
    chrome_session.cleanup_chrome_profile(SimpleNamespace(cleanup=cleanup))
    assert len(calls) == 3 and len(sleeps) == 2


def test_profile_cleanup_does_not_hide_permanent_permission_errors(monkeypatch):
    def cleanup():
        raise OSError(errno.EACCES, 'Permission denied')
    monkeypatch.setattr(chrome_session.time, 'sleep', lambda *args: pytest.fail('must not retry permission errors'))
    with pytest.raises(OSError) as caught:
        chrome_session.cleanup_chrome_profile(SimpleNamespace(cleanup=cleanup))
    assert caught.value.errno == errno.EACCES


def test_public_host_rule_uses_current_system_resolution(monkeypatch):
    monkeypatch.setattr(chrome_session.socket, 'getaddrinfo', lambda *args, **kwargs:
                        [(None, None, None, None, ('93.184.216.34', 443))])
    monkeypatch.setattr(chrome_session, 'urlopen', lambda *args, **kwargs: pytest.fail('DNS already resolved'))
    assert chrome_session.public_host_resolver_rule('ebank.feib.com.tw') == 'MAP ebank.feib.com.tw 93.184.216.34'


@pytest.mark.parametrize('answer,expected', [
    ({'Status': 0, 'Answer': [{'name': 'ebank.feib.com.tw.', 'type': 1, 'data': '93.184.216.34'}]}, True),
    ({'Status': 0, 'Answer': [{'name': 'ebank.feib.com.tw.', 'type': 1, 'data': '127.0.0.1'}]}, False),
    ({'Status': 0, 'Answer': [{'name': 'untrusted.example.', 'type': 1, 'data': '93.184.216.34'}]}, False),
    ({'Status': 3}, False),
    ([], False),
])
def test_public_host_rule_falls_back_to_https_dns_with_validated_answer(monkeypatch, answer, expected):
    def dns_error(*args, **kwargs):
        raise OSError('DNS unavailable')
    monkeypatch.setattr(chrome_session.socket, 'getaddrinfo', dns_error)
    urls = []
    def resolve(url, **kwargs):
        urls.append(url)
        return io.BytesIO(json.dumps(answer).encode())
    monkeypatch.setattr(chrome_session, 'urlopen', resolve)
    if expected:
        assert chrome_session.public_host_resolver_rule('ebank.feib.com.tw') == 'MAP ebank.feib.com.tw 93.184.216.34'
    else:
        with pytest.raises(ValueError, match='無法解析'):
            chrome_session.public_host_resolver_rule('ebank.feib.com.tw')
    assert urls == ['https://dns.google/resolve?name=ebank.feib.com.tw&type=A']


def test_wait_for_debug_port_fails_when_chrome_exits():
    with pytest.raises(ValueError, match="Chrome 啟動失敗"):
        chrome_session.wait_for_debug_port(12345, SimpleNamespace(poll=lambda: 1))


def test_wait_for_debug_port_times_out():
    with pytest.raises(ValueError, match="逾時"):
        chrome_session.wait_for_debug_port(12345, SimpleNamespace(poll=lambda: None), timeout=0)


@pytest.mark.parametrize('rules', [None, 'MAP ebank.feib.com.tw 93.184.216.34'])
@pytest.mark.parametrize('cleanup_race', [True, False])
@pytest.mark.parametrize('body_error', [True, False])
@pytest.mark.parametrize('unresponsive', [True, False])
def test_context_cleans_profile_without_overwriting_result_or_original_error(monkeypatch, rules, cleanup_race, body_error, unresponsive):
    calls = []
    commands = []
    def wait(**kwargs):
        calls.append('wait')
        if unresponsive and calls.count('wait') < 3:
            raise chrome_session.subprocess.TimeoutExpired('chrome', kwargs['timeout'])
    process = SimpleNamespace(
        poll=lambda: None,
        terminate=lambda: calls.append("terminate"),
        kill=lambda: calls.append('kill'),
        wait=wait,
    )
    def launch(command, **kwargs):
        commands.append(command)
        return process
    monkeypatch.setattr(chrome_session, "chrome_executable", lambda: "/installed/chrome")
    monkeypatch.setattr(chrome_session.subprocess, "Popen", launch)
    monkeypatch.setattr(chrome_session, "wait_for_debug_port", lambda port, process: port)
    if cleanup_race:
        original_temporary = chrome_session.tempfile.TemporaryDirectory
        def temporary(*args, **kwargs):
            directory = original_temporary(*args, **kwargs)
            original_cleanup = directory.cleanup
            attempts = []
            def cleanup():
                attempts.append(True)
                if len(attempts) < 3:
                    raise OSError(errno.ENOTEMPTY, 'Directory not empty', 'Default')
                original_cleanup()
            directory.cleanup = cleanup
            return directory
        monkeypatch.setattr(chrome_session.tempfile, 'TemporaryDirectory', temporary)
        monkeypatch.setattr(chrome_session.time, 'sleep', lambda *args: None)
    context = object()
    browser = SimpleNamespace(
        contexts=[context],
        new_browser_cdp_session=lambda: SimpleNamespace(send=lambda message: calls.append(message)),
        close=lambda: calls.append("disconnect"),
    )
    endpoints = []
    engine = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=lambda url, **kwargs: endpoints.append(url) or browser))
    def run():
        with chrome_session.system_chrome_context(engine, host_resolver_rules=rules) as actual:
            assert actual is context
            if body_error:
                raise RuntimeError("cancelled")
            return actual
    if body_error:
        with pytest.raises(RuntimeError, match="cancelled"):
            run()
    else:
        assert run() is context
    profile_argument = next(arg for arg in commands[0] if arg.startswith("--user-data-dir="))
    assert not Path(profile_argument.split("=", 1)[1]).exists()
    assert endpoints[0].startswith("http://127.0.0.1:")
    assert "--remote-debugging-address=127.0.0.1" in commands[0]
    assert "--window-size=1440,1000" in commands[0]
    assert [arg for arg in commands[0] if arg.startswith('--host-resolver-rules=')] == (
        ['--host-resolver-rules=' + rules] if rules else [])
    assert not any("no-sandbox" in arg or "disable" in arg for arg in commands[0])
    assert calls == (["Browser.close", "disconnect", "wait", "terminate", "wait", "kill", "wait"] if unresponsive
                     else ["Browser.close", "disconnect", "wait"])
