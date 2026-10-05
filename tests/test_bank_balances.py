from decimal import Decimal

import ctbc_balance
import sinopac_balance
from bank_balances import run_queries


def test_queries_both_banks_and_reports_time(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(ctbc_balance, "query_balance", lambda selector: calls.append(selector) or Decimal("100.50"))
    monkeypatch.setattr(sinopac_balance, "query_balance", lambda selector: calls.append(selector) or Decimal("200"))
    assert run_queries({"balance_query": {
        "ctbc": {"enabled": True, "balance_selector": "#ctbc"},
        "sinopac": {"enabled": True, "balance_selector": "#sinopac"},
    }})
    assert calls == ["#ctbc", "#sinopac"]
    output = capsys.readouterr().out
    assert "中國信託臺幣存款餘額：NT$ 100.50" in output
    assert "永豐銀行帳戶餘額：200.00" in output
    assert "查詢時間：" in output


def test_failed_bank_does_not_block_next_bank(monkeypatch, capsys):
    def fail(selector):
        raise ValueError("銀行維修")
    monkeypatch.setattr(ctbc_balance, "query_balance", fail)
    monkeypatch.setattr(sinopac_balance, "query_balance", lambda selector: Decimal("200"))
    assert not run_queries({"balance_query": {"ctbc": {"enabled": True}, "sinopac": {"enabled": True}}})
    output = capsys.readouterr().out
    assert "銀行維修" in output
    assert "永豐銀行帳戶餘額：200.00" in output


def test_cancellation_preserves_prior_results(monkeypatch, capsys):
    def cancel(selector):
        raise KeyboardInterrupt
    monkeypatch.setattr(ctbc_balance, "query_balance", lambda selector: Decimal("100"))
    monkeypatch.setattr(sinopac_balance, "query_balance", cancel)
    assert not run_queries({"balance_query": {"ctbc": {"enabled": True}, "sinopac": {"enabled": True}}})
    assert "中國信託臺幣存款餘額：NT$ 100.00" in capsys.readouterr().out
