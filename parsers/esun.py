import re

from parsers.base import BaseParser
from parsers.general import GeneralParser


class EsunParser(BaseParser):
    """Parser for E.SUN Bank credit-card statements."""

    _total = re.compile(r"本期應繳總金額[：:]\s*TWD\s*([\d,]+)")
    _summary_date = re.compile(r"(?m)^\s*((?:1\d{2}|20\d{2})/\d{1,2}/\d{1,2})\s*$")

    def parse(self, text: str) -> dict:
        generic_result = GeneralParser().parse(text)
        result = {
            "total_amount": generic_result["total_amount"],
            "total_amount_found": generic_result["total_amount_found"],
            "details": generic_result["details"],
            "due_date": generic_result["due_date"],
        }
        total_match = self._total.search(text)
        if total_match:
            result["total_amount"] = int(total_match.group(1).replace(",", ""))
            result["total_amount_found"] = True

        # The first standalone ROC or Gregorian date in the E.SUN payment summary is the due date.
        due_date = self._summary_date.search(text)
        if due_date:
            result["due_date"] = due_date.group(1)
        return result
