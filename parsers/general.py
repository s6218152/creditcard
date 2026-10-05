import re
from parsers.base import BaseParser

class GeneralParser(BaseParser):
    def __init__(self):
        # 用於匹配「本期應繳總金額」的正規表示式
        self.amount_patterns = [
            re.compile(r'(?:本期)?(?:應繳總金額|應繳金額|總金額|New\s+Balance|Total\s+Amount\s+Due)[：:\s]*\$?([0-9,.-]+)', re.IGNORECASE),
            re.compile(r'(?:本期)?(?:應繳總金額|應繳金額|總金額|New\s+Balance|Total\s+Amount\s+Due)[^0-9$.]*\$?([0-9,.-]+)', re.IGNORECASE),
            re.compile(r'Total\s+Amount\s+Due[^0-9$.]*\$?([0-9,.-]+)', re.IGNORECASE)
        ]
        self.due_date_patterns = [
            re.compile(r'(?:繳款截止日|應繳截止日|到期日|繳費截止日|付款截止日)[：:\s]*((?:[0-9]{4}|1[0-9]{2})[/-][0-9]{1,2}[/-][0-9]{1,2}|[0-9]{1,2}[/-][0-9]{1,2}|[0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日|[0-9]{1,2}月[0-9]{1,2}日)', re.IGNORECASE),
            re.compile(r'(?:Payment\s+Due\s+Date|Due\s+Date)[：:\s]*([0-9]{4}[/-][0-9]{1,2}[/-][0-9]{1,2}|[0-9]{1,2}[/-][0-9]{1,2})', re.IGNORECASE)
        ]
        
        # 用於定位「消費明細」區間的起點與終點關鍵字
        self.section_start_keywords = ["消費明細", "交易明細", "消費日期", "交易日期", "消費明細及帳款", "Transaction Details", "Description"]
        self.section_end_keywords = ["說明", "注意事項", "循環", "循環利息", "循環信用", "紅利", "繳款截止日", "Important Information", "Note"]

        # 用於匹配單行消費明細的正規表示式
        # 模式一：兩個日期 (消費日 + 入帳日) + 描述 + 金額
        self.tx_pattern_two_dates = re.compile(
            r'(\d{2}/\d{2}|\d{4}/\d{2}/\d{2})\s+(\d{2}/\d{2}|\d{4}/\d{2}/\d{2})\s+(.+?)\s+(?:NT\$)?\$?\s*([-\d,.]+)'
        )
        # 模式二：一個日期 (消費日) + 描述 + 金額
        self.tx_pattern_one_date = re.compile(
            r'(\d{2}/\d{2}|\d{4}/\d{2}/\d{2})\s+(.+?)\s+(?:NT\$)?\$?\s*([-\d,.]+)'
        )

    def parse_amount(self, amount_str: str) -> float:
        """
        將金額字串轉換成數字
        """
        cleaned = amount_str.replace(",", "").replace("$", "").replace("NT", "").strip()
        try:
            if "." in cleaned:
                return float(cleaned)
            return int(cleaned)
        except ValueError:
            return 0

    def parse(self, text: str) -> dict:
        result = {
            "total_amount": 0,
            "total_amount_found": False,
            "details": [],
            "due_date": ""
        }
        
        if not text:
            return result

        lines = text.split("\n")
        due_date = ""
        for line in lines:
            for pattern in self.due_date_patterns:
                match = pattern.search(line)
                if match:
                    due_date = match.group(1).strip()
                    break
            if due_date:
                break
        result["due_date"] = due_date
        no_payment_patterns = [
            re.compile(r'本期應繳總(?:金額|總額)[^0-9]*?(?:無須繳款|免繳|不需繳款|不需付款|不需繳費)', re.IGNORECASE),
            re.compile(r'Total\s+Amount\s+Due[^0-9]*?(?:No\s+Payment\s+Due|No\s+Amount\s+Due|No\s+Payment\s+Required)', re.IGNORECASE)
        ]

        # 1. 提取本期應繳總金額
        for line in lines:
            if any(p.search(line) for p in no_payment_patterns):
                result["total_amount"] = 0
                result["total_amount_found"] = True
                break

            for pattern in self.amount_patterns:
                matches = pattern.findall(line)
                if matches:
                    # 排除可能匹配到的空字串或無效值，取第一個非空匹配
                    for m in matches:
                        if not re.fullmatch(r"[-+]?(?:\d[\d,]*(?:\.\d+)?|\.\d+)", m):
                            continue
                        val = self.parse_amount(m)
                        result["total_amount"] = val
                        result["total_amount_found"] = True
                        break
                    if result["total_amount_found"]:
                        break
            if result["total_amount_found"]:
                break

        # 2. 定位消費明細區段
        lines = text.split("\n")
        start_idx = 0
        end_idx = len(lines)
        
        # 尋找起點
        for i, line in enumerate(lines):
            if any(kw in line for kw in self.section_start_keywords):
                start_idx = i
                break
                
        # 尋找終點 (在起點之後)
        for i in range(start_idx + 1, len(lines)):
            stripped_line = lines[i].strip()
            is_end = False
            for kw in self.section_end_keywords:
                if kw == "說明":
                    if (stripped_line.startswith("說明") or "說明：" in stripped_line or "說明:" in stripped_line) and not (stripped_line.startswith("消費說明") or stripped_line.startswith("交易說明")):
                        is_end = True
                        break
                elif kw in stripped_line:
                    is_end = True
                    break
            if is_end:
                end_idx = i
                break
                
        # 擷取消費明細文字區段
        detail_lines = lines[start_idx:end_idx]
        if not detail_lines:
            detail_lines = lines # 如果找不到區段，退回到對整份文件逐行解析
            
        print(f"  [Parser] 擷取明細範圍：行 {start_idx} 至 行 {end_idx}")

        # 3. 逐行解析消費明細
        for line in detail_lines:
            line = line.strip()
            
            # 過濾掉包含客服、電話等不相干的行 (常見於頁尾或廣告)
            if any(exclude in line for exclude in ["電話", "專線", "客服", "利息", "年利率", "循環", "帳號", "百分比"]):
                continue
                
            # 優先嘗試雙日期匹配
            match = self.tx_pattern_two_dates.search(line)
            if match:
                date, post_date, desc, amt_str = match.groups()
                amt = self.parse_amount(amt_str)
                # 簡單過濾：描述字元數大於 1 且金額不為 0
                if len(desc.strip()) > 1 and amt != 0:
                    result["details"].append({
                        "date": date.strip(),
                        "post_date": post_date.strip(),
                        "description": desc.strip(),
                        "amount": amt
                    })
                continue
                
            # 嘗試單日期匹配
            match = self.tx_pattern_one_date.search(line)
            if match:
                date, desc, amt_str = match.groups()
                amt = self.parse_amount(amt_str)
                # 簡單過濾
                if len(desc.strip()) > 1 and amt != 0:
                    result["details"].append({
                        "date": date.strip(),
                        "post_date": "",
                        "description": desc.strip(),
                        "amount": amt
                    })
                    
        print(f"  [Parser] 解析完成，找到 {len(result['details'])} 筆消費明細，應繳總金額：{result['total_amount']}")
        return result
