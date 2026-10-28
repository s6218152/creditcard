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


def test_hncb_preserves_hidden_action_columns_and_ignores_hidden_rows(page):
    from manual_bank_balance import read_hncb_twd_balance

    load_mock_page(page, "https://netbank.hncb.com.tw/netbank/overview", """
        <table><tbody>
        <tr><td>帳號</td><td>類別</td><td>幣別</td><td>帳上餘額</td></tr>
        <tr><td>原幣</td><td>折合新台幣</td></tr>
        <tr><td>111</td><td>活儲</td><td>新台幣</td><td>203.00</td><td>203.00</td>
            <td>-</td><td>-</td><td>餘額</td><td>明細</td><td>轉帳</td><td>查詢</td></tr>
        <tr><td>222</td><td>活儲</td><td>新台幣</td><td>997.00</td><td>997.00</td>
            <td>-</td><td>-</td><td>餘額</td><td>明細</td><td style="display:none">轉帳</td><td>查詢</td></tr>
        <tr style="display:none"><td>111</td><td>活儲</td><td>新台幣</td><td>203.00</td><td>203.00</td>
            <td>-</td><td>-</td><td>餘額</td><td>明細</td><td>轉帳</td><td>查詢</td></tr>
        </tbody></table>
    """)
    value = read_hncb_twd_balance(page)
    assert value == Decimal("1200.00")
    assert value.scope == "twd_current_deposit_total"


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
          document.getElementById('submit').innerText='登入';setTimeout(()=>location.search='?locale=zh-TW',100)">繁中</a>
        <a data-lang-locale="zh-TW" style="display:none">繁體中文</a>
        <input id="userId"><input id="idNumber"><input id="pppd">
        <button id="submit" type="submit">{label}</button>
        <script>if(location.search)document.getElementById('submit').innerText='登入';</script>
    ''')
    prepare_entry(page, "shanghai")
    assert page.locator('#submit').inner_text() == "登入"
    if label == "Log in":
        assert "locale=zh-TW" in page.url


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


def test_chb_scrolls_balance_entry_clear_of_fixed_header(page, monkeypatch):
    from manual_bank_balance import click_chb_navigation
    from playwright.sync_api import Locator
    load_mock_page(page, "https://www.chb.com.tw/netbank/overview", """
        <style>
          body {height:3000px;margin:0}
          header {position:fixed;top:0;left:0;width:100%;height:120px;background:white;z-index:10}
          nav {position:absolute;top:800px}
          a {display:block;width:200px;height:40px}
          #menu {position:absolute;top:250px}
        </style>
        <header class="header fixed">固定頁首</header>
        <nav>
          <a id="menu" data-txnmenuid="TW">臺幣存款</a>
          <a data-url="../TxnPage/tw#!/tw01001_1" onclick="window.entryClicked=true">活期餘額</a>
        </nav>
    """)
    page.evaluate("window.scrollTo(0,750)")
    original_click = Locator.click
    def click_clear_entry(locator, **kwargs):
        if locator.get_attribute('data-url') == '../TxnPage/tw#!/tw01001_1':
            assert locator.evaluate('''e => {
                const r = e.getBoundingClientRect();
                return e.contains(document.elementFromPoint(r.x+r.width/2, r.y+r.height/2));
            }'''), 'Balance entry must clear the fixed header before its bounded click'
        return original_click(locator, **kwargs)
    monkeypatch.setattr(Locator, 'click', click_clear_entry)
    visited = set()
    assert click_chb_navigation(page, visited)
    assert page.evaluate("window.entryClicked") is True
    assert visited == {"chb_twd_menu"}


@pytest.mark.parametrize('known_loading', [True, False])
def test_chb_retries_only_known_layer_appearing_after_readiness_check(page, monkeypatch, known_loading):
    from playwright.sync_api import Locator, Error
    from manual_bank_balance import click_chb_navigation
    load_mock_page(page, 'https://www.chb.com.tw/netbank/overview', '''
        <a data-txnmenuid="TW">臺幣存款</a>
        <a data-url="../TxnPage/tw#!/tw01001_1" onclick="window.entryClicked=true">活期餘額</a>
        <div id="loading-icon-wrap"></div>
        <style>#loading-icon-wrap.show {position:fixed;inset:0;background:white;z-index:20}</style>
    ''')
    original_click = Locator.click
    attempts = []
    def late_layer(locator, **kwargs):
        if locator.get_attribute('data-url') == '../TxnPage/tw#!/tw01001_1':
            attempts.append(True)
            if len(attempts) == 1:
                if known_loading:
                    page.evaluate('''() => {
                        const e=document.getElementById('loading-icon-wrap');e.className='show';
                        setTimeout(()=>e.className='',100);
                    }''')
                raise Error('overlay intercepts pointer events')
        return original_click(locator, **kwargs)
    monkeypatch.setattr(Locator, 'click', late_layer)
    visited = set()
    if known_loading:
        assert click_chb_navigation(page, visited)
        assert len(attempts) == 2
        assert page.evaluate('window.entryClicked') is True
        assert visited == {'chb_twd_menu'}
    else:
        with pytest.raises(Error, match='intercepts pointer events'):
            click_chb_navigation(page, visited)
        assert len(attempts) == 1
        assert visited == set()


def test_chb_narrow_tall_entry_uses_visible_area_below_fixed_header(page):
    from manual_bank_balance import click_chb_navigation
    load_mock_page(page, 'https://www.chb.com.tw/netbank/overview', '''
        <style>
          header {position:fixed;inset:0 0 auto;height:74px;background:white;z-index:10}
          #menu {position:fixed;top:300px}
          #entry {position:fixed;left:116px;top:-16px;width:28px;height:179px}
        </style>
        <header class="header fixed">固定頁首</header>
        <nav><a id="menu" data-txnmenuid="TW">新臺幣帳戶</a>
          <a id="entry" data-url="../TxnPage/tw#!/tw01001_1"
             onclick="window.entryClicked=true">新臺幣活期餘額</a></nav>
    ''')
    visited = set()
    assert click_chb_navigation(page, visited)
    assert page.evaluate('window.entryClicked') is True
    assert visited == {'chb_twd_menu'}


def test_shanghai_waits_for_loader_before_balance_navigation(page):
    from manual_bank_balance import click_balance_navigation
    load_mock_page(page, 'https://ebank.scsb.com.tw/', '''
        <button class="accordion-button" onclick="window.clicked=true">臺幣存匯</button>
        <div class="loader-overlay" style="position:fixed;inset:0;background:white;z-index:10"></div>
    ''')
    visited = set()
    assert not click_balance_navigation(page, 'shanghai', visited)
    assert visited == set()
    page.locator('.loader-overlay').evaluate('e => e.remove()')
    assert click_balance_navigation(page, 'shanghai', visited)
    assert visited == {'臺幣存匯'}
    assert page.evaluate('window.clicked') is True


@pytest.mark.parametrize('known_loading', [True, False])
def test_shanghai_late_loader_never_marks_failed_navigation_as_visited(page, monkeypatch, known_loading):
    from playwright.sync_api import Locator, Error
    from manual_bank_balance import click_balance_navigation
    load_mock_page(page, 'https://ebank.scsb.com.tw/',
                   '<button class="accordion-button" onclick="window.clicked=true">臺幣存匯</button>')
    original = Locator.click
    attempts = []
    def click(locator, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            page.evaluate("name => document.body.insertAdjacentHTML('beforeend','<div class=\"'+name+'\">loading</div>')",
                          'loader-overlay' if known_loading else 'unknown-blocker')
            raise Error('overlay intercepts pointer events')
        return original(locator, **kwargs)
    monkeypatch.setattr(Locator, 'click', click)
    visited = set()
    if known_loading:
        assert not click_balance_navigation(page, 'shanghai', visited)
        assert visited == set()
        page.locator('.loader-overlay').evaluate('e => e.remove()')
        assert click_balance_navigation(page, 'shanghai', visited)
        assert visited == {'臺幣存匯'}
    else:
        with pytest.raises(Error, match='intercepts pointer events'):
            click_balance_navigation(page, 'shanghai', visited)
        assert visited == set()


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
