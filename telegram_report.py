"""Run the existing pipeline and deliver its output without exposing CI logs."""
import io
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import yaml
from dotenv import dotenv_values
from bank_registry import normalize_bank
from statement_report import render_report


def redact(text):
    secrets = [value for key, value in os.environ.items()
               if value and any(word in key.upper() for word in
                                ("PASSWORD", "TOKEN", "SECRET", "_ID", "BIRTHDAY", "EMAIL"))]
    for value in sorted(set(secrets), key=len, reverse=True):
        text = text.replace(value, "[已隱藏]")
    return text


def public_report(report):
    """Drop local-only diagnostics even when reading an older report file."""
    result = json.loads(json.dumps(report))
    for balance in result.get("balances", []):
        balance.pop("private_diagnostics", None)
    return result


def read_current_report(path, run_id):
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict) or report.get("run_id") != run_id:
        return None
    if not isinstance(report.get("generated_at"), str):
        return None
    if (not isinstance(report.get("files", []), list)
            or not all(isinstance(name, str) for name in report.get("files", []))
            or not isinstance(report.get("balances", []), list)
            or not all(isinstance(row, dict) for row in report.get("balances", []))
            or not isinstance(report.get("statements", []), list)
            or not all(isinstance(row, dict) for row in report.get("statements", []))):
        return None
    return public_report(report)


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


def main(argv=()):
    parser = argparse.ArgumentParser(description="產生報告並傳送 Telegram")
    parser.add_argument("--balances-only", action="store_true",
                        default=os.getenv("REPORT_BALANCES_ONLY", "").lower() == "true")
    parser.add_argument("--bank", action="append", default=[], help="指定查詢銀行，可重複使用")
    args = parser.parse_args(argv)
    for key, value in dotenv_values(stream=io.StringIO(os.getenv("REPORT_ENV", ""))).items():
        if value is not None:
            os.environ[key] = value
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("請設定 TELEGRAM_BOT_TOKEN 與 TELEGRAM_CHAT_ID")

    # Do not stream bank/email errors or credentials into public Actions logs.
    banks = []
    run_id = uuid4().hex
    with tempfile.TemporaryFile() as log:
        try:
            command = [sys.executable, "main.py"]
            if args.balances_only:
                command.append("--balances-only")
            banks = list(dict.fromkeys(normalize_bank(value) for value in
                         [*args.bank, *re.split(r'[\s,，、]+', os.getenv("REPORT_BANKS", "").strip())] if value))
            for bank in banks:
                command.extend(["--bank", bank])
            result = subprocess.run(command, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=2400,
                                    env={**os.environ, "CREDITCARD_REPORT_RUN_ID": run_id})
            succeeded = result.returncode == 0
        except subprocess.TimeoutExpired:
            succeeded = False
            log.write("\n雲端執行逾時；以下為已完成的部分結果。\n".encode())
        except ValueError as error:
            succeeded = False
            log.write(f"銀行篩選設定錯誤：{error}\n".encode())
        log.seek(0)
        output = redact(log.read().decode("utf-8", errors="replace"))
    config = yaml.safe_load(Path("config.yaml").read_text())
    output_dir = Path(config["storage"]["output_dir"])
    latest = output_dir / "latest.json"
    current_report = read_current_report(latest, run_id)
    succeeded = succeeded and current_report is not None
    report = current_report if current_report is not None else {}
    header = ("銀行餘額報告\n" if args.balances_only else "信用卡完整報告\n") + ("執行完成\n" if succeeded else "部分流程失敗，請查看原因\n")
    if current_report is None:
        header += "本次尚未產出可確認的報告。\n"
    if banks:
        header += "本次餘額查詢銀行：" + "、".join(banks) + "\n"
    for warning in report.get("warnings", []):
        header += f"提醒：{warning}\n"
    for balance in report.get("balances", []):
        if balance.get("error_code") == "verification_required":
            header += f"{balance['bank_name']}：需要人工驗證（可能為 OTP），本次未取得餘額。\n"
        elif str(balance.get("error_code", "")).startswith("captcha_"):
            header += f"{balance['bank_name']}：圖形驗證碼流程未完成，本次未取得餘額；這不是已確認的 OTP 要求。\n"
        elif balance.get("error_code") == "login_unconfirmed":
            header += f"{balance['bank_name']}：登入／驗證未完成；無法確認是否為 OTP。\n"
    for part in chunks(redact(header + "\n" + render_report(report))):
        request_telegram(token, "sendMessage", json.dumps(
            {"chat_id": chat_id, "text": part}, ensure_ascii=False).encode(), "application/json")
    # Include all structured results, not just the terminal summary.
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("console.txt", output)
        if current_report is not None:
            bundle.writestr("latest.json", redact(json.dumps(report, ensure_ascii=False, indent=2)))
        private_path = output_dir / "private_bank_diagnostics.json"
        if current_report is not None and private_path.exists():
            private_report = json.loads(private_path.read_text())
            current = {(row.get("bank"), row.get("queried_at"))
                       for row in report.get("balances", []) if row.get("bank") and row.get("queried_at")}
            private_rows = [row for row in private_report.get("banks", [])
                            if private_report.get("run_id") == run_id
                            and (row.get("bank"), row.get("queried_at")) in current]
            if private_rows:
                bundle.writestr(private_path.name, redact(json.dumps(
                    {"schema_version": 1, "banks": private_rows}, ensure_ascii=False, indent=2)))
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
        raise SystemExit(main(sys.argv[1:]))
    except Exception:
        print("報告傳送失敗；請檢查 Secrets 與執行設定。", file=sys.stderr)
        raise SystemExit(1)
