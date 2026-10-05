"""Run the existing pipeline and deliver its output without exposing CI logs."""
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml
from dotenv import dotenv_values


def summary_text(output, balances):
    prefixes = ("銀行:", "本期應繳總金額:", "繳款截止日:", "臺幣存款餘額:",
                "臺幣活期存款餘額:", "存款餘額:", "提醒：", "檔案:",
                "注意：本次部分下載流程失敗", "雲端執行逾時")
    lines = []
    standalone = tuple(row.get("bank_name", "") + label for row in balances
                       for label in ("臺幣存款餘額:", "臺幣活期存款餘額:", "存款餘額:"))
    for line in output.splitlines():
        if line.startswith(prefixes + standalone):
            if line.startswith("銀行:") and lines:
                lines.append("--------------------")
            lines.append(line)
    text = "\n".join(lines)
    for row in balances:
        name = row.get("bank_name", "未知銀行")
        if row.get("status") != "success" and f"銀行: {name}\n" not in text + "\n":
            lines.append(f"{name}：存款餘額未取得（{row.get('error_message') or row.get('error_code') or row.get('status')}）")
    return "\n".join(lines) or "本次未產出可顯示的結果；請查看附件中的完整紀錄。"


def redact(text):
    secrets = [value for key, value in os.environ.items()
               if value and any(word in key.upper() for word in
                                ("PASSWORD", "TOKEN", "SECRET", "_ID", "BIRTHDAY", "EMAIL"))]
    for value in sorted(set(secrets), key=len, reverse=True):
        text = text.replace(value, "[已隱藏]")
    return text


def chunks(text, limit=3500):
    # Telegram counts UTF-16 units; emoji can consume two units.
    part, size = [], 0
    for char in text:
        units = len(char.encode("utf-16-le")) // 2
        if size + units > limit:
            yield "".join(part)
            part, size = [], 0
        part.append(char)
        size += units
    if part:
        yield "".join(part)


def request_telegram(token, method, data, content_type):
    request = Request(f"https://api.telegram.org/bot{token}/{method}",
                      data=data, headers={"Content-Type": content_type})
    try:
        with urlopen(request, timeout=60) as response:
            result = json.load(response)
        if not result.get("ok"):
            raise RuntimeError("Telegram 未接受通知")
    except (HTTPError, URLError) as error:
        # urllib errors can include the token-bearing URL.
        raise RuntimeError("Telegram 傳送失敗；請檢查 Token、Chat ID 與網路") from None


def send_document(token, chat_id, name, content):
    boundary = "creditcard-report-boundary"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n"
            f"{chat_id}\r\n--{boundary}\r\nContent-Disposition: form-data; "
            f"name=\"document\"; filename=\"{name}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n").encode()
    body += content + f"\r\n--{boundary}--\r\n".encode()
    request_telegram(token, "sendDocument", body, f"multipart/form-data; boundary={boundary}")


def main():
    for key, value in dotenv_values(stream=io.StringIO(os.getenv("REPORT_ENV", ""))).items():
        if value is not None:
            os.environ[key] = value
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("請設定 TELEGRAM_BOT_TOKEN 與 TELEGRAM_CHAT_ID")

    # Do not stream bank/email errors or credentials into public Actions logs.
    with tempfile.TemporaryFile() as log:
        try:
            command = [sys.executable, "main.py"]
            banks = os.getenv("REPORT_BANKS", "").split()
            for bank in banks:
                command.extend(["--bank", bank])
            result = subprocess.run(command, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=2400)
            succeeded = result.returncode == 0
        except subprocess.TimeoutExpired:
            succeeded = False
            log.write("\n雲端執行逾時；以下為已完成的部分結果。\n".encode())
        log.seek(0)
        output = redact(log.read().decode("utf-8", errors="replace"))
    header = "信用卡完整報告\n" + ("執行完成\n" if succeeded else "部分流程失敗，請查看原因\n")
    if banks:
        header += "本次僅測試銀行餘額：" + "、".join(banks) + "\n"
    config = yaml.safe_load(Path("config.yaml").read_text())
    output_dir = Path(config["storage"]["output_dir"])
    latest = output_dir / "latest.json"
    report = json.loads(latest.read_text()) if latest.exists() else {}
    for balance in report.get("balances", []):
        if balance.get("error_code") == "verification_required":
            header += f"{balance['bank_name']}：需要人工驗證（可能為 OTP），本次未取得餘額。\n"
        elif str(balance.get("error_code", "")).startswith("captcha_"):
            header += f"{balance['bank_name']}：圖形驗證碼流程未完成，本次未取得餘額；這不是已確認的 OTP 要求。\n"
        elif balance.get("error_code") == "login_unconfirmed" or (
                balance.get("status") == "error" and "驗證" in balance.get("error_message", "")):
            header += f"{balance['bank_name']}：登入／驗證未完成；無法確認是否為 OTP。\n"
    for part in chunks(redact(header + "\n" + summary_text(output, report.get("balances", [])))):
        request_telegram(token, "sendMessage", json.dumps(
            {"chat_id": chat_id, "text": part}, ensure_ascii=False).encode(), "application/json")
    # Include all structured results, not just the terminal summary.
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("console.txt", output)
        if latest.exists():
            bundle.writestr("latest.json", redact(latest.read_text()))
        for name in report.get("files", []):
            path = output_dir / Path(name).name
            if path.exists():
                bundle.writestr(path.name, redact(path.read_text()))
    send_document(token, chat_id, "creditcard-report.zip", archive.getvalue())
    # Status and counts only: keep financial amounts and errors in Telegram.
    safe_status = [{"bank": row.get("bank"), "status": row.get("status"),
                    "error_code": row.get("error_code"), "diagnostics": row.get("diagnostics", [])}
                   for row in report.get("balances", [])]
    print("Telegram 訊息與附件傳送完成；銀行狀態：" + json.dumps(safe_status, ensure_ascii=False))
    return 0 if succeeded else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("報告傳送失敗；請檢查 Secrets 與執行設定。", file=sys.stderr)
        raise SystemExit(1)
