from io import BytesIO
from pathlib import Path
import subprocess

from PIL import Image
from pypdf import PdfWriter
import pytest

from fubon_downloader import FubonDownloadError, FubonStatementDownloader


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


@pytest.mark.parametrize("recover", [True, False])
def test_fubon_login_retries_ocr_timeouts(tmp_path, monkeypatch, recover):
    monkeypatch.setattr("fubon_downloader.shutil.which", lambda _: "/offline/tesseract")
    downloader = FubonStatementDownloader("synthetic-id", "synthetic-birthday", tmp_path,
                                          captcha_retries=2)
    image = BytesIO()
    Image.new("RGB", (4, 4), "white").save(image, "PNG")
    captcha_calls = []
    ocr_calls = []
    login_calls = []

    def fake_captcha():
        captcha_calls.append(True)
        return "offline-key", image.getvalue()

    def fake_ocr(command, **kwargs):
        ocr_calls.append(command)
        if len(ocr_calls) == 1 or not recover:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return subprocess.CompletedProcess(command, 0, stdout="1234", stderr="")

    def fake_request(path, payload=None, **kwargs):
        login_calls.append((path, payload))
        return b'{"jwt": "offline-token"}'

    monkeypatch.setattr(downloader, "_get_captcha", fake_captcha)
    monkeypatch.setattr(downloader, "_request", fake_request)
    monkeypatch.setattr("fubon_downloader.subprocess.run", fake_ocr)

    if recover:
        assert downloader._login("offline-serial") == {"jwt": "offline-token"}
        assert login_calls[0][1]["captchaCode"] == "offline-key,1234"
    else:
        with pytest.raises(FubonDownloadError, match="驗證碼辨識逾時"):
            downloader._login("offline-serial")
        assert login_calls == []
    assert len(captcha_calls) == 2
