import time
import sys
import yaml
import argparse
import logging
from datetime import datetime
from main import run_pipeline
from config_validation import validate_config
from pathlib import Path

logger = logging.getLogger(__name__)

def run_daemon(config_path="config.yaml"):
    """
    以背景守護程式 (Daemon) 模式運行，根據 config.yaml 設定的間隔時間定期執行。
    """
    # 讀取設定檔確認排程間隔
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    validate_config(config, Path(config_path).resolve().parent)
    interval_hours = float(config.get("schedule", {}).get("interval_hours", 12))

    interval_seconds = interval_hours * 3600
    
    print("=" * 60)
    print(f"信用卡帳單自動下載與解析守護程式啟動。")
    print(f"目前設定檢查間隔時間: {interval_hours} 小時 (即每 {interval_seconds} 秒檢查一次)")
    print("按下 Ctrl+C 可隨時退出程式。")
    print("=" * 60)

    try:
        consecutive_failures = 0
        while True:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{current_time}] === 開始執行排程檢查 ===")
            
            try:
                succeeded = run_pipeline(config_path, interactive=False)
                consecutive_failures = 0 if succeeded else consecutive_failures + 1
                if not succeeded:
                    logger.error("帳單流程失敗，連續失敗次數：%d", consecutive_failures)
            except Exception as e:
                consecutive_failures += 1
                logger.exception("排程執行失敗")
                print(f"排程執行中發生未預期錯誤: {e}")
            if consecutive_failures >= 3:
                logger.critical("帳單流程已連續失敗 %d 次，請檢查信箱與解析結果", consecutive_failures)
                
            next_run_time = datetime.fromtimestamp(time.time() + interval_seconds).strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 檢查完成。下次檢查時間: {next_run_time}")
            print(f"進入睡眠模式...")
            
            # 每隔 10 秒鐘微睡眠一次，以便能即時響應 Ctrl+C 退出訊號
            sleep_needed = interval_seconds
            while sleep_needed > 0:
                time.sleep(min(10, sleep_needed))
                sleep_needed -= 10
                
    except KeyboardInterrupt:
        print("\n偵測到鍵盤中斷指令 (Ctrl+C)，守護程式已安全關閉。")
        sys.exit(0)

if __name__ == "__main__":
    argument_parser = argparse.ArgumentParser(description="定期下載並解析信用卡帳單")
    argument_parser.add_argument("--config", default="config.yaml", help="設定檔路徑")
    argument_parser.add_argument("--once", action="store_true", help="只執行一次")
    args = argument_parser.parse_args()
    if args.once:
        print("以單次運行模式啟動...")
        sys.exit(0 if run_pipeline(args.config) else 1)
    else:
        run_daemon(args.config)
