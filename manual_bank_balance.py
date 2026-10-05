"""Log in once and read a deposit balance without terminal input."""

from urllib.parse import urlparse
from decimal import Decimal
import re
import time

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from bank_registry import AUTO_BANKS
from chrome_session import system_chrome_context
from bank_diagnostics import diagnostic_chrome_context, has_verification_challenge
from bank_login import BankQueryError, LOGIN_FORMS, login_once, locate_form, has_logged_in, check_login_error, handle_bank_dialog
from bank_captcha import CAPTCHA_IMAGES
from ctbc_balance import parse_balance_text

BALANCE_LABELS = ("帳戶餘額", "存款餘額", "帳面餘額", "目前餘額", "Account Balance", "Current Balance")


def is_bank_url(url, bank):
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.hostname) and any(
        parsed.hostname == domain or parsed.hostname.endswith("." + domain)
        for domain in AUTO_BANKS[bank][2]
    )


def read_balance_from_page(page, bank, selector=None):
    if not is_bank_url(page.url, bank):
        raise ValueError("目前頁面不是該銀行官方 HTTPS 網址")
    frames = [frame for frame in page.frames if is_bank_url(frame.url, bank)]
    if selector:
        locators = [frame.locator(selector) for frame in frames]
        matches = [locator for locator in locators if locator.count()]
        if sum(locator.count() for locator in matches) != 1:
            raise ValueError("balance_selector 必須只對應一個顯示金額的元素")
        return parse_balance_text("帳戶餘額\n" + matches[0].inner_text(timeout=5_000))
    texts = []
    for frame in frames:
        try:
            body = frame.locator("body")
            if body.count():
                texts.append(body.inner_text(timeout=5_000))
        except PlaywrightError:
            continue
    return parse_balance_text("\n".join(texts), labels=BALANCE_LABELS)


NAVIGATION_LABELS = (
    "臺幣存款", "台幣存款", "臺幣帳戶查詢", "台幣帳戶查詢", "臺幣存款查詢", "台幣存款查詢",
    "存款帳戶查詢", "存款餘額查詢", "我的存款", "帳戶總覽", "存款總覽", "帳戶概要", "我的帳戶", "帳務查詢",
)
TWD_TOTAL_LABELS = ("臺幣帳戶餘額", "台幣帳戶餘額", "臺幣存款總額", "台幣存款總額", "新臺幣存款合計")


class BankBalance(Decimal):
    def __new__(cls, value, currency=None, scope="single_account"):
        instance = super().__new__(cls, value)
        instance.currency = currency
        instance.scope = scope
        return instance


def check_deposit_access(page, bank):
    """Report explicit bank-side account availability instead of guessing a balance."""
    if bank != "dbs" or not is_bank_url(page.url, bank):
        return
    for frame in page.frames:
        if not is_bank_url(frame.url, bank):
            continue
        body = frame.locator("body")
        if body.count() == 1 and "請洽分行開立存款帳號" in body.inner_text(timeout=1000):
            raise BankQueryError("星展已登入，但銀行顯示尚未開立存款帳號", "deposit_access_unavailable")


def check_customer_information_update(page, bank):
    if bank != "hncb" or not is_bank_url(page.url, bank):
        return
    required = ("SnY/數位存款帳戶基本資料維護", "填寫EDD問卷", "變更基本資料")
    for frame in page.frames:
        if not is_bank_url(frame.url, bank):
            continue
        body = frame.locator("body")
        if body.count() == 1:
            text = body.inner_text(timeout=1000)
            if all(label in text for label in required):
                raise BankQueryError("華南已登入，但銀行要求先完成數位存款帳戶基本資料與 EDD 問卷更新", "customer_information_update_required")


def is_hncb_nickname_prompt(bank, text):
    return bank == "hncb" and text.strip() == "請輸入代號"


def parse_esun_twd_overview(text):
    # These two bank-provided category subtotals do not overlap. Account rows
    # repeat the current-account amount; the asset chart also includes FX.
    current = parse_balance_text(text, labels=("活存支存餘額",))
    fixed = parse_balance_text(text, labels=("定存本金餘額",))
    return BankBalance(current + fixed, "TWD", "twd_deposit_total")


def parse_dbs_twd_overview(title, label, currency, amount):
    if title.strip() != "活期存款" or label.strip() != "帳戶餘額" or currency.strip() != "TWD":
        raise ValueError("星展活期存款欄位或幣別不符")
    return BankBalance(parse_balance_text("帳戶餘額\n" + amount.strip()), "TWD", "twd_current_deposit_total")


def read_dbs_twd_balance(page):
    matches = []
    selectors = (
        '[data-testid="dashboard.casa.header.title"]:visible',
        '[data-testid="dashboard.casa.header.label"]:visible',
        '[data-testid="dashboard.casa.header.currency"]:visible',
        '[data-testid="dashboard.casa.header.amount"]:visible',
    )
    for frame in page.frames:
        if not is_bank_url(page.url, "dbs") or not is_bank_url(frame.url, "dbs"):
            continue
        fields = [frame.locator(selector) for selector in selectors]
        if all(field.count() == 1 for field in fields):
            matches.append([field.inner_text(timeout=1000) for field in fields])
    if len(matches) != 1:
        raise ValueError("星展活期存款餘額尚未完整載入")
    return parse_dbs_twd_overview(*matches[0])


def read_esun_twd_balance(page):
    if not is_bank_url(page.url, "esun"):
        raise ValueError("目前頁面不是玉山銀行官方 HTTPS 網址")
    widgets = []
    for frame in page.frames:
        if not is_bank_url(frame.url, "esun"):
            continue
        matches = frame.locator(".widget-balance-detail-container").filter(
            has=frame.get_by_text("臺幣存款總覽", exact=True))
        widgets.extend(matches.nth(index) for index in range(matches.count())
                       if matches.nth(index).is_visible())
    if len(widgets) != 1:
        raise ValueError("尚未找到唯一的玉山臺幣存款總覽")
    return parse_esun_twd_overview(widgets[0].inner_text(timeout=1000))


def parse_cathay_twd_overview(text):
    if sum(line.strip() == "臺幣帳戶總額" for line in text.splitlines()) != 1:
        raise ValueError("未找到唯一的國泰臺幣帳戶總額")
    # The bank renders the currency symbol and amount in separate paragraphs.
    text = re.sub(r"(?m)^\s*\$\s*\n\s*(?=[+-]?\d)", "$", text)
    return BankBalance(parse_balance_text(text), "TWD", "twd_deposit_total")


def read_cathay_twd_balance(page):
    if not is_bank_url(page.url, "cathay"):
        raise ValueError("目前頁面不是國泰銀行官方 HTTPS 網址")
    sections = []
    for frame in page.frames:
        if not is_bank_url(frame.url, "cathay"):
            continue
        headings = frame.get_by_role("heading", name="臺幣帳戶總額", exact=True)
        sections.extend(headings.nth(index).locator("..") for index in range(headings.count())
                        if headings.nth(index).is_visible())
    if len(sections) != 1:
        raise ValueError("尚未找到唯一的國泰臺幣帳戶總額區塊")
    return parse_cathay_twd_overview(sections[0].inner_text(timeout=1000))


def parse_chb_current_subtotal(headers, cells):
    headers = [header.strip() for header in headers]
    if headers.count("帳面餘額") != 1 or "分行名稱" not in headers or not cells or cells[0]["text"].strip() != "小計":
        raise ValueError("彰銀活期餘額小計的欄位不符")
    target = headers.index("帳面餘額")
    position, amount = 0, None
    for cell in cells:
        span = cell["span"]
        if not isinstance(span, int) or span < 1:
            raise ValueError("彰銀小計儲存格跨欄不符")
        if position <= target < position + span:
            if span != 1 or amount is not None:
                raise ValueError("彰銀帳面餘額小計不明確")
            amount = re.sub(r"^\s*帳面餘額\s*[：:]?\s*", "", cell["text"])
        position += span
    if position != len(headers) or amount is None:
        raise ValueError("彰銀小計缺少帳面餘額")
    value = parse_balance_text("帳面餘額\n" + amount, labels=("帳面餘額",))
    return BankBalance(value, "TWD", "twd_current_deposit_total")


def read_chb_current_balance(page):
    if not is_bank_url(page.url, "chb"):
        raise ValueError("目前頁面不是彰銀官方 HTTPS 網址")
    tables = []
    for frame in page.frames:
        parsed = urlparse(frame.url)
        if not is_bank_url(frame.url, "chb") or not parsed.path.endswith("/TxnPage/tw") or parsed.fragment != "!/tw01001_2":
            continue
        matches = frame.locator("#content table").filter(
            has=frame.locator("th").filter(has_text=re.compile(r"^\s*分行名稱\s*$")))
        tables.extend(matches.nth(i) for i in range(matches.count()) if matches.nth(i).is_visible())
    if len(tables) != 1:
        raise ValueError("尚未找到唯一的彰銀新臺幣活期餘額表格")
    table = tables[0]
    subtotals = table.locator("tr").filter(has_text=re.compile(r"^\s*小計"))
    if subtotals.count() != 1:
        raise ValueError("彰銀新臺幣活期餘額小計不明確")
    cells = subtotals.locator("td").evaluate_all(
        "(els)=>els.map(e=>({text:e.innerText.trim(),span:e.colSpan}))")
    return parse_chb_current_subtotal(table.locator("th").all_inner_texts(), cells)


def read_automatic_balance(page, bank, selector=None):
    if selector:
        return BankBalance(read_balance_from_page(page, bank, selector))
    if bank == "esun":
        return read_esun_twd_balance(page)
    if bank == "cathay":
        return read_cathay_twd_balance(page)
    if bank == "chb":
        return read_chb_current_balance(page)
    if bank == "sinopac":
        return read_sinopac_twd_balance(page)
    if bank == "fubon":
        return read_fubon_twd_balance(page)
    if bank == "shanghai":
        return read_shanghai_twd_balance(page)
    if bank == "feib":
        return read_feib_twd_balance(page)
    if bank == "first_bank":
        return read_first_twd_balance(page)
    if bank == "taishin":
        return read_taishin_twd_balance(page)
    if bank == "skbank":
        return read_skbank_twd_balance(page)
    if bank == "hncb":
        return read_hncb_twd_balance(page)
    if bank == "dbs":
        return read_dbs_twd_balance(page)
    texts = []
    for frame in page.frames:
        if is_bank_url(frame.url, bank):
            try:
                body = frame.locator("body")
                if body.count():
                    texts.append(body.inner_text(timeout=1000))
            except PlaywrightError:
                continue
    text = "\n".join(texts)
    # Prefer an explicitly named TWD total. Never add bare amounts or FX equivalents.
    if any(label in text for label in TWD_TOTAL_LABELS):
        return BankBalance(parse_balance_text(text, labels=TWD_TOTAL_LABELS), "TWD", "twd_deposit_total")
    value = read_balance_from_page(page, bank)
    return BankBalance(value)


def parse_sinopac_deposit_rows(headers, rows):
    if [label.strip() for label in headers] != ["帳號", "幣別", "餘額", "綜存定存", "快速連結"]:
        raise ValueError("永豐存款總覽欄位不符")
    amounts, accounts = [], set()
    for cells in rows:
        if len(cells) != 5:
            raise ValueError("永豐存款總覽資料列尚未完整載入")
        currency_text = re.sub(r"\s+", "", cells[1])
        currency_match = re.fullmatch(r"(?:[^\x00-\x7f]+)?([A-Z]{3})", currency_text)
        currency = currency_match.group(1) if currency_match else "TWD" if currency_text in ("新台幣", "新臺幣") else ""
        if not currency:
            raise ValueError("永豐存款列的幣別不明確")
        if currency != "TWD":
            continue
        account = cells[0].strip().splitlines()[-1].strip()
        if not re.fullmatch(r"\d[\d-]{5,}", account) or account in accounts:
            raise ValueError("永豐臺幣存款帳號缺漏或重複")
        accounts.add(account)
        # Separate current-deposit and associated fixed-deposit columns.
        amounts.append(parse_balance_text("帳戶餘額\n" + cells[2]) +
                       parse_balance_text("帳戶餘額\n" + cells[3]))
    if not amounts:
        raise ValueError("永豐存款總覽尚未找到臺幣存款資料")
    return BankBalance(sum(amounts, Decimal(0)), "TWD", "twd_deposit_total")


def read_sinopac_twd_balance(page):
    for frame in page.frames:
        if is_bank_url(page.url, "sinopac") and is_bank_url(frame.url, "sinopac") and urlparse(frame.url).path.lower().endswith("/mma_assets_summary.aspx"):
            table = frame.locator("#tbasset:visible")
            if table.count() == 1:
                return parse_sinopac_deposit_rows(table.locator("th").all_inner_texts(),
                    table.locator("tr:has(td)").evaluate_all("""es=>es.map(e=>Array.from(e.querySelectorAll(':scope > td'),(c,i)=>{
                        if(i===0){const parts=c.querySelectorAll(':scope > p');
                            return parts.length===2 ? parts[0].innerText.trim()+'\\n'+parts[1].innerText.trim() : '';}
                        return c.innerText.trim();}))"""))
    raise ValueError("尚未找到永豐存款總覽")


def parse_twd_account_rows(headers, rows, amount_label, certificate_label=None):
    headers = [header.strip() for header in headers]
    required = ("帳號", "幣別", amount_label)
    if any(headers.count(label) != 1 for label in required):
        raise ValueError("存款資料表的帳號、幣別或餘額欄位不明確")
    account_column, currency_column, amount_column = (headers.index(label) for label in required)
    certificate_column = headers.index(certificate_label) if certificate_label and headers.count(certificate_label) == 1 else None
    amounts, seen = [], set()
    for cells in rows:
        if len(cells) != len(headers):
            raise ValueError("存款資料列尚未完整載入")
        currency = re.sub(r"\s+", "", cells[currency_column])
        if not currency:
            raise ValueError("存款資料列幣別缺漏")
        if currency not in ("臺幣", "台幣", "新台幣", "新臺幣", "TWD", "新台幣TWD", "新臺幣TWD"):
            continue
        account = cells[account_column].strip()
        certificate = cells[certificate_column].strip() if certificate_column is not None else ""
        key = (account, certificate)
        if not account or key in seen:
            raise ValueError("臺幣存款資料列的帳號缺漏或重複")
        seen.add(key)
        amounts.append(parse_balance_text("帳戶餘額\n" + cells[amount_column]))
    if not amounts:
        raise ValueError("尚未找到臺幣存款資料列")
    return BankBalance(sum(amounts, Decimal(0)), "TWD", "twd_deposit_total")


def visible_table_rows(table):
    return table.locator("tr:has(td)").evaluate_all("""es=>es.map(e=>Array.from(e.querySelectorAll(':scope > td'))
        .filter(c=>c.getClientRects().length).map(c=>c.innerText.trim())).filter(cells=>cells.length)""")


def parse_hncb_deposit_rows(rows):
    balances = []
    accounts = set()
    for cells in rows:
        if len(cells) != 11 or cells[1].strip() not in ("活存", "活儲") or cells[2].strip() != "新台幣":
            continue
        account = re.sub(r"\s", "", cells[0])
        if not account or account in accounts:
            raise ValueError("華南帳務總覽包含重複或空白的新台幣帳號")
        accounts.add(account)
        balances.append(parse_balance_text("帳戶餘額\n" + cells[3].strip()))
    if not balances:
        raise ValueError("華南帳務總覽未找到幣別明確為新台幣的活期存款帳上餘額")
    return BankBalance(sum(balances, Decimal(0)), "TWD", "twd_current_deposit_total")


def read_hncb_twd_balance(page):
    matches = []
    for frame in page.frames:
        if not is_bank_url(page.url, "hncb") or not is_bank_url(frame.url, "hncb"):
            continue
        tables = frame.locator("table:visible")
        for index in range(tables.count()):
            table = tables.nth(index)
            rows = table.locator(":scope > tbody > tr").evaluate_all("""rows => rows.map(row =>
                Array.from(row.children).filter(cell => cell.tagName === 'TD' && cell.getClientRects().length)
                .map(cell => cell.innerText.trim())).filter(cells => cells.length)""")
            if (len(rows) >= 3 and rows[0][:4] == ["帳號", "類別", "幣別", "帳上餘額"] and
                    rows[1] == ["原幣", "折合新台幣"]):
                matches.append(rows[2:])
    if len(matches) != 1:
        raise ValueError("尚未找到唯一的華南帳務總覽存款表格")
    return parse_hncb_deposit_rows(matches[0])


def read_fubon_twd_balance(page):
    for frame in page.frames:
        if is_bank_url(page.url, "fubon") and is_bank_url(frame.url, "fubon") and urlparse(frame.url).path.lower().endswith("/cdfqu002_home.faces"):
            table = frame.locator('table:has(th:text-is("即時餘額")):visible')
            if table.count() == 1:
                return parse_twd_account_rows(table.locator("th").all_inner_texts(), visible_table_rows(table), "即時餘額", "存單號碼")
    raise ValueError("尚未找到富邦我的存款明細表")


def read_shanghai_twd_balance(page):
    for frame in page.frames:
        if not is_bank_url(page.url, "shanghai") or not is_bank_url(frame.url, "shanghai") or urlparse(frame.url).fragment != "/twde/qr/04/01":
            continue
        tables = frame.locator('table:has(th:text-is("帳上餘額")):visible')
        matches = [tables.nth(i) for i in range(tables.count()) if [s.strip() for s in tables.nth(i).locator("th").all_inner_texts()] ==
                   ["類別", "帳號", "分行名稱", "幣別", "帳上餘額", "到期日", "備註"]]
        if len(matches) == 1:
            table = matches[0]
            return parse_twd_account_rows(table.locator("th").all_inner_texts(), visible_table_rows(table), "帳上餘額")
    raise ValueError("尚未找到上海商銀臺幣所有帳號查詢資料")


def read_feib_twd_balance(page):
    for frame in page.frames:
        if not is_bank_url(page.url, "feib") or not is_bank_url(frame.url, "feib") or not urlparse(frame.url).path.endswith("/SvDeposit.jsp"):
            continue
        tables = frame.locator('table:has(th:text-is("帳戶餘額")):visible')
        expected = (["所屬分行", "帳戶類別", "帳號", "幣別", "帳戶餘額", "我要查詢/申請"],
                    ["往來單位", "帳戶類別", "帳號", "幣別", "帳戶餘額", ""])
        if tables.count() == 2 and all([s.strip() for s in tables.nth(i).locator("th").all_inner_texts()] == labels for i, labels in enumerate(expected)):
            # Both deposit categories must be loaded. An empty fixed-deposit
            # tbody is valid; a missing category or loading row is not.
            rows = [row for i in range(2) for row in visible_table_rows(tables.nth(i))]
            return parse_twd_account_rows(expected[0], rows, "帳戶餘額")
    raise ValueError("尚未找到遠東商銀活期及定期性存款明細表")


def read_first_twd_balance(page):
    for frame in page.frames:
        if not is_bank_url(page.url, "first_bank") or not is_bank_url(frame.url, "first_bank") or not urlparse(frame.url).path.endswith("/1/acntReviewAll.html"):
            continue
        row = frame.locator('tr:has(> td:text-is("臺幣存款")):visible')
        if row.count() == 1:
            cells = row.locator(":scope > td").all_inner_texts()
            if len(cells) == 3 and cells[0].strip() == "臺幣存款" and re.fullmatch(r"NTD\s+[\d,]+(?:\.\d{1,2})?", cells[1].strip()):
                return BankBalance(parse_balance_text("帳戶餘額\n" + re.sub(r"^NTD\s+", "", cells[1].strip())), "TWD", "twd_deposit_total")
    raise ValueError("尚未找到第一銀行帳戶總覽的臺幣存款總額")


def read_taishin_twd_balance(page):
    for frame in page.frames:
        if not is_bank_url(page.url, "taishin") or not is_bank_url(frame.url, "taishin") or urlparse(frame.url).fragment.split("?", 1)[0] != "/RB0100/0100":
            continue
        total = frame.locator(".summaryTotal:visible")
        if total.count() == 1:
            title = total.locator(".swiperCard__title")
            amount = total.locator(".swiperCard__money")
            if title.count() == 1 and title.inner_text(timeout=1000).strip() == "臺幣存款" and amount.count() == 1:
                return BankBalance(parse_balance_text("帳戶餘額\n" + amount.inner_text(timeout=1000).replace("\n", "")), "TWD", "twd_deposit_total")
    raise ValueError("尚未找到台新臺幣總覽的存款總計")


def read_skbank_twd_balance(page):
    if not is_bank_url(page.url, "skbank") or not urlparse(page.url).path.rstrip("/").endswith("/AccountQuery/QueryAcctSummary"):
        raise ValueError("尚未進入新光我的帳戶總覽")
    label = page.locator(".scale__type:visible").filter(has_text=re.compile(r"^\s*臺幣存款/定存\s*$"))
    if label.count() == 1:
        amount = label.locator("..").locator(".scale__num:visible")
        if amount.count() == 1:
            return BankBalance(parse_balance_text("帳戶餘額\n" + amount.inner_text(timeout=1000)), "TWD", "twd_deposit_total")
    raise ValueError("新光臺幣存款/定存金額尚未完整載入")


def click_chb_navigation(page, visited):
    for frame in page.frames:
        if not is_bank_url(page.url, "chb") or not is_bank_url(frame.url, "chb"):
            continue
        if frame.locator("#loading-icon-wrap.show:visible").count():
            continue
        if "chb_twd_menu" not in visited:
            menu = frame.locator('a[data-txnmenuid="TW"]:visible')
            if menu.count() != 1:
                continue
            menu.click(timeout=5000)
            entry = menu.locator("..").locator('a[data-url="../TxnPage/tw#!/tw01001_1"]')
            if entry.count() != 1:
                raise ValueError("未找到唯一的彰銀新臺幣活期餘額入口")
            try:
                # Opening the menu can start another loading layer. The
                # pre-menu check cannot protect this subsequent click.
                frame.locator("#loading-icon-wrap.show:visible").wait_for(
                    state="hidden", timeout=15_000)
                entry.click(timeout=5000)
            except PlaywrightError as error:
                # Distinguish a hidden template link from an unavailable menu.
                # Counts and visibility only; no account text or identifiers.
                try:
                    error.navigation_state = {
                        "selected_entry_visible": entry.is_visible(),
                        "visible_matching_entries": frame.locator(
                            'a[data-url="../TxnPage/tw#!/tw01001_1"]:visible').count(),
                    }
                except PlaywrightError:
                    error.navigation_state = {"state": "page_unavailable"}
                raise
            visited.add("chb_twd_menu")
            return True
        parsed = urlparse(frame.url)
        if "chb_twd_query" not in visited and parsed.path.endswith("/TxnPage/tw") and parsed.fragment == "!/tw01001_1":
            content = frame.locator("#content")
            accounts = content.locator("select:visible")
            confirm = content.get_by_text("確定", exact=True)
            if accounts.count() == 1 and confirm.count() == 1 and confirm.is_visible():
                accounts.select_option(label="全部帳號", timeout=5000)
                confirm.click(timeout=5000)
                visited.add("chb_twd_query")
                return True
    return False


def click_balance_navigation(page, bank, visited):
    if bank == "skbank" and is_bank_url(page.url, bank):
        entry = page.locator('a.iMenu__pib[href="zh-TW/AccountQuery/QueryAcctSummary/"]:visible')
        if "skbank_accounts" not in visited and entry.count() == 1:
            visited.add("skbank_accounts")
            entry.click(timeout=5000)
            return True
        return False
    if bank == "first_bank" and is_bank_url(page.url, bank):
        for frame in page.frames:
            if not is_bank_url(frame.url, bank) or not urlparse(frame.url).path.endswith("/1/acntReviewAll.html"):
                continue
            button = frame.locator("#m-sum:visible")
            if "first_twd_total" not in visited and button.count() == 1:
                visited.add("first_twd_total")
                button.click(timeout=5000)
                return True
        return False
    if bank == "shanghai" and is_bank_url(page.url, bank):
        for label in ("臺幣存匯", "臺幣帳戶查詢", "所有帳戶查詢"):
            if label in visited:
                continue
            entry = page.locator('button.accordion-button:visible').filter(has_text=re.compile(r"^\s*" + re.escape(label) + r"\s*$"))
            if entry.count() == 1:
                visited.add(label)
                entry.click(timeout=5000)
                return True
        return False
    if bank == "ubot" and is_bank_url(page.url, bank):
        if "ubot_menu" not in visited:
            menu = page.locator('a.btn.dropdown-toggle:text-is("帳戶查詢"):visible')
            if menu.count() == 1:
                menu.click(timeout=5000)
                visited.add("ubot_menu")
                return True
        if "ubot_balance" not in visited:
            entry = page.locator('a.dropdown-item[href="#/B0103001"]:visible')
            if entry.count() == 1:
                entry.click(timeout=5000)
                visited.add("ubot_balance")
                return True
        return False
    if bank == "chb":
        return click_chb_navigation(page, visited)
    if bank == "cathay" and "cathay_twd_overview" not in visited and is_bank_url(page.url, bank):
        overview = page.locator('button[data-evt="home_twd_overview"]:visible')
        if overview.count() == 1:
            visited.add("cathay_twd_overview")
            overview.click(timeout=5000)
            return True
    for label in NAVIGATION_LABELS:
        if label in visited:
            continue
        matches = []
        for frame in page.frames:
            if not is_bank_url(frame.url, bank):
                continue
            links = frame.locator("a, button").filter(has_text=re.compile(r"^\s*" + re.escape(label) + r"\s*$"))
            matches.extend(links.nth(index) for index in range(links.count()) if links.nth(index).is_visible())
        if len(matches) == 1:
            visited.add(label)
            matches[0].click(timeout=5000)
            return True
    return False


def prepare_entry(page, bank):
    if bank == "ubot" and is_bank_url(page.url, bank):
        # The homepage includes an off-screen form. Opening the drawer also
        # requests its CAPTCHA; filling that hidden form leaves the image blank.
        entry = page.get_by_role("button", name="網銀登入", exact=True)
        entry.wait_for(state="visible", timeout=15000)
        if entry.count() == 1:
            entry.click(timeout=5000)
    if bank == "fubon":
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            for frame in page.frames:
                if not is_bank_url(frame.url, bank):
                    continue
                if "Login.faces" in frame.url and "PreLogin.faces" not in frame.url:
                    return
                entry = frame.locator('a[id="form1:login_btn"]:visible')
                if entry.count() == 1:
                    entry.click(timeout=5000)
                    return
            page.wait_for_timeout(250)


def confirm_existing_login(page, bank, confirmed):
    if bank != "fubon" or bank in confirmed or not is_bank_url(page.url, bank):
        return False
    for frame in page.frames:
        if not is_bank_url(frame.url, bank):
            continue
        notice = frame.get_by_text("確認是否繼續登入,將幫您登出其他已登入裝置(多元版網路銀行)。", exact=True)
        confirm = frame.locator('input[id="form1:confirmBtn"]:visible')
        if notice.count() == 1 and notice.is_visible() and confirm.count() == 1:
            confirmed.add(bank)
            print("[台北富邦] 已確認本次登入，結束先前未正常登出的登入狀態。", flush=True)
            confirm.click(timeout=5000)
            return True
    return False


def dismiss_balance_notice(page, bank):
    if not is_bank_url(page.url, bank):
        return False
    if bank == "feib":
        notice = page.locator('.fancybox-wrap:visible').filter(has=page.locator('#lightbox-b #projectTable'))
        if notice.count() == 1 and "請確認您留存於本行的OTP收訊電話" in notice.inner_text(timeout=1000):
            cancel = notice.locator('a[onclick="javascript:cheatLimitAmtDecision(\'N\');return false;"]:visible')
            if cancel.count() == 1:
                cancel.click(timeout=5000)
                return True
    if bank == "first_bank":
        cancel = page.locator('a.btn-cancel[href="#confirmMsg"]:visible')
        if cancel.count() == 1 and "因您尚未辦理資料更新" in page.locator("body").inner_text(timeout=1000):
            cancel.click(timeout=5000)
            return True
    return False


def _query_balance(bank, balance_selector=None, *, verification_timeout=120):
    name, url, _ = AUTO_BANKS[bank]
    is_official = lambda location: is_bank_url(location, bank)
    with sync_playwright() as playwright:
        with diagnostic_chrome_context(playwright, bank, is_official, system_chrome_context) as context:
            page = context.new_page()
            alerts = []
            def on_dialog(dialog):
                handle_bank_dialog(dialog, bank, is_official, alerts)
            page.on("dialog", on_dialog)
            context.on("page", lambda opened: opened.on("dialog", on_dialog))
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            prepare_entry(page, bank)
            # SPA and frame-based banks need time to build their verified form.
            deadline = time.monotonic() + 20
            while True:
                try:
                    locate_form(page, bank, is_official)
                    break
                except BankQueryError as error:
                    if error.code != "login_form_unavailable" or time.monotonic() >= deadline:
                        raise
                    page.wait_for_timeout(250)
            submitted = login_once(page, bank, is_official, verification_timeout)
            if submitted:
                print(f"[{name}] 已送出一次登入；如有 OTP，請在 Chrome 完成，餘額會自動讀取。")
            deadline = time.monotonic() + verification_timeout
            logged_in = False
            visited = set()
            confirmed = set()
            recovered_session = False
            hncb_nickname_stage = False
            last_error = None
            while time.monotonic() < deadline:
                pages = [candidate for candidate in context.pages if is_official(candidate.url) and not candidate.is_closed()]
                if not pages:
                    raise BankQueryError("網銀視窗已關閉，查詢已取消", "browser_closed")
                page = pages[-1]
                if confirm_existing_login(page, bank, confirmed):
                    page.wait_for_timeout(250)
                    continue
                if alerts:
                    text = alerts.pop()
                    if is_hncb_nickname_prompt(bank, text):
                        if hncb_nickname_stage:
                            raise BankQueryError("華南第二階段仍要求輸入代號；已停止，不會持續重送登入", "login_unconfirmed")
                        hncb_nickname_stage = True
                        frame, _, _ = locate_form(page, bank, is_official)
                        spec = CAPTCHA_IMAGES[bank]
                        captcha = frame.locator(LOGIN_FORMS[bank].captcha + ":visible")
                        refresh = frame.locator(spec.refresh + ":visible")
                        if captcha.count() != 1 or refresh.count() != 1:
                            raise BankQueryError("華南第二階段登入表單或驗證碼更新按鈕不唯一；未重送登入", "login_form_changed")
                        captcha.fill("", timeout=5000)
                        refresh.click(timeout=5000)
                        print("[華南銀行] 銀行要求第二階段輸入代號；更新驗證碼後只再登入一次。", flush=True)
                        login_once(page, bank, is_official, verification_timeout)
                        deadline = time.monotonic() + verification_timeout
                        continue
                    if re.search(r"(?:圖形)?驗證碼.{0,12}(?:錯誤|不符|不正確|無效)", text):
                        raise BankQueryError("銀行回覆圖形驗證碼錯誤；已停止，不會自動重送登入", "captcha_rejected")
                    if re.search(r"密碼.*(?:錯誤|不符|不正確|無效)|登入失敗|使用者.*(?:錯誤|不符|無效)|帳號.*(?:錯誤|不符|無效)", text):
                        raise BankQueryError("銀行回覆登入資料不符；已停止，不會自動重試", "credentials_rejected")
                check_login_error(page, is_official)
                check_customer_information_update(page, bank)
                if bank == "shanghai" and is_bank_url(page.url, bank):
                    unavailable = page.get_by_text("交易暫停、禁止", exact=True)
                    if unavailable.count() == 1 and unavailable.is_visible():
                        raise BankQueryError("上海商銀回覆「交易暫停、禁止」；本次未取得餘額", "bank_query_unavailable")
                if bank == "taishin":
                    for frame in page.frames:
                        if not is_official(frame.url):
                            continue
                        restart = frame.locator("#reloginbtn:visible")
                        message = "您上次使用網路銀行未正常登出，重新登入後即可繼續使用。"
                        if restart.count() == 1 and message in frame.locator("body").inner_text(timeout=1000):
                            if recovered_session:
                                raise BankQueryError("台新仍要求重新登入；已停止，不會持續重送帳密", "previous_session_conflict")
                            recovered_session = True
                            print("[台新銀行] 銀行要求重建前次連線；只處理一次重新登入。", flush=True)
                            image_selector = CAPTCHA_IMAGES[bank].image
                            previous_image = frame.locator(image_selector).get_attribute("src")
                            with frame.expect_navigation(wait_until="domcontentloaded", timeout=15000):
                                restart.click(timeout=5000)
                            for selector in LOGIN_FORMS[bank].fields:
                                frame.locator(selector).wait_for(state="visible", timeout=15000)
                            frame.wait_for_function("""args => {
                                const image=document.querySelector(args.selector);
                                return image && image.complete && image.naturalWidth>0 &&
                                    image.getAttribute('src') && image.getAttribute('src')!==args.previous;
                            }""", arg={"selector": image_selector, "previous": previous_image}, timeout=15000)
                            login_once(page, bank, is_official, verification_timeout)
                            deadline = time.monotonic() + verification_timeout
                            break
                if bank == "ubot" and is_bank_url(page.url, bank):
                    message = page.get_by_text("訊息內容：您目前為信用卡會員，若要使用此功能請先執行存戶申請。", exact=True)
                    if message.count() == 1 and message.is_visible():
                        raise BankQueryError("聯邦銀行回覆目前為信用卡會員，尚未開通存款查詢功能", "deposit_access_unavailable")
                    if submitted and urlparse(page.url).fragment == "/I1201001" and "ubot_balance" not in visited:
                        click_balance_navigation(page, bank, visited)
                        page.wait_for_timeout(250)
                        continue
                if has_logged_in(page, is_official):
                    logged_in = True
                if logged_in:
                    if dismiss_balance_notice(page, bank):
                        page.wait_for_timeout(250)
                        continue
                    if bank == "chb" and "chb_twd_query" not in visited:
                        click_balance_navigation(page, bank, visited)
                        page.wait_for_timeout(250)
                        continue
                    try:
                        return read_automatic_balance(page, bank, balance_selector)
                    except (ValueError, PlaywrightError) as error:
                        last_error = error
                        if len(visited) < 3:
                            click_balance_navigation(page, bank, visited)
                page.wait_for_timeout(500)
            if not logged_in:
                code = "verification_required" if has_verification_challenge(page, is_official) else "login_unconfirmed"
                raise BankQueryError("尚未完成登入或銀行驗證，未取得餘額；本次不會重送帳密", code)
            raise BankQueryError(f"已登入但未辨識到唯一存款餘額；請設定該銀行 balance_selector（{last_error}）", "balance_unavailable")


def query_balance(bank, balance_selector=None, *, verification_timeout=120):
    try:
        return _query_balance(bank, balance_selector, verification_timeout=verification_timeout)
    except PlaywrightError as error:
        # Playwright action logs can include the argument passed to fill().
        # Keep credentials out of terminal output and persisted reports.
        sanitized = BankQueryError("網銀頁面操作逾時或已關閉；未取得餘額，不會重送登入", "browser_action_failed")
        sanitized.diagnostics = getattr(error, "diagnostics", [])
        raise sanitized from error
