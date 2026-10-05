import re

from parsers.base import BaseParser
from parsers.general import GeneralParser


class ShanghaiParser(BaseParser):
    """Parser for Shanghai Commercial & Savings Bank credit-card statements."""

    _roc_date = re.compile(r"^\s*(1\d{2}/\d{1,2}/\d{1,2})\s*$")
    _payment_amount = re.compile(r"^\s*\$\s*([\d,]+)\s*$")

    def parse(self, text: str) -> dict:
        generic_result = GeneralParser().parse(text)
        result = {
            "total_amount": 0,
            "total_amount_found": False,
            "details": generic_result["details"],
            "due_date": "",
        }
        lines = text.splitlines()

        # On the payment slip, the due date is followed by cardholder details
        # and then the total and minimum payment amounts.  This avoids the
        # explanatory "0 元" in the statement terms.
        for index, line in enumerate(lines):
            date_match = self._roc_date.match(line)
            if not date_match:
                continue
            for candidate in lines[index + 1:index + 7]:
                amount_match = self._payment_amount.match(candidate)
                if amount_match:
                    result["due_date"] = date_match.group(1)
                    result["total_amount"] = int(amount_match.group(1).replace(",", ""))
                    result["total_amount_found"] = True
                    return result

        return result
