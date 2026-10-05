from pathlib import Path

from bank_registry import SUPPORTED_BANKS


def validate_config(config: dict, base_dir: Path) -> None:
    if not isinstance(config, dict):
        raise ValueError("設定檔必須是 YAML 對應表")
    for section in ("mail", "storage"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"缺少 {section} 設定區塊")
    mail = config["mail"]
    for key in ("imap_server", "folder"):
        if not isinstance(mail.get(key), str) or not mail[key]:
            raise ValueError(f"mail.{key} 必須是非空字串")
    port = mail.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("mail.port 必須介於 1 至 65535")
    if mail.get("search_mode") not in (None, "latest_month", "fixed_days"):
        raise ValueError("mail.search_mode 不支援此值")
    days = mail.get("search_days", 90)
    if type(days) is not int or days <= 0:
        raise ValueError("mail.search_days 必須是正整數")
    filters = mail.get("local_filters")
    if not isinstance(filters, dict):
        raise ValueError("mail.local_filters 必須是對應表")
    for key in ("subjects", "banks", "exclude_subjects"):
        values = filters.get(key, [])
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError(f"mail.local_filters.{key} 必須是字串清單")
    for section, keys in ((mail, ("bank_attachment_patterns", "bank_pdf_passwords")),
                          (config["storage"], ("pdf_password_patterns",))):
        for key in keys:
            if not isinstance(section.get(key, {}), dict):
                raise ValueError(f"{key} 必須是對應表")
            if not all(isinstance(pattern, str) for pattern in section.get(key, {})):
                raise ValueError(f"{key} 的鍵必須是字串")
    attachment_patterns = mail.get("bank_attachment_patterns", {})
    if not all(isinstance(patterns, list) and all(isinstance(p, str) for p in patterns)
               for patterns in attachment_patterns.values()):
        raise ValueError("bank_attachment_patterns 的值必須是字串清單")
    for key in ("bank_pdf_passwords",):
        if not all(isinstance(value, str) for value in mail.get(key, {}).values()):
            raise ValueError(f"{key} 的值必須是字串")
    storage = config["storage"]
    if not all(isinstance(value, str) for value in storage.get("pdf_password_patterns", {}).values()):
        raise ValueError("pdf_password_patterns 的值必須是字串")
    root = base_dir.resolve()
    for key in ("download_dir", "output_dir", "history_file"):
        value = storage.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"storage.{key} 必須是非空路徑")
        target = (root / value).resolve()
        if not target.is_relative_to(root) or target == root:
            raise ValueError(f"storage.{key} 必須位於專案目錄內")
    if not isinstance(config.get("schedule", {}), dict):
        raise ValueError("schedule 必須是對應表")
    hours = config.get("schedule", {}).get("interval_hours", 12)
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0 < hours < float("inf"):
        raise ValueError("schedule.interval_hours 必須是有限正數")
    fubon = config.get("fubon", {})
    if not isinstance(fubon, dict) or type(fubon.get("captcha_retries", 5)) is not int or fubon.get("captcha_retries", 5) <= 0:
        raise ValueError("fubon.captcha_retries 必須是正整數")
    balances = config.get("balance_query", {})
    if not isinstance(balances, dict):
        raise ValueError("balance_query 必須是對應表")
    for bank, settings in balances.items():
        if bank not in SUPPORTED_BANKS:
            raise ValueError(f"尚未支援的餘額查詢銀行：{bank}")
        if not isinstance(settings, dict):
            raise ValueError(f"balance_query.{bank} 必須是對應表")
        if type(settings.get("enabled", False)) is not bool:
            raise ValueError(f"balance_query.{bank}.enabled 必須是布林值")
        if not isinstance(settings.get("balance_selector", ""), str):
            raise ValueError(f"balance_query.{bank}.balance_selector 必須是字串")
