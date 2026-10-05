"""Fill SinoPac credentials and read the balance automatically after verification."""

import argparse
import re
from decimal import Decimal
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from ctbc_balance import parse_balance_text


LOGIN_URL = "https://mma.sinopac.com/MemberPortal/Member/MMALogin.aspx"
BALANCE_LABELS = ("帳戶餘額", "存款餘額", "帳面餘額")


def is_sinopac_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in {
        "mma.sinopac.com", "bank.sinopac.com",
    }


def read_balance_from_page(page, selector: str | None = None) -> Decimal:
    if not is_sinopac_url(page.url):
        raise ValueError("目前頁面不是永豐銀行官方 HTTPS 網址，已停止讀取")
    if selector:
        matches = [frame.locator(selector) for frame in page.frames if is_sinopac_url(frame.url)]
        matches = [locator for locator in matches if locator.count()]
        if sum(locator.count() for locator in matches) != 1:
            raise ValueError("--balance-selector 必須只對應一個顯示金額的元素")
        return parse_balance_text("帳戶餘額\n" + matches[0].inner_text(timeout=5_000))
    texts = []
    for frame in page.frames:
        if not is_sinopac_url(frame.url):
            continue
        try:
            texts.append(frame.locator("body").inner_text(timeout=5_000))
        except PlaywrightError:
            continue
    if not texts:
        raise ValueError("無法讀取永豐網銀頁面")
    return parse_balance_text("\n".join(texts), labels=BALANCE_LABELS)


def describe_page(page) -> str:
    """Return a small diagnostic without account numbers or page source."""
    descriptions = []
    for frame in page.frames:
        if not is_sinopac_url(frame.url):
            continue
        try:
            content = frame.locator("body").inner_text(timeout=3_000)
        except PlaywrightError:
            continue
        labels = []
        for line in content.splitlines():
            if "餘額" in line or "登入網路銀行" in line:
                sanitized = re.sub(r"\d", "＊", line.strip())[:60]
                if sanitized:
                    labels.append(sanitized)
        location = urlparse(frame.url)
        descriptions.append(f"{location.hostname}{location.path}: {', '.join(labels[:6]) or '未找到餘額欄名'}")
    return "；".join(descriptions) or "未找到永豐銀行頁面"


def query_balance(balance_selector: str | None = None) -> Decimal:
    from manual_bank_balance import query_balance as query_bank_balance
    return query_bank_balance("sinopac", balance_selector)


def main() -> int:
    parser = argparse.ArgumentParser(description="查詢永豐銀行單一存款帳戶餘額")
    parser.add_argument("--balance-selector", help="金額元素的 CSS selector；有多個金額時使用")
    args = parser.parse_args()
    try:
        balance = query_balance(args.balance_selector)
    except KeyboardInterrupt:
        print("查詢已取消")
        return 130
    except (ValueError, PlaywrightError, EOFError) as error:
        print(f"查詢失敗：{error}")
        return 1
    print(f"永豐銀行存款餘額：{balance:,.2f}（幣別依網銀頁面）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
