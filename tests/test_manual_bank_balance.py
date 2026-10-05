from decimal import Decimal
from types import SimpleNamespace

import pytest

from bank_registry import MANUAL_BANKS
from bank_login import BankQueryError
from manual_bank_balance import check_customer_information_update, check_deposit_access, is_bank_url, is_hncb_nickname_prompt, parse_dbs_twd_overview, parse_hncb_deposit_rows, read_balance_from_page


@pytest.mark.parametrize("bank", MANUAL_BANKS)
def test_only_official_https_urls(bank):
    url = MANUAL_BANKS[bank][1]
    assert is_bank_url(url, bank)
    assert not is_bank_url(url.replace("https:", "http:"), bank)
    assert not is_bank_url("https://" + MANUAL_BANKS[bank][2][0] + ".evil.example/", bank)


def frame(url, content, count=1):
    return SimpleNamespace(url=url, locator=lambda selector: SimpleNamespace(
        count=lambda: count, inner_text=lambda **kwargs: content))


def test_dbs_reports_authenticated_customer_without_deposit_account():
    url = "https://internet-banking.dbs.com.tw/digitw/overview"
    page = SimpleNamespace(url=url, frames=[frame(url, "帳戶概覽\n活期存款\n請洽分行開立存款帳號")])
    with pytest.raises(BankQueryError) as error:
        check_deposit_access(page, "dbs")
    assert error.value.code == "deposit_access_unavailable"


def test_dbs_reads_loaded_twd_demand_deposit_header():
    value = parse_dbs_twd_overview("活期存款", "帳戶餘額", "TWD", "72")
    assert value == Decimal("72")
    assert value.currency == "TWD"
    assert value.scope == "twd_current_deposit_total"


@pytest.mark.parametrize("args", [
    ("活期存款", "可用餘額", "TWD", "72"),
    ("活期存款", "帳戶餘額", "USD", "72"),
    ("定期存款", "帳戶餘額", "TWD", "72"),
])
def test_dbs_rejects_wrong_scope_or_currency(args):
    with pytest.raises(ValueError):
        parse_dbs_twd_overview(*args)


def test_hncb_only_recognizes_exact_two_stage_nickname_prompt():
    assert is_hncb_nickname_prompt("hncb", "請輸入代號")
    assert not is_hncb_nickname_prompt("hncb", "代號錯誤")
    assert not is_hncb_nickname_prompt("esun", "請輸入代號")


def test_hncb_reports_required_customer_information_update():
    url = "https://netbank.hncb.com.tw/netbank/pages/jsp/Personal_new/html/personal.jsp"
    content = "SnY/數位存款帳戶基本資料維護\n步驟一\n填寫EDD問卷\n步驟二\n變更基本資料"
    page = SimpleNamespace(url=url, frames=[frame("https://netbank.hncb.com.tw/netbank/servlet/TrxDispatcher", content)])
    with pytest.raises(BankQueryError) as error:
        check_customer_information_update(page, "hncb")
    assert error.value.code == "customer_information_update_required"


def test_hncb_sums_only_explicit_twd_book_balances():
    rows = [
        ["帳號", "類別", "幣別", "帳上餘額", "原幣可用", "折合新台幣", "薪轉利率", "餘額查詢", "明細查詢", "轉帳", "其他查詢"],
        ["111", "活儲", "新台幣", "203.00", "203.00", "-", "-", "餘額", "明細", "轉帳", "查詢"],
        ["222", "活存", "", "99.00", "99.00", "-", "-", "餘額", "明細", "", "查詢"],
        ["333", "活存", "美元", "10.00", "10.00", "320.00", "-", "餘額", "明細", "", "查詢"],
    ]
    value = parse_hncb_deposit_rows(rows)
    assert value == Decimal("203.00")
    assert value.currency == "TWD"
    assert value.scope == "twd_current_deposit_total"


def test_hncb_rejects_missing_or_duplicate_accounts_instead_of_guessing():
    with pytest.raises(ValueError):
        parse_hncb_deposit_rows([])
    duplicate = [
        ["111", "活儲", "新台幣", "1.00", "1.00", "-", "-", "餘額", "明細", "", "查詢"],
        ["111", "活儲", "新台幣", "1.00", "1.00", "-", "-", "餘額", "明細", "", "查詢"],
    ]
    with pytest.raises(ValueError):
        parse_hncb_deposit_rows(duplicate)


def test_dbs_deposit_access_check_ignores_foreign_or_unrelated_pages():
    url = "https://internet-banking.dbs.com.tw/digitw/overview"
    foreign = frame("https://evil.example/", "請洽分行開立存款帳號")
    check_deposit_access(SimpleNamespace(url=url, frames=[foreign, frame(url, "活期存款\n123")]), "dbs")


def test_reads_official_frame_ignores_foreign_frame():
    url = MANUAL_BANKS["esun"][1]
    page = SimpleNamespace(url=url, frames=[frame(url, "存款餘額\n1,234.50"), frame("https://evil.example", "存款餘額\n999")])
    assert read_balance_from_page(page, "esun") == Decimal("1234.50")


def test_multiple_accounts_are_not_summed_or_selected():
    url = MANUAL_BANKS["fubon"][1]
    page = SimpleNamespace(url=url, frames=[frame(url, "帳戶餘額\n100\n帳戶餘額\n200")])
    with pytest.raises(ValueError):
        read_balance_from_page(page, "fubon")


def test_selector_must_be_unique_across_frames():
    url = MANUAL_BANKS["dbs"][1]
    page = SimpleNamespace(url=url, frames=[frame(url, "100"), frame(url, "200")])
    with pytest.raises(ValueError):
        read_balance_from_page(page, "dbs", "#balance")
    page.frames = [frame(url, "100")]
    assert read_balance_from_page(page, "dbs", "#balance") == Decimal("100")


def test_manual_bank_dispatch(monkeypatch, capsys):
    import manual_bank_balance
    from bank_balances import run_queries
    calls = []
    monkeypatch.setattr(manual_bank_balance, "query_balance", lambda bank, selector: calls.append((bank, selector)) or Decimal("123"))
    assert run_queries({"balance_query": {"esun": {"enabled": True, "balance_selector": "#amount"}}})
    assert calls == [("esun", "#amount")]
    assert "玉山銀行帳戶餘額：123.00" in capsys.readouterr().out


def test_twd_total_has_currency_metadata_and_excludes_fx():
    from manual_bank_balance import read_automatic_balance
    url = 'https://www.ubot.com.tw/'
    page = SimpleNamespace(url=url, frames=[frame(url, '臺幣存款總額\n1,234\n外幣存款\n帳戶餘額\n9,999')])
    value = read_automatic_balance(page, 'ubot')
    assert value == Decimal('1234')
    assert value.currency == 'TWD'
    assert value.scope == 'twd_deposit_total'


def test_browser_errors_do_not_reveal_filled_credentials(monkeypatch):
    import manual_bank_balance
    from playwright.sync_api import Error
    from bank_login import BankQueryError
    def fail(*args, **kwargs):
        raise Error('fill(very-secret-password) failed')
    monkeypatch.setattr(manual_bank_balance, '_query_balance', fail)
    with pytest.raises(BankQueryError) as error:
        manual_bank_balance.query_balance('esun')
    assert 'very-secret-password' not in str(error.value)
    assert error.value.code == 'browser_action_failed'


@pytest.mark.parametrize("fixed, expected", [("0", "2100"), ("10,000.50", "12100.50")])
def test_esun_totals_twd_categories_without_counting_account_rows_or_fx(fixed, expected):
    from manual_bank_balance import parse_esun_twd_overview
    text = f"""臺幣存款總覽
活存支存餘額
2,100
0000000000000
2,100
定存本金餘額
{fixed}
外幣活存
5
等值新臺幣
2,105
"""
    value = parse_esun_twd_overview(text)
    assert value == Decimal(expected)
    assert value.currency == "TWD"
    assert value.scope == "twd_deposit_total"


@pytest.mark.parametrize("text", [
    "活存支存餘額\n2,100",  # Missing fixed deposit subtotal is not zero.
    "活存支存餘額\n2,100\n定存本金餘額\n載入中",
    "活存支存餘額\n2,100\n活存支存餘額\n2,100\n定存本金餘額\n0",
])
def test_esun_missing_loading_or_duplicate_categories_fail_instead_of_guessing(text):
    from manual_bank_balance import parse_esun_twd_overview
    with pytest.raises(ValueError):
        parse_esun_twd_overview(text)


def test_cathay_reads_total_with_separate_currency_symbol():
    from manual_bank_balance import parse_cathay_twd_overview
    value = parse_cathay_twd_overview("臺幣帳戶總額\n\n帳戶餘額\n\n$\n\n10,440")
    assert value == Decimal("10440")
    assert value.currency == "TWD"
    assert value.scope == "twd_deposit_total"


@pytest.mark.parametrize("text", [
    "外幣帳戶總額\n帳戶餘額\n$\n10,440",
    "臺幣帳戶總額\n帳戶餘額\n$\n****",
    "臺幣帳戶總額\n帳戶餘額\n$\n10,440\n帳戶餘額\n$\n10,440",
])
def test_cathay_does_not_guess_from_fx_masked_or_duplicate_amounts(text):
    from manual_bank_balance import parse_cathay_twd_overview
    with pytest.raises(ValueError):
        parse_cathay_twd_overview(text)


@pytest.mark.parametrize("book, available", [("12,500.55", "9,000.00"), ("0.00", "500.00")])
def test_chb_reads_book_subtotal_and_keeps_zero_as_a_successful_amount(book, available):
    from manual_bank_balance import parse_chb_current_subtotal
    headers = ["筆次", "帳號", "分行名稱", "帳面餘額", "可用餘額", "本日交換票金額", "功能"]
    cells = [{"text": "小計", "span": 3}, {"text": book, "span": 1},
             {"text": available, "span": 1}, {"text": "100.00", "span": 1}, {"text": "", "span": 1}]
    value = parse_chb_current_subtotal(headers, cells)
    assert value == Decimal(book.replace(",", ""))
    assert value.currency == "TWD"
    assert value.scope == "twd_current_deposit_total"


def test_frameset_without_body_does_not_block_reading_its_account_frame():
    url = MANUAL_BANKS["ubot"][1]
    def missing_body(selector):
        def should_not_read(**kwargs):
            raise AssertionError("A frameset has no body to wait for")
        return SimpleNamespace(count=lambda: 0, inner_text=should_not_read)
    page = SimpleNamespace(url=url, frames=[SimpleNamespace(url=url, locator=missing_body),
        frame(url, "帳戶餘額\n100")])
    assert read_balance_from_page(page, "ubot") == Decimal("100")


def test_sinopac_total_adds_current_and_fixed_twd_deposits_and_excludes_fx():
    from manual_bank_balance import parse_sinopac_deposit_rows
    headers=["帳號","幣別","餘額","綜存定存","快速連結"]
    rows=[["活期儲蓄\n001-002-12345678-9","新台幣TWD","14,716.25","1,000.50",""],
          ["另一帳戶\n001-003-12345678-0","新台幣","0.00","0.00",""],
          ["外幣\n001-004-12345678-1","美金USD","999.00","500.00",""]]
    value=parse_sinopac_deposit_rows(headers,rows)
    assert value == Decimal("15716.75")
    assert value.currency == "TWD"
    assert value.scope == "twd_deposit_total"
    with pytest.raises(ValueError):parse_sinopac_deposit_rows(headers,rows+[rows[0]])


def test_account_rows_use_book_balance_keep_cents_and_distinguish_certificates():
    from manual_bank_balance import parse_twd_account_rows
    headers=["帳號","幣別","即時餘額","可用餘額","存單號碼"]
    rows=[["10000001","臺幣","121.25","9999.00",""],
          ["10000002","臺幣","20.50","8888.00","A"],
          ["10000002","臺幣","30.75","7777.00","B"],
          ["10000003","USD","500.00","500.00",""]]
    value=parse_twd_account_rows(headers,rows,"即時餘額","存單號碼")
    assert value == Decimal("172.50")
    assert value.currency == "TWD"
    with pytest.raises(ValueError):parse_twd_account_rows(headers,rows+[rows[0]],"即時餘額","存單號碼")
    rows[0][2]="讀取中"
    with pytest.raises(ValueError):parse_twd_account_rows(headers,rows,"即時餘額","存單號碼")
