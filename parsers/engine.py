from parsers.general import GeneralParser
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
from parsers.fubon import FubonParser
from parsers.chb import ChbParser

# 解析器註冊表，未來若有開發特定銀行解析器可在此擴充
PARSERS_REGISTRY = {
    "general": GeneralParser(),
    "ctbc": CtbcParser(),
    "dbs": DbsParser(),
    "shanghai": ShanghaiParser(),
    "cathay": CathayParser(),
    "first_bank": FirstBankParser(),
    "esun": EsunParser(),
    "sinopac": SinopacParser(),
    "skbank": SkbankParser(),
    "feib": FeibParser(),
    "ubot": UbotParser(),
    "fubon": FubonParser(),
    "chb": ChbParser(),
}

PARSER_ENGINE_VERSION = "21"


def get_parser_for_text(text: str, filename: str = ""):
    """
    根據 PDF 提取的文字內容特徵，自動挑選合適的解析器。
    目前均返回通用解析器，但此處設計方便未來針對特定銀行（如國泰、玉山等）做特定解析邏輯的切換。
    """
    filename = filename.casefold()
    if "ctbc" in filename or "中國信託" in filename or "china trust" in filename:
        return PARSERS_REGISTRY["ctbc"]
    if "cbgcc-dailystmt" in filename or "dbs" in filename or "星展" in filename:
        return PARSERS_REGISTRY["dbs"]
    if "上海商銀" in filename or "shanghai" in filename:
        return PARSERS_REGISTRY["shanghai"]
    if "信用卡電子帳單消費明細" in filename or "國泰世華" in filename or "cathay" in filename:
        return PARSERS_REGISTRY["cathay"]
    if "第一銀行" in filename or "first bank" in filename:
        return PARSERS_REGISTRY["first_bank"]
    if "esun" in filename or "玉山銀行" in filename:
        return PARSERS_REGISTRY["esun"]
    if "永豐" in filename or "sinopac" in filename:
        return PARSERS_REGISTRY["sinopac"]
    if "新光銀行" in filename or "skbank" in filename or "新光銀行" in text:
        return PARSERS_REGISTRY["skbank"]
    if "cycle-statement" in filename or "遠東商銀" in filename or "feib" in filename:
        return PARSERS_REGISTRY["feib"]
    if "ubot_estatement" in filename or "聯邦銀行" in filename or "ubot" in filename:
        return PARSERS_REGISTRY["ubot"]
    if "台北富邦" in filename or "fubon" in filename or "台北富邦" in text:
        return PARSERS_REGISTRY["fubon"]
    if "彰化銀行" in filename or "彰銀" in filename or "chb" in filename:
        return PARSERS_REGISTRY["chb"]

    if not text:
        return PARSERS_REGISTRY["general"]
        
    # 範例：未來若有特定銀行 parser：
    # if "國泰世華" in text:
    #     return PARSERS_REGISTRY.get("cathay", PARSERS_REGISTRY["general"])
    # if "玉山銀行" in text:
    #     return PARSERS_REGISTRY.get("esun", PARSERS_REGISTRY["general"])
        
    return PARSERS_REGISTRY["general"]
