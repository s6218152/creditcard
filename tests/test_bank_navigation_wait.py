"""Login redirects must not be confused with a user closing the bank window."""
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bank_login import BankQueryError
import manual_bank_balance as bank


def run_query(monkeypatch, *, after_login, after_wait=None, close=False):
    calls = []
    clock = [0.0]
    startup = SimpleNamespace(url="about:blank", is_closed=lambda: False)
    page = SimpleNamespace(url="about:blank", closed=False, on=lambda *args: None)
    page.is_closed = lambda: page.closed
    page.goto = lambda url, **kw: setattr(page, "url", url)
    def wait(milliseconds):
        clock[0] += milliseconds / 1000
        if after_wait:
            page.url = after_wait
    page.wait_for_timeout = wait
    context = SimpleNamespace(pages=[startup], on=lambda *args: None)
    def new_page():
        context.pages.append(page)
        return page
    context.new_page = new_page
    @contextmanager
    def playwright():
        yield None
    @contextmanager
    def chrome(*args):
        yield context
    def login(*args):
        calls.append("login")
        page.url = after_login
        page.closed = close
        return True
    monkeypatch.setattr(bank, "sync_playwright", playwright)
    monkeypatch.setattr(bank, "diagnostic_chrome_context", chrome)
    monkeypatch.setattr(bank.time, "monotonic", lambda: clock[0])
    for name in ("prepare_entry", "locate_form", "check_login_error", "check_customer_information_update"):
        monkeypatch.setattr(bank, name, lambda *args, **kw: None)
    monkeypatch.setattr(bank, "login_once", login)
    monkeypatch.setattr(bank, "confirm_existing_login", lambda *args: False)
    monkeypatch.setattr(bank, "has_logged_in", lambda *args, **kw: True)
    monkeypatch.setattr(bank, "dismiss_balance_notice", lambda *args: False)
    def read(page, *args):
        assert bank.is_bank_url(page.url, "cathay")
        calls.append("read")
        return Decimal("123.45")
    monkeypatch.setattr(bank, "read_automatic_balance", read)
    try:
        return bank.query_balance("cathay", verification_timeout=20)
    finally:
        assert calls.count("login") == 1


def test_blank_login_redirect_resumes_without_resubmitting_credentials(monkeypatch):
    assert run_query(monkeypatch, after_login="about:blank",
                     after_wait="https://www.cathaybk.com.tw/MyBank/Overview") == Decimal("123.45")


def test_external_redirect_is_only_observed_until_official_page_returns(monkeypatch):
    assert run_query(monkeypatch, after_login="https://redirect.example/?secret=private",
                     after_wait="https://www.cathaybk.com.tw/MyBank/Overview") == Decimal("123.45")


@pytest.mark.parametrize(("url", "close", "code"), [
    ("about:blank", True, "browser_closed"),
    ("about:blank", False, "bank_navigation_unconfirmed"),
    ("https://redirect.example/?secret=private", False, "bank_navigation_unconfirmed"),
    ("chrome-error://chromewebdata/", False, "bank_navigation_failed"),
])
def test_closed_and_unconfirmed_navigation_have_distinct_errors(monkeypatch, url, close, code):
    with pytest.raises(BankQueryError) as caught:
        run_query(monkeypatch, after_login=url, close=close)
    assert caught.value.code == code
    assert "private" not in str(caught.value)
