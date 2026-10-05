"""Verify deposit readers against DOMs containing different card bill amounts."""
from decimal import Decimal

import pytest
from playwright.sync_api import sync_playwright

from chrome_session import chrome_executable
from manual_bank_balance import read_dbs_twd_balance, read_feib_twd_balance


@pytest.fixture
def page():
    try:
        executable = chrome_executable()
    except ValueError:
        pytest.skip("需要 Chrome 才能驗證實際 DOM 選取")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable, headless=True)
        try:
            yield browser.new_page()
        finally:
            browser.close()


def load_mock_page(page, url, html):
    # All requests are fulfilled locally; no bank connection or login.
    page.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html; charset=utf-8"))
    page.goto(url)


def test_dbs_deposit_reader_does_not_use_credit_card_due_amount(page):
    load_mock_page(page, "https://internet-banking.dbs.com.tw/digitw/overview", """
        <div>信用卡 本期應繳金額 1,051</div>
        <div data-testid="dashboard.casa.header.title">活期存款</div>
        <div data-testid="dashboard.casa.header.label">帳戶餘額</div>
        <div data-testid="dashboard.casa.header.currency">TWD</div>
        <div data-testid="dashboard.casa.header.amount">987.65</div>
    """)
    assert read_dbs_twd_balance(page) == Decimal("987.65")


def test_feib_deposit_reader_excludes_card_and_foreign_currency_amounts(page):
    load_mock_page(page, "https://ebank.feib.com.tw/netbank/SvDeposit.jsp", """
        <div>信用卡 本期應繳金額 5,609</div>
        <table><tr><th>所屬分行</th><th>帳戶類別</th><th>帳號</th><th>幣別</th><th>帳戶餘額</th><th>我要查詢/申請</th></tr>
        <tr><td>測試</td><td>活期存款</td><td>test-account</td><td>TWD</td><td>123.45</td><td></td></tr>
        <tr><td>測試</td><td>外幣存款</td><td>test-foreign</td><td>USD</td><td>5609</td><td></td></tr></table>
        <table><tr><th>往來單位</th><th>帳戶類別</th><th>帳號</th><th>幣別</th><th>帳戶餘額</th><th></th></tr></table>
    """)
    assert read_feib_twd_balance(page) == Decimal("123.45")


def test_dbs_credit_card_only_page_is_not_a_deposit_balance(page):
    load_mock_page(page, "https://internet-banking.dbs.com.tw/digitw/overview",
                   "<div>信用卡 帳戶餘額 TWD 1,051</div>")
    with pytest.raises(ValueError):
        read_dbs_twd_balance(page)


@pytest.mark.parametrize("label", ["登入", "Log in"])
def test_shanghai_login_form_accepts_official_chinese_and_english_buttons(page, label):
    from bank_login import locate_form
    from manual_bank_balance import is_bank_url
    load_mock_page(page, "https://ebank.scsb.com.tw/", f'''
        <input id="userId"><input id="idNumber"><input id="pppd" type="password">
        <input id="verified" maxlength="5">
        <button type="submit">{label}</button><button type="button">Apply immediately</button>
    ''')
    _, fields, submit = locate_form(page, "shanghai", lambda u: is_bank_url(u, "shanghai"))
    assert len(fields) == 3 and submit.count() == 1
    assert submit.inner_text() == label


@pytest.mark.parametrize("label", ["登入", "Log in"])
def test_shanghai_uses_official_traditional_chinese_choice_before_login(page, label):
    from manual_bank_balance import prepare_entry
    load_mock_page(page, "https://ebank.scsb.com.tw/", f'''
        <button type="button" onclick="document.getElementById('choice').style.display='block'">EN</button>
        <a id="choice" data-lang-locale="zh-TW" style="display:none" onclick="
          document.getElementById('submit').innerText='登入';this.style.display='none'">繁中</a>
        <a data-lang-locale="zh-TW" style="display:none">繁體中文</a>
        <input id="userId"><input id="idNumber"><input id="pppd">
        <button id="submit" type="submit">{label}</button>
    ''')
    prepare_entry(page, "shanghai")
    assert page.locator('#submit').inner_text() == "登入"


@pytest.mark.parametrize("timer,expected", [(True, True), (False, False)])
def test_esun_new_dashboard_recognizes_session_without_visible_logout(page, timer, expected):
    from bank_login import has_logged_in
    from manual_bank_balance import is_bank_url, read_esun_twd_balance
    countdown = '<div>184 秒後自動登出，點擊重新計時</div>' if timer else ''
    load_mock_page(page, "https://ebank.esunbank.com.tw/dashboard", countdown + """
        <div>信用卡應繳金額 2,100</div><div>等值新臺幣 1,005</div>
        <section class="widget-balance-detail-container">
          <h2>臺幣存款總覽</h2><div>活存支存餘額</div><div>984</div>
          <div>定存本金餘額</div><div>16</div>
        </section>
    """)
    assert has_logged_in(page, lambda u: is_bank_url(u, "esun"), bank="esun") is expected
    if expected:
        assert read_esun_twd_balance(page) == Decimal("1000")


def test_chb_waits_for_loading_layer_started_by_menu(page):
    from manual_bank_balance import click_chb_navigation
    load_mock_page(page, "https://www.chb.com.tw/netbank/overview", """
        <style>
          body {height:3000px;margin:0}
          header {position:fixed;top:0;left:0;width:100%;height:120px;background:white;z-index:10}
          nav {position:absolute;top:800px}
          a {display:block;width:200px;height:40px}
          #menu {position:absolute;top:250px}
          #loading-icon-wrap.show {position:fixed;inset:0;background:white;z-index:20}
        </style>
        <header class="header fixed">固定頁首</header>
        <nav>
          <a id="menu" data-txnmenuid="TW" onclick="
            document.getElementById('loading-icon-wrap').className='show';
            setTimeout(()=>document.getElementById('loading-icon-wrap').className='',5500)
          ">臺幣存款</a>
          <a data-url="../TxnPage/tw#!/tw01001_1" onclick="window.entryClicked=true">活期餘額</a>
        </nav>
        <div id="loading-icon-wrap"></div>
    """)
    page.evaluate("window.scrollTo(0,750)")
    visited = set()
    assert click_chb_navigation(page, visited)
    assert page.evaluate("window.entryClicked") is True
    assert "chb_twd_menu" in visited


def test_ctbc_unrelated_visible_input_does_not_block_login(page, monkeypatch):
    from ctbc_balance import LOGIN_URL, login_from_environment
    for key in ("CTBC_ID", "CTBC_USER_ID", "CTBC_PASSWORD"):
        monkeypatch.setenv(key, "test-credential")
    load_mock_page(page, LOGIN_URL, """
        <input placeholder="搜尋">
        <input formcontrolname="custIxd">
        <input formcontrolname="userIxd">
        <input formcontrolname="pxd" type="password">
        <button type="button" onclick="window.loginClicks=(window.loginClicks||0)+1">登入</button>
    """)
    wait = page.wait_for_function
    def bounded_wait(*args, **kwargs):
        kwargs["timeout"] = 100
        return wait(*args, **kwargs)
    monkeypatch.setattr(page, "wait_for_function", bounded_wait)
    assert login_from_environment(page)
    assert page.evaluate("window.loginClicks") == 1


def test_ctbc_validation_diagnostics_never_include_credentials(page):
    import json
    from bank_diagnostics import collect_page_diagnostics
    from ctbc_balance import LOGIN_URL, is_ctbc_url
    load_mock_page(page, LOGIN_URL, """
        <input formcontrolname="custIxd" class="ng-invalid" value="private-identity">
        <input formcontrolname="userIxd" value="private-user">
        <input formcontrolname="pxd" type="password" value="private-password">
        <button disabled>登入</button>
    """)
    diagnostic = collect_page_diagnostics(page, "ctbc", is_ctbc_url)
    frame = diagnostic["frames"][0]
    assert frame["credential_validation"][0]["invalid"] is True
    assert frame["login_buttons"][0]["disabled"] is True
    assert frame["login_buttons"][0]["visible"] is True
    assert "private-" not in json.dumps(diagnostic)
