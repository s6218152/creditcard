# 信用卡帳單與銀行存款餘額查詢

安裝相依套件：

```bash
python -m pip install -r requirements.txt
```

開發與測試環境另安裝：

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
ruff check .
```

銀行、登入表單、驗證碼與帳單辨識的共用資料集中在 `bank_specs.py`；新增銀行時應先擴充該 registry，再實作對應的 reader／adapter，避免在多個檔案重複維護名稱與網址。

## 郵件信任與本機資料留存

`config.yaml` 預設只接受 `mail.local_filters.trusted_sender_domains` 中的銀行寄件網域，並拒絕無法對應銀行規則的 PDF。銀行若更換寄件服務，請先確認完整寄件地址，再新增其網域；不要關閉寄件者驗證作為長期解法。

下載的 PDF、解析 JSON 與郵件歷史預設保留 730 天，由 `storage.retention_days` 調整；設為 `0` 可停用自動清理。失敗頁面的可見文字只寫入本機 `output/private_bank_diagnostics.json`，不會放進 `latest.json` 或 Telegram 附件，且會遮罩常見身分證、電子郵件、帳號與幣別金額格式。

下載與解析信用卡帳單：

```bash
python main.py
```

主流程完成帳單處理後，會依 `config.yaml` 的 `balance_query` 設定，逐一開啟已啟用銀行的網銀查詢存款餘額。中國信託會登入後自動進入臺幣存款查詢頁並讀取合計餘額；其他銀行會自動填入登入資料，有圖形驗證碼的十間銀行也會在本機辨識、填入並確認欄位內容，再送出一次登入，不需要終端機按 Enter。OTP 若由銀行要求，仍需在 Chrome 完成。每間銀行可用 `enabled: false` 關閉查詢，或以 `balance_selector` 指定唯一金額元素。查詢失敗會繼續下一間銀行，最後彙整成功取得的餘額與各自查詢時間，結束碼 1 表示有查詢失敗。背景模式 `daemon.py` 略過互動查詢，`daemon.py --once` 則會執行此步驟。

只查存款餘額，略過帳單下載與解析：

```bash
python bank_balances.py
python bank_balances.py --bank ctbc --bank sinopac
```

目前中信、永豐、富邦、第一、玉山、遠東、新光、台新、國泰、彰銀及華南已實測登入與餘額讀取。國泰讀取「臺幣帳戶總額」；玉山加總「活存支存餘額」與「定存本金餘額」；永豐只加總存款總覽中的 TWD 活存與綜存定存；富邦讀取我的存款各臺幣帳戶「即時餘額」；遠東讀取活期與定期性存款明細；第一讀取帳戶總覽的「臺幣存款」總額；台新讀取臺幣總覽的存款總計；新光讀取我的帳戶總覽「臺幣存款/定存」，依銀行頁面不含可轉讓定期存單 NCD；華南完成兩階段登入後，自動開啟左側「帳務查詢」，只加總幣別明確標示為「新台幣」的活期存款「帳上餘額」。各銀行查詢時間不同，金額與精度依當次網銀頁面顯示，外幣與約當臺幣資產不混入臺幣存款。上海已實測讀取臺幣所有帳號的帳上餘額；銀行若改回覆「交易暫停、禁止」，程式會記錄錯誤，不沿用前次金額。聯邦若為信用卡會員、未開通存款查詢，會顯示此原因。若華南強制要求數位存款帳戶基本資料與 EDD 問卷更新，程式會提示先由本人完成，不會代填。

聯邦銀行目前依使用者要求停用餘額查詢。星展會等待非同步載入的「活期存款」卡片，讀取幣別 `TWD` 的「帳戶餘額」；初始畫面的開戶提示不再被當成最終狀態。

查詢中國信託存款帳戶的即時「帳戶餘額」：

```bash
python ctbc_balance.py
```

程式會開啟中國信託官方網銀。若本機 `.env` 設有 `CTBC_ID`、`CTBC_USER_ID`、`CTBC_PASSWORD`，會自動填入並送出一次登入；未設定時由你自行登入。驗證碼或 OTP 請在 Chrome 完成。登入完成後，程式自動點選「臺幣存款」，讀取銀行顯示的「臺幣帳戶餘額」合計，不需要切換到單一帳戶或回終端機按 Enter。外幣存款及約當臺幣金額不計入這個結果。若登入或 OTP 未完成，會等待最多 120 秒；失敗會顯示錯誤，不會自動重送帳密。瀏覽器 session 不會保存到檔案。

中國信託改從[新版網銀登入頁](https://www.ctbcbank.com/twrbc/twrbc-general/ot001/010)登入，以新版表單的 `formcontrolname`（`custIxd`、`userIxd`、`pxd`）辨識三個欄位與「登入」按鈕。舊版官網的登入入口可能轉回原頁或回覆 APP-1053，因此不再使用舊版 `#btnHeaderLogin`／`#btnGeneralLogin`。若頁面載入失敗，不會自動重送帳密；可執行 `python ctbc_balance.py --manual-login` 自行登入。送出登入只代表已點擊按鈕，實際登入成功以 Chrome 顯示帳戶頁為準。

若銀行頁面使用不同排版，可用 `--balance-selector 'CSS 選擇器'` 指定唯一的金額元素。程式只讀取當前頁面的文字，不呼叫銀行的內部 API，也不執行轉帳。

永豐銀行可用相同方式查詢：

```bash
python sinopac_balance.py
```

永豐 MMA 網銀會自動讀取存款總覽，依幣別 TWD 加總各帳戶的「餘額」與「綜存定存」，排除外幣；帳號重複、資料未載入或金額缺漏時會記錄錯誤。

## 新增銀行查詢入口

| 銀行 | `--bank` 代碼 | 官方入口 |
| --- | --- | --- |
| 新光銀行 | `skbank` | [官方網站](https://nbank.skbank.com.tw/) |
| 台北富邦 | `fubon` | [官方網站](https://accessible.taipeifubon.com.tw/BFS/common/Index.faces) |
| 第一銀行 | `first_bank` | [官方網站](https://ibank.firstbank.com.tw/NetBank/indexPortlet.html?locale=zh_TW) |
| 玉山銀行 | `esun` | [官方網站](https://ebank.esunbank.com.tw/index.jsp?obj=ebank) |
| 遠東商銀 | `feib` | [官方網站](https://ebank.feib.com.tw/netbank/html/pages/jsp/Logon/mainIndex6.jsp) |
| 聯邦銀行 | `ubot` | [官方網站](https://www.ubot.com.tw/home) |
| 星展銀行 | `dbs` | [官方網站](https://internet-banking.dbs.com.tw/) |
| 上海商銀 | `shanghai` | [官方網站](https://ebank.scsb.com.tw/) |
| 台新銀行 | `taishin` | [官方網站](https://my.taishinbank.com.tw/TIBNetBank/) |
| 彰化銀行 | `chb` | [官方網站](https://www.chb.com.tw/chbnib/faces/login/Login/Login_Action) |
| 國泰世華 | `cathay` | [官方網站](https://www.cathaybk.com.tw/MyBank/Home) |
| 華南銀行 | `hncb` | [官方網站](https://netbank.hncb.com.tw/) |

例如只查玉山與台北富邦：

```bash
python bank_balances.py --bank esun --bank fubon
```

新增銀行使用 `.env` 的 `BANK_ID`、`BANK_USER_ID`、`BANK_PASSWORD` 自動填寫。有圖形驗證碼時會先自動辨識；無法可靠辨識時，最多重新產生兩次圖片（共三張），全部發生在送出登入之前。三張都失敗會顯示 `captcha_recognition_failed`，結束該銀行查詢並繼續下一間，不會停著等手動輸入。程式不重送驗證碼或登入資料被拒絕的登入。永豐、富邦與第一銀行的特定重複登入提示會確認本次登入；台新明確要求重建前次連線時，只允許一次重新登入，並在送出前補檢查重建表單是否清空欄位。登入成功後自動尋找存款查詢入口並讀取餘額，無需終端機按 Enter。未讀到唯一餘額時會記錄錯誤；不會任意挑選帳戶、不會加總不同幣別。

```dotenv
BANK_ID=your_identity
BANK_USER_ID=your_user_id
BANK_PASSWORD=your_default_password
# 僅此銀行改用獨立密碼，其餘欄位沿用預設：
ESUN_BANK_PASSWORD=your_esun_password
```

獨立設定前綴為 `SKBANK_BANK_`、`FUBON_BANK_`、`FIRST_BANK_BANK_`、`ESUN_BANK_`、`FEIB_BANK_`、`UBOT_BANK_`、`DBS_BANK_`、`SHANGHAI_BANK_`、`TAISHIN_BANK_`、`CHB_BANK_`、`CATHAY_BANK_`、`HNCB_BANK_`、`SINOPAC_BANK_`，可覆寫 `ID`、`USER_ID`、`PASSWORD` 任一欄位。中國信託繼續使用獨立的 `CTBC_ID`、`CTBC_USER_ID`、`CTBC_PASSWORD`。

星展（`dbs`）只需要 `DBS_BANK_USER_ID` 與 `DBS_BANK_PASSWORD`，不讀取或填寫身分證；也可沿用預設的 `BANK_USER_ID` 與 `BANK_PASSWORD`。星展的自訂密碼欄位會逐字輸入並核對遮蔽字元數量，避免整串填入時只接收到最後一碼。若登入後顯示「請洽分行開立存款帳號」，程式會記錄 `deposit_access_unavailable`。

圖形驗證碼使用本機 [ddddocr](https://github.com/sml2h3/ddddocr) 的兩個模型。一般要求完整文字、大小寫一致，每個字元至少有一個模型信心達 95%，並檢查表單長度與字元格式。已接入彰銀、永豐、富邦、華南、台新、上海、第一、新光、聯邦與遠東新版入口。新光四張字元圖在記憶體中去除外框、拼接；字型造成模型不一致時，可由本機 Tesseract 去除彩色背景、放大後，以兩種分段模式確認完整文字，且 ddddocr 須以至少 50% 字元信心支持字形；輸入保留 Tesseract 兩次一致辨識的大小寫，不任意修改。此交叉辨識需要本機 `tesseract`，macOS 可用 `brew install tesseract` 安裝。上海讀取背景圖片；聯邦先開啟網銀登入抽屜；第一依官方 image map 點擊登入。辨識後逐字輸入、觸發鍵盤事件並讀回確認，不只設定欄位值。OCR 在獨立程序執行，圖片只經記憶體處理，不存檔、不送外部服務。每張 OCR 最多 20 秒；模型程序異常、相依套件缺漏與辨識信心不足會分別顯示原因。銀行拒絕驗證碼或登入資料時立即停止，不持續重送。

彰銀（`chb`）會關閉登入頁的「銀行公告」。登入後程式點左側「新臺幣帳戶」→「新臺幣活期餘額」，選「全部帳號」並點「確定」，讀取「帳面餘額」小計。輸出使用「臺幣活期存款餘額」與 `twd_current_deposit_total`，不計入可用餘額、交換票金額、定存或外幣。已用 `venv/bin/python bank_balances.py --bank chb` 實測驗證碼自動填入、登入與讀取餘額，全程無需手動操作。

專案有 `venv` 與 `.venv` 兩個 Python 環境；套件需安裝到實際執行的環境。若缺少 OCR 套件，程式會明確顯示提示。可用以下指令固定使用同一環境：

```bash
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python main.py
```

全部銀行的餘額或驗證狀態都會放在各銀行信用卡「繳款截止日」下一行，並儲存至 `output/latest.json` 的 `balances`。`verification_required` 表示待完成銀行驗證；`captcha_recognition_failed` 表示驗證碼未能可靠辨識、尚未送出登入；`captcha_rejected` 表示銀行拒絕驗證碼；`credentials_rejected` 才代表銀行明確回覆登入資料不符；`login_unconfirmed` 表示尚未確認登入成功；`deposit_access_unavailable` 表示未開通存款查詢；`customer_information_update_required` 表示需先由本人完成銀行基本資料或 EDD 問卷；`bank_query_unavailable` 表示銀行暫停查詢；`previous_session_conflict` 表示重建連線後仍無法登入。失敗金額為 null。

## 中國信託 Chrome 啟動方式

中信改由程式啟動本機安裝的 Chrome，再透過本機 CDP 連到 Chrome 的預設 session，不使用 Playwright `launch()` 加 `new_context()` 的啟動方式。CDP 僅綁定 `127.0.0.1`，每次使用獨立的臨時 profile，結束或取消後關閉該瀏覽器並清除臨時 profile。不會使用或複製你平常 Chrome 的 profile，也不修改瀏覽器指紋或保存 cookies。

```bash
cd /Users/xiaomi/workspace/creditcard
.venv/bin/python bank_balances.py --bank ctbc
```

已以此指令實測全自動登入與讀取臺幣存款合計餘額，結束碼為 0，全程不需要終端機輸入。銀行若要求驗證碼或 OTP，仍需在 Chrome 完成。

中信信用卡「繳款截止日」正下方加上即時餘額：

```bash
.venv/bin/python main.py --bank ctbc
```

帳單完成並取得餘額後，會把 `臺幣存款餘額: NT$ 22,542.00` 放在中國信託的「繳款截止日」正下方，再顯示下一間銀行的帳單。同一份 `output/latest.json` 會保留信用卡 `files` 清單，並新增 `balances`，記錄銀行、金額（十進位字串）、幣別 TWD、範圍 `twd_deposit_total` 與查詢時間。查詢失敗則記錄錯誤，金額為 null，不沿用上一筆餘額。

啟動與連線方式參考 [Playwright CDP 文件](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp) 與 [Chrome 獨立 profile 的除錯規範](https://developer.chrome.com/blog/remote-debugging-port)。
