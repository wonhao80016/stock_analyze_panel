# 策略選股事後統計：實作規劃

> 本文件已封存（2026-10-25）：計畫已實作完成，部分內容已與現行程式不符，只保留作為紀錄。文中提到的 market-filter-volume-patterns-plan 位於 `docs/archive/market-filter-volume-patterns-plan.md`。

- 日期：2026-10-02（第八版，依七輪獨立驗收修正）；狀態：已實作並上線（2026-10-02），實作結果與偏差見第 7 節。
- 本文件的 `路徑:行號` 是實作前的位置，實作後已偏移，請以程式碼為準。
- 需求：每天記錄盤後策略選股結果，事後計算報酬與「報酬為正比例」，讓使用者判斷哪些策略有用。
- 標註規則：`路徑:行號` 為本次親自讀到的內容；「推測」為未實跑或未逐行核對的推論；「未查到」為搜尋無結果。
- 參考 kevin801221/stock-strategies-only 的 performance.py 只取概念（T+1 開盤、T+1/5/10/20、停利停損）；本次未讀取其原始碼。

## 0. 結論摘要

1. 記錄點：`refresh_strategy_cache()` 算完後寫入「選股快照」，快照只來自 `as_of` 等於該訊號日的計算，以 (訊號日, 策略) 為單位整批替換。日 K 出現晚於 T 的分區後，禁止再覆寫；在那之前、預測開盤之後，只允許策略指紋不變的覆寫。首次寫入一律保存；延遲只依執行清單上固定不變的 `first_recorded_at` 判定（晚於實際 T+1 開盤即延遲），預設不計入統計。
2. 儲存：`data/user_data/strategy_picks/date=YYYY-MM-DD/part.parquet`，一列代表一筆（訊號日, 策略, 標的）。
3. 報酬：不存結果，查詢時用日 K store 原始價加公司行動因子即時計算。除權息資料不可用時 fail-closed，絕不改用未還原價。
4. 不重用 `backend/app/backtest/`，另寫輕量模組，只重用日 K、公司行動、漲跌停等既有函式。
5. 前端：「策略庫」頁（`/strategies`）新增分頁「事後統計」，二開分級為 L3。
6. MVP 不回補，上線日起累積；第一批 T+20 結果約在 20 個交易日後出現。

## 1. 現況（已核實）

- 快取只存最新結果：`data/user_data/strategy_cache.json`（`backend/app/services/strategy_cache.py:45-46`）。換日時整份替換，同日依策略合併（`backend/app/services/strategy_cache.py:141-144`）。
- `compute_and_cache_all()`（`backend/app/api/screener.py:546-637`）：`as_of` 預設取 `latest_date()`（`backend/app/api/screener.py:562-566`），回傳的 `as_of` 是 `date`（`backend/app/api/screener.py:637`）；各策略結果裡的 `as_of` 是字串（`backend/app/api/screener.py:624`）；21 支內建策略都是 `matrix_native`（例：`backend/app/strategy/builtin/boll_breakout.py:38`），走 `backend/app/strategy/engine.py:852-862` → `backend/app/strategy/engine.py:1245-1264`；每列取自 `context.history`（無則 `context.current`，`backend/app/strategy/engine.py:1167-1181`）中 `date==as_of` 的列（`backend/app/strategy/engine.py:1398-1408`），enriched 的 `date` 為 pl.Date。
- `latest_date()` 取「有官方日 K 且已發佈 enriched」的最近交易日（`backend/app/services/screener.py:434-458`、`backend/app/repository.py:1424-1444`）；完全沒有 enriched 分區時退回最新日 K 日（`backend/app/repository.py:1444`）。
- 會呼叫 `refresh_strategy_cache()` 的有兩處，都不傳 `as_of`（`backend/app/jobs/daily_pipeline.py:92`）：16:30 與 21:40 排程（`backend/app/jobs/daily_pipeline.py:178-179,182-215`），只有日 K 狀態為 `failed` 才跳過，`partial` 也會重算（`backend/app/jobs/daily_pipeline.py:173-177`）；「更新至最新交易日」按鈕，只在 `dates_fetched` 或 `enriched_published` 為真時重算（`backend/app/api/taiwan.py:152-157`）。
- 排程用記憶體 jobstore（`backend/app/jobs/daily_pipeline.py:103`），`misfire_grace_time=3600`（`backend/app/jobs/daily_pipeline.py:192,211`）只在排程器已在執行時生效；觸發當下後端沒開，那一班就不會補跑。
- `POST /api/screener/run_all` 接受任意 `as_of` 與 `strategy_ids`，並寫入同一份快取（`backend/app/api/screener.py:640-696`）；`run_preset` 也會改寫快取（`backend/app/api/screener.py:198-209,304`）。前端只在 `frontend/src/lib/api.ts:2534-2535,2544` 定義這兩支，沒有呼叫端。
- 策略：`backend/app/strategy/builtin/` 有 21 個檔，`factor_rank_research` 為 `research_only`（`backend/app/strategy/builtin/factor_rank_research.py:20`），被排除（`backend/app/api/screener.py:580-586`）；`data/strategies/` 不存在，所以實際執行 20 支。每支依 score 排序後取前 N 筆（`backend/app/strategy/engine.py:1257-1264`），上限多為 500（例：`backend/app/strategy/builtin/boll_breakout.py:35`）。
- 失敗隔離：單支策略失敗會被跳過（`backend/app/strategy/engine.py:1087-1103`），但共用矩陣建構在 try 之外（`backend/app/strategy/engine.py:1063-1083`），`validate_context` 也在 try 之外（`backend/app/strategy/engine.py:1045-1046`），兩者拋錯都會讓整批不記錄（推測，未實跑）。
- 實際資料（2026-10-01，唯讀查詢）：快取 `as_of=2026-10-01`，`updated_at` 為 21:40:42；20 支策略、980 筆（`sum(total)` 與 `sum(len(rows))` 兩種算法一致）；不重複標的 497 檔；檔案 3.77 MB。
- 日 K store 存原始價（`backend/app/taiwan/daily_store.py:7-9,38-44`），提供 `scan_range`、`available_dates`、`incomplete_dates`（`backend/app/taiwan/daily_store.py:254-288,294-314`）。排程的 `run_update` 會重抓近 20 個分區中暫定或不完整的分區（`backend/app/taiwan/daily_update.py:378-385`），重抓失敗只記為 `repair_failed`（`backend/app/taiwan/daily_refresh.py:190-195`）；手動按鈕從最新日的隔天開始抓，不修補最新日（`backend/app/taiwan/bootstrap.py:457-458`）。enriched 發佈失敗只記入備註、不改日 K 狀態（`backend/app/taiwan/daily_update.py:208-213,499-504`）。
- enriched 的 OHLC 為前復權，`raw_*` 沒有 open（`CONTRIBUTING.md:100-101`，並以快取欄位清單核對）。
- 公司行動因子含現金股利（`backend/app/taiwan/corporate_actions.py:371-405`）；計算函式為 `forward_adjusted`、`factors_between`、`attach_reference_prices`（`backend/app/taiwan/corporate_actions.py:408-434,296-332`）。規範要求直接讀日 K 算報酬時使用這些函式（`docs/secondary-development.md:42-47`）。
- 事件讀取會把失敗當成「沒有事件」：`read()` 缺檔回空表（`backend/app/taiwan/corporate_actions.py:199-200`）；`read_events_or_empty` 吞例外（`backend/app/taiwan/corporate_actions.py:271-282`）；`fetch_state()` 讀不到回 `{}`（`backend/app/taiwan/corporate_actions.py:161-170`），格式錯誤的來源項目 `_coverage` 回 None（`backend/app/taiwan/corporate_actions.py:264-268`）；覆蓋只在成功抓取後更新，最多到當天（`backend/app/taiwan/corporate_actions.py:573-579`）。共 6 個來源（`backend/app/taiwan/enrichment/corporate_actions.py:354-362`），實際 `covered_through` 都是 2026-10-01。
- 交易日曆：專案的實作是 `TaiwanTradingCalendar`（`backend/app/taiwan/realtime/calendar.py:44-79`），開盤時間 `REGULAR_OPEN_START=09:00`（`backend/app/taiwan/realtime/calendar.py:22`）。建構時沒有預載休市日（`backend/app/taiwan/chip_history.py:68` 有註明），未知的平日回傳 None，專案慣例把 None 視為可能交易日（`backend/app/taiwan/daily_refresh.py:40-46`）；建構子可以傳入 `known_holidays`、`known_trading_days`（`backend/app/taiwan/realtime/calendar.py:51-57`）。
- 測試隔離（實作當時）：`backend/tests/conftest.py:1-32` 沒有隔離 `data_dir`；2026-10-23 起已由 conftest 隔離，見 `CONTRIBUTING.md` 第 9 節。`DATA_DIR` 會注入 `settings.data_dir`（`backend/app/config.py:24,120-121`），`backend.log` 也跟著它（`backend/app/main.py:68`）。證券主檔是全域單例（`backend/app/taiwan/universe/__init__.py:21-26`），缺檔時會連網抓取（`backend/app/taiwan/universe/service.py:134-143`）。

## 2. 範圍：MVP 與可選

| # | MVP 項目 | 對應需求 |
|---|---|---|
| M1 | 盤後選股快照與逐策略執行清單（含 0 筆與指紋）：凍結前可冪等覆寫；記錄首次寫入時間與完整性 | 記錄每天的選股 |
| M2 | 單筆報酬：以 T+1 開盤價為起點，前復權收盤算 T+1/5/10/20；標出開盤漲停、無成交、除權息資料不可用 | 事後計算報酬 |
| M3 | 各策略彙總：筆數、未成交與排除筆數；各持有期的樣本數、平均報酬、中位數報酬、報酬為正比例 | 報酬與勝率 |
| M4 | 兩支唯讀 API：彙總與單一策略明細 | 把 M2、M3 交給畫面 |
| M5 | 策略庫「事後統計」分頁：持有期切換、彙總表、逐筆紀錄、計算方式說明 | 讓使用者看到並判斷 |

可選項目（報酬是即時計算，之後加欄位不必遷移資料）：
- O1 停利 +10%／停損 -8%，從 T+2 起判定（現股不得當日出場：`backend/app/taiwan/market_rules.py:366-376`）。
- O2 相對加權指數 `t00.TWSE` 的超額報酬（`backend/app/taiwan/benchmarks.py:19`）。這是價格指數，不含股利，需註明。
- O3 淨報酬：預設手續費 0.1425%、證交稅 0.3%（`backend/app/taiwan/market_rules.py:76,101,383-401`）。
- O4 回補：寫到獨立目錄 `strategy_picks_backfill/`，永遠不覆寫即時快照。
- O5 同策略同標的 20 個交易日內只計第一次。
- O6 依 `strategy_fingerprint` 分段統計。
- O7 多策略共振的統計。
- O8 圖表：累積報酬、報酬分布。
- O9 看板卡顯示摘要，並把標題「策略當日推薦」改名（`frontend/src/pages/Dashboard.tsx:451`）。（註：原則已於 2026-10-02 調整，見 market-filter-volume-patterns-plan 第 1 節）
- O10 啟動補記：啟動後以 `latest_date()` 走與排程相同的記錄路徑，完全套用 3.1 的規則，不是例外。（執行清單已移入 MVP。）
- O11 結果記憶化（實測超過 1 秒才做）、CSV 匯出，以及在畫面上切換是否納入延遲紀錄或資料不完整日。

## 3. 設計問題逐題回答

### 3.1 快照來源與時機

- 記錄點：在 `refresh_strategy_cache()`（`backend/app/jobs/daily_pipeline.py:89-98`）內呼叫 `strategy_pick_history.record(...)`，以獨立的 try 包住，失敗只記 log（比照 `backend/app/jobs/daily_pipeline.py:97-98`）。觸發條件同第 1 節：排程只要日 K 不是 `failed`（含 `partial`）就會重算；按鈕要有新日 K 或新發佈的 enriched 才重算。hook 依序做以下幾件事：
  1. 只解析一次 T：`T = ScreenerService(repo).latest_date()`。
  2. 清掉程序級 TTL 快取（`backend/app/services/screener.py:56-62`）。
  3. 讀 `kline_daily_enriched/date=<T>/part.parquet` 的修改時間，作為 `input_mtime`。
  4. 呼叫 `compute_and_cache_all(repo, engine, as_of=T, run_report=report)`，並斷言回傳的 `as_of == T`；不相等就不記錄。
  5. 把 `report` 交給 `record`。
- 不記錄 `run_all`、`run_preset`（兩者可指定過去日期），也不記錄 `/cached` 的盤中疊加結果（`backend/app/api/screener.py:309-329`）。快照的寫入分區只由傳入的 `as_of` 決定，沒有任何 API 能寫入快照。
- 訊號日型別：`as_of`、各結果的 `as_of`、每列的 `date`，一律先用 `_as_date()` 轉成 `datetime.date` 再比對；`_as_date()` 接受 `date`、`datetime` 與 ISO 字串。`date` 不等於 `as_of` 的列計數後記 log，不寫入。
- 輸入可用性：由資料讀取端明確回報，不從策略結果反推。
  - 為什麼需要：讀取失敗目前會被靜默當成 0 筆。history 或 current 讀取失敗、分區不存在或為空時，都回傳空表（`backend/app/services/screener.py:104-114,286-291`）；`run_all` 只擋 `None`、不擋空表（`backend/app/strategy/engine.py:1038-1039,1053-1054`）；矩陣策略與 polars_expr 策略遇到空表都回 0 筆（`backend/app/strategy/engine.py:893-903,918-928,1170-1173,1179-1182`）。
  - 傳遞路徑：
    - 在 `backend/app/services/screener.py` 加一個 `contextvars.ContextVar` 作為狀態收集器。`build_strategy_context`（`backend/app/services/screener.py:398-432`）呼叫載入函式前先設定收集器；`_load_enriched_for_date`（`backend/app/services/screener.py:64-118`）與 `_load_enriched_history`（`backend/app/services/screener.py:222-332`）在每個 return 點寫入 `{state, rows, exchanges_at_T, dates}`。
    - `state` 的取值：`ok` 表示快取命中或計算成功（`backend/app/services/screener.py:71-95,117-118,230-252,326-332`）；`missing` 表示分區不存在（`backend/app/services/screener.py:104-105`）；`error` 表示讀取例外（`backend/app/services/screener.py:107-111,286-288`）；`empty` 表示讀到空表（`backend/app/services/screener.py:113-114,290-291`）；呼叫端直接傳入 `current` 時記為 `provided`。
    - `StrategyDataContext`（`backend/app/strategy/engine.py:155-164`）尾端新增有預設值的欄位 `input_status`。`compute_and_cache_all`（`backend/app/api/screener.py:546-637`）新增有預設值的參數 `run_report: dict | None = None`，把 `getattr(context, "input_status", None)` 與指紋（見 3.2）寫進去。
  - 影響範圍：不改任何現有回傳值，也不改既有的位置參數；兩個載入函式的簽名與回傳都不變。
    - 呼叫端：`build_strategy_context` 有 4 個（`backend/app/api/screener.py:284,600`、`backend/app/api/strategy.py:383,431`），`compute_and_cache_all` 有 2 個（`backend/app/jobs/daily_pipeline.py:92`、`backend/app/api/screener.py:668`）。
    - 受影響的測試：
      - `backend/tests/test_strategy_required_history.py:105-108`：替身不寫收集器，狀態為 `unverified`，判定時視為不可用；該測試只看 context，不受影響。
      - `backend/tests/test_screener_builtin_params.py:21`：靠 `getattr` 相容。
      - `backend/tests/test_update_latest_refreshes_caches.py:115` 與 `backend/tests/test_taiwan_forward_adjustment.py:884-886`。
      - 另有 10 個檔直接建構 `StrategyDataContext`；新欄位有預設值，不受影響。
      - 以上都列入第 2、4 步的回歸測試。
  - 依執行後端分組判定（後端種類見 `backend/app/strategy/engine.py:482-489`）：
    - history 組：`matrix_native`、`python_history_legacy`，以及帶 `filter_history_fn` 的 `polars_expr`（`backend/app/strategy/engine.py:893-895`）。條件是 history 的 `state=ok`，而且 `date==T` 有列，不檢查交易日數。磁碟路徑最多只讀 180 個日曆日（`backend/app/services/screener.py:267`），約有一半的交易日湊不滿 121 根；若以 `required_history_bars` 作為門檻，這些日子會整批不記錄。暖機不足時策略的實際行為見 R16。（2026-10-10 第 4 階段起，歷史改以交易日計：載入所需根數再加 60 個交易日；矩陣策略只用自己的「所需根數 + 1 + 60」個交易日計算，`filter_history` 策略收到目標日加前 `LOOKBACK_DAYS` 個交易日，不再是 180 個日曆日。）
    - current 組：其餘的 `polars_expr`（`backend/app/strategy/engine.py:918-921`）。條件是 current 的 `state` 為 `ok` 或 `provided`，而且有列。這組的評分欄位在 history 可用時會從 history 補（`backend/app/strategy/engine.py:1534-1557`），history 不可用時退回只用 current；這時照常寫入，但在清單上標記 `history_degraded`（推測：評分可能與正常日不同）。
    - `composite`：子策略共用同一份 context（`backend/app/strategy/engine.py:1288`），所以依賴是全部子策略依賴的聯集。
  - 判定結果：可用性只看載入是否成功、T 日分區是否可讀；`state=unverified` 一律視為不可用。交易所是否齊全交給 `universe_complete` 處理。
- 套用順序（`record` 內，依序執行）：
  1. 依組別判定：`results` 中屬於不可用組的策略，從本次寫入集合中移除。這些策略即使在 `results` 中以 0 筆出現（例如 history 失敗時，矩陣策略回 0 筆：`backend/app/strategy/engine.py:1170-1173`），也不寫入、不清空，保留既有選股與清單列，並記 log（依 `CONTRIBUTING.md:147`）。
  2. 依凍結狀態與資料新舊（見下）：被拒寫的策略同樣移除。
  3. 對剩下的策略，套用「(訊號日, 策略) 整批替換、0 筆即清空」，並寫入清單列。
  - 不在 `results` 中的策略（本次執行失敗）不受影響。
- 執行清單：每次寫入時，通過套用順序第 1、2 步的每個策略（含 0 筆），在 `data/user_data/strategy_picks/date=<T>/runs.parquet` 留一列。凍結時的指紋比對與延遲判定都以清單為準，所以 0 筆的策略也能比對指紋。清單欄位：
  - `strategy_id`、`run_id`（本次寫入的 uuid）。
  - `total`（策略回傳筆數）、`rows_written`（實際寫入的選股列數）。
  - `strategy_fingerprint`、`input_mtime`、`universe_complete`、`history_degraded`。
  - `first_recorded_at`（之後的覆寫不得改動）、`last_recorded_at`、`run_label`。
- 替換單位（套用順序第 3 步）：以 (`signal_date`, `strategy_id`) 整批替換。只有通過第 1、2 步的策略會先刪除舊列再寫入，0 筆即清空；被過濾掉的策略，以及缺席的策略，都保留舊列與舊清單列（比照 `backend/app/services/strategy_cache.py:141-142`）。
- 一致性判定：某策略「有效」的條件是兩者同時成立：
  - 選股檔中該策略的列都帶清單上的 `run_id`。
  - 這些列的列數等於清單的 `rows_written`。
  - 任一不成立，或缺少清單列，都視為 `manifest_missing`。讀取端預設不計入，但回傳筆數；寫入端視同「已存在、指紋為 None」。
- 寫入順序與失敗組合：先讀既有的 `part.parquet` 與 `runs.parquet`，任一存在卻讀取失敗就中止、不寫入，不當成首寫。接著先替換 `part.parquet`，成功後才替換 `runs.parquet`。
  - (a) `part.parquet` 替換失敗：中止，不寫清單；舊選股與舊清單維持一致，狀態不變。
  - (b) 選股成功、清單失敗，首寫：選股帶新 `run_id`，但沒有清單列 → `manifest_missing`。下次寫入時，在預測開盤前可補寫，`first_recorded_at` 取補寫時間；軟凍結、硬凍結期間拒寫。
  - (c) 選股成功、清單失敗，覆寫：選股帶新 `run_id`，清單仍是舊 `run_id`，兩者不符 → `manifest_missing`。下次寫入時，在預測開盤前可補寫，並沿用舊清單列的 `first_recorded_at`；凍結期間拒寫。
  - (d) 0 筆首寫，清單也失敗：兩個檔案都沒有這支策略的痕跡，下次寫入就當成首寫；若晚於實際 T+1 開盤，就判為延遲。0 筆本來就不貢獻樣本，損失只是當天無法比對指紋。
- 資料新舊：若新的 `input_mtime` 小於清單上的值（較早開始的計算比較晚寫入），拒寫並記 log。
- 凍結判定：逐策略判斷，核心是「真正的 T+1 交易時段已開始」，或「寫入所用資料已不是 `as_of=T`」。
  - 硬凍結：日 K store 已有晚於 T 的分區。這是 T+1 交易時段已發生、唯一能在資料中查證的證據。此時已存在的組合一律拒絕覆寫。這種情況下仍以 `as_of=T` 重算，只會出在 T+1 的 enriched 發佈失敗時（`backend/app/repository.py:1440-1443`）。
  - 軟凍結：尚無晚於 T 的分區，但 `taipei_now()`（`backend/app/taiwan/realtime/calendar.py:34-36`）已過預測開盤。
    - 此時只允許新指紋與清單上的舊指紋相同、而且兩者都不是 None 的覆寫；None 一律視同改變，拒絕覆寫。
    - 預測開盤 = T 之後第一個平日加 `REGULAR_OPEN_START`。專案的 `TaiwanTradingCalendar` 沒有預載休市日；而從分區推得的休市日只會落在最新分區之前，一旦出現晚於 T 的分區就已經是硬凍結。所以 `known_holidays`、`known_trading_days` 對預測開盤沒有作用，實作直接用「第一個平日 09:00」即可。
    - 指紋相同代表規則沒變，價格也沒有前視。但覆寫可能納入 T+1 才取得的 T 日成交量修正（`backend/app/taiwan/daily_store.py:50-57`），以及 T+1 重抓的股本（`backend/app/taiwan/daily_update.py:199-200`，會影響 `turnover_rate`），這些資料的取得時點都晚於 T+1 開盤。接受的理由：它們是 T 日的定案值，不是 T+1 的盤勢。
    - 不在預測開盤時直接凍結，是因為未預知的休市日和「T+1 盤中、資料還沒到」在資料上分不出來。
  - 預測開盤前：可以覆寫（仍受上述資料新舊規則限制）。
  - 首次寫入：任何時點都保存。拒寫等於永久缺口，因為 `as_of` 前進之後，就無法再記錄該日。
- 延遲判定：只看清單上的 `first_recorded_at`。`D1_actual` 是日 K 中第一個晚於 T 的分區日期；`first_recorded_at >= D1_actual 09:00` 就算延遲，預設不計入統計。`last_recorded_at` 不參與判定。
- 重走失效情境：
  1. 一般日 16:30 → 21:40：預測開盤前，覆寫成功。
  2. 未預知休市後首寫（週三 T、週四休市、週四 10:00 首寫）：保存；`D1_actual` 是週五，不算延遲。
  3. 週三分區不完整、週四休市、週四 16:30 修補後重算：指紋相同 → 覆寫成完整版本。目前 TPEx 抓取失敗會整批拋錯（`backend/app/taiwan/providers/snapshot_provider.py:143-156`），這條屬防禦性設計。
  4. 晚開機：T+1 10:00 按鈕抓到 T 並首寫 → 延遲。T+1 盤中改了策略後再重算 T → 軟凍結且指紋改變 → 拒寫。
  5. T+1 收盤後 enriched 發佈失敗、`as_of` 仍為 T → 硬凍結 → 拒寫。
  6. 週五 T、週末：按鈕通常不會重算（`backend/app/taiwan/bootstrap.py:447-455`）；只有 enriched 落後、補發佈時才會重算（`backend/app/api/taiwan.py:152`），此時預測開盤是週一 09:00 → 覆寫成功。
  7. `partial`：T+1 16:30 修補 T 成功、T+1 抓取失敗（`backend/app/taiwan/daily_update.py:414-415`），仍以 `as_of=T` 重算 → 軟凍結、指紋相同 → 覆寫；`first_recorded_at` 不變，仍算準時。
  8. TWSE 在 16:30 尚未發佈，回空表、記為 skipped（`backend/app/taiwan/daily_refresh.py:179-184`）：結果同情境 7。
  9. 21:40 的 history 掃描失敗：history 組不可用 → 該組不寫入，16:30 的選股保留；current 組照常寫入（標 `history_degraded`）。若改成 current 失敗，則只有 current 組保留舊值。
- 殘餘缺口：情境 3 中若使用者週四改過策略，週三會停在不完整版本（照常寫入、預設不計入）；所有排程與按鈕都沒在 T+1 資料到來前跑過時，該日漏記（見 R2）。
- 市場完整性：`universe_complete` 依該策略所屬組實際讀到的輸入判定：從載入狀態的 `exchanges_at_T` 看 `date==T` 的列是否兩個交易所都有。這和策略讀的是同一份資料，因此不受記憶體快取分層影響。
  - 背景：內建策略暖機最多 121 根（`backend/app/strategy/builtin/lorentzian_knn.py:126-127`，加 1 見 `backend/app/strategy/engine.py:681-684`）。第 4 階段改用新的指標暖機慣例後，`lorentzian_knn` 要載入 407 根（分類一根要有 366 根之前的有效 K 線）、`macd_golden` 190 根；2026-10-10 實測每個日期讀 468 個交易日。快取只有 300 個日曆日（`backend/app/repository.py:572,1182-1184`），所以 history 目前走磁碟掃描（`backend/app/services/screener.py:255-285`）；`data/backend.log.1` 有多筆 `cache miss ... 121` 為證。
  - 不完整時照常寫入，但預設不計入統計。實測 2026-10-01 的 enriched 有 TWSE 1,324 列、TPEx 981 列。

### 3.2 儲存

- 路徑：`data/user_data/strategy_picks/date=<signal_date>/part.parquet`（選股列）與同目錄 `runs.parquet`（執行清單），與 `strategy_cache.json` 同放 `user_data`。按日期分區、原子寫入，沿用 `backend/app/taiwan/daily_store.py:130-170` 的作法。
- 不用 JSON：單日快取已達 3.77 MB；若每天存一份 JSON，一年約 0.9 GB（推測）。Parquet 只存必要欄位，每天約 1,000 列。
- 併發：模組鎖搭配暫存檔，再以 `os.replace` 替換（同 `backend/app/services/strategy_cache.py:39-42,182-185`）。Windows 下替換失敗時，重試間隔參考 `backend/app/enriched_generation.py:26`（重試迴圈在 `backend/app/enriched_generation.py:82-87`）。
- 選股列欄位：`signal_date`（Date，分區鍵）、`strategy_id`、`symbol`、`run_id`、`rank`（Int32，從 1 起算）、`score`、`signal_raw_close`（只供顯示）。寫入時間、指紋與完整性只存在清單中。
- `strategy_fingerprint`：
  - 由新函式 `strategy_fingerprints(engine, all_overrides, ids)` 計算，在 `compute_and_cache_all` 內、緊接 `strategy_config.list_overrides` 之後呼叫（`backend/app/api/screener.py:592`）。用的是同一份 `all_overrides` 快照與同一個 engine，結果放進 `run_report`。
  - 逐策略計算，每支各自 try：任何例外（META 無法序列化、`file_path` 為 None 或讀檔失敗、`engine.get` 找不到）都只讓該策略記為 `None` 並記 log，不得往外拋。所以一支策略失敗不影響其他策略寫入，而指紋為 None 的策略在軟凍結期間不能被覆寫。
  - 內容是下列項目以 `json.dumps(..., sort_keys=True)` 序列化後取 sha256，不加 `default`。無法序列化的內容（例如 META 含 set）會拋 TypeError，由該策略的 try 接住，指紋記為 None：
    - `StrategyDef.meta`，以及合併後的 `StrategyDef.basic_filter`（`backend/app/strategy/engine.py:468-475`，含 `DEFAULT_BASIC_FILTER`）。
    - `all_overrides.get(id, {})`，也就是計算時實際使用的同一份設定。
    - `StrategyDef.file_path` 的檔案內容 sha256（`backend/app/strategy/engine.py:203`；`file_path` 為 None 時整支記 None）。
    - 複合策略的子策略指紋（`META["children"]`，`backend/app/strategy/engine.py:118-140`；任一子策略為 None 則父策略也為 None）。
  - 限制一：自訂策略檔若在磁碟上改了但沒 reload，指紋反映的是磁碟上的版本。會 reload 的地方：自訂與 AI 策略共用 `_save_strategy_code`（`backend/app/api/strategy.py:704`，reload 在 `backend/app/api/strategy.py:754`）、複合策略存檔（`backend/app/api/strategy.py:1037`）、reload 端點（`backend/app/api/strategy.py:1150`）；內建策略改檔則會觸發 uvicorn 重載。
  - 限制二：共用程式碼變動不會反映在指紋上，但會改變選股，例如 `backend/app/backtest/matrix.py` 的特徵計算。`DEFAULT_BASIC_FILTER`（`backend/app/strategy/engine.py:41`）的值已經透過 `basic_filter` 納入指紋。
    - 實例：2026-10 第 4 階段的引擎層規則（`exclude_new_days` 實作、評分缺值改為 null、指標暖機慣例、歷史長度）改變了選股，沒改策略檔的內建策略指紋卻不變。依使用者決定（2026-10-09），已凍結的快照不回改；上線時記下策略邏輯變更日，變更日前後的統計不可直接合併。在事後統計畫面顯示變更日留到第 5 階段。
- 一檔被多個策略選中時各一列；連續多日入選時每天各一列，畫面同時顯示訊號日數（去重版本是 O5）。

### 3.3 報酬計算

- 讀取：新函式 `load_inputs(data_dir, ...)` 一律從傳入的 `data_dir` 建立 store，不使用預設建構子（預設建構子會指向全域 settings）：
  - `TaiwanDailyStore(data_dir/"taiwan"/"daily")`（`backend/app/taiwan/daily_store.py:68-77`）。
  - `TaiwanCorporateActionStore(data_dir/"taiwan"/"corporate_actions")`（`backend/app/taiwan/corporate_actions.py:146-151`）。
  - API 層從 `request.app.state.repo.store.data_dir` 取得 `data_dir`（`backend/app/api/screener.py:271`）。
  - 漲跌幅比例表由參數注入：正式環境用 `taiwan_limit_profile`（`backend/app/indicators/pipeline.py:2011-2040`），測試傳入 stub，避免連網。
- 不用 enriched（沒有 raw open，而且新因子會整批改寫尺度：`backend/app/taiwan/enriched_refresh.py:9-14`）。也不用 `get_daily_batch`，因為它可能直接回傳含盤中列的記憶體快取（`backend/app/repository.py:1531-1534`）。
- 計算流程：
  1. 剔除無成交列。
  2. 用 `attach_reference_prices(..., with_limits=True)` 帶出事件日的官方漲停價。
  3. 保留 `raw_open`、`raw_close` 後，以 `forward_adjusted(through=最新日K日)` 統一尺度。
  4. 計算 `ret_k = adj_close(T+k) / adj_open(T+1) - 1`。T+k 指訊號日之後第 k 個已存分區日。
- 單位：`CONTRIBUTING.md:86-96` 中各欄位小數與百分數混用，所以新欄位明定為小數制（0.0123 = 1.23%），並依 `CONTRIBUTING.md:96` 加單位測試。
- 無成交：OHLC 任一為 null 即視為無成交；另保留 `filter_halt_days` 的 open=0 且 high=0 判準（`backend/app/indicators/pipeline.py:1040-1042`），不得用 volume 判斷。
  - 實測依據：2026-10-01 分區 2,335 列中，30 列 OHLC 全為 null，其中 15 列 volume>0（例：`4192.TPEX`），沒有 open=0 的列。
  - T+1 無成交標 `not_filled_no_trade`，畫面顯示「未成交：無成交／停牌」，不順延到 T+2。
  - T+k 無成交時沿用前一筆收盤，標 `stale_exit`。
  - 整列缺席：分區存在但沒有該標的（實測 2026-09-30→10-01 有 `2323.TWSE`、`3085.TPEX` 整列消失），規則同無成交：T+1 缺席標 `not_filled_no_trade`；T+k 缺席沿用前一筆收盤，標 `stale_exit` 並記錄缺席日數；連續缺席到最新分區的另標 `absent_since=<日期>`（可能停牌或下市，不另判斷）。`pending_entry` 只代表「T 之後還沒有任何日 K 分區」。只要第 k 個分區存在，該持有期就會結算，不會一直停在等待狀態。
- 開盤漲停：以原始價判斷（`CONTRIBUTING.md:102`）。
  - 漲停價：事件日用 `ref_limit_up`；其他日為 T 日原始收盤 ×(1+`limit_pct`)，再依跳動單位取整（`backend/app/indicators/pipeline.py:2079-2085`）。
  - `raw_open >= 漲停價 - 0.005`（容差同 `backend/app/indicators/pipeline.py:888`）時標 `not_filled_limit_up`。
  - `limit_pct` 為空時照常進場，標 `limit_check=unknown`（`taiwan_limit_profile` 的空值語意見 `backend/app/indicators/pipeline.py:2014-2016`）。
- 除權息事件可用性：不使用 `read_events_or_empty`。依序判斷：
  - (a) `store.path` 不存在 → `events_missing`。
  - (b) `store.read()` 拋出例外 → `events_unreadable`。
  - (c) 六個來源的覆蓋：取 `fetch_state()` 的 `covered_from` 最大值與 `covered_through` 最小值；讀不到、缺少任一來源、或來源項目格式錯誤（`_coverage` 回 None）→ `coverage_missing`。單筆的 T+1 早於 `covered_from`，或 T+k 晚於 `covered_through` 時，依日期分兩種：(i) T+1 早於 `covered_from` 或晚於 `covered_through` → 這筆的全部持有期都標 `adjustment_not_covered`，而且開盤漲停判定改標 `limit_check=unknown`（缺少 T+1 的 `ref_limit_up`）；(ii) T+1 在覆蓋內、只有 T+k 晚於 `covered_through` → 僅 T+k 晚於覆蓋的那幾個持有期標記，其餘照常計算。
  - (a)～(c) 全域不可用時：所有報酬為 null，開盤漲停判定一律標 `limit_check=unknown`，API 回傳 `adjustment.status="unavailable"` 及原因，筆數照常回傳；UI 頂部顯示「除權息資料不可用（原因），報酬暫不計算」。
  - 部分未覆蓋時：受影響的持有期不計入統計，API 回傳排除筆數，UI 表格附註說明。絕不改用未還原價。
- 其他狀態：`pending_entry`（T 之後尚無分區）、`tracking`、`completed`。T+k 分區只含單一交易所時，該持有期標 `data_incomplete`，不計入。日 K 讀取：`scan_range` 失敗時會退回 `read_range`，而 `read_range` 會略過讀不到的分區、只記 warning（`backend/app/taiwan/daily_store.py:247-248,275-277`）。所以計算前要比對 `available_dates()` 與實際讀到的日期序列；只要窗口內有缺口，相關持有期就不計算，標 `prices_incomplete`。標 `stale_exit` 與 `absent_since` 的持有期照常計入彙總，因為排除停牌或下市的標的會讓統計偏樂觀，另外顯示筆數。
- 出場跌停與滑點：MVP 不模擬出場成交，T+k 報酬是以收盤價計算的持有期報酬（mark-to-market）。T+k 收盤即使鎖在跌停也照用；進出場都不扣滑點。這兩點都會讓統計偏樂觀：跌停鎖死時實際賣出價通常更低，加上滑點後報酬也較低。畫面與 API 的 `method` 要明示「未模擬出場可成交性、未計滑點」，是否處理併入待決問題 4。

### 3.4 回補

- 可行性：enriched 有 665 個分區（2024-01-02～2026-10-01），資料只讀到該日以前：當日資料經 `_compute_enriched_full` 以 `date <= target` 過濾（`backend/app/services/screener.py:414-415,117,180-190`）；歷史資料見 `backend/app/services/screener.py:421-423,279-283`、`backend/app/repository.py:1193`。
  - 回補必須仿照 `/api/strategies/run-all`（`backend/app/api/strategy.py:404-448`）直接呼叫引擎，不能用 `compute_and_cache_all`，否則會把看板快取蓋成過去日期。
- 前視偏誤：(1) 用現在的程式與參數重算過去，容易過度擬合；(2) enriched 已依最新因子前復權，絕對價格門檻與當天不同；(3) 暫定值已換成最終值；(4) 證券主檔是現況，可能有存活者偏差（推測）；(5) 歷史股本是否 point-in-time 未核對（推測）。
- 成本：快取只涵蓋 300 個日曆日（`backend/app/repository.py:572`）；更早的日子走慢路徑，約 5 秒一日（`backend/app/services/screener.py:257-259`），一年約 20 分鐘以上（推測）。
- 建議：MVP 不回補。樣本量方面，T+20 至少要 20 個交易日；同日選股彼此高度相關，有效樣本接近訊號日數。約需 60 個已完成的訊號日，也就是上線後約 4 個月（推測）。訊號日數少於 20 的策略標「樣本少」。

### 3.5 回測模組：重用還是另寫

| 比較 | `backend/app/backtest/engine.py` | 輕量新模組 |
|---|---|---|
| 模型 | 投資組合模擬，`max_positions=10`、需設初始資金（`backend/app/backtest/engine.py:49-78`） | 每筆訊號獨立計算 |
| 進場來源 | 用現在的程式在歷史上重新產生訊號 | 讀已記錄的快照 |
| 漲停 | 只擋一字漲停（`backend/app/backtest/engine.py:1340-1361`） | 擋「開盤即漲停」 |
| 價格／狀態 | 讀 enriched、沒有前端入口（`frontend/src/lib/navigation.ts:25-31`）、產品不提供（`docs/features.md:42`） | 日 K 原始價加因子，可用合成資料單元測試 |

建議另寫輕量模組；O1 的觸價判定順序可參考 `backend/app/backtest/engine.py:964-985`。

### 3.6 API

放在 `/api/screener`（`backend/app/api/screener.py:24`），以避開 `/api/strategies/{strategy_id}`（`backend/app/api/strategy.py:345`）。API 層保持薄層（`CONTRIBUTING.md:67`），一次回傳全部策略，避免 N+1（`CONTRIBUTING.md:189`）。
- `GET /api/screener/pick-performance` 回傳：
  - `method`：計算起點 `next_session_open`、持有期 `[1,5,10,20]`、`forward_adjusted_incl_dividends`、`costs_included=false`。
  - `adjustment`（`status`、`reason`、`covered_through`），以及 `first_signal_date`、`last_signal_date`、`prices_through`。
  - `strategies[]`：`strategy_id`、`signal_count`、`signal_days`、`filled_count`、`not_filled_limit_up`、`not_filled_no_trade`、`limit_check_unknown`、`pending_entry_count`、`tracking_count`、`completed_count`；計入統計但需揭露的 `stale_exit_count`、`absent_count` 也放在這一層。
  - `excluded`（整筆不計入的筆數）：`recorded_late`、`universe_incomplete`、`manifest_missing`。
  - `horizons["1"|"5"|"10"|"20"]`：`sample_count`、`sample_days`、`mean_return`、`median_return`、`positive_ratio`；以及該持有期不計入的筆數：`adjustment_not_covered`、`data_incomplete`、`prices_incomplete`。
- `GET /api/screener/pick-performance/{strategy_id}?limit=200`：逐筆回傳 `signal_date`、`symbol`、`name`、`rank`、`entry_date`、`entry_price_raw`、`status`、`limit_check`、`stale_exit`、`absent_since`、`returns`（`{"1"|"5"|"10"|"20": {value, reason}}`，每個持有期各自標原因，可以只有部分持有期不可用）、`recorded_late`、`manifest_missing`、`universe_complete`。無資料時回空清單。名稱解析比照 `backend/app/api/screener.py:332-388`。
- 讀快照檔失敗回 503 並附原因。除權息不可用時回 200，內容為上述 `adjustment` 狀態，報酬全部為 null。

### 3.7 前端位置

- 盤點：
  - 路由：`frontend/src/router.tsx:94-109`；擴充路由在 `frontend/src/router.tsx:110-120`；側欄在 `frontend/src/lib/navigation.ts:53-60`。
  - 頁層級分頁只有設定頁的 `?tab=`（`frontend/src/pages/Settings.tsx:33-42,47-49`）。訊號庫子分頁（`frontend/src/pages/settings/CustomSignals.tsx:33-37`）與自選股的檢視切換（`frontend/src/pages/Watchlist.tsx:656`）都是頁內切換。
  - 策略庫：篩選鈕在 `frontend/src/pages/Strategies.tsx:33-38,161-178`、共振區在 `frontend/src/pages/Strategies.tsx:181-332`、策略卡在 `frontend/src/pages/Strategies.tsx:334-419`，沒有分頁。看板也沒有分頁（`frontend/src/pages/Dashboard.tsx:623-649`）。

| 方案 | 分級 | 要改的熱點 | 其他改動 | 使用體驗 |
|---|---|---|---|---|
| A 策略庫新分頁 `?tab=performance` | L3 | `api.ts`、`queryKeys.ts` | `Strategies.tsx`（不是熱點）、新元件 | 與策略規則、當日選股同頁對照 |
| B 擴充路由新頁 `frontend/src/custom/<ns>/extension.tsx` | L2 | 同 A：請求與查詢鍵必須集中（`docs/secondary-development.md:182-183`） | 新增二開目錄；不必改 `router.tsx`（自動發現：`frontend/src/extensions/bootstrap.ts:4-6`；側欄：`frontend/src/components/Layout.tsx:321-328`） | 側欄末端多一項，要切頁才能對照 |
| C 看板卡加欄位 | L3 | 同 A | `Dashboard.tsx` | 空間不足，數字放首頁容易被讀成推薦（註：原則已於 2026-10-02 調整，見 market-filter-volume-patterns-plan 第 1 節） |

- 結論：選 A。A 與 B 都要改 `api.ts`、`queryKeys.ts` 這兩個熱點（`docs/secondary-development.md:298-299`），B 唯一省下的 `Strategies.tsx` 並不是熱點。此外，B 把核心功能放進二次開發目錄（「前端二次開發目錄」，`frontend/src/custom/README.md:1-3`），還把統計和策略規則拆到兩頁。A 不需要動 `router.tsx`；`navigation.ts` 本來就不在熱點清單（`docs/secondary-development.md:292-300`）。
- 畫面內容：
  1. 一行計算方式說明：「以 T+1 開盤價為計算起點，已含除權息調整，未扣交易成本」，附資料區間。
  2. 持有期切換鈕 T+1／5／10／20，預設 T+20。
  3. 彙總表：策略名稱、訊號筆數（訊號日數）、未成交（開盤漲停／無成交）、樣本數、平均報酬、中位數報酬、報酬為正比例；另以「延遲紀錄／市場不完整／清單缺漏（`manifest_missing`），未計入 N 筆」顯示排除數，持有期層級的排除則附在對應欄位旁。預設依名稱排序；樣本少的策略以灰字顯示。
  4. 點一列展開最近 200 筆紀錄，點標的開啟 `StockPreviewDialog`。
  5. 空、載入、錯誤三種狀態（`CONTRIBUTING.md:203`），以及除權息不可用的橫幅。
- 查詢失效（依 `CONTRIBUTING.md:171,175`）：
  - 後端：MVP 不做記憶化，沒有服務端快取要失效。若做 O11，快取鍵要包含快照分區、最新日 K 分區、`events.parquet`、`fetch_state.json` 的修改時間。
  - 前端：新查詢鍵前綴 `screener-pick-performance`，不加入 SSE 失效清單（理由同 `frontend/src/lib/queryKeys.ts:107-112`），`staleTime` 5 分鐘。手動更新按鈕在 `dates_fetched>0` 時會讓全部查詢失效，涵蓋新鍵；只有 `enriched_published` 時，後端仍會重算並寫入快照（`backend/app/api/taiwan.py:152`），前端卻只讓兩個狀態鍵失效（`frontend/src/pages/settings/TaiwanHistoryDataCard.tsx:55-61`），而且全域設定 `refetchOnWindowFocus: false`（`frontend/src/main.tsx:37`），資料過了 `staleTime` 也不會自動重抓，要等元件重新掛載且資料已超過 5 分鐘 `staleTime`（5 分鐘內切頁回來不會重抓），或重新整理頁面，才會取得新統計。MVP 接受這個延遲，不改 `TaiwanHistoryDataCard.tsx`；排程寫入的情況相同。

### 3.8 文字合規

- 註：原則已於 2026-10-02 調整，見 market-filter-volume-patterns-plan 第 1 節；本節原文保留作為紀錄。
- 依據：產品「不推薦個股」（`README.md:34`）；AI 禁語包含「勝率評分」「強烈推薦」（`backend/app/taiwan/ai_research.py:323-327`）。
- 命名：API 用 `pick`、`signal`、`positive_ratio`，不用 `recommend`、`win_rate`、`rating`；UI 用「選股紀錄」「事後統計」「報酬為正比例」，不用「推薦」「勝率排行」「最佳策略」；「進場」改說「以 T+1 開盤價為計算起點」。
- 固定說明文字：「以下為過去選股紀錄的統計結果，計算方式固定，不構成買賣建議，也不代表未來表現。」
- 版面：不預設依報酬排序；紅綠色只表示正負（`frontend/src/pages/Strategies.tsx:25-29`）。

### 3.9 二開分級與熱點複核

- 後端 L3：快照 hook 必須接在核心重算之後，而後端擴充點只有 `include_router` 與啟動鉤子（`docs/secondary-development.md:248,250`），無法表達這個需求。要改的 `daily_pipeline.py`、`api/screener.py` 都不在熱點清單（`docs/secondary-development.md:292-300`）。
- 前端 L3：改 `Strategies.tsx`，以及熱點 `api.ts`、`queryKeys.ts`。
- 複核點（依 `docs/secondary-development.md:286`，PR 要標出）：
  - `api.ts` 只在 screener 區塊（`frontend/src/lib/api.ts:2528-2556` 附近）新增型別與函式，不改既有簽章。
  - `queryKeys.ts` 只新增 `screener-pick-performance` 前綴，不動 `SSE_INVALIDATE_PREFIXES`。
  - 日後合併上游時，複查這兩段與 `refresh_strategy_cache` 的 hook。

## 4. 實作步驟（每步可獨立建置與測試）

> 2026-10-23 更新：`backend/tests/conftest.py` 現在會把 `DATA_DIR`、`CACHE_DIR` 改寫到測試會話的臨時目錄並使用合成證券主檔，下列指令裡的 `set DATA_DIR` 與複製證券主檔已不起作用，直接執行 pytest 即可；`data/` 前後比對仍可保留作為佐證（見 `CONTRIBUTING.md` 第 9 節）。

以下每條後端指令都會建立新的暫存目錄 `spt-<亂數>`（每步都是空目錄，只複製證券主檔），並只在 `cmd /c` 子程序內設定 `DATA_DIR`，不會殘留在呼叫端的 shell；另外在測試前後各取一次正式 `data/` 的遞迴清單（含目錄、隱藏檔、修改時間與大小）再比對，可偵測新增、刪除、修改與新建目錄（PowerShell 5.1，在 repo 根目錄執行）。`Compare-Object` 必須沒有輸出；若使用者的 `uvicorn --reload` 同時在跑而出現 `data\backend.log` 等檔案，必須逐一判明寫入來源，無法判明就視為失敗。最穩當的做法是停掉後端再測。
這個比對有三個偵測不到的盲點：
- 寫到 `data/` 以外的位置偵測不到，例如沒吃到 `DATA_DIR`、改以 CWD 解析的相對路徑（`backend/app/taiwan/data_root.py:11-20` 的歷史說明）；根目錄 `.env:38` 設有相對路徑 `DATA_DIR=./data`，只有指令內的環境變數覆寫它時，才會指向暫存目錄。
- 內容改變但大小與修改時間都不變的情況偵測不到。
- 正式環境的漲跌幅表與名稱解析仍走全域 `get_security_master()`（`backend/app/indicators/pipeline.py:2023`、`backend/app/api/screener.py:357`），測試必須注入 stub 或 monkeypatch，不能依賴它。

1. **快照儲存模組**（M1）
   - 新增 `backend/app/services/strategy_pick_history.py`：`record(data_dir, as_of, results, run_report, run_label, input_mtime, now)`（`run_report` 含輸入狀態與指紋；`universe_complete` 與各組可用性由它推得；日 K 分區由 `record` 從 `data_dir` 讀取）、`read_picks`、`strategy_fingerprints`、`_as_date`。
   - 新增 `backend/tests/test_strategy_pick_history.py`，一律用 `tmp_path`。測試案例：
     - 凍結：3.1 的 9 個情境各一條（含硬凍結；軟凍結下指紋相同、不同、為 None）。缺席策略保留；某策略重算後為 0 筆時舊列被清空，清單仍留指紋。
     - 寫入失敗組合（以 monkeypatch 讓 `os.replace` 對指定檔案拋錯）：3.1 的 (a)～(d) 各一條，斷言狀態與 `manifest_missing` 判定；另加一條 `run_id` 不符、一條 `rows_written` 不符。
     - 輸入不可用：
       - 先以 16:30 寫入正常選股，再模擬 21:40 history 讀取失敗的重算：讓 `results` 中的矩陣策略以 0 筆出現，斷言它們在 16:30 寫入的選股與清單完全不變，也沒有被 0 筆清空。
       - 只有 history 失敗、而且有一支 `polars_expr` 策略時：矩陣策略保留舊值，`polars_expr` 策略照常寫入並標 `history_degraded`。
       - 只有 current 失敗時：current 組保留舊值，history 組照常寫入。
       - 分區只有單一交易所時照常寫入，`universe_complete=False`。
       - `state=unverified` 時不寫入。history 交易日數少於 121 根，但 `state=ok` 時照常寫入。
     - 讀取既有檔失敗：`part.parquet` 或 `runs.parquet` 損毀時中止，既有檔案不變。
     - 資料新舊：`input_mtime` 倒退時拒寫。
     - 重啟路徑（`CONTRIBUTING.md:227`）：模組沒有記憶體狀態，用新的呼叫（重新 import 模組、重建鎖）對同一 `as_of` 再寫一次，結果只依磁碟上的清單判定，`first_recorded_at` 不變。
     - 併發：兩個執行緒同時以同一 `as_of` 呼叫 `record`，同時另一個執行緒反覆讀取；讀取端不得讀到半寫檔，最終內容等於其中一次寫入（依 `CONTRIBUTING.md:229`）。
     - 延遲：首寫晚於實際 T+1 開盤 → 延遲；情境 7、8 的 `first_recorded_at` 不變，仍算準時。
     - 型別：用 `compute_and_cache_all` 實際輸出形狀測試（`as_of` 為 date、結果的 `as_of` 為 str、列的 `date` 為 date），以及 JSON 讀回的全字串形狀，兩者都要保留全部列。另以 stub engine 加 monkeypatch `ScreenerService.build_strategy_context`，實際呼叫 `compute_and_cache_all(..., as_of=...)`，再把輸出餵給 `record()`。
     - `universe_complete`：磁碟上的 enriched 分區只有 TWSE 時為 False；分區讀取失敗時也為 False，並記下原因。指紋：改覆寫設定或改檔後會變；沒有覆寫檔時不拋錯；一支策略的 META 含 set 或策略檔被刪時，該策略為 None、其他策略照常寫入。
   - 驗證：`$d="$env:TEMP\spt-$(Get-Random)"; New-Item -ItemType Directory -Force "$d\taiwan" | Out-Null; Copy-Item -LiteralPath data\taiwan\security_master.parquet -Destination "$d\taiwan\"; $s={Get-ChildItem -LiteralPath data -Recurse -Force | ForEach-Object {"$($_.FullName)|$($_.LastWriteTimeUtc.Ticks)|$($_.Length)"}}; $b=& $s; cmd /c "set DATA_DIR=$d&& uv run --directory backend --frozen pytest tests/test_strategy_pick_history.py tests/test_strategy_cache.py -q && uv run --directory backend --frozen ruff check app/services/strategy_pick_history.py tests/test_strategy_pick_history.py"; Compare-Object $b (& $s)`
2. **接上盤後流程**（M1；越早上線越早累積樣本）
   - 修改 `backend/app/jobs/daily_pipeline.py` 的 `refresh_strategy_cache`。
   - 擴充 `backend/tests/test_update_latest_refreshes_caches.py`（比照 `backend/tests/test_update_latest_refreshes_caches.py:108-119` 的 patch 寫法）。測試案例：只解析一次 T 並以 `as_of=T` 呼叫；回傳的 `as_of` 不等於 T 時不記錄；帶入計算前讀到的 `input_mtime`；計算前清除 TTL 快取；`run_report` 有輸入狀態與指紋；`record` 失敗不影響快取；`compute_and_cache_all` 失敗時不記錄；所有 store 都由 `data_dir` 建立。
   - 驗證（加跑排程測試；該測試會啟動排程器並建構真實的 `TaiwanDailyUpdateService()`，見 `backend/tests/test_taiwan_schedule_split.py:28-42,98`）：`$d="$env:TEMP\spt-$(Get-Random)"; New-Item -ItemType Directory -Force "$d\taiwan" | Out-Null; Copy-Item -LiteralPath data\taiwan\security_master.parquet -Destination "$d\taiwan\"; $s={Get-ChildItem -LiteralPath data -Recurse -Force | ForEach-Object {"$($_.FullName)|$($_.LastWriteTimeUtc.Ticks)|$($_.Length)"}}; $b=& $s; cmd /c "set DATA_DIR=$d&& uv run --directory backend --frozen pytest tests/test_update_latest_refreshes_caches.py tests/test_taiwan_schedule_split.py tests/test_strategy_pick_history.py -q && uv run --directory backend --frozen ruff check app/jobs/daily_pipeline.py tests/test_update_latest_refreshes_caches.py"; Compare-Object $b (& $s)`
   - 上線驗收：下一班排程後，快照列數要等於執行清單的 `sum(rows_written)`；`sum(total)` 減去被剔除與被拒寫的列數後，也要與它相符。存檔要避開 16:20–16:50 與 21:30–22:10，存檔前先把 `data/user_data/` 備份到 repo 外。
3. **報酬計算服務**（M2、M3）
   - 新增 `backend/app/services/strategy_pick_performance.py`：純函式 `compute_outcomes(picks, bars, events, adjustment_state, calendar, limit_profile)`、`summarize`，以及 `load_inputs(data_dir, ...)`。
   - 新增 `backend/tests/test_strategy_pick_performance.py`，用合成資料測試：
     - 一般路徑；開盤漲停；T+1 的 OHLC 為 null 但 volume>0（判為無成交）；持有期內除息（用固定樣本斷言）；T+1 為事件日並使用 `ref_limit_up`；`limit_pct` 為空；`stale_exit`；未滿期。
     - 除權息不可用：`events.parquet` 缺檔、內容損毀、`fetch_state.json` 缺檔或缺來源、`covered_through` 早於 T+k，各自得到對應狀態，且報酬一律為 null。
     - 整列缺席：T+1 缺席、T+k 缺席、缺席到最新分區，都不得停在 `pending_entry`。模擬某個日 K 分區讀不到時，相關持有期標 `prices_incomplete`。除權息全域不可用時，所有筆的 `limit_check` 都為 unknown。
     - 單位（依 `CONTRIBUTING.md:96`）：固定樣本漲 1% 時 `ret_k == 0.01`，不是 1；中位數與零報酬的邊界值。
   - 驗證：`$d="$env:TEMP\spt-$(Get-Random)"; New-Item -ItemType Directory -Force "$d\taiwan" | Out-Null; Copy-Item -LiteralPath data\taiwan\security_master.parquet -Destination "$d\taiwan\"; $s={Get-ChildItem -LiteralPath data -Recurse -Force | ForEach-Object {"$($_.FullName)|$($_.LastWriteTimeUtc.Ticks)|$($_.Length)"}}; $b=& $s; cmd /c "set DATA_DIR=$d&& uv run --directory backend --frozen pytest tests/test_strategy_pick_performance.py tests/test_taiwan_forward_adjustment.py tests/test_taiwan_corporate_actions.py -q && uv run --directory backend --frozen ruff check app/services/strategy_pick_performance.py tests/test_strategy_pick_performance.py"; Compare-Object $b (& $s)`
4. **API**（M4）
   - 修改 `backend/app/api/screener.py`。
   - 新增 `backend/tests/test_screener_pick_performance_api.py`：比照 `backend/tests/test_screener_cache_api.py:16-19`，以 `SimpleNamespace` 把 `tmp_path` 當作 `repo.store.data_dir` 傳入，不 import `app.main`。測試案例：有資料、無資料、除權息不可用、讀檔錯誤回 503、未知策略回空清單；回傳的報酬欄位為小數制。
   - 驗證：`$d="$env:TEMP\spt-$(Get-Random)"; New-Item -ItemType Directory -Force "$d\taiwan" | Out-Null; Copy-Item -LiteralPath data\taiwan\security_master.parquet -Destination "$d\taiwan\"; $s={Get-ChildItem -LiteralPath data -Recurse -Force | ForEach-Object {"$($_.FullName)|$($_.LastWriteTimeUtc.Ticks)|$($_.Length)"}}; $b=& $s; cmd /c "set DATA_DIR=$d&& uv run --directory backend --frozen pytest tests/test_screener_pick_performance_api.py tests/test_screener_cache_api.py tests/test_screener_cached_names.py tests/test_screener_builtin_params.py tests/test_kline_hot_path_memory.py tests/test_update_latest_refreshes_caches.py tests/backtest/test_factor_rank_research.py -q && uv run --directory backend --frozen ruff check app/api/screener.py tests/test_screener_pick_performance_api.py"; Compare-Object $b (& $s)`
5. **前端分頁**（M5）
   - 修改 `frontend/src/lib/api.ts`、`frontend/src/lib/queryKeys.ts`、`frontend/src/pages/Strategies.tsx`；新增 `frontend/src/components/StrategyPickPerformance.tsx`。
   - 擴充 `frontend/src/pages/Strategies.test.tsx`。測試案例：預設分頁的內容不變；切到「事後統計」才發請求；載入、空、錯誤、除權息不可用四種狀態；樣本為 0 的持有期按鈕呈停用；不出現「推薦」「勝率」（註：原則已於 2026-10-02 調整，見 market-filter-volume-patterns-plan 第 1 節）。無權限狀態不另做：401 由全域 `QueryCache` 導向登入（`frontend/src/main.tsx:30-33`），此頁沒有額外的權限分級（`CONTRIBUTING.md:203`）。
   - 驗證：`$env:Path="$env:APPDATA\npm;$env:Path"; pnpm --dir frontend test -- Strategies; pnpm --dir frontend build`。`frontend/vite.config.d.ts` 與 `frontend/pnpm-lock.yaml` 已有未提交的修改，不要還原，也不要混進本功能的提交。
6. **前後端聯調**（依 `CONTRIBUTING.md:230-231`）
   - 使用者的後端重載到新程式後，以 `Invoke-RestMethod http://127.0.0.1:3018/api/screener/pick-performance` 核對回傳欄位。
   - 用 vite 開啟 `/strategies?tab=performance`，手動檢查：分頁切換、空狀態（第一個 T+1 之前）、載入、錯誤（暫停後端）、展開與預覽、窄螢幕與深淺色（`CONTRIBUTING.md:204`），並截圖附在 PR。
   - 最後在 `docs/features.md` 補一節說明計算方式，並執行 `git diff --check`。

## 5. 風險

- R1 正式資料被改寫：`uvicorn --reload` 存檔即生效，且測試沒有隔離 `data_dir`。對策：每條指令自帶 `DATA_DIR`、遞迴比對修改時間、避開排程時段、事前備份。2026-10-23 起測試隔離改由 conftest 負責，`DATA_DIR` 對測試已不起作用。
- R2 漏記：MVP 沒有啟動補記；T 日要記到快照，必須在 `as_of` 前進到 T+1 之前至少有一次 `as_of=T` 的重算。能記到的途徑只有兩種：
  - T 日 16:30 或 21:40 觸發的當下後端正在執行（記憶體 jobstore；之後才開機不會補跑該班）。
  - 在 T+1 的日 K 被抓進來前，使用者按了更新按鈕並抓到新資料（`backend/app/api/taiwan.py:152`）。
  - 例：T 日兩個時點後端都沒開，隔天開機但沒按按鈕，T+1 16:30 排程同時抓到 T 與 T+1，`as_of` 跳到 T+1，T 就此漏記。T+1 開盤後才補的首次寫入會保存但標為延遲。O10 可縮小窗口；已漏記的日子只能靠 O4 回補並分開標示。
- R3 統計被誤讀：同日選股相關、持有窗口重疊，加上延遲紀錄、資料不完整日被排除，都會影響樣本。對策：顯示訊號日數與排除筆數。
- R4 策略改版：用 `strategy_fingerprint` 記錄。手動改檔但未 reload，以及共用程式（`DEFAULT_BASIC_FILTER`、矩陣特徵）的變動，都不會反映在指紋上（見 3.2）。
- R5 上限截斷：只取 score 前 N 筆，改 `display_limit` 會改變樣本構成（`backend/app/strategy/engine.py:649-658`）。
- R6 還原權值：缺檔、讀取失敗、覆蓋落後都能偵測並 fail-closed。只有官方表在已覆蓋區間內本身漏列事件時偵測不到。
- R7 漲跌停分類取現況，歷史分類可能不同，畫面要顯示 `limit_check=unknown` 的筆數。
- R8 暫定值修正：日 K 會被重抓，即時計算的報酬可能小幅變動。
- R9 合規：依 3.8 的命名規則，並用前端測試鎖住禁用字眼。（註：原則已於 2026-10-02 調整，見 market-filter-volume-patterns-plan 第 1 節）
- R10 時區：凍結與延遲判定一律用 Asia/Taipei，因為伺服器時區不得成為隱式輸入（`CONTRIBUTING.md:114`）。
- R11 效能：一年約 25 萬列，預估 1 秒內（推測，未實測）；超過才做 O11。
- R12 判定與實際來源：可用性與 `universe_complete` 都取自策略實際讀到的資料，兩者一致；程序級 TTL 快取（`backend/app/services/screener.py:28,244-253`）由 hook 在計算前清除。殘餘：覆寫參數使最大暖機降到 90 根以下時，會命中 repo 歷史快取（`backend/app/repository.py:572,1182-1184`）；若之前的 refresh 失敗，策略可能讀到 T 的舊版本。這類失敗會被 `backend/app/repository.py:676-677` 吞掉，`backend/app/repository.py:552-565` 也是靜默 return，refresh 回報不了，所以本計畫不緩解。影響只是 T 日資料的新舊，沒有前視。
- R13 指紋時點：指紋與設定在計算開頭讀取的同一份快照上計算（見 3.2），設定不會錯配。殘餘：策略檔的雜湊在計算開頭讀取，而 engine 載入的模組更早；手動改檔又沒有 reload 時兩者可能不同（見 3.2 限制一）。若做不到在計算開頭計算，窗口就是整段計算時間，`data/backend.log*` 記錄的 `run_all` 為 6.3–16.3 秒。
- R14 並行重算：按鈕在 API 執行緒、排程在排程器執行緒，可能同時重算，而 `max_instances=1` 只限制排程本身。較早開始的計算若較晚寫入，在預測開盤前可能用較舊的資料覆寫較新的結果。緩解：hook 在計算前讀 enriched `date=<T>` 分區的修改時間作為 `input_mtime` 傳入 `record`，清單保存這個值；新值小於清單值時拒寫。殘餘：計算期間分區被改寫時，記到的是改寫前的時間。
- R15 漲停判定失準：T+1 的 `ref_limit_up` 缺失時，原本會退回用 T 日收盤乘以漲跌幅計算，可能誤判。對策見 3.3 (c)(i)，改標 `limit_check=unknown`，畫面顯示筆數。
- R16 暖機不足（既有行為，不屬本功能範圍）：history 少於 121 根時，`lorentzian_knn` 不報錯也不跳過，而是用較少資料照算。引擎只用暖機根數決定讀取量，不比對實際根數（`backend/app/strategy/engine.py:681-684`）；滾動視窗不足時結果為 NaN（`backend/app/backtest/matrix.py:3449-3450`），正規化把 NaN 退回 0.5（`backend/app/strategy/builtin/lorentzian_knn.py:112-119`）；近鄰只從現有歷史取樣（`backend/app/strategy/builtin/lorentzian_knn.py:145-163`）。
  - 影響：這類日子的選股品質可能與資料充足時不同，本計畫照常記錄。
  - 第 4 階段已處理（2026-10-10）：歷史不足的標的不分類，正規化窗口內缺值時不再退回 0.5，候選近鄰固定取 120 根有效 K 線，選股與回測結果一致。

## 6. 需要使用者拍板的問題

1. 同一交易日多次重算以哪次為準？A 預測開盤前以最後一次為準，之後只接受指紋相同的覆寫，依策略整批替換（推薦）；B 第一次；C 兩次都保留。
2. 「勝率」的定義與名稱？A 持有期報酬大於 0 的比例，畫面稱「報酬為正比例」（推薦）；B 同樣定義，畫面稱「勝率」；C 以先觸及停利算勝（需要先做 O1）。
3. 連續多日入選同一標的怎麼計？A 每日各算一筆，並顯示訊號日數（推薦）；B 20 個交易日內只計第一次；C 兩種並列。
4. 交易成本、滑點與出場跌停（`CONTRIBUTING.md:163` 要求回測計入這些約束，屬於灰區）？A MVP 算收盤持有期毛報酬，並標示「未扣成本、未計滑點、未模擬出場可成交性」（推薦）；B 加上淨報酬與固定滑點；C 再加上出場跌停時順延到可成交日。
5. 延遲紀錄（T+1 開盤後才首次寫入）是否計入預設統計？A 不計入，只顯示筆數（推薦）；B 指紋與前一筆準時紀錄相同時才計入；C 全部計入。
6. 是否回補歷史？A 不回補（推薦）；B 回補，分開標示且預設不併入統計；C 回補並併入統計。

使用者決定（2026-10-02）：六題全部採 A。

## 7. 實作結果與偏差

- 輸入狀態：改由載入端經 ContextVar 回報，依策略執行後端分組判定；`InputStatus` 放在 `backend/app/services/strategy_inputs.py`，避免篩選服務反向依賴紀錄模組。
- `_history_cache` 加鎖，因為盤後 hook 與更新按鈕會從其他執行緒清除它。
- API：`stale_exit` 只放在持有期層級（策略層移除 `stale_exit_count`）；另回傳 `history_degraded_count`、各持有期的 `pending_count`；明細的 `limit` 參數範圍為 1–2000。
- 前端：「樣本少」灰字門檻為 `signal_days < 20`；彙總表顯示缺席筆數。
- O11（結果快取）暫緩：以一年份合成資料（25 萬筆選股、57.5 萬列日 K）量測，每次請求 1.4–2.0 秒，主要耗時在逐分區讀取快照；實際資料量超過再處理。
- 尚未處理的建議：「樣本少」的訊號日數包含被排除的日子；從未選出標的的策略不會出現在彙總表。
- 第 4 階段（2026-10-10）：快照照舊凍結，舊日期的 0 分與排名不回改；評分缺值的候選現在以空分數記錄。策略邏輯變更日見 3.2 限制二。

## 附錄：查證指令（唯讀）

- 快取：用 Python 讀 `data/user_data/strategy_cache.json`，計算 `sum(total)`、`sum(len(rows))`、不重複 symbol 數，結果為 980、980、497。
- 無成交：用 `backend/.venv` 的 Polars 讀 `data/taiwan/daily/date=2026-10-01/part.parquet`：2,335 列中 OHLC 全為 null 的有 30 列，其中 volume>0 的 15 列，open==0 的 0 列；TWSE 1,331 列、TPEx 1,004 列。
- 分區數：`ls data/kline_daily_enriched` 與 `ls data/taiwan/daily` 都是 665 個；除權息覆蓋讀 `data/taiwan/corporate_actions/fetch_state.json`，6 個來源的 `covered_through` 都是 2026-10-01。
