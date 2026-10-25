# A09-strategies-momentum｜策略合理性（量價、動能、因子與機器學習類）

> 本文件已封存，後續追蹤改到 [docs/audit-2026-10-22/](../../audit-2026-10-22/README.md)。

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 5 | 7 | 0 | 0 | 0 | 21 | 7 |

## 稽核者摘要

結論：7 支策略的程式邏輯都和各自的公式一致。我依權威定義獨立重寫一次，在 923,331 格的訊號矩陣上比對，除了浮點邊界或已查明的原因之外，完全一致。7 支策略把 T 之後 59 根 K 棒全部擾動，T 以前的進出場訊號與分數都沒有改變，沒有未來函數；lorentzian_knn 的標籤只取 j+4<=t 的鄰居，也沒有資料洩漏。問題不在公式本身，而在輸入資料與口徑：
(1) 台股 enriched 價格完全未還原除權息。全部 1,423,597 列的 close 都等於 raw_close，違反 CONTRIBUTING §3.2 的「前復權」契約。2026-06~09 共 956 次除權息中，有 221 次在除權息日產生假的 MA20 跌破出場（P1）。
(2) lorentzian_knn 的 KNN 候選池大小取決於歷史窗長度。選股只載入 121 根，回測則載入整段期間，同一天的進場訊號只有 47.5% 一致（P2）。
(3) factor_rank_research 用截面 min-max 標準化，再套固定的 70/40 分門檻，結果被極端值主導。雙因子組合每天中位數 0 檔入選、99.6% 標的觸發出場，而且和因子研究採用的 rank IC 口徑不一致（P2）。
(4) 歷史 turnover_rate 只在 2026-09-23 這一天有值。high_turnover_surge 在 418 個歷史交易日產生 0 筆進場。low_volatility_leader 在其他日期的分數全為 0；在最新日，ETF（包含自選的 0050、00878、00893）被評為 0 分，等於把空值偽裝成零分；程式沒有 fail-closed（P2）。
(5) rolling_quantile／rolling_min／rolling_max／rolling_sum 以原始列而非有效 K 棒為窗口，遇到缺列時訊號會被靜默抑制 60～80 根。布林收斂突破因此少了 8.4% 的進場（P2）。
(6) enriched／台股日線存檔的成交量低於官方 STOCK_DAY／MI_INDEX（380 列中 16 列），2330 在 9/18 的量比因此從 2.11 變成 1.93，跨過 2.0 門檻；此外 9/23 的分區完全沒有上櫃資料（P2）。
另有 P3：composite 的選股與回測在單一候選時分數口徑不同，排名也受 PYTHONHASHSEED 影響；lorentzian_knn 與 TradingView 原版在設計上有多處差異，38% 的鄰居含 0.5 佔位值；布林用樣本標準差，而 Bollinger 規定母體標準差；換手率門檻 5% 沿用 A 股慣例，在台股以發行股數計算時落在約 p97；年化用 252 天，台股實際為 242／243 天；ema{N}_bias 會隨矩陣窗長度漂移。
驗證方式：官方資料與 enriched 的 OHLC 完全一致；BB、BandWidth、close_position、change_pct、動能、年化波動與量比，由官方資料獨立計算後與 app 比對，誤差在 1e-4 以內；Yahoo 奇摩的漲跌幅與振幅和 app 一致（3 檔）。所有腳本都在 scratchpad\audit\work\A09\，repo 工作樹與 data\ 都沒有被修改。網路請求共 23 次，沒有連線 TWSE／TPEx。

## 問題清單

### [P2 → P1] enriched／台股日線存檔的成交量低於官方數字，且 2026-09-23 的分區缺少全部上櫃資料（確認成立）

- **主張**：2026-09-16～09-23 有 16 列的成交量與成交額低於 STOCK_DAY／MI_INDEX，而量比、布林量能與換手率都以這個量計算。另外，選股的基準日 2026-09-23 的分區只有上市 1,323 列，上櫃 0 列。
- **正確的定義或數值**：成交量與官方 STOCK_DAY 一致；選股基準日的資料應完整（上市與上櫃都有）
- **app 實際行為**：部分日子量偏低；最新日缺上櫃
- **對使用者的影響**：量價類策略的量能門檻可能被翻轉；在 2026-09-23 做選股時，上櫃股全部消失，截面排序（min-max）的母體也跟著改變
- **修法建議**：由資料擷取領域追查量的來源（推測：部分日子使用了不含某類交易的端點或盤中快照）；latest_date 應要求上市與上櫃都已到齊才視為完整交易日
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/repository.py:1416-1427 — taiwan_official_latest_date 取 TaiwanDailyStore 的最後日期
  - backend/app/indicators/pipeline.py:486-491 — vol_ratio_5d = volume / 前 5 日均量
- **線上證據**：
  - TWSE STOCK_DAY 與 MI_INDEX 參考檔：2454 在 2026-09-23 的成交股數都是 6,430,491（reference\csv\twse_stock_day_202608_202609.csv、twse_mi_index_20260923_quotes.csv）
- **本地計算證據**：
  - step2_coverage.py：380 列中有 16 列的量不一致，全部是 enriched 較少：2330 在 9/18 少 3,702,177 股（−9.05%）；6196 在 9/17 少 165,000 股（−17%）；2454 在 9/18 少 434,469 股
  - data\taiwan\daily 的 2454 在 9/23 也是 6,425,491，可見差異在存檔層就已存在
  - step14_vr.py：2330 在 9/18 以官方量計算的量比為 2.110，app 為 1.932，跨過 volume_price_surge 的 2.0 門檻
  - 2026-09-23：enriched 1,323 列、上櫃 0 列；taiwan/daily 1,330 列、上櫃 0 列；前一日 2,316／2,340 列（上櫃 988／1,009 列）
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：台股日線存檔（data/taiwan/daily）與 enriched 的最近幾天資料是在當天約 16:30 從 MI_INDEX 抓到的未定稿快照，只寫一次、不再更新。結果有兩個。
(1) 成交量與成交額偏低：STOCK_DAY 參考列在 9/16–9/23 有 16 列偏低，9/23 全市場有 23 檔偏低。缺少的部分很可能是鉅額交易（推測），但只影響當天寫入的日期；隔天補抓的日期與官方完全一致，所以量的口徑前後不一致。
(2) TPEx 抓取失敗時，程式只寫入上市資料並回報成功。9/16 與 9/23 兩天都缺全部約 1,000 檔上櫃股，且沒有自動修復路徑，會成為永久缺口：選股基準日 9/23 的上櫃股全部消失，上櫃股的 5 到 60 日滾動指標也跟著錯位（例如 6488 在 9/17 的 momentum_5d，app 為 −4.50%，官方資料算是 −0.55%）。
量比受影響的實例是 2330 在 9/18，放量訊號由 true 翻成 false（官方 2.110 對 app 1.932），但 volume_price_surge 在該日本來就不會進場。修法要同時做到三件事：當天寫入的分區隔天重抓定稿；只有單一市場的寫入不得視為完成；latest_date 要求上市與上櫃都已到齊。
    - 理由：finding 的核心主張都已成立，我逐項重跑確認過。
(1) 成交量偏低：拿 STOCK_DAY 參考檔 380 列比對，taiwan/daily 與 kline_daily_enriched 各有 16 列的量與額低於官方，而且全部是偏低，列數、代號、差額都與 finding 一致。收盤價則全部相符。
(2) 9/23 分區缺上櫃：9/23 分區只有上市資料（daily 1,330 列、enriched 1,323 列，上櫃都是 0）。screener.latest_date 走 taiwan_official_latest_date，取 TaiwanDailyStore 最後一個日期，所以選股基準日就是這個不完整的 9/23。
(3) 量比：2330 在 9/18 的量比，用官方量算是 2.110，app 存的是 1.9317，signal_volume_surge 因此由 true 翻成 false。

需要修正或補充的地方：
(a) 根因不是 finding 推測的「用了不同端點」，而是同一天約 16:30 抓 MI_INDEX 得到的是未定稿快照。當天寫入的分區（9/16、9/17、9/18、9/22、9/23，寫入時間 16:19 至 17:03）都有短少；隔天才補抓的分區（9/1–9/15 在 9/16 11:21 寫入，9/21 在 9/22 10:55 寫入）與官方完全一致。9/23 全市場比對有 23 檔短少，缺的量大多是整千股的大筆數字，隱含成交價還常落在當日高低區間之外（例如 2882 的隱含價是 123.0，當日最高只有 112.5）。這符合鉅額交易的特徵，但鉅額交易的時段我沒能上網查證，所以歸因屬於推測。
(b) 上櫃缺漏不只 9/23，9/16 也缺全部上櫃（1,006 檔），而且沒有任何自動修復路徑。TPEx 抓取失敗的例外會被吞掉，只寫入上市資料；refresh_dates 遇到已存在的日期會直接跳過；run_update 只從 d_max+1 開始抓；排程也從不帶 force。9/23 16:30 的 log 記錄了 TPEx 抓取失敗，整次更新卻回報 success；21:40 那次甚至回報 is_fully_current=True。所以 9/23 在下一次更新後也會變成永久缺口。9/16 缺口已經讓上櫃的滾動指標出錯：6488 在 9/17 的 momentum_5d，app 是 −4.50%，用官方資料算是 −0.55%。9/16 缺上櫃的原因未查明，因為當天的 log 已經輪替掉了。
(c) 影響要收斂一點：2330 在 9/18 是 signal_ma20_breakout=false、收盤等於開盤，volume_price_surge 本來就不會進場，實際翻轉的只有放量訊號。380 列中只有這 1 列翻轉，使用者自選股在這段期間都沒有翻轉。程式碼裡找不到「布林量能」，這一點沒有驗證。換手率確實用 volume 計算。
(d) 修法：只把 latest_date 改成「兩個市場都到齊才算」並不夠。還要讓當天寫入的分區隔天重抓定稿，並讓只有單一市場的部分寫入不被視為已完成。

嚴重度：只看 finding 原本描述的範圍，P2 合理。但這個問題會永久、無聲地留下缺口：6 個交易日內已發生 2 次，每次讓約 1,000 檔上櫃股從選股基準日消失，並讓 5 到 60 日的滾動指標算錯，更新狀態卻回報成功。這違反 CONTRIBUTING 第 11 節第 8 點「部分寫入時不得產生錯誤狀態」，而且範圍涵蓋整個上櫃市場的選股核心流程，所以我建議升為 P1。以使用者情境來看，自選 9 檔都是上市股，不受上櫃缺口影響；受影響的是自選股的量（例如 6196 在 9/17 少 17%）以及選股功能。

線上來源：
- Yahoo Finance 的 2330.TW 日量比官方總量與 app 都低，顯示各資料商的量口徑不同。至於 Yahoo 只算盤中整股，屬於推測。
- MoneyDJ 對鉅額交易的定義是單筆 500 交易單位（50 萬股）以上，與多數短少量的規模相符。
- 搜尋工具的額度已用完，Google、Bing、DuckDuckGo 不是被擋就是結果無關，所以鉅額交易時段沒有查到線上佐證。
    - 證據：
      - 本地計算，v2_volume.py（AUDIT_SCRATCH/work/verify_vol_otc/v2_volume.py）：taiwan/daily 與 kline_daily_enriched 各與 STOCK_DAY 比對 380 列，vol/amount 不一致 16 列、全部偏低、收盤不一致 0 列。例：2330 9/18 官方 40,892,688、存檔 37,190,511（−9.05%）；6196 9/17 官方 973,428、存檔 808,428（−16.95%）；2454 9/18 少 434,469；2454 9/23 官方 6,430,491、存檔 6,425,491
      - 本地計算，v1_compare.py：taiwan/daily 9/16 為 1,330 列（TPEX=0）、9/23 為 1,330 列（TPEX=0），9/22 為 2,340 列（TPEX 1,009）；enriched 9/16 為 1,319 列（TPEX=0）、9/23 為 1,323 列（TPEX=0），9/22 為 2,316 列（TPEX 988）。9/16 缺上櫃是 finding 沒提到的部分
      - 分區修改時間（ls --time-style=full-iso）：daily 9/1–9/15 在 2026-09-16 11:21 寫入，比對 0 差異；9/21 在 2026-09-22 10:55 寫入，0 差異；有差異的 9/16、9/17、9/18、9/22、9/23 都在當天 16:19–17:03 寫入。可見差異來自當日快照，不是端點不同
      - 本地計算，v4_mi_index.py：9/23 存檔與 MI_INDEX 全市場 1,380 列比對，23 檔量偏低、0 檔偏高，全市場合計差 12,235,374 股（7,547,808,593 對 7,535,573,219，約 0.16%）。個股差距可以很大，例如 6464 為 302,300 對 2,300（−99.24%）、1109 −89.21%、1227 −77.95%
      - 本地計算，v5_amt.py：缺少部分的隱含成交價落在當日高低區間外，例如 2882 為 123.0（當日高 112.5）、1227 為 25.67（當日低 28.2）、00919 為 28.95（當日低 32.0）、2903 為 20.3（當日低 22.25）。這符合鉅額交易特徵（歸因屬推測）
      - data/backend.log:37258 — '2026-09-23 16:30:05 [WARNING] ... TPEx snapshot fetch failed for 2026-09-23: peer closed connection without sending complete message body'；:37265 同一次回報 overall_status=success, daily=success；:45477 21:40 那次回報 is_fully_current=True
      - backend/app/taiwan/providers/snapshot_provider.py:115-118 — TPEx 例外被吞掉，只記 warning，仍回傳上市 frames
      - backend/app/taiwan/daily_refresh.py:167-175 — 已存在的日期直接跳過（if cur in available），否則 fetch_date 後就 write_batch，不檢查兩個市場是否都到齊
      - backend/app/taiwan/daily_update.py:282 與 :124-147 — d_range 從 d_max+1 開始，已寫入的日期永不重抓；backend/app/jobs/daily_pipeline.py:149-153 排程呼叫 run_update 不帶 force
      - backend/app/repository.py:1416-1427 — taiwan_official_latest_date 回傳 TaiwanDailyStore.available_dates()[-1]；backend/app/services/screener.py:426 選股基準日用這個值（得到 9/23）
      - backend/app/indicators/pipeline.py:436（_vol_ma5_prev = volume.shift(1).rolling_mean(5)）、:486-491（vol_ratio_5d = volume/_vol_ma5_prev）、:659（signal_volume_surge = vol_ratio_5d>=2.0）、:738-743（turnover_rate 用 volume）。grep 找不到「布林量能」
      - 本地計算，v3_vr.py：2330 9/18 官方量比 2.11、enriched 1.9317，signal_volume_surge 由 true 翻成 false。380 列中只有這 1 列翻轉。該列 signal_ma20_breakout=false、close=open=2460，volume_price_surge（backend/app/strategy/builtin/volume_price_surge.py:56-66）本來就不會進場
      - 本地計算，v6_tpex_gap.py：參考檔 tpex_trading_stock 顯示 TPEx 在 9/16 有交易；6488 的 momentum_5d 在 app 為 9/17 −0.045、9/22 +0.0789，用官方資料算是 −0.0055、+0.0624，可見 9/16 缺口讓上櫃滾動指標錯位
      - 線上：https://query1.finance.yahoo.com/v8/finance/chart/2330.TW?period1=1789344000&period2=1790208000&interval=1d — 9/18 volume 35,352,856、9/23 volume 20,406,553，都低於官方（40,892,688／22,817,873）與 app（37,190,511／21,978,873），顯示資料商之間的量口徑不同。app 自己的設計（snapshot_provider.py 取 MI_INDEX 成交股數）與歷史分區都採用官方總量，所以最近幾天是口徑前後不一致
      - 線上：https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=6aabb5c6-179c-4a22-95ee-9b48fe83eee7 — 原文：「一次買賣同一上市證券數量在相當於每股(或其他單位)面額十元，總數五佰交易單位（意即五十萬股）以上者，稱為鉅額交易」。多數短少量（00919 2,000,000、2376 1,300,000、2880 1,258,000、2834 900,000、2330 839,000 股）符合這個規模
      - 沒查到的部分：鉅額交易的申報／成交截止時間，以及 MI_INDEX 何時併入鉅額量，都沒有線上佐證（WebSearch 額度已用完，Google／Bing／DuckDuckGo 不是被擋就是結果無關，zh／en Wikipedia 沒有這項資料）。9/16 缺上櫃的直接原因未查明（backend.log.2 已輪替）
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：finding 的數字全部重現。以 STOCK_DAY 的 10 檔、380 列比對，enriched 與 taiwan/daily 各有 16 列的成交量與成交額低於官方數字，日期在 2026-09-16～09-23，全部偏低；8/03～9/15 完全一致。以 9/23 全市場比對 MI_INDEX，有 23 檔上市股偏低。

有差異的分區都是交易日當天 16:19～17:03 寫入的；隔天才寫入的分區（9/21、9/10～9/15）完全一致。官方 STOCK_DAY／MI_INDEX 的量含鉅額交易，因此推測是當天抓到尚未定稿的 MI_INDEX（缺鉅額交易）；這點未獨立驗證。refresh_dates 看到分區已存在就不會重抓，所以這些偏低的量會一直留著。量比、換手率與量比門檻都用這個量計算。

2330 在 9/18 的量比：官方量算出 2.110，app 為 1.932。跨過 2.0 門檻的是 signal_volume_surge；volume_price_surge 當天因為沒有突破 MA20、也沒有收紅K，進場結果不受影響。

上櫃的缺口不只發生在最新日。9/23 與 9/16 兩個分區的上櫃資料都是 0 列（9/23：taiwan/daily 1,330 列、enriched 1,323 列，全部是上市）。9/23 的 log 證實上櫃抓取因連線中斷而失敗，pipeline 卻仍回報 success，並以 9/23 為 as_of 重算策略快取。9/16 的缺口至今未補，會讓約 1,000 檔上櫃股的滾動指標缺少一天。選股基準日只看 TaiwanDailyStore 的分區是否存在，所以 9/23 選股時上櫃股全部不在母體內。
    - 理由：我寫了兩支腳本（放在 scratchpad\audit\work\verify-volgap\s1_compare.py、s2_vr_tpex.py），直接用 polars 讀 parquet，再和已下載的官方參考檔比對。finding 列出的每個數字都能重現，一字不差。

(1) 10 檔 × 2026-08～09 的 STOCK_DAY 共 380 列：enriched 與 taiwan/daily 都有 16 列的量與額不一致，16 列全部是本地較少，日期都落在 9/16～9/23。8/03～9/15 的 364 列完全相同。
(2) 並列比較：
- 2330 9/18 少 3,702,177 股（−9.053%）；finding 寫 −9.05%，一致。
- 6196 9/17 少 165,000 股（−16.95%）；finding 寫 −17%，一致。
- 2454 9/18 少 434,469 股，一致。
- 2454 9/23 本地 6,425,491，官方 6,430,491，一致。
- 2330 9/18 量比：官方量算出 2.109952，app 存檔 1.931707；finding 寫 2.110／1.932，一致。
(3) 列數：
- 9/23：taiwan/daily 上市 1,330、上櫃 0；enriched 上市 1,323、上櫃 0。
- 9/22：taiwan/daily 2,340 列（上櫃 1,009）；enriched 2,316 列（上櫃 988）。
兩天都與 finding 相同。
(4) 選股基準日 = TaiwanDailyStore 最後一個分區（repository.py:1427、screener.py:426），所以基準日是 9/23，上櫃股全部不在選股母體內。

另有四點是原 finding 沒提到的：
(a) 9/16 的分區也完全沒有上櫃資料（兩個存檔都是），而且到 9/24 仍未補上。官方參考檔顯示 6488、8069 在 9/16 都有成交。所以這不只是「最新日尚未到齊」，而是永久缺口。原因在 daily_refresh.py:167-170：分區已存在就跳過。再加上 snapshot_provider.py:112-118：上櫃抓取失敗時，仍會把只含上市的資料寫入。
(b) 9/23 的 log 直接證實上櫃缺漏的成因：「TPEx snapshot fetch failed for 2026-09-23: peer closed connection…」，但該次執行仍回報 overall_status=success，並且照樣以 as_of=2026-09-23 重算了策略快取。
(c) 量偏低與寫檔時間高度相關。有差異的分區都是交易日當天 16:19～17:03 寫入的（9/16、9/17、9/18、9/22、9/23）。隔天才寫入的分區完全一致（9/21 在 9/22 10:55 寫入；9/10～9/15 在 9/16 11:21 寫入）。官方 MI_INDEX 的附註寫明成交股數「含一般、零股、盤後定價、鉅額交易」，因此推測差額是當天稍晚才納入的鉅額交易。這點沒有獨立驗證：WebSearch 額度已用完，也不能連證交所。
(d) 以 9/23 全市場比對 MI_INDEX：1,330 檔上市股中有 23 檔的量偏低（例如 6464：2,300 對 302,300；1109：61,788 對 572,788），範圍比 10 檔樣本更廣。

finding 有一處需要修正：2330 在 9/18 跨過 2.0 門檻，翻轉的是 signal_volume_surge（量比≥2.0，存檔為 false，官方量應為 true）。volume_price_surge 的進場結果並沒有被翻轉，因為同一天 signal_ma20_breakout=false，而且開盤等於收盤（2460），不是紅K。在 10 檔、9/14～9/23 的範圍內，2.0 門檻只翻轉這一列；boll_breakout 預設的 1.5 門檻沒有翻轉任何一列。

嚴重度維持 P2。影響集中在當天寫入的分區，以及上櫃母體缺漏，屬於明確的功能錯誤。
    - 證據：
      - 本地計算：`cd backend && uv run python verify-volgap/s1_compare.py` → 『enriched vs STOCK_DAY: matched 380, missing 0, vol/amt mismatches 16』，16 列都是負差（neg 16、pos 0），日期範圍 2026-09-16～2026-09-23。taiwan/daily 也是 16 列，數值相同
      - s1.out：2330 2026-09-18 官方 40,892,688、本地 37,190,511，差 −3,702,177（−9.053%）；6196 2026-09-17 官方 973,428、本地 808,428（−16.95%）；2454 2026-09-18 差 −434,469；2454 2026-09-23 官方 6,430,491、本地 6,425,491
      - s1.out 列數樞紐表：taiwan/daily 9/23 TWSE 1330、TPEX null（0）；9/22 TWSE 1331、TPEX 1009；9/16 TWSE 1330、TPEX null。enriched 9/23 TWSE 1323、TPEX 0；9/22 1328+988=2316；9/16 TWSE 1319、TPEX 0
      - s1.out：taiwan/daily 9/23 對 MI_INDEX 全市場：joined 1330、mismatches 23，全部偏低，例如 6464.TWSE 官方 302,300、本地 2,300；1109.TWSE 官方 572,788、本地 61,788
      - s2.out：2330.TWSE 2026-09-18 的 vol_ratio_5d 存檔 1.931707，官方量重算 2.109952；signal_volume_surge=false、signal_ma20_breakout=false、open=2460=close。10 檔中 2.0 門檻只翻轉這一列
      - s2.out：TPEx 官方 tradingStock 6488 在 9/16 為 6,666 張、8069 為 3,921 張，本地兩個存檔的 9/16 都沒有這兩檔（TPEx rows missing locally：6488/8069 的 9/16 與 9/23）
      - 官方附註：reference\raw\twse_stock_day_2330_202609.json 的 notes 為『當日統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。』；twse_mi_index_20260923.json 每日收盤行情的 notes 也寫『本統計資訊含一般、零股、盤後定價、鉅額交易』；tpex_trading_stock_6488_202609.json 的 notes 為『以上資料不含上櫃股票鉅額交易』
      - 分區修改時間（stat）：taiwan/daily 9/16 16:30、9/17 16:19、9/18 17:03、9/22 16:30、9/23 16:30，這些都有差異；9/21 在 9/22 10:55 寫入、9/10～9/15 在 9/16 11:21 寫入，這些完全一致
      - data/backend.log：『2026-09-23 16:30:05,341 [WARNING] ... TPEx snapshot fetch failed for 2026-09-23: peer closed connection without sending complete message body』，之後是『16:30:33 TaiwanDailyUpdateService finished: overall_status=success』與『16:30:54 strategy cache refreshed ... as_of=2026-09-23』
      - backend/app/taiwan/providers/snapshot_provider.py:112-124：TPEx 抓取失敗只記 warning，仍回傳只含 TWSE 的 frame
      - backend/app/taiwan/daily_refresh.py:150,167-170：`if cur in available:` 分區已存在就跳過，不會重抓，所以部分分區與盤後未定稿的量會永久保留
      - backend/app/repository.py:1416-1427 與 backend/app/services/screener.py:426：選股基準日＝TaiwanDailyStore().available_dates()[-1]，也就是只看分區是否存在
      - backend/app/indicators/pipeline.py:436,490（量比＝volume／前 5 日均量）、pipeline.py:738-743（turnover_rate 取自 volume）、backend/app/strategy/builtin/boll_breakout.py:59-61（量比門檻，預設 1.5）、volume_price_surge.py:62-67（量比≥2.0，且需同時突破 MA20 並收紅K）

### [P3 → P3] composite 選股合併與回測合併在單一候選與同分時的分數口徑不同，且選股結果受雜湊種子影響（確認成立）

- **主張**：merge_results 對只有 1 個候選的子策略給 1.0，merge_signal_matrices 給中性分 0.5；選股端同分時依 set 的迭代順序給不同名次，會隨 PYTHONHASHSEED 改變。
- **正確的定義或數值**：選股與回測使用相同的排名歸一口徑；同分應給相同名次（例如取平均名次）
- **app 實際行為**：分數口徑不同，而且選股排序不具決定性
- **對使用者的影響**：目前使用者沒有 composite 策略（data\user_data 中找不到），屬於潛在問題；一旦使用，選股排序與回測的持倉取捨會不一致
- **修法建議**：merge_results 在 count<=1 時給 0.5，並以平均名次處理同分；兩端共用同一個 rank 函式
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/composite.py:80-83 — sorted(set) 後 norm = 1-(rank-1)/max(count-1,1)，單一候選得 1.0
  - backend/app/strategy/composite.py:222-223 — 回測 n<=1 時給 _NEUTRAL_NORM
  - backend/app/strategy/composite.py:3-5,32 — 註解宣稱兩端共用口徑，單一候選應為中性分
- **線上證據**：
  - （無）
- **本地計算證據**：
  - step8_composite.py：A={X:55}、B={X:90,Y:80,Z:10} 以 union 合併，選股得 X=100、Y=50、Z=0，回測得 X=75、Y=50、Z=0
  - 4 檔同為 50 分時，5 個 PYTHONHASHSEED 產生 5 種不同的名次與分數
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：finding 的內容全部重現。merge_results 對只有 1 個候選的子策略給 1.0（composite.py:83），merge_signal_matrices 給中性分 0.5（composite.py:222-223），和模組註解宣稱的共用口徑矛盾。以 A={X:55}、B={X:90,Y:80,Z:10} 做 union 合併，選股得 X=100，回測得 X=75。這個差異也可能讓兩端排名反轉：同一組輸入下，X 在選股排第 1，在回測排第 4。

同分方面，選股端依 set 的迭代順序給名次，會隨 PYTHONHASHSEED 改變：seeds 0-4 得到 5 種結果，engine 截取前 N 名後的名單也會跟著變。finding 漏了一個面向：回測端雖然可重現，但同樣沒有處理同分，而是用 stable argsort 依資產欄位順序，把同分標的拉開成 100/66.67/33.33/0。所以「同分給相同名次」兩端都沒做到。

實際策略結果中同分很常見。例如 bullish_alignment 有 44/166 檔同為 0 分，若拿它當子策略，這些標的的歸一分數會在 0 到 26 分之間浮動。使用者目前沒有 composite 策略，屬潛在問題，維持 P3。
    - 理由：我呼叫 repo 裡真正的 merge_results、merge_signal_matrices 和 StrategyEngine._run_composite_strategy，數值與 finding 完全一致，所以判 confirmed。自建腳本放在 scratchpad\audit\work\verify-composite\。這條 finding 只涉及純程式邏輯，官方行情參考資料派不上用場；本地資料我讀了 strategy_cache.json，用來評估同分實際多常出現。

一、單一候選（finding 的數字 vs 我的數字）：
- A={X:55}、B={X:90,Y:80,Z:10}，union，權重 [1,1]。finding 寫選股 X=100、Y=50、Z=0，回測 X=75、Y=50、Z=0；我算出選股 100/50/0，回測 75/50/0，完全相同。
- 原因：composite.py:83 在 count=1 時算成 1-0/max(0,1)=1.0；composite.py:222-223 在 n<=1 時給 _NEUTRAL_NORM=0.5。這和 composite.py:3-5、32 的註解（兩端共用口徑、單一候選用中性分）矛盾。
- 只有 A 一個子策略時，選股 X=100、回測 X=50。
- 我另外找到一個排名反轉的例子：A 只有 X 一個候選，B 有 6 個候選。選股 X=100，排第 1（與 P 同分）；回測 X=50，排第 4，落在 Y=60 之後。這證實 user_impact 說的「選股排序和回測取捨不一致」真的會發生，不只是分數高低不同。

二、同分與雜湊種子：
- 4 檔同為 50 分，PYTHONHASHSEED=0~4 產生 5 種不同的分數對應，與 finding 的「5 個種子 5 種結果」相同；擴大到 0~9 共 7 種。
- 經過 engine 排序再截前 2 名後，10 個種子產生 5 種不同的前 2 名。原因是 engine.py:1352 的 sorted 也吃到 merged.scores 的插入順序，而這個順序來自 composite.py:105 迭代 universe 這個 set。
- 回測端 10 個種子結果都一樣，所以回測是可重現的。但它同樣沒有處理同分：composite.py:226 用 stable argsort，照資產欄位順序給 4 檔同分標的 100/66.67/33.33/0。finding 的 expected 寫「同分應給相同名次」，這一點兩端都沒做到；finding 的 actual 只提到選股端，漏了這個面向。

三、實際資料中同分常見：data\user_data\strategy_cache.json（as_of 2026-09-23）裡，bullish_alignment 有 166 檔中 44 檔、low_volatility_leader 有 135 檔中 69 檔、pullback_to_support 有 43 檔中 13 檔同為 0.0 分。若拿 bullish_alignment 當子策略，這 44 檔會落在第 123~166 名，歸一值介於 0 到 0.2606，也就是單一權重下同分標的最多相差約 26 分，完全取決於雜湊種子。

四、影響範圍：data\ 底下沒有 strategies\ 目錄，app\strategy\builtin 也沒有 composite 策略，所以使用者目前確實沒有 composite 策略，屬潛在問題，維持 P3。docs\code-review-2026-09-22.md 裡找不到 composite 相關記錄，不是重複回報。

稽核前後 git status 相同，只有原本就存在的 3 個改動，repo 沒有被寫入。
    - 證據：
      - backend/app/strategy/composite.py:80-83 — ordered = sorted(symbols, key=score, reverse=True)；norm = 1 - (rank-1)/max(count-1,1)，count=1 時得 1.0；symbols 是 set，同分時名次跟著 set 的迭代順序走
      - backend/app/strategy/composite.py:221-223 — n <= 1 時給 _NEUTRAL_NORM (0.5)；composite.py:32 註解寫『子策略無 score 或單候選(無法排名)時的占位』；composite.py:3-5 宣稱選股與回測共用同一套口徑
      - backend/app/strategy/composite.py:226 — 回測用 np.argsort(-vals, kind='stable')，同分依資產欄位順序給不同名次，沒有取平均名次
      - backend/app/strategy/composite.py:105 與 backend/app/strategy/engine.py:1352-1359 — merged.scores 依 universe（set）的順序插入，engine 再以 sorted 排序並依 _result_limit 截斷，同分標的的去留跟著雜湊種子變
      - 指令：uv run python verify-composite\single_candidate.py → case1 X: selection=100.0000 backtest=75.0000；Y: 50/50；Z: 0/0；case2（只有 A）X: selection=100 backtest=50；case3（權重 1:3）X: selection=100 backtest=87.5
      - 指令：uv run python verify-composite\order_flip.py → selection X=100（第 1 名）、Y=60；backtest X=50、Y=60，backtest order ['P','Q','Y','X',...]，X 在回測掉到第 4 名
      - 指令：uv run python verify-composite\ties_driver.py（每個 PYTHONHASHSEED 各開一個子程序）→ seeds 0-4 有 5 種選股分數對應；seeds 0-9 有 7 種；回測分數對應 1 種（固定為 2454=100、3034=66.67、2379=33.33、6196=0）；engine 截前 2 名有 5 種
      - 例如 seed 0 選股 6196.TWSE=100、2379.TWSE=0；seed 4 選股 2454.TWSE=100、2379.TWSE=0；seed 2 選股 2379.TWSE=100、6196.TWSE=0
      - data/user_data/strategy_cache.json（as_of 2026-09-23）：bullish_alignment 共 166 檔，其中 44 檔 score=0.0；low_volatility_leader 共 135 檔，其中 69 檔=0.0；pullback_to_support 共 43 檔，其中 13 檔=0.0。實際資料中同分很常見
      - backend/app/main.py:205-210 — composite 策略從 data_dir\strategies\composite 載入；data/strategies 不存在，app\strategy\builtin 也沒有 composite 策略，使用者目前沒有使用

### [P3 → P3] high_turnover_surge 的 5% 換手門檻沿用 A 股流通股慣例，台股以發行股數計算時只篩出極端的前 3%（確認成立）

- **主張**：A 股的換手率以流通股為分母，3%～7% 屬於「相對活躍」；台股以發行股數為分母，2026-09-23 的中位數只有 0.155%，5% 約在 p97。策略名稱「資金活躍」與實際篩出的極端值不相符。
- **正確的定義或數值**：門檻依台股（發行股數口徑）的分布校準，或改用相對指標（例如 turnover_z_60d）
- **app 實際行為**：沿用 A 股的絕對門檻
- **對使用者的影響**：只會篩出少數投機股，與「資金活躍」的直覺不符（自選 5 檔股票 9/23 的換手率為 0.19%～0.95%，都不可能入選）
- **修法建議**：預設門檻改為依台股分布設定（例如 p90 約 1.8%），或在說明中標明分母為發行股數
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/builtin/high_turnover_surge.py:17-27 — min_turnover 預設 5.0，描述為「換手率 > 5% 且漲幅 > 3%，資金活躍」
- **線上證據**：
  - https://baike.baidu.com/item/%E6%8D%A2%E6%89%8B%E7%8E%87/631895（搜尋摘要）— 「换手率=(成交股数/当时的流通股股数)×100%」；「3%~7%表示股票已经相对活跃」
  - https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=34f28c61-4b62-4a5a-bd9a-dd728d948d9a — 台股週轉率以「上市的總股數」為分母
- **本地計算證據**：
  - step9_misc.py：2026-09-23 共 1,087 檔股票的 turnover_rate（%）：p50 0.155、p75 0.48、p90 1.842、p99 11.525；>5% 的 36 檔（3.3%），>1% 的 167 檔；同時符合 >5% 與漲幅 >3% 的 18 檔
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：high_turnover_surge 的預設門檻 min_turnover=5.0 來自 A 股上游。A 股以流通股為分母時，3%～7% 屬於「相對活躍」；台股以已發行普通股為分母，同樣的 5% 大約落在 p96.5～p97，只篩出極端值。

2026-09-23 的數據：
- 上市 1,087 檔：中位數 0.155%，p90 約 1.84%～1.85%（依插值方法），換手率 >5% 的 36 檔（3.3%），同時漲幅 >3% 的 18 檔。
- 上市加上櫃共 1,981 檔（官方數據）：中位數 0.141%，p90 1.92%，換手率 >5% 的 70 檔（3.5%），同時漲幅 >3% 的 27 檔。

60 個交易日來看，每天換手率 >5% 的股票比例中位數是 2.8%（範圍 1.3%～5.5%），所以這不是單日偶然。

自選 5 檔股票 9/23 的換手率在 0.19%～0.95% 之間；8/3～9/23 期間最高只有 1.67%，預設參數下都不會入選。「資金活躍」的描述和實際篩出的極端值不相符。

補充：finding 原文的 1,087 檔只是上市股票，因為本地 9/23 分區沒有上櫃資料；但加入上櫃後結論不變。
    - 理由：finding 的數字全部重現。我用兩個互相獨立的來源各算一次：本地 enriched 9/23 分區，以及官方 9/23 行情（MI_INDEX 的成交股數除以 t187ap03_L 的已發行普通股數）。另外用 60 個交易日確認這不是單日偶然。

【finding 數字與我算出的數字並列】（本地是 kline_daily_enriched/date=2026-09-23 裡 instrument_type=stock 的 1,087 檔；官方是 TWSE 1,091 檔）
- 中位數：finding 0.155｜本地 0.1548｜官方 0.1549
- p75：finding 0.48｜本地 0.479｜官方 0.479
- p90：finding 1.842｜本地 1.8547（linear 插值）；改用 nearest／lower 插值是 1.8421，與 finding 相同｜官方 1.8421
- p99：finding 11.525｜本地 11.636（linear）；nearest 是 11.5252｜官方 11.604
- 換手率 >5%：finding 36 檔（3.3%）｜本地 36（3.31%）｜官方 36（3.30%）
- 換手率 >1%：finding 167｜本地 167｜官方 167
- >5% 且漲幅 >3%：finding 18｜本地 18｜官方 18
- 5% 在分布中的位置：本地 p96.69，官方 p96.70，符合「約 p97」。

p90 與 p99 的小差異只是分位數插值方法不同，不是數據錯誤。

【自選 5 檔股票，9/23 換手率】
官方與本地一致：2379 為 0.948%，2454 為 0.401%，3034 為 0.467%，3545 為 0.185%，6196 為 0.805%，與 finding 的「0.19%～0.95%」相符。官方 STOCK_DAY 從 2026-08-03 到 09-23 共 38 個交易日，這 5 檔的最高換手率是 6196 的 1.67%，沒有任何一天超過 5%。

【需要補充的範圍說明（不影響結論）】
1. finding 的 1,087 檔只是上市股票。本地 9/23 分區完全沒有 .TPEX 資料：共 1,323 列，全部是 TWSE，其中股票 1,087、ETF 236。9/22 分區則有 2,316 列（TPEX 988 列）。把上櫃也算進去後，官方全市場 1,981 檔的數字如下：
   - 中位數 0.141%，p90 1.918%；5% 落在 p96.47
   - 換手率 >5% 的有 70 檔（3.53%）
   - 同時符合兩個條件的有 27 檔
   - 其中上櫃 890 檔：中位數 0.129%，p90 2.09%，5% 落在 p96.18
   結論不變。
2. 用單日做判斷可能有偶然性，所以我用 taiwan/daily 最近 60 個交易日（7/01～9/23）的成交量，除以目前的已發行股數（近似）再算一次：
   - 每日換手率 >5% 的股票比例：中位數 2.76%，範圍 1.32%～5.49%
   - 每日 p90：中位數 1.80%
   - 全部合併後 p97 為 4.97%
   可見 5% 門檻長期大約都在 p97。
3. 「沿用 A 股」有佐證：這個檔案的第一版（git show fd79b63）是簡體中文，預設值 5.0 也從那時就存在。Baidu 百科（wap 版）寫 A 股以流通股為分母，3%～7% 屬於「相對活躍」。MoneyDJ 寫台股週轉率是「當日交易的總股數與上市的總股數之比」。

嚴重度維持 P3。這是參數校準與說明文字的問題，策略本身能跑，也沒有算錯。專案文件 docs/taiwan-share-capital-phase-6h.md:145-150 已經寫明分母是已發行股數，但沒有提到門檻要重新校準，所以這不是重複回報。

【範圍外的旁證，另案處理，不屬於本 finding】
本地 9/23 有 12 檔的成交量比官方 MI_INDEX 少，差額都是 1,000 的整數倍：
- 6464：本地 2,300 股，官方 302,300 股
- 2376：本地 2,629,451 股，官方 3,929,451 股
- 1109：本地 61,788 股，官方 572,788 股
推測本地資料缺了盤後定價或鉅額交易的成交量。這會讓換手率偏低，但只影響個別股票，不改變整體分布（>5% 的檔數相同）。
    - 證據：
      - backend/app/strategy/builtin/high_turnover_surge.py:18,25-31,67-70：description「換手率 > 5% 且漲幅 > 3%, 資金活躍」、min_turnover default 5.0；entry 條件是 turnover_rate > min_turnover（單位 %）
      - backend/app/volume_units.py:28-30：turnover_rate = 成交股數 × 100 / float_shares；backend/app/indicators/pipeline.py:122 的欄位說明是「台股分母為已發行普通股數, 非流通股數」
      - git show fd79b63:backend/app/strategy/builtin/high_turnover_surge.py：第一版就是簡體中文（「换手率 > 5% 且涨幅 > 3%, 资金活跃」），default 5.0，來自 A 股上游
      - 本地重現腳本 AUDIT_SCRATCH/work/verify-hts/s3_reproduce.py（指令：cd backend && uv run python <腳本>）。輸出 [LOCAL enriched 9/23 TWSE stocks]：n=1087，p50 0.1548，p75 0.479，p90 1.8547，p97 5.5738，p99 11.6359；>5% 36 檔（3.31%）；>1% 167 檔；5% 位於 p96.69；>5% 且 change_pct>0.03 共 18 檔
      - 同一腳本的官方數據輸出 [OFFICIAL TWSE 9/23]（twse_mi_index_20260923_quotes.csv 的成交股數 ÷ twse_t187ap03_L_share_capital.csv 的 issued_common_shares）：n=1091，p50 0.1549，p90 1.8421，p99 11.6043；>5% 36；>1% 167；兩個條件都符合 18
      - 同一腳本 [OFFICIAL TWSE+TPEx 9/23]：n=1981，p50 0.1407，p90 1.9183，p97 5.7496；>5% 70 檔（3.53%）；兩個條件都符合 27。[OFFICIAL TPEx]：n=890，p50 0.1293，p90 2.0918；>5% 34；兩個條件都符合 9
      - s4_multiday.py 的插值比較：nearest/lower 的 p90 為 1.8421、p99 為 11.5252，與 finding 相同；linear 為 1.8547／11.6359
      - s4_multiday.py 的 60 日結果（taiwan/daily 7/01～9/23 的成交量 ÷ share_capital.parquet 的 issued_shares）：每日 >5% 比例中位數 2.76%（範圍 1.32～5.49%），每日 p90 中位數 1.80%，全部合併後 p97 為 4.97%，n=116,350
      - 自選股：官方 9/23 換手率 2379 0.948%、2454 0.401%、3034 0.467%、3545 0.185%、6196 0.805%，本地值相同（2454 本地為 0.400%）。twse_stock_day_202608_202609.csv 的 38 個交易日中，最高是 6196 的 1.67%，超過 5% 的天數為 0
      - 本地 9/23 分區的組成：1,323 列全部是 TWSE（stock 1,087、etf 236），沒有 .TPEX；9/22 分區有 2,316 列（TPEX 988）
      - https://wapbaike.baidu.com/item/%E6%8D%A2%E6%89%8B%E7%8E%87：「成交股數/流通股數×100%（按流通股本口徑）」；「3%~7%：該股進入相對活跃状态」；「3%就成为一种分界」。桌面版 baike.baidu.com 回 403
      - https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=34f28c61-4b62-4a5a-bd9a-dd728d948d9a：週轉率是「當日交易的總股數與上市的總股數之比」，頁面沒有數值門檻
      - docs/taiwan-share-capital-phase-6h.md:145-152：已記錄分母是已發行股數、歷史換手率不可用，但沒有討論策略門檻校準，所以這個 finding 不是重複回報

### [P3 → P3] annual_vol_20d 以 252 日年化，台股實際每年約 242～243 個交易日（確認成立）

- **主張**：低波動龍頭的 30% 門檻以 √252 年化；台股 2024 年有 242 個、2025 年有 243 個交易日，波動率因此高估約 1.8%。
- **正確的定義或數值**：年化天數符合台股交易日，或文件註明採用 252
- **app 實際行為**：固定 252
- **對使用者的影響**：輕微，邊界標的可能被排除
- **修法建議**：台股改用 √242（或依實際交易日曆），或在欄位說明中註明
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/matrix.py:3919 — * np.float32(252 ** 0.5)
  - backend/app/indicators/pipeline.py:524
- **線上證據**：
  - （無）
- **本地計算證據**：
  - step2_coverage.py：enriched 分區數 2024=242、2025=243；√(252/243)=1.0183，30% 門檻相當於 243 日口徑下的 29.46%
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：annual_vol_20d 的年化固定用 √252，涵蓋策略矩陣 matrix.py:3919、收盤後 pipeline.py:524、盤中 pipeline.py:1619 三處。台股實際交易日數：2024 年 242 天、2025 年 243 天（本地分區與 Yahoo ^TWII 一致）。因此以 243 日口徑計，波動率高估 1.835%（2024 年以 242 日計為 2.045%）；低波動龍頭的 30% 門檻相當於 243 日口徑的 29.46%（242 日口徑的 29.40%）。在「20 日動量>0 且收盤>MA20」的前提下，全市場約 1.8%、自選 9 檔約 3.0% 的 243 日口徑進場日會被排除，影響輕微。另外，盤中 compute_enriched_today 用母體變異數（ddof=0），收盤後的已存值用 ddof=1，兩者依公式推算相差約 2.5%，這個差距比 252 與 243 的差距還大（僅由程式碼推得，未實跑）。
    - 理由：finding 的每個數字都重現了。

(1) 程式碼：策略用的矩陣路徑 matrix.py:3912-3919 是日報酬 20 日標準差（ddof=1）×√252。低波動龍頭的 low_volatility_leader.py:73 用 `annual_vol_20d < vol_max`，預設 0.30（:35）。收盤後的 pipeline.py:522-525 也乘 √252。
(2) 交易日數：本地 kline_daily_enriched 和 taiwan/daily 的非空分區，2024 年是 242 個（01-02～12-31），2025 年是 243 個（01-02～12-31），沒有空分區。我另外用 Yahoo ^TWII 日K 獨立核對（非交易所來源，只發 1 次請求），2024=242、2025=243，日期集合與本地完全相同，兩邊都沒有多出的日期。本地 2026-08/09 的日期也和官方參考資料 STOCK_DAY 一致（21＋17 天）。
(3) 已存資料的口徑：1,376,716 列已存的 annual_vol_20d，除以我重算的日報酬標準差（ddof=1），比值的最小值和最大值都是 15.874508，也就是 √252。偏離超過 0.1% 的列數是 0，所以已存資料確實固定用 252。
(4) 數值：√(252/243)=1.018350，高估 1.835%；30% 門檻換到 243 日口徑是 29.459%。finding 寫 1.0183、約 1.8%、29.46%，全部吻合。2024 年用 242 日算，高估 2.045%，門檻相當於 29.399%，finding 沒寫這個年度。
(5) 影響：這是我用與 matrix 相同的公式（ddof=1、√N）自己組 DataFrame 模擬的結果，不是真的跑策略回測。全市場加上「20 日動量>0、收盤>MA20」後，243 日口徑有 252,474 個進場的股票日，252 日口徑有 248,011 個，只因 252 被排除的有 4,463 個（1.77%）。自選 9 檔是 1,760 對 1,708，被排除 52 個（2.96%），9 檔每檔都有。所以「影響輕微，邊界標的會被排除」屬實，P3 合理：252 是業界常用慣例，30% 門檻本身也是人為設定，談不上錯誤的金融口徑。

finding 沒提到的面向：
(a) 盤中路徑 pipeline.py:1619（compute_enriched_today，:1415 起）同樣乘 √252。
(b) 同一個函式的變異數是 `total_sq_sum/20 - mean²`（pipeline.py:1614-1615），這是母體變異數（ddof=0）；收盤後的已存值是 ddof=1。依公式推算，盤中值會比收盤值低約 2.53%（√(19/20)），而且方向與 252 的偏差相反。這一點我只從程式碼推出，沒有實跑 compute_enriched_today。docs/code-review-2026-09-22.md 沒有記錄。
(c) 年化 Sharpe、Sortino、年化報酬也寫死 252：engine.py:2808/2924/3057/3145/3149、factor.py:1186/1274、strategy.py:1707。
    - 證據：
      - backend/app/backtest/matrix.py:3912-3919 — annual_vol_20d = valid_rolling_std(daily, ..., 20, ddof=1) * np.float32(252 ** 0.5)
      - backend/app/strategy/builtin/low_volatility_leader.py:73 — entry &= matrix_feature(market, "annual_vol_20d") < float(params.get("vol_max", 0.30))；:35 default 0.30
      - backend/app/indicators/pipeline.py:522-525 — rolling_std(20) * (252 ** 0.5)（收盤後 enriched）
      - backend/app/indicators/pipeline.py:1612-1619 — 盤中 compute_enriched_today：vol_var = total_sq_sum/20 - vol_mean**2（母體變異數，ddof=0）× √252（finding 未列）
      - 本地計算 AUDIT_SCRATCH/work/verify-ann252td/repro.py（輸出 out.txt）：kline_daily_enriched 2024: 242 個非空分區（2024-01-02～2024-12-31）、2025: 243 個（2025-01-02～2025-12-31），空分區 []；taiwan/daily 相同
      - 同一腳本：本地 2026-08/09 日期與官方 csv/twse_stock_day_202608_202609.csv（2330）比對，官方 38 天、本地 38 天，identical: True
      - 線上來源 https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII?period1=1704038400&period2=1767196800&interval=1d（HTTP 200，原始回應存於 verify-ann252td\yahoo_twii_2024_2025.json）→ yahoo ^TWII days per year: {'2024': 242, '2025': 243}；only in yahoo: []、only in local: []
      - 縮放係數：N=243 → sqrt(252/N)=1.018350，高估 1.835%，30% 門檻相當於 29.459%；N=242 → 1.020452，高估 2.045%，相當於 29.399%（finding：1.0183／約 1.8%／29.46%）
      - 已存值口徑：1,376,716 列 stored annual_vol_20d / std_ddof1 的最小值 15.874507866387543、最大值 15.874507866387546（√252=15.8745），偏離超過 0.1% 的列數 0
      - 影響（全市場，加上 mom20>0 且 close>MA20）：entry252=248011、entry243=252474，只因 252 被排除的 band=4463（佔 243 口徑進場數的 1.768%）；自選 9 檔：entry252=1708、entry243=1760，band=52（2.955%），每檔 3～10 列
      - 其他寫死 252 的年化位置（finding 未列）：backend\app\backtest\engine.py:2808,2924,3057,3145,3149；backend\app\backtest\factor.py:1186,1274；backend\app\backtest\strategy.py:1707

### [P3 → P3] ema{N}_bias 在矩陣路徑從矩陣起點重新計算 EMA，忽略已載入的 emaN 欄位，結果隨窗長度漂移（確認成立）

- **主張**：scoring 的依賴會載入 ema60，但 matrix_feature 的 ema60_bias 以矩陣內的 close 重新計算 EMA，起始值是窗口第一根。選股窗很短時與 enriched／因子研究的值差異明顯。
- **正確的定義或數值**：矩陣路徑在已載入 emaN 時直接使用，或保證足夠的暖機期
- **app 實際行為**：短窗下 ema60_bias 約有 20% 的相對誤差
- **對使用者的影響**：只影響以 ema30／60_bias 為因子的挖掘策略或自訂評分；目前的內建策略不受影響
- **修法建議**：matrix_feature 在 market.fields 含有 emaN 時直接用它計算 bias；或讓 scoring_warmup_bars 對 emaN_bias 回傳約 3N
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/matrix.py:3835-3837 — _matrix_relative(close, _matrix_ema(close, ...))
  - backend/app/strategy/scoring.py:20-23,122-125 — polars 路徑使用 emaN 欄位
  - backend/app/backtest/factor.py:49-53 — ema5～60_bias 是可挖掘的因子
- **線上證據**：
  - （無）
- **本地計算證據**：
  - step13_scoring.py：61 根窗的 ema60_bias 與 enriched ema60 相比，絕對差中位數 0.00751、p90 0.0236、最大 0.0556（|bias| 中位數 0.0384），156 檔中有 7 檔正負號相反；ema30 的中位數差 0.00059；ema20 為 0.00007
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：矩陣路徑的 matrix_feature 只在 'ema{N}_bias' 本身是欄位時才直接取用。因此即使選股路徑已把 ema60 載入 market.fields，ema60_bias 仍然以矩陣內的 close 從窗口第一根重新計算 adjust=False EMA。

挖掘發布的因子排名策略單獨執行時，實際窗長是 62 根（lookback 61 加 1），不是 61 根。在這個窗長下，全市場 1323 檔的 ema60_bias 與 enriched（全歷史 EMA）相比：
- 絕對差中位數約 0.0064～0.0068、p90 約 0.022～0.024、最大約 0.16
- 相對誤差中位數約 17～19%
- 約 87～88 檔（6.6%）正負號相反
- ema30_bias 的絕對差中位數約 0.00056；ema20_bias 約 0.00006，可忽略

run_all 的窗長取所有策略的最大值（約 122 根），此時 ema60_bias 絕對差中位數降到約 0.0007。所以同一支策略在單獨執行與 run_all 之間的因子值不一致。

回測路徑不會把 ema60 以欄位載入矩陣，起點只有 60 根暖機，前段誤差量級相近，之後逐漸衰減。因此修正應以暖機（約 3N 根）為主，「有欄位就直接用」只能修到選股路徑。

目前沒有任何內建策略、自訂策略或已發布的挖掘策略使用 ema*_bias（data/strategies 不存在），對這位使用者的實際影響為 0，維持 P3。
    - 理由：我重新推了一次程式碼路徑，也用本地 parquet 和 app 的純計算函式重算。finding 的核心機制和數值量級都能重現。

程式碼路徑：
- matrix_feature 只在 name 本身（"ema60_bias"）存在於 market.fields 時才直接取欄位。其他情況一律走 _matrix_relative(close, _matrix_ema(close,…))，也就是從矩陣第一根 close 開始算 adjust=False 的 EMA（matrix.py:3782-3784、3835-3837、4081-4082）。
- 選股路徑的 _matrix_field_columns 會用 scoring_dependencies 把 ema60 載入 market.fields（engine.py:1108-1147；scoring.py:20-23）。實測 market.fields 確實含有 ema60，但 ema60_bias 沒有使用它。
- 挖掘發布的策略由 FactorRankResearchMatrixStrategy 產生，它的 required_warmup_bars 是 60（factor_rank_research.py:92-94）。scoring_warmup_bars 對 emaN_bias 回傳 1（scoring.py:52-65、93-97）。因此單獨執行時，history_bars 為 61。
- repo 快取以 trading_dates[-(lookback_days+1)] 裁切（repository.py:1184-1189），實際窗長是 62 根，不是 61 根。

數值重現（2026-09-23，1323 檔全市場）：
- 61 根窗：ema60_bias 絕對差中位數 0.00675、p90 0.02438、max 0.160，相對誤差中位數 19.2%，正負號相反 88 檔。
- 62 根窗：中位數 0.00643、p90 0.0218，相對誤差 17.2%。
- ema30：絕對差中位數 0.00056；ema20：0.00006。
- finding 的對應數字是 0.00751／0.0236／0.0556、7/156、0.00059、0.00007。中位數與 p90 吻合；max 與正負號相反數不同，原因是 finding 只用了 156 檔的子樣本。

窗長漂移也確認成立：窗長 91／121／181／250 根時，絕對差中位數依序為 0.00322／0.00066／0.00006／0.00001。run_all 會對全部策略取 history_bars 的最大值；lorentzian_knn 預設需要 120+1 根，窗長因此約 122 根，此時中位數差 0.00071。同一個挖掘發布策略在單獨執行（62 根）與 run_all（約 122 根）下會得到不同的 ema60_bias。以單因子排名看，62 根窗的 top20 重疊 19/20，bottom20 只有 14/20。

資料來源檢查：
- 本地儲存的 ema60 與「以全部歷史 close 重算的 adjust=False EMA」完全一致（相對差 0）。
- 本地 raw_close 與官方 TWSE STOCK_DAY 380 筆逐筆比對，最大差 0.0。

使用者影響：
- 內建策略沒有任何一支使用 ema*_bias；grep 只在 factor.py 命中。
- data/strategies 目錄不存在，也就是沒有已發布的挖掘策略或自訂策略；strategy_overrides 為空。
- 因此對目前的使用者實際影響為 0，維持 P3。

補充面向（依程式碼推論，未實跑）：回測的 matrix_native 路徑中，_resolve_base_columns 只保留 ENRICHED_STORAGE_COLS，ema60 不會以欄位載入矩陣（strategy.py:239-262、501-515；pipeline.py:92-101）。所以 finding 建議的「有 emaN 欄位就直接用」修不到回測。回測起點只有 60 根暖機，前段誤差量級與選股相同，之後逐漸衰減。修正應優先採用暖機方案（例如約 3N 根）。
    - 證據：
      - backend/app/backtest/matrix.py:3782-3784：matrix_feature 只在 name（例如 'ema60_bias'）本身存在於 market.fields 時直接取欄位
      - backend/app/backtest/matrix.py:3835-3837、4081-4082：ema{N}_bias = _matrix_relative(close, valid_ewm_adjust_false(close, alpha=2/(N+1)))，從矩陣起點開始算
      - backend/app/strategy/engine.py:1108-1147：_matrix_field_columns 透過 required_fields_for_params 與 scoring_dependencies 把 ema60 載入矩陣欄位
      - backend/app/strategy/builtin/factor_rank_research.py:92-94：required_warmup_bars 回傳 60；scoring.py:52-65、93-97：emaN_bias 的 warmup 為 1
      - backend/app/repository.py:1184-1189：以 trading_dates[-(lookback_days+1)] 裁切，lookback 61 時實際窗長為 62 根
      - 腳本 AUDIT_SCRATCH/work/verify-emabias-matrix/repro.py（輸出在同目錄 out.txt），指令：cd backend && uv run python repro.py
      - out.txt：market.fields keys 含 ema60；close/欄位 ema60-1 與 enriched bias 的最大差 1.5e-07；matrix ema60_bias 與 close/欄位 ema60-1 的最大差 0.168，證明欄位已載入但沒被使用
      - out.txt，61 根窗、1323 檔：ema60_bias 絕對差中位數 0.00675／p90 0.02438／max 0.15998，|bias| 中位數 0.03650，相對誤差中位數 0.192，正負號相反 88 檔；ema30 中位數 0.00056；ema20 中位數 0.00006（finding：0.00751／0.0236／0.0556、7/156、0.00059、0.00007）
      - out.txt，62 根窗：ema60_bias 絕對差中位數 0.00643／p90 0.02179，相對誤差 0.172，正負號相反 87 檔
      - out.txt，窗長漂移：91 根 0.00322、121 根 0.00066、181 根 0.00006、250 根 0.00001（ema60_bias 絕對差中位數）
      - out.txt 資料檢查：本地儲存的 ema20／30／60 與全歷史重算完全一致（相對差 0）
      - extra.out：官方 TWSE STOCK_DAY 380 筆全部對上，max |raw_close - 官方收盤價| = 0.0；單因子 ema60_bias 排名在 62 根窗 top20 重疊 19/20、bottom20 14/20；122 根窗 top20 19/20、bottom20 19/20
      - backend/app/api/screener.py:600-606：run_all 以全部策略 ID 建立 context，窗長取各策略最大值（lorentzian_knn.py:127 為 norm_window 60 + 60）
      - 使用者影響：grep 'ema[0-9]*_bias' 在 backend/app 只命中 factor.py；data/strategies 不存在；data/user_data/strategy_overrides 為空
      - backend/app/backtest/strategy.py:239-262、501-515：回測 matrix_native 路徑只載入儲存欄位，ema60 不在其中（依程式碼推論，未實跑）
      - git status 與 session 開始時相同，沒有修改 repo

### [P1 → P1] 台股 enriched 價格完全未還原除權息，動能與均線類策略在除權息日產生假訊號（部分成立）

- **主張**：CONTRIBUTING §3.2 規定 enriched OHLC 為前復權價，但台股 enriched 的 close 在所有列都等於 raw_close。change_pct、prev_close、MA20、動能、布林、RSI 與 lorentzian 標籤都以未還原價計算，除權息日被當成下跌。
- **正確的定義或數值**：技術指標、報酬與訊號應使用前復權價（或以參考價為基準的漲跌），除權息日不應被視為價格下跌
- **app 實際行為**：全部使用原始價：除權息日 change_pct 被高估為下跌，MA20 下穿、動能與 KNN 標籤都受影響
- **對使用者的影響**：自選的 00878（季配）、0050、2379、6196、3545、2454、3034 在每次除權息日都可能收到假的跌破出場訊號，或讓進場條件失效；回測績效與選股都會出現系統性偏差
- **修法建議**：在台股 enriched 計算前，以 TWT49U 等除權息事件建立復權因子（或至少以參考價計算 prev_close 與 change_pct），並補上除權息日的單位測試；此項可能與價格／復權領域的稽核重疊，請合併處理
- **稽核者信心**：confirmed
- **程式碼證據**：
  - CONTRIBUTING.md:99-102 — 「enriched 的 open/high/low/close 為前復權價格」
  - backend/app/strategy/builtin/volume_price_surge.py:60 — breakdown=(close<ma20)&(prev_close>=prev_ma20)（high_turnover_surge.py:76、strong_open.py:84、low_volatility_leader.py:76 用同一出場條件）
  - backend/app/strategy/builtin/lorentzian_knn.py:144-145 — 標籤用未還原的 close[t+4] 與 close[t]
- **線上證據**：
  - TWSE TWT49U 參考檔（官方除權息計算結果）：例如 2379 在 2026-09-08 除息前收盤 729、參考價 704；00878 在 2026-08-18 除息前 33.75、參考價 32.74（reference\csv\twse_twt49u_20260601_20260923.csv，於 2026-09-24 由參考資料 agent 下載）
- **本地計算證據**：
  - step10_adj.py：rows with raw_close: 1,423,597；close != raw_close: 0
  - step10_adj.py：2026-06-01～09-23 的 956 次除權息，app 的 prev_close 全部等於除息前的原始收盤（956／956）；change_pct 與交易所基準（close/參考價−1）的絕對誤差中位數 2.82%、p90 6.43%、最大 66.3%；|誤差|>3% 的有 457 次
  - 2379 在 2026-09-08：app 的 change_pct −5.075%，交易所基準 −1.705%；00878 在 2026-08-18：−4.06% 對 −1.10%；3545 在 2026-08-04：−0.40% 對 +1.26%
  - step11_adj_impact.py：除權息日的 MA20 下穿出場，未還原價有 295 次、還原價只有 74 次，其中 221 次是假出場；strong_open 進場翻轉 16 次
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：台股 enriched 完全沒有還原除權息：enriched_refresh.py:95 傳入空的 factors，provider 也宣告 adj_factor=False，所以 close 在所有列都等於 raw_close（1,423,597／1,423,597）。這違反 CONTRIBUTING §3.2「enriched OHLC 為前復權價」。prev_close、change_pct、MA／布林／動能／RSI、MA20 下穿出場，以及 lorentzian 的 4 日標籤（lorentzian_knn.py:141-143）都以未還原價計算，除權息日被當成下跌：956 次事件的 change_pct 誤差中位數 2.82%，最大 66.3%。同一根因也讓除權息日的台股漲跌停訊號以未還原的昨收為基準（enriched_refresh.py:176），6/1～9/23 出現 4 次假跌停。在使用者情境下（監控規則 0 條），影響出現在選股、策略、回測結果，以及除權息日的漲跌幅顯示，不會觸發告警。6/1～9/23 自選股 7 次除權息中，實際出現假 MA20 下穿的只有 00878（2026-08-18）；其他檔並不是每次都會出現假訊號。全市場「221 次假出場」的數字本次未重現。
    - 理由：主張成立，嚴重度 P1 恰當。我親自讀了程式碼：台股 enriched 由 TaiwanEnrichedRefreshService 產生，它呼叫 compute_enriched 時傳的是空的 factors（enriched_refresh.py:95）。台股 provider 也宣告不提供復權因子（hybrid_provider.py:31 adj_factor=False；:144-152 get_adj_factors 回傳空表，docstring 寫「Phase 2 records raw price semantics」）。因此 pipeline.py:1023 的前復權分支永遠不會執行，close 就等於 raw_close。這和 CONTRIBUTING.md:99「enriched 的 open/high/low/close 為前復權價格」直接衝突。

prev_close、ma20、布林、動能與 change_pct 都以 close 計算（pipeline.py:388、397、417、494-504）。MA20 下穿出場的條件在四支策略中都找到了，行號與 finding 相符。lorentzian 的標籤實際在 lorentzian_knn.py:141-143（finding 寫 144-145，偏移 1–2 行），內容是 sign(close[t+4]-close[t])。

本地數據逐項重現：1,423,597 列中 close≠raw_close 的列數為 0；956 次除權息全部用除息前的原始收盤當 prev_close；change_pct 與交易所基準的絕對誤差中位數 2.82%、p90 6.43%、最大 66.3%，誤差大於 3% 的有 457 次。2379、00878、3545 三檔的數值也完全一致。

「正確定義」的依據：Yahoo 對 adjusted close 的定義是回溯調整除權息日之前的價格（也就是前復權口徑）；證交所 STOCK_DAY（與 TWT49U 不同的官方資料集）在除權息日的漲跌欄標 X，意思是「不比價」，也就是官方不拿除息日收盤和前一日收盤相比；維基百科列出除息參考價＝前一日收盤－現金股利。另外，專案自己的 A 股管線在除權息日會改用前復權昨收當參考價（pipeline.py:749-762），可見專案知道正確規則，只是台股路徑缺少復權因子。

需要修正的地方：
(1) 使用者影響說得過頭。我用 TWT49U 的因子重算自選股在 6/1～9/23 的 7 次除權息，只有 00878 在 2026-08-18 真的出現假的 MA20 下穿（原始 MA20 32.715，還原後 31.785，收盤 32.38）。其餘 6 次不論是否還原，結果都一樣。另外監控規則是 0 條，所以不會產生告警，影響只出現在選股、策略和回測，以及除權息日顯示的漲跌幅。
(2) 我沒有重現全市場「221 次假出場」和「strong_open 翻轉 16 次」這兩個數字。
(3) 找到一個 finding 沒提到、但根因相同的新面向：台股漲跌停訊號 _add_limit_signals（enriched_refresh.py:176）用未還原的 prev_close 當基準，除權息日不是以參考價計算。6/1～9/23 因此出現 4 次假跌停：5906 在 09-10、6924 在 09-15、1442 和 2542 在 09-23。以 5906 為例，官方跌停價 35.45，app 算出約 37.40，收盤 37.35 被判成跌停。這個面向和已知的 B5、N5 都不同。
(4) 附帶發現：2026-07-10 這天，在 taiwan/daily 和 kline_daily_enriched 都沒有分區。但 TWT49U 在這天列了 10 筆除權息，所以這天應該是交易日，3034 的除息日因此沒有資料。這是另一個問題，不屬於本 finding。

程序說明：讀一個參考 JSON 的 notes 時，我誤用了一次系統 python（唯讀，沒有寫入）；其餘計算都用 uv run python。WebSearch 額度已用完，Investopedia 抓不到，所以改用 WebFetch 直接抓 Yahoo 說明頁和維基百科。
    - 證據：
      - CONTRIBUTING.md:99 — 「enriched 的 `open/high/low/close` 為前復權價格。」；:100 raw_* 為不復權
      - backend/app/taiwan/enriched_refresh.py:95 — enriched = compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)
      - backend/app/taiwan/providers/hybrid_provider.py:31 adj_factor=False；:144-152 get_adj_factors 回傳 pl.DataFrame()，docstring「Phase 2 records raw price semantics」
      - backend/app/indicators/pipeline.py:1014-1024 — 先把 raw_close 設成 close，只有在 factors 不是空表時才執行 _apply_adj_factor
      - backend/app/indicators/pipeline.py:388 prev_close=close.shift(1)；:397 ma20；:417 _boll_std；:494-502 momentum；:504 change_pct=close/close.shift(1)-1
      - backend/app/indicators/pipeline.py:748-762 — A 股路徑在 adj_factor 變化（XD/DR）時改用前復權昨收當交易所參考價，證明專案知道正確規則
      - backend/app/strategy/builtin/volume_price_surge.py:60；high_turnover_surge.py:76；strong_open.py:84；low_volatility_leader.py:76 — (close<ma20)&(shift(close)>=shift(ma20)) 出場
      - backend/app/strategy/builtin/lorentzian_knn.py:141-143 — future[:-horizon]=close[horizon:]；labels=np.sign(future-close)（finding 寫 144-145，偏移 1–2 行）
      - backend/app/taiwan/enriched_refresh.py:176-194 — _add_limit_signals 以 ref=pl.col("prev_close")（未還原）計算漲跌停價
      - 本地 verify_adj_defcode\check.py（uv run python）：partitions 661、rows 1423597、close!=raw_close 0；events matched 956 of 994，prev_close==pre_ex_close 956；abs err median 0.02824、p90 0.06426、max 0.66348、>3% 457
      - check.py 輸出：00878 2026-08-18 close 32.38、prev_close 33.75、change_pct -0.040593，交易所基準 -0.010996；2379 2026-09-08 change_pct -0.050754 對 -0.017045；3545 2026-08-04 -0.003992 對 +0.012581
      - 本地 ma20.py（以 TWT49U 因子 k=參考價/除息前收盤回溯調整）：自選股 7 次除權息中，只有 00878 2026-08-18 的原始 MA20 下穿為 True（ma20 32.715），還原後為 False（ma20 31.785）；6196、2454、0050、3545、2379 還原前後都沒有下穿；3034 在 2026-07-10 沒有資料
      - 本地 limits.py：6/1～9/23 除權息日有 4 次假跌停（signal_limit_down=True，但收盤高於官方跌停價），例如 5906.TWSE 2026-09-10 收盤 37.35、官方跌停 35.45；1442.TWSE 2026-09-23 收盤 27.8、官方跌停 25.9
      - 官方 STOCK_DAY 參考檔（與 TWT49U 不同的資料集）reference\raw\twse_stock_day_00878_202608.json notes：「符號說明:+/-/X表示漲/跌/不比價」；reference\csv\twse_stock_day_202608_202609.csv：00878 2026-08-18 change_raw=X0.00、2379 2026-09-08 X0.00、3545 2026-08-04 X0.00
      - https://help.yahoo.com/kb/SLN28256.html — 「Adjusted close is the closing price after adjustments for all applicable splits and dividend distributions.」；除息乘數為 1−股利/收盤，只回溯套用到除權息日之前的價格（即前復權口徑）
      - https://zh.wikipedia.org/zh-tw/除息 — 「除息參考價＝除息前一日收盤價－現金股利」
      - docs/code-review-2026-09-22.md — grep「復權／除權／除息／raw_close／adjust」零命中，本問題未曾記錄
      - 附帶：data\kline_daily_enriched 與 data\taiwan\daily 都沒有 date=2026-07-10 分區，但 TWT49U 在 2026-07-10 列有 10 筆除權息（grep -c 輸出 10）
  - **reproduction**：partially_confirmed；修正後嚴重度 P1
    - 修正後敘述：CONTRIBUTING §3.2 規定 enriched OHLC 為前復權價，但台股 enriched 刷新時傳入空的 factors（enriched_refresh.py:94），1,423,597 列的 close 全部等於 raw_close。所有技術指標、訊號與回測矩陣都以未還原價計算。2026-06-01～09-23 的 956 次上市除權息中，prev_close 全部等於除息前原始收盤，change_pct 與交易所基準（close/參考價−1）的絕對誤差中位數 2.82 個百分點、p90 6.43、最大 66.3，超過 3 個百分點的 457 次，有 373 次 app 顯示下跌但交易所基準為平盤或上漲。除權息日的 MA20 下穿出場，原始價 296 次、還原價 74 次，其中 222 次是假出場。strong_open 在除權息日有 5 次應進場卻被漏掉（原稿的 16 次來自比較時尺度錯位的腳本 bug）。跨除權息日的 4 日 KNN 標籤有 1,092/3,840 翻轉，幾乎都由漲翻跌。自選股在參考窗口內只有 00878（2026-08-18）實際觸發假 MA20 出場，其他自選股受影響的是 change_pct 與均線數值偏移，例如 2379 在 09-08 app −5.08% 對交易所 −1.71%。同一根因還讓股票分割未被還原：自選 0050 在 2025-06-18 的 1 拆 4 被記成 −74.8% 的單日跌幅，全歷史 change_pct < −40% 的共 28 列，回測區間跨過這些日子會出現假崩跌。
    - 理由：核心主張可以完整重現，數字幾乎逐位相同：台股 enriched 完全沒有還原除權息。TaiwanEnrichedRefreshService.refresh 呼叫 compute_enriched 時傳入空的 factors（pl.DataFrame()），所以 _apply_adj_factor 不會執行，close 就等於 raw_close。change_pct、prev_close、MA20 都以原始價計算，除權息日被當成下跌。CONTRIBUTING §3.2 明文規定 enriched 的 OHLC 為前復權價，這是金融口徑錯誤，維持 P1。

我自己寫腳本重算，與 finding 並列如下：
(1) close != raw_close：我算出 0／1,423,597，finding 為 0／1,423,597，一致。
(2) TWT49U 共 994 筆，其中 956 筆對得到 enriched 的除權息日列（finding 為 956）。這 956 列的 prev_close 全部等於除息前原始收盤（956/956）。change_pct 也全部恰好等於 close/除息前收盤−1。
(3) change_pct 與交易所基準（close/參考價−1）的絕對誤差：中位數 2.824 個百分點（finding 2.82%）、p90 6.425（finding 6.43%）、最大 66.35（finding 66.3%），超過 3 個百分點的 457 筆（finding 457）。以上全部重現。
(4) 範例全部重現。2379 在 2026-09-08：app −5.075%，交易所基準 −1.705%。00878 在 2026-08-18：−4.059% 對 −1.100%。3545 在 2026-08-04：−0.399% 對 +1.258%。官方 STOCK_DAY 在這幾天的漲跌欄標為「X0.00」（除權息），收盤 692、32.38、49.9 與 app 一致。
(5) 除權息日的 MA20 下穿：原始價 296 次、還原價 74 次、假出場 222 次；finding 為 295／74／221。差 1 是原稿跳過 k<21 造成的，實質一致。儲存的 signal_ma20_breakdown 在除權息日也是 296 次，與原始價重算相同。

無法重現的部分：finding 說「strong_open 進場翻轉 16 次」。我讓同一列使用一致的價格尺度重算，只有 5 次，而且全部是原始價漏掉、還原價才會進場的情況：1709 在 08-28、2546 在 08-10、6951 在 07-13、7610 在 07-13、8039 在 06-30。原稿 A09/step11_adj_impact.py 第 41–42 行拿未調整的 o[k]、c[k] 去和已乘上後續事件因子的 ca[k-1] 比較，窗口內有第二次除權息的標的因此尺度錯位。照原稿的混合尺度寫法重算，確實得到 16，其中 11 次來自有後續事件的列。所以 16 是原稿腳本的 bug。

user_impact 也寫得過度。在 2026-06-01～09-23 的參考窗口裡，自選股只有 00878 在 2026-08-18 真的觸發假的 MA20 下穿出場（原始價 True，還原價 False）。6196（06-24）、2454（07-07）、0050（07-21）、3545（08-04）、2379（09-08）當天都沒有觸發。窗口內也沒有 3034 以外的其他自選股觸發。00885 與 00893 在窗口內沒有除權息事件。「每次除權息日都可能收到假跌破訊號」只對 00878 有實證，其他自選股受影響的是 change_pct 與均線等數值偏移。

原 finding 沒提到的新面向（同一根因）：未還原的不只是除權息，股票分割或面額變更也沒有處理。
(a) 自選股 0050 在 2025-06-18 的 enriched change_pct 為 −74.78%（prev_close 188.65 → close 47.57）。維基百科證實 0050 於 2025 年「每一受益權單位分割為四個新單位」。回測區間只要跨過這一天，MA60、momentum_60d、high_60d、停損等都會看到一次假崩跌。
(b) 全歷史 change_pct < −40% 的共 28 列，例如 00685L 在 2026-07-07 −96.0%、00631L 在 2026-03-31 −95.7%、6949 在 2026-09-07 −95.5%。
(c) KNN 標籤：2026-05-25 之後有 3,840 個 4 日標籤窗口跨過除權息日，其中 1,092 個標籤符號翻轉，1,035 個由「漲」翻成「跌」，偏向看空。
(d) 另外看到儲存的 signal_limit_down 在 4 個除權息日為 True（1442、2542 在 09-23，5906 在 09-10，6924 在 09-15）。收盤都高於 TWT49U 官方跌停價（例如 1442 收 27.8，官方跌停 25.9），是假跌停。這屬於漲跌停以前收盤而非參考價為基準的問題，可能與其他 finding（work\V_exlimit、verify-exlimit）重疊，我沒有深入追查。

環境附註：
- WebSearch 額度已用盡（200/200），分割的恢復交易日（2025-06-18）只由本地資料得出，線上只確認了 1 拆 4。
- 我的工作目錄 verify-adj 裡原本就有其他 agent 留下的 gaps.py、repro.py（03:03），我沒有動它們。
- repo 的 git status 與開始時相同。
    - 證據：
      - backend/app/taiwan/enriched_refresh.py:94 — enriched = compute_enriched(raw, factors=pl.DataFrame(), instruments=instruments)（台股永遠傳入空的復權因子）
      - backend/app/indicators/pipeline.py:1016-1024 — 先把 close 複製成 raw_close；只有 factors 非空才呼叫 _apply_adj_factor
      - backend/app/indicators/pipeline.py:388,397,504 — prev_close=close.shift(1)、ma20=close.rolling_mean(20)、change_pct=close/close.shift(1)-1
      - CONTRIBUTING.md:100 — 「enriched 的 open/high/low/close 為前復權價格。」
      - backend/app/backtest/engine.py:509 — 回測矩陣直接讀 data_dir/enriched_dirname(asset_type)，也就是未還原的 kline_daily_enriched
      - backend/app/strategy/builtin/volume_price_surge.py:60、strong_open.py:84、high_turnover_surge.py:76、low_volatility_leader.py:76（另有 trend_breakout.py:77、oversold_bounce.py:77 等共用同一 MA20 下穿出場）；lorentzian_knn.py:144-145 標籤＝sign(close[t+4]-close[t])
      - v1_raw_vs_close.py 輸出：rows with raw_close: 1423597；close != raw_close (exact): 0（涵蓋 2024-01-02～2026-09-23，2346 檔）
      - v2_exdiv_changepct.py 輸出：TWT49U rows 994；matched 956；prev_close == pre_ex_close 956/956；abs_err median 2.824pp、p90 6.425pp、max 66.348pp（6669 緯穎 2026-09-02，權，7800→參考價 2614.99）；>3pp 457；>1pp 743；app 為負但交易所基準≥0 的有 373 筆
      - v2 範例：2379 2026-09-08 app −5.075% 對交易所 −1.705%；00878 2026-08-18 −4.059% 對 −1.1%；3545 2026-08-04 −0.399% 對 +1.258%；6196 2026-06-24 −0.185% 對 +1.02%
      - 參考資料 csv\twse_stock_day_202608_202609.csv：2379,2026-09-08 收盤 692.0，漲跌欄 X0.00；00878,2026-08-18 收盤 32.38，X0.00；3545,2026-08-04 收盤 49.9，X0.00（官方收盤與 app 相同，漲跌以除權息標記）
      - 參考資料 csv\twse_twt49u_20260601_20260923.csv：2379 2026-09-08 除息前 729.0、參考價 704.0；00878 2026-08-18 33.75→32.74；3545 2026-08-04 50.1→49.28
      - v3_signal_impact.py 輸出：stored ma20 vs recomputed raw ma20 max abs diff 1.14e-13；除權息日 raw MA20 breakdown 296（儲存的 signal_ma20_breakdown 也是 296）、還原價 74、raw-only 222、adj-only 0；自選股只有 00878.TWSE 2026-08-18 r_bd=True、a_bd=False
      - v4_extra.py 輸出：strong_open 一致尺度翻轉 5 次；照原稿的混合尺度寫法翻轉 16 次，其中 11 次出現在窗口內還有後續除權息事件的列；原稿 A09\step11_adj_impact.py:41-42 以未調整的 o[k]、c[k] 比較調整後的 ca[k-1]
      - v4_extra.py 輸出：跨除權息日的 4 日標籤窗口 3840 個，翻轉 1092 個，其中由漲翻跌 1035 個
      - v4_extra.py 輸出：0050.TWSE 2025-06-18 prev_close 188.65、close 47.57、change_pct −0.7478；全歷史 change_pct < −40% 的共 28 列（例如 00685L 2026-07-07 −96.0%、00631L 2026-03-31 −95.7%）
      - https://zh.wikipedia.org/wiki/元大台灣卓越50證券投資信託基金 — 0050 於 2025 年「每一受益權單位分割為四個新單位」（WebFetch 摘要；頁面沒有列出恢復交易日）
      - v3 輸出：除權息日儲存的 signal_limit_down=True 共 4 列（1442、2542 在 09-23，5906 在 09-10，6924 在 09-15）；TWT49U 官方跌停價分別為 25.9／37.35／35.45／125.5，收盤 27.8／39.55／37.35／128 都高於跌停價
      - 腳本與輸出目錄：AUDIT_SCRATCH/work/verify-adj/（v1～v4、exdiv_join.csv）

### [P2 → P2] lorentzian_knn 的選股與回測在同一天給出不同訊號（候選池取決於歷史窗長度）（部分成立）

- **主張**：KNN 的候選鄰居是矩陣中從第 0 根開始、每 4 根取 1 根的所有 K 棒，所以預測值取決於載入了多少歷史。選股只載入 required_warmup_bars+1=121 根（候選約 29 個，其中前 60 根的 15 個是 0.5 佔位值）；回測則載入整段期間。同一天的進場訊號因此不一致。原版 Pine 以固定的 maxBarsBack 視窗計算。
- **正確的定義或數值**：同一交易日、同一參數下，選股結果與回測訊號一致（CONTRIBUTING §5.3：選股、評分與回測必須使用同一候選集）
- **app 實際行為**：選股與回測的進場只有約一半重疊；回測的起始日不同，也會改變同一天的訊號
- **對使用者的影響**：使用者依回測績效採用 lorentzian_knn 後，實際選股訊號與回測驗證過的訊號約有一半不同，回測失去參考價值
- **修法建議**：把候選池改成固定長度的回看視窗（例如最近 N 根有效 K 棒，類似 maxBarsBack），並讓 required_warmup_bars 涵蓋 N 加上正規化窗；排除特徵為佔位值的候選
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/builtin/lorentzian_knn.py:148 — candidate_idx = np.arange(0, bars, stride)
  - backend/app/strategy/builtin/lorentzian_knn.py:126-127 — required_warmup_bars = norm_window + 60
  - backend/app/strategy/engine.py:683 — 選股歷史根數 = required_warmup_bars(params) + 1
  - backend/app/repository.py:1184-1189 — 只取最後 lookback_days 個交易日
- **線上證據**：
  - https://gist.github.com/itsXactlY/450618a1ab3a85b450b22c8e188c7a1d — 原版迴圈 `size = math.min(settings.maxBarsBack-1, array.size(y_train_array)-1)` 與 `for i = 0 to sizeLoop`（以固定的 maxBarsBack 限制訓練窗）
- **本地計算證據**：
  - step6_lkn_window.py：200 檔 × 最近 20 個交易日，選股窗（121 根）與回測窗（2025-01 起）相比：進場不同的格數 139／4000；選股 160 筆、回測 131 筆，兩者都進場的只有 76 筆（占選股的 47.5%）
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：lorentzian_knn 的 KNN 候選鄰居是矩陣中從第 0 根錨定、每 sample_stride 根取 1 根的所有已可知標籤的 K 棒，沒有回看上限（lorentzian_knn.py:148、:151）。因此 T 日的預測取決於矩陣從哪天開始、載入了多少歷史；但策略宣告的暖機根數只有有限的 norm_window+60=120（:126-127）。

選股只載入 122 個交易日（engine.py:683、repository.py:1186）：共 31 個候選，其中 T 日可用 30 個，約一半（15 個）是前 59 根、特徵全為 0.5 的佔位值。回測則從 start−192 日曆日一路載入到區間結束（backtest/strategy.py:1079-1080）。

實測結果：
- 全市場 T=2026-09-23，使用者 UI 快取的 47 筆選股結果中，回測自 2025-01-02 起算時同日也進場的只有 13 筆，自 2026-01-02 起算時 16 筆。
- 200 檔 × 20 日的比較中，兩邊都進場的只占選股的約 44%。
- 回測起點不同，同一天的訊號也會改變。

次要因素：窗內計算的指標（如 MACD、RSI）暖機不同，約影響 2% 的特徵值。

原版 Pine 並不是固定的最近 N 根視窗。它從圖表第一根起累積陣列，迴圈掃最舊的 ≤maxBarsBack 筆，同樣取決於圖表載入的歷史長度。因此判準應是專案自己的選股與回測一致契約，不是對齊原版。

修正方向：
- 候選改為以 T 為錨的固定長度回看窗，取樣相位也從 T 起算（T−h、T−h−s……）。
- required_warmup_bars ≥ N＋norm_window＋horizon。
- 排除特徵為佔位值的候選。
    - 理由：核心缺陷成立，P2 恰當；有三處敘述要修正。

【程式碼：屬實】
1. 候選鄰居從矩陣第 0 根開始，每 stride 根取 1 根（lorentzian_knn.py:148）。只要 j+horizon<=t 就可以用（:151），所以 T 日的預測取決於矩陣從哪一天開始、長度多少。

2. 策略宣告的暖機根數是有限的 norm_window+60=120（:126-127）。演算法本身卻會用到矩陣裡全部的歷史，沒有任何有限的暖機根數能保證結果一致。

3. 選股的歷史根數是 required_warmup_bars+1（engine.py:683）。repository.get_enriched_history 取 trading_dates[-(lookback_days+1)]（repository.py:1186），實際載入 122 個交易日。_run_matrix_strategy 直接用這段歷史建矩陣（engine.py:1167-1178）。

4. 回測的矩陣起點是 load_start = config.start − max(120, 1.6×warmup) 日曆日（backtest/strategy.py:1079-1080），一路到回測結束。

5. rolling_min／max 要湊滿整個視窗才有值，不足時回 NaN（matrix.py:3449-3451）。_normalise 把 NaN 換成 0.5（lorentzian_knn.py:119）。因此前 59 根的 5 個特徵全是 0.5。

【本地重現（我自己寫的腳本，沒有沿用稽核的腳本）】
1. 抽 200 檔（含自選中的 5 檔股票），比較最近 20 個交易日，共 4000 格。選股窗 122 根，回測從 2025-01-02 起（矩陣 530–549 根）：
   - 進場訊號不同的有 133 格。
   - 選股 155 筆、回測 116 筆，兩邊都進場的只有 69 筆，占選股的 44.5%。
   - 兩邊都只給基礎 OHLCV 欄位時，結果幾乎一樣（135 格不同、重疊 43.9%）。可見差異來自視窗長度，不是欄位來源。

2. 全市場 2345 檔、T=2026-09-23：
   - 選股窗得到 68 筆原始進場，使用者實際的 strategy_cache.json 快取 47 筆，全部落在這 68 筆裡。這證明我的複製與生產結果一致。
   - 這 47 筆之中，回測從 2025-01-02 起算時，同一天也進場的只有 13 筆（27.7%）；從 2026-01-02 起算時為 16 筆（34%）。

3. 只改回測起點（2025-01-02／2025-07-01／2026-01-02），同一天 200 檔中就有 4–8 檔的訊號不同，而當天總進場數只有 3–7 檔。

4. 最近 20 根的正規化特徵，在兩種視窗下中位數差 0，只有 2.3% 的值差距超過 0.01。所以主因是候選池，特徵的暖機差異只是次要因素。

【使用者情境：確實會發生】
使用者資料中 lorentzian_knn 沒有覆寫參數（strategy_overrides 為空）。每日策略快取會重算它，2026-09-23 快取了 47 筆，會顯示在 UI。監控規則為 0 條，所以不影響監控路徑。

【需要修正的敘述】
1. 「原版 Pine 以固定的 maxBarsBack 視窗計算」不正確。原版每根 K 棒都把特徵與標籤 push 進 var 陣列，迴圈是 `for i = 0 to sizeLoop`，其中 sizeLoop = min(maxBarsBack−1, size−1)。它掃的是陣列最舊的 ≤2000 筆，也就是從圖表第一根起算，不是固定的最近 N 根。TradingView 圖表能載入的根數又依方案不同（5000–40000）。所以原版同樣取決於載入了多少歷史，maxBarsBack 只是數量上限。本專案文件（docs/radar-strategies-validation.md:12、:103）也聲明本策略不是原版的移植。因此判準應以專案自己的「選股與回測一致」契約為準，不是對齊原版。

2. 數字：選股窗是 122 根，不是 121 根。候選共 31 個，其中 T 日可用 30 個，不是約 29 個；0.5 佔位的有 15 個屬實，約占可用候選的一半。

3. CONTRIBUTING §5.3 的「同一候選集」原意是指候選股票集合，不是 KNN 的鄰居。不過選股與回測應一致的精神有依據，repository.py:1183 的註解就明言要避免「與回測結果不一致」。

4. 修正建議要補一點：固定長度的回看窗之外，取樣相位也必須以 T 為錨，例如取 T−h、T−h−s……。若仍從矩陣第 0 根起每 s 根取樣，視窗起點一變，選到的鄰居就會變。另外，required_warmup_bars 必須至少涵蓋 N＋norm_window＋horizon，並排除特徵為佔位值的候選。

【嚴重度】
沒有未來函數（usable 遮罩正確），也不涉及金融口徑。但同一策略、同一參數、同一天，選股與回測的訊號只有三到四成重疊，回測對選股失去驗證力。這屬於明確的功能錯誤，P2 恰當。

【外部來源】
- TradingView 腳本頁：https://www.tradingview.com/script/WhBzgfDu-Machine-Learning-Lorentzian-Classification/ ，作者 jdehorty，原文 "Max Bars Back - This has a default value of 2000."
- Pine 限制文件：https://www.tradingview.com/pine-script-docs/writing/limitations/ ，原文 "40000 historical bars for the Ultimate plan … 5000 historical bars for other plans."
- scikit-learn TimeSeriesSplit：https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html ，原文 "max_train_size … Maximum size for a single training set"，以及 "successive training sets are supersets of those that come before them"。它說明擴張窗與固定上限窗都是合法設計；關鍵在訓練集必須是 T 的確定函數，不能取決於載入窗。
- 為核對原版迴圈，也讀了 finding 引用的 gist，原始碼確認如上。

另外，本次的 WebSearch 額度已用完，改用 WebFetch 完成查證，共發出 7 次網路請求。
    - 證據：
      - backend/app/strategy/builtin/lorentzian_knn.py:148 — candidate_idx = np.arange(0, bars, stride, dtype=np.int64)（候選從矩陣第 0 根錨定）；:151 usable = candidate_idx + horizon <= bar_idx（只受前瞻限制，沒有回看上限）
      - backend/app/strategy/builtin/lorentzian_knn.py:126-127 — required_warmup_bars = norm_window + 60 = 120；:112-119 _normalise 把 NaN 換成 0.5
      - backend/app/backtest/matrix.py:3449-3451 — _rolling_reduce 初始為 NaN，不足整個視窗的列維持 NaN，所以前 59 根 5 個特徵全是 0.5
      - backend/app/strategy/engine.py:683 — 選股歷史 = required_warmup_bars(params) + 1；engine.py:1167-1178 _run_matrix_strategy 直接用 context.history 建矩陣
      - backend/app/repository.py:1186 — lookback_start = trading_dates[-(lookback_days + 1)]，實際為 122 個交易日；:1183 註解明言要避免「与回测结果不一致」
      - backend/app/backtest/strategy.py:1079-1080 — 回測 load_start = config.start - max(120, 1.6×warmup) 日曆日，矩陣涵蓋整段回測區間
      - 本地重現 1（AUDIT_SCRATCH/work/V-lkn-window-def/lkn_window_check.py，執行：cd backend && uv run python <腳本>）：200 檔 × 最近 20 個交易日，選股窗 122 根 vs 回測自 2025-01-02 起（530–549 根）：diff=133/4000，選股 155 筆、回測 116 筆、兩邊都進場 69 筆（占選股 0.445）；兩邊都只給基礎欄位時 diff=135、重疊 0.439
      - 本地重現 2（同一支腳本）：T=2026-09-23，回測起點 2025-01-02(549 根) vs 2025-07-01(426 根) 有 4/200 檔訊號不同；2025-07-01 vs 2026-01-02(308 根) 有 8/200 檔不同（當日進場數僅 3–7 檔）。選股窗 122 根有 31 個候選，其中 15 個特徵為 0.5 佔位值
      - 本地重現 3（lkn_full_universe.py、lkn_full_universe2.py）：全市場 2345 檔、T=2026-09-23，選股窗原始進場 68 筆；使用者 data/user_data/strategy_cache.json 快取 lorentzian_knn 共 47 筆（as_of 2026-09-23），47/47 都在我的選股複製結果中。這 47 筆在回測自 2025-01-02 起算時同日也進場的只有 13 筆，自 2026-01-02 起算時 16 筆
      - 本地重現 4（lkn_feature_attrib.py）：比較最近 20 根的正規化特徵，122 根窗 vs 549 根窗，中位數差 0.0，差距 > 0.01 的比例 0.0234。差異主因是候選池，特徵暖機是次要因素
      - https://gist.github.com/itsXactlY/450618a1ab3a85b450b22c8e188c7a1d — 原文 `array.push(y_train_array, y_train_series)`、`array.push(f1Array, featureSeries.f1)`、`size = math.min(settings.maxBarsBack-1, array.size(y_train_array)-1)`、`for i = 0 to sizeLoop`：迴圈掃陣列最舊的 ≤2000 筆，從圖表第一根錨定，不是最近的固定視窗
      - https://www.tradingview.com/script/WhBzgfDu-Machine-Learning-Lorentzian-Classification/ — 作者 jdehorty："Max Bars Back - This has a default value of 2000."
      - https://www.tradingview.com/pine-script-docs/writing/limitations/ — "40000 historical bars for the Ultimate plan ... 10000 historical bars for Essential and Plus plans. 5000 historical bars for other plans."（原版的訓練起點同樣取決於載入了多少歷史）
      - https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html — "max_train_size ... Maximum size for a single training set"；"successive training sets are supersets of those that come before them"
      - docs/radar-strategies-validation.md:12,103 — 已記錄「非原指標精確移植」與「遠短於 2000 根回看」，但沒有記錄選股與回測不一致；docs\code-review-2026-09-22.md 查無 lorentzian 或 knn
      - CONTRIBUTING.md:161 — 「歷史策略結果、回測交易記錄和 UI 展示是否使用同一候選集」（原意是候選股票集合，精神支持選股與回測應一致）
      - 工作樹核對：稽核前後 git status 相同，都只有既存的 3 個修改檔，沒有 stash
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：lorentzian_knn 的 KNN 候選取自矩陣第 0 根起、每 4 根 1 根（lorentzian_knn.py:148），所以預測值取決於矩陣載入了多少歷史。

選股時：
- repo 快取涵蓋不足而落空，改走 screener.py 慢路徑，只載入 122 個交易日（screener.py:249、296-299）。
- 最後一根可用的候選有 30 個。其中 15 個（第 0～58 根）的 5 個特徵全是 0.5 佔位值，另 5 個（第 60～76 根）部分特徵是佔位值。
- 2026-09-23 選股時，8 個近鄰平均約一半（50.9%）是全佔位的候選。

回測時：
- 矩陣從 start−192 個日曆天開始（backtest/strategy.py:858-859），候選池長得多。

實測結果（最近 20 個交易日，全市場 44,201 格）：
- 對比回測起始 2025-01-01：進場不同 1,606 格。選股 1,602 筆、回測 1,244 筆，兩者都進場 620 筆（占選股 38.7%）。
- 對比回測起始 2026-01-01：兩者都進場 761 筆（占選股 47.5%）。
- 兩個回測起始日互比，同日也有 1,300 格不同。
- 2026-09-23 實際選股 47 筆，與回測 33 筆只重疊 13 筆。

另有次要差異來源：選股的 rsi_14 取自 compute_indicators 欄位，回測則在矩陣內計算，但影響很小。

原 Pine 並不是「固定的近期 maxBarsBack 滑動窗」：它限制候選數，但候選從最舊的 K 棒索引 0 起算。

修正方向：
- 改為固定長度的近期候選窗。
- 讓 required_warmup_bars 涵蓋候選窗與正規化窗。
- 排除特徵為佔位值的候選。
    - 理由：這條 finding 的核心主張成立：選股和回測在同一天給出不同的進場訊號，而且回測起始日不同也會改變同一天的訊號。數據重現結果與 finding 的量級一致。只有幾個輔助細節需要修正。

一、重現方法（可信度已驗證）
- 選股路徑沿用實際流程：
  - required_history_bars＝121，單跑與 run_all 相同。
  - repo 快取只涵蓋 300 天，但需要 362 天，所以實際會落空，改走 screener.py 的慢路徑：讀 180 個日曆天，再裁到最後 122 個交易日。
  - 用 compute_indicators、compute_signals、build_market_data_matrix 組矩陣，欄位與 _matrix_field_columns 相同。
- 用這條路徑重算 2026-09-23 的 pipeline 結果，與使用者已存的 strategy_cache.json（lorentzian_knn 共 47 筆）逐檔比對，47／47 完全相同，零差異。重現路徑因此可信。
- 回測路徑依 backtest/strategy.py:858-859 設定 load_start＝start−192 天。矩陣欄位只含儲存欄，所以 rsi_14 等指標在矩陣內計算。

二、數字並列（最近 20 個交易日：2026-08-27～09-23，全市場）

| 比較 | 格數 | 進場不同 | 選股進場 | 回測進場 | 兩者都進場 |
|---|---|---|---|---|---|
| finding（200 檔） | 4,000 | 139（3.5%） | 160 | 131 | 76（占選股 47.5%） |
| 本次 vs 回測起始 2025-01-01 | 44,201 | 1,606（3.6%） | 1,602 | 1,244 | 620（38.7%） |
| 本次 vs 回測起始 2026-01-01 | 44,201 | 1,562 | 1,602 | 1,482 | 761（47.5%） |
| 兩個回測起始日互比（2025 vs 2026） | 44,201 | 1,300 | — | 1,244 vs 1,482 | 713 |

- 套用 basic_filter 的 pipeline 層級（2026-09-23）：實際選股 47 筆，回測（2025 起）33 筆，只有 13 筆重疊。
- 自選 9 檔 × 20 天共 180 格，其中 39 格的進場或出場不同。例如 00885 在 2026-08-27：回測進場，選股沒有進場而是出場。

三、機制確認
- 候選池取自矩陣的第 0 根起、每 4 根取 1（lorentzian_knn.py:148）。
- 選股矩陣只有 122 根，最後一根可用的候選有 30 個（第 0～116 根）。
- 佔位值情況：
  - 第 0～58 根的 5 個特徵正規化後全部是 0.5 佔位值，共 15 個候選。
  - 另有 5 個候選部分特徵仍是佔位值（第 60～76 根）：kdj_k 從第 67 根起、boll_position 從第 78 根起才有效。
  - 合計 30 個候選中有 20 個受影響。
- 2026-09-23 選股時，k=8 個近鄰平均有 4.07 個（50.9%）是特徵全為佔位值的候選；83.7% 的個股至少選到 1 個。這一點原 finding 沒有量化。

四、finding 需修正的細節
1. 選股載入的是 122 根，不是 121 根：lookback_start＝trading_dates[-(lookback_days+1)]。
2. 可用候選是 30 個，不是約 29 個。
3. 實際走的是 screener.py:249／296-299 的慢路徑。finding 引用的 repository.py:1184-1189 是 repo 快取路徑，而 1178-1179 的覆蓋檢查會讓它落空。
4. 次要來源：選股的 rsi_14 取自 compute_indicators 欄位，回測則在矩陣內計算。但影響很小：改成矩陣內計算後，選股進場 1,602 → 1,624，與回測的差異 1,606 → 1,618。主因仍是窗長。
5. Pine 原版的描述不精確。y_train_array 只用 array.push、沒有 shift，迴圈 for i = 0 to sizeLoop 從索引 0（最舊的 K 棒）開始。因此原版是把候選數上限設為 maxBarsBack，但候選錨定在圖表最早的歷史，並不是「最近 N 根」的滑動窗；它同樣受載入歷史影響。這是依引用程式碼所做的推論。這一點不影響本 repo 選股與回測不一致的結論，修正建議（固定的近期窗）仍然合理。

嚴重度維持 P2：明確的功能錯誤，選股與回測的候選集不一致，違反 CONTRIBUTING §5.3 第 161 行。
    - 證據：
      - backend/app/strategy/builtin/lorentzian_knn.py:148 — candidate_idx = np.arange(0, bars, stride)：候選從矩陣第 0 根起每 4 根取 1，候選池依矩陣長度而定
      - backend/app/strategy/builtin/lorentzian_knn.py:117-119 — 正規化窗內有 NaN 時回退為 0.5；lorentzian_knn.py:126-127 required_warmup_bars = norm_window + 60 = 120
      - backend/app/strategy/engine.py:683 — 選股需求 = required_warmup_bars + 1 = 121（實測 required_history_bars single=121, run_all=121）
      - backend/app/repository.py:1178-1179 — 快取需涵蓋 (121+60)*2=362 天；repository.py:570 快取只讀 300 天，因此實際會落空
      - backend/app/services/screener.py:249,296,299 — 慢路徑讀 180 個日曆天，再裁到 trading_dates[-(lookback_days+1)] 之後，實際為 122 根（實測 screen_bars=122）
      - backend/app/backtest/strategy.py:858-859 — 回測 load_start = start − max(120, int(120*1.6)) = start − 192 天；matrix.py:713 矩陣從 load_start 開始（實測起始 2025-01-01 時為 549 根，首日 2024-06-24）
      - backend/app/backtest/strategy.py:515 — 回測矩陣只含儲存欄（實測 fields=[amount, raw_close, raw_high, raw_low]），選股矩陣則含 rsi_14、momentum_20d、vol_ratio_5d 欄位
      - CONTRIBUTING.md:161 — 「同時核對歷史策略結果、回測交易記錄和 UI 展示是否使用同一候選集和排序方向」
      - 指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-lknwin/repro.py；輸出見同資料夾 run1.log 與 per_date.json
      - run1.log：reproduced screening (pipeline, basic_filter) rows 47 overlap with cache 47 only_cache [] only_repro []（與 data\user_data\strategy_cache.json 在 as_of 2026-09-23 的 47 筆完全一致）
      - run1.log：screening vs backtest(start=2025-01-01)，20 天：cells=44201 entry_differs=1606 screen_entries=1602 backtest_entries=1244 both=620
      - run1.log：screening vs backtest(start=2026-01-01)：entry_differs=1562 screen=1602 backtest=1482 both=761；backtest 2025 vs 2026：differs=1300 bt2025=1244 bt2026=1482 both=713
      - run1.log：短窗但改在矩陣內計算指標：screen_entries=1624 differs=1618（相對 1602/1606），欄位來源的影響很小
      - run1.log：backtest(2025 start, pipeline) entries on 2026-09-23 共 33 筆，與實際選股快取 47 筆重疊 13 筆
      - placeholder.log（placeholder.py）：正規化後首個有效列 rsi_14/rsi_9/macd_hist_pct=59、kdj_k=67、boll_position=78；最後一根可用候選 30 個 [0,4,...,116]
      - neighbors.log（neighbors.py）：2026-09-23 選股 k=8，平均全佔位近鄰 4.07 個（50.9%），至少 1 個的個股占 83.7%；自選 180 格中進場或出場不同的有 39 格
      - https://gist.github.com/itsXactlY/450618a1ab3a85b450b22c8e188c7a1d — 原文：`size = math.min(settings.maxBarsBack-1, array.size(y_train_array)-1)`、`sizeLoop = math.min(settings.maxBarsBack-1, size)`、`for i = 0 to sizeLoop`、`array.push(y_train_array, y_train_series)`；array.shift 只作用在 distances/predictions，y_train_array 沒有 shift → 候選從索引 0（最舊）起算（推論）
      - 工作樹比對：git status --porcelain 執行前後同為 3 個既有修改（daily_update.py、pnpm-lock.yaml、vite.config.d.ts），本次未寫入 repo 或 data

### [P2 → P2] factor_rank_research 以截面 min-max 搭配固定的 70/40 門檻，結果被極端值主導，且與因子研究的 rank 口徑不一致（部分成立）

- **主張**：build_matrix_score 對每個交易日的全體標的做 min-max 正規化，只要一個極端值就會把其他標的壓到接近 0 分。挖掘流程固定使用 entry 70、exit 40，因子研究卻用 rank IC 與 rank 分組。結果是多數因子組合幾乎沒有標的入選，而且幾乎全部標的每天都觸發出場。
- **正確的定義或數值**：截面因子應採用 rank 百分位或縮尾後的 z-score，使 70/40 門檻有穩定的語意，並與因子研究的 rank IC 口徑一致
- **app 實際行為**：分數分布由當天的極端值決定：多數日子沒有標的入選，持倉則在隔天就被 exit 旗標平倉
- **對使用者的影響**：因子挖掘產生的候選策略在回測中幾乎沒有交易，或交易後立即出場，Sharpe 等評估失真，可能把好因子誤判為無效
- **修法建議**：把 factor_rank_research（或 build_matrix_score 的研究模式）改為截面 rank 百分位（或縮尾加 z-score 再轉成百分位），並補一個含極端值的單位測試
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/matrix.py:3708-3739 — 以 row_min 與 row_max 做 min-max
  - backend/app/strategy/builtin/factor_rank_research.py:117-129 — universe=isfinite(close)；entry=score>=70；exit=score<=40
  - backend/app/backtest/mining_runtime.py:272-273 — 固定 entry_score 70.0、exit_score 40.0
  - backend/app/backtest/factor.py:745-746,1006 — 研究用 rank(method="average") 計算 IC 與分組
- **線上證據**：
  - https://quantdare.com/scaling-normalisation-standardisation-a-pervasive-question/ — 「The control of the outliers is problematic, since the minimum and the maximum will always be 0 and 1, no matter the dispersion of the raw data.」；rank 轉換「smooths the effect of outliers」
- **本地計算證據**：
  - step7_factor_rank.py（2026-03～09-22，全市場 2,316 檔）：單一因子 score>=70 的比例中位數：vol_ratio_5d 0.04%（每天約 1 檔；9/22 最大值為 00867B.TPEX 的 279.3）、amihud_20d 0.04%（1 檔）、momentum_20d 0.37%（8 檔）；rank 百分位則固定為 30%
  - momentum_20d 加 vol_ratio_5d 的組合：每天 score>=70 的標的中位數 0、進場 0；score<=40（出場旗標）的比例中位數 99.6%
  - 獨立重算的分數與 app 最大差 1e-5，進場差 0，確認是口徑問題而非實作錯誤
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：factor_rank_research（含挖掘發布的 factor-rank 策略）以逐日截面 min-max 分數套固定門檻 70/40。挖掘時卻用截面 average-rank 合成分數來挑選因子組合與方向（mining.py:1339-1371），研究與執行的口徑不一致。

結果依方向與因子偏態而定。高方向的右偏因子（vol_ratio_5d、amihud_20d、turnover_rate）幾乎沒有標的入選，其中 turnover_rate 在 142 天內全部 0 進場。短暫型因子（vol_ratio_5d）的持倉約 94% 會在次日被出場旗標平倉。低方向時則相反：約 99% 標的分數 ≥70，進場由 top_rank=20 截斷，出場旗標幾乎失效。持續型因子（momentum_20d、amihud_20d）不會出現次日即出場的情況。

回測績效因此失真，也可能誤判候選因子。不過挖掘功能已從產品 UI 移除，排程預設關閉，使用者資料中也沒有任何挖掘紀錄或已發布的 factor-rank 策略，在使用者情境下不會發生，只影響直接呼叫後端 API 的研究用途。嚴重度應為 P3，不是 P2。修正方向維持原建議：改用截面 rank 百分位，或先縮尾再做 z-score。
    - 理由：程式碼機制成立：build_matrix_score 對每個交易日的全體標的做 min-max 正規化，factor_rank_research 再對這個分數套用固定的絕對門檻 70/40。挖掘流程的 beam search 則用截面 average-rank 的加權合成分數來挑選因子組合與決定方向，所以「研究（選組合）」用 rank，「執行（回測）」用 min-max，口徑確實不一致。外部權威來源（scikit-learn、Microsoft qlib）也支持「min-max 對極端值非常敏感；截面因子正規化通常用 rank 或 z-score」。

finding 有兩處說得太絕對：
1. 「多數因子組合幾乎沒有標的入選」只在「高方向＋右偏因子」時成立。方向為 low 時結果相反：約 99% 標的分數 ≥70，由 top_rank=20 截成 20 檔，出場旗標幾乎不會觸發。
2. 「持倉隔天就被 exit 平倉」只在因子持續性低時成立，例如 vol_ratio_5d（251/267）。momentum_20d（28/1286）與 amihud_20d（4/231）都不成立。

使用者影響：產品已移除挖掘功能。前端不註冊 /mining 路由，docs/features.md 寫明不提供因子挖掘，排程預設關閉且偏好檔沒有開啟，data/research/mining/runs 是空的，也沒有已發布的 factor-rank 策略，factor_rank_research 又是 research_only。所以在使用者情境下不會發生，只剩後端 API 直接呼叫這條路。嚴重度應從 P2 降為 P3。
    - 證據：
      - backend/app/backtest/matrix.py:3698-3741 — 以 row_min／row_max 逐日做 min-max，(v-row_min)/row_range；方向為 low 時是 1-x；最後乘以 100
      - backend/app/strategy/builtin/factor_rank_research.py:117-129 — universe=np.isfinite(market.close)；entry=score>=entry_score，再經 _limit_top_rank 截斷；exit=score<=exit_score
      - backend/app/backtest/mining_runtime.py:269-275 — factor_rank 候選固定使用 entry_score 70.0、exit_score 40.0、top_rank 20；symbols=self.request.symbols（None 代表全市場）
      - backend/app/backtest/mining.py:1339-1371 — _proxy_rank_ic 以 _average_rank 後的加權合成分數（composite=(factor_ranks*direction_values)@weight_values）計算 rank IC；:682 方向由 rank_ic 的正負決定。挖掘用 rank 挑組合，回測卻用 min-max 執行（比 finding 引用的 factor.py 更直接的不一致證據）
      - backend/app/backtest/factor.py:737-749,998-1003 — 因子研究的 IC 與分組都用 rank(method="average")
      - backend/app/strategy/engine.py:1495-1530 — 一般策略的 _apply_scoring 也用 min-max，但只用於排序；會對 min-max 分數套絕對門檻的只有 factor_rank_research（及其發布版本 mining_candidates.py:553-625）
      - 本地重算（指令：cd backend && PYTHONIOENCODING=utf-8 uv run python AUDIT_SCRATCH/work/verify_factor_rank_minmax/check.py；輸出存於同目錄 out.txt）— 合成資料：100 檔中 1 檔換手率 300，方向 high 時 score>=70 只有 1 檔、score<=40 有 99 檔、中位數 1.76；方向 low 時 score>=70 有 99 檔、entry=20、中位數 98.24
      - 本地實跑 app 的 FactorRankResearchMatrixStrategy.compute_signals（2026-03-02～09-22 共 142 天，全市場約 2,294 檔）：vol_ratio_5d:high 的 ge70 中位數 1 檔（0.04%）、le40 99.9%、次日出場 251/267；turnover_rate:high 142 天全部 0 進場；momentum_20d+vol_ratio_5d 高方向組合 ge70 中位數 0，97/142 天沒有進場，次日出場 49/56
      - 本地實跑的反例：vol_ratio_5d:low 的 ge70 為 99.65%、entry 中位數 20、le40 0.2%、次日出場 7/2820；amihud_20d:low 的 ge70 為 99.39%、次日出場 0/2820；momentum_20d:high 次日出場只有 28/1286；amihud_20d:high 次日出場 4/231；有界因子 rsi_14:high 的 ge70 為 6.28%、close_position:high 為 29.0%，分布正常
      - https://scikit-learn.org/stable/auto_examples/preprocessing/plot_all_scaling.html — 「Both StandardScaler and MinMaxScaler are very sensitive to the presence of outliers.」「this scaling compresses all inliers into the narrow range [0, 0.005]」；QuantileTransformer「robust to outliers」
      - https://raw.githubusercontent.com/microsoft/qlib/main/qlib/data/dataset/processor.py — qlib 的截面處理器只有 CSZScoreNorm（逐 datetime 做 zscore 或 robust_zscore）與 CSRankNorm（groupby datetime rank(pct=True) 後 -0.5、*3.46）；MinMaxNorm 則以 fit_start_time～fit_end_time 的時間區間擬合，不是截面；RobustZScoreNorm 有 clip_outlier：np.clip(X,-3,3)
      - frontend/src/router.test.ts:1,20-23 — 「Legacy Product Removal」，斷言沒有註冊 /mining；repo 中只有 frontend/src/lib/miningTask.ts 定義 startMining，沒有任何元件呼叫它
      - docs/features.md:42 — 「本產品不提供因子挖掘、A 股連板分析、舊式策略回測平台或自動下單功能。」
      - backend/app/services/preferences.py:539-541 — mining_schedule_enabled 預設 False；data\user_data\preferences.json 沒有任何 mining 鍵；data\research\mining\runs 是空目錄；repo 內沒有已發布、使用 FactorRankResearchMatrixStrategy 的策略檔；後端仍有 app.include_router(mining.router)（main.py:425），可經 API 直接觸發
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：factor_rank_research 用截面 min-max 產生 0～100 分（matrix.py:3697-3739），再套挖掘流程寫死的 70/40 門檻（mining_runtime.py:272-274）。可是挖掘在挑候選與決定方向時，用的是 rank 合成的 IC（mining.py:1357-1362、682）。因此偏態因子的門檻語意取決於當天的極端值和因子方向。

方向為 high 時（例如 vol_ratio_5d、amihud_20d、atr_pct 等），每天只有 1～2 檔分數 >=70（vol_ratio_5d 的中位數為 0.044%；9/22 由 00867B.TPEX 的 279.3 撐開全距）。約 99.9% 的標的分數 <=40。vol_ratio 類的進場，隔天約 96% 會被出場旗標命中。

方向為 low 時正好相反：99% 以上的標的分數 >=70，出場旗標（<=40）只有 0.2%～0.6% 會觸發，持倉只能靠停損或持有天數才會出場。挖掘依 IC 的正負選方向，所以 low 方向常被選用（以 5 日報酬近似推測，52 個因子中有 44 個選 low）。

「多數組合沒有標的入選、而且隔天就出場」只在 high 方向加上偏態因子時成立。52 個因子在近似 IC 方向下，<=40 比例 >=90% 的只有 4 個，隔天出場率 >=50% 的沒有；momentum_20d 取 high 方向時，隔天出場率只有 2.5%。

多因子組合時，min-max 加權使 app 實際選出的 20 檔與 rank 合成的前 20 檔只重疊 10%～65%（中位數）。回測評估的已經不是依 IC 選中的那個組合。另外，_limit_top_rank 在 basic filter 之前截取前 20 名（matrix.py:3532、3556）。高分標的若是低流動性個股，會在後段被全部濾掉，例如 amihud_20d 取 high 方向時，142 天全部 0 筆進場。

建議修正：改用截面 rank 百分位（或縮尾後的 z-score 再轉成百分位），讓回測與挖掘的 rank IC 口徑一致；top_rank 改在 basic filter 之後截取；補上含極端值、並涵蓋 high 與 low 兩個方向的單元測試。
    - 理由：核心機制成立。build_matrix_score 對每個交易日做截面 min-max；factor_rank_research 用固定的 70/40 門檻（挖掘流程寫死這兩個值），而挖掘挑選候選用的卻是 rank 合成的 IC。我用 app 的純計算函式和自己寫的 polars 重算，兩邊的分數最大差 0.0002 分，所以不是實作錯誤，是口徑問題。finding 的主要數字大多重現。

不過 finding 有兩處以偏概全：
1. 它只看了方向為 high 的情況。挖掘流程依 rank IC 的正負決定方向（mining.py:682），方向為 low 時，偏態因子會出現相反的病態：99% 以上的標的分數 >=70，出場旗標（<=40）幾乎從不觸發。
2. 「隔天就被出場」只在 vol_ratio 與 amount_ratio 這類尖峰型因子取 high 方向時成立。momentum_20d 取 high 方向時，隔天出場率只有 2.5%。

另外找到兩個原記錄沒提到的面向：
- 多因子組合時，app 實際選出的 20 檔與挖掘 rank 合成的前 20 檔，重疊的中位數只有 10% 到 65%。也就是說，回測評估的組合和靠 IC 選中的組合並不相同。
- _limit_top_rank 在 basic filter 之前就截取前 20 名。amihud_20d 取 high 方向時，分數夠高的都是成交額不到 2,000 萬的標的，被 basic filter 全部刪掉，142 天裡每天都是 0 筆進場。

finding 與本次重算的數字對照：
- vol_ratio_5d：finding 為 0.04%（約 1 檔）；重算 0.0443%（中位數 1 檔），重現。
- 9/22 最大值：兩者都是 00867B.TPEX 的 279.34，重現。
- amihud_20d：finding 為 0.04%（1 檔）；重算 0.087%（2 檔），有小差異。
- momentum_20d：finding 為 0.37%（8 檔）；重算 0.31%（7 檔），有小差異。
- rank 百分位 >=70 的比例：兩者都是 30%，重現。
- momentum_20d＋vol_ratio_5d 組合：兩者都是分數 >=70 的中位數 0、進場 0；<=40 的比例 finding 為 99.6%，重算 99.59%，重現。

嚴重度維持 P2：這個策略是 research_only，只影響因子挖掘對候選策略的評估。使用者平常的看盤、自選股與監控流程不走這條路徑。
    - 證據：
      - 程式碼 backend/app/backtest/matrix.py:3697-3739：對每列算 row_min 與 row_max 做 min-max；方向為 low 時分數取 1-x（3735-3736）；最後乘以 100（3739）
      - 程式碼 backend/app/strategy/builtin/factor_rank_research.py:117-129：universe=isfinite(close)；entry=score>=70，再經 _limit_top_rank(20)；exit=score<=40
      - 程式碼 backend/app/backtest/mining_runtime.py:272-274：挖掘流程固定 entry_score 70.0、exit_score 40.0、top_rank 20
      - 程式碼 backend/app/backtest/mining.py:1357-1362（proxy IC 用各因子 average rank 的加權和）與 682（direction = 1 if rank_ic >= 0.0 else -1，所以 low 方向也會被選用）；factor.py:745-746、1006 的研究口徑用 rank(method="average")
      - 程式碼 backend/app/backtest/matrix.py:3532、3556：strategy.compute_signals（內含 top_rank 截取）先執行，之後才與 basic_mask（預設 price_min 3、amount_min 0.2e8，engine.py:41-54）做 AND，出場旗標則不套遮罩
      - 本地重現 repro.py（kline_daily_enriched 2026-03-02～09-22，共 142 個交易日；9/22 截面 2,316 檔）：vol_ratio_5d 方向 high，分數 >=70 的比例中位數 0.0443%（1 檔），finding 為 0.04%；amihud_20d 為 0.087%（2 檔），finding 為 0.04%（1 檔）；momentum_20d 為 0.3099%（7 檔），finding 為 0.37%（8 檔）；三個因子的 rank 百分位 >=70 都是 29.87%～30.01%
      - 本地重現：9/22 的 vol_ratio_5d 最大值為 00867B.TPEX 279.3426（次高 6674.TWSE 151.98；p50 1.006、p99 7.33），與 finding 相符
      - 本地重現：momentum_20d＋vol_ratio_5d（high,high），分數 >=70 的中位數 0（142 天中 97 天為 0）、進場中位數 0、<=40 的比例中位數 99.59%；進場在隔天被出場旗標命中的比例為 25/30（83.3%）
      - 本地重現：獨立的 polars 因子與 min-max 重算，對 app 分數的最大絕對差為 vol_ratio_5d 0.0、amihud_20d 0.0002、momentum_20d 0.0 分（p99 <1e-5，約 32 萬筆），可確認是口徑問題而不是實作錯誤
      - 反例（方向為 low，repro.py 與 scan_all.py）：vol_ratio_5d 分數 >=70 的比例 99.65%、<=40 只有 0.22%；amihud_20d 分別為 99.39% 與 0.56%。進場每天都滿額 20 檔，隔天出場率 0%，與 finding 說的「幾乎全部標的每天出場」正好相反
      - 反例（隔天出場）：momentum_20d 方向 high 的隔天出場率為 27/1089 = 2.48%；52 個可計算因子在近似 IC 方向下，沒有任何一個的隔天出場率 >=50%。>=50% 只出現在 vol_ratio_5d、vol_ratio_10d、amount_ratio_5d 取 high 方向時（95%～96.6%）
      - 全面掃描 scan_all.py（52 個因子，以 5 日 close-to-close rank IC 的正負近似挖掘會選的方向，屬推測性近似）：44 個選到 low、8 個選到 high；在選中的方向下，分數 >=70 的中位數低於 20 檔的有 14 個，<=1 檔的只有 3 個；<=40 的比例 >=90% 的只有 4 個，<=10% 的反而有 33 個。「多數因子組合幾乎沒有標的入選」並不成立，較常見的病態是出場幾乎從不觸發
      - 新面向（top_rank 先於 basic filter）：amihud_20d 方向 high，app 每天有 2 檔 >=70，但經 basic_mask 過濾後 142 天全部為 0 筆進場；log_amount 方向 low 同樣 142 天全為 0；vol_ratio_5d 方向 low，進場從 20 檔降到中位數 1 檔
      - 新面向（組合口徑不一致，combo_overlap.py）：app 入選的標的落在 rank 合成前 20 名內的比例中位數：momentum_20d＋amihud_20d（low,low）15%；momentum_5d＋atr_pct（low,low）10%；rsi_6＋annual_vol_20d（low,low）35%；ma10_bias＋amplitude（low,low）65%
      - 線上來源 https://quantdare.com/scaling-normalisation-standardisation-a-pervasive-question/ （WebFetch 取得）原文："The control of the outliers is problematic, since the minimum and the maximum will always be 0 and 1"；rank 轉換 "defines a specific range of values and smooths the effect of outliers."，與 finding 引用一致
      - 腳本與結果（工作樹外）：AUDIT_SCRATCH/work/verify-frminmax/ 下的 repro.py、repro_result.json、scan_all.py、scan_all.json、combo_overlap.py、combo_overlap.json。執行前後 git status 相同，repo 未被修改

### [P2 → P2] 換手率類策略在沒有歷史換手率時靜默降級：high_turnover_surge 永遠 0 筆，low_volatility_leader 把空值偽裝成 0 分（部分成立）

- **主張**：台股歷史 turnover_rate 只有 2026-09-23 一天有值。high_turnover_surge 在任何過去日期的選股與回測都靜默產生 0 筆；low_volatility_leader 的評分含 turnover_rate，因為 all_finite 不成立，除了最新日以外全部 0 分，最新日的 ETF 也是 0 分。文件已記錄「不能回測」，但程式沒有 fail-closed；ETF 被評為 0 分則是新發現的面向。
- **正確的定義或數值**：缺少換手率時回報明確的「不可計算」，或拒絕回測；評分缺欄時以缺值處理（例如只用可得因子重新分配權重），而不是給 0 分
- **app 實際行為**：回測與歷史選股靜默產生 0 筆，或全部 0 分；最新日 ETF 被排到最後
- **對使用者的影響**：使用者在低波動策略中看到自選的 0050、00878、00893 排在所有股票之後，會誤以為是評分結果；對換手策略做回測會得到「0 交易」而不是錯誤訊息
- **修法建議**：策略依賴的欄位在目標期間全部為 NaN 時，回測與選股應 fail-closed 並提示；build_matrix_score 對個別因子缺值改為以其餘因子的權重重新正規化，或回傳缺值，不填 0
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/matrix.py:3741 — score[~universe | ~all_finite] = 0.0
  - backend/app/backtest/matrix.py:1440-1447 — 台股的多根矩陣把 turnover_rate 設為 NaN
  - backend/app/strategy/builtin/low_volatility_leader.py:46 — scoring 含 turnover_rate 0.3；asset_types 含 etf
  - backend/app/strategy/builtin/high_turnover_surge.py:66-69
  - CONTRIBUTING.md:139 — 「缺少輸入時應返回明確的不可計算狀態，不能把空值偽裝成零分」
  - docs/taiwan-share-capital-phase-6h.md:151-152 — 「a strategy that scores on turnover_rate still cannot be backtested」（文件有記錄，但 backtest 與 strategy 程式沒有任何防護，grep turnover 在 app/backtest 中找不到拒絕邏輯）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - step2_coverage.py：661 個 enriched 分區中，只有 2026-09-23 有 turnover_rate（1,087 列）
  - step3_strategies.py：high_turnover_surge 在 2025-01-02～2026-09-22 的 418 個交易日，有進場的日數為 0
  - step9_misc.py：low_volatility_leader 在 2026-09-22 有 406 筆進場，分數最大值 0.0；2026-09-23 有 255 筆進場，其中 ETF 120 筆，ETF 分數最大值 0.0，股票分數 3.9～69.3；自選的 0050、00878、00893 都是 0.0
  - ETF 在 enriched 的 turnover_rate 為 null（step1：152／152 列）
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：只要任一評分因子是 NaN，build_matrix_score（matrix.py:3741）就把整筆分數設為 0。min-max 正規化下，0 分等於最差。這違反 CONTRIBUTING.md:139，也不符合業界做法：FTSE 因子指數只對可得的因子取平均，缺值給中性值。

台股 ETF 的 turnover_rate 全部是 null，因此在使用者實際的選股結果（strategy_cache.json，基準日 2026-09-23）中，受影響的有三支策略，不只 low_volatility_leader：
- low_volatility_leader：69 檔 ETF 全部 0 分，自選的 0050、00878、00893 排在第 75、92、96 名，都在全部 66 檔股票之後。
- bullish_alignment：44 檔 ETF 全部 0 分，0050 排第 126／166 名。
- pullback_to_support：13 檔 ETF 全部 0 分。

歷史 turnover 只有 2026-09-23 有值，所以理論上所有過去日期都是 0 分或 0 筆進場。但過去日期選股只能從 API 觸發，前端沒有入口。

回測在使用者目前的環境不會靜默產生 0 筆。data/instruments 是空的，所以回測會以「回测矩阵准备失败: matrix parquet fields unavailable: ['float_shares', 'total_shares']」報錯，而且報錯內容跟換手率無關。matrix.py:1447 的靜默 NaN 路徑沒有 fail-closed，只有在 instruments 帶有 float_shares 時才會觸發，是潛在缺陷。一旦觸發，high_turnover_surge 會是 0 筆交易，另外三支策略則會因為全部 0 分而依代碼順序挑股。

修正方向：個別因子缺值時，用其餘因子重新正規化權重，或者回傳明確的不可計算狀態；目標期間的必要欄位全部缺值時，回測與選股應 fail-closed。
    - 理由：核心主張成立，而且影響範圍比 finding 說的更大。「回測靜默 0 筆」這部分在使用者目前的環境不會發生，選股數字也不是 app 的實際輸出。

一、成立的部分
1. 程式碼：build_matrix_score 只要任一評分因子是 NaN，就把整筆分數歸零（matrix.py:3690、3706、3741），連另外 70% 權重的動能分數也一併丟掉。因為採 min-max 正規化，0 分等於排在最差的位置。專案規範 CONTRIBUTING.md:139 明文禁止這種做法：「缺少輸入時應返回明確的不可計算狀態，不能把空值偽裝成零分」。
2. 外部權威來源：FTSE Global Factor Index Series Ground Rules（v8.8，2026 年 7 月，第 9 頁）規定兩點。第一，複合因子只對非缺值的子因子取平均。第二，缺值給中性 Z-score 0，也就是平均水準，而不是最差。
3. 資料：661 個 enriched 分區中，只有 2026-09-23 有 turnover_rate，共 1,087／1,323 列。所有「00」開頭的 236 列（ETF）都是 null。
4. 使用者實際畫面：data/user_data/strategy_cache.json（2026-09-23 21:40 寫入）中，low_volatility_leader 有 135 筆結果，其中 69 檔 ETF 全部 0.0 分，股票 3.57～68.97 分。自選的 0050、00878、00893 分別排在第 75、92、96 名，都排在全部 66 檔股票之後。
5. 新面向：同樣的問題還出現在另外兩支策略。bullish_alignment 的 44 檔 ETF 全部 0 分，0050 排第 126／166 名，00878 排第 143 名；pullback_to_support 的 13 檔 ETF 也全部 0 分。這三支策略的評分都含 turnover_rate 0.3，asset_types 也都包含 etf。

二、不成立或需修正的部分
1. 回測不是靜默產生 0 筆。這個環境的 data/instruments 是空的，backend.log 在 2026-09-24 01:19 仍記錄 matrix cache prewarm skipped。我把回測載入流程的快取寫到 scratchpad 模擬，結果如下：
   - 預設開啟磁碟快取時，回測直接報錯：matrix parquet fields unavailable: ['float_shares', 'total_shares']，前端會看到「回测矩阵准备失败」。
   - 關閉快取時，報錯內容是：matrix turnover_rate requires float_shares。
   - 所以使用者得到的是一個錯誤訊息，雖然這個訊息跟換手率無關、看不懂。
   - finding 描述的靜默路徑（matrix.py:1439-1447 把 turnover 設成 NaN，而且沒有 fail-closed）只有在 instruments 帶有 float_shares 時才會觸發，屬於潛在缺陷。一旦觸發，high_turnover_surge 會是 0 筆交易；low_volatility_leader 則因為 engine.py:2075 是穩定排序，會依代碼順序挑股。我的重現顯示 2026-09-22 的前 10 名是 00400A、00401A 等等。
2. 「任何過去日期的選股」只能透過 API 的 as_of 參數做到。前端的 strategyRun 和 screenerRunPreset 都沒有任何呼叫端，所以使用者不會碰到。
3. finding 的 406／255／120 筆沒有套用預設基本過濾（price_min 3、amount_min 2 千萬）。實際的 app 輸出是 2026-09-22 共 168 筆，2026-09-23 共 135 筆，其中 ETF 69 筆。

三、嚴重度
維持 P2。這是明確的功能錯誤，也違反 CONTRIBUTING 5.1，但只影響排序和分數顯示，不會自動下單。

四、範圍外的附帶發現
在這個台股環境，連不用換手率的矩陣策略回測也會失敗。我實際跑了 boll_breakout 和 bollinger_squeeze，都得到同樣的 float_shares／total_shares 錯誤。原因是共用快取設定檔的 common_filter 要求股本欄位，但 instruments 是空的。這可能是另一個 P1（核心流程不可用），建議另案確認。
    - 證據：
      - backend/app/backtest/matrix.py:3690,3706,3741：all_finite 會把各因子的 isfinite 結果累積起來，最後 score[~universe | ~all_finite] = 0.0，只要任一因子是 NaN，整筆分數就歸零
      - backend/app/backtest/matrix.py:3718-3735：分數採 min-max 正規化，所以 0 分等於排最差
      - backend/app/backtest/matrix.py:1439-1447：台股多根矩陣的 turnover_rate[:] = np.nan；缺值時只這樣處理，沒有拒絕執行
      - backend/app/strategy/builtin/low_volatility_leader.py:20,46、bullish_alignment.py:20,36、pullback_to_support.py:20,56：三支策略的 scoring 都含 turnover_rate 0.3，asset_types 都含 etf；high_turnover_surge.py:59,66-69 要求 turnover_rate 大於 5
      - CONTRIBUTING.md:139：「缺少輸入時應返回明確的不可計算狀態，不能把空值偽裝成零分」
      - docs/taiwan-share-capital-phase-6h.md:151-152：文件已記錄 turnover 策略不能回測，只能做當日選股
      - 線上來源：https://www.lseg.com/content/dam/ftse-russell/en_us/documents/ground-rules/ftse-global-factor-index-series-ground-rules.pdf（FTSE Global Factor Index Series v8.8，2026 年 7 月，第 9 頁）原文：「This average is taken across non missing sub-factor Z-scores」，以及「stocks with missing factor data are allocated a neutral Z-score of zero」
      - 本地 cov.py（uv run python scratchpad\audit\work\verify_turnover_def\cov.py）：kline_daily_enriched 共 661 個分區，只有 6 個分區有 turnover_rate 欄位，其中只有 date=2026-09-23 有非空值（1087／1323）；00 開頭 236 列的 turnover 非空值為 0；0050、00878、00885、00893 都是 null
      - 實際快取 data\user_data\strategy_cache.json（mtime 2026-09-23 21:40:24）：low_volatility_leader 135 筆，ETF 69 筆的最高分為 0，股票 3.57～68.97 分；0050 排 75／135、00878 排 92、00893 排 96。bullish_alignment 的 ETF 44 筆全部 0 分，0050 排 126／166、00878 排 143。pullback_to_support 的 ETF 13 筆全部 0 分
      - 本地 repro.py 呼叫純計算函式 build_market_data_matrix 和 MatrixStrategyPipeline，並套用預設基本過濾。結果與實際快取完全一致（low_volatility_leader 2026-09-23 共 135 筆、ETF 69 筆）；2025-01-02～2026-09-22 共 418 天中，出現正分的天數為 0；high_turnover_surge 在這 418 天中有進場的天數為 0
      - 本地 repro.py 的回測排序示意：2026-09-22 全部 0 分，穩定排序後前 10 名為 00400A.TWSE、00401A.TWSE…0053.TWSE，也就是按代碼順序排（engine.py:2075 candidates.sort）
      - 本地 bt_load.py 以 load_market_data_matrix_from_parquet 模擬回測載入，instruments 用空的 DataFrame，快取寫到 scratchpad。low_volatility_leader、high_turnover_surge、bullish_alignment 在關閉快取時報 ValueError「matrix turnover_rate requires float_shares」，開啟快取時報「matrix parquet fields unavailable: ['float_shares', 'total_shares']」。所以回測會報錯，不是靜默 0 筆（backend\app\backtest\strategy.py:1150-1151 會回傳「回测矩阵准备失败」）
      - data\backend.log 2026-09-24 01:19:43 記錄：matrix cache prewarm skipped: matrix parquet fields unavailable: ['float_shares', 'total_shares']；data\instruments 目錄是空的
      - 本地 bt_load2.py：不用換手率的 boll_breakout、bollinger_squeeze 在開啟快取時也報相同錯誤。這是範圍外的附帶發現
      - frontend\src\lib\api.ts:2526、2809：strategyRun 和 screenerRunPreset 雖然接受 asOf，但在 src 中沒有任何呼叫端，所以「過去日期選股」只能經由 API 觸發
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：台股 enriched 的 turnover_rate 只有 2026-09-23 有值（1,087 列股票）；ETF 全部是 null（該日 236 列），因為 share_capital 不含 ETF。

選股路徑（StrategyEngine._run_matrix_strategy → build_market_data_matrix）遇到缺值時會靜默降級：
- high_turnover_surge 在 2025-01-02～2026-09-22 的 418 個交易日都是 0 筆進場，也沒有任何提示。
- low_volatility_leader 在歷史日期的所有進場格都是 0 分，例如 2026-09-22 有 406 筆，全部 0 分。
- 最新日 2026-09-23，ETF 因為 turnover_rate 為空，被 matrix.py:3741 設成 0 分，排在所有股票之後。實際的選股快取 strategy_cache.json 裡，69 檔 ETF 全部是 0.0，自選的 0050、00878、00893 也是 0.0。這違反 CONTRIBUTING.md:141。

回測路徑目前不是靜默 0 交易。回測時，enriched dataset 的 schema 不含 turnover_rate，而 data/instruments 是空的，所以 matrix.py:1437 拋出「matrix turnover_rate requires float_shares」，由 backtest/strategy.py:1151 回傳「回测矩阵准备失败」。回測會明確失敗，但錯誤訊息沒有說明原因是台股缺少歷史換手率。只有將來 instruments 提供 float_shares 時，matrix.py:1447 才會把整個矩陣的 turnover_rate 設為 NaN，讓回測變成靜默的 0 筆或 0 分，這屬於潛在風險。
    - 理由：選股路徑的數據全部重現，而且數字一致。回測路徑與 finding 說的不同：目前不是「靜默產生 0 筆」，而是直接報錯。

一、覆蓋率（s1_coverage.py）
- 661 個 enriched 分區中，只有 6 個有 turnover_rate 欄位，其中只有 2026-09-23 有非空值（1,087／1,323 列），與 finding 相符。
- 2026-09-23 當天，security_master 標為 ETF 的 236 列全部是 null；1,087 列股票全部有值。
- 自選 0050、00878、00885、00893 都是 null。share_capital.parquet 共 1,988 列，ETF 為 0 列。
- finding 寫的「152／152 列」我沒有重現，我數到的是 236 列；但「ETF 全部為空」這個結論成立。

二、選股路徑（s4_screening.py）
做法與 StrategyEngine._run_matrix_strategy 相同：先用 build_market_data_matrix 建矩陣，再跑 MatrixStrategyPipeline，並使用 META 的預設參數與評分。
- high_turnover_surge：2025-01-02～2026-09-22 共 418 個交易日，有進場的日數為 0（finding 也是 418 日、0）；2026-09-23 有 18 筆進場。
- low_volatility_leader，2026-09-22：406 筆進場，分數最大值 0.0（finding：406、0.0）。
- low_volatility_leader，2026-09-23：255 筆進場，其中 ETF 120 筆，ETF 分數最大值 0.0；股票分數 3.89～69.28（finding：255、120、0.0、3.9～69.3）。
- 自選 0050、00878、00893 都有進場，分數都是 0.0。
- 另外，2025-01-02～2026-09-22 共 157,815 個進場格，分數最大值是 0.0。

程式實際產出也佐證這點。data/user_data/strategy_cache.json（as_of 2026-09-23）裡，low_volatility_leader 有 135 列，其中 69 檔 ETF 全部是 0.0；66 檔股票的分數在 3.57～68.97；第一檔 ETF 排在第 67 名，也就是所有股票之後。0050、00878、00893 都是 0.0。這違反 CONTRIBUTING.md:141「不能把空值偽裝成零分」。

三、回測路徑（s2_backtest_loader.py）
- load_market_data_matrix_from_parquet 的輸入與 engine 相同：cache_root=None，instruments 用與 repo 相同的方式從 data/instruments 讀取；該目錄是空的，讀取失敗後得到空表。
- 兩個策略、end 設為 09-22 或 09-23，全部拋出 ValueError「matrix turnover_rate requires float_shares」。
- 原因有兩個。第一，pyarrow dataset 的 schema 取自第一個分區 2024-01-02，其中沒有 turnover_rate（驗證結果：False），所以不會從 parquet 讀這個欄位，改走由股本推算的路徑。第二，instruments 是空的，沒有 float_shares，於是在 matrix.py:1437 拋錯。
- backtest/strategy.py:1151 會把這個錯誤包成「回测矩阵准备失败: …」回傳給使用者。
- .backtest_matrix_cache 裡只有 axes json，沒有可重用的矩陣快取。
- finding 引用的 matrix.py:1447（多根矩陣把 turnover_rate 設為 NaN）在目前環境不會執行到。只有 instruments 將來有 float_shares 時才會走到那裡，屆時回測才會變成靜默 NaN，進而 0 筆或 0 分，所以這是潛在問題。
- 因此，「回測靜默得到 0 交易、程式沒有 fail-closed」在目前部署不成立：回測會失敗並顯示錯誤，只是錯誤訊息不易看懂，也沒有說明原因是台股沒有歷史換手率。

四、嚴重度
選股時 ETF 被評為 0 分並排在所有股票之後，以及歷史日期的選股靜默得到 0 筆或全部 0 分，這兩點都是明確的功能錯誤。維持 P2。

工作樹比對：派工前後的快照沒有差異（worktree-snapshot 結束碼 0）。所有腳本都只寫 scratchpad。
    - 證據：
      - backend/app/backtest/matrix.py:3741 — score[~universe | ~all_finite] = 0.0：任一評分因子不是有限值，該格分數就設為 0
      - backend/app/backtest/matrix.py:900 — parquet_fields 取自 dataset.schema.names；本地驗證 pyarrow dataset schema 不含 turnover_rate（第一個分區 2024-01-02 沒有這個欄位）
      - backend/app/backtest/matrix.py:1434-1437 — turnover_rate 不在 parquet_fields，而且沒有 float_shares 時，raise ValueError("matrix turnover_rate requires float_shares")
      - backend/app/backtest/matrix.py:1447 — 把 NaN 寫進 turnover_rate 的分支，只有 instruments 有 float_shares 時才會走到；目前不會
      - backend/app/backtest/engine.py:509-510 — 回測從 kline_daily_enriched 讀資料，instruments 取自 repo.get_instruments_asset；data/instruments 是空目錄
      - backend/app/backtest/strategy.py:1151 — 捕捉 ValueError 後回傳 _err(f"回测矩阵准备失败: {e}")，也就是回測會明確失敗，不是 0 交易
      - backend/app/strategy/engine.py:1174 — 選股用 build_market_data_matrix(context.history) 建矩陣，turnover_rate 的 null 變成 NaN，不會報錯
      - CONTRIBUTING.md:141 — 「缺少輸入時應返回明確的不可計算狀態，不能把空值偽裝成零分」（finding 寫的是第 139 行，實際在第 141 行）
      - 指令：uv run python …\verify-turnover\s1_coverage.py → 輸出：partitions 661；partitions_with_turnover_column 6；nonnull 只有 {"2026-09-23": 1087/1323}；pyarrow_dataset_schema_has_turnover_rate False；2026-09-23 ETF 236 列、nonnull 0，股票 1087 列、nonnull 1087；share_capital 的 ETF 列數 0
      - 指令：uv run python …\verify-turnover\s2_backtest_loader.py → 輸出：instruments 讀取失敗，退回空表；high_turnover_surge 與 low_volatility_leader 的 end 無論是 2026-09-22 或 2026-09-23，都 RAISED ValueError matrix turnover_rate requires float_shares
      - 指令：uv run python …\verify-turnover\s4_screening.py → 輸出：hts 2025-01-02..2026-09-22 共 418 日、有進場的日數 0；2026-09-23 進場 18；lvl 2026-09-22 進場 406、分數最大值 0.0；2026-09-23 進場 255、ETF 120 筆且分數最大值 0.0、股票分數 3.89～69.28；0050、00878、00893 進場且分數 0.0；歷史 157,815 個進場格的分數最大值 0.0
      - 指令：uv run python …\verify-turnover\s3_strategy_cache.py 與後續檢查 → data/user_data/strategy_cache.json（as_of 2026-09-23）：low_volatility_leader 共 135 列，ETF 69 檔分數全為 0.0 且 turnover_rate 為 null，股票 66 檔分數 3.57～68.97，第一檔 ETF 排在索引 66（在所有股票之後）；0050、00878、00893 分數 0.0
      - 與 finding 數字對照：418/0、406/0.0、255/120/0.0/3.9～69.3 都一致；「ETF 152／152 列 null」沒有重現，我在 2026-09-23 數到 236 列，全部為 null
      - 腳本位於 AUDIT_SCRATCH/work/verify-turnover/（s1～s4 與派工前後快照）；工作樹比對結果為 no change since baseline

### [P2 → P2] 原始列滾動窗口（rolling_quantile、rolling_min、rolling_max、rolling_sum）遇到缺列時，靜默抑制布林收斂與 KNN 訊號（部分成立）

- **主張**：矩陣的時間軸是全市場交易日的聯集，個股停牌或缺列時為 NaN。valid_* 系列會跳過缺列，但 rolling_quantile／rolling_min／rolling_max／rolling_sum 使用 sliding_window_view 的原始列窗口，只要窗口內有一個 NaN 就回傳 NaN：bollinger_squeeze 的門檻在缺列後 80 根內都是 NaN；lorentzian 的特徵在 60 根內被替換為 0.5 佔位值。這與 valid_shift 的註解「those rows must be treated as absent」不一致。
- **正確的定義或數值**：滾動分位數與極值只計算有效 K 棒（與 valid_rolling_* 一致），缺列不應讓訊號失效長達一個窗口
- **app 實際行為**：任何缺列都讓布林收斂失效 80 根，並讓 KNN 特徵退化為 0.5 共 60 根
- **對使用者的影響**：曾停牌或分割的標的（例如自選的 0050 在 2025-06 分割停牌）之後數個月不會出現布林收斂訊號，KNN 預測也被佔位值污染；回測少算約 8% 的進場
- **修法建議**：新增 valid_rolling_quantile（沿用 _valid_rolling_kernel 的 CSR 索引），並讓 lorentzian 與 bollinger_squeeze 改用 valid 版本的 min、max、sum 與 quantile
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/matrix.py:3287-3302 — rolling_quantile 走 _rolling_reduce（原始列窗口）
  - backend/app/backtest/matrix.py:3465-3470 — sliding_window_view 沿 axis=0
  - backend/app/backtest/matrix.py:2976-2981 — valid_shift 註解：suspended rows must be treated as absent
  - backend/app/strategy/builtin/bollinger_squeeze.py:97,105
  - backend/app/strategy/builtin/lorentzian_knn.py:112-119 — 以 rolling_min 與 rolling_max 正規化，NaN 時填 0.5
- **線上證據**：
  - （無）
- **本地計算證據**：
  - step4_debug_bs.py：2,346 檔中有 1,197 檔在區間內有缺列；0050.TWSE 缺 2025-06-11～06-17 共 5 列，width 在這 5 列為 NaN，threshold 在 2025-07-08 之後連續 NaN（例如 0050 在 2025-08-07 的 width[t-1]=0.0507、thr[t-1]=nan）
  - step3_strategies.py：bollinger_squeeze 以有效 K 棒語意獨立重寫後共 3,205 筆，app 只有 2,947 筆，差 270 筆（8.4%）；抽樣 8 筆都是 app=False
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：矩陣時間軸是全體標的交易日的聯集，缺列為 NaN。rolling_quantile、rolling_min、rolling_max 以 sliding_window_view 取原始列窗口，並用不略過 NaN 的 np.quantile、np.min、np.max 計算，窗口內只要有缺列就輸出 NaN。這違反本 repo 自己的有效 K 棒語意（valid_shift 的 docstring、matrix_feature、polars 管線），也不符合「N 期＝該標的的 N 根 K 棒」的標準定義。

實際效果：
- bollinger_squeeze 的帶寬分位門檻在缺列後 79 根有效 K 棒內為 NaN，這段期間不會產生進場訊號。
- 全期間（2024-01～2026-09）少算 387 / 5,136 筆，約 7.5%；0050 在 2025-06 分割停牌後，從 2025-06-18 到 2025-10-08 都沒有門檻。
- lorentzian_knn 的 5 個特徵在缺列後 59 根被改成 0.5，共 547,553 格、1,223 檔。

修正點：
- bollinger 內的 rolling_sum 輸入是 0/1 的有限值，不會因缺列產生 NaN，不是抑制訊號的成因。
- 新面向：資料層整日缺少上櫃資料時（kline_daily_enriched 的 2026-09-16、09-23 上櫃列數為 0），此缺陷會讓所有上櫃股約 80 根內沒有布林收斂訊號、KNN 特徵全部退化。實測 2026-09-17～09-23 上櫃進場數 app 為 0，修正後為 41。選股與 radar 候選受影響。自選股都是上市股，目前不受影響。
- 同樣的原始列 rolling 寫法也出現在 consolidation_breakout、pullback_resume、bottom_reversal、swing_trend_confirmation（未量化）。

修法：新增 valid_rolling_quantile，並讓這些策略改用 valid_rolling_min、valid_rolling_max、valid_rolling_quantile。
    - 理由：核心主張成立，但有三處細節需要修正。

1. 程式碼：
   - rolling_min、rolling_max、rolling_sum、rolling_quantile 都經過 _rolling_reduce，以 sliding_window_view 沿原始列（axis=0）取窗口，再直接呼叫 np.min、np.max、np.sum、np.quantile。這幾個函式都不會略過 NaN，窗口裡只要有一個缺列就回傳 NaN。
   - valid_rolling_* 走 _valid_rolling_kernel，會跳過無效列。
   - 矩陣時間軸是全體標的日期的聯集（_encode_axes 用 unique().sort()），缺列預設填 NaN。
   - boll_width 與 5 個 KNN 特徵都由 valid_* 計算，在缺列上本身就是 NaN。
   - 因此：bollinger_squeeze 的 threshold = rolling_quantile(width, 80)，在缺列後 79 根有效 K 棒內都是 NaN，squeezed 恆為 False。lorentzian 的 _normalise 用 rolling_min／rolling_max(60)，缺列後 59 根的特徵被填成 0.5。

2. 正確定義：
   - Wikipedia 把布林通道定義為「N-period」移動平均與標準差，N 是該標的自己的 K 棒數。
   - numpy 文件說明 quantile 遇到 NaN 回傳 NaN，nanquantile 才會略過。
   - pandas 文件說明整數窗口的 min_periods 預設等於窗口大小，觀測值不足就回傳 NaN。
   - 本 repo 的慣例也是「有效 K 棒」：valid_shift 的 docstring 寫明「those rows must be treated as absent」；matrix_feature 全部使用 valid_*；polars 管線先濾掉停牌日，再用 .over("symbol") 在個股自己的列上做 rolling。
   - 不論採「有效 K 棒」還是「日曆窗口」語意，「缺一列就讓整整 80 根輸出 NaN」都不是任何一種正確定義，而是 NaN 傳播造成的副作用。

3. 本地重算（kline_daily_enriched，2024-01-02～2026-09-23，661 個交易日，2,346 檔）：
   - 1,230 檔有內部缺列。
   - 0050.TWSE 缺 2025-06-11～06-17 共 5 列。其 threshold 從 2025-06-18 復牌日起到 2025-10-08 共 79 列為 NaN；改用有效 K 棒版本則有值（例如 2025-06-18 為 0.0642）。
   - 兩種算法都有值的地方最大差異為 0，所以差異全部來自缺列。
   - 只把 rolling_quantile 換成有效 K 棒版本：進場數由 app 的 4,749 筆變為 5,136 筆。新增的 387 筆（7.5%）只出現在修正版，app 獨有的是 0 筆。0050 在 2025-08-07 多出一筆進場，與 finding 的例子相符。
   - lorentzian 被強制改成 0.5 的特徵格共 547,553 個，涉及 1,223 檔。0050 的 rsi_14 從 2025-06-18 到 2025-09-08 共 59 根被改成 0.5。

4. 需要修正的細節：
   - (a) bollinger_squeeze 裡的 rolling_sum 輸入是 shift(breakout.astype(float32))，缺列上 breakout 為 False，也就是有限值。全市場 NaN 只有 2,346 個，每檔 1 個（第一列），所以 rolling_sum 不會造成訊號抑制，只會讓「5 根內首次突破」的窗口在缺列處涵蓋較少的有效 K 棒。title 與 fix_suggestion 把 rolling_sum 列為 bollinger 的成因並不精確。
   - (b) 0050 的 threshold NaN 是從 2025-06-18 開始，不是 2025-07-08。
   - (c) finding 的 local_evidence 內部不一致：3,205−2,947=258，不是 270。我重算的差距是 7.5%，量級與「約 8%」相符。

5. 原記錄沒有提到的新面向：整個櫃買市場缺整日資料時，影響會擴大到全市場。
   - kline_daily_enriched 在 2026-09-16 與 2026-09-23 的上櫃（TPEX）列數都是 0，taiwan/daily 的 09-16 也是 0。
   - 選股與儀表板 radar 由 run_all 以聯集時間軸建立矩陣（engine.py:1080），因此 09-16 之後 79 根內，所有上櫃股的布林收斂門檻都是 NaN。實測 2026-09-17～09-23：上櫃進場 app 為 0 筆，修正版為 41 筆；上市 64 筆對 65 筆。lorentzian 的上櫃特徵同樣全部退化為 0.5。
   - 缺資料本身應屬另一條資料完整性 finding，這條程式碼缺陷則把「缺一天」放大成「約 4 個月沒有訊號」。

6. 使用者情境：
   - 自選 9 檔都是上市股。0050 在 2025-06、3545 在 2026-02-26 各有缺列，會影響涵蓋這些日期的回測。
   - 目前（2026-09-24）的選股結果中，自選股不受影響。受影響的是全市場的回測與 radar 候選，上櫃股目前是全面受影響。

7. 同類寫法：其他內建策略也有同樣的原始列 rolling 用法，包括 consolidation_breakout:103-104、pullback_resume:97、bottom_reversal:100,136、swing_trend_confirmation:37。影響幅度未量化（推測類似）。

8. 嚴重度：結果是漏掉訊號（偽陰性），不是錯誤方向的交易，也不是資料破壞，屬於明確的功能錯誤，維持 P2。
    - 證據：
      - backend/app/backtest/matrix.py:3012-3049 — rolling_min/max/sum 走 _rolling_reduce，分別使用 np.min/np.max/np.sum
      - backend/app/backtest/matrix.py:3287-3299 — rolling_quantile 用 np.quantile（不略過 NaN）
      - backend/app/backtest/matrix.py:3465-3470 — sliding_window_view(source[:, start:stop], window_shape=window, axis=0)：原始列窗口
      - backend/app/backtest/matrix.py:3074-3098 — _valid_rolling_kernel 以 `if not valid[row, asset_id] ...: continue` 跳過缺列
      - backend/app/backtest/matrix.py:2976-2981 — valid_shift 的 docstring：suspended rows 'must treat those rows as absent rather than as observations'
      - backend/app/backtest/matrix.py:2721-2730 — 時間軸 unique_timestamps = timestamp_series.unique().sort()（聯集）；matrix.py:590-593 缺值預設 NaN
      - backend/app/backtest/matrix.py:3893-3907 — boll_upper、lower、width 由 valid_rolling_mean、valid_rolling_std 計算，缺列為 NaN
      - backend/app/strategy/builtin/bollinger_squeeze.py:97-98 — threshold = rolling_quantile(width, lookback, percentile)；squeezed = shift(width,1) <= shift(threshold,1)
      - backend/app/strategy/builtin/bollinger_squeeze.py:105 — rolling_sum 的輸入是 shift(breakout.astype(float32),1)，本地實測全市場只有 2,346 個 NaN（每檔 1 個），不受缺列影響
      - backend/app/strategy/builtin/lorentzian_knn.py:112-119 — _normalise 用 rolling_min、rolling_max，非有限值回填 0.5
      - backend/app/indicators/pipeline.py:419-440、979-1009 — polars 管線先濾掉停牌日，再以 .over('symbol') 在個股自己的列上做 rolling（有效 K 棒語意）
      - backend/app/strategy/engine.py:1080-1083 — 選股 run_all 以 build_market_data_matrix(shared_history) 建立聯集時間軸矩陣
      - 線上：https://numpy.org/doc/stable/reference/generated/numpy.nanquantile.html — 'Compute the qth quantile of the data along the specified axis, while ignoring nan values'；文件範例中 np.quantile(a, 0.5) 回傳 nan
      - 線上：https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.rolling.html — min_periods：'Minimum number of observations in window required to have a value; otherwise, result is np.nan. ... For a window that is specified by an integer, min_periods will default to the size of the window.'
      - 線上：https://en.wikipedia.org/wiki/Bollinger_Bands — 'Bollinger Bands consist of an N-period moving average (MA) ... Typical values for N and K are 20 days and 2'（窗口以該標的的期數計）
      - 線上：https://chartschool.stockcharts.com/.../bollinger-bandwidth — BandWidth = ((Upper Band - Lower Band) / Middle Band) * 100；Squeeze 指 BandWidth 落在該證券歷史低檔
      - 線上：https://zh.wikipedia.org/wiki/元大台灣卓越50證券投資信託基金 — 2025 年『每一受益權單位分割為四個新單位』（本地資料：2025-06-10 close 188.65 → 06-18 47.57）
      - 本地 s2_verify.py（AUDIT_SCRATCH/work/v-rollgap-codedef2/s2_out.txt）：assets 2346，assets_with_internal_gap 1230；0050 缺 2025-06-11/12/13/16/17；0050 thr_app 為 NaN 而 thr_valid 有值共 79 列（2025-06-18～2025-10-08）；兩者都有值時 max abs diff 0.0
      - 本地 s2_verify.py：bollinger entries app 4749、fixed_quantile_only 5136、only_fix 387、only_app 0；0050 在修正版多一筆 2025-08-07；lorentzian 被強制改成 0.5 的特徵格 547,553 個，涉及 1,223 檔；0050 rsi_14 的 59 根（2025-06-18～2025-09-08）
      - 本地 s3_gapdates.py：缺列最多的日期為 2026-09-16（1,022 檔），該日 kline_daily_enriched 的 TPEX 列數為 0；2026-09-23 的 TPEX 列數也是 0
      - 本地 s4_latest.py：2026-09-17～09-23 布林收斂進場，app 上櫃 0 筆、修正版上櫃 41 筆；上市 app 64、修正版 65
      - 自選股缺列：3545.TWSE 缺 2026-02-26 一列、0050.TWSE 缺 5 列，其他 7 檔 0 列
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：矩陣時間軸是全部日期的聯集，缺列處為 NaN。rolling_quantile、rolling_min、rolling_max 用原始列窗口（sliding_window_view），窗口內只要有一格 NaN 就回傳 NaN。

bollinger_squeeze：門檻 rolling_quantile(boll_width, 80) 從缺口起到缺口後第 79 根有效 K 棒都是 NaN。例如 0050 的門檻在 2025-06-11 到 2025-10-08 之間是 NaN（有效 K 棒 79 根）；缺口後第 80 根會再用一次缺口前的舊門檻。2025-01～2026-09-22 的全市場回測中，改用有效 K 棒 quantile 會從 2,947 筆增為 3,209 筆，也就是 app 單向少算 262 筆（修正後總數的 8.2%）；自選股中 0050 少了 2025-08-07 這筆。rolling_sum 不在失效機制內：breakout 在缺列處是 0.0，不是 NaN，改成有效 K 棒視窗對結果沒有影響。

lorentzian_knn：rolling_min 和 rolling_max 讓缺口後 59 根有效 K 棒的五個特徵全部退化為 0.5，全市場共 8.2% 的有效特徵格被替換。進場訊號會雙向偏移：只在 app 出現 3,536 筆、只在修正版出現 2,520 筆，影響 533 檔；自選股 3545 多出 16 筆，0050 少 3 筆。

缺列的主要來源不是停牌或分割，而是本地 2026-09-16 分區缺少整個上櫃市場：官方 TPEx 資料顯示當日有交易，而 1,197 檔有缺列的標的中有 660 檔唯一的缺列就是這天。因此目前約 1,000 檔上櫃股的布林收斂門檻，在 2026-09-16 之後 80 根內都會是 NaN；回測和選股都受影響。
    - 理由：我用自己的腳本重算：以 polars 讀 kline_daily_enriched 2025-01-01～2026-09-22，丟進純函式 build_market_data_matrix 建矩陣，再跑 app 的策略並和兩種對照組比較。兩種對照組分別是「只把 rolling_quantile 換成有效 K 棒版」，以及完全獨立的 pandas 逐檔 dropna 重寫。finding 的核心機制與主要數字都能逐一重現：
- 2,346 檔中有 1,197 檔有區間內缺列。
- 0050 缺 2025-06-11～06-17 共 5 列。
- 0050 在 2025-08-07 的 width[t-1]=0.050717827、thr[t-1]=NaN。
- app 產生 2,947 筆，獨立重寫產生 3,205 筆，對稱差 270 筆（264+6），占 8.42%。
- lorentzian 的 0050 有 59 根 K 棒被換成 0.5。

以下幾點要修正：
(1) rolling_sum 在 bollinger_squeeze 裡不會出現 NaN。breakout 在缺列處轉成 0.0，所以 rolling_sum 的輸入全矩陣只有第 0 列是 NaN（2,346 格）。改成有效 K 棒視窗後，進場筆數仍是 3,209，差 0 筆，因此 rolling_sum 不是抑制布林訊號的機制。
(2) 能歸因於此缺陷的差額是 262 筆，不是 270 筆。只替換 quantile 後，app 少了 262 筆，多出的是 0 筆；其中 261 筆落在 thr_app 為 NaN 的列，另 1 筆是缺口後第一列取到缺口前的舊門檻。270 裡另外 8 筆是 float32 與 float64 的精度雜訊，與此缺陷無關。
(3) KNN 不是單向抑制，而是雙向扭曲。app 產生 68,587 筆進場，改用有效 K 棒正規化後是 67,571 筆；只出現在 app 的 3,536 筆，只出現在修正版的 2,520 筆，共影響 533 檔。
(4) 缺列的主因不是停牌或分割。有 660 檔唯一的缺列日是 2026-09-16；該日本地分區只有 TWSE 1,319 列、完全沒有 TPEX，但官方 TPEx 參考資料顯示 6488 和 8069 當天都有成交。262 筆遺失訊號中有 199 筆是 TPEX 標的，42 筆落在 2026-09-16 之後。這代表目前約 1,000 檔上櫃股的布林門檻會在之後 80 根內持續是 NaN；選股執行（app/strategy/engine.py 的 _run_matrix_strategy）同樣走 compute_signals，所以也受影響。

嚴重度維持 P2。我只寫入 scratchpad，工作樹前後的 git status 相同，numba 快取也導向 scratchpad。
    - 證據：
      - 程式碼：backend/app/backtest/matrix.py:3287-3302，rolling_quantile 走 _rolling_reduce；matrix.py:3436-3470 以 sliding_window_view 沿 axis=0 取原始列窗口；matrix.py:3012-3049 的 rolling_min、rolling_max、rolling_sum 也走同一條路徑；matrix.py:2969-2981 是 valid_shift 的註解（those rows must be treated as absent）
      - 程式碼：backend/app/backtest/matrix.py:958-964,1035-1044，矩陣先以 NaN 初始化、時間軸取全部日期；matrix.py:3895-3904，boll_width 以 valid_* 計算，缺列處為 NaN
      - 玩具測試（s1_out.txt）：第 4 列設為 NaN 後，rolling_quantile(3,0.2) 輸出 [nan nan 0.4 1.4 nan nan nan 5.4 6.4 7.4]；rolling_min、rolling_max、rolling_sum 同樣連續 3 格 NaN
      - s1_gaps.py 2025-01-01 2026-09-22：matrix shape (418, 2346)；有區間內缺列的標的 1197/2346，缺列共 10,784 列；0050 缺 ['2025-06-11','2025-06-12','2025-06-13','2025-06-16','2025-06-17']；3545 缺 ['2026-02-26']；自選其餘 7 檔無缺列
      - s1_gaps.py：0050 的 thr NaN 區段為 2025-06-11..2025-10-08（原始 84 列，其中有效 K 棒 79 根）；2025-08-07 的 width[t-1]=0.050717827、thr[t-1]=nan；2025-10-09 起 thr 恢復為 0.0637
      - s1_gaps.py：bollinger 的 rolling_sum 輸入 valid_shift(breakout.astype(float32),1) 在全矩陣只有 2,346 格 NaN（各檔第 0 列），缺列處為 0.0 而不是 NaN
      - s2_boll.py：APP entries 2947；我複製的策略邏輯與 app 完全相同（True）；只換成有效 K 棒 quantile 後為 3209（only_in_fix=262、only_in_app=0，其中 thr_app 為 NaN 的有 261 筆）；再把首根突破窗口也換成有效 K 棒版，仍是 3209
      - s2_boll.py：獨立 pandas 逐檔重寫得到 3205 筆，與 app 相比只在獨立版 264 筆、只在 app 6 筆（對稱差 270），與 finding 的 3,205／2,947／270 一致
      - s2_boll.py：自選股中 0050 在 app 只有 ['2025-06-10','2026-09-21']，修正版多出 2025-08-07；其餘 8 檔兩版相同；共 211 檔至少少了 1 筆訊號
      - s5_breakdown.py：1,197 檔缺列標的中，660 檔唯一的缺列日是 2026-09-16，另有 537 檔含其他缺列日（TWSE 186、TPEX 351）；262 筆遺失訊號中 TPEX 199、TWSE 63，其中 42 筆在 2026-09-16 之後；有效 K 棒日中 app 門檻為 NaN、但有效 K 棒版可算出門檻的共 76,424／695,395（11.0%）
      - s4_hole.py：本地 date=2026-09-16 分區有 1319 列，全部是 TWSE；前一日 2026-09-15 有 TPEX 987 列、TWSE 1324 列。官方參考 reference/csv/tpex_trading_stock_202608_202609.csv 顯示 6488 在 2026-09-16 收盤 937.0、8069 收盤 148.0，但本地 6488.TPEX 和 8069.TPEX 只有 09-15 與 09-17 兩天
      - s3_lkn.py：五個特徵中，有效值卻被換成 0.5 的格數合計 371,376／4,553,343（8.2%）；0050 每個特徵各 59 列（2025-06-18..2025-09-08）
      - s3_lkn.py：LKN 進場 app 68587、有效 K 棒正規化版 67571，只在 app 3536、只在修正版 2520，共 533 檔有差異；自選股 3545.TWSE 在 app 多 16 筆（2026-04-24..2026-05-26），0050 少 3 筆（2025-10-01、2025-10-14、2026-04-09）
      - 0050 分割：本地 close 在 2025-06-10 為 188.65、2025-06-18 為 47.57，比值約 3.97。https://zh.wikipedia.org/wiki/元大台灣卓越50證券投資信託基金 原文：「2025年，元大投信宣布進行元大台灣50 ETF（0050）自成立以來的首次拆股。該次拆股以『每一受益權單位分割為四個新單位』（1拆4）方式進行」。該頁沒有停止或恢復交易日期；WebSearch 額度已用完，DuckDuckGo 遇到 CAPTCHA，Bing 查無相關結果，所以 6/11～6/17 的停止交易日期未能線上查證
      - 範圍外但同機制（推測，未量化）：bottom_reversal.py:100,136、consolidation_breakout.py:103-104、pullback_resume.py:97、swing_trend_confirmation.py:37 也對含 NaN 缺列的 market.high、low、close 使用原始列 rolling_min／rolling_max
      - 腳本與輸出：AUDIT_SCRATCH/work/verify-rollgap-data/（s1～s5 腳本與 *_out.txt）；git_status_before.txt 與 git_status_after.txt 相同

### [P3 → P2] lorentzian_knn 與 TradingView「Lorentzian Classification」原版有多處設計差異，且 38% 的近鄰含 0.5 佔位特徵（部分成立）

- **主張**：docstring 自稱依已發表的演算法描述「clean-room」實作，但只承認特徵不同。實際上，標籤方向、正規化、近鄰搜尋、濾網、進場條件（原版要求訊號翻轉）與訓練窗都不同；另外，暖機期與缺列產生的全 0.5 特徵列也被當成有真實標籤的候選鄰居。
- **正確的定義或數值**：文件說明所有與原版的差異；佔位特徵列不應成為候選鄰居
- **app 實際行為**：使用者拿 TradingView 對照時，訊號會系統性不同；近鄰被佔位值污染，同距離時結果由實作細節決定
- **對使用者的影響**：研究類策略的可解釋性與可重現性下降；不會造成資料洩漏
- **修法建議**：在 META 與 docstring 列出差異（標籤為前瞻 4 根、正規化方式、濾網與進場規則）；把 _normalise 產生佔位值的列從 usable 候選中排除
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/builtin/lorentzian_knn.py:7-10 — 「Clean-room implementation from the published algorithm description ... same family, not a port」
  - backend/app/strategy/builtin/lorentzian_knn.py:31 — 特徵為 rsi_14、rsi_9、kdj_k、boll_position、macd_hist_pct
  - backend/app/strategy/builtin/lorentzian_knn.py:162 — argpartition 取 k 近鄰（完整 KNN）
  - backend/app/strategy/builtin/lorentzian_knn.py:173-178 — atr_pct 與 10 根前比較；MA20 5 根斜率；出場為 prediction<0 或收 < MA20
  - backend/app/strategy/builtin/lorentzian_knn.py:119 — 非有限值填 0.5
- **線上證據**：
  - https://gist.github.com/itsXactlY/450618a1ab3a85b450b22c8e188c7a1d — 原版標籤 `y_train_series = src[4] < src[0] ? direction.short : src[4] > src[0] ? direction.long : direction.neutral`；近鄰條件 `if d >= lastDistance and i%4`；濾網 `ml.filter_volatility(1, 10, ...)`、`ml.regime_filter(ohlc4, ...)`；進場 `isNewBuySignal = isBuySignal and isDifferentSignalType`
  - https://www.tradingview.com/pine-script-docs/language/operators/ — `close[1]` 為前一根，所以 src[4] 是 4 根之前的價格
  - https://www.tradingview.com/script/WhBzgfDu-Machine-Learning-Lorentzian-Classification/ — 預設特徵 RSI、WT、CCI、ADX，另有 Use Volatility／Regime／ADX Filter 與 Trade with Kernel
  - https://www.tradingview.com/script/ia5ozyMF-MLExtensions/ — normalize() 用於「unbounded range」、rescale() 用於有界到有界
- **本地計算證據**：
  - step5b_diag.py：自選股 t≥120 的 2,682 列中，有 1,028 列（38%）的 8 個近鄰含全 0.5 的佔位鄰居；獨立實作不一致的 90 列中有 88 列是距離完全相同、由 argpartition 任意選取
  - step5_lorentzian.py：把未來擾動後，T 以前沒有任何差異（沒有洩漏）
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：lorentzian_knn 的正規化 `_normalise`（lorentzian_knn.py:113-119）用的是不會跳過缺漏列的 rolling_min/rolling_max，視窗內只要有 NaN，就會回退成 0.5。因此會產生兩類全 0.5 特徵列：資料窗最前面 59 根，以及任何缺 1 根之後的 60 根。這些列帶著真實的 ±1 標籤進入候選池（9 檔自選股共 166 個候選列，標籤 −1/0/+1 為 79/3/84）。

在 9 檔自選股 t≥120 的 2,682 列中，有 876 列（32.7%）的 8 個近鄰含全 0.5 列。原稽核的 1,028（38%）來自 `market.symbols[:9]`，那是 4 檔自選加 5 檔隨機股，不是自選股。這 876 列都會在第 k 名邊界出現距離並列，結果由 argpartition 任意決定。改用 stable 排序時，9 檔自選股的 prediction 會有 210 列不同、entry 10 列、exit 12 列不同；30 檔面板則有 79～84 列訊號不同。

更嚴重的是，缺列之後 60 根內查詢列本身就是全 0.5，prediction 等於 8 個任意佔位標籤的總和，但仍會發出進場訊號。例子：3545 在 2026-02-26 沒有成交，之後在 2026-04-23～05-26 產生 17 個這類雜訊進場；0050 缺 2025-06-11～06-17 的 5 列，之後 t≥120 有 46 列受影響。

沒有前瞻洩漏：三個切點下，T 以前的差異都是 0。與 TradingView 原版的差異確實存在。其中「非精確移植、正規化與濾網改寫、2000 根回看」已記錄在 docs/radar-strategies-validation.md:101-107；標籤方向（原版看過去 4 根且上漲記為 short）、完整與近似 KNN、訊號翻轉才進場、持有 4 根出場這幾項，在 docstring、META 與文件中都沒有說明。

建議修正：
- 正規化改用 valid_rolling_min/max，或排除含 NaN 的視窗。
- 全佔位的列既不當候選，也不當查詢；查詢列是佔位時，prediction 設為 0。
- 在 docstring 補上未記錄的差異。
    - 理由：我用 app 自己的純計算函式（build_market_data_matrix、matrix_feature、LorentzianKnnMatrixStrategy.compute_signals）直接讀 kline_daily_enriched 的 parquet 重算。資料窗為 2025-01-01～2026-09-22，共 418 根，t≥120 時每檔 298 列。以下逐項對照 finding 的說法。

(1) 「自選股 1,028/2,682 列（38%）的近鄰含全 0.5 佔位列」：1,028 能精確重現，但算的不是自選股。原腳本 step5b_diag.py:43 迴圈跑的是 `market.symbols[:9]`，在 30 檔混合面板裡依字母排序，實際是 0050、00878、00885、00893、1109、1418、1541、1604、2114，也就是 4 檔自選加 5 檔隨機股。真正的 9 檔自選股：依回測實際管線（rsi_14 在矩陣層計算）是 876/2,682（32.7%）；用 parquet 內存的 rsi_14 是 873/2,682（32.6%）。30 檔合計 3,183/8,940（35.6%）。
- 佔位候選列確實帶有真實標籤：9 檔自選共有 166 個全 0.5 候選列，標籤 −1 有 79 個、0 有 3 個、+1 有 84 個。
- 876 列全部都在第 k 名邊界出現距離完全相同的並列，而且並列集合都含佔位列。

(2) 「不一致的 90 列中有 88 列是距離並列」：90 這個數字是拿原稽核自寫的特徵實作去比對得到的，無法以 app 特徵重現。改用 app 的同一套特徵，只把 argpartition 換成 stable argsort，就足以讓結果不同：
- 30 檔面板中，進出場訊號不同的有 84 列（parquet 存的 rsi_14）或 79 列（管線計算的 rsi_14）。
- 只看 9 檔自選股、t≥120：prediction 不同 210 列，entry 不同 10 列，exit 不同 12 列。
- 結論方向成立：同距離時，結果取決於選取演算法的實作細節。

(3) 沒有前瞻洩漏：我在 2025-09-11、2026-02-06、2026-06-30 三個切點把 T 之後的價量乘上 0.7～1.3 的隨機倍數。兩種欄位設定下，T 以前的 entry 與 exit 差異都是 0，T 之後則有 155～925 列不同。確認成立。

(4) 與原版的設計差異：從 gist 原始碼逐項核對，成立。但「文件未說明」只對 docstring 和 META 成立。docs/radar-strategies-validation.md:12-13、101-107 已說明三件事：不是精確移植、特徵正規化與 volatility/regime 濾網改用等價實作、原版的 2000 根回看比本專案資料長。真正沒有記錄的差異是：
- 原版標籤是看過去 4 根，而且上漲記為 short；app 是看未來 4 根的方向。
- 原版是近似近鄰搜尋，條件為 `d >= lastDistance and i%4`；app 是完整 KNN。
- 原版進場要求訊號翻轉。
- 原版出場規則是持有滿 4 根。

(5) 原記錄沒提到、影響更大的新面向：
- 根因：`_normalise` 用的是不會跳過缺漏列的 rolling_min／rolling_max（lorentzian_knn.py:113-114）。矩陣層其實已有會跳過缺漏列的 valid_rolling_min／max（matrix.py:3165、3189），這裡沒有使用。所以只要缺 1 根，之後 60 根的全部特徵都會變成 0.5。
- 此時連查詢列本身都是全 0.5，它的 8 個近鄰都是距離為 0 的佔位列，由 argpartition 任意挑出；prediction 只是這 8 個標籤的總和，是雜訊。
- 自選股實例一：3545 在 2026-02-26 沒有成交。本地原始資料 taiwan/daily 顯示該日 volume=0、沒有價格。之後 60 列（t≥120）查詢特徵全是 0.5。
- 自選股實例二：0050 在 2025-06-11 到 06-17 缺 5 列（推測是股票分割停止交易，網路搜尋額度已用完，無法查證），之後 t≥120 有 46 列全是 0.5。
- 3545 在 2026-04-23 到 05-26 期間，從全佔位的查詢列產生了 17 個進場訊號。9 檔自選股在 t≥120 共有 421 個進場訊號。

finding 把影響寫成「只是可解釋性下降」，低估了。缺列後 60 根內，策略會持續根據雜訊發出訊號，屬於明確的功能錯誤，所以嚴重度調為 P2。
    - 證據：
      - 腳本：AUDIT_SCRATCH/work/verify-lknph/repro.py、repro2.py、repro3.py；執行方式：cd backend && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. uv run python <腳本>
      - repro3.py 輸出：market.symbols[:9] = ('0050.TWSE','00878.TWSE','00885.TWSE','00893.TWSE','1109.TWSE','1418.TWSE','1541.TWSE','1604.TWSE','2114.TWSE')。前 9 檔（原稽核口徑）：1028 of 2682；真正自選股：873 of 2682（parquet 存的 rsi_14）、876 of 2682（管線計算的 rsi_14）；30 檔：3183 of 8940
      - 原稽核腳本 A09\step5b_diag.py:43 `for a, s in enumerate(market.symbols[:9]):`：以排序後的前 9 檔冒充自選股
      - repro.py 輸出（9 檔自選股，管線欄位）：rows t>=120: 2682；含全 0.5 鄰居：876（32.7%）；第 k 名邊界有並列：876，且並列集合都含全 0.5 候選列；app 與 stable 排序的 prediction 不同：210 列；entry 不同 10 列、exit 不同 12 列；all-0.5 candidate rows: 166，標籤 −1/0/+1 = 79/3/84
      - repro3.py 輸出（30 檔）：app 與 stable 並列處理不同 → entry diff=36、exit diff=52、entry 或 exit 不同的列 84（parquet 存的 rsi_14）；管線 rsi_14 則為 79 列
      - repro3.py 前瞻測試：T=2026-06-30／2026-02-06／2025-09-11，rows<=T 的 entry diff=0、exit diff=0（兩種欄位設定都是），T 之後 entry diff 為 155～925
      - repro2.py 輸出：0050.TWSE 的 NaN close 在 2025-06-11～2025-06-17（5 列）；3545.TWSE 的 NaN close 在 2026-02-26；查詢列本身全 0.5 的列（t>=120）：106（0050 46 列、3545 60 列）；全佔位查詢列上的進場訊號：17 of 421，全在 3545，日期 2026-04-23～2026-05-26
      - 本地原始資料 data\taiwan\daily\date=2026-02-26：3545.TWSE 的 open/high/low/close 為 None、volume=0.0；kline_daily_enriched 該日沒有 3545 列
      - backend/app/strategy/builtin/lorentzian_knn.py:113-119：_normalise 用 rolling_min/rolling_max（np.min 遇 NaN 會傳遞 NaN），非有限值一律填 0.5
      - backend/app/backtest/matrix.py:3012-3029：rolling_min/max 透過 _rolling_reduce（3436-3471）以 sliding_window_view 加 np.min 計算，不跳過缺漏列；matrix.py:3165、3189 已有 valid_rolling_min/valid_rolling_max 但未使用
      - backend/app/strategy/builtin/lorentzian_knn.py:142-145：標籤為 sign(close[t+4]-close[t])（前瞻）；:148-151：候選取 stride 4 且 j+horizon<=t；:162：argpartition 完整 KNN
      - https://gist.github.com/itsXactlY/450618a1ab3a85b450b22c8e188c7a1d（jdehorty 原版 MPL-2.0）原文：`y_train_series = src[4] < src[0] ? direction.short : src[4] > src[0] ? direction.long : direction.neutral`；`if d >= lastDistance and i%4`，超過 k 個時 lastDistance := distances[k*3/4]；`ml.filter_volatility(1, 10, ...)`、`ml.regime_filter(ohlc4, ...)`；`isNewBuySignal = isBuySignal and isDifferentSignalType`；`isHeldFourBars = barsHeld == 4`
      - https://www.tradingview.com/script/ia5ozyMF-MLExtensions/ 原文：normalize「Rescales a source value with an unbounded range to a target range」；rescale「...with a bounded range to anther bounded range」
      - docs/radar-strategies-validation.md:12-13、101-107：已記載「非原指標的精確移植」「特徵正規化方法、volatility/regime 濾網判定式…改用本專案既有指標的等價實作」「遠短於該演算法設計的 2000 根回看」；沒有記載標籤方向、訊號翻轉進場、近似與完整 KNN、出場規則的差異

### [P3 → P3] 布林通道以樣本標準差（n−1）計算，與 Bollinger 規定的母體標準差（n）不同；收斂定義為自訂變體（部分成立）

- **主張**：matrix.py 與 enriched 都用 ddof=1，Bollinger 規定分母為 n。σ 因此放大 √(20/19)=1.026 倍，上軌偏高，突破次數變少。收斂的定義是 80 根的第 20 百分位，而不是 StockCharts 所說的「near the low end of its six-month range」。
- **正確的定義或數值**：依原作者定義使用母體 σ，或在 UI 註明採用樣本 σ
- **app 實際行為**：使用樣本 σ，突破判定略嚴
- **對使用者的影響**：與 TradingView 等採母體 σ 的平台比對時，上下軌略有差異，突破訊號少約 8%；收斂分位數判定不受影響（兩者等比例）
- **修法建議**：布林的 std 改為 ddof=0（enriched 與 matrix 要同步），或在欄位說明中寫明口徑
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/matrix.py:3895 — valid_rolling_std(..., 20, ddof=1)
  - backend/app/indicators/pipeline.py:417 — pl.col("close").rolling_std(20)（polars 預設 ddof=1）
  - backend/app/strategy/builtin/bollinger_squeeze.py:31-44,97
- **線上證據**：
  - https://en.wikipedia.org/wiki/Bollinger_Bands — 「Since Bollinger Bands use the population method of calculating standard deviation, the proper divisor for the sigma calculation is n, not n − 1.」（引用 Bollinger On Bollinger Bands – The Seminar, DVD I）
  - https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/bollinger-band-squeeze — 「Ideally, BandWidth should be near the low end of its six-month range.」
- **本地計算證據**：
  - step1_official.py：以官方資料計算，母體與樣本上軌的絕對差最大 19.20 元（2454，相對 0.39%），BandWidth 相對差 2.60%
  - step12_bb_pop.py：全市場 2025-01～2026-09-22，母體 σ 的 close>上軌共 56,105 根、樣本 σ 為 51,374 根（少 8.4%）；布林收斂進場 3,255 筆對 3,205 筆
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：回測矩陣（matrix.py:3895，ddof=1）和批次 enriched（pipeline.py:417，polars 預設 ddof=1）的布林 σ 用樣本標準差，Bollinger 原始定義用母體標準差（除以 n）。σ 和 BandWidth 因此固定放大 √(20/19)，約 2.598%。官方資料下上軌最大差 19.20 元（2454，2026-09-14，0.39%）。全市場 2025-01～2026-09-22 在完整暖機下，close>上軌 母體 58,163 對樣本 53,259，少 8.43%。finding 的 56,105／51,374 是在沒有暖機、2025 年頭 19 根沒有布林值的條件下算出來的。收斂分位數判定幾乎不受影響（186,571 根中只有 1 根不一致），但布林收斂策略的進場是母體 3,612 對樣本 3,553，淨少 1.6%；其中 223 筆（約 6%）的進場日不同，因為首根突破窗口會推移進場日。finding 寫的 3,255／3,205 無法重現。另有一個 finding 沒提到的問題：盤中增量路徑 compute_enriched_today（pipeline.py:1524-1531、1669）用母體 σ，突破判定用 >=；批次與回測用樣本 σ 加 >，同一個系統混用兩種口徑。即時行情開啟時，同一根 K 棒盤中和盤後的布林上下軌與 signal_boll_breakout_upper 會不一致。例如 2379 在 2026-09-22 收盤 755：盤中上軌 754.23，判為突破；盤後上軌 755.10，判為未突破。收斂條件『80 根的第 20 百分位』確實是自訂變體，不是 StockCharts 所說的「near the low end of its six-month range」。『TradingView 採用母體 σ』無法線上核實，屬推測。建議統一口徑：批次、矩陣與盤中三條路徑用同一個 ddof 和同一個比較運算子，並在欄位說明寫明採用的口徑。
    - 理由：核心說法成立：回測矩陣和批次 enriched 的布林 σ 都用樣本標準差（ddof=1），Bollinger 原始定義用的是母體標準差（除以 n）。用官方資料重算的數值也逐一重現：上軌絕對差最大 19.2025 元（2454，2026-09-14，相對 0.39%），BandWidth 相對差固定是 2.598%，等於 √(20/19)−1。全市場 close>上軌在 ddof=0 時比 ddof=1 多 8.43%。收斂分位數判定幾乎不受影響：186,571 根收斂 K 棒中只有 1 根不一致，原因是 float32 捨入。

有三處需要修正或補充。
(1) 「enriched 用 ddof=1」不完整。盤中增量路徑 compute_enriched_today 用 sq_sum/20 − mean² 計算，這是母體變異數（ddof=0），突破判定也改用 >=；批次路徑則是 ddof=1 加 >。同一個系統因此混用兩種口徑，同一根 K 棒盤中和盤後的突破狀態會翻轉。log 顯示增量路徑確實在跑，2026-09-23 13:29 還有「enriched 增量」紀錄。已落盤的 1,379,045 列布林值全部符合 ddof=1（0 列符合 ddof=0），所以盤中的母體 σ 值收盤後會被覆寫。實例：自選股 2379 在 2026-09-22 收盤 755，盤中公式的上軌是 754.2267，判為突破；批次公式的上軌是 755.0977，判為未突破。這是 finding 沒提到的新面向。
(2) finding 的 close>上軌筆數（56,105／51,374）只在「資料從 2025-01-01 才開始載入、沒有暖機」時才重現（我算出 56,098／51,373）。這樣做會讓 2025 年頭 19 根 K 棒沒有布林值。改用 2024 年起的完整暖機，筆數是 58,163／53,259，比例同樣是 8.43%。
(3) 布林收斂進場數 3,255／3,205 用實際策略程式碼無法重現。在完整暖機下，母體 σ 是 3,612 筆、樣本 σ 是 3,553 筆，淨差 1.63%。不暖機時是 2,990／2,947；暖機 60 根時是 3,208／3,159。另外兩種口徑的進場不是包含關係：兩者都有 3,471 筆、只有樣本 σ 有 82 筆、只有母體 σ 有 141 筆，共 223 筆（約 6%）的進場日不同，原因是「首根突破」窗口會把進場日往前或往後推移。所以「突破訊號少約 8%」只適用於原始的 close>上軌訊號（boll_breakout 策略與 signal_boll_breakout_upper）；收斂策略淨少約 1.6%，約 6% 的進場時點不同。

TradingView 採用母體 σ 這點無法線上核實：Pine 參考手冊是動態頁面，抓不到內容；TradingView 的 BB 說明頁沒寫 σ 口徑。這點標為推測。StockCharts 所說的「near the low end of its six-month range」已核實，程式碼的 80 根第 20 百分位確實是自訂變體。

嚴重度維持 P3。盤中與盤後口徑不一致的問題建議另開一條紀錄評估。
    - 證據：
      - backend/app/backtest/matrix.py:3895 — deviation = valid_rolling_std(market.close, close_valid, 20, ddof=1)（函式預設是 ddof=0，見 matrix.py:3242，這裡明確指定 1）
      - backend/app/indicators/pipeline.py:417 — pl.col("close").rolling_std(20)（polars 預設 ddof=1）；:449 boll_upper = ma20 + 2*_boll_std；:657 signal_boll_breakout_upper = close > boll_upper
      - 新面向：backend/app/indicators/pipeline.py:1524-1531（compute_enriched_today）— boll_var = boll_sq_sum/20 - boll_ma**2，是母體變異數（ddof=0）；:1669 signal_boll_breakout_upper = close >= boll_upper（用 >=，批次路徑用 >）；呼叫端 backend/app/services/quote_service.py:1681（盤中 _flush_live_enriched）
      - data/backend.log — 「2026-09-23 13:29:06,480 ... enriched 增量: 1 只, 2026-09-23」，增量路徑確實在跑（backend.log 共 688 筆，.1 有 2721 筆）
      - s1_official.py（AUDIT_SCRATCH/work/verify-bbpop/）：官方 TWSE STOCK_DAY 與 TPEx 資料，2026-08～09-23，共 228 根有完整 20 根窗口 → 2454 在 2026-09-14 的 up0=4926.4217、up1=4945.6241、差 19.2025（相對 0.0039）；12 檔的 bw_rel 全部是 0.0260（理論值 √(20/19)−1=0.025978）。finding 的數字是 19.20／0.39%／2.60%，全部重現
      - s1_official.py：官方資料中唯一翻轉的樣本是 2379 在 2026-09-22：close=755、母體上軌 754.2267、樣本上軌 755.0977
      - s2_local.py：kline_daily_enriched 共 1,423,597 列（2024-01-02～2026-09-23）；已落盤的 (boll_upper−boll_lower)/4 有 1,379,045／1,379,045 列符合 ddof=1，符合 ddof=0 的是 0 列；包括 2026-09-23 分區，中位數比值 1.025978
      - s2_local.py／s3_matrix.py（完整暖機，2025-01-02～2026-09-22，418 根 K 棒）：close>上軌 母體=58,163、樣本=53,260（polars）／53,259（matrix），少 8.43%；finding 是 56,105／51,374
      - s4_variants.py 變體 A（只載入 2025-01-01 起的資料，沒有暖機）：close>上軌 母體=56,098、樣本=51,373，才與 finding 的 56,105／51,374 吻合（差 7 與 1）
      - s3_matrix.py（實際的 BollingerSqueezeMatrixStrategy.compute_signals，ddof 以 monkeypatch 切換）：收斂進場 樣本=3,553、母體=3,612；兩者都有 3,471 筆、只有樣本 σ 有 82 筆、只有母體 σ 有 141 筆；收斂條件不一致 1／186,571；寬度比值範圍 1.025932～1.026010。finding 的 3,255／3,205 無法重現（s4/s5：不暖機 2,990／2,947，暖機 20 根 3,104／3,057，暖機 60 根 3,208／3,159，暖機 80 根 3,434／3,381，暖機 100 根以上 3,611～3,612／3,552～3,553）
      - s3_matrix.py：自選股進場差異 2 筆：00893.TWSE 2026-01-16 只有母體 σ 進場、2026-01-22 只有樣本 σ 進場（進場日被推移，不是單純消失）
      - s2_local.py：2025-01～2026-09-22 期間，自選 9 檔有 29 根 K 棒『母體 σ 判為突破、樣本 σ 判為未突破』，例如 2454 2026-05-11 close=3880、樣本上軌 3880.1688
      - s6_live_formula.py：照 pipeline.py:1524-1529 原式計算 2379 在 2026-09-22 的盤中上軌 = 754.2267（close>=upper → True），批次上軌 = 755.0977（close>upper → False）
      - https://en.wikipedia.org/wiki/Bollinger_Bands — Note 2：「Since Bollinger Bands use the population method of calculating standard deviation, the proper divisor for the sigma calculation is n, not n − 1.」（已核實）
      - https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/bollinger-band-squeeze — 「BandWidth should be near the low end of its six-month range.」（已核實）
      - https://www.tradingview.com/pine-script-reference/v5/#fun_ta.stdev 是動態頁面，抓不到內容；https://www.tradingview.com/support/solutions/43000501840-bollinger-bands-bb/ 沒寫 σ 口徑。『TradingView 採用母體 σ』是推測，未經核實

## 確認正確的項目

- **官方 OHLC 與 enriched（自選 9 檔，2026-08-03～09-23，共 342 列）**：用 polars 讀取 kline_daily_enriched，與 TWSE STOCK_DAY 參考 CSV 做 join；open、high、low、close 的最大絕對差都是 0.0（成交量差異見 findings）
  - 來源：AUDIT_SCRATCH/work/A09/step1_official.py
  - 來源：reference\csv\twse_stock_day_202608_202609.csv
- **boll_upper、boll_lower、ma20（app 實作為 MA20 ± 2×樣本 std）**：以官方收盤價獨立計算 SMA20 ± 2σ（ddof=1）。把官方資料餵給 app 的 matrix_feature 比對：171 格，上軌最大差 2.56e-4、下軌 1.19e-4、ma20 2.4e-5（float32 精度）。與已存 enriched 比對（未除權息的 2454／3034／6196／0050／00885／00893，共 114 格）：最大差 3.8e-11。公式與 app 自身定義一致（母體或樣本 std 的差異見 findings）
  - 來源：backend/app/backtest/matrix.py:3893-3897
  - 來源：backend/app/indicators/pipeline.py:417,448-451
  - 來源：step1_official.py 輸出
- **boll_width（BandWidth）＝(上軌−下軌)/中軌**：與官方資料的獨立計算比對：171 格，最大差 1.13e-7。定義與 StockCharts「( (Upper Band - Lower Band) / Middle Band) * 100」及 Wikipedia「Bandwidth = (upperBB − lowerBB) / middleBB」一致（app 沒有乘 100，屬於單位選擇，門檻用的是自身分位數，不受影響）
  - 來源：backend/app/backtest/matrix.py:3903-3907
  - 來源：https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/bollinger-bandwidth
  - 來源：https://en.wikipedia.org/wiki/Bollinger_Bands
- **boll_position（%b）＝(close−下軌)/(上軌−下軌)**：程式碼與 Wikipedia 的「%b = (last − lowerBB) / (upperBB − lowerBB)」一致。scoring.py 的 polars 路徑與矩陣路徑比對：18,499 格，最大差 6e-6
  - 來源：backend/app/backtest/matrix.py:3898-3902
  - 來源：backend/app/strategy/scoring.py:131-135
  - 來源：https://en.wikipedia.org/wiki/Bollinger_Bands
- **close_position、change_pct、momentum_5d、momentum_20d、annual_vol_20d（公式）、vol_ratio_5d（矩陣公式）**：以官方資料獨立計算，與 app 的 matrix_feature 比對：close_position 341 格最大差 2.0e-5；change_pct 333 格 1.2e-7；momentum_20d 162 格 1.2e-7；momentum_5d 297 格 1.3e-7；annual_vol_20d（日報酬 std ddof=1×√252）162 格 3.5e-7；vol_ratio_5d（今日量／前 5 日均量，不含今日）297 格 1.8e-7。enriched 的 change_pct、momentum 與官方資料的獨立計算差為 0（未除權息標的）
  - 來源：step1_official.py 輸出
  - 來源：backend/app/backtest/matrix.py:3809-3861,3912-3919,3970-3971
- **第三方比對：漲跌幅與振幅（6196、2454、3034，2026-09-23）**：Yahoo 奇摩股市：6196 為 +2.62%、振幅 4.49%，app 為 change_pct 0.026168、amplitude 0.04486；2454 為 +0.10%、2.41%，app 為 0.000965、0.024131；3034 振幅 3.41%，app 為 0.034111，漲跌幅量值 2.15% 相符（頁面摘要判讀為 +，但收盤 545 < 昨收 557，實際為 −2.15%，app 為 −0.021544）
  - 來源：https://tw.stock.yahoo.com/quote/6196.TW
  - 來源：https://tw.stock.yahoo.com/quote/2454.TW
  - 來源：https://tw.stock.yahoo.com/quote/3034.TW
- **turnover_rate 口徑：成交股數／已發行普通股數×100（%）**：以 TWSE t187ap03_L 的 issued_common_shares 與官方成交股數獨立計算 2026-09-23 的 2379、2454、3034、3545、6196，與 enriched 比對：最大差 3.1e-4（2454，原因是 enriched 成交量少 5,000 股，見 findings）。台灣慣例以發行／上市股數為分母：MoneyDJ 定義為「當日交易的總股數與上市的總股數之比」；分母與 docs/taiwan-share-capital-phase-6h.md 記錄的一致
  - 來源：https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=34f28c61-4b62-4a5a-bd9a-dd728d948d9a
  - 來源：docs/taiwan-share-capital-phase-6h.md:160-166
  - 來源：step1_official.py 輸出
- **volume_price_surge（MA20 上穿、vol_ratio_5d≥2、收紅；出場為 MA20 下穿）**：在 2025-01～2026-09-22 的全市場 923,331 格，以 pandas 逐檔獨立重寫後比對：app 11,230 筆、獨立實作 11,231 筆，差 1 筆；出場差 19 筆，經 step15 確認全部是 |close−ma20|<1e-14 的相等邊界，由 float32 與 float64 的差異決定
  - 來源：backend/app/strategy/builtin/volume_price_surge.py:58-78
  - 來源：step3_strategies.py、step15_exitmis.py 輸出
- **strong_open（開盤 > 昨收×1.03、收 > 開、漲幅 > 3%）**：獨立重寫比對：app 12,324 筆、獨立實作 12,323 筆，差 1 筆（浮點邊界）。三個條件都成立時，漲幅 > 3% 的條件在預設參數下必然成立，屬於冗餘條件，但不是錯誤。台股開盤價由集合競價決定，與策略描述一致
  - 來源：backend/app/strategy/builtin/strong_open.py:71-84
  - 來源：step3_strategies.py 輸出
- **low_volatility_leader（momentum_20d>0、annual_vol_20d<0.30、收 > MA20）的篩選邏輯**：獨立重寫比對：151,668 筆中差 14 筆（邊界值）。評分問題見 findings
  - 來源：backend/app/strategy/builtin/low_volatility_leader.py:67-76
  - 來源：step3_strategies.py 輸出
- **high_turnover_surge 的單位（turnover_rate 為 %、change_pct 為小數，門檻 min_change/100）**：enriched 的 turnover_rate 為百分數（6196 的 0.805 代表 0.805%），change_pct 為小數，與 CONTRIBUTING §3.1 一致。2026-09-23 手動篩出 turnover>5 且 change>0.03 的 18 檔，與 pipeline 的 18 筆進場一致
  - 來源：backend/app/strategy/builtin/high_turnover_surge.py:66-74
  - 來源：CONTRIBUTING.md:88-95
  - 來源：step9_misc.py 輸出
- **bollinger_squeeze 的其餘邏輯（前一根收斂、帶量 ≥1.5×含當日的 20 日均量、close_position≥0.7、首根突破窗口 5、出場為收 < MA20）**：獨立重寫比對：app 2,947 筆、獨立實作 3,205 筆，差 270 筆；抽樣的 8 筆都是 app=False，原因是缺列造成原始列窗口出現 NaN（見 findings）。其餘條件一致。注意：這裡的量能條件分母包含當日，與 vol_ratio_5d 不含當日的定義不同，屬於設計選擇
  - 來源：backend/app/strategy/builtin/bollinger_squeeze.py:93-126
  - 來源：step3_strategies.py、step4_debug_bs.py 輸出
- **7 支策略都沒有未來函數**：把 T（倒數第 60 個交易日）之後的 OHLCV 與各衍生欄位乘上 U(0.7,1.3) 擾動後重跑；7 支策略在 T 以前（359 列 × 30 檔）的 entry、exit、score 差異都是 0，T 以後則有差異，證明擾動確實生效
  - 來源：step5_lorentzian.py 輸出：[lookahead] 7 行 entry_diff=0 exit_diff=0 score_maxdiff=0
- **lorentzian_knn 的實作忠於自身設計，標籤沒有洩漏**：標籤為 sign(close[t+4]−close[t])，只使用 j+4<=t 的鄰居（lorentzian_knn.py:144-151）。以 Python 迴圈獨立重寫（特徵、60 根原始窗 min-max、每 4 根取 1 個候選、Lorentzian 距離 Σlog1p|Δ|、k=8）後比對：30 檔 × 418 根，進場差 37 筆（獨立實作共 1,203 筆）、出場差 57 筆；不一致的 90 列中有 88 列是第 8 與第 9 近鄰距離完全相同，由 argpartition 的非穩定排序決定
  - 來源：backend/app/strategy/builtin/lorentzian_knn.py:141-178
  - 來源：step5_lorentzian.py、step5b_diag.py 輸出
- **MACD 柱狀體×2、KD 起始值對 lorentzian 特徵的影響**：推導：特徵先經 60 根滾動 min-max 正規化，對正的縮放倍數不變，所以 macd_hist 是否乘 2 不影響。EMA 起始值的影響在選股的 121 根窗內衰減到 (2/3)^121、(8/9)^121、(25/27)^121，都小於 1e-4。這是推導結果，沒有另外實測
  - 來源：backend/app/backtest/matrix.py:3848,3920-3932
- **factor_rank_research 的評分實作（截面 min-max、加權、top_rank 穩定排序）**：以 momentum_20d 與 vol_ratio_5d 兩個因子，在 2026-03～09-22 全市場（2,316 檔）獨立重算：分數最大差 1e-5，入選差 0 筆。口徑問題見 findings
  - 來源：backend/app/backtest/matrix.py:3686-3742
  - 來源：backend/app/strategy/builtin/factor_rank_research.py:104-139,192-206
  - 來源：step7_factor_rank.py 輸出
- **composite._hold_masks_from_entries 的持倉窗口沒有未來洩漏**：微測試：t=2 進場、max_hold=3，得到 [0,0,1,1,1,0,0,0]，符合預期；np.roll 後把第 0 列清零，阻止環繞
  - 來源：backend/app/strategy/composite.py:132-154
  - 來源：step14_vr.py 輸出
- **composite 的 union、intersect 與 min_confirm 語意，以及 exit 來源投影**：讀程式碼：intersect 以 Σentry≥effective_min 判定；min_confirm≤0 時視為全部子策略；每個子策略的 exit 只在自己的持倉窗口內生效。選股與回測兩個合併器的 entry 語意一致（分數口徑的差異見 findings）
  - 來源：backend/app/strategy/composite.py:103-110,183-199
- **scoring.py：effective_scoring、effective_scoring_directions、scoring_dependencies、scoring_warmup_bars、materialize_scoring_columns，以及虛擬欄位 polars 與矩陣兩條路徑的一致性**：151 檔加自選股，比對最近 120 個交易日：amihud_20d 最大差 1.76e-4、amount_ratio_5d 4e-6、atr_pct 4.9e-5、boll_position 6e-6、boll_width 0、close_position 4.8e-5、distance_to_high_60d／distance_from_low_60d 0、ma5／10／20／30／60_bias 0、macd_dif／dea／hist_pct 0、gap_return 0、intraday_return 0、log_amount 1e-6、max_ret_20d 0、ret_skew_20d 3.5e-5、up_days_20d 0、limit_up_count_20d／60d 0、vol_price_corr_20d 7.9e-6、vol_ratio_10d 4.8e-6、vol_trend_5_10 2e-7、vol_trend_5_60 6.5e-7、vwap_bias 2.5e-7、ema5／10／20／30_bias ≤1.8e-4；兩條路徑都沒有單邊 NaN。max_ret_20d 對應 MAX 效應、amihud 為 |R|/成交額（億）的 20 日平均，都與學術定義一致
  - 來源：backend/app/strategy/scoring.py:110-207
  - 來源：step13_scoring.py 輸出
- **選股只用官方收盤資料，不用盤中未完成的 K 棒**：讀程式碼：ScreenerService.latest_date 取 TaiwanDailyStore 的最後官方日，不是即時的 enriched 日期，所以盤中部分量不會造成量比或換手偏低的問題（監控規則為 0 條，也不會走即時路徑）
  - 來源：backend/app/services/screener.py:415-427
  - 來源：backend/app/repository.py:1416-1427
- **strong_open 等策略用前一日收盤計算跳空，這個計算方式本身正確**：方法本身正確；但台股 enriched 沒有還原除權息，除權息日的基準錯誤（見 findings F1）
  - 來源：backend/app/strategy/builtin/strong_open.py:73-76

## 無法驗證的項目

- **第三方的布林上下軌、BandWidth、KD、RSI、週轉率數值**：HiStock 技術分析頁由前端動態產生，抓不到數值（https://histock.tw/stock/tv/tvchart.aspx?no=2454）；Yahoo 個股頁沒有顯示週轉率。因此只完成官方資料獨立計算與 app 的兩方比對
- **turnover_ratio_5d、turnover_z_60d 在 polars 與矩陣兩條路徑的一致性**：歷史 turnover_rate 只在 2026-09-23 有值，比對時有效格數 n=0
- **TradingView 原版 MLExtensions 中 n_rsi、n_cci、n_wt 的確切程式碼**：函式庫頁面只有函式說明：normalize()「Rescales a source value with an unbounded range to a target range」、rescale() 用於有界到有界的轉換，沒有原始碼。所以「原版 RSI 以固定 0–100 區間 rescale」是依說明推論，不是逐字核對（https://www.tradingview.com/script/ia5ozyMF-MLExtensions/）
- **實際執行回測引擎（撮合、T+1、持倉上限）**：依規定不呼叫會寫入 backtest_results 的服務。high_turnover_surge 回測 0 交易、low_volatility_leader 分數全為 0，是由相同輸入的矩陣訊號推得（回測也讀同一份 enriched parquet，engine.py:509-548），沒有實際執行回測
- **上櫃（TPEX）標的在 2026-09-23 的策略結果**：enriched 與 taiwan/daily 的 2026-09-23 分區都沒有 TPEX 列
- **enriched 成交量比官方少的根因**：只確認 taiwan/daily 存檔本身的量就偏低（例如 2454 在 9/23 為 6,425,491，STOCK_DAY 與 MI_INDEX 都是 6,430,491），沒有追查擷取程式；屬於資料擷取領域
- **台灣券商軟體的布林 σ 是母體還是樣本**：沒有找到台灣券商公開的計算說明；只有 Bollinger 原始定義（母體）可引用
