"""Known bank notices use their own defer controls, without accepting settings."""
import pytest
from playwright.sync_api import sync_playwright, Error as PlaywrightError

import manual_bank_balance as bank
from bank_login import dismiss_login_notice
import bank_login
from chrome_session import chrome_executable


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome_executable(), headless=True)
        try:
            yield browser.new_page()
        finally:
            browser.close()


NOTICES = {
    'first_bank': ('https://ibank.firstbank.com.tw/NetBank/index.html',
        '<div class="modality-modal mm-show" id="confirmMsg"><p>因您尚未辦理資料更新</p>'
        '<a class="btn-cancel" href="#confirmMsg">下次再說</a></div>', '下次再說'),
    'chb': ('https://www.chb.com.tw/chbnib/index',
        '<div class="modal fade orange-style-modal in"><button aria-label="關閉">×</button><h2>密碼到期通知</h2>'
        '<p>您的密碼已經超過三個月未變更！</p><div class="modal-footer">'
        '<button>變更密碼</button><button>關閉</button><button>三個月後再提醒</button></div></div>', '關閉'),
    'taishin': ('https://my.taishinbank.com.tw/TIBNetBank/',
        '<div class="_popup"><p>即可使用相同的使用者代號/使用者密碼來登入Richart App</p>'
        '<a>台新Richart會員條款</a><div class="_popup_footer"><button>立即開通</button>'
        '<button>之後提醒我</button></div></div>', '之後提醒我'),
    'cathay': ('https://www.cathaybk.com.tw/MyBank/Home',
        '<div role="alertdialog" class="chakra-modal__content-container"><p>您尚未啟用「登入兩步驟」</p>'
        '<footer class="chakra-modal__footer"><button>立即啟用</button>'
        '<button>暫時不用</button></footer></div>', '暫時不用'),
}


def load(page, url, body):
    page.route('**/*', lambda route: route.fulfill(content_type='text/html; charset=utf-8', body=
        '<meta charset="utf-8">' + body + '''<script>
        window.clicked=[];
        document.addEventListener('click', e => {
            if (!e.target.matches('button,a')) return;
            e.preventDefault(); window.clicked.push(e.target.textContent);
            e.target.closest('.modality-modal,.modal,._popup,[role=dialog],.chakra-modal__content-container').remove();
        });</script>'''))
    page.goto(url)


@pytest.mark.parametrize('key', list(NOTICES))
@pytest.mark.parametrize('known', [True, False])
def test_only_exact_known_notice_is_deferred(page, key, known):
    url, html, action = NOTICES[key]
    if not known:
        for original in ('因您尚未辦理資料更新', '密碼到期通知', '台新Richart會員條款', '您尚未啟用「登入兩步驟」'):
            html = html.replace(original, '其他尚未確認內容')
    load(page, url, html)
    assert bank.dismiss_balance_notice(page, key) is known
    assert page.evaluate('window.clicked') == ([action] if known else [])


def test_foreign_page_does_not_dismiss_bank_notice(page):
    _, html, _ = NOTICES['cathay']
    load(page, 'https://untrusted.example/', html)
    assert not bank.dismiss_balance_notice(page, 'cathay')
    assert page.evaluate('window.clicked') == []


def test_late_first_bank_notice_is_dismissed_before_next_navigation_attempt(page, monkeypatch):
    url, html, _ = NOTICES['first_bank']
    load(page, url, '<div id="late"></div>')
    attempts = []
    def navigate(page, visited):
        attempts.append(True)
        if len(attempts) == 1:
            page.locator('#late').evaluate('(e, html) => e.innerHTML = html', html)
            raise PlaywrightError('a div intercepts pointer events')
        visited.add('complete')
        return True
    monkeypatch.setitem(bank.BANK_ADAPTERS, 'first_bank', bank.BankAdapter(navigate=navigate, allow_generic_navigation=False))
    visited = set()
    assert not bank.click_balance_navigation(page, 'first_bank', visited)
    assert bank.click_balance_navigation(page, 'first_bank', visited)
    assert visited == {'complete'}
    assert page.evaluate('window.clicked') == ['下次再說']


def test_unknown_overlay_keeps_navigation_error_visible(page, monkeypatch):
    load(page, NOTICES['first_bank'][0], '')
    def navigate(*args):
        raise PlaywrightError('unknown blocker intercepts pointer events')
    monkeypatch.setitem(bank.BANK_ADAPTERS, 'first_bank', bank.BankAdapter(navigate=navigate))
    with pytest.raises(PlaywrightError, match='unknown blocker'):
        bank.click_balance_navigation(page, 'first_bank', set())


@pytest.mark.parametrize('known', [True, False])
@pytest.mark.parametrize('acknowledge', ['我知道了', 'I got it'])
def test_shanghai_acknowledges_only_scoped_maintenance_announcement(page, known, acknowledge):
    url = 'https://ebank.scsb.com.tw/'
    body = '<div class="modal custom-modal" role="dialog"><h2>網路銀行 10/14、10/15 維護公告</h2>' \
           '<p>本行為提供更優質的服務，將進行系統維護作業。</p><button>我知道了</button></div>'
    if not known:
        body = body.replace('維護公告', '尚未確認的條款')
    body = body.replace('我知道了', acknowledge)
    load(page, url, body)
    dismiss_login_notice(page, page.main_frame, 'shanghai', lambda value: bank.is_bank_url(value, 'shanghai'))
    assert page.evaluate('window.clicked') == ([acknowledge] if known else [])


@pytest.mark.parametrize('key', list(NOTICES))
def test_duplicate_defer_controls_are_not_clicked(page, key):
    url, html, action = NOTICES[key]
    tag = 'a' if key == 'first_bank' else 'button'
    html = html.replace(f'>{action}</{tag}>', f'>{action}</{tag}><{tag}>{action}</{tag}>')
    load(page, url, html)
    assert not bank.dismiss_balance_notice(page, key)
    assert page.evaluate('window.clicked') == []


@pytest.mark.parametrize('key, method', [('first_bank', '_navigate_first_bank'), ('cathay', '_navigate_cathay')])
def test_failed_navigation_is_not_recorded_as_completed(key, method):
    from types import SimpleNamespace
    def blocked(**kwargs):
        raise PlaywrightError('known notice intercepts pointer events')
    button = SimpleNamespace(count=lambda: 1, click=blocked)
    url = NOTICES[key][0] if key != 'first_bank' else 'https://ibank.firstbank.com.tw/NetBank/1/acntReviewAll.html'
    frame = SimpleNamespace(url=url, locator=lambda selector: button)
    page = SimpleNamespace(url=url, frames=[frame], locator=frame.locator)
    visited = set()
    with pytest.raises(PlaywrightError):
        getattr(bank, method)(page, visited)
    assert visited == set()


def test_shanghai_form_rebuild_between_preflight_and_login_is_waited_for(page, monkeypatch):
    url = 'https://ebank.scsb.com.tw/'
    html = '<form id="loginForm" onsubmit="event.preventDefault(); window.submits++">' \
           '<input id="userId"><input id="idNumber"><input id="pppd" type="password">' \
           '<input id="verified" maxlength="5"><button type="submit">登入</button></form>'
    page.route('**/*', lambda route: route.fulfill(content_type='text/html; charset=utf-8',
                                                 body='<meta charset="utf-8">' + html))
    page.goto(url)
    page.evaluate('window.submits=0')
    official = lambda url: bank.is_bank_url(url, 'shanghai')
    bank_login.locate_form(page, 'shanghai', official)
    page.locator('#loginForm').evaluate('''e => {
        const html = e.outerHTML; e.remove();
        setTimeout(() => document.body.insertAdjacentHTML('beforeend', html), 500);
    }''')
    monkeypatch.setattr(bank_login, 'bank_credentials', lambda key: ('test-id', 'test-user', 'test-password'))
    monkeypatch.setattr(bank_login, 'dismiss_login_notice', lambda *args, **kwargs: None)
    monkeypatch.setattr(bank_login, 'try_fill_captcha', lambda *args: True)
    assert bank_login.login_once(page, 'shanghai', official)
    assert page.evaluate('window.submits') == 1
    assert page.locator('#userId').input_value() == 'test-id'
    assert page.locator('#idNumber').input_value() == 'test-user'


def test_shanghai_submit_failure_is_never_retried(monkeypatch):
    from types import SimpleNamespace
    calls, submissions = [], []
    def submit(**kwargs):
        submissions.append(True)
        raise PlaywrightError('browser closed after submit')
    field = SimpleNamespace(fill=lambda *args, **kwargs: None)
    frame = SimpleNamespace(url='https://ebank.scsb.com.tw/', locator=lambda selector: SimpleNamespace(count=lambda: 1))
    def locate(*args):
        calls.append(True)
        return frame, [field] * 3, SimpleNamespace(click=submit)
    monkeypatch.setattr(bank_login, 'bank_credentials', lambda key: ('test-id', 'test-user', 'test-password'))
    monkeypatch.setattr(bank_login, 'locate_form', locate)
    monkeypatch.setattr(bank_login, 'dismiss_login_notice', lambda *args, **kwargs: None)
    monkeypatch.setattr(bank_login, 'try_fill_captcha', lambda *args: True)
    with pytest.raises(PlaywrightError, match='after submit'):
        bank_login.login_once(SimpleNamespace(url=frame.url), 'shanghai', lambda url: True)
    assert len(calls) == len(submissions) == 1


def test_login_form_wait_keeps_timeout_as_an_unsubmitted_form_error(page):
    url = 'https://ebank.scsb.com.tw/'
    load(page, url, '')
    with pytest.raises(bank_login.BankQueryError) as caught:
        bank_login.wait_for_login_form(page, 'shanghai', lambda value: bank.is_bank_url(value, 'shanghai'), timeout=0)
    assert caught.value.code == 'login_form_unavailable'
    assert page.evaluate('window.clicked') == []


def test_login_form_wait_stops_immediately_on_foreign_redirect():
    from types import SimpleNamespace
    waits = []
    page = SimpleNamespace(url='https://ebank.scsb.com.tw/', frames=[])
    def redirect(milliseconds):
        waits.append(milliseconds)
        page.url = 'https://untrusted.example/'
    page.wait_for_timeout = redirect
    with pytest.raises(bank_login.BankQueryError) as caught:
        bank_login.wait_for_login_form(page, 'shanghai', lambda value: bank.is_bank_url(value, 'shanghai'))
    assert caught.value.code == 'untrusted_url'
    assert waits == [250]


@pytest.mark.parametrize('stage', ['before_language', 'after_menu'])
def test_shanghai_english_announcement_and_delayed_language_reload(page, stage):
    from manual_bank_balance import prepare_entry
    notice = '<div class="modal custom-modal" role="dialog" style="position:fixed;inset:0;background:white;z-index:10">' \
             '<p>網路銀行維護公告：系統維護作業</p>' \
             '<button onclick="this.parentElement.remove()">I got it</button></div>'
    import json
    markup = '<button role="button" onclick="document.querySelector(\'#choice\').style.display=\'block\';' \
             'if(window.lateNotice) {document.body.insertAdjacentHTML(\'beforeend\',window.lateNotice);window.lateNotice=null;}">EN</button>' \
             '<a id="choice" data-lang-locale="zh-TW" style="display:none" onclick="' \
             'document.querySelector(\'#submit\').textContent=\'登入\';setTimeout(()=>location.search=\'?lang=zh-TW\',200)">繁中</a>' \
             '<input id="userId"><input id="idNumber"><input id="pppd">' \
             '<button id="submit" type="submit">Log in</button>'
    script = '<script>window.lateNotice=' + (json.dumps(notice) if stage == 'after_menu' else 'null') + ';' \
             'if(location.search){document.querySelector("#submit").textContent="登入";window.lateNotice=null;}' \
             '</script>'
    if stage == 'before_language':
        script += '<script>if(!location.search)document.body.insertAdjacentHTML("beforeend",' + json.dumps(notice) + ');</script>'
    page.route('**/*', lambda route: route.fulfill(content_type='text/html; charset=utf-8', body=markup + script))
    page.goto('https://ebank.scsb.com.tw/')
    prepare_entry(page, 'shanghai')
    assert 'lang=zh-TW' in page.url
    assert page.locator('#submit').inner_text() == '登入'
    assert page.locator('#userId').input_value() == ''
    assert page.locator('#pppd').input_value() == ''


@pytest.mark.parametrize('complete_form', [True, False])
def test_shanghai_language_switch_without_reload_requires_complete_form(page, monkeypatch, complete_form):
    from manual_bank_balance import prepare_entry
    markup = '<button onclick="document.querySelector(\'#choice\').style.display=\'block\'">EN</button>' \
             '<a id="choice" data-lang-locale="zh-TW" style="display:none" onclick="' \
             'document.querySelector(\'#submit\').textContent=\'登入\'">繁中</a>' \
             '<input id="userId"><input id="idNumber"><input id="pppd">' \
             '<button id="submit" type="submit">Log in</button>'
    if not complete_form:
        markup = markup.replace('<input id="pppd">', '')
    page.route('**/*', lambda route: route.fulfill(content_type='text/html; charset=utf-8', body=markup))
    page.goto('https://ebank.scsb.com.tw/')
    original = page.expect_navigation
    monkeypatch.setattr(page, 'expect_navigation', lambda **kwargs: original(**{**kwargs, 'timeout': 300}))
    if not complete_form:
        with pytest.raises(bank_login.BankQueryError) as caught:
            prepare_entry(page, 'shanghai')
        assert caught.value.code == 'login_form_unavailable'
    else:
        prepare_entry(page, 'shanghai')
        assert page.locator('#submit').inner_text() == '登入'
        assert page.locator('#pppd').input_value() == ''


def test_cathay_notice_is_scoped_by_visible_container_with_dialog_role(page):
    url, html, _ = NOTICES['cathay']
    load(page, url, html.replace('alertdialog', 'dialog'))
    assert bank.dismiss_balance_notice(page, 'cathay')
    assert page.evaluate('window.clicked') == ['暫時不用']


@pytest.mark.parametrize('variant', ['known', 'unknown_title', 'unknown_body', 'duplicate', 'foreign'])
def test_cathay_securities_maintenance_uses_only_remind_later(page, variant):
    html = '<div class="chakra-modal__content-container"><h2>系統維護公告</h2>' \
           '<p>2026/10/17 配合國泰證券系統維護作業，維護期間將暫停證券相關查詢服務。</p>' \
           '<footer class="chakra-modal__footer"><button>不要再顯示</button>' \
           '<button>下次再提醒</button></footer></div>'
    if variant == 'unknown_title':
        html = html.replace('系統維護公告', '其他公告')
    elif variant == 'unknown_body':
        html = html.replace('配合國泰證券系統維護作業', '請啟用新的驗證設定')
    elif variant == 'duplicate':
        html = html.replace('</footer>', '<button>下次再提醒</button></footer>')
    url = NOTICES['cathay'][0] if variant != 'foreign' else 'https://untrusted.example/'
    load(page, url, html)
    assert bank.dismiss_balance_notice(page, 'cathay') is (variant == 'known')
    assert page.evaluate('window.clicked') == (['下次再提醒'] if variant == 'known' else [])


def test_disappearing_login_notice_does_not_abort_logged_in_page(page, monkeypatch):
    from playwright.sync_api import Locator
    load(page, NOTICES['cathay'][0], '<a href="/logout">登出</a><div role="alert">資料載入中</div>')
    original = Locator.is_visible
    def disappears_after_visibility_check(locator, **kwargs):
        visible = original(locator, **kwargs)
        if visible and locator.get_attribute('role') == 'alert':
            # Model a SPA removing its notice between separate browser calls.
            locator.evaluate('e => e.remove()')
        return visible
    monkeypatch.setattr(Locator, 'is_visible', disappears_after_visibility_check)
    bank_login.check_login_error(page, lambda url: bank.is_bank_url(url, 'cathay'))


@pytest.mark.parametrize('message, code', [
    ('驗證碼錯誤', 'captcha_rejected'),
    ('密碼不正確', 'credentials_rejected'),
    ('帳號已鎖定', 'account_locked'),
])
def test_visible_login_errors_still_stop_query_amid_other_notices(page, message, code):
    load(page, NOTICES['cathay'][0],
         '<div role="alert" style="display:none">登入失敗</div>'
         '<div role="alert">系統維護公告</div><div role="alert">' + message + '</div>')
    with pytest.raises(bank_login.BankQueryError) as caught:
        bank_login.check_login_error(page, lambda url: bank.is_bank_url(url, 'cathay'))
    assert caught.value.code == code
