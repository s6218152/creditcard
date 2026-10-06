"""Single source of truth for supported banks and statement identification."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class LoginForm:
    fields: tuple[str | None, ...]
    submit: str | None
    captcha: str | None = None
    group: str | None = None


@dataclass(frozen=True)
class CaptchaSpec:
    image: str
    length: int
    refresh: str
    alphabet: str = "digits"
    tiles: int = 1
    background: bool = False
    check_maxlength: bool = True
    tile_border: int = 0
    tile_gap: int = 0
    rendered_image: bool = False
    consensus_fallback: bool = False
    all_model_consensus: bool = False
    natural_image: bool = False


@dataclass(frozen=True)
class BankSpec:
    name: str
    entry_url: str
    domains: tuple[str, ...]
    balance_module: str
    aliases: tuple[str, ...] = ()
    login_form: LoginForm | None = None
    captcha: CaptchaSpec | None = None


BANK_SPECS = {
    "ctbc": BankSpec("中國信託", "https://www.ctbcbank.com/twrbc/twrbc-general/ot001/010",
                     ("ctbcbank.com",), "ctbc_balance", ("中信",)),
    "sinopac": BankSpec("永豐銀行", "https://mma.sinopac.com/MemberPortal/Member/MMALogin.aspx",
        ("sinopac.com",), "sinopac_balance", ("永豐",),
        LoginForm((None, None, None), "#MMA_Login", 'input[id$="sino_keyword3"]',
                  'input[id^="ctl00_ctl00_ContentPlaceHolder1_DefaultContent_MMA"]'),
        CaptchaSpec("#imgCode", 6, "#imgCode")),
    "skbank": BankSpec("新光銀行", "https://nbank.skbank.com.tw/", ("skbank.com.tw",),
        "manual_bank_balance", (),
        LoginForm(("#nb_GeneralId", 'input[placeholder^="用戶代號"]', 'input[placeholder^="理財密碼"]'),
                  'a:text-is("一般用戶登入")', 'input[placeholder="輸入右圖文字"]'),
        CaptchaSpec(".verify img", 4, "a.icon__login--refresh", "alnum", tiles=4,
                    tile_border=2, tile_gap=8, consensus_fallback=True)),
    "fubon": BankSpec("台北富邦", "https://accessible.taipeifubon.com.tw/BFS/common/Index.faces",
        ("taipeifubon.com.tw",), "manual_bank_balance", ("富邦",),
        LoginForm((None, None, None), 'input[id="form1:loginBtn"]',
                  'input[id="form1:userCaptcha"]', 'input[type="password"]'),
        CaptchaSpec("#captchaImage", 6, "#regenCaptchaLink")),
    "first_bank": BankSpec("第一銀行", "https://ibank.firstbank.com.tw/NetBank/indexPortlet.html?locale=zh_TW",
        ("firstbank.com.tw",), "manual_bank_balance", (),
        LoginForm(("#loginCustIdFake", "#usrIdInput", "#pwd"), "#captchaLoginArea", "#vrfyCode"),
        CaptchaSpec("#code_verify", 4, 'a[onclick*="chgImg"]', "alnum",
                    consensus_fallback=True, all_model_consensus=True, natural_image=True)),
    "esun": BankSpec("玉山銀行", "https://ebank.esunbank.com.tw/index.jsp?obj=ebank",
        ("esunbank.com.tw", "esunbank.com"), "manual_bank_balance", ("玉山",),
        LoginForm(('input[name="id"]', 'input[name="userName"]', 'input[name="pxssword"]'),
                  "button.btn-main-fill")),
    "feib": BankSpec("遠東商銀", "https://ebank.feib.com.tw/netbank/html/pages/jsp/Logon/mainIndex6.jsp",
        ("feib.com.tw",), "manual_bank_balance", (),
        LoginForm(("#cid0", "#uid0", "#pad0"), "#submitbtn", "#VERCODE0"),
        CaptchaSpec('#vercode0area img[data-role="vercode"]', 6,
                    '#vercode0area button[data-action="resetcode"]')),
    "ubot": BankSpec("聯邦銀行", "https://www.ubot.com.tw/home", ("ubot.com.tw",),
        "manual_bank_balance", (),
        LoginForm(("#sid", "#nickname", "#password"), 'button:text-is("登入")', "#CAPTCHA"),
        CaptchaSpec('img[alt="CAPTCHA"]', 6,
                    '#CAPTCHA + div > div:has(> svg[data-icon="rotate"])', check_maxlength=False)),
    "dbs": BankSpec("星展銀行", "https://internet-banking.dbs.com.tw/", ("dbs.com.tw",),
        "manual_bank_balance", ("星展",),
        LoginForm((None, "#username", "#password"), "#loginbutton")),
    "shanghai": BankSpec("上海商銀", "https://ebank.scsb.com.tw/", ("scsb.com.tw",),
        "manual_bank_balance", ("上海",),
        LoginForm(("#userId", "#idNumber", "#pppd"),
                  'button[type="submit"]:text-matches("^(?:登入|Log in)$", "i")', "#verified"),
        CaptchaSpec(".ved_img", 5, "button.chg_link", background=True)),
    "taishin": BankSpec("台新銀行", "https://my.taishinbank.com.tw/TIBNetBank/",
        ("taishinbank.com.tw",), "manual_bank_balance", (),
        LoginForm(('input[placeholder="身分證字號"]', 'input[placeholder="使用者代號"]',
                   'input[placeholder="使用者密碼"]'), "#loginBtn", 'input[placeholder="驗證碼"]'),
        CaptchaSpec("img._field_item__verify-code", 6, "button.js-btn-refresh")),
    "chb": BankSpec("彰化銀行", "https://www.chb.com.tw/chbnib/faces/login/Login/Login_Action",
        ("chb.com.tw",), "manual_bank_balance", ("彰銀",),
        LoginForm(('input[name="uid_show"]', "#uuid", "#pwd"), "#pb-login", "#captcha"),
        CaptchaSpec("img.cimg", 6, ".captcha a.btn-refresh")),
    "cathay": BankSpec("國泰世華", "https://www.cathaybk.com.tw/MyBank/Home",
        ("cathaybk.com.tw",), "manual_bank_balance", (),
        LoginForm(("#CustID", "#UserIdKeyin", "#PasswordKeyin"), 'button:text-is("登入")')),
    "hncb": BankSpec("華南銀行", "https://netbank.hncb.com.tw/", ("hncb.com.tw",),
        "manual_bank_balance", ("華南",),
        LoginForm(("#USERIDTEXT", "#NICKNAME", "#password"), 'a:text-is("確定登入")', "#TrxCaptchaKey"),
        CaptchaSpec("#code_Cap", 4, 'a[onclick="chgCaptcha();"]', consensus_fallback=True)),
}

LOGIN_FORMS = {key: spec.login_form for key, spec in BANK_SPECS.items() if spec.login_form}
CAPTCHA_IMAGES = {key: spec.captcha for key, spec in BANK_SPECS.items() if spec.captcha}


@dataclass(frozen=True)
class StatementSpec:
    name: str
    parser: str
    filename_markers: tuple[str, ...]
    text_markers: tuple[str, ...] = ()


STATEMENT_SPECS = (
    StatementSpec("中國信託", "ctbc", ("中國信託", "ctbc", "china trust")),
    StatementSpec("第一銀行", "first_bank", ("第一銀行", "first bank")),
    StatementSpec("國泰世華", "cathay", ("國泰世華", "cathay", "信用卡電子帳單消費明細")),
    StatementSpec("玉山銀行", "esun", ("玉山銀行", "esun", "e.sun")),
    StatementSpec("台新銀行", "general", ("台新銀行", "tsb", "taishin")),
    StatementSpec("台北富邦", "fubon", ("台北富邦", "fubon"), ("台北富邦",)),
    StatementSpec("花旗", "general", ("花旗", "citibank")),
    StatementSpec("渣打", "general", ("渣打", "standard chartered")),
    StatementSpec("星展", "dbs", ("星展", "dbs", "cbgcc-dailystmt")),
    StatementSpec("上海商銀", "shanghai", ("上海商銀", "shanghai")),
    StatementSpec("永豐銀行", "sinopac", ("永豐", "sinopac")),
    StatementSpec("新光銀行", "skbank", ("新光銀行", "skbank"), ("新光銀行",)),
    StatementSpec("遠東商銀", "feib", ("遠東商銀", "fareastone", "feib", "cycle-statement")),
    StatementSpec("聯邦銀行", "ubot", ("聯邦銀行", "ubot_estatement", "ubot")),
    StatementSpec("彰化銀行", "chb", ("彰化銀行", "彰銀", "chb")),
)


def identify_statement(filename: str, text: str = "") -> StatementSpec | None:
    folded = filename.casefold()
    for spec in STATEMENT_SPECS:
        if any(marker.casefold() in folded for marker in spec.filename_markers):
            return spec
    for spec in STATEMENT_SPECS:
        if any(marker in text for marker in spec.text_markers):
            return spec
    if re.search(r"信用卡帳單20\d{2}年\d{1,2}月", folded):
        return next(spec for spec in STATEMENT_SPECS if spec.parser == "skbank")
    return None


def validate_bank_specs() -> None:
    for key, spec in BANK_SPECS.items():
        if not spec.name or not spec.entry_url.startswith("https://") or not spec.domains:
            raise RuntimeError(f"銀行規格不完整：{key}")
        if spec.balance_module == "manual_bank_balance" and spec.login_form is None:
            raise RuntimeError(f"自動銀行缺少登入表單：{key}")


validate_bank_specs()
