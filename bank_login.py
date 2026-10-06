"""Fill verified official bank forms and submit at most once per query."""

import os
from pathlib import Path
import re

from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError
from bank_captcha import CaptchaError, try_fill_captcha
from bank_specs import LOGIN_FORMS


class BankQueryError(ValueError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def bank_credentials(bank):
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    keys = ("USER_ID", "PASSWORD") if bank == "dbs" else ("ID", "USER_ID", "PASSWORD")
    values = tuple(os.getenv(f"{bank.upper()}_BANK_{key}", os.getenv(f"BANK_{key}", ""))
                   for key in keys)
    if not all(values):
        if bank == "dbs":
            raise BankQueryError("請設定 DBS_BANK_USER_ID、DBS_BANK_PASSWORD，或預設的 BANK_USER_ID、BANK_PASSWORD；星展不需要身分證", "missing_credentials")
        raise BankQueryError("請設定 BANK_ID、BANK_USER_ID、BANK_PASSWORD，或該銀行的獨立登入資料", "missing_credentials")
    return ("", *values) if bank == "dbs" else values


def locate_form(page, bank, is_official):
    spec = LOGIN_FORMS[bank]
    if not is_official(page.url):
        raise BankQueryError("登入頁不是該銀行官方 HTTPS 網址，已停止填寫帳密", "untrusted_url")
    for frame in page.frames:
        if not is_official(frame.url):
            continue
        if spec.group:
            group = frame.locator(spec.group + ":visible")
            if group.count() != 3:
                continue
            fields = [group.nth(index) for index in range(3)]
        else:
            fields = [frame.locator(selector + ":visible") if selector else None for selector in spec.fields]
            if any(field is not None and field.count() != 1 for field in fields):
                continue
        submit = frame.locator(spec.submit + ":visible") if spec.submit else None
        if submit is not None and submit.count() != 1:
            continue
        return frame, fields, submit
    raise BankQueryError("未找到已驗證的個人網銀登入表單，未送出帳密", "login_form_unavailable")


def is_logout_href(href):
    value = (href or "").lower()
    return "logout" in value and "loginlogout" not in value


def has_logged_in(page, is_official, *, bank=None):
    try:
        return _has_logged_in(page, is_official, bank=bank)
    except PlaywrightError as error:
        if not is_frame_transition_error(error):
            raise
        return False


def _has_logged_in(page, is_official, *, bank=None):
    if not is_official(page.url):
        return False
    for frame in page.frames:
        if not is_official(frame.url):
            continue
        if bank == "esun":
            # The September 2026 dashboard hides logout inside its menu.
            # Require both an active-session countdown and the bank's TWD
            # deposit widget; public card/balance examples are insufficient.
            timers = frame.get_by_text(re.compile(r"秒後自動登出[，,]\s*點擊重新計時"))
            widgets = frame.locator(".widget-balance-detail-container:visible").filter(
                has=frame.get_by_text("臺幣存款總覽", exact=True))
            if widgets.count() == 1 and any(timers.nth(i).is_visible() for i in range(timers.count())):
                return True
        matches = frame.get_by_text(re.compile(r"^\s*(?:登出|會員登出|登出網路銀行|安全登出|簽出|Logout|Log out|Sign out)\s*$", re.I))
        if any(matches.nth(i).is_visible() for i in range(matches.count())):
            return True
        controls = frame.locator('a[href*="logout" i], a[onclick*="logout" i], button[id*="logout" i], a[id*="logout" i], [aria-label="登出"], [title="登出"], input[type="button"][value="登出"]')
        for index in range(controls.count()):
            control = controls.nth(index)
            href = control.get_attribute("href") or ""
            if "logout" in href.lower() and not is_logout_href(href):
                continue
            if control.is_visible():
                return True
    return False


def is_frame_transition_error(error):
    return any(message in str(error).lower() for message in (
        "frame was detached", "frame has been detached", "execution context was destroyed",
        "cannot find context with specified id"))


def check_login_error(page, is_official):
    try:
        return _check_login_error(page, is_official)
    except PlaywrightError as error:
        if not is_frame_transition_error(error):
            raise
        # A replaced iframe will be checked in the next read-only poll.


def _check_login_error(page, is_official):
    for frame in page.frames:
        if not is_official(frame.url):
            continue
        dialogs = frame.locator('[role="alert"], [role="dialog"], .error-message, .alert-danger, .errorMsg, .login-error, .swal2-popup, .ui-dialog, .modal.show, .pop-hint, [class*="Pure__StatusBar-"]')
        for index in range(dialogs.count()):
            dialog = dialogs.nth(index)
            if not dialog.is_visible():
                continue
            text = dialog.inner_text(timeout=1000)
            if re.search(r"(?:圖形)?驗證碼.{0,12}(?:錯誤|不符|不正確|無效)", text):
                raise BankQueryError("銀行回覆圖形驗證碼錯誤；已停止，不會自動重送登入", "captcha_rejected")
            if re.search(r"登入失敗|密碼.{0,10}(?:錯誤|不符|不正確|無效)|使用者.{0,10}(?:錯誤|不符|無效)|帳號.{0,10}(?:錯誤|不符|無效)", text):
                raise BankQueryError("銀行回覆登入資料不符；本次只送出一次，請更新該銀行的登入資料", "credentials_rejected")
            if re.search(r"鎖定|停用|暫停使用", text):
                raise BankQueryError("銀行顯示帳號已鎖定或停用，已停止查詢", "account_locked")


def dismiss_login_notice(page, frame, bank, is_official, *, wait_for_notice=False):
    if not is_official(page.url) or not is_official(frame.url):
        return
    if bank == "cathay":
        notice = frame.locator('#divSystemLoginMsgList[role="dialog"]:visible')
        if wait_for_notice and notice.count() == 0:
            try:
                notice.wait_for(state="visible", timeout=3000)
            except PlaywrightError:
                return
        for _ in range(10):
            if notice.count() == 0:
                return
            acknowledge = notice.get_by_text("我知道了", exact=True)
            if acknowledge.count() != 1 or not acknowledge.is_visible():
                # Multi-page announcements expose the acknowledgement only
                # after the final page. Stay inside the verified notice.
                acknowledge = notice.get_by_text("下一則", exact=True)
                if acknowledge.count() != 1 or not acknowledge.is_visible():
                    raise BankQueryError("國泰登入前公告沒有唯一的下一則或確認按鈕；未送出登入", "login_form_changed")
            acknowledge.click(timeout=5000)
            frame.wait_for_timeout(250)
        if notice.count():
            raise BankQueryError("國泰登入前公告超過安全處理上限；未送出登入", "login_form_changed")
        return
    if bank != "chb":
        return
    notice = frame.locator(".mfp-wrap:visible")
    if notice.count() != 1 or notice.get_by_text("銀行公告", exact=True).count() != 1:
        return
    close = notice.locator("button.mfp-close:visible")
    if close.count() == 1:
        close.click(timeout=5000)
        notice.wait_for(state="hidden", timeout=5000)


def handle_bank_dialog(dialog, bank, is_official, alerts):
    known_notice = (
        bank == "sinopac" and "重複登入" in dialog.message and "關閉他處登入狀態" in dialog.message
    ) or (
        bank == "first_bank" and "本次為重複登入或前次未能正常登出" in dialog.message and
        "將自動關閉前次連線" in dialog.message and "正常登入系統" in dialog.message
    )
    if known_notice and dialog.type == "confirm" and is_official(dialog.page.url):
        print(f"[{bank}] 已確認本次登入，結束先前未正常登出的登入狀態。", flush=True)
        dialog.accept()
        return
    alerts.append(dialog.message)
    dialog.dismiss()


def login_once(page, bank, is_official, verification_timeout=120):
    spec = LOGIN_FORMS[bank]
    values = bank_credentials(bank)
    frame, fields, submit = locate_form(page, bank, is_official)
    # Cathay injects its pre-login announcement asynchronously after the form
    # is already usable, so allow one bounded wait before entering credentials.
    dismiss_login_notice(page, frame, bank, is_official, wait_for_notice=(bank == "cathay"))
    for index, (field, value) in enumerate(zip(fields, values)):
        if field is None:
            continue
        if not is_official(page.url) or not is_official(frame.url):
            raise BankQueryError("登入頁已轉往非官方網址，已停止填寫帳密", "untrusted_url")
        if bank == "dbs" and index == 2:
            # DBS keeps a custom masked password. Its change handler appends
            # only the final character of each input event, so fill(value)
            # silently submits just the last character of a whole password.
            field.fill("", timeout=10000)
            field.press_sequentially(value, delay=80, timeout=10000)
            field.press("Tab", timeout=5000)
            if field.input_value(timeout=1000) != "•" * len(value):
                raise BankQueryError("星展密碼欄位未完整接收逐字輸入；未送出登入", "login_form_changed")
        else:
            field.fill(value, timeout=10000)
    if spec.captcha:
        captcha = frame.locator(spec.captcha + ":visible")
        try:
            filled = captcha.count() == 1 and try_fill_captcha(page, frame, bank, captcha, is_official)
        except CaptchaError as error:
            raise BankQueryError(f"{error}；未送出登入，已停止本行查詢", error.code) from error
        if not filled:
            raise BankQueryError("驗證碼圖片未載入、格式不符，或三次辨識未達可信門檻；未送出登入，已停止本行查詢", "captcha_recognition_failed")
        print(f"[{bank}] 已在本機辨識，並確認圖形驗證碼欄位已填入。")
    if bank == "taishin" and any(not field.input_value(timeout=1000) for field in fields if field is not None):
        # The bank rebuilds its form after its previous-session notice. The
        # rebuild can finish while OCR runs, clearing an earlier filled field.
        # Repair empty fields before the first submit, not after a rejection.
        for field, value in zip(fields, values):
            if not is_official(page.url) or not is_official(frame.url):
                raise BankQueryError("登入頁已跳轉，未送出登入", "untrusted_url")
            if field is not None and not field.input_value(timeout=1000):
                field.fill(value, timeout=10000)
        if any(not field.input_value(timeout=1000) for field in fields if field is not None):
            raise BankQueryError("台新重建表單後仍有空欄位；未送出登入", "login_form_changed")
    if not is_official(page.url) or not is_official(frame.url):
        raise BankQueryError("登入頁已跳轉至非官方網址，未送出登入", "untrusted_url")
    if submit is None:
        raise BankQueryError("未找到已驗證的登入按鈕，未送出帳密", "login_form_unavailable")
    # The scheduled-maintenance announcement may arrive while OCR is running.
    dismiss_login_notice(page, frame, bank, is_official)
    # Never retry this click: an error can occur after the bank receives it.
    if bank == "first_bank":
        area = frame.locator('map[name="loginMap"] area[href="javascript:clickArea();"]')
        coords = (area.get_attribute("coords") or "") if area.count() == 1 else ""
        if not re.fullmatch(r"\d+,\d+,\d+,\d+", coords):
            raise BankQueryError("第一銀行登入圖片未提供唯一有效的按鈕範圍，未送出登入", "login_form_unavailable")
        left, top, right, bottom = map(int, coords.split(","))
        bounds = submit.bounding_box()
        if not bounds or not (0 <= left < right <= bounds["width"] and 0 <= top < bottom <= bounds["height"]):
            raise BankQueryError("第一銀行登入按鈕範圍超出圖片，未送出登入", "login_form_unavailable")
        submit.click(position={"x": (left + right) / 2, "y": (top + bottom) / 2}, timeout=10000)
    else:
        submit.click(timeout=10000)
    return True
