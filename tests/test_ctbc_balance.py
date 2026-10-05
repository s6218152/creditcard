from decimal import Decimal

import pytest

from ctbc_balance import is_ctbc_url, parse_balance_text
from types import SimpleNamespace
from contextlib import nullcontext


def test_busy_bank_page_fails_before_login_prompt(monkeypatch):
    import ctbc_balance
    page = SimpleNamespace(
        url=ctbc_balance.LOGIN_URL,
        goto=lambda *args, **kwargs: None,
        locator=lambda *args: SimpleNamespace(inner_text=lambda **kwargs: "系統忙碌中(APP-1053)"),
    )
    from contextlib import contextmanager
    closed = []
    @contextmanager
    def context(engine):
        try:
            yield SimpleNamespace(new_page=lambda: page)
        finally:
            closed.append(True)
    monkeypatch.setattr(ctbc_balance, "sync_playwright", lambda: nullcontext(None))
    monkeypatch.setattr(ctbc_balance, "system_chrome_context", context)

    def unexpected_input(*args):
        raise AssertionError("銀行忙碌時不應等待登入")

    monkeypatch.setattr("builtins.input", unexpected_input)
    with pytest.raises(ValueError, match="APP-1053"):
        ctbc_balance.query_balance()
    assert closed == [True]


def test_parse_single_account_balance():
    assert parse_balance_text("可動用餘額\n100,000\n帳戶餘額\n125,680\n") == Decimal("125680")
    assert parse_balance_text("帳戶餘額：NT$ 125680.50") == Decimal("125680.50")


def test_reject_missing_or_multiple_balances():
    with pytest.raises(ValueError):
        parse_balance_text("可動用餘額\n125680")
    with pytest.raises(ValueError):
        parse_balance_text("帳戶餘額\n100\n帳戶餘額\n200")


def test_only_ctbc_https_pages_are_read():
    assert is_ctbc_url("https://www.ctbcbank.com/twrbc/twrbc-home/qu000/010")
    assert not is_ctbc_url("http://www.ctbcbank.com/")
    assert not is_ctbc_url("https://ctbcbank.com.evil.example/")


def login_page(*, text="一般登入 登入", url=None, recognized=True):
    from ctbc_balance import LOGIN_URL
    filled, clicked = [], []
    page = SimpleNamespace(
        url=url or LOGIN_URL,
        wait_for_function=lambda *args, **kwargs: None,
        locator=lambda selector: SimpleNamespace(
            inner_text=lambda **kwargs: text,
            count=lambda: 1 if recognized else 0,
            fill=lambda value, **kwargs: filled.append((selector, value)),
        ),
        get_by_role=lambda role, **kwargs: SimpleNamespace(click=lambda **options: clicked.append((role, kwargs))),
    )
    return page, filled, clicked


def test_environment_login_submits_once(monkeypatch):
    from ctbc_balance import login_from_environment
    for key in ("CTBC_ID", "CTBC_USER_ID", "CTBC_PASSWORD"):
        monkeypatch.setenv(key, "test-value")
    page, filled, clicked = login_page()
    assert login_from_environment(page)
    assert len(filled) == 3
    assert clicked == [("button", {"name": "登入", "exact": True})]


def test_login_entry_error_does_not_fill_or_submit(monkeypatch):
    from ctbc_balance import login_from_environment
    for key in ("CTBC_ID", "CTBC_USER_ID", "CTBC_PASSWORD"):
        monkeypatch.setenv(key, "test-value")
    page, filled, clicked = login_page(text="APP-1053")
    with pytest.raises(ValueError, match="APP-1053"):
        login_from_environment(page)
    assert not filled and not clicked


def test_blocking_notice_is_not_misreported_as_form_timeout(monkeypatch):
    from ctbc_balance import login_from_environment
    from bank_login import BankQueryError
    from playwright.sync_api import TimeoutError
    for key in ("CTBC_ID", "CTBC_USER_ID", "CTBC_PASSWORD"):
        monkeypatch.setenv(key, "test-value")
    page, filled, clicked = login_page()
    def blocked(**kwargs):
        raise TimeoutError('<h5 class="modal-title">private-notice</h5> intercepts pointer events')
    page.get_by_role = lambda *args, **kwargs: SimpleNamespace(click=blocked)
    with pytest.raises(BankQueryError) as caught:
        login_from_environment(page)
    assert caught.value.code == "login_notice_blocked"
    assert "提示視窗" in str(caught.value)
    assert "private-notice" not in str(caught.value)
    assert len(filled) == 3 and not clicked


@pytest.mark.parametrize("url", [
    "https://ctbcbank.com.evil.example/twrbc/twrbc-general/ot001/010",
    "https://www.ctbcbank.com/content/dam/ctbc-ib/zh_rb/general/out_of_service.html",
])
def test_redirect_does_not_receive_credentials(monkeypatch, url):
    from ctbc_balance import login_from_environment
    for key in ("CTBC_ID", "CTBC_USER_ID", "CTBC_PASSWORD"):
        monkeypatch.setenv(key, "test-value")
    page, filled, clicked = login_page(url=url)
    with pytest.raises(ValueError):
        login_from_environment(page)
    assert not filled and not clicked


def test_form_timeout_reports_bank_error(monkeypatch):
    from ctbc_balance import login_from_environment
    from playwright.sync_api import TimeoutError
    for key in ("CTBC_ID", "CTBC_USER_ID", "CTBC_PASSWORD"):
        monkeypatch.setenv(key, "test-value")
    page, filled, clicked = login_page(text="APP-1053")
    def timeout(*args, **kwargs):
        raise TimeoutError("waiting for form")
    page.wait_for_function = timeout
    with pytest.raises(ValueError, match="APP-1053"):
        login_from_environment(page)
    assert not filled and not clicked


def test_unrecognized_form_is_not_filled(monkeypatch):
    from ctbc_balance import login_from_environment
    for key in ("CTBC_ID", "CTBC_USER_ID", "CTBC_PASSWORD"):
        monkeypatch.setenv(key, "test-value")
    page, filled, clicked = login_page(recognized=False)
    with pytest.raises(ValueError, match="欄位不符"):
        login_from_environment(page)
    assert not filled and not clicked


def test_automatic_query_does_not_wait_for_terminal_input(monkeypatch):
    import ctbc_balance
    calls = []
    page = SimpleNamespace(
        url=ctbc_balance.LOGIN_URL,
        goto=lambda *args, **kwargs: calls.append("open"),
        wait_for_url=lambda *args, **kwargs: calls.append("authenticated"),
    )
    context = SimpleNamespace(new_page=lambda: page)
    monkeypatch.setattr(ctbc_balance, "sync_playwright", lambda: nullcontext(None))
    monkeypatch.setattr(ctbc_balance, "system_chrome_context", lambda engine: nullcontext(context))
    monkeypatch.setattr(ctbc_balance, "read_bank_page", lambda page: "登入")
    monkeypatch.setattr(ctbc_balance, "login_from_environment", lambda page: True)
    monkeypatch.setattr(ctbc_balance, "read_twd_deposit_balance", lambda page, selector: Decimal("22542"))
    def unexpected_input(*args):
        raise AssertionError("不得等待終端機 Enter")
    monkeypatch.setattr("builtins.input", unexpected_input)
    assert ctbc_balance.query_balance() == Decimal("22542")
    assert calls == ["open", "authenticated"]


def test_twd_total_excludes_foreign_currency_and_duplicate_subtotal():
    text = "臺幣帳戶餘額 22,542\n活存帳戶餘額\t22,542\n外幣存款\n帳戶餘額\t97,661"
    assert parse_balance_text(text, labels=("臺幣帳戶餘額",)) == Decimal("22542")
    with pytest.raises(ValueError):
        parse_balance_text("外幣帳戶餘額\n97,661", labels=("臺幣帳戶餘額",))
