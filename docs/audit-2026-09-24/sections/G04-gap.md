# G04-gap｜G4-key-price-levels

> 本文件已封存，後續追蹤改到 [docs/audit-2026-10-22/](../../audit-2026-10-22/README.md)。

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 7 | 4 | 0 | 0 | 0 | 12 | 5 |

## 稽核者摘要

結論：11 類價位裡，算術層面全部正確。9 檔自選股的樞軸點、60/250 日極值、ATR 通道、Keltner、布林帶、斐波那契、整數關口，都用獨立實作重算比對過，差異只有取到小數兩位造成的捨入誤差（≤0.0033）。程式讀進來的 OHLC 也和證交所 STOCK_DAY 一致：9 檔 × 38 個交易日，差異為 0。問題出在口徑與資料語意，共 4 個 P2、7 個 P3：
(1) 籌碼分布的換手率衰減在台股永遠不會生效。原因是台股歷史 turnover_rate 依設計只保留當日（share_capital.py:46-47），實際輸出等於約 338 個交易日、沒有衰減的累積成交量分布；9 檔中有 7 檔的 POC 比現價低 29%～73%。
(2) 即時行情開啟時，樞軸點是用當日還沒收完的 K 棒算的。對話框的預設目標價，9 檔中有 7 檔正好就是樞軸點 P／R1。
(3) 台股用未還原價，0050 在 2025-06 做了 1 拆 4 分割，分割前的價格混進了前高（183.85／188.9）與籌碼分布；2026-06 以前，250 日新高也一直是 188.9。
(4) 缺口回補的判定：只要部分回補就被剔除，但被另一個跳空越過的缺口卻被保留，與它自己的 docstring 以及 StockCharts 的定義都不符。
另外 7 個 P3 見 findings 清單（除息缺口、Keltner 定義、布林帶 ddof、升降單位、聚合函式、無人消費的回傳欄位、簡體標籤）。
方法：用 polars 以 app 相同的 schema 讀 kline_daily_enriched，重現 get_daily 的慢路徑（filter_halt_days＋compute_indicators，視窗為 2025-05-12～2026-09-23），直接呼叫 compute_levels，再用獨立實作比對。官方基準用 reference 內的 STOCK_DAY、TWT49U（除權息）、t187ap03_L／t187ap47_L（股數）以及 MIS 五檔。
順帶觀察：每次走 get_daily 慢路徑，compute_limit_signals 都會因為 instruments 空表而失敗。backend.log／.log.1／.log.3 各有 102／109／96 筆 'on-demand compute failed: unable to find column "symbol"'。這不影響價位計算，但屬於呼叫鏈上的雜訊。
腳本與輸出放在 AUDIT_SCRATCH/work/G04gap/（s1～s7、app_levels.json）。沒有修改 repo：git status 與開始時相同；import app 模組時可能更新了被 gitignore 的 __pycache__。網路請求：WebFetch 約 16 次、WebSearch 1 次（session 額度已用盡，未執行）；Investopedia 無法抓取，改用 TradingView／StockCharts／Wikipedia。

## 問題清單

### [P2 → P2] 即時行情開啟時，樞軸點用當日未完成 K 棒計算，盤中持續漂移（確認成立）

- **主張**：_pivot_points 取 df 最後一根 K。盤中 get_daily 會用 live 快取覆蓋今天那一列（當日部分 K 棒），所以 P/R/S 是用今天未收完的 H/L/C 算出，不是標準做法的前一交易日。
- **正確的定義或數值**：盤中顯示以前一完整交易日 H/L/C 算出、全日固定的樞軸點；收盤後才以當日完整 K 棒計算下一交易日的樞軸點。
- **app 實際行為**：09:00～13:30 間每次開啟或刷新對話框，P/R/S 都依當下的部分 K 棒重算，數值與標準值差距可達 1%～2%（2454 R1 5280 對 5390）。
- **對使用者的影響**：使用者在盤中建立點位提醒時，預設填入的目標價與列出的 R/S 不是市場通行的樞軸點，而且會隨時間變動。
- **修法建議**：當最後一列日期是今天且尚未收盤（或不在官方已發布日期內）時，改取倒數第二列計算，並在回應中標示樞軸點的基準日。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/levels.py:175-182（docstring「代表上一交易日」，程式卻取 df.tail(1)）
  - backend/app/repository.py:1484-1492（以 enriched_latest 覆蓋 cache_date 那一列）
  - backend/app/repository.py:2302-2304（merge_live_enriched_asset 把當日即時列設為 _enriched_cache）
  - frontend/src/components/stock-analysis/PriceAlertDialog.tsx:94-101（預設目標價取上方最近的價位）
- **線上證據**：
  - https://www.tradingview.com/support/solutions/43000521824-pivot-points-standard/ 公式全部使用 prevHigh/prevLow/prevClose
  - https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/pivot-points 「The calculations use the prior day's high, low, and close」（日內圖）
- **本地計算證據**：
  - s6_timeline.py（用 app 錄的 09-23 分鐘 K 模擬）：2454 的標準 09-23 樞軸（取 09-22 K 棒）P 5270／R1 5390；app 在 10:30 為 P 5215／R1 5280，12:00 為 P 5188.33。3034 標準 P 557.67，app 在 12:00 為 550.67。0050 標準 P 112.48，app 在 10:30 為 112.87
  - s7_dialog.py：9 檔中有 7 檔的預設目標價是樞軸 P 或 R1
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：即時行情開啟時，09:00～13:30 盤中 _pivot_points（levels.py:172-182）取 df 最後一列。這一列是 get_daily 用記憶體中的即時 enriched 快取取代的當日部分 K 棒（repository.py:1484-1492、2302-2304；官方未公布當日資料時不寫盤，但快取照樣更新）。所以 P/R/S 用當日未收完的 H/L/C 計算，不是業界通行的前一交易日。

本部署的 instruments_etf／instruments_index 沒有資料，ETF（0050、00878、00885、00893）也被判成 stock，走同一條路徑，所以自選 9 檔全部受影響。

偏差例：2454 R1 盤中約 5280，標準值 5390（約 2%）。/levels 目前唯一的消費者 PriceAlertDialog 的推薦價位與預設目標會因此隨時間漂移。收盤後即時列已是完整 K 棒，當作下一交易日的樞軸是正確的。
    - 理由：我自己讀了程式碼、找了其他權威來源，也在本地重算過，finding 成立，P2 恰當。

1. 程式碼：_pivot_points 取 df.tail(1)，也就是最後一根 K 棒。docstring 自己寫著「代表上一交易日」，程式卻沒有排除當日那一列。compute_levels 把完整的 df 直接傳進 _pivot_points。/levels 端點的 df 來自 repo.get_daily_asset。

2. 盤中最後一列確實是當日未完成的 K 棒：
- QuoteService 每輪呼叫 merge_live_enriched_asset。
- 當日交易所還沒公布官方資料時，_live_write_allowed 會阻止寫盤，但 _enriched_cache 與 _enriched_cache_date=today 的更新寫在寫盤判斷之外，照樣執行。
- get_daily 再用 get_enriched_latest() 的 cache_date 那一列取代歷史中的同日列。
- 所以 09:00～13:30 間，股票路徑的最後一列是當日的部分 K 棒。

3. ETF 在使用者情境下同樣受影響：
- get_etf_daily 本身不做即時覆蓋，而且寫盤被阻止，原本會用前一日 K 棒。
- 但本部署的 instruments_etf、instruments_index 都沒有 parquet 檔（實測 0 個檔），所以 get_etf_symbol_set() 是空集合。
- 因此 resolve_asset_type 把 0050、00878 等判成 stock，一樣走 get_daily 的即時覆蓋。0050.TWSE 的資料也確實存放在 kline_daily_enriched。
- 自選 9 檔在盤中全部受影響。

4. 定義：Wikipedia 與 CFI 都說樞軸點用前一期的 H/L/C 計算，並作為當日固定的支撐與壓力。這兩個來源都不同於 finding 引用的 TradingView 與 StockCharts。

5. 本地重算：
- 官方 09-22 K 棒算出的標準樞軸：2454 P 5270／R1 5390，3034 P 557.67，0050 P 112.48。
- 把 app 錄的 09-23 分鐘 K 以台北時間截斷，組成部分 K 棒，餵進 app 的 _pivot_points：2454 在 10:30 為 P 5215／R1 5280，12:00 為 P 5188.33；3034 在 12:00 為 550.67；0050 在 10:30 為 112.87。
- 這幾個數字與 finding 完全一致。R1 的差距是 2.0%（5280 對 5390）。

6. 使用者影響：/levels 目前唯一的前端消費者是 PriceAlertDialog。它 staleTime 60 秒、預設目標取最近的價位，所以盤中每次開啟或重新抓取，推薦價位都會漂移。

7. 嚴重度：這是明確的功能錯誤，但只影響價格提醒的推薦價位，不破壞資料，也不影響交易結果，所以 P2 合理。
- 收盤後即時列的收盤價已是最終值，用當日 K 棒算下一日的樞軸是正確的，問題只在盤中。

8. 保留意見：
- 模擬用分鐘 K 聚合（分鐘資料從 09:32 才開始），實際即時列的 H/L 取自報價的日內高低，數值只是近似。例如 2454 模擬出的日高是 5260，官方是 5265。
- 「9 檔中有 7 檔的預設目標是 P 或 R1」這一點我沒有重驗。
    - 證據：
      - backend/app/indicators/levels.py:172-182：_pivot_points 的 docstring 寫「基准:最后 1 根 K(代表"上一交易日")」，第 179 行 `last = df.tail(1)` 取最後一列，沒有排除當日
      - backend/app/indicators/levels.py:579：compute_levels 內 "pivot": _pivot_points(df)，傳入完整 df
      - backend/app/api/stock_analysis.py:122,130：df = repo.get_daily_asset(repo.resolve_asset_type(symbol), ...)，接著 levels = compute_levels(df)
      - backend/app/repository.py:1484-1492：get_daily 以 get_enriched_latest() 的 cache_date 那一列取代歷史中的同日列（「盘中更准确」）
      - backend/app/repository.py:2252,2302-2304：write_to_disk 只控制寫盤；`self._enriched_cache = merged_cache`、`self._enriched_cache_date = dt` 在寫盤判斷之外，官方未公布當日資料時照樣更新
      - backend/app/services/quote_service.py:1774-1778：即時輪詢以 merge_live_enriched_asset／flush_live_enriched_asset 寫入 enriched_today
      - backend/app/repository.py:1548-1575（get_etf_daily 不做即時覆蓋），但 resolve_asset_type 依 get_etf_symbol_set()：本地實測 data\instruments_etf、instruments_index、instruments 都是 0 個 parquet 檔，所以 0050、00878 等判成 stock，走 get_daily 的覆蓋路徑
      - frontend/src/components/stock-analysis/PriceAlertDialog.tsx:49-50,96：queryFn api.stockAnalysisLevels(symbol, 250)、staleTime 60_000；預設目標 = recommended.above[0] ?? recommended.below[0]；/levels 在前端只有這一個消費者（AnalysisKChart 未被引用）
      - 線上來源：https://en.wikipedia.org/wiki/Pivot_point_(technical_analysis) 寫明樞軸點是用「the performance of a market in the prior trading period」算出，P = (H + L + C) / 3
      - 線上來源：https://corporatefinanceinstitute.com/resources/equities/pivot-points/ 公式為「Pivot point (P) = (Previous High + Previous Low + Previous Close)/3」，並說用來找出「two support and two resistance levels for the day」
      - 官方參考 CSV（scratchpad\audit\reference\csv\twse_stock_day_202608_202609.csv）09-22：2454 H5480 L5150 C5180；3034 H567 L549 C557；0050 H113.75 L111.85 C111.85。據此算出 09-23 的標準 P：5270／557.67／112.48，2454 R1 5390
      - 本地指令：`cd backend && uv run python scratchpad\audit\work\verify_g4_pivot\check.py`（呼叫 app 的 _pivot_points，歷史 K 棒後接上以台北時間截斷的 09-23 部分 K 棒）。輸出：2454 10:30 P=5215.0 R1=5280.0，12:00 P=5188.33；3034 12:00 P=550.67；0050 10:30 P=112.87 R1=113.08；00878 10:30 P=35.19，標準值 35.31
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：即時行情開啟時，盤中 `/api/stock-analysis/levels` 的樞軸點 P/R/S 是用當日未完成的 K 棒算的，會隨時間漂移。原因是 `_pivot_points` 取 `df.tail(1)`，而 `get_daily` 用即時 `enriched_latest` 快取的今天那一列蓋掉最後一列。標準做法應該用前一個完整交易日的 H/L/C。

ETF 也受影響，前提是本機 ETF instruments 沒有載入（目前正是如此），這時 ETF 被判成 stock、走同一條路徑。

用 app 錄的 09-23 分鐘 K 模擬的結果：
- 2454：標準 P 5270、R1 5390；app 在 10:30 為 P 5215、R1 5280，12:00 為 P 5188.33、R1 5226.67。
- 3034：標準 P 557.67；app 在 12:00 為 550.67。
- 0050：標準 P 112.48；app 在 10:30 為 112.87。

9 檔自選的偏差：P 約 0.05%～2.2%，R1 最高約 3%。10:30 時有 7 檔的提醒對話框預設目標價是樞軸 P 或 R1。

因為模擬用的是稀疏的分鐘 K，而 app 實際用交易所報價的當日高低，實際顯示值會略有不同。收盤後用完整 K 棒計算隔日樞軸點是正確的。
    - 理由：這條 finding 成立，數值也重現了。我用官方參考資料和本地 parquet 各跑了一次重算，並把真實程式碼路徑重放一遍，結果與 finding 的數字逐一吻合。

一、程式碼路徑
1. `_pivot_points` 直接取 `df.tail(1)` 的 H/L/C（levels.py:179-182）。這與它自己 docstring 寫的「上一交易日」（levels.py:175），以及 levels.py:169 標題說的「最近完整交易日」互相矛盾。
2. `/levels` 端點把 `end` 設為 `date.today()`（stock_analysis.py:117-121），再呼叫 `get_daily_asset`。
3. `get_daily` 讀的歷史只到昨天。它接著用 `get_enriched_latest()` 的快取蓋掉 `cache_date` 那一列（repository.py:1484-1492）。
4. 即時輪詢呼叫 `merge_live_enriched_asset`。這個函式在 `write_to_disk` 判斷之外，無條件把 `_enriched_cache` 設成今天的即時列，並把 `_enriched_cache_date` 設為今天（repository.py:2302-2304）。
5. 即時列的 high、low、close 分別取報價的 high、low、last_price（quote_service.py:1097-1100），也就是當日到目前為止的部分 K 棒。所以盤中計算樞軸點用的是當日未完成的 K 棒。

二、ETF 也受影響
- ETF 本來走 `get_etf_daily`，這條路徑不會蓋上即時快取。
- 但本機 `data\instruments_etf`、`instruments_index`、`instruments` 三個目錄都是空的，所以 `get_etf_symbol_set()` 回傳空集合。
- 結果 0050、00878、00885、00893 都被判成 stock，改走 `get_daily`。
- backend.log 在 2026-09-23 10:00 記錄「行情刷新: 8 只股票, 0 只ETF, 0 只指數」，並連續出現「enriched 增量: 8 只, 2026-09-23」，佐證 ETF 走的是股票路徑。

三、數值重現
- 標準樞軸點：用官方 STOCK_DAY 09-22 的 K 棒重算。
- 盤中值：用 app 錄下的 09-23 分鐘 K，時間換成 Asia/Taipei 後截到指定時點，累計 H/L/C。
- 重放方式：把歷史資料（kline_daily_enriched 到 09-22）接上部分 K 棒，餵給真正的 `compute_indicators` 和 `compute_levels`。
- 以下數字全部與 finding 一致。
- 預設目標價：照前端的邏輯重放（取上方最近的價位），10:30 時 9 檔中有 7 檔是 P 或 R1，與 s7 一致；12:00 時是 5 檔。

四、線上來源
- StockCharts 原文：「Pivot Points for 1-, 5-, 10-, and 15-minute charts use the prior day's high, low, and close.」
- TradingView 原文：「P = (prevHigh + prevLow + prevClose) / 3」「R1 = P * 2 - prevLow」「S1 = P * 2 - prevHigh」。

五、需要補充的地方
1. 分鐘 K 很稀疏：第一根在 09:32，全天只有 60～88 根。真正的即時列用的是交易所報價的當日高低，可能更極端。例如 2454 官方當日低點 5140，分鐘 K 最低只到 5150。因此 app 實際顯示的值會和模擬值略有出入，但「用部分 K 棒、隨時間漂移」的結論不受影響。
2. 偏差幅度：9 檔在 10:30 和 12:00 兩個時點，P 偏差介於 0.05% 到 2.16%，R1 偏差介於 0.03% 到 3.03%。其中 2454 在 12:00 的 R1 為 5226.67，比標準值低 3.03%。所以「可達 1%～2%」略低估了上限。
3. ETF 受影響，前提是 ETF instruments 沒有載入，也就是本機目前的狀態。
4. 收盤後用完整 K 棒計算下一交易日的樞軸點，這是正確做法。

六、嚴重度
這是明確的功能錯誤，只影響參考價位和提醒對話框的預設值，而且使用者可以自行修改，維持 P2。
    - 證據：
      - backend/app/indicators/levels.py:175-182：docstring 寫「代表上一交易日」，程式卻用 `last = df.tail(1)` 取 high/low/close；levels.py:169 的標題寫「基于最近完整交易日」
      - backend/app/api/stock_analysis.py:117-121：`end = date.today()`，並呼叫 `repo.get_daily_asset(repo.resolve_asset_type(symbol), ...)`
      - backend/app/repository.py:1484-1492：`get_enriched_latest()` 若 `start <= cache_date <= end`，就先 `df.filter(date != cache_date)`，再 concat 快取列
      - backend/app/repository.py:2302-2304：`self._enriched_cache = merged_cache; self._enriched_cache_date = dt`，位於 `write_to_disk` 判斷之外，未發布日也會執行
      - backend/app/services/quote_service.py:1097-1100：即時日 K 列的 close 取 last_price，high、low 取報價的 high、low（當日至今）
      - backend/app/repository.py:1101-1123、1329-1348：ETF 集合來自 instruments_etf 與 instruments_index，兩者在本機 data 下都是空目錄，所以 `resolve_asset_type` 對 0050 等回傳 stock，改走 `get_daily`
      - data/backend.log：2026-09-23 10:00:00 出現「行情刷新: 8 只股票, 0 只ETF, 0 只指数」，並連續出現「enriched 增量: 8 只, 2026-09-23」
      - frontend/src/components/stock-analysis/PriceAlertDialog.tsx:94-101：預設目標價取 `recommended.above[0] ?? recommended.below[0]`，即所有價位中離現價最近的一個
      - 官方 twse_stock_day_202608_202609.csv 的 09-22 K 棒（H/L/C）：2454 為 5480/5150/5180，算出 P=5270.00、R1=5390.00；3034 為 567/549/557，P=557.67；0050 為 113.75/111.85/111.85，P=112.48。三檔都與 finding 一致
      - 指令：`cd backend && uv run python scratchpad\audit\work\verify-pivot\p1_minute.py`（輸出見 p1_out.txt）。2454 在 10:30 的部分 K 棒為 H5260/L5150/C5235，得 P=5215.00（-1.04%）、R1=5280.00（-2.04%）；12:00 的 P=5188.33、R1=5226.67（-3.03%）。3034 在 12:00 的 P=550.67（-1.26%）。0050 在 10:30 的 P=112.87（+0.34%）。三檔都與 finding 的數字相同
      - 指令：`uv run python scratchpad\audit\work\verify-pivot\p2_codepath.py`（輸出見 p2_out.txt），用 app 的 compute_indicators 與 compute_levels 重放，最後一列日期確認為 2026-09-23。P 值隨時間漂移：2454 在 09:45、10:30、11:00、12:00 依序為 5168.33、5215.00、5198.33、5188.33
      - 同一個 p2 腳本重放預設目標價：10:30 時 2379 為 P、6196 為 R1、3545 為 P、0050 為 R1、00878 為 P、00885 為 P、00893 為 P，共 7/9；另外兩檔 2454 為布林上軌、3034 為 Fib 23.6%。12:00 時為 5/9
      - 同一個 p2 腳本：收盤後用官方 09-23 完整 K 棒，得 2454 P=5196.67、R1=5253.33。這是隔日正確的基準，所以問題只出現在盤中
      - 分鐘 K 的限制：第一根 09:32；2454 分鐘 K 全日 H/L 為 5260/5150，官方為 5265/5140。實際即時列用交易所報價的當日高低，數值會與模擬值略有差異
      - https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/pivot-points 原文：「Pivot Points for 1-, 5-, 10-, and 15-minute charts use the prior day's high, low, and close.」
      - https://www.tradingview.com/support/solutions/43000521824-pivot-points-standard/ 原文：「P = (prevHigh + prevLow + prevClose) / 3」「R1 = P * 2 - prevLow」「S1 = P * 2 - prevHigh」

### [P2 → P2] 缺口回補判定與自身 docstring 及通行定義不一致：部分回補即剔除、被跳空越過的缺口卻保留（確認成立）

- **主張**：程式碼以「後續任一 K 棒與缺口區間有交集（含觸及邊緣）」判為已回補，但 docstring 寫明「僅觸及邊緣不算，必須整個被覆蓋」。通行定義則是價格回到缺口前一日的價位。此外，價格以另一個跳空越過缺口區（沒有任何 K 棒落在區內）時，app 會把缺口判為未回補。
- **正確的定義或數值**：向上缺口在之後 low ≤ 缺口下沿（前日高）時才算回補，向下缺口在 high ≥ 缺口上沿（前日低）時才算回補；越過也算回補。
- **app 實際行為**：只要淺淺碰到就剔除；越過的島型缺口則殘留。
- **對使用者的影響**：「缺口位」清單會漏掉仍有效的缺口，同時保留已失效的缺口（例如 00893 的 40.54 被標為支撐）。
- **修法建議**：回補條件改為 up: any(low[j] <= g_lo)、down: any(high[j] >= g_hi)，並同步修正 docstring；如要保留部分回補的語意，則改為縮小缺口區間而不是整筆剔除。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/levels.py:441-444（docstring：必須整個覆蓋）
  - backend/app/indicators/levels.py:450 if lows[j] <= g_hi and highs[j] >= g_lo（任何交集）
- **線上證據**：
  - https://chartschool.stockcharts.com/table-of-contents/chart-analysis/gaps-and-gap-analysis.md 「the price action at a later time (a few days to a few weeks) usually retraces, at the least, to the last day before the gap」
- **本地計算證據**：
  - s4_sr_gap.py (2)：app 未回補數對標準未回補數：2454 8/11、0050 6/8、00878 12/15、00885 1/3、6196 1/2
  - 部分回補卻被剔除：2454 2026-05-05 向上缺口 2870–3155、0050 2026-04-27 89.95–92.7 與 07-31 96.0–99.8、00885 07-30 17.38–17.52
  - 被越過卻保留：2454 07-28 向下缺口 3460–3525（07-31 以 3410→3555 跳空越過）仍輸出「向下缺口 3492.5」；00893 07-30 向下缺口 40.41–40.67 仍輸出「向下缺口 40.54」
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：_gap_levels 的回補條件（levels.py:450）是「之後任一根 K 棒與缺口區間有交集，只碰到邊緣也算」，這偏離主流定義。主流定義是：向上缺口要跌回前一日高點（low ≤ g_lo），向下缺口要漲回前一日低點（high ≥ g_hi），才算回補；部分回補不算回補。後果是部分回補的有效缺口被剔除，而被另一次跳空越過的缺口（例如 2454 的 3492.5、00893 的 40.54）仍以「向下缺口／支撐」輸出。docstring 的問題是自相矛盾：函式 docstring（:414-416）與括號內的公式（:442）都和程式碼一致，是刻意的「測試過就剔除」設計；只有 :443-444 的文字寫成「僅觸及邊緣不算，必須整個被覆蓋」。「只碰到邊緣就算回補」在使用者自選股資料中沒有單獨造成差異。以使用者自選 9 檔的實際資料來看，有 6 檔的缺口價位線與主流定義不同，包括 2454 漏掉距離現價最近的 09-22 缺口 5035–5150。影響範圍只有圖表價位線與 summary，維持 P2。
    - 理由：我親自讀了程式碼：backend/app/indicators/levels.py:449-452 在後續任一根 K 棒符合 `lows[j] <= g_hi and highs[j] >= g_lo` 時，就把缺口判為已回補並剔除。這是「與缺口區間有交集」的條件，而且用 <=／>=，只碰到邊緣也算。內層 docstring 的文字（:441-444）寫「完全覆蓋缺口真空帶」「僅觸及缺口邊緣不算回補」「必須整個被某根 K 線的高低區間覆蓋」，和程式碼矛盾，finding 這點成立。不過有一個原記錄沒提的細節：同一句括號裡的公式（:442），以及函式本身的 docstring（:414-416：「只要回到缺口區間內,就算已回補(支撐/阻力已被測試消化)」），都和程式碼一致。所以更精確的說法是：docstring 前後自相矛盾，而且「只要進入缺口就剔除」是刻意寫下的設計，不是單純寫錯字。

定義方面，我另外找了三個和 finding 不同的來源。CenterPoint Securities 明確寫出：向上缺口要跌回前一根 K 棒的高點、向下缺口要漲回前一根 K 棒的低點，才算回補；還把「fill only partially」和完整回補分開講。TrendSpider 的定義是價格回到跳空前的水準。台灣的巴菲特線上學院用 26 年回測統計回補率，判定標準是「價格重新觸及缺口的另一端（昨日高／低點）」。另有少數來源（Brave 搜尋結果中的 chartmath 摘要）寫「回到空白區內交易即算回補」，和 app 的做法相同，但只是少數。因此 app 的「部分回補就剔除」偏離主流口徑，和它自己標示的「未回補跳空缺口」也不一致。

被跳空越過的缺口：以 app 自己「支撐或阻力已被測試」的邏輯來看，沒有 K 棒進入過缺口區，所以保留它其實前後一致。但按照主流的「價格回到跳空前水準」定義，缺口已經回補；而且保留下來的線仍掛著「向下缺口」的標籤，卻已被標成支撐，會誤導使用者。

本地重算（我自己寫腳本直接讀 parquet，資料截至 2026-09-23，取最近 120 根 K 棒）：finding 列的未回補數完全吻合，app 對主流定義分別是 2454 8/11、0050 6/8、00878 12/15、00885 1/3、6196 1/2。它列的部分回補案例與被越過的案例也都重現了。我再把兩種定義經過同樣的聚合與「每方向取最近 3 條」後的最終輸出做比對：自選 9 檔中有 6 檔顯示的缺口線不同。最明顯的是 2454：收盤 5185，2026-09-22 的向上缺口 5035–5150 最低只被回測到 5035 以上，主流定義下仍有效，而且是最近的缺口支撐，app 卻把它剔除，反而顯示 2777.5、3482.5，以及已失效的「向下缺口 3492.5（support）」。00893 輸出「向下缺口 40.54（support）」。只碰到邊緣這種情形，在使用者資料中沒有單獨造成差異（沒有任何缺口是只被恰好碰到邊緣、之後再沒深入）；2454 的 05-06 是先碰到邊緣 3155，之後又跌到 2990。

嚴重度：這個功能只影響圖表上的價位線與 summary 文字，不影響交易訊號或回測。但它讓「未回補缺口」這項功能的輸出，在多數自選股上與它的名稱及主流定義不符，屬於明確的功能錯誤，維持 P2 合理。
    - 證據：
      - backend/app/indicators/levels.py:449-452 程式碼：`for j in range(i + 1, len(highs)): if lows[j] <= g_hi and highs[j] >= g_lo: filled = True`（任一交集，含邊緣）
      - backend/app/indicators/levels.py:441-444 內層 docstring 文字：「完全覆盖缺口真空带(low <= g_hi 且 high >= g_lo)…仅"触及缺口边缘"(如 low 跌到上沿)不算回补…必须整个被某根 K 线的高低区间覆盖」；文字與括號內的公式本身就互相矛盾
      - backend/app/indicators/levels.py:414-416 函式 docstring：「后续任何一根 K 线的价格(low/high)只要回到缺口区间内,就算已回补(支撑/阻力已被测试消化)」，與程式碼一致，是 finding 沒提到的部分
      - backend/app/indicators/levels.py:52 標籤 "gap": "缺口位"  # 未回补跳空缺口；:586 compute_levels 呼叫 _gap_levels；backend/app/api/stock_analysis.py:130,138 只用於 /levels API（圖表價位線與 summary）
      - https://centerpointsecurities.com/what-is-a-gap-fill-in-stocks/ 原文："A gap is said to 'fill' when the price of a stock moves back to the pre-gap level."；"After a gap up: the price falls back to the top of the pre-gap candlestick"；"After a gap down: the price rises to the bottom of the pre-gap candlestick"；"Breakaway gaps often do not fill, or fill only partially"
      - https://trendspider.com/learning-center/gap-fill-trading-strategies/ 原文："Gap fill refers to a price movement where the price of a financial instrument moves back to the level it was at before the gap occurred."
      - https://buffettonlineschool.com.tw/gap-fill-myth/ （台灣來源，26 年實測）回補判定：「價格重新觸及缺口的另一端（昨日高／低點）」
      - 少數派來源（Brave 搜尋結果摘要，未另行抓取全文）：https://chartmath.com/blog/gap-fill-trading-strategy-how-to-trade-stock-gaps "A gap is considered 'filled' when the price returns to trade within that empty zone."，和 app 的做法相同
      - 本地重算 verify_gaps.py（scratchpad\audit\work\V-G4-gapfill-def\，用 uv run python，讀 kline_daily_enriched 2026-01-27..2026-09-23、取最近 120 根）：2454 gaps=27 app_unfilled=8 std_unfilled=11；0050 6/8；00878 12/15；00885 1/3；6196 1/2；3034、2379、3545 相同
      - detail.py：2454 2026-07-28 向下缺口 3460–3525，07-30 H=3410、07-31 L=3555（跳空越過），之後最高 5480，app 仍輸出 ('向下缺口', 3492.5, 'support')；00893 2026-07-30 向下缺口 40.41–40.67，07-31 L=42.52 越過，app 輸出 ('向下缺口', 40.54, 'support')
      - detail.py：部分回補被剔除的案例，0050 2026-07-31 缺口 96.0–99.8 之後最低 99.75（沒回到 96.0）；00885 2026-07-30 缺口 17.38–17.52 之後最低 17.43；2454 2026-05-05 缺口 2870–3155，05-06 L=3155 恰好碰到邊緣就被判回補，之後最低 2990；在這些自選股中，沒有任何缺口是只被恰好碰到邊緣就剔除
      - compare_out.py 最終輸出比對：9 檔中 6 檔不同。2454 的 app 輸出為 [4757.5, 3482.5, 2777.5 向上；3492.5 向下]，主流定義為 [5092.5, 4757.5, 4130.0 向上]（收盤 5185，09-22 的 5035–5150 缺口被 app 漏掉）；00885 的 app 輸出為 [17.9]，主流定義為 [17.9, 17.45 向上；18.71 向下]
      - git status --short 前後相同（只有既有的 3 個 M），稽核過程沒有改動 repo
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：_gap_levels 的回補判定（levels.py:450）只要後續任一 K 棒與缺口區間有交集，包含最低價剛好等於缺口上沿這種純觸及邊緣，就把缺口判為已回補並剔除。這違反內層 docstring（levels.py:441-444）「僅觸及邊緣不算、必須整個被覆蓋」的說法，但與外層 docstring（levels.py:414-415）「回到缺口區間內就算回補」一致，所以兩段 docstring 本身互相矛盾。它也不符合通行定義：通行定義要求向上缺口的價格回到缺口前一日的最高價，向下缺口回到前一日的最低價。另一方面，價格以另一個跳空越過缺口區時，因為沒有 K 棒與缺口區間相交，缺口會被保留成未回補。

以本地數據重現（窗口 2026-04-02～09-23；8～9 月的數據已與官方 TWSE 逐列核對一致）：
- 未回補數（app 對標準）：2454 8/11、0050 6/8、00878 12/15、00885 1/3、6196 1/2，與 finding 相同。00893 為 15/15，總數相同，但包含 2 筆部分回補被剔除、2 筆被越過卻保留。
- finding 列出的 4 個部分回補案例，以及 2454 的 3492.5、00893 的 40.54 兩個越過殘留案例，全部重現。
- 另外發現：00893 08-13（43.73–43.87）也被越過卻保留；00885 09-08（18.70–18.73）也是部分回補被剔除。
- 9 檔自選中有 6 檔的「缺口位」輸出會因此不同。
    - 理由：以數據重現的角度驗證，finding 的數字全部重現。我把本地 kline_daily_enriched parquet 餵進 app 自己的 `_gap_levels`，另外依同一窗口重算三種回補規則（app 的交集判定、通行定義、docstring 所寫的單根 K 棒完全覆蓋）。窗口為 2026-04-02～2026-09-23 的 120 根日 K（start = 2026-09-24 − 240 天，與 API get_levels 相同）。

**未回補數（app 對標準）**

| 標的 | finding | 我的結果 |
|---|---|---|
| 2454 | 8/11 | 8/11 |
| 0050 | 6/8 | 6/8 |
| 00878 | 12/15 | 12/15 |
| 00885 | 1/3 | 1/3 |
| 6196 | 1/2 | 1/2 |

3034、2379、3545 在兩種規則下相同。00893 為 15/15，總數相同，但內部組成不同：2 筆部分回補被剔除，2 筆被越過的缺口被保留。

**finding 列出的個案逐一重現**
- 部分回補被剔除：
  - 2454 2026-05-05 向上缺口 2870–3155：05-06 最低價 3155，正好等於缺口上沿，純觸及邊緣就被判回補。這直接違反 levels.py:443 寫的「触及缺口边缘不算回补」。
  - 0050 04-27 缺口 89.95–92.7：04-28 最低價 91.9 即被判回補。
  - 0050 07-31 缺口 96.0–99.8：08-04 最低價 99.75，只比缺口上沿低 0.05 即被判回補。
  - 00885 07-30 缺口 17.38–17.52：08-17 最低價 17.43 即被判回補。
- 被越過卻保留：
  - 2454 07-28 向下缺口 3460–3525：07-30 最高價 3410，07-31 一價到底開在 3555，整個跳過缺口。app 實際輸出「向下缺口 3492.5（support）」。
  - 00893 07-30 向下缺口 40.41–40.67：07-31 最低價 42.52，越過缺口。app 實際輸出「向下缺口 40.54（support）」。

**finding 沒提到的補充**
- 00893 08-13 向上缺口 43.73–43.87 也被越過（08-18 最低價 44.0，08-19 最高價 43.22）。
- 00885 09-08 向下缺口 18.70–18.73 也屬於部分回補被剔除。
- 使用者實際看到的缺口位：9 檔自選中有 6 檔（2454、6196、0050、00878、00885、00893）的輸出會因規則不同而改變。例如 2454 在 app 下為上 [4757.5, 3482.5, 2777.5]、下 [3492.5]；改用標準規則則為上 [5092.5, 4757.5, 4130.0]、下 []。

**資料可信度**
- 這些欄位的 high/low 與 raw_high/raw_low 完全相同（0 列不同），所以缺口用的是未還原價。
- 8～9 月 9 檔共 342 列的 high/low，與官方 TWSE STOCK_DAY 參考資料逐列比對，0 筆不符。
- 4～7 月的事件不在官方參考資料的涵蓋範圍，只能以本地 parquet 為準。

**線上定義**
StockCharts ChartSchool 原文已確認：「the price action at a later time … usually retraces, at the least, to the last day before the gap. This is also known as closing the gap.」

**一點修正**
「與自身 docstring 不一致」只對內層 docstring（levels.py:441-444）成立。外層 docstring（levels.py:414-415）寫的是「只要回到缺口区间内,就算已回补」，這其實與程式碼的交集判定一致。所以更精確的說法是：兩段 docstring 互相矛盾，程式碼符合外層、違反內層。line 442 本身也把交集條件稱為「完全覆盖」。這一點不影響 finding 的核心結論；嚴重度維持 P2。
    - 證據：
      - backend/app/indicators/levels.py:450 `if lows[j] <= g_hi and highs[j] >= g_lo:` 交集判定（含等號觸及邊緣）
      - backend/app/indicators/levels.py:441-444 內層 docstring：「完全覆盖缺口真空带(low <= g_hi 且 high >= g_lo)…仅触及缺口边缘(如 low 跌到上沿)不算回补…必须整个被某根 K 线的高低区间覆盖」
      - backend/app/indicators/levels.py:414-415 外層 docstring：「后续任何一根 K 线的价格(low/high)只要回到缺口区间内,就算已回补」（與程式碼一致，與內層矛盾）
      - backend/app/api/stock_analysis.py:120-130 get_levels 以 date.today()-days*2（days=120）取日K後呼叫 compute_levels；_gap_levels 取 tail(120)
      - 指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-gapfill/gap_verify.py → 2454 gaps=27 app_unfilled=8 std_unfilled=11；0050 6/8；00878 12/15；00885 1/3；6196 1/2；3034 3/3；2379 1/1；3545 1/1；00893 15/15（明細在同目錄 result.json）
      - gap_verify.py 輸出：2454 PARTIAL dropped up 2026-05-05 2870.0–3155.0 app_filled@2026-05-06，edge_touch_bar=2026-05-06（05-06 最低價=3155=缺口上沿）
      - bars_check.py 輸出：0050 04-24 high 89.95／04-27 low 92.7／04-28 low 91.9；0050 07-30 high 96.0／07-31 low 99.8／08-04 low 99.75；00885 07-29 high 17.38／07-30 low 17.52／08-17 low 17.43
      - bars_check.py 輸出：2454 07-27 low 3525／07-28 high 3460／07-30 high 3410／07-31 O=H=L=C=3555；app 真實 _gap_levels 輸出含 ('向下缺口', 3492.5, 'support')
      - bars_check.py 輸出：00893 07-29 low 40.67／07-30 high 40.41／07-31 low 42.52；app 輸出含 ('向下缺口', 40.54, 'support')；另 08-13 向上缺口 43.73–43.87 在 08-19（high 43.22）被越過，也被保留
      - std_output.py：9 檔中有 6 檔的顯示缺口位不同；2454 app 上=[4757.5,3482.5,2777.5] 下=[3492.5]，標準規則 上=[5092.5,4757.5,4130.0] 下=[]；00885 標準規則多出 17.45 與向下 18.71；00893 標準規則移除 40.54
      - bars_check.py：本地 high/low 與 raw_high/raw_low 相異的列數 = 0；官方 twse_stock_day_202608_202609.csv 9 檔 342 列，與本地 high/low 比對不符 = 0
      - https://chartschool.stockcharts.com/table-of-contents/chart-analysis/gaps-and-gap-analysis.md 原文："the price action at a later time (a few days to a few weeks) usually retraces, at the least, to the last day before the gap. This is also known as closing the gap."
      - 工作樹比對：git status 與派工前相同（只有既有的 3 個 M），所有腳本與產物都在 scratchpad\audit\work\verify-gapfill\

### [P3 → P3] 除權息造成的機械性跳空被當成「向下缺口」，並以缺口中點而不是填息價呈現（確認成立）

- **主張**：未還原價在除息日跌出的跳空，會被 _gap_levels 判為向下缺口並作為壓力位輸出。
- **正確的定義或數值**：用還原價偵測缺口；或把除權息日排除，另外呈現填息價（除息前收盤價）。
- **app 實際行為**：除息後數天內顯示並不存在的向下缺口，價位取中點，既不是缺口邊緣，也不是填息價。
- **對使用者的影響**：9 檔目前都已回補，所以現在看不到；但每逢除息季（00878 每季除息）會短暫出現錯誤的壓力位。
- **修法建議**：缺口偵測前先套用 TWT49U 的除權息調整，或跳過除權息日並另設「填息價」類型。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/levels.py:435-436
  - backend/app/indicators/levels.py:454（以缺口中點作為價位）
- **線上證據**：
  - https://zh.wikipedia.org/zh-tw/除權 「除息參考價…＝（除息前一日收盤價－現金股利）」；填權是「股價回升至除權前收盤價」
  - https://chartschool.stockcharts.com/table-of-contents/chart-analysis/gaps-and-gap-analysis.md 「They can be caused by a stock going ex-dividend when the trading volume is low.」
- **本地計算證據**：
  - TWT49U：00878 2026-08-18 除息 1.01（2.99%）、3034 2026-07-10 除息 23（4.24%，首個交易日為 07-13）
  - s6：app 在 2026-08-18 輸出 00878「向下缺口 33.16 resistance」；以還原價計，前日低 33.55−1.01=32.54 < 除息日高 32.76，其實沒有缺口。3034 07-13 輸出「向下缺口 520.0」，其中 23 元是股利
  - 依 app 規則，兩者分別到 08-26、07-22 才被判為回補
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：_gap_levels 用的是未還原價（kline_daily_enriched 的 close 等於 raw_close），所以除息日的機械性跌價會被當成一般的「向下缺口」，並以缺口中點作為壓力位輸出，時間持續到被判定回補為止。實例如下：

- 00878 於 2026-08-18 顯示「向下缺口 33.16 resistance」，缺口區間 (32.76, 33.55)，8/26 才消失。以 TWT49U 還原後，前一日低點是 32.54～32.546，比 8/18 高點 32.76 低，實際上沒有缺口。
- 3034 於 7/13（7/10 除息日休市）顯示「向下缺口 520.0」，缺口區間 (505, 535)，7/22 才消失。還原後仍有一個真缺口 (505, 512.3)，中點 508.65，所以 app 顯示的是被 23 元股利放大、位置也偏高的缺口，而不是不存在的缺口。

同一個根因還有另一面：除息跌價會讓除息前已存在的向上缺口（支撐）被誤判為已回補，因此提早從畫面消失。例如 00878 的 31.59 在 8/19 消失，還原後對應的 30.64 仍然有效；3034 的 454.25 在 7/14 消失，還原後對應的 434.97 仍然有效。

目前 9 檔自選的輸出中，已經沒有由除權息日造成的向下缺口。
    - 理由：我用本地 parquet 加上官方 TWT49U，以 app 的純函式重算了一次。finding 列的數字全部重現，一個都沒差：

- 00878：2026-08-18 輸出「向下缺口 33.16 resistance」，缺口區間 (32.76, 33.55)，中點 33.155，四捨五入後 33.16。套上 TWT49U 的除權息調整後，前一日低點變成 32.54（減股利）或 32.546（乘比例），都比 8/18 的高點 32.76 低，所以其實沒有缺口。依 app 規則，這個缺口一直顯示到 8/25，8/26 才消失。
- 3034：7/13 輸出「向下缺口 520.0 resistance」，缺口區間 (505, 535)，7/22 被判為回補。
- 目前 9 檔自選的輸出裡，已經沒有任何由除息日造成的向下缺口。

資料來源也確認了。get_levels（stock_analysis.py:122,130）經 get_daily 讀 kline_daily_enriched。9 檔自選從 2024-01-02 到 2026-09-23 的資料中，close 與 raw_close 不同的列數都是 0，而且 kline_etf_enriched 與 instruments_etf 目錄都是空的。所以 00878 走的是股票路徑，計算用的是未還原價。

這筆問題在 docs/code-review-2026-09-22.md 裡沒有記錄，docs 內也找不到除權息或還原價相關的條目。

有兩點要修正或補充：

1. 3034 並不是「不存在的缺口」。還原後仍有一個 7 元左右的真缺口，區間 (505, 512.0) 或 (505, 512.30)，中點 508.5 或 508.65。app 顯示的 520.0 是被 23 元股利放大的結果，所以 finding 裡「actual：顯示並不存在的向下缺口」這句只對 00878 成立。finding 在 local_evidence 寫的「其中 23 元是股利」是對的。另外，照 finding 的建議改用還原價偵測後，3034 仍會顯示 508.65 的缺口。
2. 新發現：除息的機械性跌價，也會讓除息前已存在的向上缺口（支撐）被誤判為已回補。
   - 00878 的向上缺口 31.59，用原始價在 8/19 就消失了；還原後對應的 30.64 到 8/25 仍在。
   - 3034 的向上缺口 454.25，用原始價在 7/14 消失；還原後對應的 434.97 到 7/21 仍在。

   這一點原 finding 沒有提到。

「7/13 是 3034 除息後首個交易日」這點也確認了：本地所有標的都沒有 2026-07-10 分區，Yahoo 的 chart API 顯示 7/10 成交量 0、OHLC 都是 542，股利 23.0 記在 7/13。另外 7/13 收盤 467.5，正好等於 TWT49U 以參考價 519 算出的跌停價 467.5。

兩個線上引文都核實了。StockCharts 的句子逐字相符。維基的公式原文是「除息參考價÷（1＋配股率）＝（除息前一日收盤價－現金股利）÷（1＋配股率）」，finding 用刪節號省略了一部分，意思一致。填權的原文是「若除權後股價漲回除權前收盤價，稱為填權」；該頁沒有「填息」的明確定義。

附帶一個不在本 finding 範圍內的觀察：回補判定（levels.py:450）只要價格碰到缺口邊緣就算回補，但 docstring 寫的是要「完全覆蓋」缺口才算。finding 裡的回補日期是照程式實際行為算的，所以仍然成立。

嚴重度維持 P3。它只影響價位顯示，每次除息後持續約 6 個交易日，使用者目前沒有任何監控規則，不會觸發錯誤交易，屬於局部體驗問題。
    - 證據：
      - backend/app/indicators/levels.py:435-436：`elif highs[i] < lows[i - 1]: dn_gaps.append((i, float(highs[i]), float(lows[i - 1])))`，用未還原的 high/low 判斷向下缺口
      - backend/app/indicators/levels.py:454：`mids.append((g_lo + g_hi) / 2)`，價位取缺口中點；levels.py:450 回補判定只要求區間有重疊
      - backend/app/api/stock_analysis.py:122,130：get_daily_asset，接著 compute_levels(df)；repository.py:1761-1776 的 _scan_daily_symbol 讀 kline_daily_enriched，沒有做任何還原
      - 本地（腳本 ...\scratchpad\audit\work\verify-g4gap\s2_repro.py，輸出 s2_out.txt）：9 檔自選在 2024-01-02..2026-09-23 期間 close!=raw_close 的列數都是 0；data\kline_etf_enriched、data\instruments_etf 都是 0 個項目
      - 官方 STOCK_DAY 與本地一致：00878 2026-08-17 H/L=33.90/33.55，2026-08-18 O/H/L/C=32.76/32.76/32.30/32.38（change_flag X）；TWT49U：00878 2026-08-18 除息前收盤 33.75、參考價 32.74、股利 1.01
      - 重算 00878：app 在 2026-08-18 的輸出 ('向下缺口', 33.16, 'resistance') 與 finding 相同，(32.76+33.55)/2=33.155；8/19～8/25 持續出現，8/26 消失（8/26 H=32.85≥32.76），與 finding 的 08-26 相同
      - 重算 00878 還原後：前一日低點 33.55−1.01=32.54，或 33.55×32.74/33.75=32.546，都小於 32.76，所以沒有缺口；還原後在 8/18 的 gap 輸出只剩三個向上缺口 [30.64, 27.71, 26.95]
      - TWT49U：3034 除息日 2026-07-10，除息前收盤 542、參考價 519、股利 23、跌停 467.5；本地 7/09 L=535，7/13 O/H/L/C=494/505/467.5/467.5
      - 重算 3034：app 在 7/13 輸出 ('向下缺口', 520.0, 'resistance')，與 finding 相同；7/14～7/21 持續出現，7/22 消失（H=507），與 finding 的 07-22 相同
      - 3034 還原後仍有缺口：前一日低點 535−23=512.0，或 535×519/542=512.297，都大於 7/13 高點 505；還原後輸出 ('向下缺口', 508.65, 'resistance')，所以 3034 是真缺口被股利放大，不是不存在的缺口
      - 新面向：原始價下，00878 的向上缺口 31.59 在 8/19 消失，還原後對應的 30.64 到 8/25 仍在；3034 的向上缺口 454.25 在 7/14 消失，還原後對應的 434.97 到 7/21 仍在。除息跌價讓支撐缺口被誤判為已回補
      - 現況（as-of 2026-09-23，腳本 s3_current_gaps.py）：只有 2454 在 2026-07-28 形成、00893 在 07-02 與 07-30 形成的向下缺口尚未回補，都不是 TWT49U 列出的除權息日（2454 的除息日是 07-07，00893 不在 TWT49U 中）。其他 TWT49U 列出的除息日（6196 06-24、2454 07-07、0050 07-21、3545 08-04、2379 09-08）在原始價下都沒有形成向下缺口
      - https://query1.finance.yahoo.com/v8/finance/chart/3034.TW?period1=1783296000&period2=1784073600&interval=1d&events=div ：timestamp 1783645200（7/10）volume=0、open=close=542.0；dividends {"1783904400":{"amount":23.0}}（7/13），確認 7/13 是除息後首個交易日
      - https://zh.wikipedia.org/zh-tw/除權 原文：「除息參考價÷（1＋配股率）＝（除息前一日收盤價－現金股利）÷（1＋配股率）」「若除權後股價漲回除權前收盤價，稱為填權」
      - https://chartschool.stockcharts.com/table-of-contents/chart-analysis/gaps-and-gap-analysis 原文：「They can be caused by a stock going ex-dividend when the trading volume is low.」「the high price after the market closes must be lower than the low price of the previous day.」
      - docs/code-review-2026-09-22.md：grep「缺口／除息／除權／還原／復權」沒有相關條目（只有指數 K 線資料缺口的無關內容），不是已知問題

### [P3 → P3] Keltner 三檔不是原作者定義：中軌用 SMA、ATR 固定 14 日，中期／長期參數為自訂（確認成立）

- **主張**：app 的 Keltner 為 SMA20／60／120 ± 2／2.5／3 × ATR14，而通行定義是 EMA20 ± 2×ATR10（Raschke 版，StockCharts／TradingView 預設）；原始版本則是 10 日典型價 SMA ± 10 日平均高低差。
- **正確的定義或數值**：標示為 Keltner 時採用 EMA20±2×ATR10，或把標籤改為「MA±ATR 通道」。
- **app 實際行為**：標籤是 Keltner，但數值與券商或 TradingView 的 Keltner 相差約 1%。
- **對使用者的影響**：使用者拿到 TradingView 對照時，數值對不上；中期／長期兩檔找不到外部依據。
- **修法建議**：改用 EMA20＋ATR10 作為標準 Keltner，或把三檔改名為 ATR 均線通道並在說明中註明參數。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/levels.py:356-368
  - backend/app/api/stock_analysis.py:74-91
- **線上證據**：
  - https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/keltner-channels 「Upper Channel Line: 20-day EMA + (2 x ATR(10))」；Keltner 1960 年原版為「a 10-day SMA of the typical price」
  - https://www.tradingview.com/support/solutions/43000502266-keltner-channels-kc/ 「Basis = 20 Period EMA」
  - https://en.wikipedia.org/wiki/Keltner_channel
- **本地計算證據**：
  - s3 (d)：短期上下軌與標準 EMA20±2ATR10 的差：2454 −1.18%／−1.03%、6196 −0.57%／−1.37%、00893 −1.08%／−1.13%、2379 下軌 −1.13%
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：app 標為「Keltner短期/中期/長期」的三檔通道（短期預設開啟），實際是 SMA20／SMA60／SMA120 ± 2／2.5／3 × ATR14（Wilder）。StockCharts 的通行定義是 EMA20 ± 2×ATR(10)，預設 (20, 2.0, 10)；TradingView 支援頁寫 20 期 EMA ± 2×ATR，但沒寫 ATR 長度預設值；Keltner 1960 年原版是典型價 10 日 SMA ± 10 日平均高低差。中期、長期兩檔參數沒有外部依據。

以本地資料計算（本地資料在 2026-08 至 09 與官方 STOCK_DAY 逐日一致），2026-09-23 短期通道相對標準 EMA20±2ATR10 的差距如下：
- 2454：上軌 −1.18%、下軌 −1.03%
- 6196：上軌 −0.57%、下軌 −1.37%
- 00893：上軌 −1.08%、下軌 −1.13%
- 2379：上軌 −0.26%、下軌 −1.13%
- 0050、3034、3545、00878、00885：差距都在 0.7% 以內

差距會隨行情變化。最新日往前 180 個日曆天內，差距中位數約 0.5% 到 2.5%，波動大的個股最大可到 5% 至 8%（2454 上軌 7.98%、2379 下軌 5.78%）。差距主要來自中軌用 SMA 而不是 EMA，ATR 用 14 日而不是 10 日只佔小部分。
    - 理由：我自己寫了腳本重算，finding 引用的四組數字全部重現，連小數點後兩位都一樣。

一、定義部分
- 程式碼：短期 Keltner 是 ma20 ± 2×atr_14。ma20 是收盤價 20 日簡單均線（SMA），不是指數均線（EMA）。atr_14 是 14 日 Wilder 平滑，三檔都用它。中期是 MA60 ± 2.5×ATR14，長期是 MA120 ± 3×ATR14。前端標籤直接寫「Keltner短期/中期/長期」，而且「Keltner短期」預設開啟。
- StockCharts 線上核實：預設 (20, 2.0, 10)，也就是 EMA20 ± 2×ATR(10)；Keltner 1960 年原版是典型價的 10 日 SMA ± 10 日平均高低差。
- TradingView 支援頁核實：Basis = 20 期 EMA ± 2×ATR。但抓到的頁面內容沒寫 ATR 長度預設值，所以「TradingView 預設 ATR10」這點我沒能線上核實。
- Wikipedia 只說 Raschke 改成 EMA 加 ATR 倍數，沒寫具體參數，所以「Raschke 版＝EMA20±2ATR10」這個歸屬也沒能核實。
- 中期與長期兩檔參數找不到外部定義，只看得出是自訂。

二、數據重現
- 本地資料正確：9 檔自選股在 2026-08 至 09 共 38 個交易日，本地 parquet 的高、低、收與官方 STOCK_DAY 最大差都是 0。近 60 個交易日沒有還原權值造成的價差，raw_close 與 close 完全相同。
- app 算法重現：照 app 的算法，取最新日往前 300 天的 OHLCV，跑 compute_indicators，再取「今天往前 240 天」的視窗，呼叫 levels._keltner_short 與 _build_series。算出的 atr_14、ma20 和 parquet 裡存的值差距小於 1e-5。
- 標準值：用 EMA20（先以 SMA 起算，同 TradingView 做法）± 2×Wilder ATR10 在全部歷史上計算。
- 2026-09-23 的差距（app 相對標準值）與 finding 逐一相符：
  - 2454：上軌 −1.18%、下軌 −1.03%
  - 6196：上軌 −0.57%、下軌 −1.37%
  - 00893：上軌 −1.08%、下軌 −1.13%
  - 2379：下軌 −1.13%

三、需要補充的地方
- 「相差約 1%」只是其中幾檔在單一日期的描述。
  - 同一天 9 檔自選中，0050、3034、3545、00878 的差距都在 0.51% 以內。
  - 最新日往前 180 個日曆天內，差距中位數約 0.5% 到 2.5%，最大可到 8%（2454 上軌 7.98%、下軌 7.0%；2379 下軌 5.78%）。
  - 2454 有 92.7% 的交易日、6196 有 78% 的交易日，差距超過 1%。
- 差距主要來自均線用 SMA 而不是 EMA。以 2454 上軌為例：均線造成 −1.01%，ATR 長度造成 −0.17%。

嚴重度維持 P3：這是指標名稱和參數與通行定義不一致，不會造成資料破壞或錯誤的交易結果。
    - 證據：
      - backend/app/indicators/levels.py:356-368 — _keltner_short: ma20, 2.0；_keltner_mid: ma60, 2.5；_keltner_long: rolling_mean(120), 3.0；三檔共用 atr_14（levels.py:306）
      - backend/app/indicators/pipeline.py:396 — ma20 = close.rolling_mean(20)（SMA）；pipeline.py:482-484 — atr_14 = _tr.ewm_mean(alpha=1/14, adjust=False)（Wilder 14）
      - backend/app/api/stock_analysis.py:74-91 — 曲線序列同樣是 ma20±2ATR14、ma60±2.5ATR14、ma120±3ATR14
      - frontend/src/components/stock-analysis/AnalysisKChart.tsx:52-54,119 — 標籤「Keltner短期/中期/長期」，defaultLevelTypes 包含 keltner_s（預設開啟）
      - https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/keltner-channels 「Upper Channel Line: 20-day EMA + (2 x ATR(10))」、預設「(20,2.0,10)」；原版「a 10-day SMA of the typical price」，通道寬度為「the 10-day SMA of the High-Low range」
      - https://www.tradingview.com/support/solutions/43000502266-keltner-channels-kc/ 「Basis = 20 Period EMA」「Upper Envelope = 20 Period EMA + (2 X ATR)」；抓到的內容沒有 ATR 長度預設值（ATR10 未線上核實）
      - https://en.wikipedia.org/wiki/Keltner_channel 原版「a 10-day simple moving average of typical price」；Raschke 只提到改用 EMA／Wilder ATR 倍數，沒有具體參數
      - 指令：cd backend && uv run python ...\verify-kelt\s3_extra.py → 9 檔本地與官方 twse_stock_day_202608_202609.csv 各 38 日，高、低、收的最大絕對差都是 0.0
      - 指令：uv run python ...\verify-kelt\s2_repro.py（2026-09-23）app_up/std_up/dUp%，app_lo/std_lo/dLo%：2454 4983.72/5043.31/−1.18，4078.78/4121.30/−1.03；6196 559.31/562.49/−0.57，472.79/479.38/−1.37；00893 44.37/44.86/−1.08，41.68/42.16/−1.13；2379 779.40/781.39/−0.26，664.70/672.28/−1.13 — 與 finding 完全一致
      - 同一腳本其他 5 檔 dUp%/dLo%：0050 +0.07/−0.19；3034 +0.15/−0.14；3545 −0.11/−0.51；00878 −0.14/−0.48；00885 +0.68/+0.47；重算的 atr_14、ma20 與 parquet 存值差距小於 1e-5
      - s3_extra.py：最新日往前 180 日曆天（123 日）|差距| 的中位數與最大值：2454 上軌 1.73/7.98、下軌 2.48/7.00，超過 1% 的天數占 92.7%；2379 下軌最大 5.78；6196 下軌最大 4.68，超過 1% 的天數占 78%；0050 上軌最大 2.35
      - s3_extra.py 拆解（2026-09-23，以標準上軌為分母）：2454 均線部分 −1.01%、ATR 部分 −0.17%；00893 −1.07%／−0.01%；6196 −0.87%／+0.30%

### [P3 → P3] 布林帶使用樣本標準差（ddof=1），Bollinger 定義為母體標準差（確認成立）

- **主張**：pipeline 用 polars rolling_std（預設 ddof=1），價位模組直接沿用，上下軌比原定義略寬（約 sqrt(20/19)=1.026 倍）。此項可能與 BOLL 指標領域重疊。
- **正確的定義或數值**：MA20 ± 2 × 母體標準差。
- **app 實際行為**：MA20 ± 2 × 樣本標準差。
- **對使用者的影響**：布林上下軌偏寬約 0.1%～0.4%（相對價格），與多數看盤軟體略有差異。
- **修法建議**：rolling_std(20, ddof=0)，並同步檢查其他使用 _boll_std 的訊號。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:414（rolling_std(20)）
  - backend/app/indicators/levels.py:335-339
- **線上證據**：
  - https://en.wikipedia.org/wiki/Bollinger_Bands 「Since Bollinger Bands use the population method of calculating standard deviation, the proper divisor for the sigma calculation is n, not n − 1.」
- **本地計算證據**：
  - s3 (d2)：2454 上下軌 app 5260.40/3802.10，母體法 5241.94/3820.56；0050 112.38/104.45 對 112.28/104.55
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：布林帶的標準差用了樣本法（ddof=1），但 Bollinger 本人的定義是母體法（ddof=0）。

- 出處：pipeline.py:417 呼叫 polars rolling_std(20) 時沒有傳 ddof，polars 預設 ddof=1。levels.py:335-336 與 K 線序列都直接讀預先算好的 boll_upper／boll_lower。
- 回測也一樣：backtest/matrix.py:3895 明寫 ddof=1，所以回測的布林軌同樣是樣本法。
- 偏差大小：軌道半寬是定義值的 sqrt(20/19)=1.026 倍。9/23 的例子：2454 上下軌 5260.40／3802.10，母體法應為 5241.94／3820.56；0050 為 112.38／104.45，應為 112.28／104.55。
- 相對價格的偏寬幅度：自選股單邊 0.045%（00885）～0.36%（2454），全市場中位數約 0.11%、p90 約 0.29%。近 20 日約 1.1% 的「標的×交易日」，收盤價落在兩種軌道之間，這些情況的突破／跌破判定會隨口徑改變。
- 修正建議：pipeline 改成 rolling_std(20, ddof=0)，matrix.py:3895 同步改成 ddof=0，讓回測與即時結果口徑一致。
- 未查證：主流看盤軟體（TradingView 等）實際用哪種標準差，沒有查到。
    - 理由：這條 finding 成立，數值差異完全重現。我用證交所官方 STOCK_DAY 參考資料，取 2026-08-27～2026-09-23 共 20 個交易日的收盤價，自己重算 MA20 ± 2σ，並把樣本法（ddof=1）與母體法（ddof=0）並列：
- 2454：樣本法 5260.401453／3802.098547，母體法 5241.938926／3820.561074。finding 寫 app 5260.40／3802.10、母體法 5241.94／3820.56，一致。
- 0050：樣本法 112.377594／104.447406，母體法 112.277196／104.547804。finding 寫 112.38／104.45 對 112.28／104.55，一致。

另外做了三項交叉比對：
1. app 純函式 compute_indicators 吃官方 OHLCV 後，輸出的 boll_upper／boll_lower 與樣本法逐位相同。
2. 本地 kline_daily_enriched 在 9/23 存的值，也與我用它自己 20 天收盤重算的樣本法結果相同（10 檔 stored_eq_ddof1 全為真）。
3. 這 20 天的本地收盤與官方收盤最大差為 0。

根因確認：polars 1.40.1 的 rolling_std 預設 ddof=1，實測與 np.std(ddof=1) 相同，官方文件簽名也寫 ddof: int = 1。pipeline.py 第 417 行呼叫 rolling_std(20) 時沒有傳 ddof。每一檔的比值都是 1.025978，等於 sqrt(20/19)。線上來源方面，Wikipedia 與 John Bollinger 本人的官網都明說用母體法。

需要修正的細節有四項：
1. 程式碼行號：實際在 pipeline.py:417，不是 414。
2. 影響幅度：自選股在 9/23 單邊偏寬 0.045%（00885）～0.356%（2454），不是 0.1%～0.4%。全市場 1320 檔的中位數 0.112%、p90 0.295%。
3. finding 沒提到的新面向：回測矩陣 backtest/matrix.py:3895 也明寫 ddof=1。修正時只改 pipeline，會讓回測與即時結果不一致。
4. 「與多數看盤軟體有差異」這句沒有查證：TradingView 與 StockCharts 的說明頁都沒寫用哪種標準差。這句應標為推測。

實際影響：近 20 個交易日共 44062 個「標的×交易日」，其中 491 個（1.11%）的收盤價落在兩種軌道之間。這些情況下，突破／跌破布林軌的判定會因口徑不同而改變。9/23 的自選股沒有任何一檔落在兩軌之間。整體偏差幅度小，維持 P3。
    - 證據：
      - 程式碼：backend/app/indicators/pipeline.py:417 `pl.col("close").rolling_std(20).over("symbol").alias("_boll_std")`，沒有傳 ddof；上下軌在 :449 與 :451 計算（ma20 ± 2*_boll_std）。finding 寫 :414，實際行號是 417。
      - 程式碼：backend/app/indicators/levels.py:335-336 直接讀預先算好的 boll_upper／boll_lower，沒有重算；呼叫端是 backend/app/api/stock_analysis.py:130，資料來自 repo.get_daily_asset（kline_daily_enriched）。
      - 新面向：backend/app/backtest/matrix.py:3895 `deviation = valid_rolling_std(market.close, close_valid, 20, ddof=1)`，回測的布林軌也用樣本法。只改 pipeline 會讓回測與即時結果口徑不一致。
      - 本地實測（s2_recompute.py）：polars 1.40.1 `pl.Series([1,2,4,8]).rolling_std(4)` = 3.095696，等於 np.std ddof=1（3.095696），不等於 ddof=0（2.680951）
      - 官方資料重算（twse_stock_day_202608_202609.csv，20 個交易日 2026-08-27～2026-09-23）上軌／下軌，依序為樣本法 ddof=1、母體法 ddof=0：2454 5260.401453/3802.098547 對 5241.938926/3820.561074；0050 112.377594/104.447406 對 112.277196/104.547804；3034 561.857125/526.742875 對 561.412568/527.187432；2379 760.523367/683.576633 對 759.549200/684.550800；6196 551.837237/480.262763 對 550.931084/481.168916；3545 52.747170/47.942830 對 52.686346/48.003654；00878 35.505224/32.754776 對 35.470403/32.789597；00885 18.802073/18.146927 對 18.793779/18.155221；00893 45.399538/40.655462 對 45.339476/40.715524；每檔比值都是 1.025978（sqrt(20/19)）
      - 本地 enriched 儲存值：data\kline_daily_enriched\date=2026-09-23\part.parquet 中 2454 為 boll_upper 5260.401453、boll_lower 3802.098547；0050 為 112.377594、104.447406。與樣本法重算完全相同，10 檔 stored_eq_ddof1 全為真；這 20 天的本地收盤與官方收盤最大絕對差 0.0
      - app 純函式：compute_indicators(官方 OHLCV, needed={ma20,boll_upper,boll_lower}) 在 2026-09-23 的輸出，2454 是 5260.401453/3802.098547，0050 是 112.377594/104.447406，與樣本法相同
      - 影響幅度（單邊偏寬佔收盤價的百分比，9/23）：自選股 00885 0.045%、2330 0.075%、3034 0.082%、0050 0.089%、00878 0.099%、3545 0.119%、2379 0.128%、00893 0.130%、6196 0.165%、2454 0.356%；全市場 1320 檔 min 0.010%、p10 0.043%、中位數 0.112%、p90 0.295%
      - 訊號影響（s3_between.py）：最近 20 個交易日 44062 個「標的×交易日」中，有 491 個（1.11%）的收盤價落在母體軌與樣本軌之間；9/23 的自選股沒有一檔落在兩軌之間
      - 線上來源：https://en.wikipedia.org/wiki/Bollinger_Bands 原文 "Since Bollinger Bands use the population method of calculating standard deviation, the proper divisor for the sigma calculation is n, not n − 1."
      - 線上來源：https://www.bollingerbands.com/bollinger-bands（John Bollinger 官網）原文 "We use the population calculation for standard deviation"，並寫預設是 "20 periods for the moving average with the bands set at plus and minus two standard deviations"
      - 線上來源：https://docs.pola.rs/api/python/stable/reference/series/api/polars.Series.rolling_std.html 簽名為 `rolling_std(..., ddof: int = 1)`，說明寫 "The divisor for a length N window is N - ddof"
      - 未查證：TradingView（https://www.tradingview.com/support/solutions/43000501840-bollinger-bands-bb/）與 StockCharts ChartSchool 的說明頁都沒寫用母體法還是樣本法；Pine 參考手冊是前端動態頁，抓不到內容。「多數看盤軟體用母體法」屬推測。
      - 腳本與輸出：AUDIT_SCRATCH/work/verify-bollddof/s2_recompute.py、s2_output.txt、s3_between.py；執行前後 git status 相同，repo 沒有被寫入

### [P3 → P3] _aggregate_levels 註解說「保留最新」，實際保留價格最高者，且聚合會鏈式漂移超過 ±1%（確認成立）

- **主張**：函式先把 values 依價格排序，再用 out[-1]=v 覆蓋，所以保留的是群內價格最大值，而非最近期的點；而且每次都和更新後的代表值比較，連續相差 <1% 的點可以串成遠超 1% 的一群。
- **正確的定義或數值**：依 docstring 保留最近期的價位，群內跨度不超過 tol。
- **app 實際行為**：保留最高價，且跨度不受限。
- **對使用者的影響**：前低（支撐）聚合後偏向群內最高者，缺口中點的聚合也有相同偏差；影響小。
- **修法建議**：以時間順序保留代表值，並以群的起點（或平均）作為比較基準。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/levels.py:639-650
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 由程式碼直接推得：輸入 [100, 100.9, 101.8, 102.7]、tol=0.01，會合併成單一值 102.7（群內跨度 2.7%）
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：_aggregate_levels（levels.py:639-650）先把價位依大小排序，再用 out[-1]=v 覆寫，所以每一群留下的都是群內最高價，不是 docstring 和第 647 行註解說的「最新」價位。每次比較也都拿更新後的代表值，群內跨度因此不受 tol 限制：人工輸入 [100,100.9,101.8,102.7] 會合併成 102.7（跨度 2.7%）。用自選 9 檔的本地資料實測（2026-01-27～09-24，swing 價位已與官方 TWSE 日K核對一致）：41 個多成員群全部取最大值，21 群不是最新點，6 群跨度超過 1%（最大 1.325%）；和「以時間上最新點為代表」的版本相比，有 5 檔的前高／前低顯示值不同，例如 2379 最近的前高顯示 783，9/02 較新、較低的高點 774 被吸收而看不到。缺口中點的聚合（tol=0.005）邏輯上有同樣問題，但目前自選資料只出現一個跨度 0.1% 的群，沒有實際偏差。整體影響是 1.3% 以內的參考線偏移，屬 P3。
    - 理由：我直接 import 了 repo 的 `_aggregate_levels`／`_extreme_levels`／`_gap_levels`（都是純函式，不動任何檔案），先跑人工輸入，再用自選 9 檔的本地 parquet 實測，兩部分都重現了 finding。

一、程式碼：`levels.py:639-650` 先 `values = sorted(values)`，時間順序就此丟失；接著 `out[-1] = v` 會一路覆寫成群內較大的值。第 646 行每次都拿更新後的 `out[-1]` 當比較基準，所以會鏈式漂移。docstring 寫「保留最新」，第 647 行註解寫「聚合到最新(更近期)」，兩者都和實際行為不符。

二、人工輸入的重現結果和 finding 完全一致：
- `[100, 100.9, 101.8, 102.7]`、tol=0.01，得到 `[102.7]`，群內跨度 2.7%。
- 延長到 105.4，得到 `[105.4]`，跨度 5.4%。
- 依時間排列的前低 `[101.0(舊), 100.5(新)]` 得到 `[101.0]`：留下的是較高、較舊的那個，不是最新的 100.5。

三、實際資料：模擬 `/levels` API 的預設讀取範圍（days=120，也就是 2026-01-27～2026-09-24），從 kline_daily_enriched 讀 159 個分區。
- 9 檔共有 41 個由多個 swing 點組成的群。每一群的代表值都是群內最大值（41/41）。其中 21 群的代表值不是時間上最新的點；6 群跨度超過 1%，最大 1.325%（00893 前低：41.79、41.52、42.07）。
- 另外寫了一個符合 docstring 本意的替代版（以群起點為比較基準，保留時間上最新的點）。和它比，有 5 檔的前高／前低顯示值不同：
  - 2379 前高：現行 [783, 848]，替代版 [774, 783]。9/02 較新、較低的高點 774 被 7/27 的 783 吸收，現行版看不到。
  - 3545 前低：[50.5, 48.25] 對 [50.5, 49.85]。
  - 0050 前低：[105.85, 102.8] 對 [105.85, 102.4]。
  - 00885 前高：[18.73, 19.26] 對 [18.73, 19.06]。
  - 00893 前高：[48.1] 對 [47.7]。
- 用官方 TWSE STOCK_DAY 參考 CSV 核對了 14 個 8～9 月的 swing 價位，全部相符。所以上述差異來自演算法，不是資料錯誤。

四、缺口：9 檔中只有 00893 的向上缺口出現 1 個多成員群（43.8 與 43.845，跨度 0.103%）。這一群剛好同時是群內最大值和時間上最新的點，所以目前缺口顯示沒有實際偏差。finding 說缺口「也有相同偏差」，在程式邏輯上成立，但現況資料沒有觸發。

五、嚴重度：偏差最多約 1.3%，只影響前高／前低參考線與 AI 摘要的顯示；tests 目錄裡也沒有測試鎖定這個行為。維持 P3 合理。

六、需要修正的一處細節：finding 說鏈式漂移「遠超 1%」，這只在人工輸入（2.7%）上成立；自選 9 檔的實測跨度最大是 1.325%。
    - 證據：
      - backend/app/indicators/levels.py:643 `values = sorted(values)`（時間順序在這裡丟失）；:646 `abs(v - out[-1]) / out[-1] <= tol`（以更新後的代表值比較，造成鏈式漂移）；:647 `out[-1] = v  # 聚合到最新(更近期)`（實際覆寫成群內較大的值）
      - 呼叫端：levels.py:260、:267（前高／前低，tol=0.01）；levels.py:455（缺口中點，tol=0.005）；API 預設 days=120，讀取範圍為 today-240 天（backend/app/api/stock_analysis.py:106,120）
      - 腳本 AUDIT_SCRATCH/work/verify-agglvl/verify.py；輸出 out.txt 與 result.json（同一資料夾）；指令 `cd backend && uv run python verify.py`，EXIT=0
      - 人工輸入：_aggregate_levels([100,100.9,101.8,102.7],0.01) -> [102.7]，span 2.7%（與 finding 相同）；[100..105.4] -> [105.4]，span 5.4%；時間序前低 [101.0,100.5] -> [101.0]（最新點是 100.5）
      - 實測（2026-01-27～2026-09-24，159 分區，1430 列）：41 個多成員群全部取群內最大值；21 群的代表值不是時間上最新的點；6 群跨度 >1%，最大 1.325%（00893 前低 41.79/41.52/42.07 -> 42.07）
      - 2379.TWSE 前高群 [783(07-27), 780(08-11), 774(09-02)] -> 783，跨度 1.163%；顯示值現行 [783, 848]，docstring 本意版 [774, 783]；_extreme_levels 實際輸出 前高 783、848
      - 顯示值不同的另外 4 檔：3545 前低 [50.5,48.25] 對 [50.5,49.85]；0050 前低 [105.85,102.8] 對 [105.85,102.4]；00885 前高 [18.73,19.26] 對 [18.73,19.06]；00893 前高 [48.1] 對 [47.7]
      - 官方核對（reference/csv/twse_stock_day_202608_202609.csv）：2379 08-11 高 780、09-02 高 774；00885 08-05/08-13/08-27/09-07 高 18.6/18.55/18.73/19.06；0050 08-25 低 102.4；00893 08-25/09-02/09-15 低 41.79/41.52/42.07；3545、3034、6196 的檢查點，共 14 筆全部 match
      - 缺口：9 檔中只有 00893 向上缺口有 1 個多成員群 [43.8(08-13), 43.845(09-21)]，跨度 0.103%，代表值同時是最大值與最新點，目前沒有實際偏差
      - backend\tests 下 grep 'aggregate_levels|_extreme_levels|前高' 無命中，沒有測試鎖定此行為

### [P3 → P3] /levels 回傳的 series、dates、summary 沒有任何前端消費者，ATR series 仍命名為 stop_loss/take_profit（確認成立）

- **主張**：AnalysisKChart 只被 import LEVEL_GROUPS，沒有被渲染；PriceAlertDialog 只用 levels 與 close。每次請求仍計算 _build_series 與 summarize_levels，而 API 的 series 用止損／止盈命名，與 levels 的中性「ATR 上軌/下軌」不一致。
- **正確的定義或數值**：移除無人使用的輸出，或讓前端實際使用。
- **app 實際行為**：每次請求都計算並回傳無人使用的資料。
- **對使用者的影響**：無功能影響，只是維護負擔與多餘計算。
- **修法建議**：移除 series/dates/summary 或恢復圖表使用；統一 ATR 命名。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/api/stock_analysis.py:93-97、133-141
  - frontend/src/components/stock-analysis/PriceAlertDialog.tsx:6（只 import LEVEL_GROUPS）
  - backend/app/indicators/levels.py:595-613（summary 的 docstring 聲稱供 AI 提示詞使用）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - grep '<AnalysisKChart' frontend/src 無結果；compute_levels／summarize_levels 在 backend 只有 stock_analysis.py 使用
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：這條 finding 成立。/api/stock-analysis/levels 在前端只有 PriceAlertDialog 使用，而且只讀 levels 與 close。AnalysisKChart 只被 import 了 LEVEL_GROUPS 常數，沒有任何地方渲染，所以後端每次請求算出的 dates、series（_build_series）與 summary（summarize_levels）都沒有消費者；summarize_levels 的 docstring 說供 AI 提示詞使用，但 repo 內沒有對應呼叫。API 的 series.atr 用 stop_loss／take_profit 命名，而 levels.atr_stop 用中性的「ATR 上轨/下轨」，兩者數值相同（2454.TWSE 實測：4732.53／5637.47），只有命名不一致。補充量化：以 2454.TWSE、days=250 實測，回應 JSON 約 42 KB，其中約 89% 沒有消費者；額外運算約 1.6 ms，可以忽略，所以負擔主要在傳輸量與維護。另外，stock_analysis.py 開頭 module docstring 說 /levels 供「K 线关键价位」使用，這句也已過時。沒有功能或金融口徑影響，維持 P3。
    - 理由：這條 finding 講的是程式碼沒有被使用，不是數值錯誤，所以沒有官方數據可以比對。我改用兩種方式驗證：一是查程式碼的呼叫關係，二是拿本地 parquet 實際跑一次 /levels 的純計算函式，看回傳結構。三項宣稱都成立。(1) 整個前端只有 PriceAlertDialog.tsx:49 會打 /api/stock-analysis/levels（呼叫 api.stockAnalysisLevels(symbol, 250)）。這個元件只讀 levelsQuery.data?.close（第 74 行）和 levelsQuery.data?.levels（第 78、92 行），沒有讀 summary、dates、series。(2) AnalysisKChart 在 frontend/src 裡只有第 114 行的定義，以及 PriceAlertDialog.tsx:6 對 LEVEL_GROUPS 的 import，沒有任何地方渲染 \<AnalysisKChart>。也就是說，會用到 series/seriesDates 的元件是死碼。(3) backend 內 compute_levels、summarize_levels、_build_series 只有 stock_analysis.py 在用，repo 裡也沒有 AI 提示詞呼叫 summarize_levels。實跑 2454.TWSE（338 列，2025-05-12～2026-09-23，對應 days=250）確認 series.atr 的 key 是 ['stop_loss','take_profit']，而 levels.atr_stop 的標籤是中性的「ATR 上轨(+2)／ATR 下轨(-2)」，兩者數值相同（4732.53／5637.47），只有命名不一致。另外量到一個 finding 沒寫的數字：整份 JSON 42,259 bytes，前端用到的 levels 加 close 只有 4,541 bytes，約 89.3% 的回傳內容沒人讀。計算成本方面，_build_series 約 1.54 ms、summarize_levels 約 0.06 ms，可以忽略；多出來的負擔主要在傳輸量和維護，不在運算。還發現一處過時說明：stock_analysis.py 開頭的 module docstring（約第 9～10 行）寫 /levels 供「价格点位提醒 + K 线关键价位」使用，但 K 線圖其實沒有被渲染。整體沒有功能或金融口徑上的影響，維持 P3。
    - 證據：
      - frontend/src/components/stock-analysis/PriceAlertDialog.tsx:6 `import { LEVEL_GROUPS } from './AnalysisKChart'`；第 49 行 queryFn: () => api.stockAnalysisLevels(symbol, 250)；第 74 行只讀 levelsQuery.data?.close；第 78、92 行只讀 levelsQuery.data?.levels；全檔沒有讀 summary、series、dates
      - grep -rn "AnalysisKChart" frontend/src 只有兩筆：AnalysisKChart.tsx:114（export function 定義）與 PriceAlertDialog.tsx:6（只 import LEVEL_GROUPS），沒有任何 <AnalysisKChart 渲染
      - grep -rn "stockAnalysisLevels" frontend/src 只有 PriceAlertDialog.tsx:49 與 lib/api.ts:2788 的定義，/levels 只有這一個消費者
      - backend/app/api/stock_analysis.py:93-97 series 的 out["atr"] = {"stop_loss": close - 2*atr, "take_profit": close + 2*atr}；第 133-141 行每次請求都計算並回傳 dates、series、summary
      - backend/app/indicators/levels.py:51 LEVEL_TYPES 的 "atr_stop": "ATR波动通道"（註解寫「中性命名,非操作指令」）；第 397-400 行標籤為「ATR 上轨(+2)」「ATR 下轨(-2)」等；第 596 行 summarize_levels 的 docstring 寫「生成给 AI 提示词的价位摘要文本」
      - 排除 node_modules、.venv、.git、data 後，用 grep 搜 summarize_levels|compute_levels|_build_series|stock-analysis/levels，backend 只有 stock_analysis.py 在呼叫，沒有 AI 提示詞路徑使用 summary
      - 本地重現腳本 AUDIT_SCRATCH/work/verify-g4-dead-series/repro.py（uv run python repro.py 2454.TWSE）輸出：rows 338 first 2025-05-12 last 2026-09-23；series keys atr: ['stop_loss','take_profit']；levels atr_stop labels [('ATR 上轨(+2)',5637.47),('ATR 上轨(+1.5)',5524.35),('ATR 下轨(-1.5)',4845.65),('ATR 下轨(-2)',4732.53)]；series atr 末值 4732.53／5637.47，與 levels 數值一致，只是命名不同
      - 同一腳本量到：json bytes full=42259，used(levels+close)=4541，unused_share=89.3%；ms compute_levels=1.38、build_series=1.54、summarize=0.062（計算成本可忽略）
      - backend/app/api/stock_analysis.py 開頭 module docstring（約第 9-10 行）寫「/levels 端点本身供 PriceAlertDialog(价格点位提醒 + K 线关键价位)持续使用」，其中「K 线关键价位」已過時（K 線圖元件沒有被渲染）

### [P2 → P2] 籌碼分布的換手率衰減在台股永遠不生效，實際輸出約 338 個交易日的無衰減累積成交量分布（部分成立）

- **主張**：_support_resistance 聲稱用 CN109711994A 的換手率衰減模型，但台股歷史 turnover_rate 依設計全為 null，在 :115 被當成 0，decay_ratio 恆為 1。POC 與「成交密集區」因此退化成請求視窗內（days=250 → 500 個日曆日 → 333～338 個交易日）的純累積成交量分布；40 個分箱橫跨整段高低範圍，解析度也很粗。
- **正確的定義或數值**：依 docstring：各價位筹码逐日乘以 (1−換手率) 衰減，反映「現在誰還拿著」；或者，若只打算做 Volume Profile，就應明確標示期間，且不宣稱是籌碼分布。
- **app 實際行為**：無衰減，16 個月成交量累加；7/9 檔的 POC（標記 strong）落在現價 29%～73% 以下。
- **對使用者的影響**：/levels 回傳與 summary 中的「成交密集区(POC)」代表很久以前的成交堆積，不是目前的持倉成本。對話框只顯示最近的 6 個價位，所以 9 檔中目前只有 3545 會看到 sr 價位；但任何把 POC 當強支撐的解讀都會偏離。
- **修法建議**：台股沒有歷史換手率時，改用逐日以當時已發行股數推算換手率（或明確降級為固定期間的 Volume Profile，並在標籤註明期間），分箱範圍改以近期視窗計算。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/levels.py:115 t = float(turnovers[i] or 0)
  - backend/app/indicators/levels.py:92-101（分箱橫跨整個 df 的高低範圍）
  - backend/app/share_capital.py:46-47（Taiwan 非今日的 float_shares 一律為 None）
  - backend/app/taiwan/enriched_refresh.py:88（註解：compute_enriched nulls it for every Taiwan row that is not today）
  - backend/app/api/stock_analysis.py:120 start = end - timedelta(days=days * 2)
- **線上證據**：
  - https://patents.google.com/patent/CN109711994A/zh 「当天每一个价位上的筹码量＝前一交易日所述价位上的筹码量×(1-前一交易日的换手率)」（衰減是模型核心）
  - https://www.tradingview.com/support/solutions/43000502040-volume-profile/ POC=「The price level for the time period with the highest traded volume.」
- **本地計算證據**：
  - s1b.py：整個 kline_daily_enriched 共 1,423,597 列，turnover_rate 非 null 只有 1,087 列，全部在 2026-09-23
  - s4_sr_gap.py (1)：app 的 sr 與獨立的無衰減 VP 在 9/9 完全相同
  - 改用官方已發行股數（t187ap03_L／t187ap47_L）算換手率並套用衰減後，POC 變為：3034 386.93→545.67、2379 537.75→723.95、6196 278.44→512.33、0050 62.32→101.55、00885 16.29→18.95、00893 22.9→33.65（2454、00878 不變）
  - app 的 POC 相對現價：2454 −73.0%、00893 −50.4%、6196 −49.3%、0050 −44.6%、00878 −39.1%、2379 −29.3%、3034 −29.0%、00885 −10.8%、3545 +1.1%；2454 單一分箱寬 108.75 元
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：_support_resistance 的 docstring 宣稱是 CN109711994A 的換手率衰減籌碼分布，但台股歷史 turnover_rate 依設計（share_capital.py:46-47 對非今日台股列 fail-closed）全為 null，levels.py:115 把 null 當 0，所以衰減在台股實際上不生效。唯一的例外是最後一個交易日有當日換手率時會衰減一次，但那一步是同比例縮放，不改變排序。

結果是 sr 組退化成 /levels 請求視窗內（days=250 → 2025-05-12 起，333～338 個交易日）的無衰減 Volume Profile，40 個分箱橫跨整段高低範圍，期間沒有標示；9 檔的輸出與純 VP 完全相同，7 檔的 POC 在現價 29%～73% 以下。

使用者影響有限：唯一的消費端 PriceAlertDialog 上下各只顯示最近 6 個價位，不顯示 strength 與 summary；目前 9 檔中只有 3545 會出現 sr 價位，而且它在衰減與否之下落在同一區間。實際效果是「接近現價的籌碼價位缺席，加上靜默降級」，不是畫面上出現錯誤數值，嚴重度應為 P3。

修法應選「明確降級為固定期間的 Volume Profile，在標籤註明期間，並考慮縮短視窗或改用近期範圍分箱」。用現行已發行股數回填歷史來推算換手率，是 docs/taiwan-share-capital-phase-6h.md 明文拒絕的 look-ahead 做法，finding 據此算出的替代 POC 不能當正確值。

另外 0050 的視窗含 2025-05-12～06-10 未還原 1 拆 4 分割的價格（close 181～189），分箱範圍因此被撐大；這是 finding 沒提到、需另行記錄的資料問題。
    - 理由：核心機制成立，但使用者實際受到的影響比 finding 描述的小；finding 提出的替代 POC 數字與第一個修法，依據的都是 repo 設計文件明文拒絕的股本回填做法。所以判定為部分成立，嚴重度從 P2 降為 P3。

【成立的部分】
(1) 衰減程式碼。levels.py:115 會把 null 換手率當成 0，只有 decay_ratio<1 時才衰減（:119）。
(2) 資料實況。台股歷史列的換手率一律是 null：share_capital.py:46-47 對非今日的台股列採 fail-closed；本機 instruments 目錄是空的，compute_limit_signals 因而丟出例外，turnover_rate 保持儲存值。我自己重跑確認：kline_daily_enriched 共 1,423,597 列，turnover_rate 非 null 的只有 1,087 列，全部在 2026-09-23。
(3) 樣本視窗。前端 PriceAlertDialog.tsx:49 傳 days=250，stock_analysis.py:120 回推 500 個日曆日，起點是 2025-05-12，各檔實際有 333～338 列。
(4) 結果比對。app 的 sr 輸出與強制換手率為 0 的純 Volume Profile，9 檔全部相同。
(5) 數字核對。POC 相對現價的 9 個百分比、2454 分箱寬 108.75、替代 POC 數字，都與我重算的結果一致。
(6) 定義。專利 CN109711994A 的原文確認衰減是模型核心；Wikipedia Market profile 對 POC 的定義是「成交量峰值所在價位」。所以 app 輸出的是一份合法的 Volume Profile，但不是 docstring 宣稱的籌碼分布。

【需要修正的部分】
(a) decay_ratio 不是「恆為 1」。5 檔個股最後一列（9/23）有換手率，會衰減一次；但這一步對所有舊分箱同比例縮放，不改變排序。
(b) 替代 POC 數字與「逐日以當時已發行股數推算」的修法都有問題。替代數字是用現在的已發行股數回填整段歷史算出來的，而 docs/taiwan-share-capital-phase-6h.md 的「Rejected inference」一節明文拒絕「current issued or float shares backward-filled across history」，並說明台股沒有 PIT 歷史股本。所以那些數字不能當「正確值」，第一個修法目前也做不到。可行的只有第二個修法：降級並標示期間，或縮短視窗。
(c) 0050 的視窗含未還原分割的價格。2025-05-12～06-10 共 21 列 close 在 181～189，06-18 起變成 47.57；0050 分割是 1 拆 4。高價端被撐到 188.9，0050 的分箱與 POC 比較因此被汙染。這是 finding 沒提到的資料問題，另外記錄於此，不在本 finding 範圍內。

【使用者影響】
/levels 在前端唯一的使用者是 PriceAlertDialog。對話框只顯示 label、分組名稱「壓力支撐」與數值，不顯示 strength，也不顯示 summary；AI 個股分析的消費端已在 Phase 8B-5.6B 移除。我依對話框「上下各取最近 6 個」的邏輯模擬，9 檔中只有 3545 會顯示 sr 價位（51.86/52.66/51.08），而且它套用衰減後的結果（51.08/50.28/51.86）也在同一區間。其餘 8 檔遠低於現價的 POC 根本不會出現。

實際效果是：接近現價的籌碼價位缺席（例如 00885 衰減後的 18.95 不會出現），但畫面上沒有顯示錯誤的數值；「成交密集区(POC)」這個標籤對 Volume Profile 來說也名實相符。這比較接近「靜默降級、期間未標示、docstring 與行為不符」，屬於局部體驗與維護性問題，符合 CONTRIBUTING.md 第 11 節的 P3，不到 P2 的「明確功能錯誤」。
    - 證據：
      - backend/app/indicators/levels.py:115 `t = float(turnovers[i] or 0)`；:117 `decay_ratio = 1.0 - max(0.0, min(t, 100.0)) / 100.0`；:120 只有在 `if decay_ratio < 1.0:` 時才衰減；:90-101 分箱範圍取整個 df 的 high.max 到 low.min，共 40 格
      - backend/app/indicators/levels.py:62-86 docstring 宣稱依 CN109711994A 做換手率衰減籌碼分布，並寫明「反映现在谁还拿着」
      - backend/app/share_capital.py:46-47 對台股且非今日的列：`pl.when(taiwan & (trade_date != pl.lit(today))).then(None)`
      - backend/app/taiwan/enriched_refresh.py:88-89 註解：compute_enriched nulls it for every Taiwan row that is not today
      - frontend/src/components/stock-analysis/PriceAlertDialog.tsx:49 `api.stockAnalysisLevels(symbol, 250)`；:75-92 上方、下方各只取最近的 6 個價位；:294-296 只顯示 label、分組名稱與數值（不含 strength、summary）
      - backend/app/api/stock_analysis.py:120 `start = end - timedelta(days=days * 2)`（days=250 → 起點 2025-05-12）
      - backend/app/repository.py:1724-1731 compute_limit_signals 被 try 包住；data\instruments 目錄是空的，backend.log 有 102 筆 `on-demand compute failed: unable to find column "symbol"`
      - docs/taiwan-share-capital-phase-6h.md:86-91、95-96 歷史台股換手率必須有 PIT 股本，缺少時輸出 null；Rejected inference 明列「current issued or float shares backward-filled across history」
      - 本地重算（scratchpad\audit\work\V-G4-poc-def\repro.py，uv run python）：kline_daily_enriched 共 1,423,597 列，turnover_rate 非 null 1,087 列，全部在 2026-09-23；9 檔的 app sr 與強制換手率為 0 的結果 same_as_VP=True；列數 2454/3034/2379/6196/00878/00885/00893=338、3545=337、0050=333
      - 同一支 repro.py 的輸出：POC 相對現價 2454 −73.0%、3034 −29.0%、2379 −29.3%、6196 −49.3%、3545 +1.1%、0050 −44.6%、00878 −39.1%、00885 −10.8%、00893 −50.4%；2454 分箱寬 108.75；以現行已發行股數回填後的 POC：3034 545.67、2379 723.95、6196 512.33、0050 101.55、00885 18.95、00893 33.65，2454 與 00878 不變，3545 從 51.86 變 51.08（finding 沒列出 3545）
      - 本地模擬對話框取價位的邏輯（dialog.py）：9 檔中只有 3545 會顯示 sr（成交密集区(POC) 51.86、52.66、51.08），其餘 8 檔的 sr 顯示清單都是空的
      - 0050.TWSE 在 kline_daily_enriched：2025-05-12～2025-06-10 共 21 列 high>120（2025-06-10 close 188.65），2025-06-18 close 47.57，1 拆 4 分割沒有還原；0050 的分箱範圍 46.28～188.9
      - 官方參考資料 reference\csv\twse_stock_day_202608_202609.csv 的 2026-09-23 收盤：2454 5185.0、3034 545.0、2379 761.0、6196 549.0、3545 51.3、0050 112.45、00878 35.09、00885 18.27、00893 46.19，與 app 的 close 一致
      - https://patents.google.com/patent/CN109711994A/zh 原文：「当天每一个价位上的筹码量＝前一交易日所述价位上的筹码量×(1-前一交易日的换手率)」，衰減的目的為「保证总筹码量一定，即总股本固定」
      - https://en.wikipedia.org/wiki/Market_profile 原文：「the price of the peak cleared volume is identified as the Point of Control (POC)」；並指出 volume profile「is based purely on volume and price and does not take time into account at all」
      - https://wiki.mbalib.com/wiki/筹码分布 原文：「如果有些筹码中有人卖掉了，它们将从CYQ中拿掉，而重新堆到买方新建仓的价位上」（籌碼分布是會轉移的持倉分布，不是成交累加）
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：原主張全部重現。_support_resistance 宣稱採用 CN109711994A 的換手率衰減籌碼模型，但台股歷史 turnover_rate 全為 null：整個 kline_daily_enriched 1,423,597 列中，非 null 的只有 1,087 列，全部在 2026-09-23。因此實際輸出等同請求視窗內的無衰減累積成交量分布：days=250 對應 2025-05-12～2026-09-23，約 333～338 個交易日，40 個分箱橫跨整段最高與最低價。9 檔中有 7 檔標為 strong 的 POC 落在現價下方 29%～73%，改用官方已發行股數套用衰減後，其中 6 檔的 POC 會大幅移動，數值與 finding 完全一致。

兩點修正與補充：
1. 5 檔個股在最新官方交易日（9/23）的 turnover 不是 null，會衰減一次，所以「decay_ratio 恆為 1」不完全精確。但這次衰減對所有舊分箱乘上同一比例，不影響輸出。
2. 0050 另受 2025-06-18 的 1 拆 4 分割未還原影響：分箱範圍被分割前的 188.9 撐大，分箱寬度 3.57，分割前的成交量也是舊單位。這一部分光修衰減模型無法解決，修復時要一併處理價格還原，或把分箱範圍改成只用近期視窗。
    - 理由：我用本地 parquet 和官方參考資料，從頭重算了這條 finding，所有數值都能重現，完全一致。

1. 資料面：整個 kline_daily_enriched 共 1,423,597 列，turnover_rate 非 null 的只有 1,087 列，而且全部落在 2026-09-23。
2. 取數路徑：前端 PriceAlertDialog.tsx:49 呼叫時帶 days=250。後端 stock_analysis.py:120 把起日設為 today−500 天，也就是 2025-05-12。enriched 歷史快取只涵蓋 latest−300 天（repository.py:569），不夠用，所以實際走 _scan_daily_symbol 加 _compute_enriched_range。我照這條路徑重跑（filter_halt_days → compute_indicators → compute_signals → 裁到起日 → compute_levels），得到的交易日數是：2454、3034、2379、6196、00878、00885、00893 各 338 天，3545 為 337 天，0050 為 333 天。
3. 衰減是否生效：我對每檔各跑三種輸入：保留 parquet 原本的 turnover、把 turnover 全設為 null、直接刪掉 turnover 欄。三種情況下 _support_resistance 的輸出，9 檔全部相同。我另外寫了一個無衰減的 Volume Profile 做對照：8 檔 POC 完全相同；3545 算出 51.87，app 是 51.86，是同一個分箱中點（41.2+13.5×0.79=51.865）四捨五入造成的差異。
4. 改用官方股數：以 t187ap03_L／t187ap47_L 的已發行股數算出換手率並套用衰減後，6 檔的 POC 變化和 finding 完全一致；2454、00878 維持不變。
5. 相對現價：POC 相對現價的百分比，以及 2454 的分箱寬度 108.75，都與 finding 一致。
6. 使用者影響：我照對話框的邏輯模擬（依距離排序、以 toFixed(2) 去重、上下各取 6 個）。結果 9 檔裡只有 3545 會顯示 sr 價位，與 finding 相符。
7. 線上來源：CN109711994A 的衰減公式與 TradingView 對 POC 的定義，都經我親自抓取網頁確認。

有兩處小修正，不影響結論：
- 「decay_ratio 恆為 1」不完全精確。5 檔個股在 2026-09-23 那一天的 turnover 不是 null，所以會衰減一次。但這一次衰減是把之前所有分箱乘上同一個比例，對輸出沒有影響（已實測）。
- 0050 另有 finding 沒提到的面向：kline_daily_enriched 裡的 0050 價格沒有做 1 拆 4 分割還原（2025-06-10 收盤 188.65，2025-06-18 變成 47.57）。分箱的最高價 188.9 來自分割前，所以分箱寬度被撐到 3.57。這個扭曲光修衰減模型不會消失。

嚴重度維持 P2。這是明確的功能錯誤：docstring 宣稱用換手率衰減的籌碼模型，實際輸出卻是無衰減的累積成交量分布。但目前在對話框裡只有 3545 看得到 sr 價位，所以不升到 P1。
    - 證據：
      - backend/app/indicators/levels.py:105 `turnovers = df["turnover_rate"].to_list() if has_turnover else [0.0] * n`；:115 `t = float(turnovers[i] or 0)`；:117 `decay_ratio = 1.0 - max(0.0, min(t, 100.0)) / 100.0`；:92-101：分箱範圍取自整個 df 的最高價與最低價，40 個分箱
      - frontend/src/components/stock-analysis/PriceAlertDialog.tsx:49 `queryFn: () => api.stockAnalysisLevels(symbol, 250)`；backend/app/api/stock_analysis.py:120 `start = end - timedelta(days=days * 2)` → 2025-05-12
      - backend/app/repository.py:569 `start_full = latest - timedelta(days=300)`（歷史快取不夠涵蓋 2025-05-12）→ :1466-1472 改走 _scan_daily_symbol ＋ _compute_enriched_range；backend/app/share_capital.py:39-51：台股非今日的 float_shares 一律設為 None
      - 本地計算（verify.py，指令：cd backend && uv run python ...\work\verify-g4sr\verify.py）：TABLE rows=1423597 turnover_nonnull=1087 nonnull_dates=['2026-09-23']，與 finding 所說的 1,423,597／1,087 一致
      - verify.py：9 檔的 sr_parquet == sr_nulled == sr_dropped（衰減對輸出沒有作用）；獨立無衰減 VP 的 POC 有 8 檔完全相同，3545 為 51.87 對 51.86，是同一分箱的四捨五入差異
      - verify.py，POC 並列比較（finding → 我的重算）：3034 386.93→545.67 ／ 386.93→545.67；2379 537.75→723.95 ／ 537.75→723.95；6196 278.44→512.33 ／ 278.44→512.33；0050 62.32→101.55 ／ 62.32→101.55；00885 16.29→18.95 ／ 16.29→18.95；00893 22.9→33.65 ／ 22.9→33.65；2454 1401.88 不變、00878 21.37 不變，兩邊一致
      - verify.py，POC 相對現價：2454 −73.0%、00893 −50.4%、6196 −49.3%、0050 −44.6%、00878 −39.1%、2379 −29.3%、3034 −29.0%、00885 −10.8%、3545 +1.1%；2454 分箱寬度 108.75（範圍 1130～5480）。與 finding 完全一致
      - verify2.py 照 API 路徑重跑（filter_halt_days → compute_indicators → compute_signals → compute_levels）：交易日數 338／338／338／338／337／333／338／338／338，sr 輸出與上面相同
      - verify3.py 模擬對話框的最近 6 價位：只有 3545 會顯示 sr（51.86 POC、52.66、51.08），其他 8 檔都沒有 sr 價位
      - 官方參考資料 twse_stock_day 2026-09-23：9 檔的收盤價與 parquet 相同（例如 2454 5185.0、0050 112.45）；成交量方面，2454 官方 6,430,491、parquet 6,425,491，相差 5,000 股，其餘 8 檔相同
      - https://patents.google.com/patent/CN109711994A/zh（大智慧，主力筹码追踪的计算方法和系统）原文：「当天每一个价位上的筹码量＝前一交易日所述价位上的筹码量×(1-前一交易日的换手率)」
      - https://www.tradingview.com/support/solutions/43000502040-volume-profile/ 原文：POC = "The price level for the time period with the highest traded volume."
      - 新面向（verify2.py）：0050.TWSE 在 parquet 中未做分割還原：2025-06-10 收盤 188.65（最高 188.9），2025-06-18 收盤 47.57，成交量從 3.1e7 跳到 2.5e8。https://zh.wikipedia.org/zh-tw/元大台灣卓越50證券投資信託基金：「每一受益權單位分割為四個新單位」（1拆4）

### [P2 → P2] 台股未還原價跨 0050 的 1 拆 4 分割，前高與籌碼分布混入分割前價格（2026-06 前 250 日新高也受影響）（部分成立）

- **主張**：台股 provider 不提供還原因子，enriched 的 close 等於 raw_close（與 CONTRIBUTING 3.2「enriched OHLC 為前復權」相反）。0050 於 2025-06-18 分割後恢復交易，價格由約 188 變為約 47。levels 的 swing 前高、籌碼分箱都使用整個 500 日曆日視窗，所以混入分割前價格。
- **正確的定義或數值**：價位計算使用經分割（及除權息）調整的連續價格，或至少在分割日切斷視窗。
- **app 實際行為**：分割前的原始價格直接參與前高、籌碼分箱，以及過去一年的 250 日極值、缺口與斐波那契。
- **對使用者的影響**：0050 的關鍵價位回傳與 AI summary 中出現不存在的壓力位（高於現價 63%～68%）；2025-06～2026-06 期間的 250 日新高、Fib 也失真。對話框只顯示最近 6 個價位，所以遠離現價的錯值通常不會出現在清單中。
- **修法建議**：levels 改以還原價計算（用 TWT49U／分割事件建立還原因子），或偵測分割缺口後只用分割後資料；同時更正 CONTRIBUTING 3.2 對台股的口徑描述。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/providers/hybrid_provider.py:31 adj_factor=False；:151「Phase 2 records raw price semantics」
  - CONTRIBUTING.md:100「enriched 的 open/high/low/close 為前復權價格」
  - backend/app/indicators/levels.py:247-272（swing 使用整個 df）
  - backend/app/indicators/levels.py:92-101（分箱使用整個 df 的高低範圍）
- **線上證據**：
  - https://zh.wikipedia.org/zh-tw/元大台灣卓越50證券投資信託基金 「該次拆股以「每一受益權單位分割為四個新單位」（1拆4）方式進行，拆股後總資產不變，單位淨值按比例調整。」
- **本地計算證據**：
  - s1b.py：0050 2025-06-10 收 188.65，06-11～06-17 無資料，06-18 開 47.5、收 47.57；9 檔所有列的 close==raw_close（close_ne_raw=0）
  - s2：0050 目前的 extreme 含「前高 183.85」「前高 188.9」（現價 112.45）
  - s4：0050 全視窗 sr 為 [62.32, 101.55, 105.11]，分箱寬 3.566、hi=188.9；只用分割後資料則為 [62.3, 106.16, 72.42]
  - s6：as-of 2026-06-01 時「250日新高 188.9」、前高 168.7/185.5；as-of 2025-07-15（現價 50）時缺口出現 159.1／158.2／203.8，Fib 為 83.97／107.29
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：台股 provider 不提供還原因子（hybrid_provider.py:31、:151），所以 enriched 的 OHLC 等於未還原價，與 CONTRIBUTING.md:100 的前復權口徑不符。0050 於 2025-06-18 以 1 拆 4 恢復交易，而 /api/stock-analysis/levels 由 PriceAlertDialog 以 days=250（500 日曆日）呼叫，因此目前的 swing 前高（183.85、188.9）與籌碼分箱仍混入分割前價格。以還原價重算，sr 應為 [62.17, 105.88, 72.66]，而不是 [62.32, 101.55, 105.11]，兩個前高也不該存在。2025-06-18 起的一年內，250 日新高、缺口與 Fib 同樣失真；分割後數週的布林、Keltner、ATR 也受影響。不過，summary 並不含這些遠端前高，也沒有任何消費者；對話框只列最近 6 個價位，目前看不到受汙染的值；0050 的汙染會在 2026-10-23 之後離開視窗。因此就關鍵價位而言，這是 API 輸出錯誤、使用者看不到，嚴重度 P3。建議把「台股未還原、與契約不符」這個根因，拿到指標、回測層面另外評估。
    - 理由：核心主張成立。台股 provider 不提供還原因子，enriched 的 close 就是未還原價，這與 CONTRIBUTING 3.2 寫的口徑相反。0050 在 2025-06-18 以 1 拆 4 恢復交易，而 levels 的視窗是 500 個日曆日，所以分割前價格仍混在 swing 前高與籌碼分箱裡。

我讀到的程式碼如下：
- hybrid_provider.py:31 設 adj_factor=False，:144-152 的 get_adj_factors 回傳空表。
- data/adj_factor 與 data/adj_factor_etf 都是空目錄。
- repository._compute_enriched_range（repository.py:1713-1733）只算指標，沒有做還原。
- 前端 PriceAlertDialog.tsx:49 以 days=250 呼叫，stock_analysis.py:120 取 start=today-500 天，所以視窗從 2025-05-12 起算，含 21 筆分割前資料。
- instruments_etf 目錄是空的，所以 0050 走 stock 路徑（kline_daily_enriched），實際確有資料。

本地重算的結果：
- 目前 extreme 群組確實有「前高 183.85」「前高 188.9」，現價 112.45。
- 以 1/4 還原價重算後，這兩個前高消失；sr 由 [62.32, 101.55, 105.11] 變成 [62.17, 105.88, 72.66]。
- as-of 2026-06-01 時，250日新高=188.9。250 列尾端到 2026-06-26 才不再含分割前資料。
- as-of 2025-07-15 時，Fib 為 83.97/107.29，缺口有 159.1/158.2/203.8，與 finding 一致。

線上來源：Yahoo Finance 的 0050.TW 經分割調整歷史價顯示 2025-06-10 收 47.16、高 47.22，正好等於本地原始價 188.65 與 188.9 各除以 4，而且 6/11～6/17 沒有交易。Wikipedia 的 Stock split 條目也說明標準做法是把分割前價格除以分割比例。

finding 錯誤或誇大的部分：
(1) 「AI summary 中出現高於現價 63%～68% 的壓力位」不成立。summarize_levels（levels.py:595-615）每組只取離現價最近的 2 個值，目前 extreme 那一項是「60日新高=113.75、250日新高=113.75」，不含 183.85/188.9。而且 summary 沒有任何消費者：前端只有 api.ts 的型別定義用到它，AI 長篇分析 workflow 也已移除（stock_analysis.py:8-11）。
(2) 使用者實際看得到的影響，比 P2 小。對話框每側只顯示最近 6 個價位（PriceAlertDialog.tsx:89-90），我模擬 2026-09-23 的清單，沒有任何受汙染的值。
(3) 這個汙染會自然消失：2025-06-10 這筆資料在 2026-10-23 之後就離開 500 日視窗。

finding 沒提到的面向：分割後幾週，布林、Keltner、ATR 也被原始價汙染。as-of 2025-06-20 時「布林下軌 62.8」高於現價 47.03，曾出現在對話框「上方」前 6 名。這已是歷史情況，目前不會再發生。

嚴重度：計算輸出確實是錯的，但唯一的消費端會把它濾掉，影響在一個月內消失，而且 summary 的主張不成立，所以就 levels 這條而言降為 P3。

推測（未驗證）：「台股完全沒有還原，但契約寫前復權」這個根因，對指標、訊號、回測在除權息與分割附近的影響，可能值得在其他領域另外評估嚴重度。
    - 證據：
      - backend/app/taiwan/providers/hybrid_provider.py:31 `adj_factor=False,`；:151 "Return empty adjustment factors (Phase 2 records raw price semantics)."
      - CONTRIBUTING.md:100 「enriched 的 `open/high/low/close` 為前復權價格。」
      - data/adj_factor 與 adj_factor_etf 為空目錄（ls -la 只有 . 與 ..）；repository.py:1713-1733 `_compute_enriched_range` 只呼叫 compute_indicators/compute_signals/compute_limit_signals，不套還原
      - frontend/src/components/stock-analysis/PriceAlertDialog.tsx:49 `api.stockAnalysisLevels(symbol, 250)`；backend\app\api\stock_analysis.py:120 `start = end - timedelta(days=days * 2)` → 視窗 2025-05-12～今日
      - levels.py:92/100 分箱 hi=整個 df 的 high.max、step=(hi-lo)/40；levels.py:247-272 swing 用整個 df；levels.py:233 60/250 日極值用 tail(n)
      - 本地 v1.py（uv run python ...\work\verify-G4-split\v1.py）：0050 2025-06-10 O184.9 H188.9 L184.9 C188.65，06-18 O47.5 C47.57；close!=raw_close 列數=0；days=250 視窗 333 列、其中分割前 21 列；extreme 含 ('前高',183.85)、('前高',188.9)；sr=[62.32,101.55,105.11]
      - 本地 v3.py：以分割前價格/4、成交量×4 還原後，sr=[62.17,105.88,72.66]，extreme 的兩個前高消失（其餘相同）
      - 本地 v2.py：as-of 2026-06-01 的 250日新高=188.9、前高 168.7/185.5；as-of 2025-07-15 的缺口有 159.1/158.2/203.8、Fib 83.97/107.29；250 列尾端到 2026-06-26 才不含分割前資料；as-of 2026-09-23 的對話框模擬清單上方 [112.63,112.97,113.48,113.75,113.82,115.13]、下方 [112.38,112.12,111.99,111.78,111.65,111.27]，都不含受汙染的值；as-of 2025-06-20 時「布林下軌 62.8」（現價 47.03）列在對話框上方
      - levels.py:595-615 summarize_levels 每組只取距現價最近的 2 個；v1 目前 summary 的 extreme 項為「60日新高=113.75、250日新高=113.75」，不含 183.85/188.9；前端 grep `.summary` 只在 frontend\src\lib\api.ts:107 型別定義出現，沒有消費者
      - 官方參考 csv/twse_stock_day_202608_202609.csv：0050 2026-09-23 收 112.45，與本地一致
      - https://finance.yahoo.com/quote/0050.TW/history/ ：Jun 10, 2025 Open 46.22、High 47.22、Close 47.16（='Close price adjusted for splits'），Jun 18, 2025 Close 47.57；6/11～6/17 無資料。等於本地原始價 184.9/188.9/188.65 ÷4
      - https://en.wikipedia.org/wiki/Stock_split ："many charts use what is known as an adjusted share price; that is, they divide all closing prices before the split by the split ratio."
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：台股 provider 不提供還原因子（hybrid_provider.py:31 adj_factor=False），enriched 的 close 等於 raw_close，與 CONTRIBUTING.md:100 所寫的前復權口徑相反。0050 在 2025-06-18 以 1 拆 4 恢復交易，價格由 188.65 變為 47.57。PriceAlertDialog 用 days=250 呼叫 /levels，所以視窗是 500 日曆日，而 swing 前高與籌碼分箱都用整個視窗。

目前（as-of 2026-09-24）API 的 levels.extreme 回傳「前高 183.85／188.9」，比現價 112.45 高 63.5%～68.0%。sr 分箱寬度因此由約 1.75 變成 3.57，成交密集區變為 [62.32, 101.55, 105.11]，還原版是 [62.17, 105.88, 72.66]。

歷史上受影響的範圍：
- 250 日新高、新低：到 2026-06-25 為止都受影響。
- Fib 與缺口（120 日視窗）：只到 2025-12-05 為止受影響。as-of 2026-06-01 時 Fib 沒有受影響。

使用者影響：
- 對話框只列最接近現價的 6 個價位，遠離現價的錯值不會出現。但分割後 312 個交易日中有 95 天，對話框的成交密集區或極值與還原版計算略有不同（最後一次 2026-09-16），偏移約 1% 以內。
- summary 只取每組最近的 2 個，不含 183.85／188.9（受影響的是壓力支撐組的 101.55），而且 summary 沒有任何前端或 AI 消費者。
- 500 日視窗在 2026-10-24 起不再包含分割前資料，0050 會自行恢復正常。
    - 理由：我用本地 parquet 和專案裡的純計算函式（filter_halt_days、compute_indicators、compute_levels、summarize_levels）照 API 的路徑重算。finding 的核心數據與程式碼判斷都重現了，而且數字一致；但使用者影響有三處寫錯或不精確，所以判為部分確認。

一、已確認的部分（逐項與 finding 並列比較）
1. 資料未還原：9 檔自選共 5943 列，close≠raw_close、high≠raw_high、low≠raw_low 的列數都是 0。0050 在 2025-06-10 收 188.65，2025-06-11～06-17 沒有資料，2025-06-18 開 47.5、收 47.57，與 finding 的 s1b 完全相同。9 檔自選中，單日漲跌超過 20% 的只有 0050 在 2025-06-18 這一天，漲跌幅 -74.8%。2026-09-23 本地收盤 112.45，與官方參考 CSV（twse_stock_day 0050）的 112.45 一致。
2. 程式碼：hybrid_provider.py:31 為 adj_factor=False；:151 為 "Phase 2 records raw price semantics"；CONTRIBUTING.md:100 寫「enriched 的 open/high/low/close 為前復權價格」。PriceAlertDialog.tsx:49 以 days=250 呼叫，stock_analysis.py:120-121 的 start=today-500 天（2025-05-12）。levels.py:247-257 的 swing 以整個 df 迴圈計算；levels.py:92-101 的分箱用整個 df 的最高、最低價。
3. 目前（as-of 2026-09-24）重算結果：extreme 含「前高=183.85」（出自 2025-05-21 的 high）與「前高=188.9」（出自 2025-06-10 的 high）。finding s2 同為 183.85／188.9，比現價 112.45 高 63.5% 與 68.0%，與 finding 所說的 63%～68% 相符。
4. 籌碼分箱：全視窗的 sr 為 [62.32, 101.55, 105.11]，hi=188.9，分箱寬 3.5655。只用分割後資料時為 [62.3, 106.16, 72.42]。以上都與 finding s4 相同。我另外把分割前價格除以 4、成交量乘以 4 做成還原版，得到 [62.17, 105.88, 72.66]，分箱寬 1.7484。還原版中，前高 183.85／188.9 都消失了。
5. as-of 2026-06-01：250日新高 188.9，前高 168.7／185.5，與 finding s6 相同。還原版的 250日新高則是 106.7。
6. as-of 2025-07-15（現價 50）：缺口含 159.1／158.2／203.8；Fib 為 83.97／107.29／126.14／144.99／171.82，與 finding s6 相同。還原版的 Fib 為 39.85～48.24。
7. 線上佐證：以 WebFetch 讀維基百科，原文為「每一受益權單位分割為四個新單位」（1拆4）。本地價格比 188.65/47.57=3.97，與 1:4 一致。

二、需要更正的部分
(a)「AI summary 中出現不存在的壓力位（高於現價 63%～68%）」不成立。
   - summarize_levels（levels.py:603-612）每組只取最接近現價的 2 個。重算後，前高前低組的摘要是「60日新高=113.75、250日新高=113.75」，不含 183.85／188.9。
   - summary 只在 stock_analysis.py:138 產生並回傳；前端 PriceAlertDialog 沒有讀取它，也沒有任何 AI 流程取用。stock_analysis.py:8-11 已註明 AI 報告流程移除。
   - summary 真正受影響的是「壓力支撐」組：它列出 105.11、101.55，還原版則是 105.88、72.66。
(b)「2025-06～2026-06 期間 250日新高、Fib 也失真」不精確。
   - tail(250) 含分割前資料的最後一個 as-of 日是 2026-06-25，所以 250日新高到 2026-06-25 為止都失真，這部分成立。
   - Fib 與缺口只看 tail(120)，最後受影響的 as-of 日是 2025-12-05。as-of 2026-06-01 時，原始與還原版的 Fib 完全相同（96.01／89.4／84.05／78.7／71.09）。
   - 另外 finding 沒提到：250日新低也失真。as-of 2026-06-01 原始為 46.28，還原版為 43.81。
(c)「對話框的錯值通常不會出現」：遠離現價的錯值確實不會出現；as-of 2026-09-24 時，原始與還原版的對話框上方 6 個、下方 6 個價位完全相同。但分箱寬度加倍，會讓接近現價的成交密集區數值偏移。逐日回算分割後 312 個交易日，有 95 天對話框列出的 sr/extreme/gap/fib 價位不在還原版清單裡，最後一次是 2026-09-16（成交密集區 105.11）。偏移幅度小，例如 105.11 對 105.88 約差 0.7%。
(d) 時效：500 日曆日視窗到 today=2026-10-23 為止仍包含 2025-06-10，2026-10-24 起 0050 會自行恢復正常。目前 9 檔自選中只有 0050 受影響。

另外的新面向（只有觀察，未深入）：分割日的真實波幅也汙染了 atr_14。2025-06-18 對話框出現「ATR 上轨(+1.5)=66.87」，當時現價只有 47.57；還原版沒有這個值。這屬於 ATR/布林，不在這條 finding 的範圍內。

嚴重度：API 對自選標的回傳比現價高 63%～68% 的前高，屬於明確的功能錯誤，維持 P2。但對話框的可見影響小，而且會在 2026-10-24 自行消失。summary 沒有消費者，所以對 AI 沒有影響。
    - 證據：
      - 本地計算 v1_data.py（AUDIT_SCRATCH/work/verify-g4split/v1_data.py）：9 檔自選共 5943 列，close/high/low 與 raw_* 不相等的列數都是 0；0050 在 2025-06-10 收 188.65，2025-06-18 開 47.5、收 47.57，中間沒有資料；單日漲跌超過 20% 的只有 0050 在 2025-06-18（-0.748）
      - 官方參考 csv/twse_stock_day_202608_202609.csv：0050 在 2026-09-23 收 112.45，與本地相同
      - v2_levels.py as-of 2026-09-24：全視窗 sr=[62.32, 101.55, 105.11]，hi=188.9，分箱寬 3.5655（finding：3.566，相同）；只用分割後資料 sr=[62.3, 106.16, 72.42]（相同）；還原版（分割前 /4）sr=[62.17, 105.88, 72.66]
      - v2_levels.py as-of 2026-09-24：extreme 含 前高=183.85（2025-05-21 的 high）、前高=188.9（2025-06-10 的 high）；還原版沒有這兩個值
      - v2_levels.py as-of 2026-06-01：原始的 250日新高=188.9，前高 168.7/185.5；還原版的 250日新高=106.7；兩者的 Fib 同為 96.01/89.4/84.05/78.7/71.09（未受影響）；250日新低 原始 46.28，還原版 43.81
      - v2_levels.py as-of 2025-07-15：原始缺口含 159.1/158.2/203.8，Fib 為 83.97/107.29/126.14/144.99/171.82；還原版 Fib 為 39.85～48.24
      - v2_levels.py：tail(250) 含分割前資料的最後 as-of 日是 2026-06-25；tail(120)（Fib/缺口）是 2025-12-05；500 日曆日視窗在 today=2026-10-23 前都包含 2025-06-10
      - v2_levels.py summary（as-of 2026-09-24）：前高前低組為『60日新高=113.75、250日新高=113.75』，不含 183.85/188.9；壓力支撐組為『成交密集区=105.11、成交密集区=101.55』
      - v4_dialog_scope.py：分割後 312 個交易日中，有 95 天對話框的 sr/extreme/gap/fib 價位不在還原版清單中（最後一次 2026-09-16，成交密集区 105.11）；as-of 2026-09-24 原始與還原版的對話框完全相同
      - backend/app/taiwan/providers/hybrid_provider.py:31 adj_factor=False；:151 'Phase 2 records raw price semantics'
      - CONTRIBUTING.md:100 『enriched 的 `open/high/low/close` 為前復權價格。』
      - frontend/src/components/stock-analysis/PriceAlertDialog.tsx:49 api.stockAnalysisLevels(symbol, 250)；:89-90 上下各取 slice(0, 6)
      - backend/app/api/stock_analysis.py:120-121 start = end - timedelta(days=days*2)；:138 summary 只在此回傳；:8-11 註明 AI 報告流程已移除；前端沒有讀取 levels summary 的程式
      - backend/app/indicators/levels.py:92-101（分箱用整個 df 的最高、最低價）；:247-257（swing 用整個 df）；:603-612（summary 每組只取最近的 2 個）
      - https://zh.wikipedia.org/zh-tw/元大台灣卓越50證券投資信託基金 原文：『該次拆股以「每一受益權單位分割為四個新單位」（1拆4）方式進行，拆股後總資產不變，單位淨值按比例調整。』停止交易與恢復交易的日期未能從線上來源證實（WebSearch 額度已用完，Bing 結果頁沒有相關片段），只依本地資料的 06-11～06-17 空窗判斷

### [P3 → P3] 價位不依台股升降單位取整；整數關口步長沿用 A 股設計，00878 沒有任何整數關口（部分成立）

- **主張**：所有價位都以 round(…,2) 輸出，多數不在台股的報價格點上。整數關口步長（<20 取 1、<100 取 5、≥500 取 50）不參考升降單位，也不參考 repo 內已有的 TWSE Rule 62 模型。
- **正確的定義或數值**：顯示與儲存的價位貼齊升降單位；整數關口步長至少涵蓋整數元（例如 00878 的 34／36）。
- **app 實際行為**：價位含無法成交的小數；部分標的沒有整數關口。
- **對使用者的影響**：提醒以 close >= / <= 觸發，功能上等同進位到下一個格點，但顯示值會誤導；心理關口清單偏稀（步長屬品味判斷，見 unverifiable）。
- **修法建議**：輸出前用 market_rules 的 round_to_valid_tick 依方向取整；整數關口步長改以升降單位的倍數為基礎。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/levels.py:533-542、552、562
  - backend/app/taiwan/market_rules.py:185-207（已有 get_tick_size，但未被使用）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 官方 MIS 五檔（reference/csv/mis_snapshot.csv）：2454 買價 5180/5175/5170（跳 5）、6196 548/547（跳 1）、0050 112.45/112.40（ETF ≥50 跳 0.05）、00878 35.09/35.08（跳 0.01）
  - s4 (4)：不在格點上的價位：2454 30/44、3034 30/40、0050 25/41、3545 21/39
  - 例：2454 預設目標價 5196.67（實際只能成交在 5195 或 5200）
  - 00878 現價 35.09，步長 5：±10% 內只有 35，且距離 <1% 被剔除，所以整數關口為空
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：levels.py 的全部價位都只做 round(…,2)，不依台股升降單位（TWSE 第 62 條階梯）取整，也沒有引用 repo 內既有的 TickSizeModel（它在 research_context.py 有被使用，只是 levels.py 沒用）。

以 9/23 資料、days=250 逐價位套用各自升降單位計算，偏離格點的比例如下：
- 2454：30/44
- 3034：29/40
- 2379：22/40
- 6196：20/39
- 3545：19/39
- 0050：25/41
- 00878：0
- 00885：0
- 00893：0（這三檔都是低於 50 元的 ETF，升降單位 0.01）
合計約 40%，而且只發生在升降單位 ≥0.05 的標的。例如 2454 的 Pivot P＝5196.67，實際只能成交在 5195 或 5200。

整數關口的步長階梯（分界點 10／20／100／500，步長 0.5／1／5／10／50）與升降單位無關，導致部分標的關口過稀。00878 收盤 35.09、步長 5 時只有 35 一個候選，距離 0.26% 小於 1% 被濾掉，最後整數關口為空。

5196.67 是點位提醒「上方」推薦清單的第一項，不是預設目標價。預設目標價是 K 線雙擊處的價格，取兩位小數，同樣不貼齊格點。提醒條件是 close >= / <=，觸發結果等同取整到下一個格點，影響只在顯示與關口清單的完整性。
    - 理由：我照 /api/stock-analysis/levels 的實際路徑重算了一次：PriceAlertDialog 用 days=250 呼叫，所以取 start=2026-09-24−500 天，並往前多讀 150 天 warmup。資料直接讀 kline_daily_enriched 的 parquet，依序跑 filter_halt_days、compute_indicators、compute_levels，再用 repo 內的 TickSizeModel.get_tick_size 逐一檢查每個價位是否落在升降單位的格點上。

【核心結論成立】
- levels.py 所有價位都用 round(…,2) 輸出，也沒有 import market_rules。
- 整數關口的步長階梯（分界點 10／20／100／500）與升降單位無關。
- 00878 的整數關口確實是空的。
- 2454 的 Pivot P=5196.67 不在跳 5 的格點上。
- 2454 30/44、0050 25/41 兩個數字完全重現。

【有四處需要修正】
(1) 3034 的 30/40 與 3545 的 21/39，只有在「全部價位都套用現價的升降單位」時才會重現。台股升降單位是依委託價格本身所在的區間決定，逐價位套用各自的升降單位後，結果是 3034 29/40、3545 19/39。
  - 3034 的 Fib 50%＝492.5：低於 500 元，升降單位 0.5，在格點上。
  - 3545 的 44.15 與 46.85：低於 50 元，升降單位 0.05，在格點上。
(2) 「多數不在格點上」不成立於全部自選股。
  - 9 檔合計只有 145/363，約 40%。
  - 3545 是 19/39，未過半。
  - 00878、00885、00893 都是低於 50 元的 ETF，升降單位是 0.01，round(…,2) 必然落在格點上，所以都是 0 個。
  - 這個問題只出現在升降單位 ≥0.05 的 6 檔，比例 49%～72%。
(3) 「get_tick_size 未被使用」有誤。它在 research_context.py:731 被呼叫，market_rules 內部也有使用，只是 levels.py 沒有用。
(4) 「2454 預設目標價 5196.67」的說法不精確。PriceAlertDialog 唯一的入口是 K 線圖雙擊，一定會帶入大於 0 的 initialTarget。因此目標價預設是雙擊點的價格（取兩位小數，同樣不貼齊格點），用 recommended.above[0] 當預設值的分支實際上不會執行。5196.67 是「上方」推薦清單的第一項，使用者點選後才會成為目標價。

另外，「沿用 A 股設計」沒有直接證據：_round_numbers 的 docstring 沒有提到 A 股，只能算推測。

【嚴重度維持 P3】提醒條件是 close >= / <=，功能上等同把目標價取整到下一個格點，影響只在顯示，以及心理關口清單偏稀。
    - 證據：
      - 程式碼：backend/app/indicators/levels.py:533-542。步長依價格分為 0.5／1／5／10／50，分界點 10／20／100／500，沒有參考 tick_class。
      - 程式碼：backend/app/indicators/levels.py:552、562，以及 155、163、201、240、243、264、271、316、318、341、343、351、393、461、464、506。全部價位一律 round(v, 2)。15-18 行只 import logging／typing／polars，沒有 import market_rules。
      - 程式碼：backend/app/taiwan/market_rules.py:184-214。TickSizeModel.get_tick_size 定義的普通股 6 個區間、ETF 2 個區間。
      - 程式碼：get_tick_size 有被使用。backend/app/taiwan/research_context.py:731 呼叫 TickSizeModel.get_tick_size(p_close)；market_rules.py:231、254 內部也有呼叫。與 finding 說的「未被使用」不符。
      - 官方 MIS 五檔（reference/csv/mis_snapshot.csv，9/23 收盤快照），sens.py 的輸出如下：2454 買價 5180/5175/5170、賣價 5185/5190/5195，跳動 5.0。6196 買價 548/547/546，跳動 1.0。0050 買價 112.45/112.40/112.35，跳動 0.05。00878 買價 35.09/35.08/35.07，跳動 0.01。6488 跳動 1.0。五檔全部與 get_tick_size 一致。
      - 本地重算（腳本在 scratchpad\audit\work\verify-g4tick\repro.py；days=250，資料 2025-05-12..2026-09-23），逐價位套用各自升降單位的結果（finding 的數字列在括號內）：
- 2454：30/44（30/44，一致）
- 3034：29/40（30/40）
- 2379：22/40
- 6196：20/39
- 3545：19/39（21/39）
- 0050：25/41（25/41，一致）
- 00878：0/37
- 00885：0/41
- 00893：0/42
合計 145/363，約 40%。
      - detail.py：改成「全部套用現價的升降單位」時，3034 是 30/40，3545 是 21/39，與 finding 相符。造成差異的價位如下：
- 3034：Fib 50%=492.5，本身價位的升降單位是 0.5，現價的是 1.0。
- 3545：60日新低 44.15、向上缺口 46.85，本身價位的升降單位是 0.05，現價的是 0.1。
- 6196：套用現價升降單位時會變成 24/39。
      - detail.py：2454 最後一根 K 線 H/L/C=5265/5140/5185（與 MIS h/l/z 相同），P=(H+L+C)/3=5196.666…，輸出 5196.67，不在跳 5 的格點上（實際可成交價是 5195 或 5200）。R1=5253.33、S1=5128.33 也都不在格點上。
      - sens.py：00878 收盤 35.09，步長 5，視窗 31.581～38.599 之間只有 35；35 與收盤價相距 0.256%，小於 1% 被濾掉，_round_numbers 回傳 []，與 finding 一致。
      - sens.py：把 turnover_rate 設為 None、0.5、2、5 分別重算，sr 組的偏離格點數幾乎不變（3545 從 3/3 變成 2/3，其他相同）。本地 parquet 的 turnover_rate 幾乎全是 null（338 列中只有 1 列有值），所以上述計數與實際 API 的差距最多只有 ±1。
      - 前端：frontend/src/components/StockPreviewDialog.tsx:160-162、440、465 是 PriceAlertDialog 唯一的開啟入口，一定會帶入 targetPrice。EChartsCandlestick.tsx:1008-1016 只在 price 大於 0 時才觸發。PriceAlertDialog.tsx:53-55、61 用雙擊價格取兩位小數作為預設目標。94-101 行在 target 已有值時直接 return，所以 recommended.above[0]（2454 的 5196.67，枢轴位 P）只會出現在「上方」推薦清單的第一項，要點選（187 行）才會成為目標價。
      - 前端：PriceAlertDialog.tsx:152 的提醒條件是 close >= 或 <= targetValue，由於實際價格只落在格點上，效果等同把目標價取整到下一個格點，不影響觸發結果。
      - 工作樹：git status 與 session 開始時的快照相同，只有 3 個原本就存在的修改，驗證腳本都只寫在 scratchpad。
      - 線上核實：WebSearch 已達 session 上限，無法使用。豐雲學堂的網址是 404；zh.wikipedia 的臺灣證券交易所條目沒有升降單位表。因此升降單位只以官方 MIS 五檔實測佐證。

### [P3 → P3] 價位標籤為簡體中文，直接顯示在繁中 UI 與提醒名稱中（部分成立）

- **主張**：後端 label（如「枢轴位 P」「压力位 R1」「成交密集区」「向上缺口」）由 PriceAlertDialog 原樣顯示，並寫入規則名稱；群組標籤則是繁體。
- **正確的定義或數值**：台股部署的使用者看到繁體標籤。
- **app 實際行為**：簡繁混排，提醒名稱也留下簡體。
- **對使用者的影響**：局部介面體驗。
- **修法建議**：後端改回傳 label key，由前端翻譯；或後端改用繁體標籤。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/levels.py:155、205-211、240-244、461-465
  - frontend/src/components/stock-analysis/PriceAlertDialog.tsx:143、295
- **線上證據**：
  - （無）
- **本地計算證據**：
  - s7_dialog.py：9 檔清單都含「枢轴位 P／压力位 R1／支撑位 S1」等簡體標籤
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：後端 compute_levels 回傳的點位 label 有 42/55 種含簡體字：成交密集区、枢轴位 P、压力位 R1-R3、支撑位 S1-S3、布林上／中／下轨、短／中／长期通道上／下轨、ATR 上／下轨、整数关口 N（levels.py:155、163、205-211、316-318、341-351、368、397-400、562）。PriceAlertDialog 會把這些 label 原樣顯示在推薦價位清單（PriceAlertDialog.tsx:295），下方的群組名稱則是前端 LEVEL_GROUPS 的繁體（:296），因此同一列簡繁混排。使用者點選價位後，label 會寫進新建規則的名稱（:143、:189）。以 9 檔自選股的現有資料重現，每檔的 12 個推薦價位中有 7 到 12 個是簡體，合計 87/108。另外，「向上／向下缺口」「60／250日新高／新低」「前高／前低」「Fib x%」都是簡繁同形字，不屬於此問題，原 finding 對 levels.py:240-244、461-465 的引用與「向上缺口」的舉例都是錯的。
    - 理由：主要說法成立，但舉的例子和引用的行號有錯。後端 compute_levels 的點位 label 確實有簡體字。PriceAlertDialog 會把 level.label 原樣顯示在推薦價位清單（PriceAlertDialog.tsx:295），下方的群組名稱則取前端 LEVEL_GROUPS 的繁體字（PriceAlertDialog.tsx:296、34；AnalysisKChart.tsx:47-58），所以同一列上下是簡繁混排。使用者點選價位後，label 會存進 selectedLabel（PriceAlertDialog.tsx:189、100），再組進規則名稱「點位提醒 · … · 漲至{selectedLabel}」（PriceAlertDialog.tsx:143）。repo 裡找不到任何簡轉繁的轉換（grep opencc/zhconv/s2t/toTraditional，只命中安裝程式的語系檔）。/levels API 也是直接回傳 compute_levels 的結果（stock_analysis.py:130-141）。AnalysisKChart 元件本身沒有地方使用，別處只 import 它的 LEVEL_GROUPS，所以會顯示這些 label 的 UI 只有這個對話框。

錯誤一：finding 舉的「向上缺口」（levels.py:461-465）以及引用的 levels.py:240-244「{n}日新高／新低」，都是簡繁同形字，不是簡體。佐證是專案自己的繁體前端也用同樣的字：前端把後端群組名改成繁體時，「缺口位」「前高前低」原樣保留（AnalysisKChart.tsx:50、56）；signals.ts:105 的繁體 UI 也直接用「60日新高」。錯誤二：finding 漏列了實際含簡體的其他 label，包括布林上／中／下「轨」（341-351）、Keltner「通道上／下轨」與「长期」（316-318、358-368）、ATR「上／下轨」（397-400）、「整数关口」（562）。

數據重現：我自己寫腳本，用 polars 直接讀 kline_daily_enriched，取 2025-05-12 到 2026-09-24 共 338 個分區，資料到 2026-09-23 為止。把 9 檔自選股各自組成 DataFrame，餵給純計算函式 compute_levels。結果共 55 種不同的 label，其中 42 種含簡體獨有字，其餘 13 種是同形字（60／250日新高／新低、前高、前低、向上／向下缺口、Fib x%）。接著照對話框的邏輯模擬：以價位依離收盤價的距離排序、同價位去重，上下方各取最近 6 個。每檔都顯示 12 個推薦價位，其中簡體 label 的個數依序為 2454=12、3034=7、2379=8、6196=11、3545=9、0050=10、00878=10、00885=9、00893=11，合計 108 個中有 87 個是簡體。

嚴重度維持 P3，屬於局部介面體驗，不影響數值與交易結果。另外，這次 session 的 WebSearch 額度已用完（200/200），無法再做線上比對。簡繁字形是字元層級的事實，改用專案內繁體前端的既有用字作為佐證。
    - 證據：
      - frontend/src/components/stock-analysis/PriceAlertDialog.tsx:295 `{level.label}` 原樣顯示；:296 `levelGroupLabel(level)` 取自 LEVEL_GROUPS（:34）
      - frontend/src/components/stock-analysis/PriceAlertDialog.tsx:143 規則名稱 `點位提醒 · ${name || symbol} · ${...}${selectedLabel || targetValue.toFixed(2)}`；selectedLabel 在 :189（點選價位）和 :100（沒有預設目標價時自動選取）設定，:194 使用者手動改價時清空
      - frontend/src/components/stock-analysis/AnalysisKChart.tsx:47-58 LEVEL_GROUPS 是繁體（壓力支撐、樞軸點、布林帶、Keltner長期、整數關口），但「前高前低」「缺口位」與後端相同，證明這兩組是同形字
      - frontend/src/lib/signals.ts:105 繁體 UI 也用 '60日新高'，與 levels.py:240 同形
      - backend/app/indicators/levels.py 簡體 label 所在行：155、163（成交密集区）、205-211（枢轴位／压力位／支撑位）、316-318（通道上／下轨）、341-351（布林上／下／中轨）、368（长期）、397-400（ATR 上／下轨）、562（整数关口）；同形字所在行：240-244（日新高／新低）、264／271（前高／前低）、461-465（向上／向下缺口）、506（Fib）
      - backend/app/api/stock_analysis.py:130-141 /levels 直接回傳 compute_levels 的結果，沒有轉換；在整個 repo grep opencc|zhconv|s2t|toTraditional，只命中 packaging\*.iss/.isl
      - AnalysisKChart 元件沒有任何地方渲染（grep 'AnalysisKChart' 只命中 PriceAlertDialog.tsx:6 的 LEVEL_GROUPS import 與元件定義本身）
      - 腳本：AUDIT_SCRATCH/work/verify-g4-sclabel/repro.py；指令：cd backend && uv run python <repro.py>；輸出：files 338 rows 3036；distinct labels 55 with simplified-only chars 42
      - 重現輸出（對話框上下方各 6 個價位中含簡體的個數）：2454=12/12、3034=7/12、2379=8/12、6196=11/12、3545=9/12、0050=10/12、00878=10/12、00885=9/12、00893=11/12，合計 87/108；例如 2454 顯示 '枢轴位 P','整数关口 5250','压力位 R1','布林上轨'…
      - 與 finding 並列：finding 稱「向上缺口」是簡體，重現結果為 gap 組 '向上缺口'／'向下缺口' 判定 script-neutral；finding 引用的 240-244 為 '60日新高' 等，同為 script-neutral；finding 沒列出的 布林／通道／ATR 的「轨」以及「整数关口」，都判定為 SIMPLIFIED
      - WebSearch 無法使用：本 session 額度 200/200 已用完，未取得線上來源

## 確認正確的項目

- **樞軸點公式 P=(H+L+C)/3、R1=2P−L、S1=2P−H、R2=P+(H−L)、S2=P−(H−L)、R3=H+2(P−L)、S3=L−2(H−P)（levels.py:187-193）**：用官方 STOCK_DAY 2026-09-23 的 H/L/C 獨立計算 9 檔，與 app 輸出比對；再把 app 的 _pivot_points 餵入每檔最近 14 個官方交易日（共 126 例）比對。最大差 0.0033，全部來自 round(…,2)。例：2454 H/L/C 5265/5140/5185 → P 5196.67、R3 5378.33、S3 5003.33，兩邊相同。R3/S3 在代數上等同 TradingView 的 R3=2P+(prevH−2·prevL)、S3=2P−(2·prevH−prevL)
  - 來源：https://www.tradingview.com/support/solutions/43000521824-pivot-points-standard/ 「P = (prevHigh + prevLow + prevClose) / 3 … R3 = P * 2 + (prevHigh - 2 * prevLow) S3 = P * 2 - (2 * prevHigh - prevLow)」
  - 來源：https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/pivot-points 「Support 1 (S1) = (P x 2) - High … Resistance 2 (R2) = P + (High - Low)」
  - 來源：s3_independent.py 輸出：max |app-ind| pivot: 0.0033；pivot rolling check: cases 126 max|diff| 0.0033
- **60 日／250 日極值（levels.py:233-244，以 tail(n) 的最高價／最低價計）**：用獨立的 tail(60)／tail(250) high.max／low.min 比對 9 檔，全部一致。和 A04 的 high_60d／low_60d 口徑不同：後者是收盤價的 rolling_max/min（pipeline.py:438-440），例如 2454 為 5480 對 5185、2379 為 862 對 840、3034 為 572 對 558。兩者都是合法定義；價位模組用盤中高低點符合前高前低的慣例，不算錯誤。df 有 333～338 列，tail(250) 起點約為 2025-09-12
  - 來源：s3_independent.py 輸出 (c)：9/9 app==ind True
  - 來源：backend/app/indicators/pipeline.py:438
- **ATR14（pipeline.py:482-485 ewm alpha=1/14, adjust=False；即時遞推 pipeline.py:1553 tr/14+atr*13/14）**：從同一份資料獨立實作以 SMA 起始的 Wilder ATR14，9 檔最後一日到小數第 4 位都相同（例：2454 226.2349、0050 1.7897）
  - 來源：https://en.wikipedia.org/wiki/Average_true_range 「ATR_t = (ATR_{t−1} × (n−1) + TR_t) / n」、「Wilder recommended n = 14」
  - 來源：s3_independent.py 輸出 (d)：app ATR14(ewm) == Wilder SMA-seeded
- **ATR 波動通道 close±1.5ATR、close±2ATR（levels.py:375-401）**：用 close 與 atr14 獨立計算 4 條線，9 檔全部完全一致（例：0050 116.03／115.13／109.77／108.87）
  - 來源：s3_independent.py 輸出 (e)：9/9 ok
- **Keltner 三檔的算術（依 app 自己的定義：MA20±2·ATR14、MA60±2.5·ATR14、現場計算的 MA120±3·ATR14；levels.py:293-368、stock_analysis.py:74-91）**：獨立計算 SMA20／SMA120 與 ATR14 後比對，9 檔短期與長期上下軌都完全一致（例：2454 KS 4983.72/4078.78、KL 4395.00/3037.59）。定義本身與原作者不同，見 findings
  - 來源：s3_independent.py 輸出 (d)：indep same formula == app
- **布林帶數值（levels.py:323-353 讀 pipeline 的 boll_upper/lower、ma20）**：用 MA20±2·樣本標準差（ddof=1）獨立計算，9 檔完全一致。ddof 口徑問題見 findings
  - 來源：s3_independent.py 輸出 (d2)：ddof1 列與 app 相同
- **斐波那契回撤（levels.py:473-508）**：在 tail(120) 取最高／最低及其位置判斷波段方向，上漲波段為 hi−rng·r、下跌波段為 lo+rng·r，比率 0.236～0.786。9 檔完全一致（例：2454 上漲波段 1430(04-07)→5480(09-22)：4524.2/3932.9/3455.0/2977.1/2296.7；3545 下跌波段：48.72/51.54/53.83/56.11/59.36）。方向邏輯符合 StockCharts 的說明
  - 來源：https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-annotation-tools/fibonacci-retracements.md 「After an advance, chartists apply Fibonacci ratios to define retracement levels」、「can also be applied after a decline to forecast the length of a counter-trend bounce」
  - 來源：s4_sr_gap.py 輸出 (3)：9/9 ok
- **缺口偵測條件：當日 low > 前日 high 為向上缺口、當日 high < 前日 low 為向下缺口（levels.py:431-436）**：這是收盤後仍未回補的完整跳空，比 StockCharts 的 full gap（開盤價高於昨日最高價）更嚴格，但合理。獨立實作的偵測結果與 app 相同（回補判定另見 findings）
  - 來源：https://chartschool.stockcharts.com/table-of-contents/chart-analysis/gaps-and-gap-analysis.md?ask=... 「A full gap up happens when today's opening price is higher than yesterday's high price」
  - 來源：s4_sr_gap.py 輸出 (2)
- **整數關口的算術（levels.py:515-564，依 app 自己的步長規則）**：獨立列舉 ±10% 範圍內的步長倍數，依距離排序取前 8 個，再剔除距離 <1% 者；9 檔完全一致（例：2454 5250/5100/5300/5050/5350/5000；00878 為空）
  - 來源：s4_sr_gap.py 輸出 (4)：9/9 ok
- **repo.resolve_asset_type／get_daily_asset 對台股個股與 ETF 的讀取（repository.py:1338-1348、1578-1592）**：data\instruments_etf、instruments_index、kline_etf_enriched 都是空目錄（ls -la 為空），所以 ETF 與指數集合是空集合，9 檔（含 0050、00878、00885、00893）都解析為 stock，走 get_daily 讀 kline_daily_enriched。實測請求視窗 2025-05-12～2026-09-23 的列數：0050 333、3545 337、其他 338；ETF 讀得到資料
  - 來源：s1_explore.py 輸出 rows_in_request
  - 來源：backend/app/repository.py:1344-1348
- **價位計算的輸入 OHLCV 與證交所 STOCK_DAY 一致**：9 檔 × 38 個交易日（2026-08、2026-09/01～09/23）逐日 join：open/high/low/close 最大差都是 0.0，4 檔 ETF 的成交量差也是 0；個股成交量有小差異（見 unverifiable）
  - 來源：reference/csv/twse_stock_day_202608_202609.csv（https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?...，由 refdl 預先下載）
  - 來源：s3_independent.py 輸出 (a)
- **PriceAlertDialog 的推薦價位與提醒條件（PriceAlertDialog.tsx:47-51、75-101、152）**：以 days=250 呼叫 API；把 11 組價位合併後依距現價排序，依 toFixed(2) 去重，上下方各取 6 個；預設目標價取上方最近一個；提醒條件是 close >= / <= 目標價。用 Python 重現此邏輯（s7_dialog.py），得到各檔實際顯示的清單：樞軸點在 9 檔中都占上下方清單的多數，其中 7 檔的預設目標價是 P 或 R1
  - 來源：s7_dialog.py 輸出
  - 來源：frontend/src/components/stock-analysis/PriceAlertDialog.tsx:75-101

## 無法驗證的項目

- **第三方公開數值三方比對（Yahoo 奇摩、鉅亨等網站上這 9 檔的樞軸點、Keltner、斐波那契、POC）**：這些價位多半是由圖表工具即時計算，財經網站不會公開固定數值；本 session 的 WebSearch 額度已用盡（200/200），無法搜尋；Investopedia 對 WebFetch 回應「Claude Code is unable to fetch from www.investopedia.com」。公式定義改以 TradingView／StockCharts／Wikipedia 原文查核。
- **台股「心理整數關卡」的步長慣例（例如 ≥500 元用 50 元一檔、20～100 元用 5 元一檔是否合適）**：找不到交易所或權威來源定義；屬於品味判斷。可客觀驗證的只有升降單位（已用 MIS 五檔驗證，見 findings）。
- **盤中即時快取那一列是否帶有 atr_14、ma20、boll 等欄位**：get_daily 以 common_cols 交集覆蓋最新日（repository.py:1491-1492）；如果即時快取缺少某些指標欄位，整個 df 會失去該欄，盤中的 Keltner／ATR／布林帶就會變成空值。這需要啟動後端與輪詢才能觀察，依規定不執行。
- **個股成交量與 STOCK_DAY 的小差異（2454 38 天內最大差 434,469 股、6196 165,000 股；ETF 為 0）**：屬於成交量口徑（其他領域），未追查來源。對籌碼分布的相對權重影響推測可忽略。
- **樞軸點盤中漂移的實機畫面**：不得啟動後端或呼叫 API；改用 app 自己錄下的 2026-09-23 分鐘 K（data\kline_minute，時間軸已轉成 Asia/Taipei）組出部分 K 棒，餵給 _pivot_points 模擬（見 findings）。分鐘資料本身稀疏（2454 只有 84 根），模擬出的 H/L 與官方全日值（5265/5140）不同。
