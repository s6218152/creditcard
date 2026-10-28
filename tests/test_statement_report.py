from datetime import date

import pytest

from statement_report import classify_statements, normalized_due_date, render_report, statement_identity


@pytest.mark.parametrize('raw,expected', [
    ('115/10/30', '2026-10-30'), ('2026/10/30', '2026-10-30'),
    ('2026-10-30', '2026-10-30'), ('115/2/30', None), ('未知', None), ('無需繳款', None),
])
def test_due_dates_normalize_without_guessing(raw, expected):
    assert normalized_due_date(raw) == expected


def test_identity_requires_unique_full_explicit_account_not_last_four_or_filename():
    for text in ['卡號末四碼：1234', '信用卡卡號：123456******1234',
                 '信用卡帳號：123456789012\n信用卡帳號：123456789099']:
        assert statement_identity(text, '測試銀行', '2026年10月.pdf')['identity_hash'] is None
    first = statement_identity('信用卡帳號：1234-5678-9012\n帳單月份：115年10月', '測試銀行', 'x.pdf')
    second = statement_identity('信用卡帳號：123456789012', '測試銀行', '2026年09月.pdf')
    assert first['identity_hash'] == second['identity_hash']
    assert '123456' not in first['identity_hash']
    assert first['statement_period'] == '2026-10'
    assert statement_identity('信用卡帳號：123456789012', '其他銀行', 'x.pdf')['identity_hash'] != first['identity_hash']


def test_latest_requires_account_identity_and_keeps_older_documents():
    rows = classify_statements([
        {'bank_name': 'A', 'identity_hash': 'verified-a', 'statement_period': '2026-09', 'due_date': '115/09/30'},
        {'bank_name': 'A', 'identity_hash': 'verified-a', 'statement_period': '2026-10', 'due_date': '115/10/30'},
        {'bank_name': 'A', 'identity_hash': 'other-account', 'statement_period': '2026-09'},
        {'bank_name': 'A', 'statement_period': '2026-09'},
    ], today=date(2026, 10, 28))
    assert [r['selection_status'] for r in rows] == ['history', 'latest', 'latest', 'unverified']
    assert rows[0]['date_status'] == 'past_due_unconfirmed'
    assert rows[1]['date_status'] == 'due_soon'
    assert len(rows) == 4


def test_structured_report_selects_latest_without_status_or_balance_time_labels():
    def statement(period, due, identity='verified-a', amount=75):
        return {'bank_name': '測試銀行', 'status': 'success', 'statement_period': period,
                'due_date': due, 'identity_hash': identity, 'total_amount': amount}
    text = render_report({'statements': [
        statement('2026-09', '115/09/30'), statement('2026-10', '115/10/30'),
        statement(None, '115/09/23', None), statement(None, '115/10/28', None, -100),
    ], 'balances': [{'bank_name': '測試銀行', 'status': 'success', 'balance': '87', 'currency': 'TWD',
                     'queried_at': '2026-10-28T12:00:00+08:00'}]}, today=date(2026, 10, 28))
    assert '2026/10/30' in text and '2 天後截止' in text
    assert '歷史帳單（不代表已繳清）' not in text
    assert '銀行: 測試銀行' in text and text.count('銀行: 測試銀行') == 1
    assert '完整帳單保留附件' in text
    assert '今天截止' not in text  # Negative amounts do not trigger payment reminders.
    assert text.count('NT$ 87.00') == 1
    assert '餘額查詢時間' not in text
    assert '帳單狀態' not in text


def test_invalid_and_unrecognized_amounts_never_become_zero_payments():
    for value in (None, 'NaN', 'Infinity', 'bad'):
        text = render_report({'statements': [{'bank_name': 'A', 'status': 'success', 'total_amount': value}]})
        assert '未辨識（需人工確認）' in text
        assert 'NT$ 0' not in text


def test_report_shows_only_latest_month_per_bank_without_account_identity():
    def row(bank, period, due, amount, filename=''):
        return {'bank_name': bank, 'status': 'success', 'statement_period': period,
                'due_date': due, 'total_amount': amount, 'filename': filename}
    text = render_report({'statements': [
        row('新光銀行', '2026-09', '115/09/30', 2000),
        row('新光銀行', '2026-10', '115/10/30', 75),
        row('台北富邦', '2026-09', '無需繳款', -19529),
        row('台北富邦', '2026-10', '無需繳款', -6174),
        row('星展', None, '2026/10/16', 1051), row('星展', None, '2026/11/13', 5341),
        row('遠東商銀', None, '115/10/13', 5609, '321633_Mon_28_Sep_2026_1534_202609cycle-statement.pdf'),
        row('遠東商銀', None, '115/11/13', 4672, '322623_Wed_28_Oct_2026_1534_202610cycle-statement.pdf'),
    ]}, today=date(2026, 10, 28))
    for name in ['新光銀行', '台北富邦', '星展', '遠東商銀']:
        assert text.count('銀行: ' + name + '\n') == 1
    for old_amount in ('2,000', '-19,529', '1,051', '5,609'):
        assert '本期應繳總金額: NT$ ' + old_amount not in text
    assert '本期應繳總金額: NT$ 75' in text
    assert '本期應繳總金額: NT$ -6,174' in text
