import json

import telegram_report as report


def test_bank_names_accept_chinese_aliases():
    from bank_registry import normalize_bank
    assert normalize_bank("上海") == "shanghai"
    assert normalize_bank("彰銀") == "chb"
    assert normalize_bank("玉山銀行") == "esun"
    assert normalize_bank("CTBC") == "ctbc"


def test_chunks_preserve_all_text_and_respect_telegram_units():
    text = "帳單😀\n" * 3000
    parts = list(report.chunks(text))
    assert "".join(parts) == text
    assert all(len(part.encode("utf-16-le")) // 2 <= 3500 for part in parts)


def test_redact_credentials(monkeypatch):
    monkeypatch.setenv("CTBC_PASSWORD", "private-password")
    monkeypatch.setenv("BANK_ID", "private-identity")
    assert report.redact("private-password private-identity") == "[已隱藏] [已隱藏]"


def test_summary_omits_debug_output_but_preserves_results_and_failures():
    output = ('fontTools is required to fully parse a font\n'
              '[銀行] 啟動 Chrome\n'
              '銀行: 星展\n本期應繳總金額: NT$ 1,051\n'
              '繳款截止日: 2026/10/16\n臺幣存款餘額: NT$ 1,051.00\n'
              '銀行: 玉山銀行\n存款餘額: 尚未確認登入成功\n'
              '提醒：需人工確認\n')
    text = report.summary_text(output, [
        {"bank_name": "玉山銀行", "status": "error", "error_code": "login_unconfirmed"},
        {"bank_name": "華南銀行", "status": "error", "error_message": "連線逾時"}])
    assert "fontTools" not in text and "Chrome" not in text
    assert "NT$ 1,051.00" in text and "2026/10/16" in text
    assert "尚未確認登入成功" in text and "提醒：需人工確認" in text
    assert "華南銀行：存款餘額未取得（連線逾時）" in text


def test_send_document_includes_chat_and_file(monkeypatch):
    captured = []
    monkeypatch.setattr(report, "request_telegram", lambda *args: captured.append(args))
    report.send_document("token", "123", "report.zip", b"zip-content")
    token, method, data, content_type = captured[0]
    assert method == "sendDocument"
    assert b"123" in data and b"zip-content" in data
    assert b'filename="report.zip"' in data
    assert content_type.startswith("multipart/form-data;")


def test_partial_failure_still_sends_report_and_verification_notice(monkeypatch, tmp_path, capsys):
    import io
    import zipfile
    from types import SimpleNamespace

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("REPORT_ENV", "BANK_PASSWORD=private-password")
    monkeypatch.setenv("BANK_PASSWORD", "private-password")
    monkeypatch.setenv("REPORT_BANKS", "彰銀、玉山銀行")
    (tmp_path / "config.yaml").write_text("storage:\n  output_dir: output\n")
    output = tmp_path / "output"
    output.mkdir()
    (output / "latest.json").write_text(json.dumps({
        "files": ["statement.json"], "balances": [{"bank_name": "測試銀行",
        "status": "error", "error_code": "verification_required"},
        {"bank_name": "上海商銀", "status": "error", "error_code": "login_form_unavailable",
         "error_message": "未找到已驗證的個人網銀登入表單，未送出帳密",
         "private_diagnostics": [{"visible_text": "private-bank-notice"}]}]}))
    (output / "statement.json").write_text('{"details": [{"amount": 500}]}')

    def run(*args, **kwargs):
        assert args[0] == [report.sys.executable, "main.py", "--bank", "chb", "--bank", "esun"]
        kwargs["stdout"].write("fontTools is required\n帳單完成 private-password".encode())
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(report.subprocess, "run", run)
    messages, documents = [], []
    monkeypatch.setattr(report, "request_telegram", lambda *args: messages.append(json.loads(args[2])))
    monkeypatch.setattr(report, "send_document", lambda *args: documents.append(args[3]))
    assert report.main() == 1
    assert "private-bank-notice" not in capsys.readouterr().out
    assert "需要人工驗證" in messages[0]["text"]
    assert "上海商銀：登入／驗證未完成" not in messages[0]["text"]
    assert "未找到已驗證的個人網銀登入表單" in messages[0]["text"]
    assert "本次僅測試銀行餘額：chb、esun" in messages[0]["text"]
    assert "private-password" not in messages[0]["text"]
    assert "fontTools" not in messages[0]["text"]
    assert "private-bank-notice" not in messages[0]["text"]
    with zipfile.ZipFile(io.BytesIO(documents[0])) as archive:
        assert set(archive.namelist()) == {"console.txt", "latest.json", "statement.json"}
        assert json.loads(archive.read("statement.json"))["details"][0]["amount"] == 500
        assert b"fontTools is required" in archive.read("console.txt")
        assert b"private-bank-notice" in archive.read("latest.json")
