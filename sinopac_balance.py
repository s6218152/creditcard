"""Fill SinoPac credentials and read the balance automatically after verification."""

import argparse
from decimal import Decimal
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
BALANCE_LABELS = ("帳戶餘額", "存款餘額", "帳面餘額")


def is_sinopac_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in {
        "mma.sinopac.com", "bank.sinopac.com",
    }


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
