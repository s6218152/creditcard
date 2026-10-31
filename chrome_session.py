"""Start installed Chrome with an isolated, temporary profile and local CDP.

Chrome owns the browser session; Playwright attaches to its default context.
The user's everyday profile is never opened or copied.
"""

from contextlib import contextmanager
from pathlib import Path
import shutil
import socket
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from ipaddress import IPv4Address
import json
import errno
import subprocess
import sys
import tempfile
import time


def chrome_executable() -> str:
    if sys.platform == "darwin":
        candidates = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    elif sys.platform == "win32":
        import os
        candidates = [Path(os.environ.get(key, "")) / "Google/Chrome/Application/chrome.exe"
                      for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    else:
        candidates = [Path(path) for name in ("google-chrome", "google-chrome-stable", "chromium")
                      if (path := shutil.which(name))]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise ValueError("找不到本機 Google Chrome，請先安裝 Chrome")


def wait_for_debug_port(port: int, process, timeout: float = 20) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValueError("Chrome 啟動失敗，未建立本機連線")
        try:
            with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.5) as response:
                if response.status == 200:
                    return port
        except OSError:
            pass
        time.sleep(0.1)
    raise ValueError("等待 Chrome 本機連線逾時")


def public_host_resolver_rule(host):
    """Resolve a public host for Chrome while keeping its HTTPS name and TLS checks."""
    def public_address(values):
        for value in values:
            try:
                address = IPv4Address(value)
            except ValueError:
                continue
            if address.is_global:
                return str(address)
        return None
    attempts = []
    try:
        address = public_address(item[4][0] for item in socket.getaddrinfo(host, 443, family=socket.AF_INET))
        attempts.append({"resolver": "system", "result": "resolved" if address else "no_public_ipv4"})
    except OSError as error:
        address = None
        attempts.append({"resolver": "system", "result": type(error).__name__})
    if not address:
        # Only the public hostname is sent, never bank credentials or page URLs.
        # An independent HTTPS resolver can recover a transient outage of the first.
        query = urlencode({"name": host, "type": "A"})
        requests = (
            ("google", "https://dns.google/resolve?" + query),
            ("cloudflare", Request("https://cloudflare-dns.com/dns-query?" + query,
                                   headers={"Accept": "application/dns-json"})),
        )
        for resolver, request in requests:
            try:
                with urlopen(request, timeout=10) as response:
                    answer = json.load(response)
                status = answer.get("Status") if isinstance(answer, dict) else None
                records = answer.get("Answer") if isinstance(answer, dict) else None
                if status == 0 and isinstance(records, list):
                    address = public_address(item.get("data", "") for item in records
                        if isinstance(item, dict) and item.get("type") == 1
                        and isinstance(item.get("name"), str)
                        and item["name"].rstrip(".").lower() == host.lower())
                attempts.append({"resolver": resolver,
                                 "result": "resolved" if address else "no_valid_answer",
                                 "dns_status": status if isinstance(status, int) else None})
                if address:
                    break
            except (OSError, ValueError) as error:
                # Keep only the exception type: network error strings can include
                # proxy credentials or machine-specific details.
                attempts.append({"resolver": resolver, "result": type(error).__name__})
    if not address:
        error = ValueError("無法解析銀行官方登入網址")
        error.resolution_attempts = attempts
        raise error
    return f"MAP {host} {address}"


def cleanup_chrome_profile(directory):
    """Chrome helpers can finish writes briefly after the main process exits."""
    for attempt in range(10):
        try:
            directory.cleanup()
            return
        except OSError as error:
            if error.errno not in (errno.ENOTEMPTY, errno.EBUSY) or attempt == 9:
                raise
            time.sleep(0.2)


@contextmanager
def temporary_chrome_profile():
    directory = tempfile.TemporaryDirectory(prefix="ctbc-chrome-")
    try:
        yield directory.name
    finally:
        cleanup_chrome_profile(directory)


@contextmanager
def system_chrome_context(playwright, *, host_resolver_rules=None):
    executable = chrome_executable()
    with temporary_chrome_profile() as directory:
        profile = Path(directory)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            debug_port = listener.getsockname()[1]
        process = subprocess.Popen(
            [executable, f"--user-data-dir={profile}", "--remote-debugging-address=127.0.0.1",
             f"--remote-debugging-port={debug_port}", "--no-first-run", "--no-default-browser-check",
             "--window-size=1440,1000",
             *([f"--host-resolver-rules={host_resolver_rules}"] if host_resolver_rules else []), "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        browser = None
        try:
            port = wait_for_debug_port(debug_port, process)
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=20_000)
            if not browser.contexts:
                raise ValueError("Chrome 未建立預設瀏覽器 session")
            yield browser.contexts[0]
        finally:
            if browser is not None:
                try:
                    browser.new_browser_cdp_session().send("Browser.close")
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass
            if browser is None and process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
