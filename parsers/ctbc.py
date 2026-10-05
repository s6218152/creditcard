import re

from parsers.base import BaseParser
from parsers.general import GeneralParser


class CtbcParser(BaseParser):
    supports_details = False
    """Parser for CTBC credit-card statements.

    CTBC's September 2026 statement template stores Chinese labels in an
    embedded font which common PDF text extractors cannot map back to Unicode.
    The amount and due date are still present in the first-page payment
    summary, in a stable order: due date, then total amount, then minimum due.
    """

    _roc_date_line = re.compile(r"^\s*(1\d{2}/\d{1,2}/\d{1,2})\s*$")
    _amount = re.compile(r"(?<![\d/])(\d{1,3}(?:,\d{3})+|\d+)(?![\d,/%])")

    def parse(self, text: str) -> dict:
        result = {
            "total_amount": 0,
            "total_amount_found": False,
            "details": [],
            "due_date": "",
        }
        lines = text.splitlines()

        for index, line in enumerate(lines):
            match = self._roc_date_line.match(line)
            if not match:
                continue

            # Only the summary date is followed shortly by an amount line.
            # The following minimum-payment value must not be selected.
            for candidate in lines[index + 1:index + 5]:
                # This is the card-limit row (for example "/ 60,000/ 60,000")
                # that sits between the due date and the payable amount.
                if "/" in candidate:
                    continue
                amounts = self._amount.findall(candidate)
                if not amounts:
                    continue
                result["due_date"] = match.group(1)
                result["total_amount"] = self.parse_amount(amounts[0])
                result["total_amount_found"] = True
                return result

        return result

    @staticmethod
    def parse_amount(value: str) -> int:
        return int(value.replace(",", ""))
