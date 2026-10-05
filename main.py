import os
import json
import hashlib
import logging
import fnmatch
import tempfile
import re
from decimal import Decimal
import yaml
from pathlib import Path
from dotenv import load_dotenv

from mail_fetcher import MailFetcher, is_excluded_email
from pdf_processor import PDFProcessor
from parsers.engine import get_parser_for_text, PARSER_ENGINE_VERSION
from statement_filter import latest_statement_flags
from fubon_downloader import FubonStatementDownloader
from config_validation import validate_config
from storage_utils import private_directory, private_file

logger = logging.getLogger(__name__)

def detect_bank_from_filename(filename: str) -> str:
    bank_patterns = {
        "中國信託": ["中國信託", "ctbc", "china trust"],
        "第一銀行": ["第一銀行", "first bank"],
        "國泰世華": ["國泰世華", "cathay", "信用卡電子帳單消費明細"],
        "玉山銀行": ["玉山銀行", "esun", "e.sun"],
        "台新銀行": ["台新銀行", "tsb", "taishin"],
        "台北富邦": ["台北富邦", "fubon"],
        "花旗": ["花旗", "citibank"],
        "渣打": ["渣打", "standard chartered"],
        "星展": ["星展", "dbs", "cbgcc-dailystmt"],
        "上海商銀": ["上海商銀", "shanghai"],
        "永豐銀行": ["永豐", "sinopac"],
        "新光銀行": ["新光銀行", "skbank"],
        "遠東商銀": ["遠東商銀", "fareastone", "feib", "cycle-statement"],
        "聯邦銀行": ["聯邦銀行", "ubot"],
        "彰化銀行": ["彰化銀行", "彰銀", "chb"],
    }
    lower_name = filename.lower()
    for bank, patterns in bank_patterns.items():
        if any(pattern in lower_name for pattern in patterns):
            return bank
    if re.search(r"信用卡帳單20\d{2}年\d{1,2}月", lower_name):
        return "新光銀行"
    return "未知銀行"


def is_ctbc_statement(pdf_path: Path) -> bool:
    filename = pdf_path.name.lower()
    return "ctbc" in filename or "中國信託" in filename or "china trust" in filename


def keep_latest_statements(pdf_files: list[Path]) -> list[Path]:
    ranks = [pdf_file.stat().st_mtime for pdf_file in pdf_files]
    keep = latest_statement_flags([pdf_file.name for pdf_file in pdf_files], ranks)
    return [pdf_file for pdf_file, should_keep in zip(pdf_files, keep) if should_keep]


def write_json_atomic(path: Path, data: dict):
    private_directory(path.parent)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as temp_file:
            json.dump(data, temp_file, ensure_ascii=False, indent=2)
            temp_path = Path(temp_file.name)
        temp_path.replace(path)
        private_file(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def get_review_reasons(parsed_data: dict, text: str, parser) -> list[str]:
    reasons = []
    if not parsed_data.get("total_amount_found", False):
        reasons.append("未辨識到本期應繳金額")

    total = parsed_data.get("total_amount", 0)
    if total and not parsed_data.get("due_date"):
        reasons.append("應繳金額非零但未辨識到繳款期限")

    details = parsed_data.get("details", [])
    transaction_markers = ("消費明細", "交易明細", "消費日期", "交易日期")
    if (getattr(parser, "supports_details", True)
            and any(marker in text for marker in transaction_markers)
            and parser.has_transaction_rows(text)
            and not details):
        reasons.append("帳單包含交易區塊但未解析出明細")

    malformed = [
        row for row in details
        if not row.get("date") or not row.get("description")
        or not isinstance(row.get("amount"), (int, float))
    ]
    if malformed:
        reasons.append(f"有 {len(malformed)} 筆交易欄位不完整")

    amounts = [row.get("amount") for row in details if isinstance(row.get("amount"), (int, float))]
    if len(amounts) >= 3 and len(set(amounts)) == 1 and 100 <= abs(amounts[0]) <= 200:
        reasons.append("多筆交易金額異常相同，可能誤將民國年份辨識為金額")
    reasons.extend(parser.validation_errors(parsed_data, text))
    return reasons


def process_single_pdf(pdf_path: Path, processor: PDFProcessor, output_dir: Path, password=None) -> dict:
    """
    處理單一 PDF 帳單檔案：解密、解析文字、儲存 JSON 結果。
    """
    output_path = output_dir / f"{pdf_path.stem}_parsed.json"
    try:
        digest = hashlib.sha256()
        with pdf_path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        pdf_digest = digest.hexdigest()
        if output_path.exists():
            try:
                with output_path.open("r", encoding="utf-8") as source:
                    cached = json.load(source)
                metadata = cached.get("metadata", {})
                if (metadata.get("status") == "success"
                        and metadata.get("parser_version") == PARSER_ENGINE_VERSION
                        and metadata.get("pdf_sha256") == pdf_digest):
                    private_file(output_path)
                    return cached
            except (OSError, ValueError, TypeError):
                logger.warning("快取 %s 無法讀取，將重新解析", output_path.name)
        # CTBC uses an embedded font that pypdf exposes as /UNICxxxx glyph
        # names.  Its layout-aware extractor preserves the payment summary.
        use_pdfplumber = is_ctbc_statement(pdf_path)
        text = processor.extract_text(pdf_path, use_pdfplumber=use_pdfplumber, password=password)

        # 2. 獲取對應的解析器
        parser = get_parser_for_text(text, filename=pdf_path.name)
        
        # 3. 解析文字內容
        parsed_data = parser.parse(text)
        
        # 若使用 pypdf 解析出的明細是空的，試著用 pdfplumber 重試
        # A bank-specific parser may intentionally return no transaction rows
        # while still having confidently extracted the payment summary.  In
        # that case, do not overwrite good data with a second extractor.
        if (not parsed_data["details"]
                and not parsed_data.get("total_amount_found", False)
                and not use_pdfplumber):
            print("  [提示] 使用預設提取器未找到消費明細，嘗試切換至 pdfplumber 進行精細解析...")
            try:
                text_plumber = processor.extract_text(pdf_path, use_pdfplumber=True, password=password)
                text = text_plumber
                parser = get_parser_for_text(text_plumber, filename=pdf_path.name)
                parsed_data = parser.parse(text_plumber)
            except Exception as e:
                logger.warning("pdfplumber 提取 %s 失敗: %s", pdf_path.name, e)

        if not text.strip():
            raise ValueError("PDF 未提取到任何文字，可能是掃描影像或文件格式不支援。")

        # 4. 附加檔案元數據
        review_reasons = get_review_reasons(parsed_data, text, parser)
        parsed_data["metadata"] = {
            "filename": pdf_path.name,
            "file_size_bytes": pdf_path.stat().st_size,
            "status": "success",
            "parser_version": PARSER_ENGINE_VERSION,
            "schema_version": 1,
            "pdf_sha256": pdf_digest,
            "needs_review": bool(review_reasons),
            "review_reasons": review_reasons,
        }
        
        # 5. 儲存解析結果至 output 資料夾
        write_json_atomic(output_path, parsed_data)
            
        print(f"  [成功] 解析結果已儲存至: {output_path.name}")
        return parsed_data

    except Exception as e:
        logger.exception("處理 %s 失敗", pdf_path.name)
        error_data = {
            "total_amount": 0,
            "details": [],
            "metadata": {
                "filename": pdf_path.name,
                "status": "error",
                "error_message": str(e)
            }
        }
        # 仍然寫入錯誤紀錄，方便排查
        write_json_atomic(output_path, error_data)
        return error_data

def run_balance_query(config: dict, interactive: bool = True, output_dir: Path | None = None,
                      *, show_results: bool = True) -> bool:
    from bank_balances import run_queries
    report = None
    if output_dir is not None:
        path = output_dir / "latest.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        report["balances"] = []
        write_json_atomic(path, report)

    def save_result(result):
        if report is not None:
            report["balances"].append(result)
            write_json_atomic(output_dir / "latest.json", report)

    return run_queries(config, interactive=interactive,
                       on_result=save_result if report is not None else None,
                       show_summary=False, show_results=show_results)


def print_statement_summaries(summaries: list, balances: list) -> set:
    by_bank = {result["bank_name"].removesuffix("銀行"): result for result in balances}
    displayed = set()
    for bank_name, parsed_result in summaries:
        print("-" * 50)
        meta = parsed_result.get("metadata", {})
        if meta.get("status") != "success":
            print(f"檔案: {meta.get('filename')} - 解析錯誤: {meta.get('error_message')}")
            continue
        print(f"銀行: {bank_name}")
        if parsed_result.get("total_amount_found", True):
            print(f"本期應繳總金額: NT$ {parsed_result['total_amount']:,}")
        else:
            print("本期應繳總金額: 未辨識（需人工確認）")
        if bank_name == "未知銀行":
            print(f"檔案: {meta.get('filename', '未知')}（銀行未辨識）")
        print(f"繳款截止日: {parsed_result.get('due_date') or '未知'}")
        balance = by_bank.get(bank_name.removesuffix("銀行"))
        if balance:
            displayed.add(balance["bank"])
            if balance["status"] == "success":
                amount = Decimal(balance["balance"])
                if balance.get("currency") == "TWD":
                    label = "臺幣活期存款餘額" if balance.get("scope") == "twd_current_deposit_total" else "臺幣存款餘額"
                    print(f"{label}: NT$ {amount:,.2f}")
                else:
                    print(f"存款餘額: {amount:,.2f}（幣別依網銀頁面）")
            elif balance["status"] == "cancelled":
                print("存款餘額: 查詢已取消")
            elif balance.get("error_code") == "maintenance_skipped":
                print("存款餘額: 已跳過（銀行維修中）")
            elif balance.get("error_code") == "verification_required":
                print("存款餘額: 待完成網銀驗證")
            elif balance.get("error_code") == "captcha_recognition_failed":
                print("存款餘額: 圖形驗證碼無法確認，需人工處理（本次未送出登入）")
            elif balance.get("error_code") == "credentials_rejected":
                print("存款餘額: 登入資料不符，請更新該銀行設定")
            elif balance.get("error_code") == "login_unconfirmed":
                print("存款餘額: 尚未確認登入成功")
            elif balance.get("error_code") == "deposit_access_unavailable":
                print("存款餘額: 尚未開通存款網銀查詢")
            elif balance.get("error_code") == "customer_information_update_required":
                print("存款餘額: 請先在銀行完成基本資料與 EDD 問卷更新")
            else:
                print(f"存款餘額: 查詢失敗（{balance.get('error_message', '未取得餘額')}）")
        if meta.get("needs_review"):
            reasons = "；".join(meta.get("review_reasons", []))
            print(f"提醒：解析結果需要人工確認：{reasons}")
    return displayed


def run_pipeline(config_path="config.yaml", interactive=True, balance_banks=None):
    """
    執行完整的下載與解析流程。
    """
    # 載入設定
    config_path = Path(config_path).resolve()
    load_dotenv(config_path.parent / ".env", override=False)
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if balance_banks is not None:
        settings = config.get("balance_query", {})
        config["balance_query"] = {
            bank: {**settings.get(bank, {}), "enabled": True}
            for bank in dict.fromkeys(balance_banks)
        }

    base_dir = config_path.parent
    validate_config(config, base_dir)
    env_path = base_dir / ".env"
    if env_path.exists():
        private_file(env_path)
    download_dir = base_dir / config["storage"]["download_dir"]
    output_dir = base_dir / config["storage"]["output_dir"]
    private_directory(download_dir)
    private_directory(output_dir)

    print("=== 步驟 1: 檢查 Yahoo 信箱下載最新帳單 ===")
    fetcher = MailFetcher(config_path)
    excluded_filenames = {
        Path(attachment).name
        for record in fetcher.history.values()
        if isinstance(record, dict) and is_excluded_email(record.get("subject", ""), config["mail"]["local_filters"], record.get("sender", ""))
        for attachment in record.get("attachments", [])
    }
    mail_fetch_succeeded = True
    try:
        downloaded_count = fetcher.fetch_statements()
        print(f"下載步驟結束。本次下載了 {downloaded_count} 個新檔案。")
    except Exception as e:
        mail_fetch_succeeded = False
        print(f"Yahoo 信箱附件下載失敗: {e}")

    fubon_fetch_succeeded = True
    fubon_config = config.get("fubon", {})
    if fubon_config.get("enabled", False):
        fubon_id = os.getenv("FUBON_ID", "")
        fubon_birthday = os.getenv("FUBON_BIRTHDAY", "")
        if not fubon_id or not fubon_birthday:
            fubon_fetch_succeeded = False
            print("  [富邦] 未設定 FUBON_ID/FUBON_BIRTHDAY，略過網頁帳單下載。")
        else:
            try:
                fubon_links = fetcher.fetch_fubon_statement_links()
                downloader = FubonStatementDownloader(
                    fubon_id, fubon_birthday, download_dir,
                    captcha_retries=fubon_config.get("captcha_retries", 5),
                )
                for link in fubon_links:
                    try:
                        downloaded_path = downloader.download(link)
                        fetcher.mark_fubon_link_downloaded(link)
                        print(f"  [富邦] 已下載帳單: {downloaded_path.name}")
                    except Exception as error:
                        fubon_fetch_succeeded = False
                        print(f"  [富邦] 單一帳單下載失敗，稍後會重試: {error}")
            except Exception as error:
                fubon_fetch_succeeded = False
                print(f"  [富邦] 信件連結讀取失敗: {error}")

    print("\n=== 步驟 2: 開始解密與解析 PDF 帳單 ===")
    bank_passwords = {
        bank: os.getenv(value[2:-1], "") if value.startswith("${") and value.endswith("}") else value
        for bank, value in config.get("mail", {}).get("bank_pdf_passwords", {}).items()
    }
    filename_passwords = {
        pattern: os.getenv(value[2:-1], "") if value.startswith("${") and value.endswith("}") else value
        for pattern, value in config.get("storage", {}).get("pdf_password_patterns", {}).items()
    }

    # 取得下載目錄中所有的 PDF 檔案
    pdf_files = list(download_dir.glob("*.pdf"))
    if excluded_filenames:
        eligible_files = [pdf_file for pdf_file in pdf_files if pdf_file.name not in excluded_filenames]
        excluded_count = len(pdf_files) - len(eligible_files)
        if excluded_count:
            print(f"依郵件主旨排除 {excluded_count} 個 PDF，不進行解析。")
        pdf_files = eligible_files
    latest_pdf_files = keep_latest_statements(pdf_files)
    skipped_count = len(pdf_files) - len(latest_pdf_files)
    if skipped_count:
        print(f"各帳單版型的舊期 PDF 略過 {skipped_count} 個，只處理最新一期。")
    pdf_files = latest_pdf_files
    if not pdf_files:
        print("未在下載目錄中找到任何 PDF 檔案。請確認是否有收到對帳單郵件，或檢查 .env 設定。")
        write_json_atomic(output_dir / "latest.json", {
            "schema_version": 1, "parser_version": PARSER_ENGINE_VERSION, "files": [],
        })
        balance_succeeded = run_balance_query(config, interactive=interactive, output_dir=output_dir)
        return mail_fetch_succeeded and fubon_fetch_succeeded and balance_succeeded

    print(f"共找到 {len(pdf_files)} 個 PDF 帳單進行處理。")
    
    # 初始化 PDF 處理器
    processor = PDFProcessor()
    current_results = []
    parse_failures = 0
    summaries = []
    
    # 遍歷處理
    for pdf_file in pdf_files:
        print(f"解析帳單: {pdf_file.name}")
        bank_name = detect_bank_from_filename(pdf_file.name)
        password = next(
            (secret for pattern, secret in filename_passwords.items()
             if fnmatch.fnmatchcase(pdf_file.name.casefold(), pattern.casefold())),
            bank_passwords.get(bank_name),
        )
        parsed_result = process_single_pdf(pdf_file, processor, output_dir, password=password)
        if parsed_result.get("metadata", {}).get("status") == "success":
            current_results.append(f"{pdf_file.stem}_parsed.json")
        else:
            parse_failures += 1
        
        summaries.append((bank_name, parsed_result))

    print("-" * 50)
    write_json_atomic(output_dir / "latest.json", {
        "schema_version": 1,
        "parser_version": PARSER_ENGINE_VERSION,
        "files": current_results,
    })
    balance_succeeded = run_balance_query(config, interactive=interactive, output_dir=output_dir,
                                          show_results=False)
    balances = json.loads((output_dir / "latest.json").read_text(encoding="utf-8"))["balances"]
    displayed = print_statement_summaries(summaries, balances)
    # Banks without a successful card statement still retain their query result.
    for result in balances:
        if result["bank"] not in displayed and result["status"] == "success":
            amount = Decimal(result["balance"])
            if result.get("currency") == "TWD":
                label = "臺幣活期存款餘額" if result.get("scope") == "twd_current_deposit_total" else "臺幣存款餘額"
                print(f"{result['bank_name']}{label}: NT$ {amount:,.2f}")
            else:
                print(f"{result['bank_name']}存款餘額: {amount:,.2f}（幣別依網銀頁面）")
    print("所有帳單處理完成！")
    if not mail_fetch_succeeded or not fubon_fetch_succeeded:
        print("注意：本次部分下載流程失敗，上述結果可能包含先前下載的帳單。")
    return mail_fetch_succeeded and fubon_fetch_succeeded and parse_failures == 0 and balance_succeeded

if __name__ == "__main__":
    import argparse
    from bank_registry import SUPPORTED_BANKS
    parser = argparse.ArgumentParser(description="產出信用卡帳單清單，並在各銀行繳款截止日下方附上即時餘額")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--bank", choices=sorted(SUPPORTED_BANKS), action="append",
                        help="帳單完成後只查指定銀行的餘額，可重複使用")
    args = parser.parse_args()
    raise SystemExit(0 if run_pipeline(args.config, balance_banks=args.bank) else 1)
