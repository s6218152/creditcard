import re

from parsers.base import BaseParser


class FeibParser(BaseParser):
    supports_details = False
    """Parser for Far Eastern International Bank credit-card statements."""

    _unicode_glyph = re.compile(r"/UNIC([0-9A-Fa-f]{4})")
    _roc_date = re.compile(r"^1\d{2}/\d{1,2}/\d{1,2}$")
    _amount = re.compile(r"^[\d,]+$")

    def parse(self, text: str) -> dict:
        # FEIB embeds digits as Adobe glyph names; restore them before reading
        # the payment-slip sequence.
        decoded = self._unicode_glyph.sub(lambda match: chr(int(match.group(1), 16)), text)
        result = {
            "total_amount": 0,
            "total_amount_found": False,
            "details": [],
            "due_date": "",
        }
        lines = [line.strip() for line in decoded.splitlines()]

        # The payment slip repeats the due date immediately before the total
        # payable amount.  Earlier dates on the page are statement dates.
        for index in range(len(lines) - 1, -1, -1):
            if not self._roc_date.fullmatch(lines[index]):
                continue
            if index + 1 < len(lines) and self._amount.fullmatch(lines[index + 1]):
                result["due_date"] = lines[index]
                result["total_amount"] = int(lines[index + 1].replace(",", ""))
                result["total_amount_found"] = True
                return result
        return result
