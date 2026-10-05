import pytest
import pypdf
from pathlib import Path
from pdf_processor import PDFProcessor

def test_pdf_processor_decryption(tmp_path, monkeypatch):
    """
    動態生成加密的 PDF，測試 PDFProcessor 的解密功能與異常處理。
    """
    # 建立一個測試用的 PDF
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    
    # 用密碼「test1234」加密
    writer.encrypt("test1234")
    
    pdf_path = tmp_path / "test_encrypted.pdf"
    with open(pdf_path, "wb") as f:
        writer.write(f)
        
    # 1. 使用正確的密碼解密，應該成功且不拋出錯誤
    processor = PDFProcessor(password="test1234")
    text = processor.extract_text(pdf_path)
    assert isinstance(text, str)  # 雖然是空白頁，但也應該返回空字串或格式化內容，且不報錯
    
    # 2. 使用錯誤的密碼，應該拋出 ValueError 代表解密失敗
    processor_wrong = PDFProcessor(password="wrong_password")
    with pytest.raises(ValueError, match="解密失敗"):
        processor_wrong.extract_text(pdf_path)

    # 3. 未提供密碼，應該拋出 ValueError 代表未提供密碼
    monkeypatch.delenv("PDF_PASSWORD", raising=False)
    processor_none = PDFProcessor(password="")
    with pytest.raises(ValueError, match="未提供解密密碼"):
        processor_none.extract_text(pdf_path)
        
    # 4. 使用 override 密碼解密，應該成功
    processor_override = PDFProcessor(password="wrong_password")
    text_override = processor_override.extract_text(pdf_path, password="test1234")
    assert isinstance(text_override, str)
        
def test_pdf_processor_non_encrypted(tmp_path):
    """
    測試處理未加密的 PDF
    """
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    
    pdf_path = tmp_path / "test_normal.pdf"
    with open(pdf_path, "wb") as f:
        writer.write(f)
        
    processor = PDFProcessor()
    text = processor.extract_text(pdf_path)
    assert isinstance(text, str)
