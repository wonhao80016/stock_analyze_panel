# A12-screener-stats｜選股與市場統計

> 本文件已封存，後續追蹤改到 [docs/audit-2026-10-22/](../../audit-2026-10-22/README.md)。

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 3 | 7 | 0 | 1 | 0 | 14 | 9 |

## 稽核者摘要

結論：多數統計的算術正確，但輸入資料和口徑有 5 個會直接影響畫面數字的問題。
（1）P1：9/16 與 9/23 兩天的日K整份沒有上櫃資料。上櫃快照抓取失敗被吞掉，只寫入上市資料，之後的排程又把這天當成已完成而跳過。9/23 首頁的漲跌家數、成交額、產業強弱、選股結果與策略快取全部只含上市，data_quality 卻標為 complete。
（2）P1：除權息日用未還原的昨收計算。9/23 有 5 筆被算成下跌（官方標為無比價），跌停家數 3 筆（官方 1 筆），1442 被顯示為 -10.75%（以參考價計為 -3.30%）。6 至 9 月間有 17/81 天首頁「最弱產業」前 5 名因此改變。
（3）P2：選股的「逼近漲停／跌停（≤3%）」篩選永遠 0 筆，因為漲跌停價以當日收盤計算，實際是隔日的漲跌停價。
（4）P2：全市場法人買賣超以「股數加總」呈現，混入 ETF 單位數與上櫃權證。9/23 投信顯示 +20,233 張，以金額估算上市投信卻賣超約 47.9 億。
（5）P2：16:30 抓取的成交量值與 T86 投信數字之後不再更新，缺少 17:00 前的鉅額交易，市場投信合計比官方定版高 59%。
其他問題：選股的 RSI 用簡單平均、量比分母含當日，與 app 自身 enriched 及台灣慣例（Wilder）不一致；首頁策略「552 檔」為重複加總，實際 290 檔；降序排序把空值排在最前；regime 仍是 A 股校準且在此部署從未產出。
已驗證正確：9/23 上市 1,330 檔收盤、380 筆 STOCK_DAY 開高低收、2,216 筆融資融券與券資比、法人外資與自營逐檔數值、漲停 15 檔名單、趨勢參與度、產業各項算術（含 B0 修正後 5 日基期）、選股 MA／動能、自選分組統計，以及多策略共振計數。
全部腳本與中間結果在 AUDIT_SCRATCH/work/A12/。repo 與 data 皆未改動，git status 與執行前相同，檢查的分區修改時間也沒有變。

## 問題清單

### [P1 → P1] 9/16 與 9/23 日K缺整個上櫃市場，市場統計、產業、選股、策略全部只剩上市，品質卻顯示 complete（確認成立）

- **主張**：上櫃快照抓取失敗被吞掉，只把上市資料寫成完整分區。之後的排程因分區已存在而判定 already current，不再重抓。market_intelligence 與 industry_intelligence 只檢查分區是否存在，所以把 overall_status 標成 complete。
- **正確的定義或數值**：官方 9/23 市場統計應包含上市與上櫃；任一交易所缺資料時，應標為 partial 或 failed 並重抓
- **app 實際行為**：9/23 首頁與選股頁只含上市資料（上櫃成交額 0 億、上櫃漲跌 0 家），品質標籤卻是 complete；策略選股、產業 5 日與 20 日相對強弱排除全部上櫃；9/17 上櫃的單日漲跌幅實際是 2 日漲跌幅
- **對使用者的影響**：使用者 9/23 看到的全市場成交額少了約 2,532 億（約 22%），漲跌家數少了約 978 家，上櫃股票不會出現在任何策略推薦中；而且畫面沒有任何警示
- **修法建議**：snapshot_provider 任一交易所失敗時整批視為失敗（或回傳 partial 並不寫分區）；daily_update 判斷是否完成時，要檢查各交易所的列數；market/industry 的 data_quality 按交易所計算覆蓋率；另外補抓 9/16 與 9/23 的上櫃資料
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/providers/snapshot_provider.py:111-118（TPEx 例外只記 warning，仍回傳只含 TWSE 的 frame）
  - backend/app/taiwan/daily_update.py:282-286（d_range 為空時標為 success／Daily store already current）
  - backend/app/taiwan/market_intelligence.py:386-392、576-582（daily_status 只看 daily_as_of >= target）
  - backend/app/taiwan/screener.py:207、215（read_latest_per_symbol 讓上櫃列退回 9/22，daily_as_of 卻顯示 9/23）
  - frontend/src/pages/Dashboard.tsx:241（只在 overall_status≠complete 時才顯示警示）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 掃描 661 個 data/taiwan/daily 分區，只有 date=2026-09-16 與 date=2026-09-23 缺上櫃（各只有 TWSE 1,330 列，其他日上櫃約 1,003–1,009 列）
  - backend.log:37258 '2026-09-23 16:30:05 TPEx snapshot fetch failed for 2026-09-23: peer closed connection ... (incomplete chunked read)'；37265 'daily=success'；45477 21:40 再跑時 'is_fully_current=True'，沒有重抓
  - app 9/23 快照：by_exchange.tpex 的 snapshot_row_count=0、turnover=0，data_quality.overall_status='complete'（work/A12/breadth.py 輸出）
  - 依官方 tpex_daily_quotes_20260923 獨立計算，被漏掉的上櫃支援標的共 1,007 列：上漲 432、下跌 427、平盤 119，成交額 2,532.4 億
  - strategy_cache.json（as_of 2026-09-23）552 筆選股全部是 .TWSE，上櫃 0 筆
  - 選股 9/23：1,010 列 quote_date=2026-09-22，但 institutional_date=2026-09-23，同一列混用兩天的資料
  - 連帶影響：enriched 9/17 的 6488.TPEX change_pct 為 -0.22%（912 對 9/15 的 914），官方 9/17 為 -25 元（-2.67%，對 9/16 的 937）
  - 產業 9/23：comparable_stocks_5d_count=1077，全部是上市（5 日基期 9/16 同樣缺上櫃）
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：主張成立，只補兩點。第一，除了 TPEx 抓取丟出例外之外，fetch_tpex 找不到行情表而回傳空 frame（snapshot_provider.py:228-230）時，也會只寫上市。真正寫入分區的地方是 daily_refresh.py:173-177，只要 frame 不是空的就寫，而且算成功。之後 daily_update.py:282-286 與 daily_refresh.py:167 因分區已存在，除非 force 都不會重抓。第二，market_intelligence 其實有計算 missing_symbols_count，但前端沒有任何頁面顯示；overall_status 只看分區日期，所以結果是 complete。9/16 與 9/23 的日K都缺整個上櫃市場（各缺約 1,007 檔支援標的，9/23 的上櫃成交額約 2,532 億，約占全市場 22%），首頁與選股頁的市場統計、產業強弱、策略推薦都只剩上市，卻沒有任何警示。9/17 上櫃個股的漲跌幅實際是 2 日漲跌幅（例如 6488 顯示 -0.22%，官方為 -2.67%）；選股頁的上櫃列同時混用 9/22 行情與 9/23 法人資料。9/23 的失敗原因有 log 佐證（incomplete chunked read）；9/16 的失敗原因因 log 已輪替而無法證實，只能從分區修改時間 16:30:22 推定是同一個排程流程所寫。
    - 理由：這條 finding 成立，嚴重度 P1 合理。我逐一重讀了它引用的程式碼（行號與現況一致），也直接讀 parquet 分區、log，並和官方參考資料比對，每個環節都得到確認。

一、根因：上櫃失敗被吞掉，只寫進上市的半份分區，之後也不會重抓
- snapshot_provider.py:112-118 對 TPEx 的例外只記一條 warning，接著回傳只含 TWSE 的 frame。另外，fetch_tpex 找不到行情表時（228-230）也會回傳空 frame。這是 finding 沒提到的第二條觸發路徑，結果一樣是只寫上市。
- daily_refresh.py:173-177 只要 frame 不是空的就寫進分區，並計入 dates_fetched，不檢查各交易所有沒有資料。
- 下次排程時，daily_update.py:282-286 用 max(available_dates) 判斷，得出「Daily store already current」。daily_refresh.py:167 也會跳過已存在的分區。除非 force，不會重抓。

二、品質標籤仍顯示 complete
- market_intelligence.py:386-392 與 industry_intelligence.py:226-227 的 daily_status 只看 daily_as_of >= target；market_intelligence.py:576-582 據此把 overall_status 設為 complete。
- missing_symbols_count 確實有算（market_intelligence.py:394-395、614），但前端只在 api.ts 宣告型別，沒有任何頁面顯示。
- Dashboard.tsx:241 與 TaiwanScreener.tsx:547 都只在 overall_status≠complete 時才顯示警示，所以使用者看不到任何提示。唯一的線索是選股頁「有效撮合標的 x / 總數」的分子變小，並不是明確警示。

三、實際資料
- 661 個分區裡，只有 9/16 與 9/23 的上櫃列數是 0（上市各 1,330 列）；其他分區上櫃為 876–1,009 列。兩個分區的修改時間都是當天 16:30 排程時段。
- 9/23 的失敗原因在 log 裡查得到。9/16 當時的 log 已經輪替（backend.log.2 不存在），失敗原因無法從 log 證實，推測是同一類問題。
- 用官方 9/23 上櫃行情，並依支援清單過濾後共 1,007 檔：上漲 432、下跌 427、平盤 119，成交額 2,532.3 億。app 的上市成交額為 8,872.5 億，漏掉的部分約占全市場 22.2%。
- 策略快取（as_of 2026-09-23）裡的代號全是 .TWSE，沒有任何 .TPEX。
- 6488.TPEX 的 enriched 9/17 change_pct 為 -0.2188%，等於 (912-914)/914，用的是 9/15 的收盤。官方 9/16 收盤 937、9/17 漲跌 -25，應為 -2.67%。
- 選股頁每檔取各自最新一天：1,009 檔上櫃加 1 檔上市的最新日是 9/22，共 1,010 列。institutional 9/23 分區有 5,670 列上櫃資料，而選股頁的法人欄位也是取各檔最新一筆，所以這些上櫃列會把 9/22 的行情和 9/23 的法人資料放在同一列。

四、定義依據
- 專案自己的文件（docs/taiwan-market-overview.md:10、14）寫明涵蓋範圍是 TWSE 與 TPEx，選股範圍是 full TWSE/TPEx universe；UI 也直接標示「全市場成交額／全市場漲跌家數」並分列上市與上櫃。
- Wikipedia 的 Advance–decline line 條目把上漲／下跌家數定義為相對「前一日收盤價」，所以 9/17 上櫃的漲跌必須以 9/16 收盤為基準。
- Wikipedia 的 Taipei Exchange 條目說明 TPEx 是台灣另一個獨立的櫃檯買賣市場。
- CONTRIBUTING.md 第 11 節第 8 點要求「部分寫入時不得產生錯誤狀態」。

五、嚴重度
在使用者的實際情境下，自選 9 檔都是上市股，個股報價本身不受影響。但首頁「今日市場強弱」、選股頁的市場統計、產業強弱與策略推薦，都會在「complete」標籤下漏掉整個上櫃市場。這個缺口不會自行修復：9/16 的缺口過了 8 天仍在。受影響上櫃股的單日漲跌幅與技術指標也會因少一根K棒而失真。這屬於「錯誤金融口徑」，定為 P1 合理。它沒有破壞既有的正確資料，也不直接造成自選交易錯誤，所以不到 P0。

六、我沒有獨立重算的部分
產業 comparable_stocks_5d_count=1077 這個數字沒有重算。從邏輯推論，9/23 與 5 日基期 9/16 都缺上櫃，上櫃個股必然被排除，這一點屬推論。
    - 證據：
      - backend/app/taiwan/providers/snapshot_provider.py:112-118：TPEx 例外只記 logger.warning，只要上市不是空的，frames 仍會回傳；228-230 找不到行情表時回傳空 DataFrame，結果相同
      - backend/app/taiwan/daily_refresh.py:167-177：已存在的分區直接跳過；只要 df 不是空的就 write_batch 並計為 dates_fetched，不檢查各交易所的列數
      - backend/app/taiwan/daily_update.py:282-286：d_range 為空時，status='success'、note='Daily store already current'
      - backend/app/taiwan/market_intelligence.py:386-392、576-582：daily_status 只比較 max(available_dates) 與 target，overall_status 據此判定；394-395、614 雖有算 missing_symbols_count，但前端沒有顯示（只出現在 frontend/src/lib/api.ts:1119 的型別宣告）
      - backend/app/taiwan/industry_intelligence.py:226-227、251：同樣只看分區日期
      - backend/app/taiwan/screener.py:207、215、409-418：行情與法人都取各檔最新一筆，daily_as_of 取全體的最大日期
      - frontend/src/pages/Dashboard.tsx:241；TaiwanScreener.tsx:547：只在 overall_status !== 'complete' 時顯示警示
      - 本地掃描（work\V_tpex_gap\scan.py）：661 個分區中，上櫃=0 的只有 date=2026-09-16（上市 1330，mtime 2026-09-16T16:30:22）與 date=2026-09-23（上市 1330，mtime 2026-09-23T16:30:05）；其他分區上櫃 876–1009 列
      - data/backend.log:37257-37258：'dailyQuotes?date=115/09/23 ... 200 OK'，接著是 'TPEx snapshot fetch failed for 2026-09-23: peer closed connection without sending complete message body (incomplete chunked read)'；37265 'daily=success'；45462-45477（21:40 再跑）沒有 MI_INDEX 或 dailyQuotes 請求，最後 is_fully_current=True
      - 官方 reference\csv\tpex_daily_quotes_20260923.csv（依證券主檔的 TPEX 支援清單過濾）：1007 列，上漲 432、下跌 427、平盤 119，成交額 2532.3 億；app 9/23 上市成交額 8872.5 億，合計下上櫃約占 22.2%
      - data\user_data\strategy_cache.json：as_of=2026-09-23，其中 1104 個代號欄位全為 .TWSE，.TPEX 為 0
      - data\kline_daily_enriched\date=2026-09-17：6488.TPEX close=912、change_pct=-0.002188，即 (912-914)/914；官方 reference\csv\tpex_trading_stock_202608_202609.csv：9/16 close 937、9/17 close 912、change -25（約 -2.67%）
      - work\V_tpex_gap\latest.py：最新日為 9/22 的有上櫃 1009 檔加上市 1 檔（共 1010），最新日為 9/23 的有上市 1330 檔；institutional date=2026-09-23 分區有上櫃 5670 列，可見同一列會混用兩天的資料
      - docs/taiwan-market-overview.md:10、14：模組涵蓋 TWSE 與 TPEx，選股範圍是 full TWSE/TPEx universe
      - https://en.wikipedia.org/wiki/Advance%E2%80%93decline_line：'the number of issues advancing, i.e., above the previous day's closing price'，漲跌以前一日收盤為基準
      - https://en.wikipedia.org/wiki/Taipei_Exchange：'a non-profit foundation set up to manage over-the-counter (OTC) market ... in Taiwan'，上櫃是獨立於證交所的另一個市場
      - CONTRIBUTING.md 第 11 節第 8 點：『網絡失敗、無權限、空數據、能力缺失和部分寫入時不得產生錯誤狀態』；P1 定義包含『錯誤金融口徑』
      - docs\code-review-2026-09-22.md 已記錄的 B4/B5/B6/B12/C1/C4/C5/N2–N5 都不涉及「單一交易所快照失敗後寫入半份分區」，不是重複回報
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：成立。9/23 16:30 抓上櫃快照時連線中斷（backend.log:37258）。snapshot_provider.py:112-118 只記了 warning，仍把只含上市 1,330 列的 frame 寫成完整分區。之後 daily_update.py:282-286 與 daily_refresh.py:167-170 看到分區已存在就判為最新，21:40 那次也沒有重抓。9/16 的分區有同樣的缺口：上櫃 0 列，寫入時間 9/16 16:30:22；但該日沒有日誌可查，失敗原因屬推測。market_intelligence 與 industry_intelligence 只看最新分區日期，所以 9/23 的 margin 補齊後，overall_status 變成 complete。

重算結果：
- app 顯示 9/23 上櫃 0 列、成交額 0；上市 1,330 列，成交額 8,872.51 億。
- 官方支援的上櫃標的實為 1,007 列：漲 432、跌 427、平 119，成交額 2,532.32 億，約占全市場 22.1%。
- 策略快取 552 筆全部是上市；產業 9/23 的 5 日可比股數 1,077，全部是上市。
- enriched 9/17 上櫃的 change_pct 實際是 2 日漲跌，例如 6488 為 -0.22%，官方是 -2.67%。
- 選股有 1,010 列的 quote_date 退回 9/22，其中 897 列的 institutional_date 是 9/23（不是全部 1,010 列）。

補充：
- API 的 data_quality.missing_symbols_count 其實是 1,047，但前端沒有顯示。
- 9/23 16:30–21:40 之間，因為融資資料還沒到，狀態是 partial。
- 以 9/16 為前一日的 9/17 單日比較，都排除了全部上櫃。
    - 理由：我用自己寫的腳本（放在 work/verify-otc/），直接讀本地 parquet 與官方參考資料 CSV 重算，finding 的核心數值全部重現。
(1) 本地 661 個 data/taiwan/daily 分區中，只有 2026-09-16 與 2026-09-23 的上櫃列數為 0，兩天都只有 TWSE 1,330 列；2026 年 8–9 月其他交易日的上櫃列數介於 1,003 到 1,009，與 finding 相同。
(2) 程式碼行號逐一核對都正確：
- snapshot_provider.py:112-118：TPEx 抓取例外只記 warning，仍回傳只含 TWSE 的 frame。
- daily_refresh.py:167-177：分區已存在就跳過；非空的 frame 直接 write_batch。
- daily_update.py:282-286：已有分區時判為 already current。
- market_intelligence.py:387-392、576-582：只比較 daily_as_of >= target。
- industry_intelligence.py:224-255：同上。
- screener.py:207、215：用 read_latest_per_symbol，daily_as_of 取所有列的最大日期。
- Dashboard.tsx:241：只在 overall_status≠complete 時才顯示警示。
(3) backend.log 的三個行號都對得上：
- 37258：16:30:05 TPEx 失敗（incomplete chunked read）。
- 37265：daily=success。
- 45477：21:40 is_fully_current=True，而且 21:40 那次沒有任何 MI_INDEX 或 dailyQuotes 請求。
另外，9/23 分區的寫入時間是 16:30:05.39，緊接在失敗之後。
(4) 我照 app 的算法重建 9/23 市場統計：上櫃 0 列、成交額 0；上市 1,330 列，成交額 8,872.51 億，漲 538、跌 647、平 137。
依官方 tpex_daily_quotes_20260923 篩出支援的上櫃標的共 1,007 列：依官方漲跌欄計為漲 432、跌 427、平 119（合計 978），成交額 2,532.32 億（finding 寫 2,532.4 億，只是四捨五入的差異）。以官方支援標的總成交額計，上櫃占 22.13%。
9/23 的 margin 與 institutional 分區都存在，依程式邏輯 overall_status 會是 complete。
(5) strategy_cache.json 的 as_of 為 2026-09-23，results 共 552 筆、290 檔，全部是 .TWSE；kline_daily_enriched 的 9/23 分區只有 TWSE 1,323 列。
(6) enriched 9/17 的 6488.TPEX：close 912、prev_close 914（9/15）、change_pct 為 -0.219%。官方 9/16 收 937、9/17 跌 25，應為 -2.668%。
(7) 產業 9/23：5 日基期是 9/16，comparable_5d=1,077，全部是上市，重現。
(8) 選股：quote_date=9/22 的列確實是 1,010 列（上櫃 1,009＋上市 1)。但其中真正「institutional_date=9/23」、同一列混用兩天資料的是 897 列；其餘列的 institutional 日期更舊或是 null。finding 寫 1,010 列略為誇大，但不影響結論。

需要補充的細節：
a. API 的 data_quality 其實有 missing_symbols_count（重建值 1,047），但前端只在 api.ts 型別和測試裡用到，頁面沒有顯示，所以「畫面沒有任何警示」仍然成立。
b. 9/23 的 16:30 到 21:40 之間，margin 還沒更新，overall_status 應為 partial，這段時間會出現一般性的「尚未完整」提示，但原因是融資資料，不是上櫃缺資料。21:40 之後才變成 complete。
c. 9/16 的失敗原因沒有日誌可查：backend.log.2 不存在，backend.log.3 從 9/17 才開始。只能從分區寫入時間 9/16 16:30:22 推斷是同一機制。這點是推測。
d. 新面向：以 9/16 為前一日的所有單日比較都整批排除上櫃。產業 9/17 comparable_1d=1,078，全部是上市。另外，除非強制重抓，9/24 的上櫃 change_pct 預料也會變成對 9/22 的 2 日漲跌（依 9/17 的前例推測）。
e. 範圍外觀察：本地 9/23 上市有 23 檔的成交額低於官方，合計少 39.27 億（2330 少 20.9 億），可能是 16:30 抓到的不是最終值。這與本 finding 無關，建議另案查證。

線上交叉核對：WebSearch 本 session 的額度（200 次）已用完，沒能另查第三方網站。官方數值一律取自已下載的 TWSE／TPEx 參考資料。

嚴重度維持 P1：市場統計、產業、選股、策略口徑都靜默遺漏約 22% 的成交額與 978 家漲跌家數，品質標籤還顯示 complete。資料可以強制重抓修復，不算 P0 的資料破壞。
    - 證據：
      - 本地重算 work/verify-otc/scan2.py：partitions with TPEX==0: 2 → ('2026-09-16', 1330, {'TWSE': 1330})、('2026-09-23', 1330, {'TWSE': 1330})；scan3.py：Aug-Sep TPEX nonzero min/max: 1003 1009
      - backend/app/taiwan/providers/snapshot_provider.py:112-118 TPEx try/except 只 logger.warning，frames 仍只含 TWSE；backend/app/taiwan/daily_refresh.py:167-177 分區已存在即 dates_skipped，非空 df 直接 write_batch
      - backend/app/taiwan/daily_update.py:282-286 d_range 為 None 時 status=success、note='Daily store already current'
      - backend/app/taiwan/market_intelligence.py:387-392（daily_status 只看 daily_as_of >= target）、576-582（三個 dataset 都 current 就 complete）；industry_intelligence.py:224-255 同樣的邏輯
      - data/backend.log:37258 '2026-09-23 16:30:05,341 ... TPEx snapshot fetch failed for 2026-09-23: peer closed connection without sending complete message body (incomplete chunked read)'；:37265 'daily=success ... is_fully_current=False'；:45477 '21:40:10 ... is_fully_current=True'（45470-45477 之間沒有任何 MI_INDEX/dailyQuotes 請求）；分區 date=2026-09-23/part.parquet 修改時間 16:30:05.394
      - repro.py（app 算法重建 9/23）：TWSE rows 1330、turnover 8872.51 億、adv 538 dec 647 flat 137；TPEX rows 0、turnover 0.0；missing_symbols_count 1047
      - repro.py（官方 reference/csv/tpex_daily_quotes_20260923.csv ∩ 支援上櫃標的）：1,007 列；官方漲跌欄 adv 432 / dec 427 / flat 119（=978）；成交額 2532.32 億（finding 寫 2,532.4 億）；官方支援標的總成交額 11,444.1 億，上櫃占 22.13%
      - sc2.py：strategy_cache.json as_of=2026-09-23，results 552 筆（290 檔唯一），Counter({'TWSE': 552})；enr.py：kline_daily_enriched date=2026-09-23 共 1323 列，{'TWSE': 1323}
      - enr.py：enriched 9/17 6488.TPEX close 912、prev_close 914、change_pct -0.002188；官方 reference/csv/tpex_trading_stock_202608_202609.csv：6488 9/16 收 937.0、9/17 收 912.0、漲跌 -25.0（-25/937 = -2.668%）
      - ind.py：9/23 d5=2026-09-16，comparable_5d 1077 {'TWSE': 1077}、comparable_20d 1086 全部是上市；9/17 prev=9/16，comparable_1d 1078 {'TWSE': 1078}（新面向）
      - scr.py：quote_date=9/22 的列 1,010（TPEX 1,009、TWSE 1）；其中 institutional_date=9/23 的 897 列，其餘為更舊日期或 null（finding 的 1,010 略為誇大）
      - frontend/src/pages/Dashboard.tsx:241 dataIncomplete 只看 overall_status；grep missing_symbols_count 只出現在 src/lib/api.ts:1119 與測試檔，頁面沒有顯示
      - WebSearch 本 session 的額度已用完（200/200），沒有額外的線上第三方比對；官方數值取自 scratchpad/audit/reference（manifest failed_requests=[]）

### [P2 → P2] 選股「逼近漲停／跌停（≤3%）」篩選永遠 0 筆：漲跌停價以當日收盤計算，實際是隔日的漲跌停價（確認成立）

- **主張**：_enrich_price_limits 以當日 close 作為參考價計算 limit_up 與 limit_down，所以 distance_to_upper_limit 固定約等於漲跌幅上限（約 10%），不可能 ≤3%。
- **正確的定義或數值**：接近漲停應以當日參考價（昨收或除權息參考價）計算當日漲停價，再與收盤比較距離
- **app 實際行為**：這兩個篩選按下後結果永遠為 0；API 回傳的 limit_up／limit_down 實際是次一交易日的漲跌停價
- **對使用者的影響**：使用者無法用選股頁找出強勢逼近漲停或弱勢逼近跌停的標的，而且畫面上看起來像是市場沒有這類股票
- **修法建議**：以前一交易日收盤（或 TWT49U 參考價）呼叫 calc_limits；若刻意要提供隔日價，應另外命名為 next_day_limit_up，並把篩選改用當日漲停價
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/screener.py:383-385（calc_limits(close, inst)）
  - backend/app/taiwan/screener.py:527-530（distance <= 0.03）
  - frontend/src/pages/TaiwanScreener.tsx:1361-1381（按鈕「逼近漲停 (≤ 3%)」「逼近跌停 (≤ 3%)」）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 以 fake store 執行 9/23 全部 2,343 列：distance_to_upper_limit 最小 0.0948、中位數 0.0992、最大 0.1999；near_upper_limit=True 與 near_lower_limit=True 的 total 都是 0（work/A12/screener_check.py）
  - 2454：app limit_up=5700（以 9/23 收盤 5185 計算），但官方 MIS 9/23 的當日漲停 u=5695（以昨收 5180 計算）。app 給出的是 9/24 的漲停價
  - 已知的 B5 是 pipeline.py 增量路徑缺欄，與本項是不同程式碼路徑、不同成因
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：TaiwanScreenerService._enrich_price_limits（backend/app/taiwan/screener.py:383）用當日收盤價當參考價，呼叫 MarketProfileBridge.calc_limits 算漲跌停價。算出來的 limit_up／limit_down 其實是隔日漲跌停價：866 檔上櫃股票與官方「次日漲停價／次日跌停價」全部一致；2454 的漲停在 app 是 5700，MIS 當日是 5695。因此 distance_to_upper_limit／distance_to_lower_limit 恆約等於漲跌幅上限：一般股約 0.095 到 0.10，槓桿 ETF 約 0.20。「逼近漲停／跌停（≤3%）」篩選和自然語言選股的同名條件一定回傳 0 筆；distance_*_max 在低於約 9.5% 的門檻下同樣失效。9/23 用昨收正確計算時，應命中逼近漲停 19 檔、逼近跌停 5 檔。pipeline.py:1970 的 docstring 已承認 screener 有這個 bug，但 docs 的已知問題清單沒有記錄，所以不算重複回報。修法：以前一交易日收盤計算當日漲跌停價，遇到除權息日則用 TWT49U 的參考價。
    - 理由：這條 finding 成立，嚴重度 P2 合理。我親自讀了程式碼：screener 計算漲跌停價時用的是當日收盤價（close），但法定口徑應該用前一交易日的收盤價或參考價。

以收盤價為基準時，漲停價 = 收盤 × 1.1 再依升降單位向下取整，所以距漲停的距離只剩「漲跌幅上限 − 升降單位／收盤」，最低約 0.095，槓桿 ETF 約 0.2，數學上不可能 ≤ 0.03。「逼近漲停」「逼近跌停」兩個篩選因此一定回傳 0 筆。

我自己寫腳本跑同一套函式，對象是 9/23 上市的 1,330 列：距漲停與距跌停的最小值都是 0.0948、最大值都是 0.1999，兩個篩選都是 0 筆。改用 9/22 收盤當基準重算，則有 19 檔逼近漲停、5 檔逼近跌停，其中 15 檔當天收在漲停。官方 9/23 上市行情也有 15 檔漲幅 ≥ 9.5%。

官方資料也直接證明 app 算出的是隔日漲跌停價：
- 在線上櫃 866 檔股票上，app 公式算出的漲跌停價與櫃買官方「次日漲停價／次日跌停價」欄位全部相同（866/866）。
- MIS 顯示 2454 的昨收是 5180、漲停 5695、跌停 4665；app 給的是 5700／4670。6196、0050、00878 也有同樣的落差。

網路來源（三個頁面都與 finding 不同）：
- 中文維基「漲跌停板制度」：漲跌停是參照前一個交易日的結算價來設上下限。
- 英文維基「Limit up」：price limit 從 previous day's settlement price 起算。
- 中文維基「臺灣證券交易所」：台股每日漲跌幅原則上為 10%。

另外，repo 裡的 `backend/app/indicators/pipeline.py` 在 docstring 自己寫了「基准价一律用 prev_close (法定口徑), 与 screener.py 用 close 的既有 bug 不同」，可見開發者知道這個 bug。但它沒有列入 docs/code-review-2026-09-22.md 已知問題；B5 講的是增量路徑缺欄，是不同的程式碼路徑、不同成因，所以這條不算重複回報。

在使用者情境下：只要開選股頁、按這兩個按鈕，或用自然語言選股帶到 near_upper_limit／near_lower_limit，結果就一定是 0 筆，畫面看起來像市場上沒有強勢或弱勢股。自選裡 00885、00893 被判定為無漲跌幅限制，本來就是 null。監控規則有 0 條，而且監控走的是 pipeline 的昨收版本，不受影響。選股頁前端沒有顯示 limit_up 欄位，所以錯誤的隔日價只出現在 API 回應裡。這屬於明確的功能錯誤，不是核心流程掛掉，也沒有造成交易損失，維持 P2。

finding 有兩處小誤差，不影響結論：
- finding 說 9/23 有 2,343 列，但 9/23 分區實際只有 1,330 列（只有上市）。screener 讀的是每檔最新一筆，所以上櫃股票會退回用 9/22 的資料。
- 修法建議只提到昨收。遇到除權息日時，基準應該是除權息參考價，而 pipeline 的正確版本同樣只用 prev_close，也沒有處理這點。

我這次沒有修改任何 repo 檔案：執行前後 git status 相同，都只有既有的 3 個 M 檔。
    - 證據：
      - backend/app/taiwan/screener.py:337 close = r.get("close")；:383 upper, lower = MarketProfileBridge.calc_limits(close, inst)；:384-385 dist_up=(upper-close)/close、dist_dn=(close-lower)/close
      - backend/app/taiwan/screener.py:527-530 near_upper_limit／near_lower_limit 篩選條件是 distance <= 0.03；:531-534 distance_*_max 篩選也吃同一組錯誤的距離
      - backend/app/taiwan/market_rules.py:309-322 calc_limits_for_pct：up_raw = ref_price*(1+pct)，再依升降單位向下取整（跌停向上取整）
      - backend/app/indicators/pipeline.py:1970 docstring 原文：「基准价一律用 prev_close (法定口徑), 与 screener.py 用 close 的既有 bug 不同」；:1981 ref = pl.col("prev_close")
      - frontend/src/pages/TaiwanScreener.tsx:1361-1381 兩個按鈕「逼近漲停 (≤ 3%)」「逼近跌停 (≤ 3%)」；backend/app/taiwan/screener_nl.py:65 自然語言選股也會帶出 near_upper_limit／near_lower_limit
      - backend/tests/test_taiwan_screener.py:200-236 只測了「不該命中」的情況，沒有任何測試斷言 near_upper_limit 會命中，所以 bug 沒被測試抓到
      - 本地計算：scratchpad\audit\work\V_A12_limits\verify.py（uv run，exit=0）。以 9/23 上市 1,330 列、有漲跌幅限制的 1,156 列跑 app 的 _enrich_price_limits：distance_to_upper_limit min=0.0948、median=0.0992、max=0.1999；near_upper_limit 篩選 0 筆、near_lower_limit 篩選 0 筆。改用 9/22 收盤重算：逼近漲停（≤3%）19 檔、收在漲停 15 檔；逼近跌停 5 檔、收在跌停 3 檔
      - 官方 MIS 快照（reference\csv\mis_snapshot.csv）：2454 y=5180 u=5695 w=4665，app 給 5700/4670；6196 y=535 u=588 w=481.5，app 給 603/494.5；0050 y=111.85 u=123.0，app 給 123.65；00878 y=35.21 u=38.73，app 給 38.59。用昨收重算的結果與 MIS 完全一致
      - 官方櫃買 9/23 收盤行情（reference\raw\tpex_daily_quotes_20260923.json）欄位原文是「次日 參考價」「次日 漲停價」「次日 跌停價」。verify_tpex.py：866 檔上櫃股票的 calc_limits(當日收盤) 與官方次日漲停價、次日跌停價全部相同（866/866），證明 app 算出的就是隔日漲跌停價
      - 官方上市 9/23 行情（reference\csv\twse_mi_index_20260923_quotes.csv）：1,364 檔中漲幅 ≥ +9.5% 有 15 檔、≥ +7% 有 18 檔、≤ -7% 有 3 檔。市場上確實有逼近漲跌停的股票
      - https://zh.wikipedia.org/wiki/涨跌停板制度 原文：「参照前一个交易日的结算价设置报价的上下限」
      - https://en.wikipedia.org/wiki/Limit_up 原文："A price limit is an established amount in which a price may increase or decrease in any single trading day from the previous day's settlement price."
      - https://zh.wikipedia.org/zh-tw/臺灣證券交易所 原文：「每日漲跌幅限制原則上為10%，盤中並設有瞬間價格穩定措施」（此頁沒有寫明基準價）
      - 已知問題比對：docs/code-review-2026-09-22.md:334-337 的 B5 是 pipeline 增量路徑缺欄，與本項不同；docs 內搜尋 screener、逼近、昨收、prev_close，都沒有本項的記錄
      - 我沒有修改任何 repo 檔案：執行前後 git status --porcelain 相同（只有既有的 daily_update.py、pnpm-lock.yaml、vite.config.d.ts）
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：finding 成立，只需小幅補充：screener.py:383 以最新日K的當日收盤呼叫 calc_limits，算出的 limit_up／limit_down 其實是次一交易日的漲跌停價，與 TPEx 官方 next_day_limit 在 867 檔上櫃股票上完全一致；2454 在 9/23 的 app 值是 5700，官方當日漲停是 5695。因此 distance_to_upper/lower_limit 恆落在約 9.5%～10%（兩倍槓桿 ETF 約 20%），實測最小值 0.0948，「逼近漲停／逼近跌停（≤3%）」篩選在 9/23 的 2,346 筆中永遠回 0 筆。改用當日參考價（昨收，除權息日用 TWT49U 參考價）重算後，9/23 收在漲停 15 檔、收在跌停 1 檔，與官方漲跌家數 388(15)／562(1) 完全吻合；距離 ≤3% 的應有逼近漲停 19 檔、逼近跌停 2 檔。選股頁沒有顯示 limit_up 或 distance，使用者看得到的影響只有這兩個篩選失效。pipeline.py:1970 的註解已承認這個 bug，但 code-review 文件沒有記錄，與 B5 不重複。
    - 理由：我自己寫腳本重跑了一遍，finding 的數值差異全部重現，成因與它說的一致。

做法：把 security_master.parquet 和 data/taiwan/daily 複製到 scratchpad（證交所與櫃買的網路 adapter 換成會直接報錯的假物件，法人與融資 store 換成回傳空表），再呼叫真正的 TaiwanScreenerService.run 與 _enrich_price_limits。repo 裡沒有寫入任何檔案：執行後比對 git status 沒有變化；data/backend.log 的新行是使用者正在跑的後端排程寫的，不是我的腳本。

1. 程式碼路徑：screener.py:207 用 read_latest_per_symbol 取每檔最新一列。daily parquet 只有 symbol、date、open、high、low、close、volume、amount、quote_ts 這幾欄，沒有昨收欄位。screener.py:383 用 calc_limits(close, inst) 以「當日收盤」算漲跌停，:528/:530 再篩 distance <= 0.03。數學上 (floor(close×1.1) − close)/close 大約落在 9.5%～10%（兩倍槓桿 ETF 約 20%），不可能 ≤3%。

2. 數值並列（finding → 我的結果）：
- 全體筆數：2,343 → 2,346。差 3 筆，推測是資料時點不同，不影響結論。
- near_upper_limit 與 near_lower_limit 的 total：0 → 0。
- 距漲停距離 min／中位數／max：0.0948／0.0992／0.1999 → 0.09483／0.09920／0.19994。
- 距跌停距離：min 0.09483、中位數 0.09935、max 0.19994。
- 2454：app 的 limit_up 5700 → 同為 5700；官方 MIS 的 u=5695、y=5180。

3. 我另外補的比較：
- 其他自選股，app 的漲停／跌停對照 MIS 的 u／w：6196 是 603／494.5 對 588／481.5；0050 是 123.65／101.25 對 123.0／100.7；00878 是 38.59／31.59 對 38.73／31.69。
- 改用當日參考價重算：以 9/22 收盤為參考價，當日除息的個股改用 TWT49U 的除息參考價。9/23 收在漲停的上市股有 15 檔、收在跌停的有 1 檔（6225）。這和官方 MI_INDEX 9/23 漲跌家數「股票 上漲 388(15)、下跌 562(1)」完全一致；那 16 檔在官方行情的漲跌幅也都在 ±9.6%～10% 之間。同一口徑下，距離 ≤3% 的有：逼近漲停 19 檔、逼近跌停 2 檔。所以 9/23 市場上確實有這類股票，篩選卻回 0 筆。
- 「app 給的其實是次一交易日的漲跌停價」有官方資料佐證：TPEx 9/23 行情有 next_day_limit_up／down 欄位。挑出次日參考價等於當日收盤的 867 檔上櫃股票，拿收盤呼叫 calc_limits 算出的結果，867 檔全部與官方「次日」漲停價和跌停價相同。

4. 重複性：docs/code-review-2026-09-22.md 的 B5 講的是 pipeline.py 增量路徑缺欄，和本項不同。不過 pipeline.py:1970 的程式碼註解已經寫著「与 screener.py 用 close 的既有 bug 不同」，也就是說程式碼裡知道這個問題，但沒有記錄在審查文件或修正。

5. 嚴重度：TaiwanScreener.tsx 沒有顯示 limit_up 或 distance 欄位，使用者看得到的影響只有「逼近漲停／逼近跌停」兩顆按鈕永遠回 0 筆。屬於明確的功能錯誤，P2 合理。

6. 線上核實：WebSearch 的 session 配額已用完。WebFetch 只抓到維基百科「漲停板」的一般定義，台灣的具體規則主要以官方參考資料（MIS 的 y／u／w、TPEx 的次日漲跌停欄位）為證。
    - 證據：
      - backend/app/taiwan/screener.py:383 `upper, lower = MarketProfileBridge.calc_limits(close, inst)`：參考價是當日收盤
      - backend/app/taiwan/screener.py:527-530 near_upper_limit／near_lower_limit 篩 distance <= 0.03
      - backend/app/taiwan/screener.py:207 用 read_latest_per_symbol 取最新一列；daily parquet 的欄位只有 symbol、date、open、high、low、close、volume、amount、quote_ts，沒有 prev_close
      - backend/app/indicators/pipeline.py:1970 註解「基准价一律用 prev_close (法定口徑), 与 screener.py 用 close 的既有 bug 不同」：程式碼已承認這個 bug，但審查文件沒有記錄
      - frontend/src/pages/TaiwanScreener.tsx:216-217 送出 near_upper_limit／near_lower_limit；:1361-1381 是按鈕「逼近漲停 (≤ 3%)」「逼近跌停 (≤ 3%)」；本頁沒有顯示 limit_up 或 distance
      - 腳本：AUDIT_SCRATCH/work/verify-nearlimit/repro.py；輸出在同目錄 result.json。執行指令：cd backend && PYTHONIOENCODING=utf-8 uv run python repro.py
      - 執行結果：run_totals baseline=2346、near_upper_limit=0、near_lower_limit=0、daily_as_of=2026-09-23
      - 執行結果：distance_to_upper_limit min=0.09483、median=0.09920、max=0.19994；distance_to_lower_limit min=0.09483、median=0.09935、max=0.19994；≤0.03 的筆數：上 0、下 0
      - 2454.TWSE：app 的 limit_up=5700、limit_down=4670；官方 MIS（reference/csv/mis_snapshot.csv）u=5695.0、w=4665.0、y=5180.0；以 9/22 收盤 5180 重算得 5695／4665，與 MIS 相同
      - 6196：app 603／494.5，MIS 588／481.5（y=535）；0050：app 123.65／101.25，MIS 123.0／100.7（y=111.85）；00878：app 38.59／31.59，MIS 38.73／31.69（y=35.21）；每一檔以昨收重算都與 MIS 相同
      - 以當日參考價重算（9/22 收盤；除息股用 TWT49U 的 ex_ref_price）：9/23 收在漲停 15 檔（1727、3653、4770、1235、6526、2368、6243、6838、2104、2404、3016、2342、6205、6426、6933），收在跌停 1 檔（6225）；距離 ≤3% 的有：逼近漲停 19 檔、逼近跌停 2 檔
      - 官方 reference/csv/twse_mi_index_20260923_updown_counts.csv：「上漲(漲停), 5,575(67), 388(15)」「下跌(跌停), 7,862(45), 562(1)」，股票欄的 15 與 1 與重算結果完全一致；官方行情中這 16 檔的漲跌幅在 +9.62%～+10.00% 與 -9.94% 之間
      - 官方 reference/csv/tpex_daily_quotes_20260923.csv 的 next_day_limit_up／next_day_limit_down：次日參考價等於收盤的 867 檔上櫃股票，以收盤呼叫 calc_limits 的結果 867 檔全部與官方次日漲跌停相同，證明 app 的 limit_up／limit_down 是次一交易日的價格
      - 線上：https://zh.wikipedia.org/zh-tw/漲停板 原文「指的是在任意一個交易日中參照前一個交易日的結算價設置報價的上下限」（WebSearch 的 session 配額已用完，未能再查券商頁面）
      - 唯讀檢查：執行後 git status 與執行前相同；data/ 底下唯一的新寫入是 data/backend.log 的 05:19:41 apscheduler 排程紀錄，來自使用者正在執行的後端，不含我的腳本路徑

### [P3 → P3] 首頁「策略當日推薦 552 檔」重複加總各策略的命中數，實際不同股票只有 290 檔（確認成立）

- **主張**：totalPicks 是各策略 total 的總和，同一檔被多個策略選中時會重複計算，卻以「檔」作單位顯示。
- **正確的定義或數值**：顯示不重複的標的數（290 檔），或改用「552 筆命中」的措辭
- **app 實際行為**：顯示「2026-09-23 · 552 檔」
- **對使用者的影響**：高估當日候選股數量約 90%
- **修法建議**：以 Set 計算所有 rows 的不同 symbol 數再顯示；或把單位改成「筆」
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/pages/Dashboard.tsx:445
  - frontend/src/pages/Dashboard.tsx:452
- **線上證據**：
  - （無）
- **本地計算證據**：
  - strategy_cache.json（as_of 2026-09-23）：15 個有命中的策略 total 合計 552，不同 symbol 290 檔，其中 170 檔被 2 個以上策略選中（最多 6 個，1102.TWSE）（work/A12/cache_stats.py）
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：原敘述正確，不需要修正。首頁「策略當日推薦」的 hint 把各策略的 total 直接相加，顯示為「2026-09-23 · 552 檔」（Dashboard.tsx:445、452）。但 strategy_cache.json（as_of 2026-09-23）裡 15 個有命中的策略合起來，不同的標的只有 290 檔。其中 170 檔被 2 個以上策略重複選中，最多的是 1102.TWSE，被 6 個策略選中。結果以「檔」為單位時高估了約 90.3%。使用者的監控規則是 0 條，實時結果不會疊加上去，所以首頁顯示的數字就等於快取裡算出的值。
    - 理由：我自己寫了腳本重算，finding 的每個數字都完全吻合。前端 StrategyPicksCard 在 Dashboard.tsx:441-445 取出 rows 非空的策略，把各策略的 r.total 直接相加成 totalPicks，再在 Dashboard.tsx:452 顯示成 `${as_of} · ${totalPicks} 檔`，過程中完全沒有對 symbol 去重。strategy_cache.json 的 as_of 是 2026-09-23，裡面有 20 個策略，其中 15 個有命中。每個策略的 total 都等於 len(rows)，策略內部也沒有重複的 symbol，所以 totalPicks = 552。把 15 個策略的 rows 合起來，不同 symbol 只有 290 檔，全部是 .TWSE。被 2 個以上策略選中的有 170 檔，分布是 1 個策略 120 檔、2 個 105 檔、3 個 45 檔、4 個 14 檔、5 個 5 檔、6 個 1 檔。被最多策略選中的是 1102.TWSE，共 6 個。552/290−1 = 90.34%，和 finding 說的「約 90%」一致。另外用 today_ever_matched 交叉核對：聯集同樣是 290，長度總和同樣是 552。

我也確認了顯示值就等於快取值。/api/screener/cached 讀快取時沒有截斷 rows（screener.py:175-195 只在有 ext_columns 時補欄位）。唯一的疊加來源是 monitor_engine.latest_strategy_results()，而它的 docstring（monitor.py:551-557）寫明沒有 type=strategy 規則時回傳空 dict。使用者的監控規則是 0 條，所以這條路徑不會改動數字，首頁看到的就是「2026-09-23 · 552 檔」。

這個問題沒有記錄在 docs/code-review-2026-09-22.md，Dashboard.test.tsx 也沒有任何測試斷言這段 hint 文字。這行從 d839721（2026-09-18）引入後就沒改過，工作樹對這個檔案沒有未提交的改動。

嚴重度方面，這只是提示文字把命中筆數當成檔數，不影響選股結果或任何交易計算，符合 P3 的「局部體驗」，維持 P3。

這個 finding 是計數邏輯的問題，不涉及官方行情數值，所以沒有比對證交所或櫃買的參考資料，也沒有發任何網路請求。
    - 證據：
      - frontend/src/pages/Dashboard.tsx:441-445：hits = rows.length > 0 的策略；totalPicks = hits.reduce((sum, [, r]) => sum + (r.total ?? 0), 0)，沒有對 symbol 去重
      - frontend/src/pages/Dashboard.tsx:452：hint={cached.data?.as_of ? `${cached.data.as_of} · ${totalPicks} 檔` : undefined}，單位寫成「檔」
      - git blame：這兩行都出自 d8397211（2026-09-18，feat: add six Taiwan radar strategies with a dashboard picks card）；git diff HEAD 對 Dashboard.tsx 沒有輸出，表示工作樹未改動
      - backend/app/api/screener.py:391-412：/cached 回傳完整快取加上監控引擎的實時結果；175-195 的 _cache_payload_with_ext 只補 ext 欄位，不截斷 rows
      - backend/app/strategy/monitor.py:551-557：latest_strategy_results 在「无 type=strategy 规则时返回空 dict」；使用者監控規則 0 條，所以沒有疊加
      - 指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-A12picks/recount.py
      - 輸出：as_of 2026-09-23；results 共 20 個策略；hits (rows.length>0) 15 個；totalPicks 552；len(rows) 總和 552；沒有任何策略 total != len(rows)；每個策略內部重複 symbol 皆為 0
      - 輸出：不同 symbol 290 檔（market suffix 全為 TWSE）；被 2 個以上策略選中的 170 檔；分布 {1:120, 2:105, 3:45, 4:14, 5:5, 6:1}；最多 6 個策略，為 1102.TWSE；552/290−1 = 0.9034
      - 交叉核對：today_ever_matched 聯集 290、長度總和 552，與 results 算出的數字一致
      - 並列比較：finding 的 552 對我算的 552；finding 的 290 對 290；finding 的 170 對 170；finding 的最多 6 個（1102.TWSE）對 6 個（1102.TWSE）；finding 的約 90% 對 90.34%
      - docs\code-review-2026-09-22.md 裡找不到 totalPicks 或策略推薦重複計數的相關記錄；Dashboard.test.tsx:242-293 沒有斷言這段 hint 文字

### [P1 → P1] 除權息日以未還原昨收計算漲跌，造成假下跌、假跌停，並扭曲漲跌幅分布、產業強弱與分組平均（部分成立）

- **主張**：市場漲跌家數、跌停判定、漲跌幅分布、產業單日平均與 5 日報酬、選股 change_pct 與 momentum、自選分組平均，都用原始收盤對原始昨收計算，沒有使用除權息參考價。官方把當日除權息列為「無比價」，漲跌停也以參考價為基準。
- **正確的定義或數值**：除權息日的漲跌應以參考價為基準（或以還原價計算），當日標為無比價；漲跌停以參考價為基準；5 日與 20 日報酬用還原價
- **app 實際行為**：除息金額被當成價格下跌，計入下跌家數與跌停家數；除權息旺季的產業排名與相對強弱會系統性偏向高殖利率產業的弱勢
- **對使用者的影響**：首頁「漲停／跌停」數字錯誤（9/23 顯示 15/3，官方 15/1）；除權息旺季的產業最強、最弱名單約四分之一的交易日不正確；選股的跌幅篩選會把除息股當成重挫股
- **修法建議**：以 TWT49U（及上櫃除權息表）的參考價取代昨收，計算當日漲跌、漲跌停與分類，並把當日除權息列歸入 uncompared；5 日與 20 日報酬、MA、momentum 改用還原收盤（enriched 已有 close 與 raw_close 兩欄，可比照）
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/market_intelligence.py:249-254（以 close 對 prev_close 分類漲跌）
  - backend/app/taiwan/market_intelligence.py:439（calc_limits(prev_close)）
  - backend/app/taiwan/market_intelligence.py:278-289（分布）
  - backend/app/taiwan/industry_intelligence.py:327-343
  - backend/app/taiwan/screener.py:308、312
  - backend/app/taiwan/daily_store.py:38-43（PriceSemantics.RAW）
  - frontend/src/lib/watchlistGroupStats.ts:35-40（收盤後回退到 change_pct）
- **線上證據**：
  - https://www.twse.com.tw/zh/announcement/ex-right/twt49u.html（WebSearch 摘要：除權息交易日的漲跌停價以除權息參考價或減除股利參考價為計算基準）
  - https://investoredu.twse.com.tw/FileSystem/eBook/12_%E8%82%A1%E7%A5%A8%E9%99%A4%E6%AC%8A%E9%99%A4%E6%81%AF%E5%8F%83%E8%80%83%E5%83%B9%E8%A8%88%E7%AE%97%E8%AA%AA%E6%98%8E.pdf（證交所除權息參考價計算說明）
- **本地計算證據**：
  - 官方 MI_INDEX 9/23 標為 X（無比價）的有 6 列：00930、1442、1525、1808、2542、5258；官方漲跌證券數表「股票 無比價 5」
  - app 把其中 5 列算成下跌（00930、1442、1808、2542、5258），1525 因缺前一日資料列為 uncompared。股票下跌 571 對官方 567，ETF 下跌 76 對 75
  - 跌停：app lower_limit_count=3（6225、1442、2542），依規則獨立計算為 1（6225），與官方「股票 下跌(跌停) 562(1)」一致。TWT49U 列出 1442 除息日跌停 25.9、收盤 27.8；2542 跌停 37.35、收盤 39.55，兩檔都沒有跌停
  - 漲跌幅：1442 在 app 為 -10.75%（27.8/31.15），以參考價 28.75 計為 -3.30%；2542 為 -12.98% 對 -4.58%。分布 '<-5%' 段 app 13 檔、以參考價計為 10 檔
  - 自選分組 ic設計 9/8（2379 除息 25 元）：app 平均 -2.412%，以參考價計為 -1.289%
  - 2026-06-01～09-23 共 81 個交易日（只計上市除權息）：首頁「產業強弱 最弱前 5」有 17 天被改變、最強前 5 有 13 天；RS5 前 5 有 21 天、後 5 有 18 天；上市下跌家數累計多算 300 家（單日最多 14 家）；產業單日平均最大偏差 2.88 個百分點（7/16），5 日最大偏差 3.12 個百分點（work/A12/exdiv_bias.py、exdiv_bias.csv）
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：除權息交易日，台股各處都以原始收盤對原始昨收計算漲跌，完全沒有使用官方除權息參考價，影響範圍包括：
- 市場漲跌家數與跌停判定（market_intelligence.py:249-254、439）
- 漲跌幅分布（:278-289）
- 產業單日平均與 5 日、20 日報酬（industry_intelligence.py:327-343）
- 選股的 change_pct、momentum_5d、MA、RSI（screener.py:308、312）
- 台股 enriched 的 change_pct、momentum、MA 與漲跌停基準（enriched_refresh.py:95 不傳復權因子；pipeline.py:754-760 在台股恆用 raw_close.shift(1)）
- 自選分組平均與個股漲跌幅：watchlistGroupStats.ts 的 rt_pct 從未被賦值，所以一律使用 enriched 的 change_pct，不只是收盤後才回退

官方把當日除權息列為「無比價（X）」，漲跌停以參考價計算，由 TWT49U 的漲停價格與跌停價格重現證實。

後果：
- 9/23 的 1442、2542 被誤判為跌停，首頁顯示 3 檔，官方為 1 檔。
- 除息金額被計為下跌。
- 自選的 2379 在 9/8 顯示 -5.08%，以參考價計應為 -1.70%。自選 9 檔在 6/1～9/23 期間有 7 次除權息日都會出現這類錯誤。
- 上櫃也受影響（9/23 有 1595、4109），finding 只計上市，低估了範圍。

修正方向：以上市 TWT49U 與上櫃除權息參考價取代昨收，或把當日除權息列歸入 uncompared。由於台股 enriched 目前 close 等於 raw_close，沒有現成的還原價可比照，多日報酬與 MA 需要先為台股建立復權因子，才能符合 CONTRIBUTING 3.2「enriched close 為前復權價」的契約。
    - 理由：這條 finding 的核心主張成立，嚴重度 P1 也恰當。

一、程式碼（行號與 finding 所列一致，沒有偏移）
- 市場漲跌家數以 close 對 prev_close 分類（market_intelligence.py:249-254）。prev_close 是前一日 daily_store 的原始收盤（:401-405）。跌停判定用 calc_limits(prev_close)（:439）。漲跌幅分布也用 close/prev_close（:278-289）。
- 產業單日平均與 5 日、20 日報酬用原始 close_prev、close_5d（industry_intelligence.py:327-343）。選股的 change_pct、momentum_5d、MA、RSI 也全部用原始 close（screener.py:308、312）。
- 整個台股路徑都沒有除權息參考價：repo 內 grep 不到 TWT49U 或 ref price 的使用；data/adj_factor 有 0 個檔。

二、正確規則（官方資料，取自已下載的參考資料）
- MI_INDEX 9/23 的附註寫明「"無比價"含前一日無收盤價、當日除權、除息、新上市、恢復交易者」，且「+/-/X表示漲/跌/不比價」。
- TWT49U 的欄位有「除權息參考價」「漲停價格」「跌停價格」，並附參考價公式。官方漲跌停可以用參考價加升降單位進位完全重現，例如：
  - 1442：28.75 → 31.6／25.9
  - 2542：41.45 → 45.55／37.35
  - 以原始昨收計算，則會得到 34.25／28.05 與 49.95／40.95。
- STOCK_DAY 把 2379 的 9/8 標為 X、漲跌 0.00。

三、線上佐證
- 找到的是與 finding 不同來源的定義性佐證：
  - HiStock 除權息表列出 9/23 的 1442、2542、1808、5258、00930 為除息，股利分別為 2.4、4、1.5、1.0073、0.815。
  - 中文維基「除權」提供參考價公式。
  - 英文維基 Ex-dividend date 說明除息日開盤價依股利金額下調，屬於機械性調整。
- 沒找到的：線上明文寫出「漲跌停以參考價為基準」的條文頁。WebSearch 額度已用盡；抓過的券商與教學頁（永豐 richclub、rich01、winvest、cmoney、moneydaily）都沒有提到這條規則。這一點改以官方 TWT49U 資料證實。

四、本地重算（唯讀）
- 9/23 在 daily_store 內的漲跌幅：
  - 1442：-10.754%（以參考價計為 -3.304%），被判為跌停。
  - 2542：-12.981%（-4.584%），被判為跌停。
  - 1808：-8.857%（-4.776%）。
  - 5258：-3.184%（-0.821%）。
  - 00930：-3.426%（-0.125%）。
- 1525 沒有前一日資料（官方同列為無比價，推測是恢復交易）。6225 是真跌停。
- 2379 在 9/8 的 enriched 資料：close = raw_close = 692，change_pct = -5.075%（以參考價 704 計為 -1.705%）。與 2454（-1.050%）、3034（-1.111%）的等權平均為 -2.412%，以參考價計為 -1.289%，與 finding 的數字一致。

五、在使用者情境下會發生
- 2026-06-01～09-23 期間，TWT49U 裡自選 9 檔共有 7 次除權息：6196（6/24）、2454（7/7）、3034（7/10）、0050（7/21）、3545（8/4）、00878（8/18）、2379（9/8）。
- 上市除權息共 994 筆，單日最多 61 筆（7/16）。
- 首頁 Dashboard.tsx:266、282 會顯示跌家數與「漲停／跌停」；TaiwanScreener.tsx:570、584 也會顯示。

六、嚴重度
CONTRIBUTING.md 第 11 節把「錯誤金融口徑」列為 P1。第 3.2 節也要求漲跌停依「對應交易日規則」判斷，並規定 enriched 的 close 為前復權價。台股的實作兩條都違反，所以維持 P1。

七、需要修正或補充 finding 的地方
1. 自選分組的機制描述不精確：rt_pct 在整個 repo 裡從來沒有被賦值（grep 只有讀取端），分組統計一律使用 change_pct，不是「收盤後才回退」。盤中即時寫入的 prev_close 來自 MIS 的 y 欄位，除息日是否等於參考價「未驗證（推測可能是）」；但收盤後 enriched 重算的結果確定是用原始昨收。
2. 修正建議的前提有誤：enriched_refresh.py:95 以 factors=pl.DataFrame() 呼叫 compute_enriched，台股 enriched 的 close 等於 raw_close，沒有可以「比照」的還原價。所以 enriched 的 change_pct、momentum、MA，以及 pipeline.py:754-760 的漲跌停基準（_adj_changed 在台股恆為 false），在除權息日同樣錯。
3. 影響範圍被低估：finding 只計上市。上櫃 9/23 的 dailyQuotes 也有 7 列標為「除息」，含 1595 川寶、4109 加捷生醫兩檔股票，同樣受影響。
    - 證據：
      - backend/app/taiwan/market_intelligence.py:249-254 comparable = prev_close 非空；advance/decline 以 close 對 prev_close 比較；:401-405 prev_close = 前一日 daily_store 的 close
      - backend/app/taiwan/market_intelligence.py:439 `upper, lower = MarketProfileBridge.calc_limits(prev_close, inst)`；:278-289 _change = close/prev_close - 1
      - backend/app/taiwan/industry_intelligence.py:327-343 change_pct = close/close_prev-1、return_5d = close/close_5d-1、return_20d 同理，全部是原始 close
      - backend/app/taiwan/screener.py:308、312 change_pct 與 momentum_5d 用原始 close 的 shift(1)、shift(5)；:482-485、505-508 用它們篩選
      - backend/app/taiwan/enriched_refresh.py:95 `compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)`；data\adj_factor 內 0 個 parquet 檔（本地 glob 結果）
      - backend/app/indicators/pipeline.py:754-760 只有 adj_factor 改變時才改用參考昨收；台股沒有因子，所以恆用 raw_close.shift(1)
      - CONTRIBUTING.md:98-103 規定 enriched close 為前復權價，且漲跌停須依對應交易日規則判斷；:318 P1 包含「錯誤金融口徑」
      - frontend/src/lib/watchlistGroupStats.ts:35-40 採 rt_pct ?? change_pct；grep 顯示 frontend 與 backend 都沒有任何地方賦值 rt_pct，所以實際一律使用 change_pct
      - frontend/src/pages/Dashboard.tsx:266、282 首頁顯示跌家數與「漲停 / 跌停」；TaiwanScreener.tsx:570、584 也顯示
      - 官方 MI_INDEX 9/23 附註（reference\raw\twse_mi_index_20260923.json）：「"無比價"含前一日無收盤價、當日除權、除息、新上市、恢復交易者」；「+/-/X表示漲/跌/不比價」；報價 CSV 中 00930、1442、1525、1808、2542、5258 的 change_sign 為 X
      - 官方 TWT49U（reference\csv\twse_twt49u_20260601_20260923.csv）：1442 前收 31.15、參考價 28.75、漲停 31.6、跌停 25.9；2542 前收 45.45、參考價 41.45、漲停 45.55、跌停 37.35。以參考價乘 1.1／0.9 再依升降單位進位，可以完全重現官方漲跌停
      - 官方 STOCK_DAY：2379 在 2026-09-08 收 692、change_flag=X、change_raw=X0.00；前一日收 729；TWT49U 參考價 704
      - 本地重算 `uv run python ...\audit\work\V_exdiv_def\check.py`：1442 app_pct=-10.754%、app 跌停界線 28.05、app_is_limit_down=True，以參考價計為 -3.304%；2542 為 -12.981%、界線 40.95、True，以參考價計為 -4.584%；1808 為 -8.857%，以參考價計為 -4.776%；5258 為 -3.184%，以參考價計為 -0.821%；00930 為 -3.426%，以參考價計為 -0.125%；6225 為 -9.942%、True（真跌停）
      - 本地重算 `enriched_check.py`：kline_daily_enriched 在 2026-09-08 的 2379 為 close=692.0、raw_close=692.0、change_pct=-0.05075；2454 為 -0.01050；3034 為 -0.01111。三檔平均 -2.412%，以參考價計為 -1.289%
      - TWT49U 自選除權息事件：6196（6/24）、2454（7/7）、3034（7/10）、0050（7/21）、3545（8/4）、00878（8/18）、2379（9/8）；期間上市共 994 筆，單日最多 61 筆（7/16）
      - 上櫃 dailyQuotes 9/23（reference\csv\tpex_daily_quotes_20260923.csv）：7 列 change_raw=除息，其中股票為 1595 川寶（收 78.5）與 4109 加捷生醫（收 11.05），代表上櫃同樣受影響，finding 沒有計入
      - https://histock.tw/stock/dividend.aspx：2026/09/23 除息列表含 1442 名軒（息 2.4、股價 27.80）、2542 興富發（息 4、股價 39.55）、1808 潤隆（息 1.5）、5258 虹堡（息 1.0073）、00930（息 0.815）
      - https://zh.wikipedia.org/zh-tw/除權：「除權參考價格為除權前一日收盤價除以1.025」（配股 25‰ 的例子），並提供參考價公式
      - https://en.wikipedia.org/wiki/Ex-dividend_date："The opening price on the ex-dividend date, in comparison to the previous closing price, can be expected to decrease by the amount of the dividend."
      - 線上沒找到明文寫出「除權息日漲跌停以參考價為基準」的非官方頁面：WebSearch 額度已用盡；永豐 richclub、rich01、winvest、cmoney、moneydaily 的頁面都沒有提到。這條規則改以官方 TWT49U 的漲停／跌停欄位重現證實
  - **reproduction**：partially_confirmed；修正後嚴重度 P1
    - 修正後敘述：市場漲跌家數、跌停判定、漲跌幅分布（market_intelligence.py:249-253、:278-289、:439），產業單日平均與 5 日報酬、RS5（industry_intelligence.py:327-343），以及選股的 change_pct 與 momentum_5d（screener.py:308、:312），都以原始收盤對原始昨收計算，沒有使用除權息參考價。官方的做法是把除權息當日列為「無比價」，漲跌停也以參考價為基準（TWT49U、MI_INDEX 可證）。

9/23 的實測：
- app 的股票下跌 571 檔，官方口徑為 567 檔；ETF 下跌 76 對 75。
- 跌停 app 為 3 檔（6225、1442、2542），官方為 1 檔（6225）。
- 1442 的漲跌幅 app 為 -10.75%，以參考價計為 -3.30%；2542 為 -12.98% 對 -4.58%。
- 分布 '<-5%' 段 app 為 13 檔，以參考價計為 10 檔。

6/01～9/23 共 81 個交易日（只修正上市除權息事件）：
- 首頁產業強弱最強前 5 有 13 天、最弱前 5 有 17 天的成員被改變。
- RS5 前 5 有 21 天、後 5 有 18 天被改變。
- 產業單日平均最大偏差 2.88 個百分點，5 日最大偏差 3.12 個百分點（都在 7/16 水泥工業）。
- 上市下跌家數的多算量取決於口徑。依官方無比價口徑：上市股票累計多算 662 家、單日最多 37 家；含 ETF 累計 816 家、單日最多 59 家。只有改以參考價比較、除權息列仍計漲跌時，才是約 300 家（我重算為 302 家、單日最多 14 家）。

上櫃除權息沒有納入修正，所以以上是下限。

自選分組的部分只在日K備援層成立。台股自選列不會有 rt_pct；change_pct 來自即時報價鏈，第一層 MIS 的 y 欄位推測就是參考價（尚未以除權息日的實際回應驗證），官方快照層的除權息日 Change 為 X0.00（換算為 0%）。只有所有即時與快照來源都失敗、退回日K時，才會出現像 -2.41% 對 -1.29% 這樣的偏差。而且 ic設計分組在 9/17 才建立，9/8 的例子是假設情境。
    - 理由：我寫了自己的腳本（放在 work/verify-exdiv/），用本地 parquet 與官方參考資料（MI_INDEX 9/23、TWT49U 6/01～9/23、STOCK_DAY）重算。finding 的核心主張與大部分數字都能重現，而且是逐位相同：9/23 股票下跌 571 對 567、ETF 76 對 75；跌停 3 對 1；1442 為 -10.75% 對 -3.30%，2542 為 -12.98% 對 -4.58%；分布 '<-5%' 段 13 對 10；產業平均漲跌幅最強前 5 被改變 13 天、最弱前 5 為 17 天，RS5 前 5 為 21 天、後 5 為 18 天；產業單日最大偏差 2.88pp（7/16），5 日最大偏差 3.12pp。

官方依據已從本地參考資料確認：
- MI_INDEX 在 9/23 把 5 檔除息股加上 1525 共 6 列標為 X（無比價），漲跌證券數表也列出「股票 無比價 5」。
- TWT49U 的漲跌停價由除權息參考價推算：例如 1442 以 28.75×0.9 取 tick 得 25.9，不是用昨收 31.15 算出的 28.05。
- STOCK_DAY 裡 2379 在 9/8 的漲跌欄是「X0.00」。

程式碼行號全部核實：market_intelligence.py:249-253、:439；screener.py:308、:312；daily_store.py:38-43；watchlistGroupStats.ts:35-40。以 CONTRIBUTING 第 11 節「錯誤金融口徑」判斷，P1 成立。docs/code-review-2026-09-22.md 沒有記錄除權息參考價這個問題。

需要修正的有兩點：
1. 上市下跌家數累計多算 300 家：只有在「上市股票、改以參考價比較、除權息列仍計漲跌」這個口徑下才接近。我算出 302 家、單日最多 14 家，與 300 相差 2 家，原因未查明。依 finding 自己主張的官方口徑（除權息列歸為無比價），多算量是：只算上市股票 662 家、單日最多 37 家；上市股票加 ETF 816 家、單日最多 59 家。所以 300 低估了偏差。而且這個 300 與它 9/23 的比較口徑不一致：9/23 的 571 對 567 用的是無比價口徑。
2. 自選分組：以日K算出 -2.412% 對 -1.289% 的算術正確，但它主張的機制大致不成立：
   - 台股自選列從來不設 rt_pct，backend 與 frontend 都沒有賦值。
   - change_pct 來自即時報價鏈，第一層是 MIS，用的是 y 欄位。參考資料把 y 標為 y_prev_close_ref_price，漲跌停價 u/w 也由 y 推算；推測 y 在除權息日就是參考價，這點沒有用除權息日的實際 MIS 回應驗證。
   - 官方快照層以 close − Change 回推昨收，而官方在除權息日的 Change 是 X0.00，換算結果是 0%，不是假下跌。
   - 只有最後一層日K備援（watchlist_enrichment.py:110-135）才會出現 finding 所說的偏差。
   - ic設計分組在 2026-09-17 才建立，9/8 當時還不存在，所以這個例子是假設情境。

另外補充：多日分析只修正了上市（TWT49U）的除權息事件，上櫃除權息沒有納入，所以產業指標的偏差是下限。web 查證方面，WebSearch 額度已用完；WebFetch 試了維基百科與鉅亨，都沒有找到直接說明參考價基準的頁面。官方口徑改以本地官方下載資料為準。
    - 證據：
      - 腳本：AUDIT_SCRATCH/work/verify-exdiv/snap_0923.py（執行：cd backend && uv run python <腳本>）輸出：App 邏輯 stock {adv 391, dec 571, flat 124, uncompared 2} 跌停 3；etf dec 76。修正後（除權息列歸無比價、以參考價計漲跌停）stock dec 567、uncompared 6、跌停 1；etf dec 75。漲停兩者都是 15。
      - 同一腳本：app 的跌停列為 1442（close 27.8，prev 31.15，以參考價算的跌停 25.9）、2542（39.55／45.45／37.35）、6225（46.2／51.3／46.2）；TWT49U 9/23 的 1442 limit_down_twd=25.9、2542=37.35。
      - 同一腳本：1442 chg_app -0.107544 對 chg_ref -0.033043；2542 為 -0.129813 對 -0.045838；1808 為 -0.088571 對 -0.047761；5258 為 -0.03184 對 -0.008215；00930 為 -0.03426 對 -0.001251。
      - 同一腳本：分布（app，以原始昨收）'<-5%'=13；以參考價為基準=10；排除除權息列=10。
      - 官方 reference/csv/twse_mi_index_20260923_quotes.csv 的 change_sign=X 共 6 列：00930、1442、1525、1808、2542、5258；twse_mi_index_20260923_updown_counts.csv 為「下跌(跌停) 562(1)」「無比價 5」「上漲(漲停) 388(15)」。以 MI_INDEX 逐檔符號、按 app 的 stock 分類重算，下跌 567 檔（含 3 檔 TDR，所以與官方表的 562 不同）。
      - 1525.TWSE 在 9/22 分區的 close=None、volume=17，因此 app 列為 uncompared。
      - watch_0908.py：TWT49U 2379 在 2026-09-08 除息 25 元，參考價 704；日K 2379 692/729 = -5.0754%、2454 -1.0504%、3034 -1.1111%；分組平均原始 -2.4123%，以參考價計 -1.2887%；STOCK_DAY 2379 在 9/8 的 change_raw='X0.00'。
      - data/user_data/watchlist.parquet：3034、2379、2454 的 added_at 為 2026-09-17T02:17–02:18，所以 ic設計分組在 9/8 還不存在。
      - backend/app/taiwan/watchlist_enrichment.py:245 的 change_pct 取自 q.change_pct（即時報價鏈）；grep 整個 frontend/src 與 backend/app，都沒有任何地方賦值 rt_pct；backend/app/taiwan/realtime/mis_provider.py:160 prev_close=item['y']；backend/app/taiwan/enrichment/quote.py:82 prev_close=close-Change；只有 watchlist_enrichment.py:110-135 的日K備援用原始前收。
      - reference/csv/mis_snapshot.csv 的欄名為 y_prev_close_ref_price；2454 的 y=5180、u=5695、w=4665，與 y×1.1 和 y×0.9 取 tick 後相符。推測 y 就是漲跌停基準（參考價），但沒有除權息日的樣本可以驗證。
      - multiday.py（2026-06-01～09-23 共 81 個交易日，只修正 TWT49U 事件，事件窗口為 (前一可用日, T]）輸出：avg_top_set 13、avg_bot_set 17、rs_top_set 21、rs_bot_set 18（依順序比較則為 19/31/36/42）；產業單日平均最大偏差 2.8845pp（2026-07-16 水泥工業）；5 日 3.1162pp（同日同產業）；RS5 2.8866pp。
      - multiday.py 的下跌家數多算量：twse_stock 以參考價口徑合計 302、單日最多 14；以無比價口徑（排除除權息列）合計 662、單日最多 37；twse_all（含 ETF）以無比價口徑合計 816、單日最多 59，以參考價口徑 375／29。逐日明細在 work/verify-exdiv/breadth_daily.csv。
      - 日K 分區缺 2026-07-10，但 TWT49U 列有 10 筆 7/10 除權息（例如 3034 pre_ex_close 542 就是 7/9 的收盤）；institutional／margin／index_daily 也都沒有 7/10。
      - 程式碼行號核實：market_intelligence.py:249（comparable）、:252-253（advance/decline）、:278-289（分布）、:439（calc_limits(prev_close, inst)）；industry_intelligence.py:327-343（change_pct／return_5d／return_20d 都用原始 close）；screener.py:308（change_pct）、:312（momentum_5d）；daily_store.py:38-43（PriceSemantics.RAW）；Dashboard.tsx:282 顯示 totals.upper_limit_count / lower_limit_count，:329-334 依 average_change_pct 取前 5 與後 5。
      - 網路：WebSearch 額度已用完（200/200），無法使用；WebFetch https://zh.wikipedia.org/wiki/除權 只取得除權參考價公式，沒有漲跌停基準的說明；https://www.cnyes.com/twstock/Dividend 回 404。官方口徑改以本地下載的 TWT49U／MI_INDEX／STOCK_DAY 為證據。

### [P2 → P1] 全市場法人買賣超以「股數加總」呈現，混入 ETF 單位數與上櫃權證，方向可能與金額相反（部分成立）

- **主張**：market_intelligence 直接加總 store 中所有列的 foreign_net、investment_trust_net、dealer_net 股數（上市 T86 ALLBUT0999 不含權證，上櫃 sect=AL 含權證），前端再除以 1000 顯示為「張」。不同價位的標的相加後，股數無法代表資金流向。
- **正確的定義或數值**：全市場法人買賣超以金額（元或億）呈現，如同官方三大法人買賣金額統計表；若要用張數，應排除權證並分開股票與 ETF
- **app 實際行為**：以跨標的股數加總呈現，而且上市與上櫃的涵蓋範圍不一致（上櫃含權證）
- **對使用者的影響**：首頁與選股頁的「外資／投信買賣超」可能與實際資金方向相反（例如 9/23 投信實際賣超，畫面卻顯示紅色買超），誤導盤勢判讀
- **修法建議**：改以金額呈現：抓取 BFI82U，或用逐檔淨股數乘以均價或收盤估算並註明；彙總前先以證券主檔過濾掉權證；股票與 ETF 分開顯示
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/market_intelligence.py:495-507
  - frontend/src/pages/Dashboard.tsx:286-291（外資買賣超 … 張）
  - frontend/src/pages/TaiwanScreener.tsx:606-629（外資／投信買賣超，全市場總計（股數/1000））
- **線上證據**：
  - https://www.twse.com.tw/zh/trading/foreign/bfi82u.html（WebSearch 結果標題：「三大法人買賣金額統計表 - TWSE」，官方全市場法人統計以金額計；依規定未直接連線）
- **本地計算證據**：
  - 9/23 app 投信合計 +20,232,552 股（畫面顯示 +20,233 張，紅色），但上市投信的 Σ（逐檔淨股數×收盤）約為 -47.85 億（work/A12/fvalue.py），方向相反
  - 9/23 上市外資：股票淨股數 -161,698,329 股（賣超），以收盤估算的金額約 +333.7 億（買超）；ETF +314,411,657 股只值約 +37.4 億
  - 9/23 法人 store 有 6,999 列，其中 4,800 列不在證券主檔（上櫃權證等），貢獻自營商 +11,947,000 股（占自營合計 4.5%）；9/22 為 -20,789,000 股
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：market_intelligence.py:495-507 把 store 當日所有列的外資、投信、自營淨股數直接跨標的加總，混入 ETF 單位數；上櫃因 sect=AL 還混入約 4,600～5,200 列權證（上市 ALLBUT0999 不含權證）。首頁（Dashboard.tsx:286-291）與選股頁（TaiwanScreener.tsx:605-629）再把總和除以 1000，以「張」加紅綠色顯示。市場層級的法人買賣超慣例以金額計，上市、上櫃分開（Yahoo 奇摩股市、HiStock 皆以億元呈現）。股數加總常與資金方向相反：2026-09-23 投信畫面顯示 +20,233 張（紅），官方金額為集中 -47.59 億、櫃買 -8.17 億（賣超）。以收盤估算，119 個交易日中投信有 44 天、外資有 23 天方向相反。權證列對數值影響小（自營約 4.5%），主因是跨價位的股數加總。同一個 total_net 也被放進 research_context 的 institutional_market_net，但目前沒有送入 LLM。依 CONTRIBUTING §11，錯誤金融口徑屬於 P1。
    - 理由：結論：finding 成立。嚴重度建議從 P2 上調為 P1，理由是 CONTRIBUTING §11 把「錯誤金融口徑」列為 P1，這個問題正好屬於這一類。

一、程式碼（依現況行號）
- market_intelligence.py:495-507 用 read_range(None, target, target) 讀出當日所有列，直接對 foreign_net／investment_trust_net／dealer_net 做 sum()，沒有依證券主檔、商品類別或價格過濾或加權；模型註解（:121-129）也寫明單位是 shares。
- 資料來源端：上市用 T86 selectType=ALLBUT0999（institutional.py:47），上櫃用 dailyTrade sect=AL（institutional.py:152）。refresh（institutional_margin_refresh.py:179-220）把兩者的所有列原樣寫入 store，沒有過濾。
- 前端：Dashboard.tsx:286-291 顯示為「外資買賣超 ±X 張」，紅綠色依正負號，沒有註明口徑；TaiwanScreener.tsx:605-629 顯示外資、投信，底下小字「全市場總計 (股數/1000)」。
- finding 沒提到的面向：research_context.py:639 把同一個 total_net（股數，含權證與 ETF）放進 MarketContextEvidence.institutional_market_net（:98），欄位沒有標示單位。ai_research.py:146-150 目前只送漲跌家數與成交額給 LLM，所以尚未造成實際影響，屬潛在誤用。

二、正確定義（線上來源，與 finding 引用的 BFI82U 不同）
- Yahoo 奇摩股市把大盤三大法人分成「集中市場三大法人買賣金額」與「櫃買市場三大法人買賣金額」，單位為億元。
- HiStock「近三十日上市三大法人買賣超」單位也是億元。
兩者都以金額計，上市與上櫃分開呈現；兩個網站都沒有「跨標的股數加總」這種口徑。

三、使用者情境下確實會發生（9/23 實例）
- 畫面顯示的投信是 store 股數合計 +20,232,552 股，即紅色 +20,233 張（買超）。
- 官方金額：集中市場投信 -47.59 億，櫃買市場投信 -8.17 億，合計約 -55.8 億（賣超），方向相反。
- 本地估算（store 逐檔淨股數 × 官方 9/23 收盤）：投信 -54.75 億；改用均價是 -56.10 億。只算上市（官方參考 T86 × MI_INDEX 收盤）是 -47.85 億，和 Yahoo／HiStock 的 -47.59 億幾乎一致，因此以收盤估算的方法可信。
- 同日外資：股數 +123.0M，金額約 +199 億（官方 373.13 - 174），方向相同，但股數的大小沒有金融意義。
- 9/22 上市投信官方為 -3.66 億，store 全市場投信股數卻是 +15.9M。

四、長期頻率（推測程度中等，因為用 store 日線收盤近似，而且 9/16、9/23 的價格覆蓋不完整）
- 119 個 store 日期中，股數加總與金額估算的正負號相反：投信 44 天、外資 23 天、自營 14 天。
- 也就是說，選股頁的投信卡約 37% 的交易日顏色方向錯誤，首頁外資卡約 19%。

五、權證涵蓋不一致成立，但對數值的影響很小
- 官方參考 T86 ALLBUT0999 中，符合 ^0[3-8]\d{4}$ 的上市權證代號為 0 列。
- TPEx sect=AL 9/23 有 4,634 列 7xxxxx 代號，名稱如「700024 新應材統一64購02」。
- store 9/23 共 6,999 列，其中 4,800 列不在證券主檔（上櫃 7xxxxx 有 4,769 列），貢獻自營 +11,947,000 股（佔 4.48%）；9/22 為 -20,789,000 股。這些數字都與 finding 相符。

六、finding 的小瑕疵（不影響結論）
- 「上市外資股票估算 +333.7 億」「ETF 約 +37.4 億」這兩個數字我沒有精確重現。我依證券主檔分類得到上市股票 -161,729,329 股（加上不在主檔的列正好是 -161,698,329）；以收盤估算，股票約 +300 億級，ETF 約數十億（依分類方式介於 +37 到 +54 億）。方向與量級都和 finding 一致。
- finding 的線上證據只有搜尋結果標題；我補上了兩個實際抓到數值的來源。

七、嚴重度
- 這是金融口徑錯誤：市場層級的買賣超慣例以金額計，跨價位標的的股數加總沒有意義，而且方向常常相反。它出現在首頁與選股頁的主要盤勢指標上。
- 依 CONTRIBUTING §11，錯誤金融口徑屬於 P1。
- 可以降級的理由：只影響資訊展示，沒有進入策略、回測或告警；選股頁有標「股數/1000」。如果團隊把純展示指標排除在 P1 之外，P2 是下限。

八、附帶觀察（新問題，與本 finding 分開）
- store 9/23 有 24 列的數值與現行官方 T86 不同，多數是投信欄；store 投信合計 +20,232,552，現行官方為 +12,743,511。
- store 分區寫於 9/23 16:30，參考資料在 9/24 01:51 下載。institutional_margin_refresh.py:166-169 對已存在的日期直接跳過，所以交易所事後的更正永遠不會被抓回（推測：證交所事後修正了投信資料）。這不改變本 finding 的方向結論。
    - 證據：
      - backend/app/taiwan/market_intelligence.py:495-507（read_range(None,target,target) 後直接 sum 三個 *_net 欄，未過濾、未加權）；:121-129 模型註解 'in shares'
      - backend/app/taiwan/enrichment/institutional.py:47（T86 selectType=ALLBUT0999）、:152（TPEx dailyTrade sect=AL）
      - backend/app/taiwan/institutional_margin_refresh.py:179-220（上市＋上櫃所有列原樣 write_batch，無證券主檔過濾）；:166-169（已存在日期直接跳過，不會重抓交易所的更正）
      - frontend/src/pages/Dashboard.tsx:286-291（label「外資買賣超」，value = foreign_net/1000 + ' 張'，以正負號著色，無口徑說明）
      - frontend/src/pages/TaiwanScreener.tsx:605-629（外資／投信買賣超 /1000 張，小字「全市場總計 (股數/1000)」於 :615、:628）
      - backend/app/taiwan/research_context.py:639（institutional_market_net=market_snap.institutional.total_net，股數含權證／ETF，:98 欄位未標單位）；ai_research.py:146-150 目前未把它送入 LLM
      - 線上：https://tw.stock.yahoo.com/institutional-trading —「集中市場三大法人買賣金額」資料時間 2026/09/23：外資 373.13、投信 -47.59、自營商 58.25（億）；「櫃買市場三大法人買賣金額」2026/09/23：外資 -174、投信 -8.17、自營商 -9.05（億）
      - 線上：https://histock.tw/stock/three.aspx —「近三十日上市三大法人買賣超」單位億：09/23 外資 373.13、投信 -47.59、自營(總) 58.25；09/22 外資 453.21、投信 -3.66、自營(總) 158.54
      - 本地 work/V_inst_def/check.py：store 9/23 共 6,999 列，sum 為 F +123,023,127／IT +20,232,552／D +266,465,131；不在證券主檔 4,800 列（TPEX 7xxxxx 4,769 列），貢獻 D +11,947,000；9/22 NOT_IN_MASTER 的 D 為 -20,789,000
      - 本地 work/V_inst_def/value.py（官方參考 T86 × MI_INDEX 收盤）：上市 9/23 IT 股數 +21,397,398、金額 -47.85 億；F +371.13 億；D +58.52 億（與 Yahoo/HiStock 的 373.13／-47.59／58.25 吻合）。上市 T86 符合 ^0[3-8]\d{4}$ 的權證 0 列；TPEx sect=AL 9/23 有 4,634 列 7xxxxx（例：700024 新應材統一64購02）
      - 本地 work/V_inst_def/storeval.py（store × 官方 9/23 收盤）：全市場 IT 股數 +20,232,552、金額 -54.75 億（用均價為 -56.10 億）；F 股數 +123,023,127、金額 +198.64 億
      - 本地 work/V_inst_def/history.py（119 個 store 日期 × store 日線收盤）：股數與金額正負號相反的天數，投信 44、外資 23、自營 14；例：9/22 IT 股數 +15,949,609、估算金額 -22.9 億
      - 本地 work/V_inst_def/diff.py：store 與現行官方 T86 在 9/23 有 24 列不一致（IT 合計 store +20,232,552、官方 +12,743,511）；store 分區 mtime 為 2026-09-23 16:30，參考資料下載時間為 2026-09-24 01:51
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：market_intelligence.py:503-507 直接加總法人 store 當日所有列的淨股數，前端再除以 1000，以「張」顯示外資（首頁、選股頁）與投信（選股頁）的全市場買賣超。跨價位標的的股數相加（ETF 單位數占很大比重），方向可能與實際金額相反。

- 投信（已重現兩天）：
  - 9/23 app 顯示 +20,232.552 張（紅色，張數帶小數，不是 +20,233 張）。上市投信實際為賣超 47.59 億（HiStock 上市三大法人表）；以逐檔淨股數乘收盤估算，上市約 -47.85 億（官方更正版）或 -42.52 億（app store），全市場約 -55 億。
  - 9/22 app 顯示 +15,949.609 張，上市投信實際為 -3.66 億。
- 外資：這兩天畫面顯示的總數與金額同方向。9/23 為 +123,023 張，上市金額 +373.13 億；9/22 為 +1,058,563 張，上市金額 +453.21 億。不過 9/23 的總數由 ETF 的 +353,736 張主導，股票部分是 -230,289 張，換算金額卻是 +153.76 億。
- 上櫃 sect=AL 含權證（4,769 列），而上市 ALLBUT0999 不含，所以兩市涵蓋範圍不一致。但權證主要影響自營商（9/23 +11,947,000 股，占 4.48%）與三大法人合計，這兩個全市場欄位目前沒有前端顯示，也沒有送進 AI payload。對畫面上的外資影響約 0.35%，對投信沒有影響。
- 另外，store 的投信逐檔數據與交易所事後的更正版不一致（9/23 上市 20 列、上櫃 7 列），應另案處理。

建議：改以金額呈現（BFI82U，或以逐檔淨股數乘均價、收盤估算並註明），股票與 ETF 分開顯示，並在彙總前排除權證。
    - 理由：finding 的核心重現成立，所有數值都重現了；但需要四點修正，所以判 partially_confirmed。

一、確認的部分（本機重算，並與線上金額比對）
1. 程式碼：market_intelligence.py:503-507 直接加總 store 當日所有列的淨股數，不分標的也不過濾。Dashboard.tsx:289 與 TaiwanScreener.tsx:611、624 把它除以 1000 後標成「張」。投信大於 0 時顯示 text-red-400（TaiwanScreener.tsx:623）。
2. 投信方向相反，9/23 與 9/22 兩天都重現：
   - 9/23 app 投信合計為 +20,232,552 股（上市 +24,292,839，上櫃 -4,060,287）。
   - HiStock 近三十日上市三大法人表，9/23 投信為 -47.59 億。
   - 我用官方 T86（已更正版）逐檔淨股數乘官方收盤，上市投信約 -47.85 億，與 finding 相同，也與 HiStock 的 -47.59 億只差約 0.5%。改用 app store 的資料則是 -42.52 億。
   - 全市場（上市加上櫃）估算為 -54.75 億（store）或 -54.95 億（官方更正版）。
   - 9/22：app 投信 +15,949,609 股，HiStock 上市投信為 -3.66 億，store 乘收盤的全市場估算約 -22.9 億，方向同樣相反。
3. ETF 單位數主導外資數字（9/23）：
   - 全市場外資 +123,023,127 股，其中 ETF +353,736,359 股，股票 -230,288,674 股。
   - 上市股票為 -161,729,329 股，加上 27 列不在主檔的標的後為 -161,698,329 股，與 finding 相同；以收盤估算為 +333.67 億。
   - 上市 ETF +314,411,657 股，約 +37.43 億，與 finding 相同。
4. 權證混入：
   - 9/23 store 有 6,999 列，其中 4,800 列不在證券主檔，貢獻自營商 +11,947,000 股，占自營合計 266,465,131 股的 4.48%。
   - 9/22 同類列的自營商合計為 -20,789,000 股，與 finding 相同。
   - 上櫃不在主檔的 4,773 列中，有 4,769 列的名稱含「購／售」，是權證。上市不在主檔的 27 列是 ETN（02xxxx）、REIT（01xxxT）與特別股，沒有權證，符合 ALLBUT0999 排除權證的行為。

二、需要修正的部分
(a) 畫面不會顯示「+20,233 張」。前端用 (x/1000).toLocaleString()，實際顯示「+20,232.552 張」，張數帶小數（node 實測）。
(b) 權證只影響 dealer_net 與 total_net，但這兩個全市場欄位沒有任何前端元件顯示，也沒有送進 AI payload：
   - research_context.py:639 雖把 total_net 存成 institutional_market_net，但 ai_research.py:146-150 的 market_context 沒有這個欄位。
   - 前端只在 api.ts:1225 有型別定義，沒有顯示它的元件。
   - 對畫面上的欄位，權證的影響很小：9/23 外資 -424,558 股，約占 0.35%；投信 0 股。
   因此「上櫃含權證、上市與上櫃涵蓋範圍不一致」在畫面上幾乎沒有可見影響。
(c) 外資在畫面上的總數，9/22 與 9/23 兩天方向都與金額相同：
   - 9/23：+123,023 張，HiStock 上市外資 +373.13 億。
   - 9/22：+1,058,563 張，HiStock 上市外資 +453.21 億。
   finding 舉的外資例子（-1.6 億股卻是 +333.7 億）只是上市股票這個子集，不是畫面顯示的數字。已實際看到方向相反的是投信。
(d) finding 說 -47.85 億是用 store 算的，但這個數字其實出自官方參考資料的 T86 更正版；用 app 自己的 store 算是 -42.52 億。兩者方向相同，結論不變。

三、額外發現（原 finding 沒有提到，建議另開一條）
store 的投信資料與官方參考資料（9/24 01:49 下載）不一致：
- 9/23：上市 20 列不符、上櫃 7 列不符，淨差約 +7.49M 股。例如 2330 在 store 為 -973,707 股，官方為 -1,142,808 股；3264.TPEX 在 store 為 -1,577,000 股，官方為 -6,963,000 股。
- 9/22：上市 20 列、上櫃 5 列不符，另有上市、上櫃外資各 1 列不符。
推測：交易所事後更正了投信數據，但 store 沒有重抓已存的日期。

四、嚴重度
維持 P2。這是摘要卡片，口徑選錯，會誤導盤勢判讀，但不影響任何自動交易或告警（監控規則 0 條），也沒有送進 AI 研究的 payload。選股頁的卡片也標了「股數/1000」。
    - 證據：
      - backend/app/taiwan/market_intelligence.py:503-507（對 inst_df 的 foreign_net／investment_trust_net／dealer_net 直接 sum，無標的過濾）
      - backend/app/taiwan/enrichment/institutional.py:47（T86 selectType=ALLBUT0999）、:152（TPEx sect=AL）
      - frontend/src/pages/Dashboard.tsx:289（外資 foreign_net/1000 toLocaleString + ' 張'）；TaiwanScreener.tsx:611、:623-624（投信 >0 為 text-red-400，/1000 toLocaleString 張），:616/:629 標註「全市場總計 (股數/1000)」
      - node -e "(20232552/1000).toLocaleString('zh-TW')" → 20,232.552（畫面實為 +20,232.552 張，非 +20,233 張）
      - work/verify-instnet/out.txt（repro.py）：9/23 store 6,999 列，app 合計 外資 123,023,127／投信 20,232,552／自營 266,465,131 股；不在主檔 4,800 列，自營 +11,947,000（4.48%）；9/22 store 7,478 列，不在主檔自營 -20,789,000
      - work/verify-instnet/out.txt：9/23 上市外資 股票 -161,729,329 股 → +333.67 億；ETF +314,411,657 股 → +37.43 億；上市投信（store）-42.52 億；全市場投信（store×收盤）-54.75 億
      - work/verify-instnet/value2_out.txt（value2.py，官方 T86／TPEx 更正版×官方收盤）：上市 9/23 外資 +371.13 億、投信 -47.85 億（21,397,398 股）、自營 +58.52 億；全市場外資 +198.64 億（+123,023,127 股）、投信 -54.95 億；上櫃非主檔 4,773 列中 4,769 列名稱含購/售
      - https://histock.tw/stock/three.aspx（WebFetch，近三十日上市三大法人買賣超，單位億）：2026/09/23 外資 373.13、投信 -47.59、自營 58.25、總計 383.79；2026/09/22 外資 453.21、投信 -3.66、自營 158.54、總計 608.09
      - work/verify-instnet/day22_out.txt：9/22 上市投信 store +11,836,489 股（×收盤 +0.53 億）、官方更正版 16,543,539 股（-5.42 億）；上櫃投信 +4,113,120 股（-23.43 億）；app 顯示 +15,949,609 股（紅色）
      - work/verify-instnet/diff_out.txt：store 與官方參考資料的投信欄位，9/23 上市 20 列、上櫃 7 列不符（例 2330 -973,707 對 -1,142,808；3264.TPEX -1,577,000 對 -6,963,000），9/22 上市 20 列、上櫃 5 列不符；外資與自營 9/23 全數相符
      - backend/app/taiwan/ai_research.py:146-150（market_context payload 不含 institutional_market_net）；research_context.py:639 只存欄位；前端僅 frontend/src/lib/api.ts:1225 有型別定義，沒有顯示 dealer_net／total_net 全市場彙總的元件
      - 腳本位置：AUDIT_SCRATCH/work/verify-instnet/（repro.py、diff.py、value2.py、day22.py 與對應 *_out.txt）

### [P2 → P2] 16:30 抓取的成交量值與 T86 投信數字不是定版，之後也不再更新（部分成立）

- **主張**：每日排程在 16:30 寫入日K與法人資料，但鉅額交易持續到 17:00，官方 T86 投信欄位之後也會變動；之後的排程看到分區已存在就跳過，所以成交量、成交值與投信買賣超永久停留在未定版。
- **正確的定義或數值**：成交量、成交值與法人資料應在官方定版後（例如 17:30 之後）再重抓一次，或在晚間排程覆寫當日分區
- **app 實際行為**：保留盤後初值；量比、成交額、投信買賣超、法人類異常訊號都以初值計算
- **對使用者的影響**：大型權值股的成交值與量比偏低；投信買賣超可能方向相反（3105 被記為賣超，實際是買超 795 張）
- **修法建議**：晚間排程（21:40）以 force 模式重抓當日的日K與 T86／TPEx 法人資料，或以官方回應是否變動決定是否覆寫；投信差異的原因需向官方確認（推測是鉅額交易或事後更正）
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/daily_update.py:282-286（已有分區就不重抓）
  - backend/app/taiwan/market_intelligence.py:246（turnover＝store amount 加總）
  - backend/app/taiwan/screener.py:313（vol_ratio_5d 使用 store volume）
- **線上證據**：
  - https://shl.twse.com.tw/page/trading/7.html（證交所：鉅額交易「配對交易 08:00-08:30及09：00-17：00」「逐筆交易 09：00-17：00」）
- **本地計算證據**：
  - 分區修改時間：daily/date=2026-09-23 為 16:30:05，institutional/date=2026-09-23 為 16:30:09；backend.log:45477 21:40 的排程沒有重抓
  - 9/23 上市有 23 檔成交量值低於官方 MI_INDEX（例如 2330 少 839,000 股、20.9 億），股票加 ETF 成交值 app 8,872.5 億、官方 8,911.8 億（少 39.3 億）
  - STOCK_DAY 比對：16 筆不一致全部落在 9/16～9/23（每天 16:30 抓取的日期），8 月到 9 月上旬 0 筆。2330 在 9/18 少 3,702,177 股、91.0 億
  - T86：9/23 有 17 檔上市、7 檔上櫃的投信買賣超與官方不同（例如 2330 app -973,707 股、官方 -1,142,808 股；3105 app -7,000 股、官方 +795,000 股）。全市場投信 app +20,232,552 股，官方 +12,743,511 股（高 59%）；外資與自營商合計則完全相同
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：日 K（MI_INDEX／dailyQuotes）與三大法人（T86／TPEx）都在盤後當天抓取，實際時間是 16:19～17:03。這時官方數字還不是最終值：成交量與成交值還沒納入全部鉅額交易，投信買賣超之後也會被官方重新歸類。9/23 上市 15 檔投信差異出現時，該檔總成交量根本沒變，所以不能歸因於鉅額交易；9/22 連外資也有差異。

排程兩班都沒有傳 force，daily_update.py:282-286、:324-328 看到分區已存在就跳過，所以這些未定版的數值會永久保留。實測結果：
- 當天抓的日期全部偏低，官方數字只會比 app 大或相等。
- 隔天才抓的日期全部與官方一致。
- 9/18 在 17:03 抓的仍然不完整。

因此修法不能只把時間改到「17:30 後」，應該在較晚的時點（例如 21:40，或隔天早上）以 force 重抓前一個交易日的日 K 與法人資料，並比對是否有變動。

同一個根因還延伸出更嚴重的面向：TPEx 抓取失敗會被 snapshot_provider.py:117-118 吞掉，於是 9/16、9/23 的分區只有上市資料，整個上櫃市場當天的日 K 也因為分區已存在而永久缺漏。建議另立一條 finding 處理。
    - 理由：主張的核心成立，嚴重度 P2 合理，在使用者的環境裡確實已經發生。

一、程式碼
- 排程沒有傳 force。兩個班次都只呼叫 run_update(refresh_daily=True, refresh_institutional=True, ...)：16:30 班在 backend/app/jobs/daily_pipeline.py:177-192，21:40 班在 :194-210。
- 21:40 班的註解寫明「已落盤的日期會被 refresh 略過」。
- daily_update.py:282-286 只算 d_max 之後缺的日期，當日分區存在就記為 "Daily store already current"；法人在 :324-328，行為相同。
- daily_refresh.py:150、:166-169 與 institutional_margin_refresh.py:142、:173-176 也都把 available 裡的日期直接 skip。
- 因此當日分區一旦寫過，就不會再被覆寫。

二、官方定義
- 這部分用的是與 finding 不同的權威來源：參考資料裡的證交所原始 JSON notes。
- MI_INDEX 與 STOCK_DAY 註明「本統計資訊含一般、零股、盤後定價、鉅額交易」。
- T86 註明統計含鉅額，而且「以當日原始成交情形統計，不以證券商申報錯帳、更正帳號等調整後資料統計」。
- 所以 app 選的口徑本身正確，錯在抓取時間點：抓到的不是最終值。

三、實測（自己重算，數字與 finding 一致）
- 9/23 上市 1330 檔中，23 檔成交量低於官方，沒有任何一檔高於官方。成交值合計少 3,926,807,712 元。
- STOCK_DAY 對 10 檔 380 筆：16 筆不一致，全部落在當天就抓的日期。
- 隔天才抓的日期完全一致：9/21 是 9/22 10:55 抓的，8 月到 9/15 是 9/16 11:21 抓的。
- T86／TPEx 兩天共 45 筆投信不一致，官方的買、賣股數一律大於或等於 app。
- 其中 3 筆方向相反：9/22 的 8046、6488，以及 9/23 的 3105（app -7,000，官方 +795,000）。
- 9/23 全市場投信：app +20,232,552，官方 +12,743,511。
- backend.log:45469-45477 顯示 21:40 那班完全沒有請求 MI_INDEX 或 T86。

四、finding 需要修正的細節
- 「每天 16:30 抓取」不精確。9/17 是 16:19 抓的，9/18 是 17:03 抓的（backend.log.1:48884-48886）。9/18 在 17:00 之後抓，2330 仍少 3,702,177 股，所以「17:00 後就定版、17:30 重抓即可」沒有證據支持。已確認的只有「隔天抓的一定是最終值」。
- 投信差異大多不是鉅額交易造成。9/23 上市 17 檔投信不一致中，有 15 檔的總成交量與官方一致（成交量缺口為 0），例如 3481 投信賣出多 3,725,000 股、3605 投信買進多 1,035,000 股，但這兩檔成交量都沒變。9/22 的 2454 投信買進多 157,000 股，總量只多 68,000 股。這表示官方在事後把既有成交重新歸類給投信，機制不明（推測）。官方 note 排除了「錯帳／更正帳號」調整，所以 finding 推測的「事後更正」也不太成立。
- 「外資、自營商完全相同」只在 9/23 成立。9/22 外資也有差異：3189 差 665,000 股，TPEx 3264 app 1,300,618、官方 17,273,026。

五、使用者情境下的影響
- 自選 2454、3034、2379、6196、3545 都有被低估的日期。
- vol_ratio_5d 最大誤差：6196 在 9/17 為 app 0.629、官方 0.739（-14.8%），2330 在 9/18 為 -5.5%。
- 監控規則 0 條，不會造成交易結果錯誤，所以維持 P2。

六、同一個根因的新面向（原 finding 沒提到）
- 9/23 16:30 抓 TPEx dailyQuotes 時失敗（backend.log:37258「incomplete chunked read」）。
- snapshot_provider.py:117-118 把這個失敗吞掉，只寫入 TWSE 1330 列，daily 狀態卻仍是 success。
- 21:40 那班看到分區已存在就跳過，所以 9/23（以及 9/16）整個上櫃市場的日 K 永久缺漏。
- 使用者自選都是上市股，不直接受影響；但上櫃的市場統計與選股會受影響。建議另立一條 finding，嚴重度可能到 P1。

七、線上查證的限制
- 本 session 的 WebSearch 額度已用完。zh／en Wikipedia 只確認了「鉅額交易另訂交易時間」與 block trade 的定義，沒有找到不在 twse.com.tw 網域、又能獨立證實 17:00 這個時段的來源；Investopedia 抓取失敗。
- 17:00 的時段仍只有 finding 自己引用的來源；不過本結論不依賴這個時段。
    - 證據：
      - backend/app/jobs/daily_pipeline.py:177-210：16:30 與 21:40 兩班都呼叫 run_update，但沒有 force；註解寫著「已落盤的日期會被 refresh 略過」
      - backend/app/taiwan/daily_update.py:282-286（日 K）、:324-328（法人）：當日分區存在就不再抓；daily_refresh.py:150,166-169 與 institutional_margin_refresh.py:142,173-176 在 cur in available 時直接 skip
      - backend/app/taiwan/market_intelligence.py:246 turnover=sum(amount)；screener.py:313 vol_ratio_5d=volume/rolling_mean(5)；abnormal_diagnostics.py:18 TRUST_FLOW_SPIKE 使用 trust_net。三者都直接吃 store 裡的值
      - 官方定義（參考資料 raw JSON 的 notes，URL 見 manifest）：MI_INDEX（https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&date=20260923&type=ALLBUT0999）與 STOCK_DAY 註明「本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購」；T86（https://www.twse.com.tw/rwd/zh/fund/T86?response=json&selectType=ALLBUT0999&date=20260923）註明「本資訊以當日原始成交情形統計，不以證券商申報錯帳、更正帳號等調整後資料統計」
      - https://zh.wikipedia.org/wiki/臺灣證券交易所 原文：「盤後定價、零股及鉅額交易等，則另訂交易時間、委託及撮合方式」（只能證明鉅額交易另有時段，沒有寫到具體到幾點）
      - 本地計算（uv run python scratchpad/audit/work/verify_finalize/cmp.py、cmp2.py）：9/23 上市 23 檔成交量不一致，app > 官方 0 檔；2330 少 839,000 股／2,091,500,000 元；成交值合計 app 887,250,849,184、官方 891,177,656,896，差 3,926,807,712
      - cmp2.py：STOCK_DAY 380 筆有 16 筆不一致，日期是 9/16（分區 16:30:22 寫入）、9/17（16:19:15）、9/18（17:03:37）、9/22（16:30:03）、9/23（16:30:05）；9/21（9/22 10:55 寫入）與 9/15 以前（9/16 11:21 寫入）全部一致
      - backend.log.1:48884-48886：9/18 的 16:30 班因為 misfire 延到 17:03:32 才抓 MI_INDEX，但 2330 仍少 3,702,177 股／9,102,800,000 元，可見 17:00 之後也還不是最終值
      - cmp3.py／cmp4.py：投信兩天不一致共 45 筆（9/22 上市 16、上櫃 5；9/23 上市 17、上櫃 7），官方買賣股數 < app 的有 0 筆；方向相反 3 筆：9/22 8046 app +4,818／官方 -134,182，9/22 6488 +5,811／-14,189，9/23 3105 -7,000／+795,000；9/23 全市場投信 app 20,232,552、官方 12,743,511
      - cmp4.py：9/23 上市投信不一致 17 檔中有 15 檔成交量缺口為 0（例如 3481 sell_gap 3,725,000、vol_gap 0；3605 buy_gap 1,035,000、vol_gap 0），表示投信數字是事後重新歸類，不是晚到的鉅額成交
      - cmp3.py：9/22 外資也有差異：3189 app 8,471,578／官方 9,136,578；TPEx 3264 app 1,300,618／官方 17,273,026
      - backend.log:45469-45477：9/23 21:40 那班只抓 t187ap03_L／mopsfin_t187ap03_O（股本），沒有 MI_INDEX 或 T86 請求，結果為 daily=success、inst=success
      - 自選影響（cmp4.py）：vol_ratio_5d 的 app／官方值：6196 9/17 為 0.629／0.739（-14.8%）、2330 9/18 為 1.656／1.753（-5.5%）、2454 9/16 為 -3.0%；2454 9/22 投信 app +216,171、官方 +373,171
      - 新面向：backend.log:37258「TPEx snapshot fetch failed for 2026-09-23: peer closed connection ... incomplete chunked read」；snapshot_provider.py:117-118 吞掉例外後只寫入 TWSE；daily 分區 9/16、9/23 的 TPEX 列數為 0（9/15、9/17、9/22 約 1006-1009 列），之後的排程看到分區已存在就跳過，無法補齊
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：每日排程抓取當天的日K（MI_INDEX）與法人資料（T86、TPEx dailyTrade）時，官方資料還沒定版。之後的排程（包括 21:40）因為沒有帶 force，看到分區已存在就跳過（daily_update.py:282-286、:324-328；daily_pipeline.py:149-153），所以初值會永久留下。

以官方隔日版本重算的結果：9/23 上市有 23 檔的成交量值偏低（2330 少 839,000 股、約 20.9 億）；在對得上的 1,330 檔中，成交值合計少 39.27 億。STOCK_DAY 抽樣 10 檔，有 16 筆不一致，全部落在當天抓取的 9/16、9/17、9/18、9/22、9/23；隔日才抓的 9/21 與 8/03～9/15 都是 0 筆。2330 在 9/18 少 3,702,177 股、91.0 億；自選股 6196 在 9/17 少 17%。

當天抓取的時間不全是 16:30：9/17 為 16:19，9/18 為 17:03。17:03 抓到的資料仍未定版，所以「17:30 之後」這個門檻沒有證據；目前只證實隔日抓取的資料已定版。

法人方面：9/23 上市投信 17 檔、上櫃 7 檔與官方不同，全市場投信 app +20,232,552 股，官方 +12,743,511 股；外資與自營商當天相同。9/22 投信有 21 檔不同，外資也有 2 檔不同（上櫃 3264 的外資 app 1,300,618 股，官方 17,273,026 股）。所以會變動的不只投信欄位。app 的數值一律比官方少一部分買進或賣出，推測是鉅額交易較晚才併入官方統計。官方 notes 寫明統計含鉅額交易，且不採用更正帳後的資料。

同一個跳過機制還會把抓取失敗留下的殘缺分區永久保留：9/23 16:30 上櫃抓取失敗，當日日K分區只有上市資料，21:40 沒有補抓；9/16 的分區同樣缺上櫃。

修法：晚間班次改用 force 重抓當日的日K與法人資料，或檢查當日分區的完整性，例如上市、上櫃兩邊都要有資料，缺一邊就重抓。
    - 理由：我自己寫了腳本，用本地 parquet 對照官方參考資料（凌晨 01:49～01:57 抓取，屬於定版後的資料）重新計算。finding 列出的數字全部重現，連小數都一致；程式碼路徑也成立。但有三處敘述要修正或補充：

(1) 不是每天都在 16:30 抓取。9/17 的日K分區在 16:19:15 寫入。9/18 的排程延遲到 17:03:32 才執行，也就是鉅額交易 17:00 收市之後，資料卻仍然沒定版，而且差異最大（2330 少 3,702,177 股）。所以建議的「17:30 之後重抓」門檻沒有證據支持。目前能證實已定版的，只有隔天抓到的資料：9/21 的分區在 9/22 10:55 抓取，8 月到 9/15 的分區在 9/16 回補，這些日期都是 0 筆差異。

(2) 會變動的不只投信。9/23 外資與自營商確實完全相同，但 9/22 外資也有差異：上市 3189 少 665,000 股；上櫃 3264 的外資買賣超 app 記為 1,300,618 股，官方為 17,273,026 股。

(3) 另一個新面向（finding 沒提）：同一套「分區已存在就跳過」的邏輯，也把只寫了一半的分區永久留下來。9/23 16:30 抓上櫃時失敗（incomplete chunked read），日K分區只有 1,330 筆上市資料；21:40 的排程沒有補抓，9/23 的上櫃日K至今全缺。9/16 的分區同樣只有上市資料。

原因推測是鉅額交易較晚才併入統計，證據有三點，但我沒辦法直接證實：官方 JSON 的 notes 寫明 MI_INDEX、STOCK_DAY、T86「含一般、零股、盤後定價、鉅額交易」；T86 另註明「以當日原始成交情形統計，不以證券商申報錯帳、更正帳號等調整後資料統計」，可排除更正帳的可能；所有差異都是 app 比官方少（量值與法人買、賣股數都是），收盤價 0 筆差異。

finding 引用的線上證據 shl.twse.com.tw 屬於禁止連線的網域，我沒有連。WebSearch 的額度也已經用完，維基百科只寫了「另訂交易時間」，沒有寫出時段。因此「鉅額交易到 17:00」這一點我沒有獨立查證。

嚴重度維持 P2：這是資料沒定版就被鎖住，不是口徑定義錯誤。但若把上櫃整天缺漏一併計入，影響比原 finding 描述的大。
    - 證據：
      - 程式碼：backend/app/taiwan/daily_update.py:282-286（非 force 模式下，resolve_missing_date_range(d_max, target) 回傳 None，就設成 already current）；:324-328 法人資料邏輯相同；:136-137 若 earliest_available >= target_latest 則回傳 None。backend/app/jobs/daily_pipeline.py:149-153 排程呼叫 run_update 時沒有帶 force；:194-205 的 21:40 班次在註解中寫明「已落盤的日期會被 refresh 略過」。backend/app/taiwan/daily_refresh.py:150,167-170：分區已存在的日期直接略過。
      - 程式碼：backend/app/taiwan/market_intelligence.py:246 turnover = df['amount'].sum()；backend/app/taiwan/screener.py:313 vol_ratio_5d = volume / rolling_mean(5)。兩處都與 finding 所述相符。
      - 分區修改時間（ls --time-style=full-iso）：daily 9/16 16:30:22、9/17 16:19:15、9/18 17:03:37、9/21 為 9/22 10:55:40、9/22 16:30:03、9/23 16:30:05；institutional 9/22 16:30:07、9/23 16:30:09。8/27～9/15 的日K分區都在 9/16 11:21 寫入（隔日以後才抓）。
      - data/backend.log:37256-37258：9/23 16:30:02 抓 MI_INDEX；16:30:05 出現「TPEx snapshot fetch failed for 2026-09-23: peer closed connection ... (incomplete chunked read)」。:45470-45478：21:40 的班次只抓了 t187ap03，沒有抓 MI_INDEX、dailyQuotes、T86，最後回報 daily=success。backend.log.1:48885-48887：9/18 的 16:30 排程延到 17:03:32 才執行，MI_INDEX 在 17:03:32 抓取。
      - 重算 A（cmp_daily.py，9/23 本地日K對照 twse_mi_index_20260923_quotes.csv）：23 檔量值偏低，偏高 0 檔，收盤價差異 0 筆。2330 本地 21,978,873 股／官方 22,817,873 股（-839,000 股），成交值差 -2,091,500,000 元左右，即 20.9 億（finding：少 839,000 股、20.9 億，一致）。在 1,330 檔都對得上的標的中，本地成交值合計 887,250,849,184 元，官方 891,177,656,896 元，差 -3,926,807,712 元（finding：8,872.5 億／8,911.8 億／少 39.3 億，一致；但這個合計含 TDR、創新板等，不只是股票加 ETF）。
      - 重算 B（cmp_daily.py，STOCK_DAY 10 檔 × 38 個交易日，共 380 筆）：不一致 16 筆，只出現在 9/16（2 筆）、9/17（4 筆）、9/18（6 筆）、9/22（2 筆）、9/23（2 筆）；8/03～9/15 與 9/21 都是 0 筆。2330 在 9/18 本地 37,190,511 股、官方 40,892,688 股（-3,702,177 股），成交值差 -9.1028e9 元（finding：少 3,702,177 股、91.0 億，一致）。自選股受影響：6196 在 9/17 為 808,428 股、官方 973,428 股（-17.0%）；2454 在 9/18 少 434,469 股（-4.2%）；3034、2379、3545 在 9/17～9/18 也有差異；0050、00878、00885、00893 為 0 筆差異。
      - 重算 C（cmp_inst.py，對照 T86 與 TPEx dailyTrade）：9/23 上市投信 17 筆不一致、上櫃 7 筆（與 finding 一致）。2330 app -973,707、官方 -1,142,808；3105 app -7,000、官方 +795,000（買進 1,000 股對 803,000 股）。全市場投信 app 24,292,839 + (-4,060,287) = 20,232,552，官方 21,397,398 + (-8,653,887) = 12,743,511，比值 1.588（高 59%，一致）。9/23 外資與自營商 0 筆差異。所有差異都是 app 的投信買進或賣出股數比官方少，沒有任何一筆 app 較多。
      - 重算 D（cmp_inst.py 與 cmp_foreign922.py，9/22）：上市投信 16 筆、上櫃 5 筆不一致，方向不同的有 7 筆（例如 8028 app 0、官方 +3,007,000；8046 app +4,818、官方 -134,182）。自選股 2454 app +216,171、官方 +373,171。外資也有差異：上市 3189 app 淨買 8,471,578、官方 9,136,578；上櫃 3264 app 1,300,618、官方 17,273,026。自營商 0 筆差異。
      - 重算 E（cmp_tpex_stock.py，並讀本地分區）：daily/date=2026-09-23 只有 1,330 筆 .TWSE，date=2026-09-16 也只有 1,330 筆 .TWSE，其他日期都有 1,006～1,009 筆 .TPEX。TPEx 參考資料中的 6488、8069 在 9/16 與 9/23 都找不到本地資料。
      - 官方 notes（reference/raw/twse_mi_index_20260923.json 與 twse_stock_day_2330_202609.json）：「本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。」twse_t86_20260923.json：「本統計資訊含一般、零股、盤後定價、鉅額，不含拍賣、標購。」「本資訊以當日原始成交情形統計，不以證券商申報錯帳、更正帳號等調整後資料統計。」
      - 線上：finding 引用的 https://shl.twse.com.tw/page/trading/7.html 屬於禁止連線的 twse.com.tw 子網域，我沒有連。WebSearch 顯示本 session 額度已用完（200/200）。https://zh.wikipedia.org/wiki/臺灣證券交易所 只寫了「盤後定價、零股及鉅額交易等，則另訂交易時間、委託及撮合方式。」沒有寫出具體時段，所以「鉅額交易到 17:00」我沒有獨立查證。
      - 腳本與輸出：AUDIT_SCRATCH/work/verify-finalize/cmp_daily.py（輸出 cmp_daily.out）、cmp_inst.py（輸出 cmp_inst.out）、cmp_foreign922.py、cmp_tpex_stock.py。

### [P2 → P2] 選股頁的 RSI14 與量比和 app 其他頁定義不同，也不符合台灣慣例（部分成立）

- **主張**：taiwan/screener.py 的 RSI14 用 14 日簡單平均（Cutler 式），量比分母為包含當日的 5 日均量；enriched pipeline 則用 Wilder 平滑（alpha=1/n），量比分母為不含當日的前 5 日均量。同一檔股票在不同頁面顯示不同數值。
- **正確的定義或數值**：全站只用一種 RSI（台灣慣例為 Wilder）和一種量比定義（不含當日的前 5 日均量）
- **app 實際行為**：選股頁用 SMA-RSI 與含當日的量比，個股與自選頁用 enriched 的 Wilder-RSI 與前 5 日量比
- **對使用者的影響**：RSI≥70 超買、≤30 超賣的判讀在不同頁面互相矛盾；以 RSI 或量比篩選的結果與個股頁數值對不上
- **修法建議**：選股改為直接讀取 enriched 的 rsi_14 與 vol_ratio_5d，或共用 pipeline 的同一個函式
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/screener.py:316-322（rolling_mean(14)）
  - backend/app/taiwan/screener.py:313（volume / rolling_mean(5) 含當日）
  - backend/app/indicators/pipeline.py:529-547（ewm_mean alpha=1/n）
  - backend/app/indicators/pipeline.py:434-436、486-490（volume.shift(1).rolling_mean(5)）
  - frontend/src/pages/TaiwanScreener.tsx:1722（≥70 標紅）
- **線上證據**：
  - https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=1342fb37-760e-48b0-9f27-65674f6344c9（MoneyDJ：「UP t = UP t-1 + 1 / N ( Ut – UP t-1)」，即威爾德平滑）
  - https://wiki.mbalib.com/zh-tw/%E9%87%8F%E6%AF%94（量比：過去 5 個交易日平均，不包括今天）
- **本地計算證據**：
  - 120 筆比對（10 檔 × 12 天）：選股 rsi_14 與獨立計算的 SMA-RSI 差 0；與 Wilder RSI 最大差 23.40（00885 9/8：74.52 對 51.12）；與 enriched rsi_14 最大差 21.27
  - 9/23：00878 選股 77.45（標紅超買）對 enriched 69.26；00893 83.33 對 70.06；3034 57.76 對 51.54
  - 量比：00893 在 9/21 選股 2.80、enriched 4.74（work/A12/indicators_check.py）
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：台股選股頁（taiwan/screener.py:313、317-321）的 RSI14 用 14 日簡單平均（Cutler 式），量比分母為含當日的 5 日均量。自選頁（technical_indicators.py 的 Wilder）、K 線 RSI 副圖與 enriched pipeline（pipeline.py:529-549）的 RSI 都是 Wilder 平滑；enriched 與異常診斷的量比用前 5 日（不含當日）。

後果如下：
1. 同一檔股票在選股頁與自選頁的 RSI 不同，超買判讀互相矛盾。例如 9/23 的 0050 是 73.06 對 66.92，00878 是 77.45 對 69.26。全市場 RSI≥70：選股 140 檔，Wilder 49 檔。
2. 量比的不一致主要出現在選股頁內部。「放量股」快速篩選（含當日，53 檔）與「放量」訊號、異常診斷的 5D 量比（前 5 日，98 檔）口徑不同。含當日量比恆小於 5，所以量比 ≥5 的篩選永遠為空。
3. 自選頁台股不顯示量比；個股頁的「5日均量比」（research_context）和選股頁同為含當日口徑，並非 finding 所說的前 5 日。
4. research_context.py:456-467 另有一份 SMA-RSI，送進 AI 研究。

Wilder 是 RSI 的原始與主流定義，但 Cutler 版是公認的變體，所以問題本質是全站口徑不一致，而不是單純算錯。
    - 理由：核心主張成立。台股選股頁的 RSI14 用 14 日簡單平均（Cutler 式），量比分母是含當日的 5 日均量；自選頁、K 線 RSI 副圖和 enriched pipeline 用 Wilder 平滑（alpha=1/14），enriched 量比分母是不含當日的前 5 日均量。本地重算與選股函式的結果完全一致。9/23 使用者自選股裡，0050 在選股頁是 73.06、標紅超買，自選頁是 66.92；00878 是 77.45 對 69.26。兩頁的超買判讀確實互相矛盾。擴大到全市場 1323 檔：選股頁 RSI≥70 有 140 檔，enriched 只有 49 檔。

finding 有三處不精確。
1. 自選頁台股的 RSI 不是讀 enriched，而是 technical_indicators.py 另外算的 Wilder RSI（數值與 enriched 相同）。
2. 自選頁台股根本不顯示量比：watchlist_enrichment.py 沒有產出 vol_ratio_5d，畫面顯示「—」。個股頁嵌入的「5日均量比」來自 research_context，也是含當日口徑，與選股頁相同。所以「個股與自選頁用前 5 日量比」這句不對。
3. 「台灣慣例為 Wilder」只有部分依據。MoneyDJ 與 StockCharts 支持 Wilder 是原始定義，但中文維基稱 Cutler 版「被較多人採用」。Cutler 版是公認的變體，不能直接說是錯的。

量比的不一致實際出現在選股頁內部：「放量股」快速篩選設定 vol_ratio_5d_min=2，走含當日口徑；同一頁的「放量」訊號和異常診斷的 5D 量比都用前 5 日口徑。9/23 前者只篩出 53 檔，後者標了 98 檔。另外，含當日量比在數學上恆小於 5：全市場最大值 3.853，而 enriched 有 16 檔 ≥5。所以量比 ≥5 的篩選永遠為空。

finding 漏掉的新面向：
- research_context.py 有第三份 SMA-RSI 實作，會送進 AI 研究。
- 程式碼本身早就在 technical_indicators.py:18-22 註明這個分歧，但 code-review 文件沒有記錄。

嚴重度維持 P2。RSI 屬於混用兩種合法變體，造成頁面間矛盾與篩選結果不一致，是明確的功能錯誤，但算不上錯誤的金融口徑到 P1 的程度。量比口徑如果要嚴格計較，可以考慮升級，但不影響本判定。
    - 證據：
      - backend/app/taiwan/screener.py:313 `(pl.col("volume") / pl.col("volume").rolling_mean(5).over("symbol"))`：分母含當日
      - backend/app/taiwan/screener.py:317-321：gain／loss 各取 rolling_mean(14)，即 Cutler SMA-RSI
      - backend/app/indicators/pipeline.py:434-436、486-491：`volume.shift(1).rolling_mean(5)`，前 5 日、不含當日；pipeline.py:529-549 用 ewm_mean(alpha=1/n, adjust=False)，即 Wilder；pipeline.py:659 定義 signal_volume_surge 為 vol_ratio_5d>=2.0
      - backend/app/taiwan/technical_indicators.py:18-22：程式碼自己註明 Wilder 與 screener.py 的 SMA-RSI 是 'a pre-existing, separate divergence ... not touched here'；89-102 為自選頁用的 Wilder RSI
      - backend/app/taiwan/watchlist_enrichment.py:246-266：台股自選列只產出 rsi_14（technical_indicators），沒有 vol_ratio_5d；api\watchlist.py:459-464 用 setdefault 補 None，前端 Watchlist.tsx:591 顯示「—」
      - backend/app/taiwan/research_context.py:450-454：vol/mean(volumes[-5:]) 含當日；456-467：第三份 SMA-RSI，經 ai_research.py:200-201 送進 AI 研究；TaiwanStockDetail.tsx:897-900 顯示「5日均量比」
      - backend/app/taiwan/abnormal_diagnostics.py:209-214、378-380：異常診斷的 5D 量比用 strictly < target 的前 5 日；前端 TaiwanScreener.tsx:883、925-926 同一頁顯示「5D量比」
      - frontend/src/pages/TaiwanScreener.tsx:373-378：「放量股」preset 設定 volRatioMin='2'（含當日口徑）；1720-1723：rsi_14 ≥70 標紅、≤30 標綠
      - 本地（直接讀 parquet，把資料餵進 TaiwanScreenerService._compute_batch_indicators 與 compute_taiwan_daily_indicators）：work\V_rsi_vr_defcode\check.py，9/23 輸出：選股 RSI 與獨立 Cutler 完全相同；自選 Wilder＝enriched。0050 選股 73.06／自選 66.92；00878 77.45／69.26；00893 83.33／70.06；2454 74.07／72.15；3034 57.76／51.54
      - 本地 work\V_rsi_vr_defcode\universe.py，9/23 全市場 1323 檔：RSI 最大差 35.95、中位差 4.89；RSI≥70 選股 140 檔、enriched 49 檔（只有選股標超買的 94 檔）；RSI≤30 選股 120、enriched 31；量比≥2 選股 53、enriched 98（signal_volume_surge=98）；選股量比最大 3.853、≥5 共 0 檔，enriched ≥5 共 16 檔
      - 本地 work\V_rsi_vr_defcode\spot.py：00885 9/8 選股 RSI 74.52、enriched 53.25（差 21.27，與 finding 相符）；00893 9/21 選股量比 2.80、enriched 4.74（與 finding 相符）
      - https://en.wikipedia.org/wiki/Relative_strength_index ："A variation called Cutler's RSI is based on a simple moving average of U and D, instead of the exponential average above."；"Cutler's RSI generally comes out slightly different from the normal Wilder RSI"
      - https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi ："Average Gain = [(previous Average Gain) x 13 + current Gain] / 14"（Wilder 平滑為標準定義）
      - https://zh.wikipedia.org/zh-tw/相對強弱指數 ：U、D 平均用 α=1/n 的指數移動平均；另載「卡特勒相對強弱指數……改用簡單移動平均法」，並稱兩者差異不大、卡特勒版被較多人採用（所以「台灣慣例只有 Wilder」證據不足）
      - https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=1342fb37-760e-48b0-9f27-65674f6344c9 ："UP t = UP t-1 + 1 / N ( Ut – UP t-1)"（核實 finding 引文屬實）
      - https://baike.eastmoney.com/item/量比 ：「量比＝現成交總手/〖(過去5個交易日平均每分鐘成交量)×當日累計開市時間(分)〗」（分母為過去 5 日，「不含當日」是依字面推論）；baike.baidu.com 回 403，未取得
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：選股頁的 RSI14 用 14 日簡單平均（Cutler 式，screener.py:316-321），量比分母為含當日的 5 日均量（screener.py:313）。前者用官方收盤價獨立重算，120 筆差 0；後者與官方成交量的差來自本地成交量短缺，不是公式。自選頁台股列（technical_indicators.py:93-94）、預覽對話框日K（kline.py:435 → pipeline.py:529-547），以及策略與回測 matrix（matrix.py:3943-3964）都用 Wilder 平滑，彼此差 <0.0004。因此同一檔在選股頁和自選頁的 RSI 最多差 21.27 點（00885 在 9/8：74.52 對 53.25，不是 finding 的 51.12 與 23.40）；9/23 有 00878 77.45 對 69.26、00893 83.33 對 70.06、3034 57.76 對 51.54。120 筆中有 24 筆的 ≥70 或 ≤30 判定兩頁不一致。個股比較頁（research_context rsi14）也是 SMA 式，與選股一致；個股頁不顯示 RSI。量比方面，純台股部署中沒有任何頁面顯示「不含當日」的量比：自選頁台股列的 vol_ratio_5d 恆為空，個股頁的 5日均量比與選股同樣含當日。量比的分歧在於選股篩選與個股頁顯示用含當日的定義，內建策略進場門檻用前 5 日定義（matrix.py:3852-3860）。例如 00893 在 9/21，選股 2.80 對策略口徑 4.74，所以「量比≥2」這類門檻在兩處會篩出不同標的。台灣 RSI 慣例為 Wilder 有 MoneyDJ 支持；量比的「台灣慣例」未能查證。
    - 理由：核心的數值差異可以完全重現：選股頁的 RSI14 就是 14 日簡單平均 RSI（Cutler 式），自選頁和預覽日K副圖則用 Wilder 平滑。9/23 的三個例子與 00893 在 9/21 的量比都逐位吻合。以下三處不正確，因此判「部分確認」：
(1) 「與 Wilder 最大差 23.40（00885 9/8 為 74.52 對 51.12）」無法重現。用 2024 年起的完整歷史算出的收斂 Wilder 值是 53.25，四種獨立實作彼此差 <0.0004，所以最大差應是 21.27。51.12 推測是只用短期資料暖機、尚未收斂的結果。
(2) finding 對頁面的對應有誤。在純台股部署下，自選頁的台股列由 app/taiwan/technical_indicators.py 計算（也是 Wilder，數值與 pipeline 相同），不是 enriched pipeline。而且台股列根本沒有 vol_ratio_5d，永遠顯示「—」。個股頁（TaiwanStockDetail）不顯示 RSI，它的「5日均量比」來自 research_context，定義是含當日，與選股頁相同。個股比較頁的 rsi14 也是 SMA 式，同樣與選股頁一致。所以 RSI 的實際分歧是：SMA 式（選股頁、個股比較頁、AI 研究脈絡）對 Wilder 式（自選頁、預覽日K副圖、策略與回測 matrix）。至於量比，本部署中沒有任何頁面顯示「不含當日」的前 5 日量比給台股。量比分歧實際出現在兩處之間：一邊是選股篩選與個股頁的顯示，另一邊是內建策略進場條件的 vol_ratio_5d 門檻。監控規則也用前 5 日定義，但使用者目前 0 條規則。「同一檔在不同頁面顯示不同量比」在此部署下不成立。
(3) 「台灣慣例」這一點：RSI 部分有 MoneyDJ 支持（威爾德平滑）。量比所引的 MBA 智庫是大陸站，內容描述的是盤中「每分鐘平均量」的量比，原文只寫「過去5個交易日」，沒有明說不含當日。我無法確認台灣是否有量比慣例（WebSearch 額度已用完），這部分只能標為推測。
嚴重度維持 P2：選股頁以 ≥70 標紅，120 筆中有 24 筆的超買判定與自選頁不一致；自選股 00878 在 9/23 選股頁顯示 77.5 並標紅，自選頁顯示 69.3 且未標色。
    - 證據：
      - 重現腳本：AUDIT_SCRATCH/work/verify-scr-rsivr/repro.py；輸出在同目錄的 repro_out.txt，逐列結果在 repro_rows.csv。執行方式：cd backend && uv run python repro.py，exit=0。腳本直接呼叫實際程式碼，取 10 檔 × 9/8～9/23 共 12 個交易日，合計 120 筆：TaiwanScreenerService._compute_batch_indicators（以假的 daily_store 限定可用分區 ≤D）、compute_taiwan_daily_indicators（取 ≤D 的最近 150 個分區）、compute_enriched（依 kline 預覽路徑，從 2025-10-25 起暖機）
      - 選股 RSI 對官方 SMA-RSI：用證交所 STOCK_DAY 官方收盤價（reference csv）獨立計算 14 日簡單平均 RSI，120 筆最大差 0.0，證實 backend/app/taiwan/screener.py:316-321 的 rolling_mean(14) 是 Cutler 式
      - Wilder 四種實作彼此的差距：自選頁 technical_indicators、kline 路徑 compute_enriched、全歷史 compute_enriched，以及我自寫的經典 Wilder（先以前 14 日 SMA 作種子，再做遞迴平滑），最大差 0.00033
      - finding 數字對照（finding 對 本次重現）：00885 在 9/8 選股 74.52 對 74.519；Wilder 51.12 對 53.253（不符）；最大差 23.40 對 21.27（不符）。00878 在 9/23：77.45／69.26 對 77.455／69.256（相符）。00893 在 9/23：83.33／70.06 對 83.333／70.059（相符）。3034 在 9/23：57.76／51.54 對 57.759／51.540（相符）。00893 在 9/21 的量比：2.80／4.74 對 2.797／4.742，且與用官方成交量兩種定義算出的 2.797／4.742 完全相同
      - 超買判定分歧：選股 RSI≥70 有 29 筆，自選頁 Wilder≥70 只有 5 筆；兩頁 ≥70 或 ≤30 判定不一致的有 24／120 筆，例如 2454 在 9/8 為 75.07 對 68.71、0050 在 9/23 為 73.06 對 66.92
      - 自選頁台股列的 RSI 來源：backend/app/taiwan/watchlist_enrichment.py:262 取自 backend/app/taiwan/technical_indicators.py:93-94（ewm_mean alpha=1/14, adjust=False）。該檔的 module docstring 自己也寫明與 screener.py 的 SMA RSI 不同，這是既有分歧
      - 自選頁台股列沒有量比：watchlist_enrichment.py:240-265 組列時沒有 vol_ratio_5d。backend/app/api/watchlist.py 的合併段以 setdefault(None) 補空，前端 Watchlist.tsx:591 因此顯示「—」
      - 個股頁：frontend/src/pages/TaiwanStockDetail.tsx:157-167 傳給 K 線的只有 OHLCV，沒有 RSI；同檔 :900 顯示的 5日均量比來自 backend/app/taiwan/research_context.py:450-454，算法是 volumes[-5:] 平均，含當日，與選股同定義
      - 個股比較頁：frontend/src/pages/TaiwanStockCompare.tsx:363 顯示 research_context 的 rsi14，其算法在 research_context.py:456-467，是 14 日簡單平均，與選股一致
      - 預覽對話框日K：backend/app/api/kline.py:435 對台股本地歷史呼叫 compute_enriched，使用 Wilder RSI（pipeline.py:529-547）；前端 EChartsCandlestick.tsx:249 畫 RSI14。策略與回測的 vol_ratio_5d 則採前 5 日（不含當日）定義，見 backend/app/backtest/matrix.py:3852-3860；RSI 用 Wilder，見 matrix.py:3943-3964
      - 線上來源：MoneyDJ https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=1342fb37-760e-48b0-9f27-65674f6344c9 原文「UP t = UP t-1 + 1 / N ( Ut – UP t-1)」與「威爾德平滑法 ( Wilder's Smoothing)」，第一筆為簡單平均。MBA 智庫 https://wiki.mbalib.com/zh-tw/量比 原文「量比＝現成交總手/〖(過去5個交易日平均每分鐘成交量)×當日累計開市時間(分)〗」，沒有明說不含當日，也沒有提到台灣
      - 附帶觀察（不在本 finding 範圍，推測尚未被記錄）：本地 parquet 在 9/16～9/23 有 16 筆成交量低於證交所官方值，例如 2330 在 9/18 本地 37,190,511 對官方 40,892,688；收盤價 380 筆全部相符。這使選股量比與官方重算最多差 0.109
      - 唯讀確認：執行前後 git status --porcelain 相同；data\ 目錄沒有比執行前 marker 更新的檔案

### [P3 → P2] 選股降序排序把空值排在最前，5 日法人欄位永遠為空卻可以排序（部分成立）

- **主張**：_apply_sort 用 polars sort 的預設 nulls_last=False，降序時缺值列排在最前面；foreign_net_5d 與 investment_trust_net_5d 只是 null 占位欄，卻列在 SortField 中。
- **正確的定義或數值**：排序時空值一律排在最後；沒有實作的欄位不應開放排序
- **app 實際行為**：點選「漲跌幅 ↓」時，第一頁先顯示沒有漲跌的標的
- **對使用者的影響**：看漲幅排行時要先略過空值列；自然語言翻譯出的 foreign_net_5d 排序會被悄悄退回代號順序
- **修法建議**：改成 df.sort([...], descending=[...], nulls_last=True)；實作 5 日法人加總，或從 SortField 移除這兩個欄位
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/screener.py:572-581
  - backend/app/taiwan/screener.py:41-47、435-437
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 9/23 以 change_pct 降序，前 5 列全部為 None（00625K、00643K、00682U、00707R、00867B）；rsi_14、short_margin_ratio、foreign_net、momentum_5d 降序的第一頁同樣先出現空值列；以 foreign_net_5d 排序等於按代號排序（work/A12/sort_check.py）
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：screener.py:581 的 _apply_sort 沒有傳 nulls_last，使用 polars 預設值，因此不論升序或降序，空值列一律排在最前面。以 2026-09-23 資料實測（母體 2346 檔、前端每頁 50 列、第一次點表頭即為降序）：
- 點「漲跌幅」後，第一頁 50 列中有 46 列漲跌幅是空值，前 5 列為 00625K、00643K、00682U、00707R、00867B（9/23 官方行情同樣沒有成交價）。
- 點「券資比」降序時有 288 列空值，第 1 到 5 頁全是空值。
- 點「融資增減」降序時有 129 列空值，前 2 頁全是空值。
- rsi_14、外資與投信買賣超、momentum_5d 的第一頁也先出現 6 到 50 列空值。
另外，foreign_net_5d 與 investment_trust_net_5d 列在 SortField 中，實際上卻只是全 null 的占位欄；用它們排序的結果等於按代號升序。這只能透過直接呼叫 API 觸發：自然語言翻譯的白名單沒有 sort_by，前端套用翻譯時也不讀 sort_by，UI 也沒有這兩欄的表頭。
修法：改成 nulls_last=True，並另外處理 NaN。vol_ratio_5d 在成交量 0/0 時會產生 NaN（例如 00625K），polars 把 NaN 視為最大值，只加 nulls_last=True 時它仍會排在降序的第一列。同時實作 5 日法人加總，或把這兩個占位欄從 SortField 移除。
    - 理由：我寫了自己的腳本重跑實際的 TaiwanScreenerService.run()：security master 只從快取載入，網路與 write_parquet 都先封鎖。資料基準日是 2026-09-23，母體共 2346 檔。finding 的核心數據完全重現。

重現結果：
- change_pct 降序前 5 列與 finding 一字不差，都是 None：00625K、00643K、00682U、00707R、00867B。
- 以 foreign_net_5d 或 investment_trust_net_5d 排序時，2346 列全部是 null。第一頁結果與「symbol 升序」的第一頁完全相同（True）。

程式碼：
- screener.py:581 的 df.sort([sort_by,"symbol"], descending=[d, False]) 沒有傳 nulls_last。
- 用 polars 1.40.1 實測，預設 desc 得到 [None,3,2,1]，asc 得到 [None,1,2,3]；只有傳入 nulls_last=True 才會變成 [3,2,1,None]。

finding 有 3 處不準確，1 處低估：
1. 不只是降序。升序時 null 也排在最前面：change_pct 升序第一個非空值同樣在第 46 列。
2. 「自然語言翻譯出的 foreign_net_5d 排序會悄悄退回代號順序」這段影響敘述不成立：
   - screener_nl.py 的 SYSTEM_PROMPT 白名單（69-87 行）根本沒有列出 sort_by／sort_order。
   - 前端 handleApplyTranslation（TaiwanScreener.tsx:134-163）完全不套用翻譯結果裡的 sort_by。
   - UI 的可排序表頭也沒有 foreign_net_5d。
   因此這兩個占位欄位只能透過直接呼叫 API 來排序，使用者在 UI 上碰不到。
3. finding 低估了影響範圍。前端 pageSize=50，而第一次點表頭就是降序（handleSort 在 297-305 行）：
   - short_margin_ratio 有 288 列 null，降序時第 1 到 5 頁（前 250 列）全部是空值。
   - margin_balance_change 有 129 列 null，前 2 頁全部是空值。
   - change_pct 第一頁 50 列中有 46 列是空值。
   這已是排行功能在 UI 上明確失效，依 CONTRIBUTING 第 11 節屬於「明確功能錯誤」，應為 P2 而不是 P3。
4. 新面向：vol_ratio_5d 有 1 列是 NaN（00625K，成交量 0/0）。polars 把 NaN 視為最大值，即使改成 nulls_last=True，降序時它仍排在第一列。finding 建議的修法處理不到 NaN；不過 vol_ratio_5d 不是 UI 的可排序表頭，影響很小。

空值來源也查證過。以 00625K、00643K、00682U、00707R 為例，本地 9/23 分區的 close 都是 null，對照官方 TWSE MI_INDEX 2026-09-23 參考檔，這幾檔的 close 也是 null（當天沒有成交）。所以這些 null 是真實的「無成交」，不是資料錯誤；問題純粹在排序位置。

另外，空值列中有 quote_date 是 2025-06-04、2026-04-02 的過期標的。這屬於另一個議題，這裡不展開。

唯讀確認：跑完後 git status 與跑前相同；data 下相關目錄在最近 20 分鐘內沒有新檔。
    - 證據：
      - backend/app/taiwan/screener.py:572-581：_apply_sort 呼叫 df.sort([sort_by, "symbol"], descending=[descending, False])，沒有傳 nulls_last
      - backend/app/taiwan/screener.py:41-47：SortField 包含 foreign_net_5d 與 investment_trust_net_5d；screener.py:435-437 只放 null 占位欄。因為欄位存在於 df.columns，不會觸發 575-577 行退回 symbol 的邏輯
      - polars 1.40.1 實測：預設 desc 為 [None,3.0,2.0,1.0]，預設 asc 為 [None,1.0,2.0,3.0]，nulls_last=True 的 desc 為 [3.0,2.0,1.0,None]；含 NaN 且 nulls_last=True 的 desc 為 [nan,2.0,1.0,None]（work/verify-nullsort/out.txt、nan_out.txt）
      - 本地重跑 TaiwanScreenerService.run()（腳本 work/verify-nullsort/repro.py）：total=2346，daily_as_of=2026-09-23。change_pct desc 前 5 列為 00625K、00643K、00682U、00707R、00867B，值都是 None，與 finding 完全相同；第一個非空值在第 46 列
      - 同一腳本各欄位降序時第一頁（50 列）的空值數：rsi_14 6、foreign_net 7、investment_trust_net 7、momentum_5d 50（總 null 56）、short_margin_ratio 50（總 null 288）、margin_balance_change 50（總 null 129）、close 28；volume 為 0
      - foreign_net_5d：母體 null 數 2346/2346；desc 與 asc 的第一頁 200 列都等於 symbol 升序；foreign_net_5d desc 第一頁 == symbol asc 第一頁 → True
      - 官方對照 reference/csv/twse_mi_index_20260923_quotes.csv：00625K 成交量 0、00643K 0、00682U 999、00707R 0，close_twd 都是 null；本地 daily 9/23 分區這 4 檔的 close 同樣是 null。空值是真實的無成交，不是資料錯誤
      - frontend/src/pages/TaiwanScreener.tsx:297-305：handleSort 切換到新欄位時 setSortOrder('desc')，第一次點擊就是降序；TaiwanScreener.tsx:113 pageSize = 50；可排序表頭（1509-1590）包含 change_pct、short_margin_ratio、margin_balance_change 等，沒有 foreign_net_5d／investment_trust_net_5d／vol_ratio_5d
      - frontend/src/pages/TaiwanScreener.tsx:134-163：handleApplyTranslation 只套用篩選欄位，完全不讀 req.sort_by／sort_order
      - backend/app/taiwan/screener_nl.py:69-87：SYSTEM_PROMPT 的支援欄位白名單沒有 sort_by／sort_order；自然語言翻譯不會產生 foreign_net_5d 排序，即使產生，前端也不會套用
      - 新面向：vol_ratio_5d 母體中 00625K.TWSE 為 NaN。nulls_last=True 的 desc 排序第一列仍是 00625K（NaN），其後才是 6198.TPEX 4.9975（nan_out.txt）
      - 唯讀確認：執行前後 git status --porcelain 相同；data/taiwan/{daily,institutional,margin}、security_master.parquet、kline_daily_enriched 在最近 20 分鐘內沒有新檔
      - docs/code-review-2026-09-22.md 沒有記錄選股排序的空值位置問題（grep nulls_last、排序、_apply_sort 都沒有命中相關條目）

### [P3 → P3] regime（市場環境）仍是 A 股校準，而且在此部署從未產出（部分成立）

- **主張**：regime_builder 的指數漲幅固定讀上證 000001.SH；子分數門檻沿用 A 股分位（漲停數 35–97、連板 4–9）；ST 剔除依賴 A 股維表。實際上沒有任何正式程式呼叫 compute_regime_incremental，偏好設定 pipeline_regime_enabled 也沒有被 pipeline 讀取。
- **正確的定義或數值**：台股 regime 應以加權指數與台股分位數校準，並接入每日 pipeline
- **app 實際行為**：沒有產出；即使產出，指數維度也固定為中性，投機維度系統性偏低
- **對使用者的影響**：挖掘或回測頁的市場環境過濾無法使用，或會基於空資料運作；目前首頁未顯示 regime，影響有限
- **修法建議**：改讀 t00.TWSE 或 data/taiwan/index_daily；以台股歷史資料重算 p15/p85；在 daily_pipeline 依 pipeline_regime_enabled 呼叫增量計算，或明確停用相關功能
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/services/regime_builder.py:446（symbol='000001.SH'）
  - backend/app/services/regime_builder.py:102-128（A 股 p15/p85 門檻）
  - backend/app/services/regime_builder.py:637-665（增量計算只在 tests 中被呼叫）
  - backend/app/services/preferences.py:302-309
  - backend/app/api/regime.py:58-66
- **線上證據**：
  - （無）
- **本地計算證據**：
  - data/regime_history 不存在，所以 /api/regime/latest 回傳 row=None；backtest 的 regime 過濾讀到的是空表
  - 以純函式 _aggregate_daily 套用台股 enriched 資料（9/8～9/23）：index_pct 全部為 0；max_consecutive 只有 0–2，低於門檻下限 4，連板子分數恆為 0；有 5 天 speculation_score=0，其中 9/22、9/23 當天漲停分別有 26、15 家（work/A12/regime_check.py）
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：regime（市場環境）在此部署從未產出，而且即使產出也仍是 A 股校準：
(1) 沒有任何正式程式呼叫 compute_regime_incremental。它在 2026-09-17 的 commit 0ab1eac 已從 daily_pipeline 移除，pipeline_regime_enabled 只剩設定 API 在讀寫。data/regime_history 不存在，/api/regime/latest 回傳 row=None。
(2) 指數涨幅固定讀 000001.SH，但 kline_index_enriched 是空的，因此 index_pct 恆為 0，指數子分恆為中性 50。改用官方 TAIEX 後，9/14、9/23 的狀態會改變。
(3) 投機子分數沿用 A 股 p15/p85 門檻（漲停 35/97、封板率 57/75、連板 4/9）。台股在 9/16～9/23 的最高連板只有 1–2，連板子分恆為 0。
(4) ST 剔除讀取 A 股維表 data/instruments，但該目錄是空的，所以完全沒有剔除。
用已存 enriched 重算 9/8～9/23 時，speculation_score=0 的是 4 天，不是 5 天。其中 9/8、9/9 是分區缺少 signal_limit_up／consecutive_limit_ups 欄位造成的假零；9/22（26 家漲停）與 9/23（15 家，全部是上市股，與證交所 MI_INDEX 的 15 家一致；當天本地資料缺上櫃，全市場約 31 家）才是門檻造成的真實零分。
對使用者的影響：回測的 regime 過濾與挖掘遇到空表時會報錯或直接跳過，不會基於空資料運作；前端也已沒有任何 regime 的使用點，實際影響很小。
    - 理由：主張的核心已完全重現：regime 在此部署從未產出，指數維度固定為中性，投機維度沿用 A 股門檻，ST 剔除實際上沒有作用。但 finding 列的本地數字有兩處不準，對使用者影響的描述也有一處錯誤：
(1) 以相同的純函式 _aggregate_daily 對 9/8～9/23 重算，speculation_score=0 的天數是 4 天（9/8、9/9、9/22、9/23），不是 5 天。其中 9/8、9/9 並非真實訊號，而是資料缺口造成的假象：已存的 enriched 分區在 9/10 以前沒有 signal_limit_up 欄位，9/16 以前沒有 consecutive_limit_ups 欄位（全部 661 個分區中，缺少這兩欄其一的有 655 個）。缺欄時 limit_up 與 max_consecutive 會被當成 0。
(2) max_consecutive 的範圍確實是 0–2，但 0 同樣來自缺欄。在有欄位的 9/16～9/23，實際值是 1–2，仍低於門檻下限 4，所以「連板子分數恆為 0」這個結論成立。
(3) 9/22、9/23 的漲停家數 26、15 可以重現。9/23 的 15 家全部是上市股，與證交所 MI_INDEX 公布的「股票 388(15)」完全一致。不過當天本地資料完全缺少上櫃股，只有 1323 列、全為 .TWSE。我用櫃買 9/23 行情推算約有 16 家上櫃股漲停，全市場約 31 家，仍低於 A 股下限 35。
(4) 對使用者影響：回測的 regime 過濾讀到空表時不會「基於空資料運作」，而是由 align_regime_t_minus_one 拋出 ValueError 直接失敗。挖掘排程的前置檢查也會回傳 "regime data is unavailable" 而跳過。另外，前端已沒有任何 regime 的使用點（regimeLatest 已移除，前端也找不到 regime_filter），實際影響比 finding 描述的更小，維持 P3 合理。
(5) 以官方加權指數（TAIEX）帶入比對：同期間指數日漲跌介於 -1.61% 到 +1.93%，但程式一律取 0。趨勢子分數最多差 19 分，9/14 的狀態會由 range 變 lean_weak，9/23 則由 lean_weak 變 range。
(6) 額外發現（只在缺欄的資料上實測過，需要時再另開 finding）：對全部 661 天直接呼叫 _aggregate_daily，會在 pl.DataFrame(rows) 拋出 ComputeError。原因是 promo_rate 在前 100 列全部是 None，直到第 657 列才出現浮點數。由於 instruments 是空的，fallback 路徑不會重算漲跌停訊號，看到的就是這種缺欄資料，所以若重新接上增量計算，首次全量回填很可能在 fallback 路徑崩潰。快取路徑是否有同樣問題未驗證。
    - 證據：
      - 程式碼：backend/app/services/regime_builder.py:446 預設 symbol="000001.SH"；:458-466 run_regime_batch 呼叫 _load_index_pct 時沒有傳入 symbol
      - 程式碼：regime_builder.py:102-128 註解寫明「用 A 股 2022-2026 真实 p15/p85 分位数校准」，涨停数 35/97、封板率 57/75、连板高度 4/9；:83 docstring 寫明「未针对台股重算」
      - 程式碼：regime_builder.py:27-49 load_risk_warning_symbols 讀取 data/instruments；實測該目錄有 0 個 parquet 檔，函式回傳 0 檔、只記一筆 warning，因此 ST 剔除完全沒有作用
      - grep compute_regime_incremental：正式程式碼只有 regime_builder.py:637 的定義，其餘出現處都在 tests\test_regime_builder.py；git show 0ab1eac（2026-09-17）刪除了 app/jobs/daily_pipeline.py 裡原本 Step 2.6 依 get_pipeline_regime_enabled() 呼叫 compute_regime_incremental 的程式碼
      - grep pipeline_regime_enabled：只出現在 app\api\settings.py:456,963-971、app\services\preferences.py:302-309 與 frontend api.ts:2050；daily_pipeline.py 裡沒有任何 regime 字樣（grep exit=1）
      - 本地資料：data/regime_history 不存在，data/kline_index_enriched 有 0 個 parquet 檔 → /api/regime/latest（app\api\regime.py:58-66）回傳 {"row": None}，_load_index_pct 回傳 {}
      - 重算指令：cd backend && uv run python scratchpad/audit/work/verify-regime/repro.py（輸出在 out.txt）。9/8～9/23 index_pct 的所有值都是 [0.0]；max_consecutive 範圍 0..2；speculation_score==0 共 4 天：9/8（limit_up=0）、9/9（limit_up=0）、9/22（limit_up=26，seal 0.5652，max_consec 1）、9/23（limit_up=15，seal 0.5172，max_consec 1）。finding 寫的是 5 天
      - repro2.py（out2.txt）：各分區欄位檢查顯示 9/1～9/9 缺 signal_limit_up、9/10～9/15 缺 consecutive_limit_ups；全部 661 個分區中缺其一的有 655 個（2024-01-02..2026-09-15）。有欄位的 6 天（9/16～9/23）台股分位數：limit_up p15=24、p85=66（A 股 35/97），max_consecutive p15=1、p85=2、最大值 2（A 股 4/9），seal_rate p15=54.4%、p85=88.0%（A 股 57/75）
      - 官方參考 csv/twse_mi_index_20260923_updown_counts.csv：上漲(漲停) stocks="388(15)"，與本地 9/23 的 15 家上市漲停完全一致；本地 9/23 分區只有 1323 列 .TWSE，沒有 .TPEX（tpex923.py，out3.txt）
      - 官方參考 csv/tpex_daily_quotes_20260923.csv：以前一日收盤價×1.1 並依檔位無條件捨去推算漲停價，866 檔上櫃股中收盤價達漲停價的有 16 檔，推算全市場約 31 家，仍低於 A 股下限 35（檔位規則為近似，屬推測）
      - 官方參考 csv/index_daily_202608_202609.csv 的 TAIEX 收盤價：9/8～9/23 日漲跌 -0.47%、+0.16%、-0.51%、-1.61%、-0.70%、-0.77%、+0.74%、+0.96%、+1.93%、+1.14%、+0.17%、+0.75%。帶入後 trend_score 在 9/18 由 50 變 69、9/11 由 30 變 14；state 在 9/14 由 range 變 lean_weak、9/23 由 lean_weak 變 range
      - data/taiwan/index_daily 已有 t00.TWSE（9/17 收盤 46288.0，與官方 TAIEX 相同），finding 的修法方向可行
      - 影響面：app\backtest\regime_alignment.py 的 align_regime_t_minus_one 在 regime_map 為空時 raise ValueError("市场环境数据为空...")，屬於直接失敗；app\services\mining_schedule.py:179-183 回傳 "regime data is unavailable"；frontend/src/lib/api.ts:2557-2563 註明 regimeLatest 唯一的前端使用點已移除，前端 ts/tsx 找不到 regime_filter
      - 額外發現：repro2.py 對 661 天呼叫 _aggregate_daily 時出現 ComputeError「could not append value: 0.0882 of type: f64」；promo_rate 第一個非 null 值在第 657 列（2026-09-18）
      - 唯讀確認：稽核前後 git status 相同（只有原本的 3 個 M 檔），data/regime_history 仍不存在

### [P3 → P3] 市場快照的指數欄位寫死為 unavailable，另有分布與成交檔數的口徑細節（部分成立）

- **主張**：indexes 永遠回傳 unavailable（即使 data/taiwan/index_daily 已有資料）；漲跌幅分布把平盤（0%）歸入「0~1%」正向段；traded_count 以 volume>0 計算，把只有零股成交、沒有收盤價的列算成已成交。
- **正確的定義或數值**：指數欄位讀取本地指數資料；平盤獨立成一段或註明；已成交以有收盤價判定
- **app 實際行為**：如上；目前這三個欄位只由 API 提供，前端未顯示
- **對使用者的影響**：目前畫面沒有影響，將來若接上前端會顯示錯誤或誤導的數值
- **修法建議**：從 index_daily 分區讀取目標日收盤與漲跌；分布新增「0%」段；traded_count 改以 close 非空判定
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/market_intelligence.py:558-573
  - backend/app/taiwan/market_intelligence.py:45-46
  - backend/app/taiwan/market_intelligence.py:244
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 9/23 平盤 137 檔全部落在「0~1%」段
  - app traded_count 為 1,326；官方有收盤價的股票加 ETF 為 1,323。差異是 00682U（999 股）、1538（167 股）、6952（10 股）這類只有零股成交、官方標為未成交的列
  - data/taiwan/index_daily 已有 2024-08-01 起的分區，但 IndexSnapshot 的 status 恆為 'unavailable'
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：三個技術缺陷都能重現，但 finding 的影響敘述要修正。

(1) indexes 和 data_quality.indexes 被寫死為 unavailable（market_intelligence.py:558-573、606-611），完全沒有讀本地 index_daily。本地 index_daily 有加權指數 t00.TWSE，期間 2024-08-01～2026-09-17；櫃買指數 o00.TPEX 只從 2025-08-01 開始。本地數值和官方逐筆一致。所以對 9/17 以前的日期，這個欄位把手上有的資料報成 unavailable。不過本地指數缺 9/18～9/23，以最新交易日 9/23 為目標時，就算接上也拿不到當日值。

(2) 漲跌幅分布把平盤（0%）歸入「0~1%」段。9/23 的「0~1%」段有 487 檔，其中 137 檔是平盤，跟官方持平口徑的 137 檔相同。官方統計則把「持平」獨立成一類。

(3) traded_count 用 volume>0 判定，9/23 算出 1326。官方有收盤價的股票加 ETF 是 1323 檔，差的是 00682U、1538、6952 這三檔只有零股成交、官方收盤價為「--」的列。官方成交股數本來就含零股，這三檔只有在官方漲跌統計裡被歸為「未成交」。這個比較只涵蓋上市，因為本地 9/23 分區沒有 TPEX 資料。

影響要更正：traded_count 並不是「前端未顯示」。它顯示在台股選股頁 TaiwanScreener.tsx:556 的「有效撮合標的: 1326 / 2377 檔」，比有收盤價的口徑多 3 檔。change_distribution 和 indexes 才是目前前端沒有使用的欄位。

另外，同一根因下還有一個 finding 沒提的問題：有前收、但當日 close 為 null 的 5 列不算進上漲、下跌、平盤、uncompared 任何一類。結果 538+647+137+3 = 1325，比 snapshot_row_count 1330 少 5。
    - 理由：我用官方參考資料和本地 parquet 重算，finding 講的三個技術缺陷全部重現，數值也一致。但它的影響敘述錯了：traded_count 其實有顯示在前端。

1) 指數欄位：market_intelligence.py:558-573 的 IndexSnapshot 把 status="unavailable" 寫死，close、change 都是 None；606-611 的 data_quality.indexes 也寫死成 unavailable。本地 index_daily 有 519 個分區（2024-08-01～2026-09-17）。其中 t00.TWSE（加權指數）有 519 列，從 2024-08-01 起；o00.TPEX（櫃買指數）只有 276 列，從 2025-08-01 才開始，finding 沒提到這點。本地收盤價和官方 MI_5MINS_HIST／TPEx inx 重疊的 68 筆逐筆比對，最大差異是 0.0。所以像 date=2026-09-17 這樣的查詢，本來可以給出 TAIEX 46288.0（+439.1，+0.96%）和櫃買 398.17（-1.04，跟官方的 -1.04 一致），實際回傳卻是 unavailable。不過本地指數只到 9/17，9/18～9/23 共 8 筆官方資料本地沒有。因此以目前最新交易日 9/23 為目標時，就算接上 index_daily 也拿不到當日值，此時回傳 unavailable 碰巧是對的。

2) 漲跌幅分布：9/23 的 `_calc_change_distribution` 總共分到 1322 列。「0~1%」段有 487 列，其中恰好 0% 的 137 列（平盤）全部在這段，嚴格大於 0 的有 350 列；「-1~0%」段 393 列。這和 finding 說的「平盤 137 全部落在 0~1%」完全一致。官方 MI_INDEX 的漲跌證券數合計把「持平」獨立成一類。另外在 universe 範圍內，官方「漲跌價差為 0 且有收盤價」的也是 137 檔，和 app 的 flat_count 相同。

3) traded_count：我自己組 9/23 的資料丟進 app 的 `_calc_breadth`，得到 traded_count=1326，close 非空的有 1323 列。官方 MI_INDEX 在 universe 內有收盤價的是 1323 檔。差異正好是 00682U（999 股，官方收盤價為「--」）、1538（167 股）、6952（10 股），跟 finding 相同。補充一點：官方 MI_INDEX 註記寫「本統計資訊含一般、零股、盤後定價、鉅額交易」，所以這三檔的官方成交股數本來就大於 0，app 的 volume>0 和官方成交股數口徑是一致的。它們只有在官方漲跌統計裡被歸為「未成交」：官方「股票」欄未成交 3 檔，和官方行情中股票類收盤為「--」的 7732、1538、6952 數量一致。這三檔就是那 3 檔屬於推測，因為官方表沒有列出個別代號。

需要修正的地方：
(a) finding 說「前端未顯示、目前畫面沒有影響」，這不對。traded_count 顯示在台股選股頁（路由 /taiwan-screener）：TaiwanScreener.tsx:556 寫的是「有效撮合標的: {traded_count} / {supported_count} 檔」，9/23 會顯示 1326，比官方有收盤價的口徑多 3。change_distribution 和 indexes 則確實沒有前端使用：前端 src 只有 api.ts:1109-1111 的型別定義，沒有任何元件讀取。
(b) 這裡的 1326 只涵蓋上市。本地 9/23 分區只有 1330 列 TWSE 資料，沒有 TPEX（9/22 分區有 TPEX 1009 列），所以這是上市範圍內的比較，不是全市場。
(c) 同一個根因還連帶另一個 finding 沒提的問題：有前收、但當日 close 為 null 的 5 列（00643K、00707R、7732、00682U、6952）既不算進上漲、下跌、平盤，也不算進 uncompared。結果 advance+decline+flat+uncompared = 538+647+137+3 = 1325，比 snapshot_row_count 1330 少 5。

嚴重度維持 P3。畫面上的偏差只有 3 檔（約 0.2%），另外兩個欄位沒有接到前端。
    - 證據：
      - backend/app/taiwan/market_intelligence.py:558-573：IndexSnapshot 的 status 寫死為 'unavailable'，沒有讀取任何本地資料；:606-611 的 data_quality.indexes 也寫死成 unavailable
      - backend/app/taiwan/market_intelligence.py:45-46：('-1~0%', -0.01, 0.0) 與 ('0~1%', 0.0, 0.01)，下界含、上界不含，0.0 因此落在 0~1%
      - backend/app/taiwan/market_intelligence.py:244：traded = df.filter(pl.col('volume') > 0)；:249-254 的 comparable 只看 prev_close 非空，close 為 null 的列不會進任何一類
      - frontend/src/pages/TaiwanScreener.tsx:556：有效撮合標的: {intelData.market_totals.traded_count} / {supported_count} 檔（路由在 frontend\src\router.tsx:99 path 'taiwan-screener'），traded_count 確實顯示在畫面上
      - 前端 src 用 grep 搜 change_distribution、.indexes、tpex_index、.taiex，只命中型別定義 frontend\src\lib\api.ts:1109-1111，沒有元件使用
      - 本地計算 AUDIT_SCRATCH/work/verify-mi-snap/step2.py（cd backend && uv run python step2.py）：_calc_breadth 輸出 traded_count=1326、advance=538、decline=647、flat=137、uncompared=3、snapshot_row_count=1330；close 非空 1323；volume>0 且 close 為 null 的是 00682U（999）、1538（167）、6952（10）
      - step2.py 分布輸出：<-5% 13、-5~-3% 21、-3~-1% 220、-1~0% 393、0~1% 487（其中 _change==0 的 137、>0 的 350）、1~3% 130、3~5% 28、>5% 30，合計 1322
      - 官方 MI_INDEX 2026-09-23（https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&date=20260923&type=ALLBUT0999，取自 reference\raw\twse_mi_index_20260923.json）：00682U 成交股數 999、收盤價 '--'；1538 為 167、'--'；6952 為 10、'--'；在 universe 內有收盤價 1323 檔，成交股數>0 的 1326 檔；漲跌價差 0 且有收盤價 137 檔
      - 官方 MI_INDEX 註記原文：'本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。'；漲跌證券數合計分成 上漲(漲停)、下跌(跌停)、持平、未成交、無比價五類，股票欄為 388(15)、562(1)、123、3、5
      - 本地 index_daily（step1.py、step3.py）：519 個分區 2024-08-01～2026-09-17；t00.TWSE 519 列從 2024-08-01 起，o00.TPEX 276 列從 2025-08-01 起；和官方 reference\csv\index_daily_202608_202609.csv 重疊 68 筆，最大絕對差 0.0；官方 9/18、9/21、9/22、9/23 兩個指數共 8 筆本地缺漏
      - 如果接上 index_daily，2026-09-17 會是 t00.TWSE close 46288.0、change +439.1（+0.96%），o00.TPEX close 398.17、change -1.04（官方 change_pts_official 同為 -1.04）
      - 本地 daily 分區：date=2026-09-23 有 1330 列，全部是 TWSE（沒有 TPEX）；date=2026-09-22 有 2340 列（TWSE 1331、TPEX 1009）。1326 對 1323 只是上市範圍內的比較
      - close 為 null 但 prev_close 非空的 5 列：00643K（前收 3.88）、00707R（31.86）、7732（34.3）、00682U（20.64）、6952（38.45），不在 adv/dec/flat/uncompared 任何一類；538+647+137+3=1325，比 1330 少 5

### [P3 → P3] 自選維度成員彈窗的平盤數把沒有行情的成員也算進去（被推翻）

- **主張**：flat 的算法是 rows.length 減去非 0 漲跌的筆數，漲跌幅為 null 的成員因此被算成平盤。
- **正確的定義或數值**：flat＝漲跌幅等於 0 的筆數
- **app 實際行為**：flat 包含缺值的成員
- **對使用者的影響**：啟用擴充維度欄位後，平盤家數會被高估
- **修法建議**：改成 changes.filter(v => v === 0).length
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/components/DimensionMembersDialog.tsx:151
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 程式推導：10 列中 3 漲、2 跌、1 平、4 列 null 時，flat＝10−5＝5，正確值應為 1
  - 此部署沒有 data/ext 擴充設定，這個彈窗目前不可達
- **獨立驗證**：
  - **reproduction**：refuted；修正後嚴重度 none
    - 修正後敘述：stats.flat 等於 rows.length 減去非 0 漲跌的筆數，所以缺值成員確實會算進這一格。但這格在畫面上的欄位名稱是「平盤/待更新」（DimensionMembersDialog.tsx:217），它本來就是「0 漲跌 + 無行情」的合計，而且上漲、下跌、平盤/待更新三欄加總恆等於成員總數。finding 的例子（3 漲、2 跌、1 平、4 缺值）顯示 5 是正確值，不是高估。finding 建議的修法反而會讓待更新成員從統計中消失，欄位名稱也會跟著變錯。另外，此部署的 data/ext_data 不存在（finding 誤寫成 data/ext），彈窗本來就不可達。不構成缺陷。
    - 理由：finding 描述的計算方式可以重現，但它說的缺陷並不存在。我用 node 照 DimensionMembersDialog.tsx:146-154 的 stats 邏輯重跑 finding 舉的例子（10 列：3 漲、2 跌、1 平、4 列 null），結果是 up=3、down=2、flat=5，和 finding 算出的 5 一樣。

問題在於畫面上顯示這個數字的欄位名稱是「平盤/待更新」（DimensionMembersDialog.tsx:217），不是「平盤」。rows.length − 非 0 漲跌筆數，恰好等於「漲跌為 0 的筆數 + 沒有有效漲跌幅的筆數」。腳本在三組測資（含 NaN、undefined、非數字）都驗證了兩件事：flat === zeroOnly + missing，以及 up + down + flat === n。可見這是刻意設計的完整分桶，每一列都會落進其中一個桶，而且數值和欄位名稱一致。

所以 finding 說「正確值應為 1」「平盤家數被高估」，是只看了變數名 flat、沒看顯示欄位名稱。照它的修法改成 changes.filter(v => v === 0).length，4 列待更新就會從統計中消失，三欄加總不再等於總數，「平盤/待更新」這個欄位名稱反而變成錯的。

另外，finding 說「沒有 data/ext 擴充設定」，路徑寫錯了。ExtConfigStore 讀的是 data/ext_data（backend/app/services/ext_data.py:212、219）。實際檢查 data/ext_data、data/ext_configs.json、data/ext_configs.json.bak 都不存在，.env 的 DATA_DIR=./data。因此「這個彈窗目前在此部署不可達」的結論成立，只是路徑名稱要更正。

這條 finding 屬於前端 UI 的計數邏輯，官方參考資料與 parquet 裡都沒有可以比對的數值，因此沒有做線上核實，也不需要。

還有一個次要語意點（推測，未列為缺陷）：成員不在 marketSnapshot 時也沒有 change_pct（member 列只挑了 symbol、name 等欄位，見 backend/app/api/ext_data.py:236-242）。這類成員也會被算進「待更新」，但它們未必真的是「待更新」。不過欄位名稱已經明示這一格混合了兩類，而且功能在此部署不可達，影響低於 P3。
    - 證據：
      - frontend/src/components/DimensionMembersDialog.tsx:151 `flat: rows.length - changes.filter(value => value !== 0).length,`（計算方式和 finding 描述的一致）
      - frontend/src/components/DimensionMembersDialog.tsx:217 `<Summary label="平盤/待更新" value={stats.flat} ... />`：顯示的欄位名稱明確把「待更新」算在內
      - git blame 顯示第 151 行與第 217 行都來自同一個 commit fd79b63（nanachi1212，2026-09-11），是一起寫進去的設計
      - 腳本 AUDIT_SCRATCH/work/verify-dimflat/repro.mjs，指令 `timeout 30 node repro.mjs` 的輸出：finding_example {n:10, up:3, down:2, flat:5, zeroOnly:1, missing:4, flatEqualsZeroPlusMissing:true, sumCheck:true}
      - 數值並列：finding 算出 flat=5 → 重現值 5（一致）；finding 主張正確值 1 → 那是只算 0 漲跌的筆數，但欄位是「平盤/待更新」，應顯示 1+4=5，所以目前的 5 才是對的
      - 其他兩組測資：nan_and_undefined {n:6, flat:4, zeroOnly:1, missing:3, flatEqualsZeroPlusMissing:true, sumCheck:true}；all_quoted {n:5, flat:2, zeroOnly:2, missing:0, flatEqualsZeroPlusMissing:true, sumCheck:true}
      - backend/app/services/ext_data.py:212、219：擴充設定放在 data_dir / "ext_data" / {config_id} / config.json，不是 finding 寫的 data/ext
      - `ls data/ext_data data/ext_configs.json data/ext_configs.json.bak` 三個都是 No such file or directory；.env 第 38 行 DATA_DIR=./data，所以此部署沒有任何擴充設定，彈窗不可達
      - frontend/src/pages/Watchlist.tsx:186-192、216：只有 ext 類型的欄位才會觸發 onDimensionClick 開啟這個彈窗

## 確認正確的項目

- **日K價格（9/23 上市全市場收盤、10 檔自選／指定股 2026-08～09 開高低收）**：以 polars 讀 data/taiwan/daily，逐檔比對官方 MI_INDEX 9/23 1,330 列的收盤（不一致 0 筆），以及 STOCK_DAY 380 列的 OHLC（開、高、低、收不一致皆為 0）。腳本 work/A12/breadth.py、indicators_check.py
  - 來源：reference/csv/twse_mi_index_20260923_quotes.csv
  - 來源：reference/csv/twse_stock_day_202608_202609.csv
  - 來源：data/taiwan/daily/date=2026-09-23\part.parquet
- **市場統計：非除權息列的上漲、平盤家數（market_intelligence._calc_breadth）**：以 fake store 餵入本地資料後執行 TaiwanMarketIntelligenceService.get_snapshot(2026-09-23)，與官方 MI_INDEX 的 +/-/空白 符號逐列分類比對。股票：上漲 391＝391、平盤 124＝124；ETF：上漲 147＝147、平盤 13＝13。下跌差異只來自除權息 X 列（見 findings）
  - 來源：backend/app/taiwan/market_intelligence.py:249-254
  - 來源：reference/csv/twse_mi_index_20260923_quotes.csv
  - 來源：reference/csv/twse_mi_index_20260923_updown_counts.csv
- **漲停家數 upper_limit_count（9/23 上市股票 15 檔）**：依規則獨立計算：參考價＝收盤－漲跌，漲停價＝參考價×1.1 依升降單位向下取整。得到 15 檔 [1235,1727,2104,2342,2368,2404,3016,3653,4770,6205,6243,6426,6526,6838,6933]，與 app 旗標的名單完全相同，也與官方漲跌證券數表「股票 上漲(漲停) 388(15)」的 15 檔一致
  - 來源：backend/app/taiwan/market_intelligence.py:413-443
  - 來源：reference/csv/twse_mi_index_20260923_updown_counts.csv
- **融資融券全市場彙總（margin_balance、margin_balance_change、short_balance、short_balance_change、aggregate_short_margin_ratio）**：app 9/23：融資餘額 11,629,455,000 股、增減 48,944,000、融券 246,733,000、增減 -7,889,000、券資比 2.12%。官方上市（9,282,264 張）加上櫃（2,347,191 張）＝11,629,455 張；增減 36,897＋12,047＝48,944 張；融券 213,059＋33,674＝246,733 張；券資比 246,733/11,629,455＝2.1216%。全部一致
  - 來源：backend/app/taiwan/market_intelligence.py:533-550
  - 來源：reference/csv/twse_mi_margn_summary_20260922_20260923.csv
  - 來源：reference/csv/tpex_margin_balance_summary_20260922_20260923.csv
- **逐檔融資餘額、融券餘額、融資增減、short_margin_ratio（選股欄位「券資比」，10.0＝10%）**：9/23 上市 1,297 列與上櫃 919 列逐檔比對：餘額、融券、增減、券資比（融券÷融資×100，取 2 位）不一致皆為 0 筆。例如 2454：73/10,300＝0.71%，app 0.71。券資比定義與永豐「券資比是融券餘額與融資餘額的比值」一致。腳本 work/A12/smr.py
  - 來源：reference/csv/twse_mi_margn_20260922_20260923.csv
  - 來源：reference/csv/tpex_margin_balance_20260922_20260923.csv
  - 來源：https://www.sinotrade.com.tw/richclub/hotstock/%E5%88%B8%E8%B3%87%E6%AF%94%E6%98%AF%E4%BB%80%E9%BA%BC-%E5%A6%82%E4%BD%95%E5%BE%9E%E5%88%B8%E8%B3%87%E6%AF%94%E5%88%A4%E6%96%B7%E6%95%A3%E6%88%B6%E6%83%85%E7%B7%92-%E6%8E%8C%E6%8F%A1%E8%82%A1%E5%B8%82%E9%97%9C%E9%8D%B5%E9%A2%A8%E5%90%91%E7%90%83-%E8%82%A1%E5%B8%82%E8%A9%B1%E9%A1%8C-6891963480316d3e57862f57
- **逐檔外資、自營商買賣超（法人 store，餵給市場彙總與產業彙總）**：9/23 上市 T86 與 app 逐檔比對：外資不一致 0、自營不一致 0。上櫃 insti/dailyTrade 同樣為 0。app 外資全列合計 123,023,127 股，恰好等於官方 T86（152,713,328）＋TPEx（-29,690,201）。腳本 work/A12/inst_margin.py
  - 來源：reference/csv/twse_t86_20260922_20260923.csv
  - 來源：reference/csv/tpex_insti_daily_trade_20260922_20260923.csv
- **趨勢參與度（above_ma5/20/60、new_high/low_60d）**：以 store 收盤獨立重算 60 個交易日窗（2026-07-01 起）。MA20 以上 639/1289、MA60 以上 568/1254、60 日新高 65、新低 53，與 app 完全一致。MA5 以上 app 640、獨立計算 639，差 1 檔為收盤恰等於均線時的浮點誤差。此欄只由 API 提供，前端未顯示
  - 來源：backend/app/taiwan/market_intelligence.py:307-367
  - 來源：work/A12/dist_trend.py
- **產業情報算術：漲跌平家數、advance_ratio、average/median_change_pct、average_return_5d、relative_strength_5d/20d、turnover、turnover_share；以及 B0 修正後 5 日口徑**：以 fake store 執行 TaiwanIndustryIntelligenceService.get_snapshot，日期為 9/22 與 9/23，與獨立重算比對 35 個產業：家數差 0，報酬類最大差 4.9e-7（來自 6 位小數四捨五入）。5 日基期＝目標日前第 5 個交易日（9/22 的基期是 9/15），相當於 close_T/close_{T-5}−1，符合 5 個交易日報酬的定義。腳本 work/A12/industry_check.py
  - 來源：backend/app/taiwan/industry_intelligence.py:160-182
  - 來源：backend/app/taiwan/industry_intelligence.py:327-423
- **產業前三大漲幅、跌幅、成交額，以及產業法人與融資彙總（foreign_net、investment_trust_net、dealer_net、margin/short_balance_change）**：9/22 共 35 個產業，與獨立排序及加總逐一比對，不一致 0。腳本 work/A12/movers.py
  - 來源：backend/app/taiwan/industry_intelligence.py:395-475
- **選股的 change_pct、ma5、ma10、ma20、momentum_5d（taiwan/screener.py _compute_batch_indicators）**：以官方 STOCK_DAY 原始收盤獨立計算，比對 10 檔 × 12 個交易日（9/8～9/23）共 120 筆，最大絕對差 5.7e-14。口徑為未還原價，除權息問題另見 findings
  - 來源：backend/app/taiwan/screener.py:307-314
  - 來源：work/A12/indicators_check.py
- **第三方比對：2379 在 9/23 的收盤與漲跌幅**：Yahoo 奇摩個股頁（資料時間 9/23 14:30）顯示漲跌 +6（+0.79%）、昨收 755，與官方 STOCK_DAY 收盤 761、+6 及 app change_pct 0.7947% 一致。WebFetch 摘要把數字位數顯示錯（例如 7,616.00），比對以官方值為準
  - 來源：https://tw.stock.yahoo.com/quote/2379.TW
  - 來源：reference/csv/twse_stock_day_202608_202609.csv
- **自選分組統計 computeGroupPcts（等權平均、中位數、上漲家數比、最強、最弱）**：把 watchlistGroupStats.ts:35-86 的函式原樣抽出，去掉型別後用 node 執行。輸入為 enriched 9/1～9/23 共 17 天，分組為使用者實際的「ic設計」「已買」與 all，與 Python 獨立計算比對 204 項，差異 0。9/23 各組結果與官方參考價漲跌幅算出的一致，例如 all 平均 +0.1306%、上漲 6、下跌 3
  - 來源：frontend/src/lib/watchlistGroupStats.ts:35-86
  - 來源：work/A12/groupstats.mjs
  - 來源：work/A12/group_run.mjs
- **多策略共振計數（Strategies.tsx 的 resonance），以及策略快取 total 與 rows 是否一致**：讀 data/user_data/strategy_cache.json（as_of 2026-09-23）：20 個策略的 total 都等於 rows 筆數，rows 內沒有重複代號。被 2 個以上策略選中的標的，獨立 Counter 算得 170 檔，最多一檔被 6 個策略選中（1102），與 Strategies.tsx:109-127 的聚合邏輯一致。api/screener.py:485-497 的 strategy_ids_by_symbol 只納入同一 as_of 的結果，邏輯正確
  - 來源：frontend/src/pages/Strategies.tsx:109-127
  - 來源：backend/app/api/screener.py:432-497
  - 來源：work/A12/cache_stats.py
- **漲跌幅分布的分段算術（change_distribution）**：與官方參考價漲跌幅分段比較：總數都是 1,322；-3~-1%（220）、0~1%（487）、1~3%（130）、3~5%（28）、>5%（30）五段完全相同。其餘差異只來自 3 檔除權息列（見 findings）。此欄只由 API 提供，前端未顯示
  - 來源：backend/app/taiwan/market_intelligence.py:273-305
  - 來源：work/A12/dist_trend.py

## 無法驗證的項目

- **第三方全市場三大法人買賣超金額（9/23 外資、投信，以億元計）**：Goodinfo 回 HTTP 403；鉅亨網盤後頁由前端動態產生，抓不到數值；規則禁止直接連證交所 BFI82U。只能用 Σ（逐檔淨股數×收盤）估算（見 findings），沒有官方金額可以對照
- **第三方 RSI、量比數值（Yahoo、玩股網、XQ）**：XQ 說明頁沒有揭露 RSI 的平滑方式；技術指標頁多由前端動態產生，未取得數值。台灣慣例改以 MoneyDJ 的 Wilder 公式原文作為權威依據
- **ETF 的漲跌停家數（app 9/23 ETF 漲停、跌停皆為 0）**：官方漲跌證券數表只有「全部」（含權證）與「股票」兩欄，沒有 ETF 分項；ETF 次日漲跌停價只在上櫃 dailyQuotes 中有提供
- **9/22 上櫃市場漲跌家數與成交額**：參考資料只有 9/23 的上櫃全市場行情，沒有 9/22 的上櫃全市場檔
- **9/16 上櫃日K缺漏的直接原因**：9/16 16:30 的 log 所在的 backend.log.2 已被輪替刪除；9/23 的同一症狀有 log 可證（見 findings）
- **盤中 rt_pct 在除權息日是否以參考價為基準**：參考資料的 MIS 快照是收盤後抓取，而且 5 檔都不是除權息日，無法確認 MIS 欄位 y 在除權息日是否等於除權息參考價（推測是）
- **首頁「今日市場強弱」門檻（上漲占比 ≥55% 偏強、≤45% 偏弱）**：沒有交易所或教科書的權威門檻。9/23 app 的上漲占比 538/1322＝40.7%，判為偏弱；若補上上櫃並修正除權息列，約為 970/2295＝42.3%，仍是偏弱，結論相同
- **regime_builder 門檻（涨停数 35/97、封板率 57/75、连板 4/9 等）所宣稱的 A 股 p15/p85 分位**：本地沒有 A 股資料，無法重算分位數；只能確認這些門檻不是依台股資料校準（見 findings）
- **T86 投信欄位在 16:30 之後被修正的原因**：只能證明 16:30 寫入的值與 9/24 凌晨官方回傳值不同（外資、自營完全相同，只有投信不同）；是鉅額交易補入或官方事後更正，無法從資料判定（推測）
