from types import SimpleNamespace

import pytest

import bank_login
from bank_login import BankQueryError, bank_credentials, dismiss_login_notice, is_logout_href, login_once, locate_form
from manual_bank_balance import is_bank_url


def default_credentials(monkeypatch):
    monkeypatch.setattr(bank_login, "load_dotenv", lambda *args, **kwargs: None)
    for key, value in (("ID", "test-id"), ("USER_ID", "test-user"), ("PASSWORD", "test-password")):
        monkeypatch.setenv("BANK_" + key, value)
        monkeypatch.delenv("ESUN_BANK_" + key, raising=False)


def test_hncb_login_help_link_is_not_a_logout_control():
    assert not is_logout_href("http://www.hncb.com.tw/ibankqa/LoginLogout.shtml")
    assert is_logout_href("/netbank/logout")


def test_cathay_login_announcements_are_acknowledged_before_submit():
    state = {"remaining": 2, "clicks": 0}
    button = SimpleNamespace(
        count=lambda: 1,
        is_visible=lambda: True,
        click=lambda **kwargs: state.update(remaining=state["remaining"] - 1, clicks=state["clicks"] + 1),
    )
    notice = SimpleNamespace(
        count=lambda: int(state["remaining"] > 0),
        get_by_text=lambda *args, **kwargs: button,
        wait_for=lambda **kwargs: None,
    )
    frame = SimpleNamespace(
        url="https://www.cathaybk.com.tw/",
        locator=lambda selector: notice,
        wait_for_timeout=lambda milliseconds: None,
    )
    page = SimpleNamespace(url=frame.url)
    dismiss_login_notice(page, frame, "cathay", lambda url: is_bank_url(url, "cathay"))
    assert state == {"remaining": 0, "clicks": 2}


def test_cathay_advances_announcement_before_acknowledging():
    state = {"step": 0, "clicks": []}
    def button(text):
        expected = "下一則" if state["step"] == 0 else "我知道了"
        return SimpleNamespace(count=lambda: int(text == expected), is_visible=lambda: True,
            click=lambda **kwargs: (state["clicks"].append(text), state.update(step=state["step"] + 1)))
    notice = SimpleNamespace(count=lambda: int(state["step"] < 2), get_by_text=lambda text, **kw: button(text))
    frame = SimpleNamespace(url="https://www.cathaybk.com.tw/MyBank/Home", locator=lambda selector: notice,
                            wait_for_timeout=lambda ms: None)
    dismiss_login_notice(SimpleNamespace(url=frame.url), frame, "cathay", lambda url: True)
    assert state["clicks"] == ["下一則", "我知道了"]


def test_bank_password_override_does_not_change_default_or_ctbc(monkeypatch):
    default_credentials(monkeypatch)
    monkeypatch.setenv("ESUN_BANK_PASSWORD", "esun-only")
    monkeypatch.setenv("CTBC_PASSWORD", "ctbc-only")
    assert bank_credentials("esun") == ("test-id", "test-user", "esun-only")
    assert bank_credentials("cathay") == ("test-id", "test-user", "test-password")
    assert bank_login.os.getenv("CTBC_PASSWORD") == "ctbc-only"


def test_captcha_form_is_filled_but_not_submitted(monkeypatch):
    default_credentials(monkeypatch)
    monkeypatch.setattr(bank_login, "try_fill_captcha", lambda *args: False)
    monkeypatch.setattr(bank_login, "dismiss_login_notice", lambda *args, **kwargs: None)
    fills, clicks = [], []
    field = lambda: SimpleNamespace(fill=lambda value, **kwargs: fills.append(value))
    frame = SimpleNamespace(url="https://www.chb.com.tw/", locator=lambda selector: SimpleNamespace(count=lambda: 1))
    page = SimpleNamespace(url=frame.url)
    submit = SimpleNamespace(click=lambda **kwargs: clicks.append(True))
    monkeypatch.setattr(bank_login, "locate_form", lambda *args: (frame, [field(), field(), field()], submit))
    with pytest.raises(BankQueryError) as error:
        login_once(page, "chb", lambda url: is_bank_url(url, "chb"))
    assert error.value.code == "captcha_recognition_failed"
    assert fills == ["test-id", "test-user", "test-password"]
    assert not clicks


def test_failed_submit_is_not_retried(monkeypatch):
    default_credentials(monkeypatch)
    monkeypatch.setattr(bank_login, "dismiss_login_notice", lambda *args, **kwargs: None)
    from playwright.sync_api import Error
    clicks = []
    def click(**kwargs):
        clicks.append(True)
        raise Error("button unavailable")
    frame = SimpleNamespace(url="https://www.cathaybk.com.tw/")
    page = SimpleNamespace(url=frame.url)
    field = SimpleNamespace(fill=lambda *args, **kwargs: None)
    monkeypatch.setattr(bank_login, "locate_form", lambda *args: (frame, [field, field, field], SimpleNamespace(click=click)))
    with pytest.raises(Error):
        login_once(page, "cathay", lambda url: is_bank_url(url, "cathay"))
    assert clicks == [True]


def test_captcha_failure_reason_is_preserved_and_login_is_not_submitted(monkeypatch):
    default_credentials(monkeypatch)
    field = SimpleNamespace(fill=lambda *a, **kw: None)
    frame = SimpleNamespace(url="https://www.chb.com.tw/", locator=lambda s: SimpleNamespace(count=lambda: 1))
    clicks = []
    submit = SimpleNamespace(click=lambda **kw: clicks.append(True))
    monkeypatch.setattr(bank_login, "locate_form", lambda *a: (frame, [field]*3, submit))
    monkeypatch.setattr(bank_login, "dismiss_login_notice", lambda *a, **kw: None)
    def fail(*a):
        raise bank_login.CaptchaError("OCR 程序異常結束", "captcha_ocr_unavailable")
    monkeypatch.setattr(bank_login, "try_fill_captcha", fail)
    with pytest.raises(BankQueryError) as error:
        login_once(SimpleNamespace(url=frame.url), "chb", lambda url: True)
    assert error.value.code == "captcha_ocr_unavailable"
    assert "OCR 程序異常結束" in str(error.value)
    assert clicks == []


def test_untrusted_login_url_never_looks_for_fields():
    page = SimpleNamespace(url="https://esunbank.com.tw.evil.example/", frames=[])
    with pytest.raises(BankQueryError) as error:
        locate_form(page, "esun", lambda url: is_bank_url(url, "esun"))
    assert error.value.code == "untrusted_url"


def test_dbs_uses_two_fields_without_sending_national_id(monkeypatch):
    default_credentials(monkeypatch)
    fills, clicks = [], []
    frame = SimpleNamespace(url="https://internet-banking.dbs.com.tw/")
    page = SimpleNamespace(url=frame.url)
    field = SimpleNamespace(fill=lambda value, **kwargs: fills.append(value))
    state = {"masked": ""}
    def fill(value, **kwargs):
        # Model the bank's change handler: one event takes only the last char.
        state["masked"] = "•" if value else ""
        fills.append(value)
    def type_password(value, **kwargs):
        state["masked"] += "•" * len(value)
    password = SimpleNamespace(fill=fill, press_sequentially=type_password,
                               press=lambda *a, **kw: None,
                               input_value=lambda **kw: state["masked"])
    monkeypatch.setattr(bank_login, "locate_form", lambda *args: (frame, [None, field, password], SimpleNamespace(click=lambda **kwargs: clicks.append(True))))
    assert login_once(page, "dbs", lambda url: is_bank_url(url, "dbs"))
    assert fills == ["test-user", ""]
    assert state["masked"] == "•" * len("test-password")
    assert clicks == [True]


def test_dbs_incomplete_masked_password_never_submits(monkeypatch):
    default_credentials(monkeypatch)
    frame = SimpleNamespace(url="https://internet-banking.dbs.com.tw/")
    page = SimpleNamespace(url=frame.url)
    field = SimpleNamespace(fill=lambda *a, **kw: None)
    password = SimpleNamespace(fill=lambda *a, **kw: None,
                               press_sequentially=lambda *a, **kw: None,
                               press=lambda *a, **kw: None,
                               input_value=lambda **kw: "•")
    clicks = []
    monkeypatch.setattr(bank_login, "locate_form", lambda *args: (frame, [None, field, password], SimpleNamespace(click=lambda **kw: clicks.append(True))))
    with pytest.raises(BankQueryError) as error:
        login_once(page, "dbs", lambda url: is_bank_url(url, "dbs"))
    assert error.value.code == "login_form_changed"
    assert clicks == []


def test_dbs_credentials_do_not_require_or_read_identity(monkeypatch):
    monkeypatch.setattr(bank_login, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.delenv("BANK_ID", raising=False)
    monkeypatch.delenv("DBS_BANK_ID", raising=False)
    monkeypatch.setenv("DBS_BANK_USER_ID", "dbs-user")
    monkeypatch.setenv("DBS_BANK_PASSWORD", "dbs-password")
    assert bank_credentials("dbs") == ("", "dbs-user", "dbs-password")


@pytest.mark.parametrize("coords", ["10,20,60,50", "10,20,500,50", "unexpected"])
def test_first_bank_clicks_only_valid_official_image_map(monkeypatch, coords):
    default_credentials(monkeypatch)
    clicks = []
    captcha = SimpleNamespace(count=lambda: 1)
    area = SimpleNamespace(count=lambda: 1, get_attribute=lambda name: coords)
    frame = SimpleNamespace(url="https://ibank.firstbank.com.tw/NetBank/", locator=lambda selector:
                            area if selector.startswith("map[") else captcha)
    page = SimpleNamespace(url=frame.url)
    field = SimpleNamespace(fill=lambda *a, **kw: None)
    submit = SimpleNamespace(bounding_box=lambda: {"width": 290, "height": 90},
                             click=lambda **kw: clicks.append(kw["position"]))
    monkeypatch.setattr(bank_login, "locate_form", lambda *a: (frame, [field]*3, submit))
    monkeypatch.setattr(bank_login, "try_fill_captcha", lambda *a: True)
    if coords == "10,20,60,50":
        assert login_once(page, "first_bank", lambda url: is_bank_url(url, "first_bank"))
        assert clicks == [{"x": 35, "y": 35}]
    else:
        with pytest.raises(BankQueryError) as error:
            login_once(page, "first_bank", lambda url: is_bank_url(url, "first_bank"))
        assert error.value.code == "login_form_unavailable"
        assert not clicks


@pytest.mark.parametrize("visible", [True, False])
def test_dbs_invalid_credentials_status_is_reported_only_when_visible(visible):
    message = "您輸入的帳號或密碼無效，請重新嘗試。如果您忘記了登入資訊，請點擊忘記帳號或密碼。"
    dialog = SimpleNamespace(is_visible=lambda: visible, inner_text=lambda **kwargs: message)
    def locator(selector):
        assert '[class*="Pure__StatusBar-"]' in selector
        return SimpleNamespace(count=lambda: 1, nth=lambda i: dialog)
    frame = SimpleNamespace(url="https://internet-banking.dbs.com.tw/digitw/login", locator=locator)
    page = SimpleNamespace(frames=[frame])
    if visible:
        with pytest.raises(BankQueryError) as error:
            bank_login.check_login_error(page, lambda url: is_bank_url(url, "dbs"))
        assert error.value.code == "credentials_rejected"
    else:
        bank_login.check_login_error(page, lambda url: is_bank_url(url, "dbs"))


def test_recognized_captcha_submits_credentials_only_once(monkeypatch):
    default_credentials(monkeypatch)
    fills, clicks = [], []
    field = SimpleNamespace(fill=lambda value, **kwargs: fills.append(value))
    frame = SimpleNamespace(url="https://www.chb.com.tw/", locator=lambda selector: SimpleNamespace(count=lambda: 1))
    page = SimpleNamespace(url=frame.url)
    submit = SimpleNamespace(click=lambda **kwargs: clicks.append(True))
    monkeypatch.setattr(bank_login, "locate_form", lambda *args: (frame, [field, field, field], submit))
    monkeypatch.setattr(bank_login, "dismiss_login_notice", lambda *args, **kwargs: None)
    monkeypatch.setattr(bank_login, "try_fill_captcha", lambda *args: True)
    assert login_once(page, "chb", lambda url: is_bank_url(url, "chb"))
    assert fills == ["test-id", "test-user", "test-password"]
    assert clicks == [True]


@pytest.mark.parametrize("announcement", [True, False])
def test_only_verified_bank_announcement_is_closed(announcement):
    clicks, waits = [], []
    close = SimpleNamespace(count=lambda: 1, click=lambda **kwargs: clicks.append(True))
    notice = SimpleNamespace(count=lambda: 1,
                             get_by_text=lambda *args, **kwargs: SimpleNamespace(count=lambda: int(announcement)),
                             locator=lambda selector: close,
                             wait_for=lambda **kwargs: waits.append(kwargs["state"]))
    frame = SimpleNamespace(url="https://www.chb.com.tw/", locator=lambda selector: notice)
    bank_login.dismiss_login_notice(SimpleNamespace(url=frame.url), frame, "chb", lambda url: is_bank_url(url, "chb"))
    assert clicks == ([True] if announcement else [])
    assert waits == (["hidden"] if announcement else [])


@pytest.mark.parametrize("bank, official, kind, expected", [
    ("sinopac", True, "confirm", "accept"),
    ("sinopac", False, "confirm", "dismiss"),
    ("sinopac", True, "alert", "dismiss"),
    ("chb", True, "confirm", "dismiss"),
])
def test_only_official_sinopac_duplicate_login_confirmation_is_accepted(bank, official, kind, expected):
    actions, alerts = [], []
    dialog=SimpleNamespace(type=kind,page=SimpleNamespace(url="https://mma.sinopac.com/"),
                           message="您可能重複登入，如確定登入，系統將強制關閉他處登入狀態",
                           accept=lambda: actions.append("accept"),dismiss=lambda: actions.append("dismiss"))
    bank_login.handle_bank_dialog(dialog,bank,lambda url:official,alerts)
    assert actions == [expected]
    assert alerts == ([] if expected == "accept" else [dialog.message])


@pytest.mark.parametrize("bank, official, kind, expected", [
    ("first_bank", True, "confirm", "accept"),
    ("first_bank", False, "confirm", "dismiss"),
    ("first_bank", True, "alert", "dismiss"),
    ("sinopac", True, "confirm", "dismiss"),
])
def test_first_bank_duplicate_login_confirmation_is_scoped(bank, official, kind, expected):
    actions, alerts = [], []
    dialog = SimpleNamespace(type=kind, page=SimpleNamespace(url="https://ibank.firstbank.com.tw/"),
        message="本次為重複登入或前次未能正常登出， 按下【確定】，將自動關閉前次連線， 並正常登入系統。",
        accept=lambda: actions.append("accept"), dismiss=lambda: actions.append("dismiss"))
    bank_login.handle_bank_dialog(dialog, bank, lambda url: official, alerts)
    assert actions == [expected]
    assert alerts == ([] if expected == "accept" else [dialog.message])


def test_taishin_repairs_fields_cleared_during_captcha_before_submitting(monkeypatch):
    default_credentials(monkeypatch)
    values, fills, clicks = ["", "", ""], [], []
    def field(index):
        def fill(value, **kw):
            values[index] = value
            fills.append(index)
        return SimpleNamespace(fill=fill, input_value=lambda **kw: values[index])
    fields = [field(i) for i in range(3)]
    frame = SimpleNamespace(url="https://my.taishinbank.com.tw/TIBNetBank/", locator=lambda s: SimpleNamespace(count=lambda: 1))
    def recognize(*args):
        values[0] = ""  # Official form rebuild finishes while OCR is running.
        return True
    monkeypatch.setattr(bank_login, "locate_form", lambda *a: (frame, fields, SimpleNamespace(click=lambda **kw: clicks.append(True))))
    monkeypatch.setattr(bank_login, "try_fill_captcha", recognize)
    assert login_once(SimpleNamespace(url=frame.url), "taishin", lambda url: is_bank_url(url, "taishin"))
    assert fills == [0, 1, 2, 0]
    assert values == ["test-id", "test-user", "test-password"]
    assert clicks == [True]
