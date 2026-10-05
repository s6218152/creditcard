"""Exercise actual browser image loading with no bank access or credentials."""
import base64
import threading
import time
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import sync_playwright

from bank_captcha import CAPTCHA_IMAGES, _image_bytes
from chrome_session import chrome_executable
from bank_login import BankQueryError, check_login_error
from manual_bank_balance import is_bank_url


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


def test_first_bank_reads_loaded_natural_pixels_without_a_second_image_request():
    from PIL import Image
    buffer = BytesIO()
    original = Image.new("RGB", (67, 29), "white")
    original.putpixel((32, 14), (0, 0, 0))
    original.save(buffer, format="PNG")
    requests = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome_executable(), headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: (requests.append(route.request.url),
                       route.fulfill(content_type="image/png", body=buffer.getvalue()))
                       if route.request.url.endswith("/captcha.png") else route.fulfill(
                           content_type="text/html", body='<img id="code_verify" src="/captcha.png" height="25">'))
            page.goto("https://ibank.firstbank.com.tw/NetBank/test.html")
            images = page.locator("#code_verify")
            assert images.bounding_box()["height"] == 25
            _, content = _image_bytes(images, CAPTCHA_IMAGES["first_bank"])
            decoded = Image.open(BytesIO(content)).convert("RGB")
            assert decoded.size == (67, 29)
            assert decoded.tobytes() == original.tobytes()
            assert len(requests) == 1
        finally:
            browser.close()


@pytest.mark.parametrize("visible", [True, False])
def test_first_bank_actual_popup_reports_captcha_rejection_only_when_visible(visible):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome_executable(), headless=True)
        try:
            page = browser.new_page()
            style = "" if visible else 'style="display:none"'
            page.route("**/*", lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=
                       f'<div class="mm-wrap"><div id="login-msg" class="pop-hint" {style}>'
                       '<div class="pop-out-title">提醒</div>'
                       '<div class="pop-content">圖形驗證碼錯誤！</div></div></div>'))
            page.goto("https://ibank.firstbank.com.tw/NetBank/test.html")
            official = lambda url: is_bank_url(url, "first_bank")
            if visible:
                with pytest.raises(BankQueryError) as caught:
                    check_login_error(page, official)
                assert caught.value.code == "captcha_rejected"
            else:
                check_login_error(page, official)
        finally:
            browser.close()
