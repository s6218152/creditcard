import re

from parsers.base import BaseParser
from parsers.general import GeneralParser


class DbsParser(BaseParser):
    supports_details = False
    """Parser for DBS (CBGCC-DAILYSTMT) credit-card statements.

    The payment slip is the authoritative source.  It prevents an explanatory
    example of a total repayment amount elsewhere in the statement from being
    mistaken for the current balance.
    """

    _due_date = re.compile(r"繳交截止日[：:]\s*(\d{4})年(\d{1,2})月(\d{1,2})日")
    _total_label = re.compile(r"^\s*應繳總金額\s*$")
    _amount = re.compile(r"^\s*\$?\s*([\d,]+)\s*$")

    def parse(self, text: str) -> dict:
        # The generic parser still handles DBS transaction rows well.  Retain
        # those, but never retain its total because it can match explanatory
        # text before reaching the payment slip.
        generic_result = GeneralParser().parse(text)
        result = {
            "total_amount": 0,
            "total_amount_found": False,
            "details": generic_result["details"],
            "due_date": "",
        }
        lines = text.splitlines()

        for line in lines:
            match = self._due_date.search(line)
            if match:
                year, month, day = match.groups()
                result["due_date"] = f"{year}/{int(month):02d}/{int(day):02d}"
                break

        for index, line in enumerate(lines):
            if not self._total_label.match(line):
                continue
            for candidate in lines[index + 1:index + 4]:
                match = self._amount.match(candidate)
                if match:
                    result["total_amount"] = int(match.group(1).replace(",", ""))
                    result["total_amount_found"] = True
                    return result

        return result
