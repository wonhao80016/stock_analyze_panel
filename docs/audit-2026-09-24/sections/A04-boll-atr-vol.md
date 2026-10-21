# A04-boll-atr-vol｜布林、ATR、波動率、量比、量均、動能、區間高低

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 5 | 7 | 0 | 0 | 0 | 14 | 7 |

## 稽核者摘要

盤後全量路徑（compute_indicators，也就是 enriched_refresh 寫入的值）已用獨立實作核對：2026-08-03～09-23 共 38 個交易日 × 9 檔自選、342 列。布林（MA20±2×樣本標準差）、ATR(Wilder)、annual_vol_20d（簡單報酬、ddof=1、×√252）、vol_ma5/10、vol_ratio_5d（當日量／前 5 日均量）、momentum_5～60d、high_60d/low_60d（以收盤價計）全部吻合，最大誤差 ≤4e-11；自選股 OHLC 與證交所 STOCK_DAY 342/342 列完全一致。

問題集中在五處：
- **台股未做除權息／分割還原（P1）**：0050 在 2025-06-18 一拆四，當天 momentum_60d 算成 −73.7%，annual_vol 算成 267%。2379 的 momentum_20d 被低估一半（3.7%，還原後應為 7.4%）。
- **盤中增量路徑與盤後全量路徑口徑不一致（P2）**：
  - high/low_60d 盤中用最高／最低價、盤後用收盤價，實測「創60日新高」訊號有 14 列不一致，例如 2454 在 9/21～9/23 盤中為否、盤後為是。
  - 布林與年化波動率盤中用母體標準差、盤後用樣本標準差，突破條件也分別是 >= 與 >。
  - 盤中狀態取日曆 90 天，最少只有 53～54 個交易日，momentum_60d 最大誤差 29 個百分點，ma60 偏低 8～11%。
- **同名 vol_ratio_5d 定義不同（P2）**：台股選股器與研究脈絡的分母含當日，與管線不同。
- **P3 級（6 項）**：
  - 盤中量比的分子是 MIS 盤中整股量，分母是官方含零股、盤後定價、鉅額的全日量，收盤時低估 5～8%。
  - 開盤數秒內的時間折算放大到約 3240 倍。
  - ATR 暖機期的種子與 Wilder 不同，前段最多相差 27%。
  - 年化用 √252，台股實際一年 242～243 個交易日；且用簡單報酬而非對數報酬。
  - 台股自選清單與選股器沒有過濾停牌空列。
  - app 近期部分成交量比官方少，最多 17%。

第三方指標數值抓不到：Yahoo 奇摩技術分析頁由前端動態產生，investing 回 404。已用 Yahoo chart API 交叉比對 2454：收盤 22/22 天與官方一致；Yahoo 成交量為官方的 83%～93%，佐證量比口徑問題。

本 agent 未修改 repo（git status 與開始時相同）；import app 模組可能更新既有的 __pycache__。產物都放在 scratchpad\audit\work\A04\。網路請求共 21 次，未連證交所或櫃買中心。

## 問題清單

### [P1 → P1] 台股 enriched 指標以未還原價計算：除權息與 0050 分割讓動能、波動率、ATR、布林、60 日高低嚴重失真（確認成立）

- **主張**：台股 enriched 固定以空的復權因子計算，所有 A04 指標都建立在未還原價上，違反「enriched OHLC 為前復權」的契約。除權息日與分割日造成人為跳空，扭曲動能、年化波動、ATR、布林與 60 日高低及其訊號。根因可能與股價／復權領域的 finding 重複；本項聚焦 A04 指標的量化影響。
- **正確的定義或數值**：依契約以前復權價計算指標，除息與分割不應在動能、波動、ATR、布林、60 日高低產生人為跳空；至少分割（0050）必須還原。
- **app 實際行為**：全部以未還原價計算。分割後 60 個交易日內 0050 的 momentum_60d 約為 -70%，年化波動超過 260%；除息股的動能被低估一個股利率，波動與 ATR 高估。
- **對使用者的影響**：自選中的 0050、00878、2379、3034、3545、2454、6196 都受影響。以 2025 年資料回測 0050 會得到錯誤的動能與波動排名及進出場訊號（例如 low_volatility_leader 的 annual_vol 門檻、momentum 評分）。K 線圖的布林通道與 ATR 停損線在分割日附近完全失真。
- **修法建議**：台股建立 adj_factor（除權息參考價與分割比例，官方 TWT49U 與分割資料），在 enriched_refresh 傳入 factors；或明訂台股指標採未還原價，但至少對分割做比例還原，並同步更新 CONTRIBUTING 契約。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/enriched_refresh.py:95 enriched = compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)
  - CONTRIBUTING.md:100 「enriched 的 open/high/low/close 為前復權價格。」
  - backend/app/indicators/pipeline.py:6 存储列 OHLCV(前复权)；:493-502、:518-526 動能與波動直接用 close
  - data/adj_factor 為空目錄
- **線上證據**：
  - https://money.udn.com/money/story/124512/8553991 等（搜尋摘要）：0050 1拆4，6/10 為分割前最後交易日，6/11～6/17 暫停交易，6/18 恢復交易；分割前 188.65 元，6/18 約 47.16 元
  - 官方參考資料 raw\twse_stock_day_2454_202609.json 的 notes：「註記欄位有**者…表示該ETF恢復買賣日價格因辦理分割或反分割作業因素與其最近一成交日價格有一定比例之轉換關係」
  - 官方參考資料 csv\twse_twt49u_20260601_20260923.csv：2379 在 2026-09-08 除息（除息前收盤 729，參考價 704）；00878 在 08-18（33.75→32.74）；3034 在 07-10（542→519）；自選 9 檔中 7 檔近 60 個交易日內有除息
- **本地計算證據**：
  - a04_step1.py：enriched 5,943 列 close≠raw_close 的列數 = 0
  - a04_step4.py（d）已存 enriched，0050.TWSE 在 2025-06-18：change_pct=-0.7478、momentum_60d=-0.7373、annual_vol_20d=2.672、atr_14=12.86、boll_lower 到 06-25 降為 15.09、signal_n_day_low 連續 4 天為真；2025-08-15 momentum_60d 仍為 -0.7088
  - a04_step4.py（c）以 TWT49U 參考價向後還原，2026-09-23 的結果：2379 momentum_20d 0.0368（還原後 0.0736）、momentum_60d -0.0558（-0.0223）、annual_vol 0.3536（0.3035）、high_60d 840（811.19）；00878 momentum_60d 0.0475（0.0798）；3034 momentum_60d 0.0187（0.0638）
  - 除息日的 TR：2379 在 09-08 為 49（還原後 41），00878 在 08-18 為 1.45（0.46）；2026-07 以後 ATR 相對誤差最高 14.7%，年化波動最高 66.5%
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：台股 enriched 固定以空的復權因子計算（enriched_refresh.py:95）。台股 provider 也宣告 adj_factor=False，所以動能、年化波動、ATR、布林、60 日高低及其訊號全部建立在未還原價上，違反 CONTRIBUTING.md:100 的前復權契約與 :128 的「禁止靜默換用錯誤口徑」。同一個根因還出現在 K 線的 taiwan_local 路徑（kline.py:435）與自選列表的 momentum（taiwan/technical_indicators.py:85-86）。

量化影響：
- 0050 在 2025-06-18 的 1 拆 4 分割：momentum_60d 約 -74%，持續 60 個交易日；high_60d 卡在 188.65 共 59 天；annual_vol_20d 約 267%，持續 20 個交易日（不是 60 天）；ATR 12.86（還原後約 0.73）；boll_lower 一度為負值；signal_n_day_low 連續 4 天誤觸發。
- 除息：到 2026-09-23 為止，自選股中有 6 檔在 60 個交易日內除息，影響現行數值。例如 2379 momentum_20d 3.7% 對還原後 7.4%、high_60d 840 對 811；3034 與 00878 的 momentum_60d 被低估 3～4.5 個百分點；除息後 20 日內年化波動最多偏離 66.5%，ATR 最多偏離 14.7%。偏離方向視除息日走勢而定，不一定是高估。
- 6196 的除息日已超過 60 個交易日，目前數值不受影響。

使用者目前沒有監控規則，所以不會誤發告警。但篩選器訊號、個股分析的 ATR／布林通道、K 線布林，以及涵蓋 2025 年 6～9 月的回測都會拿到錯誤數值。
    - 理由：核心主張成立：台股 enriched 指標完全以未還原價計算，違反 repo 自己寫的「enriched OHLC 為前復權」契約，也違反「provider 缺少能力時禁止靜默換用錯誤口徑」的規定。finding 列的本地數值我全部獨立重算過，結果一致。嚴重度 P1 恰當，理由是這屬於錯誤的金融口徑：0050 的 1 拆 4 分割會讓動能變成約 -74%，任何定義都不會接受這種結果；除息則讓現行指標出現 3～5 個百分點的偏差。

【程式碼】
- enriched_refresh.py:95 固定傳入 factors=pl.DataFrame()。compute_enriched 在 pipeline.py:1023 遇到空因子就跳過 _apply_adj_factor。
- TR 在 :422-427，60 日高低在 :437-440，布林在 :417、:448-451，動能與年化波動在 :495-526，全部直接用 close/high/low。
- 台股 provider 在 hybrid_provider.py:31 宣告 adj_factor=False，get_adj_factors 回傳空表（:144-151，docstring 寫「Phase 2 records raw price semantics」）。但 CONTRIBUTING.md:100 的契約仍寫前復權，:128 規定「禁止靜默換用錯誤數據或錯誤口徑」。
- 這個問題不在 code-review-2026-09-22.md 的已知清單內。

【權威定義】我另外查了 StockCharts 與 TEJ 兩個來源，與 finding 引用的來源不同。兩者都說明技術分析應使用對分割與除權息還原後的價格，未還原的分割會讓多數指標誤發賣出訊號。TEJ 的調整係數為「除權息參考價 ÷ 前日收盤價」，從最新價往回推，與程式碼 _apply_adj_factor 的前復權語意一致。

【本地重算】做法是用 polars 直接讀 parquet，再把自組的 factors 餵給純計算函式 compute_enriched。因子來源是 TWT49U 的 pre/ref 與 0050 在 2025-06-18 的 1 拆 4。以下 finding 引用的數值全部重現：
- 2379：momentum_20d 0.0368→0.0736，momentum_60d -0.0558→-0.0223，annual_vol 0.3536→0.3035，high_60d 840→811.19。
- 00878：momentum_60d 0.0475→0.0798。
- 3034：momentum_60d 0.0187→0.0638。
- 除息日 TR：2379 為 49→41，00878 為 1.45→0.46。
- 2026-07 以後 ATR 最大相對誤差 14.7%，年化波動最大相對誤差 66.5%。
- 已存的 0050 enriched 在 2025-06-18：change_pct=-0.7478、momentum_60d=-0.7373、annual_vol=2.672、atr=12.86；boll_lower 到 06-25 降為 15.09；signal_n_day_low 在 06-18～06-23 連續 4 天為真；2025-08-15 的 momentum_60d 為 -0.7088。

【使用者情境】
- 監控規則為 0 條，所以不會誤發告警。
- 台股篩選器會 join 最新 enriched 分區的 signal_*（taiwan/screener.py:585-603）。
- 個股分析的 ATR 波動通道與布林，讀取路徑是 stock_analysis.py:122 → enriched。
- K 線圖讀 enriched，或讀 kline.py:433-435 另一條同樣不還原的路徑。
- 回測在 backtest/engine.py:615-619 讀 enriched。
- 因此在正常使用下確實會看到錯值。
- 分割的影響目前只存在於 2025-06～09 的歷史區段（回測與圖表），除息則影響目前的值。

【finding 的小錯誤，不影響成立】
(1) 「自選 9 檔中 7 檔近 60 個交易日內有除息」應為 6 檔。6196 的除息日 2026-06-24 距 2026-09-23 已 64 個交易日，它在 09-23 的指標還原前後完全相同；7 檔是指 06-01～09-23 整個 TWT49U 區間。
(2) 「分割後 60 個交易日內年化波動超過 260%」有誇大：annual_vol_20d>2 只持續 20 個交易日，到 2025-07-15 為止。momentum_60d<-50% 確實持續 60 天（到 2025-09-09），high_60d 卡在 188.65 共 59 天，boll_lower 為負值 12 天。
(3) 「波動與 ATR 高估」方向不是一定成立：0050 在 2026-07-21 除息日的未還原 TR 3.3 反而小於還原後的 3.9，09-23 的 ATR 也是未還原 1.7897 < 還原 1.7906。方向取決於除息日當天走勢。
(4) 程式碼證據漏了兩條同源路徑：kline.py:435 的 compute_enriched(local, factors=pl.DataFrame())，以及自選列表的 taiwan/technical_indicators.py:85-86（momentum_5d/20d 同樣未還原）。只修 enriched_refresh 不會修到這兩處。
    - 證據：
      - backend/app/taiwan/enriched_refresh.py:95 enriched = compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)
      - backend/app/indicators/pipeline.py:1023-1024 只有 factors 非空才呼叫 _apply_adj_factor；:13 docstring「有 adj_factor 时先应用前复权再算指标;无因子时直接用 raw」
      - backend/app/indicators/pipeline.py:422-427（TR 用 high/low/prev_close）、:437-440（high_60d/low_60d 用 close rolling）、:417 與 :448-451（布林）、:495-526（動能、年化波動用 close）
      - backend/app/taiwan/providers/hybrid_provider.py:31 adj_factor=False；:144-151 get_adj_factors 回傳空表「Phase 2 records raw price semantics」
      - CONTRIBUTING.md:100「enriched 的 open/high/low/close 為前復權價格。」；:128「provider 缺少某項能力時…禁止靜默換用錯誤數據或錯誤口徑。」
      - backend/app/api/kline.py:433-435 台股 K 線走 taiwan_local 分支時同樣呼叫 compute_enriched(local, factors=pl.DataFrame())（第二條未還原路徑）
      - backend/app/api/stock_analysis.py:122 個股分析（ATR 波動通道、布林）讀 repo.get_daily_asset → enriched；backend/app/taiwan/screener.py:585-603 篩選器 join 最新 enriched 分區的 signal_*；backend/app/backtest/engine.py:615-619 回測讀 enriched
      - backend/app/taiwan/technical_indicators.py:85-86 自選列表的 momentum_5d/20d 直接用 TaiwanDailyStore 的未還原 close（修 enriched 也修不到）
      - 線上（與 finding 不同來源）https://help.stockcharts.com/data-and-ticker-symbols/data-availability/price-data-adjustments ："We adjust our historical price data to remove gaps caused by stock splits, dividends, and distributions"；"Most of the technical indicators on that chart would give sell signals because of the significant price drop"（未還原的分割）；"By default, all of our ticker symbols are adjusted for splits, dividends, and distributions."
      - 線上 https://www.tejwin.com/insight/%E9%82%84%E5%8E%9F%E8%82%A1%E5%83%B9/ ：「若中間存在除權息落差或分割事件，容易視覺上出現股價『跳空』的錯覺，因而做出錯誤的技術分析判斷」；「以最新股價為基準往回推」；「調整係數：除權息參考價 ÷前日收盤價」
      - 線上 https://wealth.businessweekly.com.tw/GArticle.aspx?id=ARTL003012773 與搜尋摘要：0050 以 4 倍進行分割，6/10 為分割前最後交易日，6/18 恢復交易；本地 taiwan/daily 顯示 2025-06-10 收盤 188.65、2025-06-18 收盤 47.57
      - 官方參考 raw\twse_stock_day_0050_202609.json notes：「註記欄位有**者…表示該ETF恢復買賣日價格因辦理分割或反分割作業因素與其最近一成交日價格有一定比例之轉換關係」
      - 本地 v1.py（讀 data\kline_daily_enriched）：自選 9 檔 enriched 共 5,943 列，close≠raw_close 為 0 列；0050 在 2025-06-18：change_pct=-0.74784、momentum_60d=-0.737327、annual_vol_20d=2.672053、atr_14=12.864955；boll_lower 在 06-25 為 15.087896、06-30 為 -15.138709；signal_n_day_low 在 06-18/19/20/23 為 true；2025-08-15 的 momentum_60d 為 -0.708767、high_60d 為 188.65
      - 本地 v2.py（compute_enriched 分別餵空因子與 TWT49U pre/ref 因子加 0050 ex_factor=4），2026-09-23：2379 momentum_20d 0.036785 vs 0.073602、momentum_60d -0.055831 vs -0.022303、annual_vol 0.353597 vs 0.303537、high_60d 840 vs 811.193416、low_60d 683 vs 659.5775；00878 momentum_60d 0.047463 vs 0.079776；3034 momentum_60d 0.018692 vs 0.063836；6196 全部指標相同
      - 本地 v2.py 除息日 TR：2379 在 2026-09-08 為 49 vs 41；00878 在 2026-08-18 為 1.45 vs 0.46；0050 在 2026-07-21 為 3.3 vs 3.9（未還原反而較小）；2026-07-01 以後 ATR 最大相對誤差 00878 為 0.147431，年化波動為 0.665374
      - 本地 v2.py 交易日距離（到 2026-09-23）：6196 為 64、2454 為 55、0050 為 46、3545 為 36、00878 為 26、2379 為 11 → 60 個交易日內有除息的是 6 檔（加上 3034 約 52 天），不是 7 檔
      - 本地（讀 enriched 2025-06～09）：0050 分割後 annual_vol_20d>2 共 20 個交易日（到 2025-07-15）；momentum_60d<-0.5 共 60 天（到 2025-09-09）；high_60d≥188 共 59 天；boll_lower<0 共 12 天；atr_14>2 共 30 天
      - docs/code-review-2026-09-22.md：以 adj|還原|復權|除權 搜尋，都沒有相關條目，所以這不是已知問題
      - 所有腳本與輸出都放在 AUDIT_SCRATCH/work/verify_a04_adj/；結束後 git status 與開始時相同（仍是原本 3 個已修改檔）
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：台股 enriched 固定不做還原。根因有兩個呼叫點，都傳入空的 factors：enriched_refresh.py:95 與 K 線 API 的 kline.py:435。因此全部 1,423,597 列的 close 都等於 raw_close，違反 CONTRIBUTING.md:100「enriched OHLC 為前復權」的契約。動能、年化波動、ATR、布林、60 日高低與相關訊號都在除權息日與分割日出現人為跳空。

0050 在 2025-06-18 進行 1 拆 4 分割（188.65→47.57）後的影響：
- 當日 change_pct 為 -74.8%。
- momentum_60d 在 -70%～-74% 之間，持續整整 60 個交易日（2025-06-18～09-09）；還原後約為 +2%～+16%。
- annual_vol_20d 約 267%，持續 20 個交易日（2025-06-18～07-15）；還原後約 19%。
- atr_14 當日為 12.86（還原後 0.73），而且因為 EWM 衰減很慢，到 2025-08-15 仍高估 85%。
- boll_lower 在 06-25 降到 15.09，06-27 起變成負值。
- signal_n_day_low 誤觸發 4 天。

除息的影響：以 TWT49U 參考價還原後，2026-09-23 的 2379 momentum_20d 為 0.0368（還原後 0.0736）、momentum_60d 為 -0.0558（-0.0223）、annual_vol 為 0.354（0.304）、high_60d 為 840（811.19）；00878 momentum_60d 為 0.0475（0.0798）；3034 momentum_60d 為 0.0187（0.0638）。2026-07 以後，ATR 最大高估 14.7%，年化波動最大高估 66.5%。2026-06 以後，自選股有數十次均線、布林、MACD 與 60 日高低訊號因此翻轉。

TWT49U 期間內，自選股有 7 檔除息；但以 2026-09-23 的 60 日窗計算只有 6 檔，6196 已在窗外。00885、00893 在此期間沒有除權息。
    - 理由：我寫了三支腳本，用本地 parquet 與官方參考資料重新計算。只讀 parquet，也只呼叫純計算函式 compute_enriched；呼叫前把 _custom_signal_exprs 預設為 {}，避免讀取使用者的自訂訊號設定。finding 裡的每個數值都能逐位重現，所以判定成立，嚴重度維持 P1：這是錯誤的金融口徑，違反 CONTRIBUTING.md:100 的前復權契約。

(1) 根因成立。enriched_refresh.py:95 固定傳入空的 factors。全部 1,423,597 列 enriched 中，close≠raw_close 的列數為 0；自選 9 檔共 5,943 列，close≠raw_close 與 high≠raw_high 的列數也都是 0。我用 compute_enriched(factors=空) 重算，與已存 enriched 的最大絕對差為 0.0。

(2) 0050 分割成立。本地原始資料 2025-06-10 收盤 188.65，06-18 開盤 47.5、收盤 47.57，中間 06-11～06-17 沒有資料，所以 0050 只有 656 列。以 ex_factor=4 還原前後對照如下：
- 2025-06-18 change_pct：-0.747840 → 0.00864
- 2025-06-18 momentum_60d：-0.737327 → 0.050690
- 2025-06-18 annual_vol_20d：2.672053 → 0.197539
- 2025-06-18 atr_14：12.864955 → 0.730703
- 2025-06-25 boll_lower：15.087896 → 43.504
- signal_n_day_low：06-18、06-19、06-20、06-23 連續 4 天為真；還原後 0 天
- 2025-08-15 momentum_60d：-0.708767 → 0.164932

(3) 除息部分成立。我用 TWT49U 算出 ex_factor＝除權息前收盤價／參考價，並先核對：7 筆除權息前收盤價都與本地前一交易日收盤一致。2026-09-23 的對照如下：
- 2379 momentum_20d：0.036785 → 0.073602
- 2379 momentum_60d：-0.055831 → -0.022303
- 2379 annual_vol_20d：0.353597 → 0.303537
- 2379 high_60d：840 → 811.193
- 00878 momentum_60d：0.047463 → 0.079776
- 3034 momentum_60d：0.018692 → 0.063836
除息日的 TR：2379 在 09-08 為 49 → 41；00878 在 08-18 為 1.45 → 0.46。2026-07-01 以後，ATR 最大相對誤差 +14.74%（00878，08-18），年化波動最大相對誤差 +66.54%（00878，09-07）。

需要修正措辭的地方有兩處：
- 年化波動超過 100% 只持續 20 個交易日（2025-06-18～07-15，最高 2.677），不是 60 個交易日；momentum_60d 低於 -50% 則剛好持續 60 個交易日（2025-06-18～09-09）。
- TWT49U 期間（2026-06-01～09-23）內有除息的自選股是 7 檔。若以 2026-09-23 的 60 日窗（基準日 2026-06-30）計算只有 6 檔，因為 6196 的 06-24 除息已在窗外。

finding 沒提到的新面向有三點：
- 分割造成的 ATR 失真衰減很慢：2025-08-15 為 1.173，還原後 0.633（+85%）；2025-09-11 仍高 19%。
- boll_lower 在 2025-06-27 已降為負值（-6.53），07-02 為 -28.3。
- 除息也會翻轉訊號：2026-06 以後自選股中，signal_n_day_high 有 7 次、boll_breakout_upper 有 7 次、macd_dead 有 9 次、ma20_breakdown 有 6 次，未還原與還原的結果不同。

另外，K 線 API 的台股路徑 kline.py:435 同樣以空的 factors 計算，是第二個呼叫點。
    - 證據：
      - backend/app/taiwan/enriched_refresh.py:95 `enriched = compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)`；pipeline.py:1023-1024 只有 factors 非空時才呼叫 _apply_adj_factor
      - CONTRIBUTING.md:100「enriched 的 `open/high/low/close` 為前復權價格。」；pipeline.py:6「存储列: symbol, date, OHLCV(前复权)」；pipeline.py:493-502 動能、:518-526 年化波動、:422-427/:482-485 ATR、:416-417/:449-451 布林、:437-440 60 日高低，全部直接使用 close/high/low
      - backend/app/api/kline.py:435 台股 K 線路徑同樣呼叫 `compute_enriched(local, factors=pl.DataFrame())`（finding 未列出的第二個呼叫點）
      - data\adj_factor 與 data\adj_factor_etf 皆為空目錄（ls -la 只有 . 與 ..）
      - v1_load.py 輸出：enr rows 1423597，close != raw_close（全市場）: 0；自選 9 檔 5943 列，close≠raw_close 0、high≠raw_high 0；enriched close 與 taiwan/daily close 不同的列數 0
      - v2_0050.py 輸出：0050 原始資料 2025-06-10 收盤 188.65，2025-06-18 O/H/L/C 47.5/47.72/47.14/47.57（06-11～06-17 無資料）；已存 enriched 在 2025-06-18 的 change_pct -0.74784、momentum_60d -0.737327、annual_vol_20d 2.672053、atr_14 12.864955；2025-06-25 boll_lower 15.087896；n_day_low 在 06-18/19/20/23 為真；2025-08-15 momentum_60d -0.708767
      - v2_0050.py 以 ex_factor=4.0 還原後的 2025-06-18 值：momentum_60d 0.05069、change_pct 0.00864、annual_vol_20d 0.197539、atr_14 0.730703、n_day_low 0 天；2025-08-15 atr 為 1.173，還原後 0.633；momentum_60d<-0.5 共 60 天（2025-06-18～2025-09-09）；annual_vol_20d>1.0 只有 20 天（2025-06-18～07-15），最大值 2.677；boll_lower 在 06-27 為 -6.53
      - v3_div.py：TWT49U 自選股事件：6196 06-24（541→534.55）、2454 07-07（4125→4100.49）、3034 07-10（542→519）、0050 07-21（99.2→98.6）、3545 08-04（50.1→49.28）、00878 08-18（33.75→32.74）、2379 09-08（729→704）；除權息前收盤價 7 筆都與本地前一交易日收盤一致
      - v3_div.py 2026-09-23（前為未還原值，後為還原值）：2379 momentum_20d 0.036785→0.073602、momentum_60d -0.055831→-0.022303、annual_vol_20d 0.353597→0.303537、high_60d 840→811.1934、atr_14 28.673→27.970；00878 momentum_60d 0.047463→0.079776；3034 momentum_60d 0.018692→0.063836；0050 0.043135→0.049483；3545 -0.077338→-0.061985
      - v3_div.py 除息日 TR：2379 在 2026-09-08 為 49.0，還原後 41.0；00878 在 2026-08-18 為 1.45，還原後 0.46。2026-07-01 以後相對誤差最大值：atr_14 +14.74%（00878，2026-08-18：0.6860 對 0.5979）、annual_vol_20d +66.54%（00878，2026-09-07：0.1856 對 0.1115）
      - v3_div.py：2026-09-23 的 60 日窗 shift(60) 基準日為 2026-06-30，因此窗內只有 6 檔除息（2454、3034、0050、3545、00878、2379），6196 的 06-24 已在窗外
      - v3_div.py 2026-06-01 以後自選股的訊號差異（未還原與還原不同）：signal_n_day_high 7 次、boll_breakout_upper 7 次、macd_dead 9 次、ma20_breakdown 6 次、ma_dead_5_20 5 次、boll_breakdown_lower 2 次、n_day_low 1 次
      - 旁證（推測）：本地 taiwan/daily 與 taiwan/index_daily 都沒有 2026-07-10 分區，推測當日全市場休市；3034 在 07-13 收盤 467.5，正好等於 TWT49U 以參考價 519 計算的跌停價
      - https://money.udn.com/money/story/124512/8553991、https://news.cnyes.com/news/id/5979734 等（WebSearch 摘要）：0050「1拆4」，6/10 為分割前最後交易日，6/11～6/17 暫停交易，6/18 恢復交易，恢復買賣參考價 47.16 元（188.65/4=47.16，與本地資料一致）
      - 腳本位置：AUDIT_SCRATCH/work/verify-a04adj/v1_load.py、v2_0050.py、v3_div.py；repo 的 git status 與開始時相同（仍是原本 3 個既有修改），未寫入 data\

### [P2 → P2] 盤中增量路徑的 high_60d/low_60d 改用最高／最低價，與盤後收盤價口徑不同，導致「創60日新高」盤中幾乎不觸發（確認成立）

- **主張**：全量路徑的 high_60d/low_60d 是 60 日收盤價極值；盤中增量路徑卻用 _high_59d/_low_59d（最高價／最低價）與當日 high/low 取極值。同一欄位、同一訊號（close >= high_60d）盤中與盤後結果不同，欄位標籤也互相矛盾。
- **正確的定義或數值**：同一欄位在盤中與盤後使用同一定義（收盤價，與 factor.py、matrix.py、market_intelligence 一致），盤中訊號才可比。
- **app 實際行為**：盤中 high_60d 取含當日最高價的 60 日最高價。只有收盤價恰好等於當日最高價且創新高時，訊號才會成立；收盤後 enriched_refresh 又會翻轉。
- **對使用者的影響**：即時行情開啟時，K 線與盤中選股、策略、自訂訊號看到的 60 日高低與「創60日新高／低」跟盤後不同。2454 連三天創收盤新高，盤中都不會觸發。監控規則目前 0 條，警示不受影響，但盤中策略與自訂訊號受影響。
- **修法建議**：repository.py 的 _high_59d/_low_59d 改為 close.tail(59).max()/min()，增量路徑改取 max(_high_59d, close)；並統一欄位標籤為「60日最高收盤價」。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:437-440 pl.col("close").rolling_max(60) / rolling_min(60)
  - backend/app/indicators/pipeline.py:1589-1599 max_horizontal(_high_59d, high) / min_horizontal(_low_59d, low)
  - backend/app/repository.py:913-914 pl.col("high").tail(59).max() / pl.col("low").tail(59).min()
  - backend/app/indicators/pipeline.py:655-656 與 :1680-1681 訊號同為 close >= high_60d
  - 標籤：pipeline.py:162-163「60日最高价」；strategy\monitor.py:65「60日最高」；strategy\prompts\strategy-guide.md:217「60日最高/最低价」；backtest\factor.py:85「60日最高收盘价」
- **線上證據**：
  - （無權威來源規定台股「創N日新高」必須以收盤價或最高價計算；https://www.cnyes.com/twstock/a_technical2.aspx 沒有定義。問題在於同一系統兩條路徑不一致，不在慣例選擇。）
- **本地計算證據**：
  - a04_step3.py：重建 live_agg，對 2026-08-03～09-23 共 38 天 × 9 檔（342 列）逐日跑 compute_enriched_today，與全量比對。high_60d max|abs|=410（12.4%），low_60d max|abs|=160（6.9%）
  - signal_n_day_high 有 14 列不一致，全部是盤中為否、盤後為是。例如 2454.TWSE 在 2026-09-21、09-22、09-23，收盤 5010、5180、5185，盤中 high_60d 為 5035、5480、5480，全量為 5010、5180、5185；0050 在 09-21～09-23；00878 在 09-16～09-22
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：全量路徑（pipeline.py:437-440）與已存的 enriched 分區，把 high_60d／low_60d 定義為含當日的 60 日收盤價極值；前端說明與 factor.py、matrix.py、market_intelligence 也都是收盤價口徑。盤中增量路徑 compute_enriched_today（pipeline.py:1589-1599，狀態值來自 repository.py:913-914）卻改用「前 59 日最高價／最低價，再併入當日最高價／最低價」，而且訊號同樣是 close >= high_60d。

因此盤中 60 日新高要同時滿足兩個條件才會成立：close 等於當日最高價，而且不低於前 59 日的最高價。即使收盤價等於當日最高價，只要前期最高價更高也不會觸發（例：3034 在 09-18）。

實測 342 列裡有 14 列「盤中否、盤後是」，high_60d 最大差 12.4%。盤後重算會覆寫回收盤價口徑。增量結果為空而退回全量時，盤中也會暫時改用收盤價口徑。

外部慣例（Donchian、52 週高）常用最高價與最低價，所以這不是「最高價口徑一定錯」。問題在於同一欄位在兩條路徑的定義不一致，而且盤中公式把當日最高價併進比較基準，讓訊號在任一種口徑下都幾乎無法成立。

受影響範圍是即時輪詢池（這 9 檔）當日列的 60 日高低值與新高／新低訊號，消費端是盤中選股、自訂訊號與盤中策略。K 線與台股自選頁不使用這個欄位，監控規則目前 0 條，警示不受影響。

修法方向：repository.py 的 _high_59d／_low_59d 改用 close.tail(59) 的 max／min，增量路徑改取 max(_high_59d, close)／min(_low_59d, close)，並把標籤統一為「60日最高／最低收盤價」。
    - 理由：我親自讀了程式碼，也重跑了本地數據，finding 的核心主張成立。嚴重度判 P2 恰當。有兩處細節要修正。

1. 程式碼（現況行號與 finding 一致）：
   - 全量路徑 pipeline.py:437-440 用 close 的 rolling_max(60)／rolling_min(60)。
   - 盤中增量路徑 pipeline.py:1589-1599 取 max_horizontal(_high_59d, high)／min_horizontal(_low_59d, low)。
   - 這兩個狀態值來自 repository.py:913-914 的 high.tail(59).max()／low.tail(59).min()，也就是最高價與最低價。
   - 訊號在兩條路徑都是 close >= high_60d（pipeline.py:655-656 與 1680-1681）。
   - 盤中增量路徑由 quote_service.py:1681 的 _enrich_today_incremental 呼叫，repository.py:713 的缺列補回也會呼叫。
   - 這兩條路徑都從上游合併時的 fd79b63 就存在（git log -S），docs/code-review-2026-09-22.md 沒有記錄這個問題。

2. 定義：外部慣例並不統一。
   - Wikipedia 的 Donchian channel 定義是「Upper = HighestHigh(N)」「Lower = LowestLow(N)」。
   - StockTitan 寫 52 週高低是「not closing prices, but the actual intraday highs and lows」。
   - 所以「用最高價算 N 日高」本身是合法的業界口徑，不能說它絕對錯。
   - 但本系統自己的契約是收盤價：
     - 前端 signals.ts:108 的訊號說明是「收盤價創近 60 日新高」。
     - factor.py:85 的標籤是「60日最高收盘价」。
     - matrix.py:3908-3911、market_intelligence.py:334-341 都用 close。
     - 已存的 enriched 分區也全部是收盤價口徑。
   - 另外，盤中公式在任一種口徑下都是壞的。
     - Donchian 式突破要跟「前 N 日」（不含當日）的高點比。
     - 盤中卻把「當日最高價」放進 high_60d，再比 close >= high_60d。
     - 結果必須同時滿足兩個條件才會成立：close 等於當日最高價，而且不低於前 59 日的最高價。
   - 因此這是明確的功能錯誤，不只是口徑的選擇問題。

3. 本地重現（純讀 parquet，不寫檔）：
   - 期間與範圍：2026-08-03～09-23 共 38 天、9 檔自選，共 342 列。
   - 已存的 high_60d 與 close.rolling_max(60) 的差異 max|abs| 為 0.0，342 列全部等於收盤價口徑；與最高價口徑則 342 列全部不同。
   - 兩種口徑的差距：high_60d 最大 410（12.4%），low_60d 最大 160（6.9%）。
   - 訊號：全量為真有 14 列，增量為真 0 列，14 列全部「盤中否、盤後是」。低點訊號兩邊都是 0 列。
   - 我另外實際呼叫了 compute_enriched_today，2454 的結果：

     | 日期 | 收盤 | 當日最高 | 盤中 high_60d／訊號 | 全量 high_60d／訊號 |
     |---|---|---|---|---|
     | 09-21 | 5010 | 5035 | 5035／False | 5010／True |
     | 09-22 | 5180 | 5480 | 5480／False | 5180／True |
     | 09-23 | 5185 | 5265 | 5480／False | 5185／True |

   - 以上與 finding 所述一字不差。

4. 需要修正的細節：
   - 條件描述不精確：finding 說「只有收盤價恰好等於當日最高價且創新高時才成立」。反例是 3034 在 09-18，收盤價等於最高價，都是 558，但前 59 日的最高價是 572，所以仍然不觸發。正確條件是「close 等於當日最高價，而且不低於前 59 日最高價」。
   - 使用者影響有誇大：finding 提到「K 線」受影響，但前端 K 線沒有使用 high_60d。前端引用它的只有 stock-table、signals.ts、watchlist-columns。台股自選頁走 taiwan/technical_indicators.py，這個模組根本不產生 high_60d。
   - 實際受影響的範圍：即時輪詢池（這 9 檔）當日 enriched 列的 high_60d、low_60d 與 60 日新高／新低訊號。消費端是盤中選股（stock-table.ts:31 的「60日新高」標籤）、自訂訊號與盤中策略。
   - 同一天內口徑也可能翻轉：quote_service.py:1722-1768 在增量結果為空時會退回全量 compute_enriched，改用收盤價口徑。這是 finding 沒提到的面向。
   - 問題是暫時性的：盤後重算會覆寫成正確值，資料沒有遭到持久破壞。監控規則目前 0 條，警示不受影響。

5. 嚴重度：它不造成資料破壞，也會自行復原，所以不到 P0／P1。但它讓一個有明文定義的訊號在盤中幾乎失效，屬於「明確功能錯誤」，維持 P2。
    - 證據：
      - backend/app/indicators/pipeline.py:437-440 全量：pl.col("close").rolling_max(60)／rolling_min(60)
      - backend/app/indicators/pipeline.py:1589-1599 增量：max_horizontal(_high_59d, high)／min_horizontal(_low_59d, low)
      - backend/app/repository.py:913-914 _high_59d = high.tail(59).max()；_low_59d = low.tail(59).min()
      - backend/app/indicators/pipeline.py:655-656 與 :1680-1681 兩條路徑的訊號都是 close >= high_60d／close <= low_60d
      - frontend/src/lib/signals.ts:104-108 signal_n_day_high 的說明是「收盤價創近 60 日新高」（系統自己的定義是收盤價）
      - backend/app/backtest/factor.py:85「60日最高收盘价」；matrix.py:3908-3911 valid_rolling_max(market.close…)；taiwan\market_intelligence.py:334-341 close.rolling_max(TREND_EXTREME_WINDOW)
      - backend/app/services/quote_service.py:1681 盤中呼叫 compute_enriched_today；:1722-1768 增量結果為空時退回 compute_enriched（收盤價口徑），同一個盤中口徑可能翻轉
      - backend/app/taiwan/technical_indicators.py:46 台股自選頁的指標集不含 high_60d；前端 K 線沒有引用 high_60d（grep frontend/src 只命中 stock-table、signals、watchlist-columns）
      - 本地：uv run python scratchpad\audit\work\v_hl60_def\probe.py → window rows 342；stored vs full(close) max abs diff: 0.0；stored vs inc(high) rows diff>1e-6: 342；max dh 410.0 rel 0.124；max dl 160.0 rel 0.0693；sig_high full true 14、inc true 0；high mismatches 14（0050 09-21～23、00878 09-07/16/17/18/21/22、2454 09-07/21/22/23、3034 09-18）
      - 本地：3034.TWSE 2026-09-18 close=558=high，但 inc_h=572，訊號仍為 false（反證 finding 所說「收盤價等於最高價就會成立」）
      - 本地：uv run python scratchpad\audit\work\v_hl60_def\call_fn.py，實際呼叫 compute_enriched_today → 2454 09-21 incremental high_60d 5035.0 signal False／stored 5010.0 True；09-22 5480.0 False／5180.0 True；09-23 5480.0 False／5185.0 True
      - 線上：https://en.wikipedia.org/wiki/Donchian_channel「Upper = HighestHigh(N)」「Lower = LowestLow(N)」（N 期高低點業界常用最高價與最低價）
      - 線上：https://www.stocktitan.net/articles/52-week-highs-and-lows「not closing prices, but the actual intraday highs and lows」（52 週高低採盤中極值），可見外部口徑不統一；本系統的契約是收盤價
      - git log -S "_high_59d" → 只有 fd79b63（上游合併）；docs/code-review-2026-09-22.md 沒有 high_60d 相關記錄，屬於新 finding
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：全量路徑（pipeline.py:437-440）的 high_60d/low_60d 是 60 日收盤價極值。盤中增量路徑（pipeline.py:1589-1599，狀態來自 repository.py:913-914）則把前 59 日的最高價／最低價和當日 high/low 取極值。兩者口徑不同，但訊號都用 close >= high_60d 判斷。
以 2026-08-03～09-23 共 38 天 × 自選 9 檔（342 列）、收盤後快照重算：
- high_60d 每一列都比全量高。max|abs| 為 410（2454 2026-08-21，4560 對 4970，9.0%）；最大偏差比例 12.4%（2379 2026-09-15，863 對 970）。
- low_60d 有 336 列比全量低。max|abs| 為 160（2454 2026-08-03）；最大偏差比例 6.9%（2379 2026-09-09，678 對 631）。
- signal_n_day_high 全量成立 14 次，增量 0 次：0050 09-21～23、00878 09-07 與 09-16～22、2454 09-07 與 09-21～23、3034 09-18。signal_n_day_low 沒有差異。
增量路徑的訊號要成立，最新價必須等於當日盤中累計最高價，而且不低於前 59 日所有最高價。3034 09-18 收盤等於當日最高價 558，仍因前期最高價 572 而不成立。所以盤中只會在創高當下短暫成立，收盤快照幾乎都是否，盤後 enriched 重算又會翻回收盤價口徑。
0050、00878 受影響的前提是目前 ETF 維表為空、ETF 走股票增量路徑；ETF 維表有資料時，ETF 走全量重算，不受影響。使用者目前沒有自訂訊號，所以受影響的是盤中 K 線、選股與策略看到的 60 日高低和創高訊號。
    - 理由：我用兩支彼此獨立的腳本重算，都完整重現 finding 的數字。
(1) s1_formula.py 不引用任何 app 程式碼，自己實作兩種定義：全量是收盤價 tail(60) 的極值；增量是基準日往前 90 個日曆天的切片中，取 tail(59) 的最高／最低價，再和當日 high/low 取極值。
(2) s2_app.py 直接呼叫 app 的純計算函式：compute_indicators、compute_signals、KlineRepository._build_live_agg（掛在以 object.__new__ 建立、不跑 __init__ 的物件上，只改記憶體屬性），再接 compute_enriched_today；當日 OHLCV 取自 data/taiwan/daily。
兩支腳本的結果一致：9 檔 × 38 天共 342 列，high_60d 每一列都不同，max|abs|=410、最大偏差比例 12.4%；low_60d 有 336 列不同，max|abs|=160、最大偏差比例 6.93%。signal_n_day_high 有 14 列不一致，全部是全量為 true、增量為 false；signal_n_day_low 沒有不一致。
全量路徑重算的 high_60d、low_60d、訊號，與 kline_daily_enriched 的落盤值 0 筆不符，證實盤後落盤採收盤價口徑。
342 列的 high/low/close 全部與官方 TWSE STOCK_DAY 參考資料一致，0 筆不符。所有列 close==raw_close，所以復權不會干擾比較。基準窗口有 63～64 列，tail(59) 的資料量足夠。

需要修正或補充的細節（不影響結論）：
(a) finding 寫的「410（12.4%）」其實是兩個不同列的最大值。410 出現在 2454 2026-08-21（4560 對 4970，8.99%）；12.4% 出現在 2379 2026-09-15（863 對 970）。low 的 160 出現在 2454 2026-08-03（5.08%）；6.93% 出現在 2379 2026-09-09（678 對 631）。
(b) 14 列除了 finding 舉的例子，還有 2454 09-07、00878 09-07、3034 09-18。3034 09-18 當天收盤 558 等於最高價，但前 59 日最高價 572（08-05）仍然高於它，所以即使「收盤＝當日最高」訊號也不成立。
(c)「盤中幾乎不觸發」是用收盤後快照模擬的結果（增量 0 次、全量 14 次）。真實盤中只要最新價恰好等於盤中累計最高價，且高於前 59 日所有最高價，訊號仍會瞬間成立。
(d) 0050、00878 會進入股票增量路徑，是因為 data/instruments_etf 與 data/instruments_index 都是空的，ETF 集合為空。如果 ETF 維表有資料，這兩檔會走 asset_type=etf 的全量重算（use_incremental 要求 asset_type=="stock"），就不受影響。
(e) data/user_data/custom_signals 是空目錄，所以對這位使用者而言「自訂訊號受影響」目前只是潛在影響。
嚴重度維持 P2：同一欄位、同一訊號在盤中與盤後結果不一致，屬於明確的功能錯誤；使用者監控規則 0 條，警示不受影響。
    - 證據：
      - 程式碼 backend/app/indicators/pipeline.py:437-440：high_60d/low_60d = pl.col("close").rolling_max(60)/rolling_min(60)（全量，收盤價口徑）
      - 程式碼 backend/app/indicators/pipeline.py:1589-1599：增量 high_60d=max_horizontal(_high_59d, high)，low_60d=min_horizontal(_low_59d, low)
      - 程式碼 backend/app/repository.py:913-914：_high_59d=pl.col("high").tail(59).max()、_low_59d=pl.col("low").tail(59).min()
      - 程式碼 backend/app/indicators/pipeline.py:655-656 與 :1680-1681：兩條路徑的訊號都是 close >= high_60d / close <= low_60d
      - 標籤 pipeline.py:162-163「60日最高价/最低价」；strategy\monitor.py:65「60日最高」；strategy\prompts\strategy-guide.md:217「60日最高/最低价」；backtest\factor.py:85「60日最高收盘价」，全部已實際查看
      - 程式碼 backend/app/services/quote_service.py:1708-1713：只有 asset_type=="stock" 才走增量；ETF 走全量 compute_enriched。實際檢查 data/instruments_etf 與 data/instruments_index 都是空目錄，所以 ETF 集合為空，0050、00878 被當成股票處理
      - 指令：cd backend && uv run python ...\audit\work\verify-h60\s1_formula.py（獨立公式，不引用 app）→ 342 列；full 對落盤 h60/l60/訊號的不符筆數都是 0；high_60d 342 列不同、max abs 410.0、最大比例 0.12399；low_60d 336 列不同、max abs 160.0、最大比例 0.06932；signal_n_day_high 不一致 14 列（全量 true、增量 false）；signal_n_day_low 不一致 0 列；增量 true 共 0 次、全量 true 共 14 次
      - 指令：cd backend && uv run python ...\audit\work\verify-h60\s2_app.py（app 純函式 _build_live_agg + compute_enriched_today，端到端）→ 與 s1 完全相同：342 列、410.0／0.12399、160.0／0.06932、14 列不一致、增量 0 次 true 對全量 14 次 true
      - 數值並列（finding → 本次）：high_60d max|abs| 410 → 410.0；12.4% → 12.399%（出現在 2379 2026-09-15，863 對 970，不是 410 那一列）；low_60d max|abs| 160 → 160.0；6.9% → 6.932%（出現在 2379 2026-09-09，678 對 631）；訊號不一致 14 → 14
      - 2454.TWSE：09-21 收盤 5010，全量 h60 5010，增量 5035；09-22 收盤 5180，全量 5180，增量 5480；09-23 收盤 5185，全量 5185，增量 5480。全部與 finding 一致
      - 14 列不一致的完整清單：0050 09-21/22/23；00878 09-07/16/17/18/21/22；2454 09-07/21/22/23；3034 09-18。其中 3034 09-18 的 high=close=558，但前 59 日最高價是 572（2026-08-05）
      - 官方比對：scratchpad\audit\reference\csv\twse_stock_day_202608_202609.csv 與 342 列的 high/low/close 不符筆數都是 0；2454 前一日最高價 5480 出現在 2026-09-22（官方）
      - 復權檢查：9 檔自 2025-12-01 起共 1781 列，close != raw_close 為 0 列；twt49u 顯示期間內有除息（例如 2454 07-07、00878 08-18），但資料沒有做復權，兩條路徑的口徑一致，不構成干擾
      - data/user_data/custom_signals 是空目錄：目前沒有使用者自訂訊號
      - 我的腳本與結果只寫在 scratchpad\audit\work\verify-h60\（s1_formula.py、s1_result.csv、s2_app.py、s2_result.csv）；同目錄的 repro.py、repro_fn.py、repro_rows.csv 不是本 agent 建立的。repo 的 git status 與開始時相同（只有原本的 3 個已修改檔）

### [P2 → P2] 盤中增量路徑的布林與年化波動用母體標準差、全量用樣本標準差，且突破比較子一邊是 >= 一邊是 >（確認成立）

- **主張**：全量路徑 boll 與 annual_vol_20d 用 polars rolling_std（ddof=1），增量路徑用 E[x²]−E[x]²（除以 20，即母體）。布林突破訊號在增量路徑用 >=/<=，全量用 >/<。盤中 K 線最後一根的布林點與前面各根口徑不同；布林訊號可能盤中成立、盤後翻轉。另外，Bollinger 原始定義是母體標準差，全量路徑（樣本）反而偏離原作。
- **正確的定義或數值**：盤中與盤後同一口徑：同樣的 σ 定義（建議依 Bollinger 原定義用母體，或全系統明訂樣本）與同樣的比較子。
- **app 實際行為**：盤中是母體 σ 加上 >=，盤後是樣本 σ 加上 >，同一天的值與訊號在收盤後改變。
- **對使用者的影響**：即時行情開啟時，K 線當日的布林點比前一根口徑窄 2.6%，會出現折角；盤中策略或自訂訊號的布林突破可能在收盤後翻轉；annual_vol_20d 盤中比盤後低約 2.5%。
- **修法建議**：增量公式改用 (sq_sum − sum²/20)/19（或全量改 ddof=0，含 matrix.py）；比較子統一為 >/<。選定口徑後寫進 ENRICHED_COLUMNS 說明。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:417 rolling_std(20)（預設 ddof=1）；:522-526 annual_vol 同樣 rolling_std(20)
  - backend/app/indicators/pipeline.py:1524-1528 boll_var = boll_sq_sum / 20 - boll_ma ** 2
  - backend/app/indicators/pipeline.py:1612-1621 vol_var = total_sq_sum / 20 - vol_mean ** 2
  - backend/app/indicators/pipeline.py:657-658（> / <）與 :1669-1670（>= / <=）
  - backend/app/api/kline.py:576-583 活蠟燭直接採用增量路徑的 boll_upper/boll_lower/atr_14/vol_ratio_5d
  - backend/app/backtest/matrix.py:3895 回測也用 ddof=1
- **線上證據**：
  - https://en.wikipedia.org/wiki/Bollinger_Bands 「Since Bollinger Bands use the population method of calculating standard deviation, the proper divisor for the sigma calculation is n, not n − 1.」
  - https://www.cnblogs.com/citycomputing/p/10447657.html 實測結論「通達信使用的是總體方差」（中國來源另有 STD 為估算標準差之說，互相矛盾）
- **本地計算證據**：
  - a04_step3.py（342 列）：boll_upper 增量與全量 max|abs|=19.20（2454），相對誤差最高 0.56%；annual_vol_20d 相對誤差最高 2.53%（=1−√(19/20)）
  - signal_boll_breakout_upper 不一致 1 列：2379.TWSE 在 2026-09-22，close=755，盤中上軌 754.23（>= 成立），全量 755.10（> 不成立）
  - a04_step2.py：全量帶寬比母體寬 2.598%
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：布林帶與 annual_vol_20d 在盤中（增量）和盤後（全量）用不同的標準差口徑。
- 全量路徑 pipeline.py:417、:524，以及回測 matrix.py:3895、:3914 都用樣本標準差（ddof=1）。
- 增量路徑 pipeline.py:1527、:1616 用 E[x²]−E[x]²，除以 20，是母體標準差。
- 母體 σ 恆比樣本 σ 小 2.53%（=1−√(19/20)）。

即時行情開啟時，台股自選標的的增量結果會寫進當日 enriched 分區，也會成為 K 線最後一根活蠟燭。收盤後 daily_update 的 refresh_recent 再用全量口徑覆寫，所以同一天的布林與年化波動值會在盤後改變。實例：2379.TWSE 在 2026-09-22 收 755，母體上軌 754.23、樣本上軌 755.10，盤中會判定突破，盤後翻成不突破。

兩個指標的正確口徑相反，修法要分開：
- 布林依 John Bollinger 原定義是母體 σ，應把全量路徑與 matrix.py 改成 ddof=0，讓增量維持母體。
- 歷史波動率慣例是樣本 σ（n−1），應把增量路徑改成 (sq_sum − sum²/20)/19。

盤中用 >=／<=、盤後用 >／< 的比較子差異確實存在，應統一為 >／<。但價格和浮點上軌幾乎不會剛好相等，實務影響可以忽略；訊號翻轉主要來自 σ 口徑。
    - 理由：我讀了 finding 引用的每一處程式碼，行號都和現況一致，主張成立。
1. 全量路徑用 polars rolling_std(20)，預設 ddof=1（樣本標準差），布林帶與 annual_vol_20d 都是。本地實測 sd(ddof=1)/sd(ddof=0) 恆為 1.025978，等於 √(20/19)。
2. 增量路徑 compute_enriched_today 的布林與年化波動都用 E[x²]−E[x]²，除以 20，是母體標準差。它的狀態欄 _boll_partial_sq_sum／_vol_19d_pct_sq_sum 由 repository._build_live_agg 用 tail(19) 產生。
3. 突破訊號的比較子：全量用 >／<，增量用 >=／<=。
4. 這條路徑在使用者情境下確實會跑：
   - QuoteService 對台股輪詢後呼叫 _flush_live_enriched(asset_type="stock")，live_agg 不為空就走增量，結果再 merge 寫進今日的 enriched 分區。
   - K 線 API 的歷史列取自 enriched 表（全量，ddof=1），最後一根活蠟燭則用 get_enriched_latest() 的增量值（ddof=0）。前端 EChartsCandlestick 直接畫後端給的 boll_upper，自己不重算。
   - 收盤後 daily_update 呼叫 TaiwanEnrichedRefreshService().refresh_recent()，用 compute_enriched 覆寫當日分區，所以盤中值在盤後會換成樣本口徑。
   - instruments_etf 目錄是空的，所以 0050 等 ETF 應該也歸在 stock、走增量路徑。這一點是推測，但 enriched 表裡確實有 0050.TWSE 的 boll_upper。
5. 用真實資料重算：2379.TWSE 在 2026-09-22 收 755，樣本上軌 755.0977（> 不成立），母體上軌 754.2267（>= 成立）。磁碟上該日分區存的是 boll_upper 755.097674、signal=false，與全量一致。所以若盤中最後一筆報價就是 755，增量會亮突破訊號，盤後會翻成 false。
6. 定義：Bollinger 本人的網站寫明用母體標準差，TA-Lib 提案整理的 TA_STDDEV、pandas-ta、TTR 也都用 n。因此布林的全量路徑（含 matrix.py）偏離原作，finding 這點成立。

需要修正的三處：
(a) finding 把兩個指標一起處理，但兩者的正確口徑相反。布林依原作應為母體（n），該改的是全量路徑與 matrix.py。歷史波動率業界慣例是樣本（n−1，並乘 √252），該改的是增量路徑。finding 的建議只給「增量改 /19，或全量改 ddof=0」二選一，沒有分開處理。
(b) >= 和 > 的差異在實務上幾乎不會觸發，因為上軌是浮點計算值，與成交價剛好相等的機率極低。2379 的翻轉完全來自 σ 口徑不同，不是比較子。
(c) 「窄 2.6%」的精確說法：母體 σ 比樣本 σ 小 2.53%（1−√(19/20)），反過來說樣本 σ 比母體大 2.60%。這是帶寬的差距。換算成上軌的價格位移只有約 0.1–0.4%，K 線上的折角很輕微。

嚴重度維持 P2。同一指標盤中與盤後口徑不同，訊號會翻轉，屬於明確的功能錯誤。使用者的監控規則是 0 條，不會觸發告警，影響限於 K 線顯示、盤中選股與策略，達不到 P1。
    - 證據：
      - backend/app/indicators/pipeline.py:417 `pl.col("close").rolling_std(20).over("symbol")`（預設 ddof=1）；:449-451 boll_upper/lower = ma20 ± 2*_boll_std
      - backend/app/indicators/pipeline.py:522-525 annual_vol_20d = _daily_pct.rolling_std(20) * √252（ddof=1）
      - backend/app/indicators/pipeline.py:1524-1528 `boll_var = boll_sq_sum / 20 - boll_ma ** 2`（母體）
      - backend/app/indicators/pipeline.py:1612-1621 `vol_var = total_sq_sum / 20 - vol_mean ** 2`（母體）
      - backend/app/indicators/pipeline.py:657-658 全量 `close > boll_upper`／`close < boll_lower`；:1669-1670 增量 `>=`／`<=`
      - backend/app/repository.py:861-862, 910-911 live_agg 以 tail(19) 產生 _vol_19d_pct_sum/_sq_sum 與 _boll_partial_sum/_sq_sum
      - backend/app/services/quote_service.py:883 對 stock 呼叫 _flush_live_enriched；:1708-1716 live_agg 不為空就走增量 compute_enriched_today；:1774-1778 merge 寫入當日 enriched 分區；:188 _fill_taiwan_turnover_rate 對 .TWSE 列生效，證明台股走這條路徑
      - backend/app/api/kline.py:430/465 歷史列取自 enriched 表（全量）；:514-584 _maybe_inject_live_candle 以 get_enriched_latest() 的增量列覆蓋 boll_upper/boll_lower
      - backend/app/taiwan/daily_update.py:388-390 盤後 TaiwanEnrichedRefreshService().refresh_recent()；backend/app/taiwan/enriched_refresh.py:95,104-108 用 compute_enriched 覆寫當日分區（ddof=1）
      - backend/app/backtest/matrix.py:3895 boll 用 valid_rolling_std(..., ddof=1)；:3914-3918 annual_vol_20d 用 ddof=1
      - 本地計算（AUDIT_SCRATCH/work/v_boll_ddof/check.py；uv run python，讀 data/taiwan/daily 最近 120 個分區）：sd1/sd0 = 1.025978 = √(20/19)
      - 本地計算：2379.TWSE 2026-09-22 close=755.0，up(ddof=1)=755.097674 → `>` 為 false；up(ddof=0)=754.226706 → `>=` 為 true；data/kline_daily_enriched/date=2026-09-22 存 boll_upper=755.097674、signal_boll_breakout_upper=false
      - 本地計算：data/kline_daily_enriched/date=2026-09-23 含 0050.TWSE（boll_upper=112.3776）與 00878.TWSE；data/instruments_etf 為空目錄，repository.py:1329-1336 get_etf_symbol_set 因而回空集合，所以 ETF 應也走 stock 增量路徑（推測）
      - https://www.bollingerbands.com/bollinger-bands（John Bollinger 原作網站）原文：「We use the population calculation for standard deviation」；預設「20 periods ... plus and minus two standard deviations」
      - https://github.com/TA-Lib/ta-lib-proposal-drafts/issues/79：TA_STDDEV 用母體除數 n，ta4j ofPopulation、pandas-ta ddof=0、TTR runSD(sample=FALSE) 一致，「No source disagrees」
      - https://www.macroption.com/historical-volatility-calculation/ 原文：「We are dividing by n - 1 rather than n, as we are calculating sample standard deviation」，並乘 √252：歷史波動率慣例為樣本口徑
      - https://chartschool.stockcharts.com/.../bollinger-bands 只寫「20-day SMA ± (20-day standard deviation of price x 2)」，沒有指明 n 或 n−1
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：全量路徑的 boll 與 annual_vol_20d 用 polars rolling_std(20)，ddof=1，是樣本 σ（pipeline.py:417、:524；matrix.py:3895 也是 ddof=1）。增量路徑用 E[x²]−E[x]²，除以 20，是母體 σ（pipeline.py:1527、:1616）。兩條路徑除此之外完全等價：把增量 σ 乘上 √(20/19)，殘差不超過 3e-11。

固定比例的差距：
- 同樣的收盤資料下，增量的布林帶寬比全量窄 2.53%，也就是全量比增量寬 2.598%。
- annual_vol_20d 每一列都低 2.532%。

自選 9 檔 342 列的重現數字：
- boll_upper 最大絕對差 19.20，最大相對差 0.388%，都出現在 2454 的 2026-09-14。
- 0.56% 是 boll_lower 的最大相對差，不是 boll_upper 的。
- 2379 在 2026-09-22 收盤 755：增量上軌 754.23，訊號成立；全量上軌 755.10，訊號不成立。當天增量路徑一直跑到收盤後，落盤值後來被全量覆蓋，所以同一天的訊號在盤後翻轉。

全市場 38 個交易日：
- 上軌訊號翻轉 343 列，下軌翻轉 392 列，共 606 檔。
- 盤中路徑多出約 9% 的上軌突破訊號、13% 的下軌跌破訊號。

比較子（增量 >= / <=，全量 > / <）確實不一致，但實測只因比較子造成的翻轉是 0 列，所有翻轉都來自 σ 口徑。

Bollinger 原始定義用母體 σ（除以 n）。
    - 理由：這條 finding 的核心數字全部重現了。我自己寫了腳本：先用 taiwan/daily 原始日 K 跑全量 compute_enriched；再依 repository._build_live_agg 的邏輯重建前一日的遞推狀態，呼叫 compute_enriched_today 算增量結果。比對範圍是自選 9 檔在 2026-08-03～09-23 的 38 個交易日，共 342 列，和 finding 的列數相同。

重現結果：
- boll_upper 的最大絕對差是 19.2025，出現在 2454 的 2026-09-14，與 finding 的 19.20 相同。
- 兩條路徑的帶寬比值落在 1.02597835～1.02597835，等於 √(20/19)，也就是全量比增量寬 2.598%。
- annual_vol_20d 的 1−inc/full 每一列都是 0.0253206，等於 1−√(19/20)。這是固定比例，不只是最大值。
- 把增量的 σ 乘上 √(20/19) 之後，boll_upper 殘差不超過 3.3e-11，annual_vol 殘差不超過 5e-16。所以兩條路徑的差異完全來自母體與樣本 σ，沒有別的原因。
- 2379.TWSE 在 2026-09-22 收盤 755，這個收盤價與官方 STOCK_DAY 參考資料一致。增量上軌 754.2267，用 >= 判斷訊號成立；全量上軌 755.0977，用 > 判斷不成立。磁碟上 kline_daily_enriched 目前存的是 bu=755.0977、訊號 false，與全量一致。backend.log 顯示 09-22 當天增量路徑跑了 1031 次，最後一筆在收盤後 15:34，所以使用者當天看到的是增量值，後來才被全量值覆蓋。
- 全市場擴大比對：排除 25 檔 ma20 視窗本來就不一致的新上市標的後，剩 85,383 列。上軌訊號翻轉 343 列，下軌翻轉 392 列，涉及 606 檔。按上軌訊號成立的列數算，全量 3,870 列、增量 4,213 列；下軌全量 3,019 列、增量 3,411 列。

finding 有幾處需要修正：
(1) 「相對誤差最高 0.56%」其實是 boll_lower 的數字（2454 在 09-14 為 0.5600%）。boll_upper 的最大相對誤差是 0.388%。
(2) 比較子 >= 與 > 的差異在程式碼裡確實存在，但全市場 85,383 列裡，只換比較子造成的翻轉是 0 列。所有翻轉都是 σ 口徑造成的，比較子只是次要的一致性問題。
(3) 「窄 2.6%」要看基準：增量比全量窄 2.53%，全量比增量寬 2.60%。

Wikipedia 引文我查過，逐字相符。這個問題在 docs/code-review-2026-09-22.md 裡沒有紀錄。嚴重度維持 P2：這是明確的盤中與盤後口徑不一致，會讓訊號翻轉，但盤後落盤的資料是一致的。
    - 證據：
      - backend/app/indicators/pipeline.py:417 rolling_std(20)；:524 annual_vol 的 rolling_std(20)。實測可證是 ddof=1：增量 σ 乘上 √(20/19) 後，與全量的殘差不超過 3.3e-11
      - backend/app/indicators/pipeline.py:1527 boll_var = boll_sq_sum / 20 - boll_ma ** 2；:1616 vol_var = total_sq_sum / 20 - vol_mean ** 2
      - backend/app/indicators/pipeline.py:657-658 用 > / <；:1669-1670 用 >= / <=
      - backend/app/api/kline.py:576-583：當日 K 棒注入 q 的 boll_upper/boll_lower；歷史 K 棒來自 enriched（全量）。backend/app/backtest/matrix.py:3895 ddof=1
      - 指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-bollmix-repro/repro.py watch。輸出：joined rows 342；boll_upper max |inc-full| 19.202461758778554，出現在 2454.TWSE 2026-09-14；width ratio 1.0259783520792674～1.0259783520897037，√(20/19)=1.025978352085154；annual_vol 1-inc/full 0.02532056551910178～0.02532056551910533；ddof 修正後 max|bu err| 3.3e-11
      - 同一次執行：signal_boll_breakout_upper 不一致 1 列，2379.TWSE 2026-09-22 close 755.0，bu_inc 754.226706 為 true，bu_full 755.097674 為 false；只因比較子造成的翻轉 0 列，只因 σ 造成的翻轉 1 列
      - check_stored.py：boll_upper 最大相對誤差 0.3883%（upper）；lower 為 0.5600%（2454，2026-09-14）。磁碟上 enriched 與全量的 max|bu| 差 4.3e-11；2379 在 09-22 存的是 bu 755.0977、訊號 false；本地收盤價與官方 twse_stock_day CSV 對 342/342 列，最大差 0.0
      - analyze_all.py（全市場，視窗一致的 85,383 列）：上軌翻轉 343 列、下軌翻轉 392 列，共 606 檔；比較子單獨造成的翻轉 0/0；上軌訊號成立列數全量 3870、增量 4213，下軌全量 3019、增量 3411
      - data/backend.log：2026-09-22 共 1031 筆「enriched 增量: 9 只」，最後一筆在 15:34:42
      - https://en.wikipedia.org/wiki/Bollinger_Bands 原文：「Since Bollinger Bands use the population method of calculating standard deviation, the proper divisor for the sigma calculation is n, not n − 1.」

### [P3 → P3] session_progress 以秒計，開盤後數秒量比放大上千倍，signal_volume_surge 幾乎必然觸發（確認成立）

- **主張**：time_factor = 1/session_progress。09:00:05 時 progress=0.000309，factor=3240；只要當下成交量 ≥ 前 5 日均量的 1/1620，量比就 ≥ 2。中國量比以分鐘為單位（最小 1 分鐘，約 240 倍）也有開盤放大，但本實作在第一分鐘內更極端。
- **正確的定義或數值**：開盤初期設最小分母（例如以整分鐘、至少 1～5 分鐘計），或在前 N 分鐘不計算放量訊號。
- **app 實際行為**：開盤第一分鐘的量比與放量訊號幾乎失去意義。
- **對使用者的影響**：即時行情下，開盤初期的盤中選股、策略與自訂訊號會出現大量假放量；監控規則為 0 條，所以不會發出警示。
- **修法建議**：session_minutes_elapsed 改為 max(1, ceil(分鐘))，或 time_factor 設上限；前 N 分鐘 signal_volume_surge 設為 null。
- **稽核者信心**：plausible
- **程式碼證據**：
  - backend/app/taiwan/realtime/calendar.py:140 return float(_minutes_of_day(t) - _minutes_of_day(REGULAR_OPEN_START)) + now.second / 60.0
  - backend/app/indicators/pipeline.py:1579-1580 time_factor = 1.0 / session_progress；:1682 vol_ratio_5d >= 2.0
- **線上證據**：
  - https://wiki.mbalib.com/wiki/%E9%87%8F%E6%AF%94 量比以「當日累計開市時間（分）」計
- **本地計算證據**：
  - a04_step6.py：session_progress(09:00:05)=0.000309（factor 3240.0），(09:00:30)=0.001852（540.0），(09:01:00)=0.003704（270.0），(09:05)=0.018519（54.0）
  - 以 2454 前 5 日均量約 9.33M 股計，只要約 5,760 股（5.8 張）即達 2.0；開盤集合競價量遠大於此（推測，本地無 09:00 分鐘資料可量化）
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：session_minutes_elapsed 以秒計，time_factor=1/session_progress：09:00:05 為 3240、09:00:30 為 540、09:01 為 270、09:05 為 54，都已實算重現。以 2454（官方五日均量 9,332,027 股）為例，09:00:05 只要累計 5,761 股（約 6 張）量比就 ≥2，端對端實跑 6,000 股得 2.08（True）。開盤集合競價量幾乎一定大於這個門檻，所以開盤初期 signal_volume_surge 會大量假觸發。不過本地與線上都沒有逐秒資料，觸發比例無法精確量化。

需要修正或補充的地方：
(1) 報價時間正好 09:00:00 時 progress=0，time_factor=1，完全不折算（100 張得 0.0107）；隔 1 秒就跳到 16200 倍，是雙向的不連續。
(2) 假放量不只在第一分鐘：開盤量為均量 1%～2% 時，量比 ≥2 會持續到約 09:02:40～09:05:24。
(3) 只把分母下限設為 1 分鐘不夠，因為 09:01 時 factor 仍是 270，開盤量 0.5% 仍得 2.35。應該在前 N 分鐘把放量訊號設為 null，或扣掉開盤集合競價量。
(4) MBA 智庫只寫「累计开市时间(分)」和「刚开市时可忽略不计」，沒有寫「最小 1 分鐘」。

監控規則 0 條，不會發出警示，影響限於開盤數分鐘的盤中篩選與策略顯示，維持 P3。
    - 理由：finding 的數值全部重現，逐項一致。session_minutes_elapsed 以秒計（calendar.py:140），compute_enriched_today 用 time_factor=1/session_progress（pipeline.py:1579-1580），門檻是 vol_ratio_5d>=2.0（pipeline.py:1682）。依此重算：09:00:05 的 progress=0.000309、factor=3240；09:00:30 為 540；09:01 為 270；09:05 為 54。2454 前 5 日（9/17～9/23）官方均量 9,332,027 股（本地 enriched 為 9,226,733），09:00:05 的觸發門檻是 5,761 股，約 6 張。以真實函式 compute_enriched_today 端對端實跑，6,000 股時 vol_ratio=2.083、訊號 True；5,000 股時 1.736、False。「≥ 均量 1/1620 即 ≥2」成立。「幾乎必然觸發」無法精確量化：本地分 K 最早只到 09:13，Yahoo 1 分 K 在開盤段又不完整（09:00 bar 多為 0，量只涵蓋官方日量的 62～99%）。但 45 個「標的×交易日」中有 44 個，首根非零分 K 量 ≥ 09:00:05 門檻；以 2454 為例，第一根非零分 K 為 107k～525k 股。所以整體方向成立。

另有四點 finding 沒提到或需要修正：
(1) 報價時間正好 09:00:00 時 progress=0，程式走 else 分支，time_factor=1，完全不折算。100 張只算出 0.0107，隔 1 秒卻跳到 16200 倍，是雙向的不連續。
(2) 假放量不只出現在第一分鐘。以「開盤集合競價量 L + 正常節奏」建模，量比 = 1+L·270/(均量·t)：L=1% 時，量比 ≥2 會持續到約 09:02:42；L=2% 時約到 09:05:24。
(3) 建議的 max(1 分鐘) 修法不夠：09:01 時 factor 仍是 270，L=0.5% 仍得 2.35（True）。有效的是在前 N 分鐘把訊號設為 null。
(4) 線上來源只寫「当日累计开市时间(分)」和「刚开市时可忽略不计」，沒有寫「最小 1 分鐘」。

業務影響方面：輪詢預設 6 秒（preferences.py:78），MIS 時間精度到秒（mis_provider.py:177-184），第一分鐘內會有多輪輪詢落在 09:00:01～09:00:59，所以情境確實會發生。監控規則為 0 條，不會發出警示。影響限於開盤數分鐘的盤中篩選與策略顯示，維持 P3。docs/code-review-2026-09-22.md 的 B2／B12 只談 session_progress_from_ts 回傳 1.0 的 fallback，沒有涵蓋開盤放大，這條不算重複。repo 工作樹與派工前相同，只寫了 scratchpad。
    - 證據：
      - backend/app/taiwan/realtime/calendar.py:140 `return float(_minutes_of_day(t) - _minutes_of_day(REGULAR_OPEN_START)) + now.second / 60.0`；calendar.py:31 SESSION_TOTAL_MINUTES=270
      - backend/app/indicators/pipeline.py:1579-1580 `if session_progress and 0.0 < session_progress < 1.0: time_factor = 1.0 / session_progress`（progress=0 時走 else，factor=1）；pipeline.py:1682 `(pl.col("vol_ratio_5d") >= 2.0).alias("signal_volume_surge")`
      - backend/app/services/quote_service.py:1678 progress = session_progress_from_ts(valid_ts.median())；mis_provider.py:177-184 報價時間取 MIS 的 d/t，精度到秒；preferences.py:78 預設輪詢 6.0 秒
      - 腳本 scratchpad\audit\work\verify-volsurge\s2_avg.py 輸出：09:00:00 progress=0 → factor 1.0；09:00:01 → 16200；09:00:05 progress=0.000309 → 3240（與 finding 一致）；09:00:30 → 540；09:01:00 → 270；09:05:00 → 54
      - s2_avg.py 輸出（官方 TWSE STOCK_DAY，9/17～9/23 五日均量）：2454=9,332,027.2（本地 kline_daily_enriched 為 9,226,733.4）；09:00:05 門檻 2×均量/3240＝5,761 股（finding 寫約 5,760，一致）。其餘自選標的 09:00:05 門檻：3034=2,054、2379=2,130、6196=906、3545=393、0050=49,213、00878=16,811、00885=1,537、00893=674 股
      - s8_e2e.py 以真實 compute_enriched_today 實跑 2454（_vol_ma5_prev_sum=官方均量×5）：09:00:05／6,000 股 → vol_ratio 2.0831、signal True；09:00:05／5,000 股 → 1.7360、False；09:00:05／100,000 股 → 34.72、True；09:00:00／100,000 股 → 0.0107、False；09:00:00／1,000,000 股 → 0.1072、False（新面向：整點不折算）
      - s8_e2e.py 建模（累計量＝開盤量 L%×均量＋正常節奏）：L=0.5%：09:00:05=17.20T、09:01=2.35T、09:05=1.27F；L=1%：09:00:05=33.40T、09:01=3.70T、09:05=1.54F；L=2%：09:05=2.08T、09:15=1.36F。可見假放量延續數分鐘，而且 09:01 時 factor 已是 270，量比仍 ≥2
      - 本地分 K（s1_minute.py）：data\kline_minute 9/17、9/18、9/22、9/23 最早的 bar 為 01:13 UTC（09:13 台北），沒有 09:00 資料，與 finding 所述相符
      - Yahoo 1 分 K（https://query1.finance.yahoo.com/v8/finance/chart/{code}.TW?interval=1m&range=5d，9 次請求、間隔 11 秒；s6_analyze.py）：資料不完整，量只涵蓋官方日量的 62%～99%，09:00 bar 大多為 0。2454 首根非零分 K 為 107,000～525,000 股（門檻的 26～119 倍）；45 個「標的×交易日」中有 44 個，首根非零分 K ≥ 09:00:05 門檻。這是整分鐘量，不是前 5 秒的量，只能當作間接支持
      - https://wiki.mbalib.com/wiki/%E9%87%8F%E6%AF%94 原文：「量比＝现成交总手/〖(过去5个交易日平均每分钟成交量)×当日累计开市时间(分)〗」「（刚开市时可忽略不计）」，沒有寫最小 1 分鐘
      - docs\code-review-2026-09-22.md:270-289、432、601-602 只記錄 session_progress_from_ts 失敗時回 1.0（B2／B12），沒有開盤放大這個面向，因此不算重複

### [P3 → P3] 增量失敗時的全量回退以 kline_daily 為 90 天歷史；台股該表只有 4 個分區，且每個分區都是前一交易日的資料（確認成立）

- **主張**：_flush_live_enriched 在 live_agg 或 prev_enriched 為空時（例如啟動預熱中），改以 kline_daily 最近 90 天加今日重算。台股部署的 kline_daily 只有 4 個分區，33 列全部是前一交易日的收盤與量被標成分區日期。回退時 boll、momentum、high_60d、vol_ratio 會是 null，ATR 只以 4 根錯位 K 棒計算，結果還會寫入當日 enriched。
- **正確的定義或數值**：台股回退路徑應以 TaiwanDailyStore（或 enriched 歷史）為歷史來源，不足時不輸出指標。
- **app 實際行為**：回退時用 4 根錯日期 K 棒計算，產出 null 或錯誤的指標。
- **對使用者的影響**：只在增量狀態不可用的短暫時段（推測為啟動或預熱時）發生，會讓當日盤中 K 線或選股的指標暫時失效或錯誤，收盤後被 enriched_refresh 覆蓋。
- **修法建議**：台股回退改讀 TaiwanDailyStore 或 enriched history cache，或回退時不寫入指標欄；並清理 kline_daily 中錯日期的舊分區。
- **稽核者信心**：plausible
- **程式碼證據**：
  - backend/app/services/quote_service.py:1721-1766 cutoff = today - timedelta(days=90); table = … "kline_daily"; compute_enriched(full_df, …)
  - backend/app/taiwan/enriched_refresh.py:3-4 「The indicator pipeline only sources kline_daily, which the Taiwan deployment never populates」
- **線上證據**：
  - 官方參考資料 STOCK_DAY：2454 在 2026-09-21 收 5010、量 11,237,165；9-22 收 5180
- **本地計算證據**：
  - a04_step8.py：data\kline_daily 只有 2026-09-16、09-17、09-18、09-22 四個分區，共 33 列，每列 close 與 volume 都等於前一官方交易日（例如 2026-09-22 分區的 2454 為 5010.0／11,237,165）
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：`_flush_live_enriched` 在 live_agg 或 prev_enriched 為空時（log 證實 166/166 次都發生在啟動後 120 秒內的預熱期），改以 kline_daily 最近 90 天加今日重算。台股部署的 kline_daily 只有 09-16、09-17、09-18、09-22 四個分區，共 33 列，33/33 列的 OHLCV 都是前一官方交易日的資料，卻被標成分區日期；quote_ts 也指向前一日 13:30。成因推測是收盤後 final sync 抓到仍是前一日資料的 STOCK_DAY_ALL。

因此回退時，boll、ma20、momentum、high/low_60d、vol_ratio_5d、kdj、annual_vol_20d 全部是 null。ATR 只用 3–4 根錯日 K 棒加今日一根計算，比正確值低 3%～42%。change_pct 以錯日前收計算，例如模擬 09-23 時 2454 得到 3.49%，實際是 0.10%。

這些完整指標欄只進記憶體 `_enriched_cache`。磁碟分區只寫存儲欄（OHLCV、raw、quote_ts），而且要當天已有官方資料才會寫。盤中發生時，約 6–10 秒後的下一輪增量輪詢就會覆蓋。但有 111/166 次是收盤後 final sync 的最後一輪，這時錯誤值會留在記憶體，直到 enriched_refresh 呼叫 refresh_cache 或下次重啟。
    - 理由：我用本地 parquet、官方 STOCK_DAY 參考資料和後端 log 重做了一次，finding 的數值主張全部重現。

1) 觸發條件與歷史來源：`_flush_live_enriched` 在 live_agg 或 prev_enriched 為空時，改走全量回退。回退時以 kline_daily 最近 90 天加上今日 K 棒，呼叫 compute_enriched（quote_service.py:1708-1713、1722-1768）。repo 在預熱期間，get_enriched_latest 與 get_live_agg 會回傳空表（repository.py:1131-1133、1248-1251）。

2) kline_daily 內容：只有 09-16、09-17、09-18、09-22 四個分區，共 33 列（6+9+9+9）。與官方 STOCK_DAY 逐列比對 OHLCV：和同日官方資料相符 0/33，和前一官方交易日相符 33/33。quote_ts 也對得上：09-16 分區的 quote_ts 1789450200000000 解碼後是 2026-09-15 13:30（台北時間）。成因從 log 看，是收盤後的 close final sync 抓 openapi STOCK_DAY_ALL，這個端點當時還是前一日資料，被寫成當天分區（backend.log:27348-27370；backend.log.1:44305-44311）。成因這一段屬推測，但與檔案 mtime 相符。

3) 指標重算（today=2026-09-23，今日 K 棒用官方 09-23 資料；factors 與 instruments 為空，與台股部署一致）：
- boll_upper/lower、ma20、vol_ratio_5d、high_60d、low_60d、momentum_5/20/60d、kdj_k、annual_vol_20d 都是 9/9 null。正確值（kline_daily_enriched 全歷史加 compute_all）全部有值。
- ATR 是用 3–4 根錯日 K 棒再加今日一根算出來的，比正確值低 3.3%～41.9%。手算 2454：TR 依序為 190、200、535、255，ewm(α=1/14)=218.1414，與程式輸出一致；正確值是 226.2349。
- ma5、vol_ma5 有 6 檔有值但偏誤，最大 -44.9%。

4) 實際發生頻率：09-17 到 09-23 的 log 中，回退共 166 次、增量 3676 次。166 次全部發生在啟動後 120 秒內，連續回退最多 2 次（10 秒）。「啟動或預熱時的短暫時段」這個推測得到證實。

5) 需要補充修正的敘述：
- 「寫入當日 enriched」要分兩層看。指標欄不在 ENRICHED_STORAGE_COLS 裡（pipeline.py:92-101），磁碟分區只會寫入 OHLCV、raw 與 quote_ts。而且只有在當天已有官方資料時才寫盤（repository.py:1396-1410、2252）。錯誤指標實際進入的是記憶體中的 `_enriched_cache`（repository.py:2302-2304）。
- 盤中發生時，下一輪增量輪詢（約 6–10 秒）就會用 keep=last 蓋掉。但 166 次中有 111 次是收盤後 final sync 的最後一輪，之後進入 closed state 不再輪詢。這種情況下錯誤值會留在記憶體裡，直到 enriched_refresh 呼叫 repo.refresh_cache()（enriched_refresh.py:207-208）或下次重啟。
- 新面向：回退路徑不使用 quote_extra，change_pct 改以錯日的前一根 close 計算。模擬 09-23 時，2454 得到 3.49%，實際是 0.10%。模擬 09-22 時 9 檔全錯，例如 2454 得到 15.1%，實際是 3.39%。

嚴重度維持 P3。使用者有 0 條監控規則，這個錯誤只影響啟動後單輪，或收盤後到 refresh_cache 之間記憶體裡的 9 檔指標。回退時 turnover_rate 為 null 的問題已記錄在 code-review 文件 N 節，不重複計入。
    - 證據：
      - backend/app/services/quote_service.py:1701-1713：只有 stock、live_agg 非空、prev_enriched 非空都成立時才走增量；否則在 1722-1768 走全量回退。回退時 cutoff=today-90 天，table='kline_daily'，然後 compute_enriched(full_df,…)
      - backend/app/repository.py:1131-1133、1248-1251：_enriched_warming 期間 get_enriched_latest 與 get_live_agg 回傳空表
      - backend/app/indicators/pipeline.py:92-101：ENRICHED_STORAGE_COLS 不含 boll、atr 等指標欄；repository.py:2273-2304：磁碟只寫存儲欄，完整欄位合併進記憶體 _enriched_cache；repository.py:1396-1410：_live_write_allowed 只在當天已有官方資料時才允許寫盤
      - 腳本 AUDIT_SCRATCH/work/verify-kdfallback/s2_repro.py，輸出存於同目錄 s2_out.txt。kline_daily 分區為 [2026-09-16, 09-17, 09-18, 09-22]，共 33 列；與同日官方 OHLCV 相符 0/33，與前一官方交易日相符 33/33
      - 官方 STOCK_DAY（reference csv/twse_stock_day_202608_202609.csv）：2454 在 2026-09-21 為 O4800/H5035/L4780/C5010、量 11,237,165；09-22 收 5180；09-23 收 5185。本地 kline_daily 的 date=2026-09-22 分區中，2454 為 4800/5035/4780/5010、量 11237165，與 finding 所列數值一致
      - quote_ts 解碼：09-16 分區 1789450200000000 µs = 2026-09-15 05:30 UTC（台北 13:30）；09-17 分區 = 09-16 13:30；09-18 分區 = 09-17 13:30。確認資料被標到後一天
      - 模擬回退 today=2026-09-23，數值對照（回退值 vs 正確值）：boll_upper、vol_ratio_5d、high_60d、momentum_5d、ma20、kdj_k、annual_vol_20d 回退值 9/9 null，正確值 0/9 null。例如 2454 正確值 boll_upper=5260.40、vol_ratio_5d=0.6969、high_60d=5185、momentum_5d=0.1446
      - 模擬回退 today=2026-09-23 的 ATR（回退 vs 正確）：0050 1.1620 vs 1.7897（-35.1%）、2379 18.7095 vs 28.6729（-34.8%）、2454 218.1414 vs 226.2349（-3.6%）、3034 11.0835 vs 18.2886（-39.4%）、00878 0.2441 vs 0.4201（-41.9%）。手算 2454 的 TR 為 [190,200,535,255]，ewm=218.1414，與程式一致
      - 每檔實際使用的 K 棒數（today=09-23）：0050、00878、00885、00893、3545、6196 為 5 根（4 根錯日加今日）；2379、2454、3034 為 4 根（09-16 分區沒有這三檔）
      - 新面向 change_pct：回退以錯日的前一根收盤計算。today=09-23 時 2454 為 0.0349、正確值 0.0010；00893 為 0.0410、正確值 0.0092。today=09-22 時 9 檔全錯，例如 2454 為 0.1511、正確值 0.0339
      - log 統計腳本 s3_logs.py，輸出 s3_out.txt：backend.log.3、.1 與 backend.log（涵蓋 2026-09-17 到 09-24）中，回退 166 次、增量 3676 次、啟動 294 次。166 次回退全部在啟動後 120 秒內；連續回退最長 2 次（2026-09-18 09:42:12→09:42:22）；52 次落在 09:00-13:30
      - 166 次回退中有 111 次緊接 'close final sync completed, entering closed state'（09-17 79 次、09-18 2 次、09-22 30 次），之後沒有增量輪詢覆蓋；要等 enriched_refresh.py:207-208 的 repo.refresh_cache() 或下次重啟
      - 實際發生例：backend.log.1:44305-44311，2026-09-18 14:59:46 GET openapi STOCK_DAY_ALL → 行情刷新 9 只 → 'enriched 全量计算 (live_agg=空, 上次日期=None)' → 'enriched 全量: 9 只, 2026-09-18' → entering closed state；kline_daily date=2026-09-18 的 mtime 為 14:59:47.27
      - 稽核前後 git status 相同，只有三個既有修改檔案；kline_daily 各分區 mtime 未變，未寫入 repo 或 data

### [P2 → P1] 台股選股器與研究脈絡的 vol_ratio_5d 分母含當日，與管線（前 5 日）同名不同義（部分成立）

- **主張**：app/taiwan/screener.py 與 research_context.py 的 vol_ratio_5d 是 volume / 含當日的 5 日均量，上限為 5。管線、回測與因子說明都是 volume / 前 5 日均量。兩者同名，選股器的自然語言解析也稱為「5日量比」，同一門檻篩出不同結果。（research_context 已知的是法人 5 日口徑，這是另一個欄位。）
- **正確的定義或數值**：全系統的 vol_ratio_5d 都用「當日量 / 前 5 日均量」；若需要含當日版本，應換欄名。
- **app 實際行為**：台股選股器篩選（vol_ratio_5d_min/max）與 AI 研究脈絡的量比，系統性小於管線與回測的值（例如管線 2.0 約對應選股器 1.43）。
- **對使用者的影響**：使用者在台股選股器設「量比 ≥ 2」會漏掉管線定義下已放量的標的；AI 研究引用的量比與 K 線、策略回測數值對不上。
- **修法建議**：screener.py:313 改為 volume.shift(1).rolling_mean(5) 為分母；research_context 用 volumes[-6:-1]（排除 target）；補一支跨路徑同值測試。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/screener.py:313 (pl.col("volume") / pl.col("volume").rolling_mean(5).over("symbol")).alias("vol_ratio_5d")
  - backend/app/taiwan/research_context.py:385（hist_df 含 target 日）、:450-454 avg_vol_5d = sum(volumes[-5:]) / 5.0
  - backend/app/indicators/pipeline.py:434-436、:486-491 前5日均量（不含当天）
  - backend/app/backtest/factor.py:74 「当日成交量 / 前5日平均成交量」；app\taiwan\screener_nl.py:79、:112「5日量比」
- **線上證據**：
  - https://wiki.mbalib.com/wiki/%E9%87%8F%E6%AF%94 量比分母為「過去5日平均每分鐘成交量」（不含當日）
- **本地計算證據**：
  - a04_step7.py（自選 9 檔、2026-08～09，342 列）：以門檻 2.0 判斷有 7 列結果相反，例如 00878 在 2026-09-01 管線 2.505、選股器口徑 1.874；2379 在 08-31 為 2.146 vs 1.655
  - 以門檻 1.5 判斷有 18 列結果相反；差距最大的是 00893 在 2026-09-21：4.742 vs 2.797（-41%）
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：台股選股器（screener.py:313）與研究脈絡（research_context.py:451-454）的 vol_ratio_5d = 當日量 / 含當日的 5 日均量，上限 5。管線（盤後與盤中）、回測矩陣、因子說明、台股 abnormal_diagnostics 以及業界標準（東方財富、通達信 VOL/REF(MA(VOL,5),1)）都是當日量 / 前 5 日均量。

同名欄位、同一個「5日量比」標籤，含當日的算法會把數值往 1 壓縮，並不是一律比較小：
- 放量區會被低估。管線 2.0 對應選股器中位數約 1.65；反過來，選股器 2.0 約等於管線 2.67。
- 縮量區會被高估。管線值 <1 時，約 76% 的列含當日值較大。

影響：
- 選股器的「放量股」preset（量比 ≥2）在 2026-09-22 只篩出 182 檔，管線口徑是 333 檔。
- vol_ratio_5d_max 的縮量篩選也會反向失準。
- 自選頁的量比（enriched，前 5 日口徑）與個股詳細頁或 AI 研究的「5日均量比」（含當日口徑），同一檔同一天數字不同。例如 2379 在 2026-09-23 分別是 1.64 與 1.42。

依 CONTRIBUTING.md 第 11 節，屬於錯誤金融口徑（P1）。修法：選股器改為重用 compute_indicators 的 vol_ratio_5d，研究脈絡改用前 5 日（volumes[-6:-1]），並補跨路徑同值測試。
    - 理由：主張成立。台股選股器與研究脈絡的 vol_ratio_5d 分母是「含當日的 5 日均量」；管線（盤後與盤中增量兩條路徑）、回測矩陣、因子說明，以及同屬台股模組的 abnormal_diagnostics，分母都是「前 5 日均量（不含當日）」。外部權威定義也是不含當日：東方財富百科寫「過去5個交易日」，通達信日量比公式是 VOL/REF(MA(VOL,5),1)，REF(...,1) 取前一根 K 線的 MA5，也就是 t-5..t-1。

含當日版本 r_i = 5v/(v+S4)，上限確實是 5，本地實測最大值正好是 5.0。原 finding 引用的數字我用獨立腳本全部重現：342 列；門檻 2.0 有 7 列結果相反，1.5 有 18 列；00878 在 09-01 是 2.505 vs 1.874，2379 在 08-31 是 2.146 vs 1.655，00893 在 09-21 是 4.742 vs 2.797。

這個問題在使用者情境下確實會發生：
- TaiwanScreener 的「放量股」快速篩選就是設 vol_ratio_5d_min=2。2026-09-22 全市場用選股器口徑有 182 檔 ≥2，用管線口徑有 333 檔，選股器約漏掉 45%。
- 自選頁的「量比」欄讀 enriched，與管線值完全相同（最大差 0.0）。個股詳細頁的「5日均量比」讀研究脈絡。同一檔同一天，兩處數字不同，例如 2379 在 09-23：自選 1.64，詳細頁 1.42；6196 是 1.43 vs 1.24。

原 finding 有兩處敘述需要修正：
1. 「系統性小於」只在放量區成立（管線 ≥2 的 13,006 列中，12,848 列含當日值較小）。含當日的算法其實是把數值往 1 壓縮：管線 <1 的 78,055 列中，有 59,357 列（76%）含當日值反而較大，所以 vol_ratio_5d_max 的縮量篩選也會反向失準。
2. 「管線 2.0 約對應選股器 1.43」有誤。實測管線值落在 [1.95, 2.05] 的列，選股器值中位數 1.65（p10 1.52，p90 1.84）；理論上前 5 日量相同時是 5r/(r+4)=1.667。反過來，選股器 2.0 約等於管線 2.67。

嚴重度：CONTRIBUTING.md 第 11 節表格把「錯誤金融口徑」列為 P1；第 3.2 節規定技術指標必須與現有指標定義一致；第 2 節要求不能各自形成不同口徑。這裡是同名欄位偏離系統自身定義與業界標準，照字面屬於 P1。不到 P0：它不寫入資料，不影響回測與策略；使用者的監控規則是 0 條，監控也不受影響。若改以影響面衡量（只影響篩選與展示），P2 也說得通，但依專案規則應為 P1。

原記錄沒有重複：docs/code-review-2026-09-22.md 裡只提到研究脈絡的法人 5 日口徑，沒有提到量比。

修法方向正確。另建議選股器直接重用 compute_indicators(needed={"vol_ratio_5d"})，不要再手寫一套平行實作（CONTRIBUTING.md 第 16 行要求不平行實現第二套邏輯）。

本次為唯讀稽核：執行腳本前後 git status 相同，data\ 下沒有比基準檔新的檔案。
    - 證據：
      - backend/app/taiwan/screener.py:313 `(pl.col("volume") / pl.col("volume").rolling_mean(5).over("symbol")).alias("vol_ratio_5d")`：分母含當日；:510-513 的 min/max 篩選直接使用這個值
      - backend/app/taiwan/research_context.py:386 read_range(..., target) 含 target；:451-454 `avg_vol_5d = sum(volumes[-5:]) / 5.0`：含 target 當日；:501 餵給 vol_ratio_5d，ai_research.py:201 再交給 AI
      - backend/app/indicators/pipeline.py:434-436 `volume.shift(1).rolling_mean(5)`、:486-491 註解「标准量比(同花顺/东财): 今日成交量 / 前5日均量(不含当天)」；盤中增量路徑 :1578、:1586 同樣用前 5 日
      - backend/app/backtest/matrix.py:3852-3861 用 valid_shift(volume,1) 後取 5 日均值；factor.py:74「当日成交量 / 前5日平均成交量」
      - backend/app/taiwan/abnormal_diagnostics.py:210-214（嚴格 < target 的前 5 日）、:378-380：同屬台股模組也用前 5 日，所以台股模組內部口徑也不一致（新面向）
      - frontend/src/pages/TaiwanScreener.tsx:374-379「放量股」preset 設 volRatioMin='2'；:883 欄名「5D量比」；TaiwanStockDetail.tsx:898-900「5日均量比」顯示研究脈絡的值；screener_nl.py:79、:112 稱「5日量比」
      - 線上：https://baike.eastmoney.com/item/%E9%87%8F%E6%AF%94 「量比＝现成交总手/〖(过去5个交易日平均每分钟成交量)×当日累计开市时间(分)〗」
      - 線上：https://www.gupang.com/201702/021J1S12017.html 通達信「日量比:=VVOL/REF(MA(VOL,5),1);」，REF(X,1) 取前一根 K 線的 5 日均量，即 t-5..t-1，不含當日
      - 本地（uv run python scratchpad\audit\work\v_volratio\check.py）：自選 9 檔 2026-08-01 起共 342 列；門檻 2.0 有 7 列相反、1.5 有 18 列相反；00878 在 09-01 為 2.505/1.874、2379 在 08-31 為 2.146/1.655、00893 在 09-21 為 4.742/2.797（-41%）
      - 本地 check.py：enriched 儲存的 vol_ratio_5d 與 compute_indicators 的前 5 日值最大差 0.0，與含當日值最大差 1.945。09-23 的 2379：自選 1.643 vs 研究脈絡 1.42；6196：1.432 vs 1.24
      - 本地 check.py：全市場『≥2』檔數，選股器 vs 管線：09-22 為 182 vs 333；09-18 為 290 vs 462；09-23 為 54 vs 99（兩者同時成立 52）
      - 本地 check2.py（最近 60 個分區，共 126,036 列）：含當日最大值 5.0；管線 ≥2 的 13,006 列中只有 158 列含當日值 ≥ 管線值；管線 <1 的 78,055 列中有 59,357 列含當日值較大；管線值 ∈[1.95,2.05] 時選股器值中位數 1.6465；選股器值 ∈[1.95,2.05] 時管線值中位數 2.669
      - CONTRIBUTING.md:295 P1 包含「錯誤金融口徑」；:103「技術指標…必須與現有指標定義一致」；:73「不能各自形成不同口徑」；:16「不平行實現第二套邏輯」
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：台股選股器（screener.py:313）與 AI 研究脈絡（research_context.py:386、:452）的 vol_ratio_5d，分母都是含當日在內的最近 5 日均量，算式為 5v/(v+前4日量和)，上限為 5。研究脈絡的值等於選股器的值取到小數第 2 位。管線（pipeline.py:434-436、:486-491）、回測矩陣（matrix.py:3852-3861）與因子說明（factor.py:74）則都用前 5 日均量（不含當日）。兩邊同名，選股器的自然語言解析也稱為「5日量比」。

差距不是單向偏小，而是往 1 壓縮，近似關係為選股器 ≈ 5R/(R+4)：
- 放量時選股器偏低：管線 2.0 約對應 1.6～1.67，不是 1.43。
- 縮量時選股器偏高。

自選 9 檔在 2026-08～09 的 342 列中：
- 下限條件「≥2.0」有 7 列結果相反，「≥1.5」有 18 列（最大差距是 00893 在 09-21：4.742 對 2.797，-41%）。
- 上限條件「≤0.5」有 9 列相反，「≤0.7」有 27 列相反，例如 3545 在 09-09：0.482 對 0.733。

因此，選股器的量比上下限與 AI 研究引用的量比，都和 K 線、策略回測的同名數值不一致。
    - 理由：以數據重現驗證。腳本：AUDIT_SCRATCH/work/verify-volratio/repro.py 與 extra.py；結果檔為同目錄的 compare.csv。

計算方式：
- 用 polars 逐檔讀 data\taiwan\daily 的 parquet，範圍 2026-03-01～09-23，只取自選 9 檔，共 1287 列，(symbol,date) 無重複。
- 管線值：直接呼叫真實的 compute_indicators(needed={"vol_ratio_5d"})。
- 選股器值：假設在每個交易日執行選股器，把 TaiwanScreenerService._compute_batch_indicators 綁到一個唯讀的假 store 上呼叫（只提供 available_dates 與 read_range，資料只到當天）。
- 研究脈絡值：照 research_context.py:385-454 逐行複刻。
- 官方交叉核對：用參考資料 twse_stock_day_202608_202609.csv 的 volume_shares，重算兩種口徑。

finding 的數值全部精確重現：
- 以門檻 2.0 判斷，結果相反 7 列；門檻 1.5 為 18 列；總列數 342。
- 三個例子逐位吻合，而且和用官方成交量重算的值完全相同。
- 研究脈絡的值等於選股器的值取到小數第 2 位（342 列的最大差值為 0.0），證實兩條路徑同一口徑。

程式碼面也成立：
- screener.py:313 的分母是含當日的 rolling_mean(5)。
- research_context.py:386 的 hist_df 含 target 日；:420 取 volumes，:452 取 volumes[-5:]。
- pipeline.py:434-436、:486-491，以及回測 matrix.py:3852-3861（先 valid_shift 1 期再取均值），都是前 5 日均量。
- factor.py:74 的說明文字也是前 5 日。
- 選股器的值等於 5v/(v+前4日量和)，因此上限為 5（除非前 4 日量全為 0）。這一點正確。

finding 的「actual」欄位有兩處不成立：
1. 「系統性小於管線」只在放量時成立。
   - 管線 ≥1 的 129 列中，選股器較小的有 107 列；管線 ≥2 的 16 列中有 15 列。
   - 管線 <1 的 213 列中，選股器反而較大的有 165 列。
   - 全部 342 列合計：選股器較大 187 列、較小 155 列。
2. 「管線 2.0 約對應選股器 1.43」有誤。
   - 理論近似為 5R/(R+4)，R=2 時是 1.667；此近似與實際值的平均絕對誤差為 0.058。
   - 實測管線落在 [1.8, 2.2] 的 10 列，選股器中位數為 1.611。

finding 漏掉的面向：縮量上限條件 vol_ratio_5d_max 也會被反向影響。以 ≤0.5 判斷有 9 列結果相反，以 ≤0.7 判斷有 27 列；例如 3545 在 2026-09-09，管線 0.482、選股器 0.733。

線上來源：mbalib 原文是「量比＝現成交總手/〖(過去5個交易日平均每分鐘成交量)×當日累計開市時間(分)〗」。頁面沒有逐字寫「不含當日」。finding 括號裡的「不含當日」是它自己的解讀；不過「當日 vs 過去 5 日」的比較本意支持管線口徑。

額外觀察（不在本 finding 範圍，推測另屬資料完整性問題）：本地 parquet 有 11 列 volume 與官方 STOCK_DAY 不符，都落在 2026-09-16～09-22。例如 6196 在 09-17，本地 808428、官方 973428。只用官方量重算時（297 列），門檻 2.0 仍有 7 列相反、門檻 1.5 為 16 列，所以結論不受影響。

嚴重度維持 P2。這是同名欄位口徑不一致：影響台股選股器的量比篩選（只作用在最新一日）與 AI 研究文字，不涉及交易執行或資料破壞；使用者監控規則為 0 條，監控走的是管線口徑。若嚴格照 CONTRIBUTING.md §11 把「錯誤金融口徑」列為 P1，也有升級的論據，屬邊界判斷。

repo 工作樹在執行前後的 git status --porcelain 完全相同（diff 無輸出）。沒有寫入 repo 或 data\。
    - 證據：
      - backend/app/taiwan/screener.py:313 (pl.col("volume") / pl.col("volume").rolling_mean(5).over("symbol")).alias("vol_ratio_5d")；篩選在 :510-513
      - backend/app/taiwan/research_context.py:385-386 window_dates／hist_df 讀到 target（含當日）；:397 vol 取 target 列；:420 volumes = hist_df["volume"].drop_nulls()；:452 avg_vol_5d = sum(volumes[-5:]) / 5.0
      - backend/app/indicators/pipeline.py:434-436 volume.shift(1).rolling_mean(5)；:486-491 volume / _vol_ma5_prev
      - backend/app/backtest/matrix.py:3852-3861 valid_shift(volume,1) 後 rolling mean（前 5 日）；factor.py:74 說明「当日成交量 / 前5日平均成交量」；screener_nl.py:79、:112「5日量比」
      - 重現指令：cd backend && uv run python ...\verify-volratio\repro.py。輸出：compare rows 342, win dates 38；threshold 2.0: flips=7；threshold 1.5: flips=18；max|round(screener,2)-research_ctx| 0.0
      - 數值並列（finding → 本次重算）：00878 在 2026-09-01，管線 2.505／選股器 1.874 → 2.50466／1.87409（研究脈絡 1.87；官方量重算相同）
      - 數值並列：2379 在 2026-08-31，2.146／1.655 → 2.14556／1.65481；00893 在 2026-09-21，4.742／2.797（-41%）→ 4.74204／2.79679（rel -0.4102）
      - 門檻 2.0 結果相反的 7 列：00878 09-01、00885 08-14/08-17/08-25、00893 08-24、2379 08-31、6196 09-09。管線 ≥2 有 16 列，選股器 ≥2 只有 9 列
      - 反證「系統性小於」：extra.py 輸出 pipeline>=1: n 129, screener<pipeline 107；pipeline<1: n 213, screener>pipeline 165；全部 342 列中 screener>pipeline 187 列、< 155 列
      - 反證「2.0≈1.43」：管線在 [1.8,2.2] 的 n=10，選股器中位數 1.611；理論 5R/(R+4)=1.667；mean|screener-5R/(R+4)|=0.0575
      - 新面向：以上限門檻判斷時結果也會相反。≤0.5 有 9 列、≤0.7 有 27 列，例如 3545 在 2026-09-09，管線 0.482／選股器 0.733；0050 在 2026-08-07，0.392／0.570
      - 官方交叉核對：用 twse_stock_day_202608_202609.csv 重算，官方量可用的 297 列中，門檻 2.0 結果相反 7 列、1.5 為 16 列；本地與官方 volume 不符 11 列（2026-09-16～22，例如 6196 在 09-17，本地 808428、官方 973428）
      - https://wiki.mbalib.com/wiki/%E9%87%8F%E6%AF%94 原文：「量比＝現成交總手/〖(過去5個交易日平均每分鐘成交量)×當日累計開市時間(分)〗」（頁面沒有逐字寫「不含當日」）

### [P2 → P2] 盤中遞推狀態取「日曆 90 天」，春節後常不足 60 個交易日，momentum_60d、ma60、60 日高低全錯（部分成立）

- **主張**：_build_live_agg 以 latest−90 個日曆天切歷史，註解假設「≈60 個交易日」。台股在春節、228、清明等假期後，這個窗口只有 53～54 個交易日。tail(59)/tail(60) 取不到足夠列數時不會報錯，而是取最早一列、除以固定 60；_window_len 有計算卻沒有使用。
- **正確的定義或數值**：按交易日數取足 60 列（例如取最後 N 個交易日分區或加大窗口），或列數不足時輸出 null。
- **app 實際行為**：用較短窗口計算並除以固定分母，得出錯誤的數值。
- **對使用者的影響**：每年約 3～5 月（25% 的交易日），即時行情下的盤中 momentum_60d、ma60（連帶 MA20/MA60 金叉）、60 日高低出錯；盤中策略評分（momentum_60d 權重 0.4 的多個內建策略）與 K 線當日值錯。收盤後由全量覆蓋。
- **修法建議**：df_hist 改為依交易日取最後 60（至少 61）列，例如在 history cache 以 rank 或 tail(60) 過濾；或把窗口放寬到 120 日曆天，並以 _window_len 守衛不足時回傳 null。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/repository.py:746 start_60d = latest - timedelta(days=90)  # 日曆90天 ≈ 60个交易日
  - backend/app/repository.py:908、:913-914、:920、:930 tail(59)/tail(60).first()/_window_len
  - backend/app/indicators/pipeline.py:1520 ma60 = (_ma60_partial_sum + close)/60；:1607 momentum_60d；:1703 _window_len 被直接 drop
- **線上證據**：
  - （交易日曆以 app 的 daily store 分區為準，不需外部來源。）
- **本地計算證據**：
  - a04_step4.py（b）：605 個可評估的基準日中，154 個的 90 日曆天窗口不足 60 個交易日，最少 54（2024-05-02、2026-05-04～05-08）
  - a04_step5.py 增量與全量比對：2026-03-02（窗口 56 天）2454 momentum_60d 0.362 vs 0.652（差 29 個百分點）；2026-05-06（54 天）2379 為 0.143 vs 0.026，6196 為 0.430 vs 0.350；ma60 各檔偏低 7.5%～11.2%（3545 在 05-05：43.84 vs 49.36）。對照組 2025-05-06（窗口 59 以上）差異為 0
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：_build_live_agg（repository.py:746）以「基準日 − 90 個日曆天」切歷史。台股在春節、228、清明、勞動節前後，這個窗口只剩 53～56 列（全市場最多 54～56 列），而下游不檢查列數：
- tail(59)/tail(60).first() 在列數不足時仍會回傳結果；
- pipeline.py:1520 固定除以 60；
- _window_len 算了卻只被 drop。

影響如下：
- 盤中增量的 ma60 系統性偏低 4%～11%；
- momentum_60d 實際變成約 54～56 日報酬（2454 在 2026-03-02 差 29 個百分點）；
- high_60d/low_60d 只有在 60 日極值落在被截掉的列時才會錯，不是全部錯。

發生期間約為每年 2 月初～5 月下旬，約占 24% 的交易日；目前（2026-09-24 窗口 64 列）不會發生。

受影響的畫面與功能：
- 即時行情輪詢池中的自選股與點開個股的活蠟燭 MA60；
- 盤中快照的選股與策略評分（momentum_60d 權重 0.4）；
- MA20/MA60 金叉與 N 日新高低訊號。

自選頁台股列不受影響，因為它走 watchlist_enrichment.py 的另一條路徑。收盤後全量重算會覆蓋，已存資料正確。
    - 理由：主張成立。我直接呼叫專案裡真正的 KlineRepository._build_live_agg（用 __new__ 建實例，只放記憶體內的歷史，不寫檔）和 compute_enriched_today，結果與 finding 的數值逐位相符。

一、程式碼（現況行號與 finding 一致）
- repository.py:746 用 baseline−90 個日曆天切歷史。
- :908、:913-914 用 tail(59) 取部分和與極值；:920 用 tail(60).first() 取 60 列前的收盤價。
- pipeline.py:1520 固定除以 60；:1607 用 _close_60d_ago 算動量。
- _window_len 只在 repository.py:930 算出、在 pipeline.py:1703 被 drop，沒有任何檢查。
- 列數不足時 polars 不會報錯：tail 取到多少算多少，first() 取的是窗口內最早一列。

二、正確定義
- MA60 與 60 日動量都以「60 個交易日（列）」為單位，窗口滾動時最舊一天掉出。
- 專案自己的定義也一樣：backtest/factor.py:41 寫「60个交易日累计收益率」；全量路徑 pipeline.py:502 用 shift(60)。
- 盤中路徑窗口不足時，ma60 變成「53～55 天收盤價總和 ÷ 60」，momentum_60d 變成約 54 日報酬，是錯誤數值，不是另一種口徑。

三、發生頻率
- 依 kline_daily_enriched 的 661 個分區，缺的平日與台股假日（春節、228、清明、勞動節、颱風假）吻合。
- 604 個可評估基準日中，154 個窗口不足 60 列；最少 54 列。
- 2025 年受影響的是 02-04～04-30，共 59/243 天（24%）；2026 年是 02-24～05-21。
- 今天（2026-09-24）的窗口有 64 列，目前不會發生，但每年春節後會重複出現。

四、使用者情境
- 即時行情開啟時，自選股會走 compute_enriched_today（quote_service.py:1715）。從清單點開的個股走 fetch_live_bar（quote_service.py:808），也用同一段遞推計算。
- K 線活蠟燭的 ma60 取自增量結果（kline.py:577）。
- 盤中快照被 get_daily／選股使用；六個內建策略的評分中 momentum_60d 權重是 0.4；MA20/MA60 金叉與 N 日新高低訊號也用到這些值。
- 自選頁的台股列不受影響：它走 watchlist_enrichment.py 的另一條計算路徑。監控規則 0 條，告警也不受影響。
- 收盤後全量重算會修正：已存的 2026-05-06 分區中，2379 的 momentum_60d 0.0264、ma60 497.74，與我的全量參考值一致。因此只是盤中暫時錯誤，不會破壞資料。

五、嚴重度
P2 恰當：這是明確的功能錯誤，會週期性出現且可見；但定義意圖正確、寫入的資料正確、只在盤中發生，不到 P1 的「錯誤金融口徑」或核心流程不可用。

六、需修正的細節
1. 「60 日高低全錯」說過頭。極值落在被截掉的列時才會錯：
   - 03-02：9 檔的 high 全部正確，low 錯 5 檔。
   - 05-05／05-06：只有 3545 的 high 錯（57.1 對 57.3）。
2. 「3～5 月」應為 2～5 月。

另有一個觀察，與本 finding 無關，稽核紀錄中未見：全量路徑的 high_60d/low_60d 用 close 的 rolling_max/min（pipeline.py:438-440），盤中路徑用 high/low（repository.py:913-914）。即使窗口足夠，兩者也不一致，例如 2026-09-23 的 2454 是 5480 對 5185。
    - 證據：
      - backend/app/repository.py:746 `start_60d = latest - timedelta(days=90)  # 日历90天 ≈ 60个交易日`；:761-763 以此日期過濾歷史快取
      - backend/app/repository.py:908 `tail(59).sum()`（_ma60_partial_sum），:913-914 `high.tail(59).max()`／`low.tail(59).min()`，:920 `close.tail(60).first()`（_close_60d_ago），:930 `_window_len`
      - backend/app/indicators/pipeline.py:1520 `(_ma60_partial_sum + close) / 60`；:1607 `close / _close_60d_ago - 1`；:1703 _window_len 只被 drop，app 內沒有其他使用點（Grep `_window_len` 只命中 repository.py:930 與 pipeline.py:1703）
      - backend/app/indicators/pipeline.py:502 全量定義為 `close / close.shift(60) - 1`；backend/app/backtest/factor.py:41 描述為「60个交易日累计收益率」
      - 交易日曆腳本 AUDIT_SCRATCH/work/verify_live60/cal.py：partitions 661（2024-01-02～2026-09-23）；evaluable baselines 604；窗口 <60 列 154 個，<59 列 143 個，最少 54 列；2025 年 short 59/243（02-04～04-30），2026 年 short 59（02-24～05-21）；今天的基準日 2026-09-23 窗口有 64 列；缺的平日與台股假日吻合（例：2026-02-12～02-20、02-27、04-03、04-06、05-01）
      - 重現腳本 AUDIT_SCRATCH/work/verify_live60/repro.py（實際呼叫 KlineRepository._build_live_agg 與 compute_enriched_today，並以 compute_indicators 在相同輸入上算全量參考值）：2026-03-02（_window_len 56）2454 momentum_60d 0.3620 vs 0.6522；ma60 1480.75 vs 1544.50；全市場 2215 檔 ma60 全部不符
      - 同一腳本：2026-05-06（54 列）2379 momentum_60d 0.1429 vs 0.0264，6196 0.4304 vs 0.3498；自選 9 檔 ma60 偏低 7.7%～11.1%；2026-05-05 3545 ma60 43.8433 vs 49.3633（-11.18%）
      - 對照組：2025-05-06（59 列）與 2026-09-23（59 列），自選 9 檔的 ma60 與 momentum_60d 誤差皆為 0.0000；ma20 在所有日期幾乎沒有不符（0～2 檔），證明誤差只來自 60 列窗口不足
      - high_60d/low_60d 只有極值落在被截掉的列時才錯：03-02 高點 9/9 正確、低點錯 5/9（例：2454 為 1365 vs 1175）；05-05／05-06 只有 3545 的高點錯（57.1 vs 57.3）。因此「全錯」說過頭
      - 影響面：backend/app/services/quote_service.py:1708-1716（股票走增量路徑）、:808（fetch_live_bar 共用同一段計算）；backend/app/api/kline.py:577 活蠟燭帶入 ma60；六個內建策略的評分中 momentum_60d 權重為 0.4（例：backend/app/strategy/builtin/trend_breakout.py:45）；自選頁台股列走 backend/app/taiwan/watchlist_enrichment.py，不受影響
      - 收盤後會被全量值覆蓋：已存分區 kline_daily_enriched/date=2026-05-06 的 2379 momentum_60d 0.02642、ma60 497.7417，與全量參考值一致
      - 線上定義 1：https://en.wikipedia.org/wiki/Moving_average — SMA 為「the unweighted mean of the previous k data-points」，除數為 k
      - 線上定義 2：https://www.tradingview.com/support/solutions/43000502343-rate-of-change-roc/ — 「ROC = [(CurrentClose - Close n periods ago) / (Close n periods ago)] X 100」
      - 線上定義 3（台股）：https://efrontrade.com/2026/05/stock-life-line-60ma-trading-strategy.html — 「60 日移動平均線（60MA），代表過去 60 個交易日…」「每天收盤後，最舊的那一天會『掉出』這個 60 天的窗口」
      - 唯讀確認：執行前後 git status 與 data 目錄列表都沒有變化（DATA_UNCHANGED）
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：_build_live_agg（repository.py:746）以基準日往前 90 個日曆天切歷史，再用 tail(59)／tail(60).first() 取窗口。列數不足時不會報錯，ma60 仍固定除以 60，momentum_60d 則以窗口最早的一列當作 60 日前的收盤價；_window_len 有計算，但從未用來守衛。

以本地分區為交易日曆，605 個可評估的基準日中有 154 個（25.5%）窗口不足 60 列，最少 54 列，集中在每年約 2～5 月（CNY 前後至 5 月中），2024 年 10 月另有一小段 59 列（只影響 momentum_60d）。此外，個股缺列時全年都會出錯，約占全市場 5～6% 的標的。

重現結果：
- 2026-03-02：2454 的 momentum_60d 增量 0.362，全量 0.652。
- 2026-05-06：2379 為 0.143 vs 0.026，6196 為 0.430 vs 0.350。
- 自選股的盤中 ma60 在 5 月初偏低 7.7%～11.2%，3 月初偏低 4.1%～6.5%。
- 全市場重現時，因 ma60 偏低而誤觸的 MA20/60 金叉約 750～1000 檔。

60 日高低只有部分標的受窗口影響，沒有「全錯」。增量與全量之間 high/low 的主要差異，來自另一個全年都存在的定義不一致：增量用最高價與最低價，全量用收盤價。

影響面：使用者的自選頁走 _taiwan_indicator_map，不受影響。受影響的是 K 線活蠟燭的 ma60、盤中 live enriched 快取（輪詢池股票與 fetch_live_bar），以及依賴這些資料的訊號與評分。收盤後全量計算會覆蓋。
    - 理由：核心缺陷已完整重現。我建了一個只設屬性的 KlineRepository 實例，呼叫真正的 _build_live_agg 與 compute_enriched_today，再和 compute_indicators 的全量結果比對。finding 列出的交易日曆統計，以及 momentum_60d、ma60 的數值都逐一吻合，對照組 ma60 與 momentum_60d 的差異也是 0。

finding 的「60 日高低全錯」說得過頭：
- 窗口不足只會讓部分標的的 _high_59d 或 _low_59d 改變。例如 2026-03-02 高點 157/2228、低點 644/2228；自選股只有少數幾檔受影響。
- 增量結果與全量結果在 high/low 上的大量差異，另有一個不受季節影響的根因：增量用 max(high)/min(low)，全量用 rolling_max(close)/rolling_min(close)。所以對照組的 high/low 其實也不是 0 差異。

補充與更正：
- 受影響的期間是約 2～5 月（CNY 前後至 5 月中），10 月國慶期間另有一小段只少到 59 列，這時只有 momentum_60d 出錯。
- 最少 54 列的基準日共 11 個，不只 finding 列的那幾天。
- 即使不在春節季，也約有 6% 標的因個股缺列而同樣出錯。
- 影響面方面：使用者的自選頁走另一條指標計算路徑，不受影響；受影響的是 K 線活蠟燭的 ma60 與 live enriched 快取。MA20/60 金叉的誤觸在全市場重現時非常大量，但在實際部署中可能只出現在當天第一次輪詢（推測）。

嚴重度維持 P2：這是明確的盤中功能錯誤，收盤全量計算後會自動修正。
    - 證據：
      - 程式碼：backend/app/repository.py:746 `start_60d = latest - timedelta(days=90)`；:762 以此過濾 df_hist；:913 `close.tail(59).sum()` → _ma60_partial_sum；:918-919 high/low tail(59)；:925 `close.tail(60).first()` → _close_60d_ago；:930 `_window_len` 只計算不使用（全 app 只在 backend/app/indicators/pipeline.py:1703 被 drop）。pipeline.py:1520 ma60 固定除以 60；:1607 momentum_60d；全量版 pipeline.py:401 rolling_mean(60)、:502 close.shift(60)。
      - 交易日曆（指令：uv run python verify-w90\v1_calendar.py，交易日以 kline_daily_enriched 分區為準，共 661 個，2024-01-02～2026-09-23）：可評估的基準日 605 個；窗口不足 60 列的有 154 個（25.5%），不足 59 列的有 143 個；最少 54 列，共 11 個基準日（2024-05-02、05-03、05-06、2026-05-04～05-08、05-11～05-13）。不足的連續區段：2024-04-01～05-13（54～59）、2024-10-11～10-22（全為 59，只影響 momentum_60d）、2025-02-03～04-29（55～59）、2026-02-23～05-20（54～59）。finding 的 605／154／最少 54 全部吻合。
      - 數值重現（verify-w90\v2_repro.py，輸出存在 v2_out.txt），格式為 finding 值 vs 本次重算（增量 inc／全量 full）：2026-03-02（基準日 02-26，窗口 56 列）2454 momentum_60d，finding 0.362 vs 0.652，重算 0.3620 vs 0.6522 ✓；2026-05-06（基準日 05-05，窗口 54 列）2379，finding 0.143 vs 0.026，重算 0.1429 vs 0.0264 ✓；6196，finding 0.430 vs 0.350，重算 0.4304 vs 0.3498 ✓；3545 ma60 在 05-05，finding 43.84 vs 49.36，重算 43.8433 vs 49.3633 ✓。
      - ma60 偏低幅度（重算）：05-05／05-06 自選 5 檔股票為 -7.72%～-11.18%（finding 寫 7.5%～11.2%，大致吻合）；03-02 為 -4.13%～-6.52%。全市場 ma60 相對誤差中位數：03-02 為 -4.94%，05-05 與 05-06 為 -8.45%／-8.42%。對照組 2025-05-06（窗口 61 列）與 2026-09-23（64 列）的自選股 ma60 與 momentum_60d 差異都是 0.0000 ✓。
      - 60 日高低的窗口效應單獨量測（verify-w90\v3_isolate.py，比對 90 日曆天窗口與真正最後 59 列）：2026-03-02 的 hi59 改變 157/2228、lo59 改變 644/2228；05-05 分別為 579/2260、228/2260；05-06 為 531/2260、239/2260。自選股方面，03-02 有 2454 lo 1365 vs 1175、3545 lo 49.55 vs 48.7、6196 lo 225 vs 219.5，05-05／05-06 有 3545 hi 57.1 vs 57.3，其餘不變。所以「全錯」不成立。
      - high/low 另有一個不受季節影響的定義差異（finding 未提）：全量版 pipeline.py:438 與 :440 用 `close.rolling_max(60)` 與 `rolling_min(60)`，backend/app/backtest/factor.py:85 的說明也寫「60日最高收盘价」；增量版 pipeline.py:1592-1596 卻用 max(_high_59d, high) 與 min(_low_59d, low)。窗口足夠的對照日照樣大量不一致：2025-05-06 的 hi60 有 2004/2114 檔不同，2026-09-23 有 1222/1309 檔不同；signal_n_day_high 不一致 25 檔（2025-05-06）與 58 檔（2026-09-23）。
      - 連帶訊號（全市場重現）：signal_ma_golden_20_60 在 03-02 全量 27 檔、增量 782 檔；05-05 全量 44、增量 1070；05-06 全量 30、增量 1043；對照組 2025-05-06 為 5 vs 39，2026-09-23 為 13 vs 18。signal_ma_golden_5_20 幾乎一致（相差 0～2 檔），證明誤差來自 ma60。
      - 新面向：即使日曆窗口足夠，個股若在窗口內缺列，同樣會出錯。2025-05-06 有 134/2141 檔窗口不足 60 列，其中 123 檔的 close60ago、101 檔的 ma60 部分和受影響；2026-09-23 有 126/2319 檔不足 60 列，其中 123 檔的 close60ago、111 檔的 ma60 部分和受影響。
      - 影響面更正（程式碼）：自選頁的台股走 backend/app/taiwan/watchlist_enrichment.py:138-159 的 `_taiwan_indicator_map` → compute_taiwan_daily_indicators，不經過 live_agg，因此不受影響。K 線活蠟燭會注入 ma60（backend/app/api/kline.py:577），這部分受影響。momentum_60d 權重 0.4 的內建策略有 6 個（backend\app\strategy\builtin 下的 bullish_alignment、low_volatility_leader、macd_golden、pullback_ma20_bounce、pullback_to_support、trend_breakout）；盤中策略評分是否讀取 live enriched 未查證。
      - 推測（未實跑）：第一次輪詢後 merge_live_enriched_asset 會把 _enriched_cache 換成當日列（repository.py:2255、:2303），而 quote_service.py:1702 以 get_enriched_latest() 作為 prev_enriched。所以在實際部署中，MA20/60 金叉誤觸可能只出現在當天第一次輪詢；全市場數字只適用於輪詢池（使用者的池子是 5 檔股票）。
      - 唯讀確認：執行前後 `git status --short` 不變；以 find -newer 檢查，data\ 與 backend\ 底下都沒有新寫入的檔案。腳本與輸出位於 AUDIT_SCRATCH/work/verify-w90/。

### [P3 → P2] app daily store 近期成交量少於官方，影響 vol_ratio_5d 與 vol_ma5（部分成立）

- **主張**：2026-09-16～09-23 有 11 列成交量低於 STOCK_DAY 官方值，價格全部一致。量比與均量隨之偏差。根因在資料擷取，可能與股價／成交量領域重複；本項只記錄對 A04 指標的影響。
- **正確的定義或數值**：daily store 的成交量等於官方 STOCK_DAY 成交股數。
- **app 實際行為**：近期多檔少計數千到數十萬股。
- **對使用者的影響**：這幾天的量比與均量略低，沒有造成放量訊號翻轉。
- **修法建議**：由資料擷取領域處理：盤後以 STOCK_DAY 或 MI_INDEX 最終值覆寫當日量，並補官方比對測試。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:428-436、:486-491（直接使用 daily store 的 volume）
- **線上證據**：
  - 官方參考資料 csv\twse_stock_day_202608_202609.csv（STOCK_DAY）
  - https://query1.finance.yahoo.com/v8/finance/chart/2454.TW?range=1mo&interval=1d ：Yahoo 9/23 量 6,425,491，恰等於 app 值（官方 6,430,491）
- **本地計算證據**：
  - a04_step1.py 不一致 11 列：6196 在 09-17 app 808,428 vs 官方 973,428（-17.0%）；2454 在 09-18 為 9,930,705 vs 10,365,174（-434,469）；2454 在 09-16 為 -249,000
  - a04_step2.py：vol_ratio_5d 相對誤差最大為 6196 在 09-17 的 -16.95%（0.572 vs 0.689），2454 在 09-16 為 -3.7%；vol_ma5 最大 -2.7%；signal_volume_surge 沒有翻轉
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：app 的 daily store（taiwan/daily）在 2026-09-16～09-23 有多筆成交量低於官方 STOCK_DAY／MI_INDEX 值，成交金額也同步偏低，OHLC 則完全一致。在參考資料的 10 檔中有 16 列少計：自選 9 檔佔 11 列，另 5 列是 2330。以 09-23 MI_INDEX 比對全市場，有 23/1330 檔少計，最嚴重的是 6464，2,300 vs 302,300（-99.2%）。kline_daily_enriched 直接沿用這些量，所以 vol_ma5、vol_ma10、vol_ratio_5d 都跟著偏差。少計當天量比偏低，例如 6196 在 09-17 為 0.572 vs 0.689（-16.95%）；之後 5 天分母變小，量比反而偏高 2～4%。自選 9 檔沒有放量訊號翻轉，但全市場確有錯誤：2330 在 09-18 經完整重算為 1.93 vs 2.11，signal_volume_surge 應為 true 卻是 false；1109 與 6464 在 09-23 以近似重算官方量比達 5.7 與 22.7，app 只有 0.62 與 0.17。app 值等於 Yahoo 的 meta.regularMarketVolume（13:30 快照），不是 Yahoo 的日 K bar 量。推測 store 保存的是收盤快照，沒有納入盤後的整張交易。
    - 理由：核心數值完全重現。在 finding 列出的 9 檔自選中，taiwan/daily 與 kline_daily_enriched 的成交量都有 11 列低於官方 STOCK_DAY，全部落在 09-16～09-23；OHLC 與官方一致，差 0 列；08-03～09-15 沒有任何差異。逐項對照：6196 在 09-17 為 808,428 vs 973,428（-16.95%）；2454 在 09-18 為 9,930,705 vs 10,365,174（-434,469）；2454 在 09-16 為 -249,000（-3.75%）。以 app 的純函式 compute_indicators 重算：6196 在 09-17 的 vol_ratio_5d 為 0.5723 vs 0.6891（-16.95%）；2454 在 09-16 為 -3.75%；自選檔 vol_ma5 最大誤差 -2.689%（6196，09-21）。自選 9 檔沒有任何 signal_volume_surge 翻轉。重算值與 enriched 已存值的差為 0.0（比對 170 列），可見 enriched 直接沿用 daily store 的量。以下幾點需要修正：(1) 「11 列」只算自選 9 檔。參考資料另含 2330，合計 16 列；2330 有 5 列，09-18 少 3,702,177 股（-9.05%）。(2) 「沒有造成放量訊號翻轉」只對自選檔成立。2330 在 09-18 的 app 量比 1.9317，官方為 2.1100，放量訊號由應為 true 變成 false。全市場比對 09-23 的 MI_INDEX（1330 檔可配對）有 23 檔少計，其中 1109 與 6464 以官方當日量換算的量比分別為 5.72 與 22.75，app 卻是 0.62 與 0.17（6464 為 2,300 vs 302,300，-99.2%）。這兩檔屬近似翻轉，分母沿用 app 值。(3) 「量比略低」只對少計的當天成立；之後幾天分母被拉低，量比反而偏高，6196 最多 +2.76%，2330 最多 +3.87%。(4) 成交金額也同步少計，16 列都有，finding 沒提；finding 說的「價格全部一致」只適用 OHLC。(5) Yahoo 證據描述不精確。9/23 的日 K bar volume 是 5,917,267；等於 app 值 6,425,491 的是 meta.regularMarketVolume，regularMarketTime 為 13:30:04。app 的即時 provider 讀的正是這個欄位（推測：store 存的是 13:30 收盤快照，漏了盤後的整張交易；23 檔差異中有 22 檔是 1000 股的整數倍）。嚴重度：自選檔的影響很輕，但全市場確實有放量訊號出錯（2330 經完整重算，1109 與 6464 為近似），足以構成 P2「明確的功能錯誤」。已查過 docs/code-review-2026-09-22.md，不是已記錄的問題。
    - 證據：
      - 腳本 AUDIT_SCRATCH/work/verify-a04vol/cmp.py 輸出：taiwan/daily 對照 STOCK_DAY 共 380 列（10 檔 × 38 日），成交量不一致 16 列，全部是 store 少於官方、日期 09-16～09-23；價格不一致 0 列、金額不一致 16 列；kline_daily_enriched 也是同樣 16 列。明細存於同目錄 vol_mismatch_store.csv
      - 與 finding 並列（finding → 本次重算）：6196 在 09-17 為 808,428 vs 973,428 (-17.0%) → 808,428 vs 973,428 (-16.95%)；2454 在 09-18 為 -434,469 → -434,469；2454 在 09-16 為 -249,000 → -249,000；自選 9 檔共 11 列 → 11 列（2454×5、3034×2、6196×2、2379×1、3545×1），加上 2330 的 5 列共 16 列
      - 腳本 verify-a04vol\ind.py（呼叫 app.indicators.pipeline.compute_indicators，needed={vol_ma5,vol_ma10,vol_ratio_5d}）：vol_ratio_5d 6196 在 09-17 為 0.5723 vs 0.6891 (-16.95%)；2454 在 09-16 為 0.9991 vs 1.0381 (-3.75%)；自選檔 vol_ma5 最大 -2.689%（6196，09-21）；vol_ma10 最大 -1.167%；自選檔 vr 誤差範圍 -16.95% 至 +2.76%；自選檔翻轉 []
      - ind.py：2330 在 09-18 的 vol_ratio_5d 為 app 1.9317 vs 官方 2.1100 → signal_volume_surge app=false、官方=true（翻轉）；2330 的 vol_ma5 最大 -5.22%（09-23）；enriched 已存值與從 store 重算的 vr、ma5、ma10 最大絕對差都是 0.0（170 列）
      - 腳本 verify-a04vol\mkt0923.py：對照 csv\twse_mi_index_20260923_quotes.csv 與 taiwan/daily date=2026-09-23，1330 檔可配對，成交量不一致 23 檔，全部是 store < 官方；其中 22 檔差額為 1000 的整數倍；收盤價不一致 0 檔。以官方當日量搭配 app 分母近似重算，翻轉 2 檔：1109.TWSE（61,788 vs 572,788；app 0.62 vs 官方 5.72）與 6464.TWSE（2,300 vs 302,300；app 0.17 vs 官方 22.75）
      - MI_INDEX 09-23：2454 成交股數 6,430,491，與 STOCK_DAY 一致；app 為 6,425,491
      - Yahoo（一次請求）https://query1.finance.yahoo.com/v8/finance/chart/2454.TW?range=1mo&interval=1d ：meta.regularMarketVolume=6425491，regularMarketTime=1790141404（2026-09-23 13:30:04 +08:00），等於 app 值；但日 K bar 9/23 的 volume=5917267，不是 6,425,491
      - backend/app/taiwan/realtime/yahoo_provider.py:73 volume = meta.get("regularMarketVolume")（推測：store 的量來自 13:30 快照）
      - backend/app/indicators/pipeline.py:428-436（vol_ma5/vol_ma10/_vol_ma5_prev 直接對 volume 做 rolling）、:486-491（vol_ratio_5d = volume / _vol_ma5_prev），行號與 finding 一致
      - grep docs/code-review-2026-09-22.md 找「成交量/volume/STOCK_DAY/盤後定價」：只有 :562 一筆無關命中，不是已知問題
      - repo 工作樹 git status --short 與開始時相同（只有原本就存在的 3 個修改檔），本次只寫入 scratchpad

### [P3 → P3] 盤中量比分子（MIS 盤中整股量）與分母（官方全日量，含零股、盤後定價、鉅額）範圍不一致，收盤時系統性低估 5～8%（部分成立）

- **主張**：盤中 volume 取自 MIS 的 v（張）×1000，只含盤中整股成交；分母 _vol_ma5_prev_sum 來自 daily store，是官方 STOCK_DAY 全日量，官方註明含一般、零股、盤後定價、鉅額。即使 session_progress=1，盤中量比仍低於盤後值。另外，盤中 vol_ma5/vol_ma10 直接把未折算的部分量加進均量，盤中時段偏低。
- **正確的定義或數值**：分子與分母使用同一成交範圍（同為盤中整股，或同為官方全日統計）。
- **app 實際行為**：收盤時的盤中量比比盤後低 5～8%（2454 約為 0.63，盤後為 0.686）；在 2.0 門檻附近會漏判 signal_volume_surge。
- **對使用者的影響**：即時行情下，放量訊號與量比排序偏保守；高價股（零股占比高，如 2454）偏差最大。
- **修法建議**：daily store 另存盤中整股量作為量比分母，或在盤中把分母乘上該檔歷史「整股／全日」比例；vol_ma5/10 盤中改用時間折算量，或標示為「含部分量」。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/realtime/mis_provider.py:165-167 v_lots … volume_shares = int(round(v_lots * 1000))
  - backend/app/indicators/pipeline.py:1573-1586（vol_ma5/vol_ma10 含未折算部分量；vol_ratio 分母為 daily store 前 5 日和）
  - backend/app/repository.py:925 _vol_ma5_prev_sum
- **線上證據**：
  - 官方參考資料 raw\twse_stock_day_2454_202609.json 的 notes：「當日統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。」
  - https://www.moneydj.com/kmdj/news/newsviewer.aspx?a=4a6176bb-d43a-4abf-906c-322636bba550 ：9/23 盤後定價 2454 成交 7 張（36,295,000 元）——差額主要不是盤後定價，推測以零股為主
  - https://query1.finance.yahoo.com/v8/finance/chart/2454.TW?range=1mo&interval=1d ：Yahoo 日量為官方的 0.83～0.93 倍（如 9/22 10,551,036 vs 官方 12,131,543）
- **本地計算證據**：
  - 參考資料 csv\mis_snapshot.csv（t=13:30:00）與 STOCK_DAY 9/23 比值：2454 為 5,902,000/6,430,491=0.918；6196 為 1,657,000/1,773,924=0.934；0050 為 55,272,000/58,059,253=0.952；00878 為 24,973,000/26,437,407=0.945
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：盤中量比的分子是 MIS 的 v×1000，只含盤中整股（整股的盤後定價 fv、ov 欄位都不計入）。分母 _vol_ma5_prev_sum 取自本地 enriched／daily store，範圍接近官方全日量（含零股、盤後定價、鉅額），但並不等於 STOCK_DAY：本地 store 在部分日子少收盤後或鉅額成交，例如 2454 前 5 日有 4 天偏低，9/23 全體上市有 23/1330 檔偏低。兩邊範圍不一致，所以 13:30 收盤快照（session_progress=1.0，不做時間折算）到盤後更新之間的即時量比會系統性偏低。9/23 實測，系統實際函式算出的盤中值和盤後值為：2454 0.640 對 0.697（-8.1%）、6196 1.338 對 1.432（-6.6%）、0050 0.669 對 0.703（-4.8%）、00878 0.929 對 0.984（-5.5%）；finding 寫的 0.63／0.686 是用 STOCK_DAY 當分母算的，不是系統實際的值。「5～8%」只是單日樣本：以 Yahoo 日量（9/23 剛好等於 MIS 的 v+fv+ov）當代理，2454 其他日子的低估可能達 8%～15%（推測）。vol_ma5/vol_ma10 含未折算當日部分量是刻意的設計，收盤時只偏低 0.4%～1.6%，盤中早段才會明顯偏低（推測）。看盤清單過去約半年 71 次放量訊號中，有 15 次的量比落在 2.0～2.18，收盤時可能被漏判（推測）。
    - 理由：核心機制與相對偏差都重現了。盤中 volume 取 MIS 的 v×1000，只含盤中整股（fv=盤後定價、ov 是另外的欄位，都沒加進去）。量比分母 _vol_ma5_prev_sum 取自本地 daily/enriched store，範圍接近官方全日量（含零股、盤後定價、鉅額）。9/23 13:30 的 MIS 快照換算的 session_progress=1.0，所以 time_factor=1，收盤時的盤中量比就是 MIS 量除以前 5 日官方口徑均量。我用 app 的 compute_enriched_today 實際跑過：盤中收盤量比比盤後值低 4.8%～8.2%，和 finding 的 MIS／官方比值 0.918、0.934、0.952、0.945 完全一致。

不準確的地方有三個。
(1) finding 的絕對值（2454 盤中約 0.63、盤後 0.686）是拿 STOCK_DAY 當分母算的，不是系統實際的數字。系統的分母來自本地 store，而本地 store 並不等於 STOCK_DAY：看盤清單在 8～9 月 342 列中有 11 列偏低，2454 前 5 日有 4 天偏低；9/23 全體上市 1330 檔中也有 23 檔偏低。缺額換算的成交價落在收盤價或盤外價位，推測是更新時還沒收進盤後定價、盤後零股或鉅額。所以系統實際的值是盤中 0.640、盤後 0.697（enriched 分區存的 vol_ratio_5d=0.696907）。兩者相對落差一樣是 8.1%。
(2) 「5～8%」只是 9/23 單日、4 檔的樣本。Yahoo 在 9/23 的 2454 日量 5,917,267，剛好等於 MIS 的 v+fv+ov（5,902,000+7,000+8,267），所以可以把 Yahoo 當作盤中口徑的代理。用這個代理看，2454 在 9/14～9/23 的比值是 0.849～0.922，其他日子的低估可能到 8%～15%（推測）。
(3) vol_ma5/vol_ma10 含未折算部分量的寫法屬實，但程式碼註解寫明這是刻意保留的語意。收盤時的實際影響只有 -0.7%～-1.6%（ma5）和 -0.4%～-0.8%（ma10），真正明顯偏低的是盤中早段（推測）。

嚴重度維持 P3。這個偏差只出現在盤中到盤後更新之間的即時列，每日更新後就會被官方量覆蓋。使用者設定的監控規則是 0 條，影響限於量比顯示、排序與 signal_volume_surge。而且盤中本來就用線性時間折算，那個近似誤差遠大於這裡的 5～8%。歷史上看盤清單 71 次 vol_ratio_5d≥2.0 之中，有 15 次落在 [2.0, 2.179)，收盤時可能被漏判（推測，因為各檔的口徑比值每天不同）。
    - 證據：
      - 程式碼：backend/app/taiwan/realtime/mis_provider.py:165-167，volume_shares 只取 item['v']×1000，沒有用到 fv 或 ov
      - 程式碼：backend/app/indicators/pipeline.py:1572-1586，vol_ma5=(_vol_ma5_partial_sum+volume)/5 沒有折算，註解寫「保留原语义(含当天的均量)」；vol_ratio_5d=(volume*time_factor)/(_vol_ma5_prev_sum/5)
      - 程式碼：backend/app/repository.py:922-925，分母取 enriched 歷史快取的 volume.tail(5).sum()；quote_service.py:1673-1686 用 quote_ts 算 progress；calendar.py:23 REGULAR_OPEN_END=13:30，所以 t=13:30:00 時 progress=1.0（實跑 session_progress_from_ts(1790145000000)=1.0）
      - 官方參考資料 raw\twse_stock_day_2454_202609.json 的 notes：「當日統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。」；MI_INDEX 9/23 的 notes 相同，2454 為 6,430,491 股（和 STOCK_DAY 相同）
      - raw\mis_snapshot.json 的 2454：t=13:30:00、v=5902、fv=7（和 finding 引用 moneydj 的盤後定價 7 張一致）、ov=8267、ot=14:30:00
      - 本地計算 step2_ratio.py：MIS/官方 = 2454 0.917815、6196 0.934087、0050 0.951993、00878 0.944609，和 finding 完全一致；另外 6488（上櫃，不在看盤清單）= 28,081,000/28,714,838 = 0.9779
      - 本地計算，用 STOCK_DAY 當分母（finding 的算法）：2454 盤後 0.6860、盤中收盤 0.6296；6196 1.3946 對 1.3027；0050 0.7029 對 0.6691；00878 0.9839 對 0.9294
      - 本地計算，以系統實際路徑呼叫 compute_enriched_today(session_progress=1.0)，分母用本地 enriched 前 5 日（09-16、09-17、09-18、09-21、09-22）：2454 盤中 0.64013、盤後 0.696907（enriched 分區存的值也是 0.696907）；6196 1.338032 對 1.432449；0050 0.66913 對 0.702872；00878 0.929397 對 0.983897；比值 0.9185、0.9341、0.9520、0.9446；vol_ma5 比值 0.984～0.993，vol_ma10 比值 0.992～0.996
      - 本地計算 step1_scope.py：看盤清單 2026-08～09 共 342 列，本地 store 和 STOCK_DAY 完全相同的有 331 列、偏低的有 11 列。例如 2454 在 9/16 少 249,000 股、9/18 少 434,469 股、9/22 少 68,000 股、9/23 少 5,000 股（換算成交價 5,018.1，低於當日最低價 5,140）；9/23 全體上市 1330 檔中有 23 檔的本地量低於 MI_INDEX
      - 線上來源 https://query1.finance.yahoo.com/v8/finance/chart/2454.TW?range=1mo&interval=1d ：9/23 volume 5,917,267 = 5,902,000+7,000+8,267；9/22 為 10,551,036（官方 12,131,543，比值 0.870）；9/14～9/23 八天和官方的比值在 0.849～0.922 之間
      - 本地計算：看盤清單 2026-03-02～09-23 共 1287 列，存下的 vol_ratio_5d≥2.0 有 71 列，其中 15 列落在 [2.0, 2.179)
      - 腳本位置：AUDIT_SCRATCH/work/verify-volscope/step1_scope.py、step2_ratio.py

### [P3 → P3] ATR 以第一根 H−L 為 EWM 種子且無 min_samples，暖機期（約前 40～60 根）與 Wilder 定義偏差最高 27%（部分成立）

- **主張**：全量 atr_14 = TR.ewm_mean(alpha=1/14, adjust=False)。第一根沒有前收，TR=H−L，直接成為種子，且從第 1 根起就輸出值。Wilder 或 ChartSchool 的定義是前 14 個 TR 的平均作為第一筆 ATR。長歷史下差異收斂到 1e-13，但歷史起點（2024-01）與新上市股的前幾十根不準。matrix.py 的回測特徵同樣如此。
- **正確的定義或數值**：前 13 根為 null，第 14 根取 SMA(TR,14)，之後用 Wilder 遞推。
- **app 實際行為**：從第 1 根起就有值，前約 60 根偏離 Wilder。
- **對使用者的影響**：回測以 2024-01～03 為起點時，ATR 停損與 atr_pct 評分偏高；新上市或新納入的股票前兩三個月的 ATR 與 Keltner 通道不準。對目前自選的現值沒有影響。
- **修法建議**：TR 首根設為 null，ATR 以 rolling_mean(14) 作種子再遞推（或 ewm 加 min_samples=14）；matrix.py 同步修改。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:422-427（prev_close 為 null 時 max_horizontal 忽略 null，TR=H−L）、:482-485 ewm_mean(alpha=1.0 / 14, adjust=False)（無 min_samples）
  - backend/app/backtest/matrix.py:3933-3941 同樣是 valid_ewm_adjust_false
- **線上證據**：
  - https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-true-range-atr 「the first 14-day ATR is the average of the daily TR values for the last 14 days」
- **本地計算證據**：
  - a04_step7.py：2454 第 14 根（2024-01-22）app 25.57 vs Wilder 20.36（+25.6%），第 20 根 +16.2%，第 30 根 +7.0%，第 50 根 +0.83%，第 120 根 +0.005%；9 檔中第 0～19 根最大 26.6%，第 20～39 根 19.2%
  - 5 列的新序列：atr_14 從第 1 根就有值（0.70, 0.693, …），而 boll/annual_vol/momentum 都是 null
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：atr_14 的計算是 TR.ewm_mean(alpha=1/14, adjust=False)，位置在 pipeline.py:482-485，matrix.py:3941 的回測特徵與之相同。第一根沒有前收，所以 TR = H−L，並直接成為 EWM 種子；從第 1 根起就輸出值，沒有 14 根的暖機 null。Wilder 則要先累積 14 個 TR，取平均作為第一筆 ATR，之後才遞推。這會讓每條序列在前約 60 根偏離 Wilder，偏差可正可負，大小與方向取決於首根 H−L 相對於後續 TR 的大小；長歷史下差異會收斂到 1e-13 等級。

以本地 9 檔自選（2024-01-02 起）重算的結果：
- 若以 TA-Lib 風格為基準（首根不計 TR，第 15 根取 TR 第 2～15 根的平均）：2454 在 2024-01-22 的 app 值 25.57 vs 20.36（+25.6%）；9 檔第 0～19 根最大 26.6%，第 20～39 根最大 19.2%，第 40～59 根最大 4.1%，第 60～119 根最大 0.96%。
- 若依 ChartSchool 定義為基準（首日 H−L 也計入，第 14 根取 SMA）：2454 在 2024-01-19 為 +21.4%；9 檔第 0～19 根最大 24.0%，第 20～39 根最大 15.3%。
- 方向：4 檔的 app 值低於 Wilder（6196、0050、00885、00893，最多 −18.4%）。

這些百分比不是上限：以官方 TWSE 2026-08-03 起算的新序列，6196 在第 15 根偏 +47.4%。

影響：回測期間從 2024-01～03 開始時，atr_14 與 atr_pct 因子會偏高或偏低，不是一律偏高。新上市或新納入的股票，前兩三個月的 ATR 與 ATR 波動通道／Keltner 通道不準。台股 enriched 一律由完整歷史重算，自選目前的現值不受影響。

修法：TR 首根設為 null（或保留首日 H−L，但要與所採用的定義一致），以 rolling_mean(14) 作種子，再依 (prev×13+TR)/14 遞推，matrix.py 同步修改。只加 ewm 的 min_samples=14 無效，因為它只遮蔽前 13 個輸出，不改變種子。
    - 理由：核心機制與大部分數字都重現了。app 的 ATR 以第一根 H−L 為種子，從第 1 根起就輸出值。暖機期與 Wilder 的偏差也與 finding 的數字完全一致：2454 第 14 根（0 起算）app 25.5671 vs Wilder 20.3571，+25.593%；第 20、30、50、120 根分別 +16.154%、+6.986%、+0.834%、+0.005%。9 檔在第 0～19 根最大 26.577%，第 20～39 根最大 19.245%。長歷史末值差 5.7e-14。5 列新序列的 atr_14 為 0.7、0.692857…，而 boll、annual_vol、momentum 都是 null。matrix.py 的 float32 結果與儲存值的相對差在 5.6e-8 以內，第一根 TR = H−L = 35.0。

但有四處需要更正：
(1) 基準不一致。finding 的數字只有在「首根不算 TR、第 15 根（0 起算第 14 根）取 TR 第 2～15 根的平均」時才對得上，這是 TA-Lib 風格。它引用的 ChartSchool 定義則是「首日 TR = H−L 也算在內，第 14 根取前 14 個 TR 的平均」。依 ChartSchool，2454 在 0 起算第 13、14 根的偏差是 +21.4%、+19.8%，9 檔在第 0～19 根最大 24.0%、第 20～39 根最大 15.3%。finding 的 expected 寫「第 14 根取 SMA」，與它自己 local_evidence 的算法也互相矛盾。
(2) 方向不是一律偏高。9 檔中有 4 檔的 app 值低於 Wilder：6196 −4.9%、0050 −2.5%、00885 −4.5%、00893 −18.4%（第 14 根，TA-Lib 風格基準）。
(3)「最高 27%」只是這批資料的觀察值，不是上限。拿官方 TWSE 資料從 2026-08-03 起算一段新序列，6196 在第 15 根達 +47.4%，2379 達 +34.9%，2454 則是 −13.7%。
(4) 修法建議中「ewm 加 min_samples=14」無效。polars 1.40.1 的 min_samples 只會把前 13 根遮成 null；從第 13 根起，數值與不加時完全相同（第 14 根仍是 25.5671）。

另外，回測引擎裡 grep 不到 ATR 停損。atr_14 與 atr_pct 在回測裡是當因子／特徵使用，「ATR 停損」屬於推測。

台股 enriched 一律由完整歷史重算（enriched_refresh.py:67-99）；儲存的 atr_14 與完整重算的最大差為 0.0，沒有增量重新起算的痕跡。自選目前的現值不受影響，這點成立。嚴重度維持 P3。
    - 證據：
      - 程式碼：backend/app/indicators/pipeline.py:388（prev_close = close.shift(1)，首根為 null）、:422-427（max_horizontal 忽略 null，首根 TR = H−L）、:482-485（ewm_mean(alpha=1/14, adjust=False)，沒有 min_samples）
      - 程式碼：backend/app/backtest/matrix.py:3933-3941（fmax.reduce 會跳過 NaN 的前收）、:3359-3384（_valid_ewm_kernel 以第一個有效值為 state 並立即輸出）
      - 程式碼：backend/app/taiwan/enriched_refresh.py:67-99（台股 enriched 一律由完整歷史重算，不截斷暖機）
      - 本地計算 repro.py（AUDIT_SCRATCH/work/verify-atrseed/repro.py，輸出在 repro_out.txt）：以 compute_indicators 重算 9 檔，與 kline_daily_enriched 儲存的 atr_14 最大絕對差 0.0；enriched 的 OHLC 與 taiwan/daily 最大差 0.0（5943 列）
      - 數字並列（2454，基準為 TA-Lib 風格 W2，finding 的數字在括號內）：idx14 2024-01-22 app 25.5671 vs 20.3571 +25.593%（25.57 vs 20.36 +25.6%）；idx20 +16.154%（+16.2%）；idx30 +6.986%（+7.0%）；idx50 +0.834%（+0.83%）；idx120 +0.005%（+0.005%）；9 檔 0～19 根最大 26.577%，出自 00878（26.6%）；20～39 根最大 19.245%（19.2%）；末值絕對差 5.684e-14（1e-13）
      - 依 finding 引用的 ChartSchool 定義 W1 重算（首日 TR 計入，第 14 根取 SMA）：2454 idx13 2024-01-19 app 25.7646 vs 21.2143 +21.449%；idx14 +19.798%；idx20 +12.713%；idx30 +5.592%；idx50 +0.675%；9 檔 0～19 根最大 23.962%，20～39 根最大 15.337%，40～59 根最大 3.344%，60～119 根最大 0.793%
      - 方向（W2，idx14）：2454 +25.6%、3034 +12.2%、2379 +16.8%、3545 +20.1%、00878 +26.6%；6196 −4.864%、0050 −2.469%、00885 −4.535%、00893 −18.355%。9 檔中 4 檔的 app 值低於 Wilder，不是一律偏高
      - 官方資料 repro3.py（reference\csv\twse_stock_day_202608_202609.csv）：342 列 high、low、close 與本地 parquet 差值皆為 0.0；從 2026-08-03 起算的新序列在第 15 根的偏差（app vs W2）：6196 +47.44%、2379 +34.92%、3545 +25.00%、2454 −13.66%、0050 −4.81%。可見偏差大小與方向取決於首根 H−L，27% 不是上限
      - repro2.py：5 列合成新序列 compute_indicators 的 atr_14 = 0.7, 0.692857, 0.679082, 0.673433, 0.661045，boll_upper、annual_vol_20d、momentum_5d、ma20 全是 null（與 finding 一致）
      - repro2.py（polars 1.40.1）：ewm_mean(alpha=1/14, adjust=False, min_samples=14) 在 idx0、12 為 nan，idx13 = 25.764565383662205、idx14 = 25.567096427686334、idx20 = 24.014718322752454，與不加 min_samples 時逐位相同。min_samples 不改變種子，建議的替代修法無效
      - repro2.py：matrix.valid_ewm_adjust_false 重算 2454，首根 TR = 35.0 = H−L；idx14 = 25.5671 等於儲存值，與 W2 相差 +25.593%；對儲存值的最大相對差 5.55e-8（float32）
      - 線上：https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-true-range-atr 原文「the first TR value is simply the High minus the Low」「the first 14-day ATR is the average of the daily TR values for the last 14 days」「Current ATR = [(Prior ATR x 13) + Current TR] / 14」「The real ATR formula kicks in on day 15.」
      - 影響面：atr_14 與 atr_pct 在回測裡是因子／特徵（backend/app/backtest/factor.py:69-70、app\strategy\scoring.py:129-130、app\backtest\strategy.py:505）；ATR 波動通道／Keltner 用最後一列 ATR，只要求 height≥20（app\indicators\levels.py:302-306、375-393）。回測引擎 grep 不到 ATR 停損，「ATR 停損」屬推測

### [P3 → P3] annual_vol_20d 以 √252 年化並使用簡單報酬；台股實際每年 242～243 個交易日，教科書定義用對數報酬（部分成立）

- **主張**：年化係數沿用美股的 252；本 app 的台股交易日曆 2024 年 242 天、2025 年 243 天，年化值系統性高估約 2%。報酬用 pct_change（簡單報酬），與歷史波動率常用的對數報酬不同，高波動股差距最高約 4%。口徑是否可接受屬慣例選擇，但沒有文件說明。
- **正確的定義或數值**：採用台股年交易日數（約 242～250）與對數報酬，或在欄位說明中明示採 √252 與簡單報酬。
- **app 實際行為**：√252 加上簡單報酬，沒有說明。
- **對使用者的影響**：low_volatility_leader 的 vol_max=0.30 等門檻，實際上比名目嚴約 2%；與外部（對數報酬）年化波動比較時會偏高幾個百分點。
- **修法建議**：把年化天數設為依市場的常數（台股 250 或以實際日曆計），改用 log(close).diff()；或至少在 ENRICHED_COLUMNS、factor.py 的說明寫明口徑。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:518-526 pct_change … rolling_std(20) * (252 ** 0.5)
  - backend/app/indicators/pipeline.py:1612-1619 同樣 ×√252
  - backend/app/backtest/matrix.py:3912-3919 同樣
- **線上證據**：
  - https://en.wikipedia.org/wiki/Volatility_(finance) 「usually measured by the standard deviation of logarithmic returns」；「A common assumption is that P = 252 trading days in any given year.」
  - https://wiki.mbalib.com/zh-tw/%E5%8E%86%E5%8F%B2%E6%B3%A2%E5%8A%A8%E7%8E%87 （搜尋摘要）：求股價比的自然對數之標準差，再乘以一年時段數的平方根，若扣除閉市每年約 250 個交易日則乘 √250
- **本地計算證據**：
  - a04_step4.py（a）：daily store 分區數 2024=242、2025=243；√(252/242)=1.0205，√(252/243)=1.0184
  - a04_step2.py：stored（簡單報酬）與對數報酬版相對差最高 3.91%（2454，年化約 0.57～0.60）；√252 與 √245 相對差 1.42%
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：annual_vol_20d 的三條路徑都用簡單報酬（pct_change 或 close/prev-1）乘以 √252，程式與文件都沒有說明這個口徑。位置是 pipeline.py:518-526、pipeline.py:1612-1621、matrix.py:3912-3919；說明欄位在 factor.py:68、pipeline.py:171。
(1) √252 對台股的影響：本 app 的交易日曆 2024 年 242 天、2025 年 243 天，外部來源也吻合（2024 年計畫 247 天減 5 天颱風休市，2025 年計畫 246 天減 3 天新增國定假日）。相較依實際天數年化，數值恆定高估 2.0%／1.8%；相較 250 天只高 0.4%。low_volatility_leader 的 vol_max=0.30，換成 242 天口徑約等於 0.294。全市場約 1.1% 的列會因此改變門檻判斷。
(2) 簡單報酬與對數報酬的差距是雙向的，不是單向偏高：全市場中位數 +0.08%，p1 為 -3.4%，p99 為 +3.6%。在稽核窗口（2026-08～09），最大絕對差是 3034 的 -3.91%（2026-08-04，年化約 0.66），不是 2454；2454 在同期最多 +2.89%（2026-09-17，年化約 0.55），而且可以用官方 STOCK_DAY 收盤價重現。
屬於慣例選擇加上文件不足，嚴重度 P3。
另有一個新面向：盤中增量路徑 pipeline.py:1614-1618 用母體變異數（ddof=0），和收盤後全量路徑的 ddof=1 不一致，盤中值約低 2.5%（√(19/20)）。這一點只驗證了公式，沒有實際執行盤中路徑。
    - 理由：主軸成立：三條計算路徑都用簡單報酬乘以 √252，本 app 的台股交易日數也確實是 2024 年 242 天、2025 年 243 天，兩者的換算係數可以重現。但以下兩個細節不成立：
(1) finding 說簡單報酬與對數報酬的差距「最高 3.91%（2454，年化約 0.57～0.60）」，這個標的指錯了。在稽核使用的同一窗口（2026-08-03～09-23），差距最大的 3.91% 是 3034 在 2026-08-04 的 -3.91%，是簡單報酬版本比對數版本「低」。2454 在這個窗口的最大差距只有 +2.89%（2026-09-17，stored 0.552，對數版 0.536）。改用官方 STOCK_DAY 收盤價獨立重算，同樣得到 2.89%；而且 stored 值和官方重算值的差距小於 2e-15。
(2) user_impact 寫「與外部（對數報酬）年化波動比較時會偏高幾個百分點」，方向說錯了。簡單報酬和對數報酬的差距是雙向的：全市場 1,376,709 列中，stored 比對數版高的只佔 54.8%，中位數 +0.08%，p1 為 -3.37%，p99 為 +3.57%。正確說法是相對差約 ±3.5%，在 0.30 附近約等於 ±1 個絕對百分點。會單向高估的只有 √252 這一項。
√252 的部分完全重現：√(252/242)=1.02045，√(252/243)=1.01835，√(252/245)=1.01419。把 vol_max=0.30 換成 242 天的口徑，實際門檻約 0.294，這個說法成立。「沒有文件說明」也成立。252 是 Wikipedia 認可的常見慣例，所以這一項屬於口徑與文件層面的問題，P3 合適。
另外有一個 finding 沒提到的面向：pipeline.py:1614-1619 的增量（盤中）路徑用 sum_sq/20 - mean² 計算，是母體變異數（ddof=0）；全量路徑和 matrix 路徑都用 ddof=1。兩者相差 √(19/20)=0.9747，所以盤中值會比收盤後重算的值低約 2.5%。落地的 EOD 值 100% 符合 ddof=1，所以只影響盤中即時值。這一點我只驗證了公式，沒有實際執行盤中路徑。
    - 證據：
      - 程式碼：backend/app/indicators/pipeline.py:518-526 使用 pct_change，再算 rolling_std(20)*(252**0.5)（polars 預設 ddof=1）
      - 程式碼：backend/app/indicators/pipeline.py:1612-1621 用 close/prev_close-1 計算，vol_var = total_sq_sum/20 - vol_mean**2（母體變異數），再乘以 (252**0.5)。這條是 compute_enriched_today 的盤中增量路徑，呼叫處在 backend/app/services/quote_service.py:1681
      - 程式碼：backend/app/backtest/matrix.py:3912-3919 用 _valid_return_over_bars（matrix.py:4133-4142，values/previous-1，屬簡單報酬），再乘以 ddof=1 的 valid_rolling_std 與 np.float32(252**0.5)
      - 門檻：backend/app/strategy/builtin/low_volatility_leader.py:73 annual_vol_20d < vol_max（預設 0.30）
      - 文件：backend/app/backtest/factor.py:68 的 desc 只寫「20日收益率年化标准差」；pipeline.py:171 只寫「20日年化波动率」；strategy-guide.md:216 同樣只寫這幾個字；Grep docs\ 找不到 annual_vol／年化波動／ddof。三處都沒寫 √252 或報酬口徑
      - 本地重算（AUDIT_SCRATCH/work/verify-vol252log/v1_load.py）：taiwan/daily 與 kline_daily_enriched 的分區數都是 2024=242、2025=243、2026=176
      - 外部佐證交易日數：Yahoo 報導 2024 年計畫開市 247 天，扣掉颱風休市 7/24、7/25（凱米）、10/2、10/3（山陀兒）、10/31（康芮）共 5 天，得 242 天（https://tw.stock.yahoo.com/news/台股明年開市247天-近七年平高-201000302.html ；https://tw.stock.yahoo.com/news/2颱風攪局-台股10月休市3天... ；https://www.cna.com.tw/news/afe/202410020321.aspx）。2025 年計畫 246 天，扣掉新增的 9/29、10/24、12/25 三天國定假日，得 243 天（搜尋摘要；https://tw.stock.yahoo.com/news/必存！2025年下半年多三天假-台股行事曆搶先看！-062622985.html）
      - v2_calc.py 的係數計算：sqrt(252/242)=1.020452、sqrt(252/243)=1.018350、sqrt(252/245)=1.014185、sqrt(252/250)=1.003992、sqrt(20/19)=1.025978
      - v2_calc.py：用 enriched close 重算「簡單報酬、ddof=1、√252」，和 stored annual_vol_20d 比對，1,376,709 列全部相符（|rel|<1e-6 的比例為 1.0）；ddof=0 版本相符比例為 0.0，最近 8 個交易日也一樣
      - v3_window_official.py（稽核窗口 2026-08-03～09-23，共 10 檔）：stored 相對對數版本的最大值，0050 為 +3.898%（2026-08-27，0.395 對 0.380），2330 為 +3.38%，2454 只有 +2.891%（2026-09-17，0.552 對 0.536）；最大絕對差是 3034 在 2026-08-04 的 -3.911%（0.655 對 0.682）。finding 寫的「2454、3.91%、0.57～0.60」對不上
      - 官方資料重算（reference csv/twse_stock_day_202608_202609.csv，只取完全落在官方資料內的 20 日窗口，即 2026-08-31～09-23）：2454 簡單對對數最大 +2.891%（09-17）；stored 與官方簡單報酬版本的最大 |rel| 為 1.8e-15。其他自選股最大值：6196 為 +1.38%、3034 為 +1.17%、2379 為 -0.32%
      - 全歷史（2024-01～2026-09，全市場）：stored 相對對數版本的中位數 +0.0785%、平均 +0.216%、正值比例 54.8%、p1 為 -3.37%、p99 為 +3.57%。v_log 落在 0.25～0.35 的列：min -4.54%、max +4.90%、正值比例 51.2%。自選股的最大值是 00893 的 +4.43%（2025-05-08）
      - 改變門檻判斷的列數（vol<0.30，共 1,376,709 列）：改用 √242 為 15,631 列（1.14%）、√243 為 14,076 列、√245 為 10,980 列、√250 為 3,090 列；改用對數報酬為 6,262 列（0.45%）
      - 資料副作用（不屬本 finding）：0050 在 2025-06-18 的 enriched close 單日 -74.8%，00632R 在 2024-12-11 為 +604.9%，分割與反分割都沒有還原，造成 stored 波動率高達 2.68 和 21.47。這類離群值會讓簡單與對數的差距膨脹到 +209%，但根因是還原問題，不是年化口徑
      - Wikipedia（https://en.wikipedia.org/wiki/Volatility_(finance)）原文：「usually measured by the standard deviation of logarithmic returns」；「A common assumption is that P = 252 trading days in any given year.」
      - git status 與開始時相同（只有 daily_update.py、pnpm-lock.yaml、vite.config.d.ts 三個既有修改）；所有產物都在 scratchpad\audit\work\verify-vol252log\

### [P3 → P3] 台股自選清單與選股器的指標沒有過濾停牌空列，且盤中顯示昨收時點的動能（部分成立）

- **主張**：TaiwanDailyStore 含停牌列（OHLC 為 null、volume=0），technical_indicators 與台股選股器直接以列數滾動、未過濾。停牌後 5～20 日內，vol_ma 被 0 拉低、momentum 的 shift 少算一個真實交易日，甚至為 null。另外，自選清單的 momentum/vol_ma 以 daily store 最後一日計算，盤中與即時價並列時沒有標示時點。
- **正確的定義或數值**：與管線相同先過濾停牌列（或以有效列計窗）；盤中清單明示指標為「截至前一交易日」，或納入今日即時棒。
- **app 實際行為**：停牌後一段期間自選清單與台股選股器的動能、量均和 K 線或策略值不一致，甚至缺值。
- **對使用者的影響**：自選中的 3545 在 2026-03 初顯示的 5 日與 20 日動能、5 日均量與 K 線不同；盤中清單的動能是昨收時點。
- **修法建議**：compute_taiwan_daily_indicators 與 screener._compute_batch_indicators 先 drop close 為 null 或 volume=0 的停牌列；自選清單回傳指標的 as_of 日期。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/technical_indicators.py:76-86 rolling_mean(…, min_samples=…) 與 shift(5)/shift(20) 皆未過濾停牌列
  - backend/app/taiwan/screener.py:299-313 同樣未過濾
  - backend/app/indicators/pipeline.py:978-987 管線有 filter_halt_days（兩者行為不同）
  - backend/app/taiwan/watchlist_enrichment.py:150-161（指標來自 daily store）、:233-267（與即時 quote 放在同一列）
- **線上證據**：
  - （無需外部來源，屬內部一致性問題。）
- **本地計算證據**：
  - a04_nulls.py：daily store 中 3545.TWSE 在 2026-02-26 的 OHLC 全為 null、volume=0.0
  - a04_step9.py 自選清單函式與管線比對 3545：2026-03-02 momentum_5d 0.0294 vs 0.0135、momentum_20d -0.0557 vs -0.0723、vol_ma5 2,117,068 vs 2,299,323；2026-03-06 momentum_5d 為 null（管線 -0.1350）；2026-09-23 無停牌影響，差 0
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：TaiwanDailyStore 裡有 close 為 null 的列，包括停牌列（OHLC null、volume=0）和盤中無成交列（OHLC null、volume>0）。自選清單的 technical_indicators.py:79-86 與台股選股器的 screener.py:307-313 都直接按列數做 rolling/shift，沒有像 K 線來源 compute_enriched→filter_halt_days 那樣先剔除這些列。technical_indicators.py:9-14 自稱與 pipeline 同口徑，實作卻違反了這個約定。

以 3545.TWSE 在 2026-02-26 的停牌為例：
- 3/2：自選的 momentum_5d 為 0.02935，K 線為 0.01349；momentum_20d 為 -0.05566 vs -0.07231；vol_ma5 為 2,117,068 vs 2,299,323。
- 3/6：自選 momentum_5d 為 null，K 線為 -0.1350。3/27：自選 momentum_20d 為 null。
- 均線缺值更久：自選 MA5 缺 4 天（3/2～3/5）、MA10 缺 9 天、MA20 缺 19 天（3/2～3/26）、MA60 缺 59 天，K 線都有值。RSI14 與 MACD 也有小幅偏差（最多約 0.9 點）。
- 選股器的 momentum_5d、ma5、ma20 行為相同。截至 2026-09-23，全市場有 11 檔的最新 5 根內有 null 列，31 檔在 20 根內有。

對使用者的影響：使用者的自選欄位裡動能與量均是隱藏的，實際看得到的影響是 3545 在 2026-03 的 MA5 與 MA20 顯示「—」，RSI14 有小偏差。目前自選 9 檔都不受影響。

另外，盤中（16:30 日線更新之前）自選清單的指標停在前一交易日收盤，與即時價並列，但沒有指標的 as_of 標示。
    - 理由：核心數值完全重現，但影響範圍比 finding 寫的更大，使用者影響也寫錯，所以判「部分確認」。
(1) 重現的部分：我直接用 polars 讀 661 個 taiwan/daily 分區，再用純計算的 compute_taiwan_daily_indicators、TaiwanScreenerService._compute_batch_indicators（daily_store 換成只讀的 stub）和 compute_enriched 重算，並與磁碟上 kline_daily_enriched（K 線實際讀的表）逐日對照。3545.TWSE 在 2026-02-26 那一列 OHLC 全為 null、volume=0.0。自選函式對上 K 線（finding 數字在括號）：3/2 momentum_5d 0.0293542 vs 0.0134875（0.0294 vs 0.0135）、momentum_20d -0.0556553 vs -0.0723104（-0.0557 vs -0.0723）、vol_ma5 2,117,068 vs 2,299,322.8（2,117,068 vs 2,299,323）；3/6 momentum_5d 自選為 null，K 線 -0.134981；9/23 各欄差 0。選股器的 momentum_5d 與自選相同，同樣是 3/2 0.0293542、3/6 null。K 線表沒有 2/26 這一列，filter_halt_days 之後列數由 661 變 660：open 是 null，比較結果為 null，filter 就把這列丟掉。
(2) finding 漏掉的面向：自選清單 ma5／ma10／ma20／ma60 的 rolling_mean 帶 min_samples，視窗裡有 null close 就回 null。從 3/2 到 6/30，自選為 null、K 線卻有值的天數分別是 ma5 4 天、ma10 9 天、ma20 19 天、ma60 59 天。所以不是「5～20 日」，MA60 會缺約 3 個月。RSI14 和 MACD 也有小偏差，例如 3/9 RSI14 20.281 vs 21.143、3/2 macd_hist -0.40468 vs -0.42588。選股器的 ma5／ma20 同樣會變 null。
(3) 使用者影響寫錯：使用者的 preferences.json 把 momentum_5d、momentum_20d、vol_ma5 設為 visible:false，前端預設也是隱藏。畫面上實際受影響的是 visible:true 的 MA5、MA20、RSI14：MA5 在 3/2～3/5 顯示「—」，MA20 在 3/2～3/26 顯示「—」。目前（9/23）自選 9 檔都不受影響。
(4) 名詞修正：store 裡的 null 列不只有停牌。全市場共 12,351 列 close 為 null，其中 5,685 列 volume=0，另外 6,666 列 volume>0（盤中無成交但有零股或盤後量，例如 00656R.TWSE 2026-09-08 volume 636）。
(5) 選股器目前的影響：截至 2026-09-23，最新一列有效、且前 5 根內有 null 列的有 11 檔（00656R、1341、1423 等）；前 20 根內有 31 檔。
(6) 盤中時點：daily store 在 16:30 排程更新，16:00 前的目標日回退到前一交易日。所以盤中自選的指標停在前一交易日收盤，而同一列的 close 是即時價。這一列只有 live_date 和 quote_time，沒有指標的 as_of。已知的 A7 修的是價格的 live_date，沒有涵蓋指標時點。
(7) 嚴重度：這是偶發的局部顯示問題。technical_indicators.py 的 docstring 自稱與 pipeline 同口徑，實作卻違反了這個約定，但沒有造成資料破壞，維持 P3。
    - 證據：
      - 資料：3545.TWSE 2026-02-26 open/high/low/close=null、volume=0.0、amount=0.0（v1_halt_rows.py 輸出）；自選 9 檔的全部歷史只有這一列是 null。
      - 程式：backend/app/taiwan/technical_indicators.py:79-86 對 close/volume 直接 rolling_mean(min_samples=n) 與 shift(5)/shift(20)，沒有過濾；同檔 :9-14 docstring 宣稱公式與 pipeline.py 同口徑。
      - 程式：backend/app/taiwan/screener.py:307-313 同樣沒有過濾（rolling_mean(5)/shift(5)）。
      - 程式：backend/app/indicators/pipeline.py:978-987 filter_halt_days、:1010 compute_enriched 先過濾；K 線的來源 backend/app/taiwan/enriched_refresh.py:95 與 backend/app/api/kline.py:435 都走 compute_enriched。
      - 重算（v2_compare.py）2026-03-02：自選/選股器 momentum_5d=0.0293542，K 線 enriched 與 pipeline 重算=0.0134875；momentum_20d -0.0556553 vs -0.0723104；vol_ma5 2,117,068 vs 2,299,322.8（磁碟 kline_daily_enriched\date=2026-03-02 的值）。
      - 重算 2026-03-06：自選/選股器 momentum_5d=null，K 線=-0.134981；2026-03-27 自選 momentum_20d=null，K 線=-0.140684；2026-09-23 各欄與 K 線完全相同。
      - 重算（v3_ma_rsi.py）2026-03-02..06-30，自選為 null、K 線有值的天數：ma5 4、ma10 9、ma20 19、ma60 59、momentum_5d 1、momentum_20d 1；例如 3/2 ma20 null vs 53.76；3/9 rsi_14 20.281 vs 21.143。
      - 使用者設定：data/user_data/preferences.json 的 watchlist_columns 中 ma5、ma20、rsi14 為 visible:true，momentum_5d、momentum_20d、vol_ma5、vol_ma10 為 visible:false；前端預設 frontend/src/lib/watchlist-columns.ts:60-65 也是 false。
      - 全市場（v4_market.py）：共 1,435,948 列，其中 close 為 null 12,351 列、涉及 541 檔；volume=0 的 5,685 列，volume>0 的 6,666 列（例如 00656R.TWSE 2026-09-08 volume 636）；沒有 close 有值而 volume=0 的列。
      - 選股器目前的影響（v5_detail.py）：截至 2026-09-23，最新一列有效、且前 5 根內有 null 列的有 11 檔（00656R、00684R、1341、1423、1435、1525、1541、3168、8499、9110、911608）；前 20 根內有 31 檔。
      - 盤中時點：backend/app/taiwan/daily_update.py:39-40,113-119，16:00 前的目標日為前一交易日；backend/app/jobs/daily_pipeline.py:176-181 在 16:30 排程更新；backend/app/taiwan/watchlist_enrichment.py:150-160 的指標來自 daily store，:235-269 同一列只有報價的 live_date/quote_time，沒有指標 as_of。
      - 腳本與輸出：AUDIT_SCRATCH/work/verify-halt/（v1～v5 腳本、compare_3545.csv）；repo 的 git status 與開始時相同，只有原本 3 個修改。

## 確認正確的項目

- **boll_upper / boll_lower（盤後全量路徑）**：讀 backend/app/indicators/pipeline.py:417、:448-451。公式為 close.rolling_std(20)（polars 預設 ddof=1）與 ma20±2σ。以 a04_step2.py 的純 Python 獨立實作（a04_indep.py）比對已存 enriched，342 列。
  - 來源：本地：stored boll_upper - 獨立樣本標準差 max|diff|=3.8e-11；與母體標準差版 max|diff|=19.20（2454），帶寬比母體寬 2.598%
  - 來源：本地：stored 與 compute_indicators(app 序列) max|diff|=0.0
  - 來源：https://en.wikipedia.org/wiki/Bollinger_Bands 「Typical values for N and K are 20 days and 2」（參數 20、2 與權威定義一致；σ 口徑差異見 findings）
- **ma20（布林中軌）**：獨立 SMA20 比對已存 ma20
  - 來源：本地：stored ma20 - 獨立中軌 max|diff|=5.7e-14（342 列）
- **atr_14（盤後全量，暖機後）**：pipeline.py:422-427（TR=max(H-L,|H-PC|,|L-PC|)）與 :482-485（ewm alpha=1/14，adjust=False）。獨立實作 Wilder：第一筆 ATR 取前 14 日 TR 平均，之後 (ATR×13+TR)/14。
  - 來源：本地：stored atr_14 - Wilder max|diff|=5.7e-14，相對誤差 5.2e-16（342 列）
  - 來源：https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-true-range-atr 「the first 14-day ATR is the average of the daily TR values for the last 14 days… ATR(14) = [(Prior ATR x 13) + Current True Range] / 14」
- **atr_14（盤中增量路徑）**：pipeline.py:1546-1554 遞推 tr/14+ATR×13/14。依 repository.py:736-937 重建 live_agg 後餵入 compute_enriched_today，逐日模擬 38 天，再與全量比對（a04_step3.py）。
  - 來源：本地：增量與全量 atr_14 max|abs|=5.7e-14
- **annual_vol_20d（盤後全量）的實作是否符合其宣告公式**：pipeline.py:518-526：close.pct_change() 的 rolling_std(20)（ddof=1）×√252。以獨立實作核對。
  - 來源：本地：stored - 獨立（簡單報酬、ddof1、√252）max|diff|=1.2e-14；年化係數與報酬口徑的慣例問題見 findings
- **vol_ma5 / vol_ma10（盤後全量；盤中在收盤時點）**：pipeline.py:428-431（含當日的 rolling_mean）、:1572-1574（部分和加當日量）。獨立比對，並做增量模擬。
  - 來源：本地：stored - 獨立 max|diff|=0.0（342 列）
  - 來源：本地：增量與全量 max|abs|=3e-08（float 誤差）
- **vol_ratio_5d（盤後全量）**：pipeline.py:434-436、:486-491：volume / volume.shift(1).rolling_mean(5)（不含當日）。獨立比對。
  - 來源：本地：stored - 獨立 vol/mean(前5日) max|diff|=0.0
  - 來源：https://wiki.mbalib.com/wiki/%E9%87%8F%E6%AF%94 （量比 = 現成交總手 / (過去5日平均每分鐘成交量 × 當日累計開市時間(分))，分母為過去 5 日，不含當日）
- **盤中量比時間折算的數學形式**：pipeline.py:1578-1587：volume×(1/p)/前5日均量，p = 已過分鐘數/270；calendar.py:31、:124-145。代數上等於 (V/已過分鐘)/(前5日均量/270)，就是量比定義換成台股 270 分鐘；p=1 時與全量一致。
  - 來源：本地：增量（p=None）與全量 vol_ratio_5d max|abs|=8.9e-16
  - 來源：https://wiki.mbalib.com/wiki/%E9%87%8F%E6%AF%94 量比公式如上
- **session_progress / session_minutes_elapsed（台股時段）**：calendar.py:21-31、:124-161：09:00～13:30 連續 270 分鐘、無午休，超出範圍即夾限。實測輸出：09:00=0、11:15=0.5、13:30 以後=1.0。
  - 來源：https://academy.fbs.com.tw/academy/columndetails/c000590 及同批搜尋結果：盤中 9:00～13:30；13:25～13:30 收盤集合競價；盤後定價 14:00～14:30
  - 來源：本地 a04_step6.py：session_progress(11:15)=0.500000、(13:30)=1.0、(08:59)=0.0
- **momentum_5d/10d/20d/30d/60d（盤後全量；盤中在窗口足夠時）**：pipeline.py:493-502：close/close.shift(N)-1（ROC，小數）；增量路徑 :1602-1608 用 _close_Nd_ago=tail(N).first()。獨立比對，並做 38 天增量模擬。
  - 來源：本地：stored - 獨立 各期 max|diff|=0.0；增量與全量（2026-08/09）max|abs|=0.0
  - 來源：https://www.ezchart.com.tw/inds.php?IND=MTM 等：台灣慣例 MTM = Ct − Ct-n（價差）。app 欄名為「動量」，但 ENRICHED_COLUMNS 已標明「涨跌幅小数」（pipeline.py:165），屬 ROC，口徑已明示
- **high_60d / low_60d（盤後全量，以收盤價計、含當日）**：pipeline.py:437-440 close.rolling_max/min(60)。獨立比對兩種定義（收盤價與最高／最低價）。
  - 來源：本地：stored - max(close,60) max|diff|=0.0；stored - max(high,60) max|diff|=410（證實以收盤價計）
  - 來源：factor.py:85「收盘价 / 60日最高收盘价 - 1」、market_intelligence.py:330-366 同樣以收盤價計
- **signal_n_day_high/low、signal_boll_breakout_upper/breakdown_lower、signal_volume_surge（盤後全量）**：pipeline.py:655-659。以獨立欄位重算布林值並比對。
  - 來源：本地：三項 max|diff|=0（342 列）
- **自選股 OHLC 與官方（輸入資料）**：a04_step1.py：TaiwanDailyStore 與 STOCK_DAY 逐列比對 open/high/low/close
  - 來源：本地：342 列 OHLC 完全一致，差異只在 volume（見 findings）
  - 來源：第三方 https://query1.finance.yahoo.com/v8/finance/chart/2454.TW?range=1mo&interval=1d ：2454 收盤 22/22 天與官方一致
- **台股自選清單的 momentum_5d/20d、vol_ma5/10（technical_indicators.compute_taiwan_daily_indicators）在無停牌列時**：把 daily store 資料餵入純函式，與管線在 2026-09-23 比對（a04_step9.py）
  - 來源：本地：四欄 max|diff|=0.0（9 檔）

## 無法驗證的項目

- **第三方網站的指標數值（BOLL、ATR、量比、波動率）三方比對**：https://tw.stock.yahoo.com/quote/2454.TW/technical-analysis 靜態內容沒有指標數值（由前端動態產生）；https://www.investing.com/equities/mediatek-inc-technical 回 HTTP 404；https://www.cnyes.com/twstock/a_technical2.aspx 只有欄名、沒有資料列。只取得 Yahoo chart API 的原始 OHLCV 作為第三方原始資料比對。
- **台灣券商或看盤軟體的布林標準差口徑（母體或樣本）**：搜尋結果只說各軟體可能不同，沒有權威來源。中國來源互相矛盾：chanluns 或 agudashi 稱通達信 STD 為「估算標準差」（n−1）；https://www.cnblogs.com/citycomputing/p/10447657.html 實測結論為「通達信使用的是總體方差」。
- **開盤數秒內量比放大的實際幅度（finding 7）**：data\kline_minute 只有 4 天，每天從 09:13～09:42 才開始記錄，覆蓋全日量 21%～55%，量不到開盤集合競價的占比。
- **2026-08 以前的暖機歷史是否與官方一致**：參考資料只下載了 2026-08～09 的 STOCK_DAY。2024-01～2026-07 只能用 app 自己的 daily store 當暖機序列，不能連證交所補抓。
- **除權息日 MIS 的 y（前收）是否為除權息參考價**：需要除權息當日的 MIS 快照；參考資料只有 9/23 的快照，而且禁止直連 mis.twse.com.tw。這會影響盤中路徑在除權息日的 TR、today_ret 與 change_pct（推測盤中用參考價，盤後用前一日收盤價）。
- **app 近期成交量與官方不符的來源（finding 11 的根因）**：2454 在 9/23 的 app 量 6,425,491 恰好等於 Yahoo 當日量，其他日期不等於 Yahoo 也不等於官方；屬資料擷取領域，未追查 daily_update 來源鏈。
- **backtest/matrix.py 衍生特徵：vol_ratio_10d、vol_trend_5_10、atr_pct、boll_position、boll_width、distance_to_high_60d、distance_from_low_60d**：只做程式碼比對（matrix.py:3852-3975：boll 用 ddof=1、high_60d 用收盤價、vol_ratio 分母為前 N 日、ATR 用 ewm 1/14，與管線全量同口徑），沒有建構 MarketDataMatrix 實際跑數值。
