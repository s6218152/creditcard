import re

from parsers.base import BaseParser
from parsers.general import GeneralParser


class SinopacParser(BaseParser):
    """Parser for Bank SinoPac credit-card statements."""

    _due_date = re.compile(r"繳款截止日\s*(\d{4}/\d{1,2}/\d{1,2})")
    _scheduled_payment = re.compile(r"預定扣款金額\s*([\d,]+)\s*元")

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

        # This amount appears in the first-page payment summary.  Do not use
        # the separate repayment-period illustration later in the statement.
        scheduled_payment = self._scheduled_payment.search(text)
        if scheduled_payment:
            result["total_amount"] = int(scheduled_payment.group(1).replace(",", ""))
            result["total_amount_found"] = True
        return result
