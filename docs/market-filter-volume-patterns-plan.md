# 原則調整、大盤濾鏡分級與量價型態：實作規劃

- 日期：2026-10-02。第五版：依使用者當日的新決定改寫，並修正第三、四輪驗收的意見。狀態：已實作（2026-10-03，分支 `feat/market-filter-volume-patterns`，基底 `ba2e007`），第 7 節 11 題全部採 A；實作偏差見文末「實作結果」。2026-10-10 第 4 階段改了分級規則，見文末「第 4 階段後的變更」。本文件的 `路徑:行號` 是實作前的位置，請以程式碼為準。
- 需求原話：
  - #2「推薦加上大盤濾鏡：加權指數收盤跌破 20 日均線時，所有 BUY 降為 WATCH。」
  - #3「6 種量價型態」（定義見 4.4）。
- 新決定（2026-10-02）：
  - D1：取消「不預測漲跌、不推薦個股」，只保留「不做自動下單」。本次只改文件、UI 文案與測試，AI 的提示詞、輸出驗證與黑名單不動。
  - D2：#2 照原意做，策略選股依評分分成「買進（BUY）」與「觀察（WATCH）」；加權指數收盤跌破 20 日均線時，所有「買進」降為「觀察」。
  - D3：風險提示改成「建議僅供參考，不保證獲利，投資風險自負」，取代「不構成買賣建議」一類的說法。
- 標註方式：`路徑:行號` 是本次親自讀到的位置；「推測」表示未實跑或未逐行核對；「未查到」表示用兩種以上樣式搜尋都沒有結果（樣式列在附錄）。外部 `market.py` 的行號依驗收方取得的原檔，`volume.py` 依使用者提供的 29-118。

## 0. 結論摘要

1. 原則調整（第 1 節）：改 7 個檔案的原則宣告與 UI 文案，並刪掉兩組舊禁用字測試。AI 功能的自述文字與後端免責聲明保留，因為 AI 行為這次沒變。
2. #2（第 3 節）：
   - 分級在後端 `/api/screener/cached` 產生，每列加上 `grade`（`buy`／`watch`）與 `grade_reason`。
   - 門檻推薦「每支策略依排序取前 5 名為買進」。
   - 大盤狀態用本機指數日 K 判定，排程與手動更新都會無條件補抓指數。
3. #3（第 4 節）：在台股 enriched 盤後發佈時算出 6 個 `vp_*` 欄位，不進即時管線，也不登錄到 `ENRICHED_COLUMNS`。顯示位置在「台股選股」頁。
4. 建議順序：原則調整 → #2 → #3。三者都會改到熱點 `frontend/src/lib/api.ts`，請分開提交。二開分級都是 L3（理由見 4.5）。

## 1. 原則調整（MVP，對應 D1、D3）

逐處判斷如下（行號都已實查）：

| 位置 | 現況 | 處理 |
|---|---|---|
| `README.md:32` | 「不是投資軟體」 | 改成「資料整理與策略選股工具；建議僅供參考，不保證獲利，投資風險自負」 |
| `README.md:34` | 「不預測漲跌、不推薦個股、不做自動下單」 | 改成「明確不做：自動下單」，其餘照舊 |
| `README.md:59` | 異常訊號「系統不據此給推薦」 | 保留：理由同 `操作說明書.md:242` |
| `README.md:63` | AI 的兩個用途、「不直接出股」 | 保留：「不直接出股」如實描述 AI 行為（`screener_nl.py` 不動）；「兩個用途」本來就不準，列為 O2-8 |
| `README.md:188` | 「不構成任何投資建議」 | 改成 D3 的寫法 |
| `操作說明書.md:7`、`:682` | 「不構成任何投資建議」 | 改成 D3 的寫法 |
| `操作說明書.md:49` | 「不預測漲跌、不推薦個股、不做自動下單」 | 改成只保留「不做自動下單」 |
| `操作說明書.md:174` | 市場強弱「不是對後續走勢的預測」 | 保留：仍然如實描述該區塊的資料性質 |
| `操作說明書.md:242` | 異常訊號「系統不會據此給推薦」 | 保留：異常訊號仍不分級，敘述屬實 |
| `操作說明書.md:248`、`:317` | AI 翻譯與比較的限制 | 保留：AI 這次不動 |
| `docs/features.md:15` | AI 解析「不直接推薦股票，也不自動執行投資決策」 | 保留：描述 AI 行為，後半句屬於保留的原則 |
| `docs/features.md:23` | 「不構成買賣建議」 | 改成 D3 的寫法 |
| `docs/strategy-pick-tracking-plan.md:55,228,241-245,303,323` | O9 改名、「被讀成推薦」、§3.8 文字合規、禁用字測試、R9 合規 | 每處加註「原則已於 2026-10-02 調整，見 market-filter-volume-patterns-plan 第 1 節」，原文保留作為紀錄 |
| `frontend/src/pages/TaiwanScreener.tsx:788` | 「(純確定性無推薦)」 | 改成「(規則判定)」 |
| `frontend/src/pages/TaiwanScreener.tsx:836` | 「不含任何買賣建議」 | 改成「屬規則判定，僅供參考」 |
| `frontend/src/components/StrategyPickPerformance.tsx:232` | 「不構成買賣建議，也不代表未來表現」 | 改成「以下為過去選股紀錄的統計結果，計算方式固定；過去表現不代表未來。」後面接 `{INVESTMENT_DISCLAIMER}`（完整原字串） |
| `frontend/src/pages/Onboarding.tsx:179` | 「不構成任何投資建議」 | 改成 D3 的寫法，並註明「本專案不做自動下單」 |
| `frontend/src/pages/TaiwanStockDetail.tsx:938,942`、`TaiwanStockCompare.tsx:429,432` | 「無買賣推薦」「絕不……提供進出場操作建議」 | 保留：這是 AI 報告的實際限制，提示詞沒改（個股頁依 `backend/app/taiwan/ai_research.py:325-326`，比較頁依 `comparison_ai_research.py:189-191`） |
| `backend/app/taiwan/ai_research.py:47`、`comparison_ai_research.py:54-57` | AI 報告的免責文字 | 不動：D1 明定 AI 不改，且內容仍如實描述 AI 報告 |

- 共用文案：新增 `frontend/src/lib/disclaimer.ts` 匯出 `INVESTMENT_DISCLAIMER = "建議僅供參考，不保證獲利，投資風險自負。"`，供上表的 UI 與第 3 節的新元件共用。
- 測試（統一做法：只在「有統計資料」的畫面斷言免責字串；錯誤狀態依設計不渲染說明，見 `StrategyPickPerformance.tsx:114-120`，而 MethodNote 只在 `:128` 成功時渲染）：
  - 刪除 `frontend/src/pages/Strategies.test.tsx:514` 的 `FORBIDDEN`。
  - 涉及的兩個 it、三個斷言：
    - `:812`（成功狀態）：改成 `expect(container.textContent).toContain(INVESTMENT_DISCLAIMER)`。
    - `:826`（摘要讀取錯誤的迴圈）：刪除這個斷言，保留 `findByText(/無法讀取/)`。
    - `:836`（摘要成功、明細錯誤）：MethodNote 仍會渲染，所以改成同 `:812` 的正向斷言。
    - 兩個 it 的標題同步改名。
  - `backend/tests/test_screener_pick_performance_api.py`：刪除 `:28-29` 的 `BANNED`、`:286-289` 的測試，以及因此沒人用的 `:4` `import json`，最後以 ruff 驗證。API 欄位名稱不變。
  - AI 相關測試不動。
- 保留：所有「不做自動下單」的敘述，包括 `docs/features.md:50`。

## 2. 現況盤點（#2、#3 共用）

### 2.1 選股結果與評分

- `/api/screener/cached`（`backend/app/api/screener.py:391-412`；前端型別在 `frontend/src/lib/api.ts:2651-2656`）回傳 `as_of`、`results{total, as_of, rows}`。
  - 每一列帶有 `score` 與全部 enriched 欄位，沒有 `rank` 欄位（以唯讀方式讀 `strategy_cache.json` 實查）。
  - 快取為空時，在 `:405-407` 直接返回。
  - 盤中監控引擎的結果會覆蓋同名策略（`:309-330`），這些結果的 `as_of` 是當天（`backend/app/strategy/monitor.py:1103`）；磁碟快取不存在時，頂層 `as_of` 為 None（`:313-314`）。
- 評分方式：
  - 20 支會執行的內建策略都有非空的 META `scoring`（例：`backend/app/strategy/builtin/boll_breakout.py:32`）。research_only 的 `factor_rank_research` 是 `{}`（`factor_rank_research.py:52`）。全部 `limit` 都是 500。
  - 任一因子不是有限值時，分數直接設為 0（`backend/app/backtest/matrix.py:3741`）；所有因子都落在當日最小值時，分數也可能正常地算出 0。第 4 階段起，缺任一評分輸入的候選分數改為 null，見文末。
  - `build_matrix_score` 只在當日入選者（`entry & basic_mask`，`backend/app/backtest/matrix.py:3556-3565`）之間，對每個因子做 min-max 正規化，再依權重加總後乘以 100（`:3686-3741`）。所以分數範圍是 0–100，衡量的是「同策略當日入選者之間」的相對位置。
  - 只有 1 檔入選時，分數固定是 50（`:3734-3735`；實查 `n_day_low_reversal` 1 檔，分數 50.0）。
  - 沒有 `scoring` 的自訂策略，改用 `order_by` 的原值排序（`:3744-3756`），尺度各不相同。
- 排序：引擎依 score 排序，方向取 `descending`，再截到 `limit`（`backend/app/strategy/engine.py:1252-1264`）。
- 快照：`rank` 是快取各列的順序，從 1 開始（`backend/app/services/strategy_pick_history.py:417-423`），schema 中也存了 `score`（`:41-48`）。
- 實查 2026-10-02 的快取：20 支策略，每支入選 0–342 檔；有 0 分列的策略有 3 支：`bullish_alignment` 67/342、`low_volatility_leader` 82/160、`pullback_to_support` 7/35；三支的前 5 名都沒有 0 分。這些 0 分來自換手率缺值，第 4 階段已把這三支的 `turnover_rate` 評分改成 `vol_ratio_5d`。
- 前端顯示：看板卡標題「策略當日推薦」（`frontend/src/pages/Dashboard.tsx:449-453`），每支策略顯示前 3 檔（`:417,510`）；策略庫清單分頁的選股列表在 `frontend/src/pages/Strategies.tsx:423`，「選股基準日」在 `:169-172`。
- 既有的「買進」樣式：`frontend/src/components/AlertToast.tsx:94`（`bg-danger/15 text-danger`），用於策略監控的 `buy_signal` 推播；後端文字是「買入訊號」（`backend/app/strategy/monitor.py:1176`）。這條推播不隨大盤降級，不在本次範圍（O2-9）。

### 2.2 指數與觸發路徑

- 加權指數 `t00.TWSE`（`backend/app/taiwan/benchmarks.py:19`）存在 `taiwan/index_daily`（`backend/app/taiwan/index_daily.py:90`），有 OHLC 但沒有成交量（`:113-123`）。
  - 連網逾時 20 秒（`:91`）；抓取失敗會被吞掉（`:144-146,209-211`）；失敗的月份 600 秒內不重試（`:33,170-172`）；當月資料有 300 秒的快取期（`:32,167-169`）。
  - `read_range` 持鎖期間會連網（`:224-227`）。
- 只有個股頁（`backend/app/taiwan/detail_service.py:266-272`）與 K 線 API（`backend/app/api/kline.py:424-437`）會補抓指數，排程不會（附錄 A2）。實查：指數最新到 09-17，日 K 最新到 10-02。
- 手動更新（`backend/app/api/taiwan.py:136-159`）只在 `:152` 的條件成立時重算。
  - 排程更新見 `backend/app/jobs/daily_pipeline.py:176-216`：`run_update` 例外時在 `:200-202` return，日 K 失敗時在 `:210-214` 跳過。
  - 前端只有在 `dates_fetched>0` 時才讓所有查詢失效（`frontend/src/pages/settings/TaiwanHistoryDataCard.tsx:55-62`）。
- 台北時間：`taipei_now()`（`backend/app/taiwan/realtime/calendar.py:34`）。
- 沒有現成功能可以重用：市場情報的 `taiex` 固定是 `unavailable`（`backend/app/taiwan/market_intelligence.py:623-638`）；regime 算的是「全市場 MA20 上方占比」（`backend/app/services/regime_builder.py:247-256`）。

### 2.3 量能與 enriched

- enriched 已有的量能欄位：`vol_ma5`、`vol_ratio_5d`（`backend/app/indicators/pipeline.py:162-164`）。`change_pct` 為小數制（`:131`）；`ENRICHED_COLUMNS` 的定位是「策略可直接使用」（`:109-111`）。
- 台股發佈流程：`filter_halt_days` → `compute_enriched` → `_add_limit_signals`（`backend/app/taiwan/enriched_refresh.py:129-137`），以全部歷史計算並原樣寫入（`:70-82,85-91`）。
  - 實查：666 個分區，都是 76 欄。
  - 平時只重發最近 5 天（`:315-325`）；因子變動時全部重寫（`:105-112`）。
- 策略只讀儲存欄並自行重算（`backend/app/services/screener.py:249-271`）；讀取端不得重算指標（`backend/app/taiwan/enriched_reader.py:1-7`）。
- 停牌過濾：`backend/app/indicators/pipeline.py:1033-1043`。即時熱路徑：`backend/app/services/quote_service.py:884` → `:1744` → `:1683`。
- 台股選股：
  - 只讀最新分區的 `signal_*`（`backend/app/taiwan/screener.py:547,549-567`），篩選為「且」（`:526-531`）。
  - 前端：技術面區塊 `frontend/src/pages/TaiwanScreener.tsx:1250-1252`、訊號籤 `:1295-1328`、表頭 `:1504-1592`、降級提示 `:1496`。
- 撞名：策略「縮量回踩」用 `vol_ratio_5d < 0.8`（`backend/app/strategy/builtin/pullback_to_support.py:16-17,85-87`），與本文的「縮量」不同。6 種型態目前都沒有實作（附錄 A3）。

## 3. #2 策略分級與大盤濾鏡（對應 #2 原話與 D2）

### 3.1 「評分前段」的門檻方案

| 方案 | 規則 | 優點 | 缺點 |
|---|---|---|---|
| A 前 N 名（推薦，N=5） | 每支策略依既有排序，前 5 檔為買進，其餘為觀察 | 和分數尺度無關，沒有評分設定的自訂策略也能依排名分級；快照的 `rank` 可以事後還原 | 入選 342 檔與 6 檔的策略都只給 5 檔；入選 5 檔以下時全部是買進 |
| B 前 N%（20%，至少 1 檔、至多 20 檔） | 依入選數量按比例 | 隨策略規模縮放 | 多一組參數；只有 1 檔時也一定是買進 |
| C 分數 ≥ 60 | 以 0–100 的分數設門檻 | 直觀 | 單檔固定 50，永遠是觀察；沒有 `scoring` 的策略尺度不同，無法套用 |

- 推薦 A：排序本來就由各策略的 `scoring` 決定，取前 N 名等於「評分前段」，而且不受尺度影響。
- 同分時沿用引擎排序後的順序，與快照的 `rank` 一致。
- 推薦規則是「前 5 名且 `score > 0` 才算買進」（Q11-A）。理由：0 分多半代表有因子缺值；同為 0 分時，順序只取決於代號。這條規則會連帶排除少數「每個因子都墊底」的合法 0 分，屬於可接受的代價。0 分但在前 5 名的列記為 `watch`，`grade_reason` 為 `"zero_score"`。目前三支有 0 分的策略，前 5 名都沒有 0 分，所以不受影響。（2026-10-09 起由第 4 階段的 D11 取代：0 分是合法分數，照排名分級；只有分數為 null 才判觀察，`grade_reason` 為 `"score_unavailable"`。）
- `score > 0` 只用於「有評分設定」的策略。
  - 判斷依據與引擎一致：用 `effective_scoring(meta.scoring, overrides)`（`backend/app/strategy/scoring.py:68-79`，引擎在 `engine.py:877,1144` 使用）解析出權重後，套用 `matrix.py:3686-3688` 的條件，也就是存在非 0 權重、而且權重總和大於 0。
  - 沒有評分設定的策略，分數可能是排序欄位的原值，可能全為負（`matrix.py:3744-3758`）；polars 路徑甚至沒有 `score` 欄位（`engine.py:1503-1504`）。自訂策略的 scoring 預設為 `{}`（`engine.py:435`；`ai_generator.py:254` 也允許空值）。這類策略只依排名分級。
  - `score` 缺欄位或為 null 時，不得拋例外：有評分設定的策略判觀察（實作時記 `zero_score`，第 4 階段起記 `score_unavailable`），其他策略忽略分數。

### 3.2 分級規則與欄位

- 分級產生在後端：新增純函式模組 `backend/app/services/pick_grades.py`，提供 `assign_grades(results, market_state, top_n=5)`。`get_cached` 在完成盤中覆蓋與名稱附加之後才呼叫它，API 層保持只做轉接。
  - 這個函式回傳新的 results，每一列都先複製（`{**row, ...}`）再寫入。原因是盤中結果的列與監控引擎共用同一個物件（淺層合併，`backend/app/api/screener.py:321-322`）；直接改會違反 `CONTRIBUTING.md:183`。
  - 看板與策略庫都只讀 `/cached`（`Dashboard.tsx:421-424`、`Strategies.tsx:75-77`），在一處計算，兩邊口徑一致。
- 每一列加上：`grade`：`"buy"` 或 `"watch"`；`grade_reason`：買進時為 `null`；排名在前 N 名之外為 `"outside_top_n"`；有評分設定的策略分數無法計算（null）時為 `"score_unavailable"`（第 4 階段之前是分數為 0 時記 `"zero_score"`）；因大盤而降級時為 `"market_below_ma20"`。第 4 階段另加 `limit_up_locked`（布林值，見文末）。
- 頂層加上：
  - `market_state`（見 3.3）。
  - `grading: {top_n, market_filter}`。`market_filter` 只有三個值，畫面文字一對一，不可混用：

| `market_filter` | 意義 | 畫面文字 |
|---|---|---|
| `applied` | 指數跌破，已經降級 | 「大盤：跌破 20 日均線，買進已降為觀察」 |
| `passed` | 指數未跌破，不降級 | 「大盤：未跌破 20 日均線」 |
| `unavailable` | 無法判定，濾鏡沒有套用 | 「大盤狀態無法判定，濾鏡未套用」 |
- 大盤濾鏡：`market_state.below_ma == true` 時，所有原本是 `buy` 的列都改成 `watch`，`grade_reason` 設為 `market_below_ma20`。
- 大盤狀態無法判定時，推薦 Q2-A：不降級，並標為 `unavailable`。外部 repo 在資料不足時也是不套用濾鏡（`market.py:15-17,29-31`），差別在於它不會告訴使用者。
- 盤中覆蓋的結果：推薦 Q3-A，照樣依排序分級，大盤狀態用最近一個已收盤日（也就是頂層 `as_of`），畫面註明「大盤狀態依 {日期} 收盤」。
- 頂層 `as_of` 為 None 或快取為空（含 `:405-407` 的提早返回分支）時：一律回傳 `market_state: null` 與 `grading: {top_n, market_filter: "unavailable"}`；有列時只依排名分級。

### 3.3 大盤狀態的計算

- T 取頂層 `as_of`（盤後快照的基準日），交易日序列以股票日 K 為準。
- 判斷順序：先看 T 有沒有資料（缺就是 `index_missing`）；再看歷史是否不足 20 天（`insufficient_history`）；最後看 20 日窗口內是否有缺日，或有指數日期不在股票交易日裡（`index_gap`）。
- MA20 含 T 在內。`below_ma = close < ma`，兩者相等時不算跌破；外部 repo 只有 close > ma 才算多頭（`market.py:22`）。
- 只讀本機資料：用 `TaiwanDailyStore(...).scan_range` 讀取；目錄不存在時回 `unavailable`，而且不建立目錄（比照 `backend/app/services/strategy_pick_performance.py:170-171`）。
- 回傳格式：`market_state: {as_of, index_symbol, close, ma, window: 20, below_ma, status, reason}`。計算失敗時只記 log，`/cached` 照常回應。
- 加權指數是價格指數，不含股利，畫面上要註明。

### 3.4 前端

- 看板（`/`）的「策略當日推薦」卡：
  - 每一列的名稱旁加徽章：「買進」用紅色樣式，沿用 `AlertToast.tsx:94`；「觀察」用灰色。
  - 因大盤降級的列，徽章顯示「觀察・大盤」，滑鼠提示寫「加權指數 {日期} 收盤 {close} 低於 20 日均線 {ma}（價格指數，不含股利）」。
  - 卡片的 hint 下方顯示一行大盤狀態，文字依 3.2 的表格，後面接 `INVESTMENT_DISCLAIMER`。
- 策略庫（`/strategies`）的「策略清單」分頁：選股列表的每一列加同樣的徽章；「選股基準日」旁顯示同一行大盤狀態與提示。「事後統計」分頁不動。

### 3.5 MVP 與可選

| # | MVP 項目 | 對應 |
|---|---|---|
| M2-1 | `market_state` 模組：只讀本機資料，判定是否低於 20 日均線 | #2「加權指數收盤跌破 20 日均線時」 |
| M2-2 | 排程與手動更新都無條件補抓加權指數日 K | 同上（需要當日收盤） |
| M2-3 | `pick_grades`：前 N 名為買進，其餘為觀察；有評分設定的策略另外要求 `score > 0`（第 4 階段改為：分數無法計算時判觀察）；跌破時把買進全部降為觀察 | D2；#2「所有 BUY 降為 WATCH」 |
| M2-4 | `/cached` 回傳 `grade`、`grade_reason`、`market_state`、`grading` | 同上 |
| M2-5 | 看板卡與策略清單的徽章、大盤狀態列與「僅供參考」提示 | D2、D3 |

可選：O2-1 事後統計依分級分組。快照已存有 `rank` 與 `score`（`strategy_pick_history.py:41-48,417-423`），再加上訊號日的大盤狀態，可以事後還原分級，不需要改 schema；O2-2 均線天數可設定為 5–120；O2-3 上櫃股改以櫃買指數判定；O2-4 `/cached-result` 等其他端點也加上分級；O2-5 程序內快取；O2-6 把 `index_daily` 改成在鎖外抓取、鎖內寫入（R9）；O2-7 「只看買進」篩選；O2-8 修正既有的不一致：「AI 只用在兩個地方」的說法與實際不符（`README.md:63`、`操作說明書.md:466`、`docs/configuration.md:29`）；O2-9 策略監控的 `buy_signal` 推播（`AlertToast.tsx:94`、`monitor.py:1176`）也隨大盤降級。

## 4. #3 量價型態

### 4.1 計算位置與 B1 決定

| 方案 | 作法 | 成本與風險 |
|---|---|---|
| P2 全管線 `signal_*` | 改 `compute_signals`、`compute_enriched_today` 與 live_agg | 動到即時熱路徑；回測遮罩也要對齊（`backend/app/backtest/strategy.py:1910`） |
| P1 台股發佈 `vp_*`（推薦） | 在 `_add_limit_signals` 之後加 6 個可為 null 的布林欄位 | 盤後算一次；選股本來就會讀該分區；舊分區缺欄位時，在讀取端降級處理 |
| R 讀取端即時計算 | 選股時讀最近 21 個分區 | 每次多讀 21 個檔（推測約 100 ms）；違反 `enriched_reader.py:1-7` |

- 推薦 P1，並維持向後相容（`CONTRIBUTING.md:213`）。若分區缺 `vp_*`，回傳 `degraded_sections: ["volume_patterns"]`，篩選結果為空；不做全量重算。
- B1 採 (a)：`vp_*` 不加進 `ENRICHED_COLUMNS`，改由新模組常數 `VOLUME_PATTERN_COLUMNS` 明確讀取。理由：策略的重算路徑沒有這些欄位，而 AI 評分白名單會自動納入 `ENRICHED_COLUMNS` 中所有非 `signal_` 的欄位（`backend/app/strategy/ai_generator.py:51-55`，檢查在 `:270-276`）。
- 會自動列出欄位的地方，在 (a) 之下都不受影響：`ai_generator.py:56-` 是靜態清單；`pipeline.py:208-223` 的分類表由 `ENRICHED_COLUMNS` 推導；`ALLOWED_FIELDS` 白名單：`custom_signals.py:34-55`、`api/signals.py:63-88`、`custom_signals_ai.py:35-48`；監控依 `signal_`／`csg_` 前綴判斷：`api/monitor_rules.py:149-158`、`strategy/monitor_rules.py:47,99-101`、`strategy/monitor.py:204,227,283`；回測與引擎同樣依前綴：`backtest/strategy.py:164,439,1915`、`backtest/matrix.py:2817`、`backtest/engine.py:164`、`strategy/engine.py:1005`；`taiwan/screener.py:547`。
- 有兩處會帶入 `vp_*`，都只發生在降級狀態，影響低：`services/screener.py:268-270`、`backend/app/repository.py:666`。`api/data.py:169` 的欄位數會從 76 變成 82，只影響顯示。匯出功能未查到（附錄 A5）。

### 4.2 前端位置

- 「台股選股」頁（`/taiwan-screener`）→ 進階條件 → 技術面：新增「量價型態（多選為且）」6 個籤，滑鼠提示顯示公式。
- 結果表在「成交量」欄後新增「量價型態」欄。資料未發佈時顯示「—」，並沿用 `TaiwanScreener.tsx:1496` 的降級提示。
- 不放進異常訊號清單：那份清單依 `signal_count` 排序（`backend/app/taiwan/abnormal_diagnostics.py:679-688`）。

### 4.3 MVP 與可選

| # | MVP 項目 | 對應 |
|---|---|---|
| M3-1 | `volume_patterns` 純函式，定義依 4.4 | #3 原話與各條定義 |
| M3-2 | 發佈時寫入 `vp_*`，不登錄到 `ENRICHED_COLUMNS` | 同上（偵測全市場） |
| M3-3 | 選股 API 的型態篩選，以及每列命中的型態 | 同上（偵測） |
| M3-4 | 選股頁的型態籤、結果欄與公式提示 | 同上（顯示） |

可選：O3-1 個股頁標籤（`frontend/src/pages/TaiwanStockDetail.tsx:521`）；O3-2 放進異常訊號清單；O3-3 監控規則（需要 P2）；O3-4 自訂訊號；O3-5 回測；O3-6 全量重發佈；O3-7 把型態納入分級加減分（外部 repo 的作法）。

### 4.4 採用的定義

符號：V 是成交量；C、O、H 是收盤、開盤、最高價；t 是交易日。

- 輸入：發佈中的 enriched frame。OHLC 已前復權；成交量只在股數事件時調整（`CONTRIBUTING.md:104-106`）；`change_pct` 是小數制，事件日以參考價為分母（`:107-109`）。
- 「有成交」的定義：該交易日有資料列，而且 V > 0。交易日序列取自日 K store。型態所需的任一日沒有成交，結果就記 null（不可判定）。

| 型態（欄位） | 條件 | 與外部 repo 的差異 |
|---|---|---|
| 倍量 `vp_double` | t-1、t 有成交；`V_t >= 2*V_{t-1}` | 外部依資料列，本專案依交易日 |
| 梯量 `vp_step_up` | t-2..t 有成交；`V_{t-2} < V_{t-1} < V_t`；`C_t > C_{t-2}` | 比較基準同外部（Q6 可改）；本專案用前復權價 |
| 縮量 `vp_step_down` | 同上，但量遞減，且 `C_t < C_{t-2}` | 同上 |
| 低量 `vp_low` | t 當日須有成交（`V_t = 0` 記 null）；前 20 日至少 15 日有成交；`V_t < 0.6*`這些日子的均量 | 外部沒有門檻（見 Q8） |
| 平量 `vp_flat` | t-2..t 有成交；`(max-min)/max < 0.15` | 相同 |
| 放量滯漲 `vp_up_stall` | 前 5 日都有成交，且均量 > 0；`V_t >= 1.5*`均量；並滿足任一：`C < O`、`body > 0 and upper > 2*body`、`0 <= change_pct < 0.01` | 外部用 `0 < chg`（見 Q10）；外部有 `vol_5ma > 0` 防護（`volume.py:79`），本專案依上述「有成交」定義，並明寫此防護 |

- `body = abs(C-O)`，`upper = H - max(C,O)`。不移植外部的加減分。梯量與縮量互斥。

### 4.5 二開分級與快取層

- 三項改動都屬 L3：前端插槽只有 `layout.navigation.extra`、`stock-preview.footer`、`watchlist.toolbar`（`docs/secondary-development.md:23`）；後端擴充點只有路由、啟動鉤子與 `NotificationFormatter`（`:22,24,248,250`），無法介入排程、`/cached` 或發佈流程；熱點只有 `api.ts`（`:292-300`）。
- 受影響的快取層（依 `CONTRIBUTING.md:171` 列出）：指數服務單例的月份狀態（`index_daily.py:94-99`）；前端 `/cached`，staleTime 60 秒（`Dashboard.tsx:421-425`）；enriched 分區，以原子替換寫入（`enriched_refresh.py:70-82`）；`['taiwanScreener', payload]`，staleTime 10 秒（`TaiwanScreener.tsx:245-249`）；分級沒有後端快取，每次請求時計算。

## 5. 實作步驟（依序；每步可獨立建置與測試）

後端驗證範本（在 repo 根目錄，PowerShell 5.1 執行）：
- 必須先停掉後端，否則 `data/backend.log` 會造成比對誤報；`backend/tests/conftest.py` 沒有隔離 data_dir。
- `DATA_DIR` 只在 `cmd /c` 內設定；`Compare-Object` 必須沒有輸出。
- 測試要注入假 client，並 monkeypatch `httpx.Client.send` 使其拋錯，確保不連網。

```powershell
$d="$env:TEMP\mfvp-$(Get-Random)"; New-Item -ItemType Directory -Force "$d\taiwan" | Out-Null; Copy-Item -LiteralPath data\taiwan\security_master.parquet -Destination "$d\taiwan\"; $s={Get-ChildItem -LiteralPath data -Recurse -Force | ForEach-Object {"$($_.FullName)|$($_.LastWriteTimeUtc.Ticks)|$($_.Length)"}}; $b=& $s; cmd /c "set DATA_DIR=$d&& uv run --directory backend --frozen pytest <TESTS> -q && uv run --directory backend --frozen ruff check <FILES>"; Compare-Object $b (& $s)
```

前端驗證：`$env:Path="$env:APPDATA\npm;$env:Path"; pnpm --dir frontend test -- <名稱>; pnpm --dir frontend build`。`frontend/pnpm-lock.yaml` 與 `frontend/vite.config.d.ts` 已有未提交的修改：不要還原，也不要混進這次的提交。

### 原則調整

1. **M0-1 文件**：依第 1 節的表格修改 README、操作說明書、`docs/features.md`，以及在 `docs/strategy-pick-tracking-plan.md` 加註。完成後執行 `git diff --check`，再用 `rg -n "不推薦個股|不預測漲跌|不構成" README.md 操作說明書.md docs/features.md` 核對：剩下的結果只能是第 1 節判定「保留」的列。
2. **M0-2 UI 與測試**：
   - 新增 `disclaimer.ts`，修改上表 4 處 UI 文案。
   - 依第 1 節「測試」的逐項改法，修改 `Strategies.test.tsx` 並刪除後端的 `BANNED`。
   - 前端 `<名稱>`= `Strategies Onboarding TaiwanScreener`
   - 後端 `<TESTS>`= `tests/test_screener_pick_performance_api.py`；`<FILES>` 同一檔，以確認沒有遺留 F401。

### #2

3. **M2-1 `backend/app/taiwan/market_state.py`**：
   - 新增 `tests/test_taiwan_market_state.py`，案例包括：低於、高於、相等、不足 20 天、T 缺、窗口缺日、指數多出不在交易日序列的日期、三種狀態同時成立時的優先順序、目錄不存在（不得建立目錄）。
   - `<TESTS>`= `tests/test_taiwan_market_state.py tests/test_taiwan_index_daily.py`
4. **M2-2 補抓指數**：
   - `backend/app/jobs/daily_pipeline.py` 新增 `refresh_benchmark_index(label) -> date | None`：用 `read_range(TAIEX.symbol, 台北日期往前約 60 日, 台北今日, refresh_current_month=True)` 補抓，回傳補抓後本機最新的指數日期；例外一律攔下並記 log。
   - 繞過快取期：`index_daily.py` 的 `read_range`／`ensure_range` 新增關鍵字參數 `refresh_current_month`（預設 False）。設為 True 時，在持鎖狀態下先清除該 symbol 的 `_current_month_fetched_at`（`:94,167-169`），保證當月一定會重抓。個股頁的既有行為不變。
   - 插入點與條件判斷無關：排程：放在 `_scheduled_taiwan_update` 開頭（`:183` 的 try 之前），即使 `run_update` 拋例外也會先補抓；手動：放在 `update_taiwan_history_to_latest` 開頭（`backend/app/api/taiwan.py:139` 的 try 之前）；回應加上 `benchmark_index_latest_date`，並同步修改 `api.ts:3285` 的 `TaiwanUpdateLatestResult` 型別。
   - 已完成的月份會略過（`index_daily.py:173-183`），但往前 60 日通常跨 3 個月份，冷啟動或月份尚未完整時要發 3 次請求；`httpx` 的 20 秒逾時是分階段計算，不是總時限（見 R9）。單元測試要 patch `app.taiwan.index_daily.get_index_daily_service`：`refresh_benchmark_index` 在函式內 import 它，patch 模組屬性才會生效。
   - 測試隔離：不靠逐一點名，改在 `backend/tests/conftest.py` 加一個 autouse fixture，把 `app.jobs.daily_pipeline.refresh_benchmark_index` 換成回傳 None 的 stub：自己的單元測試用 marker `real_benchmark_refresh` 退出這個 fixture，比照既有 `real_auth` 的做法（`backend/tests/conftest.py:17-32`）；手動端點必須在函式內 `from app.jobs.daily_pipeline import refresh_benchmark_index`，比照 `taiwan.py:153`，patch 才會生效。
   - 會走到兩個插入點的既有測試完整清單（`rg -n "update_taiwan_history_to_latest|update-latest|_scheduled_taiwan_update|start_scheduler|\.func\(" backend/tests`）：手動路徑共 7 個：`test_update_latest_refreshes_caches.py` 的 `:33`、`:42`、`:53`（三者都經 `_run` `:22-30`）、`:63-81`、`:84-95`、`:288-307`，以及 `test_taiwan_bootstrap.py:435`；排程路徑：沒有測試會執行 `_scheduled_taiwan_update`。`test_taiwan_schedule_split.py:28-42` 只註冊 job 再關閉，沒有呼叫 job 的函式；其他 `run_update` 測試都直接呼叫服務，不經過這個包裝函式；以上都由 autouse fixture 覆蓋。`:53-60` 改為斷言「有補抓、沒有重算」；`:84-95` 加斷言「`update_to_latest` 拋例外前已補抓」。
   - 新增案例：補抓拋錯時排程仍會重算；`dates_fetched=0` 時仍會補抓；`run_update` 拋例外時也會補抓；`refresh_current_month=True` 會繞過 300 秒快取期。
   - `<TESTS>`= `tests/test_update_latest_refreshes_caches.py tests/test_taiwan_bootstrap.py tests/test_taiwan_index_daily.py tests/test_taiwan_schedule_split.py`
5. **M2-3、M2-4 分級與 API**：
   - 新增 `backend/app/services/pick_grades.py` 與 `tests/test_pick_grades.py`。案例：前 N 名的邊界；入選少於 N 檔；同分；前 5 名內的 0 分列改判觀察（第 4 階段改為 null 分數判觀察、0 分照排名）；無評分設定的策略（負分、或整個沒有 `score` 欄位、或值為 null）前 5 名仍為買進，而且 `/cached` 回 200；`market_filter` 三種值：跌破時全部降級、`passed`、`unavailable` 時不降級；盤中覆蓋；`as_of` 為 None；呼叫後輸入的列不被修改。
   - `get_cached` 在回傳前呼叫 `assign_grades`；空快取分支（`backend/app/api/screener.py:405-407`）回傳 `market_state: null` 與 `grading`。
   - 擴充 `tests/test_screener_cache_api.py`：涵蓋有值、模組拋錯時仍回 200、空快取。
   - `<TESTS>`= `tests/test_pick_grades.py tests/test_screener_cache_api.py tests/test_screener_cached_names.py tests/test_taiwan_market_state.py`
6. **M2-5 前端**：
   - 修改 `api.ts`：`screenerCached` 的型別加上 `grade` 等欄位。
   - 修改 `Dashboard.tsx` 與 `Strategies.tsx` 的清單分頁。
   - `TaiwanHistoryDataCard.tsx:55-62` 補上 `QK.screenerCached()` 失效，讓補抓後的狀態立即反映在畫面上。
   - 擴充 `Dashboard.test.tsx`、`Strategies.test.tsx`。案例：買進／觀察徽章、降級提示、`market_filter` 三種值各自對應 3.2 表格的文字、盤中加註；新增的大盤狀態列要含 `INVESTMENT_DISCLAIMER`；事後統計分頁不出現徽章。
   - `<名稱>`= `Dashboard Strategies TaiwanHistoryDataCard`
7. **聯調**（先重新啟動後端；2026-10-02 實查時，指數停在 09-17）：按「更新至最新交易日」：即使 `dates_fetched=0`，`benchmark_index_latest_date` 也應等於當時日 K 的最新交易日；檢查 `/api/screener/cached`：`market_state.as_of` 等於頂層 `as_of`，且各列都有 `grade`；在畫面上核對徽章，以及窄螢幕、深淺色兩種模式；更新 `docs/features.md` 的「看板」「策略庫」兩段，最後執行 `git diff --check`。

### #3

8. **M3-1 `backend/app/taiwan/volume_patterns.py`**（`add_volume_patterns`、`VOLUME_PATTERN_COLUMNS`）：
   - `tests/test_taiwan_volume_patterns.py`：用固定樣本逐值斷言。邊界涵蓋：剛好 2.0 倍、15%、60%、1.5 倍；`change_pct` 剛好 0 與 0.01；十字線；量為 0；停牌缺日；15/20 門檻；除息日；分割日。
9. **M3-2 發佈掛點**：在 `backend/app/taiwan/enriched_refresh.py:137` 之後呼叫，交易日序列用 `self.daily_store.available_dates()`；不改 `ENRICHED_COLUMNS`。
   - 測試：發佈後的分區含 `vp_*`；舊分區缺欄位時，`scan_enriched_parquet` 仍能讀取。
   - 在 `tests/test_ai_generator_prompt.py` 斷言：`VOLUME_PATTERN_COLUMNS` 不在 `ENRICHED_COLUMNS` 與 `_POLARS_SCORING_FIELDS` 中，`/api/monitor-rules/options` 也不含 `vp_*`。
   - `<TESTS>`：`test_taiwan_volume_patterns`、`test_taiwan_enriched_refresh_write`、`test_ai_generator_prompt`、`test_custom_signals_ai`、`test_taiwan_screener`，以及其他會呼叫發佈流程的測試：`test_taiwan_daily_update`、`test_taiwan_forward_adjustment`、`test_taiwan_reference_prices`、`test_taiwan_provisional_refresh`、`test_taiwan_corporate_actions`、`test_taiwan_watchlist_enrichment`、`test_taiwan_research_context`、`test_taiwan_market_institutional`、`test_taiwan_share_capital_store`、`test_screening_basis_date`、`test_taiwan_bootstrap`（皆位於 `tests/`，檔名加 `.py`）。另外記錄發佈耗時。
10. **M3-3 選股 API**：每列結果加上 `volume_patterns: list[str]`。
    - 篩選欄位不能直接加進 `TaiwanScreenerRequest`：AI 解析的白名單是由這個模型的欄位動態產生的（`backend/app/taiwan/screener_nl.py:188`），加進去等於擴大 AI 的輸出驗證，違反 D1。
    - 做法：在 `taiwan/screener.py` 新增子類別 `TaiwanScreenerRunRequest(TaiwanScreenerRequest)` 並加入 `volume_patterns`，只給 `run_taiwan_screener`（`backend/app/api/taiwan.py:424-425`）使用；`screener_nl.py` 不改。
    - 從 `_join_signals` 已讀入的分區，依 `VOLUME_PATTERN_COLUMNS` 取值；缺欄位時列入 `degraded_sections`。
    - 服務端用 `getattr(req, "volume_patterns", None)` 讀取，因為既有測試會直接傳入基底 request（`backend/tests/test_taiwan_screener.py`、`test_taiwan_institutional_margin.py:283`）。
    - 測試涵蓋：多選取「且」、未知 id、缺欄位、null 不算命中，以及斷言 `"volume_patterns" not in TaiwanScreenerRequest.model_fields`。最後一項放在 `test_taiwan_screener.py`，AI 測試檔不動。
    - `<TESTS>`= `tests/test_taiwan_screener.py tests/test_taiwan_screener_nl.py tests/test_taiwan_institutional_margin.py`
11. **M3-4 前端**：
    - `signals.ts` 新增 `VOLUME_PATTERN_DEFINITIONS`。
    - `api.ts` 只改選股型別（`:1026-1075` 附近）。
    - 修改 `TaiwanScreener.tsx`。`:1496` 的降級提示目前只判斷 `includes('indicators')`，要另外加上 `includes('volume_patterns')` 的條件，文字為「量價型態尚未發佈」。
    - `TaiwanScreener.test.tsx` 只對新元素斷言：payload、新欄位、降級提示、公式提示。
12. **聯調**：等盤後發佈或手動更新之後，逐一勾選 6 種型態，抽 3 檔人工核算；更新 `docs/features.md`，執行 `git diff --check`。

## 6. 風險

- R1 指數過期：排程與手動補抓都失敗時，狀態為 `unavailable`，濾鏡不套用；300 秒快取期已用 `refresh_current_month` 繞過；但若 16:30 時 MI_5MINS_HIST 還沒有當日收盤（推測，聯調時確認），T 日要等到 21:40 那一班或手動更新才會補齊。`refresh_current_month` 也繞不過前一個月份 600 秒的失敗冷卻（`index_daily.py:170-172`）：月初時，若個股頁在 16:30 前 10 分鐘內抓前月失敗，排程會略過前月，20 日窗口缺日，判為 `index_gap`，可能要到 21:40 才恢復。
- R2 分級被當成保證：靠 `INVESTMENT_DISCLAIMER` 與徽章旁的提示說明。前 N 名只代表「該策略評分前段」，評分方式寫在既有的策略規則說明中。
- R3 口徑不一致：AI 報告仍寫「無買賣推薦」（D1 不改 AI），與選股卡上的「買進」並存。第 1 節已判定兩者分別描述 AI 與策略功能，但使用者仍可能困惑；可在 O2-8 一併處理。
- R4 門檻任意：N=5 是產品選擇，不是統計結論，可用 O2-1 的事後統計分組驗證。0 分的處理見 Q11。
- R5 混合 schema：`scan_enriched_parquet` 能容忍新增欄位；`regime_builder.py:364` 會失敗，但例外被 `:441-443` 吞掉，而且在模組外沒有呼叫者。
- R6 發佈耗時增加，推測不到 1 秒；第 9 步會量測。
- R7 「縮量」與策略「縮量回踩」撞名，公式提示要寫清楚定義。
- R8 熱點衝突：三項改動都會動到 `api.ts`，請分開提交，並在 PR 標出複核點（`docs/secondary-development.md:286`）。
- R9 持鎖期間連網，違反 `CONTRIBUTING.md:184`：根源在 `index_daily.py:224-227` 這個既有寫法。TWSE 連不上時，手動按鈕最壞要等 3 個月份、每次各自超過 20 秒的逾時，約 1 分鐘以上（推測）；這段期間個股頁讀加權指數也會一起被擋住；排程只會延後重算。修法見 O2-6。
- R10 即時合併改寫分區時，自選股的 `vp_*` 可能變成 null（推測，`repository.py:2299-2318`）。
- R11 自訂策略的分數口徑不一：沒有評分設定的策略只依排名分級（3.1）。若實作直接比較 `row["score"] > 0`，會讓這類策略全部變成觀察，或遇到 null 時拋錯，導致 `/cached` 回 500。目前 `data/strategies` 不存在（`ls` 實查），以第 5 步的測試鎖住。

## 7. 需要使用者拍板的問題

1. 「評分前段」的門檻？A 每支策略前 5 名（推薦）；B 前 20%，至少 1 檔、至多 20 檔；C 分數 ≥ 60。
2. 大盤狀態無法判定時要不要降級？A 不降級，`market_filter` 為 `unavailable`（推薦）；B 全部降為觀察；C 當日不分級。
3. 盤中覆蓋的結果怎麼處理？A 照樣分級，大盤狀態用最近一次收盤並標出日期（推薦）；B 盤中結果不分級。
4. 卡片標題「策略當日推薦」要不要改？A 保留，新原則允許（推薦）；B 改成「策略當日選股」；C 改成「策略買進／觀察」。
5. 「放量滯漲」的顯示名稱？A 沿用「放量滯漲」，新原則允許帶判斷的用詞（推薦）；B「量增未收高」；C「量增價平」。
6. 梯量、縮量比較價格的基準？A 收盤對 2 日前收盤，同外部（推薦）；B 對前一日；C 逐日比較。
7. 長上影的判定？A 實體 > 0 且上影 > 2 倍實體，同外部（推薦）；B 上影 ≥ 振幅 50%；C 上影 ≥ 前收 2%。
8. 低量的成交日門檻？A 20 日中至少 15 日（推薦）；B 20 日全部；C 不設門檻。
9. 量價型態多選時的語意？A「且」（推薦）；B「或」。
10. 放量滯漲是否計入平盤？A 計入，即 `0 <= change_pct < 0.01`（推薦）；B 不計入，即 `0 < change_pct < 0.01`，同外部；C 改以前收計算，並排除平盤。
11. 前 5 名中分數為 0 的列怎麼處理？A 有評分設定的策略只有 `score > 0` 才算買進，0 分記為觀察（`zero_score`）；沒有評分設定的策略只看排名（推薦）；B 不特別處理，只看排名；C 往後遞補，取前 5 個分數大於 0 的列。（2026-10-09 第 4 階段改採 D11：0 分合法，只有 null 判觀察，Q11-A 已被取代。）

## 8. 相依與順序

- 原則調整和 #2 的 UI 文案都會用到 `disclaimer.ts`，所以先做原則調整（步驟 1–2），再做 #2（步驟 3–7）。
- #3（步驟 8–12）與前兩者沒有程式相依，但要等盤後發佈才能聯調。

## 實作結果（2026-10-03）

- 新增共用徽章元件 `frontend/src/components/PickGrade.tsx`（看板與策略庫共用），型態定義放在 `frontend/src/lib/signals.ts`。
- 分級依 `/cached` 列序；有評分設定的判斷沿用 `effective_scoring` 與 matrix 的權重條件；複合策略、引擎查不到的策略只看排名（第 4 階段起，複合策略只要有子策略有評分設定，就當成有評分設定，見下一節）。評分設定在請求當下讀取，可能與快取計算時不同。
- 「有成交」定義為有資料列、OHLC 皆非 null 且量 > 0；`_enrich_price_limits` 改為 `infer_schema_length=None`，避免稀疏的布林欄推斷失敗。
- 尚未處理的建議：自訂 polars 策略若評分欄位全無效，前 5 名會全為觀察；補抓指數為同步執行、無總時限（R9）。

## 第 4 階段後的變更（2026-10-10）

第 4 階段（策略與選股）改了分級規則與每列的欄位，以 `backend/app/services/pick_grades.py` 為準：

- 分數：有評分設定的策略，候選缺任一評分輸入時分數為 null，候選保留並排在最後。0 分是合法分數（每個因子都在當日最低），照排名分級。這取代了 Q11-A。
- `grade_reason`：`zero_score` 改為 `score_unavailable`，只在分數為 null 時出現，不論排名都判觀察。判定順序是 `score_unavailable`、`outside_top_n`、`market_below_ma20`。畫面徽章顯示「觀察・資料不足」，滑鼠提示列出缺值的評分欄位（「評分欄位缺值：…」，複合策略逐一列出子策略與欄位）；沒有這項資訊的舊快取列顯示「評分資料不足（分數 —）」。
- 複合策略：只要有任一子策略有評分設定（保存的子策略設定優先於 META），就當成有評分設定。複合分數由能評分的子策略依權重合併，只有沒有任何子策略能評分的候選，分數才是 null。只有引擎查不到的策略只看排名。
- 「漲停鎖死」：每列新增布林欄位 `limit_up_locked`，取自該列的 `signal_limit_up`（收盤後是已發佈的旗標，盤中是即時旗標）。它和分級分開計算，不改變 `grade`、`grade_reason` 或排名；前端在徽章旁加「漲停鎖死」標籤，滑鼠提示「價格已在漲停價，買單難以成交」。
- 實測（2026-10-10 的前後對照，21 支策略、2026-09-09 ～ 10-08 共 20 個交易日）：
  - 判觀察的 `zero_score` 列 293 筆，改版後 `score_unavailable` 13 筆（都是上市 30–60 個交易日、還沒有 `momentum_60d` 的 `low_volatility_leader` 選股）。
  - 買進從 1,031 列變成 1,360 列，主要來自兩處：`bullish_alignment`、`low_volatility_leader`、`pullback_to_support` 的前 5 名不再因 0 分判觀察；`factor_rank_research` 改成每天選出 20 檔。
  - 1,677 列帶「漲停鎖死」，其中 607 列在前 5 名（前 5 名共 1,588 列）。
- 加權指數長區間 K 線（與 R9 同源）：K 線暖機改為 539 天後，365 天的加權指數圖會在請求中同步回補本機缺少的較早月份（每個月份 20 秒逾時，失敗的月份 600 秒後才重試）。網路連不上時，最壞約每 10 分鐘卡住約 80 秒。

## 附錄：否定結論用過的搜尋（唯讀）

- A1 原本沒有 BUY／WATCH 分級：`rg -n -w "WATCH" backend/app frontend/src`，0 筆；`rg -n "['\"]BUY['\"]" backend/app frontend/src`，只命中法人的 subtype（`abnormal_diagnostics.py:473,495`）。小寫的 `buy_signal` 是監控推播用的，見 2.1 與 O2-9。
- A2 排程沒有更新指數：Grep `get_index_daily_service|index_daily|TaiwanIndexDailyService`，台股只命中 `detail_service.py:271`、`kline.py:436`；`rg -n "ensure_range|read_range\(" backend/app/jobs backend/app/taiwan/daily_update.py backend/app/taiwan/bootstrap.py`，0 筆；另以分區日期交叉核對。
- A3 沒有量價型態：`rg -n "倍量|梯量|縮量|低量|平量|放量滯漲|量增價平" backend/app frontend/src`，只命中「最低量比」與「縮量回踩」；`rg -n -i "volume_pattern|vol_pattern|volumePattern" backend frontend/src`，0 筆。
- A4 快取各列沒有 `rank`：以唯讀 Python 列出 `strategy_cache.json` 各列的鍵，沒有 `rank`；`rg -n "rank" backend/app/strategy/monitor.py`，0 筆。
- A5 沒有匯出功能：`rg -n "write_csv|to_csv|text/csv" backend/app`，0 筆。
- 其他依據：測試清單：`rg -l "TaiwanEnrichedRefreshService|refresh_taiwan_enriched|publish_enriched" backend/tests`；原則宣告：`rg -n "不預測|不推薦|不構成|自動下單" README.md 操作說明書.md docs/*.md`。
