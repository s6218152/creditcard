from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter

from fubon_downloader import FubonStatementDownloader


def test_fubon_output_name_converts_roc_period():
    assert (
        FubonStatementDownloader._output_name("11509")
        == "台北富邦銀行2026年9月信用卡帳單.pdf"
    )
    assert (
        FubonStatementDownloader._output_name("1150929")
        == "台北富邦銀行2026年9月信用卡帳單.pdf"
    )


def test_fubon_download_uses_login_data_and_writes_pdf(tmp_path, monkeypatch):
    downloader = FubonStatementDownloader("S123456789", "0800101", tmp_path)
    monkeypatch.setattr(
        downloader,
        "_login",
        lambda serial: {
            "jwt": "token",
            "billPeriod": "11509",
            "batchPeriod": "batch",
            "uniqueIdentifier": "identifier",
            "twYearMonth": "11509",
        },
    )
    calls = []
    pdf_buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(pdf_buffer)
    valid_pdf = pdf_buffer.getvalue()

    def fake_request(path, payload=None, headers=None, method="GET"):
        calls.append((path, headers))
        return valid_pdf

    monkeypatch.setattr(downloader, "_request", fake_request)

    output = downloader.download(
        "https://fbmbill.taipeifubon.com.tw/client/pdf/abcdef123456"
    )

    assert output == Path(tmp_path) / "台北富邦銀行2026年9月信用卡帳單.pdf"
    assert output.read_bytes().startswith(b"%PDF")
    assert calls[0][1] == {"Authorization": "token"}


def test_fubon_download_validates_encrypted_pdf(tmp_path, monkeypatch):
    identity = "S123456789"
    downloader = FubonStatementDownloader(identity, "0800101", tmp_path)
    monkeypatch.setattr(
        downloader,
        "_login",
        lambda serial: {
            "jwt": "token",
            "billPeriod": "11509",
            "batchPeriod": "batch",
            "uniqueIdentifier": "identifier",
            "twYearMonth": "11509",
        },
    )
    pdf_buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt(identity)
    writer.write(pdf_buffer)
    monkeypatch.setattr(downloader, "_request", lambda *args, **kwargs: pdf_buffer.getvalue())

    output = downloader.download(
        "https://fbmbill.taipeifubon.com.tw/client/pdf/abcdef123456"
    )

    assert output.exists()
