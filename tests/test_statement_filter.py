import pytest

from statement_filter import latest_statement_flags


@pytest.mark.parametrize("filenames", [
    ["statement.pdf", "statement.pdf"],
    ["CBGCC-MTHLYSTMT_A.pdf", "CBGCC-MTHLYSTMT_B.pdf"],
    ["bank2026年8月.pdf", "bank2026年9月.pdf"],
])
def test_unknown_account_identity_preserves_every_statement(filenames):
    assert latest_statement_flags(filenames, [1, 2]) == [True, True]


def test_verified_identity_selects_latest_period_without_using_file_time():
    filenames = ["bank2026年8月_card1234.pdf", "bank2026年9月_card1234.pdf"]
    assert latest_statement_flags(
        filenames, [2, 1], identities=["bank:card1234", "bank:card1234"],
    ) == [False, True]


def test_distinct_verified_accounts_preserve_their_own_statements():
    assert latest_statement_flags(
        ["bank2026年8月.pdf", "bank2026年9月.pdf"], [1, 2],
        identities=["bank:card1234", "bank:card5678"],
    ) == [True, True]


def test_verified_identity_without_statement_period_preserves_every_file():
    assert latest_statement_flags(
        ["statement.pdf", "statement.pdf"], [1, 2], identities=["bank:card", "bank:card"],
    ) == [True, True]


def test_identity_count_must_match_filenames():
    with pytest.raises(ValueError, match="identities"):
        latest_statement_flags(["statement.pdf"], [1], identities=[])
