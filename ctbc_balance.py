"""Read a CTBC deposit balance from an authenticated browser page.

The browser profile is temporary. Login credentials are read from local
environment variables; cookies are not persisted.
"""

import argparse
import os
import re
from pathlib import Path
from dotenv import load_dotenv
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from chrome_session import system_chrome_context
from bank_diagnostics import diagnostic_chrome_context
from bank_login import BankQueryError


# Use the current standalone Internet Banking login, not the legacy header form.
LOGIN_URL = "https://www.ctbcbank.com/twrbc/twrbc-general/ot001/010"
_AMOUNT = re.compile(r"^(?:NT\$|NTD\$|TWD|新臺幣|新台幣)?\s*\$?\s*([+-]?\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|[+-]?\d+(?:\.\d{1,2})?)\s*(?:元)?$", re.IGNORECASE)


def is_ctbc_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (
        parsed.hostname == "ctbcbank.com" or
        bool(parsed.hostname and parsed.hostname.endswith(".ctbcbank.com"))
    )


def parse_balance_text(text: str, labels: tuple[str, ...] = ("帳戶餘額",)) -> Decimal:
    """Accept one unambiguous amount adjacent to the exact account-balance label."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    balances = []
    for index, line in enumerate(lines):
        label = re.fullmatch(
            rf"(?:{'|'.join(re.escape(label) for label in labels)})\s*[：:]?\s*(.*)",
            line,
        )
        if label is None:
            continue
        candidates = [label.group(1)]
        if not label.group(1) and index + 1 < len(lines):
            candidates.append(lines[index + 1])
        for candidate in candidates:
            amount = _AMOUNT.fullmatch(candidate)
            if amount is not None:
                try:
                    balances.append(Decimal(amount.group(1).replace(",", "")))
                except InvalidOperation:
                    pass
                break
    if len(balances) != 1:
        raise ValueError(
            f"找到 {len(balances)} 個可辨識的存款餘額；請在網銀開啟單一存款帳戶明細，"
            "或用 --balance-selector 指定顯示金額的元素。"
        )
    return balances[0]


def read_bank_page(page) -> str:
    if not is_ctbc_url(page.url):
        raise ValueError("目前頁面不是中國信託官方 HTTPS 網址，已停止讀取")
    text = page.locator("body").inner_text(timeout=5_000)
    if "APP-1053" in text:
        raise ValueError("中信網銀回覆 APP-1053（系統忙碌），未取得餘額；請稍後再試")
    return text


def login_from_environment(page) -> bool:
    credentials = [os.getenv(key, "") for key in ("CTBC_ID", "CTBC_USER_ID", "CTBC_PASSWORD")]
    if not any(credentials):
        return False
    if not all(credentials):
        raise ValueError("請完整設定 CTBC_ID、CTBC_USER_ID、CTBC_PASSWORD")
    if not is_ctbc_url(page.url):
        raise ValueError("登入頁不是中國信託官方 HTTPS 網址")
    selectors = (
        'input[formcontrolname="custIxd"]:visible',
        'input[formcontrolname="userIxd"]:visible',
        'input[formcontrolname="pxd"]:visible',
    )
    # Wait for the actual credential fields. Unrelated visible inputs (e.g.
    # responsive search UI) must not block an otherwise complete login form.
    try:
        page.wait_for_function(
            """selectors => selectors.every(selector =>
                Array.from(document.querySelectorAll(selector))
                    .filter(el => el.getClientRects().length && el.type !== 'hidden').length === 1)""",
            arg=[selector.removesuffix(":visible") for selector in selectors],
            timeout=30_000,
        )
        read_bank_page(page)
        if urlparse(page.url).path.rstrip("/") != "/twrbc/twrbc-general/ot001/010":
            raise ValueError("未進入新版中信登入表單，已停止填寫帳密")
        fields = [page.locator(selector) for selector in selectors]
        if any(field.count() != 1 for field in fields):
            raise ValueError("中信登入表單欄位不符，已停止填寫帳密")
        for field, value in zip(fields, credentials):
            # Recheck after navigation before transmitting each credential.
            if not is_ctbc_url(page.url) or urlparse(page.url).path.rstrip("/") != "/twrbc/twrbc-general/ot001/010":
                raise ValueError("登入頁已跳轉，已停止填寫帳密")
            field.fill(value, timeout=10_000)
        page.get_by_role("button", name="登入", exact=True).click(timeout=10_000)
    except PlaywrightError as error:
        # A service error must not be misreported as a selector timeout.
        read_bank_page(page)
        if ("intercepts pointer events" in str(error) and
                page.locator('.modal-header:visible, h5.modal-title:visible').count()):
            raise BankQueryError(
                "中信登入按鈕被銀行提示視窗遮擋；需人工確認提示內容。"
                "本次不會強制點擊或重送帳密；可用 --manual-login 自行登入。",
                "login_notice_blocked",
            ) from error
        raise ValueError(
            "中信新版登入表單未完成載入或操作逾時；尚未確認登入成功。"
            "可加 --manual-login 自行登入；不會自動重送帳密。"
        ) from error

    return True


def query_balance(balance_selector: str | None = None, *, manual_login: bool = False) -> Decimal:
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    with sync_playwright() as playwright:
        with diagnostic_chrome_context(playwright, "ctbc", is_ctbc_url, system_chrome_context) as context:
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="load", timeout=30_000)
            read_bank_page(page)
            if not manual_login and login_from_environment(page):
                print("已使用本機設定送出登入；如有驗證碼或 OTP，請在 Chrome 完成驗證。")
            else:
                print("請在 Chrome 中自行登入中國信託網銀。")
            try:
                page.wait_for_url("**/twrbc/twrbc-home/qu000/010", timeout=120_000)
            except PlaywrightError as error:
                read_bank_page(page)
                raise ValueError("尚未完成中信登入或驗證，等待 120 秒逾時；未取得餘額，不會自動重送帳密") from error
            return read_twd_deposit_balance(page, balance_selector)


def read_twd_deposit_balance(page, balance_selector=None) -> Decimal:
    """Read the bank's TWD deposit total, excluding foreign-currency equivalents."""
    read_bank_page(page)
    deposits = page.locator("a.link").filter(has_text=re.compile(r"^臺幣存款$"))
    deposits.wait_for(state="visible", timeout=30_000)
    if deposits.count() != 1:
        raise ValueError("未找到唯一臺幣存款查詢入口")
    deposits.click(timeout=10_000)
    page.wait_for_url("**/twrbc/twrbc-deposit/qu001/010", timeout=30_000)
    read_bank_page(page)
    if balance_selector:
        matches = page.locator(balance_selector)
        matches.wait_for(state="visible", timeout=30_000)
        if matches.count() != 1:
            raise ValueError("--balance-selector 必須只對應一個顯示金額的元素")
        return parse_balance_text("帳戶餘額\n" + matches.inner_text(timeout=5_000))
    page.get_by_text(re.compile(r"^臺幣帳戶餘額")).wait_for(state="visible", timeout=30_000)
    return parse_balance_text(read_bank_page(page), labels=("臺幣帳戶餘額",))


def main() -> int:
    parser = argparse.ArgumentParser(description="登入後自動查詢中國信託臺幣存款合計餘額")
    parser.add_argument("--balance-selector", help="網銀金額元素的 CSS selector；頁面有多個金額時使用")
    parser.add_argument("--manual-login", action="store_true", help="略過本機帳密自動填寫，由使用者自行登入")
    args = parser.parse_args()
    try:
        balance = query_balance(args.balance_selector, manual_login=args.manual_login)
    except KeyboardInterrupt:
        print("查詢已取消")
        return 130
    except (ValueError, PlaywrightError, EOFError) as error:
        print(f"查詢失敗：{error}")
        return 1
    print(f"中國信託臺幣存款餘額：NT$ {balance:,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
