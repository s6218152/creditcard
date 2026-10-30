from datetime import date
from decimal import Decimal
import re

from parsers.base import BaseParser
from parsers.general import GeneralParser


class CathayParser(BaseParser):
    """Parser for Cathay United Bank CUBE credit-card statements."""

    _number = r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
    _total = re.compile(
        r"^\s*本期應繳總(?:金額|額)\s*[:：]?\s*(?:NT\$|新[臺台]幣|\$)?\s*("
        + _number + r")\s*(?:元)?\s*$", re.M)
    _amount_line = re.compile(r"^\s*(?:NT\$|\$)?\s*(" + _number + r")\s*(?:元)?\s*$")
    _date = r"(?:\d{4}|1\d{2})[/-]\d{1,2}[/-]\d{1,2}"
    _due = re.compile(r"(?:繳款截止日|繳款期限|Payment\s+Due\s+Date)\s*[:：]?\s*(" + _date + r")", re.I)
    _date_line = re.compile(r"^\s*(" + _date + r")\s*$")

    @staticmethod
    def _valid_date(value):
        year, month, day = map(int, re.split(r"[/-]", value))
        try:
            return date(year + 1911 if year < 1911 else year, month, day)
        except ValueError:
            return None

    def parse(self, text: str) -> dict:
        generic_result = GeneralParser().parse(text)
        result = {
            "total_amount": 0,
            "total_amount_found": False,
            "details": generic_result["details"],
            "due_date": "",
        }
        # Never use explanatory repayment periods or minimum payments as totals.
        amounts = {Decimal(value.replace(",", "")) for value in self._total.findall(text)}
        if len(amounts) == 1:
            total = next(iter(amounts))
            result["total_amount"] = int(total) if total == total.to_integral_value() else float(total)
            result["total_amount_found"] = True
        if (not any(amount > 0 for amount in amounts)
                and re.search(r"^\s*(?:新[臺台]幣\s*)?無[須需]繳款\s*$", text, re.M)):
            result["total_amount_found"] = True
            result["due_date"] = "無須繳款"
            return result

        candidates = self._due.findall(text)
        lines = text.splitlines()
        # PDF drawing order separates the first-page labels from their values.
        # The payment slip repeats a standalone date beside total/minimum
        # amounts. Bind that date to a block containing the confirmed total.
        for index, line in enumerate(lines):
            if not re.search(r"新[臺台]幣\s*應繳總額", line):
                continue
            block = lines[index + 1:index + 25]
            block_amounts = {Decimal(match.group(1).replace(",", "")) for item in block
                             if (match := self._amount_line.fullmatch(item))}
            if len(amounts) == 1 and amounts.intersection(block_amounts):
                candidates.extend(match.group(1) for item in block
                                  if (match := self._date_line.fullmatch(item)))
        dates = {valid: value for value in candidates if (valid := self._valid_date(value))}
        if len(dates) == 1:
            result["due_date"] = next(iter(dates.values()))
        return result
