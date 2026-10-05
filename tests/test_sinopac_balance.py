from decimal import Decimal

import pytest

from ctbc_balance import parse_balance_text
from sinopac_balance import BALANCE_LABELS, is_sinopac_url


def test_sinopac_balance_label_variants():
    assert parse_balance_text("存款餘額\n125,680", BALANCE_LABELS) == Decimal("125680")
    assert parse_balance_text("帳面餘額：NT$ 1,234.50", BALANCE_LABELS) == Decimal("1234.50")


def test_sinopac_multiple_accounts_are_ambiguous():
    with pytest.raises(ValueError):
        parse_balance_text("帳戶餘額\n100\n存款餘額\n200", BALANCE_LABELS)


def test_sinopac_url_check():
    assert is_sinopac_url("https://mma.sinopac.com/MemberPortal/Member/MMALogin.aspx")
    assert not is_sinopac_url("https://mma.sinopac.com.evil.example/")
