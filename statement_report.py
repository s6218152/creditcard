"""Conservative statement identity, date classification and structured reports."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from zoneinfo import ZoneInfo

from statement_filter import _period_and_group


def taipei_today():
    return datetime.now(ZoneInfo("Asia/Taipei")).date()


def normalized_due_date(value):
    match = re.fullmatch(r"\s*(\d{3,4})[/-](\d{1,2})[/-](\d{1,2})\s*", str(value or ""))
    if not match:
        return None
    year, month, day = map(int, match.groups())
    if year < 1911:
        year += 1911
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def statement_identity(text, bank_name, filename):
    # A bank name, delivery date or card's last four digits is not an identity.
    # Hash only a unique, explicitly labelled full billing/card account number.
    numbers = set(re.findall(
        r"(?m)^\s*(?:信用卡帳號|帳單帳號|信用卡卡號)\s*[:：]\s*([0-9][0-9 -]{8,30}[0-9])\s*$", text))
    numbers = {re.sub(r"[ -]", "", value) for value in numbers}
    numbers = {value for value in numbers if 10 <= len(value) <= 19}
    identity = None
    if len(numbers) == 1 and bank_name != "未知銀行":
        identity = sha256((bank_name + ":" + next(iter(numbers))).encode()).hexdigest()
    periods = set()
    for year, month in re.findall(
            r"(?:帳單年月|帳單月份|帳單期別)\s*[:：]?\s*(\d{3,4})[年/-](\d{1,2})(?:月)?", text):
        year, month = int(year), int(month)
        if year < 1911:
            year += 1911
        if 1 <= month <= 12:
            periods.add(f"{year:04d}-{month:02d}")
    period = next(iter(periods)) if len(periods) == 1 else None
    if not periods:
        filename_period, _ = _period_and_group(filename)
        if filename_period:
            period = f"{filename_period[0]:04d}-{filename_period[1]:02d}"
    return {"identity_hash": identity, "statement_period": period}


def classify_statements(statements, *, today=None):
    today = today or taipei_today()
    rows = [dict(row) for row in statements]
    latest = {}
    for row in rows:
        identity, period = row.get("identity_hash"), row.get("statement_period")
        if identity and period:
            key = (row.get("bank_name"), identity)
            latest[key] = max(latest.get(key, ""), period)
    for row in rows:
        due = normalized_due_date(row.get("due_date"))
        row["due_date_iso"] = due
        identity, period = row.get("identity_hash"), row.get("statement_period")
        row["selection_status"] = "unverified"
        if identity and period:
            row["selection_status"] = "latest" if period == latest[(row.get("bank_name"), identity)] else "history"
        row["date_status"] = "unknown"
        if due:
            days = (date.fromisoformat(due) - today).days
            row["date_status"] = "past_due_unconfirmed" if days < 0 else "due_soon" if days <= 7 else "upcoming"
    return rows


def statement_summary(bank_name, parsed_result, parsed_file):
    meta = parsed_result.get("metadata", {})
    return {
        "bank_name": bank_name, "filename": meta.get("filename", ""), "parsed_file": parsed_file,
        "total_amount": parsed_result.get("total_amount"),
        "total_amount_found": parsed_result.get("total_amount_found", True),
        "due_date": parsed_result.get("due_date", ""),
        "identity_hash": meta.get("identity_hash"), "statement_period": meta.get("statement_period"),
        "status": meta.get("status", "error"), "error_message": meta.get("error_message", ""),
        "review_reasons": meta.get("review_reasons", []),
    }


def balance_lines(row):
    name = row.get("bank_name", "未知銀行")
    if row.get("status") != "success":
        return [f"{name}：存款餘額未取得（{row.get('error_message') or row.get('error_code') or row.get('status')}）"]
    try:
        amount = Decimal(str(row.get("balance")))
        if not amount.is_finite():
            raise ValueError("non-finite balance")
    except (InvalidOperation, ValueError):
        return [f"{name}：存款餘額資料無法確認"]
    label = "臺幣活期存款餘額" if row.get("scope") == "twd_current_deposit_total" else "臺幣存款餘額"
    result = [f"{label}: NT$ {amount:,.2f}" if row.get("currency") == "TWD"
              else f"存款餘額: {amount:,.2f}（幣別依網銀頁面）"]
    if row.get("queried_at"):
        result.append(f"餘額查詢時間: {row['queried_at']}")
    return result


def render_report(report, *, today=None):
    """Render JSON only; console messages never determine financial results."""
    today = today or taipei_today()
    rows = classify_statements(report.get("statements", []), today=today)
    balances = report.get("balances", [])
    by_bank = {r.get("bank_name", "").removesuffix("銀行"): r for r in balances}
    groups = {"最新／待確認帳單": [], "歷史帳單（不代表已繳清）": [], "已過截止日（繳款狀態未確認）": []}
    for row in rows:
        section = "歷史帳單（不代表已繳清）" if row["selection_status"] == "history" else (
            "已過截止日（繳款狀態未確認）" if row["date_status"] == "past_due_unconfirmed" else "最新／待確認帳單")
        groups[section].append(row)
    lines, displayed = [], set()
    if report.get("generated_at"):
        lines.append(f"報告產生時間: {report['generated_at']}")
    for title, items in groups.items():
        if not items:
            continue
        lines.extend(["", title])
        for row in sorted(items, key=lambda r: (r.get("due_date_iso") or "9999", r.get("bank_name", ""))):
            name = row.get("bank_name", "未知銀行")
            lines.extend(["--------------------", f"銀行: {name}"])
            if row.get("status") != "success":
                lines.append(f"檔案: {row.get('filename')} - 解析錯誤: {row.get('error_message')}")
                continue
            if row.get("statement_period"):
                lines.append(f"帳單期別: {row['statement_period']}")
            if row["selection_status"] != "unverified":
                lines.append("帳單狀態: " + ("最新一期" if row["selection_status"] == "latest" else "歷史帳單"))
            try:
                amount = Decimal(str(row.get("total_amount")))
                amount_found = row.get("total_amount_found", True) and amount.is_finite()
            except InvalidOperation:
                amount, amount_found = Decimal(0), False
            lines.append(f"本期應繳總金額: NT$ {amount:,}" if amount_found else "本期應繳總金額: 未辨識（需人工確認）")
            lines.append(f"繳款截止日: {(row.get('due_date_iso') or row.get('due_date') or '未知').replace('-', '/')}")
            if row["selection_status"] == "unverified":
                lines.append("提醒：卡片／帳戶身分或期別未確認，保留此帳單；未確認是否已繳款。")
            if row["selection_status"] != "history" and row["date_status"] == "due_soon" and amount_found and amount > 0:
                days = (date.fromisoformat(row["due_date_iso"]) - today).days
                lines.append(f"提醒：{'今天截止' if days == 0 else f'{days} 天後截止'}，請確認繳款安排。")
            if row.get("review_reasons"):
                lines.append("提醒：解析結果需要人工確認：" + "；".join(row["review_reasons"]))
            balance = by_bank.get(name.removesuffix("銀行"))
            if balance and name.removesuffix("銀行") not in displayed:
                lines.extend(balance_lines(balance))
                displayed.add(name.removesuffix("銀行"))
    for row in balances:
        if row.get("bank_name", "").removesuffix("銀行") not in displayed:
            lines.extend([f"銀行: {row.get('bank_name', '未知銀行')}", *balance_lines(row)])
    return "\n".join(lines) or "本次未產出可顯示的結果；請查看附件中的完整紀錄。"
