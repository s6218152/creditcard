import re

from parsers.base import BaseParser


class FubonParser(BaseParser):
    """Parser for Taipei Fubon credit-card statements."""

    _total_amount = re.compile(r"本期應繳總額\s*(-?[\d,]+)\s*元")
    _previous_amount = re.compile(r"^前期應繳總額\s+(-?[\d,]+)$", re.MULTILINE)
    _transaction = re.compile(
        r"^(?P<date>1\d{2}/\d{2}/\d{2})\s+"
        r"(?P<description>.+?)\s+"
        r"(?P<post_date>1\d{2}/\d{2}/\d{2})"
        r"(?:\s+TWD)?\s+"
        r"(?P<amount>-?[\d,]+)$"
    )

    @staticmethod
    def _amount(value: str) -> int:
        return int(value.replace(",", ""))

    def parse(self, text: str) -> dict:
        result = {
            "total_amount": 0,
            "total_amount_found": False,
            "details": [],
            "due_date": "",
        }

        total_match = self._total_amount.search(text)
        if total_match:
            result["total_amount"] = self._amount(total_match.group(1))
            result["total_amount_found"] = True

        if "無需繳款" in text:
            result["due_date"] = "無需繳款"
        else:
            due_match = re.search(
                r"繳款截止日.*?\n.*?(1\d{2}/\d{2}/\d{2})",
                text,
                re.DOTALL,
            )
            if due_match:
                result["due_date"] = due_match.group(1)

        for raw_line in text.splitlines():
            match = self._transaction.match(raw_line.strip())
            if not match:
                continue
            result["details"].append(
                {
                    "date": match.group("date"),
                    "post_date": match.group("post_date"),
                    "description": match.group("description").strip(),
                    "amount": self._amount(match.group("amount")),
                }
            )

        return result

    def validation_errors(self, parsed_data: dict, text: str) -> list[str]:
        previous = self._previous_amount.search(text)
        details = parsed_data.get("details", [])
        if not previous or not details or not parsed_data.get("total_amount_found"):
            return []
        expected = self._amount(previous.group(1)) + sum(row["amount"] for row in details)
        if expected != parsed_data["total_amount"]:
            return [
                f"交易加總不符：前期餘額與交易合計為 {expected}，帳單總額為 {parsed_data['total_amount']}"
            ]
        return []
