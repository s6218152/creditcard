from abc import ABC, abstractmethod
import re

class BaseParser(ABC):
    supports_details = True

    def validation_errors(self, parsed_data: dict, text: str) -> list[str]:
        return []

    def has_transaction_rows(self, text: str) -> bool:
        return bool(re.search(
            r"(?m)^\s*(?:\d{2}/\d{2}|1\d{2}/\d{2}/\d{2})\s+.+\s+-?[\d,]+\s*$",
            text,
        ))

    @abstractmethod
    def parse(self, text: str) -> dict:
        """
        解析帳單文字內容，提取應繳金額與消費明細。
        
        :param text: PDF 的原始文字內容。
        :returns: 一個 dict，包含:
                  - "total_amount": float 或 int (本期應繳總金額)
                  - "details": list of dicts (消費明細)，每個項目包含:
                    - "date": str (消費日期，如 MM/DD 或 YYYY/MM/DD)
                    - "post_date": str (入帳日期，如 MM/DD 或 YYYY/MM/DD)
                    - "description": str (消費項目描述)
                    - "amount": float 或 int (消費金額)
        """
        pass
