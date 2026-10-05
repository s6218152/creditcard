import json

import telegram_report as report


def test_chunks_preserve_all_text_and_respect_telegram_units():
    text = "帳單😀\n" * 3000
    parts = list(report.chunks(text))
    assert "".join(parts) == text
    assert all(len(part.encode("utf-16-le")) // 2 <= 3500 for part in parts)


def test_redact_credentials(monkeypatch):
    monkeypatch.setenv("CTBC_PASSWORD", "private-password")
    monkeypatch.setenv("BANK_ID", "private-identity")
    assert report.redact("private-password private-identity") == "[已隱藏] [已隱藏]"


def test_send_document_includes_chat_and_file(monkeypatch):
    captured = []
    monkeypatch.setattr(report, "request_telegram", lambda *args: captured.append(args))
    report.send_document("token", "123", "report.zip", b"zip-content")
    token, method, data, content_type = captured[0]
    assert method == "sendDocument"
    assert b"123" in data and b"zip-content" in data
    assert b'filename="report.zip"' in data
    assert content_type.startswith("multipart/form-data;")


def test_partial_failure_still_sends_report_and_verification_notice(monkeypatch, tmp_path):
    import io
    import zipfile
    from types import SimpleNamespace

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("REPORT_ENV", "BANK_PASSWORD=private-password")
    monkeypatch.setenv("BANK_PASSWORD", "private-password")
    monkeypatch.setenv("REPORT_BANKS", "chb esun")
    (tmp_path / "config.yaml").write_text("storage:\n  output_dir: output\n")
    output = tmp_path / "output"
    output.mkdir()
    (output / "latest.json").write_text(json.dumps({
        "files": ["statement.json"], "balances": [{"bank_name": "測試銀行",
        "status": "error", "error_code": "verification_required"}]}))
    (output / "statement.json").write_text('{"details": [{"amount": 500}]}')

    def run(*args, **kwargs):
        assert args[0] == [report.sys.executable, "main.py", "--bank", "chb", "--bank", "esun"]
        kwargs["stdout"].write("帳單完成 private-password".encode())
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(report.subprocess, "run", run)
    messages, documents = [], []
    monkeypatch.setattr(report, "request_telegram", lambda *args: messages.append(json.loads(args[2])))
    monkeypatch.setattr(report, "send_document", lambda *args: documents.append(args[3]))
    assert report.main() == 1
    assert "需要人工驗證" in messages[0]["text"]
    assert "本次僅測試銀行餘額：chb、esun" in messages[0]["text"]
    assert "private-password" not in messages[0]["text"]
    with zipfile.ZipFile(io.BytesIO(documents[0])) as archive:
        assert set(archive.namelist()) == {"console.txt", "latest.json", "statement.json"}
        assert json.loads(archive.read("statement.json"))["details"][0]["amount"] == 500
