"""Query supported banks through their existing authenticated browser readers."""

import argparse
import importlib
import sys
from datetime import datetime
from pathlib import Path

import yaml

from bank_registry import MANUAL_BANKS

BANKS = {"ctbc": ("中國信託", "ctbc_balance"), "sinopac": ("永豐銀行", "sinopac_balance")}

BANKS.update({key: (entry[0], "manual_bank_balance") for key, entry in MANUAL_BANKS.items()})


def run_queries(config: dict, interactive: bool = True, *, on_result=None, show_summary: bool = True, show_results: bool = True) -> bool:
    settings = config.get("balance_query", {})
    enabled = [key for key in settings if settings[key].get("enabled", False)]
    unknown = [key for key in enabled if key not in BANKS]
    if unknown:
        raise ValueError(f"尚未支援的餘額查詢銀行：{', '.join(unknown)}")
    if not enabled:
        return True
    if not interactive:
        print("[銀行餘額] 背景排程略過手動登入查詢；請執行 bank_balances.py 查詢。")
        return True
    if show_summary:
        print("\n=== 查詢各銀行即時存款餘額 ===")
    print(f"[銀行餘額] 執行環境：{sys.executable}", flush=True)
    results = []
    succeeded = True
    for key in enabled:
        name, module = BANKS[key]
        print(f"[{name}] 正在啟動本次查詢的 Chrome 並開啟登入頁…", flush=True)
        try:
            reader = importlib.import_module(module).query_balance
            selector = settings[key].get("balance_selector") or None
            balance = reader(key, selector) if key in MANUAL_BANKS else reader(selector)
            queried_at = datetime.now().astimezone().isoformat(timespec="seconds")
            currency = "TWD" if key == "ctbc" else getattr(balance, "currency", None)
            label = "臺幣活期存款餘額" if getattr(balance, "scope", None) == "twd_current_deposit_total" else "臺幣存款餘額"
            row = (f"{name}{label}：NT$ {balance:,.2f}" if currency
                   else f"{name}帳戶餘額：{balance:,.2f}（幣別依網銀頁面）")
            row += f"；查詢時間：{queried_at}"
            results.append(row)
            if not show_summary and show_results:
                print(row)
            if on_result:
                on_result({"bank": key, "bank_name": name, "status": "success",
                           "balance": str(balance), "currency": currency,
                           "scope": "twd_deposit_total" if key == "ctbc" else getattr(balance, "scope", "single_account"),
                           "queried_at": queried_at})
        except KeyboardInterrupt:
            print(f"[{name}餘額] 查詢已取消，已完成的結果已保留。")
            succeeded = False
            if on_result:
                on_result({"bank": key, "bank_name": name, "status": "cancelled", "balance": None,
                           "queried_at": datetime.now().astimezone().isoformat(timespec="seconds")})
            break
        except Exception as error:
            print(f"[{name}餘額] 查詢失敗：{error}")
            diagnostics = getattr(error, "diagnostics", [])
            error_code = getattr(error, "code", "query_failed")
            if any(item.get("verification_input_visible") for item in diagnostics):
                error_code = "verification_required"
            if diagnostics:
                import json
                print(f"[{name}頁面診斷] {json.dumps(diagnostics, ensure_ascii=False)}")
            succeeded = False
            if on_result:
                on_result({"bank": key, "bank_name": name, "status": "error", "balance": None,
                           "error_message": str(error), "error_code": error_code,
                           "diagnostics": diagnostics,
                           "private_diagnostics": getattr(error, "private_diagnostics", []),
                           "queried_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    if results and show_summary and show_results:
        print("\n=== 本次餘額查詢結果 ===")
        for result in results:
            print(result)
    return succeeded


def main() -> int:
    parser = argparse.ArgumentParser(description="查詢已啟用銀行的即時存款餘額")
    parser.add_argument("--config", default=str(Path(__file__).with_name("config.yaml")))
    parser.add_argument("--bank", choices=tuple(BANKS), action="append", help="只查指定銀行，可重複使用")
    args = parser.parse_args()
    try:
        with open(args.config, encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
        if not isinstance(config, dict):
            raise ValueError("設定檔必須是 YAML 對應表")
        if args.bank:
            configured = config.get("balance_query", {})
            config["balance_query"] = {
                key: {**configured.get(key, {}), "enabled": True} for key in args.bank
            }
        return 0 if run_queries(config) else 1
    except (ValueError, OSError, yaml.YAMLError) as error:
        print(f"查詢失敗：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
