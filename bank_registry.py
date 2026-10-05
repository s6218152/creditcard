"""Official entry points for authenticated deposit-balance queries."""

# key: (display name, official entry URL, allowed DNS suffixes)
MANUAL_BANKS = {
    "skbank": ("新光銀行", "https://nbank.skbank.com.tw/", ("skbank.com.tw",)),
    "fubon": ("台北富邦", "https://accessible.taipeifubon.com.tw/BFS/common/Index.faces", ("taipeifubon.com.tw",)),
    "first_bank": ("第一銀行", "https://ibank.firstbank.com.tw/NetBank/indexPortlet.html?locale=zh_TW", ("firstbank.com.tw",)),
    "esun": ("玉山銀行", "https://ebank.esunbank.com.tw/index.jsp?obj=ebank", ("esunbank.com.tw", "esunbank.com")),
    "feib": ("遠東商銀", "https://ebank.feib.com.tw/netbank/html/pages/jsp/Logon/mainIndex6.jsp", ("feib.com.tw",)),
    "ubot": ("聯邦銀行", "https://www.ubot.com.tw/home", ("ubot.com.tw",)),
    "dbs": ("星展銀行", "https://internet-banking.dbs.com.tw/", ("dbs.com.tw",)),
    "shanghai": ("上海商銀", "https://ebank.scsb.com.tw/", ("scsb.com.tw",)),
    "taishin": ("台新銀行", "https://my.taishinbank.com.tw/TIBNetBank/", ("taishinbank.com.tw",)),
    "chb": ("彰化銀行", "https://www.chb.com.tw/chbnib/faces/login/Login/Login_Action", ("chb.com.tw",)),
    "cathay": ("國泰世華", "https://www.cathaybk.com.tw/MyBank/Home", ("cathaybk.com.tw",)),
    "hncb": ("華南銀行", "https://netbank.hncb.com.tw/", ("hncb.com.tw",)),
}
SUPPORTED_BANKS = {"ctbc", "sinopac", *MANUAL_BANKS}

AUTO_BANKS = {**MANUAL_BANKS, "sinopac": ("永豐銀行", "https://mma.sinopac.com/MemberPortal/Member/MMALogin.aspx", ("sinopac.com",))}
