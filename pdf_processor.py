import os
import pypdf
import pdfplumber
from pathlib import Path
from dotenv import load_dotenv

class PDFProcessor:
    def __init__(self, password=None):
        """
        初始化 PDF 處理器。
        如果沒有傳入密碼，將會嘗試自 .env 環境變數中讀取 PDF_PASSWORD。
        """
        self.password = password or os.getenv("PDF_PASSWORD")
        if not self.password:
            # 某些時候使用者尚未設定密碼，在此先發出警告，但允許實例化以利測試未加密 PDF
            print("提示: 未偵測到 PDF_PASSWORD，如果是加密 PDF 將無法完成解密。")

    def extract_text(self, pdf_path, use_pdfplumber=False, password=None) -> str:
        """
        開啟 PDF 檔案，若有加密則嘗試解密，並提取所有頁面的文字內容。
        
        :param pdf_path: PDF 檔案路徑（字串或 Path 物件）
        :param use_pdfplumber: 是否使用 pdfplumber 替代 pypdf 進行文字提取（對於表格排版可能更精準）
        :param password: 選用的 PDF 解密密碼，若未提供則使用初始化時的密碼
        :return: 提取出的完整文字內容
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"找不到 PDF 檔案: {pdf_path}")

        print(f"正在處理 PDF: {pdf_path.name}...")

        password_to_use = password if password is not None else self.password

        if use_pdfplumber:
            return self._extract_with_pdfplumber(pdf_path, password_to_use)
        else:
            return self._extract_with_pypdf(pdf_path, password_to_use)

    def _extract_with_pypdf(self, pdf_path: Path, password=None) -> str:
        text_content = []
        password_to_use = password if password is not None else self.password
        with open(pdf_path, "rb") as f:
            reader = pypdf.PdfReader(f)
            
            if reader.is_encrypted:
                if password_to_use is None:
                    raise ValueError(f"PDF 檔案 {pdf_path.name} 已加密，但未提供解密密碼。")
                
                print(f"  偵測到加密 PDF，嘗試使用提供的密碼進行解密...")
                # pypdf decrypt 成功會回傳解密成功的狀態
                # 0 表示解密失敗，非 0 表示成功（通常為 1 或 2 代表不同權限）
                decrypt_status = reader.decrypt(password_to_use)
                if decrypt_status == 0:
                    # 嘗試以密碼字串的 bytes 解密作二次嘗試
                    decrypt_status = reader.decrypt(password_to_use.encode('utf-8'))
                    
                if decrypt_status == 0:
                    raise ValueError(f"PDF 檔案 {pdf_path.name} 解密失敗。請確認密碼是否正確。")
                print("  解密成功！")

            # 提取每一頁的文字
            for i, page in enumerate(reader.pages):
                page_text = page.extract_text()
                if page_text:
                    text_content.append(page_text)
                    
        return "\n".join(text_content)

    def _extract_with_pdfplumber(self, pdf_path: Path, password=None) -> str:
        text_content = []
        password_to_use = password if password is not None else self.password
        # pdfplumber 需要使用密碼來開啟加密 PDF
        open_args = {}
        if password_to_use is not None:
            # 為了確定是否加密，我們可以先用 pypdf 檢查，或者直接傳 password 嘗試開啟
            # 這裡我們先用 pypdf 檢查是否加密，避免 pdfplumber 拋出錯誤
            with open(pdf_path, "rb") as f:
                reader = pypdf.PdfReader(f)
                is_encrypted = reader.is_encrypted
                
            if is_encrypted:
                open_args["password"] = password_to_use
                print(f"  偵測到加密 PDF，嘗試使用 pdfplumber 與密碼進行解密...")

        try:
            with pdfplumber.open(pdf_path, **open_args) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_content.append(page_text)
            print("  文字提取成功（使用 pdfplumber）")
        except Exception as e:
            if "password" in open_args and "password" in str(e).lower():
                raise ValueError(f"PDF 檔案 {pdf_path.name} 解密失敗。請確認密碼是否正確。錯誤: {e}")
            raise e

        return "\n".join(text_content)

if __name__ == "__main__":
    # 簡單的測試執行
    import sys
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    if len(sys.argv) > 1:
        test_pdf = sys.argv[1]
        processor = PDFProcessor()
        try:
            txt = processor.extract_text(test_pdf)
            print("--- 提取文字預覽 (前 300 字) ---")
            print(txt[:300])
        except Exception as e:
            print(f"錯誤: {e}")
    else:
        print("使用方式: python pdf_processor.py [pdf_file_path]")
