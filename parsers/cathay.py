from parsers.base import BaseParser
from parsers.general import GeneralParser


class CathayParser(BaseParser):
    """Parser for Cathay United Bank CUBE credit-card statements."""

    def parse(self, text: str) -> dict:
        generic_result = GeneralParser().parse(text)
        result = {
            "total_amount": generic_result["total_amount"],
            "total_amount_found": generic_result["total_amount_found"],
            "details": generic_result["details"],
            "due_date": generic_result["due_date"],
        }
        # The payment slip explicitly says no payment is required.  This is
        # authoritative over unrelated monetary values elsewhere in the PDF.
        if "無須繳款" in text:
            result["total_amount"] = 0
            result["total_amount_found"] = True
            result["due_date"] = "無須繳款"
        return result
