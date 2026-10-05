import os
import imaplib
import email
from email.header import decode_header
import json
import yaml
from pathlib import Path
from dotenv import load_dotenv
import datetime
import re
import fnmatch
import html
import tempfile
import hashlib
import io
import pypdf
from email.utils import parsedate_to_datetime, parseaddr
from statement_filter import latest_statement_flags
from config_validation import validate_config
from storage_utils import private_directory, private_file

FUBON_PDF_LINK = re.compile(
    r"https://fbmbill\.taipeifubon\.com\.tw/client/pdf/[0-9a-f]+",
    re.IGNORECASE,
)

def decode_mime_header(header_value):
    """
    解碼郵件的 MIME 標頭內容（如主旨、寄件者、檔名等）。
    """
    if not header_value:
        return ""
    try:
        decoded = decode_header(header_value)
    except Exception:
        return str(header_value)
        
    parts = []
    for text, charset in decoded:
        if isinstance(text, bytes):
            if charset:
                try:
                    parts.append(text.decode(charset))
                except Exception:
                    parts.append(text.decode('utf-8', errors='ignore'))
            else:
                parts.append(text.decode('utf-8', errors='ignore'))
        else:
            parts.append(str(text))
    return "".join(parts)

def matches_email_filter(subject, sender, local_filters):
    """
    判斷郵件是否符合本地過濾條件。
    支援純文字包含檢查與簡單的通配符模式（例如 * 和 ?）。
    """
    subject = subject or ""
    sender = sender or ""

    if is_excluded_email(subject, local_filters, sender):
        return False

    def matches_any(text, patterns):
        text = text.casefold()
        for pattern in patterns:
            pattern = pattern.casefold()
            if any(wild in pattern for wild in "*?"):
                if fnmatch.fnmatch(text, pattern):
                    return True
            elif pattern in text:
                return True
        return False

    if matches_any(subject, local_filters.get("subjects", [])):
        return True
    if matches_any(subject, local_filters.get("banks", [])):
        return True
    if matches_any(sender, local_filters.get("banks", [])):
        return True
    return False


def is_excluded_email(subject, local_filters, sender=""):
    original_subject = subject or ""
    sender_domain = parseaddr(sender or "")[1].rpartition("@")[2].casefold()
    for bank, rule in local_filters.get("bank_subject_rules", {}).items():
        domains = rule.get("sender_domains", [])
        belongs_to_bank = (bank in original_subject or bank in (sender or "") or
                           any(sender_domain == domain or sender_domain.endswith("." + domain)
                               for domain in domains))
        allowed = (original_subject in rule.get("exact_subjects", []) or
                   any(re.fullmatch(pattern, original_subject)
                       for pattern in rule.get("subject_regexes", [])))
        if belongs_to_bank and not allowed:
            return True
    subject = (subject or "").casefold()
    for pattern in local_filters.get("exclude_subjects", []):
        pattern = pattern.casefold()
        if any(wild in pattern for wild in "*?"):
            if fnmatch.fnmatchcase(subject, pattern):
                return True
        elif pattern in subject:
            return True
    return False

def get_search_cutoff_date(mail_config):
    """
    根據 mail 設定計算 IMAP 搜尋的起始日期。
    支援最新月份模式（latest_month）與固定天數模式。
    """
    today = datetime.date.today()
    if mail_config.get("search_mode") == "latest_month":
        # 以 "最新月份" 為基準，從上個月份的第一天開始搜尋，避免在月初時遺漏前一個月的帳單。
        current_month_start = today.replace(day=1)
        previous_month_last_day = current_month_start - datetime.timedelta(days=1)
        cutoff_date = previous_month_last_day.replace(day=1)
    else:
        search_days = mail_config.get("search_days", 90)
        cutoff_date = today - datetime.timedelta(days=search_days)
    return cutoff_date.strftime("%d-%b-%Y")


def extract_fubon_statement_links(message) -> list[str]:
    links = []
    for part in message.walk():
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        body = payload.decode(charset, errors="ignore")
        links.extend(FUBON_PDF_LINK.findall(html.unescape(body)))
    return list(dict.fromkeys(links))

class MailFetcher:
    def __init__(self, config_path="config.yaml"):
        self.config_path = Path(config_path).resolve()
        load_dotenv(self.config_path.parent / ".env", override=False)
        # 讀取設定檔
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
        base_dir = self.config_path.parent
        validate_config(self.config, base_dir)
        env_path = base_dir / ".env"
        if env_path.exists():
            private_file(env_path)
            
        # 建立下載與輸出目錄
        self.download_dir = base_dir / self.config["storage"]["download_dir"]
        private_directory(self.download_dir)
        
        self.output_dir = base_dir / self.config["storage"]["output_dir"]
        private_directory(self.output_dir)
        
        self.history_file = base_dir / self.config["storage"]["history_file"]
        private_directory(self.history_file.parent)
        if self.history_file.exists():
            private_file(self.history_file)
        
        # 載入下載歷史紀錄
        self.history = self.load_history()
        
    def load_history(self):
        if self.history_file.exists():
            try:
                with open(self.history_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"警告: 無法載入歷史紀錄檔案 {self.history_file}，錯誤: {e}")
        return {}
        
    def save_history(self):
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.history_file.parent,
                prefix=f".{self.history_file.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                json.dump(self.history, temp_file, ensure_ascii=False, indent=2)
                temp_path = Path(temp_file.name)
            temp_path.replace(self.history_file)
            private_file(self.history_file)
        except Exception as e:
            if "temp_path" in locals():
                temp_path.unlink(missing_ok=True)
            raise OSError(f"無法儲存歷史紀錄至 {self.history_file}: {e}") from e

    def connect(self):
        """
        連線並登入 IMAP 伺服器
        """
        imap_server = self.config["mail"]["imap_server"]
        port = self.config["mail"]["port"]
        
        email_user = os.getenv("YAHOO_EMAIL")
        email_password = os.getenv("YAHOO_APP_PASSWORD")
        
        if not email_user or not email_password:
            raise ValueError("請在 .env 檔案中設定 YAHOO_EMAIL 與 YAHOO_APP_PASSWORD")
            
        print(f"連線至 IMAP 伺服器: {imap_server}:{port}...")
        self.mail = imaplib.IMAP4_SSL(imap_server, port)
        
        print(f"登入帳號: {email_user}...")
        self.mail.login(email_user, email_password)
        
        folder = self.config["mail"]["folder"]
        self.mail.select(folder)
        status, uidvalidity = self.mail.response("UIDVALIDITY")
        if status == "UIDVALIDITY" and uidvalidity and uidvalidity[0]:
            current_uidvalidity = uidvalidity[0].decode("ascii", errors="ignore")
            if self.history.get("_uidvalidity") != current_uidvalidity:
                self.history = {"_uidvalidity": current_uidvalidity}
                self.save_history()
        print(f"已選擇資料夾: {folder}")
        
    def disconnect(self):
        """
        關閉連線
        """
        try:
            self.mail.close()
            self.mail.logout()
            print("已登出並中斷連線")
        except Exception:
            pass

    def fetch_statements(self):
        """
        搜尋並下載符合條件的帳單 PDF 附件
        """
        try:
            self.connect()
            date_cutoff = get_search_cutoff_date(self.config.get("mail", {}))
            
            print(f"第一階段：伺服器端搜尋自 {date_cutoff} 以來的所有郵件...")
            status, search_data = self.mail.uid('search', None, 'SINCE', date_cutoff)
            if status != 'OK':
                print(f"伺服器搜尋失敗。狀態: {status}")
                return 0
                
            all_uids = [u.decode('utf-8') for u in search_data[0].split()]
            print(f"伺服器端共找到 {len(all_uids)} 封郵件。")

            local_filters = self.config["mail"]["local_filters"]
            filter_fingerprint = hashlib.sha256(
                json.dumps(local_filters, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()

            # 過濾掉已經下載過的 UID
            new_uids = []
            for uid in all_uids:
                record = self.history.get(uid)
                if not isinstance(record, dict):
                    new_uids.append(uid)
                elif ((record.get("matched") is False or record.get("excluded") is True)
                      and record.get("filter_fingerprint") != filter_fingerprint):
                    new_uids.append(uid)
            print(f"排除已下載歷史紀錄後，尚有 {len(new_uids)} 封新郵件待檢查標頭。")
            
            if not new_uids:
                print("沒有需要檢查的新郵件。")
                return 0
                
            # 批次獲取信件標頭以優化效能 (一次抓 50 筆)
            matched_emails = [] # 儲存 (uid, subject, sender, date_str)
            chunk_size = 50
            
            history_changed = False
            for i in range(0, len(new_uids), chunk_size):
                chunk = new_uids[i:i+chunk_size]
                id_str = ",".join(chunk)
                
                status, fetch_data = self.mail.uid('fetch', id_str, '(RFC822.HEADER)')
                if status != 'OK':
                    print(f"批次獲取標頭失敗 (UID: {id_str})，跳過此批次。")
                    continue
                    
                for item in fetch_data:
                    if not isinstance(item, tuple):
                        continue
                    
                    # 從項目的第一部分提取 UID
                    envelope_info = item[0].decode('utf-8', errors='ignore')
                    match = re.search(r'UID\s+(\d+)', envelope_info)
                    if not match:
                        continue
                    uid_str = match.group(1)
                    
                    # 解析標頭內容
                    msg = email.message_from_bytes(item[1])
                    subject = decode_mime_header(msg.get('Subject', ''))
                    sender = decode_mime_header(msg.get('From', ''))
                    date_str = msg.get('Date', '')

                    if is_excluded_email(subject, local_filters, sender):
                        self.history[uid_str] = {
                            "subject": subject,
                            "sender": sender,
                            "date": date_str,
                            "attachments": [],
                            "excluded": True,
                            "filter_fingerprint": filter_fingerprint,
                        }
                        history_changed = True
                        continue
                    
                    # 本地過濾：主旨或寄件者
                    if matches_email_filter(subject, sender, local_filters):
                        print(f"發現符合郵件! 主旨: {subject} | 寄件者: {sender} | 日期: {date_str} (UID: {uid_str})")
                        matched_emails.append((uid_str, subject, sender, date_str))
                    else:
                        self.history[uid_str] = {
                            "subject": subject,
                            "sender": sender,
                            "date": date_str,
                            "attachments": [],
                            "matched": False,
                            "filter_fingerprint": filter_fingerprint,
                        }
                        history_changed = True

            if history_changed:
                self.save_history()
            
            print(f"第二階段篩選完成：本地共找到 {len(matched_emails)} 封符合條件的帳單郵件。")
            
            downloaded_count = 0
            pending_attachments = []
            scanned_emails = {}
            
            # 開始下載符合條件的完整信件並提取 PDF
            for uid_str, subject, sender, date_str in matched_emails:
                status, full_data = self.mail.uid('fetch', uid_str, '(RFC822)')
                if status != 'OK' or not full_data:
                    print(f"無法下載郵件 UID: {uid_str}")
                    continue
                
                raw_email = None
                for part in full_data:
                    if isinstance(part, tuple):
                        raw_email = part[1]
                        break
                        
                if not raw_email:
                    continue
                    
                full_msg = email.message_from_bytes(raw_email)
                
                # 搜尋並儲存 PDF 附件
                attachments = []
                attachment_failed = False
                scanned_emails[uid_str] = {
                    "subject": subject,
                    "sender": sender,
                    "date": date_str,
                    "attachments": attachments,
                    "failed": False,
                    "fubon_links": [],
                    "fubon_links_scanned": False,
                }
                for part in full_msg.walk():
                    if part.get_content_maintype() == 'multipart':
                        continue
                    if part.get('Content-Disposition') is None:
                        continue
                    
                    filename = part.get_filename()
                    if filename:
                        filename = decode_mime_header(filename)
                        if filename.lower().endswith('.pdf'):
                            if not should_save_attachment(filename, subject, sender, self.config.get("mail", {})):
                                continue
                            # 檔名處理：[UID]_[日期]_[原檔名]
                            safe_date = date_str.replace(' ', '_').replace(':', '').replace(',', '')
                            safe_date = safe_date[:20]
                            save_filename = f"{uid_str}_{safe_date}_{filename}"
                            save_filename = "".join(c for c in save_filename if c.isalnum() or c in "._-")
                            
                            payload = part.get_payload(decode=True)
                            if not payload:
                                attachment_failed = True
                                print(f"  附件內容為空，稍後重試: {filename}")
                                continue
                            try:
                                received_at = parsedate_to_datetime(date_str).timestamp()
                            except (TypeError, ValueError, OverflowError):
                                received_at = 0
                            pending_attachments.append({
                                "filename": save_filename,
                                "original_filename": filename,
                                "payload": payload,
                                "rank": received_at,
                                "email": scanned_emails[uid_str],
                            })

                fubon_links = extract_fubon_statement_links(full_msg)
                scanned_emails[uid_str]["fubon_links"] = fubon_links
                scanned_emails[uid_str]["fubon_links_scanned"] = True
                
                if attachment_failed:
                    scanned_emails[uid_str]["failed"] = True
                    continue

            keep_attachments = latest_statement_flags(
                [item["original_filename"] for item in pending_attachments],
                [item["rank"] for item in pending_attachments],
            )
            for attachment, should_download in zip(pending_attachments, keep_attachments):
                if not should_download:
                    print(f"  略過較舊帳單附件: {attachment['original_filename']}")
                    continue
                save_path = self.download_dir / attachment["filename"]
                try:
                    write_pdf_atomic(save_path, attachment["payload"])
                except Exception as exc:
                    attachment["email"]["failed"] = True
                    print(f"  附件驗證或儲存失敗，稍後重試: {save_path.name}: {exc}")
                    continue
                print(f"  已下載附件: {save_path.name}")
                attachment["email"]["attachments"].append(str(save_path))
                downloaded_count += 1

            history_changed = False
            for uid_str, record in scanned_emails.items():
                if record.pop("failed"):
                    continue
                self.history[uid_str] = record
                history_changed = True
            if history_changed:
                self.save_history()
                
            print(f"下載完成。本次共下載 {downloaded_count} 個 PDF 帳單附件。")
            return downloaded_count
            
        finally:
            self.disconnect()

    def fetch_fubon_statement_links(self) -> list[str]:
        """Find Taipei Fubon action links in recent statement emails."""
        links = [
            link
            for record in self.history.values()
            if isinstance(record, dict)
            for link in record.get("fubon_links", [])
            if link not in record.get("fubon_downloaded_links", [])
        ]
        pending_uids = [
            uid
            for uid, record in self.history.items()
            if uid != "_uidvalidity"
            and isinstance(record, dict)
            and "台北富邦銀行" in record.get("subject", "")
            and "信用卡帳單" in record.get("subject", "")
            and not record.get("fubon_links_scanned", False)
        ]
        if not pending_uids:
            return list(dict.fromkeys(links))

        try:
            self.connect()
            history_changed = False
            for uid in pending_uids:
                status, fetch_data = self.mail.uid("fetch", uid, "(RFC822)")
                if status != "OK":
                    continue
                raw_email = next((item[1] for item in fetch_data if isinstance(item, tuple)), None)
                if not raw_email:
                    continue
                message = email.message_from_bytes(raw_email)
                found_links = extract_fubon_statement_links(message)
                record = self.history.get(uid)
                if not isinstance(record, dict):
                    continue
                record["fubon_links"] = found_links
                record["fubon_links_scanned"] = True
                links.extend(found_links)
                history_changed = True
            if history_changed:
                self.save_history()
            return list(dict.fromkeys(links))
        finally:
            self.disconnect()

    def mark_fubon_link_downloaded(self, link: str):
        changed = False
        for record in self.history.values():
            if not isinstance(record, dict) or link not in record.get("fubon_links", []):
                continue
            downloaded = record.setdefault("fubon_downloaded_links", [])
            if link not in downloaded:
                downloaded.append(link)
                changed = True
        if changed:
            self.save_history()

def should_save_attachment(filename: str, subject: str, sender: str, mail_config: dict) -> bool:
    if not filename.lower().endswith(".pdf"):
        return False

    source_text = f"{subject} {sender}".casefold()
    attachment_patterns = mail_config.get("bank_attachment_patterns", {})

    for bank_name, patterns in attachment_patterns.items():
        bank_key = bank_name.casefold()
        if bank_key in source_text or bank_key.replace("銀行", "") in source_text:
            return any(fnmatch.fnmatchcase(filename.casefold(), pat.casefold()) for pat in patterns)

    return True


def write_pdf_atomic(path: Path, payload: bytes) -> None:
    if not payload.startswith(b"%PDF"):
        raise ValueError("附件不是 PDF")
    reader = pypdf.PdfReader(io.BytesIO(payload))
    if not reader.is_encrypted and len(reader.pages) < 1:
        raise ValueError("PDF 沒有頁面")
    private_directory(path.parent)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp",
                                         delete=False) as temp_file:
            temp_file.write(payload)
            temp_path = Path(temp_file.name)
        temp_path.replace(path)
        private_file(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

if __name__ == "__main__":
    fetcher = MailFetcher()
    try:
        fetcher.fetch_statements()
    except Exception as e:
        print(f"執行時發生錯誤: {e}")
