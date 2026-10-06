import importlib

from bank_specs import BANK_SPECS, CAPTCHA_IMAGES, LOGIN_FORMS, identify_statement
from bank_registry import AUTO_BANKS, MANUAL_BANKS, SUPPORTED_BANKS


def test_registry_views_are_derived_from_complete_specs():
    assert SUPPORTED_BANKS == set(BANK_SPECS)
    assert set(AUTO_BANKS) == set(LOGIN_FORMS)
    assert set(MANUAL_BANKS) == {
        key for key, spec in BANK_SPECS.items() if spec.balance_module == "manual_bank_balance"
    }
    assert set(CAPTCHA_IMAGES) <= set(LOGIN_FORMS)


def test_balance_modules_expose_query_entrypoint():
    for spec in BANK_SPECS.values():
        assert callable(importlib.import_module(spec.balance_module).query_balance)


def test_statement_identity_drives_name_and_parser_together():
    spec = identify_statement("CBGCC-DAILYSTMT_202610.pdf")
    assert (spec.name, spec.parser) == ("星展", "dbs")
