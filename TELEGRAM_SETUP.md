# Telegram 雲端排程

每月 1、15 日台灣時間 12:00 啟動 GitHub Actions，完成後傳送帳單、餘額與必要提醒。
另附 ZIP，包含終端輸出、latest.json 與本次成功解析的完整 JSON（含交易明細）。
GitHub 排程可能延遲，並不保證 12:00 整收到。

## 啟用

1. 建議推到私人 GitHub repository。
2. 在 Telegram 找官方 @BotFather，使用 /newbot 建立 Bot，取得 Token。
3. 開啟你的 Bot 聊天並按 Start。透過 Bot API getUpdates 取得自己的 chat.id。
4. 在 GitHub repository → Settings → Secrets and variables → Actions 新增：
   - TELEGRAM_BOT_TOKEN：Bot Token。
   - TELEGRAM_CHAT_ID：自己的 chat.id。
   - REPORT_ENV：多行 dotenv 內容，填入本機 .env 的帳单所需設定與銀行登入設定。
     支援各銀行的 BANK_ID / BANK_USER_ID / BANK_PASSWORD 及獨立設定；請勿把真實值提交進 Git。
5. 推送後，到 Actions → Monthly Telegram report → Run workflow 手動測試一次。
   排程檔必須存在於預設分支，才能自動執行。
   手動執行時可填 banks（例如 chb esun），僅測試指定銀行的餘額；
   信用卡帳單仍全部處理。留白與每月自動排程均查詢全部已啟用銀行。

## 銀行登入與錯誤

雲端使用 macos-15-intel runner 的 Google Chrome 執行登入與圖形驗證碼辨識流程。
中信與上海網銀不支援 Linux，因此使用銀行列出的 macOS 作業系統；不偽裝瀏覽器或作業系統。
成功取得的餘額會照常傳送；確認需驗證的銀行會另外標示。
登入逾時不能直接判定為 OTP，會標示登入／驗證未完成，保留原始原因。
只有實際看到一次性密碼輸入欄位時，才標示需要人工驗證。
失敗時附頁面結構診斷（官方主機、欄位數量、驗證碼圖片載入狀態），
公開 GitHub 日誌不包含帳密、帳號、完整網址、頁面文字或截圖。
私人 ZIP 另保留失敗時的畫面文字（已遮罩環境中的帳密），用來確認提示內容。
通知不提供遠端 OTP 輸入，需人工處理的銀行本次無法取得餘額。
雲端 IP 可能被銀行拒絕；圖形驗證碼辨識及無人登入仍需實際雲端測試。

每次使用新的 runner，重新從信箱下載可取得的最新帳單，不保留先前本機下載快取。
部分銀行失敗仍傳送已完成結果，Actions 同時顯示失敗狀態。
程式最長執行 40 分鐘，逾時會嘗試傳送已產出的結果。
帳密與 Token 不輸出到 Actions 日誌，也不將報告上傳成 GitHub artifact。
Telegram 會收到財務資料，請確認 Chat ID 是你自己的私人聊天。
