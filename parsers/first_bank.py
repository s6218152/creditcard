import re

from parsers.base import BaseParser
from parsers.general import GeneralParser


class FirstBankParser(BaseParser):
    """Parser for First Bank credit-card statements."""

    _roc_date = re.compile(r"\b1\d{2}/\d{1,2}/\d{1,2}\b")

    def has_transaction_rows(self, text: str) -> bool:
        # The statement always prints the transaction table headers, including
        # periods with no transactions. Only inspect rows inside that table.
        match = re.search(r"起息日\s+消費明細說明.*?(?=您的本期金額總計|-{5,}|\Z)", text, re.DOTALL)
        return bool(match and self._roc_date.search(match.group(0)))

    def parse(self, text: str) -> dict:
        generic_result = GeneralParser().parse(text)
        result = {
            "total_amount": generic_result["total_amount"],
            "total_amount_found": generic_result["total_amount_found"],
            "details": generic_result["details"],
            "due_date": generic_result["due_date"],
        }
        if "無須繳款" in text:
            # The closest date before the no-payment summary is the due date;
            # an earlier date may be the statement period start.
            summary_index = text.index("無須繳款")
            dates_before_summary = list(self._roc_date.finditer(text[:summary_index]))
            due_date = dates_before_summary[-1] if dates_before_summary else None
            result["total_amount"] = 0
            result["total_amount_found"] = True
            result["due_date"] = due_date.group(0) if due_date else "無須繳款"
        return result
