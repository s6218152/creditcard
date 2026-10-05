import pytest
from parsers.general import GeneralParser
from parsers.fubon import FubonParser
from parsers.ctbc import CtbcParser
from parsers.dbs import DbsParser
from parsers.shanghai import ShanghaiParser
from parsers.cathay import CathayParser
from parsers.first_bank import FirstBankParser
from parsers.esun import EsunParser
from parsers.sinopac import SinopacParser
from parsers.skbank import SkbankParser
from parsers.feib import FeibParser
from parsers.ubot import UbotParser
from parsers.chb import ChbParser

def test_parse_amount():
    parser = GeneralParser()
    assert parser.parse_amount("12,345") == 12345
    assert parser.parse_amount("$1,200.50") == 1200.50
    assert parser.parse_amount("NT$ -500") == -500
    assert parser.parse_amount("abc") == 0

def test_parse_total_amount():
    parser = GeneralParser()
    
    # 測試中文格式一
    text_zh1 = "本期應繳總金額：$15,680\n隨便其他文字"
    assert parser.parse(text_zh1)["total_amount"] == 15680
    
    # 測試中文格式二
    text_zh2 = "應繳金額 5,230\n本期應繳總金額 0"
    # 一般會抓到非 0 的金額或者是第一個匹配項目
    assert parser.parse(text_zh2)["total_amount"] == 5230

    # 測試英文格式
    text_en = "Statement Date: 2026/07/01\nTotal Amount Due: $10,500.50"
    assert parser.parse(text_en)["total_amount"] == 10500.50

def test_parse_total_amount_no_payment_due():
    parser = GeneralParser()
    text = "本期應繳總額 無須繳款\n本期最低應繳金額 NT$ 200,000"
    assert parser.parse(text)["total_amount"] == 0

def test_parse_transaction_details():
    parser = GeneralParser()
    
    text = """
    卡片明細
    消費日 入帳日 消費說明 金額
    07/08 07/09 蝦皮購物-台北市 1,200
    07/10 07/11 麥當勞餐廳 150
    07/12 07/12 國泰世華銀行 100
    客服專線：0800-818-001 (應被過濾)
    說明：此帳款已經入帳
    """
    
    res = parser.parse(text)
    details = res["details"]
    
    # 預期抓到三筆：蝦皮、麥當勞與國泰世華銀行 (客服專線與說明應被過濾或不匹配)
    assert len(details) == 3
    
    assert details[0]["date"] == "07/08"
    assert details[0]["post_date"] == "07/09"
    assert details[0]["description"] == "蝦皮購物-台北市"
    assert details[0]["amount"] == 1200
    
    assert details[1]["date"] == "07/10"
    assert details[1]["post_date"] == "07/11"
    assert details[1]["description"] == "麥當勞餐廳"
    assert details[1]["amount"] == 150

    assert details[2]["date"] == "07/12"
    assert details[2]["post_date"] == "07/12"
    assert details[2]["description"] == "國泰世華銀行"
    assert details[2]["amount"] == 100

def test_parse_transaction_details_single_date():
    parser = GeneralParser()
    
    text = """
    交易明細
    07/08 蘋果在線商店 32,900
    07/09 台灣電力公司 1,850
    說明：此帳款
    """
    
    res = parser.parse(text)
    details = res["details"]
    
    assert len(details) == 2
    
    assert details[0]["date"] == "07/08"
    assert details[0]["post_date"] == ""
    assert details[0]["description"] == "蘋果在線商店"
    assert details[0]["amount"] == 32900
    
    assert details[1]["date"] == "07/09"
    assert details[1]["post_date"] == ""
    assert details[1]["description"] == "台灣電力公司"
    assert details[1]["amount"] == 1850

def test_parse_due_date():
    parser = GeneralParser()
    text = "繳款截止日：2026/08/20\n本期應繳總金額：$5,000"
    assert parser.parse(text)["due_date"] == "2026/08/20"


def test_parse_due_date_with_roc_year():
    parser = GeneralParser()
    text = "繳款截止日 115/10/12\n本期應繳總金額：$3,159"
    assert parser.parse(text)["due_date"] == "115/10/12"


def test_parse_ctbc_payment_summary_with_unreadable_labels():
    # The labels in the CTBC PDF's embedded font are not extractable, but the
    # payment summary preserves this sequence and the numbers.
    text = """
    115/09
    115/10/10
    / 60,000/ 60,000
    ( ) 5,770 7.7%
    1,000 ( )
    """
    result = CtbcParser().parse(text)
    assert result["due_date"] == "115/10/10"
    assert result["total_amount"] == 5770
    assert result["total_amount_found"] is True


def test_parse_dbs_payment_slip_overrides_explanatory_amount():
    text = """
    應繳納總金額為新台幣6,524元
    繳交截止日：2026年09月14日
    應繳總金額
    6,343
    最低應繳金額
    """
    result = DbsParser().parse(text)
    assert result["due_date"] == "2026/09/14"
    assert result["total_amount"] == 6343
    assert result["total_amount_found"] is True


def test_parse_shanghai_payment_slip_overrides_explanatory_zero():
    text = """
    應繳納總金額為新台幣為0元
    115/09/23
    S85541****
    林睿弘
    $68
    $68
    □繳交應繳總額
    """
    result = ShanghaiParser().parse(text)
    assert result["due_date"] == "115/09/23"
    assert result["total_amount"] == 68
    assert result["total_amount_found"] is True


def test_parse_cathay_no_payment_required():
    text = """
    本期應繳總額 0
    無須繳款
    手續費 NT$100
    """
    result = CathayParser().parse(text)
    assert result["due_date"] == "無須繳款"
    assert result["total_amount"] == 0
    assert result["total_amount_found"] is True


def test_parse_first_bank_no_payment_required_with_due_date():
    text = """
    115/09/18
    本期應繳總額 您已辦妥自動扣繳 無須繳款
    應繳總額：-50
    """
    result = FirstBankParser().parse(text)
    assert result["due_date"] == "115/09/18"
    assert result["total_amount"] == 0
    assert result["total_amount_found"] is True


def test_parse_esun_statement_summary_overrides_notice_amount():
    text = """
    紙本帳單當期臺幣應繳總金額為「1,500元（含）以下」
    2,100 元
    115/10/13
    本期應繳總金額： TWD 2,100
    """
    result = EsunParser().parse(text)
    assert result["due_date"] == "115/10/13"
    assert result["total_amount"] == 2100
    assert result["total_amount_found"] is True


def test_parse_sinopac_payment_summary_overrides_repayment_illustration():
    text = """
    您的繳款截止日2026/10/13
    您的預定扣款金額 13,954 元將於2026/10/13透過永豐銀行帳號自動扣款
    應繳納總金額為臺幣14,853元，以上僅供參考
    """
    result = SinopacParser().parse(text)
    assert result["due_date"] == "2026/10/13"
    assert result["total_amount"] == 13954
    assert result["total_amount_found"] is True


def test_parse_skbank_spaced_payment_summary():
    text = """
    ○繳 款 截 止 日 115/09/30
    ○本 期 應 繳 總 金 額 2,000
    """
    result = SkbankParser().parse(text)
    assert result["due_date"] == "115/09/30"
    assert result["total_amount"] == 2000
    assert result["total_amount_found"] is True


def test_parse_feib_payment_slip_with_unicode_glyph_names():
    text = """
    /UNIC0031/UNIC0031/UNIC0035/UNIC002F/UNIC0031/UNIC0030/UNIC002F/UNIC0031/UNIC0033
    /UNIC0035/UNIC002C/UNIC0036/UNIC0030/UNIC0039
    """
    result = FeibParser().parse(text)
    assert result["due_date"] == "115/10/13"
    assert result["total_amount"] == 5609
    assert result["total_amount_found"] is True


def test_parse_ubot_payment_slip():
    text = """
    115/09/21
    40
    40
    □應繳總金額
    """
    result = UbotParser().parse(text)
    assert result["due_date"] == "115/09/21"
    assert result["total_amount"] == 40
    assert result["total_amount_found"] is True


def test_parse_chb_payment_summary_with_legacy_glyphs():
    text = """帳單資訊
結帳日 繳款截止日
請於繳款截止日前一日將款項存入。2026/09/30 2026/10/19
本期應繳總額 最低應繳金額 本期現金回饋 本月循環利率
= 6,437 1,048 0 4.26%
消費明細
09/03 09/04 商店 6,483
"""
    result = ChbParser().parse(text)
    assert result["due_date"] == "2026/10/19"
    assert result["total_amount"] == 6437
    assert result["total_amount_found"] is True
def test_fubon_parser_extracts_negative_balance_and_transactions():
    text = """本期應繳總額 -19,529元
年度累計利息/費用帳單結帳日國內預借現金額度 循環信用利率 帳單分期利率繳款截止日帳單年月 信用額度
42,000 115/09/26 無需繳款 6.46% ---% 0/25
115/09/14 自動扣繳 115/09/15 -28,153
115/09/02 代扣繳Costco續期會員年費 115/09/02 TWD 1,349
115/09/06 好市多新竹店                (退貨) 115/09/07 TWD -1,636
"""

    parser = FubonParser()
    result = parser.parse(text)

    assert result["total_amount"] == -19529
    assert result["total_amount_found"] is True
    assert result["due_date"] == "無需繳款"
    assert result["details"] == [
        {
            "date": "115/09/14",
            "post_date": "115/09/15",
            "description": "自動扣繳",
            "amount": -28153,
        },
        {
            "date": "115/09/02",
            "post_date": "115/09/02",
            "description": "代扣繳Costco續期會員年費",
            "amount": 1349,
        },
        {
            "date": "115/09/06",
            "post_date": "115/09/07",
            "description": "好市多新竹店                (退貨)",
            "amount": -1636,
        },
    ]
    assert parser.validation_errors(result, text) == []
