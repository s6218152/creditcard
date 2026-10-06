"""Compatibility views over the centralized bank specification registry."""

from bank_specs import BANK_SPECS


MANUAL_BANKS = {
    key: (spec.name, spec.entry_url, spec.domains)
    for key, spec in BANK_SPECS.items()
    if spec.balance_module == "manual_bank_balance"
}
SUPPORTED_BANKS = set(BANK_SPECS)


def normalize_bank(value):
    aliases = {spec.name: key for key, spec in BANK_SPECS.items()}
    aliases.update({name.removesuffix("銀行"): key for name, key in list(aliases.items())})
    aliases.update({alias: key for key, spec in BANK_SPECS.items() for alias in spec.aliases})
    key = aliases.get(value, value.lower())
    if key not in SUPPORTED_BANKS:
        raise ValueError(f"不支援的銀行名稱或代碼：{value}")
    return key

AUTO_BANKS = {
    key: (spec.name, spec.entry_url, spec.domains)
    for key, spec in BANK_SPECS.items()
    if spec.login_form is not None
}
