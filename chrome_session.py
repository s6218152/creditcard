"""Start installed Chrome with an isolated, temporary profile and local CDP.

Chrome owns the browser session; Playwright attaches to its default context.
The user's everyday profile is never opened or copied.
"""

from contextlib import contextmanager
from pathlib import Path
import shutil
import socket
from urllib.request import urlopen
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


@contextmanager
def system_chrome_context(playwright):
    executable = chrome_executable()
    with tempfile.TemporaryDirectory(prefix="ctbc-chrome-") as directory:
        profile = Path(directory)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            debug_port = listener.getsockname()[1]
        process = subprocess.Popen(
            [executable, f"--user-data-dir={profile}", "--remote-debugging-address=127.0.0.1",
             f"--remote-debugging-port={debug_port}", "--no-first-run", "--no-default-browser-check",
             "--window-size=1440,1000", "about:blank"],
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
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
