# G01-gap｜G1-abnormal-diagnostics

> 稽核日期 2026-09-24。本章由多代理稽核與獨立驗證的結構化結果直接轉換而成，未經模型改寫。
> 嚴重度標示為「原始 → 驗證後」；驗證者修正過的敘述以驗證者為準。

## 統計

| 確認成立 | 部分成立 | 有爭議 | 被推翻 | 未驗證 | 確認正確 | 無法驗證 |
|---|---|---|---|---|---|---|
| 5 | 7 | 0 | 0 | 0 | 15 | 6 |

## 稽核者摘要

結論：10 種訊號的程式實作與 docstring、前端公式幾乎一字不差。我用 polars 直接讀 parquet，照文件定義獨立重算 2026-08-10～09-23 共 33 個交易日的全市場訊號，與 app 純計算結果（把自組的 DataFrame 注入 TaiwanAbnormalDiagnosticsService，並封鎖網路）比對：30,618 筆一致，app 多出 92 筆、少了 55 筆。多出的 92 筆全部來自「先四捨五入再比門檻」，少的 55 筆全部來自「20 日中位數為 0 時直接不評估」。真正的問題在口徑與資料：
(1) P1：漲跌幅以原始前收計算，沒有改用除權息參考價。對照官方 TWT49U，上市股在 33 個交易日內除權息當天觸發的 73 筆 PRICE_MOVE 中有 60 筆是假訊號；PRICE_FLOW_DIVERGENCE 至少有 4 筆假背離；5 日視窗內含除權息日的 RELATIVE_STRENGTH_OUTLIER 有 122 筆，還原後 83 筆不再觸發。自選股 2379 瑞昱 9/8 被標成「大幅下跌 −5.08%」，依官方參考價實際只跌 −1.70%，STOCK_DAY 當日的漲跌欄標為 X（不比價）。
(2) P2：上櫃缺日（9/16、9/23）時，整個上櫃市場被靜默排除，但 data_quality 仍報 complete。9/17 上櫃全部沒有漲跌幅；9/17～9/22 上櫃爆量／成交額訊號全部無法評估（每日 0 筆，缺日前每日約 100～157 筆）。9/23 的市場脈絡只剩上市資料：成交額少算 22.5%，上漲比率 0.407（官方全市場為 0.4221）。
(3) P2：畫面把 SHORT_SURGE 的「減少」也標成「融券激增」，佔 479/1081 筆。
(4) P2：融資／融券 20 日中位數為 0 時直接跳過，漏掉最極端的案例，例如 3717 聯嘉投控融券一天增加 2,448 張。
(5) P2：當日 16:30 寫入的日K量額與法人資料是初值，官方事後修正後沒有回補：2330 在 9/18 的量比 app 算 1.93、依官方資料為 2.11，因此漏報；投信買賣超在兩天內有 45 列與官方不同。
其餘為 P3，包括四捨五入邊界、嚴重度分級沒有文件、標籤與台灣慣用語不符、市場與產業的母體不一致、產業均值含自身、券資比沒有最低融資餘額門檻、法人缺列的慣例與 B9 不一致。槓桿正規化（00885、00893 為 1 倍，是 no-op）與官方 ETF 類型一致。產業 5 日口徑與 industry_intelligence 完全對齊。
限制：本 session 的 WebSearch 額度已用完（200/200），網路查證改用 WebFetch 共 23 次（MoneyDJ、Wikipedia、MBA智庫）。台灣券商「爆量／法人異常」的具體門檻找不到可引用的來源，列入 unverifiable。本次只寫入 scratchpad\audit\work\G01-gap；import app 模組時，repo 內 gitignore 的 __pycache__ 可能被 Python 更新。

## 問題清單

### [P2 → P2] 上櫃缺日時診斷靜默少掉整個上櫃市場，data_quality 仍報 complete，面板與 AI 都看不出來（確認成立）

- **主張**：目標日沒有日K列的標的直接被略過；前收或前 5 日不足時訊號也不評估。但 data_quality 只要目標日有任何一列就判定 current／complete，Screener 也不顯示 data_quality。
- **正確的定義或數值**：資料覆蓋不足時，data_quality 應標示 partial，並依交易所列出缺漏；前端應顯示交易日與資料狀態；未評估的訊號應可與「未觸發」區分。
- **app 實際行為**：缺日當天上櫃約 900 檔完全不出現，其後 4 個交易日上櫃的量能訊號全部不評估，狀態卻是 complete；AI 研究對上櫃標的收到 abnormal_signals: []，而系統提示同時要求「零數值非缺失」。
- **對使用者的影響**：「全市場偵測到 N 檔」其實只涵蓋上市。9/16～9/22 的上櫃爆量訊號完全消失，使用者無從察覺；9/23 缺日若沒回補，預期 9/24～9/30 會重演（推測）。自選 9 檔都是上市，不受直接影響。
- **修法建議**：data_quality 依交易所比較 evaluated 與 universe 並標示 partial；Screener 顯示 trade_date 與 overall_status；在 item 加 unevaluated_signals（附原因）；ai_research 在 diag_item 為 None 時加入 missing_items。缺日回補本身屬資料更新領域。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - abnormal_diagnostics.py:340-342（沒有目標日日K就 continue）
  - abnormal_diagnostics.py:378、:399（count_vol_5d／count_amt_5d == 5 才評估）
  - abnormal_diagnostics.py:671-677（d_status 在 len(df_target_daily) > 0 時為 current，三者 current 就是 complete）
  - frontend/src/pages/TaiwanScreener.tsx:794-797（只顯示 diagnostic_count，不顯示 trade_date 與 data_quality）
  - backend/app/taiwan/ai_research.py:284-302、:391-405（diag_item 為 None 時 abnormal_signals=[]，missing_items 沒有對應標記）
- **線上證據**：
  - 官方 TPEx dailyQuotes 2026-09-23（reference\csv\tpex_daily_quotes_20260923.csv）有 11,696 列，其中 supported 上櫃標的 1,007 列，證實 9/23 上櫃正常交易
  - 官方 TWSE MI_INDEX 9/23 加上 TPEx 9/23（supported 股票與 ETF、有成交）：全市場上漲比 970/(970+1069+259)=0.4221，成交額 1,144,409,887,354 元
- **本地計算證據**：
  - data\taiwan\daily：date=2026-09-16 與 date=2026-09-23 的 TPEX 列數為 0（explore1.py）
  - run_app.py：9/23 與 9/16 的 evaluated_symbol_count 為 1090 / universe 1986，overall_status 為 'complete'
  - gap.py：9/17 上櫃 1,006 檔 change_pct 全為 null，PRICE_MOVE 0 筆；9/18、9/21、9/22 上櫃 volume_ratio_5d 全部為 null，VOLUME_SPIKE 0 筆（9/10～9/15 為每日 97～157 筆）
  - gap.py：9/23 的 market_context 成交額 887,250,849,184（官方全市場 1,144,409,887,354，少 22.5%），advance_ratio 0.407（等於官方上市部分的 0.4076；官方全市場為 0.4221）
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：成立，嚴重度維持 P2。

- 異常診斷在目標日沒有日K時，整檔跳過（abnormal_diagnostics.py:340-342），連已經有資料的法人與融資訊號也一起丟掉。
- data_quality 只看目標日有沒有任何一列（:671-677），不比對 evaluated 與 universe，因此 9/16、9/23 上櫃整段缺日（本地 TPEX 0 列，官方當天正常交易）時，仍回報 complete（evaluated 1090 / universe 1986）。
- 缺日之後 4 個交易日（9/17～9/22），上櫃的 5 日量能訊號全部沒有評估；9/17 的漲跌幅也無法計算。
- Screener 異常面板只顯示 diagnostic_count，不顯示 trade_date 與 data_quality。

需修正的陳述：
- 同頁 Market Intelligence 面板會顯示「有效撮合標的 1326 / 2377」這個間接線索，但它的狀態同樣是日期層級的 complete，所以不會出現警示。
- AI 研究在缺日當天會收到 close=null，知道價格缺失。缺日之後的幾天，abnormal_signals: [] 則無法與「未評估」區分，也沒有 missing 標記。

使用者自選 9 檔都是上市，不受直接影響；但「全市場」診斷在缺日期間只涵蓋上市。
    - 理由：這條 finding 的核心主張全部成立：上櫃在 9/16、9/23 缺日時，整批上櫃標的直接被略過；缺日之後 4 個交易日，上櫃的量能訊號全部沒有評估；data_quality 仍然回報 complete；Screener 的異常面板只顯示 diagnostic_count。

程式碼查證（行號與 finding 一致）：
- 目標日沒有日K就 continue，所以上櫃標的有法人、融資資料也一起被跳過。這是原 finding 沒提到的面向：9/23 本地的法人分區有 TPEX 5,670 列、融資分區有 919 列，FOREIGN_FLOW_SPIKE、MARGIN_SURGE 等訊號本來可以評估，現在也一併消失。
- d_status 只看 len(df_target_daily) > 0，屬於日期層級判斷，不檢查覆蓋率。evaluated 1,090 檔、universe 1,986 檔的情況下，照樣判定 complete。
- 依 ISO/IEC 25012 的定義，completeness 是「具備所有預期屬性與相關實體實例的程度」。半個市場的實體不在，卻標成 complete，與這個定義矛盾。

本地資料（只用 polars 直接讀 parquet，沒有執行任何服務）：
- 9/16、9/23 的 TPEX 列數都是 0。9/16 缺日到今天（9/24）仍未回補，所以這不是單次暫態。
- 模擬程式邏輯的結果：9/17 上櫃前收可用 0 檔；9/17、9/18、9/21、9/22、9/23 上櫃 5 日量能可評估都是 0 檔，VOLUME_SPIKE 上櫃都是 0 筆。缺日前 9/10、9/11、9/14、9/15 分別是 102、139、90、91 筆。finding 寫的「97～157」與我算的數值略有出入（推測是計算口徑不同），不影響結論。

官方資料交叉比對：
- 我用的是與 finding 不同的資料集：TPEx tradingStock 6488／8069 月資料，以及櫃買指數月資料。兩者都證實 9/16、9/23 上櫃正常交易。
- 另以 Yahoo 的 8069.TWO 日線做第三方確認：收盤序列 145.5、146.0、148.0、145.5、146.5、144.5、145.0、144.0，與官方 9/14～9/23 逐日吻合，其中包含 9/16。

市場脈絡數值：
- 9/23 本地成交額 887,250,849,184 元，官方上市加上櫃（supported）合計 1,144,409,887,354 元，兩個數字與 finding 一字不差。
- 拆開來看，官方上櫃部分 253,232,230,458 元整段缺漏。

需要修正的過度陳述：
- 「面板看不出來」不完全正確。同頁的 Market Intelligence 面板會顯示「有效撮合標的 1326 / 2377 檔」，平日約 2337 / 2377，這是間接線索。但該面板的 overall_status 也是日期層級，同樣回報 complete，所以不會出現「尚無完整市場資料」的警示 badge；異常面板本身則完全沒有線索。
- 「AI 看不出來」只在缺日之後的幾天成立。缺日當天，研究脈絡的 price_context.close 為 null（meta 為 MISSING），系統提示要求把 null 陳述為資料不足，所以 AI 知道當天價格缺失，只是 abnormal_signals: [] 沒有標記。缺日後的 9/17～9/22，上櫃標的的 abnormal_signals 同樣是 []，而量能訊號其實沒有評估，也沒有任何 missing 標記。系統提示第 5 條已要求不得把 signal_count=0 解讀為「全部檢查且正常」，算是部分緩解，但 diag_item 為 None 時，連 abnormal.signal_count 這個鍵都不存在。

嚴重度：
- 根因（缺日）屬於資料更新領域。本 finding 是品質旗標與訊號可評估性的明確功能錯誤，會讓「全市場偵測到 N 檔」靜默縮成只有上市。
- 使用者自選 9 檔都是上市，不受直接影響。
- 維持 P2 恰當。
    - 證據：
      - backend/app/taiwan/abnormal_diagnostics.py:340-342：`t_daily = target_daily_map.get(sym)`／`if not t_daily: continue`，法人與融資訊號一併跳過
      - backend/app/taiwan/abnormal_diagnostics.py:378、:399：`v_stat["count_vol_5d"] == 5`／`v_stat["count_amt_5d"] == 5` 才評估；:211 `_resolve_prior_sessions` 以 available_dates() 取日期，只有上市列的 9/16 分區仍算一個 session
      - backend/app/taiwan/abnormal_diagnostics.py:671-677：`d_status = "current" if len(df_target_daily) > 0`，三個 current 即為 complete；:682 evaluated_symbol_count=len(target_daily_map)，沒有與 universe 比較
      - backend/app/taiwan/market_intelligence.py:389-392、:576-578：market_context 的 overall_status 同樣是日期層級判斷，所以 CompactMarketContext 也回報 complete
      - frontend/src/pages/TaiwanScreener.tsx:794-797：異常面板只顯示 `diagnostic_count`；全檔 grep abnormalData 只出現在 :284、:794、:796、:893、:894、:901，沒有使用 trade_date 或 data_quality
      - frontend/src/pages/TaiwanScreener.tsx:547-557：同頁 Market Intelligence 的警示 badge 只在 overall_status !== 'complete' 時出現；另顯示「有效撮合標的 traded_count / supported_count」，是間接線索
      - backend/app/taiwan/ai_research.py:284-302：`if diag_item and diag_item.signals: ... else: payload["abnormal_signals"] = []`；:123-275 的 missing_items 沒有 abnormal 相關項目；:391-405 查無 item 時 diag_item 維持 None
      - backend/app/taiwan/research_context.py:486：缺日當天 price meta 為 classification="MISSING"，AI 收到 close=null（部分緩解）
      - 本地計算（uv run python scratchpad\audit\work\verify_g1_dq\cov.py）：universe 股票 1986 檔（TWSE 1094、TPEX 892）；2026-09-16 與 2026-09-23 的 daily 分區 TPEX 列數都是 0，上市 1330 列、universe 命中 1090 檔；9/17 上櫃前收可用 0 檔；9/17、9/18、9/21、9/22、9/23 上櫃 5 日量能可評估 0 檔
      - 本地計算（vol.py，模擬 VOLUME_SPIKE 條件）：上櫃 VOLUME_SPIKE 9/10=102、9/11=139、9/14=90、9/15=91，9/16～9/23 全為 0；PRICE_MOVE 上櫃在 9/16、9/17、9/23 為 0
      - 本地計算：institutional 分區 2026-09-23 有 TWSE 1329 列、TPEX 5670 列；margin 分區有 TWSE 1297 列、TPEX 919 列。三個 store 目標日都有列，所以判定 complete
      - 本地計算：9/23 supported（stock+etf）成交額合計 887,250,849,184 元；9/22 為 1,339,555,264,362 元；volume>0 的標的 9/23 為 1326 檔，9/22 為 2337 檔（分母 2377）
      - 官方參考資料 reference\csv\tpex_trading_stock_202608_202609.csv：8069 在 2026-09-16 成交 3,921 張、收盤 148.0，9/23 收盤 144.0；6488 在 9/16 收盤 937.0；index_daily_202608_202609.csv 有 TPEX_INDEX 2026-09-16 收盤 399.21、9/23 收盤 413.76，證實上櫃正常交易
      - 官方參考資料：twse_mi_index_20260923_quotes.csv 的上市 supported 成交額 891,177,656,896 元，tpex_daily_quotes_20260923.csv 的上櫃 supported 成交額 253,232,230,458 元（1007 列），合計 1,144,409,887,354 元，與 finding 相符
      - 線上來源 https://iso25000.com/index.php/en/iso-25000-standards/iso-25012：ISO/IEC 25012 Completeness 定義原文為 "The degree to which subject data associated with an entity has values for all expected attributes and related entity instances in a specific context of use."
      - 線上來源 https://query1.finance.yahoo.com/v8/finance/chart/8069.TWO?period1=1789344000&period2=1790208000&interval=1d：回傳 8 個交易日，收盤序列 145.5、146.0、148.0、145.5、146.5、144.5、145.0、144.0，與官方 9/14～9/23 吻合（含 9/16 的 148.0）；抓取工具把時間戳轉成日期時轉錯，日期以序列對齊為準
      - 已知問題清單 docs/code-review-2026-09-22.md：沒有任何項目涵蓋異常診斷的 data_quality 或上櫃缺日，不是重複回報
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：本地日K在 2026-09-16 與 09-23 整天沒有上櫃列（0 列），但官方資料證明這兩天上櫃正常交易；同一天的法人與融資資料有上櫃列。get_diagnostics 對沒有目標日日K的標的直接略過（abnormal_diagnostics.py:340-342），而 data_quality 只要目標日有任何一列就判 current，三個資料集都是 current 就判 complete（:671-677）。因此兩天都回報 evaluated 1090/1986、overall_status=complete。
缺日還會波及之後的交易日：
- 9/17 以 9/16 為前一交易日，上櫃股票 887 檔（含 ETF 為 1,006 檔）的 change_pct 全為 null，PRICE_MOVE 不評估。
- 9/17、9/18、9/21、9/22 的前 5 日視窗都含 9/16，上櫃只有 4 筆，VOLUME_SPIKE 與 TURNOVER_SPIKE 全部不評估，結果是 0 筆。缺日前上櫃每天約 90～139 筆（只含股票）或 97～157 筆（含 ETF）。
9/23 的 market_context 實際上只涵蓋上市：advance_ratio 0.407（官方只看上市為 0.4076，上市加上櫃為 0.4221），成交額 887,250,849,184（官方 1,144,409,887,354，少 22.5%）。其中約 98.5% 的差額來自上櫃整體缺席，其餘約 3.9B 是另一個上市個股成交額不一致的問題。
Screener 只顯示 diagnostic_count，不顯示 trade_date 與 data_quality。AI 研究在 diag_item 為 None 時送出 abnormal_signals=[]，missing_items 不加註；有 item 但量能訊號沒評估時，同樣無法和「未觸發」區分。
9/23 缺日若不回補，之後 5 個有分區的交易日（9/24 起）會重演：上櫃 change_pct 為 null（只有 9/24），量能訊號全部不評估。結束日取決於休市日，未查證（推測）。自選 9 檔都是上市，不受直接影響。
    - 理由：我寫了一支唯讀腳本，直接讀 parquet，照 get_diagnostics 的邏輯重跑一次（沒有呼叫任何 store 或 service），也用官方 9/23 行情重算大盤數字。finding 裡的數值全部重現，只有兩個計數要補註口徑。
(1) 缺日確實存在：daily 分區 9/16 與 9/23 的 TPEX 列數都是 0，其他日約 1,005～1,009 列。官方資料證明上櫃兩天都有交易：6488 與 8069 在 9/16、9/23 都有成交，TPEX_INDEX 兩天也都有收盤；TPEx dailyQuotes 9/23 裡 supported 標的有 1,007 列。institutional 與 margin 兩天都有上櫃資料（9/23 分別有 5,670 列與 919 列），所以缺口只在日K。
(2) 狀態誤報成立。只含股票時，9/16 與 9/23 的 evaluated 都是 1090/1986，d/i/m 三個 status 都是 current，overall 為 complete。原因是 abnormal_diagnostics.py:671-677 只檢查 len(df_target_daily) > 0。
(3) 訊號沒有被評估。9/17 前一交易日是 9/16，上櫃股票 887 檔（含 ETF 為 1,006 檔）的 change_pct 全部是 null，PRICE_MOVE 為 0。9/17、9/18、9/21、9/22 的前 5 日視窗都包含 9/16，上櫃每檔都只有 4 筆（p5_full=0），volume_ratio 與 amount_ratio 全部是 null，VOLUME_SPIKE 與 TURNOVER_SPIKE 都是 0。缺日前的 9/10～9/15，上櫃每天的 VOLUME_SPIKE 在含 ETF 時是 106/157/99/97，也就是 finding 說的 97～157；只含股票時是 104/139/90/92。對照組上市同期正常，例如 9/18 有 222 筆。
(4) 9/23 大盤脈絡重現到個位數。本地上漲家數 538、下跌 647、平盤 137，advance_ratio 0.407，成交額 887,250,849,184。官方 supported 且有成交的標的：上市加上櫃是 970/(970+1069+259)=0.4221，成交額 1,144,409,887,354；只看上市是 0.4076。本地比官方少 22.47%。
(5) 程式碼證據屬實。TaiwanScreener.tsx:794-797 只顯示 diagnostic_count，abnormalData 在檔內只出現在 284、794、796、891、893、894、901 行，沒有任何地方讀 data_quality 或 trade_date。ai_research.py:284-302 在 diag_item 為 None 或沒有 signals 時，一律給 abnormal_signals=[]，missing_items 也不會加註。research_context.py:356 的 target 取全域最新交易日，所以 9/23 的上櫃標的會拿到 diag_item=None。
(6) docs/code-review-2026-09-22.md 裡的 B、C、N 各項都沒有提到這個問題，不是重複回報。

需要補註的口徑：
- finding 的「1,006 檔」與「97～157 筆」是 include_etfs=True（AI 研究路徑）的數字；公開 Screener 路由只含股票，對應數字是 887 檔與 90～139 筆。
- 成交額差 257.16B 裡，上櫃整體缺席占 253.23B（約 98.5%）。剩下約 3.93B 是上市個股的成交額與官方不一致，共 23 檔，例如 2330 本地成交股數 21,978,873，官方 22,817,873。這部分屬於別的議題，不在本 finding 範圍。
- 「9/24～9/30 會重演」本來就標為推測。依程式邏輯，受影響的是 9/23 之後、前 5 日視窗還涵蓋 9/23 的 5 個有分區交易日；結束日取決於休市日。我想上網確認 2026 年 9 月的休市日，但本 session 的 WebSearch 額度已用完，所以結束日沒有核實。
嚴重度維持 P2：這是覆蓋率與狀態標示的功能錯誤，根因（缺日沒有回補）屬於資料更新領域；自選 9 檔都是上市，不受直接影響。
    - 證據：
      - 缺日：data/taiwan/daily 的 TPEX 列數，9/15=1006、9/16=0、9/17=1006、9/22=1009、9/23=0。上市列數每天 1328～1333。指令：uv run python AUDIT_SCRATCH/work/verify-g1dq/replay.py（結果另存 replay.json）
      - 官方證明上櫃有開市：reference\csv\tpex_trading_stock_202608_202609.csv 裡，6488 在 9/16 成交 6,666 張、9/23 成交 28,715 張，8069 在 9/16 成交 3,921 張、9/23 成交 5,797 張；index_daily_202608_202609.csv 的 TPEX_INDEX 9/16 收 399.21、9/23 收 413.76
      - 狀態重現（只含股票，universe 1986＝上市 1094＋上櫃 892）：9/16 evaluated 1090、current/current/current、complete；9/23 evaluated 1090、complete
      - 9/17 前一交易日為 9/16：上櫃只含股票時 chg_null 887/887、vol_ratio_null 887、PRICE_MOVE 0；含 ETF 時 chg_null 1006/1006
      - 9/18、9/21、9/22 上櫃 vol_ratio_null 為 888/888、891/891、891/891，p5_full 全為 0，VOLUME_SPIKE 與 TURNOVER_SPIKE 全為 0；同日上市 VOLUME_SPIKE 為 222/147/133
      - 缺日前 9/10、9/11、9/14、9/15 上櫃 VOLUME_SPIKE：含 ETF 為 106/157/99/97（etf_early.py），只含股票為 104/139/90/92
      - 9/23 大盤脈絡（market.py）：本地 rows 1330、comparable 1327、上漲/下跌/平盤 538/647/137、advance_ratio 0.407、turnover 887250849184。官方 supported 且有成交：上市 538/642/140、上櫃 432/427/119，全市場 970/2298=0.4221，只看上市 0.4076，成交額 1144409887354（上市 891177656896＋上櫃 253232230458），本地少 22.47%
      - institutional 與 margin 在 9/16、9/23 都有上櫃資料：institutional 9/23 有 TPEX 5670 列，margin 9/23 有 TPEX 919 列
      - backend/app/taiwan/abnormal_diagnostics.py:340-342（沒有目標日日K就 continue）、:378、:399（count==5 才評估）、:671-677（len>0 就判 current，三者 current 就是 complete）、:251-252（prev_date 取全庫共用的前一個分區，不分交易所）
      - frontend/src/pages/TaiwanScreener.tsx:794-797 只顯示 diagnostic_count；檔內 abnormalData 只出現在 284/794/796/891/893/894/901 行，沒有讀取 data_quality 或 trade_date
      - backend/app/taiwan/ai_research.py:284-302（沒有 diag_item 或沒有 signals 時 abnormal_signals=[]，不寫 missing_items）、:343（系統提示「零數值非缺失」）、:397-405；backend/app/taiwan/research_context.py:356（target 取全域最新交易日）
      - 成交額缺口拆解：上市 23 檔個股的成交額與官方 MI_INDEX 不一致，例如 2330 本地 volume 21,978,873、官方 22,817,873，占約 3.93B；屬於別的議題

### [P2 → P2] 當日 16:30 寫入的日K量額與三大法人資料是初值，官方事後修正後不回補，使量能與法人訊號失真（確認成立）

- **主張**：diagnostics 讀取的 daily／institutional 分區在交易日 16:30 寫入後就不再更新；比對次日凌晨抓到的官方資料，部分大型股的成交量額與投信、外資買賣超明顯不同，訊號判定因此改變（可能與日K／法人資料領域重疊，此處只記錄對異常診斷的影響）。
- **正確的定義或數值**：異常診斷使用的量額與法人資料應為官方定稿值，或至少在次日回補修正。
- **app 實際行為**：使用 16:30 的初值且永不更新；成因推測為鉅額交易或投信資料的晚到申報。
- **對使用者的影響**：大型權值股的爆量與投信異常可能漏報或誤報（2330 在 9/18 為實例）。自選股在此期間沒有出現判定翻轉，但量比數值有落差。
- **修法建議**：每日更新對 T-1（或最近 N 日）的 daily、institutional 分區強制重抓並覆寫，或在晚間（例如 21:00 之後）再抓一次；data_quality 註記資料版本與抓取時間。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/daily_refresh.py:137-181（refresh_dates 對已有分區的日期 skip，不重抓）
  - abnormal_diagnostics.py:250、:261（直接讀取這些分區）
- **線上證據**：
  - 官方 STOCK_DAY notes：「當日統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購」（reference\raw\twse_stock_day_2379_202609.json）
  - 官方 STOCK_DAY 2330 在 2026-09-18 成交股數 40,892,688（reference\csv\twse_stock_day_202608_202609.csv）；MI_INDEX 9/23 的 2330 為 22,817,873，與 STOCK_DAY 相同
  - 官方 T86 9/23：3481 群創投信買賣超 −3,734,900；TPEx 9/22：3264 欣銓外資合計 +17,273,026、投信 −2,350,000
- **本地計算證據**：
  - official1b.py：9/23 的 store 與官方 MI_INDEX 比對，1,330 檔中 23 檔量額不同（收盤價 0 差異）；store 的 2330 在 9/18 為 37,190,511 股
  - official2.py：2330 在 9/18 的 app volume_ratio_5d 1.93、amount_ratio_5d 1.98，依官方為 2.11 與 2.16，因此漏報 VOLUME_SPIKE 與 TURNOVER_SPIKE；9/16～9/23 自選 6196、2454 的量比差最多 0.12
  - qts.py：daily 的 9/16、9/17、9/18、9/22、9/23 分區寫入時間都是當日 16:19～17:03；9/11～9/15 於 9/16 11:21 重寫，且與 STOCK_DAY 完全一致
  - official3.py：投信買賣超與官方不同的列數，上市 33、上櫃 12（9/22～9/23）；institutional 分區寫入時間為當日 16:30
  - trust_off.py：以官方目標日數值重算，9/22 的 TRUST_FLOW_SPIKE 由 44 變 46、FOREIGN 由 140 變 141（3264.TPEX 外資由不觸發變觸發）
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：每日更新對已寫入的 daily／institutional 分區從不重抓（daily_update.py:282/324 從最大日期的下一天開始，daily_refresh.py:167 與 institutional_margin_refresh.py:166 略過已存在的日期；21:40 那班也會跳過）。因此交易日 16:30 左右（9/18 為 17:03）抓到、尚未納入全部成分的量額與三大法人數值，會被永久沿用。依官方附註，正確值應包含鉅額交易；差異推測來自當日較晚的鉅額或組合交易，不是官方事後修正錯帳（T86 註明以原始成交統計）。實測：當天寫入的分區，量一律比官方少（9/23 上市 1,330 檔中有 23 檔），隔天才寫入的分區與官方完全一致；投信買賣超不一致在 9/22～9/23 共有上市 33 列、上櫃 12 列。後果是大型股訊號翻轉：2330 在 9/18 量比 1.93 對 2.11、額比 1.98 對 2.16，漏報 VOLUME_SPIKE 與 TURNOVER_SPIKE；3264 在 9/22 外資與投信訊號、3481 在 9/23 投信訊號漏報。自選股沒有翻轉，量比差最多 0.12。同一根因還有 finding 沒提到、影響更大的面向：9/23 16:30 抓 TPEx 時連線中斷，只寫入上市列且回報 success，該日整個上櫃市場（約 1,006 檔）的日K永久缺失；9/16 分區同樣只有上市列，原因推測相同。這使 9/23 的異常診斷不含任何上櫃股，也讓上櫃股以 9/17～9/23 為目標日時量額訊號因基期不足而停用，建議另立一條 finding。修法：21:40 那班對 T 與 T-1 強制重抓並覆寫；另外只有一個市場成功時，不要把該日期當成已完成。
    - 理由：這條 finding 成立，嚴重度維持 P2。我逐項查了程式碼、本地 parquet、官方參考資料和 backend.log。

（1）程式碼：每日更新本身不會重抓已經寫入的日期，這點已確認。daily_update.py 先用 resolve_missing_date_range 算出抓取範圍，從既有最大日期的下一天開始；日K和法人的 refresh_dates 也會把已存在的日期跳過。21:40 那班的註解更直接寫明「已落盤的日期會被 refresh 略過」。所以 16:30 或 17:03 寫入的值，之後的排程都不會覆寫。

（2）本地資料和官方比對：自選股等 10 檔上市股票，9/1～9/15 與 9/21 這幾天是隔天才抓的，和官方 STOCK_DAY 完全一致。9/16、9/17、9/18、9/22、9/23 是當天抓的，每天各有 2～6 檔的量額偏低，而且一律是 store 比官方少；收盤價則 0 差異。9/23 全體上市 1,330 檔中有 23 檔量不同。2330 在 9/18 的 store 為 37,190,511 股，官方為 40,892,688 股；9/23 的 MI_INDEX 與 STOCK_DAY 同為 22,817,873 股。投信買賣超不同的列數：上市 9/22 有 16 列、9/23 有 17 列，合計 33；上櫃 5＋7＝12。這些都和 finding 的數字一致。

（3）訊號翻轉：我用 app 的判定式重算。2330 在 9/18 的量比從 1.93 變 2.11、額比從 1.98 變 2.16，漏報 VOLUME_SPIKE 與 TURNOVER_SPIKE。3264 在 9/22 的外資倍數從 1.02 變 13.6、投信倍數從 1.03 變 4.17，兩個訊號都漏報。3481 在 9/23 的投信倍數從 0.02 變 8.39，也漏報。自選股沒有翻轉；6196 在 9/17 的量比差 0.12（0.57 對 0.69）。

（4）定義：官方 MI_INDEX 行情表和 T86 的附註都寫「本統計資訊含一般、零股、盤後定價、鉅額（交易）」，所以應以包含鉅額交易的完整值為準。T86 另註明「以當日原始成交情形統計，不以證券商申報錯帳、更正帳號等調整後資料統計」，因此 finding 用「官方事後修正」這個說法不夠精確。比較可能的情況是 16:30 或 17:03 抓的時候，官方還沒納入全部成分（推測是當天較晚的鉅額或組合交易），不是官方改寫了已公布的數字。9/18 的資料是 17:03 抓的，仍少 3.7M 股，所以單把排程延到 17:00 之後不一定夠。上網查證方面，MoneyDJ 的鉅額交易定義確認「五佰交易單位以上」屬於鉅額交易。專案文件 docs/taiwan-data-sources.md:88-100 的官方對帳也顯示，TPEx 日收盤總量包含鉅額交易。我沒有在網上找到鉅額交易逐筆時段的權威來源：WebSearch 額度已用完，Bing 與 DuckDuckGo 查不到有用結果，所以鉅額交易時段沒有核實。

（5）finding 沒提到、同一根因造成的更嚴重新面向：9/23 16:30 抓 TPEx dailyQuotes 時連線中斷（incomplete chunked read）。fetch_date 吞掉了這個例外，只寫入上市 1,330 列，status 仍是 success；21:40 那班又因為日期已存在而跳過。結果 9/23 整個上櫃市場（約 1,006 檔）的日K永久缺失；9/16 也同樣只有上市列，但當天的 log 已經輪替掉，原因只能推測。後果是 9/23 的異常診斷完全不含上櫃股，而且 9/16 缺漏會讓上櫃股以 9/17～9/23 為目標日時，前 5 日基期不滿 5 筆，量額訊號因此停用。這個面向較適合獨立成一條 P1/P2 finding。

嚴重度：量額與法人數值偏低屬於資料未取得定稿值，屬於明確的功能錯誤，不是錯誤的金融口徑。它每天影響少數大型股（上市約 1.7%），自選股沒有翻轉，所以 P2 恰當。
    - 證據：
      - backend/app/taiwan/daily_update.py:282 與 :324：resolve_missing_date_range(d_max/i_max, target) 只從既有最大日期的下一天開始抓；:128-147 在 earliest_available >= target 時回傳 None
      - backend/app/taiwan/daily_refresh.py:150、:167-170：available = set(store.available_dates())；if cur in available: dates_skipped += 1（沒有 force 就不重抓）
      - backend/app/taiwan/institutional_margin_refresh.py:143、:166-169：法人資料的 refresh 用同樣的邏輯略過已存在日期
      - backend/app/jobs/daily_pipeline.py:180（16:30 排程）、:194-195 註解「已落盤的日期會被 refresh 略過, 不重複下載」
      - backend/app/taiwan/abnormal_diagnostics.py:250（df_target_daily）、:261（df_target_inst）、:378-381（量比 >= 2.0）、:445-449、:467-471（外資、投信倍數 >= 3.0）
      - 本地 cmp_daily.py（AUDIT_SCRATCH/work/verify-g1-late-revision/）：store 對 STOCK_DAY 的量不一致數：9/1～9/15 為 0（09-16 11:21 寫入）、9/21 為 0（09-22 10:55 寫入）；9/16 為 2（16:30:22）、9/17 為 4（16:19:15）、9/18 為 6（17:03:37）、9/22 為 2（16:30:03）、9/23 為 2（16:30:05）；收盤價全為 0 差異；2330 在 9/18 官方 40,892,688、store 37,190,511
      - 本地 cmp_full.py：9/23 上市 1,330 檔比對 MI_INDEX，23 檔量不同（2330 官方 22,817,873、store 21,978,873），收盤價 0 差異；投信不一致數：T86 9/22 為 16、9/23 為 17，TPEx 9/22 為 5、9/23 為 7；3481 在 9/23 投信 store −9,900、官方 −3,734,900；3264 在 9/22 外資 store 1,300,618、官方 17,273,026
      - 本地 ratios.py：2330 在 9/18 store 量比 1.93、額比 1.98，官方 2.11、2.16（翻轉）；3264 在 9/22 外資倍數 1.02 對 13.6、投信 1.03 對 4.17；3481 在 9/23 投信倍數 0.02 對 8.39；6196 在 9/17 量比 0.57 對 0.69；自選股 2454、3034、2379、6196、3545 在 9/16～9/23 都沒有翻轉
      - 官方附註（reference\raw\twse_t86_20260923.json notes）：「本統計資訊含一般、零股、盤後定價、鉅額，不含拍賣、標購。」「本資訊以當日原始成交情形統計，不以證券商申報錯帳、更正帳號等調整後資料統計。」MI_INDEX 行情表附註同樣寫「本統計資訊含一般、零股、盤後定價、鉅額交易」
      - 線上：https://www.moneydj.com/KMDJ/wiki/wikiviewer.aspx?keyid=6aabb5c6-179c-4a22-95ee-9b48fe83eee7：「股票交易凡一次買賣同一上市證券數量在…總數五佰交易單位（意即五十萬股）以上者，稱為鉅額交易」；鉅額交易時段查不到（WebSearch 額度用完，Bing 與 DuckDuckGo 查不到有用結果），未核實
      - 專案文件 docs/taiwan-data-sources.md:88-100：TPEx 6488 官方對帳，日收盤總量 11,772,540 股中含鉅額交易 250,000 股
      - 新面向：data/backend.log:37258「2026-09-23 16:30:05 TPEx snapshot fetch failed for 2026-09-23: peer closed connection without sending complete message body」，:37265 仍記 daily=success；:45470-45477 21:40 那班沒有重抓日K。daily 分區 9/16 與 9/23 只有 TWSE 1,330 列、TPEX 0 列，其他日期約 1,006 列（snapshot_provider.py:113-118 吞掉 TPEx 例外後照樣回傳上市資料）
  - **reproduction**：confirmed；修正後嚴重度 P2
    - 修正後敘述：異常診斷讀取的 daily／institutional 分區，只要在交易日當天（16:19～17:03）寫入過，之後就不再更新。原因是 run_update 只從 d_max／i_max 的下一日開始抓，refresh_dates 也會略過已存在的日期，21:40 那一班同樣略過。

與官方資料比對：9/23 上市 1,330 檔中有 23 檔的量額低於官方 MI_INDEX（收盤價沒有差異）。2330 在 9/18 的成交股數是 37,190,511，官方為 40,892,688，量比與額比因此由 2.11／2.16 降到 1.93／1.98，漏報 VOLUME_SPIKE 與 TURNOVER_SPIKE。9/22～9/23 的投信買賣超有 33 列上市、12 列上櫃與官方 T86／上櫃法人資料不同，修正方向有增有減：例如 1560 的 store 值大於官方，會造成誤報。

以官方目標日數值重算 9/22：預設公開路由（只看股票）的 TRUST_FLOW_SPIKE 由 37 變 39、FOREIGN_FLOW_SPIKE 由 100 變 101；股票加 ETF 範圍（include_etfs=True）則由 44 變 46、由 140 變 141。自選股的量比最多差 0.12，沒有判定翻轉。

同一個「不重抓」的根因還造成更大的缺口：9/16 與 9/23 的 daily 分區在當天寫入時上櫃快照抓取失敗（9/23 的 log 為 incomplete chunked read），只落盤了上市 1,330 列，且永不補抓。結果是：這兩天約 890 檔上櫃股票完全不出現在異常診斷；9/17 上櫃股的 PRICE_MOVE 無法判定；9/17～9/22 所有上櫃股的 VOLUME／TURNOVER 因為前 5 日不足而無法判定。

量額與法人差異的成因推測是鉅額交易等晚到資料，官方註記寫明統計含鉅額交易。
    - 理由：我用官方參考資料和本地 parquet 自己寫腳本重算（放在 scratchpad\audit\work\verify-g1late\），finding 的數字幾乎全部逐一重現，程式碼路徑也成立。只有法人訊號的兩個計數要補上前提：44→46、140→141 是在「上市櫃股票加 ETF」範圍（include_etfs=True）下算出的；預設的公開路由只看股票，數字是 37→39、100→101。兩種範圍下翻轉的是同一批標的，所以結論不變。

程式碼：run_update 只從 d_max 或 i_max 的下一個交易日開始抓（daily_update.py:282、:324）。refresh_dates 遇到已存在的日期就略過（daily_refresh.py:167-170）。21:40 那一班原本就預期已落盤的日期會被略過（daily_pipeline.py:194-195），因此 16:30 寫入的分區之後不會再更新。

數據對照（finding 對我的結果）：
- 9/23 store 對 MI_INDEX：兩邊都是 1,330 檔對上、23 檔量額不同、收盤價 0 差異。23 檔全部是 store 比官方低。
- 2330 在 9/18 的成交股數：store 37,190,511，官方 40,892,688，兩邊一致。
- 2330 在 9/18 的量比與額比：app 1.93／1.98，官方 2.11／2.16，一致；依官方值 VOLUME_SPIKE 與 TURNOVER_SPIKE 會觸發。
- 自選股：6196 量比最大差 0.12，出現在 9/17；2454 最大差 0.06；沒有任何判定翻轉。一致。
- STOCK_DAY 對照：10 檔共 380 列，有差的 16 列全部落在當日寫入的 9/16、9/17、9/18、9/22、9/23。隔日才寫入的分區（9/1～9/15 於 9/16 11:21 寫入，9/21 於 9/22 10:55 寫入）全部與官方一致。
- 投信買賣超不同的列數：上市 16+17=33，上櫃 5+7=12，與 finding 相同。3481、3264 的個別數值也相同。

原記錄沒提到的新面向：
1. 同一個「不重抓」的根因，讓 9/16 與 9/23 的 daily 分區只有上市 1,330 列，上櫃是 0 列。log 顯示 9/23 16:30:05 上櫃快照抓取失敗，錯誤是 incomplete chunked read。fetch_date 把這個例外吞掉，只回傳上市資料；daily 狀態仍記為 success，之後永不補抓。對異常診斷的影響比量額初值大得多：9/16、9/23 約 887～891 檔上櫃股票完全不出現在診斷裡；9/17 的上櫃股缺前一日資料，PRICE_MOVE 無法判定；9/17～9/22 的上櫃股全部因為前 5 日只有 4 日，VOLUME_SPIKE 與 TURNOVER_SPIKE 無法判定。9/24 起的 5 個交易日也會因為 9/23 缺上櫃資料而受同樣影響（推測，照同一邏輯推算）。
2. 修正方向不是只有向上。1560.TWSE 的 store 投信買賣超大於官方（9/22：381,600 對 86,600），於是在 store 上出現官方數字不會觸發的 TRUST_FLOW_SPIKE。
3. 9/23 若把目標日換成官方量額：VOLUME_SPIKE 由 81 變 83、TURNOVER_SPIKE 由 86 變 88，新增 1109、6464（只算股票範圍）。

成因是鉅額交易晚到：官方註記寫明統計「含鉅額交易」，而差額多為整千股，兩者吻合；但這仍是推測。9/18 在 17:03 寫入的分區同樣偏低。這次 WebSearch 額度已用完，無法再上網核實鉅額交易的時段。

嚴重度維持 P2：明確的功能錯誤，但自選股 9 檔都是上市，這段期間沒有判定翻轉。
    - 證據：
      - backend/app/taiwan/daily_update.py:282 與 :324：d_range 和 i_range 只從 d_max／i_max 的下一日開始算；backend/app/taiwan/daily_refresh.py:167-170：已有的日期會被 skip
      - backend/app/jobs/daily_pipeline.py:178-208：16:30 與 21:40 兩班排程；註解寫明「已落盤的日期會被 refresh 略過」
      - 分區寫入時間（stat mtime）：daily 的 9/16 在 16:30:22、9/17 在 16:19:15、9/18 在 17:03:37、9/22 在 16:30:03、9/23 在 16:30:05；9/11～9/15 於 9/16 11:21:37～40；9/21 於 9/22 10:55。institutional 的 9/22 在 16:30:07、9/23 在 16:30:09
      - v_daily.py（scratchpad\audit\work\verify-g1late\v_daily.out）：STOCK_DAY 10 檔 380 列中 16 列不同，全部落在 9/16、9/17、9/18、9/22、9/23，且都是 store 低於官方；2330 在 9/18：store 37,190,511，官方 40,892,688
      - v_daily.py：9/23 store 對 MI_INDEX，對上 1,330 檔，vol_diff=23、amt_diff=23、close_diff=0；23 檔都是 store 低於官方，例如 2330：21,978,873 對 22,817,873，2376：2,629,451 對 3,929,451
      - MI_INDEX 9/23 raw notes：「本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。」（reference\raw\twse_mi_index_20260923.json）
      - v_ratio.py：2330 以 9/18 為目標日、前 5 日為 9/11～9/17；store 的量比／額比為 (1.93, 1.98)，官方為 (2.11, 2.16)，判定翻轉；自選股最大差：6196 為 0.12（9/17），2454 為 0.06，沒有翻轉
      - v_inst.py：投信不同的列數，上市 9/22 為 16、9/23 為 17（合計 33），上櫃 9/22 為 5、9/23 為 7（合計 12）；3481 在 9/23 投信 store −9,900、官方 −3,734,900；3264 在 9/22 外資 store 1,300,618、官方 17,273,026，投信 store −581,000、官方 −2,350,000
      - v_flow_mode.py：以 9/22 為目標日，股票加 ETF 的範圍下 TRUST 由 44 變 46、FOREIGN 由 140 變 141；只算股票（預設公開路由）時 TRUST 由 37 變 39、FOREIGN 由 100 變 101。翻轉標的：TRUST 新增 2383、3264、8028，移除 1560；FOREIGN 新增 3264
      - 新面向：data\backend.log 第 37258 行：「2026-09-23 16:30:05 WARNING snapshot_provider: TPEx snapshot fetch failed for 2026-09-23: peer closed connection without sending complete message body」；backend/app/taiwan/providers/snapshot_provider.py:112-124 吞掉這個例外，只回傳上市資料；daily 的 9/16、9/23 分區都是 1,330 列、上櫃 0 列，而官方 9/23 dailyQuotes 對應的上櫃資料有 1,007 檔
      - v_flow.py：上櫃股票在 9/17～9/22 的 VOLUME／TURNOVER 無法判定的檔數是 887～891 檔（全部）；9/17 沒有前一日資料（PRICE_MOVE 無法判定）的有 887 檔；9/16、9/23 的目標日上櫃列數為 0
      - v_flow.py：9/23 把目標日換成官方 MI_INDEX 量額後，VOLUME_SPIKE 由 81 變 83、TURNOVER_SPIKE 由 86 變 88（新增 1109.TWSE、6464.TWSE）；9/23 的 TRUST 新增 3481、移除 1560
      - 唯讀確認：git status 仍只有原本的 3 個 M 檔；data\taiwan 底下沒有任何檔案比我的第一支腳本新

### [P3 → P3] 嚴重度分級與 evidence 欄位語意未文件化、刻度不一致，也沒有被任何畫面使用（確認成立）

- **主張**：severity 切點在各訊號之間不一致（量能 2/3/5 → moderate/high/extreme；法人 3/6 → high/extreme，沒有 moderate；融資融券 3/6 → moderate/high，沒有 extreme；背離固定為 high），docstring 與前端都沒有定義。PRICE_FLOW_DIVERGENCE 把外資股數放進 baseline 欄、threshold 寫 3.0，AI payload 會直接轉傳這個 baseline。
- **正確的定義或數值**：文件化分級規則並統一刻度，或移除未使用的欄位；背離訊號的 baseline 應為基期值（外資 20 日平均），外資股數另存欄位。
- **app 實際行為**：未文件化、未使用；欄位語意混用。
- **對使用者的影響**：目前沒有直接的畫面影響；Screener tooltip 會顯示「基期: -375708」這類難懂的值，AI 也可能誤讀 baseline。
- **修法建議**：在 docstring 與 signals.ts 補上分級表或移除該欄；背離 evidence 改為 baseline=mean_abs_f20、ratio=f_mult，外資淨額另存 observed_flow。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - abnormal_diagnostics.py:382-384、:424、:450、:472、:496、:516、:534、:553、:571、:599
  - abnormal_diagnostics.py:555-557、:573-575（observed=chg_pct*100、baseline=f_net）
  - ai_research.py:290-298（payload 不含 severity，但含 baseline）
  - signals.ts:216-225（定義沒有 severity 欄）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - grep 結果：前端除了 api.ts 型別之外，沒有任何地方讀取異常訊號的 severity
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：以下陳述都已用本地資料實測重現。

嚴重度切點：
- VOLUME_SPIKE、TURNOVER_SPIKE：2/3/5 倍對應 moderate/high/extreme。
- FOREIGN_FLOW_SPIKE、TRUST_FLOW_SPIKE：3/6 倍對應 high/extreme，沒有 moderate。
- MARGIN_SURGE、SHORT_SURGE：3/6 倍對應 moderate/high，沒有 extreme。
- PRICE_MOVE：5%/7%/9.5% 對應 moderate/high/extreme。
- SHORT_MARGIN_RATIO_SPIKE：5/10 個百分點對應 moderate/high。
- RELATIVE_STRENGTH_OUTLIER：10%/15% 對應 moderate/high。
- PRICE_FLOW_DIVERGENCE：固定為 high（abnormal_diagnostics.py:554、:572）。
- Literal 宣告了 "low"，但實測 96,864 筆沒有任何一筆產生 low。

文件與使用情形：
- docstring、signals.ts 都沒有定義分級規則。
- 前端只有 api.ts 型別宣告 severity，沒有任何畫面使用；AI payload（ai_research.py:290-298）也不帶 severity。

PRICE_FLOW_DIVERGENCE 的 evidence 欄位：
- baseline 放的是當日外資淨買賣股數，已與官方 T86 逐筆比對一致；ratio 為 null。
- Screener 的 tooltip 把這個值標成「基期」，例如 3008.TWSE 在 2026-09-18 顯示「基期: -375708」；AI payload 也原樣轉傳這個 baseline。
- threshold=3.0 本身沒有錯，它是 3% 價格門檻，單位與 observed 一致。

修法：
- 不應把 baseline 改成 mean_abs_f20，那樣仍會與 observed（價格 %）的單位不一致。
- 建議 baseline 比照 PRICE_MOVE 設為 0.0 或 null，外資淨額、f_mult、mean_abs_f20 另設明確欄位。
- 同時在 docstring 與 signals.ts 補上分級表，或移除未使用的 severity 欄位。
    - 理由：我照 abnormal_diagnostics.py 的訊號 1–9，寫了一支唯讀的重播腳本，直接用 polars 讀本地 daily、institutional、margin 的 parquet，不經過任何 Store 類別。範圍是 99 個目標日（2026-05-06～2026-09-23），標的為股票加 ETF。總共觸發 96,864 筆訊號，finding 對事實的各項陳述都重現了：

(1) 嚴重度切點各訊號不一致，實測值與程式碼相符：
- VOLUME_SPIKE、TURNOVER_SPIKE：ratio 2.0–2.99 為 moderate，3.0–4.99 為 high，5.0 以上為 extreme。
- FOREIGN_FLOW_SPIKE、TRUST_FLOW_SPIKE：3.0–5.99 為 high，6.0 以上為 extreme，沒有 moderate。
- MARGIN_SURGE、SHORT_SURGE：3.0–5.99 為 moderate，6.0 以上為 high，沒有 extreme。
- PRICE_FLOW_DIVERGENCE：1,566 筆全部是 high。
- 同樣落在 ratio 3.0–5.99：外資 7,936 筆判 high，融資 2,507 筆判 moderate，量能則有 1,059 筆判 extreme。
- finding 漏列的部分：PRICE_MOVE 以 5%/7%/9.5% 分為 moderate/high/extreme；SHORT_MARGIN_RATIO_SPIKE 以 5/10 個百分點分為 moderate/high；RELATIVE_STRENGTH_OUTLIER 以 10%/15% 分為 moderate/high。此外 Literal 雖宣告了 "low"，但 96,864 筆中沒有任何一筆是 low。

(2) 模組 docstring 只列觸發門檻，Field 描述只寫「確定性嚴重度判定」；signals.ts 的 AbnormalSignalDefinition 沒有 severity 欄位，也沒有任何分級說明。

(3) 前端 severity 的使用：前端只有 api.ts:1357 的型別宣告用到異常訊號的 severity。其他命中的 severity（TaiwanStockDetail、AlertToast、RuleEditor 等）都是監控規則或告警的 severity，與異常訊號無關。後端也沒有任何地方讀取 .severity 或 .baseline，唯一例外是 ai_research.py:295 轉傳 baseline。

(4) PRICE_FLOW_DIVERGENCE 的 evidence 欄位：1,566 筆的 baseline 都是當日的外資淨買賣股數，絕對值介於 200,000 到 150,842,831；ratio 全部是 null；threshold 全部是 3.0。
- finding 舉的「基期: -375708」完全重現：3008.TWSE，2026-09-18，observed 6.8106，baseline -375708.0，f_mult 2.0，mean_abs_f20 188,142.1。
- 另與官方 T86 交叉比對，9/22–9/23 的 8 筆背離，baseline 都逐位等於 T86 的「外陸資買賣超股數（不含外資自營商）」。例如 8438 在 9/23 為 -247,378，2498 為 -4,149,770，2241 在 9/22 為 -1,221,252。
- 同一批的 observed 也與官方 MI_INDEX 的收盤與漲跌推算一致：2351 為 -5.2411%，2498 為 3.8653%，8438 為 4.5576%。
- 換句話說，Screener 在 TaiwanScreener.tsx:951 標為「基期」的值，其實是當日外資淨額。

(5) 影響面：99 個交易日每天至少有一檔股票觸發背離，股票平均約每日 15 筆，所以 Screener 的 tooltip 每天都會出現這種標錯的「基期」。
- 使用者自選股中，2379 在 2026-06-02 與 06-05、6196 在 06-15 與 07-31、3034 在 07-13 都有背離訊號，AI payload 會帶這個 baseline。
- 「AI 會誤讀」屬推測，我沒有實跑 AI。

需要修正的細節：
- 引用行號 :553、:571 是 subtype 行，severity="high" 實際在 :554 與 :572。
- threshold=3.0 本身沒有錯：它是 3% 的價格門檻，單位與 observed（漲跌幅 ×100）一致。真正的問題是 baseline 放了當日外資淨額，而且 ratio 沒有填入 f_mult。
- expected 與 fix 建議的「baseline 改為 mean_abs_f20」，仍會讓 observed（價格 %）與 baseline（股數）單位不一致。較合理的做法是 baseline 比照 PRICE_MOVE 設為 0.0 或 null，外資淨額、f_mult、mean_abs_f20 則另設明確欄位。

嚴重度：severity 欄位沒有畫面使用，屬於維護性問題；baseline 標錯只影響 tooltip 與 AI payload 的解讀，沒有交易結果影響。因此維持 P3，屬局部體驗與文件問題。
    - 證據：
      - backend/app/taiwan/abnormal_diagnostics.py:382-384（VOLUME 2/3/5）、:403-405（TURNOVER 同）、:424（PRICE_MOVE 5/7/9.5%）、:450、:472（法人 3/6 對應 high/extreme）、:496、:516（融資、融券 3/6 對應 moderate/high）、:534（券資比 5/10pp）、:554、:572（背離固定為 high；finding 寫的 :553、:571 是 subtype 行）、:599（RS 10/15%）
      - backend/app/taiwan/abnormal_diagnostics.py:85：Literal 含 "low"，描述只寫「確定性嚴重度判定」；:87：baseline 的描述為「基期比較值 (如 5D/20D 均值或中位數)」；:555-557、:573-575：observed=chg_pct*100.0、baseline=f_net、threshold=3.0，沒有設 ratio
      - backend/app/taiwan/ai_research.py:290-298：payload 帶 observed、baseline、ratio、delta，不含 severity、threshold、formula；整個 backend\app 裡只有這一處讀取 baseline（Grep 查 '\.baseline|\.severity|"baseline"'）
      - frontend/src/lib/signals.ts:216-225：AbnormalSignalDefinition 沒有 severity；signals.ts 找不到「嚴重」或 severity；frontend\src 中異常訊號的 severity 只出現在 api.ts:1357 的型別；TaiwanScreener.tsx:951 的 tooltip 為 `${sig.formula} (觀測值: ${sig.observed}, 基期: ${sig.baseline})`
      - 重播腳本：AUDIT_SCRATCH/work/verify-g1sev/repro.py（指令：cd backend && uv run python <該檔>）。輸出 triggered_signals.csv（96,864 筆，99 個目標日：2026-05-06～2026-09-23）與 divergence.csv（1,566 筆：股票 1,496、ETF 70）
      - 分級實測（ratio 或 delta 的最小至最大值）：FOREIGN high 3.0–5.99（7,936 筆）、extreme ≥6.0（2,383 筆）；MARGIN moderate 3.0–5.99（2,507 筆）、high ≥6.0（2,727 筆）；VOLUME moderate 2.0–2.99、high 3.0–4.99、extreme ≥5.0；SHORT_MARGIN_RATIO moderate 5.0–9.99、high ≥10.0；DIVERGENCE 全為 high（1,566 筆）；low 共 0 筆
      - finding 的數值重現：3008.TWSE，2026-09-18，PRICE_UP_FOREIGN_SELL，observed 6.8106，baseline -375708.0，ratio null，threshold 3.0，f_mult 2.0，mean_abs_f20 188142.1。divergence.csv 中 ratio 全為 null，threshold 唯一值為 [3.0]
      - 官方 T86（reference\csv\twse_t86_20260922_20260923.csv，欄位 foreign_ex_dealer_net_shares）逐筆等於背離的 baseline：2241 在 9/22 為 -1221252；4956 在 9/22 為 1839000；6120 在 9/22 為 -1576697；6533 在 9/22 為 797891；8215 在 9/22 為 1806000；2351 在 9/23 為 1834684；2498 在 9/23 為 -4149770；8438 在 9/23 為 -247378
      - 官方 MI_INDEX 9/23（reference\csv\twse_mi_index_20260923_quotes.csv）：2351 收 226、漲跌 -12.5，等於 -5.2411%；2498 收 41.65、+1.55，等於 3.8653%；8438 收 78.0、+3.4，等於 4.5576%。三者都與背離的 observed 一致
      - 自選股受影響的背離：2379 在 2026-06-02 的 baseline 為 -2553847、在 06-05 為 -2833213；6196 在 06-15 為 -839360、在 07-31 為 -440353；3034 在 07-13 為 +2383371
      - 旁支觀察（不屬於本 finding，未深查）：本地 data\taiwan\daily 缺少 date=2026-07-10 分區。官方 TWT49U 顯示 3034 於 2026-07-10 除息，可見當天有開市。因此 3034 在 7/13 的漲跌幅是以 7/09 收盤 542 為基準，算出 -13.75%，超過 ±10% 漲跌幅限制

### [P3 → P3] 市場脈絡 advance_ratio 母體含 ETF，產業脈絡只含股票，兩者不可直接比較（確認成立）

- **主張**：CompactMarketContext.advance_ratio 取 market_totals（股票加 ETF、上市加上櫃），CompactIndustryContext.advance_ratio 只算股票；個股診斷同時列出兩者，容易被當成同一口徑。
- **正確的定義或數值**：市場脈絡改用股票口徑（stock breadth），或欄位名稱與說明註明含 ETF。
- **app 實際行為**：混用母體。
- **對使用者的影響**：輕度：市場上漲比被 ETF 拉高約 5 個百分點（9/23 例）。
- **修法建議**：改用 market_snap.by_instrument.stock 計算 advance_ratio，或新增 stock_advance_ratio 欄位。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - abnormal_diagnostics.py:275-279
  - market_intelligence.py:375-379（instrument_type in [stock, etf]）、:445-447（totals 來自全部 joined）
  - industry_intelligence.py:195-198（只含 stock）
- **線上證據**：
  - 官方 MI_INDEX 9/23 漲跌證券數（reference\csv\twse_mi_index_20260923_updown_counts.csv）：「股票」欄上漲 388、下跌 562、持平 123，與「整體市場」欄分開列示
- **本地計算證據**：
  - breadth.py：9/23 官方上市股票 391/567/126 → 0.3607；上市股票加 ETF 為 0.4076；app 顯示 0.407（當日上櫃缺日，只剩上市）
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：CompactMarketContext.advance_ratio 由 market_totals 計算，母體是上市加上櫃的股票加 ETF（market_intelligence.py:378、:451）；CompactIndustryContext.advance_ratio 只算股票（industry_intelligence.py:197）。兩者並列在同一個診斷項目的 API 回應裡（abnormal_diagnostics.py:655-656）。AI 研究 payload 也把它們並列（research_context.py:632-635 → ai_research.py:141-147、162-165），但前端沒有顯示診斷的 market_context.advance_ratio。

9/23 重現結果：
- app 市場上漲比 0.4070（538／647／137），股票口徑 0.3600（391／571／124），ETF 0.6229，差距 +4.70pp。
- 官方口徑：上市股票 0.3614（391／567／124；官方漲跌統計的股票欄是 388／562／123，比例 0.3616），上市股票加 ETF 0.4085，差距 +4.71pp。
- 當日上櫃缺資料，所以 market_totals 只有上市。

ETF 的影響方向不固定：近 60 個交易日差距介於 −3.7pp 到 +5.0pp，平均絕對值約 2.1pp，有一半的交易日 ≥2pp。所以應改為「ETF 使市場上漲比與股票口徑偏離，可能偏高也可能偏低」，不是固定拉高。

修正建議不變：改用 by_instrument.stock 計算，或欄位與說明註明含 ETF。
    - 理由：我用本地 parquet 照著 app 的邏輯重算了一次，也用已下載的 TWSE MI_INDEX 9/23 資料獨立核對，finding 的核心主張可以重現：市場脈絡的 advance_ratio 母體是「股票加 ETF」，產業脈絡只算股票；9/23 兩者相差約 4.7 個百分點，方向與量級都符合 finding。

【程式碼口徑】
- abnormal_diagnostics.py:275-279 用 market_snap.market_totals 算 adv_ratio。
- market_totals 的母體在 market_intelligence.py:378 過濾為 instrument_type ∈ [stock, etf]，並在 :451 由 totals = self._calc_breadth(joined) 產生。finding 寫的 :445-447 行號偏移了約 4 行。
- 產業母體在 industry_intelligence.py:197 限定 instrument_type == "stock"。
- 兩者都放在同一個診斷項目裡，見 abnormal_diagnostics.py:144-145 與 :655-656。

【數值並列（9/23，我的數字 vs finding 的數字）】
1. app 的 market_totals：538 漲／647 跌／137 平，比例 0.4070。finding 寫 0.407，一致。
2. app 的 by_instrument.stock：391 漲／571 跌／124 平，比例 0.3600。產業口徑把全部產業合併後同樣是 391／571／124，比例 0.36，證明這才是與產業可比的市場口徑。
3. app 的 ETF：147 漲／76 跌／13 平，比例 0.6229。
4. 官方 MI_INDEX 漲跌證券數的「股票」欄：388 漲／562 跌／123 平，比例 0.3616。finding 引用的 388／562／123 正確。
5. 官方逐檔行情對到 app 的上市股票母體（1090 檔）：391／567／124，比例 0.3614。finding 寫 391／567／126、比例 0.3607，持平數多算 2。它的 breadth.py 把 change_sign 為空的列一律算成持平，推測把未成交或無比價的列算進去了。
6. 官方逐檔行情對到 app 的上市「股票加 ETF」母體（1330 檔）：538／642／137，比例 0.4085。finding 寫 0.4076。
7. 兩者差距：app 口徑 4.70pp，官方口徑 4.71pp，與 finding 說的「約 5 個百分點」相符。
8. 9/23 的日線分區沒有任何上櫃資料，所以這天的 market_totals 只剩上市。「當日上櫃缺日」也獲得確認。

【補充面向（finding 沒提到）】
- 方向不固定：我用目前的證券主檔，算了 60 個交易日（2026-07-01～09-23）「market_totals 減股票」的差距。平均 +0.62pp，平均絕對值 2.08pp，範圍 −3.68pp 到 +5.02pp；絕對值 ≥2pp 的有 29 天，≥4pp 的有 5 天。例如 7/02 是 −3.7pp，8/03 是 −3.5pp。所以 ETF 不只會「拉高」，也會拉低；9/23 只是偏高的一個例子。
- 使用者實際能看到的地方：前端沒有顯示診斷項目的 market_context.advance_ratio（在 frontend/src 搜尋只找到 api.ts 的型別定義 :1391-1402）。這個混用口徑主要出現在兩處：API JSON，以及 AI 研究 payload。AI 那條路徑是 research_context.py:632-635 取 market_totals 的漲跌平家數，在 ai_research.py:141-147 算出 market_context.advance_ratio，和 :162-165 的 industry_context.advance_ratio 一起放進 LLM 輸入，LLM 可能直接拿兩者比較。

【嚴重度】
這屬於母體定義沒有標示、未附說明，不是資料破壞，也不會讓核心流程無法使用；app 另外有 by_instrument.stock 可以改用。維持 P3。
    - 證據：
      - backend/app/taiwan/abnormal_diagnostics.py:275-279 — adv_ratio 由 market_snap.market_totals 的漲跌平家數計算
      - backend/app/taiwan/market_intelligence.py:378 — universe_df.filter(instrument_type.is_in(["stock","etf"]))；:451 totals = self._calc_breadth(joined)；:463-465 另有 stock_breadth／etf_breadth（by_instrument）
      - backend/app/taiwan/industry_intelligence.py:197 — 產業母體只含 instrument_type == "stock"；:383-388 用 change_pct 正負計算 adv_ratio
      - backend/app/taiwan/abnormal_diagnostics.py:144-145, 655-656 — 同一個診斷項目同時帶 market_context 與 industry_context
      - backend/app/taiwan/research_context.py:632-635 + ai_research.py:141-147, 162-165 — AI payload 裡同樣並列含 ETF 的市場上漲比與只含股票的產業上漲比
      - 官方 reference\csv\twse_mi_index_20260923_updown_counts.csv（來源 https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&date=20260923&type=ALLBUT0999）：股票欄 上漲 388(15)、下跌 562(1)、持平 123，算得 0.3616；整體市場欄 5,575／7,862／1,031 另外列示
      - 指令：cd backend && uv run python AUDIT_SCRATCH/work/verify-g1-advratio/repro.py（輸出在同目錄 out.txt）：market_totals adv=538 dec=647 flat=137 ratio=0.407；by_instrument.stock 391/571/124 ratio=0.36；etf 147/76/13 ratio=0.6229；TPEX rows=0；產業合併 391/571/124 ratio=0.36
      - 同一支 repro.py 的官方逐檔行情（twse_mi_index_20260923_quotes.csv，1380 列）對到 app 的 TWSE 母體：股票 1090 檔 391/567/124 ratio=0.3614；股票加 ETF 1330 檔 538/642/137 ratio=0.4085；ETF 147/75/13 ratio=0.6255
      - 指令：uv run python ...\verify-g1-advratio\gap_series.py（輸出 gap_out.txt）：60 個交易日的 totals 減 stock 差距，平均 +0.623pp，平均絕對值 2.082pp，最大 +5.02pp，最小 −3.682pp；|gap|≥2pp 29 天、≥4pp 5 天；例如 2026-07-02 0.5687 vs 0.6056，2026-09-23 0.407 vs 0.36
      - frontend/src 搜尋 market_context.advance_ratio／industry_context.advance_ratio：沒有任何渲染點，只有 frontend/src/lib/api.ts:1391-1402 的型別定義
      - git status 前後一致（只有工作階段開始前既有的 3 個改動），這次驗證沒有寫入 repo

### [P3 → P3] SHORT_MARGIN_RATIO_SPIKE 沒有最低融資餘額門檻，極小基數會產生雜訊（確認成立）

- **主張**：只看券資比絕對上升 5 個百分點；融資餘額只有幾十張時，券資比可以動輒上百 %。
- **正確的定義或數值**：加上最低融資或融券餘額（例如 ≥ 500 張），與其他訊號的絕對量門檻一致。
- **app 實際行為**：沒有絕對量門檻。
- **對使用者的影響**：清單中混入少量極小基數的雜訊。
- **修法建議**：加上 margin_balance ≥ 門檻的條件，並同步更新 docstring 與 signals.ts。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - abnormal_diagnostics.py:527-543
- **線上證據**：
  - MoneyDJ 券資比條目：「券資比每達到一定的比率，放空就會有所收斂」——指標的判讀以具規模的融資融券為前提（該頁未給數值門檻）
- **本地計算證據**：
  - smr.py：33 個交易日 989 筆中，融資餘額 < 100 張的有 43 筆，< 500 張的有 121 筆；例：009811 在 9/22、9/23 融資 21 張、融券 35 張，券資比 166.67%
- **獨立驗證**：
  - **reproduction**：confirmed；修正後嚴重度 P3
    - 修正後敘述：SHORT_MARGIN_RATIO_SPIKE（abnormal_diagnostics.py:527-543）只看券資比比前 20 日中位數高出 ≥5 個百分點，沒有融資或融券餘額的絕對量門檻；其他籌碼訊號都有門檻。最近 33 個交易日（2026-08-10～09-23），股票＋ETF 共觸發 989 筆，其中融資 <100 張 43 筆、<500 張 121 筆。但使用者在 TaiwanScreener 看到的公開清單只含股票：同期 868 筆，<100 張 11 筆（1.3%）、<500 張 82 筆（9.4%）。股票例子有 4442.TPEX 融資 45 張、券資比 155.56%，以及 5209.TPEX 融資 23 張、券資比 108.7%。ETF 例子 009811（融資 21 張、融券 35 張、券資比 166.67%，官方資料相符）不會出現在清單中，只會出現在 AI 研究與比較（include_etfs=True）的個股診斷裡；ETF 的極小基數比例明顯較高（26.4% 融資 <100 張）。建議加上最低融資餘額門檻。
    - 理由：程式碼部分成立：abnormal_diagnostics.py:527-543 的 SHORT_MARGIN_RATIO_SPIKE 只檢查三件事：sm_ratio 不為 null、前 20 日有 ≥15 筆資料、(sm_ratio − 前 20 日中位數) ≥ 5.0。完全沒有融資或融券餘額的絕對量門檻。其他籌碼訊號都有絕對量下限：外資 ≥100,000 股（:447）、投信 ≥50,000 股（:469）、融資增減 ≥500,000 股（:490）、融券增減 ≥100,000 股（:510）。

數據部分我自己寫腳本重現。做法是逐日讀 margin 與 daily 的 parquet，照程式碼的邏輯重算：前 20 個 margin 分區、margin_change 至少 15 筆、中位數為 null 時補 0、delta 取到小數第 2 位、目標日須有 daily 資料。結果與 finding 的數字完全一致：最近 33 個可評估交易日（2026-08-10～09-23）觸發 989 筆，其中融資 <100 張 43 筆、<500 張 121 筆。009811 在 9/22、9/23 的融資 21 張、融券 35 張、券資比 166.67%，與官方 MI_MARGN 參考資料逐筆相符；它在 9/22 的 delta 是 41.67，嚴重度 high。

需要修正的範圍：989／43／121 這組數字把股票與 ETF 合計，009811 本身也是 ETF。使用者實際看到的「清單」來自 TaiwanScreener 呼叫的公開路由 GET /api/taiwan/abnormal-diagnostics，該路由不傳 include_etfs，預設只含股票。只有 AI 研究（ai_research.py:397）與比較（comparison.py:236）會帶 include_etfs=True。只算股票時，同樣 33 天是 868 筆，其中 <100 張 11 筆、<500 張 82 筆，占 1.3% 與 9.4%。ETF 的極小基數比例高得多（121 筆中 <100 張 32 筆，占 26.4%）。

不過股票本身也有同類雜訊，所以「清單混入少量極小基數雜訊」仍然成立。例：4442.TPEX 在 7/31 融資 45 張、融券 70 張，券資比 155.56%；5209.TPEX 在 9/4 融資 23 張，券資比 108.7%，基準中位數 0；3045.TWSE 在 9/11 融資 67 張，券資比 246.27%。9/22 公開清單共 33 筆 SMR 觸發，<500 張的有 2 筆（5209 176 張、6184 489 張）。這兩筆的數值也與官方 TPEx／TWSE 融資融券資料相符。

線上佐證：MoneyDJ 條目只寫「券資比每達到一定的比率，放空就會有所收斂」，沒有給數值門檻，與 finding 的說法相同，但這不能直接證明需要最低門檻。「應加門檻」屬設計判斷，P3 合理。

使用者自選清單方面：00893 的融資曾低到 39 張、券資比最高 58.97%，它只會透過 AI 研究或比較功能受影響。WebSearch 額度已用完，只做了 3 次 WebFetch。
    - 證據：
      - backend/app/taiwan/abnormal_diagnostics.py:527-543：if sm_ratio is not None and m_stat and m_stat["count_margin_20d"] >= 15: ... sm_delta = round(sm_ratio - med_sm20, 2); if sm_delta >= 5.0 → 沒有任何餘額門檻
      - backend/app/taiwan/abnormal_diagnostics.py:447,469,490,510：其他籌碼訊號有絕對量門檻（100000／50000／500000／100000 股）
      - backend/app/taiwan/margin_store.py:131-135：short_margin_ratio = short_balance / margin_balance * 100，只排除 margin_balance ≤ 0
      - backend/app/api/taiwan.py:390-415：公開路由沒有 include_etfs 參數，呼叫 get_diagnostics 時也不傳，所以預設 False（只含股票）；ai_research.py:397、comparison.py:236 才用 include_etfs=True
      - 本地重現（指令：cd backend && PYTHONIOENCODING=utf-8 uv run python ...\scratchpad\audit\work\verify-smrfloor\repro.py，輸出在同目錄 repro.out）：最近 33 個交易日 2026-08-10～09-23，股票＋ETF 觸發 989 筆，融資<100張 43 筆、<500張 121 筆（與 finding 完全一致）；只算股票 868 筆，<100張 11 筆、<500張 82 筆；只算 ETF 121 筆，<100張 32 筆、<500張 39 筆
      - 全期間（104 個可評估日，2026-04-28～09-23）只算股票：觸發 2713 筆；融資 [0,100) 張 37 筆、[100,500) 張 245 筆、[500,1000) 張 207 筆、≥1000 張 2224 筆；券資比 ≥100% 的有 19 筆
      - 009811.TWSE 本地 parquet：9/22 融資 21000 股、融券 35000 股、券資比 166.67，前 20 日中位數 125.0，delta 41.67；9/23 中位數 127.315，delta 39.35。security_master 的 instrument_type 是 etf，全期間共觸發 61 次
      - 官方參考 csv\twse_mi_margn_20260922_20260923.csv：'2026-09-22,009811,統一美國50,...,margin_balance_lots=21,...,short_balance_lots=35'；9/23 同樣是 21／35 → 35/21=166.67%，與本地一致
      - 股票極小基數例（本地重現）：4442.TPEX 2026-07-31 融資 45000、融券 70000，券資比 155.56，基準 0；5209.TPEX 2026-09-04 融資 23000、融券 25000，券資比 108.7，基準 0；3045.TWSE 2026-09-11 融資 67000、融券 165000，券資比 246.27，基準 66.53
      - 官方比對 csv\tpex_margin_balance_20260922_20260923.csv：'2026-09-22,5209,新鼎,...,margin_balance_lots=176,...,short_balance_lots=13'；twse_mi_margn：'2026-09-22,6184,大豐電,...,489,...,30' → 與本地 9/22 觸發列（5209 7.39%、6184 6.13%）相符
      - MoneyDJ 券資比條目 https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=de0413d1-f0a3-45c7-9f63-b955d3e0e9ec ：「券資比每達到一定的比率，放空就會有所收斂，使軋空行情達到瓶頸。」該頁沒有給最低融資餘額門檻
      - frontend/src/lib/signals.ts:290-296：公式寫成「券資比 − 前 20 日券資比中位數 ≥ 5 個百分點」，也沒有提到餘額門檻
      - 自選清單（watch.py／watch.out）：00893.TWSE 期間融資最低 39000 股、券資比最高 58.97%；其餘 8 檔的融資都 ≥492 張
      - git status --short 在稽核前後相同（只有既有的 3 個已修改檔），沒有寫入 repo 或 data

### [P1 → P1] 除權息日以原始前收計算漲跌，產生假 PRICE_MOVE／PRICE_FLOW_DIVERGENCE／RELATIVE_STRENGTH_OUTLIER（部分成立）

- **主張**：漲跌幅與 5 日報酬都用原始收盤價計算（前收取上一分區的 raw close），除權息日沒有改用官方除權息參考價，股利缺口因此被當成價格異動。
- **正確的定義或數值**：除權息（含權值）當日的漲跌幅應以除權息參考價為基準（官方標 X 不比價，或以參考價計算）；5 日報酬應使用還原權息的價格序列。
- **app 實際行為**：以 raw 前收計算，除息缺口直接變成「大幅下跌」、「價跌外資買」背離，以及連續 5 天的「產業相對弱勢」。排序又以 |change_pct| 為第二鍵（:661-668），假訊號因此排在面板前列。
- **對使用者的影響**：自選 2379 瑞昱在 9/8 被列為大幅下跌異常；高殖利率股與 ETF 集中除息的日子（例如 8/18 有 00881、009801、00923 等），異常清單會被假訊號佔滿。AI 研究與個股比較讀到錯的 PRICE_MOVE 證據，會把除息描述成價格異動。
- **修法建議**：除權息日改用 TWT49U／TPEx 除權息表的參考價作為 previous_close，或改用 enriched 的前復權收盤計算 chg_pct 與 5 日報酬；沒有參考價時，該日 PRICE_MOVE／DIVERGENCE 標記為不評估。industry_intelligence 的 return_5d 也要一併處理。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - backend/app/taiwan/abnormal_diagnostics.py:250-252（prev_date 取上一分區）
  - abnormal_diagnostics.py:351-357（chg_pct = close / previous_close − 1，previous_close 為 raw close）
  - abnormal_diagnostics.py:423-441（PRICE_MOVE）、:548-584（PRICE_FLOW_DIVERGENCE）、:590（ret_5d = close_p / c5_base − 1）
  - backend/app/taiwan/daily_store.py:41（price_semantics=PriceSemantics.RAW）
  - backend/app/taiwan/industry_intelligence.py:329-342（產業 5 日報酬同樣用 raw close）
- **線上證據**：
  - 官方 STOCK_DAY notes（reference\raw\twse_stock_day_2379_202609.json）：「符號說明:+/-/X表示漲/跌/不比價」；2379 在 115/09/08 的漲跌價差欄為「X0.00」（除息日不以前收比價）
  - 官方 TWT49U notes（reference\raw\twse_twt49u_20260601_20260923.json）：「權值+息值=除權息前收盤價-除權息參考價」；2379 在 ex_date 2026-09-08：除權息前收盤價 729、除權息參考價 704
  - Wikipedia Ex-dividend date https://en.wikipedia.org/wiki/Ex-dividend_date ：「The opening price on the ex-dividend date, in comparison to the previous closing price, can be expected to decrease by the amount of the dividend」
  - Wikipedia 除權 https://zh.wikipedia.org/wiki/除權 ：「除息參考價÷（1＋配股率）＝（除息前一日收盤價－現金股利）÷（1＋配股率）」
- **本地計算證據**：
  - exdiv.py：TWT49U 在 app 視窗（8/10～9/23）內有 249 個上市除權息事件；當日 app 觸發 PRICE_MOVE 73 筆，其中 60 筆依參考價計算的 |漲跌| < 5%（假訊號），另有 5 筆依參考價 ≥ 5% 卻未觸發
  - 假訊號例：6669 緯穎 9/2 app −66.5%（參考價基準 −0.19%）；6005 群益證 8/11 app −11.2%（參考價基準 +1.6%）；00881 8/18 app −10.3%（參考價基準 −2.1%）
  - exdiv.py：除權息日的 PRICE_FLOW_DIVERGENCE（PRICE_DOWN_FOREIGN_BUY）有 2892（8/11）、2886（8/13）、5534（8/25）、2845（9/2），依參考價計算的跌幅都 < 3%
  - exdiv2.py：RSO 訊號中 122 筆的 5 日視窗含除權息日，以 (前收 / 參考價) 還原後有 83 筆不再達 10 個百分點
  - 自選股：2379.TWSE 9/8 app 顯示 PRICE_MOVE DOWN −5.08%（observed −5.08）；依參考價 704 計算為 692/704 − 1 = −1.70%
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P1
    - 修正後敘述：成立。異常診斷的 change_pct（abnormal_diagnostics.py:351-357）以上一分區的 raw 收盤當前收，5 日報酬（:590）與產業 5 日報酬（industry_intelligence.py:328-343）也都用 raw close；除權息日沒有改用官方參考價，也沒有還原權息。官方在除權息日標「X 不比價」，漲跌停價由參考價推得，所以這是錯誤的金融口徑。實際影響（8/10～9/23 的上市事件）：公開面板預設只含股票，有 50 筆假 PRICE_MOVE，其中包含自選的 2379 在 9/8（raw −5.08%，以參考價計算為 −1.70%），另有 5 筆應觸發卻漏報。ETF 的 10 筆假訊號（例如 00881）只出現在 AI 研究與個股比較，因為公開路由不傳 include_etfs；finding 說面板會被 ETF 假訊號佔滿，是誇大。另外 2892、2886、5534、2845 的「價跌外資買」背離，以參考價計算都不成立。大比例除權會讓 RSO 方向顛倒：6669 在 9/2 被判 −62pp 的 LAGGARD，還原後約 +14pp，應是 LEADER。RSO 122/83 的統計未重算。
    - 理由：我親自讀了程式碼並重跑資料，finding 的核心主張成立。abnormal_diagnostics.py:250-252 把前收取成上一個分區的原始 close，:351-357 用 close / previous_close − 1 算 chg_pct；程式裡沒有任何讀取除權息參考價或調整因子的邏輯。daily_store.py:41 也宣告價格是 PriceSemantics.RAW。PRICE_MOVE（:423）、PRICE_FLOW_DIVERGENCE（:548-584）、RELATIVE_STRENGTH_OUTLIER 的 ret_5d（:590）都直接吃這個 raw 值；industry_intelligence.py:328-343 的 change_pct、return_5d 也用 raw close，排序第二鍵是 |change_pct|（:661-668）。

正確口徑以官方為準。本地的官方參考資料顯示：STOCK_DAY 在除權息日把漲跌標成「X0.00（不比價）」，參考資料裡所有 X 日都落在除權息日。TWT49U 的漲跌停價由參考價推得（2379 為 704×1.1=774.4→774、704×0.9=633.6→634，與官方 774/634 一致；若以前收 729 計算應為 801.9/656.1），可見交易所當日的比價基準是參考價，不是前收。另找的線上來源：Yahoo 說明頁把除息調整定義為乘數 (1 − 股利 / 除息前一日收盤)；Yahoo 奇摩的 2379 股利頁也證實 9/8 除息、現金股利 25 元、除息日昨收 729。

本地重算的數字與 finding 一致：區間內 249 個有資料的上市除權息事件，app 觸發 PRICE_MOVE 73 筆，其中 60 筆改用參考價後未達 5%，另有 5 筆以參考價計算達 5% 卻未觸發。2379 在 9/8 的 app 漲跌為 −5.08%，以參考價計算為 −1.70%。四筆 PRICE_DOWN_FOREIGN_BUY 都滿足外資門檻，但以參考價計算的漲跌只有 −2.82%、−2.34%、+0.23%、+0.15%。

另發現一個新面向：6669 在 9/2 是大比例除權。app 算出的 5 日報酬為 −61.5%，相對產業差 −62pp，判為 LAGGARD；還原權值後約 +14.7%，約 +14pp（產業平均以未還原近似）。也就是 9/2、9/3 兩天 RSO 方向完全相反（還原後本應是 LEADER）。

需要修正的誇大之處：公開路由 api/taiwan.py:409-415 不傳 include_etfs，預設只含股票，所以 60 筆假訊號中的 10 筆 ETF（例如 00881）只會出現在 AI 研究與個股比較，不會出現在異常面板。面板上的假 PRICE_MOVE 是 50 筆股票。RSO 的 122/83 我沒有重算。

使用者情境下會實際發生：自選的 2379 在 9/8 被列為 PRICE_MOVE DOWN。自選的 00878 在 8/18 的 raw 漲跌為 −4.06%，未達門檻，外資倍數 0.74，也沒有觸發背離。

嚴重度：CONTRIBUTING.md:295 把「錯誤金融口徑」明列為 P1，這裡的漲跌口徑與交易所定義不符，所以維持 P1。
    - 證據：
      - backend/app/taiwan/abnormal_diagnostics.py:250-252：prev_date = prior_5_dates[-1]，df_prev_daily 讀上一個分區
      - backend/app/taiwan/abnormal_diagnostics.py:351-357：prev_close_p = p_daily['close']；chg_pct = close_p / prev_close_p − 1（沒有參考價）
      - abnormal_diagnostics.py:423（PRICE_MOVE ≥0.05）、:548-584（DIVERGENCE ≥0.03）、:313-316＋:590（c5_base 取 prior_5_dates[0] 的原始 close，ret_5d = close_p / c5_base − 1）、:661-668（排序第二鍵是 |change_pct|）
      - backend/app/taiwan/daily_store.py:41：price_semantics=PriceSemantics.RAW；industry_intelligence.py:328-343：change_pct 與 return_5d 都用 raw close_prev／close_5d
      - 對照：backend/app/indicators/pipeline.py:748-759：enriched pipeline 在除權息日改用前復權昨收當作交易所參考價，專案其他地方已採用這個口徑
      - backend/app/api/taiwan.py:409-415：公開路由不傳 include_etfs，面板只含股票；ETF 只經 ai_research.py:397-400、comparison.py:236 進入
      - 官方（reference\raw\twse_stock_day_2379_202609.json）：115/09/08 收盤 692.00、漲跌「X0.00」；notes「符號說明:+/-/X表示漲/跌/不比價」。reference 內的 X 日共 4 個（00878 8/18、3545 8/4、2379 9/8、2330 9/16），都是除權息日
      - 官方 TWT49U（reference\raw\twse_twt49u_20260601_20260923.json）：2379 在 115/09/08 的除權息前收盤價 729、參考價 704、漲停 774、跌停 634；704×1.1=774.4→774、704×0.9=633.6→634 相符；729×1.1=801.9、729×0.9=656.1 不符，可見交易所比價基準是參考價
      - 線上：https://help.yahoo.com/kb/SLN28256.html 原文 "Dividend multipliers are calculated based on dividend as a percentage of the price ... (1 - dividend amount / closing price on day before ex-date)"
      - 線上：https://tw.stock.yahoo.com/quote/2379.TW/dividend 顯示 2026/09/08 除息、現金股利 25.00 元、除息日昨收價 729 元，與 TWT49U 一致
      - 線上：https://zh.wikipedia.org/wiki/除息 原文「除息參考價＝除息前一日收盤價－現金股利」
      - 本地（scratchpad\audit\work\verify_g1_exdiv\check.py）：窗口 8/10～9/23 內有 249 個事件具 app close 與前收，app 前收與官方除權息前收盤價 249/249 相同；app PRICE_MOVE 73 筆，以參考價計算未達 5% 的有 60 筆（股票 50、ETF 10），參考價達 5% 但 app 未觸發 5 筆
      - 本地：2379 9/8 app_chg −0.0508、ref_chg −0.0170；6669 9/2 −0.6654 vs −0.0019；6005 8/11 −0.1124 vs +0.0159；00881 8/18 −0.1028 vs −0.0212
      - 本地（check2.py）：2892 8/11 外資淨額 23,747,049、倍數 2.11，app −6.44%、參考價 −2.82%；2886 8/13 倍數 3.23，−5.85% vs −2.34%；5534 8/25 倍數 6.04，−6.11% vs +0.23%；2845 9/2 倍數 5.00，−4.76% vs +0.15%。四筆都滿足 DIVERGENCE 的外資條件，但以參考價計算的價格條件都不成立
      - 本地（check3.py）：6669 在 9/2 的 raw r5 −0.6153、產業平均 +0.0063、差 −0.6216（LAGGARD）；還原權值後 r5 +0.1474、差約 +0.1411（應為 LEADER）；9/3 為 −0.6158 vs +0.1043，方向相反。2379 在任何口徑下都沒有觸發 RSO
      - 自選 00878 8/18：raw −4.06% / 參考價 −1.10%，外資 −4,915,052、倍數 0.74，沒有觸發 PRICE_MOVE 或 DIVERGENCE（check2.py、check4.py）
      - CONTRIBUTING.md:295：P1 = 核心流程不可用、錯誤金融口徑、未來函數、廣泛迴歸；docs/code-review-2026-09-22.md 內沒有任何除權／除息／參考價的記錄（grep 無結果），不是重複回報
  - **reproduction**：partially_confirmed；修正後嚴重度 P1
    - 修正後敘述：異常診斷的漲跌幅用上一分區的 raw 收盤當前收（abnormal_diagnostics.py:251、:357），5 日報酬用 raw 收盤（:590、industry_intelligence.py:334-337）。除權息日沒有改用交易所的比價基準，股利缺口因此被當成價格異動。官方 STOCK_DAY 在除權息日標「X 不比價」。

以 2026-08-10 至 2026-09-23 的上市除權息事件重算，並對照官方 TWT49U（上櫃未量化）：
- 公開的異常清單只含股票。165 件除權息事件中，app 觸發 PRICE_MOVE 63 筆，其中 50 筆以參考價計算的 |漲跌| < 5%（假訊號），例如自選 2379 9/8 為 −5.08%，以參考價計算為 −1.70%；6669 9/2 為 −66.54%，以參考價計算為 −0.19%。
- 另有 4 筆假 PRICE_DOWN_FOREIGN_BUY 背離：2892、2886、5534、2845。
- 122 筆 RSO 的 5 日視窗含除權息日，還原後有 83 筆不再達 10 個百分點。6669 連續 5 天被判為 LAGGARD，差值約 −62 至 −67 個百分點。產業平均本身也受污染。
- ETF 另有 10 筆假 PRICE_MOVE（例如 00881 8/18），但只出現在 AI 研究與個股比較（include_etfs=True），不會出現在公開異常清單。
- 上市除權息假訊號佔全部股票 PRICE_MOVE 的 1.3%，單日最高 7.1%，不會「佔滿清單」。不過除權息缺口超過 10% 的個股，|change_pct| 必然超過當日漲跌停幅度，會排在同訊號數那一層的最前面。

修法應以 TWT49U 的「開盤競價基準」為前收，這欄在含現金增資除權時取減除股利參考價，不能直接用「除權息參考價」；否則 6225 8/18 會被算成 +62.45%，而官方漲停 48.8 的基準是 44.4。依此基準，漏報是 3 筆，不是 5 筆。5 日報酬要改用還原權息序列，產業平均也要一併還原。
    - 理由：我用官方 TWT49U、STOCK_DAY 參考資料和本地 daily／institutional parquet 重算，finding 的核心機制與所有數字都逐一重現。程式碼方面：前收取自 daily store 的上一個分區（abnormal_diagnostics.py:251）；chg_pct = close/prev_close−1（:357）；ret_5d = close_p/c5_base−1（:590）；排序第二鍵是 −abs(change_pct)（:661-668）；daily_store.py:41 為 RAW；industry_intelligence.py:334-337 的 return_5d 也用 raw close。程式裡沒有任何除權息處理，已知問題文件也沒有記錄除權息相關項目。

所有除權息事件中，app 讀到的前收都等於官方「除權息前收盤價」（不符 0 筆），所以差異完全來自沒有改用參考價。

以下三點不成立或需要修正：
(1) 公開的 /abnormal-diagnostics 端點不傳 include_etfs，universe 只有股票（api/taiwan.py:409-415、abnormal_diagnostics.py:240）。ETF 例子（00881、009801、00923）只會出現在 AI 研究與個股比較（ai_research.py:397-401、comparison.py:236），不會出現在異常清單。只看股票時為 63 筆 PRICE_MOVE、50 筆假訊號。
(2)「異常清單會被假訊號佔滿」不成立：窗口內股票 PRICE_MOVE 共 3846 筆，上市除權息造成的假訊號 50 筆（1.3%），單日佔比最高 7.1%（9/23 為 3/42）。上櫃除權息沒有官方參考資料，無法量化。
(3) 修法建議有瑕疵：TWT49U notes 規定「遇含現金增資除權時，則取最接近減除股利參考價之檔位價」作為開盤競價基準。窗口內有 6 件現金增資除權，其開盤競價基準等於前收。finding 列的 5 筆漏報中，4764（8/20）與 4977（9/01）屬於這一類；改以交易所的開盤競價基準計算時只剩 3 筆。直接用「除權息參考價」反而會把 6225 天瀚 8/18 算成 +62.45%，但官方漲停 48.8 = 44.4 × 1.1，基準是 44.4。

嚴重度維持 P1，屬於錯誤的金融口徑：官方 STOCK_DAY 在除權息日標「X 不比價」。自選股 2379 確實在公開清單中被列為 PRICE_MOVE DOWN。6669 在 9/2～9/8 連續 5 天被列為 RSO LAGGARD，差值 −61.6 至 −66.7 個百分點。
    - 證據：
      - 程式碼：backend/app/taiwan/abnormal_diagnostics.py:251 prev_date = prior_5_dates[-1]；:357 chg_pct = close_p/prev_close_p − 1；:419 起為 PRICE_MOVE；:545 起為 PRICE_FLOW_DIVERGENCE；:590 ret_5d = (close_p / c5_base) − 1；:661-668 排序鍵 (−signal_count, −abs(change_pct), …)。daily_store.py:41 price_semantics=PriceSemantics.RAW；industry_intelligence.py:334-337 return_5d = close/close_5d − 1。行號都已實查並與 finding 相符。
      - Universe：abnormal_diagnostics.py:240 只在 include_etfs=True 時加入 ETF；api/taiwan.py:409-415 公開端點不傳 include_etfs（預設 False）；ai_research.py:397-401 與 comparison.py:236 傳入 include_etfs=True。因此 ETF 的假 PRICE_MOVE 只影響 AI 研究與比較，不會出現在異常清單。
      - 官方資料：reference\raw\twse_stock_day_2379_202609.json 的 115/09/08 列為收盤 692.00、漲跌價差 'X0.00'，notes 記載「符號說明:+/-/X表示漲/跌/不比價」。reference\csv\twse_twt49u_20260601_20260923.csv 的 2379 列：ex_date 2026-09-08、除權息前收盤價 729.0、除權息參考價 704.0、息。
      - 重算腳本：AUDIT_SCRATCH/work/verify-g1exdiv/repro.py，執行 `cd backend && uv run python repro.py`。數字並列（finding → 本次重算）：除權息事件 249 → 249（TWT49U 窗口內共 252 筆，另 3 筆為 9941A、01010T、01009T，不在 universe）；除權息日 PRICE_MOVE 73 → 73；參考價基準下 |漲跌| < 5% 60 → 60；參考價基準 ≥ 5% 但 app 未觸發 5 → 5；前收與官方除權息前收盤價不符 0 筆。
      - 只看股票（公開清單）：除權息事件 165、PRICE_MOVE 63、假訊號 50、漏報 5。ETF：事件 84、PRICE_MOVE 10、假訊號 10。窗口內股票 PRICE_MOVE 總數 3846，上市除權息假訊號 50 筆（1.3%），單日佔比最高 9/23 的 3/42 = 7.1%。
      - 範例並列（app 值 vs 參考價基準）：2379 9/8 為 −5.08% vs −1.70%（692/704−1），與 finding 相同；6669 9/2 為 −66.54% vs −0.19%（前收 7800、參考價 2614.99），相同；6005 8/11 為 −11.24% vs +1.59%，相同；00881 8/18 為 −10.28% vs −2.12%，相同（屬 ETF，只出現在 AI 研究與比較）。
      - PRICE_FLOW_DIVERGENCE（PRICE_DOWN_FOREIGN_BUY）重現，app 值 vs 參考價基準：2892 8/11 為 −6.44% vs −2.82%（f_mult 2.11）；2886 8/13 為 −5.85% vs −2.34%（3.23）；5534 8/25 為 −6.11% vs +0.23%（6.04）；2845 9/2 為 −4.76% vs +0.15%（5.00）。與 finding 列的 4 筆完全一致。
      - RSO：窗口內 5 日視窗含除權息日的訊號 122 → 122；只還原個股報酬後不再達 10 個百分點 83 → 83；個股與產業平均都還原時為 84。6669 在 9/2、9/3、9/4、9/7、9/8 連續 5 天被判為 LAGGARD，diff_raw 為 −62.16、−61.58、−64.70、−64.30、−66.74，還原後為 +14.11、+10.43、+5.94、+5.28、−7.26。另有 16 筆非除權息個股的 RSO 在產業平均還原後跌破 10 個百分點，代表產業平均本身也受污染。
      - 基準修正：reference\raw\twse_twt49u_20260601_20260923.json 的 notes 記載「開盤競價基準：當不含現金增資除權時，取最接近除權息參考價之檔位價，遇含現金增資除權時，則取最接近減除股利參考價之檔位價」。basis_alt.py 找出 6 件開盤競價基準與除權息參考價相差超過 1% 的事件（6698、6225、4764、9958、4977、6533，都是「權」），其開盤競價基準都等於前收。6225 8/18：除權息參考價 30.04、開盤競價基準 44.4、漲停 48.8、收盤 48.8。以除權息參考價計算為 +62.45%，以開盤競價基準計算為 +9.91%。改用開盤競價基準後，假訊號仍為 60（股票 50），漏報從 5 降為 3。
      - 線上來源：https://en.wikipedia.org/wiki/Ex-dividend_date 原文「The opening price on the ex-dividend date, in comparison to the previous closing price, can be expected to decrease by the amount of the dividend」，已核實。https://zh.wikipedia.org/wiki/除權 原文「除息參考價÷（1＋配股率）＝（除息前一日收盤價－現金股利）÷（1＋配股率）」，已核實。該頁沒有現金增資除權的公式。Yahoo 歷史頁回 404，抓不到；WebSearch 額度已用完，未做第三方行情比對。
      - 已知問題：docs/code-review-2026-09-22.md 以「除權／除息／復權／還原」搜尋無結果，不屬於已記錄的問題（B0 是另一件已修的產業 5 日對單日口徑問題）。

### [P2 → P2] SHORT_SURGE 的「減少」在選股頁被標成「融券激增」（部分成立）

- **主張**：後端 SHORT_SURGE 有 INCREASE／DECREASE 兩種 subtype，前端標籤卻固定寫「融券激增」，融券大量回補被顯示成相反的意思。
- **正確的定義或數值**：DECREASE 應顯示為「融券大減／回補」。
- **app 實際行為**：一律顯示「融券激增」。
- **對使用者的影響**：使用者會把大量空單回補誤讀成空單大增，正好與籌碼意義相反。
- **修法建議**：依 subtype 顯示「融券激增」或「融券大減（回補）」，並補對應的前端測試。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - frontend/src/pages/TaiwanScreener.tsx:959（{sig.type === 'SHORT_SURGE' && '融券激增'}，不看 subtype）
  - abnormal_diagnostics.py:515（subtype="INCREASE" if s_chg > 0 else "DECREASE"）
  - TaiwanScreener.tsx:958（MARGIN_SURGE 有依 subtype 區分「融資激增／融資大減」，對照可知 SHORT_SURGE 是遺漏）
- **線上證據**：
  - MoneyDJ 軋空 https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=56ac1340-e1a5-4113-8689-e87cdddf152c ：「先借股票放空賣出，打算等股價下跌後再回補獲利。所以他會「先賣再買」」——融券餘額減少即為回補，與「激增」語意相反
- **本地計算證據**：
  - med0.py：視窗內 app 的 SHORT_SURGE 共 1,081 筆，其中 DECREASE 479 筆（44%）
  - 例：2409 友達 9/23 融券 −8,402 張、8473 山林水 9/23 −412 張、4956 光鋐 9/22 −759 張，都會顯示「融券激增」
- **獨立驗證**：
  - **definition**：confirmed；修正後嚴重度 P2
    - 修正後敘述：後端 SHORT_SURGE 依融券餘額增減的正負號分為 INCREASE/DECREASE，但選股頁異常診斷表（TaiwanScreener.tsx:959）不看 subtype，一律顯示「融券激增」。融券餘額大減（融券買進回補或現券償還）因此被標成空單大增，意思正好相反。表格沒有融券變化欄，只有滑鼠停留時的 tooltip 看得到負號。實際資料中每天約有一半的 SHORT_SURGE 是 DECREASE（9/22 有 37 筆中的 20 筆，9/23 有 28 筆中的 15 筆）。DECREASE 應顯示中性的「融券大減」，不要固定寫「回補」，因為減少可能完全來自現券償還，例如 8473 在 9/23。另外應一併檢視 signals.ts:283 的篩選名稱「融券激增」。
    - 理由：核心主張成立。後端 SHORT_SURGE 以 |short_change| 判定，並依正負號設 subtype=INCREASE/DECREASE（abnormal_diagnostics.py:515）。short_change 是「今日融券餘額 − 前日餘額」（enrichment/margin.py:91、189），負值就是融券餘額減少。前端選股頁的標籤不看 subtype，一律顯示「融券激增」（TaiwanScreener.tsx:959）；同一區塊的 MARGIN_SURGE、PRICE_MOVE、外資／投信都有依 subtype 分成兩種文字（955-958），可見這裡是漏掉了。

定義面：官方 MI_MARGN 的融券欄位是「買進、賣出、現券償還、前日餘額、今日餘額」，今日餘額 = 前日 + 賣出 − 買進 − 現券償還。Wikipedia 把買回已放空的證券稱為 covering（回補），所以餘額大減代表空單了結，與「激增」的意思相反。

使用者情境：異常診斷面板在選股頁無條件顯示，範圍是全市場，與自選清單、監控規則數量無關。表格有「融資變化」欄，卻沒有融券變化欄，所以標籤是畫面上唯一看得到的融券方向；只有滑鼠停留時，tooltip 才會露出帶負號的觀測值。本地重現顯示 9/22、9/23 的 SHORT_SURGE 各約一半是 DECREASE，這個錯誤每天都會出現。

嚴重度維持 P2：計算口徑正確，錯的是顯示標籤，屬「明確功能錯誤」，不到 P1 的錯誤金融口徑。

需要修正的細節：finding 建議把標籤改成「融券大減／回補」，但餘額減少不一定來自回補買進。8473 山林水 9/23 減少的 412 張全部是現券償還，融券買進為 0，所以用中性的「融券大減」比較準確。另外，篩選下拉選單與訊號說明裡的名稱也叫「融券激增」（signals.ts:283），但說明文字寫的是「融券增減」，名稱同樣有誤導之嫌，屬次要面向。docs/ 內查無此問題的既有記錄，不算重複回報。
    - 證據：
      - frontend/src/pages/TaiwanScreener.tsx:959 `{sig.type === 'SHORT_SURGE' && '融券激增'}`，不讀 sig.subtype；同檔 955-958 的 PRICE_MOVE、FOREIGN_FLOW_SPIKE、TRUST_FLOW_SPIKE、MARGIN_SURGE 都依 subtype 分兩種文字
      - backend/app/taiwan/abnormal_diagnostics.py:510-515 以 abs(s_chg) 判定觸發，subtype="INCREASE" if s_chg > 0 else "DECREASE"
      - backend/app/taiwan/enrichment/margin.py:91、189 `schange = sbal - sprev`（今日融券餘額 − 前日餘額）
      - frontend/src/pages/TaiwanScreener.tsx:789 異常診斷面板無條件渲染；878-887 表頭有「融資變化」欄，沒有融券變化欄；951 只有 title tooltip 會顯示觀測值
      - frontend/src/lib/signals.ts:282-286 SHORT_SURGE 名稱為「融券激增」，說明文字卻寫「融券增減至少 10 萬股」
      - 官方參考資料 reference/raw/twse_mi_margn_20260923.json，融券欄位為 ['買進','賣出','現券償還','前日餘額','今日餘額']：2409 友達為 10,084 / 1,759 / 77 / 23,553 / 15,151，23553+1759−10084−77=15151，減少 8,402 張；8473 山林水為 0 / 0 / 412 / 528 / 116，減少的 412 張全部是現券償還，不是回補買進
      - https://en.wikipedia.org/wiki/Short_(finance) 原文："The act of buying back the securities that were sold short is called covering the short, covering the position or simply covering."
      - https://zh.wikipedia.org/wiki/賣空 原文：「在证券價格較低時再從市場買回證券還給券商」（回補）
      - 本地重現：cd backend && uv run python scratchpad/audit/work/verify_short_surge/repro.py，只讀 data/taiwan/margin parquet 並套用與 app 相同的門檻。輸出為 target=2026-09-22 SHORT_SURGE=37（INCREASE=17，DECREASE=20）；target=2026-09-23 SHORT_SURGE=28（INCREASE=13，DECREASE=15）。2409.TWSE 9/23 short_change=-8402000，比值 6.01；8473.TWSE 9/23 為 -412000；4956.TWSE 9/22 為 -759000。三者都觸發 DECREASE
      - 自選股回溯（2026-04-07～09-23）：0050、00878、2379、3034 共 14 次 SHORT_SURGE DECREASE，例如 2026-07-15 0050 為 -529000，但面板本身是全市場，不受自選清單限制
      - grep docs/ 內「融券激增|SHORT_SURGE|融券大減」查無結果，不是已記錄的問題；git status 與開始時相同，未修改 repo
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：選股頁異常診斷表（TaiwanScreener.tsx:959）把所有 SHORT_SURGE 一律標成「融券激增」，但後端（abnormal_diagnostics.py:515）在融券餘額大減時會給 subtype=DECREASE，所以大量空單減少會被顯示成相反的「激增」。篩選下拉選單與說明卡的名稱（signals.ts:283）也用「融券激增」統稱雙向訊號。

以選股頁實際使用的個股範圍，2026-08-10～09-23 共觸發 970 筆，其中 DECREASE 419 筆（43.2%）。finding 的 1,081／479 含 111 筆 ETF 訊號，那些只在 include_etfs=True 的比較／AI 研究服務產生，不會出現在選股頁。

實例（官方 MI_MARGN 驗證）：2409 友達 9/23 −8,402 張，預設視圖第 26 名；2351 順德 9/23 −132 張，第 12 名；4956 光鋐 9/22 −759 張，當日第 4 名。三者都顯示為「融券激增」。8473 山林水 9/23 −412 張不在預設前 50 列，只有切到 SHORT_SURGE 篩選時才會顯示為「融券激增」。

修正時，DECREASE 應顯示為「融券大減」（對齊「融資大減」）。不宜一律寫「回補」，因為減少也可能來自現券償還，例如山林水 9/23 的 412 張全是現券償還。
    - 理由：核心缺陷完全重現：後端 SHORT_SURGE 依 short_change 正負給 INCREASE／DECREASE，但選股頁標籤固定寫「融券激增」，沒有看 subtype。MARGIN_SURGE 在前一行有依 subtype 區分，所以這裡確實是遺漏。三個舉例都與官方 MI_MARGN 逐張吻合，本地 parquet 也與官方一致。

數字只部分重現。finding 的 1,081 筆／DECREASE 479 筆（44%）是 2026-08-10～09-23 共 33 個交易日的統計，其中含 111 筆 ETF 訊號（DECREASE 60、INCREASE 51）。但選股頁呼叫的 /api/taiwan/abnormal-diagnostics 路由不傳 include_etfs，預設 False，只算個股，所以這 111 筆永遠不會出現在選股頁。

選股頁實際範圍（只算個股）：同一視窗共 970 筆，DECREASE 419 筆（43.2%），和稽核者的個股子集逐筆比對 970/970 相同。若看本地全部歷史（4/28～9/23）：3,786 筆，DECREASE 1,751 筆（46.3%）。

「都會顯示融券激增」也要修正。選股頁不帶日期，只顯示最新交易日，而且預設只列前 50 列。9/23 預設視圖裡，DECREASE 只有 2 檔進前 50：順德第 12 名、友達第 26 名。8473 山林水排在 50 名外，只有把篩選器切到 SHORT_SURGE 時才看得到（9/23 共 23 檔，全部列出）。9/22 當天是最新交易日時，有 7 檔 DECREASE 進前 50，光鋐第 4 名。

另有兩個 finding 沒提到的面向。
- 篩選下拉選單與說明卡用的名稱（signals.ts:283 name='融券激增'）一樣把雙向訊號叫成「激增」。
- 修正建議的「回補」不能一律套用。山林水 9/23 減少的 412 張全是現券償還（融券買進 0 張），「融券大減」比「回補」準確。

緩解因素：tooltip 的「觀測值」會顯示負數，但表格沒有融券變化欄，一般使用者只看得到標籤。嚴重度維持 P2：明確的功能錯誤，不影響資料正確性。
    - 證據：
      - 程式碼：frontend/src/pages/TaiwanScreener.tsx:959 `{sig.type === 'SHORT_SURGE' && '融券激增'}`，不看 subtype；:958 MARGIN_SURGE 依 subtype 顯示「融資激增／融資大減」
      - 程式碼：backend/app/taiwan/abnormal_diagnostics.py:510-515，abs(s_chg)>=100000 且 ratio>=3.0 時觸發，subtype 為 "INCREASE" if s_chg > 0 else "DECREASE"
      - 程式碼：backend/app/api/taiwan.py:409-415，get_diagnostics 沒有傳 include_etfs（預設 False，abnormal_diagnostics.py:223、239-241 只取 TAIWAN_STOCKS）
      - 程式碼：frontend/src/pages/TaiwanScreener.tsx:286-287 不帶 date；:901 items.slice(0, 50)，只顯示最新交易日的前 50 列
      - 程式碼：frontend/src/lib/signals.ts:282-286，篩選器與說明卡名稱為 '融券激增'，說明文字寫的卻是「融券增減」
      - 官方參考資料 csv/twse_mi_margn_20260922_20260923.csv：2409 友達 9/23 融券前日餘額 23553 → 今日餘額 15151（−8,402 張；融券買進 10084、賣出 1759、現券償還 77）
      - 官方參考資料同檔：8473 山林水 9/23 融券 528 → 116（−412 張；融券買進 0、現券償還 412）
      - 官方參考資料同檔：4956 光鋐 9/22 融券 1205 → 446（−759 張；融券買進 877、賣出 118）
      - 本地重算：uv run python AUDIT_SCRATCH/work/verify-shortsurge/repro.py，直接讀 data/taiwan/margin 與 daily parquet，獨立實作 SHORT_SURGE 規則
      - 重算結果：9/22～9/23 共 52 筆 SHORT_SURGE，其 short_change 與官方 MI_MARGN／TPEx margin 餘額差 ×1000 逐筆比對 52/52 完全相同；其中 DECREASE 25 筆（9/22 14 筆、9/23 11 筆）
      - 數字並列：finding 1,081 筆／DECREASE 479（44%）；我重算同視窗 8/10～9/23（33 日）個股 970 筆／DECREASE 419（43.2%），與稽核者個股子集逐筆 anti-join 差異 0；稽核者多出的 111 筆依 security_master instrument_type 皆為 ETF（DECREASE 60、INCREASE 51）
      - 全部可用歷史（4/28～9/23）個股：SHORT_SURGE 3,786 筆，DECREASE 1,751 筆（46.3%）
      - 排名（出自稽核者以 app 跑出的 G01-gap/app_2026-09-2x_stk.json；其 SHORT_SURGE 集合經我獨立重算驗證，排名本身未獨立重算）：9/23 前 50 列中 DECREASE 2 檔（2351 順德第 12 名 −132,000 股；2409 友達第 26 名 −8,402,000 股），8473 山林水不在前 50；9/22 前 50 列中 DECREASE 7 檔（6168 宏齊第 2、4956 光鋐第 4、8110 華東第 9 等）
      - 線上：MoneyDJ 理財網軋空條目 https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=56ac1340-e1a5-4113-8689-e87cdddf152c 原文「先借股票放空賣出，打算等股價下跌後再回補獲利。所以他會「先賣再買」」：回補（買回）會讓空單減少，與「激增」語意相反
      - 工作樹：執行前後 git status 相同（只有原本就有的 3 個修改檔），沒有寫入 repo

### [P2 → P2] 融資／融券 20 日中位數為 0 時直接不評估，漏掉最極端的激增（部分成立）

- **主張**：MARGIN_SURGE 與 SHORT_SURGE 多了一個文件沒寫的條件 median > 0。平時幾乎沒有變動的標的突然出現大量增減時（比值應為無限大），反而不會觸發。
- **正確的定義或數值**：依文件公式，達絕對門檻且基期中位數為 0 時應視為遠超 3 倍而觸發，或文件明寫排除並說明原因。
- **app 實際行為**：靜默不評估，也不在 evidence 或 unevaluated 中揭露。
- **對使用者的影響**：冷門股、ETF 最突兀的融資融券暴增不會出現在異常清單。
- **修法建議**：中位數為 0 時改用下限（例如 1 張）或直接判定超過倍數；在 docstring 與 signals.ts 同步寫明。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - abnormal_diagnostics.py:490（if med_abs_m20 > 0 and abs(m_chg) >= 500000）
  - abnormal_diagnostics.py:510（if med_abs_s20 > 0 and abs(s_chg) >= 100000）
  - abnormal_diagnostics.py:20-23（docstring 沒有 median > 0 條件）
  - signals.ts:274-288（前端公式與資料條件也沒有）
- **線上證據**：
  - MoneyDJ 券資比 https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=de0413d1-f0a3-45c7-9f63-b955d3e0e9ec ：「可用以研判個股融券是否過高，有無軋空行情」——融券突增是該指標關注的情境
- **本地計算證據**：
  - compare.py／med0.py：獨立實作把 median=0 視為比值無限大，33 個交易日多出 55 筆（MARGIN 8、SHORT 47），app 全部未觸發
  - 例：3717 聯嘉投控融券 9/18 +1,208 張、9/21 +2,448 張、9/22 +1,100 張，連續未觸發；3049 精金融資 8/25 +1,707 張、8/28 +1,865 張，未觸發；00712 9/17 融券 +13,000 張，未觸發
- **獨立驗證**：
  - **definition**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：MARGIN_SURGE 與 SHORT_SURGE 在程式裡多了一個文件和 signals.ts 都沒寫的 median>0 條件（abnormal_diagnostics.py:490、:510），會讓訊號出現非單調行為：同一檔股票最早、最大的那筆暴增因為前 20 日中位數為 0 而靜默不評估，隨後較小的增減在中位數變成 500 到 2000 股時反而觸發（例如 1611、6186、3219）。33 個交易日共 55 筆未評估，其中 ETF 16 筆只影響 include_etfs=True 的比較與 AI 研究路徑，不影響公開的異常篩選。39 筆個股中約 28 筆當天仍會因其他訊號列入清單，缺的只是融資或融券這一條證據；整檔從清單消失的最多 11 筆。修正時要先區分「能做信用交易但很冷門」與「前 20 日根本不能做信用交易」（3 筆，例如 3049 在 8/24 首日出現融資）：前者應觸發或改用備援基期（參考 MAD=0 時改用 MeanAD 的慣例），後者應視為覆蓋不足或另外說明；並同步更新 docstring 與 signals.ts。
    - 理由：核心缺陷成立。現行程式確實多了一個文件沒寫的 median > 0 條件：abnormal_diagnostics.py:490（`if med_abs_m20 > 0 and abs(m_chg) >= 500000`）與 :510（`if med_abs_s20 > 0 and abs(s_chg) >= 100000`）。docstring 第 20–23 行只寫 `abs(chg) / median(abs(previous_20)) >= 3.0` 與 15/20 覆蓋率；signals.ts:274-288 的 formula、dataRequirement 也都沒有這個條件，而 description 寫的是「達前 20 日中位數的 3 倍」，照乘法讀（|chg| ≥ 3×0）這類情況應該觸發。資料模型沒有 unevaluated 欄位，只有 not_applicable_signals（:140、:614-616），所以這類跳過完全不會揭露。git 歷史只在 merge fd79b63 出現，沒有任何說明理由，也沒有 median=0 的測試。測試檔第 37-38 行反而明訂「官方回報的 0 是有效資料、不等於缺值」。

外部權威來源（與 finding 引用的不同）是 IBM 的 modified z-score 定義。以中位數為基準的離散度（MAD）等於 0 是已知的退化情況，標準做法是改用平均絕對偏差（MeanAD）作為分母，並不是跳過不評估。finding 引用的 MoneyDJ 券資比條目只說明融券值得關注，不能拿來佐證公式的定義。

我自己寫腳本重算（直接讀 parquet，33 個交易日，count≥15、達絕對門檻、median=0），結果與 finding 一致：共 55 筆，其中 MARGIN 8 筆、SHORT 47 筆。3717 的數據已與兩個來源交叉比對：官方 MI_MARGN 9/22 的融券餘額由 4175 增加到 5275（+1,100 張），Yahoo 9/18 為 +1,208、9/21 為 +2,448，與 store 逐筆相同。更有力的證據是這個條件造成非單調行為：同一檔股票最早、最大的那筆暴增因為 median=0 不觸發，隨後較小的增減在 median 變成 500–2000 股時反而觸發。例如 1611.TWSE 8/24 +125,000 股不觸發，8/27 −112,000（median 500）觸發；6186.TPEX 8/26 +144,000、8/27 +267,000 不觸發，9/01 +141,000（median 500）觸發；3219.TPEX 8/24、8/27 不觸發，8/31 +130,000（median 1000）觸發。沒有合理設計會漏掉第一筆暴增、只抓後續較小的變動，因此判定為明確的功能錯誤，P2 恰當。

需要修正的部分：
(1) user_impact 說「不會出現在異常清單」是誇大。55 筆中有 16 筆是 ETF，而公開的 /abnormal-diagnostics 與 TaiwanScreener 預設 include_etfs=False（:31-35、:239-241），ETF（包括 00712）只影響比較與 AI 研究路徑。其餘 39 筆個股中，有 28 筆當天仍會因 MARGIN_SURGE、券資比驟升或爆量等其他訊號列入清單（我的近似計算，未含 PRICE_MOVE 與法人訊號），缺的只是融資或融券這一條證據。整檔從清單消失的最多 11 筆，約每 3 個交易日 1 筆，這是上限。finding 舉的 3717 在 9/18、9/21、9/23 都有 MARGIN_SURGE，9/22 有券資比驟升，所以仍在清單上。
(2) 55 筆中有 3 筆（3049 8/24、00953B 8/11、009825 8/13）在前 20 日的餘額始終是 0。Yahoo 也顯示 3049 在 8/24 以前融資與融券餘額全是 0，推測是先前不能做信用交易。這類情況屬於制度狀態改變，不是冷門股突然暴增。finding 建議的「視為無限大」或「下限 1 張」都會把它們判為觸發，需要另外做設計決定。
(3) 使用者的 9 檔自選都沒有出現在 55 筆中，只有全市場異常篩選受影響。
    - 證據：
      - backend/app/taiwan/abnormal_diagnostics.py:488-491：MARGIN_SURGE 有 `if med_abs_m20 > 0 and abs(m_chg) >= 500000`，median 為 0 時直接跳過
      - backend/app/taiwan/abnormal_diagnostics.py:508-511：SHORT_SURGE 有 `if med_abs_s20 > 0 and abs(s_chg) >= 100000`
      - backend/app/taiwan/abnormal_diagnostics.py:20-23：docstring 公式沒有 median>0；:140、:614-616 只有 not_applicable_signals，沒有揭露未評估項目的欄位
      - frontend/src/lib/signals.ts:274-288：description 寫「達前 20 日中位數的 3 倍」，formula 與 dataRequirement 都沒有 median>0
      - backend/tests/test_taiwan_abnormal_diagnostics.py:37-38：「Official Zero vs Missing: foreign_net = 0 counts as valid reported zero」；沒有 margin median=0 的測試
      - backend/app/taiwan/abnormal_diagnostics.py:31-35、239-241：公開路由預設 include_etfs=False，ETF 不在異常篩選範圍
      - 外部來源 https://www.ibm.com/docs/en/cognos-analytics/11.1.0?topic=terms-modified-z-score ：MAD 等於 0 時「Subtract the median from the score and divide by 1.253314*MeanAD」，也就是 median 型的尺度為 0 時改用備援分母，不是跳過
      - 本地重算（scratchpad\audit\work\verify-g1-med0\check.py，uv run python）：最近 33 個交易日 median=0 且達絕對門檻的共 55 筆（MARGIN 8、SHORT 47；增加 39、減少 16），與 finding 一致
      - cover.py：55 筆中個股 39、ETF 16；39 筆個股中 28 筆當日仍有其他訊號（MARGIN_SURGE、SM_RATIO 或 VOLUME_SPIKE 近似），完全沒有其他訊號的最多 11 筆；前 20 日餘額始終為 0 的有 3 筆：3049 2026-08-24 MARGIN、00953B 2026-08-11、009825 2026-08-13
      - series.py 呈現非單調行為：median 在 500 到 2000 股之間時，app 等效邏輯在 33 日內觸發 54 筆 SHORT_SURGE，例如 1611.TWSE 8/27 −112000（median 500）、6186.TPEX 9/01 +141000（median 500）、3219.TPEX 8/31 +130000（median 1000）；同一批股票較早、較大的 median=0 暴增（1611 8/24 +125000、6186 8/27 +267000、3219 8/27 +121000）不觸發
      - 官方參考資料 reference\csv\twse_mi_margn_20260922_20260923.csv：3717 在 2026-09-22 融券前日餘額 4175、今日 5275（+1,100 張）；00712 在 2026-09-22 現券償還 13000、餘額 13001→1，與 store 一致
      - https://tw.stock.yahoo.com/quote/3717.TW/margin ：9/18 融券 +1,208（餘額 1,727）、9/21 +2,448、9/22 +1,100，8/21–9/17 之間多數日子為 0，與 store 逐筆相同
      - https://tw.stock.yahoo.com/quote/3049.TW/margin ：8/28 融資 +1,865、8/31 +649；8/24 以前融資與融券餘額全為 0（推測先前不能做信用交易）
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：MARGIN_SURGE（abnormal_diagnostics.py:490）和 SHORT_SURGE（:510）多了一個文件沒寫的條件：前 20 日中位數大於 0（docstring :20-23 與 signals.ts:274-288 都沒提）。基期中位數為 0 時，即使當日增減已達絕對門檻，也會直接略過不評估，而且 evidence 裡沒有任何揭露。

本地資料重現：8/10～9/23 共 33 個交易日，依文件公式應觸發而 app 沒觸發的有 55 筆（MARGIN 8、SHORT 47），實跑 get_diagnostics 確認 0/55 觸發。例子：3717 融券 9/18 +1,208、9/21 +2,448、9/22 +1,100 張；3049 融資 8/25 +1,707、8/28 +1,865 張；00712 融券 9/17 +13,000 張。以上數字經官方 MI_MARGN 與 HiStock 核實。

影響範圍需要修正：
- 公開的異常診斷路由只含股票，受影響 39 筆；ETF 的 16 筆只影響個股比較與 AI 研究。
- 55 筆中有 37 筆仍會因其他訊號出現在清單，只是少了融資或融券那條證據；完全不出現的只有 18 筆（股票最多 7 筆）。
- 使用者自選 9 檔在 104 個可評估日中都沒有受影響。

修法方面，不宜把中位數為 0 直接視為倍數無限大：55 筆中有 17 筆的當日變動小於前 20 日窗內的最大值（例如 3717 9/23 只有 -172 張），會變成誤報。應改用下限（例如以 1 張，或某個絕對量作為分母下限），並在 docstring 與 signals.ts 同步寫明。另外，3049 的案例是融資餘額從 6 月到 8/21 一直為 0 後才恢復（推測是信用交易資格恢復），要不要當成激增，也應在文件中說明。
    - 理由：finding 的程式碼證據與數據全部重現，一個數字都沒有差。但「使用者影響」寫得太重，修法建議也有副作用，所以判部分成立。

1) 程式碼：abnormal_diagnostics.py:490、:510 確實多了 `med_abs_m20 > 0`、`med_abs_s20 > 0` 兩個條件；docstring（:20-23）和 signals.ts（:274-288）的公式與資料條件都沒寫這一點。回應模型裡沒有 unevaluated 欄位，只有 not_applicable_signals，所以這種略過完全不會揭露。測試檔沒有任何案例涵蓋中位數為 0 的情況；docs/ 裡的既有記錄也沒提過。

2) 數據重現：我自己寫腳本 repro.py，直接用 polars 逐個分區讀 margin parquet（119 個分區，4/07～9/23）。每個目標日取它之前 20 個分區，要求 count>=15 且當日有日K，並限定在 supported 且 active 的 stock/etf。兩套規則對照：app 的規則，以及「中位數為 0 視為倍數無限大」的規則。最近 33 個目標日（8/10～9/23）共差 55 筆：MARGIN 8、SHORT 47，和 finding 完全一致。反方向（app 有觸發、替代規則沒有）0 筆；中位數大於 0 卻不一致的也是 0 筆，差異全部來自中位數為 0。

3) 實跑 app：run_app.py 直接呼叫真正的 get_diagnostics（include_all、include_etfs=True）。三個 store 指向真實資料目錄；security master、市場服務、產業服務用 stub 替換，避免寫檔。55 筆都有 item，觀測到的 margin/short change 值和我的計算相同，但對應的 MARGIN_SURGE/SHORT_SURGE 觸發 0/55。執行前後比對 data/taiwan 的 2844 個路徑、大小與修改時間，完全沒有變化。

4) 例子數值：3717 融券 9/18 +1,208、9/21 +2,448、9/22 +1,100 張；3049 融資 8/25 +1,707、8/28 +1,865 張；00712 融券 9/17 +13,000 張，都和本地 parquet 一致。證交所 MI_MARGN 9/22 原始資料：3717 融券前日餘額 4,175、當日餘額 5,275（+1,100）；00712 前日餘額 13,001、現券償還 13,000、當日餘額 1。HiStock 線上數字也逐日相符。

需要修正的部分：
(a)「不會出現在異常清單」太絕對。55 筆裡有 37 筆因為其他訊號（VOLUME_SPIKE、PRICE_MOVE、FOREIGN_FLOW_SPIKE、SHORT_MARGIN_RATIO_SPIKE 等）仍然會出現，缺的只是融資或融券那一條證據。完全從清單消失的只有 18 筆（股票 7、ETF 11）。我的實跑把 RELATIVE_STRENGTH_OUTLIER 關掉了，所以股票的「消失」筆數是上限。例如 3717 在 9/18、9/21、9/23，3049 在 8/24～8/31 都仍在清單上。
(b) 公開路由 /abnormal-diagnostics 和篩選頁只含股票，受影響的是 39 筆（MARGIN 8、SHORT 31）。ETF 的 16 筆只影響 comparison 和 ai_research（include_etfs=True）。使用者自選的 9 檔，在全部 104 個可評估日中 0 筆受影響。
(c) 修法若照建議把中位數為 0 直接判成超過倍數，55 筆裡有 17 筆的當日變動比前 20 日窗內的最大值還小，照樣會被觸發。例如 3717 9/23 只有 -172 張（前幾天是 +2,448 張），3049 9/02 是 -1,083 張（窗內有 +1,865 張）。這會變成誤報，改用下限（例如 1 張或某個絕對值）比較合理。
(d) 3049 在 6 月～8/21 融資餘額一直是 0，但每天都有成交，8/24 起恢復融資。所以那 6 筆比較像信用交易資格恢復（推測），不是一般的激增；算不算異常有解讀空間。
嚴重度維持 P2：實作與文件公式不一致，屬於明確的功能錯誤；實際影響有限，不到 P1。
    - 證據：
      - backend/app/taiwan/abnormal_diagnostics.py:490 `if med_abs_m20 > 0 and abs(m_chg) >= 500000:`；:510 `if med_abs_s20 > 0 and abs(s_chg) >= 100000:`；:20-23 docstring 沒有中位數大於 0 的條件；:95 附近的模型只有 not_applicable_signals，沒有 unevaluated 欄位
      - frontend/src/lib/signals.ts:274-288：formula 與 dataRequirement 只寫「前 20 日至少 15 日有融資融券資料」，沒有中位數大於 0 的說明
      - 腳本 AUDIT_SCRATCH/work/verify-med0/repro.py 的輸出：可評估目標日 104 個（4/28～9/23），最後 33 個是 8/10～9/23；這 33 日共 55 筆＝MARGIN stock 8 + SHORT stock 31 + SHORT etf 16；app 觸發而替代規則沒觸發的 0 筆；中位數大於 0 仍不一致的 0 筆；全部 104 日共 173 筆
      - 腳本 run_app.py 實際呼叫 TaiwanAbnormalDiagnosticsService.get_diagnostics（真實 store，其餘服務用 stub）：rows 55、fired 0、missing items 0；data/taiwan 執行前後 2844 個路徑的清單 diff 顯示 DATA UNCHANGED
      - 腳本 detail.py 的輸出（diff_last33_detail.csv）：3717 SHORT 9/18 +1208、9/21 +2448、9/22 +1100、9/23 -172 張（med=0、cnt=20）；3049 MARGIN 8/24 +669、8/25 +1707、8/26 +724、8/28 +1865、8/31 +649、9/02 -1083 張；00712 SHORT 9/17 +13000、9/22 -13000 張；有 17 筆當日變動小於前 20 日窗內最大值；3 筆的前 20 日全為 0
      - 有其他訊號的筆數（由 app_run_result.csv 統計）：stock 有其他訊號 32、完全沒有 7；etf 有其他訊號 5、完全沒有 11。例：3717 9/18 另有 VOLUME_SPIKE,TURNOVER_SPIKE,PRICE_MOVE,FOREIGN_FLOW_SPIKE,MARGIN_SURGE,SHORT_MARGIN_RATIO_SPIKE
      - 官方參考資料 csv/twse_mi_margn_20260922_20260923.csv：`2026-09-22,3717,聯嘉投控,...,short_prev_balance 4175, short_balance 5275`；`2026-09-23,3717,...,5275,5103`；`2026-09-22,00712,...,short_stock_repay 13000, prev 13001, balance 1`；`2026-09-22,3049,...,margin_prev 4226, margin_balance 5047`，都和本地 parquet 一致
      - HiStock https://histock.tw/stock/chips.aspx?no=3717&m=mg ：09/18 融券 +1,208（餘額 1,727）、09/21 +2,448（4,175）、09/22 +1,100（5,275）、09/23 -172（5,103）
      - HiStock https://histock.tw/stock/chips.aspx?no=00712&m=mg ：09/17 融券 +13,000（餘額 13,000）、09/22 -13,000（餘額 1）
      - HiStock https://histock.tw/stock/chips.aspx?no=3049&m=mg ：08/28 融資 +1,865（餘額 5,195）、08/31 +649、09/02 -1,083；08/14～08/19 融資為 0
      - 本地 3049 的 margin_balance 從 2026-06-01 到 07-22 全是 0，但 daily 在 4～9 月每天都有成交（例：8/21 volume 9,199,081、8/24 10,131,116）
      - 使用者自選 9 檔在全部 104 個目標日的差異清單中 0 筆；最近 33 日涉及 26 個不同標的；tests/test_taiwan_abnormal_diagnostics.py 沒有中位數為 0 的測試；docs/ 沒有既有記錄

### [P3 → P2] 前端標籤與定義名稱不一致，「價量背離」誤用台灣慣用語；TURNOVER_SPIKE 命名易與週轉率混淆（部分成立）

- **主張**：表格標籤與 ABNORMAL_SIGNAL_DEFINITIONS 的名稱各寫一套，其中「價量背離」在台灣指股價與成交量背離，本訊號比的卻是股價與外資買賣超。
- **正確的定義或數值**：表格標籤直接取用 ABNORMAL_SIGNAL_LABELS（依 subtype 加方向）；背離訊號避免使用「價量背離」。
- **app 實際行為**：寫死在 JSX，與訊號庫脫鉤。
- **對使用者的影響**：「價量背離」會被解讀成量能背離；名稱不一致增加理解成本。
- **修法建議**：表格標籤改由 ABNORMAL_SIGNAL_DEFINITIONS 產生（與下拉選單同源），背離訊號改稱「價格與外資背離」。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - TaiwanScreener.tsx:954（'成交額倍增'）vs signals.ts:244（'成交額放大'）
  - TaiwanScreener.tsx:961（'價量背離'）vs signals.ts:299（'價量與法人背離'）
  - TaiwanScreener.tsx:962（'產業強弱極端'）vs signals.ts:309（'產業相對強弱異常'）
  - signals.ts:235-248、TaiwanScreener.tsx:873（說明卡顯示 id TURNOVER_SPIKE，實際比的是成交金額）
- **線上證據**：
  - MoneyDJ 逆時鐘曲線 https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=256d30bd-8d22-4ce2-925b-bc01f40c17ed ：「當趨勢即將反轉前，通常可以觀察到價跌但量增或價漲但量減的價量背離現象」
- **本地計算證據**：
  - 同一訊號在篩選下拉選單、說明卡與表格標籤上使用三種不同名稱
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P2
    - 修正後敘述：異動診斷表格的標籤寫死在 TaiwanScreener.tsx:953-962，沒有用 signals.ts 的 ABNORMAL_SIGNAL_DEFINITIONS。ABNORMAL_SIGNAL_LABELS 雖然有匯出，但沒有任何地方使用。

這造成兩類問題：
(1) 命名不一致（P3）。每個訊號只有兩套名稱：下拉選單與說明卡共用的 sig.name，以及寫死的表格標籤，不是三套。有三個不一致：TURNOVER_SPIKE「成交額倍增」對「成交額放大」、PRICE_FLOW_DIVERGENCE「價量背離」對「價量與法人背離」、RELATIVE_STRENGTH_OUTLIER「產業強弱極端」對「產業相對強弱異常」。其中「價量背離」在台灣通指股價與成交量背離（MoneyDJ），本訊號比的卻是漲跌幅與外資反向買賣超，容易誤導。TURNOVER_SPIKE 的 id 和專案內 turnover＝成交金額的慣例一致，而且畫面上的中文名稱寫的是成交額，「易與週轉率混淆」查無佐證，不列為問題。
(2) 方向顯示錯誤（P2，原 finding 未提）。SHORT_SURGE 由後端依 |融券增減| 雙向觸發，會回傳 subtype INCREASE 或 DECREASE（abnormal_diagnostics.py:510-515），但表格一律顯示「融券激增」（TaiwanScreener.tsx:959），融券大幅減少也會被標成激增。本地重跑最近 60 個交易日，2,089 次觸發中有 949 次（約 45%）是減少。例如 2026-09-23 友達的融券餘額 23,553 → 15,151 張（官方 MI_MARGN），自選股 3034 在 2026-07-03 也有一例。此外，定義名稱「融資激增／融券激增」本身也是單向措辭，和雙向觸發的邏輯不符。

修正建議：表格標籤改從訊號庫產生，並依 subtype 標示方向，至少 SHORT_SURGE 要區分「融券激增」與「融券大減」；背離訊號改稱「價格與外資背離」。
    - 理由：這條 finding 講的是命名，沒有數值可以重現。所以「數據重現」的做法改成三件事：寫腳本逐一比對兩個檔案的標籤；用本地 margin parquet 重跑後端的觸發條件；再拿官方 MI_MARGN 參考資料核對其中一筆實例。

已確認的部分：
(1) 有三個訊號的表格標籤和定義名稱不一樣：TURNOVER_SPIKE 是「成交額倍增」對「成交額放大」，PRICE_FLOW_DIVERGENCE 是「價量背離」對「價量與法人背離」，RELATIVE_STRENGTH_OUTLIER 是「產業強弱極端」對「產業相對強弱異常」。行號 954/961/962 對 244/299/309 全部正確。
(2) 「價量背離」在台灣確實指股價與成交量的背離。MoneyDJ 原文：「價跌但量增或價漲但量減的價量背離現象」。本訊號比的是漲跌幅與外資反向買賣超（abnormal_diagnostics.py:548-584），用這個標籤會誤導讀者。
(3) ABNORMAL_SIGNAL_LABELS 全專案沒有任何地方使用，只有 signals.ts:317 的定義本身。表格標籤確實是寫死在 JSX，和訊號庫脫鉤。

不成立或不準確的部分：
(a)「下拉選單、說明卡與表格標籤三種不同名稱」有誤。下拉選單（TaiwanScreener.tsx:809-810）和說明卡（834、856；CustomSignals.tsx:196）都用 sig.name，同源。實際只有兩套名稱：定義名稱與寫死的表格標籤。
(b) 引用的 TaiwanScreener.tsx:873 是空行。訊號 id 實際顯示在 835、859 與 CustomSignals.tsx:197，都是灰色小字等寬字。
(c)「TURNOVER_SPIKE 易與週轉率混淆」證據薄弱。本專案內 `turnover` 一律指成交金額，例如 TaiwanScreener.tsx:665 的 option value="turnover" 標示為「成交金額」；換手率則一律用 `turnover_rate`（signals.ts:190）。使用者看到的中文名稱與說明也都寫「成交額／成交金額」。線上查證英文 turnover 的口徑時，Investopedia 抓不到，Wikipedia 回 404 或只有消歧義頁，WebSearch 額度也已用完，所以這點只能算推測。

finding 沒提到、但同一根因造成的較嚴重問題：
SHORT_SURGE 由後端依絕對值觸發，subtype 為 INCREASE 或 DECREASE（abnormal_diagnostics.py:510-515）。但表格標籤不論 subtype，一律顯示「融券激增」（TaiwanScreener.tsx:959），所以融券大幅回補也會標成「融券激增」，方向完全相反。重跑本地資料：最近 60 個交易日（至 2026-09-23）的股票池共觸發 2,089 次，其中 949 次是減少，約 45%；單看 2026-09-23 是 28 次中 15 次。實例是友達 2409：官方 MI_MARGN 融券餘額 23,553 → 15,151 張（−8,402 張），和本地 short_change −8,402,000 股完全一致，但會被標成「融券激增」。自選股 3034 在 2026-07-03 也有一次同樣的情形（short_change −154,000 股）。另外，下拉選單與說明卡的定義名稱「融資激增／融券激增」也是單向措辭，但這兩個訊號其實是雙向觸發（說明文字寫的是「增減」）。

嚴重度判定：純命名部分維持 P3。SHORT_SURGE 減少被標成激增屬於明確的功能錯誤，整體改判 P2。
    - 證據：
      - frontend/src/pages/TaiwanScreener.tsx:954 '成交額倍增' vs frontend/src/lib/signals.ts:244 '成交額放大'（成立）
      - TaiwanScreener.tsx:961 '價量背離' vs signals.ts:299 '價量與法人背離'；TaiwanScreener.tsx:962 '產業強弱極端' vs signals.ts:309 '產業相對強弱異常'（成立）
      - 腳本 AUDIT_SCRATCH/work/verify-g1labels/repro.py，輸出存於 repro_out.txt：10 個訊號中標籤不一致 same=False 的有 TURNOVER_SPIKE、PRICE_MOVE、FOREIGN_FLOW_SPIKE、TRUST_FLOW_SPIKE、PRICE_FLOW_DIVERGENCE、RELATIVE_STRENGTH_OUTLIER；其中 PRICE_MOVE、FLOW 兩項只是加上方向，不算錯誤
      - ABNORMAL_SIGNAL_LABELS 的引用檔案只有 signals.ts 本身（repro.py 輸出：files referencing ABNORMAL_SIGNAL_LABELS: [signals.ts]），也就是沒有任何消費端
      - 下拉選單 TaiwanScreener.tsx:809-810 {sig.name} 與說明卡 TaiwanScreener.tsx:834/856、CustomSignals.tsx:196 {sig.name} 同源，所以只有兩套名稱，不是 finding 說的三種
      - TaiwanScreener.tsx:873 是空行；id 的實際顯示位置在 835、859 與 frontend/src/pages/settings/CustomSignals.tsx:197
      - 專案內 turnover 指成交金額：TaiwanScreener.tsx:554 '全市場成交額'、:665 \<option value="turnover">成交金額；換手率一律用 turnover_rate（signals.ts:190）
      - MoneyDJ https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=256d30bd-8d22-4ce2-925b-bc01f40c17ed 原文：「當趨勢即將反轉前，通常可以觀察到價跌但量增或價漲但量減的價量背離現象」（WebFetch 實抓確認）
      - backend/app/taiwan/abnormal_diagnostics.py:510-515：SHORT_SURGE 以 abs(s_chg) 觸發，subtype 為 "INCREASE" if s_chg > 0 else "DECREASE"；TaiwanScreener.tsx:959 則不看 subtype，一律顯示 '融券激增'
      - 重跑結果（repro.py，股票池 1986 檔，前 20 日中位數、至少 15 日、|chg| ≥ 100,000 股、倍數 ≥ 3.0）：60 個交易日合計 short_inc=1140、short_dec=949；2026-09-23 當天 inc=13、dec=15
      - 官方 twse_mi_margn_20260922_20260923.csv 2026-09-23 友達 2409：融券前日餘額 23,553 張 → 今日 15,151 張（−8,402 張），與本地 short_change=-8,402,000 股一致，前 20 日中位數 1.398e6；此筆會被標成「融券激增」
      - 自選股重跑（watch.py）：3034.TWSE 在 2026-07-03 的 short_change=-154,000 股（前 20 日中位數 2,000）觸發 SHORT_SURGE DECREASE，表格顯示「融券激增」
      - Investopedia turnover 頁抓取失敗（unable to fetch）；Wikipedia Turnover_(stock)、Share_turnover 回 404；WebSearch 額度已用完：「TURNOVER_SPIKE 易與週轉率混淆」查無線上佐證

### [P3 → P3] 門檻比較前先四捨五入，使略低於門檻的值被判定觸發（部分成立）

- **主張**：各比值與差值先 round 到 2 位（RSO 與漲跌幅分別為 4 位、6 位），再與門檻比較，使 [1.995, 2.0)、[2.995, 3.0)、[4.995, 5.0) 等區間被當成達標。
- **正確的定義或數值**：以未四捨五入的原值比較門檻，四捨五入只用於顯示。
- **app 實際行為**：約 0.3% 的觸發屬邊界誤判，而且 evidence 的 ratio 顯示 2.0，看起來剛好達標。
- **對使用者的影響**：影響小，但與文件寫的「≥ 2.0」不一致。
- **修法建議**：門檻判斷改用原值，evidence 另存顯示用的四捨五入值。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - abnormal_diagnostics.py:380-381、:401-402、:448-449、:470-471、:491-492、:511-512、:530-531（先 round 再比較）
  - abnormal_diagnostics.py:357（chg_pct round 6 位）、:594-595（diff_5d round 4 位）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - compare2.py：33 個交易日 app 比獨立實作多出的 92 筆，全部是未四捨五入值略低於門檻，例如 VOLUME 1.9953、TURNOVER 1.9951、FOREIGN 2.9953、SMR 4.995、RSO −0.09995
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：VOLUME、TURNOVER、FOREIGN、TRUST、MARGIN、SHORT 的比值，以及 SMR 差值，都先 round 到 2 位，RSO 差值先 round 到 4 位（產業均值另已 round 6 位），之後才與門檻比較。因此 [1.995, 2.0)、[2.995, 3.0)、[4.995, 5.0)、[0.09995, 0.10) 的值會被判為觸發，evidence 的 ratio 顯示成剛好 2.0 或 3.0，與文件寫的「≥ 2.0」不一致。

實測結果：股票加 ETF 在 2026-08-10～2026-09-23 的 33 個交易日共 92 筆差異（只算股票的公開路由是 83 筆），約占觸發數的 0.3%。其中 91 筆是真的略低於門檻；另 1 筆是 PRICE_MOVE，屬浮點雜訊（9934.TWSE 2026-08-11，實際漲跌剛好 5.00%）。

漲跌幅 round 6 位並不造成誤判。119 天內 PRICE_MOVE 的 24 筆差異全部是剛好 ±5% 的漲跌被浮點誤差算低約 1e-16，round 6 位反而修正了誤差。所以修法不可以直接改用原值比較，否則剛好 5% 的漲跌會被漏判；應改用 Decimal 或整數價位比較，或加上容許誤差（例如 1e-9）。

另外，severity 分級同樣用四捨五入後的值判斷（extreme、high 的邊界），股票 119 天內有 107 筆被升級一級。
    - 理由：程式碼層面確認：各比值與差值都是先 round，再拿去和門檻比較，finding 列的行號全部正確。我用 polars 直接讀 parquet，重寫了一份獨立實作，逐日比較「app 的先 round 再比」與「用原值比」，範圍是 119 個交易日（2026-04-07～2026-09-23）。

finding 的「33 個交易日多出 92 筆」可以精確重現，前提是 universe 為股票加 ETF（include_etfs=True，比較與 AI 研究走這條路徑）、視窗為 2026-08-10～2026-09-23。公開路由只算股票，同一視窗是 83 筆。

但 finding 有三處不準或漏掉：
1. 「漲跌幅 round 6 位」並不會造成誤觸發。119 天內 PRICE_MOVE 的差異共 24 筆（股票 23 筆），全部是十進位下剛好 ±5.00% 的漲跌，只是浮點運算讓結果低了約 1e-16，例如 0.04999999999999982。round 6 位反而修正了這個浮點誤差。落在 [0.0499995, 0.05) 的真實案例是 0 筆。所以 92 筆裡有 1 筆（9934.TWSE，2026-08-11）不是「略低於門檻」，而是剛好等於門檻。「全部是未四捨五入值略低於門檻」這句不成立，只有 91/92 符合。
2. 修法建議如果照字面改成原值比較，會讓剛好 5% 的漲跌被漏判，形成回歸。需要加 epsilon，或改用 Decimal 或整數價位比較。
3. 漏掉的新面向：severity 分級也用四捨五入後的值判斷。股票 119 天內有 107 筆被升級，例如 TURNOVER 4.99977 判成 extreme、FOREIGN 5.99999 判成 extreme、RSO 0.14996 判成 high。

其餘比值型訊號（VOLUME、TURNOVER、FOREIGN、TRUST、MARGIN、SMR、RSO、PFD）的差異都是真的略低於門檻：與門檻的差距介於 1.7e-6 和 0.005 之間，符合 finding 描述的 [x.995, x) 區間。

比例方面：股票 119 天是 351/123540＝0.28%（排除浮點雜訊後 328 筆，0.27%）；股票加 ETF 在最近 33 天是 92/30470＝0.30%。與 finding 的「約 0.3%」相符。

使用者自選 9 檔只受影響 1 筆：00893.TWSE 在 2026-07-29 的 TURNOVER_SPIKE，原值 1.99809，app 顯示 2.0。官方參考資料只涵蓋 2026-08 與 2026-09，無法回溯這一筆的前 5 日，所以沒有做官方比對。這個問題屬於比較邏輯，與資料值是否正確無關。嚴重度維持 P3。
    - 證據：
      - backend/app/taiwan/abnormal_diagnostics.py:380-381、:401-402、:448-449、:470-471、:491-492、:511-512、:530-531：先 round(…, 2) 再與門檻比較（已逐行核對）
      - backend/app/taiwan/abnormal_diagnostics.py:357：chg_pct = round(close/prev - 1, 6)；:594-595：diff_5d = round(…, 4) 後判斷 abs >= 0.10
      - severity 也用四捨五入後的值（finding 沒提）：abnormal_diagnostics.py:383-384、:404-405、:450、:472、:496、:516、:534、:599
      - RSO 還有一層捨入：backend/app/taiwan/industry_intelligence.py:410，avg_ret_5d = round(ind_5d_mean, 6)
      - 文件寫明 ≥ 2.0：abnormal_diagnostics.py:13；frontend/src/lib/signals.ts:239 寫「成交量 ÷ 前 5 日平均成交量 ≥ 2.0」
      - 重現腳本：AUDIT_SCRATCH/work/verify-g1round/repro.py（執行：cd backend && uv run python repro.py [--etf]）
      - 股票 universe（1986 檔），119 天：app 共 123540 筆、原值比較 123189 筆，差 351 筆。各訊號：VOLUME 118、TURNOVER 142、PRICE_MOVE 23、RSO 23、FOREIGN 28、TRUST 6、MARGIN 5、PFD 4、SMR 2；另有 severity 差異 107 筆（summary_stock.json）
      - 股票加 ETF（2377 檔），最近 33 天（2026-08-10～2026-09-23）：app 30470 筆、原值 30379 筆，差 92 筆，與 finding 的 92 一致。其中 TURNOVER 44、VOLUME 34、RSO 4、FOREIGN 4、MARGIN 2、SMR 1、TRUST 1、PFD 1、PRICE_MOVE 1（summary_etf.json、diffs_etf.json）
      - 同一視窗只算股票（公開路由）：83 筆，排除浮點雜訊後 82 筆
      - finding 的範例可以對應到實際案例：VOLUME 4554.TPEX 2026-07-29 原值 1.995329 → 2.0；TURNOVER 5608.TWSE 2026-09-03 原值 1.995111 → 2.0；FOREIGN 4746.TWSE 2026-09-02 原值 2.995343 → 3.0；SMR 1312.TWSE 2026-07-01 差值 4.995 → 5.0（Python round(4.995,2)=5.0）；RSO 最小原值 0.09995075 → 0.1
      - 浮點雜訊的反例：PRICE_MOVE 差異 24 筆全部是十進位下剛好 ±5.00%，用 Decimal(close)/Decimal(prev)-1 驗證股票 23/23 筆為 exact 0.05；與門檻的差距最大只有 1.8e-16，例如 3209.TWSE 2026-04-08 原值 0.04999999999999982。PRICE_MOVE 的 severity 差異 5 筆也是剛好 7%/9.5%，例如 3019.TWSE 2026-06-26 原值 0.06999999999999995
      - 扣除 PRICE_MOVE 與 PFD 後，真實差異與門檻的距離介於 1.70e-6 和 0.005 之間，全部落在 [門檻−0.005, 門檻)
      - 自選股受影響：只有 00893.TWSE 在 2026-07-29 的 TURNOVER_SPIKE，原值 1.99809，app 顯示 2.0；severity 差異 0 筆
      - 官方參考資料只涵蓋 2026-08 至 2026-09，不含 2026-07-22～07-29，無法對 00893 這一筆做官方比對；本 finding 屬比較邏輯問題，與資料值無關
      - git status 在稽核前後相同，repo 未被修改

### [P3 → P3] RELATIVE_STRENGTH_OUTLIER 的產業等權報酬包含個股自身，小產業的離群值被稀釋（部分成立）

- **主張**：average_return_5d 是含自身的產業平均；成員少的產業（農業科技 4 檔、玻璃陶瓷 5 檔）中，個股偏離會被自身拉回。
- **正確的定義或數值**：離群判定通常以排除自身的同儕平均為基準（設計選擇，文件應寫明）。
- **app 實際行為**：含自身，文件沒有說明。
- **對使用者的影響**：小產業的強勢或弱勢股可能漏報。
- **修法建議**：改用 (產業總和 − 自身) / (n − 1)，或在 signals.ts 的 dataRequirement 寫明含自身。
- **稽核者信心**：confirmed
- **程式碼證據**：
  - industry_intelligence.py:406-410
  - abnormal_diagnostics.py:589-595
- **線上證據**：
  - （無）
- **本地計算證據**：
  - rso_self.py：33 個交易日含自身時觸發 3,707 筆；改用 leave-one-out 會再多 108 筆，且原有觸發 0 筆消失
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：RELATIVE_STRENGTH_OUTLIER 比較的產業等權 5 日報酬包含個股自身（industry_intelligence.py:406-410；abnormal_diagnostics.py:589-595）。排除自身時的差距等於含自身差距乘以 n/(n−1)，所以含自身會系統性縮小差距：成分股越少縮得越多（n=4 時縮小 25%），但不會讓任何原本不該觸發的個股觸發。2026-08-10 到 2026-09-23 共 33 個交易日，依 App 實際四捨五入邏輯重算，含自身觸發 3,711 筆；改成排除自身會多 109 筆、原有觸發消失 0 筆。finding 的 3,707 與 108 漏了第 594 行 round(diff, 4)，要改正。多出的 109 筆中只有 8 筆來自可比成分股數 ≤10 的產業，農業科技與玻璃陶瓷各 2 筆；其餘 101 筆是大產業在 10% 門檻附近的邊界案例。小產業受影響的比例較高（19 筆增加到 27 筆），但絕對數量很小。使用者自選的 5 檔個股都在大產業，33 天內沒有受影響；ETF 本來就不評估這個訊號。這屬於設計選擇加上文件沒有說明（signals.ts 的 dataRequirement 沒寫含自身），嚴重度 P3。
    - 理由：主張的實質內容成立，但筆數與實際 App 差幾筆，影響也不像 finding 說的集中在小產業。

1. 產業平均確實包含個股自身。industry_intelligence.py 用產業內所有有 5 日報酬的成分股取平均，這些成分股包含被判定的個股本身；abnormal_diagnostics.py 直接拿這個平均相減，然後對 |diff| ≥ 0.10 做判斷。前端 signals.ts 的 dataRequirement 只寫 ETF 不適用，沒寫平均含自身。

2. 數學上，排除自身時的差距 = 含自身的差距 × n/(n−1)。正負號不變，絕對值只會變大，所以改用排除自身的平均不可能讓原有的觸發消失，「消失 0 筆」必然成立。成分股越少，放大倍數越大：n=4 時是 1.33 倍，n=200 時約 1.005 倍，所以「小產業被稀釋得最多」在比例上成立。

3. 數據重現：自寫腳本，視窗是 2026-08-10 到 2026-09-23 共 33 個交易日，讀本地 parquet，宇宙與產業對應照 App 邏輯處理。
   - 不做四捨五入時，得到含自身 3,707 筆、排除自身多 108 筆、消失 0 筆，和 finding 完全一致。
   - 但 App 在 abnormal_diagnostics.py:594 會先把 diff 四捨五入到小數 4 位再比門檻，加入這一步之後是含自身 3,711 筆、多 109 筆、消失 0 筆。
   - 用 App 實際執行 get_diagnostics 的輸出（G01-gap/app_\<d>_stk.json，稽核 agent 執行的）交叉比對：四個日期（9/16、9/17、9/22、9/23）含四捨五入版的觸發集合與 App 完全相同，分別是 32、100、128、70 筆，沒有任何只出現在一邊的個股。所以正確數字是 3,711 與 109；finding 的 3,707 與 108 漏了這一步四捨五入。

4. 影響分布：多出的 109 筆裡，只有 8 筆來自可比成分股數 n≤10 的產業，其中 finding 點名的兩個產業各 2 筆：農業科技 6578，n 為 3 到 4；玻璃陶瓷 1809，n=5。其餘 101 筆來自較大的產業，屬於門檻邊界效應，例如半導體 3 筆、n 約 205。依 n 分組看：n≤10 觸發 19 筆、多 8 筆，相對增加約 42%；n 在 11 到 50 觸發 697 筆、多 55 筆；n>50 觸發 2,995 筆、多 46 筆。「小產業漏報」比例上成立，但絕對數量很小。

5. 使用者自選股：109 筆多出的觸發裡沒有 2454、3034、2379、6196、3545。四檔 ETF 本來就不評估這個訊號（abnormal_diagnostics.py:615-616），所以對使用者目前的使用情境沒有實際影響。

6. 「離群判定通常以排除自身的同儕平均為基準」這句沒有線上證據。本次 WebSearch 額度已用完，查不到，只能標為推測，應視為設計選擇。嚴重度維持 P3。
    - 證據：
      - backend/app/taiwan/industry_intelligence.py:406-410：comp_5d = ind_df.filter(return_5d not null)；ind_5d_mean = comp_5d['return_5d'].mean()，平均的對象包含每一檔成分股本身
      - backend/app/taiwan/abnormal_diagnostics.py:589-595：diff_5d = round(ret_5d - ind_m.average_return_5d, 4)；if abs(diff_5d) >= 0.10 就觸發（先四捨五入到 4 位才比門檻）
      - frontend/src/lib/signals.ts:308-314：formula 寫「所屬產業等權重 5 日報酬率」，dataRequirement 只寫 ETF 不適用，沒有說明含自身
      - data/taiwan/security_master.parquet（supported、active、stock 共 1,986 檔）：農業科技業 4 檔、玻璃陶瓷 5 檔、水泥與造紙各 7 檔，是最小的幾個產業
      - 指令：cd backend && uv run python ...\scratchpad\audit\work\verify-rsoself\repro.py（含 App 的 round(avg,6) 與 round(diff,4)）→ 輸出『RSO triggers incl-self: 3711／leave-one-out: 3820／extra 109／lost 0』，視窗 33 天（2026-08-10～2026-09-23）
      - 指令：uv run python ...\verify-rsoself\repro2.py → 輸出『unrounded: incl 3707 excl 3815 extra 108』，與 finding 的 3,707／108 一致，證明 finding 沒套用第 594 行的四捨五入
      - repro2.py 交叉比對 App 實際 get_diagnostics 輸出（...\audit\work\G01-gap\app_2026-09-{16,17,22,23}_stk.json）：app 32／100／128／70 與我的四捨五入版 32／100／128／70 相同，app-only 與 mine-only 都是空集合
      - repro2.py 依 n 分組 [評估的個股日數, 含自身觸發, 排除自身多出]：n≤10 [1075, 19, 8]；11-50 [20320, 697, 55]；>50 [40743, 2995, 46]
      - ...\verify-rsoself\extra_triggers.csv 範例：2026-08-10 6578.TPEX 農業科技業 n=4，5 日報酬 +12.05%，含自身差距 +8.95%（未觸發），排除自身差距 +11.93%（會觸發）；2026-09-22 1809.TWSE 玻璃陶瓷 n=5，差距 +8.86% 對 +11.07%
      - extra_triggers.csv 以 grep 找 2454、3034、2379、6196、3545 → 無結果（自選股不受影響）；自選 ETF 依 abnormal_diagnostics.py:615-616 列為不適用
      - 數學恆等式：r − (S−r)/(n−1) = n/(n−1)·(r − S/n)，所以排除自身不會讓任何原有觸發消失
      - 線上佐證：WebSearch 額度已用完（200/200），「通常以排除自身為基準」沒有查到來源，屬推測

### [P3 → P3] 法人 20 日基期以「有列的日數」為分母，與專案 B9「T86 缺列視為 0」的慣例不一致（部分成立）

- **主張**：mean(abs(foreign_net)) 只對有列的日子取平均，而且要至少 15 列才評估；專案在 detail_service（B9）已採「缺列即當日無法人交易＝0」的慣例。
- **正確的定義或數值**：同一專案對 T86 缺列採一致口徑，並排除上市未滿 20 日的標的。
- **app 實際行為**：冷門股的基期被高估或直接不評估。
- **對使用者的影響**：輕度：冷門股的外資突襲買超可能漏報。
- **修法建議**：以上市日判斷應有的交易日數，缺列補 0 後再算平均；或在文件中明寫目前的口徑。
- **稽核者信心**：plausible
- **程式碼證據**：
  - abnormal_diagnostics.py:296-301、:445、:467
  - docs\code-review-2026-09-22.md:387-388（B9：「T86 只列出當天有法人交易的股票，所以視窗內缺列視為 0」）
- **線上證據**：
  - （無）
- **本地計算證據**：
  - zerofill.py：改成缺列補 0、分母固定 20 後，33 個交易日外資異常多 47 筆、投信多 5 筆，原有觸發 0 筆消失；差異集中在有列日數 < 15 的冷門股（其中部分可能是上市未滿 20 日的新股，這類不應補 0）
- **獨立驗證**：
  - **reproduction**：partially_confirmed；修正後嚴重度 P3
    - 修正後敘述：abnormal_diagnostics.py:296-301 的法人 20 日基期只對有列的日子取平均，且要至少 15 列才評估（:445、:467）；detail_service（B9）則把 T86 缺列當成 0。官方參考資料證實 T86 與 TPEx 法人明細都不會列出當天沒有法人交易的標的（9/23 全 0 列為 0，另有成交卻無法人列的股票），所以兩處口徑確實不一致。改成缺列補 0、分母 20、不排除新股時，2026-08-10～09-23 共 33 日（股票加 ETF）外資多 47 筆、投信多 5 筆、原有觸發 0 筆消失；但其中 32 個差異標的日是視窗內才上市的標的（多為 ETF）。只看股票（公開路由）時外資 +24、投信 +5。若照 expected 排除上市未滿 20 日的標的，淨效果是外資 +15／−7、投信 +5／−0（只看股票為外資 +15／−1、投信 +5）；新增的外資觸發多為賣超（12／15），分布在 8 檔冷門股，而且會移除少數已有 15–19 列的新上市標的的現有觸發。自選 9 檔的有列日數全是 20，不受影響。
    - 理由：口徑不一致確實存在，finding 的三個數字（外資 +47、投信 +5、原有觸發 0 筆消失）在稽核者的模擬條件下也逐一重現。問題出在 finding 對這些數字的解讀，以及它建議的修法會產生的結果。

1) 程式碼：abnormal_diagnostics.py:296-301 用 polars 的 mean() 與 count() 對 20 日視窗內「有列」的日子取平均，:445 與 :467 再要求 count_inst_20d >= 15 才評估。detail_service.py:428-429 與 docs/code-review-2026-09-22.md:387 的 B9 則採「缺列＝0」。兩處口徑確實不同。

2) 前提有官方資料支持。以參考資料 9/23 為例：T86 共 1332 列，六個買賣欄全為 0 的列有 0 列；MI_INDEX 有成交的 48 檔不在 T86 裡，其中 18 檔是四碼股票，例如 6225 天瀚成交 174,523 股。TPEx 法人明細 5670 列，同樣沒有全 0 列；有 110 個四碼代號有成交卻沒有法人列，例如 5701 劍湖山成交 187,198 股。本地 store 的股票列數每日相當穩定（TWSE 1053–1084、TPEX 763–824），沒有缺半天的分區，所以缺列可以解讀成「當天沒有法人交易」。

3) 數字重現。條件照稽核者：股票加 ETF、目標日 2026-08-10～09-23 共 33 日、缺列補 0、分母固定 20、不設上市日門檻。結果外資 +47、投信 +5、原有觸發消失 0 筆，與 finding 完全相同。

4) 但 finding 的描述有三處偏差：
(a) 「其中部分可能是新股」其實是多數。51 個有差異的標的日中，32 個是視窗內才上市的標的（多為 00409A、00411A、009828 等 ETF，另有 3718.TPEX、2237.TWSE）。47 筆外資新增觸發裡有 23 筆是 ETF。公開 API 路由與 TaiwanScreener 只看股票，只看股票時外資 +24、投信 +5。
(b) 照 finding 自己的 expected（補 0，但排除上市未滿 20 日的標的）去算，股票加 ETF 是外資 +15／−7、投信 +5／−0；只看股票是外資 +15／−1、投信 +5／−0。也就是說，照建議修正後，現有觸發會消失 7 筆（只看股票為 1 筆），都是視窗內才上市、已有 15–19 列、目前會被評估的標的，例如 7855.TWSE、00411A.TPEX。「原有觸發 0 筆消失」只在不排除新股的簡化模擬下成立。
(c) 「外資突襲買超漏報」的說法太窄。依 expected 規則新增的 15 筆外資觸發中，12 筆是賣超、3 筆是買超，影響的是 8 檔冷門股。差異也不全在 n<15：3489.TPEX 在 n=19 時也受影響。
(d) 把期間拉長到 99 日（2026-05-06～09-23）、只看股票並採 expected 規則：外資 +29／−8、投信 +5／−0。

5) 使用者影響：自選 9 檔在 33 個目標日的有列日數全是 20，兩種口徑的結果完全相同，對使用者零影響。維持 P3。

另外，目標日的外資淨額與官方參考資料相符（9/22–23 共 3509 筆中 3507 筆一致，finding 列出的 3 個例子逐筆一致）。附帶觀察（不屬於本 finding）：9/22 有 37 檔股票的投信淨額與參考資料不同，例如 1560 本地 381,600、官方 86,600。這可能是 T86 盤後修訂沒有重抓，其他稽核項目可能已經涵蓋。
    - 證據：
      - backend/app/taiwan/abnormal_diagnostics.py:296-301：p20_inst_stats 以 pl.col('foreign_net').abs().mean() 與 .count() 計算，只涵蓋有列的日子
      - backend/app/taiwan/abnormal_diagnostics.py:445 與 :467：`i_stat['count_inst_20d'] >= 15` 才評估外資與投信訊號；:262 的視窗是法人 store 最近 20 個日期
      - backend/app/taiwan/detail_service.py:428-429：docstring 寫明 'a stored date without a row for the symbol counts as zero'；docs\code-review-2026-09-22.md:387 有同樣的說法
      - 參考資料 csv/twse_t86_20260922_20260923.csv 與 twse_mi_index_20260923_quotes.csv（腳本 work\verify-g1-instzf\ref_premise.py）：9/23 T86 1332 列、全 0 列 0 列；MI_INDEX 1380 檔中 48 檔不在 T86（四碼股票 18 檔，例如 6225 天瀚成交 174,523 股）
      - 參考資料 tpex_insti_daily_trade 與 tpex_daily_quotes 的 9/23：5670 列、全 0 列 0 列；有 110 個四碼代號有成交卻沒有法人列，例如 5701 劍湖山成交 187,198 股
      - 指令 `cd backend && uv run python work\verify-g1-instzf\repro.py`，股票加 ETF、33 日、補 0 且分母 20（Z1）：外資 app=3087、Z1=3134、只有 Z1 觸發 47 筆、只有 app 觸發 0 筆；投信 app=1164、Z1=1169、+5／−0。與 finding 的 47、5、0 一致
      - 同一腳本，Z1 的 51 個差異標的日中有 32 個在視窗內才上市（security_master listing_date 與 daily 首日一致）；依 n 分布，n>=15 的只有 3 個（n=15 一個、n=19 兩個）；47 筆外資新增中 ETF 23 筆、股票 24 筆
      - 同一腳本，只看股票（預設路由）：Z1 外資 +24／−0、投信 +5／−0
      - 同一腳本，補 0 並排除視窗內上市的標的（Z2＝finding 的 expected）：股票加 ETF 外資 +15／−7、投信 +5／−0；只看股票外資 +15／−1、投信 +5／−0。被移除的 7 筆是 00410A(n=17)、00991B(19)、7855.TWSE(17)、00411A.TPEX(16/17/19)、009828(15)
      - work\verify-g1-instzf\post.py：Z2 新增的 15 筆外資觸發中，買超 3 筆、賣超 12 筆，分布在 8 檔股票（5904.TPEX、3489.TPEX、8105、1563、6949、6129.TPEX、3591、3356）
      - repro.py，股票、99 日（2026-05-06～09-23）：Z2 外資 +29／−8、投信 +5／−0
      - repro.py：自選 9 檔在 33 個目標日的最小 n 都是 20；Z2 與 app 的差異為 0 筆
      - post.py：3591.TWSE 9/22 的 f=992116、6129.TPEX 9/22 的 f=-257486、3356.TWSE 9/23 的 f=-253304，都與官方參考資料的外資淨額完全相同；9/22–23 目標日外資一致的有 3507／3509 筆

## 確認正確的項目

- **VOLUME_SPIKE 公式、門檻與覆蓋條件（成交量 ÷ 前 5 日均量 ≥ 2.0，前 5 個 session 全部要有資料）**：讀碼後獨立重算 33 個交易日全市場（independent.py），與 app 結果（run_app_range.py，注入 stub store）比對：app 7,554 筆與獨立 7,520 筆完全重疊，差異的 34 筆全是比值落在 [1.995, 2.0) 被四捨五入成 2.0（見 P3 finding）。docstring 與前端的公式、dataRequirement 也與程式一致。
  - 來源：backend/app/taiwan/abnormal_diagnostics.py:13, :289-294, :378-396
  - 來源：frontend/src/lib/signals.ts:234-241
  - 來源：MBA智庫 量比：https://wiki.mbalib.com/zh-tw/%E9%87%8F%E6%AF%94 原文「量比＝現成交總手/〖(過去5個交易日平均每分鐘成交量)×當日累計開市時間(分)〗」——盤後即等於當日量 ÷ 前 5 日平均量，與 app 的基期口徑相同
  - 來源：AUDIT_SCRATCH/work/G01-gap/compare.py 輸出：both 30618 / only_app 92 / only_indep 55
- **TURNOVER_SPIKE（實際比的是成交金額 amount，不是週轉率）**：同上獨立重算。差異 44 筆全為四捨五入邊界；前端名稱「成交額放大」、公式寫成交金額，與程式相符。
  - 來源：abnormal_diagnostics.py:14, :399-417
  - 來源：signals.ts:242-249
- **PRICE_MOVE 公式與槓桿正規化（|漲跌幅| ÷ |槓桿倍數| ≥ 5%；7%／9.5% 分級）**：獨立重算 3,880 筆，差異只有 1 筆（9934 在 8/11 的 −0.0499996 被 round(…,6) 成 −0.05）。observed 保留原始漲跌幅，與 docstring :42-46 一致。除權息日的口徑問題另列 P1 finding。
  - 來源：abnormal_diagnostics.py:357-365, :423-441
  - 來源：signals.ts:250-256
- **槓桿倍數來源（Security Master leverage_multiplier）對反向與國外成分 ETF 的正確性**：官方 t187ap47_L 中 51 檔「槓桿/反向指數股票型基金」在 security_master 內的倍數全部 ≠1（正2 為 2.0，反1 為 −1.0，取絕對值後除 1），沒有漏標；非槓桿類只有 00716R（期貨信託，反1）為 −1.0，屬正確。自選 00885（國外成分證券指數股票型基金，富時越南30）與 00893（國外成分，NYSE FactSet 全球智能電動車）倍數都是 1.0，正規化是 no-op，正確。上櫃沒有任何槓桿／反向 ETF。
  - 來源：AUDIT_SCRATCH/reference/csv/twse_t187ap47_L_etf_meta.csv
  - 來源：backend/app/taiwan/universe/adapters.py:84-94
  - 來源：abnormal_diagnostics.py:364
  - 來源：work\G01-gap\lev.py 輸出：official leveraged/inverse 51, multiplier==1: 0
- **FOREIGN_FLOW_SPIKE（|外資| ≥ 100,000 股、前 20 日平均 ≥ 10,000 股、至少 15 日有資料、比值 ≥ 3）**：獨立重算 3,083 筆，差異 4 筆皆為四捨五入邊界（例如 4746 的 2.995 被進位成 3.0）。另與官方 T86／TPEx 三大法人核對 9/22～9/23 的外資買賣超：上市 2,661 列中 1 列不同（3189），上櫃 11,816 列中 1 列不同；store 的 foreign_net 等於「外陸資(不含外資自營商)」，外資自營商欄在 T86 兩日皆為 0。
  - 來源：abnormal_diagnostics.py:16-17, :296-301, :443-463
  - 來源：signals.ts:257-264
  - 來源：reference\csv\twse_t86_20260922_20260923.csv
  - 來源：reference\csv\tpex_insti_daily_trade_20260922_20260923.csv
- **TRUST_FLOW_SPIKE 公式與門檻**：獨立重算 1,163 筆，差異 1 筆（3189 在 8/17 的 2.996 被進位成 3.0）。投信資料本身的初值問題另列 finding。
  - 來源：abnormal_diagnostics.py:18-19, :465-485
  - 來源：signals.ts:265-272
- **MARGIN_SURGE／SHORT_SURGE／SHORT_MARGIN_RATIO_SPIKE 的輸入資料（融資增減、融券增減、券資比）**：store 與官方 MI_MARGN（上市 2,594 列）、TPEx 融資融券（1,838 列）在 9/22～9/23 逐列比對：margin_change、short_change 0 列不同，單位為股（官方張數 ×1000）。券資比 = 融券餘額 ÷ 融資餘額 × 100，四捨五入到小數 2 位，最大差 0.005；融資餘額為 0 時券資比為 null（316/316）。自選 9 檔兩日全部相符（例如 00893 在 9/23 為 15.79 vs 官方 15.789）。
  - 來源：reference\csv\twse_mi_margn_20260922_20260923.csv
  - 來源：reference\csv\tpex_margin_balance_20260922_20260923.csv
  - 來源：work\G01-gap\official3.py 輸出
  - 來源：MoneyDJ 券資比 https://www.moneydj.com/kmdj/wiki/wikiviewer.aspx?keyid=de0413d1-f0a3-45c7-9f63-b955d3e0e9ec 原文「券資比越高，代表軋空的可能性越大」（該條目公式句寫成『融資餘額除以融券餘額』，與通行定義及官方資料相反，因此只引用它的判讀句）
- **SHORT_MARGIN_RATIO_SPIKE 公式（券資比 − 前 20 日中位數 ≥ 5 個百分點，只看上升）**：獨立重算 988 筆，差異 1 筆為四捨五入邊界（1560 的 4.995 被進位成 5.0）。與前端「券資比驟升」語意一致。
  - 來源：abnormal_diagnostics.py:24-25, :527-543
  - 來源：signals.ts:289-296
- **PRICE_FLOW_DIVERGENCE（|正規化漲跌| ≥ 3%，且外資反向 ≥ 200,000 股，並達自身 20 日平均的 2 倍，繼承外資訊號的覆蓋條件）**：獨立重算 239 筆全部一致，另多 1 筆為倍數邊界（3008 在 9/18，1.99x 被進位成 2.0）。f_mult 只在「平均 ≥ 10,000 且 |外資| ≥ 100,000」時才會計算，所以 docstring 的「inherits coverage gates」屬實。
  - 來源：abnormal_diagnostics.py:26-27, :444-449, :545-584
  - 來源：signals.ts:297-306
- **RELATIVE_STRENGTH_OUTLIER 與 industry_intelligence 5 日口徑是否一致（第 (5) 項）**：兩者的 5 日基期都取 daily_store 中早於目標日的第 5 個分區：abnormal 用 prior_5[0]，industry 用 prior[-5]；產業報酬取 average_return_5d（產業等權 5 日報酬，不是 relative_strength_5d），母體同為 active 且 supported 的股票。獨立重算 3,707 筆，其中 3,703 筆一致，4 筆為 round(…,4) 邊界。ETF 一律列入 not_applicable_signals。
  - 來源：abnormal_diagnostics.py:209-214, :310-316, :586-616
  - 來源：industry_intelligence.py:160-182, :195-198, :406-414
  - 來源：docs\code-review-2026-09-22.md:214-215（B0 已記錄的基期對齊）
- **昨收基準在上櫃缺日時不會跨日錯位（第 (2) 項的錯位部分）**：prev_date 取全 store 中最近的前一分區。9/17 的 prev 是 9/16，上櫃在 9/16 沒有列，所以 9/17 上櫃 1,006 檔的 change_pct 全部為 null。沒有出現把 9/15→9/17 兩日漲跌當成一日漲跌的情形（gap.py 輸出 chg_null=1006）。缺值不評估，而不是算出錯的數字（缺值的揭露問題見 P2 finding）。
  - 來源：abnormal_diagnostics.py:250-252, :344, :352, :357
  - 來源：work\G01-gap\gap.py 輸出
- **沒有未來函數（Point-In-Time）**：所有基期都取 < target 的分區（:211、:262、:267），industry 的基期也嚴格小於目標日（:165-176）。獨立實作採同樣定義，結果一致。
  - 來源：abnormal_diagnostics.py:211, :262, :267
  - 來源：industry_intelligence.py:165-182
- **自選股三方比對：官方 STOCK_DAY vs app（上市 9 檔自選 + 2330，33 個交易日）**：以 STOCK_DAY 自行計算量比、額比與漲跌：2026-08-10～09-15 期間 app 的 volume_ratio_5d、amount_ratio_5d 與官方計算值差距 ≤ 0.005（只是 app 四捨五入到 2 位）；非除權息日 app 的 change_pct 與官方漲跌幅差 ≤ 5e-7；全期間收盤價 0 差異。9/16 之後的量額差異與除權息日另列 finding。
  - 來源：reference\csv\twse_stock_day_202608_202609.csv
  - 來源：work\G01-gap\official2.py 輸出（rows compared 330, dates 33, symbols 10）
- **CompactMarketContext.advance_ratio 的公式（第 (6) 項）**：advance ÷ (advance + decline + flat)，未比較的不計入，是通行的上漲家數比。9/23 以原始收盤比前收，上市股票加 ETF 的漲／跌／平為 538/647/137；官方以參考價比較為 538/642/140，差異僅來自當日除權息股（2542、1442、1808）。缺日造成的母體不足、以及母體含 ETF 另列 finding。
  - 來源：abnormal_diagnostics.py:275-279
  - 來源：market_intelligence.py:239-271
  - 來源：reference\csv\twse_mi_index_20260923_quotes.csv
  - 來源：work\G01-gap\breadth.py 輸出
- **include_etfs 範圍與 not_applicable_signals**：include_etfs=True 時 universe = TAIWAN_STOCKS + TAIWAN_ETFS（:239-241）：9/22 項目數從 1,982 增為 2,340，ETF 的 not_applicable 只含 RSO（:614-616）。公開 API 與 Screener 都沒有傳 include_etfs（taiwan.py:407-414、TaiwanScreener.tsx:286-288）；ai_research 與 comparison 有傳 True（ai_research.py:397-401、comparison.py:236）。與 docstring 相符。
  - 來源：abnormal_diagnostics.py:30-54, :239-241, :614-616
  - 來源：backend/app/api/taiwan.py:390-420
  - 來源：backend/app/taiwan/comparison.py:236

## 無法驗證的項目

- **台灣券商／財經網站對「爆量」「外資／投信異常買賣超」「融資／融券激增」的具體倍數門檻（2 倍、3 倍、100 張、50 張、500 張）**：本 session 的 WebSearch 額度已耗盡（工具回報 200 of 200）；MoneyDJ 財經百科查「爆量」「融券回補」都回報「目前沒有相關資料」；Yahoo 奇摩股市、HiStock、玩股網的排行頁由前端動態產生，找不到可直接引用的定義頁。只能確認量比口徑（5 日基期），門檻數值屬設計選擇，無法對照台灣慣例。
- **上櫃個股除權息、減資、面額變更造成的假 PRICE_MOVE 數量**：參考資料只下載了 TWSE 的 TWT49U，沒有 TPEx 除權息表。觀察到上櫃有超過 ±10% 的異常單日變動，例如 5314 世紀* 8/14 −73.6%、7805 威聯通 8/24 −24.2%、4303 信立 8/27 −22.9%，推測是公司行動，但無法以官方資料證實。
- **第三方網站（Yahoo 奇摩股市、鉅亨、HiStock）上的異常訊號或量比數值，用於三方比對**：WebSearch 無額度，這些頁面也是動態產生；三方比對改用官方 STOCK_DAY、MI_INDEX、T86、MI_MARGN。
- **16:30 當日抓取值與官方最終值差異的成因（推測是鉅額交易在 16:30 後才完成申報，以及 T86 投信欄事後修正）**：只能證實差異存在：store 分區的寫入時間是當日 16:30，參考資料是 9/24 01:5x 抓取。無法查證官方的修正時點；而且不得直接連 TWSE／TPEx。
- **嚴重度分級（moderate／high／extreme 的切點）是否合理**：沒有可對照的外部標準；docstring 與前端也都沒有定義分級（改列 P3 維護性 finding）。
- **2026-09-24（今日）及之後因 9/23 缺日持續造成的影響（9/24～9/30 上櫃量能訊號無法評估、9/24 上櫃沒有漲跌幅）**：今日的資料還沒產生；影響是依程式邏輯（:250-252、:378）推論，屬推測，尚未實測。
