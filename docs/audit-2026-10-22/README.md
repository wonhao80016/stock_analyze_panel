# 全專案審查與修正計畫（2026-10-22）

> 審查基準：HEAD `1724b12`（分支 `feat/market-filter-volume-patterns`）。所有行號都對這個版本；之後改過的檔案請以函式名稱定位。
> 本頁是進度入口與修正計畫。問題明細在 [findings.md](findings.md)，dead code 在 [dead-code.md](dead-code.md)，冗餘檔案在 [redundant-files.md](redundant-files.md)，方法、工具實跑結果與被反駁的宣稱在 [verification.md](verification.md)。
> 與舊稽核的關係：[docs/audit-2026-09-24](../audit-2026-09-24/README.md) 與 [docs/code-review-2026-09-22.md](../code-review-2026-09-22.md) 中仍未修正的條目，凡本次重新確認仍存在的，都已編入本清單，並在 findings.md 的「既有文件」欄註記對應條目。依該欄計算（欄內文字含「未見於」或「沒有被記錄」的不算；引用舊稽核的檔案、章節或根因編號，或引用其 README 段落的都算），97 項對到舊稽核、16 項對到舊 code review。之後以本目錄為追蹤正本，修好時兩邊都要更新。

## 目前結論

- 去重後共 337 項問題：P0 3、P1 6、P2 60、P3 268。另有 26 項宣稱經驗證不成立，列在 verification.md，沒有編號。
- 3 個 P0 都是安全問題，在「未設密碼」這個預設狀態下就成立：跨來源可操作本機 API（B001）、`POST /api/screener/run` 可讀寫主機任意檔案（B002）、策略程式碼的安全檢查可繞過並在後端執行（B003）。三項都以腳本實際重現。
- 6 個 P1：Docker 容器一啟動就失敗（B004）；反向代理部署時可偽造來源 IP（B005）；回測與挖掘有 4 個未來函數或口徑混用（B006–B009），已發佈的挖掘結果與回測績效可能偏高。
- Dead code 581 項：完全沒有引用 285、A 股／TickFlow 遺留且台股模式到不了 137、只有測試引用 111、重複實作 31、其他 17。後端 191 個路由中 38 個沒有任何呼叫者。
- 冗餘檔案與文件 55 項。
- 測試現況：後端全套會在 `test_taiwan_research_context.py` 耗盡記憶體崩潰（B057）；排除該檔後 4316 passed、7 failed。測試曾寫入正式 `data/`（`data/tmp` 有 42 個測試留下的空目錄，`data/backend.log.3` 混有測試 log）。前端 tsc 0 錯誤、vitest 505 項全過。
- 尚未執行：依賴套件的已知弱點掃描（需要把套件名稱與版本送到外部弱點資料庫，待決定）。

## 修正順序規劃

排序依據：先止住可被利用的安全漏洞；再讓測試能可靠地驗收；然後依「會不會產生錯誤的金融數字或寫壞資料」排序；顯示、效能與清理放在後面。每一階段開工前，先確認下方對應的決策已定。

| 階段 | 主題 | 範圍 | 前置與理由 |
|---|---|---|---|
| 0 | 安全止血 | B001、B002、B003、B005、B023、B026、B030、B035、B018 | 需要 D1–D4。三個 P0 在預設狀態下就能被利用，修法都局部，不必等測試基線。 |
| 1 | 測試基線與隔離 | B057，以及 findings.md P3「測試」區 19 項 | 全套目前跑不完，且會寫入正式資料；之後每一階段都靠測試驗收。 |
| 2 | 部署與打包 | B004、B010、B069，以及 P3「建置與部署」區 8 項 | Docker 路徑完全不可用；桌面版缺 AI 提示詞與 Linux GUI 後端。 |
| 3 | 回測與挖掘可信度 | P1：B006–B009；P2：B015、B016、B017、B036、B037；P3「回測與挖掘」區 22 項 | 需要 D5。會產生錯誤的績效與錯誤的挖掘發佈。 |
| 4 | 資料寫入與一致性 | B031、B032、B047、B048、B044、B051、B052、B024、B012、B025、B060 | 會寫壞或遺失設定與資料，或讓快取讀到舊資料。上線前要備份。 |
| 5 | 台股資料與分析 | B046、B045、B050、B040、B041、B042、B043、B055、B020、B021、B027、B011；指標口徑 B125、B126、B128–B131 | 需要 D8。含舊稽核第 5 階段的大盤基準卡（RC15）與券資比（A07#1）。指標口徑改動要全量重新發佈。 |
| 6 | 即時行情、監控與自訂訊號 | B033、B054、B028、B029、B038、B039、B013、B014、B022、B019、B034 | 需要 D6。告警不觸發或靜默失效，使用者不會察覺。 |
| 7 | 前端顯示與狀態 | B058、B063、B061、B062、B065、B066、B059、B067、B068，以及 P3「前端」區 77 項 | 多為顯示錯誤或狀態殘留，單一畫面影響。 |
| 8 | 效能 | B049、B064、B056、B053 | 個股頁每 10 秒觸發全市場下載；選股每次讀全部歷史。 |
| 9 | Dead code 與 A 股／TickFlow 遺留移除 | dead-code.md 全部 | 需要 D7。放在功能修正之後，避免與修正衝突；B002 的端點在階段 0 先刪。 |
| 10 | 文件與冗餘檔案 | redundant-files.md 全部，以及 P3「文件」區 18 項 | 需要 D9。最後統一處理，舊稽核文件同時改為歷史紀錄。 |

其餘 P3（「台股資料與分析」43、「策略、指標與監控」32、「API 層」21、「核心、資料源與排程」11、「後端服務」11、「即時行情」6）隨所屬模組的階段一起評估，但仍一項一個提交，不順手合併（CONTRIBUTING.md 1.3）。

## 需要先決定的事項

每項附建議；決定後記在本節，並在對應階段實作。

| 編號 | 問題 | 建議 | 替代方案 | 影響階段 |
|---|---|---|---|---|
| D1 | `POST /api/screener/run`（B002）要刪除還是改寫？ | 刪除端點與 `ScreenerService` 的字串拼接路徑。前端只有定義、沒有呼叫者。 | 保留，改成欄位白名單加參數化查詢。 | 0 |
| D2 | 自訂與 AI 產生的 Python 策略（B003）怎麼處理？ | 預設停用「寫入並載入任意 Python」（`/api/strategies/code/save`、`/ai/save`，以及載入 `data/strategies/{custom,ai}`），需要在設定中明確開啟且只限本機；AST 檢查改當語法提示，不當安全邊界。 | 改在隔離的子行程執行策略（成本高）。 | 0 |
| D3 | 未設密碼時的存取政策（B001、B005、B023）？ | 只放行 127.0.0.1 與 ::1，內網必須先設密碼；CORS 改成同源，不再用 `*`；`X-Forwarded-For` 只在設定的可信代理清單內採信，取最右側的不受信任位址。 | 保留內網放行，但首次啟動強制在本機設密碼。 | 0 |
| D4 | AI 預設端點（B018）？ | 預設留空，未設定就停用 AI；文件提供範例。 | 改成 `.env.example` 現有的 DeepSeek 預設。 | 0 |
| D5 | B006–B009 修正後，既有挖掘發佈的策略與已呈現的回測績效怎麼處理？ | 以修正後的程式重新評估既有挖掘發佈的策略，未通過門檻的下架；回測頁標示口徑變更日。 | 只標示變更日，不重新評估。 | 3 |
| D6 | 台股模式的分時訊號規則（B028、B029）？ | 台股模式回報「不可用」並拒絕存檔（CONTRIBUTING.md 第 4 節的 fail-closed）。 | 實作台股分鐘資料取數。 | 6 |
| D7 | A 股／TickFlow 遺留的移除範圍？ | 全面移除：本分支定位台股，TickFlow SDK 已停用（`get_client()` 一律拋錯）。 | 保留非台股相容路徑，只刪完全不可達的部分。 | 9 |
| D8 | 指標口徑（B125、B126、B128–B131：MACD 柱 ×2、布林樣本標準差、年化方式、價格不動時的 KD／RSI、交叉判定的浮點容差）？ | 改為台股常見口徑，一次提高 `INDICATOR_DEFINITION_VERSION` 並全量重新發佈。具體公式與舊稽核 A02#4、A08#6、A04#8 一起決定。 | 維持現狀，只在畫面標示口徑。 | 5 |
| D9 | 冗餘資產與重複文件？ | 刪除 `screenshots/`、`gui-test-screenshots/`、`community-qr-code.jpg`；`frontend/vite.config.d.ts` 移出版控；`docs/deploy-password.md` 併入 `docs/deployment.md`；README、`操作說明書.md`、`docs/features.md` 三份收斂成「README 簡介、操作說明書為功能正本」。 | 保留截圖並改放 Release 附件。 | 10 |

另外待決：是否執行依賴弱點掃描（pip-audit、pnpm audit）。這會把套件名稱與版本送到外部弱點資料庫。

已定案的決策（2026-10-22，使用者採用建議方案）：

- D1：刪除 `POST /api/screener/run`、`ScreenerService` 的字串拼接查詢路徑，以及前端未使用的 `api.screenerRunCustom`。
- D2：預設停用「儲存並載入使用者 Python 策略」（`/api/strategies/code/save`、`/ai/save` 與 `data/strategies/{custom,ai,composite}` 的載入）；需要時由設定明確開啟，且只限本機請求。AST 檢查不再視為安全邊界。
- D3：未設密碼時只放行 127.0.0.1 與 ::1，區網請求要先設密碼；CORS 改為同源；`X-Forwarded-For` 只在設定的可信代理清單內採信，取最右側的不受信任位址。
- D4：AI 服務位址預設留空，未設定即視為 AI 未啟用；設定頁提示需填寫。
- D5（2026-10-24）：B006–B009 修正後，以修正後的程式重新評估既有挖掘發佈的策略，未通過門檻的下架。
- D8（2026-10-24）：指標改為台股常見口徑，一次提高 `INDICATOR_DEFINITION_VERSION` 並全量重新發佈。MACD 柱為 DIF − 訊號線（不乘 2）；布林通道為 20 日中線 ±2 倍母體標準差；`annual_vol_20d` 用對數報酬、20 日樣本標準差 ×√252；KD 在 H9 = L9 時沿用前一日 K；RSI 在漲跌皆為 0 時為 50；交叉判定加浮點容差，等於 0 的那根不算穿越（Polars 路徑容差為 max(1e-9, 1e-9×|收盤|)；矩陣路徑任一輸入為 float32 時，容差下限為 2⁻²²×max(|左|,|右|)）；跌停翻紅為「原始最低價觸及跌停價（容差 0.005）且原始收盤高於昨收」。
- D6（2026-10-24）：台股模式的分時訊號只在設定了自訂分鐘資料插件時回報可用；預設台股設定回報「不可用」並附原因，存檔時拒絕，已存的規則在列表標示警告。四個分時訊號、評估器與自訂插件路徑保留；TickFlow 能力分支留待 D7 於階段 9 一併處理。
- D7（2026-10-25）：全面移除 A 股／TickFlow 遺留，包括 TickFlow SDK 整合、A 股資料路徑與台股模式不可達的分支。
- Mining（2026-10-25）：策略挖掘已沒有任何正式入口，使用者決定整組刪除；D5 的重新評估與恢復端點、B148 隨之結案。原本附帶的資料目錄行程鎖保留為 `DataDirProcessLock`。
- D9（2026-10-25）：採建議方案：刪除 `screenshots/`、`gui-test-screenshots/`、`community-qr-code.jpg`；`frontend/vite.config.d.ts` 移出版控；`docs/deploy-password.md` 併入 `docs/deployment.md`；README、`操作說明書.md`、`docs/features.md` 收斂為「README 簡介、操作說明書為功能正本」。
- B180（2026-10-24）：融資未公布的 16:30–21:40 期間，AI 研究仍帶入大盤與產業脈絡，並附資料狀態，要求引用時註明。

## 各階段做法與完成條件

### 階段 0：安全止血

1. 認證邊界（D3）：B001（CORS 萬用來源）、B023（未設密碼時內網放行）、B005（`X-Forwarded-For` 取最左值）。這三項都在 `backend/app/main.py` 的中介層與 `backend/app/api/auth.py`，要一起改，並補測試：外部 Origin 的預檢被拒、偽造 XFF 無效、未設密碼時內網請求被拒。
2. B002（D1）：刪除端點，或改成白名單加參數化查詢。測試：注入字串無法讀寫檔案。
3. B003（D2）：停用或隔離任意 Python 策略的寫入與載入。測試：以屬性鏈或別名繞過檢查的策略（例如 `numpy.ctypeslib` 的 `CDLL`、polars 的 `write_csv`、把 `exec` 指派給變數後再呼叫）無法存檔，也不會被載入。
4. B030：Windows 系統通知改成不經可展開字串的方式，或完整跳脫告警文字。B035：Telegram token 不得出現在 log（httpx 的 INFO log 會記錄含 token 的 URL）。B018（D4）。B026：`auth.json` 改為原子寫入，讀取損毀時要 fail-closed，不可視為未設密碼。
5. 完成條件：每項都有在未修正程式上會失敗的測試，修正後通過；後端受影響測試與 ruff 通過。

### 階段 1：測試基線與隔離

1. B057：修 `test_taiwan_research_context.py` 的 mock，讓 `read_range` 依日期過濾，全套才能跑完。
2. findings.md P3「測試」區的 19 項：`conftest.py` 隔離 `settings.data_dir`、證券主檔單例、`backend.log` 與 `data/tmp`；註冊 `integration` 標記並預設排除；`deny_network` 補擋 DNS；修 `test_taiwan_chip_history.py` 的日期腐化；修 `test_auth_env_bootstrap.py` 在設了 `DATA_DIR` 時寫入 `auth.json`；`test_mining_manager.py` 改為等待最後一筆事件。
3. 完成條件：不設任何環境變數直接執行 `uv run pytest tests -q`，全套跑完、不連網、不寫正式 `data/`，記下基線數字；前端 `pnpm build`、`pnpm test` 維持全過。

### 階段 2：部署與打包

1. B004：Docker 內 `app/__init__.py` 找得到 `VERSION`（修正路徑計算，或把 `VERSION` 複製到對應位置）。
2. B069：PyInstaller 規格打包 `app/strategy/prompts/*.md`。找不到提示詞時改為明確報錯：目前 `ai_generator._get_guide` 只記警告就回空字串，`prompt_builder._load_doc` 則連警告都沒有。
3. B010：Linux 桌面產物補上 pywebview 的 GUI 後端。
4. P3「建置與部署」區 8 項，例如 `.env.example` 缺少 compose 會讀取的鍵。
5. 完成條件：Docker 建置後實際啟動容器並呼叫 `/health`；桌面版打包後實際開窗。

### 階段 3：回測與挖掘可信度

1. B006：組合模式開盤買進只能使用開盤當下已知的倉位與資金。
2. B007：「觸發後下一分鐘成交」的選日不得依賴當日收盤才確定的訊號。
3. B008：`vwap_bias` 的分子分母使用同一價格尺度。
4. B009：mining 交叉評估只能用勝出者選取窗之後的測試窗。
5. B015、B016、B017、B036、B037，以及 P3「回測與挖掘」區 22 項。
6. 完成條件：每項都有可在未修正程式上失敗的測試（CONTRIBUTING.md 5.3）；在同一份資料上跑固定的回測與 mining，記錄修正前後的差異。
7. 上線：依 D5 處理既有挖掘發佈的策略。

### 階段 4：資料寫入與一致性

1. B031、B032：`preferences.save` 加鎖並改為原子寫入，解析失敗時不得以空設定為基底寫回。
2. B047、B048：增量 enriched 發佈改走 `EnrichedPublication`（換 generation、互斥、Windows 重試）。
3. B044：資料包匯入完成後發佈 enriched 並刷新記憶體快取。
4. B051、B052：籌碼補抓遇到失敗日時，不再寫入後面的日期，或記錄缺口供重抓；融資寫入遵守 21:40 發布閘門，且要求兩個交易所的資料齊全。
5. B024、B012；B025 與 B060 是同一根因（告警以時間戳當唯一鍵），前後端一起改成唯一 ID。
6. 上線注意：會改寫資料的修正，先把會被改寫的分區備份到 repo 外，並避開 16:30 與 21:40 的排程。

### 階段 5：台股資料與分析

1. B046：個股頁「所屬大盤基準」改為讀已存的指數日資料（舊稽核 RC15）。
2. B045、B050：即時路徑的券資比（舊稽核 A07#1）。
3. B040：異常診斷的 5 日量額基期要排除無成交或停止交易的列。B041、B042、B043：異常診斷「未評估」與「沒有異常」要區分，並正確傳給 AI 研究。B055：研究脈絡改按交易日計數。
4. B020、B021（關鍵價位）、B027、B011。
5. 指標口徑（D8）：B125、B126、B128–B131。改動要提高 `INDICATOR_DEFINITION_VERSION` 並全量重新發佈（CONTRIBUTING.md 3.4），發佈前備份。
6. P3「台股資料與分析」區 43 項，以及「策略、指標與監控」區中與指標相關者。

### 階段 6：即時行情、監控與自訂訊號

1. B033：自訂資料源缺 realtime 資料集時，不得落入已刪除的 TickFlow 分支。B054：單一無法解析的代號不得讓整輪台股即時行情歸零。
2. B028、B029（D6）。B038：編輯規則條件後重置觸發與冷卻狀態。B039：批次事件的冷卻鍵要包含標的。
3. B013、B014、B022：自訂訊號新建時不得覆蓋同名訊號、刪除或停用前檢查引用、`distance_*` 欄位的計算順序。B019：移除「中位數判斷百分數」的啟發式（CONTRIBUTING.md 3.1 禁止）。B034：截圖匯入改認台股代號。
4. P3「即時行情」區 6 項，以及「策略、指標與監控」「後端服務」「API 層」中屬於這些模組的項目。

### 階段 7：前端顯示與狀態

1. B058（成交額少顯示 10 倍）、B063（融券大減標成「融券激增」）、B061、B062（選股頁條件套用與重置）、B065（切換代號後 AI 報告沿用前一檔）、B066（自選篩選單位）、B059（預覽對話框日 K 區間凍結）、B067（AI 設定「測試」寫入未儲存的表單）、B068（資料來源頁路由無效）。
2. P3「前端」區 77 項。
3. 完成條件：`pnpm build`、`pnpm test` 通過，並手動檢查載入、空資料、錯誤、切換與即時刷新狀態（CONTRIBUTING.md 第 7 節）。

### 階段 8：效能

1. B049、B064：個股頁不再每次即時下載全市場法人與融資表，改用快取或已存資料。
2. B056：選股只讀需要的分區。B053：即時輪詢重用 provider 與 HTTP client。
3. 完成條件：在同一資料規模下，記錄修改前後的耗時與請求數（CONTRIBUTING.md 6.3）。

### 階段 9：Dead code 與 A 股／TickFlow 遺留移除

1. 範圍依 D7 決定。順序：沒有呼叫者的端點 → 整個沒用的模組 → A 股／TickFlow 遺留分支 → 只有測試引用的程式（連同其測試）→ 未使用的依賴。
2. 每批刪除前重新搜尋一次，刪除後跑後端全套測試與 `pnpm build`。

### 階段 10：文件與冗餘檔案

1. 依 D9 處理 redundant-files.md 的 55 項，以及 findings.md P3「文件」區 18 項。
2. 在 `docs/audit-2026-09-24/README.md` 與 `docs/code-review-2026-09-22.md` 開頭加註：後續追蹤改到本目錄；並更正已過時的狀態（A06#10、G05#7、A03#6）。

## 執行守則

1. 一個問題一個提交，先寫能在未修正程式上失敗的測試，再修到通過（CONTRIBUTING.md 1.4、第 9 節）。
2. 階段 1 完成前，跑後端測試一律把 `DATA_DIR` 指到暫存目錄，並先複製 `data/taiwan/security_master.parquet` 進去；否則部分測試會連網並寫入正式資料。
3. 會改寫資料的修正：先備份會被改寫的分區到 repo 外，避開 16:30 與 21:40 排程。開發後端若以 `uvicorn --reload` 執行，存檔就會立刻作用在正式資料上。
4. 指標定義改變要提高 `INDICATOR_DEFINITION_VERSION`（CONTRIBUTING.md 3.4）。
5. 每次提交前：受影響的後端測試、`uv run ruff check` 受影響的檔案、前端 `pnpm build`、`git diff --check`。

## 進度

| 階段 | 狀態 | 備註 |
|---|---|---|
| 0 安全止血 | 已完成（2026-10-23） | B001、B005、B023：`bedae43`；B002：`6451def`；B003：`d754f6e`；B018：`b6f75a1`；B030：`f90cf95`；B035：`a41233e`；B026：`b625690`。合併後階段 0 相關測試 324 passed。後續事項見下方 |
| 1 測試基線與隔離 | 已完成（2026-10-23） | B057：`c0587fc`；B223–B228、B231–B235、B238–B241：`99931b1`；B236、B237：`3ae2f62`；B229、B230：`e6fab1c`。不設環境變數的全套基線（在不含 data/ 的副本上）：4485 passed、7 xfailed、12 deselected，約 9.5 分鐘，峰值記憶體約 0.8 GB，無外連，正式 data/ 未被寫入。後續事項見下方 |
| 2 部署與打包 | 程式已完成（2026-10-24），待實機驗證 | B004：`bfaf74e`；B069、B334：`de8086d`；B010：`7e5eff8`；B222：`cdab715`；B242、B243：`62d8e9b`；B332、B333：`e45ca41`；B335、B336：`76fd791`。本機沒有 Docker、PyInstaller 與 Inno Setup，打包與容器只做了靜態驗證，實機步驟見下方 |
| 3 回測與挖掘可信度 | 已完成（2026-10-25）；第二輪 B097–B108、B111–B114 的提交見下方紀錄；B148 隨階段 9 移除 mining 結案 | B006、B007、B008、B015、B016、B017、B036、B037、B109：`70725a3`；B009、B110、B145、B146、B147 與 D5：`7aab380`。合併驗證 1747 passed。正式 data 的 679 個日 K 分區都有 raw_close |
| 4 資料寫入與一致性 | 已完成（2026-10-24） | B012：`8448d4c`；B024：`63ac041`；B031、B032：`efd63da`；B025、B060：`162a670`；B047、B048、B044：`19999ef`；B051、B052：`4f0ef75`。合併後的後端全套：4727 passed、7 xfailed、12 deselected，0 failed。上線步驟與後續事項見下方 |
| 5 台股資料與分析 | 已完成（2026-10-24） | 5A 指標口徑（D8）：`5c25f3c`；5B 關鍵價位：`05073b1`；5C 詳細頁與籌碼：`3d43f37`；5E 資料同步與 store：`9a45b33`；5F 選股、市場統計、自選頁：`59a8fde`；5D 異常診斷與 AI 研究：`26abe38`。合併後的後端全套：5058 passed、1 xfailed、12 deselected，0 failed；前端 tsc 通過，vitest 525 passed。上線步驟與後續事項見下方 |
| 6 即時行情、監控與自訂訊號 | 已完成（2026-10-24） | 6A 即時行情：`6774d2f`；6D 截圖匯入與標的搜尋：`30c7787`；6B 監控規則與 D6：`63cc3e2`；6C 自訂訊號、AI 策略與自訂資料源：`2bd7544`。合併後的後端全套：5287 passed、1 xfailed、12 deselected，0 failed；前端 tsc 通過，vitest 547 passed。上線步驟與後續事項見下方 |
| 7 前端顯示與狀態 | 程式已完成（2026-10-24），待瀏覽器檢查 | 7A 版面與共用函式庫：`4551cf8`；7B 圖表與個股頁：`5c6193a`；7C 選股與自選：`dd4534d`；7D 看板、監控中心、策略頁：`d48dc76`；7E 設定頁：`78d7e89`。合併後的後端全套：5330 passed、1 xfailed、12 deselected，1 failed（`test_stage2_master_refresh` 偶發，單獨重跑通過）；前端 tsc 通過，vitest 757 passed，`pnpm build` 通過（2026-10-24，echarts chunk 1.03 MB 有大小警告）。手動檢查清單見下方 |
| 8 效能 | 已完成（2026-10-25） | B049、B064：`ba5a9be`；B056、B053：`08f997f`。合併後的後端全套：5374 passed、1 skipped、1 xfailed、12 deselected，0 failed；前端 tsc 通過，vitest 770 passed，`pnpm build` 通過。量測與後續事項見下方 |
| 9 Dead code 與遺留移除 | 已完成（2026-10-25） | 第一輪：`1b7d67c`（前端）、`4505586`（後端）；第二、三輪：`b073f5f`（ext 設定快取修正）、`da2efe8`（前端）、`2e87ed3`（後端，含台股 ETF asset_type 修正）；mining 移除：`a99262d`。最後的後端全套 4700 passed、1 xfailed，0 failed；前端 tsc 通過，vitest 771 passed，`pnpm build` 通過。後續事項見下方 |
| 10 文件與冗餘檔案 | 已完成（2026-10-25） | 提交紀錄見 git log；連結檢查 0 錯；後續事項見下方 |

### 階段 0 留下的後續事項

審查時確認存在、但不阻擋提交的項目，排入之後的階段：

1. `auth.json` 的暫存檔在 Linux 上先以預設權限建立，之後才 chmod 0600，中間有極短的可讀窗口；應改為建檔時就用 0600（`backend/app/services/auth.py` 的 `_save`）。
2. 前端登入頁把 503（`AUTH_STORE_CORRUPT`）當成未設密碼，顯示「設密碼」表單（`frontend/src/pages/Auth.tsx`）。
3. 執行期間 `auth.json` 才損毀時，已登入的會話仍可用到下次讀檔；寫入沒有 fsync；手動修檔存成帶 BOM 的 UTF-8 會被判為損毀。
4. 前端沒有顯示策略的 `load_errors`，使用者 Python 策略被停用時，畫面看不出原因。
5. `backend/pyproject.toml` 的 `winotify>=1.1` 沒有上限；新版若改了屬性名稱，B030 的修正會失效（目前 `uv.lock` 鎖在 1.1.0）。
6. macOS 通知（`notify_adapter.py` 的 `_notify_osascript`）只跳脫 `"`、沒跳脫 `\`，可能同類注入，需另案審查。
7. FinMind provider 把含 token 的 URL 寫進資料的 `source_url` 欄位；`backend/app/api/settings.py` 約 678 行把含 URL 的例外訊息直接回傳給 API。
8. 未帶 Origin 的跨站 GET（圖片、導覽）仍會放行；若有會改狀態的 GET 端點，可加 `Sec-Fetch-Site` 檢查。
9. `ScreenerResult` 資料類別在 B002 之後只剩測試使用（階段 9 處理）。

### 階段 1 留下的後續事項

1. 網路攔截只在 pytest 主行程生效，spawn 出的子行程（回測 worker、lorentzian 測試）不受攔截；目前這些子行程只讀本機資料。
2. `backend/app/services/mining_manager.py` 先寫終態狀態、後寫終態事件（約 206–259 行），測試已改為等兩者都到位；產品端 SSE 會自行補送終態事件，但順序本身仍可改善。
3. `backend/tests/backtest/test_worker_process.py` 偶發子行程以 0xC0000005 結束，單獨重跑會通過，原因未查明。
4. 合成證券主檔只含約 30 個代號；新測試用到其他代號時要補進 conftest 的 `_synthetic_instruments`。
5. conftest 約 440 行，可拆成 `pytest_plugins` 載入的隔離與網路攔截兩個模組。

### 階段 2 待實機驗證與後續事項

待實機驗證：

1. Docker：`docker compose up --build` 後 `curl http://127.0.0.1:3018/health`，應回 VERSION 的版本；確認容器啟動時沒有重新安裝開發工具。
2. Linux 桌面版：以 `platforms=linux` 跑 release workflow，確認 Qt 步驟與 QtWebEngineProcess 檢查通過，並在有桌面的 Linux 上實際開出視窗。
3. Windows 桌面版：本機打包後確認 `_internal/app/custom` 存在（AI 提示詞在階段 9 已不再打包）；不帶 `/D` 執行 ISCC，產物版本應為 VERSION；先裝到非預設目錄再升級，確認沿用原目錄。

後續事項：

1. 找不到 AI 提示詞時仍會靜默回空字串（`ai_generator._get_guide` 只記警告，`prompt_builder._load_doc` 沒有任何紀錄），應改為明確報錯。
2. 容器內 `_PROJECT_ROOT` 為 `/`；若 `.env` 把 CACHE_DIR 設成相對路徑，會落在掛載卷之外。
3. `scripts/upgrade_check.py`：docstring 的「Read-only」應改為「不修改 refs 與工作區」；標題「文本冲突」應改為「合并冲突」；`git diff --name-only` 的中文路徑可加 `-c core.quotepath=false`。
4. cp950 解碼的回歸測試只在系統編碼為 cp950 的機器上有效。

### 階段 3 第一輪留下的後續事項

1. vwap_bias 在股數型事件（分割、面額變更、減資）之前的 K 棒，仍偏移該事件的成交量調整比例；要精確需要未調整股數或 volume_factor。
2. 缺 raw_close 現在一律報錯（即時報價列與指數除外）；即時資料若某檔當日 raw_close 為空，該檔當天會被價格上下限排除。
3. 全量模式開啟分鐘成交時，會先多跑一次日線回測做預讀；大股票池的實際耗時未實測。
4. D5：下架流程若在搬移策略檔之後、寫 backlink 之前失敗，下一輪重試後 restore 只取最新紀錄，可能只接回 backlink；永久性錯誤會一直停在 retry；沒有唯讀的查詢入口；restore 不檢查 `ALLOW_USER_PYTHON_STRATEGIES`。
5. 挖掘演算法升為 mining-v3，所有舊 run 的候選都不能再發佈。
6. B148（每週自動挖掘沒有接線）未處理，接線等於在背景自動執行重型任務，待使用者決定是否啟用。
7. 回測撮合仍在同一根 K 棒處理開盤跳空停損的釋放，屬慣例；嚴格來說集合競價下單時尚不知開盤價。

### 階段 3 第二輪留下的後續事項

合併後的後端全套：4673 passed、7 xfailed、12 deselected，0 failed。

1. 開盤即漲停、收盤未鎖住的開板日，矩陣沒有當日漲跌停價，偵測不到；分鐘成交價重算後若落在鎖住的漲停價，進場不會再檢查一次。
2. 期末未平倉（`end_unclosed`）已從交易數、勝率、平均報酬分開統計；`select_stats` 的白名單尚未包含 `unclosed_at_end` 與 `sample_sizing`，用 `required_stats` 篩選時會被拿掉。
3. `get_minute_range` 掃描整個分鐘目錄，任一天分區損壞都會讓所有分時圖回 500；同目錄若混入不帶時區的分區，讀取時會平移 8 小時。
4. 成交稀少的股票分鐘資料常被判為不完整而退回日線，結果偏保守。
5. walk-forward 的 B113 父行程監看只在 Windows 實測；Linux 依標準庫機制推論。
6. 階段 3 第一輪的 `70725a3` 讓關閉基本過濾的策略仍要求 raw_close，已在第二輪修正。

### 階段 4 上線步驟與後續事項

上線步驟：

1. 上線前把 `data/kline_daily_enriched/`、`data/.matrix_generation_stock.json`、`data/.taiwan_enriched_factors.json` 備份到 repo 外，並避開 16:30 與 21:40 的排程。
2. 上線後在設定頁按一次「回補」：已存在的單邊融資分區與舊的籌碼缺口不會自動修復。

後續事項：

1. `secrets_store.py`（約 40、59 行）、`strategy/custom_signals.py`（約 95 行）、`strategy/monitor_rules.py`（約 87 行）、`strategy/config.py`（約 80 行）仍以非原子的 `write_text` 寫檔。
2. preferences 的寫入鎖只在同一個行程內有效；`set_financial_sync_time`、`api/strategy.py` 約 128 行、`services/mining_candidates.py` 約 349 行的「先 get 再 set」在鎖外，並行時仍可能遺失一筆清單或字典的更新。
3. preferences.json 損壞時，寫入 API 回 503（`PREFERENCES_STORE_UNAVAILABLE`），前端沒有對應提示；讀取端仍回預設值。
4. 部分發佈中途失敗會重試一次全量；重試也失敗時，讀取端會被擋到下一次成功發佈。全量接手未完成狀態時，判斷是否接手的讀取不在鎖內，有極窄的競態，要徹底修需改 `enriched_generation.py`。
5. 籌碼 store 為空且第一天就失敗時，回補會中止；訊息已註明「未完成」與未嘗試的天數，再按一次回補即可。
6. 即時推播（SSE）的告警沒有帶 id；之後若要從推播直接刪除，需在 `quote_service.py` 補上 id。
7. `latest_enriched_date` 仍會採計沒有 `part.parquet` 的空分區目錄；B024 之前留下的空目錄還在。
8. `services/detail_service.py` 約 762 行在取不到 data_dir 時退回相對路徑 `Path("data")`。

### 階段 5 上線步驟與後續事項

上線步驟：

1. `INDICATOR_DEFINITION_VERSION` 由 2 升為 3，下次啟動或每日更新時會全量重寫所有 enriched 分區，完成前讀取端會暫時回 503。上線前備份 `data/kline_daily_enriched/`、`data/.matrix_generation_stock.json`、`data/.taiwan_enriched_factors.json`，並避開 16:30 與 21:40 的排程。
2. MACD 柱變為原本的一半；使用者自訂策略或訊號若以 `macd_hist` 數值當門檻，需自行調整（`data/` 下的自訂內容未檢查）。

後續事項：

1. A 股盤中增量路徑的交叉只比對昨天一根；昨天落在線上（差值為 0）時，穿越要等下一次全量計算才會判到。
2. 矩陣路徑與 Polars 路徑的交叉容差在相對差值 1e-9 到 2.4e-7 之間仍有空隙；台股檔位取整後實務上碰不到。
3. B206：升降單位表沒有歷史日期維度，缺可靠的歷史規則來源，未修改。
4. B221：盤中量比口徑不一致的實際位置在 `taiwan/live_enriched.py` 的 `_scale_volume_ratio`，未處理；B219 的市值顯示在詳細頁 `StockInfoBar`，未處理。
5. 上櫃股的大盤基準多半顯示 unavailable：每日管線只回補加權指數，櫃買指數只有開過 o00 圖表才會存下。
6. 詳細頁的交易所判定直接讀 store 的私有 `_data_dir` 與 `date=*/part.parquet` 路徑；store 改分區格式時會靜默變成 unavailable，應在 store 補公開方法。
7. 法人與融資的 5 日窗口以已存分區計，不是交易日曆；兩所都回空表而略過的交易日不會被察覺。
8. 異常診斷每次請求仍做一次全市場計算，沒有「只算指定股票」的入口（B181 剩餘部分）。
9. `newest_rows_per_symbol` 快取未命中時仍讀遍所有分區；備援源（FinMind／Yahoo）寫入日線 store 時沒有來源標記（B208 剩餘部分）；B189 的排程時間仍寫死 16:30；ETF 清單在 profile 與 snapshot 各下載一次。
10. 官方月表重試依賴官方 adapter 的私有 `_fetch_month`；官方站整個失效時，第一個月仍要約 48 秒才改用備援源。
11. 更新鎖只擋同一個行程，CLI 另開的行程不受限；live 路徑的 K 線仍是未復權價（B027 剩餘部分）。
12. AI 用語掃描是最後一道防線，仍可能漏掉改寫過的推薦或目標價說法。

### 階段 6 上線步驟與後續事項

上線步驟：

1. 自訂資料源的 realtime 資料集若映射了 `change_pct`、`turnover_rate` 等比例欄位，必須在 `units` 宣告 `percent` 或 `decimal`，否則拒絕載入。升級後到設定頁的資料源錯誤橫幅點「編輯修正」補上即可（`docs/custom-data-source.md` 有遷移說明）。
2. 台股模式下含分時訊號的既有監控規則會在列表顯示警告且不會觸發（D6）；台股指數規則只接受價格與漲跌幅類條件。
3. 策略目錄中與 Python 模組同名的策略檔（例如 `random.py`、`signal.py`）改為載入失敗並提示改名。

後續事項：

1. TickFlow 能力分支（`kline_sync._tickflow_intraday_support`）已不可到達，留待 D7 移除。
2. 監控重啟後的回填以 `alert_store.list_recent` 為準，單日告警超過 5000 筆時最早的觸發會漏回填；條件從昨收一路成立到今日時，重啟過的程序會在今日開盤再告警一次。
3. 台股指數的欄位限制只認 `t00.TWSE`、`o00.TPEX`；偏好清單若加入其他台股指數，不受此限制。台股指數沒有歷史 enriched，均線類條件無法使用。
4. 自選截圖匯入會把「2025年度」「2025年報」中的 2025 抽成代碼並預設勾選。
5. 插件型資料源重載時立即關閉舊實例，沒有租借語意；資料源 YAML 的 `name` 與檔名不同時，「編輯修正」會回 404。
6. `quote_service.stop()` 仍可能阻塞 API 最多 10 秒；系統通知佇列在程序結束時未送出的部分會遺失；記錄無法解析代號的集合沒有上限。
7. 自訂資料源缺 realtime 資料集時，整輪行情停止且只記 log，UI 看不出原因。
8. 監控規則存檔時不檢查 `csg_` 訊號是否存在（列表會顯示警告）；`prompt_builder._load_doc` 找不到檔案時仍靜默回空字串；`/options` 的欄位標籤仍有簡體。
9. `frontend/src/pages/StrategyBacktest.test.tsx` 在工作區有一段與既有測試重複的新增測試，來源不明，未提交。

### 階段 7 待瀏覽器檢查與後續事項

`pnpm build` 已通過；手動檢查尚未執行，待使用者在瀏覽器確認：

1. 全域：一般 Toast 移到右上角後是否遮住頁首按鈕；第一次開預覽視窗的延遲；404 頁與 chunk 載入失敗頁；在 127.0.0.1 首次設定密碼的完整流程；重新整理後第一次點擊時告警聲音（含 iOS Safari）；跨分頁切換主題；macOS 的繁中字形。
2. 個股頁與預覽框：跨日後重開預覽框的日 K 區間與分時輪詢；除權息日之前懸停聯動線的位置與「成交」標籤；輪詢失敗時的提示；比較清單已滿時的確認框（含桌面版 WebView2）。
3. 選股與自選：窄螢幕下新增上限欄位的排版；打字時表格是否還會閃爍、「更新中」標示；側欄分組高亮與上一頁；刪除分組的提示；盤中訊號欄的 MM-DD 標示。
4. 看板與監控：即時行情關閉時報價條與指數條在盤中每 10 秒更新、非交易時段的頁尾；沒有報價的標的空狀態；盤後排程跑完後看板自動刷新；告警時間顯示。
5. 設定頁：AI 頁兩個「測試」按鈕；改 base_url 未填 key 時的提示；資料來源頁固定官方卡片的排版；選單隱藏快速連點；語音音色選回預設；籌碼回補重複啟動與後端重啟後的提示；「更新到最新」切換分頁後仍鎖定。

後續事項：

1. 國定假日交易所日曆尚未記錄時，後端回 `scheduled_open_unverified`，監控頁仍會在 08:30–13:35 每 10 秒呼叫 MIS。
2. 預覽框的分時輪詢只判斷所選日是否為最新一根 K，開盤前選最新一根仍會輪詢前一天的分時。
3. 看板的監控小組件載入中也顯示「暫無觸發記錄」；選股頁不會在盤後排程後自動刷新；設定頁回補成功後不會讓市場情報類查詢失效。
4. `Dashboard.tsx` 仍靜態引入預覽視窗；正式建置仍輸出 `Dev` 的 chunk（路由沒有註冊，進不去）；echarts chunk 超過 500 kB 的建置警告；`/dev` 不跳過國定假日。
5. 資訊列（`StockInfoBar`、`stock-info-fields.ts`）的財務欄位仍走 A 股 financial；比較頁基本面仍為 unavailable（`TaiwanFundamentalStore` 沒有寫入端）。
6. `stock-table` 的 SIGNAL_FIELDS 含不存在的 `signal_broken_board_recovery`，缺跌停與 MA5/10 訊號；B306 的嚴重度切點未處理。
7. 監控徽標最多只算 7 天內 100 筆，舊的 `monitor_last_seen_total` 沒有清掉；`voiceBroadcast.ts` 沒有逾時保護。
8. 新的 AI 測試端點沿用 `ai_provider` 的私有函式；`save_ai_settings` 會把記憶體中的 `ai_user_agent` 改成空字串。
9. `frontend/src/pages/StrategyBacktest.test.tsx` 在工作區仍有一段與既有測試重複的新增測試，來源不明，未提交。

### 階段 8 量測與後續事項

量測（CONTRIBUTING.md 6.3）：在 scratchpad 以合成資料建立接近正式資料的規模，修改前的數字以 `git archive HEAD` 匯出的程式碼量測；官方 API 以 mock 模擬每次 100 毫秒。

| 項目 | 修改前 | 修改後 |
|---|---|---|
| 個股頁開頁加輪詢 6 次的官方請求（上市 T86 1,300 列、上櫃 5,500 列） | 28 次 | 4 次；store 已有前一日時 2 次 |
| 同上，官方全部逾時 | 70 次，以 3 秒逾時計約 30 秒 | 4 次，約 6 秒 |
| 首頁靜態依賴 | 約 1.9 MB（含 echarts） | 約 0.55 MB |
| 選股讀取的日K分區（680 分區 × 2,000 檔） | 682 個 | 8～9 個 |
| 選股冷啟動 | 約 1.16 秒 | 約 0.42 秒；暖快取兩者相近 |
| 即時輪詢每輪的 provider 建構（暖） | 約 0.4 秒、每輪 1 個 client | 低於 0.1 毫秒、0 個 |
| `/api/taiwan/data` 每次（暖） | 約 0.28 秒、每次 1 個 client | 1.4 毫秒、0 個 |

冷啟動時第一次建立 client 的成本省不掉，輪詢與 `/api/taiwan/data` 的冷啟動耗時兩者相近。

行為變化：

1. 個股頁的法人與融資：store 已有完整當日資料時直接讀 store，來源顯示為本機 store；即時抓取以 (dataset, exchange, trade_date) 快取，逾時、連線錯誤或 5xx 時該交易所退避、不再往前抓，回退 store 並標 stale。
2. 個股頁 10 秒輪詢只在台北盤中進行，盤後改為偵測資料日期變動時重新讀取。
3. 選股：完整分區中缺列的代號（例如停止買賣）不再列出，回應附 `daily_unlisted_count`；某交易所在最新分區缺席或列數未達前 5 個分區最大值的 50% 時，回退最多 5 個分區並標 `daily_stale_*` 或 `daily_missing_*`。

後續事項：

1. SSE `quotes_updated` 仍讓整個個股詳情失效；報價與籌碼在同一個回應中，要拆開需要新增後端端點。
2. 沒有蓋日期的官方回應會被接受並快取到當日午夜（原本就接受，現在快取時間較長）。
3. `listing_sessions` 的 `first_sessions` 每 60 秒仍掃描全部日K分區；分區檔損毀時會讓選股直接丟例外。
4. 共用 provider 若在測試中建立於被 patch 的 `httpx.Client` 之下，會沿用到整個測試行程。
5. echarts chunk 約 1.03 MB，建置時有大小警告。

### 階段 9 處置與後續事項

處置摘要：

1. 每個 D 編號刪除前都重新搜尋整個 repo（含動態載入、打包設定、插件文件與使用者策略可 import 的模組）；刪除後跑後端全套、前端 tsc、vitest 與 `pnpm build`。
2. A 股漲跌停、成交量單位與換手率改動前後，以台股合成樣本做 golden 對照（上市、上櫃、ETF、TDR、除權息、漲跌停），輸出逐位元組相同。
3. 移除的大項：TickFlow SDK 整合與能力框架（`app/tickflow/`、`app/capabilities.py`）、A 股資料與盤中增量路徑、A 股 ETF enriched 快取、optimizer、walkforward、prompt_builder、mining；依賴 fastexcel、platformdirs、plyer、mypy、class-variance-authority、echarts-for-react、lightweight-charts。
4. 一併修正：台股 ETF 以 `asset_type=etf` 回測、選股與 ETF 策略型規則原本讀到空目錄，改讀 `kline_daily_enriched` 並以 Security Master 篩選；監控的 ETF 輪原本永遠不會觸發；擴展表設定編輯後可能讀到舊快取。
5. 已發佈的挖掘策略仍可載入與回測（只依賴 `factor_rank_research`）。下架改為手動：把 `data/strategies/custom/<id>.py` 移走、停用引用它的監控規則並重啟後端；以前下架的檔案在 `data/research/mining/reevaluation/delisted/`。

後續事項：

1. 使用者 data 目錄會留下 `research/mining/`、`.mining_process.lock` 與偏好中的 mining 鍵，程式不再讀取，可手動清理。
2. 後端 builtin 資料源仍宣告 `financial`，目前由前端過濾不顯示。
3. 保留但無正式入口或待決的項目：matrix 運算子（使用者策略可 import）、基本面相關 D301、D365、D442、D495、D538；`ScreenerResult` 只剩測試使用。
4. 台股 ETF 價位用的是股票的升降單位（`_TICK_CLASSES["etf"]` 不可達），屬既有問題。
5. 階段 10 要同步的文件：`CONTRIBUTING.md`（`app/tickflow/`、優化與步進優化、lightweight-charts）、`docs/configuration.md`（已刪端點、mining 發佈、TICKFLOW_API_KEY）、`docs/deployment.md`、`docs/plugin-development.md`（回退 TickFlow、`plugins/` 目錄）、`docs/taiwan-data-sources.md`（`/api/data/status`）、`docs/radar-strategies-validation.md`（optimizer）、`.env.example`；audit README 階段 2 的實機步驟仍提到已不再打包的 prompts。
6. 本階段有三次 subagent 讀到正式 data/（遞迴 grep、未隔離的驗證腳本），都沒有寫入；派工限制已改為明列「不得從 repo 根目錄遞迴、驗證腳本必須設 DATA_DIR」。

### 階段 10 處置與後續事項

處置摘要：

1. D9：刪除 `screenshots/`、`gui-test-screenshots/`、`community-qr-code.jpg` 與未使用的 `packaging/tickflow-logo.svg`；`frontend/vite.config.d.ts` 移出版控；`docs/deploy-password.md` 併入 `docs/deployment.md`；`docs/features.md` 併入操作說明書後刪除，README 只留簡介與文件導覽。
2. 使用者與開發者文件依現行程式改寫（頁面、設定分頁、排程、AI 用途、插件能力、環境變數與優先級、Docker 建置參數、模組邊界），並修正 findings.md「文件」區 18 項。
3. 兩份計畫文件移到 `docs/archive/`；`docs/audit-2026-09-24/` 與 `docs/code-review-2026-09-22.md` 加封存註記，A03#6、A06#10、G05#7 改判已修正。
4. redundant-files.md 的 55 項都有處置；其中多數程式與測試項目已在階段 9 刪除。

後續事項：

1. 選股頁的排程說明只寫 16:30，文字來自 `backend/app/taiwan/daily_update.py:104`、`:506`，沒有提 21:40 的資券更新。
2. 舊審查其他條目在 `1724b12` 之後沒有重判；本目錄的 findings.md 與 verification.md 仍以文字提到已移到 `docs/archive/` 的計畫文件路徑，屬於當時的紀錄，不改。
3. `.dockerignore` 仍有 `screenshots` 一行，無害。
4. `frontend/src/pages/StrategyBacktest.test.tsx` 在工作區仍有一段與既有測試重複的新增測試，來源不明，未提交。

## 維護方式

- 修好一項，在本頁「進度」記下提交與已完成的 ID；findings.md 保留原始描述，不改寫。
- 該項若對到舊稽核（findings.md 的「既有文件」欄），同步更新舊文件的狀態。
- 決策定案後，寫進「需要先決定的事項」的對應列。
- 行號只對 `1724b12` 有效；引用前以函式名稱重新定位。
