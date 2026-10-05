import datetime
from email.message import EmailMessage
import pytest
from mail_fetcher import (
    MailFetcher,
    get_search_cutoff_date,
    is_excluded_email,
    matches_email_filter,
    should_save_attachment,
    extract_fubon_statement_links,
    write_pdf_atomic,
)
from io import BytesIO
from pypdf import PdfWriter
from pathlib import Path
import yaml


@pytest.mark.parametrize("subject,sender,allowed", [
    ("國泰世華銀行信用卡2026年8月電子帳單", "國泰世華銀行 <notice@example.com>", True),
    ("國泰世華銀行信用卡2026年9月電子帳單", "notice@example.com", True),
    ("國泰世華銀行信用卡2027年01月電子帳單", "notice@example.com", True),
    ("國泰世華銀行信用卡2027年12月電子帳單", "notice@example.com", True),
    ("國泰世華銀行信用卡2027年13月電子帳單", "notice@example.com", False),
    ("國泰世華銀行信用卡2027年0月電子帳單", "notice@example.com", False),
    ("國泰世華銀行信用卡2027年1月電子帳單通知", "notice@example.com", False),
    ("Re: 國泰世華銀行信用卡2026年8月電子帳單", "notice@example.com", False),
    ("國泰世華銀行貸款通知", "notice@example.com", False),
    ("本期電子對帳單", "國泰世華銀行 <notice@example.com>", False),
    ("電子對帳單", "notice@bill.cathaybk.com.tw", False),
    ("彰化銀行2026年9月份信用卡帳單", "notice@example.com", True),
])
def test_cathay_subject_requires_statement_title_with_valid_year_month(subject, sender, allowed):
    config = yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())
    assert matches_email_filter(subject, sender, config["mail"]["local_filters"]) is allowed


def test_rejected_cathay_message_never_fetches_body(monkeypatch):
    config = yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())
    message = EmailMessage()
    message["Subject"] = "國泰世華銀行信用卡活動通知"
    message["From"] = "notice@cathaybk.com.tw"
    calls = []
    class Mail:
        def uid(self, command, *args):
            calls.append((command, args))
            if command == "search":
                return "OK", [b"123"]
            assert args[-1] == "(RFC822.HEADER)", "Excluded message body must not be fetched"
            return "OK", [(b"1 (UID 123)", message.as_bytes())]
    fetcher = MailFetcher.__new__(MailFetcher)
    fetcher.config, fetcher.history, fetcher.mail = config, {}, Mail()
    monkeypatch.setattr(fetcher, "connect", lambda: None)
    monkeypatch.setattr(fetcher, "disconnect", lambda: None)
    monkeypatch.setattr(fetcher, "save_history", lambda: None)
    assert fetcher.fetch_statements() == 0
    assert fetcher.history["123"]["excluded"] is True
    assert len(calls) == 2


def test_get_search_cutoff_date_latest_month():
    today = datetime.date.today()
    current_month_start = today.replace(day=1)
    previous_month_last_day = current_month_start - datetime.timedelta(days=1)
    expected = previous_month_last_day.replace(day=1).strftime("%d-%b-%Y")

    assert get_search_cutoff_date({"search_mode": "latest_month"}) == expected


def test_get_search_cutoff_date_search_days():
    days = 45
    expected = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%d-%b-%Y")
    assert get_search_cutoff_date({"search_days": days}) == expected


def test_matches_email_filter_with_ctbc_subject():
    subject = "中國信託信用卡電子帳單 - 7月"
    sender = "ctbcbank@example.com"
    local_filters = {
        "subjects": ["中國信託信用卡電子帳單", "信用卡對帳單"],
        "banks": ["中國信託"]
    }

    assert matches_email_filter(subject, sender, local_filters)


def test_matches_email_filter_with_chb_credit_card_statement():
    filters = {"subjects": ["信用卡帳單"], "banks": ["彰化銀行", "彰銀"]}
    assert matches_email_filter(
        "彰化銀行2026年9月份信用卡帳單", "彰化銀行 <service@bill.chb.com.tw>", filters
    )


def test_matches_email_filter_with_wildcard_subject():
    subject = "中國信託信用卡電子帳單2026"
    sender = "ctbcbank@example.com"
    local_filters = {
        "subjects": ["中國信託信用卡電子帳單*"],
        "banks": []
    }

    assert matches_email_filter(subject, sender, local_filters)


def test_english_mail_and_attachment_patterns_ignore_case():
    filters = {"subjects": ["Statement"], "banks": []}
    assert matches_email_filter("YOUR MONTHLY STATEMENT", "x", filters)
    config = {"bank_attachment_patterns": {"台新銀行": ["TSB_Creditcard_Estatement_*.pdf"]}}
    assert should_save_attachment("tsb_creditcard_estatement_1.pdf", "台新銀行", "", config)


def test_chb_attachment_filter_keeps_card_statement_only():
    config = {"bank_attachment_patterns": {"彰化銀行": ["*信用卡對帳單.pdf"]}}
    subject = "彰化銀行2026年9月份信用卡帳單"
    assert should_save_attachment("彰化銀行2026年9月份信用卡對帳單.pdf", subject, "", config)
    assert not should_save_attachment("彰銀綜合對帳單.pdf", subject, "彰化銀行", config)


def test_write_pdf_atomic_preserves_previous_file_on_bad_payload(tmp_path):
    target = tmp_path / "bill.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    write_pdf_atomic(target, buffer.getvalue())
    original = target.read_bytes()
    with pytest.raises(Exception):
        write_pdf_atomic(target, b"%PDF invalid")
    assert target.read_bytes() == original
    assert target.stat().st_mode & 0o077 == 0


def test_matches_email_filter_with_sender_bank_name():
    subject = "本期帳單通知"
    sender = "中國信託信用卡客服 <service@ctbcbank.com>"
    local_filters = {
        "subjects": ["信用卡對帳單"],
        "banks": ["中國信託"]
    }

    assert matches_email_filter(subject, sender, local_filters)


def test_excludes_bank_of_taiwan_deposit_statement():
    filters = {"exclude_subjects": ["*臺灣銀行存款電子對帳單*"]}
    assert is_excluded_email("臺灣銀行存款電子對帳單", filters)
    assert not matches_email_filter(
        "臺灣銀行存款電子對帳單", "service@example.com", filters
    )


def test_matches_email_filter_with_skbank_subject():
    filters = {"subjects": [], "banks": ["新光銀行"]}
    assert matches_email_filter(
        "新光銀行2026年9月信用卡電子帳單(HTML5)", "service@example.com", filters
    )


def test_matches_email_filter_with_feib_subject():
    filters = {"subjects": [], "banks": ["遠東商銀"]}
    assert matches_email_filter(
        "遠東商銀115年09月信用卡消費明細及帳單", "service@example.com", filters
    )


def test_matches_email_filter_with_ubot_subject():
    filters = {"subjects": [], "banks": ["聯邦銀行"]}
    assert matches_email_filter(
        "聯邦銀行信用卡電子帳單(2026年09月)", "service@example.com", filters
    )


def test_should_save_attachment_taishin_pattern():
    mail_config = {
        "bank_attachment_patterns": {
            "台新銀行": ["TSB_Creditcard_Estatement_*.pdf"]
        }
    }

    assert should_save_attachment("TSB_Creditcard_Estatement_202608.pdf", "台新銀行電子帳單", "", mail_config)
    assert not should_save_attachment("Other_Attachment.pdf", "台新銀行電子帳單", "", mail_config)


def test_should_save_attachment_esun_pattern():
    mail_config = {
        "bank_attachment_patterns": {
            "玉山銀行": ["ESUN_Estatement_*.pdf"]
        }
    }

    assert should_save_attachment("ESUN_Estatement_202608.pdf", "玉山銀行電子帳單", "", mail_config)
    assert not should_save_attachment("Other_Attachment.pdf", "玉山銀行電子帳單", "", mail_config)


def test_should_save_attachment_sinopac_pattern():
    mail_config = {
        "bank_attachment_patterns": {
            "永豐銀行": ["永豐銀行信用卡帳單.pdf"]
        }
    }

    subject = "永豐銀行信用卡2026年09月份電子帳單通知"
    assert should_save_attachment("永豐銀行信用卡帳單.pdf", subject, "", mail_config)
    assert not should_save_attachment("Other_Attachment.pdf", subject, "", mail_config)


def test_extract_fubon_statement_links_from_html_email():
    message = EmailMessage()
    message.set_content("請使用電子帳單連結")
    message.add_alternative(
        '<a href="https://fbmbill.taipeifubon.com.tw/client/pdf/abcdef123456">下載帳單明細（PDF）</a>',
        subtype="html",
    )

    assert extract_fubon_statement_links(message) == [
        "https://fbmbill.taipeifubon.com.tw/client/pdf/abcdef123456"
    ]


def test_mark_fubon_link_downloaded_is_persisted_once(monkeypatch):
    fetcher = MailFetcher.__new__(MailFetcher)
    link = "https://fbmbill.taipeifubon.com.tw/client/pdf/abcdef123456"
    fetcher.history = {
        "123": {"fubon_links": [link], "fubon_downloaded_links": []}
    }
    saves = []
    monkeypatch.setattr(fetcher, "save_history", lambda: saves.append(True))

    fetcher.mark_fubon_link_downloaded(link)
    fetcher.mark_fubon_link_downloaded(link)

    assert fetcher.history["123"]["fubon_downloaded_links"] == [link]
    assert saves == [True]
