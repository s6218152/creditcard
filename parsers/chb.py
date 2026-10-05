import re

from parsers.base import BaseParser
from parsers.general import GeneralParser


class ChbParser(BaseParser):
    """Parser for Chang Hwa Bank credit-card statements."""

    _date_summary = re.compile(
        r"結帳日\s+繳款截止日.{0,160}?(\d{4}/\d{2}/\d{2})\s+(\d{4}/\d{2}/\d{2})",
        re.DOTALL,
    )
    _amount_summary = re.compile(
        r"本期應繳總額\s+最低應繳[金金]額[^\n]*\n\s*=\s*([\d,]+)",
    )

    def parse(self, text: str) -> dict:
        generic = GeneralParser().parse(text)
        result = {
            "total_amount": generic["total_amount"],
            "total_amount_found": generic["total_amount_found"],
            "details": generic["details"],
            "due_date": generic["due_date"],
        }
        date = self._date_summary.search(text)
        amount = self._amount_summary.search(text)
        if date:
            result["due_date"] = date.group(2)
        if amount:
            result["total_amount"] = int(amount.group(1).replace(",", ""))
            result["total_amount_found"] = True
        return result
