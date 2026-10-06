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
from bank_specs import identify_statement

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

PARSER_ENGINE_VERSION = "22"


def get_parser_for_text(text: str, filename: str = ""):
    """
    根據 PDF 提取的文字內容特徵，自動挑選合適的解析器。
    目前均返回通用解析器，但此處設計方便未來針對特定銀行（如國泰、玉山等）做特定解析邏輯的切換。
    """
    spec = identify_statement(filename, text)
    return PARSERS_REGISTRY[spec.parser if spec else "general"]
