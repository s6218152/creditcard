"""Exercise actual browser image loading with no bank access or credentials."""
import base64
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import sync_playwright

from bank_captcha import CAPTCHA_IMAGES, _image_bytes
from chrome_session import chrome_executable


def test_pending_captcha_image_is_loaded_before_capture():
    try:
        executable = chrome_executable()
    except ValueError:
        pytest.skip("需要已安裝 Chrome 才能驗證圖片載入")
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1cAAAAASUVORK5CYII=")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(0.3)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(png)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=executable, headless=True)
            try:
                page = browser.new_page()
                page.set_content(f'<img class="cimg" src="http://127.0.0.1:{server.server_port}/image">',
                                 wait_until="domcontentloaded")
                assert not page.locator("img").evaluate("e=>e.complete")
                sources, content = _image_bytes(page.locator("img"), CAPTCHA_IMAGES["chb"])
                assert content.startswith(b"\x89PNG")
                assert len(sources) == 1
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
