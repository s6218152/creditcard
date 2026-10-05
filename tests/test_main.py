from pathlib import Path
from main import is_ctbc_statement, detect_bank_from_filename, get_review_reasons, process_single_pdf
from parsers.general import GeneralParser
from parsers.ctbc import CtbcParser
from parsers.dbs import DbsParser
from parsers.shanghai import ShanghaiParser
from parsers.cathay import CathayParser
from parsers.first_bank import FirstBankParser
from parsers.esun import EsunParser
from parsers.sinopac import SinopacParser
from parsers.skbank import SkbankParser
from parsers.feib import FeibParser
from parsers.ubot import UbotParser
from parsers.engine import get_parser_for_text
from main import run_balance_query
from decimal import Decimal


def test_main_balance_query_uses_configured_selector(monkeypatch, capsys):
    import ctbc_balance
    calls = []

    def query(selector):
        calls.append(selector)
        return Decimal("125680")

    monkeypatch.setattr(ctbc_balance, "query_balance", query)
    assert run_balance_query({"balance_query": {"ctbc": {"enabled": True, "balance_selector": "#balance"}}})
    assert calls == ["#balance"]
    assert "125,680.00" in capsys.readouterr().out


def test_main_balance_query_skips_background_and_disabled(monkeypatch):
    import ctbc_balance

    def unexpected(*args):
        raise AssertionError("不應開啟瀏覽器")

    monkeypatch.setattr(ctbc_balance, "query_balance", unexpected)
    assert run_balance_query({})
    assert run_balance_query({"balance_query": {"ctbc": {"enabled": True}}}, interactive=False)


def test_main_balance_query_reports_failure(monkeypatch, capsys):
    import ctbc_balance

    def failed(*args):
        raise ValueError("APP-1053")

    monkeypatch.setattr(ctbc_balance, "query_balance", failed)
    assert not run_balance_query({"balance_query": {"ctbc": {"enabled": True}}})
    assert "APP-1053" in capsys.readouterr().out


def test_is_ctbc_statement_detects_ctbc_filename():
    assert is_ctbc_statement(Path("CTBC_Bank_Estatement.pdf"))
    assert is_ctbc_statement(Path("中國信託信用卡電子帳單.pdf"))
    assert is_ctbc_statement(Path("china trust statement.pdf"))


def test_is_ctbc_statement_ignores_other_bank_files():
    assert not is_ctbc_statement(Path("esun_credit_card_statement.pdf"))
    assert not is_ctbc_statement(Path("cathay_statement.pdf"))


def test_ctbc_filename_selects_ctbc_parser_even_when_labels_are_unreadable():
    parser = get_parser_for_text("/UNIC0031/UNIC0031", filename="CTBC_card_Estatement.pdf")
    assert isinstance(parser, CtbcParser)


def test_dbs_filename_selects_dbs_parser():
    parser = get_parser_for_text("", filename="CBGCC-DAILYSTMT_123.pdf")
    assert isinstance(parser, DbsParser)


def test_shanghai_filename_selects_shanghai_parser():
    parser = get_parser_for_text("", filename="2026年9月上海商銀信用卡對帳單.pdf")
    assert isinstance(parser, ShanghaiParser)


def test_cathay_filename_selects_cathay_parser():
    parser = get_parser_for_text("", filename="信用卡電子帳單消費明細_11508.pdf")
    assert isinstance(parser, CathayParser)


def test_first_bank_filename_selects_first_bank_parser():
    parser = get_parser_for_text("", filename="第一銀行電子對帳單2026年09月.pdf")
    assert isinstance(parser, FirstBankParser)


def test_esun_filename_selects_esun_parser():
    parser = get_parser_for_text("", filename="ESUN_Estatement_11508.pdf")
    assert isinstance(parser, EsunParser)


def test_sinopac_filename_selects_sinopac_parser():
    parser = get_parser_for_text("", filename="永豐銀行信用卡帳單.pdf")
    assert isinstance(parser, SinopacParser)


def test_skbank_text_selects_skbank_parser_for_generic_attachment_name():
    parser = get_parser_for_text("新光銀行信用卡電子帳單", filename="信用卡帳單2026年9月.pdf")
    assert isinstance(parser, SkbankParser)


def test_feib_filename_selects_feib_parser():
    parser = get_parser_for_text("", filename="202609cycle-statement.pdf")
    assert isinstance(parser, FeibParser)


def test_ubot_filename_selects_ubot_parser():
    parser = get_parser_for_text("", filename="UBOT_Estatement_11509.pdf")
    assert isinstance(parser, UbotParser)


def test_detect_bank_from_filename_tsbc():
    assert detect_bank_from_filename("TSB_Creditcard_Estatement_202608.pdf") == "台新銀行"


def test_detect_bank_from_filename_esun():
    assert detect_bank_from_filename("ESUN_Estatement_202608.pdf") == "玉山銀行"


def test_detect_bank_from_filename_dbs_daily_statement():
    assert detect_bank_from_filename("CBGCC-DAILYSTMT_123.pdf") == "星展"


def test_detect_bank_from_filename_shanghai():
    assert detect_bank_from_filename("2026年9月上海商銀信用卡對帳單.pdf") == "上海商銀"


def test_detect_bank_from_filename_cathay():
    assert detect_bank_from_filename("信用卡電子帳單消費明細_11508.pdf") == "國泰世華"


def test_detect_bank_from_filename_sinopac():
    assert detect_bank_from_filename("永豐銀行信用卡帳單.pdf") == "永豐銀行"


def test_detect_bank_from_filename_skbank():
    assert detect_bank_from_filename("新光銀行信用卡電子帳單.pdf") == "新光銀行"
    assert detect_bank_from_filename("信用卡帳單2026年9月.pdf") == "新光銀行"
    assert detect_bank_from_filename("信用卡帳單2026年10月.pdf") == "新光銀行"


def test_review_flags_suspicious_year_as_repeated_amount():
    parsed = {
        "total_amount": 1000,
        "total_amount_found": True,
        "due_date": "115/10/20",
        "details": [
            {"date": "09/01", "description": "A", "amount": 115},
            {"date": "09/02", "description": "B", "amount": 115},
            {"date": "09/03", "description": "C", "amount": 115},
        ],
    }
    reasons = get_review_reasons(parsed, "消費明細", GeneralParser())
    assert any("民國年份" in reason for reason in reasons)


def test_empty_first_bank_transaction_table_does_not_require_review():
    parsed = {"total_amount": 0, "total_amount_found": True, "due_date": "115/09/18", "details": []}
    text = "信用卡消費明細\n消費日 入帳\n起息日 消費明細說明 新臺幣金額\n您的本期金額總計 -50\n結束"
    assert get_review_reasons(parsed, text, FirstBankParser()) == []


def test_pdf_read_failure_is_reported_per_file(tmp_path):
    class Processor:
        def extract_text(self, *args, **kwargs):
            raise AssertionError("should not be called")
    result = process_single_pdf(tmp_path / "missing.pdf", Processor(), tmp_path / "output")
    assert result["metadata"]["status"] == "error"


def test_detect_bank_from_filename_feib():
    assert detect_bank_from_filename("遠東商銀信用卡帳單.pdf") == "遠東商銀"
    assert detect_bank_from_filename("202609cycle-statement.pdf") == "遠東商銀"


def test_detect_bank_from_filename_ubot():
    assert detect_bank_from_filename("聯邦銀行信用卡電子帳單.pdf") == "聯邦銀行"


def test_detect_bank_from_filename_chb():
    assert detect_bank_from_filename("彰化銀行2026年9月份信用卡帳單.pdf") == "彰化銀行"
    assert detect_bank_from_filename("彰銀信用卡帳單.pdf") == "彰化銀行"


def test_selects_chb_parser_for_chb_statement_filename():
    from parsers.chb import ChbParser
    from parsers.engine import get_parser_for_text
    assert isinstance(get_parser_for_text("", filename="彰化銀行信用卡對帳單.pdf"), ChbParser)


def test_balance_is_appended_to_statement_manifest(monkeypatch, tmp_path, capsys):
    import json
    import ctbc_balance
    from main import write_json_atomic
    path = tmp_path / "latest.json"
    write_json_atomic(path, {"schema_version": 1, "files": ["card_parsed.json"],
                             "balances": [{"balance": "999", "status": "success"}]})
    monkeypatch.setattr(ctbc_balance, "query_balance", lambda selector: Decimal("22542"))
    assert run_balance_query({"balance_query": {"ctbc": {"enabled": True}}}, output_dir=tmp_path)
    report = json.loads(path.read_text())
    assert report["files"] == ["card_parsed.json"]
    assert len(report["balances"]) == 1
    assert report["balances"][0]["balance"] == "22542"
    assert report["balances"][0]["currency"] == "TWD"
    assert report["balances"][0]["scope"] == "twd_deposit_total"
    assert report["balances"][0]["queried_at"]
    output = capsys.readouterr().out
    assert "中國信託臺幣存款餘額：NT$ 22,542.00" in output
    assert "=== 本次餘額查詢結果 ===" not in output


def test_failed_balance_preserves_statement_manifest_without_stale_amount(monkeypatch, tmp_path):
    import json
    import ctbc_balance
    from main import write_json_atomic
    path = tmp_path / "latest.json"
    write_json_atomic(path, {"files": ["card_parsed.json"], "balances": [{"balance": "999"}]})
    def fail(selector):
        raise ValueError("APP-1053")
    monkeypatch.setattr(ctbc_balance, "query_balance", fail)
    assert not run_balance_query({"balance_query": {"ctbc": {"enabled": True}}}, output_dir=tmp_path)
    report = json.loads(path.read_text())
    assert report["files"] == ["card_parsed.json"]
    assert report["balances"][0]["status"] == "error"
    assert report["balances"][0]["balance"] is None


def test_pipeline_prints_balance_after_cards_and_saves_it(monkeypatch, tmp_path, capsys):
    import json
    import yaml
    import main
    import ctbc_balance
    config = yaml.safe_load(Path("config.yaml").read_text())
    config["fubon"]["enabled"] = False
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True))
    downloads = tmp_path / config["storage"]["download_dir"]
    downloads.mkdir()
    (downloads / "CTBC_card_Estatement_11509.pdf").write_bytes(b"test")
    monkeypatch.setattr(main, "MailFetcher", lambda path: type("Fetcher", (), {
        "history": {}, "fetch_statements": lambda self: 0})())
    monkeypatch.setattr(main, "process_single_pdf", lambda *args, **kwargs: {
        "metadata": {"status": "success"}, "total_amount": 1234, "due_date": "2026-10-10"})
    monkeypatch.setattr(ctbc_balance, "query_balance", lambda selector: Decimal("22542"))
    assert main.run_pipeline(config_path, balance_banks=["ctbc"])
    output = capsys.readouterr().out
    assert "銀行: 中國信託\n本期應繳總金額: NT$ 1,234\n繳款截止日: 2026-10-10\n臺幣存款餘額: NT$ 22,542.00" in output
    assert output.count("臺幣存款餘額") == 1
    report = json.loads((tmp_path / "output/latest.json").read_text())
    assert report["files"] == ["CTBC_card_Estatement_11509_parsed.json"]
    assert report["balances"][0]["balance"] == "22542"


def test_current_deposit_balance_is_identified_under_bank_due_date(capsys):
    from main import print_statement_summaries
    print_statement_summaries([("彰化銀行", {"metadata": {"status": "success"},
        "total_amount": 1000, "due_date": "115/10/20"})], [
        {"bank": "chb", "bank_name": "彰化銀行", "status": "success", "balance": "0.00",
         "currency": "TWD", "scope": "twd_current_deposit_total"}])
    assert "繳款截止日: 115/10/20\n臺幣活期存款餘額: NT$ 0.00" in capsys.readouterr().out


def test_each_bank_balance_is_placed_after_its_own_due_date(capsys):
    from main import print_statement_summaries
    statements = [('玉山銀行', {'metadata': {'status': 'success'}, 'total_amount': 100, 'due_date': '115/10/10'}),
                  ('台新銀行', {'metadata': {'status': 'success'}, 'total_amount': 200, 'due_date': '115/10/20'})]
    balances = [{'bank': 'esun', 'bank_name': '玉山銀行', 'status': 'success', 'balance': '1234', 'currency': 'TWD'},
                {'bank': 'taishin', 'bank_name': '台新銀行', 'status': 'error', 'balance': None, 'error_code': 'verification_required'}]
    print_statement_summaries(statements, balances)
    output = capsys.readouterr().out
    assert '繳款截止日: 115/10/10\n臺幣存款餘額: NT$ 1,234.00' in output
    assert '繳款截止日: 115/10/20\n存款餘額: 待完成網銀驗證' in output


def test_unrecognized_payment_amount_is_not_reported_as_zero(capsys):
    from main import print_statement_summaries
    print_statement_summaries([("國泰世華", {"metadata": {"status": "success"},
        "total_amount": 0, "total_amount_found": False, "due_date": ""})], [])
    output = capsys.readouterr().out
    assert "本期應繳總金額: 未辨識（需人工確認）" in output
    assert "NT$ 0" not in output


def test_image_captcha_failure_is_reported_as_graphical_verification(capsys):
    from main import print_statement_summaries
    print_statement_summaries([("第一銀行", {"metadata": {"status": "success"},
        "total_amount": 10, "total_amount_found": True, "due_date": "2026/10/15"})],
        [{"bank": "first_bank", "bank_name": "第一銀行", "status": "error",
          "error_code": "captcha_recognition_failed"}])
    assert "圖形驗證碼無法確認，需人工處理" in capsys.readouterr().out
