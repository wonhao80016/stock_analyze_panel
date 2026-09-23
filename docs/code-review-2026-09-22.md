# Code Review 2026-09-22 — feat/wire-unreachable-settings

審查範圍：`feat/wire-unreachable-settings` 分支相對 `main` 的變更，加上使用者回報的
「清單價格與個股彈窗 card 不同步」症狀追查。由四個獨立 agent 分頭審查（前端、後端
core、`backend/app/taiwan/*`、價格資料流追查），本文彙整其結果。

狀態欄：`已修` / `待修` / `不修（附理由）`。

---

## 一、使用者回報的症狀：6196 帆宣點開 card 資訊不同步

清單與 card 走**兩條完全不同的價格資料流**：

| | 清單（自選股列表） | Card（個股彈窗） |
|---|---|---|
| 端點 | `/api/watchlist/enriched` | `/api/kline/daily` |
| 來源 | `watchlist_enrichment.py` 直接呼叫 `TaiwanRealtimeService.get_quotes()` | `TaiwanDailyStore` 磁碟日K ＋ 條件式注入當日活蠟燭 |
| 漲跌幅 | 後端 `change_pct`（小數制） | 前端用**最後兩根日K**自行重算 |
| 刷新 | 每個 SSE `quotes_updated` tick 失效 | `_focusSymbol` 單槽 hack，且受即時行情開關閘門 |

`QK` 的 SSE 失效前綴含 `watchlist-enriched`、`taiwan-quotes`，**不含 `kline`**
（`frontend/src/lib/queryKeys.ts`），所以兩邊刷新節奏本來就不對等。

關鍵不對稱：`hybrid_provider._live_quote_frame` 要求 `is_realtime and not is_stale`
才收錄，而 `watchlist_enrichment` 對 `q.last_price` 照單全收。同一個 symbol 在同一秒
可以「清單有價、card 沒有活蠟燭」。

---

## 二、Findings

### A. 價格同步群組

#### A1. MIS 缺成交價時以最佳買價冒充 — 已修
`backend/app/taiwan/realtime/mis_provider.py`

`z` 與 `trade.z` 皆缺時 `last_price = bid_p`。買價不是成交價，會讓畫面系統性低於
交易所，`change`／`change_pct` 由此推算也跟著錯，而 `available_fields` 仍標示
`last_price`。

**嚴重度修正**：原審查稱「`z` 缺席率 83–95%，所以這是常態路徑」。2026-09-22 10:44
（盤中）實測 MIS 六檔標的：`z` 全部是 `'-'`（6/6），但 `trade.z` **全部有值**（6/6）。
83–95% 只適用於 `item["z"]` 這一層，`trade.z` 已經接住；買價 fallback 是罕見的第三層。
原判斷的方向對，嚴重度高估，**不是使用者症狀的成因**。

修法：該層改為 `continue`（不產生該 symbol 的 quote），讓
`TaiwanRealtimeService` 既有的 Yahoo → 官方快照 → 日K fallback 鏈接手——該鏈是以
「symbol 不在結果 map 中」為觸發條件，回傳 `last_price=None` 的 quote 反而會讓
fallback 全部被跳過、畫面直接空白。

副作用（已知，待觀察）：盤前試撮時段若 `z` 與 `trade.z` 同時缺，整批會落到 Yahoo，
輪詢成本上升。輪詢池有 `TAIWAN_REALTIME_MAX_SYMBOLS` 上限，影響有界。

#### A2. Card 的漲跌幅忽略後端 `change_pct` — 已修
`frontend/src/components/StockInfoBar.tsx`

原本 `chgPct = (rows[n-1].close - rows[n-2].close) / rows[n-2].close`。後端注入的活蠟燭
明明帶了 `change_pct`（`backend/app/api/kline.py` 的 `live_row`）卻被忽略；本地日K若
缺最近一個交易日，分母會是兩天前的收盤，%數必然與清單不同。

修法：以 `latest.change_pct`（小數制，見 `backend/app/indicators/pipeline.py` 的欄位
說明）反推 `prevClose`，`chg`／`chgPct`／振幅三者改用同一個 `prevClose`，缺
`change_pct` 時才退回前一根日K。歷史列的 `change_pct` 本來就等於兩列相除，所以行為
只在活蠟燭那一根改變——正是出問題的那一根。

#### A3. 換股時 placeholder 把上一檔的價格填進新股票 — 已修
`frontend/src/components/StockDailyKChart.tsx`

`placeholderData: (prev) => prev` 在 queryKey 換 symbol 時仍回傳舊資料。父層
`StockPanel` 換股時會清掉 `dailyResult`，子層的 `onDataChange` 立刻用 placeholder
（上一檔的 rows）回填，而標頭的 symbol/name 走 props 已經更新 → 新代碼配舊價格。
彈窗現在是 Layout 裡常駐的單一實例，不關閉直接換股是常見路徑。

修法：`placeholderData: (prev, prevQuery) => prevQuery?.queryKey?.[1] === symbol ? prev : undefined`，
只在同一檔股票換日期區間／ext 欄位時沿用。

#### A4. `get_realtime` 丟掉沒有即時報價的標的 — 不修（原建議有誤）
`backend/app/taiwan/providers/hybrid_provider.py`

原審查建議「live 與 official 依 symbol 合併後回傳，而非二選一」。**不採納**，理由是
該建議與它自己引用的程式碼矛盾：

- `_live_quote_frame` 的 docstring 明寫官方端點在盤中發布的是**前一交易日收盤**。
- `quote_service._build_daily` 無條件蓋上 `cn_today()` 再寫入。

兩者相加，合併後的 official 列會把**昨收寫成今日的日K／enriched**——正是 card 現在
靠守衛避免的事，合併等於讓 card 顯示「標成今天的昨收」，比現況更糟。

另有第二個回歸：`_records_realtime_status` 要求整批全部 `realtime` 才回 True，
`_evaluate_monitors` 看到 False 就整輪跳過。合併後只要有一檔拿不到即時報價，
**整輪監控告警會靜默停擺**。

正確方向（尚未實作，需獨立一輪）：把批次層級的閘門改成 per-symbol，讓非即時列
只進報價快取、不進日K寫入也不進規則評估，然後才談合併。

#### A5. 非自選標的永遠拿不到當日活蠟燭 — 已修
`backend/app/api/kline.py` 的注入守衛依賴 `quote_service.get_enriched_today()`，
而該快取的輪詢 universe 只有「自選股 ＋ 監控規則標的 ＋ 指數」
（`backend/app/services/quote_service.py`）。從 Screener／Monitor 點開的股票永遠
顯示昨收。

2026-09-23 補充範圍：受影響的是預覽視窗上方的日K（`StockPanel` → `StockDailyKChart`
→ `/api/kline/daily`）；下方研究區塊（`TaiwanStockDetail`）的K線走 detail 端點，不經過
這條路徑。即時行情關閉時任何標的都沒有活蠟燭，那是開關本身的設計，不屬於本項。

修法：快照是今天的、但裡面沒有這檔個股時，`_maybe_inject_live_candle` 改呼叫新的
`QuoteService.fetch_live_bar` 單獨取一次報價。它與輪詢共用同一段取價函式
（`_taiwan_realtime_records`：同一個 provider、同一個 `change_pct` 百分數→小數換算），
並經過同一道 `_records_datable_today` 閘門，所以非即時的備援報價不會被標成今天。
沒有採用「把該檔加進輪詢池」：池內標的會寫進今日日K與 enriched 快照、出現在盤中選股、
並被全市場範圍的監控規則評估，看一眼就改變這些結果不合理。代價是池外標的每次重抓日K
多一次報價查詢（provider 內部經 `TaiwanRealtimeService` 的短快取）。

技術指標（2026-09-23 追加）：這根活蠟燭的均線、MACD、KDJ、布林、RSI、ATR、量比，與池內
標的走同一個增量計算——把輪詢 `_flush_live_enriched` 裡「昨日遞推狀態 ＋ 今日 OHLCV」
那段原封不動抽成 `_enrich_today_incremental`，輪詢與 `fetch_live_bar` 共用。遞推狀態
`live_agg` 由啟動時載入的 enriched 歷史建成，而 enriched 分區是全市場（2026-09-22 分區
2,316 檔），所以池外標的一樣有狀態；只取單列計算，成本在毫秒級。遞推狀態不可用時
（例如啟動預熱中、或該標的沒有歷史）退回只有 OHLCV 的K棒，不會讓請求失敗。均線交叉
這類訊號依賴 `prev_enriched`（盤中是只含池內標的的快照），池外標的的訊號欄位可能為空，
但日K注入只取指標數值，不取訊號。

已知風險（未處理）：這次查詢在 API 請求執行緒內同步進行。網路正常時是一次 MIS 往返；
網路卡住而非立即失敗時，備援鏈依序嘗試，各來源逾時為 MIS 8 秒、Yahoo 8 秒、官方端點
15 秒（見各 provider 的 `timeout` 預設值），最壞可達數十秒，期間該次日K請求（包括第一次
打開預覽視窗）都會等待。只在快照日期為今天時才會走到；DNS 解析失敗這類立即失敗不受影響。
若要處理，方向是對失敗的標的加冷卻時間，或改成背景取價、下一次重抓時再注入。

已知風險（未處理，2026-09-23 追加技術指標後）：`fetch_live_bar` 是 `get_live_agg()` 第一個
在請求執行緒上的呼叫者。該函式每天第一次被呼叫時會查一次 DuckDB，若遞推狀態的基準日
不符，還會同步重建（`_refresh_enriched`，耗時數十秒），而且這個跨日分支不受
`_enriched_warming` 保護。盤中輪詢在把快照更新成今天之前就會先呼叫 `get_live_agg()`，
所以即時行情開啟時不會發生；只有「快照已是今天、但當天還沒有輪詢呼叫過它」時（例如快照
由其他流程更新），才可能由請求執行緒承擔。報價或組K棒失敗時 `fetch_live_bar` 回傳 None、
不注入，日K請求本身不會失敗（`test_fetch_live_bar_none_when_building_the_bar_fails`）。
`frontend/src/lib/useQuoteStream.ts` 原註解稱這條路徑「只讀內存緩存、零額外成本」，
已同步改正。

驗證：`tests/test_taiwan_quote_fallback_gate.py` 的 `test_fetch_live_bar_*`（正例、
三種非即時狀態、provider 例外、非台股 provider；指標：以遞推狀態斷言 MA5 = (40.0 + 10.2) / 5，
以及無遞推狀態、管線拋例外時退回純 OHLCV）與 `tests/test_kline_hot_path_memory.py`
的 `test_maybe_inject_live_candle_*`（池外標的注入；快照非今天、標的已在池內、ETF
三種情境不呼叫 `fetch_live_bar`）。

#### A6. `/api/kline/daily` 幾乎恆繞過 enriched 正本 — 已修
`_needs_local_history` 以 `earliest > start` 判斷，而 `start = today - days` 通常不是
交易日，所以幾乎恆真 → 走 `TaiwanDailyStore` ＋ 現算 `compute_enriched`，形成第二套
獨立計算的價格／指標來源。修法：改以列數或日期跨度判斷，而非與請求視窗起點比較。

2026-09-23 實測影響：起始日為週一（2026-05-25）時走 enriched 正本，為週日（2026-05-24）
時改走 `TaiwanDailyStore` 現算，確認會依起始日切換。以 2454、6196、0050 在 2026-05-25～
09-22（85 個交易日）比較兩條路徑：收盤價、MA5／MA20／MA60、KDJ、布林、量比完全相同；
MACD DIF 最大差 0.017（6196）、RSI14 最大差 0.0023（6196），來自遞迴型指標往前暖機的
長度不同。兩邊收盤價逐日完全一致，這段期間兩條路徑的價格口徑相同。畫面上看不出差別，
實際代價是被誤判時多一次本地重算（兩次實測約 80～190 ms／次，依快取狀態而定）。原描述
「第二套獨立計算的價格來源」屬實，但數值影響可忽略。

修法（2026-09-23）：`_needs_local_history` 不再要求 enriched 最早日等於請求起始日，改為
只有在「起始日與 enriched 最早日之間，本地 `TaiwanDailyStore` 有交易日」時才判定不足、
回退本地重算。逐日檢查分區是否存在（新增 `TaiwanDailyStore.has_date`），成本只與這段
間隔的天數有關（週末 1～2 天、連假約 10 天）；間隔超過 31 天（`_MAX_MARKET_CLOSURE_DAYS`，
長於任何休市）直接回退本地重算、不逐日檢查，所以單次最多檢查 31 天，不隨歷史長度增長。
沒有改用 `available_dates()`，因為它每次列出全部分區（實測 661 個約 15～32 ms），會讓熱路徑
隨歷史線性變慢。真的只有當日活蠟燭、缺整段歷史時，仍會回退本地重算。實測修正後週日起始
也改走 enriched。

驗證：`tests/test_kline_hot_path_memory.py` 新增 6 個 `test_needs_local_history_*`（週末起始、
連假起始、長間隔不逐日檢查三個重現測試，修正前失敗；缺交易日仍回退、起始日即有資料、
無 enriched 列三個護欄）；引用 K 線與 `TaiwanDailyStore` 的 23 個測試檔共 306 個測試通過。

#### A7. 純台股自選時 `as_of` 為 `None` — 已修
`backend/app/api/watchlist.py` 的 `as_of` 只由 A 股／ETF／指數快取決定，純台股自選股
時為 `None`，導致 `frontend/src/pages/Watchlist.tsx` 的當日蠟燭即時修補整段被跳過。
迷你日K批次資料的查詢快取 5 分鐘，結果是價格欄隨即時價跳動、迷你K棒卻停住。

修法（2026-09-23）：不能只把 `as_of` 補成今天——台股列來自 `TaiwanRealtimeService`
的備援鏈（MIS → Yahoo → 官方收盤快照 → 本地日K），同一次回應裡不同列可能屬於不同
交易日，全域日期會把備援拿到的昨收畫成今天的K棒（與 B3 同類）。改為逐列日期：每列新增
`live_date`，台股列只有即時報價（`source_meta.is_realtime`）才填入該報價自己的
`trade_date`，其餘為 None；A 股列填原本的全域 `as_of`，行為不變。前端修補邏輯抽成
`frontend/src/lib/liveCandles.ts` 的 `patchLiveCandles`，逐列依 `live_date` 更新或追加。
回應的全域 `as_of` 保留，向後相容。

驗證：`tests/test_taiwan_watchlist_enrichment.py` 與 `tests/test_watchlist_market_dispatch.py`
各新增 3 個測試（即時報價帶自身交易日、非即時與查無報價為 None、A 股列等於 `as_of`、
每列都有 `live_date`），`frontend/src/lib/liveCandles.test.ts` 8 個測試（含「非即時列不得
修補」護欄）；後端相關測試 37 個、前端全套 256 個通過，build 通過。MIS 層只有在盤中
（`MarketStatus.OPEN`）且報價未過期（交易日為今天、時間戳正常）時才標 `is_realtime`
（`backend/app/taiwan/realtime/mis_provider.py` 的 `date_mismatch`／`is_stale`），Yahoo、
官方快照、日K備援恆為非即時。因此盤前與收盤後迷你K棒不做即時修補，顯示批次資料的內容，
直到批次查詢重抓；修正前純台股自選完全不修補，這不是新的退步。

---

### B. 資料正確性（後端）

#### B0. 產業相對強弱訊號拿 5 日報酬減單日平均 — 已修
`backend/app/taiwan/abnormal_diagnostics.py` 的 `RELATIVE_STRENGTH_OUTLIER` 算
`diff_5d = ret_5d - ind_m.average_change_pct`，其中 `ret_5d` 是個股**5 日**報酬率，
而 `average_change_pct` 是產業成分股**當日**漲跌幅的平均（`industry_intelligence.py`
對 `change_pct` 取 mean）。兩個不同期間的數字相減，門檻 10 個百分點因此沒有一致的
意義：多頭期間會系統性高估個股的相對強度。

同一行上方的註解寫著「Excess over industry equal-weight 5D」，與程式碼矛盾；真正的
5 日欄位 `relative_strength_5d` 就在同一個 `if` 裡被當存在性守衛用，卻沒有進入運算。
看起來是原意要用 5 日、實作接錯欄位。

修法（已採用「5 日對 5 日」）：`IndustryMetrics` 新增 `average_return_5d`，即產業
成分股等權重的 5 日報酬平均。它原本就在 `relative_strength_5d` 的算式裡被算出來
（`ind_5d_mean`），只是沒有外露；`relative_strength_5d` 本身是「產業 − 大盤」，
已經扣掉大盤，不能拿來當產業自身的報酬用。

兩側的 5 日基期日是對齊的：`abnormal_diagnostics` 的 `prior_5[0]` 與
`industry_intelligence` 的 `prior[-5]` 都解析到同一個交易日。

實測影響（2026-09-15～09-21 五個交易日，全市場）：

| | 舊 | 新 | 重疊 | 只有舊 | 只有新 |
|---|---|---|---|---|---|
| 五日合計 | 534 | 439 | 392 | 142 | 47 |

只有舊算式會觸發的 142 筆是被移除的誤報，主因是多頭日的產業單日平均偏小，使個股
的五日累積漲幅輕易超過 10 個百分點；只有新算式會觸發的 47 筆則是新增的命中，
它們原先因為基期尺度不對而被舊算式漏掉。

數字以兩種互不相依的方法各算一次並取得一致結果：一是直接以 store 與 industry
服務重算，二是實跑 `get_diagnostics()`（暫時把守衛與算式改回舊版取得舊側集合）。
注意舊算式寫成 `(average_change_pct or 0.0)`，基期為 None 時會退成 0 而照常觸發，
重算時若補上一個「基期不得為 None」的守衛就會少算。

附帶：同檔 docstring 已腐化——列出程式碼並不存在的 `DEALER_FLOW_SPIKE`、把
`== 5` 寫成 `coverage >= 5`、未提法人類的 15 日覆蓋門檻與基期下限、並把本項寫成
`industry_5d_return`。前端說明文字最初就是照抄這行而寫錯，建議一併修。

#### B1. 即時輪詢非 merge 覆寫 Taiwan 發佈的 enriched 分區 — 已由 `feat/radar-strategies` 修正
`backend/app/taiwan/enriched_refresh.py` 把含 `signal_*` 的全市場資料寫進
`kline_daily_enriched/date=X/part.parquet`；`quote_service` →
`repository.flush_live_enriched_asset` 對**同一路徑**做非 merge 覆寫，且只寫
`ENRICHED_STORAGE_COLS`（不含任何 `signal_*`）與當次輪詢的自選股。

後果：`backend/app/taiwan/screener.py` 讀不到 signal 欄 → `df.clear()` → 任何帶
`signals` 條件的選股**永遠回 0 筆**；其他讀該分區者也只剩自選股那幾列。

修法：Taiwan enriched 改用獨立表名，或兩個寫入端共用 merge 路徑與同一把 `_write_lock`。

**已由 `feat/radar-strategies` 的 `889cbd9` 修正**（於 2026-09-22 併入本分支）。實查
合併後的程式碼：`flush_live_enriched_asset` 已改為委派 `merge_live_enriched_asset`，
落盤時對既有分區做 `concat(...).unique(["symbol","date"], keep="last")` 且整段在
`_write_lock` 內——非自選股不再被抹掉。另新增 `_live_write_allowed`：交易所尚未
發布該日資料前，即時寫入只留在記憶體，不建立分區；`ScreenerService.latest_date()`
也改讀 `taiwan_official_latest_date()`（`TaiwanDailyStore`）而非即時快取日期。該
commit 自述選股從 0 筆恢復為 108 筆，與本項描述的症狀吻合。

**殘留疑點（尚未驗證）**：磁碟合併時 `df_storage` 只帶 `ENRICHED_STORAGE_COLS`
（不含 `signal_*`），`diagonal_relaxed` 會讓即時列的 `signal_*` 成為 null，而
`keep="last"` 讓即時列勝出。理論上自選股的 signal 欄仍可能在**交易所發布之後**的
即時寫入被清成 null。`_live_write_allowed` 讓這個窗口變窄，但未實測確認。

**附帶觀察**：2026-09-22 盤中的 `kline_daily_enriched/date=2026-09-22` 分區只有
12 欄、0 個 `signal_*`，而 09-17 與 09-21 都是 69 欄、20 個 `signal_*`——即本項
描述的污染實例。當下不影響選股（基準日為 09-21，不會讀到該分區），且該分區由
合併前的程式碼產生。

#### B2. `quote_ts` 寫成微秒而非毫秒 — 已修
`backend/app/services/quote_service.py` 把 provider 的 `timestamp`（`datetime`）
`.cast(pl.Int64)`，polars 產出的是 µs（實測 `1790073000000000`），是契約的 1000 倍；
provider 本來就有正確的 ms `quote_ts` 欄，卻因為不在 `cols_map` 而被丟棄。

後果：`session_progress_from_ts` 例外後靜默回 `1.0`（盤中量比從不做時間折算，
`vol_ratio_5d` 全天失真）；`data_integrity._is_snapshot` 永遠無法判定盤中快照，
損毀修復機制形同關閉。

**修法**：`quote_ts` 從 `cols_map` 抽出，改由 `_quote_ts_expr()` 決定，依來源分流——
provider 已備好的毫秒欄優先直接採用；只有 `timestamp` 時**依型別**處理，`Datetime`
走 `dt.epoch("ms")`，整數則原樣視為毫秒。原本的無差別 `cast(pl.Int64)` 正是微秒的來源。

**驗證**（三種輸入，期望值 `1790083800000`）：

| 輸入 | 結果 |
|---|---|
| provider 帶毫秒 `quote_ts` | 1790083800000 ✓ |
| 只有 `datetime` 的 `timestamp` | 1790083800000 ✓（修正前為 1790083800000000）|
| 只有整數 `timestamp` | 1790083800000 ✓ |

另有一條測試把結果直接餵進 `session_progress_from_ts`，斷言落在 0～1 之間——那正是
這個 bug 原本發作的位置。突變測試：把型別分流關掉退回微秒，該測試轉紅。

**注意 B12 並未因此解決**：`session_progress_from_ts` 仍然永不回傳 `None`，所以
`quote_service` 那個 `progress is None` 分支依舊不可達。本項修的是不會再用錯誤的
微秒去觸發它，兩者是不同的問題。

#### B3. 非即時 fallback 報價被蓋上今天日期寫入 — 已修
`TaiwanHybridProvider.get_realtime` 退回 `official.fetch_quote`／`get_daily` 時拿到的是
**前一交易日** OHLC，`_build_daily` 仍無條件蓋上 `cn_today()` 並 `flush_live_daily`。
新增的 `_quote_source_realtime` 閘門只擋監控告警，不擋寫入。這是「同一檔股票、不同
畫面不同價」的直接成因之一，也是 A4 不能直接合併的原因。

2026-09-22 複查：**成因未變，後果範圍已縮小**。合併帶進來的 `_live_write_allowed`
讓交易所尚未發布該日資料前的即時寫入只留記憶體、不建立分區，所以不再整片污染。
但發布**之後**，該股仍會被前一交易日的 OHLC 以今天日期蓋掉（merge 的
`keep="last"` 讓即時列勝出），UI 錯價依舊。

**修法**：新增 `_records_datable_today()`，**逐筆**（非整批）過濾出 provider 標記為
`realtime` 的記錄，掛在三個 `_build_daily` 呼叫點（股票／ETF／指數）。

刻意只擋**落盤**這一段：報價快取仍保有那些 fallback 價格，畫面照常顯示並帶著自己的
provenance，只是不會被寫成今天的日K。有一條測試以程式碼位置斷言
`_build_index_quotes` 出現在閘門**之前**，把這個分界釘住。

無 `status` 欄的 provider（A 股 TickFlow）不套用此閘門——否則會變成什麼都不寫。

**驗證**（四種批次）：

| 批次 | 保留 |
|---|---|
| 全部 realtime | 全部 |
| realtime ＋ official 混合 | 只留 realtime 那筆 |
| 全部非即時 | 0 筆（不產生日K）|
| 無 status（A 股）| 全部 |

突變測試：把閘門改成全放行，三條行為測試轉紅。

#### B4. 台股路徑從不請求指數，卻每輪用空結果覆寫指數快取 — 待修
`sym_list` 由自選 ＋ 監控規則組成，`CORE_INDEX_SYMBOLS`（`t00.TWSE`／`o00.TPEX`）
被分類卻從未被請求，於是 `index_records` 恆空，每輪用空 frame 覆寫
`_index_quotes_cache`。`GET /api/intraday/indices` 因此永遠回退到
`source: "index_daily"` 的昨收。

2026-09-23 補正影響範圍：前端沒有任何地方呼叫 `/api/intraday/indices`；Monitor 的
`TaiwanIndexBar` 走 `/api/intraday/quotes`（`TaiwanRealtimeService`），會正常即時更新。
本項只影響依賴 `_index_quotes_cache` 的指數類監控規則，畫面上看不到，優先度應下調。

#### B5. `distance_to_upper/lower_limit` 只存在於增量路徑 — 待修
`backend/app/indicators/pipeline.py` 只在 `compute_enriched_today` 內加這兩欄；
`run_pipeline`／`compute_enriched` 與全量回退路徑沒有。回退發生時
`monitor._condition_mask_expr` 遇到缺欄回傳 `None`，「接近漲停／跌停」規則**靜默零命中**。

#### B6. Edge 觸發在 cooldown 吞掉事件時仍解除 armed — 待修
`backend/app/strategy/monitor.py` 在命中當下就 `armed=False`，但 cooldown 檢查在
`_evaluate_rule` 之後才做並 `continue`。在 `cooldown_seconds`（預設 3600）內重新觸發的
條件會同時被「抑制」與「解除武裝」，使用者永遠看不到，而且要等條件再次轉為 false
才可能重新觸發。

#### B7. enriched 暫存檔命名與讀取端 glob 衝突 — 待修
`enriched_refresh.py` 用 `suffix=".parquet"` 在分區目錄內建暫存檔，讀取端卻是
`**/*.parquet`。寫入到 rename 之間若被讀到，同日分區出現兩個檔案 → 該日每列重複計算。
同分支的 `realtime/minute_recorder.py` 已用 `.parquet.tmp` 並註明此風險，兩處不一致。

#### B8. 指數月資料一次網路失敗即永久放棄回補 — 已修
`backend/app/taiwan/index_daily.py` 任何例外都回空 frame，隨即把該月加入
`_barren_months` 且行程生命週期內不再重試。單次逾時 = 該月指數 K 線永久缺口。
修法：區分「抓到空資料」與「抓取失敗」，只有前者標 barren。

2026-09-23 補充：影響範圍只有加權指數（`t00.TWSE`）與櫃買指數（`o00.TPEX`）自己的日K
（`api/kline.py` 與 `detail_service.py` 兩個呼叫點），個股、選股、相對強弱不使用。實查當時
以個股日K分區為交易日曆，兩指數在各自已抓範圍內（加權 2024-08-01 起 519 個、櫃買
2025-08-01 起 276 個交易日）缺口為 0，log 無抓取失敗紀錄；最後一筆停在 9/17，
比個股落後 4 個交易日。同根因的另一條路徑：當月抓到空結果（例如當月第一個交易日前）也會被
記成 barren，該月變成過去月份後永遠不再抓。

修法（2026-09-23）：`_fetch_month` 失敗時回傳 None，空結果仍回空 frame。失敗的月份記入
`_failed_months`，冷卻 10 分鐘（`_FAILED_MONTH_RETRY_SECONDS`）後重試，冷卻期間不重打，
保留原本「避免每次載入圖表都重打」的用意；只有過去月份的空結果記為 barren，當月的空結果
不記。驗證見 B10。

#### B9. 法人／融資 fallback 只看全庫最新日，且把 1 日值填進 5 日欄 — 已修
`backend/app/taiwan/detail_service.py` 用 `max(dates)` 當查詢區間：該標的當日若無列
直接回 None（卡片顯示「無資料」，儘管本地有大量歷史）。另把當日淨額填入
`foreign_net_5d` 等欄，因子計算當成 5 日累計使用 → **因子值錯**。
（即時抓取路徑 `_aggregate_institutional` 也有同樣的 1 日值充數，一併修正。）

修法：
- 法人與融資的 store 備援改為在最近 20 個 store 日期內找該標的的最後一列，
  `meta.trade_date` 標示該列日期。
- 5 日欄位改由 `_institutional_5d` 計算：以該筆日期為終點、往前 5 個 store 日期的加總。
  T86 只列出當天有法人交易的股票，所以視窗內缺列視為 0。證據：2026-09-23 實測本地
  store 最近 20 個日期共 136,437 列，三大法人六個買賣欄全為 0 的列數是 0。
- store 湊不滿 5 個日期、不含該筆日期、或該標的在該日沒有列時，5 日欄位回傳 None，
  不再拿 1 日值充數。即時路徑也走同一個計算，因此 store 尚未寫入當天時 5 日欄位為 None。

驗證：`tests/test_detail_chip_store_fallback.py` 新增 6 個測試（最新日無列、缺列當 0、
store 不足 5 日、即時路徑兩種情境、融資最新日無列），修正前全數失敗、修正後通過。

#### B10. `_needs_fetch` 把部分快取的月份當成已完整 — 已修
`index_daily.py` 的 `cached_months` 只要該月有任一列就算命中。曾只抓到半個月
（或被 B8 中斷）的歷史月份永遠補不齊。

2026-09-23 確認會發生：當月只在有人看指數圖時才每 5 分鐘重抓，若某月最後一次被看是
月中，下個月起該月「已有資料」而永不補齊，且以磁碟資料判斷，重啟也補不回。實查時兩指數
停在 9/17，若 9/30 前沒人打開指數圖，9/18 起的 9 月資料就會變成永久缺口。

修法（2026-09-23）：過去月份以個股日K分區（`TaiwanDailyStore.has_date`）為交易日曆，
快取的最後一天達到該月最後一個交易日才算完整，否則重抓一次；抓過或判定完整的過去月份
記入 `_complete_months`，同一行程內不再檢查或重抓。以真實資料模擬載入 2024-08 起的整段
範圍：兩個指數各只請求當月一次，過去月份全部判定完整，沒有集體重抓；5 分鐘內再次載入
零請求。

驗證：新增 `tests/test_taiwan_index_daily.py` 6 個測試。三個重現測試修正前失敗：失敗月份
冷卻後重試、半月資料的過去月份補齊到最後交易日且只重抓一次、當月空結果在該月過去後
會再抓。三個護欄：冷卻期間不重打、過去月份空結果不重抓、完整月份不重抓。相關 23 個測試檔
共 277 個測試通過。

#### B11. MinuteBarRecorder 落盤在鎖外，且回傳值語意錯誤 — 待修（plausible）
`backend/app/taiwan/realtime/minute_recorder.py` 的 `_write()` 在釋放 `self._lock`
之後執行，輪詢執行緒與收盤 `flush()` 可能同時對同一 parquet 做 read-modify-write。
另回傳整個分區列數而非本次新增列數，「completed bars written」被灌水。

#### B12. 死路徑：`progress is None` fallback — 待修（minor）
`session_progress_from_ts` 永不回傳 `None`（失敗時回 `1.0`），所以
`quote_service` 的 `session_progress()` 伺服器時間 fallback 不可達。與 B2 相加，
壞掉的 `quote_ts` 靜默等於「整個交易時段已過完」而不是退回牆鐘時間。

---

### C. 前端

#### C1. 報價清單與詳細頁刷新政策不一致 — 待修
`frontend/src/components/monitor/TaiwanQuotePanel.tsx` 與
`frontend/src/components/monitor/TaiwanIndexBar.tsx` 只設 `staleTime: 3000`、無
`refetchInterval`，只靠 SSE `quotes_updated`，而該事件在 `useQuoteStream` 內被
`if (!enabledRef.current) return` 擋掉（即時行情開關預設 false）。同時
`frontend/src/pages/TaiwanStockDetail.tsx` 是 `refetchInterval: 10_000`。
→ 即時行情關閉時，Monitor 報價條停在進頁面那一刻，個股視窗每 10 秒更新。

#### C2. 自選頁板塊篩選沿用舊 A 股 localStorage — 已修
`frontend/src/pages/Watchlist.tsx` 的 `BOARDS` 由 5 個 A 股板塊改為
`['上市','上櫃']`，但讀 `storage.watchlistBoardFilter` 無任何遷移／清洗，且以
`boardFilter.size < BOARDS.length` 判斷是否啟用篩選。

舊使用者存了 1 個舊板塊（例如 `創業板`）→ size 1 < 2 → 篩選生效 → 上市／上櫃皆不在
集合中 → **所有個股行消失**，而 UI 上兩個板塊鈕都顯示未選取，看不出原因。
存 2 個舊值則相反：篩選被整個略過但按鈕全暗。

2026-09-22 複查修正描述：不是「整頁清空」。`Watchlist.tsx` 有「非股票不受板塊
篩選」的豁免，所以 ETF 與指數仍會留在清單上。**半空的清單比全空更難判斷成因**，
使用者更容易誤以為是資料壞了。

**修法**：載入持久化值時以 `BOARDS` 白名單過濾，過濾後為空就回退全選。清洗放在
初始化而不是渲染時，舊值只會被丟棄一次，之後存回去的就是乾淨的。

**驗證**：

| 存著的舊值 | 結果 |
|---|---|
| `['創業板']` | 回退全選，篩選不生效 |
| `['主板','科創板','創業板']` | 回退全選 |
| `['創業板','上櫃']` | 只留 `上櫃`，篩選生效（使用者確實選過上櫃）|
| `['上市','上櫃']` | 原樣保留 |
| `[]` | 預設全選 |

另有一條測試**刻意重現未修正的行為**：`new Set(['創業板'])` 會讓篩選判定為生效，
但沒有任何板塊能匹配——把症狀本身釘住，而不只測修正後的結果。
修法：初始化時過濾掉不在 `BOARDS` 內的值，空集合則回退全選。

#### C3. 個股「建立監控規則」預設 `logic:'or'` — 撤除（原判斷有誤）

2026-09-22 複查撤除。原判斷把**兩個不同畫面**的東西放在一起比了：文件引用的
「多選預設需全部命中(AND)」文案位於 `RuleEditor.tsx` 的完整模式；而兩個 preset
呼叫端都帶 `simple`，該模式在更早處就 early return，它自己的文案是「選擇觸發訊號
(任一命中即報警)」——**與 `logic:'or'` 一致，同畫面內沒有矛盾**。

殘留的真實問題（嚴重度低，未另立項）：完整模式選「訊號」情境會強制 `and`，個股
彈窗固定 `'or'`，同一種規則**因入口不同而語意不同**。這是跨畫面不一致，不是同畫面
矛盾，使用者不會在單一畫面上看到相反的說法。

#### C4. 回補任務啟動後、第一次輪詢回來前可重複啟動 — 待修
`frontend/src/pages/settings/TaiwanEnrichmentDataCard.tsx` 的 `isJobRunning` 為真，
但渲染條件是 `isJobRunning && activeJob`；`mutate` 成功後 `isPending` 已回 false 而
`activeJob` 仍是 null，因此按鈕仍在畫面上且可點。800ms 內點兩次 → 第二個 job 覆蓋
`activeJobId`，第一個變成無人追蹤的孤兒，下方還顯示上一輪的數字。

#### C5. Monitor 告警記錄的股票代號按鈕是空點擊 — 待修
`frontend/src/components/monitor/TaiwanAlertsList.tsx` 的
`onClick={() => onSelectSymbol?.(alert.symbol!)}`，但 `frontend/src/pages/Monitor.tsx`
只傳 `alerts`，沒傳 `onSelectSymbol`。本次改動把 Screener／QuotePanel／IndexBar 的
代號連結都改成 `openPreview`，唯獨漏了這裡。游標是 pointer、有 hover 底線，但點了沒反應。

#### C6. RuleEditor 的 `simple` 模式仍可切到「策略・板塊（進階）」 — 撤除（原判斷有誤）

2026-09-22 複查撤除。「監控情境」晶片列確實沒有 `!simple` 守衛（資產類型列則有），
這點原判斷是對的——但那一列位於 `simple` 模式的 early return **之後**，簡易模式
根本渲染不到它，所以描述的情境無法發生。

缺守衛本身仍是隱患：若將來有人把 early return 移走或改成條件渲染，這一列就會暴露
出來。但它現在不是 bug。

---

## 三、次要問題（已確認存在，未列入主清單）

2026-09-22 複查新增（各一句，未展開調查）：

- `backend/app/services/quote_service.py`：輪詢池超過上限時**從尾端截斷**，而監控
  規則的標的正是 append 在自選股之後——**會最先被丟掉，規則靜默失效**。這比 A5
  嚴重，因為使用者明確設過規則卻不會收到告警，且畫面上沒有任何線索。
  **2026-09-23 已修**：規則標的改排在自選股之前並去重，截斷只會丟自選股（自選列停住
  在畫面上看得到）。補正：截斷時後端 log 會寫一行 warning，「靜默」指的是畫面上。
  驗證：`tests/test_taiwan_quote_fallback_gate.py` 的
  `test_monitor_rule_symbol_survives_truncation_when_watchlist_fills_the_cap`。
- `backend/app/taiwan/research_context.py`（2026-09-23 修 B9 時發現，未修）：研究脈絡的
  法人 5 日累計取「該標的最後 5 列」（`inst_sorted.tail(5)`），不是最後 5 個交易日。
  T86 只列有法人交易的日子，交易稀疏的標的這 5 列會跨越超過 5 個交易日。B9 修正後的
  個股卡片以交易日為視窗，兩處在稀疏標的上會出現不同的「5 日」數字。
- `backend/app/api/kline.py`：活蠟燭注入守衛用 `date.today()`（伺服器本地日）而非
  全專案一致的 `cn_today()`／台北日，跨時區部署會整段跳過注入。
- `frontend/src/lib/useQuoteStream.ts`：`depth_updated` 明文不受即時行情開關限制，
  而 `quotes_updated` 受閘門擋住。結果是 Monitor 上封單數字會動、報價不動，
  **放大 C1 的錯覺**——看起來像在更新，其實價格是凍的。
- `frontend/src/pages/Watchlist.tsx`：「全選＝跳過篩選」的寫法還有第二個副作用，
  手動同時選取上市＋上櫃時 `hasBoardFilter` 會顯示成未篩選。


- `frontend/src/components/Layout.tsx` 靜態 import `StockPreviewProvider` →
  `StockPreviewDialog` → `TaiwanStockDetail` → ECharts，把整個個股詳細頁拉進首屏
  bundle，抵銷 `frontend/src/router.tsx` 明文寫的 lazy 分割。
- `TaiwanEnrichmentDataCard` 的 `isJobRunning` 見 C4。

## 四、已實查排除（非 finding）

`daily_store.scan_range` 的 except fallback；`chip_history._trading_days_start` 的
±1 天（被 `.tail(days)` 吸收）；`daily_update` 新增 `skipped` 狀態對 `overall_status`
的影響；`universe/adapters.py` 抽出的 `leverage_multiplier_of`；`search_instruments`
簽名變更無孤兒呼叫端；`numpy_limit_price` 的新 `usable` mask 形狀正確；
`add_limit_distance_columns` 的 polars Null-dtype 運算傳播 null 而非拋例外
（對 polars 1.40.1 實測）；`quote_service` 的 `change_pct / 100` 正規化正確（只有
MIS／Yahoo live frame 帶該欄）；`queryKeys.ts` 移除 `market` 參數後
`api.instrumentSearch` 也不再收該參數；告警列表的 `fmtPct` 是修正而非 bug；
`intraday-chart.ts` 移除 `volume * 100` 正確；`EChartsIntraday` 軸標籤索引與 271 格
時間軸一致；`benchmarks.ts` 大小寫與後端 `_BY_UPPER` 相容；
`ABNORMAL_SIGNAL_DEFINITIONS` 的 10 個 id 與門檻值與 `abnormal_diagnostics.py` 吻合。

## 五、審查方法與涵蓋範圍

四個 agent 分頭審查前端 diff、後端 core diff、`backend/app/taiwan/*` diff，以及價格
資料流的專項追查。行號在審查當下逐一實查；本文刻意只寫檔案路徑不寫行號，因為後續
修改會讓行號腐化（見 `~\.claude\rules\lessons.md` L7／L10）。

A1 的嚴重度由本文作者在 2026-09-22 10:44 盤中實打 MIS 端點推翻，A4 的建議由本文
作者以該 finding 自身引用的程式碼反駁後不採納。其餘 finding 未經第二輪獨立驗證。

---

## 六、複查（2026-09-22 晚間）

本文寫於當日上午。之後這條分支推進約 20 個 commit、合併了 `feat/radar-strategies`、
新增 `/strategies` 頁面、介面全面正體化。待修項逐一對**當下的程式碼**重新查證，
由兩個未參與實作的 agent 分頭執行（後端 14 項、前端 6 項）。

| | 結果 |
|---|---|
| 後端 A5–A7、B2–B12 | 14 項**全部仍然成立**，零失效 |
| 前端 C1、C2、C4、C5 | 仍然成立 |
| 前端 C3、C6 | **撤除**——原判斷有誤，非被修掉 |

前後端的差異有跡可循：後端的 14 項今日幾乎未觸及（只動了 enriched 發佈與股本），
自然全數留存；前端因為新增頁面、改寫文案而被重新檢視，才暴露出兩項原本就錯的判斷。

**撤除的兩項都不是「被新 commit 修掉」，而是原本就判斷錯**。C3 把兩個不同畫面的
文案放在一起比；C6 指出的缺守衛屬實，但那段程式碼在該模式下不可達。這類錯誤的
共同點是：**只讀了程式碼，沒有確認它在目標情境下會不會被執行**。

### 待修優先序

判準是「使用者實際遇到的機率 × 遇到時被誤導的程度」，不是修起來難不難。

當時排出的前三項 **B2 → B3 → C2 已於同日修正**，各自的修法與驗證見其章節。
B2 的預期沒有完全兌現：它讓 `vol_ratio_5d` 與 data_integrity 的快照修復恢復作用，
但**沒有解開 B12**——`session_progress_from_ts` 永不回傳 `None` 是獨立的問題。

剩餘 15 項，接下來建議：

1. **B9** — 把 1 日值當 5 日累計餵進因子計算，產出的是**錯的數字**而非缺值。
   錯的數字比缺值難發現得多。
2. **輪詢池從尾端截斷**（見次要問題章節）——監控規則的標的 append 在自選股之後，
   超過上限時最先被丟掉。症狀是「設了規則卻收不到告警」，比清單少一檔難察覺。
3. **A5** — 從 Screener／Monitor 點開的股票永遠看不到當日活蠟燭。與 B3 同屬
   「不同畫面不同價」這條線，但成因不同（輪詢池不含該標的，而非寫錯日期）。

2026-09-23 更新：以上三項已修正，修法與驗證見各章節。受影響模組的 27 個測試檔共
259 個測試通過；同日追加 A5 技術指標後，涵蓋增量管線的 29 個測試檔共 275 個測試通過。
主清單剩餘 13 項：A6、A7、B4–B8、B10–B12、C1、C4、C5。

2026-09-23 A7 已修（見其章節）。主清單剩餘 12 項：A6、B4–B8、B10–B12、C1、C4、C5。
依實際使用情境重排（即時行情開啟、無監控規則、自選全為台股）：A6 → B8＋B10 → B7 → B11；
開始使用監控規則前需修 B6、B5、C5；C1 只在即時行情關閉時可見；B4 無畫面使用。

2026-09-23 A6 已修（見其章節；實測數值影響可忽略，修正後統一走 enriched 正本）。
主清單剩餘 11 項：B4–B8、B10–B12、C1、C4、C5。建議順序：B8＋B10 → B7 → B11。

2026-09-23 B8、B10 已修（見其章節）。主清單剩餘 9 項：B4–B7、B11、B12、C1、C4、C5。
建議順序：B7 → B11；開始使用監控規則前修 B6、B5、C5。
