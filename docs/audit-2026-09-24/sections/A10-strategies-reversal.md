# A10-strategies-reversal｜策略合理性（反轉與回檔類）與策略評分框架

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 9 | 7 | 0 | 0 | 0 | 18 | 6 |

## 稽核者摘要

結論：7 支策略的程式邏輯、所有指標公式、min-max 評分公式都能被獨立重算出來，而且結果一致。問題在價格口徑、缺值處理、出場設計與評分方向。
(1) 用官方 STOCK_DAY 未還原價獨立計算 9 檔自選股、38 個交易日（8/3–9/23），change_pct、momentum、MA5/10/20/60、low_60d 與 app 的差距為 0（最多 4e-14），RSI14 最多差 5e-5。7 支策略的進場判斷有 342 個「股票×日」，其中只有 1 個不同，原因是 app 的基本過濾（00885 當天成交金額 1283 萬，低於 2000 萬門檻）。全市場重跑 9/23，7 支策略的命中名單與評分都和 app 實際的 strategy_cache.json 完全相同，分數差 0.0。
(2) 最嚴重的問題（P1）：台股 provider 宣告 adj_factor=False，enriched 的 close 就是未還原價。7 個官方除權息事件前後，app 的 close/raw_close 都是 1.000000，官方還原因子則是 0.9576 到 0.9941。因此策略把除權息缺口當成下跌。例如 6669 在 9/2 除權後，app 的 RSI14 是 26.5，被判超賣而入選 oversold_reversal，還原後應為 53.5。9 月全市場合計，pullback_to_support 多出 37 筆假訊號、漏掉 131 筆，pullback_ma20_bounce 多出 22 筆、漏掉 73 筆。自選股 00878 在 8/19–8/28 漏掉 9 筆。
(3) P2 有三項：
- 矩陣評分遇到缺值時給 0 分，候選仍留在結果裡，違反 CONTRIBUTING 5.1。pullback_to_support 的 turnover_rate 在歷史資料中幾乎全部缺值，28,068 筆歷史候選有 28,038 筆是 0 分；9/23 的結果裡有 13 檔 ETF 是 0 分。
- bottom_reversal 的出場條件「收盤 < MA20」在進場當根 100% 成立，隔天仍成立的比例是 96.1%，實際上等於只持有一天。
- composite 的選股與回測對「單一候選」的歸一化不同（選股給 1.0、回測給 0.5），會造成排名互換；同分時的排名還會隨 PYTHONHASHSEED 改變。使用者目前沒有 composite 策略，所以暫時不受影響。
(4) 其餘 P3 共 11 項：反轉策略的評分方向（rsi_14、momentum_5d 越高分數越高）和策略邏輯相反；爆量只和前一天比；新低反轉幾乎抓不到「盤中破底、收盤收回」的型態；回檔策略的進場訊號被誤標成 5/20 黃金交叉；最低成交量用股數而不是金額；縮量回踩允許在跌破 MA20 當天進場；限股票的策略也選進 ETF；暖機期不足；非矩陣評分路徑把缺值排在第一位；exclude_new_days 從未實作；近期成交量和官方不一致。
方法：所有腳本與中間檔都在 AUDIT_SCRATCH/work/A10/（下文簡稱 W），以 uv run python 執行。只讀取 parquet，只呼叫純函式（compute_indicators、build_market_data_matrix、MatrixStrategyPipeline、merge_*）。git status 前後比對沒有變化。網路共 16 次請求，沒有連線證交所或櫃買中心。第三方網站（HiStock、Yahoo 奇摩、Goodinfo）的技術指標數值是動態產生或回傳 403，抓不到。

## 問題清單

### [P1 → P1] 台股價格未還原除權息，反轉與回檔策略把除權息缺口當成下跌，產生假訊號與漏訊號（確認成立）

- **主張**：台股 provider 宣告 adj_factor=False，enriched 的 open/high/low/close 就是未還原原始價。7 支策略依賴的 RSI、MA、動能、60 日低點、漲跌幅，在除權息日都會把配息或配股造成的參考價下調算成真實下跌。
- **正確的定義或數值**：技術指標與策略應使用前復權價格（依 CONTRIBUTING 3.2），除權息日以參考價銜接前一日收盤；漲跌停相關判斷才使用 raw_*。
- **app 實際行為**：台股 close 未還原。除權息日的缺口被算成跌幅，RSI、MA、動能與 60 日新低都失真，反轉策略會對配股或高殖利率標的發出假超賣、假新低訊號，回檔策略則會漏掉訊號。
- **對使用者的影響**：自選股 00878（季配息約 3%）、0050、2379（9/8 除息 25 元）、3545、3034、2454、6196 在除權息前後約 60 個交易日內，回檔類策略的結果都受影響；9/23 的回檔策略清單裡有依賴未還原價才成立的候選（例如 4912 排第 1 名）。
- **修法建議**：用 TWT49U（或 MOPS 除權息）的參考價建立台股 adj_factor，並寫入 enriched 的前復權 OHLC；指標與策略一律使用還原價，raw_* 保留給漲跌停判斷。修正後補一組跨除權息日的回歸測試。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/providers/hybrid_provider.py:31 adj_factor=False
  - backend/app/taiwan/providers/hybrid_provider.py:151 'Return empty adjustment factors (Phase 2 records raw price semantics).'
  - CONTRIBUTING.md:100「enriched 的 open/high/low/close 為前復權價格」（與實作不符）
  - backend/app/indicators/pipeline.py:493-504（momentum、change_pct 直接用 close 計算）
  - backend/app/strategy/builtin/oversold_reversal.py:73-80（RSI<30＋漲幅＞1%＋站上 MA5）
- **線上證據**：
  - 官方 TWT49U 除權息計算結果（本地參考檔 csv/twse_twt49u_20260601_20260923.csv，來源 https://www.twse.com.tw/rwd/zh/exRight/TWT49U?response=json&startDate=20260601&endDate=20260923）：6669 在 2026-09-02 的除權前收盤 7800.0、參考價 2614.99；00878 在 2026-08-18 的除息前 33.75、參考價 32.74；3034 在 2026-07-10 的除息前 542.0、參考價 519.0
- **本地計算證據**：
  - W\s6_indep.py：7 個官方除權息事件（6196 6/24、2454 7/7、3034 7/10、0050 7/21、3545 8/4、00878 8/18、2379 9/8），app 的 close/raw_close 在事件前後都是 1.000000，官方因子為 0.957565 到 0.994058
  - 6669 在 2026-09-15：app 的 RSI14 是 26.50（超賣），用 TWT49U 因子還原後是 53.48；app 據此把 6669 列入 oversold_reversal（W\s8_adjusted.py 的 only_raw 清單）。app 的 6669 在 9/2 的 change_pct 為 −66.5%、momentum_20d 為 −57.7%
  - 00878 在 2026-08-18：app 的 change_pct 為 −4.06%，還原後為 −1.10%；RSI14 分別為 45.96 與 55.78
  - W\s8_adjusted.py，全市場 2026-09-01 到 09-23（未還原 vs 還原）：pullback_to_support 680 vs 774（只在未還原價出現 37 筆、只在還原價出現 131 筆）；pullback_ma20_bounce 327 vs 378（22／73）；oversold_reversal 16 筆裡有 4 筆只在未還原價出現；oversold_bounce 2/22；n_day_low_reversal 1/16；pullback_resume 漏掉 3 筆
  - W\s6_indep.py，自選股：00878 在 8/19–8/28 用還原價會命中 pullback_to_support 5 筆、pullback_ma20_bounce 4 筆，app 全部沒有；0050 在 8/27 的 pullback_ma20_bounce 也漏掉。9/23 app 的 pullback_ma20_bounce 第 1 名 4912（72.02 分）在還原價下不成立
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：台股 provider 宣告未還原價（hybrid_provider.py:31、:144-152），data/adj_factor 也是空的，所以 enriched 的 OHLC 是原始價，違反 CONTRIBUTING.md:100 的前復權契約。除權息日的缺口被 RSI、MA、動能、change_pct 算成真實下跌，造成兩種結果。第一種是假訊號：例如 6669 在 9/15 的 RSI 為 26.5，實際應為 53.5，卻被列入 oversold_reversal；4912 在 9/23 名列 pullback_ma20_bounce 第 1 名，這在還原價下不成立。第二種是漏訊號：例如 00878 在 8/21、8/24、8/25、8/28 應命中 pullback_ma20_bounce，app 全部漏掉。change_pct 也和官方口徑不一致：TWSE STOCK_DAY 在除權息日標記「X0.00」、不比價，app 卻給出 00878 −4.06%、3034 在 7/13 −13.75%（後者超過 10% 漲跌幅限制）。修正兩處範圍：(1) 影響範圍是 60 日窗口內的指標失真，策略結果只在接近門檻時翻轉，不是「全部受影響」；受影響的也不只 7 支反轉與回檔策略，所有用 close 算指標的策略都有。(2) 新面向：除權息日的漲跌停基準會退回除權息前的原始收盤價，而不是官方參考價（pipeline.py:755-762；6669 在 9/2 存的 prev_close 為 7800，官方為 2614.99）。所以修正時，除了建立台股 adj_factor 並讓指標改用前復權價，漲跌停判斷也要改用 TWT49U 參考價，不能只用 raw_close.shift(1)。
    - 理由：結論：finding 的核心主張成立，嚴重度 P1 合理。台股 provider 本來就只提供未還原價；enriched 在每個除權息日 close/raw_close 都是 1.0，所以 RSI、MA、動能、change_pct 都把除權息缺口當成下跌。我自己重算的數字和 finding 一致。

一、程式碼（依現況讀取）
- hybrid_provider.py:31 宣告 adj_factor=False，:144-152 的 get_adj_factors 回傳空表。finmind_provider.py:8、yahoo_provider.py:8 也明寫「Price semantics: RAW」。
- data/adj_factor/ 是空目錄。compute_enriched 在沒有因子時會跳過 _apply_adj_factor（pipeline.py:1022-1024）；momentum 與 change_pct 直接用 close 相除（pipeline.py:498、504）。
- 這和 CONTRIBUTING.md:98-101「enriched 的 open/high/low/close 為前復權價格」相衝突。依 CONTRIBUTING.md:295，「錯誤金融口徑」屬於 P1。

二、正確定義（線上來源，與 finding 引用的 TWT49U 不同）
- Yahoo Finance 說明頁 https://help.yahoo.com/kb/SLN28256.html（依 CRSP 標準）：「Adjusted close is the closing price after adjustments for all applicable splits and dividend distributions」，股利乘數為「1 - (dividend amount/closing price)」。以 00878 為例，1−1.01/33.75=0.970074，正好等於 TWT49U 的參考價／除息前收盤。
- Wikipedia https://en.wikipedia.org/wiki/Ex-dividend_date：「The opening price on the ex-dividend date ... can be expected to decrease by the amount of the dividend」。也就是說，除息造成的價格下調不是市場跌幅。
- 官方 STOCK_DAY 本地參考檔在除權息日的漲跌欄是「X0.00」（00878 8/18、2379 9/8），表示交易所不拿除權息前收盤比價。app 的 change_pct −4.06% 與官方口徑矛盾。

三、本地重算
- 我用 app 的純函式 compute_indicators，分別餵入未還原價與 TWT49U 還原價。未還原版的 rsi_14 與 app 存檔逐位相同，證明 app 用的就是未還原價。
- 6669 在 9/15：app 的 RSI14 是 26.4957，change_pct 是 +1.98%，收盤 2315 高於 MA5 2313，三個條件同時滿足 oversold_reversal；還原後 RSI 是 53.48，不成立。6669 在 9/2 的 change_pct 為 −66.5%，momentum_20d 為 −57.7%。
- 00878 在 8/18：change_pct −4.06%，還原後 −1.10%；RSI 45.96 對 55.78。
- 00878 的 pullback_ma20_bounce：用還原價在 8/21、8/24、8/25、8/28 各命中一次。未還原價下這幾天 MA5 都低於 MA20，所以全部不命中。這與 finding 的 4 筆相符。
- 4912 在 9/23：strategy_cache.json 裡 pullback_ma20_bounce 第 1 名確實是 4912，分數 72.015。未還原價下 close/MA20=98.4/96.64=1.0182，在 ±2% 內；還原後是 98.4/96.2242=1.0226，超出範圍，不成立。
- 快取結果裡還有 00985A、00915 等 ETF，證明 ETF 在 stock 策略宇宙內，所以 00878、0050 確實會受影響。

四、需要修正或補充的地方
1. 「約 60 個交易日內回檔類策略結果都受影響」說得太滿。實際上是指標在 MA60／60 日窗口內失真，策略結果只有在接近門檻時才會翻轉。自選股的息值在 0.6% 到 4.2% 之間，影響屬中度。
2. 影響範圍不只 7 支策略。凡是用 close 算的 MA、RSI、MACD、動能類策略都會受影響，只是本題限定反轉與回檔類。
3. 新面向（原記錄沒有）：除權息日的漲跌停基準也錯了。pipeline.py:755-762 只在 close/raw_close 比值改變時才改用還原昨收；台股比值永遠是 1，所以除權息日的基準會退回除權息前的原始收盤價，而不是官方參考價。app 存檔的 prev_close 可以佐證：6669 在 9/2 存的是 7800，官方參考價是 2614.99。另外，3034 在 7/13 的 change_pct 為 −13.75%，超過 10% 漲跌幅限制；官方 TWT49U 的跌停價 467.5 是以參考價 519 計算的。因此 finding 的修法「漲跌停判斷用 raw_*」並不夠，還需要官方參考價。6669 在 9/2 會不會被誤標跌停，是從程式碼推得的，沒有實跑。
4. 推測（未線上驗證）：台灣散戶看盤軟體常以未還原 K 線為預設，所以「用未還原價算指標」在市場上不算罕見。但本專案的契約明訂前復權，Yahoo／CRSP 標準也是還原，所以口徑判定不變。
5. 未查：2026-07-10 全市場沒有 enriched 分區，原因可能是休市，也可能是缺資料，不在本題範圍。
    - 證據：
      - backend/app/taiwan/providers/hybrid_provider.py:31 adj_factor=False；:144-152 get_adj_factors 回傳 pl.DataFrame()，docstring 為 'Return empty adjustment factors (Phase 2 records raw price semantics).'
      - backend/app/taiwan/providers/finmind_provider.py:8、yahoo_provider.py:8 標明 'Price semantics: RAW'
      - CONTRIBUTING.md:100「enriched 的 open/high/low/close 為前復權價格」；:295 的 P1 包含「錯誤金融口徑」
      - backend/app/indicators/pipeline.py:1022-1024 只在 factors 不為空時才前復權；data/adj_factor/ 為空目錄
      - backend/app/indicators/pipeline.py:498 momentum_20d、:504 change_pct 直接用 close 計算
      - backend/app/strategy/builtin/oversold_reversal.py:74、77、80（RSI<30、change_pct>1%、close>MA5）；pullback_ma20_bounce.py:67-75（±2%、MA5>MA20>MA60、change_pct>0）
      - backend/app/indicators/pipeline.py:755-762 只在 close/raw_close 比值改變時才用還原昨收作漲跌停基準；台股比值恆為 1，除權息日因此退回除權息前的原始收盤
      - 線上：https://help.yahoo.com/kb/SLN28256.html 原文 'Adjusted close is the closing price after adjustments for all applicable splits and dividend distributions'；股利乘數 '1 - (dividend amount/closing price)'
      - 線上：https://en.wikipedia.org/wiki/Ex-dividend_date 原文 'The opening price on the ex-dividend date, in comparison to the previous closing price, can be expected to decrease by the amount of the dividend'
      - 官方參考檔 reference\csv\twse_stock_day_202608_202609.csv：00878 在 2026-08-18、2379 在 2026-09-08 的 change_raw 為 'X0.00'（除權息日不比價）
      - 官方參考檔 reference\csv\twse_twt49u_20260601_20260923.csv：00878 在 8/18 為 33.75→32.74；6669 在 9/2 為 7800→2614.99（權）；3034 在 7/10 為 542→519，跌停價 467.5；4912 在 9/10 為 97.5→96.69
      - 本地：work\verify_adj\v1.py 讀 enriched。9 個除權息事件的 close/raw_close 全部是 1.0；00878 在 8/18 的 chg 為 −0.040593；6669 在 9/2 為 −0.665385；3034 在 7/13 的 change_pct 為 −0.137454
      - 本地：work\verify_adj\v2.py 用 compute_indicators 重算，未還原版 rsi_14 與 app 存檔逐位相同。6669 在 9/15 的 RSI 為 26.4957（app）對 53.4808（還原）；00878 在 8/18 為 45.9584 對 55.7820，chg −4.06% 對 −1.10%；2379 在 9/8 的 chg 為 −5.08% 對 −1.70%
      - 本地：00878 用還原價時，8/21、8/24、8/25、8/28 同時滿足 MA5>MA20>MA60、±2%、當日上漲（例如 8/21 為 32.39>31.87>31.76，close/MA20=1.016）；未還原價這幾天 MA5<MA20（8/21 為 32.592<32.654）
      - 本地：data/user_data/strategy_cache.json 的 results.pullback_ma20_bounce 第 1 名為 4912.TWSE，score 72.0151，ma20 96.64；還原版 ma20 為 96.2242，close/ma20=1.0226>1.02，不成立；同一清單含 00985A.TWSE、00915.TWSE 等 ETF
      - 本地：enriched 在 2026-09-02 存的 6669 prev_close=7800.0（官方參考價 2614.99）；2026-07-10 全市場沒有分區（原因未查）
  - **reproduction**：confirmed；修正後嚴重度 P1
    - 修正後敘述：台股 provider 不提供還原因子（adj_factor=False，get_adj_factors 回傳空表），enriched 的 OHLC 全是未還原的原始價：1,423,597 列的 close/raw_close 都是 1。這與 CONTRIBUTING 3.2 的前復權約定不符。所以 change_pct、momentum、RSI、MA、low_60d 在除權息日都把參考價的下調當成真實下跌。實例如下：
- 00878 在 8/18：change_pct −4.06%，還原後 −1.10%。
- 6669 在 9/2 除權（配股 19.83 元）：change_pct −66.5%，並在 9/15 產生假超賣（RSI14 26.50，還原後 53.48），被 oversold_reversal 選入。
- 3034 的 7/13（app 沒有 7/10 這個交易日，7/13 是除息後第一根 K 線）：change_pct −13.75%，超過 ±10% 漲跌幅限制。

全市場 9/01–9/23 的進場訊號差異與 finding 的數字完全相同：pullback_to_support 680 對 774、pullback_ma20_bounce 327 對 378、oversold_reversal 4 筆假訊號、oversold_bounce 2 筆、n_day_low_reversal 1 筆、pullback_resume 漏掉 3 筆；bottom_reversal 在此視窗內沒有差異。9/23 pullback_ma20_bounce 的第 1 名 4912（72.02 分）只在未還原價下成立。

自選股受影響的範圍要修正：真正改變策略結果的只有 00878（pullback_to_support 5 筆、pullback_ma20_bounce 4 筆）、0050（8/27）與 6196（7/13 的 pullback_ma20_bounce，原 finding 沒提到）。2454、3034、2379、3545 只是指標值失真，策略訊號沒有改變。

另一個未驗證的面向：回測損益同樣使用未還原 close，而且沒有任何配息處理。
    - 理由：我用官方參考資料和本地 parquet 自行重算，finding 的每一個數值都重現了，而且與 finding 一致到小數點。以下並列（finding → 我的結果）。

程式碼證據：
- hybrid_provider.py:31 的 adj_factor=False、:151 的 get_adj_factors 回傳空表、CONTRIBUTING.md:100 寫明前復權、pipeline.py:494-504 的 momentum／change_pct 直接用 close 算，這四處都屬實。
- 全部 enriched 資料（661 個分區、1,423,597 列）的 close/raw_close 都是 1.0，一列例外都沒有。
- adj_factor 目錄是空的。
- 同一批資料用 compute_enriched 在不帶因子的情況下重算，rsi_14、ma20、momentum_20d、change_pct、low_60d、vol_ratio_5d 與存檔值的差異全部是 0。這證明我的重算管線就是 app 的口徑。

官方資料對帳：
- TWT49U 共 994 個事件，其中 956 個在 app 的除權息日有資料，app 前一日的 raw_close 與官方除權息前收盤全部相同。
- 7 檔自選股的官方因子是 0.957565（3034）到 0.994058（2454），與 finding 相同。
- 另外有兩個官方證據：STOCK_DAY 在 00878 的 8/18、2379 的 9/8、3545 的 8/4 都標 X、漲跌記為 0；HiStock 也列出 00878 在 8/18 配息 1.01、除息前 33.75，2379 在 9/8 配現金 25 元、除息前 729，6669 在 9/2 配股票股利 19.83 元（相當於每股配 1.983 股，所以 7800→2614.99 的比例 2.983 是合理的）。

單點數值（前為 finding，後為我的結果）：
- 6669，9/15：RSI14 26.50 → 26.4957；還原後 53.48 → 53.4808。
- 6669，9/2：change_pct −66.5% → −66.5385%；momentum_20d −57.7% → −57.7328%。
- 00878，8/18：change_pct −4.06% → −4.0593%；還原後 −1.10% → −1.0996%；RSI14 45.96／55.78 → 45.9584／55.7820。

策略層：我用 app 自己的 StrategyEngine._load_file、_matrix_field_columns、build_market_data_matrix 和 MatrixStrategyPipeline，套 DEFAULT_BASIC_FILTER 與預設參數，全市場 9/01–9/23 的比較如下。
- pullback_to_support 680 對 774，只在未還原價出現 37 筆、只在還原價出現 131 筆，與 finding 相同。
- pullback_ma20_bounce 327 對 378，22／73，相同。
- oversold_reversal 16 筆裡有 4 筆只在未還原價出現（6669 9/15、8473 9/17、2442 9/18、9938 9/21），相同。
- oversold_bounce 22 筆裡 2 筆、n_day_low_reversal 16 筆裡 1 筆，相同。
- pullback_resume 漏掉 3 筆（00981A 9/01、6177 9/03、2886 9/11），相同。
- 自選股：00878 用還原價會多出 pullback_to_support 5 筆（8/19、8/20、8/21、8/24、8/25）和 pullback_ma20_bounce 4 筆（8/21、8/24、8/25、8/28），0050 在 8/27 多出 pullback_ma20_bounce，全部重現。

9/23 的 pullback_ma20_bounce：
- 我在未還原價下算出的第 1 名是 4912，72.02 分，與 app 實際的 data/user_data/strategy_cache.json（as_of 2026-09-23）完全一致。
- 用還原價時 4912 不成立：close/ma20 是 1.0226，超過 ±2% 的門檻；未還原價是 1.0182。

需要修正或補充的地方（都不影響結論）：
1. app 所有台股資料集（taiwan/daily、institutional、margin、enriched）都沒有 2026-07-10。3034 除息後的第一根 K 線是 7/13，app 算出的 change_pct 是 −13.75%，超過 ±10% 漲跌幅限制；還原後是 −9.92%。7/13 的收盤 467.5 正好等於官方 7/10 的跌停價。所以 finding 說 3034「7/10 事件前後 close/raw_close=1」，精確的說法應是 7/13。
2. finding 說 7 檔自選股的回檔類策略結果「都受影響」，說得太滿。6/1 以來真正改變策略進場結果的只有三檔：00878（9 筆）、0050（1 筆）、6196（7/13 的 pullback_ma20_bounce 漏掉，這筆 finding 沒提到）。2454、3034、2379、3545 只是指標值被扭曲，7 支策略的進場訊號都沒有改變。bottom_reversal 在 9 月的視窗內也沒有差異。
3. 我的還原只套用 TWT49U 在 6/1 到 9/23 的事件。6 月以前的除權息沒有還原，所以「真實差異」只可能比上面的數字更多，不會更少。
4. 新面向（推測，未實跑回測）：app/backtest 底下 grep dividend、ex_rights、除權、除息都是零命中，engine.py:701/1224/2257 直接用 panel["close"]。台股回測只要持有跨過除權息日，就會把配息或配股的缺口記成虧損。例如持有 6669 跨過 9/2，會記一筆 −66.5% 的單日虧損。

嚴重度：這違反 CONTRIBUTING 3.2 的前復權口徑，屬於錯誤的金融口徑，維持 P1。
    - 證據：
      - 程式碼：backend/app/taiwan/providers/hybrid_provider.py:31 `adj_factor=False`；:151 `Return empty adjustment factors (Phase 2 records raw price semantics).`；CONTRIBUTING.md:100 `enriched 的 open/high/low/close 為前復權價格。`；backend/app/indicators/pipeline.py:494-504 momentum_*／change_pct 直接用 close 計算；pipeline.py:1022-1024 只有傳入 factors 時才會前復權，但 data\adj_factor 目錄是空的
      - 腳本 AUDIT_SCRATCH/work/verify-exright/s1_load.py 的輸出：partitions 661 rows 1423597；close/raw_close min 1.0 max 1.0，不等於 1 的列數為 0；TWT49U 在 app 除權息日有資料的事件 956 of 994，app 前一日 raw_close 與官方 pre_ex_close 相同 956 筆、不符 0 筆
      - s1_load.py 官方因子（ref/pre）：6196 6/24 0.988078、2454 7/7 0.994058、3034 7/10 0.957565、0050 7/21 0.993952、3545 8/4 0.983633、00878 8/18 0.970074、2379 9/8 0.965706、6669 9/2 0.335255（7800→2614.99）
      - s2_recompute.py：app 存檔值與不帶因子重算值比對，rsi_14／ma20／momentum_20d／change_pct／low_60d／vol_ratio_5d 的最大絕對差都是 0.0，證明重算管線與 app 相同
      - s2_recompute.py 單點（app vs 還原）：6669 9/15 RSI14 26.49571 vs 53.480824；6669 9/2 change_pct −0.665385 vs −0.001908、momentum_20d −0.577328 vs 0.260748；00878 8/18 change_pct −0.040593 vs −0.010996、RSI14 45.958436 vs 55.782045；2379 9/8 change_pct −0.050754 vs −0.017045；3034 7/13 change_pct −0.137454 vs −0.099229
      - s3_strategies.py（套 basic filter，9/01–9/23，未還原 vs 還原，只在未還原價／只在還原價）：pullback_to_support 680/774（37/131）；pullback_ma20_bounce 327/378（22/73）；oversold_reversal 16/12（4/0，含 6669 9/15）；oversold_bounce 22/20（2/0）；n_day_low_reversal 16/15（1/0）；pullback_resume 19/22（0/3）；bottom_reversal 2/2（0/0）
      - s3_strategies.py 自選股差異（6/1 以來）：pullback_to_support 只在還原價出現 00878 8/19、8/20、8/21、8/24、8/25；pullback_ma20_bounce 只在還原價出現 6196 7/13、00878 8/21、8/24、8/25、8/28、0050 8/27；2454／3034／2379／3545 的 7 支策略進場訊號都沒有差異
      - s3_strategies.py 9/23 pullback_ma20_bounce：未還原價第 1 名 4912.TWSE 72.02（與 data/user_data/strategy_cache.json as_of 2026-09-23 的第 1 名 4912.TWSE 72.02 相同）；還原價下第 1 名是 2354 62.31，4912 不入選（close/ma20：未還原 1.0182，還原 1.0226，超過 ±2%）
      - 官方參考 csv/twse_stock_day_202608_202609.csv：00878 2026-08-18、2379 2026-09-08、3545 2026-08-04 的 change_flag=X、change_raw=X0.00（交易所把除權息日標為 X，不當成一般跌幅）
      - 線上：https://histock.tw/stock/00878/%E9%99%A4%E6%AC%8A%E9%99%A4%E6%81%AF 顯示「08/18 | 1.01 | 33.75」；https://histock.tw/stock/2379/%E9%99%A4%E6%AC%8A%E9%99%A4%E6%81%AF 顯示「09/08 | 25 | 0 | 729」；https://histock.tw/stock/6669/%E9%99%A4%E6%AC%8A%E9%99%A4%E6%81%AF 顯示現金股利 144.39、股票股利 19.83、9/02 除權（Investopedia 無法抓取；WebSearch 額度已用完）
      - s1b.py：app 的 taiwan/daily、institutional、margin、kline_daily_enriched 都沒有 2026-07-10 分區；3034 7/13 收盤 467.5，等於 TWT49U 7/10 的 limit_down_twd 467.5
      - 推測（未實跑）：backend/app/backtest 底下 grep dividend／ex_rights／除權／除息都是零命中，engine.py:701、1224、2257 使用 panel["close"]，回測損益也會把除權息缺口算成虧損

### [P2 → P2] 矩陣評分遇到缺值給 0 分並留在結果中；pullback_to_support 的換手率幾乎全歷史缺值，候選幾乎全部 0 分（確認成立）

- **主張**：build_matrix_score 只要任一評分欄位是 NaN，就把該候選的分數設為 0.0，但仍保留它的進場訊號。缺值看起來就和「最差」一樣，違反 CONTRIBUTING 5.1。
- **正確的定義或數值**：缺少評分輸入的候選應回傳「不可計算」（例如 score=None 並在 UI 標示），或排除該欄位後明確註記，而不是和真正最差的候選同樣是 0 分。
- **app 實際行為**：缺值候選得 0 分，和最差者混在一起；pullback_to_support 在歷史上所有候選幾乎都同為 0 分，排序失去意義。
- **對使用者的影響**：9/23 的縮量回踩清單中，13 檔 ETF 顯示 0 分，看起來像評分最差；回測時此策略的評分與前 N 名選擇實際上由平手順序決定（推測，未實跑回測）。
- **修法建議**：缺值候選回傳 NaN 或 None 並加上『無法評分』旗標；或只用可得欄位重新計算權重並標示；台股缺換手率時，改用成交金額類欄位，或暫時從預設評分移除 turnover_rate。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/backtest/matrix.py:3741 score[~universe | ~all_finite] = 0.0
  - backend/app/strategy/engine.py:1256-1265（進場的候選不論分數都列入 rows）
  - backend/app/strategy/builtin/pullback_to_support.py:56 scoring 含 'turnover_rate': 0.3
  - CONTRIBUTING.md:141「缺少輸入時應返回明確的不可計算狀態，不能把空值偽裝成零分」
- **線上證據**：
  - （無）
- **本地計算證據**：
  - data/user_data/strategy_cache.json（as_of 2026-09-23）的 pullback_to_support 43 列中，有 13 檔 ETF（00404A、00642U、00731、00900、00915、00919、00929、00934、00939、00940、009802、009803、00985A）score=0.0 且 turnover_rate=None，全部排在最後
  - W\s9_experiments.py E5：pullback_to_support 的歷史進場 28,068 筆中有 28,038 筆 score==0；其他策略為 n_day_low 29 筆、pullback_ma20_bounce 102 筆、pullback_resume 72 筆、oversold_reversal 16 筆、oversold_bounce 15 筆、bottom_reversal 2 筆
  - enriched 的 turnover_rate 非空比例：2025-06 到 2026-08 每月都是 0.0；2026-09 股票 3.45%、ETF 0%；2026-09-23 當天股票 54.7%、ETF 0%
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：build_matrix_score（backend/app/backtest/matrix.py:3741）只要任一評分欄位是 NaN，就把該候選的分數設為 0.0，但保留進場訊號，候選仍列入結果。這個 0.0 和「三個因子都最差」的真實 0 分無法分辨，違反 CONTRIBUTING.md:141。

台股 ETF 的換手率刻意留空，歷史換手率也為了避免未來函數而全部設成 NaN（matrix.py:1439-1447）。因此所有以 turnover_rate 評分的內建策略都受影響：pullback_to_support、bullish_alignment、low_volatility_leader，以及只選股票的 high_turnover_surge。影響分兩部分：
(1) 選股結果中所有 ETF 一律 0 分、排在最後。例如 00642U 的動量在全部候選中最高，卻排在第 31 名。自選的 0050、00878、00893 在 bullish_alignment 或 low_volatility_leader 也是 0 分。
(2) 回測時歷史分數全部是 0。候選依穩定排序按資產索引取前 max_positions 檔；score_weight 退回等權；設了 score_min 就濾掉全部候選。

修正兩處：前端不顯示 score 數值，使用者看到的是排序偏差，Dashboard 的前 3 檔預覽永遠不會出現這些 ETF，而不是「顯示 0 分」。修法不能直接回傳 NaN，因為 matrix.py:2319-2320 要求 score 全部是有限值；應改用「無法評分」旗標，或只用可得欄位重新正規化權重。
    - 理由：核心主張成立。我逐行讀過程式碼、用合成資料實跑，也用本地資料重算過。build_matrix_score 在任一評分欄位出現 NaN 時，會把該格強制設成 0.0，進場訊號仍然保留，候選也照樣排進結果。0.0 同時也是「每個因子都是最差」的真實分數，兩者無法分辨。這違反專案自己在 CONTRIBUTING.md:141（5.1 策略）寫下的規則。

外部慣例：sklearn 的 MinMaxScaler 和 pandas 的 rank 預設都保留缺值，不會把缺值當成最低。程式碼在 fit 階段（算 min/max）有略過 NaN，和 sklearn 一致；transform 階段卻把缺值填成 0，和兩者都不一致。

在使用者情境中確實會發生，而且範圍比 finding 寫的大：
(1) 台股 ETF 的 turnover_rate 刻意留空，歷史換手率也為了避免未來函數而全部設成 NaN。所以凡是評分含 turnover_rate 的策略，所有 ETF 都拿 0 分；歷史上每一天的所有候選也都拿 0 分。這類策略有 pullback_to_support、bullish_alignment、low_volatility_leader，以及只選股票的 high_turnover_surge。
(2) 回測的候選排序（engine.py:2075）是穩定排序，全部 0 分時等於按資產索引順序挑前 max_positions 檔。position_sizing=score_weight 會退回等權；只要設了 score_min（大於 0），所有候選都會被濾掉。
(3) 9/23 的快取中，使用者自選的 0050、00878 在 bullish_alignment 是 0 分；0050、00878、00893 在 low_volatility_leader 也是 0 分。

需要修正的細節：
(a) 前端沒有任何地方顯示 score 數值。Dashboard 與 Strategies 頁只顯示名稱、收盤價和漲跌幅，所以「顯示 0 分」不準確。實際看得到的是排序：ETF 一律排在最後，Dashboard 每個策略只預覽前 3 檔，這些 ETF 永遠不會出現。例如 00642U 的 20 日與 60 日動量都是 43 檔候選中最高的（0.1415、0.3348），卻排在最後。
(b) finding 說 9/23 當天股票的非空比例是 54.7%，我重算不出來。現存的 9/23 分區在 22:07 被改寫過，只剩 1,323 列；以代碼 00 開頭判定 ETF，股票 1,087 列全部有換手率。這是分母不同造成的，不影響結論。
(c) 修正建議中「回傳 NaN」會違反 SignalMatrix 的契約：matrix.py:2319-2320 要求 score 全部是有限值。只能改用獨立旗標，或對可得欄位重新正規化權重。

嚴重度維持 P2：這是有明文規範的明確功能錯誤，影響排序與回測選股，但不破壞資料，也不涉及金融口徑。
    - 證據：
      - backend/app/backtest/matrix.py:3690,3706: all_finite 會累積每個評分欄位的 np.isfinite 結果；matrix.py:3741: `score[~universe | ~all_finite] = 0.0`（行號與 finding 相同）
      - backend/app/backtest/matrix.py:3556-3565: pipeline 以 entry 當 universe 呼叫 build_matrix_score，進場旗標不受分數影響
      - backend/app/strategy/engine.py:1251-1265: 所有 entry 候選都附上 score 加入 ranked，依 score 排序後截斷到 limit（limit=500）
      - backend/app/strategy/builtin/pullback_to_support.py:56 `"scoring": {"momentum_60d": 0.4, "momentum_20d": 0.3, "turnover_rate": 0.3}`；bullish_alignment.py:36、low_volatility_leader.py:46 權重相同，且 asset_types 都包含 etf；high_turnover_surge.py:44 也含 turnover_rate（只選股票）
      - CONTRIBUTING.md:141（### 5.1 策略）「評分字段必須有確定的數據來源或臨時計算路徑。缺少輸入時應返回明確的不可計算狀態，不能把空值偽裝成零分。」
      - backend/app/backtest/matrix.py:1439-1447: 台股跨多根 K 棒時，turnover_rate 刻意整欄設成 NaN（避免未來函數）；app\taiwan\enriched_refresh.py:86-89 註解寫明 compute_enriched 會把非今日的台股換手率設成 null
      - backend/app/backtest/engine.py:2056-2075: 回測候選依 np.flatnonzero 的資產順序加入，再以 list.sort 穩定排序；分數全為 0 時，就按資產索引取前 max_positions 檔。engine.py:2068 的 score_min 會濾掉 0 分候選；engine.py:2088-2091 的 score_weight 權重總和為 0 時退回等權
      - backend/app/backtest/matrix.py:2319-2320 `SignalMatrix.score must contain only finite values`：修正建議「回傳 NaN」和這條契約衝突
      - 合成資料實跑（scratchpad\audit\work\verify-missing-score-def\synth.py）：A 的動量最佳但換手率缺值，B 的三個因子全部最差，C、D 正常。輸出為 [0.0, 0.0, 41.47, 80.15]，A 與 B 同為 0.0；換手率全部缺值時，輸出為 [0.0, 0.0, 0.0, 0.0]
      - strategy_cache.json 重算（cache_check.py，as_of 2026-09-23）：pullback_to_support 共 43 列，其中 13 列 score=0 且 turnover_rate=None，都是 finding 列出的那 13 檔 ETF，排在第 30–42 名；00642U 的 momentum_20d=0.1415、momentum_60d=0.3348，是全部候選中最高，卻排在第 31 名。bullish_alignment 166 列中 44 列為 0 分，low_volatility_leader 135 列中 69 列為 0 分，這些列的 turnover_rate 全部是 None
      - 自選股檢查（watch.py）：bullish_alignment 的 0050 排第 125 名、00878 排第 142 名，都是 0 分；low_volatility_leader 的 0050、00878、00893 也都是 0 分
      - turnover_cov.py 重算 kline_daily_enriched：2024-01 到 2026-08 每月非空比例都是 0.0%，這些分區完全沒有 turnover_rate 欄；2026-09 股票為 1087/31479=3.45%，ETF 為 0%，與 finding 一致。9/23 分區（22:07 改寫）只有 1,323 列，股票 1087/1087 全部有值，無法重現 finding 的 54.7%
      - E5 的 28,068 − 28,038 = 30，正好等於 9/23 快取中 pullback_to_support 有正分的非 ETF 候選數（30 列），與「只有 9/23 股票有換手率」一致（我未重跑 E5，這是由資料覆蓋率推得）
      - 前端：frontend\src\pages\Dashboard.tsx:510-523、pages\Strategies.tsx:388-405 只顯示名稱、收盤價和漲跌幅，不顯示 score；lib\watchlist-columns.ts 沒有 score 欄。Dashboard.tsx:417 的 STRATEGY_PICK_LIMIT = 3，所以 0 分的 ETF 不會出現在預覽中
      - https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.MinMaxScaler.html 原文："NaNs are treated as missing values: disregarded in fit, and maintained in transform."
      - https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.rank.html 原文：na_option 預設 'keep'，"keep: assign NaN rank to NaN values"；'top'、'bottom' 需要明確指定
      - 嘗試過但沒有取得的來源：S&P DJI 品質指數方法論 PDF（HTTP 403）、MSCI Quality 方法論 PDF（404），MSCI 方法論索引頁上找不到連結
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：build_matrix_score（matrix.py:3741）只要任一評分欄位不是有限值，就把分數設成 0.0，但候選仍保留在進場與結果列（engine.py:1257-1263），違反 CONTRIBUTING.md:141。以 turnover_rate 評分的三支內建策略（pullback_to_support、bullish_alignment、low_volatility_leader）都受影響，原因有兩個：台股 enriched 從 2024-01 到 2026-08 的換手率全部缺值，ETF 則永遠沒有換手率。
影響分兩條路徑：
(1) 即時選股：9/23 快取中 pullback_to_support 有 13/43、bullish_alignment 有 44/166、low_volatility_leader 有 69/135 列是 0 分且換手率缺值，排序墊底（例如 00642U 的 60 日動量 33%，照理應高分）。
(2) 歷史與回測：用 panel 路徑計算，pullback_to_support 的零分是 28,038/28,068，全部由 NaN 造成；實際回測載入路徑因為 dataset schema 沒有 turnover_rate，整段歷史都是 NaN，零分是 28,068/28,068（100%）。有多筆進場的日子全部同分，回測的前 N 名選擇因此依代號字母順序決定，而且系統不拒絕執行。
修正 finding 的兩處細節：「2026-09-23 股票 54.7%」是以主檔 1,986 檔股票為分母，目前 9/23 分區只有 1,087 列股票且 100% 非空；「UI 顯示 0 分」未證實，已確定的影響是排序墊底。其他反轉策略的少量零分多半是真正的最低分，不是缺值造成的（pullback_ma20_bounce 除外：102 筆中有 91 筆因 momentum_60d 為 NaN）。
    - 理由：我用自己寫的腳本（放在 scratchpad\audit\work\verify-scorenan\），從本地 parquet 和 strategy_cache.json 重新計算。finding 的核心數字全部重現，而且吻合到個位數：
(1) 快取中 pullback_to_support 共 43 列，其中 13 檔 ETF 的 score=0.0 且 turnover_rate=None，排在第 30 到 42 位；score=0 的列和換手率缺值的列完全重疊。
(2) 用全部 enriched 資料組成 panel，再呼叫 build_market_data_matrix 和 MatrixStrategyPipeline 實跑：進場 28,068 筆，score==0 有 28,038 筆，而這 28,038 筆全部都是 turnover_rate 為 NaN 造成的，沒有一筆是真正的最低分。其他六支策略的零分筆數也與 finding 一致（29、102、72、16、15、2）。
(3) 按月統計換手率非空比例：2026-09 股票為 1087/31479=3.45%，ETF 為 0%。
程式碼層面，matrix.py:3741 把任一評分欄位非有限值的格子設為 0；engine.py:1257-1263 不論分數都把候選放進 rows。CONTRIBUTING.md:141 明文禁止這種做法。

需要修正或補充的有五點：
(a) 2026-09-23 股票「54.7%」重現不出來。目前 9/23 分區只有 1087 列股票，而且全部非空（100%）。這個分區在 9/23 22:07 改寫過，TPEx 的列不見了。54.7% 大約等於 1087/1986（主檔全部股票數），分母不同。
(b) 換手率全歷史都缺值，從 2024-01 起每月都是 0%，不只 finding 寫的 2025-06 起。
(c) 實際回測走的是 load_market_data_matrix_from_parquet。pyarrow dataset 的 schema 從第一個分區推斷，該分區沒有 turnover_rate，所以轉走 float_shares 向量路徑，跨多根 K 棒時整欄被設成 NaN。我用唯讀的 cache_root=None 實跑：pullback_to_support 的零分是 28,068/28,068（100%），不是 28,038；596 個有多筆進場的交易日全部同分。回測排序在 engine.py:2075 用穩定排序，資產軸在 matrix.py:2077 依代號排序，所以選前 N 名時實際是依代號字母順序決定（00 開頭的 ETF 最先）。finding 把這點標成「推測」，程式碼層面可以確認，但我沒有實跑完整回測。
(d) 同一個機制也影響 bullish_alignment 和 low_volatility_leader。兩者的 scoring 同樣含 turnover_rate 0.3；快取中分別有 44/166 和 69/135 列是 0 分且換手率缺值，歷史回測零分率達 99.9%（panel）或 100%（loader）。
(e) finding 列的其他反轉策略零分，大多是真正的最低分（所有欄位都有限），不是缺值造成的；只有 pullback_ma20_bounce 的 102 筆中有 91 筆來自 momentum_60d 為 NaN。
另外，「UI 顯示 0 分」未證實：前端 src 沒有找到評分欄的渲染，只有 stock-table.ts:101 的排序取值。目前能確定的影響是排序被墊底。
docs/taiwan-share-capital-phase-6h.md:152 已記錄「以換手率評分的策略不能回測」，但沒提到回測不會拒絕執行、會照常跑完並全部 0 分，也沒提到 ETF 在即時選股中永遠 0 分。所以這條 finding 仍是新面向。嚴重度維持 P2。
    - 證據：
      - backend/app/backtest/matrix.py:3741 `score[~universe | ~all_finite] = 0.0`；all_finite 在 3706 行依每個評分欄位做 &= np.isfinite
      - backend/app/strategy/engine.py:1257 ranked.append((score, {...}))、1263 selected_rows = ranked[:limit]：不論分數，進場候選都會列入
      - backend/app/strategy/builtin/pullback_to_support.py:56、bullish_alignment.py:36、low_volatility_leader.py:46 的 scoring 都是 {momentum_60d:0.4, momentum_20d:0.3, turnover_rate:0.3}
      - CONTRIBUTING.md:141「缺少輸入時應返回明確的不可計算狀態，不能把空值偽裝成零分」
      - s2_pts.py 輸出：strategy_cache.json as_of=2026-09-23，pullback_to_support 共 43 列；score==0 有 13 列，全部是 turnover_rate=None 的 ETF（00404A、00642U、00731、00900、00915、00919、00929、00934、00939、00940、009802、009803、00985A），位置 [30..42]。其中 00642U 的 m60=0.335、m20=0.141，照動量本應高分。同一快取中 bullish_alignment score0=44／turnover_none=44（共 166 列），low_volatility_leader 為 69／69（共 135 列）
      - s6_hist.py（panel＋build_market_data_matrix＋MatrixStrategyPipeline）輸出：pullback_to_support entries=28068、score==0=28038，其中 zero&anyNaN=28038、zero&allFinite=0；n_day_low_reversal 29、pullback_ma20_bounce 102（91 筆為 momentum_60d NaN）、pullback_resume 72、oversold_reversal 16、oversold_bounce 15、bottom_reversal 2，與 finding 一致；非 NaN 的零分是全欄位最低的真實 0 分
      - s6_hist.py（load_market_data_matrix_from_parquet，cache_root=None，唯讀）：dataset.schema has turnover_rate: False，loader 的 turnover 有限值格數為 0；pullback_to_support score==0 為 28068/28068（100%），596 個多筆進場日全部同分；bullish_alignment 為 136660/136660，low_volatility_leader 為 94289/94289
      - backend/app/backtest/matrix.py:1439-1447：台股向量 float_shares 在多根 K 棒時把 turnover_rate 整欄設為 NaN；engine.py:2075 candidates.sort(key=score, reverse=True) 是穩定排序；matrix.py:2077 資產軸依代號排序，同分時依代號選前 N 名
      - s4_cov_split.py 輸出：2024-01 到 2026-08 每月股票與 ETF 的 turnover_rate 非空都是 0；2026-09 股票 1087/31479=0.0345、ETF 0/5801；2026-09-23 目前分區股票 1087/1087=1.0、ETF 0/236（該分區 mtime 為 2026-09-23 22:07，晚於快取 21:40；主檔股票共 1094+892=1986，1087/1986≈0.547）
      - backend/app/backtest/matrix.py:2319-2320 要求 SignalMatrix.score 全為有限值，修法若改成 NaN 需另加旗標
      - docs/taiwan-share-capital-phase-6h.md:152 已記錄「a strategy that scores on turnover_rate still cannot be backtested」，但沒提到回測不拒絕、會照常執行並全部 0 分，也沒提到 ETF 永遠 0 分
      - 前端 frontend/src/lib/stock-table.ts:101 只有排序取值 case 'score'；在 src 中沒找到評分欄位渲染，「UI 顯示 0 分」未證實

### [P2 → P2] composite 的選股與回測在單一候選與同分時歸一化不一致，同分排名還會隨 hash seed 改變（確認成立）

- **主張**：merge_results（選股）在子策略只有 1 個候選時給 norm=1.0，merge_signal_matrices（回測）給中性分 0.5；同分時，選股依 set 的迭代順序決定名次（受 PYTHONHASHSEED 影響），回測則依資產索引的穩定排序。兩者都違反 composite.py 開頭宣稱「必須共享同一套口徑」。
- **正確的定義或數值**：選股與回測使用同一套歸一化：單一候選給同樣的值，同分給平均名次，並以 symbol 排序作為確定的次序。
- **app 實際行為**：兩條路徑的合成分數不同，排名可能互換；同分時的選股結果在每次重啟程序後都可能不同。
- **對使用者的影響**：使用者目前沒有任何 composite 策略（data/strategies 目錄不存在），所以暫時不受影響；一旦建立疊加策略就會發生，而嚴格的反轉策略經常每天只有 0–2 個候選，會觸發單一候選的路徑。
- **修法建議**：把單一候選的規則統一（兩邊都用 0.5 或都用 1.0）；排名改用平均名次（例如 scipy.stats.rankdata 的 average 方法）；把兩條路徑的歸一化抽成共用函式並補上一致性測試。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/composite.py:3-5、:72（docstring 宣稱單一候選用中性分）
  - backend/app/strategy/composite.py:76-83 symbols=set(...)；sorted(...)；norm = 1-(rank-1)/max(count-1,1)，count=1 時為 1.0
  - backend/app/strategy/composite.py:222-224 n<=1 時給 _NEUTRAL_NORM 0.5
  - backend/app/strategy/composite.py:226 np.argsort(-valid_vals, kind='stable')
  - CONTRIBUTING.md:161
- **線上證據**：
  - （無）
- **本地計算證據**：
  - W\s10_composite.py CASE1（A 權重 2、只有 X 一個候選；B 權重 1、Y>W>X）：選股分數 X=66.67、W=50 → 排名 Y、X、W；回測分數 X=33.33、W=50 → 排名 Y、W、X（順序互換）
  - CASE2（4 檔全為 0 分）：PYTHONHASHSEED=1 時 1101=100、3034=0；PYTHONHASHSEED=3 時 1101=0、2454=100；回測固定為 1101=100、2330=66.67、2454=33.33、3034=0
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：主張成立。composite 的選股（merge_results）與回測（merge_signal_matrices）在兩種情況下的歸一化不一致。

- 子策略只有一個候選時，選股給 1.0（composite.py:83），違反 :32 與 :72 自己寫的中性分 0.5；回測則照註解給 0.5（:222-224）。
- 同分時，選股依 set 的迭代順序決定名次（:76、:80）。PYTHONHASHSEED 預設隨機，所以每次重啟程序都可能不同；回測用 stable argsort（:226），依資產索引固定排序。

補充兩個原 finding 沒提到的面向：
- 最終合成排名也受 hash 影響。merged.scores 依 universe 這個 set 的順序建立（composite.py:93-119），engine.py:1357-1365 做穩定排序後以 display limit 截斷，最終同分時上榜名單可能隨重啟改變。
- 兩條路徑的歸一化分母不同。選股的子策略結果在歸一化前已被截斷到 display limit（預設 100，engine.py:655、:969、:1263），回測則對全部命中標的歸一化。

嚴重度維持 P2。使用者目前沒有任何 composite 策略（data\strategies 不存在），所以尚未受影響。

修正方向：兩邊統一單一候選的值（依文件原意用 0.5）；同分時採確定的次序（例如先依 symbol 排序），或改用平均名次；歸一化抽成共用函式並補一致性測試。平均名次是可選的改進，不是唯一正解。
    - 理由：我親自讀了程式碼，並用自己寫的腳本（PYTHONHASHSEED 0 到 5 各跑一次）重現，finding 的主張全部成立，引用的行號也都跟現況相符。

(1) 單一候選的處理不一致。composite.py:32 與 :72 的註解都寫明「单候选…用中性分」（_NEUTRAL_NORM=0.5）。回測的 merge_signal_matrices 在 :222-224 照註解給 0.5；選股的 merge_results 在 :83 用 1-(rank-1)/max(count-1,1)，count=1 時算出 1.0，違反自己的註解。依 Wikipedia Percentile rank 的中位名次公式 PR=(CF-0.5F)/N，單一樣本是第 50 百分位，也就是 0.5，所以回測的 0.5 符合文件原意，選股的 1.0 才是偏離的一方。

(2) 同分時選股的名次不確定。:76 把候選放進 set，:80 再用 sorted(..., reverse=True) 排序。Python 官方文件保證排序是穩定的，reverse 也維持穩定，所以同分標的的先後完全取決於 set 的迭代順序。官方文件也說明 PYTHONHASHSEED 沒設定時會用隨機值替 str 的 hash 加鹽，因此每次重啟程序都可能不同；repo 內查不到任何地方設定 PYTHONHASHSEED。回測在 :226 用 np.argsort(kind='stable')，同分時依資產索引排序，結果固定。Wikipedia Ranking 條目指出：同分若要給不同名次，應採「任意但一致」的規則，或改用平均名次（scipy rankdata 的預設 'average'）。回測符合前者，選股兩者都不符合。

(3) 原 finding 沒提到的新面向：
- 最終合成排名同樣受 hash 影響。merged.scores 在 composite.py:93-119 依 universe 這個 set 的迭代順序建立，engine.py:1357-1365 再對它做穩定排序後依 display limit 截斷。最終分數同分時，上榜名單本身可能因重啟而改變。
- 選股與回測的歸一化分母不同。子策略結果在歸一化之前已被 display limit 截斷（engine.py:655 預設 100，:969／:1263 截斷），回測則對全部命中標的歸一化。

嚴重度：兩條路徑的進場集合相同，只有分數與排名在邊界情況不一致；在回測中，這會影響 max_positions 的取捨與 score_min/score_max 的過濾。這屬於明確的功能錯誤，維持 P2。

使用者情境：data\strategies 目錄不存在，data\backtest_results 裡也沒有任何 composite 紀錄，所以目前不會發生，要等使用者建立疊加策略才會觸發。另外，子策略用 _apply_scoring 時，候選只有 1 檔或欄位值全部相同，都會讓子策略分數同分（engine.py:1518-1520 給 0.5），因此同分的觸發條件並不罕見。

修正建議的附註：「同分改用平均名次」是合理的改進，但不是唯一正確答案。只要把 set 換成確定的次序（例如先依 symbol 排序），並與回測統一單一候選的規則，就能消除本 finding 的缺陷。
    - 證據：
      - backend/app/strategy/composite.py:32-33 註解「子策略无 score 或单候选(无法排名)时的占位」，_NEUTRAL_NORM = 0.5
      - backend/app/strategy/composite.py:72 註解「单候选或无 score 时用中性分」；:76 symbols = set(res.scores.keys())；:80 sorted(symbols, key=..., reverse=True)；:83 norm = 1 - (rank-1)/max(count-1,1)，count=1 時為 1.0
      - backend/app/strategy/composite.py:222-224 n<=1 時給 _NEUTRAL_NORM；:226 np.argsort(-valid_vals, kind="stable")，同分依資產索引排序
      - 新面向：backend/app/strategy/composite.py:93-119 由 universe 這個 set 迭代建立 scores 字典；backend/app/strategy/engine.py:1357-1365 對 merged.scores 做穩定排序後以 limit 截斷，最終同分時上榜名單受 hash 影響
      - 新面向：backend/app/strategy/engine.py:655 limit 預設 100；:969 df.head(limit)；:1263 ranked[:limit]：子策略在歸一化前已被截斷，回測則對全部命中標的歸一化
      - backend/app/strategy/engine.py:1518-1520 min-max 的 range=0 時給 0.5，單一候選或欄位值全部相同時子策略分數同分
      - CONTRIBUTING.md:161「修改選股、評分或排序邏輯時，同時核對歷史策略結果、回測交易記錄和 UI 展示是否使用同一候選集和排序方向」
      - 本地重現：cd backend && PYTHONHASHSEED=<0..5> uv run python AUDIT_SCRATCH/work/verify_s10_composite_def/repro.py
      - Case A（單一子策略、單一候選 2454）：每個 seed 都是選股 100.0、回測 50.0
      - Case B（A 權重 2 只有 X；B 權重 1，Y>W>X）：選股 Y=100、X=66.6667、W=50，名次 Y、X、W；回測 Y=100、W=50、X=33.3333，名次 Y、W、X
      - Case C（4 檔全部 0 分）：seed 0/2/5 為 1101=100、2330=66.67、2454=33.33、3034=0；seed 1 為 1101=100、2454=66.67、2330=33.33、3034=0；seed 3 為 2454=100、1101=0；seed 4 為 2330=100。回測在所有 seed 下固定為 1101=100、2330=66.67、2454=33.33、3034=0
      - Case D（兩個子策略各一個候選，最終同分 100）：最終名次在 seed 0/3 為 [3034, 2454]，seed 1/2/4/5 為 [2454, 3034]
      - repo 內 grep 不到 PYTHONHASHSEED；data/strategies 不存在（ls 報 No such file），data\backtest_results 為空，也沒有任何 composite 紀錄
      - https://docs.python.org/3/using/cmdline.html 原文："If this variable is not set or set to random, a random value is used to seed the hashes of str and bytes objects."；"hash randomization is enabled by default"；"they are not predictable between repeated invocations of Python"
      - https://docs.python.org/3/howto/sorting.html 原文："Sorts are guaranteed to be stable. That means that when multiple records have the same key, their original order is preserved."；"The reverse parameter still maintains sort stability"
      - https://en.wikipedia.org/wiki/Ranking 原文："it is generally preferable to use a system that is arbitrary but consistent, as this gives stable results if the ranking is done multiple times"；Fractional ranking："Items that compare equal receive the same ranking number, which is the mean of what they would have under ordinal rankings"
      - https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.rankdata.html：method 預設 'average'，rankdata([0, 2, 3, 2]) → [1., 2.5, 4., 2.5]
      - https://en.wikipedia.org/wiki/Percentile_rank：PR = (CF - 0.5×F)/N×100，單一樣本時 PR=50，支持單一候選給 0.5 中性分
      - 工作樹比對：驗證前後 git status --short 都只有原本就存在的 3 個 M 檔案，沒有新增未追蹤檔
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：finding 成立，數值全部精確重現。補充三點：(a) 除了子策略內部同分，合成後同分的排序（composite.py:93/105 的 set 迭代，再經 engine.py:1356 的穩定排序）同樣受 PYTHONHASHSEED 影響，而回測依資產索引固定排序；(b) 兩條路徑的絕對分數不同（單一子策略、單一候選時，選股 100、回測 50），會讓回測的 score_min／score_max 篩選與選股顯示的分數不一致；(c) 單候選在實務上很常見：以本地資料近似計算，oversold_reversal 在 419 個交易日中有 121 天恰好 1 檔（28.9%），≤2 檔佔 79.2%。全部 0 分的同分主要發生在候選特徵缺值時（matrix.py:3741）。使用者目前沒有 composite 策略，不受影響。
    - 理由：我先讀程式碼，再用自己寫的腳本直接呼叫 app.strategy.composite 裡的兩個純函式，finding 的數值全部精確重現。

(1) 單一候選的處理不一致：composite.py:83 的 norm = 1 - (rank-1)/max(count-1,1)，count=1 時得 1.0；composite.py:222-224 在 n<=1 時給 _NEUTRAL_NORM=0.5。composite.py:72 的註解自己寫著「單候選…用中性分」，所以 merge_results 連自己的註解都違反了。

(2) 同分時的處理不一致：composite.py:76 用 set 收集候選，:80 再用 sorted() 排序。同分時名次取決於字串 hash 決定的 set 迭代順序，repo 內沒有任何地方設定 PYTHONHASHSEED。回測在 composite.py:226 用 argsort(kind='stable')，按資產索引排序，結果是確定的。

我另外發現兩個 finding 沒提到的面向。第一，合成後的同分也不確定：composite.py:93/105 以 set 迭代順序寫入 scores dict，engine.py:1356-1360 再做穩定排序，所以同分時的先後同樣受 hash seed 影響；回測在 engine.py:2075 按資產索引穩定排序。第二，兩邊的絕對分數不同，會讓回測的 score_min／score_max 篩選（backtest/strategy.py:1042、engine.py:2068）與選股畫面顯示的分數對不上。例如單一子策略只有一個候選時，選股顯示 100，回測只有 50。

使用者影響：data/strategies 不存在（ls 回傳 No such file），builtin/ 裡也沒有 composite 策略，所以使用者目前確實不受影響。至於「嚴格反轉策略經常只有 0–2 個候選」這一句，我用本地 enriched parquet 近似驗證：從 2025-01-01 起共 419 個交易日，不套 basic_filter，oversold_reversal 預設條件下每日候選數 ≤2 的比例是 79.2%，剛好 1 檔的有 121 天（28.9%）。另外 matrix.py:3735 在單候選時會把子策略分數設為 50，matrix.py:3741 會把特徵非有限值的候選設為 0 分，所以 CASE2 那種全部 0 分的同分，在實務上主要出現在候選特徵缺值的時候。

嚴重度維持 P2：這是明確的功能錯誤，選股與回測的口徑不一致，但目前使用者沒有任何 composite 策略，不受影響。
    - 證據：
      - backend/app/strategy/composite.py:72 註解寫明「单候选或无 score 时用中性分」；:76 symbols = set(res.scores.keys())；:80 sorted(symbols, key=score, reverse=True)；:83 norm = 1 - (rank-1)/max(count-1,1)，count=1 時得 1.0
      - backend/app/strategy/composite.py:222-224 n<=1 時給 _NEUTRAL_NORM（:33 定義為 0.5）；:226 np.argsort(-valid_vals, kind="stable")
      - backend/app/strategy/composite.py:93/105 合成時對 universe 這個 set 迭代，決定 scores dict 的插入順序；engine.py:1356-1360 以 sorted(merged.scores.keys(), key=score) 排序，同分時保留 set 順序；回測 engine.py:2075 candidates.sort 為穩定排序，同分時依資產索引
      - 重現腳本 AUDIT_SCRATCH/work/verify-composite-norm/repro.py，指令 `cd backend && PYTHONHASHSEED=<0..5> uv run python repro.py`，輸出在同目錄 out.txt
      - CASE1 並列比較：finding 寫選股 X=66.67、W=50，排名 Y、X、W；回測 X=33.33、W=50，排名 Y、W、X。本次實測選股 {Y:100.0, X:66.6667, W:50.0}，排名 [Y,X,W]；回測 {Y:100.0, W:50.0, X:33.3333}，排名 [Y,W,X]。6 個 seed 全部相同，完全一致
      - CASE2 並列比較：finding 寫 seed=1 時 1101=100、3034=0。實測 seed=1：1101=100、2454=66.67、2330=33.33、3034=0。finding 寫 seed=3 時 1101=0、2454=100。實測 seed=3：2454=100、2330=66.67、3034=33.33、1101=0。另外 seed=4 時 2330 拿到 100、1101 只有 33.33。回測在 6 個 seed 下都固定為 1101=100、2330=66.67、2454=33.33、3034=0。與 finding 一致
      - 新增 CASE3（合成後同分，A: 2379>6196、B: 6196>2379，權重相同，兩檔都是 50）：選股排名在 seed 0/1/3/4/5 為 [6196, 2379]，seed 2 為 [2379, 6196]；回測固定為 [2379, 6196]
      - 新增 CASE4（單一子策略、單一候選 2454）：選股分數 100.0，回測分數 50.0。回測的 score_min 篩選（engine.py:2068，來源為 backtest/strategy.py:1042 的 overrides.score_min）會因此與選股畫面顯示的分數對不上
      - 使用者影響：`ls data/strategies` 回傳 No such file or directory；settings.data_dir 解析為 data；app/strategy/builtin/ 內 grep 不到 composite
      - 候選數頻率：cand_counts.py 讀本地 kline_daily_enriched，範圍為 2025-01-01 起 419 個交易日的 .TWSE/.TPEX，不套 basic_filter，屬於近似值。oversold_reversal 的條件（rsi_14<30、change_pct>0.01、close>ma5）：0 檔 139 天、1 檔 121 天、2 檔 72 天，≤2 檔佔 79.2%。n_day_low_reversal：1 檔 86 天，≤2 檔佔 51.3%
      - 子策略分數來源：matrix.py:3735 在單候選或當日無變異時設為 0.5，所以子策略分數為 50；matrix.py:3741 把特徵非有限值的候選設為 0 分，是全部 0 分同分的主要成因
      - CONTRIBUTING.md:161 原文：「修改選股、評分或排序邏輯時，同時核對歷史策略結果、回測交易記錄和 UI 展示是否使用同一候選集和排序方向。」
      - git status --short 與開始時的快照相同，repo 未被修改

### [P3 → P3] bottom_reversal 的「爆量」只和前一天比，不是台股慣用的 5 日均量倍數（確認成立）

- **主張**：止跌 K 的量能條件是 stop_volume ≥ 前一日量×2。前一天剛好量縮時，普通量也會被當成爆量；連續放量的真正賣壓高潮反而不成立。
- **正確的定義或數值**：爆量以前 5 日（或 10 日）均量的倍數判斷，可以直接沿用 vol_ratio_5d。
- **app 實際行為**：只和單日比較，雜訊大，和台股的爆量慣例不同。
- **對使用者的影響**：影響深跌止穩反彈的候選品質；9/23 沒有命中。
- **修法建議**：改成 shift(vol_ratio_5d,1) ≥ stop_bar_vol_ratio，也就是止跌 K 當日量除以前 5 日均量。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/builtin/bottom_reversal.py:119-121 climax_volume = stop_volume >= shift(market.volume, 2) * stop_bar_vol_ratio
  - backend/app/strategy/builtin/bottom_reversal.py:43 標籤為「止跌K最低量倍數」
- **線上證據**：
  - https://murmurcats.com/trading-volume/：「若今日成交量高於過去5日、10日平均 2-3 倍以上，就可稱為「爆量」」
- **本地計算證據**：
  - W\s9_experiments.py E3（全市場全歷史的股票×日）：量 ≥2×前一日有 202,682 筆；量 ≥2×前 5 日均量有 149,162 筆；只符合「前一日」的有 101,862 筆；符合 5 日均量標準卻被排除的有 48,342 筆
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：成立。bottom_reversal 判斷止跌 K 爆量的條件是「stop_volume ≥ 前一根有效 K 棒的量 × 2」（bottom_reversal.py:119-121），和一般慣例「當日量 ≥ 前 5 日（或 10 日）均量 × 2～3」不同，也和 repo 自己的 signal_volume_surge（vol_ratio_5d≥2，pipeline.py:659）不一致。finding 的數字（202,682／149,162／101,862／48,342）是用共用日曆軸算的，會把停牌後的 K 棒排除；app 實際以有效 K 棒 shift，同一資料範圍（2,346 檔、2024-01-02～2026-09-23）的正確數字是：只和前一日比 ≥2 倍 211,734 筆；≥ 前 5 日均量 2 倍 155,013 筆；兩者都符合 105,121 筆；只符合前一日 106,613 筆（其中 95,270 筆的前一日量縮到 <0.7 倍均量）；只符合 5 日均量 49,892 筆（其中 28,779 筆是連續放量）。放到整個策略看：現行規則進場 287 筆，改用 shift(vol_ratio_5d,1)≥2 為 347 筆，只重疊 163 筆。9/23 兩種規則都沒有命中，自選股只有 00893 在 2025-04-08 命中，兩種規則相同，所以目前對使用者沒有實際影響。
    - 理由：程式碼、線上來源、數值三方面都重現了。
(1) 程式碼：bottom_reversal.py:119-121 的寫法是 climax_volume = stop_volume >= shift(market.volume, 2) * stop_bar_vol_ratio，也就是止跌 K 的量只和它前一根比；:43 的 label 是「止跌K最低量倍數」，:27 的 description 寫「深跌後爆量止跌K」。另外確認了一點：這條策略經 MatrixStrategyPipeline.run（matrix.py:3514）執行時會啟用 valid bar index，所以 shift 以「有效 K 棒」為單位，會跳過停牌日。
(2) 線上來源：murmurcats 原文有這句：「若今日成交量高於過去5日、10日平均 2-3 倍以上，就可稱為「爆量」」。repo 自己的 signal_volume_surge 也用 vol_ratio_5d>=2.0（pipeline.py:659），vol_ratio_5d 的定義是當日量除以前 5 日均量（factor.py:74）。所以 bottom_reversal 不只和一般慣例不同，和專案內其他地方的「爆量」口徑也不一致。
(3) 數值：我用 data/taiwan/daily 的 661 個分區自己重算，範圍 2,346 檔（含 ETF，TWSE+TPEX），期間 2024-01-02～2026-09-23。用 finding 的方法（共用日曆軸，停牌缺口會中斷視窗）算出的數字和 finding 完全相同：202,682／149,162／both 100,820／只符合前一日 101,862／只符合 5 日均量 48,342。改成 app 實際的有效 K 棒語意，用 app 自己的 valid_shift 與 matrix_feature('vol_ratio_5d') 算，和 polars 的獨立算法兩邊一致：211,734／155,013／105,121／106,613／49,892，比 finding 多約 4%，方向不變。
claim 的兩個敘述都有數據支撐：只符合前一日的 106,613 筆中，有 95,270 筆的前一根量不到它自己 5 日均量的 0.7 倍，也就是前一天剛好量縮、普通量被當成爆量；只符合 5 日均量的 49,892 筆中，有 28,779 筆的前一根也已達 5 日均量 2 倍以上，也就是連續放量被排除。
策略層級的影響（未復權價、不含成本、樣本內）：現行規則全歷史進場 287 筆；改用 shift(vol_ratio_5d,1)>=2 有 347 筆，兩者重疊 163 筆。9/23、9/22、9/18 兩種規則都沒有命中；自選股只有 00893 在 2025-04-08 命中，兩種規則都有。10 根 K 棒後報酬：現行規則平均 4.80%、勝率 68.8%；替代規則 6.09%、76.2%；只有現行規則命中的 3.71%、63.7%；只有替代規則命中的 6.48%、79.3%。但這是樣本內結果，而且進場日集中在少數幾天（例如 2025 年 4 月），所以「替代規則品質較好」只能算推測。
嚴重度維持 P3：這是策略口徑與命名不一致，目前沒有造成自選股誤判，也不是資料破壞。
附帶說明（不影響本 finding）：官方 STOCK_DAY 380 列中，有 16 列的本地量低於官方，集中在 9/16～9/23，例如 2330 在 9/18 少了 3,702,177 股。用官方資料算這 10 檔、8～9 月：只符合前一日 15 次、只符合 5 日均量 3 次，方向相同。data/backend.log 在我執行期間有更新，但內容是使用者自己執行中後端的 watchfiles 紀錄，不是我的腳本寫的；git status 前後相同。
    - 證據：
      - backend/app/strategy/builtin/bottom_reversal.py:119-121 climax_volume = stop_volume >= shift(market.volume, 2) * float(params.get("stop_bar_vol_ratio", 2.0))
      - backend/app/strategy/builtin/bottom_reversal.py:43 label「止跌K最低量倍數」；:27 description「深跌後爆量止跌K」
      - backend/app/backtest/matrix.py:3514 MatrixStrategyPipeline.run 以 _activate_valid_bar_index(market.valid_bars) 包住 compute_signals，所以 shift 以有效 K 棒為單位
      - backend/app/indicators/pipeline.py:659 signal_volume_surge = vol_ratio_5d >= 2.0；app\backtest\factor.py:74 vol_ratio_5d =「当日成交量 / 前5日平均成交量」
      - https://murmurcats.com/trading-volume/ 原文：「若今日成交量高於過去5日、10日平均 2-3 倍以上，就可稱為「爆量」」（WebFetch 2026-09-24）
      - 腳本 AUDIT_SCRATCH/work/verify-brclimax/v3_calendar.py（日曆軸，也就是 finding 的方法）輸出：n=1379172 prev>=2x=202682 avg5>=2x=149162 both=100820 prev_only=101862 avg_only=48342，與 finding 完全一致
      - 腳本 v1_counts.py（polars 有效 K 棒）與 v2_strategy.py（app valid_shift＋matrix_feature('vol_ratio_5d')）輸出相同：n=1411869 prev>=2x=211734 vr5>=2=155013 both=105121 prev_only=106613 avg_only=49892
      - v2_strategy.py：只符合前一日、且前一根量 <0.7 倍自身 5 日均量的有 95270 筆；只符合 5 日均量、且前一根也 >=2 倍的有 28779 筆
      - v2_strategy.py：進場 current=287、alt(shift(vol_ratio_5d,1)>=2)=347、both=163、current_only=124、alt_only=184；2026-09-23、09-22、09-18 兩種規則都是 []；自選股只有 00893.TWSE 在 2025-04-08 命中，兩種規則相同
      - v2_strategy.py：10 根 K 棒後報酬 current 平均 0.0480、勝率 0.688；alt 0.0609、0.762；current_only 0.0371、0.637；alt_only 0.0648、0.793（樣本內、未復權、不含成本）
      - v4_official.py（官方 twse_stock_day_202608_202609.csv，10 檔）：n=330 prev>=2x=30 avg5>=2x=18 prev_only=15 avg_only=3；例如 00893 在 2026-09-18，r_prev=2.92、r_avg5=1.12（對前一日算是爆量，對 5 日均量並沒有）

### [P3 → P3] 兩支回檔策略的進場訊號被標成「MA5/MA20 黃金交叉」，但實際上很少是黃金交叉（確認成立）

- **主張**：pullback_ma20_bounce 與 pullback_to_support 的 entry_signal_ids 都是 signal_ma_golden_5_20，每一筆進場都會被歸因成黃金交叉，但進場條件和交叉無關。
- **正確的定義或數值**：進場歸因使用策略自己的 id（例如 signal_pullback_ma20），或在真正發生交叉時才標示。
- **app 實際行為**：回測成交紀錄的 entry_signal_id 與監控通知都顯示錯誤的訊號原因。
- **對使用者的影響**：回測歸因與（未來的）監控通知會誤導使用者；目前監控規則為 0 條，只影響回測紀錄的顯示。
- **修法建議**：把 entry_signal_ids 改為策略專屬 id，並同步 ENTRY_SIGNALS 常數。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/builtin/pullback_ma20_bounce.py:48、:85
  - backend/app/strategy/builtin/pullback_to_support.py:63、:100
  - backend/app/strategy/engine.py:1384-1396（依 code 對應 signal id）
  - backend/app/strategy/monitor.py:1164-1172（通知會帶出 hit_signals）
  - frontend/src/lib/api.ts:1860 entry_signal_id（回測成交紀錄）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - W\s12_direction.py：pullback_ma20_bounce 16,952 筆進場中，當根真的有 MA5 上穿 MA20 的只有 1,473 筆（8.7%）；pullback_to_support 28,068 筆中只有 545 筆（1.9%）
  - strategy_cache.json 2026-09-23：pullback_ma20_bounce 17 列中 signal_ma_golden_5_20 為 True 的只有 4 列；pullback_to_support 43 列中只有 2 列
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：pullback_ma20_bounce 與 pullback_to_support 的 ENTRY_SIGNALS 和 entry_signal_ids 都只有 signal_ma_golden_5_20，而且 entry 為真時一律寫 code 0，所以每一筆進場都被歸因成 MA5 上穿 MA20。但兩支策略的進場條件都不要求交叉：前者只要求 MA5>MA20 的狀態，後者沒有任何 MA5 條件。

重現結果（2024-01-02～2026-09-23，套 DEFAULT_BASIC_FILTER）：
- pullback_ma20_bounce：16,952 筆進場中，當根真的交叉的只有約 1,478 筆（8.7%）。
- pullback_to_support：28,068 筆進場中，真的交叉的只有約 543 筆（1.9%）。
- strategy_cache 2026-09-23：兩支策略分別只有 4/17 與 2/43 列為真交叉。

使用者影響需要更正：
- 回測成交紀錄的 entry_signal_id 雖然錯，但前端沒有顯示它。
- 真正看得到的誤導是 Dashboard 與策略頁的策略規則面板（StrategyRule），它把這兩支策略的進場訊號寫成「MA5上穿MA20」。
- 監控通知也會帶出同樣的錯誤原因，但目前使用者監控規則為 0 條，不會觸發。

不影響任何成交或報酬。
    - 理由：程式碼與數據都重現了。兩支策略的 compute_signals 在 entry 為真時一律寫 entry_signal_code=0，而 entry_signal_ids 只有 ("signal_ma_golden_5_20",)，所以每一筆進場都被標成 MA5/MA20 黃金交叉。但 pullback_ma20_bounce 只要求 ma5>ma20 這個「狀態」，不要求「交叉」；pullback_to_support 則完全沒有 MA5 條件。

我自己寫腳本讀 kline_daily_enriched（2024-01-02～2026-09-23，2,346 檔，1,423,597 列），用 app 的 MatrixStrategyPipeline 加上 DEFAULT_BASIC_FILTER 與預設參數跑兩支策略，並用 app 自己的交叉定義（pipeline.py:622）重算當根是否真的上穿：
- pullback_ma20_bounce：finding 寫 16,952 筆、交叉 1,473 筆（8.7%）；我算出 16,952 筆（MA 由收盤價計算）或 16,950 筆（MA 用已存欄位），交叉 1,478／1,479 筆（8.72%／8.73%）。
- pullback_to_support：finding 寫 28,068 筆、交叉 545 筆（1.9%）；我算出 28,068 筆、交叉 543／544 筆（1.93%／1.94%）。
- 兩支策略的標籤分布都是 100% signal_ma_golden_5_20。
- 不套基本過濾時比例相近：8.48%、2.27%。
- strategy_cache.json（as_of 2026-09-23）：pullback_ma20_bounce 17 列中 4 列為 True，pullback_to_support 43 列中 2 列為 True，與 finding 完全一致。

交叉筆數有 1～6 筆的差距，推測來自兩邊計算 MA 前一根的方式不同（valid_shift 對上原始索引減一），不影響結論。

需要修正的是「使用者影響」的描述：
- 前端從來不顯示回測成交紀錄的 entry_signal_id。它只出現在 api.ts:1860 的型別宣告，整個 frontend/src 沒有任何頁面使用 trades、pnl_pct、entry_price。所以「回測成交紀錄顯示錯誤原因」只存在於 API 回傳的資料，使用者在畫面上看不到。
- 真正看得到的錯誤在另一處：ENTRY_SIGNALS 經 api/strategy.py:215 傳給 StrategyRule，Dashboard.tsx:507 與 Strategies.tsx:385 的策略規則面板會寫「進場訊號：MA5上穿MA20」，直接誤述這兩支策略的進場邏輯。
- 監控通知（monitor.py:39 對應成「MA5上穿MA20」）在規則為 0 條時不會觸發。

這個問題不影響任何成交或報酬，維持 P3。
    - 證據：
      - backend/app/strategy/builtin/pullback_ma20_bounce.py:48 ENTRY_SIGNALS = ["signal_ma_golden_5_20"]；:83 entry_signal_code=np.where(entry, 0, -1)；:85 entry_signal_ids=("signal_ma_golden_5_20",)；:72-73 只有 (ma5 > ma20) & (ma20 > ma60)，屬於狀態條件，不是交叉
      - backend/app/strategy/builtin/pullback_to_support.py:63、:98、:100 同上；進場條件（:81-92）只有 MA20 附近、vol_ratio_5d<0.8、close>ma60、momentum_20d>0，沒有 MA5 條件
      - backend/app/backtest/engine.py:887-888、:1097-1098 用 _signal_id(code, ids) 取 ids[0] 寫入成交紀錄的 entry_signal_id；backend/app/strategy/engine.py:1223-1228、:1384-1394 _matrix_signal_hits 用同一組 code 對應訊號
      - backend/app/indicators/pipeline.py:622-624 app 自己的交叉定義是 (ma5>ma20) & (前一根 ma5 <= 前一根 ma20)
      - 腳本 AUDIT_SCRATCH/work/verify-pbgold/repro_pipeline.py（指令：cd backend && uv run python <腳本>）輸出：{stored_ma_fields: false, pullback_ma20_bounce: {entries: 16952, labels: {signal_ma_golden_5_20: 16952}, true_cross: 1478, pct: 8.72}, pullback_to_support: {entries: 28068, labels: {signal_ma_golden_5_20: 28068}, true_cross: 543, pct: 1.93}}；stored_ma_fields: true 時為 16950/1479/8.73%、28068/544/1.94%
      - 與 finding 並列比較：pullback_ma20_bounce 的進場筆數 16,952 對 16,952、交叉 1,473 對 1,478；pullback_to_support 的進場筆數 28,068 對 28,068、交叉 545 對 543
      - 腳本 ...\verify-pbgold\repro.py 不套基本過濾：pullback_ma20_bounce 43,415 筆中交叉 3,681 筆（8.48%），pullback_to_support 75,516 筆中交叉 1,713 筆（2.27%）；已存的 signal_ma_golden_5_20 欄位與重算結果一致（3,680 / 1,716）
      - 同一支腳本讀 data/user_data/strategy_cache.json（as_of 2026-09-23）：pullback_ma20_bounce 17 列中 4 列為 True（4912、2317、00985A、00982A）；pullback_to_support 43 列中 2 列為 True（4912、00985A）
      - frontend/src/lib/api.ts:1860 只有型別宣告；在 frontend/src 搜尋 entry_signal_id、trades、pnl_pct、entry_price，除 api.ts 外沒有任何命中，代表前端不顯示回測成交紀錄
      - backend/app/api/strategy.py:215 回傳 entry_signals=ENTRY_SIGNALS → frontend/src/components/StrategyRule.tsx:18、:49-51 以 cnSignal 顯示「進場訊號：MA5上穿MA20」（frontend\src\lib\signals.ts:27-28）；用在 frontend\src\pages\Dashboard.tsx:507 與 frontend\src\pages\Strategies.tsx:385
      - backend/app/strategy/monitor.py:39 把 signal_ma_golden_5_20 對應成「MA5上穿MA20」；:1164-1172 會把 hit signals 帶進通知；使用者監控規則為 0 條，目前不會觸發
      - 驗證前後 git status --short 相同（只有原本就存在的 3 個改動），沒有寫入 repo

### [P3 → P3] pullback_to_support 允許在跌破 MA20 當天進場，與自己的出場訊號同一根（確認成立）

- **主張**：進場只要求收盤在 MA20 ±2% 內，沒有要求站在 MA20 之上或當日收紅，所以當天剛跌破 MA20（正是它的出場訊號）也會被當成「回踩支撐」而進場。
- **正確的定義或數值**：回踩支撐應要求支撐守住（close ≥ MA20，或下影線觸及 MA20 後收回）。
- **app 實際行為**：同一根 K 棒同時給出買進與賣出。
- **對使用者的影響**：縮量回踩清單中約 14% 的候選其實是跌破月線的標的。
- **修法建議**：加上 close ≥ MA20（或 low ≤ MA20 ≤ close）條件。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/builtin/pullback_to_support.py:80-84（±proximity 帶狀）
  - backend/app/strategy/builtin/pullback_to_support.py:93 exit_ = (close < ma20) & (prev close >= prev ma20)
- **線上證據**：
  - （無）
- **本地計算證據**：
  - W\s9_experiments.py：28,068 筆進場中，進場當根就有出場訊號的佔 14.3%，隔天有出場訊號的佔 20.3%（pullback_ma20_bounce 分別為 0.2% 與 28.1%）
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：finding 的主張與數字都成立。pullback_to_support 的進場只要求收盤落在 MA20 ±2% 帶內，沒有要求 close ≥ MA20 或當日收紅；出場訊號是收盤由上往下跌破 MA20。所以收盤在 [0.98·MA20, MA20) 且前一日仍在 MA20 上方時，同一根 K 棒會同時產生進場與出場。2024-01～2026-09 全期、套用預設基本過濾的結果，兩種獨立方法一致：28,068 筆進場中，4,014～4,016 筆（14.3%）進場當根就有出場訊號；隔一根有出場訊號的佔 20.3%（以進場總數為分母，以有隔日資料者為分母則是 20.4%）。pullback_ma20_bounce 的對應數字是 0.2% 與 28.1%。

需要補充的地方：
- 14.3% 是全期合併比例。個股為 16.3%，ETF 為 7.4%；每天也差很多，例如 2026-09-23 只有 2/43（00642U、1303，已用官方收盤核實）。
- 若把「跌破月線」解讀為進場當天收盤就在 MA20 下方，全期比例是 30.7%（2026-09-23 為 6/43），影響比 finding 描述的更大。
    - 理由：程式碼與數據都重現了這條 finding。

程式碼面：pullback_to_support 的進場條件只有四項：收盤在 MA20 的 ±2% 帶內（第 80-84 行）、vol_ratio_5d < 0.8（第 85-88 行）、收盤 > MA60（第 89-90 行）、20 日動量 > 0（第 91-92 行）。進場條件沒有 close ≥ MA20，也沒有當日收紅。出場訊號在第 93 行，定義為當日收盤 < MA20 且前一日收盤 ≥ 前一日 MA20。當日收盤落在 [0.98·MA20, MA20) 而前一日仍在 MA20 上方時，同一根 K 棒會同時觸發進場與出場。MatrixStrategyPipeline 只把 entry 與 basic filter 做 AND（matrix.py:3556），出場沿用策略原始輸出（matrix.py:3576），框架層不處理兩者衝突。對照組 pullback_ma20_bounce 要求 change_pct > 0，且 MA5 > MA20 > MA60（pullback_ma20_bounce.py:72-75），所以衝突極少。

數據面我用兩種互不依賴的方法重算，資料範圍都是本地 kline_daily_enriched 2024-01-02～2026-09-23，共 661 個交易日、2,346 檔、1,423,597 列：
(A) 純 polars float64 自行實作指標，並套 DEFAULT_BASIC_FILTER（收盤 ≥ 3、成交金額 ≥ 2e7）；
(B) 直接呼叫 app 的純計算函式 build_market_data_matrix、MatrixStrategyPipeline.run 與 compute_signals。

兩種方法與 finding 的數字並列如下：
- 進場數：finding 28,068／A 28,068／B 28,068
- 進場當根即有出場訊號：finding 14.3%／A 4,014 筆（14.3%）／B 4,016 筆（14.3%）。差 2 筆來自 float32 的邊界捨入。
- 隔天有出場訊號：finding 20.3%／A 5,704 筆，除以進場總數 28,068 為 20.3%，除以有隔日資料的 28,020 為 20.4%／B 相同。finding 的分母是全部進場數。
- pullback_ma20_bounce 的當根出場：finding 0.2%／A 30/16,959（0.2%）／B 30/16,952（0.2%）。
- pullback_ma20_bounce 的隔天出場（跌破 MA20 或死叉）：finding 28.1%／A 4,769/16,940（28.2%）／B 4,761/16,933（28.1%）。

官方資料交叉核對：
- 參考清單的 10 檔在 2026-08～09 共 452 個本地有資料的交易日，本地 close 與證交所 STOCK_DAY、櫃買月資料逐筆比對不一致數為 0。只用官方收盤算出的 MA20 也與本地一致，例如 0050 在 2026-09-15 官方 MA20 為 106.6725，本地也是 106.6725。
- 2026-09-23 共 43 筆進場，收盤全部與官方當日行情（MI_INDEX／櫃買日行情）一致。其中兩筆是同根衝突，官方收盤與昨收都證實當日跌破 MA20：
  - 00642U：收 30.66、昨收 32.05，MA20 31.183，昨日 MA20 30.993。
  - 1303：收 227.5、昨收 231.5，MA20 232.075，昨日 MA20 231.05。

需要補充的細節（不推翻結論）：
1. 「約 14%」是 2024-01～2026-09 全期合併的比例，每天差異很大。2026-09-23 只有 2/43（4.7%）是當日跌破；同日收盤在 MA20 下方的有 6/43（14.0%）。
2. 若把「跌破月線」解讀成進場當天收盤 < MA20（不限當日交叉），全期比例是 8,626/28,068（30.7%），比 finding 講的更大。
3. 個股（不含 00 開頭）的同根衝突率為 16.3%，ETF 為 7.4%。
4. 2026-09-23 的兩筆衝突都不在使用者的 9 檔自選內。自選股歷史上確實出現過同根衝突，例如 2454 在 2025-12-23、3034 在 2026-04-02、0050 在 2026-06-10。

線上依據：Wikipedia「Support and resistance」寫道 "once the price has breached this level, by an amount exceeding some noise, it is likely to continue falling until meeting another support level"。這句話支持「跌破支撐就不再是回踩買點」的看法。不過它也提到噪音容忍，所以 ±2% 的下緣本身不一定算錯。真正的問題是進場帶和出場觸發點不一致：出場在收盤一低於 MA20 時就觸發，完全沒有容忍區間。嚴重度維持 P3，理由是策略描述明寫「回踩MA20附近」，而且只影響策略清單的品質，不是資料破壞。

限制：本 session 的 WebSearch 額度已用完（200/200），Investopedia 無法抓取，線上證據只有這一條 Wikipedia。
    - 證據：
      - backend/app/strategy/builtin/pullback_to_support.py:80-84：進場只用 close > ma20*(1-0.02) 且 close < ma20*(1+0.02)，沒有 close >= ma20
      - backend/app/strategy/builtin/pullback_to_support.py:93：exit_ = (market.close < ma20) & (shift(market.close, 1) >= shift(ma20, 1))
      - backend/app/backtest/matrix.py:3556,3576：pipeline 只把 entry 與 basic_mask 做 AND，exit 原樣輸出，框架不處理 entry 與 exit 同根
      - backend/app/strategy/builtin/pullback_ma20_bounce.py:72-75：對照組另外要求 MA5 > MA20 > MA60 與 change_pct > 0
      - 方法 A（獨立 polars）：cd backend && uv run python ...\work\verify-pbsup\v1_indep.py → pullback_to_support (basic filter): entries=28068 same_bar_exit=4014 (14.3%) next_bar_exit=5704/28020 (20.4%) close<ma20_at_entry=8626 (30.7%)；pullback_ma20_bounce exit=breakdown|dead: entries=16959 same_bar_exit=30 (0.2%) next_bar_exit=4769/16940 (28.2%)
      - 方法 A 分組：個股 same_bar 3551/21773 (16.3%)；ETF(00*) 463/6295 (7.4%)；本地儲存的 ma20 與自算 ma20 最大相對差 0.0；儲存的 signal_ma20_breakdown 在進場列為 true 的有 4014/28068
      - 方法 B（app 純計算函式）：NUMBA_CACHE_DIR 指到工作目錄後執行 ...\work\verify-pbsup\v2_app.py → pullback_to_support: entries=28068 same_bar_exit=4016 (14.3%) next_valid_bar_exit=5704/28020 (20.4%; /entries 20.3%)；pullback_ma20_bounce: entries=16952 same_bar_exit=30 (0.2%) next_valid_bar_exit=4761/16933 (28.1%; /entries 28.1%)
      - 官方比對（...\work\verify-pbsup\v3_official.py）：參考清單的 10 檔在 2026-08～09 共 456 個 symbol-day，其中 452 筆本地有資料，本地 close 與官方 close_twd 不一致數為 0
      - 官方比對 2026-09-23：43 筆進場收盤全部與 twse_mi_index_20260923_quotes.csv／tpex_daily_quotes_20260923.csv 一致。00642U.TWSE 官方收盤 30.66、昨收 32.05（收盤減漲跌推得）、MA20=31.183、昨日 MA20=30.993；1303.TWSE 官方收盤 227.5、昨收 231.5、MA20=232.075、昨日 MA20=231.05，兩筆都同時觸發進場與出場
      - 2026-09-23 當日：同根衝突 2/43（4.7%）；收盤低於 MA20 的進場 6/43（14.0%），另外 4 筆是 00919、00934、009803、2353
      - 自選股的歷史同根衝突範例（本地計算）：2454.TWSE 2025-12-23 收 1390、昨收 1400、MA20 1406.5、昨日 MA20 1396.25；3034.TWSE 2026-04-02 收 382、昨收 391.5、MA20 382.975、昨日 MA20 382.6
      - 線上：https://en.wikipedia.org/wiki/Support_and_resistance 原文 "once the price has breached this level, by an amount exceeding some noise, it is likely to continue falling until meeting another support level."
      - WebSearch 額度已用完（200/200），Investopedia 無法抓取（WebFetch 回報 unable to fetch）
      - 唯讀確認：執行前後 git status 相同，都是 3 個既有修改；find backend/app 與 data 沒有比腳本更新的檔案；numba 快取寫在 ...\work\verify-pbsup\numba_cache

### [P3 → P3] 限股票的策略也把台股 ETF 選進結果（確認成立）

- **主張**：pullback_ma20_bounce、oversold_reversal 的 META 宣告 asset_types=['stock']，但台股 ETF 存在 stock 分區（kline_daily_enriched），run_all 只依 asset_type='stock' 篩選，所以 ETF 也被納入。
- **正確的定義或數值**：asset_types 宣告應被執行（依證券類別過濾），或把 META 改成包含 etf。
- **app 實際行為**：宣告沒有作用。
- **對使用者的影響**：只限股票的策略清單中混入 ETF；影響不大。
- **修法建議**：以 security_master 的證券類別建立 asset_mask，套用 META.asset_types。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/builtin/pullback_ma20_bounce.py:20 'asset_types': ['stock']
  - backend/app/strategy/builtin/oversold_reversal.py:21
  - backend/app/api/screener.py:584
- **線上證據**：
  - （無）
- **本地計算證據**：
  - strategy_cache.json 2026-09-23 的 pullback_ma20_bounce 包含 00985A（35.53 分）、00915（32.72 分）、00982A（26.15 分）
  - kline_daily_enriched/date=2026-09-23 共 1323 檔，含 0050、00878、00885、00893 等 ETF
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：只限股票的策略（META asset_types=['stock']）有 4 個：pullback_ma20_bounce、oversold_reversal、strong_open、high_turnover_surge。screener.py:584 的 asset_types 判斷只決定哪些策略要跑，不過濾標的。台股部署把全部 ETF 都寫進 kline_daily_enriched（9/23 共 1323 檔，其中 236 檔是 ETF），kline_etf_enriched 則是空的；engine.py 的 asset_mask 也只來自 pool，所以這項宣告沒有依證券類別排除 ETF。實證：9/23 的 pullback_ma20_bounce 快取 17 檔中有 3 檔 ETF，分別是 00985A 35.53、00915 32.72、00982A 26.15；strong_open 也選進 00673R。oversold_reversal 在 9/23 沒有結果，但近 60 個交易日的回放中，36 次入選有 17 次是槓桿或反向 ETF（00685L、00673R）。high_turnover_surge 沒選進 ETF，只是因為 ETF 的 turnover_rate 全為 null，碰巧被排除。
    - 理由：我自己寫腳本重現了數據（放在 scratchpad\audit\work\verify-etfmask\），finding 的每個數字都對得上。
(1) kline_daily_enriched/date=2026-09-23 共 1323 檔，finding 同為 1323。依 security_master，其中 etf 236 檔、stock 1087 檔。這 236 檔 ETF 的代號全部出現在官方 TWSE ETF 清單（參考資料 twse_t187ap47_L），自選的 0050、00878、00885、00893 都在內。
(2) strategy_cache.json（as_of 2026-09-23）裡，pullback_ma20_bounce 共 17 列，其中 3 檔 ETF。實際分數與 finding 並列：00985A 35.525（finding 35.53）、00915 32.715（32.72）、00982A 26.146（26.15），四捨五入後完全一致。
(3) 我用分區內存的指標欄、策略預設參數，加上 DEFAULT_BASIC_FILTER（price_min=3、amount_min=2e7）獨立重算 9/23，得到 17 檔，ETF 也正是同樣 3 檔，與快取完全吻合。
程式碼上，screener.py:584 的 asset_types 判斷只決定「哪些策略要跑」，不決定「哪些標的入選」。標的範圍是整個 kline_daily_enriched 分區。台股的 enriched_refresh 把 TaiwanDailyStore 的所有標的（含 ETF）都寫進這個分區，而 kline_etf_enriched、kline_etf_daily、instruments_etf 都是 0 個檔案。engine.py:1188-1195 的 asset_mask 只由 pool 產生，沒有依證券類別過濾。所以 META 的 asset_types=['stock'] 在台股部署下擋不住 ETF，結論成立。
finding 沒有提到、我另外查到的面向：
- oversold_reversal 在 9/23 的快取是 0 列，所以 finding 對它只有結構性論證。我補做近 60 個交易日的回放，套用預設基本過濾後共 36 次入選，其中 17 次是 ETF（槓桿型 00685L 群益臺灣加權正2、反向型 00673R 期元大S&P原油反1），確認它也會選進 ETF。這段回放用的是分區內存的指標欄，屬近似；只有 9/23 的 pullback 對過快取。
- 另外兩個只限股票的策略中，strong_open 在 9/23 也選進 ETF 00673R.TWSE。high_turnover_surge 沒有選進 ETF，但原因只是 ETF 的 turnover_rate 全為 null（0/236），是碰巧被排除，不是 asset_types 發揮了作用。
嚴重度維持 P3：屬於結果清單的品質問題，沒有算錯金額或指標。不過入選的包含槓桿型與反向型 ETF，這類標的的 RSI 超賣與均線回踩訊號意義和個股不同，可讀性影響比 finding 描述的略大。
    - 證據：
      - backend/app/strategy/builtin/pullback_ma20_bounce.py:20 "asset_types": ["stock"],
      - backend/app/strategy/builtin/oversold_reversal.py:21 "asset_types": ["stock"],；另外 strong_open.py、high_turnover_surge.py 也只宣告 ['stock']（用 grep -rln '"asset_types": \["stock"\]' app/strategy/builtin 查到共 4 檔）
      - backend/app/api/screener.py:580-586 asset_types 只用在策略清單推導 all_ids（決定哪些策略要跑），不過濾標的
      - backend/app/strategy/engine.py:1188-1195 asset_mask 只在有 pool 時由 pool 產生，沒有依證券類別過濾
      - backend/app/taiwan/enriched_refresh.py:28,76-108 ENRICHED_DIR_NAME='kline_daily_enriched'，TaiwanDailyStore 的全部標的（含 ETF）都寫進這個分區
      - 本地：data/kline_etf_enriched、kline_etf_daily、instruments_etf 的 parquet 檔數都是 0（verify.py 輸出）
      - 指令：cd backend && uv run python scratchpad/audit/work/verify-etfmask/verify.py → 'enriched 2026-09-23 rows: 1323 unique symbols: 1323'；instrument_type：etf 236／stock 1087；代號出現在官方 TWSE ETF 清單（reference/csv/twse_t187ap47_L_etf_meta.csv）的有 236 檔
      - verify.py：0050、00878、00885、00893 在 9/23 分區裡的 type 都是 etf，而且都在官方 ETF 清單；2454、3034、2379、6196、3545 是 stock
      - verify.py：strategy_cache.json as_of=2026-09-23，pullback_ma20_bounce total=17，其中 ETF 3 檔：00985A.TWSE score=35.525177、00915.TWSE score=32.715099、00982A.TWSE score=26.146084（finding：35.53／32.72／26.15）
      - verify.py：strong_open total=8，含 ETF 00673R.TWSE score=6.693；high_turnover_surge 18 檔全是 stock；oversold_reversal 9/23 total=0
      - reconcile.py：用 9/23 分區內存的欄位重算 pullback 條件，加上 close>=3、amount>=2e7（DEFAULT_BASIC_FILTER，engine.py:41-54）→ 17 檔，ETF=['00915.TWSE','00982A.TWSE','00985A.TWSE']，與快取完全一致
      - oversold_hist.py：近 60 個交易日（2026-07-01..2026-09-23）回放 oversold_reversal，套用預設基本過濾 → 36 次入選，其中 etf 17 次（00685L.TWSE 多日；00673R.TWSE 在 2026-09-17、09-18）
      - turnover_null.py：9/23 分區的 ETF turnover_rate 非 null 的有 0/236，stock 有 1087/1087，所以 high_turnover_surge 沒選到 ETF 是碰巧
      - git status --short 的輸出與開始時的快照相同（只有 daily_update.py、pnpm-lock.yaml、vite.config.d.ts 三個原本就有的改動），我沒有寫入 repo

### [P3 → P3] 非矩陣（polars）評分路徑：缺值候選排在第一位，缺少的評分欄位被靜默剔除（確認成立）

- **主張**：_apply_scoring 遇到缺值時分數為 null，接著 df.sort('score', descending=True) 依 polars 預設（nulls_last=False）把 null 排到最前面；scores 字典又把 null 轉成 0。缺少依賴欄位的評分項會被直接略過，並重新正規化權重，沒有任何提示。
- **正確的定義或數值**：與矩陣路徑一致：缺值候選標記為不可計算並排在最後；缺欄時明確報錯或標註。
- **app 實際行為**：同一份設定在兩個後端的缺值處理方向相反。
- **對使用者的影響**：所有內建策略都走 matrix_native，使用者也沒有自訂或 AI 策略，所以目前不受影響。
- **修法建議**：sort 時加上 nulls_last=True 並回傳不可計算旗標；缺欄時拋出明確錯誤，或在結果中標註缺少的評分欄位。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/engine.py:1495-1531
  - backend/app/strategy/engine.py:963 df.sort('score', descending=order_desc)
  - backend/app/strategy/engine.py:978 float(r.get('score') or 0)
  - backend/app/strategy/engine.py:1504-1508（無法計算的欄位被 filter 掉）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - W\s11_polars_scoring.py：ETF（turnover_rate=None）排序後在第 1 列、score=null；scores 字典為 {'ETF.TWSE': 0.0, 'A.TWSE': 99.99…}
  - 拿掉 turnover_rate 欄後，ETF 變成 77.78 分、排第 2 名（評分組成已改變，但沒有任何提示）
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：非矩陣（polars_expr／python_history_legacy）評分路徑的問題分三點。第一，_apply_scoring 遇到缺值時 score 為 null；engine.py:963 的 sort 沒有設 nulls_last，所以升冪與降冪都把 null 排在最前面；engine.py:978 的 scores 字典再把 null 轉成 0.0。第二，評分欄位缺少依賴時，會在 engine.py:1504-1508 被靜默剔除，剩下的權重重新正規化，沒有錯誤也沒有 log。第三，矩陣路徑的處理方式不同：缺值給 score=0，降冪時排在最後；缺欄則直接拋出 ValueError。

真實資料的放大效果：2026-09-23 共有 236 檔 ETF 的 turnover_rate 為 null，套用預設 basic_filter 後還剩 132 檔，多於預設 limit=100。因此以 turnover_rate 評分的 polars_expr 策略跑全市場時，前 100 名會全是 score=None 的 ETF，沒有任何可評分的候選。

目前使用者只有 21 支 matrix_native 內建策略，沒有自訂、AI 或疊加（composite）策略，所以不受影響。但策略建構 API 與 AI 產生器的預設後端都是 polars_expr，使用者一旦新建這類策略就會觸發。修正時，sort 應加上 nulls_last=True（升冪策略也要處理），缺欄時要明確報錯或在結果中標註，不能只依賴矩陣路徑的「0 分」語意（該語意在升冪排序時同樣會把不可計算的候選排到前面）。
    - 理由：這條 finding 成立，嚴重度維持 P3。我沒有採信原稽核的說法，自己寫了探針策略，餵入本地真實 parquet（kline_daily_enriched date=2026-09-23，共 1323 列），分別經 polars_expr 與 matrix_native 兩條路徑跑 StrategyEngine.run。兩種評分設定都是 turnover_rate 0.5＋change_pct 0.5。重現結果如下：

(1) 缺值時分數為 null，而且排在最前面。自選 9 檔中，4 檔 ETF（0050、00878、00885、00893）的 turnover_rate 是 null，score 也是 None，排在第 1 到 4 名。scores 字典把這 4 檔轉成 0.0，其餘個股依序是 6196=90.63、2379=80.91、2454=37.69、3034=18.45、3545=8.47。

(2) 缺欄時被靜默略過，並重新正規化權重。刪掉 turnover_rate 欄之後，score 與「只用 change_pct 做 min-max」的結果逐列完全相同，00893 以 64.39 分排第 2。程式沒有 raise，除了 DEBUG 的載入訊息之外，也沒有任何 WARNING log。

(3) 矩陣路徑的行為相反。同一份資料與設定下，可評分的 5 檔分數與 polars 路徑一致（例如 90.6315 對 90.6315），4 檔 ETF 的 score=0.0，排在第 6 到 9 名。缺欄時則直接拋出 ValueError: unsupported matrix feature: turnover_rate。

(4) 原 finding 的合成資料數字我也重跑了，完全一致：ETF 為 null、排第 1，scores 字典給 0.0；A=99.99999999999997；拿掉欄位後 ETF=77.78、排第 2。手算 (0.08−0.01)/(0.10−0.01)×100=77.78，也吻合。

原記錄沒提到的新面向是實際資料上的放大效果。當日 236 列 turnover_rate 為 null，全部是 00 開頭的 ETF；經過預設 basic_filter（price_min=3、amount_min=2e7）後仍剩 132 列。這個數字大於預設 limit=100，因此全市場執行時前 100 名全是 score=None 的 ETF，真正可評分的候選一檔都沒有。

影響評估：21 支內建策略全部宣告 EXECUTION_BACKEND = "matrix_native"。data\strategies\{custom,ai,composite} 目錄都不存在，所以使用者目前不受影響，finding 的 user_impact 正確。不過有兩個潛在觸發點：策略建構 API 的預設後端是 polars_expr（app/api/strategy.py:550），AI 產生器的 fallback 也是 polars_expr（app/strategy/ai_generator.py:118）。使用者一旦用預設設定建立以 turnover_rate 評分的自訂策略，就會立即碰到前述的全數擠出效果。

小修正：finding 的 expected 寫「與矩陣路徑一致：缺值候選排在最後」，但矩陣路徑其實只是把分數設成 0（matrix.py:3741），並沒有「不可計算」旗標。排在最後只在降冪（descending）時成立；若策略用升冪，分數 0 的候選在矩陣路徑同樣會排在前面。另外，polars 的預設 nulls_last=False 在升冪與降冪時都會把 null 放在最前面（實測 [None, 1.0, 3.0] 與 [None, 3.0, 1.0]）。整個重現過程工作樹沒有變動（git status --porcelain 前後比對相同）。
    - 證據：
      - backend/app/strategy/engine.py:1504-1508 executable 以 scoring_value_expr(...) is not None 過濾，無法計算的欄位被剔除；:1509 total_weight 只加總剩下的權重；:1515 w = weight / total_weight 重新正規化；:1531 產生 score 欄
      - backend/app/strategy/engine.py:963 df.sort("score", descending=order_desc)，沒有 nulls_last；polars 1.40.1 簽名為 nulls_last=False；實測降冪 [None, 3.0, 1.0]、升冪 [None, 1.0, 3.0]
      - backend/app/strategy/engine.py:978 float(r.get("score") or 0) 把 None 轉成 0.0
      - backend/app/backtest/matrix.py:3741 score[~universe | ~all_finite] = 0.0；matrix.py:3795 缺欄時 raise ValueError("unsupported matrix feature")
      - 指令：cd backend && PYTHONDONTWRITEBYTECODE=1 uv run python AUDIT_SCRATCH/work/verify-polarsnull/p1_repro.py；輸出存在同目錄的 p1_out.txt
      - A（polars，pool=自選 9 檔）：第 1-4 名為 0050、00878、00885、00893，score=None；scores={0050:0.0, 00878:0.0, 00885:0.0, 00893:0.0, 6196:90.6315, 2379:80.9051, 2454:37.6852, 3034:18.4517, 3545:8.4700}
      - B（polars，全市場，limit=100）：回傳 100 列，其中 100 列 score=None（00400A.TWSE 起），非 null 分數的候選 0 列
      - C（polars，刪除 turnover_rate 欄）：score 與只用 change_pct 的 min-max 逐列相同（6196=100、00893=64.387、2379=61.810 … 3034=0），00893 排第 2；沒有任何 WARNING log
      - D（matrix_native，同一資料與設定）：6196=90.6315、2379=80.9051、2454=37.6852、3034=18.4517、3545=8.4700 排第 1-5；4 檔 ETF score=0.0 排第 6-9
      - E（matrix_native，刪除 turnover_rate 欄）：E raised: ValueError unsupported matrix feature: turnover_rate
      - p2_count.py：2026-09-23 分區中 turnover_rate 為 null 的 236 列全部是 00 開頭的 ETF；套用預設 basic_filter（close>=3、amount>=2e7）後共 635 列，其中 132 列為 null，多於預設 limit=100
      - p3_synthetic.py 用原 finding 的合成資料重算：[('ETF.TWSE', None), ('A.TWSE', 99.99999999999997), ('B.TWSE', 37.78), ('C.TWSE', 0.0)]；scores={'ETF.TWSE': 0.0, 'A.TWSE': 99.99999999999997, …}；拿掉欄位後 ETF=77.7778 排第 2；手算 (0.08-0.01)/(0.10-0.01)*100=77.78；原 finding 的數字全部一致
      - 影響範圍：backend\app\strategy\builtin\*.py 的 21 支策略全部是 EXECUTION_BACKEND = "matrix_native"；data 下沒有 strategies 目錄（.env DATA_DIR=./data）
      - 潛在觸發點：backend/app/api/strategy.py:550 BuildRequest.execution_backend 預設為 "polars_expr"；backend/app/strategy/ai_generator.py:118 fallback 回傳 "polars_expr"

### [P3 → P3] composite 的 union 模式：只被一支子策略命中的標的不受權重影響，也不獎勵多重確認（確認成立）

- **主張**：union 模式只在「有命中的子策略」之間加權。只被低權重子策略命中且排第 1 的標的，也會得到 100 分；被兩支子策略都命中但名次較低的標的反而分數較低，子策略權重幾乎不起作用。
- **正確的定義或數值**：權重應反映子策略的重要性，例如以全部子策略的權重總和為分母，未命中計 0；或加上命中數加成。
- **app 實際行為**：權重只影響多重命中的標的。
- **對使用者的影響**：目前沒有 composite 策略，暫不影響。
- **修法建議**：改以所有子策略的權重總和為分母，或提供『確認數加成』選項，並在 UI 說明合成規則。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/composite.py:105-119（分母只加總有命中的子策略權重）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - W\s10_composite.py CASE3（權重 0.9 與 0.1）：S3 只被權重 0.1 的子策略命中得 100 分；S1 在權重 0.9 的子策略排第 1 也是 100 分；S2 兩支都命中但都排最後，得 0 分
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：composite 合併時，分數的分母只加總這個標的有命中的子策略權重（選股在 composite.py:105-119，回測在 232-237 行）。因此只被一支子策略命中的標的，分數就等於它在該子策略內的排名歸一值乘以 100，完全不受權重影響。權重 0.9/0.1 時，只被 0.1 子策略排第 1 的 S3 與只被 0.9 子策略排第 1 的 S1 都是 100 分；改用全權重分母應為 90 對 10。多重確認也沒有加分，甚至可能吃虧：D 在 0.9 子策略排第 1、在 0.1 子策略排中段，只得 95 分，低於只被 0.1 子策略排第 1 的 G（100 分）。finding 原例的 S2（兩支都排最後、得 0 分）是排名造成的，改用全權重分母也一樣是 0，不能用來證明權重失效。這個問題同樣存在於 intersect 模式 min_confirm 小於子策略數的情況，也存在於回測合併。子策略交集很小時，最終排名由各子策略的候選數決定，而不是權重：本地代理實驗中翻轉權重，324 檔裡 0 檔分數改變。另外有兩個原記錄沒提到的選股與回測口徑不一致：(1) 權重 0 的子策略獨占命中的標的，選股因 114-117 行退化為均等權重而得 100 分，回測得 0 分；(2) 只有單一候選的子策略，選股得 100（83 行，與 32、72 行註解說的「中性分」不符），回測得 50（222-224 行）。目前沒有任何 composite 策略，前端也沒有建立入口，所以不影響使用者。
    - 理由：我用自己的腳本直接呼叫 repo 裡的純函式 merge_results（選股）與 merge_signal_matrices（回測），finding 的 CASE3 數值完全重現。權重 0.9/0.1，子策略 A 為 {S1:10, S2:1}，子策略 B 為 {S3:10, S2:1}。選股結果 S1=100、S3=100、S2=0，回測合併結果也一樣，都與 finding 相同。若改用「全部子策略權重總和當分母、未命中計 0」的算法，則是 S1=90、S3=10、S2=0。

原因在 composite.py:105-119：分母 total_w 只加總這個標的有命中的子策略權重（_effective_weights，36-43 行）。回測端 232-237 行的 weight_sum 同樣只加總命中子策略的權重。所以只被一支子策略命中的標的，分數就等於它在該子策略內的排名歸一值乘以 100，權重完全抵銷。

權重掃描的結果：權重設為 (0.9,0.1)、(0.5,0.5)、(0.1,0.9)，選股與回測的 S1、S3 都是 100。選股端連 (1,0)、(0,1) 也一樣，因為 114-117 行在命中權重總和 ≤0 時會退化成均等權重，權重 0 的子策略獨占命中仍然拿 100 分。所以對單一命中的標的，權重不是「幾乎不起作用」，而是完全不起作用。

finding 的敘述有一處要修正：S2 得 0 分是因為它在兩支子策略裡都排最後，改用全權重分母也一樣是 0，不能用來證明權重失效。能證明「多重確認沒有加分，甚至吃虧」的例子是 CASE-B：權重 0.9/0.1，D 在 0.9 子策略排第 1、在 0.1 子策略排 3 名中第 2，得 95 分；G 只在 0.1 子策略排第 1，卻得 100 分。CASE-C 用三支子策略、權重都是 1：X 被兩支都排第 1 是 100 分，Q 只被一支排第 1 也是 100 分，沒有確認數加成。intersect 模式在 min_confirm=1 時結果完全一樣（S1=100、S3=100），所以問題不限於 union 模式。

本地 parquet 代理實驗（kline_daily_enriched 2026-09-23，1323 檔）。這兩支是我自己組的代理子策略，不是 builtin 策略：子策略 A 是 rsi_14<35，命中 80 檔；子策略 B 是收盤價在 ma20±2% 且 ma20>ma60，命中 244 檔；兩者交集 0 檔。把權重從 0.9/0.1 翻成 0.1/0.9，324 檔裡分數改變的是 0 檔。權重 0.9/0.1 時前 20 名有 15 檔只被權重 0.1 的子策略命中，原因是候選數多的子策略排名歸一的間距比較密（推測），也就是說排名實際上由各子策略的候選數決定，而不是權重。

額外發現原記錄沒提到的兩個面向，都屬於選股與回測口徑不一致：
(1) 權重為 0 的子策略獨占命中的標的：選股得 100（114-117 行的退化分支），回測得 0（233-237 行 hit_weights=0 且 safe_sum=1）。
(2) 只有單一候選的子策略：選股得 100，因為 83 行在 count=1 時 norm=1.0，與 32、72 行註解說的「单候选用中性分」不符；回測得 50（222-224 行 n<=1 用 0.5）。

影響：data/ 下沒有 strategies 目錄，也沒有任何 composite 策略檔。前端的 strategySaveComposite（frontend/src/lib/api.ts:3085）在 src 裡只有定義、沒有呼叫者，所以目前不影響使用者。模組 docstring 第 13 行明文寫了「命中子策略间按权重加权」，屬於有記載的設計選擇。維持 P3。WebSearch 額度已用盡（200/200），這一條沒有線上來源；它是公式設計問題，不涉及外部數據，證據都來自本地重算。
    - 證據：
      - backend/app/strategy/composite.py:105-119：分母 total_w 只加總命中子策略的權重（_effective_weights 在 36-43 行），單一命中時分數 = norm × 100，與權重無關
      - backend/app/strategy/composite.py:114-117：命中權重總和 ≤0 時退化為均等權重，權重 0 的子策略獨占命中仍得 100 分（選股端）
      - backend/app/strategy/composite.py:232-237：回測端 weight_sum 也只加總命中權重，行為相同；但權重 0 時 blended=0、safe_sum=1，所以得 0 分，與選股端不一致
      - backend/app/strategy/composite.py:83 與 222-224：單一候選時選股 norm=1.0、回測 norm=0.5；32、72 行註解寫單候選用中性分
      - 指令：cd backend && PYTHONIOENCODING=utf-8 uv run python AUDIT_SCRATCH/work/verify-comp-unionw/repro.py（輸出在同目錄的 out.txt，exit=0）
      - CASE3 並列比較。finding：S1=100、S3=100、S2=0；我的選股結果：{'S1': 100.0, 'S2': 0.0, 'S3': 100.0}；回測結果：{'S1': 100.0, 'S2': 0.0, 'S3': 100.0}；全權重分母算法：{'S1': 90.0, 'S2': 0.0, 'S3': 10.0}
      - 權重掃描：(0.9,0.1)、(0.5,0.5)、(0.1,0.9) 的選股與回測都是 S1=100、S3=100；(1.0,0.0) 選股 S3=100 但回測 S3=0；(0.0,1.0) 選股 S1=100 但回測 S1=0
      - CASE-B（權重 0.9/0.1）：選股 {'D': 95.0, 'E': 0.0, 'F': 0.0, 'G': 100.0}。雙重確認且在 0.9 子策略排第 1 的 D 低於只被 0.1 子策略命中的 G；全權重分母算法下 D=95、G=10
      - CASE-C（三支子策略，權重都是 1）：X 被兩支都排第 1 得 100，Q 只被一支排第 1 也得 100，沒有確認數加成
      - intersect 且 min_confirm=1：{'S1': 100.0, 'S2': 0.0, 'S3': 100.0}，與 union 相同
      - 單一候選子策略（A={S1,S2}、B={S3}，權重 0.9/0.1）：選股 S3=100.0，回測 S3=50.0
      - 本地 parquet 代理（data/kline_daily_enriched/date=2026-09-23\part.parquet，1323 列）：A（rsi_14<35）命中 80、B（收盤價在 ma20±2% 且 ma20>ma60）命中 244、交集 0；權重從 0.9/0.1 翻成 0.1/0.9，324 檔中分數改變 0 檔；前 20 名有 15 檔只被權重 0.1 的子策略命中
      - 影響面：data\ 下沒有 strategies 目錄（find 查無 composite_*）；frontend/src/lib/api.ts:3085 的 strategySaveComposite 在 src 內沒有呼叫者；後端入口在 backend/app/api/strategy.py:1060
      - docs\code-review-2026-09-22.md 沒有任何 composite 或疊加相關條目，不是重複回報
      - 執行前後 git status 相同（3 個既有修改），HEAD 為 ced12a5，沒有 stash；composite.py 沒有未提交修改

### [P2 → P2] bottom_reversal 的出場條件「收盤 < MA20」在進場當根就成立，實際上只持有一天（部分成立）

- **主張**：策略會在價格仍低於下彎均線時進場（docstring 自己也這樣寫），但出場用的是水位條件 close<MA20，不是穿越，因此進場當根與隔天幾乎必定觸發出場訊號，MAX_HOLD_DAYS=12 形同虛設。
- **正確的定義或數值**：逆勢反彈策略的出場應該是停損（跌破止跌 K 低點）、回到均線後再跌破（穿越型），或達到目標報酬，而不是一個進場時就已經成立的條件。
- **app 實際行為**：回測中 96% 的交易在進場隔天就因 signal 出場，策略績效只反映一天的報酬。
- **對使用者的影響**：使用者用「深跌止穩反彈」做回測或參考賣出訊號時，會得到與策略描述（最長持有 12 天）不符的結果；9/23 沒有命中，所以選股畫面暫時看不出來。
- **修法建議**：把出場改成穿越型（上一根 ≥MA20 且本根 <MA20），或改為「收盤跌破止跌 K 低點」，並補上測試斷言進場當根的出場訊號為 False。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/builtin/bottom_reversal.py:4-6 'entries fire while price is still below the falling averages'
  - backend/app/strategy/builtin/bottom_reversal.py:151 exit_ = market.close < matrix_feature(market, 'ma20')
  - backend/app/strategy/builtin/bottom_reversal.py:88 MAX_HOLD_DAYS = 12
  - backend/app/backtest/engine.py:1123（進場後第一根出現 matrix.exit 就以 signal 出場）
- **線上證據**：
  - https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/rsi-2：「exiting long positions on a move above the 5-day SMA」（均值回歸策略是在價格回到均線上方時出場，不是在仍低於均線時）
- **本地計算證據**：
  - W\s9_experiments.py E1：歷史進場 207 筆，進場當根出場訊號成立 207 筆（100%），隔天成立 96.1%；距離第一個出場訊號的根數中位數為 1
  - 進場時 close/MA20−1 的中位數為 −18.0%，最大值為 −2.5%（沒有任何一筆在 MA20 之上）
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：bottom_reversal 的出場條件是水位型「收盤 < MA20」（bottom_reversal.py:151），不是專案正本 signal_ma20_breakdown（pipeline.py:640-642）與同類逆勢策略使用的穿越型。策略一律在 MA20 之下進場（close/MA20−1 最大值為 −2.47%），所以出場條件在訊號日就已成立（207/207）。引擎會略過與進場同根的出場，但只要下一根收盤仍低於 MA20，就以 signal 出場（下一根成立 96.6%）。結果回測有 95～97% 的交易只持有一天，MAX_HOLD_DAYS=12 從未觸發。另外，docs/radar-strategies-validation.md 把它列為唯一通過驗證的策略，那組數字實際上是隔日沖版本的績效。這是明確的功能錯誤，嚴重度 P2。
    - 理由：這條 finding 成立，嚴重度維持 P2。

一、定義與程式碼。bottom_reversal.py:151 的出場條件寫成水位條件 `exit_ = market.close < matrix_feature(market, "ma20")`，不是穿越。專案自己的正本不是這樣定義的：`signal_ma20_breakdown`（pipeline.py:640-642、1657）要求本根 close<MA20，且上一根 close>=MA20。同類的逆勢策略 oversold_bounce.py:77、n_day_low_reversal.py:68、oversold_reversal.py:82 也都用穿越型。只有這支逆勢策略用水位型，與它同一個 commit（d839721）加入的趨勢策略 consolidation_breakout 與 pullback_resume 寫法相同。趨勢策略在 MA20 之上進場，水位型等同「第一次跌破」，沒有問題。這支策略卻在 MA20 之下進場（docstring 第 4-6 行自己寫明），所以進場時出場條件就已經成立。

外部來源也把「跌破均線」定義為「由上往下」的事件：
- StockCharts：「A bearish signal is generated when prices move below the moving average.」
- backtrader CrossDown：`last_non_zero_diff > 0 and data0(0) < data1(0)`。

二、引擎行為（engine.py）。獨立撮合從進場後下一根開始檢查（future 取自 range(time_id + 1, ...)），第 1123 行只要 matrix.exit 為真，就以 signal 出場。組合撮合每天先處理出場、後處理進場，效果相同。預設是 open_t+1：出場訊號延遲一根，所以訊號日 T 的出場訊號落在進場根 T+1 上，會被略過。真正觸發的是 T+1 收盤仍低於 MA20，於是在 T+2 開盤出場，duration=1。finding 說「進場當根也成立」是事實，但當根的訊號在引擎裡不會生效；實際效果（只持有一天）與 finding 一致。

三、本地重算（資料為 kline_daily_enriched 2024-01-02～2026-09-23；只呼叫純計算函式，外加 minute_fill 關閉時不讀外部資料的撮合）：
- 207 筆進場（已套用 DEFAULT_BASIC_FILTER）。訊號日出場條件成立 207/207（100%），下一根成立 200/207（96.6%；finding 寫 96.1%，差異可能來自多了一天資料）。
- 距離第一個出場訊號的根數：1 根 200 筆、2 根 5 筆、3 根 1 筆、11 根 1 筆，中位數 1。
- 進場時 close/MA20−1：中位數 −18.05%，最大 −2.47%，沒有任何一筆在 MA20 之上。
- 用策略自帶的 STOP_LOSS=-0.06、MAX_HOLD_DAYS=12 回測：
  - 獨立撮合：207 筆，201 筆（97.1%）duration=1；出場原因 signal 189、stop_loss 18、max_hold 0。
  - 組合撮合（最多 20 檔）：125 筆，119 筆（95.2%）duration=1。
  - MAX_HOLD_DAYS 確實形同虛設。
- 把出場換成穿越型（只換出場，進場不變）：獨立撮合 duration 中位數變 12，max_hold 出場 125 筆。可見出場規則的寫法決定了持有期。

四、finding 沒提到的新面向：
- docs/radar-strategies-validation.md:77、84 把 bottom_reversal 列為六支中唯一通過驗證者（123 筆，平均 +2.41%，勝率 66.7%）。我用同口徑重算組合撮合，得到 125 筆、平均 +2.30%、勝率 64.8%，其中 95% 只持有一天。所以那份驗證結論衡量的其實是隔日沖版本，不是 META 與 MAX_HOLD_DAYS=12 描述的策略。
- 同一個出場矩陣也會進入 strategy/engine.py:1219-1233 的 exit_signal_hits，再由 monitor.py:1158 當成 sell_signal。若有監控規則套用這支策略，每天都會把所有低於 MA20 的股票報成賣出訊號。使用者目前監控規則 0 條，暫無影響。

五、嚴重度與使用者情境。這是策略規則寫錯（明確的功能錯誤），不是資料破壞，也不是撮合計算錯誤：引擎對既有規則的計算本身正確。P2 恰當。使用者只要對這支策略跑回測、參數優化或參考上述驗證文件，就一定會遇到；選股的進場清單不受影響。

小修正：
- finding 說「隔天成立 96.1%」，本次重算為 96.6%。
- 「進場當根的出場訊號」在 open_t+1 下會被引擎略過，真正觸發的是下一根。這兩點都不影響結論。
- 修法建議可行：改成穿越型，或改為跌破止跌 K 低點。另外建議補測試，並重跑 radar-strategies-validation 的數字。
    - 證據：
      - backend/app/strategy/builtin/bottom_reversal.py:4-6 docstring「entries fire while price is still below the falling averages」
      - backend/app/strategy/builtin/bottom_reversal.py:151 `exit_ = market.close < matrix_feature(market, "ma20")`（水位型，不是穿越型）；:87-88 STOP_LOSS=-0.06、MAX_HOLD_DAYS=12；:86 EXIT_SIGNALS=["signal_ma20_break"]
      - backend/app/indicators/pipeline.py:640-642 專案正本 signal_ma20_breakdown =（close<ma20）且（上一根 close>=上一根 ma20），是穿越型
      - backend/app/strategy/builtin/oversold_bounce.py:77、n_day_low_reversal.py:68、oversold_reversal.py:82 同類逆勢策略的出場都是 `(market.close < ma20) & (shift(market.close, 1) >= shift(ma20, 1))`
      - backend/app/backtest/engine.py:1080-1083 持倉檢查從 time_id+1 開始；:1123-1125 `elif matrix.exit[future, asset_id]: reason = "signal"`；:2033-2047 組合撮合同樣先出場後進場；matrix.py:2853-2877 open_t+1 時出場訊號延遲一根
      - git log -L151,151 顯示第 151 行自 d839721（新增六支雷達策略）起就是水位型，之後沒有改過
      - 本地指令：cd backend && uv run python scratchpad\audit\work\verify_br_exit_def\check.py → 輸出：filtered entries 207；entry bar exit true 207/207=1.000；next bar exit true 200/207=0.966；first exit gap [(1,200),(2,5),(3,1),(11,1)]，中位數 1；close/ma20-1 中位數 -0.1805、最大 -0.0247
      - 同一腳本的回測輸出：[水位型/獨立撮合] trades=207，dur=1 為 201 筆（0.971），出場原因 {signal:189, stop_loss:18}；[水位型/組合撮合] trades=125，dur=1 為 119 筆（0.952），平均損益 0.0230，勝率 0.648；[穿越型/獨立撮合] duration 中位數 12，出場原因 {max_hold:125, stop_loss:61, signal:20, end:1}
      - docs/radar-strategies-validation.md:77、84 把 bottom_reversal（123 筆，+2.41%，勝率 66.7%）列為唯一通過驗證者；重算顯示該結果約 95% 是只持有一天的交易（新面向）
      - backend/app/strategy/engine.py:1219-1233 exit 矩陣會成為 exit_signal_hits；monitor.py:1158 當成 sell_signal 使用（使用者監控規則 0 條，目前無影響）
      - 線上來源 https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/moving-averages-simple-and-exponential：「A bearish signal is generated when prices move below the moving average.」
      - 線上來源 https://www.backtrader.com/docu/indautoref/ CrossDown：「diff = data - data1; downcross = last_non_zero_diff > 0 and data0(0) < data1(0)」（跌破的定義是上一狀態在上方、本根在下方）
      - 限制：TradingView Pine 參考頁為動態產生，抓不到內容；Investopedia 無法連線；quantifiedstrategies 被機器人驗證擋下；WebSearch 額度已用完
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：bottom_reversal 的出場條件是水位型的 close<MA20（bottom_reversal.py:151），但進場時收盤全部低於 MA20：在全市場 2024-01～2026-09 的資料上，app 產生 287 筆進場，close/MA20−1 中位數約 −16.6%，最大值 −0.08%。因此訊號日出場條件 100% 成立，隔一根仍成立的比例約 96.5%。撮合器不看進場當根的出場訊號（engine.py:1083；open_t+1 會把訊號日的 exit 平移到成交日後略過），所以實際效果是約 93%～97% 的交易只持有 1 個交易日：預設 position 模式 126/135，full 模式 278/287。只有 1 筆撐到 MAX_HOLD_DAYS=12，這個設定形同虛設。套用策略自帶的 −6% 停損後，這些一日交易的出場原因多數是 signal（open_t+1 full 模式 88%，position 模式 77%），其餘是在同一根被停損攔下；「96% 因 signal 出場」只在不開風控時成立。回測績效因此只反映約一天的報酬，與「深跌止穩反彈、最長持有 12 天」的設計不符。修正方向照原建議：改成穿越型出場，或改用停損／目標報酬出場。
    - 理由：重現了核心機制與後果，但 finding 的幾個數字和「因 signal 出場」的比例跟我算出來的不一樣。

機制方面：bottom_reversal.py:151 的出場條件是水位型的 close<MA20。我自己獨立實作了一份，也用 app 的 compute_signals 跑了一次，兩邊都顯示進場訊號當根的出場條件 100% 成立。隔一根仍成立的比例是 96.5%～96.8%，從訊號根到第一個出場訊號的根數中位數是 1。

撮合方面：engine.py:1083 從進場後的下一根開始檢查。在 open_t+1 模式下，matrix.py:2853-2877 會把訊號日的 exit 平移到成交日，所以訊號日當根的 exit 一律被略過。實際觸發出場的是成交日（t+1）的 exit，出場成交在 t+2 開盤，因此實際持有一天，不是零天。

把策略自己的 STOP_LOSS=6%、MAX_HOLD_DAYS=12 餵進 app 的撮合器：
- full 模式、open_t+1：287 筆中 278 筆（96.9%）只持有 1 天，只有 1 筆撐到 max_hold。
- API 預設的 position 模式：126/135 筆（93.3%）只持有 1 天。
結論是 MAX_HOLD_DAYS 確實形同虛設。

和 finding 不符的地方：
1. 樣本數：finding 說 207 筆，我在 2024-01-02～2026-09-23、2346 檔的全市場 enriched 資料上，app 跑出 287 筆，獨立實作跑出 314 筆。差異推測來自樣本範圍不同。
2. 進場時 close/MA20−1：finding 說中位數 −18.0%、最大 −2.5%；我算出中位數 −16.6%、最大 −0.08%。「全部低於 MA20」這點一致。
3. 「96% 的交易在隔天因 signal 出場」只在不開風控時成立（open_t+1 full 無風控 277/287=96.5%）。套用策略自帶的 −6% 停損後，隔天出場的交易裡有一部分改記成 stop_loss，只算「持有 1 天且原因為 signal」：open_t+1 full 為 253/287=88.2%，close_t full 為 172/287=59.9%，position 模式為 104/135=77%。
所以正確的說法是「約 93%～97% 的交易只持有一天，大多數由 signal 觸發」。

線上證據：StockCharts 原文「Connors advocates exiting long positions on a move above the 5-day SMA」確認存在。

嚴重度維持 P2：這是策略邏輯錯誤，讓回測結果與策略描述不符，但沒有破壞資料。
    - 證據：
      - backend/app/strategy/builtin/bottom_reversal.py:151 exit_ = market.close < matrix_feature(market, "ma20")（水位條件，不是穿越）；:88 MAX_HOLD_DAYS = 12；:87 STOP_LOSS = -0.06；:4-5 docstring："entries fire while price is still below the falling averages"
      - backend/app/backtest/engine.py:1083 future_times 從 time_id + 1 開始（不檢查進場當根）；:1114 先 _risk_exit；:1123 接著看 matrix.exit[future]；:1126 最後才輪到 max_hold
      - backend/app/backtest/matrix.py:2853-2877 _delay_signal_matrix 在 open_t+1 時把 raw exit[t] 平移到 t+1（成交日，會被略過），raw exit[t+1] 則在 t+2 開盤觸發出場
      - backend/app/api/backtest.py:331 matching 預設 open_t+1；:342 mode 預設 position（投組撮合 simulate_market_matrix，見 app\backtest\strategy.py:1519-1535）
      - 指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-brexit/repro.py（輸出在同目錄 out.txt）。資料為 kline_daily_enriched 共 661 個交易日（2024-01-02～2026-09-23）、2346 檔、1,423,597 列
      - [A] 獨立實作（逐檔自己的列）：進場 314 筆；進場當根 close<MA20 共 314/314（100%）；隔一根 304/314（96.8%）；到第一個出場訊號的根數中位數 1，分布 {1:304, 2:5, 3:2, 5:1, 11:1, 17:1}；close/MA20−1 中位數 −16.34%、最大 −0.08%
      - [B] app compute_signals：進場 287 筆（全部落在獨立實作的 314 筆之內）；進場當根出場訊號 287/287；隔一根 276/286（96.5%）
      - [C] app 撮合器 full 模式、open_t+1、STOP_LOSS 6%、MAX_HOLD 12：287 筆交易，出場原因 signal 261／stop_loss 25／max_hold 1；持有 1 天 278/287（96.9%）；持有 1 天且因 signal 253/287（88.2%）。close_t：signal 181／stop_loss 105／max_hold 1；持有 1 天 277/287，其中因 signal 172（59.9%）。不開風控：持有 1 天且因 signal 277/287（96.5%）
      - repro2.py（輸出 out2.txt），預設 position 模式：open_t+1 共 135 筆，持有 1 天 126 筆（93.3%），其中 signal 104、stop_loss 22、max_hold 1；close_t 共 134 筆，持有 1 天 123 筆（91.8%）。app 進場時 close/MA20−1 中位數 −16.61%、最大 −0.08%、p90 −6.64%
      - 與 finding 的數字對照：進場筆數 207 對 287（app）/314（獨立）；進場當根 100% 對 100%；隔天 96.1% 對 96.5%～96.8%；根數中位數 1 對 1；close/MA20−1 中位數 −18.0% 對 −16.6%；最大值 −2.5% 對 −0.08%；「96% 因 signal 隔天出場」對 88.2%（full、open_t+1、含停損）/77%（position 預設）/96.5%（不開風控）
      - 線上：https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/rsi-2 原文："Connors advocates exiting long positions on a move above the 5-day SMA."
      - git status 與開始時相同（只有原本就在的 3 個修改檔），repo 沒有被寫入

### [P3 → P2] DEFAULT_BASIC_FILTER 的 exclude_new_days=30 從未實作（部分成立）

- **主張**：引擎預設的基本過濾宣告要排除上市未滿 30 日的新股，但整個後端沒有任何程式讀取這個鍵。
- **正確的定義或數值**：實作新股排除，或刪除這個預設鍵，以免讓人誤以為有效。
- **app 實際行為**：沒有作用；只需要 20 根暖機的條件（例如 bottom_reversal 的 20 日高低點）可能對上市 20 到 30 日的新股觸發。
- **對使用者的影響**：低；多數策略需要 60 根暖機，自然排除了新股。
- **修法建議**：在 _build_basic_filter_mask_uncached 依有效 bar 數或上市日實作，或刪除該鍵。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/engine.py:52 'exclude_new_days': 30
  - grep -rn exclude_new_days backend/app 只有這 1 處；frontend 與 docs 沒有任何結果
- **線上證據**：
  - （無）
- **本地計算證據**：
  - grep 的輸出只有 ./strategy/engine.py:52
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：DEFAULT_BASIC_FILTER 的 exclude_new_days=30（engine.py:52）確實從未實作。polars 與矩陣兩套基本過濾都不讀這個鍵，把它設成 0、30、10000 或拿掉，輸出完全相同。但 AI 策略生成指南（prompts/strategy-guide-compact.md:36，由 ai_generator.py:20 載入；strategy-guide.md:34）把它寫成「引擎統一處理」的「排除上市N天內新股」，所以 grep 並不是只有 1 處。另外，策略宣告的 required_warmup_bars 只決定歷史載入長度，不會逐檔排除新股，「60 根暖機自然排除新股」的說法不成立。以本地資料（191 檔新上市股票）重跑內建策略並套用預設過濾：有 9 個策略在新股前 30 根 bar 內產生 256 筆進場訊號；其中 strong_open、macd_golden、oversold_reversal、oversold_bounce 最早在第 2 到第 7 根就觸發。最近一例是 oversold_reversal 在 2026-09-17 選中上市第 14 天的 3718.TPEX。bottom_reversal 理論上最早可在第 21 根觸發，但實際資料中前 30 根內 0 筆。修正方式：在兩套基本過濾中依 security_master.listing_date（需處理三種日期格式）或有效 bar 數實作，並同步 AI 指南；或刪除這個鍵並修正指南。
    - 理由：核心主張成立：exclude_new_days 沒有任何程式讀取。兩套基本過濾實作都沒有這個鍵：polars 路徑（backend/app/strategy/engine.py:1416-1480）和矩陣路徑（backend/app/backtest/matrix.py:3641-3673）。依賴解析 backend/app/backtest/strategy.py:459-477 也沒有。我把這個鍵分別設成「不存在、0、30、10000」重跑兩套實作，輸出完全相同。

finding 有三處錯誤或低估：
(1) 「grep 只有 1 處，docs 沒有結果」不正確。backend/app/strategy/prompts/ 底下還有 4 處。其中 strategy-guide-compact.md:36 由 backend/app/strategy/ai_generator.py:20 當作 LLM 指南使用；strategy-guide.md:28-34 在「基础过滤参数 (Stage 1, 引擎统一处理)」底下把它寫成「排除上市N天内新股」。所以 AI 產生的策略會被告知這個鍵有效，這是原 finding 沒提到的面向。
(2) 「多數策略需要 60 根暖機，自然排除了新股」這個前提錯誤。required_warmup_bars 只決定要載入多少歷史（engine.py:660-710 required_history_bars、services/screener.py:397-404），不會逐檔擋掉 bar 數不足的標的。_run_matrix_strategy（engine.py:1184-1230）與 pipeline（matrix.py:3523-3582）都沒有逐檔的 bar 數門檻。
(3) 實際影響不只「20 到 30 日」。我用本地 enriched parquet（2024-01-02～2026-09-23，其中新上市股票 191 檔）跑全部內建策略，並套用 DEFAULT_BASIC_FILTER。可執行的 20 個股票策略裡，有 9 個在新股上市後前 30 根 bar 內產生進場訊號，合計 256 筆；其中 4 個策略在第 20 根 bar 之前就觸發，最早第 2 根。finding 舉的 bottom_reversal 在理論上正確：合成資料證實它最早可在第 21 根觸發，儘管宣告的暖機是 60 根。但在實際資料裡，它對新股最早只在第 93 根觸發，前 30 根內是 0 筆，所以不是主要案例。

最近的實例：oversold_reversal 在 2026-09-17 對 3718.TPEX 中光電投控觸發。security_master 記錄的 listing_date 是 2026-09-03，當天是它的第 10 根 bar、上市第 14 天。官方參考檔 tpex_daily_quotes_20260923.csv 確認 3718 在櫃買交易（9/23 收盤 64.7）；上市日無法上網核實，因為 WebSearch 配額已用完。

修正可行性：security_master 的 listing_date 共 2377 筆，全部可解析，有 YYYYMMDD、民國 yyyMMdd、YYYY/MM/DD 三種格式。視窗內上市的 274 檔，首根 bar 與上市日最多只差 2 天。

嚴重度：新股在上市頭幾天（台股新股前 5 日無漲跌幅限制）就會進入 strong_open、macd_golden 等策略的候選清單。加上 AI 指南把這個鍵當成引擎功能，屬於「設定了卻靜默無效」的明確功能錯誤，因此調整為 P2。以這位使用者的情境看（自選 9 檔都是舊股、監控規則 0 條），實際影響只在選股與回測的候選清單。

唯讀確認：git status 與開始時相同。numba 快取改寫到 scratchpad，並關閉 pyc 寫入。
    - 證據：
      - backend/app/strategy/engine.py:52 "exclude_new_days": 30 位於 DEFAULT_BASIC_FILTER；engine.py:39-40 註解寫明 exclude_st 是刻意停用，「rather than left as a no-op default」，但 exclude_new_days 仍以無作用的預設值留著
      - backend/app/strategy/engine.py:1416-1480 _basic_filter_expr 只讀 price/market_cap/float_cap/amount/turnover/exclude_st/boards；backend/app/backtest/matrix.py:3641-3673 _build_basic_filter_mask_uncached 同樣沒有 exclude_new_days
      - grep -rn exclude_new_days（排除 node_modules、.git、.venv）：除 engine.py:52 外，還有 backend/app/strategy/prompts/strategy-guide.md:34（"exclude_new_days": 60, # 排除上市N天内新股，位於「基础过滤参数 (Stage 1, 引擎统一处理)」）、strategy-guide.md:309、strategy-guide-compact.md:36、strategy-example.md:37；frontend/src 與 docs/ 沒有結果
      - backend/app/strategy/ai_generator.py:20 GUIDE_PATH = .../prompts/strategy-guide-compact.md（LLM 會被告知可用 exclude_new_days）
      - s5_synthetic.py 輸出：matrix basic mask identical for exclude_new_days in {absent,0,30,10000}: True；polars basic expr identical: True；rows kept [105,105,105] of 105
      - s5_synthetic.py 輸出：合成新股共 25 根 bar，bottom_reversal 在 DEFAULT_BASIC_FILTER 下於第 [21] 根進場，declared required_warmup_bars: 60；把型態提前 1 根、讓確認 K 落在第 20 根時 → []（最早只能是第 21 根）
      - backend/app/strategy/engine.py:660-710 required_history_bars 只決定歷史長度；backend/app/services/screener.py:397-404 用它載入 enriched 歷史；matrix.py:3523-3582 pipeline 沒有逐檔 bar 數門檻
      - s4_strategy_entries.py（腳本與輸出在 AUDIT_SCRATCH/work/verify-newdays/s4_out.txt、s4_summary.csv、s4_hits.txt）：股票類新股在前 30 根 bar 內的進場筆數（最早 bar）：strong_open 83 筆（第 2 根）、macd_golden 54（7）、boll_breakout 35（20）、low_volatility_leader 22（21）、oversold_bounce 17（7）、volume_price_surge 17（21）、oversold_reversal 15（6）、lorentzian_knn 12（25）、swing_trend_confirmation 1（25），合計 256 筆；其中第 20 根以前 103 筆；上市未滿 30 個日曆天 121 筆
      - s4 輸出：bottom_reversal 對新股（股票）最早在第 93 根 bar 觸發，前 30 根內 0 筆；ETF 最早第 128 根。另有 11 個策略前 30 根內 0 筆（bullish_alignment、ma_golden_cross 等最早第 60～61 根）；factor_rank_research 因缺 scoring 參數無法執行
      - s4_hits.txt：stock,oversold_reversal,3718.TPEX,2026-09-17,bar=10,cal_days=14（security_master listing_date 2026-09-03）；官方參考 csv/tpex_daily_quotes_20260923.csv 有 2026-09-23,3718,中光電投控,64.7,...
      - s2_new_listings.py 輸出：security_master listing_date 2377 筆全部可解析（8 碼 1986 筆、民國 7 碼 271 筆、YYYY/MM/DD 120 筆）；視窗內上市 274 檔（股票 191 檔、ETF 83 檔），first_bar − listing_date 最大 2 天，表示用上市日實作可行
      - 線上核實：嘗試用 WebSearch 查 3718 上櫃日期，但本 session 的 WebSearch 配額已用完（200/200），沒有線上來源
      - 唯讀確認：執行時設 PYTHONDONTWRITEBYTECODE=1，並把 NUMBA_CACHE_DIR 指到 scratchpad；執行後 git status --untracked-files=all 仍只有開始時的 3 個 M 檔，backend/app 內沒有新檔

### [P3 → P3] 反轉類策略預設偏好高 RSI 與高 5 日動能，評分方向和「超跌反彈」邏輯相反（部分成立）

- **主張**：oversold_bounce、n_day_low_reversal、oversold_reversal、bottom_reversal 的 META scoring 沒有設定方向，預設全部是 high。在超賣或新低的候選中，這會讓「比較不超賣、跌得比較少」的股票排在前面，和均值回歸的研究結論相反。
- **正確的定義或數值**：反轉策略對 rsi_14、momentum_5d 預設採 low 方向（越超賣越優先），或換成衡量反轉強度的欄位。
- **app 實際行為**：預設偏好 RSI 較高、5 日跌幅較小的候選；樣本內與未來報酬呈負相關。
- **對使用者的影響**：使用反轉策略時，排序靠前的候選在歷史上表現反而較差（樣本內統計）；目前這四支策略在 9/23 都沒有命中。
- **修法建議**：在 META 加上預設的 scoring_directions，例如 {'rsi_14':'low','momentum_5d':'low'}，或提供 META 層級的方向宣告；調整後用回測比較排名前 N 名的績效。
- **稽核者信心**：plausible
- **程式碼證據**：
  - backend/app/strategy/builtin/oversold_bounce.py:45 {'change_pct':0.3,'vol_ratio_5d':0.3,'momentum_5d':0.2,'rsi_14':0.2}
  - backend/app/strategy/builtin/n_day_low_reversal.py:36
  - backend/app/strategy/builtin/oversold_reversal.py:50
  - backend/app/strategy/builtin/bottom_reversal.py:78
  - backend/app/strategy/scoring.py:82-90（未設定時沒有 low 方向）
- **線上證據**：
  - https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/rsi-2：「the lower RSI dipped, the higher the returns on subsequent long positions」
- **本地計算證據**：
  - W\s12_direction.py（樣本內、未還原價、5 日前瞻報酬）：oversold_bounce n=1736，同日多候選 n=1546 時名次與超額報酬的相關為 −0.125；合併 Spearman：score −0.289、rsi −0.194、momentum_5d −0.448
  - n_day_low_reversal n=1036：同日相關 −0.129；合併 Spearman：rsi −0.424、momentum_5d −0.536
  - oversold_reversal：同日相關 −0.062；bottom_reversal：同日相關 −0.066
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：四支反轉策略的 META scoring 都沒有設定方向；META 也無法宣告方向（scoring.py:82-90 只讀 overrides），所以所有評分因子都採 high。各策略的評分因子如下：oversold_bounce 同時含 rsi_14 和 momentum_5d；n_day_low_reversal 只含 momentum_5d；oversold_reversal 和 bottom_reversal 只含 rsi_14。

finding 的樣本內數字可以重現（2024-01 到 2026-09，5 日前瞻報酬，未還原價）。例如 oversold_bounce 的同日相關是 −0.124。但只有 oversold_bounce 的負相關比較穩健：排除崩跌日後仍是 −0.128，每日 IC −0.166（t≈−3）。主要來自 momentum_5d 和 change_pct，rsi_14 的影響很小（同日相關 −0.048，不顯著），而且 2024 年沒有這個效應。

n_day_low_reversal 的 −0.129 全部來自 2025-04-07／08 兩天，排除後約為 0。oversold_reversal 和 bottom_reversal 都不顯著，樣本也太小。合併 Spearman 反映的是擇時，和每日排序無關。持有 1 天時，oversold_bounce 和 n_day_low_reversal 的相關方向相反（轉為正）。

線上來源講的是 RSI(2) 的進場門檻，不能直接推論到 RSI(14) 的候選排序。建議只針對 oversold_bounce 評估把 momentum_5d 改成 low（或移除），並用前 N 名的回測驗證；不宜對四支策略一律把 rsi_14 和 momentum_5d 改成 low，因為 n_day_low_reversal 和 bottom_reversal 改完後，樣本內的每日 IC 反而變差。
    - 理由：程式機制成立：四支策略的 META 都沒有設定評分方向，而且 META 本來就沒辦法宣告方向。scoring.py:82-90 的 effective_scoring_directions 只讀 overrides；data/user_data/strategy_overrides 是空目錄，所以實際都用預設的 high。matrix.py:3676-3745 的 build_matrix_score 在每日候選集合內做 min-max 正規化，只有明確設為 low 的因子才會反轉。

finding 的敘述有兩處不精確。第一，只有 oversold_bounce 同時納入 rsi_14 和 momentum_5d：n_day_low_reversal 只有 momentum_5d，沒有 RSI；oversold_reversal 和 bottom_reversal 只有 rsi_14，沒有 momentum_5d。所以不能說四支都「偏好高 RSI 與高 5 日動能」。finding 替 n_day_low_reversal 列出的 RSI 相關（−0.424）和它的排序沒有關係。

數值部分，我另外寫了一套程式，直接使用資料庫已存的 enriched 指標，不重算指標；再用 polars 自己實作進場條件、基本過濾和每日 min-max 評分，接著計算 5 日前瞻報酬（未還原收盤價）。重現結果與 finding 幾乎一致，差距在 ±0.01 內，只有 bottom_reversal 的同日相關略有差異（−0.058 對 −0.066）。

穩健性檢查後，結論要大幅收窄：
(1) 合併 Spearman 同時混合不同日期與不同股票，反映的主要是擇時。例如 2025-04 關稅崩跌日，候選股的 RSI 和動能都低，之後的報酬卻高。但評分只在同一天內排序，所以這個數字不能拿來證明排序方向錯。
(2) 同日相關集中在少數崩跌日。n_day_low_reversal 有 44% 的事件落在前 5 大日期（2025-04-08 一天就有 299 檔）；排除這 5 天後，同日相關從 −0.129 變成 −0.001，每日等權 IC 則是 −0.024（t=−0.30）。
(3) oversold_reversal 排除前 5 大日期後是 +0.014，改用百分位名次算則是 +0.049，都不顯著。bottom_reversal 只有 15 個交易日同時有 2 個以上候選，71% 事件集中在 5 天，樣本不足以下結論。
(4) 只有 oversold_bounce 的負相關比較穩健：同日相關 −0.124，排除前 5 大日期後 −0.128，每日 IC −0.166（t=−3.05）。但驅動它的主要是 momentum_5d（−0.134）和 change_pct（−0.117）；rsi_14 只有 −0.048（每日 IC −0.064，t=−1.1）。而且 2024 年沒有這個效應（+0.008），負相關只出現在 2025 和 2026 年。
(5) rsi_14 的同日相關在四支策略裡都沒有顯著為負。n_day_low_reversal 本來就沒用 rsi_14；在 oversold_reversal 和 bottom_reversal 的每日 IC 甚至是正的（+0.017、+0.107）。
(6) 結論受持有天數影響。H=1 時，oversold_bounce 和 n_day_low_reversal 的同日相關是正的（+0.090、+0.111），H=5 才轉負。
(7) 把 rsi_14 和 momentum_5d 改成 low 之後：oversold_bounce 的同日相關從 −0.124 變成 +0.023，每日 IC 從 −0.166 變成 −0.051；n_day_low_reversal 的每日 IC 反而變差（−0.024 變成 −0.083）；bottom_reversal 也變差（−0.086 變成 −0.133）。所以依樣本內資料，「對四支策略一律改成 low」這個修法沒有支持。
(8) 線上來源的原句確實存在，但講的是 Connors 的 RSI(2) 進場門檻（RSI 跌破 5 優於跌破 10），不是 RSI(14)，也不是在候選股之間排序，不能直接推論到本案。
(9) 9/23 四支策略都沒有命中：用 9/23 enriched 資料重算為 0 檔，而且該日 1323 檔上市股的收盤價與官方 MI_INDEX 完全一致。不過 9/23 分區只有上市股，沒有上櫃股（9/22 有 988 檔上櫃）。

綜合來看，機制和數字都能重現，但「四支都偏好高 RSI 與高動能」和「與均值回歸結論相反」只在 oversold_bounce 的 momentum_5d／change_pct 部分有穩健的樣本內支持。嚴重度維持 P3。
    - 證據：
      - backend/app/strategy/builtin/oversold_bounce.py:45 scoring={change_pct:0.3, vol_ratio_5d:0.3, momentum_5d:0.2, rsi_14:0.2}（同時含 RSI 和動能）
      - backend/app/strategy/builtin/n_day_low_reversal.py:124（檔內第 36 行）scoring={change_pct:0.4, vol_ratio_5d:0.3, momentum_5d:0.3}：沒有 rsi_14
      - backend/app/strategy/builtin/oversold_reversal.py:50 scoring={change_pct:0.4, rsi_14:0.3, vol_ratio_5d:0.3}：沒有 momentum_5d
      - backend/app/strategy/builtin/bottom_reversal.py:78 scoring={change_pct:0.4, vol_ratio_5d:0.35, rsi_14:0.25}：沒有 momentum_5d
      - backend/app/strategy/scoring.py:82-90 effective_scoring_directions 只讀 overrides['scoring_directions']，META 無法宣告方向；data/user_data/strategy_overrides 是空目錄
      - backend/app/backtest/matrix.py:3676-3745 build_matrix_score：每日候選集合內做 min-max，只有 directions[name]=='low' 才用 1−x；backend/app/strategy/engine.py:1495-1530 polars 路徑的邏輯相同
      - 指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-revdir/repro.py（使用已存的 enriched 指標，並自行實作進場條件與評分）。進場數：oversold_bounce 1745、n_day_low_reversal 1041、oversold_reversal 568、bottom_reversal 214
      - 重現對照（stats.py）。oversold_bounce n=1736（finding 為 1736）；合併 Spearman：score −0.290（−0.289）、rsi −0.194（−0.194）、momentum_5d −0.449（−0.448）；同日相關 −0.124，n=1548（finding：−0.125，n=1546）
      - 重現對照。n_day_low_reversal n=1039（finding 為 1036）；合併 Spearman：rsi −0.424（−0.424）、momentum_5d −0.537（−0.536）；同日相關 −0.129（−0.129）。oversold_reversal 同日相關 −0.062（−0.062）；bottom_reversal 同日相關 −0.058（finding 為 −0.066）
      - 集中度檢查（concentration.py）。n_day_low_reversal 前 5 大日期占事件 44.3%（2025-04-08 有 299 檔、2025-04-07 有 76 檔）；排除這 5 天後，同日相關從 −0.129 變成 −0.001，momentum_5d 從 −0.134 變成 −0.010。oversold_reversal 排除後是 +0.014，改用百分位名次算是 +0.049。bottom_reversal 前 5 大日期占 71.0%
      - 每日等權 IC（只計入有 3 個以上候選的交易日）。oversold_bounce：score −0.166（t=−3.05，86 天）、rsi −0.064（t=−1.11）、mom −0.076（t=−1.27）。n_day_low_reversal：score −0.024（t=−0.30）。oversold_reversal：score −0.133（t=−1.79）、rsi +0.017。bottom_reversal：score −0.086（僅 10 天）、rsi +0.107
      - oversold_bounce 的組成因子同日相關：momentum_5d −0.134、change_pct −0.117、rsi_14 −0.048、vol_ratio_5d +0.037。分年度的同日相關：2024 為 +0.008、2025 為 −0.185、2026 為 −0.227
      - 持有天數敏感度（同日相關）：oversold_bounce H1 +0.090、H5 −0.124、H10 −0.064；n_day_low_reversal H1 +0.111、H5 −0.129、H10 −0.087
      - rsi_14、momentum_5d 改成 low 後（score_low）：oversold_bounce 同日相關 +0.023，每日 IC −0.051；n_day_low_reversal 每日 IC −0.083（原為 −0.024，變差）；bottom_reversal 每日 IC −0.133（原為 −0.086，變差）
      - 排除前瞻窗內單日漲跌超過 10.5% 的事件（公司行動）後，結果幾乎不變：oversold_bounce 同日相關 −0.122、合併 score −0.287
      - 9/23 重算：四支策略都是 0 檔命中。9/23 enriched 的 1323 檔上市收盤價與官方 reference/csv/twse_mi_index_20260923_quotes.csv 的 close_twd 全部一致（1323/1323）；該分區沒有 .TPEX 股票，9/22 分區有 988 檔上櫃股
      - https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/rsi-2 原文：「He found that returns were higher when buying on an RSI dip below 5 than on one below 10. In other words, the lower RSI dipped, the higher the returns on subsequent long positions.」這是 Connors 對 RSI(2) 進場門檻的研究，不是 RSI(14)，也不是候選股之間的排序
      - git status 與開始時的快照相同（只有既有的 3 個改動）；產物只寫在 scratchpad\audit\work\verify-revdir\

### [P3 → P3] n_day_low_reversal 要求「收盤就是 60 日最低收盤」，幾乎抓不到盤中破底後收回的反轉（部分成立）

- **主張**：進場條件是 close ≤ low_60d（含當日的 60 日最低收盤）且 close > open，實際上是「開低走高、但仍收在 60 日最低點」的 K 棒；典型的「盤中破新低、收盤收回」型態幾乎全部被排除。
- **正確的定義或數值**：「觸及新低後反轉」應該用盤中低點觸及前 60 日低點、收盤收回的條件，或把描述改成「收盤創 60 日新低且收紅」。
- **app 實際行為**：策略抓到的是收在新低的 K 棒，和名稱「新低反轉」所指的型態幾乎沒有交集。
- **對使用者的影響**：使用者看到的「新低反轉」候選並不是反轉型態；9/23 沒有命中。
- **修法建議**：進場改為 low ≤ shift(rolling_min(low,60),1) 且 close > shift(low_60d,1) 且 close>open（可加下影線比例）；或修改 META 描述，並另外提供盤中破底版本。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/builtin/n_day_low_reversal.py:18 description「觸及60日新低後當日收陽放量, 反轉訊號」
  - backend/app/strategy/builtin/n_day_low_reversal.py:60 entry &= market.close <= matrix_feature(market, 'low_60d')
  - backend/app/indicators/pipeline.py:440（low_60d 為收盤價的 rolling min，:163 標籤寫「60日最低价」）
- **線上證據**：
  - https://www.cmcmarkets.com/en-gb/technical-analysis/hammer-candlestick-patterns：底部反轉 K 棒的描述為「a small body relative to the overall candle range. It sits near the top of the trading range」（收盤回到高檔，而不是收在最低點）
  - https://github.com/shy3130/tick-stock-panel/issues/329（上游把 60 日極值統一為收盤口徑，屬指標層的決定）
- **本地計算證據**：
  - W\s9_experiments.py E2：程式條件命中 2,127 筆；「盤中 low ≤ 前 60 日最低、收陽、收盤高於前 60 日最低收盤、量比 ≥1.5」命中 2,564 筆；兩者重疊只有 7 筆
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：n_day_low_reversal 的進場條件是收盤價 ≤ 含當日的 60 日最低收盤、收盤 > 開盤，且量比 ≥ 1.5，因此只抓「收盤創 60 日收盤新低且收紅」的 K 棒。凡是盤中破底、收盤收回到前 60 日最低收盤之上的反轉，都依定義被排除。

全歷史（2024-01-02～2026-09-23）的實算數字：app 原始訊號共 2,694 筆（finding 寫 2,127，是 NaN 傳播算法低估）。「盤中破前 60 日低點 + 收紅 + 量比 ≥ 1.5」的事件共 5,556 筆，程式只抓到 2,247 筆（40.4%）。其中收盤收回前低的 3,316 筆（finding 寫 2,564），與程式命中只重疊 7 筆，但這個重疊數是兩個定義互斥造成的，不是實證結果。

程式命中不能說成「不是反轉型態」：2,694 筆中有 83.4% 盤中觸及 60 日新低，71.6% 收在當日振幅上半部，10.7% 符合嚴格錘子線，與 META 描述「觸及60日新低後當日收陽放量」大致相符；錘子線的定義也不要求收盤回到前低之上。

2026-09-23 原始訊號有 1 筆（00668.TWSE），因成交金額 1,809 萬元低於 basic filter 門檻 2,000 萬元而被濾掉，所以選股結果為 0 筆。

這是型態涵蓋範圍的設計取捨（P3）。建議在描述中註明「收盤創新低」口徑，或另外提供「盤中破底、收盤收回」的版本。
    - 理由：重現結果：finding 的三個數字（2,127／2,564／7）只有用稽核原本的算法才算得出來。那個算法把資料攤成「日期 × 標的」的密集矩陣，再用 np.min 取 60 日最低；只要窗內有任何一天缺值，結果就是 NaN，這樣排除了 128,521 列有收盤價的資料。app 實際執行的 compute_signals 是按每檔自己的有效交易日滾動，全歷史（2024-01-02～2026-09-23，2,346 檔）共有 2,694 筆進場。另一套獨立的 polars 實作也是 2,694 筆，兩邊逐筆比對零差異。finding 提出的替代定義實際是 3,316 筆，兩者重疊 7 筆，這個重疊數和 finding 相同。

程式語意確認如下：進場條件是收盤價 ≤ 含當日在內 60 根 K 棒的最低收盤、收盤 > 開盤，且量比 ≥ 1.5。所以命中的 K 棒必定收在 60 日最低收盤，finding 對這點的描述正確。

但有兩個結論過度推論：
（1）「重疊只有 7 筆」是定義本身造成的，不是實證發現。替代定義要求收盤 > 前 60 日最低收盤，程式要求收盤 ≤ 前 59 日最低收盤，兩者只有在「第 60 根前的那一天剛好是唯一最低點」時才會同時成立。7 筆重疊全部符合 cmin59_prior ≥ close > cmin60_prior。
（2）說「和名稱所指的型態幾乎沒有交集」「不是反轉型態」並不成立。2,694 筆命中中：
- 2,247 筆（83.4%）盤中低點確實跌破前 60 日盤中最低；
- 1,930 筆（71.6%）收在當日振幅上半部，收盤位置中位數 0.69；
- 287 筆（10.7%）符合嚴格錘子線條件。
這些命中大多符合 META 描述「觸及60日新低後當日收陽放量」。CMC Markets 對錘子線的定義只要求實體在當日區間頂部、下影線 ≥ 實體 2 倍，沒有要求收盤要回到前期低點之上，所以 finding 引用的線上證據不能推出「命中的不是反轉型態」。

finding 真正成立的部分：把事件定義為「盤中破前 60 日低點 + 收紅 + 量比 ≥ 1.5」，全歷史共 5,556 筆，程式只抓到 2,247 筆（40.4%）。其餘約 60% 是收盤收回到前 60 日最低收盤之上的那一類，這些確實被全數排除。

9/23 的狀況：原始訊號其實有 1 筆（00668.TWSE），但它的成交金額是 18,090,958 元，低於 DEFAULT_BASIC_FILTER 的 amount_min 2,000 萬元，所以被過濾掉。選股結果顯示 0 筆，finding 說「9/23 沒有命中」就選股輸出而言正確。自選 9 檔最近一次命中是 2025-11-07。

嚴重度維持 P3：這屬於語意與名稱的設計取捨，沒有資料錯誤。
    - 證據：
      - 程式：backend/app/strategy/builtin/n_day_low_reversal.py:60 `entry &= market.close <= matrix_feature(market, "low_60d")`，:62 `close > open`，:64-66 `vol_ratio_5d >= 1.5`；:18 description「觸及60日新低後當日收陽放量, 反轉訊號」
      - 程式：backend/app/backtest/matrix.py:3910-3911 `if name == "low_60d": return valid_rolling_min(market.close, close_valid, 60)`（matrix 策略自行以收盤價計算，含當日）；pipeline.py:439-440 批次路徑同為 close.rolling_min(60)
      - 本地計算（app 本身）：`uv run python ...\work\verify-ndlow\repro.py` → `METHOD1 app compute_signals entry count: 2694`；獨立 polars 實作 `METHOD2 indep code: 2694 touch_rev (finding's alt): 3316 overlap: 7`；`app-only: 0 indep-only: 0`
      - 並列比較：finding 程式命中 2,127 對上實算 2,694；finding 替代定義 2,564 對上實算 3,316；重疊兩邊都是 7。以 repro2.py 模擬 finding 的密集矩陣 np.min 算法 → `code 2127 touch_rev 2564 overlap 7`，另有 `rows with a close but NaN grid window: 128521`，可見差異來自 NaN 傳播造成低估
      - 重疊是定義本身造成的：repro_out.txt 列出的 7 筆重疊全部滿足 cmin59_prior ≥ close > cmin60_prior，例如 1558.TWSE 2024-08-01 close 99.4、prior59 min 100.5、prior60 min 99.1；8367.TWSE 2024-08-06 close 47.6、47.6、46.7
      - 型態分布（repro_out.txt）：盤中破前 60 日低點、收紅、量比 ≥ 1.5 的事件共 5,556 筆，程式抓到 2,247 筆（40.4%）。程式命中的 2,694 筆中：盤中觸及 60 日新低 2,247 筆；收盤位置 ≥ 0.5 有 1,930 筆；≥ 0.67 有 1,386 筆；錘子線（下影線 ≥ 2 倍實體且上影線 ≤ 實體）287 筆；收盤位置四分位數 0.45／0.69／0.93。finding 替代定義那組的收盤位置中位數為 0.91
      - 線上：https://www.cmcmarkets.com/en-gb/technical-analysis/hammer-candlestick-patterns 原文「A small body at the top of the candle's range」「This lower shadow should be at least twice the length of the body」；頁面沒有要求收盤高於前期低點，只強調「forming after a sustained downtrend」
      - 9/23：repro2_out.txt → `raw hit on latest: 00668.TWSE filtered-in: False`、`filtered hits on latest: []`；全歷史 `raw entries 2694 after DEFAULT_BASIC_FILTER 1041`。backend/app/strategy/engine.py:41-54 DEFAULT_BASIC_FILTER amount_min 0.2e8
      - 官方比對：reference/csv/twse_mi_index_20260923_quotes.csv 第 63 行 `2026-09-23,00668,國泰美國道瓊,300035,130,18090958,60.2,60.4,60.2,60.35`，與本地 enriched 完全一致；前 59 日最低收盤 60.4、前 60 日盤中最低 60.3、量比 1.896（repro3_out.txt）
      - 資料品質旁證：twse_stock_day_202608_202609.csv 共 380 列，與本地 raw_close 比對最大差值 0.0。成交量有 16 列在 9/16–9/23 低於官方（例如 2330 9/18 官方 40,892,688、本地 37,190,511），不影響 00668 當日（兩邊都是 300,035），也不在本 finding 範圍內，未深究
      - 旁注（新面向，推測其影響有限）：即時路徑 pipeline.py:1594-1598 用 min(_low_59d, 當日 low) 計算 low_60d，_low_59d 來自 repository.py:914 的盤中 low。這和批次路徑 pipeline.py:440 的收盤口徑不一致。matrix 策略會用 matrix.py:3911 以收盤重算，所以不影響本策略，但即時寫入日的 enriched signal_n_day_low 欄位口徑不同

### [P3 → P3] pullback_resume 的最低成交量用股數 2,000,000 股，與股價無關（A 股 2 萬手的沿用）（部分成立）

- **主張**：流動性門檻用股數而不是金額；在台股，低價股容易達到、中價股很難達到，和實際流動性不一致。
- **正確的定義或數值**：流動性門檻以成交金額（例如 ≥1 億元）或相對量能表示。
- **app 實際行為**：以股數表示，對 6196、3545 等自選股幾乎永遠不成立。
- **對使用者的影響**：自選股 6196、3545、00893 幾乎不可能觸發「強勢回檔再起漲」。
- **修法建議**：改成 amount ≥ min_amount（新增參數），或以 vol_ratio 取代絕對股數。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/builtin/pullback_resume.py:56-64 min_volume 預設 2000000.0（股）
  - backend/app/strategy/builtin/pullback_resume.py:118 market.volume >= min_volume
- **線上證據**：
  - （無）
- **本地計算證據**：
  - W\s9_experiments.py E4（2026-09-23 全市場）：各價格區間達到 2M 股的比例為 <50 元 24.2%、50–200 元 24.3%、200–1000 元 40.3%、≥1000 元 60%；各區間成交金額中位數為 568 萬、3,425 萬、4.55 億、66.7 億
  - 自選股近 60 日達標天數：6196 13/60、3545 4/60、00893 2/60、00885 37/60；2454、0050、00878 為 60/60
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：pullback_resume.py:56-64、118 的流動性門檻用固定股數，預設 2,000,000 股。這個預設值是忠實移植台股參考專案 tw-stock-radar「當日成交量大於 2000 張」的規則，不是沿用 A 股的 2 萬手；swing_trend_confirmation.py:66-74、140 也用同一個門檻。因為門檻以股數計，換算成成交金額就是 200 萬股×股價，兩個方向都會和成交金額所代表的流動性不一致。9/23 全市場（上市加上櫃）有 158 檔成交金額 ≥1 億、股價 ≥57.4 元的股票被擋下；另有 92 檔成交金額不到 1 億、股價 ≤47 元的股票通過。回測中，被擋掉但成交金額 ≥1 億的進場訊號有 175 次，全部是 ≥50 元的股票。但如果不固定成交金額，各價格區間的達標比例其實隨股價上升（官方上市櫃普通股 11.7%、19.1%、35.2%、49.0%），所以「低價股容易達到、中價股很難達到」不成立。對自選股的影響也有限：661 個交易日中，門檻只擋掉 6196、3545、2379 各一次進場，00893 一次都沒被擋，「幾乎不可能觸發」不成立。自選股中真正被誤判的是 6196：近 60 日每天成交金額都 ≥1 億，卻只有 13 天達到 200 萬股。3545 與 00893 本身成交金額就低（近 60 日 ≥1 億的天數只有 4 天與 2 天），改成「成交金額 ≥1 億」一樣受限；00885 則相反，以股數算 37/60 天達標，以成交金額算只有 4/60 天。修正方向仍建議改用成交金額門檻，或相對量能；兩支策略要一起改。
    - 理由：我用自己寫的腳本重算，並拿官方參考資料交叉比對，finding 列出的數字全部重現。E4 用本地 2026-09-23 資料算，各價格區間達到 200 萬股的比例是 24.17%、24.28%、40.32%、60.0%，finding 寫 24.2%、24.3%、40.3%、60%。成交金額中位數若照 finding 的算法（收盤價×成交量），得 568 萬、3,425 萬、4.55 億、66.7 億，與 finding 一字不差；改用 amount 欄位則是 569 萬、3,441 萬、4.51 億、67.3 億。自選股近 60 日（2026-07-01～09-23）的達標天數也完全相同：6196 13/60、3545 4/60、00893 2/60、00885 37/60，2454、0050、00878 都是 60/60。另外 2379 是 50/60、3034 是 59/60。我再用官方 STOCK_DAY 對 8/3～9/23 共 38 個交易日逐日核對，達標與否 0 筆不一致。

「門檻用股數，所以達標所需的成交金額隨股價放大」這個機制成立：門檻換算成金額就是 200 萬股×股價，18 元約 3,654 萬，549 元約 10.98 億。不過 finding 有三處說法不成立或講過頭：

(1)「低價股容易達到、中價股很難達到」和 finding 自己的 E4 數據矛盾。達標比例隨股價單調上升。用官方上市加上櫃資料算，只看普通股時是 11.7%、19.1%、35.2%、49.0%。回測中被這個門檻擋掉的訊號比例，低價區反而最高：<50 元 60.4%，≥200 元約 37%～41%。只有在「成交金額相同」的前提下，才是低價股比較容易達標。真正的問題是兩個方向的誤判：9/23 本地資料中，有 74 檔達到 200 萬股但成交金額不到 1 億，全部是 ≤42.15 元的股票；另有 97 檔沒達到 200 萬股，成交金額卻 ≥1 億，全部是 ≥57.4 元的股票。官方上市加上櫃資料分別是 92 檔與 158 檔。

(2) 對使用者的影響講過頭了。我用實際的 compute_signals 跑完 661 個交易日，拿掉門檻時 6196、3545、00893 各有 3、2、1 次進場，套用預設門檻後是 2、1、1 次。門檻只擋掉 6196 的 2025-05-12 和 3545 的 2025-07-15 各一次，00893 一次都沒被擋。訊號少是策略條件本身就嚴格，不是門檻造成的。而且 3545 和 00893 在近 60 日中，成交金額 ≥1 億的天數只有 4 天和 2 天。finding 建議改成「成交金額 ≥1 億」後，這兩檔在歷史上的進場次數仍是 1 次和 1 次，一樣受限。自選股中真正被誤判的只有 6196：近 60 日每天成交金額都 ≥1 億，但只有 13 天達到 200 萬股；另外 2379 也有一次被擋。00885 則是相反方向：以股數算 37/60 天達標，以成交金額 ≥1 億算只有 4/60 天。

(3) 標題說門檻是「A 股 2 萬手的沿用」，這不成立。這支策略是在 commit d839721 以 clean-room 方式移植台股專案 tw-stock-radar 時加入的。該專案的 README 原文就是「當日成交量大於 2000 張」，2000 張正好等於 200 萬股，單位也和 Taiwan 的 volume_unit=shares 一致。

另外 finding 漏掉一處：swing_trend_confirmation 也用了同樣的 200 萬股預設門檻。嚴重度維持 P3。它是忠實移植原始規則的參數設計，使用者可以自行調整，也沒有造成錯誤的交易結果。
    - 證據：
      - backend/app/strategy/builtin/pullback_resume.py:56-64 min_volume 預設值 2000000.0，標籤是「最低成交量(股)」；:118 條件 market.volume >= min_volume
      - backend/app/strategy/builtin/swing_trend_confirmation.py:66-74,140 有相同的 min_volume 預設 2000000.0 與相同判斷（finding 沒提到）
      - backend/app/volume_units.py:12-16 台股（.TWSE/.TPEX）矩陣的 volume_unit 是 'shares'；本地 2454 在 9/23 的 volume 是 6,425,491，官方 MI_INDEX 是 6,430,491，單位與數量級一致
      - docs/radar-strategies-validation.md:8-10 策略以 clean-room 方式移植自 tw-stock-radar；git show d839721 的 commit 標題是 'feat: add six Taiwan radar strategies'
      - 線上來源 https://raw.githubusercontent.com/b9007082-cell/tw-stock-radar/main/README.md 原文：「確認時需紅 K 收復 5 日線、突破前一日高點、轉強量大於回檔均量，且當日成交量大於 2000 張」。2000 張＝2,000,000 股，不是 A 股的 2 萬手
      - 指令 v1_cross_section.py（本地 taiwan/daily 2026-09-23，n=1323，只有上市）：各區間達到 200 萬股的比例 <50 元 0.2417、50-200 元 0.2428、200-1000 元 0.4032、≥1000 元 0.60；amount 中位數 5,693,500／34,411,104／451,164,060／6,734,400,000
      - 指令 v4_crosstab.py：用 finding 的算法（收盤價×成交量）得中位數 5,684,747／34,254,702／455,011,823／6,667,407,880，與 finding 的 568 萬、3,425 萬、4.55 億、66.7 億完全吻合
      - 指令 v1_cross_section.py（官方 TWSE MI_INDEX 加 TPEx dailyQuotes 2026-09-23，只看普通股 n=1958）：達到 200 萬股的比例 <50 元 11.7%、50-200 元 19.1%、200-1000 元 35.2%、≥1000 元 49.0%。比例隨股價上升，與「低價股容易達到」相反
      - 指令 v4_crosstab.py：本地 9/23 有 74 檔達到 200 萬股但成交金額 <1 億（收盤價最高 42.15、中位數 14.98），97 檔未達 200 萬股但成交金額 ≥1 億（收盤價最低 57.4、中位數 264.5）；官方上市加上櫃資料分別是 92 檔與 158 檔
      - 指令 v2_watchlist.py（本地 2026-07-01～09-23 共 60 日）：達到 200 萬股的天數 6196 13、3545 4、00893 2、00885 37、2454/0050/00878 60、2379 50、3034 59；成交金額 ≥1 億的天數 6196 60、3545 4、00893 2、00885 4
      - 指令 v2_watchlist.py：官方 STOCK_DAY 8/3～9/23 共 38 日，自選 9 檔的達標天數與本地相同（例如 6196 6/38、3545 1/38、00893 1/38），逐日判定不一致 0 筆。有 11 筆本地 volume 略低於官方，但都不影響是否達到 200 萬股
      - 指令 v3_signal_block.py（kline_daily_enriched 2024-01-02～2026-09-23，661 日×2346 檔，呼叫實際的 compute_signals）：全市場進場訊號不設門檻 2320 次、預設門檻 1022 次；被擋比例 <50 元 60.4%、50-200 元 56.4%、200-1000 元 37.3%、≥1000 元 40.5%。被擋但成交金額 ≥1 億的有 175 次，全部是 ≥50 元的股票；通過門檻但成交金額 <1 億的有 161 次
      - 指令 v3_signal_block.py，自選股進場次數（不設門檻／預設門檻／改成成交金額 ≥1 億）：6196 3/2/3、3545 2/1/1、00893 1/1/1、00885 2/2/2、2379 1/0/1、2454 3/3/3。被擋掉的只有 6196 的 2025-05-12（成交金額 1.86 億）、3545 的 2025-07-15（6,429 萬）與 2379 的 2025-07-16（11.2 億）
      - 腳本位置：AUDIT_SCRATCH/work/verify-pbr-minvol/v1_cross_section.py、v2_watchlist.py、v3_signal_block.py、v4_crosstab.py。執行前後 git status 相同；data\backend.log 的最後幾行是使用者正在執行的後端所記錄的 watchfiles 變動，不是本稽核寫入的資料

### [P3 → P3] 暖機期不足：RSI 型進場用 60 根，EMA/MACD/ATR 類虛擬評分欄位的暖機只有 1 根（部分成立）

- **主張**：required_history_bars 取策略暖機（60）與 scoring_warmup_bars 的最大值；EMA、ATR、MACD 類虛擬欄位在 _ROLLING_SCORING_WARMUP 裡沒有登記，會回傳 1。欄位不由面板提供時，矩陣會用短歷史重算 EWM，起始值的影響還沒消退。
- **正確的定義或數值**：暖機根數要涵蓋 EWM 的收斂需求（例如 ≥ 4×週期，或 RSI 至少 250 根）。
- **app 實際行為**：單策略執行（約 62 根）與全部執行或回測（較長歷史）可能得到不同的 RSI 與 EMA 值。
- **對使用者的影響**：預設設定下 rsi_14 由面板提供（已收斂），不受影響；只有使用者改評分欄位（例如移除 rsi_14，或加入 ema60_bias）時才會出現。使用者目前沒有任何 override。
- **修法建議**：把 ema*_bias、macd_*_pct、atr_pct、rsi_* 加入 _ROLLING_SCORING_WARMUP（例如 max(4×n, 120)），RSI 策略的 required_warmup_bars 也提高。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/strategy/scoring.py:52-65、:93-97
  - backend/app/strategy/builtin/oversold_bounce.py:62-64 required_warmup_bars 回傳 60
  - backend/app/strategy/engine.py:660-686
  - backend/app/services/screener.py:403-404（history_bars 決定載入的根數）
- **線上證據**：
  - https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi：「A formula will need at least 250 data points to replicate our RSI numbers.」
- **本地計算證據**：
  - W\s14_short_rsi.py：只給 62 根歷史時，矩陣 RSI14 與完整歷史相比 max 差 4.77、p95 0.49，2026-09-23 有 2 檔在 30 門檻上判斷翻轉；82 根時 max 差 1.45，不再翻轉
  - W\s16_ema_warmup.py：scoring_warmup_bars 對 ema60_bias、macd_hist_pct、atr_pct 回傳 1；62 根時 ema60_bias 的 max 差 0.168、p95 0.030
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：單策略執行時，required_history_bars 取策略暖機（反轉類 oversold_bounce 與 oversold_reversal 為 60）加 1，和 scoring_warmup_bars 取較大值；而 scoring_warmup_bars 對 ema*_bias、atr_pct、rsi_* 都回傳 1，所以實際只載入 62 個交易日。

在這個前提下有兩類 EWM 會用短歷史重算：
1. rsi_14 不在評分欄位時（scoring_replace 移除，或權重設 0），進場條件用的 RSI14 會以 62 根重算。
2. ema{n}_bias 與 atr_pct 的矩陣實作一律從 close 或 true range 重算，不使用已當作依賴載入的面板欄位 ema{n}／atr_14。

以 2026-09-23 實測，62 根對完整歷史：RSI14 max 差 4.77、p95 0.49，00685L.TWSE 與 1476.TWSE 在 30 門檻翻轉；ema60_bias max 差 0.169、p95 0.030；atr_pct max 差 0.0055。端到端實跑時，oversold_reversal 移除 rsi_14 評分後，62 根比 252 根多出 1476.TWSE 的進場命中。全部執行（122 根）或較長歷史的回測則不會出現這些差異。

macd_*_pct 不受影響：macd_hist 由面板提供，62 根對 252 根的差為 0。預設設定下 rsi_14 也由面板提供，結果不受影響。

建議修法：ema*_bias 與 atr_pct 改用已載入的面板欄位（比照 macd_*_pct 的做法），或在 _ROLLING_SCORING_WARMUP 登記足夠的暖機根數；RSI 策略要把 rsi_14 列進 required_fields，或提高 required_warmup_bars（約 250 根才完全收斂）。
    - 理由：finding 列出的數字全部精確重現，但「MACD 類也受影響」這部分不成立，而且它對 EMA／ATR 成因的說法不準。

1) 暖機根數（程式碼與實跑一致）：scoring_warmup_bars 對 rsi_14、ema60_bias、ema20_bias、macd_hist_pct、atr_pct、ma60_bias 都回傳 1。oversold_bounce 與 oversold_reversal 的 required_warmup_bars 都是 60，所以 required_history_bars=61，_load_enriched_history 或 repo.get_enriched_history 再用 trading_dates[-(61+1)] 切出 62 個交易日。

2) 數值重現（用本地 kline_daily_enriched 共 661 個交易日，以 matrix_feature 重算，比較 2026-09-23 共 1323 檔）：
- RSI14，62 根：finding 為 max 4.77／p95 0.49，我算出 max 4.7666／p95 0.4902，中位數 0.1499。
- RSI14，82 根：finding 為 max 1.45，我算出 1.4533，且沒有檔數在 30 門檻翻轉。
- ema60_bias，62 根：finding 為 max 0.168／p95 0.030，我算出 0.1684／0.0302。
- 30 門檻翻轉：62 根時有 2 檔，分別是 00685L.TWSE（完整歷史 32.68，62 根算出 27.92）與 1476.TWSE（30.28 對 29.998），與 finding 相符。最大差來自 00685L，它在 62 根視窗內只有 58 根有效K棒。
- 收斂情形：122 根時 RSI max 0.39；252 根時 max 約 1e-5，與 StockCharts「250 data points」一致。

3) 端到端實跑：用 StrategyEngine.run 餵自組的 history。
- 預設設定：兩個策略在 62 根與 252 根下結果相同，證實 rsi_14 由面板提供、預設不受影響。
- oversold_reversal 移除 rsi_14 評分後：無論用 scoring_replace 還是設 rsi_14 權重 0，62 根單策略執行都比 252 根多出 1476.TWSE 這個進場命中。這是實際的訊號差異。
- oversold_bounce：00685L 的 RSI 雖然翻轉，但其他條件沒過，所以沒有命中差異。
- 全部執行（run_all）時 required_history_bars=121，也就是 122 根。

4) 需要修正的部分：
- macd_*_pct 實際不受影響。scoring_dependencies 會把 macd_hist 當成依賴載入 market.fields，而 matrix_feature('macd_hist_pct') 經由 matrix_feature('macd_hist') 直接讀面板值。62 根對 252 根的差異 max=0.00000。只有在不帶欄位強制重算時才有差（max 0.0227），但正式路徑不會這樣走。
- ema{n}_bias 與 atr_pct 則是「一律」用短歷史重算，面板的 ema60／atr_14 雖然也載入了 market.fields，卻被忽略（engine 測到 in market.fields=False，差異 max 0.1686／0.00555）。所以成因不是「欄位不由面板提供時」才重算，而是這兩類虛擬欄位的矩陣實作沒有使用已載入的面板欄位。
- atr_pct 的誤差很小：p95 0.00043。

5) 線上來源：已用 WebFetch 查證 StockCharts 原文「A formula will need at least 250 data points to replicate our RSI numbers.」。

6) 官方資料交叉核對：TWSE STOCK_DAY 共 380 筆（10 檔、2026 年 8～9 月），本地 raw_close 全部相符，缺漏 0、不符 0。只用官方 38 根算的 RSI14 與完整歷史相差最多 3.43（00893），可佐證截斷效應。

7) 自選股影響：62 根時自選 9 檔的 RSI 差最多 0.38（2454），都離 30 很遠。ema60_bias 差最多 0.0294（2379：完整歷史 0.0616，62 根 0.0322），但只有使用者改評分欄位時才會用到。

嚴重度維持 P3：只有非預設設定會觸發，使用者目前也沒有任何 override。
    - 證據：
      - backend/app/strategy/scoring.py:52-65（_ROLLING_SCORING_WARMUP 沒有 ema*/atr/macd/rsi）與 :93-97（未登記時回傳 1）；實跑 scoring_warmup_bars：ema60_bias=1、macd_hist_pct=1、atr_pct=1、rsi_14=1
      - backend/app/strategy/engine.py:660-686 required_history_bars = max(scoring 暖機, 60+1)=61；backend/app/repository.py:1185-1189 與 backend/app/services/screener.py:294-298 用 trading_dates[-(61+1)] 切出 62 個交易日（實跑 short dates=62）
      - backend/app/backtest/matrix.py:3782-3784 欄位在 market.fields 裡就直接回傳；:3829-3831 ema*_bias 一律用 _matrix_ema(close) 重算；:3934-3942 atr_pct 一律從 true range 重算；:3839-3841 macd_*_pct 經由 matrix_feature('macd_hist') 讀面板欄位
      - backend/app/strategy/engine.py:1108-1147 _matrix_field_columns 用 scoring_dependencies 載入 ema60／atr_14／macd_hist；實跑的欄位清單為 ['amount','atr_14','close','ema60','macd_hist','open','volume']
      - 腳本 AUDIT_SCRATCH/work/verify-warmup-ewm/repro.py（輸出在 repro_out.txt），比較 2026-09-23、1323 檔：62 根時 rsi_14 max=4.7666、p95=0.4902；ema60_bias max=0.1684、p95=0.0302；atr_pct max=0.0055；macd_hist_pct（不帶欄位強制重算）max=0.0227
      - repro.py：30 門檻翻轉有 00685L.TWSE（完整歷史 32.682，62 根 27.916）與 1476.TWSE（30.282 對 29.998）；82 根時 rsi max=1.4533、無翻轉；122 根時 max=0.3929；252 根時 max≈1e-5；00685L 在 62 根視窗內只有 58 根有效K棒
      - 腳本 ...\verify-warmup-ewm\e2e.py（輸出在 e2e_out.txt）：StrategyEngine.run 預設設定下 62 根與 252 根結果相同；oversold_reversal 在 drop_rsi_from_scoring 與 rsi_weight_zero 時，62 根多出 only_short=['1476.TWSE']；全部策略的 required_history_bars=121
      - e2e.py 用實際欄位建矩陣時的 62 根對 252 根：ema60_bias max=0.16859（in market.fields=False）、atr_pct max=0.00555（False）、macd_hist_pct max=0.00000（macd_hist 在 fields 內）、rsi_14 max=4.76656（移除評分後）
      - 面板 rsi_14 與矩陣完整歷史 RSI 在 2026-09-23 的差異 max=0.0001：面板值已收斂，公式也一致
      - 官方交叉核對：twse_stock_day_202608_202609.csv 380 筆，與本地 raw_close 比對缺漏 0、不符 0；只用官方 38 根算 RSI14，與完整歷史相差最多 -3.43（00893.TWSE 73.49 對 70.06）
      - https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi 原文："A formula will need at least 250 data points to replicate our RSI numbers."

### [P3 → P3] 近期（9/16–9/23）本地成交量低於官方，量比 vol_ratio_5d 因此偏差（跨資料層，只報對策略的影響）（部分成立）

- **主張**：enriched 與 taiwan/daily 的 volume 在 9/16–9/23 幾檔股票上比官方 STOCK_DAY 與 MI_INDEX 少，差額都是整張的倍數；量比與放量、縮量過濾都跟著偏差。
- **正確的定義或數值**：收盤後以官方成交股數覆蓋當日量。
- **app 實際行為**：最近幾個交易日的量偏低（原因推測是盤中即時累計量寫入後，沒有被官方盤後資料覆蓋，缺少盤後定價的部分）。
- **對使用者的影響**：量比類條件在最新日期有數個百分點的偏差；檢查的自選股案例中沒有造成門檻翻轉（6196 在 9/17 兩個值都低於 0.8）。
- **修法建議**：交由資料層確認盤後官方覆寫流程；策略側不需要改動。
- **稽核者信心**：plausible
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:486-491（vol_ratio_5d 直接使用 volume）
  - backend/app/strategy/builtin/pullback_to_support.py:85-88、oversold_bounce.py:72-75（量比過濾）
- **線上證據**：
  - 官方 MI_INDEX 2026-09-23（本地參考檔，來源 https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&date=20260923&type=ALLBUT0999）：2330 volume_shares 22,817,873，與 STOCK_DAY 相同
  - https://histock.tw/stock/3034 顯示 9/23 成交量 2,734 張（第三方的口徑也不同）
- **本地計算證據**：
  - W\s3_vol.py：2330 在 9/22 本地 20,891,927、官方 22,009,927（−5.08%）；9/23 本地 21,978,873、官方 22,817,873（−3.68%）；2454 在 9/16 為 −3.75%；ETF 沒有差異
  - W\s7_raw.py：6196 在 9/17 的 vol_ratio_5d，app 為 0.5723、官方為 0.6891；2454 在 9/18 分別為 1.514 與 1.568
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：9/16、9/17、9/18、9/22、9/23 這五天（9/21 沒有），taiwan/daily 的 volume 與 amount 比官方 STOCK_DAY 和 MI_INDEX 少，kline_daily_enriched 的 volume 與 taiwan/daily 完全相同。差額不全是整張，例如 9/18 的 2330 少 3,702,177 股、2454 少 434,469 股、3034 少 29,388 股。

原因：taiwan/daily 取自 MI_INDEX，不是盤中即時累計量。有差異的分區都是當日 16:19–17:03 抓的，隔日補抓的 9/21 與 9/15 以前的分區都正確。缺少那部分成交的均價常偏離收盤價，例如 2454 在 9/22 為 5,302.8、收盤 5,180，所以缺的不只是盤後定價，推測主要是鉅額交易，而且之後沒有重抓覆寫。

vol_ratio_5d 直接使用這個 volume，所以跟著偏差。自選股最大偏差是 6196 在 9/17 的 −16.95%（0.5723 對 0.6891）。由於分母是前 5 日均量，偏差會延續 5 天。自選 10 檔在 0.8 與 1.2 門檻都沒有翻轉。

但 9/23 全市場有 23/1330 檔 TWSE 證券的量偏少，含 ETF 00919 與 00918，個股最多少了 99%（6464）。其中 4 檔翻轉 0.8 門檻、3 檔翻轉 1.2 門檻，所以全市場選股結果會受影響。應由資料層在盤後較晚時段或隔日重抓 MI_INDEX 覆寫當日量；策略側不需要改。
    - 理由：finding 引用的數值都能逐位重現：本地量偏低、vol_ratio_5d 跟著偏差、自選股沒有門檻翻轉。但有 4 處敘述有誤或以偏概全，所以判「部分確認」。

一、重現結果（finding 的數字與我算出的數字並列）
- 2330，9/22：finding 為本地 20,891,927、官方 22,009,927、−5.08%；我的計算完全相同（−5.080%）。
- 2330，9/23：finding 為 21,978,873 對 22,817,873、−3.68%；我的計算相同（−3.677%）。MI_INDEX 9/23 同樣是 22,817,873，與 STOCK_DAY 一致。
- 2454，9/16：finding 為 −3.75%；我的計算是 6,391,865 對 6,640,865，同為 −3.75%。
- 6196 在 9/17 的 vol_ratio_5d：finding 為 app 0.5723、官方 0.6891；我算出 0.5723 與 0.6891。
- 2454 在 9/18 的 vol_ratio_5d：finding 為 1.514 與 1.568；我算出 1.5142 與 1.5676。
- 儲存的 vol_ratio_5d 與我用本地 volume 依 pipeline.py:436/490 公式重算的值完全一致（最大誤差 0.0）。所以量比偏差完全來自 volume。
- enriched 與 taiwan/daily 的 volume 在比對範圍內逐筆相同。
- 10 檔、380 筆（8/3–9/23）中，只有 16 筆和官方不同，全是本地偏少。日期是 9/16、9/17、9/18、9/22、9/23；9/21 沒有差異。
- 4 檔 ETF（0050、00878、00885、00893）沒有差異。
- 自選 10 檔在 9/16–9/23 以 0.8 和 1.2 兩個門檻檢查，翻轉 0 筆，與 finding 一致。

二、不正確或以偏概全的敘述
1. 「差額都是整張的倍數」不成立。9/18 的差額不是整張：2330 少 3,702,177 股（除以 1000 餘 177）、2454 少 434,469 股（餘 469）、3034 少 29,388 股（餘 388）。9/23 全市場還有 6669 少 229,374 股。
2. 「推測缺少盤後定價」與「盤中即時累計量寫入」兩個推測都與證據不合。
   - 用（官方成交金額 − 本地成交金額）÷ 缺少股數，可以算出缺少那部分成交的均價。這個均價常常明顯偏離收盤價：2454 在 9/22 為 5,302.8，收盤 5,180；2330 在 9/22 為 2,494.8，收盤 2,460；9/23 的 1109 為 12.15，收盤 13.35；2882 為 123.0，收盤 111.5；00919 為 28.95，收盤 32.06。
   - 一般認知是盤後定價交易以當日收盤價成交（本 session 的 WebSearch 額度已用完，未能線上查證，標「推測」）。均價偏離收盤價，表示缺少的部分不可能全是盤後定價，比較像官方附註中也計入的鉅額交易。只有少數案例的均價剛好等於收盤價，例如 6196 在 9/17 為 516、3034 在 9/17 為 545。
   - taiwan/daily 的資料來源是 MI_INDEX（snapshot_provider.py:129），quote_ts 為 null，不是即時累計量。
   - 有差異的日期，對應的分區都是當天 16:19–17:03 寫入。9/21 分區在 9/22 10:55 補寫，9/15 以前的分區在 9/16 11:21 寫入，兩者都與官方一致。因此比較可能的原因是：當日抓 MI_INDEX 時，官方統計還沒納入部分成交（推測是鉅額交易），之後也沒有重抓覆蓋。這些分區到今天仍然偏少，不會自動修正。
3. 「ETF 沒有差異」只適用於抽樣的 4 檔 ETF。9/23 全市場比對中，00919 少 2,000,000 股（−3.41%），00918 少 500,000 股（−1.25%）。
4. 「量比有數個百分點偏差」低估了幅度。
   - 自選股中最大偏差是 6196 在 9/17 的 −16.95%；2330 在 9/18 為 −8.45%。
   - 分母是前 5 日均量，所以單日量偏少的影響會延續 5 天。例如 6196 在 9/18–9/23 的量比都比官方口徑高 2.4%–2.8%。
   - 9/23 全市場有 23/1330 檔 TWSE 證券的量偏少，合計少 12,235,374 股。個股最多少了 99.2%：6464 本地 2,300 股，官方 302,300 股。
   - 只把 9/23 當天的量換成官方值、分母沿用本地資料估算，就有 4 檔翻轉 0.8 門檻（1109、1227、6464、9940），3 檔翻轉 1.2 門檻。例如 6464 的量比由 0.173 變成 22.75。
   - 這些股票都不在自選清單，所以對使用者自選股的影響仍是沒有翻轉。但全市場選股結果確實會變。

嚴重度：以「對策略的影響」這個範圍與使用者情境（自選股 0 翻轉、監控規則 0 條）來看，維持 P3。不過資料層的問題範圍比 finding 描述的大：部分交易日的量長期偏少，而且與隔日補抓的日期口徑不一致。這部分應交給資料層另行評估，可能達到 P2。

附帶觀察（不在本 finding 範圍，影響未評估）：
- data\kline_daily 的 33 筆，都等於官方前一交易日的量，看起來日期標錯了一天。
- taiwan/daily 的 9/16 與 9/23 分區只有 1330 筆 TWSE 資料，沒有 TPEX 資料。
    - 證據：
      - 腳本與輸出都在 AUDIT_SCRATCH/work/verify-vol0916/（cmp.py、vr.py、implied.py、mi923.py、flip923.py、kdshift.py、merged.csv），執行方式為 cd backend && uv run python <腳本>
      - cmp.py：10 檔、380 筆（8/3–9/23）中本地與官方 STOCK_DAY 不同的有 16 筆，全是本地偏少。2330 在 9/16、9/17、9/18、9/22、9/23 分別少 44,000、597,000、3,702,177、1,118,000、839,000 股；2454 在同 5 天分別少 249,000、19,000、434,469、68,000、5,000 股；3034 在 9/17、9/18 少 33,000、29,388 股；2379 在 9/18 少 32,000 股；3545 在 9/18 少 6,000 股；6196 在 9/17、9/18 少 165,000、3,000 股。9/21 與 9/15 以前沒有差異；ETF 0050、00878、00885、00893 沒有差異；收盤價沒有任何差異
      - cmp.py：enriched 的 volume 與 taiwan/daily 逐筆相同，差額欄位完全一樣（en_diff == tw_diff）
      - vr.py：儲存的 vol_ratio_5d 與依 backend/app/indicators/pipeline.py:436（volume.shift(1).rolling_mean(5)）與 :490（volume / _vol_ma5_prev）重算的值，最大誤差為 0.0
      - vr.py：6196 在 9/17 為 0.5723 對官方 0.6891（−16.95%）；2454 在 9/18 為 1.5142 對 1.5676（−3.41%）；2330 在 9/18 為 1.9317 對 2.1100（−8.45%）；6196 在 9/18–9/23 比官方高 2.42%–2.76%（分母延續效應）。10 檔在 9/16–9/23 以 0.8 與 1.2 門檻檢查，翻轉 0 筆
      - implied.py，計算缺少部分的成交均價（官方金額 − 本地金額）÷ 缺少股數：2330 在 9/22 為 2,494.787，收盤 2,460；2454 在 9/22 為 5,302.8，收盤 5,180；2454 在 9/23 為 5,018.13，收盤 5,185；2454 在 9/17 為 4,598.3，收盤 4,500。6196 在 9/17 為 516.0、3034 在 9/17 為 545.0，都等於收盤價
      - 官方附註（reference\raw\twse_stock_day_2330_202609.json 與 twse_mi_index_20260923.json 的 notes）：「當日統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。」
      - mi923.py：9/23 本地 taiwan/daily 有 1330 筆，全部是 .TWSE（0 筆 .TPEX）。與 MI_INDEX 比對，23 檔不同，全是本地偏少，其中 22 檔差額是整張，合計少 12,235,374 股、3,926,807,712 元。例如 6464 為 2,300 對 302,300（−99.2%），1109 為 61,788 對 572,788，00919 少 2,000,000 股（均價 28.95，收盤 32.06），2882 少 500,000 股（均價 123.0，收盤 111.5）
      - flip923.py：9/23 的 23 檔只把當日量換成官方值（分母沿用本地），就有 0.8 門檻翻轉 4 檔（1109、1227、6464、9940）、1.2 門檻翻轉 3 檔。例如 6464 的量比由 0.173 變成 22.75
      - 分區寫入時間（ls -la）：taiwan/daily 的 9/16 為 9/16 16:30、9/17 為 9/17 16:19、9/18 為 9/18 17:03、9/22 為 9/22 16:30、9/23 為 9/23 16:30，這些都有差異；9/21 在 9/22 10:55 寫入、9/10–9/15 在 9/16 11:21 寫入，都沒有差異
      - 資料來源是 MI_INDEX：backend/app/taiwan/providers/snapshot_provider.py:129（type=ALL），:178–201 直接取成交股數，quote_ts 設為 None；本地 taiwan/daily 的 quote_ts 全部為 null，不是即時累計量
      - kdshift.py（附帶觀察）：data\kline_daily 的 33 筆全部等於官方前一交易日的量，與同日官方量相等的有 0 筆
      - 無法驗證：finding 引用的 https://histock.tw/stock/3034；本 session 的 WebSearch 額度已用盡，也無法線上查證盤後定價以收盤價成交、鉅額交易時段

## 確認正確的項目

- **官方行情資料 vs app 原始價（raw_close/raw_high/raw_low）**：W\s2_data.py：官方 STOCK_DAY 10 檔（含 9 檔自選股與 2330）共 38 日，與 enriched 的 raw_close/raw_high/raw_low，以及 taiwan/daily 的 open/close 逐日比對
  - 來源：官方參考資料 csv/twse_stock_day_202608_202609.csv（https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?response=json&date=20260801&stockNo=2454 等）
  - 來源：輸出：10 檔的 d_close、d_high、d_low 皆為 0.0，沒有缺漏
- **change_pct（小數制）、momentum_5d/20d/60d**：W\s7_raw.py：以官方未還原價（8 月前接 taiwan/daily）用獨立的 numpy 公式計算 c/c[-n]-1，與 app 的 compute_indicators 輸出比較，共 9 檔×38 日
  - 來源：輸出：change_pct、momentum_5d、momentum_20d、momentum_60d 的 max_abs 皆為 0
  - 來源：backend/app/indicators/pipeline.py:493-504
- **ma5/ma10/ma20/ma60（簡單移動平均；台灣慣例 MA20＝月線、MA60＝季線）**：W\s7_raw.py 獨立計算 SMA 後比對；另上網查台灣均線命名慣例
  - 來源：輸出：max_abs 為 2.8e-14 到 4.3e-14
  - 來源：https://www.cmoney.tw/learn/course/technicals/topic/485（搜尋摘要：「20MA則是月均線…季線就是60MA」）
  - 來源：backend/app/indicators/pipeline.py:391-400
- **low_60d（含當日的 60 日最低收盤價）**：W\s7_raw.py 獨立計算 rolling min(close,60) 後比對
  - 來源：輸出：max_abs 為 0
  - 來源：backend/app/indicators/pipeline.py:439-440（以收盤價計算，標籤寫「60日最低价」，見 :163；上游專案刻意改採收盤口徑，見 https://github.com/shy3130/tick-stock-panel/issues/329）
- **rsi_14（Wilder 平滑法，超賣門檻 30）**：W\s7_raw.py：獨立以 Wilder 法計算（前 14 期簡單平均作起始值，之後 (prev×13+cur)/14），使用官方未還原價，與 app 比對；另把同一獨立演算法套在 app 自己的 close 上比對
  - 來源：https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi：「First Average Gain = Sum of Gains over the past 14 periods / 14 … Average Gain = [(previous Average Gain) x 13 + current Gain] / 14」「below 30, it indicates oversold」
  - 來源：輸出：與官方未還原價比 max_abs 5.26e-05；套在 app close 上 max_abs 2.8e-14
  - 來源：backend/app/indicators/pipeline.py:529-549
- **vol_ratio_5d 公式（當日量 ÷ 前 5 日均量，不含當日）**：W\s7_raw.py 獨立計算；比對 A 股量比定義在全日收盤後的等價形式
  - 來源：https://wiki.mbalib.com/zh-tw/%E9%87%8F%E6%AF%94（搜尋摘要：「量比＝現成交總手/[(過去5個交易日平均每分鐘成交量)*當日累計開市時間(分)]」；收盤後等於當日量 ÷ 前 5 日均量）
  - 來源：輸出：有差異的日期只落在 9/16–9/23，差異來自成交量資料本身，公式一致（見 finding『近期成交量與官方不一致』）
  - 來源：backend/app/indicators/pipeline.py:434-491
- **矩陣路徑與面板路徑計算同一特徵的一致性（ma*、change_pct、momentum*、low_60d、vol_ratio_5d、rsi_14）**：W\s13_matrix_vs_panel.py：以 field_columns=set() 強制由 matrix_feature 自行計算，與 compute_indicators 比對 2025-06-02 之後約 72 萬個點
  - 來源：輸出：相對誤差最大 4.8e-4（float32 精度）；rsi_14 max_abs 0.00124；finite_mismatch 為 0
  - 來源：backend/app/backtest/matrix.py:3805-3963
- **7 支策略的進場邏輯（bottom_reversal、n_day_low_reversal、oversold_bounce、oversold_reversal、pullback_ma20_bounce、pullback_resume、pullback_to_support）**：W\indep.py 依各檔 META 預設參數獨立重寫每支策略的條件；W\s7_raw.py 用官方未還原價，與 app 的 MatrixStrategyPipeline（含 DEFAULT_BASIC_FILTER）比對 9 檔×38 日
  - 來源：輸出：共 342 個「股票×日」，只有 1 個不同：pullback_to_support 00885 在 2026-09-16。原因是當天成交金額 12,832,447 < amount_min 2e7（backend/app/strategy/engine.py:47），屬於基本過濾，不是邏輯錯誤
  - 來源：命中比對：pullback_resume 2454 在 9/18 兩邊皆命中；pullback_ma20_bounce 兩邊各 7 筆；pullback_to_support 獨立版 16 筆、app 15 筆
- **app 實際產出（strategy_cache.json 2026-09-23）的重現**：W\s4_app.py＋s5_compare_cache.py：以 compute_indicators→build_market_data_matrix→MatrixStrategyPipeline（DEFAULT_BASIC_FILTER）重跑全市場
  - 來源：輸出：7 支策略的命中名單完全一致（pullback_ma20_bounce 17 檔、pullback_to_support 43 檔，其餘 0 檔），分數最大差 0.0
- **min-max 評分公式、權重正規化、0–100 分尺度、單一候選得 50 分**：W\indep.py minmax_score 獨立實作（各欄在候選中做 min-max 正規化 → 權重除以總權重 → ×100），W\s9_experiments.py E6 與 app 的 pipeline 分數逐筆比對全部歷史進場
  - 來源：輸出 max_abs：bottom_reversal 1.2e-5（207 筆）、n_day_low 1.2e-5（1041 筆）、oversold_bounce 2.9e-5、oversold_reversal 4.5e-5、pullback_ma20_bounce 1.3e-5（16952 筆）、pullback_resume 5.8e-5、pullback_to_support 5.8e-6（28068 筆）
  - 來源：backend/app/backtest/matrix.py:3676-3742；backend/app/strategy/engine.py:1495-1531
- **評分方向機制（scoring_directions 設為 low 時做 1−x 翻轉）**：讀程式碼：矩陣路徑只對有限值做翻轉，polars 路徑也有對應處理
  - 來源：backend/app/backtest/matrix.py:3736-3737
  - 來源：backend/app/strategy/engine.py:1520-1522
  - 來源：backend/app/strategy/scoring.py:82-90
- **scoring.py 的 36 個虛擬評分欄位（polars 路徑 vs 矩陣路徑）**：W\s15_virtual.py：兩條路徑計算約 71 萬個點後比對；另用獨立 numpy 公式在 2454 的 2026-09-23 抽查 10 個欄位
  - 來源：輸出：均線乖離、報酬、成交量、位置類欄位 max_abs ≤ 1.4e-4；抽查的 max_ret_20d、up_days_20d、amihud_20d、ret_skew_20d(bias=True)、vol_price_corr_20d、vwap_bias、vol_ratio_10d、vol_trend_5_60、close_position、boll_position 與獨立值差 ≤ 3.8e-15
  - 來源：注意：ema*/macd*_pct/atr_pct 的差異來自 EMA 起始值（見 finding『暖機不足』）；ret_skew_20d 在接近常數的視窗 max_abs 2.18（p99 只有 1.6e-5），屬於數值不穩定的極端情況
- **oversold_bounce、oversold_reversal 的門檻與單位**：讀程式碼並與 Wilder 定義比對；change_pct 為小數制，min_change 以百分數輸入後除以 100
  - 來源：backend/app/strategy/builtin/oversold_bounce.py:68-75
  - 來源：backend/app/strategy/builtin/oversold_reversal.py:73-80
  - 來源：enriched change_pct 實值：0050 在 2026-09-23 為 0.005364（小數制）
- **pullback_ma20_bounce 的月線 ±2% 與多頭排列；pullback_to_support 的縮量（vol_ratio<0.8）**：讀程式碼並比對台灣慣例；兩支策略同名參數 ma_proximity 的單位不同（前者為 %、除以 100；後者為小數），但各自換算正確，UI 範圍 0.01–0.05 可防誤填
  - 來源：backend/app/strategy/builtin/pullback_ma20_bounce.py:67-75
  - 來源：backend/app/strategy/builtin/pullback_to_support.py:80-92
  - 來源：https://murmurcats.com/trading-volume/：「價跌量縮：股價下跌但成交量縮小，賣壓減輕」
- **pullback_resume 的視窗語意（回檔窗、守 MA20、量縮、站回 MA5、突破前高）**：W\indep.py 獨立重寫並逐項核對 shift/rolling 的對齊方式
  - 來源：backend/app/strategy/builtin/pullback_resume.py:94-121
  - 來源：獨立版與 app 在 342 個「股票×日」中 0 個不同
- **bottom_reversal 的各個組成條件（距 20 日高回落 ≥15%、連跌 3 日或 5 日跌 ≥8%、止跌 K 型態、貼近 20 日低點、隔日收盤突破止跌 K 高點）**：W\indep.py 獨立重寫；型態條件與錘子線定義比對
  - 來源：backend/app/strategy/builtin/bottom_reversal.py:100-149
  - 來源：https://www.cmcmarkets.com/en-gb/technical-analysis/hammer-candlestick-patterns：「In a valid hammer, this lower shadow should be at least twice the length of the body」。app 的 long_lower_wick 是「下影線 ≥ 全幅 50%」，比錘子線寬鬆，但程式命名為下影線，不是錘子線，因此不列為 finding
- **composite 的 union/intersect 進場合成與出場來源投影（不看未來資料）**：讀程式碼：選股與回測的 effective_min 算法相同；_hold_masks_from_entries 用 np.roll 後把頂列歸零，不會把未來資料帶進過去
  - 來源：backend/app/strategy/composite.py:103-109
  - 來源：backend/app/strategy/composite.py:132-154
  - 來源：backend/app/strategy/composite.py:184-199
- **矩陣策略結果的排序與筆數上限（依 score 降序、limit 500）**：經由重現 strategy_cache.json 驗證（名單與分數完全一致）
  - 來源：backend/app/strategy/engine.py:1250-1264

## 無法驗證的項目

- **第三方網站上的 RSI、MA 等技術指標數值（三方比對）**：HiStock（https://histock.tw/stock/3034）與 Yahoo 奇摩（https://tw.stock.yahoo.com/quote/3034.TW/technical-analysis）的指標由前端 JavaScript 動態產生，靜態內容裡沒有數值；Goodinfo（ShowK_Chart.asp）回傳 HTTP 403。只能改用官方原始價獨立重算
- **2026-06-01 以前除權息事件的還原因子**：官方 TWT49U 參考資料只涵蓋 2026-06-01 到 09-23，所以「還原價 vs 未還原價」對策略的全市場影響只量化了 2026-09-01 到 09-23 的進場（60 日視窗大致落在 6 月以後）
- **pullback_to_support 評分幾乎全為 0 分，對實際回測選股與成交的影響**：跑回測服務會寫入 backtest_results，違反唯讀限制，所以沒有執行；只證實了矩陣 score 為 0 的比例與平手的機制，回測結果的變化屬推測
- **反轉策略評分方向與未來報酬的關係能否推廣**：統計只是樣本內（2024–2026 台股），使用未還原價、以 5 日報酬計算，樣本 207 到 1736 筆；只能當作輔助證據，主要依據是程式碼與 Connors 的研究
- **盤中即時策略的量比（用當日部分成交量）**：只有監控規則才會在盤中跑策略，使用者監控規則為 0 條；選股的基準日是官方最後交易日（backend/app/services/screener.py:415-426），所以沒有實測
- **composite 在資金受限的組合模式下，出場投影是否會平掉其他子策略的倉位**：需要執行回測撮合，因唯讀限制沒有模擬；只做了程式碼閱讀
