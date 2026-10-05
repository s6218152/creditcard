import re

from parsers.base import BaseParser
from parsers.general import GeneralParser


class UbotParser(BaseParser):
    """Parser for Union Bank of Taiwan credit-card statements."""

    _roc_date = re.compile(r"^1\d{2}/\d{1,2}/\d{1,2}$")
    _amount = re.compile(r"^[\d,]+$")

    def parse(self, text: str) -> dict:
        generic_result = GeneralParser().parse(text)
        result = {
            "total_amount": generic_result["total_amount"],
            "total_amount_found": generic_result["total_amount_found"],
            "details": generic_result["details"],
            "due_date": generic_result["due_date"],
        }
        lines = [line.strip() for line in text.splitlines()]
        # The final payment slip repeats the due date, followed by total and
        # minimum payment amounts.  Use the last such sequence.
        for index in range(len(lines) - 1, -1, -1):
            if not self._roc_date.fullmatch(lines[index]):
                continue
            if index + 1 < len(lines) and self._amount.fullmatch(lines[index + 1]):
                result["due_date"] = lines[index]
                result["total_amount"] = int(lines[index + 1].replace(",", ""))
                result["total_amount_found"] = True
                return result
        return result
