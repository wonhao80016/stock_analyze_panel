# 台股雷達策略移植與回測驗證

日期：2026-09-18
分支：`feat/radar-strategies`

> 本文件是 2026-09-18 的移植與驗證紀錄，屬於歷史文件；文中的數字與行為以當時的程式為準。之後的變更以引用框標出。

## 背景

評估外部專案 [tw-stock-radar](https://github.com/b9007082-cell/tw-stock-radar) 的選股策略
能否併入本專案。該 repo **沒有 LICENSE 檔**（GitHub API 回 `license: null`），因此
不得複製其任何程式碼。本次採 clean-room 做法：只依規則描述重新實作，程式碼全部自行撰寫。

其中 `lorentzian_knn` 對應的原始指標衍生自 MPL-2.0 授權的 TradingView 指標，
因此僅依公開的演算法數學描述實作，未參考任何既有實作之原始碼。

現行的 `lorentzian_knn` 不是原指標的移植（2026-10-10 第 4 階段後的說明，見
`backend/app/strategy/builtin/lorentzian_knn.py` 的模組說明）：

- 特徵、正規化與濾網都不同（矩陣層沒有 CCI、ADX、WaveTrend）。
- 標籤看的是之後 `label_horizon` 根 K 線的漲跌。
- 近鄰是固定窗口內的精確 k-NN。
- 進場不等訊號翻轉。

外部 repo 的回測不具參考價值：其資料只保留 183 天，抽查的回測結果為零交易，
遠低於它自己設定的 200 筆交易門檻。本次全部重新以本專案資料驗證。

## 新增策略

| 策略 id | 名稱 | 核心規則 |
|---|---|---|
| `bollinger_squeeze` | 布林收斂突破 | 帶寬處近 80 日 20% 分位後，首根帶量突破上軌 |
| `consolidation_breakout` | 底部整理突破 | 低檔區間收斂 + 量縮，帶量突破區間高點 |
| `swing_trend_confirmation` | 波段結構確認 | 頭頭高底底高 + 均線多排，結構剛成立時觸發 |
| `pullback_resume` | 強勢回檔再起漲 | 回檔量縮守 MA20，站回 MA5 且突破前高 |
| `bottom_reversal` | 深跌止穩反彈 | 深跌後爆量止跌 K，隔日突破其高點確認 |
| `lorentzian_knn` | Lorentzian KNN 分類 | Lorentzian 距離 k-NN（k=8）對 5 維震盪指標分類 |

全部採 `matrix_native` 後端、NumPy 向量化，無逐日迴圈。

第 4 階段（2026-10-10）改了其中幾支的規則，上表是 2026-09-18 的原始設計：

- `bottom_reversal`：爆量改以止跌 K 之前 5 根的均量為基準。出場改成收盤創 20 日收盤新低，或收盤由上往下跌破 MA20。
- `consolidation_breakout`：`distance_from_low_60d` 改為低者優先評分；整理期量縮比上限預設改為 0.9。
- `swing_trend_confirmation`、`pullback_resume`：流動性門檻改為成交額 ≥ 1 億元（`min_amount`），取代 200 萬股。已存的 `min_volume` 設定仍會疊加套用。`swing_trend_confirmation` 另有自己的結構轉弱出場訊號 `signal_swing_structure_exit`。
- `lorentzian_knn`：候選近鄰改為被分類那根之前 120 根有效 K 線；歷史不足的標的不分類，選股與回測結果一致（細節見下方「已知限制」）。
- 全部策略：指標改用新的暖機慣例（EMA 以 SMA 起始、RSI 與 ATR 用 Wilder 平滑），並排除上市未滿 30 個交易日的新股。

### 未實作：低檔高殖利率

原規劃的第 7 支策略（殖利率 ≥5% + PB ≤2.5 + 低檔站回 MA20）**無法實作**：

- 矩陣層可用的財務因子只有 `backend/app/backtest/fundamentals.py` 的 7 個，**不含殖利率**。
- 唯一相關的 `pb_latest` 由財報 `bps` 反推，且其資料源 `data/financials/metrics/` 目前為空。
- `backend/app/taiwan/fundamentals.py` 雖有 `dividend_yield` / `PBratio`，但未接入矩陣層。

要補齊需新增一條帶 point-in-time（`available_at`）對齊的基本面資料管線，屬獨立工作。

## 回測方法

- 資料：`kline_daily_enriched`，2024-01-02 ～ 2026-09-17，2345 檔，141 萬列。
- 撮合：`BacktestEngine.simulate_portfolio`，`open_t+1` 進場（避免前視偏誤）。
- 成本：手續費 0.1425%（雙邊）、證交稅 0.3%（賣方）、滑價 0.1%，
  對齊 `backend/app/taiwan/market_rules.py` 的台股稅費模型。
- 標的池過濾：股價 ≥ 3 元、成交額 ≥ 2000 萬，對齊 `DEFAULT_BASIC_FILTER`。
- 部位：最多 20 檔，依策略自身 `META["scoring"]` 排序選股。

### profit_factor 公式錯誤（已修正）

原本 `stats["profit_factor"]` 算的是 `avg_win / avg_loss`（payoff ratio），
而非標準定義的「總獲利 ÷ 總虧損」，公式漏了勝率項。兩者關係為
`標準PF = payoff × 勝率/(1-勝率)`，只有勝率恰為 50% 時相等。

因此**勝率低於 50% 時嚴重高估、高於 50% 時低估**。例如 `bollinger_squeeze`
（勝率 24.5%）原本回報 2.44，實際為 0.79；`bottom_reversal`（勝率 66.7%）
原本回報 1.41，實際為 2.82。六支策略的實測值皆與該關係式吻合。

最嚴重的影響是 `backend/app/backtest/optimizer.py` 的 `VALID_OBJECTIVES`
允許把 `profit_factor` 當成網格搜參的最大化目標——錯誤公式會把參數推向
「低勝率、高 payoff」的區域，而那正是它讀數最虛高、實際最可能淨虧損之處。

> 2026-10-25 起，參數優化與步進優化（`optimizer.py`、`walkforward.py`）已從專案移除，上段只說明當時的影響。

**已修正**：4 處公式（`engine.py` 3 處、`strategy.py` 1 處）改用總額比；
原值另存為新欄位 `payoff_ratio`（該指標本身有意義，只是名稱掛錯）。
新增 `backend/tests/backtest/test_profit_factor.py` 鎖住此不變式——
原本零測試覆蓋正是此缺陷長期存在的原因。

本文件所有 PF 均為自交易明細重算，與修正後的引擎輸出一致。

## 驗證結果

本節的數字是 2026-09-18 的實作。第 4 階段之後的規則已不同（見「新增策略」下方的清單），這些數字不代表現行程式。

2026-10-10 第 4 階段的前後對照用另一組設定量測，不能與下表直接比較：2025-01-02 ～ 2026-09-30，最多 10 檔部位，`open_t+1` 進場；「前」是第 4 階段之前的程式（`2f691d5`），「後」是第 4 階段的工作樹，兩邊使用同一份資料，並在跑「後」之前用新程式重新發佈指標。

| 策略 | 交易數 | 勝率 | 年化 | 最大回撤 |
|---|---|---|---|---|
| `bottom_reversal` | 42 → 33 | 59.5% → 54.5% | +6.1% → +3.9% | -3.8% → -12.9% |
| `swing_trend_confirmation` | 480 → 459 | 29.2% → 28.5% | -9.9% → -10.0% | -32.9% → -30.8% |
| `pullback_resume` | 312 → 273 | 28.8% → 30.0% | +0.1% → +8.6% | -20.7% → -13.8% |
| `consolidation_breakout` | 355 → 343 | 26.5% → 27.4% | -5.1% → -14.4% | -32.1% → -30.2% |
| `bollinger_squeeze` | 378 → 383 | 23.0% → 23.0% | -25.4% → -25.4% | -45.7% → -48.4% |
| `lorentzian_knn` | 960 → 772 | 35.9% → 36.3% | -19.6% → -26.3% | -39.1% → -50.4% |

- `lorentzian_knn`：資料從 2024-01-02 開始，而每檔要先有 366 根之前的有效 K 線（第 367 根起才分類），所以第一筆可分類的進場從 2025-01-03 延後到 2025-07-14。
- `pullback_resume` 的變化來自流動性門檻改用成交額；`bottom_reversal` 的平均持有天數從 1.2 天變成 5.5 天，因為出場不再於進場隔天就觸發。

2026-09-18 的結果，全市場、台股真實成本：

| 策略 | 交易數 | Sharpe | 真實 PF | 平均每筆 | 年化 | 最大回撤 | 勝率 |
|---|---|---|---|---|---|---|---|
| `bottom_reversal` | 123 | **+0.96** | **2.82** | **+2.41%** | +5.4% | -3.7% | 66.7% |
| `swing_trend_confirmation` | 1566 | -0.25 | 0.94 | -0.26% | -10.7% | -57.0% | 26.6% |
| `pullback_resume` | 757 | -0.45 | 0.87 | -0.53% | -6.7% | -28.6% | 25.2% |
| `consolidation_breakout` | 1011 | -0.65 | 0.86 | -0.69% | -13.7% | -49.6% | 26.4% |
| `bollinger_squeeze` | 1011 | -1.00 | 0.79 | -1.02% | -18.9% | -44.8% | 24.5% |
| `lorentzian_knn` | 3980 | -2.66 | 0.74 | -0.93% | -47.7% | -85.3% | 34.8% |

**六支中只有 `bottom_reversal` 通過驗證。**

### 標的池敏感度

四支趨勢／突破型策略在成交量前 50 大的個股上全部獲利（Sharpe +0.72 ～ +1.56），
在全市場則全部虧損。把成交額門檻從 2000 萬提高到 1 億後：

| 策略 | Sharpe（2000萬） | Sharpe（1億） | PF（1億） |
|---|---|---|---|
| `consolidation_breakout` | -0.65 | +0.06 | 1.01 |
| `swing_trend_confirmation` | -0.25 | -0.03 | 0.97 |
| `pullback_resume` | -0.45 | -0.39 | 0.88 |
| `bollinger_squeeze` | -1.00 | -0.34 | 0.90 |

提高門檻使虧損收斂，但**沒有任何一支產生正期望值**（PF 最高 1.01，剛好打平）。
在僅 2.7 年資料的前提下繼續調參有過擬合風險，因此停止優化。

### lorentzian_knn 的已知限制

- 候選近鄰固定取被分類那根之前 120 根有效 K 線（每 `sample_stride` 根取一根），不再隨載入的歷史長度變動。
- 分類一根 K 線要往回讀 366 根自己的有效 K 線（近鄰窗口、正規化窗口與指標暖機），而且最舊那根距今不能超過 406 個交易日（最多容許缺 40 個交易日）。所以結果只取決於最近 407 個交易日，選股與回測一致；歷史不足的標的不分類，新上市股約要一年半（第 367 根有效 K 線起）才會有訊號（已接受的取捨）。以上是預設參數的值，改動 `label_horizon`、`sample_stride`、`norm_window` 會改變根數。
- 原演算法設計的回看是 2000 根，本專案只有約 670 個交易日的資料；第 4 階段依使用者的決定把候選窗口固定為 120 根有效 K 線。
- 原指標有 11 項參數（特徵正規化方法、volatility/regime 濾網判定式等）
  僅存在於原始碼、查無文字規格。這些缺口改用本專案既有指標的等價實作，
  因此本策略屬同類演算法，非原指標的精確移植。
- 社群對原指標的主要質疑為自由度過高、易過擬合。回測結果與該質疑一致。

## 決策

經使用者裁決：

1. 六支策略全部正常上架，不設 `research_only`、不在 UI 標註回測績效。
2. 四支在大型股獲利、全市場虧損的策略先行擱置，不再投入調參或 walk-forward。

## 其他變更

- `backend/app/api/screener.py`：抽出 `compute_and_cache_all()`，
  供 `POST /api/screener/run_all` 與盤後排程共用，避免兩份會漂移的複製品。
- `backend/app/jobs/daily_pipeline.py`：盤後資料更新後觸發策略快取重算。
  在此之前 `strategy_cache.json` **沒有任何排程或前端會寫入**，
  `GET /api/screener/cached` 因此永遠回空。

  把關條件是 `result.daily.status == "failed"`，**刻意不用 `overall_status`**：
  `run_update()` 的失敗以狀態回報而非拋例外，而其聚合規則是「有 failed 又有 success → partial」
  （`backend/app/taiwan/daily_update.py`），因此「日線失敗、法人成功」會聚合成 `partial`——
  正是最該擋的拿舊價格重算的情形。改查日線自身狀態才擋得住。
  日線 `partial` 仍重算是安全的：`compute_and_cache_all` 未指定 `as_of` 時走
  `ScreenerService.latest_date()` 讀實際 `max(date)`，只會退回上一個完整落盤的日期，
  不會用殘缺資料計算。
- `frontend/src/pages/Dashboard.tsx`：新增「策略當日推薦」Card，讀取上述快取。

### 即時行情曾讓選股永遠回 0 檔（已修）

Card 接上後仍空白，根因不在策略也不在 Card：

1. `quote_service` 的背景輪詢在台股只抓自選股（當時 9 檔），卻沿用「全市場覆寫」語意
   寫入 `kline_daily` 與 `kline_daily_enriched`，為交易所尚未公布的當日造出分區。
2. `enriched_latest_date()` 在任何一次寫入（哪怕 1 列）後就把快取日期設為當天，零完整度檢查。
3. `ScreenerService.latest_date()` 讀的正是該日期，於是全市場掃描以一個只有 9 檔的日子為基準，
   20 支策略一律回 0 檔。

修法（`feat/radar-strategies`）：

- **語意分離**：新增 `KlineRepository.taiwan_official_latest_date()` 直接查 `TaiwanDailyStore`，
  選股改用它；`enriched_latest_date()` 維持「今天」語意不動，即時行情、自選股卡、
  監控規則等需要當日的消費者不受影響。
- **寫入閘門**：`_live_write_allowed()` 只允許為交易所已公布的日期落磁碟。
  閘門直接查 `TaiwanDailyStore`，**刻意不查 enriched 自己的分區**——否則既有污染分區
  會替自己背書，形成「污染騙過閘門、閘門放行更多污染」的迴圈。
  被擋下時記憶體快取仍更新，live 讀者照樣拿得到今天。
- **覆寫改合併**：`flush_live_*` 收斂為 `merge_live_*`。覆寫語意只在「即時來源真的涵蓋全市場」
  時才安全，台股並非如此，覆寫會清掉分區內其他所有標的。保留 `force=True` 逃生門。
- **補刷快取**：盤後排程發布指標後補呼叫 `rebuild_views()` + `refresh_cache()`。
  在此之前發布只寫磁碟，記憶體仍留著盤中那幾檔，而 `_load_enriched_for_date()`
  只要日期相符就直接採用快取、不檢查列數。

驗證：選股由 0 檔恢復（`as_of=2026-09-17`，六支新策略共 108 檔）。
新增 `tests/test_screening_basis_date.py`（4 項）與 `tests/test_live_write_gate.py`（5 項）。

**後果須知**：盤中選股基準固定為前一個已公布交易日，直到晚間排程跑完才前進。
技術上正確，但體感像「盤中不會動」，前端宜標示基準日。

### 執行前提

> 2026-10-10 第 4 階段後，下面這段已不成立，現況如下：
>
> - 內建策略的預設評分只剩 `high_turnover_surge` 用 `turnover_rate`。`bullish_alignment`、`low_volatility_leader`、`pullback_to_support` 改用 `vol_ratio_5d`。
> - 台股換手率的分母來自 `data/taiwan/share_capital.parquet`（已發行普通股扣除私募股），不是 `data/instruments/`。
> - 換手率覆蓋不足的交易日，`high_turnover_surge` 會以覆蓋率訊息拒絕執行。`run_all` 把它記在 `failures`，其他策略照常完成；看板列出未能計算的策略，策略庫顯示失敗原因。

（2026-09-18 原文）策略執行需要 `data/instruments/` 有股本資料——既有策略
（`bullish_alignment`、`high_turnover_surge`、`low_volatility_leader`、
`pullback_to_support`）的 `scoring` 用到 `turnover_rate`，缺該資料會讓
整批 `run_all` 失敗。本次驗證環境該目錄為空，僅能單獨執行本批新策略。
**部署前需確認該資料已回補，否則盤後排程會失敗、Card 不會更新。**
