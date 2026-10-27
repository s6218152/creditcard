import json
import pytest

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
    text = report.render_report({'statements': [{'bank_name': '星展', 'status': 'success',
        'total_amount': 1051, 'due_date': '115/10/16', 'review_reasons': ['需人工確認']}], 'balances': [
        {'bank_name': '星展', 'status': 'success', 'balance': '1051', 'currency': 'TWD'},
        {"bank_name": "玉山銀行", "status": "error", "error_message": "尚未確認登入成功"},
        {"bank_name": "華南銀行", "status": "error", "error_message": "連線逾時"}]})
    assert "fontTools" not in text and "Chrome" not in text
    assert "NT$ 1,051.00" in text and "2026/10/16" in text
    assert "尚未確認登入成功" in text and "需人工確認" in text
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


@pytest.mark.parametrize("failure", ["invalid_bank", "exit_error", "timeout", "missing_current_report"])
def test_failed_run_never_attaches_previous_results(monkeypatch, tmp_path, failure):
    import io
    import zipfile
    from types import SimpleNamespace

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("REPORT_ENV", "")
    monkeypatch.setenv("REPORT_BANKS", "unsupported-test-bank" if failure == "invalid_bank" else "")
    (tmp_path / "config.yaml").write_text("storage:\n  output_dir: output\n")
    output = tmp_path / "output"
    output.mkdir()
    (output / "latest.json").write_text(json.dumps({
        "run_id": "previous-run", "files": ["previous.json"], "balances": [{
            "bank": "ctbc", "bank_name": "Previous bank", "status": "success",
            "balance": "4321", "queried_at": "2000-01-01T00:00:00+08:00"}]}))
    (output / "previous.json").write_text('{"previous_result": true}')

    def run(*args, **kwargs):
        assert failure != "invalid_bank"
        if failure == "timeout":
            raise report.subprocess.TimeoutExpired(args[0], 2400)
        return SimpleNamespace(returncode=0 if failure == "missing_current_report" else 1)

    messages, documents = [], []
    monkeypatch.setattr(report.subprocess, "run", run)
    monkeypatch.setattr(report, "request_telegram", lambda *args: messages.append(json.loads(args[2])))
    monkeypatch.setattr(report, "send_document", lambda *args: documents.append(args[3]))
    assert report.main() == 1
    assert all("Previous bank" not in message["text"] for message in messages)
    with zipfile.ZipFile(io.BytesIO(documents[0])) as archive:
        assert set(archive.namelist()) == {"console.txt"}


def test_corrupt_manifest_still_delivers_failure_log(monkeypatch, tmp_path):
    from types import SimpleNamespace

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setenv("REPORT_ENV", "")
    monkeypatch.setenv("REPORT_BANKS", "")
    (tmp_path / "config.yaml").write_text("storage:\n  output_dir: output\n")
    (tmp_path / "output").mkdir()
    (tmp_path / "output/latest.json").write_text("{corrupt")
    monkeypatch.setattr(report.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    messages, documents = [], []
    monkeypatch.setattr(report, "request_telegram", lambda *args: messages.append(args))
    monkeypatch.setattr(report, "send_document", lambda *args: documents.append(args))
    assert report.main() == 1
    assert messages and documents


def test_successful_run_delivers_the_current_manifest(monkeypatch, tmp_path):
    import io
    import zipfile
    from types import SimpleNamespace

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    # A short numeric secret can randomly match and redact part of a UUID.
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat-id")
    monkeypatch.setenv("REPORT_ENV", "")
    monkeypatch.setenv("REPORT_BANKS", "")
    (tmp_path / "config.yaml").write_text("storage:\n  output_dir: output\n")
    (tmp_path / "output").mkdir()
    run_ids = []

    def run(*args, **kwargs):
        run_id = kwargs["env"]["CREDITCARD_REPORT_RUN_ID"]
        run_ids.append(run_id)
        (tmp_path / "output/latest.json").write_text(json.dumps({
            "run_id": run_id, "generated_at": "2026-10-13T00:00:00+00:00",
            "files": [], "balances": [], 'statements': [{'bank_name': '目前銀行',
            'status': 'success', 'total_amount': 1051, 'due_date': '115/10/16'}]}))
        kwargs['stdout'].write('銀行: 偽造銀行\n本期應繳總金額: NT$ 999999\nfontTools warning'.encode())
        return SimpleNamespace(returncode=0)

    messages, documents = [], []
    monkeypatch.setattr(report.subprocess, "run", run)
    monkeypatch.setattr(report, "request_telegram", lambda *args: messages.append(json.loads(args[2])))
    monkeypatch.setattr(report, "send_document", lambda *args: documents.append(args[3]))
    assert report.main() == 0
    assert "執行完成" in messages[0]["text"]
    assert '目前銀行' in messages[0]['text'] and '1,051' in messages[0]['text']
    assert '偽造銀行' not in messages[0]['text'] and '999999' not in messages[0]['text']
    with zipfile.ZipFile(io.BytesIO(documents[0])) as archive:
        assert json.loads(archive.read("latest.json"))["run_id"] == run_ids[0]


@pytest.mark.parametrize('use_environment', [True, False])
def test_balances_only_telegram_command_and_archive(monkeypatch, tmp_path, use_environment):
    from types import SimpleNamespace
    import io
    import zipfile
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'test-chat-id')
    monkeypatch.setenv('REPORT_ENV', '')
    monkeypatch.setenv('REPORT_BANKS', '彰銀' if use_environment else '')
    monkeypatch.setenv('REPORT_BALANCES_ONLY', 'true' if use_environment else '')
    (tmp_path / 'config.yaml').write_text('storage:\n  output_dir: output\n')
    (tmp_path / 'output').mkdir()
    (tmp_path / 'output/old.json').write_text('{"old":true}')
    def run(command, **kwargs):
        assert command == [report.sys.executable, 'main.py', '--balances-only', '--bank', 'chb']
        (tmp_path / 'output/latest.json').write_text(json.dumps({
            'run_id': kwargs['env']['CREDITCARD_REPORT_RUN_ID'], 'generated_at': '2026-10-28T04:00:00+00:00',
            'files': [], 'statements': [], 'balances': [{'bank_name': '彰化銀行', 'bank': 'chb',
                'status': 'success', 'balance': '121', 'currency': 'TWD'}]}))
        return SimpleNamespace(returncode=0)
    messages, documents = [], []
    monkeypatch.setattr(report.subprocess, 'run', run)
    monkeypatch.setattr(report, 'request_telegram', lambda *args: messages.append(json.loads(args[2])))
    monkeypatch.setattr(report, 'send_document', lambda *args: documents.append(args[3]))
    assert report.main(() if use_environment else ['--balances-only', '--bank', '彰銀']) == 0
    assert '銀行餘額報告' in messages[0]['text'] and '121.00' in messages[0]['text']
    with zipfile.ZipFile(io.BytesIO(documents[0])) as archive:
        assert set(archive.namelist()) == {'console.txt', 'latest.json'}


@pytest.mark.parametrize("current_diagnostics", [True, False])
@pytest.mark.parametrize("current_run_diagnostics", [True, False])
def test_partial_failure_still_sends_report_and_verification_notice(monkeypatch, tmp_path, capsys, current_diagnostics, current_run_diagnostics):
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
        {"bank": "shanghai", "queried_at": "2026-10-06T19:11:00+08:00",
         "bank_name": "上海商銀", "status": "error", "error_code": "login_form_unavailable",
         "error_message": "未找到已驗證的個人網銀登入表單，未送出帳密",
         "private_diagnostics": [{"visible_text": "private-bank-notice"}]}]}))
    (output / "statement.json").write_text('{"details": [{"amount": 500}]}')
    (output / "private_bank_diagnostics.json").write_text(json.dumps({"schema_version": 1, "banks": [{
        "bank": "shanghai", "queried_at": "2026-10-06T19:11:00+08:00" if current_diagnostics else "2026-10-05T19:11:00+08:00",
        "private_diagnostics": [{"visible_text": "private-bank-notice private-password"}]}]}))

    def run(*args, **kwargs):
        assert args[0] == [report.sys.executable, "main.py", "--bank", "chb", "--bank", "esun"]
        run_id = kwargs["env"]["CREDITCARD_REPORT_RUN_ID"]
        manifest = json.loads((output / "latest.json").read_text())
        manifest.update(run_id=run_id, generated_at="2026-10-06T19:11:00+08:00")
        (output / "latest.json").write_text(json.dumps(manifest))
        private = json.loads((output / "private_bank_diagnostics.json").read_text())
        private["run_id"] = run_id if current_run_diagnostics else "previous-run"
        (output / "private_bank_diagnostics.json").write_text(json.dumps(private))
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
    assert "本次餘額查詢銀行：chb、esun" in messages[0]["text"]
    assert "private-password" not in messages[0]["text"]
    assert "fontTools" not in messages[0]["text"]
    assert "private-bank-notice" not in messages[0]["text"]
    with zipfile.ZipFile(io.BytesIO(documents[0])) as archive:
        expected = {"console.txt", "latest.json", "statement.json"}
        if current_diagnostics and current_run_diagnostics:
            expected.add("private_bank_diagnostics.json")
            private = archive.read("private_bank_diagnostics.json")
            assert b"private-bank-notice" in private
            assert b"private-password" not in private
        assert set(archive.namelist()) == expected
        assert json.loads(archive.read("statement.json"))["details"][0]["amount"] == 500
        assert b"fontTools is required" in archive.read("console.txt")
        assert b"private-bank-notice" not in archive.read("latest.json")
