import re

from parsers.base import BaseParser
from parsers.general import GeneralParser


class SkbankParser(BaseParser):
    """Parser for Shin Kong Bank credit-card statements."""

    _due_date = re.compile(r"繳\s*款\s*截\s*止\s*日\s*(1\d{2}/\d{1,2}/\d{1,2})")
    _total = re.compile(r"本\s*期\s*應\s*繳\s*總\s*金\s*額\s*[：:]?\s*([\d,]+)")

    def parse(self, text: str) -> dict:
        generic_result = GeneralParser().parse(text)
        result = {
            "total_amount": generic_result["total_amount"],
            "total_amount_found": generic_result["total_amount_found"],
            "details": generic_result["details"],
            "due_date": generic_result["due_date"],
        }
        due_date = self._due_date.search(text)
        if due_date:
            result["due_date"] = due_date.group(1)
        total = self._total.search(text)
        if total:
            result["total_amount"] = int(total.group(1).replace(",", ""))
            result["total_amount_found"] = True
        return result
