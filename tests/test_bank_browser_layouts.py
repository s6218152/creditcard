"""Offline regressions across desktop and narrow production-like viewports."""
import pytest
from playwright.sync_api import sync_playwright

from chrome_session import chrome_executable
from bank_login import dismiss_login_notice
from manual_bank_balance import click_chb_navigation, is_bank_url


@pytest.fixture(params=[(1440, 1000), (1280, 883), (480, 883)])
def page(request):
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome_executable(), headless=True)
        try:
            width, height = request.param
            yield browser.new_page(viewport={'width': width, 'height': height})
        finally:
            browser.close()


def load(page, url, html):
    page.route('**/*', lambda route: route.fulfill(body='<meta charset="utf-8">' + html,
                                                  content_type='text/html; charset=utf-8'))
    page.goto(url)


def test_chb_hit_tested_navigation_below_fixed_header(page):
    load(page, 'https://www.chb.com.tw/netbank/overview', '''
        <style>header{position:fixed;inset:0 0 auto;height:74px;z-index:10;background:white}
        #menu{position:fixed;top:300px}
        #entry{position:fixed;left:116px;top:-16px;width:28px;height:179px}
        @media(min-width:1300px){#entry{top:140px;width:184px;height:29px}}</style>
        <header class="header fixed">固定頁首</header><nav>
        <a id="menu" data-txnmenuid="TW">新臺幣帳戶</a>
        <a id="entry" data-url="../TxnPage/tw#!/tw01001_1"
            onclick="window.clicked=true">新臺幣活期餘額</a></nav>''')
    assert click_chb_navigation(page, set())
    assert page.evaluate('window.clicked') is True


def test_shanghai_drill_notice_is_acknowledged_without_persistent_preference(page):
    load(page, 'https://ebank.scsb.com.tw/', '''
        <input type="checkbox" id="neverShow">
        <div class="modal custom-modal" role="dialog" style="position:fixed;inset:0">
        財金公司跨行系統演練：配合財金公司辦理跨行系統演練，部分時段將暫停跨行服務，自行服務不受影響
        <button onclick="this.parentElement.remove()">I got it</button></div>''')
    assert dismiss_login_notice(page, page.main_frame, 'shanghai', lambda url: is_bank_url(url, 'shanghai'))
    assert not page.locator('#neverShow').is_checked()
