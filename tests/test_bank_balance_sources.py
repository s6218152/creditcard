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
