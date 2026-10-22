# 冗餘檔案與文件清單（2026-10-22 全專案審查）

> 基準：HEAD `1724b12`。每項都經另一位驗證者以 `git ls-files` 確認追蹤狀態、搜尋檔名確認引用情形。刪除或移動前請再確認一次，並同步修改引用它的文件。

| ID | 路徑 | 類型 | 建議 | 理由 | 驗證 |
|---|---|---|---|---|---|
| R01 | `.env.example` | config | 更新內容 | 保留已無作用的 TICKFLOW_API_KEY，ollama 選項也不正確。 | 確認 |
| R02 | `.gitignore` | config | 更新內容 | 規則與追蹤狀態矛盾，還有原專案遺留的私人或探測項目。 | 確認 |
| R03 | `README.md` | doc | 合併 | README、操作說明書與 docs/features.md 三份都在描述功能、安裝與資料更新，內容大量重複且已不同步（只有 features.md 有策略庫）；README 的文件表也沒有連到 features.md。 | 確認 |
| R04 | `backend/app/indicators/__pycache__/pipeline._seeded_ewm_kernel-258.py311.*、pipeline._seeded_ewm_kernel-259.py311.*` | generated | 刪除 | numba cache=True 依函式起始行號命名快取；目前 @njit 在 pipeline.py:257，-258 和 -259 是舊行號留下的索引與 .nbc，不會再被載入 | 確認 |
| R05 | `backend/app/strategy/prompts/strategy-example.md` | 示範文件（程式不載入） | 刪除 | 只被 strategy-guide.md:395 引用。內容描述的介面流程目前不存在：創建對話框的「AI 生成」（:18）、Step 2「AI 修改」（:126-130）、「保存策略」與策略配置的基礎參數面板（:134-153）。前端的 strategyBuild、strategyBuildStream、strategySaveCodeV2 都沒有呼叫者，frontend/src 也找不到基礎參數 UI。程式碼範例幾乎逐字重複 strategy-guide.md:327-391。META.id 寫 strong_reversal，與 ai_ 前綴規則（strategy-guide-compact.md:8）矛盾；還寫了「排除ST」（:35、:143）。（驗證者補充：delete，並同步處理 strategy-guide.md:395 的連結） | 確認 |
| R06 | `backend/app/strategy/prompts/strategy-guide.md` | 給人看的策略開發指南（程式不載入） | 更新內容 | 不被任何程式載入，只有 docs/deployment.md:145 與 strategy-guide.md:3 引用。filter_history 寫法（:150-163）、change_pct 小數（:203）、turnover_rate 百分數（:236）、basic_filter 預設值（:89 與 engine.py:46-58 一致）都正確，deployment.md 的引用也有意義。但仍有 A 股殘留：:193 的 symbol 範例是 600519.SH（台股為 .TWSE／.TPEX）；:316 寫「遵循 A 股 T+1 规则」；:34 與 :338 範例寫 exclude_st: True，但引擎預設為 False，台股也沒有效果（engine.py:44-56、docs/deployment.md:143）；:395 稱「三步完整演示」，strategy-example.md 卻是兩步。內容與精簡版大量重疊，兩份已經漂移。 | 確認 |
| R07 | `backend/app/taiwan/enrichment/quote.py` | script | 刪除 | 整個模組只被測試使用，抓取目標（STOCK_DAY_ALL、tpex_mainboard_quotes）與 official_provider.fetch_quote 重複，而且語意較差（trade_date 寫死成今天、缺值補 0）。（驗證者補充：test_only） | 確認 |
| R08 | `backend/scripts/cleanup_halt_days.py` | script | 刪除 | A 股停牌（*ST）髒資料的一次性清理腳本。DATA_DIR 寫死為專案根的 data，會忽略 settings.data_dir 與 DATA_DIR 環境變數；以 write_parquet 原地非原子改寫 kline_daily_enriched 分區，也不更新 generation 或快取，違反 CONTRIBUTING 的原子替換與快取失效要求。 | 確認 |
| R09 | `backend/scripts/probe_tickflow_pro.py` | script | 刪除 | 上游 A 股 TickFlow Pro 的一次性探測腳本：GOLDEN 是 000403.SZ、600489.SH、300059.SZ 等 A 股代碼，以上海 16:00 離峰為閘門，輸出到 reports/tickflow_pro_probe/。台股流程不使用。 | 確認 |
| R10 | `backend/tests/fixtures/mis_2026_10_08/093245.json` | data | 刪除 | MIS 樣本中唯一沒有被任何測試讀取的一份。mis_sample() 與 tsmc_entry() 的呼叫只用到 084501、090011、145659。（驗證者補充：doc_referenced） | 可能成立 |
| R11 | `backend/tests/test_kline_sync_timezone.py` | test | 刪除 | 唯一一個測試把 kline_sync.get_client 換成假 TickFlow client，驗證 fetch_minute_single 回退到 TickFlow 時的時間窗口。正式環境的 get_client() 一律 raise（app/tickflow/client.py:20-21），受測的窗口建構與 klines.batch 呼叫（kline_sync.py:858-866）到不了。 | 確認 |
| R12 | `backend/tests/test_probe_tickflow_pro_gate.py` | test | 刪除 | 測的是 A 股 TickFlow Pro 探測腳本 backend/scripts/probe_tickflow_pro.py 的時段閘門（Asia/Shanghai 16:00 離峰、TICKFLOW_API_KEY）。這支腳本除了這兩個測試以外沒有任何引用，TickFlow SDK 也已停用（app/tickflow/client.py:20-21）。 | 確認 |
| R13 | `backend/tests/test_probe_tickflow_pro_live.py` | test | 刪除 | 同上，用假 client 測 A 股 TickFlow 探測腳本的 run_live／run_dry_run，斷言的 symbols 是 000403.SZ、600489.SH、300059.SZ。（驗證者補充：obsolete_not_duplicate） | 確認 |
| R14 | `backend/tests/test_quote_index_merge.py` | test | 更新內容 | 用 A 股指數（000001.SH、399001.SZ、399006.SZ）測 index 分區的 merge。自從加入 live write gate，兩個案例都固定失敗（詳見 bugs）。（驗證者補充：env_dependent） | 可能成立 |
| R15 | `backend/tests/test_st_limit_and_sharpe.py` | test | 保留並標註 | 整個檔只剩 A 股 ST、創業板、科創板、北交所漲跌停的測試（300001、688001、830001.BJ，2026-07-06 主板 ST 新規）。檔名中的 Sharpe 測試已在 0bfa82f 隨因子回測一起刪除。受測的 A 股分支（app/price_limits.py:58-70、72-89）在純台股部署中不會遇到。 | 確認 |
| R16 | `backend/tests/test_watchlist_batch.py` | test | 刪除 | 三個測試都 patch app.tickflow.client.get_paid_realtime_client，回傳假的付費 client，以測 QuoteService._fetch_watchlist_quotes 的分批邏輯。正式程式中 get_paid_realtime_client() 永遠回傳 None（app/tickflow/client.py:28-29），quote_service.py:1056-1059 會直接 return，所以受測的分批區段在正式環境到不了。 | 確認 |
| R17 | `community-qr-code.jpg` | asset | 刪除 | 上游專案的社群 QR code 圖片（248KB），README 與文件都沒有引用，對台股分支容易誤導。 | 確認 |
| R18 | `docs/audit-2026-09-24/` | doc | 保留並標註 | 稽核過程的工作底稿（21 檔，約 19,330 行，3.1MB），對一般使用者與二開者沒有直接用處；行號都對應舊 commit，部分內容已與現況矛盾。 | 確認 |
| R19 | `docs/audit-2026-09-24/README.md` | doc | 更新內容 | 進度區有過時的內容：(1) :12 的「已修正 135、部分修正 7、未修正 52」要依 A06#10、G05#7 改判已修正、A03#6 改判部分修正重算；(2) 章節狀態表 :55 A03 主要缺口「RSV 0/0」、:58 A06「股／張單位」、:70 G05「成交量以股顯示」已過時，各章的數字也要改（A03 6/1/0、A06 8/2/2、G05 10/0/1）；(3) :259 P3 的 RC16 仍列「股／張」；(4) :317 說 docs/features.md 還沒有寫入策略邏輯變更日，但 docs/features.md:33 已經寫了 2026-10-10（:194 也說已寫入，前後矛盾）；(5) :21、:120、:160-163、:201 的 2026-10-12 收盤集合競價、MIS 零股量、21:30 定稿門檻實測，到今天都沒有結果紀錄；(6) :248 引用的 daily_pipeline.py:129-211 經 1724b12 後，refresh_benchmark_index 在 :129-153。 | 確認 |
| R20 | `docs/audit-2026-09-24/recheck-2026-10-06.md` | doc | 更新內容 | 逐條表與計數表有過時的條目：(1) A06-turnover-mcap-pct#10（:152）、G05-gap#7（:276）寫未修正，但 06166e8、109cfe9 已把 StockInfoBar 與各圖表的成交量改用 formatVolume 並帶股／張單位，fmtVol 的「10000萬」邊界也由 compactNumber 進位處理，而且有 VolumeUnit.labels.test.tsx、VolumeUnit.charts.test.tsx、volumeUnit.test.ts 測試，應改為已修正；(2) A03-kd-rsi#6（:114）寫未修正、「兩條路徑行為仍不同」，但 198e4ef 的 seeded_ewm 讓 pipeline 與矩陣都只在 0/0 的那幾根輸出 null，之後恢復，應改為部分修正；(3) A13-realtime#1（:223）、#7（:229）寫「待 2026-10-12 實測」，到 2026-10-22 沒有任何實測紀錄；(4) 計數表（:32-37）、RC16（:60）、RC32（:76）的列要重算；(5) 仍未修正條目的證據行號已經偏移：A06#9 format.ts:29→:22、G05#3 EChartsIntraday.tsx:33-34→:43-47（使用處 :618）、EChartsMultiDayIntraday.tsx:32-33→:43-47、A07#1 detail_service.py:535→:588、G03#1 detail_service.py:626-629→:678-683。 | 確認 |
| R21 | `docs/audit-2026-09-24/sections/A01-price-data.md` | doc | 保留並標註 | 本章 10 條 finding 都已修正，章節仍是 2026-09-24 的原始主張，行號只對 ced12a5 有效（驗證者補充：keep_but_mark） | 確認 |
| R22 | `docs/audit-2026-09-24/sections/A02-ma-macd.md` | doc | 保留並標註 | 9 條中 7 條已修正（#4、#8 未修正），內文沒有標示哪些已修正（驗證者補充：keep_but_mark） | 確認 |
| R23 | `docs/audit-2026-09-24/sections/A03-kd-rsi.md` | doc | 保留並標註 | 7 條中 6 條已修正，#6 本次改判部分修正；原文所述 pipeline.py:465-470 等行號已失效（驗證者補充：keep_but_mark） | 確認 |
| R24 | `docs/audit-2026-09-24/sections/A04-boll-atr-vol.md` | doc | 保留並標註 | 12 條中 10 條已修正，1 條部分修正，1 條未修正（驗證者補充：keep_but_mark） | 確認 |
| R25 | `docs/audit-2026-09-24/sections/A05-limits-signals.md` | doc | 保留並標註 | 15 條中 11 條已修正，1 條部分修正，3 條未修正（驗證者補充：keep_but_mark） | 確認 |
| R26 | `docs/audit-2026-09-24/sections/A06-turnover-mcap-pct.md` | doc | 保留並標註 | 12 條中 8 條已修正（#10 由 06166e8／109cfe9 修正，recheck 尚未更新），2 條部分修正，2 條未修正（驗證者補充：keep_but_mark） | 確認 |
| R27 | `docs/audit-2026-09-24/sections/A07-chips.md` | doc | 保留並標註 | 7 條中 2 條已修正，5 條未修正，大部分內容仍然有效（驗證者補充：keep_but_mark） | 確認 |
| R28 | `docs/audit-2026-09-24/sections/A08-strategies-trend.md` | doc | 保留並標註 | 12 條中 9 條已修正（驗證者補充：keep_but_mark） | 確認 |
| R29 | `docs/audit-2026-09-24/sections/A09-strategies-momentum.md` | doc | 保留並標註 | 12 條中 9 條已修正（驗證者補充：keep_but_mark） | 確認 |
| R30 | `docs/audit-2026-09-24/sections/A10-strategies-reversal.md` | doc | 保留並標註 | 16 條都已修正，整章只剩歷史價值（驗證者補充：keep_but_mark） | 確認 |
| R31 | `docs/audit-2026-09-24/sections/A11-backtest-costs.md` | doc | 保留並標註 | 11 條都已修正；原文引用的 backend/app/services/backtest.py 已刪除（驗證者補充：keep_but_mark） | 確認 |
| R32 | `docs/audit-2026-09-24/sections/A12-screener-stats.md` | doc | 保留並標註 | 11 條中 7 條已修正、3 條未修正、1 條已推翻（#10），推翻理由已完整寫在 README §4（驗證者補充：keep_but_mark） | 確認 |
| R33 | `docs/audit-2026-09-24/sections/A13-realtime.md` | doc | 保留並標註 | 8 條中 7 條已修正（#1、#7 待實測），1 條部分修正（驗證者補充：keep_but_mark） | 確認 |
| R34 | `docs/audit-2026-09-24/sections/G01-gap.md` | doc | 保留並標註 | 12 條中 9 條未修正，內容大多仍然有效，只是沒有狀態註記（驗證者補充：keep_but_mark） | 確認 |
| R35 | `docs/audit-2026-09-24/sections/G02-gap.md` | doc | 保留並標註 | 6 條中 5 條未修正（驗證者補充：keep_but_mark） | 確認 |
| R36 | `docs/audit-2026-09-24/sections/G03-gap.md` | doc | 保留並標註 | 10 條中 6 條未修正（驗證者補充：keep_but_mark） | 確認 |
| R37 | `docs/audit-2026-09-24/sections/G04-gap.md` | doc | 保留並標註 | 11 條中 8 條未修正（驗證者補充：keep_but_mark） | 確認 |
| R38 | `docs/audit-2026-09-24/sections/G05-gap.md` | doc | 保留並標註 | 11 條中 10 條已修正（#7 由 06166e8／109cfe9 修正，recheck 尚未更新），只剩 #3（驗證者補充：keep_but_mark） | 確認 |
| R39 | `docs/audit-2026-09-24/sections/G06-gap.md` | doc | 保留並標註 | 3 條都已修正，整章只剩歷史價值（驗證者補充：keep_but_mark） | 確認 |
| R40 | `docs/code-review-2026-09-22.md` | doc | 保留並標註 | 43 條中 27 條已修、5 條撤除／不修／不成立／已失效，只剩 9 條仍存在、2 條部分修復（B4、B5、B6、C1、C4、C5、research_context tail(5)、kline date.today()、Layout 靜態 import、N2、N4），另有 A5 已知風險 1 項。這些未結項目沒有遷入 docs/audit-2026-09-24/README.md 的「目前待修正項目」，所以本文件還不能整份被稽核文件取代。內容也有過時之處：第八節 :682-683 說稽核報告「只保留在本機、沒有納入版本庫」，但 1b6daf5 已把 docs/audit-2026-09-24/ 加進版本庫；第六、七節的「待修優先序／剩餘項目」把 B12、N5、N3 列為未修，現行程式碼已修（ac16ee5）。 | 確認 |
| R41 | `docs/deploy-password.md` | doc | 合併 | 內容與 docs/deployment.md 的「訪問密碼設置」一節幾乎逐段相同（方式一、方式二、原理、比較表）。 | 確認 |
| R42 | `docs/examples/custom-data-source/` | doc | 更新內容 | mock 範例沿用 A 股代碼，無法用來驗證台股流程。 | 確認 |
| R43 | `docs/market-filter-volume-patterns-plan.md` | doc | 移到封存 | 已實作完成的一次性計畫，沒有任何文件或程式碼引用；文件自己也說內文的 `路徑:行號` 是實作前的位置，已經失效。 | 可能成立 |
| R44 | `docs/plugin-development.md` | doc | 更新內容 | 大半內容描述不存在的插件（stock-sdk、fuyao）、建置參數與 TickFlow 回退。 | 確認 |
| R45 | `docs/radar-strategies-validation.md` | doc | 保留並標註 | 2026-09-18 的策略移植驗證紀錄，屬於歷史文件；文中已加註第 4 階段的變更。 | 確認 |
| R46 | `docs/strategy-pick-tracking-plan.md` | doc | 移到封存 | 已實作完成的計畫，部分內容已與現行程式碼不符。 | 確認 |
| R47 | `docs/taiwan-etf-history-phase-6i.md` | doc | 保留並標註 | 一次性的 ETF NAV 與配息調查紀錄，結論仍然有效。 | 確認 |
| R48 | `docs/taiwan-fundamentals-availability-phase-6g.md` | doc | 保留並標註 | 一次性的調查決策紀錄，結論至今仍成立，但檔名帶階段代號，看起來像暫時性文件。 | 確認 |
| R49 | `docs/taiwan-market-contract-v1.md` | doc | 更新內容 | 與 v1.1 不是重複：v1.1 是增量擴充，兩份 JSON fixture 各自有測試，都要保留；只有第 8 節有一句過時。 | 確認 |
| R50 | `frontend/src/pages/settings/Timeout.tsx` | other | 合併 | 這個 8 行的檔案只把 JobTimeoutCard 原樣包一層。JobTimeoutCard 除了這裡沒有其他引用者，兩個檔案可以合併。如果採納 bugs 中「逾時設定沒有作用」的處理方式並移除該分頁，兩個檔案都可以一起刪除。（驗證者補充：thin_wrapper） | 可能成立 |
| R51 | `frontend/vite.config.d.ts` | generated | 移出版控 | 這是 tsc -b 產生的宣告檔，卻被 git 追蹤。tsconfig.node.json 設定 composite + emitDeclarationOnly 且沒有 outDir，每次 pnpm build（package.json 的 build script 是 "tsc -b && vite build"）都會在原地重寫它。工作樹因此長期顯示 M（git diff 內容為空，只差 LF/CRLF），沒有任何程式引用它。 | 確認 |
| R52 | `gui-test-screenshots/` | asset | 刪除 | 24 張 GUI 驗收截圖（合計 5,330,293 bytes，約 5.3MB），被追蹤但沒有任何地方引用；.gitignore 的政策是驗收截圖不入版控，這個目錄沒有跟著遵守。（驗證者補充：24 張截圖（非 10 張）） | 確認 |
| R53 | `packaging/generate_icon_designs.py` | script | 刪除 | 圖示設計方案的一次性預覽腳本，已由 generate_icon.py 定稿；import 時就會在 packaging/designs/ 建目錄並輸出 PNG。 | 確認 |
| R54 | `packaging/tickflow-logo.svg` | asset | 刪除 | 沿用舊名的 logo SVG，沒有任何地方引用；圖案與 frontend/public/favicon.svg 相同但顏色不同（#8B5CF6 對 #5B21B6），容易造成品牌色混淆。（驗證者補充：近似重複（顏色與線寬不同）） | 確認 |
| R55 | `screenshots/` | asset | 刪除 | 18 張 png 加 README.md 全是原 A 股「TICK STOCK PANEL」的介面截圖，內容包含已移除的功能（挖掘因子、連板梯隊、市場環境、AI 復盤、飛書推送），README 也是簡體中文。.gitignore 明文寫 screenshots/ 不入庫，卻有 19 個檔案被追蹤。 | 確認 |
