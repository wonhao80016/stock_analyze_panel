# A05-limits-signals｜漲跌停、檔位與所有 signal_* 訊號

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 7 | 8 | 0 | 0 | 0 | 11 | 9 |

## 稽核者摘要

結論：正確的部分是 PriceLimitModel/TickSizeModel 的「10%＋漲停向下取、跌停向上取到檔位」規則，以及 enriched_refresh 寫入的 signal_limit_up/down/broken/recovery（非除權息日），我拿官方資料逐筆比對，全部吻合。比對範圍：9/23 MIS u/w 5 檔、櫃買 9/24 官方次日漲跌停 891 檔、TWT49U 除息日 809 筆，以及 9/23 上市 1,150 檔。9/23 官方股票漲停 15 檔，app 也是 15 檔。鉅亨網顯示 2454 的漲停 5695、跌停 4665，也一致。均線、MACD 交叉、突破、60 日新高低、放量這些 EOD 訊號，我用獨立實作比對自選 9 檔共 60 個交易日，540 列 0 差異。截斷測試也確認沒有用到未來資料（4 個日期，0 差異）。

有問題的部分，主要根因是另一組函式 polars_limit_price／numpy_limit_price 用 A 股「四捨五入到 0.01 元」計算台股漲跌停價，沒有依檔位取整。影響：(1) 回測的漲跌停鎖定判斷，9/23 官方 15 檔漲停只偵測到 2 檔（P1）；(2) 已儲存的連續漲停／跌停天數，9/23 的 15 檔漲停中有 13 檔記為 0。

其他問題：
- 除權息日以前一日收盤價當基準：官方除息日基準 809 筆，app 用前收算出的漲跌停價有 883/884 筆不同。9/23 的 1442、2542 被誤判為跌停，app 的股票跌停家數 3 檔，官方 1 檔。
- 新上市前 5 日仍套 10%：已產生 29 筆錯誤的漲停／跌停訊號。
- ETF 分類：櫃買 4 檔國外成分 ETF 被誤判為有 10% 漲跌幅；另有 75 檔 ETF 分類被拒並拋出 ValueError，導致搜尋「台灣」與個股詳細頁回應 500。
- 選股器「逼近漲停」永遠 0 檔。
- 「跌停翻紅」標籤與定義不符。
- 盤中與盤後兩條路徑的 60 日新高低、布林定義不一致。
- 分時圖與報價面板的漲跌停顯示錯誤。

網路請求共 23 次，沒有連線證交所或櫃買中心。repo 與 data 都沒有被修改：git status 仍是原本的 3 個檔案，security_master 的 mtime 沒變，也沒有新的 pyc。產出與腳本放在 AUDIT_SCRATCH/work/A05/（*.py、*.out、tpex_0924_limits.csv、twse_etf_class.csv、twt49u_exday_limits.csv、exday_signal_gap.csv）。

## 問題清單

### [P2 → P2] 新上市普通股前 5 個交易日仍套 10% 漲跌幅，產生假漲停與假跌停（確認成立）

- **主張**：MarketProfileBridge.get_price_limit_pct 對 instrument_type=='stock' 一律回傳 0.10，沒有處理初次上市首 5 個交易日無漲跌幅
- **正確的定義或數值**：上市日起 5 個交易日為 NO_LIMIT（漲跌停類訊號為 False/null、不顯示漲跌停價、回測不鎖定）
- **app 實際行為**：一律當作 10%
- **對使用者的影響**：新股蜜月期會出現大量「漲停」「跌停」訊號；個股詳細頁與報價面板會顯示不存在的漲跌停價；回測會在新股上市期間錯誤鎖定
- **修法建議**：依 security master 的 listing_date，並排除上櫃轉上市（需有來源欄位），計算上市後第 1～5 個交易日為 NO_LIMIT；PriceLimitModel 需要接受 trade_date
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/universe/models.py:133-134
  - backend/app/taiwan/market_rules.py:37（NO_LIMIT 的註解寫了 IPO 5 days，但實際沒有路徑會產生）
- **線上證據**：
  - https://efrontrade.com/2026/06/limit-up-limit-down-price-taiwan-stock.html：「初次上市普通股原則上首 5 個交易日無升降幅度限制（上櫃轉上市等情境不適用）」
  - 搜尋結果標題：TWSE「初次上市股票首五個交易日無漲跌幅限制 －自94年3月1日實施－」（https://www.twse.com.tw/downloads/zh/trading/introduce/introduce001.pdf，依規定未連線）
- **本地計算證據**：
  - taiwan/daily 中 96 檔上市新股的第 2～5 個交易日：29 根 K 棒漲跌幅超過 10%（例 6831 邁科 11/26 +28.6%、1623 大東電 1/23 +15.3%），可實證首 5 日沒有漲跌幅
  - 把這 384 根 K 棒餵給 app 的 _add_limit_signals：signal_limit_up 19、signal_limit_down 10、broken 4、recovery 7，全部都是錯誤訊號（目前有訊號欄的分區中沒有新股，但全量重建或日後有新股上市就會發生）
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：MarketProfileBridge.get_price_limit_pct（models.py:133-134）對普通股一律回傳 0.10，而且沒有交易日參數。依證交所營業細則第 63 條第 2 項，初次上市普通股（上櫃轉上市除外）自上市買賣日起 5 個交易日無升降幅度限制；本地資料顯示上櫃新股在同期間也超過 10%，對照組第 6～10 日則沒有。程式沒有處理這段期間，造成以下問題：
- enriched 的漲跌停、炸板、跌停翹板訊號出錯；
- 距漲跌停欄位、screener、全市場漲跌停統計、回測鎖定、個股詳細頁與盤中報價的漲跌停價都會出錯。

這不只是日後重建才會發生：現存 enriched 分區已含 7825.TPEX 和亞智慧 9/11、9/14、9/15 三個錯誤訊號，而且影響範圍涵蓋上市與上櫃新股。使用者的自選與監控不受直接影響，嚴重度維持 P2。
    - 理由：這條 finding 的核心主張成立，嚴重度維持 P2，但有兩處要修正。

一、程式碼與規則
- 程式碼：MarketProfileBridge.get_price_limit_pct 只收 instrument、不收交易日；對 instrument_type=="stock" 一律回傳 0.10（models.py:133-134）。market_rules.py 有三處註解提到 IPO 5 日屬 NO_LIMIT，但沒有任何呼叫路徑會讓普通股得到 None。
- 所有算漲跌停的路徑都經過這個函式：enriched 訊號、taiwan_limit_profile（距漲跌停欄位與 compute_limit_signals）、個股詳細頁、盤中 API、screener、market_intelligence。
- 我另找的權威來源是證券暨期貨法令判解查詢系統（selaw）收錄的證交所營業細則第 63 條第 2 項（114/12/24 版）。原文：「初次上市普通股除上櫃轉上市者外，自上市買賣日起五個交易日採無升降幅度限制」。這與 finding 引用的規則一致，也證實要排除上櫃轉上市。

二、本地實證（逐一重算，數字與 finding 相符）
- 上市新股 97 檔：第 2～5 日 388 根 K 棒中，29 根漲跌幅超過 10%。對照組第 6～10 日 485 根，0 根超過 10%。
- 把這 388 根餵給 app 的 _add_limit_signals：漲停 19、跌停 10、炸板 4、跌停翹板 7，與 finding 一字不差。
- 所有新股的 get_price_limit_pct 都回傳 0.1。

三、要修正的兩處
1. finding 說「目前有訊號欄的分區中沒有新股」，這是錯的。錯誤訊號已經寫進使用者現有資料：7825.TPEX 和亞智慧 2026-09-10 上櫃，在現存 enriched 分區中有三個錯誤訊號。
   - 9/11 被標為跌停翹板：最低價 -10.32%。
   - 9/14 被標為跌停：收盤 -14.05%，這個跌幅在 10% 限制下根本不可能出現。
   - 9/15 被標為跌停：收盤 -12.34%。
   另外，9/17 的炸板訊號與 IPO 規則無關。它的成因是 daily 缺了 9/16 這根 K 棒：9/16 分區只有 1330 檔，屬於另一個資料缺口問題。
2. 影響範圍不只上市。上櫃新股 95 檔：第 2～5 日有 31 根超過 10%，第 6～10 日 0 根。重播得到的錯誤訊號為漲停 24、跌停 11、炸板 13、翹板 17。上櫃端的官方條文網頁需要會員登入，抓不到原文；「上櫃同樣首 5 日無漲跌幅」這點僅由本地實證強力支持。

四、嚴重度
使用者自選 9 檔都不是新股，監控規則 0 條，所以不會直接觸發告警或自選訊號。實際影響落在：
- 全市場漲跌停統計（regime_builder、market_intelligence）每月多出少量錯誤計數；
- screener 的漲跌停條件與距漲跌停欄位；
- 回測在新股前 5 日錯誤鎖定；
- 查看新股詳細頁或盤中報價時，會顯示不存在的漲跌停價。
規則層面雖然錯了，但範圍窄（約每月數檔、每檔 5 日），列為「明確功能錯誤」P2 恰當。

五、修法注意事項
security master 的 listing_date 對上櫃轉上市股票是上市日期。本地 daily 沒有任何代號同時以 .TWSE 與 .TPEX 出現，無法從 K 棒歷史辨識轉市，所以修法確實需要額外的來源欄位，finding 對此的提醒正確。
    - 證據：
      - backend/app/taiwan/universe/models.py:124 簽名 get_price_limit_pct(cls, instrument) 沒有交易日參數；:133-134 `if instrument.instrument_type == "stock": return 0.10`
      - backend/app/taiwan/market_rules.py:7、:37、:300 註解寫 NO_LIMIT 涵蓋「IPO 5 days / 5-day IPOs」，但 grep 找不到任何以 listing_date 或交易日決定漲跌幅的路徑
      - 呼叫點（全部不帶日期）：enriched_refresh.py:142（_limit_profile → _add_limit_signals :157）、indicators/pipeline.py:1940（taiwan_limit_profile，供 compute_limit_signals 與 add_limit_distance_columns 使用）、taiwan/detail_service.py:126、api/intraday.py:137/:179、taiwan/screener.py:353、taiwan/market_intelligence.py:428；回測以 signal_limit_up/down 鎖定：backtest/engine.py:1275-1282、backtest/matrix.py:595-596
      - 線上（與 finding 不同來源）：https://www.selaw.com.tw/Chinese/LawDetail/LawArticleReference?lawDataid=16438355 臺灣證券交易所營業細則第63條第2項（114/12/24）：「初次上市普通股除上櫃轉上市者外，自上市買賣日起五個交易日採無升降幅度限制，且價格以跌至一分為限。」
      - 線上：https://ec.ltn.com.tw/article/paper/7439 自由財經：「初次上市普通股票（不含上櫃轉上市，及其他非普通股之有價證券），自上市日起5個交易日，其股價升降幅度無漲跌幅限制」
      - 本地（uv run python scratchpad\audit\work\verify_ipo_limit\check.py，輸出見 out.txt）：daily 範圍 2024-01-02～2026-09-23。上市新股 97 檔：第 2～5 日 388 根中 |chg|>10% 有 29 根，第 6～10 日 485 根中 0 根。上櫃新股 95 檔：第 2～5 日 378 根中 31 根，第 6～10 日 468 根中 0 根
      - 本地：192 檔新股的 MarketProfileBridge.get_price_limit_pct 全部回傳 0.1。把第 2～5 日 K 棒餵給 TaiwanEnrichedRefreshService._add_limit_signals：TWSE 漲停 19／跌停 10／炸板 4／翹板 7；TPEX 漲停 24／跌停 11／炸板 13／翹板 17
      - 本地（check.py 與 check2.py）：現存 kline_daily_enriched 中有 10 個分區帶訊號欄（2026-09-10～09-23）。7825.TPEX 和亞智慧（listing_date 20260910）：9/11 翹板 true（最低價 -10.32%）、9/14 跌停 true（收盤 92.4，-14.05%）、9/15 跌停 true（收盤 81.0，-12.34%），證實錯誤訊號已落檔；finding 所稱「目前分區沒有新股」不成立
      - 本地（check2.py，輸出見 out2.txt）：7825.TPEX 缺 9/16 的 K 棒，且 2026-09-16 分區只有 1330 檔（全期平均約 2172 檔／日），所以 9/17 的炸板訊號源自資料缺口，不屬本 finding
      - 本地（check2.py）：daily 中沒有任何代號同時以 .TWSE 與 .TPEX 出現，無法從 K 棒歷史辨識上櫃轉上市，修法需要額外的來源欄位
      - 工作樹比對：稽核前後 git status 都是同樣 3 個既有修改（daily_update.py、pnpm-lock.yaml、vite.config.d.ts），未改動 repo
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：MarketProfileBridge.get_price_limit_pct（models.py:133-134）對所有 instrument_type=='stock' 一律回傳 0.10，而且沒有日期參數，因此初次上市與初次上櫃普通股的前 5 個交易日都被當成有 10% 漲跌幅。

- 上市：96 檔、384 根第 2～5 日 K 棒中，29 根 |漲跌幅|>10%，產生 limit_up 19、limit_down 10、broken 4、recovery 7 個訊號。40 根訊號 K 棒全部落在盤中價已證明當時無漲跌幅的 28 檔上，全部是錯誤訊號。
- 上櫃同樣受影響：94 檔、374 根，產生 limit_up 24、limit_down 9、broken 12、recovery 17 個訊號。
- 已經發生在目前的正式資料中：7825.TPEX 和亞智慧（上櫃日 2026-09-10）在 kline_daily_enriched 已寫入錯誤的 signal_limit_down（09-14、09-15）與 signal_limit_down_recovery（09-11）。
- 修正要點：不能只看 master 的 listing_date。創新板轉一般板（如 6873 泓德能源，listing_date=轉板日）與上櫃轉上市都必須排除；而本地 daily store 不保留下市或已轉出市場的舊代號，無法用歷史判斷這些情況，需要官方資料提供來源欄位。
    - 理由：我用本地 parquet 和 app 的純計算函式，從頭重算了一次，finding 的每個數字都完全吻合。

重現方法：用 security_master.parquet 的 listing_date，挑出上市日落在 2024-01-03～2026-09-23 之間的 stock。對這些股票的完整日 K 跑 compute_enriched（factors 與 instruments 都給空表）。接著用真正的 TaiwanEnrichedRefreshService._add_limit_signals 算漲跌停訊號；它的 _limit_profile 換成 stub，stub 內部仍然呼叫 MarketProfileBridge.get_price_limit_pct。上市後的第幾個交易日，依市場交易日曆計算。

數字對照（finding 的數字 → 我算出的數字）：
- 上市新股：96 → 96
- 第 2～5 日 K 棒：384 → 384
- |漲跌幅|>10%：29 → 29（漲 19、跌 10）
- signal_limit_up：19 → 19
- signal_limit_down：10 → 10
- broken：4 → 4
- recovery：7 → 7
- 6831 邁科 2025-11-26：+28.6% → 178.5→229.5，+0.2857
- 1623 大東電 2026-01-23：+15.3% → 295→340，+0.1525

程式碼部分也確認了：models.py:133-134 對 stock 無條件 return 0.10，函式簽名也沒有日期參數。market_rules.py:37 和 300 的註解都寫著 IPO 5 日為 NO_LIMIT，但沒有任何路徑會回傳它。

我另外補驗了「全部都是錯誤訊號」這一點。本地 daily store 只收錄目前 master 裡的代號（daily 2346 檔全部都在 master 裡），所以無法用舊 .TPEX 歷史判斷是否為上櫃轉上市（兩個市場都出現過的代號是 0 個）。因此我改用盤中價判斷：對每一檔，檢查第 2～5 日是否有任何一根 K 棒的 high 或 low，嚴格超出依檔位對齊後的 10% 漲跌停價。只要有，就證明這檔當時沒有漲跌幅限制。結果 40 根上市訊號 K 棒全部落在這種已證實的 28 檔上，所以這些訊號全部錯誤，不會混入上櫃轉上市的合法訊號。

需要更正與補充的地方：
(1) finding 說「目前有訊號欄的分區中沒有新股」，這只對上市成立。kline_daily_enriched 目前有 10 個分區帶訊號欄（2026-09-10～09-23）。其中上櫃新股 7825.TPEX 和亞智慧（上櫃日 2026-09-10）已經寫入錯誤訊號：
- 9/11（上櫃第 2 日）signal_limit_down_recovery=True
- 9/14（第 3 日）signal_limit_down=True，收盤 -14.0%，最低 90.0，前收 107.5
- 9/15（第 4 日）signal_limit_down=True，收盤 -12.3%

所以錯誤訊號已經在目前的正式資料裡，不必等到全量重建。

(2) 同一條程式路徑也套用在上櫃股，上櫃同樣有初次上櫃前 5 日無漲跌幅的規定。上櫃新股共 94 檔、374 根 K 棒，其中 |漲跌幅|>10% 有 29 根（漲 22、跌 7）。訊號數：limit_up 24、limit_down 9、broken 12、recovery 17。其中 57／61 根訊號落在已證實無漲跌幅的 31 檔上；另外 6423 和 7712 兩檔的 4 根無法用盤中價證明。

(3) 96 檔裡有一檔不是新股：6873 泓德能源是從創新板轉到一般板。它從 2024-01-02 起就有連續的 .TWSE K 棒，master 的 listing_date=20240926 是轉板日期。這檔沒有產生任何訊號，所以訊號數字不受影響。但如果照 fix_suggestion 只看 listing_date 修正，就會把 6873 誤判成 5 日無漲跌幅。另一方面，6919 康霈*是真正的新股，因為上市日 2024-10-02 碰上颱風休市而被漏掉；它前 5 日沒有訊號，同樣不影響數字。

(4) 官方參考資料（TPEx 9/23 dailyQuotes）用 9999.95／0.01 表示「無漲跌幅」。當天所有 4 碼普通股都沒有這個標記，這與「9/24 沒有上櫃新股處於前 5 日」一致。7825 在該份資料中的 9/24 漲跌停價為 82.2／67.4，屬於第 11 個交易日以後的正常 10% 限制，也與規則一致。

線上規則已查證：pocket.tw 寫明「初次上市/櫃普通股前五日（不含上櫃轉上市者…）…目前無漲跌幅限制」。

使用者影響：使用者自選股中沒有新股，監控規則為 0 條，所以只有看到市場層級的訊號、選股結果，或打開新股的詳細頁時才會受影響。「回測會錯誤鎖定」這一點，我這次只確認 PriceLimitModel 沒有 IPO 路徑，沒有實跑回測，屬於推測。嚴重度維持 P2。
    - 證據：
      - backend/app/taiwan/universe/models.py:124-134：get_price_limit_pct(cls, instrument) 沒有日期參數，第 133-134 行 `if instrument.instrument_type == "stock": return 0.10`
      - backend/app/taiwan/market_rules.py:37 與 :300：註解寫 NO_LIMIT 涵蓋「IPO 5 days」和「5-day IPOs」，但 get_price_limit_pct 沒有任何分支會對 stock 回傳 None
      - backend/app/taiwan/enriched_refresh.py:130-186：_limit_profile／_add_limit_signals 依 get_price_limit_pct 產生 4 個訊號欄；pipeline.py:388 的 prev_close = close.shift(1).over("symbol")，所以上市第 1 日不會有訊號
      - 重現腳本：AUDIT_SCRATCH/work/verify-ipo5/repro.py（輸出在同目錄的 repro_out.txt 與 days2_5.csv）。執行方式：cd backend && uv run python repro.py
      - repro.py 的上市結果：symbols=96 bars=384；|ret|>10%: 29（ret>10%: 19、ret<-10%: 10）；signal_limit_up 19、signal_limit_down 10、signal_broken_limit_up 4、signal_limit_down_recovery 7。與 finding 完全相同
      - 6831.TWSE 2025-11-26：prev_close 178.5、close 229.5、ret 0.285714、signal_limit_up=True。1623.TWSE 2026-01-23：prev_close 295、close 340、ret 0.152542、signal_limit_up=True；1623.TWSE 2026-01-26 收盤 -13.1%，signal_limit_down=True
      - check4.py：上市 96 檔中有 28 檔，在第 2～5 日出現 high 或 low 嚴格超出依檔位對齊的 10% 漲跌停價（共 39 根），證明當時無漲跌幅；40 根訊號 K 棒全部落在這 28 檔上（signal bars 40 on proven symbols 40）
      - check3.py：kline_daily_enriched 共 661 個分區，其中 10 個有 signal_limit_up 欄（2026-09-10～2026-09-23）
      - 目前已寫入的錯誤訊號（data\kline_daily_enriched）：7825.TPEX 和亞智慧（上櫃日 2026-09-10）。2026-09-11 signal_limit_down_recovery=True；2026-09-14 prev_close 107.5、low 90.0、close 92.4，signal_limit_down=True；2026-09-15 prev_close 92.4、close 81.0，signal_limit_down=True
      - 上櫃補充（repro.py）：94 檔、374 根；|ret|>10%: 29（漲 22、跌 7）；signal_limit_up 24、limit_down 9、broken 12、recovery 17；其中 57/61 根訊號落在已證實無漲跌幅的 31 檔（check4.py），6423.TPEX、7712.TPEX 無法證明
      - 6873.TWSE 泓德能源：master listing_date=20240926，但 daily 在 2024-09-20～09-25 就有 .TWSE K 棒（例如 09-25 收 290.5），屬於創新板轉一般板，不是新股；它的第 2～5 日沒有任何訊號
      - check3.py：daily 中同時出現在 TWSE 和 TPEX 的代號為 0；daily 2346 個代號全部在目前的 master 裡（不在 master 的有 0 個），所以本地歷史無法辨識上櫃轉上市，修正方案需要額外的資料來源欄位
      - 官方參考資料 reference\csv\tpex_daily_quotes_20260923.csv：無漲跌幅的商品以 next_day_limit_up=9999.95、next_day_limit_down=0.01 標示（117 列），其中沒有任何 4 碼普通股；7825 和亞智慧的 next_day 漲跌停價為 82.2／67.4
      - https://www.pocket.tw/callcenter/questions/840/ 原文：「初次上市/櫃普通股前五日 (不含上櫃轉上市者及其它非股票之有價證券)、境外ETF及以其為發行標的之權證、興櫃股票，目前無漲跌幅限制。」
      - backend/app/taiwan/detail_service.py:126-128：詳細頁用同一個 get_price_limit_pct 產生 rule_type（新股會顯示「±10%」）；其他呼叫點包括 api\intraday.py:137,179、screener.py:353、market_intelligence.py:428、research_context.py:719、price_limits.py:172

### [P2 → P2] ETF 分類被拒時拋出的 ValueError 沒有被攔截：搜尋「台灣」、個股詳細頁、報價面板回應 500（確認成立）

- **主張**：上市 ETF 只要基金類型不在兩種一般類型或槓桿反向型之內（例如主動式 ETF），classification_source 就是 None；verify_confirmed_etf 因此拋出 ValueError，而搜尋、詳細頁、報價三個 API 都沒有捕捉
- **正確的定義或數值**：官方元資料已有「是否包含國外成分」時，應據此判定（國內 10%、國外無）；判定不了時應降級為「未知」並顯示，而不是讓整個 API 失敗
- **app 實際行為**：整個請求 500
- **對使用者的影響**：台股詳細頁與比較頁的搜尋框，搜「台灣」「主動」等常見字就會失敗；點開任一主動式 ETF（例 00981A）的詳細頁會 500；報價面板若含這類 ETF，整個面板會失敗。目前自選的 4 檔 ETF 不受影響
- **修法建議**：adapters.py 對所有基金類型都依「是否包含國外成分股」設為 official_metadata；三個 API 呼叫處包 try/except ValueError，改回傳 price_limit_pct=None 並標示「未確認」
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/universe/adapters.py:433,451（classification_source="official_metadata" if fund_type in ordinary_types or is_leveraged_type else None）
  - backend/app/taiwan/universe/models.py:93-102
  - backend/app/api/intraday.py:177-179（/taiwan/search，沒有 try）
  - backend/app/api/intraday.py:136-137（/quotes，沒有 try）
  - backend/app/taiwan/detail_service.py:126（沒有 try；api/taiwan.py:286-293 會轉成 500）
- **線上證據**：
  - 官方 openapi t187ap47_L（reference/csv/twse_t187ap47_L_etf_meta.csv）：00400A～00999A 等 22 檔「國內成分證券主動式交易所交易基金(股票)」的是否包含國外成分為「否」，依規則應為 10%
- **本地計算證據**：
  - 實際呼叫 MarketProfileBridge.get_price_limit_pct(00981A.TWSE)：拋出 ValueError「Refusing to apply regulatory market rules to unconfirmed ETF 00981A.TWSE (source: 'unknown')」
  - 上市 ETF 共 55 檔被拒（國內 22、國外 33），上櫃另有 20 檔
  - 用 security master 的 search(q, limit=10) 重現前端詳細頁與比較頁的搜尋：搜「台灣」結果含 00401A/00405A/00406A/00407A、搜「主動」10 筆全部會觸發、搜「野村」含 00980A/00985A/009812，這些查詢整個請求都會 500
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：1. 分類缺口：adapters.py:451 只在基金類型屬於兩種一般類型或槓桿／反向型時，才把 classification_source 設為 official_metadata，否則為 None，即使 :431 已從「是否包含國外成分股」算出 scope。受影響的上市 ETF 共 55 檔：國內主動股票 22、國外主動股票 8、期信 8、國外成份／加掛外幣 6、境外 3、連結式 3、國外主動債券 3、國外平衡型 2。
2. 例外沒被攔：上述 55 檔加上上櫃 21 檔（15 檔 unknown、6 檔 name_heuristic，不是 20 檔），共 76 檔 ETF 呼叫 MarketProfileBridge.get_price_limit_pct 時都會拋 ValueError。/api/intraday/taiwan/search（intraday.py:179）、/api/intraday/quotes（intraday.py:137）、個股詳細頁（detail_service.py:126，經 api/taiwan.py:288-293）這三處沒有攔截，整個請求回 500。其他 7 個呼叫點都有攔。
3. 搜尋受影響範圍（limit=10）：「台灣」「主動」「野村」「元大」「國泰」「富邦」「群益」都會失敗；「統一」在 limit=20 時也會失敗。
4. 正確規則：國內成分主動式 ETF 應為 10%，國外成分則無漲跌幅限制。
5. 在使用者情境下：自選的 9 檔不受影響，報價面板要等自選加入這類 ETF 才會失敗；但詳細頁與比較頁的搜尋框，輸入常見發行商名稱就會出錯。
6. 修正方式：只改 adapters.py 只能涵蓋上市的 55 檔。上櫃的 21 檔要靠三個 API 呼叫點攔截 ValueError，並降級為 price_limit_pct=None 且標示「未確認」。security_master.parquet 快取也要重建，分類修正才會生效。
    - 理由：主張成立，嚴重度 P2 恰當。在使用者情境下，自選的 9 檔不會觸發（實跑確認），但只要在搜尋框輸入常見字就會觸發。

一、程式碼（行號以現況為準）
1. adapters.py:433 只把兩種一般類型列入 ordinary_types，:441 另判槓桿／反向。:451 對其他所有基金類型都把 classification_source 設為 None，即使 :431 已從「是否包含國外成分股」算出 scope。
2. models.py:93-102 的 verify_confirmed_etf 看到來源不在 {official_metadata, cfi_code} 就拋 ValueError；get_price_limit_pct 在 :137 呼叫它。
3. 同一個 ValueError 有 7 個呼叫點會攔（price_limits.py:171-174、screener.py:352-354、research_context.py:718-721、market_intelligence.py:427-429、enriched_refresh.py:141-143、pipeline.py:1939-1942、detail_service.py 的 _aggregate_realtime），可見契約本來就要求呼叫端攔截。只有 3 處沒攔：
   - intraday.py:137（/quotes）
   - intraday.py:179（/taiwan/search）
   - detail_service.py:126
4. main.py:454 只註冊了 CapabilityDenied 的 handler，auth_middleware（:385-415）也不攔例外，所以 /quotes 與 /taiwan/search 的 ValueError 會冒成 500。detail 路徑則由 api/taiwan.py:286-293 轉成 HTTPException 500。

二、本地實跑（只讀 parquet，adapter 換成會拋錯的假物件以防連網）
- 55 檔上市 ETF 的 classification_source 為 null，筆數與官方 t187ap47_L 的「非兩種一般類型、非槓桿反向」55 檔完全一致：國內主動股票型 22；國外主動股票型 8；期信 8；國外成份／加掛外幣 6；境外 3；連結式 3；國外主動債券 3；國外平衡型 2。
- get_price_limit_pct 拋錯的共有：上市 55 檔、上櫃 21 檔（finding 寫 20，實為 21 = 15 檔 unknown + 6 檔 name_heuristic）。
- 00981A.TWSE 的錯誤訊息與 finding 所述逐字相同。
- 直接呼叫路由函式 search_taiwan_instruments(q, limit=10)：
  - 「台灣」「主動」「野村」都拋 ValueError；
  - 「台積電」「0050」正常。

三、定義（用 finding 以外的來源查證）
- 券商口袋證券 FAQ：「境內ETF同國內股票，有 10% 漲跌幅的限制，境外ETF則無漲跌幅限制」。
- 經濟日報報導證交所修法：
  - 「以國內主動式ETF為標的之權證，其每日升降幅度計算方式比照國內個股及被動式ETF」；
  - 以國外成分主動式 ETF 為標的的權證則「無升降幅度限制」。
- SITCA：主動式 ETF「交易方式皆比照被動式ETF」。
- 結論：國內成分主動式 ETF 應為 10%，國外成分無漲跌幅限制，與 finding 的預期一致。
- 限制：營業細則 §63 原文所在的法規網站要會員或已失效，沒能取得條文原文。

四、嚴重度
- 定為 P2：這是明確的功能錯誤。受影響的是搜尋框與 76 檔 ETF 的詳細頁；報價面板要等自選加入這類 ETF 才會出錯。
- 不到 P1：使用者目前的核心流程（自選 9 檔的報價與詳細頁）不受影響。

五、finding 沒提到的補充面向
1. 影響範圍比 finding 寫的更廣。limit=10 時，「元大」（0061）、「國泰」（00636K、00657K）、「富邦」（00625K）、「群益」（00643K）等發行商名稱也會觸發 500；limit=20 時「統一」也會。原因是期信、連結式這類上市老 ETF 同樣被拒，不只主動式。
2. finding 的修法只改 adapters.py:451，只能涵蓋上市的 55 檔。上櫃的 21 檔走 ISIN/CFI 分類路徑（adapters.py:152-187），仍需靠 API 端攔截。
3. security master 平時從 data/taiwan/security_master.parquet 快取載入，只有快取不存在時才重抓（service.py:134-144），目前快取時間為 2026-09-17。所以即使修正分類，也要重建快取才會生效。
    - 證據：
      - backend/app/taiwan/universe/adapters.py:451：classification_source="official_metadata" if (fund_type in ordinary_types or is_leveraged_type) else None。ordinary_types 定義在 :433，scope 已在 :431 由「是否包含國外成分股」算出。
      - backend/app/taiwan/universe/models.py:97-102：來源不在 CONFIRMED_SOURCES 就 raise ValueError；get_price_limit_pct 在 :137 呼叫。
      - backend/app/api/intraday.py:136-137（/quotes）與 :178-179（/taiwan/search）都沒有 try。
      - backend/app/taiwan/detail_service.py:126 沒有 try；backend/app/api/taiwan.py:286-293 以 except Exception 轉成 HTTPException(500)。
      - backend/app/main.py:454：只有 CapabilityDenied 的 exception_handler，沒有通用 ValueError handler。
      - 其他 7 個呼叫點都攔 ValueError，例如 backend/app/price_limits.py:171-174、backend/app/taiwan/screener.py:352-354、backend/app/indicators/pipeline.py:1939-1942。
      - 前端呼叫端：frontend/src/pages/TaiwanStockDetail.tsx:88 與 frontend/src/pages/TaiwanStockCompare.tsx:41 呼叫 api.taiwanSearch(q, 10)；frontend/src/components/monitor/TaiwanQuotePanel.tsx:194 以自選清單呼叫 taiwanQuotes。
      - 本地指令：cd backend && uv run python AUDIT_SCRATCH/work/verify_etf_valueerror/check.py。輸出：etf rows 391；TWSE null=55、official_metadata=216；TPEX cfi_code=99、name_heuristic=6、unknown=15；ValueError counts {('TWSE','unconfirmed'): 55, ('TPEX','unconfirmed'): 21}；00981A ValueError: Refusing to apply regulatory market rules to unconfirmed ETF 00981A.TWSE (source: 'unknown')；自選 9 檔都沒拋錯（0050/00878=0.1，00885/00893=None）。
      - 同一支腳本的搜尋重現（limit=10）：台灣→00401A/00405A/00406A/00407A；主動→10 筆全數觸發；野村→009812/00980A/00985A；元大→0061；國泰→00636K/00657K；富邦→00625K；群益→00643K。統一在 limit=10 時不觸發，limit=20 時觸發 00403A/00981A/00988A/00411A.TPEX。
      - 本地指令：uv run python AUDIT_SCRATCH/work/verify_etf_valueerror/route.py，直接呼叫 search_taiwan_instruments。輸出：台灣／主動／野村 → RAISED ValueError；台積電、0050 → OK。
      - 官方參考 reference/csv/twse_t187ap47_L_etf_meta.csv（271 列）：國內成分證券主動式交易所交易基金(股票)／否 22 檔。被程式拒絕的基金類型合計 55 檔，與 parquet 的 null 筆數一致。
      - https://www.pocket.tw/callcenter/questions/102/ ：「境內ETF同國內股票，有 10% 漲跌幅的限制，境外ETF則無漲跌幅限制。」
      - https://money.udn.com/money/story/5607/9224308 ：「以國內主動式ETF為標的之權證，其每日升降幅度計算方式比照國內個股及被動式ETF」；「若以國外成分證券之主動式ETF為標的，其權證交易採無升降幅度限制」。
      - https://www.sitca.org.tw/ROC/SITCA_ETF/ETF_ACMA/workshop.htm ：主動式 ETF「交易方式皆比照被動式ETF」。
      - https://winvest.tw/Knowledge/Article/345 ：「國外成分 ETF(含主動式)」無漲跌幅限制。
      - git status --porcelain 跑完與初始狀態相同（只有 daily_update.py、pnpm-lock.yaml、vite.config.d.ts 三個既有改動）；security_master.parquet 修改時間仍為 Sep 17 17:44，未被寫入。
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：上市 ETF 的基金類型只要不屬於兩種一般指數股票型、也不含槓桿或反向（主動式、期貨信託、境外、連結式等，共 55 檔：國內 22、國外 33），classification_source 就會被設成 None。上櫃另有 21 檔（finding 原寫 20），classification_source 是 unknown 或 name_heuristic。這 76 檔呼叫 get_price_limit_pct 時都會拋出 ValueError，而 /api/intraday/taiwan/search、/api/intraday/quotes、/api/taiwan/stocks/{symbol} 都沒有攔截，最後回 500。受影響的搜尋字不只「台灣」「主動」「野村」，還包括「元大」「國泰」「富邦」「00」這類常見字。目前自選 9 檔都不受影響。另外，只照 finding 建議修 adapters.py，只能解決上市的 55 檔；上櫃 21 檔的 fail-closed 是刻意設計，仍然要在 API 層攔截 ValueError，改成回傳「未確認」。
    - 理由：我用自己的腳本讀了現行 security_master 快取（先複製到 scratchpad，停用 httpx 網路，只呼叫純計算的 MarketProfileBridge.get_price_limit_pct），並拿官方 t187ap47_L 參考 CSV 交叉比對。finding 的核心說法全部重現，結論是 confirmed，只有一個附帶數字有出入。

逐項對照（finding 的數字／我算出的數字）：
- 上市 ETF 被拒的檔數：55／55。其中國內 22、國外 33，兩邊相同。這 55 個代號與官方 CSV 裡「基金類型不屬兩種一般類型、也不含槓桿或反向」的 55 檔完全一致，兩個方向的差集都是空的。
- 國內主動式股票 ETF：22 檔，官方「是否包含國外成分股」全部為「否」（00400A～00999A），相同。
- 00981A.TWSE：classification_source=None、underlying_scope=domestic，實際拋出 ValueError「Refusing to apply ... unconfirmed ETF 00981A.TWSE (source: 'unknown')」，與 finding 相同。
- 上櫃被拒的檔數：finding 說 20，我算出 21（15 筆 classification_source='unknown'、6 筆 'name_heuristic'，全部 active 且 is_supported=True）。這是唯一的數字差異。
- 搜尋（limit=10，與前端 TaiwanStockDetail.tsx:88、TaiwanStockCompare.tsx:41 的參數相同）：
  - 「台灣」：失敗的是 00401A/00405A/00406A/00407A，相同。
  - 「主動」：10 筆全部失敗，相同。
  - 「野村」：失敗的是 009812/00980A/00985A，相同。
  - 另外，「元大」（0061）、「國泰」（00636K、00657K）、「富邦」（00625K）、「00」也會失敗，影響比 finding 描述的更廣。
  - 「0050」「2330」「台積」正常。
- 自選 9 檔：全部正常。0050、00878 回傳 0.1；00885、00893 回傳 None（無漲跌幅限制）；5 檔個股回傳 0.1。finding 說自選 ETF 不受影響，這點成立。

程式路徑：
- 三個呼叫點都沒有 try，main.py 也只註冊了 CapabilityDenied 的 handler，所以 ValueError 會變成 500。
- 詳細頁的 ValueError 會被 api/taiwan.py 的 except Exception 攔下，但轉成 HTTPException 500。

嚴重度維持 P2：屬於明確的功能錯誤，搜尋與詳細頁在某些輸入下不能用，但用代號搜尋仍然正常，核心流程沒有完全失效。

補充一點：上櫃那 21 檔的 fail-closed 是刻意設計（adapters.py:367-372 的註解有寫），所以 finding 建議的 adapters.py 修法只解決上市的 55 檔。上櫃這 21 檔仍然要靠 API 層攔截 ValueError，才不會回 500。
    - 證據：
      - backend/app/taiwan/universe/adapters.py:433,441,451：ordinary_types 只有兩種一般類型；classification_source 只在 fund_type 屬於 ordinary_types 或 is_leveraged_type 時設為 official_metadata，否則為 None
      - backend/app/taiwan/universe/models.py:97-102：source 不在 {official_metadata, cfi_code} 時 raise ValueError；models.py:137 由 get_price_limit_pct 呼叫
      - backend/app/api/intraday.py:136-137（/api/intraday/quotes）、178-179（/api/intraday/taiwan/search）直接呼叫 get_price_limit_pct，沒有 try
      - backend/app/taiwan/detail_service.py:126 沒有 try；backend/app/api/taiwan.py:286-291 用 except Exception 轉成 status_code=500
      - backend/app/main.py:454：全域只有 CapabilityDenied 的 exception_handler，沒有處理 ValueError
      - 前端呼叫點：frontend/src/pages/TaiwanStockDetail.tsx:88 與 TaiwanStockCompare.tsx:41 呼叫 api.taiwanSearch(searchQuery, 10)；TaiwanQuotePanel.tsx:194 呼叫 api.taiwanQuotes(panelSymbols)
      - 腳本：AUDIT_SCRATCH/work/verify-etf500/repro.py；輸出：同目錄 out.txt（指令：cd backend && uv run python repro.py，exit=0）
      - 本地計算：security_master 快取共 2377 筆；ETF 依 classification_source 分組為 TWSE official_metadata 216 / null 55，TPEX cfi_code 99 / unknown 15 / name_heuristic 6
      - 本地計算：get_price_limit_pct 的結果為 TWSE ok 216 / unconfirmed 55，TPEX ok 99 / unconfirmed 21；上市被拒依基金類型分為國內主動股票 22、國外成份/加掛外幣 6、國外主動債券 3、國外主動股票 8、國外平衡 2、境外 3、期貨信託 8、連結式 3（國外合計 33）
      - 官方參考 reference\csv\twse_t187ap47_L_etf_meta.csv：共 271 筆；非一般類型且非槓桿／反向的有 55 筆，其中「國內成分證券主動式交易所交易基金(股票)」22 筆，includes_foreign_components 全部為「否」；這 55 個代號與快取中被拒的上市代號集合差集為空（兩個方向都是）
      - 本地重現：00981A.TWSE 拋出 ValueError「Refusing to apply regulatory market rules to unconfirmed ETF 00981A.TWSE (source: 'unknown')」
      - 本地重現搜尋（limit=10，依 API 邏輯逐筆呼叫 get_price_limit_pct）：「台灣」→ 00401A/00405A/00406A/00407A 失敗；「主動」→ 10 筆全部失敗；「野村」→ 009812/00980A/00985A 失敗；「元大」→ 0061 失敗；「國泰」→ 00636K/00657K 失敗；「富邦」→ 00625K 失敗；「0050」「2330」「台積」正常
      - 上櫃被拒 21 檔（finding 寫 20）：00411A, 00687C, 00694B, 00695B, 00696B, 00697B, 00768B, 00793B, 00858, 00864B, 00888, 00928, 00955, 009806, 009807, 00980T, 009814, 009815, 009823, 009825, 00998A（全部 .TPEX，active、is_supported=True）
      - 自選 9 檔：0050/00878 為 0.1，00885/00893 為 None，2454/3034/2379/6196/3545 為 0.1，都沒有拋錯

### [P2 → P2] 櫃買 CFI「CEOGEU」被當成國內成分，4 檔國外成分 ETF 被賦予 10% 漲跌幅（確認成立）

- **主張**：adapters.py 把 CFI 代碼 CEOGEU 直接對應成 domestic_equity／domestic，但 CFI 沒有地理屬性
- **正確的定義或數值**：這 4 檔應為 NO_LIMIT
- **app 實際行為**：報價面板、詳細頁、選股器會顯示不存在的漲跌停價與距漲跌停；漲跌停類訊號也可能誤觸
- **對使用者的影響**：查看這幾檔上櫃 ETF 時會看到錯誤的漲跌停資訊（目前不在自選中）
- **修法建議**：不要用 CFI 推論國別；上櫃 ETF 改用官方的國內／國外成分欄位，或用櫃買行情的 9999.95 哨兵值校正；推不出來就標為 unknown
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/universe/adapters.py:167-169
- **線上證據**：
  - https://www.iotafinance.com/en/Attributes-CFI-Codes-Group-CE.html（搜尋摘要：CEOGEU＝Open-end／Accumulation／Equities／Units，沒有國別欄位）
- **本地計算證據**：
  - 櫃買 9/23 官方 next_day_limit_up/down＝9999.95/0.01（無漲跌幅），app 卻判為 0.10 的有 00877 復華中國5G、00886 永豐美國科技、00887 永豐中國科技50大、009822 華南永昌未來金融；app 對 00877 以 10 元試算得 (11.0, 9.0)
  - 反方向（官方有漲跌幅、app 判為無）0 檔；官方有漲跌幅、app 拒判的有 00888、00928
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：adapters.py:167-169 把 ISO 10962 CFI 碼 CEOGEU 直接判成國內股票 ETF（underlying_scope=domestic），而 cfi_code 屬於 CONFIRMED_SOURCES，因此 MarketProfileBridge 會對它套用 10% 漲跌幅。CFI 的 CE 組屬性只包含基金型態、配息政策、資產類別與單位型態，沒有國別；CEOGEU 表示開放式、累積型、股票資產的 ETF，全球 ETF 也會用這個代碼。上櫃 ETF 的線上路徑沒有官方國內／國外成分欄位，只能靠 CFI，所以 5 檔 CEOGEU 中有 4 檔國外成分 ETF 被賦予 10%，官方 9/23 行情為無漲跌幅：00877 復華中國5G、00886 永豐美國科技、00887 永豐中國科技50大、009822 華南永昌未來金融。006201 是國內成分，判斷正確。這個問題來自 commit 679781ff（2026-09-17）。

影響修正：錯誤只出現在查看這 4 檔時的詳細頁（顯示 ±10% 與漲跌停價）、報價清單（算出漲跌停價），以及搜尋結果的 price_limit_pct。已存的台股日K目前沒有任何上櫃標的，所以選股器與 enriched 漲跌停訊號目前碰不到這 4 檔，finding 所說的選股器與訊號影響在現有資料下不會發生。使用者自選 9 檔都是上市標的，走官方元資料，不受影響。

修法方向：上櫃 ETF 不要從 CFI 推論國別，CEOGEU 分支改標 unknown，或改用官方國內／國外成分資料。
    - 理由：這條 finding 的核心主張成立，定為 P2 也恰當。不過它說選股器與漲跌停訊號會受影響，在目前的資料下不會發生，所以修正了影響描述。

1. 程式碼：adapters.py:167-169 把 CFI 碼 CEOGEU 直接判成 ("domestic_equity", "cfi_code", "domestic")。models.py:87-90 的 CONFIRMED_SOURCES 包含 "cfi_code"，所以 MarketProfileBridge.get_price_limit_pct（models.py:124-162）會把這筆當成已確認的國內 ETF，回傳 0.10。上櫃 ETF 的線上路徑（adapters.py:366-378）沒有傳入官方產品資料，一定走 CFI 分支。上市 ETF 改走 _official_twse_etf_directory，使用官方的「是否包含國外成分股」欄位（adapters.py:433），cfi_code 是 None，不受影響。這個問題來自 commit 679781ff（2026-09-17）：原本上櫃 ETF 一律標為 unknown，這次改成保留 CFI 分類。

2. 定義：CFI 的 CE 組屬性編的是基金型態（O）、配息政策（G 累積／I 配息／J 混合）、資產類別（E 股票、B 債券等）與單位型態（U），沒有國別。兩個獨立證據：
- TradingView 上 iShares Core MSCI World（全球已開發市場、累積型）的 CFI 也是 CEOGEU。
- 本地主檔中，CEOIEU 同時包含國內的 00888、00928 與國外的 00858、009806；CEOGEU 同時包含國內的 006201 與 4 檔國外 ETF。

分組依據是配息政策，不是國別。

3. 規則：國內成分 ETF 有 10% 漲跌幅，境外（國外成分）ETF 沒有，見 pocket.tw 券商 FAQ。發行商官網寫明 009822 是「國外成分證券指數股票型基金」。金管會 2026-07-06 的三階段新制只適用 ETF 期貨與選擇權，不影響 ETF 現貨。櫃買 9/23 官方行情中，這 4 檔的次日漲停價與跌停價都是 9999.95 與 0.01，代表無漲跌幅。

4. 重跑：用 polars 讀 security_master.parquet，再以 MarketProfileBridge 逐檔和官方行情比對，結果與 finding 完全一致：
- app 判有漲跌幅、官方為無漲跌幅：4 檔。
- 反方向：0 檔。
- app 丟 ValueError、官方有漲跌幅：00888、00928。
- 00877 用 10 元試算得 (11.0, 9.0)。

5. 嚴重度與使用者情境：
- 使用者自選 9 檔都是上市標的，classification_source 為 official_metadata 或 null，不受影響。
- 已存的 data/taiwan/daily 9/23 分區有 1330 列，全部是 .TWSE、上櫃 0 列；選股器（screener.py:207、299 讀 TaiwanDailyStore）與 enriched 漲跌停訊號目前都碰不到這 4 檔。finding 說「選股器會顯示、訊號可能誤觸」，在現有資料下不會發生。
- 實際會出錯的畫面：打開這 4 檔的詳細頁（detail_service.py:126-135，顯示「±10%」並算出漲跌停價），把它們加進報價清單（intraday.py:137-140），以及搜尋結果的 price_limit_pct（intraday.py:179）。

錯誤限於 120 檔上櫃 ETF 中的 4 檔，屬於明確的功能錯誤，維持 P2。

額外觀察（不屬本 finding，只讀程式碼、未實跑）：
- 官方無漲跌幅、app 卻丟 ValueError 的上櫃 ETF 有 18 檔。detail_service.py:126 與 intraday.py:137 呼叫 get_price_limit_pct 時沒有 try，推測打開這類 ETF 的詳細頁會回 500（api/taiwan.py:286-293 會把例外轉成 500）。
- docs/taiwan-data-sources.md:38-40 仍寫上櫃 ETF 分類為 data_insufficient，與現行程式碼矛盾。
    - 證據：
      - backend/app/taiwan/universe/adapters.py:167-169：`elif cfi_code == "CEOGEU": return "domestic_equity", "cfi_code", "domestic", multiplier`（git blame：fd79b63，2026-09-11）
      - backend/app/taiwan/universe/adapters.py:366-378：上櫃線上路徑呼叫 parse_isin_html(self.fetch_live_html(), "TPEX", "TPEX_ISIN")，沒有傳入 official_product_types，所以只能走 CFI 分支（commit 679781ff，2026-09-17，原本一律標 unknown）
      - backend/app/taiwan/universe/models.py:87-90：CONFIRMED_SOURCES = {"official_metadata", "cfi_code"}；models.py:152-160：scope 為 domestic 時回傳 0.10*abs(multiplier)
      - backend/app/taiwan/universe/adapters.py:433：上市 ETF 用官方「是否包含國外成分股」判斷國別，cfi_code=None（adapters.py:449），所以 CEOGEU 只影響上櫃
      - 本地重跑：`cd backend && uv run python scratchpad/audit/work/verify_cfi/check_master.py`，輸出：上櫃 ETF 共 120 檔；CEOGEU 5 檔全部標為 domestic_equity/cfi_code/domestic（006201、00877、00886、00887、009822）；app_limit_off_nolimit 4 檔 = 00877、00886、00887、009822（app 0.1，官方 9999.95/0.01）；app_nolimit_off_limit 0；app_error_off_limit 2 檔 = 00888（官方 39.76/32.54）、00928（36.77/30.09）；00877 calc_limits(10.0) = (11.0, 9.0)
      - 官方參考資料 reference/csv/tpex_daily_quotes_20260923.csv：00877 的次日漲停/跌停 = 9999.95/0.01；00886、00887、009822 也是 9999.95/0.01；006201（CEOGEU、國內）為 50.9/41.66，屬於正常的 10% 漲跌幅
      - CFI 不含國別（與 finding 不同的來源）：https://www.tradingview.com/symbols/XETR-EUNL/，頁面顯示 iShares Core MSCI World UCITS ETF 的 CFI Code: "CEOGEU"，追蹤 MSCI World，股利 "Capitalizes"（累積型）；本地主檔的 CEOIEU 同時含國內（00888、00928）與國外（00858、009806），顯示第 4 碼 G/I 是配息政策，不是國別
      - 漲跌幅規則：https://www.pocket.tw/callcenter/questions/102/ 原文「境內ETF同國內股票，有 10% 漲跌幅的限制，境外ETF則無漲跌幅限制。」
      - 009822 是國外成分：https://www.hnfunds.com.tw/pdf_file/009822/index.htm，發行商頁面寫明本產品為「國外成分證券指數股票型基金」，追蹤彭博全球未來金融優選30指數
      - 2026 新制只適用衍生品：https://www.ptt.cc/bbs/Stock/M.1779352679.A.3B4.html，「國外成分證券ETF期貨及選擇權」自 115/7/6 改為三階段漲跌幅，不影響 ETF 現貨
      - 使用者情境：security_master 中自選 9 檔都是 .TWSE、source=TWSE_OPENAPI，00885、00893 為 foreign_equity/official_metadata；CFI 分類只出現在 TPEX（CEOJBU 21、CEOIBU 73、CEOGEU 5）
      - 選股器與訊號目前碰不到：data/taiwan/daily/date=2026-09-23/part.parquet 共 1330 列，TPEX 0 列、TWSE 1330 列；kline_daily_enriched 9/23 分區中目標 5 檔命中 0；screener.py:207、299 從 TaiwanDailyStore 讀資料
      - 會出錯的畫面：detail_service.py:126-128（rule_type 顯示 ±10%）與 detail_service.py:212-215（calc_limits 算出漲跌停價）；intraday.py:137-140（報價清單的 limit_up/limit_down）；intraday.py:179（搜尋結果的 price_limit_pct）
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：adapters.py:167-169 看到 CFI 代碼 CEOGEU，就判成 domestic_equity／domestic（來源標 cfi_code，屬於已確認來源）。但 CFI 的 CE 屬性沒有國別資訊，因此本地 security master 中 5 檔 CEOGEU 上櫃 ETF 裡，有 4 檔國外成分 ETF 被錯套 10% 漲跌幅：00877 復華中國5G、00886 永豐美國科技、00887 永豐中國科技50大、009822 華南永昌未來金融。櫃買 9/23 官方次日漲跌停價是 9999.95/0.01，也就是無漲跌幅；這幾檔在歷史上也有超過 ±13% 的單日漲跌。只有 006201 元大富櫃50 判對。反方向誤判 0 檔；官方有漲跌幅而 app 拒判的是 00888、00928。這 4 檔確實在本地日 K 裡，所以受影響的不只報價面板與詳細頁：選股器的距漲跌停欄位也會有值，而且用歷史資料算漲跌停類訊號時會誤觸，例如 00877 誤觸漲停 2 天、跌停 1 天、炸板 2 天。使用者自選 9 檔都不受影響。
    - 理由：我用官方參考資料和本地 parquet 自己寫腳本重算了一遍，finding 列出的數值全部對得上。
(1) 程式碼：adapters.py:167-169 看到 cfi_code == "CEOGEU" 就回傳 ("domestic_equity","cfi_code","domestic")。直接呼叫 classify_etf_provenance，00877、00886、00887、009822、006201 五檔都得到 ('domestic_equity','cfi_code','domestic',1.0)。TpexInstrumentAdapter.get_instruments 在 adapters.py:375 呼叫 parse_isin_html 時沒有傳 official_product_types，所以上櫃 ETF 一定會走到 CFI 這一層。models.py:87-90 的 CONFIRMED_SOURCES 把 cfi_code 當成已確認來源；models.py:156-158 對 domestic 回傳 0.10。
(2) 本地 security_master.parquet（2377 列，mtime 9/17）裡的上櫃 ETF 共 120 檔，CEOGEU 占 5 檔，全部是 cfi_code/domestic。
(3) 拿櫃買 9/23 dailyQuotes 比對（raw JSON 與 CSV 一致）：官方給出 9999.95/0.01、app 卻判為 0.1 的正好 4 檔，就是 00877、00886、00887、009822；只有 006201 是官方有漲跌幅、app 判 0.1 且吻合。反方向（官方有漲跌幅、app 判無漲跌幅）是 0 檔。app 拒判、官方卻有漲跌幅的是 00888、00928 兩檔，也和 finding 相同。其餘 93 檔兩邊都判無漲跌幅，18 檔被 app 拒判而官方是 9999.95；另有 2 檔不在行情裡（00793B、00838B）。
(4) 用 MarketProfileBridge.calc_limits(10.0, 00877) 試算得 (11.0, 9.0)，與 finding 相同。
(5) 用兩個獨立證據確認 9999.95 代表無漲跌幅：9/23 櫃買行情中 00 開頭共 118 列，只有三檔國內指數 ETF（006201、00888、00928）有實際漲跌停價，其餘 115 列加上 2 檔境外標的 ETN 都是 9999.95/0.01；本地日 K 裡，00877 在 2024-10-04 收漲 +13.21%、2025-04-07 收跌 −16.73%，00886 在 2025-04-07 收跌 −13.99%、2025-04-10 收漲 +13.12%，受 10% 限制的標的不可能出現這種幅度。
(6) 線上來源：CFI 的 CE 群組四個屬性是開放或封閉式、配息政策、資產、證券型態，沒有地理屬性。復華投信（00877 的發行商）公告寫明「屬於國外成分證券指數股票型基金受益憑證(ETF)交易價格無漲跌幅限制」。009822 在 MoneyDJ 上的資料是：追蹤「彭博TPEx全球未來金融優選30指數」，投資區域「全球」。
(7) 下游影響重現：taiwan_limit_profile 對這 4 檔回傳 limit_pct=0.1；add_limit_distance_columns 會算出非 null 的距漲跌停，例如 00877 以 9/22 收盤 39.1、昨收 38.9 得到 0.0862 / 0.1168，而對照組 00679B 是 null。這 4 檔確實在本地 data/taiwan/daily 裡（00877、00886、00887 各 659 列，009822 77 列，最新到 2026-09-22）。用本地歷史資料餵 compute_limit_signals 會產生不存在的漲跌停訊號：00877 漲停 2 天、跌停 1 天、炸板 2 天；00886 漲停 1 天、跌停 1 天；00887 漲停 5 天、跌停 4 天、炸板 2 天，不過其中 2024-10 那段資料本身看起來異常（例如單日 +116%），推測有資料品質問題，這部分計數不宜全算在本 finding 上；009822 為 0。detail_service.py:126/213、intraday.py:137-141、screener.py:353/383 也都走 MarketProfileBridge，所以 finding 說詳細頁、報價與選股器受影響是成立的。
嚴重度維持 P2：這是明確的功能錯誤，只影響 4 檔上櫃國外成分 ETF，而且使用者自選 9 檔都是上市標的，都不受影響，未達到 P1 的全面口徑錯誤。
稽核過程的附註：repo 工作樹和 security_master.parquet 都沒有變動（git status 與開工時快照相同）。我原本把腳本寫到 work\verify-cfi\verify.py，那裡已有別人留下的同名檔（旁邊還有一份 03:56 的 out.txt），被我覆寫了。之後我把自己的腳本移到 work\verify-cfi-repro\，舊檔已無法復原；out.txt 沒動。
    - 證據：
      - backend/app/taiwan/universe/adapters.py:167-169：`elif cfi_code == "CEOGEU": return "domestic_equity", "cfi_code", "domestic", multiplier`
      - backend/app/taiwan/universe/adapters.py:375：TPEx live 路徑呼叫 parse_isin_html 時沒有傳 official_product_types，所以只能靠 CFI 判定
      - backend/app/taiwan/universe/models.py:87-90（CONFIRMED_SOURCES 含 cfi_code）與 :156-158（domestic 回傳 0.10×|multiplier|）
      - 腳本 AUDIT_SCRATCH/work/verify-cfi-repro/verify.py，輸出在同目錄 out.txt；比對明細 tpex_etf_limit_compare.csv
      - 本地重算結果（左）對照 finding（右）：官方 9999.95/0.01 而 app=0.10 → 00877、00886、00887、009822（4 檔）｜finding 同為這 4 檔；反方向 0 檔｜finding 0 檔；官方有漲跌幅、app 拒判 → 00888、00928｜finding 同；calc_limits(10.0, 00877)=(11.0, 9.0)｜finding (11.0, 9.0)
      - 櫃買 9/23 raw JSON（reference\raw\tpex_daily_quotes_20260923.json）：00877 次日參考價 38.69、漲停價 9999.95、跌停價 0.01；00886 49.46/9999.95/0.01；00887 16.12/9999.95/0.01；009822 10.01/9999.95/0.01；對照 006201 46.28/50.90/41.66
      - security_master.parquet 的上櫃 ETF 120 檔裡，CEOGEU 5 檔全部是 classification_source=cfi_code、underlying_scope=domestic；其中只有 006201 是真正的國內成分
      - 櫃買 9/23 行情中 00 開頭 118 列，只有 006201、00888、00928 三檔有實際漲跌停價，其餘 115 檔 ETF 加 2 檔 ETN（020025、020033）都是 9999.95/0.01
      - 本地日 K 的單日漲跌幅超過 10%（證明沒有 10% 限制）：00877 在 2024-10-04 +13.21%、2025-04-07 −16.73%；00886 在 2025-04-07 −13.99%、2025-04-10 +13.12%（downstream_out.txt）
      - 下游重現（downstream.py）：taiwan_limit_profile 對這 4 檔回傳 limit_pct=0.1；add_limit_distance_columns 對 00877（close 39.1、prev 38.9）得到 dist_up=0.0862、dist_down=0.1168，00679B 對照組為 null；compute_limit_signals 在本地歷史資料上產生不存在的漲跌停訊號：00877 漲停 2 天、跌停 1 天、炸板 2 天，00886 漲停 1 天、跌停 1 天
      - https://www.iotafinance.com/en/Attributes-CFI-Codes-Group-CE.html：CE 群組四個屬性為 Closed/Open-end、Distribution Policy、Assets、Security Type，擷取摘要寫明「No attribute addressing country, geography, or domestic versus foreign」
      - https://www.fhtrust.com.tw/ETF/annoucement_list/content/8f73cc66-740f-4aab-a676-5cfa3973c3b0（復華投信）：「屬於國外成分證券指數股票型基金受益憑證(ETF)交易價格無漲跌幅限制」
      - https://www.moneydj.com/etf/x/basic/basic0004.xdjhtm?etfid=009822.tw：追蹤指數「彭博TPEx全球未來金融優選30指數」、投資區域「全球」、交易所 OTC
      - backend/app/taiwan/detail_service.py:126,213、backend/app/api/intraday.py:137-141、backend/app/taiwan/screener.py:353,383、backend/app/indicators/pipeline.py:765-779 都透過 MarketProfileBridge.get_price_limit_pct 取得漲跌幅

### [P2 → P2] 台股選股器「逼近漲停 (≤3%)」永遠不會命中：以今日收盤當漲跌停基準（確認成立）

- **主張**：_enrich_price_limits 用 calc_limits(close, inst)，實際算出的是次日漲跌停，距離約等於 10%，因此近漲停／近跌停篩選恆為空；結果中的 limit_up/limit_down 也是次日的值
- **正確的定義或數值**：以前一日收盤（參考價）計算當日漲跌停與距離
- **app 實際行為**：這個篩選條件形同失效，結果中的漲跌停價也標錯日期
- **對使用者的影響**：使用者按「逼近漲停／跌停」永遠得到空結果，而且不會有任何錯誤提示
- **修法建議**：改用 prev_close（或 MIS 的 y）計算，與 add_limit_distance_columns 的口徑一致
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/screener.py:383-385
  - backend/app/taiwan/screener.py:527-534
  - frontend/src/pages/TaiwanScreener.tsx:1362-1371（按鈕「逼近漲停 (≤ 3%)」）
  - backend/app/indicators/pipeline.py:1970（註解已承認 screener 用 close 是既有 bug，但 docs/code-review-2026-09-22.md 沒有記錄）
- **線上證據**：
  - https://efrontrade.com/2026/06/limit-up-limit-down-price-taiwan-stock.html：漲跌停以「開盤競價基準（通常等於前一個交易日的收盤價）」計算
- **本地計算證據**：
  - 9/23 上市 1,155 檔有漲跌幅比例的標的：app 口徑距漲停 ≤3% 為 0 檔（最小 9.48%）；以前收為基準的正確口徑有 19 檔（含 15 檔收在漲停，例 1235 收 41.15＝漲停，app 距離 9.96%、顯示的漲停價 45.25）
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：TaiwanScreenerService._enrich_price_limits（screener.py:383-385）用資料日當天的 close 當漲跌停基準，距離又除以同一個 close，所以 distance_to_upper/lower_limit 恆約等於漲跌幅上限（10% 的標的最小 9.17%，20% 的最小 19.23%）。選股頁「逼近漲停／跌停 (≤3%)」按鈕與自然語言選股的「接近漲停／跌停」在任何資料、任何時段都一定回傳空結果；9/23 實測，以前收為基準應命中 19 檔（其中 15 檔收在漲停），app 為 0 檔。正確口徑是以前一營業日收盤（參考價）±幅度計算，再依檔位向內取整，與 pipeline.add_limit_distance_columns 相同。API 回應中的 limit_up/limit_down 是「資料日下一個交易日」的漲跌停（盤中最新分區還是前一日時，剛好是今日的值），但選股頁表格只顯示 ±%，不顯示這兩個價格，所以使用者能察覺的只有篩選失效。
    - 理由：核心主張成立。screener.py:383 用當列 close（read_latest_per_symbol 取的是資料日當天收盤，daily_store.py:177-196；daily 分區只有 symbol/date/open/high/low/close/volume/amount/quote_ts，沒有 prev_close）當 calc_limits 的 ref_price。models.py:174-183 → market_rules.py:309-322 算出 close×(1±pct)，再依檔位取整。screener.py:384-385 的距離又以同一個 close 為分母，所以距離在數學上固定約等於 pct。對任何股價 ≥1 元、pct 為 0.10 或 0.20 的標的，最小距離分別是 9.17% 和 19.23%，screener.py:527-530 的 ≤0.03 篩選永遠不會命中。這個結果與資料是什麼、在哪個時段查詢都無關：盤中最新分區還是前一日時，limit_up 恰好是今日漲停價，但距離仍是拿前一日收盤去比，一樣約 10%。

正確定義：當日漲跌停以前一營業日收盤價（開盤競價基準）±10% 計算，再依檔位向內取整。我找了兩個與 finding 不同的來源，說法一致。repo 內 pipeline.py:1966-1995 的 add_limit_distance_columns 也用 prev_close，1970 行的註解直接稱 screener 用 close 是「既有 bug」。docs/code-review-2026-09-22.md 的 B5 只講 monitor 增量路徑缺欄，沒有記錄這條，所以不算重複回報。

使用者情境：選股頁的「逼近漲停／跌停」按鈕（TaiwanScreener.tsx:1362-1382）與自然語言選股（screener_nl.py:65 把「漲跌停接近度」翻成 near_upper_limit）都會進同一個篩選，一定得到空結果。這不需要監控規則，只要使用者按按鈕或用自然語言就會觸發。

需要修正的次要敘述：(1) limit_up/limit_down 只出現在 API 回應，選股頁表格只顯示 ±%（TaiwanScreener.tsx:1702-1710），finding 說「顯示的漲停價 45.25」，其實是 API 欄位值，畫面上看不到。(2) 所謂「次日」是相對於資料日期；盤中最新分區仍是前一交易日時，這兩個值剛好是今日的漲跌停。(3) 距離的分母是 close，pipeline 用的是 limit_up，兩者定義不一致，但這是次要問題。

嚴重度維持 P2（明確的功能錯誤）。雖然基準價錯了也可以歸為「金融口徑」，但錯的價格不會顯示在畫面上，也不會影響交易或回測，使用者實際看到的後果只有「篩選條件形同失效」，所以不升到 P1。
    - 證據：
      - backend/app/taiwan/screener.py:383-385 — upper, lower = MarketProfileBridge.calc_limits(close, inst)；dist_up = (upper - close) / close（基準價與分母都是當列 close）
      - backend/app/taiwan/screener.py:527-534 — near_upper_limit/near_lower_limit 篩選 distance ≤ 0.03
      - backend/app/taiwan/universe/models.py:174-183 與 backend/app/taiwan/market_rules.py:309-322 — ref_price×(1±pct)，漲停向下、跌停向上取整到檔位
      - backend/app/taiwan/daily_store.py:177-196 — read_latest_per_symbol 取資料日當天的列；9/23 分區欄位只有 symbol,date,open,high,low,close,volume,amount,quote_ts（polars 實讀），沒有 prev_close
      - backend/app/indicators/pipeline.py:1970 — 註解原文「基准价一律用 prev_close (法定口徑), 与 screener.py 用 close 的既有 bug 不同」
      - frontend/src/pages/TaiwanScreener.tsx:1362-1382 — 「逼近漲停 (≤ 3%)」「逼近跌停 (≤ 3%)」按鈕；1702-1710 表格只顯示 ±price_limit_pct，不顯示 limit_up/limit_down
      - backend/app/taiwan/screener_nl.py:65 — 自然語言選股把「漲跌停接近度」對應到 near_upper_limit/near_lower_limit，同樣受影響
      - 本地計算（純函式 PriceLimitModel.calc_limits_for_pct，腳本 AUDIT_SCRATCH/work/verify_screener_limits/check.py）：app 的 data/taiwan/daily 9/23 分區、上市股票 n=1087：app 口徑最小距離為漲停 0.0948、跌停 0.0948，near_upper=0、near_lower=0；以 9/22 收盤為基準則 near_upper=19、near_lower=5，收在漲停 15 檔
      - 交叉核對 TWSE 官方 9/23 收盤行情（reference/csv/twse_mi_index_20260923_quotes.csv，前收＝收盤－帶號漲跌）：4 位數代號 1077 檔，app 口徑 near_upper=0、near_lower=0；以前收為基準 near_upper=19、收在漲停 15 檔，與官方漲跌家數表（twse_mi_index_20260923_updown_counts.csv）股票「上漲(漲停) 388(15)」一致
      - 1235 興泰：官方 9/23 收 41.15、漲跌 +3.70 → 前收 37.45 → 漲停 41.15（收在漲停）；app 口徑 41.15×1.1=45.265 → 取整 45.25，距離 9.96%
      - 理論下限（純函式掃描股價 1.00～11000）：pct 0.10 時 app 最小距離 0.09174（股價 1.09），pct 0.20 時 0.19231，股票與 ETF 檔位都一樣 → ≤0.03 在數學上不可能命中
      - 線上來源（Mr.Market 市場先生）https://rich01.com/limit-up-limit-down/ ：「以前一日收盤價的10%為限，也就是說當股價漲到前一日收盤價格的10%，即為當天的最高價」；範例「台積電昨天的收盤價格是600元…600 + (600 × 10%) = 660元」
      - 線上來源（Win 投資）https://winvest.tw/Knowledge/Article/348 ：基準「通常等於前一天的收盤價」；取整規則「向下(漲停)或向上(跌停)調整到最接近、但仍不超過限制的價格」，範例 73.20 → 漲停 80.50、跌停 65.90
      - docs/code-review-2026-09-22.md:334-337 — B5 只記錄 monitor 的距離欄只存在於增量路徑，沒有提到 screener 以 close 為基準
      - backend/tests/test_taiwan_screener.py:200-235 — near_upper_limit 只有「不應命中」的測試，沒有任何正向命中測試，所以測試抓不到這個錯誤
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：TaiwanScreenerService._enrich_price_limits（screener.py:383-385）用當日收盤 close 當漲跌停基準，實際算出的是次日漲跌停。櫃買官方次日漲跌停價與 app 計算值 868/872 相同。因此距漲停、距跌停在結構上恆約 9%～10%。本地 661 個日分區、1,279,249 列的最小值是 9.09%，所以「逼近漲停／跌停 (≤3%)」以及 distance_to_*_max ≤ 0.09 的篩選永遠回傳空結果，也沒有任何提示。9/23 上市 1,156 檔有漲跌幅的標的中，以正確基準計算距漲停 ≤3% 的有 19 檔（15 檔收在漲停，例：1235 收 41.15 即為漲停，app 的距離卻是 9.96%）。API 回應裡的 limit_up／limit_down 也是次日值，例如 1235 回 45.25；但選股器畫面只顯示 ±10% 比例，看不到這兩個欄位。修法應以當日的開盤競價基準計算：平日等於前收，除權息日則是除權息參考價。不能單純用 prev_close，否則在除權息日會把 1442、1808、2542 這類標的誤判為逼近跌停。距離的分母也應與 pipeline 的 add_limit_distance_columns 統一。
    - 理由：我自己寫了腳本重跑，finding 的核心數值全部對得上。

一、程式碼
- screener.py:383 用當日收盤 close 當基準呼叫 calc_limits。
- screener.py:384-385 的距離公式是 (上限 - close) / close。
- calc_limits 本身正確（models.py:174-183 → market_rules.py:316-322）。以收盤為基準時，算出的上限約為 close×1.1 再往下取到檔位，所以距離在結構上約 10%，扣掉檔位誤差也有 9% 以上，不可能 ≤3%。
- 跌停方向同理。

二、端到端重跑
我把 data\taiwan 複製到工作目錄。security master 只從複本快取載入，並攔掉連網與存檔路徑。接著用 app 自己的 TaiwanScreenerService.run() 送出四種請求，total 全部是 0（daily_as_of=2026-09-23）：
- near_upper_limit=True
- near_lower_limit=True
- distance_to_upper_limit_max=0.05
- distance_to_upper_limit_max=0.09
不帶條件時 total=2346。

三、並列比較（finding 的數值 → 我算出的數值）
- 9/23 上市有漲跌幅比例的標的：1,155 → 1,156。多出的 1 檔是 1525，官方標 X、本地也沒有前收，無法算正確基準。
- app 口徑距漲停 ≤3%：0 → 0。
- app 口徑最小距離：9.48% → 0.09483。
- 以前收（官方收盤 − 漲跌）為基準、距漲停 ≤3%：19 → 19，其中收在漲停 15 → 15。
- 1235 興泰：finding 寫收 41.15、app 上限 45.25、距離 9.96%。我算出收 41.15、前收 37.45、正確漲停 41.15、app 上限 45.25、app 距離 0.09964，完全一致。
- 本地 9/23 收盤與官方 MI_INDEX 比對，0 筆不一致；本地前收與官方前收比對，也 0 筆不一致。

四、「永遠」不會命中
我掃了本地全部 661 個日分區（2024-01-02～2026-09-23，共 1,279,249 列）。app 口徑距漲停的最小值是 0.0909，距跌停的最小值也是 0.0909，≤3% 的列數為 0。所以不是只有 9/23 空，是結構上永遠空。

五、「其實是次日漲跌停」
櫃買官方 9/23 dailyQuotes 附有次日參考價與次日漲跌停價，而且次日參考價等於當日收盤（872/872）。拿官方收盤跑 app 的 calc_limits(close)：
- 872 檔中 868 檔的漲停價、跌停價都與官方次日值完全相同。
- 另外 4 檔是 00877、00886、00887、009822，官方給 9999.95／0.01（不限漲跌幅），app 卻給 ±10%。這屬於另一個 ETF 分類問題，不影響本條結論。

六、需要修正或補充的細節（不影響判定）
1. 標的數：1,155 應為 1,156（差 1525）。
2. 「顯示的漲停價 45.25」只存在於 API 回應的 limit_up 欄位。TaiwanScreener.tsx:1701-1706 只顯示 ±10% 比例，畫面上看不到 limit_up／limit_down。
3. 修法建議「改用 prev_close」在除權息日會出錯：
   - 9/23 有 6 檔除權息（官方標 X），其中 1442、1808、2542 若以 prev_close 為基準，距跌停分別是 -0.9%、1.25%、-3.54%，會被誤判為「逼近跌停」。
   - 用 TWT49U 的開盤競價基準跑 calc_limits，5/5 與官方公布的漲跌停完全相同。
   - 所以基準應該用開盤競價基準（除權息參考價），而不是單純的前收。這也表示 pipeline.py:1970 的 add_limit_distance_columns 同樣有這個除權息日缺口。
   - 另外 pipeline 的分母是 limit_up，screener 的分母是 close，兩邊口徑也不一致。
4. 有沒有重複回報：docs/code-review-2026-09-22.md:334 的 B5 講的是 pipeline／monitor 的缺欄問題，沒有涵蓋 screener。docs 內也搜不到 screener 以收盤當基準的記錄，所以不算重複回報。

嚴重度維持 P2：一個 UI 篩選條件整個失效，是明確的功能錯誤。錯誤的 limit_up／limit_down 只出現在 API 回應，畫面上沒有顯示，未達 P1 的「錯誤金融口徑外露」。

唯讀驗證：執行前後 git status 相同，data\taiwan 下也沒有比腳本新的檔案。
    - 證據：
      - backend/app/taiwan/screener.py:383-385 — `upper, lower = MarketProfileBridge.calc_limits(close, inst)`; `dist_up = (upper - close) / close`
      - backend/app/taiwan/screener.py:527-534 — near_upper/near_lower 以 distance <= 0.03 過濾；distance_to_*_max 同欄位
      - backend/app/taiwan/universe/models.py:174-183 與 backend/app/taiwan/market_rules.py:316-322 — ref_price*(1±pct) 後依檔位取整（漲停往下、跌停往上）
      - backend/app/indicators/pipeline.py:1970 — 註解『基准价一律用 prev_close (法定口徑), 与 screener.py 用 close 的既有 bug 不同』
      - frontend/src/pages/TaiwanScreener.tsx:1362-1382（逼近漲停／跌停按鈕）；:1701-1706 畫面只顯示 ±price_limit_pct，未顯示 limit_up/limit_down
      - docs/code-review-2026-09-22.md:334-337 — B5 只談 pipeline/monitor，未涵蓋 screener
      - 腳本 AUDIT_SCRATCH/work/verify-scrlimit/verify.py（指令：cd backend && uv run --frozen python verify.py，exit=0）→ result.json：run_near_upper total=0、run_near_lower total=0、run_dist_up_max_0.05 total=0、run_dist_up_max_0.09 total=0、run_no_filter total=2346，daily_as_of=2026-09-23
      - result.json twse_all：rows_with_limit=1156、app_up_le_3pct=0、app_dn_le_3pct=0、app_min_dist_up=0.094828、corr_up_le_3pct=19、corr_at_limit_up=15；twse_local_vs_official_close_mismatch=0、prev mismatch=[]
      - result.json sample_1235：close=41.15、prev_ref_official=37.45、app_limit_up=45.25、app_dist_up=0.099635、corr_limit_up=41.15、corr_dist_up=0.0（官方 MI_INDEX 9/23 列：1235,興泰,...,close 41.15,+,3.7）
      - result.json history_scan：661 分區（2024-01-02～2026-09-23），1,279,249 列，app_min_dist_up=0.0909、app_min_dist_dn=0.0909、≤3% 列數 0
      - verify2.py → result2.json tpex_official_next_day：872 檔官方次日參考價=當日收盤，app calc_limits(close) 的漲停與官方次日漲停相同 868/872、跌停相同 868/872；不符的 4 檔為 00877/00886/00887/009822（官方 9999.95/0.01 不限漲跌幅）
      - verify3.py：TWT49U 9/23 除權息 5 檔以開盤競價基準跑 calc_limits，5/5 與官方漲跌停相同（例：1442 ref 28.75→(31.6,25.9)）；若用 prev_close，1442 距跌停=-0.0090、1808=0.0125、2542=-0.0354，會被誤判為近跌停
      - 唯讀驗證：執行前後 git status --porcelain diff 無差異；find data/taiwan -newer verify.py 無輸出

### [P2 → P2] 「跌停翻紅」標籤與實際定義不符（多數收黑），兩條路徑的定義也不同（確認成立）

- **主張**：台股路徑的定義是 low≤跌停價 且 close>跌停價，A 股路徑另要求 close>open，兩者都不要求收盤高於昨收；但台股慣用語「翻紅」指由跌轉漲
- **正確的定義或數值**：標籤應為「跌停打開」或「觸及跌停後回升」；若要叫「翻紅」，定義應為 close>prev_close；兩條路徑應統一
- **app 實際行為**：以「翻紅」為進場訊號的使用者，拿到的大多是當天仍大跌的個股
- **對使用者的影響**：進場觸發器的語意誤導；回測與監控規則的結果和使用者預期不同
- **修法建議**：把 signals.ts 的名稱改為「跌停打開」，或把定義改成 close>prev_close；並讓 enriched_refresh 與 compute_limit_signals 使用同一個定義
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/lib/signals.ts:159-165（name 跌停翻紅、kind entry）
  - backend/app/taiwan/enriched_refresh.py:187-188
  - backend/app/indicators/pipeline.py:897-911（多了 close>open）
  - backend/app/indicators/pipeline.py:195（後端標籤「跌停翘板(跌停后回升)」）
- **線上證據**：
  - https://m.hx168.com.cn/article/c24a7205bf11c5f944d3deac40c6dc95.html（搜尋摘要：翻紅指股價相對前一日由下跌變成上漲）
- **本地計算證據**：
  - 已儲存的 26 列 signal_limit_down_recovery=True 中，25 列收盤仍低於昨收（例 6547.TPEX 9/10 −9.84%、3653 9/15 −8.76%），只有 9 列收陽
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：signal_limit_down_recovery 在前端命名為「跌停翻紅」，並標成進場訊號（signals.ts:160-165），實際定義卻是「跌停打開」：台股路徑 enriched_refresh.py:187-188 為 low≤跌停價且 close>跌停價；A 股與盤中路徑 pipeline.py:906-908、1863-1865 另外要求 close>open。三者都不要求收盤高於昨收，與台股「翻紅＝站上平盤（昨收）」的慣用語意不符。已發布資料 26 筆命中中有 25 筆收盤低於昨收。

在使用者的純台股部署下：
- 使用者只會看到台股定義。A 股定義在 enriched_refresh 中被覆寫；data\instruments 是空的，回測也不會重算。
- 實際受影響的是台股選股器「跌停翻紅」篩選與訊號顯示。
- 監控規則目前 0 條、自選股沒有命中，暫無影響。
- 回測在此部署下推測產不出這個欄位，不存在「結果與預期不同」的情況。

兩條路徑定義不一致，以及後端標籤「跌停翹板」（monitor.py:48、pipeline.py:195）與前端名稱不一致，屬於潛在的維護問題。建議修法：把名稱改為「跌停打開」，或把定義改成 close>prev_close，並統一三處定義與三處標籤。
    - 理由：finding 的核心主張成立。signal_limit_down_recovery 實際算的是「跌停打開」：盤中碰到跌停價，收盤高於跌停價即可，不要求收盤高於昨收。前端卻把它命名為「跌停翻紅」，並標成進場訊號。台股慣用的「翻紅」是指價格站上平盤、也就是昨收，由跌轉漲。已儲存的資料裡，26 筆命中有 25 筆收盤仍低於昨收。兩條路徑的定義確實不同：A 股路徑與盤中路徑多了 close>open 條件。

不過，在使用者目前的純台股部署下，定義不一致的影響有限：
- compute_enriched 內 A 股路徑算出的欄位，會被 _add_limit_signals 用同名欄位覆寫。
- data\instruments 是空目錄，回測不會用 A 股定義重算。
- 盤中路徑略過台股，屬於已記錄的 N5。

所以目前使用者看到的只有台股定義，「兩條路徑不同」屬於潛在的維護問題。

使用者影響主要在台股選股器。它的「跌停翻紅」篩選按鈕直接讀已發布的 enriched 欄位，選出來的股票 96% 當天仍是下跌。滑鼠停在按鈕上的說明「盤中觸及跌停價後收盤回升至跌停之上」是正確的，能部分緩解誤導。finding 說回測與監控規則會受影響，這點說得過頭：
- 監控規則目前 0 條。
- 自選 9 檔在這 10 個交易日都沒有命中。
- 回測在此部署下推測根本產不出這個欄位（見證據），談不上結果與預期不同。

嚴重度：這是命名誤導，計算本身與說明文字一致，介於 P2 與 P3 之間。考量它以「進場」訊號、在選股器按鈕上直接呈現，而且實測 25/26 與名稱語意相反，維持 P2 可以接受。
    - 證據：
      - 名稱：frontend/src/lib/signals.ts:160-165。id 為 signal_limit_down_recovery，name 為「跌停翻紅」，kind 為 'entry'，description 為「盤中觸及跌停價後收盤回升至跌停之上。」
      - 台股定義：backend/app/taiwan/enriched_refresh.py:187-188，條件為 (low <= _limit_down) & (close > _limit_down)，沒有與 prev_close 比較。
      - A 股路徑：backend/app/indicators/pipeline.py:897-911，條件為沒跌停、raw_low <= 生效跌停價 + 0.005、close > open（行 908 的註解寫「收阳」）。盤中路徑 pipeline.py:1858-1867 也有同樣的 close>open。
      - 後端標籤：pipeline.py:195 為「跌停翘板(跌停后回升)」；另外 backend/app/strategy/monitor.py:48 為「跌停翹板」，但 monitor.py:34 的註解宣稱與前端 SIGNAL_LABELS 對齊，實際上三處名稱都不一致（finding 沒提到 monitor.py）。前端測試 signals.test.ts:95 禁止名稱含「翹板」，可見改名是刻意的在地化，但改成了語意不同的「翻紅」。
      - 覆寫順序：enriched_refresh.py:95 呼叫 compute_enriched(instruments=share_capital)，內部 pipeline.py:962 會走 compute_limit_signals 算出 A 股定義；接著 enriched_refresh.py:98 的 _add_limit_signals 用同名欄位覆寫，所以落檔的是台股定義。
      - 回測：backend/app/backtest/engine.py:437-458，只在 instruments 非空時才呼叫 compute_limit_signals。實查 data/instruments 是空目錄（ls 無輸出），所以不會以 A 股定義重算。推測（未實跑）：engine.py:471-477 會因缺少 signal_limit_down_recovery 而拋出「回测字段依赖未生成」。
      - 選股器讀已發布欄位：backend/app/taiwan/screener.py:227-228（註解為 Built-in signals, as published by TaiwanEnrichedRefreshService）；前端 TaiwanScreener.tsx:1316-1333 以 sig.name 顯示按鈕，title 為 description。
      - 本地計算：腳本 AUDIT_SCRATCH/work/verify_ldr_def/count_ldr.py，以 uv run python 執行。輸出：661 個分區中有 10 個含此欄；命中 26 筆，日期 2026-09-10 至 2026-09-23；close<prev_close 25 筆、close>prev_close 1 筆（6236.TPEX 2026-09-21，+9.38%）；close>open 9 筆、close<=open 17 筆。實例：6547.TPEX 2026-09-10 為 −9.84%，3653.TWSE 2026-09-15 為 −8.76%。自選 9 檔命中 0 筆。
      - 線上來源一（翻紅＝站上昨收）：https://udn.com/news/story/7251/9666970，原文「台股大盤指數今日開盤之後由黑翻紅」「盤中雖然一度下跌400多點，失守43000點，但之後翻紅上漲」。
      - 線上來源二（平盤＝昨收，紅＝上漲）：https://opop.tw/stock-market-even-price-definition/，原文「「平盤」指的就是該股票前一個交易日的「收盤價」」「數字跳動著紅色（上漲）與綠色（下跌）」「平盤通常以「白色」標示」。
      - 線上來源三（打開跌停只要求離開跌停價）：https://licai.cofool.com/ask/qa_7256065_1_3.html，原文「打开跌停是指股票价格触发跌停后重新恢复交易的现象，即原本处于跌停状态的股票…其价格开始回升，突破跌停价限制」。這與程式碼的實際定義相符。
      - 範圍外的附帶觀察（未深究）：kline_daily_enriched 共 661 個分區，只有最近 10 個含 signal_limit_down_recovery 等漲跌停欄位，較早的歷史分區沒有這些欄。
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：前端把 signal_limit_down_recovery 顯示為「跌停翻紅」（signals.ts:161，entry），但台股發布路徑（enriched_refresh.py:186-189）的定義是 low≤跌停價 且 close>跌停價，也就是「觸及跌停後打開」，不要求收盤高於昨收。已儲存的 26 列（9/10–9/23）中有 25 列當天仍收跌，其中 17 列收陰，只有 6236.TPEX 9/21 真的翻紅。這和台股「翻紅＝相對昨收由跌轉漲」的慣用語不符。計算結果本身和它的 description 一致，錯的只有名稱；另外後端還有「跌停翹板」「跌停翘板」兩種叫法。A 股路徑（pipeline.py:897-911、1858-1867）另外要求 close>open，兩個定義確實不同。但在這個純台股部署中，A 股版本會被 _add_limit_signals 覆蓋；回測與盤中路徑因為 instruments 是空表，也不會產出這個版本（依程式碼推斷）。所以目前使用者不會看到兩種結果，這部分屬於維護性風險。建議把名稱改成「跌停打開」或「觸及跌停後回升」，統一前後端的三種叫法，並把兩個定義收斂成一個。嚴重度 P3。
    - 理由：finding 的數值全部重現，程式碼上的說法也成立，但它對影響和嚴重度的描述要修正。

一、數值重現（兩套互不相依的方法）。
(1) 直接讀已發布的 kline_daily_enriched，共 661 個分區，其中只有 2026-09-10 到 09-23 的 10 個分區有這個欄位。signal_limit_down_recovery=True 共 26 列，和 finding 的 26 相同。其中 25 列收盤低於昨收，1 列高於昨收（6236.TPEX 9/21，+9.38%），和 finding 的「25 列仍低於昨收」相同。收陽（close>open）有 9 列，收陰 17 列，和 finding 的「只有 9 列收陽」相同。舉例：6547.TPEX 9/10 是 -9.84%，3653.TWSE 9/15 是 -8.76%，兩個例子的數值完全一致。
(2) 我用 Decimal 自己寫台股檔位與跌停價計算，從 data/taiwan/daily 的原始日 K 重算，沒有用到 app 的程式碼。窗口內觸及跌停共 64 列：收在跌停 38 列，打開 26 列。這 26 列和已儲存的集合逐列比對，差異為 0。改用 A 股路徑的定義（另加 close>open）只剩 9 列。改用「翻紅」口徑（觸及跌停且 close>prev_close）只剩 1 列。
(3) 官方資料：窗口內只有 6921.TWSE 9/23 落在參考資料範圍。MI_INDEX 9/23 顯示開 64.5、高 67.5、低 61.2、收 64.9、漲跌 -3.1，推回昨收 68.0。跌停價 68.0×0.9=61.2，當天最低價正好觸及，收盤跌 -4.56%，和本地資料一致。

二、程式碼。台股路徑在 enriched_refresh.py:186-189，條件是 low≤跌停價 且 close>跌停價。A 股路徑在 pipeline.py:897-911 與 1858-1867（盤中版），另外要求 close>open。兩條路徑都不要求收盤高於昨收。前端 signals.ts:161 的名稱是「跌停翻紅」，kind 為 entry。後端 monitor.py:48 的告警標籤是「跌停翹板」，pipeline.py:195 是「跌停翘板(跌停后回升)」。所以同一個訊號在前端和後端有三種叫法，第三種是 finding 沒提到的面向。

三、「翻紅」的語意。華西證券（hx168）說：「股价（相对前一日）由下跌变成上涨」、「每日股票价格是以上个交易日收盘价为基准」。百度知道說：「股票的当前价格低于昨天收盘价格，是绿色的，由于价格上涨到超出收盘价格时……就是所谓的翻红」。聯合新聞網的標題「台股早盤由黑翻紅」也是同樣的用法。因此 25/26 的觸發日當天仍是下跌（黑），名稱確實和定義不符。

四、需要修正的地方。
(a) 在這個純台股部署裡，「兩條路徑定義不同」不會讓使用者看到不一致的結果。compute_enriched 會經由 compute_all 呼叫 compute_limit_signals（pipeline.py:960-962），算出 close>open 版本，但 enriched_refresh.py:97-98 的 _add_limit_signals 用同名欄位把它覆蓋了。回測（backtest/engine.py:436-447）與盤中增量（quote_service.py:1668、pipeline.py:1686-1687）都要 repo.get_instruments() 不是空的才會算這個訊號。data/instruments 是空目錄，repository.py:1078-1087 與 1273-1279 在這種情況下回傳空表，所以這兩條路徑不會產出 close>open 版本（這一點是從程式碼推斷，沒有實際跑起來確認）。已儲存的 26 列中有 17 列收陰，也反證發布的資料只來自台股定義。因此兩個定義並存目前是維護性問題，不是使用者可見的結果分歧。
(b) 訊號算出來的結果和它自己的 description「盤中觸及跌停價後收盤回升至跌停之上」完全一致，錯的只有名稱。不過 description 只在 CustomSignals 設定頁顯示，在選股頁只是按鈕的 hover 提示。告警、規則清單、SignalPicker、策略規則等處都只經 cnSignal 顯示名稱。
(c) 使用者情境：監控規則 0 條，26 個觸發標的都不在自選 9 檔裡，沒有觀察到實際的誤觸發。

綜合以上，我把嚴重度從 P2 下修為 P3：標籤和術語錯誤屬於局部體驗，兩個定義並存屬於維護性問題。數值與核心主張本身判定 confirmed。

旁支觀察，與本 finding 無關、沒有深究：已儲存的 signal_limit_down 有 39 列，我重算只有 38 列。差的是 6236.TPEX 9/18：停牌後復牌，以 16.0 成交，app 拿停牌前的 19.05 當昨收，判成跌停。推測是減資或基準價重設的情況，可以另外查。
    - 證據：
      - backend/app/taiwan/enriched_refresh.py:186-189：signal_limit_down_recovery = usable & low<=_limit_down & close>_limit_down（不要求 close>open 或 close>prev_close）
      - backend/app/indicators/pipeline.py:897-911 與 :1858-1867：A 股路徑（含盤中版）另要求 (close > open)
      - frontend/src/lib/signals.ts:160-164：name '跌停翻紅'、kind 'entry'、description '盤中觸及跌停價後收盤回升至跌停之上。'（description 與實際計算一致）
      - backend/app/strategy/monitor.py:48 標籤 '跌停翹板'；pipeline.py:195 '跌停翘板(跌停后回升)'：前後端共三種叫法（finding 沒提到）
      - 覆蓋關係：enriched_refresh.py:97-98 先 compute_enriched（pipeline.py:960-962 在 instruments 非空時呼叫 compute_limit_signals），再由 _add_limit_signals 以同名欄位覆蓋；backtest/engine.py:436-447、quote_service.py:1668,1681 與 pipeline.py:1686-1687 都以 repo.get_instruments() 非空為前提；data\instruments 為空目錄（ls 確認），repository.py:1078-1087、1273-1279 會回傳空表
      - 指令：cd backend && uv run python ...\audit\work\verify-ldr\s1_stored.py → 'signal_limit_down_recovery True count: 26'、'close < prev_close: 25'、'close > prev_close: 1'（6236.TPEX 2026-09-21 +9.38%）、'close > open (yang): 9'、'close < open: 17'；6547.TPEX 9/10 chg -9.84、3653.TWSE 9/15 chg -8.76
      - 指令：uv run python ...\verify-ldr\s2_recompute.py（Decimal 自製檔位與跌停價，從 data\taiwan\daily 重算）→ 'touched limit-down rows: 64'、'def_tw (low<=LD & close>LD): 26'、'def_cn (+close>open): 9'、'def_red (touched LD & close>prev_close): 1'、'def_tw but close<prev: 25'、'stored-only: []'、'mine-only: []'
      - 並列比較：finding 26 列 → 重算 26 列；finding 25 列低於昨收 → 重算 25 列；finding 9 列收陽 → 重算 9 列；兩個例子的數值完全一致
      - 官方資料 reference\csv\twse_mi_index_20260923_quotes.csv:1220：'2026-09-23,6921,嘉雨思-創,...,64.5,67.5,61.2,64.9,-,-3.1' → 昨收 68.0，跌停價 61.2（low 觸及），收盤 -4.56%，與本地儲存值一致
      - https://m.hx168.com.cn/article/c24a7205bf11c5f944d3deac40c6dc95.html：「股价（相对前一日）由下跌变成上涨」、「每日股票价格是以上个交易日收盘价为基准，低于则以绿色表示，高于就以红色表示」
      - https://zhidao.baidu.com/question/431766093.html：「股票的当前价格低于昨天收盘价格，是绿色的，由于价格上涨到超出收盘价格时，股票由绿转红，就是所谓的翻红」；https://udn.com/news/story/7251/9666970 標題「台股早盤由黑翻紅」
      - UI 顯示範圍：description 只出現在 frontend\src\pages\settings\CustomSignals.tsx:167 與 pages\TaiwanScreener.tsx:1321（title 提示）；AlertToast、RuleEditor、SignalPicker、StrategyRule、Dashboard、StockPreviewDialog 都只經 cnSignal（signals.ts:211-214）顯示名稱
      - 使用者情境：26 個觸發標的都不在自選 9 檔中，監控規則 0 條

### [P2 → P2] 分時圖與報價面板的漲跌停顯示錯誤：國外 ETF 仍畫 ±10% 線、進位用 0.01 元、百分比顯示成 0.1%（確認成立）

- **主張**：K 線 API 的 price_limit.rate 取自 price_limit_pct(symbol)，台股一律回傳 0.10（不區分 ETF 類型與槓桿）；前端分時圖用 A 股方式四捨五入到分；報價面板把小數比例直接當百分比顯示
- **正確的定義或數值**：國外 ETF 不畫漲跌停線；槓桿 ETF 用 20%；價格依檔位取整；面板顯示「+10%」
- **app 實際行為**：自選中的 00885、00893 在預覽的分時圖會顯示不存在的 ±10% 線（前提是有分鐘資料）；報價面板標示「漲停價 (+0.1%)」
- **對使用者的影響**：自選股預覽與監控面板直接呈現錯誤的漲跌停資訊
- **修法建議**：kline.py 改用 MarketProfileBridge.get_price_limit_pct 與 calc_limits，把 limit_up/limit_down 帶給前端；TaiwanQuotePanel 顯示時乘以 100
- **稽核者信心**：plausible
- **程式碼證據**：
  - backend/app/api/kline.py:224-231
  - backend/app/price_limits.py:38-55,79-80（limit_class 為 None 時回傳 ORDINARY_TEN_PERCENT）
  - frontend/src/components/EChartsIntraday.tsx:50-58（Math.round(rawUp*100)/100，註解寫「A股」）
  - frontend/src/components/monitor/TaiwanQuotePanel.tsx:350,356（`+${price_limit_pct}%`，而後端 intraday.py:137,144 回傳的是 0.1）
- **線上證據**：
  - https://www.cnyes.com/twstock/2454（9/23 漲停 5695／跌停 4665）
- **本地計算證據**：
  - 實際呼叫 price_limit_pct(s, 2026-09-23)：2454、00885、00893、00631L 全部回傳 0.1（00885、00893 應為無漲跌幅，00631L 應為 0.2）
  - 以前收 5180 計算：分時圖會畫在 5698／4662，官方為 5695／4665
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：有三個互相獨立的顯示錯誤，都成立：

(1) K 線分鐘 API 的 price_limit.rate 呼叫 price_limit_pct(symbol, date)。它沒有傳 limit_class，所以台股一律回傳 0.10，而不是依 Security Master 判定：00885／00893 國外成分 ETF 應為無限制，00631L 應為 0.20。當日改用 instruments 漲跌停價的覆蓋路徑，在本部署因 instruments 目錄為空而永遠不會生效。

(2) EChartsIntraday 前端自己算漲跌停價，用 Math.round(x*100)/100（A 股的「取到分」），沒有依台股升降單位把漲停往下、跌停往上取整。例如 2454 昨收 5180 時，前端得 5698／4662，官方是 5695／4665。

(3) TaiwanQuotePanel 把小數比例 0.1 直接當百分比，顯示成「漲停價 (+0.1%)」「跌停價 (-0.1%)」。受影響的是自選中有漲跌幅限制的 7 檔，Monitor 頁預設就看得到。面板上的漲跌停價格本身正確。

需修正的影響描述：分時圖的漲跌停虛線（包括 00885／00893 那兩條不存在的 ±10% 線）只在使用者把圖表切到「漲跌停」模式時才會出現，預設的「自適應」模式不畫線。自適應模式下唯一的影響，是 Y 軸被夾在 ±10% 以內；國外 ETF 單日波動超過 10% 時走勢會被截掉。

修法補充：只改後端還不夠。EChartsIntraday.tsx:50 在 rate 為 null 時會退回 0.10，前端必須改成 rate 為 null 就不畫線、不夾 Y 軸，PriceLimitInfo.rate 的型別也要改為可為 null。
    - 理由：finding 的三項程式碼主張，我逐一親自核對後都成立，嚴重度 P2 也恰當。但影響描述少寫了一個前提：分時圖的漲跌停虛線要使用者先切到「漲跌停」模式才會畫出來。

(1) K 線分鐘 API 的 price_limit.rate 呼叫 price_limit_pct()。台股代號會走 taiwan_price_limit_pct；kline.py 沒有傳 limit_class，所以一律回傳 ORDINARY_TEN_PERCENT，也就是 0.10。我實際跑了自選 9 檔加 00631L，全部回傳 0.1。同一時間，security_master.parquet 經 MarketProfileBridge.get_price_limit_pct 得到的結果是：00885／00893 為 None（國外成分），00631L 為 0.2。此外，kline.py 另有一條「當日改用 instruments 的 limit_up/limit_down」的覆蓋路徑，在本部署實際上用不到，因為 data/instruments 與 data/instruments_etf 都是空目錄，所以 limit_up/limit_down 永遠是 null，前端只能自己算。

(2) EChartsIntraday 的 getLimitPrices 用 Math.round(x*100)/100 取到 0.01 元，註解還寫著「A股」，完全沒有依台股升降單位取整。我拿參考資料的 MIS 9/23 快照比對（後端 bridge 的 calc_limits 算出的數字與官方一致）：
- 2454：昨收 5180，前端算出 5698／4662，官方是 5695／4665。
- 6196：昨收 535，前端漲停 588.5，官方 588。
- 0050：前端 123.04／100.67，官方 123.0／100.7。
- 00878：兩者一致。

(3) TaiwanQuotePanel 直接把 `+${price_limit_pct}%` 印出來，而 intraday.py 回傳的 price_limit_pct 是小數比例 0.1，所以畫面顯示「漲停價 (+0.1%)」。同一專案裡 TaiwanScreener.tsx:1706 有乘以 100，detail_models.py:49 也寫明「0.1 代表 10%」，可見慣例就是比例。面板上的漲跌停價格本身是對的（用 calc_limits 依檔位取整）；00885／00893 會正確顯示「無限制」。受影響的是自選中的 7 檔（5 檔個股加 0050、00878），Monitor 頁預設就看得到。

使用者情境：
- 自選 9 檔在 kline_minute 的 9/17、9/18、9/22、9/23 分區都有分鐘資料，預覽對話框（StockPreviewDialog → StockPanel showIntraday → StockIntradayChart）會把 price_limit 傳進圖表。
- 但 yMode 預設是 'adaptive'（EChartsIntraday.tsx:419）。只有在 'limit' 模式（第 151 行）才會畫虛線、Y 軸撐到 ±limit。自適應模式下唯一的影響，是 Y 軸上限被夾在 ±10%（第 177-181 行）；00885／00893 若單日波動超過 10%，走勢會被截掉。這種情況少見，屬推測。

修法補充：finding 的修法只改後端還不夠。EChartsIntraday.tsx:50 在 rate 不是有限數時會退回 0.10，所以後端就算對國外 ETF 回 rate=null，前端還是會畫 ±10% 線。前端必須把 rate 為 null 當成「不畫線、不夾 Y 軸」處理，PriceLimitInfo.rate 的型別也要改成可為 null。

線上定義（與 finding 引用的 cnyes 不同來源）：
- 群益投信：追蹤國內指數的 ETF，漲跌幅為 10% 乘以槓桿或反向倍數，例如正 2 為 20%；追蹤國外指數則沒有漲跌幅限制。
- 口袋證券：境外 ETF 無漲跌幅限制。
- Win 投資：漲停價要往下、跌停價要往上調整到升降單位，1000 元以上的升降單位是 5 元。

嚴重度：這是明確的顯示功能錯誤，但沒有交易計算依賴這兩個顯示值（監控規則 0 條），所以 P2 恰當，不到 P1。
    - 證據：
      - backend/app/api/kline.py:213-231：_get_price_limit_info 的 rate 由 price_limit_pct(symbol, trade_date, is_risk_warning=...) 產生，沒有傳 limit_class，也沒有查 Security Master
      - backend/app/api/kline.py:236-271：只有當日才讀 repo.get_instruments_asset 的 limit_up/limit_down；本部署 data\instruments、data\instruments_etf 都是空目錄（ls 驗證），這條路徑永遠不會生效
      - backend/app/price_limits.py:50-55,79-80：limit_class 為 None 時回傳 PriceLimitModel.get_limit_pct(ORDINARY_TEN_PERCENT)；market_rules.py:302-306 對此回傳 0.10
      - 本地計算（uv run python ...\work\verify_limitdisp\check.py）：price_limit_pct(s, 2026-09-23) 對 2454、3034、2379、6196、3545、0050、00878、00885、00893、00631L 全部回傳 0.1；同一腳本以 security_master.parquet 建 TaiwanInstrument，經 MarketProfileBridge.get_price_limit_pct 得到 00885→None、00893→None（foreign_equity/official_metadata）、00631L→0.2、其餘→0.1
      - frontend/src/components/EChartsIntraday.tsx:50-59：pct 在 rate 不是有限數時退回 0.10，然後 Math.round(raw*100)/100，註解寫「A股漲跌停價四捨五入到分」
      - 本地計算對照 reference\csv\mis_snapshot.csv（MIS 9/23）：2454 y=5180 u=5695 w=4665，前端退回計算為 5698/4662；6196 y=535 官方 588/481.5，前端 588.5/481.5；0050 y=111.85 官方 123.0/100.7，前端 123.04/100.67；00878 y=35.21 兩者都是 38.73/31.69；bridge.calc_limits 四檔都與官方一致
      - frontend/src/components/EChartsIntraday.tsx:419（yMode 預設 'adaptive'）、151-173（只在 showLimitLines && yMode==='limit' 時畫漲跌停虛線）、177-181（自適應模式把 Y 軸夾在 ±limitDiff）、541-563（切換按鈕「自適應／漲跌停」）
      - frontend/src/components/StockIntradayChart.tsx:123-133 把 minute.data.price_limit 傳給 EChartsIntraday；StockPanel.tsx:172-190、StockPreviewDialog.tsx:434-437（showIntraday）
      - data\kline_minute 分區實測：date=2026-09-17/18/22/23 都有自選 9 檔的分鐘棒（例如 9/23：00885 共 80 根、00893 共 85 根、2454 共 84 根）
      - frontend/src/components/monitor/TaiwanQuotePanel.tsx:350,356：`+${currentQuote.price_limit_pct}%`／`-${...}%`；backend\app\api\intraday.py:137,144 回傳 MarketProfileBridge.get_price_limit_pct（小數 0.1）；對照 frontend\src\pages\TaiwanScreener.tsx:1706 用 (price_limit_pct*100).toFixed(0)，backend\app\taiwan\detail_models.py:49 寫「0.1 代表 10%」
      - frontend\src\pages\Monitor.tsx:102 無條件渲染 TaiwanQuotePanel；TaiwanQuotePanel.tsx:178-204 以自選清單取報價，預設顯示第一檔
      - https://www.capitalfund.com.tw/web/tw/etf/leverAndReverse/trading_guide：「追蹤國內指數標的，漲跌幅度為10%乘以槓桿或反向倍數…單日正向2倍ETF的漲跌幅為20%」「追蹤國外指數標的，漲跌幅度則沒有限制。」
      - https://www.pocket.tw/callcenter/questions/102/：「境內ETF同國內股票，有 10% 漲跌幅的限制，境外ETF則無漲跌幅限制。」
      - https://winvest.tw/Knowledge/Article/348：升降單位 1000 元以上為 5 元；漲停價、跌停價要「向下(漲停)或向上(跌停)調整到最接近、但仍不超過限制的價格」（例：73.2×110%=80.52 調整為 80.50）
      - reference\csv\twse_t187ap47_L_etf_meta.csv：00885 富邦越南、00893 國泰智能電動車 fund_type=國外成分證券指數股票型基金、includes_foreign_components=是；00631L=槓桿/反向指數股票型基金
      - docs\code-review-2026-09-22.md 已搜尋：沒有記錄 kline price_limit.rate、EChartsIntraday 取整或 TaiwanQuotePanel 百分比顯示問題（C1 只談刷新政策）
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：重現結果與原 finding 一致，以下幾點需要補充或修正：
(1) /api/kline/minute 的 price_limit.rate 來自 price_limit_pct()，所有台股一律是 0.1。本環境 instruments 快取是空的，limit_up/limit_down 永遠是 null。所以國外成分 ETF（自選的 00885、00893）也以 10% 計算，00631L 同樣以 10% 計算，而正確值是 20%。
(2) 前端 EChartsIntraday 把「前收 ×(1±rate)」四捨五入到 0.01 元，不依台股檔位取整。自選 7 檔有漲跌幅限制的標的中有 5 檔算錯，例如 2454 算出 5698/4662（官方 5695/4665）、0050 算出 123.04/100.67（官方 123.0/100.7）、6196 漲停算出 588.5（官方 588）。00878 與 3545 剛好一致。
(3) 分時圖預設是「自適應」模式，不畫漲跌停線，±10% 只用來限制 Y 軸範圍。使用者切到「漲跌停」模式後，才會畫出上述錯誤價位的虛線，或國外 ETF 根本不存在的 ±10% 虛線。
(4) 報價面板的漲跌停價本身正確，00885、00893 也正確顯示「無限制」。錯的只有百分比標籤：10% 的標的顯示「+0.1%／-0.1%」。
修法要前後端一起改：後端改用 MarketProfileBridge 的 pct 與 calc_limits；前端在 EChartsIntraday.tsx:50 移除 rate 缺值時退回 0.10 的邏輯，無限制時不畫線、也不限制 Y 軸；面板標籤乘以 100。
    - 理由：這條 finding 的三個主張，我用本地 parquet、官方參考資料（MIS 快照、STOCK_DAY、ETF 基本資料），加上在 node 實際執行前端的 getLimitPrices 邏輯重算，數值全部重現：
(1) kline.py 用的 price_limit_pct 對所有 .TWSE 代號一律回傳 0.1。證券主檔的 MarketProfileBridge 則判定 00885、00893 為無漲跌幅（None），00631L 為 0.2。
(2) 前端依 rate 乘上前收，再用 Math.round 取到 0.01 元，不依檔位取整。自選 7 檔有漲跌幅限制的標的中，5 檔的價格和檔位規則不符。2454 算出 5698/4662，官方 MIS 為 5695/4665，和 finding 的數字完全一致。
(3) 報價面板把 0.1 直接接上 %，顯示成「+0.1%」。

另外補充 finding 沒提到的面向：
(a) 本環境 data/instruments* 三個目錄都是空的。所以 _get_price_limit_info 在當日分支拿不到 limit_up/limit_down，永遠走規則推算。
(b) 前端 EChartsIntraday.tsx:50 在 rate 不是有限數（例如 null）時會退回 0.10。因此 fix_suggestion 只改後端不夠：國外 ETF 即使後端回傳 null 仍會畫出 ±10% 線，前端也得處理「無限制」。
(c) 分時圖預設 yMode 為 'adaptive'（EChartsIntraday.tsx:419），不畫漲跌停虛線，±10% 只用來限制 Y 軸範圍。要切到「漲跌停」模式（:151-174）才會畫出錯誤的虛線。所以 actual 的觸發條件要再加一條：使用者切換到漲跌停模式。
(d) 00885、00893 在 9/17、9/18、9/22、9/23 都有本地分鐘線（80–134 根），/minute 會走 local_partial 回傳。「有分鐘資料」這個前提確實成立。
(e) 報價面板的漲跌停價由 MarketProfileBridge.calc_limits 計算，與官方一致。錯的只有百分比標籤；00885、00893 在面板上正確顯示「無限制」。
以上都屬於顯示錯誤，不影響交易或資料。嚴重度維持 P2。
    - 證據：
      - 程式碼：backend/app/api/kline.py:224-235：rate=price_limit_pct(symbol, trade_date, ...)，limit_up/limit_down 預設為 None
      - 程式碼：backend/app/price_limits.py:79-80 → :50-55：Taiwan 代號在 limit_class 為 None 時回傳 PriceLimitModel.get_limit_pct(ORDINARY_TEN_PERCENT)=0.10
      - 程式碼：backend/app/api/kline.py:236-255：只有 trade_date==today 時才讀 instruments 的 limit_up/limit_down。本地計算顯示 data\instruments、instruments_etf、instruments_index 的 parquet 檔數都是 0，所以永遠 return 規則推算的 info
      - 本地計算（verify.py 區段 A）：price_limit_pct(X.TWSE, 2026-09-23) 對 2454/3034/2379/6196/3545/0050/00878/00885/00893/00631L 全部是 0.1
      - 本地計算（verify.py 區段 B，只讀取 security_master.parquet 快取）：MarketProfileBridge.get_price_limit_pct → 00885 foreign_equity/foreign=None、00893 foreign_equity/foreign=None、00631L leveraged/domestic x2.0=0.2、其餘 0.1
      - 官方參考 csv/twse_t187ap47_L_etf_meta.csv：00885 富邦越南、00893 國泰智能電動車 fund_type=國外成分證券指數股票型基金、includes_foreign_components=是；00631L=槓桿/反向指數股票型基金、臺灣50指數
      - 線上來源 https://wealth.businessweekly.com.tw/GArticle.aspx?id=ARTL003015007（搜尋摘要）：「國外成分ETF…無漲跌幅限制」「元大台灣50正2（00631L）的漲跌幅限制就是20%（＝10%ｘ2）」
      - 官方 MIS 快照 csv/mis_snapshot.csv（9/23）：2454 y=5180 u=5695 w=4665；6196 y=535 u=588 w=481.5；0050 y=111.85 u=123.0 w=100.7；00878 y=35.21 u=38.73 w=31.69
      - node 實跑前端 getLimitPrices（rate=0.1，limit_up/down=null），與官方或檔位規則並列（前端 vs 正確）：2454 5698/4662 vs 官方 5695/4665；6196 588.5/481.5 vs 官方 588/481.5；0050 123.04/100.67 vs 官方 123.0/100.7；00878 38.73/31.69 = 官方；3034 612.7/501.3 vs calc_limits 612/502（推算）；2379 830.5/679.5 vs 830/680（推算）；3545 57.2/46.8 = 推算值；00885 20.09/16.43 vs 應無限制；00893 50.35/41.19 vs 應無限制；00631L 42.37/34.67 vs calc_limits 46.22/30.82（20%）
      - 官方 STOCK_DAY 與本地 data\taiwan\daily\date=2026-09-22 的收盤價逐檔相同（2454=5180、00885=18.26、00893=45.77 等）。00631L 只有本地資料 38.52
      - 程式碼：frontend/src/components/EChartsIntraday.tsx:50：rate 不是有限數時退回 0.10。node 實跑 rate=null → 00885 usedPct=0.1、20.09/16.43，所以只改後端仍會畫 ±10%
      - 程式碼：EChartsIntraday.tsx:419 預設 yMode='adaptive'。:151-174 只有 yMode==='limit' 時才 push 漲跌停 markLine；:176-181 adaptive 模式只用 limitDiff 限制 Y 軸範圍。StockIntradayChart.tsx:123-133 沒有傳 showLimitLines（預設 true）
      - 本地計算（verify.py 區段 E）：data\kline_minute 9/17、9/18、9/22、9/23 的 00885.TWSE 有 109/121/102/80 根，00893.TWSE 有 115/134/106/85 根。kline_sync.py:855-856 在 taiwan provider 下回傳空 DataFrame，所以 kline.py:947-957 走 local_partial 回傳本地資料
      - 程式碼：backend/app/api/intraday.py:137-145：price_limit_pct=MarketProfileBridge.get_price_limit_pct（小數），limit_up/down=calc_limits（依檔位取整）。TaiwanQuotePanel.tsx:350,356 直接輸出 `+${price_limit_pct}%`。node 實跑：0.1 → 「漲停價 (+0.1%)」、0.2 → 「+0.2%」、null → 「無限制」。對照 TaiwanScreener.tsx:1706 用 (price_limit_pct * 100).toFixed(0)，確認是小數口徑
      - 腳本與輸出：AUDIT_SCRATCH/work/verify-intraday-limit/verify.py、output.txt。git status 與基準相同，未寫入 repo

### [P3 → P3] 盤中增量路徑與盤後路徑的 60 日新高／新低、布林、MA60 定義不一致（確認成立）

- **主張**：盤中路徑 high_60d／low_60d 用最高價／最低價（_high_59d=high.tail(59).max()），盤後用收盤價；布林盤中用母體標準差並用 ≥／≤，盤後用樣本標準差並用 >／<；盤中 MA 用 partial_sum/n，不檢查窗口長度
- **正確的定義或數值**：兩條路徑使用同一套定義
- **app 實際行為**：盤中寫入的當日分區（台股選股器 _join_signals 會讀取）與收盤後重算的結果不同
- **對使用者的影響**：盤中看到的「60日新高」「突破布林」訊號，收盤後可能消失或改變
- **修法建議**：_high_59d／_low_59d 改用 close；盤中布林與盤後一致（統一 ddof 與比較運算子）；MA 在 _window_len 不足時回傳 null
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/repository.py:913-914
  - backend/app/indicators/pipeline.py:1590-1599,1680-1681 對照 437-440,655-656
  - backend/app/indicators/pipeline.py:1524-1532,1669-1670 對照 416-417,449-451,657-658
  - backend/app/indicators/pipeline.py:1515-1521（MA60 沒有 _window_len 保護）
  - backend/app/indicators/pipeline.py:162（high_60d 標為「60日最高价」，但盤後其實是收盤最大值）
- **線上證據**：
  - https://www.bollingerbands.com/bollinger-bands：「We use the population calculation for standard deviation」
- **本地計算證據**：
  - 把同一根收盤 K 棒分別餵給 compute_enriched_today 與 EOD compute_signals（9/16～9/23 共 6 天）：n_day_high 差 304 次、n_day_low 280、boll_upper 49、boll_lower 19、ma_golden_20_60 25（交易有缺日的標的 MA60 被低估，例 1213、9110、5906）
  - 9/23 自選 0050、2454：盤後 signal_n_day_high=True，盤中版為 False（high_60d 分別是 113.75／5480 與 112.45／5185）
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：盤中增量路徑（compute_enriched_today 加 _build_live_agg）與盤後路徑（compute_enriched）的定義不一致，有三處：
1. 60 日極值：盤中用最高價與最低價（repository.py:913-914），盤後用收盤價（pipeline.py:437-440）。
2. 布林：盤中用母體標準差與 ≥／≤，盤後用樣本標準差（ddof=1）與 >／<。
3. 均線：盤中 MA 一律除以 n，不檢查 _window_len。

用同一根收盤 K 棒重現 9/16～9/23 共 6 天，只算上市時的差異次數為 n_day_high 304、n_day_low 280、boll_upper 49、boll_lower 19、ma_golden_20_60 25。納入上櫃後為 425／453／69／32／160，其中上櫃多出的 ma_golden 差異有 20 筆是另一個原因：前一交易日缺列，交叉訊號因此是 null。

差異方向：
- 60 日新高與新低是盤中漏報、收盤後才出現。
- 布林突破是盤中多報、收盤後消失。差異全部來自 ddof，通道半寬比值為 sqrt(20/19)；≥ 與 > 的差別在這 6 天沒有任何實際影響。
- MA60：無成交日在 filter_halt_days 被丟掉後，90 天窗口不到 59 筆，MA60 最多低估 88.7%，造成盤中誤報 20/60 金叉。

修正時，60 日極值應改用 close，MA 在窗口不足時回傳 null。布林應統一成 bollingerbands.com 定義的母體標準差（ddof=0）；目前偏離原始定義的其實是盤後路徑。

在此部署中，盤中路徑只處理 9 檔自選股，這 9 檔的窗口都完整。實際影響是 6 天內 12 列 60 日新高不一致（0050、2454、00878、3034）與 1 列布林上軌不一致（2379），盤後重算會覆蓋這些值。
    - 理由：我照實際執行路徑寫了模擬腳本（AUDIT_SCRATCH/work/verify-intraday-eod/repro.py、analyze.py、check2.py、check3.py），重現盤中與盤後兩條路徑，結果與 finding 的核心說法全部相符。

盤後路徑重現 TaiwanEnrichedRefreshService.refresh：用 compute_enriched 算 taiwan/daily 全部歷史，factors 為空。
盤中路徑重現 _refresh_enriched 加 _build_live_agg：取 kline_daily_enriched 最近 300 天，先跑 compute_indicators 與 compute_signals。接著用 object.__new__(KlineRepository) 直接呼叫真正的 _build_live_agg(B)（B 為前一交易日），再把當日收盤 K 棒餵給 compute_enriched_today。

一、程式碼定義差異：全部屬實。
- repository.py:913-914 以 high.tail(59).max() 與 low.tail(59).min() 取極值；pipeline.py:1590-1599 再與當日 high、low 取 max／min。盤後 pipeline.py:437-440 用 close 的 rolling_max(60) 與 rolling_min(60)。
- 盤中布林在 pipeline.py:1524-1532 用 sum_sq/20 - mean^2，也就是母體變異數；盤後 pipeline.py:416-417 用 rolling_std(20)。polars 1.40.1 的 rolling_std 預設 ddof=1。
- 盤中比較用 ≥／≤（pipeline.py:1669-1670），盤後用 >／<（pipeline.py:657-658）。
- 盤中 MA60 在 pipeline.py:1520 用 (_ma60_partial_sum + close) / 60 計算。_window_len 只出現在 pipeline.py:1703 的 drop 清單，完全沒被使用。

二、數據重現：只算上市（.TWSE）標的時，數字與 finding 完全一致。
- 6 天的差異次數：n_day_high 304、n_day_low 280、boll_upper 49、boll_lower 19、ma_golden_20_60 25，逐項相同。
- 納入上櫃（.TPEX）後總數變成 425／453／69／32／160，finding 沒有說明它只算了上市。上櫃多出的 ma_golden 差異裡，有 20 筆來自另一個機制：該標的在前一交易日沒有列，prev_enriched 缺值，盤中的交叉訊號因此是 null。這不是 MA60 的問題。

三、各項差異的成因與方向：
- 60 日新高（上市）：差異都是盤後 True、盤中 False，其中 295 筆的窗口是完整的，屬於純定義差異。盤中必須收盤價等於當日最高價，且不低於前 59 日最高價，才會觸發。
- 布林：差異都是盤後 False、盤中 True，全部來自 ddof 不同。窗口完整時，兩條路徑的通道半寬比值最小、中位數、最大值都是 1.0259784，正好等於 sqrt(20/19)。收盤價剛好落在通道線上的情況是 0 筆，所以 ≥ 與 > 的差別雖然存在於程式碼，這 6 天對訊號沒有任何實際影響。
- MA60 低估：成因是 taiwan/daily 裡無成交日的 open／high 為 null。filter_halt_days 的條件遇到 null 會把整列丟掉，於是 90 個日曆天的窗口裡不到 59 筆。全市場共 153 檔、539 列受影響，其中 1213、5906、9110 被低估 5% 到 25%，最大低估 88.7%。窗口完整的 11355 列，MA60 誤差都在 1e-15 以內。

四、官方資料交叉核對（TWSE STOCK_DAY，8 至 9 月共 38 天）：0050、2454、00878 的 OHLC 與本地資料 0 筆不符。
- 0050：9/22 最高 113.75，9/23 收盤 112.45。
- 2454：9/22 最高 5480，9/23 收盤 5185。
- 這些數字與 finding 引用的 113.75／5480 對 112.45／5185 相同。

五、需要修正或補充的地方：
- 使用者影響的方向要分開寫。60 日新高與新低是盤中漏報、收盤後才出現；布林突破與 MA20／60 金叉則是盤中多報、收盤後消失。
- 修法方向：bollingerbands.com 原文是「We use the population calculation for standard deviation」，所以盤中的母體算法才符合原始定義，偏離的是盤後的 ddof=1。統一時應改成 ddof=0，不宜照抄盤後的做法。
- 實際部署範圍：log 顯示盤中增量路徑每輪只處理 9 檔以內的自選股（backend.log 裡「enriched 增量: N 只」的 N 最多是 9）。這 9 檔的 _window_len 都是 59，不受 MA60 問題影響。在這 9 檔中，6 天共有 12 列 60 日新高不一致：00878 5 列、0050 3 列、2454 3 列、3034 1 列；另有 2379 在 9/22 盤中多出布林上軌突破。
- 嚴重度：盤後重算會覆蓋當日分區，而使用者有 0 條監控規則，影響只停在盤中選股器的訊號標籤。所以我維持 P3。若改為全市場盤中計算或啟用監控規則，影響會擴大到 P2。
    - 證據：
      - 程式碼：backend/app/repository.py:913-914（_high_59d=high.tail(59).max()、_low_59d=low.tail(59).min()）、:930（_window_len）、:751（start_60d = latest - 90 個日曆天）
      - 程式碼：backend/app/indicators/pipeline.py:1590-1599（盤中極值取 max(_high_59d, high)）對照 :437-440（盤後 close 的 rolling_max／rolling_min(60)）；:1524-1532（母體變異數）對照 :416-417（rolling_std(20)，polars 1.40.1 簽名為 ddof: int = 1）；:1669-1670（≥／≤）對照 :657-658（>／<）；:1520（MA60 固定除以 60）；:1703（_window_len 只出現在 drop 清單）
      - 只算上市、9/16～9/23 共 6 天的重現（check3.py）：n_day_high 304、n_day_low 280、boll_upper 49、boll_lower 19、ma_golden_20_60 25，與 finding 的 304／280／49／19／25 完全相同；納入上櫃後為 425／453／69／32／160（repro.py）
      - 上市差異成因（逐列分類）：n_day_high 中窗口完整 295 筆、窗口不足 9 筆，全部是盤後 True、盤中 False；boll_upper 49 與 boll_lower 19 筆全部是盤後 False、盤中 True，且窗口完整；ma_golden_20_60 25 筆全部是窗口不足（_window_len<59）造成盤中誤報金叉
      - 布林 ddof：窗口完整時，(盤後上軌 - ma20)/(盤中上軌 - ma20) 的最小值 1.025978351624624、中位數 1.0259783520851873、最大值 1.0259783530514812，理論值 sqrt(20/19) = 1.025978352085154；兩路徑 ma20 相對差最大 6.66e-16；收盤價等於通道線的列數為 0（比較運算子差異沒有實際影響）
      - MA60 低估（analyze.py）：窗口完整的 11355 列 ma60 最大相對差 8.88e-16；_window_len<59 的共 421 列（全部 539 列中盤後 MA60 非 null 者），最大低估 -0.88685。例：9/23 的 1213 為 7.530833 對 7.123833（-5.4%，窗口 56）、5906 為 39.903333 對 31.718333（-20.5%，窗口 48）、9110 為 3.157167 對 2.755667（-12.7%，窗口 52）
      - 窗口不足的成因（check3.py）：在 2026-06-24～2026-09-22 的 64 個交易日中，1213 的 open 與 high 為 null 的有 8 天（保留 56 列）、5906 有 16 天（保留 48）、9110 有 12 天（保留 52）；filter_halt_days（pipeline.py:978-987）的條件遇到 null 就會丟掉該列
      - 自選股 9/23：0050 收盤 112.45、最高 113.15，盤後 high_60d 112.45（True），盤中 _high_59d 113.75（False）；2454 收盤 5185，盤後 high_60d 5185（True），盤中 5480（False）
      - 官方資料：reference\csv\twse_stock_day_202608_202609.csv 裡 0050、2454、00878 各 38 天的 OHLC 與本地 taiwan/daily 0 筆不符；0050 9/22 最高 113.75、9/23 收盤 112.45（8～9 月最高收盤）；2454 9/22 最高 5480、9/23 收盤 5185
      - 線上來源：https://www.bollingerbands.com/bollinger-bands 原文「We use the population calculation for standard deviation」與「20 periods for the moving average with the bands set at plus and minus two standard deviations」
      - 部署實況：backend.log、backend.log.1、backend.log.3 共有 3676 筆「enriched 增量」紀錄，每筆 N 都 ≤ 9；增量路徑走 quote_service.py:1009 的自選股合併寫入（merge=True），所以盤中分區只含自選股
      - 自選股受影響列（6 天）：60 日新高不一致 12 列（00878 5 列、0050 3 列、2454 3 列、3034 1 列）；2379 在 9/22 的布林上軌：收盤 755，盤後上軌 755.097674（False）、盤中上軌 754.226706（True）
      - 唯讀確認：稽核結束後 git status 仍只有 session 開始前就存在的 3 個修改檔，所有腳本與 joined.parquet 都放在 scratchpad\audit\work\verify-intraday-eod\

### [P1 → P1] 回測的漲跌停鎖定用 A 股 0.01 元四捨五入，台股漲停鎖定大多偵測不到（部分成立）

- **主張**：numpy_limit_price／polars_limit_price 以「整數分、四捨五入到 0.01 元」計算漲跌停價，沒有依台股檔位取整；回測市場矩陣的 _limit_lock_matrices 直接用它判斷台股漲跌停鎖定
- **正確的定義或數值**：台股標的的漲跌停價應依 TickSizeModel 的檔位，漲停向下、跌停向上取整（PriceLimitModel.calc_limits_for_pct 已經正確實作）
- **app 實際行為**：回測把大多數收在漲停或跌停的台股當成可成交
- **對使用者的影響**：台股策略回測可能在實際封漲停、買不到的日子成交（跌停賣出同理），績效偏樂觀；9/23 這種 15 檔漲停的日子，有 13 檔會被當成可買
- **修法建議**：台股改用 PriceLimitModel.calc_limits_for_pct 的檔位取整（向量化時可重用 pipeline._taiwan_tick_size_expr），或讓 numpy_limit_price／polars_limit_price 接受 is_etf 並對 .TWSE/.TPEX 走檔位邏輯；同一處修正也能涵蓋盤中路徑（N5 修法的新面向）
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/price_limits.py:242-259（numerator=rint((1±pct)*100)、cents=floor(prev*100+0.5)）
  - backend/app/price_limits.py:133-138（polars 版同一公式）
  - backend/app/backtest/matrix.py:2189,2224-2238（up_price = numpy_limit_price(reference, limit_pct)，locked = raw ≥ up_price−0.005）
  - backend/app/backtest/matrix.py:1014（_build_market_data_matrix_from_dataset 呼叫）
  - backend/app/backtest/engine.py:544（load_market_data_matrix_from_parquet）
- **線上證據**：
  - https://www.cnyes.com/twstock/2454：9/23 昨收 5180、漲停價 5695.00、跌停價 4665.00（A 股進位會得到 5698/4662）
  - https://efrontrade.com/2026/06/limit-up-limit-down-price-taiwan-stock.html：「漲停價要往下找最接近、且不超過 10% 上限的合法價格」
- **本地計算證據**：
  - 直接呼叫 app 的 _limit_lock_matrices，輸入 9/22、9/23 的收盤：官方 15 檔漲停中只有 4770、6243 被標成 up_locked（2/15），6225 跌停沒有被標記（0/1）
  - 櫃買 9/24 官方 10% 標的共 891 列，polars_limit_price 算出的價格有 760 列與官方不同（85%），例：1264 參考價 244.5，官方 268.5/220.5，A 股進位得 268.95/220.05
  - 9/23 MIS：2454 的 A 股進位值為 5698/4662，MIS u/w 為 5695/4665
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P1
    - 修正後敘述：numpy_limit_price／polars_limit_price 對台股用 A 股「0.01 元四捨五入」算漲跌停價，沒有依台股檔位取整（漲停向下、跌停向上），約 86% 的漲停價與 78% 的跌停價和官方不同，而且計算值一定落在官方漲跌停價之外，所以只會漏判、不會誤判。回測的 _limit_lock_matrices（matrix_native／composite 路徑）用這個價格判斷鎖定，並捨棄 enriched 已存的正確訊號，導致絕大多數台股漲跌停鎖定偵測不到。回測引擎只在「一字板」K 棒才依鎖定旗標擋單，所以實際影響是：本地 2024-01～2026-09 的一字漲停有 1,977/2,303 格（85.8%）、一字跌停有 1,857/2,406 格（77.2%）被當成可成交，回測會在實際買不到或賣不掉的價位成交，績效偏樂觀（例：9/23 的 6225 一字跌停在回測中可以賣出）。收在漲停但盤中有成交的 K 棒，就算旗標正確，引擎本來也允許買進，所以原 finding 說「9/23 的 15 檔漲停有 13 檔會被當成可買」與這個 bug 無關（那 15 檔都不是一字板）。修法：台股改走 PriceLimitModel.calc_limits_for_pct 或 _taiwan_tick_size_expr 的檔位取整，並預留千金股升降單位預計 2027-07 由 5 元改為 1 元的日期分段。
    - 理由：核心機制成立：numpy_limit_price 和 polars_limit_price 對台股也用 A 股「整數分、四捨五入到 0.01 元」算漲跌停價，沒有依台股檔位取整（漲停向下、跌停向上）。回測矩陣的 _limit_lock_matrices 直接用這個價格判斷漲跌停鎖定，而且會忽略 enriched 分區裡已存的 signal_limit_up／signal_limit_down（matrix.py:861-872）。因為 A 股算法的漲停價永遠 ≥ 官方價、跌停價永遠 ≤ 官方價，兩者不同時就一定漏判，不會誤判：本地 2024-01-02～2026-09-23 共 1,423,597 列，程式標記「漲停鎖定」的只有 3,281 格，依檔位算法實際有 23,317 格，誤判為 0。正確定義有兩項證據支持：一是不同於原 finding 的線上來源（winvest.tw），二是官方 MIS 與櫃買的資料。repo 內 PriceLimitModel.calc_limits_for_pct 與 enriched_refresh._add_limit_signals 也已經用了正確的檔位規則。

但 finding 的使用者影響寫錯了，嚴重度描述也誇大。回測引擎擋單的條件是「鎖定旗標 AND 一字板（開高低收幾乎相等）」（engine.py:917-937、1842-1864），收在漲停但盤中有成交的 K 棒，就算旗標正確，引擎本來也照樣允許買進。9/23 官方 15 檔漲停全部不是一字板，所以「13 檔會被當成可買」與這個 bug 無關：修正後這 15 檔一樣可以買。那一天真正受影響的是 6225 一字跌停（46.20），程式沒標記，回測會允許賣出。實際影響只在一字板，但比例高：全歷史一字漲停 2,303 格漏掉 1,977 格（85.8%），一字跌停 2,406 格漏掉 1,857 格（77.2%）。這些 K 棒在回測裡會在實際買不到或賣不掉的價位成交，績效因此偏樂觀。

漲跌停價算錯屬 CONTRIBUTING 第 11 節定義的「錯誤金融口徑」，維持 P1。影響只限回測（matrix_native／composite 路徑；grep 命中 21 個內建策略檔含 matrix_native），盤後資料與看盤畫面不受影響。沒有證據顯示使用者目前跑過回測：data/backtest_results 是空的。
    - 證據：
      - 程式碼：backend/app/price_limits.py:256-258（numerator=np.rint((1±pct)*100)、cents=floor(prev*100+0.5)、(cents*numerator+50)//100/100，沒有檔位邏輯）；polars 版在同檔 :133-138，公式相同
      - 程式碼：backend/app/backtest/matrix.py:2167-2243 是 _limit_lock_matrices；:2224-2225 up/down_price = numpy_limit_price(reference, limit_pct)；:2233-2238 locked = raw ≥ up−0.005／raw ≤ down+0.005。呼叫點在 :1014-1023（直讀路徑）與 :1174-1185（memmap 快取路徑）；:861-872 把 signal_limit_up／signal_limit_down 列入 ignored，已存的正確訊號不會被採用
      - 程式碼：backend/app/backtest/engine.py:544 呼叫 load_market_data_matrix_from_parquet；engine.py:917-928 與 :1842-1855 的 _one_price_limit 要求旗標與 same（max−min ≤ max(close*1e-4, 0.01)）同時成立才擋單，也就是只擋一字板
      - 程式碼：正確規則已存在於 backend/app/taiwan/market_rules.py:217-237（漲停 floor、跌停 ceil 到檔位）、:309-323（calc_limits_for_pct），以及 backend\app\taiwan\enriched_refresh.py:157-190（_add_limit_signals 用相同的檔位規則）
      - 線上（與原 finding 不同的來源）：https://winvest.tw/Knowledge/Article/348（2025/11/30）原文「向下(漲停)或向上(跌停)調整到最接近、但仍不超過限制的價格」；範例「漲停：80.52 → 無法報價(非 0.1 的倍數) → 調整為 80.50；跌停：65.88 → … 調整為 65.90」；升降單位表為 <10:0.01／10-50:0.05／50-100:0.1／100-500:0.5／500-1000:1／≥1000:5
      - 官方參考資料 mis_snapshot.csv（9/23）：2454 y=5180 u=5695 w=4665，A 股算法得 5698/4662，檔位算法得 5695/4665；6488 y=971 u=1065 w=874，A 股 1068.1/873.9，檔位 1065/874；0050 y=111.85 u=123.0 w=100.7，A 股 123.04/100.67，檔位 123.0/100.7
      - 官方參考資料 tpex_daily_quotes_20260923.csv（next_day_*）：10% 標的 903 列（我的篩選：排除無漲跌幅限制的列、上下幅度都在 8%～10% 之間），A 股算法有 770 列與官方不同（85.3%）；PriceLimitModel 只有 8 列不同，全部是 02xxxx ETN 或 7xxxxx 權證。1264 參考價 244.5，官方 268.5/220.5，A 股 268.95/220.05（原 finding 寫 891/760，篩選方式不同，結論一致）
      - 本地計算：scratchpad\audit\work\verify_tick_limit\lock_check.py 直接呼叫 app 的 _limit_lock_matrices，資料是 kline_daily_enriched 全部 661 個分區。輸出：A 股漲停價與檔位價不同的比例 85.7%，跌停 77.5%；鎖定漲停 correct=23317、app=3281、app 誤判=0；一字漲停 correct=2303、漏判 1977；一字跌停 correct=2406、漏判 1857
      - 本地計算（9/23）：依檔位規則漲停 15 檔，與官方 updown_counts『388(15)』相符；app 只標記 4770、6243（2/15），這 15 檔一字板數量為 0；跌停一字板 6225（O=H=L=C=46.20）app=0
      - 檔位規則近期會變動（修法時要注意）：https://www.ctee.com.tw/news/20260825701782-430201 等報導指出，證交所 2026-08-25 董事會通過千金股升降單位由 5 元降為 1 元，預計 116 年 7 月上路；目前 TickSizeModel.get_tick_size 的 trade_date 參數沒有用到（market_rules.py:188-191）
      - 附帶觀察（原 finding 未提，未深入驗證）：9/23 除息的 1442、2542 被 app 標成跌停鎖定，但 TWT49U 的官方跌停是 25.9、37.35，收盤分別 27.80、39.55，推測是除息日參考價沒換成除息參考價造成的誤判，屬另一個問題；另外 enriched 分區在 2026-09 以前沒有 signal_limit_up 欄位（stored_signal_by_month.py），polars_expr 路徑也不能當作正確的替代來源
  - **reproduction**：partially_confirmed；修正後嚴重度 P1
    - 修正後敘述：numpy_limit_price／polars_limit_price 用 A 股「整數分四捨五入」算漲跌停價，沒有依台股檔位做漲停向下、跌停向上取整；回測矩陣的 _limit_lock_matrices（matrix.py:2224-2238）直接用它產生 limit_up_locked／limit_down_locked，而 21 個內建策略全部走這條 matrix_native 路徑。官方資料證實價格差異很普遍：櫃買 9/24 約 10% 的標的有 85% 不符，例如 1264 官方 268.5/220.5，算成 268.95/220.05；2454 官方 5695/4665，算成 5698/4662。引擎只在「鎖定旗標為真且當日一字板」時擋單，所以實際影響集中在一字板的日子。本地 2024-01～2026-09 資料中，一字漲停漏判 1,977/2,303 次（86%），回測會在實際買不到的日子成交買進；一字跌停漏判 1,857/2,406 次（77%），回測會在實際賣不掉的日子成交賣出。自選股例子：2454 在 2026-09-01 開高低收都是 4315，是一字漲停，沒有擋買；6225 在 9/23 一字跌停 46.20，沒有擋賣。原 finding 說 9/23 的 15 檔漲停有 13 檔被誤當成可買，這不正確：那 15 檔都不是一字板，漲停價就算算對，引擎照樣允許買進。資料庫存檔的台股 signal_limit_up／down 由 enriched_refresh 依檔位計算，是正確的；錯誤只在回測矩陣重新計算這一步，以及已知的盤中路徑 N5（pipeline.py:1794）。
    - 理由：機制與數值差異都重現了，但 finding 對使用者影響的描述不正確，所以判為部分成立。

一、重現的部分：
- numpy_limit_price 與 polars_limit_price 都用 A 股「整數分四捨五入」算漲跌停價，沒有照台股檔位取整。
- 回測的 _limit_lock_matrices（matrix.py:2224-2238）直接拿這個價格判斷是否鎖漲跌停。
- 官方資料比對數字如下：
  - MIS：2454 算成 5698/4662，官方 5695/4665；6488 算成 1068.1/873.9，官方 1065/874；0050 算成 123.04/100.67，官方 123.0/100.7。
  - 櫃買 9/24 漲跌幅約 10% 的有 912 列，其中 778 列不符（85.3%）。只看 4 碼個股是 889 列中 759 列不符（85.4%）。finding 寫 891 列中 760 列，比例一致，列數差異來自篩選條件不同。
  - 1264：參考價 244.5，官方 268.5/220.5，算成 268.95/220.05，完全重現。
- 直接呼叫 app 的 _limit_lock_matrices，輸入官方 9/22、9/23 收盤：
  - 官方 15 檔漲停中只有 4770、6243 被標為鎖漲停（2/15）。
  - 6225 跌停沒有被標記（0/1）。
  - 與 finding 完全相同。

二、需要更正的部分：
1. 引擎並不是只要鎖漲停就擋買。`_one_price_limit` 要求同時滿足「鎖定旗標」與「開高低收同價（一字板）」，才會擋買或擋賣（engine.py:917-938、1842-1873）。收在漲停、但盤中曾在較低價成交的日子，就算漲停價算對，引擎也照樣允許成交；這是設計如此。
2. 9/23 那 15 檔漲停沒有一檔是一字板，開盤都低於漲停。因此漲停價就算算對，回測仍會讓 15 檔全部可買。「13 檔會被誤當成可買」的說法不成立。9/23 真正受影響的是 6225 天瀚：開高低收都是 46.20，昨收 51.30，是一字跌停；A 股算法得到 46.17，沒有標記鎖定，回測會讓它在這天賣出成交。
3. 真正的影響範圍是一字板的日子。我用 app 的 _limit_lock_matrices 掃本地 kline_daily_enriched（2024-01-02～2026-09-23，661 天、2,346 檔），另一次把 numpy_limit_price 換成檔位版本對照：
   - 一字漲停：檔位版 2,303 次，A 股版 326 次，漏判 1,977 次（86%）。
   - 一字跌停：檔位版 2,406 次，A 股版 549 次，漏判 1,857 次（77%）。
   - 自選股漏判 12 次。其中 2454 在 2026-09-01 經官方 STOCK_DAY 查證：開高低收都是 4315，昨收 3925，官方漲停價 4315；A 股算法得到 4317.5，所以沒有擋買。
4. 資料庫裡存的台股 signal_limit_up／signal_limit_down 由 TaiwanEnrichedRefreshService._add_limit_signals 產生（enriched_refresh.py:157-189），有照檔位取整；9/23 存檔值 15 檔漲停全部標對。所以錯誤只出在回測矩陣重新計算的路徑。不過 21 個內建策略全部是 matrix_native，一般台股回測都會走這條路徑。polars_limit_price 對台股的影響，在 pipeline.py:1794（盤中路徑，已知的 N5）；pipeline.py:783 算出的訊號則會被台股 enriched refresh 覆寫。

嚴重度維持 P1：這是錯誤的金融口徑，台股回測大約八成的一字漲停日會被當成可以買到，一字跌停日會被當成可以賣出，績效偏樂觀。這只影響回測，不影響實際下單，所以不到 P0。

三、驗證範圍：
- 線上來源（cnyes、efrontrade）沒有另外抓取，改用官方 MIS 與 TPEx 參考資料驗證同一批數值。
- 稽核期間工作樹沒有變化：git status 與開始時相同，security_master.parquet 的修改時間仍是 9/17。
    - 證據：
      - backend/app/price_limits.py:256-258：numerator=np.rint((1±pct)*100)、cents=np.floor(prev*100+0.5)、(cents*numerator+50)//100/100（A 股整數分四捨五入）；polars 版 133-138 同一公式（實跑兩者 912 列結果完全相同）
      - backend/app/backtest/matrix.py:2224-2238：up_price=numpy_limit_price(...)，up_locked=raw>=up_price-0.005；1014 在 _build_market_data_matrix_from_dataset 呼叫；1174 的快取路徑也呼叫；engine.py:544 呼叫 load_market_data_matrix_from_parquet
      - backend/app/backtest/engine.py:917-938（另一份在 1842-1873）：_one_price_limit 要求 flags 為真且 max(OHLC)-min(OHLC)<=max(close*1e-4,0.01)，_can_buy／_can_sell 只在一字板時擋單
      - backend/app/strategy/builtin/*.py：21 個內建策略都是 EXECUTION_BACKEND="matrix_native"（以 grep 確認）
      - backend/app/taiwan/enriched_refresh.py:157-189：存檔的台股漲跌停訊號依檔位 floor/ceil，正確；本地 kline_daily_enriched date=2026-09-23 的 signal_limit_up 15 檔全部為 true
      - scratchpad\audit\work\verify-limtick\e_mis.py 輸出：2454 y=5180 MIS 5695/4665、A 股版 5698/4662、檔位版 5695/4665；6488 y=971 MIS 1065/874、A 股版 1068.1/873.9；0050 y=111.85 MIS 123.0/100.7、A 股版 123.04/100.67
      - scratchpad\audit\work\verify-limtick\a_tpex.py 輸出（tpex_daily_quotes_20260923 的次日參考價與漲跌停價）：約 10% 的列有 912 列，A 股版不符 778 列（漲停 778、跌停 707），其中漲停價偏高 770 列；4 碼個股 889 列中不符 759 列；1264 參考價 244.5，官方 268.5/220.5，A 股版 268.95/220.05，檔位版 268.5/220.5；檔位版不符的 16 列全部是權證或 ETN
      - scratchpad\audit\work\verify-limtick\b_twse.py 輸出：用檔位規則從 MI_INDEX 9/23 的收盤減漲跌價差反推，得到 15 檔漲停、1 檔跌停，與官方漲跌家數「上漲(漲停) 388(15)、下跌(跌停) 562(1)」一致；app 的 _limit_lock_matrices 鎖漲停 2/15（4770、6243），鎖跌停 0/1（6225）；15 檔漲停的 one_price 全部為 false，6225 的開高低收都是 46.2，one_price 為 true
      - scratchpad\audit\work\verify-limtick\c_history.py 輸出（kline_daily_enriched，2024-01-02～2026-09-23，2,346 檔，1,423,597 列）：收在漲停，檔位版 23,317 次、A 股版 3,281 次；一字漲停，檔位版 2,303 次、A 股版 326 次、漏判 1,977 次；一字跌停，檔位版 2,406 次、A 股版 549 次、漏判 1,857 次；A 股版多判 0 次
      - f_watch.py 輸出：自選股一字板漏判 12 次，包括 2025-04-07 的 2454、3034、3545、00878 跌停，2025-04-10 的 0050、2454、3034 漲停，以及 2454 在 2026-05-04、05-05、05-25、07-31、09-01 的漲停
      - reference csv\twse_stock_day_202608_202609.csv：2454 在 2026-08-31 收 3925；2026-09-01 開高低收都是 4315（+390），是一字漲停；A 股版漲停價 4317.5，沒有標記鎖定

### [P2 → P2] 已儲存的 consecutive_limit_ups/downs（連續漲停/跌停）與同一列的 signal_limit_up 不一致（部分成立）

- **主張**：enriched_refresh 先呼叫 compute_enriched → compute_limit_signals（A 股 0.01 元進位）算出 signal_limit_up 與 consecutive_limit_ups，之後 _add_limit_signals 只用檔位正確的算法覆蓋 4 個 signal 欄，連板數沒有重算
- **正確的定義或數值**：連續漲停天數應以與 signal_limit_up 相同、依檔位取整的判定來累計
- **app 實際行為**：連板數多數為 0，只有少數格線吻合的個案會累計
- **對使用者的影響**：自訂訊號與監控規則的「連續漲停／連續跌停」欄位（signals.py:80 行情組），以及回測因子 limit_up_count_20d/60d（matrix.py:4026-4031）都會嚴重低估
- **修法建議**：在 _add_limit_signals 之後，用新的 signal_limit_up/down 以同樣的 cum_sum 分組方式重算 consecutive_limit_ups/downs，或直接修正 finding 1 的根因；修正後需要用 --full 重新發佈歷史分區
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/enriched_refresh.py:95-98
  - backend/app/taiwan/enriched_refresh.py:163,182-189（只覆蓋 4 個 signal 欄）
  - backend/app/indicators/pipeline.py:782-791,839-859,875-895
- **線上證據**：
  - https://www.cnyes.com/twstock/2454（台股漲停價依檔位取整的第三方實例）
- **本地計算證據**：
  - 9/23 官方漲停 15 檔都是 signal_limit_up=True，但其中 13 檔 consecutive_limit_ups=0（例 3653 健策 5830→6410、2368 金像電 1040→1140）；只有 4770、6243 兩檔剛好落在 0.01 元格線上才有值
  - 9/23 跌停 6225 天瀚：signal_limit_down=True，但 consecutive_limit_downs=0
  - 有連板欄的 6 個分區中，signal_limit_up=True 共 344 列，其中 193 列連板＝0；signal_limit_down=True 共 39 列，其中 7 列連跌＝0
  - 661 個分區中只有 6 個有 consecutive 欄、10 個有 signal_limit_* 欄（最早是 2026-09-10），更早的分區沒有這些欄
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：台股盤後發佈（enriched_refresh.py:95-98）的流程有這個問題。股本表被傳進 compute_enriched，因此 compute_limit_signals 會以 A 股 0.01 元四捨五入的漲跌停價（price_limits.py:133-138）算出 signal 與連板數。之後 _add_limit_signals 只以台股檔位取整（漲停向下、跌停向上）覆蓋 4 個 signal 欄，consecutive_limit_ups/downs 沒有重算。

結果是已儲存的連板數與同列的 signal 不一致，而且只會低估：只有剛好落在 0.01 元格線上的漲跌停日才會累計。

在有連板欄的 6 個分區（2026-09-16 至 09-23）中：
- signal_limit_up=True 共 258 列，其中 193 列（約 75%）連板數為 0。
- signal_limit_down=True 共 13 列，其中 7 列為 0。
- 例：6225 在 9/22、9/23 連兩天跌停，連跌數都記為 0，正確應為 1 與 2。

受影響的是讀已儲存欄位的回測與策略，包括因子 limit_up_count_20d/60d，以及引用 consecutive_limit_ups 的 AI 或自訂策略（strategy-guide 建議用它判斷漲停）。純台股部署的 runtime 快取與監控路徑不讀這兩欄，使用者目前也沒有監控規則或自訂訊號，所以現況下屬於潛在影響。

修正要讓連板數改用與 signal 相同、依檔位取整的判定來累計。修正後才能用 --full 重新發佈歷史分區，否則會把低估的連板數寫進整段歷史。
    - 理由：主張的機制與資料都成立，但有兩處要修正。

一、程式碼（以目前行號為準）
1. enriched_refresh.py:95 呼叫 compute_enriched 時，傳入的 instruments 是股本表（as_instruments 只有 symbol 與 float_shares 兩欄，見 share_capital_store.py:172-175）。compute_all 在 pipeline.py:961-962 只要看到 instruments 不是空的，就以 needed=None 呼叫 compute_limit_signals，把 LIMIT_SIGNAL_OUTPUTS 全部算出來，其中包括 consecutive_limit_ups/downs（pipeline.py:588-596）。
2. compute_limit_signals 用 polars_limit_price 算漲跌停價（pipeline.py:782-791）。這個函式（price_limits.py:133-138）是以分為單位、四捨五入到 0.01 元，沒有依台股檔位取整。股本表沒有 limit_up 欄，所以一定採用理論價（pipeline.py:802-809）。
3. 連板數在 pipeline.py:839-859 與 875-895，用這個 A 股口徑的 signal 做 cum_sum 分組算出。
4. 之後 enriched_refresh.py:98 呼叫 _add_limit_signals。它在 :163 與 :182-189 用檔位取整（:174-175，漲停向下取整、跌停向上取整）重算 4 個 signal 欄，但沒有重算 consecutive_*。
5. 這條路徑是 commit ebcce1a（2026-09-22）開始把股本表傳進 compute_enriched 時才出現的副作用。git show 可以看到原本是 compute_enriched(raw, factors=pl.DataFrame())。enriched_refresh.py:9-10 的模組說明仍寫 limit-board signals stay absent，已經與現況不符。

二、正確定義（與 cnyes 不同的來源）
1. efrontrade.com 寫明：「漲停價要往下找最接近、且不超過 10% 上限的合法價格。跌停價要往上找最接近、且不低於 10% 下限的合法價格」。範例是基準價 40.60，理論漲停 44.66，實際漲停 44.65；1000 元以上的檔位是 5 元。
2. 官方本地參考資料也支持這個規則。TPEx 9/23 的 next_day_limit_up/down 中，889 檔四位數普通股：
   - 檔位取整（漲停向下、跌停向上）：漲停與跌停各 888 檔吻合。唯一不符的 7856 是 9995 無漲跌幅的哨兵值。
   - 0.01 元四捨五入：漲停只有 130 檔吻合，跌停只有 198 檔吻合。
3. TWSE 9/23 的 MI_INDEX 顯示 3653 收在 6410，委買 607 張、沒有委賣；2404 收在 1250 並鎖漲停。兩者都證實 9/23 千元以上的檔位仍是 5 元（6413 取整為 6410，1254 取整為 1250）。

三、本地實跑
1. 我在 scratchpad 重現：用實際 parquet 的 OHLC 依序跑 compute_indicators、compute_signals、compute_limit_signals，再跑 _add_limit_signals。為了不載入 Security Master，我預先填好涨跌幅快取並改寫 _limit_profile。
2. 結果：3653、2368、1235 在 pipeline 算出的 signal 是 False，覆蓋後變 True，但連板數仍是 0。4770（250 到 275）和 6243（37.5 到 41.25）剛好落在格線上，連板數是 1。
3. 6225 在 9/22 與 9/23 連兩天跌停，signal_limit_down 都是 True，但連跌數兩天都是 0，正確值應為 1 與 2。這個例子說明整段連續紀錄都會漏掉，原記錄沒有提到。
4. 重現結果與已儲存的分區逐列比對 36 列，0 列不符。
5. 反方向（連板數大於 0 但 signal 是 False）在 6 個分區中是 0 列。所以連板數是嚴格低估，不會高估。

四、需要修正的地方
1. 統計數字的分母混用了。344 與 39 是 10 個有 signal 欄的分區總數，其中 9/10 到 9/15 那 4 個分區沒有 consecutive 欄。在 6 個有連板欄的分區中：
   - signal_limit_up=True 共 258 列，其中 193 列連板數為 0，約 75%。
   - signal_limit_down=True 共 13 列，其中 7 列連跌數為 0。
2. 使用者影響寫得過寬。
   - 監控規則與盤中、盤後的自訂訊號不會讀到已儲存的值。data/instruments 是空的，所以 repository.refresh_cache 只讀基礎欄位重算，而且跳過 compute_limit_signals（repository.py:571-605）。get_enriched_today 走的是這份快取或 compute_enriched_today（pipeline.py:1686-1687，同樣要求 instruments）。
   - 使用者目前的監控規則是 0 條，custom_signals 目錄也是空的。自選頁台股走台股專屬路徑，不讀這兩欄。
   - 實際會讀到已儲存值的，是回測與策略的 load_panel（engine.py:355-391），例如因子 limit_up_count_20d/60d（factor.py:433-437、matrix.py:4026-4031、scoring.py:203-206），以及引用 consecutive_limit_ups 的 AI 或自訂策略。strategy-guide.md:265 還明文建議用 consecutive_limit_ups >= 1 當「穩定」的漲停判斷。
   - 目前 661 個分區中只有 9/16 到 9/23 這 6 個有連板欄，更早的是 null。所以在現況下，歷史回測主要受「欄位缺失」影響。若照修法建議先用 --full 重新發佈而沒有同時修正，整段歷史都會寫進低估的連板數，因此重新發佈必須在修正之後。

五、嚴重度
維持 P2：這是明確的功能錯誤，但在使用者目前的設定下屬於潛在影響，沒有進入核心流程，不構成 P1 的核心流程不可用。

六、不屬本 finding 的旁證
9/23 的 1442、2542 在官方 MI_INDEX 中漲跌欄為 X（除權息），卻被標成 signal_limit_down=True 且連跌數為 1。這是參考價的問題，另案處理。

七、重複性檢查
docs/code-review-2026-09-22.md 的 N5 講的是盤中路徑，B5 講的是 distance 欄。本 finding 是盤後已儲存欄位與 signal 不一致，屬於新面向。
    - 證據：
      - backend/app/taiwan/enriched_refresh.py:90-98：instruments=share_capital.as_instruments()，先呼叫 compute_enriched，再呼叫 _add_limit_signals
      - backend/app/taiwan/share_capital_store.py:172-175：as_instruments 只輸出 symbol 與 float_shares，沒有 limit_up/limit_down
      - backend/app/indicators/pipeline.py:961-962：instruments 不是空的就以 needed=None 呼叫 compute_limit_signals；:588-596 的 LIMIT_SIGNAL_OUTPUTS 包含 consecutive_limit_ups/downs
      - backend/app/price_limits.py:133-138：polars_limit_price 以分為單位、四捨五入到 0.01 元，不依檔位取整；pipeline.py:782-791 用它算理論價，:802-809 在沒有 limit_up 欄時一律採用理論價
      - backend/app/indicators/pipeline.py:839-859、875-895：consecutive_limit_ups/downs 以 A 股口徑的 signal 做 cum_sum
      - backend/app/taiwan/enriched_refresh.py:163、174-175、182-189：只以檔位取整重算 4 個 signal 欄，沒有重算 consecutive_*；:9-10 的模組說明仍寫 limit-board signals stay absent，已與現況不符
      - git show ebcce1a（2026-09-22）：原本是 compute_enriched(raw, factors=pl.DataFrame())，改成傳入 instruments 之後才開始產生 consecutive 欄
      - 線上：https://efrontrade.com/2026/06/limit-up-limit-down-price-taiwan-stock.html 原文「漲停價要往下找最接近、且不超過 10% 上限的合法價格。跌停價要往上找最接近、且不低於 10% 下限的合法價格」；範例 40.60 對應理論漲停 44.66、實際 44.65，理論跌停 36.54、實際 36.55；1000 元以上升降單位 5 元
      - 官方本地資料 reference/csv/tpex_daily_quotes_20260923.csv：889 檔四位數普通股的 next_day_limit_up/down，檔位向下／向上取整各 888 檔吻合（唯一不符的 7856 是 9995 哨兵值），0.01 元四捨五入漲停只 130 檔、跌停只 198 檔吻合
      - 官方本地資料 reference/csv/twse_mi_index_20260923_quotes.csv：3653 收 6410，委買 6410 共 607 張、沒有委賣；2404 收 1250，委買 262 張、沒有委賣，證實千元以上檔位為 5 元；updown_counts 的股票漲停 15 檔與 parquet 中 signal_limit_up 的 15 檔一致
      - 指令：uv run python scratchpad/audit/work/verify_consec_defcode/check_parts.py，輸出為 661 個分區；有 consecutive 欄的 6 個（2026-09-16 至 09-23）；有 signal 欄的 10 個（2026-09-10 起）；在 6 個分區中 signal_limit_up=True 共 258 列、連板數為 0 的 193 列，signal_limit_down=True 共 13 列、連跌數為 0 的 7 列；反方向（連板數大於 0 但 signal 為 False）為 0 列
      - 指令：同目錄的 count 腳本，10 個 signal 分區的 signal_limit_up 合計 344 列、signal_limit_down 合計 39 列（即原 finding 的分母，混用了沒有 consecutive 欄的 4 個分區）
      - 指令：uv run python scratchpad/audit/work/verify_consec_defcode/repro.py，實際呼叫 compute_limit_signals 與 _add_limit_signals：3653、2368、1235 的 pipeline signal 為 False，覆蓋後為 True，連板數為 0；4770、6243 連板數為 1；6225 在 9/22 與 9/23 的 signal_limit_down 都是 True，連跌數都是 0；與已儲存分區逐列比對 36 列、0 列不符；polars_limit_price(5830,0.1) 回傳 6413.0，官方為 6410
      - backend/app/repository.py:571-605：runtime 快取只讀基礎欄位；instruments 是空的（data/instruments 目錄為空）時跳過 compute_limit_signals，所以監控與自選不讀已儲存的連板數；quote_service.py:514-521 的 get_enriched_today 就是讀這份快取
      - backend/app/backtest/factor.py:433-437、engine.py:355-391、matrix.py:4026-4031、strategy\scoring.py:203-206：回測與因子直接讀已儲存的 consecutive_limit_ups；strategy\prompts\strategy-guide.md:265 建議用 consecutive_limit_ups >= 1 判斷漲停
      - data/user_data/custom_signals 與 monitor_rules 都是空的；strategy/builtin 內建策略沒有引用 consecutive_limit_ups 或 limit_up_count
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：enriched_refresh 先透過 compute_enriched → compute_limit_signals，以 A 股 0.01 元四捨五入的漲跌停價，算出 signal_limit_up/down 與 consecutive_limit_ups/downs。之後 _add_limit_signals 只用台股檔位取整覆蓋 4 個 signal 欄，連板數沒有重算。

結果是已儲存的連板數逐列等於 0.01 元口徑的結果（11895/11895），同一列的訊號卻是檔位口徑的結果（11895/11895）。只有 0.01 元漲跌停價剛好落在檔位格線上的個案（例如 4770、6243）才會累計。

數字（更正分母）：
- 6 個有連板欄的分區（2026-09-16～09-23）中，signal_limit_up=True 共 258 列，其中 193 列（74.8%）連板數為 0；依檔位重算應有 198 列不同，全部低估。
- 同一範圍內 signal_limit_down=True 共 13 列，其中 7 列連跌數為 0；應有 8 列不同，全部低估。
- finding 原本寫的 344／39，是 10 個有 signal_limit_* 欄的分區合計。

9/23 的個案都重現了：
- 官方 15 檔漲停全部 signal_limit_up=True，其中 13 檔連板數為 0。
- 6225 天瀚跌停，連跌數為 0（依檔位應為 2）。

影響：
- 回測因子 limit_up_count_20d/60d 會漏算。
- 自訂訊號行情組的「連續漲停／連續跌停」會低估。
- 盤中即時連板數以昨日已儲存值加 1，也會繼承低估。
- 此使用者的自選 9 檔在這段期間沒有漲跌停、監控規則 0 條，目前沒有直接受影響的個股。
    - 理由：主張的核心機制與 9/23 的個案都能用數據完整重現，只有一組「6 個分區內的分母」數字標錯，所以判定為部分確認。

機制（程式碼）：
- enriched_refresh.py:90 傳給 compute_enriched 的 instruments 只有 symbol 與 float_shares（share_capital_store.py:172-175），非空，所以會進 compute_limit_signals（pipeline.py:962）。
- compute_limit_signals 用 polars_limit_price 算漲跌停價（price_limits.py:133-138）。這是把價格四捨五入到 0.01 元的整數分算法（A 股口徑），然後用 cum_sum 分組算出 consecutive_limit_ups/downs（pipeline.py:839-859、875-895）。
- 之後 _add_limit_signals（enriched_refresh.py:163、182-189）依台股檔位重算，但只覆蓋 signal_limit_up、signal_limit_down、signal_broken_limit_up、signal_limit_down_recovery 這 4 欄，連板數沒有重算。

數據重現：我自己實作了兩套規則，套在 data/taiwan/daily 全部 661 個分區（1,423,597 列）上：一套複製 A 股 0.01 元的算法，另一套依台股檔位取整（漲停價向下取到檔位，跌停價向上取到檔位）。然後與已儲存的 enriched 比對。
- 已儲存的 consecutive_limit_ups 與 A 股複製版逐列相符：11895/11895。consecutive_limit_downs 也是 11895/11895。
- 已儲存的 signal_limit_up/down 與檔位版也是 11895/11895。
- 這證明同一列的訊號用的是檔位口徑，連板數用的卻是 0.01 元口徑。

與 finding 數字並列：
- 有 consecutive 欄的分區數：finding 6，我算 6（2026-09-16～09-23）。
- 有 signal_limit_* 欄的分區數：finding 10，我算 10（最早 2026-09-10）。
- 9/23 官方漲停檔數：finding 15。我從 MI_INDEX 用收盤價減漲跌價差反推參考價，再依檔位判定，得到 15 檔漲停、1 檔跌停，與官方漲跌家數表 updown_counts「388(15)」「562(1)」一致。這 15 檔全部 signal_limit_up=True，其中 13 檔連板數為 0。
- 3653：5830→6410，0.01 元算法的漲停價是 6413，連板數 0。2368：1040→1140，0.01 元算法是 1144，連板數 0。
- 4770（275.0）與 6243（41.25）的 0.01 元漲停價剛好等於檔位漲停價，所以連板數為 1。
- 6225 天瀚：signal_limit_down=True、連跌數=0，依檔位推算應為 2（9/22 也跌停）。
- 6 個分區內的漲停列：finding 寫「344 列中 193 列為 0」，我算 258 列中 193 列為 0。
- 6 個分區內的跌停列：finding 寫「39 列中 7 列為 0」，我算 13 列中 7 列為 0。
- 344／39 其實是 10 個有 signal_limit_* 欄的分區合計，不是 6 個有連板欄的分區，所以 finding 的分母標錯。正確比例是漲停 193/258＝74.8%、跌停 7/13 的連板數為 0。

依檔位重算後應有的值：
- 漲停：198 列不符，全部低估（193 列該是 ≥1 卻為 0，5 列少算）。在 258 列漲停中，應有的連板數分布是 1:199、2:46、3:8、4:5；已儲存的是 0:193、1:60、2:5。
- 跌停：8 列不符，全部低估。

影響面：
- 回測因子 limit_up_count_20d/60d 只把 consecutive>0 的日子算成命中（matrix.py:4026-4031），所以會漏算。自訂訊號的行情組欄位也包含這兩欄（signals.py:79-80）。
- 我另外找到一個 finding 沒提的面向：盤中即時計算用昨日已儲存的連板數加 1 當作今日連板數（repository.py:870-892，pipeline.py:1880-1895），所以即時連板數也會繼承這個低估。
- 使用者目前實際受影響的範圍有限：監控規則 0 條；自選 9 檔在這 6 個分區內沒有任何漲跌停，檢查結果 0 列。維持 P2（明確的功能錯誤）。

範圍外的旁證（不影響本判定）：
- 9/23 的 1442 名軒、2542 興富發當天是除權息（MI_INDEX 的漲跌符號為 X），卻被標成 signal_limit_down=True，原因是 prev_close 沒有換成除權息後的參考價。
- 9/16 與 9/23 的 daily 分區只有 TWSE、沒有 TPEX。
    - 證據：
      - backend/app/taiwan/enriched_refresh.py:95-98 先呼叫 compute_enriched，再呼叫 _add_limit_signals；:163 與 :182-189 只覆蓋 4 個 signal 欄
      - backend/app/taiwan/share_capital_store.py:172-175 as_instruments 回傳非空的 symbol/float_shares，因此 pipeline.py:962 會執行 compute_limit_signals
      - backend/app/price_limits.py:133-138 polars_limit_price 用整數分四捨五入（0.01 元）；pipeline.py:782-791、826-859、862-895 以此算 signal 與連板數
      - 腳本：AUDIT_SCRATCH/work/verify-consec/verify.py；輸出：同目錄 out.txt（執行方式：cd backend && uv run python verify.py，結束碼 0）
      - out.txt:35-38：已儲存的連板數 == A 股 0.01 元複製版，up 與 down 都是 11895/11895；已儲存的 signal == 檔位版，11895/11895；已儲存的連板數 == 檔位期望值，up 11697/11895、down 11887/11895
      - out.txt:8-9：6 個有連板欄的分區中，signal_limit_up=True 共 258 列（finding 寫 344），其中連板＝0 有 193 列（相符）；signal_limit_down=True 共 13 列（finding 寫 39），其中連跌＝0 有 7 列（相符）
      - 10 個有 signal_limit_* 欄的分區逐一加總：up 344、down 39，即 finding 的數字實際是這 10 個分區的合計
      - out.txt:41-63：258 列漲停中，檔位期望連板數分布是 1:199、2:46、3:8、4:5；已儲存的是 0:193、1:60、2:5
      - out.txt:84-122：9/23 由官方 MI_INDEX 推得 15 檔漲停（與 twse_mi_index_20260923_updown_counts.csv 的「388(15)」相符），全部 signal_limit_up=True，13 檔連板數為 0；3653 的 0.01 元算法漲停價 6413 對官方 6410，2368 是 1144 對 1140；4770 為 275.0＝275.0、6243 為 41.25＝41.25，因此連板數為 1；6225 連跌數已儲存 0、期望 2
      - backend/app/backtest/matrix.py:4026-4031 limit_up_count 只把 consecutive>0 算成命中；app\api\signals.py:79-80 的行情組包含 consecutive_limit_ups/downs
      - 新面向：backend/app/repository.py:870-892 以已儲存的連板數作為 _prev_consec_up/down，pipeline.py:1880-1895 盤中連板數＝昨日值+1，因此繼承低估
      - 使用者自選 9 檔在 9/16～9/23 無任何漲跌停或期望連板值大於 0（out.txt:151-158 為 0 列）；data\instruments 為空，repository.py:596 不會在記憶體重算，讀到的就是已儲存值

### [P2 → P2] 除權息日以前一日收盤價當漲跌停基準，產生假跌停並漏判真漲跌停（部分成立）

- **主張**：台股沒有提供 adj_factor，除權息日的漲跌停基準一律用前一日收盤，而不是官方的除權息參考價；影響儲存訊號、連跌數、市場概況的漲跌停家數與距漲跌停
- **正確的定義或數值**：除權息日的漲跌停應以官方除權息參考價為基準；均線、突破、新低等訊號應使用還原權息後的價格，或至少排除除權息缺口
- **app 實際行為**：用前一日收盤，高息股的除息缺口被當成下跌或跌停
- **對使用者的影響**：除息季會出現大量假跌停、假破線、假 60 日新低訊號；市場概況的跌停家數多算（9/23 為 3 對 1）；距漲跌停與連跌數也跟著錯
- **修法建議**：從 TWT49U（上櫃另有對應表）建立台股 adj_factor／除權息參考價表：漲跌停基準改用參考價，指標改用前復權價；至少在除權息日把漲跌停類訊號設為 null
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/enriched_refresh.py:95,169-175（factors=pl.DataFrame()，ref=prev_close）
  - backend/app/indicators/pipeline.py:755-763（沒有 adj 因子時用 raw_close.shift(1)）
  - backend/app/taiwan/market_intelligence.py:401-404,439-443（市場概況用前一日收盤計算漲跌停家數）
- **線上證據**：
  - https://www.cnyes.com/twstock/1442：9/23 成交 27.80、漲停價 31.60、漲跌 -0.95（-3.30%），即以除息參考價 28.75 為基準，並非跌停
  - https://efrontrade.com/2026/06/limit-up-limit-down-price-taiwan-stock.html：「除權除息的隔天，基準價會依除權息調整，不是直接沿用前一天收盤價。」
- **本地計算證據**：
  - TWT49U（6/1～9/23）上市除權息 884 筆：app 以前收算出的漲跌停價與官方不同 883 筆；官方除息日漲跌停以除息參考價依檔位取整，809/809 吻合
  - 已儲存的假跌停：5906 9/10、6924 9/15、1442 9/23（前收 31.15、收 27.80，app 以 28.05 判跌停；官方跌停 25.90）、2542 9/23（官方跌停 37.35，收 39.55）；1442、2542 的 consecutive_limit_downs 也記為 1
  - 以 MarketProfileBridge 重現市場概況邏輯：9/23 上市股票跌停，app 算 3 檔，官方 1 檔
  - 另有 33 筆除權息日實際觸及官方漲跌停（例 7610 7/13 收 2425＝官方漲停，app 依前收算的漲停為 2915），這些分區目前沒有訊號欄，重建後會漏判
  - 865 筆現金除息日中，未還原價格造成的額外空頭訊號（還原後就不成立）：ma5_breakdown 178、ma20_breakdown 187、n_day_low 112、boll_breakdown_lower 181、ma_dead_5_20 47（work/A05/exday_signal_gap.csv）
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：台股沒有提供除權息因子（enriched_refresh.py:95 傳入空的 factors），所以除權息日的漲跌停基準一律用前一日原始收盤，而不是官方的除權息參考價。受影響的位置：
- 已儲存的漲跌停類訊號：enriched_refresh.py:169-188。
- consecutive_limit_downs：pipeline.py:755-763、875-895。
- Dashboard 與選股頁的市場概況漲停／跌停家數：market_intelligence.py:401-404、439。

實測結果：
- 已存的假跌停為 5906 9/10、6924 9/15、1442 9/23、2542 9/23（後兩檔的連跌數也記為 1）。
- 9/23 上市股票跌停家數：app 3 檔，官方 1 檔。
- 6/1～9/23 的除權息日：假跌停 52 筆；官方收漲停 23 筆，app 漏判 22 筆。目前只有 9/10 以後的分區帶訊號欄，其餘要在全量重建時才會出現。

未還原價格也讓除息日多出均線、布林、60 日新低等空頭訊號，例如 ma5_breakdown 178 筆、ma20_breakdown 187 筆。專案 pipeline 的設計本來就是有因子時先前復權再算指標，台股只是因為沒有因子而退化。

更正兩點：
1. 「距漲跌停也跟著錯」證據不足。已存分區本來就沒有這兩欄（B5）；即時路徑的 prev_close 取自 MIS 的 y，除權息日是否正確未驗證。
2. 權／權息事件的官方漲跌停不是單純的參考價 ±10%。例如 7610 的漲停以僅除息參考價為基準，跌停以除權息參考價為基準。修法應直接採用 TWT49U（上櫃另有對應表）的官方漲停價與跌停價，不要自行以參考價重算。
    - 理由：主張成立。我重讀了引用的程式碼，行號與現況一致。
- 台股 enriched 發佈時傳入空的 factors（enriched_refresh.py:95）。
- _add_limit_signals 以 prev_close 當漲跌停基準（enriched_refresh.py:169-175），而 prev_close 由 close.shift(1) 產生（pipeline.py:388）。
- 沒有除權息因子時，pipeline.py 的 _adj_changed 永遠是 false，連跌數用的 _prev_raw_close 就是前一日原始收盤（pipeline.py:755-763）。
- 市場概況把前一交易日 close 當 prev_close（market_intelligence.py:401-404），再送進 calc_limits（market_intelligence.py:439）。

所以除權息日一律以前一日收盤為基準。

正確定義另找了與原 finding 不同的權威來源：金管會 2015-08-18 新聞稿說除權息參考價「並據以計算除權息當日交易之漲跌停價格範圍」。本地官方 TWT49U 的 limit_up／limit_down 也吻合：純除息 785/785 筆等於參考價 ±10% 依檔位取整。

實測結果：
- 前收基準與官方不同：876 筆可比對事件中有 875 筆。
- 除權息日收盤：app 判跌停 62 筆，官方只有 10 筆（多出 52 筆假跌停）；官方收在漲停 23 筆，app 只抓到 1 筆。
- 目前已儲存的假跌停正是 5906（9/10）、6924（9/15）、1442 與 2542（9/23），且 1442、2542 的 consecutive_limit_downs=1。
- 9/23 上市股票跌停家數：app 邏輯算出 3 檔，官方 MI_INDEX 為 562(1)。
- 均線類額外空頭訊號重算為 178／187／114／181／47，與 finding 的 178／187／112／181／47 幾乎相同。

使用者情境下會發生：Dashboard.tsx:282 與 TaiwanScreener.tsx:584 顯示漲停／跌停家數；StockDailyKChart.tsx:100 在日K圖標「跌停」；watchlist API 也回傳 signal_limit_down。

嚴重度維持 P2。錯誤只發生在除權息日；監控規則為 0 條，不會觸發告警；目前帶訊號欄的分區只有 9/10 以後的 10 個。也可以主張這屬於「錯誤金融口徑」的 P1，但影響面有限，P2 較恰當。

需要修正的細節：
1. 「距漲跌停也跟著錯」證據不足。已存分區本來就沒有這兩欄（已知 B5）；即時路徑 compute_enriched_today 優先用報價帶來的 prev_close，也就是 MIS 的 y。y 在除權息日是否等於參考價，我沒有除權息日的快照可驗，屬推測。只有 Yahoo 或日K回退路徑才會用到前收。
2. 權／權息事件不是單純「參考價 ±10%」：87 筆可比對中有 23 筆不符。例如 7610，漲停以僅除息參考價 2207.8 為基準，跌停以除權息參考價 2181.01 為基準。修法應直接採用 TWT49U（上櫃另有對應表）的官方 limit_up／limit_down。
3. 指標改用還原價屬於方法論選擇。不過 pipeline 的設計本身就是「有 adj_factor 時先前復權再算指標」（pipeline.py:13、1003），台股只是因為沒有因子而退化。
    - 證據：
      - 程式碼 backend/app/taiwan/enriched_refresh.py:95：compute_enriched(raw, factors=pl.DataFrame(), ...)；:169 ref = pl.col("prev_close")；:170-175 以 ref×(1±limit_pct) 依檔位取整；:183-188 產生 signal_limit_up／down、broken_limit_up、limit_down_recovery
      - 程式碼 backend/app/indicators/pipeline.py:388：prev_close = close.shift(1)；:755-763 在 _adj_changed 為 false 時 _prev_raw_close = raw_close.shift(1)（沒有因子時 close==raw_close，永遠 false）；:875-895 consecutive_limit_downs 以此累計
      - 程式碼 backend/app/taiwan/market_intelligence.py:401-404：把 prev_daily 的 close 當作 prev_close；:439 MarketProfileBridge.calc_limits(prev_close, inst)；:257-258 計算 limit_ups／limit_downs
      - 前端 frontend/src/pages/Dashboard.tsx:282 顯示 upper_limit_count / lower_limit_count；frontend/src/pages/TaiwanScreener.tsx:584 顯示「N 跌停」；frontend/src/components/StockDailyKChart.tsx:100 在 signal_limit_down 時畫「跌停」標記
      - 線上來源（與 finding 引用的不同）：https://www.fsc.gov.tw/ch/home.jsp?id=96&parentpath=0,2&mcustomize=news_view.jsp&dataserno=201508180005&toolsflag=Y&dtable=News （金管會 2015-08-18）原文：「除權息參考價」係以除權息前一營業日收盤價減除權息後之價格……「並據以計算除權息當日交易之漲跌停價格範圍」
      - 官方參考資料 TWT49U（reference/csv/twse_twt49u_20260601_20260923.csv）：1442 9/23 前收 31.15、參考價 28.75、漲停 31.6、跌停 25.9；2542 9/23 前收 45.45、參考價 41.45、跌停 37.35；5906 9/10 跌停 35.45；6924 9/15 跌停 125.5；7610 7/13 漲停 2425、跌停 1965
      - 官方 MI_INDEX 9/23（twse_mi_index_20260923_updown_counts.csv）：股票「下跌(跌停)」562(1)；quotes 中 1442 收 27.8、2542 收 39.55，漲跌符號為 X（除權息）
      - 本地計算 work\V_exref\s1.py（讀已存 enriched 分區）：1442 9/23 prev_close=31.15、close=27.8、change_pct=-0.1075、signal_limit_down=true、consecutive_limit_downs=1；2542 9/23 signal_limit_down=true、consecutive=1、signal_n_day_low=true；5906 9/10 與 6924 9/15 的 signal_limit_down=true
      - 本地計算 work\V_exref\s2.py（依 TWT49U 逐筆比對，前收取自 taiwan/daily，956/956 筆等於 pre_ex_close）：可比對 876 筆，前收基準與官方不同 875 筆；以參考價依檔位計算者，息 785/785 吻合，權 17/35、權息 51/56 吻合；除權息日收盤 app 判跌停 62 筆、官方 10 筆；官方收漲停 23 筆，app 只判出 1 筆（漏判 22 筆，含 7610：app 漲停 2915、官方 2425）
      - 本地計算 work\V_exref\s3.py（重現市場概況邏輯）：9/23 TWSE stock 1086 檔，app 跌停 3 檔（1442、2542、6225），官方 1 檔；漲停 app 15、官方 15。2026-06 以後的已存分區中，帶 signal_limit_down 欄的只有 10 個（2026-09-10～09-23），另外 71 個沒有
      - 本地計算 work\V_exref\s4.py（compute_enriched 未還原 vs 以 TWT49U 因子前復權，865 筆現金除息日）：只在未還原時成立的訊號為 ma5_breakdown 178、ma20_breakdown 187、n_day_low 114、boll_breakdown_lower 181、ma_dead_5_20 47（另有 ma10_breakdown 167、macd_dead 97）
      - 反證面向：pipeline.py:1471-1479 在 compute_enriched_today 中優先採用報價的 prev_close，而 backend/app/taiwan/realtime/mis_provider.py:160 的 prev_close 取自 MIS 的 y；add_limit_distance_columns（pipeline.py:1967-1981）只在這條即時路徑被呼叫（pipeline.py:1690），而且已存分區沒有距離欄（已知 B5）。所以「距漲跌停」在即時 MIS 路徑是否出錯取決於 y 在除權息日是否為參考價（推測是，未驗證）
      - 不在已知清單：grep docs/code-review-2026-09-22.md 的「除權／除息／adj_factor／復權」都沒有命中
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：台股 enriched 發佈路徑沒有提供 adj_factor（enriched_refresh.py:95），除權息日的漲跌停基準因此一律用前一日收盤（enriched_refresh.py:169、pipeline.py:757-761、market_intelligence.py:439），而不是官方的除權息參考價。

漲跌停價：TWT49U 6/1～9/23 有法定漲跌幅的上市除權息列共 884 筆，app 算出的漲跌停價有 883 筆與官方不同。官方現金除息日的漲跌停就是除息參考價依檔位取整，792/792 吻合；finding 寫的 809 這個分母重現不出來，但結論不變。

已儲存的錯誤訊號：5906 9/10、6924 9/15、1442 9/23、2542 9/23 被判為跌停，其中 1442、2542 的 consecutive_limit_downs 為 1。9/23 市場概況的上市股票跌停家數，app 算 3，官方為 1。

分區重建後的影響：6/1～9/23 期間會產生 52 筆假跌停。除權息日實際收在官方漲跌停的 33 筆（漲停 23、跌停 10）中，只有 22 筆漲停會漏判；10 筆官方跌停仍會被判為跌停，但用的基準是錯的。例如 7610 7/13 收 2425＝官方漲停，app 算的漲停價是 2915。

未還原價格：865 筆現金除息日中，未還原價格造成、還原後就不成立的空頭訊號有：ma5_breakdown 178、ma20_breakdown 187、n_day_low 114、boll_breakdown_lower 181、ma_dead_5_20 47。

無法確認的部分：「距漲跌停也跟著錯」沒有資料可以證實。已儲存分區本來就沒有這兩欄（已知的 B5）；即時路徑用報價來源的 prev_close，除息日是否錯誤屬推測。

另外 finding 沒提到：MI_INDEX 把除息股標為不比價，市場概況卻把它們算進下跌家數。
    - 理由：核心缺陷已用本地資料重現，大部分數字與 finding 完全一致；有 4 處細節不完全成立。

我的做法：用 polars 直接讀 data/taiwan/daily 的 661 個分區，加上 kline_daily_enriched 與官方 TWT49U、MI_INDEX 參考資料重新計算。漲跌停價一律呼叫 app 自己的 MarketProfileBridge.calc_limits，security master 讀的是我複製到 work 目錄的副本。訊號則用純計算函式 compute_enriched 分兩次跑：不帶因子一次；另一次用 TWT49U 的前收除以參考價當 ex_factor，做前復權後再算。腳本放在 AUDIT_SCRATCH/work/verify-exlim2/（s3～s8，輸出在 s*_out.txt）。跑完後 data\ 與 backend\app 下沒有任何比起始標記新的檔案，git status 也與開始時相同。

程式碼證據逐條確認成立：
- enriched_refresh.py:95 傳入 factors=pl.DataFrame()，:169 以 ref=prev_close 當基準。
- pipeline.py:757-761 在沒有復權因子時用 raw_close.shift(1)。
- market_intelligence.py:403、:439 以前一日收盤呼叫 calc_limits。

數字對照（finding → 我算出的值）：
1. 有法定漲跌幅的上市除權息列：884 → 884。app 以前收算出的漲跌停價與官方不同：883 → 883。app 的前收在 876 筆有日K的列中與 TWT49U 的前收全數相同；唯一吻合的一筆是 6451（7/27，股利只有 0.22）。
2. 官方漲跌停＝除息參考價依檔位取整：809/809 → 792/792（有 daily bar 的列為 785/785）。分母對不上，但「100% 吻合」的結論成立。
3. 已儲存的假跌停：5906 9/10、6924 9/15、1442 9/23、2542 9/23 → 完全相同。這 4 筆也是 10 個有訊號欄分區中，落在上市除權息日的全部 signal_limit_down；1442、2542 的 consecutive_limit_downs 都是 1。1442 的前收 31.15、收盤 27.80、app 跌停 28.05、官方跌停 25.90 → 相同。
4. 9/23 上市股票跌停家數：app 3、官方 1 → app 3（1442、2542、6225）。改用除息參考價後是 1，與 MI_INDEX「下跌(跌停) 562(1)」一致。漲停兩邊都是 15，也與官方 388(15) 相符。
5. 除權息日收在官方漲跌停：33 → 33（漲停 23、跌停 10）。7610 7/13 的例子正確：收 2425＝官方漲停，app 漲停 2915。但「重建後會漏判」只對 22 筆漲停成立，另外 11 筆 app 照樣會判到：
   - 10 筆收在官方跌停的，因為 app 以前收算出的跌停價比官方高，所以仍會被判為跌停；結果碰巧正確，但基準錯誤。
   - 6225 8/18 的漲停價兩邊恰好相同。
   所以不是 33 筆都會漏。
6. 865 筆現金除息日中，除權息缺口造成、還原後就不成立的空頭訊號：
   - ma5_breakdown 178 → 178
   - ma20_breakdown 187 → 187
   - n_day_low 112 → 114
   - boll_breakdown_lower 181 → 181
   - ma_dead_5_20 47 → 47（另有 1 筆是還原後才出現的訊號）
   我的未還原重算與已儲存分區在這 5 個訊號上 865/865 全部一致。

finding 沒提到的補充：
- 6/1～9/23 期間，若分區重建，app 會在除權息日產生 52 筆假跌停；目前只有 9/10 起的 10 個分區有訊號欄，所以只存了其中 4 筆。
- MI_INDEX 把 1442、2542 標為 X（不比價），市場概況卻把它們算進下跌家數。

無法確認的部分：「距漲跌停跟著錯」。已儲存的分區本來就沒有距漲跌停欄位（即已知的 B5）。即時路徑用的是報價來源提供的 prev_close（pipeline.py:1471-1474，MIS 的 y 欄），不是 shift 出來的前收；除息日 y 是否等於參考價，我沒有資料可以驗證，屬推測。線上證據我沒有重新抓取，但 1442 在 TWT49U 中參考價 28.75、漲停 31.6，27.80−28.75＝−0.95（−3.30%），與 finding 引用的鉅亨數字一致。

嚴重度維持 P2：錯誤只發生在除權息日，且只寫進最近 10 個分區；使用者自選股在期間內的 6 個除息日都沒有產生假漲跌停；監控規則 0 條。影響集中在市場概況的跌停家數與空頭訊號。
    - 證據：
      - backend/app/taiwan/enriched_refresh.py:95 compute_enriched(raw, factors=pl.DataFrame(), ...)；:169 ref = pl.col("prev_close")
      - backend/app/indicators/pipeline.py:757-761 沒有 adj 因子時 _prev_raw_close = raw_close.shift(1)；:388 prev_close = close.shift(1).over(symbol)
      - backend/app/taiwan/market_intelligence.py:403 prev_close 取前一交易日收盤；:439 MarketProfileBridge.calc_limits(prev_close, inst)
      - s4_out.txt：[denominator] rows with confirmed limit: 884; limits from TWT49U pre_ex_close != official: 883
      - s3_out.txt：comparable rows 876；app limit != official 875/876；app prev close != TWT49U pre_ex_close: 0；唯一吻合 6451 2026-07-27（股利 0.217665）
      - s4_out.txt：息 rows with confirmed limit: 792; official == calc_limits(ex_ref): 792（finding 寫 809/809；s3 有日K的 785/785）
      - s4_out.txt：已儲存 signal_limit_down=true 且落在除權息日的列：5906.TWSE 9/10（收 37.35、官方跌停 35.45）、6924.TWSE 9/15（128.0／125.5）、1442.TWSE 9/23（27.8／25.9，consecutive_limit_downs=1）、2542.TWSE 9/23（39.55／37.35，consecutive_limit_downs=1）；10 個分區中 signal_limit_down=true 共 39 筆，落在上市除權息日的就是這 4 筆
      - s3_out.txt：1442 9/23 prev_close_app 31.15、close 27.8、app_dn 28.05、limit_down_twd 25.9
      - s5_out.txt：TWSE stock rows=1090 app limit_up=15 app limit_down=3（1442、2542、6225）；改用除息參考價後 down=1；MI_INDEX updown 股票欄：上漲(漲停) 388(15)、下跌(跌停) 562(1)；MI_INDEX quotes 中 1442、2542 的 change_sign=X
      - s3_out.txt：除權息日收在官方漲停 23、官方跌停 10（合計 33）；官方漲停但 app 未判 22；官方跌停但 app 未判 0；app 判跌停但官方不是 52
      - s4_out.txt：7610 2026-07-13 權息，前收 2650、收 2425、app 漲停 2915、官方漲停 2425
      - s1 schema：kline_daily_enriched 的 2024-01-02～2026-09-09 共 651 個分區沒有 signal_limit_* 欄；consecutive_limit_downs 只在 2026-09-16～09-23 的 6 個分區
      - s6_out.txt：息 ex-date rows with a bar: 865；只在未還原價格成立的額外空頭訊號：ma5_breakdown 178、ma20_breakdown 187、n_day_low 114、boll_breakdown_lower 181、ma_dead_5_20 47（還原後才出現 1）；我的未還原重算與已儲存分區在這 5 個訊號上 865/865 一致
      - s7_out.txt：自選股 6196 6/24、2454 7/7、0050 7/21、3545 8/4、00878 8/18、2379 9/8 的除息日都沒有 app 跌停或漲停誤判
      - backend/app/indicators/pipeline.py:1471-1474 即時路徑的 prev_close 來自報價來源（quote_extra），不是 shift 出來的前收；除息日距漲跌停是否錯誤無法用本地資料驗證（推測）
      - 指令：cd backend && PYTHONDONTWRITEBYTECODE=1 uv run python AUDIT_SCRATCH/work/verify-exlim2/s3_limits.py（s4～s8 同目錄）

### [P3 → P3] 布林通道用樣本標準差（ddof=1），與 Bollinger 原定義（母體）不同（部分成立）

- **主張**：rolling_std 預設 ddof=1，使上下軌比標準定義寬約 √(20/19)
- **正確的定義或數值**：ddof=0
- **app 實際行為**：訊號比標準定義少觸發
- **對使用者的影響**：布林突破／跌破訊號與看盤軟體不一致
- **修法建議**：rolling_std(20, ddof=0)；盤中路徑本來就是母體，改完後兩條路徑也會一致
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:417,449-451
- **線上證據**：
  - https://www.bollingerbands.com/bollinger-bands：「We use the population calculation for standard deviation」
- **本地計算證據**：
  - polars 1.40.1 實測：rolling_std([1,2,3,4]) 得 1.2910（樣本），母體為 1.1180
  - 自選 9 檔 8～9 月上軌最大相對差 0.39%；7/1～9/23 全市場有 317 筆突破上軌、546 筆跌破下軌訊號因此不同（自選 1＋3 筆）
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：盤後路徑（pipeline.py:417、449-451）的布林通道用 polars rolling_std 的預設 ddof=1，也就是樣本標準差。Bollinger 原定義是母體標準差，因此帶寬寬了 √(20/19)≈1.026 倍。

影響幅度：
- 自選 9 檔在 8～9 月的上軌相對差最大 0.39%（2454，9/14）。
- 7/1～9/23 自選股有 1 筆突破上軌、3 筆跌破下軌的訊號，在母體定義下會觸發，實際沒有觸發。
- 同一期間全市場（上市＋上櫃）約 493 筆突破、965 筆跌破訊號因此漏報；原寫的 317／546 只是上市的數字。

另外兩處需要一起處理：
- 盤中路徑（pipeline.py:1526-1528）本來就用母體標準差，而且比較運算子是 >=／<=，盤後是 >／<。同一根 K 棒在盤中與盤後的訊號可能翻轉，例如 2379 在 9/22：盤中判定突破，盤後存檔為 false。
- 回測矩陣（matrix.py:3895）同樣用 ddof=1。

修正時要一起把 pipeline.py:417 與 matrix.py:3895 改成 ddof=0，並統一兩條路徑的比較運算子，否則兩條路徑仍會不一致。
    - 理由：核心主張全部重現。盤後路徑的布林通道用樣本標準差（ddof=1）；我從本地 parquet 與官方證交所收盤價各自重算，數值都與 finding 相同：
- 帶寬比為 √(20/19)=1.025978。
- 自選 9 檔在 8～9 月的上軌最大相對差為 0.3898%（2454，9/14）。
- 7/1～9/23 自選股有 1 筆突破上軌、3 筆跌破下軌的訊號因此不同，而且全部屬於「母體公式會觸發、樣本公式不會觸發」。

唯一重現不了的是「全市場 317／546 筆」。這組數字其實只對應上市（.TWSE）：以上市計算，upper 差 317～318、lower 差 546～548。上市加上櫃的全市場差異約為 491～493 筆（上軌）與 964～965 筆（下軌），finding 少算約 35%～43%。這不影響方向，只會讓影響面變大。

另外補兩個 finding 沒提到的面向：
1. 回測矩陣 backend/app/backtest/matrix.py:3895 也寫死 ddof=1。只改 pipeline.py 的話，回測策略 boll_breakout／bollinger_squeeze 會跟訊號欄位不一致。
2. 盤中路徑除了用母體標準差，比較運算子也不同：盤中用 >=／<=（pipeline.py:1669-1670），盤後用 >／<（pipeline.py:657-658）。所以改成 ddof=0 後，兩條路徑在收盤價剛好等於軌道值時仍可能不一致；finding 說的「兩條路徑也會一致」並不完全成立。

實例：2379 在 9/22 收盤 755。依盤中公式，上軌為 754.2267，755>=754.2267，盤中判定突破。依盤後公式，上軌為 755.0977，已存檔的 signal_boll_breakout_upper=false。同一根 K 棒，盤中與盤後的訊號會翻轉（推論：依公式實算，沒有抓到實際的盤中快照）。

關於「與看盤軟體不一致」：Bollinger 官網確認原定義是母體標準差。但各看盤軟體的做法不同，例如新浪部落格指稱大智慧、飛狐用樣本，通達信用母體；TradingView 文件是動態頁面，我抓不到。所以「與看盤軟體不一致」要看比對的是哪一套軟體。差異幅度小（上軌相對差不超過 0.39%），只影響臨界點上的訊號，維持 P3。
    - 證據：
      - 程式碼：backend/app/indicators/pipeline.py:417 `pl.col("close").rolling_std(20).over("symbol")`，沒有指定 ddof，polars 預設 ddof=1；:449-451 上軌＝ma20+2*_boll_std，下軌＝ma20-2*_boll_std
      - 程式碼：pipeline.py:1526-1528 盤中路徑 compute_enriched_today 用 boll_var = sq_sum/20 - ma^2，是母體變異數；呼叫端為 backend/app/services/quote_service.py:1681。部分和來自 backend/app/repository.py:910-911（tail(19)）
      - 程式碼：pipeline.py:657-658 盤後訊號用 > 與 <；pipeline.py:1669-1670 盤中訊號用 >= 與 <=
      - 程式碼：backend/app/backtest/matrix.py:3895 `valid_rolling_std(market.close, close_valid, 20, ddof=1)`，回測的布林特徵同樣用樣本標準差，finding 的修正建議沒有涵蓋
      - 本地計算（repro.py，polars 1.40.1）：rolling_std([1,2,3,4]) 預設值＝1.2909944（finding：1.2910），ddof=0 為 1.1180340（finding：1.1180）；√(20/19)=1.025978
      - 本地計算：7/1～9/23 共 135,814 列。已存檔的 boll_upper 與「ma20＋2×樣本標準差」重算值的最大相對差為 2.9e-11，與母體版本的中位相對差為 1.46e-3，確認存檔值是樣本版本
      - 本地計算：自選 9 檔 8/1～9/23 上軌最大相對差為 0.3898%（2454.TWSE，2026-09-14：樣本上軌 4945.62，母體上軌 4926.42）。finding 寫 0.39%，重現。每一列的帶寬比都是 1.025978
      - 官方資料（twse_stock_day_202608_202609.csv）：自選 9 檔共 342 列，本地 raw_close 與官方收盤價完全一致（不一致 0 列）；用官方收盤價重算的上軌最大相對差同為 0.3898%；已存檔的 boll_upper 與官方樣本版本的最大相對差為 3.5e-13
      - 官方資料重算：2379.TWSE 在 2026-09-22 收盤 755.0，樣本上軌 755.097674，母體上軌 754.226706。母體公式判定突破、樣本公式判定不突破，已存檔 signal=false。依盤中公式（部分和＋今日收盤、>=）實算為 True
      - 自選 7/1～9/23 訊號差異：上軌 1 筆（2379 9/22）、下軌 3 筆（00885 7/14、00885 7/21、2454 7/20），全部屬於「母體觸發、樣本不觸發」；與 finding 的「1＋3 筆」一致。7 月三筆超出官方資料範圍，只用本地資料驗證
      - 全市場（variants.py／variants2.py）：上市＋上櫃 2,334 檔，上軌差 493、下軌差 965（改用存檔的 ma20／boll 反推則為 491／964）；只算 .TWSE：318／548（存檔反推為 317／547）；只算 .TPEX：175／417。finding 的「全市場 317／546」實際上只是上市
      - 線上來源：https://www.bollingerbands.com/bollinger-bands 原文「We use the population calculation for standard deviation」，並寫預設為 20 期、±2 個標準差
      - 線上來源：https://blog.sina.com.cn/s/blog_4be037500100pwlh.html 標題「BOLL公式用到STD…不同软件标准差公式是不同的」，文中指「大智慧和飞狐使用的算法是样本方差」「通达信使用的是总体方差」（非權威來源，僅說明各軟體做法不同）。TradingView 文件是動態頁面，抓不到內容
      - 腳本與輸出：AUDIT_SCRATCH/work/verify-boll/（repro.py、variants.py、variants2.py、official.py、intraday.py 與各 *_out.txt）

### [P3 → P3] MACD 交叉在暖機期（不足 33 根）就觸發（部分成立）

- **主張**：ewm_mean(adjust=False) 以首筆收盤作為起始值，沒有 min_periods，新上市標的第 2 根 K 棒起就可能產生金叉／死叉
- **正確的定義或數值**：至少要有 26+9−1 根資料才產生交叉訊號（TA-Lib 的 lookback 慣例）
- **app 實際行為**：新上市或新 ETF 在暖機期產生不可靠的 MACD 訊號
- **對使用者的影響**：MACD 金叉策略會選到剛上市的標的
- **修法建議**：在 compute_signals 對 macd_* 加 bar 數條件（例 cum_count ≥ 34），或在 ewm_mean 設 min_samples
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:412-415,454-457,631-636
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 已儲存的 7～9 月資料中，41 檔在歷史不足 33 根時共觸發 52 次金叉、49 次死叉（例 00407A 7/2 第 6 根、6986.TPEX 7/3 第 5 根）
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：pipeline.py:412-415、454-457 的 MACD EMA 使用 ewm_mean(adjust=False)，以第一筆收盤作為起始值，沒有 min_samples；compute_signals（:631-636）也沒有 K 棒數門檻。因此每個標的的第 2 根 K 棒必定出現交叉：收盤高於前一根就是金叉，低於就是死叉，2346 檔中有 2167 檔在第 2 根觸發。之後到第 33 根之前也持續產生不可靠的交叉（TA-Lib 要到第 34 根才有第一筆輸出）。

已存資料在 2026-07-01～09-23 期間，有 41 檔處於暖機期，其中 38 檔觸發了 52 次金叉、49 次死叉（例：00407A.TWSE 7/2 為第 7 根，6986.TPEX 7/3 為第 6 根；兩檔的第 2 根也都已觸發）。

另外，已存歷史從 2024-01-02 起算，所以全市場（含自選 9 檔）在 2024-01～02 都有暖機假訊號：2024-01-03 單日 437 次金叉、1401 次死叉。

MACD 金叉策略走的是 matrix.py:3359-3384 的同類 EMA 實作，缺陷相同；在預設量比 ≥1.5 下，7～9 月有 12 次（10 檔）暖機期金叉會通過過濾。
    - 理由：核心機制與主要數字完全重現；只有 finding 附帶的幾個細節數字不對。

1. 程式碼：pipeline.py:412-415 的 _ema12／_ema26 與 :454-457 的 DEA，都用 ewm_mean(adjust=False)，沒有設 min_samples（polars 1.40.1）。compute_signals（:631-636）只比較當根與前一根的 DIF、DEA，沒有檢查已累積幾根 K 棒。所以第 1 根的 DIF 與 DEA 都是 0，第 2 根就能判出交叉。

2. 我自己寫腳本重算，數字與 finding 並列如下：
   - 2026-07-01～09-23，bar_no≤33（bar_no≤33 與 <33 的結果相同）：金叉 52、死叉 49。finding 同樣是 52／49，一致。
   - 標的數：finding 寫「41 檔觸發」。實際上 41 檔是這段期間有暖機列的標的數，真正觸發交叉的只有 38 檔（金叉 33 檔、死叉 35 檔，聯集 38 檔）。
   - 範例：00407A.TWSE 7/2 是它的第 7 根（finding 寫第 6 根），6986.TPEX 7/3 是第 6 根（finding 寫第 5 根）。finding 看起來是用 0 起算，差一根。

3. 「第 2 根起就可能觸發」其實比 finding 說的更嚴重。第 2 根時 DIF＝(α12−α26)(c2−c1)，DEA＝α9·DIF，所以只要第 2 根收盤高於第 1 根就必定出現金叉，低於就必定出現死叉。全體 2346 檔驗證都成立：565 檔金叉、1602 檔死叉，只有 179 檔兩根收盤相同而沒有觸發。

4. 我用 filter_halt_days → compute_indicators → compute_signals 這幾個純函式，從 taiwan/daily 原始資料重算 00407A 與 6986，DIF／DEA 與已存的 enriched 值差距為 0.0，訊號完全相同。這證明已存資料就是這段程式碼的輸出。

5. 預期口徑：TA-Lib 的 TA_MACD_Lookback＝EMA_Lookback(26)＋EMA_Lookback(9)＝25＋8＝33，第一筆輸出出現在第 34 根。finding 的 expected 寫「26+9−1＝34 根」是對的；標題寫「不足 33 根」口徑略有出入，但 7～9 月的計數不受影響。

6. 原記錄沒提到的新面向：已存歷史對所有標的都從 2024-01-02 開始，所以不只新上市標的，全市場都有暖機期。2024-01-03 單日就有 437 次金叉、1401 次死叉（共 2010 列）；bar≤33 的暖機交叉全期共 4211 次金叉、4134 次死叉。自選 9 檔在 2024-01-03～02-19 之間共有 28 次暖機交叉；7～9 月則不受影響，因為這 9 檔都從 2024-01-02 起就有資料。

7. 使用者影響的路徑要更正：MACD 金叉策略（macd_golden.py）不讀 enriched 的 signal 欄，而是用 app/backtest/matrix.py:3359-3384 的 _valid_ewm_kernel 自己算 EMA。這個 kernel 同樣以第一筆有效值作為起始值，也沒有最少樣本數，所以缺陷同樣存在。在預設量比過濾（vol_ratio_5d≥1.5）下，7～9 月的暖機期金叉中有 12 次、10 檔會通過過濾，最早在第 10 根。enriched 的 signal 欄則供自選 API（watchlist.py:269）、簡易訊號回測（backtest.py:106-107）、監控（本使用者 0 條規則）與前端使用。

8. 規模：暖機交叉佔 7～9 月全部 MACD 交叉約 0.9%（金叉 52/5700、死叉 49/5132），而且全市場有其他通行軟體也從第一根起算。維持 P3。
    - 證據：
      - backend/app/indicators/pipeline.py:412-415 _ema12/_ema26 = ewm_mean(alpha, adjust=False)，沒有 min_samples
      - backend/app/indicators/pipeline.py:454-457 macd_dea = macd_dif.ewm_mean(alpha(9), adjust=False)
      - backend/app/indicators/pipeline.py:631-636 signal_macd_golden/dead 只比較當根與前一根，沒有檢查 K 棒數
      - backend/app/backtest/matrix.py:3359-3384 _valid_ewm_kernel 以第一筆有效值作為起始值（state=float(value)），沒有最少樣本數；macd_golden.py:65-73 使用它
      - 腳本 AUDIT_SCRATCH/work/verify-macdwarm/repro.py 輸出：'2026-07-01..09-23, bar_no<=33: golden=52 dead=49 symbols_with_any=38'（finding：52/49/41 檔）
      - repro2.py 輸出：'Jul-Sep bar<=33: golden 52 dead 49 golden_syms 33 dead_syms 35 union 38'；'symbols in Jul-Sep having any bar<=33 row: 41'
      - repro.py 範例：00407A.TWSE 2026-07-02 bar_no=7 golden（finding 寫第 6 根）；6986.TPEX 2026-07-03 bar_no=6 golden（finding 寫第 5 根）；00407A 2026-06-25 bar_no=2 就已經 golden
      - repro2.py：'bar2 rows 2346 golden==up True dead==down True bar2 golden 565 bar2 dead 1602 flat 179'，證明第 2 根交叉完全由 c2 與 c1 的漲跌決定
      - repro2.py：以 compute_indicators+compute_signals 從 taiwan/daily 重算 00407A.TWSE、6986.TPEX：'max |dif/dea diff| vs stored 0.0 signals identical True'
      - repro2.py：'Jul-Sep warmup golden with vol_ratio_5d>=1.5: 12 symbols 10 min bar_no 10'；'Jul-Sep total golden 5700 dead 5132 -> warmup share golden 0.91% dead 0.95%'
      - 全期間 bar_no<=33：golden=4211 dead=4134，涵蓋 2346 檔；2024-01-03 單日 golden 437、dead 1401（2010 列）；自選 9 檔暖機交叉 28 次，日期 2024-01-03～2024-02-19
      - https://raw.githubusercontent.com/TA-Lib/ta-lib/main/src/ta_func/ta_MACD.c：'return TA_EMA_Lookback(optInSlowPeriod) + TA_EMA_Lookback(optInSignalPeriod);'，並以前 period 根的簡單平均作為起始值（'Seed each price EMA with a simple average of its first period price bars'）

### [P3 → P3] 均線交叉與突破沒有處理浮點相等，交叉日期由累加誤差決定（部分成立）

- **主張**：close 或 ma5 恰等於均線時（台股跳動價常見），rolling_mean 的浮點誤差會決定 > 或 ≤，交叉可能早一天或晚一天
- **正確的定義或數值**：比較時加入容差（例如 1e-9×價格），或用四捨五入到 0.0001 後的均線比較
- **app 實際行為**：平手時的判定不穩定
- **對使用者的影響**：低價股的交叉訊號日期偶爾錯一天
- **修法建議**：在交叉運算式中對均線 round(6)，或比較 (a−b) > eps
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:622-654
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 7/1～9/23 全市場 MA 類訊號約 300 筆與精確計算相差一天（例 1234 7/23 收盤 33.6＝MA20 33.6；3419 8/14 收盤 12.1＝MA20），自選 0 筆
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：MA 交叉與突破訊號（pipeline.py:622-654）直接比較 rolling_mean 算出的浮點均線，沒有容差。台股收盤價是跳動價，close 與 MA、MA5 與 MA20 常出現精確平手。7/1～9/23 全市場有 180 次 close=MA20、515 次 close=MA10、1,536 次 close=MA5、209 次 MA5=MA20、36 次 MA20=MA60 的平手，其中一部分被浮點判成大於或小於：多數來自滾動累加誤差，少數來自小數的二進位表示誤差。結果共 166 筆 MA 訊號與精確計算不同（不是約 300 筆），涉及 101 檔，約佔 89,462 筆浮點 MA 訊號的 0.19%。其中約 99 筆是早一天或晚一天（約 50 個事件，例如 00648R.TWSE 的 MA20 突破發生在 7/23 而不是 7/24），另外 67 筆是整個多出來或消失的訊號（例如 00919.TWSE 9/16 多出一筆 MA20 跌破）。finding 舉的 1234 7/23 與 3419 8/14 雖然是精確平手，但浮點值完全相等，判斷正確，不是差異案例。自選股 0 筆受影響。先 round(6) 再比較，可以消除所有被判錯的 close=MA 平手。
    - 理由：機制成立，但 finding 的數量與兩個例子都重現不出來。

1. 程式與資料路徑：台股 enriched 由 enriched_refresh.py:95 呼叫 compute_enriched 產生，不做復權（factors=空），所以 close 是原始跳動價。MA 用 rolling_mean 計算（pipeline.py:393-401），訊號比較用嚴格的 >／<，前一日用 <=／>=（pipeline.py:622-654），沒有容差。我用這幾個純計算函式重算全歷史（661 個分區、1,423,597 列），7/1～9/23 的 ma20 與 9 個 MA 訊號都和磁碟上的 kline_daily_enriched 逐位元相同（ma20 135,829／135,829 列一致，訊號差異 0），所以重算結果就是 app 實際用的資料。

2. 精確值計算：先確認所有收盤價都是整分（close×100 與整數的最大誤差 7.3e-12），再用整數分的 rolling_sum 比較 c×20 與 s20、4×s5 與 s20、3×s20 與 s60，得到精確訊號。7/1～9/23 視窗內的精確平手次數：close＝MA20 有 180 次、close＝MA10 有 515 次、close＝MA5 有 1,536 次、MA5＝MA20 有 209 次、MA20＝MA60 有 36 次。這些平手中，浮點判成不相等的分別是 12、28、72、54、13 次。

3. 訊號差異：9 種 MA 訊號合計有 166 筆浮點與精確結果不同（涉及 164 個「代號＋日期」、101 檔）。finding 說約 300 筆，重現不出來。166 筆全部在 t 日或 t−1 日有精確平手。其中 99 筆在相鄰一天有對應的訊號，屬於早一天或晚一天，約 50 個事件；另外 67 筆在正負一天內都沒有對應，是整個多出來或消失的訊號。所以「早一天或晚一天」只描述了一部分影響。

4. finding 引用的例子不成立：
   - 1234.TWSE 7/23：close 與浮點 ma20 都是 33.60000000000000142，完全相等。
   - 3419.TWSE 8/14：兩者都是 12.0999999999999996，完全相等。
   這兩天確實是精確平手，但浮點判斷正確。兩檔在整個視窗內也沒有任何訊號差異（diff_classified 裡 0 筆）。

5. 誤差來源：close＝MA 的平手被判錯時，大部分只在增量滾動和累加時出現，重新用 fsum 求和再除就不會錯（MA20 12 次中 10 次、MA10 28 次中 19 次、MA5 72 次中 46 次）。其餘是十進位小數轉二進位的表示誤差加上除法捨入。兩邊都先 round(6) 再比，這些平手被判錯的次數都變成 0。

6. 影響：差異列的收盤價中位數 18.25，最高 90.3，沒有 ≥100 元的。差異佔視窗內 89,462 筆浮點 MA 訊號的約 0.19%。自選股的收盤價與官方 STOCK_DAY 380 列完全一致；8～9 月自選股有 3 次 close＝MA5 平手（3034 9/21、6196 9/16、3545 9/09），浮點都判對，訊號差異 0。這與 finding 說的「自選 0 筆」一致，監控規則也是 0 條，所以 P3 合理。

未驗證：盤中增量路徑 pipeline.py:1640-1671 用部分和除以 N 計算 MA，比較方式相同，推測也有同類問題，但我沒有量化。
    - 證據：
      - backend/app/indicators/pipeline.py:393-401：ma5/10/20/60 用 pl.col('close').rolling_mean(N).over('symbol')
      - backend/app/indicators/pipeline.py:622-654：MA 交叉與突破都用嚴格的 >／<，前一日用 <=／>=，沒有容差
      - backend/app/taiwan/enriched_refresh.py:95：compute_enriched(raw, factors=pl.DataFrame(), ...)，不復權，close 是原始跳動價
      - 指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-mafloat/repro.py；輸出：daily files 661、after halt filter rows 1423597、max |close*100 - round| 7.28e-12；TOTAL differing signal-rows 7/1-9/23: 166；distinct diff rows 164、symbols 101；watchlist diff rows 0
      - repro.py 各訊號的 浮點獨有／精確獨有 筆數：golden_5_20 11/13、dead_5_20 12/11、golden_20_60 2/2、ma20_breakout 2/1、ma20_breakdown 7/3、ma5_breakout 17/15、ma5_breakdown 18/20、ma10_breakout 7/9、ma10_breakdown 9/7
      - analyze.py：與磁碟上 kline_daily_enriched 比較，ma20 逐位元相同 135829/135829，重算的浮點訊號與已存的訊號差異 0，已存訊號與精確訊號差異 166；166 筆全部在 t 或 t−1 有平手；有相鄰一天對應的 99 筆（浮點獨有 49、精確獨有 50），沒有對應的 67 筆
      - analyze.py：1234.TWSE 2026-07-23 的 close＝ma20＝33.60000000000000142、c=3360、s20=67200；3419.TWSE 2026-08-14 的 close＝ma20＝12.0999999999999996、s20=24200；兩檔整個視窗內的 diff 列都是 0，finding 的兩個例子不是差異案例
      - examples.py：00648R.TWSE 2026-07-23 的 close 4.03，精確 MA20 也是 4.03，浮點 ma20=4.029999999999999，所以浮點在 7/23 就發出突破，精確值在 7/24 才發出（早一天）
      - examples.py：00919.TWSE 2026-09-16 的 close 31.99，精確 MA20 也是 31.99，滾動 ma20=31.990000000000002（fsum/20=31.99），浮點多出一筆跌破，精確值在正負一天內都沒有
      - examples.py：各類平手中浮點判錯的次數：close=MA20 180 次中 12 次、close=MA10 515 次中 28 次、close=MA5 1536 次中 72 次、MA5=MA20 209 次中 54 次、MA20=MA60 36 次中 13 次；差異列的收盤價中位數 18.25、最高 90.3、≥100 元 0 列
      - source.py：判錯只出在滾動累加、fsum 正確的：MA20 10/12、MA10 19/28、MA5 46/72；round(6) 後判錯的平手 0 次
      - official.py：官方 STOCK_DAY 380 列與本地收盤價不一致 0 列；自選股加 2330 在 8～9 月的 close=MA5 平手 3 次（3034 9/21、6196 9/16、3545 9/09），浮點與精確的訊號差異 0
      - 腳本與輸出：AUDIT_SCRATCH/work/verify-mafloat/（repro.py、analyze.py、examples.py、source.py、official.py、diff_rows.csv、diff_classified.csv）；repo 的 git status 前後一致，只有原本就有的 3 個修改

### [P3 → P3] 10000 哨兵值會把 ≥10000 元的台股誤當成無漲跌幅（潛在）（部分成立）

- **主張**：compute_limit_signals、_compute_limit_signals_today 與回測 latest_limits，把 limit_up≥10000 視為「無漲跌停」的占位值；台股已有漲停價超過 1 萬的標的
- **正確的定義或數值**：用明確的 NO_LIMIT 標記（例如櫃買的 9999.95/0.01 組合），不要用價格門檻
- **app 實際行為**：目前台股 instruments 沒有 limit_up 欄，所以尚未觸發；若依 N5 把 MIS 的 u/w 接進來，這些股票的漲跌停訊號會被靜默關閉
- **對使用者的影響**：潛在：修 N5 後，千金股以上的高價股漲停不會被標記
- **修法建議**：哨兵判定改成 (limit_up≥9999.95 且 limit_down≤0.01) 或使用明確旗標
- **稽核者信心**：plausible
- **程式碼證據**：
  - backend/app/indicators/pipeline.py:795,806,1800,1816-1820
  - backend/app/backtest/matrix.py:2229-2230
- **線上證據**：
  - （無）
- **本地計算證據**：
  - 9/23 MI_INDEX：2059 川湖收盤 12,390；櫃買 5274 信驊 9/24 官方漲停 22,305
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：台股已有漲停價超過 1 萬的標的：9/24 的 2059 川湖依規則為 13,625（前收 12,390），5274 信驊官方為 22,305。三處程式都用 limit_up≥10000 當門檻，但處理方式不同。_compute_limit_signals_today（pipeline.py:1816-1821）把它當成無漲跌停，四個訊號強制為 False。compute_limit_signals（pipeline.py:795-815）與回測 latest_limits（matrix.py:2229-2232）則是不採用權威價，改用自算理論價；而這個理論價沒有對齊台股 5 元檔位（13,629／22,308），所以以權威漲停價收盤時仍判為未漲停，只有前收是 50 元倍數時才判對。實測三處都無法標出 2059、5274 的漲停。api/kline.py:266 也有同樣的 `<10_000` 門檻。櫃買官方的無漲跌幅標記 9999.95/0.01 小於 10000，現行哨兵反而認不出來。目前台股 instruments 沒有 limit_up 欄，所以尚未觸發；使用者自選 9 檔的最高價約 5,185，即使修好 N5 也不受影響。修法建議：改用明確的 NO_LIMIT 旗標或 (≥9999.95 且 ≤0.01) 判定，並讓 fallback 理論價對齊台股檔位。
    - 理由：數據部分完全重現。9/23 上市收盤 ≥10000 元的只有 2059 川湖（12,390），依 10% 與 5 元檔位算出的 9/24 漲停價是 13,625。上櫃只有 5274 信驊的官方次日漲停價 ≥10000（22,305／18,255）。本地 parquet 的 2059 收盤也是 12,390，與官方一致。

「目前尚未觸發」也成立：data/instruments 是空目錄；台股離線路徑的 instruments 只有 symbol 與 float_shares；compute_enriched_today 只有在 instruments 非空時才會呼叫 _compute_limit_signals_today。

機制的描述只對一部分。三處程式裡，只有 _compute_limit_signals_today 把 limit_up≥10000 當成「無漲跌停」（no_price_limit），直接把四個訊號設為 False。compute_limit_signals（pipeline.py:795-815）與回測 matrix.py:2229-2232 的做法不同：它們只是不採用權威價，改用自算的理論價，並沒有把訊號關掉。

這兩處的理論價由 polars_limit_price／numpy_limit_price 算出，只做到分位四捨五入，沒有對齊台股檔位。所以 2059 的理論漲停是 13,629（官方規則 13,625），5274 是 22,308（官方 22,305）。以權威漲停價收盤時，三處都判定「未漲停」，結果與 finding 說的相同，只是原因不同。在 fallback 那兩處，只有前收是 50 的倍數時，理論價才會落在 5 元檔上而判對，所以其餘約九成的情況會漏判（推測：依前收為 5 元檔位均勻分布估算）。

finding 沒列到的相同門檻還有一處：app/api/kline.py:266（`0 < numeric < 10_000`）。另一個新面向：櫃買官方的「無漲跌幅」標記是 9999.95/0.01，本身低於 10000，現行哨兵認不出來，會被當成權威價採用。目前帶這個標記的 117 筆多為債券 ETF，價格遠低於 9999.95，所以實際無害。

使用者自選 9 檔的最高價是 2454 的 5,185，即使修好 N5 也不受影響，維持 P3。
    - 證據：
      - 官方 TWSE MI_INDEX 9/23（reference/csv/twse_mi_index_20260923_quotes.csv:494）：2059 川湖 close_twd=12390.0。全檔 1380 列中 close≥10000 只有 1 檔。用 PriceLimitModel.calc_limits_for_pct(12390, 0.10) 算出 9/24 漲停 13625.0、跌停 11155.0（finding 只寫了收盤 12,390，數值相符）
      - 官方 TPEx dailyQuotes 9/23（reference/csv/tpex_daily_quotes_20260923.csv:516）：5274 信驊 close=20280.0，next_day_limit_up=22305.0，next_day_limit_down=18255.0。next_day_limit_up≥10000 的只有這 1 檔（finding 寫 22,305，相符）
      - 同一份 TPEx 檔有 117 列是 9999.95/0.01 無漲跌幅標記（例如 00411A、00679B、00687B…），介於 9999 到 10000 之間的其他值 0 列。9999.95<10000，所以 pipeline.py:1814 會把它當成權威價，而不是哨兵
      - 本地 data/taiwan/daily/date=2026-09-23/part.parquet：2059.TWSE close=12390.0，與官方一致。同一分區 TPEX 列數為 0（9/22 有 1009 列），所以 5274 的 9/23 無法用本地資料對照
      - 腳本 AUDIT_SCRATCH/work/verify-sentinel10k/verify.py，輸出在同目錄 out.txt。執行指令為 `cd backend && timeout 300 uv run python verify.py`，exit=0
      - _compute_limit_signals_today（pipeline.py:1800,1816-1821,1841-1872），以權威漲停價收盤：2454（limit_up=5695）signal_limit_up=True；2059（13625）False；5274（22305）False。原因是 no_price_limit 被強制設為 False，確認會被靜默關閉
      - compute_limit_signals（pipeline.py:795-815,783）以權威價收盤：2454 True／consec=1；2059 False／0；5274 False／0。原因是 ≥10000 時退回用 polars_limit_price（price_limits.py:133-138）算的未對齊檔位理論價 13629.00／22308.00，而官方規則是 13625／22305
      - matrix._limit_lock_matrices（matrix.py:2224-2232）有權威價時 up_locked 最後一列為 {2454:1, 2059:0, 5274:0}；沒有權威價（NaN）時三檔全是 0，原因同樣是 numpy_limit_price（price_limits.py:242-257）沒有對齊檔位
      - 目前未觸發：`find data/instruments -type f` 沒有結果；repository.py:1078-1087 讀 data/instruments/**；compute_enriched_today 在 pipeline.py:1686-1687 只有 instruments 非空才呼叫；台股離線 instruments 由 share_capital_store.py:170-175 產生，只有 symbol 與 float_shares；另外 enriched_refresh.py:96,157-189 會用自己的、已對齊檔位的邏輯覆寫四個訊號欄，但不覆寫 consecutive_limit_ups
      - finding 未列到的同類門檻：app/api/kline.py:266 `if math.isfinite(numeric) and 0 < numeric < 10_000:`，K 線頁的權威漲跌停價 ≥10000 時會被丟掉，改走 rule 來源
      - 自選 9 檔都遠低於 10000：MIS 快照顯示 2454 收在 5185.0、6196 在 549.0、0050 在 112.45、00878 在 35.09（reference/csv/mis_snapshot.csv）。所以對使用者目前沒有影響
      - 副觀察（不屬本 finding）：沒有權威價時，compute_limit_signals 與 matrix 的理論價未對齊檔位，連 2454 以 5695 收盤都判成未漲停（理論價 5698.00），屬於另一個獨立的檔位問題

### [P3 → P3] 千金股升降單位將由 5 元改為 1 元，程式中的三份檔位表都沒有日期維度（部分成立）

- **主張**：TickSizeModel 的 trade_date 參數未使用；另有兩份硬編碼的檔位表複本
- **正確的定義或數值**：檔位表依生效日切換並集中在單一實作
- **app 實際行為**：新制上路後，≥1000 元股票的漲跌停價與距離會全部錯，歷史回測也無法區分新舊制
- **對使用者的影響**：目前沒有影響；新制上路時才會出錯
- **修法建議**：集中成單一的日期感知檔位函式，另兩處改為引用它；生效日待主管機關公告後設定
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/market_rules.py:191,213-214
  - backend/app/taiwan/enriched_refresh.py:117-128
  - backend/app/indicators/pipeline.py:1953-1964
- **線上證據**：
  - https://money.udn.com/money/story/5607/9713625（2026-08-25）：「針對股價新台幣1000元以上的千金股，升降單位由現行5元改為1元」，預計 116 年（2027）7 月上路，「仍有待陳報主管機關」
- **本地計算證據**：
  - 9/23 MIS 2454 的 u=5695、五檔 5180/5175/5170，確認目前仍為 5 元
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：目前的程式碼是正確的：MIS 與 TPEx 官方的漲跌停價都能重現，≥1000 元的價格一律是 5 的倍數。但檔位表沒有日期維度，且有三份複本：market_rules.py:191-214、enriched_refresh.py:117-128、pipeline.py:1953-1964。get_tick_size 的 trade_date 參數沒有用到；漲跌停主要路徑 calc_limits_for_pct（market_rules.py:309-321）根本不接受日期；research_context.py:731 呼叫時也沒有傳日期。證交所與櫃買中心已分別通過（經濟日報 2026-08-25、自由時報 2026-08-21）：1000 元以上的升降單位由 5 元改為 1 元，預計 2027 年 7 月上路，仍待主管機關核准。新制上路後，漲跌停價原始值 ≥1000 的股票（包含昨收約 909～999 的股票，例如 6488 應為 1068，app 算出 1065）約 78% 的漲跌停價會偏差 1～4 元，不是「全部錯」。偏差方向是漲停價偏低、跌停價偏高，所以 signal_limit_up/down 會出現誤判（假陽性），距離欄位只有小幅偏差。若只把表改成 1 元而不加日期，全量重建會改寫舊制期間的歷史訊號：本地已存的 27 個漲跌停訊號中，會有 20 個翻成 False。修法需要把三份檔位表集中成同一個依日期判斷的實作，並讓 calc_limits_for_pct 與向量化路徑都能傳入交易日。目前使用者沒有受影響；新制上路後，自選中只有 2454 會受影響。
    - 理由：finding 的核心事實全部重現。(1) TickSizeModel.get_tick_size 的 trade_date 參數確實沒有用到：分別傳入 None、2026-09-23、2027-07-01、2030-01-01，tick(1500) 都回 5.0，round_price_limit_boundary(5698, up) 都回 5695.0。(2) 正式程式碼裡恰好有三份相同的檔位表：market_rules.py、enriched_refresh.py 的 _tick_size_expr、pipeline.py 的 _taiwan_tick_size_expr。我用 15 個價格點（含 999/1000/1001/5180/5698）比對，股票與 ETF 兩種檔位下三份的結果都不一致 0 筆。docs/taiwan_market_contract_v1.json 另有一份，但它只是測試 fixture。(3) 目前仍是 5 元檔：MIS 9/23 的 2454 昨收 y=5180、u=5695、w=4665，買進五檔為 5180/5175/5170/5165/5160，與 finding 一致；app 算出的 u/w 與官方完全相同。官方資料中所有 ≥1000 的價格都是 5 的倍數：STOCK_DAY 304 格、MI_INDEX 9/23 120 格、TPEx 9/23 77 格。(4) 新制有報導佐證：經濟日報與工商時報（2026-08-25）寫證交所董事會通過 1000 元以上升降單位由 5 元改 1 元，預計 116 年 7 月上路，仍待陳報主管機關；自由時報（2026-08-21）另寫櫃買中心同步修正。

不過 actual 寫的「≥1000 元股票的漲跌停價與距離會全部錯」言過其實，只部分重現。我用本地 enriched parquet（2024-01-02～2026-09-23，共 1,423,597 列）模擬 1 元檔：原始漲停價 ≥1000 的非 ETF 列有 17,533 列、81 檔，其中 13,745 列（78.4%）的漲停價會不同；原始跌停價 ≥1000 的 13,853 列中，有 10,867 列（78.4%）的跌停價會不同。差距都只有 1～4 元，約 21.6% 剛好相同。影響範圍也不限於股價 ≥1000 的股票：昨收在 909～999 之間、但漲停價超過 1000 的股票同樣受影響，例如 6488 昨收 971，現行算法得 1065，新制應為 1068。差異的方向是 app 的漲停價偏低、跌停價偏高，所以新制上路後 signal_limit_up／signal_limit_down 會多出誤判（不是漏判）：收盤價落在 app 價與實際漲跌停價之間時會被誤標。

另外找到 finding 沒提到的面向：calc_limits_for_pct 根本不接受 trade_date（co_varnames 檢查回 False），research_context.py:731 呼叫 get_tick_size 也沒有傳日期。因此只讓 get_tick_size 認得日期還不夠，漲跌停的主要計算路徑（MarketProfileBridge.calc_limits → calc_limits_for_pct）也要一併傳入日期。

「歷史回測無法區分新舊制」可以用反事實重現：本地資料全部落在舊制期間，如果只把表改成 1 元、不加日期再全量重建，已存的 signal_limit_up=True 共 20 列，其中 15 列會翻成 False；signal_limit_down=True 共 7 列，其中 5 列會翻成 False。

使用者情境：自選中只有 2454（5180 元）≥1000。目前沒有影響，新制上路後 2454 的漲停價會算成 5695，應為 5698。嚴重度 P3 合理：現況的計算正確，新制尚待核准，問題屬於維護性。
    - 證據：
      - backend/app/taiwan/market_rules.py:191：trade_date 參數標了 noqa: ARG004，函式本體（194-214）完全沒用到；213-214 行是 <1000 回 1.00，其餘回 5.00
      - backend/app/taiwan/market_rules.py:309-321：calc_limits_for_pct 沒有 trade_date 參數，320-321 呼叫 round_price_limit_boundary 時也沒傳日期（新面向）
      - backend/app/taiwan/enriched_refresh.py:117-128，以及 backend/app/indicators/pipeline.py:1953-1964：兩份硬編碼的 polars 檔位表複本，都沒有日期維度
      - backend/app/taiwan/research_context.py:731：TickSizeModel.get_tick_size(p_close) 呼叫時沒有傳 trade_date
      - 腳本：AUDIT_SCRATCH/work/verify-tick1000/verify.py；輸出：同目錄 out.txt（指令：cd backend && uv run python verify.py，exit=0）
      - 本地計算：trade_date 為 None／2026-09-23／2027-07-01／2030-01-01 時，tick(1500) 都是 5.0；round_price_limit_boundary(5698,up) 都是 5695.0
      - 本地計算：三份檔位表在 15 個價格點上比對，股票與 ETF 兩種檔位下不一致各為 0 筆
      - MIS 9/23（reference/csv/mis_snapshot.csv）：2454 y=5180、u=5695、w=4665，app 計算相同，1 元檔下應為 5698／4662；6488 y=971、u=1065，app 相同，1 元檔下應為 1068；2454 買進五檔 5180/5175/5170/5165/5160
      - 官方資料中 ≥1000 的價格格數，非 5 倍數均為 0：STOCK_DAY 304 格（2330、2454）、MI_INDEX 9/23 OHLC 120 格、TPEx 9/23 OHLC 77 格
      - TPEx 9/23 次日漲跌停（ref×1.1≥1000，共 20 檔）：app 與官方 19 檔相符（唯一不符的 7856 官方為 9995/0.01，屬無漲跌幅標的，與本 finding 無關），其中 14 檔在 1 元檔下會不同
      - 本地 enriched parquet（661 個分區）：原始漲停價 ≥1000 的非 ETF 列，漲停價會變動的有 13745/17533 = 78.4%；原始跌停價 ≥1000 的列，跌停價會變動的有 10867/13853 = 78.4%；最大差距 4.0 元
      - 反事實（無日期、全量重建）：signal_limit_up=True 20 列中 15 列翻成 False（例如 3443.TWSE 2026-09-22 收盤 8375，現行漲停 8375，1 元檔下應為 8376）；signal_limit_down=True 7 列中 5 列翻成 False
      - https://money.udn.com/money/story/5607/9713625（2026-08-25）：「針對股價新台幣1000元以上的千金股，升降單位由現行5元改為1元」、「上路時間預計明（116）年7月」、「該案仍有待陳報主管機關」
      - https://www.ctee.com.tw/news/20260825701782-430201（2026-08-25）：「千金股每一升降單位（tick）將從目前的5月降為1元」（「5月」為原文用字），「該案仍有待陳報主管機關」
      - https://ec.ltn.com.tw/article/breakingnews/5547899（2026-08-21）：櫃買中心將「每股市價達1000元以上者，申報買賣價格之最低單位由5元降低為1元」，預計明年 7 月實施，所以上櫃市場也適用

## 確認正確的項目

- **台股漲跌幅 10%、漲停價向下取、跌停價向上取到檔位（TickSizeModel.round_price_limit_boundary、PriceLimitModel.calc_limits_for_pct、MarketProfileBridge.calc_limits）**：以 Decimal 獨立實作「≤ 基準×1.1 的最高合法價、≥ 基準×0.9 的最低合法價」再與官方和 app 比對：(a) 9/23 MIS u/w 5 檔（2454 5695/4665、6196 588/481.5、0050 123.0/100.7、00878 38.73/31.69、6488 1065/874），app 全數吻合；(b) 櫃買 9/24 官方次日漲跌停 891 列，自算全數吻合，app 有漲跌幅比例的 889 列全數吻合；(c) TWT49U 除息日官方漲跌停，以未四捨五入的除息參考價計算 809/809 吻合（可確認取整規則，但 app 本身沒有處理除權息）
  - 來源：backend/app/taiwan/market_rules.py:216-236
  - 來源：backend/app/taiwan/market_rules.py:308-334
  - 來源：reference/csv/mis_snapshot.csv、tpex_daily_quotes_20260923.csv、twse_twt49u_20260601_20260923.csv
  - 來源：https://www.cnyes.com/twstock/2454（9/23 漲停價 5695.00、跌停價 4665.00）
  - 來源：https://efrontrade.com/2026/06/limit-up-limit-down-price-taiwan-stock.html：「漲停價要往下找最接近、且不超過 10% 上限的合法價格。跌停價要往上找最接近、且不低於 10% 下限的合法價格。」例：基準 40.60→44.65／36.55
  - 來源：work/A05/limits_check.out
- **股票升降單位六級（<10:0.01、10–50:0.05、50–100:0.1、100–500:0.5、500–1000:1、≥1000:5）**：比對程式碼與券商及財經網站的說明；另以櫃買 889 檔股票官方次日漲跌停實測，涵蓋各級距（含 2454 等 ≥1000 元股、5274 信驊 22305）
  - 來源：backend/app/taiwan/market_rules.py:203-214
  - 來源：backend/app/indicators/pipeline.py:1953-1964
  - 來源：https://concords.moneydj.com/z/glossary/glexp_4792.djhtm（搜尋摘要：未滿10元0.01…1000元以上5元）
  - 來源：efrontrade 升降單位表（同上 URL）
- **ETF 升降單位兩級（<50:0.01、≥50:0.05）**：MIS 0050：昨收 111.85 → 官方 u=123.0、w=100.7；若套用股票級距，跌停應為 101.0，因此可反證 ETF 用兩級制。00878：38.73/31.69。櫃買 ETF（例 006201 50.9/41.66）也吻合
  - 來源：reference/csv/mis_snapshot.csv
  - 來源：https://www.taifex.com.tw/file/taifex/CHINESE/10/ETF%E6%9C%9F%E8%B2%A8%E4%BB%8B%E7%B4%B9%E6%91%BA%E9%A0%81%E6%96%87%E5%AE%A3.pdf（搜尋摘要：比照上市(櫃)ETF，未滿50元0.01、50元以上0.05）
  - 來源：backend/app/taiwan/market_rules.py:197-201
- **國內成分槓桿／反向 ETF 漲跌幅＝10%×|倍數|；國外成分（含其槓桿反向）無漲跌幅**：app 結果：00631L 0.20、00632R 0.10、00663L 0.20、00664R 0.10；上市 ETF 官方「是否包含國外成分」為「是」的 171 檔，app 全數給 None 或拒判，沒有任何一檔被錯給 10%；櫃買官方無漲跌幅（9999.95/0.01）的 ETF，app 判為 None 的有 93 檔
  - 來源：backend/app/taiwan/universe/models.py:124-162
  - 來源：reference/csv/twse_t187ap47_L_etf_meta.csv
  - 來源：work/A05/twse_etf_class.csv
  - 來源：efrontrade（同上）：「國內成分為 10%×倍數。含國外成分可能無限制」；搜尋摘要（Mr.Market／TWSE 槓桿型ETF頁）：正2 為 20%、國外成分無漲跌幅
- **已儲存 enriched 的 signal_limit_up／signal_limit_down／signal_broken_limit_up／signal_limit_down_recovery（台股 enriched_refresh 路徑，非除權息日、非新上市）**：以 MI_INDEX 9/23 的收盤與漲跌反推參考價並獨立判定：1,150 檔 4 個訊號都是 0 差異；股票漲停 15 檔、跌停 1 檔，與官方漲跌家數表「388(15)」「562(1)」相符。自選 9 檔加 2330，在 2026-08-04～09-23 共 370 根 K 棒中，落在有訊號欄分區的 80 列 0 差異
  - 來源：backend/app/taiwan/enriched_refresh.py:157-189
  - 來源：reference/csv/twse_mi_index_20260923_updown_counts.csv
  - 來源：work/A05/market_check.out
- **9/24 漲跌停價（以 9/23 官方收盤獨立推算，app 的 PriceLimitModel 結果相同）**：2330 2750/2250、2454 5700/4670、3034 599/490.5、2379 837/685、6196 603/494.5、3545 56.4/46.2、0050 123.65/101.25、00878 38.59/31.59；00885、00893 無漲跌幅；6488 1045/855，與櫃買官方次日漲跌停一致
  - 來源：reference/csv/twse_stock_day_202608_202609.csv
  - 來源：reference/csv/tpex_daily_quotes_20260923.csv（6488 next_day_limit_up 1045、down 855）
- **add_limit_distance_columns 的公式（以 prev_close 為基準、依檔位取整、NO_LIMIT 回傳 null）**：把官方漲跌停價當作收盤餵入純函式：收在漲停時距漲停＝0，收在跌停時距跌停＝0（2454、6196、0050、00878、6488）。「只存在增量路徑」的問題屬已知 B5，這裡不重複
  - 來源：backend/app/indicators/pipeline.py:1967-2007
  - 來源：work/A05/limits_check.out 第 5 節
- **signal_ma_golden_5_20、signal_ma_dead_5_20、signal_ma_golden_20_60、signal_macd_golden、signal_macd_dead、signal_ma5/10/20_breakout／breakdown、signal_n_day_high、signal_n_day_low、signal_volume_surge（盤後 EOD 路徑）**：從 taiwan/daily 原始日 K 用 numpy 獨立實作：SMA；EMA 以前 n 根的 SMA 作為起始值（TA-Lib 式，與 app 以首筆收盤起始不同）；DIF/DEA；60 日收盤極值；量比＝今量÷前 5 日均量≥2。以此比對 2026-07-01～09-23 已儲存訊號：自選 9 檔 540 列全部 0 差異，DIF/DEA 最大差 1.4e-12。全市場少數差異來自價格恰等於均線的浮點平手，以及新上市暖機期，另列 P3。原始收盤與官方 STOCK_DAY 比對 380 列 0 差異
  - 來源：backend/app/indicators/pipeline.py:612-668
  - 來源：backend/app/indicators/pipeline.py:388-440
  - 來源：work/A05/signals_check.out
- **訊號不使用未來資料**：把資料截斷在 t（8/14、9/1、9/16、9/22），用 app 的 compute_indicators＋compute_signals 計算，和全量資料在同一天的結果比較，4 天共 0 個訊號格不同
  - 來源：work/A05/signals_check.out（look-ahead test: total 0）
- **signals.ts 其餘標籤與定義一致：漲停／跌停（收盤價達依檔位取整的漲跌停價）、漲停打開（盤中觸及漲停但收盤未守住）、MA／MACD 交叉、突破、60日新高低（收盤價）、放量**：逐條對照 signals.ts 的 description 與 enriched_refresh.py／pipeline.py 的實際運算式；訊號 ID 共 20 個，與後端 SIGNAL_DEPENDENCIES（16）＋LIMIT_SIGNAL_OUTPUTS 的 4 個訊號對齊
  - 來源：frontend/src/lib/signals.ts:25-166
  - 來源：backend/app/indicators/pipeline.py:569-596
- **處置股不改變漲跌幅（app 未特別處理，與規則相符）**：2026/8/10 處置新制的內容只有分盤撮合（約每 2 分鐘）與預收款券，沒有調整漲跌幅；app 也沒有處置股的特例。屬間接證據
  - 來源：https://www.stockwarden.com/disposition-new-rules（處置期間 5 個營業日、統一約每 2 分鐘撮合、預收款券；文中未提漲跌幅改變）

## 無法驗證的項目

- **一字板（開高低收皆在漲停）訊號**：app 未實作任何一字板訊號（grep 只找到 MiniCandlestick 的繪圖註解與 technical_indicators.py:4 的排除說明），沒有東西可以核對
- **9/24 上市股票的官方漲跌停價（MIS u/w）**：參考資料的 MIS 快照是 9/23 收盤後狀態，沒有 9/24 盤中資料；Yahoo 奇摩（tw.stock.yahoo.com/quote/2454.TW）與 HiStock 頁面都沒有漲跌停欄位。只能提供以 9/23 收盤獨立推算的值（見 verified_ok），第三方只有鉅亨網提供 9/23 的值
- **減資恢復買賣首 5 日是否無漲跌幅**：搜尋結果只找到「初次上市股票首五個交易日無漲跌幅限制－自94年3月1日實施－」（TWSE PDF，依規定不連線）與減資參考價計算，沒有找到減資首 5 日的權威條文
- **新上市 ETF 首日是否適用漲跌幅**：沒有查到權威來源；櫃買 00411A（2026-08-26 上市）官方為無漲跌幅，但無法區分原因是「新上市」還是「含國外成分」
- **權／權息（含現金增資）除權日的漲跌停基準**：TWT49U 實測：權 17/35、權息 52/57 可用除權參考價重現；其餘看起來是漲停用較高、跌停用較低的基準，但沒有取得官方條文。app 本身完全沒有處理除權息（見 finding）
- **盤中 _compute_limit_signals_today 的實際輸出**：純台股部署下 instruments 為空，這條路徑不會執行（已知 N5）；只能從程式碼推論它和盤後一樣用 0.01 元進位（見 finding 1 的新面向）
- **成交量與官方 STOCK_DAY 的差異（16/380 列，皆相差整張數，例 2330 9/23 少 839,000 股）**：推測缺少鉅額交易，屬資料來源領域；signal_volume_surge 的比對是以 app 自己的成交量為基準，只核對了公式
- **TPEx 9/23 日 K 缺漏（taiwan/daily 9/23 只有 TWSE 1,330 列，9/22 有 TPEX 1,009 列；kline_daily_enriched 9/23 只有 1,323 檔上市）**：屬資料完整性領域，未追查原因；會讓 9/23 全市場漲跌停家數缺上櫃
- **回測 build_market_data_matrix（以 panel 的 signal_limit_up 當鎖定）在台股部署是否被走到**：engine.py:446 在 instruments 為空時不產生 signal_limit_up，_bool_matrix 在缺欄時回傳全 0（matrix.py:2767-2777）；沒有端到端執行回測，只從程式碼推論
